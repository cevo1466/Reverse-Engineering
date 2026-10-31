import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts" / "rea_once.py"


class ReaOnceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "rea-args.jsonl"
        self.rea = self.root / "rea-stub"
        self.rea.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys, time\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "with Path(os.environ['REA_STUB_LOG']).open('a') as f: f.write(json.dumps(args) + '\\n')\n"
            "if os.environ.get('REA_STUB_SLEEP'): time.sleep(float(os.environ['REA_STUB_SLEEP']))\n"
            "if os.environ.get('REA_STUB_INVALID') == args[0]: print('{broken'); sys.exit(0)\n"
            "if os.environ.get('REA_STUB_FAIL') == args[0]:\n"
            "    print(json.dumps({'error': 'fixture failure', 'code': 'fixture_error', 'category': 'execution_failure', 'message': 'fixture failed', 'details': {'partial_observation': {'operation': args[0]}}}))\n"
            "    sys.exit(7)\n"
            "print(json.dumps({'data': {'operation': args[0], 'provider': {'id': 'stub'}, 'normalized_result': {}, 'limitations': []}}))\n",
            encoding="utf-8",
        )
        self.rea.chmod(0o755)

    def run_cli(self, target, *options, fail=None, invalid=None, sleep=None):
        env = os.environ.copy()
        env["REA_STUB_LOG"] = str(self.log)
        if fail:
            env["REA_STUB_FAIL"] = fail
        if invalid:
            env["REA_STUB_INVALID"] = invalid
        if sleep:
            env["REA_STUB_SLEEP"] = str(sleep)
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(target), "--rea", str(self.rea), "--output", str(self.root / "report"), *options],
            capture_output=True, text=True, env=env, check=False,
        )

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def manifest(self):
        return json.loads((self.root / "report" / "manifest.json").read_text())

    def test_javascript_directory_dispatch_and_evidence(self):
        target = self.root / "application"
        target.mkdir()
        (target / "main.js").write_text("module.exports = 1\n")
        result = self.run_cli(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands()[0][:2], ["analyze-javascript-application", str(target)])
        self.assertEqual(self.manifest()["status"], "succeeded")
        self.assertIn("Evidence operation", (self.root / "report" / "report.md").read_text())
        self.assertEqual((self.root / "report" / ".gitignore").read_text(), "*\n")

    def test_zero_exit_with_invalid_json_is_failure(self):
        target = self.root / "file.bin"
        target.write_bytes(b"fixture")
        result = self.run_cli(target, invalid="inspect-artifact")
        self.assertEqual(result.returncode, 1, result.stderr)
        step = self.manifest()["steps"][0]
        self.assertEqual(step["exit_code"], 0)
        self.assertFalse(step["valid_evidence"])
        self.assertEqual(self.manifest()["status"], "failed")

    def test_command_timeout_retains_partial_output(self):
        target = self.root / "file.bin"
        target.write_bytes(b"fixture")
        result = self.run_cli(target, "--timeout-seconds", "0.05", sleep=1)
        self.assertEqual(result.returncode, 1, result.stderr)
        step = self.manifest()["steps"][0]
        self.assertTrue(step["timed_out"])
        self.assertIsNone(step["exit_code"])
        self.assertIn("timed out", (self.root / "report" / step["stderr"]).read_text())

    def test_unknown_installation_directory_uses_desktop_inventory(self):
        target = self.root / "game-install"
        target.mkdir()
        (target / "game.dat").write_bytes(b"fixture")
        result = self.run_cli(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands()[0][0], "inspect-desktop-application")

    def test_managed_pe_is_detected_by_header(self):
        target = self.root / "unknown.exe"
        data = bytearray(0x80 + 24 + 224)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", data, 0x80 + 20, 224)
        struct.pack_into("<H", data, 0x80 + 24, 0x10B)
        struct.pack_into("<II", data, 0x80 + 24 + 96 + 14 * 8, 0x2000, 0x48)
        target.write_bytes(data)
        result = self.run_cli(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([command[0] for command in self.commands()], ["inspect-managed-artifact", "inspect-managed-members"])
        self.assertEqual(self.manifest()["route"], "managed")
        self.assertEqual(len(self.manifest()["sha256"]), 64)

    def test_native_provider_and_missing_provider_boundary(self):
        target = self.root / "program"
        target.write_bytes(b"\x7fELF" + b"\0" * 32)
        result = self.run_cli(target, "--provider", "ghidra")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands()[0][:4], ["analyze", str(target), "--provider", "ghidra"])

    def test_native_analysis_failure_retains_inventory_fallback(self):
        target = self.root / "program"
        target.write_bytes(b"\x7fELF" + b"\0" * 32)
        result = self.run_cli(target, fail="analyze")
        self.assertEqual(result.returncode, 2)
        self.assertEqual([command[0] for command in self.commands()], ["analyze", "inspect-artifact"])
        self.assertEqual(self.manifest()["status"], "partial")

    def test_apk_retains_failure_and_stops_following_steps(self):
        target = self.root / "app.apk"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("AndroidManifest.xml", "fixture")
        result = self.run_cli(target, "--android-code", fail="inspect-apk-signature")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([command[0] for command in self.commands()], ["inspect-artifact", "inspect-apk-signature"])
        self.assertEqual(self.manifest()["status"], "failed")
        self.assertTrue((self.root / "report" / "inspect-artifact.json").exists())
        self.assertIn("REA error: `fixture_error`", (self.root / "report" / "report.md").read_text())

    def test_web_scenario_captures_content_and_refuses_overwrite(self):
        browser = self.root / "chromium"
        browser.write_text("fixture")
        result = self.run_cli("https://example.com/page", "--browser-executable", str(browser))
        self.assertEqual(result.returncode, 0, result.stderr)
        scenario = json.loads((self.root / "report" / "browser-scenario.json").read_text())
        self.assertEqual(scenario["capture"]["at_end"], ["dom", "accessibility", "screenshot", "url"])
        self.assertEqual([command[0] for command in self.commands()], ["capture-browser-scenario"])
        second = self.run_cli("https://example.com/page", "--browser-executable", str(browser))
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(len(self.commands()), 1)


if __name__ == "__main__":
    unittest.main()
