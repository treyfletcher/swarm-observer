"""Findings → a request payload that cannot carry trace text (R42).

## How the no-trace-content property is enforced

Structurally, in two layers, and neither of them is a filter:

1. **The payload's type has no field that can hold free text.** Every string
   in a :class:`~swarm_observer.narrate.client.NarrationRequest` is a
   :class:`typing.Literal`, a member of a closed vocabulary this package
   computed, or a pattern-constrained money/key/version string, and
   construction raises otherwise. A sentinel is not something this module
   filters out badly; it is something the type refuses.

2. **The builder has almost nothing to leak.** R44 forbids ``narrate`` from
   importing ``ingest``, ``cost`` and ``report``, and this package additionally
   does not import ``model``. So no ``Trace``, no ``Span``, no ``SourceFile``,
   no ``CostReport`` and no ``SpanCost`` is in scope here — the cost figures
   arrive as already-formatted strings and integers from the caller, which is
   why :func:`build_totals` reads the way it does.

   The single object in scope that has ever touched the trace is a
   :class:`~swarm_observer.detect.base.Finding`, because R42 says the payload
   is built "from findings only". Its trace-derived surface is
   ``previews`` (R8 text) and ``metrics["tool_name"]`` (R16). ``previews`` has
   no field in the payload type to be assigned to; ``tool_name`` is dropped by
   consulting :data:`~swarm_observer.detect.base.TRACE_DERIVED_METRIC_KEYS` —
   the same machine-readable list the renderers' redaction policy consults, so
   a second trace-derived metric key added in v2 is dropped here without
   anybody remembering to. ``agent_ids`` and ``span_seqs`` become counts.

That is the honest scope of the claim, and it is why AC12's sentinel test is a
check on a property that already holds rather than the thing that makes it
hold.

## Two decisions this module had to take, both filed

* **``metrics.tool_name`` is dropped although R42 permits it.** R42 lists
  "``tool_name`` values already constrained by R16" among what the payload may
  contain; AC12 requires that no free-text field of a sentinel trace appear in
  the serialized payload, and a sentinel such as ``SENTINELSpantoolname``
  satisfies R16's pattern exactly. The two clauses of one requirement pair
  disagree; this module takes the narrower. **S33**, A-e3.

* **The cost figures are supplied by the caller, not read from a
  ``CostReport``.** R42 requires "per-agent and per-model token and cost
  totals" in a payload built by this module; R44 forbids this package from
  importing ``cost``. Both are pinned requirements and they cannot both be
  satisfied by an import. R44 wins, because it is the one with a checked-in
  AST test and because obeying it makes the property in §1 stronger rather
  than weaker. **S34**, A-e4.

* **Agents are named by ``agent_index`` and models by ``model_key``.** An
  ``agent_id`` is trace-derived (R5 takes it from the record's ``agentId``) and
  a recorded ``Span.model`` is trace-derived (R30 says so). The integer R2
  pins to ``range(len(agents))`` and the snapshot key out of
  ``model_rates.json`` are not.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from swarm_observer.detect.base import (
    SEVERITIES,
    SEVERITY_RANK,
    TRACE_DERIVED_METRIC_KEYS,
    Finding,
)
from swarm_observer.detect.registry import ALL_DETECTORS
from swarm_observer.narrate.client import (
    MAX_FINDINGS_PER_GROUP,
    MAX_TOTAL_ROWS,
    OVERALL_GROUP,
    OVERALL_TITLE,
    AgentTotals,
    FindingSummary,
    GroupSummary,
    MetricEntry,
    ModelTotals,
    NarrationRequest,
    TraceTotals,
)


def narration_groups(findings: Sequence[Finding]) -> tuple[str, ...]:
    """R43: the group keys a run asks about, in a fixed order.

    ``overall`` first, then one per detector that produced a finding, in
    **registry order** (R13) — the order ``swarm-observer detectors`` prints
    and the order the spec introduces them, so a reader of the narrative and a
    reader of the findings table meet the detectors in a consistent sequence.
    Registry order rather than the findings' own severity-descending order,
    because the latter moves when one finding's severity changes and a
    narrative whose paragraph order depends on how bad the run was is harder to
    diff between two runs.

    A detector with no findings gets no paragraph: R43 says "one paragraph per
    *finding group*", and a group with nothing in it is not one.
    """
    present = {finding.detector for finding in findings}
    return (OVERALL_GROUP, *(d.slug for d in ALL_DETECTORS if d.slug in present))


def severity_counts(findings: Sequence[Finding]) -> dict[str, int]:
    """``{severity: count}``, every severity present, keys sorted (R42, R47).

    Every key exists even at zero, for the reason ``report.json``'s own counts
    do: a missing key makes "no critical findings" indistinguishable from "the
    counter was never written", and a narrator reading the second as the first
    writes a paragraph that is wrong in the most expensive direction.
    """
    counts = dict.fromkeys(sorted(SEVERITIES), 0)
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def _metrics(finding: Finding) -> tuple[MetricEntry, ...]:
    """The metrics this finding may show a narrator, in key order (R42, S33)."""
    return tuple(
        MetricEntry(key=key, value=value)
        for key, value in sorted(finding.metrics.items())
        if key not in TRACE_DERIVED_METRIC_KEYS
    )


def _ordered(findings: Sequence[Finding]) -> list[Finding]:
    """Severity descending, then detector, then id — the report's own order (R36).

    Capping a group's detail is only defensible if what survives the cap is
    what a reader would have looked at first, so the cap is applied after the
    document's own ordering rather than to whatever the registry emitted.
    """
    return sorted(
        findings,
        key=lambda finding: (
            -SEVERITY_RANK[finding.severity],
            finding.detector,
            finding.finding_id,
        ),
    )


def _finding_summary(finding: Finding) -> FindingSummary:
    """One finding as counts and authored slugs (R42)."""
    return FindingSummary(
        severity=finding.severity,
        evidence_spans=len(finding.span_seqs),
        agents=len(finding.agent_ids),
        wasted_tokens=finding.wasted.total,
        metrics=_metrics(finding),
    )


def group_summary(group: str, findings: Sequence[Finding]) -> GroupSummary:
    """One group's counts, token total and capped per-finding detail (R42, R43)."""
    if group == OVERALL_GROUP:
        title = OVERALL_TITLE
        members: Sequence[Finding] = list(findings)
    else:
        title = next(d.title for d in ALL_DETECTORS if d.slug == group)
        members = [finding for finding in findings if finding.detector == group]
    ordered = _ordered(members)
    return GroupSummary(
        group=group,
        title=title,
        findings=len(members),
        severity_counts=severity_counts(members),
        wasted_tokens=sum(finding.wasted.total for finding in members),
        detail=tuple(_finding_summary(f) for f in ordered[:MAX_FINDINGS_PER_GROUP]),
    )


