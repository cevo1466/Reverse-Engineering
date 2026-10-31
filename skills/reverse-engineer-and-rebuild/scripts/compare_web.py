#!/usr/bin/env python3
"""Compare two REA browser Evidence files without mistaking a wrapper for a capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from rea_once import rea_prefix


def load_capture(path: Path) -> tuple[dict, str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    if payload.get("operation") == "capture_browser_scenario":
        capture = payload.get("normalized_result")
        evidence_id = payload.get("evidence_id")
    else:
        capture = payload
        evidence_id = None
    if not isinstance(capture, dict) or not isinstance(capture.get("steps"), list) or not isinstance(capture.get("browser"), dict):
        raise ValueError(f"{path}: expected REA capture_browser_scenario Evidence or a normalized browser capture")
    return capture, evidence_id if isinstance(evidence_id, str) else None


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


def write_summary(output: Path, sources: tuple[Path, Path], captures: tuple[dict, dict], ids: tuple[str | None, str | None], comparison: dict | None, exit_code: int) -> None:
    before, after = captures
    result = comparison.get("normalized_result") if isinstance(comparison, dict) else None
    result = result if isinstance(result, dict) else {}
    alignment = result.get("alignment")
    alignment = alignment if isinstance(alignment, dict) else {}
    before_steps, after_steps = step_map(before), step_map(after)
    names = list(dict.fromkeys([*before_steps, *after_steps]))
    before_viewport = before.get("scenario", {}).get("environment", {}).get("viewport")
    after_viewport = after.get("scenario", {}).get("environment", {}).get("viewport")
    same_viewport = before_viewport == after_viewport
    lines = [
        "# Web capture comparison",
        "",
        f"Reference: {sources[0]} (Evidence ID: {ids[0] or 'normalized capture'})",
        "",
        f"Rebuild: {sources[1]} (Evidence ID: {ids[1] or 'normalized capture'})",
        "",
        f"REA command exit: {exit_code}; overall status: {result.get('overall_status', 'unavailable')}; alignment: {alignment.get('status', 'unavailable')}.",
        "",
        "Command exit 0 means REA compared the inputs, not that they matched. Same means the selected artifact digest or route matched for that captured step. It is not a claim about unobserved states. Raw DOM may differ because of markup formatting or scripts even when the rendered page agrees.",
        "",
        "| Step | Screenshot bytes | Accessibility | Raw DOM | Relative route |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in names:
        left, right = before_steps.get(name), after_steps.get(name)
        values = [
            pair_status(left, right, "screenshot", "sha256", same_viewport),
            pair_status(left, right, "accessibility", "sha256"),
            pair_status(left, right, "dom", "sha256"),
            route_status(left, right),
        ]
        lines.append("| " + " | ".join([name, *values]) + " |")
    lines += ["", f"Viewport comparison: {'same' if same_viewport else 'different or unavailable'}.", ""]
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
    parser.add_argument("--no-download", action="store_true", help="require installed REA")
    args = parser.parse_args(argv)
    try:
        sources = (args.reference.expanduser().resolve(strict=True), args.rebuild.expanduser().resolve(strict=True))
        captures_and_ids = [load_capture(source) for source in sources]
        captures = (captures_and_ids[0][0], captures_and_ids[1][0])
        ids = (captures_and_ids[0][1], captures_and_ids[1][1])
        prefix = rea_prefix(args.rea, args.no_download)
        output = args.output.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=False)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    capture_paths = []
    for label, capture in zip(("reference", "rebuild"), captures):
        path = output / f"{label}-normalized.json"
        path.write_text(json.dumps(capture, ensure_ascii=False) + "\n", encoding="utf-8")
        capture_paths.append(path)
    result_path = output / "rea-comparison.json"
    stderr_path = output / "rea-comparison.stderr.txt"
    with result_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        completed = subprocess.run([*prefix, "compare-web-captures", *map(str, capture_paths), "--json"], stdout=stdout, stderr=stderr, check=False)
    try:
        comparison = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        comparison = None
    write_summary(output, sources, captures, ids, comparison, completed.returncode)
    print(output)
    return 0 if completed.returncode == 0 and isinstance(comparison, dict) and "normalized_result" in comparison else 1


if __name__ == "__main__":
    sys.exit(main())
