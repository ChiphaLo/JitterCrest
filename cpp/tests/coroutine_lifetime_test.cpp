#include "coroutine_analysis_fixture.hpp"
#include <algorithm>
#include <atomic>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <iostream>
#include <thread>
#include <vector>

namespace speedtest::analysis_fixture {
void Session::start() {
    asio::co_spawn(socket_.get_executor(), run(),
                   [owner = shared_from_this()](const std::exception_ptr &error) {
                       try {
                           if (error)
                               std::rethrow_exception(error);
                       } catch (const boost::system::system_error &caught) {
                           owner->passed_ = caught.code() == asio::error::bad_descriptor;
                       } catch (...) {
                           owner->passed_ = false;
                       }
                   });
}

asio::awaitable<void> Session::send(Response out) {
    co_await http::async_write(socket_, out, asio::use_awaitable);
}
} // namespace speedtest::analysis_fixture

int main() try {
    namespace asio = boost::asio;
    asio::io_context io;
    std::vector<std::shared_ptr<speedtest::analysis_fixture::Session>> sessions;
    constexpr unsigned count = 10000;
    sessions.reserve(count);
    for (unsigned i = 0; i < count; ++i) {
        auto session = std::make_shared<speedtest::analysis_fixture::Session>(io, i % 2 == 0);
        session->start();
        sessions.push_back(std::move(session));
    }
    // Each socket has one owned operation chain; completion fields are read
    // only after every executor worker has joined. No detached OS threads.
    std::atomic_bool executor_failed{};
    auto run = [&io, &executor_failed] {
        try {
            io.run();
        } catch (...) {
            executor_failed.store(true);
            io.stop();
        }
    };
    std::vector<std::jthread> workers;
    for (unsigned i = 0; i < 2; ++i)
        workers.emplace_back(run);
    run();
    workers.clear();
    if (executor_failed.load() ||
        !std::all_of(sessions.begin(), sessions.end(), [](const auto &p) { return p->passed(); }))
        return 1;
    std::cout << "Actual Asio nested awaitable / Beast completion ownership: " << count
              << " completed (half timeout path, half nested response path)\n";
} catch (const std::exception &error) {
    std::cerr << "Coroutine fixture failed: " << error.what() << '\n';
    return 1;
} catch (...) {
    std::cerr << "Coroutine fixture failed with an unknown exception\n";
    return 1;
}
