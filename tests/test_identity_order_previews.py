"""R5 to R8: ids, canonical order, tool-input digests and previews.

These four requirements are what make every later finding id stable, so they
are tested as *properties* rather than as example outputs wherever a property
exists: ``trace_id`` is independent of the path and of the argument order;
``seq`` is a function of file order and never of a timestamp; a digest is a
function of the canonical JSON and not of key order; a preview is one line,
bounded, and printable no matter what the trace contains.

R8's ``--no-previews`` mode gets its own class, because A10 makes it a claim
about the *process* — the hostile bytes must not exist on the model after
ingestion, not merely be omitted at render time.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from swarm_observer.ingest.claude_code.mapper import (
    ClaudeCodeSource,
    safe_agent_id,
    tool_input_digest,
)
from swarm_observer.ingest.reader import compute_trace_id, digest_id
from swarm_observer.ingest.source import IngestLimits
from swarm_observer.ingest.text import ELLIPSIS, PREVIEW_LIMIT, canonical_json, preview, slug
from swarm_observer.model.trace import PREVIEW_MAX_CHARS, Trace

from . import factories as f

DEFAULTS = IngestLimits()


def load(paths: list[Path], **kwargs: object) -> Trace:
    return ClaudeCodeSource(read_sidecars=False, **kwargs).load(paths, DEFAULTS)  # type: ignore[arg-type]


def a_conversation() -> list[dict[str, object]]:
    """A small but complete trace: a turn, a response, a tool call and its result."""
    return [
        f.user("u1", timestamp="2026-09-09T10:00:00.000Z"),
        f.assistant(
            "a1",
            timestamp="2026-09-09T10:00:01.000Z",
            content=[f.text_block("working"), f.tool_use_block("t1", "Bash", {"cmd": "ls"})],
            usage_block=f.usage(input_tokens=5, output_tokens=7),
            stop_reason="tool_use",
        ),
        f.user(
            "u2",
            timestamp="2026-09-09T10:00:02.000Z",
            content=[f.tool_result_block("t1", "file-a\nfile-b")],
        ),
    ]


class TestTraceIdentityR5:
    """R5: ids are content-derived, path-free and stable."""

    def test_r5_trace_id_is_16_lowercase_hex(self, tmp_path: Path) -> None:
        """R5: the pinned id shape."""
        trace = load([f.write_trace(tmp_path, a_conversation())])
        assert len(trace.trace_id) == 16
        assert set(trace.trace_id) <= set("0123456789abcdef")

    def test_r5_trace_id_ignores_the_directory_the_files_live_in(self, tmp_path: Path) -> None:
        """R5: two analysts on two machines get the same id for the same bytes."""
        here = f.write_trace(tmp_path / "one", a_conversation())
        there = tmp_path / "two" / "agent-1.jsonl"
        there.parent.mkdir()
        shutil.copyfile(here, there)
        assert load([here]).trace_id == load([there]).trace_id

    def test_r5_trace_id_ignores_the_order_paths_were_given(self, tmp_path: Path) -> None:
        """R5: independent of the order paths were passed on the command line."""
        first = f.write_trace(tmp_path, a_conversation(), name="agent-1")
        second = f.write_trace(
            tmp_path, [f.assistant("b1", message_id="m2", request_id="r2")], name="agent-2"
        )
        assert load([first, second]).trace_id == load([second, first]).trace_id

    def test_r5_trace_id_changes_when_content_changes(self, tmp_path: Path) -> None:
        """R5: a content-derived id must actually depend on the content."""
        original = load([f.write_trace(tmp_path / "a", a_conversation())]).trace_id
        altered = a_conversation()
        altered[0]["uuid"] = "u1-changed"
        assert load([f.write_trace(tmp_path / "b", altered)]).trace_id != original

    def test_r5_trace_id_changes_when_a_basename_changes(self, tmp_path: Path) -> None:
        """R5: the basename is part of the hashed payload, by construction."""
        first = load([f.write_trace(tmp_path / "a", a_conversation(), name="agent-1")]).trace_id
        second = load([f.write_trace(tmp_path / "b", a_conversation(), name="agent-9")]).trace_id
        assert first != second

    def test_r5_span_id_is_the_pinned_construction(self, tmp_path: Path) -> None:
        """R5: ``sha256(trace_id|agent_id|seq|kind)[:16]``, recomputed here."""
        trace = load([f.write_trace(tmp_path, a_conversation())])
        for span in trace.spans:
            expected = hashlib.sha256(
                f"{trace.trace_id}|{span.agent_id}|{span.seq}|{span.kind}".encode()
            ).hexdigest()[:16]
            assert span.span_id == expected
            assert span.span_id == digest_id(
                trace.trace_id, span.agent_id, str(span.seq), span.kind
            )

    def test_r5_span_ids_are_unique_within_a_trace(self, tmp_path: Path) -> None:
        """R5: ids are used as anchors, so a collision would be a rendering bug."""
        trace = load([f.write_trace(tmp_path, a_conversation() * 1)])
        ids = [span.span_id for span in trace.spans]
        assert len(set(ids)) == len(ids)

    def test_r5_a_tool_call_parent_is_the_emitting_model_call(self, tmp_path: Path) -> None:
        """R5, R12: the parent link is a span id, never a trace-derived value."""
        trace = load([f.write_trace(tmp_path, a_conversation())])
        model_call = next(span for span in trace.spans if span.kind == "model_call")
        tool_call = next(span for span in trace.spans if span.kind == "tool_call")
        assert tool_call.parent_span_id == model_call.span_id
        assert model_call.parent_span_id is None

    def test_r5_agent_id_defaults_to_root_when_absent(self) -> None:
        """R5: ``agentId`` when present, else the literal ``root``."""
        assert safe_agent_id(None) == "root"
        assert safe_agent_id("") == "root"
        assert safe_agent_id("agent-a1b2") == "agent-a1b2"

    @pytest.mark.parametrize(
        "hostile",
        [
            "</script><script>alert(1)</script>",
            '" onerror=alert(1) x="',
            "‮EVIL",
            "a b",
            "x" * 200,
            "../../etc/passwd",
        ],
    )
    def test_r5_a_hostile_agent_id_never_reaches_the_model(self, hostile: str) -> None:
        """R5: "ids never embed trace content", made literal by a digest fallback."""
        derived = safe_agent_id(hostile)
        assert derived.startswith("agent_")
        assert len(derived) == 6 + 12
        assert hostile not in derived
        assert safe_agent_id(hostile) == derived  # same agent, same id

    def test_r5_ids_are_safe_in_an_attribute_value(self, tmp_path: Path) -> None:
        """R5: no id contains a character that could break out of markup."""
        record = f.assistant(
            "a1", agent_id='"><script>x</script>', usage_block=f.usage(), content=[]
        )
        trace = load([f.write_trace(tmp_path, [record])])
        for value in [trace.trace_id, *(span.span_id for span in trace.spans)]:
            assert set(value) <= set("0123456789abcdef")
        for span in trace.spans:
            assert set(span.agent_id) <= set(
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            )

    def test_r5_compute_trace_id_is_order_insensitive_at_the_helper(self) -> None:
        """R5: the helper sorts by name, so the caller need not."""
        from swarm_observer.model.trace import SourceFile

        files = [
            SourceFile(name="b.jsonl", sha256="b" * 64, bytes=1, records=1),
            SourceFile(name="a.jsonl", sha256="a" * 64, bytes=1, records=1),
        ]
        assert compute_trace_id(files) == compute_trace_id(list(reversed(files)))


class TestCanonicalOrderR6:
    """R6: file order is authoritative; timestamps are advisory."""

    def test_r6_seq_is_zero_based_contiguous_and_ascending(self, tmp_path: Path) -> None:
        """R6: ``seq`` is a position in the canonical order."""
        trace = load([f.write_trace(tmp_path, a_conversation())])
        assert [span.seq for span in trace.spans] == list(range(len(trace.spans)))

    def test_r6_order_is_file_index_then_line_number(self, tmp_path: Path) -> None:
        """R6: records from ``agent-1`` precede records from ``agent-2``."""
        first = f.write_trace(
            tmp_path,
            [f.user("u1", agent_id="a1", timestamp="2026-09-09T23:00:00.000Z")],
            name="agent-1",
        )
        second = f.write_trace(
            tmp_path,
            [f.user("u2", agent_id="a2", timestamp="2026-09-09T01:00:00.000Z")],
            name="agent-2",
        )
        trace = load([first, second])
        assert [span.agent_id for span in trace.spans] == ["a1", "a2"]

    def test_r6_a_later_timestamp_never_reorders_a_trace(self, tmp_path: Path) -> None:
        """R6: a timestamp sort would make every finding id depend on clock skew."""
        records = [
            f.user("u1", timestamp="2026-09-09T10:00:05.000Z"),
            f.user("u2", timestamp="2026-09-09T10:00:01.000Z"),
            f.user("u3", timestamp="2026-09-09T10:00:03.000Z"),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        starts = [span.start for span in trace.spans]
        assert starts != sorted(s for s in starts if s is not None)

    def test_r6_an_out_of_order_timestamp_is_counted(self, tmp_path: Path) -> None:
        """R6: the clock disagreement is reported, not acted on."""
        records = [
            f.user("u1", timestamp="2026-09-09T10:00:05.000Z"),
            f.user("u2", timestamp="2026-09-09T10:00:01.000Z"),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        counts = {w.code: w.count for w in trace.warnings}
        assert counts["timestamp_out_of_order"] == 1

    def test_r6_the_whole_trace_is_stable_under_input_permutation(self, tmp_path: Path) -> None:
        """R6: permuting the command line cannot change one byte of the trace."""
        names = ["agent-1", "agent-2", "agent-3"]
        paths = [
            f.write_trace(
                tmp_path,
                [
                    f.user(f"u-{name}", agent_id=name.replace("agent-", "ag")),
                    f.assistant(
                        f"a-{name}",
                        agent_id=name.replace("agent-", "ag"),
                        message_id=f"m-{name}",
                        request_id=f"r-{name}",
                        usage_block=f.usage(input_tokens=1),
                    ),
                ],
                name=name,
            )
            for name in names
        ]
        baseline = load(paths).model_dump_json()
        for permutation in (
            [paths[2], paths[0], paths[1]],
            list(reversed(paths)),
            [paths[1], paths[2], paths[0]],
        ):
            assert load(permutation).model_dump_json() == baseline

    def test_r6_agent_index_follows_first_appearance_in_canonical_order(
        self, tmp_path: Path
    ) -> None:
        """R6: ``agent_index`` is assigned in canonical order of first appearance."""
        records = [
            f.user("u1", agent_id="zeta"),
            f.user("u2", agent_id="alpha"),
            f.user("u3", agent_id="zeta"),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        assert [agent.agent_id for agent in trace.agents] == ["zeta", "alpha"]
        assert [agent.agent_index for agent in trace.agents] == [0, 1]
        assert trace.agents[0].span_seqs == (0, 2)

    def test_r6_a_negative_duration_is_clamped_and_counted(self, tmp_path: Path) -> None:
        """R6: a computed duration is never negative in the model."""
        records = [
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:10.000Z",
                content=[f.text_block("one")],
                usage_block=f.usage(),
            ),
            f.assistant(
                "a2",
                timestamp="2026-09-09T10:00:00.000Z",
                content=[f.text_block("two")],
                usage_block=f.usage(output_tokens=3),
            ),
        ]
        trace = load([f.write_trace(tmp_path, records)])
        counts = {w.code: w.count for w in trace.warnings}
        assert counts["negative_duration"] >= 1
        for span in trace.spans:
            if span.start is not None and span.end is not None:
                assert span.end >= span.start


class TestToolInputDigestR7:
    """R7: the detectors' only view of tool arguments."""

    def test_r7_the_digest_is_the_pinned_construction(self) -> None:
        """R7: 16 hex over ``json.dumps(sort_keys, ensure_ascii, compact)``."""
        payload = {"b": 1, "a": [2, 3]}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        assert canonical == '{"a":[2,3],"b":1}'
        assert tool_input_digest(payload) == hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def test_r7_the_digest_ignores_key_order(self) -> None:
        """R7: two orderings of one argument set are one call."""
        assert tool_input_digest({"a": 1, "b": 2}) == tool_input_digest({"b": 2, "a": 1})

    def test_r7_the_digest_distinguishes_different_arguments(self) -> None:
        """R7: a duplication detector built on this must see a real difference."""
        assert tool_input_digest({"cmd": "ls"}) != tool_input_digest({"cmd": "rm"})

    def test_r7_an_absent_input_digests_the_literal_null(self) -> None:
        """R7: "a ``null``/absent tool input digests the literal ``null``"."""
        expected = hashlib.sha256(b"null").hexdigest()[:16]
        assert tool_input_digest(None) == expected
        assert canonical_json(None) == "null"

    def test_r7_the_digest_is_ascii_only_for_non_ascii_arguments(self) -> None:
        """R7: ``ensure_ascii`` removes every encoding dependency from the digest."""
        canonical = canonical_json({"path": "café/日本"})
        canonical.encode("ascii")
        assert "\\u" in canonical

    def test_r7_a_span_carries_the_digest_of_its_own_input(self, tmp_path: Path) -> None:
        """R7: the digest on the span is the digest of the recorded input."""
        record = f.assistant(
            "a1",
            content=[f.tool_use_block("t1", "Bash", {"cmd": "ls -la", "cwd": "/tmp"})],
            usage_block=f.usage(),
        )
        trace = load([f.write_trace(tmp_path, [record])])
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        assert tool.tool_input_digest == tool_input_digest({"cmd": "ls -la", "cwd": "/tmp"})

    def test_r7_the_digest_never_embeds_the_argument(self, tmp_path: Path) -> None:
        """R7: "those detectors never touch hostile text"."""
        record = f.assistant(
            "a1",
            content=[f.tool_use_block("t1", "Bash", {"cmd": "SECRET-ARGUMENT"})],
            usage_block=f.usage(),
        )
        trace = load([f.write_trace(tmp_path, [record])], no_previews=True)
        assert "SECRET-ARGUMENT" not in trace.model_dump_json()
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        assert tool.tool_input_digest is not None
        assert len(tool.tool_input_digest) == 16


