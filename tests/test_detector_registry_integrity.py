"""R48 verified against reality rather than against the expectation files.

The coder's ``tests/test_detector_coverage.py`` builds R48's two arms from the
checked-in ``*.expected.json`` documents. That is the right place for it — those
documents are the corpus contract — but it means the coverage guarantee and the
documents that state it are the same evidence, and R49's whole subject is checks
that report green while structurally unable to fail. So this module recomputes
both arms by *running the detectors*, compares the two answers, and then does
what R48's second test claims to do the only way it can be believed: it builds a
scratch copy of the package with a detector added outside the registry, and one
with a detector taken out of it, and watches the suite go red.

"Verified by running it" is the increment-1 review's standing lesson: the R9
collapse condition, the 3.12 nesting bomb and the closed canary ledger were all
correct-looking checks whose failure branch nobody had executed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from swarm_observer.detect.base import DetectorConfig
from swarm_observer.detect.registry import ALL_DETECTORS, DETECTOR_SLUGS

from .detector_corpus import (
    DETECT_PACKAGE,
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

REPO = Path(__file__).resolve().parent.parent

#: A module that is detector-shaped by R13's structural reading — a string
#: ``slug`` and a ``run`` — but is in no registry. Written into a scratch copy
#: of the package, never into the repository.
GHOST_DETECTOR_SOURCE = '''"""A detector-shaped class that nobody registered (R48 probe)."""

from __future__ import annotations


class GhostProbe:
    slug = "ghost_probe"
    title = "Ghost probe"
    default_severity = "warning"

    def run(self, trace, config):
        return ()


DETECTOR = GhostProbe()
'''

#: A class in ``detect/`` that is *not* detector-shaped: it has a slug but no
#: ``run``. The scan must ignore it, or every helper becomes a false positive.
NON_DETECTOR_SOURCE = '''"""A slug-carrying helper that is not a detector (R48 probe)."""

from __future__ import annotations


class NotADetector:
    slug = "not_a_detector"

    def summarize(self):
        return ()
'''


def _live_arms() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """R48's two arms, computed by running every detector over every fixture."""
    fires: dict[str, list[str]] = {}
    silent: dict[str, list[str]] = {}
    for path in fixture_paths():
        found = findings_by_detector(load_trace(path), DetectorConfig())
        for slug, items in found.items():
            (fires if items else silent).setdefault(slug, []).append(path.stem)
    return fires, silent


class TestCoverageArmsAgainstLiveRunsR48:
    """R48: both arms, recomputed from the detectors rather than read from a file."""

    def test_r48_the_corpus_is_not_empty(self) -> None:
        """R48: an empty corpus would make every coverage assertion vacuous."""
        assert len(fixture_paths()) >= len(ALL_DETECTORS) * 2
        assert len(fixture_names()) == len(set(fixture_names()))

    @pytest.mark.parametrize("slug", DETECTOR_SLUGS)
    def test_r48a_every_detector_has_a_fixture_that_fires_it(self, slug: str) -> None:
        """R48(a): a detector with no positive fixture may simply be dead code."""
        fires, _ = _live_arms()
        assert fires.get(slug), f"{slug} produces no findings on any checked-in fixture"

    @pytest.mark.parametrize("slug", DETECTOR_SLUGS)
    def test_r48b_every_detector_has_a_fixture_that_stays_silent(self, slug: str) -> None:
        """R48(b): a detector that fires on everything discriminates nothing."""
        _, silent = _live_arms()
        assert silent.get(slug), f"{slug} fires on every checked-in fixture"

    def test_r48_the_checked_in_expectations_agree_with_the_live_runs(self) -> None:
        """R48: the documents R48's own test reads describe what actually happens.

        If these two ever disagree, the expectation files are the fiction and
        every coverage claim built on them is worth nothing.
        """
        live_fires, live_silent = _live_arms()
        declared_fires, declared_silent = coverage_arms(load_expectations())
        assert {slug: sorted(names) for slug, names in live_fires.items()} == {
            slug: sorted(names) for slug, names in declared_fires.items()
        }
        assert {slug: sorted(names) for slug, names in live_silent.items()} == {
            slug: sorted(names) for slug, names in declared_silent.items()
        }

    def test_r48_the_coverage_check_reports_a_missing_firing_arm(self) -> None:
        """R48: the guard's (a) failure branch, driven directly."""
        problems = coverage_problems(
            {"only": {"detectors": {"agent_loop": {"fires": False}}}}, ["agent_loop"]
        )
        assert len(problems) == 1
        assert "R48a" in problems[0]

    def test_r48_the_coverage_check_reports_a_missing_silent_arm(self) -> None:
        """R48: the guard's (b) failure branch, driven directly."""
        problems = coverage_problems(
            {"only": {"detectors": {"agent_loop": {"fires": True}}}}, ["agent_loop"]
        )
        assert len(problems) == 1
        assert "R48b" in problems[0]

    def test_r48_the_coverage_check_accepts_a_detector_with_both_arms(self) -> None:
        """R48: the guard is not simply always-fail."""
        expectations: dict[str, Any] = {
            "hot": {"detectors": {"agent_loop": {"fires": True}}},
            "cold": {"detectors": {"agent_loop": {"fires": False}}},
        }
        assert coverage_problems(expectations, ["agent_loop"]) == []


