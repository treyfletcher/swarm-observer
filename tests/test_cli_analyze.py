"""The ``analyze`` and ``detectors`` subcommands: R38, R39, R40, and AC7 end to end.

Exit codes are a taxonomy rather than a habit, and the taxonomy is only useful
if the four numbers are actually distinguishable from a script. So every code is
driven from a condition only that code should describe, and the two that are
easiest to conflate — argparse's own ``2`` and R11's fail-closed ``2`` — are
pinned apart with a case each.

``main(argv, stdout=…, stderr=…)`` takes both streams, so most cases run in
process; the arms where the *process* is the subject (stdout bytes under a
foreign ``TZ`` or ``LC_ALL``, which only take effect at interpreter start) go
through a real subprocess.

Every assertion about what is *not* written is paired with one about what is:
"no output file was created" is satisfied perfectly by a tool that never writes
anything, so the control arm sits next to it.
"""

from __future__ import annotations

import io
import json
import os
import socket
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from swarm_observer import __version__
from swarm_observer.cli import main as cli_main
from swarm_observer.cli.main import (
    EXIT_FAIL_CLOSED,
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_USAGE,
    FAIL_ON_CHOICES,
    UsageError,
    build_config,
    build_limits,
    build_parser,
    check_output_path,
    detectors_document,
    exit_code_for,
    expand_inputs,
    main,
    run,
    selected_slugs,
    summary_line,
)
from swarm_observer.cost.compute import CostError, format_usd
from swarm_observer.cost.snapshot import SnapshotRateSource
from swarm_observer.detect.base import DetectorConfig, build_finding
from swarm_observer.detect.registry import ALL_DETECTORS, DETECTOR_SLUGS
from swarm_observer.ingest.registry import DEFAULT_ADAPTER, build_adapter
from swarm_observer.ingest.source import IngestLimits, TraceError
from swarm_observer.model.trace import TRACE_SCHEMA_VERSION, TokenUsage

from .detector_corpus import FIXTURE_DIR
from .harness import DETERMINISM_ENVIRONMENTS, run_cli, sha256_text
from .synthetic_traces import TraceBuilder

SHIPPED = SnapshotRateSource()

CLEAN = FIXTURE_DIR / "clean_single_agent.jsonl"
DUPLICATE = FIXTURE_DIR / "duplicate_tool_call.jsonl"
HOSTILE = FIXTURE_DIR / "hostile.jsonl"
GAPS = FIXTURE_DIR / "gaps_unexplained.jsonl"


def invoke(*argv: str) -> tuple[int, str, str]:
    """Run the CLI in process and return ``(exit code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def record(**fields: Any) -> dict[str, Any]:
    """One raw Claude Code record with the fields R4 requires."""
    base = {
        "agentId": "root",
        "parentUuid": None,
        "sessionId": "s",
        "type": "user",
        "uuid": "u-0",
        "timestamp": "2026-03-02T09:00:00.000Z",
    }
    base.update(fields)
    return base


def assistant(uuid: str, when: str, usage: dict[str, Any], model: str) -> dict[str, Any]:
    """One assistant record carrying a usage block."""
    return record(
        type="assistant",
        uuid=uuid,
        requestId=f"req-{uuid}",
        timestamp=when,
        message={
            "content": [{"text": "ok", "type": "text"}],
            "id": f"msg-{uuid}",
            "model": model,
            "role": "assistant",
            "stop_reason": "end_turn",
            "usage": usage,
        },
    )


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    """Write ``records`` as JSONL and return the path."""
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    return path


class TestSurfaceR38:
    """R38: exactly three subcommands, and the flags the requirement names."""

    def test_r38_the_parser_exposes_exactly_three_subcommands(self) -> None:
        """R38: ``analyze``, ``schema``, ``detectors`` and no others."""
        parser = build_parser()
        actions = [
            action
            for action in parser._subparsers._group_actions  # type: ignore[union-attr]
            if hasattr(action, "choices")
        ]
        assert set(actions[0].choices) == {"analyze", "schema", "detectors"}

    @pytest.mark.parametrize(
        "flag",
        [
            "--out",
            "--json",
            "--adapter",
            "--explain",
            "--no-previews",
            "--detector",
            "--blocked-gap-seconds",
            "--fail-on",
            "--max-file-bytes",
            "--max-line-bytes",
            "--max-records",
        ],
    )
    def test_r38_analyze_declares_every_flag_the_requirement_names(self, flag: str) -> None:
        """R38: ``--help`` shows the whole v1 surface even where it is deferred."""
        parser = build_parser()
        analyze = parser._subparsers._group_actions[0].choices["analyze"]  # type: ignore[union-attr]
        options = {string for action in analyze._actions for string in action.option_strings}
        assert flag in options

    def test_r38_fail_on_choices_are_the_pinned_three(self) -> None:
        """R38: ``none`` is the default because a tool that fails by default gets wrapped."""
        assert FAIL_ON_CHOICES == ("none", "warning", "critical")

    def test_r38_detectors_prints_registry_order_with_severity_and_title(self) -> None:
        """R38: one line per detector, in registry order, deterministically."""
        code, out, err = invoke("detectors")
        assert code == EXIT_OK
        assert err == ""
        lines = out.rstrip("\n").split("\n")
        assert len(lines) == len(ALL_DETECTORS)
        for line, detector in zip(lines, ALL_DETECTORS, strict=True):
            assert line.startswith(detector.slug)
            assert detector.default_severity in line
            assert line.endswith(detector.title)
        assert out == detectors_document()

    def test_r38_detectors_output_is_a_pure_function_of_the_registry(self) -> None:
        """R38/R40: no padding constant to fall out of date, no clock, no locale."""
        assert detectors_document() == detectors_document()
        lines = detectors_document().splitlines()
        widest = max(len(detector.slug) for detector in ALL_DETECTORS)
        # Every severity column starts at the same offset, computed from the
        # registry rather than from a hand-maintained constant.
        assert {
            line.index(detector.default_severity)
            for line, detector in zip(lines, ALL_DETECTORS, strict=True)
        } == {widest + 2}

    def test_r38_schema_prints_the_schema_version(self) -> None:
        """R38: the ``schema`` subcommand still names ``TRACE_SCHEMA_VERSION``."""
        code, out, err = invoke("schema")
        assert code == EXIT_OK and err == ""
        assert json.loads(out)["schema_version"] == TRACE_SCHEMA_VERSION


class TestInputExpansionR38:
    """R38: a directory expands to its ``*.jsonl`` children, sorted, non-recursively."""

    def test_r38_a_directory_yields_only_its_jsonl_children_sorted(self, tmp_path: Path) -> None:
        """R38/AC16: three JSONL files and one ``.txt``; only the three, by basename."""
        for name in ("c.jsonl", "a.jsonl", "b.jsonl"):
            (tmp_path / name).write_text("{}\n", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")
        nested = tmp_path / "deeper"
        nested.mkdir()
        (nested / "z.jsonl").write_text("{}\n", encoding="utf-8")
        expanded = expand_inputs([str(tmp_path)])
        assert [path.name for path in expanded] == ["a.jsonl", "b.jsonl", "c.jsonl"]

    def test_r38_expansion_is_not_iterdir_order(self, tmp_path: Path) -> None:
        """R47's premise: ``seq`` depends on file order, so file order is sorted.

        The files are created in reverse so the filesystem's own order is a
        plausible wrong answer rather than an unreachable one.
        """
        for name in ("z.jsonl", "m.jsonl", "a.jsonl"):
            (tmp_path / name).write_text("{}\n", encoding="utf-8")
        assert [path.name for path in expand_inputs([str(tmp_path)])] == [
            "a.jsonl",
            "m.jsonl",
            "z.jsonl",
        ]

    def test_r38_a_named_file_is_passed_through_whatever_its_suffix(self, tmp_path: Path) -> None:
        """R38: the ``*.jsonl`` filter is a directory rule, not a file rule."""
        target = tmp_path / "trace.log"
        target.write_text("{}\n", encoding="utf-8")
        assert expand_inputs([str(target)]) == (target,)

    def test_r38_a_directory_entry_that_is_not_a_regular_file_reaches_the_reader(
        self, tmp_path: Path
    ) -> None:
        """R38: a subdirectory named ``*.jsonl`` is a fail-closed exit 2, not a silent skip."""
        (tmp_path / "sub.jsonl").mkdir()
        write_jsonl(tmp_path / "a.jsonl", [record()])
        code, out, err = invoke("analyze", str(tmp_path), "--json", str(tmp_path / "r.json"))
        assert code == EXIT_FAIL_CLOSED
        assert err.count("\n") == 1
        assert err.startswith("swarm-observer: not_a_regular_file:")
        assert out == ""

    def test_r38_directory_expansion_matches_the_explicit_file_list(self, tmp_path: Path) -> None:
        """R38: a directory run and a two-file run produce the same report."""
        source = tmp_path / "in"
        source.mkdir()
        for name in ("clean_single_agent.jsonl", "duplicate_tool_call.jsonl"):
            (source / name).write_bytes((FIXTURE_DIR / name).read_bytes())
        directory_report = tmp_path / "dir.json"
        explicit_report = tmp_path / "explicit.json"
        assert invoke("analyze", str(source), "--json", str(directory_report))[0] == EXIT_OK
        assert (
            invoke(
                "analyze",
                str(source / "duplicate_tool_call.jsonl"),
                str(source / "clean_single_agent.jsonl"),
                "--json",
                str(explicit_report),
            )[0]
            == EXIT_OK
        )
        assert directory_report.read_text() == explicit_report.read_text()


class TestDetectorSelectionR38:
    """R38: ``--detector`` is repeatable and restricts the run."""

    def test_r38_a_single_detector_restricts_the_findings(self, tmp_path: Path) -> None:
        """R38: only the named detector's findings appear."""
        report = tmp_path / "r.json"
        code, _, _ = invoke(
            "analyze", str(GAPS), "--json", str(report), "--detector", "blocked_agent"
        )
        assert code == EXIT_OK
        document = json.loads(report.read_text())
        assert {item["detector"] for item in document["findings"]} == {"blocked_agent"}
        assert document["meta"]["options"]["detectors"] == ["blocked_agent"]

    def test_r38_the_flag_is_repeatable_and_reported_in_registry_order(
        self, tmp_path: Path
    ) -> None:
        """R38: two ``--detector`` flags, reported in registry order not argument order."""
        report = tmp_path / "r.json"
        invoke(
            "analyze",
            str(DUPLICATE),
            "--json",
            str(report),
            "--detector",
            "agent_loop",
            "--detector",
            "repeated_tool_call",
        )
        document = json.loads(report.read_text())
        assert document["meta"]["options"]["detectors"] == [
            "repeated_tool_call",
            "agent_loop",
        ]

    def test_r38_no_detector_flag_means_every_detector(self, tmp_path: Path) -> None:
        """R25: ``enabled=None`` is the whole registry."""
        report = tmp_path / "r.json"
        invoke("analyze", str(CLEAN), "--json", str(report))
        assert json.loads(report.read_text())["meta"]["options"]["detectors"] == list(
            DETECTOR_SLUGS
        )

    def test_r38_selected_slugs_reads_the_config_not_the_argument_list(self) -> None:
        """R38: registry order, whatever order the flags arrived in."""
        config = DetectorConfig(enabled=frozenset({"anomalous_span", "repeated_tool_call"}))
        assert selected_slugs(config) == ("repeated_tool_call", "anomalous_span")
        assert selected_slugs(DetectorConfig()) == DETECTOR_SLUGS

    def test_r38_blocked_gap_seconds_moves_one_detector(self, tmp_path: Path) -> None:
        """R25/R38: the one integer a golden report is a function of."""
        default_report = tmp_path / "a.json"
        raised_report = tmp_path / "b.json"
        invoke("analyze", str(GAPS), "--json", str(default_report), "--detector", "blocked_agent")
        invoke(
            "analyze",
            str(GAPS),
            "--json",
            str(raised_report),
            "--detector",
            "blocked_agent",
            "--blocked-gap-seconds",
            "500",
        )
        assert json.loads(default_report.read_text())["findings"]
        assert json.loads(raised_report.read_text())["findings"] == []
        raised = json.loads(raised_report.read_text())
        assert raised["meta"]["options"]["blocked_gap_seconds"] == 500


