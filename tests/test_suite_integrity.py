"""R49 and R50: the suite's own guards are well-formed and can fail.

R49's two hooks live in ``conftest.py``; this module checks the data they read
and — more importantly — exercises their decision logic directly against inputs
that must trip it. A hook whose failure branch has never executed is exactly the
thing R49 exists to prevent, so "the floor check rejects a module that collected
nothing" is asserted here rather than assumed.

R50's canaries live in ``tests/canaries/``. The ledger below records every
canary the spec requires and the increment its subject arrives in, so a missing
canary is a visible debt rather than an absence nobody notices.
"""

from __future__ import annotations

import json
import tomllib
import unicodedata
from pathlib import Path

from .conftest import (
    ALLOWED_SKIPS_PATH,
    COLLECTION_FLOOR_PATH,
    REPO,
    TESTS_DIR,
    collection_floor_problems,
    load_allowed_skips,
    load_collection_floors,
    unlisted_skips,
)

#: R50's required canaries: name → the increment whose work creates its subject.
#: Increment 1 owns the two whose subjects exist now; the rest are debts this
#: file names out loud.
REQUIRED_CANARIES: dict[str, int] = {
    "golden_byte_flip": 1,
    "determinism_harness": 1,
    "injection_probe_identity_escape": 4,
    "attribute_allowlist_injection": 4,
    "offline_socket_permitted": 4,
    "redaction_pattern_removed": 4,
    "detector_coverage_dropped": 2,
}

CURRENT_INCREMENT = 2


def all_test_modules() -> list[str]:
    """Every test module, as repository-relative posix paths."""
    return sorted(
        path.relative_to(REPO).as_posix()
        for path in TESTS_DIR.rglob("test_*.py")
        if "__pycache__" not in path.parts
    )


class TestCollectionFloorR49:
    """R49: the collection floor is honest and its failure branch works."""

    def test_r49_floor_file_is_well_formed(self) -> None:
        """R49: the floor file is checked in, parseable, and non-trivial."""
        floors = load_collection_floors()
        assert floors, "collection_floor.json lists no modules"
        for module, floor in floors.items():
            assert floor >= 1, f"{module}: a floor of {floor} can never fail"
            assert (REPO / module).is_file(), f"{module}: listed in the floor but does not exist"

    def test_r49_every_test_module_has_a_floor(self) -> None:
        """R49: a module with no floor is a module that may silently collect zero."""
        missing = [
            module for module in all_test_modules() if module not in load_collection_floors()
        ]
        assert not missing, (
            "test modules with no entry in collection_floor.json: "
            f"{missing}. Add one — the friction is the point (A11)."
        )

    def test_r49_floor_check_rejects_a_module_that_collected_nothing(self) -> None:
        """R49: the guard's failure branch fires on a zero-collect module."""
        problems = collection_floor_problems({}, {"tests/test_ghost.py": 3})
        assert len(problems) == 1
        assert "tests/test_ghost.py" in problems[0]
        assert "collected 0 tests" in problems[0]

    def test_r49_floor_check_rejects_a_module_below_its_floor(self) -> None:
        """R49: shrinking a module's test count fails the session."""
        problems = collection_floor_problems({"tests/test_thing.py": 2}, {"tests/test_thing.py": 5})
        assert len(problems) == 1
        assert "floor is 5" in problems[0]

    def test_r49_floor_check_accepts_growth(self) -> None:
        """R49: adding tests never breaks a floor."""
        assert (
            collection_floor_problems({"tests/test_thing.py": 99}, {"tests/test_thing.py": 5}) == []
        )


class TestSkipAllowlistR49:
    """R49: no test is skipped for a reason nobody wrote down."""

    def test_r49_allowlist_is_well_formed(self) -> None:
        """R49: the allowlist parses, and every entry is distinct."""
        assert ALLOWED_SKIPS_PATH.is_file()
        entries = load_allowed_skips()
        assert len(entries) == len(set(entries)), (
            f"duplicate entries in allowed_skips.txt: {entries}"
        )

    def test_r49_the_suite_currently_allows_no_skips(self) -> None:
        """R49: the default posture is that a skip is a failure."""
        assert load_allowed_skips() == (), (
            "allowed_skips.txt has grown entries; each one is a test that does not run"
        )

    def test_r49_skip_check_rejects_an_unlisted_reason(self) -> None:
        """R49: the guard's failure branch fires on an unknown skip reason."""
        offenders = unlisted_skips([("tests/test_x.py::test_y", "needs a database")], [])
        assert offenders == [("tests/test_x.py::test_y", "needs a database")]

    def test_r49_skip_check_accepts_an_allowlisted_reason(self) -> None:
        """R49: an allowlisted reason passes, so the guard is not simply always-fail."""
        allowed = ("needs a database",)
        assert unlisted_skips([("tests/test_x.py::test_y", "needs a database")], allowed) == []

    def test_r49_live_narrator_marker_is_registered(self) -> None:
        """R49: the one marker CI deselects is declared, so it cannot become a typo."""
        config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        markers = config["tool"]["pytest"]["ini_options"]["markers"]
        assert any(marker.startswith("live_narrator:") for marker in markers), markers


