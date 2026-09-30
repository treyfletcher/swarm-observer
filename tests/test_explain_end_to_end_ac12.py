"""AC12 end to end, and the four things its byte-identity depends on.

AC12's first half is one scenario with five separate claims in it, so it is
driven once and asserted five times — but the byte-identity claim is the one
that will break, and "the section can be stripped" does not say *which* of its
four dependencies broke. The coder named them: ``NARRATIVE_SECTION_ID`` staying
out of ``SECTION_IDS``, ``RenderOptions`` gaining no ``explain`` field, no CSS
rule for the two classes, and no nav link. Each gets its own arm here, so a
future edit that adds a nav entry fails a test whose name is about nav entries.

The end of the module is the rest of what makes ``--explain`` safe to ship: the
exit code in all three directions, R34/R35 over the ``--explain`` document
(which no probe in the inherited suite has ever parsed), the allowlist not
widening on a default render, and the whole thing being deterministic.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from swarm_observer.cli.main import EXIT_FINDINGS, EXIT_OK, build_narrative, main, run
from swarm_observer.narrate.client import NarratorTransportError
from swarm_observer.narrate.fixture import FixtureNarratorClient, paragraph
from swarm_observer.narrate.summary import narration_groups
from swarm_observer.report.html import (
    CSS_CLASSES,
    REPORT_SCRIPT,
    REPORT_STYLE,
    SCRIPT_SHA256,
    SECTION_IDS,
    STYLE_SHA256,
)
from swarm_observer.report.narrative import (
    FALLBACK_CLASS,
    FALLBACK_PREFIX,
    NARRATIVE_CLASS,
    NARRATIVE_SECTION_ID,
    Narrative,
    NarrativeParagraph,
)
from swarm_observer.report.sanitize import RenderOptions

from .detector_corpus import FIXTURE_DIR
from .hostile_corpus import write_hostile_trace
from .pipeline import analyze_paths
from .rendered import parse, section_ids_in_order
from .sentinel_trace import totals_for, write_sentinel_trace

CLEAN = FIXTURE_DIR / "clean_single_agent.jsonl"

#: The section's opening line, as the renderer writes it. R43's prose says
#: ``<section id="narrative">``; the document says ``<section class="section"
#: id="narrative">`` because every section carries the class. The strip below
#: uses the real bytes — a test written to R43's literal text finds nothing to
#: remove.
SECTION_OPEN = f'<section class="section" id="{NARRATIVE_SECTION_ID}">'
SECTION_CLOSE = "</section>"


def strip_narrative_section(html: str) -> str:
    """Remove ``<section id="narrative">…</section>`` as a run of whole lines.

    Line-based on purpose. R43's byte-identity clause is only satisfiable if
    the section is a contiguous run of complete lines at a fixed position, and
    a regex over the whole document would hide a failure of exactly that: a
    section spliced into the middle of an existing line would still match.
    """
    lines = html.split("\n")
    assert lines.count(SECTION_OPEN) == 1, "the narrative section is not one whole line"
    start = lines.index(SECTION_OPEN)
    end = next(index for index in range(start + 1, len(lines)) if lines[index] == SECTION_CLOSE)
    return "\n".join(lines[:start] + lines[end + 1 :])


@pytest.fixture(scope="module")
def ac12(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """AC12's scenario, run once: four groups, two answers, one error, one 900.

    The hostile corpus produces exactly three firing detectors, so the run has
    exactly AC12's four groups without restricting the registry.
    """
    root = tmp_path_factory.mktemp("ac12")
    paths = write_hostile_trace(root / "trace")
    baseline_html = root / "plain.html"
    baseline_json = root / "plain.json"
    explain_html = root / "explain.html"
    explain_json = root / "explain.json"

    def invoke(argv: list[str], narrator: Any = None) -> tuple[int, str]:
        out = io.StringIO()
        code = run(argv, stdout=out, narrator=narrator)
        return code, out.getvalue()

    common = [*(str(path) for path in paths), "--fail-on", "critical"]
    plain_code, plain_out = invoke(
        ["analyze", *common, "--out", str(baseline_html), "--json", str(baseline_json)]
    )
    client = FixtureNarratorClient(
        [
            paragraph("The run repeated one tool call many times."),
            paragraph("Identical arguments were sent again and again."),
            NarratorTransportError("transport_failed"),
            paragraph("Z" * 900),
        ]
    )
    explain_code, explain_out = invoke(
        ["analyze", *common, "--out", str(explain_html), "--json", str(explain_json), "--explain"],
        narrator=client,
    )
    return {
        "client": client,
        "plain_code": plain_code,
        "explain_code": explain_code,
        "plain_stdout": plain_out,
        "explain_stdout": explain_out,
        "plain_html": baseline_html.read_text(encoding="utf-8"),
        "explain_html": explain_html.read_text(encoding="utf-8"),
        "plain_json": baseline_json.read_text(encoding="utf-8"),
        "explain_json": explain_json.read_text(encoding="utf-8"),
        "paths": paths,
    }


class TestAc12FirstHalf:
    """AC12: the scripted run, clause by clause."""

    def test_ac12_the_run_asks_exactly_four_groups_in_order(self, ac12: dict[str, Any]) -> None:
        """AC12/R43: "one paragraph per finding group plus one overall paragraph".

        The script has four entries and the run consumed all four, so a run
        that asked three times or five would have raised the fixture client's
        ``AssertionError`` rather than quietly measuring something else.
        """
        client: FixtureNarratorClient = ac12["client"]
        assert [request.group for request in client.calls] == [
            "overall",
            "repeated_tool_call",
            "failed_tool_call",
            "unresolved_tool_call",
        ]
        assert client.remaining == 0

    def test_ac12_the_exit_code_is_unchanged(self, ac12: dict[str, Any]) -> None:
        """AC12/R39: "the exit code is unchanged from the no-``--explain`` run".

        The run exits 1 both ways (``--fail-on critical`` over a trace with a
        critical finding), so this also covers the direction R39 states least
        loudly: ``--explain`` must not turn a 1 into anything either.
        """
        assert ac12["plain_code"] == ac12["explain_code"] == EXIT_FINDINGS

    def test_ac12_stdout_differs_only_in_the_paths_it_names(self, ac12: dict[str, Any]) -> None:
        """R40/AC12: the flag changes no counted thing on stdout."""
        plain = ac12["plain_stdout"].replace("plain", "X")
        explain = ac12["explain_stdout"].replace("explain", "X")
        assert plain == explain

    def test_ac12_the_two_valid_paragraphs_render(self, ac12: dict[str, Any]) -> None:
        """AC12: "the two valid paragraphs render"."""
        document = parse(ac12["explain_html"])
        assert "The run repeated one tool call many times." in document.text
        assert "Identical arguments were sent again and again." in document.text

    def test_ac12_the_other_two_fall_back_with_both_markers(self, ac12: dict[str, Any]) -> None:
        """AC12: "class ``narrative-fallback`` and the ``Deterministic summary:`` prefix"."""
        html = ac12["explain_html"]
        assert html.count(f'<p class="{FALLBACK_CLASS}">{FALLBACK_PREFIX} ') == 2
        assert html.count(f'<p class="{NARRATIVE_CLASS}">') == 2
        document = json.loads(ac12["explain_json"])["narrative"]
        assert document["fallbacks"] == 2
        assert document["calls"] == 4
        assert [p["fallback"] for p in document["paragraphs"]] == [False, False, True, True]
        assert [p["reason"] for p in document["paragraphs"]] == [
            None,
            None,
            "transport_failed",
            "response_too_long",
        ]

    def test_ac12_the_900_character_paragraph_is_nowhere_in_either_document(
        self, ac12: dict[str, Any]
    ) -> None:
        """AC12/R43: over 800 characters after normalization is rejected, not truncated.

        Red when: an over-long paragraph is trimmed instead of refused, which
        would put 800 characters of model output into a report while the
        fallback count still said 2.
        """
        assert "Z" * 900 not in ac12["explain_html"]
        assert "Z" * 900 not in ac12["explain_json"]
        assert "Z" * 100 not in ac12["explain_html"]

    def test_ac12_stripping_the_section_leaves_byte_identical_output(
        self, ac12: dict[str, Any]
    ) -> None:
        """AC12/R43: the headline clause, over the real CLI's bytes."""
        assert strip_narrative_section(ac12["explain_html"]) == ac12["plain_html"]

    def test_ac12_the_json_report_gains_exactly_one_key(self, ac12: dict[str, Any]) -> None:
        """R43 (A-e13): ``report.json`` is unchanged apart from ``narrative``."""
        plain = json.loads(ac12["plain_json"])
        explained = json.loads(ac12["explain_json"])
        assert "narrative" not in plain
        assert set(explained) - set(plain) == {"narrative"}
        assert {key: value for key, value in explained.items() if key != "narrative"} == plain


