# Review: swarm-observer increment 3 (`feature/so-i3`)

Reviewer: reviewer-agent · Diff under review: `git diff feature/so-i2...feature/so-i3`
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED — not editable by me)
Under review: `docs/prs/feature-so-i3.md` (A-c1…A-c15, S14–S19),
`docs/test-reports/feature-so-i3.md` (BUG-1…BUG-7, S20–S23)
Prior rulings that bind me: `docs/reviews/feature-so-i2.md` — in particular the
mutation-methodology adjudication and its four amendments.

## Verdict

**Merge, once the PM has queued the amendments.** The seven reported bugs are
all real, all now fixed with pinning tests, and the fixes are in this branch as
`review:` commits. The engineering underneath is the best this project has
shipped: the cost engine's `Inexact` trap, the R30 priority ladder written as
one `return` per rung, and the coder's refusal to self-report a mutation score
are each the right call made for the right reason.

Two things stop this being an unqualified pass, and neither is a code defect:

1. **The rate table is unverified and one cell is very probably wrong.**
   Sixteen of seventeen rows follow a single ratio family in all four derived
   columns. `claude-fable-5-1` and `claude-mythos-5-1` break it in exactly one
   cell, in the one column that dominates agent traces. See §6.
2. **BUG-2 was the third occurrence of this project's worst class**, and the
   third occurrence was found by the tester rather than prevented by the
   guard the first two produced. Increment 4 renders every one of those bytes
   into HTML.

---

## 1. Blockers found and fixed

All seven of the tester's bugs are confirmed and fixed. Every fix landed with
its pinning test in the same commit, every one of the tester's twelve strict
xfails is resolved, and every "reproduces today" test is replaced rather than
deleted — a fix that turned one wrong behaviour into a different wrong
behaviour would still be red.

| Commit | Bug | What was wrong | What the fix pins |
| --- | --- | --- | --- |
| `review(R11, R29, R39)` | **BUG-1, BUG-7** | Two unrelated inputs put a raw exception past `main()` → traceback, exit **1** | Three call sites, plus a floor under `main` that is driven directly |
| `review(R11, R39, R40)` | **BUG-5, BUG-6** | `--json /dev/null` destroys the device node and reports success; the success line was the one unsanitized output in the package | "not a regular file" (not "is a FIFO"); every C0 code point plus DEL |
| `review(R33)` | **BUG-3** | `redact` deleted report text on a second pass and could erase an earlier marker | Idempotence with no exemption list; the generator's premise re-established against a reverted arm |
| `review(R33)` | **BUG-2** | Trace-derived identifiers reached `report.json` unredacted in **both** modes | A single `identifier()` boundary; five paths asserted by path, not by substring |
| `review(R33, R38)` | *(reviewer)* | The leak sweep could not see the findings section, and its ledger could be widened to silence itself | Section non-emptiness; a size ledger; a credential sweep that names no paths |
| `review(R17, R31)` | **BUG-4** | The per-detector token column contradicted the findings it aggregates | R17's quantity, with `wasted_unpriced` decomposing it, as a corpus invariant |

### 1.1 BUG-1 + BUG-7 — the fail-closed floor

The tester's framing was right and I have followed it: *a per-call-site fix
invites a third*. But a floor **alone** is the wrong answer, and this is the
part worth stating clearly. R39 pins a bad output path as exit **3** and a
fail-closed condition as exit **2**. Catching a NUL in `main` renders it as a
2 and destroys the distinction the whole taxonomy exists for. So the fix is
both:

* **Call sites (three, not two).** `price_usage`'s `quantize_cost` moved
  inside the `DecimalException` guard — with one non-zero component the
  multiply and the divide stay exact, so the quantize was the first step that
  could exceed `COST_PRECISION`, and it sat outside the `try`.
  `check_output_path` answers a NUL with the exit 3 its own docstring already
  promises. And a **third** site the report did not name: `expand_inputs` asks
  `is_dir()` before the reader is reached, and `resolve_inputs`' `is_symlink()`
  comprehension raises before its own per-path loop — so guarding the reader's
  loop alone still crashed. That third site is the report's own prediction
  coming true inside the fix.
* **The floor.** `main` gains a final `except Exception` → one sanitized line,
  exit 2. It names the exception's **type** and never `str(exc)`: an
  unsanitized message from pydantic or `json` quotes the input, which is the
  byte of file content R11 forbids on stderr. It catches `Exception`, not
  `BaseException`, so `^C` is not reported as a malformed trace.

