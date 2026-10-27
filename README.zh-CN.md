# JitterCrest

[English](README.md) | 简体中文

JitterCrest 是一个自托管的浏览器测速与网络质量分析项目。C++20 后端提供 HTTP 流式测速和 WebRTC 探针；浏览器展示上下行速度、延迟、抖动、丢包与游戏稳定性等结果。

> 当前维护版本为 **C++20**。Rust 和 Node.js 版本仅作为历史源码保存在 [archive/](archive/) 中，不参与默认构建和 CI。

## 项目特色

- **上下行测速**：下载、上传均展示实时曲线和结果；另有 10 KiB、100 KiB、1 MiB 小文件下载测试。
- **实时网络质量**：通过 WebRTC DataChannel 探针观察上下行 RTT、Jitter 和百分位数据，不只展示一个 Ping 值。
- **游戏稳定性分析**：分别观察客户端到服务端、服务端到客户端的延迟、丢包和时间线；包含结果完整性判断。
- **多节点与轻量后端**：主节点负责网页；可配置仅测速后端或仅探针节点，后端无需重复提供网页。
- **浏览器直连节点**：测速数据和探针由浏览器与所选节点直接交换；可使用域名 HTTPS，也可在证书和浏览器信任条件满足时使用 IP HTTPS。
- **自托管与资源边界**：支持受信任代理、来源限制、请求速率、连接数、超时和缓冲等控制，适合放在反向代理之后部署。
- **无需 Node.js 服务端**：后端运行不依赖 Node.js、Rust 或 Python。浏览器中的 JavaScript 仍负责交互、图表和 WebRTC；Python 主要用于构建辅助、测试和打包。

## 快速搭建（Linux）

向导可生成主端、数据节点或仅探针节点配置。准备下方构建章节所需工具与依赖，
并使用 Python 3 运行向导和辅助脚本；C++ 服务运行时不依赖 Python。Windows
请使用 Linux/WSL。输出目录必须是尚不存在的新目录。

1. 在每台后端服务器取得本仓库后运行 `python3 cpp/deploy/quickstart.py`，
   选择 **2 数据节点**或 **3 仅探针节点**，输入本节点 HTTPS 地址、主网页
   HTTPS 地址、节点 ID 等信息。仅探针节点必须填写公网 UDP IPv4。原生依赖目录须对构建用户可写；默认
   `/opt` 路径可能需要管理员预先设置。
2. 进入生成目录，运行 `./build.sh --bootstrap` 构建固定版本的原生依赖和
   服务；已有对应依赖时运行 `./build.sh`。运行 `./run.sh` 前台启动。
   将生成的 `node.json` 通过安全渠道复制到主端；文件包含节点地址但不含密码。
3. 在主端运行同一向导，选择 **1 主节点**，按提示导入各后端 `node.json`，
   然后同样构建和启动。如果主端已由向导生成，可追加节点：

   ```sh
   python3 cpp/deploy/quickstart.py --existing /path/to/main --add-node /path/to/node.json
   ```

   修改前会备份 `runtime.env` 并原子替换；手动更改过的非标准配置会被拒绝。
   添加后须手动重启主端进程或服务，无需重新编译。

请检查 `Caddyfile.example`，自行配置可信 HTTPS 反向代理及防火墙；向导不会
安装或修改 Caddy、申请证书、开放端口或远程部署。IP HTTPS 需要浏览器信任的
IP 证书；NAT 环境需验证 UDP 及 ICE 连通性。浏览器直接连接各后端，主端和
后端之间不建立常驻控制连接。

向导还生成可选的 `service.example` 和 `install-service.sh`。运行
`sudo ./install-service.sh` 前应先检查内容，创建所选非 root 用户，并保证该用户
可穿越、读取输出目录且可执行脚本和二进制文件。安装器不会覆盖已有同名服务。
非交互 JSON 参数可查阅 `python3 cpp/deploy/quickstart.py --help`。

