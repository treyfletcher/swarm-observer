"""Adversarial inputs against the increment-1 surface (R2, R5, R7, R8, R10, R11, R12).

Every byte in a trace was produced by somebody else's agent, which may have
processed a hostile web page, repository or document. Increment 1 does not
render anything yet, so the question this module asks is narrower than the
injection probe that arrives with the HTML report: *given a hostile trace, does
the ingestion layer produce a well-formed normalized model, or does it produce
an exception, a malformed id, or a field that carries the attacker's bytes into
a place the model promised it could not reach?*

The payload corpus is the spec's, plus the shapes an arithmetic bug lives in:
token counts past 2**64, negative durations, timestamps at the epoch and in the
far future, and a trace whose every record is a duplicate of one another.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm_observer.ingest.claude_code.mapper import ClaudeCodeSource
from swarm_observer.ingest.source import IngestLimits, TraceError
from swarm_observer.model.trace import (
    AGENT_ID_PATTERN,
    PREVIEW_MAX_CHARS,
    Trace,
)

from . import factories as f

DEFAULTS = IngestLimits()

#: The spec's injection corpus, minus the payloads that only matter once there
#: is a renderer. Every one of these must survive ingestion as inert text.
PAYLOADS: tuple[str, ...] = (
    "</script><script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "<!--",
    "]]>",
    "&lt;script&gt;",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "\x00\x1b[31mred\x1b[0m",
    "‮override",
    "zero​width​joiner",
    "{{7*7}}",
    "../../etc/passwd",
    "AKIAIOSFODNN7EXAMPLE",
    "sk-ant-api03-" + "a" * 40,
    "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----",
    "line\nbreak\rcarriage",
    "🙂" * 100,
    "x" * 10_000,
)


def load(records: list[dict[str, object]], tmp_path: Path, name: str, **kwargs: object) -> Trace:
    path = f.write_trace(tmp_path, records, name=name)
    return ClaudeCodeSource(read_sidecars=False, **kwargs).load([path], DEFAULTS)  # type: ignore[arg-type]


def assert_well_formed(trace: Trace) -> None:
    """Every model invariant the ingestion layer promises, re-checked."""
    assert len(trace.trace_id) == 16
    for span in trace.spans:
        assert len(span.span_id) == 16
        assert set(span.span_id) <= set("0123456789abcdef")
        assert len(span.text_preview) <= PREVIEW_MAX_CHARS
        assert len(span.tool_input_preview) <= PREVIEW_MAX_CHARS
        assert len(span.tool_result_preview) <= PREVIEW_MAX_CHARS
        for preview in (span.text_preview, span.tool_input_preview, span.tool_result_preview):
            assert all(char.isprintable() for char in preview)
            assert "\n" not in preview
    for warning in trace.warnings:
        assert len(warning.detail) <= 64


class TestHostileStringsThroughPreviewsR8:
    """R8: hostile free text survives as inert, bounded, single-line preview text."""

    @pytest.mark.parametrize("payload", PAYLOADS, ids=lambda p: repr(p[:24]))
    def test_r8_a_hostile_assistant_text_block_is_normalized_not_rejected(
        self, tmp_path: Path, payload: str
    ) -> None:
        """R8: the mapper flattens and bounds; it never refuses a legal record."""
        record = f.assistant("a1", content=[f.text_block(payload)], usage_block=f.usage())
        trace = load([record], tmp_path, name="agent-1")
        assert_well_formed(trace)
        assert len(trace.spans) == 1

    @pytest.mark.parametrize("payload", PAYLOADS, ids=lambda p: repr(p[:24]))
    def test_r8_a_hostile_tool_input_is_digested_and_previewed(
        self, tmp_path: Path, payload: str
    ) -> None:
        """R7, R8: the digest is hex and the preview is bounded, whatever the input."""
        record = f.assistant(
            "a1", content=[f.tool_use_block("t1", "Bash", {"cmd": payload})], usage_block=f.usage()
        )
        trace = load([record], tmp_path, name="agent-1")
        assert_well_formed(trace)
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        assert tool.tool_input_digest is not None
        assert set(tool.tool_input_digest) <= set("0123456789abcdef")

    @pytest.mark.parametrize("payload", PAYLOADS, ids=lambda p: repr(p[:24]))
    def test_r8_a_hostile_tool_result_is_previewed(self, tmp_path: Path, payload: str) -> None:
        """R8: the result text is the string an attacking tool controls most directly."""
        records = [
            f.assistant("a1", content=[f.tool_use_block("t1", "Bash", {})], usage_block=f.usage()),
            f.user(
                "u1",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.tool_result_block("t1", payload)],
            ),
        ]
        assert_well_formed(load(records, tmp_path, name="agent-1"))

    @pytest.mark.parametrize("payload", PAYLOADS, ids=lambda p: repr(p[:24]))
    def test_r5_a_hostile_agent_id_never_reaches_an_id(self, tmp_path: Path, payload: str) -> None:
        """R5: whatever the ``agentId`` holds, the model's id matches the alphabet."""
        import re

        record = f.assistant("a1", agent_id=payload, usage_block=f.usage())
        trace = load([record], tmp_path, name="agent-1")
        for span in trace.spans:
            assert re.match(AGENT_ID_PATTERN, span.agent_id)
        for agent in trace.agents:
            assert re.match(AGENT_ID_PATTERN, agent.agent_id)


