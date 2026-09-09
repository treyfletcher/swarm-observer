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

CURRENT_INCREMENT = 1


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
