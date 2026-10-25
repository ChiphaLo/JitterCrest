#include <boost/asio/detail/atomic_count.hpp>
#include <thread>

namespace {
struct Counter {
    boost::asio::detail::atomic_count references{2};
};
void release(Counter *counter) {
    if (boost::asio::detail::ref_count_down(counter->references))
        delete counter;
}
} // namespace

int main() {
    // Same lifetime pattern as Asio shared_target_executor. Each actor owns
    // exactly one reference; the last decrement owns destruction. The original
    // release+fence implementation reproduces LLVM 18's unmodeled-fence report.
    for (unsigned iteration = 0; iteration < 500; ++iteration) {
        auto *counter = new Counter;
        std::jthread worker([counter] { release(counter); });
        release(counter);
    }
}
