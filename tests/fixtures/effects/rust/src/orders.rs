use std::time::Duration;

use reqwest::blocking::Client;
use tokio::time::timeout;

const MAX_ATTEMPTS: u32 = 5;

pub fn submit(order: &str) -> Result<(), reqwest::Error> {
    let client = Client::builder().timeout(Duration::from_secs(10)).build()?;
    for attempt in 0..MAX_ATTEMPTS {
        match client.post("https://x/orders").body(order.to_string()).send() {
            Ok(_) => return Ok(()),
            Err(_) if attempt < MAX_ATTEMPTS - 1 => continue,
            Err(e) => return Err(e),
        }
    }
    Ok(())
}

pub fn submit_all(orders: &[String]) {
    for attempt in 0..3 {
        let ok = orders.iter().all(|o| submit(o).is_ok());
        if ok {
            break;
        }
        std::thread::sleep(Duration::from_millis(200 * 2u64.pow(attempt)));
    }
}

pub async fn place(order: String) {
    // 5 attempts x 10 s can never fit in 20 s, and spawn_blocking cannot be cancelled.
    let _ = timeout(Duration::from_secs(20), tokio::task::spawn_blocking(move || submit(&order))).await;
}
