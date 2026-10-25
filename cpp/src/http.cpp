#include "speedtest/http.hpp"
#include "speedtest/client_info.hpp"
#include "speedtest/progress.hpp"
#include "speedtest/sdp.hpp"
#include <algorithm>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/dispatch.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/read.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/strand.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/asio/write.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>
#include <charconv>
#include <cmath>
#include <ctime>
#include <iostream>
#include <limits>
#include <map>
#include <openssl/rand.h>
#include <set>
#include <syncstream>

namespace speedtest {
namespace {
namespace asio = boost::asio;
namespace beast = boost::beast;
namespace http = beast::http;
using Tcp = asio::ip::tcp;
using Error = boost::system::error_code;
using asio::awaitable;
using asio::use_awaitable;
using Millis = std::chrono::milliseconds;
using Response = http::response<http::string_body>;
void observe(const Error &error) {
    if (error)
        std::osyncstream(std::clog)
            << Json{{"event", "socket_operation_error"}, {"code", error.value()}}.dump() << '\n';
}
constexpr std::size_t chunk_size = std::size_t{512} * 1024;

std::string lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text;
}
std::string header(const http::fields &fields, std::string_view key) {
    const auto it = fields.find(beast::string_view(key.data(), key.size()));
    return it == fields.end() ? std::string{} : std::string(it->value());
}
bool token(std::string_view text, std::string_view key) {
    while (!text.empty()) {
        auto end = text.find(',');
        if (lower(std::string(trim_text(text.substr(0, end)))) == key)
            return true;
        if (end == std::string_view::npos)
            break;
        text.remove_prefix(end + 1);
    }
    return false;
}
bool path_is(std::string_view path, std::string_view target) {
    return path == target || path == "/speed" + std::string(target) ||
           (path == "/speed" && target == "/");
}
bool speed_path(std::string_view path) {
    constexpr std::array paths{"/download", "/download-100k", "/download-stream",
                               "/upload",   "/upload-stream", "/upload-progress"};
    return std::any_of(paths.begin(), paths.end(),
                       [path](auto item) { return path_is(path, item); });
}
std::string decoded(std::string_view raw) {
    std::string out;
    auto hex = [](char c) -> int {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return c - 'a' + 10;
        if (c >= 'A' && c <= 'F')
            return c - 'A' + 10;
        return -1;
    };
    for (std::size_t i = 0; i < raw.size(); ++i) {
        if (raw[i] == '+')
            out += ' ';
        else if (raw[i] == '%' && i + 2 < raw.size() && hex(raw[i + 1]) >= 0 &&
                 hex(raw[i + 2]) >= 0) {
            out += static_cast<char>(hex(raw[i + 1]) * 16 + hex(raw[i + 2]));
            i += 2;
        } else
            out += raw[i];
    }
    return out;
}
std::optional<std::string> query(const std::string &raw_target, std::string_view name) {
    auto target = std::string_view(raw_target);
    const auto start = target.find('?');
    if (start == std::string_view::npos)
        return std::nullopt;
    target.remove_prefix(start + 1);
    while (!target.empty()) {
        const auto end = target.find('&');
        const auto part = target.substr(0, end);
        const auto eq = part.find('=');
        if (decoded(part.substr(0, eq)) == name)
            return eq == std::string_view::npos ? "" : decoded(part.substr(eq + 1));
        if (end == std::string_view::npos)
            break;
        target.remove_prefix(end + 1);
    }
    return std::nullopt;
}
struct Bounds {
    std::uint64_t fallback, minimum, maximum;
};
std::uint64_t bounded(const std::optional<std::string> &raw, Bounds bounds) {
    const auto [fallback, low, high] = bounds;
    if (!raw || raw->empty())
        return fallback;
    double value{};
    auto text = std::string_view(*raw);
    if (text.starts_with('+'))
        text.remove_prefix(1);
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size() || !std::isfinite(value))
        return fallback;
    return static_cast<std::uint64_t>(
        std::clamp(std::floor(value), static_cast<double>(low), static_cast<double>(high)));
}
std::optional<unsigned> unsigned_number(std::string_view text) {
    if (text.starts_with('+'))
        text.remove_prefix(1);
    unsigned n{};
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), n);
    if (error != std::errc{} || end != text.data() + text.size() || text.empty())
        return std::nullopt;
    return n;
}
Response response(unsigned status, std::string body = {}, std::string_view type = {},
                  std::string_view cache = {}) {
    Response out{static_cast<http::status>(status), 11};
    out.body() = std::move(body);
    if (!type.empty())
        out.set(http::field::content_type, beast::string_view(type.data(), type.size()));
    if (!cache.empty())
        out.set(http::field::cache_control, beast::string_view(cache.data(), cache.size()));
    return out;
}
Response json(const Json &value, unsigned status = 200) {
    auto out = response(status, value.dump(), "application/json", "no-store");
    out.content_length(out.body().size());
    return out;
}
Response failure(unsigned status, std::string_view reason, bool close = false) {
    auto out = json(Json{{"error", reason}}, status);
    if (close)
        out.set(http::field::connection, "close");
    return out;
}
void replace_all(std::string &text, std::string_view needle, std::string_view value) {
    std::size_t at = 0;
    while ((at = text.find(needle, at)) != std::string::npos) {
        text.replace(at, needle.size(), value);
        at += value.size();
    }
}
bool private_client(std::string_view raw) {
    Error error;
    const auto address = asio::ip::make_address(raw, error);
    if (error)
        return true;
    if (address.is_v4())
        return !is_public_ip(address);
    const auto words = address.to_v6().to_bytes();
    return (words[0] & 0xe0U) != 0x20U ||
           (words[0] == 0x20 && words[1] == 1 && words[2] == 0x0d && words[3] == 0xb8);
}
struct App {
    explicit App(Config value)
        : cfg(std::move(value)), limits(cfg), info(std::make_shared<ClientInfo>()),
          payload(chunk_size) {
        if (RAND_bytes(reinterpret_cast<unsigned char *>(payload.data()),
                       static_cast<int>(payload.size())) != 1)
            throw std::runtime_error("download entropy unavailable");
    }
    Config cfg;
    Limits limits;
    ProgressRegistry progress;
    std::shared_ptr<ClientInfo> info;
    const Clock::time_point epoch = Clock::now();
    std::vector<char> payload;
    Clock::time_point checked_deadline(Clock::time_point start, std::uint64_t ms) const {
        const auto seconds =
            std::chrono::duration_cast<std::chrono::seconds>(start + Millis(ms) - epoch).count();
        return epoch + std::chrono::seconds((seconds / 30 + 1) * 30);
    }
};

