"""R3 and R11: the adapter seam, the ingest caps and the fail-closed path.

R11 is the requirement with the most ways to be half-kept. Its promise is not
"bad input raises" — it is that bad input raises *one sanitized line*, exits 2,
prints no traceback, produces no partial trace, and creates or truncates no
output file. Each clause is asserted separately below, per input class, because
an implementation can satisfy any three of them and still leak a byte of a
hostile file into a terminal.

The caps are exercised by shrinking :class:`IngestLimits` rather than by
committing a 256 MiB fixture: the cap is a number the reader compares against,
and a test that has to write a quarter of a gigabyte to reach it is a test
nobody runs.

Four of the input classes an adversary might try — an empty file, CRLF line
endings, a NUL byte inside a JSON string, and moderately nested JSON — are
*not* fatal, and this module pins that too. They are well-formed JSONL by every
rule R4 states, and failing closed on them would reject valid transcripts.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

from swarm_observer.cli.main import EXIT_FAIL_CLOSED, main
from swarm_observer.ingest.claude_code.mapper import ADAPTER_SLUG, ClaudeCodeSource
from swarm_observer.ingest.reader import (
    atomic_write_text,
    atomic_write_texts,
    compute_trace_id,
    digest_id,
    exceeds_json_depth,
    load_input,
    read_jsonl,
    resolve_inputs,
)
from swarm_observer.ingest.registry import ADAPTERS, adapter_slugs, build_adapter
from swarm_observer.ingest.source import (
    TRACE_ERROR_CODES,
    IngestLimits,
    TraceError,
    TraceLimitError,
    TraceParseError,
    TraceReadError,
    TraceSource,
)
from swarm_observer.model.trace import AGENT_ID_PATTERN, SourceFile, Trace

from . import factories as f

#: The exact shape R11 pins for the one line the CLI writes to stderr.
CLI_LINE = re.compile(r"^swarm-observer: [a-z_]+: [^\n]*$")

DEFAULTS = IngestLimits()


def load(paths: list[Path], limits: IngestLimits | None = None) -> Trace:
    return ClaudeCodeSource(read_sidecars=False).load(paths, limits or DEFAULTS)


def one_good_record() -> dict[str, object]:
    return f.assistant("a1", usage_block=f.usage(input_tokens=1))


class TestAdapterSeamR3:
    """R3: one protocol method, a registry keyed by slug, normalized types."""

    def test_r3_the_protocol_has_exactly_one_method(self) -> None:
        """R3: ``TraceSource`` is ``load(paths, limits) -> Trace`` and nothing else."""
        assert getattr(TraceSource, "_is_protocol", False) is True
        members = {name for name in vars(TraceSource) if not name.startswith("_")}
        assert members == {"load"}

    def test_r3_the_protocol_signature_is_the_pinned_one(self) -> None:
        """R3: normalized types on both sides — ``Sequence[Path]``, ``IngestLimits``, ``Trace``."""
        import inspect

        signature = inspect.signature(TraceSource.load)
        assert list(signature.parameters) == ["self", "paths", "limits"]
        assert signature.parameters["paths"].annotation == "Sequence[Path]"
        assert signature.parameters["limits"].annotation == "IngestLimits"
        assert signature.return_annotation == "Trace"

    def test_r3_the_v1_adapter_satisfies_the_protocol(self) -> None:
        """R3: ``ingest.claude_code`` is the only implementation in v1."""
        import inspect

        source = ClaudeCodeSource()
        assert callable(source.load)
        assert list(inspect.signature(source.load).parameters) == ["paths", "limits"]
        assert adapter_slugs() == (ADAPTER_SLUG,)

    def test_r3_the_registry_is_a_slug_keyed_dict_of_sources(self) -> None:
        """R3: the CLI selects an adapter through ``dict[str, TraceSource]``."""
        assert isinstance(ADAPTERS, dict)
        assert set(ADAPTERS) == {ADAPTER_SLUG}
        assert callable(ADAPTERS[ADAPTER_SLUG].load)

    def test_r3_build_adapter_returns_a_fresh_configured_instance(self) -> None:
        """R3: per-run options never mutate the shared registry instance."""
        configured = build_adapter(ADAPTER_SLUG, no_previews=True, read_sidecars=False)
        assert configured is not ADAPTERS[ADAPTER_SLUG]
        assert isinstance(configured, ClaudeCodeSource)
        assert configured.no_previews is True
        assert ADAPTERS[ADAPTER_SLUG].no_previews is False  # type: ignore[attr-defined]

    def test_r3_an_unknown_slug_raises_key_error_not_a_trace_error(self) -> None:
        """R3: naming a missing adapter is a mistake in the command, not the trace."""
        with pytest.raises(KeyError):
            build_adapter("otel_spans")

    def test_r3_the_adapter_returns_the_normalized_type(self, tmp_path: Path) -> None:
        """R3: normalized types on both sides of the seam."""
        path = f.write_trace(tmp_path, [one_good_record()])
        trace = ADAPTERS[ADAPTER_SLUG].load([path], DEFAULTS)
        assert isinstance(trace, Trace)
        assert trace.adapter == ADAPTER_SLUG

    def test_r3_ingest_limits_is_frozen_and_forbids_extras(self) -> None:
        """R3: ``IngestLimits`` is a frozen model, like everything else pinned."""
        assert IngestLimits.model_config["frozen"] is True
        assert IngestLimits.model_config["extra"] == "forbid"
        limits = IngestLimits()
        assert (limits.max_file_bytes, limits.max_line_bytes) == (268_435_456, 8_388_608)
        assert (limits.max_records, limits.max_files) == (2_000_000, 64)


class TestFailClosedInputClassesR11:
    """R11: every fatal input class raises a sanitized ``TraceError``."""

    def _fatal(self, path: Path, limits: IngestLimits | None = None) -> TraceError:
        with pytest.raises(TraceError) as raised:
            load([path], limits)
        return raised.value

    def test_r11_truncated_json_line(self, tmp_path: Path) -> None:
        """R11: a line that is not valid JSON is fatal."""
        path = tmp_path / "agent-1.jsonl"
        path.write_text('{"type":"user"\n', encoding="utf-8")
        assert self._fatal(path).code == "invalid_json"

    def test_r11_non_object_line(self, tmp_path: Path) -> None:
        """R11: valid JSON that is not an object is fatal."""
        path = tmp_path / "agent-1.jsonl"
        path.write_text("[1,2,3]\n", encoding="utf-8")
        assert self._fatal(path).code == "not_an_object"

    def test_r11_bare_scalar_line(self, tmp_path: Path) -> None:
        """R11: a bare JSON scalar is not a record either."""
        path = tmp_path / "agent-1.jsonl"
        path.write_text('"just a string"\n', encoding="utf-8")
        assert self._fatal(path).code == "not_an_object"

    def test_r11_oversized_line(self, tmp_path: Path) -> None:
        """R11: a line is rejected *before* parsing when it exceeds the cap."""
        path = f.write_trace(tmp_path, [one_good_record()])
        error = self._fatal(path, IngestLimits(max_line_bytes=16))
        assert error.code == "line_too_long"
        assert error.limit == 16

    def test_r11_oversized_line_with_no_trailing_newline(self, tmp_path: Path) -> None:
        """R11: the cap holds for a final line the file never terminates."""
        path = tmp_path / "agent-1.jsonl"
        path.write_text(json.dumps(one_good_record()), encoding="utf-8")
        assert self._fatal(path, IngestLimits(max_line_bytes=8)).code == "line_too_long"

    def test_r11_oversized_file(self, tmp_path: Path) -> None:
        """R11: the file cap names the limit it hit."""
        path = f.write_trace(tmp_path, [one_good_record()])
        error = self._fatal(path, IngestLimits(max_file_bytes=32))
        assert error.code == "file_too_large"
        assert error.limit == 32

    def test_r11_the_byte_caps_are_exact_at_their_boundary(self, tmp_path: Path) -> None:
        """R11: ``max_line_bytes`` and ``max_file_bytes`` reject at ``limit + 1``, not before.

        Review pin. Loosening either comparison by one byte left the whole suite
        green: every existing cap test sets the limit far below the payload, so
        it proves the cap fires *somewhere* and says nothing about *where*. A cap
        that is a byte off is how a hostile 40 GB line gets through, and how a
        legitimate transcript gets rejected.
        """
        path = tmp_path / "agent-1.jsonl"
        line = json.dumps(one_good_record())
        path.write_text(line + "\n", encoding="utf-8")
        line_bytes = len(line.encode("utf-8"))
        file_bytes = path.stat().st_size

        assert load([path], IngestLimits(max_line_bytes=line_bytes)).spans, "exactly at the cap"
        with pytest.raises(TraceLimitError) as raised:
            load([path], IngestLimits(max_line_bytes=line_bytes - 1))
        assert raised.value.code == "line_too_long"

        assert load([path], IngestLimits(max_file_bytes=file_bytes)).spans, "exactly at the cap"
        with pytest.raises(TraceLimitError) as raised:
            load([path], IngestLimits(max_file_bytes=file_bytes - 1))
        assert raised.value.code == "file_too_large"

    def test_r11_the_line_cap_is_exact_when_the_file_is_read_in_chunks(
        self, tmp_path: Path
    ) -> None:
        """R11: the streaming buffer check has the same boundary as the per-line one.

        The cap is enforced twice — once on a completed line and once on the
        buffer between chunks — and only the second bounds memory on a file with
        no newline in it at all. Both must agree, or a payload sized between them
        behaves differently depending on where a chunk happens to end.
        """
        path = tmp_path / "agent-1.jsonl"
        payload = "x" * 4_096
        path.write_text(payload, encoding="utf-8")  # no trailing newline at all
        with pytest.raises(TraceLimitError) as raised:
            load([path], IngestLimits(max_line_bytes=len(payload) - 1))
        assert raised.value.code == "line_too_long"
        # At the cap the line is read, and then fails as JSON rather than as a limit.
        with pytest.raises(TraceParseError) as parse_error:
            load([path], IngestLimits(max_line_bytes=len(payload)))
        assert parse_error.value.code == "invalid_json"

    def test_r11_record_cap(self, tmp_path: Path) -> None:
        """R11: ``max_records`` bounds the whole input set."""
        path = f.write_trace(tmp_path, [f.assistant("a1"), f.assistant("a2", request_id="r2")])
        assert self._fatal(path, IngestLimits(max_records=1)).code == "too_many_records"

    def test_r11_file_cap(self, tmp_path: Path) -> None:
        """R11: ``max_files`` is checked before anything is opened."""
        first = f.write_trace(tmp_path, [one_good_record()], name="agent-1")
        second = f.write_trace(tmp_path, [one_good_record()], name="agent-2")
        with pytest.raises(TraceLimitError) as raised:
            load([first, second], IngestLimits(max_files=1))
        assert raised.value.code == "too_many_files"

    def test_r11_duplicate_uuid_within_one_file(self, tmp_path: Path) -> None:
        """R11: a repeated ``uuid`` in one file is fatal."""
        path = f.write_trace(tmp_path, [f.assistant("same"), f.assistant("same", request_id="r2")])
        assert self._fatal(path).code == "duplicate_uuid"

    @pytest.mark.parametrize(
        "timestamp",
        ["not-a-time", "2026-09-09T10:00:00", "2026-09-09", "", "2026-13-45T99:99:99Z", "1757412"],
    )
    def test_r11_bad_timestamp(self, tmp_path: Path, timestamp: str) -> None:
        """R11: a timestamp that is not RFC 3339 with an offset is fatal."""
        record = dict(one_good_record())
        record["timestamp"] = timestamp
        path = f.write_trace(tmp_path, [record])
        assert self._fatal(path).code in {"bad_timestamp", "missing_field"}

    def test_r11_missing_required_field(self, tmp_path: Path) -> None:
        """R11: a record without ``type``, ``uuid`` or ``timestamp`` is fatal."""
        for field in ("type", "uuid", "timestamp"):
            record = dict(one_good_record())
            record.pop(field)
            path = f.write_trace(tmp_path, [record], name=f"agent-{field}")
            error = self._fatal(path)
            assert error.code == "missing_field"
            assert error.note == f"field {field}"

    def test_r11_message_present_but_not_an_object(self, tmp_path: Path) -> None:
        """R11: an assistant/user record with a scalar ``message`` is fatal."""
        record = dict(one_good_record())
        record["message"] = "a string, not an object"
        path = f.write_trace(tmp_path, [record])
        assert self._fatal(path).code == "bad_message"

    def test_r11_invalid_utf8(self, tmp_path: Path) -> None:
        """R11: a line that is not UTF-8 is fatal, and its bytes never surface."""
        path = tmp_path / "agent-1.jsonl"
        path.write_bytes(b'{"type":"user","uuid":"u","timestamp":"x","bad":"\xff\xfe"}\n')
        error = self._fatal(path)
        assert error.code == "invalid_encoding"
        assert "\xff" not in error.cli_line

    def test_r11_symlink_pointing_outside_the_named_inputs(self, tmp_path: Path) -> None:
        """R11: a symlink escaping the named input set is fatal."""
        target = f.write_trace(tmp_path / "elsewhere", [one_good_record()], name="secret")
        link = tmp_path / "agent-1.jsonl"
        link.symlink_to(target)
        with pytest.raises(TraceReadError) as raised:
            load([link])
        assert raised.value.code == "symlink_escape"
        assert "secret" not in raised.value.cli_line

    def test_r11_symlink_to_a_named_input_is_accepted(self, tmp_path: Path) -> None:
        """R11: the symlink rule is not simply "reject every symlink"."""
        target = f.write_trace(tmp_path, [one_good_record()], name="agent-1")
        link = tmp_path / "agent-2.jsonl"
        link.symlink_to(target)
        assert resolve_inputs([target, link], DEFAULTS) == (target, link)

    def test_r11_missing_path(self, tmp_path: Path) -> None:
        """R11: an unreadable path is fatal and names only a basename."""
        with pytest.raises(TraceReadError) as raised:
            load([tmp_path / "no-such-agent.jsonl"])
        assert raised.value.code == "unreadable_path"
        assert raised.value.cli_line.endswith("no-such-agent.jsonl")

    def test_r11_directory_is_not_a_regular_file(self, tmp_path: Path) -> None:
        """R11: a path that is not a regular file is fatal."""
        with pytest.raises(TraceReadError) as raised:
            load([tmp_path])
        assert raised.value.code == "not_a_regular_file"

    def test_r11_fifo_is_not_a_regular_file(self, tmp_path: Path) -> None:
        """R11: a FIFO would block forever; it is rejected as not a regular file."""
        fifo = tmp_path / "agent-1.jsonl"
        os.mkfifo(fifo)
        with pytest.raises(TraceReadError) as raised:
            load([fifo])
        assert raised.value.code == "not_a_regular_file"

    def test_r11_a_mode_000_file_is_unreadable_unless_the_process_is_root(
        self, tmp_path: Path
    ) -> None:
        """R11: permission denial fails closed where the platform enforces it.

        Asserted in both directions rather than skipped: as an unprivileged user
        the read is denied and must fail closed; as root the file really is
        readable, and pretending otherwise would make this a test that passes by
        not running.
        """
        path = f.write_trace(tmp_path, [one_good_record()])
        os.chmod(path, 0o000)
        try:
            if os.access(path, os.R_OK):
                assert os.geteuid() == 0, "unreadable file was readable by a non-root process"
                assert load([path]).adapter == ADAPTER_SLUG
            else:
                with pytest.raises(TraceReadError) as raised:
                    load([path])
                assert raised.value.code == "unreadable_path"
        finally:
            os.chmod(path, 0o644)

    def test_r11_duplicate_basename_across_directories(self, tmp_path: Path) -> None:
        """R11: two files with one basename would make provenance ambiguous."""
        first = f.write_trace(tmp_path / "a", [f.assistant("a1")], name="agent-1")
        second = f.write_trace(tmp_path / "b", [f.assistant("a2")], name="agent-1")
        with pytest.raises(TraceReadError) as raised:
            load([first, second])
        assert raised.value.code == "duplicate_input"

    @pytest.mark.parametrize("length", [201, 250])
    def test_r11_an_over_long_basename_fails_closed(self, tmp_path: Path, length: int) -> None:
        """R11, R2: a basename longer than ``SourceFile.name`` allows is fatal, not a crash.

        Review finding. ``SourceFile.name`` is capped at 200 characters (R2) and
        a filesystem allows 255, so the cap was enforced by pydantic several
        layers into ``load()`` as an unsanitized ``ValidationError`` quoting the
        path. R11 admits exactly one outcome here: a sanitized ``TraceError``.
        """
        name = "a" * (length - len(".jsonl")) + ".jsonl"
        path = tmp_path / name
        path.write_text("", encoding="utf-8")
        with pytest.raises(TraceReadError) as raised:
            load([path])
        assert raised.value.code == "name_too_long"
        assert name not in raised.value.cli_line, "the rejected name must not be echoed back"

    def test_r11_a_basename_at_the_cap_is_accepted(self, tmp_path: Path) -> None:
        """R11: the guard rejects only what the model would reject, one byte over."""
        name = "a" * (200 - len(".jsonl")) + ".jsonl"
        path = f.write_trace(tmp_path, [f.assistant("a1", usage_block=f.usage())], name=name[:-6])
        assert len(path.name) == 200
        assert load([path]).source_files[0].name == path.name

    def test_r11_no_input_paths_at_all(self) -> None:
        """R11: an empty input set is fatal, not an empty trace."""
        with pytest.raises(TraceReadError) as raised:
            load([])
        assert raised.value.code == "empty_input"

    def _nested_line(self, path: Path, opener: str, depth: int) -> None:
        """One valid ``user`` record whose ``deep`` value nests ``depth`` levels."""
        closer, innermost = ("]", "") if opener == "[" else ("}", "1")
        path.write_text(
            '{"type":"user","uuid":"u","timestamp":"2026-09-09T10:00:00.000Z",'
            '"message":{"content":"hi"},"deep":'
            + opener * depth
            + innermost
            + closer * depth
            + "}\n",
            encoding="utf-8",
        )

    @pytest.mark.parametrize("depth", [201, 1_000, 2_000, 5_000, 50_000])
    @pytest.mark.parametrize("opener", ["[", '{"k":'])
    def test_r11_a_deeply_nested_json_line_must_fail_closed(
        self, tmp_path: Path, depth: int, opener: str
    ) -> None:
        """R11: a nesting bomb is fatal on every interpreter, not just the local one.

        The payload is a few kilobytes — far under ``max_line_bytes`` — so no
        byte cap catches it. The first version of this fix relied on
        ``json.loads`` raising ``RecursionError``, which is not a contract: it
        held on CPython 3.11 and silently did not on 3.12, where depths 1,000
        through 5,000 parse happily. CI's version matrix caught it; a local
        single-version run could not. ``max_json_depth`` decides now, so the
        outcome is the same everywhere.
        """
        path = tmp_path / "agent-1.jsonl"
        self._nested_line(path, opener, depth)
        assert path.stat().st_size < DEFAULTS.max_line_bytes
        with pytest.raises(TraceLimitError) as raised:
            load([path])
        assert raised.value.code == "json_too_deep"
        assert raised.value.limit == DEFAULTS.max_json_depth
        assert raised.value.cli_line.count("\n") == 0
        assert opener not in raised.value.detail

    @pytest.mark.parametrize("opener", ["[", '{"k":'])
    def test_r11_the_json_depth_cap_is_exact_at_its_boundary(
        self, tmp_path: Path, opener: str
    ) -> None:
        """R11: the cap admits exactly ``max_json_depth`` levels and refuses one more."""
        path = tmp_path / "agent-1.jsonl"
        limits = IngestLimits(max_json_depth=8)
        # The record object itself is one level, so `deep` may nest 7 more.
        self._nested_line(path, opener, 7)
        assert load([path], limits).spans[0].kind == "user_message"
        self._nested_line(path, opener, 8)
        with pytest.raises(TraceLimitError) as raised:
            load([path], limits)
        assert raised.value.code == "json_too_deep"
        assert raised.value.limit == 8

    @pytest.mark.parametrize("recursion_limit", [400, 1_000, 20_000])
    def test_r11_the_depth_verdict_does_not_depend_on_the_recursion_limit(
        self, tmp_path: Path, recursion_limit: int
    ) -> None:
        """R11: the guard is interpreter-independent, which is the whole point.

        This is the regression pin for the CI failure. ``sys.getrecursionlimit``
        stands in here for everything that varies between interpreters, builds
        and platforms: whatever it is set to, a bomb is rejected and a legal
        record is accepted, with the same code both times.
        """
        original = sys.getrecursionlimit()
        path = tmp_path / "agent-1.jsonl"
        try:
            sys.setrecursionlimit(recursion_limit)
            self._nested_line(path, "[", 5_000)
            with pytest.raises(TraceLimitError) as raised:
                load([path])
            assert raised.value.code == "json_too_deep"
            self._nested_line(path, "[", 50)
            assert load([path]).spans[0].kind == "user_message"
        finally:
            sys.setrecursionlimit(original)

    def test_r11_a_legally_nested_json_line_still_parses(self, tmp_path: Path) -> None:
        """R11: the depth guard rejects only what breaches the cap."""
        path = tmp_path / "agent-1.jsonl"
        payload = "[" * 50 + "]" * 50
        path.write_text(
            '{"type":"user","uuid":"u","timestamp":"2026-09-09T10:00:00.000Z",'
            '"message":{"content":"hi"},"deep":' + payload + "}\n",
            encoding="utf-8",
        )
        assert load([path]).spans[0].kind == "user_message"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"a":"[[[[[[[[[["}', False),  # brackets inside a string are characters
            ('{"a":"\\""}', False),  # an escaped quote does not end the string
            ('{"a":"\\\\"}', False),  # an escaped backslash does
            ("]]]]]]]]]][[", False),  # leading closers cannot buy extra depth
            ("[[[", True),
            ('{"a":{"b":{"c":1}}}', True),
        ],
    )
    def test_r11_the_depth_scan_is_string_aware(self, text: str, expected: bool) -> None:
        """R11: the bound counts containers, not bracket characters.

        A cap that counted every ``[`` would reject a record whose tool result
        happens to quote a nested structure — a false fail-closed on legitimate
        input, which is as much a defect as missing the bomb.
        """
        assert exceeds_json_depth(text, 2) is expected


class TestToleratedInputClassesR11:
    """R11: input an adversary might try that is *not* fatal, and must not be."""

    def test_r11_an_empty_file_is_an_empty_trace(self, tmp_path: Path) -> None:
        """R11: an empty file breaches no cap and violates no R4 rule."""
        path = tmp_path / "agent-1.jsonl"
        path.write_text("", encoding="utf-8")
        trace = load([path])
        assert trace.spans == ()
        assert trace.source_files[0].records == 0

    def test_r11_blank_lines_are_not_records(self, tmp_path: Path) -> None:
        """R11: a trailing newline is not a record and must not be counted."""
        path = tmp_path / "agent-1.jsonl"
        path.write_text("\n\n" + json.dumps(one_good_record()) + "\n\n\n", encoding="utf-8")
        assert load([path]).source_files[0].records == 1

    def test_r11_crlf_line_endings_are_read(self, tmp_path: Path) -> None:
        """R11: a transcript written on Windows is still a transcript."""
        path = tmp_path / "agent-1.jsonl"
        records = [f.assistant("a1"), f.assistant("a2", request_id="r2")]
        path.write_bytes(b"".join(json.dumps(r).encode() + b"\r\n" for r in records))
        assert load([path]).source_files[0].records == 2

    def test_r11_a_nul_byte_inside_a_json_string_is_read(self, tmp_path: Path) -> None:
        """R11: ``\\u0000`` is legal JSON; the preview flattens it, R4 tolerates it."""
        record = f.assistant("a1", content=[f.text_block("before\x00after")], usage_block=f.usage())
        path = f.write_trace(tmp_path, [record])
        trace = load([path])
        assert "\x00" not in trace.model_dump_json()
        assert trace.spans[0].text_preview == "before after"

    def test_r11_moderately_nested_json_is_read(self, tmp_path: Path) -> None:
        """R11: nesting is not by itself hostile."""
        record = f.assistant(
            "a1",
            content=[f.tool_use_block("t1", "Deep", json.loads("[" * 50 + "]" * 50))],
            usage_block=f.usage(),
        )
        assert len(load([f.write_trace(tmp_path, [record])]).spans) == 2

    def test_r11_a_byte_order_mark_fails_closed_rather_than_silently_shifting(
        self, tmp_path: Path
    ) -> None:
        """R11: a BOM makes the first line invalid JSON, which is a fatal condition."""
        path = tmp_path / "agent-1.jsonl"
        path.write_bytes("﻿".encode() + json.dumps(one_good_record()).encode() + b"\n")
        with pytest.raises(TraceParseError) as raised:
            load([path])
        assert raised.value.code == "invalid_json"


class TestSanitizedErrorLineR11:
    """R11: the one line the CLI prints can never carry a byte of the file."""

    def test_r11_the_cli_line_has_the_pinned_shape(self) -> None:
        """R11: ``swarm-observer: <code>: <sanitized detail>``."""
        error = TraceParseError("invalid_json", source="agent-1.jsonl", line=5)
        assert CLI_LINE.match(error.cli_line)
        assert error.cli_line == "swarm-observer: invalid_json: agent-1.jsonl line 5"

    def test_r11_every_taxonomy_code_produces_the_pinned_shape(self) -> None:
        """R11: no code in the taxonomy escapes the format."""
        classes = (TraceReadError, TraceLimitError, TraceParseError)
        seen: set[str] = set()
        for cls in classes:
            for code in sorted(cls.CODES):
                seen.add(code)
                line = cls(code, source="agent-1.jsonl", line=1, limit=2, note="a note").cli_line
                assert CLI_LINE.match(line), line
        assert seen == set(TRACE_ERROR_CODES)

    def test_r11_a_code_outside_a_subclass_taxonomy_is_refused(self) -> None:
        """R11: the taxonomy is closed, so a mis-classification is caught at the raise."""
        with pytest.raises(ValueError, match="does not own the code"):
            TraceParseError("file_too_large")

    def test_r11_a_path_can_never_be_passed_as_the_source(self) -> None:
        """R11: only a basename may appear, enforced at the constructor."""
        for bad in ("/home/user/agent-1.jsonl", "dir\\agent-1.jsonl"):
            with pytest.raises(ValueError, match="basename"):
                TraceReadError("unreadable_path", source=bad)

    @pytest.mark.parametrize(
        "hostile",
        [
            "</script><script>alert(1)</script>",
            "line one\nline two",
            "\x00\x1b[31m",
            "secret=hunter2",
            "‮EVIL",
        ],
    )
    def test_r11_trace_content_cannot_be_smuggled_into_a_note(self, hostile: str) -> None:
        """R11: the note alphabet refuses anything that is not enumerated text."""
        with pytest.raises(ValueError, match="enumerated text"):
            TraceParseError("invalid_json", note=hostile)

    def test_r11_the_detail_is_always_one_printable_line(self) -> None:
        """R11: a odd-but-legal basename still yields exactly one line."""
        error = TraceReadError("unreadable_path", source="a\tb\nc.jsonl")
        assert "\n" not in error.cli_line and "\t" not in error.cli_line
        assert CLI_LINE.match(error.cli_line)

    def test_r11_the_detail_is_capped(self) -> None:
        """R11: a very long basename cannot flood a terminal."""
        error = TraceReadError("unreadable_path", source="x" * 5_000 + ".jsonl")
        assert len(error.detail) <= 200


class TestCliFailClosedR11:
    """R11: the entry point turns a ``TraceError`` into exit 2 and one line."""

    def test_r11_main_writes_exactly_one_line_and_exits_two(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R11: no traceback ever reaches stderr."""
        error = TraceParseError("invalid_json", source="agent-1.jsonl", line=5)

        def boom(argv: object, **kwargs: object) -> int:
            raise error

        monkeypatch.setattr("swarm_observer.cli.main.run", boom)
        stderr = io.StringIO()
        assert main(["schema"], stderr=stderr) == EXIT_FAIL_CLOSED
        assert stderr.getvalue() == error.cli_line + "\n"
        assert stderr.getvalue().count("\n") == 1
        assert "Traceback" not in stderr.getvalue()

    def test_r11_a_successful_command_writes_nothing_to_stderr(self) -> None:
        """R11: diagnostics only exist when something went wrong."""
        stderr = io.StringIO()
        assert main(["schema"], stderr=stderr) == 0
        assert stderr.getvalue() == ""


class TestSourceHashingR5:
    """R5: per-file SHA-256 provenance and the ids derived from it."""

    def test_r5_sha256_is_the_files_own_digest(self, tmp_path: Path) -> None:
        """R5: the hash is taken over the bytes as read, not a reconstruction."""
        import hashlib

        path = f.write_trace(tmp_path, [f.assistant("a1"), f.assistant("a2", request_id="r2")])
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        source, records = read_jsonl(path, 0, DEFAULTS, records_budget=10)
        assert source.sha256 == expected
        assert source.bytes == path.stat().st_size
        assert source.records == len(records) == 2

    def test_r5_sha256_survives_crlf_and_a_missing_trailing_newline(self, tmp_path: Path) -> None:
        """R5: line splitting must not change the digest of the file."""
        import hashlib

        path = tmp_path / "agent-1.jsonl"
        path.write_bytes(json.dumps(one_good_record()).encode() + b"\r\n")
        source, _ = read_jsonl(path, 0, DEFAULTS, records_budget=10)
        assert source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

        naked = tmp_path / "agent-2.jsonl"
        naked.write_bytes(json.dumps(one_good_record()).encode())
        source2, records2 = read_jsonl(naked, 1, DEFAULTS, records_budget=10)
        assert source2.sha256 == hashlib.sha256(naked.read_bytes()).hexdigest()
        assert len(records2) == 1

    def test_r5_sha256_is_stable_across_reads(self, tmp_path: Path) -> None:
        """R5: hashing is a pure function of the bytes."""
        path = f.write_trace(tmp_path, [one_good_record()])
        digests = {read_jsonl(path, 0, DEFAULTS, records_budget=10)[0].sha256 for _ in range(5)}
        assert len(digests) == 1

    def test_r5_trace_id_is_16_lowercase_hex_over_sorted_name_and_hash(self) -> None:
        """R5: the pinned construction, computed independently here."""
        import hashlib

        files = (
            SourceFile(name="agent-2.jsonl", sha256="b" * 64, bytes=1, records=1),
            SourceFile(name="agent-1.jsonl", sha256="a" * 64, bytes=1, records=1),
        )
        payload = f"agent-1.jsonl:{'a' * 64}\nagent-2.jsonl:{'b' * 64}"
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        assert compute_trace_id(files) == expected
        assert compute_trace_id(tuple(reversed(files))) == expected

    def test_r5_digest_id_joins_parts_with_a_pipe(self) -> None:
        """R5: the ``span_id`` construction is a pure function of its parts.

        The middle assertion used to read
        ``assert digest_id("a|b") != digest_id("a", "b") or True``, with the
        comment "documents the joiner". ``X or True`` is true for every ``X``,
        so it documented nothing and could not fail — this project's signature
        defect, inside the suite that exists to catch it. Found by the
        increment-4 tester while scanning for the same shape elsewhere; reported
        as **BUG-11**.

        The property it was reaching for is real and is now asserted in the
        direction it actually holds: ``"|"`` is an ambiguous joiner, so two
        different part lists can produce one digest. That is harmless for R5,
        whose parts are a 16-hex trace id, an ``AGENT_ID_PATTERN`` agent id, a
        decimal seq and a ``SpanKind`` — none of which can contain a ``|`` — and
        the assertion says exactly that rather than pretending otherwise.
        """
        import hashlib

        assert digest_id("a", "b") == hashlib.sha256(b"a|b").hexdigest()[:16]
        assert digest_id("a|b") == digest_id("a", "b"), (
            "the joiner is ambiguous; R5 is safe because no part can contain a pipe"
        )
        assert "|" not in AGENT_ID_PATTERN.strip("^$")
        assert len(digest_id("x")) == 16


class TestAtomicWritesR11:
    """R11: outputs are staged and renamed, never opened for writing in place."""

    def test_r11_a_successful_write_replaces_the_destination(self, tmp_path: Path) -> None:
        """R11: the happy path still writes the file."""
        destination = tmp_path / "report.html"
        destination.write_text("OLD", encoding="utf-8")
        atomic_write_text(destination, "NEW")
        assert destination.read_text(encoding="utf-8") == "NEW"
        assert list(tmp_path.glob(".report.html.*")) == []

    def test_r11_a_failure_leaves_the_existing_output_untouched(self, tmp_path: Path) -> None:
        """R11: no output file is created or truncated when the run fails."""
        good = tmp_path / "report.html"
        good.write_text("ORIGINAL", encoding="utf-8")
        with pytest.raises(TypeError):
            atomic_write_texts({good: "NEW", tmp_path / "report.json": None})  # type: ignore[dict-item]
        assert good.read_text(encoding="utf-8") == "ORIGINAL"
        assert not (tmp_path / "report.json").exists()

    def test_r11_a_failure_leaves_no_temporary_file_behind(self, tmp_path: Path) -> None:
        """R11: the staging file is cleaned up, so a failed run leaves no litter."""
        with pytest.raises(TypeError):
            atomic_write_texts({tmp_path / "report.json": None})  # type: ignore[dict-item]
        assert list(tmp_path.iterdir()) == []

    def test_r11_a_failing_load_creates_no_files_anywhere(self, tmp_path: Path) -> None:
        """R11: the reader itself never writes; a fatal parse leaves the tree as it was."""
        path = tmp_path / "agent-1.jsonl"
        path.write_text('{"type":"user"\n', encoding="utf-8")
        before = sorted(p.name for p in tmp_path.iterdir())
        with pytest.raises(TraceError):
            load([path])
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_r11_written_files_get_a_fixed_readable_mode(self, tmp_path: Path) -> None:
        """R11: a report is meant to be shared; a 0600 temp mode must not survive."""
        destination = tmp_path / "report.html"
        atomic_write_text(destination, "x")
        assert destination.stat().st_mode & 0o777 == 0o644


def test_r11_load_input_orders_files_by_basename(tmp_path: Path) -> None:
    """R11, R6: the reader hands the adapter files in canonical order."""
    second = f.write_trace(tmp_path, [f.assistant("b1")], name="agent-2")
    first = f.write_trace(tmp_path, [f.assistant("a1")], name="agent-1")
    loaded = load_input([second, first], DEFAULTS)
    assert [source.name for source in loaded.source_files] == ["agent-1.jsonl", "agent-2.jsonl"]
    assert [record.file_index for record in loaded.records] == [0, 1]
