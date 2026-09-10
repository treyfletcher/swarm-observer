"""Deterministic detectors over the normalized trace (R13-R25).

``detect`` imports ``model`` and nothing else from the package (R44), and every
detector is a pure function of ``(trace, config)``: no I/O, no clock, no
randomness, no environment reads, no mutation of the trace. That purity is what
makes a finding id (R15) a stable suppression key and a report byte-identical
between two machines (R47); it is asserted over the AST rather than reviewed.

:mod:`swarm_observer.detect.registry` is the single source of truth for which
detectors exist (R13, R48). A detector module that is not in ``ALL_DETECTORS``
is a suite failure, not a dormant feature.
"""

from __future__ import annotations
