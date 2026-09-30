"""R43: per-group validation, per-group fallback, and the two markers.

Three things this module is careful about.

**The call count, not the fallback count.** A-e6 splits the error taxonomy
into codes that end a run's narration and codes that end one call's. "Every
group fell back" is true of both, so it is a check that cannot tell them
apart. Every arm below asserts ``Narration.calls``.

**A stub client, not the coder's.** ``FixtureNarratorClient`` is used only
where its own documented behaviour is the subject; everywhere else the client
is a few lines written here, so script-exhaustion semantics are not load
bearing in an assertion about the narrator.

**The rendered document, not the ``Narration``.** R43's markers are DOM facts
and its untrusted-output clause is about what reaches a reader, so the marker
and injection arms parse ``report.html`` rather than reading a dataclass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from swarm_observer.cli.main import main
from swarm_observer.detect.registry import ALL_DETECTORS
from swarm_observer.narrate.adapters.anthropic import (
    CREDENTIAL_ENV_VARS,
    AnthropicNarratorClient,
    extract_paragraph,
    system_prompt,
    user_prompt,
)
from swarm_observer.narrate.client import (
    MAX_PARAGRAPH_CHARS,
    NARRATOR_ERROR_CODES,
    OVERALL_GROUP,
    OVERALL_TITLE,
    RESERVED_PARAGRAPH_PREFIX,
    GroupSummary,
    NarrationRequest,
    NarrationResponse,
    NarratorAuthError,
    NarratorError,
    NarratorResponseError,
    NarratorTransportError,
    TraceTotals,
)
from swarm_observer.narrate.fixture import FixtureNarratorClient, paragraph
from swarm_observer.narrate.narrator import (
    FATAL_CODES,
    FOLDED_WHITESPACE,
    Narration,
    deterministic_paragraph,
    has_control_characters,
    narrate,
    normalize_paragraph,
    validate_paragraph,
)
from swarm_observer.narrate.summary import build_requests, narration_groups
from swarm_observer.report.narrative import (
    FALLBACK_CLASS,
    FALLBACK_PREFIX,
    NARRATIVE_CLASS,
    Narrative,
    NarrativeParagraph,
)

from .hostile_corpus import R51_CREDENTIALS
from .pipeline import Analysis, analyze_paths
from .rendered import parse
from .sentinel_trace import SENTINELS, sentinel_analysis, totals_for

#: R43's three fatal conditions that are properties of the *run*, from the
#: requirement's own sentence rather than from ``FATAL_CODES`` — which is the
#: thing under test and would make this a tautology.
RUN_FATAL_BY_REQUIREMENT: frozenset[str] = frozenset(
    {"sdk_not_installed", "no_credentials", "auth_rejected"}
)


@pytest.fixture(scope="module")
def sentinel(tmp_path_factory: pytest.TempPathFactory) -> Analysis:
    """A multi-group analysis: five detectors fire, so six groups are asked."""
    return sentinel_analysis(tmp_path_factory.mktemp("fallback"))


def run_narration(analysis: Analysis, client: Any, **kwargs: Any) -> Narration:
    """``narrate`` over a real analysis, with no filesystem and no CLI."""
    return narrate(
        client=client,
        findings=analysis.findings,
        totals=totals_for(analysis),
        rate_snapshot_version=analysis.cost.meta.version,
        **kwargs,
    )


class RaisingStub:
    """Three lines: raise the same error every time, and count the calls."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def complete(self, request: NarrationRequest) -> NarrationResponse:
        self.calls += 1
        raise self.error


