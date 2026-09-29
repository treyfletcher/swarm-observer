# PR: swarm-observer increment 2 — detectors and the fixture corpus

Branch: `feature/so-i2` → `main` (stacked on `feature/so-i1`)
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED)
Tasks: T6–T10. Requirements in scope: **R13–R25, R48**, plus R50's
detector-coverage canary.

## Summary

The seven detectors, the contract they share, and the checked-in corpus that
proves each one discriminates. **No** cost engine (R26–R31), report rendering
(R32–R37), `analyze` pipeline (R38–R40), narrator (R41–R43) or replay seam —
those are increments 3–5.

Five commits, one per task. `ruff check`, `ruff format --check` and
`mypy --strict` are clean; the suite is **823 tests, 0 skipped**, green on
Python 3.11 and 3.12 (620 → 823).

The headline is not the detectors — R18–R24 are pinned tightly enough that
writing them is mostly transcription. It is that **the corpus can fail**. The
increment-1 review mutation-tested the suite and found that flipping one
character of R9's collapse condition left everything green, because no
checked-in input could tell the two behaviours apart. That is this team's
signature defect, and for a detector it lives in the fixture set rather than in
the code. So every pinned boundary has a fixture on each side of it, and the
whole thing was verified by mutating each constant in both directions:
**46 of 46 mutations caught, on both interpreters.** Two of them survived a
first pass and were closed by adding fixtures, not by adding assertions — the
detail is in "Hand-verified" below.

## Requirements coverage

