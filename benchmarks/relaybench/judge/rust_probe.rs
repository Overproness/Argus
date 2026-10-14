use edge::{cache::Cache, summary::SummaryClient, supervisor, transport};
use std::io::Write;
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    match std::env::args().nth(1).as_deref() {
        Some("peer") => {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap().to_string();
            let peer = std::thread::spawn(move || {
                let (mut client, _) = listener.accept().unwrap();
                std::thread::sleep(Duration::from_millis(150));
                client.write_all(b"available").unwrap();
            });
            let ticks = Arc::new(Mutex::new(Vec::new()));
            let samples = ticks.clone();
            let sibling = tokio::spawn(async move {
                loop {
                    samples.lock().unwrap().push(Instant::now());
                    tokio::time::sleep(Duration::from_millis(2)).await;
                }
            });
            tokio::time::sleep(Duration::from_millis(10)).await;
            assert_eq!(transport::sample_peer(address).await.unwrap(), "available");
            tokio::time::sleep(Duration::from_millis(10)).await;
            sibling.abort();
            let _ = sibling.await;
            peer.join().unwrap();
            let samples = ticks.lock().unwrap();
            let gap = samples.windows(2).map(|w| w[1].duration_since(w[0]).as_millis()).max().unwrap();
            println!("{{\"satisfied\":{},\"metrics\":{{\"max_tick_gap_ms\":{gap}}}}}", gap < 75);
        }
        Some("cache") => {
            let cache = Arc::new(Cache { rows: Mutex::new(vec!["parcel".to_owned()]) });
            let other = cache.clone();
            let competitor = std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(10));
                let start = Instant::now();
                let _guard = other.rows.lock().unwrap();
                start.elapsed().as_millis()
            });
            assert_eq!(cache.snapshot(Duration::from_millis(150)).await, vec!["parcel"]);
            let wait = competitor.join().unwrap();
            println!("{{\"satisfied\":{},\"metrics\":{{\"lock_wait_ms\":{wait}}}}}", wait < 75);
        }
        Some("supervisor") => {
            let propagated = supervisor::refresh(true).await.is_err();
            println!("{{\"satisfied\":{propagated},\"metrics\":{{\"error_propagated\":{propagated}}}}}");
        }
        Some("summary") => {
            let client = SummaryClient { delay: Duration::from_millis(150) };
            let start = Instant::now();
            assert_eq!(client.dashboard().await, (7, 11));
            let elapsed = start.elapsed().as_millis();
            println!("{{\"satisfied\":{},\"metrics\":{{\"elapsed_ms\":{elapsed}}}}}", elapsed < 225);
        }
        _ => panic!("Unknown contract"),
    }
}
