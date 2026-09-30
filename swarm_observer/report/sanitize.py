"""The redaction *policy* both renderers apply, in one place (R33, R38, A10).

``report/redact.py`` owns *what a credential looks like*. This module owns
*which of a report's strings are untrusted and what happens to each class of
them* — a distinct decision, and the one this project has now got wrong three
times running:

* **increment 1** — a trace-derived field bypassing redaction entirely;
* **increment 2 (S13)** — a credential-shaped string that is a *legal* tool name
  under R16's pattern, reaching ``metrics.tool_name`` verbatim;
* **increment 3 (BUG-2)** — ``agent_id``, ``parent_agent_id`` and
  ``ParseWarning.detail`` reaching ``report.json`` raw in **both** modes,
  because their *type* looks like an identifier rather than like text.

Each time the fix was correct and each time it lived inside the one renderer
that existed. Increment 4 adds a second renderer over the same fields, so the
policy moves here and both renderers call it. ``report/json_out.py`` re-exports
every name below, so nothing that imported them from there has to change.

The policy is a **partition of untrusted strings into two classes**, which is
the increment-3 review's ruling on S16 expressed as code:

* :func:`free_text` — previews, descriptions, recorded model ids, stop reasons,
  tool names, tool-use ids, error details. Redacted; **blanked** under
  ``--no-previews``. This is what the flag is for (R38, A10).
* :func:`identifier` — ``agent_id``, ``parent_agent_id``, a finding's
  ``agent_ids``, ``ParseWarning.detail``, ``metrics.tool_name`` and
  ``SourceFile.name``. Redacted; **never blanked**, in either mode. Blanking a
  join key collapses ``spans``, ``agents`` and ``cost.by_agent`` into one row
  and produces a document that is unreadable rather than redacted; blanking a
  warning's detail merges the ``(code, detail)`` pairs R10 aggregates on; and
  blanking ``metrics`` makes the printed ``finding_id`` unverifiable from the
  printed evidence, because R15 hashes ``metrics`` into it (A-c7).

Escaping is **not** here. R32's ``escape_html`` is the HTML renderer's boundary
and JSON has its own quoting, so a shared "sanitize" that did both would make
the JSON report escape entities it must not. Each renderer applies this module
first and then whatever its own output format requires — which is the
Modularity notes' rule that a guard is a property of the function, not of the
call path.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from swarm_observer.detect.base import TRACE_DERIVED_METRIC_KEYS
from swarm_observer.report.redact import redact


class RenderOptions(BaseModel):
    """The flags whose values change what a report contains (R38, R47).

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


def free_text(value: str, *, previews: bool) -> str:
    """One trace-derived free-text string, as it may appear in a report.

    Two rules meet here, and both are properties of *this function* rather than
    of whoever called it:

    * ``--no-previews`` (R38, A10) omits trace free text **entirely**. R8's
      three preview fields are already blanked at ingest, so for those this is a
      no-op; it is not a no-op for ``Span.model``, ``stop_reason``,
      ``tool_name``, ``tool_use_id``, ``SpanError.detail`` and the agent
      strings, none of which R8 covers and every one of which R51 names as a
      field its hostile corpus loads with payloads. Before this guard, a
      ``--no-previews`` run of ``hostile.jsonl`` still carried
      ``"><img src=x onerror=alert(1)>`` four times, as the recorded model id
      (A-c6). It costs R30 the recorded model id in the unpriced table under
      that flag — a tension the PM should settle (S16, C1).
    * Otherwise, R33's redaction, applied here at the boundary.
    """
    if not previews:
        return ""
    return redact(value)


def optional_text(value: str | None, *, previews: bool) -> str | None:
    """:func:`free_text` for a field whose absence is distinct from its emptiness."""
    return None if value is None else free_text(value, previews=previews)


def identifier(value: str) -> str:
    """A trace-derived string a report also uses as a **key** (R33).

    Redacted, in both modes. See the module docstring for why this class is not
    blanked under ``--no-previews`` and for the three increments that each found
    a member of it bypassing the boundary.

    The residual gap is real and is the PM's: an identifier can still carry
    attacker-chosen bytes under ``--no-previews``, which R38's "entirely" does
    not admit. The review's ruling on S16 proposes rendering an identifier as a
    16-hex digest of itself under the flag, which keeps every join and closes the
    gap; it is a behaviour change in a pinned taxonomy and is not implemented
    here. What increment 4 adds is that these strings are **also escaped** at the
    HTML boundary like every other untrusted string, and that the two fields
    whose alphabet makes them safe in an attribute are named as relying on that
    alphabet rather than assumed safe — see ``report/html.py``.
    """
    return redact(value)


def metric_value(key: str, value: int | str) -> int | str:
    """R16 + S13: redact the one trace-derived ``metrics`` value.

    R16 constrains ``tool_name`` by *shape*, and a shape check is not a secret
    check: ``AKIAIOSFODNN7EXAMPLE`` and ``sk-ant-api03-…`` are both legal tool
    names under R16's pattern. R51 promises credential-shaped payloads appear
    nowhere in a rendered report, so the redactor has to run over these values
    and not only over ``previews``. :data:`TRACE_DERIVED_METRIC_KEYS` is the
    machine-readable list of which keys those are, rather than a sentence in a
    docstring that the next renderer's author has to remember — and "the next
    renderer" is no longer hypothetical, which is why this moved out of
    ``json_out``.
    """
    if key in TRACE_DERIVED_METRIC_KEYS and isinstance(value, str):
        return redact(value)
    return value


__all__ = [
    "RenderOptions",
    "free_text",
    "identifier",
    "metric_value",
    "optional_text",
]
