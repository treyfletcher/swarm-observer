"""``report.json`` — the machine-readable report (R36's JSON half, R47).

The document is the same *data* the HTML report renders: provenance header,
agents, spans, findings, the four cost groupings with the unpriced list, and
the full parse-warning list. It is emitted with ``sort_keys=True``,
``ensure_ascii=True``, ``indent=2`` and a trailing newline, and every
:class:`~decimal.Decimal` becomes its six-decimal string — so the bytes are a
function of the trace, the flags and nothing else (R47).

Three rules this module exists to enforce:

* **Every trace-derived string is redacted here, at the boundary** (R33, R30).
  Not by the caller, and not "already, upstream": a renderer that trusts its
  caller is the increment-1 defect wearing a new hat. That covers previews,
  recorded model ids, tool names, stop reasons, span error details, agent
  descriptions, and the one trace-derived ``metrics`` value R16 admits
  (``tool_name``) — which the increment-2 review's S13 ruling specifically
  calls out, because a credential-shaped string can be a *legal* tool name and
  would otherwise reach the findings table verbatim.
* **No float, no clock, no path.** Timestamps are formatted by hand as
  ``YYYY-MM-DDTHH:MM:SS.mmmZ`` in UTC — never ``strftime`` with a
  locale-sensitive directive, never ``isoformat`` (whose output varies with the
  microsecond value), never ``timestamp()`` (a float). The provenance block
  names the **trace's own** last timestamp, never the current time.
* **The renderer never computes.** It receives a finished ``Trace``, finished
  findings and a finished :class:`~swarm_observer.cost.compute.CostReport`, and
  it does no detection, no pricing and no aggregation. That is what keeps a
  golden file meaningful.

Escaping is not this module's business: JSON has its own quoting and R32's
``escape_html`` belongs to the HTML renderer alone (R44 asserts there is only
one such definition).

Increment 4 moved the redaction *policy* — which strings are free text, which
are identifiers, and what happens to each under ``--no-previews`` — into
``report/sanitize.py``, because the HTML renderer makes the same decisions over
the same fields and a policy written twice is a policy that will differ. Every
name this module used to define is re-exported, so no caller changed. What is
still this module's own is the *document shape*: which fields exist, in which
objects, and the R47 formatting rules above.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from swarm_observer.cost.compute import (
    RATE_CAVEAT,
    WASTE_CAVEAT,
    CostReport,
    format_usd,
)
from swarm_observer.detect.base import SEVERITIES, Finding
from swarm_observer.model.trace import AgentRun, Span, TokenUsage, Trace
from swarm_observer.report.narrative import Narrative
from swarm_observer.report.sanitize import (
    KIND_AUTHORED,
    KIND_FREE,
    KIND_IDENTIFIER,
    KIND_NARRATOR,
    RenderOptions,
    metric_value,
    optional_text,
    text,
)

#: The one sentence both reports carry about what redaction is and is not (R33).
REDACTION_CAVEAT = (
    "Trace-derived text is passed through a credential-shaped-substring redactor. "
    "Redaction is a courtesy that reduces accidental exposure in a shared report; it "
    "cannot defeat an adversary who controls the trace. Use --no-previews to omit "
    "trace free text entirely."
)

#: The document format's own version, independent of TRACE_SCHEMA_VERSION (R1):
#: the trace model and the report layout can move separately.
REPORT_FORMAT_VERSION = "1.0.0"


def format_timestamp(value: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS.mmmZ`` in UTC, built digit by digit (R47).

    Hand-formatted on purpose. ``isoformat`` drops the fractional part when the
    microsecond is zero and prints six digits otherwise, so two traces would
    render timestamps in two different shapes; ``strftime`` consults the C
    locale for some directives. Neither is acceptable in bytes the spec
    promises are identical across ``LC_ALL`` and ``TZ``. Milliseconds are
    **truncated**, matching the whole-millisecond rule the detectors use.
    """
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
        f"T{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
        f".{value.microsecond // 1000:03d}Z"
    )


def optional_timestamp(value: datetime | None) -> str | None:
    """:func:`format_timestamp`, or ``None`` for a span with no such endpoint."""
    return None if value is None else format_timestamp(value)


