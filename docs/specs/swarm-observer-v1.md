# Spec: swarm-observer v1

Status: APPROVED — owner ratified 2026-09-09; A5 (hand-authored fixture corpus) accepted, real transcripts to be run as a manual parser check before v1 ships
Branch strategy: one branch + PR per increment (feature/so-i1 … feature/so-i5), each targeting main

## Summary

swarm-observer is a CLI that reads trace artifacts already written to disk by a multi-agent AI system — in v1, Claude Code session transcripts in JSONL — normalizes them into a versioned, source-agnostic trace model, runs a set of deterministic detectors over that model, prices the model calls from a bundled rate snapshot, and emits two artifacts: a machine-readable JSON report and a single self-contained HTML report (all CSS/JS inline, zero external requests, no build step, no server). Ingestion is post-hoc and zero-instrumentation: swarm-observer never runs inside the observed system, never requires an SDK, and never asks the observed system to change. Every finding is a pure function of the normalized model — byte-deterministic and pinned by golden tests. One optional pass (`--explain`, off by default) sends a *numeric summary of findings* — never trace text — to an LLM behind a Protocol to produce prose, with a per-group deterministic fallback that never fails the run and never perturbs the rest of the document.

The threat model is stronger than spend-sentinel's or draftsmith's, and the reason is structural: in those projects the untrusted string came from the user's own Terraform plan or from a model this codebase prompted. Here, every prompt, tool argument, tool result and agent message in the trace was produced by an agent that may itself have processed hostile input — a fetched web page, a malicious repository, a poisoned document — and swarm-observer renders that content into an HTML file a human opens in a browser and forwards to colleagues. The trace is the attacker's channel and the report is the payload's delivery vehicle. v1 therefore treats *every* trace-derived byte as hostile: escaped at the single render boundary, never placed in an attribute, URL or script context, credential-shaped substrings redacted, and a `--no-previews` mode that omits trace free text entirely.

Delivered as five increments, one PR each.

## Requirements

Each requirement is verifiable offline, with no credentials and no optional SDKs installed, unless explicitly noted. "Trace-derived string" below means any string whose bytes originate in an input trace file, or in an `--explain` narrator response.

### Trace model & ingestion

- R1 (schema version — pinned): `swarm_observer/model/trace.py` defines the normalized trace model and the module constant `TRACE_SCHEMA_VERSION = "1.0.0"` (semver). Every emitted JSON report and every `swarm-observer schema` invocation carries this exact string. The normalized model is the *only* type the detectors, cost engine and renderers see; no adapter-specific field name appears anywhere outside `swarm_observer/ingest/`.

- R2 (normalized model — pinned): All models are frozen pydantic v2 `BaseModel`s with `model_config = ConfigDict(frozen=True, extra="forbid")`. The shape is exactly:

  ```python
  class TokenUsage(BaseModel):            # all fields int >= 0, default 0
      input_tokens: int
      output_tokens: int
      cache_read_input_tokens: int
      cache_creation_5m_tokens: int
      cache_creation_1h_tokens: int

  SpanKind = Literal["model_call", "tool_call", "user_message", "system_event"]
  ToolResultStatus = Literal["ok", "error", "missing"]

  class SpanError(BaseModel):
      code: str            # enumerated slug, never trace-derived free text
      detail: str          # trace-derived, capped at 200 chars

  class Span(BaseModel):
      span_id: str                     # R5
      parent_span_id: str | None
      agent_id: str
      kind: SpanKind
      seq: int                         # 0-based position in the trace's canonical order (R6)
      start: datetime | None           # tz-aware UTC
      end: datetime | None             # tz-aware UTC
      model: str | None                # model_call only
      usage: TokenUsage | None         # model_call only
      stop_reason: str | None
      tool_name: str | None            # tool_call only
      tool_use_id: str | None
      tool_input_digest: str | None    # R7
      tool_result_status: ToolResultStatus | None
      text_preview: str                # R8, "" when absent
      tool_input_preview: str          # R8, "" when absent
      tool_result_preview: str         # R8, "" when absent
      error: SpanError | None
      extras_dropped: int              # count of unknown keys on the source record; never their content

  class AgentRun(BaseModel):
      agent_id: str
      agent_index: int                 # 0-based, assigned in canonical order of first appearance
      agent_type: str | None           # trace-derived
      description: str                 # trace-derived, capped at 200 chars, "" when absent
      parent_agent_id: str | None
      depth: int | None
      span_seqs: tuple[int, ...]       # ascending
      start: datetime | None
      end: datetime | None

  class SourceFile(BaseModel):
      name: str                        # basename only — never a directory path (R47)
      sha256: str                      # 64 lowercase hex
      bytes: int
      records: int

  class ParseWarning(BaseModel):
      code: str                        # closed enum (R10)
      count: int
      detail: str                      # enumerated slug or number only, never trace free text

  class Trace(BaseModel):
      schema_version: str              # == TRACE_SCHEMA_VERSION
      trace_id: str                    # R5
      adapter: str                     # "claude_code_jsonl" in v1
      source_files: tuple[SourceFile, ...]     # sorted by name
      agents: tuple[AgentRun, ...]             # sorted by agent_index
      spans: tuple[Span, ...]                  # sorted by seq
      warnings: tuple[ParseWarning, ...]       # sorted by (code, detail)
  ```

  No other field exists on these models in v1. Adding one is a `TRACE_SCHEMA_VERSION` minor bump plus a golden-file update.

- R3 (adapter seam — pinned): `swarm_observer/ingest/source.py` defines `TraceSource`, a `typing.Protocol` with the single method `load(paths: Sequence[Path], limits: IngestLimits) -> Trace`, and the `IngestLimits` frozen model (R11). `swarm_observer/ingest/claude_code/` is the only implementation in v1. Nothing outside `swarm_observer/ingest/` imports anything from `ingest.claude_code`; the CLI selects the adapter through a registry `dict[str, TraceSource]` keyed by adapter slug. An OTel or SDK source in v2 is a new package under `ingest/` plus a registry entry, with zero edits to `model/`, `detect/`, `cost/` or `report/` — asserted by the R44 import-boundary test.

- R4 (raw record layer — pinned, extra fields): `ingest/claude_code/records.py` defines the on-disk record models with `model_config = ConfigDict(extra="allow")`. Handling of unexpected input is split, and the split is the requirement:
  - **Tolerated and counted**: unknown top-level keys on a record (counted into `Span.extras_dropped`, contents discarded); unknown record `type` values (skipped, counted as `unknown_record_type` with the type slug in `ParseWarning.detail`); unknown `message.content` block types (skipped, counted as `unknown_content_block`); known-but-ignored keys (`iterations`, `output_tokens_details`, `server_tool_use`, `diagnostics`, `stop_details`, `container`, `context_management`, `quotaLimits`, `rendered`, `apiBlockIndex`, `logicalParentUuid`, `compactMetadata`) — counted once per key as `known_ignored_key`.
  - **Fatal (fail closed, R11)**: a line that is not valid JSON; a line whose JSON is not an object; a record missing any of `type`, `uuid`, `timestamp`; a `timestamp` that is not RFC 3339; a duplicate `uuid` within one file; an `assistant`/`user` record whose `message` is present but not an object; any size cap exceeded.

  Tolerance never produces a partial result silently: `warnings` is always rendered in both reports, and a non-zero `unknown_record_type` count is surfaced in the HTML header.

- R5 (identity — pinned, deterministic): `trace_id` is the first 16 lowercase hex characters of the SHA-256 over the UTF-8 bytes of `"\n".join(f"{f.name}:{f.sha256}" for f in sorted source_files by name)`. It is therefore independent of directory path, of CWD, and of the order paths were passed on the command line. `span_id` is the first 16 hex characters of SHA-256 over `f"{trace_id}|{agent_id}|{seq}|{kind}"`. `agent_id` is taken from the record's `agentId` when present, else the literal `"root"`. Span and agent ids never embed trace content, so they are safe in HTML attribute values (R32).

- R6 (canonical order — pinned, timestamps are advisory): Records are ordered by `(source_file_index, line_number)`, where `source_file_index` is the index of the file in `source_files` (sorted by basename). **File order is authoritative; timestamps are never used for ordering.** This is deliberate: real Claude Code transcripts contain out-of-order timestamps (observed 7 times in an 11-file, 5,385-record corpus), and a timestamp sort would make `seq` — and therefore `span_id`, and therefore every finding id — depend on clock skew. Out-of-order timestamps are counted as the warning `timestamp_out_of_order`. Any computed duration that comes out negative is clamped to 0 and counted as `negative_duration`.

- R7 (tool input digest — pinned): `tool_input_digest` is the first 16 hex characters of SHA-256 over `json.dumps(tool_input, sort_keys=True, ensure_ascii=True, separators=(",", ":"))`. It is the loop and duplication detectors' only view of tool arguments, so those detectors never touch hostile text. A `null`/absent tool input digests the literal `"null"`.

- R8 (previews — pinned): `text_preview`, `tool_input_preview` and `tool_result_preview` hold trace-derived free text for human reading, produced by: take the source string (for tool input, the R7 canonical JSON); replace every character that is not `str.isprintable()` with a single space; collapse runs of whitespace to one space; strip; truncate to 240 characters, appending `"…"` when truncated. Truncation is by Unicode code point, not bytes. These fields are the *only* trace free text that reaches a report, they are always passed through redaction (R33) then escaping (R32) at render time, and `--no-previews` (R38) sets all three to `""` at model-build time so the bytes never exist in the process after ingestion.

