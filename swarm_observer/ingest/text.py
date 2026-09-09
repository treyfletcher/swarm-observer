"""Preview normalization and slugging — the two trace-derived-text rules (R8, R10, R12).

Both functions exist exactly once. ``preview`` is the *only* way trace free
text becomes a model field, and ``slug`` is the only way a trace-derived token
becomes an enumerated value (a warning detail, a span error code). Keeping them
here rather than in the adapter is deliberate: a second adapter must reuse the
same normalization, and the R44 test can then assert there is no second
character-replacement table in the package.

Neither of these is an escaping function and neither pretends to be. Escaping
happens once, at the render boundary, in ``report/escape.py`` (R32); previews
are hostile text that has been *shortened and flattened*, not made safe.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: R8: previews are truncated to this many Unicode code points.
PREVIEW_LIMIT = 240

#: The character appended when a preview was truncated.
ELLIPSIS = "…"

#: R12: the cap on a slugged span error code.
SLUG_LIMIT = 40

_NON_SLUG = re.compile(r"[^a-z0-9_.:\-]")


def preview(value: str | None, limit: int = PREVIEW_LIMIT) -> str:
    """R8: flatten, collapse, strip and truncate one trace-derived string.

    Non-printable characters — control bytes, the bidirectional overrides, the
    Unicode separators — each become a single space before whitespace runs are
    collapsed, so a preview is always one line no matter what the trace holds.
    Truncation counts code points, not bytes, so a multi-byte character is never
    cut in half.
    """
    if not value:
        return ""
    flattened = "".join(char if char.isprintable() else " " for char in value)
    collapsed = " ".join(flattened.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + ELLIPSIS


def canonical_json(value: Any) -> str:
    """R7: the canonical serialization a tool input is digested and previewed from.

    A value that is not JSON-serializable (which the reader's ``json.loads``
    cannot produce, but a future adapter could hand in) degrades to the literal
    ``"null"`` rather than raising, so digesting never becomes a crash path.
    """
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError):  # pragma: no cover - unreachable via json.loads
        return "null"


def slug(value: str | None, limit: int = SLUG_LIMIT, *, fallback: str = "unknown") -> str:
    """Reduce a trace-derived token to an enumerated-looking slug (R10, R12).

    Lowercased, every character outside ``[a-z0-9_.:-]`` replaced with ``_``,
    capped. The result is what may appear in a ``ParseWarning.detail`` or a
    ``SpanError.code``; the original — which is attacker-controlled — goes to a
    preview field or nowhere at all.
    """
    if not value:
        return fallback
    lowered = _NON_SLUG.sub("_", value.lower())
    trimmed = lowered[:limit]
    return trimmed or fallback


__all__ = ["ELLIPSIS", "PREVIEW_LIMIT", "SLUG_LIMIT", "canonical_json", "preview", "slug"]
