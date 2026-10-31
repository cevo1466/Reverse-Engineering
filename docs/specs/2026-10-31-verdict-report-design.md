# Unified verdict report (`verdict.py`) and CI workflow

Status: approved design, 2026-10-31. Sub-project 1 of 4 (verdict report and CI, viewport matrix, animation comparison, design-token extraction).

## Problem

The four comparison helpers each report their result in a different shape:

| Helper | Output file | Status field | Values |
| --- | --- | --- | --- |
| `pixel_diff.py` | `pixel-diff.json` | `passes_threshold` (bool) plus `status` | `identical`, `within_tolerance`, `different`, `dimension_mismatch` |
| `compare_capture.py` | `comparison.json` | `overall` plus per-step `visual_status` | `matched_visual_observations`, `different_or_incomplete` |
| `compare_web.py` | `visual-summary.json` | `status` | `within_threshold`, `different`, `unknown` |
| `compare_http.py` | `comparison.json` | `status` plus per-case `status` | `matched_observations`, `different`, `inconclusive` |

All four already exit 0 (matched), 1 (different) or 2 (unknown or error), but nothing combines several results into one answer. The skill's checklist (`matched` / `different` / `unknown` per case) lives only in the agent's notes. The repository also has no CI workflow.

## Goals

1. One command reads any mix of the four result files and writes a single verdict.
2. One shared vocabulary: `matched`, `different`, `unknown`.
3. A process exit code that CI can gate on.
4. A GitHub Actions workflow that runs the existing and new tests.

## Non-goals

- No change to the four existing helpers or their output files (approach A, no breaking change).
- No WebP or other image formats. `pixel_diff.py` stays 8-bit, non-interlaced RGB/RGBA PNG. A decoder would break the standard-library-only rule; deferred.
- No new capture logic. Viewport matrix, animation comparison and token extraction are separate specs.

## Design

### Command

```
python3 skills/reverse-engineer-and-rebuild/scripts/verdict.py RESULT [RESULT ...] --output DIR
```

`RESULT` is a path to one of: `pixel-diff.json`, `comparison.json` (from `compare_capture.py` or `compare_http.py`), `visual-summary.json`. A directory argument is searched for those filenames. `--output` must not exist; it is created through `report_safety.create_report_dir`, like every other helper.

### Kind detection

Detection is by shape, never by filename alone:

- `cases` list and `summary` object: HTTP comparison.
- `overall` string and `steps` list whose rows have a `visual` object: capture comparison.
- `status` in {`within_threshold`, `different`, `unknown`} and `steps` list: web visual summary.
- top-level `passes_threshold` and `changed_ratio` keys: single pixel diff.

A file matching none, or matching more than one, is an error (exit 2), not a guess.

### Normalised case

Each source row becomes:

```json
{"case_id": "capture:home", "kind": "capture|http|web|pixel", "status": "matched|different|unknown", "reason": "short text", "evidence": "relative/path"}
```

Mapping:

| Source value | Normalised |
| --- | --- |
| `identical`, `within_tolerance`, `within_threshold`, `matched`, `matched_observations`, `matched_visual_observations` | `matched` |
| `different`, `dimension_mismatch` | `different` |
| `different_or_incomplete` | `different`; the reason names the failed check (`pixel`, `url_path_matches` or `viewport_matches`) |
| `unknown`, `inconclusive`, `passes_threshold` null, any unrecognised value | `unknown` |

Unrecognised values map to `unknown` so a future change in a helper can never silently read as a pass.

`case_id` is `<kind>:<step or case id>`; duplicates across inputs are an error.

### Verdict and exit code

Overall status: `different` if any case is `different`; else `unknown` if any case is `unknown` (or no cases); else `matched`.

Exit codes: 0 `matched`, 1 `different`, 2 `unknown` or any input error. These match the existing helpers.

### Output

`verdict.json`:

```json
{"schema_version": 1, "overall": "matched|different|unknown", "totals": {"matched": 0, "different": 0, "unknown": 0}, "scope": "...", "cases": []}
```

`verdict.md`: one table, one row per case, plus the scope sentence from the helpers: matched means only the selected observations matched; unseen states and behaviour are not certified.

### Thresholds

Thresholds stay in the helpers (`--channel-threshold`, `--max-changed-ratio`); `verdict.py` only reads their recorded `passes_threshold`. One place decides pass or fail per case, so the verdict cannot disagree with its sources.

### Error handling

Unreadable file, invalid JSON, unknown shape, duplicate `case_id`, or existing output directory: print `verdict: <message>` to stderr, exit 2, write nothing.

## CI workflow

`.github/workflows/ci.yml`:

- Triggers: `pull_request` and `push` to `main`.
- One job on `ubuntu-latest`, Python 3.10 and 3.12 matrix.
- Step: `python3 -m unittest discover -s tests -v`.
- No Chrome, no Node, no network: the existing tests already avoid them, and the new tests use small fixture JSON files.
- Permissions: `contents: read`.

The workflow gates on the test suite. Gating a user's own rebuild on `verdict.py` is the user's CI concern; the README gets a short example step showing `verdict.py` exit codes.

## Testing (written before the code)

`tests/test_verdict.py`, standard-library `unittest`, fixtures built in a temp directory:

- Each of the four kinds maps to the expected normalised status.
- Mixed inputs: one different case makes overall `different`; unknown without different makes `unknown`; all matched makes `matched`.
- Unrecognised status value maps to `unknown`.
- Duplicate `case_id`, unknown shape, invalid JSON and existing output dir each exit 2 and write nothing.
- Exit codes 0, 1, 2 through `main()`.
- Output directory contains the `.gitignore` rule.
- One end-to-end test builds real results by calling `compare_images` and `compare_http.compare_cases` on tiny in-memory inputs, so the detection stays in step with the real helper output.

## Docs

- `README.md` and `README.en.md`: a short "Combined verdict" section with the command and exit codes.
- `SKILL.md` step 8: mention `verdict.py` as the way to produce the `matched` / `different` / `unknown` checklist. Keep the existing rule that a case is never marked matched without comparing the original.

## Delivery

Branch `feat/verdict-report`, one pull request, not merged by the assistant. Small commits: tests, `verdict.py`, CI workflow, docs.

## Open items

None for this sub-project. WebP support is explicitly deferred.
