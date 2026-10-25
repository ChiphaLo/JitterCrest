#include "speedtest/client_info.hpp"
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/post.hpp>
#include <boost/asio/use_future.hpp>
#include <condition_variable>
#include <future>
#include <iostream>
#include <thread>

namespace {
void require(bool value) {
    if (!value)
        throw std::runtime_error("client-info fixture mismatch");
}
speedtest::Json lookup(const std::shared_ptr<speedtest::ClientInfo> &info, std::string ip) {
    boost::asio::io_context io;
    auto future = boost::asio::co_spawn(io, info->lookup(std::move(ip)), boost::asio::use_future);
    io.run();
    return future.get();
}
} // namespace
int main() {
    using namespace speedtest;
    try {
        const CurlRuntime runtime;
        require(url_origin("https://EXAMPLE.invalid:443/path?q=1") == "https://example.invalid");
        require(url_origin("http://example.invalid:8080/a") == "http://example.invalid:8080");
        require(url_origin("http://[2001:0db8:0000::1]:80/") == "http://[2001:db8::1]");
        for (const auto *invalid :
             {"javascript:alert(1)", "https://user@example.invalid/",
              "https://:pw@example.invalid/", "https://example.invalid:99999/",
              "https://example.invalid'; evil/", "not-a-url"})
            require(!url_origin(invalid));
        unsigned calls = 0;
        auto primary = std::make_shared<ClientInfo>([&calls](const std::string &, long timeout) {
            require(timeout == 1800);
            ++calls;
            return Json{{"success", true},
                        {"country_code", " us "},
                        {"country", "United\nStates"},
                        {"connection",
                         {{"asn", "as 1234 rest"}, {"org", "Provider"}, {"isp", " ISP \t One "}}}};
        });
        const auto a = lookup(primary, "192.0.2.1");
        require(a["asn"] == "AS1234" && a["asName"] == "Provider" && a["isp"] == "ISP One" &&
                a["country"] == "United States" && a["countryCode"] == "US" && a["ok"] == true);
        require(lookup(primary, "192.0.2.1") == a && calls == 1);
        primary->shutdown();
        primary->join();
        auto fallback = std::make_shared<ClientInfo>([](const std::string &url, long timeout) {
            if (timeout == 1800)
                return Json{
                    {"success", true},
                    {"connection", {{"asn", "as 1234 rest"}, {"org", "  "}, {"isp", "ISP One"}}}};
            require(timeout == 2500 && url.find("ipinfo.io/") != std::string::npos);
            return Json{{"org", "as7890  Fallback  Org "}};
        });
        const auto b = lookup(fallback, "192.0.2.2");
        require(b["asn"] == "AS1234" && b["asName"] == "Fallback Org" && b["isp"] == "ISP One");
        fallback->shutdown();
        fallback->join();
        auto missing =
            std::make_shared<ClientInfo>([](const std::string &, long) { return Json(nullptr); });
        require(lookup(missing, "192.0.2.3")["ok"] == false);
        missing->shutdown();
        missing->join();
        std::mutex mutex;
        std::condition_variable ready;
        bool release = false;
        unsigned active = 0, peak = 0;
        auto bounded = std::make_shared<ClientInfo>([&](const std::string &, long) {
            std::unique_lock lock(mutex);
            ++active;
            peak = std::max(peak, active);
            ready.notify_all();
            ready.wait(lock, [&] { return release; });
            --active;
            return Json{{"success", true}, {"connection", {{"asn", 123}, {"org", "Provider"}}}};
        });
        boost::asio::io_context io;
        std::vector<std::future<Json>> requests;
        for (unsigned i = 0; i < 8; ++i)
            requests.push_back(boost::asio::co_spawn(
                io, bounded->lookup("192.0.2." + std::to_string(i)), boost::asio::use_future));
        std::promise<void> dispatched;
        auto dispatch_done = dispatched.get_future();
        boost::asio::post(io, [&] { dispatched.set_value(); });
        std::jthread worker([&] { io.run(); });
        dispatch_done.wait();
        bool admitted = false;
        {
            std::unique_lock lock(mutex);
            admitted = ready.wait_for(lock, std::chrono::seconds(3), [&] { return active == 4; });
            release = true;
            ready.notify_all();
        }
        unsigned failed = 0;
        for (auto &request : requests)
            if (request.get()["ok"] == false)
                ++failed;
        worker.join();
        require(admitted && peak == 4 && failed == 4);
        bounded->shutdown();
        bounded->join();
        require(lookup(bounded, "192.0.2.99")["ok"] == false);
        std::cout << "client-info normalization, cache, bounded workers and shutdown passed\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
