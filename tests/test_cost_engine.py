"""The cost engine, clause by clause: R28, R29, R30, R31, and AC7.

Every expected dollar figure in this module is **computed from the snapshot**,
by an oracle that shares no arithmetic with the code under test:
:func:`oracle_cost` reads the rate strings out of the ``RateSource``, does the
whole of R28 in :class:`fractions.Fraction` — exact rationals, no ``Decimal``
context, no quantize — and rounds half-up with integer arithmetic. A
hard-coded total is a test that passes because somebody pasted the output; a
Fraction oracle disagrees with a wrong ``Decimal`` pipeline and agrees with a
right one, and it is the only way "the six decimal places are correct" is a
claim about the requirement rather than about the implementation.

Three things get disproportionate attention, because each is a place where one
character produces a plausible wrong number rather than a failure:

* **R30's priority order.** Four rungs, and the requirement *is* the order. Every
  test below builds a span satisfying two rungs at once and asserts which reason
  is reported — not merely that the span is unpriced.
* **``rate_key_missing``'s zero-component carve-out.** ``tokens != 0 and
  price_key not in rates`` is a conjunction and both halves are asserted, from
  both sides, one token apart.
* **The ``Inexact`` trap.** A guard that fires on honest input gets removed, so
  it is shown *not* firing on a 10^12-token call, on every shipped rate, and on
  a two-million-value sum — and shown firing on the corrupt input it exists for.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from swarm_observer.cost.compute import (
    COST_DECIMAL_PLACES,
    COST_PRECISION,
    COST_QUANTUM,
    DISPLAY_QUANTUM,
    RATE_CAVEAT,
    SYNTHETIC_MODEL,
    UNPRICED_REASONS,
    WASTE_CAVEAT,
    ZERO_USD,
    CostError,
    CostReport,
    classify_span,
    compute_costs,
    format_display_usd,
    format_usd,
    missing_price_keys,
    price_usage,
    quantize_cost,
    quantize_display,
    sum_usage,
    sum_usd,
)
from swarm_observer.cost.snapshot import SnapshotRateSource
from swarm_observer.cost.source import (
    PRICE_KEYS,
    USAGE_PRICE_KEYS,
    RateEntry,
    RateSnapshot,
    RateSource,
    RateSourceRef,
    SnapshotMeta,
    rates_for,
)
from swarm_observer.detect.base import DetectorConfig, attribute_waste
from swarm_observer.detect.registry import run_detectors_with_waste
from swarm_observer.model.trace import Span, SpanError, TokenUsage, Trace

from .detector_corpus import fixture_paths, load_trace
from .synthetic_traces import TraceBuilder

REPO = Path(__file__).resolve().parent.parent

SHIPPED = SnapshotRateSource()


# --- the oracle ---------------------------------------------------------------


def oracle_cost(usage: TokenUsage, rates: dict[str, Fraction]) -> Decimal:
    """R28 and R29 computed in exact rationals, sharing nothing with the code.

    R28's five terms summed as :class:`~fractions.Fraction`, divided by
    1,000,000, then rounded half-up to six places by integer arithmetic. Not a
    paraphrase of ``price_usage``: a different number type, a different rounding
    mechanism and no ``decimal.Context`` anywhere.
    """
    total = Fraction(0)
    for component, price_key in USAGE_PRICE_KEYS:
        rate = rates.get(price_key)
        if rate is None:
            continue
        total += Fraction(int(getattr(usage, component))) * rate
    exact = total / 1_000_000
    scaled = exact * 10**COST_DECIMAL_PLACES
    # ROUND_HALF_UP on a non-negative value: floor(x + 1/2).
    rounded = (scaled.numerator * 2 + scaled.denominator) // (scaled.denominator * 2)
    # Built from digits rather than by ``scaleb``, which would consult the
    # ambient decimal context and round a long value back to 28 places.
    digits = str(rounded).rjust(COST_DECIMAL_PLACES + 1, "0")
    return Decimal(f"{digits[:-COST_DECIMAL_PLACES]}.{digits[-COST_DECIMAL_PLACES:]}")


def oracle_rates(source: RateSource, model_key: str) -> dict[str, Fraction]:
    """The snapshot's rates for ``model_key`` as exact rationals."""
    found: dict[str, Fraction] = {}
    for price_key in PRICE_KEYS:
        rate = source.get_rate(model_key, price_key)
        if rate is not None:
            found[price_key] = Fraction(str(rate))
    return found


def oracle_for(source: RateSource, model_key: str, usage: TokenUsage) -> Decimal:
    """:func:`oracle_cost` against a live source's published rates."""
    return oracle_cost(usage, oracle_rates(source, model_key))


# --- fixtures -----------------------------------------------------------------


def snapshot_of(
    models: dict[str, RateEntry], aliases: dict[str, str] | None = None
) -> RateSnapshot:
    """A minimal valid snapshot over ``models``, every key attributed."""
    return RateSnapshot(
        meta=SnapshotMeta(
            version="test",
            snapshot_date="2026-01-01",
            sources=(
                RateSourceRef(
                    id="test_source",
                    label="a source",
                    url="https://example.invalid/rates",
                    as_of="2026-01-01",
                    models=tuple(sorted(models)),
                ),
            ),
        ),
        models=models,
        aliases=dict(aliases or {}),
    )


def source_of(models: dict[str, RateEntry], aliases: dict[str, str] | None = None) -> RateSource:
    """A :class:`SnapshotRateSource` over a hand-built snapshot."""
    return SnapshotRateSource(snapshot=snapshot_of(models, aliases))


FULL_ENTRY = RateEntry(
    input="1", output="2", cache_read="3", cache_write_5m="4", cache_write_1h="5"
)


def model_span(
    *,
    seq: int = 0,
    model: str | None = "claude-sonnet-4-5",
    usage: TokenUsage | None = None,
    error: SpanError | None = None,
    agent_id: str = "root",
) -> Span:
    """One ``model_call`` span, built directly so a boundary is one field away."""
    return Span(
        span_id=f"{seq:016x}",
        agent_id=agent_id,
        kind="model_call",
        seq=seq,
        model=model,
        usage=usage,
        error=error,
    )


# --- R28 ----------------------------------------------------------------------


