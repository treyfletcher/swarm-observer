"""The narrator seam: the protocol, the payload models, the error taxonomy (R41, R42).

## Why the payload models live here and why they look like this

R42 says the ``--explain`` request "contains **no** ``previews``, no span text,
no tool arguments, no tool results, no file names, no paths, no ``trace_id``",
and AC12 makes that testable: over a trace whose every free-text field is a
distinctive sentinel, no sentinel may appear in the serialized request.

There are two ways to satisfy that. One is to build the payload from whatever
is to hand and then filter the trace text back out. The other is to make the
payload a type that **cannot hold** trace text. This module does the second,
and the difference matters because the first has the shape of every defect this
project has shipped: a filter is only as complete as the list of fields
somebody remembered.

Concretely, **every string-valued field in the request tree** is one of:

* a :class:`typing.Literal` — ``severity``, the request version;
* a member of a closed vocabulary this package computed — a detector slug from
  :data:`~swarm_observer.detect.registry.DETECTOR_SLUGS`, a detector title from
  the same registry, a group key from :data:`NARRATION_GROUPS`;
* a decimal money string matching :data:`MONEY_PATTERN`, produced by
  :func:`~swarm_observer.cost.compute.format_usd` from a ``Decimal``;
* a metric key or an authored metric value matching the alphabet
  ``detect.base`` already refuses to construct a :class:`Finding` outside of;
* a rate-snapshot key or version, whose bytes come from
  ``swarm_observer/cost/data/model_rates.json`` — package data, not the trace.

A pydantic validator rejects anything else, so a request holding a sentinel is
not a request that gets sent and cleaned; it is a request that cannot be
constructed. Nothing in the tree is an unconstrained ``str``.

Two consequences worth stating because they are *losses*:

* **Agents are identified by ``agent_index``, never by ``agent_id``.** R42
  permits "per-agent … totals" and an agent id is trace-derived (R5 takes it
  from the record's ``agentId``), so the integer R2 pins to
  ``range(len(agents))`` is what the narrator sees. The prose it writes says
  "agent 3", which is what the report's own lane legend says too.
* **``metrics.tool_name`` is dropped.** R42 explicitly permits it, and AC12
  explicitly forbids anything a sentinel trace can put in a free-text field
  from appearing in the payload — and a tool name is exactly that, since a
  sentinel like ``SENTINELSpantoolname`` satisfies R16's pattern perfectly.
  The two clauses of the same requirement pair disagree, this module takes the
  narrower one, and the disagreement is filed as **S33**.

``NarrationResponse.paragraph`` is, by contrast, a free ``str``. It is the
model's output, it is untrusted, and R43 has it validated then redacted then
escaped like any trace string. A constrained type there would be a claim about
a provider's behaviour, which is not a claim this package can make.

## The error taxonomy

R41: "all single-line messages that never echo request or response bodies."
That is enforced by construction rather than by discipline — a
:class:`NarratorError` carries a **code from a closed set** and nothing else,
so there is no parameter a provider's message could be passed through. An
adapter maps an SDK exception to a code by its *type*, never by its text.
"""

from __future__ import annotations

import re
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from swarm_observer.detect.base import (
    AUTHORED_METRIC_VALUE_PATTERN,
    SEVERITIES,
    TRACE_DERIVED_METRIC_KEYS,
    Severity,
)
from swarm_observer.detect.registry import ALL_DETECTORS, DETECTOR_SLUGS

#: The keys a ``severity_counts`` mapping may use, sorted, derived from the
#: detector contract rather than retyped.
SEVERITY_KEYS: tuple[str, ...] = tuple(sorted(SEVERITIES))

#: The payload format's own version. Bumping it is how a v2 narrator tells a
#: provider-side prompt template that the shape changed.
NARRATION_REQUEST_VERSION: Literal["1.0.0"] = "1.0.0"

#: R43: the group key for the one paragraph that is about the whole run rather
#: than about a detector. Spelled so it cannot collide with a detector slug —
#: :class:`~swarm_observer.detect.base.Finding` constrains those to
#: ``^[a-z][a-z0-9_]{0,63}$`` and the registry is checked below.
OVERALL_GROUP = "overall"

#: Every group key a request may name: the overall paragraph, then one per
#: registered detector in registry order (R13). Closed by construction.
NARRATION_GROUPS: tuple[str, ...] = (OVERALL_GROUP, *DETECTOR_SLUGS)

#: The human title shown for :data:`OVERALL_GROUP`. Package-authored.
OVERALL_TITLE = "the whole run"

