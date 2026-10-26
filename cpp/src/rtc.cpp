#include "speedtest/rtc.hpp"
#include "speedtest/control.hpp"
#include "speedtest/limits.hpp"
#include "speedtest/sdp.hpp"
#include <algorithm>
#include <atomic>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/executor_work_guard.hpp>
#include <boost/asio/post.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/strand.hpp>
#include <boost/asio/thread_pool.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <deque>
#include <ifaddrs.h>
#include <limits>
#include <map>
#include <net/if.h>
#include <netinet/in.h>
#include <rtc/rtc.hpp>
#include <set>
#include <syncstream>

namespace speedtest {
namespace {
namespace asio = boost::asio;
using asio::awaitable;
using asio::use_awaitable;
using Error = boost::system::error_code;
using Millis = std::chrono::milliseconds;
using Key = std::pair<std::string, std::string>;
using TestKey = std::pair<std::uint32_t, std::uint32_t>;
using Executor = asio::strand<asio::any_io_executor>;
void log_event(std::string_view event) noexcept {
    try {
        std::osyncstream(std::clog) << Json{{"event", event}}.dump() << '\n';
    } catch (...) {
        static_cast<void>(std::fputs("{\"event\":\"rtc_logger_failed\"}\n", stderr));
    }
}
double milliseconds(Clock::time_point epoch) {
    return std::chrono::duration<double, std::milli>(Clock::now() - epoch).count();
}
std::optional<std::uint32_t> strict_hash(const Json &value) {
    if (!value.is_number_integer())
        return std::nullopt;
    if (value.is_number_unsigned()) {
        const auto n = value.get<std::uint64_t>();
        if (n <= UINT32_MAX)
            return static_cast<std::uint32_t>(n);
    } else {
        const auto n = value.get<std::int64_t>();
        if (n >= 0 && n <= UINT32_MAX)
            return static_cast<std::uint32_t>(n);
    }
    return std::nullopt;
}
double bounded_number(const Json &value, double fallback, double low, double high) {
    const double n = value.is_number() ? value.get<double>() : fallback;
    return std::clamp(std::floor(std::isfinite(n) ? n : fallback), low, high);
}
struct IfDelete {
    void operator()(ifaddrs *p) const {
        freeifaddrs(p);
    }
};
std::string mapping_interface(const std::string &public_ip) {
    ifaddrs *raw = nullptr;
    if (getifaddrs(&raw) != 0)
        throw std::runtime_error("interface enumeration failed");
    const std::unique_ptr<ifaddrs, IfDelete> interfaces(raw);
    std::string fallback;
    for (auto *item = interfaces.get(); item; item = item->ifa_next) {
        if (!item->ifa_addr || item->ifa_addr->sa_family != AF_INET ||
            (item->ifa_flags & IFF_UP) == 0 || (item->ifa_flags & IFF_LOOPBACK) != 0)
            continue;
        std::string name(item->ifa_name);
        std::transform(name.begin(), name.end(), name.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        bool excluded = false;
        for (const auto prefix : {"tailscale", "docker", "br-", "tun", "lo"})
            if (name.starts_with(prefix))
                excluded = true;
        if (excluded)
            continue;
        // sockaddr family was checked above. Copy into the correctly typed
        // object rather than aliasing the OS-owned sockaddr as another type.
        sockaddr_in address{};
        std::memcpy(&address, item->ifa_addr, sizeof(address));
        asio::ip::address_v4::bytes_type bytes{};
        std::memcpy(bytes.data(), &address.sin_addr, bytes.size());
        const auto ip = asio::ip::address_v4(bytes).to_string();
        if (ip == public_ip)
            return ip;
        if (fallback.empty())
            fallback = ip;
    }
    if (fallback.empty())
        throw std::runtime_error("no eligible mapped IPv4 interface");
    return fallback;
}
std::string advertised_sdp(std::string sdp, const Config &cfg) {
    if (!cfg.rtc_udp4)
        return sdp;
    std::string out;
    std::size_t at = 0;
    while (at < sdp.size()) {
        const auto end = sdp.find('\n', at);
        const auto n = end == std::string::npos ? sdp.size() - at : end - at;
        auto line = trim_text(std::string_view(sdp).substr(at, n));
        if (line.starts_with("a=candidate:")) {
            rtc::Candidate candidate(std::string(line.substr(2)));
            const bool resolved = candidate.resolve(rtc::Candidate::ResolveMode::Simple);
            const auto port = candidate.port();
            if (resolved && candidate.family() == rtc::Candidate::Family::Ipv4 &&
                candidate.transportType() == rtc::Candidate::TransportType::Udp && port) {
                candidate.changeAddress(*cfg.rtc_udp4, *port);
                out += "a=" + candidate.candidate() + "\r\n";
            }
        } else {
            out += line;
            out += "\r\n";
        }
        if (end == std::string::npos)
            break;
        at = end + 1;
    }
    return out;
}
struct Measurement {
    Measurement(const Executor &executor)
        : schedule_timer(executor), sample_timer(executor), gate(executor) {}
    MeasurementIdentity identity;
    std::uint32_t session_hash{}, test_hash{};
    std::uint64_t duration_ms{};
    double interval_ms{};
    std::uint64_t scheduled{}, skipped{}, actual_sent{};
    std::set<std::uint32_t> sent_seqs, upload_received;
    std::vector<double> sent_times, delays;
    std::map<std::uint32_t, double> acks;
    std::optional<std::uint32_t> max_up_seq;
    std::optional<Json> upload_complete;
    std::optional<Clock::time_point> upload_at;
    std::optional<double> upload_at_ms;
    std::vector<std::string> invalid;
    bool cancelled{}, download_done{}, finalized{}, scheduler_done{}, sampler_done{};
    asio::steady_timer schedule_timer, sample_timer, gate;
    void notify() {
        gate.cancel();
    }
    void stop() {
        cancelled = true;
        schedule_timer.cancel();
        sample_timer.cancel();
        gate.cancel();
    }
};
struct Peer;
} // namespace

struct RtcService::Impl : std::enable_shared_from_this<RtcService::Impl> {
    Impl(const asio::any_io_executor &executor, Config config)
        : strand(asio::make_strand(executor)), cfg(std::move(config)) {
        if (cfg.rtc_experimental)
            preparation = std::make_unique<asio::thread_pool>(2);
    }
    awaitable<Json> offer(std::string ip, std::string attempt, std::string sdp, bool probe_only);
    awaitable<void> cancel(std::string ip, std::string attempt);
    awaitable<void> shutdown();
    void cleanup(Key key);
    void expire();
    void remember(Key key);
    Executor strand;
    const Config cfg;
    const Clock::time_point epoch = Clock::now();
    std::map<Key, std::shared_ptr<Peer>> peers;
    std::map<Key, Clock::time_point> tombstones;
    std::deque<Key> tombstone_order;
    RateTable offers, cancels;
    bool stopping{};
    std::uint64_t preparing_count{};
    std::unique_ptr<asio::thread_pool> preparation;
};

namespace {
struct Peer : std::enable_shared_from_this<Peer> {
    Peer(Executor executor, Config config, Key identity, Clock::time_point time)
        : strand(std::move(executor)), cfg(std::move(config)), key(std::move(identity)),
          epoch(time), session_timer(strand), negotiation_gate(strand),
          reassembler({.fragments = static_cast<std::size_t>(cfg.control_fragment_count),
                       .bytes = static_cast<std::size_t>(cfg.control_reassembled_bytes)}) {}
    void close() {
        if (closed)
            return;
        closed = true;
        cancelled = true;
        session_timer.cancel();
        negotiation_gate.cancel();
        for (const auto &[id, m] : tests) {
            static_cast<void>(id);
            m->stop();
        }
        if (control)
            control->resetCallbacks();
        if (test)
            test->resetCallbacks();
        control.reset();
        test.reset();
        if (connection) {
            connection->resetCallbacks();
            try {
                connection->close();
            } catch (...) {
                log_event("rtc_close_failed");
            }
            connection.reset();
        }
    }
    void fail(std::string_view reason) {
        if (closed)
            return;
        log_event(reason);
        if (cleanup)
            cleanup();
    }
    void post_message(std::string label, rtc::message_variant message) noexcept;
    void install(const std::shared_ptr<rtc::DataChannel> &channel);
    awaitable<void> on_message(std::string label, rtc::message_variant message);
    awaitable<bool> send(std::shared_ptr<rtc::DataChannel> channel, rtc::message_variant message,
                         std::shared_ptr<Measurement> state = {});
    awaitable<void> start_down(Json message);
    void accept_upload(const Json &message);
    awaitable<void> scheduler(std::shared_ptr<Measurement> state,
                              std::shared_ptr<rtc::DataChannel> channel);
    awaitable<void> sampler(std::shared_ptr<Measurement> state);
    awaitable<void> finalizer(std::shared_ptr<Measurement> state);
    Json transport() const;
    Executor strand;
    const Config cfg;
    const Key key;
    const Clock::time_point epoch;
    asio::steady_timer session_timer, negotiation_gate;
    std::shared_ptr<rtc::PeerConnection> connection;
    std::shared_ptr<rtc::DataChannel> control, test;
    std::atomic<std::uint64_t> inbound_bytes{}, inbound_test{}, inbound_control{};
    std::atomic<bool> violation_posted{}, cancelled{};
    bool closed{}, prepared{}, gathered{}, preparation_failed{};
    std::optional<Json> pending;
    std::uint64_t down_starts{};
    std::map<TestKey, std::shared_ptr<Measurement>> tests;
    Reassembler reassembler;
    std::function<void()> cleanup;
};
} // namespace

RtcRuntime::RtcRuntime(bool enabled, unsigned workers) : enabled_(enabled) {
    if (enabled_) {
        rtc::InitLogger(rtc::LogLevel::None);
        rtc::SetThreadPoolSize(std::max(1U, workers));
        // The reference T3 DATA timer does not abort on a retry count. Keep
        // the association alive within our bounded peer/session lifetime,
        // rather than libdatachannel's five-retry early-abort default. This
        // does not change per-message zero-retransmission probe reliability.
        rtc::SctpSettings settings;
        settings.maxRetransmitAttempts = std::numeric_limits<std::uint16_t>::max();
        rtc::SetSctpSettings(settings);
        rtc::Preload();
    }
}
RtcRuntime::~RtcRuntime() {
    if (enabled_) {
        try {
            if (rtc::Cleanup().wait_for(std::chrono::seconds(5)) != std::future_status::ready)
                log_event("rtc_native_cleanup_timeout");
        } catch (...) {
            log_event("rtc_native_cleanup_failed");
        }
    }
}
void RtcRuntime::finish(asio::io_context &io) {
    if (!enabled_)
        return;
    auto completion = rtc::Cleanup();
    const auto warning = Clock::now() + std::chrono::seconds(5);
    bool warned = false;
    // Keep the executor alive while native callbacks finish; a late callback
    // must never post through an executor whose context has been destroyed.
    while (completion.wait_for(Millis(1)) != std::future_status::ready) {
        io.restart();
        static_cast<void>(io.poll());
        if (!warned && Clock::now() >= warning) {
            log_event("rtc_native_cleanup_delayed");
            warned = true;
        }
    }
    io.restart();
    static_cast<void>(io.poll());
    enabled_ = false;
}
void RtcService::Impl::expire() {
    while (!tombstone_order.empty()) {
        const auto it = tombstones.find(tombstone_order.front());
        if (it != tombstones.end() && Clock::now() - it->second < std::chrono::seconds(30))
            break;
        tombstones.erase(tombstone_order.front());
        tombstone_order.pop_front();
    }
}
void RtcService::Impl::remember(Key key) {
    expire();
    if (tombstones.contains(key))
        return;
    while (tombstones.size() >= cfg.rtc_tombstones_max && !tombstone_order.empty()) {
        tombstones.erase(tombstone_order.front());
        tombstone_order.pop_front();
    }
    tombstones.emplace(key, Clock::now());
    tombstone_order.push_back(std::move(key));
}
void RtcService::Impl::cleanup(Key key) {
    const auto it = peers.find(key);
    if (it == peers.end())
        return;
    auto peer = std::move(it->second);
    peers.erase(it);
    remember(std::move(key));
    peer->close();
    log_event("rtc_peer_cleaned");
}
awaitable<void> RtcService::Impl::cancel(std::string ip, std::string attempt) {
    const Key key{std::move(ip), std::move(attempt)};
    expire();
    if (peers.contains(key))
        cleanup(key);
    else if (!tombstones.contains(key)) {
        if (!cancels.check(key.first,
                           {.maximum = static_cast<std::uint32_t>(cfg.rtc_cancel_max_per_ip),
                            .map_maximum = static_cast<std::size_t>(cfg.rate_map_max),
                            .kind = RateKind::cancel}))
            throw RtcFailure(429, "too many WebRTC cancels");
        remember(key);
    }
    co_return;
}
awaitable<void> RtcService::Impl::shutdown() {
    stopping = true;
    while (!peers.empty())
        cleanup(peers.begin()->first);
    log_event("rtc_service_stopped");
    co_return;
}
awaitable<Json> RtcService::Impl::offer(std::string ip, std::string attempt, std::string sdp,
                                        bool probe_only) {
    const Key key{std::move(ip), std::move(attempt)};
    if (stopping)
        throw RtcFailure(503, "WebRTC service stopping");
    if (!preparation)
        throw RtcFailure(503, "WebRTC server dependency is unavailable");
    if (!offers.check(key.first, {.maximum = static_cast<std::uint32_t>(cfg.rtc_offer_max_per_ip),
                                  .map_maximum = static_cast<std::size_t>(cfg.rtc_offer_rate_max)}))
        throw RtcFailure(429, "too many WebRTC offers");
    expire();
    if (tombstones.contains(key))
        throw RtcFailure(409, "attempt cancelled");
    if (peers.contains(key))
        throw RtcFailure(409, "attempt already active");
    if (peers.size() >= cfg.max_rtc_peers || preparing_count >= cfg.max_rtc_peers)
        throw RtcFailure(503, "too many active WebRTC peers");
    if (static_cast<std::uint64_t>(
            std::count_if(peers.begin(), peers.end(), [&key](const auto &entry) {
                return entry.first.first == key.first;
            })) >= cfg.max_rtc_peers_per_ip)
        throw RtcFailure(429, "too many WebRTC peers from this IP");
    auto peer = std::make_shared<Peer>(strand, cfg, key, epoch);
    peers.emplace(key, peer);
    const auto weak_service = weak_from_this();
    const auto weak_peer = std::weak_ptr(peer);
    peer->cleanup = [weak_service, weak_peer, key] {
        // A late callback belongs to this admitted generation, never a new
        // attempt which reuses the key after bounded tombstone eviction.
        const auto expected = weak_peer.lock();
        if (auto service = weak_service.lock()) {
            const auto current = service->peers.find(key);
            if (expected && current != service->peers.end() && current->second == expected)
                service->cleanup(key);
        }
    };
    peer->session_timer.expires_after(Millis(cfg.rtc_session_ms));
    peer->session_timer.async_wait([weak_peer](Error error) {
        if (!error)
            if (auto item = weak_peer.lock())
                item->fail("rtc_session_timeout");
    });
    const auto negotiation_deadline = Clock::now() + Millis(cfg.rtc_negotiation_ms);
    ++preparing_count;
    const auto owner = shared_from_this();
    auto work = asio::make_work_guard(asio::any_io_executor(strand));
    asio::post(*preparation, [owner, peer, sdp = std::move(sdp), probe_only,
                              work = std::move(work)]() mutable {
        std::shared_ptr<rtc::PeerConnection> connection;
        bool failed = false;
        try {
            if (peer->cancelled)
                throw RtcFailure(409, "attempt cancelled");
            if (probe_only && !owner->cfg.rtc_udp4)
                throw RtcFailure(500, "WebRTC offer failed");
            rtc::Configuration native;
            native.disableAutoNegotiation = true;
            native.enableIceTcp = false;
            // Rust omits max-message-size in its answer: RFC 8841's default
            // is 64 KiB. Reassembly limits cover multiple control messages,
            // not one native SCTP message or its negotiated receive capacity.
            native.maxMessageSize = 65536;
            if (owner->cfg.rtc_udp4)
                native.bindAddress = mapping_interface(*owner->cfg.rtc_udp4);
            else if (owner->cfg.rtc_local_test)
                native.bindAddress = "127.0.0.1";
            connection = std::make_shared<rtc::PeerConnection>(native);
            const auto weak = std::weak_ptr(peer);
            const auto executor = owner->strand;
            connection->onDataChannel(
                [weak, executor](std::shared_ptr<rtc::DataChannel> channel) noexcept {
                    try {
                        asio::post(executor, [weak, channel = std::move(channel)]() mutable {
                            if (auto item = weak.lock())
                                if (!item->closed)
                                    item->install(channel);
                        });
                    } catch (...) {
                        log_event("rtc_callback_post_failed");
                    }
                });
            connection->onGatheringStateChange(
                [weak, executor](rtc::PeerConnection::GatheringState state) noexcept {
                    if (state != rtc::PeerConnection::GatheringState::Complete)
                        return;
                    try {
                        asio::post(executor, [weak] {
                            if (auto item = weak.lock()) {
                                item->gathered = true;
                                item->negotiation_gate.cancel();
                            }
                        });
                    } catch (...) {
                        log_event("rtc_callback_post_failed");
                    }
                });
            connection->onStateChange([weak, executor](rtc::PeerConnection::State state) noexcept {
                if (state != rtc::PeerConnection::State::Disconnected &&
                    state != rtc::PeerConnection::State::Failed &&
                    state != rtc::PeerConnection::State::Closed)
                    return;
                try {
                    asio::post(executor, [weak] {
                        if (auto item = weak.lock())
                            item->fail("rtc_peer_closed");
                    });
                } catch (...) {
                    log_event("rtc_callback_post_failed");
                }
            });
            connection->setRemoteDescription(rtc::Description(sdp, "offer"));
            connection->setLocalDescription(rtc::Description::Type::Answer);
        } catch (...) {
            failed = true;
        }
        asio::post(owner->strand, [owner, peer, connection = std::move(connection), failed,
                                   work = std::move(work)]() mutable {
            --owner->preparing_count;
            peer->prepared = true;
            peer->preparation_failed = failed;
            if (peer->closed || failed) {
                if (connection) {
                    connection->resetCallbacks();
                    try {
                        connection->close();
                    } catch (...) {
                        log_event("rtc_close_failed");
                    }
                }
            } else
                peer->connection = std::move(connection);
            peer->negotiation_gate.cancel();
            work.reset();
        });
    });
    while (!peer->closed && !peer->prepared && Clock::now() < negotiation_deadline) {
        peer->negotiation_gate.expires_at(negotiation_deadline);
        Error ignored;
        co_await peer->negotiation_gate.async_wait(asio::redirect_error(use_awaitable, ignored));
    }
    if (peer->closed)
        throw RtcFailure(409, "attempt cancelled");
    if (!peer->prepared || peer->preparation_failed || !peer->connection) {
        cleanup(key);
        throw RtcFailure(500, "WebRTC offer failed");
    }
    const auto gather_deadline =
        std::min(negotiation_deadline, Clock::now() + Millis(cfg.rtc_ice_gather_ms));
    while (!peer->closed && !peer->gathered && Clock::now() < gather_deadline) {
        peer->negotiation_gate.expires_at(gather_deadline);
        Error ignored;
        co_await peer->negotiation_gate.async_wait(asio::redirect_error(use_awaitable, ignored));
    }
    if (peer->closed)
        throw RtcFailure(409, "attempt cancelled");
    const auto local = peer->connection->localDescription();
    if (!local) {
        cleanup(key);
        throw RtcFailure(500, "WebRTC offer failed");
    }
    co_return Json{{"type", "answer"}, {"sdp", advertised_sdp(std::string(*local), cfg)}};
}
namespace {
Json Peer::transport() const {
    if (!test)
        return nullptr;
    const auto reliability = test->reliability();
    return Json{{"ordered", !reliability.unordered},
                {"maxRetransmits",
                 reliability.maxRetransmits ? Json(*reliability.maxRetransmits) : Json(nullptr)},
                {"zeroRetransmitHonored", true}};
}
void Peer::post_message(std::string label, rtc::message_variant message) noexcept {
    try {
        if (cancelled || violation_posted)
            return;
        const auto bytes = std::visit([](const auto &value) { return value.size(); }, message);
        std::string_view violation;
        // The reference native read loop has a uint16_t-sized buffer. Match
        // its close/tombstone boundary before application message accounting.
        if (bytes > std::numeric_limits<std::uint16_t>::max())
            violation = "native-message-size-limit";
        else if (inbound_bytes.fetch_add(bytes) + bytes > cfg.rtc_inbound_bytes_max)
            violation = "inbound-byte-limit";
        else if (label == "test") {
            if (bytes != frame_bytes || inbound_test.fetch_add(1) >= cfg.rtc_test_messages_max)
                violation = "test-message-limit";
        } else if (bytes > cfg.rtc_control_message_bytes ||
                   inbound_control.fetch_add(1) >= cfg.rtc_control_messages_max)
            violation = "control-message-limit";
        const auto weak = weak_from_this();
        if (!violation.empty()) {
            if (!violation_posted.exchange(true))
                asio::post(strand, [weak, violation] {
                    if (auto peer = weak.lock())
                        peer->fail(violation);
                });
            return;
        }
        asio::post(
            strand, [weak, label = std::move(label), message = std::move(message)]() mutable {
                if (auto peer = weak.lock())
                    if (!peer->closed) {
                        asio::co_spawn(peer->strand,
                                       peer->on_message(std::move(label), std::move(message)),
                                       [peer](const std::exception_ptr &error) {
                                           if (error)
                                               peer->fail("rtc_message_failed");
                                       });
                    }
            });
    } catch (...) {
        violation_posted = true;
        log_event("rtc_callback_post_failed");
    }
}
void Peer::install(const std::shared_ptr<rtc::DataChannel> &channel) {
    const auto label = channel->label();
    if (label == "control")
        control = channel;
    else if (label == "test")
        test = channel;
    else {
        log_event("rtc_unexpected_channel");
        return;
    }
    const auto weak = weak_from_this();
    channel->onMessage([weak, label](rtc::message_variant message) noexcept {
        if (auto peer = weak.lock())
            peer->post_message(label, std::move(message));
    });
    if (label == "test" && pending) {
        auto message = std::move(*pending);
        pending.reset();
        const auto self = shared_from_this();
        asio::co_spawn(strand, start_down(std::move(message)),
                       [self](const std::exception_ptr &error) {
                           if (error)
                               self->fail("rtc_start_failed");
                       });
    }
}
awaitable<bool> Peer::send(std::shared_ptr<rtc::DataChannel> channel, rtc::message_variant message,
                           std::shared_ptr<Measurement> state) {
    if (!channel || !channel->isOpen())
        co_return false;
    const auto deadline = Clock::now() + std::chrono::seconds(2);
    asio::steady_timer timer(strand);
    // false from native send means accepted into its buffer, not failure.
    // Bound that buffer before admitting another send; never retry an accepted
    // zero-retransmit probe merely because the native call returned false.
    while (channel->bufferedAmount() > 65536) {
        if (closed || (state && state->cancelled) || Clock::now() >= deadline)
            co_return false;
        timer.expires_after(Millis(2));
        Error error;
        co_await timer.async_wait(asio::redirect_error(use_awaitable, error));
        if (error)
            co_return false;
    }
    if (closed || (state && state->cancelled) || !channel->isOpen())
        co_return false;
    try {
        const bool immediate = channel->send(std::move(message));
        co_return immediate || channel->isOpen() || channel->bufferedAmount() > 0;
    } catch (const std::exception &) {
        co_return false;
    }
}
awaitable<void> Peer::on_message(std::string label, rtc::message_variant message) {
    if (closed)
        co_return;
    if (label == "test") {
        const auto bytes = std::visit(
            [](const auto &data) {
                return std::span<const std::byte>(reinterpret_cast<const std::byte *>(data.data()),
                                                  data.size());
            },
            message);
        const auto decoded = Frame::decode(bytes);
        if (!decoded) {
            fail("invalid-frame");
            co_return;
        }
        const auto frame = *decoded;
        const auto it = tests.find({frame.session_hash, frame.test_hash});
        if (frame.kind == 1) {
            bool reordered = false;
            if (it != tests.end()) {
                auto &m = *it->second;
                if (frame.seq < m.identity.planned)
                    m.upload_received.insert(frame.seq);
                reordered = m.max_up_seq && frame.seq < *m.max_up_seq;
                m.max_up_seq = m.max_up_seq ? std::max(frame.seq, *m.max_up_seq) : frame.seq;
            }
            auto ack = frame;
            ack.kind = 2;
            ack.flags = static_cast<std::uint8_t>(reordered);
            ack.actual_sent = 0;
            if (std::isnan(ack.sent_at))
                ack.sent_at = 0;
            const auto payload = ack.encode();
            static_cast<void>(co_await send(test, rtc::binary(payload.begin(), payload.end())));
        } else if (frame.kind == 4 && it != tests.end()) {
            auto m = it->second;
            const auto rtt = milliseconds(epoch) - frame.sent_at;
            if (m->sent_seqs.contains(frame.seq) && !m->acks.contains(frame.seq)) {
                m->acks.emplace(frame.seq, rtt);
                const Frame sample{.kind = 5,
                                   .flags = 0,
                                   .session_hash = frame.session_hash,
                                   .test_hash = frame.test_hash,
                                   .seq = frame.seq,
                                   .sent_at = rtt,
                                   .actual_sent = 0};
                const auto payload = sample.encode();
                static_cast<void>(co_await send(test, rtc::binary(payload.begin(), payload.end())));
            }
        }
        co_return;
    }
    const auto text = std::visit(
        [](const auto &data) {
            return std::string(reinterpret_cast<const char *>(data.data()), data.size());
        },
        message);
    Json parsed;
    try {
        parsed = parse_json(text);
    } catch (const std::invalid_argument &) {
        co_return;
    }
    if (json_field(parsed, "type") == "controlFragment") {
        if (json_field(parsed, "messageType") != "uploadComplete") {
            fail("unsupported-control-fragment");
            co_return;
        }
        std::optional<Json> assembled;
        try {
            assembled = reassembler.accept(parsed);
        } catch (const FragmentFailure &) {
            fail("invalid-control-fragment");
            co_return;
        }
        if (!assembled)
            co_return;
        parsed = std::move(*assembled);
    }
    const auto type = json_field(parsed, "type");
    if (type == "close") {
        if (cleanup)
            cleanup();
    } else if (type == "uploadComplete")
        accept_upload(parsed);
    else if (type == "downStart") {
        if (down_starts++ >= cfg.rtc_down_tests_max) {
            fail("down-start-limit");
            co_return;
        }
        if (test)
            co_await start_down(std::move(parsed));
        else
            pending = std::move(parsed);
    }
}
void Peer::accept_upload(const Json &message) {
    const auto session = rtc_number(json_field(message, "sessionHash")),
               hash = rtc_number(json_field(message, "testHash"));
    if (!session || !hash || *session > UINT32_MAX || *hash > UINT32_MAX)
        return;
    const auto it =
        tests.find({static_cast<std::uint32_t>(*session), static_cast<std::uint32_t>(*hash)});
    if (it == tests.end())
        return;
    auto &m = *it->second;
    if (m.finalized)
        return;
    if (m.upload_complete) {
        m.invalid.emplace_back("UPLOAD_COMPLETE_DUPLICATE");
        m.notify();
        return;
    }
    const auto reasons = upload_complete_reasons(message, m.identity, !m.invalid.empty());
    if (!reasons.empty()) {
        m.invalid.insert(m.invalid.end(), reasons.begin(), reasons.end());
        m.notify();
        return;
    }
    m.upload_at = Clock::now();
    m.upload_at_ms = milliseconds(epoch);
    m.upload_complete = message;
    m.notify();
}
awaitable<void> Peer::start_down(Json message) {
    const auto rate = bounded_number(json_field(message, "rate"), 200, 1, 500);
    const auto duration = bounded_number(json_field(message, "durationMs"), 5000, 100,
                                         static_cast<double>(cfg.rtc_measurement_ms));
    const auto planned = static_cast<std::uint64_t>(std::floor(rate * duration / 1000.0));
    if (planned == 0) {
        log_event("rtc_zero_measurement");
        co_return;
    }
    auto state = std::make_shared<Measurement>(strand);
    const auto test_id = json_field(message, "testId"),
               session_id = json_field(message, "sessionId");
    state->identity = {.test_id = test_id.is_string() ? test_id.get<std::string>() : "",
                       .session_id = session_id.is_string() ? session_id.get<std::string>() : "",
                       .planned = planned};
    state->session_hash = strict_hash(json_field(message, "sessionHash"))
                              .value_or(hash_string32(state->identity.session_id));
    state->test_hash = strict_hash(json_field(message, "testHash"))
                           .value_or(hash_string32(state->identity.test_id));
    state->interval_ms = 1000.0 / rate;
    state->duration_ms = static_cast<std::uint64_t>(duration);
    const TestKey test_key{state->session_hash, state->test_hash};
    if (tests.size() >= cfg.rtc_down_tests_max || tests.contains(test_key)) {
        static_cast<void>(co_await send(
            control,
            std::string(R"({"type":"error","error":"duplicate or excessive down test"})")));
        co_return;
    }
    tests.emplace(test_key, state);
    const Json ready = {{"type", "measurementReady"},
                        {"testId", test_id},
                        {"sessionId", session_id},
                        {"sessionHash", json_field(message, "sessionHash")},
                        {"testHash", json_field(message, "testHash")},
                        {"probeTransport", transport()}};
    if (!co_await send(control, ready.dump()))
        log_event("rtc_ready_send_failed");
    if (!test || closed)
        co_return;
    const auto self = shared_from_this();
    asio::co_spawn(strand, sampler(state), [self, state](const std::exception_ptr &error) {
        state->sampler_done = true;
        if (error)
            log_event("rtc_sampler_failed");
        state->notify();
    });
    asio::co_spawn(strand, scheduler(state, test), [self, state](const std::exception_ptr &error) {
        state->scheduler_done = true;
        if (error && !self->closed)
            state->invalid.emplace_back("DOWNLOAD_SCHEDULER_INCOMPLETE");
        state->notify();
    });
    asio::co_spawn(strand, finalizer(state), [self, state](const std::exception_ptr &error) {
        state->stop();
        self->tests.erase({state->session_hash, state->test_hash});
        if (error)
            self->fail("rtc_finalizer_failed");
    });
}
awaitable<void> Peer::scheduler(std::shared_ptr<Measurement> state,
                                std::shared_ptr<rtc::DataChannel> channel) {
    auto &m = *state;
    const auto start = Clock::now();
    std::uint64_t next = 0;
    while (next < m.identity.planned && !m.cancelled && !closed) {
        const auto target = start + std::chrono::duration_cast<Clock::duration>(
                                        std::chrono::duration<double, std::milli>(
                                            static_cast<double>(next) * m.interval_ms));
        m.schedule_timer.expires_at(target);
        Error error;
        co_await m.schedule_timer.async_wait(asio::redirect_error(use_awaitable, error));
        if (error || m.cancelled || closed)
            break;
        const auto due =
            static_cast<std::uint64_t>(std::min(std::floor(milliseconds(start) / m.interval_ms),
                                                static_cast<double>(m.identity.planned - 1)));
        if (next < due) {
            m.skipped += due - next;
            next = due;
        }
        const auto seq = static_cast<std::uint32_t>(next);
        ++m.scheduled;
        m.sent_seqs.insert(seq);
        const Frame frame{.kind = 3,
                          .flags = 0,
                          .session_hash = m.session_hash,
                          .test_hash = m.test_hash,
                          .seq = seq,
                          .sent_at = milliseconds(epoch),
                          .actual_sent = 0};
        const auto payload = frame.encode();
        const bool sent =
            co_await send(channel, rtc::binary(payload.begin(), payload.end()), state);
        if (sent) {
            ++m.actual_sent;
            m.sent_times.push_back(milliseconds(epoch));
        } else {
            m.sent_seqs.erase(seq);
            if (!m.cancelled && !closed)
                m.invalid.emplace_back("DOWNLOAD_SEND_FAILED");
            break;
        }
        ++next;
    }
    if (m.cancelled || closed)
        m.skipped = m.identity.planned >= m.scheduled ? m.identity.planned - m.scheduled : 0;
    m.download_done = !m.cancelled && !closed && m.scheduled + m.skipped == m.identity.planned &&
                      m.actual_sent == m.sent_seqs.size();
    if (!m.download_done && !m.cancelled && !closed)
        m.invalid.emplace_back("DOWNLOAD_SCHEDULER_INCOMPLETE");
    m.notify();
}
awaitable<void> Peer::sampler(std::shared_ptr<Measurement> state) {
    auto &m = *state;
    m.delays.reserve(4096);
    while (!m.cancelled && !closed) {
        const auto due = Clock::now() + Millis(1);
        m.sample_timer.expires_at(due);
        Error error;
        co_await m.sample_timer.async_wait(asio::redirect_error(use_awaitable, error));
        if (error || m.cancelled || closed)
            co_return;
        if (m.delays.size() < 65536)
            m.delays.push_back(std::max(0.0, milliseconds(due)));
    }
}
awaitable<void> Peer::finalizer(std::shared_ptr<Measurement> state) {
    auto &m = *state;
    const auto deadline = Clock::now() + Millis(m.duration_ms + 2000);
    bool complete = false;
    for (;;) {
        if (closed || m.cancelled)
            co_return;
        if (!m.invalid.empty())
            break;
        if (m.download_done && m.upload_at &&
            Clock::now() - *m.upload_at >= Millis(cfg.rtc_result_grace_ms)) {
            complete = true;
            break;
        }
        if (!m.upload_at && Clock::now() >= deadline) {
            if (!m.upload_complete)
                m.invalid.emplace_back("UPLOAD_COMPLETE_MISSING");
            else if (!m.download_done)
                m.invalid.emplace_back("DOWNLOAD_SCHEDULER_INCOMPLETE");
            break;
        }
        const auto wake = m.upload_at ? *m.upload_at + Millis(cfg.rtc_result_grace_ms) : deadline;
        // If upload grace is already over but the scheduler is still running,
        // wait for its notification instead of spinning an expired timer.
        m.gate.expires_at(m.upload_at && wake < Clock::now() ? Clock::time_point::max() : wake);
        Error ignored;
        co_await m.gate.async_wait(asio::redirect_error(use_awaitable, ignored));
    }
    m.stop();
    while (!m.scheduler_done || !m.sampler_done) {
        if (closed)
            co_return;
        m.gate.expires_at(Clock::time_point::max());
        Error ignored;
        co_await m.gate.async_wait(asio::redirect_error(use_awaitable, ignored));
    }
    if (closed || m.finalized)
        co_return;
    m.finalized = true;
    complete = complete && m.invalid.empty() && m.download_done;
    Json samples = Json::array();
    std::size_t count = 0;
    for (const auto &[seq, rtt] : m.acks) {
        if (count++ >= cfg.rtc_result_samples_max)
            break;
        samples.push_back(Json{{"seq", seq}, {"rtt", rtt}});
    }
    const auto upload_field = [&m](const char *name) -> Json {
        return m.upload_complete ? json_field(*m.upload_complete, name) : Json(nullptr);
    };
    const Json result = {
        {"type", "downResult"},
        {"final", complete},
        {"complete", complete},
        {"invalidReasons", m.invalid},
        {"testId", m.identity.test_id},
        {"sessionId", m.identity.session_id},
        {"sessionHash", m.session_hash},
        {"testHash", m.test_hash},
        {"planned", m.identity.planned},
        {"scheduled", m.scheduled},
        {"skipped", m.skipped},
        {"actualSent", m.actual_sent},
        {"sent", m.actual_sent},
        {"sentSeqs", m.sent_seqs},
        {"received", m.acks.size()},
        {"acked", m.acks.size()},
        {"uploadReceived", m.upload_received.size()},
        {"uploadReceivedSeqs", m.upload_received},
        {"clientUploadScheduled", upload_field("scheduled")},
        {"clientUploadSkipped", upload_field("skipped")},
        {"clientUploadActualSent", upload_field("actualSent")},
        {"samples", samples},
        {"pacing", pacing(m.sent_times, m.interval_ms)},
        {"serverEventLoopDelay", executor_delay(m.delays)},
        {"probeTransport", transport()},
        {"uploadCompleteAtMs", m.upload_at_ms ? Json(*m.upload_at_ms) : Json(nullptr)},
        {"finalizedAtMs", milliseconds(epoch)}};
    tests.erase({m.session_hash, m.test_hash});
    try {
        const auto messages = serialize_down_result(
            result,
            {.single = static_cast<std::size_t>(
                 cfg.rtc_legacy_control ? cfg.control_reassembled_bytes : cfg.control_single_bytes),
             .chunk = static_cast<std::size_t>(cfg.control_fragment_bytes),
             .maximum = static_cast<std::size_t>(cfg.control_fragment_count),
             .legacy = cfg.rtc_legacy_control});
        for (const auto &message : messages) {
            if (closed)
                break;
            if (!co_await send(control, message)) {
                log_event("rtc_result_send_failed");
                break;
            }
        }
    } catch (const FragmentFailure &) {
        log_event("rtc_result_fragmentation_failed");
    }
}
} // namespace
RtcService::RtcService(const asio::any_io_executor &executor, Config cfg)
    : impl_(std::make_shared<Impl>(executor, std::move(cfg))) {}
RtcService::~RtcService() = default;
awaitable<Json> RtcService::offer(std::string ip, std::string attempt, std::string sdp,
                                  bool probe_only) {
    const auto owner = impl_;
    co_return co_await asio::co_spawn(
        owner->strand, owner->offer(std::move(ip), std::move(attempt), std::move(sdp), probe_only),
        use_awaitable);
}
awaitable<void> RtcService::cancel(std::string ip, std::string attempt) {
    const auto owner = impl_;
    co_await asio::co_spawn(owner->strand, owner->cancel(std::move(ip), std::move(attempt)),
                            use_awaitable);
}
awaitable<void> RtcService::shutdown() {
    const auto owner = impl_;
    co_await asio::co_spawn(owner->strand, owner->shutdown(), use_awaitable);
}
} // namespace speedtest
