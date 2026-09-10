"""The rate seam, the bundled snapshot and the resolution ladder: R26, R27.

Two things here are worth more than the sum of their assertions.

**R27's rung 3 fails silently or not at all.** ``claude-opus-4-5-20251101``
starts with both ``claude-opus-4`` and ``claude-opus-4-5``, and in the shipped
snapshot those are three-fold apart in price ($15 vs $5 per million input
tokens). A longest-wins bug does not raise; it prices a whole trace at the wrong
model's rate and every total still adds up. The same is true of the ``+ "-"``
separator in the other direction: without it ``claude-opus-45-preview`` resolves
to ``claude-opus-4``. Both directions are pinned below against the *shipped*
snapshot, so the pin is a statement about the product a user runs and not only
about a fixture.

**Every rung is asserted with a case only that rung can satisfy.** Rung 2
(aliases) is empty in the shipped file — the coder says so plainly — so it is
driven through ``SnapshotRateSource(snapshot=...)``, including the one ordering
question that matters: an alias must beat a *longer* prefix key, because rung 2
runs before rung 3.

Rates are strings on disk and ``Decimal`` in memory. ``parse_rate`` rejects a
JSON number by type rather than coercing it, which is R26's structural version
of "no float touches money", and that rejection is exercised here rather than
assumed.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from swarm_observer.cost.snapshot import (
    SNAPSHOT_FILENAME,
    SNAPSHOT_PACKAGE,
    SnapshotRateSource,
    bundled_snapshot,
    parse_snapshot,
)
from swarm_observer.cost.source import (
    DATE_PATTERN,
    MODEL_KEY_PATTERN,
    PRICE_KEYS,
    USAGE_PRICE_KEYS,
    RateEntry,
    RateSnapshot,
    RateSnapshotError,
    RateSource,
    RateSourceRef,
    SnapshotMeta,
    parse_rate,
    rates_for,
)

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO / "swarm_observer" / "cost" / "data" / SNAPSHOT_FILENAME


def _snapshot(models: dict[str, RateEntry], aliases: dict[str, str] | None = None) -> RateSnapshot:
    """A minimal valid snapshot over ``models``, every key attributed."""
    return RateSnapshot(
        meta=SnapshotMeta(
            version="test",
            snapshot_date="2026-01-01",
            sources=(
                RateSourceRef(
                    id="test_source",
                    label="a source",
                    url="https://example.invalid/rates",
                    as_of="2026-01-01",
                    models=tuple(sorted(models)),
                ),
            ),
        ),
        models=models,
        aliases=dict(aliases or {}),
    )


FULL_ENTRY = RateEntry(
    input="1",
    output="2",
    cache_read="3",
    cache_write_5m="4",
    cache_write_1h="5",
)


class TestParseRateR26:
    """R26: a rate is a string on disk and a ``Decimal`` in memory, converted once."""

    @pytest.mark.parametrize(
        "text",
        ["0", "1", "15", "0.80", "0.000000000001", "12.50", "1000000", "0.0"],
    )
    def test_r26_a_plain_decimal_string_parses(self, text: str) -> None:
        """R26: the shapes the shipped file actually uses all parse."""
        assert parse_rate(text) == Decimal(text)

    @pytest.mark.parametrize(
        "value",
        [0.1, 1.0, 15, 0, True, None, [], {}, ("1",)],
    )
    def test_r26_a_non_string_rate_is_refused_by_type(self, value: object) -> None:
        """R26: a JSON number arrives as a float and is wrong before anyone looks."""
        with pytest.raises(ValueError):
            parse_rate(value)

    def test_r26_a_decimal_passes_through_for_a_programmatic_snapshot(self) -> None:
        """R26: a snapshot built in memory may hand in an already-converted rate."""
        assert parse_rate(Decimal("2.5")) == Decimal("2.5")

    @pytest.mark.parametrize(
        "text",
        [
            "1E3",
            "1e3",
            "NaN",
            "nan",
            "Infinity",
            "-1",
            "-0.5",
            "+1",
            "1_000",
            "1,000",
            " 1",
            "1 ",
            "",
            ".5",
            "1.",
            "01",
            "1.0000000000000",
            "0x10",
        ],
    )
    def test_r26_a_string_that_is_not_a_plain_decimal_is_refused(self, text: str) -> None:
        """R26: the accepted alphabet is narrower than ``Decimal`` accepts."""
        with pytest.raises(ValueError):
            parse_rate(text)

    def test_r26_float_rejection_is_not_vacuous(self) -> None:
        """R26: the float that is refused is one whose Decimal value is wrong.

        ``Decimal(0.1)`` is ``0.1000000000000000055511151231257827...``. A
        coercing loader would have accepted that and been wrong by the time the
        first token count multiplied it.
        """
        one_tenth = 1 / 10  # a float, exactly as json.loads would produce
        assert Decimal(one_tenth) != Decimal("0.1")
        with pytest.raises(ValueError):
            parse_rate(one_tenth)


class TestSnapshotShapeR27:
    """R27: the snapshot's shape is enforced at load, not trusted."""

    def test_r27_price_keys_are_the_five_r27_names_in_formula_order(self) -> None:
        """R27/R28: the five price keys, in the order R28's formula applies them."""
        assert PRICE_KEYS == (
            "input",
            "output",
            "cache_read",
            "cache_write_5m",
            "cache_write_1h",
        )

    def test_r27_usage_price_keys_pair_every_component_with_its_price(self) -> None:
        """R28: the usage-to-price mapping is total and injective."""
        components = [component for component, _ in USAGE_PRICE_KEYS]
        prices = [price for _, price in USAGE_PRICE_KEYS]
        assert components == [
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_5m_tokens",
            "cache_creation_1h_tokens",
        ]
        assert prices == list(PRICE_KEYS)

    def test_r27_a_dangling_alias_fails_to_load(self) -> None:
        """R27: an alias pointing at no model is a load failure, not a silent miss."""
        with pytest.raises(ValidationError):
            RateSnapshot(
                meta=SnapshotMeta(
                    version="t",
                    snapshot_date="2026-01-01",
                    sources=(
                        RateSourceRef(
                            id="s",
                            label="l",
                            url="u",
                            as_of="2026-01-01",
                            models=("m-1",),
                        ),
                    ),
                ),
                models={"m-1": FULL_ENTRY},
                aliases={"other": "m-2"},
            )

    def test_r27_an_unattributed_model_fails_to_load(self) -> None:
        """A4/R26: a rate nobody can attribute cannot enter the file quietly."""
        with pytest.raises(ValidationError):
            RateSnapshot(
                meta=SnapshotMeta(version="t", snapshot_date="2026-01-01", sources=()),
                models={"m-1": FULL_ENTRY},
            )

    def test_r27_a_model_claimed_by_two_sources_fails_to_load(self) -> None:
        """R26: provenance is exactly one source per key, so a rate has one date."""
        source = RateSourceRef(id="a", label="l", url="u", as_of="2026-01-01", models=("m-1",))
        other = RateSourceRef(id="b", label="l", url="u", as_of="2026-01-01", models=("m-1",))
        with pytest.raises(ValidationError):
            RateSnapshot(
                meta=SnapshotMeta(version="t", snapshot_date="2026-01-01", sources=(source, other)),
                models={"m-1": FULL_ENTRY},
            )

    def test_r27_a_source_claiming_an_absent_model_fails_to_load(self) -> None:
        """R26: provenance that names nothing is a typo, not a comment."""
        with pytest.raises(ValidationError):
            RateSnapshot(
                meta=SnapshotMeta(
                    version="t",
                    snapshot_date="2026-01-01",
                    sources=(
                        RateSourceRef(
                            id="s",
                            label="l",
                            url="u",
                            as_of="2026-01-01",
                            models=("m-1", "m-2"),
                        ),
                    ),
                ),
                models={"m-1": FULL_ENTRY},
            )

    def test_r27_an_unsorted_source_model_list_fails_to_load(self) -> None:
        """R47: the file's own orderings are data, so they are pinned at load."""
        with pytest.raises(ValidationError):
            RateSourceRef(id="s", label="l", url="u", as_of="2026-01-01", models=("m-2", "m-1"))

    def test_r27_a_source_naming_a_model_twice_fails_to_load(self) -> None:
        """R26: a duplicated claim is a merge artefact, not provenance."""
        with pytest.raises(ValidationError):
            RateSourceRef(id="s", label="l", url="u", as_of="2026-01-01", models=("m-1", "m-1"))

    @pytest.mark.parametrize("key", ["M-1", "-m", ".m", "m 1", "m/1", "", "m" * 65, "ünïcode"])
    def test_r27_an_illegal_model_key_fails_to_load(self, key: str) -> None:
        """R27: the key alphabet is what makes a resolved key safe to render."""
        with pytest.raises(ValidationError):
            RateSnapshot(
                meta=SnapshotMeta(
                    version="t",
                    snapshot_date="2026-01-01",
                    sources=(
                        RateSourceRef(
                            id="s", label="l", url="u", as_of="2026-01-01", models=(key,)
                        ),
                    ),
                ),
                models={key: FULL_ENTRY},
            )

    @pytest.mark.parametrize("date", ["2026-1-1", "26-01-01", "2026/01/01", "", "today"])
    def test_r27_a_non_iso_date_fails_to_load(self, date: str) -> None:
        """R47: a locale-formatted date would put a locale into a report."""
        with pytest.raises(ValidationError):
            SnapshotMeta(version="t", snapshot_date=date)

    def test_r27_currency_is_usd_only(self) -> None:
        """R27: ``currency`` is the literal ``USD`` in v1."""
        assert SnapshotMeta(version="t", snapshot_date="2026-01-01").currency == "USD"
        with pytest.raises(ValidationError):
            SnapshotMeta(version="t", snapshot_date="2026-01-01", currency="EUR")

    def test_r27_a_rate_entry_may_omit_a_price_key(self) -> None:
        """R30: the omission is what makes ``rate_key_missing`` reachable at all."""
        entry = RateEntry(input="1")
        assert entry.get("input") == Decimal("1")
        assert entry.get("cache_write_1h") is None
        assert entry.get("not_a_price_key") is None

    def test_r27_the_models_and_meta_patterns_are_the_documented_ones(self) -> None:
        """R27: the two exported patterns are what the docstrings claim."""
        assert MODEL_KEY_PATTERN == r"^[a-z0-9][a-z0-9._\-]{0,63}$"
        assert DATE_PATTERN == r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


