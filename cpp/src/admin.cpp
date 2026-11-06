#include "speedtest/admin.hpp"
#include "speedtest/client_info.hpp"
#include "speedtest/sdp.hpp"
#include <boost/asio/ip/address.hpp>
#include <curl/curl.h>
#include <fcntl.h>
#include <fstream>
#include <openssl/crypto.h>
#include <openssl/rand.h>
#include <openssl/sha.h>
#include <regex>
#include <sys/stat.h>
#include <unistd.h>

namespace speedtest {
namespace {
std::int64_t now_seconds() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}
std::string hex(std::span<const unsigned char> bytes) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    for (const auto byte : bytes) {
        result += digits[byte >> 4U];
        result += digits[byte & 15U];
    }
    return result;
}
std::string secret() {
    std::array<unsigned char, 32> value{};
    if (RAND_bytes(value.data(), static_cast<int>(value.size())) != 1)
        throw std::runtime_error("enrollment entropy unavailable");
    return hex(value);
}
std::string digest(std::string_view value) {
    std::array<unsigned char, SHA256_DIGEST_LENGTH> output{};
    SHA256(reinterpret_cast<const unsigned char *>(value.data()), value.size(), output.data());
    return hex(output);
}
bool matches(std::string_view input, const std::string &expected) {
    const auto actual = digest(input);
    return expected.size() == actual.size() &&
           CRYPTO_memcmp(actual.data(), expected.data(), actual.size()) == 0;
}
std::string text(const Json &body, const char *key) {
    if (!body.is_object() || !body.contains(key) || !body[key].is_string())
        throw std::invalid_argument(std::string("missing ") + key);
    return body[key].get<std::string>();
}
bool valid_id(const std::string &id) {
    return std::regex_match(id, std::regex("[a-z0-9][a-z0-9_-]{0,63}"));
}
void validate_label(const std::string &label) {
    if (label.empty() || label.size() > 160 ||
        std::any_of(label.begin(), label.end(), [](unsigned char c) { return c < 32 || c == 127; }))
        throw std::invalid_argument("label must contain 1-160 printable UTF-8 bytes");
}
std::string https_origin(const std::string &value) {
    const auto origin = url_origin(value);
    if (!origin || *origin != value || !value.starts_with("https://") ||
        value.find_first_of("'\"\\\r\n\t ") != std::string::npos)
        throw std::invalid_argument("use an HTTPS origin without path, query or credentials");
    return value;
}
Json enrollment_result(const Json &node, const std::string &key, const std::string &origin) {
    return Json{{"id", node["id"]},
                {"key", key},
                {"expiresAt", node["expiresAt"]},
                {"command", "f=$(mktemp) && curl --fail --silent --show-error '" + origin +
                                "/enroll/agent.py' -o \"$f\" && { r=; [ \"$(id -u)\" -eq 0 ] || r=sudo; $r python3 \"$f\" --main '" +
                                origin + "' --node '" + node["id"].get<std::string>() +
                                "' --key '" + key + "'; }; rc=$?; rm -f \"$f\"; (exit \"$rc\")"}};
}
void persist(const std::filesystem::path &file, const Json &value) {
    auto temporary = file.string() + ".tmp";
    const int fd = ::open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0)
        throw std::runtime_error("cannot write node registry");
    const auto body = value.dump(2) + "\n";
    std::size_t at = 0;
    bool okay = ::fchmod(fd, 0600) == 0;
    while (okay && at < body.size()) {
        const auto n = ::write(fd, body.data() + at, body.size() - at);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0) {
            okay = false;
            break;
        }
        at += static_cast<std::size_t>(n);
    }
    if (::fsync(fd) != 0)
        okay = false;
    if (::close(fd) != 0)
        okay = false;
    if (!okay || ::rename(temporary.c_str(), file.c_str()) != 0) {
        ::unlink(temporary.c_str());
        throw std::runtime_error("cannot save node registry");
    }
}
} // namespace

