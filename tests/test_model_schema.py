"""R1, R2 and R38's ``schema`` subcommand: the normalized model's pinned shape.

R2 is not only a field list. It pins orderings ("sorted by name", "sorted by
``seq``"), alphabets (64 lowercase hex, basename only, a closed warning enum),
caps ("capped at 200 chars") and a frozen, ``extra="forbid"`` posture. Each of
those is a claim a downstream consumer will rely on, so each is asserted here
against the constructed model rather than read off the source.

R1's schema version and R38's ``schema`` subcommand are checked the same way:
the printed document must equal the checked-in golden byte for byte, and must
be byte-identical across processes and across the hash-seed, timezone and
locale matrix, because a schema that renders differently on two machines is not
a contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from swarm_observer.cli.main import EXIT_OK, main, run
from swarm_observer.model.trace import (
    PARSE_WARNING_CODES,
    SPAN_KINDS,
    TRACE_SCHEMA_VERSION,
    AgentRun,
    ParseWarning,
    SourceFile,
    Span,
    SpanError,
    TokenUsage,
    Trace,
    schema_document,
    trace_json_schema,
)

from .harness import assert_cli_deterministic, assert_deterministic, assert_matches_golden

HEX16 = "0123456789abcdef"
SHA = "a" * 64
WHEN = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)

ALL_MODELS = (TokenUsage, SpanError, Span, AgentRun, SourceFile, ParseWarning, Trace)


def a_span(seq: int = 0, **overrides: object) -> Span:
    fields: dict[str, object] = {
        "span_id": HEX16,
        "agent_id": "root",
        "kind": "model_call",
        "seq": seq,
    }
    fields.update(overrides)
    return Span(**fields)  # type: ignore[arg-type]


def a_trace(**overrides: object) -> Trace:
    fields: dict[str, object] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "trace_id": HEX16,
        "adapter": "claude_code_jsonl",
    }
    fields.update(overrides)
    return Trace(**fields)  # type: ignore[arg-type]


class TestModelPostureR2:
    """R2: every normalized model is frozen and forbids unknown fields."""

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_r2_every_model_is_frozen_and_extra_forbid(self, model: type) -> None:
        """R2: ``ConfigDict(frozen=True, extra='forbid')`` on all of them."""
        config = model.model_config  # type: ignore[attr-defined]
        assert config.get("frozen") is True, f"{model.__name__} is not frozen"
        assert config.get("extra") == "forbid", f"{model.__name__} tolerates extra fields"

    def test_r2_assignment_to_a_built_model_raises(self) -> None:
        """R2: frozen means a consumer cannot mutate a trace it was handed."""
        span = a_span()
        with pytest.raises(ValidationError):
            span.seq = 5  # type: ignore[misc]

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_r2_an_unknown_field_is_rejected(self, model: type) -> None:
        """R2: adding a field is a schema-version bump, not a silent carry."""
        with pytest.raises(ValidationError):
            model(unexpected_field_that_does_not_exist=1)  # type: ignore[call-arg]

    def test_r2_token_usage_components_are_non_negative(self) -> None:
        """R2: all five components are ``int >= 0`` and default to 0."""
        assert TokenUsage().total == 0
        for field in TokenUsage.model_fields:
            with pytest.raises(ValidationError):
                TokenUsage(**{field: -1})  # type: ignore[arg-type]

    def test_r2_token_usage_plus_is_element_wise(self) -> None:
        """R2: the element-wise sum the waste attribution needs."""
        left = TokenUsage(input_tokens=1, output_tokens=2, cache_read_input_tokens=3)
        right = TokenUsage(input_tokens=10, cache_creation_5m_tokens=4, cache_creation_1h_tokens=5)
        total = left.plus(right)
        assert (total.input_tokens, total.output_tokens, total.cache_read_input_tokens) == (
            11,
            2,
            3,
        )
        assert (total.cache_creation_5m_tokens, total.cache_creation_1h_tokens) == (4, 5)
        assert total.total == 25


class TestTimestampsAreTimezoneAwareR2:
    """R2: every timestamp on the model is a tz-aware UTC datetime."""

    @pytest.mark.parametrize("field", ["start", "end"])
    def test_r2_a_naive_span_timestamp_is_rejected(self, field: str) -> None:
        """R2: a naive datetime would render differently per machine timezone."""
        with pytest.raises(ValidationError, match="timezone-aware"):
            a_span(**{field: datetime(2026, 9, 9, 10, 0)})

    def test_r2_a_naive_agent_timestamp_is_rejected(self) -> None:
        """R2: the rule holds on ``AgentRun`` too, not only on ``Span``."""
        with pytest.raises(ValidationError, match="timezone-aware"):
            AgentRun(agent_id="root", agent_index=0, start=datetime(2026, 9, 9, 10, 0))

    def test_r2_an_offset_timestamp_is_normalized_to_utc(self) -> None:
        """R2: an aware non-UTC timestamp is converted, not rejected."""
        kolkata = timezone(timedelta(hours=5, minutes=30))
        span = a_span(start=datetime(2026, 9, 9, 15, 30, tzinfo=kolkata))
        assert span.start == WHEN
        assert span.start is not None and span.start.utcoffset() == timedelta(0)

    def test_r2_epoch_and_far_future_timestamps_are_accepted(self) -> None:
        """R2: the boundary values are valid instants, not error cases."""
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        far = datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=UTC)
        span = a_span(start=epoch, end=far)
        assert span.start == epoch
        assert span.end == far


class TestIdAndPathAlphabetsR2:
    """R2: ids, hashes and file names are constrained at the model."""

    @pytest.mark.parametrize("bad", ["", "xyz", "A" * 16, HEX16 + "0", "0123456789ABCDEF"])
    def test_r2_span_id_must_be_16_lowercase_hex(self, bad: str) -> None:
        """R2: the 16-hex id shape is what makes an id safe to place in markup."""
        with pytest.raises(ValidationError):
            a_span(span_id=bad)

    @pytest.mark.parametrize("bad", ["", "z" * 64, "A" * 64, "a" * 63, "a" * 65])
    def test_r2_source_file_sha256_must_be_64_lowercase_hex(self, bad: str) -> None:
        """R2: provenance is a digest, and a digest has exactly one shape."""
        with pytest.raises(ValidationError):
            SourceFile(name="agent-1.jsonl", sha256=bad, bytes=1, records=1)

    @pytest.mark.parametrize(
        "bad", ["dir/agent-1.jsonl", "..", ".", "a\\b.jsonl", "/abs/agent-1.jsonl"]
    )
    def test_r2_source_file_name_must_be_a_basename(self, bad: str) -> None:
        """R2: a directory component would leak the analyst's filesystem."""
        with pytest.raises(ValidationError):
            SourceFile(name=bad, sha256=SHA, bytes=1, records=1)

    @pytest.mark.parametrize("bad", ["", "a b", "<script>", "x" * 65, "a/b"])
    def test_r2_agent_id_alphabet_is_enforced(self, bad: str) -> None:
        """R2: an agent id carrying trace bytes would break the safety claim."""
        with pytest.raises(ValidationError):
            a_span(agent_id=bad)

    def test_r2_span_kind_is_a_closed_set(self) -> None:
        """R2: the four span kinds and nothing else."""
        for kind in SPAN_KINDS:
            assert a_span(kind=kind).kind == kind
        with pytest.raises(ValidationError):
            a_span(kind="database_query")

    def test_r2_tool_result_status_is_a_closed_set(self) -> None:
        """R2: ok / error / missing, and nothing else."""
        for status in ("ok", "error", "missing"):
            assert a_span(tool_result_status=status).tool_result_status == status
        with pytest.raises(ValidationError):
            a_span(tool_result_status="pending")


