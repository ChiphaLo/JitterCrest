#pragma once
#include "config.hpp"
#include <boost/asio/io_context.hpp>
#include <memory>

namespace speedtest {
// Public TLS listener that speaks HTTP/2 with large receive windows and relays
// every stream to the local HTTP/1.1 listener. A single browser upload stream
// is limited by the server's HTTP/2 window divided by the round-trip time, so
// the window must cover the path's bandwidth-delay product.
class TlsEdge {
  public:
    TlsEdge(boost::asio::io_context &io, const Config &cfg);
    ~TlsEdge();
    TlsEdge(const TlsEdge &) = delete;
    TlsEdge &operator=(const TlsEdge &) = delete;
    void start();
    void stop();

  private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
} // namespace speedtest
