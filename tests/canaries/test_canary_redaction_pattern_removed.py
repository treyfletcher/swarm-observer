"""R50 canary: the redaction checks fail when a pattern is removed from the table.

R50 names this canary explicitly — "the redaction idempotence test fails when a
pattern is removed from the table" — and its subject arrived with increment 3,
because R30 needs the unpriced table's recorded model id redacted.

The canary is deliberately wider than R50's sentence. Removing a pattern does
**not** break idempotence: the remaining nine still reach a fixpoint, and the
string the removed pattern would have matched is simply carried through
unchanged. A canary that only asserted "idempotence goes red" would therefore
itself be a check that cannot fail — the exact defect R50 exists to prevent. So
what is asserted here is that the checks which *are* sensitive to the table go
red: the per-label coverage assertion, and the end-to-end "no credential shape
reaches the report" assertion.

The table is rebuilt in memory rather than by editing ``report/redact.py``, so
this canary leaves no mutated source behind if the process dies mid-run — the
harness discipline the increment-2 review recorded.
"""

from __future__ import annotations

import pytest

from swarm_observer.report.redact import (
    NAME_PRESERVING_LABEL,
    REDACTION_PATTERNS,
    marker,
)

#: A string per label that only that label's pattern matches.
PAYLOADS: dict[str, str] = {
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
    "aws_key_id": "AKIAIOSFODNN7EXAMPLE",
    "anthropic_key": "sk-ant-api03-" + "A" * 24,
    "openai_key": "sk-" + "B" * 24,
    "github_token": "ghp_" + "C" * 24,
    "slack_token": "xoxb-123456789012-abcdefgh",
    "google_api_key": "AIza" + "D" * 35,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36",
    "bearer": "Authorization: Bearer " + "E" * 24,
    "secret_assignment": "MY_TOKEN=abcdefghij",
}


def redact_with(table: tuple[tuple[str, object], ...], text: str) -> str:
    """``redact`` over an arbitrary table — the mutation this canary applies.

    A transcription of ``report/redact.redact`` parametrized by its table. It is
    the *table* this canary removes an entry from, so the replacement logic must
    stay identical for the comparison to mean anything.
    """
    redacted = text
    for label, pattern in table:
        if label == NAME_PRESERVING_LABEL:
            redacted = pattern.sub(  # type: ignore[attr-defined]
                lambda match: _keep_name(match), redacted
            )
        else:
            redacted = pattern.sub(marker(label), redacted)  # type: ignore[attr-defined]
    return redacted


def _keep_name(match: object) -> str:
    """R33's name-preserving replacement, copied so the table is the only variable."""
    whole = match.group(0)  # type: ignore[attr-defined]
    name = match.group(1)  # type: ignore[attr-defined]
    rest = whole[len(name) :]
    separator = 0
    while separator < len(rest) and rest[separator] not in "=:":
        separator += 1
    return f"{name}{rest[: separator + 1]}{marker(NAME_PRESERVING_LABEL)}"


def table_without(label: str) -> tuple[tuple[str, object], ...]:
    """The R33 table with one entry removed."""
    return tuple(entry for entry in REDACTION_PATTERNS if entry[0] != label)


def assert_every_label_fires(table: tuple[tuple[str, object], ...]) -> None:
    """The coverage check this canary breaks: every label replaces its payload.

    This is the assertion ``tests/test_redaction.py`` makes per label; asserting
    it here against a *parametrized* table is what lets the canary hand it a
    table with a hole and watch it raise.
    """
    for label, payload in PAYLOADS.items():
        assert marker(label) in redact_with(table, payload), (
            f"{label} did not fire on its own payload"
        )


def assert_no_credential_shape_survives(table: tuple[tuple[str, object], ...]) -> None:
    """The end-to-end check this canary breaks: no payload reaches the output."""
    document = " | ".join(PAYLOADS.values())
    redacted = redact_with(table, document)
    for shape in ("AKIA", "sk-ant-", "sk-B", "ghp_", "xoxb-", "AIza", "eyJ", "-----BEGIN"):
        assert shape not in redacted, f"{shape} survived redaction"


def test_r50_canary_the_control_arm_passes_with_the_shipped_table() -> None:
    """R50: the canary's control — the real table satisfies both checks."""
    assert_every_label_fires(REDACTION_PATTERNS)
    assert_no_credential_shape_survives(REDACTION_PATTERNS)


@pytest.mark.parametrize("label", sorted(PAYLOADS))
def test_r50_canary_removing_a_pattern_fails_the_coverage_check(label: str) -> None:
    """R50: every one of the ten labels, removed in turn, is caught."""
    with pytest.raises(AssertionError) as raised:
        assert_every_label_fires(table_without(label))
    assert label in str(raised.value)


@pytest.mark.parametrize(
    "label",
    [
        "private_key",
        "aws_key_id",
        "anthropic_key",
        "openai_key",
        "github_token",
        "slack_token",
        "google_api_key",
        "jwt",
    ],
)
def test_r50_canary_removing_a_pattern_lets_a_credential_shape_through(label: str) -> None:
    """R50: the end-to-end assertion goes red too, not only the unit one.

    ``bearer`` and ``secret_assignment`` are excluded because their payloads
    carry no distinctive prefix for a shape probe to look for — which is itself
    worth knowing, and is asserted below rather than left implicit.
    """
    with pytest.raises(AssertionError):
        assert_no_credential_shape_survives(table_without(label))


@pytest.mark.parametrize("label", ["bearer", "secret_assignment"])
def test_r50_canary_two_labels_are_invisible_to_the_shape_probe(label: str) -> None:
    """R50: naming the limit of the end-to-end check rather than hiding it.

    A ``Bearer`` header and a ``NAME=value`` assignment have no distinctive
    prefix a shape probe can look for, so removing either leaves the combined
    document free of every shape the probe knows. The per-label coverage check
    above is what catches these two, which is why R50 wants both checks and not
    either one.
    """
    assert_no_credential_shape_survives(table_without(label))
    with pytest.raises(AssertionError):
        assert_every_label_fires(table_without(label))


def test_r50_canary_the_removal_is_in_memory_only() -> None:
    """R50: the shipped table is untouched after every mutation above.

    The canary rebuilds the table in memory rather than editing the module, so
    a killed process cannot leave a mutated ``report/redact.py`` behind as the
    next run's baseline — the harness discipline the increment-2 review recorded
    after two agents hit it.
    """
    assert [label for label, _ in REDACTION_PATTERNS] == list(PAYLOADS)
    assert_every_label_fires(REDACTION_PATTERNS)
