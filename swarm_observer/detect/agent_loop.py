"""``agent_loop`` — an agent going round in circles (R19).

Each agent's model calls are reduced to a *signature sequence*: a call that
emitted a tool call is that tool's ``(name, input digest)``; a call that emitted
none is the single signature ``("text",)``. Tool calls, user messages and system
events are excluded, so a loop is measured in decisions rather than in records —
otherwise a three-turn cycle would look different depending on how many results
each turn happened to produce.

The search order is pinned by R19 and is the whole behaviour: **period ascending,
then start index ascending**, firing at the first triple repeat found. Ascending
period matters — ``A A A A A A`` is a period-1 loop repeated six times, not a
period-2 loop repeated three times, and reporting the latter would understate it.
Only after a loop is found is ``k`` extended to the maximal run, and the search
resumes past that run so one long trace can report several non-overlapping loops
without ever reporting the same repetition twice.

Waste attribution (R17): repeats 2..k. The first pass through a cycle is work;
the rest is the work being redone.
"""

from __future__ import annotations

from collections.abc import Sequence

from swarm_observer.detect.base import (
    Detector,
    DetectorConfig,
    Finding,
    Severity,
    attribute_waste,
    build_finding,
    first_tool_call_by_parent,
    sort_findings,
    spans_by_agent,
)
from swarm_observer.model.trace import Span, Trace

#: R19: periods 1 through 8 inclusive, searched in ascending order.
MAX_PERIOD = 8

#: R19: a cycle must repeat at least this many times consecutively to fire.
MIN_REPEATS = 3

#: R19: at this many repeats the finding is ``critical`` rather than ``warning``.
CRITICAL_REPEATS = 4

#: The signature of a model call that emitted no tool call.
TEXT_SIGNATURE: tuple[str, ...] = ("text",)


def signature_of(call: Span, first_tool: Span | None) -> tuple[str, ...]:
    """R19: a model call's signature — its first tool call, or ``("text",)``.

    ``tool_input_digest`` (R7), not the tool input, so the comparison never
    reads a byte the trace chose.
    """
    if first_tool is None:
        return TEXT_SIGNATURE
    return ("tool", first_tool.tool_name or "", first_tool.tool_input_digest or "")


def find_loop(
    signatures: Sequence[tuple[str, ...]], start_floor: int
) -> tuple[int, int, int] | None:
    """The first ``(period, start, repeats)`` at or after ``start_floor`` (R19).

    Period ascending, then start index ascending — exactly R19's iteration
    order. ``repeats`` is the maximal number of consecutive repetitions of
    ``signatures[start:start + period]`` beginning at ``start``, which is what
    turns "there is a loop" into "the loop ran this many times".
    """
    for period in range(1, MAX_PERIOD + 1):
        last_start = len(signatures) - MIN_REPEATS * period
        for start in range(start_floor, last_start + 1):
            cycle = signatures[start : start + period]
            if not all(
                signatures[start + step * period : start + (step + 1) * period] == cycle
                for step in range(1, MIN_REPEATS)
            ):
                continue
            repeats = MIN_REPEATS
            # A slice that runs off the end is shorter than ``cycle`` and so
            # cannot equal it; no separate bound check is needed, and adding one
            # would be a branch nothing can exercise.
            while signatures[start + repeats * period : start + (repeats + 1) * period] == cycle:
                repeats += 1
            return period, start, repeats
    return None


class AgentLoop:
    """R19: an agent repeating a cycle of decisions three or more times."""

    slug = "agent_loop"
    title = "Agent repeating itself"
    default_severity: Severity = "warning"

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Scan each agent's signature sequence for repeated cycles (R19)."""
        return sort_findings(finding for finding, _ in self.scan_with_waste(trace, config))

    def scan_with_waste(
        self, trace: Trace, config: DetectorConfig
    ) -> tuple[tuple[Finding, tuple[int, ...]], ...]:
        """R19's findings, each with the model calls it attributed (R17, R29)."""
        first_tool = first_tool_call_by_parent(trace)
        found: list[tuple[Finding, tuple[int, ...]]] = []
        for agent_id, spans in spans_by_agent(trace).items():
            calls = [span for span in spans if span.kind == "model_call"]
            signatures = [signature_of(call, first_tool.get(call.span_id)) for call in calls]
            start_floor = 0
            while True:
                loop = find_loop(signatures, start_floor)
                if loop is None:
                    break
                period, start, repeats = loop
                covered = calls[start : start + repeats * period]
                redundant = tuple(
                    sorted({call.seq for call in calls[start + period : start + repeats * period]})
                )
                severity: Severity = (
                    "critical" if repeats >= CRITICAL_REPEATS else self.default_severity
                )
                finding = build_finding(
                    trace=trace,
                    detector=self.slug,
                    severity=severity,
                    summary=(
                        f"an agent repeated a {period}-step cycle {repeats} times "
                        f"(spans {covered[0].seq}-{covered[-1].seq})"
                    ),
                    metrics={
                        "end_seq": covered[-1].seq,
                        "period": period,
                        "repeats": repeats,
                        "start_seq": covered[0].seq,
                    },
                    span_seqs=[call.seq for call in covered],
                    agent_ids=[agent_id],
                    previews=[covered[0].text_preview],
                    wasted=attribute_waste(trace, redundant),
                )
                found.append((finding, redundant))
                # R19: resume past the run just reported, so the same repetition
                # is never reported twice and a later, separate loop still is.
                start_floor = start + repeats * period
        return tuple(found)


#: The registered instance (R13, R48).
DETECTOR: Detector = AgentLoop()

__all__ = [
    "CRITICAL_REPEATS",
    "DETECTOR",
    "MAX_PERIOD",
    "MIN_REPEATS",
    "TEXT_SIGNATURE",
    "AgentLoop",
    "find_loop",
    "signature_of",
]
