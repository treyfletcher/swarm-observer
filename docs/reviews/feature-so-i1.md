# PR review: swarm-observer increment 1 (`feature/so-i1`)

Reviewer: pr-reviewer-agent · Spec: `docs/specs/swarm-observer-v1.md` (APPROVED) ·
PR: `docs/prs/feature-so-i1.md` · Test report: `docs/test-reports/feature-so-i1.md`

Scope reviewed: **R1–R12, R44, R45, R49, R50, R52, R38's `schema` subcommand.**
Requirements belonging to increments 2–5 do not exist yet and their absence is not
reported as a finding.

## Verdict

**Approve with the fixes in this branch.** The architecture is right, the R9
collapse is right, and the suite-integrity harness genuinely works — I tripped ten
of its guards myself rather than taking either agent's word for it, and every one
failed loudly. What was wrong was concentrated in one place and one shape: **eight
paths where a hostile trace escaped `ClaudeCodeSource.load()` as an unsanitized
exception carrying the attacker's own bytes.** The tester found four; probing found
four more, including one that silently deletes a model call and its cost.

All eight are fixed in this branch as `review:` commits with pinning tests, and the
nine strict xfails are de-xfailed in the commits that fix them. Suite, ruff, ruff
format and `mypy --strict` are green: **602 passed, 0 xfailed, 0 skipped.**

| tag | count | disposition |
| --- | --- | --- |
| [BLOCKER] | 8 | all fixed here |
| [IMPROVE] | 7 | all fixed here |
| [NIT] | 7 | comments only |
| [PRAISE] | 5 | — |

---

## The one theme

Every [BLOCKER] below is the same defect wearing a different hat: **a guard that
was correct about the value it was written for and wrong about the value next to
it.** The spec's own words for this are in R32's rationale — "a guard tuned to the
call path that exists is wrong for the next call path" — and R44's Modularity note
repeats it as the draftsmith lesson. It arrived anyway, seven times, because
increment 1 has no renderer yet and so no probe asked *what reaches the user* when
ingestion fails. `pytest.raises(TraceError)` was asserted for the classes somebody
thought of; nothing asserted that `TraceError` is the *only* thing that can come
out.

That negative claim is now asserted directly, in two places: the R4 sweep
(`test_r4_the_tolerated_path_cannot_raise_at_all`, ledger emptied and closed) and a
23-class fail-closed probe I ran by hand over every R11 input class, reproduced
below.

---

## [BLOCKER] findings

All eight reproduce on `9ba8bbc` and are fixed on this branch.

### B1 — BUG-1 confirmed: a field cap and a truncation budget are not the same number

`preview(value, DETAIL_MAX_CHARS)` returns up to 201 code points against a
`max_length=200` field. Reproduced on **seven** fields, not the four the tester
listed: `Span.model`, `Span.stop_reason`, `Span.tool_name`, `Span.tool_use_id`,
`SpanError.detail`, `AgentRun.description`, `AgentRun.agent_type`. The escaping
`ValidationError` reads
`input_value='AAAA…', input_type=str` — that is the trace, quoted, in an exception
message. R4 says the tolerated half never raises; R11 says a fail-closed detail
"may never contain a byte of file content". Both violated by one character.

**Fixed** (`4236cc1`) by encoding the S3 ruling rather than commenting it — see
Rulings. Note one behavioural detail the naive fix gets wrong: reserving the
ellipsis unconditionally truncates a value that fitted its cap *exactly*, which
broke the coder's own `[200]` case. `preview_within` only pays for the ellipsis
when truncation actually happens.

**Correction to the tester's report:** BUG-1 does not currently produce "a CLI
traceback and exit 1". `analyze` is not registered in increment 1 (A-a19), so no
CLI path reaches `load()`. The defect is real and severe at the `TraceSource` seam
— which is what R3 and R11 actually constrain — but the CLI consequence is a
prediction about increment 3, not an observation. Worth saying precisely, because
a reviewer who checks the CLI claim and finds it false may discount the rest.

### B2 — BUG-4 confirmed: three nested raw-model fields had no before-validator

