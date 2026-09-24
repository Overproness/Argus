"""Reproduce findings in any language: run programs, observe them from outside, scaffold native probes.

Two modes, both driven from a pytest file in `.audit/repros/` so the runner and
the verdict ledger stay the same for every language:

  black-box  run the real program (CLI, service, test binary) with its dependency
             pointed at a FaultServer, and watch it from outside: did it return
             before the deadline, how long it took, how far apart its heartbeat
             lines were, how often it connected.
  native     a small probe program in the target language, in a side project under
             `.audit/repros/native/<lang>/<name>/` that depends on the repo by path.
             The repo is never modified. The probe calls the code under test and
             prints `@@evidence {json}` lines.

The evidence protocol is one stdout line per fact: `@@evidence {"kind": ..., ...}`.
Any language can print it; `run_target` collects the lines and re-records them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..procs import resolve
from .harness import EVIDENCE_PREFIX, evidence

WIN = sys.platform == "win32"


def repo_root() -> Path:
    """The repo under audit (the repro runner sets ARGUS_REPO and runs from the repo root)."""
    return Path(os.environ.get("ARGUS_REPO") or os.getcwd()).resolve()


# --- running and observing any program ----------------------------------------------------------

def _kill_tree(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    if WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def run_target(cmd: list[str], cwd: str | Path | None = None, env: dict[str, str] | None = None,
               timeout: float = 60.0, heartbeat: str | None = None, label: str | None = None,
               emit: bool = True) -> dict:
    """Run `cmd` and observe it from outside.

    Returns {returned, exit_code, elapsed_s, evidence, heartbeats, max_heartbeat_gap_s, tail}.
    `returned` is False when the deadline passed; the whole process tree is killed then.
    `heartbeat` is a regex for lines the program prints regularly (a tick log): the largest
    gap between them is how long the program was unresponsive.
    With `emit`, the program's own @@evidence lines and a summary are recorded as evidence.
    """
    full_env = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"}
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {"start_new_session": True}
    t0 = time.perf_counter()
    proc = subprocess.Popen(resolve(cmd), cwd=cwd, env=full_env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
    lines: list[tuple[float, str]] = []

    def read():
        for raw in iter(proc.stdout.readline, b""):
            lines.append((time.perf_counter() - t0, raw.decode("utf8", "replace").rstrip("\r\n")))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        rc = proc.wait(timeout=timeout)
        returned = True
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        rc, returned = None, False
    elapsed = time.perf_counter() - t0
    reader.join(5)

    facts = []
    for _, line in lines:
        i = line.find(EVIDENCE_PREFIX)
        if i >= 0:
            try:
                facts.append(json.loads(line[i + len(EVIDENCE_PREFIX):]))
            except json.JSONDecodeError:
                facts.append({"kind": "unparsed", "line": line[:300]})
    beats = [t for t, line in lines if heartbeat and re.search(heartbeat, line)]
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    if heartbeat and beats and not returned:
        gaps.append(elapsed - beats[-1])  # silent until it was killed: that is a gap too
    res = {
        "cmd": " ".join(str(c) for c in cmd)[:300], "returned": returned, "exit_code": rc,
        "elapsed_s": round(elapsed, 4), "evidence": facts, "heartbeats": len(beats),
        "max_heartbeat_gap_s": round(max(gaps), 4) if gaps else None,
        "tail": [line for _, line in lines if EVIDENCE_PREFIX not in line][-30:],
    }
    if emit:
        for f in facts:
            evidence(**f)
        evidence(kind="target", label=label or res["cmd"][:80], returned=returned, exit_code=rc,
                 elapsed_s=res["elapsed_s"], heartbeats=len(beats), max_heartbeat_gap_s=res["max_heartbeat_gap_s"])
    return res


# --- native probes ----------------------------------------------------------------------------------

@dataclass
class Probe:
    lang: str
    dir: Path
    source: Path  # the file the investigator fills in
    build: list[list[str]] = field(default_factory=list)  # run once, outside the timed part
    run: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    verified: bool = False  # this scaffold is exercised by Argus's own tests


def _write_once(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():  # never clobber a probe the investigator already wrote
        path.write_text(text, encoding="utf8")


def _toml_value(text: str, key: str, section: str = "package") -> str | None:
    m = re.search(rf"^\[{re.escape(section)}\][^\[]*?^\s*{key}\s*=\s*\"([^\"]+)\"", text, re.S | re.M)
    return m.group(1) if m else None


def _rust(repo: Path, d: Path, name: str, crate: str | None = None, deps: list[str] = (), **_) -> Probe:
    crate_dir = (repo / crate) if crate else repo
    manifest = (crate_dir / "Cargo.toml").read_text(encoding="utf8")
    pkg = _toml_value(manifest, "name")
    if not pkg:
        raise ValueError(f"{crate_dir / 'Cargo.toml'} has no [package] name; pass crate='path/to/member'")
    ident = pkg.replace("-", "_")
    extra = "\n".join(deps)
    _write_once(d / "Cargo.toml", f"""[package]