## 工作方式

    主节点：网页 + C++ 服务
              │ 提供网页
              ▼
           用户浏览器
         ┌────┴───────────────┐
         ▼                    ▼
    HTTP(S) 测速         WebRTC 信令 + UDP 探针
         │                    │
         ▼                    ▼
    主节点或数据节点       主节点或探针节点

网页由主节点提供，浏览器与选定的后端节点通信并在本地渲染结果；测速流量不必先经过主节点再转发。浏览器仍需访问节点的 HTTP(S) 接口进行测速和 WebRTC 信令，探针走 WebRTC 数据通道。**直接使用 IP 不等于自动获得可信 HTTPS**。

| 角色 | 构建或配置 | 用途 |
|---|---|---|
| 主节点 | 默认 UI 构建 | 提供网页，也可提供测速和探针 |
| 数据节点 | 构建时设置 SPEEDTEST_WEB_UI=OFF | 测速与探针，不内嵌网页 |
| 仅探针节点 | 无 UI 构建，并设置 SPEEDTEST_PROBE_ONLY=1 | 提供探针，拒绝测速数据接口 |

## 功能说明

### 速度测试

- 展示下载和上传过程的速度曲线及当前／最终结果；横轴为时间，纵轴为速度。
- 测试固定大小的小文件下载，以及流式下载、上传和上传进度。
- 上传以服务端接收统计作为重要依据；实际结果受浏览器、设备性能、线路、代理和服务器负载影响。

### WebRTC 与稳定性

- 使用 WebRTC DataChannel 二进制探针，展示上下两个方向的 RTT、Jitter、P95、P99 等统计。
- 稳定性测试提供延迟与丢包时间线、上下行统计及 JSON 导出。
- 当采样密度或调度等条件不足时，结果可标为不完整；当前完整性门槛和统计定义见[验收说明](cpp/docs/acceptance-audit.md)，不应把“不完整”直接等同于真实网络丢包。

## 环境要求

已验证平台为 **Linux x86_64**；不声称其他架构已验证。需要：

- CMake ≥ 3.22、Ninja 和支持 C++20 的编译器；已验证 Clang 18。
- Boost.System 1.83、OpenSSL 3、libcurl ≥ 7.85（需要异步 DNS）、nlohmann-json ≥ 3.11。
- Git、Python 3；Python 用于构建／测试工具，而非服务端运行。
- libdatachannel 0.24.6：下方脚本固定源码版本并应用仓库中的补丁。

在 Ubuntu 24.04 上，可参考项目 CI 安装依赖：

    sudo apt-get update
    sudo apt-get install -y clang-18 lld-18 cmake ninja-build \
      libboost-system1.83-dev libssl-dev libcurl4-openssl-dev \
      nlohmann-json3-dev python3 git

其他发行版的包名和版本可能不同；构建与发布包仍受目标系统架构、glibc、CA 证书和动态库环境约束。不要将未在目标系统验证的二进制视为通用包。

## 构建与本机验证

在仓库根目录执行：

    # 指定一个本机绝对路径存放固定版本的原生依赖。
    CC=clang-18 CXX=clang++-18 \
      bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps

    # 默认构建带网页的主节点。
    cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_COMPILER=clang++-18 \
      -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
    cmake --build build --parallel 2
    ctest --test-dir build --output-on-failure

将 /absolute/local/deps 替换为实际路径。脚本在所给目录中获取并构建固定版本的原生依赖，不会自动安装生产服务。

启动本机 HTTP 服务：

    SPEEDTEST_HOST=127.0.0.1 PORT=32132 build/cpp/speedtest-cpp

在另一个终端验证：

    curl -fsS http://127.0.0.1:32132/health
    curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:32132/latency

浏览器可打开 http://127.0.0.1:32132/ 查看网页。此步骤主要验证本机 HTTP 和网页；**默认未启用 WebRTC**，不能据此认为公网 UDP、证书或多节点探针已经通过。

### 构建无网页的后端节点

    cmake -S . -B build-agent -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_COMPILER=clang++-18 \
      -DCMAKE_PREFIX_PATH=/absolute/local/deps/install \
      -DSPEEDTEST_WEB_UI=OFF
    cmake --build build-agent --parallel 2
    ctest --test-dir build-agent --output-on-failure

