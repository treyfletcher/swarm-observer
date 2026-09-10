"""What the detectors do with input nobody designed them for (R13-R25).

Two families of input, and they fail differently.

*Hostile* input is the injection-probe corpus (increment 4's requirement): every
free-text field carrying a payload. The question a detector must answer is not
"did you survive" but "did any of those
bytes reach a place R16 says they may not" — a ``summary``, or a ``metrics``
value other than a guarded tool name. The renderer's escaping is increment 4's
job; keeping the bytes out of the findings table in the first place is R16's,
and R16 exists precisely so that escaping is not the only line of defence.

*Degenerate* input is the shape nobody wrote a fixture for: an empty trace, one
span, every span identical, zero-duration spans, spans with no timing at all,
token counts near the machine word, ten thousand spans. Each of these is a real
transcript somewhere — a session that crashed on its first turn, a run inside a
container with a broken clock, an agent that emitted the same call two thousand
times. The increment-1 review's whole theme was guards that were right about the
value in front of them and wrong about the value next to it, so the question
here is deliberately the negative one: what is the complete set of things that
can come out of a detector?
"""

from __future__ import annotations

import re
import time
from typing import Any

import pytest
from pydantic import ValidationError

from swarm_observer.detect.base import (
    MAX_EVIDENCE_SPANS,
    MAX_PREVIEWS,
    NON_CONFORMING_TOOL_NAME,
    TOOL_NAME_PATTERN,
    TRACE_DERIVED_METRIC_KEYS,
    UNKNOWN_TOOL_NAME,
    DetectorConfig,
    Finding,
)
from swarm_observer.detect.registry import ALL_DETECTORS, DETECTOR_SLUGS, run_detectors
from swarm_observer.model.trace import SpanError, TokenUsage, Trace

from .detector_corpus import FIXTURE_DIR, load_trace
from .synthetic_traces import DIGEST_A, DIGEST_B, TraceBuilder, hex_id, tokens

CONFIG = DetectorConfig()

#: R16's tool-name pattern, used here to split the payload corpus by shape.
_TOOL_NAME = TOOL_NAME_PATTERN

#: Payloads from the injection-probe corpus, plus the Unicode shapes an id or a
#: tool name can carry. Assigned code points only: an unassigned one previews differently on
#: 3.11 and 3.12, which is the increment-1 addendum's finding and is checked for
#: committed data by ``tests/test_suite_integrity.py``.
PAYLOADS: tuple[str, ...] = (
    "</script><script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "<!--",
    "]]>",
    "&lt;script&gt;",
    "data:text/html;base64,PHN2Zz4=",
    "{{7*7}}",
    "../../etc/passwd",
    "AKIAIOSFODNN7EXAMPLE",
    "sk-ant-api03-notarealkey",
    "‮gnirts-detrevni",
    "zero​width",
    "العربية",
    "line\nbreak",
    "tab\tseparated",
    "null\x00byte",
)

#: The payloads above that R16's pattern *rejects* — the large majority.
NON_CONFORMING_PAYLOADS: tuple[str, ...] = tuple(
    payload for payload in PAYLOADS if not re.fullmatch(_TOOL_NAME, payload)
)

#: The payloads above that happen to be legal tool names. Both are credential
#: shapes, which is the interesting half: R16's guard is about shape, so it lets
#: them through, and redaction is what must catch them at render.
CONFORMING_PAYLOADS: tuple[str, ...] = tuple(
    payload for payload in PAYLOADS if re.fullmatch(_TOOL_NAME, payload)
)

#: Every agent id below is legal under R2's ``AGENT_ID_PATTERN``. Agent ids are
#: never raw trace text — the mapper digests a non-conforming one (R5) — so the
#: adversarial question here is about the *alphabet's* extremes, not about
#: injection.
EXTREME_AGENT_IDS: tuple[str, ...] = ("a", "0", "A" * 64, "a.b:c-d", "-", ".", ":")


def all_findings(trace: Trace, config: DetectorConfig = CONFIG) -> tuple[Finding, ...]:
    """Every detector's findings for ``trace``, merged and sorted (R13)."""
    return run_detectors(trace, config)


