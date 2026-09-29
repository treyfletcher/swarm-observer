"""R9: the stream-fragment collapse, pinned on both columns.

This is the requirement that decides whether every dollar figure in the product
is right. A Claude Code transcript writes one JSONL record per streamed content
block, and every fragment of one API response repeats that response's whole
``usage`` object. Summing per record therefore counts one response's cached
input three or four times, the run stays green, the report stays well-formed,
and every number is half again too big.

Two things follow for a test suite, and both are done here.

First, **pin both columns**. A test that pins only the collapsed totals passes
just as happily against a naive implementation fed a single-fragment fixture.
The naive total is what makes a regression loud, so ``collapse_stats`` is
asserted on ``naive_usage`` *and* ``collapsed_usage``.

Second, **pin the ratios the spec measured on real transcripts, not only the
fixture's**. The spec's constants (a 55.4% cache-read inflation, 2.5% on
output, 2,706 assistant records collapsing to 1,678 model calls) come from a
corpus that cannot be committed — it is the team's own prompts and tool output
(A5). :func:`real_transcript_paths` is the opt-in seam: it reads a directory
named by ``SWARM_OBSERVER_REAL_TRANSCRIPTS`` and is empty otherwise, so CI never
depends on data it does not have. The opt-in test still *runs* in every
configuration — R49 treats a skip as a test that did not run — and asserts the
gate itself when the corpus is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from swarm_observer.ingest.claude_code.mapper import ClaudeCodeSource, CollapseStats
from swarm_observer.ingest.source import IngestLimits
from swarm_observer.model.trace import TokenUsage

from . import factories as f

DEFAULTS = IngestLimits()
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mapper" / "stream_fragments.jsonl"

#: The environment variable that opts a run in to the real-transcript check.
REAL_TRANSCRIPTS_ENV = "SWARM_OBSERVER_REAL_TRANSCRIPTS"

# --- the fixture's pinned columns (both of them) ------------------------------

FIXTURE_NAIVE = TokenUsage(
    input_tokens=68,
    output_tokens=585,
    cache_read_input_tokens=836_844,
    cache_creation_5m_tokens=72_334,
    cache_creation_1h_tokens=900,
)
FIXTURE_COLLAPSED = TokenUsage(
    input_tokens=38,
    output_tokens=567,
    cache_read_input_tokens=482_211,
    cache_creation_5m_tokens=18_421,
    cache_creation_1h_tokens=900,
)
FIXTURE_ASSISTANT_RECORDS = 9
FIXTURE_MODEL_CALLS = 6

# --- the spec's real-corpus constants -----------------------------------------

#: The percentages the approved spec pins from the real 11-file corpus. The
#: absolute token totals are deliberately not pinned: the corpus is a live
#: session's transcript set that grows between measurements, while these ratios
#: are properties of the collapse and were reproduced to two decimal places.
SPEC_CACHE_READ_INFLATION_PCT = 55.4
SPEC_OUTPUT_INFLATION_PCT = 2.5
SPEC_ASSISTANT_RECORDS = 2_706
SPEC_MODEL_CALLS = 1_678
#: How far a re-measurement may drift before the reconciliation is in doubt.
INFLATION_TOLERANCE_PCT = 1.5
RATIO_TOLERANCE = 0.05

#: The real corpus's group-size histogram, as measured by the tester: how many
#: collapsed responses were written as 1, 2, ... fragments. Checked in because
#: spec flag S5's answer is "pin the ratio, and record the shape it came from" —
#: an absolute token total from a growing corpus can never be re-measured, but a
#: distribution can be reproduced synthetically, and the collapse ratio is a
#: function of exactly this distribution.
REAL_GROUP_SIZE_HISTOGRAM: dict[int, int] = {1: 896, 2: 578, 3: 189, 4: 21, 5: 3, 6: 1}

#: The per-fragment cache-read figure the synthetic corpus repeats. Its value
#: does not matter; that every fragment of one response repeats it is the whole
#: point of R9.
SYNTHETIC_CACHE_READ = 118_211


def real_transcript_paths() -> tuple[Path, ...]:
    """The opt-in real-transcript corpus, or ``()`` when none is configured.

    Real Claude Code transcripts are never committed (A5): they contain the
    team's own prompts, file contents and credential-adjacent tool output. This
    reads a directory named by the environment instead, so the check is
    available to a human on a machine that has one and absent everywhere else.
    """
    configured = os.environ.get(REAL_TRANSCRIPTS_ENV)
    if not configured:
        return ()
    directory = Path(configured)
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.glob("agent-*.jsonl")))


def inflation_pct(naive: int, collapsed: int) -> float:
    """How much naive summation overstates the collapsed total, as a percentage."""
    if collapsed == 0:
        return 0.0
    return (naive - collapsed) / collapsed * 100.0


def stats_for(paths: list[Path]) -> CollapseStats:
    return ClaudeCodeSource(read_sidecars=False).collapse_stats(paths, IngestLimits(max_files=256))


def load(paths: list[Path], **kwargs: object):  # type: ignore[no-untyped-def]
    return ClaudeCodeSource(read_sidecars=False, **kwargs).load(paths, DEFAULTS)  # type: ignore[arg-type]


class TestCollapseMechanicsR9:
    """R9: the collapse rules themselves, one case per clause."""

    def test_r9_four_fragments_of_one_response_become_one_model_call(self, tmp_path: Path) -> None:
        """R9: one API response written as four fragments is one ``model_call``."""
        shared = dict(message_id="msg-1", request_id="req-1", session_id="s1", agent_id="root")
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[f.text_block("part one")],
                usage_block=f.usage(input_tokens=10, output_tokens=6, cache_read=118_211),
                **shared,
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.text_block("part two")],
                usage_block=f.usage(input_tokens=10, output_tokens=6, cache_read=118_211),
                **shared,
            ),
            f.assistant(
                "a3",
                timestamp="2026-09-09T10:00:03.000Z",
                content=[f.text_block("part three")],
                usage_block=f.usage(input_tokens=10, output_tokens=6, cache_read=118_211),
                **shared,
            ),
            f.assistant(
                "a4",
                timestamp="2026-09-09T10:00:04.000Z",
                content=[f.tool_use_block("t1", "Bash", {"cmd": "ls"})],
                usage_block=f.usage(input_tokens=10, output_tokens=483, cache_read=118_211),
                stop_reason="tool_use",
                **shared,
            ),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        calls = [span for span in trace.spans if span.kind == "model_call"]
        assert len(calls) == 1
        usage = calls[0].usage
        assert usage is not None
        assert usage.output_tokens == 483, "output must come from the terminal fragment"
        assert usage.cache_read_input_tokens == 118_211, "cache read must not be summed"
        assert usage.input_tokens == 10
        assert calls[0].stop_reason == "tool_use"

    def test_r9_start_and_end_span_the_whole_group(self, tmp_path: Path) -> None:
        """R9: ``start`` is the first fragment's time, ``end`` the last."""
        shared = dict(message_id="m", request_id="r")
        records = [
            f.assistant(
                "a1", timestamp="2026-09-09T10:00:01.000Z", usage_block=f.usage(), **shared
            ),
            f.assistant(
                "a2", timestamp="2026-09-09T10:00:09.000Z", usage_block=f.usage(), **shared
            ),
        ]
        call = load([f.write_trace(tmp_path, records)]).spans[0]
        assert call.start is not None and call.end is not None
        assert (call.end - call.start).total_seconds() == 8

    def test_r9_fragments_with_differing_usage_take_the_last(self, tmp_path: Path) -> None:
        """R9: ``usage`` is taken from the last fragment — not summed, not merged."""
        shared = dict(message_id="m", request_id="r")
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                usage_block=f.usage(input_tokens=1, cache_read=100, cache_5m=7),
                **shared,
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                usage_block=f.usage(input_tokens=2, cache_read=200, cache_1h=9),
                **shared,
            ),
        ]
        usage = load([f.write_trace(tmp_path, records)]).spans[0].usage
        assert usage is not None
        assert usage.input_tokens == 2
        assert usage.cache_read_input_tokens == 200
        assert usage.cache_creation_5m_tokens == 0, "an earlier fragment's value must not merge"
        assert usage.cache_creation_1h_tokens == 9

    def test_r9_a_single_fragment_message_is_its_own_model_call(self, tmp_path: Path) -> None:
        """R9: collapse must be a no-op on a response written as one record."""
        record = f.assistant(
            "a1", content=[f.text_block("done")], usage_block=f.usage(output_tokens=5)
        )
        trace = load([f.write_trace(tmp_path, [record])])
        calls = [span for span in trace.spans if span.kind == "model_call"]
        assert len(calls) == 1
        assert calls[0].usage is not None and calls[0].usage.output_tokens == 5

    def test_r9_distinct_responses_are_never_merged(self, tmp_path: Path) -> None:
        """R9: the boundary — a different ``message.id`` is a different call."""
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                message_id="msg-1",
                request_id="req-1",
                usage_block=f.usage(output_tokens=10),
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                message_id="msg-2",
                request_id="req-1",
                usage_block=f.usage(output_tokens=20),
            ),
        ]
        calls = [
            s for s in load([f.write_trace(tmp_path, records)]).spans if s.kind == "model_call"
        ]
        assert len(calls) == 2

    @pytest.mark.parametrize(
        ("field", "value"),
        [("session_id", "other-session"), ("agent_id", "other-agent"), ("request_id", "req-2")],
    )
    def test_r9_every_part_of_the_group_key_separates_calls(
        self, tmp_path: Path, field: str, value: str
    ) -> None:
        """R9: the key is ``(sessionId, agentId, message.id, requestId)`` — all four."""
        first = f.assistant("a1", timestamp="2026-09-09T10:00:01.000Z", usage_block=f.usage())
        second_kwargs: dict[str, object] = {
            "timestamp": "2026-09-09T10:00:02.000Z",
            "usage_block": f.usage(),
            field: value,
        }
        second = f.assistant("a2", **second_kwargs)  # type: ignore[arg-type]
        calls = [
            s
            for s in load([f.write_trace(tmp_path, [first, second])]).spans
            if s.kind == "model_call"
        ]
        assert len(calls) == 2

    def test_r9_a_record_with_no_request_id_and_no_message_id_is_its_own_group(
        self, tmp_path: Path
    ) -> None:
        """R9: there is nothing to join such a record to, so it never merges."""
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                message_id=None,
                request_id=None,
                usage_block=f.usage(output_tokens=1),
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                message_id=None,
                request_id=None,
                usage_block=f.usage(output_tokens=2),
            ),
        ]
        calls = [
            s for s in load([f.write_trace(tmp_path, records)]).spans if s.kind == "model_call"
        ]
        assert len(calls) == 2
        assert [c.usage.output_tokens for c in calls if c.usage] == [1, 2]

    def test_r9_ungrouped_records_in_two_files_with_one_uuid_stay_two_calls(
        self, tmp_path: Path
    ) -> None:
        """R9: the solo-group identity must be unique across the whole input set.

        Review finding, and the sharpest one in this increment: R4 makes a
        duplicate ``uuid`` fatal *within one file* only — correctly, since agent
        files are written independently — but the solo group key was the uuid.
        Two ungrouped model calls in two files that shared a uuid therefore
        collapsed into one span, and "usage from the last fragment" then threw
        the first call's tokens away. Silent, and it moves a dollar figure down.
        """
        shared = {"message_id": None, "request_id": None, "agent_id": "a1"}
        first = f.write_trace(
            tmp_path,
            [
                f.assistant(
                    "same-uuid",
                    timestamp="2026-09-09T10:00:01.000Z",
                    usage_block=f.usage(output_tokens=100, cache_read=7),
                    **shared,  # type: ignore[arg-type]
                )
            ],
            name="agent-1",
        )
        second = f.write_trace(
            tmp_path,
            [
                f.assistant(
                    "same-uuid",
                    timestamp="2026-09-09T10:00:02.000Z",
                    usage_block=f.usage(output_tokens=200, cache_read=9),
                    **shared,  # type: ignore[arg-type]
                )
            ],
            name="agent-2",
        )
        trace = load([first, second])
        calls = [span for span in trace.spans if span.kind == "model_call"]
        assert len(calls) == 2
        assert sorted(call.usage.output_tokens for call in calls if call.usage) == [100, 200]

        stats = stats_for([first, second])
        assert stats.model_calls == 2
        assert stats.collapsed_usage.output_tokens == 300
        assert stats.collapsed_usage == stats.naive_usage, (
            "nothing here is a fragment, so the two columns must agree"
        )

    @pytest.mark.parametrize("present", ["message_id", "request_id"])
    def test_r9_one_half_of_the_join_key_is_enough_to_group(
        self, tmp_path: Path, present: str
    ) -> None:
        """R9: "no requestId **and** no message.id" — one of the two still groups.

        Review pin. Changing that ``and`` to an ``or`` — one character in the
        collapse condition — left the whole suite green, because every fragment
        in the corpus carries both. With ``or``, a real transcript whose
        fragments carry only a ``requestId`` would stop collapsing and every
        cache figure would inflate by ~55% with no test failing.
        """
        shared: dict[str, object] = {"message_id": None, "request_id": None, "agent_id": "a1"}
        shared[present] = "shared-value"
        records = [
            f.assistant(
                f"frag-{index}",
                timestamp=f"2026-09-09T10:00:0{index + 1}.000Z",
                usage_block=f.usage(output_tokens=6 if index < 2 else 483, cache_read=1_000),
                **shared,  # type: ignore[arg-type]
            )
            for index in range(3)
        ]
        stats = stats_for([f.write_trace(tmp_path, records)])
        assert stats.assistant_records == 3
        assert stats.model_calls == 1, "one of the two join fields is enough to group"
        assert stats.collapsed_usage.cache_read_input_tokens == 1_000
        assert stats.naive_usage.cache_read_input_tokens == 3_000
        assert stats.collapsed_usage.output_tokens == 483

    def test_r9_an_empty_join_field_is_not_an_absent_one(self, tmp_path: Path) -> None:
        """R9: the key is the four *recorded* values, and "" is a recorded value.

        Review pin. ``request_id or ""`` mapped an empty string and an absent
        field onto the same key component, so two unrelated responses that
        happened to share a ``message.id`` merged when one carried
        ``requestId: ""``.
        """
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                message_id="M",
                request_id="",
                usage_block=f.usage(output_tokens=10),
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                message_id="M",
                request_id=None,
                usage_block=f.usage(output_tokens=20),
            ),
        ]
        calls = [
            span
            for span in load([f.write_trace(tmp_path, records)]).spans
            if span.kind == "model_call"
        ]
        assert len(calls) == 2
        assert [call.usage.output_tokens for call in calls if call.usage] == [10, 20]

    def test_r9_an_interleaved_multi_message_stream_collapses_per_response(
        self, tmp_path: Path
    ) -> None:
        """R9: grouping is by key, so two responses may interleave on disk."""
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                message_id="m1",
                request_id="r1",
                usage_block=f.usage(output_tokens=1),
            ),
            f.assistant(
                "b1",
                timestamp="2026-09-09T10:00:02.000Z",
                message_id="m2",
                request_id="r2",
                usage_block=f.usage(output_tokens=2),
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:03.000Z",
                message_id="m1",
                request_id="r1",
                usage_block=f.usage(output_tokens=100),
            ),
            f.assistant(
                "b2",
                timestamp="2026-09-09T10:00:04.000Z",
                message_id="m2",
                request_id="r2",
                usage_block=f.usage(output_tokens=200),
            ),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        calls = [span for span in trace.spans if span.kind == "model_call"]
        assert len(calls) == 2
        assert [c.usage.output_tokens for c in calls if c.usage] == [100, 200]
        assert [c.seq for c in calls] == [0, 1], "each group lands at its first fragment"

    def test_r9_blocks_are_unioned_in_first_appearance_order(self, tmp_path: Path) -> None:
        """R9: the collapsed call's content is the union of its fragments' blocks."""
        shared = dict(message_id="m", request_id="r")
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[f.text_block("first")],
                usage_block=f.usage(),
                **shared,
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.text_block("first"), f.text_block("second")],
                usage_block=f.usage(),
                **shared,
            ),
        ]
        call = load([f.write_trace(tmp_path, records)]).spans[0]
        assert call.text_preview == "first second", "a repeated text block must dedupe to one"

    def test_r9_tool_use_blocks_dedupe_by_id(self, tmp_path: Path) -> None:
        """R9: block identity is ``id`` for ``tool_use``."""
        shared = dict(message_id="m", request_id="r")
        block = f.tool_use_block("t1", "Bash", {"cmd": "ls"})
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[block],
                usage_block=f.usage(),
                **shared,
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[block],
                usage_block=f.usage(),
                **shared,
            ),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        assert [s.kind for s in trace.spans] == ["model_call", "tool_call"]

    def test_r9_two_tool_uses_with_different_ids_both_survive(self, tmp_path: Path) -> None:
        """R9: deduplication must not collapse two genuinely different calls."""
        shared = dict(message_id="m", request_id="r")
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[f.tool_use_block("t1", "Bash", {"cmd": "ls"})],
                usage_block=f.usage(),
                **shared,
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.tool_use_block("t2", "Bash", {"cmd": "ls"})],
                usage_block=f.usage(),
                **shared,
            ),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        assert [s.kind for s in trace.spans] == ["model_call", "tool_call", "tool_call"]

    def test_r9_different_text_blocks_are_not_deduped(self, tmp_path: Path) -> None:
        """R9: text identity is ``(type, sha256(text))``, so different text survives."""
        shared = dict(message_id="m", request_id="r")
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[f.text_block("alpha")],
                usage_block=f.usage(),
                **shared,
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.text_block("beta")],
                usage_block=f.usage(),
                **shared,
            ),
        ]
        assert load([f.write_trace(tmp_path, records)]).spans[0].text_preview == "alpha beta"

    def test_r9_a_text_and_a_thinking_block_of_equal_text_are_distinct(
        self, tmp_path: Path
    ) -> None:
        """R9: the block type is part of the identity, not only the digest."""
        record = f.assistant(
            "a1", content=[f.thinking_block("same"), f.text_block("same")], usage_block=f.usage()
        )
        assert load([f.write_trace(tmp_path, [record])]).spans[0].text_preview == "same"

    def test_r9_the_collapsed_call_counts_extras_from_every_fragment(self, tmp_path: Path) -> None:
        """R9: a streamed response's unknown keys are counted once each, not per span."""
        shared = dict(message_id="m", request_id="r")
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                usage_block=f.usage(),
                newKeyOne=1,
                **shared,
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:02.000Z",
                usage_block=f.usage(),
                newKeyTwo=2,
                **shared,
            ),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        assert len([s for s in trace.spans if s.kind == "model_call"]) == 1
        assert trace.spans[0].extras_dropped == 2


