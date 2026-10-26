#include "speedtest/client_info.hpp"
#include "speedtest/http.hpp"
#include "speedtest/rtc.hpp"
#include <boost/asio/signal_set.hpp>
#include <csignal>
#include <iostream>
#include <syncstream>
#include <thread>

int main() {
    try {
        const speedtest::CurlRuntime curl;
        const auto cfg = speedtest::Config::load();
        boost::asio::io_context io(static_cast<int>(cfg.io_threads));
        speedtest::RtcRuntime native_rtc(cfg.rtc_experimental,
                                         static_cast<unsigned>(cfg.io_threads));
        boost::asio::signal_set signals(io, SIGINT, SIGTERM);
        speedtest::HttpServer server(io, cfg, [&signals] { signals.cancel(); });
        signals.async_wait([&server](const boost::system::error_code &error, int) {
            if (!error)
                server.stop();
        });
        server.start();
        std::clog << speedtest::Json{{"event", "http_listener_started"},
                                     {"address", cfg.host + ":" + std::to_string(cfg.port)},
                                     {"web_ui", speedtest::assets::web_ui()},
                                     {"rtc_native_enabled", cfg.rtc_experimental}}
                         .dump()
                  << '\n';
        auto run = [&io, &server] {
            for (;;)
                try {
                    io.run();
                    break;
                } catch (const std::exception &) {
                    std::osyncstream(std::clog) << "{\"event\":\"executor_error\"}\n";
                    server.stop();
                }
        };
        std::vector<std::jthread> workers;
        for (std::uint64_t i = 1; i < cfg.io_threads; ++i)
            workers.emplace_back(run);
        run();
        workers.clear();
        native_rtc.finish(io);
        std::clog << "{\"event\":\"http_listener_stopped\"}\n";
        return server.failed() ? 1 : 0;
    } catch (const std::exception &error) {
        // Configuration/startup errors contain setting names, not credentials.
        std::cerr << speedtest::Json{{"event", "startup_failed"}, {"error", error.what()}}.dump()
                  << '\n';
        return 1;
    }
}