A catch-all is the exact habitat of this project's signature defect — a guard
that reports green forever because nothing reaches it. `TestTheFailClosedFloorR11R39`
therefore drives it from three exception types, asserts it does **not** swallow
the three typed clauses above it (a floor placed too high passes "exit 2 with
one line" while destroying the taxonomy), asserts it never echoes the exception
text, and pins its upper bound.

I also widened the BUG-1 test beyond the reported reproduction:
`test_r29_the_quantize_is_inside_the_guard_for_every_component` drives all five
usage components, because `input_tokens` was only the component the repro
happened to use.

### 1.2 BUG-5 — the writer

`atomic_write_texts` ends in `os.replace`, which replaces a FIFO, socket or
device node with a regular file. The reader has refused a non-regular *input*
since increment 1 (`not_a_regular_file`); the writer now matches.

The guard is over "exists and is not a regular file", **not** over `is_fifo()`.
A socket and a symlink to one are the same condition reached differently, and a
guard written against the node type in the bug report is precisely the "correct
for the call path that exists" shape. The non-vacuous arm is asserted too: an
existing regular file must still be overwritten, since re-running `analyze`
over the same report is the commonest invocation there is.

### 1.3 BUG-6 — the success line

Every `UsageError` and every fail-closed line on stderr goes through
`_one_line_safe`; `summary_line` did not. `_one_line_safe` deliberately
preserves `\n` (its callers render multi-line argparse usage), so the newline is
removed at the call site, where the requirement is *one line* and not merely a
safe one.

The sweep drives every C0 code point plus DEL rather than the four the report
named. The arm that keeps the fix honest is
`test_r40_the_report_is_written_at_the_path_the_user_actually_typed`:
neutralizing the control character in the **path** rather than in the **line**
would satisfy every other assertion in the class while writing the report
somewhere the user did not ask for.

### 1.4 BUG-3 — redaction idempotence

R33 pins two things that are in tension: the pattern table *verbatim*, and that
`redact` is idempotent. The table is not idempotent — `secret_assignment`'s
bare `\S{6,}` alternative re-matches its own marker together with whatever
follows it without a space.

The pattern is left untouched and the fix is in the **replacement**, which R33
describes only as "the name is preserved, the value replaced": a value that
already begins with this function's own marker is returned unchanged. Verified
over the tester's own 40,000-string generator (zero counterexamples, from
40,000) with **no change to any first-pass output** anywhere in the adversarial
corpus.

The cost is stated in the docstring rather than hidden: a trace containing the
literal `NAME=[redacted:secret_assignment]<secret>` shields that one value. I
accept it. Redaction is a courtesy, not a boundary — an adversary who controls
the trace need only avoid a secret-shaped *name*, which costs them nothing —
and silently deleting report text is the worse of the two failures. The guard
recognises this label's marker only, so `TOKEN=[redacted:aws_key_id]rest` is
still redacted; that arm is tested, because "any marker" would have widened the
fix into a hole.

Two things about the tests are as important as the fix.

* `test_r33_the_hand_written_corpus_is_idempotent` carried a **named exemption
  list** of three strings. I deleted the list rather than emptying it: a list
  of cases a property does not hold for is a check that can be widened.
* `test_r33_the_generator_can_produce_a_distinguishing_input` was one commit
  away from becoming the eighth signature defect. It asserts the generator
  **can** produce a counterexample — a premise the fix makes structurally
  impossible to satisfy. It now re-establishes that premise against a
  deliberately reverted replacement built from the shipped pattern table, in
  the same shape `tests/canaries/` already uses.

### 1.5 BUG-2 — the third occurrence of the worst class

`agent_id`, `parent_agent_id`, `findings[].agent_ids` and
`ParseWarning.detail` are trace-derived — R5 takes an agent id straight from
the record's `agentId`, R4 puts the unknown record *type* into a warning's
detail — and they reached `report.json` through neither the redactor nor
`--no-previews`. `AKIAIOSFODNN7EXAMPLE` matches R2's agent-id alphabet exactly.

Increment 1 found trace-derived fields bypassing redaction. Increment 2's S13
found credential-shaped **legal tool names** reaching `metrics.tool_name`
verbatim. This is the same mistake a third time, made about fields whose *type*
looks like an identifier rather than like text. The tester is right to weigh it
heavily and I weigh it more heavily still: increment 4 renders every one of
these into HTML, and the S13 ruling that should have generalised did not.

`json_out.identifier()` is the new boundary: R33 redaction, in **both** modes,
for a trace-derived string the document also uses as a key. It deliberately
does **not** blank under `--no-previews` — see the ruling on S16 in §4.

`findings[].agent_ids` was **not** in the tester's bug report, and that is the
subject of the next section.

### 1.6 The eighth signature defect — `LEAKING_PATHS`

The tester asked whether `LEAKING_PATHS` is adequately guarded. **It is not**,
in two distinct ways, and both are the signature defect.

**A fixture with no case that could trip it.** The sweep rendered its document
with `findings: []`. A section that renders nothing contributes no paths, and
therefore cannot contribute a *missing* one either — so the entire findings
section, the part of a report a reader trusts most (R16), sat outside the set
comparison that exists to notice a new trace-derived field.
`findings[].agent_ids` was reaching `report.json` raw for the whole increment
with nothing going red. `sentinel_findings()` fixes the fixture;
`test_r38_the_sweep_reaches_every_section_of_the_document` fixes the class.

**A ledger that can be widened to silence itself.** The set comparison goes red
when a new field escapes, and green again the moment somebody adds that field's
path to the list — the same shape as a self-chosen mutation set, a check whose
subject is chosen by whoever needs it to pass. `LEAK_LEDGER_SIZE` pins the size
as a literal, so widening costs two edits in two places with the reason written
between them. That is the friction A11 already puts on `collection_floor.json`,
and it is the most a single repository can do about a ledger.

**The arm that cannot be silenced at all.**
`test_r33_no_credential_shape_reaches_the_document_at_any_path` names **no
paths**, so there is nothing to widen. It renders the same trace with a real AWS
key shape in every field a sentinel occupies — legal in all of them, since
`AKIAIOSFODNN7EXAMPLE` satisfies R2's agent-id alphabet, R16's tool-name pattern
and R10's detail-slug shape — and asserts the shape appears nowhere in either
mode. It has its own control arm, because "this string is absent" is satisfied
perfectly by a renderer that emits nothing.

Running it turned up one more field carrying a raw credential:
`meta.source_files[].name`. That is the tester's **S21**, and I ruled on it
here rather than deferring — see §4.

### 1.7 BUG-4 — the per-detector waste column

