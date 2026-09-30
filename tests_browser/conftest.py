"""Fixtures and integrity hooks for the browser suite (R34, R49).

Why this is a second top-level test tree rather than a marker inside ``tests/``
------------------------------------------------------------------------------

R45 pins one property of ``tests/``: the entire suite passes offline, with every
provider credential unset and with ``anthropic`` not installed. R49 pins
another: an optional test dependency is imported at **module scope**, never
behind ``pytest.importorskip`` and never behind ``skipif``, so a missing one is
a collection error rather than a skip nobody counts.

Those two together rule out putting a Playwright test inside ``tests/``. A
module-scope ``import playwright`` there would make the R45 job — which
installs only ``.[dev]`` — fail at collection. Guarding it with ``skipif`` or a
marker CI deselects is the other half of the trap: it is precisely the shape of
this project's predecessor's third failure, "a live path that could never have
passed", and of the ``live_narrator`` deselection, which is tolerable only
because a test separately asserts the deselected count is non-zero.

So the browser checks live in their own tree, with their own CI job that
installs Chromium and runs them. In *that* job Playwright is not optional: it is
imported at module scope and a missing browser is a hard error. Neither tree
skips anything; each one runs everything it collects.

``tests/test_browser_suite_wiring.py`` is the seam back: it runs in the offline
suite and fails if this tree stops existing or if the CI job stops running it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from tests.pipeline import Analysis, analyze_paths

REPO = Path(__file__).resolve().parent.parent
TRACES = REPO / "tests" / "fixtures" / "traces"

#: The minimum number of tests this tree must collect — this tree's copy of
#: R49's collection floor, which lives in ``tests/collection_floor.json`` for
#: the offline suite and cannot reach here. Raise it deliberately.
COLLECTION_FLOOR = 23


@pytest.fixture(scope="session")
def analysis() -> Analysis:
    """One render of the whole fixture corpus — the document the browser loads."""
    return analyze_paths(sorted(TRACES.glob("*.jsonl")))


@pytest.fixture(scope="session")
def report_url(analysis: Analysis, tmp_path_factory: pytest.TempPathFactory) -> str:
    """The rendered report on disk, as a ``file://`` URL."""
    path = tmp_path_factory.mktemp("report") / "report.html"
    path.write_text(analysis.html, encoding="utf-8")
    return path.as_uri()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """One Chromium for the session.

    ``--no-sandbox`` because CI runners and containers commonly lack the user
    namespaces Chromium's sandbox needs; the page under test is a local file
    with no network access of its own (R35), which is what the offline suite
    separately proves.
    """
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(args=["--no-sandbox"])
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def page(browser: Browser, report_url: str) -> Iterator[Page]:
    """A fresh page with the report loaded, failing on any console error.

    A console error or an uncaught exception is a defect in the one script R34
    allows, so it fails the test that provoked it rather than being reported as
    a note nobody reads.
    """
    context = browser.new_context()
    instance = context.new_page()
    problems: list[str] = []
    instance.on("console", lambda message: _record(problems, message))
    instance.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    instance.goto(report_url)
    try:
        yield instance
    finally:
        context.close()
    assert not problems, problems


def _record(problems: list[str], message: object) -> None:
    kind = getattr(message, "type", "")
    if kind in {"error", "warning"}:
        problems.append(f"console {kind}: {getattr(message, 'text', message)!r}")


def pytest_collection_finish(session: pytest.Session) -> None:
    """R49's collection floor, for this tree."""
    if session.config.option.keyword or session.config.option.markexpr:
        return
    count = len(session.items)
    if count < COLLECTION_FLOOR:
        raise pytest.UsageError(
            f"browser suite integrity (R49): collected {count} tests, floor is "
            f"{COLLECTION_FLOOR}. A tree that silently stops collecting is the "
            "failure this floor exists to catch."
        )


def pytest_terminal_summary(
    terminalreporter: object, exitstatus: int, config: pytest.Config
) -> None:
    """R49: no skips at all in this tree.

    ``tests/allowed_skips.txt`` deliberately lists nothing; this tree has no
    allowlist to consult, because a browser check that skipped would be exactly
    the green-but-unable-to-fail result it was added to prevent.
    """
    stats = getattr(terminalreporter, "stats", {})
    skipped = list(stats.get("skipped", []))
    if not skipped:
        return
    for report in skipped:
        print(f"  unexpected skip: {report.nodeid}")
    session = getattr(terminalreporter, "_session", None)
    if session is not None:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
