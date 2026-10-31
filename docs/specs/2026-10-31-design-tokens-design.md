# Design token extraction (`extract_tokens.py`)

Status: sub-project 4 of 4.

## Problem

When rebuilding a page the agent reads colours, fonts and spacing by eye or from raw CSS. That is slow and error-prone, and the computed values (after cascade, variables and media queries) are what the browser actually draws.

## Design

`extract_tokens.py --url URL --output DIR [--viewport 1280x720] [--max-elements 5000] [--top 20]`

It loads the page in Chromium (reusing `launch_browser`, `CDP` and `stop_browser` from `capture_web.py`), reads the computed style of every visible element, and counts how often each value is used.

- Colours: text (only elements with their own text), background (transparent skipped), border (only when a border is drawn). Normalised to `#rrggbb` or `#rrggbbaa`; formats not understood (for example `oklch(...)`) are kept as written.
- Typography: first font family, full stack, size, weight, line height, letter spacing, for elements with their own text.
- Spacing: positive margin, padding and flex/grid gap values in px.
- Border radius (top-left corner) and box shadows, ignoring `0px` and `none`.
- CSS custom properties declared on `:root` or `html` in readable stylesheets, plus a count of stylesheets that could not be read (cross-origin).
- Each group is ranked by use and cut to `--top` values.

Output: `tokens.json` (machine-readable) and `tokens.md` (tables). The report directory is created through `report_safety.create_report_dir`.

## Limits

Only visible elements in the loaded state at one viewport are counted; hover, focus, dark mode and other viewports need separate runs. `--max-elements` bounds the work and the report says when it truncated. Counts show usage, not intent: a value used once may still be a brand colour. Treat the result as a ranked starting point for the rebuild.

## Tests

`tests/test_extract_tokens.py` tests colour normalisation, pixel parsing, ranking, aggregation on a fixed element list, Markdown output and command-line errors without a browser. A manual run with real Chromium on a test page produced the expected colours, sizes, radius and shadow.
