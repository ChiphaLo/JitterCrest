#include "speedtest/control.hpp"

namespace speedtest {
namespace {
Json identity(const Json &value) {
    Json result = Json::array();
    for (const auto key : {"sessionId", "sessionHash", "testId", "testHash"})
        result.push_back(value.contains(key) ? value[key] : Json(nullptr));
    return result;
}
std::optional<std::size_t> integer(const Json &value) {
    if (value.is_number_unsigned())
        return value.get<std::size_t>();
    if (value.is_number_integer() && value.get<std::int64_t>() >= 0)
        return static_cast<std::size_t>(value.get<std::int64_t>());
    return std::nullopt;
}
} // namespace
std::optional<Json> Reassembler::accept(const Json &f) {
    const auto get = [&f](const char *key) -> Json {
        return f.contains(key) ? f[key] : Json(nullptr);
    };
    const auto id = get("fragmentId");
    const auto count = integer(get("count")), index = integer(get("index"));
    if (!id.is_string() || id.get_ref<const std::string &>().empty() ||
        get("type") != "controlFragment" || get("messageType") != "uploadComplete" ||
        get("encoding") != "base64" || !get("data").is_string() || !count || !index ||
        count.value() == 0 || count.value() > max_fragments_ || index.value() >= count.value())
        throw FragmentFailure(FragmentError::metadata);
    std::string data;
    try {
        data = base64_decode(get("data").get_ref<const std::string &>());
    } catch (const std::invalid_argument &) {
        throw FragmentFailure(FragmentError::encoding);
    }
    const auto key = id.get<std::string>();
    const auto ids = identity(f);
    auto it = assemblies_.find(key);
    if (it == assemblies_.end()) {
        if (assemblies_.size() >= 4)
            throw FragmentFailure(FragmentError::limit);
        it = assemblies_
                 .emplace(key,
                          Assembly{count.value(), ids,
                                   std::vector<std::optional<std::string>>(count.value()), 0, 0})
                 .first;
    }
    auto &a = it->second;
    if (a.count != count.value() || a.identity != ids)
        throw FragmentFailure(FragmentError::identity);
    if (a.parts[index.value()])
        return std::nullopt;
    if (data.size() > max_bytes_ - std::min(a.bytes, max_bytes_)) {
        assemblies_.erase(it);
        throw FragmentFailure(FragmentError::limit);
    }
    a.bytes += data.size();
    a.parts[index.value()] = std::move(data);
    ++a.received;
    if (a.received != a.count)
        return std::nullopt;
    auto complete = std::move(a);
    assemblies_.erase(it);
    std::string payload;
    payload.reserve(complete.bytes);
    for (const auto &part : complete.parts) {
        if (!part)
            throw FragmentFailure(FragmentError::payload);
        payload += part.value();
    }
    Json result;
    try {
        result = parse_json(payload);
    } catch (const std::invalid_argument &) {
        throw FragmentFailure(FragmentError::payload);
    }
    if (!result.is_object() || !result.contains("type") || result["type"] != "uploadComplete" ||
        identity(result) != complete.identity)
        throw FragmentFailure(FragmentError::identity);
    return result;
}
std::vector<std::string> serialize_down_result(const Json &value, ResultEncoding encoding) {
    const auto [single, chunk, maximum, legacy] = encoding;
    const auto payload = value.dump();
    if (legacy) {
        if (payload.size() > single)
            throw FragmentFailure(FragmentError::limit);
        return {payload};
    }
    if (payload.size() <= single)
        return {payload};
    if (chunk == 0 || payload.size() / chunk + (payload.size() % chunk != 0) > maximum)
        throw FragmentFailure(FragmentError::limit);
    const auto count = payload.size() / chunk + (payload.size() % chunk != 0);
    const auto id = random_hex(8);
    std::vector<std::string> result;
    result.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        Json f = {{"type", "controlFragment"},
                  {"messageType", "downResult"},
                  {"fragmentId", id},
                  {"index", i},
                  {"count", count},
                  {"encoding", "base64"}};
        for (const auto key : {"testId", "sessionId", "sessionHash", "testHash"})
            f[key] = value.contains(key) ? value[key] : Json(nullptr);
        f["data"] = base64_encode(std::string_view(payload).substr(i * chunk, chunk));
        result.push_back(f.dump());
    }
    return result;
}
} // namespace speedtest
