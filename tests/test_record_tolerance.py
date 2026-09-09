"""R4: the tolerated/counted split, and the claim that the tolerated half cannot raise.

R4's requirement *is* the split. One half tolerates: an unknown record type, an
unknown content block, an unknown key, a known-but-ignored key — each skipped
and counted, never fatal, because the observed transcript format is
undocumented and unversioned and next week's Claude Code release will add a
field. The other half fails closed: a line that is not JSON, a record missing
``type``/``uuid``/``timestamp``, a non-RFC-3339 timestamp, a duplicate ``uuid``,
a ``message`` that is not an object.

The load-bearing claim is the negative one: nothing in the tolerated half may
raise. A pydantic ``ValidationError`` escaping the adapter would be an
unsanitized crash quoting hostile bytes, which is exactly what R11 forbids — so
the sweep at the end of this module drives every hostile value this suite knows
about through every tolerated position and asserts the only exception type that
may ever come out is a sanitized ``TraceError``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from swarm_observer.ingest.claude_code.mapper import ClaudeCodeSource
from swarm_observer.ingest.claude_code.records import (
    KNOWN_BLOCK_TYPES,
    KNOWN_IGNORED_KEYS,
    KNOWN_RECORD_TYPES,
    parse_record,
)
from swarm_observer.ingest.reader import JsonRecord
from swarm_observer.ingest.source import IngestLimits, TraceError, TraceParseError
from swarm_observer.model.trace import Trace

from . import factories as f

DEFAULTS = IngestLimits()

#: Values a hostile or simply newer transcript might put anywhere at all.
HOSTILE_VALUES: tuple[Any, ...] = (
    None,
    True,
    False,
    0,
    -1,
    -(2**63),
    2**70,
    "",
    "</script><script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "‮EVIL",
    "​​​",
    "\x00\x1b[31m",
    "../../etc/passwd",
    "{{7*7}}",
    "A" * 10_000,
    [],
    [1, "two", None],
    {},
    {"nested": {"deeper": [1, 2, 3]}},
    [{"type": "text"}],
)

#: Every record-level key the mapper reads, plus a few it does not.
TOLERATED_RECORD_KEYS = (
    "parentUuid",
    "agentId",
    "sessionId",
    "requestId",
    "subtype",
    "isSidechain",
    "isMeta",
    "isApiErrorMessage",
    "apiErrorStatus",
    "error",
    "cwd",
    "gitBranch",
    "version",
    "toolUseResult",
    "iterations",
    "diagnostics",
)

#: Every ``message``-level key the mapper reads.
TOLERATED_MESSAGE_KEYS = ("id", "model", "role", "content", "usage", "stop_reason")


def load(path: Path, **kwargs: Any) -> Trace:
    return ClaudeCodeSource(read_sidecars=False, **kwargs).load([path], DEFAULTS)


class TestToleratedAndCountedR4:
    """R4: unexpected shape degrades to a counted warning, never to an exit 2."""

    def test_r4_an_unknown_record_type_is_skipped_and_counted(self, tmp_path: Path) -> None:
        """R4: the type slug goes in ``detail``; the record produces no span."""
        record = {
            "type": "telemetry_beacon",
            "uuid": "x1",
            "timestamp": f.BASE_TIME,
            "agentId": "root",
        }
        trace = load(f.write_trace(tmp_path, [record, f.assistant("a1", usage_block=f.usage())]))
        warnings = {(w.code, w.detail): w.count for w in trace.warnings}
        assert warnings[("unknown_record_type", "telemetry_beacon")] == 1
        assert [span.kind for span in trace.spans] == ["model_call"]

    def test_r4_the_type_slug_in_detail_is_slugged_not_echoed(self, tmp_path: Path) -> None:
        """R4, R10: an attacker-chosen type cannot smuggle free text into a detail."""
        record = {
            "type": "</script><script>alert(1)</script>",
            "uuid": "x1",
            "timestamp": f.BASE_TIME,
        }
        trace = load(f.write_trace(tmp_path, [record]))
        detail = next(w.detail for w in trace.warnings if w.code == "unknown_record_type")
        assert "<" not in detail and ">" not in detail and " " not in detail
        assert len(detail) <= 40

    def test_r4_an_unknown_content_block_is_skipped_and_counted(self, tmp_path: Path) -> None:
        """R4: an unknown block type is counted as ``unknown_content_block``."""
        record = f.assistant(
            "a1",
            content=[{"type": "citation", "source": "x"}, f.text_block("kept")],
            usage_block=f.usage(),
        )
        trace = load(f.write_trace(tmp_path, [record]))
        warnings = {(w.code, w.detail): w.count for w in trace.warnings}
        assert warnings[("unknown_content_block", "citation")] == 1
        assert trace.spans[0].text_preview == "kept"

    def test_r4_a_malformed_content_block_is_counted_not_fatal(self, tmp_path: Path) -> None:
        """R4: a block that is not an object, or has no ``type``, is skipped."""
        record = f.assistant(
            "a1", content=["a bare string", {"no": "type"}, 42], usage_block=f.usage()
        )
        trace = load(f.write_trace(tmp_path, [record]))
        warnings = {(w.code, w.detail): w.count for w in trace.warnings}
        assert warnings[("unknown_content_block", "malformed")] == 3

    def test_r4_unknown_top_level_keys_are_counted_into_extras_dropped(
        self, tmp_path: Path
    ) -> None:
        """R4: three unknown keys give ``extras_dropped == 3`` and no content."""
        record = f.assistant(
            "a1",
            usage_block=f.usage(),
            futureField="SECRET-A",
            anotherNewThing="SECRET-B",
            thirdOne="SECRET-C",
        )
        trace = load(f.write_trace(tmp_path, [record]))
        assert trace.spans[0].extras_dropped == 3
        blob = trace.model_dump_json()
        for secret in ("SECRET-A", "SECRET-B", "SECRET-C"):
            assert secret not in blob
        assert "futureField" not in blob

    def test_r4_unknown_extra_key_detail_is_empty_by_design(self, tmp_path: Path) -> None:
        """R4, R10: the key *name* is attacker-chosen, so it never reaches a detail."""
        record = f.assistant("a1", usage_block=f.usage(), **{"<script>": 1})
        trace = load(f.write_trace(tmp_path, [record]))
        warning = next(w for w in trace.warnings if w.code == "unknown_extra_key")
        assert warning.detail == ""

    @pytest.mark.parametrize("key", sorted(KNOWN_IGNORED_KEYS))
    def test_r4_every_known_ignored_key_is_counted_by_name(self, tmp_path: Path, key: str) -> None:
        """R4: each pinned known-but-ignored key is counted once, under its own name."""
        record = f.assistant("a1", usage_block=f.usage(), **{key: {"anything": "here"}})
        trace = load(f.write_trace(tmp_path, [record], name=f"agent-{key}"))
        warnings = {(w.code, w.detail): w.count for w in trace.warnings}
        assert warnings[("known_ignored_key", key)] == 1
        assert trace.spans[0].extras_dropped == 0

    def test_r4_the_known_ignored_set_is_exactly_the_pinned_twelve(self) -> None:
        """R4: the ignored-key list is a pinned set, not a growing convenience."""
        assert (
            frozenset(
                {
                    "iterations",
                    "output_tokens_details",
                    "server_tool_use",
                    "diagnostics",
                    "stop_details",
                    "container",
                    "context_management",
                    "quotaLimits",
                    "rendered",
                    "apiBlockIndex",
                    "logicalParentUuid",
                    "compactMetadata",
                }
            )
            == KNOWN_IGNORED_KEYS
        )

    def test_r4_the_known_record_and_block_sets_are_pinned(self) -> None:
        """R4, R12: the mapper's vocabulary is closed and stated."""
        assert frozenset({"assistant", "user", "system", "attachment"}) == KNOWN_RECORD_TYPES
        assert frozenset({"text", "thinking", "tool_use", "tool_result"}) == KNOWN_BLOCK_TYPES

    def test_r4_nested_unknown_keys_are_warned_but_not_in_extras_dropped(
        self, tmp_path: Path
    ) -> None:
        """R4: ``extras_dropped`` counts record-level keys, per its definition."""
        record = f.assistant("a1", usage_block=f.usage())
        record["message"]["brandNewMessageKey"] = "x"  # type: ignore[index]
        record["message"]["usage"]["brandNewUsageKey"] = "y"  # type: ignore[index]
        trace = load(f.write_trace(tmp_path, [record]))
        assert trace.spans[0].extras_dropped == 0
        count = sum(w.count for w in trace.warnings if w.code == "unknown_extra_key")
        assert count == 2

    def test_r4_a_wrong_typed_usage_component_becomes_zero(self, tmp_path: Path) -> None:
        """R4: coercion to the neutral default, never a validation error."""
        record = f.assistant(
            "a1",
            usage_block={
                "input_tokens": "many",
                "output_tokens": -5,
                "cache_read_input_tokens": True,
                "cache_creation_input_tokens": None,
            },
        )
        usage = load(f.write_trace(tmp_path, [record])).spans[0].usage
        assert usage is not None
        assert usage.total == 0

    def test_r4_a_wrong_typed_string_field_becomes_absent(self, tmp_path: Path) -> None:
        """R4: a non-string where a string was expected is absence, not an error."""
        record = f.assistant("a1", usage_block=f.usage(), agent_id="root")
        record["agentId"] = {"not": "a string"}
        record["message"]["model"] = 12345  # type: ignore[index]
        trace = load(f.write_trace(tmp_path, [record]))
        assert trace.spans[0].agent_id == "root"
        assert trace.spans[0].model is None


