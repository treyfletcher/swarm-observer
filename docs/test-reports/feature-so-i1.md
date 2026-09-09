# Test report: swarm-observer increment 1 (`feature/so-i1`)

Tester: tester-agent · Spec: `docs/specs/swarm-observer-v1.md` (APPROVED) ·
PR write-up under test: `docs/prs/feature-so-i1.md`

Scope tested: **R1–R12, R44, R45, R49, R50, R52, and R38's `schema` subcommand.**
Detectors (R13–R25), cost (R26–R31), report (R32–R37), narrator (R41–R43) and the
`analyze`/`detectors` subcommands do not exist in this increment; their absence is
not reported as a failure.

## Result

| | |
| --- | --- |
| Tests collected | **541 passed, 9 xfailed, 0 skipped, 0 failed** |
| New test modules | 7 (`tests/test_model_schema.py`, `test_reader_and_limits.py`, `test_record_tolerance.py`, `test_identity_order_previews.py`, `test_collapse_r9.py`, `test_warnings_and_spans.py`, `test_adversarial_inputs.py`, `test_guard_verification.py`) plus `tests/factories.py` |
| Suite before / after | 57 / 550 collected |
| `ruff check` · `ruff format --check` · `mypy --strict` | clean |
| Environments the full suite was run in | ambient; `env -u` with all nine R45 credentials unset; `TZ=Asia/Kolkata`; `LC_ALL=C`; `PYTHONHASHSEED=1`; with and without the opt-in real-transcript corpus |
| `tests/allowed_skips.txt` | still empty, as required |
| `tests/traceability_pending.txt` | shrunk by 13 lines (R1–R12, R38) |

The 9 xfails are **strict** and are the bug reports below expressed as tests: each
fails the suite the moment the defect is fixed, so no fix can land without the
ledger being updated.

## The R9 reconciliation — the headline item

**Verdict: the mapper is correct, the spec's constants are correct, and they
describe the same behaviour. No bug.** The coder's fixture constants and the
spec's real-transcript constants are two different datasets, and both are true —
but that was not sufficient evidence on its own, so the mapper was run over the
real corpus.

Real transcripts were read from
`~/.claude/projects/<slug>/<session-uuid>/subagents/agent-*.jsonl`. **No transcript
data was copied into the repository** (A5); only the measurements below leave the
machine.

The corpus is a *live* session's transcript set — the agents working on this
project are still appending to it — so it has grown since the spec measured it: 13
files / 5,887 records now, against the spec's 11 files / 5,385 records. To compare
like with like, the corpus was replayed at a timestamp cutoff chosen to reproduce
exactly 5,385 records across 11 files, and the product's own
`ClaudeCodeSource.collapse_stats()` was run over that reconstruction:

| metric | spec pinned (R9) | mapper, corpus @ 5,385 records | delta |
| --- | ---: | ---: | ---: |
| files / records | 11 / 5,385 | 11 / 5,385 | exact |
| assistant records | 2,706 | 2,704 | −2 (−0.07%) |
| model calls (collapsed) | 1,678 | 1,677 | −1 (−0.06%) |
| cache-read naive | 826,955,847 | 826,779,383 | −0.021% |
| cache-read collapsed | 532,235,958 | 532,147,726 | −0.017% |
| **cache-read inflation** | **55.4%** | **55.37%** | **match** |
| **cache-creation inflation** | **55.3%** | **55.28%** | **match** |
| **output inflation** | **2.5%** | **2.56%** | **match** |

All three pinned percentages reproduce to the precision the spec states them at.
The residual few-record delta is what a record-count cutoff can do — it cannot land
on precisely the same record boundary the planner's inspection saw seconds earlier.

Three independent confirmations that this reconstruction really is the spec's
corpus and that the mapper really is implementing R9:

1. **An independent implementation agrees.** R9 was re-implemented from the spec
   prose alone, with no `swarm_observer` import, and produced byte-identical
   totals to the mapper on the same files (2,704 → 1,677; 826,779,383 →
   532,147,726). The mapper is not right by coincidence of shared code.