class ReturningStub:
    """Three lines: return whatever it was handed, and count the calls."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls = 0

    def complete(self, request: NarrationRequest) -> NarrationResponse:
        self.calls += 1
        return self.value  # type: ignore[no-any-return]


class TestParagraphValidationR43:
    """R43: "non-empty, ``<= 800`` characters after normalization, no control characters"."""

    def test_r43_normalization_folds_ascii_whitespace_and_strips(self) -> None:
        """R43: "after normalization", as the one transformation it performs."""
        assert normalize_paragraph("  a\n\n b\t\tc \r\n") == "a b c"
        assert normalize_paragraph("\v\f one \v\f") == "one"
        assert normalize_paragraph("") == ""
        assert normalize_paragraph("   ") == ""
        assert frozenset("\t\n\r\v\f ") == FOLDED_WHITESPACE

    def test_r43_normalization_does_not_clean_control_characters(self) -> None:
        """R43 (A-e7): an ESC is rejected below, not tidied away.

        Red when: normalization starts stripping control characters, which
        would make the ``response_control_characters`` arm unreachable and
        silently accept a paragraph carrying terminal escapes.
        """
        assert "\x1b" in normalize_paragraph("a\x1bb")

    def test_r43_control_characters_are_named_by_number(self) -> None:
        """R43 (A-e7, S24): C0, DEL and C1, so the answer is interpreter-stable."""
        for char in ("\x00", "\x01", "\x1b", "\x7f", "\x80", "\x9f"):
            assert has_control_characters(f"a{char}b"), repr(char)
        for char in ("\xa0", "​", "‮", "x", " "):
            assert not has_control_characters(f"a{char}b"), repr(char)

    @pytest.mark.parametrize(
        ("text", "code"),
        [
            ("", "response_empty"),
            ("   \n\t ", "response_empty"),
            ("a\x1bb", "response_control_characters"),
            ("\x00" + "a" * 20, "response_control_characters"),
            ("z" * (MAX_PARAGRAPH_CHARS + 1), "response_too_long"),
        ],
    )
    def test_r43_each_invalid_paragraph_names_its_own_reason(self, text: str, code: str) -> None:
        """R43: the three checks, each reachable, each with its own code."""
        with pytest.raises(NarratorResponseError) as caught:
            validate_paragraph(text)
        assert caught.value.code == code

    def test_r43_the_boundary_length_is_accepted(self) -> None:
        """R43: "at most 800" — the off-by-one that decides 800 from 801."""
        assert len(validate_paragraph("z" * MAX_PARAGRAPH_CHARS)) == MAX_PARAGRAPH_CHARS
        with pytest.raises(NarratorResponseError):
            validate_paragraph("z" * (MAX_PARAGRAPH_CHARS + 1))

    def test_r43_length_is_measured_after_normalization(self) -> None:
        """R43: "<= 800 characters **after** normalization".

        Red when: the length check moves before the fold, at which point a
        paragraph a model wrote across lines is rejected for its whitespace.
        """
        padded = "\n  ".join("z" * MAX_PARAGRAPH_CHARS)
        assert len(padded) > MAX_PARAGRAPH_CHARS
        assert validate_paragraph("\n  ".join("z" * 10)) == " ".join("z" * 10)

    def test_r43_a_paragraph_that_breaks_two_checks_reports_the_first_one(self) -> None:
        """R43: the validation order, pinned rather than left to whichever ran.

        A paragraph that is both over-length **and** control-bearing can
        report either reason, and which one it reports is what a machine
        consumer of ``report.json`` reads. Nothing distinguished the two
        orders: swapping them survived the first mutation sweep.

        The order implemented is empty → control characters → length, which
        is the order that tells a reader the most: "your narrator emitted an
        ESC" is actionable and "it was too long" is not.

        Red when: the two checks are reordered.
        """
        both = "\x1b" + "z" * (MAX_PARAGRAPH_CHARS + 10)
        with pytest.raises(NarratorResponseError) as caught:
            validate_paragraph(both)
        assert caught.value.code == "response_control_characters"
        # ...and an empty-after-normalization paragraph beats both.
        with pytest.raises(NarratorResponseError) as empty:
            validate_paragraph("   \t\n   ")
        assert empty.value.code == "response_empty"

    def test_r43_a_valid_paragraph_comes_back_normalized(self) -> None:
        """R43: the control arm — validation is not simply always-refuse."""
        assert validate_paragraph("  A  paragraph.\n") == "A paragraph."


class TestFatalVersusPerCallR43:
    """R43/A-e6: the split is a claim about the **call count**."""

    def test_r43_the_fatal_set_is_the_one_the_requirement_names(self) -> None:
        """R43: the three conditions that are properties of a run, not of a call.

        Typed from R43's sentence rather than read from ``FATAL_CODES``, so
        this is a check and not a restatement.

        Red when: a code is added to or removed from ``FATAL_CODES`` without
        the requirement changing.
        """
        assert FATAL_CODES == RUN_FATAL_BY_REQUIREMENT

    @pytest.mark.parametrize("code", sorted(NARRATOR_ERROR_CODES))
    def test_r43_every_code_falls_back_and_the_call_count_tells_the_two_apart(
        self, code: str, sentinel: Analysis
    ) -> None:
        """R43/A-e6: ten of twelve codes ask once per group; three ask once.

        "Every group fell back" is true for **every** code here, which is why
        the fallback count alone cannot distinguish a run-fatal code from a
        per-call one. The call count can, and it is the assertion.

        Red when: a code moves between the two sets, or the loop stops
        continuing after a per-call failure — the change that would make
        AC12's third-group transport failure swallow its fourth group.
        """
        groups = narration_groups(sentinel.findings)
        assert len(groups) >= 4, "the corpus needs more than one group for this to mean anything"
        stub = RaisingStub(NarratorError(code))
        narration = run_narration(sentinel, stub)
        assert narration.fallbacks == len(narration.paragraphs) == len(groups)
        assert {p.reason for p in narration.paragraphs} == {code}
        expected_calls = 1 if code in RUN_FATAL_BY_REQUIREMENT else len(groups)
        assert narration.calls == stub.calls == expected_calls

    def test_r43_a_per_call_failure_does_not_lose_the_groups_after_it(
        self, sentinel: Analysis
    ) -> None:
        """R43/AC12: the mixed run — one failure in the middle, the rest answered.

        This is the shape AC12 scripts and the one a whole-run reading of R43
        would make impossible.
        """
        groups = narration_groups(sentinel.findings)

        class Middle:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, request: NarrationRequest) -> NarrationResponse:
                self.calls += 1
                if self.calls == 3:
                    raise NarratorTransportError("transport_failed")
                return NarrationResponse(paragraph=f"paragraph for {request.group}")

        client = Middle()
        narration = run_narration(sentinel, client)
        assert narration.calls == len(groups)
        assert narration.fallbacks == 1
        assert narration.paragraphs[2].fallback is True
        assert narration.paragraphs[2].reason == "transport_failed"
        assert narration.paragraphs[3].fallback is False

    def test_r43_a_fatal_failure_in_the_middle_stops_only_the_asking(
        self, sentinel: Analysis
    ) -> None:
        """R43/A-e6: after a run-fatal code, later groups fall back **unasked**.

        Red when: ``stopped`` stops short-circuiting, at which point an
        auth-rejected run makes one outbound request per group — the opposite
        of what R46's egress posture wants.
        """
        groups = narration_groups(sentinel.findings)

        class ThenFatal:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, request: NarrationRequest) -> NarrationResponse:
                self.calls += 1
                if self.calls == 2:
                    raise NarratorAuthError("auth_rejected")
                return NarrationResponse(paragraph="a paragraph")

        client = ThenFatal()
        narration = run_narration(sentinel, client)
        assert client.calls == narration.calls == 2
        assert narration.fallbacks == len(groups) - 1
        assert narration.paragraphs[0].fallback is False
        assert {p.reason for p in narration.paragraphs[1:]} == {"auth_rejected"}

    def test_r43_no_client_at_all_asks_nobody(self, sentinel: Analysis) -> None:
        """R43: ``client=None`` plus ``unavailable`` is "no narrator could be built"."""
        narration = run_narration(sentinel, None, unavailable="sdk_not_installed")
        assert narration.calls == 0
        assert narration.fallbacks == len(narration.paragraphs)
        assert {p.reason for p in narration.paragraphs} == {"sdk_not_installed"}

    def test_r43_no_client_and_no_reason_is_not_configured(self, sentinel: Analysis) -> None:
        """R43: the default reason distinguishes "never asked" from "answered badly"."""
        narration = run_narration(sentinel, None)
        assert {p.reason for p in narration.paragraphs} == {"not_configured"}


class TestNarratorMisbehaviourR43:
    """R43: the exit-code guarantee cannot depend on a vendor's manners."""

    @pytest.mark.parametrize(
        "value",
        [None, "a bare string", 42, object()],
        ids=["none", "str", "int", "object"],
    )
    def test_r43_a_client_returning_the_wrong_shape_falls_back(
        self, value: Any, sentinel: Analysis
    ) -> None:
        """R43: an SDK that answers with the wrong type is a fallback, not a crash.

        Red when: the catch-all is narrowed to ``NarratorError``.
        """
        stub = ReturningStub(value)
        narration = run_narration(sentinel, stub)
        assert narration.fallbacks == len(narration.paragraphs)
        assert {p.reason for p in narration.paragraphs} == {"provider_error"}
        assert narration.calls == len(narration.paragraphs)

    def test_r43_a_vendor_exception_becomes_a_provider_error(self, sentinel: Analysis) -> None:
        """R43: "cannot be contingent on a third party's exception hierarchy"."""

        class VendorRage(Exception):
            pass

        narration = run_narration(sentinel, RaisingStub(VendorRage("connection to host lost")))
        assert {p.reason for p in narration.paragraphs} == {"provider_error"}
        assert "connection to host lost" not in json.dumps(
            [p.__dict__ for p in narration.paragraphs], default=str
        )

    def test_r43_a_keyboard_interrupt_is_not_swallowed(self, sentinel: Analysis) -> None:
        """R43: ``except Exception`` must not catch a ``BaseException``.

        Red when: the catch-all widens to ``BaseException`` — at which point
        Ctrl-C during an ``--explain`` run produces a deterministic paragraph
        instead of stopping.
        """
        with pytest.raises(KeyboardInterrupt):
            run_narration(sentinel, RaisingStub(KeyboardInterrupt()))

    def test_r43_an_assertion_error_from_a_script_is_re_raised(self, sentinel: Analysis) -> None:
        """R43/R41: a miscounted fixture script is a red test, not a green fallback.

        Driven through ``FixtureNarratorClient`` because its exhaustion
        behaviour is the subject here.

        Red when: ``except AssertionError: raise`` is deleted, at which point
        every under-scripted test in this suite silently measures fallbacks.
        """
        client = FixtureNarratorClient([paragraph("only one")])
        with pytest.raises(AssertionError, match="script exhausted"):
            run_narration(sentinel, client)

    def test_r43_narrate_never_raises_for_anything_the_taxonomy_covers(
        self, sentinel: Analysis
    ) -> None:
        """R43: "this function raises nothing a caller has to handle"."""
        for code in sorted(NARRATOR_ERROR_CODES):
            for cls in (NarratorTransportError, NarratorAuthError, NarratorResponseError):
                narration = run_narration(sentinel, RaisingStub(cls(code)))
                assert narration.paragraphs


