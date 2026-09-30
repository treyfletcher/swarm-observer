# PR: swarm-observer increment 4 — escaping, redaction hardening, the HTML report and the timeline

Branch: `feature/so-i4` → `main` (based on `main` at `4f2023f`)
Spec: `docs/specs/swarm-observer-v1.md` (APPROVED)
Tasks: T14–T17. Requirements in scope: **R32–R37**, **R46**, **R47**, **R50**,
**R51**; acceptance criteria **AC3**, **AC5**, **AC6**.
Prior rulings honoured: `docs/reviews/feature-so-i3.md` (S14, S16, S21, C1–C5),
`docs/reviews/feature-so-i2.md` (S13).

## Summary

The product is now something a human opens. Five commits: the S14 redaction
fix, the escaping boundary plus one home for the redaction policy, the timeline
and the HTML renderer, the CLI wiring, and one stylesheet correction.

**No** narrator, **no** replay seam, **no** `narrate/` or `replay/` package.
`--explain` is still declared so `--help` shows R38's whole surface and still
refused with a usage error naming increment 5.

`ruff check`, `ruff format --check` and `mypy --strict` are clean; the suite is
**2098 passed, 1 xfailed** on CPython 3.11.15 and 3.12.3 — the same single
deliberate xfail this branch inherited.

**I wrote no new test module.** That is the same call the increment-3 coder made
and the reason is in §"Tester surface" below, because for this increment it is a
substantive decision rather than a procedural one: golden files are the prime
habitat for a check that cannot fail, and a golden generated from the renderer
by the renderer's author is the purest form of it.

### The three things I found by running it rather than by reading it

1. **The spans table dropped the very text the injection probe exists to
   inspect.** It rendered `text_preview or tool_input_preview or
   tool_result_preview` — the first non-empty one. `hostile.jsonl` puts its
   `AKIA` key, its `sk-ant-` key and its PEM block in a **tool result**, on a
   span that also has text. So the first hostile render produced an HTML file
   in which every credential check passed and every payload check passed —
   because the text was never rendered. That is this project's signature defect
   with the report as its subject rather than a test, and it would have shipped
   as "R51 is green". All three previews are now rendered, labelled, and so are
   `stop_reason`, `tool_use_id`, `SpanError`, `agent_type`, `description` and
   `parent_agent_id`. §"Redaction and escaping coverage map" is the audit that
   found the rest of them.

2. **`--no-previews` is not a rendering flag; it changes what the detectors
   find.** `analyze hostile.jsonl` reports `critical=1 warning=1 info=1`; the
   same command with `--no-previews` reports `critical=0 warning=1 info=1`.
   A10 blanks previews at *ingest*, and R22's `unknown_tool` reason is decided
   by matching five phrases against `tool_result_preview` — the one place in the
   product where a detector reads trace free text (A8). With the text gone the
   finding degrades from `critical` to nothing. This is increment-1 behaviour,
   not something this branch introduced, but nothing in the spec says it and
   `report.html` under the flag is therefore not "the same report with the text
   removed". See **S27**.

3. **`python3` does not import this worktree.** From any directory other than
   `/home/claude/so-i4`, `python3 -c "import swarm_observer"` resolves to
   `/home/claude/swarm-observer` — the *main* checkout. Every run below that
   leaves the worktree directory therefore pins `PYTHONPATH`, and the
   determinism harness asserts the imported path before it measures anything.
   The first determinism run I attempted measured `main` and told me so by
   printing `--out is not available yet`; a subtler divergence would not have
   announced itself. Recorded because the next agent will hit it.

## S14, applied first

The increment-3 review ruled S14 must be closed **before** this increment's
renderer ships. It is the first commit on the branch.

R8 caps a preview at 240 code points; R33's `private_key` pattern needs both
`-----BEGIN … KEY-----` and `-----END … KEY-----`. A key longer than the
remaining budget loses its terminator, the pattern cannot match, and
`report.json` for `hostile.jsonl` carried
`-----BEGIN RSA PRIVATE KEY----- MIIBOgIBAAJBAK5f000…` for the whole of
increment 3.

The fix is one alternation inside the existing entry, unterminated branch
**second**, so a complete block still redacts as a single unit:

```python
PRIVATE_KEY_PAIRED        = r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----"
PRIVATE_KEY_UNTERMINATED  = r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*"
```

A second `private_key` row in the table — the review's literal suggestion — was
rejected: it makes `dict(REDACTION_PATTERNS)` lossy and `REDACTION_LABELS` carry
a duplicate, which breaks the table's shape in order to add a row to it. The
left branch is still R33's text verbatim and is pinned as such on its own.

Demonstrated firing, with the paired case and idempotence beside it so the fix
is not "redact everything":

```
$ python3 -c "from swarm_observer.report.redact import redact; ..."
truncated  -> [redacted:aws_key_id] \n[redacted:private_key]
complete   -> x [redacted:private_key] y
pair+trunc -> [redacted:private_key] mid [redacted:private_key]
idempotent: True True
```

And end to end, which is what R51 actually promises — `hostile.jsonl`, both
reports, both modes:

```
credential 'AKIAIOSFODNN7EXAMPLE'   absent from html/json
credential 'sk-ant-api03'           absent from html/json
credential 'BEGIN RSA PRIVATE KEY'  absent from html/json
credential 'PRIVATE KEY'            absent from html/json
redaction markers present in html and json
```

**The cost, stated rather than hidden**: a string that merely *mentions* a PEM
header now loses everything after it. Redaction is not reversible, and a
courtesy that over-redacts a preview is strictly better than one that ships a
key. Previews are deliberately **not** lengthened — the same hole reopens at the
next cap, which is the coder-and-reviewer consensus recorded in S14.

## Requirements coverage

