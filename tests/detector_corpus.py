"""The fixture corpus and the checks that run over it. Not a test module.

``tests/fixtures/traces/`` holds one hand-authored JSONL trace per case (T6) and,
beside each, a checked-in ``<name>.expected.json`` naming **every** registered
detector and whether it must fire — with the exact findings when it does. Two
different guarantees are built on that pairing and both live here so a canary can
drive their failure branches directly:

* **R48's both arms.** Every detector must have at least one fixture that fires
  it and at least one that does not. (a) alone lets a detector that fires on
  everything pass; (b) alone lets a dead detector pass.
* **The corpus contract.** Every fixture's expectation must name exactly the
  registry's slugs — no more, no fewer — and the findings it names must be what
  the detectors actually produce, down to severity, metrics, evidence spans and
  attributed waste.

The second is what makes the first mean something. R48 as written is satisfied by
an expectation file that says "fires: true" and nothing else, and the reviewer's
mutation pass on increment 1 showed exactly what that is worth: flipping one
character of R9's collapse condition left the whole suite green because no
checked-in input could tell the two behaviours apart. So the corpus pins each
boundary constant from **both** sides — 2 versus 1 occurrence, 3 versus 2
repeats, 8 versus 7 population, 6*MAD versus 6*MAD+1, 60 s versus 59 s, 50%
versus 25% coverage — and pins the numbers each side produces.

Every check is a function returning a list of problem strings rather than a bare
assertion, for the same reason ``conftest.collection_floor_problems`` is: a
guard whose failure branch only ever runs inside a passing test is a guard
nobody has watched fail.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from swarm_observer.detect.base import DetectorConfig, Finding
from swarm_observer.detect.registry import ALL_DETECTORS, DETECTOR_SLUGS
from swarm_observer.ingest.claude_code.mapper import ClaudeCodeSource
from swarm_observer.ingest.source import IngestLimits
from swarm_observer.model.trace import Trace

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
FIXTURE_DIR = TESTS_DIR / "fixtures" / "traces"
DETECT_PACKAGE = REPO / "swarm_observer" / "detect"

#: The suffix of a fixture's checked-in expectation file.
EXPECTATION_SUFFIX = ".expected.json"

#: The keys an expectation document must carry.
EXPECTATION_KEYS = frozenset({"fixture", "purpose", "trace", "detectors"})

#: The keys one expected finding must carry.
FINDING_KEYS = frozenset(
    {
        "severity",
        "finding_id",
        "metrics",
        "span_seqs",
        "agent_ids",
        "wasted",
        "previews_count",
    }
)


# --- the corpus ---------------------------------------------------------------


def fixture_paths() -> tuple[Path, ...]:
    """Every checked-in trace fixture, sorted by name."""
    return tuple(sorted(FIXTURE_DIR.glob("*.jsonl")))


def fixture_names() -> tuple[str, ...]:
    """Every fixture's stem, sorted — the parametrization ids."""
    return tuple(path.stem for path in fixture_paths())


def expectation_paths() -> tuple[Path, ...]:
    """Every checked-in expectation file, sorted by name."""
    return tuple(sorted(FIXTURE_DIR.glob(f"*{EXPECTATION_SUFFIX}")))


def expectation_path(fixture: Path) -> Path:
    """The expectation file that must sit beside ``fixture``."""
    return fixture.with_name(fixture.stem + EXPECTATION_SUFFIX)


def load_expectation(fixture: Path) -> dict[str, Any]:
    """One fixture's checked-in expectation document."""
    document: dict[str, Any] = json.loads(expectation_path(fixture).read_text(encoding="utf-8"))
    return document


def load_expectations() -> dict[str, dict[str, Any]]:
    """Every expectation document, keyed by fixture stem."""
    return {path.stem: load_expectation(path) for path in fixture_paths()}


def load_trace(fixture: Path) -> Trace:
    """Ingest one fixture through the real adapter (R3).

    Deliberately the default configuration — previews on, sidecars on — so the
    corpus is checked through the same path an ``analyze`` run takes rather than
    through a test-only shortcut.
    """
    return ClaudeCodeSource().load([fixture], IngestLimits())


def findings_by_detector(
    trace: Trace, config: DetectorConfig | None = None
) -> dict[str, tuple[Finding, ...]]:
    """Each registered detector's findings for ``trace``, keyed by slug."""
    resolved = DetectorConfig() if config is None else config
    return {detector.slug: detector.run(trace, resolved) for detector in ALL_DETECTORS}


def finding_document(finding: Finding) -> dict[str, Any]:
    """One finding in the shape an expectation file records it."""
    return {
        "severity": finding.severity,
        "finding_id": finding.finding_id,
        "metrics": dict(finding.metrics),
        "span_seqs": list(finding.span_seqs),
        "agent_ids": list(finding.agent_ids),
        "wasted": {
            "input_tokens": finding.wasted.input_tokens,
            "output_tokens": finding.wasted.output_tokens,
            "cache_read_input_tokens": finding.wasted.cache_read_input_tokens,
            "cache_creation_5m_tokens": finding.wasted.cache_creation_5m_tokens,
            "cache_creation_1h_tokens": finding.wasted.cache_creation_1h_tokens,
        },
        "previews_count": len(finding.previews),
    }


