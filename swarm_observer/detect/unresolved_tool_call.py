"""``unresolved_tool_call`` — a tool call that never came back (R22).

Three closed reasons, and one carve-out that is the difference between a useful
detector and a nuisance:

* ``no_result`` — the call has no matching result anywhere in the trace.
* ``orphan_result`` — a result arrived for a call that is not in the trace.
* ``unknown_tool`` — the call failed with a runtime message saying the tool does
  not exist.

**The carve-out.** A transcript captured while an agent is still working always
ends on a tool call whose result has not been written yet. Firing on it would
make this detector produce a false positive on essentially every live capture,
which is the fastest way to teach a reader to ignore a whole section of a
report. So the highest-``seq`` span of an agent is excluded here and counted at
parse time as the ``dangling_tool_use`` warning instead (R10) — visible, but not
a finding. The exclusion is deliberately *positional*, not "the last one we saw
fail": a mid-trace unmatched call and a trailing one both exist in AC9's second
case, and only the trailing one is carved out.

``unknown_tool``'s five phrases are the only heuristic string matching against
trace content in the whole product (A8). A miss degrades the finding to
``failed_tool_call``, never to a crash and never to a false safety claim. Note
one consequence worth knowing: under ``--no-previews`` (A10) the result text
does not exist in the process, so every ``unknown_tool`` degrades to
``failed_tool_call``. That is the correct trade — the mode exists to guarantee
no trace text is anywhere in the process — but it means the two modes can report
different *reasons* for the same trace.

``wasted`` is zero: R22 names no redundant model calls, and R17's default for a
detector with no redundancy notion is a zero :class:`TokenUsage`.
"""

from __future__ import annotations

from swarm_observer.detect.base import (
    UNKNOWN_TOOL_NAME,
    Detector,
    DetectorConfig,
    Finding,
    Severity,
    build_finding,
    constrain_tool_name,
    sort_findings,
    spans_by_agent,
)
from swarm_observer.model.trace import Span, Trace

#: R22's closed reason set, in the order findings are produced.
REASONS: tuple[str, ...] = ("no_result", "orphan_result", "unknown_tool")

#: R22, A8: the pinned phrases a runtime uses when a tool does not exist.
#: Matched case-insensitively against the tool result's preview text.
UNKNOWN_TOOL_PATTERNS: tuple[str, ...] = (
    "no such tool",
    "tool not found",
    "unknown tool",
    "is not a recognized tool",
    "unrecognized tool name",
)

#: R10: the warning the mapper records for a result with no matching call.
ORPHAN_WARNING_CODE = "orphan_tool_result"


def looks_like_unknown_tool(result_text: str) -> bool:
    """R22, A8: does a tool result say the tool does not exist?"""
    lowered = result_text.lower()
    return any(pattern in lowered for pattern in UNKNOWN_TOOL_PATTERNS)


class UnresolvedToolCall:
    """R22: tool calls with no result, results with no call, calls to no tool."""

    slug = "unresolved_tool_call"
    title = "Unresolved tool call"
    default_severity: Severity = "warning"

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Bucket unresolved calls by ``(agent_id, reason)`` (R22)."""
        buckets: dict[tuple[str, str], list[Span]] = {}
        for agent_id, spans in spans_by_agent(trace).items():
            if not spans:
                continue
            # The carve-out: the agent's highest-seq span, whatever it is. The
            # mapper counts exactly this span as `dangling_tool_use` (R10), so
            # the two halves of R22's rule cannot drift apart.
            trailing = spans[-1]
            for span in spans:
                if span.kind != "tool_call":
                    continue
                if span.tool_result_status == "missing" and span.seq != trailing.seq:
                    buckets.setdefault((agent_id, "no_result"), []).append(span)
                elif span.tool_result_status == "error" and looks_like_unknown_tool(
                    span.tool_result_preview
                ):
                    buckets.setdefault((agent_id, "unknown_tool"), []).append(span)

        findings = [self._span_finding(trace, key, members) for key, members in buckets.items()]
        orphan = self._orphan_finding(trace)
        if orphan is not None:
            findings.append(orphan)
        return sort_findings(findings)

    def _span_finding(self, trace: Trace, key: tuple[str, str], members: list[Span]) -> Finding:
        """One ``(agent_id, reason)`` bucket that has spans behind it (R22)."""
        agent_id, reason = key
        tool_name, overflow = constrain_tool_name(members[0].tool_name)
        severity: Severity = "critical" if reason == "unknown_tool" else self.default_severity
        previews = [span.tool_result_preview or span.tool_input_preview for span in members]
        if overflow is not None:
            previews.append(overflow)
        return build_finding(
            trace=trace,
            detector=self.slug,
            severity=severity,
            summary=(
                f"{len(members)} tool calls of one agent are unresolved ({reason}); "
                f"first at span {members[0].seq}"
            ),
            metrics={
                "occurrences": len(members),
                "reason": reason,
                "tool_name": tool_name,
            },
            span_seqs=[span.seq for span in members],
            agent_ids=[agent_id],
            previews=previews,
        )

    def _orphan_finding(self, trace: Trace) -> Finding | None:
        """R22's ``orphan_result`` arm, from the parse warning (R10).

        A ``tool_result`` block whose ``tool_use_id`` matches no ``tool_use``
        produces **no span** — R12 emits a span per ``tool_use``, and a user
        record that is nothing but tool results emits none at all. So the only
        trace of an orphan in the normalized model (R2) is the aggregated
        ``orphan_tool_result`` warning, which carries a count and, by R10's
        design, no agent and no ``seq``.

        The finding is therefore emitted once for the trace with an empty
        ``agent_ids``, rather than invented against an arbitrary agent. R22 asks
        for ``(agent_id, reason)`` and the model cannot supply the agent half;
        saying so in the data is better than guessing (assumption A-b7 — the fix
        is a model field, which is an R2 amendment and a schema version bump).
        """
        total = sum(
            warning.count for warning in trace.warnings if warning.code == ORPHAN_WARNING_CODE
        )
        if total == 0:
            return None
        return build_finding(
            trace=trace,
            detector=self.slug,
            severity=self.default_severity,
            summary=(
                f"{total} tool results have no matching tool call in the trace (orphan_result)"
            ),
            metrics={
                "occurrences": total,
                "reason": "orphan_result",
                "tool_name": UNKNOWN_TOOL_NAME,
            },
            span_seqs=(),
            agent_ids=(),
        )


#: The registered instance (R13, R48).
DETECTOR: Detector = UnresolvedToolCall()

__all__ = [
    "DETECTOR",
    "ORPHAN_WARNING_CODE",
    "REASONS",
    "UNKNOWN_TOOL_PATTERNS",
    "UnresolvedToolCall",
    "looks_like_unknown_tool",
]