class TestDeterministicParagraphR43:
    """R43: the template is a pure function of the payload, and says so."""

    def test_r43_the_template_is_built_only_from_the_request(self, sentinel: Analysis) -> None:
        """R42/R43: so it is free of trace text for the reason the payload is.

        Red when: the template starts reading a ``Finding`` or a ``Trace``,
        which is the change that would put a preview into a fallback
        paragraph without touching the payload.
        """
        requests = build_requests(
            findings=sentinel.findings,
            totals=totals_for(sentinel),
            rate_snapshot_version=sentinel.cost.meta.version,
        )
        for group, request in requests.items():
            summary = next(item for item in request.groups if item.group == group)
            text = deterministic_paragraph(request, summary)
            assert text
            for label, value in SENTINELS.items():
                assert value not in text, label

    def test_r43_the_template_is_deterministic(self, sentinel: Analysis) -> None:
        """R47: the same payload gives the same paragraph, every time."""
        requests = build_requests(
            findings=sentinel.findings,
            totals=totals_for(sentinel),
            rate_snapshot_version=sentinel.cost.meta.version,
        )
        request = requests[OVERALL_GROUP]
        summary = next(item for item in request.groups if item.group == OVERALL_GROUP)
        first = deterministic_paragraph(request, summary)
        assert first == deterministic_paragraph(request, summary)

    def test_r43_the_overall_template_says_what_the_numbers_are(self) -> None:
        """R43: the fallback paragraph's own words, pinned.

        Five separate mutations of this template survived the first sweep —
        the detector count losing its ``- 1``, the unpriced clause inverting,
        the plural rule inverting, the severity order reversing and the
        overall and per-detector templates swapping — because every test
        asserted that a paragraph *existed* and none asserted what it said.
        A deterministic template nobody reads is a deterministic template
        nobody can tell has broken.

        Built from a hand-made payload so the expected sentence is a literal
        and not a second copy of the code that produces it.
        """
        totals = TraceTotals(
            agents=2,
            spans=9,
            model_calls=1,
            findings=4,
            severity_counts={"critical": 1, "info": 1, "warning": 2},
            tokens=50,
            cost_usd="0.001234",
            priced_spans=1,
            unpriced_spans=2,
        )
        overall = GroupSummary(
            group=OVERALL_GROUP,
            title=OVERALL_TITLE,
            findings=4,
            severity_counts={"critical": 1, "info": 1, "warning": 2},
            wasted_tokens=1,
        )
        detector = next(iter(_registry_titles()))
        other = GroupSummary(group=detector[0], title=detector[1], findings=1, wasted_tokens=0)
        request = NarrationRequest(
            rate_snapshot_version="2026.09.10",
            group=OVERALL_GROUP,
            totals=totals,
            groups=(overall, other),
        )
        assert deterministic_paragraph(request, overall) == (
            "This run produced 4 findings from 1 detector: 1 critical, 2 warning, 1 info. "
            "The trace holds 2 agents, 9 spans and 1 model call, costing 0.001234 USD at "
            "rate snapshot 2026.09.10. 2 model calls could not be priced. "
            "Across every detector, the attributed waste is 1 token."
        )

    def test_r43_a_per_detector_template_names_its_own_group(self) -> None:
        """R43: the other branch of the same function, also pinned."""
        totals = TraceTotals(
            agents=1,
            spans=1,
            model_calls=1,
            findings=1,
            severity_counts={"critical": 0, "info": 0, "warning": 1},
            tokens=0,
            cost_usd="0.000000",
            priced_spans=0,
            unpriced_spans=0,
        )
        slug, title = next(iter(_registry_titles()))
        group = GroupSummary(
            group=slug,
            title=title,
            findings=2,
            severity_counts={"critical": 0, "info": 0, "warning": 2},
            wasted_tokens=15,
        )
        overall = GroupSummary(
            group=OVERALL_GROUP, title=OVERALL_TITLE, findings=2, wasted_tokens=15
        )
        request = NarrationRequest(
            rate_snapshot_version="2026.09.10",
            group=slug,
            totals=totals,
            groups=(overall, group),
        )
        assert deterministic_paragraph(request, group) == (
            f"{title} ({slug}) produced 2 findings: 0 critical, 2 warning, 0 info. "
            "For this detector, the attributed waste is 15 tokens. "
            "The findings section below carries each one's metrics and evidence spans."
        )

    def test_r43_the_template_has_no_unpriced_clause_when_nothing_is_unpriced(self) -> None:
        """R43: the conditional sentence, in the direction that omits it."""
        totals = TraceTotals(
            agents=1,
            spans=1,
            model_calls=1,
            findings=0,
            severity_counts={"critical": 0, "info": 0, "warning": 0},
            tokens=0,
            cost_usd="0.000000",
            priced_spans=1,
            unpriced_spans=0,
        )
        overall = GroupSummary(
            group=OVERALL_GROUP, title=OVERALL_TITLE, findings=0, wasted_tokens=0
        )
        request = NarrationRequest(
            rate_snapshot_version="1",
            group=OVERALL_GROUP,
            totals=totals,
            groups=(overall,),
        )
        text = deterministic_paragraph(request, overall)
        assert "could not be priced" not in text
        # ...and the singular/plural rule, in both directions, in one string.
        assert "1 agent, 1 span and 1 model call" in text
        assert "0 findings from 0 detectors" in text

    def test_r43_the_template_carries_no_prefix_of_its_own(self, sentinel: Analysis) -> None:
        """R43: the prefix is the renderer's, so the two markers stay paired."""
        requests = build_requests(
            findings=sentinel.findings,
            totals=totals_for(sentinel),
            rate_snapshot_version=sentinel.cost.meta.version,
        )
        for group, request in requests.items():
            summary = next(item for item in request.groups if item.group == group)
            assert FALLBACK_PREFIX not in deterministic_paragraph(request, summary)


