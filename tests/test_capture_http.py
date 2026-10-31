import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts" / "capture_http.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("capture_http", SCRIPT)
capture_http = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_http)
from compare_http import compare_cases  # noqa: E402


def scenario():
    return [
        {
            "id": "create",
            "request": {"method": "POST", "path": "/api/items", "body": {"name": "Seed"}},
            "state_probe": {"method": "GET", "path": "/api/items"},
            "bind": {"item_id": "/id"},
            "normalize": {
                "/response/body/id": "item_id",
                "/state_after/body/items/0/id": "item_id",
            },
        },
        {
            "id": "read",
            "request": {"method": "GET", "path": "/api/items/${item_id}"},
            "normalize": {"/response/body/id": "item_id"},
        },
    ]


def run_with_id(item_id):
    def fake_send(request):
        if request["method"] == "POST":
            return {"status": 201, "headers": {"content-type": "application/json"}, "body": {"id": item_id, "name": "Seed"}}
        if request["path"] == "/api/items":
            return {"status": 200, "headers": {"content-type": "application/json"}, "body": {"items": [{"id": item_id, "name": "Seed"}]}}
        if request["path"] == f"/api/items/{item_id}":
            return {"status": 200, "headers": {"content-type": "application/json"}, "body": {"id": item_id, "name": "Seed"}}
        raise AssertionError(request)

    bindings = {}
    pairs = [capture_http.run_case(case, bindings, fake_send) for case in scenario()]
    return {case["id"]: comparable for comparable, case in zip((pair[0] for pair in pairs), scenario())}, pairs


class CaptureHttpTests(unittest.TestCase):
    def test_dynamic_ids_and_post_write_state_compare_by_relationship(self):
        left, pairs = run_with_id(41)
        right, _ = run_with_id(99)
        self.assertEqual(compare_cases(left, right)["status"], "matched_observations")
        self.assertEqual(left["read"]["request"]["path"], "/api/items/${item_id}")
        self.assertEqual(pairs[1][1]["request"]["path"], "/api/items/41")
        self.assertEqual(left["create"]["response"]["body"]["id"], {"$binding": "item_id", "type": "int"})

    def test_inconsistent_state_is_not_normalized_away(self):
        case = scenario()[0]

        def fake_send(request):
            if request["method"] == "POST":
                return {"status": 201, "headers": {}, "body": {"id": 41}}
            return {"status": 200, "headers": {}, "body": {"items": [{"id": 42}]}}

        with self.assertRaisesRegex(capture_http.CaptureError, "does not match bound variable"):
            capture_http.run_case(case, {}, fake_send)

    def test_write_response_is_retained_when_state_probe_fails(self):
        case = scenario()[0]
        partial = {}

        def fake_send(request):
            if request["method"] == "POST":
                return {"status": 201, "headers": {}, "body": {"id": 41}}
            raise capture_http.CaptureError("probe unavailable")

        with self.assertRaisesRegex(capture_http.CaptureError, "probe unavailable"):
            capture_http.run_case(case, {}, fake_send, partial)
        self.assertEqual(partial["response"]["status"], 201)
        self.assertNotIn("state_after", partial)

    def test_partial_capture_manifest_keeps_write_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scenario.json"
            source.write_text(json.dumps({"schema_version": 1, "cases": [scenario()[0]]}))
            output = root / "report"

            def fake_send(request):
                if request["method"] == "POST":
                    return {"status": 201, "headers": {}, "body": {"id": 41}}
                raise capture_http.CaptureError("probe unavailable")

            with patch.object(capture_http, "make_sender", return_value=fake_send):
                code = capture_http.main([str(source), "--base-url", "http://localhost:8080", "--output", str(output)])
            self.assertEqual(code, 1)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertTrue(manifest["failure"]["write_may_have_occurred"])
            self.assertEqual(json.loads((output / "raw-transcript.json").read_text())["cases"][0]["response"]["status"], 201)
            self.assertEqual(json.loads((output / "transcript.json").read_text())["cases"], [])

    def test_remote_origin_requires_explicit_test_flag(self):
        with self.assertRaisesRegex(capture_http.CaptureError, "allow-remote-test"):
            capture_http.validate_base_url("https://example.test", False)
        self.assertEqual(capture_http.validate_base_url("http://127.0.0.1:8080", False), "http://127.0.0.1:8080")
        with self.assertRaises(capture_http.CaptureError):
            capture_http.validate_base_url("http://user:secret@example.test", True)
        with self.assertRaises(capture_http.CaptureError):
            capture_http.validate_request({"method": "GET", "path": "//other.test/x"}, "case")

    def test_invalid_scenario_does_not_create_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scenario.json"
            source.write_text(json.dumps({"schema_version": 1, "cases": [{"id": "write", "request": {"method": "POST", "path": "/x"}, "state_probe": {"method": "DELETE", "path": "/x"}}]}))
            output = root / "private-report"
            result = subprocess.run([sys.executable, str(SCRIPT), str(source), "--base-url", "http://localhost:8080", "--output", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
