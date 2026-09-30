"""R50 canary: the attribute check fails when a trace string reaches a ``data-`` attribute.

R50 names this one verbatim — "the attribute-allowlist test fails when a
trace-derived string is injected into a ``data-`` attribute" — and its subject,
``report.html.attribute_allowlist``, arrived with increment 4.

The canary mutates the **renderer**, not the allowlist, and that direction is
the whole point. ``attribute_allowlist`` is generated from the inputs, so a
renderer that writes a trace string into an attribute produces a value the
allowlist cannot contain. Mutating the allowlist instead would prove only that
a set comparison works.

Two shapes are applied, because the two failure modes read differently:

* a trace-derived string in ``data-detector``, which is what an escaping or
  interpolation slip looks like;
* an attribute the document is allowed to write, carrying a value one integer
  outside the computed set — ``data-seq`` for a span that does not exist. That
  one *looks like a number*, which is the case A-d4 gives as the reason the
  geometry entries are exact value sets rather than "any decimal integer". A
  looser allowlist would miss it.

Both are applied by wrapping ``render_html``'s output in memory. Nothing on disk
is edited.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ..hostile_corpus import write_hostile_trace
from ..pipeline import Analysis, analyze_paths
from ..rendered import parse
from ..test_report_html_r34_r35_r36 import ATTRIBUTE_SHAPES

#: A trace-derived string from the hostile corpus, as it would reach the
#: document if the renderer put it in an attribute.
TRACE_STRING = "MARKERtoolname </script><script> {{7*7}} ../../etc/passwd"


def offenders(analysis: Analysis, html: str) -> list[tuple[str, str, str]]:
    """The attribute values ``html`` holds that ``analysis``'s inputs cannot justify."""
    allowlist = analysis.allowlist()
    return [
        (element, name, value)
        for element, name, value in parse(html).attributes
        if name not in allowlist or value not in allowlist[name]
    ]


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory) -> Analysis:
    return analyze_paths(write_hostile_trace(tmp_path_factory.mktemp("allowlist-canary")))


def test_r50_the_unmutated_document_has_no_offender(analysis: Analysis) -> None:
    """R50: the control arm — a canary whose subject is already red proves nothing.

    Red when: the renderer really does leak an attribute, in which case the two
    tests below would pass for a reason that is not the injection.
    """
    assert offenders(analysis, analysis.html) == []
    assert not {name for _, name, _ in parse(analysis.html).attributes} - set(ATTRIBUTE_SHAPES)


def test_r50_a_trace_string_in_a_data_attribute_is_caught(analysis: Analysis) -> None:
    """R50: the injection R50 names, on the attribute R34 names.

    Red when: the allowlist stops being generated from the inputs — a list
    harvested from the rendered document would contain this value and the check
    would pass.
    """
    injected = analysis.html.replace(
        'data-detector="repeated_tool_call"', f'data-detector="{TRACE_STRING}"', 1
    )
    assert injected != analysis.html, "the anchor for the injection moved"
    found = offenders(analysis, injected)
    with pytest.raises(AssertionError):
        assert not found
    assert any(TRACE_STRING in value for _, _, value in found)


def test_r50_an_integer_outside_the_computed_set_is_caught(analysis: Analysis) -> None:
    """R50/A-d4: a value that *looks like a number* and is not one the inputs produce.

    This is the case a "any decimal integer" allowlist would miss, and it is the
    reason A-d4 makes the geometry entries exact value sets. ``data-seq`` is
    given a seq one past the last span.

    Red when: an allowlist entry is widened to a pattern.
    """
    beyond = len(analysis.trace.spans) + 1
    assert str(beyond) not in analysis.allowlist()["data-seq"]
    injected = re.sub(r'data-seq="\d+"', f'data-seq="{beyond}"', analysis.html, count=1)
    assert injected != analysis.html
    with pytest.raises(AssertionError):
        assert not offenders(analysis, injected)


def test_r50_a_new_attribute_name_is_caught_by_both_oracles(analysis: Analysis) -> None:
    """R50: an attribute *name* nobody declared, which is the likeliest real hole.

    The coder named it: "an attribute I add in a later edit and add to the
    allowlist in the same commit". The generated allowlist would accept it; the
    tester-written :data:`ATTRIBUTE_SHAPES` table would not, because it does not
    know the name. Both are asserted so the pair is shown to be complementary.

    Red when: either oracle starts accepting unknown attribute names.
    """
    injected = analysis.html.replace("<h1>", '<h1 title="whatever">', 1)
    assert injected != analysis.html
    names = {name for _, name, _ in parse(injected).attributes}
    with pytest.raises(AssertionError):
        assert not names - set(ATTRIBUTE_SHAPES)
    with pytest.raises(AssertionError):
        assert not offenders(analysis, injected)


def test_r50_the_control_arm_still_passes_afterwards(analysis: Analysis) -> None:
    """R50: nothing above mutated the analysis, only copies of its bytes.

    Red when: a future edit injects into ``analysis.html`` in place, which would
    make every later test in the module meaningless.
    """
    assert offenders(analysis, analysis.html) == []


def test_r50_the_renderer_on_disk_is_untouched_by_this_module(tmp_path: Path) -> None:
    """R50: the harness discipline — a run killed mid-canary leaves no mutated source.

    Every mutation above is a ``str.replace`` on a copy of the rendered bytes,
    so the module that produced them is unchanged and a fresh render is still
    clean.

    Red when: a future edit mutates the renderer on disk or in ``sys.modules``
    instead of transforming a string.
    """
    fresh = analyze_paths(write_hostile_trace(tmp_path))
    assert offenders(fresh, fresh.html) == []
    assert 'data-detector="repeated_tool_call"' in fresh.html