class TestHostileModelAndToolNamesR2:
    """R2: the capped structural fields, which survive ``--no-previews``."""

    @pytest.mark.parametrize("length", [0, 1, 199, 200])
    def test_r2_a_model_id_up_to_the_cap_is_carried(self, tmp_path: Path, length: int) -> None:
        """R2: the field caps at 200 characters, and everything under it is fine."""
        record = f.assistant("a1", model="m" * length or None, usage_block=f.usage())
        trace = load([record], tmp_path, name=f"agent-{length}")
        assert trace.spans[0].model == ("m" * length or None)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-1: preview(value, DETAIL_MAX_CHARS) returns 201 code points when it "
            "truncates, but Span.model caps at 200, so an over-long model id raises an "
            "un-sanitized ValidationError that quotes the trace"
        ),
    )
    @pytest.mark.parametrize("field", ["model", "stop_reason"])
    def test_r2_an_over_long_structural_field_is_truncated_not_fatal(
        self, tmp_path: Path, field: str
    ) -> None:
        """R2, R11: a 300-character model id must be capped, not crash the adapter."""
        record = f.assistant("a1", usage_block=f.usage())
        record["message"][field] = "Z" * 300  # type: ignore[index]
        trace = load([record], tmp_path, name=f"agent-{field}")
        value = getattr(trace.spans[0], field)
        assert value is not None and len(value) <= 200

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-1: the same off-by-one on Span.tool_name and Span.tool_use_id",
    )
    @pytest.mark.parametrize("key", ["name", "id"])
    def test_r2_an_over_long_tool_field_is_truncated_not_fatal(
        self, tmp_path: Path, key: str
    ) -> None:
        """R2, R11: a hostile tool name is a capped string, not an exception."""
        block = f.tool_use_block("t1", "Bash", {})
        block[key] = "Z" * 300
        record = f.assistant("a1", content=[block], usage_block=f.usage())
        trace = load([record], tmp_path, name=f"agent-tool-{key}")
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        for value in (tool.tool_name, tool.tool_use_id):
            assert value is None or len(value) <= 200

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-1: the same off-by-one on SpanError.detail, fed from apiErrorStatus",
    )
    def test_r2_an_over_long_api_error_status_is_truncated_not_fatal(self, tmp_path: Path) -> None:
        """R2, R12: the error detail is capped at 200 characters, per the model."""
        trace = load([f.api_error("e1", status="S" * 400)], tmp_path, name="agent-1")
        span = trace.spans[0]
        assert span.error is not None and len(span.error.detail) <= 200