| Req | Where | Notes |
| --- | --- | --- |
| **R32** | `report/escape.py` — `escape_html`, `ESCAPE_TABLE`, `NON_PRINTABLE_REPLACEMENT` | The eight-entry table verbatim, one pass over code points, non-printable → one space **per character** (R8 already collapses runs; doing it again here would be a second normalization R32 does not ask for). R44's AST test already refuses a second definition. The table is deliberately wider than a text node needs — see A-d1. One inherited interpreter-dependence, flagged as **S24**. |
| **R33** | `report/redact.py` — the S14 alternation; `report/sanitize.py` — `free_text`, `identifier`, `metric_value` | The pattern table is still R33's, in R33's order, with the one documented exception above. The *policy* — which strings are free text, which are keys, what `--no-previews` does to each — moved to `sanitize.py` so both renderers call one implementation. See A-d2. |
| **R34** | `report/html.py` — `REPORT_SCRIPT`, `REPORT_STYLE`, `SCRIPT_SHA256`, `STYLE_SHA256`, `sha256_of`, `attribute_allowlist`, `CSP_CONTENT`, `CSS_CLASSES`, `FORBIDDEN_SCRIPT_APIS` | One `<script>`, one `<style>`, both constants with pinned digests, written by concatenation and never by formatting. Attribute allowlist generated from the **inputs**, not from the rendered bytes. CSP is the first `<head>` element after `<meta charset>`. See A-d4, A-d5 and **S25**. |
| **R35** | `report/html.py`, `report/timeline.py` | No URL of any scheme in any attribute, no `<link>`/`<img>`/`<iframe>`/`<object>`/`<embed>`/`<form>`/`<base>`, no `@import`, no font or icon reference, no `//` anywhere in the file, and **no SVG `xmlns`** — its only legal value is an `http:` URL (A-d6). Every `href` is a same-document fragment. See **S26** on the one clause that cannot be read as bytes. |
| **R36** | `report/html.py` — `render_html`, `SECTION_IDS`, `SPANS_TABLE_CAP`, `_grouped` | Sections in the pinned order; the narrative anchor is absent because R43 says it is. Findings grouped severity-descending then slug then id, applied here rather than by adding a second ordering rule to `detect/` (A-c8's shape). Spans capped at 5,000 with an "…and N more" line. |
| **R37** | `report/timeline.py` — `round_half_up`, `build_timeline`, `render_svg`, `Timeline`, `TimelineRect`, `TimelineLane`, `severity_by_seq`, `VIEWBOX_WIDTH`, `MIN_RECT_WIDTH`, `RECT_CLASSES` | Integer-only geometry via `(2n + d) // 2d`. All three named edge cases handled. One clamp R37 does not specify, declared as A-d7. No trace-derived byte enters the `<svg>` at all (A-d8). |
| **R46** | nothing new; verified by hand below | The analyze path imports no networking module and opens no socket. Demonstrated under a `socket.socket` subclass that raises, **with its control arm**. |
| **R47** | `report/html.py`, `report/timeline.py`, `cli/main.py` | No float, no clock, no path, no unsorted iteration in either document. 48 environments (2 interpreters × 4 `PYTHONHASHSEED` × 3 `TZ` × 2 `LC_ALL`) produce one hash, plus reversed path order, two working directories, two absolute paths to identical content, and directory expansion. |
| **R50** | *not delivered* | The four increment-4 canaries are the tester's, and `CURRENT_INCREMENT` is deliberately left at 3. See "What the harness expects of you". |
| **R51** | *probe not delivered; the subject is* | The renderer is built so the probe has something to assert; the probe is the tester's. My hand-run stand-in and its four control arms are below. |
| **R38–R40** | `cli/main.py` — `analyze`, `_DEFERRED_FLAGS` | `--out` wired; at least one of `--out`/`--json` required (A-d10); both documents rendered in memory before either is staged; stdout names the paths in flag order. |
| **AC3** | hand-verified below; the suite's version is the tester's | |
| **AC5** | hand-verified below, **with a gap in the acceptance criterion itself** — see **S28** | |
| **AC6** | hand-verified below (48 environments + six variants) | |

## Assumptions and interpretation calls (A-d series)

- **A-d1 (R32's table is applied to *every* string the renderer writes, not only
  the untrusted ones).** Section headings, column labels, enumerated slugs, the
  caveat sentences and `Finding.summary` all pass through `escape_html`. R16
  guarantees `summary` contains no trace-derived byte, so this is a no-op today.
  It is done anyway because the alternative makes the safety property a fact
  about this module's knowledge of its inputs, and because it deletes "is this
  string trusted?" as a question a future editor has to answer correctly on
  every line. The Modularity notes' rule — "no renderer may assume its caller
  sanitized" — read one level down.

- **A-d2 (`report/sanitize.py` is a new module, and `json_out` re-exports every
  name it lost).** R44 pins the package layout and does not list `sanitize.py`,
  so this is an addition to a pinned structure and is declared as one. The
  reason is the increment-3 review's own account of BUG-2: three increments in a
  row found an untrusted field bypassing the boundary, each fix was correct, and
  each lived inside the only renderer that existed. There are two renderers now,
  and the alternative — `html.py` importing `free_text` from `json_out.py` —
  makes the HTML report depend on the JSON report for its security policy.
  Nothing moved behaviourally; `from swarm_observer.report.json_out import
  free_text, identifier, RenderOptions` still works and no test changed.
  **PM**: one line in R44's layout block, or a ruling that `report/` may hold
  modules the block does not name.

- **A-d3 (`metrics` is redacted and rendered in the HTML, not blanked).** A-c7's
  reasoning, unchanged: R15 hashes `metrics` into the `finding_id` the document
  prints, so a masked metric makes the printed id unverifiable from the printed
  evidence. Safe because R16 constrains the one trace-derived key to
  `^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$` — **naming the constraint, as the brief
  requires**: that alphabet admits no `<`, `>`, `"`, `'`, `/`, backtick, `=`,
  space or control character, so no markup payload can be a legal tool name —
  and redaction covers the credential shapes that *are* legal (S13).
  Demonstrated: a trace whose tool name is `AKIAIOSFODNN7EXAMPLE` renders
  `tool_name&#x3D;[redacted:aws_key_id]`.

- **A-d4 (R34's attribute allowlist is generated from the inputs, and includes
  exact geometry value sets).** R34 lists six kinds of attribute value. The
  document needs more than six: the SVG geometry attributes R37 requires, the
  `id` attributes the `href` fragments point at, `lang`, `charset`,
  `http-equiv`/`content` for the CSP meta R34 itself mandates, `type="button"`
  for the filter controls, and `role`/`aria-label` on the figure. Rather than
  widen R34's prose list by hand, `attribute_allowlist(trace=…, findings=…,
  timeline=…)` returns `dict[str, frozenset[str]]` computed from the **inputs**.
  The geometry entries are exact value sets, not "any decimal integer", because
  the set of `x` values the figure may use is knowable from the timeline model —
  so an `x` that is not one of them is a leak even though it looks like a
  number. A list harvested from the rendered document would be satisfied by any
  document, payload included. **PM**: see S25.

- **A-d5 (the pinned digests are literals in `html.py`, and the assertion that
  matters is against the *parsed* document).** `SCRIPT_SHA256` and
  `STYLE_SHA256` are checked-in literals, so changing the script costs two edits
  in two places with the reason written between them — the friction
  `collection_floor.json` and `LEAK_LEDGER_SIZE` already carry. Hashing the
  constant against the constant proves little on its own; the assertion with
  content is `sha256(<script> element as html.parser yields it) ==
  SCRIPT_SHA256`, which is what proves nothing was interpolated **at render
  time**. Both are in my hand-run probe and the second is what went red when I
  appended one line to the script.

- **A-d6 (no SVG `xmlns`).** An inline `<svg>` in an HTML5 document is put into
  the SVG namespace by the HTML parser; the attribute's only legal value is
  `http://www.w3.org/2000/svg`, which is an `http:` URL, which R35 forbids
  *anywhere in the rendered file*. Writing it would break a pinned security
  property in order to state something the parser already knows. The figure
  renders correctly without it in every HTML5 browser. It is listed among R34's
  URL-bearing attributes in my probe so its absence is asserted rather than
  assumed.

