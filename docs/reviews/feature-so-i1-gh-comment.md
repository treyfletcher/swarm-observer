**Verdict: approve with the fixes on this branch.** The architecture and R9's collapse are right, and the suite-integrity harness works: I tripped 10 of its guards, all failing loudly. 602 passed, 0 xfailed, 0 skipped; ruff, format and `mypy --strict` clean.

**8 [BLOCKER] · 7 [IMPROVE] · 7 [NIT] · 5 [PRAISE]** — `docs/reviews/feature-so-i1.md`.

### [BLOCKER] — eight ways a hostile trace escaped `load()` as an exception quoting it. All fixed, with pins.

- **BUG-1** reproduces on **7** fields, not 4. **BUG-2/3/4** confirmed and fixed.
- **NEW — two regex engines disagree about `$`.** `safe_agent_id` used `re.match`; Python's `$` matches before a trailing newline, pydantic's doesn't, so `agentId: "a1\n"` passed the mapper and crashed in the model. Agreeing otherwise puts it in an inc-4 attribute.
- **NEW — R9 could silently merge two distinct model calls.** The solo group key was `record.uuid`, unique only *within one file* (R4): two ungrouped calls in two files sharing a uuid became one span, the first's tokens gone. A2 warns of a naive collapse; this is its mirror.
- **NEW** — an over-long basename and a UTC-shift timestamp overflow escaped uncaught.

### [IMPROVE] — found by mutating the product

32 mutations, 7 survived. Worst: flipping R9's collapse condition `and`→`or` left the suite green. Also both byte caps off-by-one, `timestamp_out_of_order`, `dangling_tool_use`.

### Rulings

- **S1** spec defect, not implementation: amend R4's ignored-key list. **S2** bless A-a3. **S6** code right, R6's text incomplete. **S3** encoded, not commented. **S4** half-done; still add `max_json_depth`.
- **S5** pin ratios — and the tests were *not* robust: in CI the measurement path had no caller and asserted a tautology. Fixed via the group-size histogram.
- **Canary ledger** was stricter than R50, locking the tester out — fixed. **Real-transcript test:** nothing outside the repo is read by default, so "always run" is right; the defect was CI/local divergence behind a "pass".

**Comments only:** OBS-1 (an R9/R12 ruling), A-a18, 7 nits.
