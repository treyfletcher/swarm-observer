"""The detector contract, clause by clause: R13, R14, R15, R16, R17, R25.

The corpus proves the detectors *discriminate*. This module proves the shared
machinery underneath them holds its promises independently of any one detector,
because every one of those promises is load-bearing somewhere the detectors
cannot see:

* R13's sort key is what makes a report's Findings section a function of the
  trace rather than of registry iteration order.
* R14's caps and orderings are what stop a detector shipping a finding a
  renderer cannot lay out.
* R15's id is a suppression key in v2 — so "stable across processes" is a
  contract, not a nicety, and is checked here against a real subprocess rather
  than against a second call in the same interpreter.
* R16's tool-name guard is the *only* trace-derived value R16 lets near the
  findings table, and increment 1's B5 was one metacharacter of exactly this
  kind of pattern.
* R17's arithmetic decides a dollar figure in increment 3.
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from swarm_observer.detect.base import (
    FINDING_ID_HEX,
    MAX_EVIDENCE_SPANS,
    MAX_PREVIEWS,
    NON_CONFORMING_TOOL_NAME,
    SEVERITIES,
    SEVERITY_RANK,
    TOOL_NAME_PATTERN,
    UNKNOWN_TOOL_NAME,
    DetectorConfig,
    Finding,
    attribute_waste,
    build_finding,
    constrain_tool_name,
    finding_id,
    lower_median,
    millis_between,
    model_calls,
    seq_by_span_id,
    sort_findings,
    span_duration_ms,
    spans_by_agent,
)
from swarm_observer.detect.registry import (
    ALL_DETECTORS,
    DETECTOR_SLUGS,
    detector_by_slug,
    run_detectors,
    selected_detectors,
    slugs_of,
)
from swarm_observer.model.trace import TokenUsage

from .synthetic_traces import (
    DIGEST_A,
    SYNTHETIC_TRACE_ID,
    TraceBuilder,
    at,
    hex_id,
    tokens,
)

REPO = Path(__file__).resolve().parent.parent

VALID_ID = "agent_loop:" + "0" * FINDING_ID_HEX


def _finding(**overrides: Any) -> Finding:
    """A minimally valid ``Finding``, with ``overrides`` applied."""
    fields: dict[str, Any] = {
        "detector": "agent_loop",
        "finding_id": VALID_ID,
        "severity": "warning",
        "summary": "a summary with no trace bytes in it",
    }
    fields.update(overrides)
    return Finding(**fields)


# --- R13: the protocol, the registry, the sort key -----------------------------


class TestDetectorProtocolR13:
    """R13: every registered detector wears the protocol and is pure."""

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=DETECTOR_SLUGS)
    def test_r13_each_detector_has_the_protocol_attributes(self, detector: Any) -> None:
        """R13: ``slug``, ``title``, ``default_severity``, ``run``."""
        assert isinstance(detector.slug, str) and detector.slug
        assert isinstance(detector.title, str) and detector.title
        assert detector.default_severity in SEVERITIES
        assert callable(detector.run)

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=DETECTOR_SLUGS)
    def test_r13_each_detector_returns_a_tuple_of_findings_tagged_with_its_slug(
        self, detector: Any
    ) -> None:
        """R13: ``run`` returns findings, and every one names the detector that made it."""
        builder = TraceBuilder()
        call = builder.model_call(usage=tokens(50_000), start_ms=0, end_ms=1_000)
        builder.tool_call(parent=call, status="error", result_preview="no such tool")
        builder.tool_call(parent=call, status="error", result_preview="no such tool")
        found = detector.run(builder.build(), DetectorConfig())
        assert isinstance(found, tuple)
        assert all(isinstance(item, Finding) for item in found)
        assert all(item.detector == detector.slug for item in found)

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=DETECTOR_SLUGS)
    def test_r13_each_detector_returns_findings_already_sorted(self, detector: Any) -> None:
        """R13: the returned order is ``(severity_rank, slug, finding_id)``."""
        trace = self._busy_trace()
        found = detector.run(trace, DetectorConfig())
        assert list(found) == list(sort_findings(found))

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=DETECTOR_SLUGS)
    def test_r13_each_detector_leaves_the_trace_unmutated(self, detector: Any) -> None:
        """R13: a detector is a pure function — the trace it read is unchanged."""
        trace = self._busy_trace()
        before = trace.model_dump_json()
        detector.run(trace, DetectorConfig())
        assert trace.model_dump_json() == before

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=DETECTOR_SLUGS)
    def test_r13_each_detector_is_idempotent_within_one_process(self, detector: Any) -> None:
        """R13: no clock, no randomness — two calls agree exactly."""
        trace = self._busy_trace()
        first = detector.run(trace, DetectorConfig())
        second = detector.run(trace, DetectorConfig())
        assert [item.model_dump() for item in first] == [item.model_dump() for item in second]

    def _busy_trace(self) -> Any:
        """A trace shaped to make every detector produce something."""
        builder = TraceBuilder()
        for index in range(9):
            call = builder.model_call(
                agent_id="root",
                usage=tokens(100 if index else 900_000),
                start_ms=index * 1_000,
                end_ms=index * 1_000 + (400_000 if index == 1 else 10),
            )
            builder.tool_call(
                agent_id="root",
                parent=call,
                status="error",
                result_preview="unknown tool" if index == 2 else "boom",
            )
        builder.model_call(agent_id="root", start_ms=10_000_000, end_ms=10_001_000)
        builder.warning("orphan_tool_result", 2)
        return builder.build()


class TestFindingSortOrderR13:
    """R13: the sort key is all three components, in order."""

    def test_r13_findings_sort_by_severity_rank_first(self) -> None:
        """R13: ``info`` < ``warning`` < ``critical``."""
        assert SEVERITY_RANK == {"info": 0, "warning": 1, "critical": 2}
        assert SEVERITIES == ("info", "warning", "critical")
        items = [
            _finding(severity="critical"),
            _finding(severity="info"),
            _finding(severity="warning"),
        ]
        assert [item.severity for item in sort_findings(items)] == [
            "info",
            "warning",
            "critical",
        ]

    def test_r13_findings_of_equal_severity_sort_by_detector_slug(self) -> None:
        """R13: the slug is the second key — and it is *not* redundant with the id.

        The two findings below are ordered one way by slug and the other way by
        ``finding_id``. A sort key that dropped the slug would still be
        deterministic, and would still pass a corpus in which no two detectors
        tie on severity; it would put ``retry_storm`` before ``agent_loop``.
        """
        early_slug_late_id = _finding(detector="agent_loop", finding_id="agent_loop:ffffffffffff")
        late_slug_early_id = _finding(detector="retry_storm", finding_id="retry_storm:000000000000")
        ordered = sort_findings([late_slug_early_id, early_slug_late_id])
        assert [item.detector for item in ordered] == ["agent_loop", "retry_storm"]

    def test_r13_the_slug_component_is_not_redundant_with_the_id_prefix(self) -> None:
        """R13: sorting by ``finding_id`` alone is *not* the same total order.

        Every ``finding_id`` begins with its own slug (R14), so for the seven
        slugs registered today the ``detector`` component of the sort key is
        redundant, and a mutation dropping it changes nothing observable. That
        is an accident of the current names rather than a property of the key:
        ``:`` sorts *after* every digit, so as soon as one slug is a prefix of
        another — ``retry`` beside ``retry2``, the obvious way a v2 detector
        gets named — the two keys disagree. Pinned on ``sort_findings``, which
        is the function R13 specifies, rather than on today's registry.
        """
        short = _finding(detector="retry", finding_id="retry:ffffffffffff")
        longer = _finding(detector="retry2", finding_id="retry2:000000000000")
        assert sorted([short.finding_id, longer.finding_id]) == [
            longer.finding_id,
            short.finding_id,
        ], "the premise: by id alone, the longer slug sorts first"
        assert [item.detector for item in sort_findings([longer, short])] == ["retry", "retry2"]

    def test_r13_findings_of_equal_severity_and_slug_sort_by_finding_id(self) -> None:
        """R13: the id is the final tie-break, so the order is total."""
        high = _finding(finding_id="agent_loop:ffffffffffff")
        low = _finding(finding_id="agent_loop:000000000000")
        assert [item.finding_id for item in sort_findings([high, low])] == [
            low.finding_id,
            high.finding_id,
        ]

    def test_r13_sort_findings_accepts_an_empty_input(self) -> None:
        """R13: an empty run is a legitimate result, not an error."""
        assert sort_findings([]) == ()


class TestRegistryR13:
    """R13: ``ALL_DETECTORS`` is the single source of truth, in a fixed order."""

    def test_r13_the_registry_is_non_empty_with_unique_slugs(self) -> None:
        """R13: a duplicate slug would make ``detector_by_slug`` ambiguous."""
        assert ALL_DETECTORS
        assert len(set(DETECTOR_SLUGS)) == len(DETECTOR_SLUGS)

    def test_r13_registry_slugs_match_the_detector_objects(self) -> None:
        """R13: ``DETECTOR_SLUGS`` is derived from the tuple, in the same order."""
        assert slugs_of(ALL_DETECTORS) == DETECTOR_SLUGS

    def test_r13_registry_order_is_the_specs_r18_to_r24_order(self) -> None:
        """R13: the order is fixed — ``detectors`` prints it and a report groups by it."""
        assert DETECTOR_SLUGS == (
            "repeated_tool_call",
            "agent_loop",
            "retry_storm",
            "failed_tool_call",
            "unresolved_tool_call",
            "blocked_agent",
            "anomalous_span",
        )

    @pytest.mark.parametrize("slug", DETECTOR_SLUGS)
    def test_r13_detector_by_slug_round_trips(self, slug: str) -> None:
        """R13: every registered slug resolves to the detector that declares it."""
        assert detector_by_slug(slug).slug == slug

    def test_r13_detector_by_slug_raises_for_an_unknown_slug(self) -> None:
        """R13: naming a detector that does not exist is a ``KeyError``, not ``None``."""
        with pytest.raises(KeyError):
            detector_by_slug("no_such_detector")

    def test_r13_run_detectors_returns_one_globally_sorted_tuple(self) -> None:
        """R13: the merged result carries the same order each detector guarantees."""
        builder = TraceBuilder()
        for index in range(9):
            call = builder.model_call(usage=tokens(100 if index else 800_000))
            builder.tool_call(parent=call, status="error", result_preview="boom")
        trace = builder.build()
        merged = run_detectors(trace, DetectorConfig())
        assert list(merged) == list(sort_findings(merged))
        assert {item.detector for item in merged} <= set(DETECTOR_SLUGS)


# --- R14: the Finding model ----------------------------------------------------


class TestFindingShapeR14:
    """R14: every prose ordering and cap is a constraint, not a convention."""

    def test_r14_a_minimal_finding_carries_the_documented_defaults(self) -> None:
        """R14: empty evidence, zero waste, and no cost until the cost engine lands."""
        finding = _finding()
        assert finding.span_seqs == ()
        assert finding.agent_ids == ()
        assert finding.previews == ()
        assert finding.metrics == {}
        assert finding.wasted == TokenUsage()
        assert finding.wasted_cost_usd is None

    def test_r14_a_finding_is_frozen(self) -> None:
        """R14: a finding a renderer received cannot be edited underneath it."""
        finding = _finding()
        with pytest.raises(ValidationError):
            finding.severity = "critical"  # type: ignore[misc]

    def test_r14_wasted_cost_usd_accepts_a_decimal_when_increment_3_fills_it(self) -> None:
        """R14: the field is typed ``Decimal | None``, not float."""
        assert _finding(wasted_cost_usd=Decimal("0.000001")).wasted_cost_usd == Decimal("0.000001")

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            ("descending span_seqs", {"span_seqs": (3, 1)}),
            ("repeated span_seqs", {"span_seqs": (1, 1)}),
            ("negative span_seq", {"span_seqs": (-1,)}),
            ("over the evidence cap", {"span_seqs": tuple(range(MAX_EVIDENCE_SPANS + 1))}),
            ("descending agent_ids", {"agent_ids": ("b", "a")}),
            ("repeated agent_ids", {"agent_ids": ("a", "a")}),
            ("over the preview cap", {"previews": tuple("abcdef")}),
            ("unsorted metrics keys", {"metrics": {"b": 1, "a": 2}}),
            ("id not prefixed with its detector", {"finding_id": "retry_storm:" + "0" * 12}),
            ("id with the wrong hex length", {"finding_id": "agent_loop:0" * 1}),
            ("severity outside the ladder", {"severity": "fatal"}),
            ("empty summary", {"summary": ""}),
            ("an unknown field", {"not_a_field": 1}),
            ("a float in metrics", {"metrics": {"a": 1.5}}),
        ],
    )
    def test_r14_an_invalid_finding_shape_is_a_construction_error(
        self, label: str, overrides: dict[str, Any]
    ) -> None:
        """R14: the model refuses the shape rather than carrying it to a report."""
        with pytest.raises(ValidationError):
            _finding(**overrides)

    def test_r14_the_evidence_cap_admits_exactly_fifty_spans(self) -> None:
        """R14: 50 is accepted and 51 is not — the cap is at 50, not near it."""
        assert _finding(span_seqs=tuple(range(MAX_EVIDENCE_SPANS))).span_seqs[-1] == 49
        with pytest.raises(ValidationError):
            _finding(span_seqs=tuple(range(MAX_EVIDENCE_SPANS + 1)))

    def test_r14_the_preview_cap_admits_exactly_five_entries(self) -> None:
        """R14: 5 is accepted and 6 is not."""
        assert len(_finding(previews=tuple("abcde")).previews) == MAX_PREVIEWS
        with pytest.raises(ValidationError):
            _finding(previews=tuple("abcdef"))

    def test_r14_metrics_values_are_ints_and_strings_only(self) -> None:
        """R14: JSON scalars only — a nested object never reaches a metrics table."""
        assert _finding(metrics={"a": 1, "b": "slug"}).metrics == {"a": 1, "b": "slug"}
        with pytest.raises(ValidationError):
            _finding(metrics={"a": {"nested": 1}})


class TestBuildFindingNormalizesR14:
    """R14, R15: ``build_finding`` normalizes before it hashes."""

    def test_r14_build_finding_sorts_dedups_and_caps_evidence_spans(self) -> None:
        """R14: an unsorted, repeating, over-long evidence list becomes a legal one."""
        trace = TraceBuilder().build()
        finding = build_finding(
            trace=trace,
            detector="agent_loop",
            severity="warning",
            summary="s",
            metrics={},
            span_seqs=[5, 1, 5, *range(100)],
            agent_ids=["b", "a", "b"],
        )
        assert finding.span_seqs == tuple(range(MAX_EVIDENCE_SPANS))
        assert finding.agent_ids == ("a", "b")

    def test_r14_build_finding_caps_evidence_at_fifty_not_fifty_one(self) -> None:
        """R14: a cap one too generous is a ``ValidationError``, not a longer table.

        ``build_finding`` slices before it constructs, and the model then
        refuses more than 50. The two numbers must be the same number: a slice
        of 51 makes every detector with a long run raise instead of report.
        """
        trace = TraceBuilder().build()
        finding = build_finding(
            trace=trace,
            detector="agent_loop",
            severity="warning",
            summary="s",
            metrics={},
            span_seqs=range(MAX_EVIDENCE_SPANS + 25),
            agent_ids=["a"],
        )
        assert len(finding.span_seqs) == MAX_EVIDENCE_SPANS

    def test_r14_build_finding_sorts_metrics_keys(self) -> None:
        """R14: metrics keys are sorted whatever order a detector passed them in."""
        trace = TraceBuilder().build()
        finding = build_finding(
            trace=trace,
            detector="agent_loop",
            severity="warning",
            summary="s",
            metrics={"zeta": 1, "alpha": 2},
            span_seqs=[],
            agent_ids=[],
        )
        assert list(finding.metrics) == ["alpha", "zeta"]

    def test_r14_build_finding_drops_empty_previews_and_caps_at_five(self) -> None:
        """R14: an absent preview is not an empty row in the evidence list."""
        trace = TraceBuilder().build()
        finding = build_finding(
            trace=trace,
            detector="agent_loop",
            severity="warning",
            summary="s",
            metrics={},
            span_seqs=[],
            agent_ids=[],
            previews=["", "a", "", "b", "c", "d", "e", "f"],
        )
        assert finding.previews == ("a", "b", "c", "d", "e")

    def test_r14_build_finding_defaults_wasted_to_zero(self) -> None:
        """R14, R17: a detector with no redundancy notion emits a zero ``TokenUsage``."""
        trace = TraceBuilder().build()
        finding = build_finding(
            trace=trace,
            detector="agent_loop",
            severity="warning",
            summary="s",
            metrics={},
            span_seqs=[],
            agent_ids=[],
        )
        assert finding.wasted == TokenUsage()


# --- R15: the finding id -------------------------------------------------------


class TestFindingIdR15:
    """R15: the id is a function of the evidence and of nothing else."""

    def test_r15_the_id_has_the_pinned_shape(self) -> None:
        """R15: ``<detector>:<12 lowercase hex>``."""
        value = finding_id(
            trace_id=SYNTHETIC_TRACE_ID, detector="agent_loop", metrics={"a": 1}, span_seqs=[1]
        )
        slug, _, digest = value.partition(":")
        assert slug == "agent_loop"
        assert len(digest) == FINDING_ID_HEX
        assert set(digest) <= set("0123456789abcdef")

    def test_r15_the_id_matches_the_specs_canonical_payload_byte_for_byte(self) -> None:
        """R15: recomputed here from the requirement's own words, not from the code."""
        import hashlib

        metrics: dict[str, Any] = {"occurrences": 3, "tool_name": "Bash"}
        spans = [4, 9]
        payload = json.dumps(
            {
                "trace": SYNTHETIC_TRACE_ID,
                "detector": "repeated_tool_call",
                "metrics": metrics,
                "spans": spans,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        assert (
            finding_id(
                trace_id=SYNTHETIC_TRACE_ID,
                detector="repeated_tool_call",
                metrics=metrics,
                span_seqs=spans,
            )
            == f"repeated_tool_call:{expected}"
        )

    def test_r15_the_same_evidence_gives_the_same_id(self) -> None:
        """R15: stable across runs — the whole point of a suppression key."""
        args: dict[str, Any] = {
            "trace_id": SYNTHETIC_TRACE_ID,
            "detector": "agent_loop",
            "metrics": {"period": 2, "repeats": 3},
            "span_seqs": [0, 1, 2],
        }
        assert finding_id(**args) == finding_id(**args)

    def test_r15_metrics_key_order_does_not_move_the_id(self) -> None:
        """R15: ``sort_keys`` removes every dict-ordering dependency."""
        first = finding_id(
            trace_id=SYNTHETIC_TRACE_ID,
            detector="agent_loop",
            metrics={"a": 1, "b": 2},
            span_seqs=[],
        )
        second = finding_id(
            trace_id=SYNTHETIC_TRACE_ID,
            detector="agent_loop",
            metrics={"b": 2, "a": 1},
            span_seqs=[],
        )
        assert first == second

    @pytest.mark.parametrize(
        ("label", "changed"),
        [
            ("a different trace", {"trace_id": "00000000000000ff"}),
            ("a different detector", {"detector": "retry_storm"}),
            ("a different metric value", {"metrics": {"period": 3, "repeats": 3}}),
            ("an extra metric", {"metrics": {"period": 2, "repeats": 3, "extra": 0}}),
            ("different evidence spans", {"span_seqs": [0, 1, 3]}),
            ("one fewer evidence span", {"span_seqs": [0, 1]}),
        ],
    )
    def test_r15_changed_evidence_changes_the_id(self, label: str, changed: dict[str, Any]) -> None:
        """R15: the id moves exactly when the finding moves."""
        base: dict[str, Any] = {
            "trace_id": SYNTHETIC_TRACE_ID,
            "detector": "agent_loop",
            "metrics": {"period": 2, "repeats": 3},
            "span_seqs": [0, 1, 2],
        }
        assert finding_id(**base) != finding_id(**{**base, **changed})

    def test_r15_two_findings_from_one_trace_have_distinct_ids(self) -> None:
        """R15: distinct evidence, distinct ids — ids are usable as keys."""
        builder = TraceBuilder()
        first = builder.model_call()
        builder.tool_call(parent=first, tool_name="Bash", digest=hex_id(1))
        builder.tool_call(parent=first, tool_name="Bash", digest=hex_id(1))
        second = builder.model_call()
        builder.tool_call(parent=second, tool_name="Read", digest=hex_id(2))
        builder.tool_call(parent=second, tool_name="Read", digest=hex_id(2))
        found = detector_by_slug("repeated_tool_call").run(builder.build(), DetectorConfig())
        assert len(found) == 2
        assert found[0].finding_id != found[1].finding_id

    def test_r15_every_id_in_a_whole_run_is_unique(self) -> None:
        """R15: no two findings anywhere in one trace share an id."""
        builder = TraceBuilder()
        for index in range(12):
            call = builder.model_call(
                usage=tokens(100 if index else 900_000),
                start_ms=index * 1_000,
                end_ms=index * 1_000 + 10,
            )
            builder.tool_call(parent=call, status="error", result_preview=f"boom {index}")
        found = run_detectors(builder.build(), DetectorConfig())
        ids = [item.finding_id for item in found]
        assert len(set(ids)) == len(ids)

    @pytest.mark.parametrize("seed", ["0", "1", "2", "random"])
    def test_r15_ids_are_identical_in_a_fresh_process_under_any_hash_seed(self, seed: str) -> None:
        """R15: stable across processes and ``PYTHONHASHSEED`` — checked out of process."""
        expected = finding_id(
            trace_id=SYNTHETIC_TRACE_ID,
            detector="agent_loop",
            metrics={"period": 2, "repeats": 3, "start_seq": 0, "end_seq": 5},
            span_seqs=[0, 1, 2, 3, 4, 5],
        )
        script = (
            "from swarm_observer.detect.base import finding_id;"
            f"print(finding_id(trace_id={SYNTHETIC_TRACE_ID!r}, detector='agent_loop',"
            " metrics={'period': 2, 'repeats': 3, 'start_seq': 0, 'end_seq': 5},"
            " span_seqs=[0, 1, 2, 3, 4, 5]))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO)},
            check=True,
        )
        assert proc.stdout.strip() == expected


# --- R16: the summary and the tool-name guard ----------------------------------


class TestSummaryAndToolNameR16:
    """R16: nothing the trace chose reaches ``summary``, and ``metrics`` is guarded."""

    def test_r16_the_pattern_is_the_one_the_spec_pins(self) -> None:
        """R16: the alphabet and the length bound are the requirement's, verbatim."""
        assert TOOL_NAME_PATTERN == r"^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$"
        assert NON_CONFORMING_TOOL_NAME == "<non-conforming>"

    @pytest.mark.parametrize(
        "name",
        ["Bash", "_private", "a", "A" * 64, "tool.name", "ns:tool", "kebab-tool", "T00l_1"],
    )
    def test_r16_a_conforming_tool_name_is_carried_verbatim(self, name: str) -> None:
        """R16: a legal name needs no preview overflow."""
        assert constrain_tool_name(name) == (name, None)

    @pytest.mark.parametrize(
        ("label", "name"),
        [
            ("a trailing newline", "Bash\n"),
            ("a leading newline", "\nBash"),
            ("an embedded newline", "Bash\nX"),
            ("a trailing carriage return", "Bash\r"),
            ("a line separator", "Bash\u2028"),
            ("a NUL", "Bash\x00"),
            ("a space", "Ba sh"),
            ("a leading digit", "1Bash"),
            ("empty", ""),
            ("one over the length bound", "A" * 65),
            ("a script tag", "<script>alert(1)</script>"),
            ("a right-to-left override", "‮gnirts"),
            ("a zero-width space", "Ba​sh"),
            ("a slash", "../../etc/passwd"),
        ],
    )
    def test_r16_a_non_conforming_tool_name_is_replaced_and_overflowed(
        self, label: str, name: str
    ) -> None:
        """R16: the metrics value is the literal slug; the original goes to previews."""
        value, overflow = constrain_tool_name(name)
        assert value == NON_CONFORMING_TOOL_NAME
        assert overflow == name

    def test_r16_the_trailing_newline_trap_is_closed_by_fullmatch(self) -> None:
        """R16: ``$`` also matches before a trailing newline; ``fullmatch`` does not.

        This is increment 1's B5 in the detector layer. Under ``re.match`` — or
        under ``fullmatch`` with the ``$`` anchor doing the work — ``"Bash\\n"``
        is a conforming tool name and the newline rides into a ``metrics`` value
        a later renderer will emit. The guard must reject it, and the *reason* it
        rejects it must be that the whole string is required to match, not that
        the alphabet happens to exclude one character.
        """
        import re

        assert re.match(TOOL_NAME_PATTERN, "Bash\n") is not None, (
            "the premise of this test is that `match` accepts a trailing newline"
        )
        assert constrain_tool_name("Bash\n") == (NON_CONFORMING_TOOL_NAME, "Bash\n")

    def test_r16_an_absent_tool_name_is_non_conforming_with_nothing_to_preview(self) -> None:
        """R16: ``None`` has no original to push into previews."""
        assert constrain_tool_name(None) == (NON_CONFORMING_TOOL_NAME, None)

    def test_r16_the_unknown_tool_slug_is_an_enumerated_value_not_trace_text(self) -> None:
        """R16, R22: ``orphan_result`` has no tool name; the slug is ours."""
        assert UNKNOWN_TOOL_NAME == "<unknown>"

    def test_r16_no_summary_from_any_detector_contains_trace_text(self) -> None:
        """R16: summaries are integers and enumerated slugs, on a hostile trace."""
        payload = "</script><script>alert(1)</script>"
        builder = TraceBuilder()
        for index in range(9):
            call = builder.model_call(
                usage=tokens(100 if index else 900_000),
                start_ms=index * 1_000,
                end_ms=index * 1_000 + 10,
                text_preview=payload,
            )
            builder.tool_call(
                parent=call,
                tool_name=payload,
                status="error",
                input_preview=payload,
                result_preview=f"no such tool {payload}",
            )
        builder.model_call(start_ms=9_000_000, end_ms=9_001_000)
        trace = builder.build()
        found = run_detectors(trace, DetectorConfig())
        assert found, "the probe needs findings to inspect"
        for item in found:
            assert payload not in item.summary
            assert "<" not in item.summary
            for key, value in item.metrics.items():
                if isinstance(value, str):
                    assert payload not in value, (key, value)


# --- R17: waste attribution ----------------------------------------------------


class TestWasteAttributionR17:
    """R17: element-wise sum over the *distinct model_call* spans named redundant."""

    def _trace(self) -> Any:
        builder = TraceBuilder()
        builder.model_call(
            usage=TokenUsage(
                input_tokens=1,
                output_tokens=2,
                cache_read_input_tokens=4,
                cache_creation_5m_tokens=8,
                cache_creation_1h_tokens=16,
            )
        )
        builder.model_call(usage=TokenUsage(input_tokens=100))
        builder.model_call(usage=None)
        builder.tool_call(tool_name="Bash")
        return builder.build()

    def test_r17_sums_element_wise_across_the_five_components(self) -> None:
        """R17: every component is summed, none is dropped."""
        assert attribute_waste(self._trace(), [0, 1]) == TokenUsage(
            input_tokens=101,
            output_tokens=2,
            cache_read_input_tokens=4,
            cache_creation_5m_tokens=8,
            cache_creation_1h_tokens=16,
        )

    def test_r17_counts_a_repeated_seq_once(self) -> None:
        """R17: *distinct* spans — a detector may hand in one parent per occurrence."""
        assert attribute_waste(self._trace(), [0, 0, 0]).input_tokens == 1

    def test_r17_ignores_a_span_that_is_not_a_model_call(self) -> None:
        """R17: a repeated *tool* call costs nothing by itself."""
        assert attribute_waste(self._trace(), [3]) == TokenUsage()

    def test_r17_ignores_a_model_call_with_no_recorded_usage(self) -> None:
        """R17: a span with no usage has nothing to attribute."""
        assert attribute_waste(self._trace(), [2]) == TokenUsage()

    def test_r17_an_empty_attribution_is_a_zero_token_usage(self) -> None:
        """R17: the zero case is a zero, not a ``None``."""
        assert attribute_waste(self._trace(), []) == TokenUsage()

    @pytest.mark.parametrize("seq", [-1, -100])
    def test_r17_ignores_a_negative_seq(self, seq: int) -> None:
        """R17: a negative index must not wrap round to the end of the span list."""
        assert attribute_waste(self._trace(), [seq]) == TokenUsage()

    def test_r17_ignores_a_seq_one_past_the_end_rather_than_raising(self) -> None:
        """R17: the upper bound is ``>= len``, and one past the end is the case that proves it.

        A bound written ``> len(trace.spans)`` looks equivalent and is not:
        ``spans[len(spans)]`` is an ``IndexError``, so a detector naming a seq
        one past the end would crash the run instead of contributing nothing.
        """
        trace = self._trace()
        assert attribute_waste(trace, [len(trace.spans)]) == TokenUsage()
        assert attribute_waste(trace, [len(trace.spans) + 5]) == TokenUsage()

    def test_r17_a_zero_usage_span_contributes_zero(self) -> None:
        """R17: a recorded but empty usage block is not an error."""
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage())
        assert attribute_waste(builder.build(), [0]) == TokenUsage()

    def test_r17_large_component_values_stay_exact_integers(self) -> None:
        """R17: no float ever enters the sum, so huge counts stay exact."""
        big = 2**62
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage(input_tokens=big))
        builder.model_call(usage=TokenUsage(input_tokens=big))
        assert attribute_waste(builder.build(), [0, 1]).input_tokens == 2 * big