| Req | Where | Notes |
| --- | --- | --- |
| R13 | `detect/base.py` — `Detector`, `Severity`, `SEVERITY_RANK`, `sort_findings`; `detect/registry.py` — `ALL_DETECTORS`, `DETECTOR_SLUGS`, `detector_by_slug`, `selected_detectors`, `run_detectors` | Registry order is R18→R24 and is fixed. Each detector returns findings already sorted by `(severity_rank, slug, finding_id)`; `run_detectors` merges into one globally sorted tuple. Purity is enforced by the increment-1 R44 AST test, which forbids `detect/` importing `os`, `time`, `random`, `datetime`, `pathlib` or `subprocess` (see A-b1). |
| R14 | `detect/base.py` — `Finding`, `MAX_EVIDENCE_SPANS`, `MAX_PREVIEWS`, `build_finding` | Every prose ordering and cap is a model validator: ascending deduplicated `span_seqs` capped at 50, sorted `agent_ids`, ≤5 `previews`, sorted `metrics` keys, `finding_id` prefixed with its own detector. `wasted_cost_usd` stays `None` until increment 3. |
| R15 | `detect/base.py` — `finding_id`, `FINDING_ID_HEX` | `<detector>:<12 hex>` over `json.dumps({trace, detector, metrics, spans}, sort_keys, separators, ensure_ascii)`. `build_finding` normalizes **before** hashing, so two detectors assembling the same evidence in different orders produce the same id. |
| R16 | `detect/base.py` — `TOOL_NAME_PATTERN`, `constrain_tool_name`, `NON_CONFORMING_TOOL_NAME`; every detector's `summary=` and `metrics=` | Summaries are integers and enumerated slugs only. A non-conforming tool name becomes `<non-conforming>` in `metrics` and the original goes to `previews`. `fullmatch`, not `match` — increment 1's B5 (A-b2). |
| R17 | `detect/base.py` — `attribute_waste` | Element-wise sum of `Span.usage` over **distinct `model_call`** spans a detector named as redundant. Out-of-range seqs are ignored rather than raising. Labelled an attribution, not a counterfactual (A3). |
| R18 | `detect/repeated_tool_call.py` — `RepeatedToolCall`, `MIN_OCCURRENCES`, `CRITICAL_OCCURRENCES` | Groups by `(tool_name, tool_input_digest)`; the digest (R7) is the detector's whole view of the arguments, so it never reads a byte the trace chose. Waste = the parents of occurrences 2..n, deduplicated. |
| R19 | `detect/agent_loop.py` — `AgentLoop`, `find_loop`, `signature_of`, `MAX_PERIOD`, `MIN_REPEATS`, `CRITICAL_REPEATS` | Period ascending, then start index ascending; `k` extended to the maximal run; search resumes at `i + k*p` (A-b3). Waste = repeats 2..k. |
| R20 | `detect/retry_storm.py` — `RetryStorm`, `error_kind`, `merge_runs`, `WINDOW_SPANS`, `MIN_ERRORS`, `CRITICAL_ERRORS` | Sliding 10-span window per agent, qualifying windows merged, the merged run trimmed to its first and last error (A-b4, A-b5). Waste = every `model_call` in the merged run. |
| R21 | `detect/failed_tool_call.py` — `FailedToolCall`, `WARNING_FAILURES` | One finding per `(agent_id, tool_name)`; up to five **distinct** result previews in seq order (A-b6). Zero waste, as R21 states. |
| R22 | `detect/unresolved_tool_call.py` — `UnresolvedToolCall`, `UNKNOWN_TOOL_PATTERNS`, `looks_like_unknown_tool`, `REASONS` | The carve-out is positional — the agent's highest-`seq` span — which is exactly the span the mapper counts as `dangling_tool_use`, so the two halves cannot drift. `orphan_result` is unattributed; see **A-b7**, the one flagged item in this branch. |
| R23 | `detect/blocked_agent.py` — `BlockedAgent`, `covered_millis`, `CRITICAL_GAP_SECONDS`, `COVERAGE_NUMERATOR`/`COVERAGE_DENOMINATOR` | Integer milliseconds throughout; coverage is a **union** of other agents' intervals clipped to the gap, not a sum. Zero waste. |
| R24 | `detect/anomalous_span.py` — `AnomalousSpan`, `mad_of`, `POPULATION_FLOOR`, `MAD_MULTIPLE`, `CRITICAL_MAD_MULTIPLE`, `FLOOR_TOKENS`, `FLOOR_DURATION_MS`; `detect/base.lower_median` | Lower median at index `(n-1)//2`, MAD as the lower median of absolute deviations, all `int`. Population gated once (A-b8). Zero waste. |
| R25 | `detect/base.py` — `DetectorConfig`; `registry.selected_detectors` | Exactly `blocked_gap_seconds: int = 60` and `enabled: frozenset[str] \| None = None`, frozen and `extra="forbid"`. A test drives the knob and shows it moves exactly one finding and nothing else (A9). |
| **R48** | `tests/detector_corpus.py` — `coverage_arms`, `coverage_problems`, `assert_detector_coverage`, `detector_classes_in`, `registry_problems`, `assert_registry_complete`, `expectation_problems`, `assert_corpus_contract`; `tests/test_detector_coverage.py`; `tests/test_detector_corpus.py` | Both arms, per detector, parametrized. The AST scan reads modules rather than imported objects, because a detector nobody imports has no object to inspect. The corpus contract is what makes R48 non-vacuous. |
| R50 (part) | `tests/canaries/test_canary_detector_coverage_dropped.py` | The ledgered increment-2 canary, now due (`CURRENT_INCREMENT = 2`). |
| T6 | `tests/fixtures/traces/*.jsonl` + `*.expected.json` | 26 fixtures, 26 expectation files. |

## The fixture/expectation contract (what the tester asserts against)

Every `tests/fixtures/traces/<name>.jsonl` ships
`tests/fixtures/traces/<name>.expected.json`:

```json
{
  "fixture": "<name>.jsonl",
  "purpose": "one sentence saying what this fixture is for",
  "trace": {"trace_id": "...", "spans": 14, "agents": 1,
            "warnings": {"<code>[:<detail>]": <count>}},
  "detectors": {
    "<every registry slug>": {"fires": false},
    "<every registry slug>": {
      "fires": true,
      "findings": [{
        "severity": "warning",
        "finding_id": "agent_loop:026ac4c1c74a",
        "metrics": {...},
        "span_seqs": [...],
        "agent_ids": [...],
        "wasted": {"input_tokens": 0, "output_tokens": 0,
                   "cache_read_input_tokens": 0,
                   "cache_creation_5m_tokens": 0,
                   "cache_creation_1h_tokens": 0},
        "previews_count": 1
      }]
    }
  }
}
```

