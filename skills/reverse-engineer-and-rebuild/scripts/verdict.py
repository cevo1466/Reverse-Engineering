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
