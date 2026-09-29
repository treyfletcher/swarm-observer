"""R32 and AC4: the one escaping boundary, its table, and its one interpreter tie.

What would make each check here red is written into its docstring, because the
alternative — a suite of assertions about a function whose failure mode nobody
stated — is how a guard ends up tuned to the implementation that exists.

Three properties carry the requirement and each is asserted in a way that a
plausible wrong implementation fails:

* **The table, entry by entry.** Eight characters, eight replacements. A
  transcription slip is one wrong entry, not a wrong shape, so the entries are
  compared individually against literals typed from R32's table rather than
  against ``ESCAPE_TABLE`` itself — comparing the module's table to the
  module's table would pass for any table.
* **One pass, not sequential ``str.replace``.** AC4 names the discriminating
  observation: ``&lt;`` present and ``&amp;lt;`` absent. A sequential
  implementation replacing ``&`` first produces ``&amp;lt;`` for ``<``; one
  replacing ``&`` last produces ``&lt;`` but re-enters ``&#x27;``. Both are
  covered, in both directions.
* **Non-printable → exactly one space, per character.** ``\\x00``, ``\\x1b`` and
  a newline each become one space. The *per character* half matters and is
  separately asserted: R8 collapses runs at ingest, and the fields that never
  pass through R8 (``Span.model``, ``stop_reason``, ``tool_name``,
  ``tool_use_id``, ``SpanError.detail``, ``AgentRun.description``,
  ``SourceFile.name``) reach this function with their runs intact.

**S24 is pinned here rather than argued.** ``str.isprintable`` reads the Unicode
table compiled into the running interpreter, so R32 is interpreter-dependent by
construction. ``TestInterpreterDependenceS24`` asserts the dependence *exists*,
so nobody can close S24 by editing a docstring, and
``test_r32_a_code_point_this_interpreter_calls_unassigned_becomes_a_space``
records which side of it this run is on.
"""

from __future__ import annotations

import unicodedata

import pytest

from swarm_observer.report.escape import ESCAPE_TABLE, NON_PRINTABLE_REPLACEMENT, escape_html

#: R32's table, typed out from the requirement rather than imported. A test that
#: compared ``ESCAPE_TABLE`` with itself would pass for every table, including
#: an empty one.
R32_TABLE: dict[str, str] = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#x27;",
    "/": "&#x2F;",
    "`": "&#x60;",
    "=": "&#x3D;",
}

#: AC4's exact input string.
AC4_INPUT = '</script><img src=x onerror=alert(1)>&`=" '


class TestTheTableR32:
    """R32: eight entries, transcribed and applied."""

    @pytest.mark.parametrize(("char", "replacement"), sorted(R32_TABLE.items()))
    def test_r32_each_table_entry_is_the_requirements(self, char: str, replacement: str) -> None:
        """R32: entries are checked one by one, because a slip is one wrong row.

        Red when: any single row of ``ESCAPE_TABLE`` differs from R32's table.
        """
        assert ESCAPE_TABLE[char] == replacement

    @pytest.mark.parametrize(("char", "replacement"), sorted(R32_TABLE.items()))
    def test_r32_each_table_entry_is_applied_by_escape_html(
        self, char: str, replacement: str
    ) -> None:
        """R32: the table is not merely present, it is the function's behaviour.

        Red when: ``escape_html`` stops consulting the table for one character —
        the shape a "fast path for ASCII" optimisation takes.
        """
        assert escape_html(f"a{char}b") == f"a{replacement}b"

    def test_r32_the_table_has_exactly_eight_entries(self) -> None:
        """R32: a ninth entry is an escaping rule the requirement does not have.

        Red when: a character is added to or removed from the table. Pinned as a
        literal so the table cannot be widened silently — a wider table changes
        every golden and every ``[redacted:…]`` marker's rendered form.
        """
        assert len(ESCAPE_TABLE) == 8
        assert set(ESCAPE_TABLE) == set(R32_TABLE)

    def test_r32_no_replacement_reintroduces_a_character_the_table_escapes(self) -> None:
        """R32: a replacement containing ``<`` or ``"`` would be self-defeating.

        Red when: an entry is mistyped as, say, ``"<"`` → ``"<lt;"``. The one
        character every replacement *does* contain is ``&``, which is why this
        function must be single-pass.
        """
        for char, replacement in ESCAPE_TABLE.items():
            assert "<" not in replacement and ">" not in replacement, char
            assert '"' not in replacement and "'" not in replacement, char
            assert replacement.startswith("&") and replacement.endswith(";"), char


