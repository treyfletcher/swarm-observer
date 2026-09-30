"""Per-group validation and fallback — the part R43 is mostly about.

The contract, restated so the code below can be read against it:

* one paragraph per finding group (a group = one detector slug that actually
  produced a finding), **plus** one overall paragraph;
* each response validated: non-empty, at most
  :data:`~swarm_observer.narrate.client.MAX_PARAGRAPH_CHARS` characters after
  normalization, no control characters;
* **fallback is per group** — a group whose paragraph is missing, invalid, or
  lost to a :class:`~swarm_observer.narrate.client.NarratorError` renders the
  deterministic template paragraph instead;
* the run continues and **the exit code is unchanged**, always. ``--explain``
  cannot turn a 0 into a 1 and cannot turn a 1 into a 2.

## The one place R43 and AC12 needed reconciling

R43's third bullet ends: "A transport failure, an auth failure, a missing
``anthropic`` package, an absent API key, or a timeout all take the same path:
**every group falls back**." AC12 then scripts a client that raises
``NarratorTransportError`` on the *third* of four groups and returns a valid
paragraph for the fourth — which is only satisfiable if a transport failure
falls back for its own group and the loop continues.

The two are consistent under one reading, and it is the reading below: the
conditions R43 lists differ in *scope*, not in *path*. A missing SDK, an absent
credential and a rejected credential are properties of the run — asking again
cannot succeed — so the first one ends the narration and every remaining group
falls back with that same code, which is literally "every group falls back". A
transport failure, a timeout and an unusable response are properties of one
call, so the loop continues. Both take R43's fallback path, which is the clause
that matters, and neither changes the exit code. See A-e6.

## Two things this module deliberately does not do

It does not retry. A retry would make the number of outbound requests a
function of the provider's mood, and "one group, one call" is the property that
makes R46's egress claim checkable.

It does not let a vendor exception escape. ``except NarratorError`` catches the
taxonomy R41 defines, and a second ``except Exception`` catches everything an
SDK invents, because R43's exit-code guarantee cannot be contingent on a third
party's exception hierarchy. :class:`AssertionError` is re-raised: it is how
:class:`~swarm_observer.narrate.fixture.FixtureNarratorClient` reports a
miscounted script, and swallowing it would turn a broken test into a green one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from swarm_observer.detect.base import Finding
from swarm_observer.narrate.client import (
    MAX_PARAGRAPH_CHARS,
    OVERALL_GROUP,
    GroupSummary,
    NarrationRequest,
    NarratorClient,
    NarratorError,
    NarratorResponseError,
    TraceTotals,
)
from swarm_observer.narrate.summary import build_requests, narration_groups

#: The whitespace characters :func:`normalize_paragraph` folds, named by code
#: point rather than by ``str.isspace()``. The narrator is a **new** untrusted
#: string source and S24 is an open flag about ``str.isprintable()`` moving
#: between interpreters; introducing a second Unicode-table-dependent predicate
#: on the way in would widen a defect this project has already recorded.
#: ASCII whitespace has meant these six code points since 1963.
FOLDED_WHITESPACE = frozenset("\t\n\r\v\f ")

#: The codes that end a run's narration rather than one group's. Asking again
#: cannot succeed for any of them, so the first occurrence falls every
#: remaining group back with the same code — R43's "every group falls back",
#: for the conditions R43 lists that are properties of the run.
FATAL_CODES: frozenset[str] = frozenset({"sdk_not_installed", "no_credentials", "auth_rejected"})


def normalize_paragraph(text: str) -> str:
    """R43's "after normalization": fold ASCII whitespace runs, strip.

    Deliberately narrow. It removes the line structure a model likes to add —
    which matters because the HTML renderer writes one paragraph per line and a
    newline inside one would change the document's line structure — and it does
    nothing else. It does **not** remove control characters: a paragraph
    carrying an ESC is *rejected* below rather than quietly cleaned, because a
    narrator that emits terminal escapes is a narrator whose output a reader
    should be told about, not one whose output should be tidied.
    """
    pieces: list[str] = []
    space = False
    for char in text:
        if char in FOLDED_WHITESPACE:
            space = bool(pieces)
            continue
        if space:
            pieces.append(" ")
            space = False
        pieces.append(char)
    return "".join(pieces)


def has_control_characters(text: str) -> bool:
    """True when ``text`` holds a C0, DEL or C1 code point.

    Named by number — ``< 0x20``, ``0x7F``, ``0x80``-``0x9F`` — and not by
    Unicode category, for the reason :data:`FOLDED_WHITESPACE` gives. These
    ranges are fixed in every version of the standard, so this predicate
    answers the same on CPython 3.11 and 3.12 and will answer the same on 3.15.
    """
    return any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in text)


def validate_paragraph(text: str) -> str:
    """R43's three checks, in order, returning the normalized paragraph.

    Raises :class:`NarratorResponseError` with the code naming which check
    failed, so a fallback can say *why* it fell back — in ``report.json``,
    where a machine consumer can see it, and in the HTML title attribute of
    nothing at all, because R34 forbids putting it in an attribute.
    """
    normalized = normalize_paragraph(text)
    if not normalized:
        raise NarratorResponseError("response_empty")
    if has_control_characters(normalized):
        raise NarratorResponseError("response_control_characters")
    if len(normalized) > MAX_PARAGRAPH_CHARS:
        raise NarratorResponseError("response_too_long")
    return normalized


def _plural(count: int, noun: str) -> str:
    """``1 finding`` / ``2 findings``. Authored text, integers only."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _severity_phrase(counts: dict[str, int]) -> str:
    """``3 critical, 6 warning, 3 info`` — severity descending, always all three."""
    return ", ".join(f"{counts.get(name, 0)} {name}" for name in ("critical", "warning", "info"))


