# Test report: swarm-observer increment 2 (`feature/so-i2`)

Tester: tester-agent · Spec: `docs/specs/swarm-observer-v1.md` (APPROVED) ·
PR write-up under test: `docs/prs/feature-so-i2.md` ·
Prior review honoured: `docs/reviews/feature-so-i1.md` (rulings S1–S6, Tension 1,
and the Python-3.12 addendum)

Scope tested: **R13–R25 and R48**, plus the parts of R13/R15 that make findings a
pure function of the trace. Cost (R26–R31), report rendering (R32–R37), the
`analyze` pipeline (R38–R40) and the narrator (R41–R43) do not exist in this
increment; their absence is not reported as a failure.

## Result

| | |
| --- | --- |
| Suite before / after | 823 / **1301 passed, 3 xfailed, 0 skipped, 0 failed** |
| Python 3.11.15 | 1301 passed, 3 xfailed |
| Python 3.12.3 | 1301 passed, 3 xfailed |
| New test modules | 5 (`test_detector_contract.py`, `test_detector_firing_boundaries.py`, `test_detector_registry_integrity.py`, `test_detector_determinism.py`, `test_detector_adversarial.py`) plus the non-test helper `tests/synthetic_traces.py` |
| `ruff check` · `ruff format --check` · `mypy --strict` | clean on both interpreters |
| Mutation sweep | **119 mutations on each interpreter, 8 survivors, identical set on 3.11 and 3.12 — every survivor differentially proved semantically equivalent.** Against the branch as handed over, the same 119 left **32** survivors. |
| Determinism | one findings digest across **48 environments** (2 interpreters × 4 `PYTHONHASHSEED` × 3 `TZ` × 2 `LC_ALL`, plus a foreign CWD) |
| `tests/allowed_skips.txt` | still empty, as required |
| `tests/traceability_pending.txt` | unchanged — the coder had already removed R13–R25 and R48; no pending id is now cited |
| Collection floors | 5 new entries at the exact current counts |

The 3 xfails are **strict** and are the three bug reports below expressed as
tests: each fails the suite the moment the defect is fixed, so no fix can land
without the ledger being updated.

## The headline: the mutation sweep

The coder reports "46 boundary mutations, 46/46 caught". I could not reproduce a
number that high because the mutation set is what decides it, and the task
explicitly asked me to widen it past threshold constants — the increment-1 review
found R9's `and`→`or` surviving, and no constant-only sweep would have found that.

My set is **119 mutations** across `detect/`: every threshold moved in both
directions *and* `>=`↔`>`, `<`↔`<=`, `and`↔`or`, off-by-one slice and `range`
bounds, sign flips, swapped call arguments, dropped group-key components, dropped
sort-key components, dropped guard clauses, and `while`→`if`. Same harness
discipline as the coder's: `__pycache__` purged around every mutation and
`PYTHONDONTWRITEBYTECODE=1`, because a same-length edit written inside one second
otherwise leaves a `.pyc` whose `(mtime, size)` is unchanged.

**Against the branch as handed over: 32 of 119 survived.**
**After the tests in this report: 8 of 119 survive, and all 8 are equivalent
mutants** — proved so by differential testing, not by argument (see below). The
whole sweep was run twice, once per interpreter, and 3.11 and 3.12 name the same
eight; nothing in `detect/` is version-sensitive.

The 24 real gaps closed, by requirement:

