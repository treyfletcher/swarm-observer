"""R34's one script, executed (BUG-19).

Every other check in this repository reads the *static* document. That is the
right posture for R34's security half — trace bytes must not reach an executable
or attribute context, and a parser settles it — but it is structurally unable to
say anything about whether the script *works*. The SHA-256 pin proves the bytes
have not changed; it cannot prove the bytes do what the section control claims
they do, and for five increments they did not: the collapse handler toggled
``collapsed`` onto ``div.section-title`` (the button's ``parentNode``) instead
of the ``<section>``, so ``.collapsed > *:not(.section-title)`` hid that div's
own children — the ``<h2>`` and the control itself — and left every row of
content on the page.

What makes this module worth its weight is that the assertions below are made
against ``getComputedStyle`` in a real engine, with the real stylesheet applied.
The defect is a *mismatch between the script's idea of the collapse root and the
stylesheet's*, so neither half alone can catch it: a DOM-only shim would have to
be told which element the CSS targets, and a CSS-only check would have to be
told which element the script marks. Only cascading the one stylesheet over the
DOM the one script produced answers the question, which is why this tree exists
and why its CI job installs a browser.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

#: R36's fixed section order — retyped rather than imported, so a change to the
#: renderer's constant does not quietly change what this module checks.
SECTION_IDS: tuple[str, ...] = ("header", "findings", "timeline", "cost", "spans", "warnings")

#: Reads one section's rendered state: what the *engine* computed, not what the
#: markup says. ``display`` is the property ``.collapsed > *:not(.section-title)``
#: sets, so it is the property asked about.
_PROBE = """
(id) => {
  const section = document.getElementById(id);
  const title = section.querySelector(".section-title");
  const heading = title.querySelector("h2");
  const button = title.querySelector("button.toggle");
  const content = Array.from(section.children).filter((node) => node !== title);
  const shown = (node) => getComputedStyle(node).display !== "none";
  return {
    heading_shown: shown(heading),
    button_shown: shown(button),
    heading_text: heading.textContent,
    label: button.textContent,
    expanded: button.getAttribute("aria-expanded"),
    content_shown: content.map(shown),
    content_count: content.length,
    section_has_collapsed: section.classList.contains("collapsed"),
    title_has_collapsed: title.classList.contains("collapsed"),
  };
}
"""


def probe(page: Page, section_id: str) -> dict[str, Any]:
    """One section's computed state."""
    result: dict[str, Any] = page.evaluate(_PROBE, section_id)
    return result


def toggle(page: Page, section_id: str) -> None:
    """Click that section's collapse control the way a reader would."""
    page.click(f"#{section_id} .section-title button.toggle")


class TestSectionCollapseR34:
    """R34: "the one script performs only DOM class toggling ... over nodes already present".

    It may only do that — but it must also *do* it. These are the assertions
    the static suite could not make.
    """

    def test_every_section_opens_expanded_with_content_on_the_page(self, page: Page) -> None:
        """The baseline the collapse assertions are differences from.

        Red when: a section renders with no content at all, which would make
        "collapsing hid the content" vacuously true.
        """
        for section_id in SECTION_IDS:
            state = probe(page, section_id)
            assert state["content_count"] >= 1, section_id
            assert all(state["content_shown"]), (section_id, state)
            assert state["heading_shown"] and state["button_shown"], section_id
            assert state["heading_text"].strip(), section_id
            assert not state["section_has_collapsed"], section_id
            assert not state["title_has_collapsed"], section_id

    @pytest.mark.parametrize("section_id", SECTION_IDS)
    def test_collapsing_hides_the_content_and_keeps_the_heading_and_control(
        self, page: Page, section_id: str
    ) -> None:
        """BUG-19, stated as the behaviour a reader expects.

        Red when: ``collapsed`` lands anywhere but the ``<section>``. Against the
        handler shipped through increment 5 this fails on the first assertion —
        the heading computes to ``display: none`` and every content row stays
        ``display: grid``/``block``.
        """
        before = probe(page, section_id)
        toggle(page, section_id)
        after = probe(page, section_id)

        assert after["heading_shown"], (
            f"{section_id}: collapsing removed the heading — the section is now unlabelled"
        )
        assert after["button_shown"], (
            f"{section_id}: collapsing removed its own control — the state is unrecoverable "
            "without a page reload"
        )
        assert after["heading_text"] == before["heading_text"], section_id
        assert not any(after["content_shown"]), (
            f"{section_id}: collapsing hid nothing — {sum(after['content_shown'])} of "
            f"{after['content_count']} content children are still displayed"
        )

    @pytest.mark.parametrize("section_id", SECTION_IDS)
    def test_collapsing_is_reversible(self, page: Page, section_id: str) -> None:
        """A control that cannot be undone is a one-way door, not a toggle.

        Red when: the second click does not restore the content — including the
        increment-5 behaviour, where the control the second click needs has
        itself been hidden.
        """
        before = probe(page, section_id)
        toggle(page, section_id)
        toggle(page, section_id)
        after = probe(page, section_id)
        assert after["content_shown"] == before["content_shown"], section_id
        assert all(after["content_shown"]), section_id
        assert after["heading_shown"] and after["button_shown"], section_id

    def test_the_collapsed_class_lands_on_the_section_not_on_the_title_row(
        self, page: Page
    ) -> None:
        """The root cause, named directly.

        The stylesheet's rule is ``.collapsed > *:not(.section-title)``. Its
        ``:not`` clause only means anything if the element carrying ``collapsed``
        is the one that *has* a ``.section-title`` child. Red when: the handler
        marks the title row, which is what ``parentNode`` returned.
        """
        for section_id in SECTION_IDS:
            toggle(page, section_id)
            state = probe(page, section_id)
            assert state["section_has_collapsed"], section_id
            assert not state["title_has_collapsed"], section_id

    def test_collapsing_everything_removes_the_body_of_the_report(self, page: Page) -> None:
        """The symptom a reader would actually notice, measured.

        With the increment-5 handler the page shrank by one heading row per
        click — about 26px — while the content stayed. Red when: collapsing all
        six sections does not remove the bulk of the document's height.
        """
        before = page.evaluate("() => document.documentElement.scrollHeight")
        for section_id in SECTION_IDS:
            toggle(page, section_id)
        after = page.evaluate("() => document.documentElement.scrollHeight")
        assert after < before / 2, (before, after)


