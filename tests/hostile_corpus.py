"""R51's injection corpus, as data, plus a trace that loads **every** field it names.

Not a test module.

``tests/fixtures/traces/hostile.jsonl`` is the checked-in half of R51 and it is
not the whole of it. R51 requires a payload in "agent descriptions, tool names,
tool inputs, tool results, assistant text, model ids, error details". The
checked-in fixture carries payloads in five of those seven. It carries none in
``AgentRun.agent_type``/``description``/``parent_agent_id``/``depth`` — those
come from A1's ``agent-<id>.meta.json`` sidecar, and
``TestCorpusShapeT6::test_t6_no_sidecar_metadata_sits_beside_the_corpus``
forbids a sidecar in that directory — and none in ``SpanError``, because no
record in it is an API error.

Those are exactly the fields ``report/html.py`` started rendering in increment
4: the lane legend (A-d9) and the spans table's error cell. A probe run only
against the checked-in fixture asserts "no payload escapes the lane legend"
about a legend whose payload-bearing columns are an em dash, which is this
project's signature defect with the probe as its subject. See BUG-8 and S29 in
``docs/test-reports/feature-so-i4.md``.

So this module builds a second hostile trace — two files and a sidecar, written
into ``tmp_path`` so the T6 rule stands — in which **every** field R51 names
carries a payload, and, crucially, a *distinct* one:

    Each field's payload embeds its own :data:`FIELD_MARKERS` token. A probe
    asserts every marker reaches a text node. A field the renderer silently
    stops rendering therefore takes its own marker out of the document and the
    probe goes red, naming the field. A shared payload could not tell "the
    lane legend rendered it" from "the spans table rendered it", which is how
    the increment-4 coder's spans table came to drop
    ``tool_result_preview`` — and with it every credential in the fixture —
    while every credential check still passed.

The corpus is checked in as data rather than assembled inside a test so it
cannot be narrowed in the same commit that makes a check fail;
``test_injection_probe_r51.py`` asserts its contents against R51's list.
"""

from __future__ import annotations

import json
from pathlib import Path

from .factories import (
    api_error,
    assistant,
    text_block,
    tool_result_block,
    tool_use_block,
    usage,
    user,
    write_jsonl,
)

#: R51's minimum payload corpus, verbatim from the requirement's list. The keys
#: are labels for failure messages; the values are the bytes R51 names.
R51_PAYLOADS: dict[str, str] = {
    "script_breakout": "</script><script>alert(1)</script>",
    "attribute_breakout": '"><img src=x onerror=alert(1)>',
    "javascript_url": "javascript:alert(1)",
    "comment_open": "<!--",
    "cdata_close": "]]>",
    "pre_escaped": "&lt;script&gt;",
    "data_uri": "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "backtick": "`whoami`",
    "template_probe": "{{7*7}}",
    "path_traversal": "../../etc/passwd",
}

#: R51's right-to-left override. Held apart from :data:`R51_PAYLOADS` because
#: R8 and R32 both replace it with a space, so it is asserted **absent** rather
#: than present-in-a-text-node. A probe that put it in the "must appear" list
#: would be asserting the opposite of what the product guarantees.
R51_RTL_OVERRIDE = "‮"

#: R51's 80,000-character string. R8 truncates a preview to 240 code points, so
#: what a probe asserts about it is that the document does not contain 80,000
#: of anything.
R51_HUGE_LENGTH = 80_000

#: R51's credential shapes. These must appear **nowhere** in a rendered report;
#: their ``[redacted:…]`` markers must appear instead.
R51_CREDENTIALS: dict[str, str] = {
    "aws_key_id": "AKIAIOSFODNN7EXAMPLE",
    "anthropic_key": "sk-ant-api03-0123456789abcdefghijklmnopqrstuvwxyz",
    "private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAK5f0000000000000000000000000000000000000000000000000\n"
        "-----END RSA PRIVATE KEY-----"
    ),
}

#: The redaction markers those three credentials must leave behind (R33).
R51_EXPECTED_MARKERS: tuple[str, ...] = (
    "[redacted:aws_key_id]",
    "[redacted:anthropic_key]",
    "[redacted:private_key]",
)

