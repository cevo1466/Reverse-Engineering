#!/usr/bin/env python3
"""Compare REA screenshot artifacts or PNG files at pixel level, without packages."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

from report_safety import create_report_dir


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PIXELS = 20_000_000
MAX_COMPRESSED_BYTES = 100_000_000


class ImageError(ValueError):
    """An input is not a supported, valid screenshot."""


def _chunks(png: bytes):
    if not png.startswith(PNG_SIGNATURE):
        raise ImageError("Expected a PNG image")
    position = len(PNG_SIGNATURE)
    ended = False
    while position + 12 <= len(png):
        length = struct.unpack_from(">I", png, position)[0]
        end = position + 12 + length
        if end > len(png):
            raise ImageError("Truncated PNG chunk")
        kind = png[position + 4 : position + 8]
        data = png[position + 8 : position + 8 + length]
        expected = struct.unpack_from(">I", png, position + 8 + length)[0]
        actual = zlib.crc32(kind + data) & 0xFFFFFFFF
        if actual != expected:
            raise ImageError(f"Invalid PNG CRC in {kind!r}")
        yield kind, data
        position = end
        if kind == b"IEND":
            ended = True
            break
    if not ended or position != len(png):
        raise ImageError("Missing PNG IEND or trailing bytes")


def decode_png(png: bytes) -> tuple[int, int, bytes]:
    """Decode 8-bit, noninterlaced RGB/RGBA PNG to RGBA bytes."""
    width = height = channels = None
    compressed = bytearray()
    seen_idat = False
    for kind, data in _chunks(png):
        if kind == b"IHDR":
            if width is not None or len(data) != 13:
                raise ImageError("Invalid or duplicate PNG IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise ImageError("PNG dimensions exceed the 20 million pixel limit")
            if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ImageError("Only 8-bit, noninterlaced RGB/RGBA PNG is supported")
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            if channels is None:
                raise ImageError("PNG IDAT appears before IHDR")
            seen_idat = True
            compressed.extend(data)
            if len(compressed) > MAX_COMPRESSED_BYTES:
                raise ImageError("PNG compressed data exceeds the 100 MB limit")
        elif kind == b"IEND":
            break
        elif kind[0] & 0x20 == 0 and kind not in (b"PLTE",):
            raise ImageError(f"Unsupported critical PNG chunk {kind!r}")
    if not seen_idat or width is None or height is None or channels is None:
        raise ImageError("PNG is missing IHDR or IDAT")
    stride = width * channels
    expected_size = height * (stride + 1)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected_size + 1)
        if not decoder.eof or decoder.unused_data or len(raw) != expected_size:
            raise ImageError("PNG decompressed size does not match dimensions")
    except zlib.error as error:
        raise ImageError(f"Invalid PNG compression: {error}") from error
    rgba = bytearray(width * height * 4)
    previous = bytearray(stride)
    offset = 0
    output = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + stride])
        offset += stride
        if filter_type > 4:
            raise ImageError(f"Unsupported PNG row filter {filter_type}")
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                guess = left + above - upper_left
                distances = (abs(guess - left), abs(guess - above), abs(guess - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            else:
                predictor = 0
            row[index] = (row[index] + predictor) & 0xFF
        if channels == 4:
            rgba[output : output + width * 4] = row
        else:
            for index in range(0, stride, 3):
                rgba[output : output + 4] = row[index : index + 3] + b"\xff"
                output += 4
            previous = row
            continue
        output += width * 4
        previous = row
    return width, height, bytes(rgba)


def encode_png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    if len(rgb) != width * height * 3:
        raise ValueError("RGB buffer does not match dimensions")

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    stride = width * 3
    scanlines = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    return PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scanlines, 9)) + chunk(b"IEND", b"")


def _artifact_bytes(artifact: dict) -> bytes:
    if artifact.get("media_type") != "image/png" or not isinstance(artifact.get("data_base64"), str):
        raise ImageError("Expected a REA PNG screenshot artifact")
    try:
        png = base64.b64decode(artifact["data_base64"], validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ImageError("Invalid screenshot base64") from error
    if artifact.get("bytes") != len(png) or artifact.get("sha256") != hashlib.sha256(png).hexdigest():
        raise ImageError("Screenshot byte count or SHA-256 does not match")
    return png


def load_image(path: Path, step_id: str | None = None) -> bytes:
    """Read a PNG, REA artifact JSON, or one step's screenshot from Evidence."""
    data = path.read_bytes()
    if data.startswith(PNG_SIGNATURE):
        return data
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImageError(f"{path}: expected PNG or JSON") from error
    if not isinstance(payload, dict):
        raise ImageError(f"{path}: expected JSON object")
    if payload.get("operation") == "capture_browser_scenario":
        payload = payload.get("normalized_result")
    if isinstance(payload, dict) and isinstance(payload.get("steps"), list):
        steps = payload["steps"]
        matching = [step for step in steps if isinstance(step, dict) and (step_id is None or step.get("step_id") == step_id) and step.get("artifacts", {}).get("screenshot", {}).get("state") == "captured"]
        if len(matching) != 1:
            raise ImageError(f"{path}: expected exactly one captured screenshot for step {step_id or '<unspecified>'}; found {len(matching)}")
        payload = matching[0]["artifacts"]["screenshot"]["value"]
    if not isinstance(payload, dict):
        raise ImageError(f"{path}: expected screenshot artifact")
    if payload.get("state") == "captured":
        payload = payload.get("value")
    if not isinstance(payload, dict):
        raise ImageError(f"{path}: expected screenshot artifact value")
    return _artifact_bytes(payload)


