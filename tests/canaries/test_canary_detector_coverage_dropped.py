"""R50 canary: the detector-coverage guards fail when the corpus stops watching.

R50 requires a proof for each guard that could be disabled by its environment or
by an inert input, and R48's coverage check is the clearest example of the second
kind: it reads checked-in expectation files, so it is exactly as strong as those
files are honest. An expectation that quietly stops naming a detector, or that
says "fires: true" with nothing behind it, turns the guard green while it watches
nothing — the failure mode this project has now shipped five times.

So every way of blinding it is tried here, on tampered copies, and each must
raise:

* a detector key deleted from one fixture's expectations;
* a detector's positive arm emptied across the whole corpus (R48a);
* a detector's silent arm emptied across the whole corpus (R48b);
* a firing detector's ``findings`` list emptied;
* one expected metric altered;
* a detector class that exists in ``detect/`` but not in ``ALL_DETECTORS``;
* a registry slug with no class behind it.

Each has a control arm immediately beside it, because a guard that raises on
everything is no more useful than one that raises on nothing.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from swarm_observer.detect.registry import DETECTOR_SLUGS

from ..detector_corpus import FIXTURE_DIR as TRACES
from ..detector_corpus import (
    assert_corpus_contract,
    assert_detector_coverage,
    assert_registry_complete,
    coverage_problems,
    detector_classes_in,
    expectation_problems,
    load_expectation,
    load_expectations,
    observed_document,
    registry_problems,
)

VICTIM = "duplicate_tool_call"

#: A module that would be a detector if anybody had registered it.
UNREGISTERED_DETECTOR_SOURCE = """
class GhostDetector:
    slug = "ghost_detector"
    title = "Never registered"
    default_severity = "warning"

    def run(self, trace, config):
        return ()