class TestControlStateIsAnnouncedR34:
    """The increment-5 browser review's accessibility finding, pinned.

    Ten buttons — six collapse controls and four severity filters — carried no
    ``aria-expanded``, ``aria-pressed`` or ``aria-label``, so a screen reader
    was told a button existed and nothing about what it did or what state it was
    in. The visible label is now part of the answer too: a control that always
    reads ``hide`` is wrong half the time.
    """

    def test_the_collapse_control_label_alternates(self, page: Page) -> None:
        """Red when: the label is a constant, as it was through increment 5."""
        for section_id in SECTION_IDS:
            assert probe(page, section_id)["label"] == "hide", section_id
            toggle(page, section_id)
            assert probe(page, section_id)["label"] == "show", section_id
            toggle(page, section_id)
            assert probe(page, section_id)["label"] == "hide", section_id

    def test_aria_expanded_tracks_the_collapsed_state(self, page: Page) -> None:
        """Red when: the attribute is absent, or stops being updated by the handler."""
        for section_id in SECTION_IDS:
            assert probe(page, section_id)["expanded"] == "true", section_id
            toggle(page, section_id)
            assert probe(page, section_id)["expanded"] == "false", section_id
            toggle(page, section_id)
            assert probe(page, section_id)["expanded"] == "true", section_id

    def test_every_button_in_the_document_exposes_its_state_or_its_name(self, page: Page) -> None:
        """The finding as counted in the browser: ten buttons, none of them annotated."""
        buttons: list[dict[str, Any]] = page.evaluate(
            """() => Array.from(document.querySelectorAll("button")).map((node) => ({
                text: node.textContent,
                pressed: node.getAttribute("aria-pressed"),
                expanded: node.getAttribute("aria-expanded"),
                label: node.getAttribute("aria-label"),
            }))"""
        )
        assert len(buttons) == 10, buttons
        bare = [
            button
            for button in buttons
            if button["pressed"] is None and button["expanded"] is None and button["label"] is None
        ]
        assert not bare, bare


