"""``failed_tool_call`` — a tool that returned an error, per agent (R21).

The existence half of the pair R20 completes (A7). One finding per
``(agent_id, tool_name)``, so "``Bash`` failed nine times for the reviewer
agent" is one row rather than nine, and the same tool failing under two agents
stays two rows — which is usually the interesting distinction.

``wasted`` is zero and R21 says why: a failed call is not by itself waste. The
model call that issued it produced a real attempt; whether the *retry* was waste
is what ``retry_storm`` and ``agent_loop`` are for. Attributing tokens here as
well would double-count the same spans in the cost section.
"""

from __future__ import annotations

from swarm_observer.detect.base import (
    Detector,
    DetectorConfig,
    Finding,
    Severity,
    build_finding,
    constrain_tool_name,
    sort_findings,
)
from swarm_observer.model.trace import Span, Trace

#: R21: at this many failures the finding is a ``warning`` rather than ``info``.
WARNING_FAILURES = 2


class FailedToolCall:
    """R21: one finding per ``(agent_id, tool_name)`` with at least one error result."""

    slug = "failed_tool_call"
    title = "Failed tool call"
    default_severity: Severity = "info"

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Group every errored ``tool_call`` span by agent and tool name (R21)."""
        groups: dict[tuple[str, str | None], list[Span]] = {}
        for span in trace.spans:
            if span.kind != "tool_call" or span.tool_result_status != "error":
                continue
            groups.setdefault((span.agent_id, span.tool_name), []).append(span)

        findings: list[Finding] = []
        for (agent_id, recorded), members in sorted(
            groups.items(), key=lambda item: item[1][0].seq
        ):
            failures = len(members)
            tool_name, overflow = constrain_tool_name(recorded)
            severity: Severity = (
                "warning" if failures >= WARNING_FAILURES else self.default_severity
            )
            # R21: up to five *distinct* result previews, in seq order. Distinct
            # because a tool that fails the same way nine times teaches a reader
            # nothing on the ninth repetition, and the cap is five.
            seen: set[str] = set()
            previews: list[str] = []
            for span in members:
                text = span.tool_result_preview
                if text and text not in seen:
                    seen.add(text)
                    previews.append(text)
            if overflow is not None:
                previews.append(overflow)
            findings.append(
                build_finding(
                    trace=trace,
                    detector=self.slug,
                    severity=severity,
                    summary=(
                        f"one tool failed {failures} times for one agent "
                        f"(first at span {members[0].seq})"
                    ),
                    metrics={
                        "failures": failures,
                        "first_seq": members[0].seq,
                        "tool_name": tool_name,
                    },
                    span_seqs=[span.seq for span in members],
                    agent_ids=[agent_id],
                    previews=previews,
                )
            )
        return sort_findings(findings)


#: The registered instance (R13, R48).
DETECTOR: Detector = FailedToolCall()

__all__ = ["DETECTOR", "WARNING_FAILURES", "FailedToolCall"]
