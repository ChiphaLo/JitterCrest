#include "speedtest/control.hpp"
#include "speedtest/sdp.hpp"
#include <bit>
#include <cstdint>
#include <cstdlib>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
    if (size == 0 || size > 65536)
        return 0;
    const auto input = std::span(data, size);
    const auto bytes = std::as_bytes(input.subspan(1));
    const std::string text(input.begin() + 1, input.end());
    try {
        switch (data[0] % 5) {
        case 0:
            if (const auto frame = speedtest::Frame::decode(bytes)) {
                const auto encoded = frame->encode();
                const auto copy = speedtest::Frame::decode(encoded);
                if (!copy || copy->seq != frame->seq ||
                    std::bit_cast<std::uint64_t>(copy->sent_at) !=
                        std::bit_cast<std::uint64_t>(frame->sent_at))
                    std::abort();
            }
            break;
        case 1:
            (void)speedtest::sanitize_sdp(text);
            break;
        case 2: {
            const auto fragment = speedtest::parse_json(text);
            speedtest::Reassembler reassembler({.fragments = 64, .bytes = 65536});
            (void)reassembler.accept(fragment);
            break;
        }
        case 3:
            (void)speedtest::base64_decode(text);
            break;
        default:
            (void)speedtest::hash_string32(text);
            break;
        }
    } catch (const speedtest::FragmentFailure &) {
    } catch (const speedtest::SdpFailure &) {
    } catch (const std::invalid_argument &) {
    }
    return 0;
}
