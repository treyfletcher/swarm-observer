"""R10 and R12: the closed warning taxonomy and the span-kind mapping.

R10's taxonomy is only closed if every member of it is *reachable*. A twelve-code
enum where two codes can never be produced is a nine-code enum with two lies in
it, so the first class below drives each code from a trace shaped to produce it
and then asserts the union covers the enum exactly.

R12 pins one mapping rule per input kind. Each rule gets its own test, including
the two the coder flagged for review: an ``attachment`` record produces no span
but R10 has no attachment code (A-a3), and an ``isApiErrorMessage`` record
produces a ``model_call`` with no usage and a slugged error code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm_observer.ingest.claude_code.mapper import SYNTHETIC_MODEL, ClaudeCodeSource, WarningLog
from swarm_observer.ingest.source import IngestLimits
from swarm_observer.model.trace import PARSE_WARNING_CODES, SPAN_KINDS, Trace

from . import factories as f

DEFAULTS = IngestLimits()


def load(records: list[dict[str, object]], tmp_path: Path, name: str = "agent-1") -> Trace:
    return ClaudeCodeSource(read_sidecars=False).load(
        [f.write_trace(tmp_path, records, name=name)], DEFAULTS
    )


def codes(trace: Trace) -> set[str]:
    return {warning.code for warning in trace.warnings}


def counts(trace: Trace) -> dict[str, int]:
    result: dict[str, int] = {}
    for warning in trace.warnings:
        result[warning.code] = result.get(warning.code, 0) + warning.count
    return result


# --- one trace shape per warning code ----------------------------------------


def records_for(code: str) -> list[dict[str, object]]:
    """A minimal record set that must produce ``code``."""
    if code == "unknown_record_type":
        return [{"type": "telemetry", "uuid": "x1", "timestamp": f.BASE_TIME}]
    if code == "unknown_content_block":
        return [f.assistant("a1", content=[{"type": "citation"}], usage_block=f.usage())]
    if code == "known_ignored_key":
        return [f.assistant("a1", usage_block=f.usage(), iterations=3)]
    if code == "unknown_extra_key":
        return [f.assistant("a1", usage_block=f.usage(), somethingBrandNew=1)]
    if code == "timestamp_out_of_order":
        return [
            f.user("u1", timestamp="2026-09-09T10:00:05.000Z"),
            f.user("u2", timestamp="2026-09-09T10:00:01.000Z"),
        ]
    if code == "negative_duration":
        shared = {"message_id": "m", "request_id": "r"}
        return [
            f.assistant(
                "a1", timestamp="2026-09-09T10:00:09.000Z", usage_block=f.usage(), **shared
            ),
            f.assistant(
                "a2", timestamp="2026-09-09T10:00:01.000Z", usage_block=f.usage(), **shared
            ),
        ]
    if code == "orphan_tool_result":
        return [f.user("u1", content=[f.tool_result_block("never-issued", "result")])]
    if code == "dangling_tool_use":
        return [
            f.assistant(
                "a1",
                content=[f.tool_use_block("t1", "Bash", {"cmd": "ls"})],
                usage_block=f.usage(),
            )
        ]
    if code == "missing_usage":
        return [f.assistant("a1", content=[f.text_block("no usage block")], usage_block=None)]
    if code == "synthetic_model":
        return [f.assistant("a1", model=SYNTHETIC_MODEL, usage_block=f.usage())]
    if code == "compaction_boundary":
        return [f.system("s1", subtype="compact_boundary")]
    if code == "api_error_record":
        return [f.api_error("e1")]
    raise AssertionError(f"no trace shape defined for the warning code {code!r}")


class TestWarningTaxonomyIsReachableR10:
    """R10: every code in the closed enum is produced by some real trace."""

    @pytest.mark.parametrize("code", PARSE_WARNING_CODES)
    def test_r10_every_code_is_reachable(self, tmp_path: Path, code: str) -> None:
        """R10: a code no input can produce is a lie in the taxonomy."""
        trace = load(records_for(code), tmp_path, name=f"agent-{code}")
        assert code in codes(trace), f"{code} was not produced by its own trace shape"

    def test_r10_the_twelve_shapes_between_them_cover_the_enum(self, tmp_path: Path) -> None:
        """R10: the union of the shapes is exactly the enum, in both directions."""
        seen: set[str] = set()
        for index, code in enumerate(PARSE_WARNING_CODES):
            seen |= codes(load(records_for(code), tmp_path, name=f"agent-cover-{index}"))
        assert seen == set(PARSE_WARNING_CODES)

    def test_r10_warnings_are_aggregated_and_sorted(self, tmp_path: Path) -> None:
        """R10: one entry per ``(code, detail)`` with a count, sorted."""
        records = [
            {"type": "telemetry", "uuid": "x1", "timestamp": f.BASE_TIME},
            {"type": "telemetry", "uuid": "x2", "timestamp": f.BASE_TIME},
            {"type": "beacon", "uuid": "x3", "timestamp": f.BASE_TIME},
        ]
        trace = load(records, tmp_path)
        keys = [(w.code, w.detail) for w in trace.warnings]
        assert keys == sorted(keys)
        assert len(keys) == len(set(keys))
        by_detail = {w.detail: w.count for w in trace.warnings if w.code == "unknown_record_type"}
        assert by_detail == {"telemetry": 2, "beacon": 1}

    @pytest.mark.parametrize("code", PARSE_WARNING_CODES)
    def test_r10_no_detail_ever_carries_trace_free_text(self, tmp_path: Path, code: str) -> None:
        """R10: ``detail`` is an enumerated slug, a type slug or a number."""
        trace = load(records_for(code), tmp_path, name=f"agent-detail-{code}")
        for warning in trace.warnings:
            assert " " not in warning.detail
            assert all(char.isprintable() for char in warning.detail)
            assert len(warning.detail) <= 64

    def test_r10_a_hostile_record_type_cannot_smuggle_text_into_a_detail(
        self, tmp_path: Path
    ) -> None:
        """R10: the slug alphabet is the enforcement, not a convention.

        The slug keeps alphanumerics, so the *word* ``script`` survives; what
        cannot survive is anything that could act — a bracket, a quote, a space,
        a control character — or a credential-shaped run, which the lowercasing
        and the 40-character cap between them destroy.
        """
        hostile = (
            "</script><script>alert(1)</script> AKIA0000000000000000 sk-ant-aaaaaaaaaaaaaaaaaaaaaa"
        )
        trace = load([{"type": hostile, "uuid": "x1", "timestamp": f.BASE_TIME}], tmp_path)
        detail = next(w.detail for w in trace.warnings if w.code == "unknown_record_type")
        assert set(detail) <= set("abcdefghijklmnopqrstuvwxyz0123456789_.:-"), (
            f"non-slug characters survived: {detail!r}"
        )
        assert len(detail) <= 40
        blob = trace.model_dump_json()
        assert "AKIA0000000000000000" not in blob
        assert "sk-ant-aaaaaaaaaaaaaaaaaaaaaa" not in blob
        assert "<" not in blob and ">" not in blob

    def test_r10_the_warning_log_aggregates_and_ignores_empty_counts(self) -> None:
        """R10: the aggregation helper is the single place counts are combined."""
        log = WarningLog()
        log.add("missing_usage")
        log.add("missing_usage", count=4)
        log.add("missing_usage", count=0)
        log.add("missing_usage", count=-3)
        log.add("api_error_record", "", 2)
        assert log.total("missing_usage") == 5
        entries = log.to_tuple()
        assert [(w.code, w.count) for w in entries] == [
            ("api_error_record", 2),
            ("missing_usage", 5),
        ]


class TestSpanKindMappingR12:
    """R12: one mapping rule per input kind, each asserted on its own."""

    def test_r12_the_four_kinds_are_the_only_kinds(self, tmp_path: Path) -> None:
        """R12: the mapper produces nothing outside the pinned span kinds."""
        records = [
            f.user("u1"),
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[f.tool_use_block("t1", "Bash", {})],
                usage_block=f.usage(),
            ),
            f.user(
                "u2",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.tool_result_block("t1", "ok")],
            ),
            f.system("s1", timestamp="2026-09-09T10:00:03.000Z"),
            f.attachment("at1", timestamp="2026-09-09T10:00:04.000Z"),
        ]
        trace = load(records, tmp_path)
        assert [span.kind for span in trace.spans] == [
            "user_message",
            "model_call",
            "tool_call",
            "system_event",
        ]
        assert {span.kind for span in trace.spans} <= set(SPAN_KINDS)

    def test_r12_one_tool_call_span_per_tool_use_block(self, tmp_path: Path) -> None:
        """R12: two ``tool_use`` blocks in one response make two ``tool_call`` spans."""
        record = f.assistant(
            "a1",
            content=[
                f.tool_use_block("t1", "Bash", {"cmd": "ls"}),
                f.tool_use_block("t2", "Read", {"path": "x"}),
            ],
            usage_block=f.usage(),
        )
        trace = load([record], tmp_path)
        tools = [span for span in trace.spans if span.kind == "tool_call"]
        assert [tool.tool_name for tool in tools] == ["Bash", "Read"]
        assert all(tool.parent_span_id == trace.spans[0].span_id for tool in tools)

    @pytest.mark.parametrize(("is_error", "expected"), [(False, "ok"), (True, "error")])
    def test_r12_the_result_status_comes_from_the_matching_block(
        self, tmp_path: Path, is_error: bool, expected: str
    ) -> None:
        """R12: status and preview come from the ``tool_result`` with that id."""
        records = [
            f.assistant(
                "a1",
                content=[f.tool_use_block("t1", "Bash", {"cmd": "ls"})],
                usage_block=f.usage(),
            ),
            f.user(
                "u1",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.tool_result_block("t1", "the result text", is_error=is_error)],
            ),
        ]
        tool = next(span for span in load(records, tmp_path).spans if span.kind == "tool_call")
        assert tool.tool_result_status == expected
        assert tool.tool_result_preview == "the result text"

    def test_r12_a_result_anywhere_in_the_trace_matches(self, tmp_path: Path) -> None:
        """R12: "the ``tool_result`` block with the matching id anywhere in the trace"."""
        records = [
            f.assistant(
                "a1",
                content=[f.tool_use_block("t1", "Bash", {})],
                usage_block=f.usage(),
            ),
            f.user("u1", timestamp="2026-09-09T10:00:02.000Z", content="an unrelated turn"),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:03.000Z",
                message_id="m2",
                request_id="r2",
                usage_block=f.usage(),
            ),
            f.user(
                "u2",
                timestamp="2026-09-09T10:00:04.000Z",
                content=[f.tool_result_block("t1", "late result")],
            ),
        ]
        tool = next(span for span in load(records, tmp_path).spans if span.kind == "tool_call")
        assert tool.tool_result_status == "ok"
        assert tool.tool_result_preview == "late result"

    def test_r12_no_result_means_missing(self, tmp_path: Path) -> None:
        """R12: ``"missing"`` when no matching result exists."""
        record = f.assistant(
            "a1", content=[f.tool_use_block("t1", "Bash", {})], usage_block=f.usage()
        )
        tool = next(span for span in load([record], tmp_path).spans if span.kind == "tool_call")
        assert tool.tool_result_status == "missing"
        assert tool.tool_result_preview == ""
        assert tool.end is None

    def test_r12_a_string_content_user_record_makes_a_span(self, tmp_path: Path) -> None:
        """R12: "a ``user`` record whose message content is a string"."""
        trace = load([f.user("u1", content="a human turn")], tmp_path)
        assert [span.kind for span in trace.spans] == ["user_message"]
        assert trace.spans[0].text_preview == "a human turn"

    def test_r12_a_user_record_with_a_non_tool_result_block_makes_a_span(
        self, tmp_path: Path
    ) -> None:
        """R12: "or contains a non-``tool_result`` block"."""
        trace = load([f.user("u1", content=[f.text_block("typed")])], tmp_path)
        assert [span.kind for span in trace.spans] == ["user_message"]

    def test_r12_a_pure_tool_result_user_record_makes_no_span(self, tmp_path: Path) -> None:
        """R12: a tool-result carrier is not a user turn."""
        records = [
            f.assistant("a1", content=[f.tool_use_block("t1", "Bash", {})], usage_block=f.usage()),
            f.user(
                "u1",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.tool_result_block("t1", "out")],
            ),
        ]
        assert [span.kind for span in load(records, tmp_path).spans] == ["model_call", "tool_call"]

    def test_r12_a_mixed_user_record_makes_a_span_and_still_supplies_the_result(
        self, tmp_path: Path
    ) -> None:
        """R12: "contains a non-``tool_result`` block" is an *or*, not an *only*."""
        records = [
            f.assistant("a1", content=[f.tool_use_block("t1", "Bash", {})], usage_block=f.usage()),
            f.user(
                "u1",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.tool_result_block("t1", "out"), f.text_block("and a comment")],
            ),
        ]
        trace = load(records, tmp_path)
        assert [span.kind for span in trace.spans] == ["model_call", "tool_call", "user_message"]
        assert trace.spans[1].tool_result_status == "ok"

    def test_r12_one_system_event_per_system_record(self, tmp_path: Path) -> None:
        """R12: a system record always makes a span."""
        trace = load(
            [f.system("s1"), f.system("s2", timestamp="2026-09-09T10:00:01.000Z")], tmp_path
        )
        assert [span.kind for span in trace.spans] == ["system_event", "system_event"]

    def test_r12_a_compact_boundary_carries_the_pinned_error_code(self, tmp_path: Path) -> None:
        """R12: ``error.code == "compact_boundary"`` for that subtype."""
        trace = load([f.system("s1", subtype="compact_boundary")], tmp_path)
        span = trace.spans[0]
        assert span.error is not None
        assert span.error.code == "compact_boundary"
        assert span.error.detail == ""
        assert counts(trace)["compaction_boundary"] == 1

    def test_r12_an_ordinary_system_record_has_no_error(self, tmp_path: Path) -> None:
        """R12: only the compaction subtype is marked."""
        assert load([f.system("s1", subtype="hook_result")], tmp_path).spans[0].error is None

    def test_r12_an_attachment_produces_no_span_and_is_counted(self, tmp_path: Path) -> None:
        """R12, A-a3: attachments are counted under ``known_ignored_key``.

        The coder flagged this for review and it is worth restating: R12 says an
        attachment record is "counted", but R10's closed enum has no attachment
        code, so the count lands in the enum's only "seen and deliberately
        ignored" slot. This test pins the resolution rather than the intent, so a
        later R10 amendment shows up as a test change.
        """
        trace = load(
            [f.attachment("at1"), f.attachment("at2", timestamp="2026-09-09T10:00:01.000Z")],
            tmp_path,
        )
        assert trace.spans == ()
        by_key = {(w.code, w.detail): w.count for w in trace.warnings}
        assert by_key[("known_ignored_key", "attachment")] == 2

    def test_r12_an_api_error_record_makes_an_unbillable_model_call(self, tmp_path: Path) -> None:
        """R12: ``usage = None``, model as recorded, a slugged error code."""
        trace = load([f.api_error("e1", error="rate_limit_error", status="429 Too Many")], tmp_path)
        span = trace.spans[0]
        assert span.kind == "model_call"
        assert span.usage is None
        assert span.model == SYNTHETIC_MODEL
        assert span.error is not None
        assert span.error.code == "rate_limit_error"
        assert span.error.detail == "429 Too Many"
        assert counts(trace)["api_error_record"] == 1
        assert counts(trace)["synthetic_model"] == 1

    @pytest.mark.parametrize(
        ("recorded", "expected"),
        [
            ("Rate Limit Error!", "rate_limit_error_"),
            ("</script>", "__script_"),
            ("", "unknown"),
            ("A" * 100, "a" * 40),
        ],
    )
    def test_r12_the_error_code_is_slugged_lowercased_and_capped(
        self, tmp_path: Path, recorded: str, expected: str
    ) -> None:
        """R12: "lowercased, non-slug characters replaced with ``_``, capped at 40"."""
        trace = load([f.api_error("e1", error=recorded)], tmp_path, name=f"agent-{len(recorded)}")
        span = trace.spans[0]
        assert span.error is not None
        assert span.error.code == expected

    def test_r12_a_non_string_error_slug_degrades_to_unknown(self, tmp_path: Path) -> None:
        """R12: a hostile ``error`` value cannot produce a non-slug code."""
        record = f.api_error("e1")
        record["error"] = {"nested": "object"}
        trace = load([record], tmp_path)
        span = trace.spans[0]
        assert span.error is not None
        assert span.error.code == "unknown"

    def test_r12_a_model_call_without_usage_is_counted_not_dropped(self, tmp_path: Path) -> None:
        """R12, R10: a response with no usage block still makes a span."""
        trace = load([f.assistant("a1", content=[f.text_block("x")], usage_block=None)], tmp_path)
        assert trace.spans[0].kind == "model_call"
        assert trace.spans[0].usage is None
        assert counts(trace)["missing_usage"] == 1

    def test_r12_the_cache_creation_breakdown_is_mapped_to_the_two_buckets(
        self, tmp_path: Path
    ) -> None:
        """R12, R2: the 5m/1h breakdown becomes the two pinned components."""
        record = f.assistant("a1", usage_block=f.usage(cache_5m=100, cache_1h=7))
        usage = load([record], tmp_path).spans[0].usage
        assert usage is not None
        assert (usage.cache_creation_5m_tokens, usage.cache_creation_1h_tokens) == (100, 7)

    def test_r12_a_missing_breakdown_charges_the_whole_amount_at_five_minutes(
        self, tmp_path: Path
    ) -> None:
        """R12: older transcript versions have no breakdown, and that is expected."""
        record = f.assistant(
            "a1",
            usage_block={"input_tokens": 1, "cache_creation_input_tokens": 500},
        )
        trace = load([record], tmp_path)
        usage = trace.spans[0].usage
        assert usage is not None
        assert (usage.cache_creation_5m_tokens, usage.cache_creation_1h_tokens) == (500, 0)
        assert "missing_usage" not in codes(trace)

    def test_r12_an_orphan_tool_result_is_counted(self, tmp_path: Path) -> None:
        """R12, R10: a result whose ``tool_use_id`` matches nothing is counted."""
        trace = load([f.user("u1", content=[f.tool_result_block("ghost", "out")])], tmp_path)
        assert counts(trace)["orphan_tool_result"] == 1

    def test_r12_a_dangling_final_tool_use_is_counted(self, tmp_path: Path) -> None:
        """R12, R10: the truncation carve-out is counted at parse time."""
        records = [
            f.assistant("a1", content=[f.tool_use_block("t1", "Bash", {})], usage_block=f.usage()),
        ]
        trace = load(records, tmp_path)
        assert counts(trace)["dangling_tool_use"] == 1

    def test_r12_a_resolved_final_tool_use_is_not_dangling(self, tmp_path: Path) -> None:
        """R12, R10: the carve-out is "no result", not "the last span is a tool call".

        Review pin. Dropping the ``tool_result_status == "missing"`` half of the
        condition left the suite green: every existing case had a final tool call
        that was also unresolved, so a guard that fired on *any* trailing tool
        call could not be told apart from the right one.
        """
        records = [
            f.assistant("a1", content=[f.tool_use_block("t1", "Bash", {})], usage_block=f.usage()),
            f.user(
                "u1",
                timestamp="2026-09-09T10:00:05.000Z",
                content=[f.tool_result_block("t1", "done")],
            ),
        ]
        trace = load(records, tmp_path)
        last = trace.spans[-1]
        assert last.kind == "tool_call" and last.tool_result_status == "ok"
        assert "dangling_tool_use" not in codes(trace)

    def test_r12_a_mid_trace_unmatched_tool_use_is_not_dangling(self, tmp_path: Path) -> None:
        """R12: only the agent's highest-``seq`` span qualifies."""
        records = [
            f.assistant("a1", content=[f.tool_use_block("t1", "Bash", {})], usage_block=f.usage()),
            f.user("u1", timestamp="2026-09-09T10:00:05.000Z", content="a later turn"),
        ]
        trace = load(records, tmp_path)
        assert "dangling_tool_use" not in codes(trace)


