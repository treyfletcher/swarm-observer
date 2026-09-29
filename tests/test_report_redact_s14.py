"""R33's one deviation from the requirement text: the S14 PEM alternation.

``tests/test_redaction.py`` covers R33's table, its order and its idempotence
property. This module covers only the part increment 4 changed and the part the
coder asked to have attacked: ``private_key`` gained an **unterminated**
alternative that runs to the end of the string, because R8 caps a preview at 240
code points and a long key loses the ``-----END … KEY-----`` the paired pattern
needs.

The deviation buys a real guarantee and costs a real thing, and both are pinned:

* **Bought**: a truncated PEM block redacts. Before the fix, ``report.json`` for
  ``hostile.jsonl`` carried ``-----BEGIN RSA PRIVATE KEY----- MIIBOgIBAAJBAK5f…``
  for the whole of increment 3.
* **Cost**: a string that merely *mentions* a PEM header loses everything after
  it. That is over-redaction, it is irreversible, and the only honest thing to do
  with it is assert it — a test that quietly tolerated it would leave the next
  reader to discover it from a report.

The left branch is R33's text **verbatim**, and it is pinned as a literal here
rather than compared to the module's own constant, so a widening of the paired
pattern is visible as a diff in a test rather than only in a docstring.

``TestAssignmentMarkerResiduals`` records two behaviours of R33's
``secret_assignment`` replacement that R33 does not decide and that the
increment-3 BUG-3 fix does not fully cover (**BUG-12**, **BUG-13**). They are
characterizations, not approvals: each says what happens today and what a reader
should conclude.
"""

from __future__ import annotations

import pytest

from swarm_observer.report.redact import (
    NAME_PRESERVING_LABEL,
    PRIVATE_KEY_PAIRED,
    PRIVATE_KEY_UNTERMINATED,
    REDACTION_LABELS,
    marker,
    redact,
)

#: R33's ``private_key`` pattern, typed from the requirement.
R33_PRIVATE_KEY = r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----"

BEGIN = "-----BEGIN RSA PRIVATE KEY-----"
END = "-----END RSA PRIVATE KEY-----"
BODY = "MIIBOgIBAAJBAK5f0000000000000000000000000000000000000000000000000"
PRIVATE = marker("private_key")
ASSIGNED = marker(NAME_PRESERVING_LABEL)


class TestTheAlternationIsTheRequirementPlusOneBranchS14:
    """S14: the left branch is R33 verbatim; the right branch is the declared addition."""

    def test_r33_the_paired_branch_is_the_requirements_pattern_verbatim(self) -> None:
        """R33: the pattern text is pinned by the requirement, so it is pinned here.

        Red when: the paired branch is edited — widened to match a different
        header, made greedy, or its ``[\\s\\S]`` changed to ``.`` (which would
        stop matching across the newlines a real PEM block contains).
        """
        assert PRIVATE_KEY_PAIRED == R33_PRIVATE_KEY

    def test_s14_the_unterminated_branch_is_the_paired_one_without_its_terminator(self) -> None:
        """S14: the addition is exactly "the same header, no terminator".

        Red when: the unterminated branch drifts from the paired one — a
        different header alphabet on one side would make a block that redacts
        as a pair fail to redact when truncated, which is the hole S14 closed.
        """
        assert PRIVATE_KEY_UNTERMINATED == r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*"
        assert PRIVATE_KEY_UNTERMINATED.startswith(
            PRIVATE_KEY_PAIRED.split(r"[\s\S]*?", maxsplit=1)[0]
        )

    def test_r33_private_key_still_holds_exactly_one_row_in_the_table(self) -> None:
        """R33: the alternation is one row, not two — the table's shape is pinned.

        The review's literal suggestion was a second ``private_key`` row. Red
        when: someone takes it, making ``REDACTION_LABELS`` carry a duplicate
        and ``dict(REDACTION_PATTERNS)`` lossy.
        """
        assert REDACTION_LABELS.count("private_key") == 1
        assert len(REDACTION_LABELS) == len(set(REDACTION_LABELS))
        assert REDACTION_LABELS[0] == "private_key", "R33 applies private_key first"


