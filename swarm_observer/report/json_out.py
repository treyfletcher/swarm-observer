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
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from swarm_observer.cost.compute import (
    RATE_CAVEAT,
    WASTE_CAVEAT,
    CostReport,
    format_usd,
)
from swarm_observer.detect.base import SEVERITIES, TRACE_DERIVED_METRIC_KEYS, Finding
from swarm_observer.model.trace import AgentRun, Span, TokenUsage, Trace
from swarm_observer.report.redact import redact

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


class RenderOptions(BaseModel):
    """The flags whose values change what a report contains (R38, R47).

    Recorded in the document because a reader who sees empty previews should be
    able to tell "``--no-previews`` was used" from "this trace had no text", and
    because a golden report is a function of the trace *and the flags*.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: False when ``--no-previews`` blanked trace free text at ingest (A10).
    previews: bool = True
    blocked_gap_seconds: int = Field(default=60, ge=0)
    #: The detector slugs this run was restricted to, in registry order.
    detectors: tuple[str, ...] = ()


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


def free_text(value: str, *, previews: bool) -> str:
    """One trace-derived free-text string, as it may appear in a report.

    Two rules meet here, and both are properties of *this function* rather than
    of whoever called it — the Modularity notes' rule that no renderer may
    assume its caller sanitized:

    * ``--no-previews`` (R38, A10) omits trace free text **entirely**. R8's
      three preview fields are already blanked at ingest, so for those this is a
      no-op; it is not a no-op for ``Span.model``, ``stop_reason``,
      ``tool_name``, ``tool_use_id``, ``SpanError.detail`` and the agent
      strings, none of which R8 covers and every one of which R51 names as a
      field its hostile corpus loads with payloads. Before this guard, a
      ``--no-previews`` run of ``hostile.jsonl`` still carried
      ``"><img src=x onerror=alert(1)>`` four times, as the recorded model id.
      See A-c6: this reads R38's "omits trace free text entirely" as governing,
      and it costs R30 the recorded model id in the unpriced table under that
      flag — a tension the PM should settle.
    * Otherwise, R33's redaction, applied here at the boundary.
    """
    if not previews:
        return ""
    return redact(value)


def _optional(value: str | None, *, previews: bool) -> str | None:
    """:func:`free_text` for a field whose absence is distinct from its emptiness."""
    return None if value is None else free_text(value, previews=previews)


def identifier(value: str) -> str:
    """A trace-derived string this document also uses as a **key** (R33).

    ``agent_id``, ``parent_agent_id`` and ``ParseWarning.detail`` are
    trace-derived — R5 takes an agent id straight from the record's ``agentId``,
    and R4 puts the unknown record *type* into a warning's detail — and they
    reached the report through neither the redactor nor ``--no-previews``
    (review, BUG-2). ``AKIAIOSFODNN7EXAMPLE`` matches R2's agent-id alphabet
    exactly, so a credential-shaped agent id arrived verbatim in five places
    **including under the flag**. That is the third occurrence of this project's
    worst class: increment 1 and increment 2 (the S13 ruling on ``tool_name``)
    each found a trace-derived field bypassing the boundary because its type
    looked like an identifier rather than like text.

    So: **redacted, in both modes, like every other trace-derived string.**

    Not *blanked* under ``--no-previews``, which is the one way this differs
    from :func:`free_text`, and for the same reason A-c7 keeps ``metrics``: an
    agent id is the join key between ``spans[]``, ``agents[]`` and
    ``cost.by_agent[]``, and a warning's detail is half of the ``(code, detail)``
    pair R10 aggregates on. Blanking them collapses distinct rows into one and
    makes the document unreadable rather than redacted. R2 constrains an agent
    id's alphabet and R2 calls a warning detail "an enumerated slug or number
    only", so neither is "trace free text" in R38's sense — but both are
    trace-derived, which is what R33 keys on. The residual tension is real and
    is the PM's: see the review's ruling on S16.
    """
    return redact(value)


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
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "agent_id": identifier(span.agent_id),
        "kind": span.kind,
        "start": optional_timestamp(span.start),
        "end": optional_timestamp(span.end),
        "model": _optional(span.model, previews=previews),
        "usage": None if span.usage is None else usage_document(span.usage),
        "stop_reason": _optional(span.stop_reason, previews=previews),
        "tool_name": _optional(span.tool_name, previews=previews),
        "tool_use_id": _optional(span.tool_use_id, previews=previews),
        "tool_input_digest": span.tool_input_digest,
        "tool_result_status": span.tool_result_status,
        "text_preview": free_text(span.text_preview, previews=previews),
        "tool_input_preview": free_text(span.tool_input_preview, previews=previews),
        "tool_result_preview": free_text(span.tool_result_preview, previews=previews),
        "error": (
            None
            if span.error is None
            else {
                "code": span.error.code,
                "detail": free_text(span.error.detail, previews=previews),
            }
        ),
        "extras_dropped": span.extras_dropped,
    }


def agent_document(agent: AgentRun, *, previews: bool = True) -> dict[str, Any]:
    """One agent run (R2, R33, R38)."""
    return {
        "agent_id": identifier(agent.agent_id),
        "agent_index": agent.agent_index,
        "agent_type": _optional(agent.agent_type, previews=previews),
        "description": free_text(agent.description, previews=previews),
        "parent_agent_id": (
            None if agent.parent_agent_id is None else identifier(agent.parent_agent_id)
        ),
        "depth": agent.depth,
        "span_seqs": list(agent.span_seqs),
        "start": optional_timestamp(agent.start),
        "end": optional_timestamp(agent.end),
    }


def metrics_document(metrics: dict[str, int | str]) -> dict[str, int | str]:
    """R16 + S13: redact the one trace-derived ``metrics`` value.

    R16 constrains ``tool_name`` by *shape*, and a shape check is not a secret
    check: ``AKIAIOSFODNN7EXAMPLE`` and ``sk-ant-api03-…`` are both legal tool
    names under R16's pattern. R51 promises credential-shaped payloads appear
    nowhere in a rendered report, so the redactor has to run over these values
    and not only over ``previews``. :data:`TRACE_DERIVED_METRIC_KEYS` is the
    machine-readable list of which keys those are, rather than a sentence in a
    docstring that the next renderer's author has to remember.
    """
    rendered: dict[str, int | str] = {}
    for key, value in metrics.items():
        if key in TRACE_DERIVED_METRIC_KEYS and isinstance(value, str):
            rendered[key] = redact(value)
        else:
            rendered[key] = value
    return rendered


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
        "detector": finding.detector,
        "finding_id": finding.finding_id,
        "severity": finding.severity,
        "summary": finding.summary,
        "span_seqs": list(finding.span_seqs),
        "agent_ids": [identifier(agent_id) for agent_id in finding.agent_ids],
        "metrics": metrics_document(finding.metrics),
        "previews": [free_text(text, previews=previews) for text in finding.previews],
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
        "currency": cost.meta.currency,
        "snapshot": {
            "version": cost.meta.version,
            "snapshot_date": cost.meta.snapshot_date,
            "currency": cost.meta.currency,
            "sources": [
                {
                    "id": source.id,
                    "label": source.label,
                    "url": source.url,
                    "as_of": source.as_of,
                    "models": list(source.models),
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
                "agent_id": identifier(row.agent_id),
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
                "model_key": row.model_key,
                "priced_spans": row.priced_spans,
                "usage": usage_document(row.usage),
                "cost_usd": format_usd(row.cost_usd),
            }
            for row in cost.by_model
        ],
        "by_detector": [
            {
                "detector": row.detector,
                "findings": row.findings,
                "findings_with_unknown_cost": row.findings_unpriced,
                "wasted": usage_document(row.wasted),
                "wasted_cost_usd": format_usd(row.wasted_cost_usd),
            }
            for row in cost.by_detector
        ],
        "spans": [
            {
                "seq": row.seq,
                "agent_id": identifier(row.agent_id),
                "model": free_text(row.model, previews=previews),
                "model_key": row.model_key,
                "usage": usage_document(row.usage),
                "cost_usd": format_usd(row.cost_usd),
            }
            for row in cost.spans
        ],
        "unpriced": [
            {
                "seq": row.seq,
                "agent_id": identifier(row.agent_id),
                "model": free_text(row.model, previews=previews),
                "reason": row.reason,
                "missing_price_keys": list(row.missing_price_keys),
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


def report_document(
    *,
    trace: Trace,
    findings: Sequence[Finding],
    cost: CostReport,
    tool_version: str,
    options: RenderOptions,
) -> dict[str, Any]:
    """The whole report as plain JSON-serializable data (R36).

    Findings are emitted in the order they were produced, which
    :func:`~swarm_observer.detect.registry.run_detectors` has already made the
    canonical R13 order ``(severity_rank, slug, finding_id)``. R36's
    severity-descending *grouping* is a presentation rule for the HTML section;
    duplicating it here would put a second ordering rule in the package for the
    same objects (see A-c8).
    """
    return {
        "meta": {
            "report_format_version": REPORT_FORMAT_VERSION,
            "schema_version": trace.schema_version,
            "tool": {"name": "swarm-observer", "version": tool_version},
            "adapter": trace.adapter,
            "trace_id": trace.trace_id,
            "trace_last_timestamp": last_timestamp(trace),
            "source_files": [
                {
                    "name": source.name,
                    "sha256": source.sha256,
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
                "detectors": list(options.detectors),
            },
            "notes": {"redaction": REDACTION_CAVEAT},
        },
        "agents": [agent_document(agent, previews=options.previews) for agent in trace.agents],
        "spans": [span_document(span, previews=options.previews) for span in trace.spans],
        "findings": [finding_document(finding, previews=options.previews) for finding in findings],
        "cost": cost_document(cost, previews=options.previews),
        "warnings": [
            {
                "code": warning.code,
                "count": warning.count,
                "detail": identifier(warning.detail),
            }
            for warning in trace.warnings
        ],
    }


def render_json(
    *,
    trace: Trace,
    findings: Sequence[Finding],
    cost: CostReport,
    tool_version: str,
    options: RenderOptions,
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
    )
    return json.dumps(document, sort_keys=True, ensure_ascii=True, indent=2) + "\n"


__all__ = [
    "REDACTION_CAVEAT",
    "REPORT_FORMAT_VERSION",
    "RenderOptions",
    "agent_document",
    "cost_document",
    "finding_document",
    "format_timestamp",
    "free_text",
    "identifier",
    "last_timestamp",
    "metrics_document",
    "optional_timestamp",
    "render_json",
    "report_document",
    "severity_counts",
    "span_document",
    "usage_document",
]
