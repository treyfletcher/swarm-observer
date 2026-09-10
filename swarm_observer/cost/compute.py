"""The cost engine: R28's formula, R29's Decimal discipline, R30, R31.

Four requirements meet here, and each one is a place where a fast
implementation is quietly wrong rather than loudly broken.

**R28 — the formula.** Five token components, five rates, one division by a
million. The notes attached to R28 are requirements, not commentary:
``input_tokens`` as recorded already *excludes* cached tokens, so nothing is
subtracted; ``output_tokens_details.thinking_tokens`` is already inside
``output_tokens``, so nothing is added. Both mistakes produce a plausible
number.

**R29 — Decimal from load to format.** Every arithmetic step below runs inside a
context that **traps** :class:`decimal.Inexact`. That is not decoration: the
whole point of the requirement is that no rounding happens anywhere except the
one place the spec puts it, and a trapped signal is the difference between
"nothing rounds" being a claim and being a check. A span's cost is quantized
``ROUND_HALF_UP`` to six places, and every total is a sum of *already
quantized* span costs, so a table's rows add up to its total exactly rather
than to within a rounding error.

**R30 — nothing is dropped.** A ``model_call`` span that cannot be priced is
never omitted; it goes into :attr:`CostReport.unpriced` with exactly one reason
from a closed enum, assigned in the priority order R30 states. The order
matters: an API-error record has no usage *and* an unrecognised model id, and
calling it ``model_not_in_snapshot`` would put ``<synthetic>`` in a table of
models somebody might go looking for.

**R31 — four groupings.** Whole trace, per agent, per resolved model key, per
detector for attributed waste. Each grouping's total is the sum of its members'
quantized span costs, which is what makes them comparable.

One thing this module deliberately does not know about is
:class:`~swarm_observer.detect.base.Finding`: R44 forbids ``cost`` importing
``detect``. Waste therefore arrives as ``{finding_id: attributed span seqs}``
and the detector slug is read off the finding id, whose ``<detector>:<hex>``
shape R15 pins. See :func:`compute_costs` for why the *seqs* rather than the
already-summed ``Finding.wasted`` are the input.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from swarm_observer.cost.source import (
    USAGE_PRICE_KEYS,
    RateSource,
    SnapshotMeta,
    rates_for,
)
from swarm_observer.model.trace import Span, TokenUsage, Trace

#: R29: every span cost is quantized to this many decimal places.
COST_DECIMAL_PLACES = 6

#: R29: the quantum a span cost is rounded to, ``ROUND_HALF_UP``.
COST_QUANTUM = Decimal("0.000001")

#: R29: the HTML report additionally shows a two-decimal grand total, computed
#: by quantizing the six-decimal total. Defined here so there is one rounding
#: rule for money in the package rather than one per renderer.
DISPLAY_QUANTUM = Decimal("0.01")

#: The zero every empty subtotal renders as — already at the right exponent, so
#: an empty group and a group summing to nothing produce identical bytes (R47).
ZERO_USD = Decimal("0.000000")

#: Significant digits available to the intermediate arithmetic. Chosen far above
#: anything a real trace reaches (a billion tokens at a two-figure rate is 18
#: digits) so that trapping :class:`decimal.Inexact` cannot fire on honest input
#: — and does fire on a token count with more than this many significant digits,
#: which is a corrupt trace and must not be priced approximately.
COST_PRECISION = 60

#: R12/R30: the model id Claude Code records on a synthetic, non-billable
#: response. R30 names the literal, so it is spelled here rather than imported
#: from ``ingest`` (R44 forbids that import, and rightly: the taxonomy is a
#: property of the cost engine, not of one adapter).
SYNTHETIC_MODEL = "<synthetic>"

#: R30's closed enum, **in the priority order R30 assigns it**. The order is the
#: requirement; the tuple is what a test can assert against.
UnpricedReason = Literal[
    "synthetic_span",
    "usage_missing",
    "model_not_in_snapshot",
    "rate_key_missing",
]
UNPRICED_REASONS: tuple[str, ...] = (
    "synthetic_span",
    "usage_missing",
    "model_not_in_snapshot",
    "rate_key_missing",
)

#: R17/A3: the sentence both reports carry next to every waste total. Stored
#: once, here, so the HTML and the JSON cannot say different things about what
#: the number means.
WASTE_CAVEAT = (
    "Attributed waste sums the usage of model calls the trace shows were repeated or "
    "discarded. It is an attribution, not a counterfactual: a deduplicated run would "
    "have had different cache behaviour and would not have cost exactly this much less."
)

#: A4: the sentence both reports carry next to every dollar figure.
RATE_CAVEAT = (
    "Costs are list-price estimates from a bundled rate snapshot at its snapshot date. "
    "They exclude batch and priority tiers, long-context tiers and any negotiated "
    "discount, and they are not a billing reconciliation."
)

_FINDING_ID = re.compile(r"^([a-z][a-z0-9_]{0,63}):[0-9a-f]{12}$")

#: Arithmetic that must not round. ``Inexact`` is trapped on purpose — see the
#: module docstring.
_EXACT = Context(
    prec=COST_PRECISION,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)

#: The one place rounding is allowed: R29's six-place quantization.
_ROUNDING = Context(prec=COST_PRECISION, rounding=ROUND_HALF_UP)


class CostError(Exception):
    """Pricing could not be completed exactly (R29).

    Raised only when the arithmetic itself cannot be carried out without
    rounding — a token count with more significant digits than
    :data:`COST_PRECISION`, which no honest transcript produces. Failing is the
    right answer: a dollar figure that silently lost digits is worse than no
    dollar figure, and R11's posture (one sanitized line, exit 2) is what the
    CLI applies to it.
    """

    def __init__(self, code: str, *, seq: int | None = None) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            raise ValueError("CostError code must be an enumerated slug")
        self.code = code
        self.seq = seq
        self.detail = "" if seq is None else f"span {seq}"
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)

    @property
    def cli_line(self) -> str:
        """The exact single line the CLI writes to stderr (R11's shape, R39)."""
        return f"swarm-observer: {self.code}: {self.detail}"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SpanCost(_Frozen):
    """One priced ``model_call`` span (R28, R29)."""

    seq: int = Field(ge=0)
    agent_id: str
    #: The id as recorded in the trace. Trace-derived: redacted at render (R30).
    model: str = ""
    #: The snapshot key R27's ladder resolved it to.
    model_key: str
    usage: TokenUsage
    #: Already quantized to :data:`COST_DECIMAL_PLACES` places.
    cost_usd: Decimal


class UnpricedSpan(_Frozen):
    """One ``model_call`` span that could not be priced, and why (R30)."""

    seq: int = Field(ge=0)
    agent_id: str
    #: The id as recorded in the trace. Trace-derived: redacted at render (R30).
    model: str = ""
    reason: UnpricedReason
    #: Only for ``rate_key_missing``: the price keys the entry lacked, sorted.
    #: Enumerated slugs from :data:`~swarm_observer.cost.source.PRICE_KEYS`.
    missing_price_keys: tuple[str, ...] = ()


class AgentCost(_Frozen):
    """One agent's priced totals (R31). Ordered by ``agent_index`` in the report."""

    agent_id: str
    agent_index: int = Field(ge=0)
    priced_spans: int = Field(ge=0)
    unpriced_spans: int = Field(ge=0)
    #: The usage of the *priced* spans only; unpriced usage is reported apart so
    #: a subtotal and its cost always describe the same set of spans.
    usage: TokenUsage
    cost_usd: Decimal


class ModelCost(_Frozen):
    """One resolved model key's priced totals (R31). Ordered by key."""

    model_key: str
    priced_spans: int = Field(ge=0)
    usage: TokenUsage
    cost_usd: Decimal


class DetectorWaste(_Frozen):
    """One detector's attributed waste (R17, R31). Ordered by slug."""

    detector: str
    findings: int = Field(ge=0)
    #: Findings whose attributed model calls include one this engine could not
    #: price, so their cost is unknown rather than zero.
    findings_unpriced: int = Field(ge=0)
    wasted: TokenUsage
    wasted_cost_usd: Decimal


class CostReport(_Frozen):
    """Everything the cost section of either report renders (R30, R31)."""

    meta: SnapshotMeta
    #: The sum of every priced span's already-quantized cost (R29).
    total_cost_usd: Decimal
    total_usage: TokenUsage
    priced_spans: int = Field(ge=0)
    unpriced_spans: int = Field(ge=0)
    #: The usage carried by spans that could not be priced. Reported so the
    #: token totals stay complete even where the dollar totals cannot be.
    unpriced_usage: TokenUsage
    spans: tuple[SpanCost, ...] = ()
    unpriced: tuple[UnpricedSpan, ...] = ()
    by_agent: tuple[AgentCost, ...] = ()
    by_model: tuple[ModelCost, ...] = ()
    by_detector: tuple[DetectorWaste, ...] = ()
    #: ``finding_id`` → ``Finding.wasted_cost_usd``. ``None`` where at least one
    #: attributed model call could not be priced: the cost is *unknown*, and
    #: reporting a partial sum as if it were the total would understate the
    #: product's headline number without saying so.
    waste_by_finding: dict[str, Decimal | None] = Field(default_factory=dict)

    def cost_of(self, seq: int) -> Decimal | None:
        """The quantized cost of the span at ``seq``, or ``None`` if unpriced."""
        for priced in self.spans:
            if priced.seq == seq:
                return priced.cost_usd
        return None


def quantize_cost(value: Decimal) -> Decimal:
    """R29: ``ROUND_HALF_UP`` to six decimal places, the one rounding allowed."""
    return value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP, context=_ROUNDING)


