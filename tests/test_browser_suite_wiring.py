"""The seam between the offline suite and the browser suite (BUG-19, R34, R49).

``tests_browser/`` cannot run here: it imports Playwright at module scope, and
the R45 job installs only ``.[dev]``. That is the right split — but it means the
offline suite has no way to notice if the browser tree is emptied, renamed, or
quietly dropped from CI, and "a check that stopped running and nobody noticed"
is this project's signature defect in its purest form.

So this module asserts the *wiring*, from the offline side:

* the tree exists and still holds the behavioural checks, counted from its AST;
* ``.github/workflows/ci.yml`` has a job that runs it, with nothing deselected;
* the offline job does **not** run it, and ``testpaths`` keeps a bare ``pytest``
  from collecting it, so the R45 job's meaning is unchanged;
* the browser tree obeys R49 — no ``importorskip``, no ``skipif``, no marker
  that CI could deselect.

**What this module is not.** Every assertion here reads a file and compares it
to a string. That is the weakest class of check in this repository — a constant
checking a constant — and it cannot tell you the browser tests pass, only that
something is still pointed at them. The strength is entirely in the job it
names. It is included because the alternative is no offline signal at all when
that job disappears, and because a wiring check is honest about being wiring in
a way that a "the script contains ``closest``" assertion pretending to be a
behavioural test would not be. The one structural assertion about the script's
text is labelled as exactly that where it is made.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from swarm_observer.report.html import REPORT_SCRIPT

from .conftest import REPO

BROWSER_TREE = REPO / "tests_browser"
BROWSER_MODULE = BROWSER_TREE / "test_report_interactions_r34.py"
BROWSER_CONFTEST = BROWSER_TREE / "conftest.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

#: The minimum number of ``test_`` functions the browser module must define.
#: Parametrisation makes the collected count larger; this is the floor on the
#: source, which is all the offline suite can see. Raise it deliberately.
BROWSER_TEST_FLOOR = 11


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _test_function_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            names.append(node.name)
    return names


class TestTheBrowserTreeExists:
    """The tree the browser job runs, as the offline suite can see it."""

    def test_the_tree_and_its_module_are_checked_in(self) -> None:
        """Red when: the browser suite is deleted or renamed without this being updated."""
        assert BROWSER_TREE.is_dir(), BROWSER_TREE
        assert BROWSER_CONFTEST.is_file(), BROWSER_CONFTEST
        assert BROWSER_MODULE.is_file(), BROWSER_MODULE

    def test_the_module_still_holds_behavioural_tests(self) -> None:
        """Red when: the module is emptied, which is how a check stops being able to fail."""
        names = _test_function_names(BROWSER_MODULE)
        assert len(names) >= BROWSER_TEST_FLOOR, names

    def test_the_tree_asserts_the_collapse_behaviour_by_name(self) -> None:
        """Red when: the BUG-19 assertions specifically are removed while the tree remains.

        A floor on the count alone would be satisfied by twelve tests of
        something else.
        """
        names = set(_test_function_names(BROWSER_MODULE))
        required = {
            "test_collapsing_hides_the_content_and_keeps_the_heading_and_control",
            "test_collapsing_is_reversible",
            "test_the_collapsed_class_lands_on_the_section_not_on_the_title_row",
            "test_aria_expanded_tracks_the_collapsed_state",
        }
        assert required <= names, sorted(required - names)


class TestTheBrowserTreeObeysR49:
    """R49: optional dependencies at module scope; no skip can hide a browser test."""

    @pytest.mark.parametrize("path_name", ["conftest.py", "test_report_interactions_r34.py"])
    def test_no_importorskip_and_no_skipif(self, path_name: str) -> None:
        """Red when: the browser tree grows the escape hatch R49 forbids.

        Over the AST rather than the bytes: these modules *discuss* the escape
        hatches at length, and a substring scan would be red on the prose
        explaining why they are absent.
        """
        tree = ast.parse((BROWSER_TREE / path_name).read_text(encoding="utf-8"))
        forbidden = {"importorskip", "skipif", "skip", "xfail"}
        offenders = sorted(
            {
                node.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr in forbidden
            }
            | {
                node.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id in forbidden
            }
        )
        assert not offenders, (path_name, offenders)

    def test_playwright_is_imported_at_module_scope(self) -> None:
        """Red when: the import moves inside a function, which is how it becomes optional."""
        tree = ast.parse(BROWSER_CONFTEST.read_text(encoding="utf-8"))
        top_level = {
            alias.name.split(".")[0]
            for node in tree.body
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in (node.names if isinstance(node, ast.Import) else [])
        }
        top_level |= {
            node.module.split(".")[0]
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "playwright" in top_level, sorted(top_level)

    def test_the_browser_tree_registers_no_marker_ci_could_deselect(self) -> None:
        """Red when: a ``browser`` marker appears — the shape of the instance-three trap.

        A marker is not wrong in itself; a marker plus a CI ``-m "not browser"``
        is. The simplest thing that forecloses it is to have no marker at all,
        and the workflow assertions below check the other half.
        """
        for path in sorted(BROWSER_TREE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            assert "pytest.mark.browser" not in text, path
        config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        markers = config["tool"]["pytest"]["ini_options"]["markers"]
        assert not [marker for marker in markers if marker.startswith("browser")], markers


class TestCIRunsTheBrowserTree:
    """The wiring, read out of the workflow.

    A constant checking a constant — see the module docstring for what that is
    and is not worth.
    """

    def test_a_job_runs_the_browser_tree(self) -> None:
        """Red when: the browser job is removed or stops naming the tree."""
        text = _workflow_text()
        assert "\n  browser:\n" in text, "no `browser` job in the workflow"
        assert "python -m pytest tests_browser" in text

    def test_the_browser_job_installs_a_browser(self) -> None:
        """Red when: the job runs without Chromium, which would make it error, not skip.

        An error is the correct outcome and is why this is asserted rather than
        left to chance: there is no arrangement in which the browser job passes
        without having run a browser.
        """
        text = _workflow_text()
        assert "playwright install --with-deps chromium" in text

    def test_the_browser_job_deselects_nothing(self) -> None:
        """Red when: a ``-m``/``-k`` filter appears on the browser invocation.

        The whole point of this job is that it runs everything it collects. A
        filter here would recreate ``live_narrator``'s shape without
        ``live_narrator``'s separately-asserted non-zero deselection count.
        """
        text = _workflow_text()
        line = next(
            candidate
            for candidate in text.splitlines()
            if "python -m pytest tests_browser" in candidate
        )
        # Everything after `pytest` — `python -m pytest` is the invocation, not
        # a marker expression.
        arguments = line.split("python -m pytest", 1)[1]
        assert " -m " not in arguments, line
        assert " -k " not in arguments, line
        assert "--ignore" not in arguments, line
        assert "--deselect" not in arguments, line

    def test_the_offline_job_does_not_run_the_browser_tree(self) -> None:
        """Red when: ``tests_browser`` is added to the R45 job, which installs no Playwright.

        It would be a collection error there, not a skip — loud, but it would
        break the job R45 pins.
        """
        text = _workflow_text()
        offline = next(
            candidate
            for candidate in text.splitlines()
            if 'python -m pytest -m "not live_narrator"' in candidate
        )
        assert "tests_browser" not in offline, offline

    def test_a_bare_pytest_collects_only_the_offline_tree(self) -> None:
        """``testpaths`` is what keeps the two trees apart for a developer as well as CI."""
        config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


class TestTheScriptStructure:
    """One structural assertion about the script's text, labelled as what it is.

    This is option 3 from the post-review ruling: a constant asserting something
    about another constant. It cannot tell you the collapse works — that is what
    the browser job is for — and if the browser job ever disappears this test
    will keep passing while the feature is broken, exactly as the SHA-256 pin
    did. It is kept for one narrow reason: it names the root cause in the place
    a reader of ``report/html.py`` will be standing when they next edit the
    script, and it costs nothing.
    """

    def test_the_collapse_root_is_found_with_closest_not_parent_node(self) -> None:
        """Red when: the handler goes back to ``parentNode`` (BUG-19)."""
        assert 'closest(".section")' in REPORT_SCRIPT
        assert "parentNode" not in REPORT_SCRIPT

    def test_the_control_state_is_written_by_the_handler(self) -> None:
        """Red when: the ``aria-expanded`` write or the label flip is dropped (BUG-19)."""
        assert 'setAttribute("aria-expanded"' in REPORT_SCRIPT
        assert "textContent" in REPORT_SCRIPT
