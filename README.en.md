# Reverse Engineer & Rebuild

An agent skill for rebuilding a **working** website, app, API, or feature from a URL or a shipped artifact. It guides an agent through observation, implementation, comparison, and correction. Every reported match is tied to the routes, inputs, states, and environments that were actually tested.

[Skill instructions](skills/reverse-engineer-and-rebuild/SKILL.md) · [skills.sh listing](https://www.skills.sh/cevo1466/reverse-engineering/reverse-engineer-and-rebuild) · [Türkçe README](README.md)

## Install and invoke

```bash
npx skills add cevo1466/Reverse-Engineering --skill reverse-engineer-and-rebuild
```

Then give a skill-aware coding agent one request:

```text
$reverse-engineer-and-rebuild https://example.com: Rebuild the home page and search flow as a working app in this project. Compare desktop and mobile states with the original, fix observed differences, and report what remains unknown.
```

The invocation starts an agent workflow. It is not a standalone terminal command that generates an arbitrary clone without an agent. Python 3.10+ runs the bundled helpers. [REA](https://github.com/morluto/rea) is optional and provides deeper evidence when available. Without REA, an installed Chrome/Chromium browser can capture repeatable interaction journeys directly as PNG, DOM, accessibility, and URL evidence.

## What you get

| Stage | Deliverable |
| --- | --- |
| Observe | Target identity, captured journeys or cases, provenance, and unknowns |
| Rebuild | Runnable code for the requested screens, interactions, state, and errors |
| Compare | Target and rebuild captures for the same conditions, with concrete differences |
| Refine | Repeated checks after fixes and a scoped parity report |

For websites, the skill compares visible states, URLs, accessibility, DOM, and screenshots. With installed Chrome/Chromium, the REA-free browser recorder and comparison helper cover every named step in a scenario:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/capture_web.py scenario.json --url http://127.0.0.1:8080/ --output ./original-web
python3 skills/reverse-engineer-and-rebuild/scripts/capture_web.py scenario.json --url http://127.0.0.1:8081/ --output ./rebuild-web
python3 skills/reverse-engineer-and-rebuild/scripts/compare_capture.py ./original-web/manifest.json ./rebuild-web/manifest.json --output ./web-check
```

See the [web scenario format and remote action rule](skills/reverse-engineer-and-rebuild/references/web.md). The independent PNG helper also compares individual screenshots and reports changed pixels, their ratio and bounding box, and a diff image:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/pixel_diff.py original.png rebuild.png --output ./pixel-check
```

For stateful APIs, the HTTP recorder can run the same ordered scenario against a resettable original and rebuild. It follows generated IDs into later requests, checks state after writes, and keeps session cookies in memory. The comparator checks the saved responses and state without making new requests:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/capture_http.py scenario.json --base-url http://127.0.0.1:8080 --output ./original-api
python3 skills/reverse-engineer-and-rebuild/scripts/capture_http.py scenario.json --base-url http://127.0.0.1:8081 --output ./rebuild-api
python3 skills/reverse-engineer-and-rebuild/scripts/compare_http.py ./original-api/transcript.json ./rebuild-api/transcript.json --output ./api-check
```

[Backend scenario format and safety rules](skills/reverse-engineer-and-rebuild/references/backend.md) explain variable binding, post-write probes, remote test instances, and private transcripts. Remote targets require an explicit flag; the recorder never retries writes. Each new report directory ignores its own contents in Git.

### Combined verdict

`verdict.py` combines several comparison results into one answer. It does not compare anything itself. It reads the output of `pixel_diff.py`, `compare_capture.py`, `compare_web.py` and `compare_http.py`, labels every row `matched`, `different` or `unknown`, and writes `verdict.json` and `verdict.md`:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/verdict.py ./web-check ./api-check --output ./verdict
```

Exit code: `0` everything matched, `1` at least one difference, `2` something is unknown or an input error occurred. Thresholds (`--channel-threshold`, `--max-changed-ratio`) stay with the comparison tool; `verdict.py` only reads the result recorded there. An unrecognised value is never counted as a match. When you pass a directory, it reads the `pixel-diff.json`, `comparison.json` and `visual-summary.json` files directly inside it.

In CI:

```yaml
- run: python3 skills/reverse-engineer-and-rebuild/scripts/verdict.py ./web-check ./api-check --output ./verdict
```

“Matched” means only that the selected observations matched; unseen states are not certified.

### Several viewports at once

`viewport_matrix.py` runs one scenario at several viewports against the reference and the rebuild, then writes one combined verdict whose case IDs carry the viewport (`375x812/search-results`):

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/viewport_matrix.py scenario.json --reference-url https://test.example.com/ --rebuild-url http://127.0.0.1:8080/ --viewports 375x812,768x1024,1440x900 --output ./matrix --allow-remote-actions
```

The default viewports are 375x812, 768x1024 and 1440x900. A failed capture at one viewport is reported as `unknown` for that viewport only. Exit codes match `verdict.py`.

## What “exact” means

The skill aims to match the **observable behavior within the requested scope**. A pixel match applies to the captured viewport, browser, fonts, content, and state. An API match applies to the recorded requests and resulting state. Hidden server rules, inaccessible account states, and unobserved screens remain unknown. Static inspection alone cannot establish runtime parity.

REA is not bundled, and some packaged targets need external analyzers for deep code inspection. The skill continues with available tools and reports the resulting coverage limits. See [web reconstruction](skills/reverse-engineer-and-rebuild/references/web.md), [target routing](skills/reverse-engineer-and-rebuild/references/targets.md), and [REA-free evidence paths](skills/reverse-engineer-and-rebuild/references/fallback.md).

## Development

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skills/reverse-engineer-and-rebuild
```

MIT licensed. This is an independent skill and is not the official REA distribution.