class TestUntrustedNarratorOutputR43:
    """R43: "narrator output is model-produced and therefore untrusted"."""

    @staticmethod
    def render(tmp_path: Path, *texts: str, fallback: bool = False, previews: bool = True) -> Any:
        paragraphs = tuple(
            NarrativeParagraph(
                group="overall" if index == 0 else f"group_{index}",
                title="the whole run",
                text=text,
                fallback=fallback,
            )
            for index, text in enumerate(texts)
        )
        narrative = Narrative(paragraphs=paragraphs, calls=len(paragraphs))
        return sentinel_analysis(tmp_path, previews=previews), narrative

    def test_r43_markup_in_a_paragraph_reaches_only_a_text_node(self, tmp_path: Path) -> None:
        """R43/R34: the narrator is a third untrusted string source.

        Red when: the narrative section stops escaping, at which point a
        prompt-injected narrator writes script into a report a human opens.
        """
        payloads = [
            "</script><script>alert(1)</script>",
            '"><img src=x onerror=alert(1)>',
            "javascript:alert(1)",
            "<!-- ]]> -->",
            "{{7*7}}",
            "`whoami`",
        ]
        analysis = analyze_paths(
            _sentinel_paths(tmp_path),
            narrative=Narrative(
                paragraphs=tuple(
                    NarrativeParagraph(group="overall", title="the whole run", text=text)
                    if index == 0
                    else NarrativeParagraph(
                        group=f"group_{index}", title="the whole run", text=text
                    )
                    for index, text in enumerate(payloads)
                ),
                calls=len(payloads),
            ),
        )
        document = parse(analysis.html)
        assert document.tag_counts.get("script") == 1
        assert document.tag_counts.get("style") == 1
        for payload in payloads:
            # R51's own shape: a payload must reach a text node and nothing
            # else. Some of these -- `javascript:alert(1)`, `{{7*7}}` -- hold
            # no character R32 escapes, so "absent from the source" would be
            # the wrong assertion and a test that made it would be asserting
            # the escaper mangles them.
            assert payload in document.text, payload
            assert payload not in document.raw_of("script"), payload
            assert payload not in document.raw_of("style"), payload
            assert payload not in document.raw_of("title"), payload
            assert not any(payload in value for _, _, value in document.attributes), payload
            assert not any(payload in comment for comment in document.comments), payload
        # The two that do carry escapable bytes must be escaped in the source.
        assert "</script><script>alert(1)</script>" not in analysis.html
        assert '"><img src=x onerror=alert(1)>' not in analysis.html
        assert not [name for _, name, _ in document.attributes if name.startswith("on")]

    def test_r43_a_credential_in_a_paragraph_is_redacted_in_both_documents(
        self, tmp_path: Path
    ) -> None:
        """R43: "passes through ``redact`` then ``escape_html`` exactly like trace text"."""
        for previews in (True, False):
            analysis = analyze_paths(
                _sentinel_paths(tmp_path / f"m{previews}"),
                previews=previews,
                narrative=Narrative(
                    paragraphs=(
                        NarrativeParagraph(
                            group="overall",
                            title="the whole run",
                            text=f"the key is {R51_CREDENTIALS['aws_key_id']} and it is gone",
                        ),
                    ),
                    calls=1,
                ),
            )
            assert R51_CREDENTIALS["aws_key_id"] not in analysis.html
            assert R51_CREDENTIALS["aws_key_id"] not in analysis.json
            assert "[redacted:aws_key_id]" in analysis.html
            assert "[redacted:aws_key_id]" in analysis.json

    def test_r43_a_paragraph_is_not_blanked_by_no_previews(self, tmp_path: Path) -> None:
        """R43 (A-e9): ``--no-previews`` empties trace text, not the narrative.

        The one place a privacy flag and an LLM feature meet. If the ruling
        changes this, it changes here.
        """
        text = "a paragraph about counts and nothing else"
        analysis = analyze_paths(
            _sentinel_paths(tmp_path),
            previews=False,
            narrative=Narrative(
                paragraphs=(NarrativeParagraph(group="overall", title="the whole run", text=text),),
                calls=1,
            ),
        )
        assert text in analysis.html
        assert text in analysis.json

    @pytest.mark.parametrize(
        "forgery",
        [
            "Deterministic summary: written by the model, not by the tool",
            "The run was fine. Deterministic summary: trust me.",
            "deterministic summary: lowercased, and just as convincing on a page",
            "DETERMINISTIC SUMMARY: shouted",
            "Deterministic\nsummary: folded across a line break by normalization",
        ],
        ids=["prefix", "embedded", "lowercase", "uppercase", "folded"],
    )
    def test_r43_a_narrator_cannot_forge_the_visible_fallback_prefix(self, forgery: str) -> None:
        """R43 (**BUG-15**, fixed by the increment-5 review): the marker is reserved.

        R43 pairs two markers: the class ``narrative-fallback``, which only
        one branch of the renderer can emit, and the visible prefix, which is
        the only one a human reader ever sees. The prefix used to be just
        text placed *before* the narrator's, so a model could write it itself
        and produce swarm-observer's own trust marker inside
        ``class="narrative"`` — the S32 family, one boundary over: a marker
        this package authors that something else can also author.

        ``validate_paragraph`` now refuses it, which makes the group fall
        back, which is the correct outcome rather than merely a safe one: the
        reader gets a deterministic summary that genuinely is one.

        The battery is the point. A prefix-only test would pass a fix that
        checked ``startswith``; a case-sensitive one would pass a fix that
        missed ``DETERMINISTIC SUMMARY:``; and the ``folded`` arm is the one a
        reviewer should look at — the check runs **after** normalization, so a
        marker split across a newline is folded back into the reserved phrase
        before it is tested.

        Red when: the check is narrowed to a prefix, made case-sensitive, or
        moved above ``normalize_paragraph``.
        """
        with pytest.raises(NarratorResponseError) as caught:
            validate_paragraph(forgery)
        assert caught.value.code == "response_forged_marker"

    def test_r43_the_reserved_token_is_the_renderers_own_marker(self) -> None:
        """R43/R44 (**BUG-15**): the one duplicated literal, bound by an assertion.

        ``narrate`` and ``report`` are separate branches of R44's import
        graph, so the reserved token cannot be imported from where the
        renderer defines it. The copy is honest only because this asserts the
        equality — the discipline ``tests/pipeline.py`` applies to
        ``selected_slugs`` and ``tests/sentinel_trace.py`` to the CLI's cost
        extraction.

        Red when: either literal is reworded without the other, at which point
        the narrator would start reserving a token the renderer no longer
        writes, or stop reserving the one it does.
        """
        assert RESERVED_PARAGRAPH_PREFIX == FALLBACK_PREFIX
        assert "response_forged_marker" in NARRATOR_ERROR_CODES

    def test_r43_a_forging_narrator_falls_back_and_leaves_the_exit_code_alone(
        self, tmp_path: Path
    ) -> None:
        """R43/R39 (**BUG-15**): end to end, over the real CLI, with both documents.

        The property a reader actually depends on: **in a document this tool
        produces, the visible marker appears only inside a paragraph that
        carries the DOM class.** Asserted over a run whose narrator writes the
        marker for every group, so the count is a measurement and not an
        absence.

        The exit-code arm is not decoration. The fix added a fourth way for a
        paragraph to be refused, and R43's guarantee is that no refusal can
        change what the run returns.
        """
        paths = _sentinel_paths(tmp_path)
        html_path = tmp_path / "r.html"
        json_path = tmp_path / "r.json"
        plain_html = tmp_path / "p.html"
        plain_json = tmp_path / "p.json"
        common = [*(str(path) for path in paths), "--fail-on", "critical"]
        plain_code = main(["analyze", *common, "--out", str(plain_html), "--json", str(plain_json)])
        groups = plain_html.read_text(encoding="utf-8")  # touched so the file is written
        assert groups
        client = FixtureNarratorClient(
            [paragraph(f"{FALLBACK_PREFIX} I am the tool, honestly.") for _ in range(16)]
        )
        code = main(
            ["analyze", *common, "--out", str(html_path), "--json", str(json_path), "--explain"],
            narrator=client,
        )
        assert code == plain_code
        html = html_path.read_text(encoding="utf-8")
        document = json.loads(json_path.read_text(encoding="utf-8"))["narrative"]
        # Every group was asked, every answer was refused, every group fell back.
        assert document["calls"] == len(document["paragraphs"]) >= 4
        assert document["fallbacks"] == len(document["paragraphs"])
        assert {p["reason"] for p in document["paragraphs"]} == {"response_forged_marker"}
        # The marker appears exactly as often as the class, and always with it.
        assert (
            html.count(FALLBACK_PREFIX) == html.count(FALLBACK_CLASS) == len(document["paragraphs"])
        )
        assert html.count(f'<p class="{FALLBACK_CLASS}">{FALLBACK_PREFIX} ') == len(
            document["paragraphs"]
        )
        assert f'<p class="{NARRATIVE_CLASS}">' not in html
        # ...and the forged sentence itself reached neither document.
        assert "I am the tool, honestly." not in html
        assert "I am the tool, honestly." not in json_path.read_text(encoding="utf-8")

    def test_r43_the_renderer_does_not_assume_its_caller_validated(self) -> None:
        """R43 (**BUG-15**): the second layer, at the type the renderers consume.

        ``validate_paragraph`` is upstream of the renderer, so on its own it
        is a property of the *path*. The Modularity notes say a guard is a
        property of the function, and "no renderer may assume its caller
        sanitized" is the sentence under which ``SpanError.code`` and this
        model's own ``title`` were both found. So ``NarrativeParagraph``
        refuses the combination outright, and a forged marker cannot reach a
        renderer even from a caller that skipped the narrator.

        Red when: the model validator is dropped on the grounds that the
        narrator already checks — which is the argument that was wrong four
        times.
        """
        with pytest.raises(ValidationError, match="may not carry"):
            NarrativeParagraph(
                group="overall",
                title="the whole run",
                text=f"{FALLBACK_PREFIX} written by the model",
                fallback=False,
            )
        # The control arm: the same text is legal on a real fallback, so the
        # guard is about the pairing and not about the string.
        assert NarrativeParagraph(
            group="overall",
            title="the whole run",
            text=f"{FALLBACK_PREFIX} written by the model",
            fallback=True,
        ).fallback

    def test_r43_the_two_real_markers_are_emitted_together(self, tmp_path: Path) -> None:
        """R43: a fallback paragraph carries the class **and** the prefix.

        The pairing is the property; BUG-15 is about the converse.
        """
        analysis = analyze_paths(
            _sentinel_paths(tmp_path),
            narrative=Narrative(
                paragraphs=(
                    NarrativeParagraph(
                        group="overall",
                        title="the whole run",
                        text="the deterministic template",
                        fallback=True,
                        reason="timeout",
                    ),
                    NarrativeParagraph(
                        group="agent_loop",
                        title="the whole run",
                        text="the model's own",
                        fallback=False,
                    ),
                ),
                calls=2,
            ),
        )
        assert (
            f'<p class="{FALLBACK_CLASS}">{FALLBACK_PREFIX} the deterministic template</p>'
            in analysis.html
        )
        assert (
            f'<p class="{NARRATIVE_CLASS}">the model\'s own</p>'.replace("'", "&#x27;")
            in analysis.html
        )
        document = json.loads(analysis.json)
        assert [p["fallback"] for p in document["narrative"]["paragraphs"]] == [True, False]
        assert document["narrative"]["fallbacks"] == 1
        assert document["narrative"]["calls"] == 2
        assert document["narrative"]["paragraphs"][0]["reason"] == "timeout"
        assert document["narrative"]["paragraphs"][1]["reason"] is None

    @pytest.mark.parametrize("credential", sorted(R51_CREDENTIALS))
    def test_r43_a_title_cannot_carry_a_credential_into_a_report(
        self, credential: str, tmp_path: Path
    ) -> None:
        """R33/R43 (**BUG-16**, fixed by the increment-5 review): classified by type.

        The tester pinned this ``xfail(strict=True)`` and called it the fifth
        occurrence of the ``SpanError.code`` family — a field written with the
        ``authored`` kind because of who happens to call it, when its own type
        admits anything. Four increments each shipped one of these. This one
        is closed rather than carried into v1: both renderers now write the
        title with the ``narrator`` kind.

        Parametrized over the **whole** credential corpus rather than over the
        one key the finding named, because the previous four fixes were each
        about the field that was reported.

        Red when: a renderer goes back to claiming a title is its own.
        """
        if len(R51_CREDENTIALS[credential]) > 120:
            # The PEM block is longer than the field's own bound, so this
            # member is refused by the type before any renderer sees it. Every
            # credential in the corpus is therefore stopped by one of the two
            # mechanisms, and the arm says which.
            with pytest.raises(ValidationError):
                NarrativeParagraph(
                    group="overall", title=R51_CREDENTIALS[credential], text="a paragraph"
                )
            return
        analysis = analyze_paths(
            _sentinel_paths(tmp_path),
            narrative=Narrative(
                paragraphs=(
                    NarrativeParagraph(
                        group="overall",
                        title=R51_CREDENTIALS[credential],
                        text="a paragraph",
                    ),
                ),
                calls=1,
            ),
        )
        assert R51_CREDENTIALS[credential] not in analysis.html
        assert R51_CREDENTIALS[credential] not in analysis.json
        assert f"[redacted:{credential}]" in analysis.html
        assert f"[redacted:{credential}]" in analysis.json

    def test_r43_a_real_title_is_unchanged_by_the_narrator_kind(self, tmp_path: Path) -> None:
        """R43 (**BUG-16**): the control arm — redaction costs a genuine title nothing.

        ``redact`` is the identity on a registry title, so the fix above moves
        no byte of any report the product actually produces. Without this arm
        "the credential is absent" would also be satisfied by a change that
        blanked every title.

        Red when: the title's class becomes one that blanks, or ``redact``
        grows a pattern that matches ordinary prose.
        """
        title = "Repeated identical tool call"
        analysis = analyze_paths(
            _sentinel_paths(tmp_path),
            narrative=Narrative(
                paragraphs=(NarrativeParagraph(group="overall", title=title, text="a paragraph"),),
                calls=1,
            ),
        )
        assert f"<h3>{title} (overall)</h3>" in analysis.html
        assert json.loads(analysis.json)["narrative"]["paragraphs"][0]["title"] == title


