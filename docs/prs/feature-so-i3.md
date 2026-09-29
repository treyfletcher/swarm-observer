# PR: swarm-observer increment 3 — cost accounting, the JSON report, and `analyze`

Branch: `feature/so-i3` → `main` (stacked on `feature/so-i2`)
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED)
Tasks: T11–T13. Requirements in scope: **R26–R31**, the JSON half of **R36**,
**R38–R40**, acceptance criterion **AC7**. Plus **R33**, which arrived one
increment early for a reason given below.

## Summary

The product produces a number now. Six commits: the rate seam and the curated
snapshot, the waste-attribution plumbing the cost engine needs, the cost engine,
redaction, `report/json_out.py`, and the `analyze`/`detectors` subcommands end
to end.

**No** HTML renderer, **no** SVG timeline, **no** narrator, **no** replay seam.
`--out` and `--explain` are declared on the parser so `--help` shows R38's whole
surface, and both refuse with a usage error naming the increment that builds
them.

`ruff check`, `ruff format --check` and `mypy --strict` are clean; the suite is
**1344 passed, 0 skipped** on CPython 3.11.15 and 3.12.3. All 1337 pre-existing
tests still pass — the extra 7 are the R44 boundary test's own parametrization
over the 7 new modules.

Two increment-1 tests changed, both because the spec caught up with them, and
both explained in the commit that moved them.

### The two things I found by running it rather than by reading it

Neither is a defect in code I wrote; both are places where two pinned
requirements are individually satisfiable and jointly are not. They are the most
useful things in this write-up.

1. **`--no-previews` did not deliver its own guarantee.** A10 blanks R8's three
   preview fields plus the agent strings *at ingest*, and nothing else.
   `Span.model`, `stop_reason`, `tool_name`, `tool_use_id` and
   `SpanError.detail` are trace-derived, uncovered by A10, and every one of them
   is a field R51's hostile corpus loads with payloads. Before I put the guard in
   the renderer, `analyze hostile.jsonl --no-previews` wrote
   `"><img src=x onerror=alert(1)>` four times, as the recorded model id. Fixed
   at the render boundary (A-c6); it costs R30 the recorded model id in the
   unpriced table under that flag, which the PM should settle.

2. **A truncated PEM block survives redaction, and R51 says it must not.** R8
   caps a preview at 240 code points; R33's `private_key` pattern needs
   `-----BEGIN … KEY-----` *and* `-----END … KEY-----`. A private key longer than
   the remaining budget loses its terminator, so the pattern cannot match.
   `report.json` for `hostile.jsonl` today contains
   `-----BEGIN RSA PRIVATE KEY----- MIIBOgIBAAJBAK5f000…`. Both requirements are
   pinned as written; their intersection is a hole. I did not widen R33's table,
   because R33 pins it exactly and R44 forbids a second one. See S14 below.

## Requirements coverage

