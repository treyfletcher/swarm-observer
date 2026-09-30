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

import ast
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
    # Moved 4 → 3 by the increment-3 tester in the same commit as the canary.
    # ``report/redact.py`` (R33) landed early because R30 requires the unpriced
    # table's recorded model id to be redacted, so the subject exists now.
    "redaction_pattern_removed": 3,
    "detector_coverage_dropped": 2,
    # Not one of R50's seven. Added by the increment-2 review (S13): R16's
    # tool-name guard is a shape check, so a credential-shaped string that is a
    # legal tool name reaches ``metrics.tool_name`` verbatim. R51 promises no
    # credential-shaped payload appears in a rendered report, which is only true
    # if R33's redaction covers ``metrics`` as well as ``previews``. R50 requires
    # certain canaries to exist and does not forbid others (increment-1 review,
    # tension 1), and a debt written down is the only kind that gets paid.
    "metrics_redaction_dropped": 4,
    # Not one of R50's seven either. Added by the increment-5 tester, in the
    # same commit as the canary. R43's per-group fallback has an unusual
    # failure mode: when it breaks, a paragraph still renders, the section
    # still exists and the exit code is unchanged -- what is lost is a
    # reader's ability to tell swarm-observer's own summary from a language
    # model's prose, and (for the A-e6 short-circuit) the number of outbound
    # requests a refused credential produces. Silent and cosmetic is exactly
    # the failure mode R50 exists for.
    "narrator_fallback_dropped": 5,
    # Increment 3 note for the tester: ``report/redact.py`` (R33) landed early,
    # because R30 requires the unpriced table's recorded model id to be redacted
    # and ``report/json_out.py`` cannot honestly write trace text without it.
    # Its subject therefore exists *now*, so ``redaction_pattern_removed`` can
    # move from 4 to 3 as soon as somebody writes the canary. It is left at 4
    # rather than moved by the coder, because moving it without writing the
    # canary would fail this ledger on the next run — the debt is real either
    # way and this is where it is recorded.
}

#: R50's seven required canaries, typed from the requirement rather than read
#: from :data:`REQUIRED_CANARIES`. The ledger may hold more (the increment-2
#: review added ``metrics_redaction_dropped``); it may not hold fewer.
R50_REQUIRED_BY_NAME: frozenset[str] = frozenset(
    {
        "determinism_harness",
        "golden_byte_flip",
        "injection_probe_identity_escape",
        "attribute_allowlist_injection",
        "offline_socket_permitted",
        "redaction_pattern_removed",
        "detector_coverage_dropped",
    }
)

