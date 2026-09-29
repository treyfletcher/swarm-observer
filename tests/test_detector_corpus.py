"""The fixture corpus contract (T6) and the boundary pairs it must contain.

This module is *infrastructure*: it asserts that every checked-in trace fixture
agrees with its checked-in expectation file, and that the corpus contains a case
on **each side** of every threshold R18-R24 pins. It is not a substitute for
per-requirement detector tests — those are the tester's, and they should drive
the exported constants and helpers directly (``find_loop``, ``lower_median``,
``covered_millis``, ``merge_runs``) rather than through 20 fixtures.

Why the corpus needs its own guard at all: increment 1's review mutation-tested
the suite and found that flipping one character of R9's collapse condition left
everything green, because no checked-in input could tell the two behaviours
apart. A fixture set that cannot distinguish a threshold from the value next to
it is the same defect wearing detector clothes. So the table below names, for
every pinned boundary, the fixture that sits on each side of it and the arm it
must be on — and it reads the arms from a **fresh detector run**, not from the
expectation files, so it cannot agree with itself.

Requirements exercised through the corpus: R18 (repeated_tool_call), R19
(agent_loop), R20 (retry_storm), R21 (failed_tool_call), R22
(unresolved_tool_call), R23 (blocked_agent), R24 (anomalous_span), R25
(DetectorConfig).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm_observer.detect.base import DetectorConfig
from swarm_observer.detect.registry import DETECTOR_SLUGS, run_detectors

from .detector_corpus import (
    EXPECTATION_SUFFIX,
    FIXTURE_DIR,
    expectation_path,
    expectation_paths,
    expectation_problems,
    findings_by_detector,
    fixture_names,
    fixture_paths,
    load_expectation,
    load_trace,
    observed_document,
)

#: Each entry: the boundary, the fixture on the silent side, the fixture on the
#: firing side, the detector, and a metric the firing side must report. A
#: boundary with only one side in the corpus is a boundary the corpus cannot see.
BOUNDARY_PAIRS: tuple[tuple[str, str, str, str, dict[str, int]], ...] = (
    (
        "R18: a tool group of 1 occurrence versus 2",
        "clean_single_agent",
        "loop_two_repeats",
        "repeated_tool_call",
        {"occurrences": 2},
    ),
    (
        "R19: a cycle repeated twice versus three times",
        "loop_two_repeats",
        "loop_three_repeats",
        "agent_loop",
        {"repeats": 3, "period": 2},
    ),
    (
        "R19: three repeats (warning) versus four (critical)",
        "loop_two_repeats",
        "loop_four_repeats",
        "agent_loop",
        {"repeats": 4, "period": 1},
    ),
    (
        "R19: period 1 wins over period 2 when both cycles match",
        "loop_two_repeats",
        "loop_ambiguous_period",
        "agent_loop",
        {"period": 1, "repeats": 6},
    ),
    (
        "R21: one failure (info) versus two (warning)",
        "clean_single_agent",
        "failed_tool_twice",
        "failed_tool_call",
        {"failures": 2},
    ),
    (
        "R20: two error spans in a window versus three",
        "api_error_rate_limit",
        "retry_storm_warning",
        "retry_storm",
        {"errors": 3},
    ),
    (
        "R23: a 59 s gap versus a 60 s gap",
        "clean_single_agent",
        "gaps_unexplained",
        "blocked_agent",
        {"gap_seconds": 60},
    ),
    (
        "R23: 50% subagent coverage (explained) versus 25% (not)",
        "multi_agent_subagent",
        "gaps_explained",
        "blocked_agent",
        {"gap_seconds": 120},
    ),
    (
        "R23: 40% coverage is not explained, so the rule is 50% and not looser",
        "multi_agent_subagent",
        "gaps_partly_explained",
        "blocked_agent",
        {"gap_seconds": 120},
    ),
    (
        "R23: an agent's own overlapping span does not explain its own gap",
        "multi_agent_subagent",
        "parallel_tool_calls",
        "blocked_agent",
        {"gap_seconds": 305},
    ),
    (
        "R24: a population of 7 versus 8",
        "outlier_population_seven",
        "outlier_tokens_and_duration",
        "anomalous_span",
        {"value": 50_000},
    ),
    (
        "R24: a value at exactly med + 6*mad versus one above it",
        "outlier_at_mad_threshold",
        "outlier_above_mad_threshold",
        "anomalous_span",
        {"value": 16_001, "mad": 2_000, "median": 4_000},
    ),
    (
        "R24: 12*MAD exactly (warning) versus just above it (critical)",
        "outlier_at_mad_threshold",
        "outlier_above_critical_threshold",
        "anomalous_span",
        {"value": 29_000, "mad": 2_000, "median": 4_000},
    ),
    (
        "R24: 9,999 tokens (under the floor) versus 50,000",
        "outlier_below_floors",
        "outlier_tokens_and_duration",
        "anomalous_span",
        {"value": 50_000},
    ),
    (
        "R21: one failure (info) versus three (warning)",
        "clean_single_agent",
        "retry_storm_warning",
        "failed_tool_call",
        {"failures": 3},
    ),
    (
        "R22: a trailing unresolved call (carved out) versus a mid-trace one",
        "clean_single_agent",
        "truncated_dangling_tool_use",
        "unresolved_tool_call",
        {"occurrences": 1},
    ),
)


def fired_slugs(name: str) -> set[str]:
    """The detectors that actually fire on one fixture, from a fresh run."""
    trace = load_trace(FIXTURE_DIR / f"{name}.jsonl")
    return {slug for slug, found in findings_by_detector(trace).items() if found}


class TestCorpusShapeT6:
    """T6: the corpus and its expectation files are complete and paired."""

    def test_t6_the_corpus_is_not_empty(self) -> None:
        """T6: a corpus of zero fixtures would make every check below vacuous."""
        fixtures = fixture_paths()
        assert len(fixtures) >= 15, f"the corpus has only {len(fixtures)} fixtures"
        assert "hostile.jsonl" in {path.name for path in fixtures}, (
            "R51's hostile fixture must be part of the corpus"
        )

    def test_t6_every_fixture_has_an_expectation_file(self) -> None:
        """T6: a fixture with no expectation is a fixture nothing checks."""
        missing = [path.name for path in fixture_paths() if not expectation_path(path).is_file()]
        assert not missing, f"fixtures with no {'.expected.json'} beside them: {missing}"

    def test_t6_every_expectation_file_has_a_fixture(self) -> None:
        """T6: an orphan expectation is a renamed fixture nobody noticed."""
        stems = set(fixture_names())
        orphans = [
            path.name
            for path in expectation_paths()
            if path.name[: -len(EXPECTATION_SUFFIX)] not in stems
        ]
        assert not orphans, f"expectation files with no fixture: {orphans}"

    def test_t6_no_sidecar_metadata_sits_beside_the_corpus(self) -> None:
        """T6: the corpus must be a function of the ``.jsonl`` files alone.

        A1's ``agent-<id>.meta.json`` sidecar is read opportunistically and is
        deliberately *not* covered by ``trace_id`` (R5), so one appearing here
        would change every pinned finding id without changing a fixture byte.
        """
        assert not list(FIXTURE_DIR.glob("*.meta.json"))


@pytest.mark.parametrize("name", fixture_names())
def test_r18_r24_fixture_matches_its_checked_in_expectation(name: str) -> None:
    """R18-R24: one fixture's detectors produce exactly what its expectation names."""
    fixture = FIXTURE_DIR / f"{name}.jsonl"
    problems = expectation_problems(
        name, load_expectation(fixture), observed_document(fixture), DETECTOR_SLUGS
    )
    assert not problems, "\n  ".join(["fixture corpus contract (T6):", *problems])