`RawRecord.message`, `RawMessage.usage`, `RawUsage.cache_creation`. Same leak
shape. `parse_record`'s pre-pydantic check makes a non-object `message` fatal for
`assistant`/`user` records, exactly as R4 says — but `system` and `attachment`
records fell through to pydantic, and `usage`/`cache_creation` were never checked
at all. **Fixed** (`927f080`).

### B3 — BUG-2 confirmed: a sidecar `spawnDepth: -5` kills the run

`AgentRun.depth` is `ge=0`; `build_agents` passed any non-bool int through.
A-a17 promises every sidecar failure mode is "no metadata"; this one was "the whole
run dies". Compounded by A-a17's own flag: the sidecar is deliberately outside
`trace_id`, so it is the cheapest thing in the input set to tamper with.
**Fixed** (`101931a`), with the positive arm added so the guard cannot decay into
"always `None`".

### B4 — BUG-3 confirmed: a 4 KB nesting bomb raises `RecursionError`

Not a `TraceError`, caught nowhere. **Fixed** (`1af5a7b`) by treating any decoder
failure — syntactic or not — as `invalid_json`, which is the S4 resolution that
does not require amending the spec. Pinned over array *and* object nesting at three
depths, so the fix cannot be a threshold that happens to cover the reported
payload, plus the negative arm (50 levels still parses).

### B5 — NEW: two regex engines disagree about `$`, and trace bytes fall through the gap

`safe_agent_id` decided whether a recorded `agentId` was safe to carry verbatim
using `re.match(AGENT_ID_PATTERN, ...)`. **Python's `$` also matches immediately
before a trailing newline; the engine pydantic compiles the same pattern with does
not.** So:

```
safe_agent_id("a1\n")  ->  "a1\n"      # mapper says: already an identifier
Span(agent_id="a1\n")  ->  ValidationError: String should match pattern ... input_value='a1\n'
```

Same leak family as B1 and B2, found by probing. What makes it worse than the
others is which way the disagreement happened to fall: had pydantic agreed with
Python instead, a newline-bearing agent id would have gone into an HTML attribute
value in increment 4 — which is precisely and only what R5's "ids never embed trace
content" exists to prevent. The safety claim rested on two regex engines agreeing
about one metacharacter, and they don't.

**Fixed** (`5e21634`): `fullmatch` in both ingest-layer guards. Pinned as the
property that actually matters — *every value `safe_agent_id` can emit is accepted
by the model* — not just as seven payloads. The tester's existing R5 assertions
used `re.match` too, so they could not have caught this; they now use `fullmatch`.

### B6 — NEW: an over-long basename crashes in pydantic instead of failing closed

`SourceFile.name` is capped at 200 characters (R2, so only a basename ever reaches
a report); ext4 allows 255. The cap was therefore enforced several layers into
`load()` as an unsanitized `ValidationError` quoting the path. **Fixed**
(`44136fe`) with a new `name_too_long` code on `TraceReadError`, following the
A-a11 precedent. The rejected name is deliberately *not* echoed back.

### B7 — NEW: a timestamp that overflows the UTC shift escapes as `OverflowError`

`9999-12-31T23:59:59-23:59` satisfies R4's RFC 3339 pattern, parses cleanly, and
then raises `OverflowError` inside `astimezone(UTC)` — as does
`0001-01-01T00:00:00+23:59` in the other direction. Not a `TraceError`, caught
nowhere. Forty bytes of trace against R11. **Fixed** (`3595710`).

### B8 — NEW, and the most consequential: the R9 collapse could silently merge two distinct model calls

This is the one I was asked to hunt for, and it is there twice.

**(a) The solo group key was not unique across the input set.** A record with
neither `requestId` nor `message.id` keyed on `("solo", record.uuid)`. R4 makes a
duplicate `uuid` fatal **within one file only** — correctly, since agent files are
written independently, and the coder's own test pins that cross-file duplicates are
legal. So two ungrouped assistant records in two files sharing a uuid collapsed
into one span, and R9's "usage from the last fragment" then discarded the first
call's tokens outright:

```
agent-1.jsonl: uuid "same-uuid", output_tokens 100
agent-2.jsonl: uuid "same-uuid", output_tokens 200
before:  1 model_call span, output_tokens 200      # 100 tokens gone, no warning
after:   2 model_call spans, output_tokens 100, 200
```

