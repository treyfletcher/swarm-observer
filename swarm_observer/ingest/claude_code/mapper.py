"""Claude Code records → the normalized trace (R5-R10, R12).

The one thing in this module that changes a number a human reads is **R9's
stream-fragment collapse**, so it is worth stating plainly what it does and why
a fast implementation gets it wrong.

A Claude Code transcript writes one JSONL record per streamed content block,
and every fragment of a single API response repeats that response's whole
``usage`` object; only the terminal fragment carries the final
``output_tokens`` and ``stop_reason``. Summing ``usage`` per record therefore
counts one response's cached input three or four times over. On the grounding
corpus (A1) that inflates cache-read tokens by 55.4%, cache-creation tokens by
55.3% and output tokens by 2.5% — and *nothing fails*: the run is green, the
report is well-formed, and every dollar figure is half again too big. The
collapse is not an optimization, it is the difference between the product being
right and being confidently wrong, which is why :func:`collapse_stats` exposes
both totals so a test can pin them side by side.

The rest of the module is R5's ids, R6's file-order-is-authoritative ordering,
R7's digests, R8's previews, R10's warning taxonomy and R12's span mapping.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from swarm_observer.ingest.claude_code.records import (
    KNOWN_BLOCK_TYPES,
    KNOWN_IGNORED_KEYS,
    ParsedRecord,
    RawBlock,
    RawUsage,
    block_text,
    content_blocks,
    message_text,
    parse_record,
)
from swarm_observer.ingest.reader import (
    JsonRecord,
    compute_trace_id,
    digest_id,
    load_input,
    resolve_inputs,
)
from swarm_observer.ingest.source import IngestLimits, TraceParseError
from swarm_observer.ingest.text import canonical_json, preview, preview_within, slug
from swarm_observer.model.trace import (
    AGENT_ID_PATTERN,
    DETAIL_MAX_CHARS,
    TRACE_SCHEMA_VERSION,
    AgentRun,
    ParseWarning,
    SourceFile,
    Span,
    SpanError,
    TokenUsage,
    Trace,
)

#: R3: the adapter slug this module registers under.
ADAPTER_SLUG = "claude_code_jsonl"

#: R12: the model id Claude Code records on a synthetic (non-billable) response.
SYNTHETIC_MODEL = "<synthetic>"

#: A1: the sidecar file read opportunistically for agent metadata. Its absence
#: is never an error and never changes a span.
SIDECAR_SUFFIX = ".meta.json"

#: Bound on the sidecar read. It is metadata, not a trace.
SIDECAR_MAX_BYTES = 1 << 20

_AGENT_ID_OK = re.compile(AGENT_ID_PATTERN)


def safe_agent_id(recorded: str | None) -> str:
    """R5: the agent id, guaranteed to carry no trace content.

    ``agentId`` is trace-derived, and R5 promises that ids are safe in an HTML
    attribute value (R32, R34). A recorded id that already looks like an
    identifier is kept verbatim — real ones do — and anything else is replaced
    by a deterministic digest id, which preserves the one property that matters
    (two records from the same agent map to the same id) without carrying a
    byte the attacker chose.

    ``fullmatch``, not ``match``: Python's ``$`` also matches immediately before
    a trailing newline, while the engine pydantic compiles ``AGENT_ID_PATTERN``
    with treats ``$`` as end-of-input. Under ``match`` an ``agentId`` of
    ``"a1\\n"`` therefore passed this guard verbatim and was then rejected by
    ``Span.agent_id``, raising an unsanitized ``ValidationError`` that quoted
    the trace — and had pydantic agreed instead, a newline-bearing id would have
    reached an HTML attribute value, which is the thing R5 exists to prevent.
    Two regex engines disagreeing about one metacharacter is not a detail to
    leave to the reader.
    """
    if not recorded:
        return "root"
    if _AGENT_ID_OK.fullmatch(recorded):
        return recorded
    return "agent_" + hashlib.sha256(recorded.encode("utf-8")).hexdigest()[:12]


def tool_input_digest(value: Any) -> str:
    """R7: 16 hex characters over the canonical JSON of a tool input."""
    canonical = canonical_json(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class WarningLog:
    """Aggregates R10 warnings into one entry per ``(code, detail)``."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}

    def add(self, code: str, detail: str = "", count: int = 1) -> None:
        if count <= 0:
            return
        key = (code, detail)
        self._counts[key] = self._counts.get(key, 0) + count

    def total(self, code: str) -> int:
        """How many times ``code`` fired, across all details. For assertions."""
        return sum(value for (name, _), value in self._counts.items() if name == code)

    def to_tuple(self) -> tuple[ParseWarning, ...]:
        """R2: sorted by ``(code, detail)``, one entry per pair."""
        return tuple(
            ParseWarning(code=code, count=count, detail=detail)  # type: ignore[arg-type]
            for (code, detail), count in sorted(self._counts.items())
        )