class TestFatalHalfIsCheckedBeforePydanticR4:
    """R4: the fatal conditions raise a typed error, never a ``ValidationError``."""

    @pytest.mark.parametrize("field", ["type", "uuid", "timestamp"])
    def test_r4_a_missing_required_field_names_the_field_not_the_value(self, field: str) -> None:
        """R4: the message names a field, so trace bytes cannot reach stderr."""
        data = {"type": "user", "uuid": "u", "timestamp": f.BASE_TIME}
        data.pop(field)
        data["poison"] = "</script><script>alert(1)</script>"
        with pytest.raises(TraceParseError) as raised:
            parse_record(
                JsonRecord(file_index=0, line=1, data=data), source="agent-1.jsonl", index=0
            )
        assert raised.value.note == f"field {field}"
        assert "script" not in raised.value.cli_line

    @pytest.mark.parametrize("value", ["", None, 0, [], {}, True])
    def test_r4_an_empty_or_wrong_typed_required_field_is_fatal(self, value: object) -> None:
        """R4: ``type``/``uuid``/``timestamp`` must each be a non-empty string."""
        data = {"type": "user", "uuid": value, "timestamp": f.BASE_TIME}
        with pytest.raises(TraceParseError):
            parse_record(JsonRecord(file_index=0, line=1, data=data), source="a.jsonl", index=0)

    def test_r4_a_scalar_message_on_an_assistant_record_is_fatal(self) -> None:
        """R4: ``message`` present but not an object is fatal."""
        data = {"type": "assistant", "uuid": "u", "timestamp": f.BASE_TIME, "message": "hostile"}
        with pytest.raises(TraceParseError) as raised:
            parse_record(JsonRecord(file_index=0, line=1, data=data), source="a.jsonl", index=0)
        assert raised.value.code == "bad_message"
        assert "hostile" not in raised.value.cli_line

    def test_r4_a_scalar_message_on_another_record_type_is_tolerated(self, tmp_path: Path) -> None:
        """R4: the rule is written for ``assistant``/``user``; a system record is not one."""
        record = {
            "type": "system",
            "uuid": "s1",
            "timestamp": f.BASE_TIME,
            "message": "a string",
            "agentId": "root",
        }
        trace = load(f.write_trace(tmp_path, [record]))
        assert [span.kind for span in trace.spans] == ["system_event"]

    @pytest.mark.parametrize("value", ["NESTED-PAYLOAD", 0, True, [1], 1.5])
    def test_r4_a_wrong_typed_nested_object_is_absence_not_a_crash(
        self, tmp_path: Path, value: object
    ) -> None:
        """R4, R11: ``message``/``usage``/``cache_creation`` tolerate a wrong type.

        Review fix for BUG-4. The three nested-model fields had no
        before-validator, so a hostile value reached pydantic and the escaping
        ``ValidationError`` quoted the trace. Each position below is on the
        tolerated side of R4's split, so the only admissible outcome is that the
        value is dropped and the run continues.
        """
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                "system.message",
                {"type": "system", "uuid": "s1", "timestamp": f.BASE_TIME, "message": value},
            ),
            (
                "attachment.message",
                {"type": "attachment", "uuid": "t1", "timestamp": f.BASE_TIME, "message": value},
            ),
            (
                "message.usage",
                {
                    "type": "assistant",
                    "uuid": "a1",
                    "timestamp": f.BASE_TIME,
                    "requestId": "r1",
                    "message": {"id": "m1", "model": "m", "content": [], "usage": value},
                },
            ),
            (
                "usage.cache_creation",
                {
                    "type": "assistant",
                    "uuid": "a2",
                    "timestamp": f.BASE_TIME,
                    "requestId": "r2",
                    "message": {
                        "id": "m2",
                        "model": "m",
                        "content": [],
                        "usage": {"input_tokens": 5, "cache_creation": value},
                    },
                },
            ),
        ]
        for index, (label, record) in enumerate(cases):
            trace = load(f.write_trace(tmp_path, [record], name=f"agent-{index}"))
            assert trace.spans or label.startswith("attachment"), label
            if isinstance(value, str):
                assert value not in trace.model_dump_json(), label

    def test_r4_a_scalar_message_is_still_fatal_for_a_user_record(self, tmp_path: Path) -> None:
        """R4: the fatal half names ``assistant``/``user``, and ``user`` is not dropped.

        Review pin: the ``user`` arm of R4's ``bad_message`` rule had no test, so
        deleting it from :func:`parse_record` left the suite green.
        """
        record = {"type": "user", "uuid": "u1", "timestamp": f.BASE_TIME, "message": "hostile"}
        with pytest.raises(TraceParseError) as raised:
            load(f.write_trace(tmp_path, [record]))
        assert raised.value.code == "bad_message"
        assert "hostile" not in raised.value.cli_line

    def test_r4_a_duplicate_uuid_is_fatal_within_a_file_only(self, tmp_path: Path) -> None:
        """R4: "duplicate ``uuid`` within one file" — across files is legal."""
        first = f.write_trace(tmp_path, [f.assistant("shared")], name="agent-1")
        second = f.write_trace(
            tmp_path,
            [f.assistant("shared", message_id="msg-2", request_id="req-2")],
            name="agent-2",
        )
        trace = ClaudeCodeSource(read_sidecars=False).load([first, second], DEFAULTS)
        assert len(trace.spans) == 2

    def test_r4_tolerance_never_produces_a_silent_partial_result(self, tmp_path: Path) -> None:
        """R4: warnings are always present when something was tolerated."""
        records = [
            {"type": "mystery", "uuid": "x1", "timestamp": f.BASE_TIME},
            f.assistant("a1", usage_block=f.usage(), unknownKey=1),
        ]
        trace = load(f.write_trace(tmp_path, records))
        assert trace.warnings, "a tolerated oddity produced no warning at all"
        assert {w.code for w in trace.warnings} >= {"unknown_record_type", "unknown_extra_key"}