- **A-d7 (R37's geometry is clamped to the viewBox; the formula is untouched).**
  Rounding each end independently can push a rect one unit past its own
  viewBox — `x = round_half_up(1000·0.0005) = 1` together with
  `width = round_half_up(1000·0.9995) = 1000` gives a right edge at 1001. R37
  pins the formula and says nothing about the clamp. A rect outside its viewBox
  is a rendering defect in every browser, so `x` is clamped to
  `VIEWBOX_WIDTH − MIN_RECT_WIDTH` and `width` to `VIEWBOX_WIDTH − x`, both after
  the pinned computation. **PM**: one clause on R37.

- **A-d8 (no trace-derived byte enters the SVG; the lane labels are HTML beside
  it).** R37 says one lane per agent and R34 says trace text may appear only in
  a text node. An SVG `<text>` element *is* a text node, so labels inside the
  figure would have been legal — but putting them there means the figure has an
  escaping question, and a 1000-unit viewBox has no room for a gutter without
  changing the pinned `x` computation. The lane legend is therefore an HTML
  table beside the figure, and the resulting property is stronger than R37 asks
  for: **every character of the `<svg>` element is a fixed class name this
  package authored or the decimal form of an integer it computed.**

- **A-d9 (the lane legend renders unconditionally, including when there is no
  figure).** It is the only place `AgentRun.agent_type`, `description` and
  `parent_agent_id` reach the HTML. A legend that appeared only when some span
  happened to carry timing would mean "no payload in the report" was sometimes
  true because nothing was rendered — which is exactly the defect described in
  §"three things I found", one section over.

- **A-d10 (`--out` or `--json`, at least one).** R38 writes `--out` as required
  and `--json` as optional; the increment-3 review's ruling on S18 is that a
  `--json`-only run is legitimate for a CI job gating on `--fail-on`. Enforcing
  "at least one" honours both. A run naming neither is still a usage error
  (exit 3), because A-c10's point is that a run must not report success without
  writing the file it was asked for. `--out` and `--json` naming the same path
  is also a usage error rather than one report silently overwriting the other.

- **A-d11 (both documents are rendered before either is staged).** R11 requires
  no output file be written or truncated on failure. With two outputs that
  becomes a stronger requirement than it was with one: a renderer that raised
  halfway would leave one report written and the other not, which is a partial
  result with a successful exit code. Both documents are built in memory and
  handed to `atomic_write_texts` in a single call. Verified with both output
  paths pre-existing and a malformed trace — exit 2, both files unchanged.

- **A-d12 (the spans table carries every preview, separately labelled).** See
  §"three things I found" (1). R36 says the spans table shows "all spans"; it
  does not enumerate columns. Showing one preview per span made the document
  poorer *and* made the injection probe vacuous, and only the second of those is
  a security property.

- **A-d13 (`CostReport.cost_of` still has no caller).** The review's C2 predicted
  it would acquire one in this increment's HTML and become O(n²) over 5,000
  rows. The spans table builds a `{seq: cost}` mapping once instead. C4's
  `seq == index` assumption is likewise not taken: every lookup in `report/` is
  by dict key, never by positional index into `trace.spans`.

- **A-d14 (`format_display_usd` finally has a caller).** R29's two-decimal grand
  total is rendered beside the six-decimal one, in the cost section only. It is
  the only non-six-decimal decimal token in a rendered report.

- **A-d15 (the unpriced count sits beside the grand total).** The increment-3
  review's §6 recommendation, implemented: a trace on a retired model contributes
  nothing to the headline figure and only a reader who scrolled to the unpriced
  section would otherwise know.

- **A-d16 (the one script does filtering and collapsing, and no sorting).** R34
  permits "class toggling, filtering and sorting" — an upper bound, not a
  requirement. The script filters findings by severity and collapses a section,
  both by `classList`. Sorting a 5,000-row table would have roughly tripled the
  script for a feature nobody asked for, and every line of that script is a line
  inside the one executable context in the document. The script contains no
  `//`, and its comments would be `/* */`, because a line comment is
  indistinguishable from a scheme-relative URL to a check that reads bytes.

## Spec flags

Continuing from the increment-3 tester's **S23**.

| # | Requirement | Issue | Suggested resolution |
| --- | --- | --- | --- |
| **S24** | R32 / R8 | R32 says "every other character that is not `str.isprintable()` becomes a single space". `str.isprintable()` answers from the Unicode table compiled into the running interpreter — 14.0 on CPython 3.11, 15.0 on 3.12 — so the same trace can render two different documents on the two interpreters CI builds. This is the same drift increment 1 found in R8 and the increment-2 review ruled was "a spec amendment, not a code change"; R32 inherits it. Blast radius is bounded but not zero: R8 normalizes the three preview fields at ingest with the same predicate, and `TestCheckedInDataIsInterpreterStableR8` keeps unassigned code points out of checked-in data, but `Span.model`, `stop_reason`, `tool_name`, `tool_use_id`, `SpanError.detail`, `AgentRun.description` and `SourceFile.name` never pass through R8 and would diverge on a live trace. | Replace `str.isprintable()` in **both** R8 and R32 with a fixed code-point rule that cannot move — C0, DEL, C1, and the `Cf`/`Zl`/`Zp` ranges named by number rather than by category lookup. One rule, one place, and a golden file that is a function of the trace rather than of the interpreter. Implemented verbatim as written in the meantime, because the alternative is code and spec silently out of step. |
| **S25** | R34 | R34's attribute allowlist names six kinds of value. A document that satisfies the rest of R34 needs more: R37's SVG geometry attributes; the `id` attributes R34's own `href="#<span_id\|finding_id>"` must point at; `lang`; `charset`; the `http-equiv`/`content` pair for the CSP meta R34 itself mandates; `type="button"` for any control the script attaches to; `role`/`aria-label` on the figure. As written, a literal reading of R34's list fails on the document R34 requires. | State that the allowlist is **generated**, name the classes rather than the values (fixed class names; enumerated slugs from a closed enum; decimal integers drawn from the trace or the computed layout; same-document fragments built from `span_id`/`finding_id`; and a named set of document-chrome constants), and require the generating function to take the *inputs* rather than the rendered document. `report.html.attribute_allowlist` is that function. |
| **S26** | R35 | R35 forbids "`http:`, `https:`, `//` scheme-relative, `file:`, or protocol-handler URL anywhere" and then says "asserted by a parser-based test, not a regex over the source". R51 **requires** `javascript:alert(1)` to appear in the report, in a text node. The two are consistent only under the parser reading; under a byte reading they contradict each other, and a tester who implements R35 as a `grep` will find `javascript:` in `report.html` and be right to. | One clause: R35 constrains **URL contexts** — attribute values, element types and stylesheet content — and explicitly not text nodes, where R51 requires the same strings to appear. Worth settling before the probe is written, because both readings look correct in isolation. |
| **S27** | R38 / A10 / R47 | `--no-previews` is described as an output-side guarantee ("omits trace free text entirely"), but A10 blanks at ingest, and R22's `unknown_tool` reason is decided by matching five phrases against `tool_result_preview`. The flag therefore changes **which findings exist and at what severity**: `hostile.jsonl` reports `critical=1` by default and `critical=0` under the flag. Nothing in the spec says a privacy flag moves a detector's verdict, and the two reports are not the same report with the text removed. | Either state plainly under R38/A10 that `--no-previews` suppresses R22's `unknown_tool` reason (and that the mode's findings are a subset), or move R22's phrase matching to a value computed at ingest **before** blanking — a boolean on the span — so the flag stops changing detection. I prefer the second; it is a mapper change, which is why I have not made it. |
| **S28** | AC5 / R48 | AC5 requires "a multi-agent fixture trace with a known finding in **every** detector class". No such trace exists and the corpus cannot produce one: analyzed individually, each fixture fires its own detectors; analyzed together, R6 orders by file basename and agents with the same `agentId` interleave across files, which breaks `agent_loop`'s per-agent signature sequence and `retry_storm`'s sliding window. Best achieved over the whole 26-fixture corpus is 4 of 7; the best curated six-file set I found reaches 5 of 7 (`agent_loop` and `retry_storm` missing). | Add one fixture whose agents are disjoint from the rest of the corpus and which carries a finding from all seven detectors, or amend AC5 to say "every detector class across the fixture set". This is the same shape as the increment-3 review's §7.6 recommendation — an acceptance criterion whose input the corpus cannot produce is a check that cannot pass, which is the mirror of one that cannot fail. |