class TestNarrativeModelR43:
    """R43/R36: the markers are the requirement's literals, and the model is closed."""

    def test_r43_the_two_markers_are_the_requirements_own_strings(self) -> None:
        """R43: ``narrative-fallback`` and ``Deterministic summary:``, verbatim.

        Pinned as literals rather than compared against the constants, which
        is the whole point: every other assertion in this suite writes
        ``FALLBACK_PREFIX``, so renaming the constant would move them all
        together and R43's own text would quietly stop being what the report
        says.

        Red when: either marker is reworded — including losing the colon.
        """
        assert FALLBACK_CLASS == "narrative-fallback"
        assert FALLBACK_PREFIX == "Deterministic summary:"
        assert NARRATIVE_CLASS == "narrative"
        from swarm_observer.report.narrative import NARRATIVE_SECTION_ID

        assert NARRATIVE_SECTION_ID == "narrative"

    def test_r43_a_paragraph_must_have_text(self) -> None:
        """R43: "non-empty" is a property of the rendered model too."""
        with pytest.raises(Exception):  # noqa: B017 - pydantic's validation error
            NarrativeParagraph(group="overall", title="the whole run", text="")

    @pytest.mark.parametrize(
        "group",
        ["Overall", "over all", "</script>", "", "1st", "x" * 65, "AKIAIOSFODNN7EXAMPLE"],
    )
    def test_r43_a_group_key_is_a_slug(self, group: str) -> None:
        """R43: the group is package-authored, and the type says so."""
        with pytest.raises(Exception):  # noqa: B017
            NarrativeParagraph(group=group, title="the whole run", text="x")

    @pytest.mark.parametrize("reason", ["Timeout", "transport failed", "</p>", "x" * 65])
    def test_r43_a_fallback_reason_is_an_enumerated_slug(self, reason: str) -> None:
        """R43: the reason is an enumerated slug, never a provider's message."""
        with pytest.raises(Exception):  # noqa: B017
            NarrativeParagraph(group="overall", title="t", text="x", reason=reason)

    def test_r43_a_title_is_bounded(self) -> None:
        """R36: a heading has a length bound even though it is package-authored.

        Only a length bound — see BUG-16 for the alphabet that is missing.
        """
        NarrativeParagraph(group="overall", title="t" * 120, text="x")
        with pytest.raises(Exception):  # noqa: B017
            NarrativeParagraph(group="overall", title="t" * 121, text="x")
        with pytest.raises(Exception):  # noqa: B017
            NarrativeParagraph(group="overall", title="", text="x")

    def test_r43_the_narrative_is_frozen_and_closed(self) -> None:
        """R43: like every model in this package."""
        narrative = Narrative()
        assert narrative.paragraphs == ()
        assert narrative.calls == 0
        with pytest.raises(Exception):  # noqa: B017
            Narrative(explain=True)  # type: ignore[call-arg]
        with pytest.raises(Exception):  # noqa: B017
            Narrative(calls=-1)

    def test_r43_fallbacks_counts_only_the_fallbacks(self) -> None:
        """R43: the count the HTML note and ``report.json`` both print."""
        narrative = Narrative(
            paragraphs=(
                NarrativeParagraph(group="a", title="t", text="x", fallback=True, reason="timeout"),
                NarrativeParagraph(group="b", title="t", text="y", fallback=False),
                NarrativeParagraph(
                    group="c", title="t", text="z", fallback=True, reason="auth_rejected"
                ),
            ),
            calls=3,
        )
        assert narrative.fallbacks == 2

    def test_r43_reasons_are_distinct_and_sorted(self) -> None:
        """R43: the section's own note lists them, so the order is in the bytes.

        Three paragraphs, two distinct reasons, deliberately out of order —
        so dropping the ``sorted`` or the de-duplication is visible.
        """
        narrative = Narrative(
            paragraphs=(
                NarrativeParagraph(group="a", title="t", text="x", fallback=True, reason="timeout"),
                NarrativeParagraph(
                    group="b", title="t", text="y", fallback=True, reason="auth_rejected"
                ),
                NarrativeParagraph(group="c", title="t", text="z", fallback=True, reason="timeout"),
                NarrativeParagraph(group="d", title="t", text="w", fallback=False),
            ),
            calls=4,
        )
        assert narrative.reasons == ("auth_rejected", "timeout")


