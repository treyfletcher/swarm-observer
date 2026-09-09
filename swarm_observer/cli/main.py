"""``swarm-observer`` — argparse wiring, exit codes and stream discipline.

Increment 1 implements the ``schema`` subcommand only (R38's schema portion,
R1). ``analyze`` and ``detectors`` arrive with the increments that build the
pipeline they drive; the exit-code constants and the fail-closed handler they
will use are defined here now because R11's guarantee — one sanitized line on
stderr, no traceback, exit 2 — is a property of this module, not of the
subcommand that happens to trip it.

Output discipline (R40): stdout carries the command's deterministic result and
nothing else. No progress output, no colour, no wall-clock duration.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from swarm_observer import __version__
from swarm_observer.model.trace import schema_document

#: R39: the pinned exit codes. ``EXIT_FINDINGS`` and the analyze path that can
#: return it arrive with increment 3; the constants live together so the
#: taxonomy is readable in one place.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_FAIL_CLOSED = 2
EXIT_USAGE = 3

PROGRAM = "swarm-observer"


def build_parser() -> argparse.ArgumentParser:
    """The argument parser (R38).

    Only ``schema`` is wired in increment 1. It takes no options: its output is
    a pure function of the package, which is what makes it the natural subject
    for the determinism harness.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Post-hoc observability for multi-agent AI runs.",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    subparsers.add_parser(
        "schema",
        help="print the normalized trace model's JSON Schema and TRACE_SCHEMA_VERSION",
        description=(
            "Print the normalized trace model's JSON Schema and TRACE_SCHEMA_VERSION "
            "as one deterministic JSON document."
        ),
    )
    return parser


def run(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    """Dispatch one invocation and return its exit code.

    Raises :class:`~swarm_observer.ingest.source.TraceError` for fail-closed
    input conditions; :func:`main` is what turns that into R11's single stderr
    line and exit 2, so a library caller can still see the typed error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    out = sys.stdout if stdout is None else stdout
    if args.command == "schema":
        out.write(schema_document())
        return EXIT_OK
    # argparse's `required=True` makes this unreachable for real invocations;
    # it exists so a future subcommand cannot fall through silently.
    parser.error(f"unknown command: {args.command!r}")


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point (R39, R40)."""
    return run(argv)


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m`
    sys.exit(main())
