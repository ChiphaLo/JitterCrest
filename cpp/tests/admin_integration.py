"""Real C++/Caddy/TLS and browser acceptance for node management and enrollment."""
import argparse
import base64
from contextlib import ExitStack
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
from playwright.sync_api import sync_playwright, expect
from unittest.mock import patch
from urllib.request import build_opener, HTTPSHandler


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stop(p):
    if p.poll() is None:
        p.terminate()
    p.wait(timeout=15)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ui", type=Path, required=True)
    p.add_argument("--agent", type=Path, required=True)
    p.add_argument("--caddy", type=Path, required=True)
    p.add_argument("--screenshots", type=Path)
    p.add_argument("--docker-socket", type=Path)
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="jc-admin-") as td, ExitStack() as stack:
        root = Path(td)
        main_port, agent_port, main_tls, agent_tls = [port() for _ in range(4)]
        main_origin, agent_origin = f"https://localhost:{main_tls}", f"https://127.0.0.1:{agent_tls}"
        cert, key = root / "ca.pem", root / "ca.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                        "-keyout", str(key), "-out", str(cert)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pub = subprocess.check_output(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
        der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "der"], input=pub,
                             check=True, capture_output=True).stdout
        pin = base64.b64encode(hashlib.sha256(der).digest()).decode()
        ctx = ssl.create_default_context(cafile=str(cert))
        token = "integration-administrator-token-" + os.urandom(16).hex()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SPEEDTEST_", "ALLOWED_", "TRUST"))}
        env.update(SPEEDTEST_HOST="127.0.0.1", SPEEDTEST_RUST_RTC_EXPERIMENTAL="1",
                   SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST="1", ALLOWED_ORIGINS=main_origin,
                   SPEEDTEST_ALLOW_CROSS_SITE="1", RATE_MAX="1000")
        main_env = dict(env, PORT=str(main_port), SPEEDTEST_ADMIN_TOKEN=token,
                        SPEEDTEST_ADMIN_STATE=str(root / "state"), SPEEDTEST_PUBLIC_ORIGIN=main_origin,
                        HTTP_HEADERS_TIMEOUT_MS="1000")
        log = stack.enter_context(tempfile.TemporaryFile())
        def start_ui():
            proc = subprocess.Popen([str(a.ui.resolve())], env=main_env, stdout=log, stderr=log)
            stack.callback(stop, proc)
            return proc
        ui = start_ui()
        agent = subprocess.Popen([str(a.agent.resolve())],
                                 env=dict(env, PORT=str(agent_port), SPEEDTEST_NODE_NAME="tokyo"),
                                 stdout=log, stderr=log)
        stack.callback(stop, agent)
        config = root / "Caddyfile"
        config.write_text("{\n admin off\n auto_https off\n}\n" + "\n".join(
            f'{o} {{\n bind 127.0.0.1\n tls "{cert}" "{key}"\n reverse_proxy 127.0.0.1:{pn}\n}}'
            for o, pn in [(main_origin, main_port), (agent_origin, agent_port)]))
        proxy = subprocess.Popen([str(a.caddy.resolve()), "run", "--config", str(config), "--adapter", "caddyfile"],
                                 env=dict(os.environ, XDG_CONFIG_HOME=str(root / "config"),
                                          XDG_DATA_HOME=str(root / "data")), stdout=log, stderr=log)
        stack.callback(stop, proxy)
        def request(path, body=None, bearer=None, origin=main_origin):
            con = http.client.HTTPSConnection("localhost", main_tls, context=ctx, timeout=10)
            headers = {"Origin": origin}
            if bearer:
                headers["Authorization"] = "Bearer " + bearer
            if body is not None:
                headers["Content-Type"] = "application/json"
            try:
                con.request("POST" if body is not None else "GET", path,
                            json.dumps(body).encode() if body is not None else None, headers)
                r = con.getresponse()
                return r.status, dict(r.getheaders()), r.read()
            finally:
                con.close()
        for _ in range(100):
            try:
                if request("/health")[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.1)
        else:
            raise AssertionError("services failed to start")
        assert request("/api/admin/nodes")[0] == 401
        assert request("/api/admin/nodes", bearer=token, origin="https://forbidden.example")[0] == 403
        # Incomplete management bodies have a bounded lifetime even before authentication.
        with socket.create_connection(("127.0.0.1", main_port), timeout=4) as slow:
            slow.sendall(b'POST /api/enrollment/claim HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n{')
            assert slow.recv(1024) == b""
        assert request("/api/enrollment/claim", {"padding": "x" * 17000})[0] == 413
        with sync_playwright() as pw, ExitStack() as browser_cleanup:
            browser = pw.chromium.launch(headless=True, args=["--disable-gpu", "--ignore-certificate-errors-spki-list=" + pin])
            browser_cleanup.callback(browser.close)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            admin = context.new_page()
            errors = []
            admin.on("pageerror", lambda e: errors.append(str(e)))
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(main_origin + "/?testMode=webrtc-contract")
            admin.goto(main_origin + "/admin")
            admin.locator("#adminToken").fill(token)
            admin.locator("#login button").click()
            expect(admin.locator("#dashboard")).to_be_visible()
            admin.locator("#add").click()
            admin.locator("#nodeId").fill("tokyo")
            admin.locator("#nodeLabel").fill("Tokyo 日本")
            admin.locator("#nodeBase").fill(agent_origin)
            admin.locator("#nodeIp").fill("127.0.0.1")
            admin.locator("#generate").click()
            expect(admin.locator("#createNotice")).to_contain_text("public UDP")
            admin.locator("#nodeIp").fill("8.8.8.8")
            admin.locator("#generate").click()
            expect(admin.locator("#commandDialog")).to_be_visible()
            join = admin.locator("#joinKey").inner_text()
            command = admin.locator("#command").input_value()
            assert len(join) == 64 and join in command and "python3" in command
            subprocess.run(["bash", "-n"], input=command.encode(), check=True)
            assert request("/api/nodes")[2].find(join.encode()) == -1
            claimed = request("/api/enrollment/claim", {"id": "tokyo"}, join)
            assert claimed[0] == 200
            agent_key = json.loads(claimed[2])["agentKey"]
            assert request("/api/enrollment/claim", {"id": "tokyo"}, join)[0] == 409
            assert request("/api/enrollment/activate", {"id": "tokyo"}, agent_key)[0] == 200
            assert request("/api/enrollment/heartbeat", {"id": "tokyo"}, agent_key)[0] == 200
            public = request("/api/nodes")[2]
            assert join.encode() not in public and agent_key.encode() not in public and token.encode() not in public
            assert len(json.loads(public)["nodes"]) == 2
            admin.locator("#closeCommand").click()
            admin.locator("#refresh").click()
            expect(admin.locator('[data-node-id="tokyo"] .badge')).to_contain_text("在线")
            if a.screenshots:
                a.screenshots.mkdir(parents=True, exist_ok=True)
                admin.screenshot(path=str(a.screenshots / "admin-dashboard.png"), full_page=True)
            page.locator("#nodeSelect").click()
            page.locator("#nodeSearch").fill("tokyo")
            expect(page.locator(".node-choice")).to_have_count(1)
            if a.screenshots:
                page.screenshot(path=str(a.screenshots / "node-picker.png"))
            with page.expect_navigation() as navigation:
                page.locator('.node-choice[data-node-id="tokyo"]').click()
            assert agent_origin in navigation.value.headers["content-security-policy"]
            expect(page.locator("#nodeSelect")).to_have_attribute("data-node-id", "tokyo")
            assert "node=tokyo" in page.url
            # Actual browser cross-origin TLS fetch to the enrolled C++ backend.
            result = page.evaluate("async base => { const r=await fetch(base+'/health'); return [r.status,await r.json()]; }", agent_origin)
            assert result == [200, {"ok": True, "name": "tokyo"}]
            row = admin.locator('[data-node-id="tokyo"]')
            row.locator("input").fill("Tokyo renamed")
            row.get_by_text("保存名称", exact=True).click()
            expect(row.locator("strong")).to_have_text("Tokyo renamed")
            row.get_by_text("停用", exact=True).click()
            expect(row.get_by_text("启用", exact=True)).to_be_visible()
            assert len(json.loads(request("/api/nodes")[2])["nodes"]) == 1
            row.get_by_text("启用", exact=True).click()
            expect(row.get_by_text("停用", exact=True)).to_be_visible()
            admin.on("dialog", lambda d: d.accept())
            row.get_by_text("移除", exact=True).click()
            expect(admin.locator('[data-node-id="tokyo"]')).to_have_count(0)
            assert request("/api/enrollment/heartbeat", {"id": "tokyo"}, agent_key)[0] == 401
            # Narrow viewport, keyboard dismissal and session-only management token.
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#nodeSelect").click()
            expect(page.locator("#nodeDialog")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if a.screenshots:
                page.screenshot(path=str(a.screenshots / "mobile-node-picker.png"))
            page.keyboard.press("Escape")
            expect(page.locator("#nodeDialog")).not_to_be_visible()
            assert admin.evaluate("localStorage.getItem('adminToken')") is None
            admin.locator("#logout").click()
            expect(admin.locator("#login")).to_be_visible()
            assert not errors, errors
        if a.docker_socket:
            origin_docker = f"https://127.0.0.1:{port()}"
            created = request("/api/admin/enrollments", dict(id="docker-vps", label="Docker VPS",
                              base=origin_docker, udp4="8.8.8.8", role="data"), token)
            assert created[0] == 201
            install_spec = importlib.util.spec_from_file_location("enroll_agent", Path(__file__).resolve().parents[1] / "deploy/enroll_agent.py")
            installer = importlib.util.module_from_spec(install_spec)
            install_spec.loader.exec_module(installer)
            real_path, real_run = Path, installer.run
            def private_path(value):
                return root / "installed" if str(value) == "/opt/jittercrest-agents" else real_path(value)
            tls_opener = build_opener(HTTPSHandler(context=ctx))
            def docker_run(*args):
                args = list(args)
                if args[:2] == ["docker", "build"]:
                    args[2:2] = ["--network", "host"]
                    build_log = root / "docker-build.log"
                    try:
                        with build_log.open("ab") as output:
                            subprocess.run(args, check=True, stdout=output, stderr=output)
                    except subprocess.CalledProcessError as error:
                        raise AssertionError("agent Docker build failed:\n" + build_log.read_text()[-5000:]) from error
                    return
                if args[:2] == ["docker", "run"]:
                    mounts = ["-v", str(cert) + ":/etc/ssl/certs/ca-certificates.crt:ro"]
                    if args[-1] == installer.CADDY_IMAGE:
                        mounts += ["-v", str(cert) + ":/test.pem:ro", "-v", str(key) + ":/test.key:ro"]
                    args[-1:-1] = mounts
                real_run(*args)
            def test_caddy(base, backend_port):
                return ("{\n admin off\n auto_https off\n}\n" + base +
                        f" {{\n tls /test.pem /test.key\n reverse_proxy 127.0.0.1:{backend_port}\n}}\n")
            docker_env = dict(os.environ, DOCKER_HOST="unix://" + str(a.docker_socket))
            def cleanup_containers():
                subprocess.run(["docker", "rm", "-f", "jittercrest-docker-vps-tls", "jittercrest-docker-vps"],
                               env=docker_env, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            stack.callback(cleanup_containers)
            with patch.object(installer, "Path", side_effect=private_path), \
                    patch.object(installer, "urlopen", side_effect=tls_opener.open), \
                    patch.object(installer, "run", side_effect=docker_run), \
                    patch.object(installer, "caddyfile", side_effect=test_caddy), \
                    patch.dict(os.environ, docker_env, clear=True):
                installer.install(main_origin, "docker-vps", json.loads(created[2])["key"])
            listed = json.loads(request("/api/admin/nodes", bearer=token)[2])["nodes"]
            assert next(n for n in listed if n["id"] == "docker-vps")["status"] == "online"
        # Durable registry remains authoritative after restarting the main service.
        stop(ui)
        ui = start_ui()
        for _ in range(100):
            try:
                if request("/api/admin/nodes", bearer=token)[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.1)
        state = json.loads(request("/api/admin/nodes", bearer=token)[2])
        assert len(state["nodes"]) == (2 if a.docker_socket else 1)
        if a.docker_socket:
            for _ in range(40):
                listed = json.loads(request("/api/admin/nodes", bearer=token)[2])["nodes"]
                if next(n for n in listed if n["id"] == "docker-vps")["status"] == "online":
                    break
                time.sleep(1)
            else:
                raise AssertionError("the actual C++ agent did not heartbeat after controller restart")
        assert request("/enroll/agent.py")[0] == 200
        source = request("/enroll/source.tar.gz")
        assert source[0] == 200 and source[2].startswith(b"\x1f\x8b")
        print("PASS real C++/TLS admin login, key claim/replay, activation, picker, browser CORS, management and persistence")


if __name__ == "__main__":
    main()
