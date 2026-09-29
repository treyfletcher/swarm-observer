# PR: swarm-observer increment 1 — scaffold, model, reader, adapter, suite harness

Branch: `feature/so-i1` → `main`
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED)
Tasks: T1–T5. Requirements in scope: R1–R12, R44, R45, R49, R50 (partial), R52,
plus R38's `schema` subcommand.

## Summary

The bottom half of the product: the normalized trace model and its schema
document, the fail-closed JSONL reader, the Claude Code adapter with R9's
stream-fragment collapse, and the suite-integrity harness that will police
increments 2–5. **No** detectors, cost engine, report rendering, `analyze`
pipeline, narrator or replay seam — those are increments 2–5, and the import
rules that will govern them are already asserted by the R44 test so they arrive
under supervision rather than after it.

Five commits, one per task. `ruff check`, `ruff format --check`, `mypy --strict`
and `pytest` are all clean; the suite is 57 tests, all of them infrastructure.

## Requirements coverage

| Req | Where | Notes |
| --- | --- | --- |
| R1 | `swarm_observer/model/trace.py` — `TRACE_SCHEMA_VERSION = "1.0.0"`, `trace_json_schema`, `schema_document` | The normalized model is the only type outside `ingest/` that names anything; no adapter field name appears outside `ingest/claude_code/records.py`, asserted by the R44 test. |
| R2 | `model/trace.py` — `TokenUsage`, `SpanKind`, `ToolResultStatus`, `SpanError`, `Span`, `AgentRun`, `SourceFile`, `ParseWarning`, `Trace` | Exactly the pinned shape, all frozen + `extra="forbid"`. R2's prose properties are enforced in code: tz-aware UTC timestamps, basename-only source names, 16-hex ids, the 200-char caps, the closed warning enum, and a `Trace` validator asserting the schema version and every "sorted by" ordering. |
| R3 | `ingest/source.py` — `TraceSource` Protocol, `IngestLimits`; `ingest/registry.py` — `ADAPTERS`, `build_adapter` | One-method protocol, normalized types both sides. `ADAPTERS` is R3's literal `dict[str, TraceSource]`; `build_adapter()` constructs a per-run instance for `--no-previews` (A-a10). |
| R4 | `ingest/claude_code/records.py` — `KNOWN_IGNORED_KEYS`, `RawRecord` et al., `parse_record`; `mapper._count_extras` | Tolerated half: `extra="allow"` plus before-validators that coerce a wrong-typed value to its neutral default, so nothing in the tolerated half can raise. Fatal half: checked before pydantic sees the payload, so every fatal condition raises a sanitized `TraceParseError` naming a *field* rather than a `ValidationError` quoting trace bytes. |
| R5 | `ingest/reader.py` — `compute_trace_id`, `digest_id`; `mapper.safe_agent_id`, `mapper._finalize` | `trace_id` over `name:sha256` lines sorted by basename; `span_id` over `trace_id\|agent_id\|seq\|kind`. `agent_id` is constrained to an id alphabet at the model, with a digest fallback, so R5's "ids never embed trace content" is true by construction (A-a4). |
| R6 | `reader.resolve_inputs`, `reader.load_input`, `mapper._parse_all`, `mapper.build` | Order is `(source_file_index, line_number)`; timestamps never order anything. Out-of-order timestamps are counted **per file** — cross-file order is basename order by design (A6), so comparing across a file boundary would report the design as a defect on every multi-agent trace (A-a23). |
| R7 | `ingest/text.canonical_json`, `mapper.tool_input_digest` | 16 hex over `json.dumps(..., sort_keys, ensure_ascii, separators=(",",":"))`; absent input digests the literal `"null"`. |
| R8 | `ingest/text.preview` | The single definition. Non-printable → space, whitespace collapsed, stripped, truncated at 240 code points with `…`. `--no-previews` blanks the three fields at model-build time (A10) and also the agent description/type (A-a5). |
| **R9** | `mapper._group_assistants`, `mapper._model_call`, `mapper._block_identity`, `mapper.collapse_stats` | Group key `(sessionId, agentId, message.id, requestId)`; no-requestId-and-no-message.id records are their own group; blocks unioned first-appearance and deduped by `id` / `tool_use_id` / `(type, sha256(text))`; usage, `stop_reason` and `model` from the **last** fragment, never summed; start/end from first/last. `collapse_stats()` returns naive **and** collapsed totals so the regression fails loudly. Measured constants below. |
| R10 | `model/trace.ParseWarningCode`, `mapper.WarningLog`, every `warnings.add` call | All twelve codes are produced by the mapper. `detail` is constrained to an enumerated alphabet at the model, so trace free text cannot reach it even by mistake (A-a2, A-a14). |
| R11 | `ingest/source.py` — `IngestLimits`, `TraceError`/`TraceReadError`/`TraceLimitError`/`TraceParseError`; `reader._iter_lines`, `reader.read_jsonl`, `reader.resolve_inputs`, `reader.atomic_write_texts`; `cli/main.main` | All four caps, with the line cap enforced *while* buffering. Error messages are assembled from typed fields (basename, line, limit, enumerated note) so content cannot reach stderr. One line, no traceback, exit 2, nothing written. |
| R12 | `mapper._model_call`, `mapper._user_message`, `mapper._system_event`, `mapper.build` | One `model_call` per collapsed group; one `tool_call` per `tool_use` with parent and result status resolved trace-wide; `user_message` only when content is a string or has a non-`tool_result` block; `system_event` per system record with `compact_boundary`; attachments counted, no span; API-error records get `usage=None` and a slugged `SpanError`. |
| R38 (part) | `cli/main.py` — `build_parser`, `run` | `schema` only. Byte-identical output across repeat runs, `PYTHONHASHSEED` ∈ {0,1,random}, `TZ` and `LC_ALL` (verified in nine subprocesses). `analyze`/`detectors` land with their pipelines (A-a19). |
| R39 (part) | `cli/main.py` — `EXIT_OK/FINDINGS/FAIL_CLOSED/USAGE`, `main` | The fail-closed handler is a property of the entry point, so every later subcommand inherits it. |
| R44 | `tests/test_boundaries_and_posture.py` | AST-walks the package. `ALLOWED_IMPORTS` names subpackages that do not exist yet, so their rules are in force from their first commit. Also: one `escape_html`, no second replacement table, detector purity, no floats in `cost/`/`report/`, `anthropic` confined, nothing imports `cli` or `replay`. |
| R45 | `tests/conftest._no_credentials`, `tests/test_boundaries_and_posture.TestOfflinePostureR45`, `.github/workflows/ci.yml` | CI installs only `.[dev]`, asserts `anthropic` is not importable, unsets all nine variables, deselects `live_narrator`. |
| R49 | `tests/conftest.py` hooks, `tests/collection_floor.json`, `tests/allowed_skips.txt`, `tests/test_suite_integrity.py` | Counting happens before `-m`/`-k` deselection. Both hooks' decision logic is factored out and exercised against inputs that must trip it. |
| R50 (part) | `tests/canaries/` — `test_canary_golden_byte_flip.py`, `test_canary_determinism_harness.py`; ledger in `tests/test_suite_integrity.REQUIRED_CANARIES` | The two canaries whose subjects exist. The other five are ledgered against the increment that creates each subject, and the ledger test fails if one is missing once its increment is current. |
| R52 | `tests/test_traceability.py`, `tests/traceability_pending.txt` | Set equality both ways, with a shrinking debt ledger for the 47 requirements whose increments have not landed (A-a12). |
| T1 | `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml` | hatchling; `pydantic >=2.7,<3` as the only runtime dependency; `[explain]` → `anthropic` declared and imported nowhere; dev = pytest/pytest-cov/ruff/mypy; `live_narrator` registered. |