2. **R6's unrelated pinned constant falls out.** The spec says out-of-order
   timestamps were "observed 7 times in an 11-file, 5,385-record corpus". The
   reconstructed corpus produces `timestamp_out_of_order` **= 7**. That is a
   second, independent fingerprint of the same dataset.
3. **The ratio is stable across every subset.** Cache-read inflation measures
   55.3%–55.8% on the 10-file, 11-file, 13-file and cutoff corpora; the group-size
   histogram is `{1: 896, 2: 578, 3: 189, 4: 21, 5: 3, 6: 1}`. The collapse ratio
   is a property of the data, not of a lucky slice.

The one number that moves with corpus growth is **output inflation** (2.6% at 10
files, 2.56% at the cutoff, 2.03% at 11 current files, 1.8% at 13). That is
expected: output tokens are the fastest-growing component as a session continues,
and the fraction of multi-fragment groups whose earlier fragments carry non-zero
`output_tokens` shifts with it. It is not a mapper discrepancy.

**What the tests pin.** `tests/test_collapse_r9.py` pins the coder's fixture on
*both* columns as instructed, and pins the spec's real-corpus **ratios** (not its
absolute totals) behind an opt-in seam — see the tension note below. Absolute
totals from a growing corpus cannot be re-measured, which is itself a spec flag
(S5).

## Coverage

| Req | Tests | Result |
| --- | --- | --- |
| R1 | `test_model_schema.py::TestSchemaVersionR1` (3), `test_r1_the_schema_lists_every_normalized_model` | pass |
| R2 | `test_model_schema.py` — `TestModelPostureR2` (11), `TestTimestampsAreTimezoneAwareR2` (5), `TestIdAndPathAlphabetsR2` (17), `TestWarningTaxonomyIsClosedR2` (21), `TestPinnedOrderingsR2` (9), `TestJsonRoundTripR2` (3); `test_adversarial_inputs.py::TestHostileModelAndToolNamesR2`, `TestNumericAndTemporalEdgesR2` (21) | pass (4 xfail = BUG-1) |
| R3 | `test_reader_and_limits.py::TestAdapterSeamR3` (7) | pass |
| R4 | `test_record_tolerance.py` — `TestToleratedAndCountedR4` (24), `TestFatalHalfIsCheckedBeforePydanticR4` (11), `TestToleratedPathCannotRaiseR4` (5) | pass (2 xfail = BUG-1/BUG-4) |
| R5 | `test_identity_order_previews.py::TestTraceIdentityR5` (16); `test_reader_and_limits.py::TestSourceHashingR5` (5) | pass |
| R6 | `test_identity_order_previews.py::TestCanonicalOrderR6` (7); `test_reader_and_limits.py::test_r11_load_input_orders_files_by_basename` | pass |
| R7 | `test_identity_order_previews.py::TestToolInputDigestR7` (7) | pass |
| R8 | `TestPreviewsR8` (16), `TestNoPreviewsModeR8` (6); `test_adversarial_inputs.py::TestHostileStringsThroughPreviewsR8` (72) | pass |
| **R9** | `test_collapse_r9.py` — `TestCollapseMechanicsR9` (15), `TestCollapseStatsBothColumnsR9` (6), `TestApiErrorAccountingR9` (2), `TestRealTranscriptReconciliationR9` (5) | pass |
| R10 | `test_warnings_and_spans.py::TestWarningTaxonomyIsReachableR10` (28) | pass |
| R11 | `test_reader_and_limits.py` — `TestFailClosedInputClassesR11` (28), `TestToleratedInputClassesR11` (6), `TestSanitizedErrorLineR11` (10), `TestCliFailClosedR11` (2), `TestAtomicWritesR11` (5) | pass (1 xfail = BUG-3) |
| R12 | `test_warnings_and_spans.py::TestSpanKindMappingR12` (26), `TestAgentRunsR12` (3); `test_adversarial_inputs.py::TestHostileSidecarsR12` (16) | pass (1 xfail = BUG-2) |
| R38 (`schema`) | `test_model_schema.py::TestSchemaSubcommandR38` (9) | pass |
| R44 | `test_guard_verification.py::TestImportBoundaryGuardR44` (6); coder's `test_boundaries_and_posture.py` | pass |
| R45 | `test_guard_verification.py::TestOfflinePostureR45` (6); coder's `TestOfflinePostureR45` | pass |
| R49 | `test_guard_verification.py::TestCollectionFloorGuardR49` (8), `TestSkipAllowlistGuardR49` (6) | pass |
| R50 | `test_guard_verification.py::TestGoldenAndDeterminismHarnessR50` (8) | pass |
| R52 | `test_guard_verification.py::TestTraceabilityGuardR52` (7) | pass |

