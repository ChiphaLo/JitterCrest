#pragma once
#include "config.hpp"
#include <condition_variable>
#include <filesystem>
#include <map>
#include <mutex>
#include <thread>

namespace speedtest {
struct AdminReply {
    unsigned status;
    Json body;
};
class NodeAdmin {
  public:
    explicit NodeAdmin(const Config &cfg);
    [[nodiscard]] bool enabled() const;
    [[nodiscard]] Json public_nodes() const;
    [[nodiscard]] AdminReply handle(std::string_view method, std::string_view path,
                                    std::string_view bearer, std::string_view origin,
                                    const Json &body);

  private:
    void commit(Json next);
    Json listed() const;
    const std::string token_, origin_;
    const std::filesystem::path file_;
    mutable std::mutex mutex_;
    Json state_;
    std::map<std::string, std::int64_t> seen_;
};
class AgentReporter {
  public:
    explicit AgentReporter(const Config &cfg);
    ~AgentReporter();

  private:
    std::mutex mutex_;
    std::condition_variable_any wake_;
    std::jthread worker_;
};
} // namespace speedtest
