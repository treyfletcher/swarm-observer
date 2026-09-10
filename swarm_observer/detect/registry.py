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

from swarm_observer.detect import (
    agent_loop,
    anomalous_span,
    blocked_agent,
    failed_tool_call,
    repeated_tool_call,
    retry_storm,
    unresolved_tool_call,
)
from swarm_observer.detect.base import Detector, DetectorConfig, Finding, sort_findings
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


def run_detectors(trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
    """Run every enabled detector and return one globally sorted tuple (R13).

    The result is sorted by ``(severity_rank, slug, finding_id)`` — the same key
    each detector sorts its own output by — so the report's Findings section is
    a function of the trace and the config and nothing else (R47).
    """
    findings: list[Finding] = []
    for detector in selected_detectors(config):
        findings.extend(detector.run(trace, config))
    return sort_findings(findings)


def slugs_of(detectors: Iterable[Detector]) -> tuple[str, ...]:
    """The slugs of ``detectors``, in the order given."""
    return tuple(detector.slug for detector in detectors)


__all__ = [
    "ALL_DETECTORS",
    "DETECTOR_SLUGS",
    "detector_by_slug",
    "run_detectors",
    "selected_detectors",
    "slugs_of",
]
