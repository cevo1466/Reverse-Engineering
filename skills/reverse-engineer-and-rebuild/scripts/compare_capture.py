#!/usr/bin/env python3
"""Compare every aligned PNG step from two capture_web.py manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from pixel_diff import ImageError, compare_images
from report_safety import create_report_dir
from capture_web import ID_PATTERN


class ComparisonError(ValueError):
    """Capture manifests cannot be compared reliably."""


def read_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1 or not isinstance(data.get("steps"), list) or not data["steps"]:
        raise ComparisonError(f"{path}: invalid capture manifest")
    viewport = data.get("viewport")
    if not isinstance(viewport, dict) or any(type(viewport.get(axis)) is not int or not 1 <= viewport[axis] <= 4000 for axis in ("width", "height")):
        raise ComparisonError(f"{path}: invalid viewport")
    seen = set()
    for step in data["steps"]:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str) or not ID_PATTERN.fullmatch(step["id"]) or step["id"] in seen:
            raise ComparisonError(f"{path}: invalid or duplicate step ID")
        seen.add(step["id"])
        screenshot = step.get("screenshot")
        if not isinstance(screenshot, str) or Path(screenshot).name != screenshot or screenshot != step["id"] + ".png":
            raise ComparisonError(f"{path}: unsafe screenshot filename")
        if not isinstance(step.get("sha256"), str) or len(step["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in step["sha256"]):
            raise ComparisonError(f"{path}: missing screenshot digest")
        if not isinstance(step.get("url"), str) or urlsplit(step["url"]).scheme not in {"http", "https"} or not urlsplit(step["url"]).hostname:
            raise ComparisonError(f"{path}: invalid step URL")
    return data


def load_screenshot(manifest: Path, step: dict) -> bytes:
    screenshot = (manifest.parent / step["screenshot"]).read_bytes()
    if hashlib.sha256(screenshot).hexdigest() != step["sha256"]:
        raise ComparisonError(f"{manifest}: screenshot digest mismatch for {step['id']}")
    return screenshot


def compare_captures(reference_path: Path, rebuild_path: Path, output: Path, channel_threshold: int, max_changed_ratio: float) -> dict:
    reference = read_manifest(reference_path)
    rebuild = read_manifest(rebuild_path)
    reference_ids = [step["id"] for step in reference["steps"]]
    rebuild_ids = [step["id"] for step in rebuild["steps"]]
    if reference_ids != rebuild_ids:
        raise ComparisonError("Capture step IDs or order differ; use matching journeys")
    create_report_dir(output)
    rows = []
    for left, right in zip(reference["steps"], rebuild["steps"]):
        left_url = urlsplit(left["url"])
        right_url = urlsplit(right["url"])
        url_path_matches = (left_url.path or "/", left_url.query) == (right_url.path or "/", right_url.query)
        result, heatmap = compare_images(load_screenshot(reference_path, left), load_screenshot(rebuild_path, right), channel_threshold, max_changed_ratio)
        step_dir = output / left["id"]
        step_dir.mkdir()
        (step_dir / "pixel-diff.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if heatmap is not None:
            (step_dir / "pixel-diff.png").write_bytes(heatmap)
        viewport_matches = reference["viewport"] == rebuild["viewport"] and (result["reference"]["width"], result["reference"]["height"]) == (reference["viewport"]["width"], reference["viewport"]["height"]) and (result["rebuild"]["width"], result["rebuild"]["height"]) == (rebuild["viewport"]["width"], rebuild["viewport"]["height"])
        rows.append({"id": left["id"], "reference_url": left["url"], "rebuild_url": right["url"], "url_path_matches": url_path_matches, "viewport_matches": viewport_matches, "visual": result, "visual_status": "matched" if result["passes_threshold"] and viewport_matches else "different_or_incomplete"})
    overall = "matched_visual_observations" if all(row["visual_status"] == "matched" and row["url_path_matches"] for row in rows) else "different_or_incomplete"
    summary = {"schema_version": 1, "reference": str(reference_path), "rebuild": str(rebuild_path), "overall": overall, "scope": "Selected screenshot pixels and URL paths only; behavior and unseen states are not certified.", "steps": rows}
    (output / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Browser capture comparison", "", f"Overall: **{overall}**. Only saved screenshots, viewport dimensions, and URL paths are compared; review DOM, accessibility, and behavior separately.", "", "| Step | URL path | Viewport | Pixels | Changed ratio | Result |", "| --- | --- | --- | --- | ---: | --- |"]
    for row in rows:
        visual = row["visual"]
        ratio = "unknown" if visual["changed_ratio"] is None else f"{visual['changed_ratio']:.4%}"
        lines.append(f"| {row['id']} | {'same' if row['url_path_matches'] else 'different'} | {'same' if row['viewport_matches'] else 'different'} | {visual['status']} | {ratio} | {row['visual_status']} |")
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="reference capture manifest.json")
    parser.add_argument("rebuild", type=Path, help="rebuild capture manifest.json")
    parser.add_argument("--output", required=True, type=Path, help="new private comparison directory")
    parser.add_argument("--channel-threshold", type=int, default=0)
    parser.add_argument("--max-changed-ratio", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        summary = compare_captures(args.reference, args.rebuild, args.output, args.channel_threshold, args.max_changed_ratio)
    except (ComparisonError, ImageError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"compare_capture: {error}", file=sys.stderr)
        return 2
    print(args.output)
    return 0 if summary["overall"] == "matched_visual_observations" else 1


if __name__ == "__main__":
    sys.exit(main())
