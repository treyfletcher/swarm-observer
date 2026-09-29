"""``ALL_DETECTORS`` — the single source of truth for which detectors exist (R13, R48).

Not a convenience list. R48 builds its coverage guarantee on this tuple: every
detector named here must have a fixture that fires it *and* a fixture that does
not, and a second test AST-scans ``detect/`` so a detector cannot exist outside
the registry and thereby outside that guarantee. A module that defines a
detector and forgets to register it fails the suite rather than shipping as a
feature nobody can see.

The order is R18 through R24 — the order the spec introduces them — and it is
fixed, because ``detectors`` (R38) prints it and a report groups by it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from swarm_observer.detect import (
    agent_loop,
    anomalous_span,
    blocked_agent,
    failed_tool_call,
    repeated_tool_call,
    retry_storm,
    unresolved_tool_call,
)
from swarm_observer.detect.base import (
    Detector,
    DetectorConfig,
    Finding,
    WasteAttributor,
    sort_findings,
)
from swarm_observer.model.trace import Trace

#: R13: every detector, in a fixed order.
ALL_DETECTORS: tuple[Detector, ...] = (
    repeated_tool_call.DETECTOR,
    agent_loop.DETECTOR,
    retry_storm.DETECTOR,
    failed_tool_call.DETECTOR,
    unresolved_tool_call.DETECTOR,
    blocked_agent.DETECTOR,
    anomalous_span.DETECTOR,
)

#: The registry's slugs, in registry order.
DETECTOR_SLUGS: tuple[str, ...] = tuple(detector.slug for detector in ALL_DETECTORS)


def detector_by_slug(slug: str) -> Detector:
    """The registered detector called ``slug``.

    Raises :class:`KeyError` for an unknown slug; the CLI turns that into R39's
    usage error (exit 3), because naming a detector that does not exist is a
    mistake in the command rather than in the trace.
    """
    for detector in ALL_DETECTORS:
        if detector.slug == slug:
            return detector
    raise KeyError(slug)


def selected_detectors(config: DetectorConfig) -> tuple[Detector, ...]:
    """The detectors ``config`` enables, in registry order (R25)."""
    if config.enabled is None:
        return ALL_DETECTORS
    return tuple(detector for detector in ALL_DETECTORS if detector.slug in config.enabled)


@dataclass(frozen=True)
class DetectorRun:
    """One detection pass: the findings, and what each attributed waste to.

    ``waste_seqs`` maps every produced ``finding_id`` to the ``seq`` values of
    the ``model_call`` spans its detector named as redundant (R17) — empty for a
    detector R18-R24 gives no redundancy notion. The cost engine turns that into
    ``Finding.wasted_cost_usd`` and R31's per-detector waste grouping; see
    :class:`~swarm_observer.detect.base.WasteAttributor` for why the span list
    rather than the summed ``TokenUsage`` is what travels.

    Every finding has an entry, including a zero-waste one. An absent key and a
    key mapping to no spans are different claims, and only one of them is true
    of a detector that ran.
    """

    findings: tuple[Finding, ...]
    waste_seqs: dict[str, tuple[int, ...]]


def run_detectors_with_waste(trace: Trace, config: DetectorConfig) -> DetectorRun:
    """Run every enabled detector once, keeping its waste attribution (R13, R17).

    A detector that implements
    :class:`~swarm_observer.detect.base.WasteAttributor` is asked for findings
    and attributions in **one** scan, so the two cannot disagree; the rest are
    run normally and attribute nothing.
    """
    findings: list[Finding] = []
    waste: dict[str, tuple[int, ...]] = {}
    for detector in selected_detectors(config):
        if isinstance(detector, WasteAttributor):
            for finding, seqs in detector.scan_with_waste(trace, config):
                findings.append(finding)
                waste[finding.finding_id] = seqs
            continue
        for finding in detector.run(trace, config):
            findings.append(finding)
            waste[finding.finding_id] = ()
    return DetectorRun(findings=sort_findings(findings), waste_seqs=waste)


def run_detectors(trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
    """Run every enabled detector and return one globally sorted tuple (R13).

    The result is sorted by ``(severity_rank, slug, finding_id)`` — the same key
    each detector sorts its own output by — so the report's Findings section is
    a function of the trace and the config and nothing else (R47).
    """
    return run_detectors_with_waste(trace, config).findings


def slugs_of(detectors: Iterable[Detector]) -> tuple[str, ...]:
    """The slugs of ``detectors``, in the order given."""
    return tuple(detector.slug for detector in detectors)


__all__ = [
    "ALL_DETECTORS",
    "DETECTOR_SLUGS",
    "DetectorRun",
    "detector_by_slug",
    "run_detectors",
    "run_detectors_with_waste",
    "selected_detectors",
    "slugs_of",
]
