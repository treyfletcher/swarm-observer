# swarm-observer

Post-hoc observability for multi-agent AI runs. Point it at the transcripts a
multi-agent system already wrote, and get back a report that says what the
agents did, what went wrong, and what it cost — with no instrumentation, no
SDK, and no changes to the system being observed.

```bash
pip install -e .

swarm-observer analyze ~/.claude/projects/my-project/<session>/subagents/ \
  --out report.html --json report.json --fail-on warning
```

That command needs no credentials, opens no socket, and writes exactly the two
files it names. Open `report.html` in any browser, offline; forward it to a
colleague and it renders identically on their machine.

## Why

When a multi-agent run goes wrong, the evidence is already on disk — but it is
thousands of newline-delimited JSON records across a dozen files, written in an
undocumented format, with no notion of a span, a cost, or a failure. Answering
"which agent got stuck", "why did this run cost what it cost", or "did anything
retry-storm" means writing a throwaway script every time.

swarm-observer turns those transcripts into a normalized trace, runs
deterministic detectors over it, prices every model call, and renders a report.
It reads what is already there, so there is nothing to add to the system you
want to observe and no way for it to affect that system's behavior.

**Zero instrumentation is the product promise**, not a convenience: swarm-observer
never runs inside the observed system, never requires an SDK, never asks it to
emit anything, and cannot change how it behaves. The only thing it needs is a
directory of transcripts.

## The threat model, up front

Every prompt, tool argument, tool result and agent message in a trace was
produced by an agent that may itself have processed hostile input — a fetched
web page, a malicious repository, a poisoned document. swarm-observer renders
that content into an HTML file a human opens in a browser and forwards to
colleagues. **The trace is the attacker's channel and the report is the
payload's delivery vehicle.**

So every trace-derived byte is treated as hostile:

- **Escaped at one boundary.** There is exactly one `escape_html` definition in
  the package, applied to *every* string a renderer writes, trusted or not. A
  second definition, or a second character-replacement table anywhere, fails
  the suite.
- **Classified at one boundary.** Every string reaching a report passes through
  one function whose `kind` is keyword-only and exhaustive over a closed enum —
  `authored`, `free`, `identifier`, `narrator`. Omitting the classification is a
  type error, so a field cannot be added to a report without somebody saying
  what class of string it is. (This replaced a three-way per-call-site choice
  that leaked once per increment for four increments.)
- **Never in an executable or attribute context.** Trace text appears only in
  text nodes. The document contains exactly one `<script>` and one `<style>`,
  both constants whose SHA-256 are checked in, and every attribute value is
  drawn from an allowlist generated from the inputs. A `default-src 'none'` CSP
  meta is the first thing in `<head>`.
- **No external requests of any kind.** No URL of any scheme, no `<link>`,
  `<img>`, `<iframe>`, `@import`, font or icon. A beacon in a shared report
  would leak the *contents* of a trace, not just the fact of viewing it.

### Redaction is a courtesy, not a boundary

Trace-derived text is passed through a redactor that recognises AWS, Anthropic,
OpenAI, GitHub, Slack and Google key shapes, JWTs, bearer headers, PEM private
key blocks, and `NAME=value` assignments whose name contains a secret-ish word.
Matches become `[redacted:<label>]`.

**This reduces accidental exposure in a shared report. It cannot defeat an
adversary who controls the trace and wants a secret rendered.** The report says
so in its own header, and the spec says so too. The hard guarantee is
`--no-previews`.

### `--no-previews`, and the one thing it also changes

`--no-previews` omits trace free text entirely. The text is dropped **at
ingest**, not at render, so the hostile bytes do not exist in the process after
ingestion rather than existing and being trusted not to leak. What survives is
digests, counts, enumerated slugs and redacted join keys. This is the mode to
use when sharing a report outside the team.