`DetectorWaste.wasted` summed only the *priced* attributed spans, so one
finding attributing a 3,000-token priced call and a 4,000-token unpriceable one
produced a finding reporting 7,000 and a detector row reporting 2,000, in the
same document, with nothing marking the second as a subset.

The spec does not say which reading wins (S22), so this is a ruling as much as
a fix: **a field named `wasted` means R17's `wasted`.** A reader comparing a
findings table to a per-detector table is comparing the same quantity or the
document is lying to them. Money still covers the priced spans only, because
money for a span with no rate does not exist; `wasted_unpriced` names the
difference — the shape `AgentCost` already uses — so the three numbers
decompose exactly. Driven as an invariant over all 26 fixtures and 28 detector
rows, with its own premise asserted (at least one row must actually carry
unpriced waste, or both sides are trivially zero).

---

## 2. Comments — judgment calls, not blockers

These are **not** committed changes. Each is a place where I disagree, or where
I want the next increment to know something.

**C1 — `--no-previews` under R30 loses the recorded model id, and that is the
wrong trade the moment the HTML exists.** A-c6 blanks `Span.model` at render.
R30 requires the unpriced table to name the recorded model. Under the flag the
table becomes `seq + reason`, and "eleven spans on model_not_in_snapshot"
becomes unactionable: you cannot tell whether it is one unknown model or
eleven. The coder's own S16 suggests a 16-hex digest and I agree; A10's "only
digests, counts and enumerated slugs" already licenses it. Not fixed here
because it is a new field in a pinned taxonomy, which is the PM's.

**C2 — `CostReport.cost_of` is O(n) and has no caller in the product.** It scans
`spans` linearly. Either it acquires a caller in increment 4's HTML — where it
would run per row, over up to 5,000 rows, making it O(n²) — or it should go.
Flagging now because the shape is the same one `format_display_usd` has: a
helper written for a caller that does not exist yet, tested as a function and by
nothing that uses it. Two of them is a pattern worth naming before a third.

**C3 — `_by_agent` and `_by_model` are O(agents × spans).** Each row re-scans
the whole priced list. Increment 2's review found two real quadratic blowups by
measuring; this one is bounded by `max_records = 2_000_000` and a real trace's
agent count, so it is not a defect today. It is worth one line of measurement
in increment 4 rather than a rewrite now.

**C4 — the `unpriced_usage` total indexes `trace.spans[seq]`.** Three places in
`cost/compute.py` and one in `detect/base.py` assume `seq == index`. R6 makes
that true for anything the v1 mapper produces, and R2 does not require it. It is
correct today; `detect/base.spans_by_agent` already carries a paragraph about
exactly this assumption and defends against it, and `cost/` does not. Worth one
helper rather than four call sites, in increment 4.

**C5 — `severity_counts` is called twice per run**, once by `summary_line` and
once by `report_document`, and nothing compared the two. Harmless today, but R40
pins stdout as a function of the findings and a future change to one call site
would put two different tallies in front of a reader with nothing noticing. The
one comment here I did close in code, because it is four lines:
`test_r40_stdout_and_the_document_report_the_same_counts` parses the stdout line
and compares it to `meta.counts.findings_by_severity` from the same run, with
the non-vacuous arm that at least one severity is non-zero.

**C6 — the deferred-flag refusals are right, and the loop that renders them is
`sorted(_DEFERRED_FLAGS.items())`.** That makes `--explain --out` report
`--explain` first, which is alphabetical rather than meaningful. Cosmetic;
mentioned because A-c10's reasoning is otherwise exactly right and increment 4
deletes half the dict.

**C7 — `tests/mutations.json` had no test that reads it. Raised as a comment,
then implemented, because the thing it guards against happened three times
inside this review.** Amendment 2 put the mutation set in the repository so the
next sweep is a *re-run*; a re-run is only possible while every anchor still
matches, and nothing asserted that. `M10`, `M18` and `W-J15` all went stale
under my own fix commits, and the only signal was a `NOT-APPLIED` line in a
sweep I happened to run. `TestMutationLedgerR49` now asserts five properties
without running a single mutation: every live anchor occurs **exactly once**,
every mutation changes its module, every survivor carries a reason, the ledger
declares a control arm recorded as surviving, and amendment 1's per-module floor
of 8 holds. Verified to fail by drifting one anchor.

I changed my mind about this one mid-review, and the reason is worth recording:
I had ruled it "belongs with R53, which is not in the spec". That is the correct
place for the *sweep*; it is not the correct place for a test that a checked-in
data file matches the source it describes, which is ordinary suite integrity and
squarely inside R49's existing shape.

---

## 3. Rulings on the coder's assumptions (A-c1…A-c15)