class TestPreviewsR8:
    """R8: the only trace free text that reaches a report."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  spaced   out  ", "spaced out"),
            ("line\nbreak", "line break"),
            ("tab\tseparated", "tab separated"),
            ("null\x00byte", "null byte"),
            ("escape\x1b[31m", "escape [31m"),
            ("‮reversed", "reversed"),
            ("zero​width", "zero width"),
            ("", ""),
        ],
    )
    def test_r8_normalization_is_flatten_collapse_strip(self, raw: str, expected: str) -> None:
        """R8: non-printable becomes a space, runs collapse, the result is stripped."""
        assert preview(raw) == expected

    def test_r8_truncation_is_by_code_point_with_an_ellipsis(self) -> None:
        """R8: 240 code points, then ``…`` — never a byte count."""
        short = "a" * PREVIEW_LIMIT
        assert preview(short) == short and not preview(short).endswith(ELLIPSIS)
        long = "a" * (PREVIEW_LIMIT + 1)
        assert preview(long) == "a" * PREVIEW_LIMIT + ELLIPSIS
        assert len(preview(long)) == PREVIEW_LIMIT + 1 == PREVIEW_MAX_CHARS

    def test_r8_truncation_never_splits_a_multi_byte_character(self) -> None:
        """R8: "truncation is by Unicode code point, not bytes"."""
        text = "日" * 1_000
        result = preview(text)
        assert len(result) == PREVIEW_LIMIT + 1
        assert result[:-1] == "日" * PREVIEW_LIMIT
        result.encode("utf-8")

    def test_r8_a_preview_is_always_one_printable_line(self) -> None:
        """R8: whatever the trace holds, a preview is one line of printable text."""
        # a bidi override, a zero-width space, and the line and paragraph separators
        separators = "\u202e\u200b\u2028\u2029  "
        for hostile in ("a\r\n\r\nb", "\x00" * 50, "x" * 10_000, separators):
            result = preview(hostile)
            assert "\n" not in result and "\r" not in result
            assert all(char.isprintable() for char in result)
            assert len(result) <= PREVIEW_MAX_CHARS

    def test_r8_previews_are_populated_on_the_spans_that_own_them(self, tmp_path: Path) -> None:
        """R8: text on the model call, input and result on the tool call."""
        trace = load([f.write_trace(tmp_path, a_conversation())])
        model_call = next(span for span in trace.spans if span.kind == "model_call")
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        assert model_call.text_preview == "working"
        assert tool.tool_input_preview == '{"cmd":"ls"}'
        assert tool.tool_result_preview == "file-a file-b"

    def test_r8_the_tool_input_preview_is_the_canonical_json(self, tmp_path: Path) -> None:
        """R8: "for tool input, the R7 canonical JSON"."""
        record = f.assistant(
            "a1", content=[f.tool_use_block("t1", "Bash", {"z": 1, "a": 2})], usage_block=f.usage()
        )
        trace = load([f.write_trace(tmp_path, [record])])
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        assert tool.tool_input_preview == '{"a":2,"z":1}'

    def test_r8_a_missing_preview_is_the_empty_string_not_none(self, tmp_path: Path) -> None:
        """R8: ``""`` when absent — the fields are never ``None``."""
        record = f.assistant("a1", content=[], usage_block=f.usage())
        span = load([f.write_trace(tmp_path, [record])]).spans[0]
        assert (span.text_preview, span.tool_input_preview, span.tool_result_preview) == (
            "",
            "",
            "",
        )

    def test_r8_slug_is_the_single_enumerating_normalization(self) -> None:
        """R8, R10: ``slug`` is what turns a trace token into an enumerated value."""
        assert slug("Tool Name!") == "tool_name_"
        assert slug("</script>") == "__script_"
        assert slug(None) == "unknown"
        assert slug("") == "unknown"
        assert len(slug("x" * 200)) == 40


class TestNoPreviewsModeR8:
    """R8, A10: ``--no-previews`` drops free text at model-build time."""

    def _payload_trace(self, tmp_path: Path, **kwargs: object) -> Trace:
        records = [
            f.user("u1", content="SENTINEL-USER-TEXT"),
            f.assistant(
                "a1",
                timestamp="2026-09-09T10:00:01.000Z",
                content=[
                    f.text_block("SENTINEL-ASSISTANT-TEXT"),
                    f.thinking_block("SENTINEL-THINKING"),
                    f.tool_use_block("t1", "Bash", {"cmd": "SENTINEL-TOOL-INPUT"}),
                ],
                usage_block=f.usage(input_tokens=1),
            ),
            f.user(
                "u2",
                timestamp="2026-09-09T10:00:02.000Z",
                content=[f.tool_result_block("t1", "SENTINEL-TOOL-RESULT")],
            ),
            f.system("s1", timestamp="2026-09-09T10:00:03.000Z", content="SENTINEL-SYSTEM"),
        ]
        path = f.write_trace(tmp_path, records)
        sidecar = path.with_suffix(".meta.json")
        sidecar.write_text(
            json.dumps(
                {
                    "agentType": "SENTINEL-AGENT-TYPE",
                    "description": "SENTINEL-DESCRIPTION",
                    "spawnDepth": 1,
                }
            ),
            encoding="utf-8",
        )
        return ClaudeCodeSource(read_sidecars=True, **kwargs).load([path], DEFAULTS)  # type: ignore[arg-type]

    def test_r8_previews_carry_the_text_when_the_mode_is_off(self, tmp_path: Path) -> None:
        """R8: the control arm — without the flag, the sentinels are present."""
        blob = self._payload_trace(tmp_path).model_dump_json()
        for sentinel in (
            "SENTINEL-USER-TEXT",
            "SENTINEL-ASSISTANT-TEXT",
            "SENTINEL-TOOL-INPUT",
            "SENTINEL-TOOL-RESULT",
            "SENTINEL-SYSTEM",
            "SENTINEL-DESCRIPTION",
        ):
            assert sentinel in blob, f"{sentinel} missing; the suppression test would be vacuous"

    def test_r8_no_previews_removes_every_free_text_field(self, tmp_path: Path) -> None:
        """R8, A10: the bytes do not exist on the model after ingestion."""
        trace = self._payload_trace(tmp_path, no_previews=True)
        blob = trace.model_dump_json()
        for sentinel in (
            "SENTINEL-USER-TEXT",
            "SENTINEL-ASSISTANT-TEXT",
            "SENTINEL-THINKING",
            "SENTINEL-TOOL-INPUT",
            "SENTINEL-TOOL-RESULT",
            "SENTINEL-SYSTEM",
            "SENTINEL-DESCRIPTION",
            "SENTINEL-AGENT-TYPE",
        ):
            assert sentinel not in blob, f"{sentinel} survived --no-previews"

    def test_r8_no_previews_blanks_all_three_preview_fields_on_every_span(
        self, tmp_path: Path
    ) -> None:
        """R8: all three fields, on every span, not just the ones with text."""
        trace = self._payload_trace(tmp_path, no_previews=True)
        assert trace.spans
        for span in trace.spans:
            assert span.text_preview == ""
            assert span.tool_input_preview == ""
            assert span.tool_result_preview == ""

    def test_r8_no_previews_also_blanks_the_agent_description(self, tmp_path: Path) -> None:
        """R8, A-a5: the sidecar's free text is trace-derived too."""
        trace = self._payload_trace(tmp_path, no_previews=True)
        assert trace.agents[0].description == ""
        assert trace.agents[0].agent_type is None
        assert trace.agents[0].depth == 1  # a number is not free text

    def test_r8_no_previews_keeps_the_structural_fields(self, tmp_path: Path) -> None:
        """R8, A-a18: model, stop reason and tool name survive, as the cost path needs them.

        Pinned here because it is a deliberate carve-out, not an oversight: it is
        the reason a later injection probe has to consider a hostile *model id*
        even in this mode.
        """
        trace = self._payload_trace(tmp_path, no_previews=True)
        tool = next(span for span in trace.spans if span.kind == "tool_call")
        model_call = next(span for span in trace.spans if span.kind == "model_call")
        assert tool.tool_name == "Bash"
        assert tool.tool_input_digest is not None
        assert model_call.model == "claude-sonnet-4-5-20250929"

    def test_r8_no_previews_does_not_change_ids_or_ordering(self, tmp_path: Path) -> None:
        """R8: suppression is about text; the identity of the trace is unchanged."""
        with_text = self._payload_trace(tmp_path)
        without = self._payload_trace(tmp_path, no_previews=True)
        assert with_text.trace_id == without.trace_id
        assert [s.span_id for s in with_text.spans] == [s.span_id for s in without.spans]
        assert [s.kind for s in with_text.spans] == [s.kind for s in without.spans]