## The R9 constants (the point of this increment)

Measured by `ClaudeCodeSource().collapse_stats()` on
`tests/fixtures/mapper/stream_fragments.jsonl` — a hand-built fixture
reproducing AC1's shape: one API response written as four streamed fragments
sharing `requestId` and `message.id`, the terminal one reporting
`output_tokens: 483` and the earlier three `6`, plus three single-fragment
responses so the record-to-call ratio (9 → 6) is in the same family as the real
corpus's 2,706 → 1,678 rather than being all-fragments.

| component | naive (per-record sum) | collapsed (correct) | inflation |
| --- | ---: | ---: | ---: |
| `input_tokens` | 68 | 38 | +78.9% |
| `output_tokens` | 585 | 567 | +3.2% |
| `cache_read_input_tokens` | 836,844 | 482,211 | **+73.5%** |
| `cache_creation_5m_tokens` | 72,334 | 18,421 | +292.7% |
| `cache_creation_1h_tokens` | 900 | 900 | 0% |

assistant records: **9** → model calls: **6** (both counts include the API-error
record and its group; both token columns exclude API-error records, which are
never billable — the exclusion is symmetric, so the ratio is a property of the
collapse).

**Tester: pin both columns.** A test that pins only the collapsed numbers passes
just as happily against a naive implementation that happens to have been fed a
single-fragment fixture. The two-column pin is what makes a regression loud.

