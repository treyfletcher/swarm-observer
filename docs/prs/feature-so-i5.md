# PR: swarm-observer increment 5 — the narrator, the replay seam, the docs, and C1

Branch: `feature/so-i5` → `main` (based on `feature/so-i4` at `444f8e4`)
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED)
Tasks: T18–T20. Requirements in scope: **R41–R45**; acceptance criterion **AC12**.
Also delivered: the increment-4 review's **C1**, and its README condition on **S27**.
Prior rulings honoured: `docs/reviews/feature-so-i4.md` (C1, C5, S26, S27, S29).

## Summary

Six commits. The first is the increment-4 review's C1 and changes **not one
rendered byte**; the rest build the narrator, the replay seam and the docs.

```
b6ce19b fix: remove two stray report artefacts committed by accident
eafcd6b docs(T20): README, docs/CASE-STUDY.md, docs/TRACE-SCHEMA.md
bba2e3d feat(R44; Out of scope, T19): the replay seam, declared and unimplemented
56f08d5 feat(R43, R38, R39): per-group fallback, the narrative section, --explain wired
f99ca69 feat(R41, R42): the narrator seam, the payload that cannot carry trace text
fa3a9f5 refactor(R32, R33; review C1): one text boundary with an exhaustive kind
```

`ruff check`, `ruff format --check` and `mypy --strict` are clean; the suite is
**2565 passed, 1 xfailed** on CPython 3.11.15 and 3.12.3 — the same single
inherited xfail. The whole suite passes with the credential environment
scrubbed and `anthropic` not installed, which is the only environment it has
ever been run in here.

**I wrote no new test module**, the same call the increment-3 and increment-4
coders made. Two existing tests were rewritten in place because this branch
made them false, and one tester-authored scan was narrowed because R44 made it
false; all three are itemised in §"What I changed in the suite, and why".

### The three things worth reading first

1. **AC12 holds in both halves, and the second one holds structurally.** The
   `--explain` payload's *type* has no field that can hold trace free text.
   Over the hostile corpus, the complete set of distinct string **values** in
   every request this run produced is 26 strings: six money strings, a version,
   a snapshot date, three registry titles, two snapshot model keys, three
   severities, three detector slugs plus `overall`, five metric keys, one
   authored metric value and `the whole run`. Not a filter — a type. §3.

2. **R42 and R44 contradict each other, and R42 and AC12 contradict each
   other.** R42 requires the payload to carry per-agent and per-model cost
   totals and to be built by `narrate/summary.py`; R44 forbids `narrate` from
   importing `cost`. R42 permits `metrics.tool_name` in the payload; AC12
   forbids anything a sentinel trace can put in a free-text field, and a
   sentinel satisfies R16's tool-name pattern exactly. Both resolved narrowly,
   both filed as **S34** and **S33**, and in both cases the narrow reading made
   the security property *stronger*.

3. **The live narrator path has never executed, and I have written down every
   line of it.** §"What has never executed" is not a caveat paragraph; it is a
   list.

## Requirements coverage

| Req | Where | Notes |
| --- | --- | --- |
| **R41** | `narrate/client.py` — `NarratorClient`, `NarrationRequest`/`NarrationResponse` and their component models, `NarratorError` + the three subclasses, `NARRATOR_ERROR_CODES`; `narrate/fixture.py` — `FixtureNarratorClient`; `narrate/adapters/anthropic.py` | "single-line messages that never echo request or response bodies" is enforced by the *type*: a `NarratorError` takes a code from a closed set and has no parameter a provider's message could pass through. The fixture client records every request in `self.calls`, raises script entries that are exceptions, and raises `AssertionError` on exhaustion. |
| **R42** | `narrate/summary.py` — `build_totals`, `build_requests`, `serialize_request`, `narration_groups`; the payload models in `client.py` | Enforced structurally. §3. Two spec collisions: **S33** (`tool_name`), **S34** (`cost` import). |
| **R43** | `narrate/narrator.py` — `validate_paragraph`, `deterministic_paragraph`, `narrate`; `report/narrative.py` — `Narrative`, `FALLBACK_CLASS`, `FALLBACK_PREFIX`; `report/html.py` — `_narrative_section`; `cli/main.py` — `build_narrative` | Per-group fallback, the two markers emitted by one branch, byte-identity by construction. §4. |
| **R44** | `narrate/adapters/anthropic.py` is the only module importing `anthropic`, lazily, inside the method; `replay/target.py` is imported by nothing, including `replay/__init__.py` | Asserted by the existing AST test, which is parametrized over every module and therefore grew by ten arms. One tester-authored scan narrowed — §"What I changed in the suite". |
| **R45** | `pyproject.toml` (`[explain]` extra unchanged; a mypy override for the absent stub); nothing in the default path imports `narrate` | The suite runs with the extra absent and every credential unset. Verified below under `env -u`. |
| **R38–R40** | `cli/main.py` — `--explain` wired, `_DEFERRED_FLAGS` deleted | `--help` shows the same surface; the flag now does what it says. |
| **AC12** | hand-verified below, both halves; the suite's version is the tester's | §3, §4. |
| **T19** | `replay/target.py` | Protocol + two frozen models, no implementation, no importer. |
| **T20** | `README.md`, `docs/CASE-STUDY.md`, `docs/TRACE-SCHEMA.md` | Including the review's required S27 note. |
| **C1** | `report/sanitize.py`, `report/html.py`, `report/json_out.py` | §2. |

## 1. What I built, requirement by requirement

### R41 — the client seam

`narrate/client.py` holds the `NarratorClient` Protocol (`complete(request) ->
NarrationResponse`), the frozen payload models, and the error taxonomy.

The taxonomy is the part that differs from a straightforward reading. R41 says
the errors are "all single-line messages that never echo request or response
bodies". A `detail` parameter would have made that a rule every raise site has
to remember, so there is no `detail` parameter: `NarratorError(code)` validates
`code` against `NARRATOR_ERROR_CODES` and renders `narrator: <code>`. An
adapter maps an SDK exception to a code by the exception's **type**, never by
its text, so there is no path by which a provider's message can reach a
diagnostic. An unknown code is a `ValueError` at the raise site.

`narrate/fixture.py`'s one non-obvious decision: script exhaustion raises
`AssertionError`, not a `NarratorError`. A `NarratorError` would be swallowed
by R43's fallback and a test that scripted the wrong number of answers would
pass while measuring something else. `narrate` re-raises `AssertionError`
explicitly past its own catch-all for the same reason. Note for the tester:
`cli.main.main`'s R11 catch-all *does* convert it to exit 2 with
`unexpected_error: AssertionError` — drive `cli.main.run` or
`narrate.narrator.narrate` directly if you want the message.

