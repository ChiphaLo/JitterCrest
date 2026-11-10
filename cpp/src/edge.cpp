#include "speedtest/edge.hpp"
#include <algorithm>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/dispatch.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/ssl.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/strand.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>
#include <boost/beast/core/flat_buffer.hpp>
#include <boost/beast/http.hpp>
#include <charconv>
#include <cstring>
#include <deque>
#include <filesystem>
#include <iostream>
#include <map>
#include <nghttp2/nghttp2.h>
#include <syncstream>

namespace speedtest {
namespace {
namespace asio = boost::asio;
namespace ssl = asio::ssl;
namespace beast = boost::beast;
namespace http = beast::http;
using Tcp = asio::ip::tcp;
using Error = boost::system::error_code;
using asio::awaitable;
using asio::use_awaitable;
// WINDOW_UPDATE is sent after half of a window is consumed, so at least half
// stays open: 32 MiB keeps one stream above 2.4 Gbit/s at 54 ms RTT. Buffered
// request bytes per connection never exceed the connection window.
constexpr std::int32_t stream_window = 32 << 20;
constexpr std::int32_t connection_window = 64 << 20;
constexpr std::uint32_t streams_max = 128;
constexpr std::size_t header_bytes_max = std::size_t{64} << 10;
constexpr std::size_t response_buffer_max = std::size_t{2} << 20;
constexpr std::size_t backend_read_bytes = std::size_t{256} << 10;
constexpr std::size_t write_batch_bytes = std::size_t{1} << 20;
constexpr std::size_t socket_read_bytes = std::size_t{256} << 10;

#if NGHTTP2_VERSION_NUM >= 0x013d00
using BodySize = nghttp2_ssize;
#else
using BodySize = ssize_t;
#endif

void log_event(std::string_view event) {
    std::osyncstream(std::clog) << Json{{"event", event}}.dump() << '\n';
}
std::string lower(std::string_view text) {
    std::string out(text);
    std::transform(out.begin(), out.end(), out.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return out;
}
bool hop_by_hop(std::string_view name) {
    constexpr std::array names{"connection", "keep-alive", "proxy-connection", "transfer-encoding",
                               "upgrade",    "te",         "http2-settings"};
    return std::find(names.begin(), names.end(), name) != names.end();
}
// Client-controlled identity headers are replaced with the TLS peer address.
bool replaced(std::string_view name) {
    constexpr std::array names{"x-real-ip", "x-forwarded-for",  "x-forwarded-proto",
                               "forwarded", "x-speedtest-node", "expect",
                               "host"};
    return std::find(names.begin(), names.end(), name) != names.end();
}
int select_h2(SSL *, const unsigned char **out, unsigned char *out_size, const unsigned char *in,
              unsigned int in_size, void *) {
    for (unsigned int at = 0; at < in_size;) {
        const unsigned size = in[at];
        if (at + 1 + size > in_size)
            break;
        if (size == 2 && in[at + 1] == 'h' && in[at + 2] == '2') {
            *out = in + at + 1;
            *out_size = 2;
            return SSL_TLSEXT_ERR_OK;
        }
        at += 1 + size;
    }
    return SSL_TLSEXT_ERR_ALERT_FATAL;
}
std::shared_ptr<ssl::context> tls_context(const Config &cfg) {
    auto context = std::make_shared<ssl::context>(ssl::context::tls_server);
    context->set_options(ssl::context::default_workarounds | ssl::context::no_sslv2 |
                         ssl::context::no_sslv3 | ssl::context::no_tlsv1 |
                         ssl::context::no_tlsv1_1 | ssl::context::no_compression);
    SSL_CTX_set_options(context->native_handle(), SSL_OP_NO_RENEGOTIATION);
    context->use_certificate_chain_file(cfg.tls_cert);
    context->use_private_key_file(cfg.tls_key, ssl::context::pem);
    if (SSL_CTX_check_private_key(context->native_handle()) != 1)
        throw std::runtime_error("TLS private key does not match the certificate");
    SSL_CTX_set_alpn_select_cb(context->native_handle(), select_h2, nullptr);
    return context;
}
struct FileStamp {
    std::filesystem::file_time_type time{};
    std::uintmax_t size{};
    bool operator==(const FileStamp &) const = default;
};
std::array<FileStamp, 2> stamps(const Config &cfg) {
    std::array<FileStamp, 2> out{};
    std::error_code error;
    for (std::size_t i = 0; i < 2; ++i) {
        const auto &path = i == 0 ? cfg.tls_cert : cfg.tls_key;
        out[i].time = std::filesystem::last_write_time(path, error);
        out[i].size = std::filesystem::file_size(path, error);
    }
    return out;
}

struct Stream {
    Stream(std::int32_t stream_id, const asio::any_io_executor &executor)
        : id(stream_id), backend(executor), upload_ready(executor), download_space(executor) {
        upload_ready.expires_at(asio::steady_timer::time_point::max());
        download_space.expires_at(asio::steady_timer::time_point::max());
    }
    const std::int32_t id;
    std::string method, path, authority, cookies;
    std::vector<std::pair<std::string, std::string>> headers;
    std::size_t header_bytes{};
    bool length_known{}, started{};
    // Request bytes are acknowledged to the client (consumed) only after the
    // local listener accepted them, so memory is bounded by the HTTP/2 window.
    std::string upload;
    std::size_t unconsumed{};
    bool upload_done{}, discard_upload{};
    std::deque<std::string> download;
    std::size_t download_at{}, download_bytes{};
    bool download_done{}, responded{}, closed{};
    Tcp::socket backend;
    asio::steady_timer upload_ready, download_space;
};

struct Connection : std::enable_shared_from_this<Connection> {
    Connection(Tcp::socket socket, std::shared_ptr<ssl::context> context, Tcp::endpoint target,
               const Config &cfg, std::function<void()> done)
        : context_(std::move(context)), tls(std::move(socket)), target_(target),
          handshake_ms_(cfg.http_headers_ms), idle_ms_(cfg.http_keep_alive_ms),
          write_ready(tls.get_executor()), timer(tls.get_executor()), done_(std::move(done)) {
        write_ready.expires_at(asio::steady_timer::time_point::max());
        auto address = tls.remote_endpoint().address();
        if (address.is_v6() && address.to_v6().is_v4_mapped())
            address = asio::ip::make_address_v4(asio::ip::v4_mapped, address.to_v6());
        client = address.to_string();
        // Memory BIOs let one socket read carry many TLS records, so a single
        // relay/flow-control round handles hundreds of KiB at line rate.
        engine = SSL_new(context_->native_handle());
        auto *in = BIO_new(BIO_s_mem());
        auto *out = BIO_new(BIO_s_mem());
        if (!engine || !in || !out) {
            BIO_free(in);
            BIO_free(out);
            SSL_free(engine);
            throw std::runtime_error("TLS session allocation failed");
        }
        BIO_set_mem_eof_return(in, -1);
        SSL_set_bio(engine, in, out);
        SSL_set_accept_state(engine);
        cipher_in = in;
        cipher_out = out;
    }
    ~Connection() {
        if (session)
            nghttp2_session_del(session);
        SSL_free(engine);
    }
    std::shared_ptr<ssl::context> context_;
    Tcp::socket tls;
    const Tcp::endpoint target_;
    const std::uint64_t handshake_ms_, idle_ms_;
    asio::steady_timer write_ready, timer;
    std::function<void()> done_;
    std::string client;
    SSL *engine{};
    BIO *cipher_in{}, *cipher_out{};
    nghttp2_session *session{};
    std::map<std::int32_t, std::shared_ptr<Stream>> streams;
    bool closing{}, terminating{};

    void start() {
        auto self = shared_from_this();
        asio::co_spawn(tls.get_executor(), run(), [self](const std::exception_ptr &) {
            self->close();
            self->done_();
        });
    }
    void shutdown() {
        auto self = shared_from_this();
        asio::dispatch(tls.get_executor(), [self] {
            if (self->closing || self->terminating)
                return;
            if (!self->session) {
                self->close();
                return;
            }
            self->terminating = true;
            nghttp2_session_terminate_session(self->session, NGHTTP2_NO_ERROR);
            self->signal();
            self->timer.expires_after(std::chrono::seconds(2));
            self->timer.async_wait([self](Error error) {
                if (!error)
                    self->close();
            });
        });
    }
    void signal() {
        write_ready.cancel();
    }
    void close() {
        if (closing)
            return;
        closing = true;
        for (auto &[id, stream] : streams) {
            static_cast<void>(id);
            end_stream(*stream);
        }
        streams.clear();
        Error ignored;
        timer.cancel();
        write_ready.cancel();
        static_cast<void>(tls.close(ignored));
    }
    static void end_stream(Stream &stream) {
        stream.closed = true;
        Error ignored;
        static_cast<void>(stream.backend.close(ignored));
        stream.upload_ready.cancel();
        stream.download_space.cancel();
    }
    void arm_idle() {
        if (!streams.empty() || closing || terminating)
            return;
        timer.expires_after(std::chrono::milliseconds(idle_ms_));
        auto weak = weak_from_this();
        timer.async_wait([weak](Error error) {
            if (auto self = weak.lock();
                self && !error && self->streams.empty() && !self->closing && !self->terminating) {
                self->terminating = true;
                nghttp2_session_terminate_session(self->session, NGHTTP2_NO_ERROR);
                self->signal();
            }
        });
    }
    std::shared_ptr<Stream> find(std::int32_t id) const {
        const auto it = streams.find(id);
        return it == streams.end() ? nullptr : it->second;
    }
    void consume(Stream &stream, std::size_t bytes) {
        bytes = std::min(bytes, stream.unconsumed);
        if (bytes == 0 || closing)
            return;
        stream.unconsumed -= bytes;
        nghttp2_session_consume(session, stream.id, bytes);
        signal();
    }

    std::string take_cipher() {
        std::string out(BIO_ctrl_pending(cipher_out), '\0');
        if (!out.empty())
            BIO_read(cipher_out, out.data(), static_cast<int>(out.size()));
        return out;
    }
    void feed(const std::uint8_t *data, std::size_t size) {
        if (BIO_write(cipher_in, data, static_cast<int>(size)) != static_cast<int>(size))
            throw std::runtime_error("TLS input buffer failed");
    }
    awaitable<void> handshake(std::vector<std::uint8_t> &raw) {
        for (;;) {
            const auto done = SSL_do_handshake(engine);
            if (const auto out = take_cipher(); !out.empty())
                co_await asio::async_write(tls, asio::buffer(out), use_awaitable);
            if (done == 1)
                break;
            if (SSL_get_error(engine, done) != SSL_ERROR_WANT_READ)
                throw std::runtime_error("TLS handshake failed");
            const auto n = co_await tls.async_read_some(asio::buffer(raw), use_awaitable);
            feed(raw.data(), n);
        }
        const unsigned char *protocol = nullptr;
        unsigned int protocol_size = 0;
        SSL_get0_alpn_selected(engine, &protocol, &protocol_size);
        if (protocol_size != 2 || std::memcmp(protocol, "h2", 2) != 0)
            throw std::runtime_error("client did not negotiate HTTP/2");
    }
    awaitable<void> run() {
        auto self = shared_from_this();
        timer.expires_after(std::chrono::milliseconds(handshake_ms_));
        timer.async_wait([weak = weak_from_this()](Error error) {
            if (auto conn = weak.lock(); conn && !error)
                conn->close();
        });
        std::vector<std::uint8_t> raw(socket_read_bytes);
        co_await handshake(raw);
        timer.cancel();
        open_session();
        asio::co_spawn(tls.get_executor(), writer(),
                       [self](const std::exception_ptr &) { self->close(); });
        arm_idle();
        // Plaintext never exceeds the ciphertext fed plus one partial record.
        std::vector<std::uint8_t> plain(socket_read_bytes + (std::size_t{32} << 10));
        bool open = true;
        while (open && !closing) {
            std::size_t got = 0;
            for (;;) {
                const auto n =
                    SSL_read(engine, plain.data() + got, static_cast<int>(plain.size() - got));
                if (n > 0) {
                    got += static_cast<std::size_t>(n);
                    if (got == plain.size())
                        break;
                    continue;
                }
                if (SSL_get_error(engine, n) != SSL_ERROR_WANT_READ)
                    open = false;
                break;
            }
            if (got) {
#if NGHTTP2_VERSION_NUM >= 0x013d00
                const auto used = nghttp2_session_mem_recv2(session, plain.data(), got);
#else
                const auto used = nghttp2_session_mem_recv(session, plain.data(), got);
#endif
                if (used < 0)
                    break;
            }
            if (got || BIO_ctrl_pending(cipher_out))
                signal();
            if (!open || got == plain.size())
                continue;
            Error error;
            const auto n = co_await tls.async_read_some(asio::buffer(raw),
                                                        asio::redirect_error(use_awaitable, error));
            if (error || closing)
                break;
            feed(raw.data(), n);
        }
        close();
    }
    awaitable<void> writer() {
        auto self = shared_from_this();
        std::string output;
        while (!closing) {
            output.clear();
            while (output.size() < write_batch_bytes) {
                const std::uint8_t *data = nullptr;
#if NGHTTP2_VERSION_NUM >= 0x013d00
                const auto n = nghttp2_session_mem_send2(session, &data);
#else
                const auto n = nghttp2_session_mem_send(session, &data);
#endif
                if (n < 0)
                    co_return;
                if (n == 0)
                    break;
                output.append(reinterpret_cast<const char *>(data), static_cast<std::size_t>(n));
            }
            if (!output.empty()) {
                if (SSL_write(engine, output.data(), static_cast<int>(output.size())) <= 0)
                    co_return;
            }
            if (auto cipher = take_cipher(); !cipher.empty()) {
                co_await asio::async_write(tls, asio::buffer(cipher), use_awaitable);
                continue;
            }
            if (!nghttp2_session_want_read(session) && !nghttp2_session_want_write(session))
                co_return;
            Error ignored;
            co_await write_ready.async_wait(asio::redirect_error(use_awaitable, ignored));
            write_ready.expires_at(asio::steady_timer::time_point::max());
        }
    }

    void open_session() {
        nghttp2_session_callbacks *callbacks = nullptr;
        nghttp2_session_callbacks_new(&callbacks);
        nghttp2_session_callbacks_set_on_begin_headers_callback(callbacks, on_begin_headers);
        nghttp2_session_callbacks_set_on_header_callback(callbacks, on_header);
        nghttp2_session_callbacks_set_on_frame_recv_callback(callbacks, on_frame_recv);
        nghttp2_session_callbacks_set_on_data_chunk_recv_callback(callbacks, on_data);
        nghttp2_session_callbacks_set_on_stream_close_callback(callbacks, on_close);
        nghttp2_session_callbacks_set_on_frame_send_callback(callbacks, on_frame_send);
        nghttp2_option *option = nullptr;
        nghttp2_option_new(&option);
        nghttp2_option_set_no_auto_window_update(option, 1);
        const auto created = nghttp2_session_server_new2(&session, callbacks, this, option);
        nghttp2_option_del(option);
        nghttp2_session_callbacks_del(callbacks);
        if (created != 0)
            throw std::runtime_error("HTTP/2 session allocation failed");
        const std::array settings{
            nghttp2_settings_entry{NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS, streams_max},
            nghttp2_settings_entry{NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE,
                                   static_cast<std::uint32_t>(stream_window)},
            nghttp2_settings_entry{NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE,
                                   static_cast<std::uint32_t>(header_bytes_max)}};
        if (nghttp2_submit_settings(session, NGHTTP2_FLAG_NONE, settings.data(), settings.size()) !=
                0 ||
            nghttp2_session_set_local_window_size(session, NGHTTP2_FLAG_NONE, 0,
                                                  connection_window) != 0)
            throw std::runtime_error("HTTP/2 settings rejected");
    }

    static Connection &of(void *data) {
        return *static_cast<Connection *>(data);
    }
    static int on_begin_headers(nghttp2_session *, const nghttp2_frame *frame, void *data) {
        auto &conn = of(data);
        if (frame->hd.type == NGHTTP2_HEADERS && frame->headers.cat == NGHTTP2_HCAT_REQUEST)
            conn.streams.emplace(
                frame->hd.stream_id,
                std::make_shared<Stream>(frame->hd.stream_id, conn.tls.get_executor()));
        if (!conn.terminating)
            conn.timer.cancel();
        return 0;
    }
    static int on_header(nghttp2_session *, const nghttp2_frame *frame, const std::uint8_t *name,
                         std::size_t name_size, const std::uint8_t *value, std::size_t value_size,
                         std::uint8_t, void *data) {
        if (frame->hd.type != NGHTTP2_HEADERS || frame->headers.cat != NGHTTP2_HCAT_REQUEST)
            return 0;
        const auto stream = of(data).find(frame->hd.stream_id);
        if (!stream)
            return 0;
        const std::string_view key(reinterpret_cast<const char *>(name), name_size);
        const std::string_view text(reinterpret_cast<const char *>(value), value_size);
        stream->header_bytes += name_size + value_size + 32;
        if (stream->header_bytes > header_bytes_max ||
            text.find_first_of("\r\n", 0) != std::string_view::npos)
            return NGHTTP2_ERR_TEMPORAL_CALLBACK_FAILURE;
        if (key == ":method")
            stream->method = text;
        else if (key == ":path")
            stream->path = text;
        else if (key == ":authority")
            stream->authority = text;
        else if (key == "host") {
            if (stream->authority.empty())
                stream->authority = text;
        } else if (key == "cookie")
            stream->cookies += (stream->cookies.empty() ? "" : "; ") + std::string(text);
        else if (!key.starts_with(':') && !replaced(key) && !hop_by_hop(key)) {
            if (key == "content-length")
                stream->length_known = true;
            stream->headers.emplace_back(key, text);
        }
        return 0;
    }
    static int on_frame_recv(nghttp2_session *, const nghttp2_frame *frame, void *data) {
        auto &conn = of(data);
        const auto stream = conn.find(frame->hd.stream_id);
        if (!stream)
            return 0;
        const bool end = (frame->hd.flags & NGHTTP2_FLAG_END_STREAM) != 0;
        if ((frame->hd.type == NGHTTP2_DATA || frame->hd.type == NGHTTP2_HEADERS) && end) {
            stream->upload_done = true;
            stream->upload_ready.cancel();
        }
        if (frame->hd.type == NGHTTP2_HEADERS && frame->headers.cat == NGHTTP2_HCAT_REQUEST &&
            !stream->started) {
            stream->started = true;
            if (stream->method.empty() || stream->path.empty() || stream->authority.empty()) {
                conn.reply_error(*stream, 400);
                return 0;
            }
            auto self = conn.shared_from_this();
            asio::co_spawn(conn.tls.get_executor(), self->relay(stream),
                           [self, stream](const std::exception_ptr &error) {
                               if (error && !stream->closed && !stream->responded && !self->closing)
                                   self->reply_error(*stream, 502);
                           });
        }
        return 0;
    }
    static int on_data(nghttp2_session *session, std::uint8_t, std::int32_t id,
                       const std::uint8_t *bytes, std::size_t size, void *data) {
        const auto stream = of(data).find(id);
        if (!stream || stream->discard_upload) {
            nghttp2_session_consume(session, id, size);
            return 0;
        }
        stream->upload.append(reinterpret_cast<const char *>(bytes), size);
        stream->unconsumed += size;
        stream->upload_ready.cancel();
        return 0;
    }
    static int on_close(nghttp2_session *session, std::int32_t id, std::uint32_t, void *data) {
        auto &conn = of(data);
        if (const auto it = conn.streams.find(id); it != conn.streams.end()) {
            auto &stream = *it->second;
            // Connection-level credit for bytes that will never reach the listener.
            if (stream.unconsumed)
                nghttp2_session_consume_connection(session, stream.unconsumed);
            stream.unconsumed = 0;
            end_stream(stream);
            conn.streams.erase(it);
        }
        conn.arm_idle();
        return 0;
    }
    static int on_frame_send(nghttp2_session *session, const nghttp2_frame *frame, void *data) {
        const auto stream = of(data).find(frame->hd.stream_id);
        if (!stream || !(frame->hd.flags & NGHTTP2_FLAG_END_STREAM) ||
            (frame->hd.type != NGHTTP2_DATA && frame->hd.type != NGHTTP2_HEADERS))
            return 0;
        // The reply is complete; stop the rest of an unfinished request body.
        if (!stream->upload_done)
            nghttp2_submit_rst_stream(session, NGHTTP2_FLAG_NONE, stream->id, NGHTTP2_NO_ERROR);
        return 0;
    }
    static BodySize read_body(nghttp2_session *, std::int32_t, std::uint8_t *buffer,
                              std::size_t size, std::uint32_t *flags, nghttp2_data_source *source,
                              void *) {
        auto &stream = *static_cast<Stream *>(source->ptr);
        std::size_t copied = 0;
        while (copied < size && !stream.download.empty()) {
            auto &front = stream.download.front();
            const auto n = std::min(size - copied, front.size() - stream.download_at);
            std::memcpy(buffer + copied, front.data() + stream.download_at, n);
            copied += n;
            stream.download_at += n;
            if (stream.download_at == front.size()) {
                stream.download.pop_front();
                stream.download_at = 0;
            }
        }
        stream.download_bytes -= copied;
        if (stream.download_bytes < response_buffer_max / 2)
            stream.download_space.cancel();
        if (stream.download.empty() && stream.download_done)
            *flags |= NGHTTP2_DATA_FLAG_EOF;
        else if (copied == 0)
            return NGHTTP2_ERR_DEFERRED;
        return static_cast<BodySize>(copied);
    }
    void submit(Stream &stream, const std::vector<nghttp2_nv> &fields, bool body) {
#if NGHTTP2_VERSION_NUM >= 0x013d00
        nghttp2_data_provider2 provider{};
#else
        nghttp2_data_provider provider{};
#endif
        provider.source.ptr = &stream;
        provider.read_callback = read_body;
#if NGHTTP2_VERSION_NUM >= 0x013d00
        nghttp2_submit_response2(session, stream.id, fields.data(), fields.size(),
                                 body ? &provider : nullptr);
#else
        nghttp2_submit_response(session, stream.id, fields.data(), fields.size(),
                                body ? &provider : nullptr);
#endif
        stream.responded = true;
        signal();
    }
    static nghttp2_nv field(const std::string &name, const std::string &value) {
        return {reinterpret_cast<std::uint8_t *>(const_cast<char *>(name.data())),
                reinterpret_cast<std::uint8_t *>(const_cast<char *>(value.data())), name.size(),
                value.size(), NGHTTP2_NV_FLAG_NONE};
    }
    void reply_error(Stream &stream, unsigned status) {
        if (stream.responded || stream.closed || closing)
            return;
        stream.discard_upload = true;
        consume(stream, stream.unconsumed);
        stream.upload.clear();
        const std::string name = ":status", value = std::to_string(status);
        submit(stream, {field(name, value)}, false);
    }

    awaitable<void> relay(std::shared_ptr<Stream> stream) {
        auto self = shared_from_this();
        Error error;
        co_await stream->backend.async_connect(target_, asio::redirect_error(use_awaitable, error));
        if (stream->closed || closing)
            co_return;
        if (error) {
            reply_error(*stream, 502);
            co_return;
        }
        static_cast<void>(stream->backend.set_option(Tcp::no_delay(true), error));
        const bool chunked = !stream->upload_done && !stream->length_known;
        std::string head = stream->method + ' ' + stream->path +
                           " HTTP/1.1\r\nhost: " + stream->authority + "\r\n";
        for (const auto &[name, value] : stream->headers)
            head += name + ": " + value + "\r\n";
        if (!stream->cookies.empty())
            head += "cookie: " + stream->cookies + "\r\n";
        head += "x-real-ip: " + client + "\r\nx-forwarded-for: " + client +
                "\r\nx-forwarded-proto: https\r\nconnection: close\r\n";
        if (chunked)
            head += "transfer-encoding: chunked\r\n";
        head += "\r\n";
        asio::co_spawn(tls.get_executor(), respond(stream),
                       [self, stream](const std::exception_ptr &failed) {
                           if (failed && !stream->closed && !self->closing) {
                               if (!stream->responded)
                                   self->reply_error(*stream, 502);
                               else if (!stream->download_done)
                                   nghttp2_submit_rst_stream(self->session, NGHTTP2_FLAG_NONE,
                                                             stream->id, NGHTTP2_INTERNAL_ERROR);
                               self->signal();
                           }
                       });
        co_await asio::async_write(stream->backend, asio::buffer(head),
                                   asio::redirect_error(use_awaitable, error));
        while (!error && !stream->closed && !closing && !stream->discard_upload) {
            if (!stream->upload.empty()) {
                std::string data;
                data.swap(stream->upload);
                if (chunked) {
                    std::array<char, 20> size{};
                    const auto [end, ignored] =
                        std::to_chars(size.data(), size.data() + size.size(), data.size(), 16);
                    static_cast<void>(ignored);
                    std::string prefix(size.data(), end);
                    prefix += "\r\n";
                    const std::array<asio::const_buffer, 3> buffers{
                        asio::buffer(prefix), asio::buffer(data), asio::buffer("\r\n", 2)};
                    co_await asio::async_write(stream->backend, buffers,
                                               asio::redirect_error(use_awaitable, error));
                } else
                    co_await asio::async_write(stream->backend, asio::buffer(data),
                                               asio::redirect_error(use_awaitable, error));
                if (!stream->closed && !closing)
                    consume(*stream, data.size());
                continue;
            }
            if (stream->upload_done) {
                if (chunked)
                    co_await asio::async_write(stream->backend, asio::buffer("0\r\n\r\n", 5),
                                               asio::redirect_error(use_awaitable, error));
                co_return;
            }
            Error ignored;
            co_await stream->upload_ready.async_wait(asio::redirect_error(use_awaitable, ignored));
            stream->upload_ready.expires_at(asio::steady_timer::time_point::max());
        }
        if (error && !stream->closed && !closing) {
            // The listener stopped reading (for example after a rejection); its
            // reply is still relayed and the remaining request body is dropped.
            stream->discard_upload = true;
            consume(*stream, stream->unconsumed);
            stream->upload.clear();
        }
    }
    awaitable<void> respond(std::shared_ptr<Stream> stream) {
        auto self = shared_from_this();
        beast::flat_buffer input;
        // Beast sizes each socket read from the free capacity (512 bytes when empty).
        input.reserve(backend_read_bytes);
        http::response_parser<http::buffer_body> parser;
        parser.body_limit(boost::none);
        parser.header_limit(static_cast<std::uint32_t>(header_bytes_max));
        if (stream->method == "HEAD")
            parser.skip(true);
        co_await http::async_read_header(stream->backend, input, parser, use_awaitable);
        if (stream->closed || closing)
            co_return;
        auto &message = parser.get();
        std::vector<std::pair<std::string, std::string>> owned;
        owned.emplace_back(":status", std::to_string(message.result_int()));
        for (const auto &item : message.base()) {
            const auto raw = item.name_string();
            auto name = lower(std::string_view(raw.data(), raw.size()));
            if (!hop_by_hop(name))
                owned.emplace_back(std::move(name),
                                   std::string(item.value().data(), item.value().size()));
        }
        std::vector<nghttp2_nv> fields;
        fields.reserve(owned.size());
        for (const auto &[name, value] : owned)
            fields.push_back(field(name, value));
        const bool body = !parser.is_done();
        stream->download_done = !body;
        submit(*stream, fields, body);
        if (!body)
            co_return;
        std::vector<char> chunk(backend_read_bytes);
        while (!parser.is_done()) {
            if (stream->download_bytes >= response_buffer_max) {
                Error ignored;
                co_await stream->download_space.async_wait(
                    asio::redirect_error(use_awaitable, ignored));
                stream->download_space.expires_at(asio::steady_timer::time_point::max());
                if (stream->closed || closing)
                    co_return;
                continue;
            }
            message.body().data = chunk.data();
            message.body().size = chunk.size();
            Error error;
            co_await http::async_read_some(stream->backend, input, parser,
                                           asio::redirect_error(use_awaitable, error));
            if (error == http::error::need_buffer)
                error = {};
            if (stream->closed || closing)
                co_return;
            if (error)
                throw boost::system::system_error(error);
            const auto n = chunk.size() - message.body().size;
            if (n) {
                stream->download.emplace_back(chunk.data(), n);
                stream->download_bytes += n;
                nghttp2_session_resume_data(session, stream->id);
                signal();
            }
        }
        stream->download_done = true;
        nghttp2_session_resume_data(session, stream->id);
        signal();
    }
};
} // namespace

struct TlsEdge::Impl : std::enable_shared_from_this<TlsEdge::Impl> {
    Impl(asio::io_context &io, const Config &config)
        : context_io(io), cfg(config), acceptor(asio::make_strand(io)),
          reload(acceptor.get_executor()), context(tls_context(cfg)), stamp(stamps(cfg)),
          target(asio::ip::make_address(cfg.host), static_cast<unsigned short>(cfg.port)) {
        const Tcp::endpoint endpoint(asio::ip::make_address(cfg.tls_host),
                                     static_cast<unsigned short>(cfg.tls_port));
        acceptor.open(endpoint.protocol());
        acceptor.set_option(Tcp::acceptor::reuse_address(true));
        acceptor.bind(endpoint);
        acceptor.listen(asio::socket_base::max_listen_connections);
    }
    asio::io_context &context_io;
    const Config cfg;
    Tcp::acceptor acceptor;
    asio::steady_timer reload;
    std::shared_ptr<ssl::context> context;
    std::array<FileStamp, 2> stamp;
    const Tcp::endpoint target;
    std::map<std::uint64_t, std::weak_ptr<Connection>> connections;
    std::uint64_t next_id{};
    bool stopping{};

