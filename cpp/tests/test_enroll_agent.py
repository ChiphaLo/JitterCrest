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
        self.assertIn("profile shortlived", agent.caddyfile("https://8.8.8.8", 32132))
        self.assertNotIn("profile shortlived", agent.caddyfile("https://node.example.com", 32132))

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
            return root if str(value) == "/opt/jittercrest-agents" else real_path(value)
        claimed = dict(id="fixture", main="https://main.example.com", base="https://node.example.com",
                       udp4="8.8.8.8", role="data", agentKey="b" * 64)
        calls, api_calls = [], []
        def api(main, route, key, node):
            api_calls.append((route, key))
            if route.endswith("claim"):
                return dict(claimed)
            if fail_activate:
                raise OSError("activation unavailable")
            return {"ok": True}
        def urlopen(url, timeout):
            if str(url).endswith("source.tar.gz"):
                return Response(archive())
            return Response(b'{"ok":true,"name":"fixture"}')
        patches = [patch.object(agent, "Path", side_effect=path), patch.object(agent.os, "geteuid", return_value=0),
                   patch.object(agent.sys, "platform", "linux"), patch.object(agent, "api", side_effect=api),
                   patch.object(agent, "urlopen", side_effect=urlopen), patch.object(agent, "available_port", return_value=32132),
                   patch.object(agent.socket, "socket"),
                   patch.object(agent.shutil, "which", return_value="/usr/bin/docker"),
                   patch.object(agent, "run", side_effect=lambda *args: calls.append(args)),
                   patch.object(agent.subprocess, "run", return_value=subprocess.CompletedProcess([], 1))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return root, calls, api_calls

    def test_full_install_and_resume(self):
        root, calls, api_calls = self.install_fixture()
        agent.install("https://main.example.com", "fixture", "a" * 64)
        folder = root / "fixture"
        env = (folder / "agent.env").read_text()
        self.assertIn("SPEEDTEST_AGENT_KEY=" + "b" * 64, env)
        self.assertEqual((folder / "agent.env").stat().st_mode & 0o777, 0o600)
        self.assertIn("SPEEDTEST_CONTROLLER_ORIGIN=https://main.example.com", env)
        self.assertEqual(len([c for c in calls if c[:2] == ("docker", "run")]), 2)
        self.assertNotIn("b" * 64, str(calls))
        self.assertEqual([x[0] for x in api_calls], ["/api/enrollment/claim", "/api/enrollment/activate"])
        api_calls.clear()
        agent.install("https://main.example.com", "fixture", "a" * 64)
        self.assertEqual([x[0] for x in api_calls], ["/api/enrollment/activate"])

    def test_failed_activation_cleans_owned_containers(self):
        root, _, _ = self.install_fixture(fail_activate=True)
        with self.assertRaises(OSError):
            agent.install("https://main.example.com", "fixture", "a" * 64)
        self.assertTrue((root / "fixture/registration.json").is_file())
        removed = [c.args[0][-1] for c in agent.subprocess.run.call_args_list
                   if c.args and c.args[0][:3] == ["docker", "rm", "-f"]]
        self.assertEqual(removed, ["jittercrest-fixture-tls", "jittercrest-fixture"])


if __name__ == "__main__":
    unittest.main()
