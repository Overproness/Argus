"""Language-neutral runtime evidence: OTLP spans (any instrumented language) and heartbeat stalls."""
import gzip
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from conftest import FIXTURES, ROOT

from auditor import protowire as pw
from auditor.repro.faults import FaultServer
from auditor.trace import otlp, spans
from auditor.trace.evidence import Evidence
from auditor.trace.store import Trace

CLI = [sys.executable, str(ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py")]
JAVAAGENT = Path(os.environ.get("ARGUS_OTEL_JAVAAGENT", Path.home() / ".cache" / "argus" / "opentelemetry-javaagent.jar"))


def kv(key, value):
    if isinstance(value, str):
        any_value = pw.enc_field(1, value)
    elif isinstance(value, float):
        any_value = pw.enc_field(4, ("double", value))
    else:
        any_value = pw.enc_field(3, value)
    return pw.enc_field(1, key) + pw.enc_field(2, any_value)


def pb_request(*span_bytes, service="svc"):
    scope = b"".join(pw.enc_field(2, s) for s in span_bytes)
    rs = pw.enc_field(1, pw.enc_field(1, kv("service.name", service))) + pw.enc_field(2, scope)
    return pw.enc_field(1, rs)


def pb_span(span_id, name, start, end, parent=b"", kind=1, attrs=(), status=0):
    out = (pw.enc_field(1, b"\x01" * 16) + pw.enc_field(2, span_id) + pw.enc_field(5, name) + pw.enc_field(6, kind)
           + pw.enc_field(7, ("fixed64", start)) + pw.enc_field(8, ("fixed64", end)))
    if parent:
        out += pw.enc_field(4, parent)
    for k, v in attrs:
        out += pw.enc_field(9, kv(k, v))
    if status:
        out += pw.enc_field(15, pw.enc_field(3, status))
    return out


def test_protobuf_decoding():
    body = pb_request(pb_span(b"\x02" * 8, "Client.fetch", 1_000, 3_000, attrs=[
        ("code.function", "fetch"), ("retries", -5), ("ratio", 1.5)], status=2))
    [s] = otlp.decode(body, "application/x-protobuf")
    assert (s.span_id, s.name, s.kind, s.start_ns, s.end_ns, s.status) == ("02" * 8, "Client.fetch", 1, 1000, 3000, 2)
    assert s.attrs == {"code.function": "fetch", "retries": -5, "ratio": 1.5}
    assert s.resource == {"service.name": "svc"} and s.dur_s == 2e-6


def test_json_gzip_and_file_import(tmp_path):
    doc = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "j"}}]},
                              "scopeSpans": [{"spans": [{
                                  "traceId": "0a" * 16, "spanId": "0b" * 8, "parentSpanId": "0c" * 8, "name": "GET",
                                  "kind": 3, "startTimeUnixNano": "10", "endTimeUnixNano": "25",
                                  "attributes": [{"key": "http.request.method", "value": {"stringValue": "GET"}},
                                                 {"key": "n", "value": {"intValue": "7"}}],
                                  "status": {"code": 2}}]}]}]}
    [s] = otlp.decode(gzip.compress(json.dumps(doc).encode()), "application/json", "gzip")
    assert (s.parent_id, s.kind, s.status, s.attrs["n"], s.start_ns) == ("0c" * 8, 3, 2, 7, 10)
    lines = tmp_path / "collector.json"
    lines.write_text(json.dumps(doc) + "\n" + json.dumps(doc) + "\n")  # file exporter: one request per line
    assert len(otlp.load_file(lines)) == 2


def test_receiver_over_http():
    rec = otlp.Receiver().start()
    try:
        env = rec.env("x")
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == rec.endpoint
        body = pb_request(pb_span(b"\x03" * 8, "a", 1, 2))
        req = urllib.request.Request(rec.endpoint + "/v1/traces", data=body,
                                     headers={"Content-Type": "application/x-protobuf"})
        assert urllib.request.urlopen(req, timeout=5).status == 200
        bad = urllib.request.Request(rec.endpoint + "/v1/traces", data=b"{not json",
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad, timeout=5)
    finally:
        rec.stop()
    assert len(rec.spans) == 1 and rec.requests == 1 and rec.errors


