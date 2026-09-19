"""Final report: report.json (data), report.md (text) and report.html (a self-contained page).

The HTML follows the claude.ai artifact page contract so it can be published as
is: no <html>/<head>/<body> wrapper, its own <title> and <style>, all data and
code inline, theme-aware tokens. It also opens directly from disk.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

STATUS_TEXT = {
    "proven": "a reproduction triggered the predicted effect, and it still passes",
    "observed": "the runtime trace showed the effect, or measured the number the finding asked for",
    "unverified": "a static lead; nothing has exercised it yet",
    "inconclusive": "an investigation could not settle it",
    "not-observed": "the traced workload ran this code and the effect did not appear",
    "rejected": "an investigator showed the static claim is wrong",
}
STATUS_ORDER = list(STATUS_TEXT)


def to_markdown(d: dict) -> str:
    m, s = d["meta"], d["summary"]
    out = [f"# Audit report: {Path(m.get('root') or '.').name}\n"]
    out.append(f"_Generated {m['generated_at']} · languages: "
               + ", ".join(f"{k} ({v})" for k, v in m.get("languages", {}).items())
               + f" · trace: {'yes' if m['trace'] else 'no'} · reproductions: {'yes' if m['repro'] else 'no'} · "
               f"{m['investigated']} investigated over {m['rounds']} round(s)"
               + (f" · stopped: {m['stop']}" if m.get("stop") else "") + "_\n")
    out.append("| Status | Count | Meaning |")
    out.append("|---|---|---|")
    for st in STATUS_ORDER:
        if s["by_status"].get(st):
            out.append(f"| {st} | {s['by_status'][st]} | {STATUS_TEXT[st]} |")
    out.append("")
    for st in STATUS_ORDER:
        group = [f for f in d["findings"] if f["status"] == st and f["severity"] != "info"]
        if not group:
            continue
        out.append(f"## {st.capitalize()} ({len(group)})\n")
        for f in group:
            out.append(f"- **[{f['severity']}] {f['rule']}**: `{f['function']}` at `{f['file']}:{f['line']}`")
            out.append(f"  {f['message']}")
            if f.get("chain"):
                out.append(f"  - chain: {' → '.join(f['chain'])}")
            ev = f.get("evidence")
            if ev:
                out.append(f"  - runtime: **{ev['status']}**: {ev['detail']}")
            v = f.get("verdict")
            if v:
                line = f"  - verdict: **{v['verdict']}**"
                if v.get("extreme_case"):
                    line += f"; {v['extreme_case']}"
                out.append(line)
                if v.get("smallest_fix"):
                    out.append(f"  - fix: {v['smallest_fix']}")
                if v.get("repro_file"):
                    out.append(f"  - reproduction: `{v['repro_file']}` ({v.get('repro_check', '?')})")
                if v.get("note"):
                    out.append(f"  - note: {v['note']}")
            elif f.get("not_investigated"):
                out.append(f"  - not investigated: {f['not_investigated']}")
        out.append("")
    if d.get("projections"):
        out.append("## Scaling projections\n")
        for p in d["projections"]:
            for r in p["projections"]:
                out.append(f"- `{p['function']}` ~ {p['arg']}^{p['exponent']}: at {p['arg']}={r['n']:g} "
                           f"≈ {r['predicted_s']:g} s ({r['source']})")
        out.append("")
    return "\n".join(out)


def to_html(d: dict) -> str:
    repo = Path(d["meta"].get("root") or "repository").name
    payload = json.dumps({**d, "status_text": STATUS_TEXT, "status_order": STATUS_ORDER},
                         ensure_ascii=False).replace("<", "\\u003c")
    return PAGE.replace("{{TITLE}}", html.escape(f"{repo} audit")).replace("{{DATA}}", payload)


def write(d: dict, out_dir: Path) -> list[Path]:
    paths = [out_dir / "report.json", out_dir / "report.md", out_dir / "report.html"]
    paths[0].write_text(json.dumps(d, indent=2), encoding="utf8")
    paths[1].write_text(to_markdown(d), encoding="utf8")
    paths[2].write_text(to_html(d), encoding="utf8")
    return paths


PAGE = r"""<meta charset="utf-8">
<title>{{TITLE}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&family=Schibsted+Grotesk:wght@600;700&display=swap">
<style>
:root {
  --ground: #f3f5f5; --surface: #ffffff; --sunken: #e9eeee; --ink: #172224; --muted: #56676a;
  --rule: #d5dddd; --accent: #0f6e74; --accent-soft: #d8ecec;
  --high: #b42318; --high-soft: #fbe3e0; --medium: #a8540a; --medium-soft: #fbecd9;
  --low: #3f6479; --low-soft: #e1ebf0; --info: #6b7280; --info-soft: #eceef1;
  --proven: #0b7a47; --observed: #1d5fb4; --unverified: #6b7280; --inconclusive: #a8540a;
  --notobs: #7c8b8e; --rejected: #9aa6a8;
  --display: "Schibsted Grotesk", "Segoe UI", system-ui, sans-serif;
  --body: "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #0e1415; --surface: #151d1f; --sunken: #1b2527; --ink: #e2ebeb; --muted: #95a6a9;
    --rule: #28373a; --accent: #57b8be; --accent-soft: #173537;
    --high: #f0776b; --high-soft: #3a1a17; --medium: #f0a95b; --medium-soft: #3a2812; --low: #8fb4c8;
    --low-soft: #1b2c35; --info: #a0a7b1; --info-soft: #23282e;
    --proven: #4cc38a; --observed: #6fa5ef; --unverified: #a0a7b1; --inconclusive: #f0a95b;
    --notobs: #7f9296; --rejected: #5f6f72;
  }
}
:root[data-theme="dark"] {
  --ground: #0e1415; --surface: #151d1f; --sunken: #1b2527; --ink: #e2ebeb; --muted: #95a6a9;
  --rule: #28373a; --accent: #57b8be; --accent-soft: #173537;
  --high: #f0776b; --high-soft: #3a1a17; --medium: #f0a95b; --medium-soft: #3a2812; --low: #8fb4c8;
  --low-soft: #1b2c35; --info: #a0a7b1; --info-soft: #23282e;
  --proven: #4cc38a; --observed: #6fa5ef; --unverified: #a0a7b1; --inconclusive: #f0a95b;
  --notobs: #7f9296; --rejected: #5f6f72;
}
* { box-sizing: border-box; }
body { background: var(--ground); color: var(--ink); font: 15px/1.55 var(--body); margin: 0; }
.wrap { max-width: 1080px; margin: 0 auto; padding-inline: 20px; padding-block: 36px 64px; display: grid; gap: 40px; }
h1, h2, h3 { font-family: var(--display); line-height: 1.15; text-wrap: balance; margin: 0; }
h1 { font-size: 34px; font-weight: 700; letter-spacing: -0.01em; }
h2 { font-size: 21px; font-weight: 600; }
h3 { font-size: 15px; font-weight: 600; }
p { margin: 0; max-width: 68ch; }
code, .mono { font-family: var(--mono); font-size: 12.5px; }
.eyebrow { font-family: var(--mono); font-size: 11.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
.muted { color: var(--muted); }
header { display: grid; gap: 10px; }
.facts { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--muted); font-size: 13.5px; }
.facts b { color: var(--ink); font-weight: 500; }
section { display: grid; gap: 14px; }
.section-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 16px; }
/* evidence summary */
.ladder { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: var(--sunken); }
.ladder span { display: block; height: 100%; }
.legend { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 20px; }
.legend div { display: grid; grid-template-columns: 12px auto 1fr; gap: 8px; align-items: baseline; font-size: 13.5px; }
.legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.legend b { font-variant-numeric: tabular-nums; font-weight: 600; }
.legend small { color: var(--muted); grid-column: 2 / 4; font-size: 12.5px; line-height: 1.4; }
/* tables */
.scroll { overflow-x: auto; border: 1px solid var(--rule); border-radius: 8px; background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; background: var(--sunken); }
tr:last-child td { border-bottom: 0; }
td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.sub { display: grid; gap: 8px; }
/* pills */
.pill { display: inline-flex; align-items: center; gap: 5px; font: 500 11.5px/1 var(--body); padding: 4px 8px; border-radius: 999px; white-space: nowrap; }
.sev-high { color: var(--high); background: var(--high-soft); }
.sev-medium { color: var(--medium); background: var(--medium-soft); }
.sev-low { color: var(--low); background: var(--low-soft); }
.sev-info { color: var(--info); background: var(--info-soft); }
.st { border: 1px solid currentColor; background: transparent; }
.st-proven { color: var(--proven); } .st-observed { color: var(--observed); } .st-unverified { color: var(--unverified); }
.st-inconclusive { color: var(--inconclusive); } .st-not-observed { color: var(--notobs); } .st-rejected { color: var(--rejected); }
.st-ok { color: var(--proven); } .st-exceeded, .st-cannot-preempt, .st-abandons-thread { color: var(--high); }
/* filters */
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.filters input[type=search] { font: 14px var(--body); color: var(--ink); background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 7px 10px; min-width: 0; flex: 1 1 220px; }
.chip { font: 500 12.5px var(--body); color: var(--muted); background: var(--surface); border: 1px solid var(--rule); border-radius: 999px; padding: 5px 11px; cursor: pointer; }
.chip[aria-pressed="true"] { color: var(--ink); border-color: var(--accent); background: var(--accent-soft); }
.chip b { font-variant-numeric: tabular-nums; font-weight: 600; margin-left: 4px; }
button:focus-visible, input:focus-visible, summary:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
/* findings */
.list { border: 1px solid var(--rule); border-radius: 8px; background: var(--surface); }
.item { border-bottom: 1px solid var(--rule); }
.item:last-child { border-bottom: 0; }
.item > summary { list-style: none; cursor: pointer; display: grid; grid-template-columns: auto auto 1fr auto; gap: 6px 12px; align-items: center; padding: 12px 14px; }
.item > summary::-webkit-details-marker { display: none; }
.item[open] > summary { background: var(--sunken); }
.item .what { min-width: 0; display: grid; gap: 2px; }
.item .what .fn { font-family: var(--mono); font-size: 13px; overflow-wrap: anywhere; }
.item .what .rule { font-size: 12.5px; color: var(--muted); }
.track { display: flex; gap: 3px; }
.track span { width: 18px; height: 6px; border-radius: 2px; background: var(--sunken); border: 1px solid var(--rule); }
.track span.on { background: var(--accent); border-color: var(--accent); }
.track span.no { background: var(--rejected); border-color: var(--rejected); }
.body { padding: 4px 14px 16px; display: grid; gap: 12px; }
.chain { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; font-family: var(--mono); font-size: 12.5px; }
.chain span { background: var(--sunken); border-radius: 4px; padding: 2px 6px; overflow-wrap: anywhere; }
.chain em { color: var(--muted); font-style: normal; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; font-size: 13.5px; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.box { border: 1px solid var(--rule); border-radius: 6px; padding: 10px 12px; display: grid; gap: 6px; }
.empty { padding: 18px 14px; color: var(--muted); }
footer { color: var(--muted); font-size: 12.5px; }
@media (max-width: 560px) {
  .item > summary { grid-template-columns: auto 1fr; }
  .item .what { grid-column: 1 / -1; grid-row: 2; }
  .track { justify-self: end; }
  h1 { font-size: 28px; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>

<main class="wrap">
  <header>
    <div class="eyebrow">Argus audit report</div>
    <h1 id="repo"></h1>
    <div class="facts" id="facts"></div>
  </header>

  <section aria-labelledby="h-evidence">
    <div class="section-head"><h2 id="h-evidence">How far each finding got</h2>
      <span class="muted" id="evidence-note"></span></div>
    <div class="ladder" id="ladder" role="img"></div>
    <div class="legend" id="legend"></div>
  </section>

  <section aria-labelledby="h-effects" id="effects">
    <div class="section-head"><h2 id="h-effects">Effects across the call graph</h2>
      <span class="muted">Static upper bounds: timeouts, library defaults and retries multiplied along each path</span></div>
    <div class="sub" id="fx-entries"></div>
    <div class="sub" id="fx-deadlines"></div>
    <div class="sub" id="fx-retries"></div>
  </section>

  <section aria-labelledby="h-findings">
    <div class="section-head"><h2 id="h-findings">Findings</h2><span class="muted" id="shown"></span></div>
    <div class="filters">
      <input type="search" id="q" placeholder="Filter by function, file or rule" aria-label="Filter findings">
    </div>
    <div class="filters" id="f-status" aria-label="Evidence status"></div>
    <div class="filters" id="f-sev" aria-label="Severity"></div>
    <div class="list" id="list"></div>
  </section>

  <section aria-labelledby="h-proj" id="proj">
    <div class="section-head"><h2 id="h-proj">Scaling projections</h2>
      <span class="muted">A fitted curve extrapolated to larger inputs: an order of magnitude, not a measurement</span></div>
    <div id="proj-body"></div>
  </section>

  <section aria-labelledby="h-stalls" id="stalls">
    <div class="section-head"><h2 id="h-stalls">Stalls the static map did not predict</h2></div>
    <div id="stalls-body"></div>
  </section>

  <section aria-labelledby="h-hot" id="hot">
    <div class="section-head"><h2 id="h-hot">Hotspots</h2>
      <span class="muted">Where findings, loops and fan-in concentrate</span></div>
    <div id="hot-body"></div>
  </section>

  <footer id="foot"></footer>
</main>

<script type="application/json" id="argus-data">{{DATA}}</script>
<script>
(function () {
  const D = JSON.parse(document.getElementById("argus-data").textContent);
  const $ = (id) => document.getElementById(id);
  const el = (tag, props, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
    }
    for (const k of kids) if (k != null) n.append(k);
    return n;
  };
  const COLORS = {proven: "var(--proven)", observed: "var(--observed)", unverified: "var(--unverified)",
    inconclusive: "var(--inconclusive)", "not-observed": "var(--notobs)", rejected: "var(--rejected)"};
  const secs = (s) => s == null ? "unbounded" : s < 1 ? Math.round(s * 1000) + " ms" : s < 120 ? (+s.toPrecision(3)) + " s" : (s / 60).toFixed(1) + " min";
  const m = D.meta, root = (m.root || "repository").split(/[\\/]/).filter(Boolean).pop();

  $("repo").textContent = root;
  const facts = [
    ["Languages", Object.entries(m.languages || {}).map(([k, v]) => k + " " + v).join(", ") || "—"],
    ["Runtime trace", m.trace ? "yes" : "no"],
    ["Reproductions", m.repro ? "yes" : "no"],
    ["Investigated", m.investigated + " in " + m.rounds + " round" + (m.rounds === 1 ? "" : "s")],
    ["Generated", (m.generated_at || "").replace("T", " ").replace("+00:00", " UTC")],
  ];
  if (m.stop) facts.push(["Stopped", m.stop]);
  for (const [k, v] of facts) $("facts").append(el("span", {}, k + ": ", el("b", {text: v})));

  // Evidence summary
  const by = D.summary.by_status, total = Object.values(by).reduce((a, b) => a + b, 0) || 1;
  $("ladder").setAttribute("aria-label", D.status_order.filter((s) => by[s]).map((s) => by[s] + " " + s).join(", "));
  for (const s of D.status_order) {
    if (by[s]) $("ladder").append(el("span", {style: "width:" + (100 * by[s] / total) + "%;background:" + COLORS[s], title: s + ": " + by[s]}));
    $("legend").append(el("div", {}, el("i", {style: "background:" + COLORS[s]}), el("span", {}, s + " ", el("b", {text: by[s] || 0})),
      el("small", {text: D.status_text[s]})));
  }
  $("evidence-note").textContent = total + " finding" + (total === 1 ? "" : "s") + " in total";

  // Effects
  const table = (cols, rows) => {
    const t = el("table", {}, el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", {text: c})))));
    const tb = el("tbody");
    for (const r of rows) tb.append(el("tr", {}, ...r.map((c) => c instanceof Node ? el("td", {}, c) : el("td", {text: c}))));
    t.append(tb);
    return el("div", {class: "scroll"}, t);
  };
  const mono = (t) => el("span", {class: "mono", text: t});
  const pill = (cls, t) => el("span", {class: "pill " + cls, text: t});
  const fx = D.effects || {};
  const shown = [fx.entries, fx.deadlines, fx.retries].some((x) => x && x.length);
  $("effects").hidden = !shown;
  if (fx.entries && fx.entries.length) $("fx-entries").append(el("h3", {text: "Entry points: the longest wait one activation can hit"}),
    table(["Entry", "Worst wait", "How", "Attempts per request"], fx.entries.map((e) => [
      el("span", {}, mono(e.function), el("br"), el("span", {class: "muted mono", text: e.location})),
      el("span", {class: e.max_wait === "unbounded" ? "pill sev-high" : "mono", text: e.max_wait}),
      el("span", {}, e.formula ? e.formula + " via " : "", mono((e.path || []).slice(-3).join(" → "))),
      e.max_attempts_per_request == null ? "∞" : String(e.max_attempts_per_request)])));
  if (fx.deadlines && fx.deadlines.length) $("fx-deadlines").append(el("h3", {text: "Deadlines: an outer timeout around an inner call"}),
    table(["Caller → callee", "Deadline", "Inner worst wait", "Status"], fx.deadlines.map((d) => [
      el("span", {}, mono(d.caller + " → " + d.callee), el("br"), el("span", {class: "muted mono", text: d.location})),
      secs(d.deadline_s), d.inner_wait, pill("st st-" + d.status, d.status)])));
  if (fx.retries && fx.retries.length) $("fx-retries").append(el("h3", {text: "Retry sites"}),
    table(["Function", "Kind", "Attempts", "Backoff"], fx.retries.map((r) => [
      el("span", {}, mono(r.function), el("br"), el("span", {class: "muted mono", text: r.location})), r.kind,
      r.attempts == null ? "∞" : (r.attempts || "bounded by a counter or policy"), r.backoff])));

  // Findings
  const sevs = ["high", "medium", "low", "info"];
  const state = {q: "", status: new Set(D.status_order.filter((s) => s !== "rejected")), sev: new Set(["high", "medium", "low"])};
  const count = (key, val) => D.findings.filter((f) => f[key] === val).length;
  const chips = (host, values, set, key) => {
    for (const v of values) {
      const b = el("button", {class: "chip", type: "button", "aria-pressed": String(set.has(v))}, v, el("b", {text: count(key, v)}));
      b.addEventListener("click", () => { set.has(v) ? set.delete(v) : set.add(v); b.setAttribute("aria-pressed", String(set.has(v))); render(); });
      host.append(b);
    }
  };
  chips($("f-status"), D.status_order, state.status, "status");
  chips($("f-sev"), sevs, state.sev, "severity");
  $("q").addEventListener("input", (e) => { state.q = e.target.value.trim().toLowerCase(); render(); });

  const steps = (f) => {
    const t = el("span", {class: "track", title: "static lead → runtime evidence → reproduction"});
    const ev = f.evidence && f.evidence.status, v = f.verdict && f.verdict.verdict;
    t.append(el("span", {class: "on"}));
    t.append(el("span", {class: ev === "confirmed" || ev === "measured" ? "on" : ev === "not-observed" ? "no" : ""}));
    t.append(el("span", {class: v === "confirmed" && f.status === "proven" ? "on" : v === "rejected" ? "no" : ""}));
    return t;
  };
  const detail = (f) => {
    const b = el("div", {class: "body"});
    b.append(el("p", {text: f.message}));
    if (f.chain && f.chain.length) {
      const c = el("div", {class: "chain"});
      f.chain.forEach((n, i) => { if (i) c.append(el("em", {text: "→"})); c.append(el("span", {text: n})); });
      b.append(c);
    }
    const kv = el("dl", {class: "kv"});
    const add = (k, v) => { if (v) kv.append(el("dt", {text: k}), el("dd", {}, v)); };
    add("Location", mono(f.file + ":" + f.line));
    add("Confidence", f.confidence);
    if (f.reached_from && f.reached_from.length) add("Reached from", mono(f.reached_from.join(", ")));
    if (f.not_investigated && !f.verdict) add("Not investigated", f.not_investigated);
    b.append(kv);
    if (f.evidence) b.append(el("div", {class: "box"}, el("h3", {text: "Runtime evidence: " + f.evidence.status}), el("p", {text: f.evidence.detail})));
    const v = f.verdict;
    if (v) {
      const vb = el("div", {class: "box"}, el("h3", {text: "Investigation: " + v.verdict + (v.round ? " (round " + v.round + ")" : "")}));
      const vk = el("dl", {class: "kv"});
      const vadd = (k, x) => { if (x) vk.append(el("dt", {text: k}), el("dd", {}, x)); };
      const h = v.hypothesis || {};
      vadd("Trigger", h.trigger); vadd("Constraints", h.constraints); vadd("Expected effect", h.expected_effect);
      if (v.evidence && typeof v.evidence === "object") vadd("Measured", mono(Object.entries(v.evidence).map(([k, x]) => k + "=" + JSON.stringify(x)).join("  ")));
      vadd("Extreme case", v.extreme_case); vadd("Smallest fix", v.smallest_fix);
      if (v.repro_file) vadd("Reproduction", el("span", {}, mono(v.repro_file), " (" + (v.repro_check || "?") + ")"));
      vadd("Note", v.note); vadd("Reason", v.reason);
      vb.append(vk);
      b.append(vb);
    }
    return b;
  };
  function render() {
    const list = $("list");
    list.replaceChildren();
    const rows = D.findings.filter((f) => state.status.has(f.status) && state.sev.has(f.severity) &&
      (!state.q || (f.function + " " + f.file + " " + f.rule + " " + f.message).toLowerCase().includes(state.q)));
    $("shown").textContent = rows.length + " shown of " + D.findings.length;
    if (!rows.length) list.append(el("div", {class: "empty", text: "No findings match these filters."}));
    for (const f of rows.slice(0, 400)) {
      const s = el("summary", {},
        pill("sev-" + f.severity, f.severity), pill("st st-" + f.status, f.status),
        el("span", {class: "what"}, el("span", {class: "fn", text: f.function}),
          el("span", {class: "rule"}, f.rule + " · ", el("span", {class: "mono", text: f.file + ":" + f.line}))),
        steps(f));
      const d = el("details", {class: "item"}, s);
      d.addEventListener("toggle", () => { if (d.open && d.children.length === 1) d.append(detail(f)); }, {once: false});
      list.append(d);
    }
    if (rows.length > 400) list.append(el("div", {class: "empty", text: (rows.length - 400) + " more: narrow the filters or read report.json."}));
  }
  render();

  // Projections, stalls, hotspots
  const proj = D.projections || [];
  $("proj").hidden = !proj.length;
  if (proj.length) $("proj-body").append(table(["Function", "Fit", "Observed", "Projected", "Source"], proj.flatMap((p) => p.projections.map((r) => [
    el("span", {}, mono(p.function), el("br"), el("span", {class: "muted mono", text: p.location})),
    p.arg + "^" + p.exponent + " (R²=" + p.r2 + ")", p.arg + "≤" + p.observed_n_max + ": " + secs(p.observed_max_s),
    el("b", {text: p.arg + "=" + r.n + ": " + secs(r.predicted_s)}), r.source]))));
  const stalls = D.unpredicted_stalls || [];
  $("stalls").hidden = !stalls.length;
  if (stalls.length) $("stalls-body").append(table(["Function", "Stalls", "Worst", "Stack"], stalls.map((u) => [
    el("span", {}, mono(u.function), el("br"), el("span", {class: "muted mono", text: u.location})), String(u.stalls), secs(u.worst_s),
    mono(u.stack.join(" → "))])));
  const hot = D.hotspots || [];
  $("hot").hidden = !hot.length;
  if (hot.length) $("hot-body").append(table(["Score", "Function", "Loop nesting", "Fan-in", "Async"], hot.map((h) => [
    String(h.score), el("span", {}, mono(h.function), el("br"), el("span", {class: "muted mono", text: h.location})),
    String(h.loop_nesting), String(h.fan_in), h.is_async ? "yes" : "no"])));

  $("foot").textContent = "Generated by Argus from .audit/map.json" + (m.trace ? ", trace.json" : "") +
    (m.repro ? ", repro.json" : "") + (m.investigated ? " and verdicts.json" : "") +
    ". Static findings are leads; a finding counts once it is observed or reproduced.";
})();
</script>
"""
