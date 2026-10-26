#pragma once
#include "limits.hpp"
#include <atomic>
#include <boost/asio/awaitable.hpp>
#include <boost/asio/thread_pool.hpp>
#include <mutex>
#include <unordered_map>

namespace speedtest {
class CurlRuntime {
  public:
    CurlRuntime();
    ~CurlRuntime();
    CurlRuntime(const CurlRuntime &) = delete;
    CurlRuntime &operator=(const CurlRuntime &) = delete;
};
[[nodiscard]] std::optional<std::string> url_origin(std::string_view url);
class ClientInfo : public std::enable_shared_from_this<ClientInfo> {
  public:
    using Fetch = std::function<Json(const std::string &, long)>;
    explicit ClientInfo(Fetch provider = {});
    [[nodiscard]] boost::asio::awaitable<Json> lookup(std::string ip);
    void shutdown() {
        stopping_ = true;
    }
    void join() {
        // The executor context must outlive all producer calls, not merely
        // its last completion handler. Join before destroying that context.
        pool_.join();
    }

  private:
    [[nodiscard]] Json lookup_sync(const std::string &ip);
    const Fetch provider_;
    boost::asio::thread_pool pool_{4};
    std::atomic<unsigned> in_flight_{};
    std::atomic<bool> stopping_{};
    std::mutex mutex_;
    struct Entry {
        Clock::time_point at;
        Json body;
    };
    std::unordered_map<std::string, Entry> cache_;
    std::deque<std::string> order_;
};
} // namespace speedtest