def observed_document(fixture: Path) -> dict[str, Any]:
    """The expectation document a fixture *would* produce right now."""
    trace = load_trace(fixture)
    detectors: dict[str, Any] = {}
    for slug, found in findings_by_detector(trace).items():
        if found:
            detectors[slug] = {
                "fires": True,
                "findings": [finding_document(item) for item in found],
            }
        else:
            detectors[slug] = {"fires": False}
    return {
        "fixture": fixture.name,
        "trace": {
            "trace_id": trace.trace_id,
            "spans": len(trace.spans),
            "agents": len(trace.agents),
            "warnings": {
                (f"{warning.code}:{warning.detail}" if warning.detail else warning.code): (
                    warning.count
                )
                for warning in trace.warnings
            },
        },
        "detectors": detectors,
    }


# --- the corpus contract ------------------------------------------------------


def expectation_problems(
    name: str,
    expectation: Mapping[str, Any],
    observed: Mapping[str, Any],
    slugs: Sequence[str] = DETECTOR_SLUGS,
) -> list[str]:
    """Everything wrong with one fixture's expectation document.

    Checked in this order: the document's own shape, then that it names exactly
    the registry's detectors, then the trace summary, then each detector's arm
    and — when it fires — the findings themselves.
    """
    problems: list[str] = []
    missing_keys = EXPECTATION_KEYS - set(expectation)
    if missing_keys:
        problems.append(f"{name}: expectation is missing {sorted(missing_keys)}")
        return problems
    if expectation["fixture"] != observed["fixture"]:
        problems.append(
            f"{name}: expectation names fixture {expectation['fixture']!r}, "
            f"file is {observed['fixture']!r}"
        )
    if not str(expectation["purpose"]).strip():
        problems.append(f"{name}: expectation has an empty purpose")

    declared = expectation["detectors"]
    if not isinstance(declared, dict):
        problems.append(f"{name}: 'detectors' must be an object")
        return problems
    expected_slugs = set(slugs)
    if set(declared) != expected_slugs:
        undeclared = sorted(expected_slugs - set(declared))
        unknown = sorted(set(declared) - expected_slugs)
        if undeclared:
            problems.append(
                f"{name}: expectation does not say whether {undeclared} fire. Every "
                "fixture must name every registered detector, or a detector can be "
                "dropped from the corpus without anything failing (R48, R50)."
            )
        if unknown:
            problems.append(f"{name}: expectation names detectors that do not exist: {unknown}")

    for key, value in sorted(expectation["trace"].items()):
        if observed["trace"].get(key) != value:
            problems.append(
                f"{name}: trace.{key} is {observed['trace'].get(key)!r}, expectation says {value!r}"
            )

    for slug in sorted(expected_slugs & set(declared)):
        problems.extend(_detector_problems(name, slug, declared[slug], observed["detectors"][slug]))
    return problems


def _detector_problems(
    name: str, slug: str, declared: Mapping[str, Any], observed: Mapping[str, Any]
) -> list[str]:
    """One detector's arm and, when it fires, its findings."""
    problems: list[str] = []
    if "fires" not in declared:
        return [f"{name}/{slug}: expectation has no 'fires' key"]
    if bool(declared["fires"]) != bool(observed["fires"]):
        verb = "fires" if observed["fires"] else "does not fire"
        problems.append(
            f"{name}/{slug}: expectation says fires={declared['fires']}, but the detector "
            f"{verb} on this fixture"
        )
        return problems
    if not observed["fires"]:
        if "findings" in declared:
            problems.append(f"{name}/{slug}: fires=false but the expectation lists findings")
        return problems

    expected_findings = declared.get("findings")
    if not isinstance(expected_findings, list) or not expected_findings:
        return [
            f"{name}/{slug}: fires=true but the expectation lists no findings. "
            "'fires: true' with nothing behind it cannot fail when a threshold moves."
        ]
    actual = observed["findings"]
    if len(expected_findings) != len(actual):
        problems.append(
            f"{name}/{slug}: expected {len(expected_findings)} findings, got {len(actual)}"
        )
        return problems
    for index, (expected, got) in enumerate(zip(expected_findings, actual, strict=True)):
        missing = FINDING_KEYS - set(expected)
        if missing:
            problems.append(
                f"{name}/{slug}[{index}]: expected finding is missing {sorted(missing)}"
            )
            continue
        for key in sorted(FINDING_KEYS):
            if expected[key] != got[key]:
                problems.append(
                    f"{name}/{slug}[{index}]: {key} is {got[key]!r}, expectation says "
                    f"{expected[key]!r}"
                )
    return problems


