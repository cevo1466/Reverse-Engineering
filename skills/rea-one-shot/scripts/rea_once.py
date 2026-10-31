#!/usr/bin/env python3
"""Run an initial REA investigation and retain every raw result."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
import zipfile


REA_NPX_VERSION = "6.8.0"
PREVIEW_LIMIT = 16 * 1024 * 1024
PROVIDERS = ("ghidra", "hopper", "ida", "kuna")


def pe_has_cli_header(path: Path) -> bool:
    """Check the PE COM descriptor without trusting a filename extension."""
    try:
        with path.open("rb") as source:
            if source.read(2) != b"MZ":
                return False
            source.seek(0x3C)
            pe_offset = struct.unpack("<I", source.read(4))[0]
            if pe_offset > path.stat().st_size - 24:
                return False
            source.seek(pe_offset)
            header = source.read(24)
            if len(header) != 24 or header[:4] != b"PE\0\0":
                return False
            optional_size = struct.unpack_from("<H", header, 20)[0]
            optional = source.read(optional_size)
            if len(optional) != optional_size or len(optional) < 2:
                return False
            magic = struct.unpack_from("<H", optional)[0]
            directory_offset = {0x10B: 96, 0x20B: 112}.get(magic)
            if directory_offset is None or len(optional) < directory_offset + 15 * 8:
                return False
            rva, size = struct.unpack_from("<II", optional, directory_offset + 14 * 8)
            return rva != 0 and size != 0
    except (OSError, struct.error, ValueError):
        return False


def classify(target: str, mode: str) -> tuple[str, Path | None]:
    parsed = urlsplit(target)
    is_url = parsed.scheme.lower() in ("http", "https")
    if is_url:
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL must be HTTP(S) without embedded credentials")
        if mode not in ("auto", "web"):
            raise ValueError("a URL requires --mode web")
        return "web", None
    if parsed.scheme and "://" in target:
        raise ValueError("only HTTP(S) URLs or local paths are supported")
    if mode == "web":
        raise ValueError("--mode web requires an HTTP(S) URL")
    path = Path(target).expanduser().resolve(strict=True)
    if not (path.is_file() or path.is_dir()):
        raise ValueError("target must be a regular file or directory")
    if mode != "auto":
        return mode, path
    if path.suffix.lower() == ".asar":
        return "javascript", path
    if path.is_dir():
        markers = ("package.json", "app.asar")
        if any((path / marker).is_file() for marker in markers) or any(
            child.is_file() and child.suffix.lower() in (".js", ".mjs", ".cjs")
            for child in path.iterdir()
        ):
            return "javascript", path
        return "desktop", path
    with path.open("rb") as source:
        header = source.read(8)
    if path.suffix.lower() == ".apk" and zipfile.is_zipfile(path):
        return "android", path
    if header[:2] == b"MZ":
        return ("managed" if pe_has_cli_header(path) else "native"), path
    if header[:4] == b"\x7fELF" or header[:4] in {
        b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    }:
        return "native", path
    return "artifact", path


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rea_prefix(selected: str | None, no_download: bool) -> list[str]:
    if selected:
        executable = shutil.which(selected)
        if executable is None:
            raise ValueError(f"REA executable not found: {selected}")
        return [executable]
    executable = shutil.which("rea")
    if executable:
        return [executable]
    if no_download:
        raise ValueError("REA is not installed; install rea-agents or omit --no-download")
    executable = shutil.which("npx")
    if executable is None:
        raise ValueError("REA and npx are missing; install supported Node.js and rea-agents")
    return [executable, "-y", f"rea-agents@{REA_NPX_VERSION}"]


def find_browser(selected: str | None) -> str:
    if selected:
        path = Path(selected).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("browser executable must be a file")
        return str(path)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "msedge"):
        executable = shutil.which(name)
        if executable:
            return str(Path(executable).resolve())
    raise ValueError("Chrome-family browser not found; pass --browser-executable")


def make_steps(args: argparse.Namespace, kind: str, path: Path | None, output: Path) -> tuple[list[tuple[str, list[str]]], list[str]]:
    warnings: list[str] = []
    if kind == "web":
        browser = find_browser(args.browser_executable)
        scenario = {
            "browser": {"mode": "launch", "executable_path": browser},
            "start_url": {"url": args.target},
            "actions": [{"step_id": "settle", "action": "wait_for_timeout", "duration_ms": 100}],
            "capture": {"at_end": ["dom", "accessibility", "url"]},
        }
        scenario_path = output / "browser-scenario.json"
        scenario_path.write_text(json.dumps(scenario, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return [
            ("validate-browser-scenario", ["validate-browser-scenario", str(scenario_path), "--json"]),
            ("capture-browser-scenario", ["capture-browser-scenario", str(scenario_path), "--json"]),
        ], ["One page load is observed; interactions and background completion need focused follow-up."]
    assert path is not None
    target = str(path)
    if kind == "javascript":
        return [("analyze-javascript-application", ["analyze-javascript-application", target, "--json"])], warnings
    if kind == "desktop":
        return [("inspect-desktop-application", ["inspect-desktop-application", target, "--json"])], ["Installation inventory only; follow runtime candidates with focused analysis."]
    if kind == "managed":
        return [
            ("inspect-managed-artifact", ["inspect-managed-artifact", target, "--json"]),
            ("inspect-managed-members", ["inspect-managed-members", target, "--json"]),
        ], warnings
    if kind == "android":
        steps = [
            ("inspect-artifact", ["inspect-artifact", target, "--json"]),
            ("inspect-apk-signature", ["inspect-apk-signature", target, "--json"]),
        ]
        if args.android_code:
            steps.append(("inspect-android-package", ["inspect-android-package", target, "--json"]))
        else:
            warnings.append("APK code was not inspected. Configure JADX and a JDK, then rerun with --android-code.")
        return steps, warnings
    if kind == "native":
        command = ["analyze", target]
        if args.provider:
            command.extend(["--provider", args.provider])
        command.append("--json")
        return [("analyze", command)], warnings
    return [("inspect-artifact", ["inspect-artifact", target, "--json"])], warnings


def evidence_preview(path: Path) -> list[str]:
    if path.stat().st_size > PREVIEW_LIMIT:
        return ["Raw JSON is large; inspect it with REA's analysis views instead of loading it all at once."]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["REA output was not parseable JSON; inspect the raw output and stderr."]
    result: list[str] = []
    candidates = [payload]
    for _ in range(4):
        next_candidates = []
        for value in candidates:
            if not isinstance(value, dict):
                continue
            if "operation" in value and "normalized_result" in value:
                op = value.get("operation")
                if isinstance(op, str):
                    result.append(f"Evidence operation: `{op}`.")
                provider = value.get("provider")
                if isinstance(provider, dict) and isinstance(provider.get("id"), str):
                    result.append(f"Provider: `{provider['id']}`.")
                limitations = value.get("limitations")
                if isinstance(limitations, list) and limitations:
                    result.append(f"Evidence records {len(limitations)} limitation(s); inspect the raw JSON.")
                return result
            for key in ("data", "evidence", "result", "value"):
                nested = value.get(key)
                if isinstance(nested, dict):
                    next_candidates.append(nested)
        candidates = next_candidates
    if isinstance(payload, dict) and payload.get("valid") is True:
        return ["Browser scenario schema validated."]
    return ["Raw REA output retained; inspect its Evidence and coverage before making claims."]


def run_step(prefix: list[str], label: str, command: list[str], output: Path) -> dict:
    stdout_path = output / f"{label}.json"
    stderr_path = output / f"{label}.stderr.txt"
    argv = prefix + command
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        completed = subprocess.run(argv, stdout=stdout, stderr=stderr, check=False)
    return {
        "step": label,
        "command": argv,
        "exit_code": completed.returncode,
        "stdout": stdout_path.name,
        "stderr": stderr_path.name,
        "preview": evidence_preview(stdout_path) if stdout_path.stat().st_size else ["No JSON output was produced."],
    }


def write_report(manifest: dict, output: Path) -> None:
    lines = [
        "# REA initial investigation", "",
        f"Target: `{manifest['target']}`", "",
        f"Detected route: `{manifest['route']}`", "",
        f"Target SHA-256: `{manifest['sha256'] or 'not applicable (directory or URL)'}`", "",
        f"CLI operation status: **{manifest['status']}** (not an analysis coverage verdict)", "",
        "## Operations", "",
    ]
    for step in manifest["steps"]:
        lines.extend([
            f"### {step['step']}", "",
            f"Exit code: `{step['exit_code']}`. Raw output: `{step['stdout']}`. Diagnostics: `{step['stderr']}`.", "",
        ])
        lines.extend(f"- {item}" for item in step["preview"])
        lines.append("")
    if manifest["warnings"]:
        lines.extend(["## Coverage limits", ""])
        lines.extend(f"- {warning}" for warning in manifest["warnings"])
        lines.append("")
    lines.extend([
        "## Next analysis", "",
        "This is the first pass. Inspect the raw REA Evidence, trace the functions or behavior relevant to your question, and cite exact locations. Exit code zero does not prove complete coverage.", "",
    ])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a static-first REA investigation and retain raw evidence")
    parser.add_argument("target", help="existing local path or HTTP(S) URL")
    parser.add_argument("--output", help="new report directory (default: a temporary directory)")
    parser.add_argument("--mode", choices=("auto", "javascript", "desktop", "managed", "android", "native", "artifact", "web"), default="auto")
    parser.add_argument("--provider", choices=PROVIDERS, help="configured native analysis provider")
    parser.add_argument("--android-code", action="store_true", help="also inspect APK code with configured JADX/JDK")
    parser.add_argument("--browser-executable", help="absolute path to installed Chrome-family browser")
    parser.add_argument("--rea", help="existing REA executable to use")
    parser.add_argument("--no-download", action="store_true", help="require an installed REA CLI")
    args = parser.parse_args(argv)
    try:
        kind, path = classify(args.target, args.mode)
        if args.provider and kind != "native":
            raise ValueError("--provider applies only to native targets")
        if args.android_code and kind != "android":
            raise ValueError("--android-code applies only to APK targets")
        prefix = rea_prefix(args.rea, args.no_download)
        if kind == "web":
            args.browser_executable = find_browser(args.browser_executable)
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.mkdir(parents=True, exist_ok=False)
        else:
            output = Path(tempfile.mkdtemp(prefix="rea-one-shot-"))
        steps, warnings = make_steps(args, kind, path, output)
        digest = file_sha256(path)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": args.target if path is None else str(path),
        "route": kind,
        "sha256": digest,
        "status": "running",
        "warnings": warnings,
        "steps": [],
    }
    used_fallback = False
    try:
        for label, command in steps:
            step = run_step(prefix, label, command, output)
            manifest["steps"].append(step)
            if step["exit_code"] != 0:
                if kind == "native" and label == "analyze" and path is not None:
                    manifest["warnings"].append("Native analysis failed. The artifact inventory below is a fallback, not code decompilation. Inspect analyze.stderr.txt for the provider error.")
                    fallback = run_step(prefix, "inspect-artifact", ["inspect-artifact", str(path), "--json"], output)
                    manifest["steps"].append(fallback)
                    used_fallback = fallback["exit_code"] == 0 and (output / fallback["stdout"]).stat().st_size > 0
                break
    except (KeyboardInterrupt, OSError) as error:
        manifest["warnings"].append(f"Execution interrupted: {error}")
    all_succeeded = len(manifest["steps"]) == len(steps) and all(step["exit_code"] == 0 and (output / step["stdout"]).stat().st_size > 0 for step in manifest["steps"])
    manifest["status"] = "succeeded" if all_succeeded else "partial" if used_fallback else "failed"
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(manifest, output)
    print(output)
    return {"succeeded": 0, "partial": 2, "failed": 1}[manifest["status"]]


if __name__ == "__main__":
    sys.exit(main())
