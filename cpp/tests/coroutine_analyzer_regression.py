"""Reproduce two Clang18/Boost1.83 coroutine-model diagnostics without hiding them.

This expected-diagnostic regression is NOT a clean full-service Clang-Tidy gate.
The runtime fixture is separately compiled/run under the normal sanitizer CTests.
Known-bad analyzer controls are generated temporarily and are never executed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def capture(command, path, cwd=None):
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
    text = (result.stdout + result.stderr).decode(errors="replace")
    path.write_text(text)
    return result.returncode, text


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--build-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--clang-tidy", default="clang-tidy-18")
    p.add_argument("--clang", default="clang++-18")
    args = p.parse_args()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build = args.build_dir.resolve()
    source = Path(__file__).with_name("coroutine_analysis_fixture.cpp").resolve()
    entries = json.loads((build / "compile_commands.json").read_text())
    entry = next(e for e in entries if Path(e["file"]).resolve() == source)
    command = entry.get("arguments") or shlex.split(entry["command"])
    flags, i = [], 1
    while i < len(command):
        arg = command[i]
        if arg == "-o":
            i += 2
            continue
        if arg != "-c" and arg != str(source):
            flags.append(arg)
        i += 1
    fixture_files = [source, source.with_suffix(".hpp"), source.with_name("coroutine_lifetime_test.cpp")]
    identities = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in fixture_files}
    for f in fixture_files:
        assert "NOLINT" not in f.read_text() and "__clang_analyzer__" not in f.read_text()
    report = dict(passed=False, purpose="analyzer_modeling_regression",
                  full_service_tidy_clean=False, raw_expected_failure_preserved=True)
    try:
        version = subprocess.check_output([args.clang_tidy, "--version"], text=True)
        assert re.search(r"version 18\.", version), "This exact modeling regression targets Clang18"
        settings = ["-checks=-*,clang-analyzer-*", "-warnings-as-errors=*", "-header-filter=.*", "-system-headers"]
        raw_exit, diagnostics = capture([args.clang_tidy, str(source), "-p", str(build), *settings],
                                       args.output.with_suffix(".positive.log"))
        expected = {
            "clang-analyzer-core.CallAndMessage": "attached_thread_",
            "clang-analyzer-core.NullDereference": "resume_context_",
        }
        actual = re.findall(r"error:.*?\[(clang-analyzer-[^,\]]+)", diagnostics)
        assert raw_exit != 0 and sorted(actual) == sorted(expected), diagnostics
        assert all(field in diagnostics for field in expected.values())
        # AST includes the language-mandated promise construction/initial suspend;
        # the analyzer's as-written-body CFG omits those entries. Keep both raw
        # dumps so this is inspectable evidence rather than a claimed runtime fact.
        function = "speedtest::analysis_fixture::Session::request_timeout"
        ast_exit, ast = capture([args.clang, *flags, "-fsyntax-only", "-Xclang", "-ast-dump", "-Xclang",
                                "-ast-dump-filter=" + function, str(source)],
                               args.output.with_suffix(".ast.txt"), entry["directory"])
        cfg_exit, cfg = capture([args.clang, *flags, "--analyze", "-Xclang", "-analyzer-checker=debug.DumpCFG",
                                "-Xclang", "-analyze-function", "-Xclang", function + "()", str(source)],
                               args.output.with_suffix(".cfg.txt"), entry["directory"])
        promise_constructed = bool(re.search(r"VarDecl[^\n]*__promise[^\n]*\n[^\n]*CXXConstructExpr", ast))
        assert ast_exit == cfg_exit == 0 and promise_constructed and "initial_suspend" in ast
        assert "Session::request_timeout()" in cfg and "__promise" in cfg and "await_transform" in cfg
        assert not re.search(r"__promise\s*;", cfg) and "initial_suspend" not in cfg
        with tempfile.TemporaryDirectory(prefix="coroutine-analyzer-negative-control-") as tmp:
            control = Path(tmp) / "known_bad.cpp"
            control.write_text("struct Object { int value; void touch() { value=1; } };\n"
                               "void known_bad_call() { Object *pointer; pointer->touch(); }\n"
                               "int known_bad_dereference() { int *pointer=nullptr; return *pointer; }\n")
            bad_exit, bad = capture([args.clang_tidy, str(control), *settings, "--", "-std=c++20"],
                                   args.output.with_suffix(".negative.log"))
            assert bad_exit != 0 and all(check in bad for check in expected), bad
        assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
                   for name, digest in identities.items()), "fixture changed during analysis"
        report.update(passed=True, compiler_version=version.strip(), expected_raw_exit=raw_exit,
                      exact_two_diagnostics_reproduced=True, expected_checks=list(expected),
                      ast_promise_construction=True, ast_initial_suspend=True,
                      cfg_omits_promise_construction_and_initial_suspend=True,
                      genuine_defect_controls_detected=True, no_fixture_suppressions=True,
                      runtime_safety_claimed_by_this_script=False, sha256=identities)
    except BaseException as error:
        report.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS exact two-diagnostic modeling regression and known-bad controls; NOT a clean production Tidy gate")