@dataclass(frozen=True, slots=True)
class CollapseStats:
    """Naive-versus-collapsed totals — the R9 regression guard's raw material.

    ``naive_usage`` is what per-record summation would produce; ``collapsed``
    is what the mapper produces. Pinning both in a test means a regression to
    naive summation fails loudly instead of quietly changing a dollar figure.
    """

    assistant_records: int
    model_calls: int
    naive_usage: TokenUsage
    collapsed_usage: TokenUsage


@dataclass
class _Group:
    """One collapsed API response: its fragments in canonical order (R9)."""

    key: tuple[object, ...]
    fragments: list[ParsedRecord] = field(default_factory=list)


def _usage_of(usage: RawUsage | None) -> TokenUsage:
    """Map a recorded usage block onto :class:`TokenUsage` (R2, R28).

    When the ``cache_creation`` breakdown is absent — expected on older
    transcript versions — the whole ``cache_creation_input_tokens`` amount is
    carried as 5-minute cache creation, which is what R28 prices it as. That
    absence is deliberately *not* a ``missing_usage`` warning.
    """
    if usage is None:
        return TokenUsage()
    if usage.cache_creation is not None:
        five_minute = usage.cache_creation.ephemeral_5m_input_tokens
        one_hour = usage.cache_creation.ephemeral_1h_input_tokens
    else:
        five_minute = usage.cache_creation_input_tokens
        one_hour = 0
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        cache_creation_5m_tokens=five_minute,
        cache_creation_1h_tokens=one_hour,
    )


def _block_identity(block: RawBlock, position: tuple[int, int]) -> tuple[Any, ...]:
    """R9: the identity two fragments' blocks are deduplicated by.

    ``id`` for ``tool_use``, ``tool_use_id`` for ``tool_result``, and
    ``(type, sha256(text))`` for ``text`` and ``thinking``. A block missing the
    key its type is identified by, or of an unknown type, falls back to its
    position — so it is never merged with a different block on a coincidence.
    """
    kind = block.type or ""
    if kind == "tool_use" and block.id:
        return ("tool_use", block.id)
    if kind == "tool_result" and block.tool_use_id:
        return ("tool_result", block.tool_use_id)
    if kind in {"text", "thinking"}:
        payload = block.text if kind == "text" else block.thinking
        digest = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()
        return (kind, digest)
    return ("position", kind, position)


