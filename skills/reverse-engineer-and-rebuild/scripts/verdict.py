#!/usr/bin/env python3
"""Combine comparison results into one matched / different / unknown verdict.

Reads result files written by pixel_diff.py, compare_capture.py,
compare_web.py and compare_http.py. It never re-runs a comparison and never
makes network requests; thresholds stay in the helpers that produced the files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from report_safety import create_report_dir


RESULT_NAMES = ("pixel-diff.json", "comparison.json", "visual-summary.json")
WEB_STATUSES = {"within_threshold", "different", "unknown"}
MATCHED = {"identical", "within_tolerance", "within_threshold", "matched", "matched_observations", "matched_visual_observations"}
DIFFERENT = {"different", "dimension_mismatch", "different_or_incomplete"}
SCOPE = "Matched means only the selected observations matched. Unseen states, viewports and behavior are not certified."


class VerdictError(ValueError):
    """Result files cannot be combined reliably."""


def normalise_status(value: Any) -> str:
    """Map a helper status string to matched, different or unknown."""
    if isinstance(value, str):
        if value in MATCHED:
            return "matched"
        if value in DIFFERENT:
            return "different"
    return "unknown"


def threshold_status(passes: Any) -> str:
    """Map a recorded passes_threshold value to matched, different or unknown."""
    if passes is True:
        return "matched"
    if passes is False:
        return "different"
    return "unknown"


def identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerdictError(f"{where}: missing or empty ID")
    return value


def count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def detect_kind(data: dict, source: Path) -> str:
    """Return pixel, capture, web or http; refuse files that match none or several."""
    steps = data.get("steps")
    kinds = []
    if isinstance(data.get("cases"), list) and isinstance(data.get("summary"), dict):
        kinds.append("http")
    if isinstance(data.get("overall"), str) and isinstance(steps, list) and all(isinstance(row, dict) and isinstance(row.get("visual"), dict) for row in steps):
        kinds.append("capture")
    if data.get("status") in WEB_STATUSES and isinstance(steps, list):
        kinds.append("web")
    if "passes_threshold" in data and "changed_ratio" in data:
        kinds.append("pixel")
    if len(kinds) != 1:
        raise VerdictError(f"{source}: not a recognised comparison result" if not kinds else f"{source}: ambiguous result shape ({', '.join(kinds)})")
    return kinds[0]


def cases_from(kind: str, data: dict, source: Path) -> list[dict]:
    """Turn one result file into normalised cases (status, reason, no evidence path yet)."""
    rows = []
    if kind == "pixel":
        ratio = data.get("changed_ratio")
        rows.append(("pixel:" + source.parent.name, threshold_status(data.get("passes_threshold")), f"pixel status: {data.get('status')}, changed_ratio: {ratio}"))
    elif kind == "capture":
        for row in data["steps"]:
            problems = []
            if row.get("visual_status") != "matched":
                problems.append(f"pixels: {row['visual'].get('status')}")
            if row.get("url_path_matches") is not True:
                problems.append("url_path_matches is not true")
            if row.get("viewport_matches") is not True:
                problems.append("viewport_matches is not true")
            rows.append(("capture:" + identifier(row.get("id"), f"{source}: capture step"), "different" if problems else "matched", "; ".join(problems) or "pixels, URL path and viewport match"))
    elif kind == "web":
        for row in data["steps"]:
            if not isinstance(row, dict):
                raise VerdictError(f"{source}: web step must be an object")
            rows.append(("web:" + identifier(row.get("step_id"), f"{source}: web step"), threshold_status(row.get("passes_threshold")), str(row.get("reason") or f"pixel status: {row.get('status')}")))
    else:
        for row in data["cases"]:
            if not isinstance(row, dict):
                raise VerdictError(f"{source}: HTTP case must be an object")
            reason = f"{count(row.get('differences'))} difference(s), {count(row.get('unknowns'))} unknown(s)"
            rows.append(("http:" + identifier(row.get("id"), f"{source}: HTTP case"), normalise_status(row.get("status")), reason))
    return [{"case_id": case_id, "kind": kind, "status": status, "reason": reason} for case_id, status, reason in rows]


def read_result(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerdictError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise VerdictError(f"{path}: result must be a JSON object")
    return data


def expand(paths: list[Path]) -> list[Path]:
    """Replace each directory with the known result files directly inside it."""
    files = []
    for given in paths:
        path = given.expanduser()
        if path.is_dir():
            found = [path / name for name in RESULT_NAMES if (path / name).is_file()]
            if not found:
                raise VerdictError(f"{path}: no result file ({', '.join(RESULT_NAMES)})")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise VerdictError(f"{path}: not found")
    return files


def build_verdict(files: list[Path], output: Path) -> dict:
    cases = []
    seen = set()
    for path in files:
        source = path.resolve()
        data = read_result(source)
        for case in cases_from(detect_kind(data, source), data, source):
            if case["case_id"] in seen:
                raise VerdictError(f"duplicate case ID {case['case_id']}")
            seen.add(case["case_id"])
            case["evidence"] = os.path.relpath(source, output)
            cases.append(case)
    return summarise(cases)


def summarise(cases: list[dict]) -> dict:
    """Build the verdict document for already-normalised cases."""
    totals = {name: sum(case["status"] == name for case in cases) for name in ("matched", "different", "unknown")}
    overall = "different" if totals["different"] else "unknown" if totals["unknown"] or not cases else "matched"
    return {"schema_version": 1, "overall": overall, "totals": totals, "scope": SCOPE, "cases": cases}


def cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_report(output: Path, verdict: dict) -> None:
    create_report_dir(output)
    write_verdict_files(output, verdict)


def write_verdict_files(output: Path, verdict: dict) -> None:
    """Write verdict.json and verdict.md into an existing report directory."""
    (output / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Verdict", "", f"Overall: **{verdict['overall']}**. {verdict['scope']}", "", "| Case | Kind | Status | Reason | Evidence |", "| --- | --- | --- | --- | --- |"]
    for case in verdict["cases"]:
        lines.append("| " + " | ".join(cell(case[key]) for key in ("case_id", "kind", "status", "reason", "evidence")) + " |")
    (output / "verdict.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path, help="result files, or directories that contain them")
    parser.add_argument("--output", required=True, type=Path, help="new private verdict directory")
    args = parser.parse_args(argv)
    try:
        output = args.output.expanduser().resolve()
        if output.exists():
            raise VerdictError(f"{output}: output directory already exists")
        verdict = build_verdict(expand(args.results), output)
        write_report(output, verdict)
    except (VerdictError, OSError) as error:
        print(f"verdict: {error}", file=sys.stderr)
        return 2
    print(output)
    return {"matched": 0, "different": 1, "unknown": 2}[verdict["overall"]]


if __name__ == "__main__":
    sys.exit(main())