def assert_corpus_contract(
    names: Iterable[str] | None = None, slugs: Sequence[str] = DETECTOR_SLUGS
) -> None:
    """Raise :class:`AssertionError` if any fixture disagrees with its expectation."""
    wanted = set(names) if names is not None else None
    problems: list[str] = []
    for fixture in fixture_paths():
        if wanted is not None and fixture.stem not in wanted:
            continue
        problems.extend(
            expectation_problems(
                fixture.stem, load_expectation(fixture), observed_document(fixture), slugs
            )
        )
    if problems:
        raise AssertionError("fixture corpus contract (T6):\n  " + "\n  ".join(problems))


# --- R48: both arms -----------------------------------------------------------


def coverage_arms(
    expectations: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(fires, silent)`` — for each detector slug, the fixtures on each arm."""
    fires: dict[str, list[str]] = {}
    silent: dict[str, list[str]] = {}
    for name, expectation in sorted(expectations.items()):
        for slug, declared in sorted(expectation.get("detectors", {}).items()):
            bucket = fires if declared.get("fires") else silent
            bucket.setdefault(slug, []).append(name)
    return fires, silent


def coverage_problems(
    expectations: Mapping[str, Mapping[str, Any]], slugs: Sequence[str] = DETECTOR_SLUGS
) -> list[str]:
    """R48: every detector needs a fixture that fires it and one that does not."""
    fires, silent = coverage_arms(expectations)
    problems: list[str] = []
    for slug in slugs:
        if not fires.get(slug):
            problems.append(
                f"{slug}: no fixture in tests/fixtures/traces/ produces a finding from it "
                "(R48a). A detector with no positive fixture may be dead code."
            )
        if not silent.get(slug):
            problems.append(
                f"{slug}: no fixture produces zero findings from it (R48b). A detector "
                "that fires on every fixture is indistinguishable from one that fires on "
                "everything."
            )
    return problems


def assert_detector_coverage(
    expectations: Mapping[str, Mapping[str, Any]], slugs: Sequence[str] = DETECTOR_SLUGS
) -> None:
    """Raise :class:`AssertionError` when either R48 arm is empty for any detector."""
    problems = coverage_problems(expectations, slugs)
    if problems:
        raise AssertionError("detector coverage (R48):\n  " + "\n  ".join(problems))


# --- R48: the registry is the only place a detector can exist -----------------


def detector_classes_in(directory: Path) -> dict[str, str]:
    """AST-scan ``directory`` for detector-shaped classes → ``slug: module path``.

    "Detector-shaped" is R13's protocol read structurally: a class that assigns a
    string ``slug`` and defines ``run``. The scan is over the AST rather than
    over imported objects on purpose — a module that is never imported has no
    objects to inspect, and a detector nobody imports is exactly the thing this
    check exists to catch.
    """
    found: dict[str, str] = {}
    for path in sorted(directory.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            slug: str | None = None
            has_run = False
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "slug"
                            and isinstance(statement.value, ast.Constant)
                            and isinstance(statement.value.value, str)
                        ):
                            slug = statement.value.value
                if isinstance(statement, ast.FunctionDef) and statement.name == "run":
                    has_run = True
            if slug is not None and has_run:
                found[slug] = path.name
    return found


def registry_problems(
    directory: Path = DETECT_PACKAGE, slugs: Sequence[str] = DETECTOR_SLUGS
) -> list[str]:
    """R48: every detector-shaped class in ``directory`` is in ``ALL_DETECTORS``."""
    scanned = detector_classes_in(directory)
    problems: list[str] = []
    for slug, module in sorted(scanned.items()):
        if slug not in set(slugs):
            problems.append(
                f"{module} defines a detector with slug {slug!r} that is not in "
                "ALL_DETECTORS. A detector outside the registry is outside R48's "
                "coverage guarantee and outside every report."
            )
    for slug in slugs:
        if slug not in scanned:
            problems.append(
                f"ALL_DETECTORS names {slug!r}, but no class under {directory.name}/ "
                "declares it. The registry and the modules have drifted apart."
            )
    return problems


def assert_registry_complete(
    directory: Path = DETECT_PACKAGE, slugs: Sequence[str] = DETECTOR_SLUGS
) -> None:
    """Raise :class:`AssertionError` when a detector exists outside the registry."""
    problems = registry_problems(directory, slugs)
    if problems:
        raise AssertionError("detector registry (R48):\n  " + "\n  ".join(problems))


__all__ = [
    "DETECT_PACKAGE",
    "EXPECTATION_KEYS",
    "EXPECTATION_SUFFIX",
    "FINDING_KEYS",
    "FIXTURE_DIR",
    "assert_corpus_contract",
    "assert_detector_coverage",
    "assert_registry_complete",
    "coverage_arms",
    "coverage_problems",
    "detector_classes_in",
    "expectation_path",
    "expectation_paths",
    "expectation_problems",
    "finding_document",
    "findings_by_detector",
    "fixture_names",
    "fixture_paths",
    "load_expectation",
    "load_expectations",
    "load_trace",
    "observed_document",
    "registry_problems",
]