name = "argus_probe_{name}"
version = "0.0.0"
edition = "2021"
publish = false

[workspace]  # standalone: never joins the audited repo's workspace

[dependencies]
{pkg} = {{ path = {json.dumps(crate_dir.as_posix())} }}
{extra}
""")
    for lock in (crate_dir / "Cargo.lock", repo / "Cargo.lock"):  # same dependency versions as the repo
        if lock.exists() and not (d / "Cargo.lock").exists():
            shutil.copy(lock, d / "Cargo.lock")
            break
    _write_once(d / "src" / "main.rs", f"""// Argus probe for `{pkg}`: call the code under test, print @@evidence lines.
#![allow(unused_imports)]
use std::time::{{Duration, Instant}};

/// One fact as a JSON object line. Values must already be JSON (numbers, true/false, or quoted strings).
fn evidence(fields: &[(&str, String)]) {{
    let body: Vec<String> = fields.iter().map(|(k, v)| format!("{{:?}}: {{}}", k, v)).collect();
    println!("@@evidence {{{{{{}}}}}}", body.join(", "));
}}

fn main() {{
    let url = std::env::var("ARGUS_FAULT_URL").unwrap_or_default();
    let t0 = Instant::now();
    // Call the function under test here, e.g. `let r = {ident}::client::fetch_price(&url);`
    let _ = &url;
    evidence(&[("kind", "\\"probe\\"".into()), ("elapsed_s", format!("{{:.4}}", t0.elapsed().as_secs_f64()))]);
}}
""")
    target = d / "target"
    exe = target / "debug" / (f"argus_probe_{name}" + (".exe" if WIN else ""))
    return Probe("rust", d, d / "src" / "main.rs",
                 build=[["cargo", "build", "-q", "--manifest-path", str(d / "Cargo.toml")]],
                 run=[str(exe)], env={"CARGO_TARGET_DIR": str(target)}, verified=True)


def _js(repo: Path, d: Path, name: str, module: str = "index.js", ts: bool = False, **_) -> Probe:
    mod_url = (repo / module).resolve().as_uri()
    _write_once(d / "probe.mjs", f"""// Argus probe: call the code under test, print @@evidence lines.
const evidence = (fact) => console.log("@@evidence " + JSON.stringify(fact));
const url = process.env.ARGUS_FAULT_URL;
const mod = await import({json.dumps(mod_url)});

const t0 = performance.now();
// Call the function under test here, e.g. `await mod.fetchPrice(url);`
evidence({{kind: "probe", elapsed_s: (performance.now() - t0) / 1000}});
""")
    # `node --import tsx` needs "tsx" resolvable as an ESM package from the probe's directory, which a
    # global `npm install -g tsx` does not give it. The `tsx` CLI registers the loader itself instead.
    run = ["tsx", str(d / "probe.mjs")] if ts else ["node", str(d / "probe.mjs")]
    return Probe("typescript" if ts else "javascript", d, d / "probe.mjs", run=run, verified=True)


def _java_classpath(repo: Path) -> list[Path]:
    return [p for p in (repo / "target" / "classes", repo / "build" / "classes" / "java" / "main")
           if p.is_dir()]


def _java(repo: Path, d: Path, name: str, sources: str = "src/main/java", classpath: list[str] = (), **_) -> Probe:
    _write_once(d / "Probe.java", """// Argus probe: call the code under test, print @@evidence lines.
public class Probe {
    static void evidence(String json) { System.out.println("@@evidence " + json); }

    public static void main(String[] args) throws Exception {
        String url = System.getenv().getOrDefault("ARGUS_FAULT_URL", "");
        long t0 = System.nanoTime();
        // Call the code under test here, e.g. `demo.Client.fetch(url);`
        evidence("{\\"kind\\": \\"probe\\", \\"elapsed_s\\": " + (System.nanoTime() - t0) / 1e9 + "}");
    }
}
""")
    build: list[list[str]] = []
    cp = [str(p) for p in classpath] or [str(p) for p in _java_classpath(repo)]
    if not cp:  # no build output: compile the sources (works for projects without external jars)
        out = d / "classes"
        srcs = sorted(str(p) for p in (repo / sources).rglob("*.java")) if (repo / sources).is_dir() else []
        if srcs:
            build.append(["javac", "-d", str(out), *srcs])
        cp = [str(out)]
    return Probe("java", d, d / "Probe.java", build=build,
                 run=["java", "-cp", os.pathsep.join(cp), str(d / "Probe.java")], verified=True)


def _cfam(repo: Path, d: Path, name: str, sources: list[str] = (), includes: list[str] = (),
          flags: list[str] = (), cpp: bool = False, **_) -> Probe:
    ext, cc = (".cpp", "g++") if cpp else (".c", "gcc")
    src = d / f"probe{ext}"
    _write_once(src, """/* Argus probe: call the code under test, print @@evidence lines. */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