### Independent verification of the nine harness guards (R49, R50, R52, R44)

The PR claims every suite-integrity guard was tripped once and confirmed to fail.
That claim was **not taken on trust**. Each guard was tripped in a throwaway copy
of the repository, and each failure was observed:

| # | Guard tripped how | Observed |
| --- | --- | --- |
| 1 | floor raised above the actual count | `pytest` exit **4**, names the module and both numbers |
| 2 | a module made to collect zero | exit **4**, "collected 0 tests" naming the module |
| 3 | a skip with an unlisted reason | exit **1**, names the node id and the reason |
| 4 | a test citing `R99` | exit **1**, `tests cite requirement ids the spec does not define: ['R99']` |
| 5 | an uncited requirement dropped from the ledger | exit **1**, names the requirement |
| 6 | a ledger line for a requirement that *is* cited | exit **1**, names the requirement |
| 7 | `model/trace.py` importing `ingest.text` | exit **1**, the R44 rule assertion fires by name |
| 8 | `assert_matches_golden` neutered to a no-op | exit **1**, the golden canary's `DID NOT RAISE` |
| 9 | `assert_deterministic` neutered to never raise | exit **1**, the determinism canary's `DID NOT RAISE` |

All nine hold, and all nine were re-verified **after** the new modules landed
(floor hook still fires; a new test module with no floor entry fails; an unlisted
skip still fails). Two additional structural risks were checked and are clean:
`_floors_apply` really is active under CI's exact invocation
(`pytest -m "not live_narrator" --cov=...` → `config.args == ["tests"]`), and the
determinism matrix really varies `PYTHONHASHSEED`, `TZ` and `LC_ALL` rather than
calling the producer nine times in identical conditions.

## Bugs

Reported, never patched — the app code and the harness are both the coder's
deliverable. Each has a strict-`xfail` test that will fail on fix.

### BUG-1 (high) — `preview(..., DETAIL_MAX_CHARS)` is off by one, and the crash quotes the trace

`ingest/text.preview(value, limit)` returns `collapsed[:limit] + "…"` — up to
`limit + 1` code points. R8's preview fields allow for that (`PREVIEW_MAX_CHARS =
241` against a limit of 240). The seven call sites that pass `DETAIL_MAX_CHARS`
(200) do not: every consuming field is `max_length=200`, so a source string longer
than 200 printable characters produces a 201-character value and a pydantic
`ValidationError` escapes `ClaudeCodeSource.load()`.

Affected fields: `Span.model`, `Span.stop_reason`, `Span.tool_name`,
`Span.tool_use_id`, `SpanError.detail` (from `apiErrorStatus`),
`AgentRun.description` and `AgentRun.agent_type` (from the sidecar).

This is a **security** defect, not only a robustness one. R4 states the tolerated
half cannot raise and R11 states the fail-closed line "may never contain a byte of
file content". The exception that escapes is neither sanitized nor content-free —
its message embeds the attacker's bytes:

```
ValidationError: 1 validation error for Span
model
  String should have at most 200 characters
  [type=string_too_long, input_value='PAYLOAD-AAAAAAAAAAAA…', input_type=str]
