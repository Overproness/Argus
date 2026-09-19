#include <curl/curl.h>

int fetch(CURL *c) {
    for (int attempt = 0; attempt < 3; attempt++) {
        if (curl_easy_perform(c) == CURLE_OK)
            return 0;
    }
    return -1;
}
