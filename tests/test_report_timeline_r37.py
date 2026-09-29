"""R37: the timeline's geometry is integers, and the figure carries no trace byte.

R37 pins a formula, a viewBox width, a minimum rect width and three edge cases,
and R47 forbids "any float in output bytes". Those are two different claims and
they are tested separately: :class:`TestRoundHalfUpR37` is the arithmetic, and
:class:`TestTheSvgIsIntegersAndConstantsOnly` reads the rendered element and
asserts that every character of it is either a fixed class name or a decimal
integer. The second is the claim A-d8 makes and it is stronger than R37 asks
for, so it is asserted against the markup rather than taken from the docstring.

The clamp A-d7 adds is not in R37. It is tested by the one input that
distinguishes "clamped" from "never overflowed":
``test_ad7_the_clamp_fires_on_a_span_that_would_otherwise_end_at_1001`` asserts
the *unclamped* formula overflows for that input and that the rect does not, so
removing the clamp turns it red rather than leaving it green on a figure that
never reached the edge.
"""

from __future__ import annotations

import re

import pytest

from swarm_observer.detect.base import SEVERITIES, build_finding
from swarm_observer.model.trace import SPAN_KINDS, TRACE_SCHEMA_VERSION, Trace
from swarm_observer.report.timeline import (
    LANE_HEIGHT,
    MIN_RECT_WIDTH,
    NO_SEVERITY,
    RECT_CLASSES,
    RECT_HEIGHT,
    RECT_SEVERITIES,
    RECT_Y_OFFSET,
    VIEWBOX_WIDTH,
    build_timeline,
    render_svg,
    round_half_up,
    severity_by_seq,
)

from .synthetic_traces import SYNTHETIC_TRACE_ID, TraceBuilder, at

#: Every attribute the SVG may carry, and the shape its value must have. Typed
#: here rather than read from ``attribute_allowlist`` so the two oracles are
#: independent: the allowlist is the coder's, this is the requirement's.
SVG_VALUE_SHAPES: dict[str, str] = {
    "class": r"^(timeline|lane|bar k-[a-z_]+ s-[a-z]+)$",
    "viewbox": r"^0 0 \d+ \d+$",
    "role": r"^img$",
    "aria-label": r"^execution timeline$",
    "x": r"^\d+$",
    "y": r"^\d+$",
    "width": r"^\d+$",
    "height": r"^\d+$",
    "data-seq": r"^\d+$",
    "data-agent": r"^\d+$",
}

ATTRIBUTE = re.compile(r'([A-Za-z-]+)="([^"]*)"')


def empty_trace() -> Trace:
    """A valid ``Trace`` with no spans and no agents (R2 allows it)."""
    return Trace(
        schema_version=TRACE_SCHEMA_VERSION,
        trace_id=SYNTHETIC_TRACE_ID,
        adapter="claude_code_jsonl",
    )


class TestRoundHalfUpR37:
    """R37: "all coordinates are integers; no float ever reaches the output"."""

    @pytest.mark.parametrize(
        ("numerator", "denominator", "expected"),
        [
            (1, 2, 1),  # 0.5 -> 1
            (3, 2, 2),  # 1.5 -> 2
            (5, 2, 3),  # 2.5 -> 3, where Python's round() gives 2
            (7, 2, 4),  # 3.5 -> 4
            (0, 2, 0),
            (1, 3, 0),  # 0.333
            (2, 3, 1),  # 0.667
            (1000, 1, 1000),
            (999_999_999_999, 1_000_000_000, 1000),
        ],
    )
    def test_r37_round_half_up_rounds_halves_away_from_zero(
        self, numerator: int, denominator: int, expected: int
    ) -> None:
        """R37: half-up, not Python's half-to-even, and never through a float.

        Red when: ``(2n + d) // 2d`` is replaced by ``round(n / d)`` — the 5/2
        and 7/2 rows disagree — or by ``n // d``, where every non-zero row above
        ½ disagrees.
        """
        assert round_half_up(numerator, denominator) == expected

    def test_r37_round_half_up_disagrees_with_pythons_round_at_a_half(self) -> None:
        """R37: the discriminating case, stated as a comparison.

        Red when: the function becomes ``round``. This is the arm that would
        otherwise be invisible, because half-to-even and half-up agree
        everywhere except on an exact half of an odd integer.
        """
        assert round(5 / 2) == 2
        assert round_half_up(5, 2) == 3

    def test_r37_round_half_up_returns_an_int_for_every_input(self) -> None:
        """R47: no float may reach the output bytes, so none may leave this function.

        Red when: a ``/`` replaces the ``//``, which returns a float and would
        render as ``500.0``.
        """
        for numerator in range(0, 50):
            value = round_half_up(numerator, 7)
            assert isinstance(value, int) and not isinstance(value, bool)

    @pytest.mark.parametrize(("numerator", "denominator"), [(1, 0), (1, -2), (-1, 2), (-1, -1)])
    def test_r37_round_half_up_refuses_a_domain_it_is_not_defined_on(
        self, numerator: int, denominator: int
    ) -> None:
        """R37: raising beats a sentinel that looks like a computed zero.

        Red when: the guards are dropped. ``denominator == 0`` would be a
        ``ZeroDivisionError`` deep inside a render; a silent ``0`` would put
        every rect at ``x=0`` while looking like it had computed something.
        """
        with pytest.raises(ValueError):
            round_half_up(numerator, denominator)


