"""R18-R24, one detector at a time, from both sides of every pinned boundary.

The requirement text for these seven detectors is unusually precise — "``n >= 2``",
"``k == 3``", "periods 1..8", "a window of 10", "``>= 60``", "at least 50%",
"``< 8``", "``x > med + 6*mad``". Precision in a requirement is only worth
anything if the suite can tell the pinned value from the value one away from it,
and the increment-1 review's mutation pass is the evidence for what happens when
it cannot: one character of R9's collapse condition changed, and the whole suite
stayed green because no checked-in input could distinguish the two behaviours.

So every case below comes in a pair. 2 occurrences and 1; 3 repeats and 2; 3
errors and 2; a period of 8 and a period of 9; a gap of 60 seconds and one of 59;
coverage of exactly 50% and of 49%; a population of 8 and of 7; a value at
``med + 6*mad`` and one a single token above it. The corpus in
``tests/fixtures/traces/`` pins the same boundaries through the adapter; these
pin them through the detector, where a pair costs one integer of diff and the
population can be 8 without a 27-record file.
"""

from __future__ import annotations

import random
import time
from typing import Any

import pytest

from swarm_observer.detect.agent_loop import (
    CRITICAL_REPEATS,
    MAX_PERIOD,
    MIN_REPEATS,
    TEXT_SIGNATURE,
    find_loop,
    signature_of,
)
from swarm_observer.detect.anomalous_span import (
    CRITICAL_MAD_MULTIPLE,
    DIMENSION_FLOORS,
    DIMENSIONS,
    FLOOR_DURATION_MS,
    FLOOR_TOKENS,
    MAD_MULTIPLE,
    POPULATION_FLOOR,
    mad_of,
)
from swarm_observer.detect.base import (
    DetectorConfig,
    Finding,
    first_tool_call_by_parent,
    lower_median,
)
from swarm_observer.detect.blocked_agent import (
    COVERAGE_DENOMINATOR,
    COVERAGE_NUMERATOR,
    CRITICAL_GAP_SECONDS,
    CoverageIndex,
    covered_millis,
)
from swarm_observer.detect.failed_tool_call import WARNING_FAILURES
from swarm_observer.detect.registry import detector_by_slug
from swarm_observer.detect.repeated_tool_call import CRITICAL_OCCURRENCES, MIN_OCCURRENCES
from swarm_observer.detect.retry_storm import (
    CRITICAL_ERRORS,
    KIND_API,
    KIND_MIXED,
    KIND_TOOL,
    MIN_ERRORS,
    WINDOW_SPANS,
    error_kind,
    merge_runs,
    qualifying_windows,
)
from swarm_observer.detect.unresolved_tool_call import (
    REASONS,
    UNKNOWN_TOOL_PATTERNS,
    looks_like_unknown_tool,
)
from swarm_observer.model.trace import SpanError, TokenUsage, Trace

from .synthetic_traces import (
    DIGEST_A,
    DIGEST_B,
    TraceBuilder,
    at,
    at_micros,
    duration_population,
    error_run,
    hex_id,
    loop_of,
    token_population,
    tokens,
    two_span_gap,
)

CONFIG = DetectorConfig()

REPEATED = detector_by_slug("repeated_tool_call")
LOOP = detector_by_slug("agent_loop")
STORM = detector_by_slug("retry_storm")
FAILED = detector_by_slug("failed_tool_call")
UNRESOLVED = detector_by_slug("unresolved_tool_call")
BLOCKED = detector_by_slug("blocked_agent")
ANOMALOUS = detector_by_slug("anomalous_span")


def run(detector: Any, trace: Trace, config: DetectorConfig = CONFIG) -> tuple[Finding, ...]:
    """One detector's findings for ``trace``."""
    result: tuple[Finding, ...] = detector.run(trace, config)
    return result


def sig(letter: str) -> tuple[str, ...]:
    """A one-letter R19 signature, for the table-driven ``find_loop`` cases."""
    return ("tool", f"Tool{letter}", hex_id(ord(letter)))


def sigs(letters: str) -> list[tuple[str, ...]]:
    """A signature sequence spelled as a string of letters."""
    return [sig(letter) for letter in letters]


# =============================================================================
# R18 — repeated_tool_call
# =============================================================================