### R42 — what is sent

`narrate/summary.py` builds one payload per group. What it contains is exactly
R42's list, minus one item R42 permits (§S33): detector slug, severity, integer
and authored-slug metrics, per-severity counts, per-agent and per-model token
and cost totals, and the rate snapshot version. Plus counts R42 implies —
findings, spans, agents, model calls, priced and unpriced spans — and a bounded
per-finding detail list.

Two caps R42 does not state and the spec does not bound (A-e5):
`MAX_FINDINGS_PER_GROUP = 20` and `MAX_TOTAL_ROWS = 20`. A trace can carry
thousands of findings of one kind and thousands of agents; an unbounded payload
is one a provider refuses, and the failure mode would be a narrative that
silently disappears exactly on the runs worth narrating. The group's own counts
and totals are complete regardless — only the per-finding detail is capped, and
the cap is applied *after* the report's own severity-descending ordering, so
what survives it is what a reader would have looked at first.

### R43 — per-group fallback

`narrate/narrator.py`. One `complete()` call per group, in
`narration_groups` order: `overall` first, then one per detector that produced
a finding, in **registry order**. A detector with no findings gets no
paragraph, because R43 says "one paragraph per *finding group*" and a group
with nothing in it is not one.

Validation is R43's three checks in order, on the normalized paragraph:
non-empty, no control characters, at most 800 characters. Normalization folds
runs of the six ASCII whitespace characters and strips; it deliberately does
**not** remove control characters — a paragraph carrying an ESC is *rejected*
and falls back, not quietly cleaned.

### R44 / T19 — the replay seam

`replay/target.py`: one Protocol, two frozen models, no behaviour, no importer.
`replay/__init__.py` is a docstring with no re-exports so that `target.py` is
imported by literally nothing. The only design decisions a declaration-only
module makes are the model shapes, and each is argued in the docstring; the one
worth naming here is that `ReplayResult.notes` is a tuple of **enumerated
slugs**, never free text, because a v2 target talks to a live agent runtime and
is therefore exactly as attacker-influenced as a trace. That is C1's lesson
written into a seam before anything fills it rather than into a fifth per-field
fix afterwards.

### R45 — the extra is never required

Unchanged in substance and verified rather than assumed: `anthropic` is
imported inside `AnthropicNarratorClient._sdk`, the missing-package branch
raises `NarratorTransportError("sdk_not_installed")`, and the offline default
path for `--explain` is four deterministic paragraphs and exit 0. One
`pyproject.toml` addition: a mypy `ignore_missing_imports` override for
`anthropic`, as an override rather than an inline `# type: ignore` because
strict mode's `warn_unused_ignores` would turn the inline form red in an
environment where the extra *is* installed.

## 2. The C1 consolidation — what changed and what it now makes impossible

The increment-4 review's chief comment: escaping was made universal in
increment 4 and has never leaked; redaction stayed a three-way per-call-site
choice between `_t`, `_free` and `_ident`, and *that* half leaked in all four
increments. The recommendation was one `text(value, *, kind)` whose `kind` is
exhaustive over a closed enum.

**What changed.**

`report/sanitize.py` now exposes one function:

```python
TextKind = Literal["authored", "free", "identifier", "narrator"]

def text(value: str, *, kind: TextKind, previews: bool) -> str:
    if kind == "authored":   return value
    if kind == "free":       return redact(value) if previews else ""
    if kind == "identifier": return redact(value)
    if kind == "narrator":   return redact(value)
    assert_never(kind)
```

`free_text` and `identifier` are **deleted**, not kept as aliases: a surviving
public shortcut is a way to skip the classification, which is the hole. The
four kinds are also exported as `KIND_AUTHORED`/`KIND_FREE`/`KIND_IDENTIFIER`/
`KIND_NARRATOR`, for one prosaic reason and one good one — on CPython 3.11 an
f-string cannot contain a quote of its own delimiter, so `kind="authored"`
inside `f"…{w(x, kind="authored")}…"` is a syntax error on the older
interpreter CI builds; and `grep KIND_AUTHORED` is now the complete list of
claims this package makes that a string is its own.

`report/html.py` gains `_Writer`, constructed once per render from the run's
`RenderOptions` and threaded into every section. `_t`, `_free`, `_free_or_dash`,
`_ident`, `_anchor` and `_money` are gone; 69 call sites now read
`w(value, kind=KIND_…)`. `report/json_out.py` routes every string that comes
out of a `Trace`, a `Finding` or a `CostReport` through the same function — 52
call sites. For that document the `authored` branch is a literal no-op and its
value is the forced classification, not a transformation, which is worth
saying plainly rather than implying that something is now redacted that was not.

**What it makes impossible.**

* Rendering a string without naming its class. `kind` is keyword-only with no
  default, so omitting it is a `mypy --strict` error, not a quieter boundary.
* Adding a member to `TextKind` without handling it. `assert_never` makes that
  a type error too.
* Passing the wrong `previews` value at a call site. There is no `previews`
  argument at a call site any more; the writer carries the run's options.
* Answering "which boundary does this field use?" by writing the shorter
  helper. There is no shorter helper.

**What it does not make impossible**, stated because a coverage map that
overclaims is how this got to four increments: it does not stop somebody
classifying a new field as `authored` when it is trace-derived. That is exactly
what happened to `SpanError.code`. What catches *that* is
`tests/test_untrusted_field_sweep.py`, whose field list is derived from
`model_fields`, and which passes untouched — all 86 arms — on this branch.
prose→fields and model→fields are complements, as the review said; C1 adds a
third thing, which is that the *act* of classifying is now mandatory and
greppable rather than implicit in which helper somebody typed.

**Two exceptions, both named at their call sites.**

1. `_n(value: int)` does not go through the writer. The exemption is a proof
   rather than a judgment: `str` of an `int` is `-?[0-9]+`, an alphabet
   containing no character in `ESCAPE_TABLE`, no non-printable, and nothing any
   R33 pattern can match.
2. The HTML findings table's metrics line applies `metric_value` (which
   classifies by key, from `TRACE_DERIVED_METRIC_KEYS`) and then escapes the
   composite as `authored`. Re-classifying at the writer would be *free* —
   R33's redaction is idempotent — and that is precisely the reason not to: the
   second redaction would produce identical bytes with or without the first,
   and `tests/canaries/test_canary_metrics_redaction_dropped.py`, whose only
   job is to show that call is load-bearing, would go green with the call
   deleted. A guard hidden behind an idempotent second guard is a guard nothing
   can prove.

