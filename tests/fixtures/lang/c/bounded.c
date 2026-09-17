#include <curl/curl.h>

int fetch_bounded(CURL *c) {
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 5L);
    return curl_easy_perform(c);
}