def quantize_display(value: Decimal) -> Decimal:
    """R29: the two-decimal grand total, quantized from the six-decimal one."""
    return value.quantize(DISPLAY_QUANTUM, rounding=ROUND_HALF_UP, context=_ROUNDING)


def format_usd(value: Decimal) -> str:
    """The exact string both reports render for a money value (R29, R36).

    Fixed point, always six places, never scientific notation and never
    locale-formatted: ``format(value, "f")`` on a value already quantized to
    ``1E-6`` is a pure function of the digits (R47).
    """
    return format(quantize_cost(value), "f")


def format_display_usd(value: Decimal) -> str:
    """The two-decimal string R29 allows for a grand total only."""
    return format(quantize_display(value), "f")


def sum_usd(values: Iterable[Decimal]) -> Decimal:
    """Add already-quantized costs without rounding (R29).

    Starts from :data:`ZERO_USD` rather than from ``Decimal(0)`` so an empty sum
    carries the same exponent as a non-empty one and formats identically.
    """
    total = ZERO_USD
    try:
        for value in values:
            total = _EXACT.add(total, value)
    except DecimalException:  # pragma: no cover - needs a 60-digit dollar total
        raise CostError("cost_precision_exceeded") from None
    return total


def sum_usage(usages: Iterable[TokenUsage]) -> TokenUsage:
    """Element-wise sum of token usage — the same operation R17 attributes with."""
    total = TokenUsage()
    for usage in usages:
        total = total.plus(usage)
    return total


