# Case study: building swarm-observer with a four-agent pipeline

swarm-observer was specified, written, tested and reviewed by four AI agents
with the role boundaries of a real engineering team: a PM that writes the spec
and rules on ambiguities, a coder that implements to it, a tester that writes
every per-requirement test, and a reviewer that sees only the diff. The coder
cannot write tests. The tester cannot patch application code — a failing test
becomes a bug report. Every fix the reviewer commits must land with a test that
would have caught the bug.

Five increments, one branch and one PR each. The complete audit trail is in
this directory: [the spec](specs/swarm-observer-v1.md), and per increment a
[PR write-up](prs/), a [test report](test-reports/) and a
[review](reviews/).

This document is about what that arrangement actually produced, including the
parts that do not flatter it.

## The result

A CLI that reads Claude Code JSONL transcripts post-hoc and emits a JSON report
and a self-contained HTML report: 52 requirements, 20 tasks, 16 acceptance
criteria, ~2,565 tests, one runtime dependency, clean under `ruff` and
`mypy --strict` on CPython 3.11 and 3.12.

Two numbers are worth more than the rest.

**55.4%.** A Claude Code transcript writes one JSONL record per streamed
content block, and every fragment of one API response repeats that response's
`usage` object. Summing per record inflates cache-read tokens by 55.4% on the
grounding corpus — 826.9M against a true 532.2M — while every naive
implementation still produces a plausible report full of confident dollar
figures. The spec pins both the collapsed and the naive totals as constants so
a regression to naive summation changes a number loudly instead of quietly.
This was found by *reading real transcripts before writing the spec*, which is
the single highest-value hour in the project.

**Ten.** Ten separate instances of the same defect were caught across five
increments: **a check that reported green while being structurally unable to
fail.** They are the subject of the rest of this document.

## The signature defect

Not one of the ten was a wrong assertion. Every one was a *correct* assertion
about an input, an environment or a subject that could not make it fail.

| # | Where | What made it unable to fail |
|---|---|---|
| 1 | predecessor project | a fixture set with no case that could trip the check |
| 2 | predecessor project | an undeclared test dependency: 148 tests collected zero and reported as skipped, for two increments |
| 3 | predecessor project | a live-eval replay path that could never have passed |
| 4 | increment 1 | a fail-closed guard that depended on the interpreter's recursion limit — green on 3.11, broken on 3.12 |
| 5 | increment 2 | a CI measurement path with no caller, asserting a tautology |
| 6 | increment 2 | a self-reported mutation score against a self-chosen mutation set |
| 7 | increment 3 | a credential sweep run against a fixture whose `findings` array was empty |
| 8 | increment 3 | an assertion that made a mutation run report 289/289 killed — control arm included |
| 9 | increment 4 | a hostile corpus loading four of the rendered fields as empty, so every check over them passed vacuously |
| 10 | increment 4 | increment 3's credential sweep pinning a *package-authored constant* in the one field that was leaking, under a docstring promising "every trace-derived string" |

Instance 10 is the one to sit with. Increment 3 added a credential sweep
specifically to catch a class of leak. Its fixture's docstring said "a trace
whose **every** trace-derived string carries a distinct sentinel". Its body
said `SpanError(code="api_error", …)` — a constant this package wrote, in the
one field that was leaking, inside the check written to find exactly that. The
sweep was green for an entire increment, and the field it could not see reached
both reports raw, in both privacy modes.

Nine of the ten were found by a *different* agent than the one that wrote the
thing. That is the pipeline's strongest empirical result and the main argument
for the role separation: an author verifying their own work checks the thing
they were thinking about.

## The other repeated defect, and how it was finally closed

Four increments, four leaks, all the same shape: a trace-derived string
reaching a rendered report without passing through the redactor, because its
*type* looked safe.

| increment | field | why it looked safe |
|---|---|---|
| 1 | a preview field | — |
| 2 | `metrics.tool_name` | a shape check (`^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$`) had already approved it. `AKIAIOSFODNN7EXAMPLE` satisfies that pattern exactly: **a shape check is not a secret check** |
| 3 | `agent_id`, `parent_agent_id`, `ParseWarning.detail` | "these are identifiers" |
| 4 | `SpanError.code` | "an enumerated slug" — the model's own docstring said so, and it is built from the record's `error` field by the same function that builds a warning detail |