class TestWarningTaxonomyIsClosedR2:
    """R2 and R10: ``ParseWarning`` admits only the pinned twelve codes."""

    @pytest.mark.parametrize("code", PARSE_WARNING_CODES)
    def test_r2_every_pinned_warning_code_constructs(self, code: str) -> None:
        """R2: each member of the closed enum is a valid code."""
        assert ParseWarning(code=code, count=1).code == code  # type: ignore[arg-type]

    def test_r2_an_invented_warning_code_is_rejected(self) -> None:
        """R2: a new adapter cannot invent a warning kind."""
        with pytest.raises(ValidationError):
            ParseWarning(code="disk_on_fire", count=1)  # type: ignore[arg-type]

    def test_r2_the_enum_and_its_data_twin_agree(self) -> None:
        """R2: the ``Literal`` and the exported tuple cannot drift apart."""
        from typing import get_args

        annotation = ParseWarning.model_fields["code"].annotation
        assert set(get_args(annotation)) == set(PARSE_WARNING_CODES)
        assert len(PARSE_WARNING_CODES) == len(set(PARSE_WARNING_CODES)) == 12

    @pytest.mark.parametrize(
        "bad", ["a byte of trace text", "<script>", "has space", "x" * 65, "línea"]
    )
    def test_r2_warning_detail_alphabet_excludes_free_text(self, bad: str) -> None:
        """R2: ``detail`` carries a slug or a number, never trace free text."""
        with pytest.raises(ValidationError):
            ParseWarning(code="unknown_record_type", count=1, detail=bad)

    def test_r2_warning_count_is_at_least_one(self) -> None:
        """R2: a warning that fired zero times is not a warning."""
        with pytest.raises(ValidationError):
            ParseWarning(code="missing_usage", count=0)


