"""R50 canary: AC14's two hook clauses, which had no proof they can fail.

**Added by the increment-5 review.** AC14 lists six things the suite must be
able to do and says "Each of these is a checked-in canary test that asserts
the failure occurs". Four had one: ``escape_html`` monkeypatched to identity,
the renderer embedding ``time.time()``, a detector dropped from
``ALL_DETECTORS``, a detector's fixture expectations emptied. Two did not:

    When a test module is made to collect zero tests, Then the
    ``collection_floor`` hook fails the session with a message naming that
    module; When a test is skipped with a reason absent from
    ``allowed_skips.txt``, Then the session fails.

What existed instead were unit tests for :func:`collection_floor_problems` and
:func:`unlisted_skips`, the two **pure helpers** ``conftest.py`` factored out
"so the check itself can be exercised directly". Those are good tests and they
are not this. They assert that a list comprehension computes a list. Neither
of them touches the part that actually fails a run, and for the skip hook that
part is a **private pytest attribute**:

    session = getattr(terminalreporter, "_session", None)
    if session is not None:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED

``getattr(..., None)`` and ``if session is not None`` mean that on a pytest
whose terminal reporter does not carry ``_session``, the hook prints a red
banner naming the offending test and the session **exits 0**. A red banner in
a green run is exactly the shape of the incident R49 was written for: 148
tests collecting zero, reported as skipped, and CI green.

That guard is the project's answer to its own worst inherited failure, and it
had never been watched fail. It is watched here, in a real pytest session in a
subprocess, both hooks, each with a control arm — because "the session failed"
is also what a broken probe looks like.

The probes run in a **temporary directory** with their own ``conftest.py`` that
re-exports the real hooks from ``tests.conftest``. Nothing is written into
``tests/``: a canary that created a module inside the suite it is checking
would leave one behind on a hard kill, and the mutation harness digests every
``*.py`` under the repository between mutants.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent

#: A reason string no checked-in allowlist carries. ``allowed_skips.txt`` is
#: asserted empty-as-data by ``test_r49_the_suite_currently_allows_no_skips``,
#: so in fact *every* reason is unlisted; this one is spelled distinctively so
#: a failure message says which probe produced it.
UNLISTED_REASON = "a reason the canary invented and no allowlist carries"


def _run(directory: Path) -> subprocess.CompletedProcess[str]:
    """Run pytest over ``directory`` in a subprocess, and return the result.

    A subprocess, not ``pytest.main``: the thing under test is the **session
    exit status**, and a nested in-process session shares this one's config
    and reporter. The exit code of a separate interpreter is the only form of
    this assertion that cannot be satisfied by the harness.
    """
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(directory)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=environment,
        check=False,
    )


def _skip_probe(directory: Path, *, allowed: list[str]) -> subprocess.CompletedProcess[str]:
    """A one-test package whose test skips, under the real terminal-summary hook."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conftest.py").write_text(
        "from tests.conftest import pytest_terminal_summary  # noqa: F401\n"
        "import tests.conftest as real\n"
        f"real.load_allowed_skips = lambda: {allowed!r}\n",
        encoding="utf-8",
    )
    (directory / "test_probe.py").write_text(
        f"import pytest\n\n\ndef test_probe() -> None:\n    pytest.skip({UNLISTED_REASON!r})\n",
        encoding="utf-8",
    )
    return _run(directory)


