"""Detector-coverage integrity: the registry, the two arms, the finding contract.

R48 is the requirement this module exists for, and it has a shape worth stating
plainly. "Every detector has a fixture that fires it" alone is satisfied by a
detector that fires on *everything*; "every detector has a fixture that stays
silent" alone is satisfied by a detector that fires on *nothing*. Only both arms
together say the detector discriminates. The second test R48 asks for — an AST
scan of ``detect/`` — closes the remaining hole: a detector that exists but is
not in ``ALL_DETECTORS`` is outside both arms, outside every report, and outside
anybody's notice.

The rest of the module is the contract the corpus lets us assert once rather than
per detector: R13's sorted, pure detector protocol; R14's caps and orderings;
R15's evidence-derived ids, checked for stability across environments and across
processes; R16's promise that no trace-derived byte reaches a summary or a
metric, probed against the hostile fixture where every field is a payload; and
R17's attribution, which must only ever sum ``model_call`` usage.

Per-requirement detector cases (R18-R24 clause by clause, the ``find_loop``
table, the ``lower_median`` edge cases) are the tester's and are deliberately not
duplicated here.

R50's canary for this module's guards is
``tests/canaries/test_canary_detector_coverage_dropped.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest

from swarm_observer.detect.base import (
    MAX_EVIDENCE_SPANS,
    MAX_PREVIEWS,
    SEVERITY_RANK,
    DetectorConfig,
    Finding,
    attribute_waste,
    constrain_tool_name,
    finding_id,
)
from swarm_observer.detect.registry import (
    ALL_DETECTORS,
    DETECTOR_SLUGS,
    detector_by_slug,
    run_detectors,
    selected_detectors,
)
from swarm_observer.model.trace import TokenUsage

from .detector_corpus import (
    DETECT_PACKAGE,
    FIXTURE_DIR,
    coverage_arms,
    coverage_problems,
    detector_classes_in,
    findings_by_detector,
    fixture_names,
    fixture_paths,
    load_expectations,
    load_trace,
    registry_problems,
)
from .harness import DETERMINISM_ENVIRONMENTS, REPO, assert_deterministic, sha256_text

#: The R51 payloads that must never leave a preview field (R16).
HOSTILE_PROBES: tuple[str, ...] = (
    "</script><script>",
    "onerror=alert(1)",
    "javascript:alert(1)",
    "{{7*7}}",
    "../../etc/passwd",
    "AKIAIOSFODNN7EXAMPLE",
    "sk-ant-api03",
    "BEGIN RSA PRIVATE KEY",
    "\u202e",
    "`whoami`",
)

#: The child program the cross-process determinism check runs. Kept as a string
#: so the check really does start a fresh interpreter: an in-process loop cannot
#: observe a ``PYTHONHASHSEED`` difference, because the seed is fixed at start.
_DIGEST_PROGRAM = """
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from swarm_observer.detect.base import DetectorConfig
from swarm_observer.detect.registry import run_detectors
from swarm_observer.ingest.claude_code.mapper import ClaudeCodeSource
from swarm_observer.ingest.source import IngestLimits

payload = []
for path in sorted(Path(sys.argv[2]).glob("*.jsonl")):
    trace = ClaudeCodeSource().load([path], IngestLimits())
    for finding in run_detectors(trace, DetectorConfig()):
        payload.append(
            [
                path.name,
                finding.detector,
                finding.finding_id,
                finding.severity,
                dict(finding.metrics),
                list(finding.span_seqs),
                list(finding.agent_ids),
            ]
        )
