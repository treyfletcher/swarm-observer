"""The ``live_narrator`` opt-in test, and R49's proof that the marker deselects.

**S35, and the owner's provisional ruling on it.** R49 requires that the
``live_narrator`` marker's "deselection is additionally verified by a test
asserting the marker deselects a **non-zero** count". Before this module no
test carried the marker, so ``-m "not live_narrator"`` deselected zero and that
clause had nothing to verify — a requirement satisfied by an empty set, which
is instance one of this project's signature defect wearing a suite-integrity
badge. The owner ruled: use the marker, not a ``skipif``; a marker-based
deselection is *visible* in the deselected count, where a ``skipif`` would be a
silent skip. That ruling is **provisional and flagged for the owner**.

Two consequences the ruling leaves to the tester, and how they are taken here:

* **The env gate is a branch, not a skip.** ``SWARM_OBSERVER_LIVE_NARRATOR``
  decides what the marked test asserts, never whether it runs. With the
  variable unset the test asserts the offline preconditions that make a live
  run meaningful in the first place, so it is a real test in both directions
  rather than a no-op wearing a marker.
* **No entry in ``tests/allowed_skips.txt``.** That file is the *skip*
  allowlist, its own header says a deselection needs no entry, and
  ``test_r49_the_suite_currently_allows_no_skips`` asserts it is empty. The
  "ledger entry recording why" the ruling asks for is the named comment block
  in that file plus this docstring; adding an allowlist line would make a skip
  permissible, which is the opposite of what R49 is for.
"""

from __future__ import annotations

import ast
import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from swarm_observer.narrate.adapters.anthropic import (
    CREDENTIAL_ENV_VARS,
    AnthropicNarratorClient,
    system_prompt,
    user_prompt,
)
from swarm_observer.narrate.client import (
    MAX_PARAGRAPH_CHARS,
    NarratorError,
    NarratorTransportError,
)
from swarm_observer.narrate.narrator import validate_paragraph

from .test_narrate_client_r41 import a_request

REPO = Path(__file__).resolve().parent.parent

#: T18: the environment variable that turns the marked test from a
#: preconditions check into a real outbound request.
LIVE_ENV = "SWARM_OBSERVER_LIVE_NARRATOR"


@pytest.mark.live_narrator
def test_r41_r43_the_live_narrator_path_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """R41/R43: T18's opt-in smoke test — a real provider call, or its preconditions.

    With ``SWARM_OBSERVER_LIVE_NARRATOR=1`` and a credential present, this
    sends the one outbound request the product makes and asserts the answer
    survives R43's validation. That is the only way any line of
    ``AnthropicNarratorClient.complete`` past the import guard has ever been
    executed; the PR's §5 lists every one of them as unexecuted, and this test
    is where that stops being permanent.

    With the variable unset — CI, and every developer who has not opted in —
    it asserts what an offline environment *can* say about the live path:
    the client constructs without touching anything, the prompts are pure
    functions of a request, and the SDK import fails with the classified code
    rather than an ``ImportError``. Those are the preconditions a live run
    depends on, so the unset branch is a check and not a placeholder.

    Red when (offline): the adapter starts doing work in its constructor, the
    prompt stops carrying the payload, or a missing SDK stops being a
    ``NarratorTransportError``.
    Red when (live): the provider answers with something R43 refuses.
    """
    request = a_request()
    client = AnthropicNarratorClient()
    if os.environ.get(LIVE_ENV) != "1":
        assert client.model and client.timeout_seconds > 0
        assert system_prompt() == system_prompt()
        assert str(MAX_PARAGRAPH_CHARS) in system_prompt()
        from swarm_observer.narrate.summary import serialize_request

        assert serialize_request(request) in user_prompt(request)
        with pytest.raises(NarratorTransportError) as caught:
            client._sdk()
        assert caught.value.code == "sdk_not_installed"
        return

    if not any(os.environ.get(name) for name in CREDENTIAL_ENV_VARS):
        raise AssertionError(
            f"{LIVE_ENV}=1 but no credential is set. A live run with no key is not a "
            "deselected test; it is a test that cannot do what its name says."
        )
    try:
        response = client.complete(request)
    except NarratorError as error:  # pragma: no cover - live only
        raise AssertionError(f"the live narrator failed: {error.line}") from error
    paragraph = validate_paragraph(response.paragraph)
    assert paragraph
    assert len(paragraph) <= MAX_PARAGRAPH_CHARS
    # R42's promise, checked against a real provider round trip: the model was
    # told about counts and slugs only, so nothing in the answer can be a
    # trace byte, because nothing in the request was one.
    assert "\n" not in paragraph


