# The normalized trace schema, and the adapter contract

`TRACE_SCHEMA_VERSION = "1.0.0"`

This document is for two readers: somebody consuming `report.json`, and
somebody writing a second `TraceSource` in v2. It describes the model in
`swarm_observer/model/trace.py` — the only type the detectors, the cost engine
and the renderers ever see — and the rules an adapter has to satisfy to produce
one.

The model is the stable contract. The Claude Code JSONL format it is currently
built from is **undocumented and unversioned**: six different `version` strings
appeared inside a single session during grounding. That is the whole reason
swarm-observer pins a schema version of its own rather than reflecting a
vendor's.

## Versioning

`TRACE_SCHEMA_VERSION` is semver and appears in every JSON report and in
`swarm-observer schema`. Adding a field to any model below is a **minor bump
plus a golden-file update**, and — because every string field on the model must
be classified as trace-derived or package-authored before a renderer will write
it — adding a *string* field is also a decision somebody has to record. That
friction is deliberate: four separate increments shipped a trace-derived string
to a report because nobody had written it on the list of trace-derived strings.

## The models

All are frozen pydantic v2 models with `extra="forbid"`. Every ordering, cap
and alphabet stated below is a validator, not a convention — a mapper that
breaks one fails at construction rather than three layers later.

### `Trace`

| field | type | notes |
|---|---|---|
| `schema_version` | `str` | equals `TRACE_SCHEMA_VERSION` |
| `trace_id` | `str` | 16 lowercase hex; see **Identity** |
| `adapter` | `str` | registry slug; `"claude_code_jsonl"` in v1 |
| `source_files` | `tuple[SourceFile, ...]` | sorted by name |
| `agents` | `tuple[AgentRun, ...]` | sorted by `agent_index`, which is `range(len(agents))` |
| `spans` | `tuple[Span, ...]` | sorted by `seq`, which is `range(len(spans))` |
| `warnings` | `tuple[ParseWarning, ...]` | sorted by `(code, detail)` |

### `Span`

| field | type | notes |
|---|---|---|
| `span_id` | `str` | 16 lowercase hex |
| `parent_span_id` | `str \| None` | a `tool_call`'s emitting `model_call` |
| `agent_id` | `str` | `^[A-Za-z0-9_.:\-]{1,64}$` — **trace-derived** |
| `kind` | `"model_call" \| "tool_call" \| "user_message" \| "system_event"` | |
| `seq` | `int` | 0-based position in canonical order |
| `start`, `end` | `datetime \| None` | tz-aware UTC |
| `model` | `str \| None` | **trace-derived**, `model_call` only |
| `usage` | `TokenUsage \| None` | `model_call` only |
| `stop_reason` | `str \| None` | **trace-derived** |
| `tool_name` | `str \| None` | **trace-derived**, `tool_call` only |
| `tool_use_id` | `str \| None` | **trace-derived** |
| `tool_input_digest` | `str \| None` | 16 hex over canonical JSON of the input |
| `tool_result_status` | `"ok" \| "error" \| "missing" \| None` | |
| `text_preview`, `tool_input_preview`, `tool_result_preview` | `str` | **trace-derived**, capped at 240 code points, `""` when absent or when `--no-previews` |
| `error` | `SpanError \| None` | |
| `extras_dropped` | `int` | count of unknown keys on the source record — never their content |

`SpanError` is `code` + `detail`. **Both halves are trace-derived.** `code` is
built from the record's own `error` field through the adapter's slug function,
which lowercases and restricts the alphabet to `^[A-Za-z0-9_.:\-]{1,40}$` —
that stops markup and admits a lowercase credential. It reached both reports
unredacted for four increments because its *type* looked enumerated. If you are
writing an adapter: this field is not yours, it is the trace's.

`TokenUsage` is five non-negative integers: `input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_5m_tokens`,
`cache_creation_1h_tokens`. `input_tokens` as recorded already **excludes**
cached tokens, so nothing subtracts and nothing double-counts.

### `AgentRun`

`agent_id` (trace-derived), `agent_index` (0-based, canonical order of first
appearance), `agent_type` and `description` (both trace-derived; description
capped at 200), `parent_agent_id` (trace-derived), `depth`, `span_seqs`
(ascending), `start`, `end`.

### `SourceFile`

`name` (**basename only, never a path**), `sha256` (64 hex), `bytes`,
`records`. The name is not trace-derived, but `analyze <dir>` reads whatever
basenames a directory holds, so it is attacker-influenceable and is treated as
an identifier by both renderers.