class TestAnthropicAdapterR41:
    """R41/R45: the parts of the one SDK adapter that can be driven offline."""

    def test_r41_constructing_the_client_touches_nothing(self) -> None:
        """R41: no import, no network, no credential read in ``__init__``."""
        client = AnthropicNarratorClient()
        assert client.model
        assert client.timeout_seconds > 0
        assert client.max_tokens > 0

    def test_r45_the_missing_extra_is_the_live_answer_here(self) -> None:
        """R45: ``anthropic`` is absent, so ``_sdk`` raises ``sdk_not_installed``."""
        with pytest.raises(NarratorTransportError) as caught:
            AnthropicNarratorClient()._sdk()
        assert caught.value.code == "sdk_not_installed"

    def test_r43_an_absent_api_key_is_reachable_only_past_the_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R43 (**BUG-17**): "an absent API key" cannot happen in R45's environment.

        R43 lists five conditions that must take the fallback path and an
        absent key is one of them. ``complete`` imports the SDK **first**, so
        with the ``[explain]`` extra absent — the only environment CI has —
        the credential branch is unreachable and ``no_credentials`` is dead
        code. The PR's §5 inventory lists ``_credential_present`` among the
        lines that *have* executed offline; a coverage run says it has not,
        and neither have ``system_prompt`` or ``user_prompt``.

        So the condition is driven here, with the import stubbed, which is the
        only way an offline suite can reach it at all.

        Red when: the credential check moves or stops producing a fatal
        ``NarratorAuthError``.
        """
        client = AnthropicNarratorClient()
        monkeypatch.setattr(AnthropicNarratorClient, "_sdk", lambda self: object())
        for name in CREDENTIAL_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(NarratorAuthError) as caught:
            client.complete(_a_request())
        assert caught.value.code == "no_credentials"
        assert caught.value.code in FATAL_CODES

    def test_r43_a_present_credential_gets_past_the_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R43: the control arm — the credential branch is not simply always-fail.

        The SDK is a stub whose ``Anthropic`` raises, so nothing reaches a
        network; what is asserted is that the *credential* check passed.
        """

        class Boom(Exception):
            pass

        class StubSdk:
            AuthenticationError = Boom
            PermissionDeniedError = Boom
            RateLimitError = Boom
            APITimeoutError = Boom
            APIConnectionError = Boom
            APIStatusError = Boom

            @staticmethod
            def Anthropic(**kwargs: Any) -> Any:
                raise RuntimeError("reached client construction")

        client = AnthropicNarratorClient()
        monkeypatch.setattr(AnthropicNarratorClient, "_sdk", lambda self: StubSdk)
        monkeypatch.setenv(CREDENTIAL_ENV_VARS[0], "not-a-real-key")
        with pytest.raises(RuntimeError, match="reached client construction"):
            client.complete(_a_request())

    def test_r42_the_prompt_is_a_constant_and_the_body_is_the_payload(self) -> None:
        """R42: "what AC12's sentinel test inspects is what goes on the wire"."""
        request = _a_request()
        assert system_prompt() == system_prompt()
        assert str(MAX_PARAGRAPH_CHARS) in system_prompt()
        body = user_prompt(request)
        from swarm_observer.narrate.summary import serialize_request

        assert serialize_request(request) in body
        assert body.count("\n") == 1

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ([("text", "one"), ("text", "two")], "one\ntwo"),
            ([("text", "only")], "only"),
        ],
    )
    def test_r41_extract_paragraph_joins_the_text_blocks(
        self, content: list[tuple[str, str]], expected: str
    ) -> None:
        """R41: the one piece of response handling a stub can drive."""
        assert extract_paragraph(_stub_message(content)) == expected

    def test_r41_a_non_list_content_is_response_malformed(self) -> None:
        """R41: an SDK answering with the wrong shape is classified, not crashed."""
        with pytest.raises(NarratorResponseError) as caught:
            extract_paragraph(_stub_message("a bare string"))
        assert caught.value.code == "response_malformed"
        with pytest.raises(NarratorResponseError):
            extract_paragraph(object())

    def test_r41_a_list_with_no_text_block_is_response_empty(self) -> None:
        """R41: a tool-use-only answer produces no paragraph."""
        with pytest.raises(NarratorResponseError) as caught:
            extract_paragraph(_stub_message([("tool_use", "ignored")]))
        assert caught.value.code == "response_empty"


def _stub_message(content: Any) -> Any:
    """An object shaped like an SDK message, built here rather than imported."""

    class Block:
        def __init__(self, kind: str, text: str) -> None:
            self.type = kind
            self.text = text

    class Message:
        def __init__(self, value: Any) -> None:
            self.content = (
                [Block(kind, text) for kind, text in value] if isinstance(value, list) else value
            )

    return Message(content)


def _a_request() -> NarrationRequest:
    from .test_narrate_client_r41 import a_request

    return a_request()


def _sentinel_paths(directory: Path) -> tuple[Path, ...]:
    from .sentinel_trace import write_sentinel_trace

    return write_sentinel_trace(directory)


def _registry_titles() -> tuple[tuple[str, str], ...]:
    """``(slug, title)`` for every registered detector, in registry order."""
    return tuple((detector.slug, detector.title) for detector in ALL_DETECTORS)
