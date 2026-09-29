"""``anomalous_span`` — a model call far outside the run's own distribution (R24).

Integer statistics only, and the reasons are all determinism (R47):

* The **lower median** — the element at index ``(n-1)//2`` — never introduces a
  ``.5`` on an even-sized population, so no float ever reaches a threshold, a
  metric or a report byte.
* **MAD, not standard deviation.** A run with one enormous call would inflate a
  standard deviation enough to hide the very call that inflated it; the median
  absolute deviation does not move.
* **A population floor of 8.** Below that, "the median" is a description of
  three or four numbers and calling one of them an outlier is noise. Skipping
  entirely is the honest answer, and it is why a short fixture is a legitimate
  negative arm for this detector (R48).
* **An absolute floor as well as a relative one.** ``mad`` is frequently 0 on a
  uniform run, which makes ``med + 6*mad`` equal to ``med`` and would fire on
  every call one token above the median. ``floor_tokens`` and
  ``floor_duration_ms`` are what stop that from being a detector that reports
  half the trace.

An outlier is *not* waste (R17): a call can legitimately be large. ``wasted`` is
zero and stays zero.
"""

from __future__ import annotations

from collections.abc import Sequence

from swarm_observer.detect.base import (
    Detector,
    DetectorConfig,
    Finding,
    Severity,
    build_finding,
    lower_median,
    model_calls,
    sort_findings,
    span_duration_ms,
)
from swarm_observer.model.trace import Span, Trace

#: R24: below this population size the detector does not run at all.
POPULATION_FLOOR = 8

#: R24: the multiple of the MAD a value must exceed to fire.
MAD_MULTIPLE = 6

#: R24: the multiple at which the finding is ``critical`` rather than ``warning``.
CRITICAL_MAD_MULTIPLE = 12

#: R24: a token count below this never fires, whatever the MAD says.
FLOOR_TOKENS = 10_000

#: R24: a duration below this many milliseconds never fires.
FLOOR_DURATION_MS = 30_000

#: The two dimensions, in the order findings are produced.
DIMENSIONS: tuple[str, ...] = ("duration", "tokens")

#: Each dimension's absolute floor (R24).
DIMENSION_FLOORS: dict[str, int] = {"duration": FLOOR_DURATION_MS, "tokens": FLOOR_TOKENS}


def mad_of(values: Sequence[int], median: int) -> int:
    """R24: the lower median of ``|x - med|`` — integer arithmetic throughout."""
    return lower_median([abs(value - median) for value in values])


class AnomalousSpan:
    """R24: one finding per ``(span, dimension)`` that clears both thresholds."""

    slug = "anomalous_span"
    title = "Anomalous model call"
    default_severity: Severity = "warning"

    def run(self, trace: Trace, config: DetectorConfig) -> tuple[Finding, ...]:
        """Compare each model call against the run's own median and MAD (R24)."""
        population = [span for span in model_calls(trace) if span.usage is not None]
        if len(population) < POPULATION_FLOOR:
            # R24 gates on the population — model calls with usage — once, not
            # per dimension. A trace too small to have a distribution has no
            # outliers by definition.
            return ()

        findings: list[Finding] = []
        for dimension in DIMENSIONS:
            measured: list[tuple[Span, int]] = []
            for span in population:
                value = self._value(span, dimension)
                if value is not None:
                    measured.append((span, value))
            if not measured:
                continue
            values = [value for _, value in measured]
            median = lower_median(values)
            mad = mad_of(values, median)
            floor = DIMENSION_FLOORS[dimension]
            for span, value in measured:
                if value <= median + MAD_MULTIPLE * mad or value < floor:
                    continue
                severity: Severity = (
                    "critical"
                    if value > median + CRITICAL_MAD_MULTIPLE * mad
                    else self.default_severity
                )
                findings.append(
                    build_finding(
                        trace=trace,
                        detector=self.slug,
                        severity=severity,
                        summary=(
                            f"one model call's {dimension} was {value}, against a median "
                            f"of {median} and a MAD of {mad} over {len(values)} calls"
                        ),
                        metrics={
                            "dimension": dimension,
                            "mad": mad,
                            "median": median,
                            "seq": span.seq,
                            "value": value,
                        },
                        span_seqs=[span.seq],
                        agent_ids=[span.agent_id],
                        previews=[span.text_preview],
                    )
                )
        return sort_findings(findings)

    def _value(self, span: Span, dimension: str) -> int | None:
        """The span's value in ``dimension``, or ``None`` when it has none (R24)."""
        if dimension == "tokens":
            return span.usage.total if span.usage is not None else None
        return span_duration_ms(span)


#: The registered instance (R13, R48).
DETECTOR: Detector = AnomalousSpan()

__all__ = [
    "CRITICAL_MAD_MULTIPLE",
    "DETECTOR",
    "DIMENSIONS",
    "DIMENSION_FLOORS",
    "FLOOR_DURATION_MS",
    "FLOOR_TOKENS",
    "MAD_MULTIPLE",
    "POPULATION_FLOOR",
    "AnomalousSpan",
    "mad_of",
]