class TestFormulaR28:
    """R28: five terms, one division by a million, nothing added or subtracted."""

    @pytest.mark.parametrize(
        "component",
        [component for component, _ in USAGE_PRICE_KEYS],
    )
    def test_r28_each_term_is_priced_by_its_own_key(self, component: str) -> None:
        """R28: one component at a time, against the Fraction oracle."""
        rates = {
            "input": Decimal("3"),
            "output": Decimal("15"),
            "cache_read": Decimal("0.30"),
            "cache_write_5m": Decimal("3.75"),
            "cache_write_1h": Decimal("6"),
        }
        usage = TokenUsage(**{component: 1_234_567})
        expected = oracle_cost(usage, {key: Fraction(str(v)) for key, v in rates.items()})
        assert price_usage(usage, rates) == expected
        assert expected > 0, "a term that prices to zero cannot distinguish anything"

    def test_r28_the_five_terms_add_rather_than_replace(self) -> None:
        """R28: the whole-usage cost is the sum of the five single-component costs."""
        rates = oracle_rates(SHIPPED, "claude-sonnet-4-5")
        decimal_rates = dict(rates_for(SHIPPED, "claude-sonnet-4-5"))
        usage = TokenUsage(
            input_tokens=11,
            output_tokens=22,
            cache_read_input_tokens=33,
            cache_creation_5m_tokens=44,
            cache_creation_1h_tokens=55,
        )
        whole = oracle_cost(usage, rates)
        assert price_usage(usage, decimal_rates) == whole
        pieces = sum(
            oracle_cost(TokenUsage(**{component: getattr(usage, component)}), rates)
            for component, _ in USAGE_PRICE_KEYS
        )
        assert whole == pieces

    def test_r28_input_tokens_are_not_reduced_by_cached_tokens(self) -> None:
        """R28: ``input_tokens`` as recorded already excludes cached tokens.

        A subtraction would make these two prices equal. They must not be.
        """
        rates = dict(rates_for(SHIPPED, "claude-sonnet-4-5"))
        with_cache = TokenUsage(input_tokens=1_000, cache_read_input_tokens=9_000)
        without = TokenUsage(input_tokens=1_000)
        assert price_usage(with_cache, rates) != price_usage(without, rates)
        assert price_usage(with_cache, rates) == oracle_for(
            SHIPPED, "claude-sonnet-4-5", with_cache
        )
        # And the difference is exactly the cache-read term, not a reduced input term.
        difference = price_usage(with_cache, rates) - price_usage(without, rates)
        assert difference == oracle_for(
            SHIPPED, "claude-sonnet-4-5", TokenUsage(cache_read_input_tokens=9_000)
        )

    def test_r28_thinking_tokens_are_never_added_because_the_model_has_no_field(self) -> None:
        """R28: thinking tokens live inside ``output_tokens`` and nowhere else."""
        assert set(TokenUsage.model_fields) == {
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_5m_tokens",
            "cache_creation_1h_tokens",
        }
        rates = dict(rates_for(SHIPPED, "claude-sonnet-4-5"))
        usage = TokenUsage(output_tokens=500)
        assert price_usage(usage, rates) == oracle_for(SHIPPED, "claude-sonnet-4-5", usage)

    def test_r28_the_two_cache_creation_tiers_are_priced_apart(self) -> None:
        """R28: the 5m and 1h breakdowns are different keys at different rates."""
        rates = dict(rates_for(SHIPPED, "claude-sonnet-4-5"))
        five = price_usage(TokenUsage(cache_creation_5m_tokens=1_000_000), rates)
        hour = price_usage(TokenUsage(cache_creation_1h_tokens=1_000_000), rates)
        assert five == Decimal("3.750000")
        assert hour == Decimal("6.000000")
        assert five != hour

    def test_r28_an_absent_rate_contributes_nothing_for_a_zero_component(self) -> None:
        """R28/R30: a zero component with no rate is not a silent zero term."""
        assert price_usage(TokenUsage(), {}) == ZERO_USD
        assert price_usage(TokenUsage(input_tokens=0), {"output": Decimal("5")}) == ZERO_USD

    def test_r28_zero_usage_prices_to_the_canonical_zero(self) -> None:
        """R47: an empty subtotal and a zero subtotal produce identical bytes."""
        rates = dict(rates_for(SHIPPED, "claude-opus-5"))
        assert format_usd(price_usage(TokenUsage(), rates)) == "0.000000"

    @pytest.mark.parametrize("model_key", sorted(SHIPPED.snapshot.models))
    def test_r28_every_shipped_model_prices_a_realistic_call_correctly(
        self, model_key: str
    ) -> None:
        """R28: the formula against every shipped rate, oracle-checked.

        A single-model arithmetic test cannot see a rate transcribed into the
        wrong key. Seventeen can.
        """
        usage = TokenUsage(
            input_tokens=1_013,
            output_tokens=2_027,
            cache_read_input_tokens=30_011,
            cache_creation_5m_tokens=4_019,
            cache_creation_1h_tokens=57,
        )
        computed = price_usage(usage, dict(rates_for(SHIPPED, model_key)))
        assert computed == oracle_for(SHIPPED, model_key, usage)
        assert computed > 0


# --- R29 ----------------------------------------------------------------------


class TestDecimalDisciplineR29:
    """R29: Decimal from load to format, one rounding, rows that add up exactly."""

    def test_r29_six_decimal_places_is_the_pinned_quantum(self) -> None:
        """R29: the constants are what the requirement says."""
        assert COST_DECIMAL_PLACES == 6
        assert Decimal("0.000001") == COST_QUANTUM
        assert Decimal("0.01") == DISPLAY_QUANTUM
        assert Decimal("0.000000") == ZERO_USD
        assert format_usd(ZERO_USD) == "0.000000"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0.0000005", "0.000001"),
            ("0.0000004", "0.000000"),
            ("0.00000049999", "0.000000"),
            ("0.0000015", "0.000002"),
            ("0.0000025", "0.000003"),
            ("1.9999995", "2.000000"),
            ("0", "0.000000"),
            ("12345.6789015", "12345.678902"),
        ],
    )
    def test_r29_quantization_is_round_half_up_not_bankers(self, value: str, expected: str) -> None:
        """R29: ``ROUND_HALF_UP``. ``0.0000025`` is where banker's rounding differs."""
        assert format_usd(Decimal(value)) == expected

    def test_r29_the_display_total_is_quantized_from_the_six_decimal_total(self) -> None:
        """R29: two decimals for the grand total only, from the six-decimal value."""
        assert quantize_display(Decimal("0.005000")) == Decimal("0.01")
        assert quantize_display(Decimal("0.004999")) == Decimal("0.00")
        assert format_display_usd(Decimal("1.239999")) == "1.24"
        assert format_display_usd(Decimal("0")) == "0.00"

    def test_r29_a_formatted_value_is_never_scientific_notation(self) -> None:
        """R47: fixed point only, whatever the exponent of the input."""
        for value in ("1E-8", "1E+12", "0.0000001", "1000000000000"):
            rendered = format_usd(Decimal(value))
            assert "E" not in rendered and "e" not in rendered
            assert rendered.count(".") == 1
            assert len(rendered.split(".")[1]) == COST_DECIMAL_PLACES

    def test_r29_rows_sum_to_their_total_exactly_because_both_are_quantized(self) -> None:
        """R29: totals are sums of already-quantized costs, so no drift accumulates.

        Three rates chosen to land a third of a micro-dollar on each row: summing
        the *unquantized* values and quantizing once gives a different answer, so
        this case can tell the two designs apart.
        """
        rates = {"input": Decimal("1")}
        rows = [price_usage(TokenUsage(input_tokens=n), rates) for n in (1, 1, 1)]
        assert rows == [Decimal("0.000001")] * 3
        assert sum_usd(rows) == Decimal("0.000003")
        unquantized_once = quantize_cost(Decimal(3) * Decimal("0.000001"))
        assert sum_usd(rows) == unquantized_once

    def test_r29_sum_usd_of_nothing_is_the_canonical_zero(self) -> None:
        """R47: an empty group and a group summing to nothing render identically."""
        assert format_usd(sum_usd([])) == "0.000000"
        assert format_usd(sum_usd([ZERO_USD])) == "0.000000"

    def test_r29_no_float_reaches_the_computation(self) -> None:
        """R29: the arithmetic is exact where a float would already be wrong.

        ``0.1 + 0.2 != 0.3`` in binary floating point. The same three rates as
        Decimals must sum exactly, and a 0.1-rate call must price exactly.
        """
        assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
        assert 0.1 + 0.2 != 0.3
        priced = price_usage(TokenUsage(cache_read_input_tokens=3), {"cache_read": Decimal("0.1")})
        assert priced == Decimal("0.000000")
        big = price_usage(
            TokenUsage(cache_read_input_tokens=3_000_000), {"cache_read": Decimal("0.1")}
        )
        assert big == Decimal("0.300000")

    def test_r29_sum_usage_is_element_wise_and_exact_at_large_counts(self) -> None:
        """R17/R31: token subtotals are integers and do not overflow into floats."""
        big = TokenUsage(input_tokens=2**62, output_tokens=1)
        total = sum_usage([big, big])
        assert total.input_tokens == 2**63
        assert total.output_tokens == 2
        assert sum_usage([]) == TokenUsage()


