# Web reconstruction and parity checks

Treat a website as a set of observable journeys, not a single screenshot. Choose the routes and states the user requested; if unspecified, begin with the landing page, its primary navigation, and its main interaction. Record desktop and mobile viewports when the page changes layout.

## Capture the reference

For each journey, note the start URL, viewport, browser, any required seed state, actions, and what proves the final state is ready. Use the [REA browser scenario contract](https://github.com/morluto/rea/blob/main/docs/browser-scenario-contract.md). Run `capture-browser-scenario` and keep complete Evidence JSON. Some REA builds also provide `validate-browser-scenario`; check `rea --help` before relying on it. Request `dom`, `accessibility`, `screenshot`, and `url` for visual/interactive work; request `network` or storage only when the flow needs those facts. Capture intermediate states when an action changes them. Use `wait_for` with a page-specific condition after asynchronous actions; an arbitrary delay cannot prove readiness.

Example shape, adapted to the actual page and browser:

```json
{
  "browser": {"mode": "launch", "executable_path": "/absolute/path/to/chrome"},
  "start_url": {"url": "https://example.com"},
  "actions": [
    {"step_id": "primary-action", "action": "click", "locator": {"kind": "css", "selector": "#primary-action"}, "timeout_ms": 10000},
    {"step_id": "result-ready", "action": "wait_for", "locator": {"kind": "css", "selector": "#result[data-state='ready']"}, "state": "visible", "timeout_ms": 10000}
  ],
  "capture": {"at_end": ["dom", "accessibility", "screenshot", "url"]}
}
```

This is a schema example, not a locator or readiness claim about `example.com`. Build an equivalent scenario for the local rebuild, replacing URL and selectors only as needed. Keep step IDs, action kinds, capture selections, browser settings, viewport, and input data aligned. Record the actual environment and any dynamic content that cannot be stabilized.

For a local HTML project, start its normal development server and capture its loopback HTTP URL. REA's browser scenario accepts HTTP(S) start URLs, not `file://`. If the environment cannot start a server or browser, inspect the files and available outputs but leave runtime and visual parity `unknown`.

## Compare and fix

Run both apps and save the reference and rebuild Evidence files. The bundled helper extracts their `normalized_result` captures before invoking REA; passing the full Evidence wrappers straight to `rea compare-web-captures` is an input error in REA 6.8.0. It also decodes every aligned captured screenshot and compares pixels at matching viewport settings:

```bash
python3 <this-skill-directory>/scripts/compare_web.py reference-evidence.json rebuild-evidence.json --output NEW_COMPARISON_DIR
```

The output contains `comparison.md`, `visual-summary.json`, per-step `visual/step-*/pixel-diff.json` and `pixel-diff.png` heatmaps, and the two normalized captures. When REA runs it also contains `rea-comparison.json`. Add `--no-rea` when REA is unavailable. The REA comparison has a 600-second default timeout, configurable with `--timeout-seconds`. The pixel gate defaults to exact equality; `--channel-threshold N` and `--max-changed-ratio R` allow explicit, reported tolerance. Missing viewport metadata on either side makes visual parity `unknown`, even if both are missing. A zero exit from this helper means captured screenshots passed the pixel gate and REA, if selected, completed its comparison with a valid result. It does **not** certify every behavior. A nonzero exit identifies a visual difference or incomplete comparison; read the report.

REA aligns unique `step_id` values and action kinds, and requires compatible browser/origin context. A reference domain versus localhost can produce an alignment failure. Do **not** translate that into a parity verdict. Inspect the captures side by side: compare visible text/DOM, accessibility names and order, navigation destination, control states, and results for the same inputs. Raw DOM may differ because source formatting differs while rendered pixels match. Record each comparison and its Evidence path in a table such as:

| Journey / state | Reference Evidence | Rebuild Evidence | Result | Difference or unknown |
| --- | --- | --- | --- | --- |
| Home / desktop ready | path + step | path + step | matched / different / unknown | concrete observation |

Prioritize broken flows, missing content, wrong layout, typography/spacing/color, and then smaller visual discrepancies. Make a code change for each observed difference and recapture the affected state. An unchanged REA result applies only to the captured artifacts and selected steps. Missing, truncated, or unrequested artifacts remain unknown. Do not claim pixel-perfect equality from a visual glance or a source-code comparison.

For PNG screenshots from a browser tool other than REA, run the independent comparator directly:

```bash
python3 <this-skill-directory>/scripts/pixel_diff.py reference.png rebuild.png --output NEW_PIXEL_DIR
```

It uses Python's standard library and supports 8-bit, noninterlaced RGB/RGBA PNG. Match viewport, device scale, browser/font environment, loading state, and dynamic content before interpreting a pixel difference. The heatmap highlights changed pixels; a tolerance does not prove that those changes are harmless.

Capture may follow redirects and ordinary page requests. Respect the supplied target and the user's authorization; use test accounts or fixtures for account-specific states when provided.
