"""R44, R45, R49, R50, R52: the suite's guards, verified independently.

The coder built the harness and states that every one of its guards was tripped
once and confirmed to fail loudly. This module does not take that on trust. For
each guard it constructs the *tripping input* here and asserts the guard rejects
it — a guard that only ever sees good input has never been shown to work, which
is the exact defect R49 and R50 exist to prevent.

The verification lives in a normal test module rather than in ``tests/canaries/``
on purpose: the canary ledger in ``test_suite_integrity.py`` is closed to the two
names increment 1 owns, and adding a third file there would fail
``test_r50_canary_modules_are_all_ledgered``. That tension is real and is written
up in this increment's test report; the assertions themselves are the same
either way.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from .conftest import (
    CREDENTIAL_ENV_VARS,
    collection_floor_problems,
    load_allowed_skips,
    load_collection_floors,
    module_key,
    unlisted_skips,
)
from .harness import (
    DETERMINISM_ENVIRONMENTS,
    assert_deterministic,
    assert_matches_golden,
    flip_one_byte,
    read_golden,
    run_cli,
)
from .test_boundaries_and_posture import (
    ALLOWED_IMPORTS,
    imported_package_modules,
    imported_top_level,
    subpackage_of,
)
from .test_traceability import (
    _cited_in,
    all_test_modules,
    cited_requirements,
    pending_requirements,
    spec_requirements,
)

REPO = Path(__file__).resolve().parent.parent


class TestCollectionFloorGuardR49:
    """R49: the floor hook rejects what it is supposed to reject."""

    def test_r49_a_module_below_its_floor_is_reported(self) -> None:
        """R49: shrinking a module's collected count fails the session."""
        problems = collection_floor_problems({"tests/test_a.py": 4}, {"tests/test_a.py": 5})
        assert len(problems) == 1 and "floor is 5" in problems[0]

    def test_r49_a_module_that_collected_nothing_is_reported_by_name(self) -> None:
        """R49: the message must name the module, or nobody can act on it."""
        problems = collection_floor_problems({}, {"tests/test_vanished.py": 1})
        assert len(problems) == 1
        assert "tests/test_vanished.py" in problems[0]
        assert "collected 0 tests" in problems[0]

    def test_r49_several_violations_are_all_reported_not_just_the_first(self) -> None:
        """R49: a hook that stops at the first problem hides the rest."""
        problems = collection_floor_problems(
            {"tests/test_a.py": 1}, {"tests/test_a.py": 9, "tests/test_b.py": 3}
        )
        assert len(problems) == 2

    def test_r49_growth_and_an_exact_match_both_pass(self) -> None:
        """R49: the guard is not simply always-fail."""
        assert collection_floor_problems({"tests/test_a.py": 5}, {"tests/test_a.py": 5}) == []
        assert collection_floor_problems({"tests/test_a.py": 50}, {"tests/test_a.py": 5}) == []

    def test_r49_an_unlisted_module_is_not_silently_exempt(self) -> None:
        """R49: every test module carries a floor, so none can quietly stop collecting."""
        floors = load_collection_floors()
        missing = [module for module in all_module_paths() if module not in floors]
        assert not missing, f"test modules with no floor entry: {missing}"

    def test_r49_every_floor_entry_names_a_file_that_exists(self) -> None:
        """R49: a floor for a deleted module would pass by naming nothing."""
        for module in load_collection_floors():
            assert (REPO / module).is_file(), module

    def test_r49_no_floor_is_zero(self) -> None:
        """R49: "a floor of 0 can never fail" — the check would be decorative."""
        assert all(floor >= 1 for floor in load_collection_floors().values())

    def test_r49_module_key_is_repository_relative(self) -> None:
        """R49: the hook's key must match the checked-in path spelling."""

        class _Item:
            path = Path(__file__)

        assert module_key(_Item()) == "tests/test_guard_verification.py"  # type: ignore[arg-type]


