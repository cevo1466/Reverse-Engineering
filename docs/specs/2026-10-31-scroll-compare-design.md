# Scroll and animation comparison (`capture_scroll.py`, `compare_scroll.py`)

Status: sub-project 3 of 4.

## Problem

`capture_web.py` records the state of a page after each user action. It cannot show whether a scroll-driven effect (parallax, pinned sections, fade-ins, GSAP ScrollTrigger scrubs, Lenis smooth scrolling) or a hover animation behaves the same in the rebuild, because it never moves the scroll position or samples element geometry.

## Design

`capture_scroll.py SPEC --url URL --output DIR` drives Chromium through the same debugger connection that `capture_web.py` uses (its `launch_browser`, `CDP` and `stop_browser` are imported, not copied).

Spec file:

```json
{"schema_version": 1, "viewport": {"width": 1280, "height": 720},
 "probes": [".hero", ".nav"],
 "samples": [{"id": "top"}, {"id": "quarter", "scroll": 0.25, "wait_ms": 400}, {"id": "cta-hover", "scroll": 0.6, "hover": ".cta"}]}
```

- `scroll` is a fraction (0 to 1) of the scrollable height, so the same spec works for pages of different length. Default 0.
- `wait_ms` (0 to 10000, default 300) lets animations settle after scrolling and hovering.
- `hover` is an optional CSS selector; a mouse move to its centre is sent before the wait.
- `probes` (at most 20 selectors) record, for the first match, the viewport rectangle, computed opacity and computed transform. A missing element is recorded as `null`.
- Output: one PNG per sample and `manifest.json` (`kind: scroll_capture`) with SHA-256 digests, the real scroll position and the document height.

`compare_scroll.py REFERENCE_MANIFEST REBUILD_MANIFEST --output DIR` compares sample by sample:

- pixels with `pixel_diff.compare_images` (`--channel-threshold`, `--max-changed-ratio`);
- scroll position and document height within `--pixel-tolerance` (default 1 px);
- probe rectangles within `--pixel-tolerance`, opacity within `--opacity-tolerance` (default 0.02);
- transform matrices number by number: translation parts in px, other parts as factors.

A sample is `matched` only when none of these differ. The output `comparison.json` has `kind: scroll_comparison`, `overall`, and one row per sample with `status` and a readable `reason`. Exit codes: 0 matched, 1 different, 2 error (including mismatched sample IDs or a tampered screenshot).

`verdict.py` recognises `scroll_comparison` files and emits `scroll:<sample id>` cases.

## Limits

Samples capture settled states, not the motion between them. Easing curves, frame timing and scroll-hijacking libraries that need real wheel input are outside what a scripted `scrollTo` can show; the report scope says so.

## Tests

`tests/test_scroll.py` covers spec validation, probe and transform comparison, comparison of generated manifests (match, pixel/probe/height differences, order mismatch, tampering) and `verdict.py` integration, all without a browser. A manual run against a scroll-linked test page with real Chromium matched for two identical captures and reported differences for a page with half the scroll speed.
