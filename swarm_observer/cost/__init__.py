"""Pricing: the rate seam, the bundled snapshot and the cost engine (R26-R31).

Three modules, one rule each:

* ``source.py`` is the seam. :class:`~swarm_observer.cost.source.RateSource` is
  the whole contract a v2 live or historical-rate adapter has to satisfy, and
  ``compute.py`` is written against it rather than against the snapshot.
* ``snapshot.py`` is the only implementation in v1, reading the versioned
  package-data file ``data/model_rates.json``. Rates are stored as strings and
  become :class:`~decimal.Decimal` exactly once, at load.
* ``compute.py`` owns R28's formula, R29's Decimal discipline, R30's unpriced
  taxonomy and R31's aggregations.

The package imports ``model`` and nothing else of swarm-observer's (R44), and
contains no float literal and no ``float()`` call — asserted by a test, because
"money is Decimal from load to format" is a property a single ``/ 2`` would
quietly break.
"""

from __future__ import annotations

__all__: list[str] = []