生成的 build-agent/cpp/speedtest-cpp 不内嵌网页。建议分别保留 build 和 build-agent，避免混淆主节点和后端节点产物。

## 配置

程序读取**进程环境变量**，不会自动加载 .env 文件。[.env.example](.env.example) 是通用配置参考，不包含可直接用于你服务器的真实域名、节点地址或凭据。

| 环境变量 | 作用与注意事项 |
|---|---|
| SPEEDTEST_HOST | 监听 IP，默认 127.0.0.1；要求 IP 字面值。 |
| PORT | HTTP 端口，代码默认 3010；上面的本机示例显式使用 32132。 |
| SPEEDTEST_NODE_NAME | 当前节点名称。 |
| SPEEDTEST_NODES | 主网页节点列表的 JSON 数组；默认只有同源本地节点。 |
| SPEEDTEST_RUST_RTC_EXPERIMENTAL | 设为 1 才启用 WebRTC；这是历史配置键名，**不代表依赖 Rust**。默认关闭，相关请求返回 503。 |
| SPEEDTEST_WEBRTC_UDP4 | 按实际公网 IPv4／NAT 拓扑配置，并验证 UDP 连通性；不接受私有地址作为该公网映射。 |
| ALLOWED_ORIGINS | 允许的跨源网页来源，按实际部署填写。 |
| SPEEDTEST_ALLOW_CROSS_SITE | 跨站节点场景按需要设为 1，同时配置正确的来源和 CSP。 |
| TRUST_PROXY_HEADERS | 设为 1 时仅应在受控代理后使用；不能在公网监听并信任任意转发头。 |
| TRUSTED_PROXY_IPS | 明确列出可信反向代理 IP。 |
| SPEEDTEST_PROBE_ONLY | 设为 1 时启用仅探针角色，拒绝测速数据接口。 |
| SPEEDTEST_PROBE_ONLY_NODE_ID | 配置仅探针节点标识；默认 probe-only。 |
| SPEEDTEST_IO_THREADS / MAX_HTTP_CONNECTIONS | I/O 线程数和 HTTP 连接上限；需按实际机器和负载设置。 |

更多限制、超时和配置范围以 [config.cpp](cpp/src/config.cpp) 和 [C++ 文档](cpp/README.md) 为准。示例值不是容量保证，示例域名也不是可用的演示服务。

### 多节点示例

在**主节点**的环境配置中定义节点列表；以下域名是示意，必须替换为自己控制且证书有效的地址：

    export SPEEDTEST_NODES='[{"id":"local","label":"主节点","base":""},{"id":"data-a","label":"数据节点 A","base":"https://data.example.com"},{"id":"probe-only","label":"探针节点","base":"https://probe.example.com"}]'

- 在各后端分别设置正确的 ALLOWED_ORIGINS、可信代理及跨站配置；主网页的内容安全策略也必须允许实际使用的节点来源。
- 浏览器需要同时能访问 HTTP(S) 信令／测速接口和 WebRTC 的 UDP 路径；节点可能位于 NAT 后，需要按真实拓扑设置公网映射与防火墙。
- HTTPS 域名需要有效证书；IP HTTPS 需要浏览器信任的证书、包含对应 IP 的证书标识以及可用的验证／签发方式。服务商是否支持应独立确认。不要全局忽略证书错误。
- 仅探针节点需在服务端显式设置 SPEEDTEST_PROBE_ONLY=1；仅仅在网页里给节点起名不能完成角色隔离。

## 部署与安全

仓库中的 [cpp/deploy/](cpp/deploy/) 包含独立 systemd、Caddy、环境变量和打包示例。它们是**通用测试模板**，不会自动安装，也不是针对某台服务器的生产配置。历史记录中的状态有时间范围，请以当前代码、配置和[归档说明](cpp/docs/rust-archive.md)为准。

部署前建议：