Note the direction. A2 spends a paragraph on the risk of the collapse being *too
naive* and inflating every figure by 55%. This is the mirror image — the collapse
being too eager and deflating one — and it is worse in one respect: naive summation
is caught by the two-column pin, while an over-merge moves both columns' *difference*
and nothing pins that.

**(b) An absent field and an empty one were the same key.** `record.sessionId or
""`, `message_id or ""`, `request_id or ""` mapped a recorded `requestId: ""` onto
the same key component as a missing `requestId`, so two responses sharing a
`message.id` merged when one carried an empty string. R9's key is the four
*recorded* values.

**Fixed** (`52577fc`): the solo key is the record's canonical index (R6), which is
unique across the whole input set by construction and orders identically; `None` is
preserved in the grouped key.

---

## [IMPROVE] findings

Every one of these came from mutation testing the suite — changing one character of
the product and asking whether anything failed. I ran 32 mutations; the suite caught
25 on arrival. The seven it did not are below, and all are now caught.

### I1 — the R9 collapse condition itself was untested

Changing `if message_id is None and request_id is None` to `or` — **one character,
in the requirement this whole increment exists for** — left the entire suite green.
Every fragment in the fixture and in the tester's factories carries *both* fields,
so the mutation is unobservable there. On a real transcript whose fragments carry
only a `requestId`, that `or` silently restores the 55% cache inflation with the
two-column pin still passing. Pinned in `52577fc`.

### I2 — four boundaries the suite proved nothing about

`max_line_bytes` and `max_file_bytes` off by one, `timestamp_out_of_order` widened
from `<` to `<=`, and `dangling_tool_use` losing its `tool_result_status ==
"missing"` half — all survived. The cap tests set the limit far below the payload,
so they proved the cap fires *somewhere* and nothing about where; the
`dangling_tool_use` cases all had a final tool call that was *also* unresolved, so
a guard firing on any trailing tool call was indistinguishable from the right one.
Fixed in `0102c97`, with the caps exercised at exactly the cap and one byte under,
including the streaming-buffer check that is the only thing bounding memory on a
file with no newline in it.

### I3 — R4's `bad_message` rule had no `user` arm

Deleting `user` from the fatal check left the suite green. Fixed in `927f080`.

### I4 — the real-corpus measurement path was dead code in CI

See the ruling on the tester's tension 2 below. Fixed in `faee1e8`.

### I5 — the R50 canary ledger was closed against the tester

See the ruling on tension 1. Fixed in `b9e875a`.

### I6 — the tolerance ledger was left re-openable

`KNOWN_TOLERANCE_DEFECTS` had a companion test asserting the listed defects still
*reproduced*, which was right while they were open and is an invitation once they
are closed. Replaced with a guard that it is empty and stays empty (`d78136d`).

### I7 — collection floors were below the real counts after the fixes

Raised to the new exact counts (`d72eff5`). A floor left below the real count
silently exempts every test above it, which would have left the 66 tests these
commits added unprotected.

---

## [NIT] — comments only, nothing changed

- **N1** `RawBlock.errored` uses `is_error is True`; mutating it to `bool(...)`
  survives. A recorded `"is_error": "true"` (a string) currently reads as
  *success*. R12 does not say which is right. Defensible as-is; worth a line in
  R12 when increment 2 gives `tool_result_status` a consumer.
- **N2** `_finalize`'s parent-resolution loop (`parent_seq >= 0`) is more general
  than the emission order it walks: `_model_call` always returns the call
  immediately followed by its tool calls, so the search never runs more than a
  couple of steps and its boundary is unreachable. Mutating it is behaviourally
  equivalent today. Fine, but the generality is not load-bearing and reads as if
  it were.
- **N3** The two `max_file_bytes` checks (stat and stream) are mutually redundant,
  so neither alone is mutation-detectable; mutating both is caught. That is
  defence in depth, not a defect — recorded so the next reviewer running a
  mutation pass does not chase it.
- **N4** `ClaudeCodeSource._read_sidecars` calls `resolve_inputs` a second time,
  re-running every `stat`, symlink and duplicate check on every load. Harmless
  now; it will not be on a 64-file input set.