text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
sys.stdout.write(hashlib.sha256(text.encode("utf-8")).hexdigest())
"""


def corpus_findings() -> list[tuple[str, Finding]]:
    """Every finding the whole corpus produces, as ``(fixture stem, finding)``."""
    collected: list[tuple[str, Finding]] = []
    for fixture in fixture_paths():
        trace = load_trace(fixture)
        for finding in run_detectors(trace, DetectorConfig()):
            collected.append((fixture.stem, finding))
    return collected


class TestRegistryIsTheSourceOfTruthR13:
    """R13: ``ALL_DETECTORS`` is the only place a detector can exist."""

    def test_r13_the_registry_is_populated_and_its_slugs_are_unique(self) -> None:
        """R13: an empty registry would make every R48 check below vacuous."""
        assert len(ALL_DETECTORS) == 7, [detector.slug for detector in ALL_DETECTORS]
        assert len(set(DETECTOR_SLUGS)) == len(DETECTOR_SLUGS)
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
    def test_r13_every_detector_wears_the_protocol(self, slug: str) -> None:
        """R13: slug, title, default severity and a ``run`` that takes the two arguments."""
        detector = detector_by_slug(slug)
        assert detector.slug == slug
        assert detector.title.strip()
        assert detector.default_severity in SEVERITY_RANK
        clean = detector.run(load_trace(FIXTURE_DIR / "clean_single_agent.jsonl"), DetectorConfig())
        assert isinstance(clean, tuple)
        assert clean == (), f"{slug} fires on the clean fixture, which is R48's silent arm"

    def test_r13_an_unknown_slug_is_a_key_error_not_a_silent_miss(self) -> None:
        """R13: an unknown slug raises rather than silently selecting nothing.

        The CLI turns that into a usage error and exit 3 in increment 3; what R13
        needs here is that the registry does not answer for a detector it does
        not have.
        """
        with pytest.raises(KeyError):
            detector_by_slug("no_such_detector")
        assert selected_detectors(DetectorConfig(enabled=frozenset({"no_such_detector"}))) == ()

    @pytest.mark.parametrize("name", fixture_names())
    def test_r13_each_detector_returns_its_findings_already_sorted(self, name: str) -> None:
        """R13: sorted by ``(severity_rank, slug, finding_id)`` before it returns."""
        trace = load_trace(FIXTURE_DIR / f"{name}.jsonl")
        for slug, found in findings_by_detector(trace).items():
            keys = [
                (SEVERITY_RANK[item.severity], item.detector, item.finding_id) for item in found
            ]
            assert keys == sorted(keys), f"{name}/{slug} returned findings out of order"

    @pytest.mark.parametrize("name", fixture_names())
    def test_r13_run_detectors_merges_into_one_globally_sorted_tuple(self, name: str) -> None:
        """R13: the report's Findings section is a function of the trace and the config."""
        trace = load_trace(FIXTURE_DIR / f"{name}.jsonl")
        merged = run_detectors(trace, DetectorConfig())
        keys = [(SEVERITY_RANK[item.severity], item.detector, item.finding_id) for item in merged]
        assert keys == sorted(keys)
        per_detector = sum(len(found) for found in findings_by_detector(trace).values())
        assert len(merged) == per_detector


class TestDetectorCoverageR48:
    """R48: both arms, for every registered detector, plus the registry AST scan."""

    def test_r48_the_corpus_has_expectations_to_measure(self) -> None:
        """R48: a coverage check over an empty corpus passes without measuring anything."""
        expectations = load_expectations()
        assert len(expectations) >= 15
        for name, expectation in expectations.items():
            assert set(expectation["detectors"]) == set(DETECTOR_SLUGS), (
                f"{name} does not name every registered detector"
            )

    @pytest.mark.parametrize("slug", DETECTOR_SLUGS)
    def test_r48_every_detector_has_a_fixture_that_fires_it(self, slug: str) -> None:
        """R48a: a detector with no positive fixture may simply be dead code."""
        fires, _ = coverage_arms(load_expectations())
        assert fires.get(slug), f"no fixture fires {slug}"

    @pytest.mark.parametrize("slug", DETECTOR_SLUGS)
    def test_r48_every_detector_has_a_fixture_that_stays_silent(self, slug: str) -> None:
        """R48b: a detector that fires on everything is not a detector."""
        _, silent = coverage_arms(load_expectations())
        assert silent.get(slug), f"no fixture is free of {slug}"

    def test_r48_the_live_corpus_has_no_coverage_problems(self) -> None:
        """R48: the real corpus, through the same function the canary breaks."""
        assert coverage_problems(load_expectations()) == []

    def test_r48_the_ast_scan_finds_exactly_the_registered_detectors(self) -> None:
        """R48: a detector-shaped class outside ``ALL_DETECTORS`` fails the suite."""
        scanned = detector_classes_in(DETECT_PACKAGE)
        assert set(scanned) == set(DETECTOR_SLUGS), scanned
        assert registry_problems() == []

    def test_r48_the_ast_scan_reads_modules_rather_than_imported_objects(self) -> None:
        """R48: the scan must see a detector nobody imports — that is the case it is for."""
        assert detector_classes_in(DETECT_PACKAGE)["agent_loop"] == "agent_loop.py"
        assert detector_classes_in(DETECT_PACKAGE / "does_not_exist") == {}


