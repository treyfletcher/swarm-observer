"""The detector contract: :class:`Finding`, ids, severities, config (R13-R17, R25).

Three properties of this module are load-bearing, and each is enforced here
rather than asked of every detector author:

* **A finding's identity is a function of its evidence** (R15). ``finding_id``
  hashes the trace id, the detector slug, the metrics and the evidence span
  list through one canonical JSON serialization, so two runs of the same
  command agree, a cosmetic report change does not move an id, and a changed
  *finding* does. That makes ids usable as suppression keys in v2 — which only
  works if nothing else can construct a Finding with a hand-written id, so
  :func:`build_finding` is the constructor detectors use and it computes the id
  from the values it is about to store.

* **A finding's summary carries no trace-derived byte** (R16). The findings
  table is the part of a report a reader trusts most, so it is built from
  integers and swarm-observer's own enumerated slugs. The single trace-derived
  value R16 admits anywhere near it — ``tool_name`` in ``metrics`` — must match
  a strict pattern or be replaced by ``<non-conforming>`` with the original
  pushed into ``previews``, where it is treated as hostile text like any other.

* **Waste is attribution, not a counterfactual** (R17, A3). :func:`attribute_waste`
  sums ``Span.usage`` over the *distinct model_call spans a detector named as
  redundant* — nothing more. It does not claim a deduplicated run would have
  cost that much less, and both reports say so next to the total.

Every ordering, cap and alphabet R14 states in prose is a validator below. The
increment-1 lesson this encodes: a guarantee that lives in a docstring holds
only for the call path whose author read the docstring.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from swarm_observer.model.trace import Span, TokenUsage, Trace, UtcDatetime

#: R14: the severity ladder. The rank is the sort key R13 pins for findings.
Severity = Literal["info", "warning", "critical"]

#: R14: ``info`` < ``warning`` < ``critical``.
SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}

#: The same ladder as data, in ascending rank order.
SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")

#: R14: ``span_seqs`` is evidence, not an inventory — at most this many.
MAX_EVIDENCE_SPANS = 50

#: R14: at most this many preview strings per finding.
MAX_PREVIEWS = 5

#: R15: the number of hex characters after the ``<detector>:`` prefix.
FINDING_ID_HEX = 12

#: R16: the shape a recorded tool name must have to appear in ``metrics``.
TOOL_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$"

#: R16: what a recorded tool name is replaced with when it does not conform.
NON_CONFORMING_TOOL_NAME = "<non-conforming>"

#: The metrics value used where a detector's grouping genuinely has no tool
#: name to report (R22's ``orphan_result`` arm — see that module). An
#: enumerated slug, not trace-derived text.
UNKNOWN_TOOL_NAME = "<unknown>"

# ``fullmatch``, not ``match``: Python's ``$`` also matches immediately before a
# trailing newline, so ``match`` would admit ``"Bash\n"`` as a conforming tool
# name and put a newline into a metrics value that R34 will later render. The
# same one-metacharacter disagreement cost increment 1 a blocker.
_TOOL_NAME_OK = re.compile(TOOL_NAME_PATTERN)


class DetectorConfig(BaseModel):
    """The only tunables a v1 detector reads (R25).

    Exactly two fields, on purpose: every other threshold in R18-R24 is a
    module constant, so a golden report is a function of the trace and one
    integer (A9). ``enabled`` is the ``--detector`` restriction; ``None`` means
    every registered detector runs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocked_gap_seconds: int = Field(default=60, ge=0)
    enabled: frozenset[str] | None = None


class Finding(BaseModel):
    """One detector's verdict about one piece of a trace (R14).

    Frozen and ``extra="forbid"`` like every normalized model: a field a
    detector did not mean to set is a construction error, not a value that
    silently reaches a report.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The registry slug of the detector that produced this finding (R13).
    detector: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    #: R15: ``<detector>:<12 hex>``.
    finding_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}:[0-9a-f]{12}$")
    severity: Severity
    #: R16: integers and enumerated slugs only. Never trace-derived text.
    summary: str = Field(min_length=1, max_length=400)
    #: Evidence: the spans this finding is about, ascending, capped at 50.
    span_seqs: tuple[int, ...] = ()
    #: The agents involved, sorted.
    agent_ids: tuple[str, ...] = ()
    #: JSON scalars only; string values are enumerated slugs or an R16-
    #: constrained tool name. Keys sorted.
    metrics: dict[str, int | str] = Field(default_factory=dict)
    #: Trace-derived evidence text, at most five entries. Redacted and escaped
    #: at the render boundary like every other trace string (R32, R33).
    previews: tuple[str, ...] = ()
    #: R17: an attribution, not a counterfactual saving.
    wasted: TokenUsage = TokenUsage()
    #: R26: filled by the cost engine in increment 3; ``None`` until then.
    wasted_cost_usd: Decimal | None = None

    @model_validator(mode="after")
    def _pinned_shape(self) -> Finding:
        """R14's prose orderings and caps, as constraints."""
        if list(self.span_seqs) != sorted(self.span_seqs):
            raise ValueError("span_seqs must be ascending")
        if len(set(self.span_seqs)) != len(self.span_seqs):
            raise ValueError("span_seqs must not repeat a seq")
        if len(self.span_seqs) > MAX_EVIDENCE_SPANS:
            raise ValueError(f"span_seqs is capped at {MAX_EVIDENCE_SPANS} entries")
        if any(seq < 0 for seq in self.span_seqs):
            raise ValueError("span_seqs must be non-negative")
        if list(self.agent_ids) != sorted(self.agent_ids):
            raise ValueError("agent_ids must be sorted")
        if len(set(self.agent_ids)) != len(self.agent_ids):
            raise ValueError("agent_ids must not repeat an agent")
        if len(self.previews) > MAX_PREVIEWS:
            raise ValueError(f"previews is capped at {MAX_PREVIEWS} entries")
        keys = list(self.metrics)
        if keys != sorted(keys):
            raise ValueError("metrics keys must be sorted")
        if self.finding_id.split(":", 1)[0] != self.detector:
            raise ValueError("finding_id must be prefixed with its detector slug")
        return self


