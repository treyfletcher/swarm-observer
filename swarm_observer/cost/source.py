"""The rate seam, the snapshot's shape, and the fail-closed rate error (R26, R27).

R26's decision — rates are a **bundled versioned snapshot, not a live lookup** —
is what makes R47's byte-identical guarantee possible at all: a value that can
change between two runs of the same command cannot appear in an output the spec
promises is reproducible. The `RateSource` protocol below is nevertheless the
seam, so a v2 historical-rate adapter is a new implementation with no edit to
``cost/compute.py``.

Three things here are load-bearing beyond their size.

:class:`RateSource` is the whole pricing contract. ``compute.py`` never imports
:mod:`swarm_observer.cost.snapshot`; it takes a ``RateSource`` and asks it three
questions.

**Rates are strings on disk and :class:`~decimal.Decimal` in memory, converted
exactly once.** :func:`parse_rate` refuses anything that is not a string, which
is the structural version of "no float ever touches money": a JSON number would
arrive as a Python ``float`` and be wrong by the time anyone looked at it.
``0.1`` is not ``0.1``, and a rate is multiplied by token counts in the
hundreds of millions.

**Provenance is per model, not per file.** Each entry in
:attr:`SnapshotMeta.sources` names the models it prices, its publisher URL and
the date it was read, and :class:`RateSnapshot` refuses to load unless every
model key is covered by exactly one source. A rate nobody can attribute cannot
be added to the file without the loader failing, which is the only version of
"do not invent rates" that survives the next person to edit the JSON.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, DecimalException
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

#: R27: the five price keys a rate entry may carry, in the order R28's formula
#: applies them. The order is fixed so a rendered term list is stable (R47).
PRICE_KEYS: tuple[str, ...] = (
    "input",
    "output",
    "cache_read",
    "cache_write_5m",
    "cache_write_1h",
)

#: R28: each :class:`~swarm_observer.model.trace.TokenUsage` component and the
#: price key that prices it. This mapping *is* R28's formula's structure, and it
#: is also what R30's ``rate_key_missing`` reads to decide which price keys a
#: given usage actually requires.
USAGE_PRICE_KEYS: tuple[tuple[str, str], ...] = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_input_tokens", "cache_read"),
    ("cache_creation_5m_tokens", "cache_write_5m"),
    ("cache_creation_1h_tokens", "cache_write_1h"),
)

#: The alphabet a snapshot model key or alias may use. Keys are authored by this
#: repository, not by a trace, and the constraint is what lets a resolved
#: ``model_key`` be rendered without escaping worry (R34's attribute allowlist
#: reads them as ``data-`` values in increment 4).
MODEL_KEY_PATTERN = r"^[a-z0-9][a-z0-9._\-]{0,63}$"

#: A snapshot date, and the ``as_of`` date of a source. ISO calendar dates only:
#: R47 forbids locale-dependent date formatting anywhere near output bytes.
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

#: A rate string on disk: a plain non-negative decimal, no exponent, no sign, no
#: separators. Deliberately narrower than :class:`~decimal.Decimal` accepts,
#: which also excludes ``NaN``, ``Infinity`` and ``1E+3``.
_RATE_TEXT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?$")

#: The alphabet a :class:`RateSnapshotError` detail may use — enumerated words
#: and slugs, never a byte of the file being complained about.
_DETAIL_PATTERN = re.compile(r"^[A-Za-z0-9 _.:\-]{0,120}$")


class RateSnapshotError(Exception):
    """The bundled rate snapshot is unusable (R26, R11's discipline).

    A snapshot fault is a packaging fault, not a trace fault, but it reaches the
    user the same way and so it obeys the same rule: one sanitized line, no
    traceback, exit 2. The detail is assembled from a code, an optional model
    key and an optional enumerated note; the constructor raises rather than
    print anything else, so a malformed file cannot spill its own bytes onto a
    terminal.
    """

    def __init__(self, code: str, *, model_key: str | None = None, note: str | None = None) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            raise ValueError("RateSnapshotError code must be an enumerated slug")
        if model_key is not None and not re.fullmatch(MODEL_KEY_PATTERN, model_key):
            raise ValueError("RateSnapshotError model_key must be a snapshot key")
        if note is not None and not _DETAIL_PATTERN.fullmatch(note):
            raise ValueError("RateSnapshotError note must be enumerated text, never file content")
        self.code = code
        self.model_key = model_key
        self.note = note
        pieces = [piece for piece in (model_key, note) if piece]
        self.detail = " ".join(pieces)
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)

    @property
    def cli_line(self) -> str:
        """The exact single line the CLI writes to stderr (R11's shape, R39)."""
        return f"swarm-observer: {self.code}: {self.detail}"


def parse_rate(value: Any) -> Decimal:
    """One JSON rate string → :class:`~decimal.Decimal`, exactly once (R26, R29).

    Refuses anything that is not a ``str``. That is the point: a JSON number
    reaches Python as a ``float``, and R29 says floats never enter the
    computation. Rejecting the *type* rather than coercing it means the file
    cannot carry a value that is already wrong before the engine sees it.
    """
    if isinstance(value, Decimal):  # already converted (a programmatic snapshot)
        return value
    if not isinstance(value, str):
        raise ValueError("a rate must be a decimal string, never a JSON number (R26, R29)")
    if not _RATE_TEXT.fullmatch(value):
        raise ValueError("a rate must be a plain non-negative decimal string")
    try:
        return Decimal(value)
    except DecimalException:  # pragma: no cover - the pattern already excludes these
        raise ValueError("a rate must be a plain non-negative decimal string") from None


#: A USD-per-1,000,000-tokens rate, stored as a string and parsed once.
RateValue = Annotated[Decimal, BeforeValidator(parse_rate)]


class _Frozen(BaseModel):
    """The shared configuration every model in ``cost/`` uses."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RateEntry(_Frozen):
    """One model's five prices, USD per 1,000,000 tokens (R27).

    Every key is optional at the *type* level and present in the shipped file.
    That gap is deliberate and it is R30's fourth reason: ``rate_key_missing``
    describes a model that resolved but whose entry lacks a price for a
    component the usage actually consumed. A model that made the field required
    would make that arm of the taxonomy unreachable — a branch nothing can
    execute, which is this project's signature defect.
    """

    input: RateValue | None = None
    output: RateValue | None = None
    cache_read: RateValue | None = None
    cache_write_5m: RateValue | None = None
    cache_write_1h: RateValue | None = None

    def get(self, price_key: str) -> Decimal | None:
        """The rate for ``price_key``, or ``None`` when the entry omits it."""
        if price_key not in PRICE_KEYS:
            return None
        value: Decimal | None = getattr(self, price_key)
        return value


class RateSourceRef(_Frozen):
    """Where a group of rates came from, and when it was read (A4, R26).

    ``models`` is the provenance link: a rate is attributable exactly when its
    model key appears in one of these lists. :class:`RateSnapshot` enforces that
    every model key does, so provenance cannot be forgotten for a rate somebody
    adds later.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=400)
    #: The date this source was read. Not a clock read at runtime (R47).
    as_of: str = Field(pattern=DATE_PATTERN)
    #: The model keys this source prices.
    models: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _keys_are_sorted_and_distinct(self) -> RateSourceRef:
        if list(self.models) != sorted(self.models):
            raise ValueError("a source's models must be sorted")
        if len(set(self.models)) != len(self.models):
            raise ValueError("a source must not name a model twice")
        for key in self.models:
            if not re.fullmatch(MODEL_KEY_PATTERN, key):
                raise ValueError(f"{key!r} is not a legal model key")
        return self


class SnapshotMeta(_Frozen):
    """The snapshot's identity and provenance (R26, R27, R31, A4).

    ``version`` and ``snapshot_date`` are rendered next to every dollar figure
    in both reports, because a list price at a date is the honest description of
    what this engine computes: not a bill, not a negotiated rate, and not
    today's price for last month's run.
    """

    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._\-]{1,64}$")
    snapshot_date: str = Field(pattern=DATE_PATTERN)
    currency: Literal["USD"] = "USD"
    sources: tuple[RateSourceRef, ...] = ()

    def source_for(self, model_key: str) -> RateSourceRef | None:
        """The source that prices ``model_key``, or ``None`` when none does."""
        for source in self.sources:
            if model_key in source.models:
                return source
        return None


