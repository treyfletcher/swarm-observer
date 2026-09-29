"""Session fixtures and the R49 suite-integrity hooks.

This project's predecessor shipped three CI checks that reported green while
structurally unable to fail — a fixture set with no case that could trip the
check, an undeclared test dependency that made 148 tests collect as zero and
report as skipped, and a live-replay path that could never have passed. R49 is
the response, and two thirds of it are the hooks below:

* ``pytest_collection_finish`` fails the session when a module listed in
  ``collection_floor.json`` collects fewer tests than its floor, or nothing at
  all. Adding tests never breaks a floor; a module that silently stops
  collecting always does.
* ``pytest_terminal_summary`` fails the session when a test is skipped for a
  reason that is not an exact entry in ``allowed_skips.txt``. A skip is a test
  that did not run, and the default posture is that this is a failure.

The third part of R49 is a rule the hooks cannot enforce: optional test
dependencies are imported at module scope, never via ``pytest.importorskip`` and
never behind a ``skipif``, so a missing one is a collection *error* that fails
the run rather than a skip that hides it.

The counting deliberately happens in ``pytest_collection_modifyitems`` with
``tryfirst=True``, before ``-m``/``-k`` deselection runs: CI always passes
``-m "not live_narrator"``, and measuring after deselection would make every
floor a function of the marker expression.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
COLLECTION_FLOOR_PATH = TESTS_DIR / "collection_floor.json"
ALLOWED_SKIPS_PATH = TESTS_DIR / "allowed_skips.txt"

#: R45: the suite must pass with every one of these unset. CI unsets them with
#: `env -u`; this fixture does the same for a developer's shell so a green run
#: locally means the same thing a green run in CI does.
CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "OPENAI_API_KEY",
    "GH_TOKEN",
)

#: Filled by the collection hook, read by the finish hook.
_COLLECTED: dict[str, int] = {}


@pytest.fixture(autouse=True, scope="session")
def _no_credentials() -> Iterator[None]:
    """Scrub every provider credential for the whole session (R45)."""
    saved = {name: os.environ.pop(name) for name in CREDENTIAL_ENV_VARS if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def load_collection_floors() -> dict[str, int]:
    """The checked-in floors: repository-relative module path → minimum tests."""
    data = json.loads(COLLECTION_FLOOR_PATH.read_text(encoding="utf-8"))
    modules = data["modules"]
    if not isinstance(modules, dict):
        raise ValueError("collection_floor.json: 'modules' must be an object")
    return {str(key): int(value) for key, value in modules.items()}


def load_allowed_skips() -> tuple[str, ...]:
    """The exact skip reasons this suite tolerates. Comments and blanks ignored."""
    lines = ALLOWED_SKIPS_PATH.read_text(encoding="utf-8").splitlines()
    return tuple(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )


def module_key(item: pytest.Item) -> str:
    """The repository-relative path of the module a test item came from."""
    path = Path(str(item.path)) if item.path is not None else Path(str(item.fspath))
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:  # pragma: no cover - a test outside the repo
        return path.name


def _floors_apply(config: pytest.Config) -> bool:
    """Enforce floors only for a whole-suite run.

    A developer running one file or one ``-k`` expression is not making a claim
    about the whole suite, and failing them for it would train everyone to
    delete the check. CI always runs the whole suite, which is where the floor
    has to hold.
    """
    if getattr(config.option, "keyword", ""):
        return False
    arguments = {Path(str(argument)).name for argument in config.args}
    return arguments <= {"tests", ""}


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Snapshot per-module collected counts before any deselection (R49)."""
    counts: dict[str, int] = {}
    for item in items:
        key = module_key(item)
        counts[key] = counts.get(key, 0) + 1
    _COLLECTED.clear()
    _COLLECTED.update(counts)


def collection_floor_problems(collected: Mapping[str, int], floors: Mapping[str, int]) -> list[str]:
    """The floor violations in a collection snapshot (R49).

    Factored out of the hook so the check itself can be exercised directly:
    a guard whose logic only ever runs inside a pytest hook is a guard nobody
    has watched fail.
    """
    problems: list[str] = []
    for module, floor in sorted(floors.items()):
        count = collected.get(module, 0)
        if count == 0:
            problems.append(
                f"{module}: collected 0 tests (floor {floor}). A module that collects "
                "nothing is an import error or a rename, not a passing module."
            )
        elif count < floor:
            problems.append(f"{module}: collected {count} tests, floor is {floor}")
    return problems


def pytest_collection_finish(session: pytest.Session) -> None:
    """Fail the session when a listed module collected too few tests (R49)."""
    if not _floors_apply(session.config):
        return
    problems = collection_floor_problems(_COLLECTED, load_collection_floors())
    if problems:
        raise pytest.UsageError(
            "suite integrity (R49): collection floor not met\n  " + "\n  ".join(problems)
        )


def _skip_reason(report: Any) -> str:
    """The reason string pytest recorded for a skipped test."""
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        text = str(longrepr[2])
        prefix = "Skipped: "
        return text[len(prefix) :] if text.startswith(prefix) else text
    return str(longrepr)


def unlisted_skips(
    skipped: Iterable[tuple[str, str]], allowed: Iterable[str]
) -> list[tuple[str, str]]:
    """The ``(nodeid, reason)`` pairs whose reason is not in the allowlist (R49)."""
    permitted = set(allowed)
    return [(nodeid, reason) for nodeid, reason in skipped if reason not in permitted]


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    """Fail the session on any skip whose reason is not in the allowlist (R49)."""
    observed = [
        (str(report.nodeid), _skip_reason(report))
        for report in terminalreporter.stats.get("skipped", [])
    ]
    offenders = unlisted_skips(observed, load_allowed_skips())
    if not offenders:
        return
    terminalreporter.write_sep("=", "suite integrity (R49): unlisted skips", red=True)
    for nodeid, reason in offenders:
        terminalreporter.write_line(f"  {nodeid}: {reason}")
    terminalreporter.write_line(
        f"Add the exact reason to {ALLOWED_SKIPS_PATH.relative_to(REPO)} if the skip is "
        "intended, or make the test run."
    )
    session = getattr(terminalreporter, "_session", None)
    if session is not None:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
