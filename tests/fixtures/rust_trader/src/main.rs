mod exchange;
mod market_data;
mod risk;
mod strategy;

use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

#[tokio::main]
async fn main() {
    let ex = Arc::new(exchange::Exchange::new("https://api.example"));
    let state = Arc::new(Mutex::new(strategy::State { last: 0.0 }));
    // Blocking sleep directly on the runtime.
    thread::sleep(Duration::from_millis(500));
    strategy::run_strategy(ex, state).await;
}