class Detector(Protocol):
    """What every detector is (R13).

    A pure function of ``(trace, config)`` wearing four attributes: the registry
    slug, a human title, the severity the detector reports when it has no reason
    to escalate, and ``run``. ``run`` returns findings **already sorted** by
    ``(severity_rank, slug, finding_id)`` — see :func:`sort_findings` — so a
    caller never has to know a detector's internal iteration order.
    """

    slug: str
    title: str
    default_severity: Severity

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Every finding this detector makes about ``trace``."""
        ...


def finding_id(
    *,
    trace_id: str,
    detector: str,
    metrics: Mapping[str, int | str],
    span_seqs: Sequence[int],
) -> str:
    """R15: ``<detector>:<first 12 hex of sha256 over the canonical payload>``.

    ``sort_keys`` removes every dict-ordering dependency (and therefore every
    ``PYTHONHASHSEED`` dependency), ``ensure_ascii`` every encoding dependency,
    and the payload names only values the detector computed — so the id is
    stable across runs, processes and interpreters, and moves exactly when the
    evidence moves.
    """
    payload = json.dumps(
        {
            "trace": trace_id,
            "detector": detector,
            "metrics": dict(metrics),
            "spans": list(span_seqs),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:FINDING_ID_HEX]
    return f"{detector}:{digest}"


def constrain_tool_name(recorded: str | None) -> tuple[str, str | None]:
    """R16: the ``(metrics value, overflow preview)`` pair for a recorded tool name.

    A conforming name is carried verbatim and there is nothing to preview. A
    non-conforming one — or an absent one — becomes ``<non-conforming>`` in
    ``metrics``, and the original (when there is one) is returned so the caller
    can push it into ``previews``, which is trace-derived text by contract and
    is redacted and escaped like any other.
    """
    if recorded is not None and _TOOL_NAME_OK.fullmatch(recorded):
        return recorded, None
    return NON_CONFORMING_TOOL_NAME, recorded


def attribute_waste(trace: Trace, seqs: Iterable[int]) -> TokenUsage:
    """R17: element-wise sum of ``Span.usage`` over distinct redundant model calls.

    Only ``model_call`` spans with a recorded ``usage`` contribute; a repeated
    *tool* call costs nothing by itself, and a span with no usage has nothing to
    attribute. Duplicates in ``seqs`` are collapsed — R17 says *distinct* — so a
    detector may hand in one parent per occurrence without deduplicating first.

    This is an attribution and both reports label it as one (A3): it says "these
    model calls produced work the trace shows was repeated or discarded", not
    "removing them would have saved this".
    """
    total = TokenUsage()
    for seq in sorted(set(seqs)):
        if seq < 0 or seq >= len(trace.spans):
            continue
        span = trace.spans[seq]
        if span.kind != "model_call" or span.usage is None:
            continue
        total = total.plus(span.usage)
    return total


def build_finding(
    *,
    trace: Trace,
    detector: str,
    severity: Severity,
    summary: str,
    metrics: Mapping[str, int | str],
    span_seqs: Iterable[int],
    agent_ids: Iterable[str],
    previews: Iterable[str] = (),
    wasted: TokenUsage | None = None,
) -> Finding:
    """The one constructor detectors use (R14, R15).

    Normalizes before it hashes: evidence spans are deduplicated, sorted and
    capped at :data:`MAX_EVIDENCE_SPANS`, agent ids sorted and deduplicated,
    previews dropped when empty and capped at :data:`MAX_PREVIEWS`, metrics
    keys sorted. The id is then computed from *those* values rather than from
    what the caller passed, so two detectors that assemble the same evidence in
    different orders produce the same finding id — which is the whole point of
    R15.
    """
    ordered_spans = tuple(sorted(set(span_seqs)))[:MAX_EVIDENCE_SPANS]
    ordered_agents = tuple(sorted(set(agent_ids)))
    kept_previews = tuple(text for text in previews if text)[:MAX_PREVIEWS]
    ordered_metrics: dict[str, int | str] = {key: metrics[key] for key in sorted(metrics)}
    return Finding(
        detector=detector,
        finding_id=finding_id(
            trace_id=trace.trace_id,
            detector=detector,
            metrics=ordered_metrics,
            span_seqs=ordered_spans,
        ),
        severity=severity,
        summary=summary,
        span_seqs=ordered_spans,
        agent_ids=ordered_agents,
        metrics=ordered_metrics,
        previews=kept_previews,
        wasted=wasted if wasted is not None else TokenUsage(),
    )


def sort_findings(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """R13: ``(severity_rank, slug, finding_id)`` — the order every detector returns."""
    return tuple(
        sorted(
            findings,
            key=lambda item: (SEVERITY_RANK[item.severity], item.detector, item.finding_id),
        )
    )


# --- shared, purely structural views of a trace -------------------------------
#
# Every detector needs one or two of these and none of them belongs to a single
# detector. Keeping them here rather than duplicating three loops is the same
# rule R44 applies to `escape_html`: shape guarantees compose only when there is
# one implementation.


def spans_by_agent(trace: Trace) -> dict[str, tuple[Span, ...]]:
    """Each agent's spans in ``seq`` order, keyed by agent id.

    Built from ``AgentRun.span_seqs`` (ascending by R2's validator) rather than
    by grouping spans, so the detectors and the report agree about what belongs
    to an agent.
    """
    return {
        agent.agent_id: tuple(trace.spans[seq] for seq in agent.span_seqs) for agent in trace.agents
    }


def agent_order(trace: Trace) -> tuple[str, ...]:
    """Agent ids in ``agent_index`` order — the iteration order detectors use."""
    return tuple(agent.agent_id for agent in trace.agents)


def model_calls(trace: Trace) -> tuple[Span, ...]:
    """Every ``model_call`` span, in ``seq`` order."""
    return tuple(span for span in trace.spans if span.kind == "model_call")


def seq_by_span_id(trace: Trace) -> dict[str, int]:
    """``span_id`` → ``seq``, so a ``parent_span_id`` can be resolved to a span."""
    return {span.span_id: span.seq for span in trace.spans}


def first_tool_call_by_parent(trace: Trace) -> dict[str, Span]:
    """``model_call`` span id → the lowest-``seq`` ``tool_call`` it emitted (R19)."""
    found: dict[str, Span] = {}
    for span in trace.spans:
        if span.kind != "tool_call" or span.parent_span_id is None:
            continue
        found.setdefault(span.parent_span_id, span)
    return found


def millis_between(earlier: UtcDatetime, later: UtcDatetime) -> int:
    """Whole milliseconds from ``earlier`` to ``later``, as an ``int``.

    Integer arithmetic end to end. ``timedelta.total_seconds()`` returns a
    float, and R23 and R24 both say their arithmetic is integral — a float here
    would make a gap of exactly 60 seconds a coin flip at the boundary and
    would put a float into a value that reaches report bytes (R47).
    """
    delta = later - earlier
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def span_duration_ms(span: Span) -> int | None:
    """A span's duration in whole milliseconds, or ``None`` without both endpoints.

    Clamped at zero: R6 already counts a negative duration as a warning at parse
    time, and a negative outlier value is meaningless to a reader.
    """
    if span.start is None or span.end is None:
        return None
    return max(0, millis_between(span.start, span.end))


def lower_median(values: Sequence[int]) -> int:
    """The element at index ``(n-1)//2`` of the sorted values (R24).

    Pinned as the *lower* median so an even-sized population never introduces a
    ``.5`` — which would be a float in a value that reaches report bytes, and
    would make the outlier threshold depend on float formatting.
    """
    if not values:
        raise ValueError("lower_median of an empty population")
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


__all__ = [
    "FINDING_ID_HEX",
    "MAX_EVIDENCE_SPANS",
    "MAX_PREVIEWS",
    "NON_CONFORMING_TOOL_NAME",
    "SEVERITIES",
    "SEVERITY_RANK",
    "TOOL_NAME_PATTERN",
    "UNKNOWN_TOOL_NAME",
    "Detector",
    "DetectorConfig",
    "Finding",
    "Severity",
    "agent_order",
    "attribute_waste",
    "build_finding",
    "constrain_tool_name",
    "finding_id",
    "first_tool_call_by_parent",
    "lower_median",
    "millis_between",
    "model_calls",
    "seq_by_span_id",
    "sort_findings",
    "span_duration_ms",
    "spans_by_agent",
]
