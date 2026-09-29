"""R50 canary: a credential in ``metrics.tool_name`` reaches a report if redaction stops.

Not one of R50's seven. Added to the ledger by the increment-2 review as S13 and
owed by increment 4, because the subject — a *second* renderer over the same
``metrics`` — arrived with the HTML report.

The finding it protects is worth restating, because it is the one place in this
product where a guard that looks sufficient is not. R16 constrains the single
trace-derived value allowed into ``Finding.metrics`` to
``^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$``, and that alphabet admits no ``<``, ``>``,
quote, backtick, ``=``, space or control character — so no *markup* payload can
be a legal tool name. It admits ``AKIAIOSFODNN7EXAMPLE`` perfectly. R16 is a
**shape** check, and a shape check is not a secret check. The only thing keeping
that string out of a rendered report is ``sanitize.metric_value`` running R33's
redactor over it, in both renderers.

Two mutations, both in memory:

* ``metric_value`` replaced by a pass-through, separately in each renderer, so
  the canary names *which* document leaked;
* ``TRACE_DERIVED_METRIC_KEYS`` emptied, which is the subtler regression: the
  function still runs and still looks like a guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm_observer.report import html as html_module
from swarm_observer.report import json_out as json_module

from ..hostile_corpus import CONFORMING_CREDENTIAL_TOOL_NAME, write_hostile_trace
from ..pipeline import analyze_paths

MARKER = "[redacted:aws_key_id]"


def passthrough(key: str, value: int | str) -> int | str:
    """The mutation: ``metric_value`` with its redaction removed."""
    return value


def test_r50_the_unmutated_report_redacts_the_credential_tool_name(tmp_path: Path) -> None:
    """R50: the control arm, and the proof the credential is really in the input.

    ``Finding.metrics`` carries the raw credential — R16 admits it — so the
    first assertion is what makes the other two mean anything. Without it,
    "the credential is absent" would be satisfied by a trace that never had one.

    Red when: the corpus stops putting an R16-conforming credential in a tool
    name, at which point S13 is no longer covered by anything.
    """
    analysis = analyze_paths(write_hostile_trace(tmp_path))
    raw = {finding.metrics.get("tool_name") for finding in analysis.findings}
    assert CONFORMING_CREDENTIAL_TOOL_NAME in raw
    assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.html
    assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.json
    assert MARKER in analysis.html and MARKER in analysis.json


def test_r50_the_html_leaks_when_metric_value_stops_redacting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R50/S13: the HTML renderer's own call is load-bearing.

    Red when: ``html.py`` stops calling ``metric_value`` on the metrics line, or
    starts calling something else — at which point this canary would report a
    clean kill it did not earn.
    """
    monkeypatch.setattr(html_module, "metric_value", passthrough)
    analysis = analyze_paths(write_hostile_trace(tmp_path))
    with pytest.raises(AssertionError):
        assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.html
    assert f"tool_name&#x3D;{CONFORMING_CREDENTIAL_TOOL_NAME}" in analysis.html
    # The JSON report is untouched by this mutation, which is what shows the two
    # renderers are separately guarded rather than sharing one call site.
    assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.json


def test_r50_the_json_leaks_when_metric_value_stops_redacting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R50/S13: and the JSON renderer's call, independently.

    Red when: ``json_out.metrics_document`` stops routing through
    ``metric_value`` — increment 3's own finding, in the other document.
    """
    monkeypatch.setattr(json_module, "metric_value", passthrough)
    analysis = analyze_paths(write_hostile_trace(tmp_path))
    with pytest.raises(AssertionError):
        assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.json
    document = json.loads(analysis.json)
    leaked = [
        finding["metrics"]["tool_name"]
        for finding in document["findings"]
        if "tool_name" in finding["metrics"]
    ]
    assert CONFORMING_CREDENTIAL_TOOL_NAME in leaked
    assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.html


def test_r50_emptying_the_key_set_leaks_while_the_guard_still_appears_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R50/S13: the regression that leaves the guard in place and removes its effect.

    ``metric_value`` consults ``TRACE_DERIVED_METRIC_KEYS`` to decide which
    values are trace-derived. Empty it and every call still happens, every
    renderer still routes through the boundary, and nothing is redacted. This is
    the shape a "tidy up the constants" change takes.

    Red when: the key set stops being the decision — which would be an
    improvement, and must then be a deliberate change.
    """
    import swarm_observer.report.sanitize as sanitize_module

    monkeypatch.setattr(sanitize_module, "TRACE_DERIVED_METRIC_KEYS", frozenset())
    analysis = analyze_paths(write_hostile_trace(tmp_path))
    with pytest.raises(AssertionError):
        assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.html
    with pytest.raises(AssertionError):
        assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.json


def test_r50_the_key_set_names_tool_name_and_nothing_a_detector_does_not_produce(
    tmp_path: Path,
) -> None:
    """R16/S13: the set is exactly the trace-derived metric keys, and it is not empty.

    An empty set would make the guard inert; a set naming keys no detector emits
    would make it look wider than it is.

    Red when: ``tool_name`` leaves the set, or a key is added without a detector
    that produces it.
    """
    from swarm_observer.detect.base import TRACE_DERIVED_METRIC_KEYS

    assert frozenset({"tool_name"}) == TRACE_DERIVED_METRIC_KEYS
    analysis = analyze_paths(write_hostile_trace(tmp_path))
    emitted = {key for finding in analysis.findings for key in finding.metrics}
    assert emitted >= TRACE_DERIVED_METRIC_KEYS


def test_r50_the_patches_are_undone(tmp_path: Path) -> None:
    """R50: ``monkeypatch`` reversed every mutation above.

    Red when: a future edit patches a module without ``monkeypatch``, which
    would leave the boundary removed for every later test in the session.
    """
    from swarm_observer.report.sanitize import metric_value

    assert html_module.metric_value is metric_value
    assert json_module.metric_value is metric_value
    analysis = analyze_paths(write_hostile_trace(tmp_path))
    assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.html
    assert CONFORMING_CREDENTIAL_TOOL_NAME not in analysis.json
