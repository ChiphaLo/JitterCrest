#pragma once
#include "config.hpp"
#include <boost/asio/io_context.hpp>
#include <memory>

namespace speedtest {
class HttpServer {
  public:
    HttpServer(boost::asio::io_context &io, Config cfg, std::function<void()> stopped = {});
    ~HttpServer();
    void start();
    void stop();
    [[nodiscard]] bool failed() const;

  private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
namespace assets {
[[nodiscard]] bool web_ui();
[[nodiscard]] std::string page();
[[nodiscard]] std::string_view icon();
} // namespace assets
} // namespace speedtest