def _floor_probe(directory: Path, *, floor: int) -> subprocess.CompletedProcess[str]:
    """A package with one collecting module, under a floor it cannot meet."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conftest.py").write_text(
        "from tests.conftest import (  # noqa: F401\n"
        "    pytest_collection_finish,\n"
        "    pytest_collection_modifyitems,\n"
        ")\n"
        "import tests.conftest as real\n"
        "real._floors_apply = lambda config: True\n"
        f"real.load_collection_floors = lambda: {{'test_probe.py': {floor}}}\n",
        encoding="utf-8",
    )
    (directory / "test_probe.py").write_text(
        "def test_one() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    return _run(directory)


class TestTheSkipHookCanFailASessionR50:
    """R49/AC14: "a test skipped for an unlisted reason fails the session"."""

    def test_r50_an_unlisted_skip_makes_the_session_exit_non_zero(self, tmp_path: Path) -> None:
        """R50: the exit **status**, not the banner.

        Red when: the hook stops reaching ``session.exitstatus`` — which is
        what happens if ``terminalreporter._session`` ever goes away, since
        the hook reads it with ``getattr(..., None)`` and returns quietly.
        The banner would still print. Nothing else in the suite would notice.
        """
        result = _skip_probe(tmp_path / "skip", allowed=[])
        assert result.returncode != 0, (
            "an unlisted skip did not fail the session:\n" + result.stdout[-3000:]
        )
        assert "suite integrity (R49): unlisted skips" in result.stdout
        assert UNLISTED_REASON in result.stdout

    def test_r50_a_listed_skip_leaves_the_session_green(self, tmp_path: Path) -> None:
        """R50: the control arm — the hook is not simply always-fail.

        Without this, a hook that failed every session carrying any skip at
        all, listed or not, would satisfy the arm above.
        """
        result = _skip_probe(tmp_path / "allowed", allowed=[UNLISTED_REASON])
        assert result.returncode == 0, result.stdout[-3000:]
        assert "unlisted skips" not in result.stdout

    def test_r50_the_hook_still_reaches_the_sessions_exit_status(self, tmp_path: Path) -> None:
        """R49: the private attribute the guard depends on is still there.

        ``pytest_terminal_summary`` fails a run by assigning to
        ``terminalreporter._session.exitstatus``. ``_session`` is private and
        this repository pins no pytest patch version, so the dependency is
        named here rather than left to be discovered by a green CI run on a
        suite that skipped something.

        Red when: a pytest upgrade removes the attribute — at which point the
        two arms above go red too, and this one says *why*.
        """
        result = _skip_probe(tmp_path / "attr", allowed=[])
        assert result.returncode == 1, (
            "the skip hook reported but did not fail the session; "
            "terminalreporter._session has probably moved"
        )


class TestTheCollectionFloorHookCanFailASessionR50:
    """R49/AC14: "a module that collects fewer than its floor fails the session"."""

    def test_r50_a_module_below_its_floor_fails_the_session_by_name(self, tmp_path: Path) -> None:
        """R50: AC14's "with a message naming that module".

        stdout **and** stderr: the floor hook raises ``pytest.UsageError``,
        which pytest prints to stderr, while the skip hook writes through the
        terminal reporter to stdout. A probe that read only one of the two
        would pass on the exit code and assert nothing about the message.

        Red when: the hook stops raising, or stops naming the module -- a
        floor violation that says only "collection floor not met" is a floor
        nobody can act on.
        """
        result = _floor_probe(tmp_path / "under", floor=99)
        report = result.stdout + result.stderr
        assert result.returncode != 0, report[-3000:]
        assert "collection floor not met" in report
        assert "test_probe.py" in report

    def test_r50_a_module_that_collects_nothing_is_named_too(self, tmp_path: Path) -> None:
        """R50: AC14's other arm — the 148-tests-collecting-zero shape itself.

        A floor for a module that is not there at all, which is what a rename
        or an import error looks like to this hook.
        """
        directory = tmp_path / "ghost"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "conftest.py").write_text(
            "from tests.conftest import (  # noqa: F401\n"
            "    pytest_collection_finish,\n"
            "    pytest_collection_modifyitems,\n"
            ")\n"
            "import tests.conftest as real\n"
            "real._floors_apply = lambda config: True\n"
            "real.load_collection_floors = lambda: {'test_vanished.py': 1}\n",
            encoding="utf-8",
        )
        (directory / "test_probe.py").write_text(
            "def test_one() -> None:\n    assert True\n", encoding="utf-8"
        )
        result = _run(directory)
        report = result.stdout + result.stderr
        assert result.returncode != 0, report[-3000:]
        assert "test_vanished.py" in report
        assert "collected 0 tests" in report

    def test_r50_a_module_at_its_floor_leaves_the_session_green(self, tmp_path: Path) -> None:
        """R50: the control arm. Adding tests never breaks a floor (A11)."""
        result = _floor_probe(tmp_path / "at", floor=1)
        assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
        assert "collection floor" not in result.stdout + result.stderr


def test_r49_the_checked_in_floor_file_is_what_the_hook_reads() -> None:
    """R49: the probes above stub the floors, so something must read the real file.

    Otherwise the canary proves a hook works on invented data while the
    checked-in data goes unexamined — a proof about the probe.
    """
    floors = json.loads((REPO / "tests" / "collection_floor.json").read_text(encoding="utf-8"))
    from tests.conftest import load_collection_floors

    assert dict(load_collection_floors()) == floors["modules"]
    assert load_collection_floors(), "the floor file is empty; the hook has no subject"


def test_r49_the_checked_in_allowlist_is_what_the_hook_reads() -> None:
    """R49: the same, for the skip allowlist, whose emptiness is the assertion."""
    from tests.conftest import load_allowed_skips

    assert list(load_allowed_skips()) == [], (
        "allowed_skips.txt now carries an entry; a skip is a test that did not run"
    )


@pytest.mark.parametrize("hook", ["pytest_terminal_summary", "pytest_collection_finish"])
def test_r49_the_hooks_are_still_named_what_the_probes_import(hook: str) -> None:
    """R49: a rename would make every probe above silently test nothing.

    The probes import the hooks by name into a temporary ``conftest.py``. If a
    hook were renamed, the import would fail and the probe subprocess would
    exit non-zero — which is what the failure arms assert. This makes the
    rename visible as itself rather than as a passing canary.
    """
    import tests.conftest as real

    assert callable(getattr(real, hook))


class _FakeSession:
    """The one attribute ``pytest_collection_finish`` reads off a session."""

    config = None


def test_r50_the_floor_hook_raises_in_process_too() -> None:
    """R50: the same guard, proven the other way, in this interpreter.

    The subprocess arms assert the **session exit status**, which is the
    property AC14 states and the only form that can catch the skip hook's
    private-attribute dependency. This arm asserts the narrower thing the
    floor hook does on its own -- it raises ``pytest.UsageError`` -- so the
    canary does not depend on a subprocess for its entire warrant, and so a
    reader of ``conftest.py`` can see a ``pytest.raises`` against the hook
    they are reading.

    Red when: the hook starts reporting a floor violation instead of raising.
    """
    import tests.conftest as real

    saved_applies, saved_floors, saved_counts = (
        real._floors_apply,
        real.load_collection_floors,
        dict(real._COLLECTED),
    )
    try:
        real._floors_apply = lambda config: True  # type: ignore[assignment]
        floors = {"tests/test_absent.py": 3}
        real.load_collection_floors = lambda: floors  # type: ignore[assignment]
        real._COLLECTED.clear()
        with pytest.raises(pytest.UsageError, match="collection floor not met"):
            real.pytest_collection_finish(session=_FakeSession())  # type: ignore[arg-type]
    finally:
        real._floors_apply = saved_applies  # type: ignore[assignment]
        real.load_collection_floors = saved_floors  # type: ignore[assignment]
        real._COLLECTED.clear()
        real._COLLECTED.update(saved_counts)