class TestParseSnapshotR26:
    """R26: every load failure is one sanitized line, never the file's own bytes."""

    def test_r26_invalid_json_is_a_rate_snapshot_error(self) -> None:
        """R26/R11: a broken package-data file fails closed."""
        with pytest.raises(RateSnapshotError) as caught:
            parse_snapshot("{not json")
        assert caught.value.code == "rate_snapshot_unreadable"

    @pytest.mark.parametrize("document", ["[]", '"text"', "3", "null", "true"])
    def test_r26_a_non_object_document_is_a_rate_snapshot_error(self, document: str) -> None:
        """R26: the snapshot is an object; anything else is a packaging fault."""
        with pytest.raises(RateSnapshotError) as caught:
            parse_snapshot(document)
        assert caught.value.code == "rate_snapshot_unreadable"

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda d: d["models"]["claude-opus-5"].update({"input": 5}), id="float"),
            pytest.param(
                lambda d: d["models"]["claude-opus-5"].update({"input": "-1"}), id="negative"
            ),
            pytest.param(
                lambda d: d["models"]["claude-opus-5"].update({"input": "1E3"}), id="exponent"
            ),
            pytest.param(lambda d: d["models"]["claude-opus-5"].update({"input": "NaN"}), id="nan"),
            pytest.param(lambda d: d["aliases"].update({"x": "nope"}), id="dangling-alias"),
            pytest.param(lambda d: d["models"].update({"ghost": {"input": "1"}}), id="ghost-model"),
            pytest.param(lambda d: d["meta"].pop("snapshot_date"), id="no-date"),
            pytest.param(lambda d: d["meta"].update({"currency": "EUR"}), id="bad-currency"),
            pytest.param(
                lambda d: d["models"]["claude-opus-5"].update({"surcharge": "1"}), id="extra-key"
            ),
        ],
    )
    def test_r26_a_malformed_snapshot_is_rejected_with_an_enumerated_code(
        self, mutate: object
    ) -> None:
        """R26: nine ways to corrupt the shipped file, all refused at load."""
        document = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        mutate(document)  # type: ignore[operator]
        with pytest.raises(RateSnapshotError) as caught:
            parse_snapshot(json.dumps(document))
        assert caught.value.code == "rate_snapshot_invalid"

    def test_r26_the_unmodified_shipped_file_loads(self) -> None:
        """R26: the control arm — the rejections above are not simply always-fail."""
        assert parse_snapshot(SNAPSHOT_PATH.read_text(encoding="utf-8")).models

    def test_r26_a_rate_snapshot_error_never_quotes_the_file(self) -> None:
        """R11's discipline: a rate file that fails to load may not print itself."""
        error = RateSnapshotError("rate_snapshot_invalid", note="does not match")
        assert error.cli_line == "swarm-observer: rate_snapshot_invalid: does not match"
        assert "\n" not in error.cli_line
        with pytest.raises(ValueError):
            RateSnapshotError("rate_snapshot_invalid", note='{"input": "9999"}')
        with pytest.raises(ValueError):
            RateSnapshotError("Not A Slug")
        with pytest.raises(ValueError):
            RateSnapshotError("rate_snapshot_invalid", model_key="NOT A KEY")


