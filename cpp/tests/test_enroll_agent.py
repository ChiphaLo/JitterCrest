"""Installer unit tests: execute orchestration without changing the host."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "deploy/enroll_agent.py"
spec = importlib.util.spec_from_file_location("enroll_agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def archive():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as t:
        entry = tarfile.TarInfo("cpp/deploy/Dockerfile.agent")
        body = b"FROM ubuntu:24.04\n"
        entry.size = len(body)
        t.addfile(entry, io.BytesIO(body))
    return data.getvalue()


class Response(io.BytesIO):
    status = 200


class InstallerTests(unittest.TestCase):
    def test_url_and_caddy_configuration(self):
        for value in ["http://node.example", "https://u:p@node.example", "https://node.example/a",
                      "https://node.example\nrespond", "https://node.example?q=a"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                agent.origin(value)
        ip_site = agent.caddyfile("https://8.8.8.8:8444", 32132)
        self.assertIn("profile shortlived", ip_site)
        self.assertIn("https://8.8.8.8 {", ip_site)
        self.assertNotIn("profile shortlived", agent.caddyfile("https://node.example.com:8444", 32132))
        self.assertEqual(agent.edge_port("https://node.example.com:8444"), 8444)
        for value in ["https://node.example.com", "https://node.example.com:443", "https://node.example.com:80"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                agent.edge_port(value)

    def test_certificate_sync_script(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self.assertIsNone(agent.certificate_source(folder, "node.example.com"))
            source = folder / "caddy-data/caddy/certificates/acme/node.example.com"
            source.mkdir(parents=True)
            (source / "node.example.com.crt").write_text("cert")
            (source / "node.example.com.key").write_text("key")
            self.assertEqual(agent.certificate_source(folder, "node.example.com"), source)
            script = agent.sync_script(folder, "node.example.com")
            subprocess.run(["sh", "-n"], input=script, text=True, check=True)
            if os.geteuid() == 0:
                (folder / "sync.sh").write_text(script)
                subprocess.run(["sh", str(folder / "sync.sh")], check=True)
                key = folder / "tls/node.key"
                self.assertEqual(key.read_text(), "key")
                self.assertEqual((key.stat().st_uid, key.stat().st_mode & 0o777), (agent.AGENT_UID, 0o400))

    def test_archive_rejects_escaping_and_links(self):
        with tempfile.TemporaryDirectory() as d:
            for name, kind in [("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)]:
                b = io.BytesIO()
                with tarfile.open(fileobj=b, mode="w:gz") as t:
                    e = tarfile.TarInfo(name)
                    e.type = kind
                    e.linkname = "/etc/passwd"
                    t.addfile(e)
                b.seek(0)
                archive_path = Path(d) / "malformed.tar.gz"
                archive_path.write_bytes(b.getvalue())
                with self.subTest(name=name), self.assertRaises(ValueError):
                    agent.unpack(archive_path, Path(d))

    def install_fixture(self, fail_activate=False):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        real_path = Path
        def path(value):
            if str(value) == agent.SYSTEMD:
                return root / "systemd"
            return root if str(value) == "/opt/jittercrest-agents" else real_path(value)
        (root / "systemd").mkdir()
        claimed = dict(id="fixture", main="https://main.example.com", base="https://node.example.com:8444",
                       udp4="8.8.8.8", role="data", agentKey="b" * 64)
        calls, api_calls, opened = [], [], []
        def api(main, route, key, node):
            api_calls.append((route, key))
            if route.endswith("claim"):
                return dict(claimed)
            if fail_activate:
                raise OSError("activation unavailable")
            return {"ok": True}
        def urlopen(url, timeout):
            opened.append(str(url))
            if str(url).endswith("source.tar.gz"):
                return Response(archive())
            return Response(b'{"ok":true,"name":"fixture"}')
        patches = [patch.object(agent, "Path", side_effect=path), patch.object(agent.os, "geteuid", return_value=0),
                   patch.object(agent.sys, "platform", "linux"), patch.object(agent, "api", side_effect=api),
                   patch.object(agent, "urlopen", side_effect=urlopen), patch.object(agent, "available_port", return_value=32132),
                   patch.object(agent.socket, "socket"),
                   patch.object(agent, "certificate_source", return_value=root),
                   patch.object(agent, "edge_ready", return_value=True),
                   patch.object(agent.shutil, "which", return_value="/usr/bin/docker"),
                   patch.object(agent, "run", side_effect=lambda *args: calls.append(args)),
                   patch.object(agent.subprocess, "run", return_value=subprocess.CompletedProcess([], 1))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return root, calls, api_calls, opened

    def test_full_install_and_resume(self):
        root, calls, api_calls, opened = self.install_fixture()
        agent.install("https://main.example.com", "fixture", "a" * 64)
        folder = root / "fixture"
        env = (folder / "agent.env").read_text()
        self.assertIn("SPEEDTEST_AGENT_KEY=" + "b" * 64, env)
        self.assertIn("SPEEDTEST_TLS_PORT=8444", env)
        self.assertIn("SPEEDTEST_TLS_CERT=/tls/node.crt", env)
        self.assertEqual((folder / "agent.env").stat().st_mode & 0o777, 0o600)
        self.assertIn("SPEEDTEST_CONTROLLER_ORIGIN=https://main.example.com", env)
        runs = [c for c in calls if c[:2] == ("docker", "run")]
        self.assertEqual([r[r.index("--name") + 1] for r in runs], ["jittercrest-fixture-tls", "jittercrest-fixture"])
        self.assertIn(str(folder / "tls") + ":/tls:ro", runs[1])
        self.assertIn(("systemctl", "enable", "--now", "jittercrest-fixture-tls-sync.timer"), calls)
        self.assertLess(calls.index(("systemctl", "start", "jittercrest-fixture-tls-sync.service")), calls.index(runs[1]))
        self.assertIn(str(folder / "tls-sync.sh"), (root / "systemd/jittercrest-fixture-tls-sync.service").read_text())
        self.assertIn("https://node.example.com/health", opened)
        self.assertIn("https://node.example.com {", (folder / "Caddyfile").read_text())
        self.assertNotIn("b" * 64, str(calls))
        self.assertEqual([x[0] for x in api_calls], ["/api/enrollment/claim", "/api/enrollment/activate"])
        api_calls.clear()
        agent.install("https://main.example.com", "fixture", "a" * 64)
        self.assertEqual([x[0] for x in api_calls], ["/api/enrollment/activate"])

    def test_failed_activation_cleans_owned_containers(self):
        root, _, _, _ = self.install_fixture(fail_activate=True)
        with self.assertRaises(OSError):
            agent.install("https://main.example.com", "fixture", "a" * 64)
        self.assertTrue((root / "fixture/registration.json").is_file())
        removed = [c.args[0][-1] for c in agent.subprocess.run.call_args_list
                   if c.args and c.args[0][:3] == ["docker", "rm", "-f"]]
        self.assertEqual(removed, ["jittercrest-fixture", "jittercrest-fixture-tls"])


if __name__ == "__main__":
    unittest.main()
