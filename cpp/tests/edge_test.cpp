#include "speedtest/client_info.hpp"
#include "speedtest/edge.hpp"
#include "speedtest/http.hpp"
#include <boost/asio/connect.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/read.hpp>
#include <boost/asio/ssl.hpp>
#include <boost/asio/write.hpp>
#include <curl/curl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/x509v3.h>
#include <thread>
#include <unistd.h>

namespace {
namespace asio = boost::asio;
using Tcp = asio::ip::tcp;
void check(bool value, std::string_view what) {
    if (!value)
        throw std::runtime_error("TLS HTTP/2 edge assertion failed: " + std::string(what));
}
unsigned short free_port() {
    asio::io_context io;
    Tcp::acceptor acceptor(io, Tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));
    return acceptor.local_endpoint().port();
}
void self_signed(const std::filesystem::path &cert, const std::filesystem::path &key) {
    EVP_PKEY *pkey = EVP_EC_gen("P-256");
    X509 *x509 = X509_new();
    ASN1_INTEGER_set(X509_get_serialNumber(x509), 1);
    X509_gmtime_adj(X509_getm_notBefore(x509), -60);
    X509_gmtime_adj(X509_getm_notAfter(x509), 3600);
    X509_set_pubkey(x509, pkey);
    auto *name = X509_get_subject_name(x509);
    X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC,
                               reinterpret_cast<const unsigned char *>("127.0.0.1"), -1, -1, 0);
    X509_set_issuer_name(x509, name);
    X509V3_CTX ctx;
    X509V3_set_ctx_nodb(&ctx);
    X509V3_set_ctx(&ctx, x509, x509, nullptr, nullptr, 0);
    for (const auto *spec : {"IP:127.0.0.1"}) {
        auto *ext = X509V3_EXT_conf_nid(nullptr, &ctx, NID_subject_alt_name, spec);
        X509_add_ext(x509, ext, -1);
        X509_EXTENSION_free(ext);
    }
    X509_sign(x509, pkey, EVP_sha256());
    FILE *out = std::fopen(cert.c_str(), "w");
    PEM_write_X509(out, x509);
    std::fclose(out);
    out = std::fopen(key.c_str(), "w");
    PEM_write_PrivateKey(out, pkey, nullptr, nullptr, 0, nullptr, nullptr);
    std::fclose(out);
    X509_free(x509);
    EVP_PKEY_free(pkey);
}
struct Reply {
    long status{}, version{};
    std::string body;
};
struct Upload {
    std::size_t left;
};
std::size_t collect(char *data, std::size_t size, std::size_t count, void *user) {
    static_cast<std::string *>(user)->append(data, size * count);
    return size * count;
}
std::size_t produce(char *data, std::size_t size, std::size_t count, void *user) {
    auto &upload = *static_cast<Upload *>(user);
    const auto n = std::min(upload.left, size * count);
    std::memset(data, 'x', n);
    upload.left -= n;
    return n;
}
Reply request(const std::string &url, const std::filesystem::path &ca, std::string_view method,
              std::optional<std::size_t> stream_bytes = {}, std::optional<std::size_t> sized = {},
              const std::vector<std::string> &headers = {}) {
    CURL *curl = curl_easy_init();
    Reply reply;
    Upload upload{stream_bytes.value_or(0)};
    std::string fixed(sized.value_or(0), 'y');
    curl_slist *list = nullptr;
    for (const auto &h : headers)
        list = curl_slist_append(list, h.c_str());
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTP_VERSION, CURL_HTTP_VERSION_2TLS);
    curl_easy_setopt(curl, CURLOPT_CAINFO, ca.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, collect);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &reply.body);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 60L);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, list);
    if (method == "HEAD")
        curl_easy_setopt(curl, CURLOPT_NOBODY, 1L);
    if (stream_bytes) {
        curl_easy_setopt(curl, CURLOPT_UPLOAD, 1L);
        curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, "POST");
        curl_easy_setopt(curl, CURLOPT_READFUNCTION, produce);
        curl_easy_setopt(curl, CURLOPT_READDATA, &upload);
    } else if (sized) {
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, fixed.data());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE_LARGE, static_cast<curl_off_t>(fixed.size()));
    }
    const auto code = curl_easy_perform(curl);
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &reply.status);
    curl_easy_getinfo(curl, CURLINFO_HTTP_VERSION, &reply.version);
    curl_slist_free_all(list);
    curl_easy_cleanup(curl);
    check(code == CURLE_OK, std::string("transfer failed: ") + curl_easy_strerror(code));
    return reply;
}
// Reads the server's first frames and returns its advertised receive windows.
std::pair<std::uint32_t, std::uint32_t> advertised_windows(unsigned short port) {
    asio::io_context io;
    asio::ssl::context ctx(asio::ssl::context::tls_client);
    asio::ssl::stream<Tcp::socket> tls(io, ctx);
    const unsigned char alpn[] = {2, 'h', '2'};
    SSL_set_alpn_protos(tls.native_handle(), alpn, sizeof alpn);
    tls.lowest_layer().connect(Tcp::endpoint(asio::ip::make_address("127.0.0.1"), port));
    tls.handshake(asio::ssl::stream_base::client);
    const std::string preface =
        std::string("PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n") + std::string("\0\0\0\x04\0\0\0\0\0", 9);
    asio::write(tls, asio::buffer(preface));
    std::uint32_t stream = 0, connection = 0;
    for (int frames = 0; frames < 8 && (!stream || !connection); ++frames) {
        std::array<unsigned char, 9> head{};
        asio::read(tls, asio::buffer(head));
        const auto size = (std::size_t{head[0]} << 16) | (std::size_t{head[1]} << 8) | head[2];
        std::vector<unsigned char> payload(size);
        asio::read(tls, asio::buffer(payload));
        const auto word = [&payload](std::size_t at) {
            return (std::uint32_t{payload[at]} << 24) | (std::uint32_t{payload[at + 1]} << 16) |
                   (std::uint32_t{payload[at + 2]} << 8) | std::uint32_t{payload[at + 3]};
        };
        if (head[3] == 4 && !(head[4] & 1))
            for (std::size_t at = 0; at + 6 <= size; at += 6)
                if (payload[at] == 0 && payload[at + 1] == 4)
                    stream = word(at + 2);
        if (head[3] == 8 && size == 4)
            connection = 65535 + (word(0) & 0x7fffffffU);
    }
    return {stream, connection};
}
} // namespace

