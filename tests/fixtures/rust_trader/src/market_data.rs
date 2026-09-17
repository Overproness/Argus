use reqwest::Client;
use std::time::Duration;
use tokio::time::timeout;

pub async fn fetch_book(client: &Client, symbol: &str) -> String {
    // No timeout: a hung peer parks this task forever.
    client.get(format!("https://x/book/{symbol}")).send().await.unwrap().text().await.unwrap()
}

pub async fn fetch_book_bounded(client: &Client, symbol: &str) -> String {
    let resp = timeout(Duration::from_secs(2), client.get(format!("https://x/book/{symbol}")).send())
        .await
        .unwrap()
        .unwrap();
    resp.text().await.unwrap()
}

pub async fn refresh_all(client: &Client, symbols: &[String]) {
    for s in symbols {
        fetch_book(client, s).await;
    }
}
