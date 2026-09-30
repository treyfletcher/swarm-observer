# Review: swarm-observer increment 4 (`feature/so-i4`)

Reviewer: reviewer-agent · Diff under review: `git diff main...feature/so-i4`
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED — not editable by me)
Under review: `docs/prs/feature-so-i4.md` (A-d1…A-d16, S24–S28),
`docs/test-reports/feature-so-i4.md` (BUG-8…BUG-13, S29–S32)
Prior rulings that bind me: `docs/reviews/feature-so-i3.md` (S14–S23, C1–C7,
the mutation-methodology amendments inherited from increment 2).

## Verdict

**Merge, once the PM has queued S24–S32.** One blocker, found and fixed in this
branch with a pinning test and with the generalization that should have landed
three increments ago. The tester's six bugs are all real, BUG-8 is the most
important thing either agent found, and the verification of this increment is
the most thorough the project has produced.

Two sentences carry the rest of this document.

1. **There was a fourth leak, and it is `SpanError.code`.** The brief told me to
   assume one existed. It did: a trace-derived string reaching both reports
   unredacted, in both modes, for the fourth increment running and for the same
   reason every time — its *type* looks enumerated. §1.
2. **The coverage map is a list, and a list is what has failed four times.**
   A-d1 made *escaping* universal — every string the renderer writes goes
   through `escape_html`, whether or not it is trusted — and that half of the
   boundary has never leaked. Redaction stayed enumerated per field, and that
   half has leaked in increment 1, increment 2 (S13), increment 3 (BUG-2) and
   increment 4. The fix in §1 includes the first check in this repository whose
   field list is **derived from the model** rather than written down.

---

## 1. The blocker

### 1.1 `SpanError.code` reached `report.html` and `report.json` unredacted, in both modes

*Commit `review(R12, R33, R51): SpanError.code reached both reports unredacted`.*

R12 builds `SpanError.code` from the record's own `error` field through
`ingest.text.slug` — the **identical** construction R4 uses to build a
`ParseWarning.detail` from an unknown record `type`, and `ParseWarning.detail`
has gone through the redactor since increment 3's BUG-2 fix. Both renderers
treated the code as package-authored: `html.py` escaped it with `_t`,
`json_out.py` emitted it verbatim, and neither blanked it under
`--no-previews`.

The model's own docstring says it out loud — *"An enumerated slug … never
trace-derived free text"* — and the PR's coverage map files it under
**"Package-authored strings — escaped, not redacted"**, beside
`ParseWarning.code`, which genuinely is a closed enum. That sentence is the
defect. `SpanError.code`'s alphabet (`^[A-Za-z0-9_.:\-]{1,40}$`) stops *markup*;
it admits a credential, which is the S13 ruling stated about `tool_name` and
never generalised.

Demonstrated, from a trace I built rather than from a coverage map:

```
--- anthropic_key: secret='sk-ant-api03-0123456789abcdefghij'
    SpanError.code in model      : ['sk-ant-api03-0123456789abcdefghij']
    raw secret in report.html    : True
    raw secret in report.json    : True
    raw secret in html --no-previews : True
    raw secret in json --no-previews : True
```

`slug` lowercases, so `AKIA…` cannot survive it — but `sk-ant-…`, `sk-…` and
`ghp_…` are all lowercase-legal and pass through unchanged, and at the model
level (a hand-built `Trace`, a future adapter) the uppercase shapes are legal
too. A Claude Code transcript whose API-error record carries
`"error": "ghp_…"` puts that token into a report a human forwards to a
colleague.

**The fix.** `error.code` goes through `sanitize.identifier` in both renderers:
redacted in both modes, never blanked. That is the increment-3 review's S16
partition applied to the field it missed — blanking the code would merge
distinct API errors in the one column that says what went wrong, exactly as
blanking a warning detail would merge R10's aggregation pairs.

### 1.2 Why nothing caught it — and instance ten

Three separate checks were each blind to this field, and all three are the
signature defect:

* **`tests/hostile_corpus.py` loads `SpanError.code` with a *markup* payload**
  (`error="</script><script>"`). `slug` reduces that to `_________script_`
  before it is ever rendered, so the escaping arm was covered and the
  *credential* arm was not. BUG-9 and BUG-10's shape, one field over — and the
  tester's own note about the GitHub-vs-AWS shape for `CREDENTIAL_RECORD_TYPE`
  is the same insight, applied to the neighbouring field and not to this one.
* **Increment 3's credential sweep pins the field to a constant.**
  `sentinel_trace`'s docstring reads *"a trace whose **every** trace-derived
  string carries a distinct sentinel"*, and its body reads
  `SpanError(code="api_error", …)`. A package-authored constant, in the one
  field that was leaking, inside the sweep written to catch exactly this. A
  sweep whose fixture cannot carry the payload into a field is that field's
  check reporting green while structurally unable to fail. **That is instance
  ten**, and it is instance seven (`LEAKING_PATHS` over an empty `findings`
  array) with a different empty subject.
* **The goldens cannot see it.** The corpus's error code slugifies to something
  redaction leaves alone, so the fix changes **not one byte** of either
  checked-in golden. A whole-document byte golden answers "what would make this
  red?" with "any change", and here the answer was "not this one".

### 1.3 The generalization, which is the part that matters

A fifth per-field fix would have been the fourth mistake. The commit therefore
also lands `tests/test_untrusted_field_sweep.py`, whose field list is **not
written down**:

* every string-valued field of `model/trace.py` is found by walking
  `model_fields` (via a predicate that handles `str`, `str | None` and string
  `Literal`s, and that is itself driven by a test, because a predicate that
  returned `False` for everything would make the sweep pass over an empty set);
* each one must be classified **untrusted** or **authored**, each authored entry
  carrying the named reason no trace byte can reach it. A field in neither
  class fails. Adding a string field to the normalized model is a
  `TRACE_SCHEMA_VERSION` bump under R2 anyway, so the friction lands exactly
  where R2 already says a decision is being made;