**Evidence it changed nothing.** Both checked-in goldens, the full 135-test
injection probe, the 86-arm untrusted-field sweep and the determinism matrix
pass untouched; the commit's own diff changes 391 lines of `html.py` and 176 of
`json_out.py` and zero bytes of any rendered document.

**The narrator is the proof the shape was worth having.** Adding a third
untrusted string source to two renderers forced exactly one new decision —
"which class is model output, and is it blanked by `--no-previews`?" — and the
enum made that decision impossible to skip. It is A-e9, and under the old
three-helper shape it would have been whichever helper the author reached for.

## 3. How the no-trace-content property is enforced — **structurally**

Two layers, and neither is a filter.

**Layer 1: the payload's type has no field that can hold free text.** Every
string-valued field in the request tree is a `Literal`, a member of a closed
vocabulary this package computed (`NARRATION_GROUPS`, `DETECTOR_SLUGS`,
`NARRATION_TITLES`), or a pattern-constrained money / snapshot-key / version /
metric-key / authored-metric-value string. A pydantic validator rejects
anything else, so a request holding a sentinel is not one that gets sent and
cleaned — it is one that cannot be constructed.

**Layer 2: the builder has almost nothing to leak.** R44 forbids `narrate` from
importing `ingest`, `cost` and `report`, and this package additionally does not
import `model`. There is no `Trace`, no `Span`, no `SourceFile`, no
`CostReport` and no `SpanCost` in `summary.py`'s scope — the cost figures
arrive as already-formatted strings and integers from `cli/main.py`.

The residual surface, stated exactly: the one object in scope that has touched
the trace is a `Finding`, because R42 says the payload is built "from findings
only". Its trace-derived surface is `previews` and `metrics["tool_name"]`.
`previews` has no field in the payload type to be assigned to. `tool_name` is
dropped by consulting `detect.base.TRACE_DERIVED_METRIC_KEYS` — the same
machine-readable list the renderers' redaction policy consults — so a second
trace-derived metric key added in v2 is dropped without anybody remembering to.
`agent_ids` and `span_seqs` become counts.

**The measurement.** Built over `tests/hostile_corpus.py`'s extended hostile
trace, the complete set of distinct string *values* across every request of one
run:

```
'0.000006' '0.000050' '0.000056' '0.000144' '0.000200'   (money, R29 6dp)
'1.0.0'                                                   (request version)
'2026-09-10'                                              (snapshot date)
'Failed tool call' 'Repeated identical tool call' 'Unresolved tool call'
                                                          (registry titles)
'the whole run'                                           (OVERALL_TITLE)
'claude-haiku-4-5' 'claude-sonnet-4-5'                    (snapshot keys)
'critical' 'info' 'warning'                               (Severity)
'failed_tool_call' 'repeated_tool_call' 'unresolved_tool_call' 'overall'
                                                          (group keys)
'failures' 'first_seq' 'last_seq' 'occurrences' 'reason'  (metric keys)
'unknown_tool'                                            (authored metric value)
```

and the sentinel sweep:

```
### AC12, second half -- the sentinel payload
  groups                                      : ['overall', 'repeated_tool_call', 'failed_tool_call', 'unresolved_tool_call']
  serialized payload                          : 10621 bytes
  distinct sentinels / payloads searched      : 26
  present in the Finding objects it was built from: ['Span.agent_id', 'Span.tool_input_preview',
      'Span.tool_name', 'Span.tool_result_preview', 'credential.aws_key_id', 'metrics.tool_name']
  present in the serialized payload           : none
```

The second line from the bottom is the non-vacuity arm and it is the one that
matters: six sentinels — including an R16-conforming AWS key sitting in
`metrics.tool_name` — are demonstrably **in** the objects the payload was built
from, and none reaches the payload. An absence assertion over an empty subject
is instance seven of this project's signature defect; this one has a subject.

## 4. AC12, by hand

Both halves, over the 26-fixture corpus restricted to three detectors so the
run has exactly AC12's four groups.

```
### AC12, first half
  exit without --explain      : 1
  exit with    --explain      : 1   (unchanged: True)
  groups asked                : ['overall', 'repeated_tool_call', 'failed_tool_call', 'unresolved_tool_call']
  script entries left         : 0
    overall              narrator  None                  Overall, this run is dominated by repeated t
    repeated_tool_call   narrator  None                  The same tool ran with identical arguments s
    failed_tool_call     fallback  transport_failed      Failed tool call (failed_tool_call) produced
    unresolved_tool_call fallback  response_too_long     Unresolved tool call (unresolved_tool_call)
  two model paragraphs render : True
  the 900-char paragraph      : absent = True
  class="narrative-fallback"  : 2 occurrences
  'Deterministic summary:'    : 2 occurrences
  strip section == no-explain : True  (55520B -> 53879B; baseline 53879B)
```

The script is AC12's: two valid paragraphs, `NarratorTransportError` on the
third group, a 900-character paragraph on the fourth. The exit code is 1 both
ways — `--fail-on critical`, a critical finding exists — so this also exercises
the direction R39 cares about least and is easiest to get wrong: `--explain`
must not turn a 1 into anything either.

And under both modes:

```
### AC12, first half, under --no-previews
  [default      ] strip == baseline: True | narrator paragraphs rendered: 4
  [--no-previews] strip == baseline: True | narrator paragraphs rendered: 4
```

### Why the byte-identity is a property of the construction

Four things had to be true, and each is a decision rather than an accident:

1. the section is a contiguous run of **whole lines** at R36's fixed position;
2. `NARRATIVE_SECTION_ID` is **not** in `SECTION_IDS`, which drives the nav — a
   nav entry would be a byte outside the section that `--explain` changed;
3. `RenderOptions` gains **no** `explain` field, though the pattern of the
   other three invites one, because both renderers write those options into
   their header;
4. `REPORT_STYLE` and `REPORT_SCRIPT` are **untouched**, so `SCRIPT_SHA256`,
   `STYLE_SHA256` and both checked-in goldens are unchanged. The `narrative`
   and `narrative-fallback` classes are allowlisted and unstyled. A stylesheet
   edit would not have violated R43 literally — it moves both renders equally —
   but it would have moved every no-`--explain` render in the repository, which
   is what R43 is about. (A-e11.)

### The rest of R43