class TestRepeatedToolCallR18:
    """R18: one finding per ``(tool_name, tool_input_digest)`` group of size >= 2."""

    def _group(self, occurrences: int, *, parent_tokens: int = 0) -> Trace:
        builder = TraceBuilder()
        for _ in range(occurrences):
            call = builder.model_call(usage=tokens(parent_tokens) if parent_tokens else None)
            builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_A)
        return builder.build()

    def test_r18_the_constants_are_the_ones_the_spec_pins(self) -> None:
        """R18: ``n >= 2`` fires; ``n >= 4`` is critical."""
        assert (MIN_OCCURRENCES, CRITICAL_OCCURRENCES) == (2, 4)

    def test_r18_one_occurrence_is_silent(self) -> None:
        """R18: the threshold from below — a tool called once is not a repeat."""
        assert run(REPEATED, self._group(1)) == ()

    def test_r18_two_occurrences_fire_as_a_warning(self) -> None:
        """R18: the threshold from above — exactly 2 is the smallest group that fires."""
        found = run(REPEATED, self._group(2))
        assert len(found) == 1
        assert found[0].severity == "warning"
        assert found[0].metrics["occurrences"] == 2

    def test_r18_three_occurrences_are_still_a_warning(self) -> None:
        """R18: ``2 <= n <= 3`` is warning — 3 is the last warning, not the first critical."""
        found = run(REPEATED, self._group(3))
        assert (found[0].severity, found[0].metrics["occurrences"]) == ("warning", 3)

    def test_r18_four_occurrences_are_critical(self) -> None:
        """R18: ``n >= 4`` is critical — 4 is the first, not the second."""
        found = run(REPEATED, self._group(4))
        assert (found[0].severity, found[0].metrics["occurrences"]) == ("critical", 4)

    def test_r18_the_group_key_includes_the_tool_name(self) -> None:
        """R18: two different tools sharing one input digest are two groups, not one.

        A key that forgot the name would report one group of two and call it a
        repeat; nothing was repeated.
        """
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_A)
        builder.tool_call(parent=call, tool_name="Read", digest=DIGEST_A)
        assert run(REPEATED, builder.build()) == ()

    def test_r18_the_group_key_includes_the_input_digest(self) -> None:
        """R18: one tool called twice with different arguments is not a repeat."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_A)
        builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_B)
        assert run(REPEATED, builder.build()) == ()

    def test_r18_two_groups_produce_two_findings(self) -> None:
        """R18: one finding *per group*, not one per trace."""
        builder = TraceBuilder()
        call = builder.model_call()
        for digest in (DIGEST_A, DIGEST_A, DIGEST_B, DIGEST_B):
            builder.tool_call(parent=call, tool_name="Bash", digest=digest)
        found = run(REPEATED, builder.build())
        assert len(found) == 2
        assert {item.metrics["occurrences"] for item in found} == {2}

    def test_r18_metrics_name_the_first_and_last_span_of_the_group(self) -> None:
        """R18: ``first_seq`` and ``last_seq`` bracket the group in ``seq`` order."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, digest=DIGEST_A)
        builder.tool_call(parent=call, digest=DIGEST_B)
        builder.tool_call(parent=call, digest=DIGEST_A)
        found = run(REPEATED, builder.build())
        assert len(found) == 1
        assert found[0].metrics["first_seq"] == 1
        assert found[0].metrics["last_seq"] == 3
        assert found[0].span_seqs == (1, 3)

    def test_r18_the_preview_is_the_first_occurrences_tool_input(self) -> None:
        """R18: "the first occurrence's ``tool_input_preview``" — first, not last.

        The two calls below share a name and a digest and carry different
        preview text, which the adapter would not normally produce and R18
        nevertheless pins a rule for. Taking the last occurrence's preview
        passes every fixture in the corpus and is the wrong sentence.
        """
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, digest=DIGEST_A, input_preview="the first input")
        builder.tool_call(parent=call, digest=DIGEST_A, input_preview="the second input")
        found = run(REPEATED, builder.build())
        assert found[0].previews == ("the first input",)

    def test_r18_waste_is_the_parents_of_occurrences_two_onward(self) -> None:
        """R18, R17: doing the work once is not waste; doing it again is."""
        builder = TraceBuilder()
        for index in range(3):
            call = builder.model_call(usage=tokens(100 * (index + 1)))
            builder.tool_call(parent=call, digest=DIGEST_A)
        found = run(REPEATED, builder.build())
        assert found[0].wasted == tokens(200 + 300)

    def test_r18_waste_deduplicates_a_shared_parent(self) -> None:
        """R18, R17: three calls from one model call attribute that call once."""
        builder = TraceBuilder()
        call = builder.model_call(usage=tokens(500))
        for _ in range(3):
            builder.tool_call(parent=call, digest=DIGEST_A)
        found = run(REPEATED, builder.build())
        assert found[0].wasted == tokens(500)

    def test_r18_a_group_with_no_parent_model_calls_attributes_nothing(self) -> None:
        """R18, R17: a tool call with no parent has no tokens to attribute."""
        builder = TraceBuilder()
        builder.tool_call(parent=None, digest=DIGEST_A)
        builder.tool_call(parent=None, digest=DIGEST_A)
        found = run(REPEATED, builder.build())
        assert found[0].wasted == TokenUsage()

    def test_r18_a_non_conforming_tool_name_is_slugged_and_previewed(self) -> None:
        """R18, R16: the group still fires; the name does not reach ``metrics``."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, tool_name="Bash\n", digest=DIGEST_A)
        builder.tool_call(parent=call, tool_name="Bash\n", digest=DIGEST_A)
        found = run(REPEATED, builder.build())
        assert found[0].metrics["tool_name"] == "<non-conforming>"
        assert "Bash\n" in found[0].previews

    def test_r18_evidence_spans_are_capped_at_fifty_but_metrics_count_all(self) -> None:
        """R18, R14: the count is the truth; the span list is evidence."""
        builder = TraceBuilder()
        call = builder.model_call()
        for _ in range(80):
            builder.tool_call(parent=call, digest=DIGEST_A)
        found = run(REPEATED, builder.build())
        assert found[0].metrics["occurrences"] == 80
        assert len(found[0].span_seqs) == 50
        assert found[0].metrics["last_seq"] == 80


# =============================================================================
# R19 — agent_loop
# =============================================================================


class TestAgentLoopSearchR19:
    """R19's search, table-driven through ``find_loop``.

    The corpus reaches periods 1 and 2 only — a period-8 fixture needs 24 model
    calls and a period-9 negative needs 27 — so the period bound is proved here,
    where the sequence is a list of tuples.
    """

    def test_r19_the_constants_are_the_ones_the_spec_pins(self) -> None:
        """R19: periods 1..8, three repeats to fire, four to escalate."""
        assert (MAX_PERIOD, MIN_REPEATS, CRITICAL_REPEATS) == (8, 3, 4)

    @pytest.mark.parametrize("period", range(1, MAX_PERIOD + 1))
    def test_r19_a_cycle_of_every_searched_period_is_found(self, period: int) -> None:
        """R19: periods 1 through 8 are all searched, including the last one."""
        sequence = sigs("abcdefgh"[:period]) * MIN_REPEATS
        assert find_loop(sequence, 0) == (period, 0, MIN_REPEATS)

    def test_r19_a_cycle_of_period_nine_is_not_found(self) -> None:
        """R19: the search stops at 8 — period 9 is the bound from above.

        27 model calls repeating a 9-step cycle three times. Nothing shorter
        matches, so a detector that searched one period further would report a
        loop the requirement does not define.
        """
        sequence = sigs("abcdefghi") * MIN_REPEATS
        assert find_loop(sequence, 0) is None

    @pytest.mark.parametrize("period", [10, 12])
    def test_r19_longer_cycles_are_not_found_either(self, period: int) -> None:
        """R19: the bound is a bound, not an off-by-one at 9."""
        alphabet = "abcdefghijklmnop"[:period]
        assert find_loop(sigs(alphabet) * MIN_REPEATS, 0) is None

    def test_r19_two_repeats_do_not_fire(self) -> None:
        """R19: ``A B A B`` is not a loop — the threshold from below."""
        assert find_loop(sigs("abab"), 0) is None

    def test_r19_three_repeats_fire(self) -> None:
        """R19: ``A B A B A B`` is a period-2 loop repeated three times."""
        assert find_loop(sigs("ababab"), 0) == (2, 0, 3)

    def test_r19_a_loop_ending_at_the_final_signature_is_found(self) -> None:
        """R19: the start index runs to ``len(S) - 3p`` *inclusive*.

        ``A B A B A B`` has length 6 and period 2, so its only start index is
        ``6 - 3*2 == 0`` — the last legal start. A range that stopped one short
        would find no loop at all here and would still pass every corpus
        fixture, because each of those has a trailing signature after the cycle.
        """
        assert find_loop(sigs("ababab"), 0) == (2, 0, 3)
        assert find_loop(sigs("cababab"), 0) == (2, 1, 3)

    def test_r19_repeats_extend_to_the_maximal_run(self) -> None:
        """R19: ``k`` is the maximal number of consecutive repeats, not three."""
        assert find_loop(sigs("ababababab"), 0) == (2, 0, 5)
        assert find_loop(sigs("aaaaaa"), 0) == (1, 0, 6)

    def test_r19_period_ascends_before_start_index(self) -> None:
        """R19: ``A A A A A A`` is period 1 repeated six times, not period 2 thrice.

        Reporting the period-2 reading would halve the repeat count and
        understate the loop.
        """
        assert find_loop(sigs("aaaaaa"), 0) == (1, 0, 6)

    def test_r19_start_index_ascends_within_a_period(self) -> None:
        """R19: the first matching start wins, so an earlier loop is reported first."""
        assert find_loop(sigs("xaaabbb"), 0) == (1, 1, 3)

    def test_r19_the_start_floor_skips_a_run_already_reported(self) -> None:
        """R19: the search resumes at ``i + k*p``, so a repetition is reported once."""
        sequence = sigs("aaabbb")
        first = find_loop(sequence, 0)
        assert first == (1, 0, 3)
        assert find_loop(sequence, 3) == (1, 3, 3)
        assert find_loop(sequence, 6) is None

    def test_r19_a_start_floor_past_the_end_is_not_an_error(self) -> None:
        """R19: the resume index can exceed the sequence, and that just ends the search."""
        assert find_loop(sigs("aaa"), 99) is None

    @pytest.mark.parametrize("sequence", ["", "a", "aa", "ab", "abc"])
    def test_r19_short_sequences_never_fire(self, sequence: str) -> None:
        """R19: fewer than ``3p`` signatures cannot contain three repeats."""
        assert find_loop(sigs(sequence), 0) is None

    def test_r19_a_broken_cycle_does_not_fire(self) -> None:
        """R19: ``A B A B C`` — two repeats and a different decision."""
        assert find_loop(sigs("ababc"), 0) is None

    def test_r19_a_signature_is_tool_name_and_digest_together(self) -> None:
        """R19: two tools sharing an input digest are two different decisions.

        A signature that dropped the name would read ``Bash(x) Read(x) Bash(x)
        Read(x) Bash(x) Read(x)`` as a period-1 loop of six.
        """
        builder = TraceBuilder()
        first = builder.model_call()
        bash = builder.tool_call(parent=first, tool_name="Bash", digest=DIGEST_A)
        second = builder.model_call()
        read = builder.tool_call(parent=second, tool_name="Read", digest=DIGEST_A)
        assert signature_of(first, bash) != signature_of(second, read)

    def test_r19_a_signature_ignoring_the_digest_would_merge_two_decisions(self) -> None:
        """R19: one tool with two different inputs is two decisions."""
        builder = TraceBuilder()
        first = builder.model_call()
        one = builder.tool_call(parent=first, tool_name="Bash", digest=DIGEST_A)
        second = builder.model_call()
        two = builder.tool_call(parent=second, tool_name="Bash", digest=DIGEST_B)
        assert signature_of(first, one) != signature_of(second, two)

    def test_r19_a_model_call_with_no_tool_call_is_the_text_signature(self) -> None:
        """R19: "else ``("text",)``"."""
        builder = TraceBuilder()
        call = builder.model_call()
        assert signature_of(call, None) == TEXT_SIGNATURE == ("text",)


class TestAgentLoopFiringR19:
    """R19 through the detector: severity, evidence, waste, multiplicity."""

    def test_r19_two_repeats_are_silent(self) -> None:
        """R19: AC8's negative — ``A B A B C`` fires nothing."""
        assert run(LOOP, loop_of("ababc")) == ()

    def test_r19_three_repeats_are_a_warning(self) -> None:
        """R19: ``k == 3`` is warning."""
        found = run(LOOP, loop_of("abababc"))
        assert len(found) == 1
        assert found[0].severity == "warning"
        assert found[0].metrics["period"] == 2
        assert found[0].metrics["repeats"] == 3

    def test_r19_four_repeats_are_critical(self) -> None:
        """R19: ``k >= 4`` is critical — 4 is the first, not the second."""
        found = run(LOOP, loop_of("ababababc"))
        assert (found[0].severity, found[0].metrics["repeats"]) == ("critical", 4)

    def test_r19_one_finding_per_loop_not_one_per_matching_start_index(self) -> None:
        """R19: "at most one finding per agent" read as "not one per start index".

        ``A A A A A A`` matches at start 0, 1, 2 and 3. A naive implementation
        reports four findings for one loop; this reports one, with ``k == 6``.
        """
        found = run(LOOP, loop_of("aaaaaa"))
        assert len(found) == 1
        assert found[0].metrics == {
            "end_seq": 10,
            "period": 1,
            "repeats": 6,
            "start_seq": 0,
        }

    def test_r19_two_non_overlapping_loops_in_one_agent_are_two_findings(self) -> None:
        """R19, A-b3: "the search then resumes at ``i + k*p``", so both are reported.

        R19 also says "at most one finding per agent", which contradicts this
        sentence. The implemented reading is the resume clause; the flagged
        ambiguity is recorded in the test report. This test pins the behaviour
        so a later PM ruling has to change it deliberately.
        """
        found = run(LOOP, loop_of("aaabbb"))
        assert len(found) == 2
        assert sorted(int(item.metrics["start_seq"]) for item in found) == [0, 6]
        assert all(item.metrics["repeats"] == 3 for item in found)

    def test_r19_two_agents_looping_are_two_findings(self) -> None:
        """R19: the sequence is per agent, so one agent's cycle is not the other's."""
        builder = TraceBuilder()
        for agent in ("root", "sub"):
            for letter in "aaa":
                call = builder.model_call(agent_id=agent)
                builder.tool_call(
                    agent_id=agent, parent=call, tool_name=f"Tool{letter}", digest=hex_id(1)
                )
        found = run(LOOP, builder.build())
        assert len(found) == 2
        assert {item.agent_ids for item in found} == {("root",), ("sub",)}

    def test_r19_interleaved_agents_do_not_form_a_loop_together(self) -> None:
        """R19: an alternation across two agents is each agent repeating itself once."""
        builder = TraceBuilder()
        for index in range(6):
            agent = "root" if index % 2 == 0 else "sub"
            call = builder.model_call(agent_id=agent)
            builder.tool_call(
                agent_id=agent, parent=call, tool_name="Bash", digest=hex_id(index % 2)
            )
        found = run(LOOP, builder.build())
        assert len(found) == 2
        assert all(item.metrics["period"] == 1 for item in found)
        assert all(item.metrics["repeats"] == 3 for item in found)

    def test_r19_tool_user_and_system_spans_are_excluded_from_the_sequence(self) -> None:
        """R19: a loop is measured in decisions, not in records."""
        builder = TraceBuilder()
        for _ in range(3):
            call = builder.model_call()
            builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_A)
            builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_B)
            builder.user_message()
            builder.system_event()
        found = run(LOOP, builder.build())
        assert len(found) == 1
        assert found[0].metrics["period"] == 1
        assert found[0].metrics["repeats"] == 3

    def test_r19_the_signature_uses_the_lowest_seq_tool_call_of_a_model_call(self) -> None:
        """R19: "its first emitted ``tool_call``" — parallel calls do not confuse it."""
        builder = TraceBuilder()
        for _ in range(3):
            call = builder.model_call()
            builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_A)
            builder.tool_call(parent=call, tool_name="Read", digest=DIGEST_B)
        assert len(run(LOOP, builder.build())) == 1

    def test_r19_first_and_last_emitted_tool_calls_give_different_answers(self) -> None:
        """R19: "*first* emitted ``tool_call``", where first and last disagree.

        The case above cannot tell the two apart — every model call emits the
        same pair, so the first and the last are both constant and both make a
        loop. Here the first tool call is the same every turn and the second is
        different every turn: reading the first finds a period-1 loop, reading
        the last finds nothing at all. Without this pair, replacing
        ``setdefault`` with an assignment in ``first_tool_call_by_parent``
        leaves the whole suite green (reviewer's mutation sweep).
        """
        builder = TraceBuilder()
        for tail in ("Bash", "Grep", "Write"):
            call = builder.model_call()
            builder.tool_call(parent=call, tool_name="Read", digest=DIGEST_A)
            builder.tool_call(parent=call, tool_name=tail, digest=DIGEST_B)
        trace = builder.build()

        by_parent = first_tool_call_by_parent(trace)
        assert [span.tool_name for span in by_parent.values()] == ["Read", "Read", "Read"]

        found = run(LOOP, trace)
        assert len(found) == 1
        assert found[0].metrics["period"] == 1
        assert found[0].metrics["repeats"] == 3

    def test_r19_only_tool_calls_are_a_model_calls_emitted_tool_call(self) -> None:
        """R19: a span that is not a ``tool_call`` is not one, whatever its parent.

        R12 gives ``parent_span_id`` only to ``tool_call`` spans, so the kind
        check in ``first_tool_call_by_parent`` is unreachable through today's
        adapter — which is exactly why it is worth an assertion rather than a
        comment. A v2 source (R3 is the seam) that parents a message to its
        model call would otherwise silently become that call's "tool".
        """
        builder = TraceBuilder()
        for _ in range(3):
            call = builder.model_call()
            message = builder.user_message()
            builder.replace(message, parent_span_id=call.span_id)
        trace = builder.build()
        assert first_tool_call_by_parent(trace) == {}
        found = run(LOOP, trace)
        assert len(found) == 1
        assert found[0].metrics["period"] == 1, "three text-only calls are a period-1 loop"

    def test_r19_text_only_model_calls_form_a_loop(self) -> None:
        """R19: three model calls emitting no tools share the ``("text",)`` signature."""
        builder = TraceBuilder()
        for _ in range(3):
            builder.model_call()
        found = run(LOOP, builder.build())
        assert len(found) == 1
        assert found[0].metrics == {"end_seq": 2, "period": 1, "repeats": 3, "start_seq": 0}

    def test_r19_waste_is_repeats_two_onward(self) -> None:
        """R19, R17: the first pass through a cycle is work; the rest is redone work."""
        builder = TraceBuilder()
        for index in range(3):
            call = builder.model_call(usage=tokens(100 * (index + 1)))
            builder.tool_call(parent=call, tool_name="Bash", digest=DIGEST_A)
        found = run(LOOP, builder.build())
        assert found[0].wasted == tokens(200 + 300)

    def test_r19_evidence_names_the_covered_model_calls(self) -> None:
        """R19: ``span_seqs`` is the loop's model calls, and metrics bracket them."""
        found = run(LOOP, loop_of("aaa"))
        assert found[0].span_seqs == (0, 2, 4)
        assert found[0].metrics["start_seq"] == 0
        assert found[0].metrics["end_seq"] == 4


