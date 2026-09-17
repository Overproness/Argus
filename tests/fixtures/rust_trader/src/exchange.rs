use reqwest::blocking::Client;

pub struct Exchange {
    client: Client,
    base: String,
}

impl Exchange {
    pub fn new(base: &str) -> Self {
        Exchange { client: Client::new(), base: base.to_string() }
    }

    // BUG under test: sync HTTP on what ends up being an async path.
    pub fn fetch_price(&self, symbol: &str) -> f64 {
        let url = format!("{}/ticker/{}", self.base, symbol);
        let resp = self.client.get(&url).send().unwrap();
        resp.json::<f64>().unwrap()
    }
}
