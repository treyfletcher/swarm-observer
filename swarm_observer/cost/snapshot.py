"""``SnapshotRateSource`` — the bundled rate file and R27's resolution ladder (R26, R27).

The file is package data (``cost/data/model_rates.json``), read through
:mod:`importlib.resources` so it is found the same way from a source checkout,
an installed wheel and a zipapp, and never through a path relative to the
current working directory (R47).

The ladder in :meth:`SnapshotRateSource.resolve_model_key` is the part worth
reading carefully, because getting its **order** wrong does not fail — it
silently prices a call at another model's rate:

1. exact key in ``models``;
2. exact key in ``aliases``;
3. the **longest** key ``k`` in ``models`` where the recorded id is ``k`` or
   starts with ``k + "-"`` — this is what maps the real, date-suffixed
   ``claude-haiku-4-5-20251001`` onto ``claude-haiku-4-5``;
4. unresolved.

Rung 3's "longest" is the load-bearing word. ``claude-opus-4-1-20250805``
matches both ``claude-opus-4`` and ``claude-opus-4-1``; the shorter key is a
different model at five times the price. The ``+ "-"`` is equally load-bearing
in the other direction: without it ``claude-opus-45-preview`` would resolve to
``claude-opus-4``, because a plain ``startswith`` cannot tell a version
separator from more digits.

Resolution is pure string work: no network, no clock, no heuristic beyond the
four rungs.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from importlib import resources

from pydantic import ValidationError

from swarm_observer.cost.source import (
    RateSnapshot,
    RateSnapshotError,
    SnapshotMeta,
)

#: The package-data file every default run prices from (R26, Dependencies).
SNAPSHOT_PACKAGE = "swarm_observer.cost.data"
SNAPSHOT_FILENAME = "model_rates.json"


def parse_snapshot(text: str) -> RateSnapshot:
    """Parse and validate one snapshot document (R27).

    Every failure is a :class:`RateSnapshotError` with an enumerated code and a
    detail drawn from a closed vocabulary. Pydantic's own message is
    deliberately **not** forwarded: it quotes the offending value, and a rate
    file that fails to load must not print its own contents any more than a
    trace may (R11's discipline applied to package data).
    """
    try:
        payload = json.loads(text)
    except ValueError:
        raise RateSnapshotError("rate_snapshot_unreadable", note="not valid JSON") from None
    if not isinstance(payload, dict):
        raise RateSnapshotError("rate_snapshot_unreadable", note="not a JSON object")
    try:
        return RateSnapshot.model_validate(payload)
    except ValidationError:
        raise RateSnapshotError(
            "rate_snapshot_invalid", note="does not match the R27 snapshot shape"
        ) from None


@lru_cache(maxsize=1)
def bundled_snapshot() -> RateSnapshot:
    """The snapshot shipped inside the package (R26).

    Cached because it is immutable and read on every run; the cache holds a
    frozen model, so it is a memo rather than shared mutable state.
    """
    try:
        text = (
            resources.files(SNAPSHOT_PACKAGE)
            .joinpath(SNAPSHOT_FILENAME)
            .read_text(encoding="utf-8")
        )
    except (OSError, ModuleNotFoundError):
        raise RateSnapshotError("rate_snapshot_missing", note=SNAPSHOT_FILENAME) from None
    return parse_snapshot(text)


class SnapshotRateSource:
    """A :class:`~swarm_observer.cost.source.RateSource` over a fixed snapshot (R26).

    Constructed with no argument it loads the bundled file; constructed with a
    :class:`~swarm_observer.cost.source.RateSnapshot` it prices from that one,
    which is how a test drives R27's ladder and R30's ``rate_key_missing`` arm
    without editing shipped package data.
    """

    def __init__(self, snapshot: RateSnapshot | None = None) -> None:
        self._snapshot = bundled_snapshot() if snapshot is None else snapshot
        # Computed once: R27 rung 3 walks these in longest-first order, and
        # rebuilding the ordering per span would make pricing quadratic in the
        # number of model keys for no reason.
        self._prefix_keys = self._snapshot.prefix_keys()

    @property
    def snapshot(self) -> RateSnapshot:
        """The snapshot this source prices from."""
        return self._snapshot

    @property
    def meta(self) -> SnapshotMeta:
        """R26: the snapshot identity every report renders beside a figure."""
        return self._snapshot.meta

    def model_keys(self) -> tuple[str, ...]:
        """Every model key in the snapshot, sorted — R31's ``by_model`` order."""
        return tuple(sorted(self._snapshot.models))

    def get_rate(self, model_key: str, price_key: str) -> Decimal | None:
        """R26: one price, or ``None`` when the model or the key is absent."""
        entry = self._snapshot.models.get(model_key)
        if entry is None:
            return None
        return entry.get(price_key)

    def resolve_model_key(self, recorded: str | None) -> str | None:
        """R27's four rungs, in order. ``None`` means R30's ``model_not_in_snapshot``."""
        if recorded is None:
            return None
        if recorded in self._snapshot.models:
            return recorded
        alias = self._snapshot.aliases.get(recorded)
        if alias is not None:
            return alias
        for key in self._prefix_keys:
            if recorded == key or recorded.startswith(key + "-"):
                return key
        return None


__all__ = [
    "SNAPSHOT_FILENAME",
    "SNAPSHOT_PACKAGE",
    "SnapshotRateSource",
    "bundled_snapshot",
    "parse_snapshot",
]