# =============================================================================
# R20 — retry_storm
# =============================================================================


class TestRetryStormR20:
    """R20: three or more error spans inside ten consecutive spans of one agent."""

    def test_r20_the_constants_are_the_ones_the_spec_pins(self) -> None:
        """R20: a 10-span window, 3 errors to fire, 5 to escalate."""
        assert (WINDOW_SPANS, MIN_ERRORS, CRITICAL_ERRORS) == (10, 3, 5)

    def test_r20_two_errors_are_silent(self) -> None:
        """R20: the threshold from below."""
        assert run(STORM, error_run(10, [0, 1])) == ()

    def test_r20_three_errors_fire_as_a_warning(self) -> None:
        """R20: the threshold from above — exactly 3 is the smallest storm."""
        found = run(STORM, error_run(10, [0, 1, 2]))
        assert len(found) == 1
        assert (found[0].severity, found[0].metrics["errors"]) == ("warning", 3)

    def test_r20_four_errors_are_still_a_warning(self) -> None:
        """R20: "3-4 error spans" is warning — 4 is the last warning."""
        found = run(STORM, error_run(10, [0, 1, 2, 3]))
        assert (found[0].severity, found[0].metrics["errors"]) == ("warning", 4)

    def test_r20_five_errors_are_critical(self) -> None:
        """R20: ">= 5" is critical — 5 is the first, not the sixth."""
        found = run(STORM, error_run(10, [0, 1, 2, 3, 4]))
        assert (found[0].severity, found[0].metrics["errors"]) == ("critical", 5)

    def test_r20_three_errors_spread_over_eleven_spans_are_silent(self) -> None:
        """R20: the window is ten spans wide, and eleven is one too many.

        Errors at positions 0, 5 and 10 never share a ten-span window: the
        window starting at 0 covers 0..9 and the one starting at 1 covers 1..10.
        A window bound written ``<=`` covers eleven positions and fires here.
        """
        assert run(STORM, error_run(12, [0, 5, 10])) == ()

    def test_r20_three_errors_spread_over_exactly_ten_spans_fire(self) -> None:
        """R20: positions 0, 5 and 9 do share one window — the bound from the other side."""
        found = run(STORM, error_run(12, [0, 5, 9]))
        assert len(found) == 1
        assert found[0].metrics["errors"] == 3

    def test_r20_a_storm_in_the_final_ten_spans_is_found(self) -> None:
        """R20: the last window start is ``len(spans) - 10``, inclusive.

        The three errors below sit at the very end of a 13-span agent, so the
        only window containing all three starts at index 3 — the last one. A
        start range one short misses the storm entirely.
        """
        found = run(STORM, error_run(13, [10, 11, 12]))
        assert len(found) == 1
        assert found[0].metrics["errors"] == 3

    def test_r20_an_agent_shorter_than_the_window_can_still_fire(self) -> None:
        """R20, A-b4: an agent with six spans has no ten-span window.

        Read literally, that makes this detector structurally unable to fire on
        a short agent — five spans, four of them errors, silently clean. The
        implemented reading is that a short agent's whole span list is the
        single window.
        """
        found = run(STORM, error_run(5, [0, 1, 2, 3]))
        assert len(found) == 1
        assert found[0].metrics["errors"] == 4

    def test_r20_the_run_is_trimmed_to_its_first_and_last_error(self) -> None:
        """R20, A-b5: ``window_spans`` measures the storm, not the window."""
        found = run(STORM, error_run(20, [4, 5, 6]))
        assert found[0].metrics["start_seq"] == 4
        assert found[0].metrics["end_seq"] == 6
        assert found[0].metrics["window_spans"] == 3

    def test_r20_two_storms_far_apart_are_two_findings(self) -> None:
        """R20: windows that do not overlap are not merged."""
        found = run(STORM, error_run(25, [0, 1, 2, 20, 21, 22]))
        assert len(found) == 2
        assert sorted(int(item.metrics["start_seq"]) for item in found) == [0, 20]
        assert all(item.severity == "warning" for item in found)

    def test_r20_overlapping_windows_merge_into_one_finding(self) -> None:
        """R20: "overlapping windows are merged into one finding covering the maximal run"."""
        found = run(STORM, error_run(20, [0, 1, 2, 5, 6, 7]))
        assert len(found) == 1
        assert found[0].metrics["errors"] == 6
        assert found[0].severity == "critical"

    def test_r20_adjacent_but_non_overlapping_windows_are_not_merged(self) -> None:
        """R20: half-open windows [0,10) and [10,20) do not overlap, so they are two storms.

        The agent below has 20 spans, erroring at 0, 1, 2 and at 17, 18, 19. The
        only qualifying windows start at 0 (covering spans 0-9) and at 10
        (covering spans 10-19); no span is in both. R20 merges *overlapping*
        windows, and these do not overlap, so this is two warnings of three
        errors each. Before the fix this was one critical finding claiming six
        errors in a run of twenty spans — a density no ten-span window supports,
        and a severity escalation from warning to critical (BUG-1, review B1).
        """
        found = run(STORM, error_run(20, [0, 1, 2, 17, 18, 19]))
        assert len(found) == 2
        assert [item.severity for item in found] == ["warning", "warning"]
        assert sorted(int(item.metrics["errors"]) for item in found) == [3, 3]

    def test_r20_merge_runs_collapses_overlapping_ranges(self) -> None:
        """R20: the merge helper, directly."""
        assert merge_runs([]) == []
        assert merge_runs([(0, 5), (3, 10)]) == [(0, 10)]
        assert merge_runs([(5, 10), (0, 3)]) == [(0, 3), (5, 10)]
        assert merge_runs([(0, 5), (6, 10)]) == [(0, 5), (6, 10)]

    @pytest.mark.parametrize(
        ("label", "span_count", "positions", "expected"),
        [
            ("no errors", 20, [], []),
            ("two errors is not a window", 20, [0, 1], []),
            ("three at the very start", 20, [0, 1, 2], [(0, 10)]),
            ("three at the very end", 20, [17, 18, 19], [(10, 20)]),
            ("errors exactly 9 apart still share a window", 20, [0, 5, 9], [(0, 10)]),
            ("errors exactly 10 apart never share one", 20, [0, 5, 10], []),
            ("a short agent is one window", 6, [0, 2, 5], [(0, 6)]),
            ("a short agent below the threshold", 6, [0, 5], []),
            ("exactly ten spans is one window", 10, [0, 4, 9], [(0, 10)]),
        ],
    )
    def test_r20_qualifying_windows_is_the_sliding_ten_span_count(
        self, label: str, span_count: int, positions: list[int], expected: list[tuple[int, int]]
    ) -> None:
        """R20: the window scan, directly, at both sides of its two boundaries.

        The nested count this replaced (BUG-2) was correct and quadratic; the
        pointer walk is correct and linear, and "correct" has to be asserted
        against something other than the implementation it replaced. Errors
        nine apart share a ten-span window and errors ten apart never do —
        the pair the corpus could not previously distinguish.
        """
        assert qualifying_windows(positions, span_count) == expected

    def test_r20_qualifying_windows_agrees_with_a_direct_recount(self) -> None:
        """R20: the pointer walk equals a fresh count per window, over random layouts."""
        rnd = random.Random(20260910)
        for _ in range(2_000):
            span_count = rnd.randint(1, 40)
            positions = sorted(rnd.sample(range(span_count), rnd.randint(0, span_count)))
            naive = [
                (start, min(start + WINDOW_SPANS, span_count))
                for start in range(max(1, span_count - WINDOW_SPANS + 1))
                if sum(1 for spot in positions if start <= spot < start + WINDOW_SPANS)
                >= MIN_ERRORS
            ]
            assert qualifying_windows(positions, span_count) == naive, (span_count, positions)

    def test_r20_merge_runs_treats_the_ranges_as_half_open(self) -> None:
        """R20, BUG-1: abutting ranges share no index, so they do not merge.

        Both sides of the one comparison, one index apart, because the whole
        defect was a `<=` where the ranges are half-open: ``[0, 10)`` ends at
        index 9 and ``[10, 20)`` begins at index 10.
        """
        assert merge_runs([(0, 10), (10, 20)]) == [(0, 10), (10, 20)]
        assert merge_runs([(0, 10), (9, 20)]) == [(0, 20)]
        assert merge_runs([(0, 10), (10, 20), (19, 30)]) == [(0, 10), (10, 30)]

    @pytest.mark.parametrize(
        ("label", "positions", "expected"),
        [
            ("all tool errors", [0, 1, 2], KIND_TOOL),
        ],
    )
    def test_r20_kinds_reports_a_single_kind_run_as_that_kind(
        self, label: str, positions: list[int], expected: str
    ) -> None:
        """R20: ``kinds`` is a closed enumeration; a pure run names its kind."""
        found = run(STORM, error_run(10, positions))
        assert found[0].metrics["kinds"] == expected

    def test_r20_an_all_api_error_run_reports_api_error(self) -> None:
        """R20: a ``model_call`` with a non-null ``error`` is an error span."""
        builder = TraceBuilder()
        for index in range(6):
            builder.model_call(error=SpanError(code="rate_limit", detail=f"429 {index}"))
        found = run(STORM, builder.build())
        assert len(found) == 1
        assert found[0].metrics["kinds"] == KIND_API

    def test_r20_a_run_of_both_kinds_reports_mixed(self) -> None:
        """R20: ``mixed`` is the third value and needs both kinds in one run."""
        builder = TraceBuilder()
        builder.model_call(error=SpanError(code="rate_limit", detail="429"))
        builder.tool_call(status="error", result_preview="boom")
        builder.tool_call(status="error", result_preview="boom again")
        found = run(STORM, builder.build())
        assert len(found) == 1
        assert found[0].metrics["kinds"] == KIND_MIXED

    def test_r20_error_kind_classifies_each_span_shape(self) -> None:
        """R20: only an errored tool call and an errored model call are error spans."""
        builder = TraceBuilder()
        assert error_kind(builder.tool_call(status="error")) == KIND_TOOL
        assert error_kind(builder.tool_call(status="ok")) is None
        assert error_kind(builder.tool_call(status="missing")) is None
        assert error_kind(builder.model_call(error=SpanError(code="x"))) == KIND_API
        assert error_kind(builder.model_call()) is None
        assert error_kind(builder.system_event()) is None
        assert error_kind(builder.user_message()) is None

    def test_r20_a_system_event_is_not_an_error_span(self) -> None:
        """R20: a compaction boundary is not a failure, however many of them there are."""
        builder = TraceBuilder()
        for _ in range(6):
            builder.system_event()
        assert run(STORM, builder.build()) == ()

    def test_r20_errors_of_two_agents_do_not_combine(self) -> None:
        """R20: the window slides over *one agent's* spans.

        Four errors alternating between two agents are two errors each and fire
        nothing; the same four errors under one agent are a storm. A detector
        that windowed over the whole trace could not tell the two apart.
        """
        split = TraceBuilder()
        for index in range(4):
            split.tool_call(
                agent_id="root" if index % 2 == 0 else "sub",
                status="error",
                result_preview="boom",
            )
        assert run(STORM, split.build()) == ()
        assert len(run(STORM, error_run(4, [0, 1, 2, 3]))) == 1

    def test_r20_each_agents_storm_is_its_own_finding(self) -> None:
        """R20: two agents each storming produce two findings, not one merged run."""
        builder = TraceBuilder()
        for index in range(6):
            builder.tool_call(
                agent_id="root" if index % 2 == 0 else "sub",
                status="error",
                result_preview="boom",
            )
        found = run(STORM, builder.build())
        assert len(found) == 2
        assert {item.agent_ids for item in found} == {("root",), ("sub",)}
        assert all(item.metrics["errors"] == 3 for item in found)

    def test_r20_waste_is_every_model_call_in_the_merged_run(self) -> None:
        """R20, R17: a storm is the model being asked to try again.

        The run is the *trimmed* one (A-b5), so the model call that preceded the
        first error is outside it and is not attributed: only the two calls
        sitting between the first and last error are. Attributing the whole
        window would charge the storm for the work that led up to it.
        """
        builder = TraceBuilder()
        builder.model_call(usage=tokens(10))
        for _ in range(3):
            builder.model_call(usage=tokens(100))
            builder.tool_call(status="error", result_preview="boom")
        found = run(STORM, builder.build())
        assert found[0].metrics["start_seq"] == 2
        assert found[0].wasted == tokens(200)

    def test_r20_evidence_spans_are_the_error_spans(self) -> None:
        """R20, A-b10: the evidence is the errors, not every span of the run."""
        found = run(STORM, error_run(20, [4, 6, 8]))
        assert found[0].span_seqs == (4, 6, 8)
        assert found[0].metrics["window_spans"] == 5


