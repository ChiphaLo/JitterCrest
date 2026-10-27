"""Offline wizard regression tests; never installs services or starts proxies."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("quickstart", Path(__file__).resolve().parents[1] / "deploy/quickstart.py")
quick = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quick)


def spec(role="main"):
    return dict(role=role, id="main" if role == "main" else "node-a", label="测试节点",
                public_origin="https://main.example.com" if role == "main" else "https://node.example.com",
                main_origin="https://main.example.com", udp4="8.8.8.8" if role == "probe" else "",
                port=32132, native_prefix="/opt/deps", service_user="jittercrest")


class QuickstartTests(unittest.TestCase):
    def test_main_and_descriptor_roundtrip(self):
        data = quick.prepare(spec("data"), [])
        probe = quick.prepare({**spec("probe"), "id": "probe-a", "public_origin": "https://probe.example.com"}, [])
        main = quick.prepare(spec(), [data["node"], probe["node"]])
        nodes = json.loads(main["env"]["SPEEDTEST_NODES"])
        self.assertEqual([x["id"] for x in nodes], ["local", "node-a", "probe-a"])
        self.assertEqual(main["env"]["SPEEDTEST_PROBE_ONLY_NODE_ID"], "probe-a")
        self.assertEqual(data["env"]["ALLOWED_ORIGINS"], "https://main.example.com")
        self.assertEqual(probe["env"]["SPEEDTEST_PROBE_ONLY"], "1")
        self.assertNotIn("SPEEDTEST_NODES", data["env"])

    def test_origin_normalization(self):
        self.assertEqual(quick.origin("https://EXAMPLE.com:443/"), "https://example.com")
        self.assertEqual(quick.origin("https://[2001:4860:4860::8888]:8443"), "https://[2001:4860:4860::8888]:8443")
        for item in ["http://example.com", "https://user@example.com", "https://x/a", "https://x/?q=1",
                     "https://999.1.1.1", "https://a.example\nrespond 200", "https://x:65536"]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                quick.origin(item)

    def test_input_and_mapping_validation(self):
        for update in [{"id": "../x"}, {"id": "local"}, {"label": "x\ny"},
                       {"port": True}, {"port": 22}, {"udp4": "127.0.0.1"},
                       {"native_prefix": "relative"}, {"service_user": "root"}, {"extra": "x"}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                quick.prepare({**spec(), **update}, [])
        with self.assertRaises(ValueError):
            quick.prepare({**spec("probe"), "udp4": ""}, [])
        with self.assertRaises(ValueError):
            quick.prepare({**spec("data"), "main_origin": "https://node.example.com"}, [])

    def test_import_guards(self):
        node = quick.prepare(spec("data"), [])["node"]
        for rows in [[node, node], [{**node, "base": "https://main.example.com"}],
                     [{**node, "role": "main"}], [{**node, "secret": "x"}]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                quick.prepare(spec(), rows)
        with self.assertRaises(ValueError):
            quick.prepare(spec("data"), [node])
        with self.assertRaises(ValueError):
            quick.prepare(spec(), [{**node, "role": "probe"}, {**node, "role": "probe", "id": "node-b", "base": "https://b.example.com"}])

    def test_generated_shell_roundtrip_and_quoting(self):
        with tempfile.TemporaryDirectory(prefix="jc space ") as directory:
            out = Path(directory) / "new setup"
            original = "中文'quote;$(false)"
            quick.generate({**spec(), "label": original}, [], out)
            for name in ["build.sh", "run.sh", "install-service.sh"]:
                subprocess.run(["bash", "-n", str(out / name)], check=True)
            result = subprocess.check_output(["bash", "-c", 'set -a; source "$1"; printf "%s" "$SPEEDTEST_NODES"', "shell", str(out / "runtime.env")], text=True)
            self.assertEqual(json.loads(result)[0]["label"], original)
            self.assertEqual((out / "runtime.env").stat().st_mode & 0o777, 0o600)
            self.assertIn("SPEEDTEST_WEB_UI=ON", (out / "build.sh").read_text())
            self.assertIn("User=jittercrest", (out / "service.example").read_text())
            self.assertIn('set -o noclobber', (out / "install-service.sh").read_text())
            self.assertNotIn("systemctl", (out / "run.sh").read_text())
            with self.assertRaises(FileExistsError):
                quick.generate(spec(), [], out)
            self.assertEqual(json.loads(result)[0]["label"], original)

    def test_agent_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "probe"
            quick.generate(spec("probe"), [], out)
            self.assertEqual(json.loads((out / "node.json").read_text())["role"], "probe")
            self.assertIn("SPEEDTEST_WEB_UI=OFF", (out / "build.sh").read_text())
            self.assertIn("reverse_proxy 127.0.0.1:32132", (out / "Caddyfile.example").read_text())

    def test_invalid_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "target"
            with self.assertRaises(ValueError):
                quick.generate({**spec(), "public_origin": "http://unsafe"}, [], out)
            self.assertFalse(out.exists())

    def test_add_node_atomic_backup_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "main"
            quick.generate(spec(), [], out)
            path = out / "runtime.env"
            original = path.read_bytes()
            before = path.stat()
            node = quick.prepare(spec("data"), [])["node"]
            backup, added = quick.add_node(out, node)
            self.assertEqual(added["id"], "node-a")
            self.assertEqual(backup.read_bytes(), original)
            after = path.stat()
            self.assertEqual((after.st_mode & 0o777, after.st_uid, after.st_gid),
                             (before.st_mode & 0o777, before.st_uid, before.st_gid))
            saved = path.read_bytes()
            with self.assertRaises(ValueError):
                quick.add_node(out, node)
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(len(list(out.glob("runtime.env.backup-*"))), 1)

    def test_add_probe_and_custom_configuration_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "main"
            quick.generate(spec(), [], out)
            probe = quick.prepare(spec("probe"), [])["node"]
            quick.add_node(out, probe)
            self.assertIn("SPEEDTEST_PROBE_ONLY_NODE_ID=node-a", (out / "runtime.env").read_text())
            next_probe = {**probe, "id": "node-b", "base": "https://b.example.com"}
            with self.assertRaises(ValueError):
                quick.add_node(out, next_probe)
            path = out / "runtime.env"
            path.write_text(path.read_text() + "# manually edited\n")
            original = path.read_bytes()
            data = {**probe, "role": "data", "id": "node-c", "base": "https://c.example.com"}
            with self.assertRaises(ValueError):
                quick.add_node(out, data)
            self.assertEqual(path.read_bytes(), original)

    def test_add_node_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            main = d / "main"
            agent = d / "agent"
            quick.generate(spec(), [], main)
            quick.generate(spec("data"), [], agent)
            result = subprocess.run([sys.executable, str(Path(quick.__file__)), "--existing", str(main),
                                     "--add-node", str(agent / "node.json")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("node-a", (main / "runtime.env").read_text())

    def test_noninteractive_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "config.json").write_text(json.dumps(spec()), encoding="utf-8")
            p = subprocess.run([sys.executable, str(Path(quick.__file__)), "--config", str(d / "config.json"),
                                "--output", str(d / "main")], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue((d / "main/runtime.env").is_file())


if __name__ == "__main__":
    unittest.main()
