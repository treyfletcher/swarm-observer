"""R50 canary: R43's fallback stops being visible, and the guards notice.

Not one of R50's seven. Added to the ledger by the increment-5 tester, in the
same commit as the canary, the way ``redaction_pattern_removed`` moved in
increment 3 and the four increment-4 canaries moved in increment 4.

The subject is R43's central promise and it has an unusual failure mode: when
it breaks, **nothing crashes and nothing is missing**. A paragraph still
renders, the section still exists, the exit code is still unchanged. What
changes is that a reader can no longer tell swarm-observer's own deterministic
summary from a language model's prose — which is the difference between a
number a reader may act on and a sentence a compromised narrator wrote. A guard
whose failure is silent and cosmetic is exactly the guard that needs a proof it
can fail.

Three mutations, each in memory, each aimed at one marker:

* the DOM class emptied — R43's machine-readable marker;
* the visible prefix emptied — R43's human-readable marker;
* ``FATAL_CODES`` emptied — the A-e6 short-circuit, whose regression is not a
  missing paragraph but **one outbound request per group** on a run whose
  credential was already refused.

Each is paired with a control arm, because "the report no longer says
``Deterministic summary:``" is also what a report with no fallbacks looks like.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swarm_observer.narrate import narrator as narrator_module
from swarm_observer.narrate.client import NarrationRequest, NarrationResponse, NarratorAuthError
from swarm_observer.report import html as html_module
from swarm_observer.report.narrative import (
    FALLBACK_CLASS,
    FALLBACK_PREFIX,
    Narrative,
    NarrativeParagraph,
)

from ..pipeline import analyze_paths
from ..sentinel_trace import sentinel_analysis, totals_for, write_sentinel_trace


def a_fallback_narrative() -> Narrative:
    """One deterministic paragraph and one model paragraph."""
    return Narrative(
        paragraphs=(
            NarrativeParagraph(
                group="overall",
                title="the whole run",
                text="swarm-observer's own summary",
                fallback=True,
                reason="timeout",
            ),
            NarrativeParagraph(
                group="agent_loop",
                title="the whole run",
                text="the model's own prose",
                fallback=False,
            ),
        ),
        calls=2,
    )


def test_r50_the_unmutated_report_marks_the_fallback_both_ways(tmp_path: Path) -> None:
    """R50: the control arm — both markers are present, and on the right paragraph.

    Red when: the renderer stops pairing the class with the prefix, at which
    point the two mutations below would have nothing to remove.
    """
    analysis = analyze_paths(write_sentinel_trace(tmp_path / "t"), narrative=a_fallback_narrative())
    marked = f'<p class="{FALLBACK_CLASS}">{FALLBACK_PREFIX} swarm-observer&#x27;s own summary</p>'
    assert marked in analysis.html
    assert analysis.html.count(FALLBACK_CLASS) == 1
    assert analysis.html.count(FALLBACK_PREFIX) == 1
    assert "the model&#x27;s own prose" in analysis.html


def test_r50_the_report_stops_naming_the_fallback_when_the_class_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R50/R43: the DOM marker is load-bearing, and its absence is detectable.

    Red when: ``html.py`` stops reading ``FALLBACK_CLASS`` from
    ``report/narrative.py`` — at which point this canary would report a kill
    it did not earn.
    """
    monkeypatch.setattr(html_module, "FALLBACK_CLASS", "narrative")
    analysis = analyze_paths(write_sentinel_trace(tmp_path / "t"), narrative=a_fallback_narrative())
    with pytest.raises(AssertionError):
        assert FALLBACK_CLASS in analysis.html
    # ...and the paragraph is still there, which is why the loss is silent.
    assert "swarm-observer&#x27;s own summary" in analysis.html


def test_r50_the_report_stops_naming_the_fallback_when_the_prefix_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R50/R43: the visible marker is load-bearing too.

    R43 names both. A reader of the rendered page only ever sees this one.
    """
    monkeypatch.setattr(html_module, "FALLBACK_PREFIX", "")
    analysis = analyze_paths(write_sentinel_trace(tmp_path / "t"), narrative=a_fallback_narrative())
    with pytest.raises(AssertionError):
        assert FALLBACK_PREFIX in analysis.html
    assert FALLBACK_CLASS in analysis.html


def test_r50_an_empty_fatal_set_turns_one_refused_credential_into_n_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R50/R43 (A-e6): the short-circuit's regression is invisible in the document.

    With ``FATAL_CODES`` emptied, an auth failure falls every group back
    exactly as before — the paragraphs, the class, the prefix and the exit
    code are all identical — and the run makes one outbound request per group
    instead of one. Nothing about the rendered report can tell the difference,
    which is why the assertion this canary breaks is a **call count**.
    """
    analysis = sentinel_analysis(tmp_path / "t")
    groups = len({finding.detector for finding in analysis.findings}) + 1
    assert groups >= 4

    class Refusing:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request: NarrationRequest) -> NarrationResponse:
            self.calls += 1
            raise NarratorAuthError("auth_rejected")

    def narrate_with(client: Any) -> Any:
        return narrator_module.narrate(
            client=client,
            findings=analysis.findings,
            totals=totals_for(analysis),
            rate_snapshot_version=analysis.cost.meta.version,
        )

    control = Refusing()
    unmutated = narrate_with(control)
    assert control.calls == unmutated.calls == 1
    assert unmutated.fallbacks == len(unmutated.paragraphs) == groups

    monkeypatch.setattr(narrator_module, "FATAL_CODES", frozenset())
    mutated_client = Refusing()
    mutated = narrate_with(mutated_client)
    with pytest.raises(AssertionError):
        assert mutated_client.calls == 1
    assert mutated_client.calls == groups
    # The observable document is unchanged, which is the finding.
    assert mutated.fallbacks == unmutated.fallbacks
    assert [p.text for p in mutated.paragraphs] == [p.text for p in unmutated.paragraphs]