def price_usage(usage: TokenUsage, rates: Mapping[str, Decimal]) -> Decimal:
    """R28's formula, quantized per R29.

    ``rates`` are USD per 1,000,000 tokens. A component whose rate is absent
    contributes nothing here — R30's ``rate_key_missing`` is what stops a span
    with a *non-zero* such component from reaching this function at all, so a
    silently-zero term is impossible rather than merely unlikely.
    """
    try:
        subtotal = Decimal(0)
        for field, price_key in USAGE_PRICE_KEYS:
            tokens: int = getattr(usage, field)
            rate = rates.get(price_key)
            if rate is None or tokens == 0:
                continue
            subtotal = _EXACT.add(subtotal, _EXACT.multiply(Decimal(tokens), rate))
        exact = _EXACT.divide(subtotal, Decimal(1_000_000))
    except DecimalException:
        raise CostError("cost_precision_exceeded") from None
    return quantize_cost(exact)


def missing_price_keys(usage: TokenUsage, rates: Mapping[str, Decimal]) -> tuple[str, ...]:
    """R30.4: price keys a **non-zero** usage component needs and ``rates`` lacks.

    "A zero-token component never triggers ``rate_key_missing``" is R30's own
    sentence and it is the whole subtlety: almost every model call has a zero
    ``cache_creation_1h_tokens``, so keying off the rate table alone would put
    most of a trace in the unpriced list.
    """
    absent: list[str] = []
    for field, price_key in USAGE_PRICE_KEYS:
        tokens: int = getattr(usage, field)
        if tokens != 0 and price_key not in rates:
            absent.append(price_key)
    return tuple(sorted(absent))


