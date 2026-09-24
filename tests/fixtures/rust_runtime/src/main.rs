mod exchange;
mod server;
mod strategy;

use std::sync::Arc;

use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

#[tokio::main]
async fn main() {
    let (chrome_layer, _guard) = tracing_chrome::ChromeLayerBuilder::new().include_args(true).build();
    tracing_subscriber::registry().with(chrome_layer).init();

    let port = server::spawn(60); // 60ms per response, long enough to show clearly as a stall
    let ex = Arc::new(exchange::Exchange::new(format!("127.0.0.1:{port}")));

    for _ in 0..3 {
        strategy::tick(ex.clone()).await;
    }
    strategy::refresh_all(ex.clone(), vec!["BTC", "ETH", "SOL"]).await;
}
