#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <nlohmann/json.hpp>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace speedtest {
using Json = nlohmann::ordered_json;
// Match serde_json's strict UTF-8 JSON and bounded recursion on untrusted input.
[[nodiscard]] Json parse_json(std::string_view input);
inline constexpr std::size_t frame_bytes = 128;
struct Frame {
    std::uint8_t kind{}, flags{};
    std::uint32_t session_hash{}, test_hash{}, seq{};
    double sent_at{};
    std::uint32_t actual_sent{};
    [[nodiscard]] static std::optional<Frame> decode(std::span<const std::byte> input);
    [[nodiscard]] std::array<std::byte, frame_bytes> encode() const;
};
[[nodiscard]] std::uint32_t hash_string32(std::string_view utf8);
[[nodiscard]] std::optional<double> percentile(std::span<const double> values, double p);
[[nodiscard]] std::uint64_t required_sample_count(std::uint64_t planned, double density);
struct Accounting {
    std::uint64_t planned{}, scheduled{}, skipped{}, actual_sent{};
    [[nodiscard]] bool valid() const;
};
[[nodiscard]] Json pacing(std::span<const double> times, double target);
[[nodiscard]] Json executor_delay(std::span<const double> delays);
[[nodiscard]] std::string random_hex(std::size_t bytes);
[[nodiscard]] std::string base64_encode(std::string_view input);
[[nodiscard]] std::string base64_decode(std::string_view input);
} // namespace speedtest