* every untrusted field gets its own legal AWS key shape — so a failure names
  the field — and is asserted absent from **both** renderers in **both** modes;
* and asserted *present* with a harmless token, because "absent" is satisfied
  perfectly by a renderer that emits nothing, which is BUG-8;
* and read back out of the built `Trace`, because a validator that silently
  normalised the credential would make every absence assertion vacuous without
  raising anything;
* `UNTRUSTED_FIELD_COUNT` is pinned as a literal, so the exhaustiveness test
  cannot be made green by *reclassifying* the field that failed it — the
  `LEAK_LEDGER_SIZE` friction, applied to the class that replaces the ledger.

Verified red before the fix: exactly the four `SpanError.code` arms, naming the
field. The other twelve fields pass, which is the arm that says the sweep is
about the boundary rather than about one field.

The two enabling defects are fixed at source as well: `sentinel_trace` now
loads `SpanError.code`, `LEAKING_PATHS` gains `spans[].error.code` and
`LEAK_LEDGER_SIZE` goes 10 → 11 — a reviewer's call, made and recorded beside
the number, because this ledger grew from closing a blind spot rather than from
skipping a guard and the distinction is the whole value of the literal.
`test_r36_a_span_error_renders_its_enumerated_code_and_redacted_detail` is
rewritten **in place** rather than deleted: its docstring read "the code is
ours, the detail is the trace's", and a reader of that file should see the
sentence corrected rather than absent.

---

## 2. The other two commits

Neither is a blocker. Both are this project's own discipline applied to what my
sweep and the tester's open question turned up.

### 2.1 `review(R4, R11, R34, R37, R40)` — five wave-5 mutation gaps

Five of wave 5's ten first-run survivors were real gaps: properties a
requirement states that nothing could falsify. Each is closed with a named
test in that commit, and each mutant is verified killed by the test that closes
it. Full account in §6. **W5-C06 is the one to read**: A-d11's "both documents
are staged in one call" — the thing that makes R11's fail-closed guarantee hold
with two outputs — was entirely unasserted, and the PR's evidence for it is a
malformed-trace run that fails before either document is rendered.

### 2.2 `review(R49)` — the `or True` scan, as an AST walk

The tester's question 5, answered. Ruling in §7.5. An AST walk rather than a
grep because this repository's prose contains the string `or True` in several
places, including the docstring recording BUG-11; a grep would flag
documentation and be deleted within an increment.

---

## 3. Comments — judgment calls, not commits

**C1 — A-d1's discipline should be extended to redaction, and the code is now
one step from it.** A-d1 escapes *everything*, trusted or not, and argues that
the alternative "makes the safety property a fact about this module's knowledge
of its inputs". It is right, and the evidence is that the escaping half of the
boundary has never leaked in four increments while the redaction half has leaked
in all four. The reason is that `_t` / `_free` / `_ident` is a three-way choice
a human makes per call site. The strongest available shape is a single
`text(value, *, kind)` at the boundary whose `kind` is exhaustive over a closed
enum, so "which boundary does this field use?" becomes a type error rather than
a judgment. I have not made that change: it touches every line of two renderers,
it is not required by any requirement, and `test_untrusted_field_sweep` now
catches the failure it would prevent. It is the right increment-5 refactor.

**C2 — the report's R51 per-field markers should gain `SpanError.code`, and
R51's own field list should gain "error codes".** `R51_NAMED_FIELDS` is a
transcription of R51's prose ("…assistant text, model ids, **error details**")
into seven `Trace` paths, and the transcription is a human judgment that
nothing checks. R51 says "error details"; the model has *two* trace-derived
halves of `SpanError`, and the one that leaked is the other one. The
`R51_NAMED_FIELDS ⊆ FIELD_MARKERS` direction is right and should stay. Not
committed: the marker would have to be lowercase and short enough to survive
`slug`'s 40-character cap, which is corpus surgery in the tester's module for a
presence property `test_untrusted_field_sweep` already asserts. Do it when the
PM amends R51 for S29, and add `SpanError.code` to R51's list in the same
edit.

**C3 — the suite has a wall-clock assertion, and it corrupts the one
measurement the project uses to validate its own tests.**
`test_r23_blocked_agent_does_not_grow_quadratically` compares elapsed seconds
against a ratio budget. The coder saw it flake once; the tester saw it never in
~100 runs; I saw it during wave 5, where it reported a mutant "killed" that my
new sweep actually kills. Under `-x` it is one of the first tests alphabetically
that can fail from load, so **any** mutant can be attributed to it. The
correctness content is real (two quadratic blowups were found by measuring), so
deleting it is wrong; the fix is to assert operation *counts* rather than
seconds, or to mark it so a sweep can deselect it. Recorded in
`tests/mutations.json` as the fourth harness hazard, beside the mutated
baseline, the stale bytecode and the wrong tree.

**C4 — `TimelineLane` carries two fields the model guarantees are equal.**
`lane.lane` and `lane.agent_index` are always the same integer, because R2 pins
`agent_index` to `range(len(agents))` and `lane` is that same enumeration index.
Two of my four equivalent survivors (W5-T27, W5-T29) are this one fact. It is
not a defect — the *names* are meaningful and a v2 adapter could separate them —
but a reader of `render_svg` cannot tell which one is authoritative. One line of
docstring, or one field.

**C5 — `CostReport.cost_of` is now dead code the review predicted and the coder
correctly did not use.** C2 from the increment-3 review asked whether it would
acquire an O(n²) caller in the HTML; A-d13 says it did not, and the spans table
builds a `{seq: cost}` mapping instead. That is the right call and it leaves a
public helper with no caller in the product for a second increment. Delete it in
increment 5 or give it the caller; `format_display_usd` finally got one (A-d14),
which is the precedent.