class Session : public std::enable_shared_from_this<Session> {
  public:
    Session(Tcp::socket socket, std::shared_ptr<App> app, std::function<void()> done)
        : socket_(std::move(socket)), read_timer_(socket_.get_executor()),
          write_timer_(socket_.get_executor()), idle_timer_(socket_.get_executor()),
          drain_gate_(socket_.get_executor()), tick_(socket_.get_executor()), app_(std::move(app)),
          done_(std::move(done)) {
        peer_ = socket_.remote_endpoint().address().to_string();
    }
    void start() {
        const auto self = shared_from_this();
        asio::co_spawn(socket_.get_executor(), self->run(),
                       [self](const std::exception_ptr &error) {
                           if (error) {
                               try {
                                   std::rethrow_exception(error);
                               } catch (const std::exception &) {
                                   std::clog << "{\"event\":\"http_connection_error\"}\n";
                               }
                           }
                           self->close();
                           self->done_();
                       });
    }
    void shutdown(bool force) {
        const auto self = shared_from_this();
        asio::dispatch(socket_.get_executor(), [self, force] {
            self->stopping_ = true;
            if (force || !self->busy_)
                self->close();
        });
    }

  private:
    void cancel_read() {
        ++read_generation_;
        read_timer_.cancel();
    }
    void cancel_write() {
        ++write_generation_;
        write_timer_.cancel();
    }
    void cancel_idle() {
        ++idle_generation_;
        idle_timer_.cancel();
    }
    void close() {
        Error ignored;
        observe(socket_.close(ignored));
        cancel_read();
        cancel_write();
        cancel_idle();
        tick_.cancel();
        drain_gate_.cancel();
    }
    void arm_read(Clock::time_point deadline, bool close_socket = false) {
        cancel_read();
        const auto generation = read_generation_;
        read_timed_out_ = false;
        read_timer_.expires_at(deadline);
        const auto weak = weak_from_this();
        read_timer_.async_wait([weak, close_socket, generation](Error error) {
            if (!error)
                if (auto self = weak.lock()) {
                    if (self->read_generation_ != generation)
                        return;
                    self->read_timed_out_ = true;
                    if (close_socket)
                        self->close();
                    else {
                        Error ignored;
                        observe(self->socket_.cancel(ignored));
                    }
                }
        });
    }
    void arm_write(Clock::time_point deadline) {
        cancel_write();
        const auto generation = write_generation_;
        write_timer_.expires_at(deadline);
        const auto weak = weak_from_this();
        write_timer_.async_wait([weak, generation](Error error) {
            if (!error)
                if (auto self = weak.lock())
                    if (self->write_generation_ == generation)
                        self->close();
        });
    }
    void arm_idle() {
        cancel_idle();
        const auto generation = idle_generation_;
        idle_timer_.expires_after(Millis(app_->cfg.http_keep_alive_ms + 1000));
        const auto weak = weak_from_this();
        idle_timer_.async_wait([weak, generation](Error error) {
            if (!error)
                if (auto self = weak.lock())
                    if (self->idle_generation_ == generation)
                        self->close();
        });
    }
    awaitable<void> run();
    awaitable<Error> read_head();
    awaitable<void> drain();
    void start_drain();
    awaitable<std::size_t> read_body(std::span<char> buffer);
    awaitable<void> send(Response out);
    awaitable<void> send_head(Response out);
    awaitable<void> continue_body();
    awaitable<void> request_timeout();
    void decorate(Response &out);
    awaitable<void> route();
    awaitable<void> download(bool streaming);
    awaitable<void> upload(bool streaming);
    awaitable<void> progress();
    awaitable<void> offer();
    awaitable<void> chunk(std::string_view data);
    awaitable<void> chunk_end();
    Response page();
    std::string client_ip() const;
    bool cross_site() const;
    bool probe_only() const;
    Tcp::socket socket_;
    asio::steady_timer read_timer_, write_timer_, idle_timer_, drain_gate_, tick_;
    beast::flat_buffer input_{std::size_t{512} * 1024};
    std::unique_ptr<http::request_parser<http::buffer_body>> parser_;
    std::shared_ptr<App> app_;
    std::function<void()> done_;
    std::string peer_, ip_, target_, path_;
    Clock::time_point request_start_{};
    std::uint64_t requests_{};
    std::uint64_t read_generation_{}, write_generation_{}, idle_generation_{};
    bool read_timed_out_{}, body_drain_{}, drain_done_{true}, response_done_{}, busy_{},
        stopping_{}, close_response_{}, terminal_response_{}, head_{}, excess_{};
};
std::string Session::client_ip() const {
    const auto &cfg = app_->cfg;
    const auto &fields = parser_->get().base();
    if (!cfg.trust_proxy || !cfg.trusted_proxy_ips.contains(peer_))
        return peer_;
    auto raw = header(fields, "x-real-ip");
    if (raw.empty())
        raw = header(fields, "x-forwarded-for");
    auto value = trim_text(std::string_view(raw).substr(0, raw.find(',')));
    while (value.starts_with("::ffff:"))
        value.remove_prefix(7);
    Error error;
    asio::ip::make_address(value, error);
    return error ? peer_ : std::string(value);
}
bool Session::cross_site() const {
    const auto &fields = parser_->get().base();
    const auto origin = header(fields, "origin");
    const auto &cfg = app_->cfg;
    return lower(header(fields, "sec-fetch-site")) == "cross-site" &&
           !(cfg.allow_cross_site && !origin.empty() && origin != "null" &&
             cfg.allowed_origins.contains(origin));
}
bool Session::probe_only() const {
    const auto &cfg = app_->cfg;
    if (cfg.probe_only)
        return true;
    if (header(parser_->get().base(), "x-speedtest-node") == cfg.probe_only_id)
        return true;
    auto host = header(parser_->get().base(), "host");
    return !cfg.probe_only_host.empty() && host.substr(0, host.find(':')) == cfg.probe_only_host;
}
void Session::decorate(Response &out) {
    const auto &request = parser_->get();
    const auto &cfg = app_->cfg;
    out.version(request.version());
    out.set("access-control-allow-methods", "GET,POST,HEAD,OPTIONS");
    out.set("access-control-allow-headers", "Content-Type, Authorization, X-Speedtest-Attempt-Id, "
                                            "X-Speedtest-Page-Instance, X-Speedtest-Session-Id");
    out.set("access-control-max-age", "86400");
    out.set("x-content-type-options", "nosniff");
    out.set("referrer-policy", "no-referrer");
    const auto origin = header(request.base(), "origin");
    if (!origin.empty() && (cfg.allowed_origins.contains(origin) ||
                            (cfg.public_cors && (path_is(path_, "/") || path_is(path_, "/health") ||
                                                 path_is(path_, "/latency"))))) {
        out.set("access-control-allow-origin", origin);
        out.set(http::field::vary, "Origin");
    }
    if (token(header(out.base(), "connection"), "close")) {
        close_response_ = true;
        terminal_response_ = true;
    }
    if (close_response_)
        out.set(http::field::connection, "close");
    else if (request.version() == 11) {
        out.set(http::field::connection, "keep-alive");
        out.set("keep-alive", "timeout=" + std::to_string(cfg.http_keep_alive_ms / 1000) +
                                  ", max=" + std::to_string(cfg.http_max_requests_per_socket));
    }
    const auto now = std::time(nullptr);
    std::tm utc{};
    if (gmtime_r(&now, &utc)) {
        std::array<char, 64> text{};
        const auto n = std::strftime(text.data(), text.size(), "%a, %d %b %Y %H:%M:%S GMT", &utc);
        out.set(http::field::date, std::string(text.data(), n));
    }
}
awaitable<void> Session::send(Response out) {
    decorate(out);
    if (out.result_int() != 204 && !out.has_content_length() && !out.chunked()) {
        if (path_is(path_, "/") && out.result_int() == 200)
            out.content_length(out.body().size());
        else
            out.chunked(out.version() == 11);
    }
    if (head_) {
        // Hyper suppresses HEAD bodies without replacing an explicit resource length.
        const auto length =
            out.has_content_length() ? std::string(out[http::field::content_length]) : "0";
        out.chunked(false);
        out.set(http::field::content_length, length);
        http::response_serializer<http::string_body> serializer(out);
        co_await http::async_write_header(socket_, serializer, use_awaitable);
    } else
        co_await http::async_write(socket_, out, use_awaitable);
    response_done_ = true;
}
awaitable<void> Session::send_head(Response out) {
    decorate(out);
    http::response_serializer<http::string_body> serializer(out);
    co_await http::async_write_header(socket_, serializer, use_awaitable);
}
awaitable<void> Session::continue_body() {
    if (!parser_->is_done() && lower(header(parser_->get().base(), "expect")) == "100-continue") {
        static constexpr std::string_view message = "HTTP/1.1 100 Continue\r\n\r\n";
        co_await asio::async_write(socket_, asio::buffer(message.data(), message.size()),
                                   use_awaitable);
    }
}
awaitable<void> Session::request_timeout() {
    terminal_response_ = true;
    close_response_ = true;
    http::response<http::empty_body> out{http::status::request_timeout, 11};
    out.content_length(0);
    out.set(http::field::connection, "close");
    const auto now = std::time(nullptr);
    std::tm utc{};
    gmtime_r(&now, &utc);
    std::array<char, 64> date{};
    std::strftime(date.data(), date.size(), "%a, %d %b %Y %H:%M:%S GMT", &utc);
    out.set(http::field::date, date.data());
    co_await http::async_write(socket_, out, use_awaitable);
    response_done_ = true;
}
awaitable<void> Session::chunk(std::string_view data) {
    // Buffer view stays valid until the awaited complete write has finished.
    co_await asio::async_write(socket_, http::make_chunk(asio::buffer(data.data(), data.size())),
                               use_awaitable);
}
awaitable<void> Session::chunk_end() {
    co_await asio::async_write(socket_, http::make_chunk_last(), use_awaitable);
    response_done_ = true;
}
awaitable<std::size_t> Session::read_body(std::span<char> buffer) {
    if (parser_->is_done())
        co_return 0;
    auto &body = parser_->get().body();
    body.data = buffer.data();
    body.size = buffer.size();
    Error error;
    co_await http::async_read_some(socket_, input_, *parser_,
                                   asio::redirect_error(use_awaitable, error));
    if (error == http::error::need_buffer)
        error = {};
    if (error)
        throw boost::system::system_error(error);
    cancel_idle();
    co_return buffer.size() - body.size;
}
void Session::start_drain() {
    if (parser_->is_done())
        return;
    body_drain_ = true;
    drain_done_ = false;
    arm_read(app_->checked_deadline(request_start_,
                                    std::max(app_->cfg.http_headers_ms, app_->cfg.http_request_ms)),
             true);
    const auto self = shared_from_this();
    asio::co_spawn(socket_.get_executor(), self->drain(), [self](const std::exception_ptr &error) {
        self->drain_done_ = true;
        self->cancel_read();
        self->drain_gate_.cancel();
        if (error)
            self->close();
        else if (self->response_done_)
            self->arm_idle();
    });
}
awaitable<void> Session::drain() {
    std::array<char, 65536> buffer{};
    while (!parser_->is_done())
        co_await read_body(buffer);
}
awaitable<Error> Session::read_head() {
    // Hyper gives Transfer-Encoding precedence over Content-Length. Beast
    // rejects their combination. Normalize the framing ONCE, before parsing,
    // so this connection never has two competing request-body interpretations.
    // Rust's acceptance of this input is recorded by the shared raw client.
    for (;;) {
        const std::string raw = beast::buffers_to_string(input_.data());
        const auto boundary = raw.find("\r\n\r\n");
        if (boundary != std::string::npos) {
            const auto head_size = boundary + 4;
            if (head_size > 417792)
                co_return http::error::header_limit;
            std::vector<std::string_view> lines;
            const std::string_view view(raw.data(), head_size);
            std::size_t at = 0;
            while (at < view.size()) {
                const auto end = view.find("\r\n", at);
                lines.push_back(view.substr(at, end - at));
                at = end + 2;
            }
            bool chunked = false;
            for (std::size_t i = 1; i < lines.size(); ++i) {
                const auto colon = lines[i].find(':');
                if (colon == std::string_view::npos)
                    continue;
                if (lower(std::string(lines[i].substr(0, colon))) == "transfer-encoding" &&
                    token(lines[i].substr(colon + 1), "chunked"))
                    chunked = true;
            }
            std::string normalized;
            for (std::size_t i = 0; i < lines.size(); ++i) {
                const auto colon = lines[i].find(':');
                if (i > 0 && chunked && colon != std::string_view::npos &&
                    lower(std::string(lines[i].substr(0, colon))) == "content-length")
                    continue;
                normalized += lines[i];
                normalized += "\r\n";
            }
            Error error;
            const auto used = parser_->put(asio::buffer(normalized), error);
            if (error == http::error::need_more)
                error = {};
            if (!error && (used != normalized.size() || !parser_->is_header_done()))
                co_return http::error::bad_field;
            input_.consume(head_size);
            co_return error;
        }
        if (input_.size() >= 417792)
            co_return http::error::header_limit;
        Error error;
        const auto n = co_await socket_.async_read_some(input_.prepare(8192),
                                                        asio::redirect_error(use_awaitable, error));
        input_.commit(n);
        if (error)
            co_return error;
    }
}
awaitable<void> Session::run() {
    const auto connected = Clock::now();
    arm_read(app_->checked_deadline(
        connected, std::min(app_->cfg.http_headers_ms, app_->cfg.http_request_ms)));
    while (socket_.is_open() && !stopping_) {
        busy_ = false;
        // Observe first byte separately so idle expiration does not interrupt
        // a request which has started but has not completed its headers.
        if (input_.size() == 0) {
            auto space = input_.prepare(1);
            Error error;
            const auto n =
                co_await socket_.async_read_some(space, asio::redirect_error(use_awaitable, error));
            input_.commit(n);
            if (error && read_timed_out_) {
                static constexpr std::string_view timeout =
                    "HTTP/1.1 408 Request Timeout\r\nConnection: close\r\n\r\n";
                co_await asio::async_write(socket_, asio::buffer(timeout.data(), timeout.size()),
                                           asio::redirect_error(use_awaitable, error));
                co_return;
            }
            if (error || n == 0)
                co_return;
        }
        busy_ = true;
        cancel_idle();
        request_start_ = requests_ == 0 ? connected : Clock::now();
        arm_read(app_->checked_deadline(
            request_start_, std::min(app_->cfg.http_headers_ms, app_->cfg.http_request_ms)));
        parser_ = std::make_unique<http::request_parser<http::buffer_body>>();
        parser_->body_limit(std::numeric_limits<std::uint64_t>::max());
        parser_->header_limit(417792);
        Error error = co_await read_head();
        cancel_read();
        if (error) {
            if (error != http::error::end_of_stream) {
                const std::string raw =
                    read_timed_out_
                        ? "HTTP/1.1 408 Request Timeout\r\nConnection: close\r\n\r\n"
                        : (error == http::error::header_limit
                               ? "HTTP/1.1 431 Request Header Fields Too Large\r\nconnection: "
                                 "close\r\ncontent-length: 0\r\n\r\n"
                               : "HTTP/1.1 400 Bad Request\r\nconnection: close\r\ncontent-length: "
                                 "0\r\n\r\n");
                co_await asio::async_write(socket_, asio::buffer(raw),
                                           asio::redirect_error(use_awaitable, error));
            }
            co_return;
        }
        if (static_cast<std::uint64_t>(
                std::distance(parser_->get().base().begin(), parser_->get().base().end())) >
            app_->cfg.http_max_headers_count) {
            const std::string raw = "HTTP/1.1 431 Request Header Fields Too Large\r\nconnection: "
                                    "close\r\ncontent-length: 0\r\n\r\n";
            co_await asio::async_write(socket_, asio::buffer(raw), use_awaitable);
            co_return;
        }
        target_ = std::string(parser_->get().target());
        path_ = target_.substr(0, target_.find('?'));
        ip_ = client_ip();
        head_ = parser_->get().method() == http::verb::head;
        excess_ = requests_ >= app_->cfg.http_max_requests_per_socket;
        ++requests_;
        const bool limit_close =
            parser_->get().version() == 11 && requests_ >= app_->cfg.http_max_requests_per_socket;
        const bool requested_close = token(header(parser_->get().base(), "connection"), "close");
        close_response_ = limit_close || requested_close || !parser_->get().keep_alive();
        response_done_ = false;
        terminal_response_ = false;
        body_drain_ = false;
        drain_done_ = true;
        if (excess_) {
            // Node/Rust still answer requests after the nth close-marked response.
            http::response<http::empty_body> out{http::status::service_unavailable, 11};
            out.set(http::field::connection, "close");
            out.chunked(true);
            const auto now = std::time(nullptr);
            std::tm utc{};
            gmtime_r(&now, &utc);
            std::array<char, 64> date{};
            std::strftime(date.data(), date.size(), "%a, %d %b %Y %H:%M:%S GMT", &utc);
            out.set(http::field::date, date.data());
            co_await http::async_write(socket_, out, use_awaitable);
            response_done_ = true;
        } else
            co_await route();
        if (!body_drain_ || drain_done_)
            cancel_read();
        cancel_write();
        if (!socket_.is_open())
            co_return;
        // Explicit caller/handler closes are terminal; the socket request limit
        // alone permits Node-compatible pipelined 503 replies.
        if (stopping_ || terminal_response_ || requested_close || (close_response_ && !limit_close))
            co_return;
        arm_idle();
        if (body_drain_ && !drain_done_) {
            // The drainer owns parser/body reads until this gate is signalled.
            drain_gate_.expires_at(Clock::time_point::max());
            Error ignored;
            co_await drain_gate_.async_wait(asio::redirect_error(use_awaitable, ignored));
            if (!socket_.is_open())
                co_return;
        }
    }
}
Response Session::page() {
    if (!assets::web_ui())
        return failure(404, "not found");
    const auto &cfg = app_->cfg;
    const auto nonce = random_hex(18);
    std::set<std::string> sources;
    if (cfg.nodes.is_array())
        for (const auto &node : cfg.nodes)
            for (const auto field : {"base", "directPage", "singleDownloadBase"}) {
                auto it = node.find(field);
                if (it == node.end() || !it->is_string())
                    continue;
                const auto origin = url_origin(it->get<std::string>());
                if (!origin)
                    continue;
                sources.insert(*origin);
                const auto node_id = node.find("id");
                if (std::string_view(field) == "base" && node_id != node.end() &&
                    node_id->is_string() && (*node_id == "hyvps" || *node_id == "primary")) {
                    auto base = *origin;
                    const auto host_at = base.find("://") + 3;
                    const auto port_at =
                        base.find(':', base[host_at] == '[' ? base.find(']', host_at) : host_at);
                    if (port_at != std::string::npos)
                        base.resize(port_at);
                    sources.insert(base + ":8443");
                }
            }
    std::string connect;
    for (const auto &origin : split_words(cfg.csp_compat_origins)) {
        const auto safe = url_origin(origin);
        if (safe && *safe == origin) {
            if (!connect.empty())
                connect += ' ';
            connect += *safe;
        }
    }
    // Rust's optional operational compatibility origins precede node origins.
    if (!connect.empty())
        connect += ' ';
    bool first = true;
    for (const auto &origin : sources) {
        if (!first)
            connect += ' ';
        first = false;
        connect += origin;
    }
    auto nodes = cfg.nodes.dump();
    replace_all(nodes, "<", "\\u003c");
    replace_all(nodes, ">", "\\u003e");
    replace_all(nodes, "&", "\\u0026");
    auto body = head_ ? std::string{} : assets::page();
    for (const auto &[key, value] : std::vector<std::pair<std::string, std::string>>{
             {"__NODE_CONFIG__", nodes},
             {"__CSP_NONCE__", nonce},
             {"__RTC_RESULT_GRACE_MS__", std::to_string(cfg.rtc_result_grace_ms)},
             {"__RTC_CONTROL_SINGLE_MESSAGE_BYTES__", std::to_string(cfg.control_single_bytes)},
             {"__RTC_CONTROL_FRAGMENT_PAYLOAD_BYTES__", std::to_string(cfg.control_fragment_bytes)},
             {"__MAX_RTC_CONTROL_FRAGMENT_COUNT__", std::to_string(cfg.control_fragment_count)},
             {"__MAX_RTC_REASSEMBLED_CONTROL_BYTES__",
              std::to_string(cfg.control_reassembled_bytes)}})
        replace_all(body, key, value);
    auto out = response(200, std::move(body), "text/html; charset=utf-8", "no-store");
    out.set("content-security-policy",
            "default-src 'none'; script-src 'nonce-" + nonce +
                "'; script-src-attr 'none'; style-src-elem 'nonce-" + nonce +
                "'; style-src-attr 'unsafe-inline'; connect-src 'self' " + connect +
                "; worker-src blob:; img-src 'self' data:; font-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'");
    out.set("x-frame-options", "DENY");
    out.set("cross-origin-opener-policy", "same-origin");
    out.set("cross-origin-resource-policy", "same-origin");
    out.set("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()");
    return out;
}
awaitable<void> Session::route() {
    const auto method = parser_->get().method();
    const auto &fields = parser_->get().base();
    const auto &cfg = app_->cfg;
    const auto origin = header(fields, "origin");
    const bool consumes = method == http::verb::post &&
                          (path_is(path_, "/upload") || path_is(path_, "/upload-stream") ||
                           path_is(path_, "/webrtc/offer"));
    if (!consumes)
        start_drain();
    if (probe_only() && speed_path(path_)) {
        co_await send(response(404, "not found", "text/plain; charset=utf-8"));
        co_return;
    }
    if (speed_path(path_) &&
        (cross_site() || (!origin.empty() && !cfg.allowed_origins.contains(origin))))
        co_await send(failure(403, cross_site() ? "cross-site speed data requests are not allowed"
                                                : "speed data request origin is not allowed"));
    else if (method == http::verb::options ||
             ((method == http::verb::get || head_) && path_is(path_, "/latency")))
        co_await send(response(204, {}, {}, "no-store"));
    else if ((method == http::verb::get || head_) && path_is(path_, "/favicon.svg")) {
        if (!assets::web_ui())
            co_await send(failure(404, "not found"));
        else {
            auto out = response(200, head_ ? std::string{} : std::string(assets::icon()),
                                "image/svg+xml; charset=utf-8", "public, max-age=86400");
            out.content_length(assets::icon().size());
            co_await send(std::move(out));
        }
    } else if ((method == http::verb::get || head_) && path_is(path_, "/"))
        co_await send(page());
    else if ((method == http::verb::get || method == http::verb::post) &&
             path_is(path_, "/upload-progress"))
        co_await progress();
    else if (method == http::verb::get &&
             (path_is(path_, "/download") || path_is(path_, "/download-100k") ||
              path_is(path_, "/download-stream")))
        co_await download(path_is(path_, "/download-stream"));
    else if (method == http::verb::post &&
             (path_is(path_, "/upload") || path_is(path_, "/upload-stream")))
        co_await upload(path_is(path_, "/upload-stream"));
    else if (method == http::verb::get && path_is(path_, "/client-info")) {
        if (!app_->limits.rate(ip_, false))
            co_await send(failure(429, "rate limit exceeded"));
        else
            co_await send(json(private_client(ip_) ? Json{{"ip", ip_}, {"private", true}}
                                                   : co_await app_->info->lookup(ip_)));
    } else if (method == http::verb::get && path_is(path_, "/health"))
        co_await send(json(Json{{"ok", true}, {"name", cfg.name}}));
    else if (method == http::verb::post && path_is(path_, "/webrtc/offer"))
        co_await offer();
    else
        co_await send(response(404, "not found", "text/plain; charset=utf-8"));
    // Early rejections must also consume ignored bodies without postponing
    // their response. Parsing the next request waits for the owned drainer.
    if (consumes && !parser_->is_done() && !close_response_)
        start_drain();
}
awaitable<void> Session::download(bool streaming) {
    if (!app_->limits.rate(ip_, true)) {
        co_await send(failure(429, "speed test rate limit exceeded"));
        co_return;
    }
    auto admission = app_->limits.active(ip_);
    if (const auto *error = std::get_if<ActiveError>(&admission)) {
        co_await send(failure(429, active_error(*error)));
        co_return;
    }
    auto lease = std::get<ActiveLease>(std::move(admission));
    const auto &cfg = app_->cfg;
    std::optional<DownloadReservation> reservation;
    std::uint64_t size = 0;
    if (!streaming) {
        const bool small = path_is(path_, "/download-100k");
        size = bounded(query(target_, "size"), {.fallback = small ? 102400 : 96U << 20U,
                                                .minimum = 1024,
                                                .maximum = small ? 1U << 20U : cfg.max_download});
        reservation = app_->limits.reserve_download(ip_, size);
        if (!reservation) {
            co_await send(failure(429, "download byte budget exceeded"));
            co_return;
        }
    }
    auto out = response(200, {}, "application/octet-stream", "no-store, no-transform");
    out.set(http::field::content_encoding, "identity");
    out.set("x-accel-buffering", "no");
    out.set("surrogate-control", "no-store");
    if (streaming)
        out.chunked(true);
    else
        out.content_length(size);
    const auto deadline =
        Clock::now() +
        Millis(streaming ? bounded(query(target_, "maxMs"), {.fallback = cfg.stream_download_ms,
                                                             .minimum = 1000,
                                                             .maximum = 30000})
                         : cfg.fixed_download_ms);
    if (!streaming)
        arm_write(deadline);
    co_await send_head(std::move(out));
    while (streaming ? Clock::now() < deadline : size > 0) {
        const auto count =
            streaming ? chunk_size
                      : static_cast<std::size_t>(std::min<std::uint64_t>(size, chunk_size));
        if (streaming) {
            if (!app_->limits.consume(ip_, count, Direction::download)) {
                close();
                co_return;
            }
            co_await chunk(std::string_view(app_->payload.data(), count));
        } else {
            if (!reservation)
                throw std::logic_error("fixed download reservation missing");
            reservation->produced(count);
            size -= count;
            co_await asio::async_write(socket_, asio::buffer(app_->payload.data(), count),
                                       use_awaitable);
        }
    }
    if (streaming)
        co_await chunk_end();
    else
        response_done_ = true;
    cancel_write();
}
awaitable<void> Session::upload(bool streaming) {
    if (!app_->limits.rate(ip_, true)) {
        co_await send(failure(429, "speed test rate limit exceeded"));
        co_return;
    }
    auto admission = app_->limits.active(ip_);
    if (const auto *error = std::get_if<ActiveError>(&admission)) {
        co_await send(failure(429, active_error(*error)));
        co_return;
    }
    auto lease = std::get<ActiveLease>(std::move(admission));
    std::optional<UploadReceipt> receipt;
    std::optional<Response> rejected;
    if (const auto id = query(target_, "progressId")) {
        const auto raw = query(target_, "lane");
        const auto n = raw ? unsigned_number(*raw) : std::nullopt;
        const auto lane = static_cast<std::uint8_t>(n && *n <= 255 ? *n : 0);
        try {
            receipt.emplace(app_->progress.attach(ip_, *id, lane));
        } catch (const std::invalid_argument &error) {
            rejected = failure(400, error.what());
        }
    }
    if (rejected) {
        co_await send(std::move(*rejected));
        co_return;
    }
    const auto &cfg = app_->cfg;
    const auto start = Clock::now();
    const auto warmup = streaming ? bounded(query(target_, "warmupMs"),
                                            {.fallback = 2000, .minimum = 0, .maximum = 5000})
                                  : 0;
    const auto maximum = streaming ? cfg.max_stream_upload : cfg.max_upload;
    const auto generic =
        app_->checked_deadline(request_start_, std::max(cfg.http_headers_ms, cfg.http_request_ms));
    const auto deadline = std::min(start + Millis(cfg.upload_ms), generic);
    arm_read(deadline);
    co_await continue_body();
    std::optional<Clock::time_point> first, measured_start;
    std::uint64_t bytes = 0, measured = 0;
    std::array<char, 65536> buffer{};
    std::optional<Response> error_response;
    try {
        while (!parser_->is_done()) {
            const auto n = co_await read_body(buffer);
            if (n == 0)
                continue;
            if (!app_->limits.consume(ip_, n, Direction::upload)) {
                error_response = failure(429, "upload byte budget exceeded", true);
                break;
            }
            if (receipt)
                receipt->received(n);
            const auto now = Clock::now();
            if (!first)
                first = now;
            bytes += n;
            if (bytes > maximum) {
                error_response =
                    failure(413, streaming ? "upload stream too large" : "upload too large", true);
                break;
            }
            if (now - *first >= Millis(warmup)) {
                if (!measured_start)
                    measured_start = now;
                measured += n;
            }
        }
    } catch (const boost::system::system_error &) {
        error_response =
            failure(read_timed_out_ ? 408 : 400,
                    read_timed_out_ ? (streaming ? "upload stream timeout" : "upload timeout")
                                    : (streaming ? "upload stream failed" : "upload failed"),
                    read_timed_out_);
    }
    cancel_read();
    if (read_timed_out_ && deadline == generic) {
        co_await request_timeout();
        co_return;
    }
    if (error_response) {
        co_await send(std::move(*error_response));
        co_return;
    }
    if (receipt)
        receipt->success();
    receipt.reset();
    const auto ended = Clock::now();
    auto elapsed = [ended](Clock::time_point t) {
        return std::chrono::duration<double, std::milli>(ended - t).count();
    };
    if (streaming)
        co_await send(
            json(Json{{"bytes", bytes},
                      {"elapsedMs", first ? elapsed(*first) : 0.0},
                      {"measuredBytes", measured},
                      {"measuredElapsedMs", measured_start ? elapsed(*measured_start) : 0.0},
                      {"requestElapsedMs", elapsed(start)},
                      {"warmupMs", warmup}}));
    else
        co_await send(json(Json{{"bytes", bytes}, {"elapsedMs", elapsed(start)}}));
}
awaitable<void> Session::progress() {
    if (!app_->limits.rate(ip_, true)) {
        co_await send(failure(429, "speed test rate limit exceeded"));
        co_return;
    }
    const auto &cfg = app_->cfg;
    const auto id = query(target_, "id");
    if (parser_->get().method() == http::verb::post) {
        std::optional<Response> result;
        if (id) {
            if (query(target_, "finish") != "1")
                result = failure(400, "invalid progress close");
            else {
                try {
                    app_->progress.finish(ip_, *id);
                    result = json(Json{{"closed", true}});
                } catch (const std::invalid_argument &error) {
                    result = failure(400, error.what());
                }
            }
        } else {
            const auto raw = query(target_, "lanes");
            const auto lanes = raw ? unsigned_number(*raw) : std::optional<unsigned>(1);
            if (!lanes || (*lanes != 1 && *lanes != 2 && *lanes != 4 && *lanes != 8))
                result = failure(400, "invalid upload progress lane count");
            else {
                const bool reusable = query(target_, "chunked") == "1";
                const ProgressOptions options{
                    static_cast<std::uint8_t>(*lanes),
                    reusable,
                    bounded(query(target_, "warmupMs"),
                            {.fallback = 2000, .minimum = 0, .maximum = 5000}),
                    Millis(cfg.upload_ms + 10000),
                    static_cast<std::size_t>(cfg.max_active_global),
                    static_cast<std::size_t>(cfg.max_active_per_ip)};
                try {
                    const auto created = app_->progress.create(ip_, options);
                    result = json(Json{{"id", created},
                                       {"version", 1},
                                       {"intervalMs", 100},
                                       {"chunked", reusable}});
                } catch (const std::runtime_error &error) {
                    result = failure(429, error.what());
                }
            }
        }
        co_await send(std::move(result.value()));
        co_return;
    }
    if (!id) {
        co_await send(json(Json{{"version", 1}, {"intervalMs", 100}}));
        co_return;
    }
    const auto record = app_->progress.get({.owner = ip_, .id = *id});
    if (!record) {
        co_await send(failure(404, "upload progress not found"));
        co_return;
    }
    auto admission = app_->limits.active(ip_);
    if (const auto *error = std::get_if<ActiveError>(&admission)) {
        co_await send(failure(429, active_error(*error)));
        co_return;
    }
    auto lease = std::get<ActiveLease>(std::move(admission));
    auto out = response(200, {}, "application/x-ndjson", "no-store, no-transform");
    out.chunked(true);
    out.set(http::field::content_encoding, "identity");
    out.set("x-accel-buffering", "no");
    arm_write(Clock::now() + Millis(cfg.upload_ms + 11000));
    co_await send_head(std::move(out));
    auto next = Clock::now();
    for (;;) {
        const auto value = record->snapshot();
        const auto line = value.dump() + "\n";
        co_await chunk(line);
        if (value["done"] == true || value["expired"] == true)
            break;
        next += Millis(100);
        if (next < Clock::now())
            next = Clock::now() + Millis(100);
        tick_.expires_at(next);
        co_await tick_.async_wait(use_awaitable);
    }
    co_await chunk_end();
    cancel_write();
}
awaitable<void> Session::offer() {
    const auto &fields = parser_->get().base();
    const auto &cfg = app_->cfg;
    const auto origin = header(fields, "origin");
    if (cross_site()) {
        co_await send(failure(403, "cross-site WebRTC requests are not allowed"));
        co_return;
    }
    if (!origin.empty() && !cfg.allowed_origins.contains(origin)) {
        co_await send(failure(403, "WebRTC request origin is not allowed"));
        co_return;
    }
    auto type = header(fields, "content-type");
    if (lower(std::string(trim_text(std::string_view(type).substr(0, type.find(';'))))) !=
        "application/json") {
        co_await send(failure(415, "WebRTC requests require application/json"));
        co_return;
    }
    if (!app_->limits.rate(ip_, false)) {
        co_await send(failure(429, "rate limit exceeded"));
        co_return;
    }
    if (parser_->content_length().value_or(0) > cfg.rtc_offer_body_max) {
        co_await send(failure(413, "WebRTC offer body too large"));
        co_return;
    }
    std::array<char, 65536> buffer{};
    std::string body;
    std::optional<Response> rejected;
    arm_read(
        app_->checked_deadline(request_start_, std::max(cfg.http_headers_ms, cfg.http_request_ms)));
    co_await continue_body();
    try {
        while (!parser_->is_done()) {
            const auto n = co_await read_body(buffer);
            if (n > cfg.rtc_offer_body_max - body.size()) {
                rejected = failure(413, "WebRTC offer body too large");
                break;
            }
            body.append(buffer.data(), n);
        }
    } catch (const boost::system::system_error &) {
        rejected = failure(413, "WebRTC offer body too large");
    }
    cancel_read();
    if (read_timed_out_) {
        co_await request_timeout();
        co_return;
    }
    if (rejected) {
        co_await send(std::move(*rejected));
        co_return;
    }
    Json data;
    try {
        data = parse_json(body);
    } catch (const std::invalid_argument &) {
        rejected = failure(400, "invalid JSON body");
    }
    if (rejected) {
        co_await send(std::move(*rejected));
        co_return;
    }
    auto field = [&data](std::string_view key) -> Json {
        const auto it = data.find(std::string(key));
        return it == data.end() ? Json(nullptr) : *it;
    };
    const auto attempt = header(fields, "x-speedtest-attempt-id");
    const auto body_attempt = field("attemptId");
    auto valid = [](std::string_view id) {
        if (id.empty() || id.size() > 128)
            return false;
        for (std::size_t i = 0; i < id.size(); ++i) {
            const auto c = static_cast<unsigned char>(id[i]);
            if (!std::isalnum(c) &&
                !(i > 0 && std::string_view("._:-").find(id[i]) != std::string_view::npos))
                return false;
        }
        return true;
    };
    if (!valid(attempt) || !body_attempt.is_string() || !valid(body_attempt.get<std::string>())) {
        co_await send(failure(400, "invalid attemptId"));
        co_return;
    }
    if (body_attempt != attempt) {
        co_await send(failure(400, "attemptId header/body mismatch"));
        co_return;
    }
    const bool cancel = field("action") == "cancel";
    if (!cancel && (field("type") != "offer" || !field("sdp").is_string())) {
        co_await send(failure(400, "invalid SDP offer"));
        co_return;
    }
    if (field("sdp").is_string()) {
        try {
            static_cast<void>(sanitize_sdp(field("sdp").get<std::string>()));
        } catch (const SdpFailure &error) {
            rejected = failure(error.kind == SdpError::limit ? 413 : 400,
                               error.kind == SdpError::limit ? "WebRTC SDP exceeds limits"
                                                             : "invalid SDP offer");
        }
    }
    // Native RTC session transport is a separate required milestone. This
    // intermediate HTTP binary fails closed, never proxies or invokes Rust.
    if (!rejected)
        rejected = failure(503, "WebRTC server dependency is unavailable");
    co_await send(std::move(*rejected));
}
} // namespace