int main() {
    const speedtest::CurlRuntime curl;
    const auto folder = std::filesystem::temp_directory_path() /
                        ("jittercrest-edge-test-" + std::to_string(::getpid()));
    std::filesystem::create_directory(folder);
    int status = 0;
    try {
        const auto cert = folder / "cert.pem", key = folder / "key.pem";
        self_signed(cert, key);
        const auto port = free_port(), tls_port = free_port();
        const std::map<std::string, std::string> env{
            {"PORT", std::to_string(port)},       {"SPEEDTEST_HOST", "127.0.0.1"},
            {"SPEEDTEST_NODE_NAME", "edge-test"}, {"TRUST_PROXY_HEADERS", "1"},
            {"TRUSTED_PROXY_IPS", "127.0.0.1"},   {"SPEEDTEST_TLS_PORT", std::to_string(tls_port)},
            {"SPEEDTEST_TLS_HOST", "127.0.0.1"},  {"SPEEDTEST_TLS_CERT", cert.string()},
            {"SPEEDTEST_TLS_KEY", key.string()}};
        const auto cfg = speedtest::Config::from_environment([&env](std::string_view name) {
            const auto it = env.find(std::string(name));
            return it == env.end() ? std::optional<std::string>{} : it->second;
        });
        auto rejected = env;
        rejected.erase("TRUST_PROXY_HEADERS");
        bool refused = false;
        try {
            static_cast<void>(
                speedtest::Config::from_environment([&rejected](std::string_view name) {
                    const auto it = rejected.find(std::string(name));
                    return it == rejected.end() ? std::optional<std::string>{} : it->second;
                }));
        } catch (const std::invalid_argument &) {
            refused = true;
        }
        check(refused, "edge requires trusted proxy headers");
        asio::io_context io(2);
        speedtest::HttpServer server(io, cfg);
        speedtest::TlsEdge edge(io, cfg);
        server.start();
        edge.start();
        std::vector<std::jthread> workers;
        for (int i = 0; i < 2; ++i)
            workers.emplace_back([&io] { io.run(); });
        const auto base = "https://127.0.0.1:" + std::to_string(tls_port);
        try {
            const auto windows = advertised_windows(tls_port);
            check(windows.first == 32U << 20U, "stream receive window");
            check(windows.second == 64U << 20U, "connection receive window");
            const auto health = request(base + "/health", cert, "GET");
            check(health.status == 200 && health.version == CURL_HTTP_VERSION_2_0, "HTTP/2 health");
            check(speedtest::Json::parse(health.body).at("name") == "edge-test", "relayed body");
            const auto head = request(base + "/latency", cert, "HEAD");
            check(head.status == 204 && head.body.empty(), "HEAD");
            const auto who = request(base + "/client-info", cert, "GET", {}, {},
                                     {"X-Real-IP: 203.0.113.9", "X-Forwarded-For: 203.0.113.9"});
            check(speedtest::Json::parse(who.body).at("ip") == "127.0.0.1",
                  "client address comes from the TLS peer");
            const std::size_t streamed = std::size_t{64} << 20;
            const auto up = request(base + "/upload-stream?warmupMs=0", cert, "POST", streamed);
            check(up.status == 200 && speedtest::Json::parse(up.body).at("bytes") == streamed,
                  "streamed upload byte accounting");
            const auto fixed = request(base + "/upload", cert, "POST", {}, std::size_t{1} << 20);
            check(fixed.status == 200 &&
                      speedtest::Json::parse(fixed.body).at("bytes") == std::size_t{1} << 20,
                  "sized upload");
            const auto down = request(base + "/download?size=33554432", cert, "GET");
            check(down.status == 200 && down.body.size() == 33554432U, "download");
            const auto missing = request(base + "/missing", cert, "GET");
            check(missing.status == 404, "relayed status");
            asio::io_context client_io;
            asio::ssl::context client_ctx(asio::ssl::context::tls_client);
            std::vector<std::unique_ptr<asio::ssl::stream<Tcp::socket>>> held;
            const auto open = [&] {
                auto tls = std::make_unique<asio::ssl::stream<Tcp::socket>>(client_io, client_ctx);
                const unsigned char alpn[] = {2, 'h', '2'};
                SSL_set_alpn_protos(tls->native_handle(), alpn, sizeof alpn);
                tls->lowest_layer().connect(
                    Tcp::endpoint(asio::ip::make_address("127.0.0.1"), tls_port));
                boost::system::error_code error;
                tls->handshake(asio::ssl::stream_base::client, error);
                held.push_back(std::move(tls));
                return !error;
            };
            for (int i = 0; i < 16; ++i)
                check(open(), "connection within the per-client limit");
            check(!open(), "connection above the per-client limit is refused");
        } catch (...) {
            edge.stop();
            server.stop();
            throw;
        }
        edge.stop();
        server.stop();
        workers.clear();
        std::cout << "TLS HTTP/2 edge relay checks passed\n";
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        status = 1;
    }
    std::filesystem::remove_all(folder);
    return status;
}
