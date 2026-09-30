## ✅ Merge — and tag v1

**6 `review:` commits, each with its pinning test.** Suite **2907 passed, 1 xfailed**; scrubbed/offline **2906 passed, 1 deselected, 1 xfailed**; `ruff`, `ruff format`, `mypy --strict` green — all on **3.11.15 and 3.12.3**, with `anthropic` absent.

### 🔴 Instance twelve

`cli/main.py::build_narrative` could send the narrator **any** `rate_snapshot_version`. Wave-7 `V43` put a literal there; it reached the payload *and the rendered paragraph*, in a document whose header printed the real version — **2875 tests green**.

Two reasons, both the signature defect: AC12's sentinel sweep runs over a payload the *test* builds (the CLI-equality arm compares `.totals` only; this leaf is its sibling), and the vocabulary arm allows any slug anyway. **S38 is the fix.**

### Blockers fixed

- **BUG-14** — `MetricEntry` now refuses a trace-derived *key*. No value-shape check can work: `ghp_`+24 lowercase letters satisfies `AUTHORED_METRIC_VALUE_PATTERN` exactly. R42's property no longer rests on one `if`.
- **BUG-16** — title classified by type, not caller. **Not shipping v1 with occurrence five open.** The proposed `pattern=` wouldn't fix it (`AKIA…` is letters+digits); reclassified instead. **Zero golden bytes moved.**
- **BUG-15** — the visible fallback marker is now reserved. Bug, not note: the class is invisible to a reader, and the fix's failure mode *is* the correct outcome.

### Wave 7 — 45 mutants, 42 killed, 3 survived

Survivors: `V30` (equivalent, evidenced) + **both declared control arms**. Ten real gaps closed: the CLI extraction is outside AC12's reach; the corpus has 1 agent and prices nothing (instance nine, one layer down); the fallback branch was never driven.

Also: **AC14's two hook canaries were missing** — the skip hook fails a run via a *private* pytest attribute. Now canaried.

**C1: discharged**, verified independently. §5 was wrong (BUG-17) — corrected.

Full review: `docs/reviews/feature-so-i5.md`