> **Caveat, and it is not cosmetic: `--no-previews` changes which findings
> exist.** The `unresolved_tool_call` detector decides its `unknown_tool` reason
> by matching five phrases against a tool result's text — the one place in the
> product where a detector reads trace free text. With the text gone at ingest,
> that reason cannot fire, so a run that reports `critical=1` by default can
> report `critical=0` under the flag. The flag's findings are a **subset** of
> the default run's, never a superset. Read the report you are about to share
> *and* the default one, and share the first while acting on the second. A fix
> — computing that decision at ingest before the text is dropped — is queued
> with the PM as spec flag **S27**; until it lands, this paragraph is the
> warning.

Identifiers are a second, smaller caveat: `agent_id`, `parent_agent_id`,
`ParseWarning.detail` and `SpanError.code` are redacted in both modes but not
blanked, because blanking a join key collapses the spans table, the lane legend
and the per-agent cost table into one row each and merges distinct API errors.
They therefore still carry attacker-chosen bytes under the flag, inside a
constrained alphabet. Whether to render them as digests instead is an open
question with the PM.

## The normalized trace model

`swarm_observer/model/trace.py` is the contract between everything that
produces a trace and everything that consumes one, documented in
[`docs/TRACE-SCHEMA.md`](docs/TRACE-SCHEMA.md). Every model is a frozen
pydantic v2 model with `extra="forbid"`, and the module deliberately does more
than declare fields: orderings, length caps, and alphabets are expressed as
validators, so a mapper bug fails at construction time instead of surfacing
three layers later as a mis-rendered report. The model carries its own
`TRACE_SCHEMA_VERSION`, independent of any vendor format.

## Ingestion and the Claude Code adapter

`swarm_observer/ingest/` reads traces behind an adapter protocol; the only
adapter in v1 reads Claude Code JSONL transcripts. The reader is fail-closed by
construction: per-line, per-file, and total-record caps; an explicit JSON depth
bound rather than a reliance on the interpreter's recursion limit; symlink
escape rejection; a SHA-256 recorded per source file; and atomic writes. A
malformed or hostile input produces exit 2 and a single sanitized line on
stderr — never a traceback, and never a partially written output file. Because
the Claude Code format is undocumented and unversioned (six different `version`
strings appeared within a single session during grounding), an unrecognized
field or record type degrades to a counted parse warning rather than a failure.

The mapper's most important job is **stream-fragment collapse**. A transcript
writes one record per streamed content block, and every fragment of one API
response repeats that response's `usage` object — only the terminal fragment
carries final `output_tokens`. Summing per record therefore double-counts
badly: on the grounding corpus, 2,706 assistant records collapse to 1,678 real
model calls, and naive summation inflates cache-read tokens by **55.4%**
(826.9M → 532.2M). Getting this right is the reason the cost numbers mean
anything, so the collapsed and naive constants are both pinned in tests.

## Detectors

Seven deterministic detectors behind one protocol. The same trace produces the
same findings, byte for byte, with no model in the loop and no scoring
heuristics to tune. Each finding carries a severity, the spans it implicates,
and an attribution of the model calls wasted, so the cost engine can answer
"what did this problem cost" rather than only "what did the run cost".

| slug | default | what it fires on |
|---|---|---|
| `repeated_tool_call` | warning | the same tool called with byte-identical arguments 2+ times; `critical` at 4+ |
| `agent_loop` | warning | an agent's signature sequence repeating with period 1–8 three or more times; `critical` at 4+ |
| `retry_storm` | warning | 3+ error spans inside a 10-span window for one agent; `critical` at 5+ |
| `failed_tool_call` | info | a tool that returned an error at least once; `warning` at 2+ |
| `unresolved_tool_call` | warning | a tool call with no result, an orphan result, or a call to a tool that does not exist (`critical`) |
| `blocked_agent` | warning | an agent idle 60s+ with no other agent working through the gap; `critical` at 300s+ |
| `anomalous_span` | warning | a model call more than 6 MADs above the lower median in tokens or duration; `critical` above 12 |

