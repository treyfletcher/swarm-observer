"""``ReplayTarget`` — the v2 seam, and nothing else (Out of scope, T19, R44).

The spec's "Out of scope" section is explicit about what this file may contain:

    v1 defines only the seam: ``swarm_observer/replay/target.py`` contains a
    ``ReplayTarget`` ``Protocol`` (``replay(trace, selection) -> ReplayResult``)
    and the two frozen models, with **no implementation, no CLI subcommand, no
    import from anywhere else in the package**, and a test asserting exactly
    that.

So: one Protocol, two models, no behaviour. Every symbol below is a shape a v2
implementation would satisfy; none of it runs today, and
``test_r44_nothing_imports_the_replay_seam`` is what keeps that true rather
than merely intended.

**Why the models are shaped this way**, since that is the only design decision
a declaration-only module makes:

* :class:`ReplaySelection` names spans by ``seq`` and not by ``span_id``.
  Both are stable (R5, R6), but ``seq`` is the key a reader has in front of
  them in every table of both reports, and a selection a human types by hand
  from a report should not require a digest.
* :class:`ReplayResult` carries a ``trace_id`` for the trace that was replayed
  and, separately, one for whatever the replay produced. A replay that reports
  only "it differed" is a replay whose output nobody can re-analyze; a replay
  that reports a new ``trace_id`` can be pointed at ``analyze`` and diffed with
  the tools that already exist.
* There is **no** field for cost, waste or findings. Those are computed by
  ``cost/`` and ``detect/`` from a normalized trace, and a replay result that
  carried its own copies would be a second place they could disagree — the
  Modularity notes' "the renderer never computes", applied to a package that
  does not exist yet.
* ``ReplayResult.notes`` is a tuple of **enumerated slugs**, never free text.
  A replay target in v2 will be talking to a live agent runtime, which makes
  its output exactly as attacker-influenced as a trace is, and the cheapest
  moment to say "this field is a closed vocabulary" is before anything fills
  it. This is the increment-4 review's C1 lesson written into a seam rather
  than into a fix: R10 constrained ``ParseWarning.detail`` the same way for the
  same reason, and it is the one field of ``ParseWarning`` that has never
  leaked.
"""

from __future__ import annotations

from typing import Annotated, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from swarm_observer.model.trace import Trace

#: The slug alphabet :attr:`ReplayResult.notes` admits. Same shape R10 uses for
#: a parse warning's code, and for the same reason.
NOTE_SLUG_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

#: :data:`NOTE_SLUG_PATTERN` as an annotation, so the constraint is **applied**
#: and not merely declared. The first draft of this module defined the pattern
#: and never used it, which made the docstring's "never free text" a sentence
#: nothing enforced — a constraint stated and not applied is this project's
#: signature defect in the one module whose whole purpose is to state
#: constraints. Caught by driving the validator by hand before writing it up.
NoteSlug = Annotated[str, Field(pattern=NOTE_SLUG_PATTERN)]


class ReplaySelection(BaseModel):
    """Which part of a trace a v2 replay would re-execute.

    Frozen and ``extra="forbid"`` like every model in this package: a field a
    caller did not mean to set is a construction error rather than a value that
    silently changes what gets replayed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The spans to replay, by ``Span.seq`` (R6), ascending and distinct. Empty
    #: means the whole trace, which is the only defensible default: a selection
    #: that silently replayed *nothing* would look exactly like a replay that
    #: found no difference.
    span_seqs: tuple[int, ...] = ()
    #: Restrict to these agents, by ``AgentRun.agent_id``. Empty means all.
    agent_ids: tuple[str, ...] = ()
    #: A v2 target must refuse to run longer than this. Named here rather than
    #: left to an implementation because "the replay hung" is the failure mode
    #: a seam can prevent and a target cannot be trusted to.
    timeout_seconds: int = Field(default=300, ge=1)
    #: When true, a target may not perform any side effect outside its own
    #: sandbox — no writes, no network the replayed run did not make. Defaults
    #: to true, because a replay of a trace that deleted a directory should not
    #: delete it again by default.
    dry_run: bool = True


class ReplayResult(BaseModel):
    """What a v2 replay would report. No findings, no costs — see the module docstring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The ``trace_id`` (R5) of the trace that was replayed.
    source_trace_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    #: The ``trace_id`` of the trace the replay produced, or ``None`` when the
    #: target produced no analyzable artefact.
    replay_trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    #: How many spans the target actually re-executed.
    replayed_spans: int = Field(default=0, ge=0)
    #: How many of those diverged from the recorded span.
    diverged_spans: int = Field(default=0, ge=0)
    #: Enumerated slugs only — never a message from the replayed system.
    #: Constrained by :data:`NoteSlug`, so the rule is enforced by the model
    #: rather than asked of a future implementer. A field-level constraint is
    #: a declaration, not behaviour, and every other model in this package
    #: states its alphabet the same way.
    notes: tuple[NoteSlug, ...] = ()


@runtime_checkable
class ReplayTarget(Protocol):
    """The v2 seam: re-execute part of a trace against some runtime.

    Nothing implements this in v1. A v2 target is a new module under
    ``replay/`` plus a registry entry plus a CLI subcommand, with zero edits to
    ``model/``, ``ingest/``, ``detect/``, ``cost/``, ``report/`` or
    ``narrate/`` — which is the whole value of writing the signature down now.
    """

    def replay(self, trace: Trace, selection: ReplaySelection) -> ReplayResult:
        """Re-execute ``selection`` of ``trace`` and report what differed."""
        ...  # pragma: no cover - a Protocol body, and nothing implements it


__all__ = ["NOTE_SLUG_PATTERN", "ReplayResult", "ReplaySelection", "ReplayTarget"]
