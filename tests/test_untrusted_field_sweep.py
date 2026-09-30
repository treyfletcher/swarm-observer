"""R33, R51: every string the normalized model can carry, swept by construction.

This module exists because the same defect has now been found four times, once
per increment, and each of the first three fixes was a correct fix to *the field
that was reported*:

* increment 1 — a trace-derived field bypassing redaction;
* increment 2 (S13) — ``metrics.tool_name``, a credential-shaped string that is
  a *legal* tool name under R16;
* increment 3 (BUG-2) — ``agent_id``, ``parent_agent_id`` and
  ``ParseWarning.detail``, reached raw in **both** modes;
* increment 4 (review) — ``SpanError.code``, reached raw in **both** modes and
  in **both** renderers, for the same reason every time: its type looks like an
  identifier rather than like text, so the boundary was never applied to it.

Increment 3 added a credential sweep to answer this
(``test_json_report.TestCredentialShapeSweepR33``) and it did not catch the
fourth, because its fixture pins ``SpanError(code="api_error", …)`` — a
package-authored constant in the one field that was leaking. A sweep whose
fixture cannot carry the payload into a field is that field's check reporting
green while structurally unable to fail, which is this project's signature
defect with the credential sweep itself as the subject.

So the field list here is **not written down**. It is derived from the model's
own declared fields, and the test that matters is
:meth:`TestThePartitionIsExhaustive.test_r33_every_string_field_of_the_model_is_classified`:
a string-valued field added to ``model/trace.py`` belongs to the untrusted class
or to the authored class, and a field in neither fails this module. There is no
list to forget to update, and adding a field to the authored class costs an edit
next to the sentence saying what "authored" means.

Every assertion below runs over **both** renderers in **both** modes, because
three of the four occurrences reached the document under ``--no-previews`` too.
"""

from __future__ import annotations

import types
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from swarm_observer.model.trace import (
    TRACE_SCHEMA_VERSION,
    AgentRun,
    ParseWarning,
    SourceFile,
    Span,
    SpanError,
    TokenUsage,
    Trace,
)
from swarm_observer.report.narrative import Narrative, NarrativeParagraph
from swarm_observer.report.redact import marker

from .hostile_corpus import R51_CREDENTIALS
from .pipeline import analyze_paths, analyze_trace
from .sentinel_trace import write_sentinel_trace

#: Every string-valued field of the normalized model that a report must treat as
#: untrusted: its bytes came out of the trace (R2, R4, R5, R8, R12), or, for
#: ``SourceFile.name``, out of a directory an attacker may have written into
#: (the increment-3 review's ruling on S21). The boundary for each is
#: ``report/sanitize.py`` — ``free_text`` or ``identifier``, never neither.
UNTRUSTED_FIELDS: tuple[tuple[type[BaseModel], str], ...] = (
    (Span, "agent_id"),
    (Span, "model"),
    (Span, "stop_reason"),
    (Span, "tool_name"),
    (Span, "tool_use_id"),
    (Span, "text_preview"),
    (Span, "tool_input_preview"),
    (Span, "tool_result_preview"),
    (SpanError, "code"),
    (SpanError, "detail"),
    (AgentRun, "agent_id"),
    (AgentRun, "agent_type"),
    (AgentRun, "description"),
    (AgentRun, "parent_agent_id"),
    (ParseWarning, "detail"),
    (SourceFile, "name"),
)

