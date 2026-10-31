# Verdict Report and CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `verdict.py`, which combines the output of the four comparison helpers into one `matched` / `different` / `unknown` verdict with a CI-friendly exit code, plus a GitHub Actions workflow that runs the test suite.

**Architecture:** `verdict.py` reads existing result files (`pixel-diff.json`, `comparison.json`, `visual-summary.json`), detects each file's kind by its shape, maps every row to a shared case record, and writes `verdict.json` and `verdict.md`. The four existing helpers are not modified.

**Tech Stack:** Python 3.10+ standard library only, `unittest`, GitHub Actions.

**Spec:** `docs/specs/2026-10-31-verdict-report-design.md`

## Global Constraints

- Python 3.10+ and standard library only; no third-party imports.
- Do not modify `pixel_diff.py`, `compare_capture.py`, `compare_web.py`, `compare_http.py`, `capture_web.py`, `capture_http.py`, `rea_once.py` or `report_safety.py`.
- Status vocabulary is exactly `matched`, `different`, `unknown`. Unrecognised source values map to `unknown`.
- Exit codes: 0 `matched`, 1 `different`, 2 `unknown` or any input error.
- Output directory must not exist and is created through `report_safety.create_report_dir` (writes a `*` `.gitignore`).
- No WebP or other image formats in this sub-project.
- Tests are written before code. Tests need no Chrome, Node or network.
- Branch `feat/verdict-report`; one pull request; do not merge it.
- Commit messages end with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Prose written to the repository (docs, commits) is plain English, not terse style.

## File Structure

- Create `skills/reverse-engineer-and-rebuild/scripts/verdict.py`: normalisation, kind detection, case extraction, report writing, CLI. One file, about 170 lines, one responsibility; it matches the repo's one-script-per-tool layout.
- Create `tests/test_verdict.py`: unit tests, CLI tests, real-helper tests.
- Create `.github/workflows/ci.yml`.
- Modify `README.md`, `README.en.md`, `skills/reverse-engineer-and-rebuild/SKILL.md`.

## Prerequisites

Work in a real clone with write access (deploy key configured), not the read-only scratch clone.

- [ ] **Step 1: Clone and create the branch**

```bash
git clone git@github.com:cevo1466/Reverse-Engineering.git ~/Reverse-Engineering
cd ~/Reverse-Engineering
git checkout -b feat/verdict-report
```

- [ ] **Step 2: Bring over the spec and plan commits**

The spec and this plan were committed in the scratch clone. Fetch that branch and fast-forward:

```bash
git fetch /tmp/claude-1001/-home-devuser/7326ea12-b90b-4b4b-b383-496bdd157518/scratchpad/re-repo feat/verdict-report
git merge --ff-only FETCH_HEAD
ls docs/specs docs/plans
```

Expected: both directories exist with one file each.

- [ ] **Step 3: Baseline**

Run: `python3 -I -m unittest discover -s tests`
Expected: `Ran 36 tests ... OK` (about 25 seconds).

---

### Task 1: Status mapping, kind detection and case extraction

**Files:**
- Create: `skills/reverse-engineer-and-rebuild/scripts/verdict.py`
- Create: `tests/test_verdict.py`

**Interfaces:**
- Produces (all in `verdict.py`):
  - `class VerdictError(ValueError)`
  - `normalise_status(value: Any) -> str` returns `"matched" | "different" | "unknown"`
  - `threshold_status(passes: Any) -> str` same return set; `True` maps to matched, `False` to different, anything else to unknown
  - `identifier(value: Any, where: str) -> str` raises `VerdictError` on a missing or empty string
  - `count(value: Any) -> int`
  - `detect_kind(data: dict, source: Path) -> str` returns `"pixel" | "capture" | "web" | "http"` or raises `VerdictError`
  - `cases_from(kind: str, data: dict, source: Path) -> list[dict]` each dict has `case_id`, `kind`, `status`, `reason`
- Later tasks add `read_result`, `expand`, `build_verdict`, `cell`, `write_report`, `main` to the same file.

The file imports `argparse`, `json`, `os`, `sys` and `create_report_dir` now even though Task 2 is the first to use them; this avoids editing the import block later.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verdict.py`:

```python
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("verdict", SCRIPTS / "verdict.py")
verdict = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verdict)


