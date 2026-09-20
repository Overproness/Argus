"""Static pattern rules: each fires on its bad shape and stays quiet on the good one."""
import sys

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "plugins" / "argus" / "scripts"))
from auditor.analysis import RepoMap  # noqa: E402

RUST = '''
use std::time::SystemTime;
async fn feed(rx: Rx) {
    let (tx, rx2) = tokio::sync::mpsc::unbounded_channel();
    let price: f64 = 1.5;
    let n = body.parse::<u32>().unwrap();
    tokio::spawn(work());
    let a = fetch_a().await;
    let b = fetch_b().await;
    let t = SystemTime::now();
    let dt = t.elapsed();
}
async fn good(rx: Rx) {
    let (tx, rx2) = tokio::sync::mpsc::channel(100);
    let cents: i64 = 150;
    let h = tokio::spawn(work());
    let a = fetch_a().await;
    let b = fetch_b(a).await;
    h.await;
}
'''
PY = '''
import asyncio, time
async def feed(db):
    q = asyncio.Queue()
    asyncio.create_task(work())
    a = await fetch_a()
    b = await fetch_b()
    rows = cur.fetchall()
    t0 = time.time()
    dt = time.time() - t0
    m = re.compile(r"(a+)+$")

async def crunch(n):
    for i in range(n):
        for j in range(n):
            x = i * j

def retry(fn):
    for attempt in range(5):
        time.sleep(2 ** attempt)
'''


@pytest.fixture(scope="module")
def rules(tmp_path_factory):
    d = tmp_path_factory.mktemp("repo")
    (d / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (d / "src").mkdir()
    (d / "src" / "main.rs").write_text(RUST)
    (d / "app.py").write_text(PY)
    m = RepoMap(d).load()
    out: dict[str, set[str]] = {}
    for f in m.findings:
        out.setdefault(f.function.rsplit("::", 1)[-1].rsplit(".", 1)[-1], set()).add(f.rule)
    return out


@pytest.mark.parametrize("fn", ["feed"])
def test_rust_and_python_feed(rules, fn):
    assert {"unbounded-channel", "float-money", "panic-on-external-data", "fire-and-forget-task",
            "sequential-awaits", "wallclock-interval"} <= rules[fn]  # the rust `feed`; python's shares the name


def test_python_specific(rules):
    assert {"redos-regex", "unbounded-query", "backoff-without-jitter"} <= rules["feed"] | rules["retry"]
    assert "cpu-heavy-in-async" in rules["crunch"]


def test_good_code_is_quiet(rules):
    assert not {"unbounded-channel", "float-money", "fire-and-forget-task", "sequential-awaits"} & rules.get("good", set())
