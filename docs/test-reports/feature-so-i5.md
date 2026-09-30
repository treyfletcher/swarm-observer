# Test report: swarm-observer increment 5 (`feature/so-i5`)

Branch: `feature/so-i5` (based on `feature/so-i4`)
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED)
Scope tested: **R41–R45**, **AC12**, plus R49's and R50's clauses this increment
owes. Prior write-up: `docs/prs/feature-so-i5.md`.

## Result

Seven new test modules, one new support module, one new R50 canary, an 83-mutant
sweep over the six modules this increment created, and six changes to the
inherited harness. **2861 passed, 2 xfailed** on CPython 3.11.15 and 3.12.3,
with the credential environment scrubbed and `anthropic` not installed.
`ruff check`, `ruff format --check` and `mypy --strict` clean on both.

AC12 holds in both halves. The byte-identity clause holds over the real CLI's
bytes and over both `--previews` modes, and each of its four dependencies has
its own test. The sentinel clause holds — but **the coder's "structural, not
filtered" claim is upheld only in part**, and the part that is not is §6.

Five bugs, **BUG-14**…**BUG-18**. Four of the five are pinned. Four spec flags,
**S37**…**S40**. The mutation sweep found thirteen real gaps in my own first
round of tests, all now closed; five mutants survive, two of them declared
control arms.

The headline is **BUG-18**. The only `--explain` exercise in the inherited
suite runs over `clean_single_agent.jsonl`, which produces **zero findings** and
therefore exactly **one** narration group. Its assertions `all(p["fallback"])`
and `{p["reason"]} == {"sdk_not_installed"}` are statements about a single
element, so nothing in the suite could distinguish R43's per-group fallback
from a whole-run one — and `serialize_request`, the function AC12's sentinel
clause is a property of, had **never executed**. That is instance eleven, in
the family's first shape: a fixture with no case that could trip the check.

---

## 1. Requirement × test coverage

| Req | What is driven | Where | Gaps |
| --- | --- | --- | --- |
| **R41** | the Protocol as a shape (satisfied by a three-line stub written in the test module, not by `FixtureNarratorClient`); the one method's signature; a control arm that is *not* a client; the three named subclasses; every one of the twelve codes constructing and rendering one line; an undeclared code refused; **no parameter exists through which a body could be passed**, over all four constructors; `FixtureNarratorClient`'s order, its raising entries, its recording-before-raising, its `remaining`, and its `AssertionError` on exhaustion; `paragraph()` normalizing nothing | `test_narrate_client_r41.py` (28) | **Full for what runs offline.** The SDK adapter's `complete` past the import guard is §8. |
| **R42** | every string-valued leaf of the request tree **enumerated from `model_fields`** and attacked with a five-member battery; a completeness arm that fails when a leaf has no attack; the closed-vocabulary leaves refused even a slug; the shape-check leaves named; the AC12 sentinel sweep over a corpus whose sentinels the payload's own validators *accept*; the non-vacuity arm; an independently computed vocabulary; no `trace_id`, path, file name, preview or finding id; the caps; the group order; the severity counts; `serialize_request`'s determinism | `test_narrate_payload_r42.py` (140), `sentinel_trace.py` | **Full, with a stated scope.** §6 and **BUG-14**. |
| **R43** | `normalize_paragraph`, `has_control_characters`, `validate_paragraph` each arm and each boundary; the validation **order**; all twelve error codes driven through `narrate` asserting the **call count**; a per-call failure in the middle; a fatal failure in the middle; `client=None`; four wrong-shaped returns; a vendor exception; `KeyboardInterrupt` not swallowed; `AssertionError` re-raised; the deterministic template's **text**, both branches; the two markers emitted together; hostile paragraphs through the parsed document; redaction in both modes; not blanked by `--no-previews` | `test_narrate_fallback_r43.py` (75), `test_explain_end_to_end_ac12.py` (27), canary | **Full.** **BUG-15** is a property of R43's visible marker, not a gap in the coverage. |
| **R44** | the redact call-site scan (new); the replay seam's models and validators, driven for the first time | `test_boundaries_and_posture.py` (+1), `test_replay_seam.py` (16) | The inherited AST rules are increment 1's and pass untouched. |
| **R45** | `_sdk()` raising `sdk_not_installed` in this environment; the whole suite green with the extra absent and every credential unset; **`no_credentials` driven with the import stubbed**, which is the only way an offline suite can reach R43's absent-key condition | `test_narrate_fallback_r43.py`, `test_live_narrator.py` (6) | **BUG-17.** |
| **AC12** | first half: four groups asked in order, script exhausted exactly, exit code unchanged in both directions, stdout unchanged, two paragraphs rendered, two fallen back with both markers, the 900-character paragraph absent from both documents, the section strippable to byte-identical output, `report.json` gaining exactly one key. Second half: §6. | `test_explain_end_to_end_ac12.py`, `test_narrate_payload_r42.py` | **Full.** |
| **R49** | the `live_narrator` marker carried by one test; the deselected count measured as *whole suite minus deselected suite* in a subprocess and asserted non-zero; the marker carried by nothing else; an **AST** scan for `skipif`/`importorskip` over the whole test tree, with its own can-it-fail arm; the ledger entry | `test_live_narrator.py` | **S37** — the ruling is provisional. |
| **R50** | `narrator_fallback_dropped`, four arms: a control arm, the DOM class dropped, the visible prefix dropped, and `FATAL_CODES` emptied — the last one asserting a **call count**, because its regression leaves the rendered document byte-identical | `canaries/test_canary_narrator_fallback_dropped.py` (4) | `CURRENT_INCREMENT` is now 5 and the ledger's outstanding set is empty. |

### New modules