class TestNumericAndTemporalEdgesR2:
    """R2: arithmetic-shaped hostility — huge counts, negatives, extreme instants."""

    @pytest.mark.parametrize(
        "value", [2**31, 2**63 - 1, 2**64, 2**70, 10**30, 9_999_999_999_999_999_999]
    )
    def test_r2_an_integer_overflow_shaped_token_count_is_carried_exactly(
        self, tmp_path: Path, value: int
    ) -> None:
        """R2: Python ints do not overflow, and the model must not clamp them either."""
        record = f.assistant("a1", usage_block=f.usage(input_tokens=value))
        usage = load([record], tmp_path, name=f"agent-{value % 97}").spans[0].usage
        assert usage is not None
        assert usage.input_tokens == value
        assert usage.total == value

    @pytest.mark.parametrize("value", [-1, -(2**63), -0.5, "many", None, True, False, [], {}])
    def test_r2_a_non_count_token_value_becomes_zero(self, tmp_path: Path, value: object) -> None:
        """R4, R2: a negative or wrong-typed count is coerced, never carried."""
        record = f.assistant("a1", usage_block={"input_tokens": value})
        usage = load([record], tmp_path, name="agent-1").spans[0].usage
        assert usage is not None
        assert usage.input_tokens == 0

    @pytest.mark.parametrize(
        "timestamp",
        [
            "1970-01-01T00:00:00.000Z",
            "1970-01-01T00:00:00+00:00",
            "0001-01-01T00:00:00.000Z",
            "9999-12-31T23:59:59.999Z",
            "2026-09-09T10:00:00.123456Z",
            "2026-09-09T10:00:00-11:00",
            "2026-09-09T10:00:00+14:00",
        ],
    )
    def test_r2_boundary_timestamps_parse_to_aware_utc(
        self, tmp_path: Path, timestamp: str
    ) -> None:
        """R2: an extreme but legal instant is an instant, not an error."""
        trace = load([f.user("u1", timestamp=timestamp)], tmp_path, name="agent-1")
        start = trace.spans[0].start
        assert start is not None
        assert start.tzinfo is not None
        assert start.utcoffset() is not None and start.utcoffset().total_seconds() == 0

    def test_r6_a_backwards_clock_never_produces_a_negative_duration(self, tmp_path: Path) -> None:
        """R6: "any computed duration that comes out negative is clamped to 0"."""
        shared = {"message_id": "m", "request_id": "r"}
        records = [
            f.assistant(
                "a1", timestamp="9999-01-01T00:00:00.000Z", usage_block=f.usage(), **shared
            ),
            f.assistant(
                "a2", timestamp="1970-01-01T00:00:00.000Z", usage_block=f.usage(), **shared
            ),
        ]
        trace = load(records, tmp_path, name="agent-1")
        call = trace.spans[0]
        assert call.start is not None and call.end is not None
        assert call.end == call.start
        assert any(w.code == "negative_duration" for w in trace.warnings)

    def test_r6_a_tool_result_recorded_before_its_call_is_clamped(self, tmp_path: Path) -> None:
        """R6: the same clamp on the tool-call window that a later timing detector reads."""
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:10.000Z",
                content=[f.tool_use_block("t1", "Bash", {})],
                usage_block=f.usage(),
            ),
            f.user(
                "u1",
                timestamp="2026-09-09T10:00:00.000Z",
                content=[f.tool_result_block("t1", "early")],
            ),
        ]
        trace = load(records, tmp_path, name="agent-1")
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        assert tool.start is not None and tool.end is not None
        assert tool.end >= tool.start


class TestDegenerateTracesR11:
    """R11, R2: traces that are all one thing."""

    def test_r11_a_trace_of_identical_records_is_fatal_on_the_second(self, tmp_path: Path) -> None:
        """R11: every record a duplicate — the duplicate-uuid rule fires immediately."""
        record = f.assistant("a1", usage_block=f.usage())
        with pytest.raises(TraceError) as raised:
            load([record] * 500, tmp_path, name="agent-1")
        assert raised.value.code == "duplicate_uuid"
        assert raised.value.line == 2

    def test_r11_a_trace_of_records_differing_only_in_uuid_collapses_to_one_call(
        self, tmp_path: Path
    ) -> None:
        """R9, R11: 500 fragments of one response are one model call, not 500."""
        records = [
            f.assistant(f"a{index}", usage_block=f.usage(input_tokens=7, cache_read=1_000))
            for index in range(500)
        ]
        trace = load(records, tmp_path, name="agent-1")
        assert len([span for span in trace.spans if span.kind == "model_call"]) == 1
        usage = trace.spans[0].usage
        assert usage is not None
        assert usage.cache_read_input_tokens == 1_000, "500 fragments must not sum to 500,000"

    def test_r11_a_trace_of_only_attachments_has_no_spans_and_no_agents(
        self, tmp_path: Path
    ) -> None:
        """R12: attachments produce no span, so a trace of them is empty but valid."""
        records = [
            f.attachment(f"at{index}", timestamp=f"2026-09-09T10:00:{index:02d}.000Z")
            for index in range(10)
        ]
        trace = load(records, tmp_path, name="agent-1")
        assert trace.spans == ()
        assert trace.agents == ()
        assert trace.source_files[0].records == 10

    def test_r11_a_trace_of_only_unknown_record_types_is_valid_and_counted(
        self, tmp_path: Path
    ) -> None:
        """R4: an entirely unfamiliar transcript degrades to warnings, not an exit 2."""
        records = [
            {"type": "mystery", "uuid": f"x{index}", "timestamp": f.BASE_TIME}
            for index in range(20)
        ]
        trace = load(records, tmp_path, name="agent-1")  # type: ignore[arg-type]
        assert trace.spans == ()
        counted = {(w.code, w.detail): w.count for w in trace.warnings}
        assert counted[("unknown_record_type", "mystery")] == 20