```
### R43: a whole-run failure falls every group back, and asks once
  exit 0 | stderr '' | client calls 1 | fallbacks 4 of 4 | reasons ['no_credentials']

### R45/R43 offline default: no anthropic, no key, no injected client
  exit 0 | stderr ''
  calls 1 | fallbacks 4 of 4 | reasons ['sdk_not_installed']
  overall paragraph: This run produced 6 findings from 3 detectors: 1 critical, 3 warning,
                     2 info. The trace holds 5 agents, 58 spans and 1...

### R43 validation, each arm
  empty            -> response_empty   (narrator: response_empty)
  control char     -> response_control_characters   (narrator: response_control_characters)
  801 chars        -> response_too_long   (narrator: response_too_long)
  800 chars        -> accepted, len 800
  newlines folded  -> accepted, len 3

### R43: narrator output is untrusted -- redacted, escaped, text-node only
  raw AKIA...            html False  json False
  raw sk-ant-...         html False  json False
  [redacted:aws_key_id]  present in html True, json True
  raw '</script><script>' in html: False
  escaped form in html          : True
  <script> elements in document : 1

### R43/R46: narrate/ is not imported on the default path
  [without --explain] swarm_observer.narrate in sys.modules: False
  [with    --explain] swarm_observer.narrate in sys.modules: True

### R47: the --explain render is deterministic
  three runs -> 1 distinct (html, json) digest pair(s): [('c7ebb551e84523fd', 'a52f38cd3a62f053')]
```

Two of those are worth a sentence.

**"client calls 1 | fallbacks 4 of 4."** R43's third bullet ends "a transport
failure, an auth failure, a missing `anthropic` package, an absent API key, or
a timeout all take the same path: **every group falls back**", and AC12 scripts
a transport failure on the third of four groups and a valid answer on the
fourth — which is only satisfiable if a transport failure falls back for its
own group and the loop continues. The two are consistent under one reading and
it is the one implemented: the conditions differ in **scope**, not in path. A
missing SDK, an absent credential and a rejected credential are properties of
the run — asking again cannot succeed — so the first one ends the narration and
every remaining group falls back with that code, which is literally "every
group falls back". A transport failure, a timeout and an unusable response are
properties of one call. Both take R43's fallback path and neither changes the
exit code. (A-e6.)

**`narrate in sys.modules: False`.** R43 says that with the flag off "`narrate/`
is never imported by the analyze path". Every import of `narrate` in this
package is inside `cli.main.build_narrative`, and the type annotations that
need those names are under `TYPE_CHECKING`. R46's no-egress promise is at its
strongest when the code that could open a socket is never loaded.

### The replay seam

```
### The replay seam
  modules importing swarm_observer.replay: none
  ReplayTarget is a runtime-checkable Protocol: True
  ReplaySelection() -> ReplaySelection(span_seqs=(), agent_ids=(), timeout_seconds=300, dry_run=True)
  ReplayResult(source_trace_id='0'*16) -> ReplayResult(source_trace_id='0000000000000000',
      replay_trace_id=None, replayed_spans=0, diverged_spans=0, notes=())
```

## 5. What has never executed

Every path below needs a real API key, a network, or the `[explain]` extra
installed. CI has none of the three by design (R45), so none of this has run,
here or anywhere. It is a list rather than a paragraph because instance 3 of
this project's signature defect was "a live-eval replay path that could never
have passed", and an honest inventory is the only defence a coder can offer.

**In `narrate/adapters/anthropic.py`, never executed:**

| line | what it is |
| --- | --- |
| `import anthropic` succeeding | the extra is not installed here; only the `ImportError` branch has run |
| `return anthropic` | same |
| `sdk.Anthropic(timeout=…)` | client construction |
| `client.messages.create(...)` | the one outbound request in the product |
| the seven `except sdk.<Error>` clauses | every one of them. If the SDK ever renames one, `except sdk.X` raises `AttributeError` at except-evaluation time, which `narrate`'s catch-all turns into a `provider_error` fallback — so the failure mode is a deterministic paragraph, not a crash, but the mapping itself is unverified |
| `extract_paragraph(message)` against a real SDK message | it has been driven with stub objects by hand; never with the real type |

**Executed, offline, and worth distinguishing from the above:** the
`ImportError` branch (CI has no `anthropic`, so `sdk_not_installed` is the live
answer — see the `calls 1 | fallbacks 4 of 4` run), `_credential_present`
returning False under R45's scrubbed environment, and `system_prompt()` /
`user_prompt()`, which are pure functions of a `NarrationRequest`.

**What that unexecuted code is *not* responsible for**, which is the reason it
is thin: it builds nothing (the payload is `serialize_request`'s output, and
this module never sees a `Trace`, a `Finding` or a `CostReport`), it decides
nothing (validation, fallback and the exit-code guarantee are in
`narrate/narrator.py`, exercised offline over the same Protocol by the fixture
client), and it classifies by exception type rather than by message, so it has
no channel through which a provider's text could reach a diagnostic.

**Also never executed anywhere:** every symbol in `swarm_observer/replay/` —
by design, and asserted.

**Also never done, inherited and still owed:** nothing in this repository has
been opened in a browser. "The CSP meta is honoured", "the filter buttons
work", "the narrative section reads well" and "a 5,000-row table is usable" are
unverified by construction, and the narrative section adds a fourth item to
that list. Trey's rate sanity-check is also still outstanding.

## 6. Assumptions and interpretation calls (A-e series)

- **A-e1 (the narrator is asked one group at a time, in registry order).** R43
  says "one paragraph per finding group plus one overall paragraph"; AC12
  scripts four entries against four groups, which only works if one group is
  one call. Order is `overall` first, then registry order — not the findings'
  severity-descending order, because that moves when one finding's severity
  changes and a narrative whose paragraph order depends on how bad the run was
  is harder to diff between two runs.

- **A-e2 (a detector with no findings gets no paragraph).** "One paragraph per
  *finding group*", read literally. The alternative — a paragraph per
  registered detector — would make seven of eight paragraphs say "nothing
  happened" on a clean trace, and would make the payload's `groups` list a
  function of the registry rather than of the run.

