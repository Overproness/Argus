use std::time::Duration;

use reqwest::Client;
use tokio::time::timeout;

pub fn price_blocking(sym: &str) -> f64 {
    let body = reqwest::blocking::get(format!("https://x/{sym}")).unwrap().text().unwrap();
    body.parse().unwrap_or(0.0)
}

pub async fn price_async(client: &Client, sym: &str) -> f64 {
    let resp = client.get(format!("https://x/{sym}")).send().await.expect("network");
    resp.json::<f64>().await.unwrap_or(0.0)
}

pub async fn quote(sym: &str) -> f64 {
    price_blocking(sym)
}

pub async fn tick() {
    let client = Client::new();
    // The deadline cannot fire: quote blocks the worker thread.
    let _ = timeout(Duration::from_secs(2), quote("BTC")).await;
    // Bounded by the deadline: fine.
    let _ = timeout(Duration::from_secs(5), price_async(&client, "ETH")).await;
    // Nothing bounds this one.
    let _ = price_async(&client, "SOL").await;
}
