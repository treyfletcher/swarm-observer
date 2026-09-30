"""AC12's sentinel corpus, chosen so that only a *filter* could remove it.

Not a test module.

AC12's second half is "given a fixture trace whose every free-text field is a
distinctive sentinel, no sentinel appears in the serialized request payload".
``tests/hostile_corpus.py`` already has a trace whose every free-text field is
distinctive — but its markers are ``MARKERtoolname``-shaped, and *every*
string-valued field of a narration request refuses an uppercase letter. Run
against those markers, the sentinel sweep asks whether a payload full of
lowercase slugs contains an uppercase string, which it cannot.

So this module builds a second sentinel trace, and the only thing that is
different about it is the alphabet:

    Every sentinel here is ``^[a-z][a-z0-9_]{0,63}$``. That is exactly the
    alphabet ``narrate.client``'s own validators **accept** — it satisfies
    ``AUTHORED_METRIC_VALUE_PATTERN``, ``METRIC_KEY_PATTERN``,
    ``MODEL_KEY_PATTERN`` and ``SNAPSHOT_VERSION_PATTERN``. A sentinel from
    this corpus that fails to reach the payload was therefore **dropped by
    the builder**, not refused by the type, and that distinction is the whole
    subject of the coder's "structural, not filtered" claim.

``ghp_aaaa…`` is in the set for the same reason and is not decoration: a
GitHub token is entirely lowercase letters, digits and an underscore, so it is
a *credential* that the payload's authored-slug validator admits. If
``metrics.tool_name`` ever stops being dropped, this corpus is what says so.

The trace also carries an API error, an unknown record type and a sidecar, so
``SpanError``, ``ParseWarning.detail``, ``AgentRun.agent_type`` and
``AgentRun.description`` all hold a sentinel too — the four fields
``tests/hostile_corpus.py`` exists because the checked-in fixture leaves empty
(BUG-8).
"""

from __future__ import annotations

import json
from pathlib import Path

from swarm_observer.cost.compute import format_usd
from swarm_observer.detect.base import Finding
from swarm_observer.narrate.client import TraceTotals
from swarm_observer.narrate.summary import build_totals

from .factories import (
    api_error,
    assistant,
    text_block,
    tool_result_block,
    tool_use_block,
    usage,
    user,
    write_jsonl,
)
from .pipeline import Analysis, analyze_paths

#: Every free-text field R51 names, with a sentinel the narration payload's own
#: patterns would accept. Keyed by the ``Trace`` attribute path, so a failure
#: message names the field a reader has to go and look at.
SENTINELS: dict[str, str] = {
    "Span.text_preview": "sentinelassistanttext",
    "Span.tool_input_preview": "sentineltoolinput",
    "Span.tool_result_preview": "sentineltoolresult",
    "Span.model": "sentinelmodelid",
    "Span.stop_reason": "sentinelstopreason",
    "Span.tool_name": "sentineltoolname",
    "Span.tool_use_id": "sentineltooluseid",
    "Span.agent_id": "sentinelagentid",
    "SpanError.code": "sentinelerrorcode",
    "SpanError.detail": "sentinelerrordetail",
    "AgentRun.agent_type": "sentinelagenttype",
    "AgentRun.description": "sentinelagentdescription",
    "ParseWarning.detail": "sentinelrecordtype",
    # A credential that the authored-slug alphabet admits. See the module
    # docstring: this is the one sentinel whose absence from the payload is a
    # security property rather than a hygiene property.
    "metrics.tool_name.credential": "ghp_" + "a" * 24,
}

#: The subset that must be **present** in the objects the payload is built
#: from, for the sweep's non-vacuity arm. Filled by
#: :func:`sentinels_present_in_inputs` rather than typed out, so it cannot
#: drift from what the mapper actually produces.
ROOT_AGENT = SENTINELS["Span.agent_id"]


def _sidecar() -> dict[str, object]:
    """``agent-<id>.meta.json`` — the only route to ``agent_type``/``description``."""
    return {
        "agentType": SENTINELS["AgentRun.agent_type"],
        "description": SENTINELS["AgentRun.description"],
        "spawnDepth": 0,
    }


