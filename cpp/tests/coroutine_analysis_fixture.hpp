#pragma once
#include <boost/asio/awaitable.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/beast/http.hpp>
#include <memory>

namespace speedtest::analysis_fixture {
namespace asio = boost::asio;
namespace http = boost::beast::http;
using Response = http::response<http::string_body>;

// A test fixture, not another HTTP server. A closed socket intentionally returns
// bad_descriptor, exercising nested awaitables and asynchronous error unwinding.
// The real co_spawn entry/completion owner is in a separate translation unit so
// the analyzer also examines these coroutine bodies as standalone entries.
class Session : public std::enable_shared_from_this<Session> {
  public:
    Session(asio::io_context &io, bool timeout) : socket_(io), timeout_(timeout) {}
    void start();
    [[nodiscard]] bool passed() const noexcept {
        return passed_;
    }

  private:
    asio::awaitable<void> run();
    asio::awaitable<void> send(Response out);
    asio::awaitable<void> request_timeout();
    asio::ip::tcp::socket socket_;
    const bool timeout_;
    bool passed_{};
};
} // namespace speedtest::analysis_fixture
