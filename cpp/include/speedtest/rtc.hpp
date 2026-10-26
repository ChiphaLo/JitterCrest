#pragma once
#include "config.hpp"
#include <boost/asio/any_io_executor.hpp>
#include <boost/asio/awaitable.hpp>
#include <boost/asio/io_context.hpp>
#include <memory>
#include <stdexcept>

namespace speedtest {
struct MeasurementIdentity {
    std::string test_id, session_id;
    std::uint64_t planned{};
};
[[nodiscard]] Json json_field(const Json &value, std::string_view key);
[[nodiscard]] std::optional<std::uint64_t> rtc_number(const Json &value);
[[nodiscard]] std::vector<std::string> upload_complete_reasons(const Json &value,
                                                               const MeasurementIdentity &identity,
                                                               bool prior_invalid = false);

class RtcFailure : public std::runtime_error {
  public:
    RtcFailure(unsigned code, std::string_view message)
        : std::runtime_error(std::string(message)), status(code) {}
    unsigned status;
};
// One process-wide native library lifetime, destroyed only after the HTTP/RTC
// services and their owning executor have drained. Never shared with Rust.
class RtcRuntime {
  public:
    explicit RtcRuntime(bool enabled, unsigned workers);
    ~RtcRuntime();
    void finish(boost::asio::io_context &io);
    RtcRuntime(const RtcRuntime &) = delete;
    RtcRuntime &operator=(const RtcRuntime &) = delete;

  private:
    bool enabled_;
};
class RtcService {
  public:
    RtcService(const boost::asio::any_io_executor &executor, Config cfg);
    ~RtcService();
    [[nodiscard]] boost::asio::awaitable<Json> offer(std::string ip, std::string attempt,
                                                     std::string sdp, bool probe_only);
    [[nodiscard]] boost::asio::awaitable<void> cancel(std::string ip, std::string attempt);
    // Completion is posted back to its caller's executor after cancellation.
    [[nodiscard]] boost::asio::awaitable<void> shutdown();

  private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
} // namespace speedtest
