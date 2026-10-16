# Argus vs plain-prompt audit of app/main.py

Run on 2026-10-14. Target: `app/main.py` (101 lines, FastAPI), a copy of which is `audited_main.py`.

## Layout

| Path | What it is |
|---|---|
| `argus/report.html`, `report.md`, `report.json` | Final Argus report: 10 proven, 6 observed, 5 rejected |
| `argus/map.md`, `map.json` | Static map: 21 findings, call graph, I/O boundaries |
| `argus/trace.md`, `trace.json` | Runtime trace of `tests/test_api.py` with 150 ms latency injection |
| `argus/queue.json` | The 6 functions queued for investigation |
| `argus/round-1-verdicts.json`, `verdicts.json` | Investigator verdicts, as recorded in the ledger |
| `argus/repro.md`, `repro.json` | Rerun of every reproduction: 10 passed (effect triggered), 3 failed. Two of the failures (`get_job` blocking and N+1) are real measurements that the effect did not happen. The third, `test_record_request.py`, is an unfilled template (the investigator rejected that finding by reading the code and never completed the test), so its failure is not a measurement |
| `argus/repros/*.py` | The 6 reproduction test files |
| `plain/PLAIN_AUDIT.md` | The plain-prompt agent's report and the exact prompt it got |
| `TOKENS.md` | Token totals per agent and the comparison |

## Notes

- **Argus version:** the installed plugin copy (`~/.claude/plugins/cache/argus/argus/0.2.0`) is stale and still
  says `0.2.0`, the same as the marketplace checkout, so `claude plugin update` reports "already latest". This
  audit ran the marketplace HEAD (`35116a3`) directly. The installed copy was not changed.
- **Investigators:** run as general-purpose agents told to follow the latest `investigator.md`, because the
  installed `argus:investigator` agent definition is the stale one-finding-per-agent version.
- **Re-running the repros:** the tests import `auditor.repro.harness` from the Argus plugin and `app.main` from
  the audited repo, so run them through the CLI rather than plain pytest:
  `python <argus-plugin>/scripts/auditor_cli.py repro <repo-root>`, with the tests under `<repo-root>/.audit/repros/`.
  The `repro.md` here is the output of that rerun.
- **Plain audit:** read-only; nothing in it was executed or verified.
- **Scope:** this folder is separate from the earlier `audit-comparison/` folder, which covers a different app
  (orderdesk).
