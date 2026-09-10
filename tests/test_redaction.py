"""``redact``: the ordered pattern table, and the idempotence property — R33.

R33 pins three things and this module tests all three separately, because each
can hold while the others fail:

* **the table** — ten labels, transcribed verbatim, in the requirement's order;
* **the order** — a replacement rewrites the string the next pattern sees, so
  ``sk-ant-…`` redacting as ``anthropic_key`` rather than being chopped by the
  narrower ``openai_key`` is a property of the *sequence*, not of either pattern;
* **idempotence** — ``redact(redact(s)) == redact(s)`` for all ``s``, which R33
  states as a property test over a corpus and which is where this module found a
  defect.

The property is driven three ways: a hand-written adversarial corpus, every
free-text string the fixture corpus produces, and a generator over an alphabet
built from the patterns' own literals. The generator's input space is stated
next to its trial count, per the increment-2 review's ruling that a trial count
without an input space is a confident number with an unstated precondition.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache

import pytest

from swarm_observer.report.redact import (
    NAME_PRESERVING_LABEL,
    REDACTION_LABELS,
    REDACTION_PATTERNS,
    marker,
    redact,
)

from .detector_corpus import fixture_paths, load_trace

#: Hand-written cases: one clean example per label, plus the shapes that sit
#: between two patterns and the shapes that must *not* match.
ADVERSARIAL_CORPUS: tuple[str, ...] = (
    "",
    " ",
    "nothing to see here",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----abc-----END PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY----- no terminator here",
    "-----BEGIN A-----x-----END A-----",
    "AKIAIOSFODNN7EXAMPLE",
    "ASIAIOSFODNN7EXAMPLE ANVAIOSFODNN7EXAMPLE",
    "AKIAIOSFODNN7EXAMPL",  # one character short
    "AKIAIOSFODNN7EXAMPLEX",  # one character long
    "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "sk-AAAAAAAAAAAAAAAAAAAAAAAA",
    "sk-ant-tooshort",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "ghz_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",  # not one of the five letters
    "xoxb-123456789012-abcdefgh",
    "AIza" + "B" * 35,
    "AIza" + "B" * 34,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36",
    "Authorization: Bearer AAAAAAAAAAAAAAAAAAAAAAAA",
    "bearer AAAAAAAAAAAAAAAAAAAAAAAA",
    "BEARER AAAAAAAAAAAAAAAAAAAAAAAA",
    "bearer short",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
    'GITHUB_TOKEN: "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"',
    "api_key = 'abcdefghijkl'",
    "MY_PASSWORD:hunter2xyz",
    "PASSWD=short",  # value under six characters
    "TOKEN=abcdefg",
    'TOKEN="abcdefg"X',
    "API_KEY='aaaaaa'Z",
    "PASSWORD='secret'AKIAIOSFODNN7EXAMPLE",
    "ACCESS-KEY: abcdefghij",
    "PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    "token=" + "a" * 400,
    "a" * 500,
    "\n".join(["SECRET=abcdefgh"] * 5),
    "TOKEN=[redacted:secret_assignment]",
    "[redacted:aws_key_id]",
    "sk-ant-" + "A" * 20 + " sk-" + "B" * 20,
    "TOKEN:::::::",
    "…truncated AKIAIOSFODNN7EXAMPLE…",
)

#: The alphabet the generator samples from: every literal prefix and keyword the
#: R33 patterns key on, the punctuation they use as separators, ordinary
#: characters, and the redaction markers themselves — so the generated space
#: *can* contain the inputs that distinguish a non-idempotent implementation.
GENERATOR_ALPHABET: tuple[str, ...] = (
    "a",
    "b",
    "X",
    "9",
    "_",
    "-",
    "=",
    ":",
    ".",
    " ",
    "\n",
    '"',
    "'",
    "/",
    "sk-ant-",
    "sk-",
    "AKIA",
    "ASIA",
    "ghp_",
    "xoxb-",
    "AIza",
    "eyJ",
    "Bearer ",
    "authorization: bearer ",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "API_KEY",
    "ACCESS-KEY",
    "PRIVATE_KEY",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----END PRIVATE KEY-----",
    "[redacted:jwt]",
    "[redacted:secret_assignment]",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "0123456789012345678901234",
)


#: The generator's trial count and seed, fixed so a failure is reproducible.
GENERATED_TRIALS = 40_000
GENERATOR_SEED = 20260910

#: The grammar's parts. A free sample over :data:`GENERATOR_ALPHABET` alone
#: reaches the assignment-with-a-quoted-value-and-a-trailing-character shape
#: roughly once in 150,000 draws, which is how a property test ends up reporting
#: green about a property that is false. The generator below therefore *builds*
#: that shape's family rather than hoping to stumble into it: a prefix, a name,
#: a separator, a value drawn from all three of R33's alternatives, and a
#: suffix that is sometimes attached without a space. Every distinguishing input
#: for a value-alternative defect is inside this space by construction.
_NAMES = ("TOKEN", "API_KEY", "MY_PASSWORD", "aws_secret_access_key", "x", "PRIVATE-KEY")
_SEPARATORS = ("=", ": ", " = ", ":", " :\t")
_VALUES = (
    '"abcdefg"',
    "'aaaaaa'",
    "abcdefg",
    '"sk-ant-' + "A" * 24 + '"',
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "C" * 24,
    "'-----BEGIN RSA PRIVATE KEY-----abc-----END RSA PRIVATE KEY-----'",
    "[redacted:secret_assignment]",
    "short",
)
_AFFIXES = ("", "X", " X", "\n", ".", "]", " AKIAIOSFODNN7EXAMPLE", "Bearer " + "E" * 24)


@lru_cache(maxsize=2)
def generated_corpus(count: int, *, seed: int) -> tuple[str, ...]:
    """``count`` strings from the assignment grammar, with free noise around it."""
    rng = random.Random(seed)
    built: list[str] = []
    for _ in range(count):
        noise = "".join(rng.choice(GENERATOR_ALPHABET) for _ in range(rng.randint(0, 3)))
        built.append(
            rng.choice(_AFFIXES)
            + noise
            + rng.choice(_NAMES)
            + rng.choice(_SEPARATORS)
            + rng.choice(_VALUES)
            + rng.choice(_AFFIXES)
        )
    return tuple(built)


@lru_cache(maxsize=1)
def generated_non_idempotent() -> tuple[str, ...]:
    """Every generated string for which ``redact(redact(s)) != redact(s)``."""
    return tuple(
        text
        for text in generated_corpus(GENERATED_TRIALS, seed=GENERATOR_SEED)
        if redact(redact(text)) != redact(text)
    )


def corpus_free_text() -> list[str]:
    """Every trace-derived free-text string the checked-in fixtures produce."""
    texts: list[str] = []
    for path in fixture_paths():
        trace = load_trace(path)
        for span in trace.spans:
            texts.extend([span.text_preview, span.tool_input_preview, span.tool_result_preview])
            if span.model is not None:
                texts.append(span.model)
            if span.tool_name is not None:
                texts.append(span.tool_name)
            if span.error is not None:
                texts.append(span.error.detail)
        for agent in trace.agents:
            texts.append(agent.description)
    return [text for text in texts if text]


class TestPatternTableR33:
    """R33: ten labels, in the requirement's order, transcribed verbatim."""

    def test_r33_the_labels_are_the_ten_r33_names_in_order(self) -> None:
        """R33: the table's order is part of its output, so it is pinned."""
        assert REDACTION_LABELS == (
            "private_key",
            "aws_key_id",
            "anthropic_key",
            "openai_key",
            "github_token",
            "slack_token",
            "google_api_key",
            "jwt",
            "bearer",
            "secret_assignment",
        )

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            (
                "private_key",
                r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----",
            ),
            ("aws_key_id", r"\b(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{16}\b"),
            ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
            ("openai_key", r"\bsk-[A-Za-z0-9]{20,}\b"),
            ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
            ("slack_token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
            ("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
            (
                "jwt",
                r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b",
            ),
        ],
    )
    def test_r33_each_simple_pattern_is_the_requirements_text(
        self, label: str, expected: str
    ) -> None:
        """R33: the pattern source, not merely its behaviour on one example."""
        table = dict(REDACTION_PATTERNS)
        assert table[label].pattern == expected

    def test_r33_the_two_case_insensitive_patterns_carry_their_flag(self) -> None:
        """R33: ``bearer`` and ``secret_assignment`` are ``(?i)`` in the table."""
        table = dict(REDACTION_PATTERNS)
        assert table["bearer"].flags & re.IGNORECASE
        assert table["secret_assignment"].flags & re.IGNORECASE

    def test_r33_the_marker_is_the_pinned_replacement_shape(self) -> None:
        """R33: ``[redacted:<label>]`` and nothing else."""
        assert marker("aws_key_id") == "[redacted:aws_key_id]"
        assert NAME_PRESERVING_LABEL == "secret_assignment"

    @pytest.mark.parametrize("label", REDACTION_LABELS)
    def test_r33_no_pattern_matches_any_replacement_marker(self, label: str) -> None:
        """R33: the markers are inert for every pattern except the name-preserving one.

        This is the mechanism idempotence is supposed to rest on, so it is
        asserted rather than argued — and the one exception is named.
        """
        table = dict(REDACTION_PATTERNS)
        text = " ".join(marker(name) for name in REDACTION_LABELS)
        if label == NAME_PRESERVING_LABEL:
            return
        assert table[label].search(text) is None


