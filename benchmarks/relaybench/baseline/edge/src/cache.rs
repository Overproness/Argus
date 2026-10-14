use std::sync::Mutex;
use std::time::Duration;

pub struct Cache {
    pub rows: Mutex<Vec<String>>,
}

impl Cache {
    pub async fn snapshot(&self, publish_delay: Duration) -> Vec<String> {
        let snapshot = {
            let guard = self.rows.lock().unwrap();
            guard.clone()
        };
        tokio::time::sleep(publish_delay).await;
        snapshot
    }
}