class TestCanaryLedgerR50:
    """R50: every guard has a proof it can fail, and the missing ones are named."""

    def test_r50_this_increments_canaries_exist(self) -> None:
        """R50: a required canary whose subject exists must be checked in."""
        canaries = TESTS_DIR / "canaries"
        for name, increment in sorted(REQUIRED_CANARIES.items()):
            if increment > CURRENT_INCREMENT:
                continue
            path = canaries / f"test_canary_{name}.py"
            assert path.is_file(), f"required canary missing: {path.relative_to(REPO)}"

    def test_r50_later_canaries_are_ledgered_not_forgotten(self) -> None:
        """R50: the debt is written down with the increment that clears it."""
        outstanding = {
            name: increment
            for name, increment in REQUIRED_CANARIES.items()
            if increment > CURRENT_INCREMENT
        }
        assert outstanding, "the canary ledger is empty; R50 lists seven required canaries"
        assert all(increment <= 5 for increment in outstanding.values())

    def test_r50_every_canary_module_proves_a_failure(self) -> None:
        """R50: a canary must assert its guard *raises*, whether or not it is required.

        This assertion used to be ``present <= REQUIRED_CANARIES`` — a closed
        name list, which is stricter than R50 (R50 requires certain canaries to
        exist; it does not forbid others) and which locked the directory against
        the one role most likely to need a new guard proof. The tester hit
        exactly that and had to put its independent guard verification in a
        normal module instead.

        What the closed list was really protecting is the property below: a
        "canary" that never asserts a failure is a canary in name only, and is
        the thing R50 exists to prevent. That is now checked directly, so an
        extra canary is welcome and an inert one is not.
        """
        canaries = sorted((TESTS_DIR / "canaries").glob("test_canary_*.py"))
        assert canaries, "the canary directory is empty"
        for path in canaries:
            source = path.read_text(encoding="utf-8")
            assert "pytest.raises" in source, (
                f"{path.relative_to(REPO)} asserts no failure; a canary that cannot "
                "observe its guard failing is a canary in name only (R50)"
            )
            assert "R50" in source, f"{path.relative_to(REPO)} does not cite R50"


class TestCheckedInDataIsInterpreterStableR8:
    """R8: committed test data may not contain a code point the interpreters disagree on.

    Found while fixing the 3.12 nesting-bomb failure. R8 defines preview
    normalization as "replace every character that is not ``str.isprintable()``",
    and ``str.isprintable()`` answers from the Unicode table compiled into the
    running interpreter — 14.0 on CPython 3.11, 15.0 on 3.12. A code point
    assigned in 15.0 is printable on 3.12 and becomes a space on 3.11, so the
    *same trace* yields different ``Span.text_preview`` bytes on the two
    interpreters CI builds. That collides with the byte-identical guarantee the
    spec makes for reports, and it will silently make increment 4's golden files
    version-dependent.

    R8 names ``str.isprintable()`` explicitly, so the implementation is correct
    and the fix is a spec amendment, not a code change (recorded for the PM in
    the review). What is fixable here is the blast radius: keep the drift out of
    checked-in data. A code point unassigned on the running interpreter is one a
    newer interpreter may assign, so it is exactly the set at risk — and because
    CI runs the oldest supported interpreter too, that leg catches anything a
    newer one would render differently. The version matrix is the enforcement,
    which is the lesson of the bug that prompted this test.
    """

    def data_files(self) -> list[Path]:
        """Every checked-in fixture and golden file, as text."""
        roots = (TESTS_DIR / "fixtures", TESTS_DIR / "golden")
        return sorted(
            path
            for root in roots
            if root.is_dir()
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )

    def test_r8_the_scan_has_files_to_scan(self) -> None:
        """R8: an empty corpus would make the check below vacuously pass."""
        files = self.data_files()
        assert files, "no fixture or golden files found to scan"
        assert any(path.suffix == ".jsonl" for path in files)

    def test_r8_no_committed_character_is_unassigned_on_this_interpreter(self) -> None:
        """R8: an unassigned code point renders differently on a newer interpreter."""
        offenders: list[str] = []
        for path in self.data_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:  # pragma: no cover - no binary fixtures today
                continue
            for index, char in enumerate(text):
                if unicodedata.category(char) == "Cn":
                    offenders.append(f"{path.relative_to(REPO)} offset {index}: U+{ord(char):04X}")
        assert not offenders, (
            "committed test data contains code points unassigned in Unicode "
            f"{unicodedata.unidata_version} (this interpreter). They are printable on a "
            "newer interpreter and a space on this one, so previews — and any golden "
            f"built from them — differ by Python version: {offenders[:10]}"
        )

    def test_r8_the_check_would_catch_a_drifting_code_point(self) -> None:
        """R8: the guard's failure branch fires on a code point from a later table.

        U+1F6DC was assigned in Unicode 15.0. On CPython 3.11 it is ``Cn`` and
        previews as a space; on 3.12 it is ``So`` and previews as itself. Whichever
        interpreter is running, at least one of the two probes below must be
        unassigned, or this guard has nothing to detect and is inert.
        """
        later_additions = ("\U0001f6dc", "\U0001e030", "\U00011f00")  # Unicode 15.0
        much_later = ("\U00013460", "\U00016d40")  # Unicode 16.0
        candidates = later_additions + much_later
        assert any(unicodedata.category(char) == "Cn" for char in candidates), (
            "every probe code point is assigned on this interpreter; the guard "
            "needs a probe from a table newer than "
            f"{unicodedata.unidata_version}"
        )


def test_r49_collection_floor_json_has_a_stable_shape() -> None:
    """R49: the floor file's shape is part of the contract, not an accident."""
    data = json.loads(COLLECTION_FLOOR_PATH.read_text(encoding="utf-8"))
    assert set(data) == {"comment", "modules"}, sorted(data)
    assert isinstance(data["modules"], dict)
    assert all(isinstance(value, int) for value in data["modules"].values())


def test_r49_fixture_corpus_directory_exists() -> None:
    """R49: the mapper fixture is checked in, so the adapter has a real subject."""
    fixture = Path(TESTS_DIR / "fixtures" / "mapper" / "stream_fragments.jsonl")
    assert fixture.is_file()
    assert fixture.read_text(encoding="utf-8").strip(), "the fixture is empty"