/* Wall-clock seconds (C11), so waits and hangs count, not only CPU time. */
static double now_s(void) { struct timespec ts; timespec_get(&ts, TIME_UTC); return ts.tv_sec + ts.tv_nsec / 1e9; }

int main(void) {
    const char *url = getenv("ARGUS_FAULT_URL");
    double t0 = now_s();
    (void)url;
    /* Call the code under test here. */
    printf("@@evidence {\\"kind\\": \\"probe\\", \\"elapsed_s\\": %.6f}\\n", now_s() - t0);
    return 0;
}
""")
    exe = d / ("probe" + (".exe" if WIN else ""))
    inc = [f"-I{repo / i}" for i in includes] or [f"-I{repo}"] + ([f"-I{repo / 'include'}"] if (repo / "include").is_dir() else [])
    build = [[cc, "-O1", "-o", str(exe), str(src), *[str(repo / s) for s in sources], *inc, *flags]]
    return Probe("cpp" if cpp else "c", d, src, build=build, run=[str(exe)], verified=True)


def _go(repo: Path, d: Path, name: str, **_) -> Probe:
    gomod = (repo / "go.mod").read_text(encoding="utf8") if (repo / "go.mod").exists() else ""
    mod = re.search(r"^module\s+(\S+)", gomod, re.M)
    if not mod:
        raise ValueError(f"{repo / 'go.mod'} not found or has no module line")
    _write_once(d / "go.mod", f"""module argusprobe

go 1.21

require {mod.group(1)} v0.0.0-00010101000000-000000000000

replace {mod.group(1)} => {repo.as_posix()}
""")
    _write_once(d / "main.go", f"""// Argus probe: call the code under test, print @@evidence lines.
package main

import (
\t"encoding/json"
\t"fmt"
\t"os"
\t"time"
)

func evidence(fact map[string]any) {{
\tb, _ := json.Marshal(fact)
\tfmt.Println("@@evidence " + string(b))
}}

func main() {{
\turl := os.Getenv("ARGUS_FAULT_URL")
\tt0 := time.Now()
\t_ = url
\t// Call the code under test here (import "{mod.group(1)}/...").
\tevidence(map[string]any{{"kind": "probe", "elapsed_s": time.Since(t0).Seconds()}})
}}
""")
    exe = d / ("probe" + (".exe" if WIN else ""))
    return Probe("go", d, d / "main.go", build=[["go", "build", "-mod=mod", "-o", str(exe), "."]], run=[str(exe)],
                verified=True)


SCAFFOLDS = {
    "rust": _rust,
    "javascript": _js,
    "typescript": lambda repo, d, name, **kw: _js(repo, d, name, ts=True, **kw),
    "java": _java,
    "c": _cfam,
    "cpp": lambda repo, d, name, **kw: _cfam(repo, d, name, cpp=True, **kw),
    "go": _go,
}
VERIFIED = {"rust", "javascript", "typescript", "java", "c", "cpp", "go"}  # exercised by Argus's own tests


def scaffold(lang: str, name: str, repo: Path | None = None, out_dir: Path | None = None, **opts) -> Probe:
    """Create (once) a probe side project for `lang` and return how to build and run it.

    Options by language: rust crate=, deps=[...]; javascript/typescript module=; java sources=, classpath=[...];
    c/cpp sources=[...], includes=[...], flags=[...].
    """
    if lang not in SCAFFOLDS:
        raise ValueError(f"no probe scaffold for {lang}; use black-box mode (run_target on the real program)")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("probe name: letters, digits and underscores only")
    repo = (repo or repo_root()).resolve()
    d = (out_dir or repo / ".audit" / "repros" / "native") / lang / name
    d.mkdir(parents=True, exist_ok=True)
    return SCAFFOLDS[lang](repo, d, name, **opts)


def build_probe(probe: Probe, timeout: float = 900) -> None:
    """Compile the probe outside the timed part. Raises with the compiler output on failure."""
    for cmd in probe.build:
        r = subprocess.run(resolve(cmd), cwd=probe.dir, env={**os.environ, **probe.env}, capture_output=True,
                           text=True, encoding="utf8", errors="replace", timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"probe build failed: {' '.join(cmd)}\n{(r.stdout + r.stderr)[-3000:]}")


def run_probe(probe: Probe, env: dict[str, str] | None = None, timeout: float = 30.0, heartbeat: str | None = None,
              label: str | None = None) -> dict:
    return run_target(probe.run, cwd=probe.dir, env={**probe.env, **(env or {})}, timeout=timeout,
                      heartbeat=heartbeat, label=label or f"{probe.lang} probe {probe.dir.name}")
