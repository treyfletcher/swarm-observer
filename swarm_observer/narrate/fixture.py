"""``FixtureNarratorClient`` — a scripted narrator, in the house style (R41).

The spend-sentinel/draftsmith shape: constructed from an ordered script of
responses and errors, recording every request it is handed, raising the script
entries that are exceptions, and raising :class:`AssertionError` when the
script runs out.

That last rule is the one worth reading. Script exhaustion is **not** product
behaviour and is deliberately not a :class:`NarratorError`: a
:class:`NarratorError` would be swallowed by R43's fallback, the run would
succeed, and a test that scripted the wrong number of answers would pass while
measuring something other than what it claimed. An :class:`AssertionError`
propagates through
:func:`~swarm_observer.narrate.narrator.narrate` — which catches
``NarratorError`` and ``Exception`` from the *client call* but re-raises
``AssertionError`` — so a miscounted script is a red test, which is what it is.

This client ships in the package rather than in ``tests/`` on purpose (R41
names it under ``narrate/``): AC12 is an acceptance criterion about the
product, the ``--explain`` path it exercises is the real one, and the CLI's
narrator seam takes any :class:`~swarm_observer.narrate.client.NarratorClient`.
It opens no socket, reads no environment variable and imports nothing optional.
"""

from __future__ import annotations

from collections.abc import Sequence

from swarm_observer.narrate.client import (
    NarrationRequest,
    NarrationResponse,
    NarratorError,
)

#: One entry of a fixture script: an answer, or the failure to answer.
ScriptEntry = NarrationResponse | NarratorError


class FixtureNarratorClient:
    """A :class:`~swarm_observer.narrate.client.NarratorClient` that reads from a list."""

    def __init__(self, script: Sequence[ScriptEntry]) -> None:
        #: The entries still to be served, in order.
        self._script: list[ScriptEntry] = list(script)
        #: Every request handed to :meth:`complete`, in call order. The
        #: sentinel test (AC12) reads this rather than a private attribute,
        #: because "what was sent" is the question the criterion asks.
        self.calls: list[NarrationRequest] = []

    @property
    def remaining(self) -> int:
        """How many script entries have not been served yet."""
        return len(self._script)

    def complete(self, request: NarrationRequest) -> NarrationResponse:
        """Serve the next script entry, recording the request first.

        The request is recorded **before** the entry is inspected, so a call
        that raises is still visible in :attr:`calls` — the sentinel test has
        to see the payload of a request the narrator then lost.
        """
        self.calls.append(request)
        if not self._script:
            raise AssertionError(
                f"FixtureNarratorClient script exhausted after {len(self.calls) - 1} call(s); "
                f"this run asked for the group {request.group!r} as well. "
                "That is a test bug, not product behaviour, which is why it is not a "
                "NarratorError: a NarratorError here would be swallowed by R43's fallback "
                "and the miscounted script would report green."
            )
        entry = self._script.pop(0)
        if isinstance(entry, NarratorError):
            raise entry
        return entry


def paragraph(text: str) -> NarrationResponse:
    """A one-line script entry, for readability at a call site."""
    return NarrationResponse(paragraph=text)


__all__ = ["FixtureNarratorClient", "ScriptEntry", "paragraph"]
