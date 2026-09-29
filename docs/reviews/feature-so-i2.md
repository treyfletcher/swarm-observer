# PR review: swarm-observer increment 2 (`feature/so-i2`)

Reviewer: pr-reviewer-agent · Spec: `docs/specs/swarm-observer-v1.md` (APPROVED) ·
PR: `docs/prs/feature-so-i2.md` · Test report: `docs/test-reports/feature-so-i2.md` ·
Prior review: `docs/reviews/feature-so-i1.md` (rulings S1–S6, tensions 1–2, and the
Python-3.12 addendum)

Scope reviewed: **R13–R25, R48, R50's increment-2 canary.** Cost (R26–R31), report
rendering (R32–R37), the `analyze` pipeline (R38–R40) and the narrator (R41–R43) do
not exist in this increment and their absence is not a finding.

## Verdict

**Approve with the fixes in this branch.** The detectors are a faithful
transcription of R18–R24, the fixture/expectation contract is the first thing in
this repo that makes a *corpus* falsifiable rather than merely present, and I
tripped it six ways myself rather than taking the contract's word for it.

The three reported bugs are all real and all fixed here. The finding that matters
more than any of them is not in the code: **the branch's central quality claim —
"46 boundary mutations, 46/46 caught" — is a measurement of the mutation set, not
of the suite, and the tester's replacement number has the same structural
property.** My own 110-mutation sweep, weighted at the module both prior sweeps
skipped, found nine survivors neither had reached, one of which changes real
findings on 512 of 16,000 probe runs. On this branch the same 110 leave **10
survivors, the identical set on both interpreters, every one confirmed equivalent
differentially**. Details and the process ruling are below.

| tag | count | disposition |
| --- | --- | --- |
| [BLOCKER] | 4 | all fixed here |
| [IMPROVE] | 6 | all fixed here |
| [NIT] | 6 | comments only |
| [PRAISE] | 5 | — |

Suite, ruff, `ruff format --check` and `mypy --strict` are green on CPython 3.11
and 3.12: **1337 passed, 0 xfailed, 0 skipped** on each. The tester's three strict
xfails are de-xfailed in the commits that fix them.

---

## The one theme

Increment 1's theme was *a guard that was right about the value it was written for
and wrong about the value next to it*. Increment 2's is one layer up: **a check
that was right about the inputs it was given and silent about the inputs nobody
chose to give it.**

Every [BLOCKER] and [IMPROVE] below is an instance. BUG-1 is a comparison correct
for every pair of windows the corpus contains and wrong for the pair it does not.
BUG-2 and BUG-3 are algorithms correct for every trace in `tests/fixtures/` and
unusable on the trace this tool exists to read. The six survivors my sweep found in
`detect/base.py` are behaviours nothing asserted because no test happened to
produce the input that distinguishes them. And the mutation score itself is the
same defect at the meta level: a number that reports green because the person
reporting it also chose what it would be tested against.

That is a different failure from increment 1's, and it is the one this project's
own spec is organised against — R48–R52 exist precisely because "the check ran"
and "the check could have failed" are different claims. The suite-integrity
harness proves the second for its own guards. Nothing yet proves it for the
detectors' inputs.

---

## [BLOCKER] findings

All four reproduce on `3cd84b0` and are fixed on this branch.

### B1 — BUG-1 confirmed, and it inflates rather than hides

`merge_runs` decided overlap with `start <= merged[-1][1]` on **half-open** ranges,
so `[0, 10)` and `[10, 20)` — sharing no span — merged.

```
error_run(20, [0, 1, 2, 17, 18, 19])
before:  1 finding, critical, {'errors': 6, 'window_spans': 20, ...}
after:   2 findings, warning,  {'errors': 3, 'window_spans': 3} each
```

Three things were wrong, in increasing order of how much they matter:
`window_spans: 20` describes a run twice as wide as R20's window; `errors: 6` is a
density claim no ten-span window supports; and the severity was escalated from two
warnings to one critical.

The direction is worth naming. Increment 1's B8 was a collapse that was too eager
and **deflated** a cost figure. This is the mirror image — a merge that is too
eager and **inflates** a severity. Both are one comparison being right about the
values it was written for, and this repo has now shipped both signs of it. A tool
whose product is "cost and waste findings" is damaged by either.

**Fixed** (`8202630`). `merge_runs` is pinned directly at both sides of the one
comparison: `(0,10)+(10,20)` stay two, `(0,10)+(9,20)` become one. The corpus
expectations did not move, and R48's arms are unchanged (`retry_storm` still fires
on 2 of 26 fixtures) — which is itself the finding about the corpus: nothing in it
could see this.