class TestGeometryR37:
    """R37: positions, widths and the three named edge cases."""

    def test_r37_a_span_is_positioned_by_its_offset_into_the_trace(self) -> None:
        """R37: ``x = round_half_up(1000 * offset_ms, trace_span_ms)``.

        Red when: the numerator or denominator changes, or the viewBox width
        stops being 1000. Values are asserted as literals computed by hand from
        the requirement, not from ``round_half_up``.
        """
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=1_000)
        builder.model_call(start_ms=4_000, end_ms=5_000)
        builder.model_call(start_ms=8_000, end_ms=8_000)
        timeline = build_timeline(builder.build())
        assert timeline.span_ms == 8_000
        assert [(rect.seq, rect.x, rect.width) for rect in timeline.rects] == [
            (0, 0, 125),
            (1, 500, 125),
            (2, 1000 - MIN_RECT_WIDTH, MIN_RECT_WIDTH),
        ]

    def test_r37_a_zero_length_span_still_gets_the_minimum_width(self) -> None:
        """R37: "a minimum rect width of 1 unit".

        Red when: ``max(MIN_RECT_WIDTH, …)`` is dropped — a zero-width rect is
        invisible in every renderer, so the span would vanish from the figure
        while still being counted in the legend.
        """
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=10_000)
        builder.model_call(start_ms=5_000, end_ms=5_000)
        timeline = build_timeline(builder.build())
        instant = next(rect for rect in timeline.rects if rect.seq == 1)
        assert instant.width == MIN_RECT_WIDTH == 1

    def test_r37_trace_span_ms_zero_draws_every_rect_at_x0_width1(self) -> None:
        """R37, named edge case: "When trace_span_ms == 0, every rect is drawn at x=0, width=1".

        Red when: the ``span_ms == 0`` branch is removed — ``round_half_up``
        then raises on a zero denominator and the whole render dies.
        """
        builder = TraceBuilder()
        for _ in range(3):
            builder.model_call(start_ms=0, end_ms=0)
        timeline = build_timeline(builder.build())
        assert timeline.span_ms == 0
        assert len(timeline.rects) == 3
        assert {(rect.x, rect.width) for rect in timeline.rects} == {(0, MIN_RECT_WIDTH)}

    def test_r37_a_single_timed_span_is_the_same_case(self) -> None:
        """R37: one span means ``trace_start == trace_end`` is possible but need not be.

        Red when: the ``span_ms == 0`` branch is made conditional on the *count*
        of spans rather than on the measured span.
        """
        builder = TraceBuilder()
        builder.model_call(start_ms=100, end_ms=900)
        timeline = build_timeline(builder.build())
        assert timeline.span_ms == 800
        assert [(rect.x, rect.width) for rect in timeline.rects] == [(0, VIEWBOX_WIDTH)]

    def test_r37_spans_with_a_null_endpoint_are_listed_not_drawn(self) -> None:
        """R37: "Spans with a null start or end are listed in a 'no timing' note".

        Red when: ``_timed`` widens to "start is not None", which would put a
        rect with no end into the figure at width 1 and silently claim it was
        instantaneous.
        """
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=1_000)
        builder.model_call(start_ms=500, end_ms=None)
        builder.model_call(start_ms=None, end_ms=900)
        builder.user_message()
        timeline = build_timeline(builder.build())
        assert [rect.seq for rect in timeline.rects] == [0]
        assert timeline.untimed_seqs == (1, 2, 3)

    def test_r37_a_trace_with_nothing_timed_yields_no_rects_and_keeps_its_lanes(self) -> None:
        """R37: no figure, but the agents still exist and the legend still says so.

        Red when: the lanes are built from the *rects* rather than from the
        agents — an agent with no timing would disappear from the legend, which
        is the vacuity A-d9 is about.
        """
        builder = TraceBuilder()
        builder.user_message(agent_id="alpha")
        builder.user_message(agent_id="beta")
        timeline = build_timeline(builder.build())
        assert timeline.rects == ()
        assert [lane.agent_id for lane in timeline.lanes] == ["alpha", "beta"]
        assert [lane.drawn for lane in timeline.lanes] == [0, 0]
        assert timeline.untimed_seqs == (0, 1)

    def test_r37_an_empty_trace_produces_an_empty_but_valid_figure(self) -> None:
        """R37: zero agents, zero rects, and a viewBox a parser still accepts.

        Red when: ``max(timeline.height, 1)`` is dropped — ``viewBox="0 0 1000 0"``
        is a degenerate viewport.
        """
        timeline = build_timeline(empty_trace())
        assert timeline.lanes == () and timeline.rects == () and timeline.height == 0
        assert 'viewBox="0 0 1000 1"' in render_svg(timeline)

    def test_ad7_the_clamp_fires_on_a_span_that_would_otherwise_end_at_1001(self) -> None:
        """A-d7: R37's formula can put a rect's right edge one unit outside its viewBox.

        The two halves matter equally. The first asserts the *unclamped*
        requirement formula overflows for this input, so the clamp is load
        bearing rather than decorative; the second asserts the rendered rect
        does not. Red when: the clamp is removed — the second assertion fails;
        or when this input stops overflowing — the first fails and the test
        needs a new input rather than quietly testing nothing.
        """
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=2_000)
        builder.model_call(start_ms=1, end_ms=2_000)
        timeline = build_timeline(builder.build())
        assert round_half_up(VIEWBOX_WIDTH * 1, 2_000) == 1
        assert round_half_up(VIEWBOX_WIDTH * 1_999, 2_000) == 1_000
        assert VIEWBOX_WIDTH + 1 == 1 + 1_000, "the unclamped formula leaves the viewBox"
        edge = next(rect for rect in timeline.rects if rect.seq == 1)
        assert (edge.x, edge.width) == (1, 999)

    def test_ad7_no_rect_ever_leaves_the_viewbox(self) -> None:
        """A-d7: the property, over a range of offsets rather than one hand-picked pair.

        Red when: the clamp is applied to only one of ``x`` and ``width``.
        """
        for offset_ms in (0, 1, 2, 499, 500, 501, 999, 1_999, 2_000):
            builder = TraceBuilder()
            builder.model_call(start_ms=0, end_ms=2_000)
            builder.model_call(start_ms=offset_ms, end_ms=2_000)
            for rect in build_timeline(builder.build()).rects:
                assert 0 <= rect.x <= VIEWBOX_WIDTH - MIN_RECT_WIDTH, offset_ms
                assert rect.width >= MIN_RECT_WIDTH, offset_ms
                assert rect.x + rect.width <= VIEWBOX_WIDTH, offset_ms

    def test_r37_a_trace_whose_every_timed_span_ends_before_it_starts_still_renders(
        self,
    ) -> None:
        """R6/R37: ``trace_end`` can precede ``trace_start``, which makes ``span_ms`` negative.

        ``span_ms`` is ``max(0, millis_between(min(starts), max(ends)))``. The
        clamp only matters when **every** timed span ends before it starts, so a
        trace with one inverted span among several never reaches it — which is
        why the ``max(0, ...)`` mutant survived the first increment-4 sweep.
        Without the clamp, ``span_ms`` is negative, the ``span_ms == 0`` branch
        is skipped, and ``round_half_up`` raises on a negative denominator: the
        whole render dies on a trace the mapper can produce from a clock that
        went backwards.

        Red when: either ``max(0, ...)`` around ``span_ms`` is removed.
        """
        builder = TraceBuilder()
        first = builder.model_call(start_ms=2_000, end_ms=3_000)
        second = builder.model_call(start_ms=4_000, end_ms=5_000)
        builder.replace(first, start=at(2_000), end=at(0))
        builder.replace(second, start=at(4_000), end=at(1_000))
        trace = builder.build()
        assert max(span.end for span in trace.spans if span.end) < min(
            span.start for span in trace.spans if span.start
        )
        timeline = build_timeline(trace)
        assert timeline.span_ms == 0
        assert [(rect.x, rect.width) for rect in timeline.rects] == [
            (0, MIN_RECT_WIDTH),
            (0, MIN_RECT_WIDTH),
        ]
        assert "viewBox" in render_svg(timeline)

    def test_r37_a_negative_duration_is_clamped_rather_than_producing_a_negative_width(
        self,
    ) -> None:
        """R6/R37: "any computed duration that comes out negative is clamped to 0".

        The mapper clamps at parse time; this asserts the renderer does not
        assume it did. Red when: either ``max(0, …)`` in ``build_timeline`` is
        dropped, which would raise inside ``round_half_up``'s negative-numerator
        guard and kill the render.
        """
        builder = TraceBuilder()
        first = builder.model_call(start_ms=0, end_ms=4_000)
        second = builder.model_call(start_ms=2_000, end_ms=3_000)
        builder.replace(second, start=first.end, end=first.start)
        timeline = build_timeline(builder.build())
        assert all(rect.width >= MIN_RECT_WIDTH for rect in timeline.rects)
        assert all(rect.x >= 0 for rect in timeline.rects)


