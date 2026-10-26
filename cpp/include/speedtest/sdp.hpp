#pragma once
#include "core.hpp"
#include <boost/asio/ip/address.hpp>
#include <stdexcept>

namespace speedtest {
enum class SdpError { limit, candidate };
class SdpFailure : public std::runtime_error {
  public:
    explicit SdpFailure(SdpError value) : std::runtime_error("invalid SDP"), kind(value) {}
    SdpError kind;
};
struct FilteredSdp {
    std::string sdp;
    std::size_t received_candidates{}, ignored_mdns_candidates{}, ignored_unsafe_candidates{},
        deduplicated_candidates{}, remote_candidates_after_filter{};
};
[[nodiscard]] bool is_public_ip(const boost::asio::ip::address &address);
[[nodiscard]] FilteredSdp sanitize_sdp(std::string_view sdp);
} // namespace speedtest
