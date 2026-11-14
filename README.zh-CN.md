# JitterCrest

[English](README.md) | 简体中文

JitterCrest 是一个自托管的网络质量测试工具。主站负责提供网页，测速节点与浏览器直接交换数据，测量速度、RTT、抖动、丢包和稳定性。

## 技术栈

- **后端**：C++20、Boost.Asio 协程与 Boost.Beast HTTP、OpenSSL、内置 TLS + HTTP/2 入口（nghttp2）、WebRTC DataChannel（libdatachannel）、libcurl 和 nlohmann-json。
- **前端**：单个 HTML 文件，使用 Web Worker、流式 Fetch、WebRTC 和 Canvas/SVG 图表，嵌入服务端可执行文件，无需 JavaScript 构建。
- **部署**：CMake + Ninja、Docker、Caddy 和 systemd。节点通过主站 `/admin` 页面生成的命令接入。

## 测试方法

所有测试都在浏览器与所选节点之间进行。以下公式与 `public/index.html` 及节点的接收计数一致。

### 上下行速度

上下行使用同一套接收端窗口。接收端（下载为浏览器，上传为节点）以收到第一个有效字节的时刻为 $t_0$，先预热 $W = 1\ \mathrm{s}$，再统计恰好 $T = 10\ \mathrm{s}$ 的字节数。窗口外到达的字节不计入，窗口结束后发送端停止发送。下载读取 HTTP 响应流；上传发送 HTTP/2 流式请求体（不支持请求流的浏览器改为重复发送 4 MiB 请求），节点每 100 ms 回报一次计数。

```math
v = \frac{8\,B\big([t_0+W,\ t_0+W+T)\big)}{10^{6}\,T}\ \ \mathrm{Mbit/s}
```

$B(\cdot)$ 为该区间内收到的有效字节数，时间单位为秒。图表每 $\Delta = 100\ \mathrm{ms}$ 读取一次同一计数，并以各采样与最近 0.5 s 的重叠时长 $o_i$ 加权，绘制 0.5 s 滚动均值：

```math
v_k = \frac{8\,\Delta B_k}{10^{6}\,\Delta t_k},\qquad
\bar v = \frac{8\sum_i B_i\,o_i/\Delta t_i}{10^{6}\sum_i o_i}
```

端到端速率包含预热阶段：$v_{\mathrm{e2e}} = 8 B_{\mathrm{total}} / \big(10^{6} (t_{\mathrm{end}} - t_0)\big)$。多连接测试把所有连接的字节累加到同一个计数。

### RTT 与抖动

探针使用无序、不重传的 DataChannel（行为接近 UDP），上下行同时以每秒 20 个包发送 2 s。每个 RTT 都在同一个时钟上测量，无需两端对时：

- **上行 RTT**：浏览器发送序号和时间戳，节点回显，浏览器按自身时钟计算 $r = t_{\mathrm{echo}} - t_{\mathrm{send}}$。
- **下行 RTT**：节点发送，浏览器回显，节点按自身时钟计算 $r$ 并回传样本。

单方向共 $n$ 个样本，排序后为 $r_{(0)} \le \dots \le r_{(n-1)}$：

```math
P_p = r_{(\lfloor (n-1)p \rfloor)},\qquad
J_{\mathrm{MAD}} = 1.4826\cdot P_{50}\big(\left| r_i - P_{50} \right|\big),\qquad
J_{\Delta} = \frac{1}{n-1}\sum_{i=2}^{n} \left| r_i - r_{i-1} \right|
```

$J_{\mathrm{MAD}}$ 是稳健抖动，在正态噪声下与标准差同尺度；$J_{\Delta}$ 是相邻样本的平均变化量。页面同时给出最小值、平均值、最大值、P95 和 P99。

### 稳定性测试

稳定性测试使用同样的双向探针，发包速率 $R \in \lbrace 20, 60, 100, 200 \rbrace$ 包/秒，时长 10–60 s（默认每秒 100 包、10 s）。包间隔 $\tau = 1000/R\ \mathrm{ms}$，单方向共发送 $N$ 个包：

```math
L = \frac{N_{\mathrm{missing}}}{N},\qquad
L_{\mathrm{eq}} = \frac{N_{\mathrm{missing}} + N_{r>200\,\mathrm{ms}}}{N},\qquad
O = \frac{N_{\mathrm{reordered}}}{N}
```

- **迟到**：$r > 100\ \mathrm{ms}$ 的包。缺失的包和 $r > 200\ \mathrm{ms}$ 的包视为等效丢包；连续 $k$ 个等效丢包序号构成一段突发，持续 $k\tau$。
- **乱序**：在更大序号之后才到达的包。
- **本机延迟**：浏览器处理函数距上次执行的间隔超过 $\max(50\ \mathrm{ms},\ 8\tau)$，且 8 ms 内集中处理了至少 3 个包，则标记为本机延迟，单独统计并从网络等效丢包中扣除。

延迟突刺阈值随基线 $B = P_{50}$、稳健抖动 $J = J_{\mathrm{MAD}}$ 和尾部 $Q = P_{95} - P_{50}$ 自适应，其中 $\mathrm{clamp}(x, a, b) = \min(\max(x, a), b)$：

```math
\begin{aligned}
s &= \sqrt{(J/10)^2 + (Q/45)^2} \\
\theta &= B + m + (M - m)\left(1 - e^{-0.45\,s^{1.3}}\right),
\quad m = \mathrm{clamp}(0.35B, 15, 30),\ M = \mathrm{clamp}(0.65B, 28, 60) \\
\theta_s &= \max\!\left(\theta + 25,\ B + m_s + (M_s - m_s)\left(1 - e^{-0.35\,s^{1.25}}\right),\ 110\right),
\quad m_s = \mathrm{clamp}(0.9B, 50, 90),\ M_s = \mathrm{clamp}(1.25B, 80, 130)
\end{aligned}
```

以上数值单位均为毫秒。$r \ge \theta$ 的样本计为影响样本，$r \ge \theta_s$ 的样本计为严重突刺。页面给出两者比例 $N_{r\ge\theta}/n$ 和 $N_{r\ge\theta_s}/n$、影响事件，以及最长连续影响时长 $k_{\max}\tau$。

## 快速开始

```sh
CC=clang-18 CXX=clang++-18 bash cpp/cmake/bootstrap-datachannel.sh /absolute/local/deps
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=clang++-18 -DCMAKE_PREFIX_PATH=/absolute/local/deps/install
cmake --build build --parallel 2 && ctest --test-dir build --output-on-failure
SPEEDTEST_HOST=127.0.0.1 PORT=32132 build/cpp/speedtest-cpp
```

搭建主站和多个节点时运行 `python3 cpp/deploy/quickstart.py`，再在 `/admin` 中接入节点。

## 文档

- [文档索引](cpp/docs/README.md)
- [构建、部署与节点管理](cpp/docs/deployment.zh-CN.md)
- [C++ 架构与配置](cpp/README.md)
- [TLS HTTP/2 入口](cpp/docs/tls-edge.md)
- [验收报告](cpp/docs/acceptance-audit.md) · [性能对比](cpp/docs/performance-findings-recovery.md) · [Rust 归档](cpp/docs/rust-archive.md)
- [安全说明](SECURITY.md) · [第三方许可](THIRD_PARTY.md)

## 许可证

项目代码使用 [MIT 许可证](LICENSE)。第三方依赖保留各自的许可证，见 [THIRD_PARTY.md](THIRD_PARTY.md)。
