"""The machine-readable report: R36's JSON half, R33 at the render boundary, R38.

The document's job is to be a function of the trace and the flags and of nothing
else, and to be the place every trace-derived byte is redacted — "not by the
caller, and not already, upstream".

Two structures here carry most of the weight.

**The sentinel sweep.** :func:`sentinel_trace` builds a trace in which every
trace-derived string field on every model carries a distinct marker. Rendering
it twice — with previews and with ``--no-previews`` — turns "is the guard
complete?" into a set comparison over *paths*, and the checked-in
:data:`LEAKING_PATHS` is what makes a newly added trace-derived field visible:
add one to ``span_document`` without routing it through ``free_text`` and a new
path appears in the surviving set, which is a failure rather than a silence.
That is the arm the coder asked for and it is the one this project keeps
getting wrong.

**The non-vacuous arm.** A ``--no-previews`` assertion that nothing appears is
satisfied perfectly by a renderer that emits nothing at all. Every such
assertion below is paired with the same sweep run *without* the flag, asserting
the payloads do appear.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from swarm_observer import __version__
from swarm_observer.cost.compute import compute_costs, format_usd
from swarm_observer.cost.snapshot import SnapshotRateSource
from swarm_observer.detect.base import (
    SEVERITIES,
    TRACE_DERIVED_METRIC_KEYS,
    DetectorConfig,
    Finding,
    build_finding,
)
from swarm_observer.detect.registry import DETECTOR_SLUGS, run_detectors_with_waste
from swarm_observer.model.trace import (
    TRACE_SCHEMA_VERSION,
    AgentRun,
    ParseWarning,
    SourceFile,
    SpanError,
    TokenUsage,
    Trace,
)
from swarm_observer.report.json_out import (
    REDACTION_CAVEAT,
    REPORT_FORMAT_VERSION,
    RenderOptions,
    agent_document,
    cost_document,
    finding_document,
    format_timestamp,
    free_text,
    last_timestamp,
    metrics_document,
    optional_timestamp,
    render_json,
    report_document,
    severity_counts,
    span_document,
    usage_document,
)
from swarm_observer.report.redact import marker

from .detector_corpus import fixture_paths, load_trace
from .harness import assert_deterministic, sha256_text
from .synthetic_traces import TraceBuilder

SHIPPED = SnapshotRateSource()

#: The marker every trace-derived string in :func:`sentinel_trace` carries.
SENTINEL = "QQSENTINEL"


def render_of(trace: Trace, *, previews: bool = True, findings: tuple[Finding, ...] = ()) -> str:
    """One report's bytes for ``trace``, with the real cost engine behind it."""
    return render_json(
        trace=trace,
        findings=findings,
        cost=compute_costs(trace, SHIPPED),
        tool_version=__version__,
        options=RenderOptions(previews=previews),
    )


def document_of(
    trace: Trace, *, previews: bool = True, findings: tuple[Finding, ...] = ()
) -> dict[str, Any]:
    """The same document as parsed JSON."""
    return json.loads(render_of(trace, previews=previews, findings=findings))


