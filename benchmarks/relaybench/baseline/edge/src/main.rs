use edge::{cache::Cache, summary::SummaryClient, supervisor};
use std::{sync::Mutex, time::Duration};

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), String> {
    let cache = Cache { rows: Mutex::new(vec!["parcel".to_owned()]) };
    let client = SummaryClient { delay: Duration::from_millis(5) };
    let rows = cache.snapshot(Duration::from_millis(5)).await;
    let _summary = client.dashboard().await;
    supervisor::refresh(false).await?;
    assert_eq!(rows.len(), 1);
    Ok(())
}
