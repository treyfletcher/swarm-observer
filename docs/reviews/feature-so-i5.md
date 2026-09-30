# Review: swarm-observer increment 5 — `feature/so-i5`

**Verdict: merge.** Six `review:` commits, each with its pinning test. This is
the last increment of v1, so the merge verdict is also a release verdict, and
that one is in §11: **tag it, with four things written down.**

Scope: `git diff feature/so-i4...feature/so-i5`, plus my own commits on top.
Binding on me: `docs/reviews/feature-so-i4.md` (C1–C7, S24–S32) and the
increment-3 review through it.

Final status, both interpreters, after my commits:

| | CPython 3.11.15 | CPython 3.12.3 |
| --- | --- | --- |
| full suite | 2907 passed, 1 xfailed | 2907 passed, 1 xfailed |
| R45 scrubbed, `anthropic` absent, `-m "not live_narrator"` | 2906 passed, 1 deselected, 1 xfailed | 2906 passed, 1 deselected, 1 xfailed |
| `ruff check` / `ruff format --check` | pass / 104 formatted | pass / 104 formatted |
| `mypy --strict` | 44 files, clean | 44 files, clean |

The xfail count went from 2 to 1: BUG-16 is fixed, so its `xfail(strict=True)`
became a passing test. The one remaining is S16's
identifier-under-`--no-previews` gap, which is the PM's and is deliberate.

---

## 1. What I did, in one paragraph each

**Three blockers fixed**, in the order they matter: `MetricEntry` now refuses a
trace-derived metric key at the model (BUG-14), so R42's no-trace-content
property stops resting on a single `if` in the module whose thesis is that it
has none; the narrative title is classified by its type rather than by its
caller (BUG-16), closing the fifth occurrence of a family that has produced one
occurrence per increment for five increments; and R43's *visible* fallback
marker became a token a narrator may not write (BUG-15), because a trust marker
an attacker-influenced source can author does not mark anything.

**Three structural commits**, each paid for by something the sweep or the
coverage run showed: ten wave-7 mutation gaps closed with tests; the untrusted
field sweep extended to the models increment 5 made renderable, because its
fields are derived and its *models* are a hand-written list and that is exactly
where occurrence five landed; and AC14's two missing hook canaries written,
because "a test skipped for an unlisted reason fails the session" is the
project's answer to its own worst inherited failure and nothing had ever
watched it fail.

---

## 2. Blockers found and fixed

### 2.1 BUG-14 — `MetricEntry`'s documented second refusal did not exist

`82e4702`. The tester's find, and I am landing the fix **now** rather than
routing it to the PM as S39.

The reasoning is narrow and I want it on the record, because "the spec is
ambiguous, therefore wait" was an available answer. `MetricEntry`'s **own
docstring** said the validator "refuses it a second time so a future caller
cannot add it back by hand". It did not. Making code match a claim its own
docstring already makes is not a spec change and needs no ruling. S39 — putting
that requirement into R42's text — is still owed and is queued below.

What makes it a blocker rather than a tidy-up is the counter-example.
`AUTHORED_METRIC_VALUE_PATTERN` is `[a-z][a-z0-9_]{0,63}`, and `ghp_` followed
by twenty-four lowercase letters is inside it exactly. So "an authored slug" and
"a trace-derived slug" are the **same alphabet**, and no value-shape check can
ever separate them. Only the key can — which is why R16 enumerates the
trace-derived keys at all. Before the fix, the single thing keeping a credential
out of a real `--explain` payload was `summary._metrics`'s
`if key not in TRACE_DERIVED_METRIC_KEYS`.

Pinned by `test_r42_metric_entry_refuses_every_trace_derived_key`, parametrized
over the frozenset rather than over the literal key, with a control arm showing
`ghp_aaa…` still constructs under `occurrences` — so the refusal is
demonstrably about the key and not about the needle. If that arm ever goes red,
the fix has grown into a value-shape check, which cannot work.

### 2.2 BUG-16 — the narrative title, and why I would not ship v1 with it open

`4d23ea9`. **The brief asks directly: is shipping v1 with the fifth occurrence
open acceptable? No.**

The family, in order: a trace-derived field bypassing redaction (i1);
`metrics.tool_name` under R16's shape check (S13, i2); `agent_id`,
`parent_agent_id`, `ParseWarning.detail` (BUG-2, i3); `SpanError.code` (i4
review §1.1); `NarrativeParagraph.title` (this). Five increments, five
occurrences, and every one of the four previous fixes was a correct fix to *the
field that was reported*. The argument for carrying it — the product path only
ever copies a registry title — is the same argument that was available for
`SpanError.code`, which was correct for four increments and then was not.

The tester proposed "one `pattern=` on the field, or classify it as an
identifier". **The pattern does not work**, and this is worth stating because it
is the obvious fix: `AKIAIOSFODNN7EXAMPLE` is letters and digits, so any
alphabet permissive enough for `Repeated identical tool call` admits it. A
pattern would have closed the reported example and not the class — the family's
signature move. So the title now takes the `narrator` kind in both renderers:
redacted in both modes, never blanked, the same class as the paragraph under it.
The narrative section has one text class instead of a per-field judgment, which
is C1's own shape.

`report/` genuinely cannot do better than this. R44 puts `narrate` and `report`
on separate branches, so `narrate.client.NARRATION_TITLES` — the closed
vocabulary the *payload* validates a title against — is not in scope in
`report/narrative.py`. The only honest claim `report/` can make about a title is
the one its type supports, and the fix is to stop making a stronger one.