### B2 — BUG-2 confirmed: `retry_storm` was quadratic in an agent's spans

For each of the `N - 9` window starts, the window list scanned the whole
`error_positions` list. An agent whose spans are largely errors is a rate-limited
session, and that is where the term shows:

| error spans | before | after |
| --- | --- | --- |
| 1 250 | 0.035 s | 0.001 s |
| 5 000 | 0.43 s | 0.004 s |
| 10 000 | 2.2 s | 0.009 s |
| 20 000 | 9.2 s | 0.037 s |

**Fixed** (`66e759c`) with a two-pointer window count and a bisected per-run error
slice, so a trace with many separate storms does not reintroduce the term by the
back door. `qualifying_windows` is pinned against a fresh per-window recount over
2,000 random layouts, and at both sides of both of its boundaries — errors nine
apart share a ten-span window, errors ten apart never do.

### B3 — BUG-3 confirmed: `blocked_agent` was quadratic in spans × gaps

`_other_intervals` was built once per agent, but `covered_millis` clipped and
re-sorted that whole list for *every gap*.

| spans | before | after |
| --- | --- | --- |
| 1 250 | 0.15 s | 0.021 s |
| 5 000 | 2.2 s | 0.084 s |
| 10 000 | 8.3 s | 0.24 s |

**Fixed** (`f539895`) with `CoverageIndex`: the intervals are unioned once into
disjoint ascending runs with a prefix sum, and a gap query is two binary searches
plus arithmetic on the two partially covered ends. `covered_millis` keeps its
signature and is now a call onto the index, so there is exactly one implementation
of the union rule — the same discipline R44 applies to `escape_html`.

Exactness matters here because R23 says integer milliseconds: the union of the
clipped intervals is the clip of the union, so interior runs contribute their whole
already-integral length and only the two boundary runs are measured against the
window. Verified against the previous form over **200,000 random windows with
microsecond-precision timestamps — zero differences**, which is the input class
that would expose a truncation moved to a different point.

Both performance fixes carry a regression pin with two bounds, and I verified
**both pins fail on the pre-fix source** (7.7 s and 18.1 s against a 2.0 s ceiling)
rather than assuming they would.

### B4 — S13 is a recurrence, and the half that is mine was a convention, not a constraint

R16's one trace-derived value is `metrics["tool_name"]`, guarded by a *shape*
pattern. `AKIAIOSFODNN7EXAMPLE` and `sk-ant-api03-…` match it exactly and reach
`metrics` verbatim. The tester is right that R16 is doing what it says; I disagree
that this is entirely increment 4's problem, for two reasons.

First, it is a **recurrence of a known class**: increment 1's B5-adjacent finding
was a guard that accepted an AWS key id as a "safe" class name. When the same shape
appears twice in two increments, treating the second as somebody else's ticket is
how it appears a third time.

Second, the increment-2 half was not sound. R16's guarantee — "a reader skimming
the findings table is reading bytes this codebase authored" — was enforced by each
detector remembering to call `constrain_tool_name`, and by nothing at all for any
other metrics key. A future detector putting trace text under a new key would have
passed every test on the branch. That is exactly the shape the Modularity notes
forbid ("guards are properties of functions, not of call paths").

**Fixed** (`ef16cf8`), in three parts, none of which amends the spec:

* `TRACE_DERIVED_METRIC_KEYS` names the exception, so increment 4's renderer
  consumes a machine-readable set rather than a sentence in R16's prose.
* `Finding` now **refuses** any other string metric that is not a slug this package
  could have written, and refuses a `tool_name` that is not R16-constrained. Both
  arms pinned: the legal slug constructs, the payload does not.
* The canary ledger carries `metrics_redaction_dropped` as an increment-4 debt.

The residual exposure is asserted rather than hidden — a test states that a
credential-shaped legal tool name *does* reach `metrics.tool_name`. It deliberately
does **not** cite the increment-4 requirement ids: a citation the R52 check reads
would mark them covered by a test that does not test them, which is this project's
signature defect wearing a traceability badge.

---

## [IMPROVE] findings

All six came from my own mutation sweep or from probing the negative question.

### I1 — R19's "first emitted tool call" was untested, and it is not cosmetic

Replacing `setdefault` with an assignment in `first_tool_call_by_parent` — so a
model call's signature is its *last* tool call rather than its first — left the
entire suite green. Over 4,000 randomized traces the two forms disagree about the
findings on **512 of 16,000 probe runs**.