class RateSnapshot(_Frozen):
    """The whole bundled rate file (R27).

    The validator is where "the snapshot is reviewable" becomes structural: an
    alias may not dangle, a model key may not be unattributed, and no key may be
    claimed by two sources. Every one of those is a mistake a human editing JSON
    makes, and each would otherwise surface as a silently wrong dollar figure
    rather than as a load failure.
    """

    meta: SnapshotMeta
    models: dict[str, RateEntry] = Field(default_factory=dict)
    aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _pinned_shape(self) -> RateSnapshot:
        for key in self.models:
            if not re.fullmatch(MODEL_KEY_PATTERN, key):
                raise ValueError(f"model key {key!r} is not a legal model key")
        for recorded, target in self.aliases.items():
            if not re.fullmatch(MODEL_KEY_PATTERN, recorded):
                raise ValueError(f"alias {recorded!r} is not a legal model key")
            if target not in self.models:
                raise ValueError(f"alias {recorded!r} points at {target!r}, which is not a model")
        claimed: dict[str, str] = {}
        for source in self.meta.sources:
            for key in source.models:
                if key in claimed:
                    raise ValueError(f"model {key!r} is claimed by two sources")
                claimed[key] = source.id
        unattributed = sorted(set(self.models) - set(claimed))
        if unattributed:
            raise ValueError(
                f"no source records provenance for {unattributed[0]!r}; every rate must name "
                "where it came from and when it was read (A4)"
            )
        stray = sorted(set(claimed) - set(self.models))
        if stray:
            raise ValueError(f"a source claims {stray[0]!r}, which is not in models")
        return self

    def prefix_keys(self) -> tuple[str, ...]:
        """Model keys longest first, then alphabetically — R27 step (3)'s order.

        Longest wins, and R27 notes ties are impossible because keys are unique.
        The secondary sort is there anyway so the iteration order is a function
        of the data rather than of a dict (R47).
        """
        return tuple(sorted(self.models, key=lambda key: (-len(key), key)))


