use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

pub struct Exchange {
    addr: String,
}

impl Exchange {
    pub fn new(addr: String) -> Self {
        Exchange { addr }
    }

    // BUG under test: a synchronous, blocking socket call on what ends up being an async path.
    #[tracing::instrument(skip(self))]
    pub fn fetch_price(&self, symbol: &str) -> f64 {
        let mut stream = TcpStream::connect(&self.addr).unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        write!(stream, "GET {symbol}").unwrap();
        stream.shutdown(std::net::Shutdown::Write).unwrap(); // done sending: let the server see EOF
        let mut body = String::new();
        stream.read_to_string(&mut body).unwrap();
        body.trim().parse().unwrap_or(0.0)
    }
}
