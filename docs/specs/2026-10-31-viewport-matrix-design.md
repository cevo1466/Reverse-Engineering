# Viewport matrix (`viewport_matrix.py`)

Status: sub-project 2 of 4. Builds on `verdict.py` (sub-project 1).

## Problem

`capture_web.py` records one viewport per run. Checking a responsive page at phone, tablet and desktop widths means running the capture and comparison by hand several times and merging the results.

## Design

`viewport_matrix.py SCENARIO --reference-url URL --rebuild-url URL --output DIR [--viewports 375x812,768x1024,1440x900] [--rebuild-scenario FILE]`

For each viewport it replaces the scenario viewport, captures the reference and the rebuild with `capture_web.capture`, compares them with `compare_capture.compare_captures`, and writes one combined verdict.

- Output layout: `reference/<WxH>/`, `rebuild/<WxH>/`, `comparison/<WxH>/`, `verdict.json`, `verdict.md`.
- Case IDs are `<WxH>/<step id>`; evidence points at that viewport's `comparison.json`.
- A capture or comparison failure at one viewport becomes an `unknown` case (`<WxH>/reference-capture`, `rebuild-capture` or `comparison`) and the other viewports still run.
- Exit codes follow `verdict.py`: 0 matched, 1 different, 2 unknown or error.
- Remote interaction needs `--allow-remote-actions`, the same gate as `capture_web.py`.
- Default viewports: 375x812, 768x1024, 1440x900. Each side is limited to 1 to 4000, the limit `capture_web.py` already enforces.

## Changes to existing code

`verdict.py` gains `summarise(cases)` and `write_verdict_files(output, verdict)` so the matrix can reuse them, and the Markdown report now prints the verdict's own `scope` text. Behaviour for existing callers is unchanged.

## Tests

`tests/test_viewport_matrix.py` replaces the browser with a fake capture function that writes solid-colour PNGs, so no Chrome is needed. It covers viewport parsing, matching and differing pages, per-viewport failure isolation, the scope text and the command-line error paths.
