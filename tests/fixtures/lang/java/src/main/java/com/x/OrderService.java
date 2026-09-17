package com.x;

import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;

public class OrderService {
    private final OrderRepository orderRepository;
    private final HttpClient client = HttpClient.newHttpClient();

    public void load(List<Long> ids) {
        for (Long id : ids) {
            orderRepository.findById(id);
        }
        ids.forEach(id -> enrich(id));
    }

    void enrich(Long id) throws Exception {
        HttpRequest req = HttpRequest.newBuilder().build();
        client.send(req, HttpResponse.BodyHandlers.ofString());
    }

    int[][] grid(int n) {
        int[][] g = new int[n][n];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                g[i][j] = i * j;
            }
        }
        return g;
    }
}