The existing R19 case could not see it because every model call in it emitted the
same pair, so first and last were both constant and both produced a loop. **Fixed**
(`1ee6042`) with a pair that holds the first tool call fixed and varies the second:
reading the first finds a period-1 loop repeated three times, reading the last
finds nothing.

### I2 — A-b11 was stated in the PR and asserted nowhere

Hashing the caller's raw `span_seqs` instead of the normalized list left the suite
green, because no test handed in a list the normalizer actually changed. **Fixed**
(`6def3aa`) with three that do — unsorted, duplicated, and past the fifty-span cap
— and with an up-front assertion that the two payloads hash differently, so the
test cannot pass by them being the same value.

### I3 — `millis_between` truncation versus rounding was unobservable

Every timestamp in the suite and the corpus lands on a whole millisecond, so
`microseconds // 1_000` → `round(microseconds / 1_000)` survived. The two disagree
at 60,000,999 µs, which under rounding clears R23's default threshold that
truncation leaves one millisecond short of — and rounding puts a float division
into the one path A-b1 went out of its way to keep integral. **Fixed** (`35e1e75`).

### I4 — a detector could name the wrong agent, or index a span that does not exist

`spans_by_agent` read `AgentRun.span_seqs` and indexed straight into `trace.spans`,
relying on three things **R2 does not require**: that the entries are in range, that
the span named belongs to that agent, and that `agent_id` is unique across
`AgentRun`s. Reproduced, all three:

```
out-of-range span_seq   -> IndexError out of a detector (R13 says pure function)
crossed span_seqs       -> blocked_agent reports two different agents' spans as one agent's wait
duplicated agent_id     -> the second AgentRun silently erases the first from every per-agent detector
```

The v1 mapper honours all three, which is exactly why depending on them unstated is
increment 1's defect wearing a new hat — and R3 is the seam that makes it
reachable, since a second adapter is meant to be addable with zero edits to
`detect/`. **Fixed** (`b64e628`); behaviour on mapper output is unchanged, verified
by the full corpus findings digest being the same value.

### I5 — collection floors were below the real counts

Same correction as increment 1's I7: a floor below the count silently exempts every
test above it. Raised to the exact counts (`18a686a`).

### I6 — my own performance pins were noise-fragile, and the growth check had an exemption list

Worth recording because I shipped it and then caught it. With the fixed code the
small case runs in ~0.004 s, so `large < small * 8` is a coin flip on a loaded
runner: it passed three times in isolation and failed once in a full-suite run on
3.12. **A check that fails at random is one people learn to re-run rather than
read** — the same disease as a check that cannot fail, in the other direction.