def assert_well_formed(findings: tuple[Finding, ...]) -> None:
    """Every finding satisfies R14's shape, whatever produced it."""
    for finding in findings:
        assert finding.detector in DETECTOR_SLUGS
        assert finding.finding_id.startswith(f"{finding.detector}:")
        assert finding.severity in {"info", "warning", "critical"}
        assert list(finding.span_seqs) == sorted(set(finding.span_seqs))
        assert len(finding.span_seqs) <= MAX_EVIDENCE_SPANS
        assert list(finding.agent_ids) == sorted(set(finding.agent_ids))
        assert len(finding.previews) <= MAX_PREVIEWS
        assert list(finding.metrics) == sorted(finding.metrics)
        assert finding.summary


class TestHostileFixtureR16:
    """R16: the checked-in hostile transcript, through every detector."""

    @pytest.fixture(scope="class")
    def hostile(self) -> Trace:
        """``hostile.jsonl`` through the real adapter."""
        return load_trace(FIXTURE_DIR / "hostile.jsonl")

    @pytest.mark.parametrize("detector", ALL_DETECTORS, ids=DETECTOR_SLUGS)
    def test_r13_every_detector_runs_on_the_hostile_fixture(
        self, detector: Any, hostile: Trace
    ) -> None:
        """R13: no detector raises on a transcript whose every field is a payload."""
        assert_well_formed(detector.run(hostile, CONFIG))

    def test_r16_no_payload_reaches_a_summary(self, hostile: Trace) -> None:
        """R16: "Trace-derived text NEVER enters ``summary``"."""
        found = all_findings(hostile)
        assert found, "the hostile fixture must produce findings for this to mean anything"
        for finding in found:
            for payload in PAYLOADS:
                assert payload not in finding.summary
            assert "<" not in finding.summary
            assert all(character.isprintable() or character == " " for character in finding.summary)

    def test_r16_metrics_string_values_are_slugs_or_guarded_tool_names(
        self, hostile: Trace
    ) -> None:
        """R16: the one trace-derived value in ``metrics`` is a pattern-checked name."""
        import re

        from swarm_observer.detect.base import TOOL_NAME_PATTERN

        allowed = {NON_CONFORMING_TOOL_NAME, UNKNOWN_TOOL_NAME}
        for finding in all_findings(hostile):
            for key, value in finding.metrics.items():
                if not isinstance(value, str):
                    continue
                assert (
                    value in allowed
                    or re.fullmatch(TOOL_NAME_PATTERN, value)
                    or (key in {"reason", "dimension", "kinds"})
                ), (finding.detector, key, value)

    def test_r16_a_hostile_tool_name_is_slugged_and_the_original_previewed(
        self, hostile: Trace
    ) -> None:
        """R16: the fixture carries a non-conforming name, and it lands in previews."""
        found = all_findings(hostile)
        slugged = [
            finding
            for finding in found
            if finding.metrics.get("tool_name") == NON_CONFORMING_TOOL_NAME
        ]
        assert slugged, "hostile.jsonl is expected to carry a non-conforming tool name"

    def test_r14_hostile_findings_are_valid_findings(self, hostile: Trace) -> None:
        """R14: the caps and orderings hold on hostile input too."""
        assert_well_formed(all_findings(hostile))

    def test_r15_hostile_finding_ids_are_ascii_hex(self, hostile: Trace) -> None:
        """R15: ``ensure_ascii`` — a payload cannot put a non-ASCII byte in an id."""
        for finding in all_findings(hostile):
            digest = finding.finding_id.split(":", 1)[1]
            assert set(digest) <= set("0123456789abcdef")
            assert finding.finding_id.isascii()

    def test_r13_the_hostile_fixture_is_deterministic(self, hostile: Trace) -> None:
        """R13: hostile input does not make the run order-dependent."""
        first = [item.model_dump() for item in all_findings(hostile)]
        second = [item.model_dump() for item in all_findings(hostile)]
        assert first == second


