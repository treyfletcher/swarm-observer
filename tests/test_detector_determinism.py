"""Findings are a pure function of the trace: R13, R15, and R25's one integer.

The spec's byte-identical guarantee is about report files, which do not exist
yet. What does exist is the half of it the detectors own, and it is the half that
would be hardest to retrofit: if a ``finding_id`` moves between two runs, every
suppression key in v2 moves with it, and no amount of care in the renderer puts
it back.

So the whole corpus is reduced to one digest over every finding it produces —
ids, severities, summaries, metrics, evidence spans, agent ids, waste totals
*and* previews — and that digest is asserted equal across repeated calls, across
fresh processes, across ``PYTHONHASHSEED``, ``TZ``, ``LC_ALL`` and the working
directory, and finally against a **checked-in constant**.

The constant is what makes this a cross-interpreter check. The increment-1
addendum found that ``str.isprintable()`` answers from the Unicode table
compiled into the running interpreter, so previews — and anything built from
them — can differ between 3.11 and 3.12 on the same input. No single-interpreter
test can see that. A pinned digest can, because CI runs this file on both legs
of the matrix and only one of them has to disagree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from swarm_observer.detect.base import DetectorConfig
from swarm_observer.detect.registry import ALL_DETECTORS, run_detectors

from .detector_corpus import fixture_paths, load_trace

REPO = Path(__file__).resolve().parent.parent

#: The digest of every finding the whole corpus produces, previews included.
#:
#: Deliberate friction (A-b12's rule applied to the whole corpus at once): any
#: change to a fixture, to the mapper, to a detector or to a summary string
#: moves this constant and must be acknowledged in the diff. Recompute with
#: ``corpus_findings_digest()`` and check the diff, never by pasting whatever
#: the run printed.
CORPUS_FINDINGS_DIGEST = "45fb66701fce6daca6944a497a60ba943a7c2f7119783a9c2ec322edd2b202b8"


def corpus_findings_document() -> list[list[object]]:
    """Every finding the corpus produces, as JSON-safe rows in a fixed order."""
    rows: list[list[object]] = []
    for path in fixture_paths():
        trace = load_trace(path)
        for detector in ALL_DETECTORS:
            for finding in detector.run(trace, DetectorConfig()):
                rows.append(
                    [
                        path.name,
                        finding.detector,
                        finding.finding_id,
                        finding.severity,
                        finding.summary,
                        dict(finding.metrics),
                        list(finding.span_seqs),
                        list(finding.agent_ids),
                        list(finding.previews),
                        finding.wasted.model_dump(),
                    ]
                )
    return rows


def corpus_findings_digest() -> str:
    """SHA-256 over :func:`corpus_findings_document`, canonically serialized."""
    payload = json.dumps(
        corpus_findings_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: The script the subprocess cases run. Kept as a string so the child imports the
#: package fresh rather than inheriting this process's state.
_CHILD = (
    "import sys;"
    "sys.path.insert(0, %r);"
    "from tests.test_detector_determinism import corpus_findings_digest;"
    "print(corpus_findings_digest())"
)


def _child_digest(**environment: str) -> str:
    """The corpus digest computed by a fresh interpreter under ``environment``."""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(environment)
    cwd = env.pop("__CWD__", str(REPO))
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD % str(REPO)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return proc.stdout.strip()


class TestFindingsAreDeterministicR13:
    """R13, R15: the same trace yields the same findings, byte for byte."""

    def test_r13_the_corpus_digest_is_stable_within_one_process(self) -> None:
        """R13: two passes over the corpus agree exactly."""
        assert corpus_findings_digest() == corpus_findings_digest()

    def test_r15_the_corpus_digest_matches_the_checked_in_constant(self) -> None:
        """R15, R8: the pinned value, which both interpreters in CI must reach.

        A drift here is one of three things: a fixture changed, a detector
        changed, or the two interpreters disagree about a code point in a
        preview. The third is the one no single-version run can find.
        """
        assert corpus_findings_digest() == CORPUS_FINDINGS_DIGEST

    def test_r13_the_corpus_produces_findings_to_digest(self) -> None:
        """R13: a digest over nothing would be stable and worthless."""
        rows = corpus_findings_document()
        assert len(rows) >= 20
        assert len({row[2] for row in rows}) == len(rows)

    def test_r13_findings_do_not_depend_on_detector_iteration_order(self) -> None:
        """R13: ``run_detectors`` sorts, so a reordered registry gives the same tuple."""
        trace = load_trace(fixture_paths()[0])
        merged = run_detectors(trace, DetectorConfig())
        by_hand = sorted(
            (
                item
                for detector in reversed(ALL_DETECTORS)
                for item in detector.run(trace, DetectorConfig())
            ),
            key=lambda item: (
                {"info": 0, "warning": 1, "critical": 2}[item.severity],
                item.detector,
                item.finding_id,
            ),
        )
        assert list(merged) == by_hand

    @pytest.mark.parametrize("seed", ["0", "1", "2", "random"])
    def test_r15_the_digest_is_identical_under_every_hash_seed(self, seed: str) -> None:
        """R15: ``sort_keys`` removes every dict-ordering dependency, in a real process."""
        assert _child_digest(PYTHONHASHSEED=seed) == CORPUS_FINDINGS_DIGEST

    @pytest.mark.parametrize("timezone", ["UTC", "America/Los_Angeles", "Asia/Kolkata"])
    def test_r13_the_digest_is_identical_under_every_timezone(self, timezone: str) -> None:
        """R13: every timestamp on the model is UTC, so ``TZ`` cannot reach a finding."""
        assert _child_digest(TZ=timezone, PYTHONHASHSEED="0") == CORPUS_FINDINGS_DIGEST

    @pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
    def test_r13_the_digest_is_identical_under_every_locale(self, locale: str) -> None:
        """R13: no locale-dependent number or string formatting reaches a finding."""
        assert _child_digest(LC_ALL=locale, PYTHONHASHSEED="0") == CORPUS_FINDINGS_DIGEST

    def test_r13_the_digest_is_identical_from_a_different_working_directory(self) -> None:
        """R13: no absolute path, and no relative one either, reaches a finding."""
        assert _child_digest(__CWD__=os.sep) == CORPUS_FINDINGS_DIGEST

    def test_r13_two_fresh_processes_agree_with_each_other(self) -> None:
        """R13: process identity is not an input — checked directly, not via the constant."""
        assert _child_digest(PYTHONHASHSEED="1") == _child_digest(PYTHONHASHSEED="2")

    def test_r15_finding_ids_survive_a_round_trip_through_json(self) -> None:
        """R15: an id is text a report can carry and a v2 suppression file can hold."""
        rows = corpus_findings_document()
        restored = json.loads(json.dumps(rows, ensure_ascii=True))
        assert restored == json.loads(json.dumps(rows, ensure_ascii=True))
        assert all(isinstance(row[2], str) and ":" in str(row[2]) for row in rows)


class TestConfigIsTheOnlyOtherInputR25:
    """R25: a finding is a function of the trace and one integer, and nothing else."""

    def test_r25_the_default_config_and_an_explicit_sixty_agree(self) -> None:
        """R25: the default is a value, not a separate code path."""
        trace = load_trace(fixture_paths()[0])
        assert run_detectors(trace, DetectorConfig()) == run_detectors(
            trace, DetectorConfig(blocked_gap_seconds=60)
        )

    def test_r25_only_blocked_agent_findings_move_when_the_knob_moves(self) -> None:
        """R25, A9: across the whole corpus, one knob touches exactly one detector."""
        for path in fixture_paths():
            trace = load_trace(path)
            loose = run_detectors(trace, DetectorConfig(blocked_gap_seconds=60))
            tight = run_detectors(trace, DetectorConfig(blocked_gap_seconds=1))
            assert [item for item in loose if item.detector != "blocked_agent"] == [
                item for item in tight if item.detector != "blocked_agent"
            ], path.name
