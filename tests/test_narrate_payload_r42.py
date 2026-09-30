"""R42 and AC12's second half: the ``--explain`` payload cannot carry trace text.

The coder's claim is that this is **structural** — "a request holding a
sentinel is not one that gets sent and cleaned; it is one that cannot be
constructed" — and the coder's own risk list says the closed vocabularies are
a second oracle it wrote. So this module checks the claim three ways, and two
of them do not use the coder's artefacts at all:

1. **Every string-valued leaf of the request tree, enumerated from
   ``model_fields``**, is attacked with a sentinel. The enumeration is derived,
   not typed, so a field added in v2 fails the completeness arm until somebody
   classifies it. This is the claim in its own terms: which leaves refuse a
   sentinel, and — the part the PR does not state — which only refuse one
   *outside a shape*.
2. **The sentinel sweep**, over ``tests/sentinel_trace.py``, whose sentinels
   are chosen to be **acceptable to the payload's own validators**. A sentinel
   that the type would have admitted and that does not reach the payload was
   dropped by the builder. That is what tells "the type refuses it" apart from
   "``summary.py`` filters it", which is the whole disagreement.
3. **A vocabulary table computed independently** — from the detector registry,
   the bundled rate snapshot, the severity enum and R29's money format — which
   every distinct string value in every serialized payload must be a member
   of. It is deliberately built without importing ``narrate.client``'s
   patterns, because those patterns are the thing under test.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from swarm_observer.cost.snapshot import SnapshotRateSource
from swarm_observer.detect.base import (
    SEVERITIES,
    TRACE_DERIVED_METRIC_KEYS,
    build_finding,
)
from swarm_observer.detect.registry import ALL_DETECTORS, DETECTOR_SLUGS
from swarm_observer.narrate.client import (
    MAX_FINDINGS_PER_GROUP,
    MAX_PARAGRAPH_CHARS,
    MAX_TOTAL_ROWS,
    NARRATION_GROUPS,
    NARRATION_TITLES,
    OVERALL_GROUP,
    OVERALL_TITLE,
    AgentTotals,
    FindingSummary,
    GroupSummary,
    MetricEntry,
    ModelTotals,
    NarrationRequest,
    TraceTotals,
)
from swarm_observer.narrate.summary import (
    build_requests,
    build_totals,
    group_summary,
    narration_groups,
    serialize_request,
    severity_counts,
)

from .sentinel_trace import (
    SENTINELS,
    sentinel_analysis,
    sentinels_present_in_inputs,
    totals_for,
)
from .synthetic_traces import TraceBuilder

#: A sentinel no alphabet in the payload admits: it has uppercase, a space and
#: punctuation, which is what a trace's free text looks like.
LOUD = "SENTINEL free/text <script>"

#: A sentinel every lowercase-slug alphabet in the payload **does** admit. The
#: two together are what separates "closed vocabulary" from "shape check".
QUIET = "sentinelfreetext"

#: One sentinel per way a pattern can be quietly widened. Each is a shape a
#: trace's free text really takes, and each is refused by a *different* part
#: of the constraint it meets: the alphabet, the anchors, the match mode.
HOSTILE_SENTINELS: tuple[str, ...] = (
    LOUD,
    "Sentinelkey",  # an alphabet whose FIRST character widened to [A-Za-z]
    "sentinel with spaces",  # a character class that gained a space
    "sentinel <script>alert(1)",  # a fullmatch relaxed to a match
    "x" * 200,  # a length bound removed
)
HOSTILE_IDS = ("loud", "capitalised", "spaces", "prefix", "long")

#: The leaves whose alphabet admits an **uppercase** string. Only one does,
#: and it is worth naming rather than leaving to a battery: R42 calls the
#: rate-snapshot version "package data", and its pattern admits any
#: ``[A-Za-z0-9._-]`` string of 64 characters or fewer -- the shape of a
#: recorded model id or an agent id, both of which are trace-derived.
UPPERCASE_ADMITTED: frozenset[str] = frozenset({"rate_snapshot_version"})


def empty_totals(**overrides: Any) -> TraceTotals:
    fields: dict[str, Any] = {
        "agents": 0,
        "spans": 0,
        "model_calls": 0,
        "findings": 0,
        "tokens": 0,
        "cost_usd": "0.000000",
        "priced_spans": 0,
        "unpriced_spans": 0,
    }
    fields.update(overrides)
    return TraceTotals(**fields)


def a_group(**overrides: Any) -> GroupSummary:
    fields: dict[str, Any] = {
        "group": OVERALL_GROUP,
        "title": OVERALL_TITLE,
        "findings": 0,
        "wasted_tokens": 0,
    }
    fields.update(overrides)
    return GroupSummary(**fields)


def a_request(**overrides: Any) -> NarrationRequest:
    fields: dict[str, Any] = {
        "rate_snapshot_version": "1.0.0",
        "group": OVERALL_GROUP,
        "totals": empty_totals(),
        "groups": (a_group(),),
    }
    fields.update(overrides)
    return NarrationRequest(**fields)


# --- 1. every string leaf of the request tree, derived rather than typed -----


def _string_leaves(model: type[BaseModel], prefix: str = "") -> set[str]:
    """Every dotted path in ``model``'s tree at which a ``str`` can sit.

    Derived from ``model_fields`` so it cannot drift from the type. A
    ``dict[str, int]`` counts: its **keys** are strings the payload carries.
    """
    found: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        found |= _leaves_of(field.annotation, path)
    return found


def _leaves_of(annotation: Any, path: str) -> set[str]:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if annotation is str:
        return {path}
    if origin is typing.Literal:
        return {path} if any(isinstance(value, str) for value in args) else set()
    if origin in (dict,):
        return {f"{path}{{key}}"} if args and args[0] is str else set()
    if origin in (tuple, list, set, frozenset):
        inner = [arg for arg in args if arg is not Ellipsis]
        return set().union(*(_leaves_of(arg, f"{path}[]") for arg in inner)) if inner else set()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _string_leaves(annotation, prefix=f"{path}.")
    if args:  # a union: `int | str`, `str | None`
        return set().union(*(_leaves_of(arg, path) for arg in args))
    return set()


#: Every string-valued leaf, and how to put a value at it. The mapping is
#: hand-written **and checked for completeness** against the derived set below,
#: which is the pattern ``tests/test_untrusted_field_sweep.py`` uses: a field
#: added in v2 fails the completeness arm until somebody says what it is.
ATTACKS: dict[str, Any] = {
    "request_version": lambda value: a_request(request_version=value),
    "rate_snapshot_version": lambda value: a_request(rate_snapshot_version=value),
    "group": lambda value: a_request(group=value),
    "totals.cost_usd": lambda value: a_request(totals=empty_totals(cost_usd=value)),
    "totals.severity_counts{key}": lambda value: a_request(
        totals=empty_totals(severity_counts={value: 1})
    ),
    "totals.by_agent[].cost_usd": lambda value: a_request(
        totals=empty_totals(
            by_agent=(
                AgentTotals(
                    agent_index=0, priced_spans=0, unpriced_spans=0, tokens=0, cost_usd=value
                ),
            )
        )
    ),
    "totals.by_model[].model_key": lambda value: a_request(
        totals=empty_totals(
            by_model=(ModelTotals(model_key=value, priced_spans=0, tokens=0, cost_usd="0.000000"),)
        )
    ),
    "totals.by_model[].cost_usd": lambda value: a_request(
        totals=empty_totals(
            by_model=(
                ModelTotals(model_key="claude-haiku-4-5", priced_spans=0, tokens=0, cost_usd=value),
            )
        )
    ),
    "groups[].group": lambda value: a_request(groups=(a_group(group=value),)),
    "groups[].title": lambda value: a_request(groups=(a_group(title=value),)),
    "groups[].severity_counts{key}": lambda value: a_request(
        groups=(a_group(severity_counts={value: 1}),)
    ),
    "groups[].detail[].severity": lambda value: a_request(
        groups=(
            a_group(
                detail=(
                    FindingSummary(severity=value, evidence_spans=0, agents=0, wasted_tokens=0),
                )
            ),
        )
    ),
    "groups[].detail[].metrics[].key": lambda value: a_request(
        groups=(
            a_group(
                detail=(
                    FindingSummary(
                        severity="info",
                        evidence_spans=0,
                        agents=0,
                        wasted_tokens=0,
                        metrics=(MetricEntry(key=value, value=1),),
                    ),
                )
            ),
        )
    ),
    "groups[].detail[].metrics[].value": lambda value: a_request(
        groups=(
            a_group(
                detail=(
                    FindingSummary(
                        severity="info",
                        evidence_spans=0,
                        agents=0,
                        wasted_tokens=0,
                        metrics=(MetricEntry(key="occurrences", value=value),),
                    ),
                )
            ),
        )
    ),
}

#: The leaves that are a **closed vocabulary**: no string outside a set this
#: package computed is admitted, whatever its alphabet.
CLOSED_LEAVES: frozenset[str] = frozenset(
    {
        "request_version",
        "group",
        "totals.severity_counts{key}",
        "groups[].group",
        "groups[].title",
        "groups[].severity_counts{key}",
        "groups[].detail[].severity",
        "totals.cost_usd",
        "totals.by_agent[].cost_usd",
        "totals.by_model[].cost_usd",
    }
)

#: The leaves that are only a **shape** check. A lowercase-slug string is
#: admitted at each of these — which is the honest scope of "the payload's
#: type has no field that can hold trace free text". Nothing trace-derived
#: reaches them today, but nothing about the *type* stops one.
SHAPE_LEAVES: frozenset[str] = frozenset(
    {
        "rate_snapshot_version",
        "totals.by_model[].model_key",
        "groups[].detail[].metrics[].key",
        "groups[].detail[].metrics[].value",
    }
)


class TestPayloadTypeR42:
    """R42: which of the payload's string leaves actually refuse a sentinel."""

    def test_r42_the_attack_table_covers_every_string_leaf_of_the_request(self) -> None:
        """R42: a new string field must be classified before it can ship.

        Red when: a field is added to any payload model that can hold a
        ``str`` and nobody says which kind of check guards it. That is the
        failure mode a hand-written list has and a derived one does not.
        """
        derived = _string_leaves(NarrationRequest)
        assert derived == set(ATTACKS), (
            "string leaves with no attack: "
            f"{sorted(derived - set(ATTACKS))}; attacks with no leaf: "
            f"{sorted(set(ATTACKS) - derived)}"
        )
        assert derived == CLOSED_LEAVES | SHAPE_LEAVES
        assert not CLOSED_LEAVES & SHAPE_LEAVES

    @pytest.mark.parametrize("leaf", sorted(ATTACKS))
    @pytest.mark.parametrize("sentinel", HOSTILE_SENTINELS, ids=HOSTILE_IDS)
    def test_r42_no_leaf_accepts_a_trace_shaped_sentinel(self, leaf: str, sentinel: str) -> None:
        """R42/AC12: free text — uppercase, spaces, markup — is refused everywhere.

        A **battery** rather than one string, because a single sentinel makes
        the check a property of that sentinel: widening an alphabet from
        ``[a-z]`` to ``[A-Za-z]``, or turning a ``fullmatch`` into a
        ``match``, is invisible to a needle that also contains a space. Each
        member of the battery defeats a different widening.

        The one exception is ``rate_snapshot_version``, whose alphabet
        already admits a capitalised word — see
        ``UPPERCASE_ADMITTED`` and the test below it, where that scope is
        stated rather than hidden inside a battery.

        Red when: any leaf is loosened to an unconstrained ``str``, or its
        alphabet, its anchors or its match mode are relaxed.
        """
        if leaf in UPPERCASE_ADMITTED and sentinel == "Sentinelkey":
            assert sentinel in serialize_request(ATTACKS[leaf](sentinel))
            return
        with pytest.raises((ValidationError, ValueError)):
            ATTACKS[leaf](sentinel)

    @pytest.mark.parametrize("leaf", sorted(CLOSED_LEAVES))
    def test_r42_a_closed_leaf_refuses_even_a_slug_shaped_sentinel(self, leaf: str) -> None:
        """R42: these leaves are a vocabulary, so the alphabet does not help.

        Red when: a closed vocabulary is replaced by a pattern — the change
        that would turn one of these into a ``SHAPE_LEAVES`` member without
        anyone noticing, because the loud sentinel above would still be
        refused.
        """
        with pytest.raises((ValidationError, ValueError)):
            ATTACKS[leaf](QUIET)

    @pytest.mark.parametrize("leaf", sorted(ATTACKS))
    def test_r42_only_the_snapshot_version_admits_an_uppercase_string(self, leaf: str) -> None:
        """R42: which leaf would carry a recorded model id or an agent id.

        Both are trace-derived and both are ``[A-Za-z0-9._-]``-shaped, which
        is exactly what ``SNAPSHOT_VERSION_PATTERN`` admits. Nothing puts one
        there today — ``cli/main.py`` passes ``SnapshotMeta.version`` — but
        the type does not stop it, and that is the difference between a
        vocabulary and a shape.

        Red when: another leaf's alphabet gains uppercase, or this one's
        loses it without ``UPPERCASE_ADMITTED`` being updated.
        """
        upper = "SENTINELUPPERCASE"
        if leaf in UPPERCASE_ADMITTED:
            assert upper in serialize_request(ATTACKS[leaf](upper))
        else:
            with pytest.raises((ValidationError, ValueError)):
                ATTACKS[leaf](upper)

    @pytest.mark.parametrize("leaf", sorted(SHAPE_LEAVES))
    def test_r42_a_shape_leaf_admits_a_slug_shaped_sentinel(self, leaf: str) -> None:
        """R42/S33: the honest scope of "the type cannot hold trace free text".

        Four leaves are guarded by an alphabet rather than by a vocabulary,
        and a lowercase slug passes all four. ``ghp_aaaa…`` is a lowercase
        slug and it is a GitHub token — S13's finding, one boundary over. That
        nothing trace-derived reaches these leaves today is a property of
        ``summary.py`` and ``cli/main.py``, not of the type, and this test is
        where that is written down.

        Red when: one of these becomes a closed vocabulary — a *good* change,
        which should move the leaf into ``CLOSED_LEAVES`` deliberately rather
        than silently.
        """
        request = ATTACKS[leaf](QUIET)
        assert QUIET in serialize_request(request)

    def test_r42_a_request_cannot_carry_an_unknown_field(self) -> None:
        """R42: ``extra="forbid"`` closes the last way to add a string."""
        with pytest.raises(ValidationError):
            NarrationRequest(
                rate_snapshot_version="1.0.0",
                group=OVERALL_GROUP,
                totals=empty_totals(),
                groups=(a_group(),),
                previews=("a hostile preview",),  # type: ignore[call-arg]
            )

    def test_r42_metric_entry_still_admits_the_one_trace_derived_key(self) -> None:
        """R42/S33 (**BUG-14**): the documented second refusal does not exist.

        ``MetricEntry``'s docstring says ``tool_name`` "never reaches this
        model … and the validator below refuses it a second time so a future
        caller cannot add it back by hand". It does not. The validator checks
        the *value*'s alphabet and never looks at the key, and a GitHub token
        is inside that alphabet — so a caller can put a credential-shaped tool
        name into the payload by hand and nothing objects.

        The property that actually holds is one ``if`` in
        ``summary._metrics``: a filter, in the module whose thesis is that it
        is not one. This test pins the gap so a fix (rejecting the key in the
        validator) turns it red and gets it rewritten.
        """
        entry = MetricEntry(key="tool_name", value="ghp_" + "a" * 24)
        assert entry.key in TRACE_DERIVED_METRIC_KEYS
        assert isinstance(entry.value, str)
        request = a_request(
            groups=(
                a_group(
                    detail=(
                        FindingSummary(
                            severity="info",
                            evidence_spans=0,
                            agents=0,
                            wasted_tokens=0,
                            metrics=(entry,),
                        ),
                    )
                ),
            )
        )
        assert entry.value in serialize_request(request)

    def test_r42_the_builder_is_the_only_thing_that_drops_tool_name(self) -> None:
        """R42/S33: ``summary`` drops the key the type would have accepted.

        The complement of the test above, and the pair is the evidence for the
        verdict on "structural, not filtered": construction accepts it, the
        builder removes it.
        """
        finding = build_finding(
            trace=_a_trace(),
            detector="repeated_tool_call",
            severity="warning",
            summary="two identical calls",
            span_seqs=(0, 1),
            agent_ids=("root",),
            metrics={"occurrences": 2, "tool_name": "ghp_" + "a" * 24},
        )
        assert finding.metrics["tool_name"] == "ghp_" + "a" * 24
        summary = group_summary("repeated_tool_call", [finding])
        keys = {entry.key for detail in summary.detail for entry in detail.metrics}
        assert keys == {"occurrences"}


