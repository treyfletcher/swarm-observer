"""The **one** boundary every string a report writes passes through (R32, R33, R38).

``report/redact.py`` owns *what a credential looks like*. This module owns
*which class of string this is, and therefore what happens to it* — a distinct
decision, and the one this project has now got wrong four times running:

* **increment 1** — a trace-derived field bypassing redaction entirely;
* **increment 2 (S13)** — a credential-shaped string that is a *legal* tool name
  under R16's pattern, reaching ``metrics.tool_name`` verbatim;
* **increment 3 (BUG-2)** — ``agent_id``, ``parent_agent_id`` and
  ``ParseWarning.detail`` reaching ``report.json`` raw in **both** modes,
  because their *type* looks like an identifier rather than like text;
* **increment 4 (review §1.1)** — ``SpanError.code``, raw in both reports and
  both modes, for the same reason.

Four fixes, each correct, each about the field that was reported.

## Why this module has one function and not three (the increment-4 review's C1)

The increment-4 review's chief observation: *escaping* was made universal in
increment 4 — every string the HTML renderer writes goes through
``escape_html`` whether or not it is trusted — and that half of the boundary has
**never** leaked. *Redaction* stayed a per-field choice between ``free_text``,
``identifier`` and "neither", and that half has leaked in every increment. The
difference is not diligence. It is that ``_t`` / ``_free`` / ``_ident`` was a
three-way decision a human made per call site, and "neither" was always
available by writing the shorter one.

So increment 5 collapses them into :func:`text`, whose ``kind`` is keyword-only,
has no default, and is exhaustive over a closed :data:`TextKind`:

* there is no way to render a string without naming its class — omitting
  ``kind`` is a **type error**, not a quieter boundary;
* the dispatch ends in :func:`typing.assert_never`, so adding a member to
  :data:`TextKind` without handling it is a type error too;
* ``grep 'kind="authored"'`` is the complete list of claims this package makes
  that a string is its own, and each one is a sentence somebody can check.

The classes, and what each is for:

``authored``
    A string this package computed: a heading, a column label, an enumerated
    slug from a closed enum, a digest, a caveat sentence, a ``Decimal``'s
    formatting. Not redacted — running the redactor over a string this package
    authored would blur where the guarantee lives — and, in the HTML renderer,
    escaped anyway (A-d1). **Every** entry in this class is a claim that no
    *unclassified* trace byte can reach the value; ``SpanError.code`` was in it
    for four increments and should not have been. There is exactly one call
    site where "unclassified" is doing work rather than "no trace byte at all"
    — the HTML findings table's metrics line, where :func:`metric_value` has
    already applied the classification one call up — and it is named there,
    with the reason it is not simply redacted twice.

``free``
    Trace-derived free text: previews, descriptions, recorded model ids, stop
    reasons, tool names, tool-use ids, error details. Redacted (R33), and
    **blanked** under ``--no-previews`` — that is what the flag is for (R38,
    A10).

``identifier``
    A trace-derived string a report also uses as a **key**: ``agent_id``,
    ``parent_agent_id``, a finding's ``agent_ids``, ``ParseWarning.detail``,
    ``SpanError.code``, ``metrics.tool_name`` and ``SourceFile.name``.
    Redacted, in **both** modes. Blanking a join key collapses ``spans``, the
    lane legend and ``cost.by_agent`` into one row each, merges the
    ``(code, detail)`` pairs R10 aggregates on, and makes the printed
    ``finding_id`` unverifiable from the printed evidence, because R15 hashes
    ``metrics`` into it. That is the increment-3 review's ruling on S16 and the
    increment-4 review's §1.1, expressed as a class rather than as a list.

``narrator``
    Increment 5's new untrusted source: a paragraph produced by a language
    model under ``--explain``. R43 says it "passes through ``redact`` then
    ``escape_html`` exactly like trace text", and it does. It is **not** blanked
    under ``--no-previews``, and that is a decision this enum forced into the
    open rather than a default: R42 guarantees the narrator was never shown a
    byte of trace free text, so its paragraph cannot contain any, and blanking
    it would empty the one section ``--explain`` exists to produce. See A-e9.

The residual gap in the ``identifier`` class is real and unchanged: an
identifier still carries attacker-chosen bytes under ``--no-previews``, which
R38's "entirely" does not admit. The increment-3 review's proposed resolution —
render an identifier as a 16-hex digest of itself under the flag — keeps every
join and closes the gap, and is a behaviour change in a pinned taxonomy. It is
the PM's, it is the suite's one deliberate xfail, and it is now a change to one
branch of one function rather than to a list of call sites.

Escaping is **not** here. R32's ``escape_html`` is the HTML renderer's boundary
and JSON has its own quoting, so a shared "sanitize" that did both would make
the JSON report escape entities it must not. Each renderer applies this module
first and then whatever its own output format requires — which is the
Modularity notes' rule that a guard is a property of the function, not of the
call path.
"""

from __future__ import annotations

from typing import Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field

from swarm_observer.detect.base import TRACE_DERIVED_METRIC_KEYS
from swarm_observer.report.redact import redact

#: The closed classification every rendered string belongs to. Exhaustive: the
#: dispatch in :func:`text` ends in :func:`typing.assert_never`, so a new member
#: is a type error until it is handled, and a call site that omits ``kind`` is a
#: type error because the parameter is keyword-only with no default.
TextKind = Literal["authored", "free", "identifier", "narrator"]