def _records() -> list[dict[str, object]]:
    """A trace that fires several detectors and loads every sentinel field.

    Three identical tool calls with erroring results give
    ``repeated_tool_call``, ``failed_tool_call``, ``retry_storm`` and
    ``agent_loop``; the gaps between them give ``blocked_agent``; the API error
    gives ``SpanError``. More than one group is the point — a narration over a
    trace with a single group cannot tell a per-group fallback from a whole-run
    one, which is how the only ``--explain`` test in the inherited suite came
    to assert a per-group property over one element.
    """
    records: list[dict[str, object]] = []
    for index in range(3):
        stamp = f"2026-04-0{index + 1}T10:00:00.000Z"
        records.append(
            assistant(
                f"s-{index}",
                timestamp=stamp,
                agent_id=ROOT_AGENT,
                message_id=f"msg-{index}",
                request_id=f"req-{index}",
                model=SENTINELS["Span.model"],
                stop_reason=SENTINELS["Span.stop_reason"],
                usage_block=usage(input_tokens=1_000, output_tokens=200, cache_read=50),
                content=[
                    text_block(SENTINELS["Span.text_preview"]),
                    tool_use_block(
                        f"{SENTINELS['Span.tool_use_id']}{index}",
                        SENTINELS["Span.tool_name"],
                        {"query": SENTINELS["Span.tool_input_preview"]},
                    ),
                ],
            )
        )
        records.append(
            user(
                f"r-{index}",
                timestamp=stamp,
                agent_id=ROOT_AGENT,
                content=[
                    tool_result_block(
                        f"{SENTINELS['Span.tool_use_id']}{index}",
                        SENTINELS["Span.tool_result_preview"],
                        is_error=True,
                    )
                ],
            )
        )
    # A second tool, named with a credential the authored-slug alphabet admits,
    # called twice so `repeated_tool_call` carries it in `metrics.tool_name`.
    for index in range(2):
        stamp = f"2026-04-05T11:0{index}:00.000Z"
        records.append(
            assistant(
                f"c-{index}",
                timestamp=stamp,
                agent_id=ROOT_AGENT,
                message_id=f"cred-{index}",
                request_id=f"credreq-{index}",
                model=SENTINELS["Span.model"],
                usage_block=usage(input_tokens=10, output_tokens=1),
                content=[
                    tool_use_block(
                        f"cred-tu-{index}",
                        SENTINELS["metrics.tool_name.credential"],
                        {"same": "arguments"},
                    )
                ],
            )
        )
        records.append(
            user(
                f"cr-{index}",
                timestamp=stamp,
                agent_id=ROOT_AGENT,
                # Not ``"ok"``: a two-character preview is a substring of
                # ``wasted_tokens`` and would make a "no preview reaches the
                # payload" sweep fail for a reason that is about the needle.
                content=[
                    tool_result_block(
                        f"cred-tu-{index}", SENTINELS["Span.tool_result_preview"], is_error=True
                    )
                ],
            )
        )
    records.append(
        api_error(
            "e-0",
            timestamp="2026-04-06T12:00:00.000Z",
            # ``api_error`` forwards unknown keywords straight onto the record,
            # so the agent is named in the record's own spelling.
            agentId=ROOT_AGENT,
            error=SENTINELS["SpanError.code"],
            status=SENTINELS["SpanError.detail"],
        )
    )
    # An unknown record type becomes `ParseWarning.detail` (R4, R10).
    records.append(
        {
            "type": SENTINELS["ParseWarning.detail"],
            "uuid": "unknown-0",
            "timestamp": "2026-04-06T12:00:01.000Z",
            "sessionId": "sess-1",
            "agentId": ROOT_AGENT,
        }
    )
    return records


def write_sentinel_trace(directory: Path) -> tuple[Path, ...]:
    """Write the sentinel trace and its sidecar into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = write_jsonl(directory / "agent-sentinel.jsonl", _records())
    (directory / "agent-sentinel.meta.json").write_text(
        json.dumps(_sidecar(), sort_keys=True) + "\n", encoding="utf-8"
    )
    return (path,)


def sentinel_analysis(directory: Path, *, previews: bool = True) -> Analysis:
    """One analyze run over the sentinel trace."""
    return analyze_paths(write_sentinel_trace(directory), previews=previews)


def input_haystack(analysis: Analysis) -> str:
    """Every byte of the objects a payload could have been built from.

    The ``Trace`` and the findings, serialized. A sentinel found here and not
    in the payload is a sentinel the builder dropped; a sentinel found in
    neither proves nothing, which is why the sweep asserts against this string
    first.
    """
    return json.dumps(
        {
            "trace": analysis.trace.model_dump(mode="json"),
            "findings": [finding.model_dump(mode="json") for finding in analysis.findings],
        },
        default=str,
        sort_keys=True,
    )


def sentinels_present_in_inputs(analysis: Analysis) -> set[str]:
    """Which sentinel labels really reached the objects the payload is built from."""
    haystack = input_haystack(analysis)
    return {label for label, value in SENTINELS.items() if value in haystack}


def totals_for(analysis: Analysis) -> TraceTotals:
    """``build_totals`` with the figures ``cli.main.build_narrative`` extracts (S34).

    Duplicated here rather than imported, for the reason ``tests/pipeline.py``
    inlines ``selected_slugs``: ``build_narrative`` also *runs* the narrator,
    and a payload test wants the payload without the pass. The equality with
    the CLI's own is asserted in ``test_explain_end_to_end_ac12.py``.
    """
    from swarm_observer.narrate.client import AgentTotals, ModelTotals

    cost = analysis.cost
    return build_totals(
        findings=analysis.findings,
        agents=len(analysis.trace.agents),
        spans=len(analysis.trace.spans),
        model_calls=sum(1 for span in analysis.trace.spans if span.kind == "model_call"),
        tokens=cost.total_usage.total,
        cost_usd=format_usd(cost.total_cost_usd),
        priced_spans=cost.priced_spans,
        unpriced_spans=cost.unpriced_spans,
        by_agent=[
            AgentTotals(
                agent_index=row.agent_index,
                priced_spans=row.priced_spans,
                unpriced_spans=row.unpriced_spans,
                tokens=row.usage.total,
                cost_usd=format_usd(row.cost_usd),
            )
            for row in cost.by_agent
        ],
        by_model=[
            ModelTotals(
                model_key=row.model_key,
                priced_spans=row.priced_spans,
                tokens=row.usage.total,
                cost_usd=format_usd(row.cost_usd),
            )
            for row in cost.by_model
        ],
    )


def findings_of(analysis: Analysis) -> tuple[Finding, ...]:
    """The findings a payload would be built from."""
    return analysis.findings


__all__ = [
    "ROOT_AGENT",
    "SENTINELS",
    "findings_of",
    "input_haystack",
    "sentinel_analysis",
    "sentinels_present_in_inputs",
    "totals_for",
    "write_sentinel_trace",
]
