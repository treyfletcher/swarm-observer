## Review: increment 2 — detectors + fixture corpus

**Approve with the fixes in this branch.** 4 [BLOCKER], 6 [IMPROVE] — fixed here as `review:` commits ; 6 [NIT] as comments. Green on 3.11 **and** 3.12: **1337 passed, 0 xfailed, 0 skipped**, ruff + `mypy --strict` clean. The 3 strict xfails die with their fixes.

**All three reported bugs confirmed and fixed.**
- **BUG-1** (`8202630`) — `merge_runs` used `<=` on half-open ranges, so `[0,10)` and `[10,20)` merged: two warnings of 3 errors became one *critical* claiming `errors: 6, window_spans: 20`. Mirror of inc-1's B8 — that deflated a cost, this inflates severity.
- **BUG-2** (`66e759c`) — 20,000 error spans: **9.2 s → 0.037 s**.
- **BUG-3** (`f539895`) — 10,000 spans: **8.3 s → 0.24 s**. `CoverageIndex` checked against the old union over 200,000 microsecond-precision windows: 0 diffs.

Both perf pins verified to **fail on the pre-fix source** (7.7 s / 18.1 s vs a 2 s cap).

**S13 is a recurrence, so the half that was ours is fixed** (`ef16cf8`): `TRACE_DERIVED_METRIC_KEYS` names the one trace-derived metric, `Finding` now *refuses* trace text under any other key, and the ledger carries `metrics_redaction_dropped` for inc-4.

### ⚠️ The process finding

"46 boundary mutations, 46/46 caught" measures the **mutation set**, not the suite — and the tester's 119 has the same property. My 110 put 40 into `detect/base.py` (**zero** from the coder) and found **9 survivors neither reached**; one changes real findings on 512/16,000 probes.

The tester's 8 equivalents really are equivalent — I confirmed each. But "differentially proved equivalent" is no proof either: my own harness called a *real* defect equivalent, its generator making only whole-millisecond timestamps.

**PM: add R53** — a declared operator set, a **minimum mutation count per module**, the set **checked in** like `collection_floor.json`, CI running it. Plus rulings **S7–S13**. Details in `docs/reviews/feature-so-i2.md`.