class TestSinglePassAC4:
    """AC4: applied once, over code points, never by sequential ``str.replace``."""

    def test_ac4_the_named_input_escapes_every_character_exactly_once(self) -> None:
        """AC4: the characters of AC4's own string are each entity-encoded exactly once.

        AC4 says "every one of ``&``, ``<``, ``>``, ``"``, ``'``, ``/``,
        `````` ` ``````, ``=``" of the string it names — but the string it
        names contains no apostrophe (**S31**). Asserting the apostrophe here
        would mean asserting it about a character that is not in the input,
        which passes for any implementation; it is covered by
        ``TestTheTableR32`` and by the next test instead.

        Red when: the implementation becomes sequential ``str.replace`` with
        ``&`` first — ``&lt;`` becomes ``&amp;lt;``.
        """
        escaped = escape_html(AC4_INPUT)
        assert "&lt;" in escaped
        assert "&amp;lt;" not in escaped
        assert escaped.count("&amp;") == 1
        for char in '&<>"/`=':
            assert char in AC4_INPUT, char
            assert R32_TABLE[char] in escaped, char
        assert "'" not in AC4_INPUT, "S31: AC4's named string gained an apostrophe"
        assert escaped == (
            "&lt;&#x2F;script&gt;&lt;img src&#x3D;x onerror&#x3D;alert(1)&gt;"
            "&amp;&#x60;&#x3D;&quot; "
        )

    def test_ac4_an_ampersand_last_implementation_is_also_excluded(self) -> None:
        """AC4: the other sequential ordering re-enters the entities it just wrote.

        ``str.replace`` in table order with ``&`` **last** yields ``&lt;`` for
        ``<`` — which the test above would accept — but turns ``'`` into
        ``&amp;#x27;`` because ``&`` runs after ``'``.

        Red when: the loop is replaced by chained ``str.replace`` in any order.
        """
        assert escape_html("'") == "&#x27;"
        assert escape_html("`") == "&#x60;"
        assert escape_html("&#x27;") == "&amp;#x27;"

    def test_ac4_escape_html_is_deliberately_not_idempotent(self) -> None:
        """AC4: a second application must be visible, not absorbed.

        Red when: someone "fixes" double-escaping by making the function
        idempotent, which would silently hide a renderer applying it twice.
        """
        once = escape_html("<")
        assert once == "&lt;"
        assert escape_html(once) == "&amp;lt;"
        assert escape_html(once) != once

    def test_ac4_the_output_is_a_pure_function_of_the_code_points(self) -> None:
        """AC4/R32: escaping a concatenation equals concatenating the escapes.

        This is the property "one pass over code points" *means*, stated
        without reference to the implementation. Red when: any look-behind or
        look-ahead enters the function — a multi-character escape sequence, a
        run collapser, a context guess.
        """
        pieces = ["<a>", "&", "'", "\x00", "ok", "`=`", "/", "\U0001f600"]
        assert escape_html("".join(pieces)) == "".join(escape_html(piece) for piece in pieces)