class TestSkipAllowlistGuardR49:
    """R49: an unlisted skip reason fails the session."""

    def test_r49_an_unlisted_reason_is_an_offender(self) -> None:
        """R49: the failure branch, driven directly."""
        observed = [("tests/test_x.py::test_y", "needs a database")]
        assert unlisted_skips(observed, []) == observed

    def test_r49_only_an_exact_match_is_allowed(self) -> None:
        """R49: "each entry an exact reason string" — no prefix or substring match."""
        observed = [("t::x", "needs a database")]
        assert unlisted_skips(observed, ["needs a database!"]) == observed
        assert unlisted_skips(observed, ["needs a"]) == observed
        assert unlisted_skips(observed, ["NEEDS A DATABASE"]) == observed
        assert unlisted_skips(observed, ["needs a database"]) == []

    def test_r49_several_unlisted_skips_are_all_reported(self) -> None:
        """R49: a partially reported failure trains people to ignore it."""
        observed = [("a::1", "one"), ("b::2", "two"), ("c::3", "ok")]
        assert unlisted_skips(observed, ["ok"]) == observed[:2]

    def test_r49_the_allowlist_is_still_empty(self) -> None:
        """R49: the default posture — a skip is a test that did not run."""
        assert load_allowed_skips() == (), (
            "allowed_skips.txt has grown an entry; every one is a test that does not run"
        )

    def test_r49_this_suite_declares_no_optional_import_skips(self) -> None:
        """R49: no lazy optional-import skip and no ``skipif`` anywhere in the suite.

        The forbidden call name is assembled at runtime so this module does not
        report itself; the rule is about calls, and a literal in an assertion is
        not one.
        """
        forbidden_call = "import" + "orskip"
        offenders: list[str] = []
        for path in [*all_test_modules(), Path(__file__).parent / "harness.py"]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Attribute) and node.attr == forbidden_call:
                    offenders.append(f"{path.name}: {forbidden_call}")
                if isinstance(node, ast.Attribute) and node.attr == "skipif":
                    offenders.append(f"{path.name}: skipif")
        assert not offenders, offenders


class TestTraceabilityGuardR52:
    """R52: an uncited requirement and a bogus citation both fail."""

    def test_r52_a_citation_of_a_nonexistent_requirement_is_detected(self) -> None:
        """R52: the extractor plus the spec set catch an invented id."""
        fake = {"R44", "R999"}
        assert fake - spec_requirements() == {"R999"}

    def test_r52_an_uncited_requirement_with_no_ledger_entry_is_detected(self) -> None:
        """R52: an id that is neither cited nor ledgered fails the check.

        **Rewritten by the increment-5 tester, and the reason is the same one
        increment 4 gave for replacing ``test_r50_later_canaries_are_ledgered_not_forgotten``.**
        The previous version asserted ``pending - cited`` was **non-empty** —
        correct while a debt remained, and wrong the moment it was paid.
        Increment 5 cites R41 and R42, ``traceability_pending.txt`` is now
        empty, and an assertion that a completed ledger must stay incomplete
        is not a check anybody should keep.

        So the guard's arithmetic is driven against a **probe** — an id the
        spec defines, cited by nothing, ledgered by nothing — which keeps the
        mechanism exercised whether or not a real debt exists.

        Red when: the uncited computation is deleted along with the debt it
        used to describe.
        """
        requirements = spec_requirements()
        cited = cited_requirements()
        pending = pending_requirements()
        # The real ledger is consistent: nothing is both uncited and unledgered.
        assert requirements - cited - pending == set()
        # The mechanism, against a probe rather than against the real state.
        probe_id = sorted(requirements, key=lambda item: int(item[1:]))[0]
        assert requirements - (cited - {probe_id}) - pending == {probe_id}
        # ...and it stays quiet when that id *is* ledgered, so the check is
        # not simply always-fail.
        assert requirements - (cited - {probe_id}) - (pending | {probe_id}) == set()

    def test_r52_a_ledger_line_that_is_actually_cited_is_detected(self) -> None:
        """R52: the ledger can only shrink, so a covered id must be removed."""
        cited = cited_requirements()
        assert cited, "nothing is cited; this arm would be vacuous"
        inflated = pending_requirements() | {sorted(cited)[0]}
        assert inflated & cited

    def test_r52_the_extractor_reads_identity_not_comments(self, tmp_path: Path) -> None:
        """R52: "cited" must not come to mean "mentioned"."""
        module = tmp_path / "probe.py"
        module.write_text(
            "\n".join(
                [
                    '"""Module for R' + '11."""',
                    "# comment about R" + "12",
                    'NAME = "R' + '13"',
                    "def helper():",
                    '    """R' + '14 in a non-test helper."""',
                    "def test_probe():",
                    '    """Covers R' + '15."""',
                    "class TestThing:",
                    "    def test_inner(self): pass",
                ]
            ),
            encoding="utf-8",
        )
        assert _cited_in(module) == {"R11", "R15"}

    def test_r52_the_ledger_has_actually_shrunk_this_increment(self) -> None:
        """R52: increment 1's requirements must no longer be ledgered as pending."""
        pending = pending_requirements()
        in_scope = {f"R{number}" for number in range(1, 13)} | {"R38"}
        assert not (pending & in_scope), (
            f"still ledgered although this increment tests them: {sorted(pending & in_scope)}"
        )

    def test_r52_every_in_scope_requirement_is_cited(self) -> None:
        """R52: the positive direction — increment 1's scope is covered by name."""
        expected = {f"R{number}" for number in range(1, 13)} | {
            "R38",
            "R44",
            "R45",
            "R49",
            "R50",
            "R52",
        }
        assert expected <= cited_requirements(), sorted(expected - cited_requirements())

    def test_r52_the_ledger_only_holds_requirements_of_later_increments(self) -> None:
        """R52: nothing pending may be something this increment already built."""
        assert pending_requirements() <= {f"R{number}" for number in range(13, 52)} | {
            "R39",
            "R40",
        }


