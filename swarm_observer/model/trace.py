"""The normalized trace model and its schema document (R1, R2).

Every model here is a frozen pydantic v2 model with ``extra="forbid"``: the
normalized trace is the contract between the adapters that produce it and the
detectors, cost engine and renderers that consume it, so a field the producer
did not mean to set is a validation error rather than a silently carried value.

The module deliberately does more than declare fields. R2 pins orderings
("sorted by name", "sorted by ``seq``"), caps ("capped at 200 chars") and
alphabets (64 lowercase hex, basename only, a closed warning enum); each of
those is expressed as a constraint or a model validator so that a mapper bug
fails at construction time instead of surfacing three layers later as a
mis-rendered report. The safety claims other requirements make about these
values — R5's "ids never embed trace content", R10's "``detail`` never carries
trace free text", R47's "only basenames reach output" — are enforced *here*, at
the one place every trace must pass through.

Imports: stdlib and pydantic only (R1, R44).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

#: R1: the normalized model's own version, independent of any vendor format.
#: Adding a field to any model below is a minor bump plus a golden-file update.
TRACE_SCHEMA_VERSION = "1.0.0"

#: R2/R5: 16 lowercase hex characters — the shape of ``trace_id`` and ``span_id``.
HEX_ID_PATTERN = r"^[0-9a-f]{16}$"

#: R5: the alphabet an ``agent_id`` may use. Recorded agent ids are trace-derived,
#: so the mapper replaces a non-conforming one with a deterministic digest-based
#: id (see ``ingest.claude_code.mapper.safe_agent_id``); enforcing the alphabet
#: here is what makes R5's "ids never embed trace content" literally true, and
#: therefore what makes them safe in an HTML attribute value (R32, R34).
AGENT_ID_PATTERN = r"^[A-Za-z0-9_.:\-]{1,64}$"

#: R10: ``ParseWarning.detail`` carries an enumerated slug, a record-type slug
#: or a decimal count — never trace free text. The alphabet is the enforcement.
WARNING_DETAIL_PATTERN = r"^[A-Za-z0-9_.:\-]{0,64}$"

#: R2: the cap applied to every trace-derived short string on these models.
DETAIL_MAX_CHARS = 200

#: R8: the cap applied to preview text, including the appended ellipsis.
PREVIEW_MAX_CHARS = 241


def _require_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalize aware ones to UTC (R2, R47).

    A naive timestamp is a bug in an adapter, not a value to guess about: it
    would render differently depending on the machine's ``TZ``, which R47
    forbids outright.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


#: A timezone-aware UTC datetime. Every timestamp on the model is one of these.
UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]

SpanKind = Literal["model_call", "tool_call", "user_message", "system_event"]
ToolResultStatus = Literal["ok", "error", "missing"]

#: R10: the closed parse-warning taxonomy. A code outside this tuple is a
#: validation error, so a new adapter cannot invent a warning kind.
ParseWarningCode = Literal[
    "unknown_record_type",
    "unknown_content_block",
    "known_ignored_key",
    "unknown_extra_key",
    "timestamp_out_of_order",
    "negative_duration",
    "orphan_tool_result",
    "dangling_tool_use",
    "missing_usage",
    "synthetic_model",
    "compaction_boundary",
    "api_error_record",
]

#: The same taxonomy as data, for tests and for exhaustiveness checks.
PARSE_WARNING_CODES: tuple[str, ...] = (
    "unknown_record_type",
    "unknown_content_block",
    "known_ignored_key",
    "unknown_extra_key",
    "timestamp_out_of_order",
    "negative_duration",
    "orphan_tool_result",
    "dangling_tool_use",
    "missing_usage",
    "synthetic_model",
    "compaction_boundary",
    "api_error_record",
)

SPAN_KINDS: tuple[str, ...] = ("model_call", "tool_call", "user_message", "system_event")


class _Frozen(BaseModel):
    """The shared configuration every normalized model uses (R2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TokenUsage(_Frozen):
    """One model call's token accounting (R2).

    The five components are exactly the ones the cost formula (R28) prices, and
    they do not overlap: ``input_tokens`` as recorded already excludes cached
    tokens, and thinking tokens are already inside ``output_tokens``. Adding a
    sixth component here is a schema version bump precisely because it would
    change what a dollar figure means.
    """

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cache_creation_5m_tokens: int = Field(default=0, ge=0)
    cache_creation_1h_tokens: int = Field(default=0, ge=0)

    def plus(self, other: TokenUsage) -> TokenUsage:
        """Element-wise sum — the operation R17's waste attribution needs."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_5m_tokens=self.cache_creation_5m_tokens + other.cache_creation_5m_tokens,
            cache_creation_1h_tokens=self.cache_creation_1h_tokens + other.cache_creation_1h_tokens,
        )

    @property
    def total(self) -> int:
        """All five components summed — R24's ``tokens`` dimension."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_5m_tokens
            + self.cache_creation_1h_tokens
        )