Rules the contract enforces (`expectation_problems`, driven per fixture by
`test_r18_r24_fixture_matches_its_checked_in_expectation`):

1. `detectors` names **exactly** `DETECTOR_SLUGS` — no more, no fewer. Dropping
   a key is a failure, which is what makes R50's canary meaningful.
2. `fires` must match reality for every slug. Both directions.
3. `fires: true` with no `findings` is a failure. "Something fired" cannot fail
   when a threshold moves.
4. When it fires, the listed findings must match the produced ones **in order**,
   on every key in `FINDING_KEYS`.
5. The `trace` summary (id, span count, agent count, warning histogram) must
   match, so an accidental fixture edit surfaces as a mismatch rather than as a
   silently different corpus.
6. Every fixture has an expectation and every expectation has a fixture; no
   `.meta.json` sidecar may sit in the directory (it would change every pinned
   `finding_id` without changing a fixture byte — R5 hashes named inputs only).

**Regenerating.** There is no checked-in regenerator, deliberately: the
expectations are the specification of the corpus, and a one-command rewrite
would turn a failing contract into a chore. When a fixture legitimately changes,
`tests.detector_corpus.observed_document(path)` returns the document that fixture
*would* produce; diff it against the checked-in one and update by hand.

## The corpus, and which boundary each fixture pins

| fixture | fires | pins |
| --- | --- | --- |
| `clean_single_agent` | — | the silent arm for all seven (R48b); two different tools sharing one input digest, so R18's group key cannot forget the name |
| `multi_agent_subagent` | — | a 128 s gap 84% covered by a subagent: R23's explained rule, second silent arm |
| `duplicate_tool_call` | R18 ×2 | occurrences 4 (critical) and 2 (warning) beside a 1 (silent), in a signature order with no three-fold cycle so R19 stays quiet |
| `loop_two_repeats` | R18 | AC8's negative: `A B A B C` fires no R19 |
| `loop_three_repeats` | R18 ×2, R19 | AC8: `A B A B A B C` → period 2, repeats 3, warning |
| `loop_four_repeats` | R18, R19 | repeats 4 → critical; one finding, not one per start index |
| `loop_ambiguous_period` | R18, R19 | six identical decisions: period 1 ×6, not period 2 ×3 — R19's ascending search order |
| `retry_storm` | R20, R21 | 5 errors, `kinds: mixed` (tool + api), critical; merged run trimmed to its errors |
| `retry_storm_warning` | R20, R21 | exactly 3 errors → warning, in an agent shorter than the 10-span window |
| `api_error_rate_limit` | R21 | exactly 2 adjacent error spans: R20's threshold from below; an api-error record (R12) |
| `failed_tool_twice` | R21 | exactly 2 failures of one tool → warning (R21's info/warning boundary) |
| `gaps_unexplained` | R23 ×2 | 59 s (silent), 60 s (warning), 400 s (critical) |
| `gaps_explained` | R23 | 120 s gaps at 75%, exactly 50% (both explained) and 25% (fires) |
| `gaps_partly_explained` | R23 | 40% coverage fires — pins the rule at 50% rather than anything looser |
| `parallel_tool_calls` | R23 | an agent's own long tool call overlaps its own later gap; R23 counts *other* agents |
| `outlier_tokens_and_duration` | R24 ×2 | AC11 at exactly population 8: `[100]*7 + [50000]`, med 100, mad 0; plus a 120 s duration outlier |
| `outlier_population_seven` | — | population 7 with the same extreme outlier: the floor from below |
| `outlier_at_mad_threshold` | — | value exactly `med + 6*mad`, above the token floor: `>` is strict |
| `outlier_above_mad_threshold` | R24 | one above it → warning |
| `outlier_at_critical_threshold` | R24 | exactly `med + 12*mad` → still warning |
| `outlier_above_critical_threshold` | R24 | one above it → critical |
| `outlier_below_floors` | — | 9,999 tokens and 29,999 ms clear their MAD thresholds and stay under the floors |
| `truncated_dangling_tool_use` | R22 | AC9: a mid-trace unresolved call fires, the trailing one is carved out and counted |
| `unknown_tools_and_orphans` | R21, R22 ×2 | `unknown_tool` (critical) and `orphan_result` (warning) |
| `compaction_boundary` | — | a `compact_boundary` system event is not a retry-storm error span |
| `hostile` | R18, R21, R22 | R51's payloads in every field; a non-conforming tool name and a script-tag agent id |

Structural fidelity is against A1's documented shape (camelCase record keys, a
`message` object with `id`/`model`/`content`/`usage`, the
`cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens` breakdown, one record
per streamed content block, multi-fragment groups sharing `requestId` and
`message.id`). **No real transcript data is committed** (A5).