The denominator is now floored at 20 ms, which does not weaken the guard (the
quadratic form's small case took 0.43 s and 0.53 s, nowhere near the floor). And
the whole-registry growth check no longer carries an exemption list for the two
detectors that *were* quadratic: an exempt-list written to record a known bug is
how a defect becomes permanent. Fixed in `e14429a`.

---

## [NIT] — comments only, nothing changed

- **N1** `blocked_agent` still builds an interval list per agent, so it is
  O(agents × spans) even after B3. Two agents makes this invisible; fifty would not.
  Profiling the fixed 10,000-span case puts 0.024 s in the union build, 0.029 s in
  the 10,000 queries, and nearly all of the remaining 0.24 s in constructing the
  ~10,000 findings the trace legitimately produces — so the remaining term is real
  but small, and I am leaving it rather than reshaping the interval plumbing for a
  shape no observed trace has.
- **N2** The tester's observation 3 is right and under-stated: `retry_storm`'s waste
  excludes the model call that preceded the first error, because A-b5 trims the run
  to its errors. That is the correct behaviour, it changes a dollar figure in
  increment 3, and **A-b5 is not in the spec** — R20 says "every `model_call` span
  in the merged run" without saying what the merged run is trimmed to. See S10.
- **N3** Recorded so a future sweep does not spend a day on them: the R23 clip and
  union-merge comparisons, the `_other_intervals` `end > start` filter, and (new
  with B3) `CoverageIndex`'s first-run `bisect_right` are all dead defensive code —
  each mutates to an equivalent program. They are cheap and correct; I am not asking
  for their removal. The same is true of R19's `last_start`, R20's early-exit
  threshold, R22's `elif`, and R24's `abs` symmetry.
- **N4** `repeated_tool_call`'s `metrics.last_seq` can name a span outside the
  capped `span_seqs` evidence list when a group exceeds 50 occurrences. Correct per
  R14 and R18 read together, and worth one sentence in increment 4's renderer so a
  metric that is not a link is not mistaken for a broken one.
- **N5** R2 does not constrain `AgentRun.span_seqs` to be in range, owned, or
  paired with a unique `agent_id`. I4 enforces it in `detect/` because that is where
  the damage lands and because R2 belongs to increment 1, but the model is the right
  home for the invariant. **PM: consider it for the R2 amendment queue** — it is one
  clause in an existing validator.
- **N6** The PR's "Tester surface" section is genuinely useful and one line of it is
  wrong: it says `merge_runs` should be tested "including adjacent-but-not-overlapping
  ranges", which is precisely the case the implementation got wrong. The coder wrote
  down the test that would have caught his own bug and handed it over instead of
  writing it. Not a defect; a striking illustration of why the handover section is
  not a substitute for a test.

---

## [PRAISE]

- **P1 — the fixture/expectation contract can fail, and I proved it six ways.** On a
  throwaway copy: an altered metric, a `fires` arm flipped, a `fires: true` with an
  emptied `findings` list, a dropped detector key, one byte changed in a fixture
  `.jsonl`, and an altered severity. All six went red; the control run is clean, and
  clean again after restore. Pinning `finding_id` in the expectation files (A-b12)
  is what makes the last one work, and the decision not to ship a regenerator is the
  right kind of friction. This is the second thing in this repo demonstrated capable
  of failing before anyone relied on it.
- **P2 — the tester's eight equivalent mutants are genuinely equivalent.** I
  re-derived each by argument and then confirmed all eight differentially against a
  corpus I wrote myself: 4,000 randomized traces × 4 configs, 16,000 probe runs,
  zero differences on every one. A wrongly classified equivalent mutant is a hidden
  bug, and there are none here. (The *method* is a separate matter — see the
  adjudication.)
- **P3 — `tests/synthetic_traces.py` is the right tool built for the right reason.**
  Both sides of a boundary at one integer of diff, and a door to the two shapes the
  mapper cannot emit. Every fix in this review used it.
- **P4 — `CORPUS_FINDINGS_DIGEST` checked in.** The tester turned the increment-1
  addendum's `str.isprintable()` finding into the one automated check that makes
  CI's two matrix legs disagree out loud. That is a review lesson converted into a
  guard rather than into a paragraph.
- **P5 — R22's carve-out is positional and matched to the mapper character for
  character.** The tester checked the other half of the coder's claim rather than
  taking it, and found no span that falls between the two rules. That is the right
  instinct applied to the right place.

---

## Rulings

I cannot amend the spec. Each ruling states exactly what the PM should change.

### S7 — R19 says both "at most one finding per agent" and "multiple loops per agent"

**Ruling: the resume clause is operative. The coder and the tester are both right,
and there is a third piece of evidence neither cited that settles it.**

The tester's two arguments are good — the mechanical sentence beats the outcome
sentence, and the outcome reading makes the resume clause dead. The decisive one is
**AC8**, which is a ratified acceptance criterion rather than a sentence in R19:

> Given `A A A A A`, Then one finding fires with `period == 1`, `repeats == 5`,
> severity `critical`.

`A A A A A` matches at starts 0, 1 and 2. Under the "one per matching start index"
reading that is three findings; AC8 says one. So the first sentence of R19 was
reaching for exactly the charitable reading the coder gave it — *not one finding per
matching start index* — and an acceptance criterion already encodes that. There is
no reading under which "at most one finding per agent" is a separate requirement
that AC8 does not already cover.

**PM: delete "At most one finding per agent" from R19 and replace it with "one
finding per detected loop, never one per matching start index."** Both behaviours
are pinned by tests today, so whichever way you rule, a test has to change
deliberately.

### S8 — R22's `orphan_result` cannot name an agent

**Ruling: amend R22 to trace-scoped. Do not amend R2. The tester's reasoning is
correct and I verified its premise independently.**

R12 emits one span per `tool_use` block, and a user record consisting only of tool
results emits no span, so an orphan result leaves no span behind; R10's aggregated
warning carries a count and neither an agent nor a seq. The model cannot supply the
agent half of R22's `(agent_id, reason)` key.

The tester's addition is the important part and I endorse it: the model field is not
merely the more expensive option, it is the **wrong** one. An orphan result is by
definition a result whose owning call is absent from the trace. Any agent attached
to it would be the agent of the record that *carried* the result, not the agent that
*made* the call — a different fact, presented as the one the reader wants. A schema
version bump to record a misattribution is worse than an empty tuple that says
"unattributable".

One consequence neither agent stated, and increment 4 needs it: **this is the only
finding in v1 with `agent_ids == ()`**, so the renderer must not assume every
finding has at least one agent when it groups or filters by `data-agent` (R34's
attribute allowlist). Worth a sentence in the amendment.

**PM: amend R22** — `orphan_result` fires once per trace with empty `agent_ids` and
`span_seqs`, and note the renderer consequence.

### S9 — R24's population floor: one gate or one per dimension

**Ruling: the coder's reading is the literal one, the tester is right that it
contradicts R24's own rationale, and the PM should rule for per-dimension. I
reproduced it.**

```
8 model calls with usage, 3 of them timed, one of the three 500 s long
-> critical {'dimension': 'duration', 'median': 100, 'mad': 0, 'value': 500000}
   summary: "…against a median of 100 and a MAD of 0 over 3 calls"
```

The product announces the contradiction in its own summary string. R24's floor
exists because "below that, 'the median' is a description of three or four numbers
and calling one of them an outlier is noise" — and this is a finding declared
against a measured population of three, from a trace where the *tokens* dimension is
correctly suppressed.

Two things to add to the tester's account. First, the per-dimension gate is
**strictly conservative**: it can only remove findings, never introduce one, which
is the right direction for a detector whose failure mode is noise and whose R48
positive arm is already satisfied by the tokens dimension. Second, the fix is not
quite "one line and one fixture" — the floor has to apply to `measured` rather than
to `population`, and the new fixture needs eight *timed* model calls, which is a
larger fixture than the corpus currently holds anywhere.

**PM: amend R24** to state the floor applies per dimension, over the spans actually
measurable in that dimension. The current behaviour is pinned, so the amendment
lands as a visible test change.

### S10 — R20's merge rule: adjacent or overlapping

**Ruling: only windows that share at least one span merge. That is the reading I
implemented as B1, and R20's own metrics make the alternative incoherent.**

The tester frames this as genuinely two-sided. I do not think it is, and the
argument is not about the word "overlapping" — it is about what the other three
clauses of R20 would then mean:

* `window_spans` is *named* for the window. Under "merge everything a sliding
  window touched", it measures a run of arbitrary width and the name is a lie.
* `errors` is a within-window density — that is R20's entire distinction from R21
  ("that one reports *existence*, this one reports *density*"). A count over a
  twenty-span run is not a density claim about a ten-span window.
* The severity ladder (3–4 warning, ≥ 5 critical) is calibrated to what fits inside
  ten spans. Unbounded run width makes `critical` reachable by two unrelated
  three-error clusters at opposite ends of a turn, which is what the defect actually
  did.

So the "merge everything" reading is not a second coherent design; it is the first
design with three of its own clauses broken. **PM: amend R20** to say (a) only
windows sharing at least one span merge, and (b) the reported run is trimmed to its
first and last error span, so `window_spans` measures the storm rather than the
window (this blesses A-b5, which is currently a PR note carrying a dollar figure —
see N2).

### S11 — R20's sub-ten-span agent

**Ruling: the tester is right that A-b4 is a behaviour change the spec does not
authorize, and right that the spec should authorize it rather than that the code
should revert.**

One addition, because it changes this from a tidiness point to a suite-integrity
point. Under the literal reading `retry_storm` is *structurally* unable to fire on a
short agent — five spans, four of them errors, silently clean. That does not merely
lose findings: it means R48's **silent arm** for `retry_storm` can be satisfied by a
fixture that could never have fired for reasons having nothing to do with its
content. A negative arm that passes because firing is impossible is precisely the
"green because it cannot fail" shape R48's two-arm design exists to prevent, and it
would be invisible in the expectation files.

**PM: add one sentence to R20** — when an agent has fewer than ten spans, its whole
span list is the window. The implementation already does this; it is the `max(1, …)`
in the window-start range, which is easy to miss on a read.

### S12 — R23 skips a gap when an untimed span sits between two timed ones

**Ruling: the defect is real, the tester's premise about *why* is wrong, and the
correction makes it more worth fixing rather than less.**

I reproduced the behaviour exactly as reported:

```
two timed spans, 600 s apart              -> 1 finding
the same, with an untimed span between    -> 0 findings
```

But the stated cause — "a `user_message` span has no timing by construction" — is
false for the v1 adapter. `mapper._user_message` sets `start = end = item.when`; so
does `_system_event`. That claim comes from `TraceBuilder.user_message()`, which
takes no timestamps — a property of the tester's own helper, generalised to the
product. Across all 26 fixtures there are exactly **two** spans with a missing
endpoint, and both are `tool_call` spans with `tool_result_status == "missing"`.

So the real blind spot is narrower and sharper than "next to any user turn": **a
tool call whose result never arrived hides the gaps on both sides of it.** That is
the single most likely cause of a genuine stall — a tool that hung — and R23 is the
detector for stalls. The mid-trace unresolved call that `unresolved_tool_call` fires
on is the same span that makes `blocked_agent` go quiet about the wait around it.

**PM: amend R23** to say whether the pairing looks *through* spans with no timing to
the nearest timed neighbours. I recommend yes, and the amendment should say which
seq the finding then names.

### S13 — a credential-shaped string that is a legal tool name

**Ruling: not a defect in R16's implementation, agreed — but not wholly increment
4's problem either, and the half that was mine is fixed here (B4).**

The tester's analysis is correct and the pin is the right one. Where I differ is the
disposition. This is the second appearance of the same class in two increments
(increment 1: a guard that accepted an AWS key id as a "safe" class name), and
"increment 4 must remember" is not a control. What was in scope and is now done:
the trace-derived key is named in data, every other metrics string is refused by
`Finding` itself, and the canary ledger carries the increment-4 debt.

Two amendments are still the PM's:

**PM: amend R33** to say redaction is applied to every trace-derived string reaching
a report, naming `Finding.previews` **and** `Finding.metrics` string values under
`TRACE_DERIVED_METRIC_KEYS`. As written, R33 says "applied to every trace-derived
string" and R51's probe asserts credential payloads "do not appear at all", and
nothing connects either to the findings table.

**PM: amend R16** with one sentence saying its pattern is a *shape* constraint and
explicitly not a secret check. The Security considerations section currently reads
"a reader skimming the findings table is reading bytes this codebase authored",
which is true of every value except the one it is about — and that is the sentence
a renderer author will act on.

---

## The mutation-methodology adjudication

This is the finding of the increment, and it is about the verification method rather
than the code.

### What each of us measured

| | mutations | survivors | what the number is a measurement of |
| --- | --- | --- | --- |
| coder | 46 | 0 | the 46 mutations the coder thought of |
| tester (handed-over branch) | 119 | 32 | the 119 the tester thought of |
| tester (after their tests) | 119 | 8 | the same 119 |
| reviewer (handed-over branch) | 110 | 17 | the 110 I thought of |
| reviewer (this branch) | 110 | **10, the identical set on 3.11 and 3.12** | the same 110 |

All five rows are true statements about the same branch. That is the problem.

### The ruling

**A self-reported mutation score against a self-chosen mutation set is a check that
cannot fail, and it is this team's signature defect relocated from the code into the
verification methodology.**

The mechanism is not dishonesty; it is worse than that, because it survives good
faith. The mutation set is chosen by the same mental model that wrote or read the
code, so the mutations that get written are the ones whose failure mode the author
already had in mind. 46/46 was not a lie — it was a correct measurement of a set
selected by someone who was thinking about thresholds, because thresholds are what
R18–R24 pin. Nothing in "46 of 46 caught" tells a reader that `detect/base.py`
received **zero** mutations, and `detect/base.py` is the module every detector
depends on.

The tester was right to widen the set and right to say the coder's number was "not
disputed but not sufficient". But the tester's 119 has the same structural property,
and the evidence is concrete: my 110-mutation set put 40 mutations into
`detect/base.py`, and **nine survivors turned up that neither prior sweep reached**.
Six of the nine are in that one module. One of them (`first_tool_call_by_parent`)
changes real findings on 512 of 16,000 probe runs — a behaviour difference, not a
theoretical one, in the module both sweeps skipped because the requirement text does
not live there.

### The second, subtler half: "differentially proved equivalent" is not a proof either

The tester's eight equivalents are genuinely equivalent — I confirmed every one. But
the *method* used to establish that has the same defect as the mutation set, and I
walked into it myself.

My differential harness reported **EQUIVALENT** for "`millis_between` rounds instead
of truncating", over 4,000 traces × 4 configs. It is not equivalent. It looked
equivalent because my generator only produced whole-millisecond timestamps, and a
rounding-versus-truncation difference requires a sub-millisecond remainder. It took
a hand-built counterexample — 60,000,999 µs — to see it.

"200,000 random layouts, 0 differences" is therefore only as strong as what the
generator can generate, and a randomized corpus is itself a self-chosen input set.
An equivalence claim needs its generator's **input space** stated next to the trial
count, or it is a confident number with an unstated precondition.

### What the PM should amend

The fix is not "run more mutations" and it is certainly not "add a third reviewer".
It is to make the mutation set the same kind of object as `collection_floor.json` and
`allowed_skips.txt` — checked in, diffable, and only ever growing. Four amendments:

1. **Add a requirement (R53) that specifies the sweep instead of leaving it
   improvised.** It should name (a) the **operator set**: relational flips
   (`<`↔`<=`, `>`↔`>=`), ±1 on every integer constant, boolean connective swaps,
   slice and `range` bound off-by-ones, dropped conjuncts and guard clauses, dropped
   components of composite group keys and sort keys, swapped call arguments,
   `while`→`if`, and dropped `sorted`/`set`/`setdefault` normalizations; and (b) the
   **target set**: every module under the increment's packages, with a **minimum
   mutation count per module**, so a shared-helper module cannot score 46/46 by
   receiving nothing. That per-module floor is the single clause that would have
   caught this increment.

2. **Check the mutation set in**, as `tests/mutations.json` or equivalent, with the
   survivors recorded beside it as a ledger: each survivor carries either
   `equivalent` plus its evidence, or `open` plus the requirement it threatens. The
   next increment's sweep is then a **re-run**, not a re-invention, a shrinking
   survivor list is visible in a diff, and "we ran a mutation sweep" becomes a claim
   with an artifact behind it. This is exactly the shape R49 already uses for skips
   and floors, and it is the only version of a mutation score that cannot be gamed
   by choosing the set — because the set is no longer chosen per run.

3. **Require an equivalence claim to state its generator's input space**, not just
   its trial count. "20,000 random layouts" is not evidence until the reader knows
   the layouts could contain the distinguishing input. Where a hand-built
   counterexample is cheaper than a generator — as it was for all three of the real
   gaps I found — the requirement should prefer it.

4. **Run the checked-in sweep in CI**, at least on one interpreter and at least over
   the modules the increment touched. Until then this remains something an agent does
   once, at the end, and reports a number for — which is the definition of a check
   that reports green because nobody can re-run it.

One honest note on my own position: **my 110 are also self-chosen.** I found what I
found because I aimed at the module the others had not, and the next reviewer aiming
somewhere else will find more. That is not an argument for a fourth sweep; it is the
argument for amendments 1 and 2, which replace "whatever this agent thought of" with
"the declared set, plus whatever anyone adds".

### Harness discipline, already learned twice

The coder found that same-length edits written inside one second leave a `.pyc`
whose `(mtime, size)` is unchanged, so CPython runs the previous source. The tester
found that an in-memory restore in a `finally` leaves a mutated file as the next
run's baseline if the process is killed. I hit a third: **the two interpreters
disagreed on six mutants**, and the cause was not the interpreter — it was my own
noise-fragile timing pin flaking under load (I6), turning six equivalent mutants into
apparent kills on the leg that happened to be busier. A timing assertion inside a
suite used as a mutation oracle is a non-deterministic oracle.

All three belong in R53 as harness requirements: purge `__pycache__` around every
mutation, run with `PYTHONDONTWRITEBYTECODE=1`, restore with `git checkout --` in
the `finally`, and **exclude timing-based assertions from the oracle** (or run them
with a floor wide enough that load cannot flip them, which is what `e14429a` does).

---

## What I verified myself

Because the brief asked me not to trust either agent's claims, and because "the
check could have failed" is the property this project is organised around:

**The three reported bugs, reproduced from the report's own repro code**, before any
fix: `merge_runs([(0,10),(10,20)]) == [(0,20)]`; `retry_storm` 0.035 s → 9.15 s from
1,250 to 20,000 error spans; `blocked_agent` 0.15 s → 8.30 s from 1,250 to 10,000
spans. All three exactly as described.

**The fixture/expectation contract, tripped six ways** on a throwaway copy, with a
clean control before and after. See P1.

**The tester's eight equivalent mutants**, each re-derived by argument and confirmed
differentially over 4,000 randomized traces × 4 configs. Zero differences on every
one; none is a hidden bug.

**110 mutations of my own**, on both interpreters, with the harness discipline both
prior agents recorded (`__pycache__` purged around every mutation,
`PYTHONDONTWRITEBYTECODE=1`, `git checkout --` in the `finally`).

* Against the handed-over branch: **91 killed, 17 survived**.
* Against this branch: **100 killed, 10 survived — the identical set on 3.11 and
  3.12**, which is the confirmation that the earlier six-mutant disagreement between
  the interpreters was my own timing pin and not the product (I6).
* All ten survivors re-checked differentially against a corpus I wrote myself —
  4,000 randomized traces × 4 `blocked_gap_seconds` values, 16,000 probe runs — and
  every one is equivalent. They are the eight the tester classified, plus R20's
  early-exit threshold (which can only skip work no window could have used) and
  `CoverageIndex`'s first-run search, introduced by my own B3 fix and equivalent for
  the same reason as the R23 pair: a run ending exactly at the window start covers
  nothing.

The seven mutations that moved from *survivor* to *killed* are the six gaps this
review pinned plus R20's window-stop clamp, which the B2 rewrite made observable.

**Both performance pins against the pre-fix source**, to confirm they fail: 7.71 s
and 18.09 s against a 2.0 s ceiling.

**The `$`-versus-`\Z` newline trap** from increment 1's B5, in the detector layer:
`constrain_tool_name` uses `fullmatch` (so `"Bash\n"` correctly becomes
`<non-conforming>`), and the two pydantic patterns in `detect/base.py` compile under
the Rust engine, which rejects a trailing newline where Python's `re.match` accepts
it. The engines still disagree; nothing in `detect/` depends on the disagreement.
Confirmed no `Finding` can carry a newline-bearing detector slug or id.

**The Unicode/`isprintable` trap** from the increment-1 addendum: nothing in
`detect/` calls `isprintable`. The one Unicode-table-dependent call is
`str.lower()` in `looks_like_unknown_tool`, matching against five pure-ASCII
phrases. The checked-in `CORPUS_FINDINGS_DIGEST` is identical on both interpreters,
which is the guard that would catch a drift here.

**R48's two arms, recomputed from live runs** on the final branch: 6/20, 3/23, 2/24,
6/20, 3/23, 4/22, 4/22 — matching the coder's and tester's independently reported
counts, and unchanged by the B1 fix.

**The "wrong agent or nonexistent span" question**, asked as a negative: three ways
existed and all three are closed (I4).

**S9 and S12, reproduced from the trace up**, including the mapper reading that
corrects S12's stated cause.

---

## Increment-2 assessment

The detector layer is a good transcription of a tightly pinned specification, and
the parts the spec did *not* pin are where everything went wrong — which is the most
useful thing this increment could have taught. R18–R24 name their thresholds, their
metrics keys and their severity ladders, and every one of those is correct. R20 does
not say what "overlapping" means for half-open ranges, does not say how wide a
merged run may be, and does not say what a nine-span agent's window is; all three
became defects or unauthorized assumptions. R23 does not say what happens to an
untimed span in the middle; that became a blind spot around exactly the shape it
exists to detect. R24 does not say whether its floor is per dimension; that became a
finding whose own summary contradicts the requirement's rationale. Six of the seven
spec rulings in this review are the same sentence in different clothes: **the
requirement pinned the arithmetic and left the structure implicit.**

The corpus is the branch's best work. A checked-in expectation file per fixture,
naming every registry slug in both directions and pinning `finding_id` so a mapper
change surfaces as a diff, is a real answer to increment 1's finding that no
checked-in input could distinguish two behaviours of R9. I broke it six ways and it
noticed every time.

And the corpus is also where the increment's limit shows, which is worth stating
precisely because it is not a criticism of the corpus. BUG-1 is invisible to it.
BUG-2 and BUG-3 are invisible to it. Six of my nine new survivors are invisible to
it. A fixture corpus proves the detectors discriminate *between the traces someone
wrote*; it cannot prove anything about the trace nobody wrote, and the three most
serious defects in this branch all live there. The tester's `synthetic_traces.py`
and the randomized differentials are the tools that reach that space, and they
arrived as test infrastructure rather than as a requirement. **R53 is where they
should live** — the same argument that put T5's harness in increment 1 rather than
increment 5.

The team's process improved measurably. The tester genuinely re-verified rather than
re-stated, caught the coder's insufficient sweep, and expressed three bugs as strict
xfails so no fix could land without the ledger moving — it worked exactly as intended
on all three. The coder flagged his own contradictions honestly and handed over the
gap he knew he was leaving. What neither could do is the thing no participant can do
alone: notice that the number they were both reporting was a measurement of their own
imagination. That is what a review is for, and it is why the mutation set belongs in
the repository rather than in a report.

Merge once the PM has the amendments queued: **S7–S13**, the R2 clause from N5, and
**R53**. None blocks this branch. R53 blocks increment 3 in the sense that matters —
without it, the next increment's cost engine will arrive with a mutation score its
author chose, and the two most expensive numbers in the product (`wasted_cost_usd`
and the trace total) will be verified by exactly the check this review is about.