class TestInexactTrapR29:
    """R29: "nothing rounds except where the spec says" is a check, not a claim."""

    def test_r29_the_trap_does_not_fire_on_a_trillion_token_call(self) -> None:
        """R29: a guard that fires on honest input is a guard people delete."""
        usage = TokenUsage(
            input_tokens=10**12,
            output_tokens=10**12,
            cache_read_input_tokens=10**12,
            cache_creation_5m_tokens=10**12,
            cache_creation_1h_tokens=10**12,
        )
        for model_key in sorted(SHIPPED.snapshot.models):
            computed = price_usage(usage, dict(rates_for(SHIPPED, model_key)))
            assert computed == oracle_for(SHIPPED, model_key, usage)

    def test_r29_the_trap_does_not_fire_on_a_two_million_value_sum(self) -> None:
        """R29: R11 allows 2,000,000 records; adding that many must stay exact."""
        values = [Decimal("0.123457")] * 2_000_000
        assert sum_usd(values) == Decimal("246914.000000")

    def test_r29_the_trap_fires_on_a_token_count_with_too_many_digits(self) -> None:
        """R29: a corrupt trace is refused rather than priced approximately."""
        corrupt = TokenUsage(input_tokens=int("1" * (COST_PRECISION + 1)))
        with pytest.raises(CostError) as caught:
            price_usage(corrupt, {"input": Decimal("3")})
        assert caught.value.code == "cost_precision_exceeded"
        assert caught.value.cli_line.startswith("swarm-observer: cost_precision_exceeded")

    def test_r29_the_honest_side_of_that_boundary_still_prices(self) -> None:
        """R29: one digit fewer is priced exactly — the trap's non-vacuous arm."""
        honest = TokenUsage(input_tokens=int("1" * COST_PRECISION))
        assert price_usage(honest, {"input": Decimal("3")}) == oracle_cost(
            honest, {"input": Fraction(3)}
        )

    def test_r29_a_cost_error_is_a_single_sanitized_line_naming_a_span(self) -> None:
        """R11's posture applied to a pricing failure."""
        error = CostError("cost_precision_exceeded", seq=17)
        assert error.cli_line == "swarm-observer: cost_precision_exceeded: span 17"
        assert "\n" not in error.cli_line
        with pytest.raises(ValueError):
            CostError("Not A Slug")

    def test_r29_compute_costs_names_the_span_whose_arithmetic_failed(self) -> None:
        """R29: "somewhere in two million spans" is not a diagnostic."""
        builder = TraceBuilder()
        builder.model_call(usage=TokenUsage(input_tokens=1))
        builder.model_call(usage=TokenUsage(input_tokens=int("7" * (COST_PRECISION + 1))))
        with pytest.raises(CostError) as caught:
            compute_costs(builder.build(), SHIPPED)
        assert caught.value.seq == 1
        assert caught.value.detail == "span 1"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-1: quantize_cost runs outside price_usage's DecimalException guard, so a "
            "token count whose *quantized* result exceeds COST_PRECISION digits raises a raw "
            "decimal.InvalidOperation instead of CostError. It escapes cli.main, printing a "
            "traceback and exiting 1 (R11, R39)."
        ),
    )
    def test_r29_an_oversized_but_exact_token_count_is_a_cost_error_not_a_crash(self) -> None:
        """R29/R11: every pricing failure is ``CostError``, never a bare Decimal signal.

        ``10**60`` has one significant digit, so no ``Inexact`` is signalled by the
        multiply or the divide; the quantize to six places then needs 61 digits
        against a 60-digit context and raises ``InvalidOperation`` from outside the
        guard.
        """
        with pytest.raises(CostError):
            price_usage(TokenUsage(input_tokens=10**60), {"input": Decimal("3")})

    def test_r29_bug1_reproduces_as_a_raw_decimal_signal_today(self) -> None:
        """R29: the defect above, pinned as it currently behaves.

        Kept beside the xfail so the bug report has a live reproduction and so
        the fix has to move *both* — a fix that turns the raw signal into a
        different bare exception would still fail here.
        """
        with pytest.raises((CostError, InvalidOperation)) as caught:
            price_usage(TokenUsage(input_tokens=10**60), {"input": Decimal("3")})
        assert isinstance(caught.value, InvalidOperation), (
            "BUG-1 appears to be fixed: delete this test and de-xfail the one above"
        )


# --- R30 ----------------------------------------------------------------------


class TestUnpricedTaxonomyR30:
    """R30: a closed enum, in a priority order that is itself the requirement."""

    def test_r30_the_enum_is_closed_and_in_priority_order(self) -> None:
        """R30: four reasons, in the order the requirement assigns them."""
        assert UNPRICED_REASONS == (
            "synthetic_span",
            "usage_missing",
            "model_not_in_snapshot",
            "rate_key_missing",
        )

    def test_r30_rung_1_a_span_with_an_error_is_synthetic_even_with_usage(self) -> None:
        """R30.1: an API-error record is never billable."""
        span = model_span(
            model="claude-sonnet-4-5",
            usage=TokenUsage(input_tokens=100),
            error=SpanError(code="rate_limit", detail="429"),
        )
        assert classify_span(span, SHIPPED) == (None, "synthetic_span", ())

    def test_r30_rung_1_the_synthetic_model_literal_is_synthetic(self) -> None:
        """R30.1: ``<synthetic>`` by name, with usage present and a live snapshot."""
        assert SYNTHETIC_MODEL == "<synthetic>"
        span = model_span(model=SYNTHETIC_MODEL, usage=TokenUsage(input_tokens=100))
        assert classify_span(span, SHIPPED) == (None, "synthetic_span", ())

    def test_r30_rung_1_beats_rung_2(self) -> None:
        """R30: an error span with ``usage is None`` reports ``synthetic_span``.

        Both rungs match. Reordering them changes the reported reason without
        changing whether the span is priced — exactly the defect a green suite
        survives.
        """
        span = model_span(
            model="claude-sonnet-4-5", usage=None, error=SpanError(code="api_error", detail="x")
        )
        reason = classify_span(span, SHIPPED)[1]
        assert reason == "synthetic_span"
        assert reason != "usage_missing"

    def test_r30_rung_1_beats_rung_3(self) -> None:
        """R30: ``<synthetic>`` is also absent from the snapshot; rung 1 wins.

        Reporting ``model_not_in_snapshot`` would put ``<synthetic>`` in a table
        of models somebody might go looking for.
        """
        assert SHIPPED.resolve_model_key(SYNTHETIC_MODEL) is None
        span = model_span(model=SYNTHETIC_MODEL, usage=TokenUsage(input_tokens=1))
        reason = classify_span(span, SHIPPED)[1]
        assert reason == "synthetic_span"
        assert reason != "model_not_in_snapshot"

    def test_r30_rung_1_beats_rung_4(self) -> None:
        """R30: an error span whose model also lacks a needed price key."""
        source = source_of({"m-1": RateEntry(input="1")})
        span = model_span(
            model="m-1",
            usage=TokenUsage(cache_creation_1h_tokens=5),
            error=SpanError(code="api_error", detail="x"),
        )
        assert classify_span(span, source)[1] == "synthetic_span"

    def test_r30_rung_2_beats_rung_3(self) -> None:
        """R30: ``usage is None`` on an unknown model reports ``usage_missing``."""
        assert SHIPPED.resolve_model_key("no-such-model") is None
        span = model_span(model="no-such-model", usage=None)
        reason = classify_span(span, SHIPPED)[1]
        assert reason == "usage_missing"
        assert reason != "model_not_in_snapshot"

    def test_r30_rung_3_beats_rung_4(self) -> None:
        """R30: an unknown model that would also miss a price key."""
        source = source_of({"m-1": RateEntry(input="1")})
        span = model_span(model="unknown", usage=TokenUsage(cache_creation_1h_tokens=5))
        model_key, reason, missing = classify_span(span, source)
        assert (model_key, reason, missing) == (None, "model_not_in_snapshot", ())

    def test_r30_rung_2_a_span_with_no_usage_is_usage_missing(self) -> None:
        """R30.2, on a model that resolves perfectly."""
        span = model_span(model="claude-sonnet-4-5", usage=None)
        assert classify_span(span, SHIPPED) == (None, "usage_missing", ())

    def test_r30_rung_3_an_unresolvable_model_is_model_not_in_snapshot(self) -> None:
        """R30.3: R27's ladder reached step (4)."""
        span = model_span(model="some-model-nobody-published", usage=TokenUsage(input_tokens=1))
        assert classify_span(span, SHIPPED) == (None, "model_not_in_snapshot", ())

    def test_r30_rung_3_a_null_model_is_model_not_in_snapshot(self) -> None:
        """R30: ``Span.model`` is optional, and a null id resolves to nothing."""
        span = model_span(model=None, usage=TokenUsage(input_tokens=1))
        assert classify_span(span, SHIPPED) == (None, "model_not_in_snapshot", ())

    def test_r30_rung_4_names_the_price_keys_the_entry_lacked(self) -> None:
        """R30.4: the reason says *which* key, as enumerated slugs."""
        source = source_of({"m-1": RateEntry(input="1", output="2")})
        span = model_span(
            model="m-1",
            usage=TokenUsage(input_tokens=1, cache_read_input_tokens=1, cache_creation_1h_tokens=1),
        )
        model_key, reason, missing = classify_span(span, source)
        assert (model_key, reason) == (None, "rate_key_missing")
        assert missing == ("cache_read", "cache_write_1h")
        assert list(missing) == sorted(missing)

    def test_r30_exactly_one_of_model_key_and_reason_is_set(self) -> None:
        """R30: the classifier's return shape, over every reachable outcome."""
        cases = [
            model_span(model="claude-sonnet-4-5", usage=TokenUsage(input_tokens=1)),
            model_span(model=SYNTHETIC_MODEL, usage=TokenUsage(input_tokens=1)),
            model_span(model="claude-sonnet-4-5", usage=None),
            model_span(model="nope", usage=TokenUsage(input_tokens=1)),
        ]
        for span in cases:
            model_key, reason, _ = classify_span(span, SHIPPED)
            assert (model_key is None) != (reason is None)
            if reason is not None:
                assert reason in UNPRICED_REASONS


