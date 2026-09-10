"""Report rendering: the trace-derived-text boundary and the two output formats.

``redact.py`` and ``escape.py`` are the boundary; ``json_out.py``, ``html.py``
and ``timeline.py`` are the formats. Increment 3 ships ``redact.py`` and
``json_out.py``; the HTML renderer and the SVG timeline arrive with increment 4.

The rule the whole package is organised around (R32, R33, Modularity notes):
**a renderer never assumes its caller sanitized.** Every trace-derived string is
redacted at the point it enters an output document, by the one ``redact``
definition, and — for HTML only — escaped by the one ``escape_html``
definition. Guards are properties of functions, not of call paths.
"""

from __future__ import annotations

__all__: list[str] = []