def last_timestamp(trace: Trace) -> str | None:
    """The trace's own latest timestamp — the provenance clock (R47).

    R47 forbids the current time anywhere in output and names this value as what
    the provenance line carries instead. ``None`` when no span carries timing at
    all, which a trace of nothing but tool calls with missing results can be.
    """
    latest: datetime | None = None
    for span in trace.spans:
        for candidate in (span.start, span.end):
            if candidate is not None and (latest is None or candidate > latest):
                latest = candidate
    return optional_timestamp(latest)


def usage_document(usage: TokenUsage) -> dict[str, int]:
    """R2's five token components as plain integers."""
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "cache_creation_5m_tokens": usage.cache_creation_5m_tokens,
        "cache_creation_1h_tokens": usage.cache_creation_1h_tokens,
        "total_tokens": usage.total,
    }


def span_document(span: Span, *, previews: bool = True) -> dict[str, Any]:
    """One span, every trace-derived string through :func:`free_text` (R2, R33, R38).

    ``span_id``, ``parent_span_id``, ``agent_id`` and ``tool_input_digest`` are
    *not* trace-derived: R5 builds the ids from digests and R2 constrains the
    agent-id alphabet, precisely so they are safe to place in an HTML attribute
    (R34). ``kind``, ``tool_result_status`` and ``SpanError.code`` are
    enumerated values this package authored. Everything else on a span came out
    of the trace.
    """
    return {
        "seq": span.seq,
        "span_id": text(span.span_id, kind=KIND_AUTHORED, previews=previews),
        "parent_span_id": optional_text(span.parent_span_id, kind=KIND_AUTHORED, previews=previews),
        "agent_id": text(span.agent_id, kind=KIND_IDENTIFIER, previews=previews),
        "kind": text(span.kind, kind=KIND_AUTHORED, previews=previews),
        "start": optional_timestamp(span.start),
        "end": optional_timestamp(span.end),
        "model": optional_text(span.model, kind=KIND_FREE, previews=previews),
        "usage": None if span.usage is None else usage_document(span.usage),
        "stop_reason": optional_text(span.stop_reason, kind=KIND_FREE, previews=previews),
        "tool_name": optional_text(span.tool_name, kind=KIND_FREE, previews=previews),
        "tool_use_id": optional_text(span.tool_use_id, kind=KIND_FREE, previews=previews),
        "tool_input_digest": optional_text(
            span.tool_input_digest, kind=KIND_AUTHORED, previews=previews
        ),
        "tool_result_status": optional_text(
            span.tool_result_status, kind=KIND_AUTHORED, previews=previews
        ),
        "text_preview": text(span.text_preview, kind=KIND_FREE, previews=previews),
        "tool_input_preview": text(span.tool_input_preview, kind=KIND_FREE, previews=previews),
        "tool_result_preview": text(span.tool_result_preview, kind=KIND_FREE, previews=previews),
        "error": (
            None
            if span.error is None
            else {
                # `code` is trace-derived: R12 builds it from the record's own
                # `error` field with `ingest.text.slug`, exactly as R4 builds a
                # `ParseWarning.detail` from the record's unknown `type`. Its
                # alphabet (`^[A-Za-z0-9_.:\-]{1,40}$`) stops *markup*; it admits
                # a lowercase credential — `ghp_…`, `sk-…`, `sk-ant-…` all
                # survive `slug` unchanged — so it belongs in the `identifier`
                # class: redacted, never blanked, like the warning detail it is
                # built the same way as. It reached both reports raw until
                # review found it (§1.1 of docs/reviews/feature-so-i4.md).
                "code": text(span.error.code, kind=KIND_IDENTIFIER, previews=previews),
                "detail": text(span.error.detail, kind=KIND_FREE, previews=previews),
            }
        ),
        "extras_dropped": span.extras_dropped,
    }


def agent_document(agent: AgentRun, *, previews: bool = True) -> dict[str, Any]:
    """One agent run (R2, R33, R38)."""
    return {
        "agent_id": text(agent.agent_id, kind=KIND_IDENTIFIER, previews=previews),
        "agent_index": agent.agent_index,
        "agent_type": optional_text(agent.agent_type, kind=KIND_FREE, previews=previews),
        "description": text(agent.description, kind=KIND_FREE, previews=previews),
        "parent_agent_id": (
            optional_text(agent.parent_agent_id, kind=KIND_IDENTIFIER, previews=previews)
        ),
        "depth": agent.depth,
        "span_seqs": list(agent.span_seqs),
        "start": optional_timestamp(agent.start),
        "end": optional_timestamp(agent.end),
    }