class TestNoPreviewsPlumbingR38:
    """R38/A10: the flag reaches ingestion *and* the renderer."""

    def test_r38_the_flag_blanks_previews_at_ingest(self, tmp_path: Path) -> None:
        """A10: the bytes are gone from the model, not merely from the document."""
        report = tmp_path / "r.json"
        invoke("analyze", str(HOSTILE), "--json", str(report), "--no-previews")
        document = json.loads(report.read_text())
        assert all(span["text_preview"] == "" for span in document["spans"])
        assert all(span["tool_input_preview"] == "" for span in document["spans"])

    def test_r38_the_default_run_keeps_the_previews(self, tmp_path: Path) -> None:
        """R38: the non-vacuous arm — without the flag the text is there."""
        report = tmp_path / "r.json"
        invoke("analyze", str(HOSTILE), "--json", str(report))
        document = json.loads(report.read_text())
        assert any(span["tool_input_preview"] for span in document["spans"])

    def test_r38_no_hostile_payload_survives_the_flag_through_the_cli(self, tmp_path: Path) -> None:
        """R38: the whole pipeline, not the renderer in isolation."""
        blanked = tmp_path / "blank.json"
        visible = tmp_path / "visible.json"
        invoke("analyze", str(HOSTILE), "--json", str(blanked), "--no-previews")
        invoke("analyze", str(HOSTILE), "--json", str(visible))
        blanked_text = blanked.read_text()
        visible_text = visible.read_text()
        for payload in ("onerror", "alert(1)", "javascript:", "{{7*7}}", "<script", "]]>"):
            assert payload not in blanked_text, payload
            assert payload in visible_text, f"{payload} absent even by default: vacuous arm"

    def test_r38_the_two_modes_produce_different_bytes(self, tmp_path: Path) -> None:
        """R38: the flag is recorded and actually changes the document."""
        blanked = tmp_path / "blank.json"
        visible = tmp_path / "visible.json"
        invoke("analyze", str(HOSTILE), "--json", str(blanked), "--no-previews")
        invoke("analyze", str(HOSTILE), "--json", str(visible))
        assert blanked.read_text() != visible.read_text()
        assert json.loads(blanked.read_text())["meta"]["options"]["previews"] is False
        assert json.loads(visible.read_text())["meta"]["options"]["previews"] is True


