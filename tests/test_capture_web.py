import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import capture_web  # noqa: E402
import compare_capture  # noqa: E402
from pixel_diff import encode_png_rgb  # noqa: E402


def scenario():
    return {"schema_version": 1, "viewport": {"width": 390, "height": 844}, "steps": [
        {"id": "initial", "ready_selector": "main"},
        {"id": "added", "actions": [{"type": "fill", "selector": "input", "value": "A note"}, {"type": "click", "selector": "button"}], "ready_selector": ".item"},
    ]}


def write_capture(root: Path, png: bytes, viewport: dict | None = None, url: str = "http://localhost:8000/") -> Path:
    root.mkdir()
    (root / "initial.png").write_bytes(png)
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    manifest = {"schema_version": 1, "viewport": viewport or {"width": width, "height": height}, "steps": [{"id": "initial", "url": url, "screenshot": "initial.png", "sha256": hashlib.sha256(png).hexdigest(), "width": width, "height": height}]}
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


class CaptureWebTests(unittest.TestCase):
    def test_scenario_validation_and_remote_action_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scenario.json"
            source.write_text(json.dumps(scenario()))
            self.assertEqual(capture_web.read_scenario(source), scenario())
            self.assertTrue(capture_web.validate_url("http://127.0.0.1:8000/"))
            self.assertFalse(capture_web.validate_url("https://example.test/"))
            with self.assertRaisesRegex(capture_web.CaptureError, "--allow-remote-actions"):
                capture_web.ensure_remote_action_allowed("https://example.test/", False)
            capture_web.ensure_remote_action_allowed("https://example.test/", True)
            run = subprocess.run([sys.executable, str(SCRIPTS / "capture_web.py"), str(source), "--url", "https://example.test/", "--output", str(Path(directory) / "out")], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn("--allow-remote-actions", run.stderr)
            self.assertFalse((Path(directory) / "out").exists())

    def test_invalid_scenarios_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scenario.json"
            for bad in [
                {**scenario(), "schema_version": True},
                {**scenario(), "viewport": {"width": 0, "height": 844}},
                {**scenario(), "steps": [{"id": "../escape"}]},
                {**scenario(), "steps": [{"id": "same"}, {"id": "same"}]},
                {**scenario(), "steps": [{"id": "x", "actions": [{"type": "fill", "selector": "input"}]}]},
            ]:
                source.write_text(json.dumps(bad))
                with self.assertRaises(capture_web.CaptureError):
                    capture_web.read_scenario(source)

    def test_compare_detects_pixels_viewport_and_tampering(self):
        black = encode_png_rgb(2, 1, bytes([0, 0, 0] * 2))
        red = encode_png_rgb(2, 1, bytes([255, 0, 0, 0, 0, 0]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = write_capture(root / "left", black, url="http://localhost:8000/")
            right = write_capture(root / "right", black, url="http://localhost:9000/")
            summary = compare_capture.compare_captures(left, right, root / "same", 0, 0.0)
            self.assertEqual(summary["overall"], "matched_visual_observations")
            self.assertEqual(summary["steps"][0]["visual"]["changed_pixels"], 0)
            self.assertEqual((root / "same" / ".gitignore").read_text(), "*\n")
            (right.parent / "initial.png").write_bytes(red)
            with self.assertRaisesRegex(compare_capture.ComparisonError, "digest mismatch"):
                compare_capture.compare_captures(left, right, root / "tampered", 0, 0.0)
            data = json.loads(right.read_text())
            data["steps"][0]["sha256"] = hashlib.sha256(red).hexdigest()
            right.write_text(json.dumps(data))
            different = compare_capture.compare_captures(left, right, root / "different", 0, 0.0)
            self.assertEqual(different["overall"], "different_or_incomplete")
            self.assertEqual(different["steps"][0]["visual"]["changed_pixels"], 1)
            data["viewport"] = {"width": 3, "height": 1}
            right.write_text(json.dumps(data))
            wrong_viewport = compare_capture.compare_captures(left, right, root / "wrong-viewport", 0, 1.0)
            self.assertEqual(wrong_viewport["overall"], "different_or_incomplete")
            self.assertFalse(wrong_viewport["steps"][0]["viewport_matches"])


if __name__ == "__main__":
    unittest.main()