class TestBundledSnapshotR26:
    """R26: the shipped file is present, complete and internally consistent."""

    def test_r26_the_snapshot_is_package_data_not_a_relative_path(self) -> None:
        """R26/R47: it is read through the package, so CWD cannot change a rate."""
        assert SNAPSHOT_PACKAGE == "swarm_observer.cost.data"
        assert SNAPSHOT_FILENAME == "model_rates.json"
        assert SNAPSHOT_PATH.is_file()

    def test_r26_every_model_entry_carries_all_five_price_keys(self) -> None:
        """T11: a shipped entry missing a key would price a real trace wrongly."""
        snapshot = bundled_snapshot()
        for key, entry in sorted(snapshot.models.items()):
            missing = [price for price in PRICE_KEYS if entry.get(price) is None]
            assert not missing, f"{key} is missing {missing}"

    def test_r26_every_rate_is_a_non_negative_decimal(self) -> None:
        """R26: no float, no sign, no exponent survived the load."""
        for key, entry in sorted(bundled_snapshot().models.items()):
            for price in PRICE_KEYS:
                rate = entry.get(price)
                assert isinstance(rate, Decimal), f"{key}.{price}"
                assert rate >= 0, f"{key}.{price}"

    def test_r26_every_model_key_is_attributed_to_exactly_one_source(self) -> None:
        """A4: the provenance link is real for every shipped key."""
        snapshot = bundled_snapshot()
        for key in sorted(snapshot.models):
            claims = [source.id for source in snapshot.meta.sources if key in source.models]
            assert len(claims) == 1, f"{key} claimed by {claims}"

    def test_r26_the_meta_names_a_version_a_date_and_usd(self) -> None:
        """A4: the figures a report prints beside every dollar amount."""
        meta = bundled_snapshot().meta
        assert meta.currency == "USD"
        assert meta.version
        assert meta.snapshot_date
        assert meta.sources

    def test_r26_source_for_finds_the_owning_source_and_only_that(self) -> None:
        """A4: ``source_for`` is the report's provenance lookup."""
        snapshot = bundled_snapshot()
        assert snapshot.meta.source_for("claude-opus-4-1") is not None
        assert snapshot.meta.source_for("claude-opus-4-1").id == "anthropic_pricing_legacy"
        assert snapshot.meta.source_for("no-such-model") is None

    def test_r26_bundled_snapshot_is_a_memo_of_one_frozen_value(self) -> None:
        """R47: two loads in one process are the same immutable snapshot."""
        assert bundled_snapshot() is bundled_snapshot()

    def test_r26_get_rate_returns_none_for_an_absent_model_or_key(self) -> None:
        """R26: ``None`` is not an error; it is R30's third and fourth reasons."""
        source = SnapshotRateSource()
        assert source.get_rate("claude-opus-5", "input") == Decimal("5")
        assert source.get_rate("no-such-model", "input") is None
        assert source.get_rate("claude-opus-5", "no-such-key") is None

    def test_r26_rates_for_omits_absent_keys_rather_than_zeroing_them(self) -> None:
        """R30: an absent key must be absent, because that is what rung 4 reads."""
        partial = SnapshotRateSource(snapshot=_snapshot({"m-1": RateEntry(input="1")}))
        assert dict(rates_for(partial, "m-1")) == {"input": Decimal("1")}
        assert dict(rates_for(partial, "absent")) == {}
        assert set(rates_for(SnapshotRateSource(), "claude-opus-5")) == set(PRICE_KEYS)

    def test_r26_a_snapshot_rate_source_satisfies_the_rate_source_protocol(self) -> None:
        """R26: the seam is a protocol and the shipped source implements it."""
        source: RateSource = SnapshotRateSource()
        assert callable(source.get_rate)
        assert callable(source.resolve_model_key)
        assert source.meta.currency == "USD"

    def test_r26_model_keys_are_returned_sorted(self) -> None:
        """R31: ``by_model`` is ordered by key, so the source's list is too."""
        keys = SnapshotRateSource().model_keys()
        assert list(keys) == sorted(keys)
        assert len(set(keys)) == len(keys)