def string_leaves(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string leaf in a JSON document, with a structural path.

    List indices collapse to ``[]`` so the path names a *field* rather than a
    row: the question is which field leaked, not which of nine spans did.
    """
    found: list[tuple[str, str]] = []
    if isinstance(node, str):
        found.append((path, node))
    elif isinstance(node, dict):
        for key in sorted(node):
            found.extend(string_leaves(node[key], f"{path}.{key}" if path else key))
    elif isinstance(node, list):
        for item in node:
            found.extend(string_leaves(item, f"{path}[]"))
    return found


def sentinel_paths(document: dict[str, Any]) -> set[str]:
    """The paths at which a sentinel-bearing string survives into a document."""
    return {path for path, value in string_leaves(document) if SENTINEL in value}


def sentinel_trace(token: str = SENTINEL) -> Trace:
    """A trace whose every trace-derived string carries a distinct sentinel.

    Ids and enumerated values are *not* sentinelled: ``span_id`` is a digest
    (R5), ``kind`` and ``tool_result_status`` are enumerations this package
    authored, and ``tool_input_digest`` is a hash of the arguments (R7). The
    fields below are the ones whose bytes came out of the trace.

    ``token`` is a review addition. With :data:`CREDENTIAL` it produces the same
    trace with a real AWS key shape in every one of those fields — legal in all
    of them, because it satisfies R2's agent-id alphabet, R16's tool-name
    pattern and R10's detail-slug shape — which is what turns the sweep below
    from "a sentinel leaks here" into "a credential does not leak anywhere".
    """
    agent = f"{token}-AGENT"
    parent = f"{token}-PARENTAGENT"
    builder = TraceBuilder()
    builder.model_call(
        agent_id=agent,
        model=f"{token}-MODEL",
        usage=TokenUsage(input_tokens=5),
        text_preview=f"{token}-TEXTPREVIEW",
        start_ms=0,
        end_ms=1000,
    )
    error_span = builder.model_call(
        agent_id=agent,
        model=f"{token}-ERRORMODEL",
        error=SpanError(code="api_error", detail=f"{token}-ERRORDETAIL"),
    )
    builder.replace(error_span, stop_reason=f"{token}-STOPREASON")
    tool = builder.tool_call(
        agent_id=agent,
        tool_name=f"{token}.TOOLNAME",
        input_preview=f"{token}-INPUTPREVIEW",
        result_preview=f"{token}-RESULTPREVIEW",
    )
    builder.replace(tool, tool_use_id=f"{token}-TOOLUSEID")
    # One span the cost engine can actually price, so ``cost.spans[]`` is
    # populated and the sweep can see the fields on a priced row too.
    builder.model_call(
        agent_id=agent, model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000)
    )
    builder.warning("unknown_record_type", 1, f"{token}-WARNDETAIL")
    trace = builder.build()
    return trace.model_copy(
        update={
            "agents": (
                AgentRun(
                    agent_id=agent,
                    agent_index=0,
                    agent_type=f"{token}-AGENTTYPE",
                    description=f"{token}-DESCRIPTION",
                    parent_agent_id=parent,
                    depth=1,
                    span_seqs=(0, 1, 2, 3),
                ),
            ),
            "source_files": (
                SourceFile(
                    name=f"{token}-SOURCEFILE.jsonl",
                    sha256="0" * 64,
                    bytes=10,
                    records=3,
                ),
            ),
        }
    )


def sentinel_findings(trace: Trace, token: str = SENTINEL) -> tuple[Finding, ...]:
    """One finding whose every trace-derived field carries a sentinel.

    Added by review. Without it the sweep below rendered a document with
    ``findings: []``, so the **entire findings section** — the part of a report
    a reader trusts most (R16) — sat outside the set comparison that exists to
    notice a new trace-derived field. That is this project's first signature
    defect exactly: a fixture set with no case that could trip the check.
    ``findings[].agent_ids`` was reaching the document raw and the sweep could
    not see it.
    """
    return (
        build_finding(
            trace=trace,
            detector="repeated_tool_call",
            severity="warning",
            summary="2 identical tool calls",
            span_seqs=(2,),
            agent_ids=(f"{token}-AGENT",),
            metrics={"occurrences": 2, "tool_name": f"{token}.TOOLNAME"},
            previews=(f"{token}-FINDINGPREVIEW",),
            wasted=TokenUsage(input_tokens=5),
        ),
    )


def sentinel_document(*, previews: bool) -> dict[str, Any]:
    """The sweep's document: the sentinel trace **and** its sentinel finding."""
    trace = sentinel_trace()
    return document_of(trace, previews=previews, findings=sentinel_findings(trace))


#: The credential the shape sweep uses. It matches R2's agent-id alphabet, R16's
#: tool-name pattern and R10's warning-detail slug shape, so it can legally
#: occupy every field a sentinel occupies — which is what makes the sweep below
#: a statement about the redactor rather than about one field.
CREDENTIAL = "AKIAIOSFODNN7EXAMPLE"


def credential_document(*, previews: bool) -> str:
    """The sentinel document with every sentinel replaced by a real key shape.

    The sweep over :data:`LEAKING_PATHS` is a *set comparison against a
    checked-in list*, which means a red suite in increment 4 can be turned green
    by adding a line to that list rather than by fixing the leak. This is the
    check that cannot be silenced that way: it names no paths at all, so there
    is nothing to widen, and it asserts the property that actually matters.
    However the ``--no-previews`` question is eventually settled, a credential
    shape may not reach the document at any path, in either mode.
    """
    trace = sentinel_trace(CREDENTIAL)
    return render_of(trace, previews=previews, findings=sentinel_findings(trace, CREDENTIAL))


#: The paths at which a sentinel still reaches ``report.json`` under
#: ``--no-previews``. Checked in deliberately: this set is a bug report, not a
#: contract. Every entry is a trace-derived string that neither ``free_text``
#: nor ingest-time blanking covers (tester BUG-2). A path appearing here that is
#: not listed means a *new* trace-derived field escaped the guard.
LEAKING_PATHS: frozenset[str] = frozenset(
    {
        # Trace-derived identifiers. Review routed all of these through R33's
        # redactor in both modes; they are still listed because they are not
        # *blanked* under the flag, which is the half that stays open. See
        # `json_out.identifier` for why blanking a join key is refused, and
        # `LEAK_LEDGER_SIZE` below for why this list may not grow quietly.
        "agents[].agent_id",
        "agents[].parent_agent_id",
        "cost.by_agent[].agent_id",
        "cost.spans[].agent_id",
        "cost.unpriced[].agent_id",
        "spans[].agent_id",
        "warnings[].detail",
        # Added by review, not by the tester's sweep, and not because anything
        # regressed: the sweep rendered `findings: []`, so the whole findings
        # section was outside it. Both are the same identifier/metrics carve-out
        # as above — `metrics` deliberately so (A-c7: R15 hashes it into the
        # finding_id the same document prints).
        "findings[].agent_ids[]",
        "findings[].metrics.tool_name",
        # A source file's basename is path-derived rather than trace-derived, and
        # R47 already restricts it to a basename. Review routed it through the
        # redactor too (the tester's S21); it is not blanked, because a report
        # that cannot say which files it read is not a report.
        "meta.source_files[].name",
    }
)

#: The ledger's size, pinned as a literal. ``LEAKING_PATHS`` is compared as a
#: **set**, which makes a new leak in increment 4 fail the suite — and makes
#: "add a line to the list" a way to turn that red suite green again. Adding a
#: line now costs two edits in two places with this comment between them, which
#: is the same friction A11 puts on ``collection_floor.json`` and for the same
#: reason. It may be **lowered** freely; raising it is a reviewer's call.
LEAK_LEDGER_SIZE = 10


class TestJsonEmissionR36:
    """R36: the exact serialization options, and what they buy."""

    def test_r36_the_document_is_sorted_ascii_indented_and_newline_terminated(self) -> None:
        """R36: ``sort_keys``, ``ensure_ascii``, ``indent=2``, trailing newline."""
        text = render_of(sentinel_trace())
        assert text.endswith("}\n")
        assert text.count("\n}\n") == 1
        assert text.isascii()
        document = json.loads(text)
        assert text == json.dumps(document, sort_keys=True, ensure_ascii=True, indent=2) + "\n"

    def test_r36_every_object_in_the_document_has_sorted_keys(self) -> None:
        """R36: asserted structurally, not by re-serializing with the same call."""
        text = render_of(sentinel_trace())
        parsed = json.loads(text, object_pairs_hook=lambda pairs: pairs)

        def check(node: Any) -> None:
            if isinstance(node, list) and node and isinstance(node[0], tuple):
                keys = [key for key, _ in node]
                assert keys == sorted(keys), keys
                for _, value in node:
                    check(value)
            elif isinstance(node, list):
                for item in node:
                    check(item)

        check(parsed)

    def test_r36_a_non_ascii_trace_string_is_escaped_rather_than_emitted(self) -> None:
        """R36: ``ensure_ascii`` removes every encoding dependency from the bytes."""
        builder = TraceBuilder()
        builder.model_call(text_preview="right-to-left ‮ and an emoji \U0001f600")
        text = render_of(builder.build())
        assert text.isascii()
        assert "\\u202e" in text

    def test_r36_the_top_level_sections_are_the_pinned_set(self) -> None:
        """R36: the same data the HTML renders — header, agents, spans, findings, cost, warnings."""
        document = document_of(sentinel_trace())
        assert set(document) == {"meta", "agents", "spans", "findings", "cost", "warnings"}

    def test_r36_the_meta_block_names_its_provenance(self) -> None:
        """R36: schema version, adapter, trace id, source files, counts, options."""
        meta = document_of(sentinel_trace())["meta"]
        assert meta["schema_version"] == TRACE_SCHEMA_VERSION
        assert meta["report_format_version"] == REPORT_FORMAT_VERSION
        assert meta["tool"] == {"name": "swarm-observer", "version": __version__}
        assert meta["adapter"] == "claude_code_jsonl"
        assert set(meta["counts"]) == {
            "agents",
            "spans",
            "findings",
            "findings_by_severity",
            "warnings",
        }
        assert set(meta["options"]) == {"previews", "blocked_gap_seconds", "detectors"}
        assert meta["notes"]["redaction"] == REDACTION_CAVEAT

    def test_r36_every_decimal_is_its_six_decimal_string(self) -> None:
        """R36/R29: money is a fixed-point string, never a JSON number."""
        trace = sentinel_trace()
        document = document_of(trace)
        money = re.compile(r"^\d+\.\d{6}$")
        cost = document["cost"]
        assert money.fullmatch(cost["total"]["cost_usd"])
        for group in ("by_agent", "by_model", "spans"):
            for row in cost[group]:
                assert money.fullmatch(row["cost_usd"]), (group, row)
        text = render_of(trace)
        assert '"cost_usd": 0' not in text

    def test_r36_a_findings_wasted_cost_is_a_string_or_null(self) -> None:
        """R14/R36: ``Decimal | None`` renders as a six-decimal string or ``null``."""
        finding = build_finding(
            trace=sentinel_trace(),
            detector="repeated_tool_call",
            severity="warning",
            summary="two identical tool calls",
            span_seqs=(2,),
            agent_ids=(f"{SENTINEL}-AGENT",),
            metrics={"occurrences": 2},
            previews=(),
            wasted=TokenUsage(input_tokens=5),
        )
        assert finding_document(finding)["wasted_cost_usd"] is None
        priced = finding.model_copy(update={"wasted_cost_usd": Decimal("0.000123")})
        assert finding_document(priced)["wasted_cost_usd"] == "0.000123"
        assert finding_document(priced)["wasted_cost_usd"] == format_usd(Decimal("0.000123"))

    def test_r36_the_json_spans_array_is_not_capped(self) -> None:
        """A-c9: the 5,000-row cap is a browser concession, not a data rule."""
        builder = TraceBuilder()
        for _ in range(5_050):
            builder.user_message()
        document = document_of(builder.build())
        assert len(document["spans"]) == 5_050
        assert document["meta"]["counts"]["spans"] == 5_050

    def test_r36_findings_are_emitted_in_the_canonical_detector_order(self) -> None:
        """A-c8: the JSON keeps R13's order; R36's grouping is the HTML's rule."""
        path = next(p for p in fixture_paths() if p.stem == "duplicate_tool_call")
        trace = load_trace(path)
        run = run_detectors_with_waste(trace, DetectorConfig())
        document = json.loads(
            render_json(
                trace=trace,
                findings=run.findings,
                cost=compute_costs(trace, SHIPPED, waste_seqs=run.waste_seqs),
                tool_version=__version__,
                options=RenderOptions(),
            )
        )
        assert [item["finding_id"] for item in document["findings"]] == [
            finding.finding_id for finding in run.findings
        ]

    def test_r36_the_document_is_a_pure_function_of_its_inputs(self) -> None:
        """R36: two renders of one trace are byte-identical."""
        trace = sentinel_trace()
        assert render_of(trace) == render_of(trace)

    def test_r36_the_bytes_do_not_move_across_the_environment_matrix(self) -> None:
        """R36/R40: hash seed, timezone and locale change nothing in the document.

        In-process here; the subprocess arm — where ``TZ`` and ``LC_ALL`` take
        effect for real — is in the CLI module.
        """
        trace = sentinel_trace()
        assert_deterministic(lambda: render_of(trace))
        assert_deterministic(lambda: render_of(trace, previews=False))

    @pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.stem)
    def test_r36_every_fixture_renders_valid_json_in_both_modes(self, path: Path) -> None:
        """R36: the whole corpus through the renderer, with and without previews."""
        trace = load_trace(path)
        run = run_detectors_with_waste(trace, DetectorConfig())
        cost = compute_costs(trace, SHIPPED, waste_seqs=run.waste_seqs)
        for previews in (True, False):
            text = render_json(
                trace=trace,
                findings=run.findings,
                cost=cost,
                tool_version=__version__,
                options=RenderOptions(previews=previews),
            )
            document = json.loads(text)
            assert document["meta"]["schema_version"] == TRACE_SCHEMA_VERSION
            assert len(document["spans"]) == len(trace.spans)
            assert len(document["findings"]) == len(run.findings)