#: The four classes as named constants. Call sites use these rather than string
#: literals for one prosaic reason and one good one: an HTML renderer writes
#: almost everything inside an f-string, and on CPython 3.11 an f-string cannot
#: contain a quote of its own delimiter — so ``kind="authored"`` inside
#: ``f"…{w(x, kind="authored")}…"`` is a syntax error on the older interpreter
#: CI builds. The good reason is that ``grep KIND_AUTHORED`` then lists every
#: claim this package makes that a string is its own, across both renderers, in
#: one place.
KIND_AUTHORED: TextKind = "authored"
KIND_FREE: TextKind = "free"
KIND_IDENTIFIER: TextKind = "identifier"
KIND_NARRATOR: TextKind = "narrator"

#: :data:`TextKind`'s members as data, so a test can enumerate them without
#: re-typing the ``Literal``. Sorted, because everything here is sorted.
TEXT_KINDS: tuple[TextKind, ...] = (
    KIND_AUTHORED,
    KIND_FREE,
    KIND_IDENTIFIER,
    KIND_NARRATOR,
)


class RenderOptions(BaseModel):
    """The flags whose values change what a report contains (R38, R43, R47).

    Recorded in both documents because a reader who sees empty previews should
    be able to tell "``--no-previews`` was used" from "this trace had no text",
    and because a golden report is a function of the trace *and the flags*.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: False when ``--no-previews`` blanked trace free text at ingest (A10).
    previews: bool = True
    blocked_gap_seconds: int = Field(default=60, ge=0)
    #: The detector slugs this run was restricted to, in registry order.
    detectors: tuple[str, ...] = ()
    #: True when ``--explain`` was given. Recorded so a reader can tell "the
    #: narrator produced nothing" from "the narrator was never asked" (R43).
    explain: bool = False


def text(value: str, *, kind: TextKind, previews: bool) -> str:
    """One string, classified, on its way into a report (R33, R38, R43).

    The single place :func:`~swarm_observer.report.redact.redact` is called from
    either renderer, and the single place ``--no-previews`` blanks anything.

    ``previews`` is required for every ``kind``, including the three it cannot
    affect. That is deliberate: a caller who does not have the render options to
    hand is a caller who has not thought about which mode this string is being
    written in, and three of the four historic leaks reached the document under
    ``--no-previews`` as well as under the default.
    """
    if kind == "authored":
        return value
    if kind == "free":
        return redact(value) if previews else ""
    if kind == "identifier":
        return redact(value)
    if kind == "narrator":
        return redact(value)
    assert_never(kind)


def optional_text(value: str | None, *, kind: TextKind, previews: bool) -> str | None:
    """:func:`text` for a field whose absence is distinct from its emptiness."""
    return None if value is None else text(value, kind=kind, previews=previews)


def kind_for_metric(key: str) -> TextKind:
    """Which class a ``Finding.metrics`` value of this key belongs to (R16, S13).

    :data:`~swarm_observer.detect.base.TRACE_DERIVED_METRIC_KEYS` is the
    machine-readable list of which metric keys can carry a recorded value,
    rather than a sentence in a docstring the next renderer's author has to
    remember. Everything else is an enumerated slug ``Finding._authored_metrics``
    already refuses to construct out of anything but this package's own
    alphabet.
    """
    return "identifier" if key in TRACE_DERIVED_METRIC_KEYS else "authored"


def metric_value(key: str, value: int | str) -> int | str:
    """R16 + S13: one ``Finding.metrics`` value, classified by its key.

    R16 constrains ``tool_name`` by *shape*, and a shape check is not a secret
    check: ``AKIAIOSFODNN7EXAMPLE`` and ``sk-ant-api03-…`` are both legal tool
    names under R16's pattern. R51 promises credential-shaped payloads appear
    nowhere in a rendered report, so the redactor has to run over these values
    and not only over ``previews``.

    ``metrics`` is redacted in **both** modes and never blanked: R15 hashes it
    into the ``finding_id`` the same document prints, so a masked metric makes
    the printed id unverifiable from the printed evidence (A-c7, A-d3). Hence
    the fixed ``previews=True`` below — this class is mode-independent by
    ruling, and passing the flag through would suggest otherwise.

    Both renderers call **this** function and then, in the HTML renderer's
    case, escape the result as ``authored``. They do not re-classify the value
    at the writer, and the reason is a check that would otherwise stop being
    able to fail: R33 makes ``redact`` idempotent, so a second redaction at the
    boundary would produce identical bytes whether or not this call happened —
    and ``tests/canaries/test_canary_metrics_redaction_dropped.py``, whose
    whole job is to show this call is load-bearing, would go green with the
    call removed. One redaction, in one place, provably load-bearing, beats two
    that hide each other.
    """
    if isinstance(value, int):
        return value
    return text(value, kind=kind_for_metric(key), previews=True)


__all__ = [
    "KIND_AUTHORED",
    "KIND_FREE",
    "KIND_IDENTIFIER",
    "KIND_NARRATOR",
    "TEXT_KINDS",
    "RenderOptions",
    "TextKind",
    "kind_for_metric",
    "metric_value",
    "optional_text",
    "text",
]
