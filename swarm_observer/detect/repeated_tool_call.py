"""``repeated_tool_call`` — the same call, with the same arguments, again (R18).

The detector's whole view of a tool's arguments is R7's ``tool_input_digest``:
16 hex characters over the canonical JSON of the input. That is deliberate and
it is a security property, not an optimization — the arguments are the most
attacker-influenced bytes in a trace, and a detector that grouped on them would
be reading hostile text to decide what a report says. Grouping on the digest
means this module never touches a byte the trace chose.

Waste attribution (R17): occurrences 2..n are the redundant ones, and what they
cost is the *model call that emitted them*, not the tool call itself — a tool
call has no tokens. The first occurrence is not attributed: doing the work once
is not waste.
"""

from __future__ import annotations

from swarm_observer.detect.base import (
    Detector,
    DetectorConfig,
    Finding,
    Severity,
    attribute_waste,
    build_finding,
    constrain_tool_name,
    seq_by_span_id,
    sort_findings,
)
from swarm_observer.model.trace import Span, Trace

#: R18: a group of this size or larger is a finding at all.
MIN_OCCURRENCES = 2

#: R18: at this many occurrences the finding is ``critical`` rather than ``warning``.
CRITICAL_OCCURRENCES = 4


class RepeatedToolCall:
    """R18: one finding per ``(tool_name, tool_input_digest)`` group of size >= 2."""

    slug = "repeated_tool_call"
    title = "Repeated identical tool call"
    default_severity: Severity = "warning"

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Group every ``tool_call`` span by name and argument digest (R18)."""
        return sort_findings(finding for finding, _ in self.scan_with_waste(trace, config))

    def scan_with_waste(
        self, trace: Trace, config: DetectorConfig
    ) -> tuple[tuple[Finding, tuple[int, ...]], ...]:
        """R18's findings, each with the model calls it attributed (R17, R29).

        One scan produces both, so ``Finding.wasted`` and the cost engine's
        ``wasted_cost_usd`` are two readings of the same span list rather than
        two computations that agree until one of them is edited.
        """
        groups: dict[tuple[str | None, str | None], list[Span]] = {}
        for span in trace.spans:
            if span.kind != "tool_call":
                continue
            groups.setdefault((span.tool_name, span.tool_input_digest), []).append(span)

        seq_of = seq_by_span_id(trace)
        found: list[tuple[Finding, tuple[int, ...]]] = []
        # Iterate by the group's first span so the walk is a function of
        # canonical order (R6) rather than of dict insertion — the ordering is
        # already deterministic, but a reader should not have to know that.
        for members in sorted(groups.values(), key=lambda spans: spans[0].seq):
            occurrences = len(members)
            if occurrences < MIN_OCCURRENCES:
                continue
            tool_name, overflow = constrain_tool_name(members[0].tool_name)
            severity: Severity = (
                "critical" if occurrences >= CRITICAL_OCCURRENCES else self.default_severity
            )
            redundant = tuple(
                sorted(
                    {
                        seq_of[span.parent_span_id]
                        for span in members[1:]
                        if span.parent_span_id is not None and span.parent_span_id in seq_of
                    }
                )
            )
            previews = [members[0].tool_input_preview]
            if overflow is not None:
                previews.append(overflow)
            finding = build_finding(
                trace=trace,
                detector=self.slug,
                severity=severity,
                summary=(
                    f"one tool was called {occurrences} times with identical arguments "
                    f"(spans {members[0].seq}-{members[-1].seq})"
                ),
                metrics={
                    "first_seq": members[0].seq,
                    "last_seq": members[-1].seq,
                    "occurrences": occurrences,
                    "tool_name": tool_name,
                },
                span_seqs=[span.seq for span in members],
                agent_ids=[span.agent_id for span in members],
                previews=previews,
                wasted=attribute_waste(trace, redundant),
            )
            found.append((finding, redundant))
        return tuple(found)


#: The registered instance (R13, R48).
DETECTOR: Detector = RepeatedToolCall()

__all__ = ["CRITICAL_OCCURRENCES", "DETECTOR", "MIN_OCCURRENCES", "RepeatedToolCall"]
