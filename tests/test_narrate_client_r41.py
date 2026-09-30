"""R41: the client seam, the error taxonomy, and the scripted client.

R41 is three claims and they fail in different ways, so they are driven apart:

* ``NarratorClient`` is a ``Protocol`` with one method. The test for that uses a
  **three-line stub of this module's own**, never ``FixtureNarratorClient`` —
  the coder's first risk item is that the only offline oracle for ``--explain``
  is a client it also wrote, and a protocol test satisfied only by the author's
  own implementation is that risk in miniature.
* The errors are "single-line messages that never echo request or response
  bodies". The interesting half is *why*: there is no parameter a body could be
  passed through. That is a property of the signature, so it is asserted
  against the signature and not against one raise site's output.
* ``FixtureNarratorClient`` records every request, serves the script in order,
  raises the entries that are exceptions, and raises ``AssertionError`` on
  exhaustion. The last one matters most: if it were a ``NarratorError`` it
  would be swallowed by R43's fallback and a miscounted script would report
  green, which is this project's signature defect with a test harness as its
  subject.
"""

from __future__ import annotations

import inspect

import pytest

from swarm_observer.narrate.client import (
    NARRATOR_ERROR_CODES,
    OVERALL_GROUP,
    OVERALL_TITLE,
    GroupSummary,
    NarrationRequest,
    NarrationResponse,
    NarratorAuthError,
    NarratorClient,
    NarratorError,
    NarratorResponseError,
    NarratorTransportError,
    TraceTotals,
)
from swarm_observer.narrate.fixture import FixtureNarratorClient, paragraph


def a_request(group: str = OVERALL_GROUP) -> NarrationRequest:
    """The smallest legal request, for tests that only need an argument."""
    summary = GroupSummary(group=OVERALL_GROUP, title=OVERALL_TITLE, findings=0, wasted_tokens=0)
    totals = TraceTotals(
        agents=0,
        spans=0,
        model_calls=0,
        findings=0,
        tokens=0,
        cost_usd="0.000000",
        priced_spans=0,
        unpriced_spans=0,
    )
    return NarrationRequest(
        rate_snapshot_version="1.0.0", group=group, totals=totals, groups=(summary,)
    )


class StubNarrator:
    """Three lines, written here, owing nothing to ``narrate/fixture.py``.

    The coder's risk item 1: a test that scripts the coder's client against the
    coder's narrator is three of the coder's artefacts agreeing. Wherever a
    test below only needs "a client", it gets this.
    """

    def __init__(self, text: str = "a paragraph") -> None:
        self.text = text
        self.seen: list[NarrationRequest] = []

    def complete(self, request: NarrationRequest) -> NarrationResponse:
        self.seen.append(request)
        return NarrationResponse(paragraph=self.text)


class TestClientProtocolR41:
    """R41: ``NarratorClient`` is a one-method protocol anybody can satisfy."""

    def test_r41_a_three_line_stub_satisfies_the_protocol(self) -> None:
        """R41: the seam is structural, not nominal.

        Red when: ``complete`` gains a required parameter, or the protocol
        grows a second method — either of which would make a third-party
        narrator impossible to write without importing this package's classes.
        """
        assert isinstance(StubNarrator(), NarratorClient)
        assert issubclass(StubNarrator, NarratorClient)

    def test_r41_the_protocol_declares_exactly_one_method(self) -> None:
        """R41: "a ``typing.Protocol`` with ``complete(request) -> NarrationResponse``"."""
        members = {
            name
            for name, value in vars(NarratorClient).items()
            if callable(value) and not name.startswith("_")
        }
        assert members == {"complete"}
        signature = inspect.signature(NarratorClient.complete)
        assert list(signature.parameters) == ["self", "request"]

    def test_r41_an_object_without_complete_is_not_a_client(self) -> None:
        """R41: the control arm — ``runtime_checkable`` must reject something."""

        class NotAClient:
            def narrate(self) -> None:  # pragma: no cover - never called
                raise NotImplementedError

        assert not isinstance(NotAClient(), NarratorClient)

    def test_r41_the_stub_is_reached_by_the_narrator(self) -> None:
        """R41/R43: the stub is a real client, not a shape that is never used."""
        from swarm_observer.narrate.narrator import narrate

        stub = StubNarrator("stub paragraph")
        narration = narrate(
            client=stub,
            findings=(),
            totals=a_request().totals,
            rate_snapshot_version="1.0.0",
        )
        assert [p.text for p in narration.paragraphs] == ["stub paragraph"]
        assert narration.calls == 1
        assert len(stub.seen) == 1