# --- 2. the sentinel sweep, AC12's second half -------------------------------


@pytest.fixture(scope="module")
def sentinel_payload(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, list[str], str]:
    """The sentinel trace's analysis, each serialized request, and all of them joined."""
    analysis = sentinel_analysis(tmp_path_factory.mktemp("sentinel"))
    requests = build_requests(
        findings=analysis.findings,
        totals=totals_for(analysis),
        rate_snapshot_version=analysis.cost.meta.version,
    )
    payloads = [serialize_request(request) for request in requests.values()]
    return analysis, payloads, "\n".join(payloads)


class TestSentinelSweepR42:
    """AC12: "no sentinel appears in the serialized request payload"."""

    def test_r42_the_sentinels_are_really_in_the_objects_the_payload_came_from(
        self, sentinel_payload: tuple[Any, list[str], str]
    ) -> None:
        """AC12, the non-vacuity arm — an absence over an empty subject is nothing.

        Instance seven of this project's signature defect was a credential
        sweep run against a fixture whose ``findings`` array was empty. So the
        sweep below is only meaningful once this passes: **every** sentinel is
        demonstrably present in the ``Trace`` and the findings the payload was
        built from.

        Red when: the corpus stops loading a field, at which point the sweep
        would be asserting the absence of something that was never there.
        """
        analysis = sentinel_payload[0]
        present = sentinels_present_in_inputs(analysis)
        missing = sorted(set(SENTINELS) - present)
        assert present == set(SENTINELS), f"never reached the inputs: {missing}"

    def test_r42_the_findings_carry_a_credential_shaped_conforming_tool_name(
        self, sentinel_payload: tuple[Any, list[str], str]
    ) -> None:
        """AC12/S13: the one sentinel whose absence is a security property.

        ``ghp_aaaa…`` satisfies R16's tool-name pattern *and* the payload's
        authored-slug alphabet, so the type would have carried it. It is in
        ``metrics.tool_name`` of a real finding.
        """
        analysis = sentinel_payload[0]
        names = {finding.metrics.get("tool_name") for finding in analysis.findings}
        assert SENTINELS["metrics.tool_name.credential"] in names

    @pytest.mark.parametrize("label", sorted(SENTINELS))
    def test_r42_no_sentinel_reaches_the_serialized_payload(
        self, label: str, sentinel_payload: tuple[Any, list[str], str]
    ) -> None:
        """AC12: one arm per field, so a leak names the field that leaked.

        Red when: any trace-derived string is added to the payload.
        """
        blob = sentinel_payload[2]
        assert SENTINELS[label] not in blob

    def test_r42_the_payload_is_not_empty(
        self, sentinel_payload: tuple[Any, list[str], str]
    ) -> None:
        """AC12: the sweep above must be searching something.

        A serializer that returned ``""`` would pass every absence assertion
        above. Red when: the payload stops describing the run.
        """
        analysis, blob = sentinel_payload[0], sentinel_payload[2]
        assert len(blob) > 1_000
        assert len(analysis.findings) >= 4
        assert len(narration_groups(analysis.findings)) >= 4

    def test_r42_the_payload_names_no_trace_id_no_path_and_no_preview(
        self, sentinel_payload: tuple[Any, list[str], str]
    ) -> None:
        """R42: "no ``previews``, no file names, no paths, no ``trace_id``"."""
        analysis, blob = sentinel_payload[0], sentinel_payload[2]
        assert analysis.trace.trace_id not in blob
        for source in analysis.trace.source_files:
            assert source.name not in blob
            assert source.sha256 not in blob
        for finding in analysis.findings:
            assert finding.finding_id not in blob
            for preview in finding.previews:
                # A needle shorter than this is a substring of the payload's
                # own field names and would fail for a reason about the
                # needle rather than about the payload.
                assert len(preview) >= 8, preview
                assert preview not in blob
        for key in ("trace_id", "previews", "span_seqs", "agent_ids", "summary", "finding_id"):
            assert f'"{key}"' not in blob


