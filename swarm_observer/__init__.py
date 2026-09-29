"""swarm-observer: post-hoc, zero-instrumentation observability for agent runs.

The package is laid out as a set of one-way seams (R44, Modularity notes):
``model`` depends on nothing but stdlib and pydantic, ``ingest`` maps a
vendor trace format onto ``model``, ``detect``/``cost``/``report``/``narrate``
see only the normalized model, and ``cli`` is the one place adapters are
selected. An AST test asserts every one of those rules rather than trusting
review.

``__version__`` is the string the report's provenance line carries (R47); it is
never derived from a clock or an environment value.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
