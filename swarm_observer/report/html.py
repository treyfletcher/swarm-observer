"""``report.html`` — the self-contained HTML report (R34, R35, R36).

One file, no build step, no server, no external request of any kind. A human
opens it in a browser with the local-file origin and forwards it to a colleague,
which is why this module is where the project's threat model lands hardest: the
trace was produced by someone else's agent, that agent may have processed a
hostile web page or repository, and every byte it saw is now being rendered into
a document somebody will open.

The safety claim is one sentence, and every rule below exists to make it true:
**no trace-derived byte leaves a text node.**

How that is achieved, in the order it matters:

1. **One escaping boundary, applied to everything.** Every string this module
   writes — including the ones R16 guarantees this package authored — goes
   through :func:`~swarm_observer.report.escape.escape_html`, after
   :mod:`~swarm_observer.report.sanitize`'s redaction. Escaping only the strings
   known to be untrusted would make the property a fact about this module's
   knowledge of its inputs; escaping everything makes it a fact about the code.
   Numbers reach the document only as ``str`` of an ``int`` or as a
   ``Decimal``'s own formatting (R29), never as a float.

2. **Nothing is interpolated into an executable or attribute context.** The
   document holds exactly one ``<script>`` and one ``<style>``, both module
   constants with pinned SHA-256s (:data:`SCRIPT_SHA256`, :data:`STYLE_SHA256`).
   They are written into the document by concatenation, never by formatting, so
   interpolating into them is not a thing this module can do by accident. Every
   attribute value in the document is drawn from :func:`attribute_allowlist`,
   which is generated from the **inputs** — the trace, the findings, the
   timeline model — rather than from the rendered text, so a value that appears
   in the output and not in the allowlist is a leak by definition.

3. **No URL of any scheme** (R35). No ``<link>``, ``<img>``, ``<iframe>``,
   ``<object>``, ``<embed>``, ``<form>``, ``<base>``, no ``@import``, no font or
   icon reference, and not even the SVG ``xmlns`` — its only legal value is an
   ``http:`` URL and an inline ``<svg>`` does not need it. The only ``href``
   values are same-document fragments.

4. **A CSP meta as the first element of ``<head>`` after ``<meta charset>``**
   (R34), so a bypass of 1-3 still has nothing to fetch.

**The two fields whose safety rests on a constrained alphabet, named
explicitly** — because "it looked like an identifier" is how this project's
worst bug class reached production three increments running:

* ``Span.span_id`` and ``Span.parent_span_id`` are 16 lowercase hex characters
  (R5, pattern-validated on the model as ``HEX_ID_PATTERN``). They are used as
  ``id`` and fragment ``href`` values.
* ``Finding.finding_id`` is ``<detector-slug>:<12 hex>``, pattern-validated on
  ``Finding`` itself, and its slug half must equal ``Finding.detector``, which
  is ``^[a-z][a-z0-9_]{0,63}$``. Same two uses.

Both are still escaped, so the alphabet is a second line rather than the only
one. Everything else that touches an attribute is an integer this package
computed or a member of a closed enum this package defined. ``Span.agent_id``
*is* trace-derived and has a constrained alphabet too
(``^[A-Za-z0-9_.:\\-]{1,64}$``, enforced by the mapper's ``safe_agent_id``), and
it is deliberately **not** used in an attribute anywhere: ``data-agent`` carries
``AgentRun.agent_index``, a decimal integer, and the agent id appears only as
redacted, escaped text. Relying on that alphabet would have worked; not relying
on it costs nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from swarm_observer.cost.compute import (
    RATE_CAVEAT,
    WASTE_CAVEAT,
    CostReport,
    format_display_usd,
    format_usd,
)
from swarm_observer.detect.base import SEVERITIES, SEVERITY_RANK, Finding
from swarm_observer.detect.registry import DETECTOR_SLUGS
from swarm_observer.model.trace import AgentRun, Span, Trace
from swarm_observer.report.escape import escape_html
from swarm_observer.report.json_out import (
    REDACTION_CAVEAT,
    REPORT_FORMAT_VERSION,
    last_timestamp,
    optional_timestamp,
    severity_counts,
)
from swarm_observer.report.narrative import (
    FALLBACK_CLASS,
    FALLBACK_PREFIX,
    NARRATIVE_CAVEAT,
    NARRATIVE_CLASS,
    NARRATIVE_SECTION_ID,
    NARRATIVE_SECTION_TITLE,
    Narrative,
)
from swarm_observer.report.sanitize import (
    KIND_AUTHORED,
    KIND_FREE,
    KIND_IDENTIFIER,
    KIND_NARRATOR,
    RenderOptions,
    TextKind,
    metric_value,
    optional_text,
    text,
)
from swarm_observer.report.timeline import (
    LANE_HEIGHT,
    RECT_CLASSES,
    Timeline,
    build_timeline,
    render_svg,
)

#: R36: the spans table is capped, with an "…and N more" line. A rendering
#: concession to a browser and to this file's size; the JSON report emits every
#: span (A-c9, S19).
SPANS_TABLE_CAP = 5000

#: How many "no timing" seqs R37's note names before it stops listing them. R37
#: says untimed spans are "listed in a no-timing note"; it does not say the note
#: may be two million integers long, and the count is the part a reader needs.
UNTIMED_LIST_CAP = 200

#: R34, verbatim. The first element of ``<head>`` after ``<meta charset>``.
CSP_CONTENT = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "img-src data:; base-uri 'none'; form-action 'none'"
)

#: Sentences this package authored, hoisted out of their f-strings so a line
#: stays inside the formatter's width now that every rendered string names its
#: class. They still go through the writer at their call sites (A-d1).
NO_TIMESTAMP_NOTE = "none recorded"
NO_WALL_CLOCK_NOTE = "No wall-clock time from this run appears in this document."
RECT_WIDTH_NOTE = "Rect width is a proportion of that span, rounded to whole units."
SPANS_CAP_NOTE = "The JSON report carries every span; this table is capped for the browser."
NARRATIVE_CALLS_NOTE = "Narrator calls made:"

#: The ``data-severity`` value the "show everything" filter control carries. Not
#: a :data:`~swarm_observer.detect.base.SEVERITIES` member — it is spelled
#: differently on purpose so the closed severity enum stays closed.
ALL_SEVERITIES = "all"

#: R36's section ids, in the order R36 pins them. The narrative anchor (R43) is
#: absent by design: with ``--explain`` off "no ``<section id="narrative">``
#: element exists", and increment 5 inserts it between the header and the
#: findings.
SECTION_IDS: tuple[str, ...] = ("header", "findings", "timeline", "cost", "spans", "warnings")

#: Every single-token ``class`` value this module writes. Composite class
#: attributes are confined to the timeline's rects
#: (:data:`~swarm_observer.report.timeline.RECT_CLASSES`) because R37 requires
#: fill to be chosen by fixed CSS classes; everywhere else a variable dimension
#: is a ``data-`` attribute and the class stays a single fixed token, which is
#: what keeps the allowlist a set of literals rather than a grammar.
CSS_CLASSES: frozenset[str] = frozenset(
    {
        "active",
        "bar",
        "caveat",
        "cell-num",
        "chip",
        "collapsed",
        "counts",
        "empty",
        "filter",
        "filters",
        "finding",
        "finding-body",
        "finding-head",
        "hidden",
        "lane",
        "lane-name",
        "lanes",
        "meta-grid",
        "metrics",
        "narrative",
        "narrative-fallback",
        "nav",
        "note",
        "preview",
        "provenance",
        "report",
        "section",
        "section-title",
        "sev",
        "table",
        "table-wrap",
        "timeline",
        "toggle",
        "truncation",
    }
)

#: R34: the identifiers the one script may not contain. Exposed as data so the
#: source-grep check reads this list rather than re-typing it — and so the check
#: greps :data:`REPORT_SCRIPT`, **not this module's source**, which necessarily
#: contains every one of these strings right here.
FORBIDDEN_SCRIPT_APIS: tuple[str, ...] = (
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

#: The one script (R34). A constant, never formatted. It does two things —
#: filter findings by severity and collapse a section — over nodes already
#: present in the document.
#:
#: BUG-19, increment-5 post-review. The collapse handler used to mark
#: ``event.currentTarget.parentNode``, which is ``div.section-title``, not the
#: ``<section>``. ``.collapsed > *:not(.section-title)`` then hid *that div's*
#: children — the ``<h2>`` and the control itself — and left the section's
#: content on the page, so "hide" deleted the heading, deleted the only way back
#: and hid nothing. ``closest(".section")`` names the collapse root the
#: stylesheet's ``:not`` clause assumes; the two halves now agree, and
#: ``tests_browser/`` executes them together in a real engine rather than
#: reading either one's bytes.
#:
#: The handlers also write ``aria-expanded`` and the control's visible
#: label. R34's prose says the script performs "only DOM class toggling,
#: filtering and sorting"; its enforced half is the list of sinks below, and
#: neither ``setAttribute`` with a literal attribute name and a ``"true"``/
#: ``"false"`` value nor ``textContent`` with a literal parses markup, reaches
#: the network or admits an interpolated byte. Both new attribute values are in
#: :func:`attribute_allowlist`, and a browser test re-checks every attribute in
#: the *live* DOM after exercising every control, so the guarantee is asserted
#: after the script has run and not only before.
#:
#: It contains no ``//``: R35 forbids a scheme-relative URL anywhere in the
#: file, and a line comment is indistinguishable from one to a check that reads
#: bytes rather than JavaScript. Comments are ``/* */`` for the same reason.
REPORT_SCRIPT = """\
(function () {
  "use strict";
  var doc = document;
  function each(selector, visit) {
    var nodes = doc.querySelectorAll(selector);
    for (var i = 0; i < nodes.length; i += 1) {
      visit(nodes[i]);
    }
  }
  function applyFilter(wanted) {
    each(".finding", function (node) {
      var keep = wanted === "all" || node.getAttribute("data-severity") === wanted;
      node.classList.toggle("hidden", !keep);
    });
    each(".filter", function (node) {
      node.classList.toggle("active", node.getAttribute("data-severity") === wanted);
    });
  }
  function setExpanded(section, expanded) {
    var button = section.querySelector(".section-title .toggle");
    section.classList.toggle("collapsed", !expanded);
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
    button.textContent = expanded ? "hide" : "show";
  }
  each(".filter", function (node) {
    node.addEventListener("click", function (event) {
      applyFilter(event.currentTarget.getAttribute("data-severity"));
    });
  });
  each(".toggle", function (node) {
    node.addEventListener("click", function (event) {
      var section = event.currentTarget.closest(".section");
      setExpanded(section, section.classList.contains("collapsed"));
    });
  });
  applyFilter("all");
})();
"""

#: The one stylesheet (R34). A constant, never formatted. No ``@import``, no
#: font reference, no ``url(...)`` of any kind, and no ``//``.
REPORT_STYLE = """\
:root {
  color-scheme: light dark;
  --ink: #16181d;
  --paper: #ffffff;
  --muted: #5b6270;
  --rule: #d7dae1;
  --panel: #f6f7f9;
  --info: #4a7fb5;
  --warning: #b5822a;
  --critical: #b1442f;
  --model: #6f7ee0;
  --tool: #3f9f8f;
  --user: #9a8ac0;
  --event: #9aa1ad;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px;
  background: var(--paper);
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 14px;
  line-height: 20px;
}
.report { margin: 0 auto; max-width: 1080px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 0; }
h3 { font-size: 14px; margin: 16px 0 4px; }
.section { border-top: 1px solid var(--rule); margin-top: 28px; padding-top: 12px; }
.section-title { align-items: center; display: flex; gap: 10px; }
.toggle {
  background: var(--panel);
  border: 1px solid var(--rule);
  border-radius: 4px;
  color: var(--muted);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 2px 8px;
}
.collapsed > *:not(.section-title) { display: none; }
.nav { color: var(--muted); display: flex; flex-wrap: wrap; gap: 12px; margin: 10px 0 0; }
.nav a { color: var(--muted); }
.meta-grid {
  display: grid;
  gap: 2px 16px;
  grid-template-columns: max-content 1fr;
  margin: 10px 0;
}
.meta-grid dt { color: var(--muted); }
.meta-grid dd { margin: 0; overflow-wrap: anywhere; }
.counts { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.chip {
  background: var(--panel);
  border: 1px solid var(--rule);
  border-radius: 999px;
  padding: 2px 10px;
}
.caveat, .note, .provenance, .truncation, .empty {
  color: var(--muted);
  font-size: 12px;
  margin: 8px 0;
}
.table-wrap { overflow-x: auto; }
.table { border-collapse: collapse; font-size: 13px; width: 100%; }
.table th, .table td {
  border-bottom: 1px solid var(--rule);
  padding: 4px 8px;
  text-align: left;
  vertical-align: top;
}
.table th { color: var(--muted); font-weight: 600; }
.cell-num { font-variant-numeric: tabular-nums; text-align: right; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.filter {
  background: var(--paper);
  border: 1px solid var(--rule);
  border-radius: 999px;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 2px 10px;
}
.filter.active { background: var(--panel); font-weight: 600; }
.hidden { display: none; }
.finding { border: 1px solid var(--rule); border-radius: 6px; margin: 10px 0; padding: 10px 12px; }
.finding-head { align-items: baseline; display: flex; flex-wrap: wrap; gap: 10px; }
.finding-body { margin-top: 6px; }
.sev { border-radius: 4px; color: var(--paper); font-size: 11px; padding: 1px 7px; }
.finding[data-severity="info"] .sev { background: var(--info); }
.finding[data-severity="warning"] .sev { background: var(--warning); }
.finding[data-severity="critical"] .sev { background: var(--critical); }
.metrics { color: var(--muted); font-size: 12px; margin: 6px 0 0; }
.preview {
  background: var(--panel);
  border-left: 3px solid var(--rule);
  font-family: ui-monospace, monospace;
  font-size: 12px;
  margin: 6px 0;
  overflow-wrap: anywhere;
  padding: 4px 8px;
  white-space: pre-wrap;
}
.lanes { list-style: none; margin: 8px 0; padding: 0; }
.lanes li { color: var(--muted); font-size: 12px; }
.lane-name { color: var(--ink); overflow-wrap: anywhere; }
.timeline { border: 1px solid var(--rule); height: auto; width: 100%; }
.timeline .lane { fill: var(--panel); }
.bar.k-model_call { fill: var(--model); }
.bar.k-tool_call { fill: var(--tool); }
.bar.k-user_message { fill: var(--user); }
.bar.k-system_event { fill: var(--event); }
.bar.s-info { fill: var(--info); }
.bar.s-warning { fill: var(--warning); }
.bar.s-critical { fill: var(--critical); }
@media (prefers-color-scheme: dark) {
  :root {
    --ink: #e7e9ee;
    --paper: #14161a;
    --muted: #9aa1ad;
    --rule: #333842;
    --panel: #1c1f25;
  }
}
"""


def sha256_of(text: str) -> str:
    """The lowercase hex SHA-256 of ``text`` as UTF-8 — R34's pin, as a function."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: R34: the pinned digest of :data:`REPORT_SCRIPT`. Checked in as a literal on
#: purpose. Changing the script costs two edits in two places with the reason
#: written between them — the same friction ``collection_floor.json`` and
#: ``LEAK_LEDGER_SIZE`` carry, and the most a single repository can do about a
#: ledger. The assertion that matters is not this constant against the constant
#: above but against the ``<script>`` element **as parsed out of the rendered
#: document**, which is what proves nothing was interpolated at render time.
#:
#: Moved once, by the increment-5 post-review, for BUG-19: the collapse
#: handler's root changed from ``parentNode`` to ``closest(".section")`` and it
#: now writes the control's state. Previous value:
#: ``17e03dec80b6d0255ef1f1b3db2ec39ce4903a9e742833c0e0a76c03f348dffb``.
#: :data:`STYLE_SHA256` did **not** move — the stylesheet was always right; it
#: was the script that disagreed with it.
SCRIPT_SHA256 = "caf9f8b6ece9945634499157cc515c9b2b5f4840a6b5f18221c7b879da228ab8"

#: R34: the pinned digest of :data:`REPORT_STYLE`. See :data:`SCRIPT_SHA256`.
STYLE_SHA256 = "97b30480f4024d9a5bfc298962a7d9e9c51e09081e2d4ae666d9f676d82ffa11"


class _Writer:
    """The one boundary every string this module writes passes through (C1).

    The increment-4 review's chief comment: escaping was made universal in
    increment 4 and has never leaked, while redaction stayed a three-way choice
    between ``_t``, ``_free`` and ``_ident`` that a human made per call site —
    and *that* half leaked in all four increments. This class is that comment
    implemented. There is one callable, its ``kind`` is keyword-only with no
    default, and :data:`~swarm_observer.report.sanitize.TextKind` is exhaustive,
    so a string cannot reach this document without its class being named. The
    classes themselves, and what each one does, live in
    :mod:`swarm_observer.report.sanitize`; this class only adds R32's escaping
    on top, which is the half that was already universal.

    It is constructed once per render, from the :class:`RenderOptions` the run
    was given, so no call site can pass the wrong ``previews`` value — that was
    a per-call-site argument until increment 5 too.
    """

    __slots__ = ("previews",)

    def __init__(self, options: RenderOptions) -> None:
        self.previews = options.previews

    def __call__(self, value: str, *, kind: TextKind) -> str:
        """Redact or blank by ``kind`` (R33, R38, R43), then escape (R32).

        Redaction runs **before** escaping and the order is load-bearing:
        escaping first would turn ``TOKEN="secret"`` into
        ``TOKEN&#x3D;&quot;secret&quot;`` and R33's patterns would no longer
        match it.
        """
        return escape_html(text(value, kind=kind, previews=self.previews))

    def optional(self, value: str | None, *, kind: TextKind, absent: str = "\u2014") -> str:
        """:meth:`__call__` for a field whose absence is distinct from its emptiness."""
        rendered = optional_text(value, kind=kind, previews=self.previews)
        return absent if rendered is None else escape_html(rendered)

    def money(self, value: Decimal) -> str:
        """R29's six-decimal string. Authored: a ``Decimal``'s own formatting."""
        return self(format_usd(value), kind=KIND_AUTHORED)


def _n(value: int) -> str:
    """An integer this package computed (R47).

    The one string in this module that does not go through :class:`_Writer`,
    and the exemption is a proof rather than a judgment: ``str`` of an ``int``
    is ``-?[0-9]+``, an alphabet that contains no character in
    :data:`~swarm_observer.report.escape.ESCAPE_TABLE`, no non-printable, and
    nothing any R33 pattern can match. No float ever reaches the document.
    """
    return str(value)


def attribute_allowlist(
    *,
    trace: Trace,
    findings: Sequence[Finding],
    timeline: Timeline,
    narrative: Narrative | None = None,
) -> dict[str, frozenset[str]]:
    """R34: every attribute value this document may contain, generated from the inputs.

    Attribute names are lowercase, as :mod:`html.parser` reports them — so
    ``viewBox`` appears as ``viewbox``.

    The point of generating it from the ``Trace``, the findings and the
    ``Timeline`` **model** rather than from the rendered bytes is that the
    resulting check compares the output against a specification of the output. A
    list harvested from the document would be satisfied by any document,
    including one with a payload in it, which is this project's signature defect
    in its purest form.

    The geometry entries are exact value sets rather than "any decimal integer"
    for the same reason: the set of ``x`` values the figure may use is knowable
    from the timeline model, so an ``x`` that is not one of them is a leak even
    though it looks like a number.
    """
    span_ids = {span.span_id for span in trace.spans}
    finding_ids = {finding.finding_id for finding in findings}
    # R43: the narrative anchor is an `id` the document carries only when
    # `--explain` produced content, and never an `href` — nothing links to it,
    # because a nav entry would be a byte outside the section that the flag
    # changed. The entry is added only when the section is rendered, so an
    # allowlist for a no-`--explain` render is exactly as wide as it was in
    # increment 4 and cannot admit an id the document could not contain (the
    # review's W5-A08).
    section_ids = set(SECTION_IDS) | ({NARRATIVE_SECTION_ID} if narrative is not None else set())
    fragments = {f"#{value}" for value in span_ids | finding_ids | set(SECTION_IDS)}
    lane_ys = {_n(lane.lane * LANE_HEIGHT) for lane in timeline.lanes}
    return {
        "lang": frozenset({"en"}),
        "charset": frozenset({"utf-8"}),
        "http-equiv": frozenset({"Content-Security-Policy"}),
        "content": frozenset({CSP_CONTENT}),
        "class": frozenset(CSS_CLASSES | RECT_CLASSES),
        "id": frozenset(section_ids | span_ids | finding_ids),
        "href": frozenset(fragments),
        "type": frozenset({"button"}),
        "role": frozenset({"img"}),
        "aria-label": frozenset({"execution timeline"}),
        # BUG-19, increment-5 post-review: the collapse control reports its
        # state. Both values are literals the script may also write at runtime
        # (see :data:`REPORT_SCRIPT`), so the allowlist has to admit the pair
        # rather than the rendered initial value alone — a browser test
        # re-checks the live DOM against this list after every control is used.
        "aria-expanded": frozenset({"true", "false"}),
        "data-severity": frozenset(set(SEVERITIES) | {ALL_SEVERITIES}),
        "data-detector": frozenset(DETECTOR_SLUGS),
        "data-agent": frozenset({_n(agent.agent_index) for agent in trace.agents}),
        "data-seq": frozenset({_n(span.seq) for span in trace.spans}),
        "viewbox": frozenset({f"0 0 {timeline.width} {max(timeline.height, 1)}"}),
        "x": frozenset({"0"} | {_n(rect.x) for rect in timeline.rects}),
        "y": frozenset(lane_ys | {_n(rect.y) for rect in timeline.rects}),
        "width": frozenset({_n(timeline.width)} | {_n(rect.width) for rect in timeline.rects}),
        "height": frozenset({_n(LANE_HEIGHT)} | {_n(rect.height) for rect in timeline.rects}),
    }


def _section(w: _Writer, section_id: str, title: str, body: Iterable[str]) -> list[str]:
    """One R36 section: a heading, its collapse control, and its content."""
    lines = [
        f'<section class="section" id="{w(section_id, kind=KIND_AUTHORED)}">',
        f'<div class="section-title"><h2>{w(title, kind=KIND_AUTHORED)}</h2>'
        f'<button class="toggle" type="button" aria-expanded="true">hide</button></div>',
    ]
    lines.extend(body)
    lines.append("</section>")
    return lines


def _header_section(
    *,
    w: _Writer,
    trace: Trace,
    findings: Sequence[Finding],
    cost: CostReport,
    tool_version: str,
    options: RenderOptions,
) -> list[str]:
    """R36's header block, plus R4's unknown-record-type surfacing.

    ``source_files[].name`` goes through :func:`_ident`: a filename is not
    trace-derived, but ``analyze <dir>`` reads whatever basenames the directory
    holds, so it is attacker-influenceable all the same. The increment-3
    review's ruling on the tester's S21 says exactly this, and the JSON report
    already treats it this way — "a filename is not trace-derived" is precisely
    the reading that would put it into HTML raw.
    """
    counts = severity_counts(findings)
    unknown_types = sum(
        warning.count for warning in trace.warnings if warning.code == "unknown_record_type"
    )
    body: list[str] = [
        '<dl class="meta-grid">',
        f"<dt>trace id</dt><dd>{w(trace.trace_id, kind=KIND_AUTHORED)}</dd>",
        f"<dt>adapter</dt><dd>{w(trace.adapter, kind=KIND_AUTHORED)}</dd>",
        f"<dt>trace schema</dt><dd>{w(trace.schema_version, kind=KIND_AUTHORED)}</dd>",
        f"<dt>report format</dt><dd>{w(REPORT_FORMAT_VERSION, kind=KIND_AUTHORED)}</dd>",
        f"<dt>swarm-observer</dt><dd>{w(tool_version, kind=KIND_AUTHORED)}</dd>",
        f"<dt>rate snapshot</dt><dd>{w(cost.meta.version, kind=KIND_AUTHORED)} "
        f"({w(cost.meta.snapshot_date, kind=KIND_AUTHORED)})</dd>",
        f"<dt>previews</dt><dd>"
        f"{w('included' if options.previews else 'omitted', kind=KIND_AUTHORED)}</dd>",
        f"<dt>blocked-gap seconds</dt><dd>{_n(options.blocked_gap_seconds)}</dd>",
        f"<dt>detectors</dt><dd>"
        f"{w(', '.join(options.detectors) or 'none', kind=KIND_AUTHORED)}</dd>",
        "</dl>",
        '<div class="counts">',
        f'<span class="chip">{_n(len(trace.agents))} agents</span>',
        f'<span class="chip">{_n(len(trace.spans))} spans</span>',
        f'<span class="chip">{_n(len(findings))} findings</span>',
        f'<span class="chip">critical {_n(counts["critical"])}</span>',
        f'<span class="chip">warning {_n(counts["warning"])}</span>',
        f'<span class="chip">info {_n(counts["info"])}</span>',
        f'<span class="chip">{_n(len(trace.warnings))} parse warnings</span>',
        "</div>",
    ]
    if unknown_types:
        # R4: "a non-zero `unknown_record_type` count is surfaced in the HTML
        # header" — the one tolerance that means the adapter did not understand
        # part of the input, so a reader must see it without scrolling.
        body.append(
            f'<p class="note">{_n(unknown_types)} record(s) had a type this adapter does '
            f"not know and produced no span. See the warnings section.</p>"
        )
    body.append('<h3>source files</h3><div class="table-wrap"><table class="table">')
    body.append("<tr><th>name</th><th>sha256</th><th>bytes</th><th>records</th></tr>")
    for source in trace.source_files:
        body.append(
            f"<tr><td>{w(source.name, kind=KIND_IDENTIFIER)}</td>"
            f"<td>{w(source.sha256, kind=KIND_AUTHORED)}</td>"
            f'<td class="cell-num">{_n(source.bytes)}</td>'
            f'<td class="cell-num">{_n(source.records)}</td></tr>'
        )
    body.append("</table></div>")
    body.append(f'<p class="caveat">{w(REDACTION_CAVEAT, kind=KIND_AUTHORED)}</p>')
    body.append(f'<p class="caveat">{w(RATE_CAVEAT, kind=KIND_AUTHORED)}</p>')
    last = last_timestamp(trace)
    last_text = w(NO_TIMESTAMP_NOTE if last is None else last, kind=KIND_AUTHORED)
    body.append(
        f'<p class="provenance">swarm-observer {w(tool_version, kind=KIND_AUTHORED)}; rates '
        f"{w(cost.meta.version, kind=KIND_AUTHORED)} of "
        f"{w(cost.meta.snapshot_date, kind=KIND_AUTHORED)}; trace's last timestamp "
        f"{last_text}. "
        f"{w(NO_WALL_CLOCK_NOTE, kind=KIND_AUTHORED)}</p>"
    )
    return _section(w, "header", "Run", body)


def _narrative_section(w: _Writer, narrative: Narrative) -> list[str]:
    """R36 + R43: the ``--explain`` section, and nothing else in the document.

    Three properties are load-bearing and all three are visible here:

    * **Every paragraph is written with the ``narrator`` kind**, whether it is
      the model's or swarm-observer's own template. Choosing the kind from
      ``paragraph.fallback`` would put a judgment back at the call site the
      increment-4 review's C1 removed, and redacting a template that contains
      only counts and slugs costs nothing. A reader of this loop does not have
      to know which paragraphs came from where in order to know they are all
      escaped.
    * **The two fallback markers R43 pairs are emitted by one branch**, so a
      paragraph cannot carry the class without the visible prefix or the
      prefix without the class. The narrator's own text is placed *after* the
      prefix this package wrote, never instead of it, so a model that writes
      "Deterministic summary:" itself cannot forge the marker.
    * **No ``href``, and no nav entry.** The section has an ``id`` so R36's
      anchor exists; nothing links to it, because a link would be a byte
      outside the section that ``--explain`` changed.
    """
    body: list[str] = [f'<p class="caveat">{w(NARRATIVE_CAVEAT, kind=KIND_AUTHORED)}</p>']
    if narrative.fallbacks:
        reasons = ", ".join(narrative.reasons) or "no reason recorded"
        body.append(
            f'<p class="note">{_n(narrative.fallbacks)} of '
            f"{_n(len(narrative.paragraphs))} paragraph(s) are swarm-observer's own "
            f"deterministic summaries rather than the narrator's "
            f"({w(reasons, kind=KIND_AUTHORED)}). "
            f"{w(NARRATIVE_CALLS_NOTE, kind=KIND_AUTHORED)} {_n(narrative.calls)}.</p>"
        )
    for paragraph in narrative.paragraphs:
        body.append(
            f"<h3>{w(paragraph.title, kind=KIND_NARRATOR)} "
            f"({w(paragraph.group, kind=KIND_AUTHORED)})</h3>"
        )
        if paragraph.fallback:
            body.append(
                f'<p class="{w(FALLBACK_CLASS, kind=KIND_AUTHORED)}">'
                f"{w(FALLBACK_PREFIX, kind=KIND_AUTHORED)} "
                f"{w(paragraph.text, kind=KIND_NARRATOR)}</p>"
            )
        else:
            body.append(
                f'<p class="{w(NARRATIVE_CLASS, kind=KIND_AUTHORED)}">'
                f"{w(paragraph.text, kind=KIND_NARRATOR)}</p>"
            )
    return _section(w, NARRATIVE_SECTION_ID, NARRATIVE_SECTION_TITLE, body)


def _grouped(findings: Sequence[Finding]) -> list[Finding]:
    """R36: severity descending, then detector slug, then ``finding_id``.

    R13 already sorts findings by ``(severity_rank, slug, finding_id)`` — the
    *ascending* severity order the JSON report emits (A-c8). R36's section
    layout is the reverse on the first key only, so it is applied here, in the
    renderer whose requirement it is, rather than by adding a second ordering
    rule to ``detect/``.
    """
    return sorted(
        findings,
        key=lambda finding: (
            -SEVERITY_RANK[finding.severity],
            finding.detector,
            finding.finding_id,
        ),
    )


def _findings_section(
    *,
    w: _Writer,
    findings: Sequence[Finding],
    span_ids: Mapping[int, str],
    options: RenderOptions,
) -> list[str]:
    """R36: the findings, each with summary, severity, metrics, evidence, previews, waste."""
    body: list[str] = [
        f'<p class="caveat">{w(WASTE_CAVEAT, kind=KIND_AUTHORED)}</p>',
        '<div class="filters">',
        f'<button class="filter" type="button" '
        f'data-severity="{w(ALL_SEVERITIES, kind=KIND_AUTHORED)}">all</button>',
    ]
    for severity in reversed(SEVERITIES):
        body.append(
            f'<button class="filter" type="button" '
            f'data-severity="{w(severity, kind=KIND_AUTHORED)}">'
            f"{w(severity, kind=KIND_AUTHORED)}</button>"
        )
    body.append("</div>")
    if not findings:
        body.append('<p class="empty">No detector produced a finding for this trace.</p>')
        return _section(w, "findings", "Findings", body)

    for finding in _grouped(findings):
        body.append(
            f'<article class="finding" id="{w(finding.finding_id, kind=KIND_AUTHORED)}" '
            f'data-severity="{w(finding.severity, kind=KIND_AUTHORED)}" '
            f'data-detector="{w(finding.detector, kind=KIND_AUTHORED)}">'
        )
        body.append(
            f'<div class="finding-head">'
            f'<span class="sev">{w(finding.severity, kind=KIND_AUTHORED)}</span>'
            f"<strong>{w(finding.detector, kind=KIND_AUTHORED)}</strong>"
            f"<span>{w(finding.summary, kind=KIND_AUTHORED)}</span></div>"
        )
        body.append('<div class="finding-body">')
        body.append(f"<p><code>{w(finding.finding_id, kind=KIND_AUTHORED)}</code></p>")
        if finding.metrics:
            # The one place in this module where the class is applied a call up
            # rather than at the writer, and it is deliberate. `metric_value`
            # (R16, S13) chooses `identifier` for `tool_name` and `authored`
            # for everything else, by key, from
            # `detect.base.TRACE_DERIVED_METRIC_KEYS` — so by the time `w` sees
            # this string its trace-derived half has already been redacted and
            # only R32's escaping is left, which is what `KIND_AUTHORED` means
            # here. Re-classifying at the writer instead would be *free*, since
            # R33's redaction is idempotent — and that is exactly the reason
            # not to: the second redaction would produce identical bytes with
            # or without the first, and
            # `tests/canaries/test_canary_metrics_redaction_dropped.py`, whose
            # only job is to show this call is load-bearing, would go green
            # with the call deleted. A guard hidden behind an idempotent second
            # guard is a guard nothing can prove.
            pairs = " · ".join(
                f"{key}={metric_value(key, value)}"
                for key, value in sorted(finding.metrics.items())
            )
            body.append(f'<p class="metrics">{w(pairs, kind=KIND_AUTHORED)}</p>')
        if finding.agent_ids:
            # Escaped per element rather than over the join: `escape_html` is a
            # per-code-point map (R32), so the bytes are identical either way,
            # and this way every string in the line has its own named class.
            agents = ", ".join(w(agent_id, kind=KIND_IDENTIFIER) for agent_id in finding.agent_ids)
            body.append(f'<p class="metrics">agents: {agents}</p>')
        if finding.span_seqs:
            # The link target is the span row's `id`, which is `Span.span_id`
            # (R5, 16 hex) — never the `seq`, which is not an id in this
            # document and would not be in R34's allowlist. A span past
            # `SPANS_TABLE_CAP` has no row, so it is named without a link rather
            # than linked to an anchor that does not exist.
            links = " ".join(
                f'<a href="#{w(span_ids[seq], kind=KIND_AUTHORED)}">{_n(seq)}</a>'
                if seq in span_ids
                else _n(seq)
                for seq in finding.span_seqs
            )
            body.append(f'<p class="metrics">spans: {links}</p>')
        for preview in finding.previews:
            body.append(f'<p class="preview">{w(preview, kind=KIND_FREE)}</p>')
        waste = finding.wasted
        cost_text = (
            "unknown" if finding.wasted_cost_usd is None else format_usd(finding.wasted_cost_usd)
        )
        body.append(
            f'<p class="metrics">attributed waste: {_n(waste.total)} tokens, '
            f"{w(cost_text, kind=KIND_AUTHORED)} USD</p>"
        )
        body.append("</div></article>")
    return _section(w, "findings", "Findings", body)


def _timeline_section(
    *,
    w: _Writer,
    timeline: Timeline,
    agents_by_id: Mapping[str, AgentRun],
    options: RenderOptions,
) -> list[str]:
    """R36 + R37: the figure, its legend, and the "no timing" note.

    The legend is HTML and the figure is SVG, and that split is the point: agent
    ids are trace-derived, so they are rendered as text nodes beside the figure
    and no trace-derived byte enters the ``<svg>`` at all.
    """
    body: list[str] = []
    # The lane legend is rendered **unconditionally**, before the figure and
    # whether or not there is a figure. It is the only place ``AgentRun``'s
    # trace-derived strings — ``agent_id``, ``agent_type``, ``description``,
    # ``parent_agent_id`` — reach the HTML document, and a legend that appeared
    # only when some span happened to carry timing would mean "no payload in the
    # report" was sometimes true because nothing was rendered. That is the shape
    # of this project's signature defect, and the injection probe runs over this
    # exact section.
    body.append('<div class="table-wrap"><table class="table">')
    body.append(
        "<tr><th>lane</th><th>agent index</th><th>agent</th><th>type</th>"
        "<th>description</th><th>parent</th><th>depth</th><th>spans</th><th>drawn</th></tr>"
    )
    for lane in timeline.lanes:
        agent = agents_by_id.get(lane.agent_id)
        agent_kind = "—" if agent is None else w.optional(agent.agent_type, kind=KIND_FREE)
        described = "" if agent is None else w(agent.description, kind=KIND_FREE)
        parent = (
            "—"
            if agent is None or agent.parent_agent_id is None
            else w(agent.parent_agent_id, kind=KIND_IDENTIFIER)
        )
        depth = "—" if agent is None or agent.depth is None else _n(agent.depth)
        owned = _n(0 if agent is None else len(agent.span_seqs))
        body.append(
            f'<tr data-agent="{_n(lane.agent_index)}">'
            f'<td class="cell-num">{_n(lane.lane)}</td>'
            f'<td class="cell-num">{_n(lane.agent_index)}</td>'
            f'<td class="lane-name">{w(lane.agent_id, kind=KIND_IDENTIFIER)}</td>'
            f"<td>{agent_kind}</td>"
            f"<td>{described}</td>"
            f"<td>{parent}</td>"
            f'<td class="cell-num">{depth}</td>'
            f'<td class="cell-num">{owned}</td>'
            f'<td class="cell-num">{_n(lane.drawn)}</td></tr>'
        )
    body.append("</table></div>")
    if not timeline.rects:
        body.append('<p class="empty">No span in this trace carries both a start and an end.</p>')
    else:
        body.append(
            f'<p class="note">One lane per agent, in agent-index order; '
            f"the figure spans {_n(timeline.span_ms)} ms of trace time. "
            f"{w(RECT_WIDTH_NOTE, kind=KIND_AUTHORED)}</p>"
        )
        body.append(render_svg(timeline))
    if timeline.untimed_seqs:
        listed = timeline.untimed_seqs[:UNTIMED_LIST_CAP]
        shown = ", ".join(_n(seq) for seq in listed)
        more = len(timeline.untimed_seqs) - len(listed)
        tail = f" …and {_n(more)} more" if more > 0 else ""
        body.append(
            f'<p class="note">{_n(len(timeline.untimed_seqs))} span(s) have no start or no '
            f"end and are not drawn: {w(shown, kind=KIND_AUTHORED)}{tail}</p>"
        )
    if not options.previews:
        body.append(
            '<p class="note">Previews are omitted; lane labels are redacted identifiers.</p>'
        )
    return _section(w, "timeline", "Timeline", body)


def _cost_section(*, w: _Writer, cost: CostReport, options: RenderOptions) -> list[str]:
    """R31's four groupings and R30's unpriced table (R36).

    The unpriced count sits beside the grand total, which is the increment-3
    review's §6 recommendation: a trace on a retired model contributes nothing to
    the headline figure and only a reader who scrolls to the unpriced section
    would otherwise know.
    """
    body: list[str] = [
        '<dl class="meta-grid">',
        f"<dt>total</dt><dd>{w.money(cost.total_cost_usd)} USD "
        f"({w(format_display_usd(cost.total_cost_usd), kind=KIND_AUTHORED)} USD)</dd>",
        f"<dt>priced spans</dt><dd>{_n(cost.priced_spans)}</dd>",
        f"<dt>unpriced spans</dt><dd>{_n(cost.unpriced_spans)}</dd>",
        f"<dt>tokens</dt><dd>{_n(cost.total_usage.total)}</dd>",
        f"<dt>unpriced tokens</dt><dd>{_n(cost.unpriced_usage.total)}</dd>",
        "</dl>",
    ]
    if cost.unpriced_spans:
        body.append(
            f'<p class="note">{_n(cost.unpriced_spans)} model call(s) could not be priced and '
            f"contribute nothing to the total above.</p>"
        )

    body.append('<h3>by agent</h3><div class="table-wrap"><table class="table">')
    body.append(
        "<tr><th>agent</th><th>index</th><th>priced</th><th>unpriced</th>"
        "<th>tokens</th><th>cost USD</th></tr>"
    )
    for agent_row in cost.by_agent:
        body.append(
            f"<tr><td>{w(agent_row.agent_id, kind=KIND_IDENTIFIER)}</td>"
            f'<td class="cell-num">{_n(agent_row.agent_index)}</td>'
            f'<td class="cell-num">{_n(agent_row.priced_spans)}</td>'
            f'<td class="cell-num">{_n(agent_row.unpriced_spans)}</td>'
            f'<td class="cell-num">{_n(agent_row.usage.total)}</td>'
            f'<td class="cell-num">{w.money(agent_row.cost_usd)}</td></tr>'
        )
    body.append("</table></div>")

    body.append('<h3>by model</h3><div class="table-wrap"><table class="table">')
    body.append("<tr><th>model key</th><th>priced</th><th>tokens</th><th>cost USD</th></tr>")
    for model_row in cost.by_model:
        body.append(
            f"<tr><td>{w(model_row.model_key, kind=KIND_AUTHORED)}</td>"
            f'<td class="cell-num">{_n(model_row.priced_spans)}</td>'
            f'<td class="cell-num">{_n(model_row.usage.total)}</td>'
            f'<td class="cell-num">{w.money(model_row.cost_usd)}</td></tr>'
        )
    body.append("</table></div>")

    body.append('<h3>by detector</h3><div class="table-wrap"><table class="table">')
    body.append(
        "<tr><th>detector</th><th>findings</th><th>unknown cost</th>"
        "<th>wasted tokens</th><th>of which unpriced</th><th>wasted USD</th></tr>"
    )
    for detector_row in cost.by_detector:
        body.append(
            f"<tr><td>{w(detector_row.detector, kind=KIND_AUTHORED)}</td>"
            f'<td class="cell-num">{_n(detector_row.findings)}</td>'
            f'<td class="cell-num">{_n(detector_row.findings_unpriced)}</td>'
            f'<td class="cell-num">{_n(detector_row.wasted.total)}</td>'
            f'<td class="cell-num">{_n(detector_row.wasted_unpriced.total)}</td>'
            f'<td class="cell-num">{w.money(detector_row.wasted_cost_usd)}</td></tr>'
        )
    body.append("</table></div>")

    body.append("<h3>unpriced model calls</h3>")
    if not cost.unpriced:
        body.append('<p class="empty">Every model call in this trace was priced.</p>')
    else:
        body.append('<div class="table-wrap"><table class="table">')
        body.append(
            "<tr><th>seq</th><th>agent</th><th>recorded model</th><th>reason</th>"
            "<th>missing price keys</th></tr>"
        )
        for unpriced in cost.unpriced:
            body.append(
                f'<tr><td class="cell-num">{_n(unpriced.seq)}</td>'
                f"<td>{w(unpriced.agent_id, kind=KIND_IDENTIFIER)}</td>"
                f"<td>{w(unpriced.model, kind=KIND_FREE)}</td>"
                f"<td>{w(unpriced.reason, kind=KIND_AUTHORED)}</td>"
                f"<td>{w(', '.join(unpriced.missing_price_keys), kind=KIND_AUTHORED)}</td></tr>"
            )
        body.append("</table></div>")
    body.append(f'<p class="caveat">{w(RATE_CAVEAT, kind=KIND_AUTHORED)}</p>')
    return _section(w, "cost", "Cost", body)


def _spans_section(
    *,
    w: _Writer,
    trace: Trace,
    cost: CostReport,
    options: RenderOptions,
) -> list[str]:
    """R36: every span, capped at :data:`SPANS_TABLE_CAP` rows with an "…and N more" line.

    The per-span cost is looked up from a dict built once, not from
    ``CostReport.cost_of``, which scans the priced list linearly: over 5,000 rows
    that would be quadratic. The increment-3 review raised exactly this (C2), and
    the answer here is that the helper still has no caller in the product.

    ``span.seq`` is used as the row key and the anchor target, and the span is
    found by iterating ``trace.spans`` rather than by ``trace.spans[seq]``: R6
    makes ``seq == index`` true for anything the v1 mapper produces and R2 does
    not require it (review, C4).
    """
    cost_by_seq: Mapping[int, Decimal] = {row.seq: row.cost_usd for row in cost.spans}
    body: list[str] = ['<div class="table-wrap"><table class="table">']
    body.append(
        "<tr><th>seq</th><th>kind</th><th>agent</th><th>start</th><th>end</th>"
        "<th>model</th><th>stop</th><th>tool</th><th>tool use id</th><th>digest</th>"
        "<th>status</th><th>error</th><th>tokens</th><th>cost USD</th><th>previews</th></tr>"
    )
    shown: list[Span] = list(trace.spans[:SPANS_TABLE_CAP])
    for span in shown:
        span_cost = cost_by_seq.get(span.seq)
        # **Every** preview, each in its own labelled block. Rendering only the
        # first non-empty one — which this table did until the hostile fixture
        # was rendered by hand — dropped `tool_result_preview` wherever a span
        # also had text, and that is where `hostile.jsonl` puts its AWS key, its
        # `sk-ant-` key and its PEM block. The report was poorer for it, and,
        # far worse, "no credential shape appears in the HTML" was true because
        # the text was never rendered rather than because it was redacted. A
        # probe whose subject is absent is the defect this project keeps
        # shipping; the fix is to render the subject.
        previews = [
            (label, value)
            for label, value in (
                ("text", span.text_preview),
                ("input", span.tool_input_preview),
                ("result", span.tool_result_preview),
            )
            if value
        ]
        preview_cell = "".join(
            f'<p class="preview">{w(label, kind=KIND_AUTHORED)}: {w(value, kind=KIND_FREE)}</p>'
            for label, value in previews
        )
        error = span.error
        # `error.code` goes through `_ident`, not `_t`. R12 derives it from the
        # record's own `error` field through `ingest.text.slug` — the same
        # construction R4 uses for a `ParseWarning.detail`, which has gone
        # through the redactor since increment 3. Its alphabet stops markup and
        # admits a lowercase credential, so "it is an enumerated slug" is the
        # same reasoning that put `agent_id` and `tool_name` into a report raw
        # in increments 1-3. Redacted in both modes, never blanked: blanking it
        # would merge distinct API errors in the one column that says what went
        # wrong (the review's ruling on S16, applied to the field it missed).
        error_cell = (
            "—"
            if error is None
            else f"{w(error.code, kind=KIND_IDENTIFIER)}: {w(error.detail, kind=KIND_FREE)}"
        )
        body.append(
            f'<tr id="{w(span.span_id, kind=KIND_AUTHORED)}" data-seq="{_n(span.seq)}">'
            f'<td class="cell-num">{_n(span.seq)}</td>'
            f"<td>{w(span.kind, kind=KIND_AUTHORED)}</td>"
            f"<td>{w(span.agent_id, kind=KIND_IDENTIFIER)}</td>"
            f"<td>{w(optional_timestamp(span.start) or '—', kind=KIND_AUTHORED)}</td>"
            f"<td>{w(optional_timestamp(span.end) or '—', kind=KIND_AUTHORED)}</td>"
            f"<td>{w.optional(span.model, kind=KIND_FREE)}</td>"
            f"<td>{w.optional(span.stop_reason, kind=KIND_FREE)}</td>"
            f"<td>{w.optional(span.tool_name, kind=KIND_FREE)}</td>"
            f"<td>{w.optional(span.tool_use_id, kind=KIND_FREE)}</td>"
            f"<td>{w(span.tool_input_digest or '—', kind=KIND_AUTHORED)}</td>"
            f"<td>{w(span.tool_result_status or '—', kind=KIND_AUTHORED)}</td>"
            f"<td>{error_cell}</td>"
            f'<td class="cell-num">{_n(span.usage.total if span.usage else 0)}</td>'
            f'<td class="cell-num">{"—" if span_cost is None else w.money(span_cost)}</td>'
            f"<td>{preview_cell}</td></tr>"
        )
    body.append("</table></div>")
    remaining = len(trace.spans) - len(shown)
    if remaining > 0:
        body.append(
            f'<p class="truncation">…and {_n(remaining)} more span(s). '
            f"{w(SPANS_CAP_NOTE, kind=KIND_AUTHORED)}</p>"
        )
    return _section(w, "spans", "Spans", body)


def _warnings_section(w: _Writer, trace: Trace) -> list[str]:
    """R36 + R10: the full parse-warning list.

    ``detail`` goes through :func:`_ident` rather than :func:`_free`: R4 puts the
    unknown record *type* there, which is trace-derived, and R10 constrains it to
    a slug alphabet. Blanking it under ``--no-previews`` would merge the
    ``(code, detail)`` pairs R10 aggregates on (review, S16).
    """
    if not trace.warnings:
        return _section(
            w,
            "warnings",
            "Parse warnings",
            ['<p class="empty">The parser tolerated nothing.</p>'],
        )
    body = ['<div class="table-wrap"><table class="table">']
    body.append("<tr><th>code</th><th>count</th><th>detail</th></tr>")
    for warning in trace.warnings:
        body.append(
            f"<tr><td>{w(warning.code, kind=KIND_AUTHORED)}</td>"
            f'<td class="cell-num">{_n(warning.count)}</td>'
            f"<td>{w(warning.detail, kind=KIND_IDENTIFIER)}</td></tr>"
        )
    body.append("</table></div>")
    return _section(w, "warnings", "Parse warnings", body)


def render_html(
    *,
    trace: Trace,
    findings: Sequence[Finding],
    cost: CostReport,
    tool_version: str,
    options: RenderOptions,
    narrative: Narrative | None = None,
) -> str:
    """The exact bytes of ``report.html`` (R34, R35, R36, R37, R47).

    Sections in R36's pinned order: ``<h1>``, header, (narrative anchor —
    increment 5, absent), findings, timeline, cost, spans, warnings. The one
    ``<script>`` and the one ``<style>`` are written by concatenation, never by
    formatting.
    """
    w = _Writer(options)
    timeline = build_timeline(trace, findings)
    lines: list[str] = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP_CONTENT}">',
        "<title>swarm-observer report</title>",
        "<style>",
        REPORT_STYLE.rstrip("\n"),
        "</style>",
        "</head>",
        "<body>",
        '<main class="report">',
        "<h1>swarm-observer report</h1>",
        '<nav class="nav">',
    ]
    lines.extend(
        f'<a href="#{w(section_id, kind=KIND_AUTHORED)}">{w(section_id, kind=KIND_AUTHORED)}</a>'
        for section_id in SECTION_IDS
    )
    lines.append("</nav>")
    lines.extend(
        _header_section(
            w=w,
            trace=trace,
            findings=findings,
            cost=cost,
            tool_version=tool_version,
            options=options,
        )
    )
    if narrative is not None:
        # R36's fixed position: after the header block, before the findings.
        # Whole lines, contiguous, and nothing else in the document moves --
        # which is what makes R43's "strip the section and the bytes are
        # identical" a property of the construction rather than of a test.
        lines.extend(_narrative_section(w, narrative))
    span_ids = {span.seq: span.span_id for span in trace.spans[:SPANS_TABLE_CAP]}
    lines.extend(_findings_section(w=w, findings=findings, span_ids=span_ids, options=options))
    agents_by_id = {agent.agent_id: agent for agent in trace.agents}
    lines.extend(
        _timeline_section(w=w, timeline=timeline, agents_by_id=agents_by_id, options=options)
    )
    lines.extend(_cost_section(w=w, cost=cost, options=options))
    lines.extend(_spans_section(w=w, trace=trace, cost=cost, options=options))
    lines.extend(_warnings_section(w, trace))
    lines.extend(
        [
            "</main>",
            "<script>",
            REPORT_SCRIPT.rstrip("\n"),
            "</script>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "ALL_SEVERITIES",
    "CSP_CONTENT",
    "CSS_CLASSES",
    "FORBIDDEN_SCRIPT_APIS",
    "REPORT_SCRIPT",
    "REPORT_STYLE",
    "SCRIPT_SHA256",
    "SECTION_IDS",
    "SPANS_TABLE_CAP",
    "STYLE_SHA256",
    "UNTIMED_LIST_CAP",
    "attribute_allowlist",
    "render_html",
    "sha256_of",
]