def _waste_phrase(tokens: int) -> str:
    """R17's attribution, with its honest label.

    Tokens and not dollars, because the payload carries no per-group money
    (S34) — and because R17 is explicit that this is an attribution rather
    than a saving, which a token count says more plainly than a price does.
    """
    return f"the attributed waste is {_plural(tokens, 'token')}"


def deterministic_paragraph(request: NarrationRequest, group: GroupSummary) -> str:
    """R43's template paragraph for one group.

    Built from the **request payload**, which is the same object the narrator
    would have been given — so the fallback is provably free of trace text for
    exactly the reason the request is (R42), and a reader comparing a model
    paragraph with a fallback paragraph is comparing two accounts of the same
    numbers.

    It carries no ``Deterministic summary:`` prefix. R43 requires that prefix
    to be **visible**, and it is emitted by the renderer beside the
    ``narrative-fallback`` class, so the two markers R43 pairs are written in
    one place and a narrator cannot produce a paragraph that carries one of
    them without the other.
    """
    totals = request.totals
    if group.group == OVERALL_GROUP:
        detectors = len(request.groups) - 1
        unpriced = (
            ""
            if totals.unpriced_spans == 0
            else f" {_plural(totals.unpriced_spans, 'model call')} could not be priced."
        )
        return (
            f"This run produced {_plural(totals.findings, 'finding')} from "
            f"{_plural(detectors, 'detector')}: {_severity_phrase(totals.severity_counts)}. "
            f"The trace holds {_plural(totals.agents, 'agent')}, "
            f"{_plural(totals.spans, 'span')} and "
            f"{_plural(totals.model_calls, 'model call')}, costing "
            f"{totals.cost_usd} USD at rate snapshot {request.rate_snapshot_version}."
            f"{unpriced} Across every detector, "
            f"{_waste_phrase(group.wasted_tokens)}."
        )
    return (
        f"{group.title} ({group.group}) produced "
        f"{_plural(group.findings, 'finding')}: {_severity_phrase(group.severity_counts)}. "
        f"For this detector, {_waste_phrase(group.wasted_tokens)}. "
        f"The findings section below carries each one's metrics and evidence spans."
    )


