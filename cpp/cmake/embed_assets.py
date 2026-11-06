"""Embed UI assets and the allowlisted C++ agent build context."""
import argparse
import gzip
import io
from pathlib import Path
import tarfile


def agent_bundle(backend):
    paths = [backend / "CMakeLists.txt", backend.parent / "LICENSE", backend.parent / "THIRD_PARTY.md",
             backend.parent / ".dockerignore",
             backend / "deploy/package_release.py", backend / "deploy/enroll_agent.py",
             backend / "deploy/Dockerfile.agent", backend / "deploy/speedtest-cpp-staging.service",
             backend / "deploy/staging.env.example", backend / "deploy/Caddyfile.staging.example"]
    for pattern in ["src/*.cpp", "include/speedtest/*.hpp", "tests/*.cpp", "tests/*.hpp", "cmake/*.py",
                    "cmake/*.sh", "cmake/patches/*.patch"]:
        paths.extend(sorted(backend.glob(pattern)))
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for file in paths:
                data = file.read_bytes()
                info = tarfile.TarInfo(file.relative_to(backend.parent).as_posix())
                info.size, info.mode = len(data), 0o644
                archive.addfile(info, io.BytesIO(data))
            for name in ["index.html", "admin.html", "favicon.svg"]:
                info = tarfile.TarInfo("public/" + name)
                info.size, info.mode = 0, 0o644
                archive.addfile(info, io.BytesIO())
    return output.getvalue()


def text_function(name, text):
    assert ')SPEEDTEST_ASSET"' not in text
    chunks = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    parts = ",\n".join('R"SPEEDTEST_ASSET(' + s + ')SPEEDTEST_ASSET"' for s in chunks)
    return (f"std::string {name}(){{\nconst std::array<std::string_view,{len(chunks)}> parts={{{parts}}};\n"
            f"std::string out;out.reserve({len(text.encode())});\n"
            "for(auto part:parts)out+=part;return out;\n}\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--assets", required=True, type=Path)
    p.add_argument("--backend", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--web-ui", required=True)
    a = p.parse_args()
    enabled = a.web_ui.upper() in ("ON", "1", "TRUE")
    page = (a.assets / "index.html").read_text(encoding="utf-8") if enabled else ""
    icon = (a.assets / "favicon.svg").read_text(encoding="utf-8") if enabled else ""
    admin = (a.backend.parent / "public/admin.html").read_text(encoding="utf-8") if enabled else ""
    installer = (a.backend / "deploy/enroll_agent.py").read_text(encoding="utf-8") if enabled else ""
    bundle = agent_bundle(a.backend) if enabled else b""
    byte_values = ",\n".join(",".join(str(n) for n in bundle[i:i + 256])
                             for i in range(0, len(bundle), 256)) or "0"
    source = ('#include "speedtest/http.hpp"\n#include <array>\nnamespace speedtest::assets {\n'
              + f"bool web_ui(){{return {str(enabled).lower()};}}\n"
              + text_function("page", page) + text_function("admin_page", admin)
              + 'std::string_view icon(){return R"SPEEDTEST_ASSET(' + icon + ')SPEEDTEST_ASSET";}\n'
              + 'std::string_view installer(){return R"SPEEDTEST_ASSET(' + installer + ')SPEEDTEST_ASSET";}\n'
              + f"std::string_view agent_source(){{static constexpr unsigned char data[]={{ {byte_values} }};"
              + f"return {{reinterpret_cast<const char*>(data),{len(bundle)}}};}}\n}}\n")
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(source, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
