"""Hand-built :class:`Trace` values for driving detectors at their boundaries.

Not a test module. The fixture corpus (``tests/fixtures/traces/``) is the
adapter-shaped half of the detector evidence: real JSONL through the real
mapper. This module is the other half — a ``Trace`` assembled directly, which
buys three things the corpus cannot.

* **Both sides of a boundary at one line of diff.** A 60-second gap and a
  59-second gap differ here by one integer, rather than by two 14-record JSONL
  files whose every other byte must be held equal by hand. The increment-1
  review's lesson was that a suite is only as sharp as the *pair* of inputs it
  can put either side of a comparison; pairs are cheap here and expensive in
  JSONL.
* **Shapes the mapper cannot emit.** R17's "distinct ``model_call`` spans" check
  needs a ``tool_call`` seq handed to :func:`attribute_waste`; R24's duration
  clamp needs a span whose ``end`` precedes its ``start``. Neither survives the
  adapter, and both are behaviour the requirement pins.
* **Populations the corpus would need 30 records to reach.** R24's population
  floor, R20's ten-span window and R19's period-8 search are all about counts,
  and a loop writing them is more legible than a file containing them.

Everything built here is a real, fully validated ``Trace``: the same frozen
pydantic models the adapter produces, with R2's orderings enforced by the same
validators. A builder that skipped validation would be testing a shape the
product cannot receive.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from swarm_observer.model.trace import (
    TRACE_SCHEMA_VERSION,
    AgentRun,
    ParseWarning,
    Span,
    SpanError,
    TokenUsage,
    Trace,
)

#: Every synthetic timestamp is an offset from this instant.
EPOCH = datetime(2026, 9, 9, 10, 0, 0, tzinfo=UTC)

#: A digest that is not any other builder's default, for "a different tool input".
DIGEST_A = "00000000000000aa"
DIGEST_B = "00000000000000bb"

#: The ``trace_id`` every synthetic trace carries unless one is passed. Fixed,
#: because R15 hashes it: a varying id would move every ``finding_id`` a test
#: pins.
SYNTHETIC_TRACE_ID = "0000000000000010"


def at(millis: int) -> datetime:
    """``millis`` milliseconds after :data:`EPOCH`, timezone-aware and UTC."""
    return EPOCH + timedelta(milliseconds=millis)


def hex_id(number: int) -> str:
    """A 16-hex-character id built from ``number`` (R2's ``HEX_ID_PATTERN``)."""
    return f"{number:016x}"


def tokens(total: int) -> TokenUsage:
    """A usage block whose R24 ``tokens`` dimension is exactly ``total``."""
    return TokenUsage(input_tokens=total)


class TraceBuilder:
    """Accumulates spans in canonical order and builds a validated ``Trace``.

    ``seq`` and ``span_id`` are assigned from the append position, so a builder
    can never produce the non-contiguous ``seq`` list R2 rejects. Agents are
    derived from the spans, in first-appearance order, which is R6's rule.
    """

    def __init__(self) -> None:
        self._spans: list[Span] = []
        self._warnings: list[ParseWarning] = []

    # -- construction ---------------------------------------------------------

    def _add(self, **fields: Any) -> Span:
        seq = len(self._spans)
        fields.setdefault("span_id", hex_id(seq + 1))
        span = Span(seq=seq, **fields)
        self._spans.append(span)
        return span

    def model_call(
        self,
        *,
        agent_id: str = "root",
        usage: TokenUsage | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        error: SpanError | None = None,
        text_preview: str = "",
        model: str | None = "claude-sonnet-4-5",
    ) -> Span:
        """One ``model_call`` span."""
        return self._add(
            agent_id=agent_id,
            kind="model_call",
            usage=usage,
            start=None if start_ms is None else at(start_ms),
            end=None if end_ms is None else at(end_ms),
            error=error,
            text_preview=text_preview,
            model=model,
        )

    def tool_call(
        self,
        *,
        agent_id: str = "root",
        parent: Span | None = None,
        tool_name: str | None = "Bash",
        digest: str | None = DIGEST_A,
        status: str | None = "ok",
        start_ms: int | None = None,
        end_ms: int | None = None,
        input_preview: str = "input",
        result_preview: str = "",
    ) -> Span:
        """One ``tool_call`` span, optionally attributed to a parent model call."""
        return self._add(
            agent_id=agent_id,
            kind="tool_call",
            parent_span_id=None if parent is None else parent.span_id,
            tool_name=tool_name,
            tool_input_digest=digest,
            tool_result_status=status,
            start=None if start_ms is None else at(start_ms),
            end=None if end_ms is None else at(end_ms),
            tool_input_preview=input_preview,
            tool_result_preview=result_preview,
        )

    def system_event(
        self,
        *,
        agent_id: str = "root",
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> Span:
        """One ``system_event`` span — excluded from R19's signature sequence."""
        return self._add(
            agent_id=agent_id,
            kind="system_event",
            start=None if start_ms is None else at(start_ms),
            end=None if end_ms is None else at(end_ms),
        )

    def user_message(self, *, agent_id: str = "root") -> Span:
        """One ``user_message`` span — also excluded from R19's sequence."""
        return self._add(agent_id=agent_id, kind="user_message")

    def warning(self, code: str, count: int, detail: str = "") -> None:
        """Record a ``ParseWarning`` the built trace will carry (R10)."""
        self._warnings.append(ParseWarning(code=code, count=count, detail=detail))

    def replace(self, span: Span, **updates: Any) -> Span:
        """Swap ``span`` for a copy carrying ``updates``.

        The one door to a shape the mapper cannot produce — a negative duration,
        a ``tool_call`` with usage — which several requirements pin behaviour
        for and no fixture can hold.
        """
        replacement = span.model_copy(update=updates)
        self._spans[span.seq] = replacement
        return replacement

    # -- the product ----------------------------------------------------------

    def build(self, trace_id: str = SYNTHETIC_TRACE_ID) -> Trace:
        """The validated ``Trace``, with agents and warnings derived from spans."""
        order: list[str] = []
        for span in self._spans:
            if span.agent_id not in order:
                order.append(span.agent_id)
        agents = tuple(
            AgentRun(
                agent_id=agent_id,
                agent_index=index,
                span_seqs=tuple(span.seq for span in self._spans if span.agent_id == agent_id),
            )
            for index, agent_id in enumerate(order)
        )
        keys = sorted({(warning.code, warning.detail) for warning in self._warnings})
        warnings = tuple(
            ParseWarning(
                code=code,
                detail=detail,
                count=sum(
                    warning.count
                    for warning in self._warnings
                    if (warning.code, warning.detail) == (code, detail)
                ),
            )
            for code, detail in keys
        )
        return Trace(
            schema_version=TRACE_SCHEMA_VERSION,
            trace_id=trace_id,
            adapter="claude_code_jsonl",
            spans=tuple(self._spans),
            agents=agents,
            warnings=warnings,
        )


# --- ready-made shapes the boundary tests reuse --------------------------------


def token_population(values: Sequence[int]) -> Trace:
    """One agent, one ``model_call`` per value, that value as its token total."""
    builder = TraceBuilder()
    for value in values:
        builder.model_call(usage=tokens(value))
    return builder.build()


def duration_population(durations_ms: Sequence[int], *, token_total: int = 100) -> Trace:
    """One agent, one timed ``model_call`` per duration, all with equal usage."""
    builder = TraceBuilder()
    for duration in durations_ms:
        builder.model_call(usage=tokens(token_total), start_ms=0, end_ms=duration)
    return builder.build()


def error_run(span_count: int, error_positions: Iterable[int], *, agent_id: str = "root") -> Trace:
    """One agent of ``span_count`` tool calls, erroring at ``error_positions`` (R20)."""
    failing = set(error_positions)
    builder = TraceBuilder()
    for index in range(span_count):
        builder.tool_call(
            agent_id=agent_id,
            status="error" if index in failing else "ok",
            result_preview=f"failure {index}" if index in failing else "",
        )
    return builder.build()


def two_span_gap(gap_seconds: int, *, covered_seconds: int = 0) -> Trace:
    """One agent with a single gap of ``gap_seconds``, ``covered_seconds`` of it busy.

    The covering interval belongs to a *second* agent, which is the only kind
    R23 counts.
    """
    builder = TraceBuilder()
    builder.model_call(agent_id="root", start_ms=0, end_ms=1_000)
    builder.model_call(
        agent_id="root",
        start_ms=1_000 + gap_seconds * 1_000,
        end_ms=1_000 + gap_seconds * 1_000 + 1_000,
    )
    if covered_seconds > 0:
        builder.model_call(
            agent_id="sub",
            start_ms=1_000,
            end_ms=1_000 + covered_seconds * 1_000,
        )
    return builder.build()


def loop_of(signatures: Sequence[str], *, agent_id: str = "root") -> Trace:
    """One ``model_call`` per letter, each emitting a tool call named for it (R19).

    The signature of a model call is ``("tool", name, digest)``, so distinct
    letters are distinct decisions and equal letters are the same decision.
    """
    builder = TraceBuilder()
    for letter in signatures:
        call = builder.model_call(agent_id=agent_id)
        builder.tool_call(
            agent_id=agent_id,
            parent=call,
            tool_name=f"Tool{letter}",
            digest=hex_id(ord(letter)),
        )
    return builder.build()


__all__ = [
    "DIGEST_A",
    "DIGEST_B",
    "EPOCH",
    "SYNTHETIC_TRACE_ID",
    "TraceBuilder",
    "at",
    "duration_population",
    "error_run",
    "hex_id",
    "loop_of",
    "token_population",
    "tokens",
    "two_span_gap",
]