NodeAdmin::NodeAdmin(const Config &cfg)
    : token_(cfg.admin_token), origin_(cfg.public_origin),
      file_(cfg.admin_state.empty() ? std::filesystem::path{}
                                    : std::filesystem::path(cfg.admin_state) / "nodes.json") {
    state_ = Json{{"version", 1}, {"nodes", Json::array()}};
    for (const auto &entry : cfg.nodes) {
        if (!entry.is_object())
            continue;
        auto node = entry;
        node["role"] = entry.value("id", "") == cfg.probe_only_id ? "probe" : "data";
        node["enabled"] = true;
        node["managed"] = false;
        node["registered"] = true;
        state_["nodes"].push_back(std::move(node));
    }
    if (token_.empty() && cfg.admin_state.empty() && origin_.empty())
        return;
    if (token_.size() < 32 || token_.size() > 256 || !file_.is_absolute())
        throw std::invalid_argument(
            "admin requires a 32-256 character token and absolute state directory");
    https_origin(origin_);
    if (std::filesystem::exists(file_)) {
        if (std::filesystem::file_size(file_) > 512 * 1024)
            throw std::runtime_error("node registry exceeds size limit");
        std::ifstream input(file_);
        input >> state_;
        if (state_.value("version", 0) != 1 || !state_.at("nodes").is_array() ||
            state_["nodes"].size() > 64)
            throw std::runtime_error("invalid node registry");
    } else {
        std::filesystem::create_directories(file_.parent_path());
        std::filesystem::permissions(file_.parent_path(), std::filesystem::perms::owner_all);
        persist(file_, state_);
    }
}
bool NodeAdmin::enabled() const { return !token_.empty(); }
void NodeAdmin::commit(Json next) {
    persist(file_, next);
    state_ = std::move(next);
}
Json NodeAdmin::public_nodes() const {
    std::lock_guard lock(mutex_);
    auto result = Json::array();
    for (const auto &node : state_["nodes"]) {
        if (!node.value("enabled", true) || !node.value("registered", false))
            continue;
        auto view = node;
        for (const auto *key : {"joinHash", "agentHash", "expiresAt", "registered", "enabled",
                                "managed", "udp4", "claimedAt"})
            view.erase(key);
        view["probeOnly"] = node.value("role", "data") == "probe";
        result.push_back(std::move(view));
    }
    return result;
}
Json NodeAdmin::listed() const {
    auto result = Json::array();
    const auto now = now_seconds();
    for (auto node : state_["nodes"]) {
        node.erase("joinHash");
        node.erase("agentHash");
        const auto id = node.value("id", "");
        const auto it = seen_.find(id);
        node["lastSeen"] = it == seen_.end() ? Json(nullptr) : Json(it->second);
        node["status"] = !node.value("managed", false) ? "configured"
                         : !node.value("registered", false)
                             ? (node.contains("claimedAt")                      ? "installing"
                                : node.value("expiresAt", std::int64_t{}) < now ? "expired"
                                                                                : "pending")
                         : it != seen_.end() && now - it->second < 90 ? "online"
                                                                      : "offline";
        result.push_back(std::move(node));
    }
    return result;
}
AdminReply NodeAdmin::handle(std::string_view method, std::string_view path,
                             std::string_view bearer, std::string_view request_origin,
                             const Json &body) {
    std::lock_guard lock(mutex_);
    if (!enabled())
        return {404, {{"error", "management is not configured"}}};
    if (path.starts_with("/api/admin/")) {
        if (!matches(bearer, digest(token_)))
            return {401, {{"error", "invalid administrator token"}}};
        if (!request_origin.empty() && request_origin != origin_)
            return {403, {{"error", "administrator origin is not allowed"}}};
    }
    try {
        if (path == "/api/admin/nodes" && method == "GET")
            return {200, {{"nodes", listed()}, {"origin", origin_}}};
        if (path == "/api/admin/enrollments" && method == "POST") {
            if (state_["nodes"].size() >= 64)
                throw std::invalid_argument("node limit reached (64)");
            const auto id = text(body, "id"), label = text(body, "label"),
                       base = https_origin(text(body, "base")), role = text(body, "role"),
                       ip = text(body, "udp4");
            if (!valid_id(id) || id == "local" || (role != "data" && role != "probe"))
                throw std::invalid_argument("invalid node ID or role");
            validate_label(label);
            if (base == origin_)
                throw std::invalid_argument("agent origin must differ from the main origin");
            boost::system::error_code ec;
            const auto addr = boost::asio::ip::make_address(ip, ec);
            if (ec || !addr.is_v4() || !is_public_ip(addr))
                throw std::invalid_argument("enter the agent public UDP IPv4 address");
            for (const auto &node : state_["nodes"])
                if (node.value("id", "") == id || node.value("base", "") == base)
                    throw std::invalid_argument("node ID or origin already exists");
            const auto key = secret();
            Json node{{"id", id},
                      {"label", label},
                      {"base", base},
                      {"role", role},
                      {"udp4", ip},
                      {"enabled", true},
                      {"managed", true},
                      {"registered", false},
                      {"expiresAt", now_seconds() + 3600},
                      {"joinHash", digest(key)}};
            auto next = state_;
            next["nodes"].push_back(node);
            commit(std::move(next));
            return {201, enrollment_result(node, key, origin_)};
        }
        if (path == "/api/admin/node" && method == "POST") {
            const auto id = text(body, "id"), action = text(body, "action");
            auto next = state_;
            auto &nodes = next["nodes"];
            auto it = std::find_if(nodes.begin(), nodes.end(),
                                   [&id](const Json &n) { return n.value("id", "") == id; });
            if (it == nodes.end())
                return {404, {{"error", "node not found"}}};
            if (action == "delete") {
                if (it->value("base", "").empty())
                    throw std::invalid_argument("the local node must remain available");
                nodes.erase(it);
                commit(std::move(next));
                seen_.erase(id);
                return {200, {{"ok", true}}};
            }
            if (action == "reset" && it->value("managed", false)) {
                const auto key = secret();
                (*it)["joinHash"] = digest(key);
                (*it)["expiresAt"] = now_seconds() + 3600;
                (*it)["registered"] = false;
                it->erase("agentHash");
                it->erase("claimedAt");
                auto reply = enrollment_result(*it, key, origin_);
                commit(std::move(next));
                seen_.erase(id);
                return {200, std::move(reply)};
            }
            if (action != "update")
                throw std::invalid_argument("invalid node action");
            const auto label = text(body, "label");
            validate_label(label);
            if (!body.contains("enabled") || !body["enabled"].is_boolean())
                throw std::invalid_argument("enabled must be boolean");
            if (it->value("base", "").empty() && body["enabled"] == false)
                throw std::invalid_argument("the local node must remain enabled");
            (*it)["label"] = label;
            (*it)["enabled"] = body["enabled"];
            commit(std::move(next));
            return {200, {{"ok", true}}};
        }
        if (method == "POST" &&
            (path == "/api/enrollment/claim" || path == "/api/enrollment/heartbeat" ||
             path == "/api/enrollment/activate")) {
            const auto id = text(body, "id");
            auto next = state_;
            auto &nodes = next["nodes"];
            auto it = std::find_if(nodes.begin(), nodes.end(), [&id](const Json &n) {
                return n.value("id", "") == id && n.value("managed", false);
            });
            if (it == nodes.end())
                return {401, {{"error", "invalid enrollment credential"}}};
            if (path == "/api/enrollment/claim") {
                if (!matches(bearer, it->value("joinHash", "")) ||
                    it->value("expiresAt", std::int64_t{}) <= now_seconds())
                    return {401, {{"error", "enrollment key is invalid or expired"}}};
                if (it->contains("claimedAt"))
                    return {409, {{"error", "enrollment key has already been used"}}};
                const auto key = secret();
                (*it)["agentHash"] = digest(key);
                (*it)["claimedAt"] = now_seconds();
                Json reply{{"id", id},
                           {"base", (*it)["base"]},
                           {"role", (*it)["role"]},
                           {"udp4", (*it)["udp4"]},
                           {"main", origin_},
                           {"agentKey", key}};
                commit(std::move(next));
                return {200, std::move(reply)};
            }
            if (!matches(bearer, it->value("agentHash", "")))
                return {401, {{"error", "invalid agent credential"}}};
            if (path == "/api/enrollment/activate") {
                (*it)["registered"] = true;
                commit(std::move(next));
            }
            seen_[id] = now_seconds();
            return {200, {{"ok", true}}};
        }
        return {404, {{"error", "not found"}}};
    } catch (const std::invalid_argument &error) {
        return {400, {{"error", error.what()}}};
    } catch (const Json::exception &) {
        return {400, {{"error", "invalid request fields"}}};
    } catch (const std::exception &) {
        return {503, {{"error", "node registry could not be saved"}}};
    }
}