| Module | Tests | What it is |
| --- | --- | --- |
| `tests/test_narrate_client_r41.py` | 28 | R41: the seam, the taxonomy, the fixture client |
| `tests/test_narrate_payload_r42.py` | 140 | R42 + AC12's second half, three independent ways |
| `tests/test_narrate_fallback_r43.py` | 75 | R43: validation, fallback, markers, untrusted output, the adapter |
| `tests/test_explain_end_to_end_ac12.py` | 27 | AC12 through the CLI, the four byte-identity dependencies, R34/R35 over the `--explain` document |
| `tests/test_replay_seam.py` | 16 | the seam's validators, run for the first time |
| `tests/test_live_narrator.py` | 6 | the marked test and R49's deselection proof |
| `tests/canaries/test_canary_narrator_fallback_dropped.py` | 4 | the R50 canary |
| `tests/sentinel_trace.py` | — | support: AC12's sentinel corpus |

### Changes to the inherited suite

1. **`tests/test_guard_verification.py`** — `test_r52_an_uncited_requirement_with_no_ledger_entry_is_detected` asserted `pending - cited` was **non-empty** ("the ledger is empty; this arm would be vacuous"). Increment 5 cites R41 and R42, so `traceability_pending.txt` is now empty and that assertion was an assertion that a completed ledger must stay incomplete — the exact thing increment 4 removed when it replaced `test_r50_later_canaries_are_ledgered_not_forgotten`. Rewritten to drive the arithmetic against a probe, so the mechanism stays exercised with no debt outstanding.
2. **`tests/test_suite_integrity.py`** — `CURRENT_INCREMENT` 4 → 5; `narrator_fallback_dropped: 5` added to `REQUIRED_CANARIES`; `test_r50_a_later_increments_debt_would_still_be_ledgered`'s hypothetical moved from `narrator_fallback_dropped: 5` (now delivered) to an increment-6 name, which the coder's §8 named as owed in the same commit; the six modules this increment created added to `test_r49_the_ledger_covers_every_module_the_increment_touched`.
3. **`tests/test_boundaries_and_posture.py`** — one new test, the coder's risk item 6: no module outside `report/sanitize.py` calls or imports `redact`. The shape of the `escape_html` scan, one boundary over, and the boundary that has leaked in every increment.
4. **`tests/traceability_pending.txt`** — R41 and R42 deleted. **The ledger is now empty for the first time in the project.**
5. **`tests/collection_floor.json`** — the seven new modules added; every floor raised to its post-increment count.
6. **`tests/allowed_skips.txt`** — a comment block naming the one marked test and why it is deselected rather than skipped. **No entry**: the file's data is still empty and a skip is still a session failure. See **S37**.
7. **`tests/mutations.json`** — wave 6, 83 mutants over six modules. §5.

---

## 2. What I did about the three things the coder flagged hardest

### "Its fixture client is its own oracle"

Three separate answers, because the risk has three parts.

* **A stub of my own.** `StubNarrator` in `test_narrate_client_r41.py` is three lines and imports nothing from `narrate/fixture.py`. `RaisingStub` and `ReturningStub` in `test_narrate_fallback_r43.py` are the same. Every assertion about `narrate`'s behaviour uses one of those; `FixtureNarratorClient` is used only where **its own** documented behaviour is the subject (the exhaustion arm, and AC12, where the criterion names it).
* **The serialized payload as a string, against my own sentinels.** `tests/sentinel_trace.py` is a second hostile corpus and its only difference from `tests/hostile_corpus.py` is the alphabet — every sentinel is `^[a-z][a-z0-9_]{0,63}$`, which is what the payload's validators *accept*. §6 explains why that choice is the whole finding.
* **The rendered documents, parsed.** Every marker, injection and allowlist assertion reads a `tests/rendered.Document`, never a `Narration` or a `Narrative`.

### "AC12's byte-identity has four dependencies"

Four narrow tests in `TestByteIdentityDependencies`, plus a fifth for the section's position:

| Test | The edit it refuses |
| --- | --- |
| `test_r43_the_narrative_anchor_is_not_a_nav_section` | `"narrative"` added to `SECTION_IDS` |
| `test_r43_render_options_records_no_explain_flag` | an `explain` field on `RenderOptions` |
| `test_r43_the_stylesheet_carries_no_rule_for_the_narrative_classes` | a CSS rule (and it re-pins `STYLE_SHA256`/`SCRIPT_SHA256`, and asserts the two classes *are* allowlisted so it is not passing because they do not exist) |
| `test_r43_nothing_links_to_the_narrative_section` | a nav link added by hand rather than through `SECTION_IDS` — which the first test would not see |
| `test_r43_the_section_sits_at_r36s_fixed_position` | the section moving out of R36's slot |

The strip itself is **line-based**, not a regex: R43's clause is only satisfiable if the section is a contiguous run of *whole* lines, and a regex over the document would still match a section spliced into the middle of an existing line. `strip_narrative_section` asserts the opening tag is exactly one whole line before it removes anything.

One small note for the reviewer: the element the renderer writes is `<section class="section" id="narrative">`, not R43's `<section id="narrative">`. A test written to R43's literal text finds nothing to strip; the failure mode is red rather than green, so it is a documentation point rather than a bug, and it is recorded in the strip helper's docstring. **S40.**

### "A-e6's fatal/per-call split — assert the call count"