**C6 — the `--no-previews` note in the timeline section is slightly untrue.**
It reads "lane labels are redacted identifiers", which is right, but the same
section's `type` and `description` columns are *blanked*, not redacted, and the
note does not say so. Cosmetic; mentioned because a reader who trusts that
sentence will mis-read an empty description as "no description recorded".

**C7 — two unreachable branches in `build_timeline` are correctly kept and
should be said to be unreachable in the code, not only in the test report.**
The `lane is None` skip and the `index_of.get(…, lane)` fallback cannot execute
for any `Trace` the model admits. The tester found them; the code's comments
argue they are fail-safes for a future adapter, which I agree with (§7.3). What
is missing is the sentence that they have **never executed**, which is what a
future editor needs before deciding the comment is out of date.

---

## 4. Rulings on the coder's assumptions (A-d1…A-d16)

| # | Ruling |
| --- | --- |
| **A-d1** | **Upheld, and it is the best decision in the branch** — with the caveat that it covers only half the boundary. Escaping everything makes the property a fact about the code; four increments of evidence say it works. Redaction is still enumerated, and that is where the fourth leak was. See C1. |
| **A-d2** | **Upheld.** Two renderers over the same fields, one policy. The alternative — `html.py` importing its security policy from `json_out.py` — is worse than adding a module R44's layout block does not name. R44's AST test passes it, so nothing is violated in code; **PM: add `sanitize.py` to R44's layout block**, or state that `report/` may hold modules the block does not name. |
| **A-d3** | **Upheld**, inheriting A-c7. R15 hashes `metrics` into the printed `finding_id`; a masked metric makes the printed id unverifiable from the printed evidence. The constraint is correctly *named* rather than assumed, which is what the increment-3 review asked for. |
| **A-d4** | **Upheld.** Generating the allowlist from the inputs rather than from the output is the difference between a specification and a tautology, and the exact geometry value sets are the stronger form. One gap, which the coder predicted in their own risk list and my sweep confirmed: the allowlist could be **widened** and nothing noticed (W5-A08). Closed. |
| **A-d5** | **Upheld — verified independently.** I edited `REPORT_SCRIPT` at render time and confirmed the hash of the *parsed* `<script>` element diverges from `SCRIPT_SHA256` while the unmodified render matches. The constant-against-constant assertion exists separately and is labelled as the weaker one. This is the one R34 property that is genuinely proven rather than asserted. |
| **A-d6** | **Upheld.** Verified: the rendered document contains no URL-bearing attribute of any of the 18 names I checked, `xmlns` included, and no scheme of any kind outside a text node. Dropping an attribute the HTML5 parser supplies anyway, rather than breaking a pinned security property to state it, is right. |
| **A-d7** | **Upheld.** A rect outside its own viewBox is a rendering defect in every browser, and clamping *after* the pinned computation leaves R37's formula literally intact. **PM: one clause on R37.** |
| **A-d8** | **Upheld, and it is the best *structural* call in the branch.** `render_svg` takes a `Timeline`, not a `Trace`, so it **cannot** reach an agent id — the type prevents the mistake instead of the author avoiding it. Every character of the `<svg>` is a fixed class name or the decimal form of an integer this package computed. That is the shape every other guard in this project should aspire to, and it is the opposite of a coverage map. |
| **A-d9** | **Upheld, and BUG-8 is the proof it was necessary but not sufficient.** Rendering the legend unconditionally removes one way for "no payload in the report" to be vacuously true; it does nothing about a fixture that loads the columns empty, which is the way it actually happened. The complete rule is A-d9 **plus** a per-field non-vacuity assertion, and the tester supplied the second half. |
| **A-d10** | **Upheld.** Consistent with the S18 ruling; refusing a run that names neither output honours A-c10, and refusing `--out` and `--json` at the same path is the same instinct. |
| **A-d11** | **Upheld in substance, and the evidence offered for it was the wrong evidence.** The reasoning — two outputs make R11 a stronger requirement than it was with one — is exactly right and is a requirement the coder *derived* rather than one R11 states. The demonstration is a malformed-trace run, which fails before either document is rendered and therefore says nothing about a failure between the two writes. Staging one document at a time survived the entire suite (W5-C06). Now pinned. **PM: one clause on R11 naming the two-output case.** |
| **A-d12** | **Upheld, and it is the coder's best finding.** Rendering one of three previews made the document poorer and made R51 vacuous, and only the second is a security property. Found by rendering the file and reading it, which is the method this review used to find §1. |
| **A-d13** | **Upheld.** C2's prediction avoided; a dict built once beats an O(n²) helper over 5,000 rows. See C5 for what to do with the helper. |
| **A-d14** | **Upheld.** |
| **A-d15** | **Upheld.** Implements the increment-3 review's §6 recommendation verbatim: a trace on a retired model contributes nothing to the headline figure, and now a reader sees that without scrolling. |
| **A-d16** | **Upheld.** R34's "class toggling, filtering and sorting" is an upper bound and reading it as one is correct. The `//`-free script and `/* */` comments are the right kind of paranoia: a check that reads bytes cannot tell a line comment from a scheme-relative URL, and writing for the check you have is cheaper than arguing with it. |

---

## 5. Rulings on the spec flags (S24–S32)

I cannot amend the spec. Each ruling states what the PM should change, and in
what order.

**S24 (R32 / R8 — `str.isprintable()` moves between interpreters). Valid;
transcribing it verbatim was right.** The predicate is named explicitly in both
requirements, so implementing anything else would have put code and spec
silently out of step — which is the failure this project is organised against.
The consequence is bounded and correctly scoped by the coder: R8 normalizes the
three preview fields at ingest, `TestCheckedInDataIsInterpreterStableR8` keeps
unassigned code points out of checked-in data, and the seven fields R8 never
touches are where a *live* hostile trace could render two different documents.
**PM: replace `str.isprintable()` in R8 and R32 together with a fixed
code-point rule, named by number — C0, DEL, C1, and the `Cf`/`Zl`/`Zp`
ranges.** One rule, one place. Priority: after S26 and S29, because it is a
determinism risk on live traces rather than a security one, and the golden
files are guarded meanwhile. Note for whoever writes the amendment: the tester's
two tests hold the question open deliberately and **both must be rewritten** when
it is settled — that is a feature of how they were written, not a cost.