def _sweep(tmp_path: Path) -> list[tuple[str, str]]:
    """Drive every hostile value through every tolerated position.

    Returns one ``(position, exception type)`` pair per hostile input that
    escaped as something other than a sanitized :class:`TraceError` — which,
    per R4 and R11, should be nothing at all.
    """
    violations: list[tuple[str, str]] = []

    def drive(position: str, record: dict[str, Any], name: str) -> None:
        path = tmp_path / f"{name}.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        try:
            load(path)
        except TraceError:
            return  # the fatal half is allowed to fire, sanitized
        except Exception as exc:
            violations.append((position, type(exc).__name__))

    for key in TOLERATED_RECORD_KEYS:
        for index, value in enumerate(HOSTILE_VALUES):
            record = f.assistant("a1", usage_block=f.usage(), content=[f.text_block("t")])
            record[key] = value
            drive(f"record.{key}", record, f"agent-{key}-{index}")

    for key in TOLERATED_MESSAGE_KEYS:
        for index, value in enumerate(HOSTILE_VALUES):
            record = f.assistant("a1", usage_block=f.usage())
            record["message"][key] = value  # type: ignore[index]
            drive(f"message.{key}", record, f"agent-msg-{key}-{index}")

    block_keys = ("type", "text", "thinking", "id", "name", "input", "tool_use_id", "content")
    for block_key in block_keys:
        for index, value in enumerate(HOSTILE_VALUES):
            block: dict[str, Any] = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}
            block[block_key] = value
            record = f.assistant("a1", usage_block=f.usage(), content=[block])
            drive(f"block.{block_key}", record, f"agent-blk-{block_key}-{index}")

    for record_type in [*sorted(KNOWN_RECORD_TYPES), "unknown_kind"]:
        for index, value in enumerate(HOSTILE_VALUES):
            typed: dict[str, Any] = {
                "type": record_type,
                "uuid": "u1",
                "timestamp": f.BASE_TIME,
                "agentId": "root",
                "requestId": "r1",
                "message": {"id": "m1", "model": "m", "content": [], "usage": value},
            }
            drive(f"usage[{record_type}]", typed, f"agent-usage-{record_type}-{index}")

    return violations