class TestFindingContractR14R15:
    """R14's caps and orderings, and R15's evidence-derived, stable ids."""

    def test_r14_the_caps_are_the_numbers_r14_states(self) -> None:
        """R14: 50 evidence spans and 5 previews, pinned by value.

        The parametrized guard below builds its over-cap case from these
        constants, so raising one would move the guard with it. A literal pin is
        what stops the cap from being widened silently.
        """
        assert MAX_EVIDENCE_SPANS == 50
        assert MAX_PREVIEWS == 5

    def test_r14_every_corpus_finding_satisfies_the_pinned_shape(self) -> None:
        """R14: ascending capped evidence, sorted agents, sorted metric keys."""
        collected = corpus_findings()
        assert len(collected) >= 15, f"only {len(collected)} findings across the corpus"
        for name, finding in collected:
            where = f"{name}/{finding.finding_id}"
            assert list(finding.span_seqs) == sorted(set(finding.span_seqs)), where
            assert len(finding.span_seqs) <= MAX_EVIDENCE_SPANS, where
            assert list(finding.agent_ids) == sorted(set(finding.agent_ids)), where
            assert len(finding.previews) <= MAX_PREVIEWS, where
            assert list(finding.metrics) == sorted(finding.metrics), where
            assert finding.wasted_cost_usd is None, f"{where}: cost lands in increment 3"
            assert all(isinstance(value, int | str) for value in finding.metrics.values()), where

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("span_seqs", (3, 1)),
            ("span_seqs", (1, 1)),
            ("span_seqs", tuple(range(51))),
            ("span_seqs", tuple(range(MAX_EVIDENCE_SPANS + 1))),
            ("agent_ids", ("b", "a")),
            ("previews", ("a", "b", "c", "d", "e", "f")),
            ("metrics", {"b": 1, "a": 2}),
        ],
    )
    def test_r14_the_pinned_shape_is_enforced_not_merely_documented(
        self, field: str, value: Any
    ) -> None:
        """R14: each cap and ordering rejects a violation at construction."""
        base: dict[str, Any] = {
            "detector": "agent_loop",
            "finding_id": "agent_loop:0123456789ab",
            "severity": "warning",
            "summary": "probe",
        }
        base[field] = value
        with pytest.raises(ValueError):
            Finding(**base)

    def test_r15_every_finding_id_is_recomputable_from_the_finding(self) -> None:
        """R15: the id is a function of trace id, slug, metrics and evidence spans."""
        for fixture in fixture_paths():
            trace = load_trace(fixture)
            for finding in run_detectors(trace, DetectorConfig()):
                assert finding.finding_id == finding_id(
                    trace_id=trace.trace_id,
                    detector=finding.detector,
                    metrics=finding.metrics,
                    span_seqs=finding.span_seqs,
                ), f"{fixture.stem}: {finding.finding_id} is not derived from its evidence"

    def test_r15_changing_the_evidence_changes_the_id(self) -> None:
        """R15: ids must move when the finding does, or they are not suppression keys."""
        base = {"trace_id": "0123456789abcdef", "detector": "agent_loop"}
        original = finding_id(**base, metrics={"repeats": 3}, span_seqs=[1, 2])
        assert original != finding_id(**base, metrics={"repeats": 4}, span_seqs=[1, 2])
        assert original != finding_id(**base, metrics={"repeats": 3}, span_seqs=[1, 3])
        assert original != finding_id(
            trace_id="fedcba9876543210",
            detector="agent_loop",
            metrics={"repeats": 3},
            span_seqs=[1, 2],
        )
        assert original == finding_id(**base, metrics={"repeats": 3}, span_seqs=[1, 2])

    def test_r15_the_corpus_findings_are_identical_across_the_environment_matrix(self) -> None:
        """R15: ids and ordering do not move with ``TZ``, ``LC_ALL`` or the hash seed."""

        def produce() -> str:
            return json.dumps(
                [
                    [name, finding.finding_id, finding.severity, dict(finding.metrics)]
                    for name, finding in corpus_findings()
                ],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )

        assert assert_deterministic(produce, environments=DETERMINISM_ENVIRONMENTS)

    @pytest.mark.parametrize("seed", ["0", "1", "random"])
    def test_r15_a_separate_process_produces_the_same_findings(self, seed: str) -> None:
        """R15: ``PYTHONHASHSEED`` is fixed at interpreter start, so this needs a subprocess."""
        result = subprocess.run(
            [sys.executable, "-c", _DIGEST_PROGRAM, str(REPO), str(FIXTURE_DIR)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        expected = sha256_text(
            json.dumps(
                [
                    [
                        f"{name}.jsonl",
                        finding.detector,
                        finding.finding_id,
                        finding.severity,
                        dict(finding.metrics),
                        list(finding.span_seqs),
                        list(finding.agent_ids),
                    ]
                    for name, finding in corpus_findings()
                ],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
        assert result.stdout == expected


class TestNoTraceTextInSummariesR16:
    """R16: the findings table is bytes this codebase authored."""

    def test_r16_the_hostile_fixture_actually_reaches_the_findings(self) -> None:
        """R16: without findings on the hostile trace the probe below is vacuous."""
        trace = load_trace(FIXTURE_DIR / "hostile.jsonl")
        findings = run_detectors(trace, DetectorConfig())
        assert len(findings) >= 3, findings
        previews = " ".join(text for finding in findings for text in finding.previews)
        present = [probe for probe in HOSTILE_PROBES if probe in previews]
        assert present, (
            "no R51 payload reached any preview field, so the summary/metrics probe "
            "below cannot distinguish 'escaped' from 'never arrived'"
        )

    def test_r16_no_payload_reaches_a_summary_or_a_metric_value(self) -> None:
        """R16: only integers and swarm-observer's own slugs enter summaries and metrics."""
        offenders: list[str] = []
        for name, finding in corpus_findings():
            haystack = (
                finding.summary + " " + " ".join(str(value) for value in finding.metrics.values())
            )
            offenders.extend(
                f"{name}/{finding.finding_id}: {probe!r}"
                for probe in HOSTILE_PROBES
                if probe in haystack
            )
        assert not offenders, offenders

    def test_r16_a_non_conforming_tool_name_is_replaced_and_the_original_previewed(self) -> None:
        """R16: the recorded name goes to ``previews``, never into ``metrics``."""
        trace = load_trace(FIXTURE_DIR / "hostile.jsonl")
        named = [
            finding
            for finding in run_detectors(trace, DetectorConfig())
            if "tool_name" in finding.metrics
        ]
        assert named, "the hostile fixture must produce at least one tool-named finding"
        for finding in named:
            assert finding.metrics["tool_name"] == "<non-conforming>"
            assert any("</script><script>" in text for text in finding.previews), finding

    @pytest.mark.parametrize(
        ("recorded", "expected"),
        [
            ("Bash", "Bash"),
            ("mcp.server:tool-name", "mcp.server:tool-name"),
            ("_private", "_private"),
            ("9Leading", "<non-conforming>"),
            ("Bash\n", "<non-conforming>"),
            ("has space", "<non-conforming>"),
            ("", "<non-conforming>"),
            (None, "<non-conforming>"),
            ("A" * 65, "<non-conforming>"),
        ],
    )
    def test_r16_the_tool_name_pattern_matches_the_whole_name(
        self, recorded: str | None, expected: str
    ) -> None:
        """R16: ``fullmatch``, so a trailing newline cannot ride into a metric value.

        Increment 1's B5 was two regex engines disagreeing about ``$``. The same
        pattern with ``match`` would accept ``"Bash\\n"`` here.
        """
        value, overflow = constrain_tool_name(recorded)
        assert value == expected
        assert overflow == (None if value == recorded else recorded)


class TestWasteAttributionR17:
    """R17: an attribution over distinct redundant model calls, and nothing else."""

    def test_r17_attribution_only_sums_model_call_usage(self) -> None:
        """R17: a tool call, a user message and a usage-less call contribute nothing."""
        trace = load_trace(FIXTURE_DIR / "duplicate_tool_call.jsonl")
        model_call_seqs = [span.seq for span in trace.spans if span.kind == "model_call"]
        other_seqs = [span.seq for span in trace.spans if span.kind != "model_call"]
        assert attribute_waste(trace, other_seqs).total == 0
        assert attribute_waste(trace, model_call_seqs).total == 100 * len(model_call_seqs)

    def test_r17_attribution_ignores_usage_on_a_span_that_is_not_a_model_call(self) -> None:
        """R17: the kind check is load-bearing even though the mapper cannot trip it.

        R12 only ever puts ``usage`` on a ``model_call``, so on any real trace
        dropping the kind check changes nothing — which is precisely why it needs
        a probe that does not come from the mapper. ``Span`` accepts usage on any
        kind, so the counterexample is one model construction away, and a cost
        engine that started attributing tool-call usage would be caught here
        rather than in a dollar figure.
        """
        trace = load_trace(FIXTURE_DIR / "clean_single_agent.jsonl")
        tool_call = next(span for span in trace.spans if span.kind == "tool_call")
        mutated = trace.model_copy(
            update={
                "spans": tuple(
                    span.model_copy(update={"usage": TokenUsage(input_tokens=999)})
                    if span.seq == tool_call.seq
                    else span
                    for span in trace.spans
                )
            }
        )
        assert mutated.spans[tool_call.seq].usage is not None
        assert attribute_waste(mutated, [tool_call.seq]).total == 0

    def test_r17_attribution_is_over_distinct_spans(self) -> None:
        """R17: R17 says *distinct*, so a detector may hand in one parent per occurrence."""
        trace = load_trace(FIXTURE_DIR / "duplicate_tool_call.jsonl")
        assert attribute_waste(trace, [1, 1, 1]).total == attribute_waste(trace, [1]).total

    def test_r17_out_of_range_seqs_are_ignored_rather_than_raising(self) -> None:
        """R17: attribution is a helper on a finished trace, not a validation path."""
        trace = load_trace(FIXTURE_DIR / "clean_single_agent.jsonl")
        assert attribute_waste(trace, [-1, 10_000]).total == 0

    def test_r17_detectors_with_no_redundancy_notion_report_zero(self) -> None:
        """R17: ``failed_tool_call``, ``unresolved_tool_call`` and the two timing detectors."""
        zero_waste = {"failed_tool_call", "unresolved_tool_call", "blocked_agent", "anomalous_span"}
        seen: set[str] = set()
        for name, finding in corpus_findings():
            if finding.detector in zero_waste:
                seen.add(finding.detector)
                assert finding.wasted.total == 0, f"{name}/{finding.finding_id}"
        assert seen == zero_waste, f"the corpus never exercised {sorted(zero_waste - seen)}"

    def test_r17_attribution_never_exceeds_the_traces_own_model_call_total(self) -> None:
        """R17: an attribution that outgrew its trace would be a double count."""
        for fixture in fixture_paths():
            trace = load_trace(fixture)
            total = sum(
                span.usage.total
                for span in trace.spans
                if span.kind == "model_call" and span.usage is not None
            )
            for finding in run_detectors(trace, DetectorConfig()):
                assert finding.wasted.total <= total, f"{fixture.stem}/{finding.finding_id}"