AgentReporter::AgentReporter(const Config &cfg) {
    if (cfg.controller_origin.empty() && cfg.agent_id.empty() && cfg.agent_key.empty())
        return;
    https_origin(cfg.controller_origin);
    if (!valid_id(cfg.agent_id) || cfg.agent_key.size() != 64)
        throw std::invalid_argument("agent enrollment settings are incomplete");
    worker_ = std::jthread([this, url = cfg.controller_origin + "/api/enrollment/heartbeat",
                            body = Json{{"id", cfg.agent_id}}.dump(),
                            key = cfg.agent_key](std::stop_token stop) {
        while (!stop.stop_requested()) {
            CURL *curl = curl_easy_init();
            if (curl) {
                curl_slist *headers = nullptr;
                headers = curl_slist_append(headers, "Content-Type: application/json");
                headers = curl_slist_append(headers, ("Authorization: Bearer " + key).c_str());
                curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
                curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
                curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
                curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, static_cast<long>(body.size()));
                curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 5000L);
                curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 3000L);
                curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
                curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
                curl_easy_setopt(curl, CURLOPT_PROTOCOLS_STR, "https");
                curl_easy_setopt(
                    curl, CURLOPT_WRITEFUNCTION,
                    +[](char *, std::size_t size, std::size_t n, void *) -> std::size_t {
                        return size * n;
                    });
                curl_easy_perform(curl);
                curl_slist_free_all(headers);
                curl_easy_cleanup(curl);
            }
            std::unique_lock lock(mutex_);
            wake_.wait_for(lock, stop, std::chrono::seconds(30), [] { return false; });
        }
    });
}
AgentReporter::~AgentReporter() {
    worker_.request_stop();
    wake_.notify_all();
}
} // namespace speedtest
