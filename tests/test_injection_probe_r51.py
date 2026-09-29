"""R51 and AC3: the end-to-end injection probe — what actually reaches the user.

R51 asks one question: given a transcript in which every free-text field is a
payload, what does the rendered file contain? It answers it against the
**parsed document**, not against a function's return value, and that is the
whole point of the requirement.

**Two traces, and the second one is the reason this module is long.**

``tests/fixtures/traces/hostile.jsonl`` is the trace R51 names, and it does not
load every field R51 names. It carries no payload in ``AgentRun.agent_type``,
``description``, ``parent_agent_id`` or ``depth`` — those arrive through A1's
``agent-<id>.meta.json`` sidecar, which
``TestCorpusShapeT6::test_t6_no_sidecar_metadata_sits_beside_the_corpus``
forbids in that directory — and none in ``SpanError``, because none of its
records is an API error. Those are precisely the fields increment 4's HTML
renderer started rendering: the lane legend, whose unconditional rendering A-d9
justifies as anti-vacuity, and the spans table's error cell.

So a probe run only against the checked-in fixture would assert "no payload
escapes the lane legend" about a legend whose payload-bearing columns are an em
dash. That is this project's signature defect with R51's own probe as its
subject, and it is reported as **BUG-8** with **S29**.
``tests/hostile_corpus.py`` builds the trace that closes it, in ``tmp_path``, so
the T6 rule stands.

**Per-field markers.** Every field in the extended trace carries its own
:data:`~tests.hostile_corpus.FIELD_MARKERS` token. ``TestEveryFieldReaches`` is
therefore a *presence* check that names the field when it goes red. The
increment-4 coder's own finding — the spans table rendering only the first
non-empty preview, so ``hostile.jsonl``'s AWS key, ``sk-ant-`` key and PEM
block were never rendered and every credential check passed vacuously — is
exactly what a shared payload cannot detect and a per-field marker can.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from swarm_observer.report.html import SCRIPT_SHA256, STYLE_SHA256

from .detector_corpus import FIXTURE_DIR
from .hostile_corpus import (
    CONFORMING_CREDENTIAL_TOOL_NAME,
    CREDENTIAL_AGENT_ID,
    CREDENTIAL_RECORD_TYPE,
    FIELD_MARKERS,
    R51_CREDENTIALS,
    R51_EXPECTED_MARKERS,
    R51_HUGE_LENGTH,
    R51_PAYLOADS,
    R51_RTL_OVERRIDE,
    write_hostile_trace,
)
from .pipeline import Analysis, analyze_paths
from .rendered import parse, sha256_text
from .test_report_html_r34_r35_r36 import ATTRIBUTE_SHAPES, URL_BEARING_ATTRIBUTES

HOSTILE_FIXTURE = FIXTURE_DIR / "hostile.jsonl"

#: R51's named minimum, typed from the requirement. The corpus module is data;
#: this is the requirement, and the two are compared so the corpus cannot be
#: narrowed in the commit that makes a check fail.
R51_REQUIRED_PAYLOADS: tuple[str, ...] = (
    "</script><script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "<!--",
    "]]>",
    "&lt;script&gt;",
    "{{7*7}}",
    "../../etc/passwd",
)

#: The fields R51 names as payload carriers, as ``Trace`` attribute paths.
R51_NAMED_FIELDS: tuple[str, ...] = (
    "AgentRun.description",
    "Span.tool_name",
    "Span.tool_input_preview",
    "Span.tool_result_preview",
    "Span.text_preview",
    "Span.model",
    "SpanError.detail",
)


def json_strings(document: Any) -> list[str]:
    """Every string anywhere in a parsed JSON document, keys included."""
    found: list[str] = []
    if isinstance(document, str):
        found.append(document)
    elif isinstance(document, dict):
        for key, value in document.items():
            found.append(key)
            found.extend(json_strings(value))
    elif isinstance(document, list):
        for value in document:
            found.extend(json_strings(value))
    return found


@pytest.fixture(scope="module")
def fixture_previews() -> Analysis:
    """R51's named fixture, rendered with previews."""
    return analyze_paths([HOSTILE_FIXTURE])