#: Every string-valued field this package *computes*, so no trace byte can be in
#: it. Each entry is a claim with a named reason, not a convenience:
#:
#: * ``span_id``, ``parent_span_id``, ``tool_input_digest``, ``trace_id`` —
#:   SHA-256 digests (R5, R7), pattern-validated to 16 lowercase hex;
#: * ``SourceFile.sha256`` — a digest of the file's bytes (R2), 64 hex;
#: * ``Span.kind``, ``Span.tool_result_status``, ``ParseWarning.code`` — closed
#:   enumerations this package defined (R2, R10);
#: * ``Trace.adapter`` — a registry slug chosen by the CLI (R3), ``^[a-z0-9_]+$``;
#: * ``Trace.schema_version`` — pinned equal to ``TRACE_SCHEMA_VERSION`` by
#:   ``Trace._pinned_invariants`` (R1).
#:
#: ``SpanError.code`` is deliberately **not** here, and that is the finding this
#: module was written for: R12 builds it from the record's own ``error`` field
#: with ``ingest.text.slug``, which is the identical construction R4 uses for a
#: ``ParseWarning.detail``. Its alphabet stops markup and admits a credential.
AUTHORED_FIELDS: tuple[tuple[type[BaseModel], str], ...] = (
    (Span, "span_id"),
    (Span, "parent_span_id"),
    (Span, "kind"),
    (Span, "tool_input_digest"),
    (Span, "tool_result_status"),
    (ParseWarning, "code"),
    (SourceFile, "sha256"),
    (Trace, "schema_version"),
    (Trace, "trace_id"),
    (Trace, "adapter"),
)

#: The models whose fields this module claims to have classified exhaustively.
SWEPT_MODELS: tuple[type[BaseModel], ...] = (Trace, Span, SpanError, AgentRun, SourceFile)

#: The size of :data:`UNTRUSTED_FIELDS`, pinned as a literal for the same reason
#: ``LEAK_LEDGER_SIZE`` and ``collection_floor.json`` are: the exhaustiveness
#: test below can be satisfied by *moving* a field from the untrusted class to
#: the authored class, which is one edit and turns a red suite green while
#: removing a field from the sweep. Moving one now costs an edit here too, with
#: this sentence between them. It may be raised freely; lowering it is a
#: reviewer's call.
UNTRUSTED_FIELD_COUNT = 16


def _can_hold_a_string(annotation: object) -> bool:
    """True when a pydantic field's annotation admits a ``str`` value.

    Written over ``typing`` rather than over the field's repr so ``str | None``,
    ``Literal["ok", "error"]`` and a bare ``str`` are all recognised. A field
    this returns False for is not a field a redactor could ever have to cover.
    """
    if annotation is str:
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return any(isinstance(arg, str) for arg in typing.get_args(annotation))
    if origin in (typing.Union, types.UnionType):
        return any(_can_hold_a_string(arg) for arg in typing.get_args(annotation))
    return False


def string_fields(model: type[BaseModel]) -> set[str]:
    """Every field of ``model`` whose annotation admits a ``str``."""
    return {
        name for name, field in model.model_fields.items() if _can_hold_a_string(field.annotation)
    }


#: A distinct, legal AWS key shape per field: ``AKIA`` plus exactly sixteen
#: ``[0-9A-Z]`` characters, which is what R33's ``aws_key_id`` pattern matches.
#: It is legal in **every** field below — it satisfies R2's agent-id alphabet,
#: R10's warning-detail alphabet and ``SpanError.code``'s 40-character slug
#: alphabet — which is what makes one credential a statement about the redactor
#: rather than about one field's validator. Distinct per field so a failure
#: names the field that leaked rather than only that something did.
def credential_for(model: type[BaseModel], field: str) -> str:
    tag = f"{model.__name__}{field}".upper()
    tag = "".join(char for char in tag if char.isalnum())
    return "AKIA" + (tag + "X" * 16)[:16]


def sentinel_for(model: type[BaseModel], field: str) -> str:
    """A harmless distinct token, for the arm that proves the field is rendered."""
    return f"SENTINEL{model.__name__}{field}".replace("_", "")


T0 = datetime(2026, 3, 2, 9, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 3, 2, 9, 0, 1, tzinfo=UTC)


