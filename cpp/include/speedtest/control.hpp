#pragma once
#include "core.hpp"
#include <map>
#include <stdexcept>

namespace speedtest {
enum class FragmentError { metadata, limit, encoding, identity, payload };
class FragmentFailure : public std::runtime_error {
  public:
    explicit FragmentFailure(FragmentError value)
        : std::runtime_error("invalid control fragment"), kind(value) {}
    FragmentError kind;
};
class Reassembler {
  public:
    struct Limits {
        std::size_t fragments;
        std::size_t bytes;
    };
    explicit Reassembler(Limits limits)
        : max_fragments_(limits.fragments), max_bytes_(limits.bytes) {}
    [[nodiscard]] std::optional<Json> accept(const Json &fragment);
    [[nodiscard]] std::size_t pending() const {
        return assemblies_.size();
    }

  private:
    struct Assembly {
        std::size_t count{};
        Json identity;
        std::vector<std::optional<std::string>> parts;
        std::size_t received{}, bytes{};
    };
    std::size_t max_fragments_, max_bytes_;
    std::map<std::string, Assembly> assemblies_;
};
struct ResultEncoding {
    std::size_t single;
    std::size_t chunk;
    std::size_t maximum;
    bool legacy = false;
};
[[nodiscard]] std::vector<std::string> serialize_down_result(const Json &value,
                                                             ResultEncoding encoding);
} // namespace speedtest