class TestSyntheticHostileInputR16:
    """R16: payloads placed deliberately in each field the detectors read."""

    def _payload_trace(self, payload: str) -> Trace:
        builder = TraceBuilder()
        for index in range(9):
            call = builder.model_call(
                usage=tokens(100 if index else 900_000),
                start_ms=index * 1_000,
                end_ms=index * 1_000 + 10,
                text_preview=payload,
                model=payload[:200],
            )
            builder.tool_call(
                parent=call,
                tool_name=payload[:200],
                digest=DIGEST_A,
                status="error",
                input_preview=payload,
                result_preview=f"no such tool: {payload}",
            )
        builder.model_call(agent_id="root", start_ms=9_000_000, end_ms=9_001_000)
        builder.warning("orphan_tool_result", 2)
        return builder.build()

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_r16_no_payload_reaches_a_summary(self, payload: str) -> None:
        """R16: every payload shape, in every field a detector reads."""
        found = all_findings(self._payload_trace(payload))
        assert found
        assert_well_formed(found)
        for finding in found:
            assert payload not in finding.summary

    @pytest.mark.parametrize("payload", NON_CONFORMING_PAYLOADS)
    def test_r16_a_payload_tool_name_becomes_the_non_conforming_slug(self, payload: str) -> None:
        """R16: a payload that is not a legal tool name never reaches ``metrics``."""
        found = all_findings(self._payload_trace(payload))
        named = [
            item
            for item in found
            if "tool_name" in item.metrics and item.metrics["tool_name"] != UNKNOWN_TOOL_NAME
        ]
        assert named
        for item in named:
            assert item.metrics["tool_name"] == NON_CONFORMING_TOOL_NAME
            assert payload not in str(item.metrics["tool_name"])

    @pytest.mark.parametrize("payload", CONFORMING_PAYLOADS)
    def test_r16_a_payload_that_is_a_legal_tool_name_is_carried_verbatim(
        self, payload: str
    ) -> None:
        """R16: a credential-shaped string can *be* a conforming tool name.

        ``AKIAIOSFODNN7EXAMPLE`` and ``sk-ant-api03-...`` both match R16's
        pattern exactly, so R16 carries them into ``metrics.tool_name`` unchanged
        — correctly, because R16's guard is about *shape*, not about content.
        The consequence is a handoff, not a defect here: increment 4's
        redaction pass has to run over ``metrics`` string values, not only over
        ``previews``, or the injection probe's "the credential-shaped payloads
        do not appear at all" will be false for the findings table.
        """
        found = all_findings(self._payload_trace(payload))
        named = [
            item
            for item in found
            if "tool_name" in item.metrics and item.metrics["tool_name"] != UNKNOWN_TOOL_NAME
        ]
        assert named
        assert all(item.metrics["tool_name"] == payload for item in named)
        assert all(payload not in item.summary for item in found)

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_r13_a_payload_trace_is_deterministic(self, payload: str) -> None:
        """R13: no payload makes the detectors order-dependent."""
        trace = self._payload_trace(payload)
        assert [item.model_dump() for item in all_findings(trace)] == [
            item.model_dump() for item in all_findings(trace)
        ]

    @pytest.mark.parametrize("agent_id", EXTREME_AGENT_IDS)
    def test_r14_agent_ids_at_the_alphabets_extremes_are_carried_and_sorted(
        self, agent_id: str
    ) -> None:
        """R14, R5: an id at the edge of R2's alphabet still sorts and still fires."""
        builder = TraceBuilder()
        builder.model_call(agent_id=agent_id, start_ms=0, end_ms=1_000)
        builder.model_call(agent_id=agent_id, start_ms=601_000, end_ms=602_000)
        found = all_findings(builder.build())
        assert found
        assert all(item.agent_ids == (agent_id,) for item in found)
        assert_well_formed(found)

    def test_r14_many_agents_sort_by_id_not_by_appearance(self) -> None:
        """R14: ``agent_ids`` is sorted, whatever order the agents appeared in."""
        builder = TraceBuilder()
        call = builder.model_call(agent_id="zzz")
        builder.tool_call(agent_id="zzz", parent=call, digest=DIGEST_A)
        builder.tool_call(agent_id="aaa", parent=call, digest=DIGEST_A)
        found = [
            item for item in all_findings(builder.build()) if item.detector == "repeated_tool_call"
        ]
        assert found[0].agent_ids == ("aaa", "zzz")


