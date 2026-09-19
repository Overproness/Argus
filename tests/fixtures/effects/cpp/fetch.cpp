#include <cpr/cpr.h>
#include <stdexcept>
#include <string>

std::string fetch(const std::string &url) {
    for (int attempt = 0; attempt < 4; attempt++) {
        try {
            return cpr::Get(cpr::Url{url}).text;
        } catch (const std::exception &e) {
            continue;
        }
    }
    return "";
}