#: One distinctive token per R51-named field. Every marker is
#: ``str.isprintable`` on both CI interpreters, contains no character R32
#: escapes and no substring R33 redacts, so a marker that fails to reach a text
#: node means the *field* was dropped rather than that the boundary mangled it.
#:
#: The keys are the ``Trace`` attribute paths, so a failure message names the
#: field a reader has to go and look at.
FIELD_MARKERS: dict[str, str] = {
    "Span.text_preview": "MARKERtextpreview",
    "Span.tool_input_preview": "MARKERtoolinput",
    "Span.tool_result_preview": "MARKERtoolresult",
    "Span.model": "MARKERmodelid",
    "Span.stop_reason": "MARKERstopreason",
    "Span.tool_name": "MARKERtoolname",
    "Span.tool_use_id": "MARKERtooluseid",
    "SpanError.detail": "MARKERerrordetail",
    "AgentRun.agent_type": "MARKERagenttype",
    "AgentRun.description": "MARKERagentdescription",
}

#: The tool name that *conforms* to R16's pattern and is still a credential
#: (increment-2 review, S13). R16's shape check admits it; only R33 removes it,
#: and only if redaction runs over ``metrics`` as well as over ``previews``.
CONFORMING_CREDENTIAL_TOOL_NAME = R51_CREDENTIALS["aws_key_id"]

#: An ``agentId`` that is a credential **and** satisfies R5's ``AGENT_ID_PATTERN``
#: (``^[A-Za-z0-9_.:\-]{1,64}$``), so the mapper's ``safe_agent_id`` keeps it
#: verbatim rather than replacing it with a digest. It reaches the spans table,
#: the lane legend, ``cost.by_agent`` and every finding's agent list as a join
#: key, which R33's ``identifier`` redacts in **both** modes and blanking never
#: touches (S16). Added after the increment-4 mutation sweep: the mutant that
#: turned the lane legend's ``_ident`` into a plain escape survived, because no
#: agent id in any corpus was credential-shaped.
CREDENTIAL_AGENT_ID = R51_CREDENTIALS["aws_key_id"]

#: An unknown record ``type`` that is a credential and still satisfies R10's
#: ``WARNING_DETAIL_PATTERN``, so it reaches the warnings table as
#: ``ParseWarning.detail``. Same origin: the mutant that dropped redaction from
#: the warnings table survived the first sweep.
#:
#: A **GitHub** token shape rather than an AWS one, and that is not a detail:
#: the mapper lowercases an unknown record type before it becomes a warning
#: detail, so ``AKIA…`` arrives as ``akia…`` and R33's ``aws_key_id`` pattern —
#: which is uppercase-only — cannot match it. ``gh[pousr]_[A-Za-z0-9]{20,}``
#: survives lowercasing. A corpus that used the AWS shape here would assert
#: redaction of a string the redactor was never going to match, which is a
#: check that cannot fail dressed as one that passes.
CREDENTIAL_RECORD_TYPE = "ghp_" + "a" * 24

_MARKUP = '</script><script>alert(1)</script>"><img src=x onerror=alert(1)>'


def _payload(field: str) -> str:
    """The payload for ``field``: its own marker, then R51's markup.

    Kept short on purpose. R8 truncates a preview at 240 code points and
    ``AgentRun.description`` at 200, so a payload that ran past either cap would
    make "this field reached a text node" fail for a reason that has nothing to
    do with the field. ``test_r51_every_payload_bearing_field_fits_inside_r8s_cap``
    asserts the margin rather than leaving it to chance.
    """
    return f"{FIELD_MARKERS[field]} {_MARKUP} {{{{7*7}}}} ../../etc/passwd `whoami`"


#: R8's preview cap and ``DETAIL_MAX_CHARS``, as the corpus's own budget.
PREVIEW_BUDGET = 240
DESCRIPTION_BUDGET = 200


def sidecar_document() -> dict[str, object]:
    """The ``agent-<id>.meta.json`` payload for the child agent (A1).

    ``agentType`` and ``description`` are the two R51-named fields that reach a
    report **only** through this file, and ``toolUseId`` is what gives
    ``AgentRun.parent_agent_id`` a value other than ``None`` — the third column
    of the lane legend A-d9 argues must render unconditionally.
    """
    return {
        "agentType": _payload("AgentRun.agent_type"),
        # The credential is here as well as in a tool result: a mutant that
        # replaced this field's `free_text` with a plain escape survived the
        # first sweep, because no corpus put a credential in a description.
        "description": f"{_payload('AgentRun.description')} {R51_CREDENTIALS['aws_key_id']}",
        "spawnDepth": 1,
        "toolUseId": "toolu_spawn",
    }