class TestTimestampsR36:
    """R36: timestamps are built digit by digit, in UTC, with milliseconds truncated."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (datetime(2026, 3, 2, 9, 0, 0, tzinfo=UTC), "2026-03-02T09:00:00.000Z"),
            (datetime(2026, 3, 2, 9, 0, 0, 999_999, tzinfo=UTC), "2026-03-02T09:00:00.999Z"),
            (datetime(2026, 3, 2, 9, 0, 0, 1, tzinfo=UTC), "2026-03-02T09:00:00.000Z"),
            (datetime(2026, 3, 2, 9, 0, 0, 1_000, tzinfo=UTC), "2026-03-02T09:00:00.001Z"),
            (datetime(2026, 12, 31, 23, 59, 59, 500_000, tzinfo=UTC), "2026-12-31T23:59:59.500Z"),
            (datetime(6, 1, 2, 3, 4, 5, tzinfo=UTC), "0006-01-02T03:04:05.000Z"),
        ],
    )
    def test_r36_the_timestamp_shape_is_fixed_width(self, value: datetime, expected: str) -> None:
        """R36: one shape for every value, including a zero microsecond."""
        assert format_timestamp(value) == expected

    def test_r36_milliseconds_truncate_rather_than_round(self) -> None:
        """R36: ``999_999`` microseconds is ``.999``, matching the detectors' rule."""
        assert format_timestamp(datetime(2026, 1, 1, 0, 0, 0, 999_999, tzinfo=UTC)).endswith(
            ".999Z"
        )
        assert format_timestamp(datetime(2026, 1, 1, 0, 0, 0, 999_499, tzinfo=UTC)).endswith(
            ".999Z"
        )

    def test_r36_isoformat_would_not_have_done(self) -> None:
        """R36: the premise — ``isoformat`` emits two different shapes."""
        zero = datetime(2026, 1, 1, tzinfo=UTC)
        nonzero = datetime(2026, 1, 1, 0, 0, 0, 5, tzinfo=UTC)
        assert len(zero.isoformat()) != len(nonzero.isoformat())
        assert len(format_timestamp(zero)) == len(format_timestamp(nonzero))

    def test_r36_a_non_utc_timestamp_renders_in_utc(self) -> None:
        """R36: the model normalizes to UTC, and the formatter reads what it gets."""
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=1)
        trace = builder.build()
        aware = trace.spans[0].start
        assert aware is not None and aware.utcoffset() == timedelta(0)
        assert format_timestamp(aware).endswith("Z")

    def test_r36_optional_timestamp_distinguishes_absence_from_zero(self) -> None:
        """R2: a span with no endpoint renders ``null``, not an epoch."""
        assert optional_timestamp(None) is None
        assert optional_timestamp(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00.000Z"

    def test_r36_the_provenance_clock_is_the_traces_own_last_timestamp(self) -> None:
        """R36: never ``now``. The value moves with the trace and with nothing else."""
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=5_000)
        builder.model_call(start_ms=1_000, end_ms=2_000)
        trace = builder.build()
        assert last_timestamp(trace) == format_timestamp(datetime(2026, 9, 9, 10, 0, 5, tzinfo=UTC))
        assert document_of(trace)["meta"]["trace_last_timestamp"] == last_timestamp(trace)

    def test_r36_a_trace_with_no_timing_has_a_null_provenance_clock(self) -> None:
        """R36: ``None`` rather than a substituted current time."""
        builder = TraceBuilder()
        builder.user_message()
        assert last_timestamp(builder.build()) is None

    def test_r36_the_provenance_clock_is_not_the_current_time(self) -> None:
        """R36: the trace's timestamps are in 2026-09; ``now`` is not asserted anywhere."""
        trace = sentinel_trace()
        stamped = document_of(trace)["meta"]["trace_last_timestamp"]
        assert stamped is not None
        assert stamped.startswith("2026-09-09T10:00:")


