"""R34, R35 and R36: what the rendered document is allowed to contain.

Everything here reads a :class:`tests.rendered.Document` — the file as
``html.parser`` yields it — except the two byte-level checks R35 needs, which
are named where they are made.

**On the attribute allowlist.** ``report.html.attribute_allowlist`` is the
coder's oracle and the coder said so. This module does not check membership in
it and stop. :class:`TestAttributesIndependentlyClassified` enumerates every
attribute the document actually contains and, for each, asserts a *shape the
requirement justifies*, written here from R34's prose. The two oracles then
have to agree (:class:`TestAttributeAllowlistR34`), and the disagreement that
matters is named: an attribute added to the document and to the allowlist in one
commit satisfies the coder's oracle and fails this one, because this one does
not know about it.

**On goldens.** There are two, and neither stands alone. Each is paired with the
structural assertions in this module over the *same* bytes, and its docstring
says what it pins that they do not: the document's ordering and whitespace —
the thing R47 promises and no structural check can see. When a golden goes red,
the structural tests are what say whether a security property broke or a
cosmetic byte moved; if only the golden is red, it is the second, and it is
updated deliberately by running ``tests/golden/regenerate.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from swarm_observer.detect.base import SEVERITIES, Finding, build_finding
from swarm_observer.detect.registry import DETECTOR_SLUGS
from swarm_observer.report.html import (
    ALL_SEVERITIES,
    CSP_CONTENT,
    CSS_CLASSES,
    FORBIDDEN_SCRIPT_APIS,
    REPORT_SCRIPT,
    REPORT_STYLE,
    SCRIPT_SHA256,
    SECTION_IDS,
    SPANS_TABLE_CAP,
    STYLE_SHA256,
    UNTIMED_LIST_CAP,
    sha256_of,
)
from swarm_observer.report.timeline import LANE_HEIGHT, RECT_CLASSES

from .harness import assert_matches_golden, golden_path
from .hostile_corpus import write_hostile_trace
from .pipeline import Analysis, analyze_paths, analyze_trace
from .rendered import head_element_order, parse, section_ids_in_order, sha256_text
from .synthetic_traces import TraceBuilder

#: R36's section order, typed from the requirement rather than imported from
#: ``SECTION_IDS``. Comparing ``SECTION_IDS`` with itself would pass for any
#: order, including the reverse.
R36_SECTION_ORDER: tuple[str, ...] = (
    "header",
    "findings",
    "timeline",
    "cost",
    "spans",
    "warnings",
)

#: R34's forbidden script identifiers, typed from the requirement. The list's
#: length is pinned as a literal so it cannot be narrowed to make a failing
#: grep pass.
R34_FORBIDDEN_APIS: tuple[str, ...] = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval",
    "Function",
    "setTimeout",
    "setInterval",
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "import(",
)

#: R35's element list, typed from the requirement.
R35_FORBIDDEN_ELEMENTS: tuple[str, ...] = (
    "link",
    "img",
    "iframe",
    "object",
    "embed",
    "form",
    "base",
)

#: Attribute names that carry a URL in HTML or SVG. R35 permits exactly one —
#: ``href``, and only as a same-document fragment — so the rest must be absent.
URL_BEARING_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "src",
        "srcset",
        "action",
        "formaction",
        "background",
        "poster",
        "data",
        "codebase",
        "cite",
        "longdesc",
        "manifest",
        "ping",
        "profile",
        "usemap",
        "xlink:href",
        "xmlns",
        "xmlns:xlink",
        "style",
    }
)

_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_FINDING_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}:[0-9a-f]{12}$")
_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
_CLASS = re.compile(r"^[a-z][a-z0-9-]*( k-[a-z_]+ s-[a-z]+)?$")
_VIEWBOX = re.compile(r"^0 0 (0|[1-9][0-9]*) (0|[1-9][0-9]*)$")


def _is_document_fragment(value: str) -> bool:
    if not value.startswith("#"):
        return False
    target = value[1:]
    return bool(_HEX16.match(target) or _FINDING_ID.match(target) or target in R36_SECTION_ORDER)


#: A tester-written justification for every attribute name the document may
#: hold: a predicate over the value, derived from R34's prose and from what the
#: requirement says makes each safe. An attribute name absent from this table is
#: a failure whatever its value, which is the arm that catches "added to the
#: document and to the coder's allowlist in the same commit".
ATTRIBUTE_SHAPES: dict[str, object] = {
    "lang": lambda value: value == "en",
    "charset": lambda value: value == "utf-8",
    "http-equiv": lambda value: value == "Content-Security-Policy",
    "content": lambda value: value == CSP_CONTENT,
    "class": lambda value: bool(_CLASS.match(value)),
    "id": lambda value: (
        bool(_HEX16.match(value) or _FINDING_ID.match(value)) or value in R36_SECTION_ORDER
    ),
    "href": _is_document_fragment,
    "type": lambda value: value == "button",
    "role": lambda value: value == "img",
    "aria-label": lambda value: value == "execution timeline",
    "data-severity": lambda value: value in {*SEVERITIES, ALL_SEVERITIES},
    "data-detector": lambda value: value in DETECTOR_SLUGS,
    "data-agent": lambda value: bool(_DECIMAL.match(value)),
    "data-seq": lambda value: bool(_DECIMAL.match(value)),
    "viewbox": lambda value: bool(_VIEWBOX.match(value)),
    "x": lambda value: bool(_DECIMAL.match(value)),
    "y": lambda value: bool(_DECIMAL.match(value)),
    "width": lambda value: bool(_DECIMAL.match(value)),
    "height": lambda value: bool(_DECIMAL.match(value)),
}


@pytest.fixture(scope="module")
def hostile(tmp_path_factory: pytest.TempPathFactory) -> Analysis:
    """The extended hostile trace, analysed once for this module."""
    directory = tmp_path_factory.mktemp("hostile-html")
    return analyze_paths(write_hostile_trace(directory))


@pytest.fixture(scope="module")
def clean() -> Analysis:
    """A small, ordinary trace — the arm that keeps "no payload" from being vacuous."""
    builder = TraceBuilder()
    call = builder.model_call(agent_id="alpha", start_ms=0, end_ms=1_000, text_preview="hello")
    builder.tool_call(agent_id="alpha", parent=call, start_ms=100, end_ms=400)
    builder.model_call(agent_id="beta", start_ms=500, end_ms=1_000)
    builder.warning("unknown_record_type", 1, "weird_type")
    return analyze_trace(builder.build())


class TestSectionOrderR36:
    """R36: the sections, in the order the requirement pins."""

    def test_r36_the_sections_appear_in_the_pinned_order(self, hostile: Analysis) -> None:
        """R36: header, findings, timeline, cost, spans, warnings.

        Red when: two sections are swapped, one is dropped, or one is rendered
        twice. Compared against a literal typed from R36, not against
        ``SECTION_IDS``.
        """
        assert section_ids_in_order(parse(hostile.html)) == list(R36_SECTION_ORDER)
        assert SECTION_IDS == R36_SECTION_ORDER

    def test_r36_the_title_element_comes_before_every_section(self, hostile: Analysis) -> None:
        """R36: "``<h1>`` title" is first.

        Red when: the ``<h1>`` moves below a section, which a reader would see
        and no id-order check would.
        """
        order = [tag for tag, _ in parse(hostile.html).element_order]
        assert order.index("h1") < order.index("section")

    def test_r43_no_narrative_section_exists_without_explain(self, hostile: Analysis) -> None:
        """R43: with ``--explain`` off, "no ``<section id='narrative'>`` element exists".

        Red when: increment 5 lands the anchor unconditionally. This is the test
        that must be *changed* then, which is the notification.
        """
        assert "narrative" not in section_ids_in_order(parse(hostile.html))
        assert 'id="narrative"' not in hostile.html
        assert "narrative" not in SECTION_IDS

    def test_r36_every_section_is_reachable_from_the_navigation(self, hostile: Analysis) -> None:
        """R36/R35: the nav's fragments name sections that exist.

        Red when: a section id is renamed on one side only — the document would
        carry an ``href`` to a fragment with no target, which looks fine in
        every parser-based check and is broken in a browser.
        """
        document = parse(hostile.html)
        ids = set(section_ids_in_order(document))
        nav = {
            value
            for element, name, value in document.attributes
            if element == "a" and name == "href" and value[1:] in R36_SECTION_ORDER
        }
        assert nav == {f"#{section}" for section in R36_SECTION_ORDER}
        assert ids == set(R36_SECTION_ORDER)


class TestFindingOrderR36:
    """R36: "grouped by severity descending, then detector slug, then ``finding_id``"."""

    def unsorted_findings(self, trace: object) -> tuple[Finding, ...]:
        """Two findings of one severity and one detector, in the wrong order.

        Built directly rather than taken from a detector run, and that is the
        point. R13 already returns findings sorted by
        ``(severity_rank, slug, finding_id)``, and Python's sort is stable, so
        **every** end-to-end input reaches the renderer already in
        ``finding_id`` order — which is why the mutant that dropped
        ``finding_id`` from ``_grouped``'s key survived a sweep whose only
        inputs came from the registry.

        The Modularity notes' rule is "no renderer may assume its caller
        sanitized", read one key over: no renderer may assume its caller
        sorted.
        """
        pair = tuple(
            build_finding(
                trace=trace,
                detector="repeated_tool_call",
                severity="warning",
                summary="two identical calls",
                span_seqs=(seq,),
                agent_ids=("root",),
                metrics={"occurrences": 2, "first_seq": seq},
            )
            for seq in (0, 1)
        )
        ordered = sorted(pair, key=lambda finding: finding.finding_id)
        return (ordered[1], ordered[0])

    def test_r36_the_renderer_orders_findings_it_was_handed_out_of_order(self) -> None:
        """R36: the ordering is the renderer's job, not the caller's.

        Red when: ``finding_id`` is dropped from ``_grouped``'s key — with
        stable sorting the document would then simply echo the order it was
        given.
        """
        from swarm_observer import __version__
        from swarm_observer.cost.compute import compute_costs
        from swarm_observer.cost.snapshot import SnapshotRateSource
        from swarm_observer.report.html import render_html
        from swarm_observer.report.sanitize import RenderOptions

        builder = TraceBuilder()
        builder.tool_call(start_ms=0, end_ms=1)
        builder.tool_call(start_ms=1, end_ms=2)
        trace = builder.build()
        findings = self.unsorted_findings(trace)
        assert findings[0].finding_id > findings[1].finding_id, "the input is not out of order"
        document = render_html(
            trace=trace,
            findings=findings,
            cost=compute_costs(trace, SnapshotRateSource(), waste_seqs={}),
            tool_version=__version__,
            options=RenderOptions(),
        )
        positions = [document.index(f'id="{finding.finding_id}"') for finding in findings]
        assert positions[0] > positions[1], "the renderer echoed its caller's order"

    def test_r36_severity_descending_beats_detector_and_id(self) -> None:
        """R36: the first key, and its sign.

        Red when: ``-SEVERITY_RANK`` loses its minus — ``info`` would render
        above ``critical`` and a reader would meet the least important finding
        first.
        """
        from swarm_observer import __version__
        from swarm_observer.cost.compute import compute_costs
        from swarm_observer.cost.snapshot import SnapshotRateSource
        from swarm_observer.report.html import render_html
        from swarm_observer.report.sanitize import RenderOptions

        builder = TraceBuilder()
        builder.tool_call(start_ms=0, end_ms=1)
        trace = builder.build()
        info = build_finding(
            trace=trace,
            detector="failed_tool_call",
            severity="info",
            summary="one failure",
            span_seqs=(0,),
            agent_ids=("root",),
            metrics={"failures": 1},
        )
        critical = build_finding(
            trace=trace,
            detector="unresolved_tool_call",
            severity="critical",
            summary="an unknown tool",
            span_seqs=(0,),
            agent_ids=("root",),
            metrics={"occurrences": 1},
        )
        document = render_html(
            trace=trace,
            findings=(info, critical),
            cost=compute_costs(trace, SnapshotRateSource(), waste_seqs={}),
            tool_version=__version__,
            options=RenderOptions(),
        )
        assert document.index(f'id="{critical.finding_id}"') < document.index(
            f'id="{info.finding_id}"'
        )


class TestSpansTableCapR36:
    """R36: "a table of all spans, capped at 5,000 rows with an '…and N more' line"."""

    def render_spans(self, count: int) -> tuple[str, list[str]]:
        builder = TraceBuilder()
        for _ in range(count):
            builder.user_message()
        html = analyze_trace(builder.build()).html
        return html, re.findall(r'<tr id="([0-9a-f]{16})"', html)

    def test_r36_the_cap_is_five_thousand(self) -> None:
        """R36: the number is in the requirement, so it is a literal here.

        Red when: ``SPANS_TABLE_CAP`` is changed to make a slow test fast.
        """
        assert SPANS_TABLE_CAP == 5000

    def test_r36_exactly_five_thousand_spans_render_with_no_truncation_line(self) -> None:
        """R36: at the cap, every span has a row and nothing is "more".

        Red when: the slice becomes ``[:CAP - 1]`` or the truncation line's
        condition becomes ``>= 0``.
        """
        html, rows = self.render_spans(SPANS_TABLE_CAP)
        assert len(rows) == SPANS_TABLE_CAP
        assert 'class="truncation"' not in html
        assert "more span(s)" not in html

    def test_r36_five_thousand_and_one_spans_render_five_thousand_and_one_more(self) -> None:
        """R36: one past the cap is the smallest input that can tell.

        Red when: the off-by-one goes either way — 4999 rows, or 5001 rows, or
        a truncation line naming 0.
        """
        html, rows = self.render_spans(SPANS_TABLE_CAP + 1)
        assert len(rows) == SPANS_TABLE_CAP
        assert "…and 1 more span(s)." in html

    def test_r36_a_finding_naming_a_span_past_the_cap_renders_the_seq_without_a_link(
        self,
    ) -> None:
        """R36/R34: an ``href`` to an anchor that does not exist would also "look fine".

        The row for seq 5000 is not rendered, so no ``id="…"`` exists for it. The
        finding names both 4999 (linked) and 5000 (bare), which is the pair that
        can tell a correct implementation from one that links everything.

        Red when: the ``seq in span_ids`` guard is dropped — the document gains
        a dangling fragment, and every parser-based allowlist check still passes
        because the *value* is a legal fragment.
        """
        builder = TraceBuilder()
        for _ in range(SPANS_TABLE_CAP + 1):
            builder.user_message()
        trace = builder.build()
        finding = build_finding(
            trace=trace,
            detector="repeated_tool_call",
            severity="warning",
            summary="two identical calls",
            span_seqs=(SPANS_TABLE_CAP - 1, SPANS_TABLE_CAP),
            agent_ids=("root",),
            metrics={"occurrences": 2},
        )
        linked = trace.spans[SPANS_TABLE_CAP - 1].span_id

        # Rendered with the finding attached, which is what the requirement is
        # about: no detector produces a finding over 5,001 user messages, so the
        # evidence link has to be driven directly.
        from swarm_observer import __version__
        from swarm_observer.cost.compute import compute_costs
        from swarm_observer.cost.snapshot import SnapshotRateSource
        from swarm_observer.report.html import render_html
        from swarm_observer.report.sanitize import RenderOptions

        cost = compute_costs(trace, SnapshotRateSource(), waste_seqs={})
        document = render_html(
            trace=trace,
            findings=(finding,),
            cost=cost,
            tool_version=__version__,
            options=RenderOptions(),
        )
        spans_line = re.search(r'<p class="metrics">spans: (.*?)</p>', document)
        assert spans_line is not None
        markup = spans_line.group(1)
        assert f'<a href="#{linked}">{SPANS_TABLE_CAP - 1}</a>' in markup
        assert f">{SPANS_TABLE_CAP}</a>" not in markup
        assert markup.endswith(str(SPANS_TABLE_CAP))
        assert f'id="{trace.spans[SPANS_TABLE_CAP].span_id}"' not in document

    def test_r37_the_untimed_note_is_capped_and_says_how_many_it_left_out(self) -> None:
        """R37/A-d12: the "no timing" note names a bounded number of seqs.

        R37 says untimed spans are "listed"; it does not say the list may be two
        million integers long. Red when: the cap is removed (the note becomes
        the bulk of the document) or the "…and N more" tail is dropped (the
        reader cannot tell a capped list from a complete one).
        """
        # The cap is pinned as a literal, not read from the constant: a test
        # that computed its expectations from ``UNTIMED_LIST_CAP`` passes for
        # every value of it, which is how the ``200 -> 199`` mutant survived
        # the first increment-4 sweep.
        assert UNTIMED_LIST_CAP == 200
        builder = TraceBuilder()
        for _ in range(205):
            builder.user_message()
        html = analyze_trace(builder.build()).html
        assert "205 span(s) have no start or no end" in html
        assert "…and 5 more" in html
        listed = re.search(r"are not drawn: ([^<]*)</p>", html)
        assert listed is not None
        assert listed.group(1).count(",") == 199, "the note lists exactly 200 seqs"


class TestTheOneScriptAndStyleR34:
    """R34: exactly one of each, both constant, both pinned by SHA-256."""

    def test_r34_the_document_holds_exactly_one_script_and_one_style(
        self, hostile: Analysis
    ) -> None:
        """R34: "exactly one ``<script>`` element and exactly one ``<style>`` element".

        Red when: a second of either is added — the case an escaping bug would
        produce by closing the first one early.
        """
        counts = parse(hostile.html).tag_counts
        assert counts.get("script") == 1
        assert counts.get("style") == 1

    def test_r34_the_parsed_script_hashes_to_the_pinned_constant(self, hostile: Analysis) -> None:
        """R34: the digest is taken from the **parsed element**, not from the module constant.

        A constant-against-constant assertion cannot detect a render-time
        interpolation, which is the only thing R34's pin exists to prevent. The
        canonicalization — strip exactly one leading newline, the one
        ``render_html`` adds by joining its lines — is in
        ``rendered.Document.script_body`` and is stated there.

        Red when: any byte is interpolated into the script at render time, or
        the constant is edited without its digest.
        """
        document = parse(hostile.html)
        assert sha256_text(document.script_body()) == SCRIPT_SHA256
        assert sha256_text(document.style_body()) == STYLE_SHA256

    def test_r34_the_pinned_constants_are_the_digests_of_the_module_constants(self) -> None:
        """R34: the second half — the pin names the script the module holds.

        On its own this proves little (see above); together with the parsed
        check it means "the document contains exactly this script".

        Red when: the script is edited without its digest, which is the friction
        the literal exists to create.
        """
        assert sha256_of(REPORT_SCRIPT) == SCRIPT_SHA256
        assert sha256_of(REPORT_STYLE) == STYLE_SHA256

    def test_r34_the_parsed_script_is_the_module_constant_byte_for_byte(
        self, hostile: Analysis
    ) -> None:
        """R34: the strongest available form, and it does not depend on SHA-256.

        Red when: a digest is recomputed to match a changed script — this
        assertion still names the mismatch.
        """
        document = parse(hostile.html)
        assert document.script_body() == REPORT_SCRIPT
        assert document.style_body() == REPORT_STYLE

    @pytest.mark.parametrize("identifier", R34_FORBIDDEN_APIS)
    def test_r34_the_script_uses_no_forbidden_api(self, identifier: str) -> None:
        """R34: the script may not name any of these.

        The grep is over ``REPORT_SCRIPT``, never over ``html.py``'s source,
        which necessarily contains every one of these strings in
        ``FORBIDDEN_SCRIPT_APIS``. Red when: the script gains one.
        """
        assert identifier not in REPORT_SCRIPT

    def test_r34_the_forbidden_list_is_the_requirements_and_cannot_be_narrowed(self) -> None:
        """R34: the list's length is a literal, so removing an entry is visible.

        Red when: an identifier is dropped from ``FORBIDDEN_SCRIPT_APIS`` to
        make a failing grep pass — the exact move the increment-2 review ruled
        against for mutation sets.
        """
        assert len(FORBIDDEN_SCRIPT_APIS) == 12
        assert set(FORBIDDEN_SCRIPT_APIS) == set(R34_FORBIDDEN_APIS)

    def test_r34_the_script_contains_no_scheme_relative_comment(self) -> None:
        """R35/A-d16: ``//`` anywhere in the file is a scheme-relative URL to a byte check.

        Red when: a ``//`` line comment is added to the script or the
        stylesheet, which is the natural thing for the next editor to do.
        """
        assert "//" not in REPORT_SCRIPT
        assert "//" not in REPORT_STYLE

    def test_r34_the_csp_meta_is_the_first_head_element_after_charset(
        self, hostile: Analysis
    ) -> None:
        """R34: the CSP meta's position is part of the requirement.

        A CSP meta after a resource-loading element in ``<head>`` is not applied
        to it, which is why the position is pinned rather than merely the
        presence. Red when: the ``<title>`` or the ``<style>`` moves above it.
        """
        head = head_element_order(parse(hostile.html))
        assert head[0] == ("meta", ("charset",))
        assert head[1] == ("meta", ("http-equiv", "content"))
        assert f'content="{CSP_CONTENT}"' in hostile.html

    def test_r34_the_csp_content_is_the_requirements_string(self) -> None:
        """R34: the policy is quoted verbatim in the requirement, so it is a literal here.

        Red when: a directive is added, removed or loosened — ``default-src
        'none'`` in particular is what makes a bypass of everything else
        harmless.
        """
        assert CSP_CONTENT == (
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "img-src data:; base-uri 'none'; form-action 'none'"
        )


class TestNoExternalRequestR35:
    """R35: nothing in the document can cause a fetch of any kind."""

    @pytest.mark.parametrize("element", R35_FORBIDDEN_ELEMENTS)
    def test_r35_no_resource_loading_element_exists(self, element: str, hostile: Analysis) -> None:
        """R35: the element list, one test per element.

        Asserted against the parsed tag counts rather than against the bytes,
        because R51 requires the *text* ``</script><script>`` to appear in the
        document and a byte check would read it as an element.

        Red when: any of them is added. ``<img>`` is the one a future "add a
        logo" change would reach for.
        """
        assert parse(hostile.html).tag_counts.get(element, 0) == 0

    def test_r35_every_href_is_a_same_document_fragment(self, hostile: Analysis) -> None:
        """R35: "The only permitted ``href`` values are same-document fragments".

        Red when: an ``href`` gains a scheme, or points at a fragment this
        document does not define.
        """
        document = parse(hostile.html)
        hrefs = [value for _, name, value in document.attributes if name == "href"]
        assert hrefs, "a document with no href would satisfy this vacuously"
        ids = {value for _, name, value in document.attributes if name == "id"}
        for href in hrefs:
            assert href.startswith("#"), href
            assert href[1:] in ids, f"dangling fragment {href}"

    def test_r35_no_url_bearing_attribute_exists_anywhere(self, hostile: Analysis) -> None:
        """R35/A-d6: including the SVG ``xmlns``, whose only legal value is an ``http:`` URL.

        Red when: any of these appears. ``style`` is in the list because an
        inline style is a stylesheet context and ``url(…)`` inside one is a
        fetch.
        """
        names = {name for _, name, _ in parse(hostile.html).attributes}
        assert not names & URL_BEARING_ATTRIBUTES, sorted(names & URL_BEARING_ATTRIBUTES)

    def test_r35_no_event_handler_attribute_exists_anywhere(self, hostile: Analysis) -> None:
        """R34/R51: no ``on*`` attribute, on any element.

        The hostile corpus contains ``onerror=alert(1)`` as text, so a check
        that looked at the bytes would fail; this looks at attribute names.

        Red when: an ``onclick`` is added to the filter buttons instead of the
        script's ``addEventListener``.
        """
        offenders = [
            (element, name)
            for element, name, _ in parse(hostile.html).attributes
            if name.startswith("on")
        ]
        assert not offenders, offenders

    def test_r35_the_bytes_carry_no_scheme_and_no_at_import(self, hostile: Analysis) -> None:
        """R35 at the byte level, which is where ``@import`` and ``//`` live.

        ``javascript:`` is deliberately **not** in this list: R51 requires it to
        appear in the report, in a text node, and R35 is a statement about URL
        *contexts* rather than about bytes — the coder's **S26**, which this
        module adopts in the parser direction. Every other scheme is
        checked as bytes because a stylesheet ``@import`` and a scheme-relative
        ``//`` are invisible to ``html.parser``.

        Red when: a stylesheet gains an ``@import`` or a ``url(…)``, or any
        absolute URL is written anywhere.
        """
        for forbidden in ("http:", "https:", "file:", "@import", "url(", "//"):
            assert forbidden not in hostile.html, forbidden

    def test_r35_no_html_comment_survives_into_the_document(self, hostile: Analysis) -> None:
        """R34: trace text may not reach a comment, and the renderer writes none.

        The hostile corpus contains ``<!--`` as text. Red when: the renderer
        starts emitting comments, at which point "the payload is in a text node"
        stops being provable by counting comments.
        """
        assert parse(hostile.html).comments == []

    def test_r35_the_clean_arm_is_also_clean(self, clean: Analysis) -> None:
        """R35: the same properties on an ordinary trace.

        The non-vacuous arm in reverse: a renderer that emitted nothing at all
        would pass every check above, so this asserts an ordinary document is
        produced and is equally free of URLs.
        """
        document = parse(clean.html)
        assert document.tag_counts.get("table", 0) >= 3
        assert document.tag_counts.get("svg") == 1
        assert not {name for _, name, _ in document.attributes} & URL_BEARING_ATTRIBUTES
        # `http-equiv` is R34's own mandated CSP meta, so the byte scan is for
        # a scheme rather than for the four letters.
        for forbidden in ("http:", "https:", "file:", "@import", "url(", "//"):
            assert forbidden not in clean.html, forbidden


class TestAttributesIndependentlyClassified:
    """R34: every attribute in the document, justified from the requirement.

    This is the check the coder asked for by name: an enumeration of what the
    document *actually contains*, asking whether each entry belongs, rather than
    a membership test against ``attribute_allowlist``.
    """

    @pytest.mark.parametrize("fixture_name", ["hostile", "clean"])
    def test_r34_every_attribute_name_is_one_the_requirement_justifies(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """R34: an attribute name not in :data:`ATTRIBUTE_SHAPES` is a failure.

        Red when: the renderer gains an attribute — *including* one added to
        ``attribute_allowlist`` in the same commit, which is the hole the coder
        named as most likely.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        names = {name for _, name, _ in parse(analysis.html).attributes}
        assert names, "a document with no attributes would pass vacuously"
        unjustified = sorted(names - set(ATTRIBUTE_SHAPES))
        assert not unjustified, f"undeclared attribute names: {unjustified}"

    @pytest.mark.parametrize("fixture_name", ["hostile", "clean"])
    def test_r34_every_attribute_value_matches_its_justified_shape(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """R34: the shapes are written from R34's prose, not read from the coder's allowlist.

        Red when: a trace-derived string reaches any attribute. The shapes are
        deliberately tighter than "a string": ``data-seq`` must be decimal,
        ``id`` must be hex or a finding id, ``class`` may hold only lowercase
        tokens.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        offenders = [
            (element, name, value)
            for element, name, value in parse(analysis.html).attributes
            if not ATTRIBUTE_SHAPES[name](value)  # type: ignore[operator]
        ]
        assert not offenders, offenders

    def test_r34_no_attribute_value_contains_any_character_escape_html_escapes(
        self, hostile: Analysis
    ) -> None:
        """R34: the property behind the shapes, stated without reference to them.

        If no attribute value contains ``<``, ``>``, ``"``, ``'``, backtick or a
        space-separated payload, then no markup payload is in an attribute
        whatever the shapes say. Red when: any of them appears — which is what a
        trace-derived value in an attribute would look like.
        """
        for element, name, value in parse(hostile.html).attributes:
            if name in {"content", "class", "aria-label", "viewbox"}:
                continue  # constants and generated class/geometry tokens with spaces
            for char in "<>\"'`":
                assert char not in value, (element, name, value)

    def test_r34_class_tokens_are_all_declared_constants(self, hostile: Analysis) -> None:
        """R34: every class token comes from ``CSS_CLASSES`` or ``RECT_CLASSES``.

        Red when: a class is composed from a variable — the one thing that would
        turn ``class`` from a set of literals into a grammar.
        """
        declared = set(CSS_CLASSES) | set(RECT_CLASSES)
        for element, name, value in parse(hostile.html).attributes:
            if name == "class":
                assert value in declared, (element, value)

    def test_r34_the_document_uses_a_meaningful_share_of_the_declared_classes(
        self, hostile: Analysis
    ) -> None:
        """R34: ``CSS_CLASSES`` is not a bag of unused names that make the check loose.

        A constant listing a hundred classes the renderer never writes would
        widen the allowlist for free. Red when: classes are added to the
        constant without being rendered.
        """
        used = {value for _, name, value in parse(hostile.html).attributes if name == "class"}
        single = {value for value in used if value in CSS_CLASSES}
        assert len(single) >= len(CSS_CLASSES) * 2 // 3, sorted(set(CSS_CLASSES) - single)


class TestAttributeAllowlistR34:
    """R34: the coder's generated allowlist, and what it is and is not evidence of."""

    @pytest.mark.parametrize("fixture_name", ["hostile", "clean"])
    def test_r34_every_observed_attribute_is_in_the_generated_allowlist(
        self, fixture_name: str, request: pytest.FixtureRequest
    ) -> None:
        """R34: "A test parses the rendered file and asserts every observed attribute value
        matches the allowlist".

        The allowlist is built from the same ``Trace``, findings and ``Timeline``
        the document was built from — never harvested from the document, which
        would be satisfied by any document.

        Red when: a value appears that the inputs cannot justify.
        """
        analysis: Analysis = request.getfixturevalue(fixture_name)
        allowlist = analysis.allowlist()
        offenders = [
            (element, name, value)
            for element, name, value in parse(analysis.html).attributes
            if name not in allowlist or value not in allowlist[name]
        ]
        assert not offenders, offenders

    def test_r34_the_allowlist_is_not_vacuous(self, hostile: Analysis) -> None:
        """R34: an empty or wildcard allowlist would make the check above meaningless.

        Red when: an entry becomes an unbounded set — the failure mode a
        "just allow any integer" change would produce.
        """
        allowlist = hostile.allowlist()
        assert len(allowlist) >= 8
        assert sum(len(values) for values in allowlist.values()) > 50
        for name, values in allowlist.items():
            assert values, name
            assert all(isinstance(value, str) for value in values), name

    def test_r34_the_allowlist_rejects_a_trace_string_in_a_data_attribute(
        self, hostile: Analysis
    ) -> None:
        """R34: the allowlist's failure branch, exercised.

        A membership check that has only ever seen conforming values has not
        been shown to be able to fail. The R50 canary
        ``attribute_allowlist_injection`` does this against the *rendered*
        document; this is the unit form.

        Red when: an entry is widened to accept an arbitrary string.
        """
        allowlist = hostile.allowlist()
        payload = "</script><script>alert(1)</script>"
        for name in ("data-detector", "data-seq", "data-agent", "id", "href", "class"):
            assert payload not in allowlist[name], name

    def test_r34_the_allowlist_names_no_attribute_the_document_never_uses(
        self, hostile: Analysis
    ) -> None:
        """R34: an allowlist entry with no corresponding attribute is dead width.

        The coder named "an attribute I add in a later edit and add to the
        allowlist in the same commit" as the most likely hole. An entry for an
        attribute the document does not write is that hole, one commit early.

        Red when: an unused attribute name is added to ``attribute_allowlist``.
        """
        observed = {name for _, name, _ in parse(hostile.html).attributes}
        unused = sorted(set(hostile.allowlist()) - observed)
        assert not unused, f"allowlist entries no rendered attribute uses: {unused}"


class TestGoldenDocuments:
    """R47/R36: two golden documents, each paired with the structural checks above.

    What a golden pins that nothing else here does: the document's **ordering
    and whitespace** — the bytes R47 promises are identical across interpreters,
    time zones, hash seeds and working directories. What it does *not* tell you
    is which property broke, which is why every security claim in this module is
    also an assertion of its own.
    """

    def test_r47_the_hostile_report_matches_its_golden(self, hostile: Analysis) -> None:
        """R47/R36: the exact bytes of a report over a payload-bearing trace.

        Red when: any byte of the document changes — a section reorders, a
        column is added, an escape changes, the redaction table moves, the
        tool version is bumped. Every one of those is a deliberate change, and
        the structural tests above say which kind it was.
        """
        assert golden_path("report_hostile_extended.html").is_file(), (
            "run tests/golden/regenerate.py"
        )
        assert_matches_golden("report_hostile_extended.html", hostile.html)

    def test_r38_the_no_previews_report_matches_its_golden(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """R38/A10: the same trace under ``--no-previews``, pinned separately.

        Two goldens rather than one because the two documents differ by more
        than blanked text (**S27**): ``--no-previews`` blanks at ingest, so
        R22's ``unknown_tool`` reason cannot be decided and a ``critical``
        finding disappears. A test that diffed the two and attributed the
        difference to blanking would be wrong.

        Red when: the flag stops blanking a field, or starts blanking one it
        should keep as a join key.
        """
        directory = tmp_path_factory.mktemp("hostile-html-nopreviews")
        analysis = analyze_paths(write_hostile_trace(directory), previews=False)
        assert_matches_golden("report_hostile_extended_no_previews.html", analysis.html)

    def test_r47_the_golden_is_a_function_of_the_inputs_and_not_of_the_directory(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """R47: two different absolute paths to the same content render the same bytes.

        This is what makes the golden legitimate: if the document embedded a
        path, the golden would be a function of the machine that produced it.

        Red when: an absolute path, a CWD or a temp directory name reaches the
        document.
        """
        first = analyze_paths(write_hostile_trace(tmp_path_factory.mktemp("one")))
        second = analyze_paths(write_hostile_trace(tmp_path_factory.mktemp("two")))
        assert first.html == second.html

    def test_r47_the_goldens_differ_from_each_other(self) -> None:
        """R47: the two goldens are not the same file.

        A golden pair that happened to be identical would make the
        ``--no-previews`` golden prove nothing. Red when: ``--no-previews``
        becomes a no-op.
        """
        default = golden_path("report_hostile_extended.html").read_text(encoding="utf-8")
        blanked = golden_path("report_hostile_extended_no_previews.html").read_text(
            encoding="utf-8"
        )
        assert default != blanked
        assert len(blanked) < len(default)

    def test_r47_every_timestamp_in_a_golden_is_one_the_trace_carries(
        self, hostile: Analysis
    ) -> None:
        """R47: "never the current time" and "no absolute path" read out of the bytes.

        Every ``YYYY-MM-DDTHH:MM:SS.mmmZ`` token in the document is matched
        against the set of timestamps the ``Trace`` itself holds, so a
        ``datetime.now()`` anywhere in the renderer produces a token that is in
        the document and not in the trace.

        Red when: a clock, a CWD, a home directory or a temp directory name
        reaches the renderer. A golden built from such a renderer would still
        match itself on the machine that wrote it, which is why this reads the
        content rather than comparing files.
        """
        from swarm_observer.report.json_out import optional_timestamp

        trace_times = {
            rendered
            for span in hostile.trace.spans
            for rendered in (optional_timestamp(span.start), optional_timestamp(span.end))
            if rendered is not None
        }
        pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
        for name in ("report_hostile_extended.html", "report_hostile_extended_no_previews.html"):
            text = golden_path(name).read_text(encoding="utf-8")
            assert "/home/" not in text and "/tmp/" not in text and "/Users/" not in text
            found = set(pattern.findall(text))
            assert found, "a document with no timestamp would pass this vacuously"
            assert found <= trace_times, sorted(found - trace_times)


def test_r36_the_renderer_takes_the_same_arguments_as_the_json_renderer() -> None:
    """R36: one data shape, two renderers — so a probe can drive both from one run.

    Red when: the two signatures diverge, which would make every "the same
    trace in both documents" assertion in this suite compare two different runs.
    """
    import inspect

    from swarm_observer.report.html import render_html
    from swarm_observer.report.json_out import render_json

    assert set(inspect.signature(render_html).parameters) == set(
        inspect.signature(render_json).parameters
    )


def test_r36_the_golden_regenerator_exists_and_is_not_a_test() -> None:
    """R47: regenerating a golden is a deliberate act with a script, not a test side effect.

    Red when: someone adds a "write the golden if it is missing" branch to a
    test, which is how a golden becomes a check that cannot fail.
    """
    script = Path(golden_path("regenerate.py"))
    assert script.is_file()
    source = script.read_text(encoding="utf-8")
    assert "def test" not in source


class TestWaveFiveGapsR4R34R37:
    """Three wave-5 mutation survivors, closed by the increment-4 review.

    Each one is a property a requirement states and nothing asserted. None is a
    defect in the shipped renderer; all three are places where a one-character
    change produced a plausible wrong document and the whole suite stayed green,
    which is the only evidence that matters about a check.
    """

    def test_r4_the_header_note_counts_unknown_record_types_and_not_other_warnings(
        self,
    ) -> None:
        """R4: "a non-zero ``unknown_record_type`` count is surfaced in the HTML header".

        Flipping the filter from ``==`` to ``!=`` survived the whole suite: no
        trace anywhere carried an ``unknown_record_type`` warning *beside* a
        warning of another code with a different count, so "the count of unknown
        record types" and "the count of some other warnings" were the same
        number in every document the suite renders. A collection of one, one
        level up from the test — the shape the increment-3 review found in
        ``by_model``.

        The three counts here are deliberately distinct and deliberately do not
        sum to each other, so the note can name only the right one.

        Red when: the filter changes code, drops, or starts summing warnings.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="alpha", start_ms=0, end_ms=1_000)
        builder.warning("unknown_record_type", 2, "sidechain")
        builder.warning("known_ignored_key", 7, "rendered")
        builder.warning("unknown_content_block", 13, "widget")
        html = analyze_trace(builder.build()).html
        notes = [
            text
            for text in parse(html).text_nodes
            if "record(s) had a type this adapter does not know" in text
        ]
        assert len(notes) == 1, "R4's header surfacing is missing or duplicated"
        assert notes[0].startswith("2 record(s)"), notes[0]
        for wrong in ("7 record(s)", "13 record(s)", "22 record(s)", "20 record(s)"):
            assert wrong not in notes[0]

    def test_r4_the_header_note_is_absent_when_no_record_type_was_unknown(self) -> None:
        """R4: "non-zero" — the arm that stops the note being unconditional.

        Without this, the test above is satisfied by a renderer that always
        writes the note, which would make the count the only thing asserted.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="alpha", start_ms=0, end_ms=1_000)
        builder.warning("known_ignored_key", 7, "rendered")
        html = analyze_trace(builder.build()).html
        assert "had a type this adapter does not know" not in html

    def test_r37_the_no_timing_note_has_no_tail_when_nothing_was_truncated(self) -> None:
        """R37/S30: "…and N more" appears only when the list really was cut.

        ``more > 0`` → ``more >= 0`` survived the whole suite, which means the
        note would have read "…and 0 more" on every trace with any untimed span
        and nothing would have noticed. The cap itself is pinned
        (``UNTIMED_LIST_CAP``); its *tail* was not.

        Red when: the tail becomes unconditional, or stops appearing when the
        list is genuinely truncated — both arms are asserted, because a tail
        that never appears passes the first assertion alone.
        """
        below = TraceBuilder()
        below.model_call(agent_id="alpha", start_ms=0, end_ms=1_000)
        for _ in range(3):
            below.model_call(agent_id="alpha")
        html = analyze_trace(below.build()).html
        assert "span(s) have no start or no end" in html
        assert "…and" not in html, "an untruncated no-timing note carries a tail"

        above = TraceBuilder()
        above.model_call(agent_id="alpha", start_ms=0, end_ms=1_000)
        for _ in range(UNTIMED_LIST_CAP + 5):
            above.model_call(agent_id="alpha")
        html = analyze_trace(above.build()).html
        assert "…and 5 more" in html

    def test_r34_the_generated_allowlist_holds_nothing_the_inputs_do_not_justify(
        self, hostile: Analysis
    ) -> None:
        """R34: the allowlist is a specification, so it is pinned exactly, not loosely.

        Every other R34 check asks whether the document's attributes are *in*
        the allowlist. That direction cannot see a widened allowlist: adding
        ``"anything"`` to the ``data-detector`` entry survived the whole suite,
        and a value set that can be widened to admit whatever failed is the same
        shape as ``LEAKING_PATHS`` before it got ``LEAK_LEDGER_SIZE``, and as a
        self-chosen mutation set.

        So this recomputes every entry from the closed enums and from the same
        three inputs ``attribute_allowlist`` was given, in this module, and
        asserts **equality**. A value the inputs do not justify is a failure
        even if the renderer never writes it, because an unused permission is a
        hole one commit before it is used.

        Red when: any entry gains a value that is not a document constant, a
        member of a closed enum, an id or fragment built from the trace, or an
        integer drawn from the computed layout.
        """
        allowlist = hostile.allowlist()
        trace, timeline = hostile.trace, hostile.timeline
        span_ids = {span.span_id for span in trace.spans}
        finding_ids = {finding.finding_id for finding in hostile.findings}
        expected = {
            "lang": {"en"},
            "charset": {"utf-8"},
            "http-equiv": {"Content-Security-Policy"},
            "content": {CSP_CONTENT},
            "type": {"button"},
            "role": {"img"},
            "aria-label": {"execution timeline"},
            "class": set(CSS_CLASSES) | set(RECT_CLASSES),
            "data-severity": set(SEVERITIES) | {ALL_SEVERITIES},
            "data-detector": set(DETECTOR_SLUGS),
            "id": set(SECTION_IDS) | span_ids | finding_ids,
            "href": {f"#{value}" for value in set(SECTION_IDS) | span_ids | finding_ids},
            "data-agent": {str(agent.agent_index) for agent in trace.agents},
            "data-seq": {str(span.seq) for span in trace.spans},
            "viewbox": {f"0 0 {timeline.width} {max(timeline.height, 1)}"},
            "x": {"0"} | {str(rect.x) for rect in timeline.rects},
            "y": {str(lane.lane * LANE_HEIGHT) for lane in timeline.lanes}
            | {str(rect.y) for rect in timeline.rects},
            "width": {str(timeline.width)} | {str(rect.width) for rect in timeline.rects},
            "height": {str(LANE_HEIGHT)} | {str(rect.height) for rect in timeline.rects},
        }
        assert set(allowlist) == set(expected)
        for name, values in expected.items():
            assert set(allowlist[name]) == values, name