def compare_images(reference: bytes, rebuild: bytes, channel_threshold: int = 0, max_changed_ratio: float = 0.0) -> tuple[dict, bytes | None]:
    if not 0 <= channel_threshold <= 255:
        raise ValueError("channel_threshold must be between 0 and 255")
    if not 0 <= max_changed_ratio <= 1:
        raise ValueError("max_changed_ratio must be between 0 and 1")
    left_width, left_height, left = decode_png(reference)
    right_width, right_height, right = decode_png(rebuild)
    result: dict = {
        "reference": {"width": left_width, "height": left_height, "sha256": hashlib.sha256(reference).hexdigest()},
        "rebuild": {"width": right_width, "height": right_height, "sha256": hashlib.sha256(rebuild).hexdigest()},
        "channel_threshold": channel_threshold,
        "max_changed_ratio": max_changed_ratio,
    }
    if (left_width, left_height) != (right_width, right_height):
        result.update(status="dimension_mismatch", compared_pixels=0, changed_pixels=None, changed_ratio=None, mean_absolute_channel_delta=None, bounding_box=None, passes_threshold=False)
        return result, None
    changed = 0
    total_delta = 0
    maximum_channel_delta = 0
    min_x, min_y, max_x, max_y = left_width, left_height, -1, -1
    heatmap = bytearray(left_width * left_height * 3)
    for pixel in range(left_width * left_height):
        offset = pixel * 4
        deltas = [abs(left[offset + channel] - right[offset + channel]) for channel in range(4)]
        total_delta += sum(deltas)
        maximum = max(deltas)
        maximum_channel_delta = max(maximum_channel_delta, maximum)
        if maximum <= channel_threshold:
            continue
        changed += 1
        x, y = pixel % left_width, pixel // left_width
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x), max(max_y, y)
        heatmap[pixel * 3] = max(64, maximum)
    result.update(
        status="different" if changed else ("within_tolerance" if total_delta else "identical"),
        compared_pixels=left_width * left_height,
        changed_pixels=changed,
        changed_ratio=changed / (left_width * left_height),
        passes_threshold=changed / (left_width * left_height) <= max_changed_ratio,
        maximum_channel_delta=maximum_channel_delta,
        mean_absolute_channel_delta=total_delta / (left_width * left_height * 4),
        bounding_box={"x": min_x, "y": min_y, "width": max_x - min_x + 1, "height": max_y - min_y + 1} if changed else None,
    )
    return result, encode_png_rgb(left_width, left_height, bytes(heatmap))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pixel-diff two REA screenshots or PNG files")
    parser.add_argument("reference", type=Path)
    parser.add_argument("rebuild", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    parser.add_argument("--step-id", help="step ID when inputs are browser Evidence")
    parser.add_argument("--channel-threshold", type=int, default=0, help="per-channel tolerance, 0–255")
    parser.add_argument("--max-changed-ratio", type=float, default=0.0, help="allowed fraction of changed pixels, 0–1")
    args = parser.parse_args(argv)
    try:
        reference = load_image(args.reference, args.step_id)
        rebuild = load_image(args.rebuild, args.step_id)
        result, diff = compare_images(reference, rebuild, args.channel_threshold, args.max_changed_ratio)
        create_report_dir(args.output)
        (args.output / "pixel-diff.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if diff is not None:
            (args.output / "pixel-diff.png").write_bytes(diff)
    except (ImageError, ValueError, OSError) as error:
        parser.error(str(error))
    print(args.output)
    return 0 if result["passes_threshold"] else 1


if __name__ == "__main__":
    sys.exit(main())