@pytest.mark.parametrize(
    ("label", "silent", "firing", "slug", "metrics"),
    BOUNDARY_PAIRS,
    ids=[entry[0] for entry in BOUNDARY_PAIRS],
)
def test_r18_r24_the_corpus_holds_both_sides_of_every_pinned_boundary(
    label: str, silent: str, firing: str, slug: str, metrics: dict[str, int]
) -> None:
    """R18-R24: each threshold has a fixture just under it and one just over it.

    Read from a fresh detector run rather than from the expectation files, so
    this cannot pass by agreeing with the document it is meant to police.
    """
    assert slug not in fired_slugs(silent), (
        f"{label}: {slug} fires on {silent}, which is supposed to be the silent side"
    )
    trace = load_trace(FIXTURE_DIR / f"{firing}.jsonl")
    found = findings_by_detector(trace)[slug]
    assert found, f"{label}: {slug} does not fire on {firing}"
    assert any(
        all(item.metrics.get(key) == value for key, value in metrics.items()) for item in found
    ), (
        f"{label}: no finding on {firing} reports {metrics}; got "
        f"{[dict(item.metrics) for item in found]}"
    )


class TestDetectorConfigR25:
    """R25: the one integer a detector reads, and the ``--detector`` restriction."""

    def test_r25_config_has_exactly_the_two_pinned_fields(self) -> None:
        """R25: no detector reads any other tunable in v1 (A9)."""
        assert set(DetectorConfig.model_fields) == {"blocked_gap_seconds", "enabled"}
        assert DetectorConfig().blocked_gap_seconds == 60
        assert DetectorConfig().enabled is None
        with pytest.raises(ValueError):
            DetectorConfig(unknown_field=1)  # type: ignore[call-arg]

    def test_r25_blocked_gap_seconds_is_the_only_thing_that_moves_a_report(self) -> None:
        """R25: raising the gap by one second silences the 60 s finding and nothing else.

        The point of A9 — every other threshold is a module constant — is that a
        golden report is a function of the trace and this one integer. If the
        knob did nothing, the requirement would be satisfied by a dead field.
        """
        trace = load_trace(FIXTURE_DIR / "gaps_unexplained.jsonl")
        default = run_detectors(trace, DetectorConfig())
        raised = run_detectors(trace, DetectorConfig(blocked_gap_seconds=61))
        gaps_default = sorted(
            item.metrics["gap_seconds"] for item in default if item.detector == "blocked_agent"
        )
        gaps_raised = sorted(
            item.metrics["gap_seconds"] for item in raised if item.detector == "blocked_agent"
        )
        assert gaps_default == [60, 400]
        assert gaps_raised == [400]
        assert [item.detector for item in default if item.detector != "blocked_agent"] == [
            item.detector for item in raised if item.detector != "blocked_agent"
        ]

    def test_r25_enabled_restricts_the_run_to_the_named_slugs(self) -> None:
        """R25: ``enabled`` is ``--detector``'s mechanism, and ``None`` means all."""
        trace = load_trace(FIXTURE_DIR / "retry_storm.jsonl")
        every = {item.detector for item in run_detectors(trace, DetectorConfig())}
        one = {
            item.detector
            for item in run_detectors(trace, DetectorConfig(enabled=frozenset({"retry_storm"})))
        }
        assert one == {"retry_storm"}
        assert one < every
        assert run_detectors(trace, DetectorConfig(enabled=frozenset())) == ()


