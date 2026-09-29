"""Golden-file and determinism harnesses, plus the CLI subprocess runner.

These are the *subjects* of the R50 canaries: a golden comparison that cannot
detect a changed byte, or a determinism check that cannot detect a clock
reaching the output, is worse than no check at all, because it reports green
while structurally unable to fail. Each helper here is therefore written to be
breakable on purpose by a canary in ``tests/canaries/``.

Nothing in this module is a test. It is imported by tests and by canaries.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

#: R47's environment matrix, as far as it can be varied in-process. The
#: subprocess form (:func:`assert_cli_deterministic`) is what exercises
#: ``TZ``/``LC_ALL`` for real, since those only take effect at interpreter
#: start; both matrices are kept identical so a value cannot be checked one way
#: and forgotten the other.
DETERMINISM_ENVIRONMENTS: tuple[dict[str, str], ...] = (
    {"PYTHONHASHSEED": "0"},
    {"PYTHONHASHSEED": "1"},
    {"PYTHONHASHSEED": "random"},
    {"TZ": "UTC"},
    {"TZ": "America/Los_Angeles"},
    {"TZ": "Asia/Kolkata"},
    {"LC_ALL": "C"},
    {"LC_ALL": "en_US.UTF-8"},
)


def sha256_text(text: str) -> str:
    """The SHA-256 of ``text`` as UTF-8 — the unit every comparison here uses."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- golden files ------------------------------------------------------------


def golden_path(name: str) -> Path:
    """The path of the checked-in golden file called ``name``."""
    return GOLDEN_DIR / name


def read_golden(name: str) -> str:
    """The checked-in golden bytes, decoded as UTF-8."""
    return golden_path(name).read_text(encoding="utf-8")


def write_golden(name: str, text: str) -> None:
    """Rewrite a golden file. Only ever called deliberately, never by a test."""
    golden_path(name).parent.mkdir(parents=True, exist_ok=True)
    golden_path(name).write_text(text, encoding="utf-8")


def assert_matches_golden(name: str, actual: str, *, expected: str | None = None) -> None:
    """Compare ``actual`` against the golden file, byte for byte.

    ``expected`` overrides the on-disk content and exists for one reason: the
    R50 canary hands in a copy with a single byte flipped and asserts this
    function raises. A comparison that only ever sees matching inputs has never
    been shown to be able to fail.
    """
    golden = read_golden(name) if expected is None else expected
    if actual == golden:
        return
    detail = _first_difference(golden, actual)
    raise AssertionError(
        f"golden mismatch for {name}: "
        f"expected sha256 {sha256_text(golden)}, got {sha256_text(actual)}; {detail}"
    )


def _first_difference(expected: str, actual: str) -> str:
    """A short, deterministic description of where two strings first differ."""
    for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
        if left != right:
            return f"first difference at index {index}: {left!r} != {right!r}"
    return f"lengths differ: expected {len(expected)}, got {len(actual)}"


def flip_one_byte(text: str, index: int = 0) -> str:
    """Return ``text`` with one character changed — the canary's tampering."""
    if not text:
        raise ValueError("cannot flip a byte of empty text")
    position = index % len(text)
    original = text[position]
    replacement = "X" if original != "X" else "Y"
    return text[:position] + replacement + text[position + 1 :]


# --- determinism -------------------------------------------------------------


def assert_deterministic(
    produce: Callable[[], str],
    *,
    environments: Sequence[Mapping[str, str]] = DETERMINISM_ENVIRONMENTS,
) -> str:
    """Assert ``produce()`` returns identical bytes under every environment (R47).

    ``produce`` is called once per environment with that environment applied to
    ``os.environ`` (restored afterwards) and once more with the ambient one.
    Returns the single digest, so a caller can pin it.
    """
    digests: dict[str, list[str]] = {}
    baseline = sha256_text(produce())
    digests.setdefault(baseline, []).append("ambient")
    for environment in environments:
        label = ",".join(f"{key}={value}" for key, value in sorted(environment.items()))
        with _environment(environment):
            digest = sha256_text(produce())
        digests.setdefault(digest, []).append(label)
    if len(digests) != 1:
        summary = "; ".join(
            f"{digest[:12]}: {sorted(labels)}" for digest, labels in digests.items()
        )
        raise AssertionError(f"output is not deterministic across environments: {summary}")
    return baseline


class _environment:
    """Apply an environment mapping for the duration of a block, then restore it."""

    def __init__(self, overrides: Mapping[str, str]) -> None:
        self._overrides = dict(overrides)
        self._saved: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self._overrides.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *exc: object) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# --- the CLI as a subprocess -------------------------------------------------


def run_cli(
    args: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``swarm-observer`` in a subprocess via ``python -m``.

    Invoked as a module rather than through the console script so the suite does
    not depend on the package having been installed, and with the repository on
    ``PYTHONPATH`` so a checkout runs as-is.
    """
    child_environment = dict(os.environ)
    child_environment["PYTHONPATH"] = str(REPO)
    if environment:
        child_environment.update(environment)
    return subprocess.run(
        [sys.executable, "-m", "swarm_observer.cli.main", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else str(REPO),
        env=child_environment,
        check=False,
    )


def assert_cli_deterministic(
    args: Sequence[str],
    *,
    environments: Sequence[Mapping[str, str]] = DETERMINISM_ENVIRONMENTS,
) -> str:
    """Assert the CLI's stdout is byte-identical across the R47 matrix, in subprocesses."""
    digests: dict[str, list[str]] = {}
    for environment in (*environments, {}):
        label = (
            ",".join(f"{key}={value}" for key, value in sorted(environment.items())) or "ambient"
        )
        result = run_cli(args, environment=environment)
        if result.returncode != 0:
            raise AssertionError(
                f"swarm-observer {' '.join(args)} exited {result.returncode} under {label}"
            )
        digests.setdefault(sha256_text(result.stdout), []).append(label)
    if len(digests) != 1:
        summary = "; ".join(
            f"{digest[:12]}: {sorted(labels)}" for digest, labels in digests.items()
        )
        raise AssertionError(f"CLI output is not deterministic: {summary}")
    return next(iter(digests))


__all__ = [
    "DETERMINISM_ENVIRONMENTS",
    "GOLDEN_DIR",
    "REPO",
    "assert_cli_deterministic",
    "assert_deterministic",
    "assert_matches_golden",
    "flip_one_byte",
    "golden_path",
    "read_golden",
    "run_cli",
    "sha256_text",
    "write_golden",
]
