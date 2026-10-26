#pragma once
#include "config.hpp"
#include <chrono>
#include <deque>
#include <memory>
#include <unordered_map>
#include <variant>

namespace speedtest {
using Clock = std::chrono::steady_clock;
[[nodiscard]] std::uint64_t wall_time_ms();
enum class RateKind { standard, cancel };
struct RateOptions {
    std::uint32_t maximum;
    std::size_t map_maximum;
    RateKind kind = RateKind::standard;
};
class RateTable {
  public:
    [[nodiscard]] bool check(std::string_view ip, RateOptions options,
                             Clock::time_point now = Clock::now());
    [[nodiscard]] std::size_t size() const {
        return entries_.size();
    }

  private:
    struct Window {
        Clock::time_point start;
        std::uint64_t count{};
    };
    std::unordered_map<std::string, Window> entries_;
    std::deque<std::string> order_;
};
struct LimitState;
enum class Direction { download, upload };
enum class ActiveError { global, per_ip };
[[nodiscard]] std::string_view active_error(ActiveError error);
class ActiveLease {
  public:
    ActiveLease(const ActiveLease &) = delete;
    ActiveLease &operator=(const ActiveLease &) = delete;
    ActiveLease(ActiveLease &&) noexcept = default;
    ActiveLease &operator=(ActiveLease &&other) noexcept;
    ~ActiveLease();

  private:
    friend class Limits;
    ActiveLease(std::shared_ptr<LimitState> state, std::string ip)
        : state_(std::move(state)), ip_(std::move(ip)) {}
    void release();
    std::shared_ptr<LimitState> state_;
    std::string ip_;
};
class DownloadReservation {
  public:
    DownloadReservation(const DownloadReservation &) = delete;
    DownloadReservation &operator=(const DownloadReservation &) = delete;
    DownloadReservation(DownloadReservation &&other) noexcept;
    DownloadReservation &operator=(DownloadReservation &&other) noexcept;
    ~DownloadReservation();
    void produced(std::uint64_t bytes);
    [[nodiscard]] std::uint64_t remaining() const {
        return remaining_;
    }

  private:
    friend class Limits;
    struct Credit {
        std::uint64_t bucket, bytes;
    };
    DownloadReservation(std::shared_ptr<LimitState> state, std::string ip, Credit credit)
        : state_(std::move(state)), ip_(std::move(ip)), bucket_(credit.bucket),
          remaining_(credit.bytes) {}
    void refund();
    std::shared_ptr<LimitState> state_;
    std::string ip_;
    std::uint64_t bucket_{}, remaining_{};
};
class Limits {
  public:
    explicit Limits(const Config &cfg);
    [[nodiscard]] bool rate(std::string_view ip, bool speed);
    [[nodiscard]] std::variant<ActiveLease, ActiveError> active(std::string_view ip);
    [[nodiscard]] bool consume(std::string_view ip, std::uint64_t bytes, Direction direction);
    [[nodiscard]] std::optional<DownloadReservation> reserve_download(std::string_view ip,
                                                                      std::uint64_t bytes);
    [[nodiscard]] std::size_t active_count() const;

  private:
    std::shared_ptr<LimitState> state_;
};
} // namespace speedtest
