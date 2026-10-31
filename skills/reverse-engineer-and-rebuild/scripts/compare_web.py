#!/usr/bin/env python3
"""Compare two REA browser Evidence files without mistaking a wrapper for a capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from pixel_diff import ImageError, compare_images, load_image
from rea_once import rea_prefix
from report_safety import create_report_dir


def load_capture(path: Path) -> tuple[dict, str | None, dict | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    if payload.get("operation") == "capture_browser_scenario":
        capture = payload.get("normalized_result")
        evidence_id = payload.get("evidence_id")
        viewport = payload.get("parameters", {}).get("environment", {}).get("viewport")
    else:
        capture = payload
        evidence_id = None
        viewport = payload.get("scenario", {}).get("environment", {}).get("viewport")
    if not isinstance(capture, dict) or not isinstance(capture.get("steps"), list) or not isinstance(capture.get("browser"), dict):
        raise ValueError(f"{path}: expected REA capture_browser_scenario Evidence or a normalized browser capture")
    return capture, evidence_id if isinstance(evidence_id, str) else None, viewport if isinstance(viewport, dict) else None


def step_map(capture: dict) -> dict[str, dict | None]:
    steps: dict[str, dict | None] = {}
    for step in capture["steps"]:
        if isinstance(step, dict) and isinstance(step.get("step_id"), str):
            name = step["step_id"]
            steps[name] = None if name in steps else step
    return steps


def artifact_value(step: dict | None, name: str) -> dict | None:
    if not isinstance(step, dict):
        return None
    artifact = step.get("artifacts", {}).get(name)
    if not isinstance(artifact, dict) or artifact.get("state") != "captured":
        return None
    value = artifact.get("value")
    return value if isinstance(value, dict) else None


def pair_status(left: dict | None, right: dict | None, name: str, key: str, comparable: bool = True) -> str:
    if not comparable:
        return "unknown"
    before = artifact_value(left, name)
    after = artifact_value(right, name)
    if before is None or after is None or not isinstance(before.get(key), str) or not isinstance(after.get(key), str):
        return "unknown"
    return "same" if before[key] == after[key] else "different"


def route_status(left: dict | None, right: dict | None) -> str:
    before = artifact_value(left, "url")
    after = artifact_value(right, "url")
    if before is None or after is None:
        return "unknown"
    if not isinstance(before.get("url"), str) or not isinstance(after.get("url"), str):
        return "unknown"
    a, b = urlsplit(before["url"]), urlsplit(after["url"])
    return "same" if (a.path, a.query, a.fragment) == (b.path, b.query, b.fragment) else "different"


def compare_visual_steps(output: Path, sources: tuple[Path, Path], captures: tuple[dict, dict], viewports: tuple[dict | None, dict | None], channel_threshold: int, max_changed_ratio: float) -> dict:
    before_steps, after_steps = step_map(captures[0]), step_map(captures[1])
    names = list(dict.fromkeys([*before_steps, *after_steps]))
    same_viewport = isinstance(viewports[0], dict) and viewports[0] == viewports[1]
    rows = []
    visual_dir = output / "visual"
    for index, name in enumerate(names):
        before_step, after_step = before_steps.get(name), after_steps.get(name)
        left = artifact_value(before_step, "screenshot")
        right = artifact_value(after_step, "screenshot")
        if before_step is None or after_step is None:
            rows.append({"step_id": name, "status": "unknown", "changed_ratio": None, "passes_threshold": None, "reason": "Step missing or step_id duplicated in one capture."})
            continue
        if left is None and right is None:
            continue
        row = {"step_id": name, "status": "unknown", "changed_ratio": None, "passes_threshold": None}
        if before_step.get("action") != after_step.get("action"):
            row["reason"] = "Actions differ for this step_id."
        elif before_step.get("status") != "completed" or after_step.get("status") != "completed":
            row["reason"] = "One or both steps did not complete."
        elif left is None or right is None:
            row["reason"] = "Screenshot missing from one capture."
        elif not same_viewport:
            row["reason"] = "Viewport settings differ or are unavailable."
        else:
            try:
                result, heatmap = compare_images(
                    load_image(sources[0], name), load_image(sources[1], name),
                    channel_threshold, max_changed_ratio,
                )
                step_dir = visual_dir / f"step-{index:03d}"
                step_dir.mkdir(parents=True)
                (step_dir / "pixel-diff.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                if heatmap is not None:
                    (step_dir / "pixel-diff.png").write_bytes(heatmap)
                row.update(status=result["status"], changed_ratio=result["changed_ratio"], passes_threshold=result["passes_threshold"], report=str(step_dir.relative_to(output) / "pixel-diff.json"))
            except (ImageError, OSError, ValueError) as error:
                row["reason"] = str(error)
        rows.append(row)
    status = "different" if any(row["passes_threshold"] is False for row in rows) else "unknown" if not rows or any(row["passes_threshold"] is None for row in rows) else "within_threshold"
    summary = {"status": status, "channel_threshold": channel_threshold, "max_changed_ratio": max_changed_ratio, "steps": rows}
    (output / "visual-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def write_summary(output: Path, sources: tuple[Path, Path], captures: tuple[dict, dict], ids: tuple[str | None, str | None], viewports: tuple[dict | None, dict | None], comparison: dict | None, exit_code: int | None, rea_note: str | None, visual: dict) -> None:
    before, after = captures
    result = comparison.get("normalized_result") if isinstance(comparison, dict) else None
    result = result if isinstance(result, dict) else {}
    alignment = result.get("alignment")
    alignment = alignment if isinstance(alignment, dict) else {}
    before_steps, after_steps = step_map(before), step_map(after)
    names = list(dict.fromkeys([*before_steps, *after_steps]))
    same_viewport = isinstance(viewports[0], dict) and viewports[0] == viewports[1]
    visual_steps = {row["step_id"]: row for row in visual["steps"]}
    lines = [
        "# Web capture comparison",
        "",
        f"Reference: {sources[0]} (Evidence ID: {ids[0] or 'normalized capture'})",
        "",
        f"Rebuild: {sources[1]} (Evidence ID: {ids[1] or 'normalized capture'})",
        "",
        f"REA command exit: {exit_code if exit_code is not None else 'not run'}; overall status: {result.get('overall_status', 'unavailable')}; alignment: {alignment.get('status', 'unavailable')}.",
        "",
        f"Pixel gate: {visual['status']} (channel threshold {visual['channel_threshold']}, maximum changed ratio {visual['max_changed_ratio']}). This gate covers captured screenshots at matching viewport settings only.",
        "",
        "A zero exit from this helper means the captured screenshot pixel gate passed and any selected REA command completed. It does not establish behavioral parity. Same means the selected artifact digest or route matched for that captured step. Raw DOM may differ because of markup formatting or scripts even when the rendered page agrees.",
        "",
        "| Step | Pixel diff | Changed pixels | Screenshot bytes | Accessibility | Raw DOM | Relative route |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in names:
        left, right = before_steps.get(name), after_steps.get(name)
        visual_row = visual_steps.get(name, {})
        ratio = visual_row.get("changed_ratio")
        values = [
            visual_row.get("status", "not_requested"),
            f"{ratio:.4%}" if isinstance(ratio, (float, int)) else "unknown",
            pair_status(left, right, "screenshot", "sha256", same_viewport),
            pair_status(left, right, "accessibility", "sha256"),
            pair_status(left, right, "dom", "sha256"),
            route_status(left, right),
        ]
        lines.append("| " + " | ".join([name, *values]) + " |")
    lines += ["", f"Viewport comparison: {'same' if same_viewport else 'different or unavailable'}.", ""]
    for row in visual["steps"]:
        if row.get("reason"):
            lines.append(f"Pixel comparison for {row['step_id']}: {row['reason']}")
    lines.append("")
    if rea_note:
        lines += [f"REA note: {rea_note}", ""]
    if isinstance(comparison, dict) and comparison.get("error"):
        lines += [f"REA error: {comparison.get('code', 'unknown')}: {comparison.get('message', comparison['error'])}", ""]
    failures = alignment.get("failures")
    if isinstance(failures, list) and failures:
        lines += ["REA alignment failures:", ""]
        for failure in failures:
            if isinstance(failure, dict):
                lines.append(f"- {failure.get('code', 'unknown')}: {failure.get('reason', '')}")
        lines.append("")
    lines += [
        "Inspect the full REA comparison and both Evidence files before deciding whether the requested behavior matches. A different origin may make REA's overall result changed even when screenshot and accessibility digests match.",
        "",
    ]
    (output / "comparison.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and compare two REA browser scenario captures")
    parser.add_argument("reference", type=Path, help="reference Evidence JSON")
    parser.add_argument("rebuild", type=Path, help="rebuild Evidence JSON")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    parser.add_argument("--rea", help="existing REA executable")
    parser.add_argument("--no-rea", action="store_true", help="compare recorded artifacts without invoking REA")
    parser.add_argument("--no-download", action="store_true", help="require installed REA")
    parser.add_argument("--channel-threshold", type=int, default=0, help="per-channel pixel tolerance, 0–255")
    parser.add_argument("--max-changed-ratio", type=float, default=0.0, help="allowed fraction of changed pixels, 0–1")
    parser.add_argument("--timeout-seconds", type=float, default=600, help="REA comparison limit (default: 600 seconds)")
    args = parser.parse_args(argv)
    try:
        sources = (args.reference.expanduser().resolve(strict=True), args.rebuild.expanduser().resolve(strict=True))
        captures_and_ids = [load_capture(source) for source in sources]
        captures = (captures_and_ids[0][0], captures_and_ids[1][0])
        ids = (captures_and_ids[0][1], captures_and_ids[1][1])
        viewports = (captures_and_ids[0][2], captures_and_ids[1][2])
        if not 0 <= args.channel_threshold <= 255 or not 0 <= args.max_changed_ratio <= 1:
            raise ValueError("pixel thresholds must be channel 0–255 and ratio 0–1")
        if not 0 < args.timeout_seconds <= 3600:
            raise ValueError("--timeout-seconds must be greater than 0 and at most 3600")
        if args.no_rea and args.rea:
            raise ValueError("--no-rea cannot be combined with --rea")
        rea_note = "Skipped by --no-rea." if args.no_rea else None
        prefix = None
        if not args.no_rea:
            try:
                prefix = rea_prefix(args.rea, args.no_download)
            except ValueError as error:
                if args.rea:
                    raise
                rea_note = f"{error}; direct artifact comparison remains available."
        output = args.output.expanduser().resolve()
        create_report_dir(output)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    capture_paths = []
    for label, capture in zip(("reference", "rebuild"), captures):
        path = output / f"{label}-normalized.json"
        path.write_text(json.dumps(capture, ensure_ascii=False) + "\n", encoding="utf-8")
        capture_paths.append(path)
    visual = compare_visual_steps(output, sources, captures, viewports, args.channel_threshold, args.max_changed_ratio)
    comparison = None
    exit_code = None
    if prefix is not None:
        result_path = output / "rea-comparison.json"
        stderr_path = output / "rea-comparison.stderr.txt"
        with result_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                completed = subprocess.run([*prefix, "compare-web-captures", *map(str, capture_paths), "--json"], stdout=stdout, stderr=stderr, check=False, timeout=args.timeout_seconds)
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                exit_code = None
                rea_note = f"REA comparison timed out after {args.timeout_seconds:g} seconds."
                stderr.write((rea_note + "\n").encode())
        try:
            comparison = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            comparison = None
        normalized = comparison.get("normalized_result") if isinstance(comparison, dict) else None
        if not isinstance(normalized, dict) or not isinstance(normalized.get("overall_status"), str) or not isinstance(normalized.get("alignment"), dict):
            rea_note = (rea_note + " " if rea_note else "") + "REA comparison output is not valid comparison Evidence."
    write_summary(output, sources, captures, ids, viewports, comparison, exit_code, rea_note, visual)
    print(output)
    if prefix is not None and (exit_code != 0 or not isinstance(comparison, dict) or not isinstance(comparison.get("normalized_result"), dict) or not isinstance(comparison["normalized_result"].get("overall_status"), str) or not isinstance(comparison["normalized_result"].get("alignment"), dict)):
        return 2
    return {"within_threshold": 0, "different": 1, "unknown": 2}[visual["status"]]


if __name__ == "__main__":
    sys.exit(main())
