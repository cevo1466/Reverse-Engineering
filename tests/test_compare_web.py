import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts" / "compare_web.py"


def evidence(origin: str, digest: str) -> dict:
    return {
        "evidence_id": f"ev_{digest}",
        "operation": "capture_browser_scenario",
        "normalized_result": {
            "browser": {"product": "Chromium"},
            "scenario": {"environment": {"viewport": {"width": 1280, "height": 720}}},
            "duration_ms": 1,
            "steps": [{
                "step_id": "clicked",
                "action": "click",
                "status": "completed",
                "artifacts": {
                    "screenshot": {"state": "captured", "value": {"sha256": digest}},
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
            self.assertIn("| clicked | same | same | same | same |", report)
            self.assertEqual(json.loads((output / "reference-normalized.json").read_text())["steps"][0]["step_id"], "clicked")


if __name__ == "__main__":
    unittest.main()