def metrics_document(metrics: dict[str, int | str]) -> dict[str, int | str]:
    """R16 + S13: redact the one trace-derived ``metrics`` value.

    The decision itself lives in
    :func:`~swarm_observer.report.sanitize.metric_value`, because increment 4's
    HTML renderer makes the same one over the same values and a policy applied
    in two renderers is a policy that will be applied in one of them.
    """
    return {key: metric_value(key, value) for key, value in metrics.items()}


def finding_document(finding: Finding, *, previews: bool = True) -> dict[str, Any]:
    """One finding (R14, R33, R38).

    ``summary`` is *not* redacted, and that is not an oversight: R16 guarantees
    it is built from integers and this package's own enumerated slugs, so there
    is no trace-derived byte in it to redact. Running the redactor over it
    anyway would be harmless but would blur where the guarantee lives.

    ``metrics`` is redacted but **not** blanked under ``--no-previews``, unlike
    every other trace-derived value. R15 hashes ``metrics`` into the
    ``finding_id`` the same document prints, so a masked metric would make the
    printed id unverifiable from the printed evidence. It is safe to keep
    because R16 already constrains the one trace-derived key to
    ``^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$`` — which admits no ``<``, ``>``, ``"``,
    ``/``, space or control character, so none of R51's markup payloads can be
    a legal tool name — and redaction covers the credential shapes that *are*
    legal (the increment-2 review's S13). See A-c7.
    """
    return {
        "detector": text(finding.detector, kind=KIND_AUTHORED, previews=previews),
        "finding_id": text(finding.finding_id, kind=KIND_AUTHORED, previews=previews),
        "severity": text(finding.severity, kind=KIND_AUTHORED, previews=previews),
        "summary": text(finding.summary, kind=KIND_AUTHORED, previews=previews),
        "span_seqs": list(finding.span_seqs),
        "agent_ids": [
            text(agent_id, kind=KIND_IDENTIFIER, previews=previews)
            for agent_id in finding.agent_ids
        ],
        "metrics": metrics_document(finding.metrics),
        "previews": [
            text(preview, kind=KIND_FREE, previews=previews) for preview in finding.previews
        ],
        "wasted": usage_document(finding.wasted),
        "wasted_cost_usd": (
            None if finding.wasted_cost_usd is None else format_usd(finding.wasted_cost_usd)
        ),
    }


