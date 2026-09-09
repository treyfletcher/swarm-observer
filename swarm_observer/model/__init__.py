"""The normalized, source-agnostic trace model (R1, R2).

This subpackage imports stdlib and pydantic only. It is the single type the
detectors, the cost engine and the renderers see; no adapter field name may
appear here or anywhere else outside ``swarm_observer.ingest`` (R1, R44).
"""

from __future__ import annotations