def test_r22_the_truncation_carve_out_leaves_a_parse_warning_behind() -> None:
    """R22: the trailing unresolved call is counted, not reported (AC9).

    The two halves of the carve-out live in different modules — the mapper
    counts ``dangling_tool_use`` (R10) and the detector skips the same span — so
    the corpus checks they agree about which span that is.
    """
    trace = load_trace(FIXTURE_DIR / "truncated_dangling_tool_use.jsonl")
    dangling = [warning for warning in trace.warnings if warning.code == "dangling_tool_use"]
    assert [warning.count for warning in dangling] == [1]
    missing = [span.seq for span in trace.spans if span.tool_result_status == "missing"]
    assert missing == [2, 6], missing
    findings = findings_by_detector(trace)["unresolved_tool_call"]
    assert [list(item.span_seqs) for item in findings] == [[2]], (
        "the trailing span (seq 6) must be carved out and the mid-trace one (seq 2) reported"
    )


def test_r18_the_corpus_keeps_two_tools_that_share_one_input_digest() -> None:
    """R18: the group key is ``(tool_name, tool_input_digest)``, not the digest alone.

    ``clean_single_agent`` carries a ``Glob`` and a ``Grep`` called with
    byte-identical arguments. Without that pair, a group key that dropped the
    tool name would be indistinguishable from the right one on the whole corpus
    — which is the mutation-survives shape increment 1's review found in R9.
    """
    trace = load_trace(FIXTURE_DIR / "clean_single_agent.jsonl")
    by_digest: dict[str, set[str]] = {}
    for span in trace.spans:
        if span.kind == "tool_call" and span.tool_input_digest and span.tool_name:
            by_digest.setdefault(span.tool_input_digest, set()).add(span.tool_name)
    shared = [names for names in by_digest.values() if len(names) > 1]
    assert shared, "no two tools in the clean fixture share an input digest any more"
    assert findings_by_detector(trace)["repeated_tool_call"] == ()


def test_t6_fixture_directory_is_where_the_spec_says_it_is() -> None:
    """T6: the corpus lives at ``tests/fixtures/traces/``, where R48 looks for it."""
    assert Path(__file__).resolve().parent / "fixtures" / "traces" == FIXTURE_DIR
    assert FIXTURE_DIR.is_dir()