| Requirement | Surviving mutation | Why the checked-in corpus could not see it |
| --- | --- | --- |
| R13 | sort key drops the slug | every `finding_id` begins with its slug, so for today's seven names the component is redundant. It stops being redundant the moment one slug is a prefix of another (`:` sorts after every digit). Pinned on `sort_findings` directly. |
| R14 | `build_finding` caps evidence at 51 instead of 50 | no fixture produces more than 50 evidence spans, so the mismatch between the slice and the model validator is unobservable — and would surface as a `ValidationError`, not a longer table |
| R17 | bound `seq >= len` → `seq > len` | no detector hands in a seq one past the end; the mutant is an `IndexError` waiting for one that does |
| R18 | preview taken from the last occurrence | in every fixture the group members share their preview text |
| R19 | `MAX_PERIOD` 8→7 and 8→9 | the corpus reaches periods 1 and 2 only (the coder flagged this himself as the biggest handed-over gap) |
| R19 | period `range` loses 8 | same |
| R19 | start `range` loses its last index | every corpus loop has a trailing signature after the cycle, so no loop starts at exactly `len(S) - 3p` |
| R19 | signature drops the tool name | no fixture has two tools sharing an input digest *inside a loop* |
| R20 | `WINDOW_SPANS` 10→9 and 10→11 | no fixture has errors exactly 9 or exactly 10 positions apart |
| R20 | `CRITICAL_ERRORS` 5→4 | the corpus has 3-error and 5-error storms, never 4 |
| R20 | window bound `<` → `<=` | same — an 11-wide window is only visible with errors 10 apart |
| R20 | merge condition `<=` → `<` | **this is BUG-1's fix direction**; now caught by the strict xfail |
| R20 | merge everything regardless of overlap | no fixture has two separate storms |
| R20 | window starts lose the last one | no fixture has a storm confined to its agent's final ten spans |
| R23 | `CRITICAL_GAP_SECONDS` 300→299 and 300→301 | the corpus's critical gap is 400 s |
| R23 | severity `>=` → `>` | no gap of exactly 300 s |
| R24 | `FLOOR_TOKENS` 10 000→10 001 | the corpus has 9 999 (below) and 50 000 (far above), never exactly 10 000 |
| R24 | `FLOOR_DURATION_MS` 30 000→30 001 | same for 29 999 / 120 000 |
| R24 | floor test `value < floor` → `<=` | same |
| R24 | population loses the `usage is not None` filter | no fixture mixes model calls with and without usage near the floor |

The remaining 8 survivors are **equivalent mutants** — the mutated source cannot
produce a different result, so no test can kill them and none should try. Each was
checked by running the original and the mutant over a randomized corpus and
comparing every finding, rather than by reasoning about it:

| Survivor | Evidence of equivalence |
| --- | --- |
| R19 `last_start` +1 | 20 000 random signature sequences × random floors: 0 differing `find_loop` results. The extra start index makes the third slice shorter than the cycle, so it can never match. |
| R20 window stop unclamped | 200 000 random error layouts: 0 differences. Only the final window can be unclamped, and no window starts after it. |
| R20 `inside` bound `<` → `<=` | 200 000 layouts: 0 differences. An error at exactly the merged stop is impossible — if `[s, s+10)` qualifies and `s+10` errors, then `[s+1, s+11)` qualifies too and the run extends. |
| R23 clip `>` → `>=` | 400 random traces × 3 configs: identical findings. A degenerate interval contributes zero to a union. |
| R23 union merge `<=` → `<` | same run, identical. Two abutting intervals sum to the same total either way. |
| R23 gap clamp removed | 3 000 random traces (including negative durations and out-of-order timestamps) × 3 configs, 38 583 findings: identical. A negative gap can never clear a `ge=0` threshold. |
| R23 interval `end > start` filter removed | same run, identical. A zero- or negative-length interval is dropped again by the clip. |
| R24 `abs(x - med)` → `abs(med - x)` | 400 random traces: identical. |

Two of those (the R23 pair) are dead defensive code by that evidence. They are
cheap and correct, and I am not asking for them to be removed — recorded so a
future sweep does not spend a day on them.

## Coverage