class TestErrorTaxonomyR41:
    """R41: three subclasses, a closed code set, and nowhere to put a body."""

    def test_r41_the_three_subclasses_the_requirement_names_exist(self) -> None:
        """R41: ``NarratorError`` is the base of exactly the three named errors."""
        for subclass in (NarratorTransportError, NarratorAuthError, NarratorResponseError):
            assert issubclass(subclass, NarratorError)
            assert issubclass(subclass, Exception)
        assert NarratorError.__mro__[1] is Exception

    @pytest.mark.parametrize("code", sorted(NARRATOR_ERROR_CODES))
    def test_r41_every_declared_code_constructs_and_renders_one_line(self, code: str) -> None:
        """R41: every code in the closed set is usable and prints one line.

        Red when: a code is added to the set that a raise site cannot use, or
        the message grows a newline — at which point "single-line" stops being
        true of the diagnostic a CLI would print.
        """
        error = NarratorError(code)
        assert error.code == code
        assert error.line == f"narrator: {code}"
        assert str(error) == error.line
        assert "\n" not in str(error)
        assert str(error).strip() == str(error)

    def test_r41_an_undeclared_code_is_refused_at_the_raise_site(self) -> None:
        """R41: the closed set is enforced, not documented."""
        with pytest.raises(ValueError, match="unknown narrator error code"):
            NarratorError("something_a_provider_said")

    def test_r41_there_is_no_parameter_a_response_body_could_reach(self) -> None:
        """R41: "never echo request or response bodies", as a signature property.

        The strongest form of this claim is not "no raise site passes a body"
        — that is a rule every future raise site has to remember — but "there
        is no parameter to pass one through". Asserted over the constructor of
        the base and of all three subclasses, so a subclass cannot widen it.

        Red when: a ``detail``/``message``/``body`` parameter is added.
        """
        for cls in (
            NarratorError,
            NarratorTransportError,
            NarratorAuthError,
            NarratorResponseError,
        ):
            parameters = list(inspect.signature(cls).parameters)
            assert parameters == ["code"], f"{cls.__name__} takes {parameters}"

    def test_r41_a_providers_text_cannot_be_smuggled_through_the_code(self) -> None:
        """R41: the only channel left is ``code``, and it is a closed set.

        Red when: the membership check is dropped, at which point an adapter
        could classify by message and put a provider's text in a diagnostic.
        """
        for smuggled in (
            "connection refused to api.example.com:443 (key sk-ant-...)",
            "timeout\nstack trace follows",
            "",
        ):
            with pytest.raises(ValueError):
                NarratorTransportError(smuggled)


class TestFixtureClientR41:
    """R41: the scripted client behaves the way the requirement names."""

    def test_r41_it_serves_the_script_in_order(self) -> None:
        """R41: "an ordered script of ``NarrationResponse | NarratorError``"."""
        client = FixtureNarratorClient([paragraph("first"), paragraph("second")])
        assert client.complete(a_request()).paragraph == "first"
        assert client.complete(a_request()).paragraph == "second"
        assert client.remaining == 0

    def test_r41_it_raises_the_script_entries_that_are_errors(self) -> None:
        """R41: an exception in the script is raised, not returned."""
        client = FixtureNarratorClient([NarratorAuthError("auth_rejected")])
        with pytest.raises(NarratorAuthError) as caught:
            client.complete(a_request())
        assert caught.value.code == "auth_rejected"

    def test_r41_it_records_every_request_including_the_ones_it_loses(self) -> None:
        """R41: "recording every request in ``self.calls``".

        The request is recorded *before* the entry is inspected, so a call that
        raises is still visible. AC12's sentinel clause asks what was sent, and
        a request the narrator then lost was still sent.
        """
        client = FixtureNarratorClient([NarratorTransportError("transport_failed")])
        with pytest.raises(NarratorTransportError):
            client.complete(a_request(OVERALL_GROUP))
        assert [request.group for request in client.calls] == [OVERALL_GROUP]

    def test_r41_exhaustion_raises_assertion_error_and_not_a_narrator_error(self) -> None:
        """R41: a miscounted script must be a red test, not a silent fallback.

        This is the arm that matters. ``NarratorError`` is caught by R43's
        fallback, so a script that ran out would make a test asserting "two
        paragraphs rendered" pass while measuring a run that asked three times.

        Red when: the exhaustion path is changed to raise a ``NarratorError``,
        or to return a response.
        """
        client = FixtureNarratorClient([])
        with pytest.raises(AssertionError) as caught:
            client.complete(a_request())
        assert not isinstance(caught.value, NarratorError)
        assert "script exhausted" in str(caught.value)
        # It was still recorded: the call happened.
        assert len(client.calls) == 1

    def test_r41_remaining_counts_down(self) -> None:
        """R41: the script's remaining length is observable, so a test can pin it."""
        client = FixtureNarratorClient([paragraph("a"), paragraph("b")])
        assert client.remaining == 2
        client.complete(a_request())
        assert client.remaining == 1

    def test_r41_the_fixture_client_satisfies_the_protocol(self) -> None:
        """R41: "in the house style" means it is a real ``NarratorClient``."""
        assert isinstance(FixtureNarratorClient([]), NarratorClient)

    def test_r41_paragraph_is_a_response_constructor_that_changes_nothing(self) -> None:
        """R41: the readability helper builds the model and does **not** normalize.

        A helper that stripped or folded would silently make every scripted
        paragraph valid, so a test scripting a paragraph with leading
        whitespace or a control character would be measuring the helper
        rather than ``validate_paragraph``. A stripping mutant survived the
        first sweep; this is what kills it.
        """
        entry = paragraph("text")
        assert isinstance(entry, NarrationResponse)
        assert entry.paragraph == "text"
        for raw in ("  leading and trailing  ", "line\nbreak", "\x1b escape", ""):
            assert paragraph(raw).paragraph == raw

    def test_r41_the_response_model_is_frozen_and_closed(self) -> None:
        """R41: every payload model is frozen with ``extra="forbid"``."""
        response = NarrationResponse(paragraph="x")
        with pytest.raises(Exception):  # noqa: B017 - pydantic's own frozen error
            response.paragraph = "y"  # type: ignore[misc]
        with pytest.raises(Exception):  # noqa: B017
            NarrationResponse(paragraph="x", extra="y")  # type: ignore[call-arg]
