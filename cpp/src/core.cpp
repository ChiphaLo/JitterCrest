#include "speedtest/core.hpp"
#include <algorithm>
#include <bit>
#include <cmath>
#include <limits>
#include <numeric>
#include <openssl/rand.h>
#include <stdexcept>

namespace speedtest {
Json parse_json(std::string_view input) {
    if (input.starts_with("\xef\xbb\xbf"))
        throw std::invalid_argument("invalid JSON body");
    try {
        return Json::parse(input, [](int depth, Json::parse_event_t event, Json &) {
            // serde_json's default 128-unit recursion budget rejects the
            // container that would consume its last remaining unit.
            if (depth >= 127 && (event == Json::parse_event_t::object_start ||
                                 event == Json::parse_event_t::array_start))
                throw std::invalid_argument("JSON nesting limit");
            return true;
        });
    } catch (const Json::exception &) {
        throw std::invalid_argument("invalid JSON body");
    }
}
namespace {
template <typename T> T read_le(std::span<const std::byte> input, std::size_t offset) {
    T value{};
    for (std::size_t i = 0; i < sizeof(T); ++i)
        value |= static_cast<T>(std::to_integer<unsigned char>(input[offset + i])) << (i * 8);
    return value;
}
template <typename T> void write_le(std::span<std::byte> out, std::size_t offset, T value) {
    for (std::size_t i = 0; i < sizeof(T); ++i)
        out[offset + i] = static_cast<std::byte>((value >> (i * 8)) & 255U);
}
std::uint64_t saturating_integer(double value) {
    if (std::isnan(value) || value <= 0)
        return 0;
    if (static_cast<long double>(value) >=
        static_cast<long double>(std::numeric_limits<std::uint64_t>::max()))
        return std::numeric_limits<std::uint64_t>::max();
    return static_cast<std::uint64_t>(value);
}
Json nullable(std::optional<double> value) {
    return value ? Json(*value) : Json(nullptr);
}
constexpr std::string_view alphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
} // namespace

std::optional<Frame> Frame::decode(std::span<const std::byte> input) {
    if (input.size() != frame_bytes)
        return std::nullopt;
    return Frame{std::to_integer<std::uint8_t>(input[0]),
                 std::to_integer<std::uint8_t>(input[1]),
                 read_le<std::uint32_t>(input, 4),
                 read_le<std::uint32_t>(input, 8),
                 read_le<std::uint32_t>(input, 12),
                 std::bit_cast<double>(read_le<std::uint64_t>(input, 16)),
                 read_le<std::uint32_t>(input, 24)};
}
std::array<std::byte, frame_bytes> Frame::encode() const {
    std::array<std::byte, frame_bytes> output{};
    output[0] = static_cast<std::byte>(kind);
    output[1] = static_cast<std::byte>(flags);
    write_le(output, 4, session_hash);
    write_le(output, 8, test_hash);
    write_le(output, 12, seq);
    write_le(output, 16, std::bit_cast<std::uint64_t>(sent_at));
    write_le(output, 24, actual_sent);
    return output;
}
std::uint32_t hash_string32(std::string_view text) {
    std::uint32_t hash = 0x811c9dc5U;
    const auto mix = [&hash](std::uint32_t unit) { hash = (hash ^ unit) * 0x01000193U; };
    for (std::size_t at = 0; at < text.size();) {
        const auto first = static_cast<unsigned char>(text[at++]);
        std::uint32_t code = first;
        std::size_t count = 0;
        if (first >= 0xf0U && first <= 0xf4U) {
            code &= 7U;
            count = 3;
        } else if (first >= 0xe0U && first <= 0xefU) {
            code &= 15U;
            count = 2;
        } else if (first >= 0xc2U && first <= 0xdfU) {
            code &= 31U;
            count = 1;
        } else if (first >= 0x80U)
            throw std::invalid_argument("invalid UTF-8");
        if (count > text.size() - at)
            throw std::invalid_argument("truncated UTF-8");
        for (std::size_t i = 0; i < count; ++i) {
            const auto ch = static_cast<unsigned char>(text[at++]);
            if ((ch & 0xc0U) != 0x80U)
                throw std::invalid_argument("invalid UTF-8 continuation");
            code = (code << 6U) | (ch & 63U);
        }
        if ((count == 1 && code < 0x80U) || (count == 2 && code < 0x800U) ||
            (count == 3 && code < 0x10000U) || code > 0x10ffffU ||
            (code >= 0xd800U && code <= 0xdfffU))
            throw std::invalid_argument("invalid UTF-8 scalar");
        if (code < 0x10000U)
            mix(code);
        else {
            code -= 0x10000U;
            mix(0xd800U + (code >> 10U));
            mix(0xdc00U + (code & 1023U));
        }
    }
    return hash;
}
std::optional<double> percentile(std::span<const double> values, double p) {
    std::vector<double> finite;
    finite.reserve(values.size());
    for (const auto value : values)
        if (std::isfinite(value))
            finite.push_back(value);
    if (finite.empty())
        return std::nullopt;
    std::sort(finite.begin(), finite.end(), [](double a, double b) {
        return a < b || (a == 0 && b == 0 && std::signbit(a) && !std::signbit(b));
    });
    const auto index = saturating_integer(std::floor(static_cast<double>(finite.size() - 1) * p));
    return finite[std::min<std::uint64_t>(index, finite.size() - 1)];
}
std::uint64_t required_sample_count(std::uint64_t planned, double density) {
    return saturating_integer(std::ceil(static_cast<double>(planned) * density));
}
bool Accounting::valid() const {
    return planned > 0 && skipped <= std::numeric_limits<std::uint64_t>::max() - scheduled &&
           scheduled + skipped == planned && actual_sent <= scheduled;
}
Json pacing(std::span<const double> times, double target) {
    std::vector<double> intervals, drifts;
    for (std::size_t i = 1; i < times.size(); ++i) {
        const double value = times[i] - times[i - 1];
        if (std::isfinite(value) && value >= 0) {
            intervals.push_back(value);
            drifts.push_back(std::abs(value - target));
        }
    }
    if (intervals.empty())
        return nullptr;
    const auto bad = std::count_if(drifts.begin(), drifts.end(),
                                   [target](double d) { return d > std::max(target * 2, 20.0); });
    return Json{{"targetMs", target},
                {"samples", intervals.size()},
                {"p50Interval", nullable(percentile(intervals, .5))},
                {"p95Interval", nullable(percentile(intervals, .95))},
                {"p99Interval", nullable(percentile(intervals, .99))},
                {"p95Drift", nullable(percentile(drifts, .95))},
                {"maxDrift", *std::max_element(drifts.begin(), drifts.end())},
                {"badCount", bad},
                {"badRate", static_cast<double>(bad) / static_cast<double>(intervals.size())}};
}
Json executor_delay(std::span<const double> delays) {
    if (delays.empty())
        return nullptr;
    return Json{{"minMs", *std::min_element(delays.begin(), delays.end())},
                {"meanMs", std::accumulate(delays.begin(), delays.end(), 0.0) /
                               static_cast<double>(delays.size())},
                {"maxMs", *std::max_element(delays.begin(), delays.end())},
                {"p50Ms", nullable(percentile(delays, .5))},
                {"p95Ms", nullable(percentile(delays, .95))},
                {"p99Ms", nullable(percentile(delays, .99))}};
}
std::string random_hex(std::size_t bytes) {
    if (bytes > 1024)
        throw std::length_error("entropy request exceeds limit");
    std::vector<unsigned char> buffer(bytes);
    if (RAND_bytes(buffer.data(), static_cast<int>(buffer.size())) != 1)
        throw std::runtime_error("entropy unavailable");
    std::string out;
    out.reserve(bytes * 2);
    for (const auto b : buffer) {
        out.push_back("0123456789abcdef"[b >> 4U]);
        out.push_back("0123456789abcdef"[b & 15U]);
    }
    return out;
}
std::string base64_encode(std::string_view input) {
    std::string out;
    out.reserve(((input.size() + 2) / 3) * 4);
    for (std::size_t i = 0; i < input.size(); i += 3) {
        const auto a = static_cast<unsigned char>(input[i]);
        const auto b = i + 1 < input.size() ? static_cast<unsigned char>(input[i + 1]) : 0U;
        const auto c = i + 2 < input.size() ? static_cast<unsigned char>(input[i + 2]) : 0U;
        const auto value = (static_cast<unsigned>(a) << 16U) | (b << 8U) | c;
        out.push_back(alphabet[(value >> 18U) & 63U]);
        out.push_back(alphabet[(value >> 12U) & 63U]);
        out.push_back(i + 1 < input.size() ? alphabet[(value >> 6U) & 63U] : '=');
        out.push_back(i + 2 < input.size() ? alphabet[value & 63U] : '=');
    }
    return out;
}
std::string base64_decode(std::string_view input) {
    if (input.size() % 4 != 0)
        throw std::invalid_argument("invalid base64 length");
    std::string out;
    out.reserve(input.size() / 4 * 3);
    for (std::size_t i = 0; i < input.size(); i += 4) {
        unsigned value = 0;
        for (std::size_t j = 0; j < 4; ++j) {
            const char ch = input[i + j];
            const auto v = alphabet.find(ch);
            if (ch == '=') {
                if (i + 4 != input.size() || j < 2 || (j == 2 && input[i + 3] != '='))
                    throw std::invalid_argument("invalid base64 padding");
                value <<= 6U;
            } else {
                if (v == std::string_view::npos)
                    throw std::invalid_argument("invalid base64 character");
                value = (value << 6U) | static_cast<unsigned>(v);
            }
        }
        out.push_back(static_cast<char>((value >> 16U) & 255U));
        if (input[i + 2] != '=')
            out.push_back(static_cast<char>((value >> 8U) & 255U));
        if (input[i + 3] != '=')
            out.push_back(static_cast<char>(value & 255U));
    }
    if (base64_encode(out) != input)
        throw std::invalid_argument("noncanonical base64");
    return out;
}
} // namespace speedtest
