#!/usr/bin/env python3
"""Extract design tokens (colours, typography, spacing, radii, shadows) from a live page.

Loads the page in Chromium at one viewport, reads the computed style of every
visible element and counts how often each value is used. The result is a
ranked starting point for a rebuild, not a complete design system: only
visible elements in the loaded state are seen.

Uses a locally installed Chromium browser and Python's standard library.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import time
from typing import Any

from capture_web import CDP, CaptureError, launch_browser, stop_browser, validate_url
from report_safety import create_report_dir


DEFAULT_VIEWPORT = "1280x720"
VIEWPORT_PATTERN = re.compile(r"^([1-9][0-9]{0,3})x([1-9][0-9]{0,3})$")
RGB = re.compile(r"^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$")
SRGB = re.compile(r"^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*/\s*([\d.]+%?))?\s*\)$")
PIXELS = re.compile(r"^(-?\d+(?:\.\d+)?)px$")

COLLECT_SCRIPT = """(maxElements) => {
  const out = []; let total = 0, truncated = false;
  for (const e of document.querySelectorAll('body, body *')) {
    if (!e.getClientRects().length) continue;
    const s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.display === 'none') continue;
    total++;
    if (out.length >= maxElements) { truncated = true; continue; }
    let text = false;
    for (const n of e.childNodes) { if (n.nodeType === 3 && n.textContent.trim()) { text = true; break; } }
    const item = {backgroundColor: s.backgroundColor, borderRadius: s.borderTopLeftRadius, boxShadow: s.boxShadow,
      margin: [s.marginTop, s.marginRight, s.marginBottom, s.marginLeft], padding: [s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft],
      gap: /flex|grid/.test(s.display) ? [s.rowGap, s.columnGap] : []};
    if (text) Object.assign(item, {color: s.color, fontFamily: s.fontFamily, fontSize: s.fontSize, fontWeight: s.fontWeight, lineHeight: s.lineHeight, letterSpacing: s.letterSpacing});
    if (parseFloat(s.borderTopWidth) > 0 && s.borderTopStyle !== 'none') item.borderColor = s.borderTopColor;
    out.push(item);
  }
  const variables = {}; let unreadable = 0;
  for (const sheet of document.styleSheets) {
    let rules; try { rules = sheet.cssRules; } catch (error) { unreadable++; continue; }
    for (const rule of rules) {
      if (rule.style && rule.selectorText && /(^|,)\\s*(:root|html)\\s*(,|$)/.test(rule.selectorText)) {
        for (const name of rule.style) { if (name.startsWith('--') && !(name in variables)) variables[name] = rule.style.getPropertyValue(name).trim(); }
      }
    }
  }
  return {elements: out, variables, unreadable, total, truncated};
}"""


def parse_viewport(text: str) -> dict[str, int]:
    match = VIEWPORT_PATTERN.fullmatch(text.strip())
    if not match or not all(1 <= int(value) <= 4000 for value in match.groups()):
        raise ValueError(f"viewport must look like 1280x720 (each side 1 to 4000): {text!r}")
    return {"width": int(match.group(1)), "height": int(match.group(2))}


def _channel(value: str) -> int:
    return max(0, min(255, round(float(value))))


def normalise_color(text: Any) -> str | None:
    """Return #rrggbb or #rrggbbaa; None for transparent or empty; the raw text for formats not understood."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip().lower()
    if text == "transparent":
        return None
    match = RGB.match(text)
    scale = 1
    if not match:
        match = SRGB.match(text)
        scale = 255
    if not match:
        return text
    red, green, blue = (_channel(str(float(match.group(index)) * scale)) for index in (1, 2, 3))
    alpha_text = match.group(4)
    alpha = 1.0 if alpha_text is None else float(alpha_text[:-1]) / 100 if alpha_text.endswith("%") else float(alpha_text)
    if alpha <= 0:
        return None
    result = f"#{red:02x}{green:02x}{blue:02x}"
    return result if alpha >= 1 else result + f"{round(alpha * 255):02x}"


def pixels(text: Any) -> float | None:
    match = PIXELS.match(text.strip()) if isinstance(text, str) else None
    return float(match.group(1)) if match else None


