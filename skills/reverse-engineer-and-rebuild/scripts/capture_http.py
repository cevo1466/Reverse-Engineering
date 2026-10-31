#!/usr/bin/env python3
"""Record a repeatable HTTP scenario for comparison with compare_http.py.

The scenario intentionally contains only selected requests. Remote targets
require an explicit flag; writes are never retried automatically.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from report_safety import create_report_dir


METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
MAX_BODY_BYTES = 1_000_000


class CaptureError(ValueError):
    """A scenario or observation could not be captured reliably."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def validate_base_url(url: str, allow_remote_test: bool) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise CaptureError("Base URL must be HTTP(S) without embedded credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise CaptureError("Base URL must be an origin without path, query, or fragment")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.lower() == "localhost"
    if not is_loopback and not allow_remote_test:
        raise CaptureError("Remote targets require --allow-remote-test and an authorized resettable test environment")
    return url.rstrip("/")


def validate_request(request: Any, context: str) -> None:
    if not isinstance(request, dict) or set(request) - {"method", "path", "query", "body"}:
        raise CaptureError(f"{context}: request must contain only method, path, query, and body")
    if request.get("method") not in METHODS:
        raise CaptureError(f"{context}: unsupported HTTP method")
    path = request.get("path")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path or any(ord(char) < 32 for char in path):
        raise CaptureError(f"{context}: path must be a relative origin path without query or fragment")
    if "query" in request and (not isinstance(request["query"], dict) or any(not isinstance(key, str) for key in request["query"])):
        raise CaptureError(f"{context}: query must be a JSON object")
    if request["method"] in ("GET", "HEAD") and "body" in request:
        raise CaptureError(f"{context}: GET and HEAD requests cannot have a body")


def read_scenario(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1 or set(data) != {"schema_version", "cases"}:
        raise CaptureError("Scenario must contain schema_version 1 and cases")
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise CaptureError("Scenario cases must be a nonempty array")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        context = f"cases[{index}]"
        if not isinstance(case, dict) or set(case) - {"id", "request", "state_probe", "bind", "normalize"}:
            raise CaptureError(f"{context}: unsupported case shape")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise CaptureError(f"{context}: id must be a unique nonempty string")
        seen.add(case_id)
        validate_request(case.get("request"), context)
        if "state_probe" in case:
            validate_request(case["state_probe"], context + ".state_probe")
            if case["state_probe"]["method"] not in ("GET", "HEAD"):
                raise CaptureError(f"{context}: state_probe must be read-only")
        for name in ("bind", "normalize"):
            mapping = case.get(name, {})
            if not isinstance(mapping, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()):
                raise CaptureError(f"{context}.{name} must be a string map")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or not pointer.startswith("/") for name, pointer in case.get("bind", {}).items()):
            raise CaptureError(f"{context}.bind must map variable names to response-body JSON pointers")
        if any(not pointer.startswith(("/response/body/", "/state_after/body/")) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable) for pointer, variable in case.get("normalize", {}).items()):
            raise CaptureError(f"{context}.normalize must map output JSON pointers to variable names")
    return cases


def _resolve(value: Any, bindings: dict[str, Any], in_path: bool = False) -> Any:
    if isinstance(value, str):
        match = VARIABLE.fullmatch(value)
        if match:
            name = match.group(1)
            if name not in bindings:
                raise CaptureError(f"Unbound scenario variable: {name}")
            bound = bindings[name]
            return quote(str(bound), safe="") if in_path else bound

        def replacement(found: re.Match[str]) -> str:
            name = found.group(1)
            if name not in bindings:
                raise CaptureError(f"Unbound scenario variable: {name}")
            return quote(str(bindings[name]), safe="") if in_path else str(bindings[name])

        return VARIABLE.sub(replacement, value)
    if isinstance(value, list):
        return [_resolve(item, bindings) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, bindings) for key, item in value.items()}
    return value