class TestExitCodesR39:
    """R39: four codes, four conditions, and no two conditions sharing one."""

    def test_r39_a_clean_run_with_fail_on_none_is_zero(self, tmp_path: Path) -> None:
        """R39: ``none`` never fails, even with findings present."""
        report = tmp_path / "r.json"
        code, _out, err = invoke(
            "analyze", str(DUPLICATE), "--json", str(report), "--fail-on", "none"
        )
        assert code == EXIT_OK
        assert err == ""
        assert json.loads(report.read_text())["findings"]

    def test_r39_the_default_threshold_is_none(self, tmp_path: Path) -> None:
        """R39: an unflagged run of a trace with findings still exits 0."""
        report = tmp_path / "r.json"
        assert invoke("analyze", str(DUPLICATE), "--json", str(report))[0] == EXIT_OK

    @pytest.mark.parametrize(
        ("fail_on", "expected"),
        [("none", EXIT_OK), ("warning", EXIT_FINDINGS), ("critical", EXIT_FINDINGS)],
    )
    def test_r39_a_trace_with_a_critical_finding_meets_both_thresholds(
        self, tmp_path: Path, fail_on: str, expected: int
    ) -> None:
        """R39: ``1`` when a finding is at or above the threshold."""
        report = tmp_path / "r.json"
        code, _, _ = invoke("analyze", str(DUPLICATE), "--json", str(report), "--fail-on", fail_on)
        assert code == expected

    def test_r39_a_warning_only_run_does_not_meet_the_critical_threshold(
        self, tmp_path: Path
    ) -> None:
        """R39: "at or above" is a comparison, and both sides of it are exercised."""
        report = tmp_path / "r.json"
        source = FIXTURE_DIR / "retry_storm_warning.jsonl"
        assert (
            invoke("analyze", str(source), "--json", str(report), "--fail-on", "critical")[0]
            == EXIT_OK
        )
        counts = json.loads(report.read_text())["meta"]["counts"]["findings_by_severity"]
        assert counts["critical"] == 0
        assert counts["warning"] > 0, "vacuous: this fixture produced no warning findings"
        # The same trace against the lower threshold does fail, so the comparison
        # is being exercised rather than the absence of findings.
        assert (
            invoke("analyze", str(source), "--json", str(report), "--fail-on", "warning")[0]
            == EXIT_FINDINGS
        )

    def test_r39_an_info_only_run_does_not_meet_the_warning_threshold(self, tmp_path: Path) -> None:
        """R39: the lowest severity against the middle threshold."""
        report = tmp_path / "r.json"
        source = FIXTURE_DIR / "api_error_rate_limit.jsonl"
        assert (
            invoke("analyze", str(source), "--json", str(report), "--fail-on", "warning")[0]
            == EXIT_OK
        )
        counts = json.loads(report.read_text())["meta"]["counts"]["findings_by_severity"]
        assert counts == {"info": 1, "warning": 0, "critical": 0}

    def test_r39_both_reports_are_still_written_on_exit_one(self, tmp_path: Path) -> None:
        """R39: "both reports are still written" — a gate is not an abort."""
        report = tmp_path / "r.json"
        code, out, _ = invoke(
            "analyze", str(DUPLICATE), "--json", str(report), "--fail-on", "critical"
        )
        assert code == EXIT_FINDINGS
        assert report.is_file()
        assert json.loads(report.read_text())["meta"]["counts"]["findings"] > 0
        assert out.startswith("wrote ")

    def test_r39_exit_code_for_is_a_function_of_the_findings_and_the_threshold(self) -> None:
        """R39: the decision, driven directly at every severity."""
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage())
        trace = builder.build()
        findings = {
            severity: build_finding(
                trace=trace,
                detector="failed_tool_call",
                severity=severity,
                summary=f"{severity} case",
                span_seqs=(0,),
                agent_ids=(),
                metrics={"failures": 1},
                previews=(),
                wasted=TokenUsage(),
            )
            for severity in ("info", "warning", "critical")
        }
        assert exit_code_for([], "critical") == EXIT_OK
        assert exit_code_for([findings["critical"]], "none") == EXIT_OK
        assert exit_code_for([findings["info"]], "warning") == EXIT_OK
        assert exit_code_for([findings["warning"]], "warning") == EXIT_FINDINGS
        assert exit_code_for([findings["critical"]], "warning") == EXIT_FINDINGS
        assert exit_code_for([findings["warning"]], "critical") == EXIT_OK
        assert exit_code_for([findings["critical"]], "critical") == EXIT_FINDINGS

    def test_r39_a_malformed_line_is_exit_two_with_one_sanitized_line(self, tmp_path: Path) -> None:
        """R39/R11/AC2: one line matching the pinned shape, no traceback."""
        source = tmp_path / "bad.jsonl"
        source.write_text(
            '{"type":"user","uuid":"u","timestamp":"2026-01-01T00:00:00Z"}\n{"type":"user"\n',
            encoding="utf-8",
        )
        report = tmp_path / "r.json"
        code, out, err = invoke("analyze", str(source), "--json", str(report))
        assert code == EXIT_FAIL_CLOSED
        assert out == ""
        assert err.count("\n") == 1
        assert err.startswith("swarm-observer: ")
        assert "Traceback" not in err
        assert not report.exists()

    def test_r39_a_pre_existing_output_is_not_truncated_by_a_failed_run(
        self, tmp_path: Path
    ) -> None:
        """R11/AC2: "no output file written or truncated", with the bytes checked."""
        source = tmp_path / "bad.jsonl"
        source.write_text("not json at all\n", encoding="utf-8")
        report = tmp_path / "r.json"
        report.write_text("PRE-EXISTING\n", encoding="utf-8")
        code, _, err = invoke("analyze", str(source), "--json", str(report))
        assert code == EXIT_FAIL_CLOSED
        assert report.read_text() == "PRE-EXISTING\n"
        assert err.strip().startswith("swarm-observer: ")

    def test_r39_a_successful_run_does_replace_a_pre_existing_output(self, tmp_path: Path) -> None:
        """R11: the control arm for the assertion above."""
        report = tmp_path / "r.json"
        report.write_text("PRE-EXISTING\n", encoding="utf-8")
        assert invoke("analyze", str(CLEAN), "--json", str(report))[0] == EXIT_OK
        assert report.read_text() != "PRE-EXISTING\n"

    def test_r39_a_limit_breach_is_exit_two_naming_the_limit(self, tmp_path: Path) -> None:
        """R11/AC16: the cap is named, the content is not."""
        report = tmp_path / "r.json"
        code, _, err = invoke(
            "analyze", str(CLEAN), "--json", str(report), "--max-line-bytes", "10"
        )
        assert code == EXIT_FAIL_CLOSED
        assert "line_too_long" in err
        assert "limit 10" in err
        assert not report.exists()

    def test_r39_an_unknown_detector_is_a_usage_error(self, tmp_path: Path) -> None:
        """R39/AC16: exit 3, and no output written."""
        report = tmp_path / "r.json"
        code, out, err = invoke(
            "analyze", str(CLEAN), "--json", str(report), "--detector", "no_such_detector"
        )
        assert code == EXIT_USAGE
        assert out == ""
        assert "unknown detector: no_such_detector" in err
        assert all(slug in err for slug in DETECTOR_SLUGS)
        assert not report.exists()

    def test_r39_a_missing_output_directory_is_a_usage_error(self, tmp_path: Path) -> None:
        """R39/AC16: checked before anything is read."""
        code, out, err = invoke("analyze", str(CLEAN), "--json", str(tmp_path / "nope" / "r.json"))
        assert code == EXIT_USAGE
        assert "output directory does not exist" in err
        assert out == ""

    def test_r39_an_output_path_that_is_a_directory_is_a_usage_error(self, tmp_path: Path) -> None:
        """R39: writing a report over a directory is a mistake in the command."""
        target = tmp_path / "already"
        target.mkdir()
        code, _, err = invoke("analyze", str(CLEAN), "--json", str(target))
        assert code == EXIT_USAGE
        assert "output path is a directory" in err

    def test_r39_an_unknown_subcommand_is_a_usage_error_not_a_fail_closed_one(self) -> None:
        """A-c12: argparse's own default is 2, which would collide with R11's code."""
        code, out, err = invoke("no_such_command")
        assert code == EXIT_USAGE
        assert code != EXIT_FAIL_CLOSED
        assert out == ""
        assert "invalid choice" in err

    @pytest.mark.parametrize("flag", ["--explain"])
    def test_r39_a_deferred_flag_is_a_usage_error_naming_its_increment(
        self, tmp_path: Path, flag: str
    ) -> None:
        """A-c10: accepting a flag that does nothing is how a gap becomes a wrong report.

        ``--out`` left this parametrization in increment 4, when the HTML
        renderer arrived and the refusal became the mirror of the defect A-c10
        is about — a flag that is implemented and still refused. The arm is
        **replaced**, not deleted: the test below asserts the flag now does what
        it says, so a future regression that silently stopped writing the HTML
        report is still red.
        """
        report = tmp_path / "r.json"
        argv = ["analyze", str(CLEAN), "--json", str(report), flag]
        code, out, err = invoke(*argv)
        assert code == EXIT_USAGE
        assert f"{flag} is not available yet" in err
        assert out == ""
        assert not report.exists()

    def test_r38_out_writes_the_html_report_and_is_no_longer_refused(self, tmp_path: Path) -> None:
        """R38 (increment 4): ``--out`` is accepted, and the file it names exists.

        The replacement for the ``--out`` arm above. Deliberately narrow — it
        asserts the wiring, not the document. What the document contains is the
        tester's, and the requirements governing it are still ledgered in
        ``traceability_pending.txt`` precisely so this citation cannot be
        mistaken for coverage of them.
        """
        report = tmp_path / "r.html"
        code, out, err = invoke("analyze", str(CLEAN), "--out", str(report))
        assert (code, err) == (EXIT_OK, "")
        assert report.is_file()
        assert report.read_text(encoding="utf-8").startswith("<!doctype html>")
        assert str(report) in out
        assert "is not available yet" not in out

    def test_r39_no_output_flag_at_all_is_a_usage_error(self, tmp_path: Path) -> None:
        """A-c10 + S18: a run that reports success without writing a file is worse.

        R38 writes ``--out`` as required; the increment-3 review's ruling on S18
        is that a ``--json``-only run is a legitimate invocation for a CI job
        that gates on ``--fail-on``. What is enforced is therefore "at least one
        of the two", and both single-flag forms are asserted here so the rule is
        pinned in all three of its states.
        """
        code, out, err = invoke("analyze", str(CLEAN))
        assert code == EXIT_USAGE
        assert "--json is required" in err
        assert out == ""
        assert invoke("analyze", str(CLEAN), "--json", str(tmp_path / "a.json"))[0] == EXIT_OK
        assert invoke("analyze", str(CLEAN), "--out", str(tmp_path / "a.html"))[0] == EXIT_OK

    @pytest.mark.parametrize(
        "argv",
        [
            ["--max-records", "0"],
            ["--max-file-bytes", "-1"],
            ["--max-line-bytes", "0"],
        ],
    )
    def test_r39_a_non_positive_size_limit_is_a_usage_error(
        self, tmp_path: Path, argv: list[str]
    ) -> None:
        """R11/R39: a bad flag value is exit 3, not a fail-closed 2."""
        code, _, err = invoke("analyze", str(CLEAN), "--json", str(tmp_path / "r.json"), *argv)
        assert code == EXIT_USAGE
        assert "size limit must be a positive integer" in err

    def test_r39_an_output_path_that_is_not_a_regular_file_is_refused(self, tmp_path: Path) -> None:
        """R11/R39: the reader refuses a non-regular *input*; the writer must match.

        R11's posture is that a failure leaves the filesystem as it found it. An
        output path that is not a regular file cannot be atomically replaced
        without destroying what is there, so it belongs with the directory case
        in ``check_output_path`` — exit 3, nothing written.
        """
        target = tmp_path / "pipe"
        os.mkfifo(target)
        code, _, _ = invoke("analyze", str(CLEAN), "--json", str(target))
        assert code == EXIT_USAGE
        assert target.is_fifo()

    def test_r39_the_refusal_writes_nothing_and_says_nothing_on_stdout(
        self, tmp_path: Path
    ) -> None:
        """R11 (BUG-5, fixed in review): nothing written, nothing claimed.

        Replaces the reproduction that pinned the destroyed FIFO. "Exit 3" alone
        would be satisfied by a run that refused *after* replacing the node, so
        the surviving node and the empty stdout are asserted with it.
        """
        target = tmp_path / "pipe"
        os.mkfifo(target)
        code, out, err = invoke("analyze", str(CLEAN), "--json", str(target))
        assert (code, out) == (EXIT_USAGE, "")
        assert "not a regular file" in err
        assert target.is_fifo()

    def test_r39_a_socket_and_a_symlink_to_one_are_refused_too(self, tmp_path: Path) -> None:
        """R11: the guard is over "not a regular file", not over one node type.

        A FIFO is the cheap reproduction; a socket and a symlink pointing at one
        are the same condition reached differently, and a guard written against
        ``is_fifo()`` would pass the first and fail these — the "correct for the
        call path that exists" shape this project keeps re-finding.
        """
        sock = tmp_path / "sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(sock))
            link = tmp_path / "link"
            link.symlink_to(sock)
            for target in (sock, link):
                code, out, _ = invoke("analyze", str(CLEAN), "--json", str(target))
                assert (code, out) == (EXIT_USAGE, "")
            assert sock.is_socket()
            assert link.is_symlink()

    def test_r39_an_existing_regular_file_and_a_symlink_to_one_are_still_accepted(
        self, tmp_path: Path
    ) -> None:
        """R11: the non-vacuous arm — overwriting a report is the normal case.

        Without this, the guard above is satisfied by refusing every path that
        already exists, which would break re-running ``analyze`` over the same
        output — the single most common invocation there is.
        """
        report = tmp_path / "r.json"
        report.write_text("PRE-EXISTING\n")
        link = tmp_path / "link.json"
        link.symlink_to(report)
        for target in (report, link):
            target.write_text("PRE-EXISTING\n") if not target.is_symlink() else None
            code, _, err = invoke("analyze", str(CLEAN), "--json", str(target))
            assert (code, err) == (EXIT_OK, "")
            assert json.loads(target.read_text())["meta"]["adapter"] == DEFAULT_ADAPTER

    def test_r39_a_negative_blocked_gap_is_a_usage_error(self, tmp_path: Path) -> None:
        """R25/R39: the one tunable, validated at the boundary."""
        code, _, err = invoke(
            "analyze",
            str(CLEAN),
            "--json",
            str(tmp_path / "r.json"),
            "--blocked-gap-seconds",
            "-1",
        )
        assert code == EXIT_USAGE
        assert "non-negative integer" in err

    def test_r39_no_traceback_ever_reaches_stderr(self, tmp_path: Path) -> None:
        """R11: across every failing invocation this module knows how to produce."""
        cases = [
            ["analyze", str(CLEAN), "--json", str(tmp_path / "nope" / "r.json")],
            ["analyze", str(CLEAN), "--json", str(tmp_path / "r.json"), "--detector", "x"],
            ["analyze", str(tmp_path / "missing.jsonl"), "--json", str(tmp_path / "r.json")],
            ["no_such_command"],
        ]
        for argv in cases:
            _, _, err = invoke(*argv)
            assert "Traceback" not in err, argv
            assert '  File "' not in err, argv

    def test_r39_run_raises_the_typed_error_instead_of_returning_a_number(
        self, tmp_path: Path
    ) -> None:
        """R39: ``run()`` is the seam for asserting *which* error a condition produces."""
        with pytest.raises(UsageError):
            run(["analyze", str(CLEAN)], stdout=io.StringIO())