Each fix was correct. Each was about the field that was reported. The reviewer's
observation in increment 4 is the one that ended the series: *escaping* had been
made universal in increment 4 — every string a renderer writes goes through
`escape_html`, trusted or not — and that half of the boundary never leaked
once. Redaction stayed a three-way choice (`_t` / `_free` / `_ident`) that a
human made per call site, and "neither" was always available by writing the
shorter one.

Increment 5 collapsed them into one function whose `kind` is keyword-only, has
no default, and is exhaustive over a closed enum. Omitting the classification
is now a type error; adding a member without handling it is a type error;
`grep KIND_AUTHORED` is the complete list of claims the package makes that a
string is its own. The narrator, which arrived in the same increment as a third
untrusted string source, had to declare its own class — and the question "is
model output blanked by a privacy flag?" became a decision somebody wrote down
rather than a default nobody noticed.

Alongside it sits the first check in the repository whose field list is
**derived from the model** rather than written down: it walks `model_fields`,
requires every string-valued field to be classified as untrusted or authored,
puts a distinct legal AWS key in each untrusted one, asserts it is absent from
both renderers in both modes, asserts a harmless token in the same field *is*
present (so "absent" cannot mean "unrendered"), and reads the credential back
out of the constructed model (so a normalising validator cannot make every
assertion vacuous). The count of untrusted fields is pinned as a literal, so
the exhaustiveness check cannot be made green by reclassifying the field that
failed it.

## What the harnesses cost and what they bought

Six mechanisms exist purely to make the signature defect visible:

- **A collection floor** — a checked-in minimum test count per module. A module
  that silently collects zero fails the session. Cost: it has to be raised
  deliberately. That friction is the point; instance 2 is what happens without
  it.
- **A declared-skip ledger** — the allowlist of skip reasons is empty and every
  unlisted skip fails the run. A skip is a test that did not run.
- **A spec traceability map** — set equality, both directions, between the
  spec's requirement ids and the ids cited across the suite. An uncited
  requirement fails; a citation of a requirement that does not exist fails. A
  companion ledger records ids that are *not yet* covered, so the check is
  never red by design, and entries leave it only when a test arrives — a
  citation is deliberately not treated as coverage, and both coder and tester
  have avoided writing an id into a docstring for exactly that reason.
- **A checked-in mutation ledger** with a declared operator set, 408 entries
  across five waves, every survivor carrying either equivalence evidence or an
  open threat, and a **declared control arm** — a no-op mutation whose survival
  is required. That arm is the only reason instance 8 was visible: a run
  reported 289/289 killed and the control arm was among them, which meant every
  verdict in it came from a broken oracle rather than from a mutation.
- **An AST scan for assertions no input can falsify** — `assert <truthy
  literal>` and `assert X or True`. Added in increment 4 after the tester found
  `assert digest_id("a|b") != digest_id("a", "b") or True`, shipped in
  increment 1, green for three increments, documenting a property that was
  actually **false**. It carries a premise arm asserting it read more than 30
  modules and more than 1,000 assertions, because "no offenders" must not be
  able to mean "nothing was read".
- **Canaries** — for every guard, a test that deliberately breaks the guard's
  subject and asserts the guard raises. Monkeypatch `escape_html` to the
  identity function and the injection probe must go red; flip one byte of a
  golden in memory and the comparison must go red; permit a socket and the
  offline test must go red. One canary has a canary of its own: it shows that
  the *unguarded* driver exits 0, so the difference is the guard and not the
  sandbox.

The recurring lesson across all six is one sentence: **a guard needs a
non-vacuous arm.** "The credential is absent from the report" is satisfied
perfectly by a report that rendered nothing. Every absence assertion in this
repository is now paired with a presence assertion over the same field.

## Where the spec was wrong, and what that cost

Thirty-four spec flags were raised by the coder, the tester and the reviewer
across five increments. Most were small. Four are worth naming because they are
the kinds of thing a specification gets wrong even when it is careful.

**An acceptance criterion whose input cannot exist.** AC5 requires "a
multi-agent fixture trace with a known finding in every detector class". No
such trace can exist in the corpus, and the reason is structural rather than a
missing fixture: canonical order is by file basename, so merging files makes
same-named agents interleave, which destroys the per-agent signature sequence
one detector needs and the sliding window another needs. A criterion whose
input cannot exist is a check that cannot pass — the mirror image of the
project's signature defect, and just as corrosive.

