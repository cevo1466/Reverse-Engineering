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