| Req | Where | Notes |
| --- | --- | --- |
| **R26** | `cost/source.py` — `RateSource`, `SnapshotMeta`, `RateSourceRef`, `RateEntry`, `RateSnapshot`, `RateSnapshotError`, `parse_rate`, `rates_for`, `PRICE_KEYS`, `USAGE_PRICE_KEYS`; `cost/snapshot.py` — `SnapshotRateSource`, `bundled_snapshot`, `parse_snapshot` | Rates are strings on disk and `Decimal` in memory, converted exactly once at load. `parse_rate` **rejects a JSON number by type** rather than coercing it, because a JSON number arrives as a `float` and is wrong before anybody looks at it. The protocol carries one member R26 does not name — `resolve_model_key` — see A-c1. |
| **R27** | `cost/snapshot.py` — `SnapshotRateSource.resolve_model_key`; `cost/source.py` — `RateSnapshot.prefix_keys` | The four rungs in order: exact model key, exact alias, longest prefix key `k` where the recorded id is `k` or starts with `k + "-"`, unresolved. Longest wins, and the `+ "-"` is what keeps `claude-opus-45-preview` from resolving to `claude-opus-4`. The snapshot's shape is enforced by a validator: a dangling alias, an unattributed rate, a rate claimed by two sources and a float rate all fail to load. |
| **R28** | `cost/compute.py` — `price_usage`, `USAGE_PRICE_KEYS` | The five terms, divided by `Decimal(1_000_000)`. Nothing is subtracted (`input_tokens` as recorded already excludes cached tokens) and nothing is added (thinking tokens are already inside `output_tokens`). The 5m/1h split and the "no breakdown ⇒ all at 5m" rule are the mapper's and were already correct in increment 1. |
| **R29** | `cost/compute.py` — `_EXACT`, `_ROUNDING`, `quantize_cost`, `quantize_display`, `format_usd`, `format_display_usd`, `sum_usd`, `CostError` | Every arithmetic step runs in a `decimal.Context` that **traps `Inexact`**. The one rounding the spec allows happens in `quantize_cost`, in its own context. Totals are sums of already-quantized span costs, so rows add up to totals exactly. `format_display_usd` is R29's 2-decimal grand total, defined here so there is one money-rounding rule in the package rather than one per renderer. |
| **R30** | `cost/compute.py` — `classify_span`, `missing_price_keys`, `UNPRICED_REASONS`, `UnpricedSpan` | The four reasons **in R30's priority order**, one `return` per rung. `rate_key_missing` reads only the *non-zero* usage components, because almost every model call has zero 1-hour cache creation. `UnpricedSpan` also carries `missing_price_keys` (enumerated slugs) so the fourth reason says which key. |
| **R31** | `cost/compute.py` — `compute_costs`, `_by_agent`, `_by_model`, `_by_detector`, `_waste_by_finding`, `CostReport` | Four groupings: whole trace, per agent by `agent_index`, per resolved `model_key` by key, per detector by slug. An agent that made no model call still gets a row. Verified over the whole corpus that each grouping's rows sum to its total exactly. |
| **R33** | `report/redact.py` — `redact`, `REDACTION_PATTERNS`, `REDACTION_LABELS`, `marker` | Out of increment-3 scope on paper; required by R30, which says the unpriced table's recorded model is "redacted and escaped like any trace string". See A-c5. The table is verbatim from R33, in R33's order. |
| **R36** (JSON half) | `report/json_out.py` — `render_json`, `report_document`, and the per-object document functions | `sort_keys`, `ensure_ascii`, `indent=2`, trailing newline, every `Decimal` as its 6-decimal string. Sections: `meta` (provenance, counts, options, notes), `agents`, `spans`, `findings`, `cost`, `warnings`. |
| **R38** | `cli/main.py` — `build_parser`, `analyze`, `detectors_document`, `expand_inputs`, `check_output_path`, `build_limits`, `build_config` | Three subcommands. Directory expansion is non-recursive and **sorted by basename**. `--detector` repeatable, unknown slug is exit 3. `--no-previews` plumbed to both the adapter and the renderer. `--out`/`--explain` declared and refused. |
| **R39** | `cli/main.py` — `EXIT_*`, `UsageError`, `_Parser.error`, `exit_code_for`, `main` | 0/1/2/3 as pinned. argparse's own usage exit is 2, which collides with R11's fail-closed code, so the parser raises instead of exiting. `TraceError`, `RateSnapshotError` and `CostError` all render one sanitized line and exit 2. |
| **R40** | `cli/main.py` — `summary_line`; `report/json_out.py` — `severity_counts` | One line: `wrote <paths>; findings: critical=N warning=N info=N`. Paths as given, never resolved — a resolved path carries the working directory and the username into stdout, which R47 forbids. |
| **AC7** | verified by hand, output below | |
| R17/R29 plumbing | `detect/base.py` — `WasteAttributor`; `detect/registry.py` — `DetectorRun`, `run_detectors_with_waste`; `repeated_tool_call`, `agent_loop`, `retry_storm` — `scan_with_waste` | See A-c3. `run_detectors` is now `run_detectors_with_waste(...).findings`, so there is still exactly one path through the detectors. |

## Rate table — **pending Trey's rate review**

The spec (A4, T11) puts an owner sanity-check of the curated rates at this
increment's checkpoint. Trey was not available to gate on, so the rates are
curated, attributed and shipped, and the review is outstanding. **Nothing here
was invented**: every rate is a transcription of a published figure, every model
key names the source it came from, and `RateSnapshot`'s validator refuses to
load a snapshot in which any model key is unattributed — so a rate cannot be
added later without provenance.

Snapshot version `2026-09-10`, `snapshot_date` `2026-09-10`, currency USD, all
figures **USD per 1,000,000 tokens**.

Source **`anthropic_pricing_current`** — Anthropic API pricing, current models
table, <https://platform.claude.com/docs/en/about-claude/pricing>, read
2026-09-10:

| model_key | input | output | cache_read | cache_write_5m | cache_write_1h |
| --- | ---: | ---: | ---: | ---: | ---: |
| `claude-fable-5-1` | 10 | 50 | 0.25 | 12.50 | 20 |
| `claude-mythos-5-1` | 10 | 50 | 0.25 | 12.50 | 20 |
| `claude-fable-5` | 10 | 50 | 1 | 12.50 | 20 |
| `claude-mythos-5` | 10 | 50 | 1 | 12.50 | 20 |
| `claude-opus-5` | 5 | 25 | 0.50 | 6.25 | 10 |
| `claude-opus-4-8` | 5 | 25 | 0.50 | 6.25 | 10 |
| `claude-opus-4-7` | 5 | 25 | 0.50 | 6.25 | 10 |
| `claude-opus-4-6` | 5 | 25 | 0.50 | 6.25 | 10 |
| `claude-opus-4-5` | 5 | 25 | 0.50 | 6.25 | 10 |
| `claude-sonnet-5` | 2 | 10 | 0.20 | 2.50 | 4 |
| `claude-sonnet-4-6` | 3 | 15 | 0.30 | 3.75 | 6 |
| `claude-sonnet-4-5` | 3 | 15 | 0.30 | 3.75 | 6 |
| `claude-haiku-4-5` | 1 | 5 | 0.10 | 1.25 | 2 |

Source **`anthropic_pricing_legacy`** — the legacy/retired models section of the
same page, read 2026-09-10:

| model_key | input | output | cache_read | cache_write_5m | cache_write_1h |
| --- | ---: | ---: | ---: | ---: | ---: |
| `claude-opus-4-1` | 15 | 75 | 1.50 | 18.75 | 30 |
| `claude-opus-4` | 15 | 75 | 1.50 | 18.75 | 30 |
| `claude-sonnet-4` | 3 | 15 | 0.30 | 3.75 | 6 |
| `claude-3-5-haiku` | 0.80 | 4 | 0.08 | 1 | 1.60 |

