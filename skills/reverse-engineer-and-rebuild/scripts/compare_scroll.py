#!/usr/bin/env python3
"""Compare two capture_scroll.py manifests sample by sample.

A sample matches when its pixels pass the threshold, the scroll position and
page height agree, and every probe selector has the same geometry, opacity and
transform within the stated tolerances.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from capture_web import ID_PATTERN
from pixel_diff import ImageError, compare_images
from report_safety import create_report_dir


NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?")
GEOMETRY = ("x", "y", "width", "height")
SCOPE = "Selected scroll positions, screenshot pixels and probe values only; other positions, easing between samples and unseen interactions are not certified."


class ComparisonError(ValueError):
    """Scroll manifests cannot be compared reliably."""


def read_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ComparisonError(f"{path}: {error}") from error
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("kind") != "scroll_capture" or not isinstance(data.get("samples"), list) or not data["samples"]:
        raise ComparisonError(f"{path}: invalid scroll capture manifest")
    viewport = data.get("viewport")
    if not isinstance(viewport, dict) or any(type(viewport.get(axis)) is not int for axis in ("width", "height")):
        raise ComparisonError(f"{path}: invalid viewport")
    seen = set()
    for sample in data["samples"]:
        if not isinstance(sample, dict) or not isinstance(sample.get("id"), str) or not ID_PATTERN.fullmatch(sample["id"]) or sample["id"] in seen:
            raise ComparisonError(f"{path}: invalid or duplicate sample ID")
        seen.add(sample["id"])
        if sample.get("screenshot") != sample["id"] + ".png" or not isinstance(sample.get("sha256"), str) or len(sample["sha256"]) != 64:
            raise ComparisonError(f"{path}: invalid screenshot reference for {sample['id']}")
        if not isinstance(sample.get("probes"), dict) or type(sample.get("scroll_y")) is not int or type(sample.get("scroll_height")) is not int:
            raise ComparisonError(f"{path}: missing scroll data for {sample['id']}")
    return data


def load_screenshot(manifest: Path, sample: dict) -> bytes:
    data = (manifest.parent / sample["screenshot"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != sample["sha256"]:
        raise ComparisonError(f"{manifest}: screenshot digest mismatch for {sample['id']}")
    return data


def transform_numbers(value: Any) -> list[float] | None:
    if not isinstance(value, str) or value == "none":
        return None
    return [float(item) for item in NUMBER.findall(value.split("(", 1)[-1])]


def transforms_differ(left: Any, right: Any, pixel_tolerance: float, factor_tolerance: float) -> bool:
    """Compare CSS matrix values: translation parts in px, the other parts as unitless factors."""
    before, after = transform_numbers(left), transform_numbers(right)
    if before is None or after is None:
        return (before is None) != (after is None)
    if len(before) != len(after):
        return True
    translation = {4, 5} if len(before) == 6 else {12, 13, 14}
    return any(abs(a - b) > (pixel_tolerance if index in translation else factor_tolerance) for index, (a, b) in enumerate(zip(before, after)))


def number_differs(left: Any, right: Any, tolerance: float) -> bool:
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (left, right)):
        return left != right
    return abs(left - right) > tolerance


def compare_probes(reference: dict, rebuild: dict, pixel_tolerance: float, opacity_tolerance: float) -> list[str]:
    problems = []
    for selector in [*reference, *[key for key in rebuild if key not in reference]]:
        left, right = reference.get(selector), rebuild.get(selector)
        if left is None and right is None:
            continue
        if left is None or right is None:
            problems.append(f"{selector}: found only in the {'rebuild' if left is None else 'reference'}")
            continue
        for key in GEOMETRY:
            if number_differs(left.get(key), right.get(key), pixel_tolerance):
                problems.append(f"{selector}: {key} {left.get(key)} vs {right.get(key)}")
        if number_differs(left.get("opacity"), right.get("opacity"), opacity_tolerance):
            problems.append(f"{selector}: opacity {left.get('opacity')} vs {right.get('opacity')}")
        if transforms_differ(left.get("transform"), right.get("transform"), pixel_tolerance, opacity_tolerance):
            problems.append(f"{selector}: transform {left.get('transform')} vs {right.get('transform')}")
    return problems


def compare_scroll(reference_path: Path, rebuild_path: Path, output: Path, channel_threshold: int, max_changed_ratio: float,
                   pixel_tolerance: float, opacity_tolerance: float) -> dict:
    reference, rebuild = read_manifest(reference_path), read_manifest(rebuild_path)
    if [item["id"] for item in reference["samples"]] != [item["id"] for item in rebuild["samples"]]:
        raise ComparisonError("Sample IDs or order differ; use the same spec for both captures")
    create_report_dir(output)
    rows = []
    for left, right in zip(reference["samples"], rebuild["samples"]):
        result, heatmap = compare_images(load_screenshot(reference_path, left), load_screenshot(rebuild_path, right), channel_threshold, max_changed_ratio)
        sample_dir = output / left["id"]
        sample_dir.mkdir()
        (sample_dir / "pixel-diff.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if heatmap is not None:
            (sample_dir / "pixel-diff.png").write_bytes(heatmap)
        problems = []
        if reference["viewport"] != rebuild["viewport"]:
            problems.append("viewport differs")
        if not result["passes_threshold"]:
            problems.append(f"pixels: {result['status']}")
        if number_differs(left["scroll_y"], right["scroll_y"], pixel_tolerance):
            problems.append(f"scroll_y {left['scroll_y']} vs {right['scroll_y']}")
        if number_differs(left["scroll_height"], right["scroll_height"], pixel_tolerance):
            problems.append(f"scroll_height {left['scroll_height']} vs {right['scroll_height']}")
        problems += compare_probes(left["probes"], right["probes"], pixel_tolerance, opacity_tolerance)
        rows.append({"id": left["id"], "visual": result, "problems": problems, "status": "different" if problems else "matched",
                     "reason": "; ".join(problems) or "pixels, scroll position and probes match"})
    overall = "matched" if all(row["status"] == "matched" for row in rows) else "different"
    summary = {"schema_version": 1, "kind": "scroll_comparison", "reference": str(reference_path), "rebuild": str(rebuild_path), "overall": overall,
               "pixel_tolerance": pixel_tolerance, "opacity_tolerance": opacity_tolerance, "scope": SCOPE, "steps": rows}
    (output / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Scroll comparison", "", f"Overall: **{overall}**. {SCOPE}", "", "| Sample | Result | Detail |", "| --- | --- | --- |"]
    lines += [f"| {row['id']} | {row['status']} | {row['reason'].replace('|', chr(92) + '|')} |" for row in rows]
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="reference manifest.json")
    parser.add_argument("rebuild", type=Path, help="rebuild manifest.json")
    parser.add_argument("--output", required=True, type=Path, help="new private comparison directory")
    parser.add_argument("--channel-threshold", type=int, default=0)
    parser.add_argument("--max-changed-ratio", type=float, default=0.0)
    parser.add_argument("--pixel-tolerance", type=float, default=1.0, help="allowed difference in px for geometry and scroll values")
    parser.add_argument("--opacity-tolerance", type=float, default=0.02, help="allowed difference in opacity and transform matrix factors")
    args = parser.parse_args(argv)
    try:
        summary = compare_scroll(args.reference, args.rebuild, args.output, args.channel_threshold, args.max_changed_ratio, args.pixel_tolerance, args.opacity_tolerance)
    except (ComparisonError, ImageError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"compare_scroll: {error}", file=sys.stderr)
        return 2
    print(args.output)
    return 0 if summary["overall"] == "matched" else 1


if __name__ == "__main__":
    sys.exit(main())