```

The CLI's `except TraceError` does not catch it, so a hostile trace produces a
full Python traceback on stderr and exit 1 rather than one line and exit 2.

Repro:

```python
record = {"type": "assistant", "uuid": "u", "timestamp": "2026-01-01T00:00:00.000Z",
          "requestId": "r", "message": {"id": "m", "model": "A" * 201,
                                        "content": [], "usage": {}}}
# write as one JSONL line, then:
ClaudeCodeSource().load([path], IngestLimits())   # ValidationError, not TraceError
```

`"A" * 200` succeeds; `"A" * 201` raises — the boundary is exact.
Tests: `test_adversarial_inputs.py::TestHostileModelAndToolNamesR2` (4 xfail),
`test_record_tolerance.py::TestToleratedPathCannotRaiseR4`.
Suggested fix: pass `DETAIL_MAX_CHARS - 1` at those call sites, or make `preview`
budget the ellipsis inside `limit`. See spec flag S3 — the spec is ambiguous about
which, which is why this slipped.

### BUG-2 (medium) — a hostile sidecar `spawnDepth` crashes the run

`AgentRun.depth` is `ge=0`. `mapper.build_agents` passes any non-bool `int` from
the sidecar straight through, so `{"spawnDepth": -5}` in an `agent-<id>.meta.json`
raises an unsanitized `ValidationError` out of `load()`. A-a17 states every sidecar
failure mode is "no metadata"; this one is "the whole run dies". A sidecar sits
next to an input file and is not covered by `trace_id`, so it is an unusually cheap
thing for an attacker (or a future Claude Code version) to get wrong.

Repro: `tests/test_adversarial_inputs.py::TestHostileSidecarsR12::test_r12_a_negative_sidecar_depth_is_dropped_not_fatal` (xfail).

### BUG-3 (medium-high) — a JSON nesting bomb raises `RecursionError`, not `TraceError`

A single line whose JSON nests ~2,000 levels deep is a few kilobytes — far below
`max_line_bytes` (8 MiB) — and `json.loads` raises `RecursionError`, which is not a
`TraceError` and is not caught anywhere. The CLI prints a traceback hundreds of
frames long and exits 1 instead of R11's one sanitized line and exit 2. R11's caps
bound bytes but not depth, so nothing else stops it.

Repro:

```python
line = '{"type":"user","uuid":"u","timestamp":"2026-09-09T10:00:00.000Z","deep":' \
       + "[" * 5000 + "]" * 5000 + "}"
