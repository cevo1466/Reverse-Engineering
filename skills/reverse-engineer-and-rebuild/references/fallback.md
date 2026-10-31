# Evidence collection when REA is unavailable

REA improves provenance and analysis depth, but is optional to the rebuild workflow. Check for an existing REA CLI/MCP connection first. If neither exists and installing it is not appropriate or possible, choose the observations that the current environment supports. Record the tool, input, timestamp or artifact hash, and coverage for each observation; never label it REA Evidence.

| Target | Available observation path | Limit |
| --- | --- | --- |
| Website or web app | Browser automation, DevTools, or a saved HAR; capture the same journeys, viewport, screenshots, DOM/text, accessibility, and relevant requests on both sides. Run `scripts/pixel_diff.py` on PNG screenshots. | Without a browser, rendered layout and interaction parity remain unknown. Plain HTTP HTML does not prove rendered state. |
| HTTP API/backend | Capture repeatable request/response/state scenarios on a resettable test instance with `scripts/capture_http.py`; compare the saved transcripts with `scripts/compare_http.py`. | Traffic shows observed cases, not hidden rules or production consistency. |
| JavaScript/Electron source tree | Inspect entry points, package metadata, imports, IPC, assets, and available source maps; run permitted local examples. | Bundled/minified code may hide relationships; static reading does not prove behavior. |
| ZIP/APK/archive | Use Python's `zipfile`/`tarfile` or existing archive tools to inventory members and hashes. | An inventory does not decompile native or Android code. |
| Native/.NET executable | Use already available metadata, strings, symbols, platform tools, or a permitted runtime fixture. | Without a suitable analysis provider and observable execution, behavior may remain unknown. |

Prefer project or host tools already installed. Do not install a large engine solely to satisfy the skill unless the task and environment authorize it. Continue building from authenticated observations, then compare the executable result. Identify every conclusion based on inference versus direct observation.
