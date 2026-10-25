#pragma once
#include "limits.hpp"
#include <map>
#include <mutex>

namespace speedtest {
struct ProgressKey {
    std::string_view owner, id;
};
struct ProgressOptions {
    std::uint8_t lanes = 1;
    bool reusable = false;
    std::uint64_t warmup_ms = 2000;
    std::chrono::milliseconds ttl{35000};
    std::size_t maximum = 128, per_ip = 16;
};
class UploadProgress {
  public:
    [[nodiscard]] Json snapshot() const;
    [[nodiscard]] bool expired() const;

  private:
    friend class ProgressRegistry;
    friend class UploadReceipt;
    UploadProgress(std::string owner, ProgressOptions options)
        : owner_(std::move(owner)), options_(options), born_(Clock::now()) {}
    [[nodiscard]] std::uint64_t micros() const;
    [[nodiscard]] bool done_locked() const;
    [[nodiscard]] bool expired_locked() const;
    std::string owner_;
    const ProgressOptions options_;
    const Clock::time_point born_;
    mutable std::mutex mutex_;
    std::uint16_t active_{}, started_{}, finished_{};
    bool closing_{}, failed_{};
    std::uint64_t first_{}, measured_start_{}, end_{}, bytes_{}, measured_{};
};
class UploadReceipt {
  public:
    UploadReceipt(const UploadReceipt &) = delete;
    UploadReceipt &operator=(const UploadReceipt &) = delete;
    UploadReceipt(UploadReceipt &&) noexcept = default;
    UploadReceipt &operator=(UploadReceipt &&other) noexcept;
    ~UploadReceipt();
    void received(std::size_t bytes);
    void success() {
        success_ = true;
    }

  private:
    friend class ProgressRegistry;
    UploadReceipt(std::shared_ptr<UploadProgress> progress, std::uint16_t bit)
        : progress_(std::move(progress)), bit_(bit) {}
    void finish();
    std::shared_ptr<UploadProgress> progress_;
    std::uint16_t bit_{};
    bool success_{};
};
class ProgressRegistry {
  public:
    [[nodiscard]] std::string create(std::string owner, ProgressOptions options);
    [[nodiscard]] std::shared_ptr<UploadProgress> get(ProgressKey key) const;
    [[nodiscard]] UploadReceipt attach(std::string_view owner, std::string_view id,
                                       std::uint8_t lane);
    void finish(std::string_view owner, std::string_view id);

  private:
    mutable std::mutex mutex_;
    std::map<std::string, std::shared_ptr<UploadProgress>> records_;
};
} // namespace speedtest