```

Test: `test_reader_and_limits.py::TestFailClosedInputClassesR11::test_r11_a_deeply_nested_json_line_must_fail_closed` (xfail). See spec flag S4.

### BUG-4 (high) — three nested raw-model fields have no before-validator, so a wrong type crashes and quotes the trace

`records.py`'s module docstring states the on-disk models use "before-validators
that coerce a wrong-typed value to the neutral default instead of raising, because
a pydantic `ValidationError` escaping this module would be an unsanitized crash on
hostile input". That holds for the scalar fields (`OptionalString`, `UsageInt`) but
not for the three nested-model fields, which are plain `X | None`:

| field | hostile value | result |
| --- | --- | --- |
| `RawRecord.message` | `"a string"` on a `system` or `attachment` record | `ValidationError` |
| `RawMessage.usage` | `true`, `0`, `"x"`, `[1]` | `ValidationError` |
| `RawUsage.cache_creation` | `"x"` | `ValidationError` |

`message` is checked pre-pydantic and correctly fatal for `assistant`/`user`
records — R4 names only those two — but `system` and `attachment` records fall
through to pydantic. `usage` and `cache_creation` are never checked at all. As with
BUG-1, the escaping exception quotes the offending value.

Found by the R4 property sweep (`_sweep`, 22 hostile values × 4 position families).
Tests: `test_record_tolerance.py::TestToleratedPathCannotRaiseR4` — a
`KNOWN_TOLERANCE_DEFECTS` set that fails if a **new** crash position appears, a
strict xfail asserting the set should be empty, and a staleness test that fails if
a listed defect stops reproducing.

## Observations (not defects)

* **OBS-1 — a terminal API-error fragment discards its whole group's usage.**
  "`usage` from the last fragment" (R9) and "an API-error record is never billable"
  (R12/R30) compose so that a group whose *last* fragment is an API error
  contributes nothing to `collapsed_usage`, while `naive_usage` still counts its
  earlier fragments. Not reachable on real data — every API-error record in the
  corpus is alone in its group, carrying its own UUID-shaped `message.id` — but it
  is an asymmetry between the two pinned columns, so it is pinned by
  `test_r9_a_terminal_api_error_discards_its_whole_groups_usage`.
* **OBS-2 — `unknown_extra_key` dominates a real trace.** On the 5,385-record
  corpus the mapper emits **51,617** `unknown_extra_key` warnings and
  `extras_dropped` ≈ 9 on nearly every span, because R4's known-ignored list does
  not include the keys A1 documents as present on every real record:
  `userType`, `entrypoint`, `cwd`, `version`, `gitBranch` (6,153 each), `slug`
  (4,564), `attributionAgent` (3,027), `effort` (3,014), `promptId` (2,013),
  `sourceToolAssistantUUID` (1,957). Behaviour is correct per R4; the *reporting*
  will be noise. See spec flag S1.
* **OBS-3 — `--no-previews` retains `model`, `stop_reason` and `tool_name`** (A-a18,
  the coder's open question). Confirmed and pinned by
  `test_r8_no_previews_keeps_the_structural_fields`. It means increment 4's
  `--no-previews` injection arm must still consider a hostile model id.
* **OBS-4 — `unreadable_path` cannot be exercised as root.** `chmod 000` does not
  deny root, so the CI container reads the file happily. Rather than skip (R49),
  `test_r11_a_mode_000_file_is_unreadable_unless_the_process_is_root` asserts in
  both directions: denied → `TraceError`; readable → `geteuid() == 0`.
* **OBS-5 — the reader is right to split on `\n` alone.** Real transcripts contain
  records with embedded U+2028/U+0085 inside JSON strings; a reader using Python's
  `str.splitlines()` would shred them. `reader._iter_lines` splits bytes on `\n`
  and is correct. Worth keeping in mind for any future line-oriented helper.

## Spec flags

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S1** | R4 | The known-but-ignored key list omits the record keys A1 documents on every real record, so a real trace produces ~51.6k `unknown_extra_key` warnings and `extras_dropped ≈ 9` per span. The signal R4 wants (`we saw something new`) is buried in fields we have known about since the A1 inspection. | Add `cwd`, `gitBranch`, `version`, `slug`, `entrypoint`, `userType`, `sourceToolAssistantUUID`, `toolUseResult`, `promptId`, `effort`, `attributionAgent`, `perTurnEffort`, `origin` to R4's known-ignored list (an R4 amendment), or state that `extras_dropped` is expected to be non-zero on every real span. |
| **S2** | R10 / R12 | R12 says attachment records are "counted", but R10's closed enum has no attachment code, so they land under `known_ignored_key` with `detail="attachment"` (the coder's A-a3). 877 of them in the real corpus. | Either add `attachment` to R10's enum (a schema-visible amendment) or bless A-a3 in the spec text so the resolution is not left to a PR note. |
| **S3** | R2 / R8 | R2 caps several fields at "200 chars" and R8 says a truncated preview gets a `…` appended; the spec never says whether that character counts toward a cap. The model resolves it one way for previews (241 allows the ellipsis) and the other for the 200-char fields — which is exactly the gap BUG-1 fell into. | State that every cap is inclusive of the ellipsis, and make `preview`'s `limit` the total budget. |
| **S4** | R11 | `IngestLimits` bounds file bytes, line bytes, record count and file count, but not JSON nesting depth, and R11's fatal list has no entry for "the parser could not decode this line for a non-syntactic reason". A 4 KB nesting bomb therefore escapes every guard (BUG-3). | Add `max_json_depth` to `IngestLimits` and a `json_too_deep` code to `TraceLimitError`, or require the reader to convert any decoder exception into `invalid_json`. |
| **S5** | R9 / A2 | R9 pins *absolute* token totals measured on `~/.claude/projects/...`, a live corpus that grows while the session runs. The totals were already unreproducible by the time the coder finished (826,955,847 → 826,779,383 at the same record count). The ratios reproduce exactly; the absolutes cannot. | Pin the ratios (55.4% / 55.3% / 2.5%) and the record-to-call ratio as the constants, and record the corpus's file count, record count and a SHA-256 manifest alongside them so a future re-measurement is against a named dataset rather than "the transcripts". |
| **S6** | R6 vs A6 | R6 counts `timestamp_out_of_order` and the mapper counts it *per file only* (the PR calls this A-a23, but the assumption list ends at A-a22). The reasoning is right — cross-file order is basename order by design — but the requirement's text does not say so, so the implementation looks like a partial one. | Add the per-file scoping to R6's text. Also renumber or add the missing A-a23. |

## Tensions between the harness's rules and what the work needed

1. **R50 canaries vs. the closed canary ledger.** R50 requires a canary per guard,
   and the task asked me to verify each guard independently. But
   `test_suite_integrity.py::test_r50_canary_modules_are_all_ledgered` asserts
   `present ⊆ REQUIRED_CANARIES`, and `REQUIRED_CANARIES` is a constant in the
   coder's module listing only the two increment-1 names. A tester therefore
   *cannot* add a canary file without editing app-owned harness code, which the
   role boundary forbids. Resolution: the independent guard verification lives in
   `tests/test_guard_verification.py`, a normal module. The assertions are the
   same; only the directory differs. **Recommendation for increment 2:** let the
   ledger admit tester-authored canaries, or state that guard-failure proofs
   outside `tests/canaries/` satisfy R50.

2. **The real-transcript gate vs. R49's no-skip rule.** The brief asked for the
   real-transcript check to be "gated behind an env var + skip"; R49 makes any skip
   whose reason is not in `allowed_skips.txt` a session failure, and the PR's
   instruction 4 says that file must stay empty. Both cannot be satisfied by a
   `pytest.skip`. Resolution:
   `TestRealTranscriptReconciliationR9::test_r9_real_transcripts_reproduce_the_spec_constants_when_available`
   always *runs*. With `SWARM_OBSERVER_REAL_TRANSCRIPTS` set it measures the corpus
   and asserts against the spec's ratios; without it, it asserts the opt-in seam
   itself is inert. Two further tests exercise the gate against a synthetic
   directory so it cannot rot. `allowed_skips.txt` remains empty and the suite
   records zero skips. This is the better reading of R49 — a skip is a test that
   did not run — but it is a deviation from the brief's literal wording and is
   flagged here rather than buried.

3. **Collection floors and strict xfails.** The floors are set to the exact
   collected counts. An `xfail` still *collects*, so fixing BUG-1 to BUG-4 will not
   move any floor; it will turn nine strict xfails into failures, which is the
   intended forcing function. No floor needs touching when the bugs are fixed —
   only the xfail markers and `KNOWN_TOLERANCE_DEFECTS`.

4. **`tests/factories.py` is a non-`test_` module and so carries no floor.** That is
   consistent with the harness's rule (`test_*.py` only) and with `harness.py`, but
   it means the record builders every module depends on are not floor-protected. If
   `factories.py` were emptied, several modules would fail loudly at import — a
   collection error, which R49 prefers — so this is acceptable rather than a gap.

## What was deliberately not tested

* R13–R25, R26–R31, R32–R37, R41–R43, R46, R47, R48, R51 — not built yet; still
  ledgered in `tests/traceability_pending.txt`.
* R39/R40 beyond what R11 requires of the entry point. `analyze` and `detectors`
  are not registered (A-a19), so exit codes 1 and 3 have no reachable path; R39 and
  R40 remain ledgered.
* R38's `analyze` and `detectors` surfaces. `test_r38_only_the_pinned_subcommands_are_registered`
  pins that only `schema` exists today, so their arrival is a visible test change.