class ClaudeCodeSource:
    """The v1 :class:`~swarm_observer.ingest.source.TraceSource` (R3, R12).

    ``no_previews`` drops trace free text at *model-build* time rather than at
    render time (A10): with it set, the hostile bytes do not exist in the
    process after ingestion, instead of existing and being trusted not to leak.
    It blanks the three preview fields (R8) and also the agent description and
    type, because R51's hostile corpus puts payloads in agent descriptions and
    the ``--no-previews`` arm of that probe asserts *no* payload appears
    anywhere in the output.

    ``read_sidecars`` controls A1's opportunistic ``agent-<id>.meta.json`` read.
    """

    def __init__(self, *, no_previews: bool = False, read_sidecars: bool = True) -> None:
        self.no_previews = no_previews
        self.read_sidecars = read_sidecars

    # -- public API ---------------------------------------------------------

    def load(self, paths: Sequence[Path], limits: IngestLimits) -> Trace:
        """Read ``paths`` and return the normalized trace (R3)."""
        loaded = load_input(paths, limits)
        warnings = WarningLog()
        parsed = self._parse_all(loaded.source_files, loaded.records, warnings)
        trace_id = compute_trace_id(loaded.source_files)
        builder = _TraceBuilder(
            trace_id=trace_id,
            parsed=parsed,
            warnings=warnings,
            no_previews=self.no_previews,
        )
        spans, agents_by_file, tool_use_agent = builder.build()
        sidecars = self._read_sidecars(paths, limits) if self.read_sidecars else {}
        agents = builder.build_agents(spans, agents_by_file, tool_use_agent, sidecars)
        return Trace(
            schema_version=TRACE_SCHEMA_VERSION,
            trace_id=trace_id,
            adapter=ADAPTER_SLUG,
            source_files=loaded.source_files,
            agents=agents,
            spans=spans,
            warnings=warnings.to_tuple(),
        )

    def collapse_stats(self, paths: Sequence[Path], limits: IngestLimits) -> CollapseStats:
        """Both R9 totals for ``paths``: what naive summation would say, and the truth.

        Exposed as product code rather than test code so the pinned constants in
        the suite are measured through the same parse the CLI uses.

        ``assistant_records`` and ``model_calls`` count every assistant record
        and every group, API-error records included; both token totals exclude
        API-error records, which are never billable (R12, R30). Both sides of
        the comparison treat them identically, so the ratio the constants pin is
        a property of the collapse and not of the exclusion.
        """
        loaded = load_input(paths, limits)
        warnings = WarningLog()
        parsed = self._parse_all(loaded.source_files, loaded.records, warnings)
        assistants = [item for item in parsed if item.record.type == "assistant"]
        naive = TokenUsage()
        for item in assistants:
            if item.record.is_api_error:
                continue
            message = item.record.message
            if message is not None and message.usage is not None:
                naive = naive.plus(_usage_of(message.usage))
        groups = _group_assistants(assistants)
        collapsed = TokenUsage()
        for group in groups:
            last = group.fragments[-1]
            message = last.record.message
            if last.record.is_api_error:
                continue
            if message is not None and message.usage is not None:
                collapsed = collapsed.plus(_usage_of(message.usage))
        return CollapseStats(
            assistant_records=len(assistants),
            model_calls=len(groups),
            naive_usage=naive,
            collapsed_usage=collapsed,
        )

    # -- parsing ------------------------------------------------------------

    def _parse_all(
        self,
        source_files: Sequence[SourceFile],
        records: Sequence[JsonRecord],
        warnings: WarningLog,
    ) -> list[ParsedRecord]:
        """R4 fatal checks, R6 ordering, the duplicate-uuid and clock warnings."""
        names = [item.name for item in source_files]
        seen_uuids: dict[int, set[str]] = {index: set() for index in range(len(names))}
        previous_when: dict[int, datetime] = {}
        parsed: list[ParsedRecord] = []
        for index, raw in enumerate(records):
            name = names[raw.file_index]
            item = parse_record(raw, source=name, index=index)
            uuids = seen_uuids[raw.file_index]
            if item.record.uuid in uuids:
                raise TraceParseError(
                    "duplicate_uuid", source=name, line=raw.line, note="uuid repeats in file"
                )
            uuids.add(item.record.uuid)
            # R6: within a file, a timestamp that goes backwards is counted and
            # ignored. Across files the order is basename order by design (A6),
            # so comparing across a file boundary would report the design as a
            # defect on every multi-agent trace.
            earlier = previous_when.get(raw.file_index)
            if earlier is not None and item.when < earlier:
                warnings.add("timestamp_out_of_order")
            previous_when[raw.file_index] = item.when
            parsed.append(item)
        return parsed

    # -- sidecars (A1) ------------------------------------------------------

    def _read_sidecars(
        self, paths: Sequence[Path], limits: IngestLimits
    ) -> dict[int, dict[str, Any]]:
        """Read ``agent-<id>.meta.json`` next to each input, if present (A1).

        Every failure mode — absent, unreadable, not JSON, not an object, too
        large, a symlink — is silently no metadata. The sidecar may not fail a
        run: it is a convenience that fills in ``agent_type``, ``description``
        and ``depth``, and a trace without one is completely valid.
        """
        found: dict[int, dict[str, Any]] = {}
        for file_index, path in enumerate(resolve_inputs(paths, limits)):
            sidecar = path.with_suffix(SIDECAR_SUFFIX)
            try:
                if sidecar.is_symlink() or not sidecar.is_file():
                    continue
                if sidecar.stat().st_size > SIDECAR_MAX_BYTES:
                    continue
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict):
                found[file_index] = payload
        return found


