#include <rtc/utils.hpp>
#include <atomic>
#include <functional>
#include <string>
#include <thread>

int main() {
    rtc::synchronized_stored_callback<> callback;
    std::atomic<unsigned> calls{};
    const std::function<void()> increment = [&calls] { ++calls; };
    callback();
    callback = increment;
    if (calls != 1)
        return 1; // A missed event must replay once on callback installation.
    std::jthread producer([&callback] {
        for (unsigned i = 0; i < 100000; ++i)
            callback();
    });
    std::jthread resetter([&callback, &increment] {
        for (unsigned i = 0; i < 100000; ++i) {
            callback = nullptr;
            callback = increment;
        }
    });
    std::jthread copier([&callback] {
        for (unsigned i = 0; i < 10000; ++i) {
            const rtc::synchronized_stored_callback<> copy(callback);
            callback = copy;
        }
    });
    producer.join();
    resetter.join();
    copier.join();
    rtc::synchronized_stored_callback<std::string> strings;
    strings("retained event");
    const rtc::synchronized_stored_callback<std::string> snapshot(strings);
    std::string received;
    strings = snapshot;
    strings = std::function<void(std::string)>([&received](std::string text) {
        received = std::move(text);
    });
    if (received != "retained event")
        return 1;
}
