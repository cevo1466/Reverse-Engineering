import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts" / "compare_http.py"


def transcript(cases):
    return {"schema_version": 1, "cases": cases}


def case(case_id="create", request=None, response=None, **snapshots):
    return {
        "id": case_id,
        "request": request or {"method": "POST", "path": "/api/items", "body": {"title": "A"}},
        "response": response or {"status": 201, "headers": {"Content-Type": "application/json"}, "body": {"id": 1, "title": "A"}},
        **snapshots,
    }


class CompareHttpTests(unittest.TestCase):
    def run_compare(self, reference, rebuild):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        before, after, output = root / "before.json", root / "after.json", root / "result"
        before.write_text(json.dumps(reference), encoding="utf-8")
        after.write_text(json.dumps(rebuild), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), str(before), str(after), "--output", str(output)],
            capture_output=True, text=True, check=False,
        )
        report = json.loads((output / "comparison.json").read_text(encoding="utf-8")) if output.exists() else None
        return completed, report, output

    def test_semantic_json_and_header_case_match(self):
        before = case(state_after={"items": [{"id": 1, "title": "A"}]})
        after = case(response={"body": {"title": "A", "id": 1.0}, "headers": {"content-type": "application/json"}, "status": 201},
                     state_after={"items": [{"title": "A", "id": 1.0}]})
        completed, report, output = self.run_compare(transcript([before]), transcript([after]))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["status"], "matched_observations")
        self.assertEqual(report["cases"][0]["checks"]["effects"], "not_selected")
        self.assertIn("no HTTP requests were sent", (output / "comparison.md").read_text(encoding="utf-8"))

    def test_state_transition_difference_fails_even_when_response_matches(self):
        before = case(state_after={"items": [{"id": 1, "title": "A"}]})
        after = case(state_after={"items": []})
        completed, report, _ = self.run_compare(transcript([before]), transcript([after]))
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(report["status"], "different")
        self.assertEqual(report["cases"][0]["checks"]["state_after"], "different")
        self.assertTrue(any(item["path"].startswith("/state_after/items") for item in report["cases"][0]["differences"]))
        self.assertNotIn("title", json.dumps(report["cases"][0]["differences"]))

    def test_missing_case_is_inconclusive(self):
        before = transcript([case(), case("read", {"method": "GET", "path": "/api/items/1"})])
        after = transcript([case()])
        completed, report, _ = self.run_compare(before, after)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["cases"][1]["id"], "read")
        self.assertEqual(report["cases"][1]["status"], "inconclusive")

    def test_mismatched_request_does_not_compare_responses(self):
        before = case(request={"method": "GET", "path": "/api/items", "query": {"page": 1}})
        after = case(request={"method": "GET", "path": "/api/items", "query": {"page": 2}})
        completed, report, _ = self.run_compare(transcript([before]), transcript([after]))
        self.assertEqual(completed.returncode, 1)
        row = report["cases"][0]
        self.assertEqual(row["request"], "different")
        self.assertEqual(row["checks"]["body"], "unknown")
        self.assertIn("/request/query/page", [item["path"] for item in row["differences"]])

    def test_missing_selected_response_body_is_unknown(self):
        before = case()
        after = case(response={"status": 201, "headers": {"content-type": "application/json"}})
        completed, report, _ = self.run_compare(transcript([before]), transcript([after]))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["cases"][0]["checks"]["body"], "unknown")

    def test_duplicate_case_id_rejected_before_output_creation(self):
        completed, report, output = self.run_compare(transcript([case(), case()]), transcript([case()]))
        self.assertEqual(completed.returncode, 2)
        self.assertIsNone(report)
        self.assertFalse(output.exists())
        self.assertIn("unique", completed.stderr)


if __name__ == "__main__":
    unittest.main()
