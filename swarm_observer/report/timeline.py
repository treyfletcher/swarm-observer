"""The execution timeline — integer-only SVG geometry (R37).

Two decisions shape this module, and both are security decisions as much as
rendering ones.

**No float ever reaches the output bytes.** R37 pins the geometry as
``x = round_half_up(1000 * offset_ms / trace_span_ms)`` on a fixed 1000-unit
viewBox, and R47 forbids "any float in output bytes". Float formatting is not
identical across interpreters or platforms for every value, so a single
``/`` here would make the byte-identical guarantee a property of the machine
that ran it. :func:`round_half_up` therefore takes two integers and returns one,
by the ``(2n + d) // 2d`` identity, and every dimension in the document is the
``str`` of an ``int``.

**No trace-derived byte enters the SVG at all.** The lane labels — agent ids,
which are trace-derived (R5 takes one straight from the record's ``agentId``) —
are rendered by ``report/html.py`` as ordinary HTML text nodes in a legend
beside the figure, not as SVG ``<text>``. The consequence is worth stating
plainly, because it is the strongest claim in this increment:

    Every attribute value and every character of the ``<svg>`` element is
    either a fixed class name this package authored or the decimal
    representation of an integer this package computed. There is no escaping
    question inside the figure because there is nothing to escape.

That is why :func:`render_svg` takes a :class:`Timeline` and not a ``Trace``:
the model carries the agent ids for the legend, and the markup function cannot
reach them.

Three edge cases R37 names explicitly, each handled here rather than by the
caller:

* a span with a null ``start`` or ``end`` is **not drawn** and is listed in
  :attr:`Timeline.untimed_seqs` for the "no timing" note;
* ``trace_span_ms == 0`` — every timed span is an instant, or there is exactly
  one — draws every rect at ``x=0, width=1``;
* a trace with no timed span at all yields an empty :attr:`Timeline.rects`, and
  the renderer says so instead of emitting a figure with no content.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from swarm_observer.detect.base import SEVERITY_RANK, Finding, millis_between
from swarm_observer.model.trace import SPAN_KINDS, Span, Trace

#: R37: the viewBox is a fixed 1000 units wide, so a rect's ``x`` is a per-mille
#: position and the figure scales with the page rather than with the trace.
VIEWBOX_WIDTH = 1000

#: R37: "a minimum rect width of 1 unit" — an instantaneous span is still
#: visible, and a zero-width rect is invisible in every renderer.
MIN_RECT_WIDTH = 1

#: Vertical geometry, in the same user units. Integers, and constants rather
#: than configuration: R47 makes a golden report a function of the trace and one
#: integer, and a tunable lane height would be a second one.
LANE_HEIGHT = 22
RECT_HEIGHT = 12
RECT_Y_OFFSET = (LANE_HEIGHT - RECT_HEIGHT) // 2

#: The severity slug a rect carries when no finding names its span. Not a
#: severity — ``Severity`` is a closed three-value enum (R14) — so it is spelled
#: differently on purpose and lives only in a CSS class name.
NO_SEVERITY = "none"

#: The severity values a rect's class may name, ordered by rank so the set is
#: legible next to :data:`~swarm_observer.detect.base.SEVERITY_RANK`.
RECT_SEVERITIES: tuple[str, ...] = (NO_SEVERITY, "info", "warning", "critical")

#: R34: every ``class`` value a rect may carry, enumerated. The attribute
#: allowlist is generated from this rather than from the rendered document, so
#: the check compares the output against the *specification* of the output.
RECT_CLASSES: frozenset[str] = frozenset(
    f"bar k-{kind} s-{severity}" for kind in SPAN_KINDS for severity in RECT_SEVERITIES
)


def round_half_up(numerator: int, denominator: int) -> int:
    """``round(numerator / denominator)``, half away from zero, in integers only.

    ``(2n + d) // 2d`` is exact for non-negative ``n`` and positive ``d``: it is
    ``floor(n/d + 1/2)``, which is round-half-up. Python's own ``round`` is
    round-half-to-**even** and takes a float, so it is wrong twice over for this
    requirement.

    Raises on a non-positive denominator rather than returning a sentinel: the
    only caller that can reach ``trace_span_ms == 0`` handles that case before
    calling, and a silent zero here would put every rect at ``x=0`` while
    looking like it had computed something.
    """
    if denominator <= 0:
        raise ValueError("round_half_up needs a positive denominator")
    if numerator < 0:
        raise ValueError("round_half_up is defined here for non-negative numerators")
    return (2 * numerator + denominator) // (2 * denominator)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TimelineRect(_Frozen):
    """One drawn span. Every field is an integer or a fixed slug (R34, R37)."""

    seq: int = Field(ge=0)
    agent_index: int = Field(ge=0)
    #: The lane's position in :attr:`Timeline.lanes`, which is agent-index order.
    lane: int = Field(ge=0)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=MIN_RECT_WIDTH)
    height: int = Field(ge=1)
    kind: str
    severity: str

    @property
    def css_class(self) -> str:
        """The ``class`` attribute value — always a member of :data:`RECT_CLASSES`."""
        return f"bar k-{self.kind} s-{self.severity}"


class TimelineLane(_Frozen):
    """One agent's lane. ``agent_id`` is trace-derived and never enters the SVG."""

    lane: int = Field(ge=0)
    agent_index: int = Field(ge=0)
    agent_id: str
    #: How many of this agent's spans were drawn — the legend says so, which is
    #: what makes an empty lane read as "no timing" rather than as a bug.
    drawn: int = Field(ge=0)