# =============================================================================
# R21 — failed_tool_call
# =============================================================================


class TestFailedToolCallR21:
    """R21: one finding per ``(agent_id, tool_name)`` with at least one error result."""

    def _failures(self, count: int, **kwargs: Any) -> Trace:
        builder = TraceBuilder()
        for index in range(count):
            builder.tool_call(status="error", result_preview=f"failure {index}", **kwargs)
        builder.model_call()
        return builder.build()

    def test_r21_the_constant_is_the_one_the_spec_pins(self) -> None:
        """R21: one occurrence is ``info``; two or more is ``warning``."""
        assert WARNING_FAILURES == 2

    def test_r21_no_failures_are_silent(self) -> None:
        """R21: a successful tool call is not a finding."""
        builder = TraceBuilder()
        builder.tool_call(status="ok")
        assert run(FAILED, builder.build()) == ()

    def test_r21_one_failure_is_info(self) -> None:
        """R21: "``info`` for exactly 1 occurrence" — the boundary from below."""
        found = run(FAILED, self._failures(1))
        assert len(found) == 1
        assert (found[0].severity, found[0].metrics["failures"]) == ("info", 1)

    def test_r21_two_failures_are_a_warning(self) -> None:
        """R21: ">= 2" — 2 is the first warning, not the third."""
        found = run(FAILED, self._failures(2))
        assert (found[0].severity, found[0].metrics["failures"]) == ("warning", 2)

    def test_r21_a_missing_result_is_not_a_failure(self) -> None:
        """R21: "``tool_result_status == "error"``" — ``missing`` is R22's business."""
        builder = TraceBuilder()
        builder.tool_call(status="missing")
        builder.model_call()
        assert run(FAILED, builder.build()) == ()

    def test_r21_one_tool_failing_under_two_agents_is_two_findings(self) -> None:
        """R21: the group key is the pair, and that distinction is usually the point."""
        builder = TraceBuilder()
        builder.tool_call(agent_id="root", status="error", result_preview="boom")
        builder.tool_call(agent_id="sub", status="error", result_preview="boom")
        builder.model_call(agent_id="root")
        builder.model_call(agent_id="sub")
        found = run(FAILED, builder.build())
        assert len(found) == 2
        assert {item.agent_ids for item in found} == {("root",), ("sub",)}

    def test_r21_two_tools_failing_under_one_agent_are_two_findings(self) -> None:
        """R21: the other half of the group key."""
        builder = TraceBuilder()
        builder.tool_call(tool_name="Bash", status="error", result_preview="boom")
        builder.tool_call(tool_name="Read", status="error", result_preview="boom")
        builder.model_call()
        found = run(FAILED, builder.build())
        assert len(found) == 2
        assert {str(item.metrics["tool_name"]) for item in found} == {"Bash", "Read"}

    def test_r21_previews_are_distinct_by_value_first_occurrence_winning(self) -> None:
        """R21, A-b6: "up to 5 *distinct* ``tool_result_preview`` values, in ``seq`` order"."""
        builder = TraceBuilder()
        for text in ["same", "same", "other", "same", "third"]:
            builder.tool_call(status="error", result_preview=text)
        builder.model_call()
        found = run(FAILED, builder.build())
        assert found[0].previews == ("same", "other", "third")
        assert found[0].metrics["failures"] == 5

    def test_r21_previews_are_capped_at_five_distinct_values(self) -> None:
        """R21, R14: eight distinct messages yield five previews and a count of eight."""
        builder = TraceBuilder()
        for index in range(8):
            builder.tool_call(status="error", result_preview=f"distinct {index}")
        builder.model_call()
        found = run(FAILED, builder.build())
        assert len(found[0].previews) == 5
        assert found[0].metrics["failures"] == 8

    def test_r21_waste_is_always_zero(self) -> None:
        """R21: "a failed call is not by itself waste" — attributing it would double count."""
        builder = TraceBuilder()
        for _ in range(3):
            call = builder.model_call(usage=tokens(1_000))
            builder.tool_call(parent=call, status="error", result_preview="boom")
        found = run(FAILED, builder.build())
        assert found[0].wasted == TokenUsage()

    def test_r21_metrics_name_the_first_failing_span(self) -> None:
        """R21: ``first_seq`` is the earliest failure of the group."""
        builder = TraceBuilder()
        builder.tool_call(status="ok")
        builder.tool_call(status="error", result_preview="boom")
        builder.tool_call(status="error", result_preview="boom again")
        builder.model_call()
        found = run(FAILED, builder.build())
        assert found[0].metrics["first_seq"] == 1
        assert found[0].span_seqs == (1, 2)