**Zero rendered bytes move** for any report the product produces: `redact` is
the identity on a registry title, and both goldens are byte-unchanged.

Pinned over the **whole** R51 credential corpus rather than the one key the
finding named (the PEM member exceeds the field's length bound and is refused by
the type, so the arm records which of two mechanisms stops each member), with a
control arm asserting a real title is unchanged — without which "the credential
is absent" would also be satisfied by blanking every title.

### 2.3 BUG-15 — the forgeable visible marker: **bug, not note**

`7aa29fb`. The brief asks for a ruling. Three reasons it is a bug.

The DOM class is invisible to the person reading the page. R43 calls the prefix
the **visible** marker; being visible is its entire job. "The marker is present
on every fallback" is true and is not the property anyone relies on — the
property is *this sentence was written by the tool*, and a reader checks that by
reading.

The spec's own Security considerations say narrator output is "just another
attacker-influenced string source". A marker such a source can author is the
S32/BUG-12 family one boundary over, where `secret_assignment` re-matched a
`[redacted:…]` marker an earlier pattern had written. That was treated as a bug.

And the fix's failure mode is the *correct* outcome rather than merely a safe
one: the group falls back, so the reader sees a deterministic summary that
genuinely is one.

Two layers, because one would be a property of the path. `validate_paragraph`
gains a fourth check, **after** R43's own three and in their original order so
the "first failure wins" pin still holds; case-insensitive containment of the
full literal including its colon; run after `normalize_paragraph`, so a marker
folded across a newline is caught. And `NarrativeParagraph` refuses the
combination outright — unreachable from the product, since the two predicates
are the same test over the same normalized string, and present anyway because
*no renderer may assume its caller sanitized* is the sentence under which
`SpanError.code` and this model's own `title` were both found.

R44 forbids `narrate` from importing `report`, so the literal is duplicated as
`client.RESERVED_PARAGRAPH_PREFIX` and bound by an equality assertion — the
discipline `tests/pipeline.py` already applies to `selected_slugs` and
`sentinel_trace.py` to the CLI's cost extraction. I considered and rejected
moving the literal to `detect/` to avoid the copy: that would put a rendering
concern in the detector contract to save an assertion.

Pinned by a five-arm battery (prefix, embedded, lowercase, uppercase, folded
across a newline) — a single arm would pass a fix that checked `startswith`, or
one that was case-sensitive, or one that ran before normalization — plus an
end-to-end run over the real CLI with a narrator that forges on every group,
asserting the marker's count equals the class's count and that the exit code
equals the no-`--explain` run's. A fourth way to refuse a paragraph must not
change what the run returns.

---

## 3. Instance twelve

**`cli/main.py::build_narrative` can send the narrator anything it likes in the
payload's one free-provenance field, and no check in the repository can
observe it.**

Wave-7 `V43` replaced `rate_snapshot_version=cost.meta.version` with the literal
`"not_the_snapshot_version"`. That string reached the narrator's payload **and
the rendered deterministic paragraph** — "…at rate snapshot
not_the_snapshot_version" — in a document whose own header still printed the
real version. The suite stayed green at 2875 tests.

Two independent reasons, and both are the signature defect:

1. **AC12's sentinel sweep and the independent-vocabulary arm run over a
   payload the test builds.** `sentinel_payload` calls
   `sentinel_trace.totals_for`, a deliberate duplicate of the CLI's extraction.
   The duplicate is honest — `test_r42_the_cli_hands_the_narrator_the_figures_it
   _claims_to` asserts the equality — but that assertion compares
   `NarrationRequest.totals` **only**, and `rate_snapshot_version` is a sibling
   of `totals`, not a member of it. The two checks whose declared job is "no
   string the payload carries is unaccounted for" are structurally unable to see
   the code that chooses one of the strings.
2. **Even over the CLI's payload, the vocabulary arm would have allowed it.**
   `not_the_snapshot_version` matches `^[a-z][a-z0-9_]{0,63}$`, and the arm
   permits any slug. That is the `SHAPE_LEAVES` hole the tester documented,
   demonstrated rather than described.

This is the tester's own §6 taken one step further. The tester wrote that
`rate_snapshot_version` and `model_key` "are clean because `cli/main.py` passes
`SnapshotMeta.version` and `CostReport.by_model[].model_key` … That is a
call-site discipline." Correct — and what nobody checked is whether any test
*pins* the discipline. None did.

Closed by `test_r42_the_cli_sends_the_snapshots_own_version`, which compares
against a **separately loaded** `SnapshotRateSource` and then — this is the arm
that makes the first one mean anything — re-runs the extraction over a
`CostReport` whose meta has a version that differs from its date. In the bundled
snapshot `version` and `snapshot_date` are the **same string** (`2026-09-10`),
so an equality against the real data cannot distinguish "sends the version" from
"sends the date"; wave-7 `V05` swapped them and survived on that coincidence
alone until the synthetic-meta arm existed.

**S38 is the spec-level fix and the PM should take it.** Validating the snapshot
version and the model key against the keys *present in the loaded snapshot*
turns two of the four shape-checked leaves into closed vocabularies and moves
AC12's structural claim from the caller to the type.

---

## 4. Verdict on "structural, not filtered"

