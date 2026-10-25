"""Small deterministic checks for benchmark reporting, not performance results."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from benchmark import core_inputs, snapshot
from summarize import grouped, load, summary


class Reporting(unittest.TestCase):
    def test_live_process_counters(self):
        value = snapshot(os.getpid())
        self.assertGreater(value["rss_bytes"], 0)
        self.assertGreaterEqual(value["peak_rss_bytes"], value["rss_bytes"])
        self.assertGreater(value["threads"], 0)
        self.assertGreaterEqual(value["cpu_seconds"], 0)

    def test_deterministic_fixtures(self):
        cases = core_inputs(.001)
        self.assertEqual(cases, core_inputs(.001))
        frames = cases[0]["frames"]
        self.assertEqual(sum(len(frame) == 254 for frame in frames), 32)
        self.assertEqual(sum(len(frame) == 256 for frame in frames), 224)
        self.assertEqual([case["dataset"] for case in cases][1:3], ["ASCII IDs", "Unicode IDs"])

    def test_missing_pair_rejected(self):
        with self.assertRaises(AssertionError):
            grouped(dict(rows=[dict(case=0, backend="rust", round=1)]))

    def test_incomplete_or_validation_report_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            for complete, passed, purpose in [(False, True, "performance"),
                                               (True, False, "performance"),
                                               (True, True, "validation_only")]:
                path.write_text(json.dumps(dict(complete=complete, passed=passed, purpose=purpose)))
                with self.assertRaises(AssertionError):
                    load(path)

    def test_aggregate_and_redaction(self):
        metadata = dict(configuration=dict(rounds=1, server_cpus="0,2", client_cpus="4,6,8,10",
                        driver_cpu=12, warmup=1, duration=5),
                        environment=dict(kernel="fixture", machine="x86_64", python="fixture"),
                        binaries={b:dict(sha256="a"*64, path="/private/DO_NOT_PUBLISH") for b in ["rust","cpp"]})
        core = copy.deepcopy(metadata)
        core["rows"] = [dict(case=0,backend=b,round=1,operation="decode",dataset="fixture",
                             ns_per_operation=10, checksum=19) for b in ["rust","cpp"]]
        http = copy.deepcopy(metadata)
        http["rows"] = [dict(case=0,backend=b,round=1,method="GET",path="/download?size=1024",
            payload_bytes=1024,concurrency=1,payload_mbps=8,requests_per_second=1000,
            requests=5000,server_cpu_seconds=.5,server_cpu_percent_one_core=10,
            p50_ms=1,p95_ms=2,p99_ms=3,idle_rss_bytes=10<<20,peak_rss_bytes=12<<20,
            server_threads=2,client_resources=dict(cpu_seconds=1),observed_wall_seconds=5,
            errors=dict(connect=0,read=0,write=0,status=0,timeout=0)) for b in ["rust","cpp"]]
        result = summary(core,http,None)
        self.assertIn("8.000 [8.000, 8.000]", result)
        self.assertIn("1.000 / 2.000 / 3.000", result)
        self.assertIn("10.00 / 12.00", result)
        self.assertNotIn("DO_NOT_PUBLISH", result)
        self.assertIn("Not yet measured", result)
        capacity = dict(passed=True, processes_exited=True, results={backend: [dict(
            requested=n, accepted_established=n, idle_rss_bytes=10<<20,
            active_rss_bytes=12<<20, peak_rss_bytes=13<<20, threads=2,
            released_connections=True) for n in [1, 16, 64, 128]] for backend in ["rust", "cpp"]})
        observed = summary(core, http, None, capacity)
        self.assertIn("128 / 128", observed)
        self.assertNotIn("DO_NOT_PUBLISH", observed)
        capacity["results"]["cpp"][-1]["accepted_established"] = 127
        with self.assertRaises(AssertionError):
            summary(core, http, None, capacity)


if __name__ == "__main__":
    unittest.main()
