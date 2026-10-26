#include "coroutine_analysis_fixture.hpp"
#include <array>
#include <boost/asio/use_awaitable.hpp>
#include <ctime>

namespace speedtest::analysis_fixture {
asio::awaitable<void> Session::request_timeout() {
    // Same awaited Beast operation and owned Date header as the real timeout
    // path. Every view/buffer remains owned by this suspended coroutine frame.
    http::response<http::empty_body> out{http::status::request_timeout, 11};
    out.content_length(0);
    out.set(http::field::connection, "close");
    const auto now = std::time(nullptr);
    std::tm utc{};
    gmtime_r(&now, &utc);
    std::array<char, 64> date{};
    std::strftime(date.data(), date.size(), "%a, %d %b %Y %H:%M:%S GMT", &utc);
    out.set(http::field::date, date.data());
    co_await http::async_write(socket_, out, asio::use_awaitable);
}

asio::awaitable<void> Session::run() {
    if (timeout_) {
        co_await request_timeout();
        co_return;
    }
    co_await send(Response{http::status::too_many_requests, 11});
}
} // namespace speedtest::analysis_fixture
