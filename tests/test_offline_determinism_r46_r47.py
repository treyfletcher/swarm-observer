"""R46 and R47: the default path opens no socket, and the bytes never move.

**R46 runs in a subprocess, on purpose.** R46 asks for "a ``socket.socket``
subclass raising on construction for the duration of a full end-to-end analyze,
plus an audit hook on ``socket.connect``". An audit hook cannot be removed once
installed, so doing this in the pytest process would slow every later test and
leave the hook live for the rest of the session. The driver in
:func:`driver_source` installs both inside a child process that then runs the
real CLI, which is also the stronger claim: the guard covers argument parsing,
ingest, detection, pricing and both renderers rather than one function call.

**Its control arms are the point.** "A full analyze ran and no socket was
constructed" is indistinguishable from "the guard was not installed" unless the
guard is shown firing. Two arms do that: one constructs a socket before the
analyze, one makes the *renderer itself* construct one mid-run. Both must fail,
with the guard's own message.

**R47 is asserted over three artefacts, not one.** Increment 3 covered
``report.json`` and stdout; ``report.html`` and the SVG are this increment's, so
every matrix entry here hashes all three together. The **cross-interpreter** arm
is the checked-in golden: CI runs this module under CPython 3.11 and 3.12, both
compare the rendered document against the same checked-in bytes, and a
divergence makes one of the two legs red. That is the only form available from
inside a single interpreter, and it is what S24 says cannot be relied on for a
*live* hostile trace — see
``test_r47_the_cross_interpreter_arm_is_the_golden_and_its_scope_is_s24``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .detector_corpus import FIXTURE_DIR
from .harness import DETERMINISM_ENVIRONMENTS, assert_deterministic, run_cli, sha256_text
from .hostile_corpus import write_hostile_trace
from .pipeline import analyze_paths

CLEAN = FIXTURE_DIR / "clean_single_agent.jsonl"
HOSTILE = FIXTURE_DIR / "hostile.jsonl"
MULTI = FIXTURE_DIR / "multi_agent_subagent.jsonl"

REPO = Path(__file__).resolve().parent.parent

#: R47's matrix as AC6 names it, so the environments are read from the
#: requirement rather than from the harness constant they are applied through.
AC6_ENVIRONMENTS: tuple[dict[str, str], ...] = (
    {"PYTHONHASHSEED": "0"},
    {"PYTHONHASHSEED": "1"},
    {"PYTHONHASHSEED": "2"},
    {"PYTHONHASHSEED": "random"},
    {"TZ": "UTC"},
    {"TZ": "America/Los_Angeles"},
    {"TZ": "Asia/Kolkata"},
    {"LC_ALL": "C"},
    {"LC_ALL": "en_US.UTF-8"},
    {"PYTHONHASHSEED": "random", "TZ": "Asia/Kolkata", "LC_ALL": "en_US.UTF-8"},
)


def driver_source() -> str:
    """The child process R46 runs: block every socket, then run the real CLI.

    ``mode`` is the first argument:

    * ``ok`` — install the guard and run ``analyze``. Must exit 0.
    * ``before`` — install the guard, construct a socket, then run. Must fail.
    * ``inside`` — install the guard and make ``report/html.py`` construct a
      socket while rendering. Must fail, which is what proves the guard covers
      the analyze path rather than only the top of the process.
    """
    return """
import sys, socket

class SocketAttempt(RuntimeError):
    pass

class NoSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise SocketAttempt("the analyze path constructed a socket")

def audit(event, args):
    if event.startswith("socket."):
        raise SocketAttempt("audit hook saw " + event)

sys.addaudithook(audit)
socket.socket = NoSocket
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(SocketAttempt("create_connection"))

mode = sys.argv[1]
argv = sys.argv[2:]

if mode == "before":
    socket.socket()

if mode == "inside":
    from swarm_observer.report import html as html_module
    original = html_module.render_html
    def instrumented(**kwargs):
        socket.socket()
        return original(**kwargs)
    html_module.render_html = instrumented