- **N5** The PR write-up cites **A-a23** twice (R6 coverage row, and the mapper
  comment) and its own assumption list ends at A-a22. The tester noticed;
  recording it so the numbering is fixed rather than inherited by increment 2.
- **N6** R52's citation rule is satisfied by a *mention* in a test's name or
  docstring. A test called `test_r9_whatever` that asserts nothing satisfies
  traceability. That is what R52 asks for and the extractor implements it
  correctly (it rightly refuses comments and string constants), but the check
  measures naming, not coverage, and nobody should read a green R52 as coverage.
- **N7** `unknown_extra_key` aggregates to a single entry with an empty `detail`
  and a count in the tens of thousands. Correct per R10 and A-a2 — see S1 for why
  the count is that large.

---

## [PRAISE]

- **P1 — the suite-integrity harness is real.** I tripped ten guards in a throwaway
  copy: floor raised above the count, a module collecting zero, an unlisted skip, a
  citation of `R99`, an uncited requirement dropped from the ledger, a ledger line
  for a cited requirement, a `model → ingest` import, a neutered golden comparison,
  a neutered determinism harness, and an inert canary. Every one failed loudly and
  named the thing; the control run is clean. The PR's "every guard tripped once and
  confirmed to fail loudly" was accurate, and so was the tester's independent
  re-verification. This is the first check in this team's history that has been
  shown to be capable of failing before anyone relied on it.
- **P2 — `TraceError` built from typed fields.** Refusing to accept free text at
  the constructor, and rejecting a path or a non-enumerated note at the point of
  the mistake, is why all 23 fail-closed classes I probed produced a clean,
  content-free single line without anyone having to remember to sanitize. It is the
  reason the eight blockers above are all *outside* this taxonomy rather than
  inside it — the design worked exactly where it was applied.
- **P3 — `collapse_stats` as product code, not test code.** Exposing both columns
  through the same parse the CLI uses is what makes the two-column pin meaningful
  instead of a test re-implementing the thing it checks.
- **P4 — the tester's independent re-implementation of R9.** Re-deriving the
  collapse from the spec prose with no `swarm_observer` import, getting
  byte-identical totals, and then noticing that R6's unrelated pinned constant
  (`timestamp_out_of_order == 7`) falls out of the same reconstruction is a genuine
  second fingerprint on the dataset. That is how you show a number is not right by
  coincidence of shared code.
- **P5 — strict xfails as bug reports.** Nine strict xfails meant no fix could land
  without the ledger being updated in the same breath. It worked exactly as
  intended on all four bugs.

---

## Rulings

I cannot amend the spec. Each ruling below states exactly what the PM should change.

### S1 — R4's ignored-key list omits keys present on every real record

**Ruling: spec defect, not an implementation defect. Nothing here was mine to fix.**

The mapper does precisely what R4 says. I reproduced the effect on a single
realistic record — the A1-documented shape with `cwd`, `gitBranch`, `version`,
`userType`, `slug`, `entrypoint`, `sourceToolAssistantUUID` — and got
`extras_dropped == 7` and seven `unknown_extra_key` warnings from **one** record.
Scaled to the corpus that is the tester's 51,617.

R4's tolerated-and-counted split exists so that *"we saw something new"* is visible.
A signal that fires on every record of every real trace is not visible, it is
wallpaper — and worse, it will train the reader to ignore the one warning that
matters when Claude Code adds a genuinely unknown field next month. The behaviour
is correct and the requirement is wrong.

**PM: amend R4's known-but-ignored list**, adding the keys A1 already documents as
present on every record: `cwd`, `gitBranch`, `version`, `slug`, `entrypoint`,
`userType`, `sourceToolAssistantUUID`, `toolUseResult`, `promptId`, `effort`,
`attributionAgent`, `perTurnEffort`, `origin`. Note this will change a checked-in
test — `test_r4_the_known_ignored_set_is_exactly_the_pinned_twelve` pins the list
by value — which is the right kind of friction: the amendment is visible in the
diff. Do **not** take the alternative of "state that `extras_dropped` is expected
to be non-zero on every span"; that turns a counter into a constant.

