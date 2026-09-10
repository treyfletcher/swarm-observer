"""The adapter seam, the ingest limits and the fail-closed error taxonomy (R3, R11).

Two things here are load-bearing beyond their size.

:class:`TraceSource` is the whole adapter contract: one method, normalized
types on both sides. An OTel or SDK source in v2 implements it and appears in
the registry, with no edit anywhere else in the package (R3, R44).

:class:`TraceError` is the fail-closed path (R11). Its message is assembled
from *typed fields* — a basename, a line number, a limit, a note drawn from a
constrained alphabet — rather than from free text, because "the detail may
never contain a byte of file content" is a guarantee that survives review only
if it is structurally impossible to violate. Passing a record, a key or a line
of a trace into one of these constructors raises ``ValueError`` at the point of
the mistake instead of quietly printing hostile bytes to a terminal.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from swarm_observer.model.trace import Trace

#: R11: the maximum length of the sanitized detail on a fail-closed message.
MAX_DETAIL_CHARS = 200

#: The alphabet a ``note`` may use: enumerated words and slugs, nothing else.
_NOTE_PATTERN = re.compile(r"^[A-Za-z0-9 _.:\-]{0,80}$")


class IngestLimits(BaseModel):
    """The caps every reader enforces before it parses anything (R11).

    The defaults bound what a hostile "trace" can cost: 256 MiB per file, 8 MiB
    per line, two million records and 64 files. They are constructor arguments
    rather than module constants so the CLI can lower them (R38) — raising them
    is possible too, which is why every breach names the limit it hit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_file_bytes: int = Field(default=268_435_456, ge=1)
    max_line_bytes: int = Field(default=8_388_608, ge=1)
    max_records: int = Field(default=2_000_000, ge=1)
    max_files: int = Field(default=64, ge=1)
    #: The deepest container nesting a line's JSON may reach. R11's four byte
    #: and count caps do not bound depth, and a nesting bomb is only a few
    #: kilobytes, so without this the only thing standing between the reader and
    #: a hostile line is whether ``json.loads`` happens to exhaust the C stack —
    #: which is interpreter-, build- and platform-dependent and is therefore not
    #: a contract at all (CPython 3.11 raises at depth 1,000; 3.12 parses 5,000
    #: happily). 200 is an order of magnitude beyond anything an observed
    #: transcript contains and well under every supported interpreter's limit.
    #: Not yet in R11's text — see the review's ruling on spec flag S4.
    max_json_depth: int = Field(default=200, ge=1)


class TraceSource(Protocol):
    """Loads a set of trace files into one normalized :class:`Trace` (R3).

    Implementations raise :class:`TraceError` and nothing else for bad input:
    no partial ``Trace`` is ever returned, and no exception carrying a
    traceback through trace content reaches the CLI (R11).
    """

    def load(self, paths: Sequence[Path], limits: IngestLimits) -> Trace:
        """Read ``paths`` under ``limits`` and return the normalized trace."""
        ...


def _sanitize(text: str) -> str:
    """Collapse to one printable line and cap it (R11).

    Applied to an already-structured detail as a second belt: the constructors
    below only admit safe pieces, and this guarantees the result is still one
    line even if a caller supplies an odd basename.
    """
    flattened = "".join(char if char.isprintable() else " " for char in text)
    collapsed = " ".join(flattened.split())
    if len(collapsed) > MAX_DETAIL_CHARS:
        return collapsed[: MAX_DETAIL_CHARS - 1] + "…"
    return collapsed


class TraceError(Exception):
    """A fail-closed ingestion failure: one sanitized line, no content (R11).

    Construct it from typed pieces only::

        raise TraceParseError("invalid_json", source="agent-1.jsonl", line=5)

    which renders as ``invalid_json: agent-1.jsonl line 5``. The CLI prefixes
    ``swarm-observer: `` and exits 2.
    """

    #: Subclasses close their own slice of the taxonomy.
    CODES: frozenset[str] = frozenset()

    def __init__(
        self,
        code: str,
        *,
        source: str | None = None,
        line: int | None = None,
        limit: int | None = None,
        note: str | None = None,
    ) -> None:
        if self.CODES and code not in self.CODES:
            raise ValueError(f"{type(self).__name__} does not own the code {code!r}")
        if source is not None and ("/" in source or "\\" in source):
            raise ValueError("TraceError source must be a basename, never a path")
        # fullmatch, not match: Python's `$` also matches before a trailing
        # newline, so `match` would admit a note ending in one. Same defect
        # class as `safe_agent_id`'s.
        if note is not None and not _NOTE_PATTERN.fullmatch(note):
            raise ValueError("TraceError note must be enumerated text, never trace content")
        self.code = code
        self.source = source
        self.line = line
        self.limit = limit
        self.note = note
        self.detail = _sanitize(self._build_detail())
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)

    def _build_detail(self) -> str:
        pieces: list[str] = []
        if self.source is not None:
            pieces.append(self.source)
        if self.line is not None:
            pieces.append(f"line {self.line}")
        if self.limit is not None:
            pieces.append(f"limit {self.limit}")
        if self.note:
            pieces.append(self.note)
        return " ".join(pieces)

    @property
    def cli_line(self) -> str:
        """The exact single line the CLI writes to stderr (R11, R39)."""
        return f"swarm-observer: {self.code}: {self.detail}"


class TraceReadError(TraceError):
    """The input set itself is unusable: missing, unreadable, or not a file (R11)."""

    CODES = frozenset(
        {
            "empty_input",
            "unreadable_path",
            "not_a_regular_file",
            "symlink_escape",
            "duplicate_input",
            "name_too_long",
        }
    )


class TraceLimitError(TraceError):
    """An :class:`IngestLimits` cap was exceeded (R11)."""

    CODES = frozenset(
        {
            "file_too_large",
            "line_too_long",
            "too_many_files",
            "too_many_records",
            "json_too_deep",
        }
    )


class TraceParseError(TraceError):
    """A record is malformed in a way R4 declares fatal (R4, R11)."""

    CODES = frozenset(
        {
            "invalid_encoding",
            "invalid_json",
            "not_an_object",
            "missing_field",
            "bad_timestamp",
            "duplicate_uuid",
            "bad_message",
        }
    )


#: Every code the taxonomy can produce, for exhaustiveness assertions.
TRACE_ERROR_CODES: frozenset[str] = (
    TraceReadError.CODES | TraceLimitError.CODES | TraceParseError.CODES
)

__all__ = [
    "MAX_DETAIL_CHARS",
    "TRACE_ERROR_CODES",
    "IngestLimits",
    "TraceError",
    "TraceLimitError",
    "TraceParseError",
    "TraceReadError",
    "TraceSource",
]