**The tester's split verdict is right, and after `82e4702` half of its
criticism no longer applies.**

* **Layer 2 — "the builder has almost nothing to leak" — holds completely** and
  is the best thing in the design. R44 keeps `Trace`, `Span`, `SourceFile` and
  `CostReport` out of `narrate/`'s scope, so the residual surface is one object
  (`Finding`) rather than five. A-e4's refusal to widen R44 to save an import is
  what buys that, and it was the right call.
* **Layer 1 — "the payload's type has no field that can hold free text" —
  holds for free text** (spaces, markup, length, case) at all fourteen leaves,
  and did **not** hold for a slug-shaped trace-derived string at four of them.
  Of those four:
  * `metrics[].key` and `metrics[].value` are now refused **by the type**, which
    is what the docstring always claimed. The tester's "the one load-bearing
    filter" is no longer load-bearing and no longer alone.
  * `rate_snapshot_version` and `by_model[].model_key` remain **call-site
    discipline**. That discipline is now pinned by a test (§3) where before it
    was pinned by nothing, but a test is not a type. S38 is the fix.

So the honest one-line version, updated: *the payload's type makes a leak of
trace prose impossible, a leak of a trace-derived metric slug impossible, and a
leak of a snapshot-shaped string dependent on one function in `cli/main.py`
that now has a test.*

One correction to the tester's evidence, in the tester's favour. Its table says
the sentinel corpus shows "present in the inputs: 14 of 14, present in the
payloads: 0 of 14", which is true — but for `by_model[].model_key` it is true
*vacuously*: the sentinel trace's model never resolves, so it has **zero priced
spans and an empty `by_model`**. The sweep of that leaf was a sweep of an empty
tuple, which is instance nine's shape inside the corpus built to avoid instance
nine. Closed by `test_r42_the_payload_carries_a_real_snapshot_model_key`, which
drives a fixture that prices seven spans and asserts the key is one the bundled
snapshot defines while the recorded `Span.model` — a different string — appears
nowhere.

---

## 5. Was C1 discharged?

**Yes, and better than I asked for — with one residual I have now closed
myself.**

Verified independently, not taken from the PR:

| C1's claim | How I checked | Result |
| --- | --- | --- |
| `free_text` / `identifier` are gone | grep for the definitions and the old `_free` / `_ident` call sites | none remain |
| omitting `kind` is a type error | deleted `kind=` from one real call site and ran `mypy --strict` | `error: Missing named argument "kind" for "__call__" of "_Writer"` |
| a new member cannot be added without classifying it | added `"reviewprobe"` to `TextKind` and ran `mypy --strict` | `error: Argument 1 to "assert_never" has incompatible type "Literal['reviewprobe']"` |
| redaction has exactly one call site | the tester's new `test_r44_only_the_text_boundary_calls_redact` — an AST scan, the shape of `test_r44_one_definition_of_escape_html` | holds; `report/sanitize.py` and nothing else |
| **zero rendered bytes changed** | `git diff feature/so-i4...feature/so-i5 -- tests/golden` | **empty.** Both checked-in goldens are byte-identical across the C1 refactor, and remain so after my six commits |

That last row is the one that matters and it is as strong as this project can
make it: a refactor that touched every write in two renderers moved not one byte
of either golden.

**The residual, and it is the reason occurrence five happened.** C1 made the
*classification* exhaustive over kinds. The exhaustiveness check over **fields**
lives in `tests/test_untrusted_field_sweep.py`, whose whole design argument is
that "the field list here is **not written down**" — fields come from
`model_fields`. One level up, it *is* written down: `SWEPT_MODELS` is a
hand-written tuple of the five `model/trace.py` classes. Increment 5 added a
second family of renderable models in `report/narrative.py`, nobody added it to
that tuple, and BUG-16 landed in exactly the model the list did not name. A
derived check with a hand-written scope is a derived check over whatever
somebody remembered.

Closed in `88254ae`: `TestTheNarrativeModelIsSweptToo` applies the same
partition to `Narrative` and `NarrativeParagraph` — fields derived from
`model_fields`, the authored class **checked** rather than asserted (`group` and
`reason` are driven with every R51 credential and must be refused by their
pattern), and each untrusted field driven with a credential through both
renderers in both modes with a read-back arm and a "redacted, not blanked" arm.
Verified to fail: reverting the `title` classification in either renderer turns
four of its arms red.

---

## 6. What has never executed — verified independently

I ran `coverage` over the full suite rather than reading §5.

**§5 was wrong when written, and the tester was right (BUG-17).** The PR listed
`system_prompt()`, `user_prompt()` and `_credential_present()` under "executed,
offline". At the coder's HEAD they were **missed** — every test that touches them
arrived in the tester's commit. I confirmed this without re-running the old tree:
every module referencing those symbols (`test_live_narrator.py`,
`test_guard_verification.py`, `test_boundaries_and_posture.py`,
`test_narrate_fallback_r43.py`) is new or modified in `00d73fb`.

**The accurate inventory, as of this branch's head:**

```
swarm_observer/narrate/adapters/anthropic.py   55 stmts   16 missed   71%
  line 136        `return anthropic`            (the extra is not installed)
  lines 159-178   sdk.Anthropic(...), client.messages.create(...),
                  and all six `except sdk.<Error>` clauses
swarm_observer/narrate/client.py              125 stmts    5 missed   96%
swarm_observer/replay/target.py                23 stmts    0 missed  100% (definitions only)
everything else under narrate/ and report/narrative.py            100%
```

