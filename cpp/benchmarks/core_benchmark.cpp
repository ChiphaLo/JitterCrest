#include "speedtest/core.hpp"
#include <atomic>
#include <bit>
#include <chrono>
#include <iostream>
#include <stdexcept>

namespace {
using speedtest::Json;
struct Workload {
    std::string operation;
    std::vector<std::vector<std::byte>> frames;
    std::vector<std::string> texts;
    std::vector<double> values;
    std::uint64_t run(std::uint64_t iterations) {
        std::uint64_t checksum = 0;
        if (operation == "decode") {
            for (std::uint64_t i = 0; i < iterations; ++i) {
                std::uint64_t value{};
                const auto &bytes = frames[static_cast<std::size_t>(i % frames.size())];
                if (const auto frame = speedtest::Frame::decode(bytes))
                    value = std::bit_cast<std::uint64_t>(frame->sent_at) +
                            (static_cast<std::uint64_t>(frame->session_hash) << 32U) +
                            frame->test_hash + frame->seq + frame->actual_sent +
                            (static_cast<std::uint64_t>(frame->kind) << 8U) + frame->flags;
                else
                    value = i;
                checksum = std::rotl(checksum, 7) ^ value;
            }
        } else if (operation == "hash") {
            for (std::uint64_t i = 0; i < iterations; ++i) {
                const auto value =
                    speedtest::hash_string32(texts[static_cast<std::size_t>(i % texts.size())]);
                checksum = std::rotl(checksum, 7) ^ value;
            }
        } else {
            for (std::uint64_t i = 0; i < iterations; ++i) {
                values[0] = static_cast<double>(i % 1024U) / 64.0;
                const auto percentile = speedtest::percentile(values, 0.95);
                if (!percentile)
                    throw std::logic_error("finite percentile fixture produced no result");
                const auto value = std::bit_cast<std::uint64_t>(*percentile);
                checksum = std::rotl(checksum, 7) ^ value;
            }
        }
        // Consume all outputs; runtime inputs prevent constant folding. Dispatch
        // is outside the loop in BOTH drivers. No assembly/unequal barriers.
        return checksum;
    }
};
} // namespace

int main() {
    try {
        Json input;
        std::cin >> input;
        Workload work;
        work.operation = input.at("operation").get<std::string>();
        const auto iterations = input.at("iterations").get<std::uint64_t>();
        if (iterations == 0 || iterations > 1000000000ULL)
            throw std::invalid_argument("iterations outside benchmark bound");
        if (work.operation == "decode") {
            for (const auto &encoded : input.at("frames")) {
                const auto hex = encoded.get<std::string>();
                if (hex.size() > 512 || hex.size() % 2 != 0)
                    throw std::invalid_argument("invalid fixture hex length");
                std::vector<std::byte> bytes;
                for (std::size_t i = 0; i < hex.size(); i += 2)
                    bytes.push_back(
                        static_cast<std::byte>(std::stoul(hex.substr(i, 2), nullptr, 16)));
                work.frames.push_back(std::move(bytes));
            }
            if (work.frames.empty())
                throw std::invalid_argument("empty frame dataset");
        } else if (work.operation == "hash") {
            work.texts = input.at("texts").get<std::vector<std::string>>();
            if (work.texts.empty())
                throw std::invalid_argument("empty hash dataset");
        } else if (work.operation == "percentile") {
            work.values = input.at("values").get<std::vector<double>>();
            if (work.values.empty())
                throw std::invalid_argument("empty percentile dataset");
        } else
            throw std::invalid_argument("unknown benchmark operation");
        const auto warmup_checksum = work.run(iterations / 10U);
        const auto start = std::chrono::steady_clock::now();
        std::atomic_signal_fence(std::memory_order_seq_cst);
        const auto checksum = work.run(iterations);
        std::atomic_signal_fence(std::memory_order_seq_cst);
        const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
                                 std::chrono::steady_clock::now() - start)
                                 .count();
        std::cout << Json{{"checksum", checksum},
                          {"warmup_checksum", warmup_checksum},
                          {"iterations", iterations},
                          {"elapsed_ns", elapsed}}
                         .dump()
                  << '\n';
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