- R9 (stream-fragment collapse — pinned, and the reason cost accounting is correct): A Claude Code transcript writes **one JSONL record per streamed content block**, and every fragment of one API response repeats that response's `usage` object, with only the terminal fragment carrying final `output_tokens` and `stop_reason`. Naive per-record summation therefore double-counts badly: measured on the grounding corpus, 2,706 assistant records collapse to 1,678 model calls, and naive summation inflates cache-read tokens by 55.4% (826,955,847 → 532,235,958), cache-creation tokens by 55.3%, and output tokens by 2.5%. The mapper MUST collapse assistant records sharing the key `(sessionId, agentId, message.id, requestId)` into exactly one `model_call` span, by these rules:
  - Content blocks are unioned in first-appearance order and deduplicated by block identity: `id` for `tool_use`, `tool_use_id` for `tool_result`, and `(block_type, sha256(text))` for `text` and `thinking`.
  - `usage`, `stop_reason` and `model` are taken from the **last** fragment in canonical order (R6), not summed, not merged.
  - `start` is the first fragment's timestamp, `end` the last fragment's timestamp.
  - A record with no `requestId` and no `message.id` is its own group.
  A test asserts collapse against a fixture derived from real transcript structure, with both the collapsed and naive totals pinned as constants so a regression to naive summation fails loudly rather than quietly changing a dollar figure.

- R10 (parse-warning taxonomy — pinned, closed): `ParseWarning.code` is exactly one of: `unknown_record_type`, `unknown_content_block`, `known_ignored_key`, `unknown_extra_key`, `timestamp_out_of_order`, `negative_duration`, `orphan_tool_result`, `dangling_tool_use`, `missing_usage`, `synthetic_model`, `compaction_boundary`, `api_error_record`. Warnings are aggregated (one entry per `(code, detail)` with a count), sorted, and never carry trace free text in `detail` — only an enumerated slug, a record-type name, or a decimal count.

- R11 (fail-closed reading — pinned): `IngestLimits` is a frozen model with defaults `max_file_bytes = 268_435_456` (256 MiB), `max_line_bytes = 8_388_608` (8 MiB), `max_records = 2_000_000`, `max_files = 64`. Files are read line-by-line; a line is rejected before parsing if it exceeds `max_line_bytes`. Any fatal condition of R4, any limit breach, an unreadable path, a path that is not a regular file, or a symlink pointing outside the set of named inputs raises `TraceError` — a single-line, sanitized exception. The CLI catches it, writes exactly one line to stderr of the form `swarm-observer: <code>: <sanitized detail>` and exits 2. No traceback ever reaches stderr, no partial `Trace` is ever returned, no output file is written or truncated (outputs are written to a temporary file in the destination directory and atomically renamed only on full success). `<sanitized detail>` may name a file basename, a line number, a limit value and a `TraceError` code; it may never contain a byte of file content.

- R12 (span kinds — pinned mapping): The Claude Code mapper produces: one `model_call` span per collapsed assistant group (R9); one `tool_call` span per `tool_use` block, whose `parent_span_id` is the emitting `model_call` span and whose `tool_result_status`/`tool_result_preview` come from the `tool_result` block with the matching `tool_use_id` anywhere in the trace (`"missing"` when none exists); one `user_message` span per `user` record whose message content is a string or contains a non-`tool_result` block; one `system_event` span per `system` record (with `error.code = "compact_boundary"` for `subtype == "compact_boundary"`). `attachment` records produce no span and are counted. An assistant record with `isApiErrorMessage: true` produces a `model_call` span with `usage = None`, `model` as recorded (commonly `"<synthetic>"`), and `error = SpanError(code=<the record's "error" slug, lowercased, non-slug characters replaced with "_", capped at 40 chars>, detail=<preview of apiErrorStatus>)`.

### Detectors

- R13 (detector contract — pinned): `swarm_observer/detect/base.py` defines `Detector`, a `typing.Protocol` with `slug: str`, `title: str`, `default_severity: Severity`, and `run(trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]`. Every detector is a **pure function** of `(trace, config)`: no I/O, no clock, no randomness, no environment reads, no mutation of the trace. Findings are returned already sorted by `(severity_rank, slug, finding_id)`. `swarm_observer/detect/registry.py` holds `ALL_DETECTORS: tuple[Detector, ...]` in a fixed order and is the single source of truth for which detectors exist (R50).

- R14 (Finding — pinned): 

  ```python
  Severity = Literal["info", "warning", "critical"]   # rank info=0 < warning=1 < critical=2

  class Finding(BaseModel):                 # frozen, extra="forbid"
      detector: str                          # registry slug
      finding_id: str                        # R15
      severity: Severity
      summary: str                           # R16 — never contains trace-derived text
      span_seqs: tuple[int, ...]             # ascending, capped at 50; evidence
      agent_ids: tuple[str, ...]             # sorted
      metrics: dict[str, int | str]          # JSON scalars only; str values are enumerated slugs
                                             #   or trace-derived tool names (R16); keys sorted
      previews: tuple[str, ...]              # trace-derived evidence text, capped at 5 entries
      wasted: TokenUsage                     # R17
      wasted_cost_usd: Decimal | None        # R26
  ```

- R15 (finding id — pinned): `finding_id = f"{detector}:{h}"` where `h` is the first 12 hex characters of SHA-256 over `json.dumps({"trace": trace_id, "detector": slug, "metrics": metrics, "spans": list(span_seqs)}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`. Ids are therefore stable across runs and across cosmetic report changes, and change when the evidence changes — which makes them usable as suppression keys in v2.

- R16 (summary construction — pinned, injection-relevant): `Finding.summary` is built from a per-detector format string whose only substitutions are integers, `Decimal`s and enumerated slugs computed by swarm-observer. Trace-derived text NEVER enters `summary`. The one trace-derived value permitted in `metrics` is `tool_name`, which is additionally constrained: if the recorded tool name does not match `^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$` it is replaced with the literal `"<non-conforming>"` and the original goes to `previews` instead. This keeps the findings table — the part of the report a reader trusts most — free of attacker-influenced bytes even before escaping.

- R17 (waste attribution — pinned, and honestly labelled): `Finding.wasted` is the element-wise sum of `Span.usage` over the **distinct `model_call` spans named by that detector as redundant** (each detector below says which). It is an *attribution* — "these model calls produced work the trace shows was repeated or discarded" — not a counterfactual claim that removing them would have saved exactly that much. Both reports state this in one sentence next to the total. Detectors with no redundancy notion emit a zero `TokenUsage`.

- R18 (detector: `repeated_tool_call` — pinned): Groups all `tool_call` spans by `(tool_name, tool_input_digest)`. Fires one finding per group whose size `n >= 2`. Severity `warning` for `2 <= n <= 3`, `critical` for `n >= 4`. `metrics`: `{"occurrences": n, "tool_name": ..., "first_seq": ..., "last_seq": ...}`. `span_seqs`: the group's tool_call spans (ascending, capped 50). Redundant model calls for R17: the `parent_span_id` model_call spans of occurrences 2..n, deduplicated. `previews`: the first occurrence's `tool_input_preview`.

- R19 (detector: `agent_loop` — pinned): For each agent, build its signature sequence `S` in `seq` order, where a `model_call` span's signature is `("tool", tool_name, tool_input_digest)` of its first emitted `tool_call` if any, else `("text",)`; `tool_call`, `user_message` and `system_event` spans are excluded from `S`. For each period `p` in `1..8` ascending, and each start index `i` in `0..len(S)-3p` ascending, if `S[i:i+p] == S[i+p:i+2p] == S[i+2p:i+3p]`, fire at the first such `(p, i)` found by that iteration order, with `k` = the maximal number of consecutive repeats of `S[i:i+p]` starting at `i`. At most one finding per agent; the search then resumes at index `i + k*p` so a long trace can yield multiple non-overlapping loops per agent. Severity `warning` for `k == 3`, `critical` for `k >= 4`. `metrics`: `{"period": p, "repeats": k, "start_seq": ..., "end_seq": ...}`. Redundant model calls: the spans of repeats 2..k.

- R20 (detector: `retry_storm` — pinned): For each agent, scan a sliding window of 10 consecutive spans (in `seq` order). An *error span* is a `tool_call` span with `tool_result_status == "error"` or a `model_call` span with a non-null `error`. Fires when a window contains `>= 3` error spans; overlapping windows are merged into one finding covering the maximal run. Severity `warning` for 3–4 error spans in the merged run, `critical` for `>= 5`. `metrics`: `{"errors": n, "window_spans": m, "kinds": "tool_error|api_error|mixed", "start_seq": ..., "end_seq": ...}`. Redundant model calls: every `model_call` span in the merged run. This detector deliberately overlaps `failed_tool_call` (R21): that one reports *existence*, this one reports *density*, and a report may legitimately carry both for the same spans.

- R21 (detector: `failed_tool_call` — pinned): One finding per `(agent_id, tool_name)` for which at least one `tool_call` span has `tool_result_status == "error"`. Severity `info` for exactly 1 occurrence, `warning` for `>= 2`. `metrics`: `{"failures": n, "tool_name": ..., "first_seq": ...}`. `previews`: up to 5 distinct `tool_result_preview` values, in `seq` order. Redundant model calls: none (zero `wasted`) — a failed call is not by itself waste.

