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


if __name__ == "__main__":
    unittest.main()