def loaded_trace(token: typing.Callable[[type[BaseModel], str], str]) -> Trace:
    """A ``Trace`` with ``token(model, field)`` in every one of :data:`UNTRUSTED_FIELDS`.

    Built from the models directly rather than through the adapter: an adapter
    constrains what can reach a field, and the question this module asks is what
    the *renderers* do with a field the model admits. Three of the four historic
    occurrences were fields the adapter fills straight from a record.
    """
    # Two agents, because ``AgentRun.agent_id`` and ``Span.agent_id`` are
    # rendered by two different sections (the lane legend and the spans table)
    # and a shared token could not tell which of them stopped rendering — the
    # per-field-marker lesson of BUG-8, applied to the join key itself. The
    # second agent owns the second span, so the two ids are genuinely distinct
    # strings that both have to survive the boundary.
    legend_agent = token(AgentRun, "agent_id")
    span_agent = token(Span, "agent_id")
    return Trace(
        schema_version=TRACE_SCHEMA_VERSION,
        trace_id="d" * 16,
        adapter="claude_code_jsonl",
        source_files=(
            SourceFile(
                name=f"{token(SourceFile, 'name')}.jsonl", sha256="e" * 64, bytes=1, records=2
            ),
        ),
        agents=(
            AgentRun(
                agent_id=legend_agent,
                agent_index=0,
                agent_type=token(AgentRun, "agent_type"),
                description=token(AgentRun, "description"),
                parent_agent_id=token(AgentRun, "parent_agent_id"),
                depth=1,
                span_seqs=(0,),
                start=T0,
                end=T1,
            ),
            AgentRun(
                agent_id=span_agent,
                agent_index=1,
                span_seqs=(1,),
                start=T0,
                end=T1,
            ),
        ),
        spans=(
            Span(
                span_id="a" * 16,
                agent_id=legend_agent,
                kind="model_call",
                seq=0,
                start=T0,
                end=T1,
                model=token(Span, "model"),
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                stop_reason=token(Span, "stop_reason"),
                text_preview=token(Span, "text_preview"),
                error=SpanError(code=token(SpanError, "code"), detail=token(SpanError, "detail")),
            ),
            Span(
                span_id="b" * 16,
                parent_span_id="a" * 16,
                agent_id=span_agent,
                kind="tool_call",
                seq=1,
                start=T0,
                end=T1,
                tool_name=token(Span, "tool_name"),
                tool_use_id=token(Span, "tool_use_id"),
                tool_input_digest="c" * 16,
                tool_result_status="error",
                tool_input_preview=token(Span, "tool_input_preview"),
                tool_result_preview=token(Span, "tool_result_preview"),
            ),
        ),
        warnings=(
            ParseWarning(code="unknown_record_type", count=1, detail=token(ParseWarning, "detail")),
        ),
    )


@pytest.fixture(scope="module")
def credential_documents() -> dict[bool, tuple[str, str]]:
    """``{previews: (html, json)}`` for the trace loaded with credentials."""
    trace = loaded_trace(credential_for)
    return {
        previews: (analysis.html, analysis.json)
        for previews in (True, False)
        for analysis in [analyze_trace(trace, previews=previews)]
    }


@pytest.fixture(scope="module")
def sentinel_documents() -> dict[bool, tuple[str, str]]:
    """``{previews: (html, json)}`` for the same trace loaded with plain sentinels."""
    trace = loaded_trace(sentinel_for)
    return {
        previews: (analysis.html, analysis.json)
        for previews in (True, False)
        for analysis in [analyze_trace(trace, previews=previews)]
    }