## Assumptions and judgement calls (A-b series)

- **A-b1 (`detect/` may not import `datetime`)**: the increment-1 R44 purity
  test forbids it, and it is right to. Timestamps are therefore annotated with
  the model's own `UtcDatetime` alias, and `millis_between` does the arithmetic
  by hand (`days*86_400_000 + seconds*1_000 + microseconds//1_000`) rather than
  through `timedelta.total_seconds()`, which returns a float. R23 and R24 both
  say their arithmetic is integral; a float would make a gap of exactly 60
  seconds depend on binary rounding.
- **A-b2 (`fullmatch` in R16's tool-name guard)**: same defect family as
  increment 1's B5. Under `match`, `"Bash\n"` is a conforming tool name and the
  newline rides into a `metrics` value that R34 will later render.
- **A-b3 (R19's "at most one finding per agent")**: R19 says both "at most one
  finding per agent" and "the search then resumes at index `i + k*p` so a long
  trace can yield multiple non-overlapping loops per agent". These contradict.
  Implemented as the second: one finding per detected loop, non-overlapping,
  the search resuming past each run. The first clause is read as "not one
  finding per matching start index", which is the thing a naive implementation
  gets wrong. **Flagged for the PM**: one sentence in R19 settles it.
- **A-b4 (a window shorter than 10 spans still counts)**: R20 says "a sliding
  window of 10 consecutive spans". An agent with six spans has no such window,
  so a literal reading makes `retry_storm` structurally unable to fire on a
  short agent — five spans, four of them errors, silently clean. When an agent
  has fewer than ten spans its whole span list is the single window.
  `retry_storm_warning` is that case.
- **A-b5 (the merged run is trimmed to its errors)**: unioning raw ten-span
  windows would report a run padded with whatever sat either side, and
  `window_spans` would then measure the window rather than the storm. The
  reported run starts at the first error span and ends at the last.
- **A-b6 (R21's previews are distinct)**: R21 says "up to 5 distinct
  `tool_result_preview` values, in `seq` order"; implemented as distinct by
  value, first occurrence wins.
- **A-b7 (R22's `orphan_result` cannot be attributed to an agent) — FLAGGED**:
  R22 fires per `(agent_id, reason)`, but a `tool_result` block whose
  `tool_use_id` matches no `tool_use` produces **no span** (R12 emits one span
  per `tool_use`, and a user record that is nothing but tool results emits
  none). The only trace of it in the normalized model is the aggregated
  `orphan_tool_result` `ParseWarning`, which by R10's design carries a count and
  neither an agent nor a `seq`. The finding is therefore emitted once per trace
  with `agent_ids = ()` and `span_seqs = ()`, rather than attributed to a
  guessed agent. The metrics `tool_name` is the enumerated slug `<unknown>`.
  **PM**: making this attributable needs a field on the model, which is an R2
  amendment and a `TRACE_SCHEMA_VERSION` bump; the alternative is to say in R22
  that `orphan_result` is trace-scoped.
- **A-b8 (R24's population floor is checked once, not per dimension)**: R24
  says "Population = all `model_call` spans with non-null `usage`. Skips
  entirely when the population size is `< 8`", and then defines the duration
  value only "over spans with both endpoints". Read literally: one gate on the
  population, and the duration statistics computed over whatever subset of it
  has timing. A dimension with no measurable spans is skipped. **Flagged
  lightly**: if the PM means the floor to apply per dimension, that is a
  one-line change and one more fixture.
- **A-b9 (`unknown_tool` degrades under `--no-previews`)**: R22's `unknown_tool`
  matches against the tool result's preview text, which A10 blanks at
  model-build time. With `--no-previews` every `unknown_tool` therefore degrades
  to `failed_tool_call`. That is the correct trade — the mode exists so the
  hostile bytes are not in the process — but the two modes can report different
  *reasons* for the same trace, which increment 4's R51 arm should expect.
- **A-b10 (severity for `retry_storm`'s `span_seqs`)**: the evidence spans are
  the error spans, not every span of the merged run; `window_spans` already
  reports the run length, and a 50-span evidence cap would otherwise truncate
  the useful half.
- **A-b11 (`finding_id` is computed from the capped, sorted values)**: R15
  hashes `metrics` and `span_seqs`; `build_finding` normalizes first, so the id
  is a function of the finding as it will be rendered rather than of whatever
  the detector happened to pass in.
- **A-b12 (expectation files pin `finding_id`)**: this makes them goldens as
  well as contracts. It is deliberate friction: any change to a fixture, to the
  mapper or to `build_finding` must be acknowledged in the diff. It also gives
  the corpus a free cross-process determinism check.
- **A-b13 (the corpus reproduces S1's noise)**: fixtures carry the per-record
  keys A1 documents (`cwd`, `version`, `gitBranch`, `userType`), so each one
  emits `unknown_extra_key` warnings in the tens. That is R4 behaving as
  written, and it is evidence for the reviewer's S1 ruling rather than a fixture
  defect. The counts are pinned in the expectation files, so the amendment —
  when it lands — will be visible as a corpus diff.

## Tester surface

**Public API to drive.**

```python
from swarm_observer.detect.base import (
    Detector, DetectorConfig, Finding, Severity,
    SEVERITY_RANK, SEVERITIES, MAX_EVIDENCE_SPANS, MAX_PREVIEWS, FINDING_ID_HEX,
    TOOL_NAME_PATTERN, NON_CONFORMING_TOOL_NAME, UNKNOWN_TOOL_NAME,
    finding_id, build_finding, sort_findings, constrain_tool_name, attribute_waste,
    spans_by_agent, agent_order, model_calls, seq_by_span_id,
    first_tool_call_by_parent, millis_between, span_duration_ms, lower_median,
)
from swarm_observer.detect.registry import (
    ALL_DETECTORS, DETECTOR_SLUGS, detector_by_slug, selected_detectors, run_detectors,
)
from swarm_observer.detect.repeated_tool_call import MIN_OCCURRENCES, CRITICAL_OCCURRENCES
from swarm_observer.detect.agent_loop import (
    MAX_PERIOD, MIN_REPEATS, CRITICAL_REPEATS, TEXT_SIGNATURE, find_loop, signature_of,
)
from swarm_observer.detect.retry_storm import (
    WINDOW_SPANS, MIN_ERRORS, CRITICAL_ERRORS, KIND_TOOL, KIND_API, KIND_MIXED,
    error_kind, error_text, merge_runs,
)
from swarm_observer.detect.failed_tool_call import WARNING_FAILURES
from swarm_observer.detect.unresolved_tool_call import (
    REASONS, UNKNOWN_TOOL_PATTERNS, ORPHAN_WARNING_CODE, looks_like_unknown_tool,
)
from swarm_observer.detect.blocked_agent import (
    CRITICAL_GAP_SECONDS, COVERAGE_NUMERATOR, COVERAGE_DENOMINATOR, covered_millis,
)
from swarm_observer.detect.anomalous_span import (
    POPULATION_FLOOR, MAD_MULTIPLE, CRITICAL_MAD_MULTIPLE,
    FLOOR_TOKENS, FLOOR_DURATION_MS, DIMENSIONS, DIMENSION_FLOORS, mad_of,
)
# corpus helpers (tests/, not the package)
from tests.detector_corpus import (
    FIXTURE_DIR, fixture_paths, fixture_names, load_trace, load_expectation,
    load_expectations, findings_by_detector, observed_document, finding_document,
    expectation_problems, assert_corpus_contract,
    coverage_arms, coverage_problems, assert_detector_coverage,
    detector_classes_in, registry_problems, assert_registry_complete,
)
```

**What to inject.** Nothing is mocked and nothing needs to be. A detector is a
pure function of `(Trace, DetectorConfig)`; a `Trace` comes from
`ClaudeCodeSource().load([path], IngestLimits())` or from
`tests.detector_corpus.load_trace`. `Trace` and `Span` are frozen pydantic
models, so `trace.model_copy(update={"spans": ...})` builds a counterexample the
mapper cannot produce — that is how R17's `model_call` check gets a probe.

**Where the boundaries are best tested.** Prefer the exported helpers over
building a 27-record fixture:

- `find_loop(signatures, start_floor)` takes a plain list of tuples — the whole
  R19 search, including the period 8 / period 9 boundary the corpus does not
  reach (24 and 27 model calls respectively). **This is the biggest gap I am
  handing you deliberately**: the corpus covers periods 1 and 2 only.
- `lower_median` / `mad_of` for R24's statistics, including even populations,
  `mad == 0` and single-element inputs (which raise).
- `merge_runs` for R20's window merging, including adjacent-but-not-overlapping
  ranges.
- `covered_millis` for R23's interval union, including two intervals that
  overlap each other and a gap that is entirely covered.
- `constrain_tool_name` for R16's pattern, both arms.
- `finding_id` for R15's stability and sensitivity.

**What the harness expects of you** (unchanged from increment 1, plus one):

1. Add a `tests/collection_floor.json` entry for every new test module; floors
   are at the exact current counts.
2. Delete lines from `tests/traceability_pending.txt` as you cite. R13–R25 and
   R48 are already gone; R26–R37, R39–R43, R46, R47 and R51 remain.
3. Cite requirements in test *identity*, not comments.
4. `tests/allowed_skips.txt` stays empty.
5. **New**: if you change a fixture, change its `.expected.json` in the same
   commit. `observed_document(path)` gives you the new document to diff against;
   do not regenerate the whole corpus.

**What I did not test, and is yours.** Per-requirement, clause-by-clause cases
for R13–R25: table-driven `find_loop`, the R24 statistics at every edge, R20's
`kinds` enumeration on single-kind and mixed runs, R22's five phrases
individually and case-insensitively, R23 with `blocked_gap_seconds` at 0 and at
absurd values, `DetectorConfig(enabled=...)` against unknown slugs, and
`Finding` construction against every invalid shape. My modules assert the corpus
contract and the coverage integrity; they are not a substitute for those.

## Hand-verified before handoff

- **Both interpreters.** Full suite green on CPython 3.11 and 3.12 — 823 passed,
  0 skipped, on each. `ruff check`, `ruff format --check` and `mypy --strict`
  clean.
- **Findings are byte-identical across 28 environments.** A digest over every
  finding the whole corpus produces — ids, severities, metrics, evidence spans,
  agent ids, waste totals **and previews** — is one value across
  {3.11, 3.12} × `PYTHONHASHSEED` ∈ {0, 1, 2, random} × `TZ` ∈ {UTC,
  America/Los_Angeles, Asia/Kolkata}, plus `LC_ALL` ∈ {C, en_US.UTF-8} and a
  different working directory. Previews are in the digest on purpose: R8's
  normalization reads `str.isprintable()`, which answers from the interpreter's
  Unicode table, so this is also the check that no fixture carries a code point
  3.11 and 3.12 disagree about (the increment-1 addendum's finding). The suite
  carries the in-process and three-subprocess forms of the same check.
- **Mutation testing: 46 constants and conditions, both interpreters, 46
  caught.** Every threshold in R18–R24 moved in both directions, plus the group
  keys, the search direction, the signature's digest component, the strict `>`
  in R24, R16's `fullmatch`, R17's deduplication and kind check, R15's metrics
  component, R13's sort key and R14's two caps. Two survivors from the first
  pass are worth naming, because both were **corpus** gaps rather than code
  gaps:
  - `COVERAGE_DENOMINATOR 2 → 3` (a 33% rule instead of 50%) changed nothing,
    because the corpus only had 75%, 50% and 25% cases. Closed by
    `gaps_partly_explained` (40%).
  - deleting `if other_id == agent_id: continue` changed nothing, because in
    every fixture an agent's own spans happened to sit outside its own gaps.
    Closed by `parallel_tool_calls`, where one model call emits a 300 s tool
    call and a 5 s one and the long span overlaps the gap that follows the
    short one.
  A third, `CRITICAL_MAD_MULTIPLE 12 → 13`, was closed by adding the pair of
  fixtures that bracket 12×MAD.
- **The mutation harness itself had the defect it hunts.** Same-length edits
  (`MIN_ERRORS = 3` → `= 2`) written inside one second leave a `.pyc` whose
  `(mtime, size)` is unchanged, so CPython can run the *previous* source. Two
  runs disagreed about which mutations survived, which is how it surfaced. The
  harness now purges `__pycache__` around every mutation and runs with
  `PYTHONDONTWRITEBYTECODE=1`; the 46/46 result is from the fixed harness on
  both interpreters. Recorded because a reviewer repeating the sweep should not
  rediscover it, and because "the check never ran" is this project's theme.
- **The registry AST scan catches a real unregistered detector.** Dropped a
  `detect/ghost_probe.py` defining a detector-shaped class into the package: the
  suite went red naming `ghost_probe`. Removed `anomalous_span.DETECTOR` from
  `ALL_DETECTORS` while its module remained: the session failed at collection
  (the R49 floor hook, naming both affected modules) and, with floors bypassed,
  on `test_r13_the_registry_is_populated_and_its_slugs_are_unique`,
  `test_r48_the_corpus_has_expectations_to_measure` and
  `test_r48_the_ast_scan_finds_exactly_the_registered_detectors`. Both restored
  and re-verified green.
- **Every detector fires on its positives and is silent elsewhere**, from a
  fresh run rather than from the expectation files: `repeated_tool_call` 6/20,
  `agent_loop` 3/23, `retry_storm` 2/24, `failed_tool_call` 6/20,
  `unresolved_tool_call` 3/23, `blocked_agent` 4/22, `anomalous_span` 4/22
  (fires/silent, out of 26 fixtures).
- **The R50 canary's own control arm is clean** and each of its 29 cases
  observes a real failure: a dropped detector key, each arm emptied, a
  `fires: true` with no findings, an altered metric, an altered severity, a
  flipped arm, an unregistered class, a registry slug with no module, and a
  non-detector class the scan must ignore.

## Left for later increments

- **Cost** (R26–R31), **report** (R32–R37), **the `analyze` pipeline**
  (R38–R40), **narrator** (R41–R43), **replay seam**. `Finding.wasted_cost_usd`
  is `None` and stays `None` until T12 fills it.
- **Four R50 canaries** still ledgered for increment 4: injection-probe identity
  escape, attribute-allowlist injection, offline socket permitted, redaction
  pattern removed.
- **A-b7** (`orphan_result` attribution), **A-b3** (R19's contradictory
  sentence) and **A-b8** (R24's population floor per dimension) want PM rulings.
  None blocks the branch.
- **R51's probe** belongs to increment 4; `hostile.jsonl` is checked in and
  already carries every payload R51 lists, including the 80,000-character string
  and the three credential shapes.
