import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts" / "pixel_diff.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("pixel_diff", SCRIPT)
pixel_diff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pixel_diff)


def png(width: int, height: int, scanlines: bytes, color: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return pixel_diff.PNG_SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


class PixelDiffTests(unittest.TestCase):
    def test_pixel_metrics_bbox_and_heatmap(self):
        left = png(2, 1, b"\x00\x00\x00\x00\x00\x00\x00")
        right = png(2, 1, b"\x00\x00\x00\x00\x0a\x14\x1e")
        result, heatmap = pixel_diff.compare_images(left, right, 15)
        self.assertEqual(result["status"], "different")
        self.assertEqual(result["changed_pixels"], 1)
        self.assertEqual(result["changed_ratio"], 0.5)
        self.assertFalse(result["passes_threshold"])
        self.assertEqual(result["mean_absolute_channel_delta"], 7.5)
        self.assertEqual(result["maximum_channel_delta"], 30)
        self.assertEqual(result["bounding_box"], {"x": 1, "y": 0, "width": 1, "height": 1})
        self.assertEqual(pixel_diff.decode_png(heatmap)[2][-4:], b"\x40\x00\x00\xff")
        within, _ = pixel_diff.compare_images(left, right, 30)
        self.assertEqual(within["status"], "within_tolerance")
        self.assertEqual(within["changed_pixels"], 0)
        allowed, _ = pixel_diff.compare_images(left, right, 15, 0.5)
        self.assertEqual(allowed["status"], "different")
        self.assertTrue(allowed["passes_threshold"])

    def test_filters_and_rgba(self):
        # Two 1-pixel rows; second uses Up filtering against the first.
        picture = png(1, 2, b"\x00\x0a\x14\x1e\xff\x02\x01\x02\x03\x00", color=6)
        self.assertEqual(pixel_diff.decode_png(picture), (1, 2, b"\x0a\x14\x1e\xff\x0b\x16\x21\xff"))
        self.assertEqual(pixel_diff.compare_images(picture, picture)[0]["status"], "identical")

    def test_metadata_does_not_change_pixel_verdict(self):
        image = png(1, 1, b"\x00\x0a\x14\x1e")
        metadata = b"Comment\x00same pixels"
        kind = b"tEXt"
        extra = struct.pack(">I", len(metadata)) + kind + metadata + struct.pack(">I", zlib.crc32(kind + metadata) & 0xFFFFFFFF)
        with_metadata = image[:-12] + extra + image[-12:]
        result, _ = pixel_diff.compare_images(image, with_metadata)
        self.assertNotEqual(image, with_metadata)
        self.assertEqual(result["status"], "identical")
        self.assertTrue(result["passes_threshold"])

    def test_dimension_mismatch_and_unsupported_png(self):
        small = png(1, 1, b"\x00\x00\x00\x00")
        wide = png(2, 1, b"\x00\x00\x00\x00\x00\x00\x00")
        result, heatmap = pixel_diff.compare_images(small, wide)
        self.assertEqual(result["status"], "dimension_mismatch")
        self.assertIsNone(heatmap)
        palette = png(1, 1, b"\x00\x00", color=3)
        with self.assertRaisesRegex(pixel_diff.ImageError, "Only 8-bit"):
            pixel_diff.decode_png(palette)
        broken = bytearray(small)
        broken[-5] ^= 1
        with self.assertRaisesRegex(pixel_diff.ImageError, "CRC"):
            pixel_diff.decode_png(bytes(broken))

    def test_rea_evidence_and_cli_output(self):
        image = png(1, 1, b"\x00\xff\xff\xff")
        artifact = {
            "sha256": hashlib.sha256(image).hexdigest(),
            "bytes": len(image),
            "media_type": "image/png",
            "data_base64": base64.b64encode(image).decode(),
        }
        evidence = {
            "operation": "capture_browser_scenario",
            "normalized_result": {"steps": [{"step_id": "ready", "artifacts": {"screenshot": {"state": "captured", "value": artifact}}}]},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.json"
            source.write_text(json.dumps(evidence))
            self.assertEqual(pixel_diff.load_image(source, "ready"), image)
            output = root / "output"
            run = subprocess.run(
                [sys.executable, str(SCRIPT), str(source), str(source), "--step-id", "ready", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads((output / "pixel-diff.json").read_text())["status"], "identical")
            self.assertTrue((output / "pixel-diff.png").is_file())
            evidence["normalized_result"]["steps"][0]["artifacts"]["screenshot"]["value"]["sha256"] = "wrong"
            source.write_text(json.dumps(evidence))
            with self.assertRaisesRegex(pixel_diff.ImageError, "SHA-256"):
                pixel_diff.load_image(source, "ready")


if __name__ == "__main__":
    unittest.main()