So the honest v1 statement is: **one outbound request and six exception-mapping
clauses have never run, anywhere.** Everything else the increment created has.
That is a considerably smaller unexecuted surface than §5 claims, in the
direction that flatters the branch, and §5 should be corrected rather than
deleted — it is the document a future reader will trust.

**`replay/` is genuinely inert**, confirmed: nothing outside `replay/` imports
it, asserted by a test, and the 100% figure is class-body execution at import.

**Comment C-4 (below)** covers the five never-executed validator branches in
`narrate/client.py` that the coverage run surfaced.

**Signature-defect exposure of the unexecuted path.** Instance 3 was "a
live-eval replay path that could never have passed", so the question for
`AnthropicNarratorClient.complete` is not "is it tested" — it cannot be — but
"can it be wrong in a way that reports green". It cannot report green: nothing
in CI touches it, no test asserts anything about it, and the marked live test
raises `AssertionError` rather than skipping if `SWARM_OBSERVER_LIVE_NARRATOR=1`
is set without a credential. The failure mode if the SDK renames an error class
is `AttributeError` at except-evaluation time → the catch-all → a
`provider_error` fallback → a deterministic paragraph. Degraded, deterministic,
visible in `report.json`'s `reason`. That is the right shape for code nobody can
run, and I accept it.

---

## 7. My mutation wave — wave 7

Recorded in `tests/mutations.json` as wave 7 (the tester's is wave 6; I did not
renumber theirs).

**Design.** Anchors that **neither** wave 6 nor waves 1–5 touched:

| Anchor | Why it was untouched |
| --- | --- |
| `cli/main.py::build_narrative` | wave 6 did not enter `cli/main.py` at all; waves 1–5 predate the function |
| the narrative branches of `report/html.py` and `report/json_out.py` | new in this increment; waves 4–5 swept the surrounding code |
| `report/sanitize.py::text` post-C1 | waves 4–5 swept `_t`/`_free`/`_ident`, which no longer exist |
| `detect/base.py`'s newly exported `AUTHORED_METRIC_VALUE_PATTERN` | the export is new |
| the **offline-executed** surface of `narrate/adapters/anthropic.py` | wave 6 excluded the module wholesale |

That last one was a deliberate disagreement with the tester, and the sweep
settled it. Wave 6's reasoning — "most of it cannot execute in CI, so every
mutant would survive and the survivor list would say something about CI rather
than about the tests" — is right about `complete`'s body and **too wide** for the
module. `_credential_present`, `extract_paragraph`, `system_prompt`,
`user_prompt` and the `ImportError` branch all execute offline. Seven of the
module's nine mutants were killed by the inherited suite, and one (`V32`) was a
real gap. The correct rule is per-*function*, not per-module, and a coverage run
tells you which — see the harness amendment in §9.

**Operator set**, drawn from the ledger's declared list as the code allowed:
relational flip, integer constant ±1, boolean-connective swap, guard-clause
drop, negation drop/insert, membership flip, slice-bound off-by-one,
normalization drop, call-argument swap, literal substitution, control-flow swap,
container-default swap, set-operand-drop, and `control-no-op` for the control
arms. By operator: literal 11, call-argument 8, negation 5, guard-drop 5,
control-no-op 2, membership 2, int+1 2, normalization-drop 2, relational 1,
control-flow 1, slice-bound 1, container-default 1, and/or 1, set-operand-drop 1.

**Two declared control arms in two different modules** — `V41-CONTROL` (`[]` →
`list()` in `report/json_out.py`) and `V42-CONTROL` (`[…]` → `list([…])` in
`report/html.py`) — so the warrant for the kills is not a property of one file.
Both survive on both interpreters.

**Results.**

```
wave 7, first run, CPython 3.11.15, serial
TOTAL 45 killed 32 SURVIVED 13 not-applied 0
  -- ['V03','V05','V08','V10','V12','V13','V18','V30','V32','V43','V45',
      'V41-CONTROL','V42-CONTROL']

after closing ten real gaps, re-run of all thirteen
TOTAL 13 killed 10 SURVIVED 3 not-applied 0
  -- ['V30','V41-CONTROL','V42-CONTROL']

wave 7, final: 45 mutants, 42 killed, 3 survived, 0 not-applied
```

### 7.1 The ten real gaps, in three clusters

**Cluster A — the CLI's extraction is outside AC12's reach.** §3.

| Mutant | What it did | Killed by |
| --- | --- | --- |
| `V43` | `rate_snapshot_version` becomes a literal | `test_r42_the_cli_sends_the_snapshots_own_version` |
| `V05` | `rate_snapshot_version` becomes the snapshot's **date** | the same test's synthetic-meta arm |
| `V03` | `model_calls` counts every span that is *not* a model call | `test_r42_the_payload_counts_the_traces_model_calls` |

`V03` deserves its own note: my **first** version of its killing test could not
fail. It ran over `clean_single_agent.jsonl`, which has five `model_call` spans
and five of everything else, so inverting the predicate is the identity on it —
and half the fixture corpus is like that. The arm now runs over a fixture with
7 and 4, and carries an explicit `complement` guard so a future edit cannot move
it back. I am reporting this because it is the same mistake as instance eleven,
made by the reviewer, caught by the sweep, in the same session.

