import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts" / "compare_web.py"


def screenshot() -> dict:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")
    return {"sha256": hashlib.sha256(png).hexdigest(), "bytes": len(png), "media_type": "image/png", "data_base64": base64.b64encode(png).decode()}


def evidence(origin: str, digest: str) -> dict:
    return {
        "evidence_id": f"ev_{digest}",
        "operation": "capture_browser_scenario",
        "parameters": {"environment": {"viewport": {"width": 1280, "height": 720}}},
        "normalized_result": {
            "browser": {"product": "Chromium"},
            "scenario": {"environment": {"viewport": {"width": 1280, "height": 720}}},
            "duration_ms": 1,
            "steps": [{
                "step_id": "clicked",
                "action": "click",
                "status": "completed",
                "artifacts": {
                    "screenshot": {"state": "captured", "value": screenshot()},
                    "accessibility": {"state": "captured", "value": {"sha256": "same"}},
                    "dom": {"state": "captured", "value": {"sha256": digest}},
                    "url": {"state": "captured", "value": {"url": f"{origin}/result"}},
                },
            }],
            "events": {},
            "completeness": {},
        },
    }


class CompareWebTests(unittest.TestCase):
    def test_missing_viewport_is_unknown_even_on_both_sides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            rebuild = root / "rebuild.json"
            for path, origin in ((original, "https://example.test"), (rebuild, "http://127.0.0.1:3000")):
                capture = evidence(origin, "equal")
                del capture["parameters"]["environment"]["viewport"]
                del capture["normalized_result"]["scenario"]["environment"]["viewport"]
                path.write_text(json.dumps(capture))
            output = root / "comparison"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(original), str(rebuild), "--no-rea", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            visual = json.loads((output / "visual-summary.json").read_text())
            self.assertEqual(visual["status"], "unknown")
            self.assertIn("Viewport settings", visual["steps"][0]["reason"])

    def test_same_step_id_with_different_actions_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            rebuild = root / "rebuild.json"
            original.write_text(json.dumps(evidence("https://example.test", "equal")))
            changed = evidence("http://127.0.0.1:3000", "equal")
            changed["normalized_result"]["steps"][0]["action"] = "fill"
            rebuild.write_text(json.dumps(changed))
            output = root / "comparison"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(original), str(rebuild), "--no-rea", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            visual = json.loads((output / "visual-summary.json").read_text())
            self.assertEqual(visual["status"], "unknown")
            self.assertIn("Actions differ", visual["steps"][0]["reason"])

    def test_rea_timeout_or_invalid_output_does_not_pass(self):
        for body in ("import time; time.sleep(1)", "print('{broken')"):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original = root / "original.json"
                rebuild = root / "rebuild.json"
                original.write_text(json.dumps(evidence("https://example.test", "equal")))
                rebuild.write_text(json.dumps(evidence("https://example.test", "equal")))
                rea = root / "rea-stub"
                rea.write_text("#!/usr/bin/env python3\n" + body + "\n")
                rea.chmod(0o755)
                output = root / "comparison"
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), str(original), str(rebuild), "--rea", str(rea), "--timeout-seconds", "0.05", "--output", str(output)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("REA", (output / "comparison.md").read_text())

    def test_direct_comparison_runs_without_rea_or_npx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            rebuild = root / "rebuild.json"
            original.write_text(json.dumps(evidence("https://example.test", "equal")))
            rebuild.write_text(json.dumps(evidence("http://127.0.0.1:3000", "equal")))
            output = root / "comparison"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(original), str(rebuild), "--no-rea", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((output / "rea-comparison.json").exists())
            self.assertIn("Skipped by --no-rea", (output / "comparison.md").read_text())
            self.assertEqual(json.loads((output / "visual-summary.json").read_text())["status"], "within_threshold")
            self.assertEqual((output / ".gitignore").read_text(), "*\n")

    def test_full_evidence_is_unwrapped_for_rea_and_cross_origin_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            rebuild = root / "rebuild.json"
            original.write_text(json.dumps(evidence("https://example.test", "equal")))
            rebuild.write_text(json.dumps(evidence("http://127.0.0.1:3000", "equal")))
            rea = root / "rea-stub"
            rea.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "assert sys.argv[1] == 'compare-web-captures'\n"
                "for path in sys.argv[2:4]:\n"
                "    capture = json.load(open(path))\n"
                "    assert 'browser' in capture and 'evidence_id' not in capture\n"
                "print(json.dumps({'normalized_result': {'overall_status': 'changed', 'alignment': {'status': 'partial', 'failures': [{'code': 'capture_context_mismatch', 'reason': 'different origin'}]}}}))\n"
            )
            rea.chmod(0o755)
            output = root / "comparison"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(original), str(rebuild), "--rea", str(rea), "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = (output / "comparison.md").read_text()
            self.assertIn("capture_context_mismatch", report)
            self.assertIn("| clicked | identical | 0.0000% | same | same | same | same |", report)
            self.assertTrue((output / "visual" / "step-000" / "pixel-diff.png").is_file())
            self.assertEqual(json.loads((output / "reference-normalized.json").read_text())["steps"][0]["step_id"], "clicked")


if __name__ == "__main__":
    unittest.main()
