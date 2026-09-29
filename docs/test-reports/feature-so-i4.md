# Test report: swarm-observer increment 4 (`feature/so-i4`)

Branch: `feature/so-i4` → `main`
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED)
Scope tested: **R32–R37, R46, R47, R51** and acceptance criteria **AC3, AC5, AC6**;
**R50**'s four outstanding canaries delivered and `CURRENT_INCREMENT` advanced to 4.
Read alongside: `docs/prs/feature-so-i4.md` (the coder's write-up, A-d1…A-d16, S24–S28).

## Result

| | 3.11.15 | 3.12.3 |
| --- | --- | --- |
| Before this increment's tests | 2098 passed, 1 xfailed | 2098 passed, 1 xfailed |
| After | **2456 passed, 1 xfailed** | **2456 passed, 1 xfailed** |
| `ruff check` / `ruff format --check` | clean (84 files) | — |
| `mypy --strict` | clean (34 source files) | clean (34 source files) |

Identical on both interpreters: same counts, same xfail, same golden bytes. The
one xfail is inherited — `test_r38_no_trace_derived_string_survives_the_flag`,
BUG-2's open half (S16), awaiting a PM ruling.

**I changed no application code.** Two test-side files outside this increment's
scope were changed and both are listed as findings: `tests/hostile_corpus.py`
and the corpus it builds (new), and one line of
`tests/test_reader_and_limits.py` that could not fail (**BUG-11**).

Six bugs, **BUG-8**…**BUG-13**. Four spec flags, **S29**…**S32**. A 77-mutant
sweep over the six modules this increment created or changed: 71 killed, 6
survived — one declared control arm and five equivalents, each named below with
its evidence.

The headline is **BUG-8**. R51's own named fixture does not load four of the
seven fields R51 names, and they are exactly the fields the increment-4 renderer
started rendering. Every assertion about the lane legend made against
`hostile.jsonl` alone is satisfied by a legend whose payload-bearing columns are
an em dash. That is instance nine.

---

## 1. Requirement × test coverage

| Req | What is covered | Where | Assessment |
| --- | --- | --- | --- |
| **R32** | the eight-entry table row by row; AC4's single-pass property in both sequential-replacement directions; `\x00`, `\x1b`, newline, CR, tab, DEL, C1, ZWSP, RLO, BOM, U+2028, NBSP each → exactly one space; per-character not per-run; ordinary text and U+0020 pass through; `escape_html` is the object `report/html.py` holds | `test_report_escape_r32.py` (41) | **Full.** Table compared against literals typed from R32, never against `ESCAPE_TABLE` itself. S24's interpreter tie is asserted as behaviour, not argued — see §3. |
| **R33** | the S14 alternation: the paired branch verbatim, the unterminated branch, branch order (with the one input that can tell), two blocks, complete+truncated, prose mention, idempotence over eight PEM shapes; the assignment replacement's two residuals; every trace-derived *identifier* redacted in both modes including three credential-shaped ones | `test_report_redact_s14.py` (23), `test_injection_probe_r51.py` | **Full for the HTML boundary.** The table itself remains covered by `test_redaction.py`. **BUG-12**, **BUG-13**, **S32** below. |
| **R34** | exactly one `<script>` and one `<style>`; digests taken from the **parsed** elements and separately from the constants, plus byte equality; the forbidden-API grep over `REPORT_SCRIPT` with its length pinned; CSP content verbatim and its position as the first head element after `<meta charset>`; every attribute independently classified; the generated allowlist checked for non-vacuity, for dead width and for its failure branch | `test_report_html_r34_r35_r36.py` (63), `test_injection_probe_r51.py` (135), canary `attribute_allowlist_injection` | **Full.** Two independent oracles — see §2. |
| **R35** | seven forbidden elements one test each; every `href` a fragment that resolves to an `id` in the same document; no URL-bearing attribute name (18 of them, `xmlns` and `style` included); no `on*`; no comment; byte scan for `http:`, `https:`, `file:`, `@import`, `url(`, `//` | same two modules | **Full**, under S26's parser reading, which I adopt and state. |
| **R36** | the six sections in order against a literal typed from R36; `<h1>` before the first section; nav fragments ↔ section ids; the spans cap at exactly 5,000 and 5,001; a finding naming a span past the cap renders the seq **without** a link; severity-descending → slug → `finding_id`, including with an unsorted input | `test_report_html_r34_r35_r36.py`, `test_end_to_end_ac5.py` (14) | **Full for the HTML half.** R43's "no narrative anchor" clause covered; the rest of R43 is increment 5's. |
| **R37** | `round_half_up` at nine points including three exact halves and the divergence from Python's `round`; both domain guards; positions from a hand-computed literal; minimum width; `trace_span_ms == 0`; a single timed span; null endpoints; no timed span at all; an empty trace; the A-d7 clamp with the unclamped formula's overflow asserted alongside; a trace whose every span ends before it starts; lanes; severity; every SVG attribute against a shape table typed from the requirement | `test_report_timeline_r37.py` (37) | **Full**, plus A-d7 and A-d8, which R37 does not state. **S30** below. |
| **R46** | a full analyze in a child process under a raising `socket.socket` subclass **and** an audit hook on every `socket.*` event, with two control arms (a socket before the run, and one constructed by `render_html` itself); an AST scan of the package for 14 transport modules; a runtime check for third-party clients | `test_offline_determinism_r46_r47.py` (20), canary `offline_socket_permitted` | **Full**, and its canary has a canary — the *unguarded* driver exits 0, so the difference is the guard and not the sandbox. |
| **R47** | two runs in one process; ten environments in-process and the same ten in subprocesses (4 × `PYTHONHASHSEED`, 3 × `TZ`, 2 × `LC_ALL`, one combined), hashing `report.html`, `report.json` **and** stdout; two working directories; two absolute paths to identical content; reversed path order; directory expansion ≡ explicit files; CLI bytes ≡ in-process bytes; no float-shaped token outside R29's two-decimal total; every timestamp in the document is one the trace carries | same module, plus two goldens | **Substantial**, with its scope stated: the cross-interpreter arm *is* the golden, and S24 bounds what that proves for a live trace. See §6. |
| **R50** | all four outstanding canaries delivered; `CURRENT_INCREMENT` 3 → 4; the ledger test rewritten because its old assertion ("the debt is non-empty") became false when the debt was paid | `tests/canaries/` (24 across four modules), `test_suite_integrity.py` | **Full.** All seven canaries R50 names now exist. |
| **R51** | the probe over R51's named fixture **and** over an extended trace that loads every field R51 names; per-field markers so a dropped field names itself; payload-in-text-node, payload-not-in-attribute, payload-not-in-script/style/title/comment; RTL asserted absent (not present); the 80,000-character string bounded; credentials absent and their markers present; both modes | `test_injection_probe_r51.py`, canary `injection_probe_identity_escape` | **Full for the extended trace; BUG-8 for the checked-in one.** |
| **AC3** | every clause, over four renders (fixture ± previews, extended ± previews); `report.json` parsed and swept the same way | `test_injection_probe_r51.py` | **Full.** |
| **AC5** | every detector fires (synthetic multi-agent trace); schema version; priced ∪ unpriced partitions the model calls; four groupings sum exactly; R36 order; exit 1 with `--fail-on critical` and both files written; the file on disk ≡ the in-process render; `--json`-only; A-d11's write-neither | `test_end_to_end_ac5.py` | **Full in substance, split in form.** AC5's named input cannot exist — **S28**, pinned by a test that goes red when S28 is closed. |
| **AC6** | the whole matrix, twice (in-process and in subprocesses) | `test_offline_determinism_r46_r47.py` | **Full** except the literal cross-interpreter comparison, which no single interpreter can make; the golden is the mechanism. |

### New test modules

| Module | Tests |
| --- | --- |
| `tests/test_report_escape_r32.py` | 41 |
| `tests/test_report_redact_s14.py` | 23 |
| `tests/test_report_timeline_r37.py` | 37 |
| `tests/test_report_html_r34_r35_r36.py` | 63 |
| `tests/test_injection_probe_r51.py` | 135 |
| `tests/test_offline_determinism_r46_r47.py` | 20 |
| `tests/test_end_to_end_ac5.py` | 14 |
| `tests/canaries/test_canary_injection_probe_identity_escape.py` | 7 |
| `tests/canaries/test_canary_attribute_allowlist_injection.py` | 6 |
| `tests/canaries/test_canary_offline_socket_permitted.py` | 5 |
| `tests/canaries/test_canary_metrics_redaction_dropped.py` | 6 |

Support modules, not collected: `tests/rendered.py` (the parsed view every
security assertion reads), `tests/pipeline.py` (one in-process analyze, inputs
and outputs together, so R34's allowlist can be generated from the inputs),
`tests/hostile_corpus.py` (R51's payload set as data, plus the extended trace),
`tests/golden/regenerate.py`.

---

## 2. What I did about the two things the coder flagged as riskiest

### `attribute_allowlist` is the coder's own oracle

I did not check membership in it and stop. `TestAttributesIndependentlyClassified`
enumerates every attribute the rendered document actually contains and asserts,
for each, a **shape written from R34's prose** — `id` is 16 hex or
`slug:12 hex` or a section id; `href` is a fragment whose target must resolve to
an `id` in the same document; `class` is lowercase tokens only; `data-seq`,
`data-agent`, `x`, `y`, `width`, `height` are decimal; `content` is the CSP
string verbatim. An attribute **name** not in that table is a failure whatever
its value, which is the arm that catches the hole the coder named as most
likely: *an attribute added to the renderer and to the allowlist in the same
commit*. The generated allowlist satisfies it; the table does not know the name.
Both are asserted, and the canary shows the pair is complementary rather than
redundant.

Three further checks on the allowlist itself: it is non-vacuous (≥ 8 names, > 50
values, no empty entry); it rejects a trace string and an integer one past the
last seq; and it contains **no entry for an attribute the document never
writes**, because an unused entry is dead width one commit before it is a hole.
Independently of all of it, no attribute value in a hostile render may contain
any R51 payload or field marker — the property the allowlist is a proxy for,
asserted without the proxy.

**Verdict: the allowlist is sound and is not over-permissive.** Every one of the
19 attribute names in a rendered hostile report is a constant, a closed-enum
member, a pattern-validated id, or a decimal integer this package computed. No
trace-derived string reaches an attribute in any of the four hostile renders.

### Goldens

Two, both built from a corpus checked in as code (`tests/hostile_corpus.py`) so
they are a function of this repository and of nothing on the machine that
produced them — asserted, not assumed, by rendering the same corpus from two
different temporary directories and comparing.

Each is paired with structural assertions over the *same bytes*, and each
docstring answers "what would make this red" with something other than "any
change":

* what the goldens pin that nothing else does is the document's **ordering and
  whitespace** — R47's actual promise, which no structural check can see;
* what they do **not** tell you is which property broke, which is why every
  security claim in the module is also an assertion of its own;
* they are also the only available **cross-interpreter** comparison: CI runs
  both legs against the same checked-in file, so a divergence between 3.11 and
  3.12 makes one leg red. That arm is asserted together with the reason it holds
  — no code point in the corpus is unassigned on the older interpreter — and
  beside a test that shows a *live* trace can still diverge (S24).

Regeneration is a script, never a test side effect, and a test asserts the
script contains no `def test`.

---

## 3. Bugs

Numbered continuing the **BUG-n** sequence from increment 3's BUG-7. "Pinned"
means a checked-in test fails if the condition recurs.

### BUG-8 — R51's named fixture leaves four of R51's seven named fields unloaded, and they are exactly the fields increment 4 started rendering

*Severity: **high** (a security probe that cannot fail). Pinned.*

**Failing input.** `tests/fixtures/traces/hostile.jsonl`, rendered:

```
AgentRun.agent_type       -> None
AgentRun.description      -> ""
AgentRun.parent_agent_id  -> None
AgentRun.depth            -> None
every Span.error          -> None
```

**Observed.** The lane legend's `type`, `description`, `parent` and `depth`
columns render `—` or nothing for every row; the spans table's `error` cell
renders `—` for every row. Every payload, credential and escaping assertion made
about those columns against this fixture passes because there is nothing in
them.

**Expected.** R51: "`hostile.jsonl` is a syntactically valid transcript in which
**every** free-text field — agent descriptions, tool names, tool inputs, tool
results, assistant text, model ids, **error details** — carries a payload".

**Spec clause violated.** R51, and therefore AC3, which is defined over it.

**Why it matters here and not in increment 3.** Those fields had no renderer
before this branch. A-d9 argues at length that the lane legend must render
*unconditionally*, because "a legend that appeared only when some span happened
to carry timing would mean 'no payload in the report' was sometimes true because
nothing was rendered". The reasoning is right and the fixture defeats it: the
legend renders unconditionally and has nothing to render. This is the direct
descendant of instance 7 (the `LEAKING_PATHS` sweep over an empty `findings`
array) and of the coder's own finding (the spans table rendering one preview of
three), with R51's probe as the subject.

**Structural cause, and why I did not fix it in the corpus.** Agent metadata
reaches the model only through A1's `agent-<id>.meta.json` sidecar, and
`tests/test_detector_corpus.py::TestCorpusShapeT6::test_t6_no_sidecar_metadata_sits_beside_the_corpus`
forbids a sidecar in `tests/fixtures/traces/` — correctly, because a sidecar is
not covered by R5's `trace_id` and would move every pinned finding id without
changing a fixture byte. I added the sidecar, watched that test go red, and
removed it. R51 and the corpus contract cannot both hold: **S29**.

**What I did instead.** `tests/hostile_corpus.py` builds an extended hostile
trace into `tmp_path` — three files and a sidecar — in which **every** field R51
names carries a payload, and a *distinct* one: each field's payload embeds its
own marker, so a field the renderer silently stops rendering takes its own
marker out of the document and the probe goes red **naming the field**. A shared
payload cannot tell "the lane legend rendered it" from "the spans table rendered
it", which is precisely how the coder's spans-table defect survived every
credential check.

**Pinned by.** `TestTheCheckedInFixtureLeavesFieldsEmptyBug8` asserts the gap, in
the style the increment-3 tester used for S14: when the fixture is fixed those
three tests go red and their docstrings say to delete them.
`TestEveryFieldReaches` asserts the property non-vacuously over the extended
trace (20 parametrized arms, HTML and JSON).

### BUG-9 — no checked-in fixture exercises S13, so `metric_value`'s redaction branch has never run over a real trace byte end to end

*Severity: **medium**. Pinned.*

**Failing input.** `hostile.jsonl`: every tool name in it fails R16's pattern, so
R16 replaces all of them with the literal `<non-conforming>` and
`Finding.metrics["tool_name"]` never carries a trace byte.

**Observed.** `{finding.metrics["tool_name"] for finding in findings} ==
{"<non-conforming>"}`. `sanitize.metric_value`'s `redact` branch is dead over
the whole corpus.

**Expected.** S13, ruled on by the increment-2 review and listed in this
increment's canary ledger as `metrics_redaction_dropped`, is about the name that
**does** conform and is still a credential: `AKIAIOSFODNN7EXAMPLE` matches
`^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$` exactly. R16 is a shape check and a shape
check is not a secret check; R33's redactor over `metrics` is the only thing
that keeps that string out of a report, and nothing in the corpus made it run.

**Spec clause.** R51 ("the credential-shaped payloads do not appear at all"),
R16, S13.

**Pinned by.** The extended corpus gives `agent-beta` an R16-conforming
credential tool name, repeated so `repeated_tool_call` fires and puts it in
`metrics`. `test_s13_a_conforming_credential_tool_name_is_redacted_in_metrics`
asserts the raw value is in `Finding.metrics` (the non-vacuous arm) and absent
from the document, and the canary drives both renderers with the guard removed.

### BUG-10 — no corpus contained a credential-shaped **identifier**, and two boundary call sites were provably untested

*Severity: **medium**. Found by the mutation sweep. Pinned.*

**Failing input.** Two mutants survived the first sweep:

* `I-H23`: `_ident(lane.agent_id)` → `_t(lane.agent_id)` in the lane legend;
* `I-H11`: `_ident(warning.detail)` → `_t(warning.detail)` in the warnings table.

**Observed.** Both replace redact-then-escape with escape-only, and the whole
suite stayed green — because no fixture anywhere carries an `agent_id` or a
`ParseWarning.detail` that R33 would redact.

**Expected.** R33 applies to every trace-derived string; the increment-3 review's
ruling on S16 puts `agent_id`, `parent_agent_id` and `ParseWarning.detail` in
the "redacted in both modes, never blanked" class precisely because they are
join keys. The alphabets that constrain them —
`^[A-Za-z0-9_.:\-]{1,64}$` and `^[A-Za-z0-9_.:\-]{0,64}$` — stop *markup*; they
admit `AKIAIOSFODNN7EXAMPLE` and a GitHub token verbatim.

**Spec clause.** R33, R51, and the S16 ruling.

**Pinned by.** The extended corpus now gives one agent the `agentId`
`AKIAIOSFODNN7EXAMPLE`, puts an AWS key in the sidecar `description`, and adds a
record whose unknown `type` is `ghp_` + 24 characters.
`test_r33_every_field_that_reaches_a_report_as_an_identifier_is_redacted` runs
six arms (three fields × both modes), and
`test_r33_the_identifier_arms_are_not_vacuous` asserts all three credentials are
really in the `Trace`. Both mutants are now killed.

One detail worth keeping: the record type had to be a **GitHub** shape, not an
AWS one. The mapper lowercases an unknown record type before it becomes a
warning detail, so `AKIA…` arrives as `akia…` and R33's uppercase-only
`aws_key_id` pattern cannot match it. A corpus that used the AWS shape there
would have asserted redaction of a string the redactor was never going to
touch — a check that cannot fail, dressed as one that passes. It is written into
the corpus module's docstring so the next editor does not undo it.

### BUG-11 — an assertion in the suite that is structurally unable to fail

*Severity: **medium** (suite integrity). Fixed in place.*

**Failing input.** `tests/test_reader_and_limits.py::TestIdentityR5::test_r5_digest_id_joins_parts_with_a_pipe`,
inherited from increment 1:

```python
assert digest_id("a|b") != digest_id("a", "b") or True  # documents the joiner
```

**Observed.** `X or True` is true for every `X`. The assertion documented
nothing and could not fail. Worse, the property it asserts is **false** —
`digest_id("a|b") == digest_id("a", "b")`, because `"|".join(("a|b",))` and
`"|".join(("a","b"))` are the same string — so the `or True` is what a red test
was made green with.

**Expected.** R49's whole posture, and this project's stated signature defect.
This is the ninth instance, sitting inside the suite that exists to catch it.

**Spec clause.** R49; the Security considerations' "suite integrity as a security
property".

**Fixed by** asserting the property in the direction it holds, with the reason
it is harmless written beside it (R5's parts are a 16-hex trace id, an
`AGENT_ID_PATTERN` agent id, a decimal seq and a `SpanKind`, none of which can
contain a `|` — also asserted). I grepped the rest of the suite for the same
shape; this was the only one.

### BUG-12 — `redact` overwrites an earlier pattern's marker, losing the label and the count

*Severity: **low**. Pinned as a characterization.*

**Failing input.**

```
TOKEN=AKIAIOSFODNN7EXAMPLE            -> TOKEN=[redacted:secret_assignment]
SECRET=[redacted:aws_key_id][redacted:openai_key]
                                      -> SECRET=[redacted:secret_assignment]
```

**Observed.** `aws_key_id` matches first and writes its marker; `secret_assignment`
runs last, matches that marker as its bare `\S{6,}` value alternative, and
replaces it. The report tells a reader that a secret-shaped *assignment* was
found, not that an **AWS key** was found — the more actionable fact, and the one
R33's label set exists to carry. Two credentials in one value collapse to one
marker, so the count is lost too.

**Expected.** R33 pins the pattern text and idempotence, and both hold. It says
nothing about which label wins when two patterns claim overlapping text, so this
is not a violated clause — it is a gap: **S32**.

**Pinned by** three tests in `test_report_redact_s14.py::TestAssignmentMarkerResiduals`,
each with a non-vacuous arm showing the label is correct away from an assignment.

### BUG-13 — the BUG-3 idempotence guard is prefix-sensitive and still deletes report text

*Severity: **low**. Pinned as a characterization.*

**Failing input.** `SECRET=x[redacted:secret_assignment]`
**Observed.** → `SECRET=[redacted:secret_assignment]`. The `x` is deleted, on the
**first** pass.
**Expected.** `_replace_assignment`'s docstring states, unconditionally, that the
guard stops the greedy value alternative "silently deleting a character of report
text". It does — on the second pass, and only when the marker is the first thing
after the separator.

**Spec clause.** None violated: R33 pins idempotence, and
`redact(redact(s)) == redact(s)` still holds for this input. The defect is that
the code claims a stronger property than it has, in a module whose docstrings are
the only record of why its exceptions exist.

**Pinned by** `test_r33_the_bug3_guard_only_holds_when_the_value_begins_with_the_marker`,
with the arm the guard *does* cover asserted beside it so this is not a claim
that the guard is inert. A trace has to contain the literal marker text to reach
it, so an attacker gains a shielded value and nothing else; redaction is a
courtesy, not a boundary.

### Described but not pinned

* **`CostReport.cost_of` still has no caller** (the review's C2). Confirmed: the
  spans table builds a `{seq: cost}` mapping, as A-d13 says. Dead code, not a
  defect; nothing asserts it stays dead.
* **Two unreachable branches in `build_timeline`** — the `lane is None` skip and
  the `index_of.get(span.agent_id, lane)` fallback. `Trace._pinned_invariants`
  makes both impossible for any `Trace` the model will construct, and the mapper
  builds `agents` from the spans. Defensible as fail-safe for a future adapter;
  worth knowing they have never executed. Related: the review's C4 caution about
  `seq == index` is structurally moot — the model *requires*
  `[span.seq for span in spans] == list(range(len(spans)))`.

---

## 4. Spec flags

Continuing from the coder's **S28**.

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S29** | R51 / T6 | R51 requires `hostile.jsonl` to carry a payload in "agent descriptions", but `AgentRun.agent_type`, `description`, `parent_agent_id` and `depth` reach the model **only** through A1's `agent-<id>.meta.json` sidecar, and the increment-2 corpus contract forbids a sidecar beside the corpus — because a sidecar is not covered by R5's `trace_id`, so adding one moves every pinned finding id without changing a fixture byte. The two rules cannot both hold, and the fields in question are the ones increment 4's lane legend renders. **BUG-8.** | Cheapest: amend R51 to say the probe's corpus may include a trace assembled at test time, and require it to cover every field R51 names — which is what `tests/hostile_corpus.py` does today. More thorough: fold the sidecar's bytes into R5's `trace_id` (a `TRACE_SCHEMA_VERSION` conversation) so a sidecar becomes checkinable and the T6 rule can be dropped. Either way R51's field list should be machine-checked against the corpus rather than prose, as `R51_NAMED_FIELDS` now is. |
| **S30** | R37 | R37 says spans with a null endpoint "are listed in a 'no timing' note"; it puts no bound on that list, and a trace may have two million of them. `report/html.py` caps it at `UNTIMED_LIST_CAP = 200` with an "…and N more" tail. That is a sensible reading and an **undeclared** one — unlike `SPANS_TABLE_CAP`, which R36 states, this cap appears in neither R37 nor the A-d series. | One clause on R37, in R36's words: the note lists at most a fixed number of seqs, names the total, and carries an "…and N more" line. Pin the number in the requirement so the constant is a transcription rather than a choice; the test now pins `200` as a literal for the same reason. |
| **S31** | AC4 | AC4 says: "Given the string `` </script><img src=x onerror=alert(1)>&`=" ``, When `escape_html` runs, Then every one of `&`, `<`, `>`, `"`, `'`, `/`, `` ` ``, `=` is entity-encoded exactly once". The string it names contains **no apostrophe**. A test that asserted all eight against that input would be asserting something about a character that is not there — which passes for every implementation, including one with no `'` row at all. | Add a `'` to AC4's string (it costs one character and makes the criterion self-consistent), or drop `'` from the enumeration and leave it to R32's table. The test asserts the seven that are present, asserts the apostrophe is absent so the discrepancy cannot be papered over, and covers `'` separately. |
| **S32** | R33 | R33 pins the pattern order and idempotence, and says nothing about **which label wins when two patterns claim overlapping text**. `secret_assignment` runs last and its bare `\S{6,}` alternative re-matches the marker an earlier pattern wrote, so `TOKEN=<AWS key>` is reported as `secret_assignment` and two credentials in one value become one marker. **BUG-12.** | One clause: a replacement's own marker text is never re-matched by a later pattern, and the first pattern to claim a span of text owns its label. Implementable as a guard in `_replace_assignment` that leaves any `[redacted:<label>]` it did not write alone — which also closes **BUG-13**, since the prefix case falls out of the same rule. |

**On the coder's S26, which asked to be settled before this probe was written.**
I adopt the **parser** reading and the tests say so: R35 constrains URL
*contexts* — attribute values, element types, stylesheet content — and not text
nodes, where R51 requires `javascript:alert(1)` to appear. The byte-level scan
that remains covers only what a parser cannot see (`@import`, `url(`, `//`, and
the absolute schemes), and `javascript:` is deliberately excluded from it with
the reason written in the docstring. A tester who read R35 as a `grep` would
have been right and would have had to delete R51's payload to pass. **The two
readings are still not reconcilable in the spec text and S26 still needs the
ruling** — my tests encode one of them.

**On S27.** Confirmed independently on my own corpus and pinned:
`--no-previews` removes the `critical` `unresolved_tool_call` finding, because
A10 blanks at ingest and R22's `unknown_tool` reason is decided by matching five
phrases against `tool_result_preview`. Two goldens rather than one, and a test
that asserts the difference is a *superset* relation so nobody can diff the two
documents and attribute the difference to blanking.

**On S24.** Reproduced, on both interpreters, and pinned as behaviour rather than
as a note. U+1F6DC is `Cn` on 3.11 (Unicode 14.0) and `So` on 3.12 (15.0);
`escape_html` renders it a space on one and itself on the other. Two tests hold
it open: one asserts `escape_html` follows `str.isprintable` for every code point
below U+3000, and one asserts a live trace carrying such a code point renders
differently — written to `tmp_path`, never checked in, so
`TestCheckedInDataIsInterpreterStableR8` is not violated. Both must be rewritten
if the PM adopts a fixed code-point rule, which is the notification.

---

## 5. The mutation sweep

### Methodology

`tests/mutations.json`'s declared methodology, re-run: the oracle is the **whole
suite** with `-x`, `PYTHONDONTWRITEBYTECODE=1`, `__pycache__` purged around every
mutation, the original text rewritten in a `finally`, a tree digest verified
between mutants, a drifted anchor reported **NOT-APPLIED** rather than skipped,
and **serially** — the ledger's recorded hazard is that concurrent sweeps produce
spurious kills from CPU contention against the suite's performance pins.

I added two clauses to the harness contract, and both were paid for by a failure
in this increment:

1. **Run the unmutated suite first and abort if it is red.** My first sweep
   reported its own **control arm killed** — the increment-3 signature exactly. A
   previous run had been `SIGKILL`ed mid-mutation and had left
   `report/sanitize.py` mutated on disk, so the baseline itself was failing and
   every verdict in that run was about the leftover, not the mutation. That is
   the "mutated baseline" hazard the ledger already warned about, observed. The
   harness now also restores on `SIGTERM`/`SIGINT`.
2. **Every kill records the pytest node id that produced it.** A verdict of
   "killed" with no node id cannot be told apart from a flake or a broken oracle,
   which is how the increment-3 run reached 289/289. Every kill below names its
   killer.

Both are written into `tests/mutations.json`'s harness comment.

### Operator set

The ledger's declared set, unchanged, drawn on as the code allowed:
relational flip; integer constant ±1; boolean-connective swap; guard-clause
drop; negation drop/insert; membership flip; slice/range bound off-by-one; sort-
and group-key component drop; normalization drop (`sorted`, `set`, `tuple`);
rung/branch reorder; call-argument swap; literal substitution; control-flow swap;
container-default swap. Plus one **declared control arm** (`control-no-op`).

Wave 4 by operator: literal 8, guard-drop 9, call-argument 7, negation 6,
relational 6, arith 4, int±1 6, normalization-drop 3, sort-key/-drop 2, reorder
3, slice-bound 2, and/or 2, quantifier 2, membership 1, container-default 2,
boundary 1, control-no-op 1.

### Modules covered — every module this increment created or changed

| Module | Wave-4 mutants | In the ledger in total |
| --- | --- | --- |
| `swarm_observer/report/escape.py` (new) | 11 | 11 |
| `swarm_observer/report/sanitize.py` (new) | 11 | 11 |
| `swarm_observer/report/timeline.py` (new) | 22 | 22 |
| `swarm_observer/report/html.py` (new) | 24 | 24 |
| `swarm_observer/report/redact.py` (S14 change) | 5 | 27 |
| `swarm_observer/cli/main.py` (`--out` wiring) | 4 | 51 |

`tests/test_suite_integrity.py::test_r49_the_ledger_covers_every_module_the_increment_touched`
now requires the four new modules with ≥ 8 mutants each, which is the decision
the coder declined to make unilaterally.

### Results

```
wave 4, first run, CPython 3.11.15, serial
TOTAL 77 killed 65 SURVIVED 12 not-applied 0
  -- ['I-E11-CONTROL', 'I-T12', 'I-T15', 'I-T17', 'I-T18', 'I-T22',
      'I-H02', 'I-H06', 'I-H11', 'I-H15', 'I-H22', 'I-H23']

after closing the six real gaps, re-run of all twelve plus I-H05
TOTAL 77 killed 71 SURVIVED 6 not-applied 0
  -- ['I-E11-CONTROL', 'I-T12', 'I-T15', 'I-T17', 'I-T18', 'I-H15']
```

**Six of the twelve first-run survivors were real gaps**, and closing them added
the tests named below. The other six are the declared control arm and five
equivalents.

#### The six real gaps, and what closed each

| Mutant | What it did | Why it survived | Killed by |
| --- | --- | --- | --- |
| `I-T22` | drop `max(0, …)` around `span_ms` | The clamp only matters when **every** timed span ends before it starts. A trace with one inverted span among several never reaches it, and that was the only shape any test had. Without the clamp, `span_ms` goes negative, the `span_ms == 0` branch is skipped, and `round_half_up` raises on a negative denominator — the whole render dies on a trace a backwards clock can produce. | `test_r37_a_trace_whose_every_timed_span_ends_before_it_starts_still_renders` |
| `I-H02` | `UNTIMED_LIST_CAP` 200 → 199 | The test computed its expectations *from the constant*, so it passed for every value of it. | the same test, rewritten to pin `200` as a literal and to count the seqs the note lists |
| `I-H06` | drop `finding_id` from `_grouped`'s sort key | R13 already returns findings in `finding_id` order and Python's sort is stable, so **every** input that came from the registry is already ordered. An end-to-end test cannot distinguish the two. | `TestFindingOrderR36::test_r36_the_renderer_orders_findings_it_was_handed_out_of_order`, which hands `render_html` an unsorted pair directly — the Modularity notes' "no renderer may assume its caller sanitized", read one key over |
| `I-H11` | `_ident(warning.detail)` → escape-only | **BUG-10**: no corpus carried a credential-shaped warning detail. | `test_r33_every_field_that_reaches_a_report_as_an_identifier_is_redacted` |
| `I-H22` | `_free(agent.description)` → escape-only | **BUG-10**: no corpus carried a credential in a description. | the same test |
| `I-H23` | `_ident(lane.agent_id)` → escape-only | **BUG-10**: no corpus carried a credential-shaped agent id. | the same test |

#### The six survivors that remain, by name

| Mutant | Operator | Classification |
| --- | --- | --- |
| `I-E11-CONTROL` | `control-no-op` (`[]` → `list()`) | **The declared control arm. It must survive**, and it does. It is the warrant for the 71 kills: a sweep that reports it killed is not reporting verdicts that come from the mutation. Mine did, once, and §"Methodology" says why. |
| `I-T12` | relational (`span_ms == 0` → `<= 0`) | **Equivalent, with evidence.** `span_ms` is assigned `max(0, …)` three lines above, so it is never negative. The only thing that could make it negative is `I-T22`'s mutation, and `I-T22` is killed. |
| `I-T15` | guard-drop (`offset_ms = max(0, …)`) | **Equivalent, with evidence.** `trace_start` is `min(starts)` over the same set `start` is drawn from, so the offset is never negative. Defence in depth against a future adapter, not dead code, but no `Trace` the model admits can tell the two apart. |
| `I-T17` | sort-key-drop (`sorted(rects, key=seq)`) | **Equivalent, with evidence.** `rects` is appended in `trace.spans` order and `Trace._pinned_invariants` rejects any span tuple whose seqs are not `range(len(spans))`, so the list is already sorted when `sorted` runs. |
| `I-T18` | normalization-drop (`sorted(untimed)`) | **Equivalent, with evidence.** Same reason: one pass over `trace.spans`, whose seqs the model pins to `range(len(spans))`. |
| `I-H15` | normalization-drop (`sorted(finding.metrics.items())`) | **Equivalent, with evidence.** `Finding._pinned_shape` *raises* unless `list(self.metrics) == sorted(...)`, so every valid `Finding` already has sorted metric keys. Unlike `I-H06`, this one cannot be reached by handing the renderer an unsorted input, because the **model** refuses to construct one. |

Four of the five equivalents are the same fact stated four times — the normalized
model's own validators make the renderer's defensive normalizations
unobservable. That is worth the PM's attention as a *design* observation rather
than a gap: `report/` re-normalizes what `model/` already guarantees, which is
correct under "guards are properties of functions, not of call paths" and means
four of this module's lines can never be shown to matter.

### Interpreter parity

The twelve first-run survivors plus `I-H05` were re-checked on CPython 3.12.3,
serially, with the same harness: 13 mutants, 7 killed, 6 survived, the same six
names as on 3.11 and the same killing tests. Verdicts identical. Waves 1–3 were not re-run: they cover modules this increment did not
touch, and re-running them would burn ~40 minutes to re-confirm a result the
ledger already records.

One honest note on that 3.12 re-check: I started it and then, before it
finished, ran a full `pytest` in the same container to identify the suite's one
xfail. That is the concurrency the ledger warns against. Every verdict in that
run agreed with the 3.11 run, so nothing was masked, but the discipline was
broken and it is recorded rather than quietly relied on.

---

## 6. What I did not cover, and why

* **Anything in a real browser.** Every assertion here is `html.parser` and byte
  inspection. "The CSP meta is honoured", "the filter buttons work", "the figure
  renders", "a 5,000-row table is usable" are all unverified, exactly as the
  coder said. A5's manual check is still owed and now has a second half.
* **A literal cross-interpreter byte comparison.** No single interpreter can
  make one. The mechanism is the checked-in golden, and its scope is bounded by
  S24: it holds for the corpus because no code point in the corpus is unassigned
  on 3.11, and a live hostile trace has no such guard. Both halves are asserted.
* **R41–R43 beyond one clause.** The narrator is increment 5's. R43's "no
  `<section id="narrative">` without `--explain`" is asserted because increment 4
  must not emit it; R43's fallback semantics, per-group validation and
  byte-identity test are not, and `traceability_pending.txt` records that in
  writing so the citation is not mistaken for coverage.
* **The `--explain` refusal path** (`_DEFERRED_FLAGS`) — covered by
  `test_cli_analyze.py` already.
* **Waves 1–3 of the mutation ledger**, per the note above.
* **Accessibility** beyond `role`/`aria-label`, which is not a requirement.
* **Performance.** I added no timing assertion. The suite's one known flake
  (`test_r23_blocked_agent_does_not_grow_quadratically`, a wall-clock scaling
  pin) did not reproduce in any of my ~100 full-suite runs, including 77
  back-to-back sweep runs.
* **`report.html` against a real Claude Code transcript.** A5's risk stands: the
  corpus is hand-authored, and S24 says the one place a live trace could diverge
  from it is the seven fields R8 does not normalize.

### A harness hazard worth inheriting, beyond the two the coder recorded

The coder recorded that `python3` outside the worktree imports
`/home/claude/swarm-observer` rather than the worktree. I hit a worse variant of
the same class: **a stale `__pycache__` served bytecode that did not match the
source on disk.** `swarm_observer/report/html.py` on disk had
`STYLE_SHA256 = "97b30480…"`; the imported module had `"312051e6…"`, and
`sha256(REPORT_STYLE)` agreed with the *file* while `STYLE_SHA256` agreed with
the *cache*. For about ten minutes I believed I had found R34's pin broken.
Purging `__pycache__` resolved it, and every measurement in this report was
re-taken afterwards with `PYTHONDONTWRITEBYTECODE=1`.

The lesson generalizes past this container: a suite that pins a constant against
a value derived from the same module cannot detect this, because both come from
the same stale object. The mutation harness purges `__pycache__` around every
mutation for exactly this reason, and the discipline belongs in any run that
compares source to behaviour.

---

## 7. What I want the reviewer to look at hardest

1. **BUG-8 and S29 together.** R51's probe, the requirement's own named
   artefact, cannot be satisfied by the corpus it names, and the reason is a
   collision between two rules that are each correct. My answer — build the
   trace at test time, give every field its own marker — closes the coverage
   gap but leaves R51's text describing a file that does not do what it says.
   The PM ruling matters more than my workaround, and the workaround should be
   deleted when it lands.

2. **Whether per-field markers are the right shape, or whether I have invented a
   second oracle nobody asked for.** `FIELD_MARKERS` is my design. It caught
   three real gaps that a shared payload could not have (`I-H11`, `I-H22`,
   `I-H23` are all "this field was never loaded"), and it is the mechanism that
   would have caught the coder's spans-table defect in one test rather than by
   hand. But it is checked-in data that a future tester could narrow, so
   `test_r51_every_field_the_requirement_names_is_covered_by_a_marker` compares
   it against R51's list. Please check that comparison is the right one.

3. **The five equivalent survivors, as a design signal.** Four of them say the
   same thing: `report/` normalizes what `model/` already guarantees by
   validator. Under the Modularity notes that is correct and deliberate. It also
   means four lines in the shipped renderer can never be shown to matter by any
   input the type system admits, and a reviewer should decide whether that is
   defence in depth worth keeping or a claim the code makes about itself that
   nothing can check.

4. **`I-H06` and the two ordering tests I kept.** The end-to-end ordering test
   does *not* kill the mutant, because R13 pre-sorts and Python's sort is stable.
   I kept it anyway and said so in its docstring, and added a second test that
   hands the renderer an unsorted pair. If the reviewer thinks the first test is
   now decorative, it should go — but the reason it is decorative is a fact about
   `detect/base.py`, not about `report/html.py`, and it will stop being true the
   moment anyone changes R13's sort.

5. **BUG-11.** One line, inherited, green for three increments, and structurally
   unable to fail — in the suite whose stated purpose is to catch that. It was
   found by grepping for `or True` after I wrote one myself and removed it. The
   grep is cheap and is not in CI; whether it should be is a reviewer's call.

6. **S32 and whether BUG-12/BUG-13 are worth a code change.** Both are low
   severity and both are characterizations rather than failures. The single
   guard I suggest — a later pattern never re-matches a marker an earlier one
   wrote — closes both and makes `redact`'s label set mean what it says. It is a
   change to a pinned taxonomy, so it is the PM's, not mine.

7. **The goldens.** Two files, 50 kB of checked-in bytes, regenerable by a
   script. I believe they earn their place because they are the only
   cross-interpreter comparison available and because every one of their
   security properties is *also* asserted structurally. If the reviewer disagrees
   that the pairing is real, the goldens are the first thing to delete and
   nothing else in this increment depends on them.
