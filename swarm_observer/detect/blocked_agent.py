"""``blocked_agent`` — an agent that stopped, with nothing else running (R23).

A long gap in one agent's timeline is only interesting if nobody was working
during it. An orchestrator that spawns a subagent and waits four minutes is
doing exactly what it should; the same four minutes with every agent idle is a
stall worth a reader's attention. R23's *explained gap* rule is that
distinction, and it is the reason this detector is not simply "print every gap".

The arithmetic is integer milliseconds throughout (R23). Coverage is measured as
the union of other agents' ``[start, end]`` intervals clipped to the gap — a
union, not a sum, because two subagents running concurrently for thirty seconds
explain thirty seconds of the gap, not sixty.

``wasted`` is zero: waiting costs wall-clock time, not tokens.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from swarm_observer.detect.base import (
    Detector,
    DetectorConfig,
    Finding,
    Severity,
    build_finding,
    millis_between,
    sort_findings,
    spans_by_agent,
)
from swarm_observer.model.trace import Span, Trace, UtcDatetime

#: R23: at or above this many seconds the gap is ``critical`` rather than ``warning``.
CRITICAL_GAP_SECONDS = 300

#: R23: the share of a gap other agents must cover for it to count as explained,
#: expressed as a fraction so the comparison stays in integers
#: (``covered * COVERAGE_DENOMINATOR >= gap * COVERAGE_NUMERATOR``).
COVERAGE_NUMERATOR = 1
COVERAGE_DENOMINATOR = 2


def covered_millis(
    intervals: Sequence[tuple[UtcDatetime, UtcDatetime]],
    window_start: UtcDatetime,
    window_end: UtcDatetime,
) -> int:
    """The milliseconds of ``[window_start, window_end]`` covered by ``intervals``.

    Overlapping intervals are unioned before they are measured, so concurrent
    agents cannot explain more of a gap than the gap contains.
    """
    clipped: list[tuple[UtcDatetime, UtcDatetime]] = []
    for start, end in intervals:
        low = max(start, window_start)
        high = min(end, window_end)
        if high > low:
            clipped.append((low, high))
    total = 0
    current_start: UtcDatetime | None = None
    current_end: UtcDatetime | None = None
    for start, end in sorted(clipped):
        if current_end is None or current_start is None:
            current_start, current_end = start, end
        elif start <= current_end:
            current_end = max(current_end, end)
        else:
            total += millis_between(current_start, current_end)
            current_start, current_end = start, end
    if current_start is not None and current_end is not None:
        total += millis_between(current_start, current_end)
    return total


class BlockedAgent:
    """R23: a long gap in one agent's timeline that no other agent explains."""

    slug = "blocked_agent"
    title = "Blocked agent"
    default_severity: Severity = "warning"

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Measure every consecutive-span gap and ask whether anyone was working (R23)."""
        by_agent = spans_by_agent(trace)
        threshold_ms = config.blocked_gap_seconds * 1_000
        findings: list[Finding] = []
        for agent_id, spans in by_agent.items():
            others = self._other_intervals(by_agent, agent_id)
            for previous, following in pairwise(spans):
                if previous.end is None or following.start is None:
                    continue
                gap_ms = max(0, millis_between(previous.end, following.start))
                if gap_ms < threshold_ms:
                    continue
                covered = covered_millis(others, previous.end, following.start)
                if covered * COVERAGE_DENOMINATOR >= gap_ms * COVERAGE_NUMERATOR:
                    continue  # explained: other agents were working through it
                gap_seconds = gap_ms // 1_000
                severity: Severity = (
                    "critical" if gap_seconds >= CRITICAL_GAP_SECONDS else self.default_severity
                )
                findings.append(
                    build_finding(
                        trace=trace,
                        detector=self.slug,
                        severity=severity,
                        summary=(
                            f"an agent waited {gap_seconds}s between spans {previous.seq} "
                            f"and {following.seq} with no other agent working"
                        ),
                        metrics={
                            "after_seq": following.seq,
                            "before_seq": previous.seq,
                            "gap_seconds": gap_seconds,
                        },
                        span_seqs=[previous.seq, following.seq],
                        agent_ids=[agent_id],
                    )
                )
        return sort_findings(findings)

    def _other_intervals(
        self, by_agent: dict[str, tuple[Span, ...]], agent_id: str
    ) -> list[tuple[UtcDatetime, UtcDatetime]]:
        """Every timed interval belonging to an agent other than ``agent_id`` (R23)."""
        intervals: list[tuple[UtcDatetime, UtcDatetime]] = []
        for other_id, spans in by_agent.items():
            if other_id == agent_id:
                continue
            for span in spans:
                if span.start is not None and span.end is not None and span.end > span.start:
                    intervals.append((span.start, span.end))
        return intervals


#: The registered instance (R13, R48).
DETECTOR: Detector = BlockedAgent()

__all__ = [
    "COVERAGE_DENOMINATOR",
    "COVERAGE_NUMERATOR",
    "CRITICAL_GAP_SECONDS",
    "DETECTOR",
    "BlockedAgent",
    "covered_millis",
]
