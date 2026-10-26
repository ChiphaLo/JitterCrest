#include "speedtest/config.hpp"
#include <iostream>
#include <map>
#include <stdexcept>
namespace {
speedtest::Json describe(const speedtest::Config &cfg) {
    speedtest::Json result;
    result["max_upload"] = cfg.max_upload;
    result["upload_ms"] = cfg.upload_ms;
    result["rtc_session_ms"] = cfg.rtc_session_ms;
    result["port"] = cfg.port;
    result["max_download"] = cfg.max_download;
    result["max_stream_upload"] = cfg.max_stream_upload;
    result["fixed_download_ms"] = cfg.fixed_download_ms;
    result["stream_download_ms"] = cfg.stream_download_ms;
    result["http_max_requests_per_socket"] = cfg.http_max_requests_per_socket;
    result["http_keep_alive_ms"] = cfg.http_keep_alive_ms;
    result["http_headers_ms"] = cfg.http_headers_ms;
    result["http_request_ms"] = cfg.http_request_ms;
    result["http_max_headers_count"] = cfg.http_max_headers_count;
    result["rate_max"] = cfg.rate_max;
    result["speed_rate_max"] = cfg.speed_rate_max;
    result["rate_map_max"] = cfg.rate_map_max;
    result["max_active_global"] = cfg.max_active_global;
    result["max_active_per_ip"] = cfg.max_active_per_ip;
    result["download_budget"] = cfg.download_budget;
    result["upload_budget"] = cfg.upload_budget;
    result["budget_window_ms"] = cfg.budget_window_ms;
    result["rtc_result_grace_ms"] = cfg.rtc_result_grace_ms;
    result["rtc_result_samples_max"] = cfg.rtc_result_samples_max;
    result["rtc_down_tests_max"] = cfg.rtc_down_tests_max;
    result["rtc_control_message_bytes"] = cfg.rtc_control_message_bytes;
    result["rtc_control_messages_max"] = cfg.rtc_control_messages_max;
    result["rtc_test_messages_max"] = cfg.rtc_test_messages_max;
    result["rtc_inbound_bytes_max"] = cfg.rtc_inbound_bytes_max;
    result["rtc_offer_body_max"] = cfg.rtc_offer_body_max;
    result["control_single_bytes"] = cfg.control_single_bytes;
    result["control_fragment_bytes"] = cfg.control_fragment_bytes;
    result["control_fragment_count"] = cfg.control_fragment_count;
    result["control_reassembled_bytes"] = cfg.control_reassembled_bytes;
    result["max_rtc_peers"] = cfg.max_rtc_peers;
    result["max_rtc_peers_per_ip"] = cfg.max_rtc_peers_per_ip;
    result["rtc_cancel_max_per_ip"] = cfg.rtc_cancel_max_per_ip;
    result["rtc_tombstones_max"] = cfg.rtc_tombstones_max;
    result["rtc_offer_max_per_ip"] = cfg.rtc_offer_max_per_ip;
    result["rtc_offer_rate_max"] = cfg.rtc_offer_rate_max;
    result["rtc_negotiation_ms"] = cfg.rtc_negotiation_ms;
    result["rtc_measurement_ms"] = cfg.rtc_measurement_ms;
    result["rtc_ice_gather_ms"] = cfg.rtc_ice_gather_ms;

    result["host"] = cfg.host;
    result["name"] = cfg.name;
    result["allowed_origins"] = cfg.allowed_origins;
    result["trusted_proxy_ips"] = cfg.trusted_proxy_ips;
    result["public_cors"] = cfg.public_cors;
    result["allow_cross_site"] = cfg.allow_cross_site;
    result["trust_proxy"] = cfg.trust_proxy;
    result["rtc_experimental"] = cfg.rtc_experimental;
    result["rtc_legacy_control"] = cfg.rtc_legacy_control;
    result["rtc_local_test"] = cfg.rtc_local_test;
    result["rtc_udp4"] = cfg.rtc_udp4 ? speedtest::Json(*cfg.rtc_udp4) : speedtest::Json(nullptr);
    return result;
}
} // namespace
int main(int argc, char **argv) {
    try {
        if (argc > 1 && std::string_view(argv[1]) == "--dump") {
            std::cout << describe(speedtest::Config::load()).dump() << '\n';
            return 0;
        }
        if (argc > 1 && std::string_view(argv[1]) == "--json-lines") {
            for (std::string line; std::getline(std::cin, line);) {
                try {
                    const auto env = speedtest::parse_json(line);
                    const auto cfg = speedtest::Config::from_environment(
                        [&env](std::string_view name) -> std::optional<std::string> {
                            auto it = env.find(std::string(name));
                            return it == env.end() ? std::nullopt
                                                   : std::optional(it->get<std::string>());
                        });
                    std::cout << speedtest::Json{{"valid", true}, {"config", describe(cfg)}}.dump()
                              << '\n';
                } catch (const std::exception &e) {
                    std::cout << speedtest::Json{{"valid", false}, {"error", e.what()}}.dump()
                              << '\n';
                }
            }
            return 0;
        }
        auto cfg = speedtest::Config::from_environment(
            [](std::string_view) -> std::optional<std::string> { return std::nullopt; });
        if (cfg.max_upload != (64ULL << 20U) || cfg.max_stream_upload != (2ULL << 30U) ||
            cfg.rtc_measurement_ms != 60000 || cfg.rate_max != 80)
            throw std::runtime_error("default configuration mismatch");
        bool rejected = false;
        try {
            (void)speedtest::Config::from_environment(
                [](std::string_view name) -> std::optional<std::string> {
                    if (name == "MAX_UPLOAD")
                        return "1023";
                    return std::nullopt;
                });
        } catch (const std::invalid_argument &) {
            rejected = true;
        }
        if (!rejected)
            throw std::runtime_error("invalid configuration accepted");
        const speedtest::Json nodes = speedtest::Json::array(
            {speedtest::Json{{"id", "fixture"}}, speedtest::Json{{"id", "regional-probe"}},
             speedtest::Json{{"id", "probe-only"}}, nullptr, 7});
        const auto custom = [&](std::string value, bool custom_id) {
            return speedtest::Config::from_environment(
                [&](std::string_view name) -> std::optional<std::string> {
                    if (name == "SPEEDTEST_NODES")
                        return nodes.dump();
                    if (name == "ENABLE_PROBE_ONLY_NODE")
                        return value;
                    if (name == "SPEEDTEST_PROBE_ONLY_NODE_ID" && custom_id)
                        return "regional-probe";
                    return std::nullopt;
                });
        };
        if (custom("0", true).nodes != speedtest::Json::array({nodes[0], nodes[2], nullptr, 7}))
            throw std::runtime_error("custom probe visibility mismatch");
        if (custom("0", false).nodes != speedtest::Json::array({nodes[0], nodes[1], nullptr, 7}))
            throw std::runtime_error("default probe visibility mismatch");
        for (const auto *value : {"", "1", "false", "00", " 0", "0 "})
            if (custom(value, true).nodes != nodes)
                throw std::runtime_error("nonzero probe enable flag changed nodes");
        std::cout << "configuration self-test passed\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