class TestEachPatternFiresR33:
    """R33: every label is reachable — a pattern nothing can match is dead code."""

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            (
                "private_key",
                "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
            ),
            ("aws_key_id", "AKIAIOSFODNN7EXAMPLE"),
            ("anthropic_key", "sk-ant-api03-" + "A" * 24),
            ("openai_key", "sk-" + "B" * 24),
            ("github_token", "ghp_" + "C" * 24),
            ("slack_token", "xoxb-123456789012-abcdefgh"),
            ("google_api_key", "AIza" + "D" * 35),
            (
                "jwt",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36",
            ),
            ("bearer", "Authorization: Bearer " + "E" * 24),
        ],
    )
    def test_r33_the_label_replaces_the_whole_match(self, label: str, text: str) -> None:
        """R33: a clean example per label, replaced whole."""
        assert redact(text) == marker(label)

    def test_r33_secret_assignment_preserves_the_name_and_separator(self) -> None:
        """R33: "the name is preserved, the value replaced"."""
        assert redact("AWS_SECRET_ACCESS_KEY=abcdefghij") == (
            f"AWS_SECRET_ACCESS_KEY={marker('secret_assignment')}"
        )
        assert redact("MY_TOKEN : 'hunter2xyz'") == (f"MY_TOKEN :{marker('secret_assignment')}")
        assert redact('GITHUB_PASSWORD: "abcdefghij"') == (
            f"GITHUB_PASSWORD:{marker('secret_assignment')}"
        )

    @pytest.mark.parametrize(
        "keyword",
        ["SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "ACCESS-KEY", "PRIVATEKEY"],
    )
    def test_r33_every_secret_assignment_keyword_fires(self, keyword: str) -> None:
        """R33: the alternation's eight branches, each reachable."""
        assert marker("secret_assignment") in redact(f"{keyword}=abcdefghij")

    @pytest.mark.parametrize(
        "text",
        [
            "AKIAIOSFODNN7EXAMPL",
            "AKIAIOSFODNN7EXAMPLEX",
            "sk-ant-short",
            "sk-" + "B" * 19,
            "ghz_" + "C" * 24,
            "AIza" + "D" * 34,
            "bearer short",
            "PASSWD=short",
            "NAME=abcdefghij",
            "eyJshort.abc.def",
            "just some ordinary tool output",
        ],
    )
    def test_r33_a_near_miss_is_left_alone(self, text: str) -> None:
        """R33: the negative arm — the patterns are not simply always-match."""
        assert redact(text) == text


class TestOrderingR33:
    """R33: the table is applied in order, and the order changes the output."""

    def test_r33_anthropic_beats_openai_on_a_shared_prefix(self) -> None:
        """R33: ``sk-ant-…`` would otherwise be chopped by the narrower pattern.

        ``openai_key`` is ``\\bsk-[A-Za-z0-9]{20,}\\b`` — it cannot span the
        hyphens in ``sk-ant-api03-…``, so running it first would leave the label
        wrong and part of the key behind. The premise is asserted, not assumed.
        """
        key = "sk-ant-api03-" + "A" * 30
        assert redact(key) == marker("anthropic_key")
        openai = dict(REDACTION_PATTERNS)["openai_key"]
        assert openai.search(key) is None

    def test_r33_a_jwt_inside_a_bearer_header_is_marked_once(self) -> None:
        """R33: ``jwt`` runs before ``bearer``; the result is one marker, not two."""
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36"
        redacted = redact(f"Authorization: Bearer {token}")
        assert redacted == f"Authorization: Bearer {marker('jwt')}"
        assert redacted.count("[redacted:") == 1

    def test_r33_a_private_key_block_is_one_unit_not_many(self) -> None:
        """R33: the block pattern runs first, so its contents are not re-scanned."""
        block = (
            "-----BEGIN RSA PRIVATE KEY-----\nAKIAIOSFODNN7EXAMPLE\n-----END RSA PRIVATE KEY-----"
        )
        assert redact(block) == marker("private_key")

    def test_r33_a_credential_inside_an_assignment_is_labelled_by_its_own_pattern(self) -> None:
        """R33: the earlier, more specific pattern wins the value."""
        redacted = redact("GITHUB_TOKEN=ghp_" + "C" * 24)
        assert redacted == f"GITHUB_TOKEN={marker('secret_assignment')}"

    def test_r33_the_lazy_private_key_body_stops_at_the_first_terminator(self) -> None:
        """R33: ``[\\s\\S]*?`` is lazy, so a second block is not swallowed."""
        text = (
            "-----BEGIN A PRIVATE KEY-----x-----END A PRIVATE KEY----- middle "
            "-----BEGIN B PRIVATE KEY-----y-----END B PRIVATE KEY-----"
        )
        assert redact(text) == f"{marker('private_key')} middle {marker('private_key')}"


class TestIdempotenceR33:
    """R33: ``redact(redact(s)) == redact(s)`` for all ``s`` — a property, not a habit."""

    @pytest.mark.parametrize("text", ADVERSARIAL_CORPUS, ids=lambda t: repr(t[:32]))
    def test_r33_the_hand_written_corpus_is_idempotent_where_it_can_be(self, text: str) -> None:
        """R33: every adversarial case except the three BUG-3 shapes.

        The exclusions are named individually rather than filtered by a
        predicate, so a fourth non-idempotent shape appearing later is a failure
        and not an automatic exemption.
        """
        bug3 = {
            'TOKEN="abcdefg"X',
            "API_KEY='aaaaaa'Z",
            "PASSWORD='secret'AKIAIOSFODNN7EXAMPLE",
        }
        once = redact(text)
        if text in bug3:
            assert redact(once) != once, f"BUG-3 appears fixed for {text!r}"
            return
        assert redact(once) == once

    @pytest.mark.parametrize("text", ADVERSARIAL_CORPUS, ids=lambda t: repr(t[:32]))
    def test_r33_no_simple_pattern_still_matches_its_own_output(self, text: str) -> None:
        """R33: the nine non-name-preserving patterns reach a fixpoint in one pass.

        Weaker than idempotence and true even where idempotence is not, which is
        what isolates BUG-3 to the one entry that re-matches by design.
        """
        once = redact(text)
        for label, pattern in REDACTION_PATTERNS:
            if label == NAME_PRESERVING_LABEL:
                continue
            assert pattern.search(once) is None, (label, once)

    def test_r33_redact_is_a_pure_function_of_its_argument(self) -> None:
        """R33: no global state, no compiled-pattern reuse hazard, no ordering drift."""
        for text in ADVERSARIAL_CORPUS:
            assert redact(text) == redact(text)
        interleaved = [redact(text) for text in ADVERSARIAL_CORPUS]
        assert interleaved == [
            redact(text) for text in reversed(list(reversed(ADVERSARIAL_CORPUS)))
        ]

    def test_r33_every_fixture_free_text_string_is_idempotent(self) -> None:
        """R33: the property over the checked-in corpus, as the requirement says."""
        texts = corpus_free_text()
        assert len(texts) >= 50, "the corpus produced too little text to prove anything"
        for text in texts:
            once = redact(text)
            assert redact(once) == once, text

    def test_r33_the_fixture_corpus_actually_exercises_the_redactor(self) -> None:
        """R33: the control arm — the corpus above is not all inert strings."""
        assert any("[redacted:" in redact(text) for text in corpus_free_text())

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-3: secret_assignment's `\\S{6,}` value alternative re-matches its own "
            "output *plus whatever follows it without a space*, so a second pass deletes "
            "the trailing characters. R33 pins redact(redact(s)) == redact(s) for all s."
        ),
    )
    def test_r33_a_generated_corpus_is_idempotent(self) -> None:
        """R33: 40,000 strings from the assignment grammar.

        Input space, stated beside the trial count as the increment-2 review
        requires: ``affix + noise + name + separator + value + affix``, where the
        names cover both cases of every ``secret_assignment`` keyword, the
        separators cover ``=`` and ``:`` with and without surrounding
        whitespace, the values cover all three of R33's value alternatives
        (double-quoted, single-quoted, bare ``\\S{6,}``) plus a credential of
        another label and a pre-existing marker, and the affixes include the
        empty string, a bare trailing character, and whitespace. The
        distinguishing input for any value-alternative defect is inside this
        space by construction rather than by luck.
        """
        assert generated_non_idempotent() == (), list(generated_non_idempotent()[:5])

    def test_r33_bug3_reproduces_on_a_minimal_string(self) -> None:
        """R33: BUG-3, minimized, pinned beside its xfail.

        The first pass matches the quoted alternative and yields
        ``TOKEN=[redacted:secret_assignment]X``. On the second pass the value
        alternative ``\\S{6,}`` is greedy and unanchored, so it swallows the
        marker *and* the ``X`` — silently deleting a character of report text.
        """
        once = redact('TOKEN="abcdefg"X')
        assert once == f"TOKEN={marker('secret_assignment')}X"
        twice = redact(once)
        assert twice == f"TOKEN={marker('secret_assignment')}"
        assert twice != once, (
            "BUG-3 appears to be fixed: delete this test and de-xfail the one above"
        )

    def test_r33_bug3_can_delete_an_earlier_marker_too(self) -> None:
        """R33: the second pass can erase evidence, not only trailing text."""
        once = redact("PASSWORD='secret'AKIAIOSFODNN7EXAMPLE")
        assert once == f"PASSWORD={marker('secret_assignment')}{marker('aws_key_id')}"
        assert redact(once) == f"PASSWORD={marker('secret_assignment')}"

    def test_r33_the_generator_can_produce_a_distinguishing_input(self) -> None:
        """R33: the generator's precondition — its space contains the counterexample.

        A property test over a generator that cannot emit the failing shape is a
        check that cannot fail, so the space is shown to reach it.
        """
        assert generated_non_idempotent(), "the generator never produced a non-idempotent string"
