"""Multi-round investigation: the work queue, the verdict ledger, and the final report data.

The `audit` skill drives rounds:

  queue   ranked, budgeted work items for the next round (.audit/queue.json); opens the round in the ledger.
          One item is one investigation of one function: every queued finding in that function (up to
          MAX_PER_ITEM) rides along, low-severity ones included, so a site costs one investigator, not four.
  ...     one investigator per item returns a verdict JSON list, one entry per finding in `findings`
  record  verdicts into .audit/verdicts.json, cross-checked against .audit/repro.json
  queue   next round: confirmed verdicts spawn caller-level items (effects travel up the call graph);
          rejected verdicts naming a wrong call edge suppress every finding whose chain uses that edge

It stops when the budget is spent, the round cap is reached, or nothing new is left.
Nothing here calls an LLM: selection, bookkeeping and cross-checks are deterministic.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .repro.runner import status_of

SEV_W = {"high": 5.0, "medium": 3.0, "low": 1.0, "info": 0.5}
CONF_W = {"exact": 1.0, "scip": 1.0, "unique": 0.9, "heuristic": 0.8, "name": 0.5}
EVID_W = {"confirmed": 1.5, "measured": 1.2, "not-verifiable": 1.0, "not-exercised": 1.0, "not-observed": 0.3}
VERDICTS = {"confirmed", "rejected", "inconclusive"}
MAX_DERIVED_PER_VERDICT = 3
MAX_PER_ITEM = 5  # findings one investigator handles in one function
DEFAULT_BUDGET = {"total": 15, "per_round": 6, "max_rounds": 3}
# Inconclusive for reasons outside the code (wrong tree, a broken reproduction run) is worth one more try.
RETRYABLE = {"wrong-tree", "repro-error", "repro-check", "environment"}
MAX_ATTEMPTS = 2


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def harness_for(lang: str) -> str:
    """How the investigator can reproduce a finding in this language (every language has at least one way)."""
    from .repro.native import SCAFFOLDS, VERIFIED
    if lang == "python":
        return "in-process (repro/harness.py), or black-box"
    if lang in VERIFIED:
        return "native probe or black-box"
    if lang in SCAFFOLDS:
        return "native probe (template not yet verified on a toolchain) or black-box"
    return "black-box"


def finding_id(f: dict) -> str:
    return f"{f['rule']}@{f['function']}:{f['line']}"


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else None


class State:
    """Everything under .audit/ that the orchestration reads."""

    def __init__(self, out_dir: Path):
        self.out = out_dir
        self.map = _read(out_dir / "map.json")
        if self.map is None:
            raise FileNotFoundError(f"{out_dir / 'map.json'} missing; run `map` first")
        self.trace = _read(out_dir / "trace.json")
        self.repro = _read(out_dir / "repro.json")
        self.ledger = _read(out_dir / "verdicts.json") or {
            "version": 1, "budget": dict(DEFAULT_BUDGET), "rounds": [], "verdicts": {}, "derived": {}}
        self.evidence = {finding_id(f): f["evidence"] for f in (self.trace or {}).get("findings", [])}
        self.hotspot = {h["function"]: h["score"] for h in self.map.get("hotspots", [])}
        self.lang_of = {fn["qualname"]: fn["lang"] for fn in self.map["functions"]}
        self.callers = defaultdict(list)  # callee qualname -> [(caller, line, deadline)]
        for e in self.map["edges"]:
            self.callers[e["callee"]].append((e["caller"], e["line"], e.get("deadline_s")))
        self.entries = {fn["qualname"] for fn in self.map["functions"] if fn.get("is_entry")}

    def save_ledger(self):
        (self.out / "verdicts.json").write_text(json.dumps(self.ledger, indent=2), encoding="utf8")

    def repro_status(self, fid: str, repro_file: str | None = None) -> str:
        return status_of(self.repro, fid, repro_file)

    def issued(self) -> set[str]:
        """Every finding id handed to an investigator so far (primaries and the findings riding along)."""
        out = set()
        for r in self.ledger["rounds"]:
            out.update(r["items"])
            for ids in (r.get("findings") or {}).values():
                out.update(ids)
            for ids in (r.get("same_effect") or {}).values():
                out.update(ids)
        return out


# --- queue ---------------------------------------------------------------------------

def _leaf(item: dict) -> str:
    if item.get("kind") == "derived":  # one follow-up per caller: the caller is the unit of work
        return item["function"]
    names = [n for n in item.get("chain", []) if " @ " not in n and "(" not in n]
    return names[-1] if names else item["function"]


def _suppressed_by(item: dict, wrong_edges: list[tuple[str, str, str]]) -> str | None:
    chain = [n for n in item.get("chain", []) if " @ " not in n] or [item["function"]]
    pairs = set(zip(chain, chain[1:]))
    for a, b, by in wrong_edges:
        if (a, b) in pairs:
            return by
    return None


def _score(item: dict, st: State) -> float:
    ev = (item.get("trace_evidence") or {}).get("status")
    s = SEV_W.get(item["severity"], 1.0) * CONF_W.get(item.get("confidence", "heuristic"), 0.8) * EVID_W.get(ev, 1.0)
    return round(s + min(st.hotspot.get(item["function"], 0.0) / 10.0, 1.0), 3)


def _derive(st: State, fid: str, v: dict) -> list[dict]:
    """Caller-level follow-ups for a confirmed verdict: does the proven effect reach the callers?"""
    rule, rest = fid.split("@", 1)
    function = rest.rsplit(":", 1)[0]
    callers = st.callers.get(function, [])
    # Prefer callers where the effect matters most: entry points, deadlines, hotspots.
    callers = sorted(callers, key=lambda c: (c[0] not in st.entries, c[2] is None, -st.hotspot.get(c[0], 0.0)))
    out = []
    for caller, line, deadline in callers[:MAX_DERIVED_PER_VERDICT]:
        hyp = v.get("hypothesis") or {}
        out.append({
            "id": f"propagated:{rule}@{caller}:{line}", "kind": "derived", "rule": f"propagated:{rule}",
            "severity": v.get("severity", "medium"), "confidence": "exact",
            "lang": st.lang_of.get(caller, "?"), "function": caller, "file": "", "line": line,
            "message": (f"`{function}` was confirmed ({rule}); check whether the effect reaches its caller "
                        f"`{caller}` (call at line {line}"
                        + (f", inside a {deadline:g} s deadline" if deadline else "") + "). "
                        f"Trigger: {hyp.get('trigger', 'as in the confirmed reproduction')}."),
            "chain": [caller, function], "reached_from": [],
            "given": {"confirmed": fid, "evidence": v.get("evidence"), "repro_file": v.get("repro_file")},
        })
    return out


def _brief(it: dict) -> dict:
    d = {k: it.get(k) for k in ("id", "rule", "severity", "line", "message", "chain", "trace_evidence", "given")
         if it.get(k) not in (None, [], {})}
    if it.get("covers"):
        d["same_effect"] = it["covers"]  # other findings with this rule and leaf: they take this verdict
    return d


def _locate(st: State, it: dict, root: Path) -> dict:
    """Where the investigator must look, absolutely: the path is carried as data, never retyped."""
    fn = next((f for f in st.map["functions"] if f["qualname"] == it["function"]
               and (not it.get("file") or f["id"].startswith(it["file"] + ":"))), None)
    rel = it.get("file") or (fn["id"].rsplit(":", 2)[0] if fn else "")
    if not rel:
        return {}
    path = root / rel
    lo, hi = (fn["lines"] if fn else (None, None))
    return {"abs_file": str(path), "file_sha1": paths.file_digest(path),
            "source": paths.excerpt(path, int(it["line"] or lo or 1), lo, hi)}


def queue(out_dir: Path, budget: dict | None = None, rules: set[str] | None = None,
          include_low: bool = False, include_info: bool = False, retry_inconclusive: bool = False,
          dry_run: bool = False) -> dict:
    st = State(out_dir)
    led = st.ledger
    if budget:
        led["budget"].update({k: v for k, v in budget.items() if v is not None})
    b = led["budget"]
    primaries = [i for r in led["rounds"] for i in r["items"]]
    issued = st.issued()
    verdicts = led["verdicts"]
    pending = sorted(i for i in issued if i not in verdicts)
    round_no = len(led["rounds"]) + 1
    remaining = b["total"] - len(primaries)
    wrong_edges = [(v["wrong_edge"][0], v["wrong_edge"][1], fid) for fid, v in verdicts.items()
                   if v.get("verdict") == "rejected" and isinstance(v.get("wrong_edge"), list)
                   and len(v["wrong_edge"]) == 2]

    candidates: list[dict] = []
    for f in st.map["findings"]:
        fid = finding_id(f)
        candidates.append({**f, "id": fid, "kind": "finding", "trace_evidence": st.evidence.get(fid)})
    for fid, v in verdicts.items():
        if v.get("verdict") == "confirmed":
            for d in _derive(st, fid, v):
                led["derived"].setdefault(d["id"], d)
    candidates += list(led["derived"].values())

    def retryable(v: dict) -> bool:
        return (v.get("verdict") == "inconclusive"
                and (retry_inconclusive or v.get("blocked_by") in RETRYABLE)
                and v.get("attempts", 1) < MAX_ATTEMPTS)

    items, skipped, riders = [], [], []
    for c in candidates:
        fid = c["id"]
        v = verdicts.get(fid)
        reason = None
        if v and not retryable(v):
            reason = f"verdict: {v.get('verdict')}"
        elif fid in issued and fid not in verdicts:
            reason = "pending: issued in an earlier round, no verdict recorded yet"
        elif rules and c["rule"] not in rules and c["rule"].split(":")[-1] not in rules:
            reason = "not selected (--rule)"
        elif c["severity"] == "info" and not include_info:
            reason = "info severity"
        elif c["severity"] == "low" and not include_low and not rules:
            reason = "low severity"
        elif (sup := _suppressed_by(c, wrong_edges)) is not None:
            reason = f"suppressed: its chain uses the call edge rejected in {sup}"
        elif (c.get("trace_evidence") or {}).get("status") == "not-observed" and not rules:
            reason = "trace: not-observed under the recorded workload"
        c["score"] = _score(c, st)
        if reason:
            if reason == "low severity":
                riders.append(c)  # investigated for free when its function is queued anyway
            skipped.append({"id": fid, "reason": reason, "severity": c["severity"], "lang": c["lang"]})
            continue
        c["harness"] = harness_for(c["lang"])
        items.append(c)

    # One verdict per underlying effect: same rule and same leaf function ("same_effect" copies it).
    merged: dict[tuple[str, str], dict] = {}
    for it in sorted(items, key=lambda x: -x["score"]):
        key = (it["rule"], _leaf(it))
        if key in merged:
            merged[key].setdefault("covers", []).append(it["id"])
        else:
            merged[key] = it
    # One investigation per function: its findings share the code, the setup and usually the test file.
    by_fn: dict[str, dict] = {}
    for it in sorted(merged.values(), key=lambda x: -x["score"]):
        head = by_fn.get(it["function"])
        if head is None:
            it["findings"] = [_brief(it)]
            by_fn[it["function"]] = it
        elif len(head["findings"]) < MAX_PER_ITEM:
            head["findings"].append(_brief(it))
            head["score"] = round(head["score"] + 0.25 * it["score"], 3)
        else:
            it["findings"] = [_brief(it)]
            by_fn[f"{it['function']}#{it['id']}"] = it
    ranked = sorted(by_fn.values(), key=lambda x: -x["score"])

    stop = None
    take = max(0, min(b["per_round"], remaining))
    if remaining <= 0:
        stop = "budget exhausted"
    elif round_no > b["max_rounds"]:
        stop = "max rounds reached"
    elif not ranked:
        stop = "nothing left to investigate"
    selected = [] if stop else ranked[:take]
    root = Path(st.map["meta"].get("root") or out_dir.parent)
    riding: set[str] = set()
    for it in selected:
        for r in sorted(riders, key=lambda x: -x["score"]):
            if r["function"] == it["function"] and len(it["findings"]) < MAX_PER_ITEM:
                it["findings"].append(_brief(r))
                riding.add(r["id"])
        it["round"] = round_no
        it["repo"] = str(root)
        it.update(_locate(st, it, root))
    skipped = [s for s in skipped if s["id"] not in riding]

    result = {
        "round": None if stop else round_no, "stop": stop,
        "budget": {**b, "issued": len(primaries), "remaining": max(remaining, 0)},
        "repo": str(root), "path_warnings": paths.warnings(root),
        "pending": pending, "items": selected,
        "deferred": [it["id"] for it in ranked[take:]] if not stop else [it["id"] for it in ranked],
        "skipped": skipped,
    }
    if not dry_run:
        if selected:
            led["rounds"].append({
                "round": round_no, "opened_at": now(), "items": [it["id"] for it in selected],
                "findings": {it["id"]: [f["id"] for f in it["findings"]] for it in selected},
                "same_effect": {f["id"]: f["same_effect"] for it in selected for f in it["findings"]
                                if f.get("same_effect")},
            })
        st.save_ledger()
        (out_dir / "queue.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    return result


# --- record --------------------------------------------------------------------------------

def record(out_dir: Path, verdicts: list[dict]) -> dict:
    st = State(out_dir)
    led = st.ledger
    round_of: dict[str, int] = {}
    same_effect: dict[str, list[str]] = {}  # finding -> findings with the same rule and leaf: they take its verdict
    for r in led["rounds"]:
        for i in r["items"]:
            round_of[i] = r["round"]
        for ids in (r.get("findings") or {}).values():
            for i in ids:
                round_of[i] = r["round"]
        for fid, ids in (r.get("same_effect") or {}).items():
            same_effect[fid] = ids
            for i in ids:
                round_of[i] = r["round"]
    known = {finding_id(f) for f in st.map["findings"]} | set(led.get("derived", {}))
    sev_of = {finding_id(f): f["severity"] for f in st.map["findings"]}
    out = {"recorded": [], "downgraded": [], "rejected_input": [], "applied_to_same_effect": []}

    def put(fid: str, entry: dict):
        old = led["verdicts"].get(fid)
        if old is not None:
            entry["attempts"] = old.get("attempts", 1) + 1
        sev = sev_of.get(fid, led.get("derived", {}).get(fid, {}).get("severity"))
        if sev:
            entry["severity"] = sev
        led["verdicts"][fid] = entry

    for v in verdicts:
        fid, verdict = v.get("finding"), v.get("verdict")
        if not fid or verdict not in VERDICTS:
            out["rejected_input"].append({"input": v, "reason": "needs `finding` and a verdict in "
                                                               f"{sorted(VERDICTS)}"})
            continue
        if fid not in known:
            out["rejected_input"].append({"input": v, "reason": f"unknown finding id {fid}"})
            continue
        entry = {**v, "round": round_of.get(fid), "recorded_at": now(),
                 "repro_check": st.repro_status(fid, v.get("repro_file"))}
        if verdict == "confirmed" and entry["repro_check"] != "passed":
            entry["verdict"] = "inconclusive"
            entry["blocked_by"] = "repro-check"
            entry["note"] = (f"investigator said confirmed, but the latest `repro` run of this finding's own test shows "
                             f"{entry['repro_check']}; it is queued once more automatically")
            out["downgraded"].append(fid)
        put(fid, entry)
        out["recorded"].append(fid)
        for other in same_effect.get(fid, []):
            if other in known and other not in {x.get("finding") for x in verdicts}:
                put(other, {**{k: entry[k] for k in ("verdict", "reason", "extreme_case", "smallest_fix",
                                                     "repro_file", "repro_check", "blocked_by") if k in entry},
                            "via": fid, "round": round_of.get(other), "recorded_at": now()})
                out["applied_to_same_effect"].append(other)
    st.save_ledger()
    return out


# --- final report data --------------------------------------------------------------------------

STATUS_ORDER = ["proven", "observed", "unverified", "inconclusive", "not-observed", "rejected"]
SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _status(f: dict, ev: dict | None, v: dict | None) -> str:
    if v:
        if v.get("verdict") == "rejected":
            return "rejected"
        if v.get("verdict") == "confirmed":
            return "proven"
    if ev and ev.get("status") in ("confirmed", "measured"):
        return "observed"
    if ev and ev.get("status") == "not-observed":
        return "not-observed"
    if v and v.get("verdict") == "inconclusive":
        return "inconclusive"
    return "unverified"


def final(out_dir: Path) -> dict:
    st = State(out_dir)
    led = st.ledger
    last_queue = _read(out_dir / "queue.json") or {}
    skipped = {s["id"]: s["reason"] for s in last_queue.get("skipped", [])}
    repro_by = defaultdict(list)
    for t in (st.repro or {}).get("tests", []):
        if t.get("finding"):
            repro_by[t["finding"]].append({k: t[k] for k in ("file", "test", "outcome", "evidence", "time_s")})

    findings = []
    for f in st.map["findings"]:
        fid = finding_id(f)
        ev, v = st.evidence.get(fid), led["verdicts"].get(fid)
        findings.append({**f, "id": fid, "evidence": ev, "verdict": v, "repro": repro_by.get(fid, []),
                         "status": _status(f, ev, v), "not_investigated": skipped.get(fid)})
    for fid, d in led.get("derived", {}).items():
        v = led["verdicts"].get(fid)
        if v:  # derived items only matter once investigated
            findings.append({**d, "evidence": None, "verdict": v, "repro": repro_by.get(fid, []),
                             "status": _status(d, None, v), "not_investigated": None})
    findings.sort(key=lambda x: (STATUS_ORDER.index(x["status"]), SEV_ORDER.get(x["severity"], 9), x["file"], x["line"]))

    counts = defaultdict(int)
    for f in findings:
        counts[f["status"]] += 1
    meta = st.map["meta"]
    return {
        "meta": {
            "tool": "argus/report", "version": 1, "generated_at": now(), "root": meta.get("root"),
            "languages": meta.get("languages", {}), "map_generated_at": meta.get("generated_at"),
            "trace": bool(st.trace), "repro": bool(st.repro),
            "rounds": len(led["rounds"]), "budget": led["budget"],
            "investigated": len(led["verdicts"]),
            "stop": last_queue.get("stop"),
        },
        "summary": {"by_status": dict(counts),
                    "by_severity": dict(defaultdict(int, st.map["stats"].get("by_severity", {})))},
        "findings": findings,
        "effects": st.map.get("effects", {}),
        "projections": (st.trace or {}).get("projections", []),
        "unpredicted_stalls": (st.trace or {}).get("unpredicted_stalls", []),
        "hotspots": st.map.get("hotspots", [])[:15],
    }