**Cluster B — the corpus has one agent and prices nothing.** Every `--explain`
test in the branch ran over the sentinel trace: **one** agent, **zero** priced
spans. So `by_agent[:1]` is the identity, `agent_index` and `priced_spans` are
both `0` on its single row, and `by_model` is empty.

| Mutant | What it did | Killed by |
| --- | --- | --- |
| `V08` | per-agent cost rows truncated to one | `test_r42_every_agent_gets_a_row_indexed_by_its_own_index` |
| `V10` | an agent row keyed by its priced-span count instead of `agent_index` | the same |

Closed with `gaps_explained.jsonl` — four agents, seven priced spans, a
resolving model — plus `test_r42_the_payload_carries_a_real_snapshot_model_key`
for the empty-`by_model` leaf. This is instance nine's shape one layer down, and
it is worth the PM's attention that it recurred **inside the corpus the tester
built specifically because the previous corpus left fields empty** (BUG-8). A
corpus is a set of *values*, and "every field is loaded" is not the same
property as "every field is loaded with something that distinguishes the
answers".

**Cluster C — the fallback branch of the paragraph loop was never driven.** The
loop has two branches; every test took the other one.

| Mutant | What it did | Killed by |
| --- | --- | --- |
| `V18` | the fallback branch writes its text as `authored` — no redaction | `test_r43_a_fallback_paragraph_is_redacted_and_never_blanked[True]` |
| `V45` | the fallback branch writes its text as `free` — **blanked** under `--no-previews`, against A-e9's explicit ruling | the same, `[False]` |
| `V12` | the section's note is emitted when there are *no* fallbacks and suppressed when there are | `test_r43_the_section_note_counts_the_fallbacks_and_names_the_reasons` |
| `V13` | the note's `or "no reason recorded"` default deleted | `test_r43_the_note_has_a_default_when_no_reason_was_recorded` |

`V12`/`V13` are about the sentence that tells a reader **how many** of these
paragraphs the tool wrote and why. It was unasserted in both directions.

**And one on its own:**

| Mutant | What it did | Killed by |
| --- | --- | --- |
| `V32` | `ANTHROPIC_AUTH_TOKEN` dropped from `CREDENTIAL_ENV_VARS` | `test_r45_either_credential_variable_on_its_own_is_a_credential` |

R45 scrubs both names; the adapter must read both. The one credential test set
element zero of the tuple, so dropping element one changed nothing any test
looked at. A developer whose credential lives in `ANTHROPIC_AUTH_TOKEN` would
have got `no_credentials` on a machine that has one.

### 7.2 The three survivors, by name

