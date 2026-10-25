#include "speedtest/rtc.hpp"
#include <iostream>
#include <source_location>

namespace {
void require(bool condition, std::source_location where = std::source_location::current()) {
    if (!condition)
        throw std::runtime_error("RTC reason fixture failed at line " +
                                 std::to_string(where.line()));
}
} // namespace
int main() {
    using namespace speedtest;
    try {
        const MeasurementIdentity id{.test_id = "test", .session_id = "session", .planned = 2};
        const Json valid = {{"testId", "test"},  {"sessionId", "session"}, {"planned", 2},
                            {"scheduled", 2},    {"skipped", 0},           {"actualSent", 2},
                            {"sentSeqs", {0, 1}}};
        require(upload_complete_reasons(valid, id).empty());
        require(upload_complete_reasons(valid, id, true) ==
                std::vector<std::string>{"UPLOAD_COMPLETE_INVALID"});
        auto duplicate = valid;
        duplicate["sentSeqs"] = {0, 0};
        require(upload_complete_reasons(duplicate, id) ==
                std::vector<std::string>{"UPLOAD_SENT_SEQS_INVALID", "UPLOAD_COMPLETE_INVALID"});
        auto bounds = valid;
        bounds["actualSent"] = 3;
        require(upload_complete_reasons(bounds, id) ==
                std::vector<std::string>{"UPLOAD_SCHEDULER_BOUNDS",
                                         "UPLOAD_SENT_SEQS_COUNT_MISMATCH",
                                         "UPLOAD_COMPLETE_INVALID"});
        auto missing = valid;
        missing.erase("scheduled");
        require(upload_complete_reasons(missing, id) ==
                std::vector<std::string>{"UPLOAD_SCHEDULED_INVALID", "UPLOAD_SCHEDULER_ACCOUNTING",
                                         "UPLOAD_SCHEDULER_BOUNDS", "UPLOAD_COMPLETE_INVALID"});
        auto string = valid;
        string["scheduled"] = "2";
        require(upload_complete_reasons(string, id) ==
                std::vector<std::string>{"UPLOAD_SCHEDULED_INVALID", "UPLOAD_SCHEDULER_ACCOUNTING",
                                         "UPLOAD_COMPLETE_INVALID"});
        auto fraction = valid;
        fraction["sentSeqs"] = {0, 0.5};
        require(upload_complete_reasons(fraction, id) ==
                std::vector<std::string>{"UPLOAD_SENT_SEQS_INVALID", "UPLOAD_COMPLETE_INVALID"});
        require(rtc_number(Json(2.0)) == 2 && !rtc_number(Json(true)) &&
                !rtc_number(Json(9007199254740992.0)));
        require(upload_complete_reasons(Json::array(), id).size() == 9);
        std::cout << "RTC uploadComplete reason ordering and malformed count semantics passed\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
