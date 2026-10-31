#!/usr/bin/env python3
"""Compare two saved HTTP behavior transcripts without contacting either server.

Transcript schema v1:
  {"schema_version": 1, "cases": [{"id": "read", "request":
    {"method": "GET", "path": "/api/items", "query": {}, "body": null},
    "response": {"status": 200, "headers": {}, "body": []},
    "state_after": ..., "effects": ...}]}

Only id, request.method, and request.path are required per case. Omitted
response fields and optional snapshots are reported as unobserved, not equal.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

from report_safety import create_report_dir


ABSENT = object()
CHECKS = ("status", "headers", "body", "state_after", "effects")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def read_transcript(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream, parse_int=Decimal, parse_float=Decimal, parse_constant=_reject_constant)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not Decimal or data["schema_version"] != 1:
        raise ValueError(f"{path}: expected schema_version 1 object")
    if set(data) != {"schema_version", "cases"}:
        raise ValueError(f"{path}: only schema_version and cases are supported")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path}: cases must be a nonempty array")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"{path}: cases[{index}]"
        if not isinstance(case, dict):
            raise ValueError(f"{prefix} must be an object")
        if set(case) - {"id", "request", "response", "state_after", "effects"}:
            raise ValueError(f"{prefix} contains unsupported fields")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"{prefix}.id must be a unique nonempty string")
        seen.add(case_id)
        request = case.get("request")
        if not isinstance(request, dict):
            raise ValueError(f"{prefix}.request must be an object")
        if set(request) - {"method", "path", "query", "body"}:
            raise ValueError(f"{prefix}.request contains unsupported fields")
        if not isinstance(request.get("method"), str) or not request["method"].strip():
            raise ValueError(f"{prefix}.request.method must be a nonempty string")
        if not isinstance(request.get("path"), str) or not request["path"].startswith("/"):
            raise ValueError(f"{prefix}.request.path must start with /")
        if "response" in case:
            response = case["response"]
            if not isinstance(response, dict):
                raise ValueError(f"{prefix}.response must be an object")
            if set(response) - {"status", "headers", "body"}:
                raise ValueError(f"{prefix}.response contains unsupported fields")
            if "status" in response and (
                not isinstance(response["status"], Decimal)
                or response["status"] != response["status"].to_integral_value()
                or not 100 <= response["status"] <= 599
            ):
                raise ValueError(f"{prefix}.response.status must be an HTTP status integer")
            if "headers" in response:
                headers = response["headers"]
                if not isinstance(headers, dict) or any(
                    not isinstance(name, str) or not name or not isinstance(value, str)
                    for name, value in headers.items()
                ):
                    raise ValueError(f"{prefix}.response.headers must map names to strings")
                lowered = [name.lower() for name in headers]
                if len(lowered) != len(set(lowered)):
                    raise ValueError(f"{prefix}.response.headers contains duplicate names ignoring case")
    return {case["id"]: case for case in cases}


def _pointer(base: str, token: str | int) -> str:
    escaped = str(token).replace("~", "~0").replace("/", "~1")
    return f"{base}/{escaped}"


def _different(left: Any, right: Any, path: str, differences: list[dict[str, str]]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(left.keys() | right.keys()):
            child = _pointer(path, key)
            if key not in left:
                differences.append({"path": child, "reason": "missing_in_reference"})
            elif key not in right:
                differences.append({"path": child, "reason": "missing_in_rebuild"})
            else:
                _different(left[key], right[key], child, differences)
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            differences.append({"path": path, "reason": "array_length"})
        for index, (a, b) in enumerate(zip(left, right)):
            _different(a, b, _pointer(path, index), differences)
    elif type(left) is not type(right) or left != right:
        differences.append({"path": path, "reason": "value_or_type"})


def _field(case: dict[str, Any], name: str) -> Any:
    if name in ("state_after", "effects"):
        return case.get(name, ABSENT)
    response = case.get("response")
    return response.get(name, ABSENT) if isinstance(response, dict) else ABSENT


def _request_identity(request: dict[str, Any]) -> dict[str, Any]:
    # Absent query/body is distinct from an explicit null or empty value.
    return {name: request[name] for name in ("method", "path", "query", "body") if name in request}


def compare_cases(reference: dict[str, Any], rebuild: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case_id in list(dict.fromkeys([*reference, *rebuild])):
        before, after = reference.get(case_id), rebuild.get(case_id)
        row: dict[str, Any] = {
            "id": case_id,
            "request": "same",
            "checks": {},
            "differences": [],
            "unknowns": [],
        }
        if before is None or after is None:
            row["request"] = "unknown"
            row["unknowns"].append("/cases: case missing in " + ("reference" if before is None else "rebuild"))
            row["checks"] = {name: "unknown" for name in CHECKS}
        else:
            _different(_request_identity(before["request"]), _request_identity(after["request"]), "/request", row["differences"])
            if row["differences"]:
                row["request"] = "different"
                row["checks"] = {name: "unknown" for name in CHECKS}
                row["unknowns"].append("/response: requests differ, so responses cannot establish parity")
            else:
                for name in CHECKS:
                    left, right = _field(before, name), _field(after, name)
                    path = "/" + name if name in ("state_after", "effects") else "/response/" + name
                    if left is ABSENT and right is ABSENT:
                        row["checks"][name] = "not_selected"
                    elif left is ABSENT or right is ABSENT:
                        row["checks"][name] = "unknown"
                        row["unknowns"].append(path + ": missing in " + ("reference" if left is ABSENT else "rebuild"))
                    else:
                        if name == "headers":
                            left = {key.lower(): value for key, value in left.items()}
                            right = {key.lower(): value for key, value in right.items()}
                        count = len(row["differences"])
                        _different(left, right, path, row["differences"])
                        row["checks"][name] = "different" if len(row["differences"]) > count else "same"
        if row["differences"]:
            row["status"] = "different"
        elif row["unknowns"] or not any(value == "same" for value in row["checks"].values()):
            row["status"] = "inconclusive"
        else:
            row["status"] = "matched_observations"
        rows.append(row)
    totals = {name: sum(row["status"] == name for row in rows) for name in ("matched_observations", "different", "inconclusive")}
    status = "different" if totals["different"] else "inconclusive" if totals["inconclusive"] else "matched_observations"
    return {"schema_version": 1, "status": status, "summary": {"total_cases": len(rows), **totals}, "cases": rows}


def _markdown(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def write_report(output: Path, result: dict[str, Any], sources: tuple[Path, Path]) -> None:
    report = {"reference": str(sources[0]), "rebuild": str(sources[1]), **result}
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# HTTP behavior comparison", "",
        f"Reference: `{_markdown(str(sources[0]))}`", "",
        f"Rebuild: `{_markdown(str(sources[1]))}`", "",
        f"Overall: **{result['status']}**. This compares only recorded observations; no HTTP requests were sent.", "",
        "| Case | Request | Status | Headers | Body | State after | Effects | Result |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["cases"]:
        checks = row["checks"]
        lines.append("| " + " | ".join(_markdown(str(value)) for value in (
            row["id"], row["request"], checks["status"], checks["headers"], checks["body"],
            checks["state_after"], checks["effects"], row["status"],
        )) + " |")
    lines.append("")
    for row in result["cases"]:
        if not row["differences"] and not row["unknowns"]:
            continue
        lines += [f"## {_markdown(row['id'])}", ""]
        for item in row["differences"]:
            lines.append(f"- Different `{_markdown(item['path'])}` ({item['reason']}).")
        for item in row["unknowns"]:
            lines.append(f"- Unknown: {_markdown(item)}.")
        lines.append("")
    lines += ["`matched_observations` describes only selected fields in these transcripts; it does not prove unseen backend behavior.", ""]
    (output / "comparison.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare saved HTTP behavior transcripts without making requests")
    parser.add_argument("reference", type=Path)
    parser.add_argument("rebuild", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args(argv)
    try:
        sources = (args.reference.expanduser().resolve(strict=True), args.rebuild.expanduser().resolve(strict=True))
        cases = (read_transcript(sources[0]), read_transcript(sources[1]))
        output = args.output.expanduser().resolve()
        create_report_dir(output)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    result = compare_cases(*cases)
    write_report(output, result, sources)
    print(output)
    return {"matched_observations": 0, "different": 1, "inconclusive": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