class TestRateKeyMissingCarveOutR30:
    """R30: ``tokens != 0 and price_key not in rates`` — a conjunction, both halves.

    "A zero-token component never triggers ``rate_key_missing``" is the whole
    subtlety: almost every real model call has zero one-hour cache creation, so
    keying off the rate table alone would put most of a trace in the unpriced
    list.
    """

    PARTIAL = RateEntry(input="1", output="2", cache_read="3", cache_write_5m="4")

    def test_r30_a_zero_component_with_no_rate_does_not_trigger_it(self) -> None:
        """R30: half one — the zero-token side."""
        source = source_of({"m-1": self.PARTIAL})
        usage = TokenUsage(input_tokens=500, cache_creation_1h_tokens=0)
        assert missing_price_keys(usage, rates_for(source, "m-1")) == ()
        assert classify_span(model_span(model="m-1", usage=usage), source) == ("m-1", None, ())

    def test_r30_one_token_of_the_same_component_does_trigger_it(self) -> None:
        """R30: half two — the same span, one token apart."""
        source = source_of({"m-1": self.PARTIAL})
        usage = TokenUsage(input_tokens=500, cache_creation_1h_tokens=1)
        assert missing_price_keys(usage, rates_for(source, "m-1")) == ("cache_write_1h",)
        assert classify_span(model_span(model="m-1", usage=usage), source)[1] == "rate_key_missing"

    def test_r30_a_non_zero_component_whose_rate_is_present_does_not_trigger_it(self) -> None:
        """R30: the other half of the conjunction — the rate is there."""
        source = source_of({"m-1": self.PARTIAL})
        usage = TokenUsage(cache_creation_5m_tokens=1)
        assert missing_price_keys(usage, rates_for(source, "m-1")) == ()

    def test_r30_a_rate_of_zero_is_present_and_does_not_trigger_it(self) -> None:
        """R30: ``0`` is a price, not an absence. ``in`` not truthiness."""
        source = source_of({"m-1": RateEntry(input="1", cache_write_1h="0")})
        usage = TokenUsage(input_tokens=1, cache_creation_1h_tokens=1_000_000)
        assert missing_price_keys(usage, rates_for(source, "m-1")) == ()
        model_key, reason, _ = classify_span(model_span(model="m-1", usage=usage), source)
        assert (model_key, reason) == ("m-1", None)
        assert price_usage(usage, dict(rates_for(source, "m-1"))) == Decimal("0.000001")

    def test_r30_an_all_zero_usage_on_an_entry_with_no_rates_is_priced_at_zero(self) -> None:
        """R30: no non-zero component means nothing is required, so it prices."""
        source = source_of({"m-1": RateEntry()})
        span = model_span(model="m-1", usage=TokenUsage())
        assert classify_span(span, source) == ("m-1", None, ())
        assert price_usage(TokenUsage(), dict(rates_for(source, "m-1"))) == ZERO_USD

    @pytest.mark.parametrize(("component", "price_key"), USAGE_PRICE_KEYS)
    def test_r30_every_component_can_trigger_it_alone(self, component: str, price_key: str) -> None:
        """R30: the carve-out is per component, not special-cased to one of them."""
        entry = RateEntry(**{key: "1" for key in PRICE_KEYS if key != price_key})
        source = source_of({"m-1": entry})
        assert missing_price_keys(TokenUsage(**{component: 1}), rates_for(source, "m-1")) == (
            price_key,
        )
        assert missing_price_keys(TokenUsage(**{component: 0}), rates_for(source, "m-1")) == ()


# --- R31 ----------------------------------------------------------------------


def priced_trace() -> Trace:
    """Two agents, four priced model calls on three model keys, one unpriced."""
    builder = TraceBuilder()
    builder.model_call(
        agent_id="root",
        model="claude-sonnet-4-5-20250929",
        usage=TokenUsage(input_tokens=10, output_tokens=483, cache_creation_5m_tokens=17_971),
    )
    builder.model_call(
        agent_id="root", model="claude-haiku-4-5-20251001", usage=TokenUsage(input_tokens=1_000)
    )
    builder.model_call(agent_id="sub", model="claude-opus-5", usage=TokenUsage(output_tokens=2_000))
    builder.model_call(
        agent_id="sub", model="claude-haiku-4-5", usage=TokenUsage(cache_read_input_tokens=9_999)
    )
    builder.model_call(agent_id="sub", model="nowhere-model", usage=TokenUsage(input_tokens=7))
    builder.tool_call(agent_id="root")
    return builder.build()