class TestPinnedOrderingsR2:
    """R2: every "sorted by" clause is a construction-time invariant."""

    def test_r2_source_files_must_be_sorted_by_name(self) -> None:
        """R2: ``source_files`` sorted by name."""
        first = SourceFile(name="agent-1.jsonl", sha256=SHA, bytes=1, records=1)
        second = SourceFile(name="agent-2.jsonl", sha256="b" * 64, bytes=1, records=1)
        assert a_trace(source_files=(first, second)).source_files[0].name == "agent-1.jsonl"
        with pytest.raises(ValidationError, match="sorted by name"):
            a_trace(source_files=(second, first))

    def test_r2_source_file_names_are_unique(self) -> None:
        """R2: two entries with one name would make provenance ambiguous."""
        one = SourceFile(name="agent-1.jsonl", sha256=SHA, bytes=1, records=1)
        with pytest.raises(ValidationError, match="repeat a name"):
            a_trace(source_files=(one, one))

    def test_r2_agents_must_be_sorted_by_agent_index(self) -> None:
        """R2: ``agents`` sorted by ``agent_index``."""
        first = AgentRun(agent_id="root", agent_index=0)
        second = AgentRun(agent_id="sub", agent_index=1)
        assert len(a_trace(agents=(first, second)).agents) == 2
        with pytest.raises(ValidationError, match="agent_index"):
            a_trace(agents=(second, first))

    def test_r2_agent_index_is_zero_based_and_contiguous(self) -> None:
        """R2: indexes name positions, so a gap is a construction error."""
        with pytest.raises(ValidationError, match="agent_index"):
            a_trace(agents=(AgentRun(agent_id="root", agent_index=1),))

    def test_r2_spans_must_be_sorted_by_seq(self) -> None:
        """R2: ``spans`` sorted by ``seq``."""
        assert len(a_trace(spans=(a_span(0), a_span(1))).spans) == 2
        with pytest.raises(ValidationError, match="sorted by seq"):
            a_trace(spans=(a_span(1), a_span(0)))

    def test_r2_span_seq_is_zero_based_and_contiguous(self) -> None:
        """R2: ``seq`` is a position, so ``spans[seq]`` must be valid indexing."""
        with pytest.raises(ValidationError, match="sorted by seq"):
            a_trace(spans=(a_span(0), a_span(2)))

    def test_r2_warnings_must_be_sorted_by_code_then_detail(self) -> None:
        """R2: ``warnings`` sorted by ``(code, detail)``."""
        low = ParseWarning(code="api_error_record", count=1)
        high = ParseWarning(code="unknown_record_type", count=1, detail="telemetry")
        assert len(a_trace(warnings=(low, high)).warnings) == 2
        with pytest.raises(ValidationError, match="sorted by"):
            a_trace(warnings=(high, low))

    def test_r2_warnings_must_be_aggregated(self) -> None:
        """R2: one entry per ``(code, detail)`` with a count, never repeats."""
        one = ParseWarning(code="missing_usage", count=1)
        with pytest.raises(ValidationError, match="aggregated"):
            a_trace(warnings=(one, one))

    def test_r2_agent_span_seqs_must_be_ascending_and_distinct(self) -> None:
        """R2: ``span_seqs`` ascending."""
        assert AgentRun(agent_id="root", agent_index=0, span_seqs=(0, 1, 5)).span_seqs == (0, 1, 5)
        with pytest.raises(ValidationError, match="ascending"):
            AgentRun(agent_id="root", agent_index=0, span_seqs=(1, 0))
        with pytest.raises(ValidationError, match="repeat"):
            AgentRun(agent_id="root", agent_index=0, span_seqs=(1, 1))


