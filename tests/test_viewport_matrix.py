import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("viewport_matrix", SCRIPTS / "viewport_matrix.py")
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)

from pixel_diff import encode_png_rgb  # noqa: E402
from report_safety import create_report_dir  # noqa: E402
from capture_web import CaptureError  # noqa: E402


SCENARIO = {"schema_version": 1, "viewport": {"width": 10, "height": 10}, "steps": [{"id": "initial"}, {"id": "after"}]}


def fake_capture(colors, fail_on=()):
    """Return a capture function that writes solid-colour PNGs; colour is chosen by URL."""
    def capture(url, scenario, output, browser, timeout, allow_remote_actions=False):
        width, height = scenario["viewport"]["width"], scenario["viewport"]["height"]
        if (url, width) in fail_on:
            raise CaptureError("browser exploded")
        create_report_dir(output)
        records = []
        for step in scenario["steps"]:
            png = encode_png_rgb(width, height, bytes(colors[url]) * (width * height))
            (output / f"{step['id']}.png").write_bytes(png)
            records.append({"id": step["id"], "url": url, "screenshot": f"{step['id']}.png", "sha256": hashlib.sha256(png).hexdigest(), "width": width, "height": height})
        manifest = {"schema_version": 1, "start_url": url, "viewport": scenario["viewport"], "browser": "fake", "steps": records}
        (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest
    return capture


class ParseViewportTests(unittest.TestCase):
    def test_parses_names_and_sizes(self):
        self.assertEqual(matrix.parse_viewports("375x812, 1440x900"), [("375x812", 375, 812), ("1440x900", 1440, 900)])

    def test_rejects_bad_values(self):
        for text in ("", "375", "0x10", "4001x10", "10x10,10x10", "axb", "10x10,"):
            with self.assertRaises(ValueError, msg=text):
                matrix.parse_viewports(text)

    def test_with_viewport_does_not_mutate_the_original(self):
        changed = matrix.with_viewport(SCENARIO, 4, 5)
        self.assertEqual(changed["viewport"], {"width": 4, "height": 5})
        self.assertEqual(SCENARIO["viewport"], {"width": 10, "height": 10})


class RunMatrixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "matrix"

    def run_matrix(self, colors, fail_on=(), viewports=(("4x3", 4, 3), ("6x5", 6, 5))):
        return matrix.run_matrix("http://ref/", "http://reb/", SCENARIO, SCENARIO, list(viewports), self.output, "fake", 5, False, 0, 0.0,
                                 capture_fn=fake_capture(colors, fail_on))

    def test_matching_pages_at_every_viewport(self):
        verdict = self.run_matrix({"http://ref/": (1, 2, 3), "http://reb/": (1, 2, 3)})
        self.assertEqual(verdict["overall"], "matched")
        self.assertEqual(sorted(case["case_id"] for case in verdict["cases"]), ["4x3/after", "4x3/initial", "6x5/after", "6x5/initial"])
        self.assertEqual(verdict["viewports"], ["4x3", "6x5"])
        self.assertTrue((self.output / "verdict.md").is_file())
        self.assertTrue((self.output / ".gitignore").is_file())
        self.assertTrue((self.output / "comparison" / "4x3" / "comparison.json").is_file())

    def test_difference_is_attributed_to_the_viewport(self):
        verdict = self.run_matrix({"http://ref/": (1, 2, 3), "http://reb/": (9, 9, 9)})
        self.assertEqual(verdict["overall"], "different")
        self.assertEqual({case["status"] for case in verdict["cases"]}, {"different"})

    def test_capture_failure_is_unknown_for_that_viewport_only(self):
        verdict = self.run_matrix({"http://ref/": (1, 2, 3), "http://reb/": (1, 2, 3)}, fail_on={("http://reb/", 6)})
        statuses = {case["case_id"]: case["status"] for case in verdict["cases"]}
        self.assertEqual(statuses["6x5/rebuild-capture"], "unknown")
        self.assertEqual(statuses["4x3/initial"], "matched")
        self.assertEqual(verdict["overall"], "unknown")

    def test_scope_names_the_limit(self):
        verdict = self.run_matrix({"http://ref/": (1, 2, 3), "http://reb/": (1, 2, 3)})
        self.assertIn("listed viewports", verdict["scope"])


class CommandTests(unittest.TestCase):
    def test_existing_output_directory_exits_two(self):
        with tempfile.TemporaryDirectory() as root:
            scenario = Path(root) / "s.json"
            scenario.write_text(json.dumps(SCENARIO), encoding="utf-8")
            chrome = Path(root) / "chrome"
            chrome.write_text("#!/bin/sh\n", encoding="utf-8")
            (Path(root) / "out").mkdir()
            code = matrix.main([str(scenario), "--reference-url", "http://127.0.0.1:1/", "--rebuild-url", "http://127.0.0.1:2/",
                                "--output", str(Path(root) / "out"), "--chrome", str(chrome)])
            self.assertEqual(code, 2)

    def test_bad_viewport_exits_two(self):
        with tempfile.TemporaryDirectory() as root:
            scenario = Path(root) / "s.json"
            scenario.write_text(json.dumps(SCENARIO), encoding="utf-8")
            code = matrix.main([str(scenario), "--reference-url", "http://127.0.0.1:1/", "--rebuild-url", "http://127.0.0.1:2/",
                                "--viewports", "nope", "--output", str(Path(root) / "out")])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
