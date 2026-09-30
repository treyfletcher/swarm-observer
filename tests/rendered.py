"""A parsed view of a rendered ``report.html`` — what a browser would see (R34, R51).

Not a test module.

R51 is explicit that its probe asserts "against the **rendered file as a browser
would see it**, parsed by ``html.parser``", and the spec's Security
considerations say why: draftsmith's increment-5 review found a guard asserted
against a function's return value while the document it produced was wrong. So
every assertion in this increment's security tests reads a :class:`Document`
built here, never ``render_html``'s string directly — with two deliberate
exceptions, both named at their call sites: R35's byte-level scan for ``//``
and ``@import``, which are invisible to a parser, and R47's byte-identity
comparisons.

Three things this module does that a naive parse does not:

* **``convert_charrefs=True``.** Text nodes come back *decoded*, so a probe
  asks "does this payload reach a text node?" in the payload's own bytes rather
  than in its escaped form. The escaped form is separately asserted at the byte
  level by AC4's single-pass check.
* **Attribute values are kept with the element they were on.** "Every attribute
  value is allowlisted" is a weaker statement than R34 makes if the failure
  message cannot say *where*, and the allowlist is keyed by attribute name
  only, so the element is the part a reader needs.
* **``<script>`` and ``<style>`` content is kept apart from text.** R51 requires
  a payload to appear *only* in a text node; script and style content are text
  nodes to a naive parser and are the one place a payload must never be.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: The element types whose character data is *not* a text node for R51's
#: purposes. ``title`` is included because its content is not rendered in the
#: document body and a payload hiding there would satisfy a naive "appears in a
#: text node" check without being visible to a reader.
RAW_TEXT_ELEMENTS: frozenset[str] = frozenset({"script", "style", "title"})


@dataclass
class Document:
    """Everything a probe needs from one rendered report."""

    source: str
    tag_counts: dict[str, int] = field(default_factory=dict)
    #: ``(element, attribute_name, value)`` for every attribute in the document.
    attributes: list[tuple[str, str, str]] = field(default_factory=list)
    #: Decoded character data outside :data:`RAW_TEXT_ELEMENTS`.
    text_nodes: list[str] = field(default_factory=list)
    #: Decoded character data inside each element type of :data:`RAW_TEXT_ELEMENTS`.
    raw_text: dict[str, list[str]] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)
    declarations: list[str] = field(default_factory=list)
    processing_instructions: list[str] = field(default_factory=list)
    #: ``(element, ordered attribute names)`` in document order — the head
    #: element-order check R34 makes about the CSP meta needs this.
    element_order: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Every text node, joined. The haystack for "appears in a text node"."""
        return "\n".join(self.text_nodes)

    def attribute_values(self) -> Iterator[tuple[str, str, str]]:
        yield from self.attributes

    def raw_of(self, element: str) -> str:
        """The concatenated character data of one raw-text element type."""
        return "".join(self.raw_text.get(element, ()))

    def script_body(self) -> str:
        """The one ``<script>``'s content with the renderer's framing newlines removed.

        ``render_html`` writes the script as three lines — ``<script>``, the
        constant, ``</script>`` — joined with ``\\n``, so the parsed character
        data is ``"\\n" + REPORT_SCRIPT``. R34 pins the SHA-256 of the constant,
        so the canonicalization has to be stated rather than guessed at, and it
        is stated here in one place: **strip exactly one leading newline, and
        nothing else**. ``lstrip`` would hide a newline an interpolation
        introduced; ``strip`` would hide a whole injected line.
        """
        return _strip_one_leading_newline(self.raw_of("script"))

    def style_body(self) -> str:
        """The one ``<style>``'s content, canonicalized like :meth:`script_body`."""
        return _strip_one_leading_newline(self.raw_of("style"))


def _strip_one_leading_newline(text: str) -> str:
    return text[1:] if text.startswith("\n") else text


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.document = Document(source="")
        self._stack: list[str] = []

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        counts = self.document.tag_counts
        counts[tag] = counts.get(tag, 0) + 1
        names = tuple(name for name, _ in attrs)
        self.document.element_order.append((tag, names))
        for name, value in attrs:
            self.document.attributes.append((tag, name, "" if value is None else value))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)
        self._stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._stack:
            while self._stack:
                if self._stack.pop() == tag:
                    break

    def handle_data(self, data: str) -> None:
        context = self._stack[-1] if self._stack else ""
        if context in RAW_TEXT_ELEMENTS:
            self.document.raw_text.setdefault(context, []).append(data)
        else:
            self.document.text_nodes.append(data)

    def handle_comment(self, data: str) -> None:
        self.document.comments.append(data)

    def handle_decl(self, decl: str) -> None:
        self.document.declarations.append(decl)

    def handle_pi(self, data: str) -> None:
        self.document.processing_instructions.append(data)


def parse(html: str) -> Document:
    """Parse a rendered report. The only door tests use to look at one."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    reader.document.source = html
    return reader.document


def sha256_text(text: str) -> str:
    """Lowercase hex SHA-256 of ``text`` as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def head_element_order(document: Document) -> list[tuple[str, tuple[str, ...]]]:
    """The elements of ``<head>``, in document order, ``<head>`` itself excluded."""
    order = document.element_order
    try:
        start = next(index for index, (tag, _) in enumerate(order) if tag == "head")
    except StopIteration:  # pragma: no cover - a document with no head
        return []
    result: list[tuple[str, tuple[str, ...]]] = []
    for tag, names in order[start + 1 :]:
        if tag == "body":
            break
        result.append((tag, names))
    return result


def section_ids_in_order(document: Document) -> list[str]:
    """The ``id`` of every ``<section>``, in document order (R36)."""
    ids: list[str] = []
    for element, name, value in document.attributes:
        if element == "section" and name == "id":
            ids.append(value)
    return ids


__all__ = [
    "RAW_TEXT_ELEMENTS",
    "Document",
    "head_element_order",
    "parse",
    "section_ids_in_order",
    "sha256_text",
]
