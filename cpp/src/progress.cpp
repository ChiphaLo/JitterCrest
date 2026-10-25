#include "speedtest/progress.hpp"
#include <algorithm>
#include <utility>

namespace speedtest {
std::uint64_t UploadProgress::micros() const {
    return static_cast<std::uint64_t>(
               std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - born_)
                   .count()) +
           1;
}
bool UploadProgress::done_locked() const {
    return finished_ == (1U << options_.lanes) - 1;
}
bool UploadProgress::expired_locked() const {
    return Clock::now() - born_ >= options_.ttl || (done_locked() && micros() - end_ > 5000000);
}
bool UploadProgress::expired() const {
    const std::lock_guard guard(mutex_);
    return expired_locked();
}
Json UploadProgress::snapshot() const {
    const std::lock_guard guard(mutex_);
    const bool done = done_locked();
    const auto now = done ? end_ : micros();
    const auto elapsed = [now](std::uint64_t start) {
        return start == 0 ? 0.0 : static_cast<double>(now > start ? now - start : 0) / 1000.;
    };
    return Json{{"bytes", bytes_},
                {"measuredBytes", std::min(bytes_, measured_)},
                {"elapsedMs", elapsed(first_)},
                {"measuredElapsedMs", elapsed(measured_start_)},
                {"done", done},
                {"failed", failed_},
                {"expired", Clock::now() - born_ >= options_.ttl}};
}
std::string ProgressRegistry::create(std::string owner, ProgressOptions options) {
    if (options.lanes != 1 && options.lanes != 2 && options.lanes != 4 && options.lanes != 8)
        throw std::invalid_argument("invalid upload progress lane count");
    std::string id;
    try {
        id = random_hex(16);
    } catch (const std::exception &) {
        throw std::runtime_error("progress entropy unavailable");
    }
    const std::lock_guard guard(mutex_);
    std::erase_if(records_, [](const auto &entry) { return entry.second->expired(); });
    const auto count = std::count_if(records_.begin(), records_.end(), [&owner](const auto &entry) {
        return entry.second->owner_ == owner;
    });
    if (records_.size() >= options.maximum || static_cast<std::size_t>(count) >= options.per_ip)
        throw std::runtime_error("upload progress capacity exceeded");
    if (records_.contains(id))
        throw std::runtime_error("upload progress identifier collision");
    // Private constructor keeps creation behind validated registry admission.
    // Allocate an owned value via a local derived enabler, not a raw owner pointer.
    struct Owned final : UploadProgress {
        Owned(std::string key, ProgressOptions opts) : UploadProgress(std::move(key), opts) {}
    };
    records_.emplace(id, std::make_shared<Owned>(std::move(owner), options));
    return id;
}
std::shared_ptr<UploadProgress> ProgressRegistry::get(ProgressKey key) const {
    const auto [owner, id] = key;
    if (id.size() != 32 || !std::all_of(id.begin(), id.end(), [](unsigned char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
        }))
        return {};
    const std::lock_guard guard(mutex_);
    const auto it = records_.find(std::string(id));
    if (it == records_.end() || it->second->owner_ != owner || it->second->expired())
        return {};
    return it->second;
}
UploadReceipt ProgressRegistry::attach(std::string_view owner, std::string_view id,
                                       std::uint8_t lane) {
    auto p = get({.owner = owner, .id = id});
    if (!p)
        throw std::invalid_argument("upload progress not found");
    const std::lock_guard guard(p->mutex_);
    if (lane >= p->options_.lanes)
        throw std::invalid_argument("invalid upload progress lane");
    const auto bit = static_cast<std::uint16_t>(1U << lane);
    if (p->options_.reusable) {
        if (p->closing_ || p->done_locked())
            throw std::invalid_argument("upload progress is closed");
        if (p->active_ & bit)
            throw std::invalid_argument("upload progress lane is active");
        p->active_ |= bit;
        p->started_ |= bit;
    } else {
        if (p->started_ & bit)
            throw std::invalid_argument("upload progress lane already attached");
        p->started_ |= bit;
    }
    return {std::move(p), bit};
}
void ProgressRegistry::finish(std::string_view owner, std::string_view id) {
    auto p = get({.owner = owner, .id = id});
    if (!p)
        throw std::invalid_argument("upload progress not found");
    const std::lock_guard guard(p->mutex_);
    if (p->options_.reusable && p->done_locked())
        return;
    if (!p->options_.reusable || p->closing_)
        throw std::invalid_argument("upload progress cannot close");
    p->closing_ = true;
    const auto mask = static_cast<std::uint16_t>((1U << p->options_.lanes) - 1);
    if (p->active_ != 0 || p->started_ != mask) {
        p->closing_ = false;
        throw std::invalid_argument("upload progress lanes are not finished");
    }
    p->end_ = std::max(p->end_, p->micros());
    p->finished_ = mask;
}
void UploadReceipt::received(std::size_t bytes) {
    if (bytes == 0 || !progress_)
        return;
    auto &p = *progress_;
    const std::lock_guard guard(p.mutex_);
    const auto now = p.micros();
    if (!p.first_)
        p.first_ = now;
    p.bytes_ += bytes;
    if (now - p.first_ >= p.options_.warmup_ms * 1000) {
        p.measured_ += bytes;
        if (!p.measured_start_)
            p.measured_start_ = now;
    }
}
UploadReceipt &UploadReceipt::operator=(UploadReceipt &&other) noexcept {
    if (this != &other) {
        finish();
        progress_ = std::move(other.progress_);
        bit_ = other.bit_;
        success_ = other.success_;
    }
    return *this;
}
UploadReceipt::~UploadReceipt() {
    finish();
}
void UploadReceipt::finish() {
    if (!progress_)
        return;
    {
        auto &p = *progress_;
        const std::lock_guard guard(p.mutex_);
        if (!success_)
            p.failed_ = true;
        p.end_ = std::max(p.end_, p.micros());
        if (p.options_.reusable)
            p.active_ &= static_cast<std::uint16_t>(~bit_);
        else
            p.finished_ |= bit_;
    }
    progress_.reset();
}
} // namespace speedtest