class TestWhatTheBranchBuysS14:
    """S14: the case R33's own pattern cannot reach."""

    def test_s14_a_truncated_block_redacts(self) -> None:
        """S14: a header with no terminator is replaced.

        Red when: the unterminated branch is removed — which is exactly the
        increment-3 state, where this string reached ``report.json`` intact.
        """
        assert redact(f"{BEGIN}\n{BODY}") == PRIVATE
        assert BODY not in redact(f"{BEGIN}\n{BODY}")

    def test_s14_a_complete_block_still_redacts_as_a_single_unit(self) -> None:
        """S14: left branch first, so a paired block is one marker and not two.

        Red when: the alternation's branches are swapped. The unterminated
        branch would then match first at the header and swallow the terminator
        *and the rest of the string* — the same visible output for this input,
        but ``"x {block} y"`` would lose its ``y``. The next test is the one
        that can tell.
        """
        assert redact(f"x {BEGIN}\n{BODY}\n{END} y") == f"x {PRIVATE} y"

    def test_s14_branch_order_is_observable_text_after_a_complete_block_survives(self) -> None:
        """S14: the discriminating case for the alternation's order.

        Red when: ``PRIVATE_KEY_UNTERMINATED`` is placed first in the
        alternation. This is the only input in this module that distinguishes
        the two orders, which is why it is its own test.
        """
        redacted = redact(f"{BEGIN}\n{BODY}\n{END} trailing evidence")
        assert redacted == f"{PRIVATE} trailing evidence"
        assert "trailing evidence" in redacted

    def test_s14_two_complete_blocks_are_two_markers(self) -> None:
        """R33: the paired pattern is lazy, so two blocks do not merge into one.

        Red when: the paired branch is made greedy — the text between the two
        blocks would be swallowed and one credential's evidence lost.
        """
        text = f"{BEGIN}\naaa\n{END} mid {BEGIN}\nbbb\n{END}"
        assert redact(text) == f"{PRIVATE} mid {PRIVATE}"

    def test_s14_a_complete_block_followed_by_a_truncated_one_is_two_markers(self) -> None:
        """S14: the mixed case, which needs both branches to be right.

        Red when: either branch is removed, or their order is swapped.
        """
        text = f"{BEGIN}\naaa\n{END} tail {BEGIN}\nbbb"
        assert redact(text) == f"{PRIVATE} tail {PRIVATE}"


class TestWhatTheBranchCostsS14:
    """S14: over-redaction, asserted rather than tolerated in silence."""

    def test_s14_prose_that_merely_mentions_a_pem_header_loses_the_rest_of_the_string(
        self,
    ) -> None:
        """S14, the declared cost: the unterminated branch runs to end of string.

        This is not a bug report — it is the coder's stated, reviewed trade —
        but it is a *behaviour a reader of a report will meet*, so it is pinned.
        Red when: the branch is bounded (to a line, to N characters), which
        would be an improvement and must then be a deliberate change with a new
        expectation here rather than a silent one.
        """
        prose = f"See the file starting with {BEGIN} for details. Nothing secret here."
        assert redact(prose) == f"See the file starting with {PRIVATE}"
        assert "Nothing secret here" not in redact(prose)

    def test_s14_a_truncated_block_before_a_complete_one_swallows_the_complete_one(self) -> None:
        """S14: the lazy paired branch reaches the *later* terminator first.

        ``BEGIN a … BEGIN b … END b`` is one match, not two, because the paired
        branch is tried first at the first header and succeeds by running
        through the second block. Two credentials become one marker.

        Red when: the paired branch stops crossing a second header — which
        would be the more informative behaviour and is not what R33's pattern
        does.
        """
        second = "-----BEGIN EC PRIVATE KEY-----\nbbb\n-----END EC PRIVATE KEY-----"
        text = f"{BEGIN}\naaa mid {second} Z"
        assert redact(text) == f"{PRIVATE} Z"

    def test_s14_the_over_redaction_is_bounded_to_strings_that_contain_the_header(self) -> None:
        """S14: the non-vacuous arm — redaction is not "delete everything".

        A pattern that over-redacted every string would satisfy every "the
        credential is gone" assertion in this suite. Red when: the header
        alternative becomes optional or the branch is anchored to the start of
        the string.
        """
        assert redact("no credentials here at all") == "no credentials here at all"
        assert redact(f"{END} trailing text") == f"{END} trailing text"
        assert redact("-----BEGIN PRIVATE-----aaa") == "-----BEGIN PRIVATE-----aaa"
        lower = "-----begin rsa private key----- aaa"
        assert redact(lower) == lower

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("truncated", f"{BEGIN}\n{BODY}"),
            ("paired", f"x {BEGIN}\n{BODY}\n{END} y"),
            ("pair then truncated", f"{BEGIN}\naaa\n{END} mid {BEGIN}\nbbb"),
            ("prose mention", f"prose {BEGIN} more prose"),
            ("inside an assignment", f"PRIVATE_KEY={BEGIN}\n{BODY}"),
            ("inside a quoted assignment", f'PRIVATE_KEY="{BEGIN}\n{BODY}"'),
            ("header only", f"{END} tail"),
            ("two truncated", f"{BEGIN}\naaa {BEGIN}\nbbb"),
        ],
    )
    def test_r33_redaction_stays_idempotent_over_every_pem_shape(
        self, label: str, text: str
    ) -> None:
        """R33: ``redact(redact(s)) == redact(s)``, including for the S14 branch.

        Red when: a replacement produces text a later pattern matches — the
        shape of increment-3's BUG-3, where a second pass deleted report
        characters.
        """
        once = redact(text)
        assert redact(once) == once, label
        assert redact(once) == redact(redact(once)), label


