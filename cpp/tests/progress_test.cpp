#include "speedtest/progress.hpp"
#include <iostream>
#include <thread>
namespace {
void require(bool value) {
    if (!value)
        throw std::runtime_error("progress invariant failed");
}
} // namespace
int main() {
    using namespace speedtest;
    ProgressRegistry registry;
    const auto id = registry.create("one", {.lanes = 2, .warmup_ms = 0, .maximum = 2, .per_ip = 1});
    require(!registry.get({.owner = "other", .id = id}));
    {
        auto first = registry.attach("one", id, 0);
        first.received(1000);
        first.success();
    }
    require(!registry.get({.owner = "one", .id = id})->snapshot()["done"].get<bool>());
    {
        auto second = registry.attach("one", id, 1);
        second.received(2000);
        second.success();
    }
    auto p = registry.get({.owner = "one", .id = id});
    auto snap = p->snapshot();
    require(snap["done"] && !snap["failed"].get<bool>() && snap["bytes"] == 3000 &&
            snap["measuredBytes"] == 3000);
    require(snap["elapsedMs"] == p->snapshot()["elapsedMs"]);
    const auto chunks = registry.create(
        "two", {.lanes = 2, .reusable = true, .warmup_ms = 0, .maximum = 2, .per_ip = 1});
    std::vector<std::jthread> threads;
    for (std::uint8_t lane = 0; lane < 2; ++lane)
        threads.emplace_back([&registry, &chunks, lane] {
            for (int i = 0; i < 100; ++i) {
                auto receipt = registry.attach("two", chunks, lane);
                receipt.received(1024);
                receipt.success();
            }
        });
    threads.clear();
    registry.finish("two", chunks);
    registry.finish("two", chunks);
    const auto end = registry.get({.owner = "two", .id = chunks})->snapshot();
    require(end["bytes"] == 204800 && end["done"] && !end["failed"].get<bool>());
    std::cout << "owned progress receipts and concurrent lanes passed\n";
}