class TestByteIdentityDependencies:
    """R43: the four edits, each one of which would make AC12 unsatisfiable.

    Named individually because the strip test above cannot say which broke.
    """

    def test_r43_the_narrative_anchor_is_not_a_nav_section(self) -> None:
        """R43 (A-e10): ``SECTION_IDS`` drives the nav, so the anchor stays out of it.

        Red when: ``"narrative"`` is added to ``SECTION_IDS`` — which would put
        a nav link, a byte outside the section, into every ``--explain``
        render.
        """
        assert NARRATIVE_SECTION_ID not in SECTION_IDS

    def test_r43_render_options_records_no_explain_flag(self) -> None:
        """R43: both renderers write ``RenderOptions`` into their header.

        An ``explain`` field would put the flag's name into every document
        that never used it, or a byte outside the section into every one that
        did.

        Red when: a fourth option is added following the pattern of the other
        three.
        """
        assert set(RenderOptions.model_fields) == {"previews", "blocked_gap_seconds", "detectors"}

    def test_r43_the_stylesheet_carries_no_rule_for_the_narrative_classes(self) -> None:
        """R43 (A-e11): a CSS rule would move every no-``--explain`` render.

        The classes are allowlisted and unstyled. Editing ``REPORT_STYLE``
        changes ``STYLE_SHA256`` and both checked-in goldens, which is what
        makes this a *byte* question rather than a taste one.

        Red when: somebody colours the fallback paragraphs without
        regenerating the goldens and reading R43 first.
        """
        assert NARRATIVE_CLASS not in REPORT_STYLE
        assert FALLBACK_CLASS not in REPORT_STYLE
        assert hashlib.sha256(REPORT_STYLE.encode("utf-8")).hexdigest() == STYLE_SHA256
        assert hashlib.sha256(REPORT_SCRIPT.encode("utf-8")).hexdigest() == SCRIPT_SHA256
        # ...and the classes really are allowlisted, so this is not passing
        # because they do not exist.
        assert {NARRATIVE_CLASS, FALLBACK_CLASS} <= CSS_CLASSES

    def test_r43_nothing_links_to_the_narrative_section(self, ac12: dict[str, Any]) -> None:
        """R43 (A-e10): no ``href="#narrative"`` anywhere in an ``--explain`` render.

        Red when: a nav entry is added by hand rather than through
        ``SECTION_IDS`` — the arm above would still pass.
        """
        document = parse(ac12["explain_html"])
        hrefs = {value for _, name, value in document.attributes if name == "href"}
        assert f"#{NARRATIVE_SECTION_ID}" not in hrefs
        assert f'href="#{NARRATIVE_SECTION_ID}"' not in ac12["explain_html"]

    def test_r43_the_section_sits_at_r36s_fixed_position(self, ac12: dict[str, Any]) -> None:
        """R36: "the narrative section anchor" between the header and the findings."""
        with_flag = section_ids_in_order(parse(ac12["explain_html"]))
        without = section_ids_in_order(parse(ac12["plain_html"]))
        assert without == list(SECTION_IDS)
        assert with_flag == ["header", NARRATIVE_SECTION_ID, *SECTION_IDS[1:]]


