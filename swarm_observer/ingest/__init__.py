"""Trace ingestion: vendor formats in, the normalized model out (R3).

``ingest`` imports ``model`` and nothing else from the package, and nothing
outside ``ingest`` may import ``ingest.claude_code`` (R44). The CLI reaches an
adapter only through :mod:`swarm_observer.ingest.registry`, so a v2 source is a
new package here plus a registry entry.
"""

from __future__ import annotations
