"""The replay seam's validators, driven — because nothing else ever will.

The spec's "Out of scope" section says ``replay/target.py`` holds a Protocol
and two frozen models, imported by nothing. ``test_r44_nothing_imports_the_replay_seam``
enforces the "imported by nothing" half **over the AST**, which means it never
imports the module either: before this file, a coverage run of the whole suite
reported ``swarm_observer.replay`` as *never imported*, so not one of its
constraints had ever been evaluated.

That matters more here than it would anywhere else. The coder records that the
first draft of the module declared ``NOTE_SLUG_PATTERN`` and never applied it,
so its docstring's "notes are enumerated slugs, never free text" was a sentence
nothing enforced — a constraint stated and not applied, in the one module whose
entire purpose is to state constraints. The fix is checked in; this is what
says it stayed fixed.

Importing the module from a test does not violate R44: that rule is about
modules under ``swarm_observer/``, and the AST walk it uses only looks there.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from swarm_observer.model.trace import Trace
from swarm_observer.replay.target import (
    NOTE_SLUG_PATTERN,
    ReplayResult,
    ReplaySelection,
    ReplayTarget,
)

HEX16 = "0123456789abcdef"


class TestReplaySelectionOutOfScope:
    """Out of scope / T19: the selection model's defaults and refusals."""

    def test_r44_the_defaults_replay_the_whole_trace_and_touch_nothing(self) -> None:
        """Out of scope: "empty means the whole trace"; ``dry_run`` defaults true."""
        selection = ReplaySelection()
        assert selection.span_seqs == ()
        assert selection.agent_ids == ()
        assert selection.timeout_seconds == 300
        assert selection.dry_run is True

    @pytest.mark.parametrize("timeout", [0, -1, -300])
    def test_r44_a_timeout_below_one_second_is_refused(self, timeout: int) -> None:
        """Out of scope: "a v2 target must refuse to run longer than this".

        A zero timeout is a target that can never run, which is the same
        failure as a selection that silently replays nothing.
        """
        with pytest.raises(ValidationError):
            ReplaySelection(timeout_seconds=timeout)

    def test_r44_a_one_second_timeout_is_accepted(self) -> None:
        """Out of scope: the control arm — ``ge=1`` is a bound, not a ban."""
        assert ReplaySelection(timeout_seconds=1).timeout_seconds == 1

    def test_r44_the_selection_is_frozen_and_closed(self) -> None:
        """Out of scope: "frozen and ``extra="forbid"`` like every model"."""
        selection = ReplaySelection()
        with pytest.raises(ValidationError):
            ReplaySelection(dryrun=True)  # type: ignore[call-arg]
        with pytest.raises(Exception):  # noqa: B017 - pydantic's frozen error
            selection.dry_run = False  # type: ignore[misc]


