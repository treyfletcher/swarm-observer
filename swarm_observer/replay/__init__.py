"""The v2 replay seam. **Declared, not implemented** (Out of scope, T19).

This package contains exactly one module, :mod:`swarm_observer.replay.target`,
and that module contains a :class:`typing.Protocol` and two frozen models. There
is no implementation, no CLI subcommand, and nothing anywhere in
``swarm_observer/`` imports it — including this file, which is why it holds a
docstring and no re-exports. ``test_r44_nothing_imports_the_replay_seam``
asserts the last part.

The reason for a seam with nothing behind it is the one the spec gives for
every other Protocol in this package: a v2 replay target should be an addition
rather than a redesign, and the cheapest way to find out whether a shape can
carry a feature is to write the shape down while the model it depends on is
still fresh. The reason for *no implementation* is stronger, and it is this
project's own history: the predecessor shipped "a live-eval replay path that
could never have passed" — instance three of the signature defect — and a
replay implementation in v1 would be a code path no offline suite could
exercise, sitting next to the one optional path this increment already had to
argue for.
"""

from __future__ import annotations

__all__: list[str] = []
