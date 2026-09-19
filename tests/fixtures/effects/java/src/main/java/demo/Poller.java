package demo;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class Poller {
    private static final HttpClient client = HttpClient.newHttpClient();

    static String fetch(String url) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(url)).build();
        for (int attempt = 0; attempt < 5; attempt++) {
            try {
                return client.send(req, HttpResponse.BodyHandlers.ofString()).body();
            } catch (IOException e) {
                continue;
            }
        }
        return null;
    }

    public static void main(String[] args) throws Exception {
        while (true) {
            fetch("http://localhost/quote");
        }
    }
}