class TestReplayResultOutOfScope:
    """Out of scope / T19: the result model, including the note-slug constraint."""

    def test_r44_a_source_trace_id_must_be_sixteen_hex_characters(self) -> None:
        """R5: the id shape the rest of the product pins."""
        assert ReplayResult(source_trace_id=HEX16).source_trace_id == HEX16
        for bad in ("nothex", HEX16 + "0", HEX16.upper(), "", "../../etc/passwd"):
            with pytest.raises(ValidationError):
                ReplayResult(source_trace_id=bad)

    def test_r44_the_replay_trace_id_is_optional_but_still_shaped(self) -> None:
        """Out of scope: ``None`` when the target produced no analyzable artefact."""
        assert ReplayResult(source_trace_id=HEX16).replay_trace_id is None
        assert ReplayResult(source_trace_id=HEX16, replay_trace_id=HEX16).replay_trace_id == HEX16
        with pytest.raises(ValidationError):
            ReplayResult(source_trace_id=HEX16, replay_trace_id="a message from the runtime")

    def test_r44_notes_are_enumerated_slugs_and_the_pattern_is_applied(self) -> None:
        """Out of scope: the constraint the first draft declared and did not apply.

        A v2 target talks to a live agent runtime, so its output is exactly as
        attacker-influenced as a trace. Free text here would be a fifth
        untrusted string source arriving with no boundary.

        Red when: the ``NoteSlug`` annotation is replaced by a bare ``str``,
        which is precisely the regression this test exists for.
        """
        assert ReplayResult(source_trace_id=HEX16, notes=("ok", "diverged_early")).notes == (
            "ok",
            "diverged_early",
        )
        for bad in (
            "provider said: boom",
            "Uppercase",
            "</script><script>alert(1)</script>",
            "AKIAIOSFODNN7EXAMPLE",
            "",
            "a" * 65,
        ):
            with pytest.raises(ValidationError):
                ReplayResult(source_trace_id=HEX16, notes=(bad,))

    def test_r44_the_note_pattern_constant_and_the_field_agree(self) -> None:
        """Out of scope: a declared pattern that the field does not use is the defect.

        Asserted by behaviour rather than by reading the annotation: a string
        the constant admits is accepted and one it refuses is rejected, so the
        two cannot drift apart silently.
        """
        import re

        good = "a_slug_9"
        bad = "A_slug"
        assert re.fullmatch(NOTE_SLUG_PATTERN, good)
        assert not re.fullmatch(NOTE_SLUG_PATTERN, bad)
        ReplayResult(source_trace_id=HEX16, notes=(good,))
        with pytest.raises(ValidationError):
            ReplayResult(source_trace_id=HEX16, notes=(bad,))

    def test_r44_counts_cannot_be_negative(self) -> None:
        """Out of scope: ``ge=0`` on both counters."""
        for field in ("replayed_spans", "diverged_spans"):
            with pytest.raises(ValidationError):
                ReplayResult(source_trace_id=HEX16, **{field: -1})

    def test_r44_the_result_carries_no_cost_finding_or_free_text_field(self) -> None:
        """Out of scope: "no field for cost, waste or findings".

        Derived from ``model_fields`` rather than read from the docstring, so
        a v2 addition has to change this test deliberately.
        """
        assert set(ReplayResult.model_fields) == {
            "source_trace_id",
            "replay_trace_id",
            "replayed_spans",
            "diverged_spans",
            "notes",
        }
        with pytest.raises(ValidationError):
            ReplayResult(source_trace_id=HEX16, cost_usd="1.000000")  # type: ignore[call-arg]


class TestReplayTargetProtocolOutOfScope:
    """Out of scope / T19: a Protocol, and no implementation."""

    def test_r44_a_stub_satisfies_the_protocol(self) -> None:
        """Out of scope: the signature is usable, which is the point of writing it down."""

        class Stub:
            def replay(self, trace: Trace, selection: ReplaySelection) -> ReplayResult:
                return ReplayResult(source_trace_id=HEX16)

        assert isinstance(Stub(), ReplayTarget)
        signature = inspect.signature(ReplayTarget.replay)
        assert list(signature.parameters) == ["self", "trace", "selection"]

    def test_r44_something_without_replay_is_not_a_target(self) -> None:
        """Out of scope: the control arm for ``runtime_checkable``."""

        class NotATarget:
            def run(self) -> None:  # pragma: no cover - never called
                raise NotImplementedError

        assert not isinstance(NotATarget(), ReplayTarget)

    def test_r44_the_module_defines_no_implementation(self) -> None:
        """Out of scope: "no implementation" — nothing here is a concrete target."""
        import swarm_observer.replay.target as module

        concrete = [
            name
            for name, value in vars(module).items()
            if inspect.isclass(value)
            and value is not ReplayTarget
            and not name.startswith("_")
            and isinstance(value, type)
            and issubclass(value, ReplayTarget)
        ]
        assert concrete == []

    def test_r44_the_replay_package_re_exports_nothing(self) -> None:
        """R44: ``replay/__init__.py`` must not import ``target``.

        If it did, ``import swarm_observer.replay`` would import the seam and
        "imported by nothing" would be false the first time anything touched
        the package.
        """
        import swarm_observer.replay as package

        exported: Any = getattr(package, "__all__", ())
        assert tuple(exported) == ()
        assert not hasattr(package, "ReplayTarget")