class TestRedactionAtTheBoundaryR33:
    """R33: every trace-derived string is redacted *here*, not by whoever called."""

    def test_r33_free_text_redacts_when_previews_are_on(self) -> None:
        """R33: the boundary applies the redactor rather than trusting upstream."""
        assert free_text("AKIAIOSFODNN7EXAMPLE", previews=True) == marker("aws_key_id")
        assert free_text("plain text", previews=True) == "plain text"

    def test_r33_a_credential_shaped_preview_is_redacted_in_the_document(self) -> None:
        """R33: end to end through ``span_document``."""
        builder = TraceBuilder()
        builder.model_call(text_preview="key AKIAIOSFODNN7EXAMPLE here")
        document = document_of(builder.build())
        assert document["spans"][0]["text_preview"] == f"key {marker('aws_key_id')} here"
        assert "AKIA" not in render_of(builder.build())

    def test_r33_the_unpriced_rows_recorded_model_is_redacted(self) -> None:
        """R30: "its recorded ``model`` (redacted and escaped like any trace string)"."""
        builder = TraceBuilder()
        builder.model_call(model="sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA", usage=TokenUsage())
        document = document_of(builder.build())
        assert document["cost"]["unpriced"][0]["model"] == marker("anthropic_key")
        assert document["cost"]["unpriced"][0]["reason"] == "model_not_in_snapshot"

    def test_r33_a_priced_rows_recorded_model_is_redacted_too(self) -> None:
        """R33: the priced table's ``model`` came out of the trace as well."""
        builder = TraceBuilder()
        builder.model_call(
            model="claude-haiku-4-5-AKIAIOSFODNN7EXAMPLE", usage=TokenUsage(input_tokens=10)
        )
        row = document_of(builder.build())["cost"]["spans"][0]
        assert row["model_key"] == "claude-haiku-4-5"
        assert "AKIA" not in row["model"]
        assert marker("aws_key_id") in row["model"]

    def test_r33_metrics_tool_name_is_redacted_because_a_shape_check_is_not_a_secret_check(
        self,
    ) -> None:
        """R16 + the increment-2 review's S13 ruling, asserted at the boundary."""
        assert frozenset({"tool_name"}) == TRACE_DERIVED_METRIC_KEYS
        rendered = metrics_document({"tool_name": "AKIAIOSFODNN7EXAMPLE", "occurrences": 4})
        assert rendered == {"tool_name": marker("aws_key_id"), "occurrences": 4}

    def test_r33_a_non_trace_derived_metric_is_left_alone(self) -> None:
        """R16: only the one admitted key is trace-derived; the rest are ours."""
        assert metrics_document({"reason": "no_result", "occurrences": 2}) == {
            "reason": "no_result",
            "occurrences": 2,
        }

    def test_r33_the_redactor_runs_on_the_named_key_and_not_on_every_string(self) -> None:
        """R16/R33 (mutation J16): ``and``, not ``or``.

        Widening the condition to any string value looks harmless — every other
        string metric is a slug this package wrote — but it moves the guarantee
        from "we know which value came from the trace" to "we redact everything
        and hope". The distinction is only visible on a key that is *not*
        trace-derived carrying a credential-shaped value, which R16 forbids a
        ``Finding`` from constructing and which ``metrics_document`` must
        nevertheless pass through unchanged.
        """
        rendered = metrics_document({"reason": "AKIAIOSFODNN7EXAMPLE", "occurrences": 1})
        assert rendered == {"reason": "AKIAIOSFODNN7EXAMPLE", "occurrences": 1}
        assert metrics_document({"tool_name": "AKIAIOSFODNN7EXAMPLE"}) == {
            "tool_name": marker("aws_key_id")
        }

    def test_r33_the_summary_is_not_redacted_because_it_has_no_trace_bytes(self) -> None:
        """R16: ``summary`` is built from integers and this package's own slugs."""
        finding = build_finding(
            trace=sentinel_trace(),
            detector="failed_tool_call",
            severity="info",
            summary="1 failed call to AKIAIOSFODNN7EXAMPLE",
            span_seqs=(2,),
            agent_ids=(),
            metrics={"failures": 1},
            previews=(),
            wasted=TokenUsage(),
        )
        assert finding_document(finding)["summary"] == "1 failed call to AKIAIOSFODNN7EXAMPLE"

    @pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.stem)
    def test_r33_no_fixture_renders_a_complete_credential_shape(self, path: Path) -> None:
        """R33: the corpus-wide arm, including the hostile fixture."""
        trace = load_trace(path)
        run = run_detectors_with_waste(trace, DetectorConfig())
        text = render_json(
            trace=trace,
            findings=run.findings,
            cost=compute_costs(trace, SHIPPED, waste_seqs=run.waste_seqs),
            tool_version=__version__,
            options=RenderOptions(),
        )
        for shape in ("AKIA", "sk-ant-", "ghp_", "xoxb-", "AIza", "eyJ"):
            assert shape not in text, f"{path.stem} rendered {shape}"

    def test_r33_the_hostile_fixture_does_produce_redaction_markers(self) -> None:
        """R33: the control arm — the assertion above is not vacuous."""
        path = next(p for p in fixture_paths() if p.stem == "hostile")
        text = render_of(load_trace(path))
        assert marker("aws_key_id") in text
        assert marker("anthropic_key") in text

    def test_r33_a_pem_block_whose_terminator_was_truncated_is_not_redacted(self) -> None:
        """R8 vs R33: the hole the coder flagged, pinned as it behaves today.

        R8 caps a preview at 240 code points and R33's ``private_key`` pattern
        needs both ``-----BEGIN … KEY-----`` and ``-----END … KEY-----``. A key
        longer than the remaining budget loses its terminator, so the pattern
        cannot match and the header plus the first ~60 characters of key
        material reach the report.

        This is pinned rather than xfailed because the fix is an amendment to
        R33's table, which the requirement pins verbatim: the implementation is
        a faithful transcription and changing it here would put code and spec
        out of step silently. When R33 gains an unterminated alternative, this
        test fails and is the place to record it.
        """
        path = next(p for p in fixture_paths() if p.stem == "hostile")
        text = render_of(load_trace(path))
        assert "-----BEGIN RSA PRIVATE KEY-----" in text
        assert "-----END RSA PRIVATE KEY-----" not in text
        assert marker("private_key") not in text
        # A *complete* block, which fits inside the preview budget, does redact —
        # so the miss above is about the truncation and not about the pattern.
        complete = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----"
        assert free_text(complete, previews=True) == marker("private_key")

    def test_r33_the_truncated_pem_is_absent_under_no_previews(self) -> None:
        """R38: the flag is what actually removes it, which is the point of A10."""
        path = next(p for p in fixture_paths() if p.stem == "hostile")
        assert "PRIVATE KEY" not in render_of(load_trace(path), previews=False)