class TestExplainDocumentSecurity:
    """R34/R35/R51 over the ``--explain`` document — which no inherited probe parses."""

    def test_r34_the_explain_document_still_has_one_script_and_one_style(
        self, ac12: dict[str, Any]
    ) -> None:
        """R34: the constants are untouched by the new section."""
        document = parse(ac12["explain_html"])
        assert document.tag_counts.get("script") == 1
        assert document.tag_counts.get("style") == 1
        assert hashlib.sha256(document.script_body().encode("utf-8")).hexdigest() == SCRIPT_SHA256

    def test_r34_every_attribute_of_an_explain_render_is_allowlisted(self, tmp_path: Path) -> None:
        """R34: the allowlist is generated from the inputs, narrative included.

        Driven over the sentinel trace with a hostile narrative, so the
        attribute check has something adversarial to refuse.
        """
        narrative = Narrative(
            paragraphs=(
                NarrativeParagraph(
                    group="overall",
                    title="the whole run",
                    text='" onload=alert(1) class="finding',
                    fallback=True,
                    reason="timeout",
                ),
            ),
            calls=1,
        )
        analysis = analyze_paths(write_sentinel_trace(tmp_path / "t"), narrative=narrative)
        allowlist = analysis.allowlist()
        document = parse(analysis.html)
        offenders = [
            (element, name, value)
            for element, name, value in document.attributes
            if value not in allowlist.get(name, frozenset())
        ]
        assert not offenders, offenders
        assert not [name for _, name, _ in document.attributes if name.startswith("on")]

    def test_r34_a_default_render_does_not_widen_the_allowlist(self, tmp_path: Path) -> None:
        """R43/W5-A08: the narrative id is absent when the section is.

        The increment-4 review's finding was an allowlist that could be
        widened without anything noticing. ``attribute_allowlist`` gained a
        keyword this increment; this is the arm that says the keyword's
        default changes nothing.
        """
        paths = write_sentinel_trace(tmp_path / "t")
        plain = analyze_paths(paths)
        with_narrative = analyze_paths(
            paths,
            narrative=Narrative(
                paragraphs=(NarrativeParagraph(group="overall", title="the whole run", text="x"),),
                calls=1,
            ),
        )
        assert NARRATIVE_SECTION_ID not in plain.allowlist()["id"]
        assert NARRATIVE_SECTION_ID in with_narrative.allowlist()["id"]
        assert plain.allowlist()["href"] == with_narrative.allowlist()["href"]
        assert set(plain.allowlist()) == set(with_narrative.allowlist())

    def test_r35_the_explain_document_still_has_no_external_reference(
        self, ac12: dict[str, Any]
    ) -> None:
        """R35: no URL of any scheme, and none of the fetching elements."""
        html = ac12["explain_html"]
        for scheme in ("http:", "https:", "file:", "@import", "//"):
            if scheme == "//":
                assert "//" not in html.replace("<!--", "").replace("-->", "")
            else:
                assert scheme not in html
        document = parse(html)
        for element in ("link", "img", "iframe", "object", "embed", "form", "base"):
            assert document.tag_counts.get(element, 0) == 0


