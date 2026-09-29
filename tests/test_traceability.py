"""R52: every requirement is cited by a test, and every citation exists.

Set equality in both directions. An uncited requirement fails, so a requirement
cannot be quietly dropped; a citation of a non-existent requirement fails, so a
renumbering cannot leave dead references behind.

Citations are read from test *identity* — module docstrings, class and function
names, and their docstrings — not from arbitrary comments. A requirement id
mentioned in passing inside a helper is not evidence that anything tests it,
and counting it would make this check reward mentioning over testing.

Increment 1's ledger of not-yet-cited ids lives in
``tests/traceability_pending.txt``; see that file for why it exists and how it
is meant to shrink.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
SPEC = REPO / "docs" / "specs" / "swarm-observer-v1.md"
PENDING_PATH = TESTS_DIR / "traceability_pending.txt"

#: A requirement is *defined* by its bullet in the spec's Requirements section.
_DEFINITION = re.compile(r"^- (R\d+) \(", re.MULTILINE)

#: A requirement is *cited* by its bare id anywhere in a test's identity.
_CITATION = re.compile(r"\bR(\d+)\b")


def spec_requirements() -> set[str]:
    """Every requirement id the approved spec defines."""
    return set(_DEFINITION.findall(SPEC.read_text(encoding="utf-8")))


def pending_requirements() -> set[str]:
    """Ids no test cites yet, per the checked-in ledger."""
    lines = PENDING_PATH.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}


def all_test_modules() -> list[Path]:
    """Every collected test module, canaries included."""
    return sorted(path for path in TESTS_DIR.rglob("test_*.py") if "__pycache__" not in path.parts)


def _cited_in(path: Path) -> set[str]:
    """Requirement ids cited by one module's test identity."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    texts: list[str] = []
    module_docstring = ast.get_docstring(tree)
    if module_docstring:
        texts.append(module_docstring)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if not (node.name.startswith("test") or node.name.startswith("Test")):
                continue
            texts.append(node.name)
            docstring = ast.get_docstring(node)
            if docstring:
                texts.append(docstring)
    return {f"R{number}" for text in texts for number in _CITATION.findall(text)}


def cited_requirements() -> set[str]:
    """Requirement ids cited across the whole suite."""
    return set().union(*(_cited_in(path) for path in all_test_modules()))


class TestTraceabilityR52:
    """R52: the spec's requirement ids and the suite's citations agree."""

    def test_r52_every_requirement_is_cited_or_ledgered(self) -> None:
        """R52: no requirement is silently untested."""
        uncited = spec_requirements() - cited_requirements() - pending_requirements()
        assert not uncited, (
            "requirements with no test citation and no ledger entry: "
            f"{sorted(uncited, key=lambda item: int(item[1:]))}"
        )

    def test_r52_every_citation_names_a_real_requirement(self) -> None:
        """R52: a citation of a non-existent requirement fails."""
        unknown = cited_requirements() - spec_requirements()
        assert not unknown, (
            f"tests cite requirement ids the spec does not define: {sorted(unknown)}"
        )

    def test_r52_ledger_only_lists_real_and_uncited_requirements(self) -> None:
        """R52: the pending ledger cannot rot — it only shrinks."""
        pending = pending_requirements()
        stale = pending - spec_requirements()
        assert not stale, f"traceability_pending.txt lists ids the spec does not define: {stale}"
        now_covered = pending & cited_requirements()
        assert not now_covered, (
            "these requirements are cited by tests and must be removed from "
            f"traceability_pending.txt: {sorted(now_covered, key=lambda item: int(item[1:]))}"
        )

    def test_r52_citation_extraction_reads_identity_not_comments(self, tmp_path: Path) -> None:
        """R52: the extractor must not count a bare mention in a comment.

        The probe below cites two ids from a module docstring and a test
        docstring, and two more from a comment and a string constant. Only the
        first two may count, or "cited" would come to mean "mentioned".
        """
        source = "\n".join(
            [
                '"""Probe module for R' + '44."""',
                "",
                "# a comment about R" + "13",
                'CONSTANT = "R' + '14"',
                "",
                "def test_probe():",
                '    """Covers R' + '45."""',
                "",
            ]
        )
        module = tmp_path / "probe_module.py"
        module.write_text(source, encoding="utf-8")
        assert _cited_in(module) == {"R44", "R45"}

    def test_r52_the_suite_actually_has_modules_to_scan(self) -> None:
        """R52: an empty scan would make both directions vacuously pass."""
        modules = all_test_modules()
        assert len(modules) >= 3, f"traceability scanned only {len(modules)} modules"
        assert cited_requirements(), "no requirement citation found anywhere in the suite"


def test_r52_spec_is_present_and_parseable() -> None:
    """R52: the traceability source of truth is the approved spec file itself."""
    assert SPEC.is_file(), f"spec not found at {SPEC}"
    requirements = spec_requirements()
    assert len(requirements) == 52, f"expected 52 requirements, parsed {len(requirements)}"
    numbers = sorted(int(item[1:]) for item in requirements)
    assert numbers == list(range(1, 53)), f"requirement numbering has a gap: {numbers}"


if __name__ == "__main__":  # pragma: no cover - convenience for a manual run
    raise SystemExit(pytest.main([__file__]))
