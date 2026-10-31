# Reverse Engineer & Rebuild

An agent skill for rebuilding a **working** website, app, API, or feature from a URL or a shipped artifact. It guides an agent through observation, implementation, comparison, and correction. Every reported match is tied to the routes, inputs, states, and environments that were actually tested.

[Skill instructions](skills/reverse-engineer-and-rebuild/SKILL.md) · [skills.sh listing](https://www.skills.sh/cevo1466/tersine-m-hendislik/reverse-engineer-and-rebuild) · [Türkçe README](README.md)

## Install and invoke

```bash
npx skills add cevo1466/Tersine-M-hendislik --skill reverse-engineer-and-rebuild
```

Then give a skill-aware coding agent one request:

```text
$reverse-engineer-and-rebuild https://example.com: Rebuild the home page and search flow as a working app in this project. Compare desktop and mobile states with the original, fix observed differences, and report what remains unknown.
```

The invocation starts an agent workflow. It is not a standalone terminal command that generates an arbitrary clone without an agent. Python 3.10+ runs the bundled comparison helpers. [REA](https://github.com/morluto/rea) is optional and provides deeper evidence when available; visual verification still needs a browser that can capture screenshots.

## What you get

| Stage | Deliverable |
| --- | --- |
| Observe | Target identity, captured journeys or cases, provenance, and unknowns |
| Rebuild | Runnable code for the requested screens, interactions, state, and errors |
| Compare | Target and rebuild captures for the same conditions, with concrete differences |
| Refine | Repeated checks after fixes and a scoped parity report |

For websites, the skill compares visible states, URLs, accessibility, DOM, and screenshots. The independent PNG helper reports changed pixels, their ratio and bounding box, and a diff image:

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

## What “exact” means

The skill aims to match the **observable behavior within the requested scope**. A pixel match applies to the captured viewport, browser, fonts, content, and state. An API match applies to the recorded requests and resulting state. Hidden server rules, inaccessible account states, and unobserved screens remain unknown. Static inspection alone cannot establish runtime parity.

REA is not bundled, and some packaged targets need external analyzers for deep code inspection. The skill continues with available tools and reports the resulting coverage limits. See [web reconstruction](skills/reverse-engineer-and-rebuild/references/web.md), [target routing](skills/reverse-engineer-and-rebuild/references/targets.md), and [REA-free evidence paths](skills/reverse-engineer-and-rebuild/references/fallback.md).

## Development

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skills/reverse-engineer-and-rebuild
```

MIT licensed. This is an independent skill and is not the official REA distribution.