# =============================================================================
# R22 — unresolved_tool_call
# =============================================================================


class TestUnresolvedToolCallR22:
    """R22: three closed reasons and the truncation carve-out."""

    def test_r22_the_reason_set_is_closed_and_pinned(self) -> None:
        """R22: exactly three reasons, and the five ``unknown_tool`` phrases."""
        assert REASONS == ("no_result", "orphan_result", "unknown_tool")
        assert UNKNOWN_TOOL_PATTERNS == (
            "no such tool",
            "tool not found",
            "unknown tool",
            "is not a recognized tool",
            "unrecognized tool name",
        )

    # -- the carve-out --------------------------------------------------------

    def test_r22_a_trailing_unresolved_call_does_not_fire(self) -> None:
        """R22: the carve-out — a capture taken mid-flight is not a finding.

        This is the shape of essentially every live capture: the transcript ends
        on a tool call whose result has not been written yet. Firing on it would
        teach a reader to ignore the section.
        """
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="missing")
        assert run(UNRESOLVED, builder.build()) == ()

    def test_r22_a_mid_trace_unresolved_call_still_fires(self) -> None:
        """R22: the carve-out is positional, and only the trailing span is carved out.

        AC9's second case: a genuine orphan mid-trace *and* a truncated trailing
        call in one trace. The carve-out must take the second and leave the
        first, or it is not a carve-out, it is a hole.
        """
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="missing", digest=DIGEST_A)
        builder.model_call()
        builder.tool_call(parent=call, status="missing", digest=DIGEST_B)
        found = run(UNRESOLVED, builder.build())
        assert len(found) == 1
        assert found[0].metrics["reason"] == "no_result"
        assert found[0].span_seqs == (1,)
        assert found[0].metrics["occurrences"] == 1

    def test_r22_the_carve_out_is_per_agent(self) -> None:
        """R22: "the highest-``seq`` span *of its agent*" — one carve-out per agent."""
        builder = TraceBuilder()
        builder.tool_call(agent_id="root", status="missing")
        builder.tool_call(agent_id="sub", status="missing")
        assert run(UNRESOLVED, builder.build()) == ()

    def test_r22_an_agent_whose_last_span_is_not_a_tool_call_carves_out_nothing(self) -> None:
        """R22: the carve-out consumes the trailing span whatever it is, and no more."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="missing")
        builder.model_call()
        found = run(UNRESOLVED, builder.build())
        assert len(found) == 1
        assert found[0].span_seqs == (1,)

    def test_r22_a_trailing_unknown_tool_call_is_not_carved_out(self) -> None:
        """R22: the carve-out covers ``no_result`` only; a named failure is real."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="error", result_preview="no such tool: Frobnicate")
        found = run(UNRESOLVED, builder.build())
        assert len(found) == 1
        assert found[0].metrics["reason"] == "unknown_tool"

    def test_r22_two_unresolved_calls_of_one_agent_are_one_finding(self) -> None:
        """R22: the bucket is ``(agent_id, reason)``, so occurrences accumulate."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="missing", digest=DIGEST_A)
        builder.tool_call(parent=call, status="missing", digest=DIGEST_B)
        builder.model_call()
        found = run(UNRESOLVED, builder.build())
        assert len(found) == 1
        assert found[0].metrics["occurrences"] == 2

    # -- unknown_tool ---------------------------------------------------------

    @pytest.mark.parametrize("phrase", UNKNOWN_TOOL_PATTERNS)
    def test_r22_each_pinned_phrase_is_matched(self, phrase: str) -> None:
        """R22, A8: all five phrases, individually — not just the one a fixture used."""
        assert looks_like_unknown_tool(f"Error: {phrase} here")

    @pytest.mark.parametrize("phrase", UNKNOWN_TOOL_PATTERNS)
    def test_r22_each_pinned_phrase_is_matched_case_insensitively(self, phrase: str) -> None:
        """R22: "matches, case-insensitively"."""
        assert looks_like_unknown_tool(phrase.upper())
        assert looks_like_unknown_tool(phrase.title())

    @pytest.mark.parametrize(
        "text",
        ["", "permission denied", "timeout after 30s", "tool found", "no such file or directory"],
    )
    def test_r22_an_unrelated_failure_is_not_an_unknown_tool(self, text: str) -> None:
        """R22: a miss degrades the finding to ``failed_tool_call``, never a false claim."""
        assert not looks_like_unknown_tool(text)

    @pytest.mark.parametrize("phrase", UNKNOWN_TOOL_PATTERNS)
    def test_r22_each_phrase_produces_a_critical_finding_through_the_detector(
        self, phrase: str
    ) -> None:
        """R22: ``unknown_tool`` is ``critical``; the other two reasons are ``warning``."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="error", result_preview=phrase)
        builder.model_call()
        found = run(UNRESOLVED, builder.build())
        assert len(found) == 1
        assert (found[0].severity, found[0].metrics["reason"]) == ("critical", "unknown_tool")

    def test_r22_no_result_is_a_warning(self) -> None:
        """R22: "``warning`` for ``no_result``/``orphan_result``"."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="missing")
        builder.model_call()
        assert run(UNRESOLVED, builder.build())[0].severity == "warning"

    def test_r22_an_errored_call_with_no_phrase_produces_nothing_here(self) -> None:
        """R22: a plain failure belongs to R21, not to this detector."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="error", result_preview="permission denied")
        builder.model_call()
        assert run(UNRESOLVED, builder.build()) == ()

    def test_r22_the_no_previews_mode_degrades_unknown_tool_rather_than_crashing(self) -> None:
        """R22, A-b9: with the result text blanked, ``unknown_tool`` cannot be detected."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="error", result_preview="")
        builder.model_call()
        assert run(UNRESOLVED, builder.build()) == ()

    # -- orphan_result --------------------------------------------------------

    def test_r22_orphan_result_comes_from_the_parse_warning(self) -> None:
        """R22, R10: an orphan produces no span, so the warning is its only trace."""
        builder = TraceBuilder()
        builder.model_call()
        builder.warning("orphan_tool_result", 3)
        found = run(UNRESOLVED, builder.build())
        assert len(found) == 1
        assert found[0].metrics == {
            "occurrences": 3,
            "reason": "orphan_result",
            "tool_name": "<unknown>",
        }

    def test_r22_orphan_result_is_trace_scoped_with_no_agent(self) -> None:
        """R22, A-b7: R22 asks for an agent the normalized model cannot supply.

        R22 fires per ``(agent_id, reason)``, but a ``tool_result`` block whose
        ``tool_use_id`` matches no ``tool_use`` emits no span, and R10's
        aggregated warning carries a count and neither an agent nor a ``seq``.
        The finding is emitted once for the trace with empty ``agent_ids``
        rather than attributed to a guessed agent. Pinned here so the PM ruling
        that settles A-b7 has to change a test.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="root")
        builder.model_call(agent_id="sub")
        builder.warning("orphan_tool_result", 1)
        found = run(UNRESOLVED, builder.build())
        assert found[0].agent_ids == ()
        assert found[0].span_seqs == ()
        assert found[0].severity == "warning"

    def test_r22_no_orphan_warning_means_no_orphan_finding(self) -> None:
        """R22: the arm from below — zero orphans is silence, not a zero-count finding."""
        builder = TraceBuilder()
        builder.model_call()
        assert run(UNRESOLVED, builder.build()) == ()

    def test_r22_a_different_parse_warning_does_not_produce_an_orphan_finding(self) -> None:
        """R22: only ``orphan_tool_result`` counts; the warning enum is wide."""
        builder = TraceBuilder()
        builder.model_call()
        builder.warning("dangling_tool_use", 4)
        builder.warning("unknown_extra_key", 17, detail="cwd")
        assert run(UNRESOLVED, builder.build()) == ()

    def test_r22_all_three_reasons_can_coexist_in_one_trace(self) -> None:
        """R22: the reasons bucket independently, so a trace may carry all three."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="missing", digest=DIGEST_A)
        builder.tool_call(parent=call, status="error", result_preview="unknown tool")
        builder.model_call()
        builder.warning("orphan_tool_result", 2)
        found = run(UNRESOLVED, builder.build())
        assert {str(item.metrics["reason"]) for item in found} == set(REASONS)

    def test_r22_waste_is_always_zero(self) -> None:
        """R22, R17: the requirement names no redundant model calls."""
        builder = TraceBuilder()
        call = builder.model_call(usage=tokens(9_000))
        builder.tool_call(parent=call, status="missing")
        builder.model_call(usage=tokens(9_000))
        found = run(UNRESOLVED, builder.build())
        assert all(item.wasted == TokenUsage() for item in found)


# =============================================================================
# R23 — blocked_agent
# =============================================================================


class TestBlockedAgentR23:
    """R23: a long gap in one agent's timeline that no other agent explains."""

    def test_r23_the_constants_are_the_ones_the_spec_pins(self) -> None:
        """R23: 60 s default, 300 s critical, a 50% coverage rule."""
        assert DetectorConfig().blocked_gap_seconds == 60
        assert CRITICAL_GAP_SECONDS == 300
        assert (COVERAGE_NUMERATOR, COVERAGE_DENOMINATOR) == (1, 2)

    def test_r23_a_fifty_nine_second_gap_is_silent(self) -> None:
        """R23: the default threshold from below."""
        assert run(BLOCKED, two_span_gap(59)) == ()

    def test_r23_a_sixty_second_gap_fires_as_a_warning(self) -> None:
        """R23: ">= config.blocked_gap_seconds" — 60 is the first gap that fires."""
        found = run(BLOCKED, two_span_gap(60))
        assert len(found) == 1
        assert (found[0].severity, found[0].metrics["gap_seconds"]) == ("warning", 60)

    def test_r23_a_two_hundred_and_ninety_nine_second_gap_is_a_warning(self) -> None:
        """R23: "``warning`` for ``gap < 300 s``" — the escalation from below."""
        found = run(BLOCKED, two_span_gap(299))
        assert (found[0].severity, found[0].metrics["gap_seconds"]) == ("warning", 299)

    def test_r23_a_three_hundred_second_gap_is_critical(self) -> None:
        """R23: "``critical`` for ``gap >= 300 s``" — 300 is critical, not 301."""
        found = run(BLOCKED, two_span_gap(300))
        assert (found[0].severity, found[0].metrics["gap_seconds"]) == ("critical", 300)

    def test_r23_a_three_hundred_and_one_second_gap_is_critical(self) -> None:
        """R23: the escalation from above."""
        assert run(BLOCKED, two_span_gap(301))[0].severity == "critical"

    def test_r23_gap_seconds_is_the_floor_of_the_measured_gap(self) -> None:
        """R23: "``floor(gap)``" — a gap of 60.5 s reports 60, never 61."""
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=1_000)
        builder.model_call(start_ms=1_000 + 60_500, end_ms=1_000 + 61_500)
        found = run(BLOCKED, builder.build())
        assert found[0].metrics["gap_seconds"] == 60

    def test_r23_forty_percent_coverage_still_fires(self) -> None:
        """R23: below half covered is not explained."""
        found = run(BLOCKED, two_span_gap(100, covered_seconds=40))
        assert len(found) == 1
        assert found[0].metrics["gap_seconds"] == 100

    def test_r23_forty_nine_percent_coverage_still_fires(self) -> None:
        """R23: the coverage rule from below, one second short of explained."""
        assert len(run(BLOCKED, two_span_gap(100, covered_seconds=49))) == 1

    def test_r23_exactly_fifty_percent_coverage_is_explained(self) -> None:
        """R23: "covers **at least** 50%" — exactly half is enough, and is the boundary."""
        assert run(BLOCKED, two_span_gap(100, covered_seconds=50)) == ()

    def test_r23_fifty_one_percent_coverage_is_explained(self) -> None:
        """R23: the coverage rule from above."""
        assert run(BLOCKED, two_span_gap(100, covered_seconds=51)) == ()

    def test_r23_full_coverage_is_explained(self) -> None:
        """R23: an orchestrator waiting on a subagent is doing what it should."""
        assert run(BLOCKED, two_span_gap(100, covered_seconds=100)) == ()

    def test_r23_coverage_is_a_union_not_a_sum(self) -> None:
        """R23: two subagents running the same 30 s explain 30 s, not 60.

        Below, a 100-second gap has two other-agent spans covering the *same*
        40 seconds. Summing would reach 80% and call the gap explained; the
        union reaches 40% and it fires.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="root", start_ms=0, end_ms=1_000)
        builder.model_call(agent_id="root", start_ms=101_000, end_ms=102_000)
        builder.model_call(agent_id="sub_a", start_ms=1_000, end_ms=41_000)
        builder.model_call(agent_id="sub_b", start_ms=1_000, end_ms=41_000)
        assert len(run(BLOCKED, builder.build())) == 1

    def test_r23_two_disjoint_covering_intervals_add_up(self) -> None:
        """R23: a union of disjoint intervals is their total, which can explain a gap."""
        builder = TraceBuilder()
        builder.model_call(agent_id="root", start_ms=0, end_ms=1_000)
        builder.model_call(agent_id="root", start_ms=101_000, end_ms=102_000)
        builder.model_call(agent_id="sub_a", start_ms=1_000, end_ms=31_000)
        builder.model_call(agent_id="sub_b", start_ms=51_000, end_ms=81_000)
        assert run(BLOCKED, builder.build()) == ()

    def test_r23_coverage_is_clipped_to_the_gap(self) -> None:
        """R23: work outside the gap does not explain the gap."""
        builder = TraceBuilder()
        builder.model_call(agent_id="root", start_ms=0, end_ms=1_000)
        builder.model_call(agent_id="root", start_ms=101_000, end_ms=102_000)
        builder.model_call(agent_id="sub", start_ms=200_000, end_ms=900_000)
        assert len(run(BLOCKED, builder.build())) == 1

    def test_r23_an_agents_own_spans_do_not_explain_its_own_gap(self) -> None:
        """R23: "spans belonging to **other** agents".

        One model call emits a 300-second tool call and then a short one; the
        long span overlaps the gap that follows the short one. Counting an
        agent's own work would explain away exactly the stall R23 exists to
        report.
        """
        builder = TraceBuilder()
        call = builder.model_call(agent_id="root", start_ms=0, end_ms=1_000)
        builder.tool_call(agent_id="root", parent=call, start_ms=1_000, end_ms=301_000)
        builder.tool_call(agent_id="root", parent=call, start_ms=1_000, end_ms=2_000)
        builder.model_call(agent_id="root", start_ms=202_000, end_ms=203_000)
        found = run(BLOCKED, builder.build())
        assert found, "an agent's own overlapping span must not explain its own gap"

    def test_r23_covered_millis_measures_a_union_in_integer_milliseconds(self) -> None:
        """R23: the helper, directly — overlap, adjacency, containment and clipping."""
        assert covered_millis([], at(0), at(1_000)) == 0
        assert covered_millis([(at(0), at(500))], at(0), at(1_000)) == 500
        assert covered_millis([(at(0), at(500)), (at(250), at(750))], at(0), at(1_000)) == 750
        assert covered_millis([(at(0), at(500)), (at(500), at(900))], at(0), at(1_000)) == 900
        assert covered_millis([(at(0), at(200)), (at(600), at(800))], at(0), at(1_000)) == 400
        assert covered_millis([(at(-5_000), at(5_000))], at(0), at(1_000)) == 1_000
        assert covered_millis([(at(2_000), at(3_000))], at(0), at(1_000)) == 0
        assert isinstance(covered_millis([(at(0), at(1))], at(0), at(1_000)), int)

    def test_r23_a_zero_length_interval_covers_nothing(self) -> None:
        """R23: an instantaneous span explains no part of a gap."""
        assert covered_millis([(at(100), at(100))], at(0), at(1_000)) == 0

    def test_r23_the_coverage_index_answers_exactly_what_a_fresh_union_would(self) -> None:
        """R23, BUG-3: one index queried many times equals one union built per query.

        ``blocked_agent`` used to rebuild the whole clip-sort-union for every gap
        it measured, which is what made it quadratic. The index is built once per
        agent and asked per gap, so the property that matters is that the two
        forms agree — including at the ends of a window, where one form clips
        before unioning and the other unions before clipping.

        The intervals carry microsecond components on purpose: the union sums
        whole milliseconds (R23), so a form that truncated at a different point
        would disagree here and nowhere else.
        """
        rnd = random.Random(20260911)
        for _ in range(3_000):
            intervals = []
            for _ in range(rnd.randint(0, 6)):
                low = rnd.randrange(0, 200_000)
                intervals.append((at_micros(low), at_micros(low + rnd.randrange(0, 60_000))))
            index = CoverageIndex(intervals)
            for _ in range(3):
                window_start = at_micros(rnd.randrange(0, 200_000))
                window_end = at_micros(rnd.randrange(0, 260_000))
                assert index.covered(window_start, window_end) == covered_millis(
                    intervals, window_start, window_end
                ), (intervals, window_start, window_end)

    def test_r23_the_coverage_index_is_not_consumed_by_a_query(self) -> None:
        """R23: the same index answers the same window the same way, every time.

        The defect this guards is the shape a "build once, query many" rewrite
        invites: an index that mutates its own state as it walks. Two identical
        queries either side of a different one must agree.
        """
        index = CoverageIndex([(at(0), at(500)), (at(600), at(900))])
        first = index.covered(at(0), at(1_000))
        index.covered(at(700), at(800))
        assert index.covered(at(0), at(1_000)) == first == 800

    def test_r23_a_pair_with_a_missing_endpoint_is_skipped(self) -> None:
        """R23: "where both ``prev.end`` and ``next.start`` are non-null"."""
        builder = TraceBuilder()
        builder.model_call(start_ms=0)
        builder.model_call(start_ms=600_000, end_ms=601_000)
        assert run(BLOCKED, builder.build()) == ()

    def test_r23_an_untimed_span_between_two_timed_ones_breaks_the_pair(self) -> None:
        """R23: gaps are between *consecutive* spans, so an untimed span interrupts one.

        A literal reading of R23, pinned because it is surprising: an untimed
        span sitting between two timed ones hides the ten-minute stall around
        it, since neither consecutive pair has both endpoints.
        """
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=1_000)
        builder.user_message()
        builder.model_call(start_ms=601_000, end_ms=602_000)
        assert run(BLOCKED, builder.build()) == ()

    def test_r23_a_negative_gap_never_fires(self) -> None:
        """R23: "clamped at 0" — out-of-order timestamps are R6's business, not a stall."""
        builder = TraceBuilder()
        builder.model_call(start_ms=600_000, end_ms=700_000)
        builder.model_call(start_ms=0, end_ms=1_000)
        trace = builder.build()
        for threshold in (0, 1, 60):
            assert run(BLOCKED, trace, DetectorConfig(blocked_gap_seconds=threshold)) == ()

    def test_r23_a_threshold_of_zero_fires_on_every_unexplained_gap(self) -> None:
        """R25, R23: the knob's lower extreme still produces a coherent result."""
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=1_000)
        builder.model_call(start_ms=2_000, end_ms=3_000)
        found = run(BLOCKED, builder.build(), DetectorConfig(blocked_gap_seconds=0))
        assert len(found) == 1
        assert found[0].metrics["gap_seconds"] == 1

    def test_r23_an_absurd_threshold_silences_the_detector(self) -> None:
        """R25, R23: the knob's upper extreme is legal and simply never fires."""
        trace = two_span_gap(100_000)
        assert run(BLOCKED, trace, DetectorConfig(blocked_gap_seconds=10**9)) == ()

    def test_r23_the_threshold_is_inclusive_at_the_configured_value(self) -> None:
        """R23: a gap of exactly ``blocked_gap_seconds`` fires."""
        assert len(run(BLOCKED, two_span_gap(30), DetectorConfig(blocked_gap_seconds=30))) == 1
        assert run(BLOCKED, two_span_gap(29), DetectorConfig(blocked_gap_seconds=30)) == ()

    def test_r23_waste_is_always_zero(self) -> None:
        """R23: "waiting costs wall-clock time, not tokens"."""
        builder = TraceBuilder()
        builder.model_call(usage=tokens(5_000), start_ms=0, end_ms=1_000)
        builder.model_call(usage=tokens(5_000), start_ms=601_000, end_ms=602_000)
        assert run(BLOCKED, builder.build())[0].wasted == TokenUsage()

    def test_r23_evidence_and_metrics_name_the_two_spans(self) -> None:
        """R23: ``before_seq`` and ``after_seq`` bracket the gap."""
        found = run(BLOCKED, two_span_gap(120))
        assert found[0].span_seqs == (0, 1)
        assert found[0].metrics["before_seq"] == 0
        assert found[0].metrics["after_seq"] == 1


