package main

import (
	"context"
	"net/http"
	"time"
)

func price(url string) *http.Response {
	resp, _ := http.Get(url)
	return resp
}

func send(client *http.Client, req *http.Request) error {
	for i := 0; i < 3; i++ {
		if _, err := client.Do(req); err == nil {
			return nil
		}
	}
	return nil
}

func withDeadline(client *http.Client, req *http.Request) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	client.Do(req.WithContext(ctx))
}

func main() {
	for {
		price("https://x/btc")
		time.Sleep(time.Second)
	}
}
