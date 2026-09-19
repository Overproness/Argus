"""Join runtime traces to the static map: evidence for each finding, plus what the map missed.

Evidence status per finding:
  confirmed       the trace shows the predicted effect (a stall on the predicted stack, N+1 fan-out)
  not-observed    the code ran, the effect did not appear under this workload (evidence against)
  not-exercised   the function never ran; the workload does not cover it
  not-traced      the run had no function-level view of this language (e.g. only client spans)
  measured        the trace gives a number the finding asked for (complexity, recursion depth)
  not-verifiable  tracing cannot decide this (timeouts, lock semantics); observed timings are attached
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, quantiles

from .fit import best_fit
from .store import CallRec, StallRec, Trace

Key = tuple[str, int]


def _fmt(s: float) -> str:
    return f"{s * 1000:.0f} ms" if s < 1 else f"{s:.2f} s"


class Evidence:
    def __init__(self, map_data: dict, trace: Trace, scale: dict[str, float] | None = None):
        self.map = map_data
        self.trace = trace
        self.scale = scale or {}
        self.threshold = max((float(r.get("stall_threshold", 0.1)) for r in trace.runs), default=0.1)
        self.key_by_qualname: dict[str, Key] = {}
        self.qualname_by_key: dict[Key, str] = {}
        for f in map_data["functions"]:
            file, line, _ = f["id"].rsplit(":", 2)
            key = (file, int(line))
            self.key_by_qualname[f["qualname"]] = key
            self.qualname_by_key[key] = f["qualname"]
        self.calls_by_key = trace.by_key()
        self.by_id: dict[tuple[int, int], CallRec] = {(c.pid, c.id): c for c in trace.calls}
        self.children: dict[tuple[int, int], list[CallRec]] = defaultdict(list)
        for c in trace.calls:
            self.children[(c.pid, c.parent)].append(c)
        self.stalls_by_leaf: dict[Key, list[StallRec]] = defaultdict(list)
        for s in trace.stalls:
            self.stalls_by_leaf[(s.file, s.line)].append(s)
        # External calls (OpenTelemetry client spans), by the call they were made from.
        self.io_by_parent: dict[tuple[int, int], list] = defaultdict(list)
        for r in trace.io:
            self.io_by_parent[(r.pid, r.parent)].append(r)
        # Languages this run saw function by function. Elsewhere a function with no calls was not
        # necessarily idle: the run just had no function-level view of it (spans cover external calls only).
        lang_of = {(f["id"].rsplit(":", 2)[0], int(f["id"].rsplit(":", 2)[1])): f.get("lang")
                   for f in map_data["functions"]}
        self.function_level_langs = {lang_of.get(c.key) for c in trace.calls if c.file} - {None}
        self.function_level_langs |= {lang_of.get(c.key) for c in trace.counts} - {None}
        if any((r.get("source") or "python-tracer") == "python-tracer" for r in trace.runs):
            self.function_level_langs.add("python")
        # Sampling profilers give approximate activations (never used to count calls); coverage gives exact counts.
        self.sampled_pids = {int(r["pid"]) for r in trace.runs if r.get("sampled") == "1" and r.get("pid")}
        # Runs that could not see stalls (a profile without idle samples, evented profiles).
        self.stall_blind_pids = {int(r["pid"]) for r in trace.runs if r.get("stalls") == "0" and r.get("pid")}
        self.counts: dict[Key, int] = defaultdict(int)
        for c in trace.counts:
            self.counts[c.key] += c.count

    def _sampled(self, key: Key) -> bool:
        calls = self.calls_by_key.get(key, [])
        return bool(calls) and all(c.pid in self.sampled_pids for c in calls)

    def _owner_key(self, pid: int, call_id: int) -> Key | None:
        """The repo function a call belongs to: itself if mapped, else its nearest mapped ancestor."""
        cur, seen = self.by_id.get((pid, call_id)), set()
        while cur is not None and (cur.pid, cur.id) not in seen:
            seen.add((cur.pid, cur.id))
            if cur.file:
                return cur.key
            cur = self.by_id.get((cur.pid, cur.parent))
        return None

    def _io_under(self, root: CallRec) -> list:
        """External calls made by this activation, including through unmapped (library) spans below it."""
        out, stack = list(self.io_by_parent.get((root.pid, root.id), [])), [root]
        while stack:
            c = stack.pop()
            for ch in self.children[(c.pid, c.id)]:
                if not ch.file:  # unmapped span: still part of this activation
                    out += self.io_by_parent.get((ch.pid, ch.id), [])
                    stack.append(ch)
        return out

    # --- helpers ------------------------------------------------------------
    def _chain_keys(self, finding: dict) -> list[Key]:
        names = [n for n in finding["chain"] if " @ " not in n] or [finding["function"]]
        return [self.key_by_qualname[n] for n in names if n in self.key_by_qualname]

    def _descendants_named(self, root: CallRec, key: Key) -> int:
        n, stack = 0, list(self.children[(root.pid, root.id)])
        while stack:
            c = stack.pop()
            if c.key == key:
                n += 1
            stack.extend(self.children[(c.pid, c.id)])
        return n

    def _recursion_depth(self, key: Key) -> int:
        best = 0
        for c in self.calls_by_key.get(key, []):
            d, cur = 1, c
            while (p := self.by_id.get((cur.pid, cur.parent))) is not None:
                if p.key == key:
                    d += 1
                cur = p
            best = max(best, d)
        return best

    def stats(self, key: Key) -> dict | None:
        calls = self.calls_by_key.get(key)
        exact = self.counts.get(key)
        if not calls:
            if not exact:
                return None
            # Ran (the coverage count says so) but never long enough to appear in a sample.
            return {"calls": exact, "calls_exact": True, "sampled": True, "total_s": 0.0, "self_s": 0.0,
                    "p50_s": 0.0, "p95_s": 0.0, "max_s": 0.0, "max_self_slice_s": 0.0,
                    "stalls": len(self.stalls_by_leaf.get(key, [])), "is_coro": False}
        durs = sorted(c.dur for c in calls)
        p95 = quantiles(durs, n=20)[-1] if len(durs) >= 2 else durs[-1]
        sampled = self._sampled(key)
        out = {
            "calls": exact if exact else len(calls),
            "total_s": round(sum(durs), 4),
            "self_s": round(sum(c.self_dur for c in calls), 4),
            "p50_s": round(median(durs), 4),
            "p95_s": round(p95, 4),
            "max_s": round(durs[-1], 4),
            "max_self_slice_s": round(max(c.max_self_slice for c in calls), 4),
            "stalls": len(self.stalls_by_leaf.get(key, [])),
            "is_coro": calls[0].is_coro,
        }
        if sampled:
            out["sampled"] = True  # durations are sample-based; `calls` is exact only with coverage
            out["calls_exact"] = bool(exact)
        return out

    # --- per-finding rules --------------------------------------------------
    def for_finding(self, f: dict) -> dict:
        fkey = self.key_by_qualname.get(f["function"])
        st = self.stats(fkey) if fkey else None
        rule = f["rule"]
        if st is None:
            lang = f.get("lang")
            if lang and lang not in self.function_level_langs:
                ext = f"; {len(self.trace.io)} external call(s) were observed (see External calls)" if self.trace.io else ""
                return {"status": "not-traced", "stats": None,
                        "detail": f"this run has no function-level data for {lang}: no spans for the repo's own "
                                  f"functions{ext}. Add spans (method instrumentation, an SDK) or a native tracer"}
            return {"status": "not-exercised", "detail": "never ran under this workload", "stats": None}
        chain = self._chain_keys(f)
        leaf = chain[-1] if chain else fkey

        if rule in ("blocking-in-async", "sync-over-async", "runblocking-in-suspend", "await-in-loop"):
            hits = [s for s in self.stalls_by_leaf.get(leaf, []) if fkey in {(a, b) for a, b, _ in s.stack}]
            if hits:
                worst = max(hits, key=lambda s: s.dur)
                return {"status": "confirmed", "stats": st,
                        "detail": f"{len(hits)} stall(s) on the predicted stack, worst {_fmt(worst.dur)}: "
                                  + " → ".join(q for _, _, q in worst.stack)}
            ran = self.calls_by_key.get(fkey, [])
            if ran and all(c.pid in self.stall_blind_pids for c in ran):
                return {"status": "not-verifiable", "stats": st,
                        "detail": "it ran only in profiles that cannot show stalls (no idle samples to tell a "
                                  "blocked loop from ordinary work); record with idle samples or trace it natively"}
            leaf_st = self.stats(leaf) or st
            return {"status": "not-observed", "stats": st,
                    "detail": f"ran {leaf_st['calls']}×, longest uninterrupted slice "
                              f"{_fmt(leaf_st['max_self_slice_s'])} (< {_fmt(self.threshold)} threshold); "
                              "either the call was fast here or it ran off the loop thread"}

        if rule == "io-in-loop":
            if not chain or leaf == fkey:
                per_io = [self._io_under(c) for c in self.calls_by_key.get(fkey, [])]
                if any(per_io):
                    mx = max(len(x) for x in per_io)
                    targets = sorted({r.target for x in per_io for r in x})
                    status = "confirmed" if mx > 1 else "not-observed"
                    return {"status": status, "stats": st, "io": self._io_stats([r for x in per_io for r in x]),
                            "detail": f"up to {mx} external call(s) per activation "
                                      f"({len(per_io)} activation(s)) to {', '.join(targets[:3])}"
                                      + ("; cost grows with the collection" if mx > 1 else "")}
                return {"status": "not-verifiable", "stats": st,
                        "detail": f"direct library call; this run recorded no client spans for it (use --otlp with "
                                  f"an instrumented client). {st['calls']} activation(s), max {_fmt(st['max_s'])}"}
            leaf_name = self.qualname_by_key.get(leaf, "?")
            if self._sampled(fkey) or fkey not in self.calls_by_key:
                # Sampled activations cannot count calls; exact counts can give the fan-out as a ratio.
                n_f, n_leaf = self.counts.get(fkey), self.counts.get(leaf)
                if not (n_f and n_leaf):
                    return {"status": "not-verifiable", "stats": st,
                            "detail": "sampled profile without call counts; add coverage (trace --node records it)"}
                ratio = n_leaf / n_f
                return {"status": "confirmed" if ratio > 1 else "not-observed", "stats": st,
                        "detail": f"call counts: `{leaf_name}` ran {n_leaf}× while `{f['function']}` ran {n_f}× "
                                  f"({ratio:.1f} per call" + (" if all calls come from here)" if ratio > 1 else ")")
                                  + ("; cost grows with the collection" if ratio > 1 else "")}
            per = [self._descendants_named(c, leaf) for c in self.calls_by_key[fkey]]
            mx = max(per)
            if mx > 1:
                return {"status": "confirmed", "stats": st,
                        "detail": f"`{leaf_name}` ran up to {mx}× per activation of `{f['function']}` "
                                  f"(mean {sum(per) / len(per):.1f}); cost grows with the collection"}
            return {"status": "not-observed", "stats": st,
                    "detail": f"`{leaf_name}` ran at most once per activation ({len(per)} activations)"}

        if rule == "nested-loops":
            if self._sampled(fkey) or fkey not in self.calls_by_key:
                return {"status": "not-verifiable", "stats": st,
                        "detail": f"sampled profile: no input sizes to fit a curve; ran {st['calls']}×, "
                                  f"{_fmt(st['max_s'])} longest sampled stretch"}
            fit = best_fit(self.calls_by_key[fkey])
            if fit:
                return {"status": "measured", "stats": st, "fit": fit.__dict__,
                        "detail": f"{fit.label}: duration ~ {fit.arg}^{fit.exponent} (R²={fit.r2}, "
                                  f"{fit.arg}={fit.n_min:g}…{fit.n_max:g}, {fit.samples} samples)"}
            return {"status": "not-observed", "stats": st,
                    "detail": f"{st['calls']} call(s), max {_fmt(st['max_s'])}; too few distinct input "
                              "sizes (need ≥4 spanning 10×) to fit a curve"}

        if rule == "recursion":
            if self._sampled(fkey) or fkey not in self.calls_by_key:
                return {"status": "not-verifiable", "stats": st,
                        "detail": f"sampled profile: recursion depth is not observable; ran {st['calls']}×"}
            depth = self._recursion_depth(fkey)
            return {"status": "measured", "stats": st, "depth": depth,
                    "detail": f"max observed recursion depth {depth}"}

        hits = [s for s in self.trace.stalls if fkey in {(a, b) for a, b, _ in s.stack}]
        extra = f"; {len(hits)} stall(s) passed through this function" if hits else ""
        io = [r for c in self.calls_by_key.get(fkey, []) for r in self._io_under(c)]
        out = {"status": "not-verifiable", "stats": st,
               "detail": f"tracing cannot decide `{rule}`; ran {st['calls']}×, max {_fmt(st['max_s'])}{extra}"}
        if io:
            s = self._io_stats(io)
            out["io"] = s
            out["detail"] += (f"; observed {s['count']} external call(s), slowest {_fmt(s['max_s'])}"
                              + (f", {s['errors']} failed" if s["errors"] else ""))
        return out

    @staticmethod
    def _io_stats(rows) -> dict:
        durs = sorted(r.dur for r in rows)
        return {"count": len(rows), "errors": sum(r.error for r in rows), "p50_s": round(median(durs), 4),
                "max_s": round(durs[-1], 4), "targets": sorted({r.target for r in rows})[:5]}

    # --- sizes flowing downstream ---------------------------------------------
    def size_links(self) -> dict[tuple[Key, str], set[tuple[Key, str]]]:
        """(callee, arg) -> {(caller, arg)} where the caller passed the same size down in one activation."""
        links: dict[tuple[Key, str], set[tuple[Key, str]]] = defaultdict(set)
        for c in self.trace.calls:
            if not c.shapes:
                continue
            p = self.by_id.get((c.pid, c.parent))
            if p is None or not p.shapes:
                continue
            for a, n in c.shapes.items():
                if n < 2:
                    continue
                for pa, pn in p.shapes.items():
                    if pn == n:
                        links[(c.key, a)].add((p.key, pa))
        return links

    def upstream_max(self, key: Key, arg: str, links, seen=None) -> tuple[float, str]:
        """Largest size of `arg` seen here or at any argument that feeds it from upstream."""
        seen = seen if seen is not None else set()
        if (key, arg) in seen:
            return 0.0, ""
        seen.add((key, arg))
        name = self.qualname_by_key.get(key, f"{key[0]}:{key[1]}")
        best = (max((c.shapes.get(arg, 0.0) for c in self.calls_by_key.get(key, [])), default=0.0), f"{name}({arg})")
        for src_key, src_arg in links.get((key, arg), ()):
            n, where = self.upstream_max(src_key, src_arg, links, seen)
            if n > best[0]:
                best = (n, where)
        return best

    def projections(self) -> list[dict]:
        """Cost of each super-linear function at the largest input that can reach it."""
        links = self.size_links()
        out = []
        for key, calls in self.calls_by_key.items():
            fit = best_fit(calls)
            if not fit or fit.exponent < 1.3 or fit.r2 < 0.8:
                continue
            name = self.qualname_by_key.get(key, calls[0].qualname)
            cands: list[tuple[float, str]] = []
            n_up, where = self.upstream_max(key, fit.arg, links)
            if n_up > fit.n_max:
                cands.append((n_up, f"largest {fit.arg} seen upstream, at {where}"))
            for hint, n in self.scale.items():
                if hint in (fit.arg, name, f"{name}.{fit.arg}", "*"):
                    cands.append((n, f"--assume {hint}={n:g}"))
            if not cands:
                continue
            observed = max(c.dur for c in calls)
            rows = [{"n": n, "source": src, "predicted_s": round(fit.predict(n), 4)} for n, src in cands]
            out.append({
                "function": name, "location": f"{key[0]}:{key[1]}", "arg": fit.arg, "exponent": fit.exponent,
                "r2": fit.r2, "observed_n_max": fit.n_max, "observed_max_s": round(observed, 4),
                "projections": rows,
                "risk": any(r["predicted_s"] >= max(1.0, 10 * observed) for r in rows),
            })
        out.sort(key=lambda p: -max(r["predicted_s"] for r in p["projections"]))
        return out

    # --- report -------------------------------------------------------------
    def build(self) -> dict:
        findings = []
        predicted_leaves: set[Key] = set()
        for f in self.map["findings"]:
            ev = self.for_finding(f)
            if ev["status"] == "confirmed" and f["rule"] != "io-in-loop":
                ch = self._chain_keys(f)
                predicted_leaves.add(ch[-1] if ch else self.key_by_qualname[f["function"]])
            findings.append({**f, "evidence": ev})

        unmapped = []
        for leaf, stalls in self.stalls_by_leaf.items():
            if leaf in predicted_leaves:
                continue
            worst = max(stalls, key=lambda s: s.dur)
            unmapped.append({
                "function": self.qualname_by_key.get(leaf, worst.qualname),
                "location": f"{leaf[0]}:{leaf[1]}" if leaf[0] else "",
                "stalls": len(stalls), "worst_s": round(worst.dur, 4),
                "stack": [q for _, _, q in worst.stack],
            })
        unmapped.sort(key=lambda u: -u["worst_s"])

        fits = []
        for key, calls in self.calls_by_key.items():
            if not key[0]:
                continue
            fit = best_fit(calls)
            if fit and fit.exponent >= 1.3:
                fits.append({"function": self.qualname_by_key.get(key, calls[0].qualname),
                             "location": f"{key[0]}:{key[1]}", **fit.__dict__})
        fits.sort(key=lambda x: -x["exponent"])

        external = defaultdict(list)
        for r in self.trace.io:
            external[(r.system, r.target)].append(r)
        ext_rows = []
        for (system, target), rows in external.items():
            durs = sorted(r.dur for r in rows)
            callers = sorted({self.qualname_by_key.get(k, f"{k[0]}:{k[1]}")
                              for r in rows if (k := self._owner_key(r.pid, r.parent))})
            ext_rows.append({"system": system, "target": target, "count": len(rows),
                             "errors": sum(r.error for r in rows), "p50_s": round(median(durs), 4),
                             "p95_s": round(quantiles(durs, n=20)[-1] if len(durs) >= 2 else durs[-1], 4),
                             "max_s": round(durs[-1], 4), "callers": callers[:5]})
        ext_rows.sort(key=lambda x: -x["max_s"])

        slow = []
        for key, calls in self.calls_by_key.items():
            if not key[0]:
                continue  # spans that map to no repo function
            st = self.stats(key)
            slow.append({"function": self.qualname_by_key.get(key, calls[0].qualname),
                         "location": f"{key[0]}:{key[1]}", **st})
        slow.sort(key=lambda s: -s["self_s"])

        status = defaultdict(int)
        for f in findings:
            status[f["evidence"]["status"]] += 1
        return {
            "meta": {
                "tool": "argus/trace", "version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "stall_threshold_s": self.threshold,
                "runs": [{"pid": r.get("pid"), "lang": r.get("lang"), "argv": json.loads(r.get("argv", "[]")),
                          "python": r.get("python"), "source": r.get("source", "python-tracer"),
                          "service": r.get("service")} for r in self.trace.runs],
                "calls": len(self.trace.calls), "stalls": len(self.trace.stalls), "io": len(self.trace.io),
                "mapped_functions_exercised": sum(1 for k in self.qualname_by_key
                                                  if k in self.calls_by_key or self.counts.get(k)),
                "mapped_functions": len(self.qualname_by_key),
            },
            "summary": dict(status),
            "findings": findings,
            "unpredicted_stalls": unmapped,
            "complexity": fits,
            "projections": self.projections(),
            "external_calls": ext_rows[:50],
            "functions": slow[:50],
        }


def to_markdown(d: dict) -> str:
    m, out = d["meta"], ["# Runtime trace report\n"]
    cmds = "; ".join(" ".join(r["argv"]) for r in m["runs"][:3])
    sources = sorted({r.get("source") or "python-tracer" for r in m["runs"]})
    out.append(f"{len(m['runs'])} run(s) [{', '.join(sources)}] · {m['calls']} calls · {m.get('io', 0)} external "
               f"calls · {m['stalls']} stalls (threshold {_fmt(m['stall_threshold_s'])}) · "
               f"{m['mapped_functions_exercised']}/{m['mapped_functions']} mapped functions exercised\n")
    out.append(f"_Command: `{cmds}`_\n")
    out.append("_Evidence is only as good as the workload: **not-observed** means the effect did not appear "
               "in this run, **not-exercised** means the code never ran._\n")

    out.append("## Findings with evidence\n")
    out.append("Summary: " + ", ".join(f"{v} {k}" for k, v in sorted(d["summary"].items())) + "\n")
    for f in d["findings"]:
        ev = f["evidence"]
        out.append(f"- **[{f['severity']}] {f['rule']}** `{f['function']}` at `{f['file']}:{f['line']}` → "
                   f"**{ev['status']}**: {ev['detail']}")
    out.append("")

    out.append("## Stalls the map did not predict\n")
    if not d["unpredicted_stalls"]:
        out.append("None.\n")
    for u in d["unpredicted_stalls"]:
        where = f" at `{u['location']}`" if u["location"] else ""
        stack = f"; stack {' → '.join(u['stack'])}" if u["stack"] else ""
        out.append(f"- `{u['function']}`{where}: {u['stalls']} stall(s), worst {_fmt(u['worst_s'])}{stack}")
    out.append("")

    out.append("## External calls observed\n")
    if not d.get("external_calls"):
        out.append("None recorded (client spans arrive with `--otlp` from an instrumented program).\n")
    else:
        out.append("| System | Target | Calls | Errors | p50 | p95 | Max | Called from |")
        out.append("|---|---|---|---|---|---|---|---|")
        for x in d["external_calls"]:
            out.append(f"| {x['system']} | `{x['target'][:80]}` | {x['count']} | {x['errors']} | {_fmt(x['p50_s'])} | "
                       f"{_fmt(x['p95_s'])} | {_fmt(x['max_s'])} | {', '.join(f'`{c}`' for c in x['callers'])} |")
        out.append("")

    out.append("## Complexity fits (exponent ≥ 1.3)\n")
    if not d["complexity"]:
        out.append("None fitted (needs ≥4 input sizes spanning 10× and calls over 1 ms).\n")
    else:
        out.append("| Function | Location | Fit | Exponent | R² | n range |")
        out.append("|---|---|---|---|---|---|")
        for c in d["complexity"]:
            out.append(f"| `{c['function']}` | {c['location']} | {c['label']} | {c['exponent']} | {c['r2']} | "
                       f"{c['arg']}={c['n_min']:g}…{c['n_max']:g} |")
        out.append("")

    out.append("## Scaling projections\n")
    if not d.get("projections"):
        out.append("None: no super-linear function has a larger input reaching it (pass `--assume arg=N` "
                   "to project a size you expect in production).\n")
    else:
        out.append("_A fitted curve extrapolated to inputs larger than the run exercised. Treat it as an "
                   "order of magnitude, not a measurement._\n")
        out.append("| Function | Location | Fit | Observed | Projected | Source | Risk |")
        out.append("|---|---|---|---|---|---|---|")
        for p in d["projections"]:
            for r in p["projections"]:
                out.append(f"| `{p['function']}` | {p['location']} | {p['arg']}^{p['exponent']} (R²={p['r2']}) | "
                           f"{p['arg']}≤{p['observed_n_max']:g}: {_fmt(p['observed_max_s'])} | "
                           f"{p['arg']}={r['n']:g}: **{_fmt(r['predicted_s'])}** | {r['source']} | "
                           f"{'yes' if p['risk'] else 'no'} |")
        out.append("")

    out.append("## Slowest functions by self time\n")
    out.append("| Function | Location | Calls | Self | Total | p95 | Max slice | Stalls |")
    out.append("|---|---|---|---|---|---|---|---|")
    for s in d["functions"][:25]:
        out.append(f"| `{s['function']}` | {s['location']} | {s['calls']} | {_fmt(s['self_s'])} | "
                   f"{_fmt(s['total_s'])} | {_fmt(s['p95_s'])} | {_fmt(s['max_self_slice_s'])} | {s['stalls']} |")
    out.append("")
    return "\n".join(out)


def write(map_path: Path, trace: Trace, out_dir: Path, scale: dict[str, float] | None = None) -> tuple[Path, Path]:
    data = Evidence(json.loads(map_path.read_text(encoding="utf8")), trace, scale).build()
    data["meta"]["assume"] = scale or {}
    jp, mp = out_dir / "trace.json", out_dir / "trace.md"
    jp.write_text(json.dumps(data, indent=2), encoding="utf8")
    mp.write_text(to_markdown(data), encoding="utf8")
    return jp, mp