#: The positions where the tolerated path is known to raise. The tester opened
#: this as a debt ledger for BUG-1 and BUG-4; both are fixed, so it is empty and
#: must stay empty — R4's tolerated half raising *anywhere* is a defect, not a
#: state to record. It is kept rather than deleted so a future adapter change
#: that reintroduces one has a named place to fail rather than a quiet
#: temptation to widen an assertion.
KNOWN_TOLERANCE_DEFECTS: frozenset[tuple[str, str]] = frozenset()


class TestToleratedPathCannotRaiseR4:
    """R4: the negative claim — a hostile record shape never crashes the adapter."""

    def test_r4_the_sweep_actually_covers_something(self) -> None:
        """R4: a sweep over an empty corpus would pass vacuously."""
        assert len(HOSTILE_VALUES) >= 20
        assert len(TOLERATED_RECORD_KEYS) >= 10
        assert len(TOLERATED_MESSAGE_KEYS) == 6

    def test_r4_no_new_position_in_the_tolerated_path_can_raise(self, tmp_path: Path) -> None:
        """R4: the sweep may only reproduce defects that are already written down."""
        observed = set(_sweep(tmp_path))
        assert observed <= KNOWN_TOLERANCE_DEFECTS, (
            f"the tolerated path raised somewhere new: {sorted(observed - KNOWN_TOLERANCE_DEFECTS)}"
        )

    def test_r4_the_tolerated_path_cannot_raise_at_all(self, tmp_path: Path) -> None:
        """R4: the requirement itself — nothing tolerated may raise, anywhere."""
        assert _sweep(tmp_path) == []

    def test_r4_the_defect_ledger_is_empty_and_stays_empty(self) -> None:
        """R4: the tolerated half raising anywhere is a defect, never a recorded state.

        The tester's version of this test asserted the ledger was still
        *reproducible*, which was right while BUG-1 and BUG-4 were open. Both are
        fixed, so the ledger's only correct content is nothing: re-opening it
        would let a future crash position be written down instead of fixed.
        """
        assert not KNOWN_TOLERANCE_DEFECTS, (
            "R4's tolerated half may not raise; fix the position rather than ledger it: "
            f"{sorted(KNOWN_TOLERANCE_DEFECTS)}"
        )

    def test_r4_an_over_long_tolerated_value_never_reaches_an_exception(
        self, tmp_path: Path
    ) -> None:
        """R4, R11: the security consequence of the sweep, asserted rather than argued.

        This test used to assert the *defect* (BUG-1): a 300-character model id
        escaped as a pydantic ``ValidationError`` whose message quoted the
        attacker's bytes, which is exactly what R11's "never a byte of file
        content" forbids. It now asserts the fixed behaviour, so a regression to
        the ellipsis off-by-one fails here with the reason spelled out.
        """
        record = f.assistant("a1", usage_block=f.usage(), model="PAYLOAD-" + "A" * 300)
        trace = load(f.write_trace(tmp_path, [record]))
        model = trace.spans[0].model
        assert model is not None
        assert len(model) == 200, "the field cap is a total budget, ellipsis included"
        assert model.endswith("…")
