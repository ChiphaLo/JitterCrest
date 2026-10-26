#include "speedtest/limits.hpp"
#include <iostream>
#include <thread>
namespace {
void require(bool value) {
    if (!value)
        throw std::runtime_error("limits invariant failed");
}
} // namespace
int main() {
    using namespace speedtest;
    auto cfg = Config::from_environment(
        [](std::string_view) -> std::optional<std::string> { return std::nullopt; });
    cfg.max_active_global = 2;
    cfg.max_active_per_ip = 1;
    cfg.download_budget = 100;
    cfg.upload_budget = 80;
    Limits limits(cfg);
    {
        auto first = limits.active("one");
        require(std::holds_alternative<ActiveLease>(first));
        require(std::get<ActiveError>(limits.active("one")) == ActiveError::per_ip);
        auto second = limits.active("two");
        require(limits.active_count() == 2);
        require(std::get<ActiveError>(limits.active("three")) == ActiveError::global);
    }
    require(limits.active_count() == 0);
    {
        auto r = limits.reserve_download("one", 100);
        require(r.has_value());
        require(!limits.consume("one", 1, Direction::download));
        r->produced(40);
    }
    require(limits.consume("one", 60, Direction::download));
    require(!limits.consume("one", 1, Direction::download));
    require(limits.consume("one", 80, Direction::upload));
    require(!limits.consume("one", 1, Direction::upload));
    RateTable rate;
    const auto now = Clock::now();
    const RateOptions standard{.maximum = 1, .map_maximum = 2};
    require(rate.check("a", standard, now));
    require(!rate.check("a", standard, now + std::chrono::seconds(10)));
    require(rate.check("a", standard, now + std::chrono::milliseconds(10001)));
    require(rate.check("b", standard, now));
    require(rate.check("c", standard, now));
    require(rate.size() == 2);
    RateTable cancel;
    const RateOptions c{.maximum = 1, .map_maximum = 2, .kind = RateKind::cancel};
    require(cancel.check("a", c, now));
    require(cancel.check("a", c, now + std::chrono::seconds(10)));
    cfg.max_active_global = 128;
    cfg.max_active_per_ip = 16;
    Limits concurrent(cfg);
    std::vector<std::jthread> threads;
    for (int i = 0; i < 4; ++i)
        threads.emplace_back([&concurrent] {
            for (int n = 0; n < 1000; ++n) {
                auto lease = concurrent.active("shared");
                require(std::holds_alternative<ActiveLease>(lease));
                (void)concurrent.rate("shared", true);
            }
        });
    threads.clear();
    require(concurrent.active_count() == 0);
    std::cout << "RAII limits and concurrent accounting passed\n";
}