def number_text(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def rank(counter: Counter, limit: int) -> list[dict]:
    items = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return [{"value": value, "count": count} for value, count in items[:limit]]


def first_family(stack: str) -> str:
    return stack.split(",", 1)[0].strip().strip("\"'")


def aggregate(raw: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    """Count style values over the collected elements and rank them by use."""
    colors = {"text": Counter(), "background": Counter(), "border": Counter()}
    families, stacks, sizes, weights, heights, tracking = (Counter() for _ in range(6))
    spacing, radii, shadows = Counter(), Counter(), Counter()
    for item in raw.get("elements", []):
        for key, bucket in (("color", "text"), ("backgroundColor", "background"), ("borderColor", "border")):
            value = normalise_color(item.get(key))
            if value:
                colors[bucket][value] += 1
        if isinstance(item.get("fontFamily"), str):
            families[first_family(item["fontFamily"])] += 1
            stacks[item["fontFamily"]] += 1
            size = pixels(item.get("fontSize"))
            if size:
                sizes[number_text(size) + "px"] += 1
            if isinstance(item.get("fontWeight"), str):
                weights[item["fontWeight"]] += 1
            line = pixels(item.get("lineHeight"))
            heights[number_text(line) + "px" if line else str(item.get("lineHeight"))] += 1
            spacing_letter = pixels(item.get("letterSpacing"))
            tracking[number_text(spacing_letter) + "px" if spacing_letter else "normal"] += 1
        for key in ("margin", "padding", "gap"):
            for value in item.get(key, []):
                amount = pixels(value)
                if amount and amount > 0:
                    spacing[number_text(amount) + "px"] += 1
        radius = item.get("borderRadius")
        if isinstance(radius, str) and radius.strip() not in {"", "0px"}:
            radii[radius.strip()] += 1
        shadow = item.get("boxShadow")
        if isinstance(shadow, str) and shadow.strip() not in {"", "none"}:
            shadows[shadow.strip()] += 1
    return {
        "schema_version": 1,
        "elements_read": len(raw.get("elements", [])),
        "visible_elements": raw.get("total", len(raw.get("elements", []))),
        "truncated": bool(raw.get("truncated")),
        "unreadable_stylesheets": raw.get("unreadable", 0),
        "colors": {name: rank(counter, limit) for name, counter in colors.items()},
        "typography": {"families": rank(families, limit), "stacks": rank(stacks, limit), "sizes": rank(sizes, limit), "weights": rank(weights, limit),
                       "line_heights": rank(heights, limit), "letter_spacing": rank(tracking, limit)},
        "spacing": rank(spacing, limit),
        "radii": rank(radii, limit),
        "shadows": rank(shadows, limit),
        "css_variables": dict(sorted(raw.get("variables", {}).items())),
    }


def markdown(tokens: dict[str, Any], url: str, viewport: dict[str, int]) -> str:
    def table(title: str, rows: list[dict]) -> list[str]:
        if not rows:
            return []
        return [f"### {title}", "", "| Value | Uses |", "| --- | ---: |", *[f"| `{str(row['value']).replace('|', chr(92) + '|')}` | {row['count']} |" for row in rows], ""]

    lines = ["# Design tokens", "", f"Source: {url} at {viewport['width']}x{viewport['height']}. {tokens['elements_read']} of {tokens['visible_elements']} visible elements read"
             + (" (truncated)." if tokens["truncated"] else ".") + f" Unreadable stylesheets: {tokens['unreadable_stylesheets']}.",
             "Only visible elements in the loaded state are counted; hover, focus and other states are not.", ""]
    for name, rows in tokens["colors"].items():
        lines += table(f"Colour: {name}", rows)
    for name, rows in tokens["typography"].items():
        lines += table(f"Typography: {name.replace('_', ' ')}", rows)
    lines += table("Spacing", tokens["spacing"]) + table("Border radius", tokens["radii"]) + table("Shadows", tokens["shadows"])
    if tokens["css_variables"]:
        lines += ["### CSS variables on :root", "", "| Name | Value |", "| --- | --- |", *[f"| `{name}` | `{value.replace('|', chr(92) + '|')}` |" for name, value in tokens["css_variables"].items()], ""]
    return "\n".join(lines)


def extract(url: str, viewport: dict[str, int], output: Path, browser: str, timeout: float, max_elements: int, limit: int) -> dict[str, Any]:
    validate_url(url)
    create_report_dir(output)
    with tempfile.TemporaryDirectory(prefix="token-profile-") as profile:
        process = ws = None
        try:
            process, ws = launch_browser(browser, profile, timeout)
            cdp = CDP(ws)
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": viewport["width"], "height": viewport["height"], "deviceScaleFactor": 1, "mobile": False})
            cdp.call("Page.navigate", {"url": url})
            deadline = time.monotonic() + timeout
            while cdp.evaluate("document.readyState") != "complete":
                if time.monotonic() > deadline:
                    raise CaptureError("Timed out waiting for document load")
                time.sleep(0.1)
            raw = cdp.evaluate(f"({COLLECT_SCRIPT})({int(max_elements)})")
            if not isinstance(raw, dict) or not isinstance(raw.get("elements"), list):
                raise CaptureError("Could not read computed styles")
        finally:
            if ws:
                ws.close()
            if process:
                stop_browser(process)
    tokens = aggregate(raw, limit)
    tokens.update(source_url=url, viewport=viewport)
    (output / "tokens.json").write_text(json.dumps(tokens, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "tokens.md").write_text(markdown(tokens, url, viewport), encoding="utf-8")
    return tokens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="HTTP(S) URL to read")
    parser.add_argument("--output", required=True, type=Path, help="new private report directory")
    parser.add_argument("--viewport", default=DEFAULT_VIEWPORT, help=f"WIDTHxHEIGHT (default {DEFAULT_VIEWPORT})")
    parser.add_argument("--chrome", help="path to an installed Chrome or Chromium executable")
    parser.add_argument("--max-elements", type=int, default=5000, help="read at most this many visible elements (1 to 50000)")
    parser.add_argument("--top", type=int, default=20, help="keep the N most used values per group (1 to 200)")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        viewport = parse_viewport(args.viewport)
        validate_url(args.url)
        if not 1 <= args.max_elements <= 50_000 or not 1 <= args.top <= 200 or not 1 <= args.timeout_seconds <= 600:
            raise CaptureError("--max-elements, --top or --timeout-seconds is out of range")
        browser = args.chrome or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not browser or not Path(browser).is_file():
            raise CaptureError("An installed Chrome or Chromium executable is required")
        tokens = extract(args.url, viewport, args.output, browser, args.timeout_seconds, args.max_elements, args.top)
        print(json.dumps({"output": str(args.output), "elements_read": tokens["elements_read"]}))
        return 0
    except (CaptureError, OSError, ValueError, json.JSONDecodeError, KeyError, socket.timeout) as error:
        print(f"extract_tokens: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