| # | Ruling |
| --- | --- |
| **A-c1** | **Upheld.** `resolve_model_key` on the protocol is correct. R27's ladder reads tables only the source has; the alternative closes the seam R26 exists to open. S17 is the right amendment. |
| **A-c2** | **Upheld.** `meta.sources[]` entry shape is unconstrained by R27, and making provenance a load-time validation rather than a convention is the stronger choice. This is the one thing that makes the rate table auditable at all. |
| **A-c3** | **Upheld, and it is the best call in the PR.** Adding a field to R14 would move a pinned model shape and every expectation file that reads it, for data no report reader wants. Carrying `{finding_id: seqs}` beside the finding, produced by **one** scan so the two cannot drift, is right. S15 is the right amendment. |
| **A-c4** | **Upheld.** `None` for an unknowable waste cost rather than a partial sum. Quietly reporting the priced subset would understate the product's headline number without saying so — which is exactly what BUG-4 turned out to be doing one level up, in the quantity beside it. |
| **A-c5** | **Upheld — see §5 on scope discipline.** |
| **A-c6** | **Upheld in substance, disputed in extent.** The guard is right and the demonstration (four `onerror=` payloads as the recorded model id) is exactly the kind of evidence a PR should carry. But the guard is enumerated over the fields the author thought of, and BUG-2 is the proof: `agent_id` and `ParseWarning.detail` were not among them. Fixed. See C1 for the R30 cost. |
| **A-c7** | **Upheld.** `metrics` redacted but not blanked is correct: R15 hashes it into the `finding_id` the same document prints. The reasoning generalises to identifiers, which is the reasoning I used for the BUG-2 fix. |
| **A-c8** | **Upheld.** One ordering rule per object. R36's grouping is a section-layout rule. |
| **A-c9** | **Upheld.** A capped machine-readable report is not machine-readable. S19 is the right amendment. |
| **A-c10** | **Upheld.** Declaring and refusing beats silently accepting. Exit 3 is right: it is a mistake in the command. |
| **A-c11** | **Upheld.** Paths as given, never resolved. R47 forbids the alternative outright. |
| **A-c12** | **Upheld.** Exit 3 for an argparse usage error is what R39 says; increment 1 pinned argparse's default because nothing implemented R39 yet. Changing an existing test with the change is correct and it is declared. |
| **A-c13** | **Upheld with a caveat.** Removing R39 from `traceability_pending.txt` is forced by R52 the moment any test cites it. The caveat is that R52's set-equality check cannot distinguish "cited" from "covered", so the ledger's honesty depends on a comment. That is a real weakness in R52 and is worth an amendment; it is not this branch's fault. |
| **A-c14** | **Upheld — and it is the reason BUG-1 was found at all.** Trapping `Inexact` turns "nothing rounds" from a claim into a check. BUG-1 was a hole in *where* the trap was applied, not in the decision to apply it. |
| **A-c15** | **Upheld.** `by_detector` covering detectors that produced findings is the only thing `cost/` can do without importing the registry (R44). The renderer can add zero rows. |

---

## 4. Rulings on the spec flags (S14–S23)

I cannot amend the spec. Each ruling states what the PM should change.

**S14 (R8 / R33 / R51 — the truncated PEM). Valid, and it is the one flag with
a security consequence.** R8 caps a preview at 240 code points; R33's
`private_key` pattern needs both terminators; the intersection is a hole, and
`report.json` for `hostile.jsonl` demonstrably contains
`-----BEGIN RSA PRIVATE KEY----- MIIBOgIBAAJBAK5f000…` today. The coder's
proposed resolution is right and its reasoning is righter: **do not lengthen
previews** — the same hole reopens at the next cap. Add an unterminated
alternative *after* the paired form so a complete block still redacts as one
unit. I did not implement it, and this is a deliberate limit on my authority: R33
pins the table verbatim and R44 forbids a second one, so widening the table in
code without widening it in the spec puts the two out of step silently — which
is the failure this project is organised against. **PM: amend R33's table
before increment 4's renderer ships, because increment 4 is when this reaches a
browser.**

**S15 (R14 / R17 / R29 — waste attribution is not computable as written).
Valid.** Amend R17 or R29 to say the attribution travels beside the finding.
Do **not** add a field to R14. Agreed with the coder's reasoning in full.

**S16 (R30 / R38 / R51 / A10 — what `--no-previews` covers). Valid, and it now
needs a wider answer than the coder proposed.** The flag's scope was never
stated as a *rule*, only as a list of fields, which is why the list was
incomplete twice — once for `Span.model` (the coder found it) and once for
`agent_id` / `ParseWarning.detail` / `findings[].agent_ids` (the tester found
the first two; I found the third).

**My ruling, which the PM should turn into spec text:** `--no-previews`
partitions trace-derived strings into two classes, and the requirement should
name the partition rather than the fields.

* **Free text** — previews, descriptions, recorded model ids, stop reasons,
  tool names, tool-use ids, error details. **Blanked.** This is A10's guarantee
  and it is what the flag is for.
* **Identifiers and aggregation keys** — `agent_id`, `parent_agent_id`,
  `findings[].agent_ids`, `ParseWarning.detail`, `metrics.tool_name`,
  `meta.source_files[].name`. **Redacted, never blanked.** Blanking a join key
  collapses `spans[]`, `agents[]` and `cost.by_agent[]` into one row and
  produces a document that is unreadable rather than redacted; blanking a
  warning's detail merges the `(code, detail)` pairs R10 aggregates on;
  blanking `metrics` makes the printed `finding_id` unverifiable from the
  printed evidence (A-c7).

That is what the code now does. The residual gap is that an *identifier* can
still carry attacker-chosen bytes under the flag, which R38's "entirely" does
not admit. The clean resolution is the coder's own S16 suggestion generalised:
under `--no-previews`, an identifier renders as a 16-hex digest of itself. That
keeps every join, keeps "two spans had the same unknown model" legible, matches
A10's "only digests, counts and enumerated slugs", and closes the gap
completely. It is a behaviour change in a pinned taxonomy and therefore the
PM's, so the tester's strict xfail is kept open with its reason rewritten to say
exactly what is fixed and what is not.

**S17 (R26 — a third protocol member). Valid, trivial.** Add
`resolve_model_key` to R26.

**S18 (R38 / R39 — `--out` required before it exists). Valid, no v1 text
change needed.** One sentence in R38 saying `--json` alone is a legitimate
invocation is worth having: CI gating on `--fail-on` without producing a
human-facing artefact is a real use.