@functools.cache
def _collected(marker_expression: str | None) -> int:
    """How many tests the suite collects under a ``-m`` expression.

    A subprocess, because the marker expression is a property of a pytest
    session and this one already has a different one.
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    if marker_expression is not None:
        argv += ["-m", marker_expression]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        argv, capture_output=True, text=True, cwd=str(REPO), env=environment, check=False
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    counts = re.findall(r"^\S+\.py: (\d+)$", result.stdout, re.MULTILINE)
    assert counts, f"could not read a collection count from:\n{result.stdout[-2000:]}"
    return sum(int(value) for value in counts)


class TestLiveNarratorDeselectionR49:
    """R49: "deselection is additionally verified by a test asserting a non-zero count"."""

    def test_r49_the_marker_deselects_a_non_zero_count(self) -> None:
        """R49: CI's ``-m "not live_narrator"`` really removes something.

        Measured as *whole suite minus the deselected suite*, in a
        subprocess, because that is the arithmetic the requirement's clause
        describes and the one an empty marker set makes zero.

        Red when: the last ``live_narrator``-marked test is deleted or its
        marker is dropped — which is the state this branch inherited and
        which S35 is about.
        """
        everything = _collected(None)
        without_live = _collected("not live_narrator")
        deselected = everything - without_live
        assert deselected > 0, (
            "the live_narrator marker deselects nothing; R49's clause has no subject"
        )
        assert _collected("live_narrator") == deselected

    def test_r49_the_marker_is_carried_by_the_tests_that_need_it(self) -> None:
        """R49: the marker is on the live path and on nothing else.

        A marker that spread to an ordinary test would silently stop that
        test running in CI, which is the failure R49's whole section exists
        to prevent — deselection is visible in a count, but only if somebody
        looks at *which* tests it removed.
        """
        marked = _collected("live_narrator")
        assert marked == 1, (
            f"{marked} tests carry live_narrator; each one is a test CI never runs, "
            "so the set should stay as small as the requirement needs"
        )

    def test_r49_no_skip_was_used_to_gate_the_live_path(self) -> None:
        """R49: no ``skipif`` decorator and no skip call anywhere under ``tests/``.

        An AST walk rather than a grep, for the reason the ``or True`` scan is
        an AST walk: this module's own prose discusses ``skipif`` at length,
        and a substring check would be tripped by the documentation of the
        rule it enforces.

        Red when: any test is gated on a skip — the owner's ruling turns on
        the difference between a deselection, which appears in a count, and a
        skip, which appears in a line nobody reads.
        """
        offenders: list[str] = []
        for path in sorted((REPO / "tests").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    if name.endswith(("importorskip", "pytest.skip", "skip")) and name.startswith(
                        "pytest"
                    ):
                        offenders.append(f"{path.name}:{node.lineno}: {name}()")
                if isinstance(node, ast.Attribute) and node.attr == "skipif":
                    offenders.append(f"{path.name}:{node.lineno}: skipif mark")
        assert not offenders, offenders

    def test_r49_the_skip_scan_can_see_a_skip(self) -> None:
        """R49: the scan above must be able to fail.

        Driven against the two shapes R49 forbids, so "no offenders" is a
        measurement rather than a property of an empty walk.
        """
        probe = (
            "import pytest\n"
            "@pytest.mark.skipif(True, reason='x')\n"
            "def test_a():\n"
            "    pytest.importorskip('anthropic')\n"
        )
        found: list[str] = []
        for node in ast.walk(ast.parse(probe)):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name.endswith(("importorskip", "pytest.skip", "skip")) and name.startswith(
                    "pytest"
                ):
                    found.append(name)
            if isinstance(node, ast.Attribute) and node.attr == "skipif":
                found.append("skipif")
        assert sorted(found) == ["pytest.importorskip", "skipif"]

    def test_r49_the_deselection_is_recorded_in_the_skip_ledger_as_a_comment(self) -> None:
        """R49/S35: the owner's "ledger entry recording why", without allowing a skip.

        ``allowed_skips.txt`` must stay empty as data — a non-empty allowlist
        is a test that does not run — so the record is a named comment in it.
        This asserts the record names the test, so deleting the test and
        leaving the note, or the reverse, is visible.
        """
        ledger = (REPO / "tests" / "allowed_skips.txt").read_text(encoding="utf-8")
        assert "live_narrator" in ledger
        assert "test_r41_r43_the_live_narrator_path_end_to_end" in ledger
        assert all(
            line.strip().startswith("#") or not line.strip() for line in ledger.splitlines()
        ), "allowed_skips.txt now contains an entry; a skip is a test that did not run"
