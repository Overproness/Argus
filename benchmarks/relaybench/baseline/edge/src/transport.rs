use std::io::{self, Read};
use std::net::TcpStream;
use std::time::Duration;

fn read_peer(address: &str) -> io::Result<String> {
    let mut peer = TcpStream::connect(address)?;
    peer.set_read_timeout(Some(Duration::from_millis(250)))?;
    let mut body = String::new();
    peer.read_to_string(&mut body)?;
    Ok(body)
}

pub async fn sample_peer(address: String) -> io::Result<String> {
    tokio::task::spawn_blocking(move || read_peer(&address))
        .await
        .map_err(io::Error::other)?
}
