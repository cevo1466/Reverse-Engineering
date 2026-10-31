#!/usr/bin/env python3
"""Run one browser journey at several viewports and compare reference with rebuild.

For every viewport the script captures the reference and the rebuild with
capture_web.py, compares them with compare_capture.py, and writes one combined
verdict (verdict.json and verdict.md) whose case IDs are prefixed by viewport.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Callable

from capture_web import CaptureError, capture, read_scenario, validate_url
from compare_capture import ComparisonError, compare_captures
from pixel_diff import ImageError
from report_safety import create_report_dir
from verdict import SCOPE, VerdictError, cases_from, summarise, write_verdict_files


DEFAULT_VIEWPORTS = "375x812,768x1024,1440x900"
VIEWPORT_PATTERN = re.compile(r"^([1-9][0-9]{0,3})x([1-9][0-9]{0,3})$")


def parse_viewports(text: str) -> list[tuple[str, int, int]]:
    """Parse '375x812,1440x900' into [(name, width, height)]; reject bad or repeated sizes."""
    result = []
    seen = set()
    for part in text.split(","):
        match = VIEWPORT_PATTERN.fullmatch(part.strip())
        if not match or not all(1 <= int(value) <= 4000 for value in match.groups()):
            raise ValueError(f"viewport must look like 375x812 (each side 1 to 4000): {part!r}")
        name = f"{match.group(1)}x{match.group(2)}"
        if name in seen:
            raise ValueError(f"repeated viewport {name}")
        seen.add(name)
        result.append((name, int(match.group(1)), int(match.group(2))))
    if not result:
        raise ValueError("at least one viewport is required")
    return result


def with_viewport(scenario: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    changed = copy.deepcopy(scenario)
    changed["viewport"] = {"width": width, "height": height}
    return changed


def error_case(name: str, stage: str, error: Exception) -> dict:
    return {"case_id": f"{name}/{stage}", "kind": "capture", "status": "unknown", "reason": f"{stage} failed: {error}", "evidence": "."}


def run_matrix(reference_url: str, rebuild_url: str, reference_scenario: dict[str, Any], rebuild_scenario: dict[str, Any],
               viewports: list[tuple[str, int, int]], output: Path, browser: str, timeout: float, allow_remote_actions: bool,
               channel_threshold: int, max_changed_ratio: float, capture_fn: Callable[..., dict] = capture) -> dict:
    create_report_dir(output)
    cases: list[dict] = []
    for name, width, height in viewports:
        reference_dir, rebuild_dir, comparison_dir = (output / part / name for part in ("reference", "rebuild", "comparison"))
        try:
            capture_fn(reference_url, with_viewport(reference_scenario, width, height), reference_dir, browser, timeout, allow_remote_actions)
        except (CaptureError, OSError, ValueError) as error:
            cases.append(error_case(name, "reference-capture", error))
            continue
        try:
            capture_fn(rebuild_url, with_viewport(rebuild_scenario, width, height), rebuild_dir, browser, timeout, allow_remote_actions)
        except (CaptureError, OSError, ValueError) as error:
            cases.append(error_case(name, "rebuild-capture", error))
            continue
        try:
            summary = compare_captures(reference_dir / "manifest.json", rebuild_dir / "manifest.json", comparison_dir, channel_threshold, max_changed_ratio)
        except (ComparisonError, ImageError, OSError, ValueError, json.JSONDecodeError) as error:
            cases.append(error_case(name, "comparison", error))
            continue
        for case in cases_from("capture", summary, comparison_dir / "comparison.json"):
            case["case_id"] = f"{name}/{case['case_id'].split(':', 1)[1]}"
            case["evidence"] = f"comparison/{name}/comparison.json"
            cases.append(case)
    verdict = summarise(cases)
    verdict["viewports"] = [name for name, _, _ in viewports]
    verdict["scope"] = SCOPE + " Only the listed viewports were compared."
    write_verdict_files(output, verdict)
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="capture_web.py scenario for the reference (its viewport is replaced per run)")
    parser.add_argument("--reference-url", required=True)
    parser.add_argument("--rebuild-url", required=True)
    parser.add_argument("--rebuild-scenario", type=Path, help="scenario for the rebuild when selectors differ; defaults to the reference scenario")
    parser.add_argument("--viewports", default=DEFAULT_VIEWPORTS, help=f"comma-separated WIDTHxHEIGHT list (default {DEFAULT_VIEWPORTS})")
    parser.add_argument("--output", required=True, type=Path, help="new private report directory")
    parser.add_argument("--chrome", help="path to an installed Chrome or Chromium executable")
    parser.add_argument("--allow-remote-actions", action="store_true", help="permit interaction steps on an authorized remote test target")
    parser.add_argument("--channel-threshold", type=int, default=0)
    parser.add_argument("--max-changed-ratio", type=float, default=0.0)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        viewports = parse_viewports(args.viewports)
        reference_scenario = read_scenario(args.scenario)
        rebuild_scenario = read_scenario(args.rebuild_scenario) if args.rebuild_scenario else reference_scenario
        reference_local = validate_url(args.reference_url)
        validate_url(args.rebuild_url)
        interactive = any(item["type"] != "wait_for" for scenario in (reference_scenario, rebuild_scenario) for step in scenario["steps"] for item in step.get("actions", []))
        if interactive and not reference_local and not args.allow_remote_actions:
            raise CaptureError("Remote interaction requires --allow-remote-actions on an authorized test target")
        if not 1 <= args.timeout_seconds <= 600:
            raise CaptureError("--timeout-seconds must be between 1 and 600")
        browser = args.chrome or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not browser or not Path(browser).is_file():
            raise CaptureError("An installed Chrome or Chromium executable is required")
        if args.output.exists():
            raise CaptureError(f"{args.output}: output directory already exists")
        verdict = run_matrix(args.reference_url, args.rebuild_url, reference_scenario, rebuild_scenario, viewports, args.output, browser,
                             args.timeout_seconds, args.allow_remote_actions, args.channel_threshold, args.max_changed_ratio)
    except (CaptureError, VerdictError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"viewport_matrix: {error}", file=sys.stderr)
        return 2
    print(args.output)
    return {"matched": 0, "different": 1, "unknown": 2}[verdict["overall"]]


if __name__ == "__main__":
    sys.exit(main())
