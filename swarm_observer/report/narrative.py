"""The narrative section's data, owned by ``report/`` (R36, R43).

R44 puts ``narrate`` and ``report`` on separate branches — ``report`` may
import ``model``, ``detect`` and ``cost``, and ``narrate`` may import ``model``
and ``detect`` — so neither can import the other's types. That is the right
boundary and it costs one small duplication: the narrator produces
``narrate.narrator.GroupNarration`` and the renderers consume
:class:`NarrativeParagraph`, and ``cli/main.py``, the one module allowed to see
both, maps between them.

The alternative — a shared type in ``model/`` — would have been worse. R2 pins
``model/trace.py``'s contents exactly and says "no other field exists on these
models in v1"; putting a rendering concern there to save a ten-line mapping
would make the trace schema a place where unrelated things accumulate.

This module is also where R43's two fallback markers live, **together**:

* :data:`FALLBACK_CLASS`, the DOM marker;
* :data:`FALLBACK_PREFIX`, the visible one.

They are emitted by one branch of one loop in ``report/html.py``, so a
paragraph cannot carry one without the other, and a narrator cannot produce a
paragraph that looks like a fallback by writing the prefix itself — its text is
placed *after* the prefix this package wrote, never instead of it.

Like ``report/sanitize.py`` (A-d2), this is a module in ``report/`` that R44's
layout block does not name. **PM**: the same ruling covers both, or the block
should say that ``report/`` may hold modules it does not list.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: R36, R43: the narrative section's anchor. Deliberately **not** a member of
#: ``report.html.SECTION_IDS``: that tuple drives the nav, and a nav entry
#: would be a byte outside ``<section id="narrative">…</section>`` that
#: ``--explain`` changed, which is exactly what R43's byte-identity clause
#: forbids. See A-e10.
NARRATIVE_SECTION_ID = "narrative"

#: The heading R36 gives the section.
NARRATIVE_SECTION_TITLE = "Narrative"

#: The class on a paragraph the narrator wrote.
NARRATIVE_CLASS = "narrative"

#: R43: "marked in the DOM with the fixed class ``narrative-fallback``".
FALLBACK_CLASS = "narrative-fallback"

#: R43: "and the visible prefix ``Deterministic summary:``". Verbatim, with no
#: trailing space — the renderer adds the separator, so the literal here can be
#: compared against R43's own text character for character.
FALLBACK_PREFIX = "Deterministic summary:"

#: What the section says about itself, before any paragraph. Two claims a
#: reader of a forwarded report needs: the prose is model-written, and the
#: model was never shown the trace. The prefix literal is deliberately *not*
#: repeated here — a probe searching the document for it should find it only
#: where it actually marks a fallback.
NARRATIVE_CAVEAT = (
    "This section is prose written by a language model from the findings' counts, "
    "severities and enumerated slugs. No trace text was sent to it. Its output is "
    "treated as untrusted: redacted and escaped like any trace string, and confined "
    "to text nodes. A paragraph carrying the deterministic-summary prefix was "
    "produced by swarm-observer itself rather than by the model."
)


class NarrativeParagraph(BaseModel):
    """One paragraph of the narrative section (R43)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``"overall"`` or a detector slug. Package-authored.
    group: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    #: The heading shown above the paragraph. The product path copies a
    #: registry title, but this module cannot say so: R44 puts ``narrate`` and
    #: ``report`` on separate branches, so ``narrate.client.NARRATION_TITLES``
    #: — the closed vocabulary the *payload* validates against — is not in
    #: scope here. So the field is not claimed to be authored: both renderers
    #: write it with the ``narrator`` kind, and it is redacted like the
    #: paragraph beneath it (**BUG-16**, increment-5 review).
    title: str = Field(min_length=1, max_length=120)
    #: The paragraph itself. **Untrusted**: model-produced unless
    #: :attr:`fallback`, and rendered through the ``narrator`` text kind either
    #: way, so a reader of ``html.py`` does not have to know which it is.
    text: str = Field(min_length=1)
    #: True when this is swarm-observer's deterministic template (R43).
    fallback: bool = False
    #: Why it fell back — an enumerated slug from the narrator's closed error
    #: set, or ``None``. Emitted in ``report.json`` only: R34 forbids putting
    #: it in an attribute and R43 does not ask for it in the HTML, where the
    #: class and the prefix are the markers that matter.
    reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def _only_a_fallback_may_carry_the_marker(self) -> NarrativeParagraph:
        """R43 (**BUG-15**): the visible marker is the renderer's to write.

        The second layer. ``narrate.narrator.validate_paragraph`` already
        refuses a narrator paragraph carrying
        :data:`FALLBACK_PREFIX`, so in the product this branch cannot be
        reached — the two predicates are the same test over the same
        normalized string. It is here anyway because the Modularity notes'
        rule is that a guard is a property of the function and not of the call
        path, and **no renderer may assume its caller sanitized**: that
        assumption is exactly what put ``SpanError.code`` and this model's own
        ``title`` into four reports. With both layers, "the visible marker
        appears only where the DOM class does" is true of the *type* the
        renderers consume, not only of the path that happens to build it.
        """
        if not self.fallback and FALLBACK_PREFIX.casefold() in self.text.casefold():
            raise ValueError(
                f"a paragraph that is not a fallback may not carry {FALLBACK_PREFIX!r}; "
                "the visible marker is swarm-observer's own (R43)"
            )
        return self


class Narrative(BaseModel):
    """Everything the narrative section renders, or nothing at all (R43).

    ``render_html`` and ``render_json`` take ``Narrative | None``. ``None`` is
    the no-``--explain`` case and must produce byte-identical documents to a
    build with the flag absent; an instance is the ``--explain`` case and must
    add a section and change nothing else.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    paragraphs: tuple[NarrativeParagraph, ...] = ()
    #: How many times the narrator client was actually called. Zero means it
    #: was never asked — a missing SDK, an absent key — which a reader should
    #: be able to tell from "it was asked and answered badly".
    calls: int = Field(default=0, ge=0)

    @property
    def fallbacks(self) -> int:
        """How many paragraphs are deterministic templates."""
        return sum(1 for paragraph in self.paragraphs if paragraph.fallback)

    @property
    def reasons(self) -> tuple[str, ...]:
        """The distinct fallback reasons, sorted — for the section's own note."""
        return tuple(
            sorted({p.reason for p in self.paragraphs if p.reason is not None}),
        )


__all__ = [
    "FALLBACK_CLASS",
    "FALLBACK_PREFIX",
    "NARRATIVE_CAVEAT",
    "NARRATIVE_CLASS",
    "NARRATIVE_SECTION_ID",
    "NARRATIVE_SECTION_TITLE",
    "Narrative",
    "NarrativeParagraph",
]
