package main

import (
	"database/sql"
	"net/http"
	"time"
)

type Store struct{ db *sql.DB }

func (s *Store) LoadUsers(ids []int) {
	for _, id := range ids {
		s.db.QueryRow("select * from users where id = ?", id)
	}
}

func fetch(url string) (*http.Response, error) {
	return http.Get(url)
}

func fetchBounded(url string) (*http.Response, error) {
	client := &http.Client{Timeout: 5 * time.Second}
	return client.Get(url)
}

func fanOut(urls []string) {
	for _, u := range urls {
		go fetch(u)
		defer time.Sleep(time.Millisecond)
	}
}

func main() {
	for {
		fetch("https://x")
		time.Sleep(time.Second)
	}
}
