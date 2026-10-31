#!/usr/bin/env python3
"""Capture repeatable browser journeys as PNG, DOM, and accessibility evidence.

Uses a locally installed Chromium browser and Python's standard library.
The browser runs in a disposable profile; no target state is reset implicitly.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

from report_safety import create_report_dir


MAX_MESSAGE_BYTES = 32_000_000
MAX_SCREENSHOT_BYTES = 20_000_000
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ACTIONS = {"click", "fill", "press", "wait_for"}


class CaptureError(ValueError):
    """A scenario or browser observation could not be captured reliably."""


def is_loopback(hostname: str) -> bool:
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return hostname.lower() == "localhost"


def validate_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise CaptureError("URL must be HTTP(S), without credentials or a fragment")
    return is_loopback(parsed.hostname)


def ensure_remote_action_allowed(url: str, allowed: bool) -> None:
    if not validate_url(url) and not allowed:
        raise CaptureError("Remote interaction requires --allow-remote-actions on an authorized test target")


def read_scenario(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise CaptureError(f"Non-JSON numeric constant: {value}")

    data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(data, dict) or set(data) != {"schema_version", "viewport", "steps"} or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise CaptureError("Scenario must contain schema_version 1, viewport, and steps")
    viewport = data["viewport"]
    if not isinstance(viewport, dict) or set(viewport) != {"width", "height"}:
        raise CaptureError("viewport must contain width and height")
    for name, limit in (("width", 4000), ("height", 4000)):
        value = viewport[name]
        if type(value) is not int or not 1 <= value <= limit:
            raise CaptureError(f"viewport.{name} must be an integer between 1 and {limit}")
    steps = data["steps"]
    if not isinstance(steps, list) or not steps or len(steps) > 100:
        raise CaptureError("steps must contain 1 to 100 entries")
    seen: set[str] = set()
    for index, step in enumerate(steps):
        context = f"steps[{index}]"
        if not isinstance(step, dict) or set(step) - {"id", "actions", "ready_selector"}:
            raise CaptureError(f"{context}: unsupported fields")
        step_id = step.get("id")
        if not isinstance(step_id, str) or not ID_PATTERN.fullmatch(step_id) or step_id in seen:
            raise CaptureError(f"{context}.id must be a unique safe filename")
        seen.add(step_id)
        actions = step.get("actions", [])
        if not isinstance(actions, list) or len(actions) > 30:
            raise CaptureError(f"{context}.actions must contain at most 30 actions")
        ready = step.get("ready_selector")
        if ready is not None and (not isinstance(ready, str) or not ready or len(ready) > 500):
            raise CaptureError(f"{context}.ready_selector must be a nonempty CSS selector")
        for action_index, action in enumerate(actions):
            label = f"{context}.actions[{action_index}]"
            if not isinstance(action, dict) or action.get("type") not in ACTIONS:
                raise CaptureError(f"{label}: unsupported action")
            kind = action["type"]
            expected = {"type", "selector", "value"} if kind == "fill" else {"type", "selector", "state", "text"} if kind == "wait_for" else {"type", "selector"} if kind == "click" else {"type", "key"}
            if set(action) - expected:
                raise CaptureError(f"{label}: unsupported fields")
            if kind == "press":
                if action.get("key") not in {"Enter", "Escape", "Tab"}:
                    raise CaptureError(f"{label}.key must be Enter, Escape, or Tab")
            else:
                selector = action.get("selector")
                if not isinstance(selector, str) or not selector or len(selector) > 500:
                    raise CaptureError(f"{label}.selector must be a nonempty CSS selector")
            if kind == "fill" and (not isinstance(action.get("value"), str) or len(action["value"]) > 10_000):
                raise CaptureError(f"{label}.value must be a string of at most 10000 characters")
            if kind == "wait_for" and action.get("state", "visible") not in {"visible", "hidden"}:
                raise CaptureError(f"{label}.state must be visible or hidden")
            if kind == "wait_for" and "text" in action and (action.get("state", "visible") != "visible" or not isinstance(action["text"], str) or not action["text"] or len(action["text"]) > 1000):
                raise CaptureError(f"{label}.text requires a visible selector and nonempty text")
    return data


class WebSocket:
    def __init__(self, url: str, timeout: float):
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise CaptureError("Browser debugger must be on loopback WebSocket")
        self.socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
        self.socket.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        request = (f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port or 80}\r\n"
                   f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n")
        self.socket.sendall(request.encode("ascii"))
        headers = bytearray()
        while b"\r\n\r\n" not in headers:
            chunk = self.socket.recv(4096)
            if not chunk or len(headers) + len(chunk) > 16_384:
                raise CaptureError("Browser debugger handshake failed")
            headers.extend(chunk)
        header_bytes, self.pending = bytes(headers).split(b"\r\n\r\n", 1)
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if not header_bytes.startswith(b"HTTP/1.1 101 ") or f"Sec-WebSocket-Accept: {expected}".lower().encode() not in header_bytes.lower():
            raise CaptureError("Browser debugger rejected WebSocket handshake")

    def close(self) -> None:
        self.socket.close()

    def _read(self, length: int) -> bytes:
        chunks = bytearray()
        if self.pending:
            taken = min(length, len(self.pending))
            chunks.extend(self.pending[:taken])
            self.pending = self.pending[taken:]
        while len(chunks) < length:
            part = self.socket.recv(length - len(chunks))
            if not part:
                raise CaptureError("Browser debugger closed the connection")
            chunks.extend(part)
        return bytes(chunks)

    def send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        mask = secrets.token_bytes(4)
        length = len(data)
        head = bytes([0x81, 0x80 | length]) if length < 126 else bytes([0x81, 0x80 | 126]) + length.to_bytes(2, "big") if length < 65536 else bytes([0x81, 0x80 | 127]) + length.to_bytes(8, "big")
        masked = bytes(char ^ mask[index % 4] for index, char in enumerate(data))
        self.socket.sendall(head + mask + masked)

    def receive(self) -> dict[str, Any]:
        while True:
            first, second = self._read(2)
            opcode = first & 15
            if first & 0x80 == 0:
                raise CaptureError("Fragmented debugger messages are unsupported")
            length = second & 127
            if length == 126:
                length = int.from_bytes(self._read(2), "big")
            elif length == 127:
                length = int.from_bytes(self._read(8), "big")
            if length > MAX_MESSAGE_BYTES or second & 0x80:
                raise CaptureError("Invalid or oversized debugger message")
            data = self._read(length)
            if opcode == 9:
                self.socket.sendall(bytes([0x8A, len(data)]) + data)
                continue
            if opcode == 8:
                raise CaptureError("Browser debugger closed the connection")
            if opcode != 1:
                continue
            result = json.loads(data)
            if not isinstance(result, dict):
                raise CaptureError("Invalid debugger response")
            return result


class CDP:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.next_id = 1

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        command_id = self.next_id
        self.next_id += 1
        self.ws.send({"id": command_id, "method": method, "params": params or {}})
        while True:
            message = self.ws.receive()
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise CaptureError(f"{method}: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise CaptureError(f"{method}: missing result")
            return result

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise CaptureError(f"Browser JavaScript failed: {result['exceptionDetails'].get('text', 'unknown error')}")
        return result.get("result", {}).get("value")


def wait_for(cdp: CDP, selector: str, state: str, deadline: float, text: str | None = None) -> None:
    encoded = json.dumps(selector)
    expression = f"(() => {{ const e = document.querySelector({encoded}); return {{visible: !!e && !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden', text: e?.textContent || ''}}; }})()"
    while time.monotonic() < deadline:
        observed = cdp.evaluate(expression)
        if isinstance(observed, dict) and observed.get("visible") is (state == "visible") and (text is None or text in observed.get("text", "")):
            return
        time.sleep(0.1)
    raise CaptureError(f"Timed out waiting for {state} selector: {selector}")


def action(cdp: CDP, item: dict[str, Any], timeout: float) -> None:
    kind = item["type"]
    if kind == "wait_for":
        wait_for(cdp, item["selector"], item.get("state", "visible"), time.monotonic() + timeout, item.get("text"))
        return
    if kind == "press":
        key = item["key"]
        for event_type in ("keyDown", "keyUp"):
            cdp.call("Input.dispatchKeyEvent", {"type": event_type, "key": key, "code": key, "windowsVirtualKeyCode": {"Enter": 13, "Escape": 27, "Tab": 9}[key]})
        return
    selector = item["selector"]
    wait_for(cdp, selector, "visible", time.monotonic() + timeout)
    encoded = json.dumps(selector)
    if kind == "fill":
        expression = f"(() => {{ const e = document.querySelector({encoded}); e.scrollIntoView({{block:'center'}}); e.focus(); const p = Object.getPrototypeOf(e); const s = Object.getOwnPropertyDescriptor(p,'value')?.set; if (s) s.call(e,''); else e.value=''; e.dispatchEvent(new Event('input',{{bubbles:true}})); return true; }})()"
        cdp.evaluate(expression)
        if item["value"]:
            cdp.call("Input.insertText", {"text": item["value"]})
        cdp.evaluate(f"document.querySelector({encoded}).dispatchEvent(new Event('change',{{bubbles:true}}))")
        return
    box = cdp.evaluate(f"(() => {{ const e = document.querySelector({encoded}); e.scrollIntoView({{block:'center'}}); const r=e.getBoundingClientRect(); return {{x:r.x+r.width/2,y:r.y+r.height/2}}; }})()")
    if not isinstance(box, dict) or not isinstance(box.get("x"), (int, float)) or not isinstance(box.get("y"), (int, float)):
        raise CaptureError(f"Could not locate clickable element: {selector}")
    for event_type in ("mousePressed", "mouseReleased"):
        cdp.call("Input.dispatchMouseEvent", {"type": event_type, "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1})


def launch_browser(executable: str, profile: str, timeout: float) -> tuple[subprocess.Popen[bytes], WebSocket]:
    command = [executable, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
               "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + timeout
        active_port = Path(profile) / "DevToolsActivePort"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise CaptureError(f"Browser exited during startup ({process.returncode})")
            if active_port.exists():
                port = int(active_port.read_text().splitlines()[0])
                with urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as response:
                    pages = json.load(response)
                page = next((entry for entry in pages if entry.get("type") == "page"), None)
                if not page:
                    raise CaptureError("Browser did not expose a page target")
                return process, WebSocket(page["webSocketDebuggerUrl"], timeout)
            time.sleep(0.1)
        raise CaptureError("Timed out starting browser debugger")
    except BaseException:
        stop_browser(process)
        raise


def stop_browser(process: subprocess.Popen[bytes]) -> None:
    """Stop the browser and descendants before removing its disposable profile."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            continue
    time.sleep(0.1)


