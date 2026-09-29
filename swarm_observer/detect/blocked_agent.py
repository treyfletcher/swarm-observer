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

from bisect import bisect_left, bisect_right
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


class CoverageIndex:
    """The union of a set of intervals, answered for any window in log time (R23).

    The union rule has exactly one implementation and this is it —
    :func:`covered_millis` is a thin call onto this class, so the helper the
    tests drive and the structure the detector uses cannot drift apart.

    Built once, queried many times. That is the whole point: ``blocked_agent``
    asks the same question of the same other-agent intervals once per gap, and
    re-clipping and re-sorting the entire interval list per gap is what made the
    detector quadratic in spans (BUG-3). Here the intervals are clipped-free:
    they are unioned once at construction into disjoint ascending runs with a
    prefix sum, and a window query is two binary searches plus arithmetic on
    the two partially-covered ends.

    Integer milliseconds throughout, and the terms summed are the same terms the
    clip-then-union form summed: the union of the clipped intervals is the clip
    of the union, so an interior run contributes its whole (already integral)
    length and only the two boundary runs are measured against the window.
    """

    def __init__(self, intervals: Sequence[tuple[UtcDatetime, UtcDatetime]]) -> None:
        merged: list[tuple[UtcDatetime, UtcDatetime]] = []
        for start, end in sorted(interval for interval in intervals if interval[1] > interval[0]):
            if merged and start <= merged[-1][1]:
                previous_start, previous_end = merged[-1]
                merged[-1] = (previous_start, max(previous_end, end))
            else:
                merged.append((start, end))
        self._starts = [start for start, _ in merged]
        self._ends = [end for _, end in merged]
        self._prefix = [0]
        for start, end in merged:
            self._prefix.append(self._prefix[-1] + millis_between(start, end))

    def covered(self, window_start: UtcDatetime, window_end: UtcDatetime) -> int:
        """The milliseconds of ``[window_start, window_end]`` this union covers."""
        if window_end <= window_start or not self._starts:
            return 0
        # The first run that reaches into the window, and the first that starts
        # after it ends.
        first = bisect_right(self._ends, window_start)
        limit = bisect_left(self._starts, window_end)
        if first >= limit:
            return 0
        if limit - first == 1:
            return millis_between(
                max(self._starts[first], window_start), min(self._ends[first], window_end)
            )
        total = self._prefix[limit - 1] - self._prefix[first + 1]
        total += millis_between(max(self._starts[first], window_start), self._ends[first])
        total += millis_between(self._starts[limit - 1], min(self._ends[limit - 1], window_end))
        return total


def covered_millis(
    intervals: Sequence[tuple[UtcDatetime, UtcDatetime]],
    window_start: UtcDatetime,
    window_end: UtcDatetime,
) -> int:
    """The milliseconds of ``[window_start, window_end]`` covered by ``intervals``.

    Overlapping intervals are unioned before they are measured, so concurrent
    agents cannot explain more of a gap than the gap contains.
    """
    return CoverageIndex(intervals).covered(window_start, window_end)


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
            others = CoverageIndex(self._other_intervals(by_agent, agent_id))
            for previous, following in pairwise(spans):
                if previous.end is None or following.start is None:
                    continue
                gap_ms = max(0, millis_between(previous.end, following.start))
                if gap_ms < threshold_ms:
                    continue
                covered = others.covered(previous.end, following.start)
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
    "CoverageIndex",
    "covered_millis",
]