def pixel(passes=True, status="identical", ratio=0.0):
    return {"status": status, "passes_threshold": passes, "changed_ratio": ratio}


def capture_row(step_id="home", visual_status="matched", url=True, viewport=True):
    return {"id": step_id, "url_path_matches": url, "viewport_matches": viewport, "visual_status": visual_status,
            "visual": {"status": "identical" if visual_status == "matched" else "different", "passes_threshold": visual_status == "matched"}}


def capture(*rows):
    return {"schema_version": 1, "overall": "matched_visual_observations", "steps": list(rows)}


def web_row(step_id="home", passes=True):
    return {"step_id": step_id, "status": "identical", "changed_ratio": 0.0, "passes_threshold": passes}


def web(*rows, status="within_threshold"):
    return {"status": status, "steps": list(rows)}


def http_case(case_id="create", status="matched_observations", differences=(), unknowns=()):
    return {"id": case_id, "status": status, "differences": list(differences), "unknowns": list(unknowns)}


def http(*cases):
    return {"schema_version": 1, "status": "matched_observations", "summary": {"total_cases": len(cases)}, "cases": list(cases)}


class NormaliseTests(unittest.TestCase):
    def test_matched_values(self):
        for value in ("identical", "within_tolerance", "within_threshold", "matched", "matched_observations", "matched_visual_observations"):
            self.assertEqual(verdict.normalise_status(value), "matched", value)

    def test_different_values(self):
        for value in ("different", "dimension_mismatch", "different_or_incomplete"):
            self.assertEqual(verdict.normalise_status(value), "different", value)

    def test_unrecognised_values_are_unknown(self):
        for value in ("inconclusive", "unknown", "weird", None, 5, ["matched"]):
            self.assertEqual(verdict.normalise_status(value), "unknown", repr(value))

    def test_threshold_status(self):
        self.assertEqual(verdict.threshold_status(True), "matched")
        self.assertEqual(verdict.threshold_status(False), "different")
        self.assertEqual(verdict.threshold_status(None), "unknown")
        self.assertEqual(verdict.threshold_status("yes"), "unknown")


class DetectKindTests(unittest.TestCase):
    source = Path("/tmp/x/result.json")

    def test_each_kind(self):
        self.assertEqual(verdict.detect_kind(pixel(), self.source), "pixel")
        self.assertEqual(verdict.detect_kind(capture(capture_row()), self.source), "capture")
        self.assertEqual(verdict.detect_kind(web(web_row()), self.source), "web")
        self.assertEqual(verdict.detect_kind(http(http_case()), self.source), "http")

    def test_http_status_different_is_not_web(self):
        data = http(http_case(status="different"))
        data["status"] = "different"
        self.assertEqual(verdict.detect_kind(data, self.source), "http")

    def test_unknown_shape_raises(self):
        with self.assertRaises(verdict.VerdictError):
            verdict.detect_kind({"hello": "world"}, self.source)

    def test_ambiguous_shape_raises(self):
        data = {**pixel(), **web(web_row())}
        with self.assertRaises(verdict.VerdictError):
            verdict.detect_kind(data, self.source)