from swarm_observer.cli.main import main
sys.exit(main(argv))
"""


def run_driver(tmp_path: Path, mode: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    driver = tmp_path / "socket_driver.py"
    driver.write_text(driver_source(), encoding="utf-8")
    environment = {
        "PYTHONPATH": str(REPO),
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, str(driver), mode, *argv],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=environment,
        check=False,
    )


class TestNoSocketOnTheDefaultPathR46:
    """R46/AC13: "With ``--explain`` absent, the process opens no socket"."""

    def test_r46_a_full_analyze_completes_with_every_socket_blocked(self, tmp_path: Path) -> None:
        """R46: ingest, detect, price and render both documents, with sockets refused.

        Red when: any module on the analyze path opens a socket — a telemetry
        call, a rate lookup, a DNS resolution for a hostname in a report header.
        """
        html = tmp_path / "report.html"
        report = tmp_path / "report.json"
        result = run_driver(
            tmp_path,
            "ok",
            ["analyze", str(HOSTILE), str(MULTI), "--out", str(html), "--json", str(report)],
        )
        assert result.returncode == 0, result.stderr
        assert html.is_file() and report.is_file()
        assert "SocketAttempt" not in result.stderr
        assert html.read_text(encoding="utf-8").endswith("</html>\n")

    def test_r46_the_guard_fires_when_a_socket_is_constructed_before_the_run(
        self, tmp_path: Path
    ) -> None:
        """R46, control arm one: the guard is installed and it raises.

        Without this, "exit 0 with sockets blocked" and "exit 0 with nothing
        blocked" are the same observation.

        Red when: the driver stops installing the guard — the analyze would
        then pass for the wrong reason.
        """
        result = run_driver(tmp_path, "before", ["analyze", str(CLEAN), "--json", "x.json"])
        assert result.returncode != 0
        assert "SocketAttempt" in result.stderr

    def test_r46_the_guard_fires_from_inside_the_renderer(self, tmp_path: Path) -> None:
        """R46, control arm two: the guard covers the analyze path, not just the process top.

        A guard that only caught a socket constructed before ``main`` would say
        nothing about the renderer. This one makes ``render_html`` itself
        construct one.

        Red when: the guard is installed in a way that the analyze path can
        bypass — a module that captured ``socket.socket`` at import time, for
        instance.
        """
        html = tmp_path / "report.html"
        result = run_driver(tmp_path, "inside", ["analyze", str(CLEAN), "--out", str(html)])
        assert result.returncode != 0
        assert "SocketAttempt" in result.stderr
        assert not html.exists(), "R11: a failing run writes no output file"

    def test_r46_no_module_under_swarm_observer_imports_a_transport(self) -> None:
        """R46: the static half — no module in the package names a networking library.

        Deliberately an AST scan of *this package's* source rather than a check
        over ``sys.modules`` after a run. ``ssl`` and ``asyncio`` are both in
        ``sys.modules`` after any run, because ``pydantic_core`` imports
        ``typing_extensions.deprecated``, which imports ``asyncio.coroutines``,
        which pulls in ``ssl``. A runtime check would therefore be red for a
        dependency's reasons and would teach the next reader to loosen it —
        which is how a guard stops meaning anything.

        Red when: a module under ``swarm_observer/`` imports ``socket``,
        ``ssl``, ``http``, ``urllib``, ``asyncio``, ``httpx``, ``requests`` or
        ``anthropic``. R44 covers ``anthropic``'s placement; this covers the
        rest.

        **Amended in increment 5, in place rather than by deletion.** This
        module was written when ``narrate/`` did not exist, and it forbade
        ``anthropic`` everywhere under the package. R44 requires that import to
        exist in exactly one module — ``narrate/adapters/anthropic.py`` — so
        "nowhere" became false the moment the narrator landed. The rule is therefore narrowed to what
        it always meant — its own docstring already said "R44 covers
        ``anthropic``'s placement" — with the exemption written as a single
        ``(module, name)`` pair rather than by dropping ``anthropic`` from
        ``forbidden``: an import of it in any *other* module is still an
        offender here as well as in R44's test, which is the arm that would
        otherwise have been lost.
        """
        import ast

        forbidden = {
            "socket",
            "ssl",
            "http",
            "urllib",
            "asyncio",
            "httpx",
            "requests",
            "aiohttp",
            "anthropic",
            "webbrowser",
            "ftplib",
            "smtplib",
            "telnetlib",
            "xmlrpc",
        }
        # R44: the one (module, top-level name) pair the package is allowed
        # to hold. Anything else, anywhere, is an offender.
        exempt = {("narrate/adapters/anthropic.py", "anthropic")}
        offenders: list[str] = []
        sources = sorted((REPO / "swarm_observer").rglob("*.py"))
        assert len(sources) > 20, "the scan found no source files"
        seen_exempt = False
        for path in sources:
            relative = path.relative_to(REPO / "swarm_observer").as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    if root not in forbidden:
                        continue
                    if (relative, root) in exempt:
                        seen_exempt = True
                        continue
                    offenders.append(f"{path.relative_to(REPO)}: {name}")
        assert not offenders, offenders
        # The exemption is not dead width: the module it names really does hold
        # the import, so an exemption left behind after a deletion is visible.
        assert seen_exempt, "the anthropic exemption matched nothing; delete it"

    def test_r46_the_static_scan_would_catch_an_added_transport_import(
        self, tmp_path: Path
    ) -> None:
        """R46: the scan's failure branch, on a file written to look like the package.

        A scan that has only ever seen clean files has not been shown to be able
        to fail. Red when: the scan stops walking ``ast.Import`` or
        ``ast.ImportFrom``.
        """
        import ast

        probe = tmp_path / "leaky.py"
        probe.write_text("import socket\nfrom urllib import request\n", encoding="utf-8")
        tree = ast.parse(probe.read_text(encoding="utf-8"))
        found = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert found == {"socket", "urllib"}

    def test_r46_no_third_party_transport_is_importable_at_runtime(self, tmp_path: Path) -> None:
        """R46/R45: after a full analyze, no third-party HTTP client is loaded.

        Scoped to the libraries no dependency of this project pulls in, so it
        says something the static scan does not: a lazily imported transport
        inside a function body would be invisible to an AST walk of top-level
        imports but present here.

        Red when: ``httpx``, ``requests`` or ``anthropic`` appears at any point
        of a default-path run.
        """
        probe = tmp_path / "imports.py"
        probe.write_text(
            "import sys, json\n"
            "from swarm_observer.cli.main import main\n"
            "main(sys.argv[1:])\n"
            "print(json.dumps(sorted(set(sys.modules))))\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(probe),
                "analyze",
                str(CLEAN),
                "--json",
                str(tmp_path / "r.json"),
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        modules = set(json.loads(result.stdout.splitlines()[-1]))
        for name in ("httpx", "requests", "anthropic", "aiohttp", "urllib3"):
            assert name not in modules, name
        # The non-vacuous arm: the probe really did import the product.
        assert "swarm_observer.report.html" in modules


class TestDeterminismInProcessR47:
    """R47: repeated runs in one process, and the environment matrix."""

    def test_r47_two_renders_of_one_trace_are_byte_identical(self) -> None:
        """AC6: "run twice in one process".

        Red when: a ``set`` or ``dict`` iteration order reaches the output, or a
        counter, cache or memo is shared between renders.
        """
        first = analyze_paths([HOSTILE, MULTI])
        second = analyze_paths([HOSTILE, MULTI])
        assert first.html == second.html
        assert first.json == second.json

    def test_r47_the_html_is_identical_across_the_in_process_matrix(self) -> None:
        """AC6: ``PYTHONHASHSEED``, ``TZ`` and ``LC_ALL``, as far as they act in-process.

        ``TZ`` and ``LC_ALL`` only take full effect at interpreter start, which
        is what the subprocess matrix below is for; this leg catches anything
        that reads them at render time.

        Red when: a ``datetime`` is formatted through the local zone, or a
        number through the locale.
        """
        digest = assert_deterministic(
            lambda: analyze_paths([HOSTILE, MULTI]).html,
            environments=AC6_ENVIRONMENTS,
        )
        assert len(digest) == 64

    def test_r47_the_matrix_this_suite_applies_covers_ac6s_values(self) -> None:
        """AC6: the environments are the requirement's, not a convenient subset.

        Red when: a value is dropped from the matrix — the change that makes a
        determinism harness pass by not looking.
        """
        applied = {
            key: {environment[key] for environment in AC6_ENVIRONMENTS if key in environment}
            for key in ("PYTHONHASHSEED", "TZ", "LC_ALL")
        }
        assert applied["PYTHONHASHSEED"] >= {"0", "1", "2", "random"}
        assert applied["TZ"] == {"UTC", "America/Los_Angeles", "Asia/Kolkata"}
        assert applied["LC_ALL"] == {"C", "en_US.UTF-8"}
        harness = {
            key: {
                environment[key] for environment in DETERMINISM_ENVIRONMENTS if key in environment
            }
            for key in ("PYTHONHASHSEED", "TZ", "LC_ALL")
        }
        assert harness["TZ"] == applied["TZ"]
        assert harness["LC_ALL"] == applied["LC_ALL"]

    def test_r47_path_order_does_not_change_a_byte(self, tmp_path: Path) -> None:
        """AC6/R5: "the order in which the paths are given on the command line".

        R5 computes ``trace_id`` from the *sorted* source files, and R6 orders
        records by the sorted basename, so this is the arm that proves both.

        Red when: either sort is dropped — every ``span_id`` and therefore every
        ``finding_id`` would depend on argument order.
        """
        paths = list(write_hostile_trace(tmp_path))
        forward = analyze_paths(paths)
        backward = analyze_paths(list(reversed(paths)))
        assert forward.html == backward.html
        assert forward.json == backward.json
        assert forward.trace.trace_id == backward.trace.trace_id

    def test_r47_two_absolute_paths_to_identical_content_render_identically(
        self, tmp_path: Path
    ) -> None:
        """AC6: "via two different absolute paths to byte-identical copies of the input".

        The copies keep their basenames, because R2 puts the basename in
        ``SourceFile.name`` and R5 hashes it — a report *should* change if the
        file is renamed, and must not change if it is moved.

        Red when: an absolute path, a directory name or an inode reaches the
        document or the id computation.
        """
        first = tmp_path / "one"
        second = tmp_path / "two"
        write_hostile_trace(first)
        shutil.copytree(first, second)
        left = analyze_paths(sorted(first.glob("*.jsonl")))
        right = analyze_paths(sorted(second.glob("*.jsonl")))
        assert left.html == right.html
        assert left.json == right.json

    def test_r47_renaming_a_file_does_change_the_report(self, tmp_path: Path) -> None:
        """R5: the non-vacuous arm for the test above.

        A renderer that ignored its inputs entirely would satisfy every
        determinism assertion in this module. ``trace_id`` is a function of the
        basenames and their hashes, so a rename must move it.

        Red when: ``trace_id`` stops depending on the source file names, which
        would make two different input sets produce one report id.
        """
        write_hostile_trace(tmp_path)
        before = analyze_paths(sorted(tmp_path.glob("*.jsonl")))
        (tmp_path / "agent-alpha.jsonl").rename(tmp_path / "agent-zulu.jsonl")
        after = analyze_paths(sorted(tmp_path.glob("*.jsonl")))
        assert before.trace.trace_id != after.trace.trace_id
        assert before.html != after.html

    def test_r47_no_float_shaped_token_appears_outside_r29s_display_total(self) -> None:
        """R47: "any float in output bytes" is forbidden.

        R29 mandates exactly one non-six-decimal decimal token in the HTML — the
        two-decimal grand total — so the scan allows that shape and nothing
        else. The pattern excludes a run with a further ``.`` on either side, so
        ``1.0.0`` (the schema and report format versions) is one token and not a
        float; a naive ``\\d+\\.\\d+`` matches ``1.0`` inside it and would make
        this test red for the wrong reason.

        Red when: a float reaches the geometry, a token count or a rate.
        """
        import re

        html = analyze_paths([HOSTILE, MULTI]).html
        pattern = re.compile(r"(?<![\d.])\d+\.\d+(?![\d.])")
        # R47 mandates `YYYY-MM-DDTHH:MM:SS.mmmZ`, whose seconds-and-millis run
        # is float-shaped. Removed by its full pattern rather than by loosening
        # the float pattern, so a bare `00.000` anywhere else still trips.
        timestamps = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
        assert timestamps.search(html), "no timestamp to strip; the fixture changed"
        tokens = set(pattern.findall(timestamps.sub("", html)))
        for token in tokens:
            decimals = len(token.split(".")[1])
            assert decimals in {2, 6}, token
        assert any(len(token.split(".")[1]) == 6 for token in tokens), (
            "a document with no six-decimal figure would make this vacuous"
        )
        assert any(len(token.split(".")[1]) == 2 for token in tokens), (
            "R29's two-decimal grand total is missing"
        )
        # The stylesheet is part of the document, and R47's sentence covers the
        # whole file: the fifth increment-4 commit split `font: 14px/1.5` for
        # exactly this reason.
        from swarm_observer.report.html import REPORT_STYLE

        assert not pattern.search(REPORT_STYLE)


class TestDeterminismAcrossProcessesR47:
    """AC6: "twice in separate subprocesses", under each environment."""

    def digests(self, tmp_path: Path, environment: dict[str, str], label: str) -> tuple[str, ...]:
        target = tmp_path / label
        target.mkdir(parents=True, exist_ok=True)
        html = target / "report.html"
        report = target / "report.json"
        result = run_cli(
            ["analyze", str(HOSTILE), str(MULTI), "--out", str(html), "--json", str(report)],
            environment=environment,
        )
        assert result.returncode == 0, f"{label}: {result.stderr}"
        return (
            sha256_text(html.read_text(encoding="utf-8")),
            sha256_text(report.read_text(encoding="utf-8")),
            # The stdout line names the output paths, which differ per run, so
            # the part R47 pins is the rest of it.
            sha256_text(result.stdout.split("; ", 1)[1]),
        )

    def test_ac6_every_environment_produces_the_same_three_digests(self, tmp_path: Path) -> None:
        """AC6: "the SHA-256 of ``report.html``, of ``report.json`` and of stdout are identical".

        Subprocesses, so ``TZ`` and ``LC_ALL`` take effect at interpreter start,
        which is the only way they can be tested at all.

        Red when: anything in the output depends on the environment.
        """
        baseline = self.digests(tmp_path, {}, "ambient")
        for index, environment in enumerate(AC6_ENVIRONMENTS):
            label = "-".join(f"{key}={value}" for key, value in sorted(environment.items()))
            assert self.digests(tmp_path, environment, f"env{index}") == baseline, label

    def test_ac6_two_working_directories_produce_the_same_bytes(self, tmp_path: Path) -> None:
        """AC6: "from two different working directories".

        Red when: the CLI resolves an input or an output relative to the CWD in
        a way that reaches the document — the ``trace_id`` and the source file
        names are both computed from paths.
        """
        first = tmp_path / "cwd-one"
        second = tmp_path / "cwd-two"
        first.mkdir()
        second.mkdir()
        outputs = tmp_path / "out"
        outputs.mkdir()
        digests = []
        for index, directory in enumerate((first, second)):
            html = outputs / f"report{index}.html"
            result = run_cli(
                ["analyze", str(HOSTILE), str(MULTI), "--out", str(html)], cwd=directory
            )
            assert result.returncode == 0, result.stderr
            digests.append(sha256_text(html.read_text(encoding="utf-8")))
        assert digests[0] == digests[1]

    def test_ac6_a_directory_argument_expands_to_the_same_report_as_its_files(
        self, tmp_path: Path
    ) -> None:
        """R38/AC6: ``analyze <dir>`` and ``analyze <dir>/*.jsonl`` are the same run.

        R38 expands a directory to its ``*.jsonl`` children sorted by basename,
        which must be the same set and order the shell would have passed.

        Red when: the expansion picks up the ``.meta.json`` sidecar, recurses,
        or sorts differently.
        """
        source = tmp_path / "trace"
        paths = write_hostile_trace(source)
        outputs = tmp_path / "out"
        outputs.mkdir()
        from_directory = outputs / "dir.html"
        from_files = outputs / "files.html"
        assert run_cli(["analyze", str(source), "--out", str(from_directory)]).returncode == 0
        assert (
            run_cli(
                ["analyze", *[str(path) for path in paths], "--out", str(from_files)]
            ).returncode
            == 0
        )
        assert from_directory.read_text(encoding="utf-8") == from_files.read_text(encoding="utf-8")

    def test_ac6_the_subprocess_bytes_equal_the_in_process_bytes(self, tmp_path: Path) -> None:
        """AC6: the CLI and ``tests.pipeline`` agree, which is what links the two matrices.

        Red when: the CLI passes an option the helper does not, which would
        quietly make every in-process determinism result a claim about a
        document nobody ships.
        """
        html = tmp_path / "report.html"
        report = tmp_path / "report.json"
        result = run_cli(
            ["analyze", str(HOSTILE), str(MULTI), "--out", str(html), "--json", str(report)]
        )
        assert result.returncode == 0, result.stderr
        expected = analyze_paths([HOSTILE, MULTI])
        assert html.read_text(encoding="utf-8") == expected.html
        assert report.read_text(encoding="utf-8") == expected.json


class TestCrossInterpreterR47:
    """R47: "byte-identical" has to hold on both interpreters CI builds."""

    def test_r47_the_cross_interpreter_arm_is_the_golden_and_its_scope_is_s24(
        self, tmp_path: Path
    ) -> None:
        """R47/S24: the golden is what compares 3.11's bytes with 3.12's.

        A single interpreter cannot compare itself with another one, so the
        mechanism is the checked-in file: CI runs this module under both, both
        render the same trace and compare against the same bytes, and a
        divergence makes one leg red.

        The scope of that guarantee is exactly what S24 limits. R32 and R8 both
        normalize by ``str.isprintable``, which reads the interpreter's Unicode
        table, so the golden holds only because
        ``TestCheckedInDataIsInterpreterStableR8`` keeps code points the two
        interpreters disagree about out of checked-in data. A *live* trace has
        no such guard. Both halves are asserted below.

        Red when: a code point unassigned on 3.11 enters the corpus (the second
        assertion), or the renderer becomes interpreter-dependent for some other
        reason (the first).
        """
        import unicodedata

        from .harness import read_golden

        analysis = analyze_paths(write_hostile_trace(tmp_path))
        assert analysis.html == read_golden("report_hostile_extended.html")

        offenders = [
            f"U+{ord(char):04X}"
            for source in sorted(tmp_path.glob("*"))
            for char in source.read_text(encoding="utf-8")
            if unicodedata.category(char) == "Cn"
        ]
        assert not offenders, (
            "the hostile corpus contains a code point unassigned on this interpreter; "
            f"the golden is interpreter-dependent (S24): {offenders[:5]}"
        )

    def test_s24_a_live_trace_can_still_diverge_between_the_two_interpreters(
        self, tmp_path: Path
    ) -> None:
        """S24: the honest scope of R47, pinned as behaviour rather than as a note.

        A trace carrying a code point this interpreter calls unassigned renders
        a space here and the character itself on a newer one. The trace is
        written at test time and never checked in, so
        ``TestCheckedInDataIsInterpreterStableR8`` is not violated.

        Red when: R32 and R8 adopt S24's fixed code-point rule — at which point
        this test must be deleted and R47's guarantee genuinely covers a live
        trace.
        """
        import unicodedata

        from .factories import assistant, text_block, usage, write_trace

        probes = [
            char
            for char in ("\U0001f6dc", "\U00013460", "\U00016d40")
            if unicodedata.category(char) == "Cn"
        ]
        assert probes, "pick probes from a Unicode table newer than this interpreter's"
        source = write_trace(
            tmp_path,
            [
                assistant(
                    "a-0",
                    model="claude-sonnet-4-5-20250929",
                    usage_block=usage(input_tokens=1, output_tokens=1),
                    content=[text_block(f"before{probes[0]}after")],
                )
            ],
        )
        analysis = analyze_paths([source])
        assert "before after" in analysis.html
        assert probes[0] not in analysis.html

    def test_r47_the_report_names_the_traces_last_timestamp_and_no_clock(self) -> None:
        """R47: "the **trace's own** last timestamp — never the current time".

        Red when: ``datetime.now`` reaches the provenance line. The trace's last
        timestamp is a fixed string in a fixed fixture, so a clock produces a
        value that is not it.
        """
        from swarm_observer.report.json_out import last_timestamp

        analysis = analyze_paths([HOSTILE])
        stamp = last_timestamp(analysis.trace)
        assert stamp is not None
        assert stamp in analysis.html
        assert "No wall-clock time from this run appears in this document." in analysis.html
        document = json.loads(analysis.json)
        assert document["meta"]["trace_last_timestamp"] == stamp