class TestSchemaVersionR1:
    """R1: one schema version, carried by every trace and every schema print."""

    def test_r1_the_version_is_the_pinned_semver(self) -> None:
        """R1: ``TRACE_SCHEMA_VERSION == '1.0.0'``."""
        assert TRACE_SCHEMA_VERSION == "1.0.0"

    def test_r1_a_trace_must_carry_the_current_version(self) -> None:
        """R1: a trace built at another version is a construction error."""
        assert a_trace().schema_version == TRACE_SCHEMA_VERSION
        with pytest.raises(ValidationError, match="schema_version"):
            a_trace(schema_version="0.9.0")

    def test_r1_the_schema_document_carries_the_version(self) -> None:
        """R1: every ``schema`` invocation carries the exact string."""
        document = json.loads(schema_document())
        assert document["schema_version"] == TRACE_SCHEMA_VERSION
        assert document["schema"] == trace_json_schema()


class TestJsonRoundTripR2:
    """R2: a trace survives a JSON round trip unchanged."""

    def _full_trace(self) -> Trace:
        span = a_span(
            0,
            start=WHEN,
            end=WHEN,
            model="claude-sonnet-4-5",
            usage=TokenUsage(input_tokens=3, output_tokens=4, cache_read_input_tokens=5),
            stop_reason="tool_use",
            text_preview="hello",
            extras_dropped=2,
        )
        tool = a_span(
            1,
            kind="tool_call",
            span_id="f" * 16,
            parent_span_id=HEX16,
            tool_name="Bash",
            tool_use_id="toolu_1",
            tool_input_digest="0" * 16,
            tool_result_status="ok",
            tool_input_preview='{"cmd":"ls"}',
            tool_result_preview="ok",
        )
        error_span = a_span(
            2, span_id="c" * 16, error=SpanError(code="rate_limit_error", detail="429")
        )
        return a_trace(
            source_files=(SourceFile(name="agent-1.jsonl", sha256=SHA, bytes=10, records=3),),
            agents=(
                AgentRun(
                    agent_id="root",
                    agent_index=0,
                    agent_type="general",
                    description="audit the repo",
                    depth=0,
                    span_seqs=(0, 1, 2),
                    start=WHEN,
                    end=WHEN,
                ),
            ),
            spans=(span, tool, error_span),
            warnings=(ParseWarning(code="missing_usage", count=1),),
        )

    def test_r2_round_trip_through_json_is_lossless(self) -> None:
        """R2: dump then validate reproduces an equal model."""
        trace = self._full_trace()
        restored = Trace.model_validate_json(trace.model_dump_json())
        assert restored == trace
        assert restored.model_dump_json() == trace.model_dump_json()

    def test_r2_round_trip_preserves_tuples_not_lists(self) -> None:
        """R2: the collection fields are tuples, which is what "frozen" means here."""
        restored = Trace.model_validate_json(self._full_trace().model_dump_json())
        assert isinstance(restored.spans, tuple)
        assert isinstance(restored.agents, tuple)
        assert isinstance(restored.warnings, tuple)
        assert isinstance(restored.source_files, tuple)
        assert isinstance(restored.agents[0].span_seqs, tuple)

    def test_r2_round_trip_keeps_timestamps_in_utc(self) -> None:
        """R2: a timestamp survives serialization as the same instant, in UTC."""
        restored = Trace.model_validate_json(self._full_trace().model_dump_json())
        assert restored.spans[0].start == WHEN
        assert restored.spans[0].start is not None
        assert restored.spans[0].start.tzinfo is not None


