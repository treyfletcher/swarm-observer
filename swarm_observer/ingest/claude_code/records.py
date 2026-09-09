"""On-disk Claude Code record models and R4's tolerated/fatal split.

This is the only module in the package where a Claude Code field name appears
(R1, R44) — ``agentId``, ``requestId``, ``isApiErrorMessage``,
``cache_creation.ephemeral_5m_input_tokens`` and the rest live here and nowhere
else, so a v2 adapter for another format touches nothing outside its own
package.

R4 splits unexpected input two ways, and the split *is* the requirement:

* **Tolerated and counted** — unknown record types, unknown content blocks,
  unknown keys, known-but-ignored keys. The observed format is undocumented and
  unversioned (A1); six ``version`` strings appear inside one session. A field
  added by next week's Claude Code release must degrade to a counted warning,
  never to an exit 2.
* **Fatal** — a line that is not JSON, a record without ``type``/``uuid``/
  ``timestamp``, a non-RFC-3339 timestamp, a duplicate ``uuid`` in one file, a
  ``message`` that is present but not an object. These are not "unexpected
  shape", they are "this file is not what it claims to be", and R11 fails
  closed on them.

Everything below the fatal checks is deliberately lenient: the field models use
``extra="allow"`` and before-validators that coerce a wrong-typed value to the
neutral default instead of raising, because a pydantic ``ValidationError``
escaping this module would be an unsanitized crash on hostile input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict

from swarm_observer.ingest.reader import JsonRecord
from swarm_observer.ingest.source import TraceParseError

#: R4: keys swarm-observer knows about and deliberately does not model. Each is
#: counted once per key as the ``known_ignored_key`` warning, so "we ignored
#: something on purpose" is visible in the report rather than invisible.
KNOWN_IGNORED_KEYS: frozenset[str] = frozenset(
    {
        "iterations",
        "output_tokens_details",
        "server_tool_use",
        "diagnostics",
        "stop_details",
        "container",
        "context_management",
        "quotaLimits",
        "rendered",
        "apiBlockIndex",
        "logicalParentUuid",
        "compactMetadata",
    }
)

#: The record types the mapper knows how to handle (R12). Anything else is
#: tolerated and counted as ``unknown_record_type``.
KNOWN_RECORD_TYPES: frozenset[str] = frozenset({"assistant", "user", "system", "attachment"})

#: The content block types the mapper knows (R12); others are counted.
KNOWN_BLOCK_TYPES: frozenset[str] = frozenset({"text", "thinking", "tool_use", "tool_result"})

#: RFC 3339 with a mandatory offset. ``fromisoformat`` alone would accept a
#: naive date, which R2 forbids and R47 would render differently per ``TZ``.
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


def _optional_string(value: Any) -> Any:
    """Keep strings, drop anything else. A wrong type is absence, not an error."""
    return value if value is None or isinstance(value, str) else None


def _non_negative_int(value: Any) -> Any:
    """Coerce a usage component to a non-negative ``int``.

    Booleans are excluded on purpose (``True`` is an ``int`` in Python but not a
    token count), and a negative count — which a hostile trace may well
    contain — becomes 0 rather than a negative dollar figure downstream.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _optional_object(value: Any) -> Any:
    """Keep JSON objects, drop anything else. A wrong-typed nested value is absence.

    The scalar fields have had this treatment since T4 (``_optional_string``,
    ``_non_negative_int``); the *nested model* fields did not, so a ``message``
    of ``"a string"`` on a ``system`` record, or a ``usage`` of ``true``, reached
    pydantic and raised a ``ValidationError`` whose message quotes the offending
    trace bytes. R4 makes a non-object ``message`` fatal for ``assistant`` and
    ``user`` records only, and :func:`parse_record` still enforces that *before*
    pydantic runs; every other position is tolerated, which per R4 means it may
    not raise at all.
    """
    return value if value is None or isinstance(value, dict | BaseModel) else None


OptionalString = Annotated[str | None, BeforeValidator(_optional_string)]
UsageInt = Annotated[int, BeforeValidator(_non_negative_int)]


class RawBase(BaseModel):
    """R4: every on-disk model allows extra keys so new ones are counted, not fatal."""

    model_config = ConfigDict(extra="allow")

    def extra_keys(self) -> tuple[str, ...]:
        """The keys present on the record that this model does not declare."""
        return tuple(sorted(self.model_extra or {}))


class RawCacheCreation(RawBase):
    """``message.usage.cache_creation`` — the 5m/1h breakdown (A1, R28)."""

    ephemeral_5m_input_tokens: UsageInt = 0
    ephemeral_1h_input_tokens: UsageInt = 0


class RawUsage(RawBase):
    """``message.usage`` (A1).

    ``cache_creation_input_tokens`` is the pre-breakdown total; R28 charges the
    whole of it at the 5-minute rate when no ``cache_creation`` block is
    present, which is expected on older transcript versions and is explicitly
    *not* a ``missing_usage`` warning.
    """

    input_tokens: UsageInt = 0
    output_tokens: UsageInt = 0
    cache_read_input_tokens: UsageInt = 0
    cache_creation_input_tokens: UsageInt = 0
    cache_creation: Annotated[RawCacheCreation | None, BeforeValidator(_optional_object)] = None