def build_totals(
    *,
    findings: Sequence[Finding],
    agents: int,
    spans: int,
    model_calls: int,
    tokens: int,
    cost_usd: str,
    priced_spans: int,
    unpriced_spans: int,
    by_agent: Sequence[AgentTotals] = (),
    by_model: Sequence[ModelTotals] = (),
) -> TraceTotals:
    """R31's figures, as the narrator may see them (R42, S34).

    Every money value arrives as a **string** already produced by
    :func:`~swarm_observer.cost.compute.format_usd`, because R44 forbids this
    package from importing ``cost`` and a second money-formatting rule here
    would be exactly the duplication R29 and the Modularity notes refuse. The
    caller — ``cli/main.py``, the one module allowed to see both sides — does
    the extraction.

    The row caps are applied here rather than trusted to the caller, so a
    caller that passed the whole of a 5,000-agent trace gets a bounded payload
    rather than a provider-side rejection.
    """
    return TraceTotals(
        agents=agents,
        spans=spans,
        model_calls=model_calls,
        findings=len(findings),
        severity_counts=severity_counts(findings),
        tokens=tokens,
        cost_usd=cost_usd,
        priced_spans=priced_spans,
        unpriced_spans=unpriced_spans,
        by_agent=tuple(by_agent[:MAX_TOTAL_ROWS]),
        by_model=tuple(by_model[:MAX_TOTAL_ROWS]),
    )


def build_requests(
    *,
    findings: Sequence[Finding],
    totals: TraceTotals,
    rate_snapshot_version: str,
) -> dict[str, NarrationRequest]:
    """One payload per group, keyed by group, in :func:`narration_groups` order.

    Built once for the whole run rather than per call. Every request carries
    the same figures and differs only in :attr:`NarrationRequest.group`, which
    is what stops two paragraphs of one report disagreeing about the run, and
    what gives a determinism test one object to pin instead of N.
    """
    groups = narration_groups(findings)
    summaries = tuple(group_summary(group, findings) for group in groups)
    return {
        group: NarrationRequest(
            rate_snapshot_version=rate_snapshot_version,
            group=group,
            totals=totals,
            groups=summaries,
        )
        for group in groups
    }


def serialize_request(request: NarrationRequest) -> str:
    """The exact bytes an adapter sends (R42, R47).

    ``sort_keys`` and ``ensure_ascii`` for the reasons ``report.json`` uses
    them: the payload must be a function of the findings and nothing else,
    ``PYTHONHASHSEED`` included. It is a named function rather than something
    an adapter does inline because AC12's sentinel test searches this string,
    and a property asserted about a private expression inside a vendor adapter
    would only be asserted about the adapter that exists.
    """
    return json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


__all__ = [
    "build_requests",
    "build_totals",
    "group_summary",
    "narration_groups",
    "serialize_request",
    "severity_counts",
]