- R22 (detector: `unresolved_tool_call` — pinned, with the truncation carve-out): Fires per `(agent_id, reason)` where `reason` is one of the closed set:
  - `no_result` — a `tool_call` span with `tool_result_status == "missing"`;
  - `orphan_result` — a `tool_result` content block whose `tool_use_id` matches no `tool_use` block in the trace (counted at parse time as `orphan_tool_result`, R10);
  - `unknown_tool` — a `tool_call` span with `tool_result_status == "error"` whose result text matches, case-insensitively, any of the pinned patterns `no such tool`, `tool not found`, `unknown tool`, `is not a recognized tool`, `unrecognized tool name`.

  **Carve-out**: a `no_result` span that is the highest-`seq` span of its agent is excluded and instead counted as the parse warning `dangling_tool_use`; a trace captured while an agent is mid-flight always has exactly one such span, and firing on it would make this detector produce a false positive on essentially every live capture. Severity `warning` for `no_result`/`orphan_result`, `critical` for `unknown_tool`. `metrics`: `{"reason": ..., "occurrences": n, "tool_name": ...}`.

- R23 (detector: `blocked_agent` — pinned): For each agent, for each pair of spans consecutive in that agent's `span_seqs` where both `prev.end` and `next.start` are non-null, compute `gap = next.start - prev.end` clamped at 0. Fires when `gap >= config.blocked_gap_seconds` (default 60) **and** the gap is not explained: a gap is *explained* when the union of `[start, end]` intervals of spans belonging to other agents covers at least 50% of the gap's duration (integer millisecond arithmetic, no floats). Severity `warning` for `gap < 300 s`, `critical` for `gap >= 300 s`. `metrics`: `{"gap_seconds": floor(gap), "before_seq": ..., "after_seq": ...}`. Zero `wasted`.

- R24 (detector: `anomalous_span` — pinned, integer statistics only): Population = all `model_call` spans with non-null `usage`. Skips entirely when the population size is `< 8`. For dimension `tokens`, `x(span) = input + output + cache_read + cache_creation_5m + cache_creation_1h`; for dimension `duration`, `x(span) = (end - start)` in integer milliseconds over spans with both endpoints. For each dimension: `med` = the **lower median** of the sorted values (element at index `(n-1)//2` — pinned, so even-sized populations never introduce a `.5`), `mad` = the lower median of `|x - med|`. A span fires when `x > med + 6*mad` **and** `x >= floor_d`, where `floor_tokens = 10_000` and `floor_duration_ms = 30_000`. All arithmetic is `int`. Severity `warning`, or `critical` when `x > med + 12*mad`. One finding per `(span, dimension)`. `metrics`: `{"dimension": ..., "value": x, "median": med, "mad": mad, "seq": ...}`. Redundant model calls: none (zero `wasted`) — an outlier is not necessarily waste.

- R25 (detector configuration — pinned): `DetectorConfig` is a frozen model with exactly `blocked_gap_seconds: int = 60`, `enabled: frozenset[str] | None = None` (None means all). No detector reads any other tunable in v1; thresholds inside R18–R24 are constants in their modules, not configuration, so a golden report is a function of the trace and one integer.

### Cost accounting

- R26 (rate source — bundled snapshot, and why): `swarm_observer/cost/source.py` defines `RateSource`, a `typing.Protocol` with `get_rate(model_key: str, price_key: str) -> Decimal | None` and `meta -> SnapshotMeta`, in the style of spend-sentinel's `PricingSource`. `cost/snapshot.py` provides `SnapshotRateSource`, loading the package-data file `swarm_observer/cost/data/model_rates.json` — rates stored as strings, converted to `Decimal` exactly once at load. **Decision: rates are a bundled versioned snapshot, not a live lookup, and there is no live adapter in v1.** Three reasons, in order: (1) determinism — R47's byte-identical guarantee is incompatible with a value that can change between two runs of the same command; (2) correctness — a trace is a *historical* artifact, so today's list price is the wrong number for last month's run, and a pinned `snapshot_date` at least makes the approximation legible; (3) offline — the whole suite and the whole default path run with zero credentials and zero egress. The `RateSource` protocol is nevertheless the seam: a v2 live or historical-rate adapter is a new implementation with no change to `cost/compute.py`.

- R27 (rate file shape and model resolution — pinned): The snapshot is `{"meta": {"version", "snapshot_date", "currency": "USD", "sources": [...]}, "models": {<model_key>: {"input", "output", "cache_read", "cache_write_5m", "cache_write_1h"}}, "aliases": {<recorded_model_id>: <model_key>}}`, all rates being USD **per 1,000,000 tokens** as decimal strings. A recorded `Span.model` resolves to a `model_key` by, in order: (1) exact key in `models`; (2) exact key in `aliases`; (3) the longest key `k` in `models` such that the recorded id equals `k` or starts with `k + "-"` (this is what maps the real, date-suffixed `claude-haiku-4-5-20251001` onto `claude-haiku-4-5`); (4) unresolved. Ties in (3) are impossible because keys are unique and longest wins. Resolution is pure string work with no network and no heuristics beyond the above.

