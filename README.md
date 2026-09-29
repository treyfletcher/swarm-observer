# swarm-observer

Post-hoc observability for multi-agent AI runs. Point it at the transcripts a
multi-agent system already wrote, and get back a report that says what the
agents did, what went wrong, and what it cost — with no instrumentation, no
SDK, and no changes to the system being observed.

```
swarm-observer analyze ~/.claude/projects/my-project/<session>/subagents/ \
  --json report.json --fail-on warning
```

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

## The normalized trace model

`swarm_observer/model/trace.py` is the contract between everything that
produces a trace and everything that consumes one. Every model is a frozen
pydantic v2 model with `extra="forbid"`, and the module deliberately does more
than declare fields: orderings, length caps, and alphabets are expressed as
validators, so a mapper bug fails at construction time instead of surfacing
three layers later as a mis-rendered report. The safety properties the rest of
the system relies on are enforced here and only here — span and agent ids are
constrained to a closed alphabet, so they can never embed trace content and are
therefore safe in an HTML attribute; parse-warning details are restricted to
enumerated slugs, so a warning can never smuggle free text into output. The
model carries its own `TRACE_SCHEMA_VERSION`, independent of any vendor format.

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

`swarm_observer/detect/` holds seven deterministic detectors behind a common
protocol — repeated tool calls, agent loops, retry storms, failed and
unresolved tool calls, blocked agents, and anomalous model calls. They are
deterministic on purpose: the same trace produces the same findings, byte for
byte, with no model in the loop and no scoring heuristics to tune. Each finding
carries a severity, the spans it implicates, and an attribution of the model
calls wasted, so the cost engine can answer "what did this problem cost" rather
than only "what did the run cost". Detectors are registered rather than
hard-wired, and `--detector <slug>` restricts a run to one.

## Cost engine

`swarm_observer/cost/` prices every model call from a bundled rate snapshot
rather than a live pricing API, so a run is reproducible and needs no network.
Rates resolve through a pinned four-rung ladder (exact key, alias, longest
prefix, then unpriced), and the snapshot's loader refuses to accept a rate that
carries no provenance — every one of the 17 model keys names its source and an
as-of date. All money is `Decimal` end to end, with an `Inexact` trap that
turns a silent rounding error into a loud failure. Anything unpriceable lands
in an explicit taxonomy (`model_not_in_snapshot`, `rate_key_missing`, and
friends) applied in a pinned priority order — nothing is ever silently dropped
or quietly zeroed.

## Reporting and redaction

`swarm_observer/report/` renders the machine-readable JSON report today; the
self-contained HTML report is the next increment. Between the trace and the
report sits the redaction layer, which exists because trace content is
attacker-influenced by definition — an agent may have processed hostile input,
and whatever it saw is now in the transcript you are rendering. Redaction runs
over every trace-derived field that reaches output, not only the obvious
free-text previews, because the bugs found in this repo were precisely the ones
where a credential-shaped string arrived through a field nobody had classified
as free text. `--no-previews` drops trace free text at ingest rather than at
render, so the text never enters the pipeline at all.

## CLI

`swarm_observer/cli/main.py` is the only module that wires adapters,
detectors, the cost engine, and the renderers together — everything below it is
independently importable. Exit codes are pinned and mean one thing each: `0`
ran clean, `1` ran clean but a finding met the `--fail-on` threshold, `2`
fail-closed on bad input, `3` usage error. That makes it usable as a CI gate:
`--fail-on warning` turns a retry storm into a red build. `schema` prints the
trace model's JSON Schema, and `detectors` prints every detector's slug,
severity, and description.

## Design commitments

- **Offline and credential-free.** The whole suite runs with no network and no
  API keys. CI scrubs the credential environment to keep it that way.
- **Deterministic.** Byte-identical output across repeat runs, subprocess
  boundaries, `PYTHONHASHSEED`, `TZ`, `LC_ALL`, working directory, input path
  order, and both supported interpreters.
- **Fail-closed.** Bad input is exit 2 with one sanitized line, never a
  traceback and never a half-written file.
- **Untrusted input as the threat model.** Every trace-derived value is treated
  as hostile until it has passed through the model's constraints and the
  redactor.
- **Python 3.11 and 3.12.** CI runs both. Interpreter-dependent behavior —
  recursion limits, Unicode table versions, float formatting, dict ordering —
  is treated as a defect, because it has been one here before.

## Development

```bash
pip install -e ".[dev]"
pytest                  # 2093 tests, offline, ~35s
ruff check . && ruff format --check .
mypy                    # strict
```

## Status

| Increment | Scope | State |
|---|---|---|
| 1 | Trace model, fail-closed ingestion, Claude Code adapter, suite-integrity harness | shipped |
| 2 | Detectors and the fixture corpus | shipped |
| 3 | Cost accounting and the JSON report | shipped |
| 4 | Self-contained HTML report, SVG timeline, security and determinism probes | in progress |
| 5 | LLM narrator, replay seam, docs | planned |

## How this was built

swarm-observer was built by a four-agent development pipeline — a PM agent that
writes the spec, a coder, a tester, and a PR reviewer — with the role
boundaries of a real engineering team. The coder cannot write tests. The tester
cannot patch application code; a failing test becomes a bug report. The
reviewer sees only the PR diff, and every fix it commits must land with a test
that would have caught the bug. Specs, test reports, and reviews for every
increment are preserved in [`docs/`](docs/) as the audit trail.

The pipeline's most useful recurring finding is worth stating plainly: its
characteristic defect is **a check that reports green while being structurally
unable to fail** — a fixture set with no case that could trip it, an undeclared
test dependency silently collecting zero tests, a guard that depends on an
interpreter's recursion limit, a self-reported mutation score against a
self-chosen mutation set. Eight instances have been caught so far. Several of
the harnesses in `tests/` exist specifically to make that class of failure
visible: a collection floor, a declared-skip ledger, a spec traceability map, a
checked-in mutation ledger with a declared operator set, and canaries that
prove each check can still fail.

## License

MIT.