class RateSource(Protocol):
    """Where a price comes from (R26).

    Three members, and ``compute.py`` uses only these:

    * :meth:`get_rate` — one price, or ``None`` when the model or the key is
      absent. ``None`` is not an error; it is R30's third and fourth reasons.
    * :meth:`resolve_model_key` — R27's four-rung ladder. It lives on the source
      because the ladder reads the source's own key and alias tables, and a
      historical-rate adapter in v2 will have different ones.
    * :attr:`meta` — the snapshot identity every report renders beside a figure.
    """

    @property
    def meta(self) -> SnapshotMeta:
        """The snapshot's version, date, currency and provenance."""
        ...

    def get_rate(self, model_key: str, price_key: str) -> Decimal | None:
        """The USD-per-1,000,000-token rate, or ``None`` when it is absent."""
        ...

    def resolve_model_key(self, recorded: str | None) -> str | None:
        """R27: a recorded ``Span.model`` → a snapshot key, or ``None``."""
        ...


def rates_for(source: RateSource, model_key: str) -> Mapping[str, Decimal]:
    """Every price ``source`` holds for ``model_key``, keyed by price key.

    A convenience over :meth:`RateSource.get_rate` that keeps the caller from
    writing the five-way lookup twice. Absent keys are simply not in the result,
    which is what R30's ``rate_key_missing`` check reads.
    """
    found: dict[str, Decimal] = {}
    for price_key in PRICE_KEYS:
        rate = source.get_rate(model_key, price_key)
        if rate is not None:
            found[price_key] = rate
    return found


__all__ = [
    "DATE_PATTERN",
    "MODEL_KEY_PATTERN",
    "PRICE_KEYS",
    "USAGE_PRICE_KEYS",
    "RateEntry",
    "RateSnapshot",
    "RateSnapshotError",
    "RateSource",
    "RateSourceRef",
    "RateValue",
    "SnapshotMeta",
    "parse_rate",
    "rates_for",
]