class TestAggregationR31:
    """R31: four groupings, each summing exactly to its members."""

    def test_r31_the_four_groupings_exist_with_their_pinned_orders(self) -> None:
        """R31: whole trace, per agent by index, per model key, per detector by slug."""
        report = compute_costs(priced_trace(), SHIPPED)
        assert [row.agent_index for row in report.by_agent] == sorted(
            row.agent_index for row in report.by_agent
        )
        assert [row.agent_id for row in report.by_agent] == ["root", "sub"]
        assert [row.model_key for row in report.by_model] == sorted(
            row.model_key for row in report.by_model
        )
        assert [row.detector for row in report.by_detector] == sorted(
            row.detector for row in report.by_detector
        )

    def test_r31_each_grouping_sums_to_the_trace_total(self) -> None:
        """R31: the property that makes the groupings comparable."""
        report = compute_costs(priced_trace(), SHIPPED)
        assert sum_usd(row.cost_usd for row in report.spans) == report.total_cost_usd
        assert sum_usd(row.cost_usd for row in report.by_agent) == report.total_cost_usd
        assert sum_usd(row.cost_usd for row in report.by_model) == report.total_cost_usd
        assert report.total_cost_usd > 0

    def test_r31_the_trace_total_equals_the_oracle_over_every_priced_span(self) -> None:
        """R28/R29/R31: the headline number, computed from the snapshot."""
        trace = priced_trace()
        report = compute_costs(trace, SHIPPED)
        expected = ZERO_USD
        for span in trace.spans:
            if span.kind != "model_call" or span.usage is None:
                continue
            model_key = SHIPPED.resolve_model_key(span.model)
            if model_key is None:
                continue
            expected += oracle_for(SHIPPED, model_key, span.usage)
        assert report.total_cost_usd == expected

    def test_r31_token_subtotals_match_their_groupings(self) -> None:
        """R31: usage subtotals describe the same spans as the dollar subtotals."""
        report = compute_costs(priced_trace(), SHIPPED)
        assert sum_usage(row.usage for row in report.spans) == report.total_usage
        assert sum_usage(row.usage for row in report.by_agent) == report.total_usage
        assert sum_usage(row.usage for row in report.by_model) == report.total_usage

    def test_r31_an_agent_that_made_no_model_call_still_gets_a_row(self) -> None:
        """R31: a zero row is information; a missing row is a hole."""
        builder = TraceBuilder()
        builder.model_call(agent_id="root", usage=TokenUsage(input_tokens=10))
        builder.tool_call(agent_id="quiet")
        report = compute_costs(builder.build(), SHIPPED)
        rows = {row.agent_id: row for row in report.by_agent}
        assert set(rows) == {"root", "quiet"}
        assert rows["quiet"].priced_spans == 0
        assert format_usd(rows["quiet"].cost_usd) == "0.000000"

    def test_r31_by_model_groups_by_the_resolved_key_not_the_recorded_id(self) -> None:
        """R27/R31: two dated ids of one model are one row."""
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5-20251001", usage=TokenUsage(input_tokens=1_000))
        builder.model_call(model="claude-haiku-4-5-20260101", usage=TokenUsage(input_tokens=2_000))
        report = compute_costs(builder.build(), SHIPPED)
        assert [row.model_key for row in report.by_model] == ["claude-haiku-4-5"]
        assert report.by_model[0].priced_spans == 2

    def test_r31_no_model_call_span_is_silently_omitted(self) -> None:
        """R30: every ``model_call`` is priced or in ``unpriced``, and never both."""
        trace = priced_trace()
        report = compute_costs(trace, SHIPPED)
        model_calls = {span.seq for span in trace.spans if span.kind == "model_call"}
        priced = {row.seq for row in report.spans}
        unpriced = {row.seq for row in report.unpriced}
        assert priced | unpriced == model_calls
        assert priced & unpriced == set()
        assert report.priced_spans == len(priced)
        assert report.unpriced_spans == len(unpriced)

    def test_r31_non_model_call_spans_are_not_priced(self) -> None:
        """R28: the formula applies to ``model_call`` spans and nothing else."""
        builder = TraceBuilder()
        tool = builder.tool_call()
        builder.replace(tool, usage=TokenUsage(input_tokens=10**6))
        builder.user_message()
        builder.system_event()
        report = compute_costs(builder.build(), SHIPPED)
        assert report.spans == () and report.unpriced == ()
        assert report.total_cost_usd == ZERO_USD

    def test_r31_unpriced_usage_is_reported_apart_from_priced_usage(self) -> None:
        """R31: token totals stay complete where dollar totals cannot be."""
        trace = priced_trace()
        report = compute_costs(trace, SHIPPED)
        assert report.unpriced_usage == TokenUsage(input_tokens=7)
        assert report.total_usage.input_tokens == 10 + 1_000

    def test_r31_cost_of_finds_a_priced_span_and_returns_none_otherwise(self) -> None:
        """R31: the per-span lookup a renderer uses."""
        trace = priced_trace()
        report = compute_costs(trace, SHIPPED)
        assert report.cost_of(0) == report.spans[0].cost_usd
        assert report.cost_of(4) is None
        assert report.cost_of(999) is None

    def test_r31_the_report_carries_the_snapshot_meta(self) -> None:
        """R31: version, date and currency travel with the figures."""
        report = compute_costs(priced_trace(), SHIPPED)
        assert report.meta == SHIPPED.meta
        assert report.meta.currency == "USD"

    def test_r31_the_two_caveats_are_single_definitions(self) -> None:
        """A3/A4: both reports read one sentence each, not one per renderer."""
        assert "attribution" in WASTE_CAVEAT and "counterfactual" in WASTE_CAVEAT
        assert "list-price" in RATE_CAVEAT and "snapshot date" in RATE_CAVEAT

    def test_r31_a_cost_report_is_frozen_and_refuses_unknown_fields(self) -> None:
        """R31: the result models obey the package's frozen/extra-forbid rule."""
        report = compute_costs(priced_trace(), SHIPPED)
        with pytest.raises(ValidationError):
            report.total_cost_usd = Decimal("1")  # type: ignore[misc]
        with pytest.raises(ValidationError):
            CostReport(meta=SHIPPED.meta, surprise=1)  # type: ignore[call-arg]


class TestWasteCostR31:
    """R17/R29/R31: a finding's ``wasted_cost_usd``, and the per-detector grouping."""

    def waste_trace(self) -> Trace:
        """Two priced model calls and one on a model the snapshot cannot price."""
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=2_000))
        builder.model_call(model="nowhere-model", usage=TokenUsage(input_tokens=4_000))
        builder.tool_call()
        return builder.build()

    def test_r31_all_attributed_calls_priced_gives_a_sum(self) -> None:
        """R29: the sum of already-quantized span costs."""
        trace = self.waste_trace()
        report = compute_costs(
            trace, SHIPPED, waste_seqs={"repeated_tool_call:abcdef012345": (0, 1)}
        )
        expected = oracle_for(
            SHIPPED, "claude-haiku-4-5", TokenUsage(input_tokens=1_000)
        ) + oracle_for(SHIPPED, "claude-haiku-4-5", TokenUsage(input_tokens=2_000))
        assert report.waste_by_finding["repeated_tool_call:abcdef012345"] == expected

    def test_r31_one_unpriceable_attributed_call_makes_the_cost_none(self) -> None:
        """A-c4: an unknown cost is ``None``, not a quietly partial sum."""
        trace = self.waste_trace()
        report = compute_costs(trace, SHIPPED, waste_seqs={"agent_loop:abcdef012345": (0, 2)})
        assert report.waste_by_finding["agent_loop:abcdef012345"] is None

    def test_r31_attributing_nothing_gives_zero_not_none(self) -> None:
        """R31: "nothing was attributed" and "the cost is unknown" differ."""
        trace = self.waste_trace()
        report = compute_costs(trace, SHIPPED, waste_seqs={"retry_storm:abcdef012345": ()})
        assert report.waste_by_finding["retry_storm:abcdef012345"] == ZERO_USD

    def test_r31_only_model_calls_with_usage_are_relevant(self) -> None:
        """R17: the cost engine applies the same filter the token attribution does."""
        trace = self.waste_trace()
        report = compute_costs(
            trace, SHIPPED, waste_seqs={"retry_storm:abcdef012345": (0, 3, 999, -1)}
        )
        assert report.waste_by_finding["retry_storm:abcdef012345"] == oracle_for(
            SHIPPED, "claude-haiku-4-5", TokenUsage(input_tokens=1_000)
        )

    def test_r31_wasted_tokens_and_wasted_cost_describe_the_same_spans(self) -> None:
        """R17/R29: ``wasted`` is zero exactly when the relevant set is empty."""
        trace = self.waste_trace()
        for seqs in ((), (3,), (0,), (0, 1), (0, 3), (2,)):
            key = "repeated_tool_call:abcdef012345"
            report = compute_costs(trace, SHIPPED, waste_seqs={key: seqs})
            tokens = attribute_waste(trace, seqs)
            cost = report.waste_by_finding[key]
            if tokens == TokenUsage():
                assert cost == ZERO_USD
            else:
                assert cost is None or cost > 0

    def test_r31_by_detector_deduplicates_within_a_detector(self) -> None:
        """R31: a detector cannot double-count its own evidence."""
        trace = self.waste_trace()
        report = compute_costs(
            trace,
            SHIPPED,
            waste_seqs={
                "repeated_tool_call:aaaaaaaaaaaa": (0, 1),
                "repeated_tool_call:bbbbbbbbbbbb": (1,),
            },
        )
        row = {r.detector: r for r in report.by_detector}["repeated_tool_call"]
        assert row.findings == 2
        assert row.findings_unpriced == 0
        assert row.wasted == TokenUsage(input_tokens=3_000)
        assert row.wasted_cost_usd == oracle_for(
            SHIPPED, "claude-haiku-4-5", TokenUsage(input_tokens=1_000)
        ) + oracle_for(SHIPPED, "claude-haiku-4-5", TokenUsage(input_tokens=2_000))

    def test_r31_by_detector_counts_findings_whose_cost_is_unknown(self) -> None:
        """A-c4: a per-detector zero is legible because the count sits beside it."""
        trace = self.waste_trace()
        report = compute_costs(trace, SHIPPED, waste_seqs={"agent_loop:aaaaaaaaaaaa": (2,)})
        row = {r.detector: r for r in report.by_detector}["agent_loop"]
        assert row.findings == 1
        assert row.findings_unpriced == 1
        assert row.wasted_cost_usd == ZERO_USD

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-4: DetectorWaste.wasted sums only the *priced* attributed spans, so the "
            "per-detector token column contradicts R17's Finding.wasted for the same "
            "findings. AgentCost.usage documents that restriction and has unpriced_usage "
            "beside it; DetectorWaste has neither."
        ),
    )
    def test_r31_by_detector_wasted_tokens_equal_the_findings_it_aggregates(self) -> None:
        """R17/R31: the per-detector token total is what its findings reported.

        One finding attributing a priced span and an unpriceable one. R17 defines
        ``wasted`` as the element-wise sum over the distinct redundant model calls
        with no reference to whether a rate exists, so the detector row must say
        3,000 + 4,000 tokens — not 3,000 with the rest silently dropped beside a
        dollar figure that names neither.
        """
        trace = self.waste_trace()
        key = "agent_loop:aaaaaaaaaaaa"
        report = compute_costs(trace, SHIPPED, waste_seqs={key: (1, 2)})
        row = {r.detector: r for r in report.by_detector}["agent_loop"]
        assert row.wasted == attribute_waste(trace, (1, 2))

    def test_r31_bug4_reproduces_as_a_priced_only_token_column_today(self) -> None:
        """R17/R31: BUG-4 pinned as it currently behaves, beside its xfail.

        The two numbers a reader would compare disagree, and nothing in the
        document says the second is a subset of the first.
        """
        trace = self.waste_trace()
        key = "agent_loop:aaaaaaaaaaaa"
        report = compute_costs(trace, SHIPPED, waste_seqs={key: (1, 2)})
        row = {r.detector: r for r in report.by_detector}["agent_loop"]
        assert attribute_waste(trace, (1, 2)) == TokenUsage(input_tokens=6_000)
        assert row.wasted == TokenUsage(input_tokens=2_000), (
            "BUG-4 appears to be fixed: delete this test and de-xfail the one above"
        )
        assert row.wasted_cost_usd > ZERO_USD

    def test_r31_the_same_span_attributed_by_two_detectors_counts_in_both(self) -> None:
        """A7: overlap is intentional; the number is an attribution, not a bill."""
        trace = self.waste_trace()
        report = compute_costs(
            trace,
            SHIPPED,
            waste_seqs={
                "repeated_tool_call:aaaaaaaaaaaa": (0,),
                "agent_loop:bbbbbbbbbbbb": (0,),
            },
        )
        rows = {r.detector: r for r in report.by_detector}
        one_span = oracle_for(SHIPPED, "claude-haiku-4-5", TokenUsage(input_tokens=1_000))
        assert rows["repeated_tool_call"].wasted_cost_usd == one_span
        assert rows["agent_loop"].wasted_cost_usd == one_span
        # The same tokens under two readings: the per-detector column deliberately
        # sums to more than the span cost, and is not a bill.
        assert sum_usd(r.wasted_cost_usd for r in report.by_detector) == one_span * 2

    def test_r31_a_detector_that_fired_nothing_has_no_row(self) -> None:
        """A-c15: ``cost/`` cannot import the registry, so it reports what it was given."""
        report = compute_costs(self.waste_trace(), SHIPPED, waste_seqs={})
        assert report.by_detector == ()

    def test_r31_a_waste_key_that_is_not_a_finding_id_is_refused(self) -> None:
        """R15: the slug is read off the key, so the key's shape is validated."""
        trace = self.waste_trace()
        for bad in ("not-a-finding-id", "Detector:abcdef012345", "d:XYZ", "d:abcdef01234", ""):
            with pytest.raises(ValueError):
                compute_costs(trace, SHIPPED, waste_seqs={bad: (0,)})

    def test_r31_the_pipeline_attribution_prices_the_real_corpus(self) -> None:
        """R17/R29: the detectors' own attributions, priced end to end."""
        seen = set()
        for path in fixture_paths():
            trace = load_trace(path)
            run = run_detectors_with_waste(trace, DetectorConfig())
            report = compute_costs(trace, SHIPPED, waste_seqs=run.waste_seqs)
            assert set(report.waste_by_finding) == {f.finding_id for f in run.findings}
            for finding in run.findings:
                cost = report.waste_by_finding[finding.finding_id]
                seen.add(cost is None)
                if finding.wasted == TokenUsage():
                    assert cost == ZERO_USD, finding.finding_id
        assert seen, "the corpus produced no findings at all"


