#!/usr/bin/env python3
"""Capture scroll-driven and timed visual states of a page.

For each sample the page is scrolled to a fraction of its scrollable height
(optionally hovering an element first), left to settle, then captured as a PNG
together with geometry, opacity and transform of chosen probe selectors. Use
compare_scroll.py on a reference run and a rebuild run.

Uses a locally installed Chromium browser and Python's standard library.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
from typing import Any

from capture_web import CDP, ID_PATTERN, MAX_SCREENSHOT_BYTES, CaptureError, launch_browser, stop_browser, validate_url
from report_safety import create_report_dir


MAX_SAMPLES = 100
MAX_PROBES = 20

PROBE_SCRIPT = """(selectors) => selectors.map((selector) => {
  const element = document.querySelector(selector);
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return {x: rect.x, y: rect.y, width: rect.width, height: rect.height, opacity: parseFloat(style.opacity), transform: style.transform};
})"""


def read_spec(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise CaptureError(f"Non-JSON numeric constant: {value}")

    data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(data, dict) or set(data) - {"schema_version", "viewport", "probes", "samples"} or data.get("schema_version") != 1 or type(data.get("schema_version")) is not int:
        raise CaptureError("Spec must contain schema_version 1, viewport, samples and optional probes")
    viewport = data.get("viewport")
    if not isinstance(viewport, dict) or set(viewport) != {"width", "height"} or any(type(viewport[axis]) is not int or not 1 <= viewport[axis] <= 4000 for axis in viewport):
        raise CaptureError("viewport must contain integer width and height between 1 and 4000")
    probes = data.get("probes", [])
    if not isinstance(probes, list) or len(probes) > MAX_PROBES or any(not isinstance(item, str) or not item or len(item) > 500 for item in probes) or len(set(probes)) != len(probes):
        raise CaptureError(f"probes must be at most {MAX_PROBES} distinct nonempty CSS selectors")
    samples = data.get("samples")
    if not isinstance(samples, list) or not samples or len(samples) > MAX_SAMPLES:
        raise CaptureError(f"samples must contain 1 to {MAX_SAMPLES} entries")
    seen: set[str] = set()
    for index, sample in enumerate(samples):
        context = f"samples[{index}]"
        if not isinstance(sample, dict) or set(sample) - {"id", "scroll", "wait_ms", "hover"}:
            raise CaptureError(f"{context}: unsupported fields")
        if not isinstance(sample.get("id"), str) or not ID_PATTERN.fullmatch(sample["id"]) or sample["id"] in seen:
            raise CaptureError(f"{context}.id must be a unique safe filename")
        seen.add(sample["id"])
        scroll = sample.get("scroll", 0)
        if isinstance(scroll, bool) or not isinstance(scroll, (int, float)) or not 0 <= scroll <= 1:
            raise CaptureError(f"{context}.scroll must be a number from 0 to 1")
        wait = sample.get("wait_ms", 300)
        if type(wait) is not int or not 0 <= wait <= 10_000:
            raise CaptureError(f"{context}.wait_ms must be an integer from 0 to 10000")
        hover = sample.get("hover")
        if hover is not None and (not isinstance(hover, str) or not hover or len(hover) > 500):
            raise CaptureError(f"{context}.hover must be a nonempty CSS selector")
    return data


def scroll_to(cdp: CDP, fraction: float) -> dict[str, int]:
    expression = (f"(() => {{ const max = Math.max(0, document.documentElement.scrollHeight - innerHeight); "
                  f"window.scrollTo(0, Math.round({float(fraction)!r} * max)); "
                  f"return {{scroll_y: Math.round(scrollY), scroll_height: document.documentElement.scrollHeight}}; }})()")
    result = cdp.evaluate(expression)
    if not isinstance(result, dict):
        raise CaptureError("Could not scroll the page")
    return result


def hover_over(cdp: CDP, selector: str) -> None:
    box = cdp.evaluate(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null; const r = e.getBoundingClientRect(); return {{x: r.x + r.width / 2, y: r.y + r.height / 2}}; }})()")
    if not isinstance(box, dict):
        raise CaptureError(f"Could not find hover element: {selector}")
    cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": box["x"], "y": box["y"]})


def capture_scroll(url: str, spec: dict[str, Any], output: Path, browser: str, timeout: float) -> dict[str, Any]:
    validate_url(url)
    create_report_dir(output)
    width, height = spec["viewport"]["width"], spec["viewport"]["height"]
    with tempfile.TemporaryDirectory(prefix="scroll-capture-profile-") as profile:
        process = ws = None
        try:
            process, ws = launch_browser(browser, profile, timeout)
            cdp = CDP(ws)
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
            cdp.call("Page.navigate", {"url": url})
            deadline = time.monotonic() + timeout
            while cdp.evaluate("document.readyState") != "complete":
                if time.monotonic() > deadline:
                    raise CaptureError("Timed out waiting for document load")
                time.sleep(0.1)
            probes = spec.get("probes", [])
            records = []
            for sample in spec["samples"]:
                position = scroll_to(cdp, sample.get("scroll", 0))
                if sample.get("hover"):
                    hover_over(cdp, sample["hover"])
                time.sleep(sample.get("wait_ms", 300) / 1000)
                values = cdp.evaluate(f"({PROBE_SCRIPT})({json.dumps(probes)})") if probes else []
                png = base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})["data"], validate=True)
                if len(png) > MAX_SCREENSHOT_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise CaptureError(f"{sample['id']}: invalid or oversized PNG screenshot")
                if (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")) != (width, height):
                    raise CaptureError(f"{sample['id']}: screenshot dimensions do not match the viewport")
                (output / f"{sample['id']}.png").write_bytes(png)
                records.append({"id": sample["id"], "url": cdp.evaluate("location.href"), "screenshot": f"{sample['id']}.png", "sha256": hashlib.sha256(png).hexdigest(),
                                "width": width, "height": height, "scroll_y": position["scroll_y"], "scroll_height": position["scroll_height"],
                                "probes": dict(zip(probes, values))})
            manifest = {"schema_version": 1, "kind": "scroll_capture", "start_url": url, "viewport": spec["viewport"], "browser": Path(browser).name, "samples": records}
            (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return manifest
        finally:
            if ws:
                ws.close()
            if process:
                stop_browser(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--url", required=True, help="HTTP(S) URL to capture")
    parser.add_argument("--output", required=True, type=Path, help="new private report directory")
    parser.add_argument("--chrome", help="path to an installed Chrome or Chromium executable")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        spec = read_spec(args.spec)
        validate_url(args.url)
        if not 1 <= args.timeout_seconds <= 600:
            raise CaptureError("--timeout-seconds must be between 1 and 600")
        browser = args.chrome or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not browser or not Path(browser).is_file():
            raise CaptureError("An installed Chrome or Chromium executable is required")
        manifest = capture_scroll(args.url, spec, args.output, browser, args.timeout_seconds)
        print(json.dumps({"output": str(args.output), "samples": len(manifest["samples"])}))
        return 0
    except (CaptureError, OSError, ValueError, json.JSONDecodeError, KeyError, socket.timeout) as error:
        print(f"capture_scroll: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