1. 本机完成构建和测试；确认目标机器的系统、架构、glibc 和动态库兼容。
2. 使用独立低权限账户、受控监听地址、HTTPS 反向代理以及合理的连接／流量上限。
3. 校验真实域名或 IP 的 TLS、CORS、NAT、UDP 和各节点角色；不要直接套用示例域名或私有生产配置。
4. 准备并验证**当前 C++ 版本**的发布和回退方案，避免覆盖其他服务或现有数据。
5. 不要提交 SSH 凭据、真实部署配置、令牌、生产日志或备份；公开版本使用通用占位配置。

Rust 源码可供历史研究，但服务器端原 Rust 回滚服务和备份已按此前要求移除；不能把历史回滚命令当作当前可用方案。详见[安全说明](SECURITY.md)和 [Rust 归档说明](cpp/docs/rust-archive.md)。

## API 概览

| 接口 | 用途 |
|---|---|
| GET /health | 健康检查 |
| GET /latency | 轻量延迟请求 |
| GET /download、GET /download-100k、GET /download-stream | 固定大小或流式下载 |
| POST /upload、POST /upload-stream | 上传与服务端接收统计 |
| /upload-progress | 上传进度创建与读取；具体方法／参数见实现及测试 |
| POST /webrtc/offer | WebRTC 信令；需显式启用 WebRTC |
| GET /client-info | 客户端网络信息 |

实际方法、参数、限流、状态码和跨源行为以 [http.cpp](cpp/src/http.cpp) 及 [cpp/tests/](cpp/tests/) 为准。仅探针节点会拒绝测速数据接口。

## 测试、性能与限制

- 根目录 CMake 构建支持 UI 和无 UI 两种角色；cpp/ 下另有 Debug、Release、ASan+UBSan 和 TSan 预设。
- 测试客户端覆盖协议、HTTP、资源边界、连接生命周期和 WebRTC 等场景；具体范围见[验收报告](cpp/docs/acceptance-audit.md)。
- 报告中有两项经分析的 Clang 18／Boost 协程模型静态分析告警；完整 HTTP 静态分析**不应称为零告警**。有限测试也不能证明不存在所有竞态或未定义行为。
- 历史同环境基准中，Rust 在多项本地大流量吞吐／CPU 效率测试中领先；C++ 在部分轻量请求和高负载内存占用中有优势。不能由此推断任一语言在所有网络和负载下更快，详见[性能报告](cpp/docs/performance-findings-recovery.md)。
- 长时间稳定性、真实网络路径、客户端能力、证书签发和外部服务兼容性仍需针对自己的环境验证；Linux aarch64 不在已验证平台声明内。

## 目录结构

    .
    ├── CMakeLists.txt          # 当前 C++ 主构建入口
    ├── cpp/                    # C++ 后端、测试、基准、部署模板与文档
    ├── public/                 # 当前网页，由主节点嵌入并提供
    ├── archive/rust/           # 冻结的 Rust 历史实现与参考工具
    ├── archive/node/           # 冻结的 Node.js 历史实现
    ├── .env.example            # 通用环境变量示例
    ├── SECURITY.md             # 安全说明
    └── THIRD_PARTY.md          # 第三方依赖及许可说明

历史实现仅供参考，不属于当前默认构建、CI 或生产服务。仓库不会公开历史生产配置、运维备份和二进制产物。

## 文档索引

- [C++ 架构、依赖和详细构建说明](cpp/README.md)
- [功能、安全与兼容性验收](cpp/docs/acceptance-audit.md)
- [性能对比与解释](cpp/docs/performance-findings-recovery.md)
- [Rust 归档与服务器端清理](cpp/docs/rust-archive.md)
- [发布打包说明](cpp/docs/release-bundle.md)
- [安全说明](SECURITY.md) · [第三方许可](THIRD_PARTY.md)

## 许可证

项目代码使用 [MIT 许可证](LICENSE)。第三方依赖保留各自的许可证和版权声明，分发时请一并遵守[第三方许可说明](THIRD_PARTY.md)。