def classify_span(
    span: Span, source: RateSource
) -> tuple[str | None, UnpricedReason | None, tuple[str, ...]]:
    """R30: ``(model_key, reason, missing_keys)`` for one ``model_call`` span.

    Exactly one of ``model_key`` and ``reason`` is not ``None``. The four tests
    run **in R30's stated priority order**, and each ``return`` below is one
    rung of it; reordering them changes which reason a span reports without
    changing whether it is priced, which is the kind of defect that survives a
    green suite.
    """
    if span.error is not None or span.model == SYNTHETIC_MODEL:
        return None, "synthetic_span", ()
    if span.usage is None:
        return None, "usage_missing", ()
    model_key = source.resolve_model_key(span.model)
    if model_key is None:
        return None, "model_not_in_snapshot", ()
    absent = missing_price_keys(span.usage, rates_for(source, model_key))
    if absent:
        return None, "rate_key_missing", absent
    return model_key, None, ()


def _detector_of(finding_id: str) -> str:
    """The detector slug carried by a finding id (R15's ``<detector>:<hex>``)."""
    matched = _FINDING_ID.fullmatch(finding_id)
    if matched is None:
        raise ValueError(f"{finding_id!r} is not a finding id")
    return matched.group(1)


def compute_costs(
    trace: Trace,
    source: RateSource,
    *,
    waste_seqs: Mapping[str, Sequence[int]] | None = None,
) -> CostReport:
    """Price a whole trace and aggregate it four ways (R28-R31).

    ``waste_seqs`` maps each finding id to the ``seq`` values of the
    ``model_call`` spans that finding's detector named as redundant (R17). The
    *seqs* are the input rather than the already-summed ``Finding.wasted``
    because R29 requires ``wasted_cost_usd`` to be a **sum of already-quantized
    span costs**: a summed ``TokenUsage`` has lost which model produced each
    token, and a trace with a Haiku subagent under a Sonnet orchestrator cannot
    be priced from it at all. R14's ``Finding`` has no field for the attributed
    spans, so the pipeline carries them alongside — see the PR's A-c3.
    """
    attributed = dict(waste_seqs or {})
    for finding_id in attributed:
        _detector_of(finding_id)  # fail loudly on a key that is not a finding id

    priced: list[SpanCost] = []
    unpriced: list[UnpricedSpan] = []
    for span in trace.spans:
        if span.kind != "model_call":
            continue
        model_key, reason, absent = classify_span(span, source)
        if reason is not None:
            unpriced.append(
                UnpricedSpan(
                    seq=span.seq,
                    agent_id=span.agent_id,
                    model=span.model or "",
                    reason=reason,
                    missing_price_keys=absent,
                )
            )
            continue
        if model_key is None:  # pragma: no cover - classify_span returns one or the other
            raise CostError("cost_classification_incomplete", seq=span.seq)
        usage = span.usage if span.usage is not None else TokenUsage()
        try:
            span_cost = price_usage(usage, rates_for(source, model_key))
        except CostError as exc:
            # Re-raised with the span named: the failure is a property of one
            # record's token counts, and "somewhere in two million spans" is not
            # a diagnostic. The seq is a decimal integer this package computed
            # (R6), so it carries no trace content (R11).
            raise CostError(exc.code, seq=span.seq) from None
        priced.append(
            SpanCost(
                seq=span.seq,
                agent_id=span.agent_id,
                model=span.model or "",
                model_key=model_key,
                usage=usage,
                cost_usd=span_cost,
            )
        )

    cost_by_seq = {item.seq: item.cost_usd for item in priced}
    usage_by_seq = {item.seq: item.usage for item in priced}

    return CostReport(
        meta=source.meta,
        total_cost_usd=sum_usd(item.cost_usd for item in priced),
        total_usage=sum_usage(item.usage for item in priced),
        priced_spans=len(priced),
        unpriced_spans=len(unpriced),
        unpriced_usage=sum_usage(trace.spans[item.seq].usage or TokenUsage() for item in unpriced),
        spans=tuple(priced),
        unpriced=tuple(unpriced),
        by_agent=_by_agent(trace, priced, unpriced),
        by_model=_by_model(priced),
        by_detector=_by_detector(trace, attributed, cost_by_seq, usage_by_seq),
        waste_by_finding=_waste_by_finding(trace, attributed, cost_by_seq),
    )