def parent_records() -> list[dict[str, object]]:
    """The spawning agent: assistant text, a tool call, a tool result, an API error."""
    return [
        user("p-0", timestamp="2026-03-02T09:00:00.000Z", agent_id="alpha", content=_MARKUP),
        assistant(
            "p-1",
            timestamp="2026-03-02T09:00:01.000Z",
            agent_id="alpha",
            message_id="msg_p1",
            request_id="req_p1",
            model=_payload("Span.model"),
            stop_reason=_payload("Span.stop_reason"),
            usage_block=usage(input_tokens=10, output_tokens=20),
            content=[
                text_block(_payload("Span.text_preview")),
                tool_use_block(
                    "toolu_spawn",
                    _payload("Span.tool_name"),
                    {"prompt": _payload("Span.tool_input_preview")},
                ),
            ],
        ),
        user(
            "p-2",
            timestamp="2026-03-02T09:00:02.000Z",
            agent_id="alpha",
            content=[
                # Two credentials and the field's own marker, and nothing else:
                # R8 truncates a preview at 240 code points, so a result that
                # carried every R51 payload as well would lose the tail and the
                # probe would be red for a reason that has nothing to do with
                # the boundary. The remaining payloads are on `agent-gamma`.
                tool_result_block(
                    "toolu_spawn",
                    f"{_payload('Span.tool_result_preview')} "
                    f"{R51_CREDENTIALS['aws_key_id']} {R51_CREDENTIALS['anthropic_key']}",
                )
            ],
        ),
        # The same `(tool_name, tool_input_digest)` a second time, so
        # `repeated_tool_call` fires and `Finding.previews` carries the
        # payload-bearing `tool_input_preview` rather than a benign one — the
        # findings section renders `previews`, and a probe over a corpus whose
        # findings all have harmless previews would assert nothing about it.
        assistant(
            "p-2a",
            timestamp="2026-03-02T09:00:02.500Z",
            agent_id="alpha",
            message_id="msg_p2a",
            request_id="req_p2a",
            model="claude-sonnet-4-5-20250929",
            stop_reason="tool_use",
            usage_block=usage(input_tokens=3, output_tokens=3),
            content=[
                tool_use_block(
                    "toolu_again",
                    _payload("Span.tool_name"),
                    {"prompt": _payload("Span.tool_input_preview")},
                )
            ],
        ),
        # An *error* result whose text matches one of R22's five `unknown_tool`
        # phrases, so `failed_tool_call` and `unresolved_tool_call` both fire
        # and `tool_result_status == "error"` is exercised.
        user(
            "p-2b",
            timestamp="2026-03-02T09:00:02.800Z",
            agent_id="alpha",
            content=[
                tool_result_block(
                    "toolu_again",
                    f"Unknown tool: {_payload('Span.tool_result_preview')}",
                    is_error=True,
                )
            ],
        ),
        api_error(
            "p-3",
            timestamp="2026-03-02T09:00:03.000Z",
            agentId="alpha",
            message_id="msg_p3",
            request_id="req_p3",
            error="</script><script>",
            status=_payload("SpanError.detail"),
        ),
        assistant(
            "p-4",
            timestamp="2026-03-02T09:00:04.000Z",
            agent_id="alpha",
            message_id="msg_p4",
            request_id="req_p4",
            model="claude-sonnet-4-5-20250929",
            stop_reason="end_turn",
            usage_block=usage(input_tokens=5, output_tokens=5),
            content=[text_block(R51_CREDENTIALS["private_key"])],
        ),
    ]