class TestRegistryScanR48:
    """R48: the AST scan, exercised against a scratch copy of ``detect/``."""

    def test_r48_the_repository_scan_is_clean(self) -> None:
        """R48: the control arm — the checked-in package has no drift."""
        assert registry_problems(DETECT_PACKAGE, DETECTOR_SLUGS) == []

    def test_r48_the_scan_finds_exactly_the_registered_detectors(self) -> None:
        """R48: every slug in the registry is declared by exactly one module."""
        scanned = detector_classes_in(DETECT_PACKAGE)
        assert set(scanned) == set(DETECTOR_SLUGS)
        assert len(set(scanned.values())) == len(DETECTOR_SLUGS)

    def _scratch_package(self, tmp_path: Path) -> Path:
        """A copy of ``swarm_observer/detect/`` the probes may edit."""
        target = tmp_path / "detect"
        shutil.copytree(
            DETECT_PACKAGE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        return target

    def test_r48_an_unregistered_detector_is_caught(self, tmp_path: Path) -> None:
        """R48: a detector-shaped class outside ``ALL_DETECTORS`` fails the scan.

        Done by actually writing the module, not by passing a doctored slug
        list: the scan reads source, so a probe that never touches a file would
        not exercise the path that matters.
        """
        package = self._scratch_package(tmp_path)
        (package / "ghost_probe.py").write_text(GHOST_DETECTOR_SOURCE, encoding="utf-8")
        problems = registry_problems(package, DETECTOR_SLUGS)
        assert len(problems) == 1
        assert "ghost_probe" in problems[0]
        assert "ALL_DETECTORS" in problems[0]

    def test_r48_a_detector_removed_from_the_registry_is_caught(self, tmp_path: Path) -> None:
        """R48: a module still declaring a detector the registry dropped fails the scan.

        This is the other direction of the same drift: the class is there, the
        registry entry is gone, so the detector exists and no report can see it.
        """
        package = self._scratch_package(tmp_path)
        shortened = tuple(slug for slug in DETECTOR_SLUGS if slug != "anomalous_span")
        problems = registry_problems(package, shortened)
        assert len(problems) == 1
        assert "anomalous_span" in problems[0]

    def test_r48_a_registry_slug_with_no_module_is_caught(self, tmp_path: Path) -> None:
        """R48: the registry naming a detector nothing declares is drift too."""
        package = self._scratch_package(tmp_path)
        (package / "anomalous_span.py").unlink()
        problems = registry_problems(package, DETECTOR_SLUGS)
        assert any(
            "anomalous_span" in problem and "drifted apart" in problem for problem in problems
        )

    def test_r48_a_class_that_is_not_detector_shaped_is_ignored(self, tmp_path: Path) -> None:
        """R48: "detector-shaped" is a slug *and* a ``run``; a helper is neither."""
        package = self._scratch_package(tmp_path)
        (package / "helper_probe.py").write_text(NON_DETECTOR_SOURCE, encoding="utf-8")
        assert registry_problems(package, DETECTOR_SLUGS) == []
        assert "not_a_detector" not in detector_classes_in(package)

    def test_r48_the_scan_reads_a_module_nothing_imports(self, tmp_path: Path) -> None:
        """R48: "AST-scans" — a detector nobody imports has no object to inspect."""
        package = self._scratch_package(tmp_path)
        (package / "ghost_probe.py").write_text(GHOST_DETECTOR_SOURCE, encoding="utf-8")
        assert detector_classes_in(package)["ghost_probe"] == "ghost_probe.py"


def _copy_repo(destination: Path) -> Path:
    """A working copy of the repository, without git or build caches."""
    shutil.copytree(
        REPO,
        destination,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".ruff_cache", ".pytest_cache", "*.egg-info"
        ),
    )
    return destination


def _run_coverage_tests(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the detector-coverage module inside ``root`` and return the result.

    A single module rather than the whole suite: R49's collection floors apply
    only to a whole-suite run, and what is being proved here is that the R48
    tests themselves go red, not that some unrelated module also notices. This
    module is deliberately *not* in the selection — it copies the repository and
    runs pytest, so including it would recurse.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_detector_coverage.py",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


class TestRegistryDriftFailsTheSuiteR48:
    """R48, R50: the two drifts, done for real in a scratch copy of the repository.

    These are slow — each one is a second pytest process over a copied tree —
    and they earn it. Every other assertion in this file calls a checking
    function directly, which proves the function's logic and not that the suite
    is wired to it. The PR write-up claims both drifts were tried by hand; this
    is that claim as a test.
    """

    def test_r48_the_unmodified_copy_is_green(self, tmp_path: Path) -> None:
        """R48: the control arm — without a drift, the copied suite passes.

        Without this, a broken copy step would make both probes below "pass" by
        failing for the wrong reason.
        """
        root = _copy_repo(tmp_path / "clean")
        result = _run_coverage_tests(root)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_r48_adding_an_unregistered_detector_fails_the_suite(self, tmp_path: Path) -> None:
        """R48: dropping a detector-shaped module into the package turns the suite red."""
        root = _copy_repo(tmp_path / "ghost")
        (root / "swarm_observer" / "detect" / "ghost_probe.py").write_text(
            GHOST_DETECTOR_SOURCE, encoding="utf-8"
        )
        result = _run_coverage_tests(root)
        assert result.returncode != 0
        assert "ghost_probe" in result.stdout + result.stderr

    def test_r48_removing_a_detector_from_all_detectors_fails_the_suite(
        self, tmp_path: Path
    ) -> None:
        """R48: taking a live detector out of the registry turns the suite red.

        The module stays; only the registry entry goes. Nothing else in the
        package changes, so the failure has to come from the coverage checks.
        """
        root = _copy_repo(tmp_path / "unregistered")
        registry = root / "swarm_observer" / "detect" / "registry.py"
        source = registry.read_text(encoding="utf-8")
        assert source.count("    anomalous_span.DETECTOR,\n") == 1
        registry.write_text(source.replace("    anomalous_span.DETECTOR,\n", ""), encoding="utf-8")
        result = _run_coverage_tests(root)
        assert result.returncode != 0
        assert "anomalous_span" in result.stdout + result.stderr
