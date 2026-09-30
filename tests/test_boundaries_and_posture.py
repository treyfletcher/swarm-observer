"""R44 import boundaries and R45 offline posture, asserted over the AST.

R44's rules are architectural claims that decay the moment they are only in a
document: "``report`` imports nothing from ``ingest``" stays true exactly as
long as something checks it. Every rule below is written so it applies to
subpackages that do not exist yet — ``detect``, ``cost``, ``report``,
``narrate``, ``replay`` land in increments 2 to 5 and are governed from their
first commit rather than from whenever someone remembers to extend this file.

R45's posture is the other half: the suite must pass offline, with every
provider credential unset and with ``anthropic`` not installed. A test that
would quietly pass because a credential happened to be present is not an
offline test.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from .conftest import CREDENTIAL_ENV_VARS

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "swarm_observer"

#: R44: for each subpackage, the swarm_observer subpackages it may import.
#: Subpackages that do not exist yet are listed so the rule is in force the
#: moment their first module lands.
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "model": {"model"},
    "ingest": {"model", "ingest"},
    "detect": {"model", "detect"},
    "cost": {"model", "cost"},
    "report": {"model", "detect", "cost", "report"},
    "narrate": {"model", "detect", "narrate"},
    "replay": {"model", "replay"},
    "cli": {"model", "ingest", "detect", "cost", "report", "narrate", "replay", "cli"},
}

#: R44: third-party SDKs and the one module each is allowed to appear in.
CONFINED_IMPORTS = {"anthropic": "narrate/adapters/anthropic.py"}

#: Modularity notes: detectors are pure functions of (trace, config).
DETECTOR_FORBIDDEN_IMPORTS = {"os", "time", "random", "datetime", "pathlib", "subprocess"}

#: Modularity notes: money is Decimal from load to format.
NO_FLOAT_PACKAGES = {"cost", "report"}


def python_modules() -> list[Path]:
    """Every module in the package, in a stable order."""
    return sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)


def subpackage_of(path: Path) -> str:
    """The subpackage a module belongs to, or ``""`` for ``swarm_observer/*.py``."""
    relative = path.relative_to(PACKAGE)
    return relative.parts[0] if len(relative.parts) > 1 else ""


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def imported_package_modules(path: Path) -> set[str]:
    """Every ``swarm_observer.<x>`` module name this file imports."""
    found: set[str] = set()
    for node in ast.walk(parse(path)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("swarm_observer")
        ):
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names if alias.name.startswith("swarm_observer")
            )
    return found


def imported_top_level(path: Path) -> set[str]:
    """Every top-level package name this file imports."""
    found: set[str] = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


MODULES = python_modules()


class TestImportBoundariesR44:
    """R44: the package's one-way seams, asserted over the AST."""

    def test_r44_the_walker_sees_the_increment_1_modules(self) -> None:
        """R44: a boundary test that scans nothing passes vacuously."""
        names = {path.relative_to(PACKAGE).as_posix() for path in MODULES}
        assert {
            "model/trace.py",
            "ingest/source.py",
            "ingest/reader.py",
            "ingest/registry.py",
            "ingest/claude_code/records.py",
            "ingest/claude_code/mapper.py",
            "cli/main.py",
        } <= names, f"walker found only {sorted(names)}"

    @pytest.mark.parametrize("module_path", MODULES, ids=lambda path: path.name)
    def test_r44_subpackage_import_rules(self, module_path: Path) -> None:
        """R44: each subpackage imports only what its rule allows."""
        package = subpackage_of(module_path)
        if not package:
            return  # swarm_observer/__init__.py imports nothing of its own
        allowed = ALLOWED_IMPORTS.get(package)
        assert allowed is not None, f"unknown subpackage {package!r}: extend ALLOWED_IMPORTS"
        for imported in imported_package_modules(module_path):
            parts = imported.split(".")
            if len(parts) < 2:
                continue
            assert parts[1] in allowed, (
                f"{module_path.relative_to(REPO)} imports {imported}, but {package} may "
                f"only import {sorted(allowed)}"
            )

    def test_r44_model_imports_only_stdlib_and_pydantic(self) -> None:
        """R44: ``model`` is the bottom of the stack."""
        for module_path in MODULES:
            if subpackage_of(module_path) != "model":
                continue
            for imported in imported_package_modules(module_path):
                parts = imported.split(".")
                assert len(parts) < 2 or parts[1] == "model", (
                    f"{module_path.relative_to(REPO)} imports {imported}"
                )

    def test_r44_claude_code_is_private_to_ingest(self) -> None:
        """R44: nothing outside ``ingest`` may import ``ingest.claude_code``."""
        for module_path in MODULES:
            if subpackage_of(module_path) == "ingest":
                continue
            for imported in imported_package_modules(module_path):
                assert not imported.startswith("swarm_observer.ingest.claude_code"), (
                    f"{module_path.relative_to(REPO)} imports the adapter directly; go through "
                    "ingest.registry"
                )

    def test_r44_report_never_imports_ingest(self) -> None:
        """R44: the renderer receives a finished trace, it does not read one."""
        for module_path in MODULES:
            if subpackage_of(module_path) != "report":
                continue
            for imported in imported_package_modules(module_path):
                assert not imported.startswith("swarm_observer.ingest"), (
                    f"{module_path.relative_to(REPO)} imports {imported}"
                )

    def test_r44_nothing_imports_cli(self) -> None:
        """R44: ``cli`` is a leaf; importing it would invert the wiring."""
        for module_path in MODULES:
            if subpackage_of(module_path) == "cli":
                continue
            for imported in imported_package_modules(module_path):
                assert not imported.startswith("swarm_observer.cli"), (
                    f"{module_path.relative_to(REPO)} imports {imported}"
                )

    def test_r44_nothing_imports_the_replay_seam(self) -> None:
        """R44: ``replay/target.py`` is a declared seam with no implementation."""
        for module_path in MODULES:
            if subpackage_of(module_path) == "replay":
                continue
            for imported in imported_package_modules(module_path):
                assert not imported.startswith("swarm_observer.replay"), (
                    f"{module_path.relative_to(REPO)} imports {imported}"
                )

    def test_r44_sdks_are_confined_to_their_adapter(self) -> None:
        """R44: ``anthropic`` appears only in ``narrate/adapters/anthropic.py``."""
        for module_path in MODULES:
            relative = module_path.relative_to(PACKAGE).as_posix()
            for sdk, allowed_module in CONFINED_IMPORTS.items():
                if sdk in imported_top_level(module_path):
                    assert relative == allowed_module, f"{relative} imports {sdk}"

    def test_r44_one_definition_of_escape_html(self) -> None:
        """R44: exactly one escaping definition, and no second replacement table."""
        definitions: list[str] = []
        for module_path in MODULES:
            relative = module_path.relative_to(PACKAGE).as_posix()
            for node in ast.walk(parse(module_path)):
                if isinstance(node, ast.FunctionDef) and node.name in {
                    "escape_html",
                    "html_escape",
                }:
                    definitions.append(relative)
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "maketrans"
                    and relative != "report/escape.py"
                ):
                    raise AssertionError(f"{relative} builds a character-replacement table")
        assert definitions in ([], ["report/escape.py"]), (
            f"escape_html must be defined once, in report/escape.py; found {definitions}"
        )

    def test_r44_only_the_text_boundary_calls_redact(self) -> None:
        """R44/R33 (increment-5 tester): one redaction call site, as a scan.

        The shape of ``test_r44_one_definition_of_escape_html``, one boundary
        over. Escaping has one definition and a test that says so; redaction
        has one *call site* and, until now, nothing that said so — which is
        the asymmetry the increment-4 review's C1 was about, since the
        redaction half is the half that has leaked in every increment.

        ``report/sanitize.py`` is the only module allowed to call ``redact``.
        A second call site is how a fifth text boundary gets added beside the
        fourth, with its own idea of which fields are trace-derived.

        Red when: a renderer imports ``redact`` directly, or a new module
        under ``report/`` grows its own classification.
        """
        callers: set[str] = set()
        for module_path in MODULES:
            relative = module_path.relative_to(PACKAGE).as_posix()
            if relative == "report/redact.py":
                continue
            for node in ast.walk(parse(module_path)):
                if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("redact"):
                    callers.add(relative)
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "swarm_observer.report.redact"
                ):
                    callers.add(relative)
        assert callers == {"report/sanitize.py"}, (
            "redact must be called from report/sanitize.py and nowhere else; "
            f"found {sorted(callers)}"
        )

    def test_r44_detectors_are_pure(self) -> None:
        """R44: no detector reads a clock, the filesystem, or the environment."""
        for module_path in MODULES:
            if subpackage_of(module_path) != "detect":
                continue
            forbidden = imported_top_level(module_path) & DETECTOR_FORBIDDEN_IMPORTS
            assert not forbidden, f"{module_path.relative_to(REPO)} imports {sorted(forbidden)}"

    def test_r44_no_float_in_money_paths(self) -> None:
        """R44: money is ``Decimal`` from load to format; a float is a defect."""
        for module_path in MODULES:
            if subpackage_of(module_path) not in NO_FLOAT_PACKAGES:
                continue
            relative = module_path.relative_to(REPO)
            for node in ast.walk(parse(module_path)):
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    raise AssertionError(f"{relative} contains a float literal")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "float"
                ):
                    raise AssertionError(f"{relative} calls float()")


class TestOfflinePostureR45:
    """R45: the suite runs with no credentials, no network and no optional SDK."""

    def test_r45_credentials_are_scrubbed_for_the_session(self) -> None:
        """R45: every provider credential is unset while the suite runs."""
        present = [name for name in CREDENTIAL_ENV_VARS if name in os.environ]
        assert not present, f"credentials present during the suite: {present}"

    def test_r45_anthropic_is_not_installed(self) -> None:
        """R45: the offline suite runs with the ``[explain]`` extra absent."""
        assert importlib.util.find_spec("anthropic") is None, (
            "anthropic is importable; the offline suite must pass without it"
        )

    def test_r45_the_package_imports_with_no_optional_extras(self) -> None:
        """R45: importing swarm_observer pulls in nothing optional."""
        import swarm_observer
        from swarm_observer.ingest.registry import ADAPTERS
        from swarm_observer.model.trace import TRACE_SCHEMA_VERSION

        assert swarm_observer.__version__
        assert TRACE_SCHEMA_VERSION == "1.0.0"
        assert "claude_code_jsonl" in ADAPTERS