def capture(url: str, scenario: dict[str, Any], output: Path, browser: str, timeout: float, allow_remote_actions: bool = False) -> dict[str, Any]:
    create_report_dir(output)
    with tempfile.TemporaryDirectory(prefix="web-capture-profile-") as profile:
        process = None
        ws = None
        try:
            process, ws = launch_browser(browser, profile, timeout)
            cdp = CDP(ws)
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": scenario["viewport"]["width"], "height": scenario["viewport"]["height"], "deviceScaleFactor": 1, "mobile": False})
            cdp.call("Page.navigate", {"url": url})
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cdp.evaluate("document.readyState") == "complete":
                    break
                time.sleep(0.1)
            else:
                raise CaptureError("Timed out waiting for document load")
            records = []
            for step in scenario["steps"]:
                for item in step.get("actions", []):
                    if item["type"] != "wait_for":
                        ensure_remote_action_allowed(cdp.evaluate("location.href"), allow_remote_actions)
                    action(cdp, item, timeout)
                if step.get("ready_selector"):
                    wait_for(cdp, step["ready_selector"], "visible", time.monotonic() + timeout)
                step_id = step["id"]
                png = base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})["data"], validate=True)
                if len(png) > MAX_SCREENSHOT_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise CaptureError(f"{step_id}: invalid or oversized PNG screenshot")
                width = int.from_bytes(png[16:20], "big")
                height = int.from_bytes(png[20:24], "big")
                if (width, height) != (scenario["viewport"]["width"], scenario["viewport"]["height"]):
                    raise CaptureError(f"{step_id}: screenshot dimensions {width}x{height} do not match requested viewport")
                (output / f"{step_id}.png").write_bytes(png)
                dom = cdp.evaluate("document.documentElement.outerHTML")
                if not isinstance(dom, str):
                    raise CaptureError(f"{step_id}: DOM capture failed")
                (output / f"{step_id}.html").write_text(dom, encoding="utf-8")
                ax = cdp.call("Accessibility.getFullAXTree").get("nodes")
                (output / f"{step_id}.ax.json").write_text(json.dumps(ax, ensure_ascii=False, indent=2), encoding="utf-8")
                records.append({"id": step_id, "url": cdp.evaluate("location.href"), "screenshot": f"{step_id}.png", "sha256": hashlib.sha256(png).hexdigest(), "width": width, "height": height, "dom": f"{step_id}.html", "accessibility": f"{step_id}.ax.json"})
            manifest = {"schema_version": 1, "start_url": url, "viewport": scenario["viewport"], "browser": Path(browser).name, "steps": records}
            (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return manifest
        finally:
            if ws:
                ws.close()
            if process:
                stop_browser(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--url", required=True, help="Starting HTTP(S) URL")
    parser.add_argument("--output", required=True, type=Path, help="New private report directory")
    parser.add_argument("--chrome", help="Path to an installed Chrome or Chromium executable")
    parser.add_argument("--allow-remote-actions", action="store_true", help="Permit interaction steps on an authorized remote test target")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        loopback = validate_url(args.url)
        scenario = read_scenario(args.scenario)
        interactive = any(action["type"] not in {"wait_for"} for step in scenario["steps"] for action in step.get("actions", []))
        if not loopback and interactive:
            ensure_remote_action_allowed(args.url, args.allow_remote_actions)
        if not 1 <= args.timeout_seconds <= 600:
            raise CaptureError("--timeout-seconds must be between 1 and 600")
        browser = args.chrome or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not browser or not Path(browser).is_file():
            raise CaptureError("An installed Chrome or Chromium executable is required")
        manifest = capture(args.url, scenario, args.output, browser, args.timeout_seconds, args.allow_remote_actions)
        print(json.dumps({"output": str(args.output), "steps": len(manifest["steps"])}))
        return 0
    except (CaptureError, OSError, ValueError, json.JSONDecodeError, KeyError, socket.timeout) as error:
        print(f"capture_web: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
