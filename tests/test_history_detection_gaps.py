"""Regression cases from rug_advisor and swapper2_rust_code history.

The integration probes in .audit run real historical code; these small cases
exercise detection and false-positive controls without trading dependencies.
"""
from auditor.analysis import RepoMap


def mapped(tmp_path, source, extension):
    (tmp_path / ("app." + extension)).write_text(source, encoding="utf-8")
    repo = RepoMap(tmp_path).load()
    assert not repo.parse_errors
    return repo


def test_rust_builtin_output_macros_and_offload(tmp_path):
    repo = mapped(tmp_path, '''
use std::println as say;
use crate::logsink::outln;
async fn reader() {
    println!("tick");
    std::eprintln!("tick");
    say!("tick");
    outln!("tick");
    tokio::task::spawn_blocking(|| println!("worker")).await;
}
''', "rs")
    boundaries = [b for b in repo.boundaries if b.kind == "stdio"]
    assert len(boundaries) == 4
    assert len([f for f in repo.findings if f.rule == "blocking-in-async"]) == 3
    assert boundaries[-1].call.context == "offloaded"


def test_rust_shadowed_output_macro(tmp_path):
    repo = mapped(tmp_path, '''
macro_rules! println { ($x:expr) => { queue($x) }; }
async fn reader() { println!("queued"); }
''', "rs")
    assert not [b for b in repo.boundaries if b.kind == "stdio"]


def test_rust_output_does_not_hide_more_severe_network_witness(tmp_path):
    repo = mapped(tmp_path, '''
fn network() { reqwest::blocking::get("local"); }
fn helper() { println!("start"); network(); }
async fn reader() { helper(); }
''', "rs")
    [finding] = [f for f in repo.findings if f.rule == "blocking-in-async"]
    assert finding.severity == "high"
    assert finding.chain[-1].startswith("http @")


def test_rust_typed_file_writes_and_memory_controls(tmp_path):
    repo = mapped(tmp_path, '''
use std::fs::File;
use std::io::Write;
struct TeeWriter { file: File }
impl TeeWriter {
    fn write(&mut self, buf: &[u8]) {
        std::io::stderr().write(buf);
        self.file.write_all(buf);
    }
}
async fn reader(writer: &mut TeeWriter, memory: &mut Vec<u8>, f: &mut File) {
    writer.write(b"x");
    memory.write_all(b"x");
    f.write_all(b"x");
    let mut output = File::create("local").unwrap();
    output.write_all(b"x");
    { let mut output = Vec::new(); output.write_all(b"memory"); }
    output.flush();
}
''', "rs")
    writes = [b for b in repo.boundaries if b.call.name in {"write", "write_all", "flush"}]
    assert len(writes) == 5
    assert not any("memory" in b.call.receiver for b in writes)
    assert len([f for f in repo.findings if f.rule == "blocking-in-async" and f.chain]) == 1


def test_rust_solana_sync_async_and_shadowing(tmp_path):
    repo = mapped(tmp_path, '''
use solana_client::rpc_client::RpcClient;
use solana_client::nonblocking::rpc_client::RpcClient as AsyncRpcClient;
struct Service { client: RpcClient }
impl Service { fn slot(&self) { self.client.get_slot(); } }
async fn reader(service: &Service, cache: &Cache, a: &AsyncRpcClient) {
    service.slot();
    let client = RpcClient::new("http://localhost");
    client.get_slot();
    { let client = Cache::new(); client.get_slot(); }
    client.get_slot();
    a.get_slot().await;
    let unused_future = a.get_slot();
    cache.get_slot();
    client.get_transport_stats();
    tokio::task::spawn_blocking(move || client.get_slot()).await;
}
''', "rs")
    rpc = [b for b in repo.boundaries if b.kind == "rpc"]
    assert len(rpc) == 5
    assert len([b for b in rpc if not b.blocking]) == 1
    assert len([f for f in repo.findings if f.rule == "blocking-in-async"]) == 3
    assert all(b.confidence == "exact" for b in rpc)


def test_rust_rpc_futures_polled_by_macros_and_string_lookalikes(tmp_path):
    repo = mapped(tmp_path, '''
use solana_client::nonblocking::rpc_client::RpcClient;
macro_rules! fallback {
    ($label:expr, $primary:expr, $secondary:expr) => {{
        match $primary.await { Ok(v) => Ok(v), Err(_) => $secondary.await }
    }};
}
async fn reader(a: &RpcClient, b: &RpcClient) {
    tokio::try_join!(a.get_slot(), b.get_balance(&key));
    fallback!("get_account(fake)", a.get_account(&key), b.get_account(&key));
    println!("a.get_slot().await is documentation");
}
''', "rs")
    fn = next(f for f in repo.functions.values() if f.name == "reader")
    assert len([c for c in fn.calls if c.name == "get_account"]) == 2
    assert len([c for c in fn.calls if c.name == "get_slot"]) == 1
    assert all(c.awaited for c in fn.calls if c.name in {"get_slot", "get_account", "get_balance"})
    assert len([b for b in repo.boundaries if b.kind == "rpc"]) == 2  # one boundary per statement