@dataclass(frozen=True)
class GroupNarration:
    """One rendered paragraph's provenance and text (R43)."""

    #: ``"overall"`` or a detector slug. Package-authored either way.
    group: str
    #: The registry title, or :data:`~swarm_observer.narrate.client.OVERALL_TITLE`.
    title: str
    #: The paragraph. Model-produced unless :attr:`fallback`, and treated as
    #: untrusted at the render boundary either way (R43).
    text: str
    #: True when this is the deterministic template rather than the narrator's.
    fallback: bool
    #: Why it fell back: a code from
    #: :data:`~swarm_observer.narrate.client.NARRATOR_ERROR_CODES`, or ``None``.
    reason: str | None = None


@dataclass(frozen=True)
class Narration:
    """Everything one ``--explain`` pass produced (R43)."""

    paragraphs: tuple[GroupNarration, ...]
    #: How many times the client was actually called. Zero when no client was
    #: configured, which is how "the narrator was never asked" is distinguished
    #: from "the narrator answered badly".
    calls: int

    @property
    def fallbacks(self) -> int:
        """How many groups rendered the deterministic template."""
        return sum(1 for paragraph in self.paragraphs if paragraph.fallback)


def narrate(
    *,
    client: NarratorClient | None,
    findings: Sequence[Finding],
    totals: TraceTotals,
    rate_snapshot_version: str,
    unavailable: str | None = None,
) -> Narration:
    """Ask for one paragraph per group, falling back per group (R43).

    ``client`` is ``None`` when no narrator could be built at all; ``unavailable``
    then names why and every group falls back with that code. This function
    raises nothing a caller has to handle, which is the whole of R43's
    exit-code guarantee: with ``--explain`` the run ends exactly as it would
    have without it.
    """
    groups = narration_groups(findings)
    requests = build_requests(
        findings=findings, totals=totals, rate_snapshot_version=rate_snapshot_version
    )
    summaries = {summary.group: summary for summary in requests[groups[0]].groups}

    paragraphs: list[GroupNarration] = []
    calls = 0
    stopped: str | None = None if client is not None else (unavailable or "not_configured")

    for group in groups:
        request = requests[group]
        summary = summaries[group]
        template = GroupNarration(
            group=group,
            title=summary.title,
            text=deterministic_paragraph(request, summary),
            fallback=True,
            reason=stopped,
        )
        if client is None or stopped is not None:
            paragraphs.append(template)
            continue
        calls += 1
        try:
            response = client.complete(request)
            text = validate_paragraph(response.paragraph)
        except AssertionError:
            # A fixture script that ran out of answers. Not product behaviour,
            # and swallowing it would make a miscounted script report green.
            raise
        except NarratorError as error:
            if error.code in FATAL_CODES:
                stopped = error.code
            paragraphs.append(_fell_back(template, error.code))
            continue
        except Exception:
            # Anything a vendor SDK invents. R43's guarantee cannot depend on a
            # third party's exception hierarchy, so the widest possible clause
            # sits here and nowhere else in the package.
            paragraphs.append(_fell_back(template, "provider_error"))
            continue
        paragraphs.append(
            GroupNarration(group=group, title=summary.title, text=text, fallback=False)
        )

    return Narration(paragraphs=tuple(paragraphs), calls=calls)


def _fell_back(template: GroupNarration, reason: str) -> GroupNarration:
    """The template paragraph, stamped with why the narrator's was not used."""
    return GroupNarration(
        group=template.group,
        title=template.title,
        text=template.text,
        fallback=True,
        reason=reason,
    )


__all__ = [
    "FATAL_CODES",
    "FOLDED_WHITESPACE",
    "GroupNarration",
    "Narration",
    "deterministic_paragraph",
    "has_control_characters",
    "narrate",
    "normalize_paragraph",
    "validate_paragraph",
]