class TestCollapseStatsBothColumnsR9:
    """R9: ``collapse_stats`` exposes naive and collapsed side by side."""

    def test_r9_the_fixture_pins_both_columns(self) -> None:
        """R9: the checked-in mapper fixture's naive *and* collapsed totals."""
        stats = stats_for([FIXTURE])
        assert stats.assistant_records == FIXTURE_ASSISTANT_RECORDS
        assert stats.model_calls == FIXTURE_MODEL_CALLS
        assert stats.naive_usage == FIXTURE_NAIVE
        assert stats.collapsed_usage == FIXTURE_COLLAPSED

    def test_r9_the_two_columns_actually_differ(self) -> None:
        """R9: a fixture where they agree would prove nothing about the collapse."""
        stats = stats_for([FIXTURE])
        assert stats.naive_usage != stats.collapsed_usage
        assert stats.assistant_records > stats.model_calls
        assert (
            inflation_pct(
                stats.naive_usage.cache_read_input_tokens,
                stats.collapsed_usage.cache_read_input_tokens,
            )
            > 50.0
        )

    def test_r9_the_collapsed_totals_match_the_spans_the_mapper_emits(self) -> None:
        """R9: ``collapse_stats`` and ``load`` must not be two implementations."""
        stats = stats_for([FIXTURE])
        trace = load([FIXTURE])
        summed = TokenUsage()
        calls = 0
        for span in trace.spans:
            if span.kind != "model_call":
                continue
            calls += 1
            if span.usage is not None:
                summed = summed.plus(span.usage)
        assert calls == stats.model_calls
        assert summed == stats.collapsed_usage

    def test_r9_naive_summation_is_what_it_claims_to_be(self) -> None:
        """R9: the naive column equals a per-record sum, computed independently."""
        import json as _json

        naive = TokenUsage()
        for line in FIXTURE.read_bytes().split(b"\n"):
            if not line.strip():
                continue
            record = _json.loads(line.decode("utf-8"))
            if record.get("type") != "assistant" or record.get("isApiErrorMessage") is True:
                continue
            usage = (record.get("message") or {}).get("usage")
            if not isinstance(usage, dict):
                continue
            breakdown = usage.get("cache_creation")
            if isinstance(breakdown, dict):
                five, hour = (
                    breakdown.get("ephemeral_5m_input_tokens", 0),
                    breakdown.get("ephemeral_1h_input_tokens", 0),
                )
            else:
                five, hour = usage.get("cache_creation_input_tokens", 0), 0
            naive = naive.plus(
                TokenUsage(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_creation_5m_tokens=five,
                    cache_creation_1h_tokens=hour,
                )
            )
        assert naive == FIXTURE_NAIVE

    def test_r9_collapse_stats_is_deterministic(self) -> None:
        """R9: the pinned constants must not depend on when they were measured."""
        assert stats_for([FIXTURE]) == stats_for([FIXTURE])

    def test_r9_a_naive_implementation_would_fail_this_module(self) -> None:
        """R9: the pin is loud — the two columns are far enough apart to notice.

        This is the canary-shaped arm of the R9 pin: if the mapper regressed to
        per-record summation, ``collapsed_usage`` would equal ``naive_usage`` and
        the assertions above would fail rather than quietly change a dollar
        figure.
        """
        assert FIXTURE_NAIVE.cache_read_input_tokens > FIXTURE_COLLAPSED.cache_read_input_tokens
        assert (
            inflation_pct(
                FIXTURE_NAIVE.cache_read_input_tokens, FIXTURE_COLLAPSED.cache_read_input_tokens
            )
            > 70.0
        )