def _by_agent(
    trace: Trace, priced: Sequence[SpanCost], unpriced: Sequence[UnpricedSpan]
) -> tuple[AgentCost, ...]:
    """R31: per agent, ordered by ``agent_index``.

    Agents come from ``trace.agents`` rather than from the spans, so an agent
    that made no model call still appears with a zero total instead of silently
    vanishing from the table. A span naming an agent no ``AgentRun`` declares
    (which the v1 mapper cannot produce, but R2 does not forbid) is given a row
    after the declared ones rather than dropped — R30's "nothing is silently
    omitted" is about the cost section as a whole, not only about the unpriced
    list.
    """
    index_of = {agent.agent_id: agent.agent_index for agent in trace.agents}
    seen = {item.agent_id for item in priced} | {item.agent_id for item in unpriced}
    order = [(agent.agent_index, agent.agent_id) for agent in trace.agents]
    undeclared = sorted(agent_id for agent_id in seen if agent_id not in index_of)
    order += [(len(index_of) + offset, agent_id) for offset, agent_id in enumerate(undeclared)]
    rows: list[AgentCost] = []
    for agent_index, agent_id in order:
        mine = [item for item in priced if item.agent_id == agent_id]
        rows.append(
            AgentCost(
                agent_id=agent_id,
                agent_index=agent_index,
                priced_spans=len(mine),
                unpriced_spans=len([item for item in unpriced if item.agent_id == agent_id]),
                usage=sum_usage(item.usage for item in mine),
                cost_usd=sum_usd(item.cost_usd for item in mine),
            )
        )
    return tuple(rows)


def _by_model(priced: Sequence[SpanCost]) -> tuple[ModelCost, ...]:
    """R31: per resolved ``model_key``, ordered by key."""
    keys = sorted({item.model_key for item in priced})
    rows: list[ModelCost] = []
    for key in keys:
        mine = [item for item in priced if item.model_key == key]
        rows.append(
            ModelCost(
                model_key=key,
                priced_spans=len(mine),
                usage=sum_usage(item.usage for item in mine),
                cost_usd=sum_usd(item.cost_usd for item in mine),
            )
        )
    return tuple(rows)


