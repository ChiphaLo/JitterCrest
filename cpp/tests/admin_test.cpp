#include "speedtest/admin.hpp"
#include "speedtest/client_info.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <unistd.h>

namespace {
void check(bool value) {
    if (!value)
        throw std::runtime_error("node administration assertion failed");
}
} // namespace
int main() {
    using speedtest::Json;
    const speedtest::CurlRuntime curl;
    const auto folder = std::filesystem::temp_directory_path() /
                        ("jittercrest-admin-test-" + std::to_string(::getpid()));
    std::filesystem::create_directory(folder);
    try {
        auto cfg = speedtest::Config::from_environment(
            [](std::string_view) { return std::optional<std::string>{}; });
        cfg.admin_token = std::string(40, 'a');
        cfg.public_origin = "https://main.example.com";
        cfg.admin_state = folder.string();
        speedtest::NodeAdmin admin(cfg);
        const auto call = [&](std::string_view method, std::string_view path, const Json &body,
                              std::string_view key) {
            return admin.handle(method, path, key, cfg.public_origin, body);
        };
        check(call("GET", "/api/admin/nodes", {}, "bad").status == 401);
        check(
            admin
                .handle("GET", "/api/admin/nodes", cfg.admin_token, "https://other.example.com", {})
                .status == 403);
        const Json node{{"id", "tokyo"},
                        {"label", "Tokyo 日本"},
                        {"base", "https://node.example.com"},
                        {"udp4", "8.8.8.8"},
                        {"role", "data"}};
        auto bad = node;
        bad["base"] = "https://node.example.com/a";
        check(call("POST", "/api/admin/enrollments", bad, cfg.admin_token).status == 400);
        bad = node;
        bad["udp4"] = "127.0.0.1";
        check(call("POST", "/api/admin/enrollments", bad, cfg.admin_token).status == 400);
        const auto created = call("POST", "/api/admin/enrollments", node, cfg.admin_token);
        check(created.status == 201);
        const auto join = created.body.at("key").get<std::string>();
        check(join.size() == 64 && admin.public_nodes().size() == 1);
        check(call("POST", "/api/admin/enrollments", node, cfg.admin_token).status == 400);
        check(call("POST", "/api/enrollment/claim", {{"id", "tokyo"}}, "wrong").status == 401);
        const auto claimed = call("POST", "/api/enrollment/claim", {{"id", "tokyo"}}, join);
        check(claimed.status == 200 && claimed.body.at("base") == node["base"]);
        check(call("POST", "/api/enrollment/claim", {{"id", "tokyo"}}, join).status == 409);
        const auto agent = claimed.body.at("agentKey").get<std::string>();
        check(call("POST", "/api/enrollment/activate", {{"id", "tokyo"}}, join).status == 401);
        check(call("POST", "/api/enrollment/activate", {{"id", "tokyo"}}, agent).status == 200);
        check(admin.public_nodes().size() == 2);
        const auto listed = call("GET", "/api/admin/nodes", {}, cfg.admin_token).body.dump();
        check(listed.find(join) == std::string::npos && listed.find(agent) == std::string::npos);
        check(listed.find("online") != std::string::npos);
        const Json update{
            {"id", "tokyo"}, {"action", "update"}, {"label", "New name"}, {"enabled", false}};
        check(call("POST", "/api/admin/node", update, cfg.admin_token).status == 200);
        check(admin.public_nodes().size() == 1);
        {
            speedtest::NodeAdmin reloaded(cfg);
            check(reloaded.public_nodes().size() == 1);
            check(reloaded.handle("GET", "/api/admin/nodes", cfg.admin_token, "", {})
                      .body.dump()
                      .find("New name") != std::string::npos);
        }
        const auto reset = call("POST", "/api/admin/node", {{"id", "tokyo"}, {"action", "reset"}},
                                cfg.admin_token);
        check(reset.status == 200 && reset.body["key"] != join);
        check(call("POST", "/api/enrollment/heartbeat", {{"id", "tokyo"}}, agent).status == 401);
        check(call("POST", "/api/admin/node", {{"id", "tokyo"}, {"action", "delete"}},
                   cfg.admin_token)
                  .status == 200);
        check(call("POST", "/api/admin/node", {{"id", "local"}, {"action", "delete"}},
                   cfg.admin_token)
                  .status == 400);
        std::ifstream input(folder / "nodes.json");
        const std::string disk((std::istreambuf_iterator<char>(input)), {});
        check(disk.find(join) == std::string::npos && disk.find(agent) == std::string::npos);
        check((std::filesystem::status(folder / "nodes.json").permissions() &
               std::filesystem::perms::group_read) == std::filesystem::perms::none);
        auto expired_node = node;
        expired_node["id"] = "expired";
        const auto expired_key =
            call("POST", "/api/admin/enrollments", expired_node, cfg.admin_token)
                .body.at("key")
                .get<std::string>();
        Json saved;
        std::ifstream(folder / "nodes.json") >> saved;
        saved["nodes"].back()["expiresAt"] = 1;
        std::ofstream(folder / "nodes.json") << saved;
        speedtest::NodeAdmin expired(cfg);
        check(expired.handle("POST", "/api/enrollment/claim", expired_key, "", {{"id", "expired"}})
                  .status == 401);
        std::filesystem::remove_all(folder);
        std::cout << "PASS administrator authentication, one-time enrollment, activation, "
                     "management and persistence\n";
    } catch (...) {
        std::filesystem::remove_all(folder);
        throw;
    }
}
