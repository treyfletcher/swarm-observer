# Test report: swarm-observer increment 3 (`feature/so-i3`)

Tester: tester-agent · Spec: `docs/specs/swarm-observer-v1.md` (APPROVED) ·
PR write-up under test: `docs/prs/feature-so-i3.md` ·
Prior rulings honoured: `docs/reviews/feature-so-i2.md` (mutation-methodology
adjudication, amendments 1 and 2)

Scope tested: **R26–R31**, **R33**, the JSON half of **R36**, **R38–R40**, the
increment-3 half of **R47**, and **AC7**. The HTML renderer (R32, R34, R35,
R37), the narrator (R41–R43), R46's socket test and R51's probe do not exist in
this increment; their absence is not reported as a failure.

## Result

| | |
| --- | --- |
| Suite before / after | 1344 / **2035 passed, 12 xfailed, 0 skipped, 0 failed** |
| Python 3.11.15 | 2035 passed, 12 xfailed |
| Python 3.12.3 | 2035 passed, 12 xfailed — identical counts, identical set |
| New test modules | 5 (`test_rate_snapshot.py`, `test_cost_engine.py`, `test_redaction.py`, `test_json_report.py`, `test_cli_analyze.py`) plus the R50 canary `canaries/test_canary_redaction_pattern_removed.py` |
| `ruff check` · `ruff format --check` · `mypy --strict` | clean on both interpreters |
| Mutation sweep | **231 mutants in two independently-designed waves, run on both interpreters. 228 killed. 3 survive: two proven equivalent and one declared control arm that must survive.** |
| Bugs found | **7**, all pinned by a strict xfail plus a live reproduction |
| Spec flags raised | **S20–S23** (continuing from the coder's S19) |
| `tests/allowed_skips.txt` | still empty, as required |
| `tests/traceability_pending.txt` | R26–R31, R33, R36, R40 and R47 removed; R32, R34, R35, R37, R41–R43, R46 and R51 stay ledgered |
| Collection floors | every module at its exact current count |

The 12 xfails are **strict**. Each is one of the seven bugs below expressed as
the test that should pass, so the suite goes red the moment a defect is fixed
and no fix can land without this ledger being updated. Each sits beside a
non-xfail test that pins the defect *as it behaves today* and carries the
message `BUG-n appears to be fixed: delete this test and de-xfail the one
above`, so a fix cannot be half-landed either.

---

## 1. Requirement × test coverage

| Req | What it pins | Where it is tested | Strength |
| --- | --- | --- | --- |
| **R26** | rate seam, `Decimal` on load, JSON number refused by type | `test_rate_snapshot.py::TestParseRateR26`, `TestSnapshotMetaFieldConstraintsR26`, `TestBundledSnapshotR26` | Full. `parse_rate` driven over the accepted grammar, floats/ints/bools/`None`/containers refused by type, `1E3`/`NaN`/`Infinity`/`0x10`/leading-zero refused by shape, and the `fullmatch`-not-`match` trailing-newline trap pinned explicitly. Snapshot provenance shapes (`as_of`, `version`) driven both ways. |
| **R27** | four-rung resolution ladder | `test_rate_snapshot.py::TestResolveModelKeyR27` | Full, including rung 3. `claude-opus-4-1-20250805` resolves to `claude-opus-4-1` and not `claude-opus-4`, and the assertion is made on the resulting **price** as well as the key, so longest-wins is non-cosmetic. `claude-opus-45-preview` → `None` pins the `+ "-"` separator. An alias shadowing a longer prefix pins the rung *order*. Empty string, a recorded id that is a prefix of a key, hostile ids (NUL, newline, RTL, 5 000 chars) all resolve to `None` without raising. |
| **R28** | the five-term formula ÷ 1e6 | `test_cost_engine.py::TestPriceUsageR28` | Full. Every expected figure is computed by a `fractions.Fraction` oracle that shares no arithmetic, no number type and no rounding mechanism with the code under test. Each term driven alone; a component with no rate and a component with zero tokens driven as the two halves of one condition. |
| **R29** | `Decimal` discipline, the one allowed rounding, the `Inexact` trap | `test_cost_engine.py::TestQuantizationR29`, `TestInexactTrapR29`, `TestSumUsdR29` | Full, **both arms**. The trap fires at 61 significant digits and does **not** fire at 60, at 10¹², on every rate in the shipped snapshot, or on a large summed total. `COST_PRECISION` is asserted as the literal `60` so the boundary tests cannot adapt to a moved constant. `ZERO_USD`'s exponent is asserted through `str`, not `==`. |
| **R30** | four unpriced reasons **in priority order** | `test_cost_engine.py::TestUnpricedTaxonomyR30`, `TestRateKeyMissingR30` | Full. Every adjacent rung pair is driven by a span that satisfies **both**, asserting *which* reason is reported: 1 beats 2 (api-error with `usage=None`), 1 beats 3 (`<synthetic>` also absent from the snapshot), 1 beats 4, 2 beats 3, 3 beats 4. The `rate_key_missing` carve-out is driven on both halves of `tokens != 0 and price_key not in rates`: a snapshot missing `cache_write_1h` prices a normal call and refuses one with a single 1h token; a rate of literal zero counts as present. |
| **R31** | four groupings, rows sum to totals | `test_cost_engine.py::TestGroupingsR31`, `TestWasteCostR31`, `TestWaveTwoGapsR30R31`, `TestCorpusInvariantsR31` | Full. Sums checked over all 26 fixtures as an invariant, not as a literal. Agents with no model call, agents known only from an *unpriced* span, undeclared agents (two of them, so `len + offset` is distinguishable from `len − offset`), per-agent unpriced counts that differ from the trace's. `by_detector` sorted over six slugs. |
| **R33** | ordered pattern table, idempotence | `test_redaction.py` (150 tests), `test_json_report.py::TestRedactionAtTheBoundaryR33`, canary `test_canary_redaction_pattern_removed.py` | Full for the JSON boundary. Every pattern transcribed against the requirement text; ordering pinned by the two cases that can tell (`sk-ant-…` must not be chopped by `openai_key`; a JWT inside a `Bearer` header marked once). Idempotence driven as a **property over 40 000 generated strings** from the assignment grammar, with the input space stated. **BUG-3** below is the result. The HTML boundary is increment 4's. |
| **R36** (JSON) | document shape, `sort_keys`, `ensure_ascii`, `indent=2`, trailing newline, Decimals as 6-decimal strings | `test_json_report.py` (119 tests) | Full for the JSON half. Section presence and shape; every `Decimal` as a string; the format version pinned as a literal and distinguished from `TRACE_SCHEMA_VERSION`; `meta.counts` driven against a trace that actually has warnings; a warnings row's `count` driven at 3 and 2 so a constant `1` is visible. |
| **R38** | three subcommands, expansion, flags, `--no-previews` | `test_cli_analyze.py` (104 tests) | Full. Directory expansion non-recursive and sorted by basename, shown not to be `iterdir` order. `--no-previews` proved to reach **both** the adapter and the renderer — the two now produce identical bytes, so the wiring is the only observable form of the ingest half and it is asserted directly. |
| **R39** | the four exit codes | `test_cli_analyze.py::TestExitCodesR39`, `TestNulInTheOutputPathR39` | Substantial, with two holes reported as **BUG-1** and **BUG-7**. Codes asserted as the literals `0/1/2/3`, not read back from `EXIT_*`. Fail-closed paths write nothing and truncate nothing. Usage errors are argparse-style, one terminated line, no traceback. |
| **R40** | one stdout line, severity counts | `test_cli_analyze.py::TestSummaryLineR40`, `TestSummaryLineSanitationR40`; `test_json_report.py::TestSeverityCountsR40` | Substantial, with one hole reported as **BUG-6**. Counts present at zero, per severity not per detector; paths named as given, never resolved. |
| **R47** | determinism of `report.json` and stdout | `test_json_report.py::TestDeterminismR47`, `test_cli_analyze.py::TestDeterminismR47` | Cited for the artifacts that exist. In-process repeats, real subprocesses, `PYTHONHASHSEED` × `TZ` × `LC_ALL`, two working directories, reversed path order; the no-float, no-clock and no-absolute-path clauses asserted against the bytes. **`report.html`, the SVG timeline and their goldens are increment 4's.** |
| **AC7** | the five-span acceptance case | `test_cost_engine.py::TestAcceptanceCriterionSeven` | Full, and every figure is recomputed from the snapshot by the `Fraction` oracle rather than pasted from the coder's hand-run output. |

### Requirements deliberately *not* covered

`R32`, `R34`, `R35`, `R37`, `R41`–`R43`, `R46` and `R51` remain in
`tests/traceability_pending.txt` because the code they govern does not exist in
this increment. `R33`, `R36` and `R47` are cited for their increment-3 halves
only; the ledger's comment records which half, so increment 4's tester does not
inherit a false "done".

---

## 2. Bugs

Numbering continues the sequence already in the test files. Severity is my
assessment; **none of the seven is a regression introduced by this branch's
code** except BUG-6 and BUG-7, which are new because the CLI is new.

### BUG-1 — a valid in-limits trace raises a raw `decimal.InvalidOperation` past `main()`
**Severity: high.** Wrong exit code on a real input, with a traceback.

* **Failing input.** A `model_call` span with `input_tokens = 10**60` and every
  other component zero, on a model the snapshot prices.
* **Observed.** `quantize_cost` sits *outside* `price_usage`'s
  `DecimalException` guard. With a single non-zero component the multiply and
  the divide stay exact, so no `Inexact` is signalled; the quantize to six
  places then needs 61 digits against a 60-digit context and raises
  `decimal.InvalidOperation` from outside every `except` in `cli/main.main`.
  The console script prints a traceback and exits **1**.
* **Expected.** `CostError("cost_precision_exceeded")`, one sanitized line on
  stderr, exit **2**, no output file written.
* **Spec clause.** R11 ("fail-closed … one sanitized line on stderr"), R29
  ("every arithmetic step runs in a context that traps"), R39 (`2` is
  fail-closed; `1` means *a finding met the threshold*, so a wrapper reading the
  exit code is told the run succeeded).
* **Pinned by.** `test_cost_engine.py::TestInexactTrapR29::test_r29_an_oversized_but_exact_token_count_is_a_cost_error_not_a_crash` (xfail) and `…::test_r29_bug1_reproduces_as_a_raw_decimal_signal_today`; end-to-end at `test_cli_analyze.py::…::test_r39_the_same_trace_with_one_component_also_exits_two` (xfail) and `…::test_r39_bug1_reproduces_as_an_uncaught_exception_from_main`.
* **Note.** The two-component form of the same trace *is* handled correctly
  (exit 2, one line). Only the single-component form escapes, which is why the
  coder's hand-run guard check reported the trap firing.

### BUG-2 — trace-derived agent ids and warning details reach `report.json` neither redacted nor blanked
**Severity: high.** Defeats `--no-previews` and R51's promise.

* **Failing input.** A record whose `agentId` is `AKIAIOSFODNN7EXAMPLE` — which
  matches R2's agent-id alphabet exactly, so the mapper keeps it verbatim.
* **Observed.** The string reaches `report.json` five times
  (`spans[].agent_id`, `agents[].agent_id`, `agents[].parent_agent_id`,
  `cost.by_agent[].agent_id`, `cost.spans[].agent_id`) in **both** modes,
  including `--no-previews`. `ParseWarning.detail` carries a trace-derived
  record-type slug the same way.
* **Expected.** Either redaction at the render boundary like every other
  trace-derived string (R33), or blanking under the flag (R38/A-c6).
* **Spec clause.** R33 ("every trace-derived string is redacted before it
  reaches an output document"), R38 (`--no-previews` "omits trace free text
  entirely"), R51 (no payload appears anywhere).
* **Pinned by.** `test_json_report.py::TestNoPreviewsGuardR38::test_r38_no_trace_derived_string_survives_the_flag` (xfail), with two live reproductions beside it. The checked-in `LEAKING_PATHS` set makes the leak a *set comparison*: a new trace-derived field escaping the guard fails the suite, and a fix must shrink the list in the same commit.
* **Note.** The coder's A-c6 guard is real and works — every field it covers is
  clean. These are the fields it does not cover, and the coder's own S16 does
  not name them.

### BUG-3 — `redact` is not idempotent; it deletes report text
**Severity: medium.** Silent data loss in a security-relevant function.

* **Failing input.** `redact(redact('TOKEN="abcdefg"X'))`.
* **Observed.** The first pass yields
  `TOKEN=[redacted:secret_assignment]X`. On the second pass
  `secret_assignment`'s bare `\S{6,}` value alternative is greedy and
  unanchored, so it swallows the marker **and the trailing `X`**, yielding
  `TOKEN=[redacted:secret_assignment]`. A character of report text is deleted
  with no trace. `redact("PASSWORD='secret'AKIAIOSFODNN7EXAMPLE")` shows the
  worse form: a second pass erases an *earlier marker*, destroying evidence that
  a credential was there.
* **Expected.** `redact(redact(s)) == redact(s)` for all `s`.
* **Spec clause.** R33 (idempotence, pinned).
* **Pinned by.** `test_redaction.py::…::test_r33_a_generated_corpus_is_idempotent` (xfail, 40 000 generated strings) plus two minimized live reproductions. The generator's input space is stated in the docstring, and a separate test asserts the generator **can** produce a distinguishing input, so the property test is not vacuous.
* **Note.** This is exactly the case the coder's PR flagged as still owed
  ("`secret_assignment` re-matches its own output"). It does — and the
  re-match is not benign.

### BUG-4 — `DetectorWaste.wasted` contradicts the findings it aggregates
**Severity: medium.** Two numbers in one document disagree, with nothing saying why.

* **Failing input.** One finding attributing two model calls — one priced, one
  on a model the snapshot cannot price — with 3 000 and 4 000 tokens.
* **Observed.** `Finding.wasted` reports 7 000 tokens (R17's definition: the
  element-wise sum over the distinct redundant model calls, with no reference
  to whether a rate exists). The per-detector row reports **2 000**, because
  `DetectorWaste.wasted` sums only the *priced* attributed spans. The two
  numbers sit in the same report and nothing marks the second as a subset.
* **Expected.** Either the same total as the findings it aggregates, or a
  companion field naming the restriction.
* **Spec clause.** R17 (`wasted` is over the attributed model calls), R31
  (per-detector aggregation).
* **Pinned by.** `test_cost_engine.py::TestWasteCostR31::test_r31_by_detector_wasted_tokens_equal_the_findings_it_aggregates` (xfail) and `…::test_r31_bug4_reproduces_as_a_priced_only_token_column_today`.
* **Note.** `AgentCost` has exactly this restriction and documents it with
  `unpriced_usage` beside it. `DetectorWaste` has neither, which is what makes
  this an inconsistency rather than a choice.

### BUG-5 — `analyze --json <fifo|socket|device>` destroys the special file and reports success
**Severity: high.** Destroys a file outside the tool's remit, silently.

* **Failing input.** `analyze clean.jsonl --json /dev/null` — the natural way to
  ask a CI job for the exit code alone.
* **Observed.** `check_output_path` rejects only a *directory*, and
  `atomic_write_texts` renames over its destination. `/dev/null` is replaced by
  a regular file containing a JSON report, for the whole machine, and the run
  prints `wrote /dev/null; findings: …` and exits 0. Found by writing that test
  and having to repair the container afterwards; the checked-in reproduction
  uses a FIFO in `tmp_path` instead.
* **Expected.** Exit 3, nothing written — the same treatment the directory case
  already gets.
* **Spec clause.** R11 (a failure leaves the filesystem as it found it), R39
  (an unwritable output path is a usage error).
* **Pinned by.** `test_cli_analyze.py::…::test_r39_an_output_path_that_is_not_a_regular_file_is_refused` (xfail) and `…::test_r39_bug5_reproduces_as_a_destroyed_fifo`.

### BUG-6 — `analyze`'s stdout is not control-safe, and a newline in the output path makes it two lines
**Severity: low–medium.** Breaks a pinned contract; misleads a wrapper script.

* **Failing input.**
  `analyze clean.jsonl --json $'b\nwrote nothing; findings: critical=0 warning=0 info=0.json'`,
  and separately any path containing `ESC`, `BEL`, `CR` or `VT`.
* **Observed.** `summary_line` does **not** pass the written paths through
  `cli/main._one_line_safe`. Every `UsageError` and every fail-closed line on
  *stderr* does. So the success line — the one a CI job parses and a human reads
  — is the single output in this package that carries a raw path through.
  `stdout` becomes two lines, and the second one is chosen above to read as a
  summary line of its own: a wrapper reading the *last* line of stdout is told
  the run wrote nothing.
* **Expected.** One line, and no C0 control character on stdout.
* **Spec clause.** R40 ("writes nothing to stdout on success **except one
  line** naming the written paths"), R11 (the sanitation discipline
  `_one_line_safe` exists for).
* **Pinned by.** `test_cli_analyze.py::TestSummaryLineSanitationR40` — two xfails, two live reproductions, plus a premise test asserting `_one_line_safe` exists and is already applied on stderr, so the report cannot be answered with "there is no sanitizer".
* **Note.** The path is one the user typed, which is what keeps this out of
  "injection" territory. Filenames the user did **not** type — the ones
  directory expansion discovers — reach `meta.source_files[].name` in
  `report.json` instead, where JSON escaping currently contains them. See S21.

### BUG-7 — a NUL byte in `--json` escapes `main()` as a bare `ValueError`
**Severity: high.** Wrong exit code, with a traceback, from a one-byte input.

* **Failing input.** `analyze clean.jsonl --json "$TMP/r"$'\0'"x.json"`.
* **Observed.** `check_output_path` calls `Path(raw).parent.is_dir()`; `os.stat`
  raises `ValueError("embedded null byte")` before any typed error can be
  raised, and `main` — documented as "the only place a typed error becomes a
  process outcome … no traceback ever reaches stderr" — has no clause for it.
  The console script exits **1** with a traceback. The input side has the
  identical hole in `expand_inputs`.
* **Expected.** Exit 3, one line — `check_output_path`'s own docstring says "an
  output whose directory does not exist **or cannot be written** is exit 3",
  which is precisely this case.
* **Spec clause.** R39 (exit 3 for an unwritable output; `1` means *a finding
  met the threshold*), R11 (every unreadable input is a sanitized exit 2).
* **Pinned by.** `test_cli_analyze.py::TestNulInTheOutputPathR39` — one xfail, one live reproduction, and a *separate* test for the input-path hole because a fix to `check_output_path` alone leaves it open.
* **Note.** This is BUG-1's shape — a raw exception past `main` — reached
  without a 60-digit token count. Two independent inputs now produce it, which
  argues the fix belongs in `main`'s `except` clauses and not only at each call
  site.

### Bugs described but **not** pinned

None. All seven carry a strict xfail and a live reproduction.

---

## 3. Spec flags

Continuing from the coder's **S19**. None blocks this branch.

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S20** | R39 / R40 | R39 says a usage error is an "argparse-style message on stderr" and R11 says a fail-closed error is "one sanitized line". Neither says what the *success* line's sanitation rule is, and R40 pins its shape ("one line") without saying what happens when a path makes that impossible. BUG-6 lives in that gap: the code has a sanitizer, applies it to both error paths, and the success path was never given a rule to follow. | One clause on R40: the written paths are rendered with C0 control characters replaced by a space and any newline removed, and the line remains exactly one line for any path the OS accepts. This is `_one_line_safe` plus a newline case, and it is the rule the error paths already follow. |
| **S21** | R33 / R36 / R47 | `meta.source_files[].name` is **user- and filesystem-supplied, not trace-derived**, so nothing in R33's or A10's scope covers it, and it is emitted verbatim. Today JSON escaping contains it. Increment 4's HTML renderer will read the same field, and the natural reading — "R33 covers trace-derived strings; a filename is not one" — puts an attacker-controlled string into HTML unredacted and possibly unescaped. Verified reachable: a file named `a<ESC>[31mEVIL.jsonl` sitting in a scanned directory reaches `source_files[].name` intact. | State that `source_files[].name` is untrusted input and is subject to R32's escaping and R33's redaction like trace text, *and* that only the basename is emitted (R47 already forbids the directory part). Cheaper to settle now than after the renderer exists. |
| **S22** | R17 / R29 / R31 | R17 defines a finding's `wasted` over the attributed model calls with no rate precondition; R29 defines `wasted_cost_usd` over already-priced spans. `_by_detector` has to aggregate both and currently silently applies the second definition to the first quantity — BUG-4. The requirement does not say which reading wins for the per-detector row. | Say that a per-detector `wasted` is the sum of its findings' `wasted` (R17's quantity), and that the row carries `wasted_unpriced` beside it — the shape `AgentCost` already uses. This is the reading that makes the two numbers in one document agree. |
| **S23** | R49 | The collection floor for `tests/test_boundaries_and_posture.py` is derived from the **product's** module count: its R44 test is parametrized over every module under `swarm_observer/`. Increment 3 added seven modules, so the module collected 43 against a floor of 36 — 16% slack in the tripwire, invisible because adding tests never breaks a floor. This will recur every increment. | Either exempt parametrized-over-the-product modules from the floor and give them their own "one case per module" assertion, or make the floor check *also* warn when a module exceeds its floor by more than a stated margin. Left as a flag, not fixed here: R49's file is a pinned artefact and the choice is the PM's. (The floor is raised to 43 in this branch.) |

---

## 4. The mutation sweep

The increment-2 ruling is that **a self-chosen mutation set is a check that
cannot fail**. Two things were done about that here.

**First, the coder chose nothing.** The PR write-up states plainly that no
sweep was run and no score is reported, and instead names seven places a
one-character change would produce a plausible wrong number. That is the right
call and it is why this sweep is independent of the code's author rather than a
re-derivation of it.

**Second, the set was built in two waves, and the second wave was designed
*after* the first wave's kills existed** — deliberately over anchors wave 1
never touched. A set assembled to be killed proves nothing; a set assembled to
reach untouched code can fail, and this one did: wave 2 found 17 survivors on
its first run in modules wave 1 had already swept.

The full set, with per-mutant verdicts, is checked in at `tests/mutations.json`
(231 entries) so the next sweep is a re-run rather than a re-invention and a
shrinking survivor list is visible in a diff.

### Operators

Declared in `tests/mutations.json`:
relational flip (`<`↔`<=`, `>`↔`>=`, `==`↔`!=`); integer constant ±1; boolean
connective swap (`and`↔`or`); guard-clause drop (`if X and Y` → `if X`);
negation drop or insertion (`not X` → `X`, `is None` → `is not None`);
membership flip (`in` → `not in`); slice/range bound off-by-one; sort- and
group-key component drop; normalization drop (`sorted()`, `set()`, `tuple()`,
`dict()`); rung/branch reorder (the R27 ladder, the R30 priority order);
call-argument swap; literal substitution (string constants, rounding modes,
quanta); control-flow swap (`continue`↔`break`, early-return removal);
container-default swap; quantifier swap (`any`↔`all`); plus one declared
**control-no-op**.

### Modules and counts

| Module | Wave 1 | Wave 2 | Total |
| --- | ---: | ---: | ---: |
| `swarm_observer/cost/compute.py` | 34 | 12 | 46 |
| `swarm_observer/report/json_out.py` | 22 | 16 | 38 |
| `swarm_observer/cli/main.py` | 24 | 14 | 38 |
| `swarm_observer/cost/source.py` | 18 | 8 | 26 |
| `swarm_observer/detect/base.py` | 12 | 12 | 24 |
| `swarm_observer/report/redact.py` | 10 | 8 | 18 |
| `swarm_observer/cost/snapshot.py` | 12 | 4 | 16 |
| `swarm_observer/detect/registry.py` | 8 | 1 | 9 |
| `swarm_observer/detect/repeated_tool_call.py` | 4 | 2 | 6 |
| `swarm_observer/detect/retry_storm.py` | 4 | 2 | 6 |
| `swarm_observer/detect/agent_loop.py` | 4 | 0 | 4 |
| **Total** | **152** | **79** | **231** |

Every module increment 3 touched is covered, each above amendment 1's
per-module floor of 8 except the three single-detector modules, whose whole
surface is smaller than eight distinct mutable decisions.

### Harness discipline

`__pycache__` purged before and after every mutation (a same-length edit written
inside one second otherwise leaves a `.pyc` whose `(mtime, size)` is unchanged);
`PYTHONDONTWRITEBYTECODE=1` in every child; the original text saved before the
edit and rewritten in a `finally`; every anchor asserted to occur **exactly
once** before it is applied, so a silently-unapplied mutation is reported as
`NOT-APPLIED` rather than counted as a kill. **Zero mutants were unapplied.**
The oracle is the whole suite with `-x`. Each interpreter sweeps its own
untouched copy of the tree, and each copy's `swarm_observer.__file__` was
verified to resolve inside that copy before the sweep began, and its baseline
verified green.

### Results

| | Wave 1 | Wave 2 | Total |
| --- | ---: | ---: | ---: |
| Mutants | 152 | 79 | 231 |
| **Against the branch as handed over** | 129 killed, **23 survived** | **17 survived** (see note) | — |
| **After the tests in this report** | 151 killed, **1 survived** | 77 killed, **2 survived** | **228 killed, 3 survived** |

Identical verdicts on CPython 3.11.15 and 3.12.3 (one exception, resolved
below).

**How these numbers were obtained — stated exactly, because this is the figure
this team has been burned by.** Three measurements, not one:

1. **Wave 1, full run, both interpreters, all 152 mutants.** 129 killed, 23
   survived, the same 23 on each.
2. **Wave 2, full run, both interpreters, all 79 mutants**, against the suite as
   it stood after wave 1's kills had already landed. 62 killed / 17 survived on
   3.11; 63 / 16 on 3.12 — the single discrepancy is `W-J01`, resolved below.
3. **Survivor recheck at the final commit**, serially: all 23 wave-1 survivors
   and all 17 wave-2 survivors re-applied against the finished suite. 22 of 23
   and 15 of 17 are now killed.

The run logs live in the tester's scratchpad and do **not** survive the session.
The durable record is `tests/mutations.json`, which carries all 231 mutants as
`(module, exact old substring, exact replacement, operator, wave, verdict)` —
enough to re-run every one of them from a clean checkout and get the same
answer, which is the point of checking it in. Anchors are asserted unique
before application, so a mutant whose anchor has drifted is reported rather
than silently skipped.

The combined figure rests on (1)+(2) for the kills and (3) for the survivors.
That composition is sound in one direction only, and it is the safe direction:
adding tests can turn a survivor into a kill but can never turn a kill into a
survivor, so no mutant counted as killed in (1) or (2) can have become a
survivor by (3). A full 231-mutant re-run at the final commit was started and is
the obvious confirmation to run before merge; it was not completed inside this
session's budget, and I would rather say that than round it up.

Wave 2's first run, against the tests that already existed after wave 1's kills,
left **17** survivors — sixteen of them real gaps, now closed. That is the
finding that matters most in this section: a module can be swept once,
thoroughly, by an independent set, and still have sixteen unasserted behaviours
in it.

### The three survivors, named

**None of the three is an untested behaviour.** Two are equivalent mutants and
one is a control arm that is *required* to survive. A survivor list of zero
would have been a claim I could not earn, so it is not claimed.

1. **`S11`** — `@lru_cache(maxsize=1)` → `maxsize=2` on `bundled_snapshot`
   (`cost/snapshot.py`). **Equivalent.** The function is nullary, so the cache
   is keyed on nothing and both sizes hold the same single entry forever. No
   test can distinguish them. The *premise* is asserted instead
   (`test_rate_snapshot.py::…::test_r26_the_bundled_snapshot_memo_takes_no_arguments`):
   the signature is empty and `cache_info().currsize` stays 1, so if a
   parameter is ever added the cache size stops being decorative and that test
   is where it surfaces.
2. **`W-C11`** — deleting `compute_costs`'s up-front waste-key validation loop
   (`cost/compute.py`). **Equivalent, verified by running it rather than
   argued.** With the loop gone, every malformed key still raises the same
   `ValueError` with the same message, because `_by_detector` calls
   `_detector_of` on every key anyway. The loop is fail-fast readability, not a
   behavioural guarantee, and its comment ("fail loudly on a key that is not a
   finding id") describes something the code does without it. The nine tests
   that pin the refusal stay, because the refusal is the requirement.
3. **`W-M11-CONTROL`** — `expanded.append(path)` → `expanded.append(Path(path))`
   on a value that is already a `Path` (`cli/main.py`). **The declared control
   arm.** It is a semantic no-op and it *must* survive. It did, on both
   interpreters. This is the evidence that this sweep's 228 "killed" verdicts
   come from the mutations rather than from a harness that reports failure
   regardless — the thing an all-killed sweep can never demonstrate about
   itself.

### The one interpreter discrepancy, and a harness hazard worth inheriting

Wave 2's first run reported `W-J01` (`REPORT_FORMAT_VERSION = "1.0.0"` →
`"1.0.1"`) **survived on 3.11 and killed on 3.12** — the exact shape of this
team's fourth signature defect. Re-run **serially** on each interpreter it
survives on both; it was a false kill.

The cause is that the two sweeps were running concurrently and the suite carries
performance pins that flake under CPU contention. That matters more than the one
mutant: **a flake in this direction under-reports survivors**, which is the
dangerous direction, and an all-killed sweep run concurrently would look exactly
like a good one. `tests/mutations.json` now records "do not run two sweeps
concurrently" as part of the harness contract, and the final sweep whose numbers
appear above was run serially, one interpreter at a time.

### What the 22 wave-1 and 16 wave-2 kills bought, by theme

The survivors were not random. Three patterns account for nearly all of them,
and all three are versions of *the test could not have failed*:

* **A collection of one.** One unknown detector slug, one deferred flag, one
  written path, one warning, one undeclared agent, one attributed span, one
  agent with unpriced spans. With one element, "the first" and "the last" are
  the same object and "sorted" and "insertion order" are the same list. Twelve
  survivors died to building the two-element case.
* **A self-referential assertion.** Exit codes compared against `EXIT_*`;
  a precision boundary built from `COST_PRECISION ± 1`; a "sorted" assertion
  against a snapshot file that is already alphabetical; `Decimal` equality used
  to check an *exponent*. Each moved with the code it was checking. Five
  survivors died to pinning a literal, or to constructing input the code does
  not already normalize.
* **A property that everything downstream re-normalizes.** A detector's
  attribution is re-filtered by `attribute_waste` and again by `_relevant_seqs`;
  a scan order is re-sorted by `sort_findings`; a mapping's insertion order is
  re-sorted by `json.dumps(sort_keys=True)`. Nothing observable moves, so the
  contract is real but only assertable at the function that owns it. Six
  survivors died to asserting the documented contract directly instead of a
  number derived from it.

---

## 5. Interpreter parity

| | 3.11.15 | 3.12.3 |
| --- | --- | --- |
| Full suite | 2035 passed, 12 xfailed | 2035 passed, 12 xfailed |
| `mypy --strict` | clean | clean |
| Mutation sweep (231) | 228 killed, 3 survived | 228 killed, 3 survived |
| Survivor set | `S11`, `W-C11`, `W-M11-CONTROL` | identical |

**Nothing differs.** Both interpreters were verified to import the tree under
test rather than an installed copy before every measurement above. The suite
contains no assertion that depends on a recursion limit, on
`str.isprintable()` or any other Unicode-table-versioned predicate, on float
formatting, or on dict iteration order — `cli/main._one_line_safe` is
specifically written against fixed C0 code points for this reason and a test
pins that choice.

---

## 6. What I did not cover, and why

* **The HTML renderer, the SVG timeline and their goldens** (R32, R34, R35,
  R36's section order and 5 000-row cap, R37). Not built. `R47`'s determinism
  matrix therefore covers `report.json` and stdout only, and
  `traceability_pending.txt` says so in a comment so increment 4's tester does
  not inherit a false "done".
* **The narrator** (R41–R43) and **R46's socket test**. Not built.
* **R51's hostile-corpus probe as a harness.** The hostile fixture is driven
  hard here — every R51 payload asserted absent under `--no-previews` and
  *present* without it, which is the arm that makes the first non-vacuous — but
  R51's own probe covers `report.html` too and is increment 4's.
* **`format_display_usd`.** Tested as a function (both quanta, half-up at the
  boundary), but nothing in the product calls it until increment 4's HTML. I
  did not manufacture a caller.
* **The `.meta.json` sidecar path under `analyze`.** Unchanged from increment 1
  and the corpus has no sidecars by design. Untested here, as in increment 1.
* **Long-context, batch and priority tiers.** Out of scope by R26; the snapshot
  models the standard tier only and every report says so. I confirmed the
  caveat is present in the document, not that the figures are right for those
  tiers — they are not modelled.
* **Whether the shipped rates are correct.** Out of a tester's reach: I can
  check that every rate is a syntactically valid non-negative decimal, that
  every model key is attributed to exactly one source, that a snapshot with a
  dangling alias or an unattributed rate refuses to load, and that AC7's figure
  recomputes from the file. I cannot check that `claude-fable-5-1`'s
  `cache_read` is really `0.25`. **The coder's rate review is still
  outstanding and is the single largest untested risk in this increment** — a
  transcription error there produces a confidently wrong dollar figure that
  every check in this suite would pass.
* **Behaviour on a real transcript.** A5's manual parser check is still owed.
  The cost engine is now the place where a fixture/reality gap converts
  directly into a wrong number, and no synthetic corpus can close it.

---

## 7. What I want the reviewer to look at hardest

1. **BUG-7 and BUG-1 together.** Two unrelated inputs now put a raw exception
   past `main`, which is documented as the one place that cannot happen. I have
   pinned both at their call sites *and* asserted the input-path variant
   separately. If the fix lands only at the call sites, a third input finds a
   third hole. My reading is that `main` needs a final `except Exception` that
   renders one sanitized line and exits 2 — but that is a design call, and a
   catch-all is exactly the kind of guard that then never fires, so it wants a
   test that proves it does.
2. **The three mutation survivors.** I claim two are equivalent. The `W-C11`
   claim I verified by running the mutant and observing the same exception with
   the same message; the `S11` claim rests on the function being nullary, which
   a test asserts. Both are the kind of claim a tester most wants to be wrong
   about, and both are cheap to re-check with the checked-in ledger.
3. **The control arm.** `W-M11-CONTROL` is the only thing in this report that
   demonstrates the sweep can report "not killed" for the right reason. If you
   disagree that it is a genuine no-op, the 228 kills lose their warrant. It is
   one line in `tests/mutations.json`.
4. **`LEAKING_PATHS` in `test_json_report.py`.** It is a checked-in *bug
   report*, not a contract, and it is compared as a set. That makes it the one
   place in this suite where a new leak in increment 4 fails automatically —
   and also the one place where someone could "fix" a red suite by adding a
   line. The comment says so; a reviewer should confirm the comment survives.
5. **S21, before increment 4 starts.** `source_files[].name` is
   attacker-influenced (a filename in a scanned directory), is not
   trace-derived, and is therefore outside every rule the package currently
   applies. JSON escaping is doing the work today. The HTML renderer is the
   moment that stops being true, and it is much cheaper to settle the rule now
   than to find it in increment 4's hostile-corpus probe.
6. **Wave 2's premise.** I designed it after wave 1's kills existed,
   specifically over anchors wave 1 never touched, and it immediately found 17
   survivors in modules already swept. That is evidence that **one** independent
   sweep is not enough, and I would expect a third wave — designed by the
   reviewer, over anchors *neither* wave touched — to find more. The increment-2
   review's own sweep found 9 survivors after my predecessor's 119-mutation
   sweep, and 40 in a module neither had touched. I do not think that pattern
   has been exhausted.