class TestStdoutDisciplineR40:
    """R40: one line, a deterministic function of the trace, the flags and the paths."""

    def test_r40_success_writes_exactly_one_line_naming_paths_and_counts(
        self, tmp_path: Path
    ) -> None:
        """R40: the pinned shape."""
        report = tmp_path / "r.json"
        code, out, err = invoke("analyze", str(DUPLICATE), "--json", str(report))
        assert code == EXIT_OK
        assert err == ""
        assert out.count("\n") == 1
        assert out == f"wrote {report.as_posix()}; findings: critical=1 warning=1 info=0\n"

    def test_r40_the_counts_match_the_document(self, tmp_path: Path) -> None:
        """R40: the line and the header cannot disagree."""
        report = tmp_path / "r.json"
        _, out, _ = invoke("analyze", str(DUPLICATE), "--json", str(report))
        counts = json.loads(report.read_text())["meta"]["counts"]["findings_by_severity"]
        for name in ("critical", "warning", "info"):
            assert f"{name}={counts[name]}" in out

    def test_r40_the_path_is_named_as_given_not_resolved(self, tmp_path: Path) -> None:
        """A-c11: a resolved path carries the working directory and the username."""
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            write_jsonl(Path("t.jsonl"), [record()])
            _, out, _ = invoke("analyze", "t.jsonl", "--json", "r.json")
            assert out.startswith("wrote r.json;")
            assert str(tmp_path) not in out
        finally:
            os.chdir(cwd)

    def test_r40_summary_line_is_a_function_of_its_arguments(self) -> None:
        """R40: no clock, no colour, no duration, no progress."""
        line = summary_line([Path("a.json")], [])
        assert line == "wrote a.json; findings: critical=0 warning=0 info=0\n"
        assert summary_line([Path("a.json"), Path("b.json")], []) == (
            "wrote a.json, b.json; findings: critical=0 warning=0 info=0\n"
        )

    def test_r40_nothing_but_that_line_reaches_stdout(self, tmp_path: Path) -> None:
        """R40: no progress bars, no spinners, no banner."""
        report = tmp_path / "r.json"
        _, out, _ = invoke("analyze", str(HOSTILE), "--json", str(report))
        assert len(out.splitlines()) == 1

    def test_r40_diagnostics_go_to_stderr_and_never_to_stdout(self, tmp_path: Path) -> None:
        """R40: the two streams are separated in every failing case."""
        for argv in (
            ["analyze", str(CLEAN), "--json", str(tmp_path / "nope" / "r.json")],
            ["analyze", str(tmp_path / "missing.jsonl"), "--json", str(tmp_path / "r.json")],
        ):
            _, out, err = invoke(*argv)
            assert out == ""
            assert err

    def test_r40_stdout_is_identical_across_the_environment_matrix(self, tmp_path: Path) -> None:
        """R40: hash seed, timezone and locale, in real subprocesses.

        The report goes to a temporary file rather than to ``os.devnull``. That
        looked like the tidy choice and is not: ``atomic_write_texts`` renames
        over its destination, so naming a character device destroys it — see
        BUG-5, which this test found by doing it.
        """
        with_json = ["analyze", str(DUPLICATE), "--json", str(tmp_path / "r.json")]
        digests = set()
        for environment in (*DETERMINISM_ENVIRONMENTS, {}):
            result = run_cli(with_json, environment=environment)
            assert result.returncode == EXIT_OK, (environment, result.stderr)
            digests.add(sha256_text(result.stdout))
        assert len(digests) == 1

    def test_r40_the_report_bytes_are_identical_across_the_environment_matrix(
        self, tmp_path: Path
    ) -> None:
        """R36/R40: the document itself, in real subprocesses under foreign TZ and locale."""
        digests = set()
        for index, environment in enumerate((*DETERMINISM_ENVIRONMENTS, {})):
            report = tmp_path / f"r{index}.json"
            result = run_cli(
                ["analyze", str(DUPLICATE), "--json", str(report)], environment=environment
            )
            assert result.returncode == EXIT_OK, (environment, result.stderr)
            digests.add(sha256_text(report.read_text()))
        assert len(digests) == 1

    def test_r40_the_report_does_not_depend_on_the_working_directory_or_path_order(
        self, tmp_path: Path
    ) -> None:
        """R38/R40: two copies of the same content, two directories, reversed order."""
        first = tmp_path / "one"
        second = tmp_path / "two"
        for directory in (first, second):
            directory.mkdir()
            for name in ("clean_single_agent.jsonl", "duplicate_tool_call.jsonl"):
                (directory / name).write_bytes((FIXTURE_DIR / name).read_bytes())
        forward = tmp_path / "forward.json"
        backward = tmp_path / "backward.json"
        run_cli(
            [
                "analyze",
                "clean_single_agent.jsonl",
                "duplicate_tool_call.jsonl",
                "--json",
                str(forward),
            ],
            cwd=first,
        )
        run_cli(
            [
                "analyze",
                "duplicate_tool_call.jsonl",
                "clean_single_agent.jsonl",
                "--json",
                str(backward),
            ],
            cwd=second,
        )
        assert forward.read_text() == backward.read_text()
        assert str(tmp_path) not in forward.read_text()


