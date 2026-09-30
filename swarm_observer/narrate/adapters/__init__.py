"""Provider adapters for the ``--explain`` narrator.

One module per provider, each importing its SDK **inside the call** so that
importing this package never requires the ``[explain]`` extra (R44, R45).
:mod:`swarm_observer.narrate.adapters.anthropic` is the only module in
``swarm_observer/`` permitted to import ``anthropic`` at all, which the R44 AST
test asserts by file path.
"""

from __future__ import annotations

__all__: list[str] = []