### S2 — R10 has no `attachment` code, R12 says attachments are "counted"

**Ruling: spec defect. Bless A-a3 rather than extend the enum.**

The coder's resolution — counting attachments as `known_ignored_key` with
`detail="attachment"` — is the better of the two. Adding `attachment` to
`ParseWarningCode` changes the `Literal`, which changes the JSON Schema, which
changes `tests/golden/schema.json` and (per R1/R2's own rule that the model's shape
is the versioned contract) argues for a `TRACE_SCHEMA_VERSION` bump — all for a
warning code that no consumer will exist for until increment 4's report renders the
warnings table. That is a lot of schema churn to name something the enum already
has an honest slot for.

**PM: add one sentence to R10** blessing `known_ignored_key` with an enumerated
record-type detail as the counting mechanism for record types R12 declares
span-less. Leaving this to a PR note means the next adapter author has to read
`docs/prs/` to know the rule.

### S3 — caps versus the ellipsis

**Ruling made, and encoded in code rather than left as prose. Both readings are
right — for different numbers.**

R8's 240 is a **truncation budget**: "truncate to 240 characters, appending `…`
when truncated" plainly means 240 characters *and then* an ellipsis, which is why
`PREVIEW_MAX_CHARS = 241` is correct and must stay. R2's 200 is a **field cap**: a
`max_length=200` field that receives 201 characters is a crash, so the ellipsis is
necessarily inside it. The spec is not ambiguous about either number; it is
ambiguous about the fact that they are *different kinds of number*, and
`preview(value, limit)` had one parameter serving both.

So the fix is not to pick a side, it is to make the confusion unrepresentable:
`preview()` keeps R8's semantics, `preview_within(value, max_chars)` is the entry
point for anything with a `max_length`, and every capped field uses it. A future
call site now has to choose a name, and the wrong choice reads wrong.

**PM: amend R8** with one clarifying sentence — "the 240 is a budget for the
truncated text; the appended ellipsis is additional, which is why the preview
fields admit 241" — and **amend R2** with "every `max_length` on this model is a
total budget, inclusive of any truncation marker." Two sentences close the gap
permanently.

### S4 — no JSON-depth cap in `IngestLimits`

**Ruling: spec defect. I implemented the half that does not require an amendment;
the amendment is still needed.**

R11's fatal list covers "a line that is not valid JSON", and a line the decoder
cannot decode is that, so converting `RecursionError` to `invalid_json` is
spec-conformant today and closes the hole. But it is a catch, not a cap: the memory
and stack cost is still paid before the failure, and the *reason* for the failure
is invisible to an operator who sees only `invalid_json`.

**PM: amend R11** — add `max_json_depth` to `IngestLimits` (with a default; 200 is
far beyond anything a transcript contains) and a `json_too_deep` code to
`TraceLimitError`, so the bound is declared, configurable and legible in the error
line, like the other four caps. Note R11's fatal list should also gain the
condition B6 fixed: a basename longer than `SourceFile.name` permits. I added
`name_too_long` under the A-a11 precedent, but a code the spec does not name is a
code the next reviewer has to adjudicate.

### S5 — R9 pins absolute totals from a corpus that grows

**Ruling: yes, ratio-pinning must replace absolute-total-pinning. And no, the
current tests were not robust — I fixed that.**

The absolutes were *already* unreproducible by the time the coder finished:
826,955,847 → 826,779,383 at the same record count. A pinned constant that cannot
be re-measured is not a pin, it is a comment with an `assert` in front of it. The
ratios reproduced to the precision the spec states them at, from a reconstruction
the tester cross-checked two independent ways. Pin the ratios.

But ratio-pinning alone would have inherited the tester's real problem, which is
separate from S5 and which I count as a finding: **in CI the reconciliation test
measured nothing at all.** With no corpus configured its only assertion was
`real_transcript_paths() == ()` — the same claim the test above it already makes —
so `stats_for` and `inflation_pct` had no caller, and the assertions that matter
had never executed in the environment that gates the merge. That is this team's
signature failure mode; it does not stop being one because the disguise changed
from "skipped" to "passed".