class TestThePartitionIsExhaustive:
    """R33: the check that makes this module survive the *next* field."""

    def test_r33_every_string_field_of_the_model_is_classified(self) -> None:
        """R33: a new string field on the model is untrusted or authored, never neither.

        This is the whole point of the module. Four increments running, a
        trace-derived string reached a report because nobody wrote it down on
        the list of trace-derived strings. There is no list here: the fields
        come from ``model_fields``, and one that is in neither class fails.

        Red when: ``model/trace.py`` gains a string-valued field — which is a
        ``TRACE_SCHEMA_VERSION`` bump under R2 anyway, so the friction lands
        exactly where R2 already says a decision is being made.
        """
        classified = {(model, field) for model, field in UNTRUSTED_FIELDS + AUTHORED_FIELDS}
        unclassified: list[str] = []
        for model in SWEPT_MODELS:
            for field in string_fields(model):
                if (model, field) not in classified:
                    unclassified.append(f"{model.__name__}.{field}")
        assert not sorted(unclassified), (
            f"unclassified string field(s): {sorted(unclassified)}. Add each to "
            "UNTRUSTED_FIELDS (and make the renderers redact it) or to "
            "AUTHORED_FIELDS with the reason no trace byte can reach it."
        )

    def test_r33_the_classification_names_only_fields_that_exist(self) -> None:
        """R33: a field renamed out of the model does not leave a dead entry behind.

        The other direction of the same set equality. A dead entry is width in
        the authored class, which is where a future field would be hidden.
        """
        for model, field in UNTRUSTED_FIELDS + AUTHORED_FIELDS:
            assert field in string_fields(model), f"{model.__name__}.{field} is not a string field"

    def test_r33_the_untrusted_class_has_not_been_narrowed(self) -> None:
        """R33: moving a field from untrusted to authored costs two edits.

        Red when: the sweep is made green by reclassifying the field that failed
        it instead of by redacting it. Raising this literal is free; lowering it
        is a review decision, which is the friction ``LEAK_LEDGER_SIZE`` and
        ``collection_floor.json`` already carry.
        """
        assert len(UNTRUSTED_FIELDS) >= UNTRUSTED_FIELD_COUNT
        assert len(set(UNTRUSTED_FIELDS)) == len(UNTRUSTED_FIELDS)

    def test_r33_the_string_field_detector_sees_the_shapes_the_model_uses(self) -> None:
        """R33: ``_can_hold_a_string`` is not the thing that could go quietly wrong.

        The exhaustiveness test is only as good as the predicate that decides
        which fields it covers. A predicate that returned ``False`` for
        everything would make it pass over an empty set. Red when: the predicate
        stops recognising ``str``, ``str | None`` or a string ``Literal``, each
        of which the model uses today.
        """
        assert _can_hold_a_string(str)
        assert _can_hold_a_string(str | None)
        assert _can_hold_a_string(typing.Literal["ok", "error"])
        assert not _can_hold_a_string(int)
        assert not _can_hold_a_string(int | None)
        assert "kind" in string_fields(Span)  # a Literal
        assert "model" in string_fields(Span)  # a `str | None`
        assert "seq" not in string_fields(Span)  # an int


@pytest.mark.parametrize("previews", [True, False], ids=["previews", "no-previews"])
@pytest.mark.parametrize(
    ("model", "field"), UNTRUSTED_FIELDS, ids=[f"{m.__name__}.{f}" for m, f in UNTRUSTED_FIELDS]
)
class TestNoCredentialSurvivesAnyField:
    """R33, R51: "the credential-shaped payloads do not appear at all"."""

    def test_r33_no_credential_reaches_report_html(
        self,
        model: type[BaseModel],
        field: str,
        previews: bool,
        credential_documents: dict[bool, tuple[str, str]],
    ) -> None:
        """R51: the credential in this field is absent from the HTML, in this mode.

        Red when: a field's renderer calls ``_t`` (escape only) rather than
        ``_free`` or ``_ident``. That is exactly the shape of the four historic
        occurrences and of ``SpanError.code``, which this arm found.
        """
        html, _ = credential_documents[previews]
        assert credential_for(model, field) not in html

    def test_r33_no_credential_reaches_report_json(
        self,
        model: type[BaseModel],
        field: str,
        previews: bool,
        credential_documents: dict[bool, tuple[str, str]],
    ) -> None:
        """R51: and absent from the JSON, which has no escaping to hide behind."""
        _, text = credential_documents[previews]
        assert credential_for(model, field) not in text