class CasesFromTests(unittest.TestCase):
    source = Path("/tmp/x/home-diff/pixel-diff.json")

    def statuses(self, kind, data):
        return {case["case_id"]: case["status"] for case in verdict.cases_from(kind, data, self.source)}

    def test_pixel_uses_threshold_not_status_word(self):
        self.assertEqual(self.statuses("pixel", pixel(True, "different", 0.001)), {"pixel:home-diff": "matched"})
        self.assertEqual(self.statuses("pixel", pixel(False, "dimension_mismatch", None)), {"pixel:home-diff": "different"})
        self.assertEqual(self.statuses("pixel", pixel(None, "unknown", None)), {"pixel:home-diff": "unknown"})

    def test_capture_requires_pixels_url_and_viewport(self):
        data = capture(capture_row("a"), capture_row("b", url=False), capture_row("c", viewport=False), capture_row("d", visual_status="different_or_incomplete"))
        self.assertEqual(self.statuses("capture", data), {"capture:a": "matched", "capture:b": "different", "capture:c": "different", "capture:d": "different"})
        reasons = {case["case_id"]: case["reason"] for case in verdict.cases_from("capture", data, self.source)}
        self.assertIn("url_path_matches", reasons["capture:b"])
        self.assertIn("viewport_matches", reasons["capture:c"])

    def test_web_rows(self):
        data = web(web_row("a", True), web_row("b", False), web_row("c", None))
        self.assertEqual(self.statuses("web", data), {"web:a": "matched", "web:b": "different", "web:c": "unknown"})

    def test_http_rows(self):
        data = http(http_case("a"), http_case("b", "different", differences=["x"]), http_case("c", "inconclusive", unknowns=["y"]))
        self.assertEqual(self.statuses("http", data), {"http:a": "matched", "http:b": "different", "http:c": "unknown"})
        reasons = {case["case_id"]: case["reason"] for case in verdict.cases_from("http", data, self.source)}
        self.assertEqual(reasons["http:b"], "1 difference(s), 0 unknown(s)")

    def test_missing_id_raises(self):
        with self.assertRaises(verdict.VerdictError):
            verdict.cases_from("web", web({"passes_threshold": True}), self.source)
        with self.assertRaises(verdict.VerdictError):
            verdict.cases_from("http", http({"status": "matched_observations"}), self.source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -I -m unittest discover -s tests -p test_verdict.py`
Expected: FAIL at import, `FileNotFoundError` for `verdict.py`.

- [ ] **Step 3: Write the minimal implementation**

Create `skills/reverse-engineer-and-rebuild/scripts/verdict.py`:

```python
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
    """Turn one result file into normalised cases (status and reason, no evidence path yet)."""
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -I -m unittest discover -s tests -p test_verdict.py -v`
Expected: 13 tests, all `ok`, final line `OK`.

- [ ] **Step 5: Commit**

```bash
git add skills/reverse-engineer-and-rebuild/scripts/verdict.py tests/test_verdict.py
git commit -m "feat: map comparison results to a shared verdict vocabulary

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Verdict building, report files and CLI

**Files:**
- Modify: `skills/reverse-engineer-and-rebuild/scripts/verdict.py` (append after `cases_from`)
- Modify: `tests/test_verdict.py` (insert before the `if __name__ == "__main__":` block)

**Interfaces:**
- Consumes (from Task 1): `VerdictError`, `detect_kind(data, source)`, `cases_from(kind, data, source)`, `RESULT_NAMES`, `SCOPE`, `create_report_dir`.
- Produces:
  - `read_result(path: Path) -> dict`
  - `expand(paths: list[Path]) -> list[Path]`
  - `build_verdict(files: list[Path], output: Path) -> dict` with keys `schema_version`, `overall`, `totals`, `scope`, `cases`; each case also carries `evidence`, a path relative to `output`
  - `cell(value: Any) -> str`
  - `write_report(output: Path, verdict: dict) -> None`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

Insert this class into `tests/test_verdict.py` directly above the `if __name__ == "__main__":` block:

```python
class VerdictCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def run_main(self, *paths, output="out"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = verdict.main([*map(str, paths), "--output", str(self.root / output)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_all_matched_exits_zero_and_writes_report(self):
        code, out, err = self.run_main(self.write("a/pixel-diff.json", pixel()), self.write("b/comparison.json", http(http_case())))
        self.assertEqual((code, err), (0, ""), err)
        report = json.loads((self.root / "out" / "verdict.json").read_text(encoding="utf-8"))
        self.assertEqual(report["overall"], "matched")
        self.assertEqual(report["totals"], {"matched": 2, "different": 0, "unknown": 0})
        self.assertEqual({case["case_id"] for case in report["cases"]}, {"pixel:a", "http:create"})
        self.assertTrue(all(not Path(case["evidence"]).is_absolute() for case in report["cases"]))
        self.assertEqual((self.root / "out" / ".gitignore").read_text(encoding="utf-8"), "*\n")
        markdown = (self.root / "out" / "verdict.md").read_text(encoding="utf-8")
        self.assertIn("Overall: **matched**", markdown)
        self.assertIn("| pixel:a | pixel | matched |", markdown)
        self.assertEqual(out.strip(), str((self.root / "out").resolve()))

    def test_one_different_exits_one(self):
        code, _, _ = self.run_main(self.write("a/pixel-diff.json", pixel()), self.write("b/visual-summary.json", web(web_row("x", False), status="different")))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((self.root / "out" / "verdict.json").read_text(encoding="utf-8"))["overall"], "different")

    def test_unknown_without_different_exits_two_and_writes_report(self):
        code, _, _ = self.run_main(self.write("a/visual-summary.json", web(web_row("x", None), status="unknown")))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads((self.root / "out" / "verdict.json").read_text(encoding="utf-8"))["overall"], "unknown")

    def test_zero_cases_is_unknown(self):
        code, _, _ = self.run_main(self.write("a/visual-summary.json", web(status="unknown")))
        self.assertEqual(code, 2)

    def test_directory_argument_finds_known_files(self):
        self.write("run/pixel-diff.json", pixel())
        self.write("run/notes.json", "not read")
        code, _, err = self.run_main(self.root / "run")
        self.assertEqual((code, err), (0, ""), err)

    def test_pipe_in_reason_is_escaped(self):
        data = web(web_row("x", None), status="unknown")
        data["steps"][0]["reason"] = "a | b"
        self.run_main(self.write("a/visual-summary.json", data))
        self.assertIn("a \\| b", (self.root / "out" / "verdict.md").read_text(encoding="utf-8"))

    def assert_error(self, *paths, output="out"):
        code, out, err = self.run_main(*paths, output=output)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("verdict: "), err)

    def test_errors_exit_two_and_write_nothing(self):
        self.assert_error(self.write("bad.json", "{not json"))
        self.assertFalse((self.root / "out").exists())
        self.assert_error(self.write("shape.json", {"hello": "world"}))
        self.assertFalse((self.root / "out").exists())
        self.assert_error(self.root / "missing.json")
        self.assertFalse((self.root / "out").exists())
        self.assert_error(self.write("empty/readme.txt", "x").parent)
        self.assertFalse((self.root / "out").exists())

    def test_duplicate_case_id_is_an_error(self):
        path = self.write("a/pixel-diff.json", pixel())
        self.assert_error(path, path)
        self.assertFalse((self.root / "out").exists())

    def test_existing_output_directory_is_an_error(self):
        path = self.write("a/pixel-diff.json", pixel())
        (self.root / "out").mkdir()
        self.assert_error(path)
        self.assertEqual(list((self.root / "out").iterdir()), [])


```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -I -m unittest discover -s tests -p test_verdict.py`
Expected: the nine new tests ERROR with `AttributeError: module 'verdict' has no attribute 'main'`; the 13 earlier tests still pass.

- [ ] **Step 3: Write the minimal implementation**

Append to `verdict.py`, after `cases_from`:

```python


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
    totals = {name: sum(case["status"] == name for case in cases) for name in ("matched", "different", "unknown")}
    overall = "different" if totals["different"] else "unknown" if totals["unknown"] or not cases else "matched"
    return {"schema_version": 1, "overall": overall, "totals": totals, "scope": SCOPE, "cases": cases}


def cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_report(output: Path, verdict: dict) -> None:
    create_report_dir(output)
    (output / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Verdict", "", f"Overall: **{verdict['overall']}**. {SCOPE}", "", "| Case | Kind | Status | Reason | Evidence |", "| --- | --- | --- | --- | --- |"]
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -I -m unittest discover -s tests -p test_verdict.py -v`
Expected: 22 tests, all `ok`, final line `OK`.

- [ ] **Step 5: Commit**

```bash
git add skills/reverse-engineer-and-rebuild/scripts/verdict.py tests/test_verdict.py
git commit -m "feat: add verdict.py to combine comparison results

Writes verdict.json and verdict.md and exits 0, 1 or 2 for matched,
different or unknown.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: End-to-end test against the real helpers

**Files:**
- Modify: `tests/test_verdict.py` (insert before the `if __name__ == "__main__":` block)

**Interfaces:**
- Consumes: the `verdict.py` CLI (`python3 verdict.py PATH... --output DIR`), `pixel_diff.py` CLI (`reference rebuild --output DIR`), `compare_http.py` CLI (`reference rebuild --output DIR`), and the module-level `SCRIPTS`, `struct`, `subprocess`, `zlib` imports already present from Task 1.
- Produces: nothing new for later tasks.

This test guards against the helpers changing their output shape without `verdict.py` noticing.

- [ ] **Step 1: Write the test**

Insert above the `if __name__ == "__main__":` block:

```python
def png(rgb):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(rgb))) + chunk(b"IEND", b"")


class RealHelperTests(unittest.TestCase):
    """Run the real helpers on tiny inputs so detection stays in step with their output."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def helper(self, script, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True, check=False)

    def pixel_run(self, name, rebuild_rgb):
        (self.root / "ref.png").write_bytes(png((255, 0, 0)))
        (self.root / f"{name}.png").write_bytes(png(rebuild_rgb))
        return self.helper("pixel_diff.py", self.root / "ref.png", self.root / f"{name}.png", "--output", self.root / name)

    def http_run(self, name):
        transcript = {"schema_version": 1, "cases": [{"id": "create", "request": {"method": "POST", "path": "/api/items"}, "response": {"status": 201, "body": {"id": 1}}}]}
        for side in ("ref", "reb"):
            (self.root / f"{name}-{side}.json").write_text(json.dumps(transcript), encoding="utf-8")
        return self.helper("compare_http.py", self.root / f"{name}-ref.json", self.root / f"{name}-reb.json", "--output", self.root / name)

    def verdict_run(self, *paths):
        return self.helper("verdict.py", *paths, "--output", self.root / "verdict")

    def test_matching_real_results_exit_zero(self):
        self.assertEqual(self.pixel_run("same", (255, 0, 0)).returncode, 0)
        self.assertEqual(self.http_run("api").returncode, 0)
        completed = self.verdict_run(self.root / "same", self.root / "api")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((self.root / "verdict" / "verdict.json").read_text(encoding="utf-8"))
        self.assertEqual({case["case_id"]: case["status"] for case in report["cases"]}, {"pixel:same": "matched", "http:create": "matched"})

    def test_different_real_pixels_exit_one(self):
        self.assertEqual(self.pixel_run("changed", (0, 0, 255)).returncode, 1)
        self.assertEqual(self.http_run("api").returncode, 0)
        completed = self.verdict_run(self.root / "changed", self.root / "api")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        report = json.loads((self.root / "verdict" / "verdict.json").read_text(encoding="utf-8"))
        self.assertEqual({case["case_id"]: case["status"] for case in report["cases"]}, {"pixel:changed": "different", "http:create": "matched"})


```

- [ ] **Step 2: Run the tests**

Run: `python3 -I -m unittest discover -s tests -p test_verdict.py -v`
Expected: 24 tests, all `ok`. This task adds tests only; they pass at once because Task 2 already implemented the behaviour. To confirm the tests can fail, temporarily change `"pixel-diff.json"` to `"pixel-diff.jsn"` in `RESULT_NAMES`, rerun (expect the two `RealHelperTests` to fail with `no result file`), then revert the change.

- [ ] **Step 3: Run the full suite**

Run: `python3 -I -m unittest discover -s tests`
Expected: `Ran 60 tests ... OK`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_verdict.py
git commit -m "test: run verdict.py against real pixel_diff and compare_http output

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the test command `python3 -m unittest discover -s tests -v` (works from the repository root; verified in Prerequisites step 3).
- Produces: a check named `unittest` on pull requests and on pushes to `main`.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: tests

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  unittest:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Run unit tests
        run: python3 -m unittest discover -s tests -v
```

- [ ] **Step 2: Check the file locally**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')" 2>&1 || echo "PyYAML not installed; skip"`
Expected: `yaml ok`, or the skip message (then rely on the first pull-request run).

Run the exact CI command: `python3 -m unittest discover -s tests`
Expected: `Ran 60 tests ... OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run the unit tests on pull requests and main

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Documentation

**Files:**
- Modify: `README.md` (insert a section before the heading `## “Birebir” ne demek?`)
- Modify: `README.en.md` (insert a section before the heading `## What “exact” means`)
- Modify: `skills/reverse-engineer-and-rebuild/SKILL.md:31`

**Interfaces:**
- Consumes: the `verdict.py` CLI from Task 2.
- Produces: nothing.

- [ ] **Step 1: Add the Turkish README section**

In `README.md`, insert directly above `## “Birebir” ne demek?`:

````markdown
### Birleşik sonuç

Birden fazla karşılaştırmanın çıktısı tek bir sonuçta birleştirilebilir. `verdict.py` yeni karşılaştırma yapmaz; `pixel_diff.py`, `compare_capture.py`, `compare_web.py` ve `compare_http.py` çıktılarını okur, her satırı `matched`, `different` veya `unknown` olarak yazar ve `verdict.json` ile `verdict.md` üretir:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/verdict.py ./web-check ./api-check --output ./verdict
```

Çıkış kodu: `0` hepsi eşleşti, `1` en az bir fark var, `2` belirsiz durum veya hata var. Eşikler (`--channel-threshold`, `--max-changed-ratio`) ilgili karşılaştırma aracında kalır; `verdict.py` yalnızca orada kaydedilen sonucu okur. Tanınmayan bir değer hiçbir zaman “eşleşti” sayılmaz. Bir klasör verirseniz içindeki `pixel-diff.json`, `comparison.json` ve `visual-summary.json` dosyaları okunur.

CI içinde kullanım:

```yaml
- run: python3 skills/reverse-engineer-and-rebuild/scripts/verdict.py ./web-check ./api-check --output ./verdict
```

“Eşleşti” yalnızca seçilen gözlemlerin eşleştiği anlamına gelir; görülmeyen durumlar kanıtlanmış sayılmaz.

````

- [ ] **Step 2: Add the English README section**

In `README.en.md`, insert directly above `## What “exact” means`:

````markdown
### Combined verdict

`verdict.py` combines several comparison results into one answer. It does not compare anything itself. It reads the output of `pixel_diff.py`, `compare_capture.py`, `compare_web.py` and `compare_http.py`, labels every row `matched`, `different` or `unknown`, and writes `verdict.json` and `verdict.md`:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/verdict.py ./web-check ./api-check --output ./verdict
```

Exit code: `0` everything matched, `1` at least one difference, `2` something is unknown or an input error occurred. Thresholds (`--channel-threshold`, `--max-changed-ratio`) stay with the comparison tool; `verdict.py` only reads the result recorded there. An unrecognised value is never counted as a match. When you pass a directory, it reads the `pixel-diff.json`, `comparison.json` and `visual-summary.json` files directly inside it.

In CI:

```yaml
- run: python3 skills/reverse-engineer-and-rebuild/scripts/verdict.py ./web-check ./api-check --output ./verdict
```

“Matched” means only that the selected observations matched; unseen states are not certified.

````

- [ ] **Step 3: Update SKILL.md step 8**

In `skills/reverse-engineer-and-rebuild/SKILL.md`, replace

```
Never mark a case matched solely because code exists or tests pass without comparing the original.
```

with

```
Never mark a case matched solely because code exists or tests pass without comparing the original. Combine the comparison outputs into that checklist with `python3 <this-skill-directory>/scripts/verdict.py RESULT_OR_DIR [RESULT_OR_DIR ...] --output VERDICT_DIR`; it writes `verdict.json` and `verdict.md` and exits 0 (all matched), 1 (any different) or 2 (unknown or error).
```

- [ ] **Step 4: Verify the docs**

Run: `grep -c "verdict.py" README.md README.en.md skills/reverse-engineer-and-rebuild/SKILL.md`
Expected: counts of `4`, `4` and `1`.

Run: `python3 -I -m unittest discover -s tests`
Expected: `Ran 60 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add README.md README.en.md skills/reverse-engineer-and-rebuild/SKILL.md
git commit -m "docs: document verdict.py

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Publish the branch

**Files:** none.

- [ ] **Step 1: Final check**

Run: `git status --short` (expect no output) and `git log --oneline main..HEAD` (expect the spec, plan and five feature commits).

Run: `python3 -I -m unittest discover -s tests`
Expected: `Ran 60 tests ... OK`.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/verdict-report
```

A deploy key can push a branch but cannot open a pull request through the GitHub API. Open the pull request from the link that `git push` prints, or with `gh pr create` if a token is configured. Do not merge it.

- [ ] **Step 3: Pull request text**

Title: `Add verdict.py and a unit-test workflow`

Body (plain English):

```
Adds verdict.py, which combines the output of pixel_diff.py, compare_capture.py,
compare_web.py and compare_http.py into one matched / different / unknown
verdict (verdict.json and verdict.md) with exit codes 0, 1 and 2. Adds a GitHub
Actions workflow that runs the unit tests on Python 3.10 and 3.12.

The four existing helpers are unchanged. Unrecognised statuses map to
unknown, so a future change in a helper cannot read as a pass.

Spec: docs/specs/2026-10-31-verdict-report-design.md
Plan: docs/plans/2026-10-31-verdict-report.md

Tests: 60 unit tests pass locally, including two that run the real helpers.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
