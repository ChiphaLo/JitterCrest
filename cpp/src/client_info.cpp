#include "speedtest/client_info.hpp"
#include "speedtest/limits.hpp"
#include <algorithm>
#include <boost/asio/associated_executor.hpp>
#include <boost/asio/async_result.hpp>
#include <boost/asio/executor_work_guard.hpp>
#include <boost/asio/ip/address.hpp>
#include <boost/asio/post.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <cctype>
#include <curl/curl.h>
#include <limits>
#include <memory>

namespace speedtest {
CurlRuntime::CurlRuntime() {
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK)
        throw std::runtime_error("curl initialization failed");
    if ((curl_version_info(CURLVERSION_NOW)->features & CURL_VERSION_ASYNCHDNS) == 0) {
        curl_global_cleanup();
        throw std::runtime_error(
            "libcurl asynchronous DNS is required for bounded lookup shutdown");
    }
}
CurlRuntime::~CurlRuntime() {
    curl_global_cleanup();
}
namespace {
struct UrlDelete {
    void operator()(CURLU *p) const {
        curl_url_cleanup(p);
    }
};
struct TextDelete {
    void operator()(char *p) const {
        curl_free(p);
    }
};
struct EasyDelete {
    void operator()(CURL *p) const {
        curl_easy_cleanup(p);
    }
};
std::optional<std::string> part(CURLU *url, CURLUPart field, unsigned flags = 0) {
    char *raw = nullptr;
    if (curl_url_get(url, field, &raw, flags) != CURLUE_OK)
        return std::nullopt;
    const std::unique_ptr<char, TextDelete> owner(raw);
    return std::string(owner.get());
}
std::string clean(std::string_view text) {
    const auto words = split_words(text);
    std::string out;
    for (const auto &word : words) {
        if (!out.empty())
            out += ' ';
        out += word;
    }
    return out;
}
Json field(const Json &j, std::string_view key) {
    auto it = j.find(std::string(key));
    return it == j.end() ? Json(nullptr) : *it;
}
std::string js_text(const Json &j) {
    if (j.is_null() || j == false || j == 0 || j == "")
        return {};
    if (j.is_string())
        return j.get<std::string>();
    if (j.is_object())
        return "[object Object]";
    if (j.is_array()) {
        std::string out;
        bool first = true;
        for (const auto &v : j) {
            if (!first)
                out += ',';
            first = false;
            out += js_text(v);
        }
        return out;
    }
    return j.dump();
}
std::string first(const Json &j, std::array<std::string_view, 2> names) {
    const auto [a, b] = names;
    auto text = js_text(field(j, a));
    return text.empty() ? js_text(field(j, b)) : text;
}
std::string asn(std::string_view text) {
    const auto start = text.find_first_of("0123456789");
    if (start == std::string_view::npos)
        return {};
    const auto end = text.find_first_not_of("0123456789", start);
    return "AS" + std::string(text.substr(start, end == std::string_view::npos ? text.size() - start
                                                                               : end - start));
}
struct BodyBuffer {
    std::string body;
};
std::size_t receive(char *data, std::size_t size, std::size_t count, void *opaque) noexcept {
    try {
        if (size != 0 && count > std::numeric_limits<std::size_t>::max() / size)
            return 0;
        const auto n = size * count;
        auto &buffer = *static_cast<BodyBuffer *>(opaque);
        if (n > (256U << 10U) - buffer.body.size())
            return 0;
        buffer.body.append(data, n);
        return n;
    } catch (...) {
        return 0;
    }
}
Json fetch(const std::string &url, long timeout) {
    const std::unique_ptr<CURL, EasyDelete> handle(curl_easy_init());
    if (!handle)
        return nullptr;
    BodyBuffer buffer;
    curl_easy_setopt(handle.get(), CURLOPT_URL, url.c_str());
    curl_easy_setopt(handle.get(), CURLOPT_TIMEOUT_MS, timeout);
    curl_easy_setopt(handle.get(), CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(handle.get(), CURLOPT_USERAGENT, "chipha-speedtest/1.0");
    curl_easy_setopt(handle.get(), CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(handle.get(), CURLOPT_PROTOCOLS_STR, "https");
    curl_easy_setopt(handle.get(), CURLOPT_WRITEFUNCTION, &receive);
    curl_easy_setopt(handle.get(), CURLOPT_WRITEDATA, &buffer);
    if (curl_easy_perform(handle.get()) != CURLE_OK)
        return nullptr;
    try {
        return parse_json(buffer.body);
    } catch (const std::invalid_argument &) {
        return nullptr;
    }
}
Json fallback_body(const std::string &ip) {
    return Json{{"ip", ip},
                {"countryCode", ""},
                {"country", ""},
                {"region", ""},
                {"city", ""},
                {"isp", ""},
                {"asn", ""},
                {"asName", ""},
                {"source", "ipwho.is"},
                {"ok", false},
                {"error", "client info lookup unavailable"}};
}
} // namespace
std::optional<std::string> url_origin(std::string_view text) {
    const std::unique_ptr<CURLU, UrlDelete> url(curl_url());
    if (!url)
        return std::nullopt;
    const std::string owned(text);
    if (curl_url_set(url.get(), CURLUPART_URL, owned.c_str(), 0) != CURLUE_OK)
        return std::nullopt;
    const auto scheme = part(url.get(), CURLUPART_SCHEME),
               host = part(url.get(), CURLUPART_HOST, CURLU_PUNYCODE);
    if (!scheme || (*scheme != "http" && *scheme != "https") || !host || host->empty() ||
        !part(url.get(), CURLUPART_USER).value_or("").empty() ||
        part(url.get(), CURLUPART_PASSWORD))
        return std::nullopt;
    if (!std::all_of(host->begin(), host->end(), [](unsigned char c) {
            return std::isalnum(c) || c == '.' || c == '-' || c == ':' || c == '[' || c == ']';
        }))
        return std::nullopt;
    auto normalized_host = *host;
    std::transform(normalized_host.begin(), normalized_host.end(), normalized_host.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (normalized_host.starts_with('[') && normalized_host.ends_with(']')) {
        boost::system::error_code error;
        const auto ip = boost::asio::ip::make_address(
            std::string_view(normalized_host).substr(1, normalized_host.size() - 2), error);
        if (error)
            return std::nullopt;
        normalized_host = "[" + ip.to_string() + "]";
    }
    auto out = *scheme + "://" + normalized_host;
    const auto port = part(url.get(), CURLUPART_PORT, CURLU_NO_DEFAULT_PORT);
    if (port)
        out += ":" + *port;
    return out;
}
ClientInfo::ClientInfo(Fetch provider) : provider_(provider ? std::move(provider) : Fetch(fetch)) {}
Json ClientInfo::lookup_sync(const std::string &ip) {
    {
        const std::lock_guard guard(mutex_);
        auto it = cache_.find(ip);
        if (it != cache_.end() && Clock::now() - it->second.at < std::chrono::seconds(600))
            return it->second.body;
    }
    std::string encoded;
    for (const auto c : ip)
        encoded += c == ':' ? "%3A" : std::string(1, c);
    const auto primary =
        provider_("https://ipwho.is/" + encoded +
                      "?fields=success,message,country,country_code,region,city,connection",
                  1800);
    const auto connection = field(primary, "connection");
    auto number = asn(js_text(field(connection, "asn"))),
         name = clean(first(connection, {"org", "isp"}));
    if (field(primary, "success") != true || number.empty() || name.empty()) {
        const auto secondary = provider_("https://ipinfo.io/" + encoded + "/json", 2500);
        const auto org = js_text(field(secondary, "org"));
        std::string fallback_number, fallback_name = clean(org);
        if (org.size() > 2 && (org[0] == 'a' || org[0] == 'A') &&
            (org[1] == 's' || org[1] == 'S')) {
            const auto end = org.find_first_not_of("0123456789", 2);
            if (end != std::string::npos && end > 2 &&
                trim_text(std::string_view(org).substr(end)).size() < org.size() - end) {
                fallback_number = asn(std::string_view(org).substr(0, end));
                fallback_name = clean(std::string_view(org).substr(end));
            }
        }
        if (number.empty())
            number = fallback_number;
        if (name.empty())
            name = fallback_name;
    }
    auto body = fallback_body(ip);
    body["asn"] = number;
    body["asName"] = name;
    body["isp"] = name;
    if (field(primary, "success") == true) {
        auto code = clean(js_text(field(primary, "country_code")));
        std::transform(code.begin(), code.end(), code.begin(),
                       [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
        body["countryCode"] = code;
        for (const auto key : {"country", "region", "city"})
            body[key] = clean(js_text(field(primary, key)));
        const auto primary_number = asn(js_text(field(connection, "asn"))),
                   primary_name = clean(first(connection, {"org", "isp"})),
                   isp = clean(first(connection, {"isp", "org"}));
        if (!primary_number.empty())
            body["asn"] = primary_number;
        if (!primary_name.empty())
            body["asName"] = primary_name;
        body["isp"] = isp.empty() ? body["asName"] : Json(isp);
        body["ok"] = true;
    } else
        body["ok"] = !number.empty() || !name.empty();
    if (body["ok"] == true)
        body["error"] = "";
    {
        const std::lock_guard guard(mutex_);
        if (cache_.size() >= 2048 && !order_.empty()) {
            cache_.erase(order_.front());
            order_.pop_front();
        }
        if (!cache_.contains(ip))
            order_.push_back(ip);
        cache_[ip] = {Clock::now(), body};
    }
    return body;
}
boost::asio::awaitable<Json> ClientInfo::lookup(std::string ip) {
    namespace asio = boost::asio;
    if (stopping_)
        co_return fallback_body(ip);
    if (in_flight_.fetch_add(1) >= 4) {
        --in_flight_;
        co_return fallback_body(ip);
    }
    const auto self = shared_from_this();
    auto token = asio::use_awaitable;
    co_return co_await asio::async_initiate<decltype(token), void(Json)>(
        [self, ip = std::move(ip)](auto handler) mutable {
            auto executor = asio::get_associated_executor(handler);
            auto work = asio::make_work_guard(executor);
            asio::post(self->pool_, [self, ip = std::move(ip), executor, work = std::move(work),
                                     handler = std::move(handler)]() mutable {
                Json value;
                try {
                    value = self->lookup_sync(ip);
                } catch (...) {
                    value = fallback_body(ip);
                }
                --self->in_flight_;
                asio::post(executor, [handler = std::move(handler), value = std::move(value),
                                      work = std::move(work)]() mutable {
                    std::move(handler)(std::move(value));
                    work.reset();
                });
            });
        },
        token);
}
} // namespace speedtest