def _group_assistants(assistants: Sequence[ParsedRecord]) -> list[_Group]:
    """R9: group assistant records by ``(sessionId, agentId, message.id, requestId)``.

    A record with neither a ``requestId`` nor a ``message.id`` is its own group
    — there is nothing to join it to, and joining such records on their other
    fields would merge two genuinely separate calls.

    Two things here decide whether a dollar figure is right, and both were
    wrong before this was reviewed:

    * **The solo key must be unique across the whole input set.** It used to be
      ``("solo", record.uuid)``, and R4 makes a duplicate ``uuid`` fatal only
      *within one file* — correctly, since agent files are written
      independently. So two ungrouped model calls in two different files that
      happened to share a uuid collapsed into one span, and R9's "usage from
      the last fragment" then discarded the first call's tokens entirely. The
      record's canonical index (R6) is unique by construction and orders the
      same way, so it is the right identity.
    * **An absent field is not an empty one.** ``message_id or ""`` mapped a
      recorded ``requestId: ""`` and a missing ``requestId`` onto the same key
      component, so a record carrying an empty string merged with one carrying
      nothing. ``None`` is preserved in the key instead, which is what R9's
      tuple of four recorded values actually says.
    """
    groups: dict[tuple[object, ...], _Group] = {}
    ordered: list[_Group] = []
    for item in assistants:
        record = item.record
        message_id = record.message.id if record.message is not None else None
        request_id = record.requestId
        key: tuple[object, ...]
        if message_id is None and request_id is None:
            key = ("solo", item.index)
        else:
            key = (
                "grouped",
                record.sessionId,
                safe_agent_id(record.agentId),
                message_id,
                request_id,
            )
        group = groups.get(key)
        if group is None:
            group = _Group(key=key)
            groups[key] = group
            ordered.append(group)
        group.fragments.append(item)
    return ordered


@dataclass
class _PendingSpan:
    """A span under construction, before ``seq`` and ``span_id`` are known."""

    kind: str
    agent_id: str
    file_index: int
    has_parent: bool = False
    start: datetime | None = None
    end: datetime | None = None
    model: str | None = None
    usage: TokenUsage | None = None
    stop_reason: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    tool_input_digest: str | None = None
    tool_result_status: str | None = None
    text_preview: str = ""
    tool_input_preview: str = ""
    tool_result_preview: str = ""
    error: SpanError | None = None
    extras_dropped: int = 0


