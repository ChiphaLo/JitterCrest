"""Exercise generated configurations with actual C++ processes and local TLS proxy."""
import argparse
from contextlib import ExitStack
import importlib.util
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import time

spec = importlib.util.spec_from_file_location("quickstart", Path(__file__).resolve().parents[1] / "deploy/quickstart.py")
quick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quick)


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(host, port_number, context, method, path, body=None, headers=None):
    conn = http.client.HTTPSConnection(host, port_number, context=context, timeout=8)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise AssertionError("test process did not stop")
    if process.returncode not in (0, -15):
        raise AssertionError(f"test process exited {process.returncode}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ui", type=Path, required=True)
    p.add_argument("--agent", type=Path, required=True)
    p.add_argument("--caddy", type=Path, required=True)
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="jittercrest-quickstart-") as td, ExitStack() as stack:
        root = Path(td)
        cert, key = root / "cert.pem", root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                        "-days", "1", "-subj", "/CN=localhost", "-addext",
                        "subjectAltName=DNS:localhost,IP:127.0.0.1", "-keyout", str(key), "-out", str(cert)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ports = []
        while len(ports) < 9:
            candidate = port()
            if candidate not in ports:
                ports.append(candidate)
        main_port, data_port, probe_port, main_tls, data_tls, probe_tls, main_edge, data_edge, probe_edge = ports
        main_origin = f"https://localhost:{main_tls}"
        data_origin, probe_origin = f"https://127.0.0.1:{data_tls}", f"https://127.0.0.1:{probe_tls}"
        common = dict(native_prefix="/opt/deps", service_user="jittercrest")
        data_spec = dict(common, role="data", id="node-a", label="Data", public_origin=data_origin,
                         main_origin=main_origin, udp4="", port=data_port, tls_port=data_edge)
        probe_spec = dict(common, role="probe", id="probe-a", label="Probe", public_origin=probe_origin,
                          main_origin=main_origin, udp4="8.8.8.8", port=probe_port, tls_port=probe_edge)
        main_spec = dict(common, role="main", id="main", label="Main", public_origin=main_origin,
                         main_origin=main_origin, udp4="", port=main_port, tls_port=main_edge)
        data, probe, main_dir = root / "data", root / "probe", root / "main"
        quick.generate(data_spec, [], data)
        quick.generate(probe_spec, [], probe)
        quick.generate(main_spec, [json.loads((data / "node.json").read_text())], main_dir)
        backup, _ = quick.add_node(main_dir, json.loads((probe / "node.json").read_text()))
        assert backup.is_file()
        for folder, binary in [(main_dir, a.ui), (data, a.agent), (probe, a.agent)]:
            (folder / "build/cpp").mkdir(parents=True)
            (folder / "build/cpp/speedtest-cpp").symlink_to(binary.resolve(strict=True))
            # What sync-cert.sh copies from Caddy's storage on a real host.
            (folder / "tls").mkdir(mode=0o700)
            (folder / "tls/node.crt").write_bytes(cert.read_bytes())
            (folder / "tls/node.key").write_bytes(key.read_bytes())
            log = stack.enter_context(tempfile.TemporaryFile())
            env = {k: v for k, v in os.environ.items() if not k.startswith(("SPEEDTEST_", "TRUST", "ALLOWED_"))}
            process = subprocess.Popen(["bash", str(folder / "run.sh")], env=env, stdout=log, stderr=log)
            stack.callback(stop, process)
        config = "{\n admin off\n auto_https off\n}\n"
        for folder in [main_dir, data, probe]:
            site = (folder / "Caddyfile.example").read_text()
            site = site.replace(" {\n", f' {{\n bind 127.0.0.1\n tls "{cert}" "{key}"\n', 1)
            config += site + "\n"
        caddyfile = root / "Caddyfile"
        caddyfile.write_text(config)
        env = dict(os.environ, XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data-home"))
        log = stack.enter_context(tempfile.TemporaryFile())
        subprocess.run([str(a.caddy), "validate", "--config", str(caddyfile), "--adapter", "caddyfile"],
                       env=env, stdout=log, stderr=log, check=True)
        caddy = subprocess.Popen([str(a.caddy), "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
                                 env=env, stdout=log, stderr=log)
        stack.callback(stop, caddy)
        context = ssl.create_default_context(cafile=str(cert))
        for _ in range(150):
            try:
                if all(request(host, pn, context, "GET", "/health")[0] == 200
                       for host, pn in [("localhost", main_tls), ("127.0.0.1", data_tls), ("127.0.0.1", probe_tls)]):
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.05)
        else:
            raise AssertionError("TLS proxy or backends did not become ready")
        status, _, page = request("localhost", main_tls, context, "GET", "/")
        assert status == 200
        node_json = re.search(rb'<script[^>]*id="nodeConfig"[^>]*>(.*?)</script>', page, re.S)
        assert node_json
        nodes = json.loads(node_json.group(1))
        assert [n["id"] for n in nodes] == ["local", "node-a", "probe-a"]
        edges = [f"https://localhost:{main_edge}", f"https://127.0.0.1:{data_edge}", f"https://127.0.0.1:{probe_edge}"]
        assert [n["base"] for n in nodes] == edges, nodes
        csp = request("localhost", main_tls, context, "GET", "/")[1]["content-security-policy"]
        assert all(e in csp for e in edges), csp
        # Speed traffic: HTTP/2 through each built-in listener with its large windows.
        for edge in edges:
            out = subprocess.run(["curl", "-sS", "--http2", "--cacert", str(cert), "-o", "/dev/null",
                                  "-w", "%{http_version} %{http_code}", edge + "/health"],
                                 capture_output=True, text=True, check=True).stdout
            assert out == "2 200", (edge, out)
        upload = subprocess.run(["curl", "-sS", "--http2", "--cacert", str(cert), "-H", "Origin: " + main_origin,
                                 "-X", "POST", "-T", "-", edges[1] + "/upload-stream?warmupMs=0"],
                                input=bytes(8 << 20), capture_output=True, check=True).stdout
        assert json.loads(upload)["bytes"] == 8 << 20
        assert request("127.0.0.1", data_tls, context, "GET", "/")[0] == 404
        headers = {"Origin": main_origin}
        # Reuse one upstream connection after a rejected request with a body.
        conn = http.client.HTTPConnection("127.0.0.1", probe_port, timeout=8)
        try:
            conn.request("POST", "/upload", b"body-must-be-drained", headers)
            r = conn.getresponse()
            assert r.status == 404
            r.read()
            conn.request("GET", "/latency", headers=headers)
            r = conn.getresponse()
            assert r.status == 204, (r.status, r.read())
            r.read()
        finally:
            conn.close()
        status, cors, body = request("127.0.0.1", data_tls, context, "GET", "/download?size=10240", headers=headers)
        assert status == 200 and len(body) == 10240 and cors["access-control-allow-origin"] == main_origin
        status, cors, body = request("127.0.0.1", data_tls, context, "POST", "/upload", bytes(10240), headers)
        assert status == 200 and json.loads(body)["bytes"] == 10240 and cors["access-control-allow-origin"] == main_origin
        assert request("127.0.0.1", data_tls, context, "GET", "/download?size=10240",
                       headers={"Origin": "https://untrusted.example"})[0] == 403
        assert request("127.0.0.1", probe_tls, context, "GET", "/download?size=10240", headers=headers)[0] == 404
        assert request("127.0.0.1", probe_tls, context, "POST", "/upload", b"data", headers)[0] == 404
        latency_status, latency_headers, latency_body = request("127.0.0.1", probe_tls, context, "GET", "/latency", headers=headers)
        assert latency_status == 204, (latency_status, latency_headers, latency_body)
        status, _, _ = request("127.0.0.1", data_tls, context, "POST", "/webrtc/offer", b"{}",
                               {"Origin": main_origin, "Content-Type": "application/json"})
        assert status == 400, status  # RTC is enabled, not a default-off 503.
        print("PASS real C++ UI/data/probe generated setup through verified local TLS/Caddy and built-in HTTP/2 listeners; add-node, CORS and role isolation")


if __name__ == "__main__":
    main()