@pytest.fixture(scope="module")
def fixture_blanked() -> Analysis:
    """R51's named fixture, rendered under ``--no-previews``."""
    return analyze_paths([HOSTILE_FIXTURE], previews=False)


@pytest.fixture(scope="module")
def extended_previews(tmp_path_factory: pytest.TempPathFactory) -> Analysis:
    """The extended hostile trace — every R51-named field loaded — with previews."""
    return analyze_paths(write_hostile_trace(tmp_path_factory.mktemp("r51-previews")))


@pytest.fixture(scope="module")
def extended_blanked(tmp_path_factory: pytest.TempPathFactory) -> Analysis:
    """The extended hostile trace under ``--no-previews``."""
    return analyze_paths(
        write_hostile_trace(tmp_path_factory.mktemp("r51-blanked")), previews=False
    )


class TestTheCorpusIsTheRequirementsR51:
    """R51: the payload set is checked in and cannot shrink to make a probe pass."""

    @pytest.mark.parametrize("payload", R51_REQUIRED_PAYLOADS)
    def test_r51_every_named_payload_is_in_the_checked_in_corpus(self, payload: str) -> None:
        """R51: the minimum list, one test per entry.

        Red when: a payload is removed from ``hostile_corpus.R51_PAYLOADS`` —
        the move that turns "every payload is in a text node" into a smaller
        claim without changing a single assertion.
        """
        assert payload in set(R51_PAYLOADS.values())

    def test_r51_the_corpus_holds_a_data_uri_an_rtl_override_and_a_huge_string(self) -> None:
        """R51: the three entries whose assertions are not "appears in a text node".

        Red when: any of them is dropped. They are held apart from the list
        above because each has a different expected outcome — the ``data:`` URI
        appears as text, the override is *removed* by R8 and R32, and the
        80,000-character string is truncated by R8.
        """
        assert R51_PAYLOADS["data_uri"].startswith("data:text/html;base64,")
        assert R51_RTL_OVERRIDE == "‮"
        assert R51_HUGE_LENGTH == 80_000

    def test_r51_the_corpus_holds_the_three_credential_shapes(self) -> None:
        """R51: an ``AKIA``-shaped key, an ``sk-ant-``-shaped key and a PEM block.

        Red when: one is removed — and the ``[redacted:…]`` assertions would
        then pass while covering two credentials instead of three.
        """
        assert R51_CREDENTIALS["aws_key_id"].startswith("AKIA")
        assert R51_CREDENTIALS["anthropic_key"].startswith("sk-ant-")
        assert "BEGIN RSA PRIVATE KEY" in R51_CREDENTIALS["private_key"]
        assert len(R51_EXPECTED_MARKERS) == 3

    def test_r51_every_field_marker_is_distinct_and_survives_both_boundaries(self) -> None:
        """R51: a marker that redaction or escaping mangled could not prove a field rendered.

        Red when: a marker gains a character R32 escapes or a substring R33
        redacts — the assertion "this field reached a text node" would then be
        untrue for a reason that has nothing to do with the field.
        """
        from swarm_observer.report.escape import escape_html
        from swarm_observer.report.redact import redact

        markers = list(FIELD_MARKERS.values())
        assert len(set(markers)) == len(markers)
        for marker in markers:
            assert redact(marker) == marker, marker
            assert escape_html(marker) == marker, marker