**S25 (R34's attribute allowlist names six kinds and the document R34 requires
needs more). Valid, and it is the clearest defect in the spec text.** R34
mandates a CSP `<meta>` whose `http-equiv` and `content` attributes R34's own
allowlist forbids: the requirement contradicts itself on a literal reading.
**PM: adopt the coder's resolution** — state that the allowlist is *generated*,
name the value **classes** (document-chrome constants; closed-enum members;
decimal integers from the trace or the computed layout; same-document fragments
built from `span_id`/`finding_id`; fixed class names) and require the generating
function to take the **inputs**, not the rendered document. Add one thing the
coder did not ask for and my sweep argues for: **require a second,
independently authored classification of the attributes the document actually
contains.** The tester built exactly that (`ATTRIBUTE_SHAPES`), and it is what
catches the hole the coder named as most likely — an attribute added to the
renderer and to the allowlist in the same commit. A generated allowlist and the
renderer that feeds it can drift together; two tables written from the same
prose by two people cannot drift in the same direction silently.

**S26 (R35's byte reading contradicts R51's `javascript:alert(1)` payload).
Valid, and I rule for the parser reading — which is what the tester
implemented.** The two readings are not symmetric: R35's own second sentence
("asserted by a parser-based test, not a regex over the source") already settles
it, and the first sentence's "anywhere" is the loose one. **Ruling: R35
constrains URL *contexts* — attribute values, element types, stylesheet content
and the one `<script>` — and explicitly not text nodes, where R51 requires the
same strings to appear.** The residual byte scan for `@import`, `url(`, `//`
and the absolute schemes is correct and must stay, because a parser cannot see
inside CSS.

I verified the strong form of this independently rather than taking it on
argument. Removing every text node from the rendered hostile document leaves
markup containing **no** `http:`, `https:`, `file:`, `ftp:`, `@import`, `url(`
or `//`, and `javascript:` appears only in text. So the byte reading and the
parser reading agree everywhere except the one place R51 requires them to
disagree, which is what makes the amendment a clarification rather than a
weakening. **PM: one clause.** This was the flag the coder asked to have
settled before the probe was written; the tester wrote the probe under the
right reading, and the spec should now say so.

**S27 (`--no-previews` changes which findings exist, and at what severity).
Valid, and it is the most under-weighted flag in the set.** Both agents file it
as a documentation gap. It is not: a **privacy** flag silently drops a
`critical` finding, so `report.html --no-previews` is not the same report with
the text removed — it is a different verdict. The user most likely to reach for
the flag is the one sharing the report outside the team, which is the user least
able to notice that a critical finding is missing.

**Ruling: the coder's second option, and it is the only one that actually
fixes it.** Compute R22's `unknown_tool` decision at ingest, **before** A10
blanks, as a boolean on the span; the flag then stops changing detection.
Option one merely writes the surprise down. It is a mapper change in increment
1's code and therefore not this branch's, and it is not a merge blocker — the
behaviour is inherited and the tester has pinned it as a superset relation so
nobody can mistake the difference for blanking. **Until it lands it belongs in
the README, in the `--no-previews` guidance R20 already plans**, because a
caveat in a spec flag is not a caveat a user sees.

