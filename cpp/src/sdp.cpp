#include "speedtest/sdp.hpp"
#include <algorithm>
#include <cctype>
#include <charconv>
#include <set>
#include <sstream>

namespace speedtest {
namespace {
bool prefix(const boost::asio::ip::address_v6 &ip, std::string_view raw, unsigned bits) {
    const auto value = ip.to_bytes();
    const auto base = boost::asio::ip::make_address_v6(raw).to_bytes();
    for (unsigned i = 0; i < bits; ++i)
        if (((value[i / 8] >> (7 - i % 8)) & 1U) != ((base[i / 8] >> (7 - i % 8)) & 1U))
            return false;
    return true;
}
std::string lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}
std::optional<std::uint32_t> number(std::string_view s) {
    if (s.empty() ||
        !std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= '0' && c <= '9'; }))
        return std::nullopt;
    std::uint32_t value{};
    const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), value);
    if (ec != std::errc{} || ptr != s.data() + s.size())
        return std::nullopt;
    return value;
}
// Rust str::split_whitespace uses Unicode White_Space, not the active C locale.
std::vector<std::string> words(std::string_view text) {
    const auto space = [&text](std::size_t at) -> std::size_t {
        const auto c = static_cast<unsigned char>(text[at]);
        if (c == 32 || (c >= 9 && c <= 13))
            return 1;
        for (const auto value :
             {"\xc2\x85", "\xc2\xa0", "\xe1\x9a\x80", "\xe2\x80\x80", "\xe2\x80\x81",
              "\xe2\x80\x82", "\xe2\x80\x83", "\xe2\x80\x84", "\xe2\x80\x85", "\xe2\x80\x86",
              "\xe2\x80\x87", "\xe2\x80\x88", "\xe2\x80\x89", "\xe2\x80\x8a", "\xe2\x80\xa8",
              "\xe2\x80\xa9", "\xe2\x80\xaf", "\xe2\x81\x9f", "\xe3\x80\x80"}) {
            const std::string_view view(value);
            if (text.substr(at).starts_with(view))
                return view.size();
        }
        return 0;
    };
    std::vector<std::string> result;
    std::size_t at = 0;
    while (at < text.size()) {
        if (const auto skip = space(at)) {
            at += skip;
            continue;
        }
        const auto start = at++;
        while (at < text.size() && space(at) == 0)
            ++at;
        result.emplace_back(text.substr(start, at - start));
    }
    return result;
}
} // namespace
bool is_public_ip(const boost::asio::ip::address &ip) {
    if (ip.is_v4()) {
        const auto v = ip.to_v4().to_bytes();
        const unsigned a = v[0], b = v[1], c = v[2];
        return !(a == 0 || a == 10 || a == 127 || a >= 224 || (a == 100 && b >= 64 && b <= 127) ||
                 (a == 169 && b == 254) || (a == 172 && b >= 16 && b <= 31) ||
                 (a == 192 && b == 168) || (a == 192 && b == 0 && (c == 0 || c == 2)) ||
                 (a == 192 && b == 31 && c == 196) || (a == 192 && b == 52 && c == 193) ||
                 (a == 192 && b == 88 && c == 99) || (a == 198 && (b == 18 || b == 19)) ||
                 (a == 198 && b == 51 && c == 100) || (a == 192 && b == 175 && c == 48) ||
                 (a == 203 && b == 0 && c == 113));
    }
    const auto address = ip.to_v6();
    if (!prefix(address, "2000::", 3))
        return false;
    for (const auto &[base, bits] :
         std::array<std::pair<std::string_view, unsigned>, 13>{{{"::", 96},
                                                                {"fc00::", 7},
                                                                {"fe80::", 10},
                                                                {"ff00::", 8},
                                                                {"100::", 64},
                                                                {"2001::", 23},
                                                                {"2001:db8::", 32},
                                                                {"2001:2::", 48},
                                                                {"2001:10::", 28},
                                                                {"2001:20::", 28},
                                                                {"2001:30::", 28},
                                                                {"2002::", 16},
                                                                {"3fff::", 20}}})
        if (prefix(address, base, bits))
            return false;
    return true;
}
FilteredSdp sanitize_sdp(std::string_view raw) {
    if (raw.size() > std::size_t{64} * 1024)
        throw SdpFailure(SdpError::limit);
    FilteredSdp out;
    out.sdp.reserve(raw.size());
    std::set<std::pair<std::string, std::uint32_t>> keys;
    std::size_t media = 0, lines = 0, at = 0;
    for (;;) {
        if (++lines > 1024)
            throw SdpFailure(SdpError::limit);
        const auto position = raw.find_first_of("\r\n", at);
        const auto end = position == std::string_view::npos ? raw.size() : position;
        const auto line = raw.substr(at, end - at);
        if (line.size() > 4096)
            throw SdpFailure(SdpError::limit);
        const auto sep = end == raw.size()
                             ? std::string_view{}
                             : raw.substr(end, raw.substr(end).starts_with("\r\n") ? 2 : 1);
        if (line.starts_with("m=") && ++media > 8)
            throw SdpFailure(SdpError::limit);
        if (line.starts_with("a=candidate:")) {
            if (++out.received_candidates > 64)
                throw SdpFailure(SdpError::limit);
            const auto fields = words(line.substr(12));
            if (fields.size() < 8 || (fields.size() - 8) % 2 != 0)
                throw SdpFailure(SdpError::candidate);
            const std::string_view component_field(fields[1]);
            const auto component =
                           number(component_field.starts_with("+") ? component_field.substr(1)
                                                                   : component_field),
                       priority = number(fields[3]), port = number(fields[5]);
            const auto transport = lower(fields[2]), kind = lower(fields[7]);
            if (!component || *component != 1 || transport != "udp" || !priority ||
                *priority == 0 || !port || *port == 0 || *port > 65535 ||
                lower(fields[6]) != "typ" ||
                (kind != "host" && kind != "srflx" && kind != "prflx" && kind != "relay"))
                throw SdpFailure(SdpError::candidate);
            auto host = lower(fields[4]);
            while (!host.empty() && host.back() == '.')
                host.pop_back();
            boost::system::error_code ec;
            const auto address = boost::asio::ip::make_address(fields[4], ec);
            if (host.ends_with(".local"))
                ++out.ignored_mdns_candidates;
            else if (ec || !is_public_ip(address))
                ++out.ignored_unsafe_candidates;
            else if (!keys.emplace(address.to_string(), *port).second)
                ++out.deduplicated_candidates;
            else {
                out.sdp.append("a=candidate:")
                    .append(fields[0])
                    .append(" ")
                    .append(std::to_string(component.value()))
                    .append(" ")
                    .append(transport)
                    .append(" ")
                    .append(std::to_string(priority.value()))
                    .append(" ")
                    .append(address.to_string())
                    .append(" ")
                    .append(std::to_string(port.value()))
                    .append(" typ ")
                    .append(kind);
                for (std::size_t i = 8; i < fields.size(); ++i)
                    out.sdp += " " + fields[i];
                out.sdp += sep;
            }
        } else {
            out.sdp += line;
            out.sdp += sep;
        }
        if (sep.empty())
            break;
        at = end + sep.size();
    }
    out.remote_candidates_after_filter = keys.size();
    return out;
}
} // namespace speedtest