"""


def tampered_expectations() -> dict[str, dict[str, Any]]:
    """A deep copy of the real corpus expectations, safe to break."""
    return copy.deepcopy(load_expectations())


def test_r50_canary_the_control_run_is_clean() -> None:
    """R50: the guards pass on the untouched corpus, so they are not always-fail."""
    assert coverage_problems(load_expectations()) == []
    assert registry_problems() == []
    assert_detector_coverage(load_expectations())
    assert_registry_complete()
    assert_corpus_contract()


@pytest.mark.parametrize("slug", DETECTOR_SLUGS)
def test_r50_canary_dropping_a_detector_from_one_fixtures_expectations_fails(slug: str) -> None:
    """R50, R48: an expectation that stops naming a detector must not pass quietly."""
    expectations = tampered_expectations()
    del expectations[VICTIM]["detectors"][slug]
    problems = expectation_problems(
        VICTIM, expectations[VICTIM], observed_document(TRACES / f"{VICTIM}.jsonl")
    )
    assert problems, f"dropping {slug} from {VICTIM}'s expectations was not noticed"
    assert slug in problems[0]


@pytest.mark.parametrize("slug", DETECTOR_SLUGS)
def test_r50_canary_emptying_a_detectors_firing_arm_fails(slug: str) -> None:
    """R50, R48a: no fixture fires it — the detector may be dead code."""
    expectations = tampered_expectations()
    for expectation in expectations.values():
        expectation["detectors"][slug] = {"fires": False}
    with pytest.raises(AssertionError) as raised:
        assert_detector_coverage(expectations)
    assert slug in str(raised.value)
    assert "R48a" in str(raised.value)


@pytest.mark.parametrize("slug", DETECTOR_SLUGS)
def test_r50_canary_emptying_a_detectors_silent_arm_fails(slug: str) -> None:
    """R50, R48b: it fires on every fixture, which is indistinguishable from always."""
    expectations = tampered_expectations()
    for expectation in expectations.values():
        expectation["detectors"][slug] = {"fires": True, "findings": [{}]}
    with pytest.raises(AssertionError) as raised:
        assert_detector_coverage(expectations)
    assert slug in str(raised.value)
    assert "R48b" in str(raised.value)


def test_r50_canary_a_fires_true_expectation_with_no_findings_fails() -> None:
    """R50: 'fires: true' and nothing else cannot fail when a threshold moves."""
    expectations = tampered_expectations()
    expectations[VICTIM]["detectors"]["repeated_tool_call"] = {"fires": True}
    problems = expectation_problems(
        VICTIM, expectations[VICTIM], observed_document(TRACES / f"{VICTIM}.jsonl")
    )
    assert any("lists no findings" in problem for problem in problems), problems


def test_r50_canary_an_altered_expected_metric_fails() -> None:
    """R50: the corpus pins the numbers, not merely the fact that something fired."""
    expectations = tampered_expectations()
    finding = expectations[VICTIM]["detectors"]["repeated_tool_call"]["findings"][0]
    finding["metrics"]["occurrences"] = finding["metrics"]["occurrences"] + 1
    problems = expectation_problems(
        VICTIM, expectations[VICTIM], observed_document(TRACES / f"{VICTIM}.jsonl")
    )
    assert any("metrics" in problem for problem in problems), problems


def test_r50_canary_an_altered_expected_severity_fails() -> None:
    """R50: a severity boundary that moved must be visible in the corpus."""
    expectations = tampered_expectations()
    finding = expectations[VICTIM]["detectors"]["repeated_tool_call"]["findings"][0]
    finding["severity"] = "info"
    problems = expectation_problems(
        VICTIM, expectations[VICTIM], observed_document(TRACES / f"{VICTIM}.jsonl")
    )
    assert any("severity" in problem for problem in problems), problems


def test_r50_canary_a_flipped_arm_fails_the_contract() -> None:
    """R50: an expectation claiming a silent detector fires (and the reverse)."""
    fixture = TRACES / f"{VICTIM}.jsonl"
    observed = observed_document(fixture)
    expectation = copy.deepcopy(load_expectation(fixture))
    expectation["detectors"]["agent_loop"] = {"fires": True, "findings": [{}]}
    assert any(
        "does not fire" in problem
        for problem in expectation_problems(VICTIM, expectation, observed)
    )
    expectation = copy.deepcopy(load_expectation(fixture))
    expectation["detectors"]["repeated_tool_call"] = {"fires": False}
    assert any(
        "fires" in problem for problem in expectation_problems(VICTIM, expectation, observed)
    )


def test_r50_canary_an_unregistered_detector_class_is_caught(tmp_path: Path) -> None:
    """R50, R48: a detector outside ``ALL_DETECTORS`` is outside every coverage arm."""
    (tmp_path / "ghost.py").write_text(UNREGISTERED_DETECTOR_SOURCE, encoding="utf-8")
    assert detector_classes_in(tmp_path) == {"ghost_detector": "ghost.py"}
    with pytest.raises(AssertionError) as raised:
        assert_registry_complete(tmp_path, DETECTOR_SLUGS)
    assert "ghost_detector" in str(raised.value)
    assert "ALL_DETECTORS" in str(raised.value)


def test_r50_canary_a_registry_slug_with_no_module_is_caught() -> None:
    """R50, R48: the registry and the modules drifting apart, in the other direction."""
    with pytest.raises(AssertionError) as raised:
        assert_registry_complete(slugs=(*DETECTOR_SLUGS, "phantom_detector"))
    assert "phantom_detector" in str(raised.value)


def test_r50_canary_the_ast_scan_ignores_classes_that_are_not_detectors(
    tmp_path: Path,
) -> None:
    """R50: the scan must not fire on any class with a ``run`` method, or it is noise."""
    (tmp_path / "helper.py").write_text(
        "class Helper:\n    def run(self, trace, config):\n        return ()\n",
        encoding="utf-8",
    )
    (tmp_path / "constant.py").write_text(
        'class Holder:\n    slug = "not_a_detector"\n',
        encoding="utf-8",
    )
    assert detector_classes_in(tmp_path) == {}