    awaitable<void> accept() {
        auto self = shared_from_this();
        while (!stopping) {
            Error error;
            auto socket = co_await acceptor.async_accept(
                asio::make_strand(context_io), asio::redirect_error(use_awaitable, error));
            if (stopping)
                co_return;
            if (error) {
                // Descriptor exhaustion is transient; do not spin on it.
                asio::steady_timer pause(acceptor.get_executor(), std::chrono::milliseconds(100));
                co_await pause.async_wait(asio::redirect_error(use_awaitable, error));
                continue;
            }
            if (connections.size() >= cfg.max_http_connections) {
                static_cast<void>(socket.close(error));
                continue;
            }
            static_cast<void>(socket.set_option(Tcp::no_delay(true), error));
            const auto id = ++next_id;
            std::shared_ptr<Connection> conn;
            try {
                conn = std::make_shared<Connection>(
                    std::move(socket), context, target, cfg, [weak = weak_from_this(), id] {
                        if (auto edge = weak.lock())
                            asio::dispatch(edge->acceptor.get_executor(),
                                           [edge, id] { edge->connections.erase(id); });
                    });
            } catch (const boost::system::system_error &) {
                continue;
            }
            connections.emplace(id, conn);
            conn->start();
        }
    }
    void watch() {
        reload.expires_after(std::chrono::seconds(30));
        reload.async_wait([weak = weak_from_this()](Error error) {
            auto self = weak.lock();
            if (!self || error || self->stopping)
                return;
            const auto now = stamps(self->cfg);
            if (now != self->stamp) {
                try {
                    self->context = tls_context(self->cfg);
                    self->stamp = now;
                    log_event("tls_certificate_reloaded");
                } catch (const std::exception &) {
                    log_event("tls_certificate_reload_failed");
                }
            }
            self->watch();
        });
    }
};

TlsEdge::TlsEdge(asio::io_context &io, const Config &cfg)
    : impl_(std::make_shared<Impl>(io, cfg)) {}
TlsEdge::~TlsEdge() = default;
void TlsEdge::start() {
    const auto impl = impl_;
    asio::dispatch(impl->acceptor.get_executor(), [impl] { impl->watch(); });
    asio::co_spawn(impl->acceptor.get_executor(), impl->accept(),
                   [impl](const std::exception_ptr &error) {
                       if (error)
                           log_event("tls_accept_failed");
                   });
}
void TlsEdge::stop() {
    const auto impl = impl_;
    asio::dispatch(impl->acceptor.get_executor(), [impl] {
        if (impl->stopping)
            return;
        impl->stopping = true;
        Error ignored;
        static_cast<void>(impl->acceptor.close(ignored));
        impl->reload.cancel();
        for (const auto &[id, weak] : impl->connections) {
            static_cast<void>(id);
            if (auto conn = weak.lock())
                conn->shutdown();
        }
    });
}
} // namespace speedtest