class TestTheCheckedInFixtureLeavesFieldsEmptyBug8:
    """BUG-8: R51's named fixture does not load every field R51 names.

    These tests assert the **gap**, deliberately, in the style the increment-3
    tester used for the S14 case that increment 4 inverted in place. When
    ``hostile.jsonl`` gains a sidecar and an API-error record — or when the T6
    no-sidecar rule is amended (**S29**) — these go red, and the instruction is
    in the docstring: delete this class, and delete the corresponding
    ``extended_*`` arms only if the extended corpus has nothing else to add.
    """

    def test_r51_hostile_jsonl_carries_no_agent_metadata(self, fixture_previews: Analysis) -> None:
        """BUG-8: ``agent_type``, ``description``, ``parent_agent_id``, ``depth`` are all empty.

        R51 names "agent descriptions" first in its list of payload-bearing
        fields. The lane legend renders all four, and over this fixture all four
        render as ``—`` or as nothing — so every assertion about the lane legend
        made against this fixture alone is satisfied by a legend with no content.
        """
        agents = fixture_previews.trace.agents
        assert len(agents) == 1
        agent = agents[0]
        assert agent.agent_type is None
        assert agent.description == ""
        assert agent.parent_agent_id is None
        assert agent.depth is None

    def test_r51_hostile_jsonl_carries_no_span_error(self, fixture_previews: Analysis) -> None:
        """BUG-8: no span has an ``error``, so ``SpanError.detail`` is never rendered.

        R51 names "error details". The spans table's error cell renders ``—``
        for every row of this fixture.
        """
        assert all(span.error is None for span in fixture_previews.trace.spans)

    def test_r51_hostile_jsonl_carries_no_r16_conforming_credential_tool_name(
        self, fixture_previews: Analysis
    ) -> None:
        """S13/BUG-8: every tool name in this fixture is *non*-conforming.

        R16 replaces a non-conforming name with ``<non-conforming>``, so
        ``metrics.tool_name`` never carries a trace byte here and the redaction
        of ``metrics`` is never exercised by this fixture. S13's whole point is
        the name that *does* conform and is still a credential.
        """
        names = {finding.metrics.get("tool_name") for finding in fixture_previews.findings}
        assert names == {"<non-conforming>"}


class TestEveryFieldReaches:
    """R51: each named field's own marker reaches both documents. The non-vacuous arm."""

    @pytest.mark.parametrize("field", sorted(FIELD_MARKERS))
    def test_r51_the_html_renders_every_payload_bearing_field(
        self, field: str, extended_previews: Analysis
    ) -> None:
        """R51: this is the check the coder's spans-table defect would have failed.

        Red when: the renderer stops rendering that field — which is what
        happened to ``tool_result_preview`` and took three credentials out of
        the document with it, leaving every credential assertion green.
        """
        marker = FIELD_MARKERS[field]
        assert marker in parse(extended_previews.html).text, field

    @pytest.mark.parametrize("field", sorted(FIELD_MARKERS))
    def test_r51_the_json_carries_every_payload_bearing_field(
        self, field: str, extended_previews: Analysis
    ) -> None:
        """R51/AC3: the same field set in ``report.json``.

        Red when: the two renderers drift — a field one of them drops is a
        reader seeing different evidence depending on which file they open.
        """
        strings = json_strings(json.loads(extended_previews.json))
        assert any(FIELD_MARKERS[field] in value for value in strings), field

    @pytest.mark.parametrize("field", R51_NAMED_FIELDS)
    def test_r51_every_field_the_requirement_names_is_covered_by_a_marker(self, field: str) -> None:
        """R51: the requirement's list and the corpus's list are the same list.

        Red when: R51 gains a field and the corpus does not — the probe would
        then be silent about it, which is how a requirement becomes a paragraph.
        """
        assert field in FIELD_MARKERS