class Timeline(_Frozen):
    """The whole figure as data, before any markup exists (R37)."""

    width: int = Field(ge=1)
    height: int = Field(ge=0)
    lanes: tuple[TimelineLane, ...] = ()
    rects: tuple[TimelineRect, ...] = ()
    #: The spans R37 says are "listed in a no-timing note rather than drawn",
    #: ascending.
    untimed_seqs: tuple[int, ...] = ()
    #: The trace's own span, in whole milliseconds. ``0`` when every timed span
    #: is an instant, and ``0`` when nothing is timed at all — the two are
    #: distinguished by :attr:`rects` being empty.
    span_ms: int = Field(default=0, ge=0)


def severity_by_seq(findings: Iterable[Finding]) -> dict[int, str]:
    """The highest severity any finding assigns to each span ``seq``.

    Presentation, not detection: it re-reads ``Finding.span_seqs``, which the
    detectors already produced, and assigns a colour. It computes no finding and
    changes none, which is the line the Modularity notes draw around ``report/``.

    "Highest" is by :data:`~swarm_observer.detect.base.SEVERITY_RANK`, so a span
    named by one ``info`` finding and one ``critical`` finding reads as
    critical — under-reporting a span's worst verdict in a figure a reader skims
    is the failure that matters here.
    """
    worst: dict[int, str] = {}
    for finding in findings:
        for seq in finding.span_seqs:
            current = worst.get(seq)
            if current is None or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[current]:
                worst[seq] = finding.severity
    return worst


def _timed(spans: Sequence[Span]) -> tuple[Span, ...]:
    """The spans R37 draws: both endpoints present."""
    return tuple(span for span in spans if span.start is not None and span.end is not None)