`swarm-observer detectors` prints this list from the registry itself.
`--detector <slug>` restricts a run; `--blocked-gap-seconds N` is the only
threshold exposed as configuration, so a report is a function of the trace and
one integer.

`retry_storm` and `failed_tool_call` can both fire on the same spans, as can
`agent_loop` and `repeated_tool_call`. The overlap is deliberate and is not
deduplicated: a density signal and an existence signal are different
information.

## Cost engine

`swarm_observer/cost/` prices every model call from a **bundled rate snapshot**
rather than a live pricing API. Rates resolve through a pinned four-rung ladder
(exact key, alias, longest prefix, then unpriced), and the snapshot's loader
refuses a rate that carries no provenance — every model key names its source
and an as-of date. All money is `Decimal` end to end, with an `Inexact` trap
that turns a silent rounding error into a loud failure. Anything unpriceable
lands in an explicit taxonomy (`model_not_in_snapshot`, `rate_key_missing`,
`usage_missing`, `synthetic_span`) applied in a pinned priority order — nothing
is ever silently dropped or quietly zeroed, and the count of unpriced calls
sits beside the grand total so a trace on a retired model cannot look cheap.

**Costs are list-price estimates at the snapshot's date**, which both reports
print next to every dollar figure. They exclude batch and priority tiers and
any negotiated discount, they are not a billing reconciliation, and a trace is
a *historical* artefact, so today's list price is already the wrong number for
last month's run. The snapshot is a bundled file so that a report is
reproducible and needs no network; the trade is that it goes stale, visibly,
with a date attached. A live or historical-rate source in v2 is a new
implementation of the existing `RateSource` protocol with no change to the cost
engine.

Attributed waste is an **attribution, not a counterfactual**: it sums the usage
of model calls the trace shows were repeated or discarded. It does not claim a
deduplicated run would have cost that much less — a deduplicated run would have
had different cache behaviour. Both reports say so in one sentence next to the
number.

## Reporting

Two artefacts, from one command:

- **`--json report.json`** — every span, every finding, all four cost
  groupings, the unpriced list and the full parse-warning list, with
  `sort_keys`, `ensure_ascii` and six-decimal money strings.
- **`--out report.html`** — one self-contained file. All CSS and JS inline, no
  build step, no server, no external request. Sections in a fixed order:
  header, narrative (only with `--explain`), findings, timeline, cost, spans,
  warnings. The timeline is inline SVG with integer-only geometry.

The HTML report's only interactivity is severity filtering and per-section
collapse, from one inline script with no dependencies. Both are exercised in a
real browser in CI (see `tests_browser/` below), which also re-asserts there —
on the live DOM, after every control has been clicked — that the document still
holds exactly one `<script>` and one `<style>`, no injected element, and no
attribute outside the allowlist.

At least one of the two is required. A run that names neither is a usage error,
because a run that reports success without writing the file it was asked for is
the failure the exit codes exist to prevent.

## `--explain`: the optional narrator

Off by default. With it on, swarm-observer asks a language model for one
paragraph per finding group plus one overall paragraph, and renders them in a
`<section id="narrative">` between the header and the findings.

```bash
pip install -e ".[explain]"
export ANTHROPIC_API_KEY=...
swarm-observer analyze <paths> --out report.html --explain
```

Four things about it are worth more than the feature:

- **It is never shown the trace.** The request payload is built from findings
  only and contains detector slugs, severities, integer metrics, per-severity
  counts, per-agent and per-model token and cost totals, and the rate snapshot
  version. No previews, no span text, no tool arguments, no tool results, no
  file names, no paths, no `trace_id`, no agent ids, no recorded model ids, and
  not even the one trace-derived metric the findings table is allowed to show.
  That is enforced by the payload's **type**: every string in it is a literal, a
  member of a closed vocabulary this package computed, or a pattern-constrained
  money or key string, so a trace byte is not filtered out of the request — it
  cannot be put in one. Pointing `--explain` at a provider therefore cannot
  forward a secret that was sitting in a tool result.
