use std::io::{Read, Write};
use std::net::TcpStream;

/// Blocking GET with no timeout: a dead peer hangs the caller forever.
pub fn fetch(url: &str) -> std::io::Result<String> {
    let addr = url.trim_start_matches("http://").split('/').next().unwrap_or("");
    let mut stream = TcpStream::connect(addr)?;
    write!(stream, "GET / HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n")?;
    let mut body = String::new();
    stream.read_to_string(&mut body)?;
    Ok(body)
}

/// Retries with no delay between attempts.
pub fn fetch_with_retries(url: &str, attempts: u32) -> std::io::Result<String> {
    let mut last = None;
    for _ in 0..attempts {
        match fetch(url) {
            Ok(body) => return Ok(body),
            Err(e) => last = Some(e),
        }
    }
    Err(last.expect("at least one attempt"))
}
