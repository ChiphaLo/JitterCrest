"""Compare actual original Rust Config::from_env with independent C++ loading."""
import argparse
import ast
import json
import os
import re
from pathlib import Path
import subprocess


def expression(text, values):
    text = text.replace("_", "") if text.replace("_", "").isdigit() else text
    text = re.sub(r"(\d[\d_]*)\.min", r"(\1).min", text)
    tree = ast.parse(text, mode="eval").body
    def node(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, int): return n.value
        if isinstance(n, ast.Name): return values[n.id]
        if isinstance(n, ast.BinOp):
            a, b = node(n.left), node(n.right)
            if isinstance(n.op, ast.Sub): return a-b
            if isinstance(n.op, ast.LShift): return a << b
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "min":
            return min(node(n.func.value), node(n.args[0]))
        raise ValueError(text)
    return node(tree)


def compare(rust, cpp, output):
    rules = json.loads(Path(__file__).with_name("config_rules.json").read_text())
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("SPEEDTEST_", "MAX_", "RATE_", "SPEED_RATE_", "HTTP_", "RTC_",
                                 "DOWNLOAD_", "UPLOAD_", "BYTE_", "TRUST", "ALLOWED_", "PUBLIC_", "ENABLE_"))}
    cases = [{}]
    defaults = {}
    for field, (name, default, low, high) in rules:
        name = json.loads(name)
        defaults[field] = expression(default, defaults)
        minimum, maximum = expression(low, defaults), expression(high, defaults)
        for value in [str(minimum), str(maximum), "+"+str(minimum), str(minimum-1),
                      str(maximum+1), "", " 1", "1 ", "1.0", "1e3", "not-number", "18446744073709551616"]:
            cases.append({name: value})
    cases += [dict(SPEEDTEST_HOST="0.0.0.0", TRUST_PROXY_HEADERS="1"),
              dict(SPEEDTEST_HOST="::", TRUST_PROXY_HEADERS="1"),
              dict(SPEEDTEST_HOST="invalid"), dict(SPEEDTEST_HOST=""),
              dict(SPEEDTEST_HOST="0.0.0.0", SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST="1"),
              dict(MAX_RTC_SESSION_MS="10000"), dict(MAX_UPLOAD="1024", MAX_UPLOAD_MS="1000")]
    for ip in ["", "\u2000 8.8.8.8 \u00a0", "8.8.8.8", "192.0.2.1", "192.168.1.1", "::1", "bad"]:
        cases.append(dict(SPEEDTEST_WEBRTC_UDP4=ip))
    cases.append(dict(ALLOWED_ORIGINS=" \u00a0https://example.invalid\u2000,https://other.invalid ,", TRUSTED_PROXY_IPS=" 127.0.0.1,::1 "))
    cases.append(dict(SPEEDTEST_RUST_RTC_EXPERIMENTAL="1", SPEEDTEST_RTC_LEGACY_CONTROL="1",
                      SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST="1", PUBLIC_CORS="1", SPEEDTEST_ALLOW_CROSS_SITE="1"))
    rust_rows = []
    for env in cases:
        result = subprocess.run([rust, "--config"], env=dict(base, **env),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        rust_rows.append(dict(valid=True, config=json.loads(result.stdout)) if result.returncode == 0
                         else dict(valid=False, error=result.stderr.strip()))
    payload = "".join(json.dumps(env, ensure_ascii=False)+"\n" for env in cases)
    result = subprocess.run([*cpp, "--json-lines"], input=payload, encoding="utf-8",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert result.returncode == 0, result.stderr
    cpp_rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(cpp_rows) == len(rust_rows)
    failures = [dict(env=env, rust=a, cpp=b) for env, a, b in zip(cases, rust_rows, cpp_rows) if a != b]
    report = dict(passed=not failures, cases=len(cases), compared="validity, exact startup errors, 42 numeric and common trust/RTC flags", failures=failures)
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}))
    return not failures


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rust", required=True)
    p.add_argument("--cpp", required=True)
    p.add_argument("--wsl-distro")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    command = [a.cpp] if not a.wsl_distro else ["wsl", "-d", a.wsl_distro, "--exec", a.cpp]
    if not compare(a.rust, command, a.output): raise SystemExit(1)