class TestPayloadsReachOnlyTextNodesAC3:
    """AC3: "every hostile payload occurs only in text nodes"."""

    @pytest.mark.parametrize("label", sorted(R51_PAYLOADS))
    def test_ac3_each_payload_is_in_a_text_node_of_the_fixture_report(
        self, label: str, fixture_previews: Analysis
    ) -> None:
        """AC3: over R51's own named fixture.

        The parse decodes character references, so the payload is looked for in
        its own bytes rather than in its escaped form. Red when: a payload stops
        being rendered — which would make every "not in an attribute" assertion
        below vacuous for it.
        """
        assert R51_PAYLOADS[label] in parse(fixture_previews.html).text

    @pytest.mark.parametrize("label", sorted(R51_PAYLOADS))
    def test_ac3_each_payload_is_in_a_text_node_of_the_extended_report(
        self, label: str, extended_previews: Analysis
    ) -> None:
        """AC3: and over the trace that loads every field R51 names."""
        assert R51_PAYLOADS[label] in parse(extended_previews.html).text

    @pytest.mark.parametrize("fixture_name", ["fixture_previews", "extended_previews"])
    def test_ac3_no_payload_reaches_an_attribute_value(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3/R34: "no trace-derived byte leaves a text node", asserted at the attribute.

        Red when: any trace string is interpolated into an attribute. This does
        not consult the allowlist at all — it is the property the allowlist is a
        proxy for.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        needles = [*R51_PAYLOADS.values(), *FIELD_MARKERS.values()]
        for element, name, value in parse(analysis.html).attributes:
            for needle in needles:
                assert needle not in value, (element, name, needle)

    @pytest.mark.parametrize("fixture_name", ["fixture_previews", "extended_previews"])
    def test_ac3_no_payload_reaches_the_script_the_style_or_a_comment(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3/R34: the three contexts a payload must never reach.

        ``<title>`` is included: its content is not visible in the body, so a
        payload there would satisfy a naive "in a text node" check while being
        invisible to a reader and live in the tab title.

        Red when: any trace byte is interpolated into the script, the style or a
        comment — which is what the R34 SHA-256 pin exists to make impossible
        and what this asserts independently of the digest.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        document = parse(analysis.html)
        contexts = {
            "script": document.raw_of("script"),
            "style": document.raw_of("style"),
            "title": document.raw_of("title"),
            "comments": "\n".join(document.comments),
            "declarations": "\n".join(document.declarations),
            "processing instructions": "\n".join(document.processing_instructions),
        }
        for label, text in contexts.items():
            for needle in [*R51_PAYLOADS.values(), *FIELD_MARKERS.values()]:
                assert needle not in text, (label, needle)

    def test_ac3_the_rtl_override_is_removed_rather_than_escaped(
        self, extended_previews: Analysis
    ) -> None:
        """R51/R8/R32: U+202E is ``Cf``, so both boundaries replace it with a space.

        R51 lists it as a payload, and the honest assertion about it is
        *absence*, not presence in a text node. A probe that asserted presence
        would be asserting the opposite of what the product does — and would be
        satisfied only by a report that had stopped removing it.

        Red when: the non-printable rule stops covering ``Cf``.
        """
        assert R51_RTL_OVERRIDE not in extended_previews.html
        assert R51_RTL_OVERRIDE not in extended_previews.json
        # The non-vacuous arm: the text around the override did render.
        assert R51_PAYLOADS["javascript_url"] in parse(extended_previews.html).text

    def test_ac3_the_eighty_thousand_character_string_is_truncated_by_r8(
        self, extended_previews: Analysis
    ) -> None:
        """R8/R51: a preview is 240 code points plus an ellipsis, so the document is bounded.

        Red when: the preview cap is lifted — a single trace field would then be
        able to make a 2 MB document out of one record.
        """
        longest = max(len(text) for text in parse(extended_previews.html).text_nodes)
        assert longest < 1_000, longest
        assert "B" * 300 not in extended_previews.html
        assert "…" in extended_previews.html

    def test_ac3_double_escaping_does_not_happen_in_the_rendered_document(
        self, extended_previews: Analysis
    ) -> None:
        """AC4 asserted against the rendered file rather than against ``escape_html``.

        The corpus contains the literal text ``&lt;script&gt;``; escaped once it
        is ``&amp;lt;script&amp;gt;``, escaped twice ``&amp;amp;lt;``.

        Red when: a renderer escapes a string the sanitizer already escaped —
        the defect a second escaping boundary would introduce.
        """
        assert "&amp;amp;" not in extended_previews.html
        assert "&amp;lt;" in extended_previews.html
        assert parse(extended_previews.html).text.count("&lt;script&gt;") >= 1


class TestCredentialsAreRedactedAC3:
    """AC3: ``AKIA…``, ``sk-ant-…`` and the PEM block "appear nowhere"."""

    @pytest.mark.parametrize("label", sorted(R51_CREDENTIALS))
    @pytest.mark.parametrize(
        "fixture_name", ["fixture_previews", "extended_previews", "fixture_blanked"]
    )
    def test_ac3_no_credential_shape_appears_in_either_document(
        self, label: str, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3: in the HTML and in the JSON, with previews and without.

        The credential is searched for by its first line, because R8 normalizes
        the PEM block's newlines into spaces. Red when: redaction is dropped, or
        a field bypasses the boundary — the shape of increment 3's BUG-2.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        needle = R51_CREDENTIALS[label].splitlines()[0]
        assert needle not in analysis.html, label
        assert needle not in analysis.json, label

    @pytest.mark.parametrize("marker", R51_EXPECTED_MARKERS)
    def test_ac3_the_redaction_markers_are_present_instead(
        self, marker: str, extended_previews: Analysis
    ) -> None:
        """AC3: "their ``[redacted:…]`` markers do" appear.

        The arm that separates "redacted" from "never rendered". Without it, the
        test above is satisfied by a renderer that dropped the field — which is
        exactly what the spans table was doing.

        Red when: a credential is dropped rather than replaced.
        """
        assert marker in extended_previews.html
        assert marker in extended_previews.json

    def test_s13_a_conforming_credential_tool_name_is_redacted_in_metrics(
        self, extended_previews: Analysis
    ) -> None:
        """S13/A-d3: R16's shape check admits ``AKIAIOSFODNN7EXAMPLE`` as a legal tool name.

        The finding's ``metrics`` carries it verbatim — R16 is a shape check,
        not a secret check — so the *only* thing that keeps it out of the
        document is ``sanitize.metric_value`` running the redactor over it.

        Red when: ``TRACE_DERIVED_METRIC_KEYS`` loses ``tool_name``, or the
        metrics line stops going through ``metric_value``. The first assertion
        is what makes the second meaningful: it proves the credential really is
        in the input.
        """
        raw = {finding.metrics.get("tool_name") for finding in extended_previews.findings}
        assert CONFORMING_CREDENTIAL_TOOL_NAME in raw
        assert CONFORMING_CREDENTIAL_TOOL_NAME not in extended_previews.html
        assert "tool_name&#x3D;[redacted:aws_key_id]" in extended_previews.html

    @pytest.mark.parametrize(
        ("label", "needle", "marker", "where"),
        [
            (
                "agent id",
                CREDENTIAL_AGENT_ID,
                "[redacted:aws_key_id]",
                "spans table, lane legend, cost.by_agent",
            ),
            (
                "agent description",
                R51_CREDENTIALS["aws_key_id"],
                "[redacted:aws_key_id]",
                "lane legend",
            ),
            (
                "parse warning detail",
                CREDENTIAL_RECORD_TYPE,
                "[redacted:github_token]",
                "warnings table",
            ),
        ],
    )
    @pytest.mark.parametrize("fixture_name", ["extended_previews", "extended_blanked"])
    def test_r33_every_field_that_reaches_a_report_as_an_identifier_is_redacted(
        self,
        label: str,
        needle: str,
        marker: str,
        where: str,
        fixture_name: str,
        request: pytest.FixtureRequest,
    ) -> None:
        """R33/S16: a field whose *alphabet* looks safe is not thereby *secret*-safe.

        Each of these three reaches a report through
        ``sanitize.identifier`` — redacted in **both** modes, never blanked,
        because they are join keys (S16). Each also has a constrained alphabet
        that a reader could mistake for a guarantee: ``AGENT_ID_PATTERN`` admits
        ``AKIAIOSFODNN7EXAMPLE`` and ``WARNING_DETAIL_PATTERN`` admits a GitHub
        token. The alphabet stops *markup*; only R33 stops a credential.

        All three arms were added after the increment-4 mutation sweep, where
        the mutants that replaced ``_ident`` with a plain escape on the lane
        legend and the warnings table **survived** — not because the guard was
        right but because no corpus contained a credential-shaped identifier.

        Red when: any of the three stops going through ``identifier``, in either
        mode.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        assert needle not in analysis.html, (label, where)
        assert needle not in analysis.json, (label, where)
        assert marker in analysis.html, (label, where)
        assert marker in analysis.json, (label, where)

    def test_r33_the_identifier_arms_are_not_vacuous(self, extended_previews: Analysis) -> None:
        """R33: the three credentials above really are in the trace.

        Without this, the assertions above are satisfied by a trace that never
        held a credential — instance 7's exact shape.

        Red when: the corpus stops loading one of the three.
        """
        trace = extended_previews.trace
        assert any(agent.agent_id == CREDENTIAL_AGENT_ID for agent in trace.agents)
        assert any(R51_CREDENTIALS["aws_key_id"] in agent.description for agent in trace.agents)
        assert any(warning.detail == CREDENTIAL_RECORD_TYPE for warning in trace.warnings)
        assert any(span.agent_id == CREDENTIAL_AGENT_ID for span in trace.spans)

    def test_s21_a_credential_in_a_filename_is_redacted(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """S21: ``analyze <dir>`` reads whatever basenames the directory holds.

        A filename is not trace-derived and is attacker-influenceable all the
        same. ``SourceFile.name`` has no alphabet constraint on the model — only
        "is a basename" — so markup and a credential can both live in one.

        Red when: ``source.name`` stops going through ``identifier``, which is
        the reading "a filename is not trace-derived" would produce.
        """
        directory = tmp_path_factory.mktemp("hostile-name")
        paths = write_hostile_trace(directory)
        renamed = directory / "AKIAIOSFODNN7EXAMPLE-\x1b[31m<script>.jsonl"
        paths[0].rename(renamed)
        analysis = analyze_paths([renamed, *paths[1:]])
        assert "AKIAIOSFODNN7EXAMPLE" not in analysis.html
        assert "AKIAIOSFODNN7EXAMPLE" not in analysis.json
        assert "\x1b" not in analysis.html
        assert "[redacted:aws_key_id]" in analysis.html
        # The non-vacuous arm: the file *is* named in the report.
        assert "jsonl" in parse(analysis.html).text


class TestStructuralPropertiesOverTheHostileFixtureAC3:
    """AC3's structural clauses, asserted over R51's own named fixture."""

    @pytest.mark.parametrize("fixture_name", ["fixture_previews", "fixture_blanked"])
    def test_ac3_one_script_and_one_style_with_the_pinned_digests(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3: one ``<script>`` and one ``<style>``, digests equal to the pinned constants.

        Taken from the parsed element, which is what proves nothing was
        interpolated at render time. Red when: a payload breaks out into the
        script, which is what ``</script><script>alert(1)</script>`` is for.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        document = parse(analysis.html)
        assert document.tag_counts.get("script") == 1
        assert document.tag_counts.get("style") == 1
        assert sha256_text(document.script_body()) == SCRIPT_SHA256
        assert sha256_text(document.style_body()) == STYLE_SHA256

    @pytest.mark.parametrize(
        "fixture_name",
        ["fixture_previews", "fixture_blanked", "extended_previews", "extended_blanked"],
    )
    def test_ac3_every_attribute_value_matches_the_generated_allowlist(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3/R34: over all four hostile renders.

        Red when: a payload reaches an attribute in any of the four.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        allowlist = analysis.allowlist()
        offenders = [
            (element, name, value)
            for element, name, value in parse(analysis.html).attributes
            if name not in allowlist or value not in allowlist[name]
        ]
        assert not offenders, offenders

    @pytest.mark.parametrize(
        "fixture_name",
        ["fixture_previews", "fixture_blanked", "extended_previews", "extended_blanked"],
    )
    def test_ac3_no_on_attribute_and_no_url_bearing_attribute_exists(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3: "no ``on*`` or URL-bearing attribute exists".

        Independent of the allowlist: this reads attribute *names* only, so it
        holds even if the allowlist were wrong.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        names = {name for _, name, _ in parse(analysis.html).attributes}
        assert not {name for name in names if name.startswith("on")}
        assert not names & URL_BEARING_ATTRIBUTES
        assert not names - set(ATTRIBUTE_SHAPES)


class TestTheJsonDocumentAC3:
    """AC3: "``report.json`` parses as JSON with the same payload/redaction properties"."""

    @pytest.mark.parametrize("fixture_name", ["fixture_previews", "extended_previews"])
    def test_ac3_the_json_report_parses(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3: a payload cannot break the JSON document's structure.

        Red when: a string is written into the JSON by concatenation rather than
        by ``json.dumps`` — the JSON analogue of an escaping bug.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        document = json.loads(analysis.json)
        assert isinstance(document, dict)
        assert document["meta"]["schema_version"]
        assert document["spans"] and document["agents"]
        assert analysis.json.endswith("\n")

    def test_ac3_the_json_reports_payloads_as_data_not_as_markup(
        self, extended_previews: Analysis
    ) -> None:
        """AC3: the payloads are values in the JSON, which is what "same properties" means.

        Red when: the JSON renderer starts HTML-escaping, which would make the
        two documents disagree about what the trace said (R33's module docstring
        names this as the reason escaping is not in ``sanitize``).
        """
        strings = json_strings(json.loads(extended_previews.json))
        for payload in R51_PAYLOADS.values():
            assert any(payload in value for value in strings), payload
        assert "&lt;" not in extended_previews.json.replace("&lt;script&gt;", "")


class TestNoPreviewsMode:
    """AC3: "When the same run adds ``--no-previews``, no hostile payload appears anywhere"."""

    @pytest.mark.parametrize("label", sorted(R51_PAYLOADS))
    @pytest.mark.parametrize("fixture_name", ["fixture_blanked", "extended_blanked"])
    def test_ac3_no_payload_appears_anywhere_under_no_previews(
        self, label: str, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """AC3/R38: in either file, anywhere — not only in a text node.

        Red when: a field is added to the renderer without being routed through
        ``free_text`` — increment 3's BUG-2, which found three of them.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        payload = R51_PAYLOADS[label]
        assert payload not in analysis.html, label
        assert payload not in analysis.json, label

    @pytest.mark.parametrize("field", sorted(FIELD_MARKERS))
    def test_r38_no_field_marker_survives_no_previews(
        self, field: str, extended_blanked: Analysis
    ) -> None:
        """R38/A10: the per-field form of the same claim, which names the leaking field.

        Red when: one field bypasses the blanking. A single "no payload appears"
        assertion would say only that something leaked.
        """
        assert FIELD_MARKERS[field] not in extended_blanked.html, field
        assert FIELD_MARKERS[field] not in extended_blanked.json, field

    def test_r38_the_blanked_document_is_still_a_report(self, extended_blanked: Analysis) -> None:
        """AC3: the non-vacuous arm — a renderer that emitted nothing passes every check above.

        Red when: ``--no-previews`` starts blanking the join keys R33 and S16
        say it must not — ``agent_id``, ``ParseWarning.detail``, the source file
        names — which would collapse the spans table, the legend and
        ``cost.by_agent`` into one row each.
        """
        document = parse(extended_blanked.html)
        text = document.text
        assert document.tag_counts.get("tr", 0) > 10
        assert "alpha" in text and "beta" in text
        assert "[redacted:aws_key_id]" in text, "the credential-shaped agent id"
        assert "totally_unknown_type" in text
        assert "agent-alpha.jsonl" in text
        assert "Previews are omitted" in text

    def test_s27_no_previews_changes_which_findings_exist(
        self, extended_previews: Analysis, extended_blanked: Analysis
    ) -> None:
        """S27: ``--no-previews`` is not "the same report with the text removed".

        A10 blanks at ingest, and R22's ``unknown_tool`` reason is decided by
        matching five phrases against ``tool_result_preview`` — the one place in
        the product a detector reads trace free text (A8). With the text gone
        the ``critical`` finding does not exist.

        Pinned so that any test which diffs the two documents and attributes the
        difference to blanking is visibly wrong, and so that a PM ruling on S27
        has something to change.

        Red when: R22's phrase matching moves to a value computed before
        blanking, which is the fix the coder prefers.
        """
        with_text = {(f.detector, f.severity) for f in extended_previews.findings}
        without = {(f.detector, f.severity) for f in extended_blanked.findings}
        assert ("unresolved_tool_call", "critical") in with_text
        assert ("unresolved_tool_call", "critical") not in without
        assert without < with_text


def test_r51_the_probe_reads_the_parsed_document() -> None:
    """R51: "asserts against the rendered file as a browser would see it".

    A structural guard on this module, and on the helper it reads through: every
    structural claim goes via ``tests.rendered.parse``, which is the only place
    the document is turned into elements, attributes and text nodes. The
    byte-level claims that remain (a credential must appear *nowhere*, not only
    outside a text node) are deliberately stronger than the parse, not weaker.

    Red when: this module starts importing the renderer and asserting on its
    return value directly — the draftsmith increment-5 defect R51 names.
    """
    with open(__file__, encoding="utf-8") as handle:
        text = handle.read()
    imported = re.findall(r"^from swarm_observer[^\n]*", text, flags=re.MULTILINE)
    assert imported == ["from swarm_observer.report.html import SCRIPT_SHA256, STYLE_SHA256"], (
        "this module imports a renderer directly; drive it through tests.pipeline"
    )
    assert text.count("parse(") > 10
