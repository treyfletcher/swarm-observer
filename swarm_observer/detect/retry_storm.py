"""``retry_storm`` — errors arriving faster than an agent is making progress (R20).

This detector deliberately overlaps :mod:`swarm_observer.detect.failed_tool_call`
(R21, A7): that one reports *existence* — this tool failed, here is how often —
and this one reports *density*. Three failures spread over a two-hour session is
a flaky tool; three failures inside ten consecutive spans is an agent stuck in a
retry loop, and those are different things for a reader to know. The report
groups by detector so the duplication reads as two signals rather than as one
signal counted twice.

Two implementation choices are worth stating because neither is forced by R20's
text and both change what fires:

* **A window shorter than ten spans still counts.** R20 says "a sliding window
  of 10 consecutive spans"; an agent with six spans has no such window, and a
  literal reading would make this detector structurally unable to fire on a
  short agent — five spans, four of them errors, silently clean. When an agent
  has fewer than ten spans its whole span list is the single window.
* **The merged run is trimmed to its errors.** Unioning raw ten-span windows
  would report a run padded with whatever happened to sit either side of the
  errors, and ``window_spans`` would then measure the window size rather than
  the storm. The run reported starts at its first error span and ends at its
  last.

Waste attribution (R17): every ``model_call`` in the merged run. A storm is the
model being asked to try again, and those retries are the tokens.
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
    sort_findings,
    spans_by_agent,
)
from swarm_observer.model.trace import Span, Trace

#: R20: the number of consecutive spans a window spans.
WINDOW_SPANS = 10

#: R20: how many error spans a window needs before it counts.
MIN_ERRORS = 3

#: R20: at this many errors in the merged run the finding is ``critical``.
CRITICAL_ERRORS = 5

#: R20's ``kinds`` metric, a closed enumeration.
KIND_TOOL = "tool_error"
KIND_API = "api_error"
KIND_MIXED = "mixed"


def error_kind(span: Span) -> str | None:
    """R20: ``tool_error``, ``api_error``, or ``None`` when the span is not an error."""
    if span.kind == "tool_call" and span.tool_result_status == "error":
        return KIND_TOOL
    if span.kind == "model_call" and span.error is not None:
        return KIND_API
    return None


def error_text(span: Span) -> str:
    """The evidence text for one error span: its tool result, else its error detail."""
    if span.tool_result_preview:
        return span.tool_result_preview
    return span.error.detail if span.error is not None else ""


def merge_runs(windows: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Collapse overlapping half-open index ranges into maximal runs (R20).

    The ranges are **half-open**, so ``[0, 10)`` and ``[10, 20)`` share no span
    and are two runs, not one. The test is therefore ``start < previous_stop``
    and not ``<=``: R20 merges *overlapping* windows, and two windows that merely
    abut have no span in common. Getting this wrong does not lose a finding — it
    manufactures one, by adding two clusters' error counts together and reporting
    a density no ten-span window supports.
    """
    merged: list[tuple[int, int]] = []
    for start, stop in sorted(windows):
        if merged and start < merged[-1][1]:
            previous_start, previous_stop = merged[-1]
            merged[-1] = (previous_start, max(previous_stop, stop))
        else:
            merged.append((start, stop))
    return merged


class RetryStorm:
    """R20: three or more error spans inside ten consecutive spans of one agent."""

    slug = "retry_storm"
    title = "Retry storm"
    default_severity: Severity = "warning"

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Slide a ten-span window over each agent and merge what qualifies (R20)."""
        findings: list[Finding] = []
        for agent_id, spans in spans_by_agent(trace).items():
            kinds = [error_kind(span) or "" for span in spans]
            error_positions = [index for index, kind in enumerate(kinds) if kind]
            if len(error_positions) < MIN_ERRORS:
                continue
            windows = [
                (start, min(start + WINDOW_SPANS, len(spans)))
                for start in range(max(1, len(spans) - WINDOW_SPANS + 1))
                if sum(
                    1 for position in error_positions if start <= position < start + WINDOW_SPANS
                )
                >= MIN_ERRORS
            ]
            for start, stop in merge_runs(windows):
                inside = [position for position in error_positions if start <= position < stop]
                run_start, run_stop = inside[0], inside[-1] + 1
                run = spans[run_start:run_stop]
                observed = {kinds[position] for position in inside}
                kind = observed.pop() if len(observed) == 1 else KIND_MIXED
                severity: Severity = (
                    "critical" if len(inside) >= CRITICAL_ERRORS else self.default_severity
                )
                findings.append(
                    build_finding(
                        trace=trace,
                        detector=self.slug,
                        severity=severity,
                        summary=(
                            f"{len(inside)} errors within a run of {len(run)} spans "
                            f"(spans {run[0].seq}-{run[-1].seq}, kinds: {kind})"
                        ),
                        metrics={
                            "end_seq": run[-1].seq,
                            "errors": len(inside),
                            "kinds": kind,
                            "start_seq": run[0].seq,
                            "window_spans": len(run),
                        },
                        span_seqs=[spans[position].seq for position in inside],
                        agent_ids=[agent_id],
                        previews=[error_text(spans[position]) for position in inside],
                        wasted=attribute_waste(
                            trace, [span.seq for span in run if span.kind == "model_call"]
                        ),
                    )
                )
        return sort_findings(findings)


#: The registered instance (R13, R48).
DETECTOR: Detector = RetryStorm()

__all__ = [
    "CRITICAL_ERRORS",
    "DETECTOR",
    "KIND_API",
    "KIND_MIXED",
    "KIND_TOOL",
    "MIN_ERRORS",
    "WINDOW_SPANS",
    "RetryStorm",
    "error_kind",
    "error_text",
    "merge_runs",
]