## Redaction and escaping coverage map

Every string that reaches `report.html`, where it comes from, and which boundary
handles it. The rule: **trace-derived and attacker-influenceable strings go
through `redact` then `escape_html`; everything else goes through `escape_html`
anyway** (A-d1).

### Trace-derived free text — redacted, blanked under `--no-previews`, escaped

| Field | Where it appears in the HTML | Boundary |
| --- | --- | --- |
| `Span.text_preview` | spans table, `text:` block | `free_text` → `escape_html` |
| `Span.tool_input_preview` | spans table, `input:` block | `free_text` → `escape_html` |
| `Span.tool_result_preview` | spans table, `result:` block | `free_text` → `escape_html` |
| `Span.model` | spans table; cost "unpriced model calls" | `optional_text`/`free_text` → `escape_html` |
| `Span.stop_reason` | spans table | `optional_text` → `escape_html` |
| `Span.tool_name` | spans table | `optional_text` → `escape_html` |
| `Span.tool_use_id` | spans table | `optional_text` → `escape_html` |
| `SpanError.detail` | spans table, error cell | `free_text` → `escape_html` |
| `AgentRun.agent_type` | timeline lane legend | `optional_text` → `escape_html` |
| `AgentRun.description` | timeline lane legend | `free_text` → `escape_html` |
| `Finding.previews` | findings, `<p class="preview">` | `free_text` → `escape_html` |
| `SpanCost.model` / `UnpricedSpan.model` | cost, unpriced table | `free_text` → `escape_html` |

### Trace-derived identifiers and join keys — redacted in **both** modes, escaped

Blanking these collapses `spans`, the lane legend and `cost.by_agent` into one
row each and merges the `(code, detail)` pairs R10 aggregates on — the
increment-3 review's ruling on S16.

| Field | Where | Boundary | Why it is not blanked |
| --- | --- | --- | --- |
| `Span.agent_id` | spans table | `identifier` → `escape_html` | join key |
| `AgentRun.agent_id` | lane legend | `identifier` → `escape_html` | join key |
| `AgentRun.parent_agent_id` | lane legend | `identifier` → `escape_html` | join key |
| `Finding.agent_ids` | findings | `identifier` → `escape_html` | join key |
| `AgentCost.agent_id`, `SpanCost.agent_id`, `UnpricedSpan.agent_id` | cost tables | `identifier` → `escape_html` | join key |
| `ParseWarning.detail` | warnings table | `identifier` → `escape_html` | half of R10's aggregation key |
| `Finding.metrics[tool_name]` | findings metrics line | `metric_value` → `escape_html` | R15 hashes `metrics` into the printed `finding_id` (A-c7, A-d3) |
| `SourceFile.name` | header, source-files table | `identifier` → `escape_html` | **S21**: not trace-derived, but `analyze <dir>` reads whatever basenames the directory holds. A report that cannot say which files it read is not a report. Demonstrated below with a filename carrying an `AKIA` key, `<script>` and an ESC. |

### Package-authored strings — escaped, not redacted

