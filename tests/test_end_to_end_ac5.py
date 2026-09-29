"""AC5: one command, a trace with findings from every detector, two files out.

AC5 names an input the corpus cannot produce — "a multi-agent fixture trace with
a known finding in **every** detector class" — and the increment-4 coder showed
why: analysed together, fixtures with the same ``agentId`` interleave in basename
order (R6), which breaks ``agent_loop``'s per-agent signature sequence and
``retry_storm``'s sliding window. Best over the whole 26-fixture corpus is 4 of
7. That is **S28**, and an acceptance criterion whose input cannot exist is the
mirror image of a check that cannot fail.

This module splits AC5 rather than weakening it:

* :class:`TestEveryDetectorClassAC5` uses a ``Trace`` assembled by
  ``tests.synthetic_traces`` in which all seven detectors fire, and asserts
  every clause AC5 makes about the *document*.
* :class:`TestTheCommandAC5` drives the real CLI as a subprocess over the real
  fixture corpus and asserts every clause AC5 makes about the *command* — exit
  code, both files written, section order in the file on disk.
* :func:`test_s28_the_fixture_corpus_cannot_produce_a_single_all_detector_trace`
  pins S28 itself, so a PM ruling or a new fixture makes it red rather than
  leaving the split above looking like a tester's convenience.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from swarm_observer.cost.compute import UNPRICED_REASONS
from swarm_observer.detect.registry import ALL_DETECTORS, DETECTOR_SLUGS
from swarm_observer.model.trace import TRACE_SCHEMA_VERSION, Trace

from .detector_corpus import FIXTURE_DIR
from .harness import run_cli
from .pipeline import Analysis, analyze_paths, analyze_trace
from .rendered import parse, section_ids_in_order
from .synthetic_traces import TraceBuilder, hex_id, tokens
from .test_report_html_r34_r35_r36 import R36_SECTION_ORDER


def all_detector_trace() -> Trace:
    """A multi-agent trace in which every registered detector fires.

    One agent per detector family, because the detectors that scan a *sequence*
    — ``agent_loop``'s signature run and ``retry_storm``'s ten-span window — are
    exactly the ones another agent's spans interleave into nothing. That is S28's
    mechanism, reproduced deliberately rather than worked around by accident.
    """
    builder = TraceBuilder()
    # agent_loop (A B A B A B) and, as a consequence, repeated_tool_call.
    for letter in "ABABAB":
        call = builder.model_call(agent_id="loop", usage=tokens(100), start_ms=0, end_ms=10)
        builder.tool_call(
            agent_id="loop",
            parent=call,
            tool_name=f"Tool{letter}",
            digest=hex_id(ord(letter)),
        )
    # retry_storm and failed_tool_call.
    for index in range(4):
        builder.tool_call(
            agent_id="storm",
            tool_name="Bash",
            digest=hex_id(900 + index),
            status="error",
            result_preview="command failed",
        )
    # unresolved_tool_call — mid-trace, so R22's truncation carve-out does not
    # exclude it (AC9).
    builder.tool_call(agent_id="unresolved", tool_name="Read", digest=hex_id(700), status="missing")
    builder.user_message(agent_id="unresolved")
    # blocked_agent: a 199-second unexplained gap.
    builder.model_call(agent_id="blocked", usage=tokens(100), start_ms=0, end_ms=1_000)
    builder.model_call(agent_id="blocked", usage=tokens(100), start_ms=200_000, end_ms=201_000)
    # anomalous_span: population 8 with one outlier over R24's token floor.
    for value in [100] * 7 + [50_000]:
        builder.model_call(agent_id="outlier", usage=tokens(value), start_ms=0, end_ms=10)
    return builder.build()


@pytest.fixture(scope="module")
def everything() -> Analysis:
    return analyze_trace(all_detector_trace())


class TestEveryDetectorClassAC5:
    """AC5's clauses about the rendered documents."""

    def test_ac5_every_registered_detector_appears_in_the_findings(
        self, everything: Analysis
    ) -> None:
        """AC5: "every detector in ``ALL_DETECTORS`` appears in the findings".

        Red when: a detector is added to the registry without this trace
        growing a case for it — the same friction R48 creates for the fixture
        corpus, applied to the acceptance criterion's own input.
        """
        fired = {finding.detector for finding in everything.findings}
        assert fired == set(DETECTOR_SLUGS)
        assert len(ALL_DETECTORS) == 7

    def test_ac5_every_detector_appears_in_the_rendered_html(self, everything: Analysis) -> None:
        """AC5/R36: and they reach the document, not only the finding list.

        Red when: the findings section drops a group — which the severity
        grouping or the filter markup could do without changing ``findings``.
        """
        document = parse(everything.html)
        rendered = {value for _, name, value in document.attributes if name == "data-detector"}
        assert rendered == set(DETECTOR_SLUGS)

    def test_ac5_the_json_report_carries_the_pinned_schema_version(
        self, everything: Analysis
    ) -> None:
        """AC5/R1: "``report.json`` validates against the R1 schema version".

        Red when: the schema version drifts from the model's constant.
        """
        document = json.loads(everything.json)
        assert document["meta"]["schema_version"] == TRACE_SCHEMA_VERSION == "1.0.0"

    def test_ac5_every_model_call_is_priced_or_unpriced_with_a_reason(
        self, everything: Analysis
    ) -> None:
        """AC5/R30: "either priced or in ``unpriced`` with a reason from the R30 enum".

        The two sets must partition the model calls: an overlap would double
        count and a gap would silently drop a span from the cost section, which
        R30 says never happens.

        Red when: a span falls out of both — the failure R30's "nothing dropped"
        clause is about.
        """
        model_calls = {span.seq for span in everything.trace.spans if span.kind == "model_call"}
        priced = {row.seq for row in everything.cost.spans}
        unpriced = {row.seq for row in everything.cost.unpriced}
        assert priced | unpriced == model_calls
        assert not priced & unpriced
        for row in everything.cost.unpriced:
            assert row.reason in UNPRICED_REASONS

    def test_ac5_each_of_the_four_groupings_sums_to_its_members(self, everything: Analysis) -> None:
        """AC5/R31/R29: "the four R31 groupings' totals each equal the sum of their members".

        Every row is an already-quantized ``Decimal``, so the sums are exact and
        a float anywhere in the engine would make one of them off by a
        micro-dollar.

        Red when: a grouping is computed from unquantized values, or a member is
        counted in one grouping and not another.
        """
        cost = everything.cost
        span_total = sum((row.cost_usd for row in cost.spans), Decimal("0.000000"))
        assert span_total == cost.total_cost_usd
        assert sum((row.cost_usd for row in cost.by_agent), Decimal("0.000000")) == span_total
        assert sum((row.cost_usd for row in cost.by_model), Decimal("0.000000")) == span_total
        for row in cost.by_detector:
            assert row.wasted_cost_usd == row.wasted_cost_usd.quantize(Decimal("0.000001"))
        assert {row.detector for row in cost.by_detector} <= set(DETECTOR_SLUGS)

    def test_ac5_the_html_sections_appear_in_the_exact_r36_order(
        self, everything: Analysis
    ) -> None:
        """AC5/R36: "the HTML sections appear in the exact R36 order".

        Red when: a section moves. Compared against a literal typed from R36.
        """
        assert section_ids_in_order(parse(everything.html)) == list(R36_SECTION_ORDER)

    def test_ac5_the_grouping_is_severity_descending_then_slug_then_id(
        self, everything: Analysis
    ) -> None:
        """R36: the findings section's own ordering rule, which is not R13's.

        R13 sorts ascending by severity for the JSON report; R36 reverses the
        first key only. Both orderings exist in one process, so the wrong one is
        an easy mistake and an invisible one.

        Red when: ``_grouped``'s key loses its minus sign, or its second or
        third component.
        """
        from swarm_observer.detect.base import SEVERITY_RANK

        html = everything.html
        order: list[tuple[int, str, str]] = []
        for finding in everything.findings:
            index = html.index(f'id="{finding.finding_id}"')
            order.append((index, finding.severity, finding.detector))
        order.sort()
        keys = [(-SEVERITY_RANK[severity], detector) for _, severity, detector in order]
        assert keys == sorted(keys)
        assert order[0][1] == "critical"

    def test_r36_two_findings_of_one_severity_and_detector_order_by_finding_id(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """R36: the third component of the grouping key — ``finding_id`` — is load-bearing.

        The test above compares ``(-severity_rank, detector)`` and is satisfied
        by any order within a detector group. This one pins the third
        component end to end. It does **not** on its own kill the mutant that
        drops ``finding_id`` from ``_grouped``'s key: R13 already returns
        findings in ``finding_id`` order and Python's sort is stable, so any
        input that came from the registry is already ordered. That mutant is
        killed by
        ``test_report_html_r34_r35_r36.TestFindingOrderR36::test_r36_the_renderer_orders_findings_it_was_handed_out_of_order``,
        which hands the renderer an unsorted pair. Both are kept: this one is
        the property a reader of the report cares about, that one is the
        property the renderer is responsible for.

        Red when: the end-to-end order stops matching ``finding_id`` — through
        ``_grouped`` **or** through R13.
        """
        from .hostile_corpus import write_hostile_trace

        analysis = analyze_paths(write_hostile_trace(tmp_path_factory.mktemp("group-order")))
        repeated = sorted(
            (finding for finding in analysis.findings if finding.detector == "repeated_tool_call"),
            key=lambda finding: finding.finding_id,
        )
        assert len(repeated) == 2, "the corpus no longer produces the pair this test needs"
        assert repeated[0].severity == repeated[1].severity
        first = analysis.html.index(f'id="{repeated[0].finding_id}"')
        second = analysis.html.index(f'id="{repeated[1].finding_id}"')
        assert first < second, "findings within a detector group render in finding_id order"


class TestTheCommandAC5:
    """AC5's clauses about the command: exit code, the two files, the bytes on disk."""

    def corpus(self) -> list[str]:
        return [str(path) for path in sorted(FIXTURE_DIR.glob("*.jsonl"))]

    def test_ac5_fail_on_critical_exits_1_and_writes_both_files(self, tmp_path: Path) -> None:
        """AC5/R39: exit 1, "both files are still written".

        Red when: the exit code is computed before the files are written, or a
        non-zero code short-circuits the write — the failure mode that makes a
        CI job that gates on ``--fail-on`` unable to show what it gated on.
        """
        html = tmp_path / "report.html"
        report = tmp_path / "report.json"
        result = run_cli(
            [
                "analyze",
                *self.corpus(),
                "--out",
                str(html),
                "--json",
                str(report),
                "--fail-on",
                "critical",
            ]
        )
        assert result.returncode == 1, result.stderr
        assert html.is_file() and report.is_file()
        assert result.stderr == ""
        assert result.stdout.count("\n") == 1
        assert "critical=" in result.stdout

    def test_ac5_the_file_on_disk_carries_the_sections_in_order(self, tmp_path: Path) -> None:
        """AC5/R36: asserted against the written file, not the renderer's return value.

        Red when: the atomic-write path writes something other than what was
        rendered — encoding, newline translation, truncation.
        """
        html = tmp_path / "report.html"
        result = run_cli(["analyze", *self.corpus(), "--out", str(html), "--fail-on", "none"])
        assert result.returncode == 0, result.stderr
        written = html.read_text(encoding="utf-8")
        assert section_ids_in_order(parse(written)) == list(R36_SECTION_ORDER)
        assert written.endswith("</html>\n")

    def test_ac5_the_written_html_equals_the_in_process_render(self, tmp_path: Path) -> None:
        """AC5/R47: the CLI's bytes and ``tests.pipeline``'s bytes are the same bytes.

        This is what licenses every other test in this increment to use the
        in-process helper. Red when: the CLI starts passing different options —
        the ``detectors`` list in the header is the likeliest divergence.
        """
        source = FIXTURE_DIR / "multi_agent_subagent.jsonl"
        html = tmp_path / "report.html"
        result = run_cli(["analyze", str(source), "--out", str(html)])
        assert result.returncode == 0, result.stderr
        assert html.read_text(encoding="utf-8") == analyze_paths([source]).html

    def test_ac5_a_json_only_run_writes_no_html(self, tmp_path: Path) -> None:
        """A-d10/S18: ``--json`` alone is a legitimate invocation.

        Red when: ``--out`` becomes mandatory again, which would make a CI job
        that gates on ``--fail-on`` write an artefact nobody reads.
        """
        report = tmp_path / "report.json"
        result = run_cli(
            ["analyze", str(FIXTURE_DIR / "clean_single_agent.jsonl"), "--json", str(report)]
        )
        assert result.returncode == 0, result.stderr
        assert report.is_file()
        assert not (tmp_path / "report.html").exists()
        assert str(report) in result.stdout

    def test_ad11_a_failing_run_writes_neither_document(self, tmp_path: Path) -> None:
        """A-d11/R11: with two outputs, a partial write is a partial result with exit 0.

        Both documents are built in memory before either is staged. Red when:
        they are written one at a time — a malformed trace would leave the HTML
        written and the JSON not.
        """
        broken = tmp_path / "broken.jsonl"
        broken.write_text('{"type":"user"\n', encoding="utf-8")
        html = tmp_path / "report.html"
        report = tmp_path / "report.json"
        html.write_text("PRE-EXISTING-HTML", encoding="utf-8")
        report.write_text("PRE-EXISTING-JSON", encoding="utf-8")
        result = run_cli(["analyze", str(broken), "--out", str(html), "--json", str(report)])
        assert result.returncode == 2
        assert result.stderr.count("\n") == 1
        assert "Traceback" not in result.stderr
        assert html.read_text(encoding="utf-8") == "PRE-EXISTING-HTML"
        assert report.read_text(encoding="utf-8") == "PRE-EXISTING-JSON"


def test_s28_the_fixture_corpus_cannot_produce_a_single_all_detector_trace() -> None:
    """S28: AC5's named input does not exist, and the corpus cannot build it.

    Analysed together, fixtures sharing an ``agentId`` interleave in basename
    order (R6), which destroys ``agent_loop``'s signature sequence and
    ``retry_storm``'s ten-span window. This asserts the shortfall rather than
    describing it, so adding the fixture S28 asks for — or amending AC5 —
    makes this test red with the instruction in its docstring: delete it, and
    move :class:`TestEveryDetectorClassAC5` onto the new fixture.

    Red when: S28 is resolved. That is the point.
    """
    paths = sorted(FIXTURE_DIR.glob("*.jsonl"))
    assert len(paths) >= 20
    fired = {finding.detector for finding in analyze_paths(paths).findings}
    assert fired < set(DETECTOR_SLUGS), (
        "the whole corpus now fires every detector in one run; S28 is closed"
    )
    assert "agent_loop" not in fired or "retry_storm" not in fired
