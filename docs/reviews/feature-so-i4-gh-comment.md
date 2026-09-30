## Review: increment 4 — **Merge**, once S24–S32 are queued

**One blocker, fixed: `SpanError.code` reached both reports unredacted, in both
modes.** The fourth increment running that a trace-derived string bypassed
redaction, each time because its *type* looks enumerated. R12 builds
it from the record's own `error` field with the same `slug()` call R4 uses for
`ParseWarning.detail` — redacted since increment 3. Its alphabet stops markup;
it admits `ghp_…`, `sk-…`, `sk-ant-…` verbatim.

**Instance ten**: increment 3's credential sweep pins
`SpanError(code="api_error", …)` — a constant in the one leaking field — under
a docstring promising "**every** trace-derived string". The goldens were blind
too: the fix changes zero golden bytes.

So it isn't a fifth per-field patch. `tests/test_untrusted_field_sweep.py`
**derives its field list from `model_fields`**: every string field is untrusted
or authored, and one in neither fails. Each gets its own credential (failures
name the field), is asserted absent from both renderers in both modes, and
asserted *present* with a harmless token — "absent" is satisfied by a renderer
that emits nothing (BUG-8).

**Wave 5: 41 mutants over untouched anchors — 36 killed, 5 survived** (control
arm + 4 equivalents). Five first-run survivors were real gaps, all closed with
tests. Worst: **A-d11's "both documents staged in one call" was unasserted**, so
R11's fail-closed guarantee on the *partial-write* path was a hope. Also, the
R34 allowlist could be **widened** unnoticed.

**Rulings.** S26 → the **parser** reading (verified: strip text nodes and the
markup holds no scheme). S27 is under-weighted — a *privacy* flag silently drops
a `critical` finding. S29 → amend R51, not `trace_id`. All A-d1…A-d16 upheld;
A-d8 is the best call here — `render_svg` takes a `Timeline`, so it *cannot*
reach an agent id. The `or True` grep belongs in CI, now an AST scan.

**2555 passed, 1 xfailed on 3.11.15 and 3.12.3; ruff + `mypy --strict` clean.**
Still owed: the rate review and A5's browser check.