**What Trey should check, in the order I would check it:**

1. The four **legacy** rows. Retired models' prices are the ones most likely to
   have been restated, and a historical transcript is exactly where they get
   used.
2. `claude-fable-5-1` and `claude-mythos-5-1` at `cache_read` **0.25** against
   `claude-fable-5`/`claude-mythos-5` at **1**. That is a 4× difference between
   two point releases and the only place the published cache-read multiplier is
   not 0.1× base input. If it is a typo of mine, it under-reports cache-heavy
   Fable/Mythos traces by 75%.
3. **What is deliberately absent.** Claude Sonnet 3.7, Sonnet 3.5, Haiku 3,
   Opus 3, Claude 2.x and Instant are all retired and their prices are no longer
   published anywhere I can attribute. They are **not** in the snapshot, so a
   trace on one of them reports `model_not_in_snapshot` rather than a guess. If
   Trey wants them priced, he has to supply the figures and a source.
4. **`aliases` is empty**, and this is the one place a reviewer should push
   back. Every model id this snapshot can price is reachable by rung 1 (exact
   key) or rung 3 (longest prefix), so R27's rung 2 has no shipped data behind
   it. I would rather say that than invent an alias so a rung looks exercised —
   that is precisely a check that cannot fail. Bedrock (`anthropic.claude-…`)
   and Vertex (`claude-…@date`) ids are the real use for it; I could not
   attribute those id strings from the published docs, so I did not write them
   down. The mechanism itself is implemented and validated (a dangling alias
   fails to load), and is exercisable via `SnapshotRateSource(snapshot=…)`.
5. **Tiers the snapshot does not model at all**, stated in `RATE_CAVEAT` and in
   every report: batch, priority, long-context (>200K) and negotiated rates. A
   long-context Sonnet call is priced at the standard tier and will be low.

## Assumptions and interpretation calls (A-c series)

- **A-c1 (`RateSource` carries a third member).** R26 names `get_rate` and
  `meta`. R27's ladder reads the source's own `models` and `aliases` tables, so
  putting it anywhere else would make `cost/compute.py` import
  `cost/snapshot.py` concretely and close the seam R26 exists to open.
  `resolve_model_key` is therefore on the protocol. **PM**: one clause in R26.

- **A-c2 (`meta.sources` entries carry the per-model provenance).** The task
  asked for a source and an as-of date *per model*; R27 pins `meta`'s key set as
  `{version, snapshot_date, currency, sources}` and says nothing about a
  `sources` entry's shape. So each entry is
  `{id, label, url, as_of, models: [...]}` and provenance is the model key's
  membership in exactly one entry's `models` list. R27's key set is untouched,
  and completeness is a load-time validation rather than a convention.