| Req | Tests (all in the five new modules unless noted) | Result |
| --- | --- | --- |
| **R13** | `test_detector_contract.py::TestDetectorProtocolR13` (35, parametrized over every registered detector: protocol attributes, tuple-of-`Finding`, already-sorted output, trace unmutated, idempotent), `TestFindingSortOrderR13` (5), `TestRegistryR13` (12); `test_detector_determinism.py::TestFindingsAreDeterministicR13` (16); `test_detector_adversarial.py::TestDegenerateTracesR13` (15) and `TestScaleR13` (5) | pass |
| **R14** | `TestFindingShapeR14` (20 — including 14 invalid shapes, both cap boundaries, frozen, `Decimal \| None`), `TestBuildFindingNormalizesR14` (5); adversarial `assert_well_formed` over every produced finding | pass |
| **R15** | `TestFindingIdR15` (16 — shape, the spec's canonical payload recomputed from the requirement text, stability, 6 sensitivity cases, uniqueness within a trace, 4 subprocess/`PYTHONHASHSEED` cases); determinism module (16) | pass |
| **R16** | `TestSummaryAndToolNameR16` (27 — 8 conforming names, 14 non-conforming, the `$`-vs-`fullmatch` newline trap with its premise asserted, `None`, the `<unknown>` slug, and a hostile-trace summary sweep); `TestHostileFixtureR16` (13) and `TestSyntheticHostileInputR16` (59) | pass |
| **R17** | `TestWasteAttributionR17` (10 — element-wise sum, dedup, non-`model_call`, no-usage, empty, negative seq, seq one past the end, zero usage, 2⁶² exactness), `TestSharedTraceViewsR17` (14) | pass |
| **R18** | `TestRepeatedToolCallR18` (15 — 1/2/3/4 occurrences, both group-key halves, two groups, first/last metrics, first-occurrence preview, waste from occurrences 2..n, dedup, no-parent, non-conforming name, the 50-span cap) | pass |
| **R19** | `TestAgentLoopSearchR19` (29 — periods 1–8 each found, period 9 and 10 and 12 not found, 2 vs 3 repeats, a loop ending at the final signature, maximal run, ascending period, ascending start, the resume floor, 5 short sequences, a broken cycle, signature composition), `TestAgentLoopFiringR19` (12) | pass |
| **R20** | `TestRetryStormR20` (23 — 2/3/4/5 errors, errors 10 vs 11 apart, a storm in the final ten spans, a sub-window agent, run trimming, two storms, overlapping storms, the three `kinds` values, `error_kind` over every span shape, per-agent windows, waste, evidence) | pass + **1 strict xfail (BUG-1)** |
| **R21** | `TestFailedToolCallR21` (11 — 0/1/2 failures, `missing` is not a failure, both group-key halves, distinct previews with first-occurrence-wins, the five-preview cap against eight distinct values, zero waste, `first_seq`) | pass |
| **R22** | `TestUnresolvedToolCallR22` (36 — the carve-out from both sides and per agent, a trailing non-tool span, a trailing `unknown_tool`, all five phrases individually and case-insensitively and through the detector, 5 non-matching texts, the `--no-previews` degradation, `orphan_result` from the warning, trace-scoped with no agent, absent and unrelated warnings, all three reasons at once, zero waste) | pass |
| **R23** | `TestBlockedAgentR23` (26 — 59/60 s, 299/300/301 s, `floor(gap)`, coverage at 40/49/50/51/100 %, union vs sum, disjoint intervals, clipping, an agent's own spans, `covered_millis` directly, a zero-length interval, a missing endpoint, an untimed span between two timed ones, a negative gap, `blocked_gap_seconds` at 0 / 29 / 30 / 10⁹, zero waste, evidence) | pass |
| **R24** | `TestAnomalousSpanR24` (26 — population 7 vs 8, the usage filter, tool calls excluded, 6×MAD −1/0/+1, 12×MAD −1/0/+1, token floor 9 999/10 000/10 001, duration floor 29 999/30 000/30 001, lower median, `mad_of`, zero MAD, the metric set, one finding per (span, dimension), a skipped dimension, the A-b8 single gate, zero waste, the silent arm, two outliers, integrality) | pass |
| **R25** | `TestDetectorConfigR25` (17 — defaults, exactly two fields, frozen, unknown knob, negative value, the whole legal range, `enabled` None/subset/unknown/empty, `run_detectors` honouring it, one knob moving one detector, and an AST scan asserting no detector reads any other `config` attribute); determinism module's corpus-wide knob check | pass |
| **R48** | `test_detector_registry_integrity.py` (29) — both arms recomputed from live detector runs and cross-checked against the expectation files, the two guard failure branches driven directly, the AST scan against a scratch package with a ghost detector / a removed registry entry / a deleted module / a non-detector class, and three end-to-end cases that copy the repository, introduce each drift for real, and assert the suite goes red (with a green control arm) | pass |

`tests/synthetic_traces.py` is a non-test helper: a `TraceBuilder` that assembles
fully validated `Trace` values directly. It exists for three things the JSONL
corpus cannot do cheaply — putting both sides of a boundary one integer apart,
reaching a population of 8 or a period of 8 without a 27-record file, and building
the two shapes the mapper cannot emit that R17 and R24 nevertheless pin behaviour
for (a non-`model_call` seq handed to `attribute_waste`; a span whose `end`
precedes its `start`).

### Independent verification of the coder's claims

| Claim | Verdict |
| --- | --- |
| "823 tests, 0 skipped, green on 3.11 and 3.12" | **Confirmed.** 823/823 on both before my changes; 1301 + 3 xfail on both after. |
| "Findings are byte-identical across 28 environments" | **Confirmed and widened to 48**: 2 interpreters × `PYTHONHASHSEED` ∈ {0,1,2,random} × `TZ` ∈ {UTC, America/Los_Angeles, Asia/Kolkata} × `LC_ALL` ∈ {C, en_US.UTF-8}, run from a foreign CWD. One digest: `45fb6670…d2b202b8`. That value is now checked in as `CORPUS_FINDINGS_DIGEST` so CI's two matrix legs must agree on it — which is the only automated check that would catch the interpreter-dependent `str.isprintable()` drift the increment-1 addendum found. |
| "46 of 46 mutations caught" | **Not disputed but not sufficient.** A wider 119-mutation set left 32 survivors on the branch as handed over. See above. |
| "The registry AST scan catches a real unregistered detector … both restored and re-verified green" | **Confirmed, and now automated.** Three tests copy the repository, apply each drift for real, and assert the suite fails naming the detector; a fourth asserts the unmodified copy passes, so a broken copy step cannot make the probes pass for the wrong reason. |
| "Every detector fires on its positives and is silent elsewhere (6/20, 3/23, 2/24, 6/20, 3/23, 4/22, 4/22)" | **Confirmed** by recomputing both arms from live detector runs and asserting they equal the arms the expectation files declare. |
| "`fullmatch`, not `match` — increment 1's B5" | **Confirmed, and the trap is now pinned with its premise**: the test first asserts that `re.match(TOOL_NAME_PATTERN, "Bash\n")` *does* succeed, so if the pattern ever loses its need for `fullmatch` the test says so instead of passing vacuously. |
| "The corpus reproduces increment 1's S1 noise (A-b13)" | **Confirmed** — see the adjudication below. |

## Bugs

Three. All are reported, none is fixed here; each has a strict xfail carrying the
repro.

### BUG-1 (medium-high) — `retry_storm` merges windows that do not overlap, inflating both the count and the severity

R20: *"overlapping windows are merged into one finding covering the maximal run."*
`merge_runs` decides overlap with `start <= merged[-1][1]` on **half-open**
ranges, so the windows `[0, 10)` and `[10, 20)` — which share no span — are merged
into `[0, 20)`.

An agent with 20 spans that errors at 0, 1, 2 and again at 17, 18, 19 has exactly
those two qualifying windows and nothing between them:

```python
from tests.synthetic_traces import error_run
from swarm_observer.detect.base import DetectorConfig
from swarm_observer.detect.registry import detector_by_slug

found = detector_by_slug("retry_storm").run(error_run(20, [0, 1, 2, 17, 18, 19]),
                                            DetectorConfig())
# produced: one finding
#   severity 'critical'
#   {'errors': 6, 'window_spans': 20, 'start_seq': 0, 'end_seq': 19, ...}
# expected: two findings, each severity 'warning', errors 3
```

Three things are wrong with the produced finding, in increasing order of how much
they matter:

1. `window_spans: 20` describes a run twice as wide as R20's window.
2. `errors: 6` is a density claim no ten-span window supports. R20 exists to say
   "three failures inside ten consecutive spans is an agent stuck in a retry
   loop"; six failures spread over twenty spans is not that.
3. The severity is escalated from two `warning`s to one `critical`, because
   `len(inside)` counts the errors of both clusters.

Reachability is not theoretical: a randomized differential over 200 000 error
layouts found the merge condition changes the result in 612 of them (0.3%). Two
clusters of failures at the start and end of a long turn is an ordinary shape.

Fix is one character — `start < merged[-1][1]` — which is also the mutation the
suite could not previously see. Test:
`test_detector_firing_boundaries.py::TestRetryStormR20::test_r20_adjacent_but_non_overlapping_windows_are_not_merged`
(strict xfail).

**Caveat worth the PM's attention.** R20's text says "overlapping", which is the
reading above, but a reader could also intend "merge everything a sliding window
touched". I have implemented the literal reading in the test and flagged the
ambiguity as **S10**; if the PM rules the other way, the fix is to delete the
xfail rather than to change the code.

### BUG-2 (medium) — `retry_storm` is quadratic in an agent's span count

`RetryStorm.run` builds its window list with

```python
windows = [
    (start, min(start + WINDOW_SPANS, len(spans)))
    for start in range(max(1, len(spans) - WINDOW_SPANS + 1))
    if sum(1 for position in error_positions if start <= position < start + WINDOW_SPANS)
       >= MIN_ERRORS
]
```

For each of the `N - 9` window starts it scans the whole `error_positions` list,
so an agent whose spans are largely errors costs `O(N²)`. Measured on 3.11:

| error spans | time |
| --- | --- |
| 1 250 | 0.035 s |
| 2 500 | 0.128 s |
| 5 000 | 0.570 s |
| 10 000 | 2.21 s |
| 20 000 | 9.15 s |

Doubling the input quadruples the time. A rate-limited session is exactly this
shape, and 10 000 spans is not a large transcript. A sliding count (add the
entering position, drop the leaving one) makes it linear.

Repro: `test_detector_adversarial.py::TestScaleR13::test_r20_retry_storm_does_not_grow_quadratically`
(strict xfail; asserts that 4× the spans costs less than 8× the time — quadratic
gives ~16×, linear ~4×).

### BUG-3 (medium) — `blocked_agent` is quadratic in spans × gaps

`_other_intervals` is built once per agent, but `covered_millis` re-clips and
re-sorts that whole list for *every gap the agent has*. Two agents alternating
with a gap after each of their spans:

| spans | time |
| --- | --- |
| 1 250 | 0.150 s |
| 2 500 | 0.528 s |
| 5 000 | 2.16 s |
| 10 000 | 8.30 s |

Again a factor of four per doubling. A long orchestrator/subagent session with
slow turns is exactly this shape. Sorting the other-agent intervals once per agent
and walking them with a cursor as the gaps advance (the gaps are already in
ascending order) makes it near-linear.

Repro: `test_detector_adversarial.py::TestScaleR13::test_r23_blocked_agent_does_not_grow_quadratically`
(strict xfail, same 8× ratio bound). The remaining five detectors are checked
against the same bound and pass it, so the guard is not vacuous.

*Note on the timing xfails.* Both compare a run against a 4× larger run rather
than against an absolute wall-clock number, so a slow or loaded runner scales both
measurements together. The observed ratios are ~16–17× against a threshold of 8×,
and a fix moves them to ~4×; the margin either side is large.

## Adjudication of the coder's flagged ambiguities

### A-b3 — R19 says both "at most one finding per agent" and "multiple loops per agent"

**I believe the coder's reading, and the contradiction is real.** R19 contains
both "At most one finding per agent" and "the search then resumes at index
`i + k*p` so a long trace can yield multiple non-overlapping loops per agent" in
consecutive sentences. They cannot both be requirements.

The resume clause is the operative one, for two reasons. First, it is *mechanical*
— it names an index and an arithmetic expression — while "at most one finding per
agent" is a bare count; when a pinned requirement contains one sentence describing
a mechanism and one describing an outcome that the mechanism contradicts, the
mechanism is what someone thought about. Second, the outcome reading makes the
resume clause dead code: if only one finding per agent may be emitted, there is
nothing for the search to resume *for*.

The charitable reading of the first sentence — and the one the coder gives — is
"not one finding per matching start index", which is a real trap: `A A A A A A`
matches at starts 0, 1, 2 and 3, and a naive implementation reports four findings
for one loop. That behaviour is now pinned
(`test_r19_one_finding_per_loop_not_one_per_matching_start_index`), as is the
multiple-loops behaviour (`test_r19_two_non_overlapping_loops_in_one_agent_are_two_findings`),
so whichever way the PM rules, a test has to change deliberately. Flagged as
**S7**.

### A-b7 — R22's `orphan_result` cannot name an agent

**The coder is right, and the implementation is the best available answer.** I
verified the premise rather than taking it: R12 emits one span per `tool_use`
block, so a `tool_result` whose `tool_use_id` matches no `tool_use` produces no
span at all, and a user record consisting only of tool results produces none
either. The only surviving evidence in the normalized model is R10's aggregated
`orphan_tool_result` warning, which by R10's own design carries a count and
neither an agent nor a `seq`.

R22 asks for a finding per `(agent_id, reason)`. The model cannot supply the agent
half. The three available answers are: invent one (guess an agent), drop the arm
(lose the signal), or emit it trace-scoped and say so in the data. The third is
the only one that does not lie, and `agent_ids = ()` with `span_seqs = ()` is a
reader-visible statement that attribution was unavailable rather than zero.

I disagree only with the PR's framing of the alternatives. The coder offers
"a model field, which is an R2 amendment and a schema version bump" *or* "say in
R22 that `orphan_result` is trace-scoped". The second is not merely the cheaper
option, it is the more honest one: an orphan result is by definition a record
whose owning call is *not in the trace*, so any agent attributed to it would be
the agent of the record that carried the result, not the agent that made the call
— which is a different and misleading fact. **Amend R22**, do not amend R2.
Flagged as **S8**. Pinned by
`test_r22_orphan_result_is_trace_scoped_with_no_agent`.

### A-b8 — R24's population floor as one gate or one per dimension

**The coder's reading is the literal one and I believe it is also wrong, but it is
the PM's call, not the coder's.** R24 says "Population = all `model_call` spans
with non-null `usage`. Skips entirely when the population size is `< 8`", and then
defines the duration value "over spans with both endpoints". Read literally that
is one gate, and the coder implemented it that way.

The consequence is concrete and I reproduced it: a trace with eight model calls
carrying usage, only three of which have timing, produces a `duration` finding
whose own summary reads *"…against a median of 100 and a MAD of 0 over 3 calls"*.
The floor of 8 exists because, in R24's own words, "below that, 'the median' is a
description of three or four numbers and calling one of them an outlier is noise".
The single gate lets exactly that noise through on the duration dimension while
forbidding it on the tokens dimension, from the same trace.

So: the implementation matches the text, the text does not match the requirement's
own stated rationale, and the fix is one line plus one fixture as the coder says.
I have pinned the current behaviour
(`test_r24_the_population_floor_is_one_gate_not_one_per_dimension`) so a ruling
has to change a test rather than slide through. Flagged as **S9**.

### A-b13 — the corpus reproduces increment 1's S1 `unknown_extra_key` noise

**Agreed, with one addition.** The fixtures carry the per-record keys A1 documents
(`cwd`, `version`, `gitBranch`, `userType`), so each emits `unknown_extra_key`
warnings in the tens; the expectation files pin the counts. That is R4 behaving
exactly as written, it is evidence for the reviewer's S1 ruling rather than a
fixture defect, and pinning the counts means the amendment will be visible as a
corpus diff. I would have made the same choice.

The addition: this is now the *second* place the S1 amendment will have to be
reflected — `test_r4_the_known_ignored_set_is_exactly_the_pinned_twelve` from
increment 1, and 26 expectation files here. That is not an argument against the
pinning; it is an argument for the PM landing the S1 amendment before increment 3
adds a third place, since the cost of the amendment grows with every increment
that pins the pre-amendment counts.

## Spec flags

Continuing from the increment-1 review's S6.

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S7** | R19 | The requirement contains two contradictory sentences: "At most one finding per agent" and "the search then resumes at index `i + k*p` so a long trace can yield multiple non-overlapping loops per agent". The second makes the first false; the first makes the second dead. (A-b3.) | Delete "At most one finding per agent" and replace it with what it was reaching for: "one finding per detected loop, never one per matching start index". |
| **S8** | R22 / R10 | `orphan_result` is specified per `(agent_id, reason)`, but the normalized model has no agent for it — R12 emits no span for an orphan and R10's warning carries no agent. The requirement asks for a fact the model does not contain. (A-b7.) | Amend R22 to say `orphan_result` is trace-scoped, with empty `agent_ids` and `span_seqs`. Do **not** add a model field: an orphan's owning call is by definition absent, so any attributed agent would name the wrong one. |
| **S9** | R24 | The population floor of 8 is stated once over "model_call spans with non-null usage", while the `duration` value is defined only "over spans with both endpoints". A duration outlier can therefore be declared against a measured population of three, which is the exact noise the floor of 8 exists to suppress. (A-b8.) | Say explicitly whether the floor applies per dimension. If it does — which R24's own rationale argues for — one line and one fixture. |
| **S10** | R20 | "Overlapping windows are merged into one finding covering the maximal run" does not say whether *adjacent but disjoint* half-open windows merge, and does not say whether `errors` and `window_spans` may then describe a run wider than the ten-span window. The implementation merges them (BUG-1). | State that only windows sharing at least one span merge, and that `window_spans` is the trimmed run. If the intent is the other reading, say that a merged run may exceed the window and that `errors` is then not a within-window density. |
| **S11** | R20 | R20 specifies "a sliding window of 10 consecutive spans", which for an agent with fewer than ten spans is no window at all — making the detector structurally unable to fire on a short agent (five spans, four of them errors, silently clean). The coder's A-b4 treats a short agent's whole span list as the single window; that is a behaviour change the spec does not authorize. | Add one sentence: when an agent has fewer than ten spans, its whole span list is the window. |
| **S12** | R23 | Gaps are measured "for each pair of spans consecutive in that agent's `span_seqs` where both `prev.end` and `next.start` are non-null". A single untimed span between two timed ones therefore hides the stall around it entirely, because neither consecutive pair qualifies. A `user_message` span has no timing by construction. | Say whether the pairing should look *through* spans with no timing to the nearest timed neighbours. As written, a stall next to any user turn is invisible. |
| **S13** | R16 / R33 / R51 | R16's tool-name guard is a *shape* check, so a credential-shaped string that happens to be a legal tool name passes it verbatim into `metrics.tool_name`. `AKIAIOSFODNN7EXAMPLE` and `sk-ant-api03-…` both match `^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$` exactly. R51 promises that credential-shaped payloads "do not appear at all" in the rendered report. | Not a defect in this increment — R16 is doing what it says. But increment 4's redaction must run over `metrics` string values and not only over `previews`, or R51's promise is false for the findings table. State that in R33. Pinned here by `test_r16_a_payload_that_is_a_legal_tool_name_is_carried_verbatim`. |

## Observations (not defects)

1. **The R13 sort key's `detector` component is currently redundant.** Every
   `finding_id` begins with its own slug (R14 enforces it), and no registered slug
   is a prefix of another, so `(rank, finding_id)` and `(rank, slug, finding_id)`
   give the same order. It stops being redundant the first time a v2 detector is
   named as an extension of an existing one (`retry` beside `retry2`), because
   `:` sorts after every digit. Pinned on `sort_findings` directly rather than on
   today's registry.
2. **R22's carve-out and the mapper's `dangling_tool_use` cannot drift.** I
   checked the other half of the coder's claim: `mapper._count_dangling` takes the
   last span *per agent* and requires `kind == "tool_call" and
   tool_result_status == "missing"` — character for character the detector's
   condition. A two-agent truncated capture is counted twice and fires nothing,
   with no span falling between the two rules.
3. **`retry_storm`'s waste excludes the model call that preceded the first
   error**, because A-b5 trims the run to its errors. That is right — the call
   that led up to a storm is not part of it — but it is a consequence of A-b5 that
   the PR does not state, and it changes a dollar figure in increment 3. Pinned by
   `test_r20_waste_is_every_model_call_in_the_merged_run`.
4. **`agent_loop` on a degenerate trace behaves well.** 500 identical model calls
   produce one finding with `repeats: 500`, not 498 findings and not a quadratic
   search; the resume floor jumps past the whole run in one step.
5. **The mutation harness needs a git-level restore, not an in-memory one.** Mine
   restored the original source in a `finally`, which is correct until the process
   is killed between the write and the restore — at which point the *next* run
   reads a mutated file as its baseline and the damage compounds silently. This
   happened to me once. `git checkout -- swarm_observer/` in the `finally` is the
   version worth keeping; recorded because "the check never ran" is this project's
   theme and "the check ran against the wrong source" is the same defect wearing a
   different coat.

## What was deliberately not tested

* **R26–R31 (cost), R32–R37 (report), R38–R40 (`analyze`), R41–R43 (narrator).**
  Not in this increment. `Finding.wasted_cost_usd` is asserted to be `None` and to
  accept a `Decimal`, which is the whole of its increment-2 surface.
* **The rendered-report half of R16.** Keeping trace bytes out of `summary` and
  `metrics` is tested here; whether the renderer escapes what does reach it is
  increment 4's R32/R34/R51.
* **The `--no-previews` mode end to end.** R22's degradation under it (A-b9) is
  pinned at the detector level by feeding a blank result preview; the mode itself
  is the ingest layer's and was tested in increment 1.
* **The coder's own test infrastructure was not modified.** `tests/detector_corpus.py`,
  `tests/test_detector_coverage.py`, `tests/test_detector_corpus.py`, the fixture
  corpus and the expectation files are app code for my purposes. Where they have
  gaps I reported them (the mutation table above) and closed them with my own
  cases rather than by editing a fixture.