`Finding.summary` (R16 guarantees no trace byte), `Finding.detector`,
`Finding.finding_id`, `Finding.severity`, `Span.kind`, `Span.tool_result_status`,
`SpanError.code`, `ParseWarning.code`, `UnpricedSpan.reason` and
`missing_price_keys`, `ModelCost.model_key`, `Trace.adapter`,
`Trace.schema_version`, the snapshot version/date, the tool version, every
caveat sentence and every heading. Escaped anyway (A-d1). Not redacted, because
running the redactor over a string this package authored would blur where the
guarantee lives — the same call `json_out` made for `summary`.

### Strings that reach an **attribute**, and what makes each safe

This is the list the brief asks to be explicit about.

| Attribute | Value | What makes it safe |
| --- | --- | --- |
| `id`, `href="#…"` (span) | `Span.span_id` | **Constrained alphabet, named**: R5 builds it from SHA-256 and `Span.span_id` is pattern-validated `^[0-9a-f]{16}$` on the model. Escaped as well, so the alphabet is a second line rather than the only one. |
| `id`, `href="#…"` (finding) | `Finding.finding_id` | **Constrained alphabet, named**: `^[a-z][a-z0-9_]{0,63}:[0-9a-f]{12}$`, pattern-validated on `Finding`, with a model validator requiring its slug half to equal `Finding.detector`. Escaped as well. |
| `href="#…"` (nav) | `SECTION_IDS` | Module constant. |
| `data-seq` | `Span.seq` | `int`, rendered by `str`. |
| `data-agent` | `AgentRun.agent_index` | `int`, rendered by `str`. |
| `data-severity` | `SEVERITIES` + `"all"` | Closed enum plus one constant. |
| `data-detector` | `DETECTOR_SLUGS` | The registry (R13, R50). |
| `class` | `CSS_CLASSES` ∪ `RECT_CLASSES` | Module constants, enumerated. |
| `x`, `y`, `width`, `height`, `viewBox` | computed layout | Integers only (R37), by `(2n + d) // 2d`. |
| `lang`, `charset`, `http-equiv`, `content`, `type`, `role`, `aria-label` | document chrome | Module constants. |

**`Span.agent_id` is trace-derived and has a constrained alphabet too**
(`^[A-Za-z0-9_.:\-]{1,64}$`, enforced by the mapper's `safe_agent_id`, which is
why `hostile.jsonl`'s `agentId` of `<script>alert(1)</script>` never becomes one).
It is deliberately **not** used in any attribute: `data-agent` carries the
integer `agent_index`, and the agent id appears only as redacted, escaped text.
Relying on that alphabet would have worked; not relying on it costs nothing, and
the last three increments were each a field that "looked like an identifier".

### What is verified, and what is asserted

Every row above was checked against the rendered bytes, not against the source:
the allowlist check over 66,513 attributes of a 5,200-span report found zero
outside the generated set, and the credential sweep over `hostile.jsonl` plus a
hostile **filename** found zero raw credential shapes in either document in
either mode. The four control arms in §"Smoke tests" are what make those numbers
mean something.

## Tester surface

### What to drive

```python
from swarm_observer.report.escape import ESCAPE_TABLE, NON_PRINTABLE_REPLACEMENT, escape_html
from swarm_observer.report.redact import (
    NAME_PRESERVING_LABEL, PRIVATE_KEY_PAIRED, PRIVATE_KEY_UNTERMINATED,
    REDACTION_LABELS, REDACTION_PATTERNS, marker, redact,
)
from swarm_observer.report.sanitize import (
    RenderOptions, free_text, identifier, metric_value, optional_text,
)
from swarm_observer.report.timeline import (
    LANE_HEIGHT, MIN_RECT_WIDTH, NO_SEVERITY, RECT_CLASSES, RECT_HEIGHT,
    RECT_SEVERITIES, RECT_Y_OFFSET, VIEWBOX_WIDTH,
    Timeline, TimelineLane, TimelineRect,
    build_timeline, render_svg, round_half_up, severity_by_seq,
)
from swarm_observer.report.html import (
    ALL_SEVERITIES, CSP_CONTENT, CSS_CLASSES, FORBIDDEN_SCRIPT_APIS,
    REPORT_SCRIPT, REPORT_STYLE, SCRIPT_SHA256, SECTION_IDS,
    SPANS_TABLE_CAP, STYLE_SHA256, UNTIMED_LIST_CAP,
    attribute_allowlist, render_html, sha256_of,
)
```

### Where the seams are

- **`render_html(trace=…, findings=…, cost=…, tool_version=…, options=…)`** takes
  the same five arguments as `render_json`, so a document can be produced from a
  hand-built `Trace` with no CLI and no filesystem. `tests/synthetic_traces.TraceBuilder`
  is still the cheapest way to put both sides of a boundary one integer apart.
- **`attribute_allowlist(trace=…, findings=…, timeline=…)`** is the R34 check's
  oracle and is generated from the inputs. Feed it the same three objects
  `render_html` got, parse the output with `html.parser`, and compare. **The arm
  that matters**: a test that builds the allowlist from the rendered document
  instead would pass on any document — assert that the allowlist is non-vacuous
  (mine checks `len(attrs) > 50` and at least 8 distinct names) and that
  injecting a trace string into a `data-` attribute makes it red.
- **`build_timeline(trace, findings)` → `Timeline`** is geometry without markup,
  so `x`, `y`, `width` and `height` can be asserted as integers without parsing
  SVG. `render_svg(timeline)` is the markup half and takes a `Timeline`, not a
  `Trace` — it *cannot* reach an agent id, which is a property worth pinning.
- **`round_half_up(n, d)`** is the whole float question in one three-line
  function. `round_half_up(5, 2) == 3` where Python's `round(2.5) == 2`.
- **`sha256_of` + `SCRIPT_SHA256`/`STYLE_SHA256`** — hash the **parsed**
  `<script>`/`<style>` content, not the module constants. The constant-against-
  constant form cannot detect a render-time interpolation, which is the thing
  R34 exists to prevent.
- **`FORBIDDEN_SCRIPT_APIS`** is R34's forbidden-identifier list as data. Grep
  `REPORT_SCRIPT`, **not `html.py`'s source** — the module necessarily contains
  every one of those strings, in that list. Pin the list's length as a literal
  so it cannot be narrowed to make a failing check pass.
- **`main(argv, stdout=…, stderr=…)`** is unchanged and still takes both streams.

### What I think is risky

Ranked, most to least.

1. **The allowlist is my own oracle and I generated it.** Every R34 assertion
   runs through `attribute_allowlist`, which I wrote, from inputs I chose. It is
   exactly the shape the increment-2 review ruled against for mutation sets. The
   mitigations are that it is generated from inputs rather than from output and
   that it has a control arm — but a tester should independently enumerate what
   the document *actually* contains and ask whether each entry belongs, rather
   than only checking membership. The most likely hole is an attribute I add in a
   later edit and add to the allowlist in the same commit.
2. **The S14 alternation over-redacts.** The unterminated branch runs to end of
   string. Drive: a preview that mentions a PEM header in prose (everything after
   is lost); two complete blocks in one string (two markers, not one); a complete
   block followed by a truncated one; a truncated block inside a
   `PRIVATE_KEY=` assignment (`secret_assignment` runs afterwards and must not
   double-mark); and idempotence over the whole generated corpus, since the
   generator alphabet already contains both PEM fragments.
3. **`escape_html` and `str.isprintable()` (S24).** Assert the eight table
   entries individually, AC4's single-pass property (`&lt;` present, `&amp;lt;`
   absent), that `\x00` and `\x1b` each become **one** space, and — the arm that
   is easy to miss — that a newline becomes a space, because that is what keeps
   a trace string from introducing a line break into the document's source. Then
   pin the interpreter question: a `Cn`-on-3.11 code point renders differently
   on 3.12, and `TestCheckedInDataIsInterpreterStableR8` is the only thing
   keeping it out of goldens.
