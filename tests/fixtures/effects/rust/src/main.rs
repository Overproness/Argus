mod feed;
mod orders;

#[tokio::main]
async fn main() {
    loop {
        feed::tick().await;
    }
}
