"""Rewrite the checked-in golden reports. Run deliberately; never from a test.

    python3 -m tests.golden.regenerate        # from the repository root

A golden that a test regenerates when it fails is a check that cannot fail, so
the only way to move one is to run this script and look at the diff. What each
golden pins, and what it does *not* tell you, is written in
``tests/test_report_html_r34_r35_r36.py::TestGoldenDocuments``: the bytes are the
ordering and whitespace R47 promises, and the structural assertions beside them
are what say whether a security property broke.

The inputs come from ``tests/hostile_corpus.py``, which is checked in, so the
goldens are a function of this repository and of nothing on the machine that
regenerates them — asserted by
``test_r47_the_golden_is_a_function_of_the_inputs_and_not_of_the_directory``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tests.hostile_corpus import write_hostile_trace
from tests.pipeline import analyze_paths

GOLDEN_DIR = Path(__file__).resolve().parent

#: golden file name → whether that run includes previews (R38, A10).
GOLDENS: dict[str, bool] = {
    "report_hostile_extended.html": True,
    "report_hostile_extended_no_previews.html": False,
}


def main() -> None:
    for name, previews in sorted(GOLDENS.items()):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_hostile_trace(Path(directory))
            analysis = analyze_paths(paths, previews=previews)
        (GOLDEN_DIR / name).write_text(analysis.html, encoding="utf-8")
        print(f"wrote {name} ({len(analysis.html)} bytes)")


if __name__ == "__main__":
    main()