4. **Goldens.** See below — I did not write any, and the reason is the warning
   in my own brief. When you write them, the question to answer in the test's
   docstring is *what change to the renderer would make this red*. A byte-golden
   over the whole document answers "any", which sounds strong and is actually
   the weakest possible answer, because it tells you nothing about which
   property broke and it will be regenerated the first time it fails. Pair each
   golden with a structural assertion that names the property.
5. **`--no-previews` changes the findings (S27).** The two modes' reports differ
   by more than text. Any test that diffs the two documents and attributes the
   difference to blanking is wrong today.
6. **The timeline's rounding boundaries.** `round_half_up` at exactly `.5`; the
   clamp (A-d7) needs a trace where `x + width` would reach 1001, which takes a
   span starting at ~0.05% and running to the end; `trace_span_ms == 0`; a
   single timed span; a trace with no timed span at all; an agent with a lane
   and no rects.
7. **The spans cap.** 5,000 exactly, 5,001, and the "…and N more" count. A
   finding naming a span past the cap renders the seq without a link — assert
   that, because the alternative (an `href` to a non-existent anchor) would also
   look fine.
8. **Section order and the absent narrative anchor.** R36's order is pinned;
   R43 requires that `<section id="narrative">` does **not** exist without
   `--explain`. Both are one regex, and both will be edited by increment 5.

### What I deliberately did not verify, and why

- **Any per-requirement test, and specifically any golden file.** By the brief,
  per-requirement tests are yours. For goldens the reason is stronger than the
  brief: my own instructions name golden files as the prime habitat for a check
  that is structurally unable to fail, and a golden generated from the renderer
  by the renderer's author, in the same commit, is that defect in its purest
  form — I would regenerate it whenever it failed, because I would assume the
  renderer was right. A golden built by someone reading R36's section list and
  R34's rules against a document they did not write is a different artefact. The
  renderer is deterministic across 48 environments (below), which is the
  precondition a golden needs; building it is yours.
- **A mutation sweep, and any mutation score.** Same ruling as increment 3, and
  the increment-3 review demonstrated its cost: a self-chosen set produced
  289/289 with a broken oracle, and only the control arm noticed. What I have
  done instead is name, above, the eight places where a one-character change
  would produce a plausible wrong document. `tests/mutations.json`'s per-module
  floor test lists increment-3's modules; `report/html.py`, `report/timeline.py`,
  `report/escape.py` and `report/sanitize.py` are **not** in it, so a sweep over
  them is currently optional rather than required — that is a gap somebody other
  than me should close.
- **The R50 canaries and `CURRENT_INCREMENT`.** Left at 3 deliberately; see
  below.
- **Behaviour in an actual browser.** Everything below is `html.parser` and byte
  inspection. Nothing was opened in Chrome or Firefox, so "the CSP meta is
  honoured", "the filter buttons work" and "the figure looks right" are
  unverified. A5's manual check is still owed and now has a second half.
- **Accessibility beyond `role`/`aria-label` on the figure.** Not a requirement;
  not done.

### What the harness expects of you

1. Add a `tests/collection_floor.json` entry for every new test module. Floors
   are at current counts: `test_boundaries_and_posture.py` 43 → **47** (R44 is
   parametrized over the four new modules) and `test_redaction.py` 155 → **156**.
2. Delete lines from `tests/traceability_pending.txt` as you cite. Still
   pending: **R32, R34, R35, R37, R41, R42, R43, R46, R51**. R33, R36 and R47
   left the ledger in increment 3 for their JSON halves only, and the ledger's
   comment says so — R33's HTML boundary, R36's section order and spans cap, and
   R47's `report.html`/SVG bytes are all yours and are **not** covered by those
   citations. I added one citation of R38 in `test_cli_analyze.py` and was
   careful not to cite R34/R35/R36/R51 anywhere in a test docstring, precisely
   so the ledger does not shrink on a citation that is not coverage (A-c13).
3. **`CURRENT_INCREMENT` is still 3, on purpose.** Bumping it to 4 makes
   `test_r50_this_increments_canaries_exist` demand four canaries that do not
   exist: `injection_probe_identity_escape`, `attribute_allowlist_injection`,
   `offline_socket_permitted` and `metrics_redaction_dropped`. All four now have
   subjects. Bump it in the same commit as the canaries, the way
   `redaction_pattern_removed` was moved in increment 3. My four control arms in
   §"Smoke tests" are working implementations of the first, second and fourth.
4. `tests/allowed_skips.txt` stays empty.
5. `tests/mutations.json`'s `test_r49_the_ledger_covers_every_module_the_increment_touched`
   lists increment-3 modules only. Adding this increment's four is a decision
   about what a sweep must cover, which is not mine to make unilaterally.

## Smoke tests — every command run from this branch, output copied verbatim

### Which tree each interpreter imports

```
3.11 imports /home/claude/so-i4/swarm_observer/__init__.py
3.12 imports /home/claude/so-i4/swarm_observer/__init__.py
```

Asserted before every measurement, with `PYTHONPATH` pinned: without it,
`python3` outside the worktree imports `/home/claude/swarm-observer`.

### Lint, types, tests — both interpreters