#: Every ``title`` a request may carry — the registry's own, plus the one
#: above. A closed vocabulary rather than a free string, so a future edit that
#: interpolated something into a title would fail construction.
NARRATION_TITLES: frozenset[str] = frozenset(
    {OVERALL_TITLE, *(detector.title for detector in ALL_DETECTORS)}
)

#: R29's six-decimal money string, as a pattern. The payload carries money as
#: the exact string both reports print, never as a float.
MONEY_PATTERN = r"^-?[0-9]+\.[0-9]{6}$"

#: A metrics key. Keys are authored by the detector modules (R18-R24); R14
#: requires them sorted and :class:`Finding` refuses a non-authored string
#: *value* outright, so the alphabet below is the same one ``detect.base``
#: enforces.
METRIC_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

#: A rate-snapshot model key (R27). Its bytes come from the bundled
#: ``model_rates.json``, not from the trace; the pattern is a second line.
MODEL_KEY_PATTERN = r"^[a-z0-9][a-z0-9._\-]{0,63}$"

#: The rate snapshot's version string, matching ``SnapshotMeta.version``.
SNAPSHOT_VERSION_PATTERN = r"^[A-Za-z0-9._\-]{1,64}$"

#: R43: the longest paragraph a narrator may return, after normalization.
MAX_PARAGRAPH_CHARS = 800

#: How many findings of one group the payload describes individually. R42 puts
#: no bound on the payload and a trace can carry thousands of findings of one
#: kind, so an unbounded request is a request that is refused by a provider on
#: a large trace — the failure mode being a narrative that silently disappears
#: exactly when a run was worth narrating. The group's own counts and totals
#: are complete regardless; only the per-finding detail is capped (A-e5).
MAX_FINDINGS_PER_GROUP = 20

#: How many agent and model rows the payload carries, for the same reason,
#: taken in the report's own order (``agent_index``; then ``model_key``).
MAX_TOTAL_ROWS = 20

#: R41: every code a :class:`NarratorError` may carry. A closed set, because
#: "the message never echoes a request or response body" is a property this
#: package should not have to remember at each raise site. An adapter maps an
#: SDK exception to one of these by the exception's **type**.
NARRATOR_ERROR_CODES: frozenset[str] = frozenset(
    {
        # Raised by an adapter, before or during the call.
        "sdk_not_installed",
        "no_credentials",
        "auth_rejected",
        "rate_limited",
        "timeout",
        "transport_failed",
        "provider_error",
        "response_malformed",
        # Raised by the narrator, about a paragraph it was handed.
        "response_empty",
        "response_too_long",
        "response_control_characters",
        # The narrator's own: the client was never asked, or asked and the
        # script ran out of answers.
        "not_configured",
    }
)


class NarratorError(Exception):
    """A narrator call that did not produce a usable paragraph (R41).

    Carries a **code and nothing else**. R41 requires single-line messages that
    never echo a request or a response body, and the cheapest way to guarantee
    that is to give the exception nowhere to put one: there is no ``detail``
    parameter, and an unknown code is a :class:`ValueError` at the raise site
    rather than a free-text channel.

    Every subclass is one of R41's three, so ``except NarratorError`` in
    :mod:`~swarm_observer.narrate.narrator` catches the taxonomy whole — which
    is what makes R43's "all take the same path" true of the code and not only
    of the prose.
    """

    def __init__(self, code: str) -> None:
        if code not in NARRATOR_ERROR_CODES:
            raise ValueError(f"unknown narrator error code: {code!r}")
        self.code = code
        super().__init__(f"narrator: {code}")

    @property
    def line(self) -> str:
        """The one line a diagnostic may print. No body, no newline, ever."""
        return f"narrator: {self.code}"


class NarratorTransportError(NarratorError):
    """The provider could not be reached, or did not answer in time (R41, R43)."""


class NarratorAuthError(NarratorError):
    """No usable credential, or the provider refused the one offered (R41, R43)."""


class NarratorResponseError(NarratorError):
    """The provider answered with something this package cannot use (R41, R43)."""