class TestHostileSidecarsR12:
    """A1: the sidecar is opportunistic metadata, and every failure mode is "none"."""

    def _with_sidecar(self, tmp_path: Path, payload: object, name: str = "agent-1") -> Trace:
        path = f.write_trace(tmp_path, [f.assistant("a1", usage_block=f.usage())], name=name)
        sidecar = path.with_suffix(".meta.json")
        sidecar.write_text(
            payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
        )
        return ClaudeCodeSource(read_sidecars=True).load([path], DEFAULTS)

    @pytest.mark.parametrize(
        "payload",
        ["not json at all", "[]", '"a string"', "123", "null", "{}", '{"agentType": 12}'],
    )
    def test_r12_a_malformed_sidecar_is_simply_no_metadata(
        self, tmp_path: Path, payload: str
    ) -> None:
        """A1: "every failure mode is no metadata" — never a failed run."""
        trace = self._with_sidecar(tmp_path, payload)
        assert trace.agents[0].agent_type is None
        assert trace.agents[0].description == ""

    def test_r12_a_hostile_sidecar_description_is_previewed(self, tmp_path: Path) -> None:
        """R8: sidecar free text goes through the same single preview definition."""
        trace = self._with_sidecar(
            tmp_path, {"description": "</script>\n\x00<img src=x>", "agentType": "gen"}
        )
        description = trace.agents[0].description
        assert "\n" not in description and "\x00" not in description
        assert all(char.isprintable() for char in description)

    def test_r12_a_missing_sidecar_is_not_an_error(self, tmp_path: Path) -> None:
        """A1: "their absence is never an error"."""
        path = f.write_trace(tmp_path, [f.assistant("a1", usage_block=f.usage())])
        trace = ClaudeCodeSource(read_sidecars=True).load([path], DEFAULTS)
        assert trace.agents[0].agent_type is None

    @pytest.mark.xfail(
        strict=True,
        raises=Exception,
        reason=(
            "BUG-2: a sidecar with a negative spawnDepth raises an un-sanitized "
            "ValidationError out of load(); AgentRun.depth is ge=0 and the mapper "
            "passes any int straight through"
        ),
    )
    def test_r12_a_negative_sidecar_depth_is_dropped_not_fatal(self, tmp_path: Path) -> None:
        """A1: a hostile depth must degrade to no metadata, like every other field."""
        trace = self._with_sidecar(tmp_path, {"spawnDepth": -5, "description": "d"})
        assert trace.agents[0].depth in (None, 0)

    def test_r12_a_wrong_typed_sidecar_depth_is_dropped(self, tmp_path: Path) -> None:
        """A1: a non-integer depth is absence, which is already handled."""
        for index, value in enumerate(["deep", 1.5, True, None, [], {}]):
            trace = self._with_sidecar(tmp_path, {"spawnDepth": value}, name=f"agent-depth-{index}")
            assert trace.agents[0].depth is None

    def test_r12_a_sidecar_symlink_is_ignored(self, tmp_path: Path) -> None:
        """A1: the sidecar read must not follow a link out of the input directory."""
        secret = tmp_path / "secret.json"
        secret.write_text(json.dumps({"description": "SIDECAR-SECRET"}), encoding="utf-8")
        path = f.write_trace(tmp_path, [f.assistant("a1", usage_block=f.usage())])
        link = path.with_suffix(".meta.json")
        link.symlink_to(secret)
        trace = ClaudeCodeSource(read_sidecars=True).load([path], DEFAULTS)
        assert "SIDECAR-SECRET" not in trace.model_dump_json()


def test_r11_a_hostile_trace_never_leaks_a_payload_into_a_warning_detail(
    tmp_path: Path,
) -> None:
    """R10, R11: no payload from the corpus reaches a warning detail, ever."""
    records: list[dict[str, object]] = []
    for index, payload in enumerate(PAYLOADS):
        records.append(
            f.assistant(
                f"a{index}",
                timestamp=f"2026-09-09T10:{index // 60:02d}:{index % 60:02d}.000Z",
                message_id=f"m{index}",
                request_id=f"r{index}",
                content=[f.text_block(payload), {"type": payload[:20]}],
                usage_block=f.usage(input_tokens=1),
                **{f"extra{index}": payload},
            )
        )
    trace = load(records, tmp_path, name="agent-1")
    for warning in trace.warnings:
        for payload in PAYLOADS:
            assert payload not in warning.detail
    assert_well_formed(trace)
