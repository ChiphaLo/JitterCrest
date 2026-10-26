#include "speedtest/rtc.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <set>

namespace speedtest {
Json json_field(const Json &value, std::string_view key) {
    const auto it = value.find(std::string(key));
    return it == value.end() ? Json(nullptr) : *it;
}
std::optional<std::uint64_t> rtc_number(const Json &value) {
    if (!value.is_number())
        return std::nullopt;
    const auto n = value.get<double>();
    if (!std::isfinite(n) || n < 0 || n > 9007199254740991.0 || std::floor(n) != n)
        return std::nullopt;
    return static_cast<std::uint64_t>(n);
}
namespace {
std::string js_string(const Json &value) {
    if (value.is_string())
        return value.get<std::string>();
    if (value.is_object())
        return "[object Object]";
    if (value.is_array()) {
        std::string result;
        bool first = true;
        for (const auto &item : value) {
            if (!first)
                result += ',';
            first = false;
            if (!item.is_null())
                result += js_string(item);
        }
        return result;
    }
    return value.dump();
}
std::optional<double> js_number(const Json &value) {
    if (value.is_null())
        return 0.0;
    if (value.is_boolean())
        return value.get<bool>() ? 1.0 : 0.0;
    if (value.is_number())
        return value.get<double>();
    const auto owned = js_string(value);
    auto text = trim_text(owned);
    if (text.empty())
        return 0.0;
    if (text.starts_with("0x") || text.starts_with("0X")) {
        text.remove_prefix(2);
        std::uint64_t n{};
        const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), n, 16);
        if (text.empty() || error != std::errc{} || end != text.data() + text.size())
            return std::nullopt;
        return static_cast<double>(n);
    }
    if (text.starts_with('+'))
        text.remove_prefix(1);
    double n{};
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), n);
    if (text.empty() || error != std::errc{} || end != text.data() + text.size())
        return std::nullopt;
    return n;
}
bool string_primitive(const Json &value) {
    return value.is_string() || value.is_array() || value.is_object();
}
bool nonnegative_integer(const Json &value) {
    if (!value.is_number())
        return false;
    const auto n = value.get<double>();
    return std::isfinite(n) && n >= 0 && std::floor(n) == n;
}
bool add_equals(const Json &a, const Json &b, const Json &expected) {
    if (string_primitive(a) || string_primitive(b))
        return expected.is_string() && expected.get<std::string>() == js_string(a) + js_string(b);
    const auto left = js_number(a), right = js_number(b);
    return left && right && expected.is_number() && expected.get<double>() == *left + *right;
}
bool greater(const Json &a, const Json &b) {
    if (string_primitive(a) && string_primitive(b))
        return js_string(a) > js_string(b);
    const auto left = js_number(a), right = js_number(b);
    return left && right && *left > *right;
}
} // namespace
std::vector<std::string>
upload_complete_reasons(const Json &msg, const MeasurementIdentity &identity, bool prior_invalid) {
    std::vector<std::string> reasons;
    for (const auto &[field, reason] : std::array<std::pair<std::string_view, std::string_view>, 4>{
             {{"planned", "UPLOAD_PLANNED_INVALID"},
              {"scheduled", "UPLOAD_SCHEDULED_INVALID"},
              {"skipped", "UPLOAD_SKIPPED_INVALID"},
              {"actualSent", "UPLOAD_ACTUALSENT_INVALID"}}}) {
        if (!nonnegative_integer(json_field(msg, field)))
            reasons.emplace_back(reason);
    }
    const auto planned = json_field(msg, "planned"), scheduled = json_field(msg, "scheduled"),
               skipped = json_field(msg, "skipped"), actual = json_field(msg, "actualSent");
    if (!planned.is_number() || planned.get<double>() != static_cast<double>(identity.planned))
        reasons.emplace_back("UPLOAD_PLANNED_MISMATCH");
    if (!add_equals(scheduled, skipped, planned))
        reasons.emplace_back("UPLOAD_SCHEDULER_ACCOUNTING");
    if (greater(actual, scheduled))
        reasons.emplace_back("UPLOAD_SCHEDULER_BOUNDS");
    const auto sequences = json_field(msg, "sentSeqs");
    if (sequences.is_array() && actual.is_number() &&
        actual.get<double>() == static_cast<double>(sequences.size())) {
        std::set<std::uint64_t> unique;
        for (const auto &seq : sequences) {
            const auto n = rtc_number(seq);
            if (!nonnegative_integer(seq) || !n || *n > UINT32_MAX || *n >= identity.planned ||
                !unique.insert(*n).second) {
                reasons.emplace_back("UPLOAD_SENT_SEQS_INVALID");
                break;
            }
        }
    } else
        reasons.emplace_back("UPLOAD_SENT_SEQS_COUNT_MISMATCH");
    if (json_field(msg, "testId") != identity.test_id ||
        json_field(msg, "sessionId") != identity.session_id)
        reasons.emplace_back("UPLOAD_COMPLETE_ID_MISMATCH");
    if (prior_invalid || !reasons.empty())
        reasons.emplace_back("UPLOAD_COMPLETE_INVALID");
    return reasons;
}
} // namespace speedtest