def cost_document(cost: CostReport, *, previews: bool = True) -> dict[str, Any]:
    """The four R31 groupings, the unpriced list and the snapshot meta (R30, R31).

    The recorded ``model`` on a priced or unpriced row is trace-derived — R30
    says so itself ("redacted and escaped like any trace string") — so it goes
    through :func:`free_text` like any other. ``model_key`` does not: it is a
    key out of this repository's own snapshot.
    """
    return {
        "currency": text(cost.meta.currency, kind=KIND_AUTHORED, previews=previews),
        "snapshot": {
            "version": text(cost.meta.version, kind=KIND_AUTHORED, previews=previews),
            "snapshot_date": text(cost.meta.snapshot_date, kind=KIND_AUTHORED, previews=previews),
            "currency": text(cost.meta.currency, kind=KIND_AUTHORED, previews=previews),
            "sources": [
                {
                    "id": text(source.id, kind=KIND_AUTHORED, previews=previews),
                    "label": text(source.label, kind=KIND_AUTHORED, previews=previews),
                    "url": text(source.url, kind=KIND_AUTHORED, previews=previews),
                    "as_of": text(source.as_of, kind=KIND_AUTHORED, previews=previews),
                    "models": [
                        text(name, kind=KIND_AUTHORED, previews=previews) for name in source.models
                    ],
                }
                for source in cost.meta.sources
            ],
        },
        "total": {
            "cost_usd": format_usd(cost.total_cost_usd),
            "priced_spans": cost.priced_spans,
            "unpriced_spans": cost.unpriced_spans,
            "usage": usage_document(cost.total_usage),
            "unpriced_usage": usage_document(cost.unpriced_usage),
        },
        "by_agent": [
            {
                "agent_id": text(row.agent_id, kind=KIND_IDENTIFIER, previews=previews),
                "agent_index": row.agent_index,
                "priced_spans": row.priced_spans,
                "unpriced_spans": row.unpriced_spans,
                "usage": usage_document(row.usage),
                "cost_usd": format_usd(row.cost_usd),
            }
            for row in cost.by_agent
        ],
        "by_model": [
            {
                "model_key": text(row.model_key, kind=KIND_AUTHORED, previews=previews),
                "priced_spans": row.priced_spans,
                "usage": usage_document(row.usage),
                "cost_usd": format_usd(row.cost_usd),
            }
            for row in cost.by_model
        ],
        "by_detector": [
            {
                "detector": text(row.detector, kind=KIND_AUTHORED, previews=previews),
                "findings": row.findings,
                "findings_with_unknown_cost": row.findings_unpriced,
                "wasted": usage_document(row.wasted),
                # R17's total and the part of it with no rate, so a reader can
                # see why the dollar figure beside them is smaller (review,
                # BUG-4). `AgentCost` reports the same pair.
                "wasted_unpriced": usage_document(row.wasted_unpriced),
                "wasted_cost_usd": format_usd(row.wasted_cost_usd),
            }
            for row in cost.by_detector
        ],
        "spans": [
            {
                "seq": row.seq,
                "agent_id": text(row.agent_id, kind=KIND_IDENTIFIER, previews=previews),
                "model": text(row.model, kind=KIND_FREE, previews=previews),
                "model_key": text(row.model_key, kind=KIND_AUTHORED, previews=previews),
                "usage": usage_document(row.usage),
                "cost_usd": format_usd(row.cost_usd),
            }
            for row in cost.spans
        ],
        "unpriced": [
            {
                "seq": row.seq,
                "agent_id": text(row.agent_id, kind=KIND_IDENTIFIER, previews=previews),
                "model": text(row.model, kind=KIND_FREE, previews=previews),
                "reason": text(row.reason, kind=KIND_AUTHORED, previews=previews),
                "missing_price_keys": [
                    text(key, kind=KIND_AUTHORED, previews=previews)
                    for key in row.missing_price_keys
                ],
            }
            for row in cost.unpriced
        ],
        "notes": {"rates": RATE_CAVEAT, "waste": WASTE_CAVEAT},
    }


def severity_counts(findings: Sequence[Finding]) -> dict[str, int]:
    """Findings per severity, with every severity present (R40).

    Every key exists even at zero: the stdout line and the header both read this
    and a missing key would make "no critical findings" indistinguishable from
    "the counter was never written".
    """
    counts = dict.fromkeys(SEVERITIES, 0)
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def narrative_document(narrative: Narrative, *, previews: bool) -> dict[str, Any]:
    """The ``--explain`` section, as data (R43).

    The HTML report marks a fallback with a class and a visible prefix; a
    machine consumer gets the same two facts as ``fallback`` and ``reason``,
    plus ``calls`` so "the narrator was never asked" is distinguishable from
    "the narrator answered badly" — which matters because R43 makes both look
    the same in the rendered prose.

    The paragraph text goes through the ``narrator`` kind, exactly as in the
    HTML renderer: a model-produced string is untrusted wherever it lands, and
    ``report.json`` is the document with no escaping to hide behind.
    """
    return {
        "calls": narrative.calls,
        "fallbacks": narrative.fallbacks,
        "paragraphs": [
            {
                "group": text(paragraph.group, kind=KIND_AUTHORED, previews=previews),
                "title": text(paragraph.title, kind=KIND_AUTHORED, previews=previews),
                "text": text(paragraph.text, kind=KIND_NARRATOR, previews=previews),
                "fallback": paragraph.fallback,
                "reason": optional_text(paragraph.reason, kind=KIND_AUTHORED, previews=previews),
            }
            for paragraph in narrative.paragraphs
        ],
    }


