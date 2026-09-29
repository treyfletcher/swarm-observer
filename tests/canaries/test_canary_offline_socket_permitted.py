"""R50 canary: the R46 offline check fails when a socket is permitted and attempted.

R50 names this one verbatim — "the offline test fails when a socket connect is
permitted and attempted" — and AC13 is the requirement it protects.

The failure mode R46 is exposed to is the one this project keeps shipping: a
test that installs a guard, runs an analyze, observes nothing and reports green,
when the guard was never in force. "No socket was constructed" and "the guard
was not installed" are the same observation unless the guard is shown firing.

So the canary drives the **same child-process driver** the R46 test uses, in
three modes, and asserts the verdicts are different:

* ``ok`` — guard installed, nothing attempts a socket. Exit 0.
* ``before`` — guard installed, a socket constructed before the analyze. Fails.
* ``inside`` — guard installed, the *renderer* constructs one mid-analyze.
  Fails, and writes no output file (R11).

And one more, which is the canary of the canary: with the guard **removed**, the
same ``inside`` mutation exits 0 — so the child really can open a socket and the
guard really is what stops it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ..test_offline_determinism_r46_r47 import CLEAN, REPO, driver_source, run_driver

#: The driver with its guard removed: the same process, the same mutation, no
#: blocking. Its exit code is what proves the guard's absence is observable.
UNGUARDED = """
import sys, socket

mode = sys.argv[1]
argv = sys.argv[2:]

if mode == "inside":
    from swarm_observer.report import html as html_module
    original = html_module.render_html
    def instrumented(**kwargs):
        socket.socket().close()
        return original(**kwargs)
    html_module.render_html = instrumented

from swarm_observer.cli.main import main
sys.exit(main(argv))
"""


def run_unguarded(tmp_path: Path, mode: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    driver = tmp_path / "unguarded.py"
    driver.write_text(UNGUARDED, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(driver), mode, *argv],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={
            "PYTHONPATH": str(REPO),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
    )


def test_r50_the_guarded_run_succeeds(tmp_path: Path) -> None:
    """R50: the control arm. Without it, every assertion below is about a broken run.

    Red when: the default analyze path acquires a socket, which is R46 going
    red for real.
    """
    report = tmp_path / "report.json"
    result = run_driver(tmp_path, "ok", ["analyze", str(CLEAN), "--json", str(report)])
    assert result.returncode == 0, result.stderr
    assert report.is_file()


def test_r50_the_guard_fires_on_a_socket_constructed_before_the_analyze(
    tmp_path: Path,
) -> None:
    """R50: the guard is in force, demonstrated rather than assumed."""
    result = run_driver(tmp_path, "before", ["analyze", str(CLEAN), "--json", "out.json"])
    with pytest.raises(AssertionError):
        assert result.returncode == 0, result.stderr
    assert "SocketAttempt" in result.stderr


def test_r50_the_guard_fires_on_a_socket_constructed_inside_the_renderer(
    tmp_path: Path,
) -> None:
    """R50/R46: the guard covers the analyze path, not only the top of the process.

    Red when: the guard is installed somewhere the product can bypass — a
    module that bound ``socket.socket`` at import time would defeat the
    monkeypatch and only the audit hook would catch it.

    ``--out`` is required for this arm and is not incidental: ``inside``
    instruments ``render_html``, which a ``--json``-only run never calls. A
    canary that passed ``--json`` alone would exit 0 and report the guard
    broken.
    """
    html = tmp_path / "report.html"
    report = tmp_path / "report.json"
    result = run_driver(
        tmp_path, "inside", ["analyze", str(CLEAN), "--out", str(html), "--json", str(report)]
    )
    with pytest.raises(AssertionError):
        assert result.returncode == 0, result.stderr
    assert "SocketAttempt" in result.stderr
    assert not html.exists(), "R11: a failing run writes no output file"
    assert not report.exists(), "A-d11: and writes neither of the two"


def test_r50_the_same_mutation_succeeds_when_the_guard_is_removed(tmp_path: Path) -> None:
    """R50: the canary's own canary — the socket really is constructible here.

    If the child process could not open a socket for an unrelated reason —
    a sandbox, a missing library — then ``inside`` would fail whether or not
    the guard existed, and the three tests above would prove nothing. This
    exits 0, so the difference is the guard.

    Red when: the environment stops allowing a socket to be constructed at all,
    at which point R46's whole test becomes untrustworthy and must be
    reconsidered rather than believed.
    """
    report = tmp_path / "report.json"
    html = tmp_path / "report.html"
    result = run_unguarded(
        tmp_path, "inside", ["analyze", str(CLEAN), "--out", str(html), "--json", str(report)]
    )
    assert result.returncode == 0, result.stderr
    assert report.is_file() and html.is_file()
    assert "SocketAttempt" not in result.stderr


def test_r50_the_driver_installs_both_guards_r46_names(tmp_path: Path) -> None:
    """R46: "a ``socket.socket`` subclass raising on construction, plus an audit hook".

    Both halves are named in the requirement and a driver with only one would
    still pass every test above — the subclass alone catches everything the
    tests attempt. Asserted against the driver's source so the audit hook
    cannot be dropped silently.

    Red when: either guard is removed from ``driver_source``.
    """
    source = driver_source()
    assert "sys.addaudithook" in source
    assert "class NoSocket(socket.socket)" in source
    assert 'event.startswith("socket.")' in source
    assert "socket.create_connection" in source