class TestAssignmentMarkerResiduals:
    """R33: two things the ``secret_assignment`` replacement does that R33 does not say.

    Both are reported as **BUG-12** and **BUG-13**. Neither violates a pinned
    clause — R33 pins the pattern text and idempotence, and both hold — so both
    are characterizations here. They are tests rather than prose because the
    increment-3 fix's docstring claims a stronger property than the code has,
    and a claim nobody asserted is how this project's defects survive a review.
    """

    def test_r33_a_truncated_pem_inside_a_private_key_assignment_is_marked_once(self) -> None:
        """S14: ``private_key`` runs first, then ``secret_assignment`` relabels it.

        The credential is gone either way. What is lost is *which* pattern found
        it: the document says ``secret_assignment`` where a reader chasing a
        leaked key would want ``private_key``.

        Red when: the ordering changes, or ``_replace_assignment`` learns to
        leave an existing marker of another label alone.
        """
        assert redact(f"PRIVATE_KEY={BEGIN}\n{BODY}") == f"PRIVATE_KEY={ASSIGNED}"
        assert BODY not in redact(f"PRIVATE_KEY={BEGIN}\n{BODY}")

    def test_r33_an_earlier_patterns_marker_is_relabelled_by_secret_assignment(self) -> None:
        """BUG-12: ``TOKEN=<aws key>`` reports ``secret_assignment``, not ``aws_key_id``.

        ``aws_key_id`` matches first and writes its marker; ``secret_assignment``
        then matches the marker as its ``\\S{6,}`` value and overwrites it. The
        report tells the reader a secret-shaped *assignment* was found and not
        that an **AWS key** was found, which is the more actionable fact and the
        one R33's label set exists to carry.

        Red when: the replacement learns to keep a marker it did not write.
        """
        assert redact("TOKEN=AKIAIOSFODNN7EXAMPLE") == f"TOKEN={ASSIGNED}"
        assert redact("TOKEN=" + marker("aws_key_id")) == f"TOKEN={ASSIGNED}"
        # The non-vacuous arm: away from an assignment, the label is right.
        assert redact("TOKEN was AKIAIOSFODNN7EXAMPLE") == f"TOKEN was {marker('aws_key_id')}"

    def test_r33_two_credentials_in_one_assignment_value_collapse_to_one_marker(self) -> None:
        """BUG-12: the count of credentials found is not preserved either.

        Red when: the greedy ``\\S{6,}`` value alternative is bounded.
        """
        text = "SECRET=" + marker("aws_key_id") + marker("openai_key")
        assert redact(text) == f"SECRET={ASSIGNED}"

    def test_r33_the_bug3_guard_only_holds_when_the_value_begins_with_the_marker(self) -> None:
        """BUG-13: ``NAME=x[redacted:secret_assignment]`` still deletes the ``x``.

        ``_replace_assignment``'s docstring says the guard stops the greedy
        value alternative "silently deleting a character of report text". It
        does — on the *second* pass, and only when the marker is the first
        thing after the separator. A trace carrying the literal marker text
        behind any non-space prefix loses that prefix on the **first** pass.

        This is a courtesy, not a boundary, and a crafted trace is the only way
        in, so it is low severity. It is pinned because the docstring states
        the property unconditionally.

        Red when: the guard is widened to "the value *contains* this marker",
        which would close it.
        """
        assert redact(f"SECRET=x{ASSIGNED}") == f"SECRET={ASSIGNED}"
        # And the arm the guard does cover, so this is not a claim that it is inert.
        assert redact(f"SECRET={ASSIGNED}") == f"SECRET={ASSIGNED}"
        assert redact(redact(f"SECRET=x{ASSIGNED}")) == f"SECRET={ASSIGNED}"