**S28 (AC5's named input cannot exist). Valid.** The corpus cannot produce a
trace with a finding in every detector class, and the reason is structural, not
a missing fixture: R6 orders by basename, so merging files makes same-named
agents interleave and destroys `agent_loop`'s signature sequence and
`retry_storm`'s window. The tester's answer — a synthetic multi-agent trace
through `TraceBuilder` — satisfies AC5 in substance, and pinning a test that
goes red when S28 closes is the right way to hold it open. **PM: amend AC5 to
"a trace with a known finding in every detector class", and allow it to be
assembled at test time.** An acceptance criterion whose input cannot exist is a
check that cannot pass, which is the mirror image of this project's signature
defect and just as corrosive.

**S29 (R51 names a fixture that cannot carry four of the fields R51 names).
Valid, and the tester's diagnosis is exactly right.** Two rules collide and each
is correct on its own: R51 wants agent descriptions in `hostile.jsonl`, and T6
forbids a `.meta.json` sidecar beside the corpus because a sidecar is not
covered by R5's `trace_id` and would move every pinned finding id without
changing a fixture byte.

**Ruling: take the cheap resolution, not the thorough one.** Amend R51 so the
probe's corpus may include a trace assembled at test time, and require it to
cover every field R51 names, machine-checked — which is what
`R51_NAMED_FIELDS` does today. Do **not** fold the sidecar's bytes into R5's
`trace_id`: that is a `TRACE_SCHEMA_VERSION` conversation started to solve a
test-fixture problem, it moves every pinned id in the repository, and it buys
nothing the assembled trace does not already buy. **And add `SpanError.code` to
R51's field list explicitly** — R51 says "error details", the model has two
trace-derived halves of `SpanError`, and the half the prose does not name is
the one that leaked. See C2.

**S30 (`UNTIMED_LIST_CAP` is undeclared). Valid, trivial.** R37 says untimed
spans are "listed in a no-timing note" and puts no bound on a list a trace can
make two million entries long. **PM: one clause on R37 in R36's words — the
note lists at most a fixed number of seqs, names the total, and carries an
"…and N more" line — with the number in the requirement so the constant is a
transcription rather than a choice.** The tester pinning `200` as a literal is
correct and my wave-5 gap W5-H31 is the other half of the same clause: the
*tail* needed pinning too.

**S31 (AC4's example string has no apostrophe). Valid, trivial.** **PM: add a
`'` to AC4's string.** One character, and it makes the criterion self-consistent.
The tester's handling — assert the seven characters that are present, assert the
apostrophe is *absent* so the discrepancy cannot be papered over, and cover `'`
separately against R32's table — is the right way to implement a criterion you
cannot edit.

**S32 (which label wins when two patterns claim overlapping text). Valid.**
R33 pins the pattern text and idempotence and says nothing about precedence, so
BUG-12 and BUG-13 are gaps rather than violated clauses and the tester is right
to characterize rather than fix them. **Ruling: the tester's clause is right and
should be written slightly wider — a later pattern never re-matches the marker
text an earlier pattern wrote, and the first pattern to claim a span of text owns
its label.** That closes BUG-12 (an AWS key inside `TOKEN=…` reported as
`secret_assignment`, and two credentials in one value collapsing to one marker)
and BUG-13 (the prefix-sensitive BUG-3 guard deleting a character of report
text) with one rule instead of two. It is a change to a pinned taxonomy and is
therefore the PM's, and I have not implemented it. **Severity: low.** Both
failures cost a *label*, not a secret; the credential is redacted either way,
and R33 already says in the spec and in the README that redaction is a courtesy
and not a boundary.

---

## 6. My own mutation wave (wave 5)

### 6.1 Design

**41 mutants over anchors neither wave 4 nor waves 1–3 touched.** Wave 4 put 24
mutants into a 1,002-line `report/html.py` and 22 into `report/timeline.py`,
concentrated on the constants and the arithmetic. What it did not reach was the
**oracle** — `attribute_allowlist`, the function every R34 assertion runs
through — and the wiring: `render_html`'s section order and span-id cap,
`_header_section`'s R4 surfacing, `_timeline_section`'s note tail,
`_spans_section`'s cost mapping, `_warnings_section`'s empty branch, the four
`ESCAPE_TABLE` rows wave 4 left alone, `build_timeline`'s lane and index maps,
`render_svg`'s lane rects, and `cli/main.py`'s two-output staging.

Two mutants are on **my own fix**, because a fix is unswept code and the
increment-3 review's discipline — "every line my own fix commits added got a
mutant" — applies hardest to a reviewer who has just written something. Both are
killed, by the sweep test that shipped with the fix.

**Operator set, declared:** relational flip; integer constant ±1; arithmetic
substitution; boolean-connective swap; guard-clause drop; negation
drop/insert; membership flip; slice/range bound; sort-key drop; normalization
drop (`sorted`/`set`/`tuple`); set-operand drop; literal substitution; **literal
widen** (new this wave — adding a value to an allowlist, which is the only
operator that models "a guard that was made more permissive"); reorder;
call-argument swap; call-argument substitution; container-default swap; and one
**declared control arm** (`control-no-op`).

By operator: set-operand-drop 5, call-argument 6, literal 6, relational 4,
arith 4, guard-drop 3, negation 3, int±1 3, reorder 2, normalization-drop 1,
membership 1, slice-bound 1, container-default 1, literal-widen 1,
control-no-op 1.

**Harness.** Serial, never two sweeps at once. `PYTHONDONTWRITEBYTECODE=1`,
`__pycache__` purged around every mutation, original text rewritten in a
`finally` and on `SIGINT`/`SIGTERM`, a **tree digest compared between mutants**,
the unmutated suite run first with an abort if it is red, an anchor that does
not occur exactly once reported `NOT-APPLIED` rather than skipped, and every
kill recording the pytest node id that produced it. Every clause the ledger's
harness contract has accumulated over four increments, which is what made §6.3
visible.

### 6.2 Results

```
wave 5, first run, CPython 3.11.15, serial
TOTAL 41  killed 31  SURVIVED 10  not-applied 0
  survivors: ['W5-A04', 'W5-A07', 'W5-A08', 'W5-H27', 'W5-H31',
              'W5-H39-CONTROL', 'W5-T27', 'W5-T29', 'W5-C05', 'W5-C06']

after closing the five real gaps, re-run of those five plus the control arm
TOTAL 6  killed 5  SURVIVED 1  not-applied 0
  survivors: ['W5-H39-CONTROL']
```

**Five of the ten survivors were real gaps.** All five are closed, each by a
named test in the same commit, each verified to kill its mutant.

| Mutant | What it did | Why it survived | Killed by |
| --- | --- | --- | --- |
| `W5-C06` | `atomic_write_texts(documents)` → one call per document | A-d11 states the property; nothing asserted it. The PR's evidence is a malformed-trace run, which fails *before* either document is rendered — so it shows nothing is written when ingestion fails and says nothing about a failure **between** the two writes. R11's fail-closed guarantee on the partial-write path was a hope. | `TestBothDocumentsAreStagedTogetherR11` — one call carrying both paths, a `render_json` failure leaving neither file, and the arm that both files appear without the failure |
| `W5-A08` | `"data-detector": frozenset(DETECTOR_SLUGS) \| {"anything"}` | Every R34 check asks whether the document's attributes are **in** the allowlist, and that direction cannot see a widened allowlist. It is `LEAKING_PATHS` before `LEAK_LEDGER_SIZE`, sitting inside the R34 oracle: a permission set whose contents are chosen by whoever needs the check to pass. | `test_r34_the_generated_allowlist_holds_nothing_the_inputs_do_not_justify`, which recomputes every entry from the closed enums and the same three inputs and asserts **equality** |
| `W5-H27` | `warning.code == "unknown_record_type"` → `!=` | No trace anywhere carries that warning beside another code with a *different* count, so "the count of unknown record types" and "the count of some other warnings" are the same number in every document the suite renders. A collection of one in the **corpus** — the increment-3 review's R-C07 finding, in a different section. | `test_r4_the_header_note_counts_unknown_record_types_and_not_other_warnings`, with three deliberately distinct non-summing counts, plus the zero-count arm |
| `W5-H31` | `if more > 0` → `>= 0` on the no-timing note's tail | `UNTIMED_LIST_CAP` was pinned; its **tail** was not. The note would have read "…and 0 more" on every trace with any untimed span. | `test_r37_the_no_timing_note_has_no_tail_when_nothing_was_truncated`, both arms |
| `W5-C05` | `written = (html_path, json_path)` reversed | R40's "a deterministic function of the trace, the flags and the output paths" held by accident of construction. The comment in `cli/main.py` names the property; nothing asserted it. | `test_r40_stdout_names_the_paths_in_flag_order_not_in_the_order_given` |

### 6.3 The five that remain, by name

| Mutant | Operator | Classification |
| --- | --- | --- |
| `W5-H39-CONTROL` | `control-no-op` (`[x]` → `list([x])`) | **The declared control arm. It must survive**, and it did in both runs. It is the warrant for wave 5's 36 kills: a sweep that reports its no-op killed is not reporting verdicts that come from the mutation. |
| `W5-A04` | set-operand-drop (`{"0"}` from the `x` allowlist) | **Equivalent, with evidence.** `trace_start = min(starts)` over the same set the offsets are drawn from, so the earliest drawable span always has `offset_ms == 0` and therefore `x == 0`; and when `rects` is empty no `<svg>` is rendered at all, so no `x` attribute exists. Dead width either way. |
| `W5-A07` | int−1 (`max(timeline.height, 1)` → `max(…, 0)` in the allowlist) | **Equivalent, with evidence.** `height == len(lanes) * LANE_HEIGHT`, so height is 0 only with no agents; with no agents no span gets a lane, `rects` is empty and `render_svg` is never called. No document can carry a `viewBox` while height is 0. |
| `W5-T27` | call-argument swap (`data-agent="{lane.agent_index}"` → `{lane.lane}`) | **Equivalent, with evidence.** `Trace._pinned_invariants` requires `[a.agent_index for a in agents] == range(len(agents))`, and `TimelineLane.lane` is that same enumeration index. See C4. |
| `W5-T29` | call-argument (`index_of.get(span.agent_id, lane)` → `lane`) | **Equivalent, with evidence.** Same invariant. The `.get(…, lane)` default is the fail-safe for a future adapter and is unreachable for any `Trace` the model admits. |

### 6.4 What the wave says, and a harness hazard I hit

**The pattern the increment-3 review described has not changed shape, and this
wave found the same residue one level up again.** Wave 2 found unasserted
behaviours; wave 3 found an unasserted behaviour that was *unassertable* because
the corpus could not produce the input (`R-C07`); wave 5 found two more of
exactly that kind — `W5-H27` is a collection of one in the corpus, and `W5-A08`
is a collection of one in an *oracle*. The predecessor's recommendation to the
PM — that for every ordering the spec pins, at least one fixture must have two
things to order — should be widened by one word: **for every distinction the
spec pins, at least one fixture must have two things to distinguish.** A corpus
where every trace has one model key, one warning code and one preview per span
cannot tell a right renderer from a wrong one, whatever the tests say.

**The hazard.** One kill in wave 5's first run was attributed to
`test_r23_blocked_agent_does_not_grow_quadratically` — a wall-clock scaling
assertion — under sweep load. Re-running that mutant alone produced the real
killer. Under `-x`, a timing assertion that can fail from noise is a mutation
harness that can report any mutant killed for no reason. **A sweep must
re-check a kill whose node id is a timing test**, which is now written into
`tests/mutations.json` as the fourth harness hazard beside the mutated
baseline, the stale bytecode and the wrong tree. See C3 for the underlying
problem. Note the asymmetry that saved this run: noise produces false *kills*,
never false *survivals*, so the five gaps above are sound as reported and only
the kills needed re-checking.

### 6.5 Interpreter parity

Wave 5's mutants were designed against, and run on, CPython 3.11.15. Nothing in
the anchors touches a Unicode-table-versioned predicate, a recursion limit or a
hash seed — the one anchor near that boundary is `ESCAPE_TABLE`'s literals,
whose four wave-5 mutants are killed by the checked-in golden, which CI compares
on **both** legs. The whole suite, including every test added by all three
review commits, is green on 3.11.15 and 3.12.3. I did not re-run waves 1–4 on
either interpreter: they cover modules these commits did not change except for
`report/html.py`, `report/json_out.py` and `cli/main.py`, and for those the full
suite is the same oracle a re-run would use.

---

## 7. The tester's five questions, answered

**7.1 BUG-8 and S29 together.** Ruled in §5 (S29). The tester is right on every
point: R51's own named artefact cannot satisfy R51, the reason is a collision
between two individually correct rules, and the PM ruling matters more than the
workaround. Two additions. First, **the workaround should not be deleted
wholesale** when R51 is amended: `TestTheCheckedInFixtureLeavesFieldsEmptyBug8`
should go, because it exists to record a gap, but the extended trace and the
machine-checked field list are the amendment's *implementation* and should stay.
Second, R51's field list should gain `SpanError.code` — §1 is what "error
details" being one phrase for two fields actually costs.

**7.2 Are per-field markers the right shape, or a second oracle nobody asked
for?** **The right shape, and they are not a second oracle.** A second oracle is
a second *judgment* about the same question, which is what the increment-2
review ruled against for self-chosen mutation sets. `FIELD_MARKERS` is the
opposite: a **labelled input**, which lets one oracle say *which* field failed
instead of only that something did. The evidence is empirical and decisive —
three of wave 4's real gaps (`I-H11`, `I-H22`, `I-H23`) are all "this field was
never loaded", and a shared payload cannot distinguish them; the coder's own
spans-table defect is the same shape and was found by hand.

The risk the tester names is real and the mitigation is the right one: checked-in
data can be narrowed, so `test_r51_every_field_the_requirement_names_is_covered_by_a_marker`
compares it against R51's list. **The comparison direction is right** — R51 is
the authority, so `R51_NAMED_FIELDS ⊆ FIELD_MARKERS` is the assertion that
matters. What it cannot check is the transcription itself: turning R51's prose
into seven `Trace` paths is a human judgment, and §1 is what that judgment cost.
The complement is a check derived from the **model** rather than from the prose,
which is what `test_untrusted_field_sweep` now is. Use both: prose→fields
catches a requirement the corpus forgot, model→fields catches a field the
requirement forgot.

**7.3 The five equivalent survivors as a design signal.** **Keep the
normalizations.** The Modularity notes' rule — "guards are properties of
functions, not of call paths" — is not a style preference in this repository; it
is the rule whose violation produced all four leaks. A renderer that assumes
`model/` sorted its input is correct until someone builds a `Timeline` by hand,
which `render_svg`'s public signature invites and which the tester's own tests
do. The cost is four lines no input can distinguish; the benefit is that the
renderer's correctness does not require reading another package's validators.
That trade is right and it is the same trade A-d1 makes about escaping.

One thing to add, because it is the part that can rot: each of those four
equivalence claims is a claim **about a validator in `model/`**. If a validator
is ever relaxed, the claims silently become false and the ledger will still say
"equivalent". The ledger entries now name the specific invariant each one
depends on (`Trace._pinned_invariants`, `Finding._pinned_shape`), which is the
cheapest available guard: a reader relaxing a validator has one place to look.

**7.4 `I-H06` and the two ordering tests.** **Keep both, and the tester's
reasoning is right.** The end-to-end test is not decorative. It asserts the
*document's* order, which is R36's requirement, and killing a mutant and
asserting a requirement are different jobs — a test that only exists to kill
mutants is a test written for a tool rather than for a reader. The second test,
which hands `render_html` an unsorted pair, is what makes `_grouped` a property
of the function rather than of `detect/base.py`, and that is A-d1's principle
applied to ordering. The tester's instinct that the first test "is decorative
because of a fact about `detect/base.py`, not about `report/html.py`" is exactly
the right way to say it, and it is why removing it would be wrong: the moment
R13's sort changes, the decorative test is the one that notices.

**7.5 BUG-11, and whether the `or True` grep belongs in CI.** **It does, and it
now is** — `TestNoAssertionIsStructurallyUnableToFailR49`. A class of defect
found by hand, fixed once and left unguarded is precisely what produced
instances two through nine. R49 already enforces its other two members with
hooks; this is the third, and it is the one found *inside* the suite that
enforces the other two.

An AST walk, not a grep: this repository's prose contains `or True` in several
places, including the docstring recording BUG-11 and the commit message adding
the scan, so a grep would flag documentation and be deleted within an increment.
It flags two shapes — `assert <truthy literal>` and `assert X or <truthy
literal>` — is driven against BUG-11's shipped line verbatim, against seven
assertions that *can* fail (so it has no false positives to get it deleted), and
carries a premise arm asserting it actually read more than 30 modules and more
than 1,000 assertions, because "no offenders" must not be able to mean "nothing
was read".

What it cannot see is stated in its own docstring rather than left to be
discovered: a fixture with no case that could trip the check, a sweep over an
empty array, a ledger that can be widened. Those need a non-vacuous arm per
check and no scanner can supply one. **§1.2's instance ten is one of those**,
and it is why the scan is the smaller half of this review's answer to the
signature defect — `test_untrusted_field_sweep` is the larger half.

**7.6 The goldens** (the tester's question 7, answered because it deserves it).
**Keep them, and the pairing is real.** Two files, regenerable only by a script
that a test asserts contains no `def test`, built from a corpus checked in as
code rather than from whatever was on the machine, and asserted to be
reproducible from two different temporary directories. Every security property
they contain is *also* asserted structurally, and what they pin that nothing
else can is the document's ordering and whitespace — R47's actual promise. They
are also the only cross-interpreter comparison available, and the tester is
right that CI comparing both legs against one checked-in file is the mechanism.

The honest limit, which the tester states and which §1 demonstrates: a golden
answers "what would make this red?" with "any change to the bytes", and the
fourth leak changed no bytes. A golden is a regression detector, not a security
check, and this branch is the first in the project where that distinction was
written down before it was needed.

---

## 8. Independent leak probe — what I did rather than what I read

The brief said to assume a fourth leak existed and not to accept a coverage map
or a passing credential sweep as evidence. Everything in this section was run
against rendered bytes.

**8.1 The per-field sweep that found it.** A `Trace` built at the **model**
level — not through the adapter, because an adapter constrains what can reach a
field and the question is what the *renderers* do with a field the model admits
— carrying a distinct legal AWS key shape in each of sixteen trace-derived
fields, rendered by both renderers in both modes. Sixteen fields × two documents
× two modes. Result before the fix: **one leaking field, `SpanError.code`, in
all four combinations.** After: none. That table is now a checked-in test.

**8.2 The rendered hostile document, read rather than summarized.**

```
fetching elements present    : ['meta']            (charset + CSP; neither fetches)
URL-bearing attrs (non-href) : []                  (18 names checked, xmlns and style included)
non-fragment href values     : []
on* attributes               : []
HTML comments                : 0
markup with text nodes removed contains:
  'http:' False   'https:' False   'file:' False   'ftp:' False
  '@import' False 'url(' False     '//' False
```

That last block is S26's ruling as a measurement: the byte reading and the
parser reading agree everywhere except inside text nodes.

**8.3 R46, both meanings.** The *document* makes zero external requests (above).
The *process* opens zero sockets: a full in-process analyze under a
`socket.socket` subclass that records construction produced an empty list. The
tester's version is stronger than mine — a child process, an audit hook on every
`socket.*` event, two control arms, and a canary whose own canary shows the
**unguarded** driver exits 0, so the difference is the guard and not the
sandbox. That is the right shape for an absence claim and I have nothing to add
to it.

**8.4 The `<script>`/`<style>` pins are taken from the parsed element.**
Verified by appending one line to `REPORT_SCRIPT` at render time: the SHA-256 of
the `<script>` element as `html.parser` yields it diverges from `SCRIPT_SHA256`,
while the unmodified render matches. The constant-against-constant assertion
exists separately and is labelled as the weaker one. A-d5 is true.

**8.5 The attribute allowlist has a real second oracle.** `ATTRIBUTE_SHAPES` in
`test_report_html_r34_r35_r36.py` is tester-authored from R34's prose, is a
table of *predicates over values* rather than a copy of the coder's value sets,
and makes an **unknown attribute name** a failure whatever its value. Beside it
sits an assertion that needs no allowlist at all: no attribute value in a
hostile render may contain `<`, `>`, `"`, `'` or a backtick. Two views, not one,
and the coder's own risk-list item — "an attribute I add in a later edit and add
to the allowlist in the same commit" — is the thing the name table catches. The
one direction neither covered was *widening*, which my sweep found and which
§6.2 closes.

**8.6 The rest, against the extended hostile render.**

```
credentials (aws_key_id, anthropic_key, private_key) : absent from html and json
payloads under --no-previews                          : none of the ten present
field markers under --no-previews                     : none present
RTL override U+202E                                   : absent (removed, not escaped)
'&amp;amp;' occurrences                               : 0
'&amp;lt;script&amp;gt;' occurrences                  : 1   (AC4's single pass, in the output)
longest whitespace-delimited run                      : 258 chars (R8's cap + markup)
```

**8.7 R47's determinism matrix covers what R47 requires.** `PYTHONHASHSEED` ∈
{0, 1, 2, "random"} — all four, not AC6's three — `TZ` ∈ {UTC,
America/Los_Angeles, Asia/Kolkata}, `LC_ALL` ∈ {C, en_US.UTF-8}, one combined
environment, in-process and in real subprocesses, hashing `report.html`,
`report.json` **and** stdout, plus two working directories, two absolute paths
to identical content, reversed path order and directory expansion. There is a
meta-test asserting the matrix itself contains R47's full sets, which is the arm
that stops the matrix being narrowed to make a failure go away — the one thing I
would have added, already there.

---

## 9. Suite, lint and types

Both interpreters verified to import the worktree before every measurement, with
`PYTHONPATH` pinned and `__pycache__` purged; `PYTHONDONTWRITEBYTECODE=1`
throughout. All three harness hazards the prior agents recorded were live in
this container and all three were handled.

| | CPython 3.11.15 | CPython 3.12.3 |
| --- | --- | --- |
| Handed to me | 2456 passed, 1 xfailed | 2456 passed, 1 xfailed |
| After three `review:` commits | **2555 passed, 1 xfailed** | **2555 passed, 1 xfailed** |
| `ruff check` | clean | — |
| `ruff format --check` | 85 files already formatted | — |
| `mypy --strict` | no issues in 34 source files | no issues in 34 source files |
| `tests/allowed_skips.txt` | empty | empty |
| Collection floors | at current counts | at current counts |

+99: 86 from `tests/test_untrusted_field_sweep.py`, 8 from the wave-5 gap
closures, 5 from the R49 scan, and 2 net in `test_json_report.py` (one test
rewritten in place, one added).

**The one xfail is the same one, and it is still the right call.** It is BUG-2's
display half — whether `--no-previews` should *blank* a trace-derived identifier
as well as redact it — which is a behaviour change in a pinned taxonomy and
therefore the PM's under the S16 ruling. Worth noting that §1 makes it one field
wider: `spans[].error.code` now joins the class of identifiers that are redacted
in both modes and not blanked, and the S16 resolution the increment-3 review
proposed (render an identifier as a 16-hex digest of itself under the flag)
would cover it automatically.

---

## 10. Merge verdict

**Merge**, with these conditions:

1. **The PM queues S24–S32.** In priority order: **S26** (settled here; the spec
   should say so, and it is the only reading under which R35 and R51 are both
   satisfiable), **S29** (R51 describes a fixture that cannot do what it says,
   and its field list should gain `SpanError.code`), **S27** (a privacy flag
   silently changes a verdict — the least documentation-shaped flag in the set,
   and the one most likely to surprise a user), **S25** (R34 contradicts itself
   on a literal reading), then **S24**, **S28**, **S30**, **S31**, **S32**.
2. **Trey's rate review is still outstanding**, and `cache_read` on
   `claude-fable-5-1` and `claude-mythos-5-1` is still the highest-leverage
   unchecked cell in the product. Nothing in this increment touched it and
   nothing in this increment can check it. The increment-3 review's §6 stands
   unchanged.
3. **A5's manual check now has two halves and is still owed.** Nothing in this
   branch has been opened in a browser. "The CSP meta is honoured", "the filter
   buttons work", "the figure renders" and "a 5,000-row table is usable" are
   unverified by construction: everything here is `html.parser` and byte
   inspection. Both agents say so plainly, which is the right way to owe
   something.
4. **Increment 5 should take C1** — one `text(value, *, kind)` boundary with an
   exhaustive `kind` — before it adds the narrator, which introduces a *third*
   untrusted string source into the same renderers.

Nothing in this branch blocks the merge. The blocker is fixed with the
generalization attached, the tester's six bugs are all real and all pinned, the
mutation ledger is 408 entries across five waves, and the suite is green on both
interpreters with a single deliberate, documented xfail.

What I would say to the owner in one sentence: **the fourth trace-derived field
to bypass redaction was found in this increment, as predicted, and for the first
time the check that found it is one that will find the fifth without anyone
having to remember the field exists.**