The fix is also the answer to S5's second half. An absolute token total from a live
corpus can never be re-measured, but the *shape* it came from can: the tester
measured the group-size histogram `{1: 896, 2: 578, 3: 189, 4: 21, 5: 3, 6: 1}`,
and the collapse ratio is a function of exactly that distribution. That histogram
is now checked in and replayed synthetically through the same measurement path the
real corpus uses, in every run, with the expected inflation asserted as exact
arithmetic rather than a tolerance and the histogram's own record-to-call ratio
asserted against the spec's 2,706 / 1,678. Verified breakable: disabling the
collapse fails it.

**PM: amend R9.** Replace the absolute token totals with (a) the three inflation
percentages, (b) the record-to-call ratio, and (c) the corpus's group-size
histogram, file count and record count, so a future re-measurement is against a
named dataset rather than "the transcripts". Keep A2's warning verbatim — it is the
best paragraph in the spec.

### S6 — R6 does not say `timestamp_out_of_order` is scoped per file

**Ruling: implementation is right, requirement text is incomplete.**

Cross-file order is basename order by design (A6), so comparing timestamps across a
file boundary would report R6's own decision as a defect on every multi-agent
trace. The mapper is correct. But a requirement whose text does not say so makes a
correct implementation look like a partial one — which is exactly what happened, to
both the tester and me. I have pinned the behaviour in both directions
(`0102c97`) so it cannot drift while the spec catches up.

**PM: amend R6** with the per-file scoping, and fix the A-a23 numbering in the PR
write-up (N5).

### Tension 1 — R50 canaries versus the closed canary ledger

**Ruling: the ledger was over-strict and the tester was right to work around it.
Fixed here rather than left to increment 2.**

`present ⊆ REQUIRED_CANARIES` is stricter than R50, which requires certain canaries
to *exist* and says nothing about forbidding others. Its effect was to lock
`tests/canaries/` — the directory whose entire purpose is proving guards can fail —
against the role most likely to need a new guard proof, and to do so via a constant
in an app-owned module. That is a role boundary enforced by a test that did not
mean to enforce one.

The property the closed list was standing in for is real: a "canary" that never
asserts a failure is the thing R50 exists to prevent, wearing the right filename.
So that is now checked directly — every canary module must contain a
`pytest.raises` and cite R50 — and an extra canary is welcome. Verified by dropping
an inert canary into the directory and watching it fail.

`tests/test_guard_verification.py` can stay where it is. Guard-failure proofs
outside `tests/canaries/` satisfy R50's intent, and duplicating them would be
worse than leaving them.

### Tension 2 — the real-transcript test runs rather than skips

**Ruling: the tester's reading of R49 is correct and better than the alternative.
The reproducibility concern is narrower than it looks, and the real defect was
elsewhere (fixed — see S5).**

Two things need separating here.

*Does the suite read files outside the repository?* **Not by default, and not from
`~/.claude` at all.** `real_transcript_paths()` reads a directory named by
`SWARM_OBSERVER_REAL_TRANSCRIPTS` and returns `()` when it is unset or does not
exist. There is no hardcoded home-directory path anywhere in the suite — I checked.
So CI reads nothing outside the repo, and a developer reads their own transcripts
only by deliberately naming the directory. That is the right shape for an opt-in
seam, and it is materially safer than the brief feared.

*Is "always run, assert the seam when absent" a legitimate reading of R49?* Yes.
R49's rule is that a skip is a test that did not run and must be declared; a test
that runs and asserts a weaker property in a weaker environment is not a skip. It
also keeps `allowed_skips.txt` empty, which is the posture the whole harness is
built around. Forcing a `pytest.skip` here to satisfy the brief's literal wording
would have made the file non-empty for the worst possible reason.

**But** the pattern has a failure mode of its own, and this instance had it: the
weaker property was a tautology, so the test reported *passed* while measuring
nothing, and its measurement code had no caller in CI at all. "Passed" is a worse
disguise than "skipped", because a skip at least announces itself in the summary
line. That is fixed (`faee1e8`) by giving CI a real subject — the synthetic
histogram — so the measurement path executes in every run and only the
corpus-specific assertions are environment-dependent.