Also verified on the fixture: exactly one `model_call` for the four-fragment
group, `usage.output_tokens == 483`, `cache_read_input_tokens == 118_211` (the
per-fragment value, *not* 4×), `stop_reason == "tool_use"` from the terminal
fragment, the repeated text block deduplicated to one preview, and both
`tool_use` blocks emitted as `tool_call` spans with `ok` / `error` results.

## Assumptions and judgement calls (A-a series)

Anything a reviewer might reasonably have decided differently.

- **A-a1 (`extras_dropped` scope)**: R4 defines it as "unknown top-level keys on
  a record", so it counts record-level unknown keys only. Unknown keys nested on
  `message`/`usage` are still counted as `unknown_extra_key` warnings — they are
  visible — but they do not inflate a span-level count that means something else.
- **A-a2 (`unknown_extra_key` detail is empty)**: the key *name* is
  attacker-chosen, and R10 forbids trace free text in a `detail`. Details for
  `unknown_record_type` and `unknown_content_block` carry a **slugged** type
  (lowercased, non-slug characters → `_`, capped at 40) because R4 explicitly
  asks for the type slug there.
- **A-a3 (attachments)**: R12 says attachment records "produce no span and are
  counted", but R10's closed enum has no attachment code. They are counted as
  `known_ignored_key` with detail `attachment` — the enum's only "we saw this and
  deliberately ignored it" slot. **Flagged for review**: if you would rather add
  an `attachment` code, that is an R10 amendment, not a code change here.
- **A-a4 (agent id alphabet)**: R5 claims ids never embed trace content, but
  `agent_id` comes from the record's `agentId`. Non-conforming ids are replaced
  by `agent_<12 hex of sha256(recorded)>`, which keeps the only property that
  matters (same agent → same id) and makes the safety claim literal.
- **A-a5 (`--no-previews` also blanks the agent description/type)**: R8 names
  the three preview fields, but R51's hostile corpus puts payloads in *agent
  descriptions* and the `--no-previews` arm of that probe asserts no payload
  appears anywhere in the output. Blanking three fields and leaving the fourth
  would fail that probe in increment 4.
- **A-a6 (tool call timing)**: a `tool_call` span starts when the emitting
  response ended and ends at the matching `tool_result` record's timestamp
  (`None` when there is no result). Borrowing the model call's window would
  report the model's duration as the tool's, which R23 and R24 both read as a
  timing signal.
- **A-a7 (symlink rule)**: R11 says a symlink pointing "outside the set of named
  inputs" is fatal. Implemented as: a symlink is accepted only when it resolves
  to another **named non-symlink** input. Resolving the symlinks themselves into
  the permitted set would make the check vacuous.
- **A-a8 (duplicate basenames are fatal)**: two `agent-1.jsonl` files from
  different directories would collide in `SourceFile` (R2) and make `trace_id`
  ambiguous (R5), so it raises `duplicate_input` rather than merging.
- **A-a9 (`ingest/text.py`)**: one module added to the Modularity notes' layout,
  holding the single definitions of `preview`, `slug` and `canonical_json`. Two
  adapters must share one normalization, and the R44 test can then assert there
  is no second character-replacement table.
- **A-a10 (registry shape)**: `ADAPTERS` is R3's literal `dict[str, TraceSource]`
  of default instances; `build_adapter(slug, no_previews=...)` constructs a
  configured instance for a run. An adapter instance is configuration, not
  state, so two runs never share flags.
- **A-a11 (TraceError taxonomy)**: split into `TraceReadError`,
  `TraceLimitError` and `TraceParseError`, each owning a closed set of codes;
  constructing one with a code it does not own raises immediately (this caught a
  real mis-classification during T3). Three codes beyond the spec's prose:
  `invalid_encoding` (a line that is not UTF-8), `duplicate_input` (A-a8) and
  `empty_input`.
- **A-a12 (traceability ledger)**: `tests/traceability_pending.txt` lists the 47
  requirements no test cites yet. Without it R52 is red from the first commit of
  a five-increment project, and a check that is red by design is a check people
  learn to ignore. The ledger can only shrink: the test fails if a listed id is
  in fact cited, or does not exist in the spec.
