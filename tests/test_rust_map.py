import pytest
from conftest import FIXTURES

from auditor.analysis import RepoMap
from auditor.langs.base import expand_braced
from auditor.report import to_json, to_markdown


@pytest.fixture(scope="module")
def repo():
    return RepoMap(FIXTURES / "rust_trader").load()


def findings(repo, rule):
    return [f for f in repo.findings if f.rule == rule]


def test_expand_use():
    assert expand_braced("std::{fs, io::{self, Read}}", "::") == [
        ("fs", "std.fs"), ("io", "std.io"), ("Read", "std.io.Read"),
    ]
    assert expand_braced("reqwest::blocking::Client as C", "::") == [("C", "reqwest.blocking.Client")]
    assert expand_braced("tokio::prelude::*", "::") == []


def test_sync_api_call_reached_from_async_loop(repo):
    chained = [f for f in findings(repo, "blocking-in-async") if f.chain]
    assert len(chained) == 1
    f = chained[0]
    assert f.function == "rust_trader::strategy::run_strategy"
    assert f.chain[:3] == [
        "rust_trader::strategy::run_strategy",
        "rust_trader::strategy::compute_signal",
        "rust_trader::exchange::Exchange::fetch_price",
    ]
    assert f.chain[-1] == "http @ src/exchange.rs:16"
    assert f.reached_from == ["rust_trader::main"]


def test_direct_blocking_sleep_in_async(repo):
    direct = [f for f in findings(repo, "blocking-in-async") if not f.chain]
    assert [(f.file, f.line) for f in direct] == [("src/main.rs", 15)]


def test_spawn_blocking_is_not_flagged(repo):
    assert not any(f.line == 24 for f in findings(repo, "blocking-in-async"))
    offloaded = [b for b in repo.boundaries if b.call.context == "offloaded"]
    assert offloaded == [] or all(b.call.line == 24 for b in offloaded)


def test_constructor_is_not_io(repo):
    assert not any(b.call.name == "new" for b in repo.boundaries)


def test_timeouts(repo):
    by_fn = {f.function: f for f in findings(repo, "io-without-timeout")}
    assert by_fn["rust_trader::market_data::fetch_book"].severity == "medium"
    # reqwest::blocking has a 30s default, so this is a low-severity reminder.
    assert by_fn["rust_trader::exchange::Exchange::fetch_price"].severity == "low"
    assert "rust_trader::market_data::fetch_book_bounded" not in by_fn


def test_io_in_loop_through_call(repo):
    per_item = [f for f in findings(repo, "io-in-loop") if f.severity == "medium"]
    assert [f.function for f in per_item] == ["rust_trader::market_data::refresh_all"]


def test_lock_across_await(repo):
    [f] = findings(repo, "lock-across-await")
    assert (f.file, f.line) == ("src/strategy.rs", 26)


def test_recursion_and_nesting(repo):
    assert [f.function for f in findings(repo, "recursion")] == ["rust_trader::risk::exposure"]
    assert [f.function for f in findings(repo, "nested-loops")] == ["rust_trader::risk::correlation_matrix"]


def test_report_renders(repo):
    md = to_markdown(to_json(repo))
    assert "## I/O boundary inventory" in md and "blocking-in-async" in md