def report_document(
    *,
    trace: Trace,
    findings: Sequence[Finding],
    cost: CostReport,
    tool_version: str,
    options: RenderOptions,
    narrative: Narrative | None = None,
) -> dict[str, Any]:
    """The whole report as plain JSON-serializable data (R36).

    Findings are emitted in the order they were produced, which
    :func:`~swarm_observer.detect.registry.run_detectors` has already made the
    canonical R13 order ``(severity_rank, slug, finding_id)``. R36's
    severity-descending *grouping* is a presentation rule for the HTML section;
    duplicating it here would put a second ordering rule in the package for the
    same objects (see A-c8).
    """
    document: dict[str, Any] = {
        "meta": {
            "report_format_version": REPORT_FORMAT_VERSION,
            "schema_version": text(
                trace.schema_version, kind=KIND_AUTHORED, previews=options.previews
            ),
            "tool": {"name": "swarm-observer", "version": tool_version},
            "adapter": text(trace.adapter, kind=KIND_AUTHORED, previews=options.previews),
            "trace_id": text(trace.trace_id, kind=KIND_AUTHORED, previews=options.previews),
            "trace_last_timestamp": last_timestamp(trace),
            "source_files": [
                {
                    # Not trace-derived — a filename comes from the filesystem —
                    # but attacker-influenceable all the same: `analyze <dir>`
                    # reads whatever basenames the directory holds, and R47
                    # already restricts this to the basename. It goes through the
                    # same boundary as every other untrusted string rather than
                    # relying on JSON quoting, because increment 4 renders this
                    # field into HTML and "a filename is not trace-derived" is
                    # exactly the reading that would put it there raw. See the
                    # review's ruling on the tester's S21.
                    "name": text(source.name, kind=KIND_IDENTIFIER, previews=options.previews),
                    "sha256": text(source.sha256, kind=KIND_AUTHORED, previews=options.previews),
                    "bytes": source.bytes,
                    "records": source.records,
                }
                for source in trace.source_files
            ],
            "counts": {
                "agents": len(trace.agents),
                "spans": len(trace.spans),
                "findings": len(findings),
                "findings_by_severity": severity_counts(findings),
                "warnings": len(trace.warnings),
            },
            "options": {
                "previews": options.previews,
                "blocked_gap_seconds": options.blocked_gap_seconds,
                "detectors": [
                    text(slug, kind=KIND_AUTHORED, previews=options.previews)
                    for slug in options.detectors
                ],
            },
            "notes": {"redaction": REDACTION_CAVEAT},
        },
        "agents": [agent_document(agent, previews=options.previews) for agent in trace.agents],
        "spans": [span_document(span, previews=options.previews) for span in trace.spans],
        "findings": [finding_document(finding, previews=options.previews) for finding in findings],
        "cost": cost_document(cost, previews=options.previews),
        "warnings": [
            {
                "code": text(warning.code, kind=KIND_AUTHORED, previews=options.previews),
                "count": warning.count,
                "detail": text(warning.detail, kind=KIND_IDENTIFIER, previews=options.previews),
            }
            for warning in trace.warnings
        ],
    }
    if narrative is not None:
        # Added only under ``--explain``. A key that always existed and was
        # sometimes null would change every no-``--explain`` document, which
        # R43 forbids for the HTML report and which there is no reason to do
        # to the JSON one either.
        document["narrative"] = narrative_document(narrative, previews=options.previews)
    return document


def render_json(
    *,
    trace: Trace,
    findings: Sequence[Finding],
    cost: CostReport,
    tool_version: str,
    options: RenderOptions,
    narrative: Narrative | None = None,
) -> str:
    """The exact bytes of ``report.json`` (R36, R47).

    ``sort_keys`` removes every dict-ordering (and therefore ``PYTHONHASHSEED``)
    dependency, ``ensure_ascii`` every encoding dependency, and the trailing
    newline makes the file a well-formed text file rather than one a diff
    complains about.
    """
    document = report_document(
        trace=trace,
        findings=findings,
        cost=cost,
        tool_version=tool_version,
        options=options,
        narrative=narrative,
    )
    return json.dumps(document, sort_keys=True, ensure_ascii=True, indent=2) + "\n"


#: Re-exported from :mod:`swarm_observer.report.sanitize`, which now owns the
#: redaction *policy* both renderers apply (increment 4). They are kept here so
#: every existing caller and test that imports them from this module still does.
__all__ = [
    "REDACTION_CAVEAT",
    "REPORT_FORMAT_VERSION",
    "RenderOptions",
    "agent_document",
    "cost_document",
    "finding_document",
    "format_timestamp",
    "last_timestamp",
    "metric_value",
    "metrics_document",
    "narrative_document",
    "optional_text",
    "optional_timestamp",
    "render_json",
    "report_document",
    "severity_counts",
    "span_document",
    "text",
    "usage_document",
]
