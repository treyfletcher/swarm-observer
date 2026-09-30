## 🔴 Found in a browser: the collapse control deleted the heading, not the content

Someone opened the report in Chromium. **BUG-19:** the toggle marked
`event.currentTarget.parentNode` — `div.section-title` — not the `<section>`. With
`.collapsed > *:not(.section-title) { display: none }`, "hide" removed each section's
`<h2>` **and its own button**, hid nothing, and was unrecoverable without a reload. All
six sections. `querySelectorAll('[hidden], .hidden, [style*="display:none"]')` → **0**.

**Instance thirteen — and the first no test here could have caught.** Nothing in `tests/`
or CI executes JavaScript. R34's SHA-256 pin proved the bytes hadn't changed and said
nothing about whether they worked. It made the area *look* covered, which is worse than
an obvious gap.

### The ruling: a real browser, in CI, not behind a marker

`tests_browser/` is a second tree with its own CI job that installs Chromium and runs it
**in full, deselecting nothing**. A marker CI deselects would be instance three with a new
name. A separate tree, not a marker, because R45 pins that `tests/` passes on `.[dev]`
alone and R49 forbids `importorskip`/`skipif`. A Python DOM shim was rejected: it can't
cascade `:not()` or `getComputedStyle`, so it wouldn't have caught *this*.
`tests/test_browser_suite_wiring.py` (14 tests) fails offline if the job is removed,
emptied, or grows a `-m`. A static `closest(".section")` assertion is kept — and labelled
in the file as the constant-checks-a-constant it is.

**Red before green, shown:** `56abead` 19F/2P → 21P; `4eee41b` 2F/21P → 23P.

**BUG-20 (fixed):** 10 buttons had no `aria-pressed`/`aria-expanded`/`aria-label`. **SVG
axis/lane labels:** ruled intentional — trace-derived text is kept out of the `<svg>` on
purpose; a time axis is v1.1.

**Pins:** `SCRIPT_SHA256` moved twice (once per defect), both goldens regenerated.
`STYLE_SHA256` unmoved — the CSS was always right.

**3.11/3.12:** 2921 passed, 1 xfailed; browser 23 passed; ruff/mypy clean.

## ✅ Still merge. Still tag v1.

Full write-up: `docs/reviews/feature-so-i5.md` §13.
