"""The Claude Code JSONL adapter — the only place vendor field names appear (R1, R3).

Nothing outside :mod:`swarm_observer.ingest` may import this package (R44); the
CLI reaches it through :mod:`swarm_observer.ingest.registry` under the slug
``claude_code_jsonl``.
"""

from __future__ import annotations
