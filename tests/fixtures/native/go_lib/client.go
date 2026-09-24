// Package client is a fixture library, imported by a generated probe in a side project.
package client

import (
	"io"
	"net"
	"strings"
)

// Fetch is a blocking GET with no timeout: a dead peer hangs the caller forever.
func Fetch(url string) (string, error) {
	addr := strings.TrimPrefix(url, "http://")
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		return "", err
	}
	defer conn.Close()
	if _, err := conn.Write([]byte("GET / HTTP/1.1\r\nConnection: close\r\n\r\n")); err != nil {
		return "", err
	}
	body, err := io.ReadAll(conn)
	return string(body), err
}

// FetchWithRetries retries immediately, with no delay between attempts.
func FetchWithRetries(url string, attempts int) (string, error) {
	var last error
	for i := 0; i < attempts; i++ {
		body, err := Fetch(url)
		if err == nil {
			return body, nil
		}
		last = err
	}
	return "", last
}
