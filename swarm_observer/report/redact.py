"""``redact`` — the one credential-shaped-substring replacement (R33).

**Redaction is a courtesy, not a boundary.** It reduces accidental credential
exposure in a report somebody forwards to a colleague. It cannot defeat an
adversary who controls the trace and wants a secret rendered — base64 it,
reverse it, split it across two tool results, and every pattern below misses.
The actual boundaries are R32's escaping, R34's text-node-only rule and R38's
``--no-previews``, which removes trace free text at ingest so the bytes are not
in the process at all (A10).

Two properties are requirements rather than niceties:

* **Order.** The table is applied in R33's exact order. ``anthropic_key`` before
  ``openai_key`` is the visible reason — ``sk-ant-…`` would otherwise be split
  by the narrower pattern — but the general reason is that a replacement
  rewrites the string the next pattern sees, so the order is part of the output.
* **Idempotence.** ``redact(redact(s)) == redact(s)`` for every ``s``. Every
  replacement is a fixed ``[redacted:<label>]`` literal that no pattern matches
  — except ``secret_assignment``, whose bare ``\\S{6,}`` value alternative
  re-matches its own marker **and whatever follows it without a space**. It did
  not reproduce the match unchanged: it swallowed the trailing text, so a second
  pass deleted report characters and could erase an *earlier* marker, destroying
  the evidence that a credential had been found there. See
  :func:`_replace_assignment` for the one condition that fixes it. Asserted as a
  property over a generated corpus, not argued.

One pattern is **not** a verbatim transcription, and it is the only one: R33's
``private_key`` gained an unterminated alternative (S14). R8 truncates a preview
at 240 code points, so a long key loses the terminator the paired pattern
requires and reached ``report.json`` intact for the whole of increment 3. See
:data:`PRIVATE_KEY_UNTERMINATED` for the ordering and the cost.

``secret_assignment`` is the one entry that keeps part of what it matched: R33
says "the name is preserved, the value replaced", because ``AWS_SECRET_ACCESS_KEY``
being present is the useful half of the evidence and its value is the dangerous
half. The separator the trace used is preserved with the name, so the redacted
line still reads as the assignment it was.

This module is *not* an escaping function and does not pretend to be. Its
output is still hostile text; HTML escaping happens once, afterwards, at the
render boundary (R32).
"""

from __future__ import annotations

import re

#: R33's ``private_key`` pattern, transcribed verbatim: a complete PEM block,
#: header through terminator, matched lazily so two blocks in one string are two
#: matches rather than one.
PRIVATE_KEY_PAIRED = r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----"

#: **S14.** The same header with **no** terminator, running to the end of the
#: string. R8 caps a preview at 240 code points and R33's paired pattern needs
#: both markers, so a private key longer than the remaining budget loses its
#: ``-----END … KEY-----`` and the paired pattern cannot match it: before this,
#: ``report.json`` for ``hostile.jsonl`` carried
#: ``-----BEGIN RSA PRIVATE KEY----- MIIBOgIBAAJBAK5f000…`` verbatim, and R51
#: promises that block "appears nowhere".
#:
#: Ordered **after** the paired form inside one alternation, which is the
#: increment-3 review's ruling on S14 expressed as a regex rather than as a
#: second table row: Python's ``|`` tries the left branch first at each starting
#: position, so a *complete* block still redacts as one unit and only a block
#: with no terminator reaches this branch. The alternative — a second
#: ``private_key`` entry in the table — was rejected because it makes
#: ``dict(REDACTION_PATTERNS)`` lossy and :data:`REDACTION_LABELS` carry a
#: duplicate, i.e. it breaks the table's own shape to add a row to it.
#:
#: The cost, stated rather than hidden: a string that merely *mentions* a PEM
#: header loses everything after it. Redaction is not reversible and a courtesy
#: that over-redacts a preview is strictly better than one that ships a key.
#: The fix is deliberately not "lengthen previews" — the same hole reopens at
#: the next cap, which is the coder-and-reviewer consensus recorded in S14.
PRIVATE_KEY_UNTERMINATED = r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*"

