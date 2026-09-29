"""One in-process run of ingest → detect → cost → render. Not a test module.

The CLI is exercised as a subprocess elsewhere (``tests/harness.run_cli``). This
module is the in-process twin, and it exists so a security probe can hold the
*inputs* — the ``Trace``, the findings, the ``Timeline`` — at the same time as
the rendered bytes. R34's attribute allowlist is generated from those inputs, so
a probe that could only see the output would have to rebuild the allowlist from
the output, which is the defect R34's own wording warns about.

Everything here mirrors ``cli.main.analyze`` exactly, including the
``wasted_cost_usd`` back-fill, so a document produced here is the document the
CLI writes. ``test_offline_determinism_r46_r47`` asserts that equality rather
than assuming it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from swarm_observer import __version__
from swarm_observer.cost.compute import CostReport, compute_costs
from swarm_observer.cost.snapshot import SnapshotRateSource
from swarm_observer.detect.base import DetectorConfig, Finding
from swarm_observer.detect.registry import DETECTOR_SLUGS, run_detectors_with_waste
from swarm_observer.ingest.registry import build_adapter
from swarm_observer.ingest.source import IngestLimits
from swarm_observer.model.trace import Trace
from swarm_observer.report.html import attribute_allowlist, render_html
from swarm_observer.report.json_out import render_json
from swarm_observer.report.sanitize import RenderOptions
from swarm_observer.report.timeline import Timeline, build_timeline


@dataclass(frozen=True)
class Analysis:
    """Everything one analyze run produced, inputs and outputs together."""

    trace: Trace
    findings: tuple[Finding, ...]
    cost: CostReport
    options: RenderOptions
    timeline: Timeline
    html: str
    json: str

    def allowlist(self) -> dict[str, frozenset[str]]:
        """R34's allowlist for *these* inputs — never harvested from the output."""
        return attribute_allowlist(trace=self.trace, findings=self.findings, timeline=self.timeline)


def analyze_paths(
    paths: Sequence[Path],
    *,
    previews: bool = True,
    blocked_gap_seconds: int = 60,
    detectors: frozenset[str] | None = None,
    limits: IngestLimits | None = None,
) -> Analysis:
    """Run the analyze path over ``paths`` and return inputs and both documents."""
    adapter = build_adapter("claude_code_jsonl", no_previews=not previews)
    trace = adapter.load(list(paths), limits or IngestLimits())
    return analyze_trace(
        trace, previews=previews, blocked_gap_seconds=blocked_gap_seconds, detectors=detectors
    )


def analyze_trace(
    trace: Trace,
    *,
    previews: bool = True,
    blocked_gap_seconds: int = 60,
    detectors: frozenset[str] | None = None,
) -> Analysis:
    """The half of :func:`analyze_paths` that starts from an already-built ``Trace``."""
    config = DetectorConfig(blocked_gap_seconds=blocked_gap_seconds, enabled=detectors)
    detected = run_detectors_with_waste(trace, config)
    cost = compute_costs(trace, SnapshotRateSource(), waste_seqs=detected.waste_seqs)
    findings = tuple(
        finding.model_copy(
            update={"wasted_cost_usd": cost.waste_by_finding.get(finding.finding_id)}
        )
        for finding in detected.findings
    )
    options = RenderOptions(
        previews=previews,
        blocked_gap_seconds=blocked_gap_seconds,
        # `cli.main.selected_slugs`, inlined rather than imported: `report/`
        # must not depend on `cli/` (R44) and neither should the helper that
        # claims to reproduce it. `test_offline_determinism_r46_r47` asserts
        # the CLI's own bytes equal this module's, which is what keeps the
        # duplication honest.
        detectors=tuple(slug for slug in DETECTOR_SLUGS if detectors is None or slug in detectors),
    )
    return Analysis(
        trace=trace,
        findings=findings,
        cost=cost,
        options=options,
        timeline=build_timeline(trace, findings),
        html=render_html(
            trace=trace,
            findings=findings,
            cost=cost,
            tool_version=__version__,
            options=options,
        ),
        json=render_json(
            trace=trace,
            findings=findings,
            cost=cost,
            tool_version=__version__,
            options=options,
        ),
    )


__all__ = ["Analysis", "analyze_paths", "analyze_trace"]