- **It cannot fail your run.** Each group falls back independently to a
  deterministic template paragraph, marked with the class `narrative-fallback`
  and the visible prefix `Deterministic summary:`. A transport failure, an auth
  failure, a missing `anthropic` package, an absent API key, an invalid
  paragraph and a timeout all take that path. **The exit code never changes**,
  and `--explain` never turns a 0 into a 1.
- **It is purely additive.** Strip `<section id="narrative">…</section>` from an
  `--explain` report and the remaining bytes are identical to the same report
  built without the flag. Nothing else in the document moves.
- **Its output is untrusted.** A compromised or prompt-injected narrator is
  just another attacker-influenced string source, so its paragraphs are
  length-validated, rejected outright if they carry control characters, then
  redacted and escaped exactly like trace text and confined to text nodes.

With `--explain` absent, `swarm_observer.narrate` is never imported at all, so
the default path cannot open a socket — not because nothing calls one, but
because the code that could is never loaded.

## CLI

`swarm_observer/cli/main.py` is the only module that wires adapters, detectors,
the cost engine, the narrator and the renderers together — everything below it
is independently importable. Exit codes are pinned and mean one thing each:

| code | meaning |
|---|---|
| `0` | ran, and no finding met `--fail-on` |
| `1` | ran, and something did; both reports are still written |
| `2` | fail-closed on input, parsing or rendering; one sanitized line on stderr, no output file written or truncated |
| `3` | usage error — a bad flag, an unknown detector slug, an unwritable output directory |

That makes it usable as a CI gate: `--fail-on warning` turns a retry storm into
a red build. `schema` prints the trace model's JSON Schema, and `detectors`
prints every detector's slug, severity and description.

## Design commitments

- **Offline and credential-free.** The whole suite runs with no network, no API
  keys and `anthropic` not installed. CI scrubs the credential environment to
  keep it that way, and the `[explain]` extra is never required.
- **Deterministic.** Byte-identical output across repeat runs, subprocess
  boundaries, `PYTHONHASHSEED`, `TZ`, `LC_ALL`, working directory, input path
  order, and both supported interpreters. No clock, no float, no absolute path,
  no username and no unsorted iteration reaches a report; the provenance line
  names the **trace's own** last timestamp, never the current time.
- **Fail-closed.** Bad input is exit 2 with one sanitized line, never a
  traceback and never a half-written file.
- **Untrusted input as the threat model.** Every trace-derived value is treated
  as hostile until it has passed through the model's constraints, the
  classifier and the redactor.
- **Python 3.11 and 3.12.** CI runs both. Interpreter-dependent behavior —
  recursion limits, Unicode table versions, float formatting, dict ordering —
  is treated as a defect, because it has been one here before.

## Not in v1

Deliberately deferred, and the seams are written down so each is an addition
rather than a redesign:

- **Replay.** `swarm_observer/replay/target.py` declares a `ReplayTarget`
  protocol and two frozen models, with no implementation, no subcommand and no
  importer.
- **Other trace formats.** `TraceSource` is the seam; an OTel or SDK source in
  v2 is a new package under `ingest/` plus a registry entry.
- **Live or historical pricing**, currency conversion, negotiated rates.
  `RateSource` is the seam.
- Live capture, an SDK, an exporter, or any in-process hook. Zero
  instrumentation is the promise.
- A server, a web UI, live tailing, `--watch`, or streaming analysis.
- Storage, an index, or any state between runs. Nothing is written except the
  two named files.
- Cross-trace analysis: comparing runs, trend lines, baselines, "cost over the
  last 30 days".
- Finding suppression and `.swarm-observer-ignore`. `finding_id` is stable so
  v2 can add them.
- Statistical or LLM-based *detection*. Every detector is deterministic, and
  `--explain` narrates findings — it never creates, ranks, suppresses or
  modifies one.
- PDF/Markdown/CSV output, theming, or configurable section order.

## Development