#: R33: the ordered pattern table. Each entry is ``(label, compiled pattern)``
#: and the label is what appears in the replacement marker.
#:
#: The patterns are transcribed verbatim from R33, with the single documented
#: exception of ``private_key``'s S14 alternation above. Where a pattern below
#: looks over-broad or under-broad, that is the requirement's call and changing
#: it here would put the implementation and the spec out of step silently.
REDACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(f"{PRIVATE_KEY_PAIRED}|{PRIVATE_KEY_UNTERMINATED}"),
    ),
    ("aws_key_id", re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{16}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    ),
    (
        "bearer",
        re.compile(
            r"(?i)\b(?:authorization\s*:\s*bearer\s+|bearer\s+)[A-Za-z0-9._\-]{20,}",
        ),
    ),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b([A-Za-z0-9_]*"
            r"(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY)"
            r"[A-Za-z0-9_]*)\s*[=:]\s*(?:\"[^\"\n]{6,}\"|'[^'\n]{6,}'|\S{6,})",
        ),
    ),
)

#: The labels, in table order — the closed set a marker may name.
REDACTION_LABELS: tuple[str, ...] = tuple(label for label, _ in REDACTION_PATTERNS)

#: The label whose replacement keeps the matched name and separator (R33).
NAME_PRESERVING_LABEL = "secret_assignment"


def marker(label: str) -> str:
    """The exact replacement text for ``label`` (R33)."""
    return f"[redacted:{label}]"


def _replace_assignment(match: re.Match[str]) -> str:
    """R33's ``secret_assignment`` replacement: keep the name and separator.

    The pattern is transcribed verbatim from R33 and has one capturing group —
    the name — so the separator is recovered from the matched text rather than
    by adding a group the requirement does not describe. Everything from the end
    of the name up to and including the first ``=`` or ``:`` is kept, which
    preserves ``FOO_TOKEN: `` as well as ``FOO_TOKEN=``; the value after it is
    replaced whole.

    **The one condition below is R33's idempotence clause** (review, BUG-3). A
    value that *already begins with this function's own marker* is left exactly
    as it was found. Without it, ``TOKEN="abcdefg"X`` redacts once to
    ``TOKEN=[redacted:secret_assignment]X`` and a second time to
    ``TOKEN=[redacted:secret_assignment]`` — the bare ``\\S{6,}`` alternative is
    greedy and unanchored, so it swallows the marker together with the ``X``,
    silently deleting a character of report text. The worse form is
    ``PASSWORD='secret'AKIAIOSFODNN7EXAMPLE``, where the second pass erases the
    ``[redacted:aws_key_id]`` marker the first pass produced and destroys the
    evidence that a credential was there.

    R33 pins two things about this table — the pattern text, and that ``redact``
    is idempotent — and they are in tension. The pattern is left verbatim and
    the fix is in the replacement, which R33 describes only as "the name is
    preserved, the value replaced": both readings of that sentence keep the
    name, and only this one keeps it stable under a second pass.

    The cost is stated rather than hidden: a trace that contains the literal
    text ``NAME=[redacted:secret_assignment]<secret>`` shields that one value.
    Redaction is a courtesy, not a boundary — an adversary who controls the
    trace need only avoid a secret-shaped *name*, which costs them nothing — and
    deleting report text is the worse of the two failures.
    """
    whole = match.group(0)
    name = match.group(1)
    rest = whole[len(name) :]
    separator = 0
    while separator < len(rest) and rest[separator] not in "=:":
        separator += 1
    kept = rest[: separator + 1]
    if rest[separator + 1 :].lstrip().startswith(marker(NAME_PRESERVING_LABEL)):
        return whole
    return f"{name}{kept}{marker(NAME_PRESERVING_LABEL)}"


def redact(text: str) -> str:
    """Replace every credential-shaped substring with ``[redacted:<label>]`` (R33).

    Applied to every trace-derived string **before** it reaches an output
    document, and — for HTML — before :func:`escape_html`. Idempotent by
    construction; see the module docstring for why.
    """
    redacted = text
    for label, pattern in REDACTION_PATTERNS:
        if label == NAME_PRESERVING_LABEL:
            redacted = pattern.sub(_replace_assignment, redacted)
        else:
            redacted = pattern.sub(marker(label), redacted)
    return redacted


__all__ = [
    "NAME_PRESERVING_LABEL",
    "PRIVATE_KEY_PAIRED",
    "PRIVATE_KEY_UNTERMINATED",
    "REDACTION_LABELS",
    "REDACTION_PATTERNS",
    "marker",
    "redact",
]