class TestExitCodeInvariantR39:
    """R39: "an ``--explain`` failure never changes the exit code"."""

    def test_r39_a_clean_run_still_exits_zero_with_explain(self, tmp_path: Path) -> None:
        """R39: "never converts a 0 into a 1", with no narrator reachable at all.

        Offline, with ``anthropic`` absent and every credential scrubbed, so
        the narration is four fallbacks and the run is otherwise unchanged.
        """
        for extra in ([], ["--fail-on", "critical"], ["--fail-on", "warning"]):
            plain = tmp_path / f"p{len(extra)}.json"
            explained = tmp_path / f"e{len(extra)}.json"
            out_a, out_b = io.StringIO(), io.StringIO()
            code_a = main(["analyze", str(CLEAN), "--json", str(plain), *extra], stdout=out_a)
            code_b = main(
                ["analyze", str(CLEAN), "--json", str(explained), "--explain", *extra],
                stdout=out_b,
            )
            assert code_a == code_b == EXIT_OK, extra

    def test_r39_a_narrator_that_raises_every_time_does_not_change_the_code(
        self, tmp_path: Path
    ) -> None:
        """R39/R43: an exception from the client is a fallback, never an exit 2."""

        class Angry:
            def complete(self, request: Any) -> Any:
                raise RuntimeError("the provider exploded")

        out = io.StringIO()
        err = io.StringIO()
        code = main(
            [
                "analyze",
                str(CLEAN),
                "--json",
                str(tmp_path / "r.json"),
                "--explain",
            ],
            stdout=out,
            stderr=err,
            narrator=Angry(),
        )
        assert (code, err.getvalue()) == (EXIT_OK, "")
        document = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
        assert {p["reason"] for p in document["narrative"]["paragraphs"]} == {"provider_error"}


