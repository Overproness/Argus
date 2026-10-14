use std::time::Duration;

pub struct SummaryClient {
    pub delay: Duration,
}

impl SummaryClient {
    pub async fn region_stats(&self) -> u64 {
        tokio::time::sleep(self.delay).await;
        7
    }

    pub async fn supply_stats(&self) -> u64 {
        tokio::time::sleep(self.delay).await;
        11
    }

    pub async fn dashboard(&self) -> (u64, u64) {
        let (region, supply) = tokio::join!(self.region_stats(), self.supply_stats());
        (region, supply)
    }
}