class TestApiErrorAccountingR9:
    """R9: API-error records are excluded from both columns, symmetrically."""

    def test_r9_an_api_error_group_is_counted_but_never_priced(self, tmp_path: Path) -> None:
        """R9, R12: the record makes a call, carries no usage, and bills nothing."""
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                usage_block=f.usage(input_tokens=5, output_tokens=5),
            ),
            f.api_error(
                "e1",
                timestamp="2026-09-09T10:00:02.000Z",
                message_id="msg-err",
                request_id="req-err",
            ),
        ]
        path = f.write_trace(tmp_path, records)
        stats = stats_for([path])
        assert stats.assistant_records == 2
        assert stats.model_calls == 2
        assert stats.naive_usage.total == 10
        assert stats.collapsed_usage.total == 10
        error_span = load([path]).spans[-1]
        assert error_span.kind == "model_call"
        assert error_span.usage is None
        assert error_span.error is not None

    def test_r9_a_terminal_api_error_discards_its_whole_groups_usage(self, tmp_path: Path) -> None:
        """R9: pinned because it is an asymmetry, not because it is obviously right.

        "``usage`` from the last fragment" and "an API-error record is never
        billable" compose into: a group whose *terminal* fragment is an API error
        contributes nothing to the collapsed total, even though its earlier
        fragments recorded real usage that the naive column still counts. Real
        transcripts never produce this shape — an API-error record carries its
        own ``message.id`` and ``requestId`` — but the behaviour is pinned here so
        a change to it is visible rather than a silent shift in a dollar figure.
        """
        shared = dict(message_id="msg-1", request_id="req-1")
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                usage_block=f.usage(input_tokens=5, output_tokens=5),
                **shared,
            ),
            f.api_error("e1", timestamp="2026-09-09T10:00:02.000Z", **shared),
        ]
        stats = stats_for([f.write_trace(tmp_path, records)])
        assert stats.assistant_records == 2
        assert stats.model_calls == 1
        assert stats.naive_usage.total == 10
        assert stats.collapsed_usage.total == 0


