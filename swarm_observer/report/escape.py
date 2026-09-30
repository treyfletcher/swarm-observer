"""``escape_html`` — the one escaping boundary (R32).

This module holds **exactly one** definition and the table it applies. R44
asserts that: a second ``escape_html`` anywhere under ``swarm_observer/``, or a
second character-replacement table, fails the suite. The reason is the
draftsmith ``single_line`` lesson quoted in the Modularity notes — shape
guarantees only compose when there is one implementation — and it is worth
restating in the place the rule is about:

    A renderer that escapes its own strings *sometimes* is a renderer whose
    safety property is a fact about its call graph rather than about its code.
    The next call path is the one that gets it wrong.

Three properties are requirements rather than taste:

* **The table is wider than a text node needs.** ``'``, ``/``, `````` ` ``````
  and ``=`` do nothing in a text node; they are here because they are what makes
  an unquoted or single-quoted *attribute* context break out, and R32's stated
  reason is that a guard tuned to the call path that exists is wrong for the
  next one. R34 forbids trace bytes in an attribute today. This table is what
  keeps that a defence in depth rather than a single point of failure.

* **One pass over code points, never sequential :meth:`str.replace`.** ``&`` is
  replaced first in R32's table, which under sequential replacement would
  re-enter every entity the later replacements produce and yield ``&amp;lt;``
  for ``<``. Building the output character by character makes double-escaping
  impossible by construction rather than by ordering discipline. AC4 asserts
  exactly this, by looking for ``&lt;`` and not ``&amp;lt;``.

* **It is applied exactly once, at the render boundary, and is deliberately
  not idempotent.** ``escape_html(escape_html(s))`` mangles ``s``; that is not
  a defect, it is what makes a double application visible. Redaction (R33) runs
  **before** it — a marker like ``[redacted:aws_key_id]`` contains no character
  this table touches, so the order is not load-bearing for the marker, but it is
  for the input: escaping first would turn ``TOKEN="secret"`` into
  ``TOKEN&#x3D;&quot;secret&quot;`` and R33's patterns would no longer match it.

**One interpreter-dependence, inherited from the requirement and flagged.**
R32 says "every other character that is not ``str.isprintable()`` becomes a
single space", and :meth:`str.isprintable` answers from the Unicode table
compiled into the running interpreter — 14.0 on CPython 3.11, 15.0 on 3.12. A
code point assigned in 15.0 is printable on 3.12 and a space on 3.11, so the
same trace can render two different documents on the two interpreters CI builds.
The requirement names the predicate explicitly, so this module transcribes it
and the fix is a spec amendment rather than a code change — the same disposition
increment 1 and the increment-2 review reached for R8, which names the same
predicate for previews. See S24 in the increment-4 PR write-up. Two things bound
the consequence, and neither is a reason to relax:

1. R8 already normalizes the three preview fields with this same predicate at
   *ingest*, so for the bulk of trace text the divergence is upstream of here and
   this module adds no new class of it.
2. ``TestCheckedInDataIsInterpreterStableR8`` refuses to let a code point
   unassigned on the older interpreter into any checked-in fixture or golden, so
   a golden file cannot become version-dependent without that test going red.

The fields that do **not** pass through R8 — ``Span.model``, ``stop_reason``,
``tool_name``, ``tool_use_id``, ``SpanError.detail``, ``AgentRun.description``
and ``SourceFile.name`` — are where a live hostile trace could still diverge
between interpreters. That is the honest scope of the flag.
"""

from __future__ import annotations

#: R32's table, verbatim. Order is irrelevant to the implementation below — a
#: single pass consults it once per code point — and is kept as the requirement
#: lists it so the two can be compared by eye.
ESCAPE_TABLE: dict[str, str] = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#x27;",
    "/": "&#x2F;",
    "`": "&#x60;",
    "=": "&#x3D;",
}

#: What every character R32 calls non-printable becomes. One space **per
#: character**, not one per run: R8 collapses runs of whitespace at ingest and
#: R32 does not repeat that instruction, so collapsing here would be a second,
#: unrequested normalization applied to strings R8 never touched.
NON_PRINTABLE_REPLACEMENT = " "


def escape_html(text: str) -> str:
    """Escape ``text`` for an HTML **text node** (R32).

    The single definition used by every renderer. Applied once, after
    :func:`~swarm_observer.report.redact.redact`, to every trace-derived string
    that reaches the document.

    Newline and tab are not :meth:`str.isprintable`, so they become spaces like
    any other control character. That is R32 as written and it is also what the
    HTML renderer wants: a trace string cannot introduce a line break into the
    document's source, so the rendered file's line structure is a function of
    the renderer alone — which is what makes a golden file's diff readable and
    a line-oriented grep over the output meaningful.
    """
    pieces: list[str] = []
    for char in text:
        replacement = ESCAPE_TABLE.get(char)
        if replacement is not None:
            pieces.append(replacement)
        elif char.isprintable():
            pieces.append(char)
        else:
            pieces.append(NON_PRINTABLE_REPLACEMENT)
    return "".join(pieces)


__all__ = ["ESCAPE_TABLE", "NON_PRINTABLE_REPLACEMENT", "escape_html"]