- **A-c3 (R29's per-finding `wasted_cost_usd` is not computable as R14 stands)
  — FLAGGED.** R17 defines `Finding.wasted` over "the distinct `model_call`
  spans named by that detector as redundant" and R29 defines `wasted_cost_usd`
  as "the sum of already-quantized span costs". Both need the span *list*. R14's
  `Finding` has no field for it: `span_seqs` is the evidence list, which for R18
  is the *tool* calls and for R20 is the error spans, not the attributed model
  calls. And a summed `TokenUsage` cannot be priced — it has lost which model
  produced each token, so a Haiku subagent under a Sonnet orchestrator has no
  single rate. R44 also forbids `cost/` importing `detect/`.
  Resolved by plumbing rather than by a model change: a detector with a
  redundancy notion implements `WasteAttributor.scan_with_waste`, which yields
  findings and attributions from **one** scan so they cannot drift, and the
  pipeline hands `{finding_id: seqs}` to the cost engine. `Finding`'s field set
  is unchanged and no expectation file moved. **PM**: either R14 gains a field
  (a `TRACE_SCHEMA_VERSION`-adjacent change) or R17/R29 gain a sentence saying
  the attribution travels beside the finding. The second is cheaper and is what
  is implemented.

- **A-c4 (a finding with an unpriceable attributed call gets `None`, not a
  partial sum).** R14 types `wasted_cost_usd` as `Decimal | None`. When any
  attributed model call could not be priced, the finding's waste cost is
  *unknown*, and quietly reporting the priced subset would understate the
  product's headline number without saying so. `DetectorWaste` carries
  `findings_unpriced` so a per-detector zero is legible rather than a lie.
  Relevance uses R17's own filter — a `model_call` with a recorded `usage` —
  so `wasted` and `wasted_cost_usd` always describe the same set of spans.

- **A-c5 (`report/redact.py` lands in increment 3).** It is T14's, in increment
  4. R30 — mine — says an unpriced span appears with "its recorded `model`
  (redacted and escaped like any trace string)", and `report/json_out.py` writes
  trace free text to a file. Shipping the JSON report without a redactor means
  increment 3's deliverable writes credential-shaped substrings verbatim and
  increment 4 repairs it. `escape_html` is **not** here and R44's
  one-definition rule for it is untouched.

- **A-c6 (`--no-previews` is enforced at the render boundary too) — FLAGGED.**
  A10 blanks R8's three previews plus the agent strings at ingest. R38 says the
  mode omits trace free text *entirely*, and R51's `--no-previews` arm says no
  payload appears anywhere. Those are false for `Span.model`, `stop_reason`,
  `tool_name`, `tool_use_id` and `SpanError.detail` — demonstrated above.
  `report/json_out.free_text` now blanks every trace-derived string under the
  flag. Blanking at *ingest* instead was rejected outright: without `Span.model`
  the cost engine cannot resolve a rate, and `--no-previews` would zero the
  entire cost report. **PM**: this costs R30 the recorded model id in the
  unpriced table under `--no-previews` (the `reason` and `seq` remain). Either
  R30 gains "except under `--no-previews`", or the mode gains a
  `model_digest` — the latter is what A10's "only digests, counts and
  enumerated slugs" implies, but it is a new field and I did not add one.

- **A-c7 (`metrics` is redacted but never blanked).** The one exception to
  A-c6. R15 hashes `metrics` into the `finding_id` the same document prints, so
  a masked metric makes the printed id unverifiable from the printed evidence.
  It is safe because R16 constrains the one trace-derived key to
  `^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$` — no `<`, `>`, `"`, `/`, space or control
  character, so no markup payload can be a legal tool name — and redaction
  covers the credential shapes that *are* legal (increment-2 review, S13).

- **A-c8 (findings are emitted in R13 order, not R36's grouping).** R36's
  "grouped by severity descending, then detector slug, then finding_id" is a
  section-layout rule for the HTML document. Duplicating it in `json_out.py`
  would put a second ordering rule for the same objects in the package, and
  `detect/base.sort_findings` is the single definition. The JSON emits the
  canonical R13 order `(severity_rank, slug, finding_id)`; increment 4's
  renderer applies R36's grouping.

- **A-c9 (the JSON spans array is not capped).** R36 caps the HTML spans
  *table* at 5,000 rows with an "…and N more" line. That is a rendering
  concession to a browser; truncating a machine-readable report would make it
  unusable for the thing it exists for. All spans are emitted.

- **A-c10 (`--out` and `--explain` are declared and refused).** R38 pins
  `--out` as required. Requiring it now would make every documented invocation
  fail, and accepting it silently would mean a run that reports success without
  writing the file it named. Both are usage errors (exit 3) naming their
  increment; `--json` is required until increment 4. Increment 4 flips this.

- **A-c11 (stdout names the output paths as given).** R40 says "naming the
  written paths"; R47 forbids an absolute path in output. Resolving would import
  the working directory and the username into stdout. As-given is both what the
  user asked for and a function of the flags alone.

- **A-c12 (an argparse usage error is exit 3, changing an increment-1 test).**
  R39 gives usage mistakes 3 and reserves 2 for the fail-closed path. Increment
  1 pinned argparse's default of 2 because nothing yet implemented R39.
  `test_r38_an_unknown_subcommand_is_a_usage_error` now asserts 3. This is the
  only behavioural change to an existing surface in this branch.

- **A-c13 (`R39` left `traceability_pending.txt`).** That test now genuinely
  asserts an R39 clause, so R52's check would have failed with R39 still
  ledgered. It is a **citation of one clause, not coverage of the requirement**,
  and the ledger file says so in a comment. R39's other four clauses are the
  tester's.

- **A-c14 (`CostError` on inexact arithmetic).** Trapping `decimal.Inexact`
  turns "nothing rounds except where the spec says" from a claim into a check.
  It fires only on a token count with more than 60 significant digits — a
  corrupt trace — and then exits 2 naming the span rather than pricing it
  approximately.

- **A-c15 (`by_detector` covers detectors that produced findings).** R31 says
  "per detector for `wasted_cost_usd`". `cost/` cannot import the registry
  (R44), so it reports the detectors present in the waste mapping, which the CLI
  populates with every produced finding. A detector that fired nothing has no
  row. Increment 4's renderer, which *may* import `detect`, can add zero rows if
  the HTML wants a complete table.

## Spec flags

Continuing from the increment-2 review's S13.

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S14** | R8 / R33 / R51 | A PEM private key longer than R8's 240-code-point preview budget loses its `-----END … KEY-----` terminator, so R33's `private_key` pattern — which requires BEGIN and END — cannot match it. R51 promises the PEM block "appears nowhere". Reproduced today: `report.json` for `hostile.jsonl` contains `-----BEGIN RSA PRIVATE KEY----- MIIBOgIBAAJBAK5f000…`. | Add a second `private_key` alternative to R33's table matching a `-----BEGIN[ A-Z]*PRIVATE KEY-----` header with **no** terminator (to end of string), ordered after the paired form so a complete block still redacts as one unit. Do not "fix" it by lengthening previews: the same hole reopens at the next cap. |
| **S15** | R14 / R17 / R29 | R29 requires a finding's `wasted_cost_usd` to be "the sum of already-quantized span costs", but R14's `Finding` has no field naming the attributed model calls and R44 forbids `cost/` importing `detect/`. As written the number is not computable. (A-c3.) | Amend R17 (or R29) to say the attribution travels beside the finding, as `{finding_id: seqs}`, and that a detector with a redundancy notion exposes it. Do **not** add a field to R14: it moves a pinned model shape and every expectation file that reads it, for data no reader of a report wants. |
| **S16** | R30 / R38 / R51 / A10 | `--no-previews` is described as omitting trace free text entirely (R38) and R51's arm asserts no payload appears anywhere, but A10's ingest-time blanking covers only R8's three previews and the agent strings. `Span.model`, `stop_reason`, `tool_name`, `tool_use_id` and `SpanError.detail` are trace-derived and were reaching the report. R30 separately *requires* the recorded model in the unpriced table. (A-c6.) | State that `--no-previews` is enforced at the render boundary over **every** trace-derived string, name the fields, and say what R30's unpriced row shows instead — I recommend a 16-hex digest of the recorded model, which keeps "two spans had the same unknown model" legible and matches A10's "only digests, counts and enumerated slugs". |
| **S17** | R26 | R26 names two protocol members; R27's resolution ladder needs a third, because it reads tables only the source has. (A-c1.) | Add `resolve_model_key(recorded: str \| None) -> str \| None` to R26's `RateSource`. |
| **S18** | R38 / R39 | R38 makes `--out` required, which cannot be satisfied until increment 4's renderer exists, and R39 does not say what an accepted-but-unimplemented flag does. (A-c10.) | No change needed to the shipped v1 text; worth one sentence in R38 saying `--json` alone is a valid invocation, since a machine-readable-only run is a legitimate use (CI gating on `--fail-on` without producing an artefact a human opens). |
| **S19** | R36 | R36 pins the HTML spans table at 5,000 rows and says the JSON emits "the same data". A capped machine-readable report is not machine-readable. (A-c9.) | One clause: the 5,000-row cap is a rendering rule for the HTML section only. |

## Tester surface

### What to drive

```python
# the rate seam and the snapshot
from swarm_observer.cost.source import (
    PRICE_KEYS, USAGE_PRICE_KEYS, MODEL_KEY_PATTERN, DATE_PATTERN,
    RateEntry, RateSnapshot, RateSnapshotError, RateSource, RateSourceRef,
    SnapshotMeta, parse_rate, rates_for,
)
from swarm_observer.cost.snapshot import (
    SNAPSHOT_FILENAME, SNAPSHOT_PACKAGE,
    SnapshotRateSource, bundled_snapshot, parse_snapshot,
)
# the cost engine
from swarm_observer.cost.compute import (
    COST_DECIMAL_PLACES, COST_PRECISION, COST_QUANTUM, DISPLAY_QUANTUM, ZERO_USD,
    RATE_CAVEAT, WASTE_CAVEAT, SYNTHETIC_MODEL, UNPRICED_REASONS,
    AgentCost, CostError, CostReport, DetectorWaste, ModelCost, SpanCost,
    UnpricedReason, UnpricedSpan,
    classify_span, compute_costs, format_display_usd, format_usd,
    missing_price_keys, price_usage, quantize_cost, quantize_display,
    sum_usage, sum_usd,
)
# redaction
from swarm_observer.report.redact import (
    NAME_PRESERVING_LABEL, REDACTION_LABELS, REDACTION_PATTERNS, marker, redact,
)
# the JSON report
from swarm_observer.report.json_out import (
    REDACTION_CAVEAT, REPORT_FORMAT_VERSION, RenderOptions,
    agent_document, cost_document, finding_document, format_timestamp,
    free_text, last_timestamp, metrics_document, optional_timestamp,
    render_json, report_document, severity_counts, span_document, usage_document,
)
# the waste plumbing
from swarm_observer.detect.base import WasteAttributor
from swarm_observer.detect.registry import DetectorRun, run_detectors_with_waste
# the CLI
from swarm_observer.cli.main import (
    EXIT_OK, EXIT_FINDINGS, EXIT_FAIL_CLOSED, EXIT_USAGE, FAIL_ON_CHOICES,
    UsageError, analyze, build_config, build_limits, build_parser,
    check_output_path, detectors_document, exit_code_for, expand_inputs,
    main, run, selected_slugs, summary_line,
)
```

### Where the seams are

- **`SnapshotRateSource(snapshot=…)`** takes a `RateSnapshot` you build. This is
  the door to everything the shipped file cannot reach: R27's **alias rung**
  (empty in the shipped snapshot — see the rate-review note), R30's
  **`rate_key_missing`** (needs a `RateEntry` with a price key absent), a
  one-model snapshot for arithmetic, and a snapshot whose rates make a
  half-cent boundary land on 6 decimal places.
- **`parse_snapshot(text)`** is the load-time validator. Every rejection is a
  `RateSnapshotError` with an enumerated code. Worth driving: a float rate, a
  negative rate, `"1E3"`, `NaN`, a dangling alias, a model claimed by two
  sources, an unattributed model, a model claimed by a source but absent from
  `models`, a bad date, a non-object, invalid JSON.
- **`compute_costs(trace, source, waste_seqs=…)`** takes any `RateSource`
  (write a fake — it is three members) and any `{finding_id: seqs}` mapping.
  Note it *validates* the mapping's keys against R15's id shape and raises
  `ValueError` on a key that is not one.
- **`price_usage(usage, rates)`** takes a plain `Mapping[str, Decimal]`, so R28's
  formula is testable one term at a time without a snapshot at all.
- **`tests/synthetic_traces.TraceBuilder`** (the increment-2 tester's) is still
  the cheapest way to put both sides of a boundary one integer apart.
- **`main(argv, stdout=…, stderr=…)`** takes both streams, so every exit-code and
  stream-discipline case runs in process. `run()` raises the typed error instead,
  which is the seam for asserting *which* error a condition produces.

### What I think is risky

Ranked, most to least.

1. **The R27 ladder's rung 3 is one string comparison from mispricing
   everything, and it fails silently.** `claude-opus-4-1-20250805` matches both
   `claude-opus-4` and `claude-opus-4-1`, three times apart in price. Hit
   longest-vs-first, the `+ "-"` separator (`claude-opus-45-preview` must **not**
   resolve to `claude-opus-4`), a recorded id equal to a key, a recorded id that
   is a *prefix* of a key (`claude-opus` → nothing), an empty string, and the
   rung order itself — an alias that shadows a longer prefix must win.
2. **R30's priority order.** Every rung is individually easy and the *order* is
   the requirement. Build a span that satisfies two rungs at once — an api-error
   span with `usage=None` (1 beats 2); a `<synthetic>` model that is also absent
   from the snapshot (1 beats 3); a `usage=None` span on an unknown model
   (2 beats 3); an unknown model that would also miss a price key (3 beats 4) —
   and assert the reported reason, not just that it is unpriced.
3. **`rate_key_missing`'s zero-component carve-out.** "A zero-token component
   never triggers `rate_key_missing`" is one condition (`tokens != 0 and
   price_key not in rates`) and both halves matter. A snapshot missing
   `cache_write_1h` must price a normal call and must *not* price a call with
   one 1h token.
4. **The `Inexact` trap.** Show it fires (a >60-significant-digit token count)
   **and** that it does not fire on anything real — a 10^12-token call, every
   rate in the shipped snapshot, a two-million-span sum. A guard that fires on
   honest input would get removed.
5. **`wasted_cost_usd is None`.** Three arms: all attributed calls priced (a
   sum), one unpriceable (None), nothing attributed (`0.000000`). And the
   consistency property that matters: for every finding, `wasted` is zero if and
   only if the attributed relevant set is empty.
6. **`--no-previews` at the render boundary.** The A-c6 guard is new, is mine,
   and is exactly the "guard tuned to the call path that exists" shape. Assert
   over `hostile.jsonl` that **no** R51 payload appears in `report.json` under
   the flag, and — the arm that makes it non-vacuous — that the payloads *do*
   appear (redacted where R33 covers them) without it. Then add a field: if
   increment 4 adds a trace-derived string to `span_document` and forgets
   `free_text`, does anything go red?
7. **Redaction idempotence and ordering.** R33's property test is still owed.
   `secret_assignment` is the interesting case — it re-matches its own output.
   So is the ordering: `sk-ant-…` must redact as `anthropic_key`, not be chopped
   by `openai_key`; a JWT inside a `Bearer` header must not be double-marked.
8. **JSON determinism.** I verified 48 environments by hand (below). The suite
   should carry it, including a subprocess arm, because a `sort_keys` I forgot
   somewhere is invisible in one process with one hash seed.

### What I deliberately did not verify

- **Any per-requirement test.** By the brief, that is yours. There is not one new
  test module in this branch. My verification is the hand-run matrix below plus
  the corpus-wide invariant checks, and neither is checked in.
- **A mutation sweep.** I did not run one and I am not reporting a number. The
  increment-2 review's ruling is that a self-chosen mutation set is a check that
  cannot fail, and running one *after* writing the code, choosing the operators
  myself, would reproduce that exactly. What I did instead is name, in "what I
  think is risky", the seven places where a one-character change would produce a
  plausible wrong number — that is the target list for a sweep somebody else
  designs. The modules to weight: `cost/compute.py` and `cost/snapshot.py`
  carry the arithmetic, and `detect/base.py` is still the module every prior
  sweep under-weighted.
- **The 2-decimal grand total** (`format_display_usd`). Written because R29
  defines it and one rounding rule beats two, but nothing calls it until
  increment 4's HTML.
- **Long-context, batch and priority tiers.** Out of scope by R26; the snapshot
  models the standard tier only and every report says so.
- **The `.meta.json` sidecar path** under `analyze`. `build_adapter` defaults
  `read_sidecars=True` and no CLI flag controls it, unchanged from increment 1.
  The corpus has no sidecars by design (it would move every `finding_id`).
- **Behaviour on a real transcript.** A5's manual parser check is still owed
  before v1 ships, and the cost engine is now the part of the product where a
  fixture/reality gap converts directly into a wrong dollar figure.

### What the harness expects of you

1. Add a `tests/collection_floor.json` entry for every new test module.
2. Delete lines from `tests/traceability_pending.txt` as you cite. Still
   pending: R26–R31, R32, R33, R34, R35, R36, R37, R40, R41, R42, R43, R46,
   R47, R51. **R39 is already gone** — see A-c13; it is cited by one clause and
   is not covered.
3. Cite requirements in test *identity*, not comments.
4. `tests/allowed_skips.txt` stays empty.
5. `CURRENT_INCREMENT` in `tests/test_suite_integrity.py` is now 3.
   `redaction_pattern_removed` is still ledgered at 4; its subject
   (`report/redact.py`) exists now, so move it to 3 in the same commit as the
   canary if you write one.

## Hand-verified before handoff

Every command below was run from this branch; the output is copied verbatim.

### Lint, types, tests — both interpreters

```
$ python3 -m ruff check .            ->  All checks passed!
$ python3 -m ruff format --check .   ->  59 files already formatted
$ python3 -m mypy                    ->  Success: no issues found in 30 source files   (3.11)
$ /tmp/v312/bin/python -m mypy       ->  Success: no issues found in 30 source files   (3.12)
$ python3 -m pytest                  ->  1344 passed in 29.82s                         (3.11)
$ /tmp/v312/bin/python -m pytest     ->  1344 passed in 26.97s                         (3.12)
```

1337 → 1344: the seven added tests are `test_r44_subpackage_import_rules`
parametrized over the seven new modules. No pre-existing test was lost.

### `detectors`

```
$ swarm-observer detectors
repeated_tool_call    warning  Repeated identical tool call
agent_loop            warning  Agent repeating itself
retry_storm           warning  Retry storm
failed_tool_call      info     Failed tool call
unresolved_tool_call  warning  Unresolved tool call
blocked_agent         warning  Blocked agent
anomalous_span        warning  Anomalous model call
```

### `analyze`, and the exit codes

```
$ swarm-observer analyze .../duplicate_tool_call.jsonl --json dup.json
wrote dup.json; findings: critical=1 warning=1 info=0
exit=0

$ swarm-observer analyze .../duplicate_tool_call.jsonl --json dup.json --fail-on critical
wrote dup.json; findings: critical=1 warning=1 info=0
exit=1

$ swarm-observer analyze .../duplicate_tool_call.jsonl --json dup.json --fail-on none
wrote dup.json; findings: critical=1 warning=1 info=0
exit=0
```

Fail-closed and usage paths, with a pre-existing output file in place:

```
$ echo "PRE-EXISTING" > out.json
$ swarm-observer analyze bad.jsonl --json out.json          # line 2 is `{"type":"user"`
swarm-observer: invalid_json: bad.jsonl line 2
exit=2
$ cat out.json
PRE-EXISTING

$ swarm-observer analyze clean.jsonl --json k.json --detector no_such_detector
swarm-observer: error: unknown detector: no_such_detector (choose from repeated_tool_call, agent_loop, retry_storm, failed_tool_call, unresolved_tool_call, blocked_agent, anomalous_span)
exit=3
(no k.json was created)

$ swarm-observer analyze clean.jsonl --json nope/k.json
swarm-observer: error: output directory does not exist: nope
exit=3

$ swarm-observer analyze clean.jsonl --out r.html --json k.json
swarm-observer: error: --out is not available yet: the HTML report arrives with increment 4; use --json for now
exit=3

$ swarm-observer analyze clean.jsonl --explain --json k.json
swarm-observer: error: --explain is not available yet: the narrator arrives with increment 5
exit=3

$ swarm-observer analyze clean.jsonl --json k.json --max-records 0
swarm-observer: error: a size limit must be a positive integer
exit=3

$ swarm-observer analyze clean.jsonl --json k.json --max-line-bytes 10
swarm-observer: line_too_long: clean_single_agent.jsonl line 1 limit 10
exit=2
```

`--detector`, `--blocked-gap-seconds` and directory expansion:

```
$ swarm-observer analyze .../gaps_unexplained.jsonl --json g2.json --detector blocked_agent
wrote g2.json; findings: critical=1 warning=1 info=0
restricted: ['blocked_agent'] options: {'blocked_gap_seconds': 60, 'detectors': ['blocked_agent'], 'previews': True}
by_detector: ['blocked_agent']

$ swarm-observer analyze .../gaps_unexplained.jsonl --json g3.json --detector blocked_agent --blocked-gap-seconds 500
wrote g3.json; findings: critical=0 warning=0 info=0

$ ls mixed/            ->  clean_single_agent.jsonl  notes.txt
$ swarm-observer analyze mixed --json m.json
wrote m.json; findings: critical=0 warning=0 info=0
source_files: ['clean_single_agent.jsonl']
```

### AC7, computed against the shipped snapshot

Five `model_call` spans: one priced by exact key, one by R27's prefix rung, one
unknown model, one api-error `<synthetic>`, one with `usage: None`.

```
  seq 0: 'claude-sonnet-4-5-20250929' -> 'claude-sonnet-4-5' = 0.074666
  seq 1: 'claude-haiku-4-5-20251001'  -> 'claude-haiku-4-5'  = 0.024889
  seq 2: 'some-model-nobody-published' -> UNPRICED model_not_in_snapshot
  seq 3: '<synthetic>'                 -> UNPRICED synthetic_span
  seq 4: 'claude-sonnet-4-5-20250929'  -> UNPRICED usage_missing
  trace total: 0.099555
  total == sum of the two quantized span costs: True
  by_model: [('claude-haiku-4-5', '0.024889'), ('claude-sonnet-4-5', '0.074666')]
  R28 recomputed by hand for seq 0: 0.07466625 -> 0.074666
```

The last line recomputes span 0 from R28's text and the published rates rather
than from the code: `(10*3 + 483*15 + 17971*3.75) / 1e6`.

### Cost invariants over the whole corpus, and the check tripped four ways

All 26 fixtures analyzed, 143 `model_call` spans:

```
fixtures: 26  model_call spans: 143  summed cost: 0.529622
invariant violations: none
```

The invariants checked: `by_agent` rows sum to the total; `by_model` rows sum to
the total; per-span rows sum to the total; every `model_call` span appears
either priced or in `unpriced`; no span appears in both; no finding's waste
exceeds the trace total.

A sum that agrees with itself is not evidence, so the checker was then run
against perturbed documents:

```
unmodified            -> clean (control arm)
by_model row altered  -> ['by_model']
a priced span dropped -> ['spans', 'coverage']
total altered         -> ['by_agent', 'by_model', 'spans']
```

### Every guard I added, shown failing

```
1. precision guard fires: swarm-observer: cost_precision_exceeded:
   and an honest 1e12-token call still prices: 3000000.000000
2. alias rung (R27 step 2): m-1
   rate_key_missing: (None, 'rate_key_missing', ('cache_read',))
   zero component does NOT trigger it: ('m-1', None, ())
3. unattributed rate rejected: swarm-observer: rate_snapshot_invalid: does not match the R27 snapshot shape
   dangling alias rejected:    swarm-observer: rate_snapshot_invalid: does not match the R27 snapshot shape
   float rate rejected:        swarm-observer: rate_snapshot_invalid: does not match the R27 snapshot shape
```

### Determinism, 48 environments plus path and directory variants

Two-file trace, `{3.11, 3.12}` × `PYTHONHASHSEED ∈ {0,1,2,random}` ×
`TZ ∈ {UTC, America/Los_Angeles, Asia/Kolkata}` × `LC_ALL ∈ {C, en_US.UTF-8}`:

```
     48 07fbb0842b1654f9 d2434bd2b18384c8      (report.json sha, stdout sha)
```

and, separately:

```
reversed order, different dir: 07fbb0842b1654f9 d2434bd2b18384c8
directory expansion:           07fbb0842b1654f9
directory-expanded report identical to explicit two-file run
```

The hostile fixture, which is where an interpreter's Unicode table would show,
is byte-identical on both interpreters in both modes:

```
Python 3.11.15 'default'       report=027433edafe41092 stdout=7e5621e53636620b
Python 3.11.15 '--no-previews' report=95ca2a5e3025ebb8 stdout=1336bf97a10067c5
Python 3.12.3  'default'       report=027433edafe41092 stdout=7e5621e53636620b
Python 3.12.3  '--no-previews' report=95ca2a5e3025ebb8 stdout=1336bf97a10067c5
```

### What reaches `report.json` from `hostile.jsonl`

Default mode — redaction fires on the two complete credential shapes, and the
truncated PEM is S14:

```
AKIA             0 occurrences        [redacted:aws_key_id]     x1
sk-ant-          0 occurrences        [redacted:anthropic_key]  x1
PRIVATE KEY      1 occurrence         <-- S14: truncated, no END marker, pattern cannot match
```

With `--no-previews`, every payload R51 lists is absent:

```
'AKIA' 0   'sk-ant-' 0   'PRIVATE KEY' 0   'onerror' 0   'alert(1)' 0
'javascript:' 0   '{{7*7}}' 0   'etc/passwd' 0   '<script' 0   '‮' 0
']]>' 0   'data:text' 0
```

Before the A-c6 guard, `'onerror'` and `'alert(1)'` were 4 each under
`--no-previews`.

### The rate file actually ships

`pyproject.toml`'s comment says the package-data file needs no extra build
configuration. Checked rather than believed — built the wheel and looked
inside, then imported the snapshot from the extracted wheel with no source tree
on the path:

```
$ python -m build --wheel  ->  Successfully built swarm_observer-0.1.0-py3-none-any.whl
swarm_observer/cost/data/model_rates.json    (present in the wheel)

importing from: /tmp/wheeltest/swarm_observer/__init__.py
snapshot loads from the wheel: 2026-09-10 17 models
```

### `wasted_cost_usd is None`, shown rather than described

A repeated tool call whose redundant parent runs on a model the snapshot cannot
price:

```
repeated_tool_call:5ff676ef54ba  wasted_tokens=1000  attributed=(2,)  wasted_cost_usd=None
by_detector: [('repeated_tool_call', findings=1, findings_unpriced=1, wasted_cost_usd='0.000000')]
```

The `0.000000` is legible because `findings_unpriced=1` sits beside it.

## Left for later increments

- **HTML renderer** (R32 `escape_html`, R34, R36's section order, R37's
  timeline) — increment 4. `format_display_usd`, `WASTE_CAVEAT`, `RATE_CAVEAT`
  and `REDACTION_CAVEAT` are already defined for it, in one place each.
- **`--out` becomes required and `--json` optional again**; the two deferred
  flags lose their refusals in increments 4 and 5. `_DEFERRED_FLAGS` in
  `cli/main.py` is the one place to edit.
- **R51's probe, R47's determinism matrix, R46's socket test** — increment 4.
  The determinism numbers above are a hand-run precursor, not the harness.
- **Four R50 canaries** remain ledgered for increment 4; `redaction_pattern_removed`
  now has a subject and could move to 3.
- **S14–S19** want PM rulings. None blocks this branch; **S14** is the one with a
  security consequence and it is already true on `main`'s trajectory rather than
  introduced here.