def child_records() -> list[dict[str, object]]:
    """The spawned agent: a repeated, credential-named, R16-conforming tool call.

    The same ``(tool_name, tool_input_digest)`` twice, so ``repeated_tool_call``
    fires and puts ``CONFORMING_CREDENTIAL_TOOL_NAME`` into ``metrics.tool_name``
    — S13's path, which R16's shape check cannot close and only R33 can.
    """
    records: list[dict[str, object]] = [
        assistant(
            "c-0",
            timestamp="2026-03-02T09:00:05.000Z",
            agent_id="beta",
            message_id="msg_c0",
            request_id="req_c0",
            model="claude-haiku-4-5-20251001",
            stop_reason="tool_use",
            usage_block=usage(input_tokens=7, output_tokens=3),
            content=[
                text_block("child text"),
                tool_use_block(
                    "toolu_c1", CONFORMING_CREDENTIAL_TOOL_NAME, {"q": "the same argument"}
                ),
            ],
        ),
        user(
            "c-1",
            timestamp="2026-03-02T09:00:06.000Z",
            agent_id="beta",
            content=[tool_result_block("toolu_c1", "ok")],
        ),
        assistant(
            "c-2",
            timestamp="2026-03-02T09:00:07.000Z",
            agent_id="beta",
            message_id="msg_c2",
            request_id="req_c2",
            model="claude-haiku-4-5-20251001",
            stop_reason="tool_use",
            usage_block=usage(input_tokens=7, output_tokens=3),
            content=[
                tool_use_block(
                    "toolu_c2", CONFORMING_CREDENTIAL_TOOL_NAME, {"q": "the same argument"}
                )
            ],
        ),
        user(
            "c-3",
            timestamp="2026-03-02T09:00:08.000Z",
            agent_id="beta",
            content=[tool_result_block("toolu_c2", "ok")],
        ),
        # R51's 80,000-character string and its RTL override, on a span that
        # also carries a `tool_use_id` payload below.
        assistant(
            "c-4",
            timestamp="2026-03-02T09:00:09.000Z",
            agent_id="beta",
            message_id="msg_c4",
            request_id="req_c4",
            model="claude-haiku-4-5-20251001",
            stop_reason="end_turn",
            usage_block=usage(input_tokens=1, output_tokens=1),
            content=[text_block("B" * R51_HUGE_LENGTH)],
        ),
        # An unknown record type, so `ParseWarning.detail` carries a
        # trace-derived slug (R4, R10) into the warnings table.
        {
            "type": "totally_unknown_type",
            "uuid": "c-5",
            "timestamp": "2026-03-02T09:00:10.000Z",
            "agentId": "beta",
        },
        {
            "type": CREDENTIAL_RECORD_TYPE,
            "uuid": "c-6",
            "timestamp": "2026-03-02T09:00:10.500Z",
            "agentId": "beta",
        },
    ]
    return records


def tool_use_id_records() -> list[dict[str, object]]:
    """A third agent whose ``tool_use_id`` is itself a payload (R51, R12).

    ``Span.tool_use_id`` is trace-derived, is rendered in the spans table, and
    is the one R51-named identifier with no alphabet constraint on the model.
    """
    marker = FIELD_MARKERS["Span.tool_use_id"]
    return [
        assistant(
            "g-0",
            timestamp="2026-03-02T09:00:11.000Z",
            agent_id=CREDENTIAL_AGENT_ID,
            message_id="msg_g0",
            request_id="req_g0",
            model="claude-haiku-4-5-20251001",
            stop_reason="tool_use",
            usage_block=usage(input_tokens=1, output_tokens=1),
            content=[tool_use_block(f"{marker}<script>", "Read", {"path": "/tmp/x"})],
        ),
        # The R51 payloads that do not fit beside the credentials on
        # `agent-alpha`, on a result of their own so R8's 240-code-point cap
        # cannot truncate any of them away.
        user(
            "g-1",
            timestamp="2026-03-02T09:00:12.000Z",
            agent_id=CREDENTIAL_AGENT_ID,
            content=[
                tool_result_block(
                    f"{marker}<script>",
                    f"{R51_PAYLOADS['data_uri']} {R51_PAYLOADS['comment_open']} "
                    f"{R51_PAYLOADS['cdata_close']} {R51_PAYLOADS['pre_escaped']} "
                    f"{R51_PAYLOADS['javascript_url']}{R51_RTL_OVERRIDE}",
                )
            ],
        ),
    ]


def write_hostile_trace(directory: Path) -> tuple[Path, ...]:
    """Write the extended hostile trace into ``directory`` and return its files.

    Three files, in basename order (R6): ``agent-alpha``, ``agent-beta``,
    ``agent-gamma``. Only ``agent-beta`` gets a sidecar, because A1 applies one
    to a file carrying exactly one agent, and only a *child* agent can have a
    ``parent_agent_id``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    alpha = write_jsonl(directory / "agent-alpha.jsonl", parent_records())
    beta = write_jsonl(directory / "agent-beta.jsonl", child_records())
    gamma = write_jsonl(directory / "agent-gamma.jsonl", tool_use_id_records())
    (directory / "agent-beta.meta.json").write_text(
        json.dumps(sidecar_document(), sort_keys=True) + "\n", encoding="utf-8"
    )
    return (alpha, beta, gamma)


__all__ = [
    "CONFORMING_CREDENTIAL_TOOL_NAME",
    "CREDENTIAL_AGENT_ID",
    "CREDENTIAL_RECORD_TYPE",
    "FIELD_MARKERS",
    "R51_CREDENTIALS",
    "R51_EXPECTED_MARKERS",
    "R51_HUGE_LENGTH",
    "R51_PAYLOADS",
    "R51_RTL_OVERRIDE",
    "child_records",
    "parent_records",
    "sidecar_document",
    "tool_use_id_records",
    "write_hostile_trace",
]