class TestResolutionLadderR27:
    """R27: four rungs, in order, and rung 3's longest-wins rule.

    The shipped snapshot is used wherever it can distinguish the answers,
    because a ladder that is right about a fixture and wrong about the file the
    product ships is the defect this whole module exists to catch.
    """

    @pytest.mark.parametrize(
        ("recorded", "expected"),
        [
            # rung 1: exact key
            ("claude-opus-5", "claude-opus-5"),
            ("claude-3-5-haiku", "claude-3-5-haiku"),
            # rung 3: the real, date-suffixed ids
            ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
            ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
            # rung 3, longest wins over a shorter key that also matches
            ("claude-opus-4-5-20251101", "claude-opus-4-5"),
            ("claude-opus-4-1-20250805", "claude-opus-4-1"),
            ("claude-sonnet-4-5-20250929-v2", "claude-sonnet-4-5"),
            ("claude-fable-5-1-20260101", "claude-fable-5-1"),
            # the separator: more digits is not a version boundary
            ("claude-fable-5-12345", "claude-fable-5"),
            # rung 4
            ("claude-opus-45-preview", None),
            ("claude-opus", None),
            ("claude", None),
            ("", None),
            ("CLAUDE-SONNET-4-5", None),
            ("anthropic.claude-opus-5", None),
            ("some-model-nobody-published", None),
            (None, None),
        ],
    )
    def test_r27_the_shipped_snapshot_resolves_these_exactly(
        self, recorded: str | None, expected: str | None
    ) -> None:
        """R27: the ladder's answer for seventeen recorded ids, pinned."""
        assert SnapshotRateSource().resolve_model_key(recorded) == expected

    def test_r27_longest_wins_changes_the_price_threefold(self) -> None:
        """R27: the shorter key is a different model at three times the rate.

        This is the assertion that makes ``longest wins`` non-cosmetic. If rung
        3 returned the first or the shortest match, the whole trace would price
        at ``claude-opus-4``'s $15 per million input tokens instead of
        ``claude-opus-4-5``'s $5, and nothing would raise.
        """
        source = SnapshotRateSource()
        resolved = source.resolve_model_key("claude-opus-4-5-20251101")
        assert resolved == "claude-opus-4-5"
        assert source.get_rate("claude-opus-4-5", "input") == Decimal("5")
        assert source.get_rate("claude-opus-4", "input") == Decimal("15")
        assert source.get_rate(resolved, "input") != source.get_rate("claude-opus-4", "input")

    def test_r27_the_separator_is_load_bearing_in_both_directions(self) -> None:
        """R27: ``k`` or ``k + "-"``; a plain ``startswith`` is wrong."""
        source = SnapshotRateSource(
            snapshot=_snapshot({"m-4": FULL_ENTRY, "m-45": RateEntry(input="9")})
        )
        assert source.resolve_model_key("m-4-2026") == "m-4"
        assert source.resolve_model_key("m-45-2026") == "m-45"
        assert source.resolve_model_key("m-4x") is None
        # ``m-42`` starts with ``m-4`` but not with ``m-4-``: rung 3 must miss.
        assert source.resolve_model_key("m-42") is None

    def test_r27_a_recorded_id_that_is_a_prefix_of_a_key_does_not_resolve(self) -> None:
        """R27: the rule is a prefix *of the recorded id*, not of the key."""
        source = SnapshotRateSource(snapshot=_snapshot({"m-1-2-3": FULL_ENTRY}))
        assert source.resolve_model_key("m-1") is None
        assert source.resolve_model_key("m-1-2") is None
        assert source.resolve_model_key("m-1-2-3") == "m-1-2-3"

    def test_r27_rung_2_resolves_an_alias(self) -> None:
        """R27: the alias rung, which the shipped file leaves empty."""
        source = SnapshotRateSource(snapshot=_snapshot({"m-1": FULL_ENTRY}, {"vendor.m-1": "m-1"}))
        assert source.resolve_model_key("vendor.m-1") == "m-1"

    def test_r27_rung_2_beats_a_longer_prefix_key(self) -> None:
        """R27: the *order* of the rungs, asserted with a case that can tell.

        ``m-1-special`` matches the prefix key ``m-1`` at rung 3 and is also an
        alias for ``m-2``. Rung 2 runs first, so the alias must win — swapping
        the two rungs prices it at ``m-1``'s rate and raises nothing.
        """
        source = SnapshotRateSource(
            snapshot=_snapshot(
                {"m-1": RateEntry(input="1"), "m-2": RateEntry(input="2")},
                {"m-1-special": "m-2"},
            )
        )
        assert source.resolve_model_key("m-1-special") == "m-2"

    def test_r27_rung_1_beats_an_alias_that_shadows_a_real_key(self) -> None:
        """R27: an exact model key wins even when an alias names it too."""
        source = SnapshotRateSource(
            snapshot=_snapshot(
                {"m-1": RateEntry(input="1"), "m-2": RateEntry(input="2")},
                {"m-1": "m-2"},
            )
        )
        assert source.resolve_model_key("m-1") == "m-1"

    def test_r27_ties_are_impossible_and_the_order_is_data_not_a_dict(self) -> None:
        """R27/R47: ``prefix_keys`` is longest-first then alphabetical."""
        snapshot = _snapshot(
            {"aaa": FULL_ENTRY, "bb": FULL_ENTRY, "cc": FULL_ENTRY, "d": FULL_ENTRY}
        )
        assert snapshot.prefix_keys() == ("aaa", "bb", "cc", "d")
        lengths = [len(key) for key in bundled_snapshot().prefix_keys()]
        assert lengths == sorted(lengths, reverse=True)

    def test_r27_resolution_is_pure_string_work(self) -> None:
        """R27: the same recorded id resolves the same way every time."""
        source = SnapshotRateSource()
        first = [source.resolve_model_key(key) for key in source.model_keys()]
        second = [source.resolve_model_key(key) for key in source.model_keys()]
        assert first == second == list(source.model_keys())

    def test_r27_every_shipped_key_is_reachable_by_a_dated_id(self) -> None:
        """T11: a rate no recorded id can reach is a rate nobody can use."""
        source = SnapshotRateSource()
        for key in source.model_keys():
            assert source.resolve_model_key(f"{key}-20260101") == key

    def test_r27_a_hostile_recorded_id_resolves_to_nothing_rather_than_raising(self) -> None:
        """R27: resolution is total — no heuristic, no exception, no crash."""
        source = SnapshotRateSource()
        for hostile in (
            "<script>alert(1)</script>",
            "claude-opus-5\n",
            "claude-opus-5\x00",
            "claude-" + "5" * 5000,
            "‮claude-opus-5",
        ):
            assert source.resolve_model_key(hostile) is None
