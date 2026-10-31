import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture_scroll = load("capture_scroll")
compare_scroll = load("compare_scroll")
verdict = load("verdict")

from capture_web import CaptureError  # noqa: E402
from pixel_diff import encode_png_rgb  # noqa: E402

BOX = {"x": 10.0, "y": 100.0, "width": 50.0, "height": 20.0, "opacity": 1.0, "transform": "none"}


def spec(**overrides):
    base = {"schema_version": 1, "viewport": {"width": 4, "height": 3}, "probes": [".box"], "samples": [{"id": "top"}, {"id": "mid", "scroll": 0.5, "wait_ms": 100, "hover": ".box"}]}
    base.update(overrides)
    return base


class SpecTests(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "spec.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return capture_scroll.read_spec(path)

    def test_valid_spec(self):
        self.assertEqual(self.read(spec())["samples"][1]["scroll"], 0.5)

    def test_invalid_specs_are_rejected(self):
        bad = [
            spec(schema_version=2), spec(extra=1), spec(viewport={"width": 0, "height": 3}), spec(probes=[".a", ".a"]), spec(probes=[""]),
            spec(samples=[]), spec(samples=[{"id": "a"}, {"id": "a"}]), spec(samples=[{"id": "../x"}]), spec(samples=[{"id": "a", "scroll": 1.5}]),
            spec(samples=[{"id": "a", "scroll": True}]), spec(samples=[{"id": "a", "wait_ms": -1}]), spec(samples=[{"id": "a", "wait_ms": 1.5}]),
            spec(samples=[{"id": "a", "hover": ""}]), spec(samples=[{"id": "a", "other": 1}]),
        ]
        for data in bad:
            with self.assertRaises(CaptureError, msg=json.dumps(data)):
                self.read(data)


class ProbeTests(unittest.TestCase):
    def test_equal_and_within_tolerance(self):
        moved = {**BOX, "y": 100.5, "opacity": 0.99}
        self.assertEqual(compare_scroll.compare_probes({".box": BOX}, {".box": moved}, 1.0, 0.02), [])

    def test_geometry_opacity_and_presence(self):
        self.assertEqual(len(compare_scroll.compare_probes({".box": BOX}, {".box": {**BOX, "y": 140.0}}, 1.0, 0.02)), 1)
        self.assertIn("opacity", compare_scroll.compare_probes({".box": BOX}, {".box": {**BOX, "opacity": 0.4}}, 1.0, 0.02)[0])
        self.assertIn("only in the rebuild", compare_scroll.compare_probes({".box": None}, {".box": BOX}, 1.0, 0.02)[0])
        self.assertEqual(compare_scroll.compare_probes({".box": None}, {".box": None}, 1.0, 0.02), [])

    def test_transforms(self):
        differ = compare_scroll.transforms_differ
        self.assertFalse(differ("none", "none", 1.0, 0.02))
        self.assertTrue(differ("none", "matrix(1, 0, 0, 1, 0, 40)", 1.0, 0.02))
        self.assertFalse(differ("matrix(1, 0, 0, 1, 0, 40)", "matrix(1, 0, 0, 1, 0, 40.4)", 1.0, 0.02))
        self.assertTrue(differ("matrix(1, 0, 0, 1, 0, 40)", "matrix(1, 0, 0, 1, 0, 60)", 1.0, 0.02))
        self.assertTrue(differ("matrix(1, 0, 0, 1, 0, 0)", "matrix(1.2, 0, 0, 1.2, 0, 0)", 1.0, 0.02))
        self.assertTrue(differ("matrix(1, 0, 0, 1, 0, 0)", "matrix3d(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)", 1.0, 0.02))


def write_capture(root, name, colors, probes=None, scroll_height=1000, scroll_y=(0, 500)):
    directory = Path(root) / name
    directory.mkdir()
    samples = []
    for (sample_id, color), y in zip(colors.items(), scroll_y):
        png = encode_png_rgb(4, 3, bytes(color) * 12)
        (directory / f"{sample_id}.png").write_bytes(png)
        samples.append({"id": sample_id, "url": "http://x/", "screenshot": f"{sample_id}.png", "sha256": hashlib.sha256(png).hexdigest(), "width": 4, "height": 3,
                        "scroll_y": y, "scroll_height": scroll_height, "probes": probes or {".box": BOX}})
    (directory / "manifest.json").write_text(json.dumps({"schema_version": 1, "kind": "scroll_capture", "start_url": "http://x/", "viewport": {"width": 4, "height": 3}, "samples": samples}), encoding="utf-8")
    return directory / "manifest.json"


class CompareScrollTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def compare(self, reference, rebuild):
        return compare_scroll.compare_scroll(reference, rebuild, self.root / "out", 0, 0.0, 1.0, 0.02)

    def test_identical_captures_match(self):
        colors = {"top": (1, 2, 3), "mid": (4, 5, 6)}
        summary = self.compare(write_capture(self.root, "a", colors), write_capture(self.root, "b", colors))
        self.assertEqual(summary["overall"], "matched")
        self.assertTrue((self.root / "out" / "comparison.md").is_file())
        self.assertTrue((self.root / "out" / ".gitignore").is_file())

    def test_pixel_probe_and_height_differences(self):
        colors = {"top": (1, 2, 3), "mid": (4, 5, 6)}
        reference = write_capture(self.root, "a", colors)
        rebuild = write_capture(self.root, "b", {"top": (1, 2, 3), "mid": (9, 9, 9)}, probes={".box": {**BOX, "y": 300.0}}, scroll_height=1400)
        summary = self.compare(reference, rebuild)
        rows = {row["id"]: row for row in summary["steps"]}
        self.assertEqual(summary["overall"], "different")
        self.assertEqual(rows["mid"]["status"], "different")
        self.assertIn("pixels", rows["mid"]["reason"])
        self.assertIn("scroll_height", rows["top"]["reason"])
        self.assertIn(".box: y", rows["top"]["reason"])

    def test_sample_order_mismatch_is_an_error(self):
        reference = write_capture(self.root, "a", {"top": (1, 2, 3), "mid": (4, 5, 6)})
        rebuild = write_capture(self.root, "b", {"mid": (4, 5, 6), "top": (1, 2, 3)})
        with self.assertRaises(compare_scroll.ComparisonError):
            self.compare(reference, rebuild)

    def test_tampered_screenshot_is_an_error(self):
        colors = {"top": (1, 2, 3), "mid": (4, 5, 6)}
        reference = write_capture(self.root, "a", colors)
        rebuild = write_capture(self.root, "b", colors)
        (rebuild.parent / "top.png").write_bytes(encode_png_rgb(4, 3, bytes((7, 7, 7)) * 12))
        with self.assertRaises(compare_scroll.ComparisonError):
            self.compare(reference, rebuild)

    def test_verdict_reads_scroll_comparison(self):
        colors = {"top": (1, 2, 3), "mid": (4, 5, 6)}
        self.compare(write_capture(self.root, "a", colors), write_capture(self.root, "b", {"top": (1, 2, 3), "mid": (9, 9, 9)}))
        data = json.loads((self.root / "out" / "comparison.json").read_text(encoding="utf-8"))
        source = self.root / "out" / "comparison.json"
        self.assertEqual(verdict.detect_kind(data, source), "scroll")
        statuses = {case["case_id"]: case["status"] for case in verdict.cases_from("scroll", data, source)}
        self.assertEqual(statuses, {"scroll:top": "matched", "scroll:mid": "different"})
        self.assertEqual(verdict.main([str(self.root / "out"), "--output", str(self.root / "verdict")]), 1)


if __name__ == "__main__":
    unittest.main()