class TestLanesAndSeverityR37:
    """R37: one lane per agent in ``agent_index`` order; fill by kind and severity."""

    def test_r37_there_is_one_lane_per_agent_in_agent_index_order(self) -> None:
        """R37: "One horizontal lane per agent, ordered by agent_index".

        Red when: lanes are built from a set, or sorted by agent id.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="zeta", start_ms=0, end_ms=10)
        builder.model_call(agent_id="alpha", start_ms=10, end_ms=20)
        trace = builder.build()
        timeline = build_timeline(trace)
        assert [lane.agent_id for lane in timeline.lanes] == ["zeta", "alpha"]
        assert [lane.agent_index for lane in timeline.lanes] == [0, 1]
        assert [lane.lane for lane in timeline.lanes] == [0, 1]
        assert timeline.height == 2 * LANE_HEIGHT

    def test_r37_a_rects_y_is_its_lane_and_the_lanes_do_not_overlap(self) -> None:
        """R37: vertical geometry is integer and per lane.

        Red when: ``RECT_Y_OFFSET`` or ``LANE_HEIGHT`` changes without the
        figure being re-read — two lanes' bars would overlap.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="a", start_ms=0, end_ms=10)
        builder.model_call(agent_id="b", start_ms=0, end_ms=10)
        timeline = build_timeline(builder.build())
        assert [rect.y for rect in timeline.rects] == [
            RECT_Y_OFFSET,
            LANE_HEIGHT + RECT_Y_OFFSET,
        ]
        assert RECT_Y_OFFSET * 2 + RECT_HEIGHT == LANE_HEIGHT

    def test_r37_severity_by_seq_keeps_the_worst_verdict_for_a_span(self) -> None:
        """R37: a span named by an ``info`` and a ``critical`` finding reads as critical.

        Red when: the comparison is dropped and last-writer wins — the figure
        would under-report a span's worst verdict depending on detector order,
        which is a reader-facing wrong answer rather than a crash.
        """
        builder = TraceBuilder()
        builder.tool_call(start_ms=0, end_ms=10)
        trace = builder.build()
        low = build_finding(
            trace=trace,
            detector="failed_tool_call",
            severity="info",
            summary="s",
            span_seqs=(0,),
            agent_ids=("root",),
            metrics={"failures": 1},
        )
        high = build_finding(
            trace=trace,
            detector="unresolved_tool_call",
            severity="critical",
            summary="s",
            span_seqs=(0,),
            agent_ids=("root",),
            metrics={"occurrences": 1},
        )
        assert severity_by_seq([low, high]) == {0: "critical"}
        assert severity_by_seq([high, low]) == {0: "critical"}
        assert severity_by_seq([]) == {}

    def test_r37_an_unnamed_span_carries_the_no_severity_slug(self) -> None:
        """R37: ``NO_SEVERITY`` is deliberately not a ``Severity`` member.

        Red when: ``NO_SEVERITY`` becomes ``"info"`` — every undiagnosed span in
        every figure would light up as a finding.
        """
        assert NO_SEVERITY not in SEVERITIES
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=10)
        assert build_timeline(builder.build()).rects[0].severity == NO_SEVERITY

    def test_r37_rect_classes_enumerates_every_kind_and_severity_combination(self) -> None:
        """R34/R37: fill comes from fixed CSS classes, and the set is closed.

        Red when: a span kind is added to ``SPAN_KINDS`` without
        ``RECT_CLASSES`` following — the new kind's rects would carry a class
        outside R34's allowlist and the attribute check would go red, which is
        the intended failure, but only if this set is generated rather than
        hand-listed.
        """
        assert RECT_SEVERITIES == (NO_SEVERITY, "info", "warning", "critical")
        assert len(RECT_CLASSES) == len(SPAN_KINDS) * len(RECT_SEVERITIES)
        for kind in SPAN_KINDS:
            for severity in RECT_SEVERITIES:
                assert f"bar k-{kind} s-{severity}" in RECT_CLASSES

    def test_r37_every_rendered_rect_class_is_a_member_of_rect_classes(self) -> None:
        """R34: the class the markup writes is the class the allowlist declares.

        Red when: ``TimelineRect.css_class`` and ``RECT_CLASSES`` drift — the
        one place a composite class attribute exists in the document.
        """
        builder = TraceBuilder()
        builder.model_call(start_ms=0, end_ms=10)
        builder.tool_call(start_ms=1, end_ms=5)
        builder.user_message()
        builder.system_event(start_ms=6, end_ms=7)
        timeline = build_timeline(builder.build())
        assert timeline.rects
        for rect in timeline.rects:
            assert rect.css_class in RECT_CLASSES