class TestRealTranscriptReconciliationR9:
    """R9: the spec's constants came from real transcripts, so check them there.

    The corpus is opt-in and never committed (A5). These tests always *run* —
    R49 makes a skip a failure — and assert the opt-in seam itself when no
    corpus is configured, so the suite is honest about what it did and did not
    measure.
    """

    def test_r9_the_opt_in_seam_is_inert_without_the_environment_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R9: CI must never depend on data it does not have."""
        monkeypatch.delenv(REAL_TRANSCRIPTS_ENV, raising=False)
        assert real_transcript_paths() == ()
        monkeypatch.setenv(REAL_TRANSCRIPTS_ENV, "/nonexistent/path/for/a/test")
        assert real_transcript_paths() == ()

    def test_r9_the_opt_in_seam_finds_transcripts_when_pointed_at_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R9: the gate is exercised on a synthetic directory, so it cannot rot."""
        f.write_trace(tmp_path, [f.assistant("a1", usage_block=f.usage())], name="agent-1")
        f.write_trace(tmp_path, [f.assistant("b1", usage_block=f.usage())], name="agent-2")
        (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
        monkeypatch.setenv(REAL_TRANSCRIPTS_ENV, str(tmp_path))
        found = real_transcript_paths()
        assert [path.name for path in found] == ["agent-1.jsonl", "agent-2.jsonl"]

    def test_r9_the_spec_constants_are_internally_consistent(self) -> None:
        """R9: the pinned record ratio and the pinned inflation agree with each other."""
        ratio = SPEC_ASSISTANT_RECORDS / SPEC_MODEL_CALLS
        assert 1.5 < ratio < 1.8, ratio
        assert SPEC_CACHE_READ_INFLATION_PCT > SPEC_OUTPUT_INFLATION_PCT * 10

    def test_r9_the_measurement_path_runs_on_a_synthetic_corpus_in_every_run(
        self, tmp_path: Path
    ) -> None:
        """R9: the real-corpus assertions are exercised even with no real corpus.

        Review finding. The opt-in test below is the only caller of
        :func:`stats_for` and :func:`inflation_pct`, so in CI — where no corpus
        is configured — the measurement path never executed and its no-corpus
        arm asserted a tautology. A guard that runs one way locally and another
        way in CI is this team's signature failure mode, wearing "always runs"
        rather than "skipped" as a disguise.

        The corpus below reproduces the real one's *group-size histogram* (the
        answer to spec flag S5: pin the ratio, and record the shape it came
        from). With every fragment of a response repeating its usage, the
        cache-read inflation is exactly the record-to-call ratio minus one, so
        the arithmetic can be asserted rather than approximated — and the
        record-to-call ratio itself lands on the spec's.
        """
        records: list[dict[str, object]] = []
        for size, groups in sorted(REAL_GROUP_SIZE_HISTOGRAM.items()):
            for group in range(groups):
                identity = f"{size}-{group}"
                for fragment in range(size):
                    records.append(
                        f.assistant(
                            f"u-{identity}-{fragment}",
                            message_id=f"msg-{identity}",
                            request_id=f"req-{identity}",
                            usage_block=f.usage(
                                output_tokens=483 if fragment == size - 1 else 6,
                                cache_read=SYNTHETIC_CACHE_READ,
                            ),
                        )
                    )
        stats = stats_for([f.write_trace(tmp_path, records, name="agent-1")])

        expected_records = sum(size * n for size, n in REAL_GROUP_SIZE_HISTOGRAM.items())
        expected_groups = sum(REAL_GROUP_SIZE_HISTOGRAM.values())
        assert (stats.assistant_records, stats.model_calls) == (expected_records, expected_groups)

        cache_read = inflation_pct(
            stats.naive_usage.cache_read_input_tokens,
            stats.collapsed_usage.cache_read_input_tokens,
        )
        assert cache_read == pytest.approx(
            (expected_records / expected_groups - 1) * 100.0, abs=0.01
        )
        assert stats.collapsed_usage.cache_read_input_tokens == (
            SYNTHETIC_CACHE_READ * expected_groups
        )
        assert stats.naive_usage.cache_read_input_tokens == (
            SYNTHETIC_CACHE_READ * expected_records
        )

        record_ratio = stats.assistant_records / stats.model_calls
        spec_ratio = SPEC_ASSISTANT_RECORDS / SPEC_MODEL_CALLS
        assert abs(record_ratio - spec_ratio) <= RATIO_TOLERANCE, (
            f"the checked-in histogram no longer matches the spec's record ratio: "
            f"{record_ratio:.4f} vs {spec_ratio:.4f}"
        )

    def test_r9_real_transcripts_reproduce_the_spec_constants_when_available(self) -> None:
        """R9: the mapper's collapse on a real corpus, against the spec's numbers.

        Runs the measurement only when a corpus is configured. The assertion is
        on the *ratios* rather than the absolute totals: the spec's corpus is a
        live session's transcript set that grows between measurements, so the
        record counts drift while the collapse ratio does not.

        With no corpus this test measures nothing, which is why the synthetic
        histogram above exists: the assertions below are the ones CI cannot
        reach, and everything they depend on is exercised there instead.
        """
        paths = list(real_transcript_paths())
        if not paths:
            assert real_transcript_paths() == ()
            return
        stats = stats_for(paths)
        assert stats.assistant_records > 0
        assert stats.model_calls > 0
        cache_read = inflation_pct(
            stats.naive_usage.cache_read_input_tokens,
            stats.collapsed_usage.cache_read_input_tokens,
        )
        output = inflation_pct(stats.naive_usage.output_tokens, stats.collapsed_usage.output_tokens)
        record_ratio = stats.assistant_records / stats.model_calls
        spec_ratio = SPEC_ASSISTANT_RECORDS / SPEC_MODEL_CALLS
        assert abs(cache_read - SPEC_CACHE_READ_INFLATION_PCT) <= INFLATION_TOLERANCE_PCT, (
            f"cache-read inflation {cache_read:.2f}% differs from the spec's "
            f"{SPEC_CACHE_READ_INFLATION_PCT}%"
        )
        assert abs(output - SPEC_OUTPUT_INFLATION_PCT) <= INFLATION_TOLERANCE_PCT, (
            f"output inflation {output:.2f}% differs from the spec's {SPEC_OUTPUT_INFLATION_PCT}%"
        )
        assert abs(record_ratio - spec_ratio) <= RATIO_TOLERANCE, (
            f"record-to-call ratio {record_ratio:.4f} differs from the spec's {spec_ratio:.4f}"
        )