#: Increment 4 (tester). The four canaries this increment owed —
#: ``injection_probe_identity_escape``, ``attribute_allowlist_injection``,
#: ``offline_socket_permitted`` and ``metrics_redaction_dropped`` — are checked
#: in, so the ledger is bumped in the same commit as they arrive, the way
#: ``redaction_pattern_removed`` was moved in increment 3. With this bump every
#: canary R50 names exists and the ledger's outstanding set is **empty**, which
#: is why ``test_r50_later_canaries_are_ledgered_not_forgotten`` was replaced by
#: ``test_r50_every_canary_the_requirement_names_is_checked_in``: the old test
#: asserted the debt was non-empty, and an assertion that a completed ledger
#: must stay incomplete is not a check anybody should keep.
#: Increment 5 (tester). Bumped here, in the same commit as
#: ``narrator_fallback_dropped``, so the ledger and the directory move
#: together -- the discipline increments 3 and 4 used and the one the
#: increment-5 coder deliberately left to this role.
CURRENT_INCREMENT = 5


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

    def test_r50_every_canary_the_requirement_names_is_checked_in(self) -> None:
        """R50: all seven named canaries exist, and the list is the requirement's.

        Replaces ``test_r50_later_canaries_are_ledgered_not_forgotten``, which
        asserted the outstanding set was **non-empty** — correct while a debt
        remained and wrong the moment it was paid. Increment 4 delivered the last
        four, so the check that survives is the one that can still fail: every
        name R50 lists is in the ledger and has a file.

        Red when: a canary is deleted, renamed, or dropped from the ledger.
        """
        canaries = TESTS_DIR / "canaries"
        assert set(REQUIRED_CANARIES) >= R50_REQUIRED_BY_NAME
        for name in sorted(R50_REQUIRED_BY_NAME):
            path = canaries / f"test_canary_{name}.py"
            assert path.is_file(), f"R50 names this canary and it is missing: {name}"
            assert REQUIRED_CANARIES[name] <= CURRENT_INCREMENT, name

    def test_r50_a_later_increments_debt_would_still_be_ledgered(self) -> None:
        """R50: the ledger's outstanding set is a live mechanism, not a dead field.

        The debt is empty today. The mechanism that reports one is asserted
        against a probe rather than against the real ledger, so it keeps working
        for increment 5 and does not require a debt to exist in order to pass.

        Red when: the outstanding computation is deleted along with the debt it
        used to describe.
        """
        # The hypothetical moved from `narrator_fallback_dropped: 5` to an
        # increment-6 name when increment 5 delivered that canary and bumped
        # `CURRENT_INCREMENT`. The coder's PR names this substitution as one
        # the tester owes in the same commit; leaving the old probe would
        # have made a *delivered* canary read as an outstanding debt, which
        # is the mirror of the assertion this test replaced.
        probe = {**REQUIRED_CANARIES, "replay_target_unconstrained": 6}
        outstanding = {
            name: increment for name, increment in probe.items() if increment > CURRENT_INCREMENT
        }
        assert outstanding == {"replay_target_unconstrained": 6}
        assert all(increment <= CURRENT_INCREMENT for increment in REQUIRED_CANARIES.values())

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

        **Widened by the increment-5 review, and the reason is the same one
        that widened it the first time.** The check was ``"pytest.raises" in
        source`` — a syntactic form, not the property. Some guards cannot be
        proven to fail in the process running the assertion: R49's two hooks
        fail a run by setting the **session's exit status**, so the only
        faithful proof is a subprocess whose return code is non-zero. AC14
        names both of those as required canaries and neither had one, partly
        because the directory's own gate rejected the only shape in which
        they can be written. A guard whose form requirement excludes the
        guards it most needs is, once again, this project's signature defect
        wearing a suite-integrity badge.

        So either proof is accepted, and an inert canary is still refused.
        """
        canaries = sorted((TESTS_DIR / "canaries").glob("test_canary_*.py"))
        assert canaries, "the canary directory is empty"
        for path in canaries:
            source = path.read_text(encoding="utf-8")
            # Two ways to observe a guard failing: it raises here, or it fails
            # a session over there. Nothing else counts.
            raises = "pytest.raises" in source
            exits_non_zero = "returncode != 0" in source
            assert raises or exits_non_zero, (
                f"{path.relative_to(REPO)} asserts no failure; a canary that cannot "
                "observe its guard failing is a canary in name only (R50). Assert "
                "`pytest.raises(...)`, or a subprocess `returncode != 0` for a guard "
                "whose failure is a session exit status."
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


class TestMutationLedgerR49:
    """R49: ``tests/mutations.json`` is a checked-in guard, so it gets checked.

    Added by the increment-3 review. The increment-2 review's amendment 2 put
    the mutation set in the repository so the next sweep is a **re-run** rather
    than a re-invention. A re-run is only possible while every anchor still
    matches the source, and nothing asserted that: three anchors went stale
    under the review's own fix commits (`M10`, `M18`, `W-J15`) and the only
    signal was a `NOT-APPLIED` line in a sweep somebody happened to run.

    A mutant whose anchor has drifted is a mutant that reports nothing — a
    check that cannot fail, in the artefact this project adopted *because* of
    checks that cannot fail. This is the cheapest possible step toward
    amendment 4 (run the sweep in CI): it does not run the mutations, it asserts
    they could be run.
    """

    LEDGER = REPO / "tests" / "mutations.json"

    def ledger(self) -> dict[str, object]:
        return json.loads(self.LEDGER.read_text(encoding="utf-8"))

    def live_mutations(self) -> list[dict[str, str]]:
        entries = self.ledger()["mutations"]
        assert isinstance(entries, list)
        return [item for item in entries if not item.get("retired")]

    # There is deliberately **no test here comparing an anchor to its module's
    # source text**, and the reason is worth more than the test would have been.
    #
    # The first version of this class had one: every live `old` string must occur
    # exactly once in its module. It looked like the obvious guard, it caught the
    # three anchors that had gone stale under the review's fix commits, and it
    # was verified to fail. It was also a **"kill everything" oracle**. A sweep
    # runs the suite with a mutation *applied*; the applied anchor is then absent;
    # this test failed; and the very next full run reported **289 of 289 mutants
    # killed, including the declared control arm**. Every verdict in that run was
    # this test failing, not the check each mutant was aimed at.
    #
    # The control arm is the only reason that was visible, which is precisely
    # what the tester asked the reviewer to weigh, answered by demonstration.
    #
    # Tolerating the mutated form is not enough either, and that is the deeper
    # point: **44 anchors in this ledger are shared by two or more entries** (a
    # line with three plausible mutations gets three entries), and 18 more are
    # nested inside another entry's anchor. Applying any one of them makes its
    # siblings match neither form. A ledger of this shape and a source-text
    # assertion inside the oracle are structurally incompatible.
    #
    # So the anchor check belongs in the **harness**, where a drifted anchor is
    # reported as NOT-APPLIED rather than silently skipped — which is what the
    # tester's harness already did and what surfaced the three stale anchors. The
    # tests below read only the ledger, so they are safe under a sweep. See the
    # increment-3 review, C7, and R53's harness clauses.

    def test_r49_every_mutation_actually_changes_its_module(self) -> None:
        """R49: an anchor whose replacement equals it is a mutant that mutates nothing."""
        for item in self.live_mutations():
            assert item["old"] != item["new"], item["id"]

    def test_r49_every_surviving_mutant_carries_its_reason(self) -> None:
        """R49: a survivor is either equivalent-with-evidence or open-with-a-threat.

        Amendment 2's shape. A survivor with no recorded reason is a number in a
        report rather than a ledger entry, which is the thing the increment-2
        adjudication ruled against.
        """
        for item in self.ledger()["mutations"]:  # type: ignore[union-attr]
            if item["verdict"] in {"SURVIVED", "retired"}:
                assert item.get("why_it_survives"), item["id"]

    def test_r49_the_ledger_declares_a_control_arm_that_must_survive(self) -> None:
        """R49/R50: the sweep's own canary — a no-op mutant whose survival is required.

        A sweep with no control arm cannot distinguish "the tests killed these"
        from "the harness reports failure regardless". Asserted as a property of
        the ledger so a future wave cannot quietly drop it.
        """
        # ``control-no-op`` exactly, not a substring match: ``control-flow`` is a
        # real operator whose mutants must be *killed*, and a loose match here
        # would demand they survive. The distinction is the whole point of the
        # arm, so it is spelled rather than pattern-matched.
        controls = [item for item in self.live_mutations() if item["operator"] == "control-no-op"]
        assert controls, "the ledger declares no control arm"
        for item in controls:
            assert item["verdict"] == "SURVIVED", (
                f"{item['id']} is a declared no-op and a killed verdict means the harness "
                "is not reporting verdicts that come from the mutation"
            )

    def test_r49_the_ledger_covers_every_module_the_increment_touched(self) -> None:
        """R49: amendment 1's per-module floor, as a check rather than a table.

        Every module under ``swarm_observer/`` that this increment's branch
        created carries mutants. Asserted against the ledger's own module set so
        a new module arriving in increment 4 with no mutants is visible.
        """
        modules = {item["module"] for item in self.live_mutations()}
        for required in (
            "swarm_observer/cost/compute.py",
            "swarm_observer/cost/snapshot.py",
            "swarm_observer/cost/source.py",
            "swarm_observer/report/json_out.py",
            "swarm_observer/report/redact.py",
            "swarm_observer/cli/main.py",
            "swarm_observer/detect/base.py",
            "swarm_observer/detect/registry.py",
            # Increment 4 (tester). The coder's write-up named this list as a
            # gap it would not close unilaterally, because "what a sweep must
            # cover" is a decision rather than a transcription. These four are
            # the modules increment 4 created, and every one of them is on the
            # path a trace byte takes to a rendered document.
            "swarm_observer/report/escape.py",
            "swarm_observer/report/sanitize.py",
            "swarm_observer/report/timeline.py",
            "swarm_observer/report/html.py",
            # Increment 5 (tester). The coder's PR §9.5 named these six as a
            # gap it would not close unilaterally, for the same reason
            # increment 4's coder gave: "what a sweep must cover" is a
            # decision, not a transcription. Every module this increment
            # created is here. `narrate/narrator.py` is the one the coder
            # asked for most loudly -- the fatal-code set, the `stopped`
            # short-circuit, the validation order and the
            # `except AssertionError: raise` clause are each one character
            # from a wrong answer.
            "swarm_observer/narrate/client.py",
            "swarm_observer/narrate/summary.py",
            "swarm_observer/narrate/narrator.py",
            "swarm_observer/narrate/fixture.py",
            "swarm_observer/report/narrative.py",
            "swarm_observer/replay/target.py",
        ):
            assert required in modules, required
            mine = [item for item in self.live_mutations() if item["module"] == required]
            assert len(mine) >= 8, f"{required} has only {len(mine)} mutants"


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


def _is_truthy_literal(node: ast.expr) -> bool:
    """True when ``node`` is a literal that is always truthy.

    ``assert True``, ``assert 1``, ``assert "x"`` and ``assert [0]`` are all
    assertions that cannot fail. ``assert 0`` and ``assert ()`` can, so they are
    not flagged — a deliberately failing literal is a different mistake and one
    a run finds immediately.
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return bool(node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    return False


def tautological_assertions(source: str) -> list[tuple[int, str]]:
    """Every ``assert`` in ``source`` that no input can make fail (R49).

    Two shapes, both observed in this repository:

    * ``assert <truthy literal>`` — the direct form;
    * ``assert X or True`` — the form **BUG-11** took, where a red assertion
      was made green by disjoining a truthy literal onto it. ``or`` is the only
      connective that does this; ``and`` narrows rather than widens, so it is
      not flagged.

    Factored out of the test below so the check itself can be driven, which is
    the rule this module exists to enforce: a guard whose logic only ever runs
    inside a test is a guard nobody has watched fail.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if _is_truthy_literal(test):
            found.append((node.lineno, "assert <truthy literal>"))
        elif (
            isinstance(test, ast.BoolOp)
            and isinstance(test.op, ast.Or)
            and any(_is_truthy_literal(value) for value in test.values)
        ):
            found.append((node.lineno, "assert ... or <truthy literal>"))
    return found


class TestNoAssertionIsStructurallyUnableToFailR49:
    """R49: this project's signature defect, as a check rather than as a habit.

    ``tests/test_reader_and_limits.py`` carried
    ``assert digest_id("a|b") != digest_id("a", "b") or True`` for three
    increments. It was green every run, it documented nothing, and the property
    it named was *false* — the ``or True`` is what a red test was made green
    with. It is the ninth recorded instance of "a check reporting green while
    structurally unable to fail", and it was sitting inside the suite whose
    stated purpose is to catch that class.

    The increment-4 tester found it by grepping for ``or True`` after writing
    one, removed it, and left the question of whether the grep belongs in CI to
    this review. **It does.** A one-off grep is exactly the mitigation that
    produced instances two through nine: a class of defect found by hand, fixed
    once, and left unguarded. It costs an AST walk over the test tree, it names
    the file and line, and — unlike a grep — it cannot be confused by the string
    ``or True`` inside a docstring, a comment or test data, which this module's
    own prose contains.

    What it does **not** catch is the subtler members of the family: a fixture
    with no case that could trip the check, a sweep over an empty array, a
    ledger that can be widened. Those need a non-vacuous arm per check and no
    scanner can supply one. This closes the one shape a scanner *can* see.
    """

    def test_r49_no_test_module_contains_an_assertion_that_cannot_fail(self) -> None:
        """R49: the scan, over every module under ``tests/``.

        Red when: an assertion is disjoined with a truthy literal, or asserts
        one outright. Both are ways of writing down a property without checking
        it, which is worse than not writing it down — a reader believes it.
        """
        offenders: list[str] = []
        for path in sorted(TESTS_DIR.rglob("*.py")):
            for line, shape in tautological_assertions(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(TESTS_DIR.parent).as_posix()}:{line}: {shape}")
        assert not offenders, (
            "assertion(s) that no input can make fail:\n  "
            + "\n  ".join(offenders)
            + "\nAssert the property in the direction it holds, or delete it."
        )

    def test_r49_the_scan_has_modules_and_assertions_to_scan(self) -> None:
        """R49: the premise — "no offenders" must not mean "nothing was read".

        A scanner that walked zero files, or files with zero assertions, would
        report clean forever. This is the arm that the check above is about
        something.
        """
        modules = sorted(TESTS_DIR.rglob("*.py"))
        assert len(modules) > 30, len(modules)
        total = sum(
            isinstance(node, ast.Assert)
            for path in modules
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
        assert total > 1_000, total

    def test_r49_the_scan_catches_bug11s_exact_shape(self) -> None:
        """R49: driven against the line that was actually shipped.

        The inherited text, verbatim, plus the direct forms. Red when: the
        predicate is narrowed — which is what would happen the first time
        somebody wants an assertion the scan refuses.
        """
        bug11 = 'assert digest_id("a|b") != digest_id("a", "b") or True  # documents the joiner\n'
        assert tautological_assertions(bug11) == [(1, "assert ... or <truthy literal>")]
        assert tautological_assertions("assert True\n") == [(1, "assert <truthy literal>")]
        assert tautological_assertions("assert 1\n") == [(1, "assert <truthy literal>")]
        assert tautological_assertions('assert x or "yes"\n') == [
            (1, "assert ... or <truthy literal>")
        ]
        assert tautological_assertions("assert x or [1]\n") == [
            (1, "assert ... or <truthy literal>")
        ]

    def test_r49_the_scan_does_not_flag_an_assertion_that_can_fail(self) -> None:
        """R49: the other half — a scanner that flagged everything is no scanner.

        ``and``-ing a truthy literal narrows nothing away, ``or 0`` cannot make
        a false assertion true, and a message argument is not the test. Each of
        these is a false positive that would get the scan deleted.
        """
        for source in (
            "assert x == y\n",
            "assert x and True\n",
            "assert x or 0\n",
            "assert x or []\n",
            'assert x, "or True"\n',
            "assert x or y\n",
            "assert not x\n",
        ):
            assert tautological_assertions(source) == [], source
