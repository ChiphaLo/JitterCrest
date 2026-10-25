#pragma once
#include "core.hpp"
#include <functional>
#include <set>

namespace speedtest {
using Environment = std::function<std::optional<std::string>(std::string_view)>;
struct Config {
    std::string host, name;
    std::set<std::string> allowed_origins, trusted_proxy_ips;
    bool public_cors{}, allow_cross_site{}, trust_proxy{}, rtc_experimental{}, rtc_legacy_control{},
        rtc_local_test{};
    bool probe_only{};
    std::string probe_only_id, probe_only_host;
    std::string csp_compat_origins;
    Json nodes;
    std::optional<std::string> rtc_udp4;
    std::uint64_t max_upload{};
    std::uint64_t upload_ms{};
    std::uint64_t rtc_session_ms{};
    std::uint64_t port{};
    std::uint64_t max_download{};
    std::uint64_t max_stream_upload{};
    std::uint64_t fixed_download_ms{};
    std::uint64_t stream_download_ms{};
    std::uint64_t http_max_requests_per_socket{};
    std::uint64_t http_keep_alive_ms{};
    std::uint64_t http_headers_ms{};
    std::uint64_t http_request_ms{};
    std::uint64_t http_max_headers_count{};
    std::uint64_t rate_max{};
    std::uint64_t speed_rate_max{};
    std::uint64_t rate_map_max{};
    std::uint64_t max_active_global{};
    std::uint64_t max_active_per_ip{};
    std::uint64_t download_budget{};
    std::uint64_t upload_budget{};
    std::uint64_t budget_window_ms{};
    std::uint64_t rtc_result_grace_ms{};
    std::uint64_t rtc_result_samples_max{};
    std::uint64_t rtc_down_tests_max{};
    std::uint64_t rtc_control_message_bytes{};
    std::uint64_t rtc_control_messages_max{};
    std::uint64_t rtc_test_messages_max{};
    std::uint64_t rtc_inbound_bytes_max{};
    std::uint64_t rtc_offer_body_max{};
    std::uint64_t control_single_bytes{};
    std::uint64_t control_fragment_bytes{};
    std::uint64_t control_fragment_count{};
    std::uint64_t control_reassembled_bytes{};
    std::uint64_t max_rtc_peers{};
    std::uint64_t max_rtc_peers_per_ip{};
    std::uint64_t rtc_cancel_max_per_ip{};
    std::uint64_t rtc_tombstones_max{};
    std::uint64_t rtc_offer_max_per_ip{};
    std::uint64_t rtc_offer_rate_max{};
    std::uint64_t rtc_negotiation_ms{};
    std::uint64_t rtc_measurement_ms{};
    std::uint64_t rtc_ice_gather_ms{};
    std::uint64_t io_threads{}, max_http_connections{};
    [[nodiscard]] static Config from_environment(const Environment &get);
    [[nodiscard]] static Config load();
};
[[nodiscard]] std::string_view trim_text(std::string_view value);
[[nodiscard]] std::vector<std::string> split_words(std::string_view value);
} // namespace speedtest