class TestTheSweepIsNotVacuous:
    """R51: "absent" must mean redacted, not unrendered.

    Every assertion above is satisfied perfectly by a renderer that emits
    nothing for the field — which is **BUG-8** exactly, and the increment-4
    coder's own spans-table finding, and instance seven's empty ``findings``
    array. These are the arms that make the absence mean something.
    """

    @pytest.mark.parametrize(
        ("model", "field"),
        UNTRUSTED_FIELDS,
        ids=[f"{m.__name__}.{f}" for m, f in UNTRUSTED_FIELDS],
    )
    def test_r33_the_field_really_is_rendered_into_both_documents(
        self,
        model: type[BaseModel],
        field: str,
        sentinel_documents: dict[bool, tuple[str, str]],
    ) -> None:
        """R51: with a harmless token in it, the field reaches both documents.

        Red when: a renderer stops rendering the field — at which point the
        credential arm above would still be green and would mean nothing.
        """
        html, text = sentinel_documents[True]
        token = sentinel_for(model, field)
        assert token in html, f"{model.__name__}.{field} is not rendered into report.html"
        assert token in text, f"{model.__name__}.{field} is not rendered into report.json"

    def test_r33_the_redaction_marker_is_what_replaced_them(
        self, credential_documents: dict[bool, tuple[str, str]]
    ) -> None:
        """R33: every document carries the ``aws_key_id`` marker, in both modes.

        The positive half of "the credentials are gone": something ran, and what
        ran was the redactor.
        """
        for previews in (True, False):
            html, text = credential_documents[previews]
            assert marker("aws_key_id") in html
            assert marker("aws_key_id") in text

    def test_r33_the_credential_shape_really_is_legal_in_every_swept_field(self) -> None:
        """R33: the trace the sweep renders actually holds the credentials.

        A validator that rejected the credential would have made
        :func:`loaded_trace` raise; a validator that silently *normalised* it
        would make every absence assertion above vacuous without raising
        anything. This reads the built ``Trace`` back and asserts each field
        holds its own credential verbatim.
        """
        trace = loaded_trace(credential_for)
        holders: dict[tuple[type[BaseModel], str], list[str]] = {
            (SourceFile, "name"): [source.name for source in trace.source_files],
            (AgentRun, "agent_id"): [agent.agent_id for agent in trace.agents],
            (AgentRun, "agent_type"): [agent.agent_type or "" for agent in trace.agents],
            (AgentRun, "description"): [agent.description for agent in trace.agents],
            (AgentRun, "parent_agent_id"): [agent.parent_agent_id or "" for agent in trace.agents],
            (ParseWarning, "detail"): [warning.detail for warning in trace.warnings],
            (SpanError, "code"): [s.error.code for s in trace.spans if s.error],
            (SpanError, "detail"): [s.error.detail for s in trace.spans if s.error],
            (Span, "agent_id"): [span.agent_id for span in trace.spans],
            (Span, "model"): [span.model or "" for span in trace.spans],
            (Span, "stop_reason"): [span.stop_reason or "" for span in trace.spans],
            (Span, "tool_name"): [span.tool_name or "" for span in trace.spans],
            (Span, "tool_use_id"): [span.tool_use_id or "" for span in trace.spans],
            (Span, "text_preview"): [span.text_preview for span in trace.spans],
            (Span, "tool_input_preview"): [span.tool_input_preview for span in trace.spans],
            (Span, "tool_result_preview"): [span.tool_result_preview for span in trace.spans],
        }
        assert set(holders) == set(UNTRUSTED_FIELDS), "a swept field has no read-back arm"
        for (model, field), values in holders.items():
            expected = credential_for(model, field)
            assert any(expected in value for value in values), (
                f"{model.__name__}.{field} does not hold its credential; "
                "every absence assertion about it is vacuous"
            )


#: Increment 5 added a **second** family of renderable models, in
#: ``report/narrative.py``, and nothing above sweeps them: ``SWEPT_MODELS`` is a
#: hand-written tuple of the ``model/trace.py`` classes. That is the list this
#: module exists to abolish, one level up — the fields are derived, the *models*
#: are not — and the fifth occurrence of the leak family (**BUG-16**,
#: ``NarrativeParagraph.title`` written with the ``authored`` kind while
#: constrained only by length) landed in exactly the model the list did not
#: name. Added by the increment-5 review.
NARRATIVE_UNTRUSTED_FIELDS: tuple[tuple[type[BaseModel], str], ...] = (
    (NarrativeParagraph, "title"),
    (NarrativeParagraph, "text"),
)

#: The narrative fields no attacker-influenced byte can occupy, with the reason.
#: Both are pattern-constrained to ``^[a-z][a-z0-9_]{0,63}$``, an alphabet that
#: contains no credential shape R33 knows and no character ``escape_html``
#: replaces — which is the claim ``title`` could not make and why it is above.
NARRATIVE_AUTHORED_FIELDS: tuple[tuple[type[BaseModel], str], ...] = (
    (NarrativeParagraph, "group"),
    (NarrativeParagraph, "reason"),
)