```
$ python3 -m ruff check .              ->  All checks passed!
$ python3 -m ruff format --check .     ->  69 files already formatted
$ python3 -m mypy                      ->  Success: no issues found in 34 source files   (3.11)
$ /tmp/v312/bin/python -m mypy         ->  Success: no issues found in 34 source files   (3.12)
$ python3 -m pytest                    ->  2098 passed, 1 xfailed in 33.98s              (3.11)
$ /tmp/v312/bin/python -m pytest       ->  2098 passed, 1 xfailed in 32.22s              (3.12)
```

2093 → 2098: +4 from `test_r44_subpackage_import_rules` parametrized over the
four new modules, +1 from the new pattern-source pin in `test_redaction.py`, and
a net 0 on `test_cli_analyze.py` (one parametrized arm removed, one test added
in its place). Two existing tests changed, both because this branch made them
false, both documented in their commits and in their own docstrings:
`test_r33_a_pem_block_whose_terminator_was_truncated_is_not_redacted` is
inverted in place as its docstring instructed, and the `--out` arm of the
deferred-flag parametrization is replaced by a test that the flag now works.

One flake seen once and not reproducible:
`test_r23_blocked_agent_does_not_grow_quadratically` failed on 3.12 during a run
launched concurrently with another suite, and passes in isolation and in every
subsequent full run. It is a wall-clock scaling assertion, so it is
load-sensitive by construction. Worth a tester's eye; not a defect I can
reproduce.

### `analyze --help` — R38's surface

```
usage: swarm-observer analyze [-h] [--out <report.html>]
                              [--json <report.json>]
                              [--adapter {claude_code_jsonl}] [--explain]
                              [--no-previews] [--detector <slug>]
                              [--blocked-gap-seconds N]
                              [--fail-on {none,warning,critical}]
                              [--max-file-bytes N] [--max-line-bytes N]
                              [--max-records N]
                              <path> [<path> ...]
```

### The hostile fixture, rendered — AC3

`hostile.jsonl`, both modes, parsed with `html.parser` and asserted against the
parsed document rather than against a function's return value:

```
=== [hostile previews=True]  html=19049B json=15190B ===
PASS  one <script>                          PASS  one <style>
PASS  script sha pinned                     PASS  style sha pinned
PASS  no HTML comment                       PASS  no R35 element
PASS  no on* attribute                      PASS  no URL-bearing attribute
PASS  every href is a fragment              PASS  no scheme in any attribute
PASS  every attribute value allowlisted     PASS  allowlist is non-vacuous
PASS  script uses no forbidden API          PASS  CSP is first head element after charset
PASS  payload '</script><script>alert(1)</script>'   in a text node
PASS  payload '"><img src=x onerror=alert(1)>'       in a text node
PASS  payload 'javascript:alert(1)'                  in a text node
PASS  payload '<!--'                                 in a text node
PASS  payload ']]>'                                  in a text node
PASS  payload '&lt;script&gt;'                       in a text node
PASS  payload '{{7*7}}'                              in a text node
PASS  payload '../../etc/passwd'                     in a text node
PASS  RTL override removed entirely
PASS  redaction markers present in html     PASS  redaction markers present in json
PASS  credential 'AKIAIOSFODNN7EXAMPLE'   absent from html   absent from json
PASS  credential 'sk-ant-api03'           absent from html   absent from json
PASS  credential 'BEGIN RSA PRIVATE KEY'  absent from html   absent from json
PASS  credential 'PRIVATE KEY'            absent from html   absent from json

=== [hostile previews=False]  html=16313B json=12637B ===
PASS  no payload under --no-previews  (all ten, html and json)
PASS  every attribute value allowlisted     PASS  script/style sha pinned

… the same structural checks over all 26 fixtures …
FAILURES: 0
```

Two results in that list are worth reading rather than skimming:

- **`RTL override removed entirely`** rather than "in a text node". U+202E is
  category `Cf`, so R8 and R32 both replace it with a space. It is *removed*,
  not escaped — which is the outcome a report wants from a bidi-override, and
  which means R51's "appears only in a text node" is satisfied vacuously for
  that one payload. A probe that asserts presence will be wrong about it.
- **`&lt;script&gt;` appears once, and `&amp;amp;` appears zero times.** The
  hostile corpus contains the literal text `&lt;script&gt;`; escaping it once
  produces `&amp;lt;script&amp;gt;`. A second application would produce
  `&amp;amp;lt;`. The counts are AC4's single-pass property asserted against the
  rendered document rather than against the function.

### The probe's control arms — proof it can fail

A green probe is worth nothing until it goes red on a broken renderer. Four
deliberate breaks, each reverted, with the unmodified run either side:

```
--- CONTROL: unmodified (must be 0)                -> FAILURES=0
--- CONTROL: escape_html is the identity function  -> FAILURES=11
      FAIL  one <script>
      FAIL  script sha pinned
      FAIL  payload '</script><script>alert(1)</script>'  in a text node
      …
--- CONTROL: a trace string in a data- attribute    -> FAILURES=1
      FAIL  every attribute value allowlisted
            [('p', 'data-detector', '<!-- ]]> <script> {{7*7}} ../../etc/passwd `whoami` abc def')]
--- CONTROL: the constant script was edited         -> FAILURES=1
      FAIL  script sha pinned
--- CONTROL: redact is the identity function        -> FAILURES=10
      FAIL  redaction markers present in html
      FAIL  credential 'AKIAIOSFODNN7EXAMPLE' absent from html
      …
--- CONTROL: restored (must be 0 again)             -> FAILURES=0
```

The first three are working implementations of three of the four R50 canaries
this increment owes.

### S21 — a hostile **filename**, which is not trace-derived

A file named `AKIAIOSFODNN7EXAMPLE-<ESC>[31m<script>.jsonl` sitting in a scanned
directory:

```
html: AKIA present: False | ESC present: False | raw <script> present: False
json: AKIA present: False | ESC present: False

<tr><td>[redacted:aws_key_id]- [31m&lt;script&gt;.jsonl</td><td>6889df96…</td>…
```

