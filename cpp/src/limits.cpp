#include "speedtest/limits.hpp"
#include <algorithm>
#include <limits>
#include <mutex>
#include <utility>

namespace speedtest {
std::uint64_t wall_time_ms() {
    const auto n = std::chrono::duration_cast<std::chrono::milliseconds>(
                       std::chrono::system_clock::now().time_since_epoch())
                       .count();
    return n > 0 ? static_cast<std::uint64_t>(n) : 0;
}
bool RateTable::check(std::string_view ip, RateOptions o, Clock::time_point now) {
    const std::string key(ip);
    if (!entries_.contains(key)) {
        if (entries_.size() >= o.map_maximum) {
            auto stale = order_.end();
            if (o.kind == RateKind::standard)
                stale = std::find_if(order_.begin(), order_.end(), [&](const auto &k) {
                    return now - entries_.at(k).start >= std::chrono::seconds(60);
                });
            if (stale == order_.end())
                stale = order_.begin();
            if (stale != order_.end()) {
                entries_.erase(*stale);
                order_.erase(stale);
            }
        }
        entries_.emplace(key, Window{now, 0});
        order_.push_back(key);
    }
    auto &window = entries_.at(key);
    const auto elapsed = now - window.start;
    if (o.kind == RateKind::cancel ? elapsed >= std::chrono::seconds(10)
                                   : elapsed > std::chrono::seconds(10))
        window = {now, 0};
    if (window.count < std::numeric_limits<std::uint64_t>::max())
        ++window.count;
    return window.count <= o.maximum;
}
namespace {
struct Budget {
    struct Credit {
        std::uint64_t at, bytes;
    };
    std::deque<std::pair<std::uint64_t, std::uint64_t>> entries;
    std::uint64_t total{};
    void prune(std::uint64_t cutoff) {
        for (auto it = entries.begin(); it != entries.end();) {
            if (it->first <= cutoff) {
                total -= it->second;
                it = entries.erase(it);
            } else
                ++it;
        }
    }
    std::uint64_t add(Credit credit) {
        const auto [now, bytes] = credit;
        const auto bucket = now / 1000 * 1000;
        auto it = std::find_if(entries.begin(), entries.end(),
                               [bucket](const auto &e) { return e.first == bucket; });
        if (it == entries.end())
            entries.emplace_back(bucket, bytes);
        else
            it->second += bytes;
        total += bytes;
        return bucket;
    }
};
struct BudgetTable {
    std::unordered_map<std::string, Budget> entries;
    std::deque<std::string> order;
    void remove(const std::string &key) {
        entries.erase(key);
        std::erase(order, key);
    }
};
} // namespace
struct LimitState {
    explicit LimitState(const Config &c)
        : rate_max(c.rate_max), speed_rate_max(c.speed_rate_max), map_max(c.rate_map_max),
          max_global(c.max_active_global), max_ip(c.max_active_per_ip),
          download_max(c.download_budget), upload_max(c.upload_budget), window(c.budget_window_ms) {
    }
    mutable std::mutex mutex;
    const std::uint64_t rate_max, speed_rate_max, map_max, max_global, max_ip, download_max,
        upload_max, window;
    RateTable general, speed;
    BudgetTable download, upload;
    std::unordered_map<std::string, std::size_t> active;
    std::size_t global{};
    std::optional<std::uint64_t> reserve(const std::string &ip, std::uint64_t bytes,
                                         Direction direction) {
        auto &table = direction == Direction::download ? download : upload;
        const auto maximum = direction == Direction::download ? download_max : upload_max;
        const auto now = wall_time_ms(), cutoff = now > window ? now - window : 0;
        auto it = table.entries.find(ip);
        if (it != table.entries.end()) {
            it->second.prune(cutoff);
            if (bytes > maximum - it->second.total) {
                if (it->second.total == 0)
                    table.remove(ip);
                return std::nullopt;
            }
        } else if (bytes > maximum)
            return std::nullopt;
        if (!table.entries.contains(ip)) {
            if (table.entries.size() >= map_max && !table.order.empty()) {
                table.entries.erase(table.order.front());
                table.order.pop_front();
            }
            table.order.push_back(ip);
        }
        return table.entries[ip].add({.at = now, .bytes = bytes});
    }
};
std::string_view active_error(ActiveError e) {
    return e == ActiveError::global ? "too many global active connections"
                                    : "too many active connections";
}
Limits::Limits(const Config &cfg) : state_(std::make_shared<LimitState>(cfg)) {}
bool Limits::rate(std::string_view ip, bool speed) {
    const std::lock_guard guard(state_->mutex);
    auto &table = speed ? state_->speed : state_->general;
    return table.check(ip, {.maximum = static_cast<std::uint32_t>(speed ? state_->speed_rate_max
                                                                        : state_->rate_max),
                            .map_maximum = static_cast<std::size_t>(state_->map_max)});
}
std::variant<ActiveLease, ActiveError> Limits::active(std::string_view ip) {
    const std::lock_guard guard(state_->mutex);
    if (state_->global >= state_->max_global)
        return ActiveError::global;
    const std::string key(ip);
    auto &count = state_->active[key];
    if (count >= state_->max_ip)
        return ActiveError::per_ip;
    ++count;
    ++state_->global;
    return ActiveLease(state_, key);
}
bool Limits::consume(std::string_view ip, std::uint64_t bytes, Direction direction) {
    const std::lock_guard guard(state_->mutex);
    return state_->reserve(std::string(ip), bytes, direction).has_value();
}
std::optional<DownloadReservation> Limits::reserve_download(std::string_view ip,
                                                            std::uint64_t bytes) {
    const std::lock_guard guard(state_->mutex);
    auto bucket = state_->reserve(std::string(ip), bytes, Direction::download);
    if (!bucket)
        return std::nullopt;
    return DownloadReservation(state_, std::string(ip), {.bucket = bucket.value(), .bytes = bytes});
}
std::size_t Limits::active_count() const {
    const std::lock_guard guard(state_->mutex);
    return state_->global;
}
void ActiveLease::release() {
    if (!state_)
        return;
    {
        const std::lock_guard guard(state_->mutex);
        --state_->global;
        auto it = state_->active.find(ip_);
        if (it != state_->active.end() && --it->second == 0)
            state_->active.erase(it);
    }
    state_.reset();
}
ActiveLease::~ActiveLease() {
    release();
}
ActiveLease &ActiveLease::operator=(ActiveLease &&other) noexcept {
    if (this != &other) {
        release();
        state_ = std::move(other.state_);
        ip_ = std::move(other.ip_);
    }
    return *this;
}
void DownloadReservation::produced(std::uint64_t bytes) {
    if (bytes > remaining_)
        throw std::logic_error("download reservation underflow");
    remaining_ -= bytes;
}
DownloadReservation::DownloadReservation(DownloadReservation &&other) noexcept
    : state_(std::move(other.state_)), ip_(std::move(other.ip_)), bucket_(other.bucket_),
      remaining_(std::exchange(other.remaining_, 0)) {}
DownloadReservation &DownloadReservation::operator=(DownloadReservation &&other) noexcept {
    if (this != &other) {
        refund();
        state_ = std::move(other.state_);
        ip_ = std::move(other.ip_);
        bucket_ = other.bucket_;
        remaining_ = std::exchange(other.remaining_, 0);
    }
    return *this;
}
DownloadReservation::~DownloadReservation() {
    refund();
}
void DownloadReservation::refund() {
    if (!state_)
        return;
    {
        const std::lock_guard guard(state_->mutex);
        const auto now = wall_time_ms(), cutoff = now > state_->window ? now - state_->window : 0;
        auto it = state_->download.entries.find(ip_);
        if (remaining_ && bucket_ > cutoff && it != state_->download.entries.end()) {
            auto &b = it->second;
            auto entry = std::find_if(b.entries.begin(), b.entries.end(),
                                      [this](const auto &e) { return e.first == bucket_; });
            if (entry != b.entries.end()) {
                const auto n = std::min({remaining_, b.total, entry->second});
                b.total -= n;
                entry->second -= n;
                if (entry->second == 0)
                    b.entries.erase(entry);
                if (b.total == 0)
                    state_->download.remove(ip_);
            }
        }
    }
    remaining_ = 0;
    state_.reset();
}
} // namespace speedtest
