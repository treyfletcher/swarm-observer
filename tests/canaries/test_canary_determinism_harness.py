"""R50 canary: the determinism harness fails when a clock reaches the output.

The determinism guarantee (see the spec's cross-cutting determinism
requirement) is that a run's bytes are a function of the trace, the flags and
the rate snapshot — never of the current time, the timezone, the hash seed or
the locale. The harness that asserts this is only worth having if it notices a
violation, so this canary hands it a producer that appends ``time.time()`` and
asserts it raises.

The subject in increment 1 is ``schema_document()``: a pure function of the
package, and therefore the one thing whose determinism can be checked before the
renderer exists. The canary re-points at the renderer in increment 4 without
changing what it proves.
"""

from __future__ import annotations

import time

import pytest

from swarm_observer.model.trace import schema_document

from ..harness import assert_deterministic

#: Only removes clock-resolution flake: two calls microseconds apart could
#: return the same float. The defect being caught is the timestamp in the
#: output, not the delay.
_CLOCK_TICK_SECONDS = 0.001


def test_r50_canary_determinism_harness_accepts_a_deterministic_producer() -> None:
    """R50: the control arm — a pure producer passes the whole environment matrix."""
    digest = assert_deterministic(schema_document)
    assert len(digest) == 64


def test_r50_canary_determinism_harness_fails_when_output_embeds_a_clock() -> None:
    """R50: a producer that embeds ``time.time()`` must fail the harness."""

    def with_a_clock() -> str:
        time.sleep(_CLOCK_TICK_SECONDS)
        return schema_document() + repr(time.time())

    with pytest.raises(AssertionError) as raised:
        assert_deterministic(with_a_clock)
    assert "not deterministic" in str(raised.value)


def test_r50_canary_determinism_harness_fails_on_an_environment_dependent_producer() -> None:
    """R50: reading an environment variable into the output must fail the harness.

    The determinism requirement forbids environment values in output for the
    same reason it forbids a clock: two people running the same command must get
    the same bytes. This arm also proves the harness really varies the
    environment rather than calling the producer nine times in identical
    conditions.
    """
    import os

    def with_the_environment() -> str:
        return schema_document() + os.environ.get("PYTHONHASHSEED", "unset")

    with pytest.raises(AssertionError):
        assert_deterministic(with_the_environment)
