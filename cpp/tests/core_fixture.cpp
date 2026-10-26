#include "speedtest/control.hpp"
#include "speedtest/sdp.hpp"
#include <bit>
#include <cmath>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <thread>

namespace {
void require(bool condition, std::string_view message) {
    if (!condition)
        throw std::runtime_error(std::string(message));
}
std::vector<std::byte> unhex(std::string_view hex) {
    if (hex.size() % 2)
        throw std::invalid_argument("hex length");
    std::vector<std::byte> result;
    for (std::size_t i = 0; i < hex.size(); i += 2)
        result.push_back(
            static_cast<std::byte>(std::stoul(std::string(hex.substr(i, 2)), nullptr, 16)));
    return result;
}
std::string hex(std::span<const std::byte> bytes) {
    std::string out;
    for (auto b : bytes) {
        const auto v = std::to_integer<unsigned char>(b);
        out.push_back("0123456789abcdef"[v >> 4U]);
        out.push_back("0123456789abcdef"[v & 15U]);
    }
    return out;
}
speedtest::Json execute(const speedtest::Json &input) {
    using namespace speedtest;
    const auto op = input.at("op").get<std::string>();
    if (op == "hash")
        return hash_string32(input.at("text").get<std::string>());
    if (op == "percentile") {
        const auto values = input.at("values").get<std::vector<double>>();
        auto p = percentile(values, input.at("p").get<double>());
        return p ? Json(*p) : Json(nullptr);
    }
    if (op == "accounting")
        return Accounting{input.at("planned"), input.at("scheduled"), input.at("skipped"),
                          input.at("actualSent")}
            .valid();
    if (op == "density")
        return required_sample_count(input.at("planned"), input.at("density"));
    if (op == "frame") {
        const auto bytes = unhex(input.at("hex").get<std::string>());
        auto f = Frame::decode(bytes);
        if (!f)
            return nullptr;
        return Json{{"kind", f->kind},
                    {"flags", f->flags},
                    {"sessionHash", f->session_hash},
                    {"testHash", f->test_hash},
                    {"seq", f->seq},
                    {"timestampBits", std::bit_cast<std::uint64_t>(f->sent_at)},
                    {"actualSent", f->actual_sent},
                    {"encoded", hex(f->encode())}};
    }
    if (op == "sdp") {
        try {
            const auto f = sanitize_sdp(input.at("sdp").get<std::string>());
            return Json{{"sdp", f.sdp},
                        {"received_candidates", f.received_candidates},
                        {"ignored_mdns_candidates", f.ignored_mdns_candidates},
                        {"ignored_unsafe_candidates", f.ignored_unsafe_candidates},
                        {"deduplicated_candidates", f.deduplicated_candidates},
                        {"remote_candidates_after_filter", f.remote_candidates_after_filter}};
        } catch (const SdpFailure &e) {
            return Json{{"error", e.kind == SdpError::limit ? "Limit" : "Candidate"}};
        }
    }
    if (op == "serialize") {
        try {
            auto messages =
                serialize_down_result(input.at("value"), {.single = input.at("single"),
                                                          .chunk = input.at("chunk"),
                                                          .maximum = input.at("maximum"),
                                                          .legacy = input.value("legacy", false)});
            Json result = Json::array();
            for (const auto &text : messages) {
                auto value = Json::parse(text);
                if (value.contains("type") && value["type"] == "controlFragment")
                    value["fragmentId"] = "NONCE";
                result.push_back(std::move(value));
            }
            return result;
        } catch (const FragmentFailure &e) {
            static constexpr std::array names = {"Metadata", "Limit", "Encoding", "Identity",
                                                 "Payload"};
            return Json{{"error", names[static_cast<std::size_t>(e.kind)]}};
        }
    }
    if (op == "fragments") {
        Reassembler r(
            {.fragments = input.value("maximum", 2048U), .bytes = input.value("bytes", 2U << 20U)});
        Json results = Json::array();
        for (const auto &f : input.at("parts")) {
            try {
                auto value = r.accept(f);
                results.push_back(value ? *value : Json(nullptr));
            } catch (const FragmentFailure &e) {
                static constexpr std::array names = {"Metadata", "Limit", "Encoding", "Identity",
                                                     "Payload"};
                results.push_back(Json{{"error", names[static_cast<std::size_t>(e.kind)]}});
            }
        }
        return results;
    }
    throw std::invalid_argument("unknown test operation");
}
void self_test() {
    using namespace speedtest;
    const Frame f{3, 1, 0x11223344, 5, 42, 123.5, 11};
    const auto encoded = f.encode();
    const auto copy = Frame::decode(encoded);
    require(copy && copy->seq == 42 && copy->sent_at == 123.5, "frame round trip");
    require(!Frame::decode(std::span(encoded).first(127)), "frame truncation");
    require(hash_string32("") == 0x811c9dc5U && hash_string32("a") == 0xe40c292cU, "UTF16 hash");
    const std::vector<double> values = {std::numeric_limits<double>::quiet_NaN(), 4, 1, 3, 2};
    require(percentile(values, .95) == 3 && !percentile({}, .5), "percentiles");
    require(required_sample_count(101, .99) == 100, "density ceil");
    require(!Accounting{1, std::numeric_limits<std::uint64_t>::max(), 2, 0}.valid(),
            "accounting overflow");
    std::mt19937 random(1234);
    for (std::size_t n = 0; n < 1024; ++n) {
        std::string raw(n, '\0');
        for (auto &b : raw)
            b = static_cast<char>(random() & 255U);
        require(base64_decode(base64_encode(raw)) == raw, "binary base64 round trip");
    }
    for (const auto invalid : {"A", "AB==", "AA=A", "AAAA\n", "AA==AAAA", "A===", "===="}) {
        bool rejected = false;
        try {
            (void)base64_decode(invalid);
        } catch (const std::invalid_argument &) {
            rejected = true;
        }
        require(rejected, "strict base64");
    }
    require(!is_public_ip(boost::asio::ip::make_address("192.0.2.1")), "documentation IP");
    require(is_public_ip(boost::asio::ip::make_address("2606:4700::1111")), "global IPv6");
    const auto filtered =
        sanitize_sdp("v=0\r\na=candidate:1 1 UDP 123 8.8.8.8 9999 typ host\r\na=candidate:2 1 udp "
                     "124 8.8.8.8 9999 typ srflx\r\n");
    require(filtered.remote_candidates_after_filter == 1 && filtered.deduplicated_candidates == 1,
            "SDP deduplication");
    std::vector<std::jthread> threads;
    for (int i = 0; i < 4; ++i)
        threads.emplace_back([] {
            for (int n = 0; n < 1000; ++n)
                require(hash_string32("concurrent") != 0, "concurrent pure functions");
        });
    std::cout << "core self-test passed\n";
}
} // namespace
int main(int argc, char **argv) {
    try {
        if (argc > 1 && std::string_view(argv[1]) == "--json-lines") {
            for (std::string line; std::getline(std::cin, line);) {
                try {
                    std::cout << execute(speedtest::Json::parse(line)).dump() << '\n';
                } catch (const std::exception &e) {
                    std::cout << speedtest::Json{{"failure", e.what()}}.dump() << '\n';
                }
            }
        } else
            self_test();
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