class TestSharedTraceViewsR17:
    """R13, R17: the structural helpers every detector shares."""

    def test_r17_spans_by_agent_follows_the_agent_run_span_seqs(self) -> None:
        """R2, R13: detectors and reports must agree about what belongs to an agent."""
        builder = TraceBuilder()
        builder.model_call(agent_id="root")
        builder.model_call(agent_id="sub")
        builder.model_call(agent_id="root")
        trace = builder.build()
        grouped = spans_by_agent(trace)
        assert {key: [span.seq for span in value] for key, value in grouped.items()} == {
            "root": [0, 2],
            "sub": [1],
        }

    def test_r17_model_calls_returns_only_model_calls_in_seq_order(self) -> None:
        """R13: the shared view is a filter, not a re-order."""
        builder = TraceBuilder()
        builder.tool_call()
        builder.model_call()
        builder.system_event()
        builder.model_call()
        assert [span.seq for span in model_calls(builder.build())] == [1, 3]

    def test_r17_seq_by_span_id_resolves_a_parent_reference(self) -> None:
        """R18: a ``parent_span_id`` becomes a seq the waste attribution can use."""
        builder = TraceBuilder()
        parent = builder.model_call()
        builder.tool_call(parent=parent)
        trace = builder.build()
        assert seq_by_span_id(trace)[parent.span_id] == 0

    def test_r23_millis_between_is_integer_arithmetic_end_to_end(self) -> None:
        """R23, R24: ``timedelta.total_seconds()`` returns a float; this does not."""
        value = millis_between(at(0), at(60_000))
        assert isinstance(value, int)
        assert value == 60_000
        assert millis_between(at(0), at(1)) == 1
        assert millis_between(at(1_000), at(0)) == -1_000
        assert millis_between(at(0), at(86_400_000 * 3 + 7)) == 86_400_000 * 3 + 7

    def test_r24_span_duration_clamps_a_negative_duration_to_zero(self) -> None:
        """R24: a span whose ``end`` precedes its ``start`` measures zero, not a negative.

        R6 already counts this at parse time as ``negative_duration``. Letting
        the negative through would drag R24's median below every real value and
        make the outlier threshold meaningless.
        """
        builder = TraceBuilder()
        span = builder.model_call(start_ms=5_000, end_ms=1_000)
        assert span_duration_ms(span) == 0

    def test_r24_span_duration_is_none_without_both_endpoints(self) -> None:
        """R24: a span with no timing has no duration, which is not the same as zero."""
        builder = TraceBuilder()
        assert span_duration_ms(builder.model_call(start_ms=0)) is None
        assert span_duration_ms(builder.model_call(end_ms=0)) is None
        assert span_duration_ms(builder.model_call()) is None
        assert span_duration_ms(builder.model_call(start_ms=7, end_ms=7)) == 0

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            ([5], 5),
            ([1, 2], 1),
            ([2, 1], 1),
            ([1, 2, 3], 2),
            ([1, 2, 3, 4], 2),
            ([4, 3, 2, 1], 2),
            ([1, 1, 1, 1, 9], 1),
        ],
    )
    def test_r24_lower_median_is_the_element_at_n_minus_one_over_two(
        self, values: list[int], expected: int
    ) -> None:
        """R24: pinned as the *lower* median so an even population never yields a ``.5``."""
        assert lower_median(values) == expected

    def test_r24_lower_median_of_an_empty_population_raises(self) -> None:
        """R24: there is no median of nothing, and guessing one would be a silent zero."""
        with pytest.raises(ValueError, match="empty population"):
            lower_median([])