struct HttpServer::Impl : std::enable_shared_from_this<HttpServer::Impl> {
    Impl(asio::io_context &io, Config cfg, std::function<void()> stopped)
        : context(io), acceptor(asio::make_strand(io)), grace(acceptor.get_executor()),
          app(std::make_shared<App>(std::move(cfg))), on_stopped(std::move(stopped)) {
        const auto endpoint = Tcp::endpoint(asio::ip::make_address(app->cfg.host),
                                            static_cast<unsigned short>(app->cfg.port));
        acceptor.open(endpoint.protocol());
        acceptor.set_option(Tcp::acceptor::reuse_address(true));
        acceptor.bind(endpoint);
        acceptor.listen(asio::socket_base::max_listen_connections);
    }
    awaitable<void> accept() {
        while (!stopping) {
            Error error;
            auto socket = co_await acceptor.async_accept(
                asio::make_strand(context), asio::redirect_error(use_awaitable, error));
            if (error) {
                if (stopping)
                    co_return;
                throw boost::system::system_error(error);
            }
            if (stopping) {
                observe(socket.close(error));
                co_return;
            }
            if (sessions.size() >= app->cfg.max_http_connections) {
                observe(socket.close(error));
                continue;
            }
            observe(socket.set_option(Tcp::no_delay(true), error));
            const auto id = ++next_id;
            const auto weak = weak_from_this();
            auto session = std::make_shared<Session>(std::move(socket), app, [weak, id] {
                if (auto self = weak.lock())
                    asio::dispatch(self->acceptor.get_executor(), [self, id] {
                        self->sessions.erase(id);
                        self->finish();
                    });
            });
            sessions.emplace(id, session);
            session->start();
        }
    }
    void stop() {
        const auto self = shared_from_this();
        asio::dispatch(acceptor.get_executor(), [self] {
            if (self->stopping)
                return;
            self->stopping = true;
            self->app->info->shutdown();
            Error ignored;
            observe(self->acceptor.close(ignored));
            for (const auto &[id, session] : self->sessions) {
                static_cast<void>(id);
                session->shutdown(false);
            }
            if (self->sessions.empty()) {
                self->finish();
                return;
            }
            self->grace.expires_after(std::chrono::seconds(5));
            self->grace.async_wait([self](Error error) {
                if (!error)
                    for (const auto &[id, session] : self->sessions) {
                        static_cast<void>(id);
                        session->shutdown(true);
                    }
            });
        });
    }
    void finish() {
        if (stopping && sessions.empty()) {
            grace.cancel();
            if (on_stopped)
                std::exchange(on_stopped, {})();
        }
    }
    asio::io_context &context;
    Tcp::acceptor acceptor;
    asio::steady_timer grace;
    std::shared_ptr<App> app;
    std::map<std::uint64_t, std::shared_ptr<Session>> sessions;
    std::uint64_t next_id{};
    bool stopping{};
    std::atomic<bool> failed{};
    std::function<void()> on_stopped;
};
HttpServer::HttpServer(asio::io_context &io, Config cfg, std::function<void()> stopped)
    : impl_(std::make_shared<Impl>(io, std::move(cfg), std::move(stopped))) {}
HttpServer::~HttpServer() = default;
void HttpServer::start() {
    const auto impl = impl_;
    asio::co_spawn(impl->acceptor.get_executor(), impl->accept(),
                   [impl](const std::exception_ptr &error) {
                       if (error) {
                           impl->failed = true;
                           std::clog << "{\"event\":\"http_accept_failed\"}\n";
                           impl->stop();
                       }
                   });
}
void HttpServer::stop() {
    impl_->stop();
}
bool HttpServer::failed() const {
    return impl_->failed;
}
} // namespace speedtest