# --- 3. a vocabulary computed without the module under test ------------------


def independent_vocabulary() -> set[str]:
    """Every string value a payload may carry, computed from other sources.

    Built from the detector registry, the bundled rate snapshot, the severity
    enum and R29's money format — deliberately **not** from
    ``narrate.client``'s patterns, which are the coder's second oracle and
    the thing this arm exists to be independent of.
    """
    snapshot = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "swarm_observer"
            / "cost"
            / "data"
            / "model_rates.json"
        ).read_text(encoding="utf-8")
    )
    vocabulary: set[str] = set()
    vocabulary |= set(SEVERITIES)
    vocabulary |= set(DETECTOR_SLUGS)
    vocabulary |= {detector.title for detector in ALL_DETECTORS}
    vocabulary |= set(snapshot["models"])
    vocabulary.add(snapshot["meta"]["version"])
    vocabulary.add(SnapshotRateSource().meta.version)
    vocabulary |= {OVERALL_GROUP, OVERALL_TITLE, "1.0.0"}
    return vocabulary


def _strings_in(value: Any) -> set[str]:
    """Every string that appears as a key or a scalar in a JSON document."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set(value) | set().union(*(_strings_in(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_strings_in(item) for item in value), set())
    return set()


#: The JSON *keys* of the payload — field names this package authored. They
#: are not values and are checked separately.
def _keys_in(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys_in(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_keys_in(item) for item in value), set())
    return set()


class TestIndependentVocabularyR42:
    """R42: every string in a payload is one this repository can name elsewhere."""

    def test_r42_every_payload_string_value_is_in_an_independently_computed_set(
        self, sentinel_payload: tuple[Any, list[str], str]
    ) -> None:
        """R42: the check that does not use the patterns it is checking.

        A metric key, a metric value and a money string are the three families
        the vocabulary cannot enumerate from another source, so they are
        allowed by an explicitly narrow rule stated here rather than imported.

        Red when: a payload starts carrying a string this repository cannot
        account for from the registry, the snapshot or the severity enum.
        """
        payloads = sentinel_payload[1]
        vocabulary = independent_vocabulary()
        money = re.compile(r"^-?[0-9]+\.[0-9]{6}$")
        slug = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
        unaccounted: set[str] = set()
        for request in payloads:
            document = json.loads(request)
            keys = _keys_in(document)
            for value in _strings_in(document) - keys:
                if value in vocabulary or money.fullmatch(value) or slug.fullmatch(value):
                    continue
                unaccounted.add(value)
        assert not unaccounted, sorted(unaccounted)

    def test_r42_the_vocabulary_arm_would_notice_an_intruder(self) -> None:
        """R42: the arm above must be able to fail.

        A check whose allowance is "anything that looks like a slug" would let
        a lowercase sentinel through, which is exactly ``SHAPE_LEAVES``. This
        arm shows the *other* half is live: a loud string is unaccounted for.
        """
        vocabulary = independent_vocabulary()
        money = re.compile(r"^-?[0-9]+\.[0-9]{6}$")
        slug = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
        assert LOUD not in vocabulary
        assert not money.fullmatch(LOUD)
        assert not slug.fullmatch(LOUD)

    def test_r42_the_payloads_own_keys_are_this_packages_field_names(
        self, sentinel_payload: tuple[Any, list[str], str]
    ) -> None:
        """R42: the JSON keys are authored, so a key cannot be a leak either."""
        payloads = sentinel_payload[1]
        declared = set()
        for model in (
            NarrationRequest,
            TraceTotals,
            GroupSummary,
            FindingSummary,
            MetricEntry,
            AgentTotals,
            ModelTotals,
        ):
            declared |= set(model.model_fields)
        declared |= set(SEVERITIES)  # severity_counts' own keys
        for request in payloads:
            document = json.loads(request)
            assert _keys_in(document) <= declared, sorted(_keys_in(document) - declared)


# --- the builder's own contract ----------------------------------------------


class TestSummaryBuilderR42:
    """R42/R43: groups, ordering, counts and the two caps."""

    def test_r42_narration_groups_is_overall_then_registry_order(self) -> None:
        """R43: ``overall`` first, then the detectors that fired, in registry order."""
        findings = _findings_for_slugs(DETECTOR_SLUGS[::-1][:3])
        groups = narration_groups(findings)
        assert groups[0] == OVERALL_GROUP
        fired = {finding.detector for finding in findings}
        assert list(groups[1:]) == [slug for slug in DETECTOR_SLUGS if slug in fired]

    def test_r42_a_detector_with_no_findings_gets_no_group(self) -> None:
        """R43 (A-e2): "one paragraph per *finding group*", read literally."""
        assert narration_groups(()) == (OVERALL_GROUP,)
        findings = _findings_for_slugs((DETECTOR_SLUGS[0],))
        assert set(narration_groups(findings)) == {OVERALL_GROUP, DETECTOR_SLUGS[0]}

    def test_r42_every_group_key_is_one_the_request_type_admits(self) -> None:
        """R42: the group vocabulary is the registry's, not a second list."""
        assert set(NARRATION_GROUPS) == {OVERALL_GROUP, *DETECTOR_SLUGS}
        assert (
            frozenset({OVERALL_TITLE, *(detector.title for detector in ALL_DETECTORS)})
            == NARRATION_TITLES
        )

    def test_r42_severity_counts_carries_every_severity_at_zero(self) -> None:
        """R42/R47: a missing key would read as "no critical findings"."""
        counts = severity_counts(())
        assert counts == dict.fromkeys(sorted(SEVERITIES), 0)
        assert list(counts) == sorted(counts)

    def test_r42_the_per_group_detail_is_capped(self) -> None:
        """R42 (A-e5): an unbounded payload is one a provider refuses."""
        findings = _findings_for_slugs(
            (DETECTOR_SLUGS[0],) * (MAX_FINDINGS_PER_GROUP + 5), distinct=True
        )
        summary = group_summary(DETECTOR_SLUGS[0], findings)
        assert summary.findings == MAX_FINDINGS_PER_GROUP + 5
        assert len(summary.detail) == MAX_FINDINGS_PER_GROUP

    def test_r42_the_cost_rows_are_capped_by_the_builder_not_the_caller(self) -> None:
        """R42 (A-e5): ``build_totals`` applies the cap itself."""
        rows = tuple(
            AgentTotals(
                agent_index=index,
                priced_spans=0,
                unpriced_spans=0,
                tokens=0,
                cost_usd="0.000000",
            )
            for index in range(MAX_TOTAL_ROWS + 7)
        )
        totals = build_totals(
            findings=(),
            agents=len(rows),
            spans=0,
            model_calls=0,
            tokens=0,
            cost_usd="0.000000",
            priced_spans=0,
            unpriced_spans=0,
            by_agent=rows,
        )
        assert totals.agents == MAX_TOTAL_ROWS + 7
        assert len(totals.by_agent) == MAX_TOTAL_ROWS

    def test_r42_the_three_bounds_are_the_numbers_the_spec_and_a_e5_name(self) -> None:
        """R43/R42: 800, 20 and 20 as **literals**, not as the constants.

        Every other assertion about a bound in this suite is written in terms
        of the constant, so moving the constant moves the expectation with it
        — increment 4's ``I-H02``, which the mutation sweep found again here
        on all three of these. R43 says 800 in its own text; A-e5 says 20 and
        20 in the coder's.

        Red when: a bound is changed without the requirement or the
        assumption that names it being changed too.
        """
        assert MAX_PARAGRAPH_CHARS == 800
        assert MAX_FINDINGS_PER_GROUP == 20
        assert MAX_TOTAL_ROWS == 20

    def test_r42_a_money_string_is_pinned_to_six_decimal_places(self) -> None:
        """R29/R42: the payload carries the exact string both reports print."""
        for good in ("0.000000", "12.345678", "-0.000001"):
            AgentTotals(agent_index=0, priced_spans=0, unpriced_spans=0, tokens=0, cost_usd=good)
        for bad in ("0.0", "0", "0.0000000", "1e-6", " 0.000000"):
            with pytest.raises(ValidationError):
                AgentTotals(agent_index=0, priced_spans=0, unpriced_spans=0, tokens=0, cost_usd=bad)

    def test_r42_the_model_rows_are_capped_too(self) -> None:
        """R42 (A-e5): both cost groupings are bounded, not just the first.

        Red when: the ``or`` in the row-cap validator loses its second
        disjunct — a guard-drop nothing else in the suite would see.
        """
        rows = tuple(
            ModelTotals(model_key=f"model-{index}", priced_spans=0, tokens=0, cost_usd="0.000000")
            for index in range(MAX_TOTAL_ROWS + 3)
        )
        with pytest.raises(ValidationError):
            empty_totals(by_model=rows)
        assert (
            len(
                build_totals(
                    findings=(),
                    agents=0,
                    spans=0,
                    model_calls=0,
                    tokens=0,
                    cost_usd="0.000000",
                    priced_spans=0,
                    unpriced_spans=0,
                    by_model=rows,
                ).by_model
            )
            == MAX_TOTAL_ROWS
        )

    def test_r42_the_cap_keeps_the_findings_a_reader_would_look_at_first(self) -> None:
        """R42 (A-e5): "the cap is applied *after* the report's own ordering".

        Red when: the severity key loses its sign, or the ordering is
        dropped — at which point a group with 200 info findings and one
        critical would describe twenty info findings to the narrator.
        """
        trace = _a_trace()
        slug = DETECTOR_SLUGS[0]
        findings = [
            build_finding(
                trace=trace,
                detector=slug,
                severity="info",
                summary="an info finding",
                span_seqs=(index,),
                agent_ids=("root",),
                metrics={"occurrences": 2},
            )
            for index in range(MAX_FINDINGS_PER_GROUP + 5)
        ]
        findings.append(
            build_finding(
                trace=trace,
                detector=slug,
                severity="critical",
                summary="the one that matters",
                span_seqs=(0, 1),
                agent_ids=("root",),
                metrics={"occurrences": 9},
            )
        )
        summary = group_summary(slug, findings)
        assert len(summary.detail) == MAX_FINDINGS_PER_GROUP
        assert summary.detail[0].severity == "critical"

    def test_r42_the_severity_counts_are_the_findings_severities(self) -> None:
        """R42: the tally the narrator writes its paragraph from.

        Red when: the counter is incremented by anything but one, or counts
        the wrong findings. Nothing else in the suite reads these numbers —
        the mutation sweep killed nothing with ``+= 2``.
        """
        trace = _a_trace()
        findings = tuple(
            build_finding(
                trace=trace,
                detector=DETECTOR_SLUGS[0],
                severity=severity,
                summary="s",
                span_seqs=(index,),
                agent_ids=("root",),
                metrics={"occurrences": 2},
            )
            for index, severity in enumerate(("info", "warning", "warning", "critical"))
        )
        assert severity_counts(findings) == {"critical": 1, "info": 1, "warning": 2}
        assert group_summary(OVERALL_GROUP, findings).severity_counts == {
            "critical": 1,
            "info": 1,
            "warning": 2,
        }

    def test_r42_a_findings_counts_are_its_own_agents_and_spans(self) -> None:
        """R42: ``evidence_spans`` and ``agents`` are two different numbers.

        They are the only two counts in ``FindingSummary`` that could be
        confused for each other, and nothing distinguished them: a mutant
        that reported ``len(span_seqs)`` for both survived the first sweep.
        """
        trace = _a_trace()
        finding = build_finding(
            trace=trace,
            detector=DETECTOR_SLUGS[0],
            severity="info",
            summary="s",
            span_seqs=(0, 1, 2, 3),
            agent_ids=("alpha", "beta"),
            metrics={"occurrences": 2},
        )
        summary = group_summary(DETECTOR_SLUGS[0], (finding,)).detail[0]
        assert summary.evidence_spans == 4
        assert summary.agents == 2

    def test_r42_the_detail_order_does_not_depend_on_the_callers_order(self) -> None:
        """R42/R47: ``_ordered``'s ``finding_id`` tiebreaker, driven out of order.

        R13 already returns findings in ``finding_id`` order and Python's
        sort is stable, so every input that came from the registry is already
        ordered and an end-to-end test cannot see the tiebreaker. This hands
        ``group_summary`` a reversed list directly — the increment-4 review's
        ``I-H06``, one module over.
        """
        trace = _a_trace()
        findings = [
            build_finding(
                trace=trace,
                detector=DETECTOR_SLUGS[0],
                severity="info",
                summary="s",
                span_seqs=(index,),
                agent_ids=("root",),
                metrics={"occurrences": index + 2},
            )
            for index in range(6)
        ]
        forward = group_summary(DETECTOR_SLUGS[0], findings)
        backward = group_summary(DETECTOR_SLUGS[0], list(reversed(findings)))
        assert forward.detail == backward.detail
        ids = sorted(finding.finding_id for finding in findings)
        assert [entry.metrics for entry in forward.detail] == [
            tuple(
                MetricEntry(key=key, value=value)
                for key, value in sorted(
                    next(f for f in findings if f.finding_id == fid).metrics.items()
                )
            )
            for fid in ids
        ]

    def test_r42_a_groups_waste_is_its_own_and_not_the_runs(self) -> None:
        """R42: "per-group" totals really are per group.

        Red when: the group's ``wasted_tokens`` is summed over every finding
        instead of over its members, which no end-to-end assertion would
        notice on a trace where one detector dominates.
        """
        from swarm_observer.model.trace import TokenUsage

        trace = _a_trace()
        first, second = DETECTOR_SLUGS[0], DETECTOR_SLUGS[1]
        findings = [
            build_finding(
                trace=trace,
                detector=first,
                severity="info",
                summary="one",
                span_seqs=(0,),
                agent_ids=("root",),
                metrics={"occurrences": 2},
                wasted=TokenUsage(input_tokens=100),
            ),
            build_finding(
                trace=trace,
                detector=second,
                severity="info",
                summary="two",
                span_seqs=(1,),
                agent_ids=("root",),
                metrics={"occurrences": 3},
                wasted=TokenUsage(input_tokens=7),
            ),
        ]
        assert group_summary(first, findings).wasted_tokens == 100
        assert group_summary(second, findings).wasted_tokens == 7
        assert group_summary(OVERALL_GROUP, findings).wasted_tokens == 107

    def test_r42_a_groups_title_is_its_own_detectors_title(self) -> None:
        """R42: the title names the group, not whichever detector came first."""
        for detector in ALL_DETECTORS:
            findings = _findings_for_slugs((detector.slug,))
            assert group_summary(detector.slug, findings).title == detector.title
        assert group_summary(OVERALL_GROUP, ()).title == OVERALL_TITLE

    def test_r42_serialize_request_is_sorted_ascii_and_separator_stable(self) -> None:
        """R42/R47: the payload is a function of the findings, hash seed included."""
        request = a_request()
        blob = serialize_request(request)
        assert blob == json.dumps(
            json.loads(blob), sort_keys=True, ensure_ascii=True, separators=(",", ":")
        )
        assert blob.isascii()
        assert serialize_request(request) == blob

    def test_r42_every_request_of_one_run_differs_only_in_the_group(self) -> None:
        """R42: "the same figures for every paragraph", asserted rather than stated."""
        findings = _findings_for_slugs(DETECTOR_SLUGS[:3])
        totals = empty_totals()
        requests = build_requests(findings=findings, totals=totals, rate_snapshot_version="1.0.0")
        bodies = {
            json.dumps(
                {k: v for k, v in request.model_dump(mode="json").items() if k != "group"},
                sort_keys=True,
            )
            for request in requests.values()
        }
        assert len(bodies) == 1
        assert set(requests) == set(narration_groups(findings))


def _a_trace() -> Any:
    """A minimal ``Trace`` so ``build_finding`` can hash a real ``trace_id`` (R15)."""
    builder = TraceBuilder()
    for _ in range(MAX_FINDINGS_PER_GROUP + 8):
        builder.model_call()
    return builder.build()


def _findings_for_slugs(slugs: Any, *, distinct: bool = False) -> tuple[Any, ...]:
    """One finding per slug, built through the real ``Finding`` validators."""
    trace = _a_trace()
    findings = []
    for index, slug in enumerate(slugs):
        findings.append(
            build_finding(
                trace=trace,
                detector=slug,
                severity="info",
                summary="a summary with no trace text",
                span_seqs=(index,) if distinct else (0,),
                agent_ids=("root",),
                metrics={"occurrences": index + 2},
            )
        )
    return tuple(findings)
