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
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CADDY_IMAGE = "caddy:2.11.9"


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
    u = urlsplit(base)
    try:
        ipaddress.ip_address(u.hostname)
        issuer = "    tls {\n        issuer acme {\n            profile shortlived\n        }\n    }\n"
    except ValueError:
        issuer = ""
    return ("{\n    admin off\n}\n" + base + " {\n" + issuer +
            f"    reverse_proxy 127.0.0.1:{port} {{\n" +
            "        header_up X-Real-IP {http.request.remote.host}\n" +
            "        header_up X-Forwarded-For {http.request.remote.host}\n" +
            "        flush_interval -1\n    }\n}\n")


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
        if spec.get("role") not in ("data", "probe"):
            raise ValueError("invalid agent role")
        addr = ipaddress.IPv4Address(spec["udp4"])
        if not addr.is_global or addr.is_multicast:
            raise ValueError("invalid public UDP mapping")
        spec.update(keyHash=key_hash, port=available_port())
        save(registration, spec)
    label = "org.jittercrest.node=" + node
    containers = ["jittercrest-" + node, "jittercrest-" + node + "-tls"]
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
        u = urlsplit(spec["base"])
        for port in {80, 443, u.port or 443}:
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
                   SPEEDTEST_AGENT_KEY=spec["agentKey"])
        envfile = folder / "agent.env"
        envfile.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        os.chmod(envfile, 0o600)
        proxyfile = folder / "Caddyfile"
        proxyfile.write_text(caddyfile(spec["base"], spec["port"]))
        print("[4/5] Starting agent and trusted HTTPS", flush=True)
        run("docker", "run", "-d", "--name", containers[0], "--label", label,
            "--restart", "unless-stopped", "--network", "host", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
            "--memory", "512m", "--tmpfs", "/tmp", "--env-file", str(envfile), image)
        created.append(containers[0])
        (folder / "caddy-data").mkdir(exist_ok=True)
        run("docker", "run", "-d", "--name", containers[1], "--label", label,
            "--restart", "unless-stopped", "--network", "host",
            "-v", str(proxyfile) + ":/etc/caddy/Caddyfile:ro",
            "-v", str(folder / "caddy-data") + ":/data", CADDY_IMAGE)
        created.append(containers[1])
        deadline = time.monotonic() + 180
        while True:
            try:
                with urlopen(spec["base"] + "/health", timeout=5) as response:
                    health = json.loads(response.read(4096))
                    if response.status == 200 and health.get("name") == node:
                        break
            except (OSError, ValueError, http.client.HTTPException):
                pass
            if time.monotonic() >= deadline:
                raise ValueError("HTTPS did not become ready: check DNS, public TCP 80/443, certificate issuance and provider policy")
            time.sleep(2)
        print("[5/5] Activating node on the main server", flush=True)
        api(main, "/api/enrollment/activate", spec["agentKey"], node)
        print(f"Connected: {node} at {spec['base']}. Reload the node picker on the main page.", flush=True)
        print(f"Installation: {folder}. Keep public UDP reachable for WebRTC.", flush=True)
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
