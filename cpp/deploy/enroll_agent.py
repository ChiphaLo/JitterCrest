#!/usr/bin/env python3
"""Install an enrolled JitterCrest C++ agent on a Linux VPS using Docker."""
import argparse
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CADDY_IMAGE = "caddy:2.11.9"
AGENT_UID = 65532
SYSTEMD = "/etc/systemd/system"
CERTIFICATE_WAIT = 180


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urlopen(request, timeout):
    return build_opener(NoRedirect()).open(request, timeout=timeout)


def origin(value):
    u = urlsplit(value)
    if (u.scheme != "https" or not u.hostname or u.username or u.password or
            u.query or u.fragment or u.path or
            any(c in value for c in "\r\n\t '\"\\")):
        raise ValueError("expected an HTTPS origin without path or credentials")
    if u.port and not 1 <= u.port <= 65535:
        raise ValueError("invalid HTTPS port")
    return value


def api(main, path, key, node):
    req = Request(main + path, data=json.dumps({"id": node}).encode(),
                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read(16384))


def save(path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        os.chmod(temp, 0o600)
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def unpack(archive, destination):
    with tarfile.open(archive, "r:gz") as tar:
        entries = tar.getmembers()
        if len(entries) > 500 or sum(e.size for e in entries) > 32 * 1024 * 1024:
            raise ValueError("source archive exceeds limits")
        for e in entries:
            p = Path(e.name)
            if p.is_absolute() or ".." in p.parts or not e.isfile() or e.size > 2 * 1024 * 1024:
                raise ValueError("invalid source archive entry")
            target = destination / p
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(e) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def run(*args):
    subprocess.run(args, check=True)


def available_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def caddyfile(base, port):
    """Caddy obtains the certificate on 80/443 and serves HTTP/1.1 tooling."""
    u = urlsplit(base)
    try:
        ipaddress.ip_address(u.hostname)
        issuer = "    tls {\n        issuer acme {\n            profile shortlived\n        }\n    }\n"
    except ValueError:
        issuer = ""
    return ("{\n    admin off\n}\n" + caddy_origin(base) + " {\n" + issuer +
            f"    reverse_proxy 127.0.0.1:{port} {{\n" +
            "        header_up X-Real-IP {http.request.remote.host}\n" +
            "        header_up X-Forwarded-For {http.request.remote.host}\n" +
            "        flush_interval -1\n    }\n}\n")


def caddy_origin(base):
    host = urlsplit(base).hostname
    return "https://" + (f"[{host}]" if ":" in host else host)


def edge_port(base):
    """Browsers stream uploads over HTTP/2 only; Caddy's fixed 1 MiB HTTP/2
    window caps them near 1 MiB per RTT, so speed traffic uses the agent's
    built-in TLS listener on the base port."""
    port = urlsplit(base).port
    if not port or port in (80, 443):
        raise ValueError("the agent HTTPS origin needs the built-in TLS port, for example https://node.example.com:8444")
    return port


def certificate_source(folder, host):
    found = sorted((folder / "caddy-data/caddy/certificates").glob(f"*/{host}/{host}.crt"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0].parent if found and (found[0].parent / f"{host}.key").is_file() else None


def sync_script(folder, host):
    q = shlex.quote
    return f"""#!/bin/sh
# Copies the Caddy-issued certificate to the agent's built-in TLS listener.
set -eu
dir={q(str(folder))}
host={q(host)}
src=$(ls -1td "$dir"/caddy-data/caddy/certificates/*/"$host" 2>/dev/null | head -n 1)
test -n "$src" && test -s "$src/$host.crt" && test -s "$src/$host.key"
install -d -m 0700 -o {AGENT_UID} -g {AGENT_UID} "$dir/tls"
for ext in crt key; do
  cmp -s "$src/$host.$ext" "$dir/tls/node.$ext" && continue
  install -m 0400 -o {AGENT_UID} -g {AGENT_UID} "$src/$host.$ext" "$dir/tls/.node.$ext"
  mv -f "$dir/tls/.node.$ext" "$dir/tls/node.$ext"
done
"""


def sync_units(node, script):
    service = (f"[Unit]\nDescription=Copy the JitterCrest {node} TLS certificate\n\n"
               f"[Service]\nType=oneshot\nExecStart=/bin/sh {script}\n")
    timer = (f"[Unit]\nDescription=Refresh the JitterCrest {node} TLS certificate\n\n"
             "[Timer]\nOnActiveSec=10min\nOnUnitActiveSec=10min\n\n[Install]\nWantedBy=timers.target\n")
    return service, timer


def edge_ready(base):
    """True when the built-in listener presents a trusted certificate and HTTP/2."""
    u = urlsplit(base)
    context = ssl.create_default_context()
    context.set_alpn_protocols(["h2"])
    with socket.create_connection((u.hostname, u.port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=u.hostname) as tls:
            return tls.selected_alpn_protocol() == "h2"


def install(main, node, key):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("run with sudo/root on Linux")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", node):
        raise ValueError("invalid node ID")
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("invalid enrollment key")
    origin(main)
    folder = Path("/opt/jittercrest-agents") / node
    if folder.exists() and not (folder / "registration.json").is_file():
        raise ValueError("installation directory already exists without a registration")
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    registration = folder / "registration.json"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    spec = json.loads(registration.read_text()) if registration.exists() else {}
    if spec and spec.get("main") != main:
        raise ValueError("existing installation belongs to a different controller")
    if spec.get("keyHash") != key_hash:
        print("[1/5] Claiming one-time enrollment key", flush=True)
        spec = api(main, "/api/enrollment/claim", key, node)
        if spec.get("id") != node or spec.get("main") != main:
            raise ValueError("controller returned a different enrollment")
        origin(spec["base"])
        edge_port(spec["base"])
        if spec.get("role") not in ("data", "probe"):
            raise ValueError("invalid agent role")
        addr = ipaddress.IPv4Address(spec["udp4"])
        if not addr.is_global or addr.is_multicast:
            raise ValueError("invalid public UDP mapping")
        spec.update(keyHash=key_hash, port=available_port())
        save(registration, spec)
    tls_port = edge_port(spec["base"])
    host = urlsplit(spec["base"]).hostname
    label = "org.jittercrest.node=" + node
    containers = ["jittercrest-" + node, "jittercrest-" + node + "-tls"]
    unit = "jittercrest-" + node + "-tls-sync"
    created = []
    try:
        print("[2/5] Preparing Docker and the C++ source", flush=True)
        if not shutil.which("docker"):
            if not shutil.which("apt-get"):
                raise ValueError("install Docker first on this Linux distribution")
            run("apt-get", "update")
            run("apt-get", "install", "-y", "docker.io")
            run("systemctl", "enable", "--now", "docker")
        run("docker", "info", "--format", "{{.ServerVersion}}")
        for name in containers:
            old = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
            if old.returncode == 0:
                info = json.loads(old.stdout)[0]
                if info.get("Config", {}).get("Labels", {}).get("org.jittercrest.node") != node:
                    raise ValueError("container name is already used by another installation")
                run("docker", "rm", "-f", name)
        for port in (80, 443, tls_port):
            with socket.socket() as s:
                s.bind(("0.0.0.0", port))
        archive = folder / "source.tar.gz"
        with urlopen(main + "/enroll/source.tar.gz", timeout=60) as response:
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("source download exceeds limit")
        archive.write_bytes(data)
        source = folder / "source"
        source.mkdir(exist_ok=True)
        unpack(archive, source)
        print("[3/5] Building and testing the C++ agent (first build takes several minutes)", flush=True)
        image = "jittercrest-agent:" + node
        run("docker", "build", "-f", str(source / "cpp/deploy/Dockerfile.agent"), "-t", image, str(source))
        env = dict(SPEEDTEST_HOST="127.0.0.1", PORT=str(spec["port"]),
                   SPEEDTEST_NODE_NAME=node, SPEEDTEST_PROBE_ONLY="1" if spec["role"] == "probe" else "0",
                   SPEEDTEST_RUST_RTC_EXPERIMENTAL="1", SPEEDTEST_WEBRTC_UDP4=spec["udp4"],
                   TRUST_PROXY_HEADERS="1", TRUSTED_PROXY_IPS="127.0.0.1,::1",
                   ALLOWED_ORIGINS=main, SPEEDTEST_ALLOW_CROSS_SITE="1",
                   SPEEDTEST_CONTROLLER_ORIGIN=main, SPEEDTEST_AGENT_ID=node,
                   SPEEDTEST_AGENT_KEY=spec["agentKey"], SPEEDTEST_TLS_PORT=str(tls_port),
                   SPEEDTEST_TLS_HOST="0.0.0.0", SPEEDTEST_TLS_CERT="/tls/node.crt",
                   SPEEDTEST_TLS_KEY="/tls/node.key")
        envfile = folder / "agent.env"
        envfile.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        os.chmod(envfile, 0o600)
        proxyfile = folder / "Caddyfile"
        proxyfile.write_text(caddyfile(spec["base"], spec["port"]))
        print("[4/5] Obtaining the certificate and starting the agent", flush=True)
        (folder / "caddy-data").mkdir(exist_ok=True)
        run("docker", "run", "-d", "--name", containers[1], "--label", label,
            "--restart", "unless-stopped", "--network", "host",
            "-v", str(proxyfile) + ":/etc/caddy/Caddyfile:ro",
            "-v", str(folder / "caddy-data") + ":/data", CADDY_IMAGE)
        created.append(containers[1])
        deadline = time.monotonic() + CERTIFICATE_WAIT
        while not certificate_source(folder, host):
            if time.monotonic() >= deadline:
                raise ValueError("no certificate was issued: check DNS, public TCP 80/443 and provider policy")
            time.sleep(2)
        script = folder / "tls-sync.sh"
        script.write_text(sync_script(folder, host))
        os.chmod(script, 0o700)
        service, timer = sync_units(node, script)
        units = Path(SYSTEMD)
        (units / (unit + ".service")).write_text(service)
        (units / (unit + ".timer")).write_text(timer)
        run("systemctl", "daemon-reload")
        run("systemctl", "start", unit + ".service")
        run("systemctl", "enable", "--now", unit + ".timer")
        run("docker", "run", "-d", "--name", containers[0], "--label", label,
            "--restart", "unless-stopped", "--network", "host", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
            "--memory", "512m", "--tmpfs", "/tmp", "--env-file", str(envfile),
            "-v", str(folder / "tls") + ":/tls:ro", image)
        created.append(containers[0])
        deadline = time.monotonic() + 180
        while True:
            try:
                with urlopen(caddy_origin(spec["base"]) + "/health", timeout=5) as response:
                    health = json.loads(response.read(4096))
                    if response.status == 200 and health.get("name") == node and edge_ready(spec["base"]):
                        break
            except (OSError, ValueError, http.client.HTTPException, ssl.SSLError):
                pass
            if time.monotonic() >= deadline:
                raise ValueError(f"HTTPS did not become ready: check public TCP 80/443/{tls_port} and provider policy")
            time.sleep(2)
        print("[5/5] Activating node on the main server", flush=True)
        api(main, "/api/enrollment/activate", spec["agentKey"], node)
        print(f"Connected: {node} at {spec['base']}. Reload the node picker on the main page.", flush=True)
        print(f"Installation: {folder}. Keep public TCP {tls_port} and UDP reachable for WebRTC.", flush=True)
    except BaseException:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
        print("Setup stopped. Registration and build cache are retained; rerun the same command to resume.", file=sys.stderr)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--main", required=True)
    p.add_argument("--node", required=True)
    p.add_argument("--key", required=True)
    a = p.parse_args()
    try:
        install(a.main, a.node, a.key)
    except Exception as e:
        # HTTPError URLs contain only API paths; credentials are sent in headers.
        print(f"Enrollment failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