class SpanError(_Frozen):
    """A span-level failure: an enumerated code plus capped trace-derived text (R2)."""

    #: An enumerated slug (R12 derives it from the record's error field by
    #: slugifying); never trace-derived free text.
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:\-]{1,40}$")
    #: Trace-derived, capped at 200 characters. Redacted and escaped at render.
    detail: str = Field(default="", max_length=DETAIL_MAX_CHARS)


class Span(_Frozen):
    """One unit of observed work: a model call, a tool call, a message, an event (R2)."""

    span_id: str = Field(pattern=HEX_ID_PATTERN)
    parent_span_id: str | None = Field(default=None, pattern=HEX_ID_PATTERN)
    agent_id: str = Field(pattern=AGENT_ID_PATTERN)
    kind: SpanKind
    #: 0-based position in the trace's canonical order (R6).
    seq: int = Field(ge=0)
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    #: ``model_call`` only.
    model: str | None = Field(default=None, max_length=DETAIL_MAX_CHARS)
    #: ``model_call`` only; ``None`` when the record carried no usage block.
    usage: TokenUsage | None = None
    stop_reason: str | None = Field(default=None, max_length=DETAIL_MAX_CHARS)
    #: ``tool_call`` only.
    tool_name: str | None = Field(default=None, max_length=DETAIL_MAX_CHARS)
    tool_use_id: str | None = Field(default=None, max_length=DETAIL_MAX_CHARS)
    #: R7: 16 hex characters over the canonical JSON of the tool input.
    tool_input_digest: str | None = Field(default=None, pattern=HEX_ID_PATTERN)
    tool_result_status: ToolResultStatus | None = None
    #: R8: the only trace free text on a span. ``""`` when absent or suppressed.
    text_preview: str = Field(default="", max_length=PREVIEW_MAX_CHARS)
    tool_input_preview: str = Field(default="", max_length=PREVIEW_MAX_CHARS)
    tool_result_preview: str = Field(default="", max_length=PREVIEW_MAX_CHARS)
    error: SpanError | None = None
    #: R4: how many unknown keys the source record carried. Never their content.
    extras_dropped: int = Field(default=0, ge=0)


class AgentRun(_Frozen):
    """One agent's participation in the trace (R2)."""

    agent_id: str = Field(pattern=AGENT_ID_PATTERN)
    #: 0-based, assigned in canonical order of first appearance (R6).
    agent_index: int = Field(ge=0)
    #: Trace-derived (from the sidecar metadata when present, A1).
    agent_type: str | None = Field(default=None, max_length=DETAIL_MAX_CHARS)
    #: Trace-derived, capped at 200 characters, ``""`` when absent.
    description: str = Field(default="", max_length=DETAIL_MAX_CHARS)
    parent_agent_id: str | None = Field(default=None, pattern=AGENT_ID_PATTERN)
    depth: int | None = Field(default=None, ge=0)
    #: The ``seq`` of every span belonging to this agent, ascending.
    span_seqs: tuple[int, ...] = ()
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None

    @model_validator(mode="after")
    def _span_seqs_ascending(self) -> AgentRun:
        if list(self.span_seqs) != sorted(self.span_seqs):
            raise ValueError("span_seqs must be ascending")
        if len(set(self.span_seqs)) != len(self.span_seqs):
            raise ValueError("span_seqs must not repeat a seq")
        return self