class TestAgentRunsR12:
    """R12, R2: the per-agent view the timeline and the detectors read."""

    def test_r12_an_agent_run_exists_for_every_agent_with_a_span(self, tmp_path: Path) -> None:
        """R2: one ``AgentRun`` per agent, indexed by first appearance."""
        records = [
            f.user("u1", agent_id="root"),
            f.user("u2", agent_id="sub-1", timestamp="2026-09-09T10:00:01.000Z"),
            f.user("u3", agent_id="root", timestamp="2026-09-09T10:00:02.000Z"),
        ]
        trace = load(records, tmp_path)
        assert [(a.agent_id, a.agent_index) for a in trace.agents] == [("root", 0), ("sub-1", 1)]
        assert trace.agents[0].span_seqs == (0, 2)
        assert trace.agents[1].span_seqs == (1,)

    def test_r12_agent_timing_spans_its_own_spans(self, tmp_path: Path) -> None:
        """R2: ``start``/``end`` bound the agent's spans, not the whole trace."""
        records = [
            f.user("u1", agent_id="root", timestamp="2026-09-09T10:00:00.000Z"),
            f.user("u2", agent_id="sub", timestamp="2026-09-09T10:00:05.000Z"),
            f.user("u3", agent_id="sub", timestamp="2026-09-09T10:00:09.000Z"),
        ]
        trace = load(records, tmp_path)
        sub = trace.agents[1]
        assert sub.start is not None and sub.end is not None
        assert (sub.end - sub.start).total_seconds() == 4

    def test_r12_every_span_seq_appears_in_exactly_one_agent_run(self, tmp_path: Path) -> None:
        """R2: the per-agent view partitions the spans, losing none."""
        records = [
            f.user("u1", agent_id="root"),
            f.assistant(
                "a1",
                agent_id="sub",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[f.tool_use_block("t1", "Bash", {})],
                usage_block=f.usage(),
            ),
            f.system("s1", agent_id="root", timestamp="2026-09-09T10:00:02.000Z"),
        ]
        trace = load(records, tmp_path)
        collected = [seq for agent in trace.agents for seq in agent.span_seqs]
        assert sorted(collected) == [span.seq for span in trace.spans]
        assert len(collected) == len(set(collected))
