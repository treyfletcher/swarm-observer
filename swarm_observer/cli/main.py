"""``swarm-observer`` — argparse wiring, exit codes and stream discipline.

This module is the only place adapters are selected (R3), the only place a
typed error becomes a process outcome (R11, R39), and the only place the
pipeline is assembled: **ingest → detect → cost → render**. Everything it calls
is a pure function of its inputs, so the wiring is the only part that touches
the filesystem.

Three rules it exists to keep:

* **Exit codes are a taxonomy, not a habit** (R39). ``0`` ran and nothing met
  ``--fail-on``; ``1`` ran and something did, with both reports still written;
  ``2`` fail-closed — one sanitized line on stderr, no traceback, no output file
  created or truncated; ``3`` usage — a bad flag, an unknown detector slug, an
  unwritable output directory. argparse's own default for a usage error is 2,
  which would collide with the fail-closed code, so the parser below raises
  :class:`UsageError` instead of exiting.
* **stdout is a deterministic function of the trace, the flags and the output
  paths** (R40). One line, no progress, no colour, no wall-clock duration, no
  count of anything the clock decides.
* **Outputs are written atomically or not at all** (R11). The document is
  rendered in full, in memory, before anything is staged; a failure anywhere
  leaves a pre-existing report untouched rather than half-overwritten.

Increment 5 wires ``--explain``, and with it the last of R38's surface;
``_DEFERRED_FLAGS`` and the loop that walked it are **deleted** rather than
left as an empty dict, because a loop that can no longer run is a check that
can no longer fail, and ``tests/mutations.json``'s ``W-M08`` — a mutant on that
loop's ``sorted(...)`` — is retired in the same commit with the reason beside
it.

Two rules the narrator adds to the three above:

* **``narrate/`` is imported inside the ``--explain`` branch, never at module
  scope.** R43 says that with the flag off "``narrate/`` is never imported by
  the analyze path", which is the strongest available form of R46's no-egress
  promise: the default path cannot open a socket because the module that could
  is never loaded. Type annotations that need those types use
  :data:`typing.TYPE_CHECKING`.
* **``--explain`` cannot change the exit code** (R39, R43).
  :func:`~swarm_observer.narrate.narrator.narrate` raises nothing, the
  narrative is assembled before either document is rendered, and
  :func:`exit_code_for` is computed from the findings exactly as it is without
  the flag.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TextIO

from pydantic import ValidationError

from swarm_observer import __version__
from swarm_observer.cost.compute import CostError, CostReport, compute_costs, format_usd
from swarm_observer.cost.snapshot import SnapshotRateSource
from swarm_observer.cost.source import RateSnapshotError
from swarm_observer.detect.base import SEVERITY_RANK, DetectorConfig, Finding
from swarm_observer.detect.registry import (
    ALL_DETECTORS,
    DETECTOR_SLUGS,
    run_detectors_with_waste,
)
from swarm_observer.ingest.reader import atomic_write_texts
from swarm_observer.ingest.registry import DEFAULT_ADAPTER, adapter_slugs, build_adapter
from swarm_observer.ingest.source import IngestLimits, TraceError
from swarm_observer.model.trace import Trace, schema_document
from swarm_observer.report.html import render_html
from swarm_observer.report.json_out import render_json, severity_counts
from swarm_observer.report.narrative import Narrative, NarrativeParagraph
from swarm_observer.report.sanitize import RenderOptions

if TYPE_CHECKING:  # pragma: no cover - typing only; R43 forbids the runtime import
    from swarm_observer.narrate.client import NarratorClient

#: R39: the pinned exit codes.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_FAIL_CLOSED = 2
EXIT_USAGE = 3

PROGRAM = "swarm-observer"

#: R11: the code :func:`main`'s catch-all renders when an exception reaches it
#: that no typed clause claimed. It names the exception's type and never its
#: message, because a message can quote the input.
_UNEXPECTED_CODE = "unexpected_error"

#: R38: the ``--fail-on`` thresholds. ``none`` never fails, and is the default,
#: because a tool that exits non-zero by default is a tool people wrap in
#: ``|| true``.
FAIL_ON_CHOICES: tuple[str, ...] = ("none", "warning", "critical")

#: The extension a directory argument expands to (R38).
JSONL_SUFFIX = ".jsonl"


class UsageError(Exception):
    """A mistake in the command rather than in the trace (R39's exit 3).

    argparse exits 2 on a usage error, which is R11's fail-closed code. Sharing
    one number between "your trace is malformed" and "you typed the flag wrong"
    makes both unusable in a script, so the parser raises this instead and
    :func:`main` renders it.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _one_line_safe(text: str) -> str:
    """Neutralize ASCII control characters in a message bound for a terminal.

    Deliberately **not** ``str.isprintable()``: that answers from the Unicode
    table compiled into the running interpreter (14.0 on CPython 3.11, 15.0 on
    3.12), and a diagnostic that differs by interpreter version is the exact
    drift the increment-1 addendum found in preview normalization. C0 and DEL
    are the characters that actually reprogram a terminal, and their code points
    are fixed forever.
    """
    return "".join(
        " " if (ord(char) < 0x20 and char != "\n") or ord(char) == 0x7F else char for char in text
    )


class _Parser(argparse.ArgumentParser):
    """An :class:`argparse.ArgumentParser` whose usage errors are exit 3 (R39)."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(_one_line_safe(f"{self.format_usage()}{self.prog}: error: {message}"))


def build_parser() -> argparse.ArgumentParser:
    """The argument parser — R38's three subcommands and no others."""
    parser = _Parser(
        prog=PROGRAM,
        description="Post-hoc observability for multi-agent AI runs.",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    analyze = subparsers.add_parser(
        "analyze",
        help="ingest traces, run the detectors, price the model calls, write the reports",
        description=(
            "Read one or more Claude Code JSONL transcripts (or a directory of them), "
            "run every detector, price every model call from the bundled rate snapshot, "
            "and write a machine-readable JSON report."
        ),
        parents=[],
    )
    analyze.add_argument(
        "paths",
        nargs="+",
        metavar="<path>",
        help="trace files, or directories whose *.jsonl children are read (non-recursively)",
    )
    analyze.add_argument(
        "--out",
        metavar="<report.html>",
        help="write the self-contained HTML report here",
    )
    analyze.add_argument(
        "--json",
        dest="json_path",
        metavar="<report.json>",
        help="write the machine-readable JSON report here",
    )
    analyze.add_argument(
        "--adapter",
        default=DEFAULT_ADAPTER,
        choices=adapter_slugs(),
        help=f"trace format to read (default: {DEFAULT_ADAPTER})",
    )
    analyze.add_argument(
        "--explain",
        action="store_true",
        help="add a narrative section written by an LLM from the findings' counts",
    )
    analyze.add_argument(
        "--no-previews",
        action="store_true",
        help="omit trace free text entirely; previews are dropped at ingest, not at render",
    )
    analyze.add_argument(
        "--detector",
        action="append",
        default=[],
        metavar="<slug>",
        help="restrict the run to this detector; repeatable",
    )
    analyze.add_argument(
        "--blocked-gap-seconds",
        type=int,
        metavar="N",
        help="the gap a blocked_agent finding needs, in seconds (default: 60)",
    )
    analyze.add_argument(
        "--fail-on",
        default="none",
        choices=FAIL_ON_CHOICES,
        help="exit 1 when a finding at or above this severity exists (default: none)",
    )
    analyze.add_argument("--max-file-bytes", type=int, metavar="N", help="per-file size cap")
    analyze.add_argument("--max-line-bytes", type=int, metavar="N", help="per-line size cap")
    analyze.add_argument("--max-records", type=int, metavar="N", help="total record cap")

    subparsers.add_parser(
        "schema",
        help="print the normalized trace model's JSON Schema and TRACE_SCHEMA_VERSION",
        description=(
            "Print the normalized trace model's JSON Schema and TRACE_SCHEMA_VERSION "
            "as one deterministic JSON document."
        ),
    )
    subparsers.add_parser(
        "detectors",
        help="print every detector's slug, default severity and description",
        description="Print each registered detector in registry order.",
    )
    return parser


def detectors_document() -> str:
    """The exact bytes ``swarm-observer detectors`` prints (R38, R40, R47).

    Registry order, which is R18 through R24 — the order the spec introduces
    them and the order a report groups by. Column widths are computed from the
    registry itself, so the output is a pure function of the package and has no
    hand-maintained padding to fall out of date.
    """
    slug_width = max(len(detector.slug) for detector in ALL_DETECTORS)
    severity_width = max(len(detector.default_severity) for detector in ALL_DETECTORS)
    lines = [
        f"{detector.slug:<{slug_width}}  {detector.default_severity:<{severity_width}}  "
        f"{detector.title}"
        for detector in ALL_DETECTORS
    ]
    return "\n".join(lines) + "\n"


def expand_inputs(raw: Sequence[str]) -> tuple[Path, ...]:
    """R38: a directory becomes its ``*.jsonl`` children, sorted by basename.

    Non-recursive, and sorted rather than in :meth:`~pathlib.Path.iterdir`
    order, which is whatever the filesystem returns — R47 forbids an output that
    depends on it, and ``seq`` (and therefore every finding id) depends on the
    file order.

    Anything a directory yields that is not a readable regular file is passed
    through to the reader rather than filtered out here, so a subdirectory
    called ``agent-3.jsonl`` is a fail-closed exit 2 and not a file silently
    missing from a report.

    No guard here for a path the OS cannot represent. ``Path.is_dir()`` answers
    ``False`` for one rather than raising (it swallows ``ValueError``
    internally), so BUG-7's NUL reaches
    :func:`~swarm_observer.ingest.reader.resolve_inputs`, which owns "this input
    cannot be read" and answers with R11's sanitized exit 2. A ``try`` around
    this call was written during the review's own BUG-7 fix and then **deleted**
    when the review's mutation wave found it: the whole suite passed with the
    clause removed, because nothing could reach it. A guard structurally unable
    to fire is the defect this project keeps shipping, and adding one while
    fixing an instance of it would have been the joke writing itself.
    ``test_r38_a_path_the_os_cannot_represent_is_not_a_directory`` pins the
    interpreter behaviour this relies on, so a Python that starts raising here
    goes red rather than silently reinstating the crash.
    """
    expanded: list[Path] = []
    for item in raw:
        path = Path(item)
        if path.is_dir():
            expanded.extend(sorted(path.glob(f"*{JSONL_SUFFIX}"), key=lambda child: child.name))
            continue
        expanded.append(path)
    return tuple(expanded)


def check_output_path(raw: str) -> Path:
    """R39: an output whose directory does not exist or cannot be written is exit 3.

    Checked *before* anything is read, so a mistyped ``--json`` costs a usage
    error rather than a full ingest followed by a write failure.
    """
    if "\x00" in raw:
        # `Path(raw).parent.is_dir()` calls `os.stat`, which raises a bare
        # `ValueError("embedded null byte")` from outside every `except` in
        # `main` (review, BUG-7). The docstring above already covers it — an
        # output that "cannot be written" is exit 3 — so the condition is
        # answered here, in the typed vocabulary, rather than as a traceback.
        raise UsageError(f"{PROGRAM}: error: output path contains a NUL byte")
    path = Path(raw)
    directory = path.parent if str(path.parent) else Path()
    if not directory.is_dir():
        raise UsageError(
            f"{PROGRAM}: error: output directory does not exist: {_one_line_safe(str(directory))}"
        )
    if path.is_dir():
        raise UsageError(f"{PROGRAM}: error: output path is a directory: {_one_line_safe(raw)}")
    if path.exists() and not path.is_file():
        # Not just a directory (review, BUG-5). `atomic_write_texts` finishes
        # with `os.replace`, which happily replaces a FIFO, a socket or a device
        # node with a regular file — `--json /dev/null`, the natural way to ask
        # a CI job for the exit code alone, destroyed `/dev/null` for the whole
        # machine and printed `wrote /dev/null`. R11's posture is that the tool
        # leaves the filesystem as it found it, and the reader already refuses a
        # non-regular *input* (`not_a_regular_file`); the writer now matches.
        # `exists()` follows symlinks, so a symlink to a FIFO is refused too and
        # a symlink to a regular file is still accepted.
        raise UsageError(
            f"{PROGRAM}: error: output path is not a regular file: {_one_line_safe(raw)}"
        )
    return path


def build_limits(args: argparse.Namespace) -> IngestLimits:
    """R11's caps, with the three R38 exposes overridden when given."""
    overrides = {
        name: getattr(args, name)
        for name in ("max_file_bytes", "max_line_bytes", "max_records")
        if getattr(args, name) is not None
    }
    try:
        return IngestLimits(**overrides)
    except ValidationError:
        raise UsageError(f"{PROGRAM}: error: a size limit must be a positive integer") from None


def build_config(args: argparse.Namespace) -> DetectorConfig:
    """R25's config from the flags, with an unknown detector slug as exit 3 (R39)."""
    selected: list[str] = list(args.detector)
    unknown = sorted({slug for slug in selected if slug not in DETECTOR_SLUGS})
    if unknown:
        raise UsageError(
            f"{PROGRAM}: error: unknown detector: {_one_line_safe(unknown[0])} "
            f"(choose from {', '.join(DETECTOR_SLUGS)})"
        )
    overrides = {}
    if args.blocked_gap_seconds is not None:
        overrides["blocked_gap_seconds"] = args.blocked_gap_seconds
    try:
        return DetectorConfig(
            enabled=frozenset(selected) if selected else None,
            **overrides,
        )
    except ValidationError:
        raise UsageError(
            f"{PROGRAM}: error: --blocked-gap-seconds must be a non-negative integer"
        ) from None


def selected_slugs(config: DetectorConfig) -> tuple[str, ...]:
    """The slugs this run will use, in registry order — for the report header."""
    if config.enabled is None:
        return DETECTOR_SLUGS
    return tuple(slug for slug in DETECTOR_SLUGS if slug in config.enabled)


def exit_code_for(findings: Sequence[Finding], fail_on: str) -> int:
    """R39: ``1`` when a finding meets the threshold, ``0`` otherwise.

    ``none`` is not a severity and has no rank, so it is handled as its own
    case rather than as a rank below ``info`` — a threshold that "never fires"
    should read as never firing, not as firing below the lowest severity.
    """
    if fail_on == "none":
        return EXIT_OK
    threshold = SEVERITY_RANK[fail_on]
    if any(SEVERITY_RANK[finding.severity] >= threshold for finding in findings):
        return EXIT_FINDINGS
    return EXIT_OK


def summary_line(written: Sequence[Path], findings: Sequence[Finding]) -> str:
    """R40: the one line ``analyze`` writes to stdout.

    The paths are named **as the command gave them**, not resolved: a resolved
    path carries the working directory and the username into stdout, which R47
    forbids, and two people running the same command from different machines
    must see the same bytes.

    They are also **sanitized** (review, BUG-6). Every ``UsageError`` and every
    fail-closed line on stderr goes through :func:`_one_line_safe`; this line —
    the one a CI job parses and a human reads — was the single output in the
    package that carried a raw path through. An ESC in a filename reprograms the
    terminal, and a newline made R40's pinned "one line" into two, the second of
    which can be made to read as a summary line of its own reporting a run that
    wrote nothing. ``_one_line_safe`` deliberately preserves ``\\n`` (its callers
    render multi-line argparse usage), so the newline is removed here, where the
    requirement is one line and not merely a safe one.
    """
    counts = severity_counts(findings)
    paths = ", ".join(_one_line_safe(path.as_posix()).replace("\n", " ") for path in written)
    tallies = " ".join(f"{name}={counts[name]}" for name in ("critical", "warning", "info"))
    return f"wrote {paths}; findings: {tallies}\n"


def build_narrative(
    *,
    trace: Trace,
    findings: Sequence[Finding],
    cost: CostReport,
    client: NarratorClient | None = None,
) -> Narrative:
    """Run the ``--explain`` pass and hand the renderers what they render (R42, R43).

    Every import of ``narrate`` in this package is inside this function. R43
    requires that with ``--explain`` off "``narrate/`` is never imported by the
    analyze path", and a module-scope import would make that false for every
    run of the tool — which would also quietly weaken R46, whose no-socket
    promise is strongest when the code that could open one is never loaded.

    The cost figures are extracted **here** rather than inside
    ``narrate/summary.py``, because R44 forbids ``narrate`` from importing
    ``cost`` while R42 requires per-agent and per-model totals in the payload.
    This module is the one place allowed to see both sides. The extraction is
    deliberately dumb: four integers and a money string per row, all of them
    already computed by the cost engine, and not one trace-derived string —
    ``AgentCost.agent_id`` and ``SpanCost.model`` are both left behind, which
    is what keeps AC12's sentinel property a fact about types rather than
    about this function's care. See **S34**.

    Raises nothing. A narrator that cannot be built, cannot be reached or
    answers badly produces a narrative of deterministic templates, and the
    exit code is whatever it would have been without the flag (R39).
    """
    from swarm_observer.narrate.adapters.anthropic import AnthropicNarratorClient
    from swarm_observer.narrate.client import AgentTotals, ModelTotals
    from swarm_observer.narrate.narrator import narrate
    from swarm_observer.narrate.summary import build_totals

    totals = build_totals(
        findings=findings,
        agents=len(trace.agents),
        spans=len(trace.spans),
        model_calls=sum(1 for span in trace.spans if span.kind == "model_call"),
        tokens=cost.total_usage.total,
        cost_usd=format_usd(cost.total_cost_usd),
        priced_spans=cost.priced_spans,
        unpriced_spans=cost.unpriced_spans,
        by_agent=[
            AgentTotals(
                agent_index=row.agent_index,
                priced_spans=row.priced_spans,
                unpriced_spans=row.unpriced_spans,
                tokens=row.usage.total,
                cost_usd=format_usd(row.cost_usd),
            )
            for row in cost.by_agent
        ],
        by_model=[
            ModelTotals(
                model_key=row.model_key,
                priced_spans=row.priced_spans,
                tokens=row.usage.total,
                cost_usd=format_usd(row.cost_usd),
            )
            for row in cost.by_model
        ],
    )
    narration = narrate(
        client=AnthropicNarratorClient() if client is None else client,
        findings=findings,
        totals=totals,
        rate_snapshot_version=cost.meta.version,
    )
    return Narrative(
        paragraphs=tuple(
            NarrativeParagraph(
                group=paragraph.group,
                title=paragraph.title,
                text=paragraph.text,
                fallback=paragraph.fallback,
                reason=paragraph.reason,
            )
            for paragraph in narration.paragraphs
        ),
        calls=narration.calls,
    )


def analyze(
    args: argparse.Namespace,
    out: TextIO,
    *,
    narrator: NarratorClient | None = None,
) -> int:
    """R38-R40: ingest → detect → cost → render, then one line and an exit code.

    R38 writes ``--out`` as required and ``--json`` as optional. What is
    enforced here is **at least one of the two**, which is S18's ruling: a CI
    job that gates on ``--fail-on`` and never opens a browser is a legitimate
    invocation and should not be made to write an artefact nobody reads. A run
    with neither flag is still a usage error, because a run that reports success
    without writing the file it was asked for is the failure A-c10 is about.
    """
    if args.out is None and args.json_path is None:
        raise UsageError(f"{PROGRAM}: error: one of --out or --json is required")

    # Both destinations are validated before anything is read, so a mistyped
    # path costs a usage error rather than a full ingest followed by a write
    # failure — and a run that can only write half of what it was asked for
    # writes neither.
    html_path = None if args.out is None else check_output_path(args.out)
    json_path = None if args.json_path is None else check_output_path(args.json_path)
    if html_path is not None and json_path is not None and html_path == json_path:
        raise UsageError(f"{PROGRAM}: error: --out and --json name the same path")
    limits = build_limits(args)
    config = build_config(args)

    adapter = build_adapter(args.adapter, no_previews=args.no_previews)
    trace = adapter.load(expand_inputs(args.paths), limits)

    detected = run_detectors_with_waste(trace, config)
    rates = SnapshotRateSource()
    cost = compute_costs(trace, rates, waste_seqs=detected.waste_seqs)
    # R14/R26: the finding carries its own priced waste. ``model_copy`` on a
    # frozen model is the sanctioned way to produce the priced twin, and the
    # value comes from the cost engine rather than being recomputed here — the
    # renderer and the CLI both read one number.
    findings = tuple(
        finding.model_copy(
            update={"wasted_cost_usd": cost.waste_by_finding.get(finding.finding_id)}
        )
        for finding in detected.findings
    )

    options = RenderOptions(
        previews=not args.no_previews,
        blocked_gap_seconds=config.blocked_gap_seconds,
        detectors=selected_slugs(config),
    )
    # R43: the narrative is produced *before* either document is rendered, so
    # both carry the same paragraphs and A-d11's "both documents are staged in
    # one call" is unaffected. `build_narrative` raises nothing, so the line
    # below cannot change the exit code or the fail-closed behaviour.
    narrative = (
        None
        if not args.explain
        else build_narrative(trace=trace, findings=findings, cost=cost, client=narrator)
    )
    # Both documents are rendered in full, in memory, before either is staged
    # (R11): a renderer that raised halfway through would otherwise leave one
    # report written and the other not, which is a partial result with a
    # successful exit code.
    documents: dict[Path, str] = {}
    if html_path is not None:
        documents[html_path] = render_html(
            trace=trace,
            findings=findings,
            cost=cost,
            tool_version=__version__,
            options=options,
            narrative=narrative,
        )
    if json_path is not None:
        documents[json_path] = render_json(
            trace=trace,
            findings=findings,
            cost=cost,
            tool_version=__version__,
            options=options,
            narrative=narrative,
        )
    atomic_write_texts(documents)
    # R40: the paths in the order R38 lists the flags, not in dict order — the
    # stdout line is a function of the flags and nothing else (R47).
    written = [path for path in (html_path, json_path) if path is not None]
    out.write(summary_line(written, findings))
    return exit_code_for(findings, args.fail_on)


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    narrator: NarratorClient | None = None,
) -> int:
    """Dispatch one invocation and return its exit code.

    Raises :class:`~swarm_observer.ingest.source.TraceError`,
    :class:`~swarm_observer.cost.source.RateSnapshotError`,
    :class:`~swarm_observer.cost.compute.CostError` and :class:`UsageError` for
    the conditions :func:`main` turns into R39's exit codes, so a library caller
    still sees the typed error rather than a number.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    out = sys.stdout if stdout is None else stdout
    if args.command == "schema":
        out.write(schema_document())
        return EXIT_OK
    if args.command == "detectors":
        out.write(detectors_document())
        return EXIT_OK
    if args.command == "analyze":
        return analyze(args, out, narrator=narrator)
    # argparse's `required=True` makes this unreachable for real invocations;
    # it exists so a future subcommand cannot fall through silently.
    parser.error(f"unknown command: {args.command!r}")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    narrator: NarratorClient | None = None,
) -> int:
    """Console-script entry point (R11, R39, R40).

    The only place a typed error becomes a process outcome. Every fail-closed
    condition renders as exactly one line on stderr and exits 2; every usage
    mistake renders argparse's own message and exits 3. No traceback ever
    reaches stderr, and any subcommand added later inherits both behaviours by
    construction rather than by remembering to wrap itself.
    """
    err = sys.stderr if stderr is None else stderr
    try:
        return run(argv, stdout=stdout, narrator=narrator)
    except (TraceError, RateSnapshotError, CostError) as exc:
        err.write(exc.cli_line + "\n")
        return EXIT_FAIL_CLOSED
    except UsageError as exc:
        err.write(exc.message.rstrip("\n") + "\n")
        return EXIT_USAGE
    except Exception as exc:
        # The floor under the three typed clauses above, added by review after
        # **two unrelated inputs** — a 10**60 token count and a NUL in an output
        # path — each put a raw exception past this function, printing a
        # traceback and exiting 1. R39 gives 1 the meaning "ran, and a finding
        # met the threshold", so a wrapper reading the exit code was told the
        # run succeeded. Both call sites are fixed; this clause is what makes
        # the *third* one an exit 2 instead of a third bug report.
        #
        # The line names the exception's **type** and nothing else. `str(exc)`
        # is not sanitized and, for a pydantic or json error, quotes the input
        # — which is precisely the byte of file content R11 forbids on stderr.
        # A catch-all that is never reached is a check that cannot fail, so
        # `_UNEXPECTED_CODE` is asserted from a test that forces it.
        err.write(f"{PROGRAM}: {_UNEXPECTED_CODE}: {_one_line_safe(type(exc).__name__)}\n")
        return EXIT_FAIL_CLOSED


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m`
    sys.exit(main())