```bash
pip install -e ".[dev]"
pytest                  # 2921 tests, offline, no credentials, ~80s
ruff check . && ruff format --check .
mypy                    # strict
```

The suite must pass with every provider credential unset and with `anthropic`
not installed. If it passes only because something was installed, it is not
testing what it claims to.

### The second tree: `tests_browser/`

`tests/` parses the report; it never executes it. That gap shipped a real
defect — the six section-collapse controls hid their own heading and button
instead of the section's content, and the pinned SHA-256 over the inline script
made the area *look* covered while proving only that the bytes had not changed.

So the report's behaviour is tested in a real engine:

```bash
pip install playwright && playwright install chromium
pytest tests_browser    # 23 tests, Chromium, nothing deselected
```

It is a separate top-level tree rather than a marker for two reasons that
already existed: `tests/` must pass with only `.[dev]` installed, which a
module-scope `import playwright` would break, and the suite-integrity rules
forbid `importorskip` and `skipif`. It runs as **its own CI job** that installs
Chromium and deselects nothing — a browser test CI skips would be exactly the
kind of never-executed path this project keeps finding.
`tests/test_browser_suite_wiring.py` runs in the offline job and fails if that
tree is emptied, if the job is removed, or if either grows a filter.

## Status

| Increment | Scope | State |
|---|---|---|
| 1 | Trace model, fail-closed ingestion, Claude Code adapter, suite-integrity harness | shipped |
| 2 | Detectors and the fixture corpus | shipped |
| 3 | Cost accounting and the JSON report | shipped |
| 4 | Self-contained HTML report, SVG timeline, security and determinism probes | shipped |
| 5 | LLM narrator, replay seam, docs | shipped |

**v1 is feature-complete**: 2921 offline tests plus 23 browser tests, green on
Python 3.11 and 3.12, with `ruff`, `ruff format --check` and `mypy --strict`
clean. Known-open items are recorded in the increment reviews under `docs/` —
chiefly the queued spec amendments (S14–S40), the `--no-previews` finding-subset
caveat above, and the fact that the bundled rate snapshot has not been reviewed
by a human: the arithmetic over it is exhaustively tested, the numbers in it are
not.

## How this was built

swarm-observer was built by a four-agent development pipeline — a PM agent that
writes the spec, a coder, a tester, and a PR reviewer — with the role
boundaries of a real engineering team. The coder cannot write tests. The tester
cannot patch application code; a failing test becomes a bug report. The
reviewer sees only the PR diff, and every fix it commits must land with a test
that would have caught the bug. Specs, test reports, and reviews for every
increment are preserved in [`docs/`](docs/) as the audit trail, and
[`docs/CASE-STUDY.md`](docs/CASE-STUDY.md) is the account of what the pipeline
found and what it cost.

The pipeline's most useful recurring finding is worth stating plainly: its
characteristic defect is **a check that reports green while being structurally
unable to fail** — a fixture set with no case that could trip it, an undeclared
test dependency silently collecting zero tests, a guard that depends on an
interpreter's recursion limit, a self-reported mutation score against a
self-chosen mutation set, a hostile corpus that loaded four of the rendered
fields as empty, a credential sweep run against a fixture whose findings array
was empty. **Thirteen instances** have been caught so far, every one of them by
a different agent than the one that wrote it.

Several of the harnesses here exist specifically to make that class visible: a
collection floor, a declared-skip ledger, a spec traceability map, a checked-in
mutation ledger with a declared operator set and a control arm that *must*
survive, an AST scan for assertions no input can falsify, a model-derived sweep
over every string field a report can render, and canaries that prove each check
can still fail.

The thirteenth instance is the one that generalises. It was found by opening the
report in a browser — something no test did — and the lesson was not that a
check was weak but that a whole *kind* of check was missing while a SHA-256 pin
made the area look covered. `tests_browser/` is the answer to that one, and the
open question it leaves behind is what the next missing kind is.

## License

MIT.
