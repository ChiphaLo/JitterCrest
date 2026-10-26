#include "speedtest/config.hpp"
#include "speedtest/sdp.hpp"
#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <limits>

namespace speedtest {
namespace {
std::size_t space_at(std::string_view value, std::size_t at) {
    const auto c = static_cast<unsigned char>(value[at]);
    if (c == 32 || (c >= 9 && c <= 13))
        return 1;
    for (const auto raw :
         {"\xc2\x85", "\xc2\xa0", "\xe1\x9a\x80", "\xe2\x80\x80", "\xe2\x80\x81", "\xe2\x80\x82",
          "\xe2\x80\x83", "\xe2\x80\x84", "\xe2\x80\x85", "\xe2\x80\x86", "\xe2\x80\x87",
          "\xe2\x80\x88", "\xe2\x80\x89", "\xe2\x80\x8a", "\xe2\x80\xa8", "\xe2\x80\xa9",
          "\xe2\x80\xaf", "\xe2\x81\x9f", "\xe3\x80\x80"}) {
        const std::string_view s(raw);
        if (value.substr(at).starts_with(s))
            return s.size();
    }
    return 0;
}
struct Numeric {
    std::string_view name;
    std::uint64_t fallback, minimum, maximum;
};
std::uint64_t numeric(const Environment &get, Numeric rule) {
    const auto raw = get(rule.name).value_or("");
    auto text = std::string_view(raw);
    std::uint64_t value = rule.fallback;
    if (!text.empty()) {
        if (text.front() == '+')
            text.remove_prefix(1);
        if (text.empty())
            throw std::invalid_argument("invalid " + std::string(rule.name));
        const auto [p, ec] = std::from_chars(text.data(), text.data() + text.size(), value);
        if (ec != std::errc{} || p != text.data() + text.size())
            throw std::invalid_argument("invalid " + std::string(rule.name));
    }
    if (value < rule.minimum || value > rule.maximum)
        throw std::invalid_argument(std::string(rule.name) + " must be between " +
                                    std::to_string(rule.minimum) + " and " +
                                    std::to_string(rule.maximum));
    return value;
}
std::set<std::string> csv(std::string_view text) {
    std::set<std::string> result;
    for (std::size_t start = 0;;) {
        const auto end = text.find(',', start);
        const auto part = trim_text(
            text.substr(start, end == std::string_view::npos ? text.size() - start : end - start));
        if (!part.empty())
            result.emplace(part);
        if (end == std::string_view::npos)
            break;
        start = end + 1;
    }
    return result;
}
} // namespace
std::string_view trim_text(std::string_view value) {
    std::size_t start = 0, end = 0;
    while (start < value.size()) {
        const auto n = space_at(value, start);
        if (!n)
            break;
        start += n;
    }
    end = start;
    for (auto at = start; at < value.size();) {
        const auto n = space_at(value, at);
        if (n)
            at += n;
        else {
            ++at;
            end = at;
        }
    }
    return value.substr(start, end - start);
}
std::vector<std::string> split_words(std::string_view value) {
    std::vector<std::string> result;
    for (std::size_t at = 0; at < value.size();) {
        if (const auto n = space_at(value, at)) {
            at += n;
            continue;
        }
        const auto start = at++;
        while (at < value.size() && !space_at(value, at))
            ++at;
        result.emplace_back(value.substr(start, at - start));
    }
    return result;
}
Config Config::load() {
    // Environment is read once, before worker threads start; never mutated here.
    return from_environment([](std::string_view name) -> std::optional<std::string> {
        const std::string key(name);
        // All environment loading precedes worker creation; no setenv/putenv
        // exists in the service. This checker assumes concurrent mutation.
        // NOLINTNEXTLINE(concurrency-mt-unsafe)
        if (const auto *value = std::getenv(key.c_str()))
            return std::string(value);
        return std::nullopt;
    });
}
Config Config::from_environment(const Environment &get) {
    Config cfg;
    cfg.host = get("SPEEDTEST_HOST").value_or("127.0.0.1");
    cfg.name = get("SPEEDTEST_NODE_NAME").value_or("speedtest-node");
    cfg.trust_proxy = get("TRUST_PROXY_HEADERS") == "1";
    if (cfg.trust_proxy && (cfg.host.empty() || cfg.host == "0.0.0.0" || cfg.host == "::"))
        throw std::invalid_argument("refusing proxy headers on a public listener");
    cfg.trusted_proxy_ips = csv(get("TRUSTED_PROXY_IPS").value_or(""));
    cfg.allowed_origins = csv(get("ALLOWED_ORIGINS").value_or(""));
    cfg.public_cors = get("PUBLIC_CORS") == "1";
    cfg.allow_cross_site = get("SPEEDTEST_ALLOW_CROSS_SITE") == "1";
    cfg.rtc_experimental = get("SPEEDTEST_RUST_RTC_EXPERIMENTAL") == "1";
    cfg.rtc_legacy_control = get("SPEEDTEST_RTC_LEGACY_CONTROL") == "1";
    cfg.rtc_local_test = get("SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST") == "1";
    if (cfg.rtc_local_test && cfg.host != "127.0.0.1")
        throw std::invalid_argument(
            "private WebRTC candidates are allowed only on a loopback listener");
    const auto raw = std::string(trim_text(get("SPEEDTEST_WEBRTC_UDP4").value_or("")));
    if (!raw.empty()) {
        boost::system::error_code ec;
        const auto address = boost::asio::ip::make_address(raw, ec);
        if (ec || !address.is_v4())
            throw std::invalid_argument("invalid SPEEDTEST_WEBRTC_UDP4");
        if (!is_public_ip(address))
            throw std::invalid_argument("SPEEDTEST_WEBRTC_UDP4 must be a public IPv4 address");
    }
    if (!raw.empty())
        cfg.rtc_udp4 = raw;
    cfg.nodes = Json::array(
        {Json{{"id", "local"}, {"label", "Local server"}, {"base", ""}, {"rtcTargetMs", 25}}});
    Json nodes;
    try {
        nodes = parse_json(get("SPEEDTEST_NODES").value_or(""));
    } catch (const std::invalid_argument &) {
        nodes = nullptr;
    }
    if (nodes.is_array() && !nodes.empty())
        cfg.nodes = std::move(nodes);
    cfg.probe_only_id = get("SPEEDTEST_PROBE_ONLY_NODE_ID").value_or("probe-only");
    if (get("ENABLE_PROBE_ONLY_NODE") == "0")
        cfg.nodes.erase(std::remove_if(cfg.nodes.begin(), cfg.nodes.end(),
                                       [&cfg](const Json &node) {
                                           return node.is_object() && node.contains("id") &&
                                                  node["id"] == cfg.probe_only_id;
                                       }),
                        cfg.nodes.end());
    cfg.probe_only = get("SPEEDTEST_PROBE_ONLY") == "1";
    cfg.csp_compat_origins = get("SPEEDTEST_CSP_COMPAT_ORIGINS").value_or("");
    cfg.probe_only_host = get("SPEEDTEST_PROBE_ONLY_HOST").value_or("");
    cfg.max_upload = numeric(get, {.name = "MAX_UPLOAD",
                                   .fallback = 64ULL << 20ULL,
                                   .minimum = 1024ULL,
                                   .maximum = 256ULL << 20ULL});
    cfg.upload_ms = numeric(
        get,
        {.name = "MAX_UPLOAD_MS", .fallback = 25000ULL, .minimum = 1000ULL, .maximum = 120000ULL});
    cfg.rtc_session_ms = numeric(get, {.name = "MAX_RTC_SESSION_MS",
                                       .fallback = 90000ULL,
                                       .minimum = 10000ULL,
                                       .maximum = 300000ULL});
    cfg.port =
        numeric(get, {.name = "PORT", .fallback = 3010ULL, .minimum = 1ULL, .maximum = 65535ULL});
    cfg.max_download = numeric(get, {.name = "MAX_DOWNLOAD",
                                     .fallback = 256ULL << 20ULL,
                                     .minimum = 96ULL << 20ULL,
                                     .maximum = 1ULL << 30ULL});
    cfg.max_stream_upload = numeric(get, {.name = "MAX_STREAM_UPLOAD",
                                          .fallback = 2ULL << 30ULL,
                                          .minimum = cfg.max_upload,
                                          .maximum = 4ULL << 30ULL});
    cfg.fixed_download_ms = numeric(get, {.name = "MAX_FIXED_DOWNLOAD_MS",
                                          .fallback = 120000ULL,
                                          .minimum = 1000ULL,
                                          .maximum = 300000ULL});
    cfg.stream_download_ms = numeric(get, {.name = "MAX_DOWNLOAD_STREAM_MS",
                                           .fallback = 15000ULL,
                                           .minimum = 1000ULL,
                                           .maximum = 30000ULL});
    cfg.http_max_requests_per_socket = numeric(get, {.name = "HTTP_MAX_REQUESTS_PER_SOCKET",
                                                     .fallback = 100ULL,
                                                     .minimum = 1ULL,
                                                     .maximum = 1000ULL});
    cfg.http_keep_alive_ms = numeric(get, {.name = "HTTP_KEEP_ALIVE_TIMEOUT_MS",
                                           .fallback = 5000ULL,
                                           .minimum = 1000ULL,
                                           .maximum = 30000ULL});
    cfg.http_headers_ms = numeric(get, {.name = "HTTP_HEADERS_TIMEOUT_MS",
                                        .fallback = 10000ULL,
                                        .minimum = 1000ULL,
                                        .maximum = 60000ULL});
    cfg.http_request_ms = numeric(get, {.name = "HTTP_REQUEST_TIMEOUT_MS",
                                        .fallback = 35000ULL,
                                        .minimum = cfg.upload_ms,
                                        .maximum = 180000ULL});
    cfg.http_max_headers_count = numeric(
        get,
        {.name = "HTTP_MAX_HEADERS_COUNT", .fallback = 64ULL, .minimum = 16ULL, .maximum = 256ULL});
    cfg.rate_max =
        numeric(get, {.name = "RATE_MAX", .fallback = 80ULL, .minimum = 10ULL, .maximum = 1000ULL});
    cfg.speed_rate_max = numeric(
        get, {.name = "SPEED_RATE_MAX", .fallback = 240ULL, .minimum = 16ULL, .maximum = 2000ULL});
    cfg.rate_map_max = numeric(
        get,
        {.name = "RATE_MAP_MAX", .fallback = 10000ULL, .minimum = 100ULL, .maximum = 100000ULL});
    cfg.max_active_global = numeric(
        get,
        {.name = "MAX_ACTIVE_GLOBAL", .fallback = 128ULL, .minimum = 8ULL, .maximum = 1024ULL});
    cfg.max_active_per_ip = numeric(
        get, {.name = "MAX_ACTIVE_PER_IP", .fallback = 16ULL, .minimum = 8ULL, .maximum = 128ULL});
    cfg.download_budget = numeric(get, {.name = "DOWNLOAD_BYTE_BUDGET_PER_IP",
                                        .fallback = 4ULL << 30ULL,
                                        .minimum = 1ULL << 20ULL,
                                        .maximum = 32ULL << 30ULL});
    cfg.upload_budget = numeric(get, {.name = "UPLOAD_BYTE_BUDGET_PER_IP",
                                      .fallback = 4ULL << 30ULL,
                                      .minimum = 1ULL << 20ULL,
                                      .maximum = 32ULL << 30ULL});
    cfg.budget_window_ms = numeric(get, {.name = "BYTE_BUDGET_WINDOW_MS",
                                         .fallback = 60000ULL,
                                         .minimum = 10000ULL,
                                         .maximum = 300000ULL});
    cfg.rtc_result_grace_ms = numeric(
        get,
        {.name = "RTC_RESULT_GRACE_MS", .fallback = 300ULL, .minimum = 50ULL, .maximum = 2000ULL});
    cfg.rtc_result_samples_max = numeric(get, {.name = "MAX_RTC_RESULT_SAMPLES",
                                               .fallback = 20000ULL,
                                               .minimum = 100ULL,
                                               .maximum = 50000ULL});
    cfg.rtc_down_tests_max = numeric(
        get,
        {.name = "MAX_DOWN_TESTS_PER_SOCKET", .fallback = 2ULL, .minimum = 1ULL, .maximum = 8ULL});
    cfg.rtc_control_message_bytes = numeric(get, {.name = "MAX_RTC_CONTROL_MESSAGE_BYTES",
                                                  .fallback = 16384ULL,
                                                  .minimum = 256ULL,
                                                  .maximum = 65536ULL});
    cfg.rtc_control_messages_max = numeric(get, {.name = "MAX_RTC_CONTROL_MESSAGES_PER_PEER",
                                                 .fallback = 256ULL,
                                                 .minimum = 4ULL,
                                                 .maximum = 512ULL});
    cfg.rtc_test_messages_max = numeric(get, {.name = "MAX_RTC_TEST_MESSAGES_PER_PEER",
                                              .fallback = 30000ULL,
                                              .minimum = 24000ULL,
                                              .maximum = 50000ULL});
    cfg.rtc_inbound_bytes_max = numeric(get, {.name = "MAX_RTC_INBOUND_BYTES_PER_PEER",
                                              .fallback = 4ULL << 20ULL,
                                              .minimum = 256ULL << 10ULL,
                                              .maximum = 16ULL << 20ULL});
    cfg.rtc_offer_body_max = numeric(get, {.name = "MAX_RTC_OFFER_BODY_BYTES",
                                           .fallback = 256ULL << 10ULL,
                                           .minimum = 16ULL << 10ULL,
                                           .maximum = 1ULL << 20ULL});
    cfg.control_single_bytes = numeric(get, {.name = "RTC_CONTROL_SINGLE_MESSAGE_BYTES",
                                             .fallback = 900ULL,
                                             .minimum = 512ULL,
                                             .maximum = 1200ULL});
    cfg.control_fragment_bytes = numeric(get, {.name = "RTC_CONTROL_FRAGMENT_PAYLOAD_BYTES",
                                               .fallback = 512ULL,
                                               .minimum = 256ULL,
                                               .maximum = 768ULL});
    cfg.control_fragment_count = numeric(get, {.name = "MAX_RTC_CONTROL_FRAGMENT_COUNT",
                                               .fallback = 2048ULL,
                                               .minimum = 8ULL,
                                               .maximum = 4096ULL});
    cfg.control_reassembled_bytes = numeric(get, {.name = "MAX_RTC_REASSEMBLED_CONTROL_BYTES",
                                                  .fallback = 2ULL << 20ULL,
                                                  .minimum = 64ULL << 10ULL,
                                                  .maximum = 4ULL << 20ULL});
    cfg.max_rtc_peers = numeric(
        get, {.name = "MAX_RTC_PEERS", .fallback = 16ULL, .minimum = 3ULL, .maximum = 256ULL});
    cfg.max_rtc_peers_per_ip = numeric(
        get, {.name = "MAX_RTC_PEERS_PER_IP", .fallback = 4ULL, .minimum = 3ULL, .maximum = 64ULL});
    cfg.rtc_cancel_max_per_ip = numeric(
        get,
        {.name = "RTC_CANCEL_MAX_PER_IP", .fallback = 80ULL, .minimum = 10ULL, .maximum = 1000ULL});
    cfg.rtc_tombstones_max = numeric(get, {.name = "MAX_RTC_ATTEMPT_TOMBSTONES",
                                           .fallback = 2048ULL,
                                           .minimum = 128ULL,
                                           .maximum = 10000ULL});
    cfg.rtc_offer_max_per_ip = numeric(
        get,
        {.name = "RTC_OFFER_MAX_PER_IP", .fallback = 20ULL, .minimum = 3ULL, .maximum = 200ULL});
    cfg.rtc_offer_rate_max = numeric(get, {.name = "RTC_OFFER_RATE_MAX",
                                           .fallback = 10000ULL,
                                           .minimum = 100ULL,
                                           .maximum = 100000ULL});
    cfg.rtc_negotiation_ms = numeric(get, {.name = "MAX_RTC_NEGOTIATION_MS",
                                           .fallback = 15000ULL,
                                           .minimum = 1000ULL,
                                           .maximum = 60000ULL});
    cfg.rtc_measurement_ms =
        numeric(get, {.name = "MAX_RTC_MEASUREMENT_MS",
                      .fallback = std::min<std::uint64_t>(60000ULL, cfg.rtc_session_ms - 5000ULL),
                      .minimum = 1000ULL,
                      .maximum = cfg.rtc_session_ms - 5000ULL});
    cfg.rtc_ice_gather_ms = numeric(
        get,
        {.name = "RTC_ICE_GATHER_MAX_MS", .fallback = 50ULL, .minimum = 5ULL, .maximum = 5000ULL});
    cfg.io_threads =
        numeric(get, {.name = "SPEEDTEST_IO_THREADS", .fallback = 2, .minimum = 1, .maximum = 64});
    cfg.max_http_connections = numeric(
        get, {.name = "MAX_HTTP_CONNECTIONS", .fallback = 4096, .minimum = 8, .maximum = 65536});
    boost::system::error_code ec;
    (void)boost::asio::ip::make_address(cfg.host, ec);
    if (ec)
        throw std::invalid_argument("SPEEDTEST_HOST must be an IP literal");
    return cfg;
}
} // namespace speedtest