SDK_SCRIPT = r'''
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider(resource=Resource.create({"service.name": "interop"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
t = trace.get_tracer("argus-test")
with t.start_as_current_span("outer", attributes={"code.function.name": "svc.api.handler", "items.count": 3}):
    with t.start_as_current_span("GET", kind=trace.SpanKind.CLIENT,
                                 attributes={"http.request.method": "GET", "url.full": "http://127.0.0.1:9/x"}) as s:
        s.set_status(trace.Status(trace.StatusCode.ERROR))
provider.shutdown()
'''


def test_real_opentelemetry_sdk_exports_to_the_receiver(tmp_path):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    rec = otlp.Receiver().start()
    try:
        script = tmp_path / "sdk.py"
        script.write_text(SDK_SCRIPT)
        r = subprocess.run([sys.executable, str(script)], env={**os.environ, **rec.env("interop")},
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
    finally:
        rec.stop()
    by_name = {s.name: s for s in rec.spans}
    outer, client = by_name["outer"], by_name["GET"]
    assert client.parent_id == outer.span_id and client.kind == 3 and client.status == 2
    assert outer.attrs["items.count"] == 3 and outer.resource["service.name"] == "interop"
    assert outer.resource["telemetry.sdk.language"] == "python"


@pytest.mark.skipif(shutil.which("go") is None, reason="go not installed")
def test_real_go_otel_sdk_exports_to_the_receiver(tmp_path):
    fx = FIXTURES / "otel_go"
    exe = tmp_path / ("otelgo.exe" if sys.platform == "win32" else "otelgo")
    subprocess.run(["go", "build", "-o", str(exe), "."], cwd=fx, check=True, capture_output=True,
                   text=True, timeout=180)
    rec = otlp.Receiver().start()
    try:
        r = subprocess.run([str(exe)], env={**os.environ, **rec.env("interop-go")},
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
    finally:
        rec.stop()
    by_name = {s.name: s for s in rec.spans}
    outer, client = by_name["outer"], by_name["GET"]
    assert client.parent_id == outer.span_id and client.kind == 3 and client.status == 2
    assert outer.attrs["items.count"] == 3 and outer.resource["service.name"] == "interop-go"


# --- mapping spans and heartbeats to the static map ------------------------------------------------

MAP = {
    "functions": [
        {"id": "svc/api.py:10:0", "qualname": "svc.api.handler", "lang": "python", "lines": [10, 30]},
        {"id": "svc/api.py:32:0", "qualname": "svc.api.fetch_quote", "lang": "python", "lines": [32, 40]},
    ],
    "findings": [
        {"rule": "blocking-in-async", "severity": "high", "function": "svc.api.handler", "file": "svc/api.py",
         "line": 12, "chain": ["svc.api.handler", "svc.api.fetch_quote", "http @ svc/api.py:36"], "message": ""},
        {"rule": "io-in-loop", "severity": "medium", "function": "svc.api.fetch_quote", "file": "svc/api.py",
         "line": 36, "chain": [], "message": ""},
    ],
}
S = 1_000_000_000  # one second in ns


def test_spans_and_heartbeat_join_the_map(tmp_path):
    repo, trace_dir = tmp_path / "repo", tmp_path / "trace"
    t0 = 1_700_000_000 * S
    sp = [
        otlp.Span("t", "h", "", "handler", 1, t0, t0 + 3 * S, {"code.function.name": "svc.api.handler"}),
        otlp.Span("t", "f", "h", "fetch", 1, t0 + S // 10, t0 + 29 * S // 10,
                  {"code.filepath": str(repo / "svc" / "api.py"), "code.lineno": 35}),
    ] + [otlp.Span("t", f"c{i}", "f", "GET", 3, t0 + a * S // 100, t0 + b * S // 100,
                   {"http.request.method": "GET", "url.full": "http://exchange/ticker"})
         for i, (a, b) in enumerate([(20, 280), (280, 284), (284, 288)])]  # one slow call, then two quick ones
    otlp.write_jsonl(sp, trace_dir / "1.spans.jsonl")
    ticks = [t0 + k * S // 10 for k in range(3)] + [t0 + 29 * S // 10 + k * S // 10 for k in range(3)]
    hb = trace_dir / "1.heartbeat.jsonl"
    hb.write_text("\n".join([json.dumps({"meta": {"regex": "tick", "stall_ms": 100, "argv": ["svc"]}})]
                            + [json.dumps({"t_ns": t, "line": "tick"}) for t in ticks]))

    t = spans.attach(Trace([], [], []), trace_dir, MAP, repo)
    keys = {(c.file, c.line) for c in t.calls}
    assert keys == {("svc/api.py", 10), ("svc/api.py", 32)}
    assert len(t.io) == 3 and {r.target for r in t.io} == {"GET http://exchange/ticker"}
    [stall] = t.stalls
    assert (stall.file, stall.line) == ("svc/api.py", 32) and 2.4 < stall.dur < 2.8
    assert [q for _, _, q in stall.stack] == ["svc.api.handler", "svc.api.fetch_quote", "http GET http://exchange/ticker"]

    ev = {f["rule"]: f["evidence"] for f in Evidence(MAP, t).build()["findings"]}
    assert ev["blocking-in-async"]["status"] == "confirmed"
    assert ev["io-in-loop"]["status"] == "confirmed" and "up to 3 external call(s)" in ev["io-in-loop"]["detail"]


def test_heartbeat_without_spans_is_a_program_level_stall(tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    ticks = [0, 1, 2, 3, 15, 16, 17]  # tenths of a second: a 1.2 s hole
    (trace_dir / "x.heartbeat.jsonl").write_text("\n".join(
        [json.dumps({"meta": {"regex": ".", "stall_ms": 100}})] + [json.dumps({"t_ns": k * S // 10}) for k in ticks]))
    t = spans.attach(Trace([], [], []), trace_dir, MAP, tmp_path)
    [s] = t.stalls
    assert s.file == "" and s.qualname.startswith("(program-level") and 1.0 < s.dur < 1.2


# --- end to end through the CLI ---------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cli_heartbeat_on_a_node_program(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "loop.mjs").write_text(
        "function crunch(ms) { const end = Date.now() + ms; while (Date.now() < end) {} }\n"
        "const t = setInterval(() => console.log('tick'), 20);\n"
        "setTimeout(() => crunch(600), 150);\n"
        "setTimeout(() => clearInterval(t), 1200);\n")
    subprocess.run(CLI + ["map", str(repo)], check=True, capture_output=True)
    # The heartbeat channel on its own (test_profiles covers it next to Node's profiler).
    r = subprocess.run(CLI + ["trace", str(repo), "--heartbeat", "^tick", "--no-node", "--", "node", "loop.mjs"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((repo / ".audit" / "trace.json").read_text())
    [u] = data["unpredicted_stalls"]
    assert u["function"].startswith("(program-level") and 0.4 < u["worst_s"] < 1.0
    assert {run["source"] for run in data["meta"]["runs"]} == {"heartbeat"}


NODE_OTEL = Path(os.environ.get("ARGUS_OTEL_NODE", Path.home() / ".cache" / "argus" / "node-otel"))
NODE_REGISTER = NODE_OTEL / "node_modules" / "@opentelemetry" / "auto-instrumentations-node" / "build" / "src" / "register.js"
NODE_APP = """const http = require("node:http");

function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => { res.resume(); res.on("end", resolve); }).on("error", reject);
  });
}

async function main(url) {
  for (let i = 0; i < 3; i++) {
    await get(url);
  }
}

main(process.argv[2]);
"""


@pytest.mark.skipif(not (shutil.which("node") and NODE_REGISTER.exists()),
                    reason="needs node and @opentelemetry/auto-instrumentations-node (ARGUS_OTEL_NODE)")
def test_cli_otlp_with_node_auto_instrumentation(tmp_path):
    repo = tmp_path / "nodeapp"
    repo.mkdir()
    (repo / "app.cjs").write_text(NODE_APP)
    subprocess.run(CLI + ["map", str(repo)], check=True, capture_output=True)
    srv = FaultServer(body="ok", latency=0.1).start()

    def trace(*flags):
        shutil.rmtree(repo / ".audit" / "trace", ignore_errors=True)  # runs accumulate; compare them one by one
        env = {**os.environ, "OTEL_NODE_ENABLED_INSTRUMENTATIONS": "http"}
        r = subprocess.run(CLI + ["trace", str(repo), "--otlp", *flags, "--", "node", "--require", str(NODE_REGISTER),
                                  "app.cjs", srv.url + "/quote"], env=env, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        data = json.loads((repo / ".audit" / "trace.json").read_text())
        [ext] = data["external_calls"]  # the exporter's chunked uploads were decoded
        assert ext["count"] == 3 and ext["target"].endswith("/quote") and ext["p50_s"] >= 0.1
        return {(f["rule"], f["function"]): f["evidence"] for f in data["findings"]}

    try:
        # Auto-instrumentation makes no spans for the repo's own functions: say so instead of "never ran".
        assert {e["status"] for e in trace("--no-node").values()} == {"not-traced"}
        # With Node's profiler on as well (the default), the same run has function-level evidence.
        ev = trace()
        assert ev[("io-in-loop", "app.main")]["status"] == "confirmed"
        assert "not-traced" not in {e["status"] for e in ev.values()}
    finally:
        srv.stop()


JAVA_MAIN = """package demo;

public class Main {
    public static void main(String[] args) throws Exception {
        for (int i = 0; i < 3; i++) {
            Client.fetch(args[0]);
        }
    }
}
"""


@pytest.mark.skipif(not (shutil.which("javac") and shutil.which("java") and JAVAAGENT.exists()),
                    reason="needs a JDK and the OpenTelemetry Java agent (ARGUS_OTEL_JAVAAGENT)")
def test_cli_otlp_with_the_java_agent(tmp_path):
    repo = tmp_path / "javaapp"
    shutil.copytree(FIXTURES / "native" / "java_lib", repo)
    (repo / "src/main/java/demo/Main.java").write_text(JAVA_MAIN)
    classes = tmp_path / "classes"
    srcs = [str(p) for p in (repo / "src/main/java").rglob("*.java")]
    subprocess.run(["javac", "-d", str(classes), *srcs], check=True, capture_output=True)
    subprocess.run(CLI + ["map", str(repo)], check=True, capture_output=True)
    srv = FaultServer(body="ok").start()
    try:
        # No method list given: `trace --otlp` derives it from the map (demo.Client[fetch];demo.Main[main]).
        env = {k: v for k, v in os.environ.items() if k != "OTEL_INSTRUMENTATION_METHODS_INCLUDE"}
        env["OTEL_JAVAAGENT_LOGGING"] = "none"
        r = subprocess.run(CLI + ["trace", str(repo), "--otlp", "--", "java", f"-javaagent:{JAVAAGENT}", "-cp",
                                  str(classes), "demo.Main", srv.url], env=env, capture_output=True, text=True,
                           timeout=300)
    finally:
        srv.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Java agent method spans for 2 class(es)" in r.stdout
    data = json.loads((repo / ".audit" / "trace.json").read_text())
    assert "java" in {run["lang"] for run in data["meta"]["runs"]}
    fns = {f["function"]: f for f in data["functions"]}
    assert fns["demo.Client.fetch"]["calls"] == 3 and fns["demo.Main.main"]["calls"] == 1
    [ext] = [x for x in data["external_calls"] if x["system"] == "http"]
    assert ext["count"] == 3 and srv.url.split("//")[1] in ext["target"] and ext["callers"] == ["demo.Client.fetch"]
    ev = {(f["rule"], f["function"]): f["evidence"] for f in data["findings"]}
    loop = ev[("io-in-loop", "demo.Main.main")]
    assert loop["status"] == "confirmed" and "up to 3" in loop["detail"]