# --- R25: detector configuration -----------------------------------------------


class TestDetectorConfigR25:
    """R25: exactly two tunables, frozen, and one of them moves exactly one detector."""

    def test_r25_the_defaults_are_the_ones_the_spec_pins(self) -> None:
        """R25: ``blocked_gap_seconds=60`` and ``enabled=None`` (meaning all)."""
        config = DetectorConfig()
        assert config.blocked_gap_seconds == 60
        assert config.enabled is None

    def test_r25_the_model_has_exactly_two_fields(self) -> None:
        """R25: "exactly" — a third knob would make a golden report a function of more."""
        assert set(DetectorConfig.model_fields) == {"blocked_gap_seconds", "enabled"}

    def test_r25_the_config_is_frozen(self) -> None:
        """R25: a detector cannot retune itself mid-run."""
        config = DetectorConfig()
        with pytest.raises(ValidationError):
            config.blocked_gap_seconds = 5  # type: ignore[misc]

    def test_r25_an_unknown_knob_is_a_construction_error(self) -> None:
        """R25: ``extra="forbid"`` — a typo'd knob fails loudly rather than doing nothing."""
        with pytest.raises(ValidationError):
            DetectorConfig(nope=1)

    def test_r25_a_negative_gap_threshold_is_refused(self) -> None:
        """R25: the field is ``ge=0``; a negative threshold has no meaning."""
        with pytest.raises(ValidationError):
            DetectorConfig(blocked_gap_seconds=-1)

    @pytest.mark.parametrize("value", [0, 1, 60, 300, 10**9])
    def test_r25_the_gap_threshold_accepts_its_whole_documented_range(self, value: int) -> None:
        """R25: zero and absurd values are both legal; behaviour is R23's business."""
        assert DetectorConfig(blocked_gap_seconds=value).blocked_gap_seconds == value

    def test_r25_enabled_none_selects_every_registered_detector(self) -> None:
        """R25: ``None`` means all, in registry order."""
        assert slugs_of(selected_detectors(DetectorConfig())) == DETECTOR_SLUGS

    def test_r25_enabled_selects_a_subset_in_registry_order(self) -> None:
        """R25: the selection is filtered, never re-ordered by the caller's set."""
        config = DetectorConfig(enabled=frozenset({"anomalous_span", "repeated_tool_call"}))
        assert slugs_of(selected_detectors(config)) == ("repeated_tool_call", "anomalous_span")

    def test_r25_enabled_ignores_a_slug_that_is_not_registered(self) -> None:
        """R25: selection is a filter over the registry, so an unknown slug adds nothing."""
        config = DetectorConfig(enabled=frozenset({"repeated_tool_call", "ghost"}))
        assert slugs_of(selected_detectors(config)) == ("repeated_tool_call",)

    def test_r25_an_empty_enabled_set_selects_nothing(self) -> None:
        """R25: an empty restriction is empty, not "unset"."""
        assert selected_detectors(DetectorConfig(enabled=frozenset())) == ()

    def test_r25_run_detectors_honours_the_enabled_restriction(self) -> None:
        """R25: the restriction reaches the merged run, not just the selection helper."""
        builder = TraceBuilder()
        call = builder.model_call()
        builder.tool_call(parent=call, status="error", result_preview="boom")
        builder.tool_call(parent=call, status="error", result_preview="boom")
        builder.model_call()
        trace = builder.build()
        everything = run_detectors(trace, DetectorConfig())
        assert {item.detector for item in everything} > {"failed_tool_call"}
        only = run_detectors(trace, DetectorConfig(enabled=frozenset({"failed_tool_call"})))
        assert {item.detector for item in only} == {"failed_tool_call"}

    def test_r25_the_gap_knob_moves_blocked_agent_and_nothing_else(self) -> None:
        """R25, A9: one integer, one detector — a golden report's only tunable."""
        trace = TraceBuilder()
        trace.model_call(agent_id="root", start_ms=0, end_ms=1_000)
        trace.tool_call(
            agent_id="root",
            status="error",
            result_preview="boom",
            start_ms=1_000,
            end_ms=1_000,
        )
        trace.model_call(agent_id="root", start_ms=41_000, end_ms=42_000)
        built = trace.build()
        loose = run_detectors(built, DetectorConfig(blocked_gap_seconds=60))
        tight = run_detectors(built, DetectorConfig(blocked_gap_seconds=40))
        assert [item for item in loose if item.detector != "blocked_agent"] == [
            item for item in tight if item.detector != "blocked_agent"
        ]
        assert not [item for item in loose if item.detector == "blocked_agent"]
        assert len([item for item in tight if item.detector == "blocked_agent"]) == 1

    def test_r25_no_detector_reads_a_tunable_outside_the_config(self) -> None:
        """R25: thresholds in R18-R24 are module constants, not configuration.

        Asserted structurally: the only attribute names any detector reads off
        ``config`` are the two R25 declares. A detector reaching for a third
        would be reading something no CLI can set and no golden can pin.
        """
        import ast

        detect_dir = REPO / "swarm_observer" / "detect"
        reads: set[str] = set()
        for path in sorted(detect_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "config"
                ):
                    reads.add(node.attr)
        assert reads <= set(DetectorConfig.model_fields), sorted(reads)


def test_r14_r17_a_finding_carrying_evidence_is_internally_consistent() -> None:
    """R14, R17: waste is attributed only to spans the finding could name.

    Not a tautology: it is the property that would break first if a detector
    handed ``attribute_waste`` the tool-call seqs instead of their parents.
    """
    builder = TraceBuilder()
    parent = builder.model_call(usage=tokens(500))
    builder.tool_call(parent=parent, digest=DIGEST_A)
    builder.tool_call(parent=parent, digest=DIGEST_A)
    trace = builder.build()
    found = detector_by_slug("repeated_tool_call").run(trace, DetectorConfig())
    assert len(found) == 1
    assert found[0].span_seqs == (1, 2)
    assert found[0].wasted == tokens(500)