**S19 (R36 — the 5,000-row cap). Valid, one clause.** The cap is an HTML
rendering rule.

**S20 (R39 / R40 — the success line's sanitation rule). Valid, and now
implemented.** BUG-6's fix is `_one_line_safe` plus newline removal, which is
exactly what the tester proposed. **PM: add the clause to R40 anyway** — the
code now does something R40 does not say, which is the same drift in the other
direction.

**S21 (R33 / R36 / R47 — `source_files[].name` is untrusted and uncovered).
Valid, and I ruled on it and fixed it rather than deferring.** The tester's
reasoning is correct: a filename in a scanned directory is
attacker-influenceable, it is not trace-derived, and nothing in R33's or A10's
scope covers it. My credential sweep found it as the single remaining path
carrying a raw AWS key shape, which settles the question empirically. It now
goes through the same boundary as every other untrusted string. It is redacted,
not blanked: a report that cannot say which files it read is not a report.
**PM: state in R33 (or R47) that `source_files[].name` is untrusted input
subject to R32's escaping and R33's redaction like trace text.** Settling this
before increment 4's renderer exists is much cheaper than finding it in the
hostile-corpus probe, and the tester was right to push for it.

**S22 (R17 / R29 / R31 — which reading wins for the per-detector row). Valid,
and I ruled: R17's reading wins.** See §1.7. The tester's proposed shape
(`wasted` plus `wasted_unpriced`) is what is implemented.

**S23 (R49 — the collection floor drifts on parametrized-over-the-product
modules). Valid, and yes, it is worth fixing now.** The tester is right that it
recurs every increment, and right that the specific number (43 against a floor
of 36, 16% slack) is invisible because adding tests never breaks a floor.

**My ruling on the design question the tester left open: neither of the two
options they offered.** "Exempt the module" removes the tripwire from the one
module whose count is *derived* rather than authored — the module where a
silent drop to zero would be least visible. "Warn when a module exceeds its
floor by more than a margin" turns a hard check into a soft one and adds a
tuning parameter that will be widened the first time it is inconvenient.

The right fix is that a floor derived from the product should be **expressed**
that way rather than transcribed: `test_boundaries_and_posture.py`'s R44 test
should carry its own assertion that it collected exactly one case per module
under `swarm_observer/`, and `collection_floor.json` should hold that module's
floor at the count of the *non*-parametrized tests. Then the derived part
cannot drift, because it is checked against the thing it is derived from, and
the authored part keeps a hard floor. This is R48's both-arms principle applied
to the floor itself. **PM: one clause on R49, and it is a ten-line change.**

I have not implemented it. It edits the shape of a pinned suite-integrity
artefact (R49 names `collection_floor.json` and its semantics), which is a spec
change wearing a code change's clothes, and the tester was right to leave it as
a flag. I have raised the floors to the current counts as this branch already
did.

---

## 5. Scope discipline: R33 arriving one increment early

**Ruling: correct, and it would have been a defect to defer it.**

R30 — which *is* in this increment's scope — says an unpriced span appears with
"its recorded `model` (redacted and escaped like any trace string)". A JSON
report that writes trace free text to a file without a redactor does not satisfy
R30; it satisfies a weaker requirement nobody wrote. The alternative is an
increment-3 deliverable that writes credential-shaped substrings verbatim and an
increment-4 commit that repairs it, which is a defect shipped on purpose with a
plan to fix it later — and this project has a name for that.

Two things make me comfortable rather than merely persuaded:

* **`escape_html` did not come with it.** R44's one-definition rule is
  untouched, and the module boundary the spec cares most about is intact. The
  coder took the requirement they needed and not the task it belongs to.
* **The table is transcribed verbatim, in R33's order, with a docstring saying
  that where a pattern looks wrong that is the requirement's call.** That is the
  discipline that makes an early arrival safe: the spec still owns the content.

The cost is real and should be stated: increment 4's T14 is now half-done, and
the tester has already written 155 redaction tests against it, so increment 4's
tester inherits a surface that has been swept once and will look "done".
`traceability_pending.txt` records that R33 is cited for its increment-3 half
only, which is the right mitigation.

The general rule I would want applied next time: **pulling a requirement forward
is correct when the current increment's own requirement cannot be satisfied
without it, and only then.** R33 clears that bar. R32 would not have, and it
was not taken.

---

## 6. The rate table

The owner's rate review is still outstanding. Here is what I can and cannot
vouch for, stated separately because conflating them is how a confidently wrong
dollar figure ships.

### What I can vouch for

**Internal consistency is near-perfect, and the two exceptions are exactly the
ones the coder flagged.** All 17 model keys were checked against every ratio
they could be checked against:

| ratio | conforming rows | value |
| --- | ---: | --- |
| `output / input` | **17 of 17** | 5.00 |
| `cache_write_5m / input` | **17 of 17** | 1.25 |
| `cache_write_1h / input` | **17 of 17** | 2.00 |
| `cache_read / input` | **15 of 17** | 0.100 |

The two rows that break the fourth ratio are `claude-fable-5-1` and
`claude-mythos-5-1`, both at **0.025** — a factor of four below every other row
in the table, in exactly one cell each, on the two most expensive models in the
snapshot.

**Tier ordering is coherent.** Input price descends
`opus-4-1 / opus-4` (15) → `fable-5* / mythos-5*` (10) → `opus-5 / opus-4-5…4-8`
(5) → `sonnet-4 / 4-5 / 4-6` (3) → `sonnet-5` (2) → `haiku-4-5` (1) →
`claude-3-5-haiku` (0.80). Two generational price *drops* (opus-4-1 → opus-5,
sonnet-4-5 → sonnet-5) are the only surprises and both are plausible and
mutually consistent.

**Provenance is enforced, not documented.** Every key is claimed by exactly one
source with a URL and an as-of date, and `RateSnapshot`'s validator refuses to
load a snapshot with an unattributed rate, a dangling alias, a doubly-claimed
key or a float. A rate cannot be added later without provenance. This is the
single best thing about the rate work and it is A-c2's doing.

### What I cannot vouch for

**Any absolute figure.** Not one. I have no way to check a published price from
here, and several of these model names post-date anything I could check against.
The ratio analysis above is a **weak** check and must not be read as
corroboration: a systematically wrong base `input` rate propagates through all
four derived columns and passes every ratio test in the table. Sixteen rows
being internally consistent tells you they were transcribed from one page by
one person in one sitting. It does not tell you the page said that.

### The one thing Trey should check first

**`claude-fable-5-1` and `claude-mythos-5-1`, `cache_read`.** Not because the
coder flagged it — because of *which* column it is in. Cache-read is the
dominant token component in agent traces by more than two orders of magnitude:
R9's own grounding measurement on an 11-file session records **532,235,958**
cache-read tokens against 2.5% inflation on output. On that session's volume,
those two rows differ by

* `0.25/M` → **$133.06**
* `1.00/M` → **$532.24**

— a **$399 difference on one session**, from one cell, in the product's
headline number, with every test in the suite passing either way. If the coder
mis-transcribed it, the product under-reports cache-heavy Fable/Mythos traces by
75% of their largest cost component. It is the highest-leverage single cell in
the snapshot and it is the one that breaks the table's own pattern.

I would rank the coder's own list slightly differently as a result: **cache_read
on the two `-5-1` rows first, legacy rows second.** A retired model's price
being restated changes a historical number; this changes today's.

### Two further notes

* **`aliases` is empty and the coder is right to have left it empty.** Rung 2
  of R27's ladder has no shipped data behind it. Inventing an alias so a rung
  looks exercised is precisely a check that cannot fail, and refusing to is the
  same instinct that produced the refusal to self-report a mutation score. The
  mechanism is implemented, validated and exercised through
  `SnapshotRateSource(snapshot=…)`. Bedrock and Vertex id strings are the real
  use and want a source before they want an entry.
* **What is absent has a cost worth naming.** Sonnet 3.7, Sonnet 3.5, Haiku 3
  and Opus 3 are not in the snapshot, so a trace on one of them reports
  `model_not_in_snapshot` and contributes **nothing** to the grand total. R30
  guarantees it appears in the unpriced table, so it is legible — but the
  headline dollar figure silently understates spend on any retired model, and
  only a reader who scrolls to the unpriced section will know. That is a README
  sentence at minimum, and an argument for the HTML report surfacing the
  unpriced count next to the total in increment 4.

---

## 7. My own mutation wave (wave 3)

### 7.1 Design

The tester's question was whether wave 2's pattern — a module swept once,
thoroughly, still holding sixteen unasserted behaviours — is exhausted. It is
not.

**59 mutants**, over anchors **neither prior wave touched**, chosen by mapping
each wave's anchors onto source lines and then reading what was left:

| module | wave-1+2 mutants | lines touched | wave 3 |
| --- | ---: | ---: | ---: |
| `cost/source.py` — snapshot validators, `source_for`, `rates_for` | 26 | 22 of 326 | 8 |
| `detect/base.py` — `Finding` validators, `attribute_waste`, `spans_by_agent`, `lower_median` | 24 | 20 of 511 | 14 |
| `cli/main.py` — the review's new code plus untouched decisions | 38 | 35 of 495 | 10 |
| `report/json_out.py` — timestamps, `severity_counts`, the `identifier` boundary | 38 | 37 of 523 | 9 |
| `cost/compute.py` — the review's new code, `missing_price_keys`, `_by_agent` | 46 | 42 of 648 | 8 |
| `report/redact.py` — the review's new idempotence guard | 18 | 18 of 158 | 4 |
| `detect/registry.py` | 9 | 8 of 138 | 3 |
| `cost/snapshot.py` | 16 | 12 of 149 | 2 |
| `ingest/reader.py` — **swept by no wave at all** | 0 | 0 | 1 |

One deliberate choice worth naming: **every line my own fix commits added got a
mutant.** A fix is unswept code, and the increment-2 ruling about self-chosen
sets applies hardest to a reviewer who has just written something.

Operators: relational flip, integer ±1, boolean-connective swap, guard-clause
drop, negation, membership flip, quantifier swap (`any`↔`all`), boundary
off-by-one, normalization drop (`sorted`/`set`/`tuple`), set-operand swap,
container-default swap (`setdefault`↔assignment), control-flow swap,
early-return removal, call-argument swap, literal substitution, exception-clause
narrowing, guard-scope change.

### 7.2 Results

**59 mutants · 54 killed · 5 survived · 0 unapplied**, against the branch as
handed to me. Run serially, one interpreter at a time, per the tester's own
harness contract.

Re-checked at the final commit against the finished suite, on **both**
interpreters, over the two prior waves' survivors plus wave 3's: `W-C11`,
`R-C07` and `R-J09` are killed; `S11`, `W-M11-CONTROL`, `R-S02` and `R-R02`
survive. **The two interpreters name the same four**, which is also the
confirmation that nothing in this branch's new code depends on a
Unicode-table-versioned predicate or a recursion limit.

**Three of the five survivors were real gaps. All three are now closed.**

| id | subject | verdict |
| --- | --- | --- |
| **R-C07** | `sorted()` dropped from `_by_model` | **REAL** — now killed |
| **R-M07** | `except (OSError, ValueError)` narrowed in `expand_inputs` | **REAL** — dead code, deleted |
| **R-J09** | `wasted_unpriced` rendered from `row.wasted` | **REAL** — now killed |
| R-S02 | `len(set(x)) != len(x)` → `< len(x)` | equivalent by construction |
| R-R02 | `waste[id] = ()` → `setdefault` | equivalent in practice, **not proven** |

**R-C07 is the finding of the wave, and it is a fixture defect wearing a
mutation's clothes.** R31 pins `by_model` as "ordered by key" and R47 forbids
"any `set`/`dict` iteration that is not explicitly sorted". Dropping the
`sorted()` survived the entire 2,077-test suite — including the R47 determinism
matrix, which exists to catch precisely this. The reason: **no fixture in the
corpus resolves more than one model key.** `max(len(report.by_model))` over all
26 fixtures is **1**, and with one element every ordering is sorted. This is
the tester's own "collection of one" theme one level up — not in a test, but in
the corpus every determinism test runs on.

The fix has a subtlety worth recording. Asserting the ordering **in-process**
would pass or fail depending on the `PYTHONHASHSEED` the session happened to
get: a test that kills the mutant for some seeds and reports green for the
others is this project's signature defect with a random number attached. So
`TestMultiModelOrderingR31R47` asserts in **subprocesses under fixed seeds**,
with six model keys, plus byte-identity across four seed settings.

**R-M07 is the one I am least comfortable about, because it is mine.** The
`except (OSError, ValueError)` I wrote into `expand_inputs` during the BUG-7 fix
is dead code: `Path.is_dir()` swallows `ValueError` and answers `False`, so
nothing can reach the clause — the whole suite passes with it removed. A guard
structurally unable to fire, written inside the fix for a class of guards
structurally unable to fire. It is deleted, and the interpreter behaviour the
deletion relies on is now pinned rather than assumed. Two of the three real
gaps this wave found were in code I had written hours earlier, which is the
argument for sweeping a fix and not only the thing it fixed.

### 7.3 The finding I did not expect: a fix that disarmed a guard

Re-running the **whole checked-in ledger** against the fixed tree — the first
time this project has done that, and precisely what amendment 2 checked the
ledger in for — turned up something no wave could have found.

**`C10` — dropping `Inexact` from `_EXACT.traps` — was killed by the tester's
suite and survives after my own BUG-1 fix.**

Moving `quantize_cost` inside the `DecimalException` guard was right. But it
made the two decimal signals indistinguishable for every input the suite drove:
an oversized token count now raises `InvalidOperation` from the quantize, is
caught, and becomes `CostError` — whether or not `Inexact` is trapped at all.
R29's central clause, the thing that turns "nothing rounds" from a claim into a
check, became **unfalsifiable as a side effect of a repair**, and every one of
2,086 tests stayed green.

That is this project's signature defect arriving through the least expected
door. Not a new guard that cannot fire; an existing, well-tested guard silently
disarmed by a fix to something else. Nothing but a re-run of the ledger says so,
which is the strongest argument I can make for the increment-2 review's
amendment 4 — **run the checked-in sweep in CI.** A ledger that is only ever
re-run when a reviewer feels like it is a regression suite with no schedule.

The separating input: a rate with 60 significant digits times an 11-token count
needs 61 digits for the **product**, so the multiply rounds — while the rounded
result quantizes to six places without complaint. Trapped it is a `CostError`;
untrapped it is a silently rounded dollar figure. Verified to kill `C10`, with a
non-vacuous arm (seven tokens against the same rate is an exact 60-digit product
and must still price) so the new test is not satisfied by refusing any long
rate. The ledger records on `C10` itself *when* it stopped being killed, because
an entry that says "killed" is worth less than one that says when that changed.

### 7.4 The two equivalence claims

**`S11` — `@lru_cache(maxsize=1)` → `maxsize=2` on `bundled_snapshot`.
UPHELD.** The function is nullary, so the cache is keyed on nothing and both
sizes hold the same single entry forever. Reproduced on a clean tree.

One refinement to the claim as written: it is **not undetectable, only
untested.** `cache_info().maxsize` is public and distinguishes them. Pinning it
would pin an implementation detail with no behavioural content, so the tester's
premise test — nullary signature, `currsize` stays 1 — is the right mitigation
and this stays a declared equivalent. Recorded because "no test can distinguish
them" is a stronger claim than the evidence supports, and the difference between
"equivalent" and "not worth testing" is exactly what the increment-2 ruling was
about.

**`W-C11` — dropping `compute_costs`'s waste-key validation loop.
OVERTURNED. It is not equivalent, and I have killed it.**

The claim: with the loop gone every malformed key still raises the identical
`ValueError`, because `_by_detector` calls `_detector_of` on every key anyway.
That is true for every input the sweep tried and false in general. **The loop
runs before any span is priced; `_by_detector` runs after.** So a trace that
raises during pricing changes which error a caller sees:

```
trace with a 10**60-token span AND waste_seqs={"NOT A FINDING ID": (0,)}

  shipped:  ValueError('NOT A FINDING ID' is not a finding id)
  mutant:   CostError(cost_precision_exceeded: span 0)
```

Two different R11 stderr lines and, since the review's floor commit, two
different diagnostics from one input. The contract the loop encodes — *a
malformed waste key is refused before any work is done* — is real, and it is
now pinned by
`test_r15_a_malformed_waste_key_is_refused_before_any_span_is_priced`, which I
verified kills the mutant.

This is the increment-2 ruling in miniature, and it is the reason that ruling
was right. The claim was "verified empirically, not argued" — but the empirical
check ran over a parametrization of malformed keys against a *healthy* trace,
and the distinguishing input needs a trace that is unhealthy in a second,
unrelated way. **An equivalence claim is only as strong as the input space it
was checked over**, and the space here was the one the ValueError test already
had. My own `R-R02` is classified `open`, not `equivalent`, for exactly this
reason: I can argue it, I cannot state an input space that reaches it.

### 7.5 The control arm

**`W-M11-CONTROL` — `expanded.append(path)` → `expanded.append(Path(path))`.
UPHELD as a genuine semantic no-op.** `path` is constructed as `Path(item)` two
lines above; `Path()` of a `Path` yields an equal `PosixPath`; nothing in the
package compares these by identity, and the line executes on every run so the
control is not a no-op merely because it is unreachable. It survived on both
interpreters in a clean run. The 228 kills keep their warrant.

**But the control arm is weaker than the report claims, and I found out the
hard way.** A no-op that survives shows the harness *can* report survival. It
does **not** show the harness applied the mutation, restored the tree, or ran
against the intended baseline. My first survivor re-run was killed by a timeout
mid-mutation and left `W-M11-CONTROL` **applied to the tree**; the next run
reported "baseline green" and measured two more mutants against a
silently-mutated baseline. Both still survived, so nothing was wrong with the
answer — and nothing in the harness would have told me if something had been.

That is the third distinct restore hazard this team has recorded (the coder's
`.pyc` `(mtime, size)` collision, the tester's killed-process restore, and now
this). The control arm cannot detect it, because a control arm that survives
looks identical whether it was applied or not. The fix is a **tree digest
compared between mutants**, which my harness now does and which belongs in R53
beside the other three. `tests/mutations.json` records it.

### 7.6 What this says about the tester's question

Wave 1 (152, independent of the author) left 23 survivors. Wave 2 (79, designed
after wave 1's kills, over untouched anchors) left 17 — sixteen of them real.
Wave 3 (59, designed after both, over anchors neither touched) left 5 — three of
them real, plus one overturned equivalence claim from wave 2's ledger.

**The pattern is not exhausted, and the shape of the residue has changed.**
Waves 1 and 2 found unasserted *behaviours*. Wave 3 found, in R-C07, an
unasserted behaviour that was unassertable — the corpus could not produce the
input, so five determinism tests ran green over a trace that could not
distinguish sorted from unsorted. That is not a gap a fourth wave of mutations
closes; it is a gap in the **fixture corpus**, and the check that would find it
is R48's both-arms principle applied to the *cost* section rather than to
detectors: for every ordering the spec pins, at least one fixture must have two
things to order.

**That is my recommendation to the PM in place of a fourth wave**, and it
generalises the amendment the increment-2 review already asked for.

---

## 8. Suite, lint and types

Both interpreters verified to import the tree under test before every
measurement.

| | CPython 3.11.15 | CPython 3.12.3 |
| --- | --- | --- |
| Full suite | **2094 passed, 1 xfailed** | **2094 passed, 1 xfailed** |
| `ruff check` | clean | clean |
| `ruff format --check` | 65 files formatted | 65 files formatted |
| `mypy --strict` | no issues in 30 source files | no issues in 30 source files |
| `tests/allowed_skips.txt` | empty | empty |
| Collection floors | at current counts | at current counts |

Baselines: 1,337 before increment 3; 2,035 handed to me; **2,094** now.

**The one remaining xfail is deliberate and is not a bug I declined to fix.** It
is the display half of BUG-2 — whether `--no-previews` should *blank* a
trace-derived identifier as well as redact it — which is a behaviour change in a
pinned taxonomy and therefore the PM's (see the S16 ruling). Its `reason` string
is rewritten to say exactly what is fixed and what is not, so nobody inherits a
false "done" and nobody inherits a false "broken".

The tester's other eleven strict xfails are resolved, and every one of their
"reproduces today" companions is **replaced** rather than deleted, so a fix that
turned one wrong behaviour into a different wrong behaviour is still red.

---

## 9. Merge verdict

**Merge**, with these conditions:

1. **The PM queues S14–S23.** S14 (the truncated PEM) is the one with a security
   consequence and must be settled **before increment 4's renderer ships**,
   because increment 4 is when those bytes reach a browser. S16 and S21 shape
   code increment 4 will write. S23 is a ten-line change that stops a tripwire
   drifting every increment.
2. **Trey does the rate review, `cache_read` on `claude-fable-5-1` and
   `claude-mythos-5-1` first.** Everything else in this branch is checkable by a
   test. That cell is not, it breaks the table's own pattern by a factor of
   four, and it sits in the token component that dominates real traces by two
   orders of magnitude.
3. **Increment 4 adds the ordering-fixture clause (§7.6).** The ledger-anchor
   test (C7) is in this branch; the corpus clause is not, because it changes
   what R48 requires of a fixture set and that is the PM's.

Nothing in this branch blocks the merge. The seven bugs are fixed, the eighth
defect is found and closed, the mutation ledger is 290 entries with one
overturned equivalence claim, and the suite is green on both interpreters with a
single deliberate, documented xfail.

What I would say to the owner in one sentence: **the code is the most careful
this project has produced, the verification of it is the most honest, and the
one number nobody can check is the one the product exists to print.**