### `ParseWarning`

`code` from a closed enum, `count`, and `detail` — an enumerated slug, a record
type name or a decimal count, **never trace free text**, but trace-*derived*
all the same when it carries an unknown record type.

The codes: `unknown_record_type`, `unknown_content_block`, `known_ignored_key`,
`unknown_extra_key`, `timestamp_out_of_order`, `negative_duration`,
`orphan_tool_result`, `dangling_tool_use`, `missing_usage`, `synthetic_model`,
`compaction_boundary`, `api_error_record`.

## Identity

- `trace_id` = first 16 hex of SHA-256 over
  `"\n".join(f"{f.name}:{f.sha256}")` across source files sorted by name.
  Independent of directory, of CWD, and of the order paths were given, so two
  people analyzing the same bytes from different machines get the same id.
- `span_id` = first 16 hex of SHA-256 over
  `f"{trace_id}|{agent_id}|{seq}|{kind}"`.
- `tool_input_digest` = first 16 hex of SHA-256 over
  `json.dumps(tool_input, sort_keys=True, ensure_ascii=True, separators=(",", ":"))`.
  A null or absent input digests the literal `"null"`.
- `finding_id` = `f"{detector}:{h}"` where `h` is 12 hex over the trace id,
  the slug, the metrics and the evidence span list. Stable across cosmetic
  report changes and across runs, which is what will make suppression possible
  in v2 without a new concept.

No id ever embeds trace content, which is why they are the only trace-adjacent
strings allowed in an HTML attribute.

## Canonical order

Records are ordered by `(source_file_index, line_number)`, with
`source_file_index` the file's position in `source_files` sorted by **basename**.

**File order is authoritative. Timestamps are never used for ordering.** Real
transcripts contain out-of-order timestamps — seven occurrences in an 11-file,
5,385-record grounding corpus — and a timestamp sort would make `seq`, and
therefore `span_id`, and therefore every finding id, depend on clock skew.
Out-of-order timestamps are counted as `timestamp_out_of_order`; a duration
that comes out negative is clamped to 0 and counted as `negative_duration`.

One consequence worth knowing before it surprises you: for `agent-<id>.jsonl`
files, basename order is agent-id order, not chronological order.

## Writing a second adapter

Implement `swarm_observer.ingest.source.TraceSource`:

```python
class TraceSource(Protocol):
    def load(self, paths: Sequence[Path], limits: IngestLimits) -> Trace: ...
```

and add one registry entry. Nothing in `model/`, `detect/`, `cost/`, `report/`
or `narrate/` changes — the R44 AST test asserts that your package is the only
one that knows your format's field names.

What the contract asks of you:

1. **Fail closed, with a sanitized single line.** A line that is not valid
   JSON, a record missing `type`/`uuid`/`timestamp`, a non-RFC-3339 timestamp,
   a duplicate uuid within a file, a symlink out of the named input set or any
   limit breach raises `TraceError`. The CLI turns that into exit 2 and one
   line naming a basename, a line number, a limit and a code — never a byte of
   file content, and never a traceback. No partial `Trace` is ever returned.
2. **Tolerate and count the rest.** Unknown record types, unknown content
   blocks, unknown keys and known-but-ignored keys become `ParseWarning`s with
   counts. Tolerance never produces a partial result silently: warnings are
   rendered in both reports and a non-zero `unknown_record_type` count is
   surfaced in the HTML header.
3. **Be a pure function of the bytes.** No clock, no environment, no path in
   the output. Two runs over the same bytes produce the same `Trace`.
4. **Collapse whatever your format fragments.** If your transcript writes one
   record per streamed block and repeats the usage object, sum nothing —
   collapse, take usage from the terminal fragment, and pin both the collapsed
   and the naive totals in a test so a regression changes a dollar figure
   loudly.
5. **Decide, for every string you set, whether it is trace-derived.** The
   renderers will ask: `report/sanitize.py` classifies every rendered string as
   `authored`, `free`, `identifier` or `narrator`, and there is no fifth option
   and no default. Getting this wrong is the single most repeated defect in
   this repository's history, and it has always been a field whose *type*
   looked safe.

## What the JSON report adds

`report.json` is this model plus computed results: the four cost groupings and
the unpriced list, the findings, the options the run used, and — only under
`--explain` — a `narrative` object. Every trace-derived string in it has been
through the redactor; nothing in it has been through `escape_html`, because
that is the HTML renderer's boundary and JSON has its own quoting.