class TestPipelineWiringR38:
    """R38: ingest → detect → cost → render, with the seams asserted individually."""

    def test_r38_build_limits_applies_only_the_flags_given(self) -> None:
        """R11/R38: three caps are exposed and the rest keep their defaults."""
        parser = build_parser()
        args = parser.parse_args(["analyze", "x", "--json", "y", "--max-records", "5"])
        limits = build_limits(args)
        assert limits.max_records == 5
        assert limits.max_files == IngestLimits().max_files
        assert limits.max_file_bytes == IngestLimits().max_file_bytes

    def test_r38_build_config_rejects_an_unknown_slug_before_reading_anything(self) -> None:
        """R39: a usage error, raised from the config builder."""
        parser = build_parser()
        args = parser.parse_args(
            ["analyze", "x", "--json", "y", "--detector", "nope", "--detector", "agent_loop"]
        )
        with pytest.raises(UsageError) as caught:
            build_config(args)
        assert "unknown detector: nope" in caught.value.message

    def test_r38_check_output_path_accepts_a_bare_filename(self, tmp_path: Path) -> None:
        """R39: ``r.json`` has an implicit parent of ``.``, which exists."""
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            assert check_output_path("r.json") == Path("r.json")
        finally:
            os.chdir(cwd)

    def test_r38_the_findings_carry_the_priced_waste_the_cost_engine_computed(
        self, tmp_path: Path
    ) -> None:
        """R14/R29: one number, computed once, read by the line and the document."""
        report = tmp_path / "r.json"
        invoke("analyze", str(DUPLICATE), "--json", str(report))
        document = json.loads(report.read_text())
        priced = {item["finding_id"]: item["wasted_cost_usd"] for item in document["findings"]}
        assert priced
        for finding in document["findings"]:
            if finding["wasted"]["total_tokens"] == 0:
                assert finding["wasted_cost_usd"] == "0.000000"

    def test_r39_the_four_exit_codes_are_the_literal_numbers_r39_pins(self) -> None:
        """R39 (mutation M02): asserted as literals, not read back from the module.

        Every other exit-code test in this file compares against ``EXIT_*``, so
        moving a constant moves the assertion with it — a self-referential check.
        A script wrapping this tool reads the numbers, not the names.
        """
        assert (EXIT_OK, EXIT_FINDINGS, EXIT_FAIL_CLOSED, EXIT_USAGE) == (0, 1, 2, 3)

    def test_r39_a_findings_run_exits_with_the_literal_one(self, tmp_path: Path) -> None:
        """R39 (M02): the number a CI job branches on."""
        report = tmp_path / "r.json"
        code, _, _ = invoke(
            "analyze", str(DUPLICATE), "--json", str(report), "--fail-on", "critical"
        )
        assert code == 1

    def test_r38_no_previews_is_plumbed_to_the_adapter_and_not_only_to_the_renderer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A10 (mutation M22): the flag must reach *ingestion*.

        A10 chose ingest-time blanking so the hostile bytes "do not exist in the
        process after ingestion", rather than existing and being trusted not to
        leak. Since A-c6 added the render-boundary guard, the two produce the
        same report — so dropping the ingest half changes no output byte and
        nothing in the suite could see it. What is left to assert is the wiring
        itself, which is the only observable form the security property has.
        """
        seen: list[bool] = []
        real = cli_main.build_adapter

        def recording(slug: str, **kwargs: Any) -> Any:
            seen.append(bool(kwargs.get("no_previews")))
            return real(slug, **kwargs)

        monkeypatch.setattr(cli_main, "build_adapter", recording)
        invoke("analyze", str(HOSTILE), "--json", str(tmp_path / "a.json"), "--no-previews")
        invoke("analyze", str(HOSTILE), "--json", str(tmp_path / "b.json"))
        assert seen == [True, False]

    def test_r38_the_adapter_really_blanks_previews_on_the_model(self, tmp_path: Path) -> None:
        """A10 (M22): the other half — the ``Trace`` itself carries no free text."""
        blanked = build_adapter(DEFAULT_ADAPTER, no_previews=True).load((HOSTILE,), IngestLimits())
        kept = build_adapter(DEFAULT_ADAPTER, no_previews=False).load((HOSTILE,), IngestLimits())
        assert all(span.text_preview == "" for span in blanked.spans)
        assert all(span.tool_input_preview == "" for span in blanked.spans)
        assert all(span.tool_result_preview == "" for span in blanked.spans)
        assert any(span.tool_input_preview for span in kept.spans), "vacuous: no previews at all"

    def test_r17_a_findings_priced_waste_reaches_the_document_as_a_positive_number(
        self, tmp_path: Path
    ) -> None:
        """R17/R29 (mutation G02): the waste map must survive the pipeline.

        Replacing the attribution with an empty tuple in
        ``run_detectors_with_waste`` turns every ``wasted_cost_usd`` into
        ``0.000000`` and every per-detector waste into zero, and left the suite
        green: nothing asserted that *some* finding has a non-zero attributed
        cost end to end.
        """
        report = tmp_path / "r.json"
        assert invoke("analyze", str(DUPLICATE), "--json", str(report))[0] == EXIT_OK
        document = json.loads(report.read_text())
        costs = [
            Decimal(finding["wasted_cost_usd"])
            for finding in document["findings"]
            if finding["wasted_cost_usd"] is not None
        ]
        assert costs, "no finding carried a priced waste at all"
        assert max(costs) > 0
        assert any(finding["wasted"]["total_tokens"] > 0 for finding in document["findings"])
        waste_rows = document["cost"]["by_detector"]
        assert waste_rows
        assert any(Decimal(row["wasted_cost_usd"]) > 0 for row in waste_rows)
        assert any(row["wasted"]["total_tokens"] > 0 for row in waste_rows)

    def test_r38_the_report_names_the_tool_version_and_the_rate_snapshot(
        self, tmp_path: Path
    ) -> None:
        """R31/R36: provenance the reader needs beside every dollar figure."""
        report = tmp_path / "r.json"
        invoke("analyze", str(CLEAN), "--json", str(report))
        document = json.loads(report.read_text())
        assert document["meta"]["tool"]["version"] == __version__
        assert document["cost"]["snapshot"]["version"] == SHIPPED.meta.version
        assert document["cost"]["snapshot"]["snapshot_date"] == SHIPPED.meta.snapshot_date

    def test_r38_the_adapter_choice_is_the_registry(self, tmp_path: Path) -> None:
        """R3/R38: an unknown adapter is refused by argparse's own choices."""
        code, _, err = invoke(
            "analyze", str(CLEAN), "--json", str(tmp_path / "r.json"), "--adapter", "otel"
        )
        assert code == EXIT_USAGE
        assert "invalid choice" in err


