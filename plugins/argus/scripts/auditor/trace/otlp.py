"""OpenTelemetry spans in, from any language: an OTLP/HTTP receiver and file importer.

Any program instrumented with OpenTelemetry (the Java or .NET agents, the Node,
Python, Go, Ruby, PHP or Rust SDKs, ...) exports its spans here when run with

  OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<port>   (set by `trace --otlp`)

Both OTLP/HTTP encodings are accepted: protobuf (the default of most SDKs) and
JSON, optionally gzip-compressed. Collector file-exporter output (JSON lines)
can be imported too. Spans are kept raw as JSON lines under .audit/trace/; the
mapping to repo functions happens at report time (see spans.py), so a newer map
re-maps old traces.
"""
from __future__ import annotations

import gzip
import json
import threading
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import protowire as pw

KINDS = {0: "unspecified", 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str
    name: str
    kind: int
    start_ns: int
    end_ns: int
    attrs: dict = field(default_factory=dict)
    status: int = 0  # 0 unset, 1 ok, 2 error
    resource: dict = field(default_factory=dict)

    @property
    def dur_s(self) -> float:
        return max(0, self.end_ns - self.start_ns) / 1e9


# --- protobuf ----------------------------------------------------------------------

def _any_value(b: memoryview):
    for fno, wt, v in pw.fields(b):
        if fno == 1:
            return bytes(v).decode("utf8", "replace")
        if fno == 2:
            return bool(v)
        if fno == 3:
            return pw.int64(v)
        if fno == 4:
            return pw.double(v)
        if fno == 5:  # ArrayValue
            return [_any_value(x) for f2, _, x in pw.fields(v) if f2 == 1]
        if fno == 6:  # KeyValueList
            return dict(_key_value(x) for f2, _, x in pw.fields(v) if f2 == 1)
        if fno == 7:
            return bytes(v).hex()
    return None


def _key_value(b: memoryview) -> tuple[str, object]:
    key, val = "", None
    for fno, _, v in pw.fields(b):
        if fno == 1:
            key = bytes(v).decode("utf8", "replace")
        elif fno == 2:
            val = _any_value(v)
    return key, val


def _span_pb(b: memoryview, resource: dict) -> Span:
    s = Span("", "", "", "", 0, 0, 0, {}, 0, resource)
    for fno, wt, v in pw.fields(b):
        if fno == 1:
            s.trace_id = bytes(v).hex()
        elif fno == 2:
            s.span_id = bytes(v).hex()
        elif fno == 4:
            s.parent_id = bytes(v).hex()
        elif fno == 5:
            s.name = bytes(v).decode("utf8", "replace")
        elif fno == 6:
            s.kind = v
        elif fno == 7:
            s.start_ns = pw.fixed64(v)
        elif fno == 8:
            s.end_ns = pw.fixed64(v)
        elif fno == 9:
            k, val = _key_value(v)
            s.attrs[k] = val
        elif fno == 15:
            for f2, _, x in pw.fields(v):
                if f2 == 3:
                    s.status = x
    return s


def decode_protobuf(body: bytes) -> list[Span]:
    out = []
    for fno, _, rs in pw.fields(memoryview(body)):
        if fno != 1:  # ExportTraceServiceRequest.resource_spans
            continue
        resource: dict = {}
        scopes = []
        for f2, _, v in pw.fields(rs):
            if f2 == 1:
                resource = dict(_key_value(x) for f3, _, x in pw.fields(v) if f3 == 1)
            elif f2 == 2:
                scopes.append(v)
        for sc in scopes:
            for f3, _, v in pw.fields(sc):
                if f3 == 2:
                    out.append(_span_pb(v, resource))
    return out


# --- JSON --------------------------------------------------------------------------------

def _json_value(v: dict):
    if not isinstance(v, dict):
        return v
    for k, conv in (("stringValue", str), ("boolValue", bool), ("intValue", int), ("doubleValue", float)):
        if k in v:
            return conv(v[k])
    if "arrayValue" in v:
        return [_json_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _json_value(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def _attrs(items) -> dict:
    return {kv["key"]: _json_value(kv.get("value", {})) for kv in items or []}


def _id(v) -> str:
    """OTLP/JSON ids are hex; some exporters emit base64 (proto3 JSON bytes). Normalize to hex."""
    if not v:
        return ""
    if all(c in "0123456789abcdefABCDEF" for c in v) and len(v) in (16, 32):
        return v.lower()
    import base64
    try:
        return base64.b64decode(v).hex()
    except ValueError:
        return v


def _kind(k) -> int:
    if isinstance(k, int):
        return k
    return {"SPAN_KIND_INTERNAL": 1, "SPAN_KIND_SERVER": 2, "SPAN_KIND_CLIENT": 3,
            "SPAN_KIND_PRODUCER": 4, "SPAN_KIND_CONSUMER": 5}.get(str(k), 0)


def decode_json(doc: dict) -> list[Span]:
    out = []
    for rs in doc.get("resourceSpans") or doc.get("resource_spans") or []:
        resource = _attrs((rs.get("resource") or {}).get("attributes"))
        for ss in rs.get("scopeSpans") or rs.get("scope_spans") or rs.get("instrumentationLibrarySpans") or []:
            for sp in ss.get("spans", []):
                status = sp.get("status") or {}
                code = status.get("code", 0)
                out.append(Span(
                    trace_id=_id(sp.get("traceId")), span_id=_id(sp.get("spanId")),
                    parent_id=_id(sp.get("parentSpanId")), name=sp.get("name", ""), kind=_kind(sp.get("kind", 0)),
                    start_ns=int(sp.get("startTimeUnixNano", 0)), end_ns=int(sp.get("endTimeUnixNano", 0)),
                    attrs=_attrs(sp.get("attributes")),
                    status=code if isinstance(code, int) else {"STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}.get(code, 0),
                    resource=resource))
    return out


def decode(body: bytes, content_type: str = "", content_encoding: str = "") -> list[Span]:
    if content_encoding.lower() == "gzip" or body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    if "json" in content_type.lower() or body[:1] in (b"{", b"["):
        return decode_json(json.loads(body))
    return decode_protobuf(body)


def load_file(path: Path) -> list[Span]:
    """A collector file-exporter dump (one JSON request per line), a single JSON document, or raw protobuf."""
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.lstrip()
    if text[:1] in (b"{", b"["):
        spans = []
        try:
            doc = json.loads(raw)
            for d in doc if isinstance(doc, list) else [doc]:
                spans += decode_json(d)
        except json.JSONDecodeError:
            for line in raw.decode("utf8", "replace").splitlines():
                if line.strip():
                    spans += decode_json(json.loads(line))
        return spans
    return decode_protobuf(raw)


# --- raw storage ---------------------------------------------------------------------------

def write_jsonl(spans: list[Span], path: Path, meta: dict | None = None) -> Path:
    """One span per line; an optional first line {"meta": {...}} records how they were produced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf8") as fh:
        if meta:
            fh.write(json.dumps({"meta": meta}, default=str) + "\n")
        for s in spans:
            fh.write(json.dumps(asdict(s), default=str) + "\n")
    return path


def read_jsonl(path: Path) -> list[Span]:
    out = []
    for line in path.read_text(encoding="utf8").splitlines():
        if line.strip():
            row = json.loads(line)
            if "meta" not in row:
                out.append(Span(**row))
    return out


def read_meta(path: Path) -> dict:
    with path.open(encoding="utf8") as fh:
        first = fh.readline()
    try:
        return json.loads(first).get("meta", {}) if first.strip() else {}
    except (json.JSONDecodeError, AttributeError):
        return {}


# --- receiver ------------------------------------------------------------------------------------

class Receiver:
    """OTLP/HTTP endpoint: POST /v1/traces (protobuf or JSON). Metrics and logs are accepted and dropped."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.spans: list[Span] = []
        self.requests = 0
        self.errors: list[str] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _body(self) -> bytes:
                if "chunked" in self.headers.get("Transfer-Encoding", "").lower():  # e.g. the Node exporter
                    data = bytearray()
                    while True:
                        size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                        if size == 0:
                            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                                pass  # trailers
                            return bytes(data)
                        data += self.rfile.read(size)
                        self.rfile.readline()  # CRLF after each chunk
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_POST(self):
                body = self._body()
                ctype = self.headers.get("Content-Type", "")
                if self.path.rstrip("/").endswith("/v1/traces"):
                    try:
                        spans = decode(body, ctype, self.headers.get("Content-Encoding", ""))
                        with owner._lock:
                            owner.spans += spans
                            owner.requests += 1
                    except Exception as e:  # a bad payload must not kill the program under test
                        owner.errors.append(f"{type(e).__name__}: {e}")
                        self.send_response(400)
                        self.end_headers()
                        return
                is_json = "json" in ctype
                reply = b"{}" if is_json else b""
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if is_json else "application/x-protobuf")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self.host, self.port = self._server.server_address[:2]
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def env(self, service: str = "argus-target") -> dict[str, str]:
        """Environment that points OpenTelemetry SDKs and agents at this receiver."""
        return {
            "OTEL_EXPORTER_OTLP_ENDPOINT": self.endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": self.endpoint + "/v1/traces",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOGS_EXPORTER": "none",
            "OTEL_BSP_SCHEDULE_DELAY": "200",
            "OTEL_TRACES_SAMPLER": "always_on",
            "OTEL_SERVICE_NAME": service,
        }

    def start(self) -> "Receiver":
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