def _relevant_seqs(trace: Trace, seqs: Sequence[int]) -> tuple[int, ...]:
    """The attributed seqs that actually carry cost — R17's own filter.

    ``attribute_waste`` counts a seq only when it names a ``model_call`` with a
    recorded ``usage``; anything else contributes zero tokens. Applying the same
    filter here is what keeps ``wasted`` and ``wasted_cost_usd`` describing the
    same set of spans, so a finding cannot report tokens it has no cost for or a
    cost for tokens it did not report.
    """
    kept: list[int] = []
    for seq in sorted(set(seqs)):
        if not 0 <= seq < len(trace.spans):
            continue
        span = trace.spans[seq]
        if span.kind == "model_call" and span.usage is not None:
            kept.append(seq)
    return tuple(kept)


def _waste_by_finding(
    trace: Trace, attributed: Mapping[str, Sequence[int]], cost_by_seq: Mapping[int, Decimal]
) -> dict[str, Decimal | None]:
    """R14/R29: each finding's ``wasted_cost_usd``, or ``None`` when unknowable."""
    found: dict[str, Decimal | None] = {}
    for finding_id in sorted(attributed):
        relevant = _relevant_seqs(trace, attributed[finding_id])
        if any(seq not in cost_by_seq for seq in relevant):
            found[finding_id] = None
            continue
        found[finding_id] = sum_usd(cost_by_seq[seq] for seq in relevant)
    return found


def _by_detector(
    trace: Trace,
    attributed: Mapping[str, Sequence[int]],
    cost_by_seq: Mapping[int, Decimal],
    usage_by_seq: Mapping[int, TokenUsage],
) -> tuple[DetectorWaste, ...]:
    """R31: attributed waste per detector, ordered by slug.

    A span attributed by two findings is counted in both, and deliberately: the
    number is an attribution of the same tokens under two different readings
    (A7's intentional detector overlap), not a bill. Within one detector the
    spans are deduplicated, so a detector cannot double-count its own evidence.
    """
    slugs = sorted({_detector_of(finding_id) for finding_id in attributed})
    rows: list[DetectorWaste] = []
    for slug in slugs:
        mine = sorted(finding_id for finding_id in attributed if _detector_of(finding_id) == slug)
        seqs: set[int] = set()
        unknown = 0
        costs: list[Decimal] = []
        for finding_id in mine:
            relevant = _relevant_seqs(trace, attributed[finding_id])
            seqs.update(relevant)
            if any(seq not in cost_by_seq for seq in relevant):
                unknown += 1
                continue
            costs.append(sum_usd(cost_by_seq[seq] for seq in relevant))
        rows.append(
            DetectorWaste(
                detector=slug,
                findings=len(mine),
                findings_unpriced=unknown,
                wasted=sum_usage(usage_by_seq[seq] for seq in sorted(seqs) if seq in usage_by_seq),
                wasted_cost_usd=sum_usd(
                    cost_by_seq[seq] for seq in sorted(seqs) if seq in cost_by_seq
                ),
            )
        )
    return tuple(rows)


__all__ = [
    "COST_DECIMAL_PLACES",
    "COST_PRECISION",
    "COST_QUANTUM",
    "DISPLAY_QUANTUM",
    "RATE_CAVEAT",
    "SYNTHETIC_MODEL",
    "UNPRICED_REASONS",
    "WASTE_CAVEAT",
    "ZERO_USD",
    "AgentCost",
    "CostError",
    "CostReport",
    "DetectorWaste",
    "ModelCost",
    "SpanCost",
    "UnpricedReason",
    "UnpricedSpan",
    "classify_span",
    "compute_costs",
    "format_display_usd",
    "format_usd",
    "missing_price_keys",
    "price_usage",
    "quantize_cost",
    "quantize_display",
    "sum_usage",
    "sum_usd",
]