class TestAcceptanceCriterion7EndToEndR28R29R30R31:
    """AC7 through the command line, with the total recomputed from the snapshot."""

    def ac7_file(self, tmp_path: Path) -> Path:
        """AC7's five model calls as a real transcript."""
        return write_jsonl(
            tmp_path / "ac7.jsonl",
            [
                record(uuid="u-0", message={"content": "go", "role": "user"}),
                assistant(
                    "a-1",
                    "2026-03-02T09:00:01.000Z",
                    {
                        "input_tokens": 10,
                        "output_tokens": 483,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 17971,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 17971,
                            "ephemeral_1h_input_tokens": 0,
                        },
                    },
                    "claude-sonnet-4-5-20250929",
                ),
                assistant(
                    "a-2",
                    "2026-03-02T09:00:02.000Z",
                    {
                        "input_tokens": 1000,
                        "output_tokens": 7,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "claude-haiku-4-5-20251001",
                ),
                assistant(
                    "a-3",
                    "2026-03-02T09:00:03.000Z",
                    {
                        "input_tokens": 5,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "some-model-nobody-published",
                ),
                record(
                    type="assistant",
                    uuid="a-4",
                    requestId="req-a-4",
                    timestamp="2026-03-02T09:00:04.000Z",
                    isApiErrorMessage=True,
                    error="rate_limit_error",
                    apiErrorStatus="429 Too Many Requests",
                    message={
                        "content": [{"text": "error", "type": "text"}],
                        "id": "msg-a-4",
                        "model": "<synthetic>",
                        "role": "assistant",
                    },
                ),
                record(
                    type="assistant",
                    uuid="a-5",
                    requestId="req-a-5",
                    timestamp="2026-03-02T09:00:05.000Z",
                    message={
                        "content": [{"text": "no usage", "type": "text"}],
                        "id": "msg-a-5",
                        "model": "claude-sonnet-4-5-20250929",
                        "role": "assistant",
                    },
                ),
            ],
        )

    def test_ac7_the_five_spans_reach_their_five_outcomes_through_the_cli(
        self, tmp_path: Path
    ) -> None:
        """AC7: two priced, three unpriced with three distinct reasons."""
        report = tmp_path / "r.json"
        code, _, err = invoke("analyze", str(self.ac7_file(tmp_path)), "--json", str(report))
        assert code == EXIT_OK, err
        cost = json.loads(report.read_text())["cost"]
        assert {row["model_key"] for row in cost["spans"]} == {
            "claude-sonnet-4-5",
            "claude-haiku-4-5",
        }
        assert sorted(row["reason"] for row in cost["unpriced"]) == [
            "model_not_in_snapshot",
            "synthetic_span",
            "usage_missing",
        ]

    def test_ac7_the_total_equals_the_sum_of_the_two_quantized_span_costs(
        self, tmp_path: Path
    ) -> None:
        """AC7: recomputed from the snapshot's rates, never from the printed total."""
        report = tmp_path / "r.json"
        invoke("analyze", str(self.ac7_file(tmp_path)), "--json", str(report))
        cost = json.loads(report.read_text())["cost"]
        rows = sum(Decimal(row["cost_usd"]) for row in cost["spans"])
        assert cost["total"]["cost_usd"] == format_usd(rows)

        expected = Decimal(0)
        for row in cost["spans"]:
            usage = row["usage"]
            terms = Decimal(0)
            for component, price_key in (
                ("input_tokens", "input"),
                ("output_tokens", "output"),
                ("cache_read_input_tokens", "cache_read"),
                ("cache_creation_5m_tokens", "cache_write_5m"),
                ("cache_creation_1h_tokens", "cache_write_1h"),
            ):
                rate = SHIPPED.get_rate(row["model_key"], price_key)
                assert rate is not None
                terms += Decimal(usage[component]) * rate
            expected += Decimal(format_usd(terms / Decimal(1_000_000)))
        assert Decimal(cost["total"]["cost_usd"]) == expected
        assert expected > 0

    def test_ac7_the_four_groupings_each_sum_to_the_total(self, tmp_path: Path) -> None:
        """AC5/R31: asserted against the emitted document, not the in-memory report."""
        report = tmp_path / "r.json"
        invoke("analyze", str(self.ac7_file(tmp_path)), "--json", str(report))
        cost = json.loads(report.read_text())["cost"]
        total = Decimal(cost["total"]["cost_usd"])
        for group in ("by_agent", "by_model", "spans"):
            assert sum(Decimal(row["cost_usd"]) for row in cost[group]) == total, group

    def test_ac7_every_model_call_appears_priced_or_unpriced(self, tmp_path: Path) -> None:
        """AC5/R30: nothing is silently omitted from the emitted cost section."""
        report = tmp_path / "r.json"
        invoke("analyze", str(self.ac7_file(tmp_path)), "--json", str(report))
        document = json.loads(report.read_text())
        model_calls = {span["seq"] for span in document["spans"] if span["kind"] == "model_call"}
        priced = {row["seq"] for row in document["cost"]["spans"]}
        unpriced = {row["seq"] for row in document["cost"]["unpriced"]}
        assert priced | unpriced == model_calls
        assert priced & unpriced == set()


class TestMultiModelOrderingR31R47:
    """R31/R47: ``by_model`` is ordered by key, and no fixture could show it.

    Found by the review's own mutation wave. Dropping the ``sorted()`` from
    ``_by_model`` — leaving ``list({...})`` over a set of model keys — survived
    the whole suite, and the reason is the tester's own "collection of one"
    pattern one level up: **no fixture in the corpus resolves more than one
    model key**, so the R47 determinism matrix, which is what would otherwise
    catch a set-iteration dependency, never had a multi-model trace to run on.
    ``max(len(report.by_model))`` over all 26 fixtures is 1.

    The assertion has to run in a **subprocess with a fixed** ``PYTHONHASHSEED``.
    In-process it would depend on the seed the test session happens to have, and
    a test that kills a mutant for some seeds is a test that reports green for
    the others — which is this project's signature defect with a random number
    attached.
    """

    KEYS = (
        "claude-haiku-4-5",
        "claude-opus-5",
        "claude-sonnet-4-5",
        "claude-sonnet-5",
        "claude-opus-4-1",
        "claude-fable-5",
    )

    def multi_model_file(self, tmp_path: Path) -> Path:
        """One priced ``model_call`` per snapshot key, in reverse-sorted order."""
        records: list[dict[str, Any]] = [
            record(uuid="u-0", message={"content": "go", "role": "user"})
        ]
        for index, key in enumerate(sorted(self.KEYS, reverse=True)):
            records.append(
                assistant(
                    f"a-{index}",
                    f"2026-03-02T09:00:0{index}.000Z",
                    {
                        "input_tokens": 100 + index,
                        "output_tokens": 10,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    f"{key}-20260101",
                )
            )
        return write_jsonl(tmp_path / "multi.jsonl", records)

    @pytest.mark.parametrize("seed", ["0", "1", "2"])
    def test_r31_by_model_is_ordered_by_key_under_a_fixed_hash_seed(
        self, tmp_path: Path, seed: str
    ) -> None:
        """R31: "per resolved ``model_key`` (ordered by key)", with six of them."""
        report = tmp_path / f"r{seed}.json"
        result = run_cli(
            ["analyze", str(self.multi_model_file(tmp_path)), "--json", str(report)],
            environment={"PYTHONHASHSEED": seed},
        )
        assert result.returncode == EXIT_OK, result.stderr
        keys = [row["model_key"] for row in json.loads(report.read_text())["cost"]["by_model"]]
        assert keys == sorted(self.KEYS)
        assert len(keys) == len(self.KEYS)

    def test_r47_a_multi_model_trace_is_byte_identical_across_hash_seeds(
        self, tmp_path: Path
    ) -> None:
        """R47: the clause the corpus could not exercise — a set that has to be sorted.

        R47 forbids "any ``set``/``dict`` iteration that is not explicitly
        sorted". With one model key per fixture that clause was unfalsifiable
        for the cost section; with six it is the whole point.
        """
        source = self.multi_model_file(tmp_path)
        digests = set()
        for seed in ("0", "1", "2", "random"):
            report = tmp_path / f"d{seed}.json"
            result = run_cli(
                ["analyze", str(source), "--json", str(report)],
                environment={"PYTHONHASHSEED": seed},
            )
            assert result.returncode == EXIT_OK, result.stderr
            digests.add(sha256_text(report.read_text()))
        assert len(digests) == 1

    def test_r31_the_six_models_really_do_resolve_and_price(self, tmp_path: Path) -> None:
        """R31: the premise — six *distinct* priced keys, not one row six times.

        Without this, both tests above are satisfied by a trace whose spans all
        resolve to one key, which is the state the corpus was already in.
        """
        report = tmp_path / "r.json"
        assert invoke("analyze", str(self.multi_model_file(tmp_path)), "--json", str(report))[
            0
        ] == (EXIT_OK)
        cost = json.loads(report.read_text())["cost"]
        assert cost["unpriced"] == []
        assert len({row["model_key"] for row in cost["by_model"]}) == len(self.KEYS)
        assert Decimal(cost["total"]["cost_usd"]) > 0


class TestFailClosedPricingR39:
    """R39/R11: a pricing failure is one sanitized line and exit 2, like any other."""

    def huge_usage_file(self, tmp_path: Path, tokens: int, output: int) -> Path:
        """A transcript with an absurd but syntactically valid token count."""
        return write_jsonl(
            tmp_path / "huge.jsonl",
            [
                record(uuid="u-0", message={"content": "go", "role": "user"}),
                assistant(
                    "a-1",
                    "2026-03-02T09:00:01.000Z",
                    {
                        "input_tokens": tokens,
                        "output_tokens": output,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "claude-sonnet-4-5-20250929",
                ),
            ],
        )

    def test_r39_a_token_count_beyond_the_precision_exits_two(self, tmp_path: Path) -> None:
        """R29/R39: the guard the coder shipped, seen through the CLI."""
        source = self.huge_usage_file(tmp_path, 10**60, 1)
        report = tmp_path / "r.json"
        code, out, err = invoke("analyze", str(source), "--json", str(report))
        assert code == EXIT_FAIL_CLOSED
        assert out == ""
        assert err == "swarm-observer: cost_precision_exceeded: span 1\n"
        assert not report.exists()

    def test_r39_the_same_trace_with_one_component_also_exits_two(self, tmp_path: Path) -> None:
        """R11/R39: every fail-closed condition is exit 2 and one sanitized line."""
        source = self.huge_usage_file(tmp_path, 10**60, 0)
        report = tmp_path / "r.json"
        code, out, err = invoke("analyze", str(source), "--json", str(report))
        assert code == EXIT_FAIL_CLOSED
        assert out == ""
        assert "Traceback" not in err
        assert not report.exists()

    def test_r39_the_one_component_trace_names_the_span_like_the_two_component_one(
        self, tmp_path: Path
    ) -> None:
        """R11/R39 (BUG-1, fixed in review): both forms produce the same line.

        Replaces the reproduction that pinned the uncaught ``InvalidOperation``.
        The two-component form was already correct, which is exactly why the
        one-component form went unnoticed — so the assertion is that the two are
        now indistinguishable on stderr, not merely that each is non-zero.
        """
        one = invoke("analyze", str(self.huge_usage_file(tmp_path, 10**60, 0)), "--json", "a.json")
        two = invoke("analyze", str(self.huge_usage_file(tmp_path, 10**60, 1)), "--json", "b.json")
        assert one == two
        assert one == (EXIT_FAIL_CLOSED, "", "swarm-observer: cost_precision_exceeded: span 1\n")

    def test_r39_an_ordinary_large_trace_still_prices(self, tmp_path: Path) -> None:
        """R29: the non-vacuous arm — a trillion tokens is priced, not refused."""
        source = self.huge_usage_file(tmp_path, 10**12, 10**12)
        report = tmp_path / "r.json"
        code, _, err = invoke("analyze", str(source), "--json", str(report))
        assert code == EXIT_OK, err
        assert Decimal(json.loads(report.read_text())["cost"]["total"]["cost_usd"]) > 0


class TestWaveTwoGapsR38R39R40:
    """CLI surfaces the second mutation sweep found nothing asserting.

    Each of these survived because the suite only ever drove the *one-item*
    case: one deferred flag at a time, one unknown detector slug, one written
    path, one error message that happened not to end in a newline. A collection
    of one cannot distinguish a sorted walk from an arbitrary one, or a
    first element from a last.
    """

    def test_r38_the_detectors_listing_ends_with_a_newline(self) -> None:
        """R38/R47 (mutation W-M02): the last line is terminated like the others.

        ``"\\n".join`` without the trailing terminator makes the final row the
        one line in the output a shell, a pager or a ``read`` loop treats
        differently, and no assertion in the suite compared the whole bytes.
        """
        document = detectors_document()
        assert document.endswith("\n")
        assert not document.endswith("\n\n")
        code, out, err = invoke("detectors")
        assert (code, err) == (EXIT_OK, "")
        assert out == document
        assert len(out.splitlines()) == len(DETECTOR_SLUGS)

    def test_r39_two_deferred_flags_together_report_the_first_by_flag_name(self) -> None:
        """R39 (mutation W-M08): a stable choice when both are given.

        ``--out`` and ``--explain`` are both refused, and with both on the
        command line exactly one message is printed. Which one must be a
        function of the flags rather than of dict insertion order — R47's rule
        applied to stderr. The walk is sorted by attribute name, so ``explain``
        precedes ``out``.
        """
        code, out, err = invoke(
            "analyze", str(CLEAN), "--json", "k.json", "--out", "r.html", "--explain"
        )
        assert code == EXIT_USAGE
        assert out == ""
        assert err.count("\n") == 1
        assert "--explain" in err
        assert "--out" not in err
        # And the same answer with the flags typed the other way round, which is
        # what makes this a property of the walk and not of argv.
        assert (
            invoke("analyze", str(CLEAN), "--json", "k.json", "--explain", "--out", "r.html")[2]
            == err
        )

    def test_r39_two_unknown_detectors_are_reported_by_the_first_in_sort_order(self) -> None:
        """R39 (mutation W-M06): ``unknown[0]``, with two unknown slugs.

        With one unknown slug ``unknown[0]`` and ``unknown[-1]`` are the same
        string. Two, given in reverse sorted order on the command line, is the
        smallest input that can tell them apart — and pins that the message is
        a function of the *set* of bad slugs rather than of the order typed.
        """
        code, _, err = invoke(
            "analyze",
            str(CLEAN),
            "--json",
            "k.json",
            "--detector",
            "zzz_not_a_detector",
            "--detector",
            "aaa_not_a_detector",
        )
        assert code == EXIT_USAGE
        assert "aaa_not_a_detector" in err
        assert "zzz_not_a_detector" not in err
        reversed_argv = invoke(
            "analyze",
            str(CLEAN),
            "--json",
            "k.json",
            "--detector",
            "aaa_not_a_detector",
            "--detector",
            "zzz_not_a_detector",
        )
        assert reversed_argv[2] == err

    def test_r39_main_renders_any_usage_error_as_exactly_one_terminated_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R39 (mutation W-M12): the ``rstrip`` in ``main``, which has no live caller.

        ``main`` strips a trailing newline off a ``UsageError``'s message before
        adding its own. Every ``UsageError`` this package constructs today ends
        without one — including argparse's, because ``_Parser.error`` appends
        ``": error: <message>"`` after the usage block — so the strip is dead on
        every existing path and the mutant survived a 79-mutant sweep.

        That makes it a guard whose only caller is hypothetical, which this
        project has shipped before (the increment-2 review's "measurement path
        with no caller"). It is asserted here at ``main``'s own seam, which is
        where the promise lives: *any* ``UsageError``, however its message is
        punctuated, renders as one terminated line and no more.
        """

        def raising(argv: Any = None, *, stdout: Any = None) -> int:
            raise UsageError("swarm-observer: error: something went wrong\n")

        monkeypatch.setattr(cli_main, "run", raising)
        err = io.StringIO()
        code = main(["detectors"], stdout=io.StringIO(), stderr=err)
        assert code == EXIT_USAGE
        assert err.getvalue() == "swarm-observer: error: something went wrong\n"

    def test_r39_a_usage_error_ends_in_exactly_one_newline_on_every_live_path(self) -> None:
        """R39: "argparse-style message on stderr", for both kinds of message.

        R39 reserves the *one sanitized line* rule for exit 2. Exit 3 is
        argparse-style, which for a bad choice is the usage block plus a final
        ``prog: error: …`` line. What must hold for both is the same: stderr
        ends in exactly one newline, stdout is untouched, and no traceback
        reaches either. The two invocations differ in who wrote the message —
        this package for the first, argparse for the second.
        """
        ours = invoke("analyze", str(CLEAN), "--json", "k.json", "--max-records", "0")
        theirs = invoke("analyze", str(CLEAN), "--json", "k.json", "--adapter", "no_such_adapter")
        assert ours[2].count("\n") == 1, repr(ours[2])
        assert theirs[2].count("\n") > 1, "vacuous: argparse no longer emits its usage block"
        for code, out, err in (ours, theirs):
            assert code == EXIT_USAGE
            assert out == ""
            assert err.endswith("\n")
            assert not err.endswith("\n\n")
            assert "Traceback" not in err
            assert err.rstrip("\n").splitlines()[-1].startswith("swarm-observer")


class TestSummaryLineSanitationR40:
    """R40 (BUG-6): the one line ``analyze`` writes to stdout, for hostile paths.

    R40 pins ``analyze``'s stdout as **one line** naming the written paths and
    the counts. ``cli/main._one_line_safe`` exists to neutralize the ASCII
    control characters that reprogram a terminal, and every ``UsageError`` and
    every fail-closed line on *stderr* goes through it. :func:`summary_line`
    does not, so the success path — the one a CI job parses and a human reads —
    is the only output in this package that carries a raw path through.

    The path is one the user typed, which is why this is a low-severity bug
    rather than an injection. It is still a broken pinned contract: a newline in
    the path makes stdout two lines, and ``read -r line`` in a wrapper script
    then reports a run that wrote nothing.
    """

    @staticmethod
    def _run(tmp_path: Path, filename: str) -> tuple[int, str, str]:
        return invoke("analyze", str(CLEAN), "--json", str(tmp_path / filename))

    def test_r40_the_control_safe_helper_is_the_one_stderr_already_uses(self) -> None:
        """R40/R11: the premise — the package already owns the fix.

        Asserted so the bug report cannot be answered with "there is no
        sanitizer": there is, it is applied on the error paths, and the success
        path is the one place it is not.
        """
        assert cli_main._one_line_safe("a\x1bb\x07c") == "a b c"
        assert cli_main._one_line_safe("keep\nthe newline") == "keep\nthe newline"
        _, _, err = invoke("analyze", str(CLEAN), "--json", "k.json", "--detector", "x\x1by")
        assert "\x1b" not in err

    @pytest.mark.parametrize("control", ["\x1b", "\x07", "\r", "\x0b"])
    def test_r40_a_control_character_in_the_output_path_is_neutralized(
        self, tmp_path: Path, control: str
    ) -> None:
        """R40/R11: stdout carries no character that reprograms a terminal.

        NUL is excluded here and covered by BUG-7 below: it never reaches this
        line because it raises out of ``check_output_path`` first, which is a
        different defect with a different exit code.
        """
        code, out, _ = self._run(tmp_path, f"r{control}x.json")
        assert code == EXIT_OK
        assert control not in out

    def test_r40_stdout_is_one_line_whatever_the_output_path_contains(self, tmp_path: Path) -> None:
        """R40: "writes nothing to stdout on success except one line"."""
        code, out, _ = self._run(tmp_path, "two\nlines.json")
        assert code == EXIT_OK
        assert out.count("\n") == 1

    def test_r40_the_forged_second_summary_line_cannot_be_produced(self, tmp_path: Path) -> None:
        """R40 (BUG-6, fixed in review): the misreport, driven end to end.

        Replaces the reproduction that pinned the two-line stdout. The forged
        text is chosen to read as a summary line of its own reporting a run that
        wrote nothing, so a wrapper reading the *last* line of stdout was told a
        different story from one reading the first. Both readings must now agree.
        """
        forged = "b\nwrote nothing; findings: critical=0 warning=0 info=0.json"
        code, out, _ = self._run(tmp_path, forged)
        assert code == EXIT_OK
        lines = out.splitlines()
        assert len(lines) == 1
        assert lines[0] == lines[-1]
        assert lines[0].startswith("wrote ")
        assert lines[0].endswith("; findings: critical=0 warning=0 info=0")

    def test_r40_every_c0_control_and_del_is_neutralized_on_stdout(self, tmp_path: Path) -> None:
        """R40/R11: the whole C0 range plus DEL, not the four the report named.

        A guard written against the reported characters is the defect this
        project keeps shipping, so every code point ``_one_line_safe`` claims is
        driven. NUL is excluded: it is refused earlier as a usage error (BUG-7)
        and never reaches this line.
        """
        for point in [*range(0x01, 0x20), 0x7F]:
            code, out, _ = self._run(tmp_path, f"r{chr(point)}x.json")
            assert code == EXIT_OK, hex(point)
            # The line's own terminator is the one newline allowed, so the
            # assertion is over the line's body rather than over the whole
            # stream — otherwise 0x0A could never be driven at all.
            assert out.endswith("\n"), hex(point)
            body = out[:-1]
            assert chr(point) not in body, hex(point)
            assert "\n" not in body, hex(point)

    def test_r40_the_report_is_written_at_the_path_the_user_actually_typed(
        self, tmp_path: Path
    ) -> None:
        """R40: sanitation is a property of the *line*, not of the filename.

        The arm that keeps the fix honest: neutralizing the control character in
        the path itself — rather than in the line naming it — would pass every
        assertion above while writing the report somewhere the user did not ask
        for. The file has to be at the raw path.
        """
        target = tmp_path / "r\x1bx.json"
        code, out, err = invoke("analyze", str(CLEAN), "--json", str(target))
        assert (code, err) == (EXIT_OK, "")
        assert target.is_file()
        assert "\x1b" not in out

    def test_r40_stdout_and_the_document_report_the_same_counts(self, tmp_path: Path) -> None:
        """R40/R36: the two callers of ``severity_counts`` must agree.

        Added by review (comment C5). ``summary_line`` and ``report_document``
        each call ``severity_counts`` independently, so a future change to one
        call site — a different findings sequence, a filter applied on one path
        — would put two different tallies in front of a reader with nothing
        comparing them. R40 pins stdout as a function of the findings; this is
        the assertion that it is a function of the *same* findings the document
        describes.
        """
        report = tmp_path / "r.json"
        source = FIXTURE_DIR / "duplicate_tool_call.jsonl"
        _, out, _ = invoke("analyze", str(source), "--json", str(report))
        document = json.loads(report.read_text())["meta"]["counts"]["findings_by_severity"]
        tallies = out.strip().split("findings: ", 1)[1]
        assert tallies == " ".join(
            f"{name}={document[name]}" for name in ("critical", "warning", "info")
        )
        # Non-vacuous: at least one severity is non-zero, so the comparison is
        # not two rows of zeros agreeing with each other.
        assert sum(document.values()) > 0

    def test_r40_an_ordinary_path_produces_exactly_one_clean_line(self, tmp_path: Path) -> None:
        """R40: the non-vacuous arm — the ordinary case is already correct, so
        the failures above are about the hostile path and not about the line."""
        code, out, err = self._run(tmp_path, "report.json")
        assert (code, err) == (EXIT_OK, "")
        assert out.count("\n") == 1
        assert out.endswith("; findings: critical=0 warning=0 info=0\n")
        assert not any(ord(char) < 0x20 and char != "\n" for char in out)


class TestNulInTheOutputPathR39:
    """R39 (BUG-7): a NUL in ``--json`` escapes ``main`` as a bare ``ValueError``.

    ``main``'s docstring says it is "the only place a typed error becomes a
    process outcome" and that "no traceback ever reaches stderr". A path
    containing a NUL byte walks past it: ``check_output_path`` calls
    ``Path(raw).parent.is_dir()``, and ``os.stat`` raises
    ``ValueError("embedded null byte")`` before any of the typed errors
    ``main`` catches can be raised.

    ``check_output_path``'s own docstring is "an output whose directory does not
    exist **or cannot be written** is exit 3", which is exactly this case. The
    console script instead exits 1 with a traceback — and R39 gives 1 the
    meaning "ran successfully and a finding met the threshold", so a wrapper
    reading the exit code is told the run succeeded and found something.

    This is BUG-1's shape (a raw exception past ``main``) reached by a far
    cheaper input: no 60-digit token count, just a byte in an argument.
    """

    def test_r39_a_nul_in_the_output_path_is_a_usage_error(self, tmp_path: Path) -> None:
        """R39: an output path that cannot be written is exit 3, not a crash."""
        code, out, err = invoke("analyze", str(CLEAN), "--json", f"{tmp_path}/r\x00x.json")
        assert code == EXIT_USAGE
        assert out == ""
        assert "Traceback" not in err

    def test_r39_the_nul_never_reaches_stderr_as_a_byte(self, tmp_path: Path) -> None:
        """R11 (BUG-7, fixed in review): the rejected path is not echoed raw."""
        _, _, err = invoke("analyze", str(CLEAN), "--json", f"{tmp_path}/r\x00x.json")
        assert err.count("\n") == 1
        assert not any(ord(char) < 0x20 and char != "\n" for char in err)

    def test_r39_a_nul_in_an_input_path_is_a_sanitized_fail_closed(self, tmp_path: Path) -> None:
        """R11/R39 (BUG-7, fixed in review): the input side, at its own call site.

        Kept as a separate test because the two are different call sites and a
        fix to ``check_output_path`` alone leaves this one open — which is what
        the report said and what the fix had to answer. R11's posture is that
        every unreadable input is a sanitized exit 2, not a usage error: the
        command is well formed, the file is not readable.
        """
        report = tmp_path / "r.json"
        code, out, err = invoke("analyze", f"{tmp_path}/in\x00put.jsonl", "--json", str(report))
        assert code == EXIT_FAIL_CLOSED
        assert out == ""
        assert err.startswith("swarm-observer: unreadable_path: ")
        assert "Traceback" not in err
        assert err.count("\n") == 1
        assert not report.exists()

    def test_r39_a_nul_inside_a_scanned_directory_argument_is_refused_too(
        self, tmp_path: Path
    ) -> None:
        """R11: the third form — the NUL is in a *directory* argument.

        ``expand_inputs`` asks ``is_dir()`` before the reader ever sees the
        path, so this is a third call site with the same raw-``ValueError``
        shape. Driven because BUG-1 and BUG-7 were two occurrences of one class
        and the report's warning was that a per-call-site fix invites a third.
        """
        code, out, err = invoke("analyze", f"{tmp_path}/dir\x00/", "--json", str(tmp_path / "r"))
        assert code == EXIT_FAIL_CLOSED
        assert out == ""
        assert "Traceback" not in err

    def test_r38_a_path_the_os_cannot_represent_is_not_a_directory(self) -> None:
        """R38/R11: the interpreter behaviour ``expand_inputs`` relies on.

        ``Path.is_dir()`` swallows ``ValueError`` and answers ``False`` for a
        path containing a NUL, which is why ``expand_inputs`` needs no guard of
        its own and the reader's check is the one that fires. Pinned rather than
        assumed: this is a CPython implementation detail, and if it changes, a
        NUL input path starts crashing again — this test is where that surfaces,
        instead of in a traceback on somebody's terminal.
        """
        assert Path("/tmp/x\x00y.jsonl").is_dir() is False
        assert Path("/tmp/x\x00y.jsonl").exists() is False
        assert cli_main.expand_inputs(["/tmp/x\x00y.jsonl"]) == (Path("/tmp/x\x00y.jsonl"),)

    def test_r39_an_ordinary_output_path_is_still_written(self, tmp_path: Path) -> None:
        """R39: the non-vacuous arm — the same invocation without the NUL works."""
        report = tmp_path / "rx.json"
        code, _, err = invoke("analyze", str(CLEAN), "--json", str(report))
        assert (code, err) == (EXIT_OK, "")
        assert report.is_file()


class TestTheFailClosedFloorR11R39:
    """R11/R39: ``main``'s catch-all, and the proof that it can fire.

    Added by review. BUG-1 and BUG-7 were two unrelated inputs — a 60-digit
    token count and a NUL byte in an argument — that each put a raw exception
    past ``main``, printing a traceback and exiting **1**, which R39 assigns the
    meaning "ran, and a finding met the threshold". Both call sites are fixed at
    the call site, because a floor that turns a usage error into an exit 2 is a
    worse answer than the exit 3 R39 pins for it. The floor exists for the
    *third* input nobody has found yet.

    A catch-all is exactly the guard shape this project's signature defect
    inhabits: it reports green forever because nothing reaches it. So it is
    driven directly, from three different exception types, and its message is
    asserted to name the type and **not** the exception's text — an unsanitized
    ``str(exc)`` on a pydantic or ``json`` error quotes the input, which is the
    byte of file content R11 forbids on stderr.
    """

    @staticmethod
    def _invoke_with(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> tuple[int, str, str]:
        def boom(*_args: Any, **_kwargs: Any) -> int:
            raise exc

        monkeypatch.setattr(cli_main, "run", boom)
        out, err = io.StringIO(), io.StringIO()
        code = cli_main.main(["analyze", "x", "--json", "y.json"], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("embedded null byte"),
            RecursionError("maximum recursion depth exceeded"),
            OSError(28, "No space left on device"),
        ],
        ids=["ValueError", "RecursionError", "OSError"],
    )
    def test_r39_an_untyped_exception_is_one_sanitized_line_and_exit_two(
        self, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> None:
        """R11/R39: the floor fires, and it fires as R11's shape."""
        code, out, err = self._invoke_with(monkeypatch, exc)
        assert code == EXIT_FAIL_CLOSED
        assert out == ""
        assert err == f"swarm-observer: unexpected_error: {type(exc).__name__}\n"

    def test_r11_the_floor_never_echoes_the_exception_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R11: "may never contain a byte of file content" — including via ``str(exc)``."""
        secret = "AKIAIOSFODNN7EXAMPLE /home/somebody/trace.jsonl line 4: {\x1b[31m"
        _, _, err = self._invoke_with(monkeypatch, ValueError(secret))
        assert "AKIA" not in err
        assert "somebody" not in err
        assert "\x1b" not in err
        assert err.count("\n") == 1

    def test_r39_the_floor_does_not_swallow_the_typed_clauses_above_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R39: the non-vacuous arm — the floor must not become the only clause.

        A bare ``except Exception`` placed too high would turn every usage error
        into an exit 2 and every fail-closed error into ``unexpected_error``,
        which would pass a test that only asserts "exit 2 with one line". Each
        typed clause is therefore re-asserted through the same entry point.
        """
        assert self._invoke_with(monkeypatch, UsageError("swarm-observer: error: nope")) == (
            EXIT_USAGE,
            "",
            "swarm-observer: error: nope\n",
        )
        code, _, err = self._invoke_with(monkeypatch, CostError("cost_precision_exceeded", seq=7))
        assert (code, err) == (
            EXIT_FAIL_CLOSED,
            "swarm-observer: cost_precision_exceeded: span 7\n",
        )
        code, _, err = self._invoke_with(monkeypatch, TraceError("invalid_json", source="a.jsonl"))
        assert (code, err) == (EXIT_FAIL_CLOSED, "swarm-observer: invalid_json: a.jsonl\n")

    def test_r39_a_keyboard_interrupt_is_not_reported_as_a_trace_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R39: the floor's *upper* bound — it catches ``Exception``, not ``BaseException``.

        Reporting ``^C`` as ``unexpected_error`` and exiting 2 would tell a CI
        wrapper the trace was malformed. ``KeyboardInterrupt`` and ``SystemExit``
        derive from ``BaseException`` and must pass through untouched.
        """
        with pytest.raises(KeyboardInterrupt):
            self._invoke_with(monkeypatch, KeyboardInterrupt())
        with pytest.raises(SystemExit):
            self._invoke_with(monkeypatch, SystemExit(9))