- **A-a13 (`seq`/`agent_index` contiguity)**: the `Trace` validator requires
  0-based contiguous `seq` and `agent_index`, which is stronger than R2's
  "sorted by". It makes `trace.spans[seq]` valid indexing for the detectors and
  turns an emission bug into a construction error.
- **A-a14 (`ParseWarning.detail` alphabet)**: constrained by pattern at the
  model, so R10's "never trace free text" is enforced rather than reviewed.
- **A-a15 (schema document shape)**: `swarm-observer schema` prints one JSON
  object `{"schema_version", "title", "schema"}` rather than two loose
  artifacts, so the output is machine-readable as well as printable.
- **A-a16 (`docs/` excluded from ruff)**: recent ruff versions reformat Python
  blocks inside Markdown, and `ruff format --check .` (which R45 pins into CI)
  would otherwise demand a rewrite of the ratified spec.
- **A-a17 (sidecars)**: A1's `agent-<id>.meta.json` is read opportunistically
  for `agent_type`, `description`, `depth` and the spawning `toolUseId`; every
  failure mode is "no metadata". **Flagged**: the sidecar's bytes are *not* in
  `trace_id` (R5 hashes the named inputs only), so editing a sidecar changes a
  report without changing the trace id. `ClaudeCodeSource(read_sidecars=False)`
  turns it off. If that bothers the reviewer, the alternative is to drop sidecar
  reading and leave `agent_type`/`description`/`depth` permanently null.
- **A-a18 (`model`, `stop_reason`, `tool_name` survive `--no-previews`)**: the
  cost engine and detectors need them, and R16 constrains `tool_name` at
  finding-construction time. **Flagged for increment 4**: if R51's
  `--no-previews` arm trips on a hostile *model id*, the fix is a pattern
  constraint on `Span.model` at ingest, not a render-time patch.
- **A-a19 (`analyze`/`detectors` not registered)**: R38 pins three subcommands;
  registering two that error would be worse than their absence. They arrive with
  the pipelines they drive (T13).
- **A-a20 (mypy scope)**: `files = ["swarm_observer"]`, matching spend-sentinel
  and draftsmith. Tests are ruff-checked and fully annotated but not
  type-checked.
- **A-a21 (model-call preview fallback)**: a response with no `text` block falls
  back to its `thinking` text, so a call that clearly said something does not
  render as an empty row.
- **A-a22 (`SourceFile.bytes`)**: the file's `stat()` size, and the SHA-256 is
  taken over the chunks as read, so the hash is the file's own hash rather than
  a reconstruction that would differ on `\r\n` or a missing trailing newline.

## Tester surface

**Public API to drive.**

```python
from swarm_observer.model.trace import (
    TRACE_SCHEMA_VERSION, PARSE_WARNING_CODES, Trace, Span, AgentRun,
    TokenUsage, SourceFile, ParseWarning, SpanError, schema_document,
    trace_json_schema,
)
from swarm_observer.ingest.source import (
    IngestLimits, TraceError, TraceReadError, TraceLimitError, TraceParseError,
    TRACE_ERROR_CODES,
)
from swarm_observer.ingest.reader import (
    load_input, read_jsonl, resolve_inputs, compute_trace_id, digest_id,
    atomic_write_text, atomic_write_texts, JsonRecord, LoadedInput,
)
from swarm_observer.ingest.text import preview, slug, canonical_json
from swarm_observer.ingest.registry import ADAPTERS, build_adapter, adapter_slugs
from swarm_observer.cli.main import main, run, build_parser, EXIT_OK, EXIT_FAIL_CLOSED
# adapter internals — reachable from tests, but note R44 forbids the *package*
# importing these from outside ingest/:
from swarm_observer.ingest.claude_code.mapper import (
    ClaudeCodeSource, CollapseStats, safe_agent_id, tool_input_digest, ADAPTER_SLUG,
)
from swarm_observer.ingest.claude_code.records import (
    KNOWN_IGNORED_KEYS, KNOWN_RECORD_TYPES, KNOWN_BLOCK_TYPES, parse_record,
)
```

**What to inject.** Nothing is mocked and nothing needs to be: everything is a
pure function of files on disk plus `IngestLimits`.

- Vary limits with `IngestLimits(max_line_bytes=..., ...)` rather than writing
  256 MiB fixtures.
- `ClaudeCodeSource(no_previews=True)` for A10; `read_sidecars=False` to make a
  test independent of any `.meta.json` next to a fixture.
- `ClaudeCodeSource().collapse_stats(paths, limits)` is the R9 seam; pin
  `naive_usage` **and** `collapsed_usage`.