`test_r43_every_code_falls_back_and_the_call_count_tells_the_two_apart` is parametrized over all **twelve** codes in `NARRATOR_ERROR_CODES`. (The PR's risk item 4 says "all ten codes"; there are twelve. Worth saying only because the instruction to drive *all* of them is the right one and a miscount is how two get left out.) Each arm asserts three things: every group fell back, every reason is that code, and `Narration.calls == 1` for the three run-fatal codes and `== len(groups)` for the other nine. `FATAL_CODES` is additionally compared against a set typed from R43's own sentence rather than read from the module, so the test is a check and not a restatement.

Two further arms drive the shapes a uniform client cannot: a failure on the **third of six** calls, with the fourth answered (AC12's shape), and a fatal code on the **second**, with the remaining four falling back unasked.

Measured, on the six-group sentinel corpus:

```
auth_rejected                  calls=1 fallbacks=6/6   fatal
no_credentials                 calls=1 fallbacks=6/6   fatal
sdk_not_installed              calls=1 fallbacks=6/6   fatal
not_configured                 calls=6 fallbacks=6/6
provider_error                 calls=6 fallbacks=6/6
rate_limited                   calls=6 fallbacks=6/6
response_control_characters    calls=6 fallbacks=6/6
response_empty                 calls=6 fallbacks=6/6
response_malformed             calls=6 fallbacks=6/6
response_too_long              calls=6 fallbacks=6/6
timeout                        calls=6 fallbacks=6/6
transport_failed               calls=6 fallbacks=6/6
```

The fallback column is identical for all twelve. That is the column the weaker assertion would have read.

---

## 3. Instance eleven

**The only `--explain` exercise in the inherited suite runs over a trace with no findings.**

`tests/test_cli_analyze.py::test_r39_explain_is_accepted_and_cannot_change_the_exit_code` runs `analyze CLEAN --explain`, where `CLEAN` is `tests/fixtures/traces/clean_single_agent.jsonl`. That fixture produces **zero findings**, so `narration_groups` returns `("overall",)` and the run has exactly one group. Its two interesting assertions —

```python
assert all(p["fallback"] for p in document["narrative"]["paragraphs"])
assert {p["reason"] for p in ...} == {"sdk_not_installed"}
```

— are statements about a **single element**. They are true of a per-group fallback and of a whole-run one, of a loop that continues and of a loop that stops, and of a narrator asked once and one asked six times.

A coverage run of the whole inherited suite over the new modules confirms what that costs:

```
swarm_observer/narrate/fixture.py     0%   nothing in it had ever run
swarm_observer/narrate/narrator.py   67%   normalize_paragraph, has_control_characters,
                                           validate_paragraph, the success path, the
                                           except-Exception clause and the
                                           `stopped is not None` short-circuit: never
swarm_observer/narrate/summary.py    84%   serialize_request: never. severity_counts'
                                           loop body: never. _finding_summary: never.
                                           _metrics: never. group_summary's
                                           per-detector branch: never.
swarm_observer/narrate/client.py     87%   every validator's raise branch: never
```

`serialize_request` is the function the PR calls "the exact string AC12's sentinel test inspects". It had not executed. Every refusal in the payload type — the whole of the "structural, not filtered" mechanism — had not executed either.

This is the family's **first** shape, not a new one: *a fixture set with no case that could trip it*. It is instance eleven, and it is closed: every new module drives a corpus with at least four narration groups, and `test_r42_the_payload_is_not_empty` asserts that precondition explicitly so a corpus that stops firing detectors makes the sweep red rather than vacuous.

---

## 4. Bugs

Numbered continuing from increment 4's BUG-13. "Pinned" means a checked-in test
fails if the bug is reintroduced (or, for an `xfail`, if it is fixed).

### BUG-14 — `MetricEntry`'s documented second refusal of `tool_name` does not exist, so AC12's structural claim is a filter at its one load-bearing point

**Severity: medium.** No leak today. The defect is that the mechanism is not the one the module says it is, in the module whose entire thesis is that mechanism.

`narrate/client.py`'s `MetricEntry` docstring:

> The trace-derived key R16 admits (``tool_name``) never reaches this model: :mod:`.summary` drops it by consulting ``detect.base.TRACE_DERIVED_METRIC_KEYS`` rather than by listing keys, **and the validator below refuses it a second time so a future caller cannot add it back by hand**.

The validator never looks at the key. It checks the *value* against `AUTHORED_METRIC_VALUE_PATTERN`, which is `[a-z][a-z0-9_]{0,63}` — an alphabet that contains a GitHub token whole.

**Failing input**

```python
from swarm_observer.narrate.client import MetricEntry
MetricEntry(key="tool_name", value="ghp_" + "a" * 24)     # constructs
```

**Observed** — the entry constructs, and serializing a request containing it puts `ghp_aaaaaaaaaaaaaaaaaaaaaaaa` into the payload verbatim.
**Expected** — per the docstring, a second refusal; per AC12, no trace-derived string in the payload at all.

The only thing keeping `metrics.tool_name` out of a real `--explain` payload is one line in `narrate/summary.py::_metrics`:

```python
if key not in TRACE_DERIVED_METRIC_KEYS
```

That is a filter, not a type, and it is the single point of failure for the half of AC12 the coder calls structural. (Mutating that one line — `NS01` — is killed, so the filter *is* tested. The finding is about what the mechanism is, not whether it currently works.)

**Spec clause violated** — none directly; R42 permits `tool_name` and AC12 forbids it, which is the coder's own **S33**. What is violated is the claim in §3 of the PR.
**Pinned** — `test_r42_metric_entry_still_admits_the_one_trace_derived_key` (pins the gap; goes red when it is fixed, which is the signal to rewrite it) and `test_r42_the_builder_is_the_only_thing_that_drops_tool_name` (pins that the filter is what does the work).
**Cheapest fix** — three lines in the validator:

```python
if self.key in TRACE_DERIVED_METRIC_KEYS:
    raise ValueError(...)
```

### BUG-15 — R43's visible fallback marker is forgeable by the narrator

**Severity: medium.** An untrusted source can produce a string identical to swarm-observer's own trust marker.

R43 pairs two markers on a fallback paragraph: the DOM class `narrative-fallback` and the visible prefix `Deterministic summary:`. The class is emitted by one branch of one loop and a narrator cannot reach it — that part is sound, and the PR is right about it. The **prefix is just text**, and the narrator's own paragraph is placed *after* it, never instead of it. But nothing stops a narrator's paragraph from *containing* the prefix.

**Failing input** — a narrator paragraph whose text is `"Deterministic summary: written by the model, not by the tool"`, on a group that did **not** fall back.

**Observed**

```html
<p class="narrative">Deterministic summary: written by the model, not by the tool</p>
```

`html.count("narrative-fallback") == 0`, `html.count("Deterministic summary:") == 1`.

**Expected** — the visible marker appears only where the DOM class does, or the prefix is a reserved token a paragraph may not contain.

This matters twice. A reader skimming the rendered page for "which of these did the tool write?" sees only the prefix — the class is invisible to them. And any test that *counts* the prefix (AC12's own wording invites one) is counting a string an untrusted source can write. It is the same family as **S32**/BUG-12, where `secret_assignment` re-matched a `[redacted:…]` marker an earlier pattern had written: a marker this package authors, which something else can also author.

**Spec clause violated** — R43's fallback bullet, in spirit: a marker that an untrusted source can forge does not mark anything. Also the Security considerations' "Untrusted model output".
**Pinned** — `test_r43_a_narrator_can_forge_the_visible_fallback_prefix` pins the current behaviour and says in its docstring what a fix should do.
**Cheapest fix** — reject a paragraph containing `FALLBACK_PREFIX` in `validate_paragraph` (a fourth check, and the fallback it causes is the correct outcome).

### BUG-16 — `NarrativeParagraph.title` is classified `authored` in both renderers but constrained only by length, so a credential in a title reaches both reports verbatim

**Severity: low** as shipped (the product path never does it), **medium** as a pattern: it is the fifth occurrence of "a field classified as this package's own that a non-package source can populate", after BUG-2, S13, the increment-3 `ParseWarning.detail` finding and the increment-4 review's `SpanError.code`.

`report/narrative.py`:

```python
group:  str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")
title:  str = Field(min_length=1, max_length=120)          # no pattern
reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
```

Its two siblings are pattern-constrained; `title` is not. Both renderers write it with `KIND_AUTHORED`, which is a no-op.

**Failing input**

```python
render_html(..., narrative=Narrative(paragraphs=(
    NarrativeParagraph(group="overall", title="AKIAIOSFODNN7EXAMPLE and <script>", text="ok"),
)))
```

**Observed** — `report.html` contains `<h3>AKIAIOSFODNN7EXAMPLE and &lt;script&gt; (overall)</h3>` and `report.json` contains the key unredacted. In **both** `--previews` modes.
**Expected** — every string a renderer writes is classified by its **type**, not by its caller.

**Spec clause violated** — R33 (every trace-or-model-derived string is redacted at the boundary) read together with the Modularity notes' "Guards are properties of functions, not of call paths. … no renderer may assume its caller sanitized."
**Pinned** — `test_r43_a_title_cannot_carry_a_credential_into_a_report`, as `xfail(strict=True)`, following the S16 precedent already in `test_json_report.py`. The suite now reports **2 xfailed**.
**Cheapest fix** — one `pattern=` on the field (the titles the product uses are registry titles and an alphabet of letters, spaces and hyphens covers them), or classify it as `identifier`.

### BUG-17 — `no_credentials` is unreachable in any environment R45 permits, and the PR's §5 inventory says three functions have executed that have not

**Severity: low** (a documentation defect plus a dead branch), **but it is a defect in the one document whose purpose is an honest list.**

The PR's §5 says:

> **Executed, offline, and worth distinguishing from the above:** the `ImportError` branch …, `_credential_present` returning False under R45's scrubbed environment, and `system_prompt()` / `user_prompt()`, which are pure functions of a `NarrationRequest`.

A coverage run of the whole inherited suite reports lines 80, 101 and 146 of `narrate/adapters/anthropic.py` — the bodies of `system_prompt`, `user_prompt` and `_credential_present` — as **missed**. None of the three had executed.

The reason is structural, and it is the substantive half of the finding. `complete` does:

```python
sdk = self._sdk()                 # raises sdk_not_installed when the extra is absent
if not self._credential_present():
    raise NarratorAuthError("no_credentials")
```

With `anthropic` not installed — the only environment R45 permits, and the only one CI has — the import raises first and the credential branch is never reached. R43 names "an absent API key" as one of five conditions that must take the fallback path; as built, that condition cannot occur in CI at all, and `no_credentials` is one of the three `FATAL_CODES` on which A-e6's whole split depends.

**Pinned** — `test_r43_an_absent_api_key_is_reachable_only_past_the_import` drives it with `_sdk` stubbed, which is the only way an offline suite can reach it, plus a control arm showing the credential check passes when a key is present. `system_prompt` and `user_prompt` are now driven too.
**Not a code change I would ask for.** The ordering is defensible (you cannot use a key without an SDK). What is owed is a corrected §5 and a note that `no_credentials` is exercised only through a stub.

### BUG-18 — the suite's only `--explain` test runs over a zero-finding trace, so R43's per-group clause and `serialize_request` had never executed

**Severity: high**, as a suite defect. No product behaviour is wrong.

Described in full in §3. **Spec clause violated** — R49's purpose, and AC12, which was not covered.
**Pinned** — closed by the new modules; `test_r42_the_payload_is_not_empty` and `test_r43_every_code_falls_back_and_the_call_count_tells_the_two_apart` both assert the corpus has at least four groups, so the precondition cannot silently disappear again.

### Described but not pinned

* **`Narration.fallbacks` is dead.** `narrate/narrator.py`'s `Narration.fallbacks` property is read by no product code — `cli/main.py` maps to `report.narrative.Narrative`, whose own `fallbacks` is what both renderers use. It is now read by tests, which is why the `NR09`/`N20` mutants are killed; before this increment neither was. Not worth a change.
* **The `class` allowlist is unconditionally wide.** `CSS_CLASSES` contains `narrative` and `narrative-fallback` whether or not a narrative is rendered, so a default render's allowlist admits two class tokens the document cannot contain. This is the `W5-A08` shape, but both tokens are package literals with no CSS rule, so nothing attacker-influenced can occupy them. The `id` entry — the one that could have admitted something — *is* conditional, and `test_r34_a_default_render_does_not_widen_the_allowlist` pins that.
* **`--explain` has no golden file.** §8.

---

## 5. The mutation sweep

### Methodology

`tests/mutations.json`'s declared methodology, re-run with both of increment 4's
added clauses: the unmutated suite runs **first** and the sweep aborts if it is
red; every kill records the pytest node id that produced it. Plus the inherited
harness contract — `-x`, the whole suite as the oracle, `PYTHONDONTWRITEBYTECODE=1`,
`__pycache__` purged around every mutation, the original text rewritten in a
`finally` and on `SIGTERM`/`SIGINT`, a tree digest verified between mutants, a
drifted anchor reported **NOT-APPLIED** rather than skipped, and **serially** —
one interpreter at a time, nothing else running in the container.

The harness re-checks any kill whose node id matches a timing test, per the
increment-4 review's fourth harness hazard. No wave-6 kill was attributed to a
timing test, so the re-check never fired.

### Operator set

The ledger's declared set, drawn on as the code allowed: relational flip;
integer constant ±1; boolean-connective swap; guard-clause drop; negation
drop/insert; membership flip; slice/range bound off-by-one; sort- and
group-key component drop; normalization drop; rung/branch reorder;
call-argument swap; literal substitution; control-flow swap; container-default
swap. Two operators added to the ledger's list, both because this increment's
code has shapes the earlier ones did not:

* **`exception-swallow`** — `except X: raise` becomes swallow-and-continue. `narrate/narrator.py`'s `except AssertionError: raise` is the only instance in the package, and it is what stops a miscounted fixture script reporting green.
* **`set-operand-add`** — widen a `frozenset` literal by one member. `FATAL_CODES` is a security-relevant closed set and dropping a member is a different defect from adding one.

Wave 6 by operator: literal 18, guard-drop 8, relational 8, negation 7,
membership 6, call-argument 6, int±1 6, normalization-drop 4, reorder 3,
slice-bound 3, set-operand-drop 2, arith 2, and/or 1, boundary 1,
container-default 1, control-flow 1, exception-swallow 1, set-operand-add 1,
set-operand-swap 1, sort-key-drop 1, **control-no-op 2**.

**Two declared control arms**, in two different modules (`N21-CONTROL` in
`narrate/narrator.py`, `NF09-CONTROL` in `narrate/fixture.py`), so the warrant
for the kills is not a property of one file. Both must survive; both do, on both
interpreters.

### Modules covered — every module this increment created

| Module | Wave-6 mutants | Previously swept |
| --- | --- | --- |
| `swarm_observer/narrate/narrator.py` | 21 | never |
| `swarm_observer/narrate/client.py` | 16 | never |
| `swarm_observer/narrate/summary.py` | 14 | never |
| `swarm_observer/replay/target.py` | 12 | never |
| `swarm_observer/report/narrative.py` | 11 | never |
| `swarm_observer/narrate/fixture.py` | 9 | never |

`narrate/adapters/anthropic.py` is **not** swept, deliberately: the module is
the one place R45 keeps out of the offline suite, so a mutant in
`complete` past the import guard would survive by construction and the
survivor list would say something about CI rather than about the tests. §8.

`test_r49_the_ledger_covers_every_module_the_increment_touched` now requires all
six with ≥ 8 mutants each — the decision the coder's §9.5 declined to make
unilaterally, taken here the way increment 4's tester took it for its four.

### Results

```
wave 6, first run, CPython 3.11.15, serial
TOTAL 83 killed 63 SURVIVED 20 not-applied 0
  -- ['N11','N15','N16','N17','N18','N19','N21-CONTROL','NC01','NC02','NC03',
      'NC05','NC06','NC16','NS02','NS04','NS06','NS11','NS14','NF08','NF09-CONTROL']

after closing thirteen real gaps, re-run of all twenty
TOTAL 20 killed 15 SURVIVED 5 not-applied 0
  -- ['N21-CONTROL','NC16','NS02','NS11','NF09-CONTROL']

wave 6, final: 83 mutants, 78 killed, 5 survived, 0 not-applied
```

**Thirteen of the twenty first-run survivors were real gaps in my own tests.**
They fell into two clusters and both are worth naming.

#### The thirteen real gaps, and what closed each

| Mutant | What it did | Why it survived | Killed by |
| --- | --- | --- | --- |
| `N15` | `len(request.groups) - 1` → `len(request.groups)` | **The deterministic template's prose was unasserted.** Every test checked that a paragraph *existed*, was non-empty, and contained no sentinel. None checked what it said. R43's fallback paragraph is the thing a reader gets instead of the narrator's, and five separate mutations of it were invisible. | `test_r43_the_overall_template_says_what_the_numbers_are` |
| `N16` | the unpriced clause inverted | the same | the same, plus `test_r43_the_template_has_no_unpriced_clause_when_nothing_is_unpriced` |
| `N17` | `_plural`'s `== 1` → `!= 1` | the same | the same |
| `N18` | severity order reversed in the template | the same | the same |
| `N19` | overall and per-detector templates swapped | the same | the same, plus `test_r43_a_per_detector_template_names_its_own_group` |
| `NC01` | `MAX_PARAGRAPH_CHARS` 800 → 801 | **Every expectation was computed from the constant**, so it held for any value of it. Increment 4's `I-H02`, recurring — and R43 names 800 in its own text. | `test_r42_the_three_bounds_are_the_numbers_the_spec_and_a_e5_name` |
| `NC02` | `MAX_FINDINGS_PER_GROUP` 20 → 21 | the same | the same |
| `NC03` | `MAX_TOTAL_ROWS` 20 → 21 | the same | the same |
| `NC05` | `METRIC_KEY_PATTERN`'s first character widened to `[A-Za-z]` | the hostile battery's members all had a space or punctuation, so a widening of the **first character only** slipped through. A one-sentinel battery is a property of that sentinel. | a `"Sentinelkey"` arm added to the battery |
| `NC06` | `MODEL_KEY_PATTERN`'s first character widened | the same | the same |
| `NS04` | `finding_id` dropped from `_ordered`'s sort key | R13 already returns findings in `finding_id` order and Python's sort is stable, so every input that came from the registry is already ordered. Increment 4's `I-H06`, one module over. | `test_r42_the_detail_order_does_not_depend_on_the_callers_order`, which hands `group_summary` a reversed list directly |
| `NS06` | `counts[severity] += 1` → `+= 2` | **nothing read the payload's severity counts.** | `test_r42_the_severity_counts_are_the_findings_severities` |
| `NS14` | `agents=len(agent_ids)` → `len(span_seqs)` | the two counts in `FindingSummary` that can be confused for each other were never distinguished | `test_r42_a_findings_counts_are_its_own_agents_and_spans` |
| `N11` | the control-character and length checks swapped | no paragraph in any test broke two checks at once, so the order was unobservable | `test_r43_a_paragraph_that_breaks_two_checks_reports_the_first_one` |
| `NF08` | `paragraph()` gained a `.strip()` | a fixture helper that normalizes would make every scripted paragraph valid and hide `validate_paragraph` behind it | `test_r41_paragraph_is_a_response_constructor_that_changes_nothing` |

(Fourteen rows for thirteen gaps: `N15`–`N19` are five mutants closed by one
test family, and the table counts the families.)

#### The five survivors that remain, by name

| Mutant | Operator | Classification |
| --- | --- | --- |
| `N21-CONTROL` | `control-no-op` (`[]` → `list()` in `narrate/narrator.py`) | **A declared control arm. It must survive**, and it does, on both interpreters. |
| `NF09-CONTROL` | `control-no-op` (`[]` → `list()` in `narrate/fixture.py`) | **The second declared control arm**, in a different module. A sweep that reported either killed is not reporting verdicts that come from the mutation. |
| `NC16` | `normalization-drop` (`tuple(sorted(SEVERITIES))` → `tuple(SEVERITIES)`) | **Equivalent, with evidence.** `SEVERITY_KEYS` has exactly two consumers, `narrate/client.py:273` and `:324`, and both read it as `set(SEVERITY_KEYS)`. The tuple's order is unobservable. |
| `NS02` | `normalization-drop` (`sorted(finding.metrics.items())`) | **Equivalent, with evidence.** `Finding._pinned_shape` raises unless `list(self.metrics) == sorted(...)`, so no `Finding` the model admits has unsorted metric keys. Increment 4's `I-H15`, one module over. Unlike `NS04`, this one cannot be reached by handing the builder an unsorted input, because the **model** refuses to construct one. |
| `NS11` | `call-argument` (`ensure_ascii=True` → `False`) | **Equivalent, with evidence.** Every string-valued leaf of a `NarrationRequest` is constrained to an ASCII alphabet — a `Literal`, a lowercase slug, a money string, a snapshot key or a `[A-Za-z0-9._-]` version — so `json.dumps` emits identical bytes either way. `test_r42_serialize_request_is_sorted_ascii_and_separator_stable`'s `blob.isascii()` arm is what makes that a measurement rather than a claim. Defence in depth against a v2 field that is not ASCII. |

Three equivalents, and all three are the same observation increment 4's review
made about `report/`: **a later layer re-normalizes what an earlier layer's
validators already guarantee.** `narrate/` does it to `detect/`'s output exactly
as `report/` does it to `model/`'s. It is correct under "guards are properties
of functions, not of call paths" and it means three of this package's lines can
never be shown to matter.

### Interpreter parity

The five survivors plus the thirteen re-killed gap-closers were re-checked on
CPython 3.12.3, serially, with the same harness: 20 mutants, 15 killed, 5
survived, **the same five names and the same killing node ids** as on 3.11.
Waves 1–5 were not re-run: they cover modules this increment did not touch.

Nothing else ran in the container during either sweep. The increment-4 tester's
recorded lapse — starting a second run before the first finished — was not
repeated.

---

## 6. Verdict on "structural, not filtered"

**Upheld for the property AC12 states. Not upheld as a description of the
mechanism.** The payload really does carry no trace text, on a corpus chosen to
make that hard; but "a request holding a sentinel is not one that gets sent and
cleaned — it is one that cannot be constructed" is false as written, and the one
place where the product's safety actually depends on it is a filter.

### The evidence

**First, a corpus the type would accept.** `tests/hostile_corpus.py`'s markers
are `MARKERtoolname`-shaped, and *every* string leaf of a narration request
refuses an uppercase letter. Run against those, the PR's own sentinel sweep asks
whether a payload of lowercase slugs contains an uppercase string — a question
whose answer does not depend on the code. So `tests/sentinel_trace.py` builds a
second corpus whose fourteen sentinels are all `^[a-z][a-z0-9_]{0,63}$`: the
alphabet the payload's validators **accept**. A sentinel from that corpus that
does not reach the payload was removed by the builder, not refused by the type.

All fourteen are present in the objects the payload is built from and none
reaches the payload:

```
Span.text_preview, Span.tool_input_preview, Span.tool_result_preview,
Span.model, Span.stop_reason, Span.tool_name, Span.tool_use_id,
Span.agent_id, SpanError.code, SpanError.detail, AgentRun.agent_type,
AgentRun.description, ParseWarning.detail, metrics.tool_name.credential
  present in the Trace and the findings : 14 of 14
  present in the serialized payloads    : 0 of 14
```

The last one is `ghp_` + 24 `a`s — a GitHub token that is entirely lowercase
letters, digits and an underscore, sitting in `metrics.tool_name` of a real
`repeated_tool_call` finding. `AUTHORED_METRIC_VALUE_PATTERN` admits it
perfectly.

**Second, every string leaf of the request tree, enumerated from `model_fields`
and attacked.** Fourteen leaves, a completeness arm that fails if a new one has
no attack, and a five-member battery per leaf. The result splits them in two:

| | Leaves | What guards them |
| --- | --- | --- |
| **Closed vocabulary** (10) | `request_version`, `group`, `groups[].group`, `groups[].title`, `groups[].detail[].severity`, both `severity_counts` key sets, and the three `cost_usd` fields | a membership test against a set this package computed, or a `Literal`, or R29's exact money format. A lowercase slug is refused. |
| **Shape check only** (4) | `rate_snapshot_version`, `totals.by_model[].model_key`, `metrics[].key`, `metrics[].value` | an alphabet. A lowercase slug is accepted at all four; `rate_snapshot_version` additionally accepts an **uppercase** one, because its pattern is `^[A-Za-z0-9._\-]{1,64}$` — which is the shape of a recorded `Span.model` and of an `agent_id`. |

So the type refuses trace free text — spaces, markup, length — everywhere. It
does **not** refuse a trace-derived string that happens to be slug-shaped, at
four of its fourteen leaves. What keeps those four clean is not the type:

* `rate_snapshot_version` and `model_key` are clean because `cli/main.py` passes `SnapshotMeta.version` and `CostReport.by_model[].model_key`, which are package data. That is a call-site discipline. It is a good one, and A-e4's decision to keep `cost` out of `narrate` makes it much harder to get wrong — but it is discipline.
* `metrics[].key` and `metrics[].value` are clean because of `summary._metrics`'s one `if key not in TRACE_DERIVED_METRIC_KEYS`. That is **a filter**, and `MetricEntry`'s docstring claims a second, type-level refusal that does not exist (**BUG-14**).

### So

* Layer 2 of the PR's argument — "the builder has almost nothing to leak", because R44 keeps `Trace`, `Span`, `SourceFile` and `CostReport` out of `narrate/`'s scope — **holds completely** and is the strongest thing in the design. It is why the residual surface is one object (`Finding`) rather than five.
* Layer 1 — "the payload's type has no field that can hold free text" — **holds for free text** and not for a slug-shaped trace-derived string. The PR's stronger sentence ("a request holding a sentinel … cannot be constructed") is contradicted by a two-line counter-example.
* AC12 passes, and passes against a corpus built to defeat it.

The honest one-line version: *the payload's type makes a leak of trace prose
impossible and a leak of a trace-derived slug merely unlikely, and the one
trace-derived slug that reaches this package is stopped by a single `if`.*

---

## 7. Spec flags

Continuing from the coder's **S36**.

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S37** | R49 / T18 | The owner's ruling on S35 says "marker for the live path, **ledger entry recording why**, and no `skipif`". `tests/allowed_skips.txt` is the *skip* allowlist; its own header says a deselection needs no entry, and `test_r49_the_suite_currently_allows_no_skips` asserts it is empty as data. An entry there would make a skip permissible — the opposite of R49's posture — so the "ledger entry" is implemented as a named **comment block** in that file recording the test, the reason and the mechanism, asserted by `test_r49_the_deselection_is_recorded_in_the_skip_ledger_as_a_comment`. **The ruling is recorded as provisional and is flagged for the owner.** | Either (a) confirm that a comment block is the ledger entry intended, or (b) add a separate `tests/declared_deselections.txt` with its own hook, so a deselection is recorded as data rather than as prose. (b) is better if a second marker ever exists; for one test it is ceremony. Also worth saying in R49 whether the env gate may be a branch inside the test — the reading taken here, and the one S35's own suggested resolution proposes. |
| **S38** | R42 | R42 permits the payload to carry "the rate snapshot version" and R44 keeps `cost` out of `narrate`, so the version arrives as a string from `cli/main.py` and is validated only by a shape: `^[A-Za-z0-9._\-]{1,64}$`. That alphabet is also the alphabet of a recorded `Span.model` (R30 calls it trace-derived) and of an `agent_id` (R5's `AGENT_ID_PATTERN` is a subset of it). The same is true of `by_model[].model_key`. Nothing trace-derived reaches either today, but R42's "contains **no** … " is a claim about the payload and it currently rests on the CLI's care. **§6.** | State in R42 that the snapshot version and the model key are drawn from the bundled `model_rates.json` and are validated against **the keys actually present in the loaded snapshot**, not against a pattern. That turns two of the four shape-checked leaves into closed vocabularies at no cost — the snapshot is already loaded in the process that builds the payload's figures — and it makes AC12's structural claim true of the type rather than of the caller. |
| **S39** | R42 / R16 | `MetricEntry`'s value alphabet is `[a-z][a-z0-9_]{0,63}`, which contains `ghp_…`, `xoxb-`-less Slack shapes and any lowercase secret. R42 relies on `TRACE_DERIVED_METRIC_KEYS` to keep the one trace-derived metric out; the *type* cannot tell an authored slug from a trace-derived one because they share an alphabet. **BUG-14.** | Add to R42 that the payload's metric entries are refused for any key in `detect.base.TRACE_DERIVED_METRIC_KEYS`, at the model, and not only dropped by the builder. One clause, and it makes the claim R42 is for into a property of the type. (This is the narrow half of the coder's own S33; S33 is about R42-versus-AC12 and this is about where the resolution lives.) |
| **S40** | R43 | R43's byte-identity clause names the element as `<section id="narrative">`. The renderer writes `<section class="section" id="narrative">`, because `_section` puts the class on every section. A test written to R43's literal text strips nothing and reports a byte difference, so the failure mode is red rather than green — but the requirement as written describes an element the product does not emit, and a reader cannot tell whether the class is forbidden. | Reword to "the `<section>` element whose `id` is `narrative`, and everything between it and its closing tag". Trivial, and it removes the one place where following the requirement literally produces a broken test. |

Still open from earlier increments, unchanged by this one: **S24–S36**. The two
the coder routed to the PM that this increment's testing bears on are **S33**
(where §6 and S39 say the resolution should land) and **S35** (ruled on
provisionally; see S37).

---

## 8. What I did not cover, and why

* **Any line of `AnthropicNarratorClient.complete` past the credential check.** `sdk.Anthropic(...)`, `client.messages.create(...)` and the seven `except sdk.<Error>` clauses need the `[explain]` extra and a key. The marked live test will execute them the first time somebody sets `SWARM_OBSERVER_LIVE_NARRATOR=1`; offline it asserts the preconditions instead. I did **not** stub the SDK deeply enough to drive the seven `except` clauses: a stub whose exception classes I invent would assert that my stub's hierarchy maps the way I wrote it, which is a check on the stub. The coder's own note is the right one — if the SDK renames an error class, `except sdk.X` raises `AttributeError` at except-evaluation time and `narrate`'s catch-all turns it into a `provider_error` fallback, so the failure mode is a deterministic paragraph rather than a crash. `test_r43_a_vendor_exception_becomes_a_provider_error` pins **that**, which is the part that is assertable.
* **`narrate/adapters/anthropic.py` is not in the mutation sweep.** Most of it cannot execute in CI, so every mutant would survive and the survivor list would be a statement about R45 rather than about the tests. Deliberate, and named here rather than left to be noticed.
* **A golden file for the `--explain` render.** I asserted the precondition a golden needs — three renders of one narration are byte-identical, and two offline `--explain` runs produce identical `report.json` — and then did not build one. A golden's value is catching an unintended byte change; the four structural assertions in `TestByteIdentityDependencies` catch the changes that would actually happen and say *which* one happened, which "any byte moved" does not. If the reviewer wants one, the precondition is checked in.
* **A client that hangs.** There is no timeout in `narrate/narrator.py` and there should not be — the bound belongs to the adapter (`DEFAULT_TIMEOUT_SECONDS`), and a test that spun a real clock would be the first wall-clock assertion in this suite's narrator path, which the mutation ledger's own hazard list says is the first test that can fail from noise. What is asserted instead: `narrate` makes exactly one call per group and never retries, so a hang is bounded by the adapter's timeout multiplied by the group count, and that multiplication is visible in the call-count assertions.
* **The HTML in a browser.** Inherited and still owed, now with a fourth item on the list: nobody has looked at the narrative section. `NARRATIVE_CAVEAT` reads well as a string and I cannot tell you whether it reads well on the page.
* **`--explain` against a trace with more than 20 findings in one group, end to end.** The cap is driven directly (`group_summary`, `build_totals`) but no CLI run exercises it, because no fixture is that large. The caps are A-e5's, not the spec's.
* **Cross-interpreter mutation parity for waves 1–5.** Not re-run; they cover modules this increment did not touch.

---

## 9. What I want the reviewer to look at hardest

1. **§6, and whether "structural" survives contact with `SHAPE_LEAVES`.** My verdict is "upheld for the property, not for the mechanism", and reasonable people could call that too harsh — AC12 does pass, over a corpus built to break it. What I would not soften is **BUG-14**: `MetricEntry`'s docstring promises a refusal that is not there, and the reviewer should decide whether the three-line fix lands now or whether S39 routes it to the PM first. The thing that makes this worth your time is that it is the *only* load-bearing filter in a design whose whole argument is that it has none.

2. **BUG-18 and whether instance eleven is the right count.** A zero-finding fixture behind a per-group assertion is the family's first shape, but the previous ten were each found by somebody looking for something else. This one was found by a coverage run over the modules the increment created, which took ninety seconds. If that is worth making routine, it belongs in the harness contract next to the tree digest — "every module an increment creates gets a coverage run before the sweep is designed" — and I did not put it there because it is a process rule and those are yours.

3. **BUG-15, and whether a forgeable visible marker is a bug or a note.** I called it medium. The counter-argument is that R43 only requires the marker to be *present* on a fallback, which it is; the argument for is that the class is invisible to a reader and the prefix is the whole of what they see, so an untrusted source can write the string that means "this came from the tool". The fix is one line in `validate_paragraph` and it costs a narrator nothing.

4. **The two control arms, and my thirteen own-goals.** Thirteen of twenty first-run survivors were gaps in tests I had written an hour earlier, and five of those thirteen were one gap: *nothing asserted what the deterministic paragraph said*. I would like the reviewer to check the fix rather than the finding — `test_r43_the_overall_template_says_what_the_numbers_are` pins a sentence character for character, which is brittle by design, and brittleness in a test that pins prose is a trade somebody else should agree with.

5. **The three cap constants, which were increment 4's `I-H02` again.** Every expectation was written in terms of the constant it was testing. That is the third increment in a row this has happened in, in three different modules, and the fix has been the same each time (pin the literal the requirement names). It may be worth a rule rather than a third fix.

6. **S37, and whether a comment block is a ledger.** The owner's ruling asked for a "ledger entry recording why" and the only ledger available is the skip allowlist, which must stay empty as data. I chose a comment plus a test that asserts the comment names the test. That is defensible and it is also the kind of thing that decays. If the reviewer thinks a deselection deserves a real data file, say so now — it is ten lines of hook.

7. **`tests/sentinel_trace.py` as a second corpus.** It duplicates some of `tests/hostile_corpus.py`'s purpose, and the project already has one hostile trace built at test time because the checked-in one was insufficient (BUG-8/S29). Two is a smell. My argument is in the module docstring: the alphabet is the whole point, and a single corpus cannot be both "every payload validator refuses this" and "every payload validator accepts this". If the reviewer disagrees, the merge is to add a slug-shaped marker set to `hostile_corpus.py` and delete mine.