def test_rust_rpc_field_through_cross_file_option_wrapper(tmp_path):
    (tmp_path / "wrapper.rs").write_text('''
use solana_client::rpc_client::RpcClient;
pub struct SolanaRpc { pub client: RpcClient }
''', encoding="utf-8")
    repo = mapped(tmp_path, '''
fn slot(rpc_ref: Option<&crate::wrapper::SolanaRpc>) -> u64 {
    match rpc_ref {
        Some(rpc) => rpc.client.get_slot().unwrap_or(0),
        None => 0,
    }
}
async fn reader(rpc_ref: Option<&crate::wrapper::SolanaRpc>) {
    slot(rpc_ref);
    tokio::task::block_in_place(|| slot(rpc_ref));
}
''', "rs")
    [boundary] = [b for b in repo.boundaries if b.kind == "rpc"]
    assert boundary.blocking
    [finding] = [f for f in repo.findings if f.rule == "blocking-in-async"]
    assert finding.line == 9
    assert finding.chain[-1].startswith("rpc @")


GRPC = '''
import asyncio
import grpc
from api import geyser_pb2_grpc as generated
async def reader():
    async with grpc.aio.secure_channel("local", creds) as channel:
        stub = generated.GeyserStub(channel)
        stream = stub.Subscribe(requests{deadline})
        {read}
'''


def test_python_grpc_implicit_iterator_read(tmp_path):
    repo = mapped(tmp_path, GRPC.format(deadline="", read="async for update in stream:\n            consume(update)"), "py")
    [finding] = [f for f in repo.findings if f.rule == "io-without-timeout"]
    assert finding.line == 9
    [boundary] = [b for b in repo.boundaries if b.kind == "rpc"]
    assert boundary.call.awaited and not boundary.blocking


def test_python_grpc_deadline_and_wait_for_controls(tmp_path):
    source = GRPC.format(deadline=", timeout=3", read="async for update in stream:\n            consume(update)")
    source += GRPC.replace("reader", "bounded").format(deadline="", read="await asyncio.wait_for(stream.__anext__(), timeout=3)")
    source += GRPC.replace("reader", "outer").format(deadline="", read="async with asyncio.timeout(3):\n            async for update in stream:\n                consume(update)")
    source += GRPC.replace("reader", "iterator").format(deadline="", read="ait = stream.__aiter__()\n        await asyncio.wait_for(ait.__anext__(), timeout=3)")
    repo = mapped(tmp_path, source, "py")
    assert len([b for b in repo.boundaries if b.kind == "rpc"]) == 4
    assert not [f for f in repo.findings if f.rule == "io-without-timeout"]


def test_python_unrelated_subscribe_and_async_iterators(tmp_path):
    repo = mapped(tmp_path, '''
import grpc
async def reader(local, cache):
    stream = cache.Subscribe()
    async for update in stream: consume(update)
    async for update in local: consume(update)
''', "py")
    assert not [b for b in repo.boundaries if b.kind == "rpc"]


def test_python_unrelated_timeout_does_not_bound_grpc_read(tmp_path):
    source = GRPC.format(deadline="", read="async for update in stream:\n            await consume(update, timeout=3)")
    source += '\nimport httpx\nclient = httpx.AsyncClient(timeout=1)\n'
    repo = mapped(tmp_path, source, "py")
    [boundary] = [b for b in repo.boundaries if b.kind == "rpc"]
    assert not repo.bounded(boundary)
    assert any(f.rule == "io-without-timeout" and f.line == 9 for f in repo.findings)


def test_rust_unrelated_http_timeout_does_not_bound_solana_rpc(tmp_path):
    repo = mapped(tmp_path, '''
use solana_client::nonblocking::rpc_client::RpcClient;
async fn reader(rpc: &RpcClient) {
    let http = reqwest::Client::builder().timeout(Duration::from_secs(1)).build();
    rpc.get_slot().await;
    tokio::time::timeout(Duration::from_secs(2), rpc.get_slot()).await;
}
''', "rs")
    [missing] = [f for f in repo.findings if f.rule == "io-without-timeout"]
    assert missing.line == 5


def test_rust_retry_counter_does_not_bound_bypassing_error_branch(tmp_path):
    repo = mapped(tmp_path, '''
use solana_client::rpc_client::RpcClient;
fn lookup(client: &RpcClient) {
    let mut non_429_attempts = 0;
    loop {
        match client.get_slot() {
            Ok(value) => return,
            Err(e) => {
                if e.is_rate_limit() {
                    std::thread::sleep(Duration::from_millis(500));
                    continue;
                }
                non_429_attempts += 1;
                if non_429_attempts >= 15 { return; }
            }
        }
    }
}
fn bounded(client: &RpcClient) {
    let mut attempts = 0;
    loop {
        attempts += 1;
        if attempts >= 15 { return; }
        match client.get_slot() {
            Ok(value) => return,
            Err(e) => {
                std::thread::sleep(Duration::from_millis(500));
                continue;
            }
        }
    }
}
''', "rs")
    assert [f.function.rsplit("::", 1)[-1] for f in repo.findings if f.rule == "unbounded-retry"] == ["lookup"]
