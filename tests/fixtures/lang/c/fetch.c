#include <curl/curl.h>
#include <unistd.h>

int fetch(CURL *c) {
    return curl_easy_perform(c);
}

void poll_all(CURL **cs, int n) {
    for (int i = 0; i < n; i++) {
        fetch(cs[i]);
        sleep(1);
    }
}
