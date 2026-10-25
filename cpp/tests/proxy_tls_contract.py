"""Shared local Caddy HTTP/TLS + unchanged-browser contracts.

Uses isolated loopback ports, a temporary certificate and a separate Caddy
process. Never installs a service/CA or changes production routing. Python
verifies the certificate/hostname; Chromium pins only this test certificate key.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
from playwright.sync_api import sync_playwright
from http_contract import Server, allocate_port


def request(port, method, path, body=None, headers=None, context=None):
    if context:
        connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=6)
    else:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=6)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        return response.status, dict(response.getheaders()), data
    finally:
        connection.close()


def run(binary, caddy, template, browser_type, folder, cert, key, pin, label):
    http_port, tls_port = allocate_port(), allocate_port()
    origin = f"https://localhost:{tls_port}"
    server = Server(binary, {"SPEEDTEST_RUST_RTC_EXPERIMENTAL": "1",
                            "SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST": "1",
                            "TRUST_PROXY_HEADERS": "1", "TRUSTED_PROXY_IPS": "127.0.0.1,::1",
                            "ALLOWED_ORIGINS": origin})
    config = folder / f"{label}.Caddyfile"
    config.write_text("{\n admin off\n auto_https off\n}\n" +
                      template.read_text().replace("127.0.0.1:32132", f"127.0.0.1:{server.port}")
                      .replace("127.0.0.1:32133", f"127.0.0.1:{http_port}") +
                      f'\nhttps://localhost:{tls_port} {{\n bind 127.0.0.1\n tls "{cert}" "{key}"\n import cpp_staging\n}}\n')
    context = ssl.create_default_context(cafile=str(cert))
    log = tempfile.TemporaryFile()
    proxy = None
    proxy_env = dict(os.environ, XDG_CONFIG_HOME=str(folder / "caddy-config"),
                     XDG_DATA_HOME=str(folder / "caddy-data"))
    try:
        validation = subprocess.run([str(caddy), "validate", "--adapter", "caddyfile", "--config", str(config)],
                                    stdout=log, stderr=log, env=proxy_env)
        if validation.returncode:
            log.seek(0)
            raise AssertionError("local Caddy validation: " + log.read().decode(errors="replace"))
        proxy = subprocess.Popen([str(caddy), "run", "--adapter", "caddyfile", "--config", str(config)],
                                 stdout=log, stderr=log, env=proxy_env)
        for _ in range(100):
            if proxy.poll() is not None:
                raise AssertionError("local proxy exited before readiness")
            try:
                if request(tls_port, "GET", "/health", context=context)[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(.05)
        else:
            raise AssertionError("local TLS proxy readiness timeout")
        values = {}
        for name, port, ctx in [("http", http_port, None), ("tls", tls_port, context)]:
            checks = {}
            for size in [10240, 102400, 1048576]:
                status, headers, body = request(port, "GET", f"/download?size={size}", context=ctx)
                assert status == 200 and len(body) == size, (status, len(body), size)
                assert all(v.lower() == "identity" for k, v in headers.items() if k.lower() == "content-encoding")
                checks[f"download-{size}"] = dict(status=status, bytes=len(body))
            status, _, body = request(port, "POST", "/upload", bytes(1048576), context=ctx)
            received = json.loads(body)
            assert status == 200 and received["bytes"] == 1048576, (status, received)
            checks["upload"] = dict(status=status, bytes=received["bytes"])
            status, headers, _ = request(port, "OPTIONS", "/upload", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST"}, context=ctx)
            headers = {k.lower(): v for k, v in headers.items()}
            assert status == 204 and headers["access-control-allow-origin"] == origin
            checks["cors"] = dict(status=status, exact_origin=True)
            status, _, body = request(port, "POST", "/upload-progress?lanes=1&warmupMs=0", b"", context=ctx)
            assert status == 200
            progress_id = json.loads(body)["id"]
            stream = (http.client.HTTPSConnection("localhost", port, context=ctx, timeout=6) if ctx else
                      http.client.HTTPConnection("127.0.0.1", port, timeout=6))
            try:
                stream.request("GET", f"/upload-progress?id={progress_id}")
                response = stream.getresponse()
                assert response.status == 200
                # This read must complete BEFORE an upload is started: proves
                # the proxy does not buffer NDJSON until the final result.
                initial = json.loads(response.readline())
                assert initial["bytes"] == 0 and not initial["done"], initial
                status, _, body = request(port, "POST",
                    f"/upload-stream?progressId={progress_id}&lane=0&warmupMs=0", bytes(65536), context=ctx)
                assert status == 200 and json.loads(body)["bytes"] == 65536
                final = initial
                for _ in range(20):
                    line = response.readline()
                    if not line:
                        break
                    final = json.loads(line)
                    if final["done"]:
                        break
                assert final["done"] and not final["failed"] and not final["expired"], final
                assert final["bytes"] == final["measuredBytes"] == 65536, final
                checks["live-ndjson"] = dict(initial_before_upload=True, final_bytes=65536, done=True)
            finally:
                stream.close()
            values[name] = checks
        # Python has already verified chain/hostname. Browser pinning is scoped
        # to this ephemeral key, not a blanket TLS-error bypass or installed CA.
        browser = browser_type.launch(headless=True, args=["--disable-gpu",
            "--disable-features=WebRtcHideLocalIpsWithMdns",
            "--ignore-certificate-errors-spki-list=" + pin])
        try:
            page = browser.new_page()
            errors, offers = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda req: offers.append(req.url) if "/webrtc/offer" in req.url else None)
            page.goto(origin + "/?testMode=webrtc-contract")
            page.wait_for_function("() => Boolean(window.__speedtestTestHooks)")
            measured = page.evaluate("() => window.__speedtestTestHooks.runPairedMeasurementForTest({planned:3})")
            assert measured["complete"] and not measured["invalidReasons"], measured
            page.click("#restartBtn")
            page.wait_for_function("() => ['RTT complete','Error'].includes(document.querySelector('#phase').textContent)", timeout=15000)
            assert page.locator("#phase").inner_text() == "RTT complete", page.locator("#log").inner_text()
            assert page.locator("#rttIncompleteBadge").evaluate("node => node.hidden")
            for selector in ["#rttUpAvg", "#rttDownAvg", "#rttUpP95", "#rttDownP95"]:
                assert float(page.locator(selector).inner_text()) >= 0
            assert not errors and offers and all(url.startswith(origin + "/") for url in offers), (errors, offers)
            values["browser"] = dict(tls_certificate_pinned=True, paired_complete=True,
                                     restart_rtt_rendered=True, signaling_through_proxy=True)
        finally:
            browser.close()
        return values
    finally:
        try:
            if proxy:
                if proxy.poll() is None:
                    proxy.terminate()
                try:
                    proxy.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proxy.kill()
                    proxy.wait(timeout=3)
                    raise AssertionError("local proxy shutdown exceeded grace")
                assert proxy.returncode == 0, proxy.returncode
        finally:
            log.close()
            server.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cpp", type=Path, required=True)
    p.add_argument("--rust", type=Path, required=True)
    p.add_argument("--caddy", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    template = Path(__file__).resolve().parents[1] / "deploy/Caddyfile.staging.example"
    results = {}
    with tempfile.TemporaryDirectory(prefix="speedtest-cpp-local-tls-") as tmp:
        folder = Path(tmp)
        key, cert = folder / "key.pem", folder / "cert.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                        "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                        "-keyout", str(key), "-out", str(cert)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        public = subprocess.check_output(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
        der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=public,
                             stdout=subprocess.PIPE, check=True).stdout
        pin = base64.b64encode(hashlib.sha256(der).digest()).decode()
        try:
            with sync_playwright() as playwright:
                for label, binary in [("rust", args.rust), ("cpp", args.cpp)]:
                    print(f"START local HTTP/TLS/browser {label}", flush=True)
                    results[label] = run(binary, args.caddy, template, playwright.chromium, folder, cert, key, pin, label)
                    print(f"PASS local HTTP/TLS/browser {label}", flush=True)
            assert results["rust"] == results["cpp"], results
        except BaseException as error:
            args.output.write_text(json.dumps(dict(passed=False, results=results, error=str(error)), indent=2)+"\n")
            raise
    args.output.write_text(json.dumps(dict(passed=True, results=results, processes_exited=True), indent=2)+"\n")
    print("PASS shared local proxy/TLS contracts", flush=True)