class TestSchemaSubcommandR38:
    """R38: ``swarm-observer schema`` prints one pinned, deterministic document."""

    def test_r38_schema_output_matches_the_checked_in_golden(self) -> None:
        """R1, R38: the printed document equals ``tests/golden/schema.json``."""
        assert_matches_golden("schema.json", schema_document())

    def test_r38_the_subcommand_writes_the_document_and_exits_zero(self) -> None:
        """R38: ``schema`` writes to the stream it is given and returns 0."""
        import io

        buffer = io.StringIO()
        assert run(["schema"], stdout=buffer) == EXIT_OK
        assert buffer.getvalue() == schema_document()

    def test_r38_schema_output_ends_with_exactly_one_newline(self) -> None:
        """R38: a printed document is a line-terminated file, not a fragment."""
        document = schema_document()
        assert document.endswith("\n")
        assert not document.endswith("\n\n")

    def test_r38_schema_output_is_pure_ascii_and_sorted(self) -> None:
        """R38: ``ensure_ascii`` and ``sort_keys`` are what remove the environment."""
        document = schema_document()
        document.encode("ascii")
        assert document == json.dumps(json.loads(document), sort_keys=True, indent=2) + "\n"

    def test_r38_schema_output_is_stable_across_the_environment_matrix(self) -> None:
        """R38: hash seed, timezone and locale do not change the bytes."""
        digest = assert_deterministic(schema_document)
        assert len(digest) == 64

    def test_r38_schema_output_is_stable_across_processes(self) -> None:
        """R38: separate interpreters print the same bytes."""
        digest = assert_cli_deterministic(["schema"])
        assert len(digest) == 64

    def test_r38_schema_subcommand_stdout_matches_the_in_process_document(self) -> None:
        """R38: the subprocess and the library agree byte for byte."""
        completed = subprocess.run(
            [sys.executable, "-m", "swarm_observer.cli.main", "schema"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert completed.stdout == schema_document()
        assert completed.stderr == ""

    def test_r38_only_the_pinned_subcommands_are_registered(self) -> None:
        """R38: registering a subcommand that errors would be worse than its absence."""
        from swarm_observer.cli.main import build_parser

        actions = [
            action
            for action in build_parser()._actions
            if getattr(action, "choices", None) and action.dest == "command"
        ]
        assert len(actions) == 1
        assert set(actions[0].choices) == {"schema"}

    def test_r38_an_unknown_subcommand_is_a_usage_error(self) -> None:
        """R38: argparse rejects a command the parser does not define."""
        with pytest.raises(SystemExit) as raised:
            main(["no-such-command"])
        assert raised.value.code == 2  # argparse's own usage exit


def test_r1_the_schema_lists_every_normalized_model() -> None:
    """R1: the schema document exposes the whole normalized shape, not part of it."""
    schema = trace_json_schema()
    definitions = set(schema.get("$defs", {}))
    assert {"Span", "AgentRun", "SourceFile", "ParseWarning", "TokenUsage", "SpanError"} <= (
        definitions
    )
    assert set(schema["properties"]) == set(Trace.model_fields)