NARRATIVE_MODELS: tuple[type[BaseModel], ...] = (Narrative, NarrativeParagraph)


class TestTheNarrativeModelIsSweptToo:
    """R33/R43: the partition, extended to the models increment 5 made renderable.

    The point is not the two fields. It is that ``report/narrative.py`` was a
    new renderable model with no sweep, and the project's recurring defect
    found it within one increment — for the fifth time, and for the same
    reason every time: a field was classified by the caller that existed
    rather than by its own type.
    """

    def test_r33_every_string_field_of_the_narrative_model_is_classified(self) -> None:
        """R33: a string field added to ``report/narrative.py`` is untrusted or authored.

        Red when: a renderable narrative field is added and classified by
        nobody — which is the state ``title`` shipped in.
        """
        classified = {
            (model, field)
            for model, field in NARRATIVE_UNTRUSTED_FIELDS + NARRATIVE_AUTHORED_FIELDS
        }
        unclassified = [
            f"{model.__name__}.{field}"
            for model in NARRATIVE_MODELS
            for field in string_fields(model)
            if (model, field) not in classified
        ]
        assert not sorted(unclassified), (
            f"unclassified string field(s): {sorted(unclassified)}. Add each to "
            "NARRATIVE_UNTRUSTED_FIELDS (and make both renderers redact it) or to "
            "NARRATIVE_AUTHORED_FIELDS with the reason no attacker-influenced byte "
            "can reach it."
        )

    def test_r33_the_narrative_classification_names_only_fields_that_exist(self) -> None:
        """R33: the other direction — no dead entry widening the authored class."""
        for model, field in NARRATIVE_UNTRUSTED_FIELDS + NARRATIVE_AUTHORED_FIELDS:
            assert field in string_fields(model), f"{model.__name__}.{field} is not a string field"

    def test_r33_an_authored_narrative_field_cannot_hold_a_credential(self) -> None:
        """R33: the authored class is a claim, so it is checked rather than asserted.

        ``group`` and ``reason`` are in the authored class because their
        pattern refuses every credential shape R33 knows — not because the
        product happens to pass slugs. This drives the pattern.
        """
        for model, field in NARRATIVE_AUTHORED_FIELDS:
            assert model is NarrativeParagraph
            for credential in R51_CREDENTIALS.values():
                with pytest.raises(ValidationError):
                    NarrativeParagraph(
                        **{  # type: ignore[arg-type]
                            "group": "overall",
                            "title": "the whole run",
                            "text": "a paragraph",
                            field: credential,
                        }
                    )

    @pytest.mark.parametrize("previews", [True, False])
    @pytest.mark.parametrize(
        ("field", "credential"),
        [
            (field, name)
            for _, field in NARRATIVE_UNTRUSTED_FIELDS
            for name in ("aws_key_id", "anthropic_key")
        ],
    )
    def test_r33_an_untrusted_narrative_field_is_redacted_in_both_documents(
        self, field: str, credential: str, previews: bool, tmp_path: Path
    ) -> None:
        """R33/R43: each untrusted narrative field, in both renderers and both modes.

        The read-back arm the module's other sweep has: the credential is
        demonstrably *in* the field, so the absence assertion is not vacuous.

        Red when: either renderer claims a narrative field is its own.
        """
        value = R51_CREDENTIALS[credential]
        paragraph = NarrativeParagraph(
            **{  # type: ignore[arg-type]
                "group": "overall",
                "title": "the whole run",
                "text": "a paragraph",
                field: f"prefix {value} suffix",
            }
        )
        assert value in getattr(paragraph, field), "the field does not hold its credential"
        analysis = analyze_paths(
            write_sentinel_trace(tmp_path / "t"),
            previews=previews,
            narrative=Narrative(paragraphs=(paragraph,), calls=1),
        )
        for document in (analysis.html, analysis.json):
            assert value not in document
            assert marker(credential) in document
            # Redacted, not blanked: A-e9 keeps the narrative under
            # ``--no-previews`` and the surrounding words prove it.
            assert "prefix" in document and "suffix" in document