- The CLI's fail-closed line is `TraceError.cli_line`; `main()` returns the exit
  code and writes exactly one line to the `stderr` you pass it.

**What the harness expects of you.**

1. **Add a floor entry** to `tests/collection_floor.json` for every new test
   module — `tests/test_suite_integrity.py` fails if a module has none. Floors
   are minimums; raise them deliberately when you split a module (A11).
2. **Delete lines from `tests/traceability_pending.txt`** as you add tests for
   R1–R12. Leaving a line in place while citing the requirement is a *failure*,
   not a warning, so the ledger stays honest.
3. **Cite requirements in test identity** — module docstring, class name,
   function name, or function docstring. A citation in a comment does not count.
4. **Do not add entries to `tests/allowed_skips.txt`.** It is empty and should
   stay empty; a skip is a test that did not run (R49). Import optional
   dependencies at module scope so a missing one is a collection error.
5. **Goldens** live in `tests/golden/`; compare with
   `tests.harness.assert_matches_golden`. `tests/golden/schema.json` is checked
   in and currently asserted only by its canary — *the R1/R2 test that asserts
   `schema_document()` equals it is yours*.
6. **Determinism**: `tests.harness.assert_deterministic(produce)` for in-process
   checks, `assert_cli_deterministic(args)` for the subprocess matrix. Both are
   proven breakable by the canaries.

**Fixture.** `tests/fixtures/mapper/stream_fragments.jsonl` is a *mapper* smoke
fixture, deliberately not in `tests/fixtures/traces/` so it does not collide
with T6's corpus (which R48 will require an expectations file for). It exercises
the fragment collapse, both tool-result statuses, a compaction boundary, an
attachment, an unknown record type, an unknown content block, three unknown
extra keys, four known-ignored keys, an orphan tool result, an API-error record,
a backwards timestamp and a trailing dangling tool call.

## Hand-verified before handoff

- `schema` output byte-identical across two runs, `PYTHONHASHSEED` ∈ {0, 1,
  random}, `TZ` ∈ {UTC, America/Los_Angeles, Asia/Kolkata}, `LC_ALL` ∈ {C,
  en_US.UTF-8} — nine subprocesses, one digest, matching the checked-in golden.
- 16 reader input classes fail closed with exit 2, one sanitized stderr line, no
  traceback, no output file: truncated JSON, non-object line, bare scalar,
  invalid UTF-8, oversized line (with and without a trailing newline), oversized
  file, record cap, file cap, missing path, directory, FIFO, empty input,
  symlink escape, duplicate basename — plus a symlink to a *named* input, which
  is accepted.
- 10 R4-fatal record classes fail closed the same way; a hostile record set
  (script-payload `agentId`, RTL override, negative and string token counts,
  scalar content blocks, unknown record type) parses without raising and leaks
  nothing into an id, a preview or a warning detail.
- Atomic write leaves a pre-existing output untouched and no temporary file
  behind when rendering fails mid-way.
- Package imports with no optional extras installed; `anthropic` absent.
- **Every suite-integrity guard tripped once and confirmed to fail loudly**:
  floor below count (exit 4, names the module), module collecting zero (exit 4),
  unlisted skip reason (exit 1), citation of a non-existent `R99`, requirement
  dropped from the ledger while uncited, ledger line that is actually cited, a
  `model → cli` import, the golden comparison neutered to always-return, and the
  determinism harness neutered to never-raise.

## Left for later increments

- **T6 fixture corpus** (`tests/fixtures/traces/*.jsonl` + expectation files)
  and `hostile.jsonl` — increment 2. The R48 both-arms coverage test and its
  canary go with it.
- **Detectors** (R13–R25), **cost** (R26–R31), **report** (R32–R37),
  **narrator** (R41–R43), **replay seam**, and the full `analyze`/`detectors`
  subcommands with exit codes and stdout discipline (R38–R40).
- **Five R50 canaries** ledgered in `tests/test_suite_integrity.REQUIRED_CANARIES`:
  injection-probe identity escape, attribute-allowlist injection, offline socket
  permitted, redaction pattern removed (all increment 4), detector coverage
  dropped (increment 2).
- **R49's deselection check** — "the `live_narrator` marker deselects a non-zero
  count" — needs a marked test to exist, so it lands with the narrator (T18).
- **A-a18's open question**: whether `Span.model` needs an ingest-time pattern
  constraint for R51's `--no-previews` arm. Decide at T17.