def build_timeline(trace: Trace, findings: Sequence[Finding] = ()) -> Timeline:
    """Lay the figure out (R37). Pure integer arithmetic, no markup.

    Lanes are one per agent in ``agent_index`` order — *every* agent, including
    one with nothing timed, because a missing lane would silently renumber the
    lanes below it and the legend is what a reader uses to read the figure.
    """
    severities = severity_by_seq(findings)
    drawable = _timed(trace.spans)
    drawable_seqs = {span.seq for span in drawable}
    untimed = tuple(span.seq for span in trace.spans if span.seq not in drawable_seqs)

    lane_of = {agent.agent_id: index for index, agent in enumerate(trace.agents)}
    index_of = {agent.agent_id: agent.agent_index for agent in trace.agents}

    rects: list[TimelineRect] = []
    span_ms = 0
    if drawable:
        # `start` and `end` are non-None for every member of `drawable`; the
        # local names are what mypy needs to see that.
        starts = [span.start for span in drawable if span.start is not None]
        ends = [span.end for span in drawable if span.end is not None]
        trace_start = min(starts)
        trace_end = max(ends)
        # R6 clamps a negative duration at parse time and counts it; the same
        # clamp here keeps a trace whose last span ends before the first one
        # starts from producing a negative denominator.
        span_ms = max(0, millis_between(trace_start, trace_end))

        for span in drawable:
            start, end = span.start, span.end
            if start is None or end is None:  # pragma: no cover - `_timed` filtered these
                continue
            lane = lane_of.get(span.agent_id)
            if lane is None:
                # An agent with spans but no `AgentRun` cannot happen with the
                # v1 mapper, which builds `agents` from the spans. Skipping is
                # the fail-safe answer for a future adapter: a rect with no lane
                # has nowhere to be drawn, and inventing a lane would shift every
                # other agent's row.
                continue
            offset_ms = max(0, millis_between(trace_start, start))
            duration_ms = max(0, millis_between(start, end))
            if span_ms == 0:
                # R37: "When trace_span_ms == 0, every rect is drawn at
                # x=0, width=1." Also the only place division is unreachable.
                x = 0
                width = MIN_RECT_WIDTH
            else:
                x = round_half_up(VIEWBOX_WIDTH * offset_ms, span_ms)
                width = max(MIN_RECT_WIDTH, round_half_up(VIEWBOX_WIDTH * duration_ms, span_ms))
                # Rounding each end independently can push the right edge one
                # unit past the viewBox (x=1 from 0.5 plus width=1000 from
                # 999.5). R37 pins the formula and says nothing about the
                # clamp; a rect outside its own viewBox is a rendering defect
                # in every browser, so the figure is clamped and the formula is
                # left exactly as written.
                x = min(x, VIEWBOX_WIDTH - MIN_RECT_WIDTH)
                width = max(MIN_RECT_WIDTH, min(width, VIEWBOX_WIDTH - x))
            rects.append(
                TimelineRect(
                    seq=span.seq,
                    agent_index=index_of.get(span.agent_id, lane),
                    lane=lane,
                    x=x,
                    y=lane * LANE_HEIGHT + RECT_Y_OFFSET,
                    width=width,
                    height=RECT_HEIGHT,
                    kind=span.kind,
                    severity=severities.get(span.seq, NO_SEVERITY),
                )
            )

    drawn_per_lane: dict[int, int] = {}
    for rect in rects:
        drawn_per_lane[rect.lane] = drawn_per_lane.get(rect.lane, 0) + 1

    lanes = tuple(
        TimelineLane(
            lane=index,
            agent_index=agent.agent_index,
            agent_id=agent.agent_id,
            drawn=drawn_per_lane.get(index, 0),
        )
        for index, agent in enumerate(trace.agents)
    )
    return Timeline(
        width=VIEWBOX_WIDTH,
        height=len(lanes) * LANE_HEIGHT,
        lanes=lanes,
        rects=tuple(sorted(rects, key=lambda rect: rect.seq)),
        untimed_seqs=tuple(sorted(untimed)),
        span_ms=span_ms,
    )


def render_svg(timeline: Timeline) -> str:
    """The inline ``<svg>`` element (R34, R35, R37).

    No ``xmlns``. An inline ``<svg>`` in an HTML5 document is put in the SVG
    namespace by the HTML parser itself, and the attribute's only legal value is
    ``http://www.w3.org/2000/svg`` — a ``http:`` URL, which R35 forbids
    *anywhere in the rendered file*. Writing it would break the no-external-URL
    property to state something the parser already knows.

    No ``<style>`` and no ``fill`` attribute either: R34 permits exactly one
    ``<style>`` element in the document and R37 says the fill comes from fixed
    CSS classes, so every colour decision is in the one constant stylesheet.
    """
    lines = [
        f'<svg class="timeline" viewBox="0 0 {timeline.width} {max(timeline.height, 1)}" '
        f'role="img" aria-label="execution timeline">'
    ]
    for lane in timeline.lanes:
        lines.append(
            f'<rect class="lane" x="0" y="{lane.lane * LANE_HEIGHT}" '
            f'width="{timeline.width}" height="{LANE_HEIGHT}" '
            f'data-agent="{lane.agent_index}"></rect>'
        )
    for rect in timeline.rects:
        lines.append(
            f'<rect class="{rect.css_class}" x="{rect.x}" y="{rect.y}" '
            f'width="{rect.width}" height="{rect.height}" '
            f'data-seq="{rect.seq}" data-agent="{rect.agent_index}"></rect>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


__all__ = [
    "LANE_HEIGHT",
    "MIN_RECT_WIDTH",
    "NO_SEVERITY",
    "RECT_CLASSES",
    "RECT_HEIGHT",
    "RECT_SEVERITIES",
    "RECT_Y_OFFSET",
    "VIEWBOX_WIDTH",
    "Timeline",
    "TimelineLane",
    "TimelineRect",
    "build_timeline",
    "render_svg",
    "round_half_up",
    "severity_by_seq",
]