class TestTheSvgIsIntegersAndConstantsOnly:
    """A-d8: every character of the ``<svg>`` element is a constant or a decimal integer."""

    def figure(self) -> str:
        builder = TraceBuilder()
        builder.model_call(agent_id="alpha", start_ms=0, end_ms=1_000)
        builder.tool_call(agent_id="alpha", start_ms=100, end_ms=400)
        builder.model_call(agent_id="beta", start_ms=500, end_ms=1_000)
        return render_svg(build_timeline(builder.build()))

    def test_r37_every_attribute_in_the_figure_matches_a_declared_shape(self) -> None:
        """R34/R37: no attribute in the figure may hold anything but those shapes.

        The shapes are typed from the requirement in :data:`SVG_VALUE_SHAPES`,
        not read from ``attribute_allowlist``, so this is a second independent
        oracle rather than a restatement of the coder's.

        Red when: an attribute is added to the figure — including a ``fill``, a
        ``style`` or an ``xmlns`` — or when a value stops being an integer.
        """
        found = ATTRIBUTE.findall(self.figure())
        assert found
        for name, value in found:
            key = name.lower()
            assert key in SVG_VALUE_SHAPES, f"undeclared SVG attribute {name}={value!r}"
            assert re.match(SVG_VALUE_SHAPES[key], value), f"{name}={value!r}"

    def test_r35_the_figure_carries_no_xmlns_and_no_url(self) -> None:
        """A-d6/R35: ``xmlns``'s only legal value is an ``http:`` URL, which R35 forbids.

        Red when: someone "fixes" the SVG by adding the namespace — which would
        put an ``http:`` URL into a document R35 says has none.
        """
        figure = self.figure()
        assert "xmlns" not in figure
        for scheme in ("http:", "https:", "file:", "data:", "//"):
            assert scheme not in figure, scheme

    def test_r34_the_figure_holds_no_script_no_style_and_no_fill(self) -> None:
        """R34/R37: one ``<script>`` and one ``<style>`` in the document, neither here.

        Red when: a per-rect ``fill`` or an inline ``<style>`` is added to the
        figure, which would put a second style context in the document and make
        R34's "exactly one" false.
        """
        figure = self.figure()
        for forbidden in ("<script", "<style", "fill=", "style=", "onclick", "<a "):
            assert forbidden not in figure, forbidden

    def test_ad8_render_svg_cannot_reach_a_trace_derived_string(self) -> None:
        """A-d8: ``render_svg`` takes a ``Timeline``, and the agent id never enters the markup.

        The strongest form available without reading the source: the lane's
        ``agent_id`` is a payload, and it is absent from the figure while the
        integer ``agent_index`` is present.

        Red when: a future edit adds an SVG ``<text>`` lane label — legal under
        R34, and the thing A-d8 gives up in exchange for having no escaping
        question inside the figure at all.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="payload.agent.id", start_ms=0, end_ms=10)
        timeline = build_timeline(builder.build())
        assert timeline.lanes[0].agent_id == "payload.agent.id"
        figure = render_svg(timeline)
        assert "payload.agent.id" not in figure
        assert 'data-agent="0"' in figure

    def test_r47_no_float_shaped_token_appears_in_the_figure(self) -> None:
        """R47: "any float in output bytes" is forbidden, including in the SVG.

        Red when: a ``/`` replaces ``round_half_up`` anywhere in the geometry.
        """
        assert not re.search(r"\d+\.\d+", self.figure())
