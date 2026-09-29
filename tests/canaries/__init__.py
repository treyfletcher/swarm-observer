"""Canaries: proofs that each guard can actually fail (R50).

Every check in this suite that could be silently disabled — by an inert input,
by a monkeypatched dependency, by an environment that never trips it — has a
test here that deliberately breaks the guard's subject and asserts the guard
raises. A canary that passes when it should fail is itself a suite failure.

The canaries whose subjects exist in increment 1 are the golden-file comparison
and the determinism harness. The rest (the injection probe, the attribute
allowlist, the socket block, redaction idempotence, detector coverage) land with
the increments that build their subjects.
"""
