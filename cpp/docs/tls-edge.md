# Built-in TLS HTTP/2 listener

## Why it exists

Browsers send streaming request bodies (`fetch` with a `ReadableStream`) only over
HTTP/2 or HTTP/3. On HTTP/2 the receiver's flow-control window bounds one stream
to roughly `window / RTT`, and all streams of a connection share the connection
window. Caddy 2.11 configures `golang.org/x/net/http2` with its defaults: 1 MiB per
stream and per connection, with no configuration option. A single-stream browser
upload through such a proxy cannot exceed about 155 Mbit/s at 54 ms RTT, and
adding streams on the same connection does not help.

Measured between two production nodes (54 ms RTT, BBR, same Caddy and C++
service for every HTTP row):

| Path | Upload |
|---|---|
| iperf3, one TCP stream | 2191-2369 Mbit/s |
| HTTP/1.1 through Caddy, one connection | 2459-2475 Mbit/s |
| HTTP/2 through Caddy, one stream | 154 Mbit/s |
| HTTP/2 through Caddy, four streams on one connection | 147 Mbit/s total |
| HTTP/2 through the built-in listener, one stream | 1393-1551 Mbit/s |
| HTTP/2 through the built-in listener, four streams on one connection | 1943 Mbit/s total |

Downloads were already unaffected (about 1.18 Gbit/s on both HTTP/2 paths). From a
browser on a client limited to about 150 Mbit/s of uplink over a ~120 ms path, the
single-stream upload rose from 66-69 Mbit/s to 141-154 Mbit/s.

## Behaviour

- TLS 1.2+ with ALPN `h2` only; clients that do not offer HTTP/2 are refused
  during the handshake. Keep a reverse-proxy origin for HTTP/1.1 tooling.
- OpenSSL runs over memory BIOs and the socket is read in 256 KiB batches, so one
  relay and flow-control round covers many TLS records.
- nghttp2 advertises 32 MiB per stream and 64 MiB per connection. Request bytes
  are acknowledged (WINDOW_UPDATE) only after the local HTTP/1.1 listener has
  accepted them, so buffered request data per connection never exceeds the
  connection window.
- Every stream is relayed to `SPEEDTEST_HOST:PORT` over HTTP/1.1. Bodies without
  `content-length` use chunked transfer; HEAD and status codes are preserved;
  responses are streamed.
- Response bytes wait for the client's flow-control window, so a client that
  stops reading cannot grow them without bound: at most 2 MiB per stream, 4 MiB
  per connection (a stream with nothing buffered may still fetch one 256 KiB
  read) and 96 MiB per process. A connection whose pending response data makes
  no progress for 20 seconds is closed.
- Per client address (IPv6 grouped by /64): at most 16 connections and 64
  concurrent streams, each of which holds one local listener connection; a
  connection carries at most 32 concurrent streams. Excess streams are refused
  with `REFUSED_STREAM`.
- Client-supplied `x-real-ip`, `x-forwarded-*`, `forwarded` and
  `x-speedtest-node` headers are dropped and replaced with the TLS peer address.
- A reply that ends before its request body resets the remainder with
  `NO_ERROR`. Idle connections receive GOAWAY after `HTTP_KEEP_ALIVE_MS`.
- Certificate and key files are checked every 30 seconds. A changed pair is
  loaded for new connections; a failed load keeps the previous certificate and is
  retried.

## Configuration

```sh
SPEEDTEST_TLS_PORT=8444
SPEEDTEST_TLS_HOST=0.0.0.0
SPEEDTEST_TLS_CERT=/etc/jittercrest/tls/node.crt
SPEEDTEST_TLS_KEY=/etc/jittercrest/tls/node.key
TRUST_PROXY_HEADERS=1
TRUSTED_PROXY_IPS=127.0.0.1,::1
SPEEDTEST_HOST=127.0.0.1
```

Startup fails when the port equals `PORT`, the files are missing or mismatched,
or `SPEEDTEST_HOST` is absent from `TRUSTED_PROXY_IPS`. Point the node's `base`
at the listener origin, for example `https://203.0.113.10:8444`, so the page's
content security policy and the node list use it.

Any ACME client can keep issuing the certificate. With Caddy, a root timer can
copy the managed files into a directory readable by the service group, for
example:

```sh
src=/var/lib/caddy/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/203.0.113.10
install -d -m 0750 -o root -g speedtest /etc/jittercrest/tls
install -m 0640 -o root -g speedtest "$src/203.0.113.10.key" /etc/jittercrest/tls/node.key
install -m 0640 -o root -g speedtest "$src/203.0.113.10.crt" /etc/jittercrest/tls/node.crt
```

At multi-Gbit/s a 12-second single-stream upload exceeds the default 2 GiB
`MAX_STREAM_UPLOAD` and quickly exhausts the 4 GiB per-IP byte budgets; raise
`MAX_STREAM_UPLOAD`, `UPLOAD_BYTE_BUDGET_PER_IP` and
`DOWNLOAD_BYTE_BUDGET_PER_IP` to match the node's capacity.

## Generated setups

`cpp/deploy/quickstart.py` and the command-based enrollment installer configure
the listener by default (port 8444, `tls_port` in the wizard). Caddy keeps
obtaining the certificate on 80/443 and serves the page, `/admin` and HTTP/1.1
endpoints; every node address in the picker, including the main server's own
entry, is the listener origin. A root systemd timer
(`jittercrest-<id>-tls-sync.timer`, every 10 minutes) copies the certificate from
Caddy's storage into a directory readable only by the service user: `tls/` in
the generated directory, or `/opt/jittercrest-agents/<id>/tls`, mounted read-only
into the agent container. The installer waits for the first certificate, starts
the agent, and activates the node only after HTTPS and HTTP/2 answer.

## Validation

CTest `tls_http2_edge_relay` starts the HTTP server and the listener with a
generated certificate and checks the advertised windows from raw SETTINGS and
WINDOW_UPDATE frames, HTTP/2 negotiation, HEAD, status relay, replacement of
spoofed client addresses, a 64 MiB streamed upload, a sized upload and a 32 MiB
download, and refusal of a 17th concurrent connection from one address.