class _TraceBuilder:
    """Turns parsed records into spans, warnings and agents (R5-R12)."""

    def __init__(
        self,
        *,
        trace_id: str,
        parsed: Sequence[ParsedRecord],
        warnings: WarningLog,
        no_previews: bool,
    ) -> None:
        self.trace_id = trace_id
        self.parsed = parsed
        self.warnings = warnings
        self.no_previews = no_previews
        self.blocks: dict[int, list[RawBlock]] = {}
        self.tool_results: dict[str, tuple[str, str, datetime]] = {}
        self.tool_use_ids: set[str] = set()

    # -- pass 1: blocks, tool results, extras -------------------------------

    def _index_blocks(self) -> None:
        """One pass over every record's content: blocks, tool results, warnings."""
        for item in self.parsed:
            record = item.record
            blocks, skipped = content_blocks(record.message)
            self.blocks[item.index] = blocks
            if skipped:
                self.warnings.add("unknown_content_block", "malformed", skipped)
            for block in blocks:
                kind = block.type or ""
                if kind not in KNOWN_BLOCK_TYPES:
                    self.warnings.add("unknown_content_block", slug(kind))
                    continue
                if kind == "tool_use" and block.id:
                    self.tool_use_ids.add(block.id)
                elif (
                    kind == "tool_result"
                    and block.tool_use_id
                    and block.tool_use_id not in self.tool_results
                ):
                    # First result wins: a trace that repeats a tool_use_id is
                    # hostile or corrupt, and taking the first keeps the mapping
                    # a function of canonical order (R6).
                    self.tool_results[block.tool_use_id] = (
                        "error" if block.errored else "ok",
                        preview(block_text(block.content)),
                        item.when,
                    )

    def _count_extras(self, item: ParsedRecord) -> int:
        """R4: count unknown and known-but-ignored keys. Never their contents.

        ``extras_dropped`` counts unknown keys at the record's top level, which
        is what R4 defines it as. Unknown keys nested on ``message``/``usage``
        are still counted as ``unknown_extra_key`` warnings so they are visible,
        but they do not inflate a span-level count with a different meaning.

        The warning detail is empty for unknown keys on purpose: the key *name*
        is attacker-chosen, and R10 forbids trace free text in a detail.
        """
        record = item.record
        dropped = 0
        for key in record.extra_keys():
            if key in KNOWN_IGNORED_KEYS:
                self.warnings.add("known_ignored_key", key)
            else:
                self.warnings.add("unknown_extra_key")
                dropped += 1
        message = record.message
        if message is not None:
            for key in message.extra_keys():
                if key in KNOWN_IGNORED_KEYS:
                    self.warnings.add("known_ignored_key", key)
                else:
                    self.warnings.add("unknown_extra_key")
            if message.usage is not None:
                for key in message.usage.extra_keys():
                    if key in KNOWN_IGNORED_KEYS:
                        self.warnings.add("known_ignored_key", key)
                    else:
                        self.warnings.add("unknown_extra_key")
        return dropped

    # -- pass 2: spans ------------------------------------------------------

    def build(self) -> tuple[tuple[Span, ...], dict[int, set[str]], dict[str, str]]:
        """Emit every span in canonical order (R6, R12)."""
        self._index_blocks()
        for tool_use_id in sorted(self.tool_results):
            if tool_use_id not in self.tool_use_ids:
                self.warnings.add("orphan_tool_result")

        assistants = [item for item in self.parsed if item.record.type == "assistant"]
        groups = _group_assistants(assistants)
        group_by_first: dict[int, _Group] = {group.fragments[0].index: group for group in groups}

        pending: list[_PendingSpan] = []
        agents_by_file: dict[int, set[str]] = {}
        for item in self.parsed:
            record = item.record
            agent_id = safe_agent_id(record.agentId)
            agents_by_file.setdefault(item.file_index, set()).add(agent_id)
            record_type = record.type
            if record_type == "assistant":
                group = group_by_first.get(item.index)
                if group is not None:
                    # R9: the whole group collapses here, at its first
                    # fragment's position. Later fragments emit nothing — their
                    # blocks are unioned in and their keys are counted by
                    # `_model_call`, so counting them again here would
                    # double-count every extras figure on a streamed response.
                    pending.extend(self._model_call(group))
            elif record_type == "user":
                span = self._user_message(item)
                if span is not None:
                    pending.append(span)
                else:
                    self._count_extras(item)
            elif record_type == "system":
                pending.append(self._system_event(item))
            elif record_type == "attachment":
                # R12: attachments produce no span. They are counted so a reader
                # can see the parser saw them; R10's taxonomy has no attachment
                # code, and "known and deliberately ignored" is what this is.
                self.warnings.add("known_ignored_key", "attachment")
                self._count_extras(item)
            else:
                self.warnings.add("unknown_record_type", slug(record_type))
                self._count_extras(item)

        return self._finalize(pending), agents_by_file, self._tool_use_agent(pending)

    def _model_call(self, group: _Group) -> list[_PendingSpan]:
        """R9 + R12: one collapsed ``model_call`` plus its ``tool_call`` children."""
        first = group.fragments[0]
        last = group.fragments[-1]
        agent_id = safe_agent_id(first.record.agentId)
        extras = sum(self._count_extras(fragment) for fragment in group.fragments)

        blocks: list[RawBlock] = []
        seen: set[tuple[Any, ...]] = set()
        for fragment in group.fragments:
            for position, block in enumerate(self.blocks[fragment.index]):
                identity = _block_identity(block, (fragment.index, position))
                if identity in seen:
                    continue
                seen.add(identity)
                blocks.append(block)

        message = last.record.message
        start = first.when
        end = last.when
        if end < start:
            self.warnings.add("negative_duration")
            end = start

        error: SpanError | None = None
        usage: TokenUsage | None = None
        model = message.model if message is not None else None
        stop_reason = message.stop_reason if message is not None else None

        if last.record.is_api_error:
            # R12: an API-error record is never billable and carries no usage.
            self.warnings.add("api_error_record")
            code = slug(last.record.error if isinstance(last.record.error, str) else None)
            detail = preview_within(
                last.record.apiErrorStatus if isinstance(last.record.apiErrorStatus, str) else None,
                DETAIL_MAX_CHARS,
            )
            error = SpanError(code=code, detail=detail)
        elif message is not None and message.usage is not None:
            usage = _usage_of(message.usage)
        else:
            self.warnings.add("missing_usage")
        if model == SYNTHETIC_MODEL:
            self.warnings.add("synthetic_model")

        # The span's free text is the response's text blocks; a response that is
        # pure thinking plus tool calls falls back to the thinking text so the
        # reader is not shown an empty row for a call that clearly said something.
        texts: list[str] = [block.text for block in blocks if block.type == "text" and block.text]
        if not texts:
            texts = [
                block.thinking for block in blocks if block.type == "thinking" and block.thinking
            ]
        call = _PendingSpan(
            kind="model_call",
            agent_id=agent_id,
            file_index=first.file_index,
            start=start,
            end=end,
            model=preview_within(model, DETAIL_MAX_CHARS) or None,
            usage=usage,
            stop_reason=preview_within(stop_reason, DETAIL_MAX_CHARS) or None,
            text_preview=self._preview(" ".join(texts)),
            error=error,
            extras_dropped=extras,
        )
        spans = [call]
        for block in blocks:
            if block.type != "tool_use":
                continue
            result = self.tool_results.get(block.id) if block.id else None
            if result is None:
                status, result_preview, result_when = "missing", "", None
            else:
                status, result_preview, result_when = result
            # The tool call is issued when the response that requested it
            # completed, and finishes when its result was recorded. Borrowing
            # the model call's window instead would report the model's duration
            # as the tool's, which R23 and R24 both read as a timing signal.
            call_end = result_when
            if call_end is not None and call_end < end:
                self.warnings.add("negative_duration")
                call_end = end
            spans.append(
                _PendingSpan(
                    kind="tool_call",
                    agent_id=agent_id,
                    file_index=first.file_index,
                    has_parent=True,
                    start=end,
                    end=call_end,
                    tool_name=preview_within(block.name, DETAIL_MAX_CHARS) or None,
                    tool_use_id=preview_within(block.id, DETAIL_MAX_CHARS) or None,
                    tool_input_digest=tool_input_digest(block.input),
                    tool_result_status=status,
                    tool_input_preview=self._preview(canonical_json(block.input)),
                    tool_result_preview=self._preview(result_preview),
                )
            )
        return spans

    def _user_message(self, item: ParsedRecord) -> _PendingSpan | None:
        """R12: a user record makes a span unless it is purely tool results."""
        record = item.record
        blocks = self.blocks[item.index]
        message = record.message
        is_string_content = message is not None and isinstance(message.content, str)
        has_non_tool_result = any(block.type != "tool_result" for block in blocks)
        if not is_string_content and not has_non_tool_result:
            return None
        return _PendingSpan(
            kind="user_message",
            agent_id=safe_agent_id(record.agentId),
            file_index=item.file_index,
            start=item.when,
            end=item.when,
            text_preview=self._preview(message_text(message)),
            extras_dropped=self._count_extras(item),
        )

    def _system_event(self, item: ParsedRecord) -> _PendingSpan:
        """R12: one span per system record; compaction boundaries are marked."""
        record = item.record
        error: SpanError | None = None
        if record.subtype == "compact_boundary":
            self.warnings.add("compaction_boundary")
            error = SpanError(code="compact_boundary", detail="")
        content = record.message.content if record.message is not None else None
        text = content if isinstance(content, str) else message_text(record.message)
        return _PendingSpan(
            kind="system_event",
            agent_id=safe_agent_id(record.agentId),
            file_index=item.file_index,
            start=item.when,
            end=item.when,
            text_preview=self._preview(text),
            error=error,
            extras_dropped=self._count_extras(item),
        )

    def _preview(self, value: str | None) -> str:
        """R8 previews, or ``""`` under ``--no-previews`` (A10)."""
        if self.no_previews:
            return ""
        return preview(value)

    # -- finalization -------------------------------------------------------

    def _finalize(self, pending: Sequence[_PendingSpan]) -> tuple[Span, ...]:
        """Assign ``seq`` and R5 ids, resolve parents, and freeze the spans."""
        spans: list[Span] = []
        ids: list[str] = []
        for seq, item in enumerate(pending):
            span_id = digest_id(self.trace_id, item.agent_id, str(seq), item.kind)
            ids.append(span_id)
        for seq, item in enumerate(pending):
            parent: str | None = None
            if item.has_parent:
                # A tool call always follows the model call that emitted it, so
                # the nearest preceding model_call span is its parent (R12).
                parent_seq = seq - 1
                while parent_seq >= 0 and pending[parent_seq].kind != "model_call":
                    parent_seq -= 1
                if parent_seq >= 0:
                    parent = ids[parent_seq]
            spans.append(
                Span(
                    span_id=ids[seq],
                    parent_span_id=parent,
                    agent_id=item.agent_id,
                    kind=item.kind,  # type: ignore[arg-type]
                    seq=seq,
                    start=item.start,
                    end=item.end,
                    model=item.model,
                    usage=item.usage,
                    stop_reason=item.stop_reason,
                    tool_name=item.tool_name,
                    tool_use_id=item.tool_use_id,
                    tool_input_digest=item.tool_input_digest,
                    tool_result_status=item.tool_result_status,  # type: ignore[arg-type]
                    text_preview=item.text_preview,
                    tool_input_preview=item.tool_input_preview,
                    tool_result_preview=item.tool_result_preview,
                    error=item.error,
                    extras_dropped=item.extras_dropped,
                )
            )
        self._count_dangling(spans)
        return tuple(spans)

    def _count_dangling(self, spans: Sequence[Span]) -> None:
        """R22's carve-out, counted at parse time as ``dangling_tool_use`` (R10).

        A trace captured while an agent is still working always ends on a tool
        call with no result. Counting it here is what lets the detector skip it
        without inventing a special case of its own.
        """
        last_by_agent: dict[str, Span] = {}
        for span in spans:
            last_by_agent[span.agent_id] = span
        for span in last_by_agent.values():
            if span.kind == "tool_call" and span.tool_result_status == "missing":
                self.warnings.add("dangling_tool_use")

    def _tool_use_agent(self, pending: Sequence[_PendingSpan]) -> dict[str, str]:
        """``tool_use_id`` → the agent that emitted it (A1 parent resolution)."""
        mapping: dict[str, str] = {}
        for item in pending:
            if item.kind == "tool_call" and item.tool_use_id:
                mapping.setdefault(item.tool_use_id, item.agent_id)
        return mapping

    # -- agents -------------------------------------------------------------

    def build_agents(
        self,
        spans: Sequence[Span],
        agents_by_file: dict[int, set[str]],
        tool_use_agent: dict[str, str],
        sidecars: dict[int, dict[str, Any]],
    ) -> tuple[AgentRun, ...]:
        """R2: one :class:`AgentRun` per agent, indexed by first appearance (R6)."""
        order: list[str] = []
        seqs: dict[str, list[int]] = {}
        starts: dict[str, datetime] = {}
        ends: dict[str, datetime] = {}
        for span in spans:
            if span.agent_id not in seqs:
                order.append(span.agent_id)
                seqs[span.agent_id] = []
            seqs[span.agent_id].append(span.seq)
            if span.start is not None:
                current = starts.get(span.agent_id)
                if current is None or span.start < current:
                    starts[span.agent_id] = span.start
            if span.end is not None:
                current = ends.get(span.agent_id)
                if current is None or span.end > current:
                    ends[span.agent_id] = span.end

        # A sidecar describes the agent of the file it sits next to; a file that
        # somehow carries two agents is ambiguous, so its sidecar is ignored.
        metadata: dict[str, dict[str, Any]] = {}
        for file_index, payload in sidecars.items():
            agents = agents_by_file.get(file_index, set())
            if len(agents) == 1:
                metadata[next(iter(agents))] = payload

        runs: list[AgentRun] = []
        for agent_index, agent_id in enumerate(order):
            payload = metadata.get(agent_id, {})
            agent_type = payload.get("agentType")
            description = payload.get("description")
            depth = payload.get("spawnDepth")
            parent_tool_use = payload.get("toolUseId")
            parent = None
            if isinstance(parent_tool_use, str):
                candidate = tool_use_agent.get(parent_tool_use)
                if candidate is not None and candidate != agent_id:
                    parent = candidate
            runs.append(
                AgentRun(
                    agent_id=agent_id,
                    agent_index=agent_index,
                    agent_type=self._metadata_text(agent_type),
                    description=self._metadata_text(description) or "",
                    parent_agent_id=parent,
                    depth=self._metadata_depth(depth),
                    span_seqs=tuple(seqs[agent_id]),
                    start=starts.get(agent_id),
                    end=ends.get(agent_id),
                )
            )
        return tuple(runs)

    def _metadata_depth(self, value: Any) -> int | None:
        """Sidecar ``spawnDepth``, or ``None`` for anything ``AgentRun`` would reject.

        A1 promises the sidecar's every failure mode is "no metadata".
        ``AgentRun.depth`` is ``ge=0``, so a negative value — which a hostile or
        simply buggy sidecar can carry, and which is not covered by ``trace_id``
        (R5 hashes the named inputs only) — used to raise an unsanitized
        ``ValidationError`` out of ``load()`` and kill the whole run. Every
        constraint the field carries is checked here so the promise holds.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value >= 0 else None

    def _metadata_text(self, value: Any) -> str | None:
        """Sidecar free text, previewed and capped — or dropped under no-previews."""
        if self.no_previews or not isinstance(value, str):
            return None
        return preview_within(value, DETAIL_MAX_CHARS) or None


__all__ = [
    "ADAPTER_SLUG",
    "SIDECAR_SUFFIX",
    "SYNTHETIC_MODEL",
    "ClaudeCodeSource",
    "CollapseStats",
    "WarningLog",
    "safe_agent_id",
    "tool_input_digest",
]
