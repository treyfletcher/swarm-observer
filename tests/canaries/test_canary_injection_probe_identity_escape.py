"""R50 canary: the R51 injection probe fails when ``escape_html`` is the identity.

R50 names this one verbatim — "the injection probe (R51) fails when
``escape_html`` is monkeypatched to the identity function" — and AC14 repeats
it. Its subject arrived with increment 4's HTML renderer.

What the canary has to get right is *which* assertions go red, because the
interesting failure is not "something broke". With escaping removed:

* the payload ``</script><script>alert(1)</script>`` closes the one ``<script>``
  element early, so the document parses as having **two** scripts and the SHA-256
  of the first no longer matches the pin;
* ``"><img src=x onerror=alert(1)>`` becomes a real ``<img>`` element with a real
  ``on*`` attribute, so R35's element check and R51's no-``on*`` check both fail;
* attribute values appear that the generated allowlist has never heard of.

Each is asserted separately, so a future change that fixes one of them without
fixing the others cannot make this canary report a clean kill.

The patch is applied to ``report/html.py``'s reference to ``escape_html``, not
to ``report/escape.py``'s definition, and it is undone by
``monkeypatch``. Nothing on disk is edited, so a run killed mid-test leaves no
mutated source behind — the harness discipline the increment-2 review recorded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm_observer.report import html as html_module
from swarm_observer.report.html import SCRIPT_SHA256

from ..hostile_corpus import write_hostile_trace
from ..pipeline import analyze_paths
from ..rendered import parse, sha256_text
from ..test_report_html_r34_r35_r36 import ATTRIBUTE_SHAPES, R35_FORBIDDEN_ELEMENTS


def identity(text: str) -> str:
    """The mutation: R32's boundary, doing nothing."""
    return text


class TestTheProbeGoesRedWithoutEscapingR50:
    """R50/AC14: every arm of the R51 probe that escaping is load-bearing for."""

    def test_r50_the_unmutated_probe_is_green(self, tmp_path: Path) -> None:
        """R50: the control arm. A canary whose subject is already red proves nothing.

        Red when: the probe itself is broken, in which case the assertions below
        would "pass" for a reason that has nothing to do with the mutation.
        """
        analysis = analyze_paths(write_hostile_trace(tmp_path))
        document = parse(analysis.html)
        assert document.tag_counts.get("script") == 1
        assert sha256_text(document.script_body()) == SCRIPT_SHA256
        assert not [name for _, name, _ in document.attributes if name.startswith("on")]
        assert not {name for _, name, _ in document.attributes} - set(ATTRIBUTE_SHAPES)

    def test_r50_the_one_script_check_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R50: ``</script>`` in a text node closes the element when it is not escaped.

        Red when: the mutation stops mattering — which would mean escaping had
        stopped being what keeps the payload inside a text node.
        """
        monkeypatch.setattr(html_module, "escape_html", identity)
        analysis = analyze_paths(write_hostile_trace(tmp_path))
        document = parse(analysis.html)
        with pytest.raises(AssertionError):
            assert document.tag_counts.get("script") == 1

    def test_r50_the_pinned_script_digest_check_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R50/R34: the digest is taken from the parsed element, so it moves.

        This is the arm that shows why R34's pin must be read off the *parsed*
        document: the module constant is untouched by this mutation, so a
        constant-against-constant assertion would still pass.
        """
        monkeypatch.setattr(html_module, "escape_html", identity)
        analysis = analyze_paths(write_hostile_trace(tmp_path))
        document = parse(analysis.html)
        with pytest.raises(AssertionError):
            assert sha256_text(document.script_body()) == SCRIPT_SHA256
        # ...while the constant-only form still passes, which is the point.
        from swarm_observer.report.html import REPORT_SCRIPT, sha256_of

        assert sha256_of(REPORT_SCRIPT) == SCRIPT_SHA256

    def test_r50_the_no_event_handler_check_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R50/R51: ``"><img src=x onerror=alert(1)>`` becomes a real handler."""
        monkeypatch.setattr(html_module, "escape_html", identity)
        analysis = analyze_paths(write_hostile_trace(tmp_path))
        document = parse(analysis.html)
        with pytest.raises(AssertionError):
            assert not [name for _, name, _ in document.attributes if name.startswith("on")]

    def test_r50_the_forbidden_element_check_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R50/R35: an ``<img>`` appears, which is a fetch the CSP would have to stop."""
        monkeypatch.setattr(html_module, "escape_html", identity)
        analysis = analyze_paths(write_hostile_trace(tmp_path))
        counts = parse(analysis.html).tag_counts
        with pytest.raises(AssertionError):
            for element in R35_FORBIDDEN_ELEMENTS:
                assert counts.get(element, 0) == 0

    def test_r50_the_attribute_allowlist_check_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R50/R34: values appear that the generated allowlist cannot justify."""
        monkeypatch.setattr(html_module, "escape_html", identity)
        analysis = analyze_paths(write_hostile_trace(tmp_path))
        allowlist = analysis.allowlist()
        offenders = [
            (element, name, value)
            for element, name, value in parse(analysis.html).attributes
            if name not in allowlist or value not in allowlist[name]
        ]
        with pytest.raises(AssertionError):
            assert not offenders

    def test_r50_the_patch_is_undone_between_tests(self, tmp_path: Path) -> None:
        """R50: the mutation is in memory and ``monkeypatch`` reverses it.

        A canary that left a mutated module behind would make every later test
        in the session meaningless — the harness hazard the mutation ledger
        records.
        """
        from swarm_observer.report.escape import escape_html

        assert html_module.escape_html is escape_html
        analysis = analyze_paths(write_hostile_trace(tmp_path))
        assert parse(analysis.html).tag_counts.get("script") == 1