class _Frozen(BaseModel):
    """Every payload model: frozen, ``extra="forbid"``, like the trace model."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class MetricEntry(_Frozen):
    """One ``Finding.metrics`` pair the narrator is allowed to see (R42).

    ``value`` is an ``int`` or an **authored** slug, and ``key`` is a metrics
    key R16 does *not* list as trace-derived. Both halves are refused here,
    which is the point of the class: :mod:`.summary` drops ``tool_name`` by
    consulting ``detect.base.TRACE_DERIVED_METRIC_KEYS``, and this model
    refuses it a second time so a future caller cannot add it back by hand.

    **The key check is load-bearing and was missing until the increment-5
    review (BUG-14).** It cannot be replaced by the value check, because the
    two alphabets are the same one: ``AUTHORED_METRIC_VALUE_PATTERN`` is
    ``[a-z][a-z0-9_]{0,63}``, and ``ghp_`` followed by twenty-four lowercase
    letters is inside it. A *shape* cannot tell a slug this package authored
    from a slug a trace supplied — only the **key** can, which is why R16
    enumerates the trace-derived keys in the first place. Without this branch
    the whole no-trace-content property rested on one ``if`` in the builder,
    in the module whose thesis is that it has no filters. See S33, S39.

    Both branches consult :data:`~swarm_observer.detect.base.TRACE_DERIVED_METRIC_KEYS`
    rather than naming ``tool_name``, so a second trace-derived metric key
    added in v2 is refused here without anybody remembering to.
    """

    key: str = Field(pattern=METRIC_KEY_PATTERN)
    value: int | str

    @model_validator(mode="after")
    def _key_and_value_are_authored(self) -> MetricEntry:
        if self.key in TRACE_DERIVED_METRIC_KEYS:
            raise ValueError(
                f"metrics[{self.key!r}] is a trace-derived key (R16); the narrator "
                "payload carries no trace-derived string (R42)"
            )
        if isinstance(self.value, str) and not re.fullmatch(
            AUTHORED_METRIC_VALUE_PATTERN, self.value
        ):
            raise ValueError(
                f"metrics[{self.key!r}] is not an authored slug; the narrator payload "
                "carries no trace-derived string (R42)"
            )
        return self


class FindingSummary(_Frozen):
    """One finding, as counts and slugs (R42).

    No ``summary``, no ``previews``, no ``agent_ids``, no ``span_seqs`` and no
    ``finding_id``. R42's list does not include them, and three of the five are
    trace-derived or hash trace-derived values.

    No per-finding cost either. R42's "contains exclusively" is an upper bound
    and its list stops at "per-agent and per-model token and cost totals", so a
    per-finding dollar figure would be a field the requirement does not admit
    — and, conveniently, it is the only thing that would have made this
    package need a ``Decimal`` and therefore ``cost``'s formatter (S34).
    """

    severity: Severity
    #: How many spans the finding names as evidence. The count, not the seqs.
    evidence_spans: int = Field(ge=0)
    #: How many distinct agents it implicates. The count, not the ids.
    agents: int = Field(ge=0)
    wasted_tokens: int = Field(ge=0)
    metrics: tuple[MetricEntry, ...] = ()


class GroupSummary(_Frozen):
    """One narration group: a detector, or the whole run (R43)."""

    group: str
    title: str
    findings: int = Field(ge=0)
    #: ``{severity: count}`` over the group, every severity present.
    severity_counts: dict[str, int] = Field(default_factory=dict)
    wasted_tokens: int = Field(ge=0)
    #: At most :data:`MAX_FINDINGS_PER_GROUP` of them, in report order.
    detail: tuple[FindingSummary, ...] = ()

    @model_validator(mode="after")
    def _closed_vocabulary(self) -> GroupSummary:
        if self.group not in NARRATION_GROUPS:
            raise ValueError(f"unknown narration group: {self.group!r}")
        if self.title not in NARRATION_TITLES:
            raise ValueError(f"title is not a registry title: {self.title!r}")
        if set(self.severity_counts) - set(SEVERITY_KEYS):
            raise ValueError("severity_counts may only key on the severity enum")
        if list(self.severity_counts) != sorted(self.severity_counts):
            raise ValueError("severity_counts keys must be sorted")
        if len(self.detail) > MAX_FINDINGS_PER_GROUP:
            raise ValueError(f"detail is capped at {MAX_FINDINGS_PER_GROUP} findings")
        return self


class AgentTotals(_Frozen):
    """One agent's cost line, keyed by index and never by id (R31, R42)."""

    agent_index: int = Field(ge=0)
    priced_spans: int = Field(ge=0)
    unpriced_spans: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: str = Field(pattern=MONEY_PATTERN)


class ModelTotals(_Frozen):
    """One resolved rate-snapshot key's cost line (R31, R42).

    ``model_key`` is a key out of ``swarm_observer/cost/data/model_rates.json``
    and never the recorded ``Span.model``, which is trace-derived (R30 calls it
    so). A span whose model did not resolve contributes to
    :attr:`TraceTotals.unpriced_spans` and brings no string with it.
    """

    model_key: str = Field(pattern=MODEL_KEY_PATTERN)
    priced_spans: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: str = Field(pattern=MONEY_PATTERN)