- **A-e3 (`metrics.tool_name` is dropped from the payload, though R42 permits
  it).** R42's list includes "``tool_name`` values already constrained by R16";
  AC12 requires that no free-text field of a sentinel trace appear in the
  serialized payload, and `SENTINELSpantoolname` satisfies R16's pattern
  exactly, as does `AKIAIOSFODNN7EXAMPLE` (which is S13's whole point). The two
  clauses of one requirement pair disagree and I have taken the narrower.
  Implemented by consulting `TRACE_DERIVED_METRIC_KEYS`, not by naming the key.
  **PM**: see **S33**.

- **A-e4 (the payload's cost figures are supplied by `cli/main.py`, not read
  from a `CostReport`).** R42 requires per-agent and per-model totals in a
  payload built by `narrate/summary.py`; R44 says `narrate` imports only
  `model`, `detect` and `narrate`. Both are pinned and an import cannot satisfy
  both. R44 wins: it has a checked-in AST test, and obeying it makes §3's
  property stronger rather than weaker. **PM**: see **S34**.

- **A-e5 (the payload is capped at 20 findings per group and 20 cost rows).**
  R42 bounds nothing. An unbounded payload is one a provider refuses on a large
  trace, and a narrative that disappears on the biggest runs is the worst
  available failure mode. Counts and totals are complete; only per-finding
  detail is capped, after the report's own ordering.

- **A-e6 (fatal codes stop the run's narration; per-call codes do not).**
  R43 lists five conditions in one sentence and AC12 requires one of them to be
  per-group. They differ in scope, not in path: `sdk_not_installed`,
  `no_credentials` and `auth_rejected` end the narration and fall every
  remaining group back with that code; `transport_failed`, `timeout`,
  `rate_limited`, `provider_error` and the three response codes fall back for
  their own group and the loop continues. Both take R43's fallback path;
  neither touches the exit code. Measured above: an auth failure produces
  **one** call and four fallbacks.

- **A-e7 (paragraph normalization folds six named ASCII whitespace characters,
  and control characters are rejected rather than cleaned).** Deliberately not
  `str.split()` and not `str.isprintable()`: the narrator is a **new** untrusted
  string source and **S24** is an open flag about `str.isprintable()` moving
  between interpreters, so introducing a second Unicode-table-dependent
  predicate on the way in would widen a defect already recorded. C0, DEL and C1
  are named by number. A paragraph with an ESC falls back visibly instead of
  being tidied.

- **A-e8 (a `NarratorError` carries a code from a closed set and nothing
  else).** R41's "never echoes a request or response body" becomes a property
  of the type rather than a rule at each raise site. The cost is that a
  provider's diagnostic text is unavailable for debugging a live failure, which
  is the right trade for a tool whose output is forwarded to colleagues.

- **A-e9 (narrator output is redacted in both modes and never blanked).** The
  `narrator` kind is the decision C1's enum forced into the open. R42
  guarantees the narrator was never shown a byte of trace free text, so its
  paragraph cannot contain any, and blanking under `--no-previews` would empty
  the one section `--explain` exists to produce. It is still redacted, because
  a compromised narrator is an attacker-influenced string source and R33 costs
  nothing here — measured above: an `AKIA`-shaped string in a paragraph becomes
  `[redacted:aws_key_id]`.

- **A-e10 (the narrative section has an `id` and no nav entry).** R36 gives it
  an anchor; R43 requires that stripping the section leaves byte-identical
  output. A nav link would be a byte outside the section that the flag changed.
  The consequence is a section the nav does not list, which is a small
  usability cost for a pinned security-adjacent property. **PM**: if a nav
  entry is wanted, R43's byte-identity clause has to say "the section and its
  nav entry".

- **A-e11 (no stylesheet change; the narrative classes are unstyled).**
  `narrative` and `narrative-fallback` are allowlisted `class` tokens with no
  CSS rule. Editing `REPORT_STYLE` would change `STYLE_SHA256` and both
  checked-in goldens, and would mean increment 5 moved every no-`--explain`
  render in the repository — the opposite of what R43 is for. R43's fallback
  markers are the class and the *visible prefix*, and the prefix is what a
  reader sees; a colour would have been nicer and is a v2 edit that costs a
  golden regeneration, deliberately.

- **A-e12 (`report/narrative.py` is a new module in `report/`).** The same
  addition A-d2 made for `sanitize.py`, for the same reason and with the same
  request: R44 puts `narrate` and `report` on separate branches, so neither can
  import the other's types, and `cli/main.py` maps between them. A shared type
  in `model/` was rejected — R2 pins that module's contents exactly and says
  "no other field exists on these models in v1". **PM**: one ruling covers
  `sanitize.py` and `narrative.py`, or R44's layout block should say `report/`
  may hold modules it does not list.

- **A-e13 (`report.json` gains a `narrative` key, only under `--explain`).**
  R36 says the JSON report "emits the same data as JSON", and a machine
  consumer of an `--explain` run would otherwise get nothing. The key is absent
  without the flag, so no no-`--explain` document changes. It carries
  `fallback`, the enumerated `reason` and the call count, so "never asked" is
  distinguishable from "answered badly" — which the rendered prose deliberately
  makes look the same.

- **A-e14 (the narrator seam is a `narrator=` keyword on `main`, `run` and
  `analyze`).** AC12 needs a `FixtureNarratorClient` to reach the real
  `--explain` path. A flag or an environment variable would have put a test
  affordance into the product's surface; a keyword-only parameter defaulting to
  `None` does not appear in `--help` and cannot be reached from a command line.

- **A-e15 (`FixtureNarratorClient` ships in the package, not in `tests/`).**
  R41 names it under `narrate/`, and it is the right place: AC12 is a criterion
  about the product, and the client opens no socket, reads no environment
  variable and imports nothing optional.

## 7. Spec flags

Continuing from the increment-4 reviewer's **S32**.

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S33** | R42 / AC12 | R42 says the `--explain` payload may contain "`tool_name` values already constrained by R16". AC12 says that given "a fixture trace whose every free-text field is a distinctive sentinel … no sentinel appears in [the serialized payload]". A tool name **is** a free-text field of a trace, and R16 is a *shape* check that admits `SENTINELSpantoolname` and `AKIAIOSFODNN7EXAMPLE` alike — S13's entire finding. So R42 permits into the payload exactly the byte AC12 forbids from it. | Delete "and `tool_name` values already constrained by R16" from R42's list, and say instead that the payload carries **no** trace-derived string of any kind. That is what is implemented, it is what AC12 tests, and it costs the narrator a tool name it can already infer nothing from — the payload keeps the occurrence counts and the detector slug, which is what the paragraph is actually about. |
| **S34** | R42 / R44 | R42: "The `--explain` request payload is **built by `narrate/summary.py`** … and contains … **per-agent and per-model token and cost totals**; the rate snapshot version." R44: "`narrate` imports only `model`, `detect` and `narrate`." Those figures live on `cost.compute.CostReport` and `cost.source.SnapshotMeta`, and formatting a `Decimal` needs `cost.compute.format_usd`. The two requirements cannot both be satisfied by an import, and R44's is the one with a checked-in AST test. | Two options and I prefer the second. (a) Add `cost` to `narrate`'s allowed imports in R44. (b) State in R42 that the cost figures are **extracted by the CLI** — the one module R44 lets see both sides — and passed to `narrate/summary.py` as integers and formatted strings. (b) is what is implemented and it is better than a wider import rule: with `cost` off-limits, `narrate/summary.py` has no object in scope that carries a recorded model id or an agent id, which is half of AC12's structural guarantee. A clause under R42 naming the split would make that deliberate rather than incidental. |
| **S35** | R49 / R41 / T18 | R49 requires that the `live_narrator` marker's "deselection is additionally verified by a test asserting the marker deselects a **non-zero** count". **No test carries the marker**, so `-m "not live_narrator"` deselects zero and that clause is unsatisfiable today. T18 names the missing artefact — a "`live_narrator`-marked opt-in smoke test gated on `SWARM_OBSERVER_LIVE_NARRATOR=1`" — and it is a test, so it is the tester's and not mine. There is a second-order problem the PM should rule on before it is written: R49 also forbids gating on a `skipif`, so the env-var gate cannot be a skip, and a marked test that *runs* in a developer's environment without a key would fail rather than deselect. | State in R49 that the `live_narrator` test is **collected always, deselected by marker in CI, and asserts nothing when `SWARM_OBSERVER_LIVE_NARRATOR` is unset** — i.e. the env gate is a branch inside the test, not a skip and not a marker. Then the non-zero deselection count is satisfiable, the collection floor sees the test, and no skip reason is needed. |
| **S36** | R43 / R36 | R43's byte-identity clause is "a test strips `<section id="narrative">…</section>` from an `--explain` render and asserts the remaining bytes are identical to the no-`--explain` render". That is satisfiable only if nothing outside the section changes — which forbids a nav entry for it, forbids recording the flag in the header alongside the other three `RenderOptions`, and (in spirit) discourages a stylesheet rule for its classes. All three are reasonable; none is stated. As written, a reasonable implementer adds a nav link and discovers the criterion is unsatisfiable. | One clause on R43 naming the consequence: the narrative section is anchored but **not linked**, and `--explain` is not recorded in the report header. Or, if the nav entry is wanted, widen the byte-identity clause to "the section and its nav entry". Either way the requirement should say which, because the two are not distinguishable from R43's current text and only one of them can be built. |

**On S27, which the review made a condition.** The README now carries it as a
blockquote in the `--no-previews` section, not a footnote: the flag does not
produce the same report with the text removed, it can drop a `critical`
finding, the flag's findings are a subset and never a superset, and the user
most likely to reach for it is the one least able to notice. The fix the review
ruled for — computing R22's `unknown_tool` decision at ingest, before A10
blanks — is a mapper change in increment 1's code and is **not** in this branch.

## 8. What I changed in the suite, and why

I wrote no new test module. Three existing files changed, each because this
branch or the spec made something in them false.

1. **`tests/test_cli_analyze.py`** — two tests replaced **in place**, the same
   substitution increment 4 made for `--out`.
   `test_r39_a_deferred_flag_is_a_usage_error_naming_its_increment`'s
   parametrization emptied when `--explain` was built; its replacement,
   `test_r39_explain_is_accepted_and_cannot_change_the_exit_code`, asserts the
   flag runs offline and that the exit code and stderr match the
   no-`--explain` run byte for byte.
   `test_r39_two_deferred_flags_together_report_the_first_by_flag_name` lost
   its subject entirely when `_DEFERRED_FLAGS` and its loop were deleted; its
   replacement asserts the same seam (one run, two output paths) for the
   behaviour that replaced it. Deliberately narrow — R43's byte-identity and
   the fallback markers are the tester's, and are still ledgered.
   One more edit: a monkeypatched `run` stub gained a `narrator` parameter.

2. **`tests/test_offline_determinism_r46_r47.py`** —
   `test_r46_no_module_under_swarm_observer_imports_a_transport` forbade
   `anthropic` **everywhere** under the package. R41 and R44 both require that
   import to exist in exactly one module, so "nowhere" became false the moment
   the narrator landed, and the test's own docstring already said "R44 covers
   `anthropic`'s placement; this covers the rest". Narrowed to a single
   `(module, name)` exemption rather than by dropping `anthropic` from the
   forbidden set — so an import of it in any *other* module is still an
   offender here as well as in R44's test — plus an arm asserting the exemption
   matched something, so it cannot become dead width after a deletion.

3. **`tests/test_json_report.py`** — five call sites of the deleted `free_text`
   became `text(..., kind="free", ...)`, and one local variable named `text`
   was renamed to avoid shadowing the import. No assertion changed.

4. **`tests/pipeline.py`** (a support module, not collected) — `analyze_trace`
   and `analyze_paths` gained an optional `narrative=` parameter and `Analysis`
   a `narrative` field, so a probe can hold the inputs and the `--explain`
   bytes at once, and `Analysis.allowlist()` passes the narrative through.
   Additive; every existing caller is unchanged.

5. **`tests/mutations.json`** — `W-M08` retired, with the reason recorded
   beside it: it anchored on `sorted(_DEFERRED_FLAGS.items())`, that dict and
   its loop are deleted, and the anchor no longer exists. `cli/main.py` keeps
   52 live mutants, comfortably above the ledger's floor of 8.

6. **Two stray files**, `k.json` and `r.html`, were written into the repository
   root by the old deferred-flag test during the single suite run between
   `--explain` being wired and that test being replaced, and were committed by
   accident. They are removed in `b6ce19b`; a full suite run from a clean tree
   now leaves `git status --porcelain` empty.

**Nothing else.** `tests/allowed_skips.txt` is still empty.
`tests/collection_floor.json` is unchanged — no module lost tests.
`CURRENT_INCREMENT` is deliberately still **4**: bumping it is the tester's, in
the same commit as any canary it demands, the way `redaction_pattern_removed`
moved in increment 3 and the four increment-4 canaries moved in increment 4.
Note that `test_r50_a_later_increments_debt_would_still_be_ledgered` probes the
ledger with a hypothetical `narrator_fallback_dropped: 5`, so if you add a
canary by that name you must adjust that probe in the same commit.

## 9. Tester surface

### What to drive

```python
from swarm_observer.report.sanitize import (
    KIND_AUTHORED, KIND_FREE, KIND_IDENTIFIER, KIND_NARRATOR, TEXT_KINDS,
    RenderOptions, TextKind, kind_for_metric, metric_value, optional_text, text,
)
from swarm_observer.report.narrative import (
    FALLBACK_CLASS, FALLBACK_PREFIX, NARRATIVE_CAVEAT, NARRATIVE_CLASS,
    NARRATIVE_SECTION_ID, NARRATIVE_SECTION_TITLE, Narrative, NarrativeParagraph,
)
from swarm_observer.narrate.client import (
    MAX_FINDINGS_PER_GROUP, MAX_PARAGRAPH_CHARS, MAX_TOTAL_ROWS, METRIC_KEY_PATTERN,
    MODEL_KEY_PATTERN, MONEY_PATTERN, NARRATION_GROUPS, NARRATION_REQUEST_VERSION,
    NARRATION_TITLES, NARRATOR_ERROR_CODES, OVERALL_GROUP, OVERALL_TITLE, SEVERITY_KEYS,
    AgentTotals, FindingSummary, GroupSummary, MetricEntry, ModelTotals,
    NarrationRequest, NarrationResponse, NarratorAuthError, NarratorClient,
    NarratorError, NarratorResponseError, NarratorTransportError, TraceTotals,
)
from swarm_observer.narrate.summary import (
    build_requests, build_totals, group_summary, narration_groups,
    serialize_request, severity_counts,
)
from swarm_observer.narrate.narrator import (
    FATAL_CODES, FOLDED_WHITESPACE, GroupNarration, Narration,
    deterministic_paragraph, has_control_characters, narrate,
    normalize_paragraph, validate_paragraph,
)
from swarm_observer.narrate.fixture import FixtureNarratorClient, ScriptEntry, paragraph
from swarm_observer.narrate.adapters.anthropic import (
    CREDENTIAL_ENV_VARS, DEFAULT_MAX_TOKENS, DEFAULT_NARRATOR_MODEL,
    DEFAULT_TIMEOUT_SECONDS, AnthropicNarratorClient, extract_paragraph,
    system_prompt, user_prompt,
)
from swarm_observer.replay.target import (
    NOTE_SLUG_PATTERN, ReplayResult, ReplaySelection, ReplayTarget,
)
from swarm_observer.cli.main import build_narrative, main, run
from swarm_observer.detect.base import AUTHORED_METRIC_VALUE_PATTERN
```

### Where the seams are

- **`main(argv, stdout=…, stderr=…, narrator=…)`**, and the same keyword on
  `run` and `analyze`. This is how a `FixtureNarratorClient` reaches the real
  `--explain` path. Note the difference: `main` has R11's catch-all, so a
  fixture's `AssertionError` becomes exit 2 with
  `unexpected_error: AssertionError`; `run` lets it propagate. Use `run` when
  you want the message and `main` when you are asserting an exit code.
- **`narrate(client=…, findings=…, totals=…, rate_snapshot_version=…,
  unavailable=…)`** is the whole of R43 without a filesystem. `client=None`
  plus `unavailable="…"` is the "no narrator could be built" case.
- **`build_requests(findings=…, totals=…, rate_snapshot_version=…)` →
  `dict[group, NarrationRequest]`**, and **`serialize_request`** is the exact
  string an adapter sends. AC12's sentinel clause is a property of that string.
  **The arm that matters**: assert the sentinels are present in the `Finding`
  objects the payload was built from, not only absent from the payload — an
  absence over an empty subject is instance seven.
- **`render_html(..., narrative=…)` / `render_json(..., narrative=…)`** take a
  `report.narrative.Narrative` directly, so the section can be rendered from a
  hand-built object with no narrator, no CLI and no filesystem. The
  `NarrativeParagraph` model is where to put a hostile paragraph.
- **`attribute_allowlist(trace=…, findings=…, timeline=…, narrative=…)`** —
  note the new keyword. With `narrative=None` it is byte-for-byte increment
  4's, which is why the existing equality test still passes; with a narrative
  it gains exactly one `id` value. The increment-4 review's `W5-A08` (a
  widened allowlist nothing notices) applies here: assert the narrative id is
  **absent** from the allowlist of a no-`--explain` render.
- **`validate_paragraph` / `normalize_paragraph` / `has_control_characters`**
  are R43's three checks as separate functions, drivable without a client.
- **`deterministic_paragraph(request, group_summary)`** is the fallback text,
  a pure function of the payload — so it is provably free of trace text for
  the same reason the payload is, and that is assertable.
- **`tests/pipeline.analyze_trace(..., narrative=…)`** holds inputs and both
  documents together, with the narrative in the `Analysis`.

### What I think is risky — ranked, most to least

1. **The `--explain` path's only offline oracle is a client I also wrote.**
   `FixtureNarratorClient` is mine, the payload models are mine, and
   `narrate` is mine; a test that scripts my client against my narrator is
   three of my own artefacts agreeing. The independent things to drive are
   (a) the **serialized** payload as a string, against sentinels you choose,
   not against `FIELD_MARKERS` as I used them; (b) the **rendered** documents,
   parsed, rather than `Narration` objects; (c) a client that is *not* my
   fixture — a three-line stub — so script-exhaustion semantics are not load
   bearing in your assertions.
2. **AC12's byte-identity, and the four things it depends on.** §4 lists them.
   Each is one edit away from being false and none of them is obviously
   load-bearing to a future editor: putting `"narrative"` in `SECTION_IDS`,
   adding an `explain` field to `RenderOptions`, adding a CSS rule, or adding
   a nav link. A test that only checks "the section can be stripped" will not
   say *which* of those broke. Consider four narrow tests plus the strip.
3. **The closed vocabularies in `narrate/client.py` are a second oracle I
   wrote.** `NARRATION_TITLES` is built from the registry, so it cannot drift
   from it — but `METRIC_KEY_PATTERN`, `MODEL_KEY_PATTERN`,
   `SNAPSHOT_VERSION_PATTERN` and `MONEY_PATTERN` are patterns I chose. The
   check worth having is the one that does not use them: enumerate every
   string **value** in a serialized payload over the hostile corpus and assert
   each one is a member of a set you computed independently from the registry,
   the snapshot file and the severity enum. §3 has the list I got; a table you
   derive yourself is the second oracle.
4. **The fatal-versus-per-call split (A-e6) is my reading of one sentence.**
   Drive all ten codes in `NARRATOR_ERROR_CODES`: three must stop the run's
   narration (one client call, every group fallen back), the rest must fall
   back once and continue. Assert the **call count**, not just the paragraph
   count — "every group fell back" is true in both cases and cannot tell them
   apart.
5. **`--explain` with `--no-previews`.** The narrator's paragraphs are *not*
   blanked (A-e9). If you think that is wrong, it is a ruling to ask for, not a
   bug to file — but do assert it, because it is the one place where a privacy
   flag and an LLM feature meet and nobody would notice a change.
6. **The C1 boundary itself.** `TEXT_KINDS` is exported so you can enumerate
   it. Two properties worth pinning that no current test covers: `text` with
   each kind produces exactly what `sanitize.py`'s docstring says, and *no
   module under `swarm_observer/report/` calls `redact` directly* — an AST scan
   in the shape of the `escape_html` one, which would catch a fifth boundary
   being added beside the fourth.
7. **`extract_paragraph`** is the one piece of the anthropic adapter drivable
   with a stub object. Three arms: a list of text blocks, a non-list `content`,
   and a list with no text block.
8. **The replay seam.** Nothing imports it, which means nothing exercises its
   validators either — and the first draft of that module declared
   `NOTE_SLUG_PATTERN` and never **applied** it, so its docstring's "notes are
   enumerated slugs, never free text" was a sentence nothing enforced. I caught
   that by driving the validators by hand before writing this document, which
   is the whole argument for doing so. Drive them:
   `ReplaySelection(timeout_seconds=0)` raises,
   `ReplayResult(source_trace_id="nothex")` raises, and
   `ReplayResult(notes=("provider said: boom",))` raises while
   `notes=("ok",)` does not. A seam whose validators nobody has run is a seam
   that might not work in v2.

### What the harness expects of you

1. `tests/collection_floor.json` needs an entry for every new module you add.
   No existing floor moved.
2. `tests/traceability_pending.txt` still lists **R41** and **R42**. Both are
   now implemented; delete each line as its requirement gains a test. I was
   careful not to write `R41` or `R42` into any test docstring, so the ledger
   does not shrink on a citation that is not coverage (A-c13) — I hit exactly
   that once while narrowing the transport scan and reworded it.
3. `CURRENT_INCREMENT` is still 4; see §8.
4. `tests/allowed_skips.txt` stays empty.
5. **`tests/mutations.json` has no entries for this increment's six new
   modules.** `test_r49_the_ledger_covers_every_module_the_increment_touched`
   lists increments 3 and 4's; `narrate/client.py`, `narrate/summary.py`,
   `narrate/narrator.py`, `narrate/fixture.py`, `report/narrative.py` and
   `replay/target.py` are not in it, and neither is the rewritten
   `report/sanitize.py` boundary. Adding them is a decision about what a sweep
   must cover, which the increment-4 tester took for the four modules that
   increment created; the same call is available to you. `narrate/narrator.py`
   is where I would put the most mutants: the fatal-code set, the
   `stopped is not None` short-circuit, the validation order and the
   `except AssertionError: raise` clause are all one character from a wrong
   answer that the current suite would not see.
6. **S35 is the one to read before writing anything.** No test carries the
   `live_narrator` marker, so R49's "the marker deselects a non-zero count"
   clause has nothing to verify. T18 names the missing test and it is yours;
   the shape question — whether the env gate may be a `skipif`, which R49
   forbids elsewhere — is the PM's and is filed.

### What I deliberately did not verify

- **Any per-requirement test.** By the brief.
- **A mutation sweep, and any mutation score.** Same ruling as increments 3 and
  4, upheld both times. What I have done instead is name, in §9's risk list,
  the eight places where a one-character change would produce a plausible wrong
  answer, and to say in item 5 above which module I would sweep first.
- **A golden file for the `--explain` render.** Same reason increment 4's coder
  gave: a golden generated from the renderer by the renderer's author, in the
  same commit, is the purest form of a check that cannot fail. The render is
  deterministic across three runs (§4), which is the precondition a golden
  needs; building it is yours. If you do, pair it with the four structural
  assertions from risk item 2, because "any change" is the weakest possible
  answer to "what would make this red".
- **Anything in a browser.** §5.
- **The live narrator path.** §5, in full.

## 10. Smoke tests — lint, types, suite

```
$ python3 -c "import swarm_observer; print(swarm_observer.__file__)"
/home/claude/so-i5/swarm_observer/__init__.py
$ /tmp/v312/bin/python -c "import swarm_observer; print(swarm_observer.__file__)"
/home/claude/so-i5/swarm_observer/__init__.py
```

Asserted before every measurement, with `PYTHONPATH` pinned and
`PYTHONDONTWRITEBYTECODE=1`; without it, `python3` outside the worktree imports
`/home/claude/swarm-observer`. `__pycache__` was purged before the final run —
the increment-4 tester's stale-bytecode hazard, which makes a SHA-256 pin look
broken for ten minutes, is live in this container.

```
$ python3 -m ruff check .                 ->  All checks passed!
$ python3 -m ruff format --check .        ->  95 files already formatted
$ python3 -m mypy                         ->  Success: no issues found in 44 source files   (3.11)
$ /tmp/v312/bin/python -m mypy            ->  Success: no issues found in 44 source files   (3.12)
$ python3 -m pytest                       ->  2565 passed, 1 xfailed in 52.29s              (3.11)
$ /tmp/v312/bin/python -m pytest          ->  2565 passed, 1 xfailed in 49.19s              (3.12)

$ env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
      -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
      -u AWS_PROFILE -u OPENAI_API_KEY -u GH_TOKEN \
      python3 -m pytest -m "not live_narrator"
                                          ->  2565 passed, 1 xfailed in 48.43s
```

2555 → 2565 is **+10, all of them parametrized arms of
`test_r44_subpackage_import_rules`**, one per new module:
`narrate/{__init__,client,summary,fixture,narrator}.py`,
`narrate/adapters/{__init__,anthropic}.py`, `replay/{__init__,target}.py` and
`report/narrative.py`. Two tests were replaced one-for-one, so no module's
collected count fell.

```
$ swarm-observer analyze --help
usage: swarm-observer analyze [-h] [--out <report.html>]
                              [--json <report.json>]
                              [--adapter {claude_code_jsonl}] [--explain]
                              [--no-previews] [--detector <slug>]
                              [--blocked-gap-seconds N]
                              [--fail-on {none,warning,critical}]
                              [--max-file-bytes N] [--max-line-bytes N]
                              [--max-records N]
                              <path> [<path> ...]
```

R38's surface is unchanged; `--explain` is the last flag to stop being refused.

## 11. What is left, and for whom

- **The PM**: S24–S32 remain queued from increment 4, in the review's priority
  order. This branch adds **S33**, **S34**, **S35** and **S36**. S35 is the one
  that blocks a tester rather than a reader.
- **The tester**: R41–R43 and AC12 in the suite; the `live_narrator` test (once
  S35 is ruled on); mutation coverage for six new modules and one rewritten
  one; the four narrow byte-identity arms.
- **A human**: open `report.html --explain` in a browser, and check the two
  rate cells the increment-3 review named. Both are still owed and neither can
  be discharged by an agent.
