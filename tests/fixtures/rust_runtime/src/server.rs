//! A minimal, dependency-free server that answers slowly, so `Exchange::fetch_price` has
//! something real and local to block on: read anything sent, sleep, write a number, close.
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::Duration;

pub fn spawn(delay_ms: u64) -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            handle(stream, delay_ms);
        }
    });
    port
}

fn handle(mut stream: TcpStream, delay_ms: u64) {
    let mut buf = Vec::new();
    let _ = stream.read_to_end(&mut buf); // drains to EOF: the client shuts its write side when done
    std::thread::sleep(Duration::from_millis(delay_ms));
    let _ = stream.write_all(b"42");
}