**A requirement that contradicts itself.** One clause mandates a CSP `<meta>`
element; another lists the six kinds of attribute value the document may
contain, and `http-equiv` is not among them. On a literal reading the
requirement forbids the element it requires.

**Two requirements that contradict each other.** One says no URL of any scheme
may appear "anywhere" in the rendered HTML; another *requires*
`javascript:alert(1)` to appear in it, in a text node, as an injection probe.
They are consistent only under a parser reading — URL *contexts*, not bytes —
which is what the first requirement's own second sentence already said. The
reviewer settled it by measurement: removing every text node from the rendered
hostile document leaves markup containing no scheme of any kind, so the two
readings agree everywhere except the one place the probe requires them to
disagree.

**A privacy flag that silently changes a verdict.** `--no-previews` was
specified as an output-side guarantee, but the text is dropped at ingest, and
one detector decides one of its reasons by matching phrases against that text.
So the flag does not produce the same report with the text removed; it produces
a different verdict, and can drop a `critical` finding. Both the coder and the
tester filed it as a documentation gap. The reviewer disagreed and was right:
the user most likely to reach for a privacy flag is the one sharing a report
outside the team, which is the user least able to notice that a critical
finding is missing. It is now a warning in the README and a queued fix.

The general pattern: the spec was wrong most often where **two individually
correct rules met**. No single clause was careless. Every one of these took a
second agent reading the first agent's implementation of it.

## What the pipeline did not do

Stating this plainly is part of the exercise.

- **Nothing has been opened in a browser.** Every claim about the HTML report
  is `html.parser` and byte inspection. "The CSP meta is honoured", "the filter
  buttons work", "the figure renders" and "a 5,000-row table is usable" are
  unverified by construction. That check is owed by a human.
- **No real transcript has been analyzed end to end as a committed test.** The
  fixture corpus is hand-authored against the documented schema, because real
  transcripts contain the team's own prompts, file contents and
  credentials-adjacent output and must not be committed. A hand-authored corpus
  can drift from reality; a shape-contract test derived from the original
  inspection is the mitigation, and it is a mitigation rather than a guarantee.
- **The `--explain` live path has never executed.** The Anthropic adapter
  requires an optional extra and a real API key, and CI has neither by design.
  Everything below its import guard is unexecuted code — which is why it is
  written to be as thin as a wrapper can be, builds nothing, decides nothing,
  and classifies errors by exception type rather than by message. The narrator
  logic it wraps *is* exercised offline, over the same protocol, by a scripted
  fixture client. Instance 3 on the list above was a live path that could never
  have passed, and the response to it was to make the live path carry as little
  as possible rather than to test it in a way that could not fail.
- **The rate snapshot has not had a human sanity-check.** Two cells are the
  highest-leverage unchecked numbers in the product and no agent can verify
  them.

## What we would tell the next team

1. **Read the real artefact before writing the spec.** One hour of reading
   transcripts produced the 55.4% finding, the file-order-over-timestamps rule
   and the tolerated-versus-fatal split. None of the three is derivable from a
   format description, because there is no format description.
2. **Make the safety property universal rather than enumerated.** Escaping
   everything has never leaked in five increments. Redacting the fields
   somebody remembered leaked in four. The difference is not diligence; it is
   that one of them is a property of the code and the other is a property of a
   list.
3. **For every distinction a spec pins, at least one fixture must have two
   things to distinguish.** A corpus where every trace has one model key, one
   warning code and one preview per span cannot tell a right renderer from a
   wrong one, whatever the tests say. Three mutation survivors across three
   waves were this, at three different levels: a collection of one in the
   corpus, a collection of one in a *test oracle*, and a permission set nobody
   could widen-check.
4. **Every absence assertion needs a presence assertion beside it.** This is
   the same sentence as the signature defect, and it is the cheapest possible
   defence against it.
5. **Give a mutation sweep a declared control arm.** It is one no-op mutant and
   it is the only thing that can tell "the tests killed these" from "the
   harness reports failure regardless".
6. **Separate the author from the verifier, and let the verifier read the
   rendered output rather than the source.** Two of the most valuable findings
   in the project — a spans table that dropped the very text the injection
   probe existed to inspect, and the fourth redaction leak — were both found by
   *rendering a document and reading it*, not by reading code and not by
   running a passing suite.