| Mutant | Operator | Classification |
| --- | --- | --- |
| `V41-CONTROL` | `control-no-op` (`[]` → `list()`, `report/json_out.py`) | **A declared control arm. It must survive**, and it does, on both interpreters. |
| `V42-CONTROL` | `control-no-op` (`[…]` → `list([…])`, `report/html.py`) | **The second control arm, in a different module.** A sweep reporting either killed is not reporting verdicts that come from the mutation. |
| `V30` | `call-argument` (`metric_value`'s `previews=True` → `False`) | **Equivalent, with evidence.** `kind_for_metric` returns only `identifier` or `authored`, and `sanitize.text` reads `previews` for neither — the `free` branch is the only one that does. The parameter is dead on this call and the mutation cannot change a byte. Keeping it is `text`'s own rule that every caller must name the mode it is writing in. |

Same three names and the same killing node ids on CPython 3.12.3. Waves 1–6 not
re-run: they cover code these commits did not change.

### 7.3 A harness failure of my own, recorded

I started a second sweep while the first was still running. The two raced on one
working tree, the digest check fired with one sweep's mutation on disk while the
other was mid-restore, and the run aborted with a mutant left applied. I killed
both, restored the tree, re-verified the unmutated suite green, and began again
serially.

The clause that says "run mutation sweeps **serially**" is in the brief and in
the ledger, and I broke it anyway. The **tree digest** — increment 4's
amendment — is what turned that into an abort rather than a wave of false kills:
without it the second sweep would have reported verdicts against a tree carrying
the first sweep's mutant. It is now recorded in `tests/mutations.json` as the
second time that clause has been paid for.

---

## 8. Judgment calls — comments, not commits

**C-1. `report.json`'s narrative is the only place a reader can tell a
deterministic paragraph from a model's, and the HTML deliberately hides it.**
`NARRATIVE_CAVEAT` says a paragraph carrying the prefix "was produced by
swarm-observer itself", which is now unforgeable — good. But `reason` is
JSON-only by A-e13, so a human reading `report.html` cannot tell "the narrator
was never asked" from "the narrator answered badly". The coder argues R34
forbids putting it in an attribute; true, and a text node is not an attribute.
Not a defect, and a v2 line of prose in the note would cost nothing.

**C-2. The `live_narrator` test never runs in CI, including its offline
branch.** The env gate is a branch, which is right (S35/S37), so with the
variable unset the test asserts the live path's *preconditions*. But CI runs
`-m "not live_narrator"`, which removes the test entirely — so those
preconditions are asserted only on a developer's machine. Wave-7 `V36`, `V37`
and `V38` were all killed by that test and **only** by it, which means three
adapter mutants are killed by nothing CI runs. Cheap fix for v1.1: move the
offline-preconditions half into an unmarked test and leave only the outbound
call behind the marker.

**C-3. The tester's control arm for the skip-AST scan re-implements the scan
rather than invoking it.** `test_r49_the_skip_scan_can_see_a_skip` copies the
predicate inline and runs it on a probe. It therefore proves the *copy* can see
a skip. Editing the real scan would not turn it red. The real scan can still
fail (it walks real files), so this is not a false green — but a control arm
that does not share code with its subject is a control arm for something else.

**C-4. Five validator branches in `narrate/client.py` have never executed.**
Coverage names them: `GroupSummary`'s "severity_counts keys must be sorted" and
"detail is capped at 20", `TraceTotals`'s "severity_counts keys must be sorted",
and `NarrationRequest`'s "the requested group is not among the described groups"
and "a group is described twice". These are guards with no proof they can fail,
in the module whose thesis is that the type refuses. The tester's leaf battery
covers the *string* refusals thoroughly; these are the structural ones. Five
small `pytest.raises` arms; R50's spirit if not its letter.

**C-5. On the thirteen own-goals, and whether pinning a paragraph
character-for-character is the right trade — yes.** The brief asks. Five of the
thirteen were one gap: nothing asserted what the deterministic paragraph *said*.
The alternative to a literal sentence is an expectation computed from the code
that produces it, which is `I-H02` — the defect that produced `NC01`–`NC03` in
the same sweep. `test_r43_the_overall_template_says_what_the_numbers_are` builds
a **hand-made payload** and compares against a literal, so it is brittle in
exactly the direction you want: it breaks when the prose changes, which is when
somebody should look. Upheld without reservation. The wider lesson — thirteen of
twenty first-run survivors were gaps in tests written an hour earlier — is not a
criticism of the tester; it is the strongest available argument that a sweep
designed *after* the tests is worth more than a higher self-reported score.

**C-6. On `tests/sentinel_trace.py` as a second hostile corpus — keep both.**
The tester's argument is correct and I can put it more sharply than "the
alphabet is the point": a single corpus cannot simultaneously satisfy "every
payload validator refuses this" and "every payload validator accepts this",
because those are complementary sets. Merging them, as the tester's alternative
proposes, would produce one corpus whose members are half refused and half
accepted, and every sweep over it would have to know which half it was asserting
about — which is how you get an assertion that passes for the wrong reason. Two
corpora with two stated purposes is the clearer artefact. **One condition**: §7.1
cluster B shows the new corpus has the *value* weakness (one agent, nothing
priced) that the old one had as a *field* weakness. A third corpus is not the
answer; the answer is that a corpus's README should state which distinctions its
values can support, and `sentinel_trace.py`'s docstring should say it prices
nothing.

**C-7. On S37 — a deselection does not deserve a data file yet.** My ruling. The
comment block is right, and the owner's "ledger entry recording why" is
satisfied, for one reason that is not convenience: `allowed_skips.txt`'s
emptiness **is itself an assertion** (`test_r49_the_suite_currently_allows_no_
skips`), and a `declared_deselections.txt` would be a second data file with a
second hook, which under R50 needs its own canary proving *it* can fail. That is
three artefacts for one entry. The comment block is already enforced — the test
asserts the record names the deselected test, so deleting one and leaving the
other is visible. **The threshold is the second marker**: when a second
deselection exists, prose stops being a ledger and a data file earns its hook.
Queued as S37's resolution below.

---

## 9. Rulings on the harness contract and on the A-e series

### 9.1 BUG-18 and the coverage clause — **yes, it belongs in the contract**

The brief asks whether "coverage-run every module an increment creates, before
designing the mutation sweep" belongs in the harness contract. Ruled **yes**,
and added to `tests/mutations.json`'s methodology.

The tester's argument is that it found instance eleven in ninety seconds. Mine
is stronger and comes from this review: the wave-6 decision to exclude
`narrate/adapters/anthropic.py` **wholesale** was a coverage question answered by
intuition, and it was wrong by seven mutants and one real gap. A coverage run
answers "which anchors can speak" per function instead of per module, which is
the resolution the exclusion needed. The clause as written also requires that any
module or branch at 0% be **named in the report**, so the next §5 is a
measurement rather than an inventory — which is precisely the defect BUG-17
found in this one.

I did not make it a pinning test. It is a process rule in a data file, like the
four harness hazards beside it; there is no behaviour to pin.

### 9.2 A-e1 … A-e15

All fifteen **upheld**. Notes only where I have something to add.

| | Ruling |
| --- | --- |
| **A-e1** (one group per call, registry order) | Upheld. Registry order over severity order is right for the stated reason — a narrative whose paragraph order moves when a severity changes is undiffable. |
| **A-e2** (a detector with no findings gets no paragraph) | Upheld. The literal reading of "per *finding group*". |
| **A-e3** (`tool_name` dropped though R42 permits it) | Upheld, and now enforced at the model as well as the builder (§2.1). The coder took the narrower of two conflicting clauses, which is the correct default for a security property. |
| **A-e4** (the CLI extracts the cost figures) | **Upheld, and it is the strongest decision in the branch.** R44 wins over R42's "built by `narrate/summary.py`" because obeying it makes the property stronger, not weaker. Caveat now closed: the discipline it creates was pinned by nothing (§3). |
| **A-e5** (caps of 20 and 20) | Upheld. R42 bounds nothing and an unbounded payload disappears on exactly the runs worth narrating. See C-4: the model-level cap guard has never fired. |
| **A-e6** (fatal codes stop narration; per-call codes do not) | Upheld. It is the **only** reading that satisfies R43's "every group falls back" and AC12's scripted third-group transport error at once, and the coder says so plainly rather than choosing one and hoping. |
| **A-e7** (six named ASCII whitespace; control characters rejected, not cleaned) | Upheld, and correct for the stated reason: S24 is an open flag about `str.isprintable()` moving between interpreters, and a new untrusted source is the wrong place to add a second Unicode-table-dependent predicate. |
| **A-e8** (a `NarratorError` carries a code and nothing else) | Upheld. R41's "never echoes a body" becomes a signature property instead of a rule every future raise site must remember. The best small decision in the increment. |
| **A-e9** (narrator output redacted in both modes, never blanked) | Upheld. Wave-7 `V45` showed the ruling was pinned for one of the loop's two branches; now both (§7.1 cluster C). |
| **A-e10** (anchor, no nav entry) | Upheld **conditionally on S36**. As R43 stands, a nav entry makes the byte-identity clause unsatisfiable, so the coder had no choice. The usability cost is real and the PM should decide it, not the coder. |
| **A-e11** (no stylesheet change) | Upheld. Editing `REPORT_STYLE` would move `STYLE_SHA256` and both goldens — i.e. increment 5 would have changed every no-`--explain` render, the opposite of R43's purpose. |
| **A-e12** (`report/narrative.py` is a new module in `report/`) | Upheld, same ruling as A-d2 covered `sanitize.py`. **PM**: amend R44's layout block once, to say `report/` may hold modules it does not list. Two increments have now asked. |
| **A-e13** (`report.json` gains one key, only under `--explain`) | Upheld. See C-1 on the asymmetry it creates for human readers. |
| **A-e14** (`narrator=` keyword on `main`/`run`/`analyze`) | Upheld. AC12 needs a fixture client on the real path; a keyword-only parameter is invisible in `--help` and unreachable from a command line, where a flag or an env var would have put a test affordance in the product surface. |
| **A-e15** (`FixtureNarratorClient` ships in the package) | Upheld. R41 names it under `narrate/`, AC12 is a criterion about the product, and the client opens no socket, reads no environment variable and imports nothing optional. |

---

## 10. Spec flags — rulings and the amendment queue

`S14`–`S32` are unchanged by this increment and remain with the PM. The eight
this review must rule on:

| # | Ruling | Amendment queued |
| --- | --- | --- |
| **S33** (R42 permits `tool_name`; AC12 forbids it) | **AC12 wins.** The coder took the narrower reading and it is right: R16 is a shape check, `SENTINELSpantoolname` and `AKIAIOSFODNN7EXAMPLE` both satisfy it, and a requirement pair that permits into the payload exactly the byte its own acceptance criterion forbids is a defect in the pair. | Delete "and `tool_name` values already constrained by R16" from R42's list; say the payload carries **no trace-derived string of any kind**. |
| **S34** (R42's "built by `narrate/summary.py`" vs R44's import rule) | **R44 wins; the coder's option (b).** R44 has a checked-in AST test and obeying it is half of AC12's structural guarantee. Widening `narrate`'s imports to include `cost` would put a `CostReport` — which carries `AgentCost.agent_id` and `SpanCost.model` — back into the builder's scope, for no benefit. | Add a clause under R42 naming the CLI as the extractor. **And say the extraction is pinned by a test** — this review found it pinned by nothing (§3). |
| **S35** (the `live_narrator` marker deselected zero) | **Satisfied.** The owner's ruling is implemented and the env gate as a *branch inside the test* is the correct reading — it is the only one under which R49's no-`skipif` rule and its non-zero-deselection clause are simultaneously satisfiable. | State in R49 that the live test is collected always, deselected by marker in CI, and branches on the env var rather than skipping. See **C-2**. |
| **S36** (byte identity forbids a nav entry) | **Accept the consequence.** The coder is right that R43 as written admits only one build. The section is anchored and unlinked. | One clause on R43 naming the consequence, or widen the byte-identity clause to "the section and its nav entry" if the nav link is wanted. The PM chooses; the implementer cannot. |
| **S37** (is a comment block a ledger entry?) | **Yes, for one deselection.** Reasoning in **C-7**: `allowed_skips.txt`'s emptiness is itself an assertion, and a second data file needs a second hook and its own R50 canary — three artefacts for one entry. The comment is already enforced by a test that names the deselected test. | Say in R49 that a deselection is recorded as a named comment in `allowed_skips.txt`, asserted by a test naming the deselected test. **Revisit at the second marker.** |
| **S38** (the snapshot version and model key are shape-checked only) | **Uphold, and this is the one I most want the PM to take.** Wave-7 `V43` is instance twelve and it lives in exactly this gap: the leaf's provenance rested on a call site nothing checked, and its shape check admits any slug. | R42 should say the snapshot version and model key are validated against the **keys present in the loaded snapshot**, not against a pattern. That converts two of four shape-checked leaves into closed vocabularies at no cost — the snapshot is already loaded in the process that builds the figures. |
| **S39** (where the `tool_name` refusal lives) | **Implemented in code by `82e4702`**; the spec clause is still owed. The code now matches its own docstring, which needed no ruling; R42 saying so is the ruling. | Add to R42 that the payload's metric entries are refused for any key in `detect.base.TRACE_DERIVED_METRIC_KEYS`, **at the model**. |
| **S40** (R43 names an element the renderer does not emit) | **Confirmed by me.** The document emits `<section class="section" id="narrative">`; the literal `<section id="narrative">` appears nowhere. The failure mode is red rather than green, so it is a wording defect and not a leak — but a reader cannot tell whether the class is forbidden. | Reword to "the `<section>` element whose `id` is `narrative`, and everything between it and its closing tag". |

### New flag

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S41** | AC14 / R50 | AC14 says of six failures that "Each of these is a checked-in canary test that asserts the failure occurs". Two had none: the collection-floor hook and the unlisted-skip hook. What existed were unit tests of the *pure helpers* `conftest.py` factored out of them, which assert that a list comprehension computes a list. Neither touched the part that fails a run — and for the skip hook that part reads a **private pytest attribute**, `terminalreporter._session`, with `getattr(..., None)` and a `if session is not None` guard, so on a pytest without it the hook prints a red banner and the session **exits 0**. Fixed in `96db54e`; the flag is that AC14 said this was already true. | Either R50 should list the two hooks among its required canaries explicitly (it lists seven subjects and neither hook is one of them, so AC14 and R50 disagree about what must exist), or AC14's "each of these" should be narrowed to the clauses R50 names. The first is better. Separately, `conftest.py` should stop depending on a private pytest attribute, or pin the pytest patch version. |

---

## 11. Release verdict for v1

**Tag it.** `v1.0.0`, from this branch after merge, with the four things below
written into the release notes rather than left in a review document.

### What is shipped

One command, one input set, two files out. Claude Code JSONL in; a
self-contained `report.html` and a `report.json` out, byte-identical across
repeated runs, separate processes, four hash seeds, three timezones, two
locales, working directories, absolute paths and command-line path order. Eight
detectors behind a registry that is the single source of truth and cannot be
bypassed. A bundled, versioned rate snapshot with `Decimal` money and no float
in the output bytes. Redaction and escaping through **one** classified boundary
per concern. An opt-in `--explain` narrative whose payload provably carries no
trace prose. A declared, unimplemented replay seam that nothing imports.

And, unusually, a suite that has been made to fail on purpose in twelve places
and a mutation ledger with seven waves and 536 recorded mutants.

### What is knowingly open

1. **Spec flags S14–S41.** Twenty-eight of them, none blocking, all with a
   suggested resolution. Four are worth the PM's time before v1.1: **S38** (the
   shape-checked payload leaves — instance twelve lives there), **S16/the
   standing xfail** (an identifier still carries attacker-chosen bytes under
   `--no-previews`, which R38's "entirely" does not admit), **S27** (`--no-previews`
   can drop a `critical` finding, and the README now says so in a blockquote —
   the code fix is an increment-1 mapper change and is not in this branch), and
   **S41** (AC14 and R50 disagree about which canaries must exist).