class TestImportBoundaryGuardR44:
    """R44: the AST rules reject a violating module."""

    def test_r44_the_rule_table_rejects_a_forbidden_import(self, tmp_path: Path) -> None:
        """R44: a ``model`` module importing ``ingest`` violates its rule."""
        module = tmp_path / "trace.py"
        module.write_text("from swarm_observer.ingest.text import slug\n", encoding="utf-8")
        imported = imported_package_modules(module)
        assert imported == {"swarm_observer.ingest.text"}
        allowed = ALLOWED_IMPORTS["model"]
        assert not all(name.split(".")[1] in allowed for name in imported)

    def test_r44_the_rule_table_accepts_a_permitted_import(self, tmp_path: Path) -> None:
        """R44: the guard is not always-fail — ``ingest`` may import ``model``."""
        module = tmp_path / "reader.py"
        module.write_text("from swarm_observer.model.trace import Trace\n", encoding="utf-8")
        imported = imported_package_modules(module)
        assert all(name.split(".")[1] in ALLOWED_IMPORTS["ingest"] for name in imported)

    def test_r44_a_plain_import_statement_is_seen_too(self, tmp_path: Path) -> None:
        """R44: ``import swarm_observer.cli.main`` must not evade the walker."""
        module = tmp_path / "sneaky.py"
        module.write_text("import swarm_observer.cli.main\n", encoding="utf-8")
        assert imported_package_modules(module) == {"swarm_observer.cli.main"}

    def test_r44_a_confined_sdk_import_is_seen(self, tmp_path: Path) -> None:
        """R44: ``anthropic`` anywhere but its adapter is a violation."""
        module = tmp_path / "summary.py"
        module.write_text("import anthropic\n", encoding="utf-8")
        assert "anthropic" in imported_top_level(module)

    def test_r44_subpackage_of_maps_every_shipped_module(self) -> None:
        """R44: a module in an unlisted subpackage must fail loudly, not be skipped."""
        package = REPO / "swarm_observer"
        for path in sorted(package.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            name = subpackage_of(path)
            assert name == "" or name in ALLOWED_IMPORTS, f"{path} has no import rule"

    def test_r44_the_boundaries_hold_right_now(self) -> None:
        """R44: the whole rule set, re-evaluated here rather than trusted."""
        package = REPO / "swarm_observer"
        violations: list[str] = []
        for path in sorted(package.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            name = subpackage_of(path)
            if not name:
                continue
            allowed = ALLOWED_IMPORTS[name]
            for imported in imported_package_modules(path):
                parts = imported.split(".")
                if len(parts) >= 2 and parts[1] not in allowed:
                    violations.append(f"{path.name} -> {imported}")
        assert violations == []


class TestGoldenAndDeterminismHarnessR50:
    """R50: the two harnesses fail when their subject is tampered with."""

    def test_r50_the_golden_comparison_detects_a_changed_character(self) -> None:
        """R50: a golden test that cannot fail is worse than no test."""
        golden = read_golden("schema.json")
        with pytest.raises(AssertionError):
            assert_matches_golden("schema.json", flip_one_byte(golden, 99))

    def test_r50_the_golden_comparison_detects_whitespace_only_damage(self) -> None:
        """R50: byte-for-byte means byte-for-byte, not "equal after normalization"."""
        golden = read_golden("schema.json")
        with pytest.raises(AssertionError):
            assert_matches_golden("schema.json", golden.replace("\n", "\n "))
        with pytest.raises(AssertionError):
            assert_matches_golden("schema.json", golden + "\n")

    def test_r50_the_golden_comparison_reports_both_digests(self) -> None:
        """R50: the failure has to say what differed, or nobody can diagnose it."""
        golden = read_golden("schema.json")
        with pytest.raises(AssertionError) as raised:
            assert_matches_golden("schema.json", golden[:-5])
        message = str(raised.value)
        assert "expected sha256" in message and "got" in message

    def test_r50_the_determinism_harness_detects_a_counter(self) -> None:
        """R50: a producer whose output changes between calls must fail."""
        state = {"n": 0}

        def counting() -> str:
            state["n"] += 1
            return f"value {state['n']}"

        with pytest.raises(AssertionError, match="not deterministic"):
            assert_deterministic(counting)

    def test_r50_the_determinism_harness_detects_an_environment_read(self) -> None:
        """R50: this also proves the harness really varies the environment."""

        def env_dependent() -> str:
            return os.environ.get("TZ", "unset") + os.environ.get("LC_ALL", "unset")

        with pytest.raises(AssertionError, match="not deterministic"):
            assert_deterministic(env_dependent)

    def test_r50_the_determinism_matrix_covers_the_pinned_variables(self) -> None:
        """R50: a matrix that varied nothing relevant would pass everything."""
        varied = {key for environment in DETERMINISM_ENVIRONMENTS for key in environment}
        assert varied == {"PYTHONHASHSEED", "TZ", "LC_ALL"}
        values = {tuple(sorted(environment.items())) for environment in DETERMINISM_ENVIRONMENTS}
        assert len(values) == len(DETERMINISM_ENVIRONMENTS) >= 8

    def test_r50_the_determinism_harness_accepts_a_pure_producer(self) -> None:
        """R50: the control arm, so the guard is not simply always-fail."""
        assert len(assert_deterministic(lambda: "constant")) == 64

    def test_r50_the_cli_runner_reports_a_nonzero_exit(self) -> None:
        """R50: the subprocess harness must notice a command that failed."""
        result = run_cli(["definitely-not-a-command"])
        assert result.returncode != 0


class TestOfflinePostureR45:
    """R45: no credentials, no optional SDK — re-asserted independently."""

    def test_r45_no_provider_credential_is_visible_to_the_suite(self) -> None:
        """R45: the session fixture scrubs all nine variables."""
        assert [name for name in CREDENTIAL_ENV_VARS if name in os.environ] == []

    def test_r45_the_scrub_list_is_the_pinned_nine(self) -> None:
        """R45: a shortened list would let a credential survive into a test."""
        assert set(CREDENTIAL_ENV_VARS) == {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_PROFILE",
            "OPENAI_API_KEY",
            "GH_TOKEN",
        }

    def test_r45_the_optional_extra_is_not_installed(self) -> None:
        """R45: the offline suite runs with ``anthropic`` absent."""
        assert importlib.util.find_spec("anthropic") is None

    def test_r45_the_ingestion_path_needs_no_credential(self, tmp_path: Path) -> None:
        """R45: a full ingest with every credential unset, end to end."""
        from swarm_observer.ingest.claude_code.mapper import ClaudeCodeSource
        from swarm_observer.ingest.source import IngestLimits

        from . import factories as factory

        path = factory.write_trace(
            tmp_path, [factory.assistant("a1", usage_block=factory.usage(input_tokens=1))]
        )
        trace = ClaudeCodeSource(read_sidecars=False).load([path], IngestLimits())
        assert len(trace.spans) == 1

    def test_r45_the_cli_runs_in_a_subprocess_with_no_credentials(self) -> None:
        """R45: the same posture through a fresh interpreter."""
        result = run_cli(["schema"])
        assert result.returncode == 0
        assert result.stdout.startswith("{")


def all_module_paths() -> list[str]:
    """Every test module as a repository-relative posix path."""
    return sorted(path.relative_to(REPO).as_posix() for path in all_test_modules())
