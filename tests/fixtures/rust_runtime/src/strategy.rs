use std::sync::Arc;

use crate::exchange::Exchange;

#[tracing::instrument(skip(ex))]
fn compute_signal(ex: &Exchange, symbol: &str) -> f64 {
    ex.fetch_price(symbol) * 1.01
}

#[tracing::instrument(skip(ex))]
pub async fn tick(ex: Arc<Exchange>) -> f64 {
    // Blocks the tokio worker through compute_signal -> fetch_price.
    let signal = compute_signal(&ex, "BTC");

    // Fine: offloaded to the blocking pool, should not be reported as a stall.
    let ex2 = ex.clone();
    let _eth = tokio::task::spawn_blocking(move || ex2.fetch_price("ETH")).await.unwrap();

    signal
}

#[tracing::instrument(skip(ex))]
pub async fn refresh_all(ex: Arc<Exchange>, symbols: Vec<&str>) {
    for s in symbols {
        // N+1: fetch_price runs once per symbol from an async context.
        let _ = compute_signal(&ex, s);
    }
}