class SourceFile(_Frozen):
    """One input file's provenance (R2, R47).

    ``name`` is a basename by construction: a directory component would leak the
    analyst's filesystem into a report that is meant to be byte-identical
    between two machines (R47) and shareable (R34).
    """

    name: str = Field(min_length=1, max_length=DETAIL_MAX_CHARS)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    records: int = Field(ge=0)

    @model_validator(mode="after")
    def _basename_only(self) -> SourceFile:
        if "/" in self.name or "\\" in self.name or self.name in {".", ".."}:
            raise ValueError("source file name must be a basename, never a path")
        return self


class ParseWarning(_Frozen):
    """An aggregated, content-free note about something the parser tolerated (R10)."""

    code: ParseWarningCode
    count: int = Field(ge=1)
    #: An enumerated slug, a record-type slug, or a decimal count — never trace
    #: free text. The alphabet constraint is the enforcement, not a convention.
    detail: str = Field(default="", pattern=WARNING_DETAIL_PATTERN)


class Trace(_Frozen):
    """A whole normalized run: the only type anything downstream sees (R1, R2)."""

    schema_version: str
    trace_id: str = Field(pattern=HEX_ID_PATTERN)
    #: The adapter slug that produced this trace, e.g. ``claude_code_jsonl``.
    adapter: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    source_files: tuple[SourceFile, ...] = ()
    agents: tuple[AgentRun, ...] = ()
    spans: tuple[Span, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()

    @model_validator(mode="after")
    def _pinned_invariants(self) -> Trace:
        """R1/R2: the version matches and every pinned ordering actually holds."""
        if self.schema_version != TRACE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {TRACE_SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        names = [source.name for source in self.source_files]
        if names != sorted(names):
            raise ValueError("source_files must be sorted by name")
        if len(set(names)) != len(names):
            raise ValueError("source_files must not repeat a name")
        indexes = [agent.agent_index for agent in self.agents]
        if indexes != list(range(len(self.agents))):
            raise ValueError("agents must be sorted by agent_index, 0-based and contiguous")
        seqs = [span.seq for span in self.spans]
        if seqs != list(range(len(self.spans))):
            raise ValueError("spans must be sorted by seq, 0-based and contiguous")
        keys = [(warning.code, warning.detail) for warning in self.warnings]
        if keys != sorted(keys):
            raise ValueError("warnings must be sorted by (code, detail)")
        if len(set(keys)) != len(keys):
            raise ValueError("warnings must be aggregated: one entry per (code, detail)")
        return self


def trace_json_schema() -> dict[str, Any]:
    """The JSON Schema of :class:`Trace` (R1, R38 ``schema``)."""
    return Trace.model_json_schema()


def schema_document() -> str:
    """The exact bytes ``swarm-observer schema`` prints (R38, R47).

    Deterministic by construction: ``sort_keys`` removes every dict-ordering
    dependency (and therefore every ``PYTHONHASHSEED`` dependency), ``ensure_ascii``
    removes every encoding dependency, and no clock, path or environment value
    is consulted. The document is a single JSON object so it is machine-readable
    as well as printable.
    """
    document = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "title": "swarm-observer normalized trace",
        "schema": trace_json_schema(),
    }
    return json.dumps(document, sort_keys=True, ensure_ascii=True, indent=2) + "\n"


__all__ = [
    "AGENT_ID_PATTERN",
    "DETAIL_MAX_CHARS",
    "HEX_ID_PATTERN",
    "PARSE_WARNING_CODES",
    "PREVIEW_MAX_CHARS",
    "SPAN_KINDS",
    "TRACE_SCHEMA_VERSION",
    "WARNING_DETAIL_PATTERN",
    "AgentRun",
    "ParseWarning",
    "ParseWarningCode",
    "SourceFile",
    "Span",
    "SpanError",
    "SpanKind",
    "TokenUsage",
    "ToolResultStatus",
    "Trace",
    "UtcDatetime",
    "schema_document",
    "trace_json_schema",
]