class TraceTotals(_Frozen):
    """What the run cost and how much of it could be priced (R31, R42)."""

    agents: int = Field(ge=0)
    spans: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    findings: int = Field(ge=0)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    tokens: int = Field(ge=0)
    cost_usd: str = Field(pattern=MONEY_PATTERN)
    priced_spans: int = Field(ge=0)
    unpriced_spans: int = Field(ge=0)
    by_agent: tuple[AgentTotals, ...] = ()
    by_model: tuple[ModelTotals, ...] = ()

    @model_validator(mode="after")
    def _bounded_and_sorted(self) -> TraceTotals:
        if set(self.severity_counts) - set(SEVERITY_KEYS):
            raise ValueError("severity_counts may only key on the severity enum")
        if list(self.severity_counts) != sorted(self.severity_counts):
            raise ValueError("severity_counts keys must be sorted")
        if len(self.by_agent) > MAX_TOTAL_ROWS or len(self.by_model) > MAX_TOTAL_ROWS:
            raise ValueError(f"cost rows are capped at {MAX_TOTAL_ROWS}")
        return self


class NarrationRequest(_Frozen):
    """One ask: "write the paragraph for this group" (R42).

    The whole payload is identical across a run's requests except for
    :attr:`group`, which is deliberate: the narrator sees the same figures for
    every paragraph it writes, so the paragraphs cannot disagree about the run,
    and a determinism test has one object to pin rather than N.
    """

    request_version: Literal["1.0.0"] = NARRATION_REQUEST_VERSION
    #: R42: "the rate snapshot version". Package data.
    rate_snapshot_version: str = Field(pattern=SNAPSHOT_VERSION_PATTERN)
    #: Which group this request wants a paragraph for.
    group: str
    totals: TraceTotals
    #: Every group, so each paragraph is written in the context of the others.
    groups: tuple[GroupSummary, ...] = ()

    @model_validator(mode="after")
    def _group_is_one_of_the_groups(self) -> NarrationRequest:
        if self.group not in NARRATION_GROUPS:
            raise ValueError(f"unknown narration group: {self.group!r}")
        keys = [group.group for group in self.groups]
        if self.group not in keys:
            raise ValueError("the requested group is not among the described groups")
        if len(set(keys)) != len(keys):
            raise ValueError("a group is described twice")
        return self


class NarrationResponse(_Frozen):
    """One paragraph, as the provider returned it (R41).

    Deliberately an unconstrained ``str``: this is model output, and a narrow
    type here would be a claim about a provider's behaviour rather than about
    this package's. :func:`~swarm_observer.narrate.narrator.validate_paragraph`
    is what decides whether it is usable, and the render boundary redacts and
    escapes it either way (R43).
    """

    paragraph: str


@runtime_checkable
class NarratorClient(Protocol):
    """R41: the one method ``--explain`` calls.

    Synchronous and single-shot on purpose. A narrator that retried, batched or
    streamed would make the number of outbound requests a function of something
    other than the number of groups, and R46's "the only outbound call in the
    product is ``--explain``" is easier to check when one group means one call.

    Implementations raise :class:`NarratorError` and nothing else for a failure
    they can classify; :mod:`~swarm_observer.narrate.narrator` additionally
    treats *any* exception from a third-party SDK as a fallback rather than
    letting it reach the CLI, because R43's exit-code guarantee cannot depend
    on a vendor's exception hierarchy.
    """

    def complete(self, request: NarrationRequest) -> NarrationResponse:
        """Write one paragraph about ``request.group``."""
        ...  # pragma: no cover - a Protocol body


__all__ = [
    "MAX_FINDINGS_PER_GROUP",
    "MAX_PARAGRAPH_CHARS",
    "MAX_TOTAL_ROWS",
    "METRIC_KEY_PATTERN",
    "MODEL_KEY_PATTERN",
    "MONEY_PATTERN",
    "NARRATION_GROUPS",
    "NARRATION_REQUEST_VERSION",
    "NARRATION_TITLES",
    "NARRATOR_ERROR_CODES",
    "OVERALL_GROUP",
    "OVERALL_TITLE",
    "SEVERITY_KEYS",
    "SNAPSHOT_VERSION_PATTERN",
    "AgentTotals",
    "FindingSummary",
    "GroupSummary",
    "MetricEntry",
    "ModelTotals",
    "NarrationRequest",
    "NarrationResponse",
    "NarratorAuthError",
    "NarratorClient",
    "NarratorError",
    "NarratorResponseError",
    "NarratorTransportError",
    "TraceTotals",
]