class TestMutationGapsR28R29R30R31:
    """Cases the mutation sweep found nothing asserting.

    Every test here corresponds to a mutant that survived the first sweep of
    this branch. They are grouped rather than scattered so the next sweep can
    see what the last one bought, and each names its mutant id.
    """

    def test_r29_the_canonical_zero_keeps_its_exponent_through_a_sum(self) -> None:
        """R29 (C04, C11): ``sum_usd`` starts from ``ZERO_USD``, not ``Decimal(0)``.

        The module's own claim is that an empty sum "carries the same exponent as
        a non-empty one and formats identically". ``Decimal`` equality ignores
        the exponent, so ``== Decimal("0.000000")`` cannot see the difference;
        ``str`` can.
        """
        assert str(ZERO_USD) == "0.000000"
        assert str(sum_usd([])) == "0.000000"
        assert str(sum_usd([Decimal("1.5")])) == "1.500000"
        assert str(sum_usd([Decimal("0.5"), Decimal("0.25")])) == "0.750000"

    def test_r29_the_precision_constant_is_sixty(self) -> None:
        """R29 (C06): pinned as a literal, not read back from the module.

        The trap tests below build their token counts from ``COST_PRECISION``,
        which makes them adapt to a changed constant and therefore blind to it —
        a self-referential check. The literal is asserted here and used there.
        """
        assert COST_PRECISION == 60

    def test_r29_a_sixty_digit_token_count_prices_and_a_sixty_one_digit_one_does_not(
        self,
    ) -> None:
        """R29 (C06): the boundary at absolute digit counts, not relative ones."""
        sixty = TokenUsage(input_tokens=int("1" * 60))
        sixty_one = TokenUsage(input_tokens=int("1" * 61))
        assert price_usage(sixty, {"input": Decimal("3")}) == oracle_cost(
            sixty, {"input": Fraction(3)}
        )
        with pytest.raises(CostError):
            price_usage(sixty_one, {"input": Decimal("3")})

    def test_r28_a_non_zero_component_with_no_rate_contributes_nothing(self) -> None:
        """R28 (C12, C13): "a component whose rate is absent contributes nothing".

        Both halves of ``rate is None or tokens == 0`` matter. With ``and``, or
        with the rate check dropped, this call multiplies an ``int`` by ``None``.
        R30's ``rate_key_missing`` is what stops the *engine* reaching here, but
        ``price_usage`` is a public function and must not depend on its caller.
        """
        assert price_usage(TokenUsage(input_tokens=5), {}) == ZERO_USD
        assert price_usage(
            TokenUsage(input_tokens=5, output_tokens=7), {"output": Decimal("15")}
        ) == oracle_cost(TokenUsage(output_tokens=7), {"output": Fraction(15)})

    def test_r30_missing_price_keys_are_sorted_not_in_formula_order(self) -> None:
        """R30 (C20): a case where the two orders differ.

        ``USAGE_PRICE_KEYS`` runs ``output`` before ``cache_read``; sorted runs
        ``cache_read`` first. The earlier test's pair happened to agree.
        """
        source = source_of({"m-1": RateEntry(input="1")})
        usage = TokenUsage(output_tokens=1, cache_read_input_tokens=1)
        missing = missing_price_keys(usage, rates_for(source, "m-1"))
        assert missing == ("cache_read", "output")
        assert [price for _, price in USAGE_PRICE_KEYS].index("output") < [
            price for _, price in USAGE_PRICE_KEYS
        ].index("cache_read"), "the two orders no longer differ; pick another pair"

    def test_r17_an_attributed_seq_one_past_the_end_is_ignored(self) -> None:
        """R17 (C26): ``seq >= len(spans)``, at exactly ``len(spans)``.

        The earlier test handed in 999 and -1, which both fail either form of the
        bound. Exactly one past the end is the only value that tells them apart,
        and the wrong form is an ``IndexError`` out of the cost engine.
        """
        trace = TraceBuilder()
        trace.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        built = trace.build()
        key = "repeated_tool_call:abcdef012345"
        report = compute_costs(built, SHIPPED, waste_seqs={key: (len(built.spans),)})
        assert report.waste_by_finding[key] == ZERO_USD

    def test_r17_an_attributed_model_call_with_no_usage_is_not_relevant(self) -> None:
        """R17 (C27): the filter is ``model_call`` **and** ``usage is not None``.

        A model call with no usage contributes no tokens, so it must not make the
        finding's cost *unknown* either — dropping the usage half turns a
        ``0.000000`` into a ``None``.
        """
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=None)
        built = builder.build()
        key = "agent_loop:abcdef012345"
        report = compute_costs(built, SHIPPED, waste_seqs={key: (0,)})
        assert report.waste_by_finding[key] == ZERO_USD
        assert report.waste_by_finding[key] is not None

    def test_r17_a_seq_attributed_twice_is_counted_once(self) -> None:
        """R17 (C28): "distinct" model calls — ``sorted(set(seqs))``."""
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        built = builder.build()
        key = "repeated_tool_call:abcdef012345"
        once = compute_costs(built, SHIPPED, waste_seqs={key: (0,)})
        twice = compute_costs(built, SHIPPED, waste_seqs={key: (0, 0, 0)})
        assert twice.waste_by_finding[key] == once.waste_by_finding[key]
        assert twice.by_detector[0].wasted == once.by_detector[0].wasted

    def test_r31_by_detector_rows_are_sorted_by_slug(self) -> None:
        """R31 (C31): six slugs, so set order cannot pass for sorted by accident.

        ``list(set(...))`` and ``sorted(...)`` agree on one element and agree by
        chance on a few; with six they agree for one permutation in 720. That is
        the honest strength of this kill and it is stated rather than implied.
        """
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        built = builder.build()
        slugs = ["zeta_d", "mike_d", "alpha_d", "romeo_d", "delta_d", "kilo_d"]
        report = compute_costs(
            built,
            SHIPPED,
            waste_seqs={f"{slug}:abcdef01234{index}": (0,) for index, slug in enumerate(slugs)},
        )
        assert [row.detector for row in report.by_detector] == sorted(slugs)

    def test_r31_two_undeclared_agents_get_distinct_non_negative_indexes(self) -> None:
        """R31 (C33): the undeclared-agent fallback, with two of them.

        One undeclared agent gets index 0 under either arithmetic. Two is the
        first case that can tell ``len(index_of) + offset`` from
        ``len(index_of) - offset``.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="bravo", model="claude-haiku-4-5", usage=TokenUsage())
        builder.model_call(agent_id="alpha", model="claude-haiku-4-5", usage=TokenUsage())
        built = builder.build().model_copy(update={"agents": ()})
        report = compute_costs(built, SHIPPED)
        assert [(row.agent_id, row.agent_index) for row in report.by_agent] == [
            ("alpha", 0),
            ("bravo", 1),
        ]

    def test_r47_waste_by_finding_is_built_in_sorted_key_order(self) -> None:
        """R47 (mutation C32): the mapping's *insertion* order is normalized.

        ``render_json`` writes with ``sort_keys=True``, so today nothing in the
        report can see this dict's order and dropping the ``sorted`` left the
        suite green. It is still the difference between a report whose bytes are
        a function of the trace and one that inherits the order the CLI happened
        to build the waste mapping in — R47's property, one layer below where
        R47 is currently asserted. The day a renderer iterates this mapping
        (increment 4's per-finding waste column is the obvious candidate) the
        normalization is the only thing standing between it and detector
        registration order.

        The input is handed in reversed so insertion order and sorted order
        differ, and that premise is asserted rather than assumed.
        """
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        built = builder.build()
        ids = [
            "retry_storm:ffffffffffff",
            "agent_loop:aaaaaaaaaaaa",
            "repeated_tool_call:555555555555",
            "blocked_agent:000000000000",
        ]
        handed_in = {finding_id: (0,) for finding_id in ids}
        assert list(handed_in) == ids, "the mapping is not in insertion order"
        assert list(handed_in) != sorted(ids), "insertion order already equals sorted order"

        report = compute_costs(built, SHIPPED, waste_seqs=handed_in)
        assert list(report.waste_by_finding) == sorted(ids)


class TestWaveTwoGapsR30R31:
    """R30/R31 rows the second mutation sweep found nothing asserting.

    Wave 2 of the sweep was designed over anchors wave 1 never touched. Four of
    its survivors live in ``_by_agent`` and ``compute_costs``, and all four hide
    behind the same thing: on the checked-in corpus every agent is declared and
    every agent has both priced and unpriced spans, so two different expressions
    for a row's contents agree everywhere. Each test below builds the trace
    where they stop agreeing.
    """

    def test_r31_an_agent_known_only_from_an_unpriced_span_still_gets_a_row(self) -> None:
        """R31 (mutation W-C06): the ``| unpriced`` half of the agent set.

        R30's "nothing is silently omitted" applies to the cost section as a
        whole. An agent whose every model call is unpriced is exactly the agent
        a reader most wants to see, and it is invisible to the priced set. The
        trace declares no ``AgentRun``, so ``trace.agents`` cannot supply it
        either — the row exists only if the unpriced list is consulted.
        """
        builder = TraceBuilder()
        builder.model_call(
            agent_id="priced_only", model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000)
        )
        builder.model_call(
            agent_id="unpriced_only", model="nothing-published", usage=TokenUsage(input_tokens=9)
        )
        trace = builder.build().model_copy(update={"agents": ()})
        report = compute_costs(trace, SHIPPED)
        rows = {row.agent_id: row for row in report.by_agent}
        assert set(rows) == {"priced_only", "unpriced_only"}
        assert rows["unpriced_only"].priced_spans == 0
        assert rows["unpriced_only"].unpriced_spans == 1
        assert rows["unpriced_only"].cost_usd == ZERO_USD

    def test_r31_each_agents_unpriced_count_is_its_own_not_the_traces(self) -> None:
        """R31 (mutation W-C07): two agents with *different* unpriced counts.

        With one agent, or with two agents holding one unpriced span each,
        ``len([item for item in unpriced if item.agent_id == agent_id])`` and
        ``len(unpriced)`` are the same number. Here they are 1 and 2 against a
        trace total of 3.
        """
        builder = TraceBuilder()
        builder.model_call(agent_id="a", model="nothing-published", usage=TokenUsage())
        builder.model_call(agent_id="b", model="nothing-published", usage=TokenUsage())
        builder.model_call(agent_id="b", model="also-nothing", usage=TokenUsage())
        report = compute_costs(builder.build(), SHIPPED)
        rows = {row.agent_id: row.unpriced_spans for row in report.by_agent}
        assert rows == {"a": 1, "b": 2}
        assert report.unpriced_spans == 3
        assert len(set(rows.values())) > 1, "vacuous: the per-agent counts are all equal"

    def test_r30_an_unpriced_span_with_no_recorded_model_reports_an_empty_string(self) -> None:
        """R30 (mutation W-C10): the default for a ``None`` model is ``""``.

        R30 puts the *recorded* model in the unpriced table. A span with no
        recorded model has nothing to show, and the field is a string, so the
        honest value is the empty one. A substituted placeholder like
        ``"<none>"`` would be this package inventing a model id that looks like
        the enumerated ``<synthetic>`` slug the very next reason uses — two
        different meanings wearing the same shape.
        """
        builder = TraceBuilder()
        builder.model_call(model=None, usage=TokenUsage(input_tokens=1))
        report = compute_costs(builder.build(), SHIPPED)
        assert len(report.unpriced) == 1
        row = report.unpriced[0]
        assert row.model == ""
        assert row.reason == "model_not_in_snapshot"
        # The contrast that gives the empty string its meaning: a *recorded*
        # model is carried through verbatim.
        builder = TraceBuilder()
        builder.model_call(model="nothing-published", usage=TokenUsage(input_tokens=1))
        assert compute_costs(builder.build(), SHIPPED).unpriced[0].model == "nothing-published"

    @pytest.mark.parametrize(
        "bad_key",
        [
            "not-a-finding-id",
            "repeated_tool_call",
            "repeated_tool_call:",
            "repeated_tool_call:XYZXYZXYZXYZ",
            "repeated_tool_call:abcdef01234",
            "repeated_tool_call:abcdef0123456",
            "Repeated_Tool_Call:abcdef012345",
            "",
            "1repeated:abcdef012345",
        ],
    )
    def test_r15_a_waste_key_that_is_not_a_finding_id_is_refused(self, bad_key: str) -> None:
        """R15/R29: a malformed waste key is refused, not turned into a detector.

        ``_by_detector`` derives a detector slug by splitting the key on its
        colon, so a key that is not a finding id would otherwise invent a
        detector row and the report would grow a section for a detector that
        does not exist.

        Mutant W-C11 — deleting ``compute_costs``'s own up-front validation
        loop — **survives this test, and is an equivalent mutant.** Verified
        rather than assumed: with the loop gone, every key in this
        parametrization still raises the same ``ValueError`` with the same
        message, because ``_by_detector`` calls ``_detector_of`` on every key
        anyway. The up-front loop is fail-fast readability, not a behavioural
        guarantee, and its comment ("fail loudly on a key that is not a finding
        id") describes something the code would do without it. Recorded in the
        test report as a survivor with a reason rather than papered over.
        """
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        with pytest.raises(ValueError):
            compute_costs(builder.build(), SHIPPED, waste_seqs={bad_key: (0,)})

    def test_r15_a_well_formed_waste_key_is_accepted(self) -> None:
        """R15: the arm that keeps the test above from being satisfied by
        ``compute_costs`` raising on everything."""
        builder = TraceBuilder()
        builder.model_call(model="claude-haiku-4-5", usage=TokenUsage(input_tokens=1_000))
        report = compute_costs(
            builder.build(), SHIPPED, waste_seqs={"repeated_tool_call:abcdef012345": (0,)}
        )
        assert report.waste_by_finding["repeated_tool_call:abcdef012345"] > ZERO_USD


class TestCorpusInvariantsR31:
    """R30/R31 over every checked-in fixture, not only over a hand-built trace."""

    @pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.stem)
    def test_r31_every_fixture_satisfies_the_grouping_invariants(self, path: Path) -> None:
        """R30/R31: rows sum to totals and no model call is dropped, corpus-wide."""
        trace = load_trace(path)
        run = run_detectors_with_waste(trace, DetectorConfig())
        report = compute_costs(trace, SHIPPED, waste_seqs=run.waste_seqs)
        assert sum_usd(row.cost_usd for row in report.spans) == report.total_cost_usd
        assert sum_usd(row.cost_usd for row in report.by_agent) == report.total_cost_usd
        assert sum_usd(row.cost_usd for row in report.by_model) == report.total_cost_usd
        model_calls = {span.seq for span in trace.spans if span.kind == "model_call"}
        priced = {row.seq for row in report.spans}
        unpriced = {row.seq for row in report.unpriced}
        assert priced | unpriced == model_calls
        assert priced & unpriced == set()
        for row in report.unpriced:
            assert row.reason in UNPRICED_REASONS

    def test_r31_the_corpus_exercises_more_than_one_unpriced_reason(self) -> None:
        """R48's shape applied to R30: a taxonomy nothing reaches proves nothing.

        Two of the four reasons are reachable from the checked-in corpus;
        ``usage_missing`` and ``rate_key_missing`` are not, which is why this
        module drives them from synthetic traces instead. Asserted so the gap is
        a recorded fact rather than an accident.
        """
        reasons = set()
        for path in fixture_paths():
            report = compute_costs(load_trace(path), SHIPPED)
            reasons.update(row.reason for row in report.unpriced)
        assert reasons == {"synthetic_span", "model_not_in_snapshot"}

    def test_r31_the_whole_corpus_prices_to_a_positive_total(self) -> None:
        """R31: the control arm — the invariants above are not checking zeros."""
        total = ZERO_USD
        model_calls = 0
        for path in fixture_paths():
            trace = load_trace(path)
            report = compute_costs(trace, SHIPPED)
            total = sum_usd([total, report.total_cost_usd])
            model_calls += sum(1 for span in trace.spans if span.kind == "model_call")
        assert model_calls >= 100
        assert total > 0


# --- AC7 ----------------------------------------------------------------------


class TestAcceptanceCriterion7R28R29R30R31:
    """AC7, verbatim: five spans, five outcomes, and a total computed from the snapshot."""

    def ac7_trace(self) -> Trace:
        """AC7's five ``model_call`` spans, in AC7's order."""
        builder = TraceBuilder()
        builder.model_call(
            model="claude-sonnet-4-5-20250929",
            usage=TokenUsage(
                input_tokens=10,
                cache_creation_5m_tokens=17_971,
                cache_read_input_tokens=0,
                output_tokens=483,
            ),
        )
        builder.model_call(
            model="claude-haiku-4-5-20251001", usage=TokenUsage(input_tokens=1_000, output_tokens=7)
        )
        builder.model_call(model="some-model-nobody-published", usage=TokenUsage(input_tokens=5))
        builder.model_call(
            model=SYNTHETIC_MODEL,
            usage=None,
            error=SpanError(code="rate_limit_error", detail="429"),
        )
        builder.model_call(model="claude-sonnet-4-5-20250929", usage=None)
        return builder.build()

    def test_ac7_the_first_span_costs_the_r28_formula_from_the_snapshot(self) -> None:
        """AC7: six decimal places, computed from the shipped rates by the oracle."""
        report = compute_costs(self.ac7_trace(), SHIPPED)
        first = report.spans[0]
        assert first.seq == 0
        assert first.model_key == "claude-sonnet-4-5"
        usage = self.ac7_trace().spans[0].usage
        assert usage is not None
        expected = oracle_for(SHIPPED, "claude-sonnet-4-5", usage)
        assert first.cost_usd == expected

        # And the same figure derived a third way, from R28's text and the
        # published rates written out by hand: (10 * $3 + 483 * $15 +
        # 17971 * $3.75) per million tokens. The rates are read back out of the
        # snapshot first, so this stays a check on the arithmetic rather than a
        # transcription of the file.
        assert SHIPPED.get_rate("claude-sonnet-4-5", "input") == Decimal("3")
        assert SHIPPED.get_rate("claude-sonnet-4-5", "output") == Decimal("15")
        assert SHIPPED.get_rate("claude-sonnet-4-5", "cache_write_5m") == Decimal("3.75")
        by_hand = Fraction(10 * 3 + 483 * 15) + Fraction(17_971 * 375, 100)
        by_hand /= 1_000_000
        micro = (by_hand * 10**6 * 2 + 1) // 2
        assert expected == Decimal(int(micro)).scaleb(-6)
        assert expected > 0

    def test_ac7_the_second_span_is_priced_via_the_longest_prefix_rule(self) -> None:
        """AC7/R27: ``claude-haiku-4-5-20251001`` onto ``claude-haiku-4-5``."""
        report = compute_costs(self.ac7_trace(), SHIPPED)
        second = {row.seq: row for row in report.spans}[1]
        assert second.model_key == "claude-haiku-4-5"
        assert second.model == "claude-haiku-4-5-20251001"
        assert second.cost_usd == oracle_for(
            SHIPPED, "claude-haiku-4-5", TokenUsage(input_tokens=1_000, output_tokens=7)
        )

    def test_ac7_the_last_three_spans_carry_their_three_distinct_reasons(self) -> None:
        """AC7: ``model_not_in_snapshot``, ``synthetic_span``, ``usage_missing``."""
        report = compute_costs(self.ac7_trace(), SHIPPED)
        reasons = {row.seq: row.reason for row in report.unpriced}
        assert reasons == {
            2: "model_not_in_snapshot",
            3: "synthetic_span",
            4: "usage_missing",
        }

    def test_ac7_the_total_is_the_sum_of_the_two_quantized_span_costs(self) -> None:
        """AC7: exactly, with no third contribution from the three unpriced spans."""
        report = compute_costs(self.ac7_trace(), SHIPPED)
        assert report.priced_spans == 2
        assert report.unpriced_spans == 3
        assert report.total_cost_usd == sum_usd(row.cost_usd for row in report.spans)
        assert report.total_cost_usd == report.spans[0].cost_usd + report.spans[1].cost_usd

    def test_ac7_the_unpriced_rows_keep_the_recorded_model_id(self) -> None:
        """R30: nothing is dropped, and the row says which model it was."""
        report = compute_costs(self.ac7_trace(), SHIPPED)
        models = {row.seq: row.model for row in report.unpriced}
        assert models[2] == "some-model-nobody-published"
        assert models[3] == SYNTHETIC_MODEL
        assert models[4] == "claude-sonnet-4-5-20250929"