class TestR34HoldsAfterTheScriptHasRun:
    """R34 asserted on the *live* DOM, not only on the bytes on disk.

    Every static check in ``tests/`` parses the document as written. The script
    now writes two attributes and one text node of its own, so "no trace byte
    reaches an attribute" needs asserting after it has run as well as before —
    otherwise the pins cover a document nobody looks at once the page is
    interactive. Nothing here is a substitute for the static checks; it is the
    arm they cannot have.
    """

    #: Every attribute name the rendered document may hold, retyped from R34's
    #: prose. ``tests/test_report_html_r34_r35_r36.py`` holds the same list with
    #: a shape per name; this copy exists so a browser-side leak is caught here
    #: even if the two ever drift.
    DECLARED: frozenset[str] = frozenset(
        {
            "lang",
            "charset",
            "http-equiv",
            "content",
            "class",
            "id",
            "href",
            "type",
            "role",
            "aria-label",
            "aria-expanded",
            "aria-pressed",
            "data-severity",
            "data-detector",
            "data-agent",
            "data-seq",
            # ``html.parser`` lowercases attribute names, so the static suite
            # sees ``viewbox``; a live DOM preserves the SVG spelling. Both are
            # the same attribute and both are declared, here and nowhere else.
            "viewbox",
            "viewBox",
            "x",
            "y",
            "width",
            "height",
        }
    )

    def _exercise(self, page: Page) -> None:
        for severity in ("info", "warning", "critical", "all"):
            page.click(f'.filter[data-severity="{severity}"]')
        for section_id in SECTION_IDS:
            toggle(page, section_id)
            toggle(page, section_id)

    def test_no_interaction_introduces_an_attribute_r34_does_not_declare(self, page: Page) -> None:
        """Red when: the script writes an attribute name nobody justified."""
        self._exercise(page)
        names: list[str] = page.evaluate(
            """() => {
              const out = [];
              document.querySelectorAll("*").forEach((node) => {
                for (const attribute of node.attributes) { out.push(attribute.name); }
              });
              return out;
            }"""
        )
        assert names, "a document with no attributes would pass vacuously"
        undeclared = sorted(set(names) - self.DECLARED)
        assert not undeclared, undeclared

    def test_no_interaction_puts_markup_characters_into_an_attribute(self, page: Page) -> None:
        """R34's property, stated without reference to the shapes, after the script ran."""
        self._exercise(page)
        offenders: list[list[str]] = page.evaluate(
            """() => {
              const out = [];
              document.querySelectorAll("*").forEach((node) => {
                for (const attribute of node.attributes) {
                  if (["content", "class", "aria-label", "viewBox"].includes(attribute.name)) {
                    continue;
                  }
                  if (/[<>"'`]/.test(attribute.value)) {
                    out.push([node.tagName, attribute.name, attribute.value]);
                  }
                }
              });
              return out;
            }"""
        )
        assert not offenders, offenders

    def test_the_script_adds_no_element_and_no_second_script(self, page: Page) -> None:
        """R34: "exactly one ``<script>`` element". Still one after every control is used."""
        before = page.evaluate("() => document.querySelectorAll('*').length")
        self._exercise(page)
        after = page.evaluate(
            """() => ({
                total: document.querySelectorAll("*").length,
                scripts: document.querySelectorAll("script").length,
                styles: document.querySelectorAll("style").length,
                iframes: document.querySelectorAll("iframe").length,
                images: document.querySelectorAll("img").length,
            })"""
        )
        assert after == {
            "total": before,
            "scripts": 1,
            "styles": 1,
            "iframes": 0,
            "images": 0,
        }


class TestSeverityFilterR34:
    """The filter half of the one script — no regression, and its state exposed.

    The increment-5 browser session found the filtering itself correct and the
    ``active`` class moving rather than accumulating. Those properties are
    pinned here so the ``aria-pressed`` addition cannot quietly break them.
    """

    def _states(self, page: Page) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = page.evaluate(
            """() => Array.from(document.querySelectorAll(".filter")).map((node) => ({
                severity: node.getAttribute("data-severity"),
                active: node.classList.contains("active"),
                pressed: node.getAttribute("aria-pressed"),
            }))"""
        )
        return result

    def test_filtering_shows_only_the_wanted_severity(self, page: Page) -> None:
        """Red when: the filter stops hiding, or hides the wrong findings."""
        for severity in ("info", "warning", "critical", "all"):
            page.click(f'.filter[data-severity="{severity}"]')
            shown = page.evaluate(
                """() => Array.from(document.querySelectorAll(".finding"))
                    .filter((node) => getComputedStyle(node).display !== "none")
                    .map((node) => node.getAttribute("data-severity"))"""
            )
            if severity == "all":
                assert len(shown) >= 1
            else:
                assert set(shown) <= {severity}, (severity, shown)

    def test_exactly_one_filter_is_active_and_pressed_at_a_time(self, page: Page) -> None:
        """Red when: ``active`` accumulates, or ``aria-pressed`` stops tracking it."""
        for severity in ("all", "info", "warning", "critical", "all"):
            page.click(f'.filter[data-severity="{severity}"]')
            states = self._states(page)
            assert [state["severity"] for state in states if state["active"]] == [severity]
            assert [state["severity"] for state in states if state["pressed"] == "true"] == [
                severity
            ]
            assert all(state["pressed"] in {"true", "false"} for state in states), states