2. **The owner's rate sanity-check.** Never done. Every dollar figure in every
   report is a `Decimal` multiplication of a number in
   `swarm_observer/cost/data/model_rates.json` that no human with domain
   knowledge has read. The arithmetic is heavily tested; the **inputs** are
   unreviewed. This is the largest unmitigated risk in v1 and it is not a
   code risk.
3. **The browser check.** Nothing in this repository has ever been opened in a
   browser. "The CSP meta is honoured", "the filter buttons work", "a 5,000-row
   table is usable" and now "the narrative section reads well" are unverified by
   construction. The security properties are asserted against the parsed DOM,
   which is the right proxy and is not the same thing as looking.
4. **The never-executed live paths.** One outbound request and six
   exception-mapping clauses in `narrate/adapters/anthropic.py` (§6). Nothing
   else. They cannot report green, they degrade to a deterministic paragraph,
   and the first person to set `SWARM_OBSERVER_LIVE_NARRATOR=1` runs them —
   which is why **C-2** matters: make sure that person is running the offline
   preconditions in CI too.

### Would I tag it?

Yes, and the reason is narrower than "the tests pass". Across five increments
this project found the same defect **twelve times** — a check reporting green
while structurally unable to fail — and it found the twelfth one *in this
review*, in the artefact built to prevent the ninth. That is not a sign the
process is failing. It is the only evidence available that the process works,
because a project that had stopped finding them would look identical to a
project that had stopped looking.

What I would not do is tag it and call the practice finished. The three things
that found instances nine through twelve — a control arm in every mutation wave,
a corpus whose values can distinguish the answers, and a coverage run before the
sweep is designed — are now all three in the harness contract. Instance thirteen
exists. The contract is what will find it.

---

## 12. My commits

| | |
| --- | --- |
| `82e4702` | `review(R42; BUG-14, S39)`: `MetricEntry` refuses a trace-derived key at the model |
| `4d23ea9` | `review(R33, R43; BUG-16)`: the narrative title is classified by its type, not its caller |
| `7aa29fb` | `review(R43; BUG-15)`: the visible fallback marker is a token a narrator may not write |
| `f978ceb` | `review(R42, R43, R45; wave 7)`: ten mutation gaps, each closed with a test |
| `88254ae` | `review(R33, R43)`: the field sweep derives its fields but not its models |
| `96db54e` | `review(R49, R50; AC14)`: the two suite-integrity hooks get the canaries AC14 requires |

Each carries its pinning test in the same commit. No commit edits the spec.
