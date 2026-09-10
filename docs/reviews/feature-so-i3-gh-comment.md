## Review: increment 3 — **Merge**, with one thing only Trey can check

All 7 reported bugs are real and **fixed here** as `review:` commits, each with its pinning test. **2094 passed, 1 xfailed** on 3.11 and 3.12; lint and types clean. Detail in `docs/reviews/feature-so-i3.md`.

### 🔴 One rate cell, first

All 17 keys follow one ratio family — `output` 5×input, `write_5m` 1.25×, `write_1h` 2× — and `cache_read` 0.1× for **15 of 17**. `claude-fable-5-1` and `claude-mythos-5-1` sit at **0.025×**: a 4× break, one cell, the two priciest models.

Cache-read dominates agent traces — R9's grounding run records **532M** such tokens. Those rows differ by **$133 vs $532** on that session: a $399 swing in the headline figure, every test green either way. **Check this before the legacy rows.** I vouch for internal consistency and provenance; for **no absolute figure**.

### Blockers fixed

- **BUG-1 + BUG-7** — raw exceptions past `main()`: traceback, exit **1** where R39 pins **2**. Fixed at **three** call sites (the report named two) **plus** a floor in `main`. A floor alone is wrong — it turns R39's exit 3 into a 2 — so it is driven directly and asserted not to swallow the clauses above it.
- **BUG-2** — the **third** occurrence of this project's worst class: agent ids and warning details reached `report.json` unredacted in **both** modes. Increment 4 renders these into HTML.
- **BUG-3/4/5/6** — redaction idempotent again; per-detector waste means what R17 says; a FIFO output path refused, not destroyed; stdout one sanitized line.

### Eighth defect, and wave 3

`LEAKING_PATHS` swept a document with `findings: []` — the findings section sat outside the check, leaking all increment. 59 new mutants, 54 killed, **3 real gaps closed** — including `_by_model`'s `sorted()`, invisible because **no fixture resolves >1 model key**. **`W-C11` overturned: not equivalent, now killed.**