class TestNoPreviewsAndExplain:
    """R38/R43: the two flags together, which nothing else in the suite drives."""

    @pytest.mark.parametrize("previews", [True, False])
    def test_r43_the_section_strips_cleanly_under_both_modes(
        self, previews: bool, tmp_path: Path
    ) -> None:
        """R43: byte identity is a property of the construction, not of one mode."""
        paths = write_sentinel_trace(tmp_path / "t")
        narrative = Narrative(
            paragraphs=(
                NarrativeParagraph(group="overall", title="the whole run", text="a paragraph"),
            ),
            calls=1,
        )
        plain = analyze_paths(paths, previews=previews)
        explained = analyze_paths(paths, previews=previews, narrative=narrative)
        assert strip_narrative_section(explained.html) == plain.html


class TestExplainWiringR43:
    """R43/R46: what the flag imports, and what the CLI hands the narrator."""

    def test_r43_narrate_is_not_imported_on_the_default_path(self, tmp_path: Path) -> None:
        """R43: "with it off, ``narrate/`` is never imported by the analyze path".

        In a subprocess, because ``sys.modules`` in this one has been full of
        ``narrate`` since the first import in this file.

        Red when: an import of ``narrate`` moves to module scope in
        ``cli/main.py``, which would also load the only code in the product
        that can open a socket on every run of the tool.
        """
        script = (
            "import sys, io\n"
            "from swarm_observer.cli.main import main\n"
            f"main(['analyze', {str(CLEAN)!r}, '--json', {str(tmp_path / 'x.json')!r}],"
            " stdout=io.StringIO())\n"
            "print('narrate' if 'swarm_observer.narrate' in sys.modules else 'absent')\n"
            "print('replay' if 'swarm_observer.replay' in sys.modules else 'absent')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent), "PATH": "/usr/bin"},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["absent", "absent"]

    def test_r43_the_flag_does_import_narrate(self, tmp_path: Path) -> None:
        """R43: the control arm — the check above must be able to say "narrate"."""
        script = (
            "import sys, io\n"
            "from swarm_observer.cli.main import main\n"
            f"main(['analyze', {str(CLEAN)!r}, '--json', {str(tmp_path / 'y.json')!r},"
            " '--explain'], stdout=io.StringIO())\n"
            "print('narrate' if 'swarm_observer.narrate' in sys.modules else 'absent')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent), "PATH": "/usr/bin"},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["narrate"]

    def test_r42_the_cli_hands_the_narrator_the_figures_it_claims_to(self, tmp_path: Path) -> None:
        """R42/S34: ``build_narrative``'s extraction equals the one a test can build.

        ``tests/sentinel_trace.totals_for`` reproduces ``cli.main``'s
        extraction, and a duplicate is only honest if something asserts the
        equality — the same discipline ``tests/pipeline.py`` applies to
        ``selected_slugs``.
        """
        captured: list[Any] = []

        class Recorder:
            def complete(self, request: Any) -> Any:
                captured.append(request)
                raise NarratorTransportError("timeout")

        analysis = analyze_paths(write_sentinel_trace(tmp_path / "t"))
        narrative = build_narrative(
            trace=analysis.trace,
            findings=analysis.findings,
            cost=analysis.cost,
            client=Recorder(),
        )
        assert captured, "the narrator was never asked"
        assert captured[0].totals == totals_for(analysis)
        assert [p.group for p in narrative.paragraphs] == list(narration_groups(analysis.findings))

    def test_r42_the_payload_carries_no_agent_id_and_no_recorded_model(
        self, tmp_path: Path
    ) -> None:
        """R42: agents are an index and models are a snapshot key (S34)."""
        captured: list[Any] = []

        class Recorder:
            def complete(self, request: Any) -> Any:
                captured.append(request)
                raise NarratorTransportError("timeout")

        analysis = analyze_paths(write_sentinel_trace(tmp_path / "t"))
        build_narrative(
            trace=analysis.trace,
            findings=analysis.findings,
            cost=analysis.cost,
            client=Recorder(),
        )
        blob = json.dumps(captured[0].model_dump(mode="json"), sort_keys=True)
        for agent in analysis.trace.agents:
            assert agent.agent_id not in blob
        for span in analysis.trace.spans:
            if span.model:
                assert span.model not in blob


class TestExplainDeterminismR47:
    """R47: an ``--explain`` render is a function of the trace and the narration."""

    def test_r47_three_renders_of_one_narration_are_byte_identical(self, tmp_path: Path) -> None:
        """R47: the precondition a golden file would need, asserted rather than assumed."""
        paths = write_sentinel_trace(tmp_path / "t")
        narrative = Narrative(
            paragraphs=(
                NarrativeParagraph(group="overall", title="the whole run", text="one"),
                NarrativeParagraph(
                    group="agent_loop",
                    title="the whole run",
                    text="two",
                    fallback=True,
                    reason="timeout",
                ),
            ),
            calls=2,
        )
        digests = {
            (
                hashlib.sha256(analysis.html.encode()).hexdigest(),
                hashlib.sha256(analysis.json.encode()).hexdigest(),
            )
            for analysis in (analyze_paths(paths, narrative=narrative) for _ in range(3))
        }
        assert len(digests) == 1

    def test_r47_the_offline_default_explain_run_is_reproducible(self, tmp_path: Path) -> None:
        """R47/R45: with no SDK and no key, two ``--explain`` runs agree byte for byte."""
        digests = set()
        for index in range(2):
            target = tmp_path / f"r{index}.json"
            out = io.StringIO()
            main(["analyze", str(CLEAN), "--json", str(target), "--explain"], stdout=out)
            digests.add(hashlib.sha256(target.read_bytes()).hexdigest())
        assert len(digests) == 1


class TestWave7CliExtractionR42:
    """R42/S34/S38: what ``cli.main.build_narrative`` chooses to put in the payload.

    **The increment-5 review's wave 7, and instance twelve of this project's
    signature defect.** AC12's sentinel sweep and the independent-vocabulary
    arm are the two checks whose declared job is "no string the payload
    carries is unaccounted for". Both run over a payload the test module
    builds with ``tests/sentinel_trace.totals_for`` -- a deliberate,
    equality-asserted duplicate of the CLI's extraction -- and the equality
    assertion covers ``NarrationRequest.totals`` only. Nothing looked at
    ``rate_snapshot_version``, which is the one leaf whose value the CLI
    chooses outright and one of the two the increment-5 tester named as
    "clean by call-site discipline" (S38).

    Wave-7 ``V43`` made ``build_narrative`` pass the literal
    ``"not_the_snapshot_version"``. It reached the narrator's payload and the
    rendered fallback paragraph -- "at rate snapshot
    not_the_snapshot_version", in a document whose own header still printed
    the real one -- and 2875 tests stayed green. The vocabulary arm would not
    have caught it even had it seen that payload, because the string is a
    lowercase slug and the arm allows any slug; that is exactly the
    ``SHAPE_LEAVES`` hole the tester documented.

    The class also closes the corpus gaps the sweep exposed: every
    ``--explain`` test in the branch ran over the sentinel trace, which has
    **one** agent and **zero** priced spans, so ``by_agent`` had a single row
    whose ``agent_index`` and ``priced_spans`` were both ``0`` and
    ``by_model`` was empty. Three mutants survived on that alone.
    """

    #: Four agents, seven priced spans and a resolvable model -- the opposite
    #: of the sentinel trace in every dimension the payload's cost rows use.
    MULTI_AGENT = FIXTURE_DIR / "gaps_explained.jsonl"

    @staticmethod
    def _captured_request(paths: Any) -> Any:
        """One ``NarrationRequest`` as the **CLI** built it, not as a test would."""
        captured: list[Any] = []

        class Recorder:
            def complete(self, request: Any) -> Any:
                captured.append(request)
                raise NarratorTransportError("timeout")

        analysis = analyze_paths(paths)
        build_narrative(
            trace=analysis.trace,
            findings=analysis.findings,
            cost=analysis.cost,
            client=Recorder(),
        )
        assert captured, "the narrator was never asked"
        return analysis, captured[0]

    def test_r42_the_cli_sends_the_snapshots_own_version(self) -> None:
        """R42/S38 (wave-7 ``V43``): the payload's snapshot version has a provenance.

        Compared against a **separately loaded** ``SnapshotRateSource``
        rather than against the ``CostReport`` the same call already used, so
        the arm is a statement about the bundled snapshot and not a tautology
        over one object.

        Red when: ``build_narrative`` passes anything but the loaded
        snapshot's version -- which nothing else in the suite would notice,
        because the leaf's only other guard is a 64-character alphabet.

        The second arm is what makes the first one mean something. In the
        bundled snapshot ``version`` and ``snapshot_date`` are the **same
        string** (``2026-09-10``), so an equality against the real data cannot
        tell "the CLI sends the version" from "the CLI sends the date" --
        wave-7 ``V05`` swapped them and survives for that reason alone. So the
        arm re-runs the extraction over a ``CostReport`` whose meta has been
        given a version that differs from its date, where the two answers are
        distinguishable.
        """
        from swarm_observer.cost.snapshot import SnapshotRateSource

        analysis, request = self._captured_request([CLEAN])
        expected = SnapshotRateSource().meta.version
        assert request.rate_snapshot_version == expected
        assert request.rate_snapshot_version == analysis.cost.meta.version
        # And it is the version the document itself prints, so the narrative
        # cannot attribute a run's costs to a snapshot the header denies.
        assert f"rates {expected} of " in analysis.html

        captured: list[Any] = []

        class Recorder:
            def complete(self, request: Any) -> Any:
                captured.append(request)
                raise NarratorTransportError("timeout")

        distinct = analysis.cost.meta.model_copy(update={"version": "v7-review-probe"})
        assert distinct.version != distinct.snapshot_date
        build_narrative(
            trace=analysis.trace,
            findings=analysis.findings,
            cost=analysis.cost.model_copy(update={"meta": distinct}),
            client=Recorder(),
        )
        assert captured[0].rate_snapshot_version == "v7-review-probe"

    def test_r42_the_payload_counts_the_traces_model_calls(self) -> None:
        """R42 (wave-7 ``V03``): ``model_calls`` was asserted by nothing.

        Inverting the span-kind test survived the suite. The figure is one of
        the three the overall template prints, so a wrong one is a sentence a
        reader would act on.

        Red when: the count stops being the number of ``model_call`` spans.

        Driven over ``gaps_explained.jsonl`` and not over ``clean_single_agent
        .jsonl``, and the reason is the finding's own shape: the clean fixture
        has five ``model_call`` spans and five of everything else, so
        inverting the test is the identity on it. Half the corpus is like
        that. The ``complement`` guard below is what stops a future edit
        moving this arm back onto such a fixture.
        """
        analysis, request = self._captured_request([self.MULTI_AGENT])
        spans = analysis.trace.spans
        expected = sum(1 for span in spans if span.kind == "model_call")
        complement = len(spans) - expected
        assert expected > 0, "the fixture must have model calls for this to measure anything"
        assert expected != complement, (
            "the fixture must have unequal model_call and non-model_call counts, or "
            "inverting the predicate is unobservable"
        )
        assert request.totals.model_calls == expected

    def test_r42_every_agent_gets_a_row_indexed_by_its_own_index(self) -> None:
        """R42/S34 (wave-7 ``V08``, ``V10``): a corpus with more than one agent.

        Both survivors were the sentinel trace's shape rather than the code:
        one agent means ``by_agent[:1]`` is the identity, and zero priced
        spans mean ``agent_index`` and ``priced_spans`` are both ``0``, so
        confusing the two is invisible. This drives a four-agent fixture,
        which is the ninth-instance lesson applied to the payload: a sweep
        over a corpus that loads one row is a sweep over one row.

        Red when: the rows are truncated, or indexed by anything but
        ``AgentCost.agent_index``.
        """
        analysis, request = self._captured_request([self.MULTI_AGENT])
        rows = request.totals.by_agent
        assert len(rows) == len(analysis.trace.agents) >= 4
        assert [row.agent_index for row in rows] == [
            row.agent_index for row in analysis.cost.by_agent
        ]
        assert [row.agent_index for row in rows] == sorted({row.agent_index for row in rows})
        # ...and the index is distinguishable from every other integer on the
        # row, so a call-argument swap cannot pass by coincidence.
        assert any(row.agent_index != row.priced_spans for row in rows)
        assert request.totals.agents == len(analysis.trace.agents)

    def test_r42_the_payload_carries_a_real_snapshot_model_key(self) -> None:
        """R42/S38: the ``model_key`` leaf, over a trace whose model resolves.

        The sentinel corpus prices nothing, so ``by_model`` is empty there and
        AC12's sweep of that leaf is a sweep of an empty tuple -- the
        ninth-instance shape again. This is the arm that gives the leaf a
        value: the key is one the **bundled snapshot** defines, never the
        recorded ``Span.model`` (R30 calls that trace-derived), and the two
        are different strings in this fixture.

        Red when: the payload starts keying cost rows by a recorded model id.
        """
        from swarm_observer.cost.snapshot import SnapshotRateSource

        analysis, request = self._captured_request([self.MULTI_AGENT])
        keys = {row.model_key for row in request.totals.by_model}
        assert keys, "the fixture must price something for this to measure anything"
        assert keys <= set(SnapshotRateSource().model_keys())
        assert request.totals.priced_spans > 0
        assert request.totals.cost_usd != "0.000000"
        recorded = {span.model for span in analysis.trace.spans if span.model}
        assert recorded - keys, "the recorded model must differ from the snapshot key here"
        blob = json.dumps(request.model_dump(mode="json"), sort_keys=True)
        for model in recorded:
            assert model not in blob