class RawBlock(RawBase):
    """One ``message.content`` block: text, thinking, tool_use or tool_result (A1)."""

    type: OptionalString = None
    text: OptionalString = None
    thinking: OptionalString = None
    id: OptionalString = None
    name: OptionalString = None
    input: Any = None
    tool_use_id: OptionalString = None
    content: Any = None
    is_error: Any = None

    @property
    def errored(self) -> bool:
        """R12: a tool result is an error only on an explicit truthy flag."""
        return self.is_error is True


class RawMessage(RawBase):
    """The ``message`` object on an assistant or user record (A1)."""

    id: OptionalString = None
    model: OptionalString = None
    role: OptionalString = None
    content: Any = None
    usage: Annotated[RawUsage | None, BeforeValidator(_optional_object)] = None
    stop_reason: OptionalString = None


class RawRecord(RawBase):
    """One JSONL record (A1).

    ``type``, ``uuid`` and ``timestamp`` are guaranteed non-empty strings by
    :func:`parse_record` before this model is constructed, so they are plain
    required fields here rather than optional ones with a downstream check.
    """

    type: str
    uuid: str
    timestamp: str
    # camelCase below is the on-disk spelling (A1), not a style choice: this is
    # the one module allowed to name Claude Code fields, and renaming them here
    # would put the mapping in two places.
    parentUuid: OptionalString = None
    agentId: OptionalString = None
    sessionId: OptionalString = None
    requestId: OptionalString = None
    subtype: OptionalString = None
    isSidechain: Any = None
    isMeta: Any = None
    isApiErrorMessage: Any = None
    apiErrorStatus: Any = None
    error: Any = None
    message: Annotated[RawMessage | None, BeforeValidator(_optional_object)] = None

    @property
    def is_api_error(self) -> bool:
        """R12: ``isApiErrorMessage: true`` marks a synthetic error assistant record."""
        return self.isApiErrorMessage is True


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    """A validated record plus its canonical position and parsed timestamp (R6)."""

    record: RawRecord
    when: datetime
    file_index: int
    line: int
    #: Position in the whole trace's canonical order; ties are impossible.
    index: int


def parse_timestamp(value: str, *, source: str, line: int) -> datetime:
    """Parse an RFC 3339 timestamp into an aware UTC datetime, or fail closed (R4)."""
    if not _RFC3339.match(value):
        raise TraceParseError("bad_timestamp", source=source, line=line, note="not RFC 3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise TraceParseError("bad_timestamp", source=source, line=line) from None
    if parsed.tzinfo is None:  # pragma: no cover - the pattern requires an offset
        raise TraceParseError("bad_timestamp", source=source, line=line, note="no offset")
    return parsed.astimezone(UTC)


def parse_record(raw: JsonRecord, *, source: str, index: int) -> ParsedRecord:
    """Apply R4's fatal checks and build the on-disk model.

    The checks run *before* pydantic sees the payload so that every fatal
    condition raises a :class:`TraceParseError` naming a field, rather than a
    ``ValidationError`` whose message would quote trace content.
    """
    data = raw.data
    for field in ("type", "uuid", "timestamp"):
        value = data.get(field)
        if not isinstance(value, str) or not value:
            raise TraceParseError(
                "missing_field", source=source, line=raw.line, note=f"field {field}"
            )
    record_type = data["type"]
    if record_type in {"assistant", "user"}:
        message = data.get("message")
        if message is not None and not isinstance(message, dict):
            raise TraceParseError(
                "bad_message", source=source, line=raw.line, note="message is not an object"
            )
    when = parse_timestamp(data["timestamp"], source=source, line=raw.line)
    record = RawRecord.model_validate(data)
    return ParsedRecord(
        record=record, when=when, file_index=raw.file_index, line=raw.line, index=index
    )


def content_blocks(message: RawMessage | None) -> tuple[list[RawBlock], int]:
    """Return the message's content blocks and the count of unusable ones (R4).

    A string ``content`` (the common shape for a user turn) has no blocks. A
    block that is not an object, or carries no ``type``, is skipped and counted
    as ``unknown_content_block`` by the caller — tolerated, never fatal.
    """
    if message is None or not isinstance(message.content, list):
        return [], 0
    blocks: list[RawBlock] = []
    skipped = 0
    for item in message.content:
        if not isinstance(item, dict):
            skipped += 1
            continue
        block = RawBlock.model_validate(item)
        if not block.type:
            skipped += 1
            continue
        blocks.append(block)
    return blocks, skipped


def message_text(message: RawMessage | None) -> str | None:
    """The message's free text: a string ``content``, or its ``text`` blocks joined."""
    if message is None:
        return None
    if isinstance(message.content, str):
        return message.content
    blocks, _ = content_blocks(message)
    texts = [block.text for block in blocks if block.type == "text" and block.text]
    return " ".join(texts) if texts else None


def block_text(value: Any) -> str | None:
    """The free text of a ``tool_result`` block's ``content`` (A1).

    Claude Code writes either a plain string or a list of ``{"type": "text"}``
    blocks; anything else yields no text rather than a repr of the payload.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
        return " ".join(texts) if texts else None
    return None


__all__ = [
    "KNOWN_BLOCK_TYPES",
    "KNOWN_IGNORED_KEYS",
    "KNOWN_RECORD_TYPES",
    "ParsedRecord",
    "RawBlock",
    "RawCacheCreation",
    "RawMessage",
    "RawRecord",
    "RawUsage",
    "block_text",
    "content_blocks",
    "message_text",
    "parse_record",
    "parse_timestamp",
]