**PM: consider one sentence in R49** blessing the pattern with the condition
attached: an environment-gated test must run in both environments *and assert
something that can fail in each*. The pattern is good; unconditioned, it is a way
to launder a skip.

---

## What I verified myself

Because the brief asked me not to trust either agent's claims, and because "the
check ran" is the property this project is organised around:

**Fail-closed contract, every R11 input class (23 classes probed by hand).** Not
valid JSON · JSON not an object · bare scalar · missing `type`/`uuid`/`timestamp`
(three) · non-RFC-3339 timestamp · timestamp overflowing the UTC shift · duplicate
`uuid` in one file · non-object `message` on assistant *and* user · invalid UTF-8 ·
line too long · file too large · too many records · too many files · missing path ·
directory · FIFO · empty input · symlink escape · duplicate basename · nesting bomb
· over-long basename. **All 23 produce exactly one sanitized line, a taxonomy code,
no traceback, and no byte of file content.** Three classes that must *not* be fatal
— an empty file, CRLF endings, a hostile `agentId` — correctly are not.

**R9 collapse, adversarially.** Constructed streams designed to make the collapse
merge or split wrongly: cross-file uuid collision (found B8a), empty-vs-absent join
fields (found B8b), one-of-two join fields present (found I1), interleaved
responses, a terminal API-error fragment, and identical `message.id` across agents.
The collapse now merges exactly what R9 says and nothing else.

**Suite-integrity harness, ten guards tripped.** Listed under P1. Plus a control
run to confirm the guards are not simply always-failing.

**Mutation testing, 32 one-character changes to the product.** 25 caught on
arrival; the 7 survivors are I1–I3 and N1–N3. After the fixes, the only survivors
are the three recorded as [NIT] — two behaviourally equivalent, one a genuine
open question for R12.

---

## Observations I did not fix

- **OBS-1 (the tester's) is real and I am leaving it.** A group whose *last*
  fragment is an API error contributes nothing to `collapsed_usage`, while
  `naive_usage` still counts its earlier fragments — I reproduced it losing 500
  output tokens. It follows correctly from R9's "usage from the last fragment"
  composed with R12/R30's "an API-error record is never billable", and it is not
  reachable on real data (every API-error record in the corpus is alone in its
  group). Changing it would mean contradicting R9's literal text on my own
  authority, which is not a reviewer's call. **PM: R9 or R12 should say which rule
  wins when a group's terminal fragment is an API error.** Until then the tester's
  pin holds the behaviour still, which is the right state for an undecided
  question.
- **A-a18 remains open for increment 4.** `--no-previews` keeps `model`,
  `stop_reason` and `tool_name`. B1's fix caps them, and B5's fix constrains
  `agent_id`, but `Span.model` still carries arbitrary trace text under
  `--no-previews`. R51's `--no-previews` arm asserts *no payload appears anywhere*.
  The coder flagged this and is right: decide at T17, and the fix is an ingest-time
  pattern constraint, not a render-time patch.

## Increment-1 assessment

The bottom half of the product is the right bottom half. The seams are where the
spec puts them, the normalized model enforces R2's prose as constraints rather than
conventions, R9's collapse is correct and honestly instrumented, and the
suite-integrity harness is the first thing this team has built that demonstrably
cannot report green while broken. The R44 test asserting rules for subpackages that
do not exist yet is the single best decision in the branch: increments 2–5 arrive
under supervision rather than after it.

The defect that got through is worth naming plainly, because it will recur. Every
one of the eight blockers is a *guard that was right about its own value and wrong
about the value next to it* — a cap that fit one field and not another, a validator
applied to scalars and not to objects, a regex engine that agreed with itself and
not with pydantic, a uuid unique in one file and not across two. The spec predicted
this failure class twice (R32's rationale, R44's Modularity note) and it landed
anyway, because increment 1 had no probe asking the negative question: *what is the
complete set of things that can come out of `load()`?* That question is now asked
directly, by the R4 sweep and by the 23-class probe, and it is the question
increments 2–5 should inherit at each new boundary.

Merge once the PM has the six spec amendments queued. None of them blocks the
branch; all of them will block someone in increment 2 if they are not written down.