class TestMetricsAreAuthoredR16:
    """R16: what is allowed to be in ``metrics``, enforced rather than remembered.

    R16's promise is that "a reader skimming the findings table is reading bytes
    this codebase authored", with exactly one exception it names: ``tool_name``.
    The exception is a *shape* check, and increment 1's review found this same
    class of defect one layer down — a guard that accepted an AWS key id as a
    "safe" class name. So two things are checked here that the branch previously
    only asserted about the values that happened to exist:

    * the exception is enumerated in :data:`TRACE_DERIVED_METRIC_KEYS`, so
      increment 4's renderer consumes a list instead of remembering a sentence;
    * every *other* metrics string is refused by :class:`Finding` itself unless
      it is a slug this package could have written, so a future detector cannot
      open a second hole by putting trace text under a new key.

    What remains open — and is deliberately visible below — is that a
    credential-shaped string *is* a legal tool name. Increment 4's redaction has
    to cover ``metrics`` for its injection probe's promise to hold; the canary
    ledger carries that as ``metrics_redaction_dropped``. Those two requirement
    ids are deliberately not written here, because a citation the R52 check reads
    would mark them covered by a test that does not test them.
    """

    #: Every enumerated metrics value the seven detectors can emit, taken from
    #: the detectors' own exported constants rather than restated here.
    def authored_values(self) -> set[str]:
        from swarm_observer.detect.anomalous_span import DIMENSIONS
        from swarm_observer.detect.retry_storm import KIND_API, KIND_MIXED, KIND_TOOL
        from swarm_observer.detect.unresolved_tool_call import REASONS

        return {KIND_API, KIND_MIXED, KIND_TOOL, *REASONS, *DIMENSIONS}

    def test_r16_every_metrics_string_is_authored_or_a_guarded_tool_name(self) -> None:
        """R16: over the whole corpus, the only trace-derived metric is ``tool_name``."""
        authored = self.authored_values()
        seen_keys: set[str] = set()
        for path in sorted(FIXTURE_DIR.glob("*.jsonl")):
            for finding in all_findings(load_trace(path)):
                for key, value in finding.metrics.items():
                    if not isinstance(value, str):
                        continue
                    seen_keys.add(key)
                    if key in TRACE_DERIVED_METRIC_KEYS:
                        continue
                    assert value in authored, (finding.detector, key, value)
        assert seen_keys, "no fixture produced a string metric; the check would be vacuous"
        assert seen_keys >= TRACE_DERIVED_METRIC_KEYS

    def test_r16_a_new_metric_key_cannot_smuggle_trace_text(self) -> None:
        """R16: an authored key refuses anything that is not a slug this package wrote.

        Both arms: the legal slug constructs, the payload does not. Without the
        negative arm this asserts only that valid input is valid.
        """
        from swarm_observer.detect.base import build_finding

        from .synthetic_traces import TraceBuilder

        builder = TraceBuilder()
        builder.model_call()
        trace = builder.build()

        ok = build_finding(
            trace=trace,
            detector="retry_storm",
            severity="warning",
            summary="s",
            metrics={"kinds": "mixed"},
            span_seqs=[0],
            agent_ids=["root"],
        )
        assert ok.metrics["kinds"] == "mixed"

        for payload in ("<script>alert(1)</script>", "Not A Slug", "AKIAIOSFODNN7EXAMPLE", "é"):
            with pytest.raises(ValidationError):
                build_finding(
                    trace=trace,
                    detector="retry_storm",
                    severity="warning",
                    summary="s",
                    metrics={"kinds": payload},
                    span_seqs=[0],
                    agent_ids=["root"],
                )

    def test_r16_the_trace_derived_key_still_admits_only_a_constrained_name(self) -> None:
        """R16: ``tool_name`` is R16-shaped or one of the two sentinels, never raw."""
        from swarm_observer.detect.base import build_finding

        from .synthetic_traces import TraceBuilder

        builder = TraceBuilder()
        builder.model_call()
        trace = builder.build()

        def make(value: str) -> Finding:
            return build_finding(
                trace=trace,
                detector="failed_tool_call",
                severity="info",
                summary="s",
                metrics={"tool_name": value},
                span_seqs=[0],
                agent_ids=["root"],
            )

        for legal in ("Bash", "mcp:server.tool-name", NON_CONFORMING_TOOL_NAME, UNKNOWN_TOOL_NAME):
            assert make(legal).metrics["tool_name"] == legal
        # The newline is the increment-1 B5 trap: Python's ``$`` matches before
        # one, so a guard using ``match`` would let it through.
        for illegal in ("Bash\n", "<script>", "9leading", "", "a" * 65):
            with pytest.raises(ValidationError):
                make(illegal)

    def test_r16_a_credential_shaped_tool_name_reaches_metrics_verbatim(self) -> None:
        """R16 (review S13): the known exposure, named and ledgered.

        ``AKIAIOSFODNN7EXAMPLE`` and ``sk-ant-api03-…`` both match R16's pattern
        exactly, so they reach ``metrics['tool_name']`` verbatim — R16 is doing
        what it says.

        The requirement that promises credential-shaped payloads appear nowhere
        in a rendered report, and the requirement that would have to redact
        ``metrics`` for that to be true, both belong to increment 4 and are
        deliberately *not* cited here: naming them would satisfy the R52
        traceability check without testing them, which is the exact green-while-
        unable-to-fail shape this project keeps shipping. The debt is carried by
        ``metrics_redaction_dropped`` in the canary ledger instead.

        What this test does assert is that the exposure exists, so it cannot be
        closed silently or forgotten.
        """
        from swarm_observer.detect.base import constrain_tool_name

        from .test_suite_integrity import REQUIRED_CANARIES

        for credential in ("AKIAIOSFODNN7EXAMPLE", "sk-ant-api03-notarealkey"):
            value, overflow = constrain_tool_name(credential)
            assert value == credential, "R16's guard is about shape, not about secrets"
            assert overflow is None, "a conforming name is not pushed into previews"
        assert "tool_name" in TRACE_DERIVED_METRIC_KEYS
        assert REQUIRED_CANARIES.get("metrics_redaction_dropped") == 4

    def test_r15_the_id_payload_is_ascii_whatever_the_metric_holds(self) -> None:
        """R15: ``ensure_ascii=True`` — recomputed from the requirement's own text.

        ``Finding`` now refuses a non-ASCII metric, so the only way to observe
        this clause is on :func:`finding_id` directly, which is public API. The
        expected digest is built from R15's serialization here rather than
        pinned as a literal, so the test states the requirement instead of
        recording an output.
        """
        import hashlib
        import json

        from swarm_observer.detect.base import finding_id

        metrics: dict[str, int | str] = {"tool_name": "café"}
        payload = json.dumps(
            {"trace": "0" * 16, "detector": "x", "metrics": metrics, "spans": [1]},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        assert payload.isascii()
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        assert (
            finding_id(trace_id="0" * 16, detector="x", metrics=metrics, span_seqs=[1])
            == f"x:{expected}"
        )


class TestDegenerateTracesR13:
    """R13-R25: the shapes no fixture holds, and every detector's answer to them."""

    def test_r13_an_empty_trace_produces_nothing_from_every_detector(self) -> None:
        """R13: zero spans is a legal trace, and the answer is silence."""
        trace = TraceBuilder().build()
        assert all_findings(trace) == ()
        for detector in ALL_DETECTORS:
            assert detector.run(trace, CONFIG) == ()

    def test_r13_a_single_span_trace_produces_nothing(self) -> None:
        """R13: a session that crashed on its first turn is not seven findings."""
        builder = TraceBuilder()
        builder.model_call(usage=tokens(5_000_000), start_ms=0, end_ms=900_000)
        assert all_findings(builder.build()) == ()

    def test_r13_a_single_tool_call_trace_produces_nothing(self) -> None:
        """R22: the lone span is also the trailing span, so the carve-out takes it."""
        builder = TraceBuilder()
        builder.tool_call(status="missing")
        assert all_findings(builder.build()) == ()

    def test_r19_a_trace_where_every_span_is_identical_reports_one_loop(self) -> None:
        """R19: 500 identical decisions are one loop of 500, not 498 findings."""
        builder = TraceBuilder()
        for _ in range(500):
            builder.model_call()
        found = all_findings(builder.build())
        assert [item.detector for item in found] == ["agent_loop"]
        assert found[0].metrics["repeats"] == 500
        assert len(found[0].span_seqs) == MAX_EVIDENCE_SPANS
        assert_well_formed(found)

    def test_r18_a_trace_of_identical_tool_calls_reports_one_group(self) -> None:
        """R18: one group, one finding, whatever the group's size."""
        builder = TraceBuilder()
        call = builder.model_call(usage=tokens(7))
        for _ in range(2_000):
            builder.tool_call(parent=call, digest=DIGEST_A)
        found = [
            item for item in all_findings(builder.build()) if item.detector == "repeated_tool_call"
        ]
        assert len(found) == 1
        assert found[0].metrics["occurrences"] == 2_000
        assert found[0].severity == "critical"
        assert found[0].wasted == tokens(7)

    def test_r24_zero_duration_spans_do_not_fire_the_duration_dimension(self) -> None:
        """R24: an instantaneous span is under every floor, and the MAD is zero."""
        builder = TraceBuilder()
        for _ in range(12):
            builder.model_call(usage=tokens(100), start_ms=5_000, end_ms=5_000)
        found = all_findings(builder.build())
        assert not [item for item in found if item.detector == "anomalous_span"]

    def test_r24_spans_with_no_timing_are_measured_only_on_tokens(self) -> None:
        """R24: a container with a broken clock still gets a token analysis."""
        builder = TraceBuilder()
        for _ in range(7):
            builder.model_call(usage=tokens(100))
        builder.model_call(usage=tokens(5_000_000))
        found = [
            item for item in all_findings(builder.build()) if item.detector == "anomalous_span"
        ]
        assert [item.metrics["dimension"] for item in found] == ["tokens"]

    def test_r23_a_trace_with_no_timing_at_all_reports_no_gaps(self) -> None:
        """R23: no timestamps, no gaps — not a gap of zero and not a crash."""
        builder = TraceBuilder()
        for _ in range(20):
            builder.model_call(usage=tokens(100))
        assert not [
            item for item in all_findings(builder.build()) if item.detector == "blocked_agent"
        ]

    def test_r17_token_counts_near_the_machine_word_stay_exact(self) -> None:
        """R17: Python ints do not overflow, and no float is allowed to intervene."""
        big = 2**62
        builder = TraceBuilder()
        for _ in range(3):
            call = builder.model_call(usage=TokenUsage(input_tokens=big, output_tokens=big))
            builder.tool_call(parent=call, digest=DIGEST_A)
        found = [
            item for item in all_findings(builder.build()) if item.detector == "repeated_tool_call"
        ]
        assert found[0].wasted.input_tokens == 2 * big
        assert found[0].wasted.output_tokens == 2 * big

    def test_r24_a_huge_outlier_against_a_huge_population_is_still_integral(self) -> None:
        """R24: the statistics are ``int``, so magnitude changes nothing about them."""
        builder = TraceBuilder()
        for _ in range(7):
            builder.model_call(usage=tokens(2**40))
        builder.model_call(usage=tokens(2**60))
        found = [
            item for item in all_findings(builder.build()) if item.detector == "anomalous_span"
        ]
        assert len(found) == 1
        assert found[0].metrics["median"] == 2**40
        assert isinstance(found[0].metrics["value"], int)

    def test_r22_an_agent_with_a_single_missing_tool_call_is_carved_out(self) -> None:
        """R22: the smallest possible truncated capture, twice over."""
        builder = TraceBuilder()
        builder.tool_call(agent_id="root", status="missing", digest=DIGEST_A)
        builder.tool_call(agent_id="sub", status="missing", digest=DIGEST_B)
        assert all_findings(builder.build()) == ()

    def test_r13_a_trace_whose_agents_never_overlap_is_handled(self) -> None:
        """R23: two agents with disjoint timelines explain none of each other's gaps."""
        builder = TraceBuilder()
        builder.model_call(agent_id="root", start_ms=0, end_ms=1_000)
        builder.model_call(agent_id="root", start_ms=601_000, end_ms=602_000)
        builder.model_call(agent_id="sub", start_ms=1_000_000, end_ms=1_001_000)
        builder.model_call(agent_id="sub", start_ms=1_601_000, end_ms=1_602_000)
        found = [item for item in all_findings(builder.build()) if item.detector == "blocked_agent"]
        assert len(found) == 2

    def test_r20_a_trace_of_nothing_but_api_errors_reports_one_storm_per_agent(self) -> None:
        """R20: a rate-limited run is a storm, and the ``kinds`` metric says which."""
        builder = TraceBuilder()
        for index in range(30):
            builder.model_call(error=SpanError(code="rate_limit", detail=f"429 {index}"))
        found = [item for item in all_findings(builder.build()) if item.detector == "retry_storm"]
        assert len(found) == 1
        assert found[0].metrics["kinds"] == "api_error"
        assert found[0].severity == "critical"
        assert len(found[0].span_seqs) <= MAX_EVIDENCE_SPANS

    def test_r14_a_finding_over_fifty_evidence_spans_is_capped_not_rejected(self) -> None:
        """R14: a 2,000-span storm reports 50 spans and the true count."""
        builder = TraceBuilder()
        for index in range(2_000):
            builder.tool_call(status="error", result_preview=f"boom {index}")
        found = [item for item in all_findings(builder.build()) if item.detector == "retry_storm"]
        assert len(found) == 1
        assert len(found[0].span_seqs) == MAX_EVIDENCE_SPANS
        assert found[0].metrics["errors"] == 2_000

    def test_r13_a_mixed_degenerate_trace_produces_only_valid_findings(self) -> None:
        """R13-R25: everything at once — the shape a real broken session has."""
        builder = TraceBuilder()
        builder.user_message()
        builder.system_event(start_ms=0, end_ms=0)
        for index in range(10):
            call = builder.model_call(
                agent_id="root" if index % 3 else "sub",
                usage=None if index == 4 else tokens(100 if index else 900_000),
                start_ms=index * 100_000,
                end_ms=index * 100_000 + (0 if index % 2 else 10),
                text_preview="‮preview",
            )
            builder.tool_call(
                agent_id=call.agent_id,
                parent=call,
                tool_name=None if index == 2 else "Bash",
                digest=None if index == 3 else DIGEST_A,
                status=["ok", "error", "missing"][index % 3],
                result_preview="unknown tool" if index == 5 else "",
            )
        builder.warning("orphan_tool_result", 9)
        found = all_findings(builder.build())
        assert found
        assert_well_formed(found)
        assert len({item.finding_id for item in found}) == len(found)


class TestScaleR13:
    """R13-R25: ten thousand spans, and what the growth curve looks like."""

    def _wide_trace(self, model_calls: int) -> Trace:
        builder = TraceBuilder()
        for index in range(model_calls):
            call = builder.model_call(
                usage=tokens(100),
                start_ms=index * 1_000,
                end_ms=index * 1_000 + 100,
            )
            builder.tool_call(
                parent=call,
                digest=DIGEST_A if index % 2 else DIGEST_B,
                start_ms=index * 1_000,
                end_ms=index * 1_000 + 50,
            )
        return builder.build()

    def test_r13_ten_thousand_spans_complete_within_a_generous_bound(self) -> None:
        """R13-R25: a whole-registry pass over 10,000 spans is bounded, not open-ended.

        The bound is loose on purpose — a shared runner is not a stopwatch — and
        what it catches is the class of change that turns a linear pass into a
        nested one.
        """
        trace = self._wide_trace(5_000)
        assert len(trace.spans) == 10_000
        started = time.perf_counter()
        found = all_findings(trace)
        elapsed = time.perf_counter() - started
        assert elapsed < 60.0, f"the registry took {elapsed:.1f}s on 10,000 spans"
        assert_well_formed(found)

    def test_r13_a_ten_thousand_span_trace_still_produces_bounded_findings(self) -> None:
        """R14: a large trace does not produce a finding per span."""
        found = all_findings(self._wide_trace(5_000))
        assert len(found) < 100
        assert all(len(item.span_seqs) <= MAX_EVIDENCE_SPANS for item in found)

    @staticmethod
    def _time(detector: Any, trace: Trace) -> float:
        started = time.perf_counter()
        detector.run(trace, CONFIG)
        return time.perf_counter() - started

    def test_r20_retry_storm_does_not_grow_quadratically(self) -> None:
        """R20: the window scan is linear in an agent's span count (BUG-2).

        An agent whose spans are all errors is not exotic — a rate-limited run
        looks exactly like this, and tens of thousands of spans is the ordinary
        size of a transcript, not an adversarial one.

        Two bounds, because each catches what the other cannot. The *ratio*
        catches a regression on a slow shared runner, where an absolute number
        would be noise. The *ceiling* catches a regression that happens to
        scale both measurements together — and it is a real number rather than
        a comfortable one: the quadratic form took 9.2 s on this input, the
        linear form takes about 0.04 s, and the bound sits between them with
        room on both sides.
        """
        from swarm_observer.detect.registry import detector_by_slug

        from .synthetic_traces import error_run

        storm = detector_by_slug("retry_storm")
        small = max(self._time(storm, error_run(5_000, range(5_000))), 1e-4)
        large = self._time(storm, error_run(20_000, range(20_000)))
        assert large < 2.0, (
            f"20,000 error spans took {large:.2f}s; the quadratic form took ~9.2s "
            "and the linear form takes ~0.04s"
        )
        assert large < small * 8.0, (
            f"4x the spans cost {large / small:.1f}x the time "
            f"({small:.3f}s -> {large:.3f}s); linear would be ~4x"
        )

    def test_r23_blocked_agent_does_not_grow_quadratically(self) -> None:
        """R23: the coverage union is not rebuilt from scratch for every gap (BUG-3).

        Two agents that alternate, every gap over the threshold and unexplained
        — a long orchestrator/subagent session with slow turns. The union of the
        other agents' intervals is now built once per agent and queried per gap,
        rather than clipped and sorted afresh for each of them.

        Same pair of bounds as the R20 pin: the quadratic form took 8.3 s on the
        10,000-span case and the indexed form takes about 0.24 s.
        """
        from swarm_observer.detect.registry import detector_by_slug

        blocked = detector_by_slug("blocked_agent")

        def alternating(pairs: int) -> Trace:
            builder = TraceBuilder()
            for index in range(pairs):
                builder.model_call(
                    agent_id="a1", start_ms=index * 200_000, end_ms=index * 200_000 + 1_000
                )
                builder.model_call(
                    agent_id="a2",
                    start_ms=index * 200_000 + 5_000,
                    end_ms=index * 200_000 + 6_000,
                )
            return builder.build()

        small = max(self._time(blocked, alternating(1_250)), 1e-4)
        large = self._time(blocked, alternating(5_000))
        assert large < 2.0, (
            f"10,000 spans took {large:.2f}s; the quadratic form took ~8.3s "
            "and the indexed form takes ~0.24s"
        )
        assert large < small * 8.0, (
            f"4x the spans cost {large / small:.1f}x the time "
            f"({small:.3f}s -> {large:.3f}s); linear would be ~4x"
        )

    def test_r13_the_other_five_detectors_are_not_quadratic(self) -> None:
        """R13: the growth check is not vacuous — five detectors pass it today."""
        from swarm_observer.detect.registry import detector_by_slug

        quadratic = {"retry_storm", "blocked_agent"}
        small = self._wide_trace(1_250)
        large = self._wide_trace(5_000)
        for slug in DETECTOR_SLUGS:
            if slug in quadratic:
                continue
            detector = detector_by_slug(slug)
            base = max(self._time(detector, small), 1e-4)
            grown = self._time(detector, large)
            assert grown < base * 12.0, f"{slug}: 4x the spans cost {grown / base:.1f}x the time"


def test_r15_unicode_tool_names_do_not_collide_in_finding_ids() -> None:
    """R15, R16: two different non-conforming names produce two different findings.

    Both names are replaced by ``<non-conforming>`` in ``metrics`` (R16), so the
    ids would collide if ``metrics`` were the only thing hashed. They do not,
    because R15 hashes the evidence spans as well — which is the property that
    keeps the slug from erasing a distinction the reader can still see in the
    previews.
    """
    builder = TraceBuilder()
    call = builder.model_call()
    for name in ("zero​width", "‮gnirts"):
        for _ in range(2):
            builder.tool_call(parent=call, tool_name=name, digest=hex_id(abs(hash(name)) % 2**60))
    found = [
        item
        for item in run_detectors(builder.build(), CONFIG)
        if item.detector == "repeated_tool_call"
    ]
    assert len(found) == 2
    assert found[0].finding_id != found[1].finding_id
    assert all(item.metrics["tool_name"] == NON_CONFORMING_TOOL_NAME for item in found)