# =============================================================================
# R24 — anomalous_span
# =============================================================================


class TestAnomalousSpanR24:
    """R24: integer statistics, a population floor, a relative and an absolute gate."""

    def test_r24_the_constants_are_the_ones_the_spec_pins(self) -> None:
        """R24: floor 8, 6x MAD to fire, 12x to escalate, 10,000 tokens, 30,000 ms."""
        assert POPULATION_FLOOR == 8
        assert (MAD_MULTIPLE, CRITICAL_MAD_MULTIPLE) == (6, 12)
        assert (FLOOR_TOKENS, FLOOR_DURATION_MS) == (10_000, 30_000)
        assert DIMENSION_FLOORS == {"duration": 30_000, "tokens": 10_000}
        assert set(DIMENSIONS) == {"duration", "tokens"}

    # -- the population floor -------------------------------------------------

    def test_r24_a_population_of_seven_is_skipped_entirely(self) -> None:
        """R24: "skips entirely when the population size is ``< 8``" — from below."""
        assert run(ANOMALOUS, token_population([100] * 6 + [5_000_000])) == ()

    def test_r24_a_population_of_eight_runs(self) -> None:
        """R24: 8 is the first population with a distribution — the floor from above."""
        found = run(ANOMALOUS, token_population([100] * 7 + [5_000_000]))
        assert len(found) == 1
        assert found[0].metrics["value"] == 5_000_000

    def test_r24_the_population_is_model_calls_with_usage(self) -> None:
        """R24: "all ``model_call`` spans with non-null ``usage``".

        Seven calls carry usage and one does not, so the population is seven and
        the detector skips. A population that counted the usage-less call would
        reach eight and report an outlier against a distribution one of whose
        members has no value at all.
        """
        builder = TraceBuilder()
        for _ in range(6):
            builder.model_call(usage=tokens(100))
        builder.model_call(usage=tokens(5_000_000))
        builder.model_call(usage=None)
        assert run(ANOMALOUS, builder.build()) == ()

    def test_r24_tool_calls_are_not_in_the_population(self) -> None:
        """R24: "``model_call`` spans" — a tool call has no usage to compare."""
        builder = TraceBuilder()
        for _ in range(7):
            builder.model_call(usage=tokens(100))
            builder.tool_call()
        builder.model_call(usage=tokens(5_000_000))
        found = run(ANOMALOUS, builder.build())
        assert len(found) == 1
        assert found[0].metrics["value"] == 5_000_000
        assert found[0].summary.endswith("over 8 calls")

    # -- the relative gate ----------------------------------------------------

    def test_r24_a_value_at_exactly_six_times_the_mad_does_not_fire(self) -> None:
        """R24: "``x > med + 6*mad``" is strict — at the threshold is not above it."""
        values = [0, 1_000, 2_000, 3_000, 4_000, 5_000, 6_000]
        median, mad = 3_000, 2_000
        assert lower_median([*values, median + MAD_MULTIPLE * mad]) == median
        assert mad_of([*values, median + MAD_MULTIPLE * mad], median) == mad
        assert run(ANOMALOUS, token_population([*values, median + MAD_MULTIPLE * mad])) == ()

    def test_r24_a_value_one_above_six_times_the_mad_fires_as_a_warning(self) -> None:
        """R24: one token above the threshold is the smallest firing value."""
        values = [0, 1_000, 2_000, 3_000, 4_000, 5_000, 6_000]
        outlier = 3_000 + MAD_MULTIPLE * 2_000 + 1
        found = run(ANOMALOUS, token_population([*values, outlier]))
        assert len(found) == 1
        assert (found[0].severity, found[0].metrics["value"]) == ("warning", outlier)

    def test_r24_a_value_at_exactly_twelve_times_the_mad_is_still_a_warning(self) -> None:
        """R24: "``critical`` when ``x > med + 12*mad``" — at the threshold is warning."""
        values = [0, 1_000, 2_000, 3_000, 4_000, 5_000, 6_000]
        outlier = 3_000 + CRITICAL_MAD_MULTIPLE * 2_000
        found = run(ANOMALOUS, token_population([*values, outlier]))
        assert (found[0].severity, found[0].metrics["value"]) == ("warning", outlier)

    def test_r24_a_value_one_above_twelve_times_the_mad_is_critical(self) -> None:
        """R24: the escalation from above."""
        values = [0, 1_000, 2_000, 3_000, 4_000, 5_000, 6_000]
        outlier = 3_000 + CRITICAL_MAD_MULTIPLE * 2_000 + 1
        found = run(ANOMALOUS, token_population([*values, outlier]))
        assert (found[0].severity, found[0].metrics["value"]) == ("critical", outlier)

    # -- the absolute floors --------------------------------------------------

    def test_r24_a_token_value_one_below_the_floor_does_not_fire(self) -> None:
        """R24: "``x >= floor_d``" — 9,999 tokens clears the MAD gate and stays silent."""
        assert run(ANOMALOUS, token_population([100] * 7 + [FLOOR_TOKENS - 1])) == ()

    def test_r24_a_token_value_at_exactly_the_floor_fires(self) -> None:
        """R24: the floor is ``>=``, so 10,000 is the first firing token count.

        With a uniform population the MAD is 0 and every value above the median
        clears the relative gate, which makes the absolute floor the only thing
        deciding this case — and makes the difference between ``>= 10_000`` and
        ``> 10_000`` visible in exactly one input.
        """
        found = run(ANOMALOUS, token_population([100] * 7 + [FLOOR_TOKENS]))
        assert len(found) == 1
        assert found[0].metrics["value"] == FLOOR_TOKENS

    def test_r24_a_token_value_one_above_the_floor_fires(self) -> None:
        """R24: the floor from above."""
        assert len(run(ANOMALOUS, token_population([100] * 7 + [FLOOR_TOKENS + 1]))) == 1

    def test_r24_a_duration_one_below_the_floor_does_not_fire(self) -> None:
        """R24: 29,999 ms clears the MAD gate and stays under ``floor_duration_ms``."""
        found = run(ANOMALOUS, duration_population([100] * 7 + [FLOOR_DURATION_MS - 1]))
        assert [item for item in found if item.metrics["dimension"] == "duration"] == []

    def test_r24_a_duration_at_exactly_the_floor_fires(self) -> None:
        """R24: 30,000 ms is the first firing duration."""
        found = run(ANOMALOUS, duration_population([100] * 7 + [FLOOR_DURATION_MS]))
        durations = [item for item in found if item.metrics["dimension"] == "duration"]
        assert len(durations) == 1
        assert durations[0].metrics["value"] == FLOOR_DURATION_MS

    def test_r24_a_duration_one_above_the_floor_fires(self) -> None:
        """R24: the duration floor from above."""
        found = run(ANOMALOUS, duration_population([100] * 7 + [FLOOR_DURATION_MS + 1]))
        assert [item for item in found if item.metrics["dimension"] == "duration"]

    # -- the statistics -------------------------------------------------------

    def test_r24_the_median_is_the_lower_median_of_the_population(self) -> None:
        """R24: index ``(n-1)//2``, so an even population never introduces a ``.5``."""
        values = [10, 20, 30, 40, 50, 60, 70, 5_000_000]
        found = run(ANOMALOUS, token_population(values))
        assert found[0].metrics["median"] == 40
        assert found[0].metrics["mad"] == 20

    def test_r24_mad_is_the_lower_median_of_the_absolute_deviations(self) -> None:
        """R24: ``mad_of``, directly, including the uniform case."""
        assert mad_of([1, 2, 3], 2) == 1
        assert mad_of([100] * 8, 100) == 0
        assert mad_of([0, 1_000, 2_000, 3_000, 4_000, 5_000, 6_000, 7_000], 3_000) == 2_000

    def test_r24_a_zero_mad_leaves_the_absolute_floor_as_the_only_gate(self) -> None:
        """R24: "``mad`` is frequently 0 on a uniform run" — the floors exist for this."""
        found = run(ANOMALOUS, token_population([100] * 7 + [50_000]))
        assert found[0].metrics["mad"] == 0
        assert found[0].severity == "critical"

    def test_r24_the_metrics_report_the_dimension_value_median_mad_and_seq(self) -> None:
        """R24: the metrics are the whole comparison, so a reader can check it."""
        found = run(ANOMALOUS, token_population([100] * 7 + [50_000]))
        assert set(found[0].metrics) == {"dimension", "mad", "median", "seq", "value"}
        assert found[0].metrics["seq"] == 7
        assert found[0].span_seqs == (7,)

    def test_r24_one_finding_per_span_and_dimension(self) -> None:
        """R24: "one finding per ``(span, dimension)``" — a span may be both."""
        builder = TraceBuilder()
        for _ in range(7):
            builder.model_call(usage=tokens(100), start_ms=0, end_ms=100)
        builder.model_call(usage=tokens(500_000), start_ms=0, end_ms=400_000)
        found = run(ANOMALOUS, builder.build())
        assert len(found) == 2
        assert {str(item.metrics["dimension"]) for item in found} == {"duration", "tokens"}
        assert {int(item.metrics["seq"]) for item in found} == {7}

    def test_r24_a_population_with_no_timing_skips_the_duration_dimension(self) -> None:
        """R24: "over spans with both endpoints" — a dimension with no members is skipped."""
        found = run(ANOMALOUS, token_population([100] * 7 + [50_000]))
        assert all(item.metrics["dimension"] == "tokens" for item in found)

    def test_r24_the_population_floor_is_one_gate_not_one_per_dimension(self) -> None:
        """R24, A-b8: the floor is checked once, on the usage population.

        Eight model calls carry usage, so the detector runs; only three of them
        have timing, so the ``duration`` statistics are a median and a MAD of
        three numbers. R24 gates on "the population size" and then defines the
        duration value "over spans with both endpoints", which reads as one gate
        — but it means a duration outlier can be declared against a population
        of three, which is exactly what the floor of 8 exists to prevent for
        tokens. Pinned so a PM ruling on A-b8 has to change a test.
        """
        builder = TraceBuilder()
        for _ in range(5):
            builder.model_call(usage=tokens(100))
        builder.model_call(usage=tokens(100), start_ms=0, end_ms=100)
        builder.model_call(usage=tokens(100), start_ms=0, end_ms=100)
        builder.model_call(usage=tokens(100), start_ms=0, end_ms=400_000)
        found = run(ANOMALOUS, builder.build())
        durations = [item for item in found if item.metrics["dimension"] == "duration"]
        assert len(durations) == 1
        assert durations[0].summary.endswith("over 3 calls")

    def test_r24_waste_is_always_zero(self) -> None:
        """R24, R17: "an outlier is not necessarily waste"."""
        found = run(ANOMALOUS, token_population([100] * 7 + [5_000_000]))
        assert found[0].wasted == TokenUsage()

    def test_r24_a_uniform_population_below_the_floors_is_silent(self) -> None:
        """R24: the negative arm — a well-behaved run produces nothing."""
        assert run(ANOMALOUS, token_population([100] * 12)) == ()

    def test_r24_two_outliers_are_two_findings(self) -> None:
        """R24: the detector reports every qualifying span, not just the largest."""
        found = run(ANOMALOUS, token_population([100] * 7 + [50_000, 60_000]))
        assert len(found) == 2
        assert {int(item.metrics["value"]) for item in found} == {50_000, 60_000}

    def test_r24_all_arithmetic_stays_integral(self) -> None:
        """R24: "All arithmetic is ``int``" — no float reaches a metric."""
        found = run(ANOMALOUS, token_population([1, 2, 3, 4, 5, 6, 7, 5_000_000]))
        for value in found[0].metrics.values():
            assert not isinstance(value, float)
        assert isinstance(found[0].metrics["median"], int)


def test_r18_r24_no_detector_is_quadratic_in_a_ten_thousand_span_trace() -> None:
    """R13, R18-R24: a large trace completes in a bounded time on every detector.

    The bound is deliberately generous — this is a blow-up guard, not a
    benchmark, and a shared CI runner is not a stopwatch. What it catches is the
    class of change that turns a linear pass into a nested one.
    """
    builder = TraceBuilder()
    for index in range(5_000):
        call = builder.model_call(
            usage=tokens(100),
            start_ms=index * 1_000,
            end_ms=index * 1_000 + 100,
        )
        builder.tool_call(parent=call, start_ms=index * 1_000, end_ms=index * 1_000 + 50)
    trace = builder.build()
    assert len(trace.spans) == 10_000
    for detector in (REPEATED, LOOP, STORM, FAILED, UNRESOLVED, BLOCKED, ANOMALOUS):
        started = time.perf_counter()
        run(detector, trace)
        elapsed = time.perf_counter() - started
        assert elapsed < 60.0, f"{detector.slug} took {elapsed:.1f}s on 10,000 spans"
