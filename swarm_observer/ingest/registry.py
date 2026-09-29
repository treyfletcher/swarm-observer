"""Adapter slug → :class:`TraceSource` (R3).

The registry is the only thing the CLI knows about adapters. Adding an OTel or
SDK source in v2 means a new package under ``ingest/`` and one entry here, with
no edit to ``model``, ``detect``, ``cost`` or ``report`` — which is the property
the R44 import-boundary test exists to keep true.

R3 pins the registry as a ``dict[str, TraceSource]``, and that is what
:data:`ADAPTERS` is: ready-to-use, default-configured instances. Per-run options
(``--no-previews``, R38/A10) need a differently constructed instance, so
:func:`build_adapter` builds one from the same slug table rather than mutating
a shared object — an adapter instance is configuration, not state, and two
concurrent runs must never see each other's flags.
"""

from __future__ import annotations

from swarm_observer.ingest.claude_code.mapper import ADAPTER_SLUG, ClaudeCodeSource
from swarm_observer.ingest.source import TraceSource

#: The slug → constructor table. Private so callers go through the two names
#: below and a new adapter has exactly one place to appear.
_ADAPTER_TYPES: dict[str, type[ClaudeCodeSource]] = {ADAPTER_SLUG: ClaudeCodeSource}

#: R3: the registry, keyed by adapter slug.
ADAPTERS: dict[str, TraceSource] = {slug: factory() for slug, factory in _ADAPTER_TYPES.items()}

#: The adapter used when ``--adapter`` is not given (R38).
DEFAULT_ADAPTER = ADAPTER_SLUG


def adapter_slugs() -> tuple[str, ...]:
    """Every registered slug, sorted — the choices ``--adapter`` accepts."""
    return tuple(sorted(_ADAPTER_TYPES))


def build_adapter(
    slug: str,
    *,
    no_previews: bool = False,
    read_sidecars: bool = True,
) -> TraceSource:
    """Construct the adapter named by ``slug`` with per-run options.

    Raises :class:`KeyError` for an unknown slug; the CLI turns that into R39's
    usage error (exit 3) rather than a fail-closed exit 2, because naming an
    adapter that does not exist is a mistake in the command, not in the trace.
    """
    factory = _ADAPTER_TYPES[slug]
    return factory(no_previews=no_previews, read_sidecars=read_sidecars)


__all__ = ["ADAPTERS", "DEFAULT_ADAPTER", "adapter_slugs", "build_adapter"]