class TestNoPreviewsGuardR38:
    """R38: ``--no-previews`` omits trace free text entirely — the A-c6 guard.

    The guard is new code in ``json_out.free_text`` and is shaped exactly like
    "correct for the one call path that happens to exist", so it is driven from
    both sides and structurally rather than field by field.
    """

    def test_r38_free_text_blanks_rather_than_redacts_under_the_flag(self) -> None:
        """R38: the mode omits the text; it does not merely mask a shape."""
        assert free_text("anything at all", previews=False) == ""
        assert free_text("AKIAIOSFODNN7EXAMPLE", previews=False) == ""

    def test_r38_an_absent_optional_field_stays_null_rather_than_becoming_empty(self) -> None:
        """R2: ``None`` and ``""`` are different claims and stay different."""
        builder = TraceBuilder()
        builder.model_call(model=None)
        document = document_of(builder.build(), previews=False)
        assert document["spans"][0]["model"] is None
        assert document["spans"][0]["text_preview"] == ""

    def test_r38_the_options_block_records_that_the_flag_was_used(self) -> None:
        """R38: "no previews" must be distinguishable from "no text in this trace"."""
        trace = sentinel_trace()
        assert document_of(trace, previews=False)["meta"]["options"]["previews"] is False
        assert document_of(trace, previews=True)["meta"]["options"]["previews"] is True

    def test_r38_the_sentinel_trace_is_loaded_in_the_first_place(self) -> None:
        """R38: the sweep's premise — every field below really carries a marker."""
        surviving = sentinel_paths(sentinel_document(previews=True))
        assert surviving >= {
            "spans[].model",
            "spans[].stop_reason",
            "spans[].tool_name",
            "spans[].tool_use_id",
            "spans[].text_preview",
            "spans[].tool_input_preview",
            "spans[].tool_result_preview",
            "spans[].error.detail",
            "agents[].agent_type",
            "agents[].description",
            "cost.unpriced[].model",
            "warnings[].detail",
            # Review: the findings section, which the sweep did not reach at all
            # until `sentinel_findings` existed.
            "findings[].agent_ids[]",
            "findings[].previews[]",
            "findings[].metrics.tool_name",
        }, surviving

    def test_r38_the_sweep_reaches_every_section_of_the_document(self) -> None:
        """R38: the premise behind the premise — no whole section is unswept.

        Added by review. The sweep is a set comparison over field paths, and a
        section that renders nothing contributes no paths and therefore cannot
        contribute a *missing* path either. `findings[]` was empty for the whole
        increment, which is how `findings[].agent_ids` reached the document raw
        without anything going red. Asserting the sections directly means the
        next empty one is a failure rather than a silence.
        """
        document = sentinel_document(previews=True)
        for section in ("agents", "spans", "findings", "warnings"):
            assert document[section], f"{section} is empty: the sweep cannot see it"
        for section in ("by_agent", "by_model", "spans", "unpriced"):
            assert document["cost"][section], f"cost.{section} is empty: the sweep cannot see it"

    def test_r38_no_new_trace_derived_field_escapes_the_guard(self) -> None:
        """R38: the sweep, as a set comparison over field paths.

        This is the test that answers "if increment 4 adds a trace-derived
        string to ``span_document`` and forgets ``free_text``, does anything go
        red?". The surviving set must be exactly the checked-in leak list; a new
        path is a new hole and an absent one is a fix that should shrink the
        list in the same commit.
        """
        surviving = sentinel_paths(sentinel_document(previews=False))
        assert surviving == set(LEAKING_PATHS), (
            f"unexpected: {sorted(surviving - LEAKING_PATHS)}; "
            f"fixed: {sorted(set(LEAKING_PATHS) - surviving)}"
        )

    def test_r38_the_leak_ledger_has_not_been_widened(self) -> None:
        """R38: the ledger is a bug report and may only shrink.

        Added by review. The set comparison above goes red when a new
        trace-derived field escapes the guard — and goes green again if somebody
        adds that field's path to ``LEAKING_PATHS``. That is the same shape as a
        self-chosen mutation set: a check whose subject is chosen by whoever
        needs it to pass. Pinning the size as a literal does not make widening
        impossible; it makes it a deliberate two-place edit with the reason
        written between them, which is exactly the friction A11 puts on the
        collection floor.
        """
        assert len(LEAKING_PATHS) == LEAK_LEDGER_SIZE, (
            "LEAKING_PATHS changed size. Shrinking it is a fix — lower "
            "LEAK_LEDGER_SIZE in the same commit. Growing it is a new leak, and "
            "needs a reason in the review, not a line in the list."
        )

    @pytest.mark.parametrize("previews", [True, False], ids=["default", "no-previews"])
    def test_r33_no_credential_shape_reaches_the_document_at_any_path(self, previews: bool) -> None:
        """R33: the arm that cannot be silenced by editing a list.

        Added by review, as the answer to "what makes ``LEAKING_PATHS`` red, and
        what could quietly make it green again?". This test names **no paths**,
        so there is nothing to widen. It renders the same trace with a real AWS
        key shape in every field a sentinel occupies — legal in all of them,
        because ``AKIAIOSFODNN7EXAMPLE`` satisfies R2's agent-id alphabet, R16's
        tool-name pattern and R10's detail-slug shape — and asserts the shape
        appears nowhere, in either mode.

        However the ``--no-previews`` question in ``LEAKING_PATHS`` is settled,
        this property must hold. It is the security half of that ledger, stated
        so that a fix to the *display* half cannot be mistaken for it.
        """
        text = credential_document(previews=previews)
        assert CREDENTIAL not in text
        assert marker("aws_key_id") in text

    def test_r33_the_credential_sweep_would_notice_a_field_that_skipped_the_boundary(
        self,
    ) -> None:
        """R33: the credential sweep's own control arm.

        A test asserting a string is absent is satisfied perfectly by a renderer
        that emits nothing. The same document, rendered with the sentinel token
        instead of the credential, must contain that token at every path the
        credential run would have — so the fields really are populated and the
        absence above is redaction rather than emptiness.
        """
        credential_paths = {
            path
            for path, value in string_leaves(json.loads(credential_document(previews=True)))
            if marker("aws_key_id") in value
        }
        assert credential_paths >= sentinel_paths(sentinel_document(previews=False))

    def test_r38_the_previews_arm_is_the_one_that_makes_it_non_vacuous(self) -> None:
        """R38: without the flag the same fields *do* carry their text."""
        with_previews = sentinel_paths(sentinel_document(previews=True))
        without = sentinel_paths(sentinel_document(previews=False))
        assert without < with_previews
        assert with_previews - without == {
            "agents[].agent_type",
            "agents[].description",
            "cost.unpriced[].model",
            "findings[].previews[]",
            "spans[].error.detail",
            "spans[].model",
            "spans[].stop_reason",
            "spans[].text_preview",
            "spans[].tool_input_preview",
            "spans[].tool_name",
            "spans[].tool_result_preview",
            "spans[].tool_use_id",
        }

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-2's remaining half, held open deliberately. Review routed every "
            "trace-derived identifier through R33's redactor (both modes), which closes "
            "the security half. It did not *blank* them under --no-previews: agent_id is "
            "the join key between spans[], agents[] and cost.by_agent[], and a warning's "
            "detail is half of the (code, detail) pair R10 aggregates on, so blanking "
            "collapses distinct rows. R2 calls both an identifier rather than free text, "
            "which is the reading the code follows; R38's 'entirely' is the reading this "
            "test follows. The PM settles it — see the review's ruling on S16."
        ),
    )
    def test_r38_no_trace_derived_string_survives_the_flag(self) -> None:
        """R38: "omits trace free text entirely", read as governing every field."""
        assert sentinel_paths(sentinel_document(previews=False)) == set()

    def test_r33_a_credential_shaped_agent_id_is_redacted_in_both_modes(self) -> None:
        """R33 (BUG-2, fixed in review): an agent id is trace-derived.

        ``AKIAIOSFODNN7EXAMPLE`` matches R2's agent-id alphabet exactly, so the
        mapper keeps it verbatim and it reached the report five times without
        passing the redactor — under ``--no-previews`` too. Both modes are
        driven, because the defect was present in both and a fix wired only into
        the ``--no-previews`` path would leave the default run leaking.
        """
        builder = TraceBuilder()
        builder.model_call(
            agent_id="AKIAIOSFODNN7EXAMPLE", model="nowhere", usage=TokenUsage(input_tokens=1)
        )
        for previews in (True, False):
            document = document_of(builder.build(), previews=previews)
            text = json.dumps(document)
            assert "AKIAIOSFODNN7EXAMPLE" not in text
            assert marker("aws_key_id") in text
            # Every one of the five paths the bug report named, by path rather
            # than by a substring search over the whole document: a search is
            # satisfied by one of them being fixed.
            assert document["spans"][0]["agent_id"] == marker("aws_key_id")
            assert document["agents"][0]["agent_id"] == marker("aws_key_id")
            assert document["cost"]["by_agent"][0]["agent_id"] == marker("aws_key_id")
            assert document["cost"]["unpriced"][0]["agent_id"] == marker("aws_key_id")

    def test_r33_a_credential_shaped_agent_id_is_redacted_on_a_priced_row_too(self) -> None:
        """R33: ``cost.spans[]`` — the fifth path, which needs a *priceable* span."""
        builder = TraceBuilder()
        builder.model_call(
            agent_id="AKIAIOSFODNN7EXAMPLE",
            model="claude-haiku-4-5",
            usage=TokenUsage(input_tokens=1_000),
        )
        document = document_of(builder.build())
        assert document["cost"]["spans"][0]["agent_id"] == marker("aws_key_id")

    def test_r33_a_credential_shaped_parent_agent_id_is_redacted(self) -> None:
        """R33: ``agents[].parent_agent_id`` — a second field, a second record."""
        agent = AgentRun(
            agent_id="child",
            agent_index=1,
            parent_agent_id="AKIAIOSFODNN7EXAMPLE",
            span_seqs=(0,),
        )
        for previews in (True, False):
            document = agent_document(agent, previews=previews)
            assert document["parent_agent_id"] == marker("aws_key_id")
        # ``None`` and a redacted value are different claims and stay different.
        assert agent_document(AgentRun(agent_id="root", agent_index=0))["parent_agent_id"] is None

    def test_r33_a_credential_shaped_finding_agent_id_is_redacted(self) -> None:
        """R33: ``findings[].agent_ids`` — the path the sentinel sweep could not see.

        Found by review rather than by the sweep: the sentinel trace produces no
        findings at all, so the whole ``findings[]`` section was outside the set
        comparison that is supposed to catch a new trace-derived field. R14 sorts
        agent ids into every finding, and they arrived there raw.
        """
        finding = build_finding(
            trace=sentinel_trace(),
            detector="failed_tool_call",
            severity="info",
            summary="one failed call",
            span_seqs=(2,),
            agent_ids=("AKIAIOSFODNN7EXAMPLE",),
            metrics={"failures": 1},
            previews=(),
            wasted=TokenUsage(),
        )
        assert finding_document(finding)["agent_ids"] == [marker("aws_key_id")]
        assert finding_document(finding, previews=False)["agent_ids"] == [marker("aws_key_id")]

    def test_r33_a_credential_shaped_warning_detail_is_redacted(self) -> None:
        """R33 (BUG-2): R4 puts the unknown record *type* into a warning detail."""
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage(input_tokens=1))
        builder.warning("unknown_record_type", 1, "ghp_abcdefghijklmnopqrstuv")
        for previews in (True, False):
            document = document_of(builder.build(), previews=previews)
            assert document["warnings"][0]["detail"] == marker("github_token")
            assert document["warnings"][0]["code"] == "unknown_record_type"
            assert document["warnings"][0]["count"] == 1

    def test_r33_an_ordinary_identifier_is_not_mangled_by_the_redactor(self) -> None:
        """R33: the non-vacuous arm — redaction must not rewrite ordinary ids.

        An agent id is a join key between ``spans[]``, ``agents[]`` and
        ``cost.by_agent[]``. If the redactor moved an ordinary id, those joins
        would break, so the ordinary case is asserted to be byte-identical and
        the three sections asserted to still agree with each other.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="agent-7", model="claude-haiku-4-5", usage=TokenUsage())
        document = document_of(builder.build())
        assert document["spans"][0]["agent_id"] == "agent-7"
        assert document["agents"][0]["agent_id"] == "agent-7"
        assert {row["agent_id"] for row in document["cost"]["by_agent"]} == {"agent-7"}
        assert document["warnings"] == []

    def test_r38_metrics_are_redacted_but_deliberately_not_blanked(self) -> None:
        """A-c7: R15 hashes ``metrics`` into the id the same document prints."""
        finding = build_finding(
            trace=sentinel_trace(),
            detector="repeated_tool_call",
            severity="warning",
            summary="two identical tool calls",
            span_seqs=(2,),
            agent_ids=(),
            metrics={"occurrences": 2, "tool_name": "Bash"},
            previews=("secret preview",),
            wasted=TokenUsage(),
        )
        blanked = finding_document(finding, previews=False)
        assert blanked["metrics"] == {"occurrences": 2, "tool_name": "Bash"}
        assert blanked["previews"] == [""]
        assert blanked["summary"] == "two identical tool calls"

    def test_r38_the_hostile_fixture_carries_no_payload_under_the_flag(self) -> None:
        """R38: the guard against the corpus it was written for, both arms."""
        path = next(p for p in fixture_paths() if p.stem == "hostile")
        trace = load_trace(path)
        payloads = (
            "onerror",
            "alert(1)",
            "javascript:",
            "{{7*7}}",
            "etc/passwd",
            "<script",
            "]]>",
            "data:text",
            "../../etc",
        )
        blanked = render_of(trace, previews=False)
        visible = render_of(trace, previews=True)
        for payload in payloads:
            assert payload not in blanked, payload
            assert payload in visible, f"{payload} is absent even with previews: vacuous arm"

    def test_r8_the_right_to_left_override_never_reaches_either_mode(self) -> None:
        """R8: a non-printable code point is normalized to a space at ingest.

        Recorded because the sweep above must not claim ``--no-previews``
        removed something R8 had already removed: U+202E is absent from the
        default render too, so it is not evidence about the flag.
        """
        path = next(p for p in fixture_paths() if p.stem == "hostile")
        assert "‮" in path.read_text(encoding="utf-8")
        assert "\\u202e" not in render_of(load_trace(path), previews=True)
        assert "\\u202e" not in render_of(load_trace(path), previews=False)


class TestSeverityCountsR40:
    """R40: the counts the stdout line and the header both read."""

    def test_r40_every_severity_is_present_even_at_zero(self) -> None:
        """R40: a missing key would make "none" indistinguishable from "unwritten"."""
        assert severity_counts([]) == {"info": 0, "warning": 0, "critical": 0}
        assert set(severity_counts([])) == set(SEVERITIES)

    def test_r40_the_counts_are_per_severity_not_per_detector(self) -> None:
        """R40: three findings of two severities count 2 and 1."""
        trace = sentinel_trace()
        findings = tuple(
            build_finding(
                trace=trace,
                detector="failed_tool_call",
                severity=severity,
                summary=f"{index} failures",
                span_seqs=(index,),
                agent_ids=(),
                metrics={"failures": index + 1},
                previews=(),
                wasted=TokenUsage(),
            )
            for index, severity in enumerate(("warning", "warning", "critical"))
        )
        assert severity_counts(findings) == {"info": 0, "warning": 2, "critical": 1}

    def test_r40_the_document_carries_the_same_counts(self) -> None:
        """R36/R40: the header and the stdout line cannot disagree."""
        path = next(p for p in fixture_paths() if p.stem == "duplicate_tool_call")
        trace = load_trace(path)
        run = run_detectors_with_waste(trace, DetectorConfig())
        document = json.loads(
            render_json(
                trace=trace,
                findings=run.findings,
                cost=compute_costs(trace, SHIPPED, waste_seqs=run.waste_seqs),
                tool_version=__version__,
                options=RenderOptions(),
            )
        )
        assert document["meta"]["counts"]["findings_by_severity"] == severity_counts(run.findings)
        assert document["meta"]["counts"]["findings"] == len(run.findings)


class TestDocumentPartsR36:
    """R36: each per-object document function, driven on its own."""

    def test_r36_usage_document_carries_the_five_components_and_a_total(self) -> None:
        """R2/R36: plain integers, and the total the reader would otherwise compute."""
        usage = TokenUsage(
            input_tokens=1,
            output_tokens=2,
            cache_read_input_tokens=4,
            cache_creation_5m_tokens=8,
            cache_creation_1h_tokens=16,
        )
        assert usage_document(usage) == {
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_input_tokens": 4,
            "cache_creation_5m_tokens": 8,
            "cache_creation_1h_tokens": 16,
            "total_tokens": 31,
        }

    def test_r36_span_document_keeps_ids_and_enumerations_unredacted(self) -> None:
        """R5/R7: a digest id is not trace-derived and must survive both modes."""
        trace = sentinel_trace()
        for previews in (True, False):
            rendered = span_document(trace.spans[2], previews=previews)
            assert rendered["span_id"] == trace.spans[2].span_id
            assert rendered["tool_input_digest"] == trace.spans[2].tool_input_digest
            assert rendered["kind"] == "tool_call"
            assert rendered["tool_result_status"] == "ok"
            assert rendered["error"] is None

    def test_r36_a_span_error_renders_its_enumerated_code_and_redacted_detail(self) -> None:
        """R2/R33: the code is ours, the detail is the trace's."""
        trace = sentinel_trace()
        rendered = span_document(trace.spans[1])
        assert rendered["error"]["code"] == "api_error"
        assert SENTINEL in rendered["error"]["detail"]
        assert span_document(trace.spans[1], previews=False)["error"]["detail"] == ""

    def test_r36_agent_document_carries_the_span_seqs_as_a_list(self) -> None:
        """R2/R36: tuples become JSON arrays."""
        agent = sentinel_trace().agents[0]
        rendered = agent_document(agent)
        assert rendered["span_seqs"] == [0, 1, 2, 3]
        assert rendered["agent_index"] == 0
        assert rendered["depth"] == 1

    def test_r36_cost_document_carries_the_four_groupings_and_the_unpriced_list(self) -> None:
        """R30/R31: the section a reader checks a dollar figure against."""
        trace = sentinel_trace()
        rendered = cost_document(compute_costs(trace, SHIPPED))
        assert set(rendered) == {
            "currency",
            "snapshot",
            "total",
            "by_agent",
            "by_model",
            "by_detector",
            "spans",
            "unpriced",
            "notes",
        }
        assert rendered["currency"] == "USD"
        assert rendered["snapshot"]["version"] == SHIPPED.meta.version
        assert rendered["notes"]["rates"]
        assert rendered["notes"]["waste"]

    def test_r36_the_unpriced_row_names_its_missing_price_keys(self) -> None:
        """R30.4: enumerated slugs, as a list."""
        from swarm_observer.cost.source import RateEntry, RateSnapshot, RateSourceRef, SnapshotMeta

        snapshot = RateSnapshot(
            meta=SnapshotMeta(
                version="t",
                snapshot_date="2026-01-01",
                sources=(
                    RateSourceRef(id="s", label="l", url="u", as_of="2026-01-01", models=("m-1",)),
                ),
            ),
            models={"m-1": RateEntry(input="1")},
        )
        builder = TraceBuilder()
        builder.model_call(model="m-1", usage=TokenUsage(cache_creation_1h_tokens=3))
        rendered = cost_document(
            compute_costs(builder.build(), SnapshotRateSource(snapshot=snapshot))
        )
        assert rendered["unpriced"][0]["reason"] == "rate_key_missing"
        assert rendered["unpriced"][0]["missing_price_keys"] == ["cache_write_1h"]

    def test_r36_render_options_are_frozen_and_closed(self) -> None:
        """R38: the flags recorded in the document are a fixed set."""
        options = RenderOptions(previews=False, blocked_gap_seconds=90, detectors=("agent_loop",))
        assert options.previews is False
        with pytest.raises(ValidationError):
            options.previews = True  # type: ignore[misc]
        with pytest.raises(ValidationError):
            RenderOptions(surprise=1)  # type: ignore[call-arg]

    def test_r36_the_detector_option_list_is_recorded_in_registry_order(self) -> None:
        """R38: the header says which detectors ran."""
        trace = sentinel_trace()
        document = json.loads(
            render_json(
                trace=trace,
                findings=(),
                cost=compute_costs(trace, SHIPPED),
                tool_version=__version__,
                options=RenderOptions(detectors=DETECTOR_SLUGS),
            )
        )
        assert document["meta"]["options"]["detectors"] == list(DETECTOR_SLUGS)

    def test_r36_warnings_render_code_count_and_detail(self) -> None:
        """R10/R36: the full parse-warning list, aggregated and sorted."""
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage())
        builder.warning("negative_duration", 2)
        builder.warning("unknown_record_type", 1, "attachment")
        rendered = document_of(builder.build())["warnings"]
        assert rendered == [
            {"code": "negative_duration", "count": 2, "detail": ""},
            {"code": "unknown_record_type", "count": 1, "detail": "attachment"},
        ]

    def test_r36_report_document_and_render_json_agree(self) -> None:
        """R36: the serializer adds formatting and nothing else."""
        trace = sentinel_trace()
        cost = compute_costs(trace, SHIPPED)
        document = report_document(
            trace=trace,
            findings=(),
            cost=cost,
            tool_version=__version__,
            options=RenderOptions(),
        )
        text = render_json(
            trace=trace,
            findings=(),
            cost=cost,
            tool_version=__version__,
            options=RenderOptions(),
        )
        assert json.loads(text) == document
        assert sha256_text(text) == sha256_text(
            json.dumps(document, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
        )


class TestParseWarningTaxonomyR36:
    """R10/R36: a warning detail is a slug, and the renderer does not widen it."""

    def test_r36_a_warning_detail_cannot_carry_free_text_by_construction(self) -> None:
        """R10: the model's alphabet is the enforcement, not the renderer's care."""
        with pytest.raises(ValidationError):
            ParseWarning(code="unknown_record_type", count=1, detail="<script>alert(1)</script>")
        with pytest.raises(ValidationError):
            ParseWarning(code="not_in_the_enum", count=1)


class TestWaveTwoGapsR36R40:
    """The document fields the second mutation sweep found nothing asserting.

    Wave 1 of the sweep concentrated on the guards and the orderings. Wave 2 was
    designed afterwards, deliberately over anchors wave 1 never touched — the
    document *body*: version literals, defaults, and the count and usage fields
    whose two plausible sources happen to agree on every fixture in the corpus.

    Every test here names its mutant id and builds the input that makes the two
    sources disagree, because on the checked-in corpus they mostly do not.
    """

    def test_r36_the_report_format_version_is_the_pinned_literal(self) -> None:
        """R36 (mutation W-J01): the document declares its own format version.

        Asserted as a literal here and read from the module everywhere else, so
        a bump is a deliberate edit to one line of one test rather than a value
        that follows the code wherever it goes. It is also distinct from
        ``TRACE_SCHEMA_VERSION``: the trace model and the report layout move
        separately, and a reader parsing ``report.json`` keys off this one.
        """
        assert REPORT_FORMAT_VERSION == "1.0.0"
        document = document_of(sentinel_trace())
        assert document["meta"]["report_format_version"] == "1.0.0"
        assert document["meta"]["schema_version"] == TRACE_SCHEMA_VERSION

    def test_r38_render_options_defaults_are_the_permissive_ones(self) -> None:
        """R38 (mutations W-J08, W-J09): the defaults an API caller inherits.

        ``RenderOptions()`` with no argument is what a library caller who never
        heard of ``--no-previews`` constructs. If ``previews`` defaulted to
        ``False`` every such caller would silently get a blanked report and no
        test in the suite would notice, because every CLI path passes the flag
        explicitly. The gap default is pinned for the same reason: it is echoed
        into ``meta.options`` and a reader compares it against the run they
        thought they asked for.
        """
        options = RenderOptions()
        assert options.previews is True
        assert options.blocked_gap_seconds == 60
        assert options.detectors == ()
        document = report_document(
            trace=sentinel_trace(),
            findings=(),
            cost=compute_costs(sentinel_trace(), SHIPPED),
            tool_version=__version__,
            options=RenderOptions(),
        )
        assert document["meta"]["options"] == {
            "previews": True,
            "blocked_gap_seconds": 60,
            "detectors": [],
        }

    def test_r36_meta_counts_warnings_counts_the_traces_warnings(self) -> None:
        """R36 (mutation W-J14): a trace *with* warnings, so zero is falsifiable.

        Every count in ``meta.counts`` was asserted against a trace whose
        warning list happened to be empty, which makes ``len(trace.warnings)``
        and the literal ``0`` indistinguishable.
        """
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage(input_tokens=1))
        builder.warning("unknown_record_type", 3, "some_detail")
        builder.warning("missing_usage", 2, "another_detail")
        document = document_of(builder.build())
        assert document["meta"]["counts"]["warnings"] == 2
        assert len(document["warnings"]) == 2

    def test_r36_a_warnings_row_carries_its_own_count_not_one(self) -> None:
        """R36 (mutation W-J15): ``count`` is the occurrence tally, not a 1.

        R9 collapses repeated parse warnings into one row *with a count*, so a
        row that always reports 1 throws away the whole point of the collapse.
        The two warnings above carry 3 and 2 so neither can be confused with a
        constant.
        """
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage(input_tokens=1))
        builder.warning("unknown_record_type", 3, "some_detail")
        builder.warning("missing_usage", 2, "another_detail")
        rows = {row["code"]: row["count"] for row in document_of(builder.build())["warnings"]}
        assert rows == {"unknown_record_type": 3, "missing_usage": 2}

    def test_r30_unpriced_usage_is_the_unpriced_spans_tokens_not_the_total(self) -> None:
        """R30/R31 (mutation W-J16): a trace where the two totals differ.

        ``cost.total.unpriced_usage`` answers "how many tokens does this report
        have no price for". On a trace whose every span is priced — or whose
        every span is unpriced — it equals ``usage`` and the field is
        untestable. This trace is deliberately mixed.
        """
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        builder.model_call(model="nothing-published", usage=TokenUsage(input_tokens=77))
        document = document_of(builder.build())
        total = document["cost"]["total"]
        assert total["usage"]["input_tokens"] == 1_000
        assert total["unpriced_usage"]["input_tokens"] == 77
        assert total["usage"] != total["unpriced_usage"], "vacuous: the two agree on this trace"
        assert total["priced_spans"] == 1
        assert total["unpriced_spans"] == 1

    def test_r31_by_detector_reports_unknown_cost_findings_separately_from_all(self) -> None:
        """R31/A-c4 (mutation W-J13): ``findings`` and ``findings_unpriced`` differ.

        A-c4's whole argument is that a per-detector ``0.000000`` is legible
        *because* ``findings_with_unknown_cost`` sits beside it. If the two
        columns carry the same number the reassurance is a tautology. Here one
        detector has two findings of which exactly one is unpriceable, which is
        the only shape that can tell the two apart.
        """
        builder = TraceBuilder()
        priced = builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        unpriceable = builder.model_call(
            model="nothing-published", usage=TokenUsage(input_tokens=1_000)
        )
        trace = builder.build()
        cost = compute_costs(
            trace,
            SHIPPED,
            waste_seqs={
                "repeated_tool_call:aaaaaaaaaaaa": (priced.seq,),
                "repeated_tool_call:bbbbbbbbbbbb": (unpriceable.seq,),
            },
        )
        rows = cost_document(cost)["by_detector"]
        assert len(rows) == 1
        row = rows[0]
        assert row["detector"] == "repeated_tool_call"
        assert row["findings"] == 2
        assert row["findings_with_unknown_cost"] == 1
        assert row["findings"] != row["findings_with_unknown_cost"], "vacuous: the two agree"
