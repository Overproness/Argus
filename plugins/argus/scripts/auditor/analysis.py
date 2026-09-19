"""Repo-wide analysis: resolution, boundary classification, effect propagation, findings.

Nothing here is language-specific; languages plug in through `langs.LangSpec`.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath

from . import effects
from .extract import FileExtractor
from .langs import SPECS, spec_for
from .langs.base import DB, FS, NET, LangSpec
from .model import Boundary, Call, Edge, FileCtx, Finding, Function, HookHit
from .scip import Precise

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".audit", "node_modules", "target", "vendor", "dist", "build", "out",
    ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "obj",
    ".gradle", ".idea", ".vscode", "Pods", "DerivedData", ".next", ".nuxt", "coverage",
    "bower_components", "site-packages", ".terraform",
}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testdata", "fixtures", "e2e", "benches"}
TEST_FILE = re.compile(
    r"(^test_.*\.py$|_test\.(py|go|rb|exs?)$|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|kt|cs|swift|scala)$|"
    r"_spec\.rb$|Test\.php$)"
)
GENERATED = re.compile(r"\.(min|bundle|pb|g|generated|designer)\.\w+$|_pb2(_grpc)?\.py$")
MAX_FILE_BYTES = 2_000_000
MAX_NAME_CANDIDATES = 4
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
DOWNGRADE = {"high": "medium", "medium": "low", "low": "low", "info": "info"}
# exact: resolved by scope/path; unique: the only same-named method with a matching arity;
# name: several candidates remain.
CONF_RANK = {"scip": 0, "exact": 0, "unique": 1, "heuristic": 1, "name": 2}
# Short local syscalls block briefly; network, sleeps, processes and waits can block for seconds.
BLOCK_SEVERITY = {FS: "medium"}
FAMILY = {"typescript": "javascript", "cpp": "c", "kotlin": "jvm", "java": "jvm", "scala": "jvm"}
SPEC_BY_NAME = {s.name: s for s in SPECS}
SCOPE_SELF = {"Self", "self", "static", "parent", "this"}


def family(lang: str) -> str:
    return FAMILY.get(lang, lang)


def arity_ok(fn: Function, call: Call) -> bool:
    if call.argc is None or fn.arity is None:
        return True
    lo, hi = fn.arity
    n = call.argc
    # `Type::method(obj, x)` passes self explicitly.
    return lo <= n <= hi or (fn.takes_self and call.kind == "path" and lo <= n - 1 <= hi)


def visible(cand: Function, caller: Function) -> bool:
    if not cand.private or cand.file == caller.file:
        return True
    if cand.lang == "rust":  # private items are visible in their module and its children
        return caller.module[:len(cand.module)] == cand.module
    if cand.lang == "go":  # package = directory
        return PurePosixPath(cand.file).parent == PurePosixPath(caller.file).parent
    return False


def iter_source_files(root: Path, include_tests: bool):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".") and (include_tests or d not in TEST_DIRS)
        )
        for f in sorted(filenames):
            if GENERATED.search(f) or (not include_tests and TEST_FILE.search(f)):
                continue
            p = Path(dirpath) / f
            if spec_for(p) is not None:
                yield p


class RepoMap:
    def __init__(self, root: Path, include_tests: bool = False, precise: Precise | None = None):
        self.root = root.resolve()
        self.include_tests = include_tests
        self.precise = precise
        self.functions: dict[str, Function] = {}
        self.files: dict[str, FileCtx] = {}
        self.edges: list[Edge] = []
        self.boundaries: list[Boundary] = []
        self.findings: list[Finding] = []
        self.hook_hits: list[tuple[str, HookHit]] = []
        self.parse_errors: list[str] = []
        self.skipped_large: list[str] = []
        self.effects: dict = {"entries": [], "deadlines": [], "retries": []}

    # building -------------------------------------------------------------
    def load(self) -> "RepoMap":
        for path in iter_source_files(self.root, self.include_tests):
            if path.stat().st_size > MAX_FILE_BYTES:
                self.skipped_large.append(path.relative_to(self.root).as_posix())
                continue
            try:
                ex = FileExtractor(spec_for(path), path, self.root, self.include_tests).run()
            except Exception as e:  # one odd file must not sink the run
                self.parse_errors.append(f"{path}: {type(e).__name__}: {e}")
                continue
            self.files[ex.fctx.rel] = ex.fctx
            self.hook_hits += ex.hook_hits
            for fn in ex.functions:
                self.functions[fn.id] = fn
        if self.precise is not None:
            self.precise.bind(self.functions)
        self._index()
        self._resolve()
        self._analyze()
        return self

    def spec(self, fn: Function) -> LangSpec:
        return SPEC_BY_NAME[fn.lang]

    @staticmethod
    def family_of(lang: str) -> str:
        return family(lang)

    def _index(self):
        self.free_by_name = defaultdict(list)
        self.methods_by_name = defaultdict(list)
        self.methods_by_type = defaultdict(list)
        for fn in self.functions.values():
            fam = family(fn.lang)
            if fn.container is None:
                self.free_by_name[(fam, fn.name)].append(fn)
            else:
                self.methods_by_name[(fam, fn.name)].append(fn)
                self.methods_by_type[(fam, fn.container, fn.name)].append(fn)

    def _candidates(self, fn: Function, call: Call) -> tuple[list[Function], str]:
        fam = family(fn.lang)
        if self.precise is not None and fn.file in self.precise.files:
            return [self.functions[i] for i in self.precise.targets(fn.file, call.line, call.name)], "scip"

        def fits(fs):
            return [f for f in fs if arity_ok(f, call) and visible(f, fn)]

        if call.kind == "ident":
            if fn.container:
                own = fits(self.methods_by_type.get((fam, fn.container, call.name), []))
                if own:
                    return own, "exact"
            cands = fits(self.free_by_name.get((fam, call.name), []))
            imported = self.files[fn.file].imports.get(call.name, "")
            mod_hint = imported.split(".")[-2] if imported.count(".") >= 1 else None
            pick = ([f for f in cands if f.file == fn.file]
                    or [f for f in cands if mod_hint and f.module and f.module[-1] == mod_hint]
                    or [f for f in cands if f.module == fn.module]
                    or cands)
            if len(pick) > MAX_NAME_CANDIDATES:
                return [], "name"
            return pick, "exact" if len(pick) == 1 else "name"

        if call.kind == "path":
            qual = call.receiver.split(".")[-1]
            if qual in SCOPE_SELF and fn.container:
                qual = fn.container
            typed = fits(self.methods_by_type.get((fam, qual, call.name), []))
            if typed:
                return typed, "exact"
            path_mod = call.path.split(".")[-2] if "." in call.path else None
            cands = fits([f for f in self.free_by_name.get((fam, call.name), [])
                          if f.module and f.module[-1] in (qual, path_mod)])
            if not cands and call.receiver.split(".")[0] in ("crate", "self", "super"):
                cands = fits(self.free_by_name.get((fam, call.name), []))
            if len(cands) > MAX_NAME_CANDIDATES:
                return [], "name"
            return cands, "exact" if len(cands) == 1 else "name"

        if call.self_call and fn.container:
            own = fits(self.methods_by_type.get((fam, fn.container, call.name), []))
            if own:
                return own, "exact"
        if call.name in self.spec(fn).common_methods:
            return [], "name"
        cands = fits(self.methods_by_name.get((fam, call.name), []))
        if len(cands) == 1:
            return cands, "unique"
        return (cands if len(cands) <= MAX_NAME_CANDIDATES else []), "name"

    def _resolve(self):
        self.unresolved: list[tuple[Function, Call]] = []
        for fn in self.functions.values():
            for call in fn.calls:
                cands, conf = self._candidates(fn, call)
                if not cands:
                    self.unresolved.append((fn, call))
                for c in cands:
                    self.edges.append(Edge(fn.id, c.id, call.line, call.awaited, call.context,
                                           call.loop_depth, call.loop_kinds, conf,
                                           call.timeout_s if call.has_timeout else None))
        self.out_edges = defaultdict(list)
        self.in_edges = defaultdict(list)
        for e in self.edges:
            self.out_edges[e.caller].append(e)
            self.in_edges[e.callee].append(e)

    # classification -----------------------------------------------------------
    def _classify(self, fn: Function, call: Call) -> Boundary | None:
        fctx = self.files[fn.file]
        for rule in self.spec(fn).rules:
            if rule.awaited is True and not call.awaited:
                continue
            if (rule.awaited is False or rule.blocking) and call.awaited:
                continue
            if rule.exclude_names is not None and rule.exclude_names.search(call.name):
                continue
            if rule.path is not None:
                if not rule.path.search(call.path):
                    continue
            elif rule.methods is not None or rule.name is not None:
                if rule.methods is not None and (call.kind == "ident" or call.name not in rule.methods):
                    continue
                if rule.name is not None and not rule.name.search(call.name):
                    continue
            else:
                continue
            if rule.receiver is not None and not rule.receiver.search(call.receiver):
                continue
            if rule.text is not None and not rule.text.search(call.snippet):
                continue
            if rule.requires_import is not None and not fctx.imported(rule.requires_import):
                continue
            return Boundary(fn.id, rule.kind, rule.category, rule.blocking, call,
                            rule.confidence(), rule.default_timeout, rule.default_client, rule.client_level)
        return None

    # analysis -------------------------------------------------------------------
    def _analyze(self):
        seen = set()
        for fn, call in self.unresolved:
            b = self._classify(fn, call)
            key = (fn.id, call.stmt_line or call.line, b.kind if b else None)
            if b is None or key in seen:  # one boundary per statement (builder chains)
                continue
            seen.add(key)
            self.boundaries.append(b)
        self.boundaries_by_fn = defaultdict(list)
        for b in self.boundaries:
            self.boundaries_by_fn[b.function].append(b)

        fns = self.functions
        # Sync functions that can block, with the next hop as a witness (propagates to callers).
        blocks: dict[str, tuple] = {}
        for b in self.boundaries:
            if b.blocking and b.call.context == "sync" and b.function not in blocks:
                blocks[b.function] = ("boundary", b)
        # Certain edges first, so witnesses prefer them over name-matched ones.
        for allowed in (("exact", "scip", "unique"), ("exact", "scip", "unique", "name")):
            changed = True
            while changed:
                changed = False
                for e in self.edges:
                    if (e.confidence in allowed and e.context == "sync" and e.caller not in blocks
                            and e.callee in blocks and not fns[e.caller].is_async
                            and not fns[e.callee].is_async):
                        blocks[e.caller] = ("call", e)
                        changed = True
        self.blocks = blocks

        io_reach = {b.function for b in self.boundaries if b.category in (NET, DB)}
        changed = True
        while changed:
            changed = False
            for e in self.edges:
                if e.callee in io_reach and e.caller not in io_reach:
                    io_reach.add(e.caller)
                    changed = True
        self.io_reach = io_reach

        client_timeout_files = defaultdict(list)
        for fctx in self.files.values():
            if fctx.client_timeout:
                client_timeout_files[family(fctx.lang)].append(fctx.rel)

        F = self.findings
        for b in self.boundaries:
            fn, c, spec = fns[b.function], b.call, self.spec(fns[b.function])
            where = f"{'blocking ' if b.blocking else ''}{b.kind} `{c.snippet}`"
            if b.blocking and c.context == "async" and spec.has_async:
                F.append(self._finding(
                    "blocking-in-async", BLOCK_SEVERITY.get(b.category, "high"), b.confidence, fn, c.line,
                    f"Blocking call on an async path ({where}). A slow response stalls {spec.stall_phrase}.",
                    reached_from=self._roots(fn.id),
                ))
            if b.category == NET and not self.bounded(b):
                configured = client_timeout_files.get(family(fn.lang), [])
                if b.default_timeout:
                    sev, note = "low", f"Library default applies: {b.default_timeout}. Check it suits this call."
                elif b.client_level and b.confidence != "exact" and configured:
                    sev = "low"
                    note = (f"No timeout at this call, but client timeouts are configured in "
                            f"{', '.join(configured[:2])}; confirm this call uses such a client.")
                else:
                    sev, note = "medium", "This library has no default timeout, so a hung peer waits forever."
                F.append(self._finding(
                    "io-without-timeout", sev, b.confidence, fn, c.line,
                    f"External call without an explicit timeout ({where}). {note}",
                ))
            if c.loop_depth and b.category in (NET, DB):
                F.append(self._loop_finding(fn, c, f"{b.kind} I/O inside a loop ({where})", b.confidence))

        reported = set()
        for e in sorted(self.edges, key=lambda e: CONF_RANK[e.confidence]):
            caller, callee = fns[e.caller], fns[e.callee]
            if (e.context == "async" and not callee.is_async and e.callee in blocks
                    and self.spec(caller).has_async and (e.caller, e.line) not in reported):
                reported.add((e.caller, e.line))
                names, terminal, confs = self._block_path(e.callee)
                weakest = max([e.confidence, *confs], key=lambda c: CONF_RANK[c])
                sev = BLOCK_SEVERITY.get(terminal.category, "high")
                if weakest == "name":
                    sev = DOWNGRADE[sev]
                F.append(self._finding(
                    "blocking-in-async", sev, weakest, caller, e.line,
                    f"Async code calls sync `{callee.qualname}`, which blocks ({terminal.kind}). A slow "
                    f"response stalls {self.spec(caller).stall_phrase}."
                    + (" Part of the chain is name-matched; confirm it." if weakest == "name" else ""),
                    chain=[caller.qualname, *names],
                    reached_from=self._roots(caller.id),
                ))
            if e.loop_depth and e.callee in io_reach and e.callee != e.caller:
                call = next(c for c in caller.calls if c.line == e.line)
                F.append(self._loop_finding(
                    caller, call, f"call to `{callee.qualname}` inside a loop performs I/O", e.confidence,
                    chain=[caller.qualname, *self._io_chain(e.callee)],
                ))

        for fid, hit in self.hook_hits:
            F.append(self._finding(hit.rule, hit.severity, hit.confidence, fns[fid], hit.line, hit.message))

        for fn in fns.values():
            if fn.max_loop_depth >= 2:
                F.append(self._finding(
                    "nested-loops", "info", "exact", fn, fn.line,
                    f"Loops nested {fn.max_loop_depth} deep: roughly O(n^{fn.max_loop_depth}). "
                    "Candidate for complexity fitting with large inputs (M2).",
                ))

        for scc in self._sccs():
            first = fns[scc[0]]
            F.append(self._finding(
                "recursion", "info", "name", first, first.line,
                "Recursive cycle; check that depth is bounded on extreme inputs.",
                chain=[fns[i].qualname for i in scc],
            ))

        # Waits, deadlines, retries and crashes, propagated across the call graph.
        fx_findings, self.effects = effects.analyze(self)
        F.extend(fx_findings)

        F.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line, f.rule))

    def bounded(self, b: Boundary) -> bool:
        if b.call.has_timeout:
            return True
        return not b.default_client and self.files[self.functions[b.function].file].client_timeout

    def _finding(self, rule, sev, conf, fn: Function, line, msg, chain=None, reached_from=None) -> Finding:
        return Finding(rule, sev, conf, fn.lang, fn.qualname, fn.file, line, msg,
                       chain or [], reached_from or [])

    def _loop_finding(self, fn, call, what, conf, chain=None) -> Finding:
        # A bare infinite loop is usually the service's main tick loop: note it, don't alarm.
        per_item = any(k != "forever" for k in call.loop_kinds)
        return self._finding(
            "io-in-loop", "medium" if per_item else "low", conf, fn, call.line,
            f"{what}. " + ("Cost grows with collection size (N+1 pattern); batch, cache or bound concurrency."
                           if per_item else "Runs once per iteration of a long-running loop."),
            chain=chain,
        )

    def _block_path(self, fid: str) -> tuple[list[str], Boundary, list[str]]:
        """Follow blocking witnesses to the boundary: (names, boundary, edge confidences)."""
        names, confs, seen = [], [], set()
        while fid not in seen:
            seen.add(fid)
            names.append(self.functions[fid].qualname)
            kind, obj = self.blocks[fid]
            if kind == "boundary":
                names.append(f"{obj.kind} @ {self.functions[fid].file}:{obj.call.line}")
                return names, obj, confs
            confs.append(obj.confidence)
            fid = obj.callee
        raise AssertionError("cycle in blocking witnesses")

    def _io_chain(self, fid: str) -> list[str]:
        out, seen = [], set()
        while fid not in seen:
            seen.add(fid)
            out.append(self.functions[fid].qualname)
            b = next((b for b in self.boundaries_by_fn[fid] if b.category in (NET, DB)), None)
            if b is not None:
                out.append(f"{b.kind} @ {self.functions[fid].file}:{b.call.line}")
                break
            nxt = next((e.callee for e in self.out_edges[fid] if e.callee in self.io_reach), None)
            if nxt is None:
                break
            fid = nxt
        return out

    def _roots(self, fid: str, limit: int = 5) -> list[str]:
        """Topmost callers (entry points first) that can reach `fid`."""
        seen, roots, q = {fid}, [], deque([fid])
        while q:
            cur = q.popleft()
            callers = [e.caller for e in self.in_edges[cur]]
            if not callers and cur != fid:
                roots.append(cur)
            for c in callers:
                if c not in seen:
                    seen.add(c)
                    q.append(c)
        roots.sort(key=lambda r: not self.functions[r].is_entry)
        return [self.functions[r].qualname for r in roots[:limit]]

    def _sccs(self) -> list[list[str]]:
        """Iterative Tarjan: recursive cycles in the resolved call graph."""
        index, low, stack, on, out = {}, {}, [], set(), []
        counter = 0
        for v in self.functions:
            if v in index:
                continue
            index[v] = low[v] = counter
            counter += 1
            stack.append(v)
            on.add(v)
            work = [(v, iter(self.out_edges[v]))]
            while work:
                node, it = work[-1]
                advanced = False
                for e in it:
                    w = e.callee
                    if w not in index:
                        index[w] = low[w] = counter
                        counter += 1
                        stack.append(w)
                        on.add(w)
                        work.append((w, iter(self.out_edges[w])))
                        advanced = True
                        break
                    if w in on:
                        low[node] = min(low[node], index[w])
                if advanced:
                    continue
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    if len(comp) > 1 or any(e.callee == node for e in self.out_edges[node]):
                        out.append(sorted(comp))
        return out

    # scoring --------------------------------------------------------------------
    def hotspots(self, limit: int = 25) -> list[dict]:
        sev_w = {"high": 5, "medium": 2, "low": 0.5, "info": 1}
        by_loc = {(fn.file, fn.qualname): fid for fid, fn in self.functions.items()}
        score = defaultdict(float)
        for f in self.findings:
            fid = by_loc.get((f.file, f.function))
            if fid:
                score[fid] += sev_w[f.severity]
        for fid, fn in self.functions.items():
            score[fid] += 2 * max(fn.max_loop_depth - 1, 0)
            score[fid] += 0.5 * min(len(self.in_edges[fid]), 10)
            score[fid] += 1.0 * len(self.boundaries_by_fn[fid])
        ranked = sorted(((s, fid) for fid, s in score.items() if s > 0), reverse=True)[:limit]
        return [{
            "function": self.functions[fid].qualname,
            "lang": self.functions[fid].lang,
            "location": f"{self.functions[fid].file}:{self.functions[fid].line}",
            "score": round(s, 1),
            "loop_nesting": self.functions[fid].max_loop_depth,
            "fan_in": len(self.in_edges[fid]),
            "is_async": self.functions[fid].is_async,
        } for s, fid in ranked]
