#!/usr/bin/env python3
"""Generate a C++ main/agent setup without installing or changing live services."""
import argparse
import fcntl
import getpass
import ipaddress
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
NODE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def origin(value):
    if not isinstance(value, str) or not value.isascii() or any(c.isspace() for c in value):
        raise ValueError("origin must be an ASCII HTTPS origin without whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid origin port or address") from error
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("origin must be https://host[:port], without credentials, path or query")
    host = parsed.hostname.lower()
    try:
        addr = ipaddress.ip_address(host)
        host = f"[{addr.compressed}]" if addr.version == 6 else addr.compressed
    except ValueError:
        labels = host.split(".")
        if len(labels) == 4 and all(v.isdigit() for v in labels):
            raise ValueError("invalid IPv4 address")
        if (len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", v)
                                   for v in labels)):
            raise ValueError("invalid DNS hostname")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid origin port")
    return "https://" + host + (f":{port}" if port and port != 443 else "")


def node_id(value):
    if not isinstance(value, str) or not NODE_ID.fullmatch(value) or value == "local":
        raise ValueError("node ID must be 1-64 lowercase letters/digits/_/- and cannot be local")
    return value


def label(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 80 or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("label must be 1-80 printable characters")
    return value.strip()


def udp4(value, role):
    if not value:
        if role == "probe":
            raise ValueError("probe-only agents require a public IPv4 mapping")
        return ""
    try:
        addr = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise ValueError("UDP mapping must be a public IPv4 address") from error
    if not addr.is_global or addr.is_multicast:
        raise ValueError("UDP mapping must be a public unicast IPv4 address")
    return str(addr)


def descriptor(value):
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("role") not in ("data", "probe"):
        raise ValueError("invalid node descriptor schema or role")
    if set(value) != {"schema", "role", "id", "label", "base"}:
        raise ValueError("unexpected node descriptor fields")
    return dict(schema=1, role=value["role"], id=node_id(value["id"]),
                label=label(value["label"]), base=origin(value["base"]))


def env_text(env):
    return "".join(f"{k}={shlex.quote(v)}\n" for k, v in env.items())


def add_node(directory, value):
    """Add a descriptor to a generated main environment without rebuilding."""
    node = descriptor(value)
    folder = Path(directory).resolve(strict=True)
    path = folder / "runtime.env"
    if not path.is_file() or path.is_symlink():
        raise ValueError("existing runtime.env must be a regular file")
    lockpath = folder / ".quickstart.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lockpath, flags, 0o600)
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original_stat = path.stat()
        original = path.read_text(encoding="utf-8")
        env = {}
        for line in original.splitlines():
            key, sep, raw = line.partition("=")
            if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in env:
                raise ValueError("runtime.env is not a canonical generated configuration")
            values = shlex.split(raw)
            if len(values) != 1:
                raise ValueError("runtime.env contains unsupported shell syntax")
            env[key] = values[0]
        if env_text(env) != original or "SPEEDTEST_NODES" not in env or env.get("SPEEDTEST_PROBE_ONLY") == "1":
            raise ValueError("expected an unmodified generated main-node runtime.env")
        nodes = json.loads(env["SPEEDTEST_NODES"])
        if (not isinstance(nodes, list) or not nodes or
                any(not isinstance(n, dict) or set(n) != {"id", "label", "base"} for n in nodes) or
                nodes[0]["id"] != "local" or nodes[0]["base"] != ""):
            raise ValueError("unsupported node list; refusing to overwrite custom configuration")
        if any(n["id"] == node["id"] or n["base"] == node["base"] for n in nodes):
            raise ValueError("node ID or origin already exists")
        if node["base"] == env.get("ALLOWED_ORIGINS"):
            raise ValueError("agent origin must differ from the main origin")
        if node["role"] == "probe":
            if "SPEEDTEST_PROBE_ONLY_NODE_ID" in env:
                raise ValueError("a probe-only node is already configured")
            env["SPEEDTEST_PROBE_ONLY_NODE_ID"] = node["id"]
        nodes.append(dict(id=node["id"], label=node["label"], base=node["base"]))
        env["SPEEDTEST_NODES"] = json.dumps(nodes, ensure_ascii=False, separators=(",", ":"))
        replacement = env_text(env)
        backup_fd, backup_name = tempfile.mkstemp(prefix="runtime.env.backup-", dir=folder)
        with os.fdopen(backup_fd, "w", encoding="utf-8", newline="\n") as backup:
            backup.write(original)
            backup.flush()
            os.fsync(backup.fileno())
        temp_name = None
        try:
            temp_fd, temp_name = tempfile.mkstemp(prefix=".runtime.env-", dir=folder)
            with os.fdopen(temp_fd, "w", encoding="utf-8", newline="\n") as updated:
                os.fchmod(updated.fileno(), stat.S_IMODE(original_stat.st_mode))
                current_stat = os.fstat(updated.fileno())
                if (current_stat.st_uid, current_stat.st_gid) != (original_stat.st_uid, original_stat.st_gid):
                    os.fchown(updated.fileno(), original_stat.st_uid, original_stat.st_gid)
                updated.write(replacement)
                updated.flush()
                os.fsync(updated.fileno())
            current_stat = path.stat()
            if (path.is_symlink() or (current_stat.st_dev, current_stat.st_ino) !=
                    (original_stat.st_dev, original_stat.st_ino) or path.read_text(encoding="utf-8") != original):
                raise ValueError("runtime.env changed during update; refusing to overwrite")
            os.replace(temp_name, path)
            temp_name = None
        finally:
            if temp_name is not None:
                os.unlink(temp_name)
        return Path(backup_name), node


def prepare(spec, imported):
    if not isinstance(spec, dict):
        raise ValueError("configuration must be a JSON object")
    allowed = {"role", "id", "label", "public_origin", "main_origin", "udp4", "port", "native_prefix", "service_user"}
    if set(spec) - allowed:
        raise ValueError("unexpected configuration keys: " + ", ".join(sorted(set(spec) - allowed)))
    role = spec.get("role")
    if role not in ("main", "data", "probe"):
        raise ValueError("role must be main, data or probe")
    name = node_id(spec.get("id"))
    title = label(spec.get("label"))
    public = origin(spec.get("public_origin"))
    main = origin(spec.get("main_origin", public))
    if role == "main" and main != public:
        raise ValueError("main_origin must equal public_origin for the main node")
    if role != "main" and main == public:
        raise ValueError("an independent agent must have a different origin from the main node")
    if role != "main" and imported:
        raise ValueError("only the main node may import descriptors")
    port = spec.get("port", 32132)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("backend port must be an integer from 1024 to 65535")
    ip = udp4(spec.get("udp4", ""), role)
    prefix_value = spec.get("native_prefix", "/opt/jittercrest-deps")
    if not isinstance(prefix_value, str) or not Path(prefix_value).is_absolute() or any(c in prefix_value for c in "\n\r\x00"):
        raise ValueError("native_prefix must be an absolute path without control characters")
    default_user = getpass.getuser()
    if default_user == "root":
        default_user = "jittercrest"
    service_user = spec.get("service_user", default_user)
    if not isinstance(service_user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", service_user) or service_user == "root":
        raise ValueError("service_user must be a non-root Linux username")
    nodes = [descriptor(d) for d in imported]
    ids = [n["id"] for n in nodes]
    if len(ids) != len(set(ids)) or name in ids:
        raise ValueError("duplicate node ID")
    if any(n["base"] == public for n in nodes) or len({n["base"] for n in nodes}) != len(nodes):
        raise ValueError("duplicate public origin")
    if sum(n["role"] == "probe" for n in nodes) > 1:
        raise ValueError("the wizard supports at most one probe-only node")
    env = dict(SPEEDTEST_HOST="127.0.0.1", PORT=str(port), SPEEDTEST_NODE_NAME=name,
               TRUST_PROXY_HEADERS="1", TRUSTED_PROXY_IPS="127.0.0.1,::1",
               ALLOWED_ORIGINS=main, SPEEDTEST_ALLOW_CROSS_SITE="1",
               SPEEDTEST_RUST_RTC_EXPERIMENTAL="1")
    if ip:
        env["SPEEDTEST_WEBRTC_UDP4"] = ip
    if role == "probe":
        env["SPEEDTEST_PROBE_ONLY"] = "1"
    if role == "main":
        config_nodes = [dict(id="local", label=title, base="")]
        config_nodes += [dict(id=n["id"], label=n["label"], base=n["base"]) for n in nodes]
        env["SPEEDTEST_NODES"] = json.dumps(config_nodes, ensure_ascii=False, separators=(",", ":"))
        probe = next((n for n in nodes if n["role"] == "probe"), None)
        if probe:
            env["SPEEDTEST_PROBE_ONLY_NODE_ID"] = probe["id"]
    output_node = None if role == "main" else descriptor(dict(schema=1, role=role, id=name, label=title, base=public))
    return dict(role=role, public=public, main=main, port=port, udp4=ip,
                prefix=prefix_value, service_user=service_user, env=env, node=output_node, nodes=nodes)


def generate(spec, imported, output):
    setup = prepare(spec, imported)
    out = Path(output).expanduser().absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {out}")
    if any(c in str(out) for c in "\n\r\x00"):
        raise ValueError("output path contains control characters")
    parent = out.parent
    if not parent.is_dir():
        raise ValueError("output parent directory must already exist")
    q = shlex.quote
    env = env_text(setup["env"])
    web = "ON" if setup["role"] == "main" else "OFF"
    build = f'''#!/usr/bin/env bash
set -euo pipefail
ROOT={q(str(ROOT))}
OUT={q(str(out))}
PREFIX={q(setup["prefix"])}
export CC="${{CC:-clang-18}}" CXX="${{CXX:-clang++-18}}"
command -v "$CC" >/dev/null && command -v "$CXX" >/dev/null || {{ echo "Install Clang 18 or set CC and CXX." >&2; exit 1; }}
if [[ ${{1:-}} == --bootstrap ]]; then
  bash "$ROOT/cpp/cmake/bootstrap-datachannel.sh" "$PREFIX"
elif [[ ${{1:-}} != "" ]]; then
  echo "Usage: ./build.sh [--bootstrap]" >&2; exit 2
fi
if [[ ! -f "$PREFIX/install/lib/cmake/LibDataChannel/LibDataChannelConfig.cmake" ]]; then
  echo "Native dependency not found in $PREFIX/install. Run ./build.sh --bootstrap." >&2; exit 1
fi
cmake -S "$ROOT" -B "$OUT/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \\
  -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_PREFIX_PATH="$PREFIX/install" -DSPEEDTEST_WEB_UI={web}
cmake --build "$OUT/build" --parallel "${{JITTERCREST_BUILD_JOBS:-2}}"
ctest --test-dir "$OUT/build" --output-on-failure
echo "Build and tests complete. Run ./run.sh; configure HTTPS using Caddyfile.example."
'''
    runner = f'''#!/usr/bin/env bash
set -euo pipefail
cd {q(str(out))}
set -a
source ./runtime.env
set +a
exec ./build/cpp/speedtest-cpp
'''
    def unit_quote(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    unit_name = "jittercrest-" + setup["env"]["SPEEDTEST_NODE_NAME"] + ".service"
    service = f'''[Unit]
Description=JitterCrest {setup["role"]} backend
After=network.target

[Service]
Type=simple
User={setup["service_user"]}
WorkingDirectory={unit_quote(str(out))}
ExecStart=/bin/bash {unit_quote(str(out / "run.sh"))}
Restart=on-failure
RestartSec=3
TimeoutStopSec=10
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
RestrictSUIDSGID=true
CapabilityBoundingSet=
AmbientCapabilities=
LockPersonality=true
UMask=027
MemoryHigh=192M
MemoryMax=256M
TasksMax=128

[Install]
WantedBy=multi-user.target
'''
    installer = f'''#!/usr/bin/env bash
set -euo pipefail
OUT={q(str(out))}
USER_NAME={q(setup["service_user"])}
UNIT={q(unit_name)}
TARGET="/etc/systemd/system/$UNIT"
[[ "$EUID" -eq 0 ]] || {{ echo "Run with sudo after checking service.example." >&2; exit 1; }}
id "$USER_NAME" >/dev/null || {{ echo "Create the non-root service user $USER_NAME first." >&2; exit 1; }}
runuser -u "$USER_NAME" -- test -r "$OUT/runtime.env" || {{ echo "Service user cannot read runtime.env or traverse its parents." >&2; exit 1; }}
runuser -u "$USER_NAME" -- test -x "$OUT/build/cpp/speedtest-cpp" || {{ echo "Build the binary and grant the service user access first." >&2; exit 1; }}
[[ ! -e "$TARGET" && ! -L "$TARGET" ]] || {{ echo "Unit already exists; refusing to overwrite $TARGET." >&2; exit 1; }}
[[ "$(systemctl show "$UNIT" -p LoadState --value)" == not-found ]] || {{ echo "Unit already loaded; refusing to replace it." >&2; exit 1; }}
(umask 022; set -o noclobber; cat "$OUT/service.example" > "$TARGET")
rollback() {{
  systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
  if cmp -s "$OUT/service.example" "$TARGET"; then rm -f -- "$TARGET"; fi
  systemctl daemon-reload || true
}}
trap rollback ERR
chmod 0644 "$TARGET"
systemctl daemon-reload
systemctl enable --now "$UNIT"
systemctl is-active --quiet "$UNIT"
trap - ERR
echo "Started $UNIT. Review Caddyfile.example separately; this script never changes the proxy."
'''
    caddy = (f"# Example site only: validate certificates and merge safely with any existing Caddy config.\n"
             f"{setup['public']} {{\n"
             f"    reverse_proxy 127.0.0.1:{setup['port']} {{\n"
             "        header_up X-Real-IP {http.request.remote.host}\n"
             "        header_up X-Forwarded-For {http.request.remote.host}\n"
             "        flush_interval -1\n"
             "    }\n}\n")
    out.mkdir(mode=0o700)
    (out / "runtime.env").write_text(env, encoding="utf-8")
    (out / "runtime.env").chmod(0o600)
    (out / "build.sh").write_text(build, encoding="utf-8")
    (out / "run.sh").write_text(runner, encoding="utf-8")
    (out / "service.example").write_text(service, encoding="utf-8")
    (out / "install-service.sh").write_text(installer, encoding="utf-8")
    for f in ["build.sh", "run.sh", "install-service.sh"]:
        (out / f).chmod(0o700)
    (out / "Caddyfile.example").write_text(caddy, encoding="utf-8")
    if setup["node"]:
        (out / "node.json").write_text(json.dumps(setup["node"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return setup


def ask(text, default=None):
    prompt = text + (f" [{default}]" if default is not None else "") + ": "
    value = input(prompt).strip()
    return value if value else (default if default is not None else "")


def wizard():
    choice = ask("角色 1=主节点 2=数据节点 3=仅探针节点", "1")
    role = {"1": "main", "2": "data", "3": "probe"}.get(choice)
    if not role:
        raise ValueError("invalid role selection")
    name = ask("节点 ID（小写字母、数字、连字符）", "main" if role == "main" else None)
    title = ask("显示名称", name)
    public = ask("本节点的 HTTPS 地址，例如 https://node.example.com")
    main = public if role == "main" else ask("主网页 HTTPS 地址，例如 https://main.example.com")
    ip = ask("本节点公网 UDP IPv4（留空自动选择；仅探针节点必须填写）", "")
    port = int(ask("本机后端监听端口", "32132"))
    prefix = ask("原生依赖目录（绝对路径）", "/opt/jittercrest-deps")
    default_user = getpass.getuser()
    user = ask("运行 systemd 服务的现有非 root 用户", "jittercrest" if default_user == "root" else default_user)
    paths = []
    if role == "main":
        print("可逐个输入后端生成的 node.json 路径；输入空行结束。")
        while True:
            path = ask("node.json 路径（回车结束）", "")
            if not path:
                break
            paths.append(path)
    output = ask("新建输出目录（必须不存在）")
    return dict(role=role, id=name, label=title, public_origin=public,
                main_origin=main, udp4=ip, port=port, native_prefix=prefix, service_user=user), paths, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON configuration; omit for interactive mode")
    parser.add_argument("--output", type=Path, help="new output directory")
    parser.add_argument("--node", type=Path, action="append", default=[], help="agent node.json to import on main; repeatable")
    parser.add_argument("--add-node", type=Path, help="add an agent descriptor to an existing generated main configuration")
    parser.add_argument("--existing", type=Path, help="existing generated main output directory")
    args = parser.parse_args()
    try:
        if args.add_node or args.existing:
            if not args.add_node or not args.existing or args.config or args.output or args.node:
                parser.error("--add-node and --existing must be provided together without other options")
            backup, node = add_node(args.existing, json.loads(args.add_node.read_text(encoding="utf-8")))
            print(f"已添加节点 {node['id']}，备份：{backup}")
            print("主端无需重新编译；重启主端进程／服务后新列表才生效。未自动重启服务或修改反向代理。")
            return 0
        if args.config:
            if not args.output:
                parser.error("--config requires --output")
            spec, paths, output = json.loads(args.config.read_text(encoding="utf-8")), args.node, args.output
        else:
            if args.output or args.node:
                parser.error("--output and --node require --config")
            spec, paths, output = wizard()
        imported = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
        result = generate(spec, imported, output)
        print(f"已生成 {result['role']} 配置：{Path(output).absolute()}")
        print("执行 ./build.sh --bootstrap 构建依赖及服务；已有依赖时执行 ./build.sh。")
        print("执行 ./run.sh 启动前台服务；Caddyfile.example 仅为示例，不会自动安装或修改配置。")
        print(f"可选 systemd：确保 {result['service_user']} 已存在且可读取输出目录，再检查并运行 sudo ./install-service.sh。")
        if result["node"]:
            print("将 node.json 安全复制到主节点，在主节点向导中导入；无需主端主动连接后端。")
        if not result["udp4"]:
            print("注意：未指定 UDP 公网映射；NAT 环境必须检查 ICE 候选地址与 UDP 连通性。")
        print("请自行配置有效 HTTPS 证书；IP 证书的签发和浏览器信任因提供商而异。")
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"设置失败：{error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