The credential is redacted, the markup is escaped, and the ESC byte is gone
(R32's non-printable rule), leaving the harmless `[31m` as text.

### S13 — a credential-shaped string that is a legal tool name

Same run; the tool name is `AKIAIOSFODNN7EXAMPLE`, which satisfies R16's pattern
exactly:

```
<p class="metrics">first_seq&#x3D;1 · last_seq&#x3D;3 · occurrences&#x3D;2 · tool_name&#x3D;[redacted:aws_key_id]</p>
<td>[redacted:aws_key_id]</td>   (the spans table's tool column)
```

### AC6 — determinism, 48 environments plus six variants

Two files, one of them `hostile.jsonl`, both interpreters:

```
48 environments (2 interpreters x 4 PYTHONHASHSEED x 3 TZ x 2 LC_ALL) -> 1 distinct:
   html=cb81ed989a1fb828 json=2bfda29d75bb3d31 stdout=b082ba0d291fed8a

reversed path order   : True
different cwd (1)     : True
different cwd (2)     : True
different abs paths   : True
directory expansion   : True
two runs in one process byte-identical: True
```

`--no-previews` is likewise one hash across both interpreters, and differs from
the default — which is the arm that shows the flag does something.

A byte-identical `report.html` across CPython 3.11 and 3.12 for a trace full of
hostile Unicode is the strongest single result here, and it is the one S24 says
cannot be relied on for a *live* hostile trace.

### R47 at the byte level

```
ac5.html: non-6dp decimal tokens -> ['0.06']
h.html:   non-6dp decimal tokens -> ['0.00']
```

The only non-six-decimal decimal token in a rendered report is R29's mandated
two-decimal grand total. No `http:`, `https:`, `file:`, `@import`, `//`,
`<link>`, `<img>`, `<iframe>`, `<object>`, `<embed>`, `<form>`, `<base>` or HTML
comment appears in any rendered file. (My first scan also flagged `1.5` — the
`font: 14px/1.5` shorthand in the stylesheet. It was a constant and not a
computed value, so it did not violate R47, but a check written against R47's
sentence would trip on it; the fifth commit splits it into `font-size` and an
integer `line-height`.)

### R46 — no socket on the default path, with its control arm

```
R46: full analyze with sockets blocked -> exit 0
R46 CONTROL: socket construction raises -> the default analyze path constructed a socket
R47: two runs in one process byte-identical: True
```

Run under a `socket.socket` subclass that raises on construction **and** an
audit hook on `socket.connect`/`getaddrinfo`/`bind`. The control arm is the
second line: the guard is shown to fire when a socket really is constructed, so
the first line is not "nothing happened".

### AC5 — end to end

The whole corpus, `--fail-on critical`:

```
$ swarm-observer analyze <26 fixtures> --out all.html --json all.json --fail-on critical
wrote all.html, all.json; findings: critical=6 warning=3 info=2
exit=1

AC5 section order: ['header', 'findings', 'timeline', 'cost', 'spans', 'warnings']
AC5 matches R36 order: True
AC5 narrative section absent (R43): True
AC5 every model_call priced-or-unpriced: True | overlap: set()
AC5 by_agent sums to total: True   0.084810 == 0.084810
AC5 by_model sums to total: True   0.084810 == 0.084810
AC5 spans   sum  to total: True
```

and a curated six-file set, which is where **S28** comes from:

```
$ swarm-observer analyze loop_three_repeats retry_storm gaps_unexplained \
    outlier_above_critical_threshold unknown_tools_and_orphans hostile \
    --out ac5.html --json ac5.json --fail-on critical
wrote ac5.html, ac5.json; findings: critical=3 warning=4 info=1
exit=1

detectors firing: 5 of 7   missing: ['agent_loop', 'retry_storm']
HTML findings grouped severity-desc then slug then id: True
first three: [('critical','anomalous_span'), ('critical','blocked_agent'), ('critical','unresolved_tool_call')]
```

AC5's "a finding in every detector class" is not satisfiable by the corpus, and
the reason is structural rather than a missing fixture: merging files makes
same-named agents interleave in basename order, which destroys `agent_loop`'s
signature sequence and `retry_storm`'s window. See S28.

### R36's spans cap, exercised

A synthetic 5,200-span transcript:

```
wrote big.html, big.json; findings: critical=0 warning=0 info=0
spans in trace: 5200 | rows rendered: 5000
truncation line: '…and 200 more span(s). The JSON report carries every span; …'
html size: 2,429,172 bytes

5200-span report: attributes 66513 | outside allowlist: 0
allowlist sizes: {'class': 48, 'data-detector': 7, 'data-seq': 5200, 'data-severity': 4,
                  'href': 5206, 'id': 5206, 'x': 1000, 'y': 2, 'width': 2, 'height': 2, …}
```

### R37's edge cases

```
three instantaneous spans (trace_span_ms == 0):
  rects = [(x=0,y=5,w=1,h=12), (x=0,y=5,w=1,h=12), (x=0,y=5,w=1,h=12)]
  note  = 'the figure spans 0 ms of trace time'

empty trace : rects 0  lanes 0  height 0  | svg viewBox "0 0 1000 1"
round_half_up : 0.5 -> 1   1.5 -> 2   2.5 -> 3     (half-up, not Python's half-even)
round_half_up guards: (1, 0) raises   (-1, 2) raises
```

### R11 with two outputs — fail closed, write neither

```
$ echo "PRE-EXISTING-HTML" > r.html ; echo "PRE-EXISTING-JSON" > r.json
$ swarm-observer analyze bad.jsonl --out r.html --json r.json    # line 2 is `{"type":"user"`
swarm-observer: invalid_json: bad.jsonl line 2
exit=2
$ cat r.html ; cat r.json
PRE-EXISTING-HTML
PRE-EXISTING-JSON

$ swarm-observer analyze clean.jsonl --out same --json same
swarm-observer: error: --out and --json name the same path
exit=3
```

## Left for increment 5

- **The narrator** (R41–R43) and the `<section id="narrative">` anchor. It goes
  between the header and the findings section; `render_html`'s body is a list of
  line lists specifically so inserting one section is one `lines.extend` at a
  fixed index, and R43's byte-identity test then has a clean subject.
- **The replay seam** (`replay/target.py`), and R20's docs.
- **`--explain` loses its refusal.** `_DEFERRED_FLAGS` in `cli/main.py` is the
  one place to edit, and it becomes empty — at which point the `sorted(...)`
  walk over it and its W-M08 mutant become dead, so delete both deliberately
  rather than leaving a loop that can no longer run.
- **S24–S28 want PM rulings.** None blocks this branch. **S24** is the one with a
  determinism consequence and it is inherited from R8 rather than introduced
  here; **S26** should be settled before the R51 probe is written, because both
  readings of R35 look correct in isolation and only one of them is compatible
  with R51.