- R28 (cost formula — pinned): For a `model_call` span with resolved rates, monthly-agnostic USD cost is

  ```
  cost = ( input_tokens            * rate.input
         + output_tokens           * rate.output
         + cache_read_input_tokens * rate.cache_read
         + cache_creation_5m_tokens * rate.cache_write_5m
         + cache_creation_1h_tokens * rate.cache_write_1h ) / Decimal(1_000_000)
  ```

  Notes that are requirements, not commentary: `input_tokens` as recorded already **excludes** cached tokens, so no subtraction is performed and no double count occurs. `output_tokens_details.thinking_tokens` is already included in `output_tokens` and is never added. When the source record carries `cache_creation_input_tokens` but no `cache_creation` breakdown, the whole amount is charged at `cache_write_5m` and the warning `missing_usage` is not raised (the breakdown's absence is expected on older transcript versions). `usage.iterations` is ignored entirely (R4).

- R29 (Decimal discipline — pinned): All money is `Decimal` from load to format; floats never enter the computation. Each span's cost is quantized `ROUND_HALF_UP` to **6 decimal places**. Every total, subtotal and per-finding `wasted_cost_usd` is the sum of already-quantized span costs, so every table's rows sum exactly to its total. Both reports render the 6-decimal string verbatim; the HTML additionally shows a 2-decimal figure for the grand total only, computed by quantizing the 6-decimal total.

- R30 (unpriced taxonomy — pinned, closed, nothing dropped): No `model_call` span is ever silently omitted from the cost section. A span that cannot be priced appears in the `unpriced` list with its `seq`, its recorded `model` (redacted and escaped like any trace string), and exactly one reason from the closed enum, assigned in this priority order:
  1. `synthetic_span` — the span has a non-null `error` (an API-error record) or `model == "<synthetic>"`. Never billable.
  2. `usage_missing` — `usage is None`.
  3. `model_not_in_snapshot` — R27 resolution reached step (4).
  4. `rate_key_missing` — the model resolved, but a price key required by a non-zero component of `usage` is absent from its rate entry.
  A span that resolves but whose only non-zero usage components have rates present is priced; a zero-token component never triggers `rate_key_missing`.

- R31 (cost aggregation — pinned): The cost report contains totals by: whole trace; per agent (ordered by `agent_index`); per resolved `model_key` (ordered by key); and per detector for `wasted_cost_usd` (ordered by slug). Each grouping's total is the sum of its members' quantized span costs, and the JSON report carries all four groupings plus the `unpriced` list plus `meta` (snapshot version, snapshot date, currency).

### Report rendering

- R32 (escaping — pinned, single definition): `swarm_observer/report/escape.py` defines exactly one `escape_html(text: str) -> str`, the single definition used by every renderer. Its table is:

  | char | replacement | | char | replacement |
  | --- | --- | --- | --- | --- |
  | `&` | `&amp;` | | `'` | `&#x27;` |
  | `<` | `&lt;` | | `/` | `&#x2F;` |
  | `>` | `&gt;` | | `` ` `` | `&#x60;` |
  | `"` | `&quot;` | | `=` | `&#x3D;` |

  Every other character that is not `str.isprintable()` becomes a single space; all other characters pass through. `&` is replaced first (the implementation is a single pass over code points, not sequential `str.replace`, so double-escaping is impossible). The table is deliberately wider than the minimum needed for text-node context — it also neutralizes the attribute-context characters — because the review lesson from draftsmith increment 5 is that a guard tuned to the call path that exists is wrong for the next call path.

- R33 (redaction — pinned, ordered, idempotent): `report/redact.py` defines `redact(text: str) -> str`, applied to every trace-derived string **before** `escape_html`. Patterns are applied in this exact order, each match replaced by `[redacted:<label>]`:

  | label | pattern (Python `re`) |
  | --- | --- |
  | `private_key` | `-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----` |
  | `aws_key_id` | `\b(?:AKIA\|ASIA\|AIDA\|AROA\|AIPA\|ANPA\|ANVA)[0-9A-Z]{16}\b` |
  | `anthropic_key` | `\bsk-ant-[A-Za-z0-9_\-]{20,}\b` |
  | `openai_key` | `\bsk-[A-Za-z0-9]{20,}\b` |
  | `github_token` | `\bgh[pousr]_[A-Za-z0-9]{20,}\b` |
  | `slack_token` | `\bxox[abposr]-[A-Za-z0-9-]{10,}\b` |
  | `google_api_key` | `\bAIza[0-9A-Za-z_\-]{35}\b` |
  | `jwt` | `\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b` |
  | `bearer` | `(?i)\b(?:authorization\s*:\s*bearer\s+\|bearer\s+)[A-Za-z0-9._\-]{20,}` |
  | `secret_assignment` | `(?i)\b([A-Za-z0-9_]*(?:SECRET\|TOKEN\|PASSWORD\|PASSWD\|API[_-]?KEY\|ACCESS[_-]?KEY\|PRIVATE[_-]?KEY)[A-Za-z0-9_]*)\s*[=:]\s*(?:"[^"\n]{6,}"\|'[^'\n]{6,}'\|\S{6,})` — the name is preserved, the value replaced |

  `redact` is idempotent: `redact(redact(s)) == redact(s)` for all `s`, asserted by a property test over the fixture corpus and a hand-written adversarial set. The spec states plainly, and the README repeats: **redaction is a courtesy, not a boundary.** It reduces accidental credential exposure in a shared report; it cannot defeat an adversary who controls the trace and wants a secret rendered. The actual boundary is R32 plus R34 plus `--no-previews` (R38).

- R34 (no trace bytes in executable or attribute context — pinned): The HTML report satisfies all of:
  - Trace-derived text appears **only** as element text content, never inside a tag, attribute value, `<style>`, `<script>`, URL, `srcset`, or comment.
  - The document contains exactly one `<script>` element and exactly one `<style>` element, both constant strings compiled into the package. A test asserts the SHA-256 of each equals a checked-in constant, so any attempt to interpolate into them fails the suite rather than shipping.
  - The one script performs only DOM class toggling, filtering and sorting over nodes already present; it never calls `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `Function`, `setTimeout`/`setInterval` with a string, or `fetch`/`XMLHttpRequest`/`WebSocket`/`import()`. Asserted by a source grep test.
  - Every attribute value in the document is drawn from a generated allowlist: fixed CSS class names, `data-severity` ∈ the `Severity` enum, `data-detector` ∈ the registry slugs, `data-agent` ∈ decimal `agent_index` values, `data-seq` ∈ decimal `seq` values, and `href="#<span_id|finding_id>"` where both are hex/slug by construction (R5, R15). A test parses the rendered file with `html.parser` and asserts every observed attribute value matches the allowlist.
  - The document carries `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">` as the first element of `<head>` after `<meta charset>`.

- R35 (no external requests — pinned): The rendered HTML contains no `http:`, `https:`, `//` scheme-relative, `file:`, or protocol-handler URL anywhere; no `<link>`, `<img src>`, `<iframe>`, `<object>`, `<embed>`, `<form>`, `<base>`, or `@import`; and no font, icon or library reference. The only permitted `href` values are same-document fragments. Asserted by a parser-based test, not a regex over the source.

- R36 (report structure — pinned section order): `report/html.py` renders, in this fixed order: `<h1>` title; a header block (trace id, adapter, schema version, source file names with SHA-256 and record counts, span/agent/finding counts, non-empty warning summary, rate snapshot version and date, swarm-observer version); the narrative section anchor (empty and omitted entirely unless `--explain` produced content, R43); Findings (grouped by severity descending, then detector slug, then `finding_id`; each finding shows summary, severity, metrics table, evidence span links, previews, and attributed waste with the R17 caveat sentence); Timeline (R37); Cost (the four R31 groupings plus the unpriced table); Spans (a table of all spans, capped at 5,000 rows with an "…and N more" line); Warnings (the full `ParseWarning` list). `report/json_out.py` emits the same data as JSON with `sort_keys=True, ensure_ascii=True, indent=2`, a trailing newline, and `Decimal` serialized as its 6-decimal string.

- R37 (timeline — pinned, deterministic geometry): The timeline is inline SVG with no script and no external reference. One horizontal lane per agent, ordered by `agent_index`; one rect per span with a start/end, positioned by integer arithmetic: `x = round_half_up(1000 * (span.start - trace_start).total_seconds_int_ms / trace_span_ms)` on a fixed 1000-unit viewBox width, with a minimum rect width of 1 unit. All coordinates are integers; no float ever reaches the output bytes. Spans with a null `start` or `end` are listed in a "no timing" note rather than drawn. When `trace_span_ms == 0`, every rect is drawn at `x=0, width=1`. Rect fill is by `kind` and `severity` via fixed CSS classes only.

### CLI

- R38 (surface — pinned): The console script `swarm-observer` exposes exactly three subcommands:
  - `analyze <path>... --out <report.html> [--json <report.json>] [--adapter claude_code_jsonl] [--explain] [--no-previews] [--detector <slug>]... [--blocked-gap-seconds N] [--fail-on {none,warning,critical}] [--max-file-bytes N] [--max-line-bytes N] [--max-records N]`
  - `schema` — prints the normalized model's JSON Schema and `TRACE_SCHEMA_VERSION` to stdout, deterministically.
  - `detectors` — prints each registry slug, default severity and one-line description, in registry order.
  A directory passed to `analyze` expands to its `*.jsonl` children sorted by basename, non-recursively; anything else must be a regular file. `--detector` may be repeated and restricts the run to the named slugs; an unknown slug is a usage error.

- R39 (exit codes — pinned): `0` — ran, and no finding met the `--fail-on` threshold (default `none`, which never fails). `1` — ran successfully and at least one finding at or above the threshold exists; both reports are still written. `2` — fail-closed input/parse/render error per R11; one sanitized line on stderr, no output file written or truncated. `3` — usage error (bad flag, unknown detector slug, unwritable output directory), argparse-style message on stderr. An `--explain` failure never changes the exit code and never converts a 0 into a 1.

- R40 (stdout/stderr discipline — pinned): `analyze` writes nothing to stdout on success except one line naming the written paths and the finding counts by severity. No progress bars, no spinners, no color, no wall-clock durations — a run's stdout is a deterministic function of the trace, the flags and the output paths. Diagnostics go to stderr.

### LLM narrator

- R41 (client seam — pinned): `swarm_observer/narrate/client.py` defines `NarratorClient`, a `typing.Protocol` with `complete(request: NarrationRequest) -> NarrationResponse`, plus the frozen models and the error taxonomy `NarratorError` (base) with `NarratorTransportError`, `NarratorAuthError`, `NarratorResponseError` — all single-line messages that never echo request or response bodies. `narrate/fixture.py` provides `FixtureNarratorClient` in the spend-sentinel/draftsmith house style: constructed from an ordered script of `NarrationResponse | NarratorError`, recording every request in `self.calls`, raising script entries that are exceptions, and raising `AssertionError` on script exhaustion (a test bug, not product behavior). `narrate/adapters/anthropic.py` is the only module importing `anthropic`, lazily, and is not required by the offline suite.

- R42 (what is sent — pinned, and it is not trace text): The `--explain` request payload is built by `narrate/summary.py` from findings only, and contains exclusively: the detector slug, severity, `metrics` values that are integers, enumerated slugs, and `tool_name` values already constrained by R16; per-severity counts; per-agent and per-model token and cost totals; the rate snapshot version. It contains **no** `previews`, no span text, no tool arguments, no tool results, no file names, no paths, no `trace_id`. A test asserts, over a fixture corpus whose every free-text field is a distinctive sentinel string, that no sentinel appears anywhere in the serialized request. This is the property that makes `--explain` safe to point at a real provider even when the trace contains secrets.

- R43 (fallback semantics — pinned, byte-identical default): 
  - `--explain` is off by default. With it off, `narrate/` is never imported by the analyze path, no `<section id="narrative">` element exists, and no bytes anywhere in either report differ from a build with the flag absent.
  - With `--explain` on, the narrator is asked for one paragraph per *finding group* (a group = one detector slug) plus one overall paragraph. Each response paragraph is validated: non-empty, `<= 800` characters after normalization, no control characters. 
  - Fallback is **per group**: any group whose paragraph is missing, invalid, or lost to a `NarratorError` renders the deterministic template paragraph for that group instead, and the group's paragraph is marked in the DOM with the fixed class `narrative-fallback` and the visible prefix `Deterministic summary:`. A transport failure, an auth failure, a missing `anthropic` package, an absent API key, or a timeout all take the same path: every group falls back, the run continues, and the exit code is unchanged (R39).
  - Narrator output is model-produced and therefore untrusted: it passes through `redact` then `escape_html` exactly like trace text (R32, R33) and is subject to R34 in full.
  - The narrative section is purely additive at a fixed anchor: a test strips `<section id="narrative">…</section>` from an `--explain` render and asserts the remaining bytes are identical to the no-`--explain` render of the same trace.

### Cross-cutting: offline, security, determinism, suite integrity

- R44 (import boundaries — pinned by a test): A test walks the AST of every module under `swarm_observer/` and asserts: `model` imports no other swarm_observer subpackage; `ingest` imports only `model` (and `ingest`); `ingest.claude_code` is imported by nothing outside `ingest`; `detect` imports only `model` (and `detect`); `cost` imports only `model` (and `cost`); `report` imports only `model`, `detect` (for `Finding`), `cost` (for its result types), and `report`; `narrate` imports only `model`, `detect` and `narrate`; `cli` may import anything; nothing imports `cli`. `anthropic` appears only in `narrate/adapters/anthropic.py`. No module outside `report/escape.py` defines a function named `escape_html` or a character-replacement table. `report/` imports nothing from `ingest`.

- R45 (offline suite — pinned, CI-enforced): The entire test suite passes with no network access, with `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`, `OPENAI_API_KEY` and `GH_TOKEN` unset (the spend-sentinel `env -u` pattern), and with `anthropic` **not installed**. CI runs that job on Python 3.11 and 3.12 alongside `ruff check`, `ruff format --check` and `mypy --strict`. The `live_narrator` marker is always deselected in CI.

- R46 (no egress on the default path — pinned): With `--explain` absent, the process opens no socket. Asserted by a test that installs a `socket.socket` subclass raising on construction for the duration of a full end-to-end analyze, plus an audit hook on `socket.connect`.

- R47 (determinism — pinned, and this is what "byte-identical" covers): For a fixed set of input files, a fixed CLI invocation, and a fixed rate snapshot, the **complete bytes** of `report.html` and `report.json`, and the complete bytes of stdout, are identical across: repeated runs in one process; separate processes; `PYTHONHASHSEED` ∈ `{0, 1, 2, "random"}`; different working directories and different absolute paths to the same file content; `TZ` ∈ `{UTC, America/Los_Angeles, Asia/Kolkata}`; `LC_ALL` ∈ `{C, en_US.UTF-8}`; and the order in which the paths are given on the command line. Concretely this forbids: any `datetime.now`/`time.time` in output; any `set`/`dict` iteration that is not explicitly sorted; any `id()`, `hash()`, `uuid4`, or `random`; any absolute path, username, hostname, PID or environment value in output; any float in output bytes; any locale-dependent number or date formatting (all timestamps render as `YYYY-MM-DDTHH:MM:SS.mmmZ` in UTC, all numbers with `str`/`Decimal` formatting only). The report's provenance line names the swarm-observer version, the rate snapshot version/date, and the **trace's own** last timestamp — never the current time.

- R48 (suite integrity: detector coverage — pinned): A test enumerates `detect.registry.ALL_DETECTORS` and asserts, for every detector: (a) at least one fixture in `tests/fixtures/traces/` produces at least one finding from it, and (b) at least one fixture produces zero findings from it. Both arms are required — (a) alone lets a detector that fires on everything pass, (b) alone lets a dead detector pass. A second test AST-scans `detect/` and asserts every module defining a `Detector`-shaped object is present in `ALL_DETECTORS`, so a detector cannot exist outside the registry and thereby outside (a). Adding a detector without fixtures fails the suite.

- R49 (suite integrity: no silent skips, loud collection errors — pinned): This project's predecessor shipped three CI checks that reported green while structurally unable to fail — a Terraform fixture set with no case that could trip the check, an undeclared test dependency that caused 148 tests to collect zero and report as skipped, and a live-replay path that could never have passed. The response is three enforced rules:
  - Optional test dependencies are imported at **module scope**, never inside a `pytest.importorskip` and never behind a `skipif`, so a missing one is a **collection error** that fails the run, not a skip that hides it.
  - `tests/collection_floor.json` maps each test module path to a minimum collected-test count, checked in and updated deliberately. A `pytest_collection_finish` hook fails the session if any listed module collects fewer than its floor, or if a listed module collects nothing at all. Adding tests never breaks the floor; a module silently collecting zero always does.
  - A `pytest_terminal_summary` hook fails the session if any test was skipped for a reason not present in the checked-in allowlist `tests/allowed_skips.txt` (each entry an exact reason string). The `live_narrator` marker's deselection is the only entry expected in CI; deselection is additionally verified by a test asserting the marker deselects a **non-zero** count.

- R50 (suite integrity: every guard has a proof it can fail — pinned): For each check that could be disabled by its environment or by an inert input, `tests/canaries/` holds a test that deliberately breaks the guard's subject and asserts the guard raises. Required canaries in v1: the determinism harness fails when the renderer is monkeypatched to embed `time.time()`; the golden-file comparison fails when one byte of a golden file is flipped in memory; the injection probe (R51) fails when `escape_html` is monkeypatched to the identity function; the attribute-allowlist test fails when a trace-derived string is injected into a `data-` attribute; the offline test fails when a socket connect is permitted and attempted; the redaction idempotence test fails when a pattern is removed from the table; the R48 coverage test fails when a detector is removed from a fixture's expectations. A canary that passes when it should fail is itself a suite failure.

- R51 (end-to-end injection probe — pinned, asks what reaches the user): `tests/fixtures/traces/hostile.jsonl` is a syntactically valid transcript in which every free-text field — agent descriptions, tool names, tool inputs, tool results, assistant text, model ids, error details — carries a payload from a checked-in corpus including at minimum: `</script><script>alert(1)</script>`, `"><img src=x onerror=alert(1)>`, `javascript:alert(1)`, `<!--`, `]]>`, `&lt;script&gt;`, a `data:` URI, a ` `-bearing string, a right-to-left override, an 80,000-character string, a `{{7*7}}` template probe, `../../etc/passwd`, an `AKIA`-shaped key, an `sk-ant-`-shaped key, and a PEM private key block. The probe renders the full report and then asserts against the **rendered file as a browser would see it**, parsed by `html.parser`: the document contains exactly one `<script>` and one `<style>` whose SHA-256 match the R34 constants; no attribute value falls outside the R34 allowlist; no `on*` attribute exists anywhere; no URL-bearing attribute exists; every payload appears only inside text nodes; and the credential-shaped payloads do not appear at all (they are `[redacted:...]`). The same probe runs against `--no-previews`, where none of the payloads appear anywhere in the file.

- R52 (suite integrity: requirement traceability — pinned): Every requirement id `R1`…`Rn` in this spec is cited by at least one test's id or docstring. A test parses this spec file, extracts the requirement ids, extracts the cited ids from the test suite, and asserts set equality in both directions — an uncited requirement fails, and a citation of a non-existent requirement fails.

## Out of scope

Explicitly deferred and documented in the README as future work; the coder builds none of it:

- **Replay.** Re-executing a trace against a live or mocked agent runtime. v1 defines only the seam: `swarm_observer/replay/target.py` contains a `ReplayTarget` `Protocol` (`replay(trace: Trace, selection: ReplaySelection) -> ReplayResult`) and the two frozen models, with **no implementation, no CLI subcommand, no import from anywhere else in the package**, and a test asserting exactly that. Adding a target in v2 is additive.
- Live capture, an SDK, an OTel exporter/receiver, instrumentation of the observed system, or any in-process hook. Zero-instrumentation is the product promise.
- Any trace format other than Claude Code JSONL (`adapter = "claude_code_jsonl"`). No LangGraph, CrewAI, AutoGen, OpenAI Assistants or generic OTel adapter in v1 — the `TraceSource` protocol (R3) is the seam.
- A server, a web UI, a hosted dashboard, live tailing, `--watch`, or incremental/streaming analysis. One command, one input set, two files out.
- A storage backend, a database, an index, multi-user access, authentication, authorization, or any persisted state between runs. Nothing is written except the two named output files.
- Distributed collection, agents pushing traces anywhere, or any outbound request other than the single optional `--explain` call.
- Cross-trace analysis: comparing two runs, trend lines over time, regression detection, baselines, or a "cost over the last 30 days" view. One trace per invocation.
- Finding suppression, baselining, `.swarm-observer-ignore`, or per-finding annotations. `finding_id` (R15) is stable so v2 can add them.
- Live or historical pricing lookups, currency conversion, negotiated/enterprise rates, batch-API or priority-tier rates, per-organization discounts.
- Statistical or LLM-based *detection*. Every detector is deterministic (R13); `--explain` narrates findings and never creates, ranks, suppresses or modifies one.
- PDF/Markdown/CSV report formats; report theming, branding, or configuration of section order.
- Detector thresholds as user configuration beyond `--blocked-gap-seconds` (R25).
- Repairing, rewriting, normalizing-in-place or re-emitting trace files.

## Dependencies

- Build/runtime, pinned in `pyproject.toml` (hatchling build backend, `requires-python = ">=3.11"`, CI matrix 3.11/3.12):
  - `pydantic >= 2.7, < 3` — every model and all validation.
  - Standard library only for everything else: `argparse`, `json`, `hashlib`, `re`, `decimal`, `datetime`, `html.parser` (tests), `importlib.resources` (snapshot loading). **No** templating engine, **no** HTML library, **no** charting library, **no** `httpx` in the core.
  - Optional extra `[explain]` → `anthropic >= 0.30, < 1`. Never required; the offline suite runs with it absent (R45).
- Dev: `pytest >= 8, < 9`, `pytest-cov`, `ruff >= 0.5`, `mypy >= 1.10`. No plugin that can silently skip; `pytest-randomly` is explicitly not used (it would fight R47's determinism harness).
- Package data: `swarm_observer/cost/data/model_rates.json`.
- Internal ordering: T1 blocks everything. T2 (model) blocks T3–T5 and every later area. T3–T4 (reader, mapper) block T6 (fixture corpus) and T7–T9 (detectors). T5 (suite-integrity harness) is built in increment 1 so increments 2–5 are checked by it from the start — building it last would repeat exactly the defect it exists to prevent. T6 blocks T7–T9 and T10. T11–T12 (rate snapshot, cost engine) block T13 (JSON output + `analyze` wiring), which carries the increment-3 end-to-end AC. T14 (escape/redact) blocks T15 (HTML) and T16 (timeline); T14–T16 block T17 (injection, determinism and no-egress probes). T18 (narrator) depends only on T7's `Finding` and T15's narrative anchor. T19 and T20 depend on everything but block nothing.

## Task breakdown

**Increment 1 — feature/so-i1 (scaffold, normalized model, Claude Code adapter, suite-integrity harness):**

- T1: Repo scaffold — `pyproject.toml` with the pins and the `[explain]` extra, package layout per Modularity notes, `ruff` + `mypy --strict` config, pytest wiring with the `live_narrator` marker registered, CI workflow with the env-scrubbed offline job (R45) and the matrix. (Supports all Rs.)
- T2: Normalized model + schema — `model/trace.py` with every model of R2, `TRACE_SCHEMA_VERSION`, and the `schema` subcommand's deterministic JSON Schema output. Round-trip and frozen/extra-forbid tests. (R1, R2, R38 partial.)
- T3: Fail-closed reader — `ingest/source.py` protocol, `IngestLimits`, `TraceError` taxonomy, line-wise JSONL reader with all R11 caps, SHA-256 of each source file, atomic-write helper. Table-driven tests over malformed and hostile inputs: truncated line, non-object line, oversized line, oversized file, duplicate uuid, bad timestamp, symlink escape, unreadable path — each asserting exit code 2, a single sanitized stderr line, no traceback, and no output file created. (R3, R4 fatal half, R11.)
- T4: Claude Code mapper — raw record models with `extra="allow"`, the R4 tolerated/counted split, the R9 stream-fragment collapse with its pinned naive-vs-collapsed constants, R5 ids, R6 canonical order, R7 digests, R8 previews, R10 warning taxonomy, R12 span-kind mapping. (R4–R10, R12.)
- T5: Suite-integrity harness — `tests/collection_floor.json` + `pytest_collection_finish` hook, `tests/allowed_skips.txt` + `pytest_terminal_summary` hook, the spec-traceability test (R52), the AST import-boundary test (R44), the `tests/canaries/` scaffold with the two canaries whose subjects already exist (golden-byte-flip, determinism-harness). (R44, R49, R50 partial, R52.)

**Increment 2 — feature/so-i2 (detectors + fixture corpus):**

- T6: Fixture corpus — checked-in `tests/fixtures/traces/*.jsonl`, hand-authored against the R2/R4 schema and cross-checked for structural fidelity against real transcript shape, covering at minimum: a clean single-agent run; a multi-agent run with a subagent; a duplicate-tool-call run; a three-repeat loop; a retry storm; a run with unknown tools and orphan results; a run with a long unexplained gap and a long *explained* gap; a token/duration outlier run; a truncated run ending on a dangling tool_use; an api-error/rate-limit run; a compaction-boundary run; `hostile.jsonl` (R51). Each fixture ships a checked-in expectation file naming which detectors must and must not fire. (Supports R48, R51.)
- T7: Detector contract + registry + waste attribution — `detect/base.py`, `Finding`, `finding_id`, `Severity`, `DetectorConfig`, `registry.py`, R17 attribution helper. (R13–R17, R25.)
- T8: Waste and loop detectors — `repeated_tool_call`, `agent_loop`, `anomalous_span`, including the pinned lower-median/MAD integer statistics. Table-driven tests including the boundary cases (`n == 1`, population size 7 vs 8, `mad == 0`). (R18, R19, R24.)
- T9: Failure and timing detectors — `retry_storm`, `failed_tool_call`, `unresolved_tool_call` with the truncation carve-out, `blocked_agent` with the explained-gap rule. (R20–R23.)
- T10: Detector-coverage integrity — the R48 both-arms test and the registry AST scan; the R50 canary that fails when a detector is dropped from a fixture's expectations. (R48, R50.)

**Increment 3 — feature/so-i3 (cost accounting + JSON report + analyze wiring):**

- T11: Rate snapshot + source — `cost/source.py` protocol, `SnapshotRateSource`, curated `model_rates.json` with `meta` provenance for the model families appearing in the fixture corpus, the R27 resolution ladder. Snapshot-shape tests (every model entry has all five price keys; every rate parses as `Decimal`; alias targets exist). Owner rate sanity-check at this increment's checkpoint. (R26, R27.)
- T12: Cost engine — R28 formula, R29 Decimal discipline, R30 unpriced taxonomy with its priority order, R31 aggregations. Golden tests computing expected micro-dollars **from the snapshot**, not from hard-coded totals; explicit tests for the thinking-token and cached-token non-double-count. (R28–R31.)
- T13: JSON report + `analyze` wiring — `report/json_out.py`, the `analyze` subcommand end to end (ingest → detect → cost → JSON), `detectors` subcommand, exit codes, stdout discipline, `--fail-on`, `--detector`, `--no-previews` plumbed to ingestion. First end-to-end AC lands here. (R36 JSON half, R38–R40; AC7.)

**Increment 4 — feature/so-i4 (HTML report + security probes + determinism):**

- T14: Escaping + redaction — `report/escape.py` (single definition, R32 table), `report/redact.py` (R33 ordered patterns, idempotence property test), the adversarial redaction corpus. (R32, R33.)
- T15: HTML renderer — R36 section order, the constant `<script>`/`<style>` with pinned SHA-256, the attribute allowlist, the CSP meta, golden-file tests for the whole document. (R34, R36.)
- T16: Timeline — R37 integer-only SVG geometry, including the zero-duration and null-timing cases. (R37.)
- T17: Security + determinism probes — the R51 end-to-end injection probe (both with and without `--no-previews`), the R35 no-external-request parser test, the R34 script/attribute tests, the R46 no-socket test, and the full R47 determinism matrix (repeat runs, subprocess, `PYTHONHASHSEED`, `TZ`, `LC_ALL`, CWD, path order). Canaries for each of them (R50). (R34, R35, R46, R47, R50, R51; AC3, AC5, AC6.)

**Increment 5 — feature/so-i5 (narrator, replay seam, docs):**

- T18: Narrator layer — `narrate/client.py` protocol + error taxonomy, `FixtureNarratorClient`, `narrate/summary.py` with the R42 sentinel test, per-group validation and fallback, the additive-section byte-identity test, the `anthropic` adapter with lazy import and sanitized errors, `live_narrator`-marked opt-in smoke test gated on `SWARM_OBSERVER_LIVE_NARRATOR=1`. (R41–R43.)
- T19: Replay seam — `replay/target.py` with the `ReplayTarget` protocol and models only, plus the test asserting nothing imports it and no implementation exists. (Out of scope section.)
- T20: Docs — README (quickstart needing no credentials, the zero-instrumentation promise, the threat model and the "redaction is a courtesy, not a boundary" statement, `--no-previews` guidance for sharing reports, rate-snapshot provenance and staleness caveat, detector reference), `docs/CASE-STUDY.md`, and a `docs/TRACE-SCHEMA.md` documenting the versioned normalized model and the adapter contract for v2 sources.

## Acceptance criteria

- AC1 (R9, cost correctness): Given a fixture transcript containing one API response written as four streamed assistant records sharing a `requestId` and `message.id`, where the terminal fragment reports `output_tokens: 483` and the earlier three report `output_tokens: 6`, When the mapper runs, Then exactly one `model_call` span exists for that group, its `usage.output_tokens == 483`, its content is the union of the four fragments' blocks deduplicated by block identity, and the trace's total output tokens equal the pinned collapsed constant and **not** the pinned naive constant.
- AC2 (R4, R10, R11): Given a trace file containing (a) a record with an unknown `type`, (b) a record with three unknown top-level keys, and (c) a record with an unknown content-block type, When analyze runs, Then it exits 0, the unknown-typed record produced no span, `warnings` contains `unknown_record_type` with count 1 and the type slug in `detail`, `extras_dropped == 3` on the affected span, and no unknown key's *content* appears anywhere in either output file. And Given a trace file whose fifth line is `{"type":"user"` (truncated JSON), When analyze runs, Then it exits 2, stderr is exactly one line matching `^swarm-observer: [a-z_]+: `, no traceback is printed, no output file exists at either output path, and any pre-existing file at those paths is unmodified.
- AC3 (R51, end-to-end injection — asks what reaches the user): Given `tests/fixtures/traces/hostile.jsonl`, When `analyze --out report.html --json report.json` runs and `report.html` is parsed with `html.parser`, Then the document contains exactly one `<script>` and one `<style>` whose SHA-256 equal the pinned constants; every attribute value matches the R34 allowlist; no `on*` or URL-bearing attribute exists; every hostile payload occurs only in text nodes; `AKIA…`, `sk-ant-…` and the PEM block appear nowhere and their `[redacted:…]` markers do; and `report.json` parses as JSON with the same payload/redaction properties. And When the same run adds `--no-previews`, Then no hostile payload appears anywhere in either file.
- AC4 (R32, R33): Given the string `` </script><img src=x onerror=alert(1)>&`=" ``, When `escape_html` runs, Then every one of `&`, `<`, `>`, `"`, `'`, `/`, `` ` ``, `=` is entity-encoded exactly once — the output contains `&lt;` and not `&amp;lt;`, proving the single-pass implementation rather than sequential `str.replace` — and a `\x00` and a `\x1b` in the input each become a single space. `escape_html` is deliberately **not** idempotent (it is applied exactly once, at the render boundary); a test asserts it is called exactly once per rendered string by instrumenting the renderer. And Given any string in the adversarial redaction corpus, Then `redact(redact(s)) == redact(s)`.
- AC5 (end-to-end, R36, R38, R39): Given a multi-agent fixture trace with a known finding in every detector class, When `swarm-observer analyze <fixtures> --out report.html --json report.json --fail-on critical` runs, Then the exit code is 1, both files are written, `report.json` validates against the R1 schema version, every detector in `ALL_DETECTORS` appears in the findings, every `model_call` span appears in the cost section either priced or in `unpriced` with a reason from the R30 enum, the four R31 groupings' totals each equal the sum of their members, and the HTML sections appear in the exact R36 order.
- AC6 (determinism, R47): Given the same input trace and invocation, When analyze is run twice in one process, twice in separate subprocesses, and once under each of `PYTHONHASHSEED` ∈ `{0, 1, random}`, `TZ` ∈ `{UTC, America/Los_Angeles, Asia/Kolkata}`, `LC_ALL` ∈ `{C, en_US.UTF-8}`, from two different working directories, via two different absolute paths to byte-identical copies of the input, and with the input paths given in reversed order, Then the SHA-256 of `report.html`, of `report.json` and of stdout are identical in every case.
- AC7 (R28–R31): Given a trace with one `model_call` span carrying `input_tokens: 10, cache_creation_5m: 17971, cache_read: 0, output_tokens: 483` on a model present in the snapshot, one span on model `claude-haiku-4-5-20251001` where only `claude-haiku-4-5` is in the snapshot, one span on an unknown model id, one api-error span with `model: "<synthetic>"`, and one span with `usage: None`, When the cost engine runs, Then the first span's cost equals the R28 formula computed from the snapshot to 6 decimal places; the second span is priced via the R27 longest-prefix rule; the third is unpriced with `model_not_in_snapshot`; the fourth with `synthetic_span`; the fifth with `usage_missing`; and the trace total equals the sum of the two priced, already-quantized span costs exactly.
- AC8 (R19): Given an agent whose signature sequence is `A B A B A B C`, When `agent_loop` runs, Then exactly one finding fires with `period == 2`, `repeats == 3`, `start_seq` pointing at the first `A`; Given `A B A B C`, Then no finding fires; Given `A A A A A`, Then one finding fires with `period == 1`, `repeats == 5`, severity `critical`.
- AC9 (R22, truncation carve-out): Given a trace whose final span is a `tool_call` with no matching result, When `unresolved_tool_call` runs, Then no `no_result` finding fires for it and the trace carries the parse warning `dangling_tool_use` with count 1; And Given a trace with a mid-trace unmatched `tool_use` **and** a final unmatched one, Then exactly one `no_result` finding fires, naming only the mid-trace span.
- AC10 (R23): Given an agent with a 120-second gap during which a subagent's spans cover 90 seconds of it, When `blocked_agent` runs, Then no finding fires (75% coverage ≥ 50%); Given the same gap with 30 seconds of subagent coverage, Then one finding fires with `gap_seconds == 120` and severity `warning`; Given a 400-second unexplained gap, Then severity is `critical`.
- AC11 (R24): Given a population of 7 `model_call` spans including an extreme outlier, When `anomalous_span` runs, Then no finding fires (population floor); Given 8 spans with values `[100]*7 + [50_000]`, Then `med == 100`, `mad == 0`, and the outlier fires (`50_000 > 100 + 0` and `>= 10_000`); Given 8 spans all equal to `100`, Then no finding fires.
- AC12 (R42, R43): Given `--explain` with a `FixtureNarratorClient` scripted to return valid paragraphs for two of four finding groups and raise `NarratorTransportError` on the third and return an 900-character paragraph on the fourth, When analyze runs, Then the exit code is unchanged from the no-`--explain` run, the two valid paragraphs render, the other two render the deterministic template with class `narrative-fallback` and the `Deterministic summary:` prefix, and stripping `<section id="narrative">…</section>` from the output yields bytes identical to the no-`--explain` render. And Given a fixture trace whose every free-text field is a distinctive sentinel, When the `--explain` request payload is serialized, Then no sentinel appears in it.
- AC13 (R45, R46): Given an environment with every R45 variable unset and `anthropic` not installed, When the full non-`live_narrator` suite runs, Then it passes; and When a full `analyze` runs without `--explain` under a `socket.socket` subclass that raises on construction, Then the run completes normally.
- AC14 (R48, R49, R50 — the suite can actually fail): Given the fixture corpus, When a detector is added to `ALL_DETECTORS` without a fixture that fires it, Then the R48 test fails; When a detector's fixture expectations are emptied, Then the R48 test fails; When a test module is made to collect zero tests, Then the `collection_floor` hook fails the session with a message naming that module; When a test is skipped with a reason absent from `allowed_skips.txt`, Then the session fails; When `escape_html` is monkeypatched to the identity function, Then the R51 probe fails; When the renderer is monkeypatched to embed `time.time()`, Then the R47 harness fails. Each of these is a checked-in canary test that asserts the failure occurs.
- AC15 (R44, R52, seams): Given the package source, When the AST boundary test runs, Then every rule of R44 holds, `report/` imports nothing from `ingest/`, `anthropic` appears only in `narrate/adapters/anthropic.py`, and `replay/target.py` is imported by nothing; And When the traceability test runs, Then the set of requirement ids in this spec equals the set cited across the test suite.
- AC16 (R38, R39): Given `analyze` on a directory containing three `*.jsonl` files and one `*.txt`, Then only the three JSONL files are ingested, ordered by basename; Given `--detector no_such_detector`, Then exit 3 with a usage message and no output written; Given a `--out` path in a non-existent directory, Then exit 3; Given a 300 MiB input file with default limits, Then exit 2 naming the limit; Given a clean trace with `--fail-on none`, Then exit 0 even though findings exist.

## Security considerations

Each item is a testable statement; the tests are named in the mapped requirements and ACs.

- **Why this threat model is stronger than the previous two projects'.** In spend-sentinel the untrusted string came from the user's own Terraform plan; in draftsmith, from a model this codebase prompted with a system prompt it controlled, into a closed pydantic schema. Here the untrusted string was produced by *someone else's agent*, which may have processed a hostile web page, repository, issue comment or document, and which had no obligation to constrain its output. The output medium is worse too: a `.html` file that a human opens in a browser with the local file origin and then forwards to a teammate. So the trace is an attacker-controlled channel into a rendered document, and swarm-observer's core safety claim is that no trace byte can leave a text node.
- **HTML/script injection fails closed** (R32, R34, R51, AC3): a single `escape_html` definition with a table wider than the text-node minimum; trace text confined to text nodes; exactly one `<script>` and one `<style>`, both constant and SHA-256-pinned so interpolation is impossible by construction rather than by discipline; an attribute-value allowlist verified by parsing the rendered document; a `default-src 'none'` CSP meta. The probe (R51) asserts against the parsed output — what reaches the user — not against a function's return value, which is the specific lesson from draftsmith's increment-5 review.
- **No exfiltration surface in the report** (R35, AC3): no external URL of any scheme, no `<img>`, `<link>`, `<iframe>`, `@import` or form; the CSP forbids every fetch a bypass might attempt. A report opened offline in an airgapped browser renders identically. This matters because a beacon in a shared report would leak the *contents* of the trace, not just the fact of viewing.
- **Credential redaction, honestly scoped** (R33, AC4): an ordered, idempotent pattern table covering AWS, Anthropic, OpenAI, GitHub, Slack and Google key shapes, JWTs, bearer headers, PEM blocks, and `NAME=value` assignments where the name contains a secret-ish word. The README and the report header both state that redaction is a courtesy that reduces accidental exposure and **cannot** defeat an adversary who controls the trace. `--no-previews` (R38) is the hard guarantee: no trace free text in the output at all, only digests, counts and enumerated slugs — the mode to use when sharing a report outside the team.
- **Findings are injection-free before escaping** (R16): `Finding.summary` is built only from integers and swarm-observer's own slugs, and the one trace-derived value allowed into `metrics` (`tool_name`) must match a strict pattern or be replaced. A reader skimming the findings table is reading bytes this codebase authored.
- **Fail-closed parsing** (R4, R11, AC2): hostile or malformed input exits 2 with one sanitized line that can name a basename, a line number and a limit but never a byte of content; no traceback, no partial result, no output file created or truncated (temp-file-plus-atomic-rename). Size caps on file bytes, line bytes, record count and file count bound the memory a malicious 40 GB "trace" can consume.
- **Path handling** (R5, R11, R47): only basenames reach output; `trace_id` is computed from content, not paths, so a report cannot leak a directory structure or a username, and two people analyzing the same trace from different machines get byte-identical reports.
- **Minimal egress** (R41, R42, R43, R46, AC12, AC13): the default path opens no socket, asserted by a socket-blocking test. The only outbound call in the product is `--explain`, whose payload is built from findings and provably contains no trace text (sentinel test), so pointing `--explain` at a provider cannot forward a secret that was sitting in a tool result. Credentials come only from the environment; no flag, file or trace field supplies a key; narrator errors are sanitized single lines that never echo payloads.
- **Untrusted model output** (R43): the narrator's response is treated exactly like trace text — redacted, escaped, text-node only, length-validated — because a compromised or prompt-injected narrator is just another attacker-influenced string source.
- **Supply chain**: one runtime dependency (`pydantic`), no HTML/templating/charting library, no frontend build step, no vendored JavaScript. The report's entire script is a constant this repo authored and hashes.
- **Suite integrity as a security property** (R48–R52, AC14): every guard above is only as good as the test that proves it can fail, which is why R50's canaries are required rather than encouraged. A green run in this repo means the checks ran and the inputs were capable of tripping them.

## Modularity notes

Package layout (enforced by the R44 AST test — these are rules, not suggestions):

```
swarm_observer/
  model/
    trace.py           # Trace, AgentRun, Span, TokenUsage, SourceFile, ParseWarning,
                       # SpanError, TRACE_SCHEMA_VERSION.  Imports: stdlib + pydantic only.
  ingest/
    source.py          # TraceSource protocol, IngestLimits, TraceError taxonomy
    reader.py          # line-wise JSONL reading, caps, hashing, atomic writes
    registry.py        # adapter slug -> TraceSource
    claude_code/
      records.py       # raw record models, extra="allow"  (ONLY place adapter field names appear)
      mapper.py        # raw records -> normalized Trace; R9 fragment collapse
  detect/
    base.py            # Detector protocol, Finding, Severity, DetectorConfig, waste attribution
    registry.py        # ALL_DETECTORS — single source of truth
    repeated_tool_call.py  agent_loop.py  retry_storm.py
    failed_tool_call.py    unresolved_tool_call.py  blocked_agent.py  anomalous_span.py
  cost/
    source.py          # RateSource protocol
    snapshot.py        # SnapshotRateSource
    compute.py         # R28 formula, R30 taxonomy, R31 aggregation
    data/model_rates.json
  report/
    escape.py          # the single escape_html definition
    redact.py          # the single redact definition
    html.py            # golden-file testable; owns the constant <script>/<style>
    timeline.py        # integer-only SVG geometry
    json_out.py
  narrate/
    client.py          # NarratorClient protocol, error taxonomy, request/response models
    summary.py         # findings -> request payload; NO trace text (R42)
    narrator.py        # per-group validation and fallback
    fixture.py         # FixtureNarratorClient
    adapters/anthropic.py   # ONLY module importing anthropic (lazily)
  replay/
    target.py          # ReplayTarget protocol + models ONLY — no implementation, no importers
  cli/
    main.py            # argparse, exit codes, wiring; the ONLY place adapters and the
                       # narrator client are selected
tests/
  fixtures/traces/  canaries/  golden/  (unit tests mirror package layout)
  collection_floor.json  allowed_skips.txt
```

Import rules (each an assertion in the R44 test): `model` → stdlib + pydantic only; `ingest` → `model`; `ingest.claude_code` imported by nothing outside `ingest`; `detect` → `model`; `cost` → `model`; `report` → `model`, `detect`, `cost` (and **never** `ingest`); `narrate` → `model`, `detect`; `replay` → `model`, and imported by nothing; `cli` → anything; nothing imports `cli`. `anthropic` only in `narrate/adapters/anthropic.py`.

Other enforceable rules:

- **One definition of each boundary function.** `escape_html` and `redact` each have exactly one definition; a second character-replacement table anywhere in the package fails R44. This is the draftsmith `single_line` lesson: shape guarantees only compose when there is one implementation.
- **Guards are properties of functions, not of call paths.** Every renderer takes already-redacted-and-escaped strings or performs both itself at its own boundary; no renderer may assume its caller sanitized. The draftsmith review's recurring defect ("correct for the caller that existed, wrong for the next one") is the reason.
- **Detectors are pure.** No detector imports `cost`, `report`, `ingest`, `os`, `time`, `random` or `datetime.now`. Enforced by an AST test on `detect/`, not by review.
- **Only `cost/compute.py` consumes `RateSource`.** Money is `Decimal` from load until the final format call; a float literal or `float()` call anywhere in `cost/` or `report/` fails an AST test.
- **The registry is the source of truth.** A detector module not in `ALL_DETECTORS` is a failure (R48); a rate model reachable by no alias or prefix in the fixture corpus is reported by a snapshot-shape test.
- **The renderer never computes.** `report/` receives a finished `Trace`, a finished `tuple[Finding, ...]` and a finished cost result; it does no detection, no pricing and no aggregation, which is what keeps golden files meaningful.

## Open questions & assumptions

- A1 (real trace format — **found, inspected, and grounded**): Real Claude Code session transcripts were located on disk at `~/.claude/projects/<project-slug>/<session-uuid>/subagents/agent-<id>.jsonl` (11 files, 5,385 records, ~7 MB) and inspected directly. The observed shape — newline-delimited JSON objects; a `uuid`/`parentUuid` linked list; record `type` ∈ `{user, assistant, attachment, system}`; per-record fields `agentId`, `sessionId`, `requestId`, `isSidechain`, `timestamp`, `cwd`, `gitBranch`, `version`, `slug`, `entrypoint`, `apiBlockIndex`, `sourceToolAssistantUUID`, `toolUseResult`, `isMeta`, `isApiErrorMessage`/`apiErrorStatus`/`error`/`quotaLimits`, `subtype: "compact_boundary"` with `compactMetadata`; `message.usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` and a `cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens` breakdown; content blocks `text | thinking | tool_use | tool_result` — is what R4, R9, R10 and R12 are written against, and sidecar `agent-<id>.meta.json` files carry `agentType`, `description`, `spawnDepth` and the spawning `toolUseId`. **The format is undocumented and unversioned**, and six different `version` strings (2.1.247 … 2.1.266) appear within one session's files. The spec therefore treats it as an *observed* format behind the R3 adapter boundary and pins swarm-observer's own `TRACE_SCHEMA_VERSION` (R1) as the stable contract; R4's tolerated-and-counted rule exists precisely so a Claude Code release that adds a field or a record type degrades to a counted warning rather than an exit 2. v1 reads `agent-*.jsonl`; the `.meta.json` sidecars are read opportunistically for `AgentRun.agent_type`/`description`/`depth` when present next to an input file, and their absence is never an error.
- A2 (stream fragments are the single highest-risk misreading — R9): This is the assumption most likely to be got wrong by a fast implementation, and getting it wrong silently inflates every dollar figure in the product by ~55% on cache-heavy traces while every test still passes. It is pinned with measured constants for exactly that reason. If the observed format later changes such that fragments no longer repeat `usage`, the collapse becomes a no-op and the pinned constants must be re-measured — a deliberate, visible change, not a silent one.
- A3 (waste attribution is attribution, not counterfactual — R17): "Wasted tokens" sums the usage of model calls the trace shows were repeated. It does not claim the run would have cost that much less; a deduplicated run would have had different cache behavior. Both reports say so in one sentence. The alternative — omitting the number — was rejected because a cost-waste figure is the product's headline value and an honestly caveated estimate beats none.
- A4 (rates are a snapshot and will go stale — R26): The snapshot's `snapshot_date` is rendered in every report next to every dollar figure, and the README states that costs are list-price estimates at that date, exclude batch/priority tiers and any negotiated discount, and are not a billing reconciliation. Owner sanity-checks the curated rates at the increment-3 checkpoint.
- A5 (fixture corpus is hand-authored, not captured): The fixture traces are written by hand against the documented schema rather than captured from real sessions, because real transcripts contain the team's own prompts, file contents and credentials-adjacent output and must not be committed. Their structural fidelity is maintained by a checked-in "shape contract" test derived from the A1 inspection (record types, key names, usage-block shape, fragment repetition) rather than by committing real data. Risk accepted and stated: a fixture corpus can drift from reality, which is a v2 argument for an opt-in "validate against a local real transcript" command that reads a path but commits nothing.
- A6 (single-trace scope): One invocation analyzes one logical trace — a set of files that belong to one session. Cross-file record ordering is by sorted basename (R6), which for `agent-<id>.jsonl` files means agent-id order, not chronological order. This is deliberate (determinism beats a clock we do not trust) and is why `AgentRun` carries timing but `seq` does not.
- A7 (detector overlap is intentional): `retry_storm` and `failed_tool_call` can both fire on the same spans, as can `agent_loop` and `repeated_tool_call`. Overlap is not deduplicated: a density signal and an existence signal are different information for the reader. The report groups by detector so the duplication is legible rather than confusing.
- A8 (`unknown_tool` pattern list is heuristic — R22): The five phrases matched are drawn from observed runtime error text and are the only heuristic string matching against trace content in the whole product. A miss degrades a finding from `unknown_tool` to `failed_tool_call`, never to a crash or a false security claim. Listed as an open question: if Trey wants it removed, the `no_result`/`orphan_result` reasons still carry the detector.
- A9 (severity thresholds are constants, not configuration — R25): Every threshold except `--blocked-gap-seconds` is a module constant so that a golden report is a function of the trace and one integer. Making them configurable is one dataclass away, and is deliberately deferred rather than half-built.
- A10 (`--no-previews` drops text at ingest, not at render — R8): Chosen so the hostile bytes do not exist in the process after ingestion, rather than existing and being trusted not to leak. The cost is that a `--no-previews` run cannot be re-rendered with previews without re-reading the trace, which is acceptable for a single-command tool.
- A11 (the collection floor will need occasional maintenance — R49): `tests/collection_floor.json` is checked in and must be raised deliberately when test modules are split or renamed. The friction is the point: the alternative is the failure mode this project's predecessor hit three times.