def resolve_request(request: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    resolved = {"method": request["method"], "path": _resolve(request["path"], bindings, in_path=True)}
    if "query" in request:
        resolved["query"] = _resolve(request["query"], bindings)
    if "body" in request:
        resolved["body"] = _resolve(request["body"], bindings)
    validate_request(resolved, "resolved request")
    return resolved


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise CaptureError(f"Invalid JSON pointer: {pointer}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def pointer_get(value: Any, pointer: str) -> Any:
    for part in _pointer_parts(pointer):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise CaptureError(f"JSON pointer not found: {pointer}")
    return value


def pointer_set(value: Any, pointer: str, replacement: Any) -> None:
    parts = _pointer_parts(pointer)
    parent = value
    for part in parts[:-1]:
        parent = parent[part] if isinstance(parent, dict) else parent[int(part)]
    last = parts[-1]
    if isinstance(parent, dict):
        parent[last] = replacement
    else:
        parent[int(last)] = replacement


def run_case(case: dict[str, Any], bindings: dict[str, Any], send: Callable[[dict[str, Any]], dict[str, Any]], partial_raw: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    request = resolve_request(case["request"], bindings)
    response = send(request)
    raw = {"id": case["id"], "request": request, "response": response}
    if partial_raw is not None:
        partial_raw.update(deepcopy(raw))
    for variable, pointer in case.get("bind", {}).items():
        if variable in bindings:
            raise CaptureError(f"Scenario variable already bound: {variable}")
        bound = pointer_get(response.get("body"), pointer)
        if bound is None or isinstance(bound, (list, dict)):
            raise CaptureError(f"Scenario variable must be a non-null scalar: {variable}")
        bindings[variable] = bound
    if "state_probe" in case:
        probe = resolve_request(case["state_probe"], bindings)
        raw["state_after"] = send(probe)
        if partial_raw is not None:
            partial_raw["state_after"] = deepcopy(raw["state_after"])
    comparable = deepcopy(raw)
    comparable["request"] = deepcopy(case["request"])
    for pointer, variable in case.get("normalize", {}).items():
        if variable not in bindings:
            raise CaptureError(f"Unbound normalization variable: {variable}")
        observed = pointer_get(comparable, pointer)
        bound = bindings[variable]
        if type(observed) is not type(bound) or observed != bound:
            raise CaptureError(f"Observed value does not match bound variable at {pointer}")
        marker = {"$binding": variable, "type": type(bound).__name__}
        pointer_set(comparable, pointer, marker)
    return comparable, raw


def parse_header_env(entries: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise CaptureError("--header-env must use Header-Name=ENV_VARIABLE")
        name, variable = entry.split("=", 1)
        if not TOKEN.fullmatch(name) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
            raise CaptureError("Invalid --header-env name or environment variable")
        value = os.environ.get(variable)
        if not value or "\r" in value or "\n" in value:
            raise CaptureError(f"Missing or unsafe value for environment variable {variable}")
        headers[name] = value
    return headers


def make_sender(base_url: str, headers: dict[str, str], timeout_seconds: float, captured_headers: list[str]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    opener = build_opener(NoRedirect())
    selected = {name.lower() for name in captured_headers}

    def send(request: dict[str, Any]) -> dict[str, Any]:
        query = request.get("query", {})
        if any(isinstance(value, (dict, list)) for value in query.values()):
            raise CaptureError("Query values must be scalar")
        url = base_url + request["path"] + ("?" + urlencode(query) if query else "")
        body = json.dumps(request["body"], ensure_ascii=False).encode("utf-8") if "body" in request else None
        outgoing = dict(headers)
        if body is not None:
            outgoing.setdefault("Content-Type", "application/json")
        prepared = Request(url, data=body, headers=outgoing, method=request["method"])
        try:
            response = opener.open(prepared, timeout=timeout_seconds)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError) as error:
            raise CaptureError(f"HTTP request failed: {type(error).__name__}") from error
        with response:
            body_bytes = response.read(MAX_BODY_BYTES + 1)
            if len(body_bytes) > MAX_BODY_BYTES:
                raise CaptureError("HTTP response exceeds the 1 MB capture limit")
            content_type = response.headers.get("Content-Type", "")
            try:
                text = body_bytes.decode("utf-8")
                try:
                    parsed_body = json.loads(text)
                except json.JSONDecodeError:
                    parsed_body = text
            except UnicodeDecodeError:
                parsed_body = {"sha256": hashlib.sha256(body_bytes).hexdigest(), "bytes": len(body_bytes)}
            result = {"status": response.status, "headers": {name.lower(): value for name, value in response.headers.items() if name.lower() in selected}, "body": parsed_body}
            if request["method"] == "HEAD":
                result["body"] = None
            return result

    return send


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture a repeatable HTTP behavior scenario")
    parser.add_argument("scenario", type=Path, help="scenario JSON containing ordered cases")
    parser.add_argument("--base-url", required=True, help="HTTP(S) origin of a resettable test instance")
    parser.add_argument("--output", type=Path, required=True, help="new private output directory")
    parser.add_argument("--allow-remote-test", action="store_true", help="explicitly allow an authorized remote test instance")
    parser.add_argument("--header-env", action="append", default=[], help="send Header-Name from an environment variable; never record its value")
    parser.add_argument("--capture-header", action="append", default=["content-type"], help="response header to compare; repeat as needed")
    parser.add_argument("--timeout-seconds", type=float, default=20, help="per-request timeout (default: 20 seconds)")
    args = parser.parse_args(argv)
    try:
        cases = read_scenario(args.scenario.expanduser().resolve(strict=True))
        base_url = validate_base_url(args.base_url, args.allow_remote_test)
        if not 0 < args.timeout_seconds <= 120:
            raise CaptureError("--timeout-seconds must be greater than 0 and at most 120")
        if any(not TOKEN.fullmatch(name) for name in args.capture_header):
            raise CaptureError("Invalid --capture-header name")
        headers = parse_header_env(args.header_env)
        sender = make_sender(base_url, headers, args.timeout_seconds, args.capture_header)
        output = args.output.expanduser().resolve()
        create_report_dir(output)
    except (CaptureError, OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.error(str(error))
    bindings: dict[str, Any] = {}
    comparable_cases: list[dict[str, Any]] = []
    raw_cases: list[dict[str, Any]] = []
    failure = None
    for case in cases:
        partial_raw: dict[str, Any] = {}
        try:
            comparable, raw = run_case(case, bindings, sender, partial_raw)
            comparable_cases.append(comparable)
            raw_cases.append(raw)
        except (CaptureError, OSError, ValueError) as error:
            if partial_raw:
                raw_cases.append(partial_raw)
            failure = {"case_id": case["id"], "reason": str(error), "write_may_have_occurred": case["request"]["method"] not in ("GET", "HEAD")}
            break
    (output / "transcript.json").write_text(json.dumps({"schema_version": 1, "cases": comparable_cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "raw-transcript.json").write_text(json.dumps({"schema_version": 1, "cases": raw_cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status = "complete" if failure is None else "partial" if comparable_cases else "failed"
    (output / "manifest.json").write_text(json.dumps({"schema_version": 1, "status": status, "base_url": base_url, "completed_cases": len(comparable_cases), "total_cases": len(cases), "failure": failure}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if failure is None else 1


if __name__ == "__main__":
    sys.exit(main())