class TestNonPrintableR32:
    """R32: every other non-``str.isprintable`` character becomes a single space."""

    @pytest.mark.parametrize(
        ("label", "char"),
        [
            ("NUL", "\x00"),
            ("ESC", "\x1b"),
            ("newline", "\n"),
            ("carriage return", "\r"),
            ("tab", "\t"),
            ("DEL", "\x7f"),
            ("C1 control", "\x85"),
            ("zero width space", "​"),
            ("right-to-left override", "‮"),
            ("BOM / zero width no-break", "﻿"),
            ("line separator", "\u2028"),
            ("no-break space", "\xa0"),
        ],
    )
    def test_r32_each_non_printable_becomes_exactly_one_space(self, label: str, char: str) -> None:
        """R32: one space, and one character of output, for each.

        Red when: a control character is dropped instead of replaced (the string
        gets shorter and two words run together), or replaced by an entity, or
        passed through.
        """
        assert not char.isprintable(), label
        assert escape_html(f"a{char}b") == "a b", label

    def test_r32_the_newline_case_is_what_keeps_the_document_line_structure_fixed(self) -> None:
        """R32: a trace string may not introduce a line break into the document source.

        The renderer joins its lines with ``\\n``, so a surviving newline would
        make a line-oriented check over ``report.html`` read a trace-derived
        line as a renderer-derived one. Red when: newline is added to the
        pass-through set.
        """
        assert "\n" not in escape_html("first\nsecond\r\nthird")
        assert escape_html("first\nsecond") == "first second"

    def test_r32_replacement_is_per_character_not_per_run(self) -> None:
        """R32: three control characters become three spaces, not one.

        R8 collapses runs at *ingest*; R32 does not repeat that instruction, and
        seven rendered fields never pass through R8. Red when: someone adds a
        run collapser here, which would make ``escape_html`` lossy in a way R32
        does not sanction and would change every golden built from a field R8
        does not touch.
        """
        assert escape_html("a\x00\x00\x00b") == "a   b"
        assert len(escape_html("\x00" * 10)) == 10
        assert NON_PRINTABLE_REPLACEMENT == " "

    def test_r32_an_ordinary_space_is_printable_and_survives(self) -> None:
        """R32: ``" ".isprintable()`` is True, so a space is not "replaced by a space".

        Red when: the branch order is changed so the table or the non-printable
        rule captures U+0020 — which would be invisible in the output and would
        make the check above vacuous.
        """
        assert " ".isprintable()
        assert escape_html("a b") == "a b"

    def test_r32_ordinary_text_passes_through_untouched(self) -> None:
        """R32: "all other characters pass through".

        The non-vacuous arm: a function that replaced everything with a space
        would satisfy every "the payload is gone" assertion in this suite.
        """
        assert escape_html("Hello, world 123 — é 日本語 😀") == "Hello, world 123 — é 日本語 😀"
        assert escape_html("") == ""


class TestInterpreterDependenceS24:
    """S24: R32 inherits R8's ``str.isprintable`` interpreter dependence.

    Recorded as tests rather than as a paragraph, because S24 is the one open
    flag with a determinism consequence and the PM's ruling will be checked
    against something. If R32 is amended to a fixed code-point rule, these two
    tests are the ones that must be rewritten — which is the notification.
    """

    def test_r32_escape_html_follows_str_isprintable_exactly(self) -> None:
        """S24/R32: the predicate is ``str.isprintable``, not a hand-rolled range.

        Red when: the implementation substitutes its own table — which is what
        S24 *asks for*, and which must then be a deliberate, visible change
        rather than a silent one.
        """
        for code_point in range(0, 0x3000):
            char = chr(code_point)
            if char in ESCAPE_TABLE:
                continue
            expected = char if char.isprintable() else NON_PRINTABLE_REPLACEMENT
            assert escape_html(char) == expected, hex(code_point)

    def test_r32_a_code_point_this_interpreter_calls_unassigned_becomes_a_space(self) -> None:
        """S24: the same trace renders differently on CPython 3.11 and 3.12.

        U+13460 and U+16D40 are assigned in Unicode 16.0 and unassigned in both
        14.0 (3.11) and 15.0 (3.12); U+1F6DC is assigned in 15.0 only, so it is
        a space on 3.11 and itself on 3.12. At least one probe must be ``Cn``
        here or the guard has nothing to observe — asserted, so a future
        interpreter that assigns all five makes this test red rather than
        vacuous.

        Red when: the probes all become assigned (rewrite them from a newer
        table), or ``escape_html`` stops following ``str.isprintable``.
        """
        probes = ("\U0001f6dc", "\U00013460", "\U00016d40")
        unassigned = [char for char in probes if unicodedata.category(char) == "Cn"]
        assert unassigned, (
            "every S24 probe is assigned on this interpreter "
            f"(Unicode {unicodedata.unidata_version}); pick probes from a newer table"
        )
        for char in unassigned:
            assert escape_html(char) == " ", f"U+{ord(char):05X}"
        assigned = [char for char in probes if unicodedata.category(char) != "Cn"]
        for char in assigned:
            assert escape_html(char) == char, f"U+{ord(char):05X}"


class TestEscapeHtmlHasOneDefinitionR32:
    """R32: one definition, and it is the one the renderers import."""

    def test_r32_the_html_renderer_uses_this_definition(self) -> None:
        """R32: ``report/html.py`` imports ``escape_html``; it does not re-implement it.

        R44's AST test already refuses a second definition. This is the other
        half: the module under test is the module the document is built with, so
        monkeypatching it is what the R50 identity-escape canary relies on.
        """
        from swarm_observer.report import html as html_module

        assert html_module.escape_html is escape_html
