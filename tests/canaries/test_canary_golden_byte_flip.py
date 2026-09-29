"""R50 canary: the golden-file comparison fails when a byte changes.

A golden test that is only ever handed matching inputs has never demonstrated
that it can fail. This canary flips one character — on each side in turn — and
asserts the comparison raises. If someone "simplifies"
:func:`~tests.harness.assert_matches_golden` into a no-op, this is the test that
goes red.

The subject is the ``schema`` command's golden document, which is the only
golden artifact increment 1 produces.
"""

from __future__ import annotations

import pytest

from ..harness import assert_matches_golden, flip_one_byte, read_golden, sha256_text

GOLDEN = "schema.json"


def test_r50_canary_golden_comparison_accepts_the_unmodified_golden() -> None:
    """R50: the canary's control arm — an honest comparison passes."""
    golden = read_golden(GOLDEN)
    assert golden, "the golden file is empty; the canary would pass vacuously"
    assert_matches_golden(GOLDEN, golden)


@pytest.mark.parametrize("position", [0, 17, 500, -1])
def test_r50_canary_golden_comparison_fails_on_a_flipped_actual_byte(position: int) -> None:
    """R50: one changed byte in the produced output fails the comparison."""
    golden = read_golden(GOLDEN)
    tampered = flip_one_byte(golden, position)
    assert tampered != golden
    with pytest.raises(AssertionError) as raised:
        assert_matches_golden(GOLDEN, tampered)
    assert GOLDEN in str(raised.value)
    assert sha256_text(tampered) in str(raised.value)


def test_r50_canary_golden_comparison_fails_on_a_flipped_expected_byte() -> None:
    """R50: tampering with the *expected* side is caught too.

    The asymmetric case matters: a comparison that normalizes both sides before
    comparing would pass this and still be worthless.
    """
    golden = read_golden(GOLDEN)
    with pytest.raises(AssertionError):
        assert_matches_golden(GOLDEN, golden, expected=flip_one_byte(golden, 42))


def test_r50_canary_golden_comparison_fails_on_truncation() -> None:
    """R50: a shorter output is a mismatch, not a prefix match."""
    golden = read_golden(GOLDEN)
    with pytest.raises(AssertionError) as raised:
        assert_matches_golden(GOLDEN, golden[:-1])
    assert "lengths differ" in str(raised.value)
