use std::sync::{Arc, Mutex};

use crate::exchange::Exchange;

pub struct State {
    pub last: f64,
}

fn compute_signal(ex: &Exchange, symbol: &str) -> f64 {
    ex.fetch_price(symbol) * 1.01
}

async fn submit(signal: f64) {
    let _ = signal;
}

pub async fn run_strategy(ex: Arc<Exchange>, state: Arc<Mutex<State>>) {
    loop {
        // Blocks the tokio worker through compute_signal -> fetch_price.
        let signal = compute_signal(&ex, "BTC");

        // Fine: offloaded to the blocking pool.
        let ex2 = ex.clone();
        let _eth = tokio::task::spawn_blocking(move || ex2.fetch_price("ETH")).await;

        let guard = state.lock().unwrap();
        submit(signal + guard.last).await;
    }
}
