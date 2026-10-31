---
name: reverse-engineer-and-rebuild
description: Rebuild a runnable website, app, or feature from a user-supplied URL or shipped artifact. Use when the user asks to reverse engineer and clone, recreate, port, or replace observable behavior. Gather REA evidence, implement the rebuild in the workspace, compare original and rebuild, and close the observed gaps.
---

# Reverse engineer and rebuild

Deliver a **working reconstruction**, not only an analysis report. One invocation should carry the work from target observation through implementation and verification. Work in the user's language and existing project unless they specify another destination. Aim for the exact observable result within the requested scope; never turn an unobserved feature into a claim of exactness.

## Choose the observable contract

1. Identify the target URL or artifact, requested output, existing workspace, and the routes, screens, or behaviors the user named. When no scope is named, start with the reachable primary flow and its visible states, then continue through other reachable routes and interactions. If no target is available, request it.
2. For a website or web app, read [web reconstruction](references/web.md). For an APK, binary, .NET assembly, Electron package, archive, or CLI, read [target routing](references/targets.md). These paths define what can actually be observed and compared.
3. Record a compact contract before coding: each route or input, action, expected visible/output state, viewport or runtime, evidence location, and confidence. Include loading, empty, error, and responsive states when they are visible or requested. Keep unknown behavior marked unknown.

## Observe and trace

4. Run the bundled REA first pass with a new report directory:

   ```bash
   python3 <this-skill-directory>/scripts/rea_once.py TARGET --output REPORT_DIR
   ```

   `REPORT_DIR` must not exist. The helper saves raw JSON, stderr, a manifest, and a readable report; it **does not build a clone**. Use `--rea`, `--provider`, `--android-code`, or `--browser-executable` when the target needs them. Read `manifest.json`, `report.md`, and relevant raw Evidence. Inspect `limitations`, `coverage`, unknowns, and source identity. A successful command is not a completeness verdict.
5. Investigate the requested behavior beyond the first pass. Use REA MCP tools or the installed CLI's matching commands. For web, capture named user journeys at matching viewport/device settings with DOM, accessibility, screenshot, URL, and relevant events; use a page-specific ready condition. For packaged software, trace the relevant entry points and collect runtime cases where execution is available. Cite exact Evidence paths in the contract. If REA is unavailable, use available observation tools, record that provenance, and continue only with claims those observations support.

## Build, compare, refine

6. Implement the runnable reconstruction in the user's workspace. Follow the project's stack and conventions; if it is empty, choose a maintainable minimal stack. Implement the content, assets, layout, navigation, responsive behavior, state transitions, and data/error behavior supported by the contract. Do not finish with a static mockup when the target responds to input. Keep independently created code and assets organized so the app builds and runs.
7. Run the rebuild. Execute each recorded journey against the original and rebuild under equivalent conditions. Capture the same states; compare visual layout, text and accessibility, URL/navigation, controls, state changes, and relevant network/process output. For web, use the bundled `scripts/compare_web.py` on the two REA Evidence files; it unwraps their normalized captures for REA and gives a direct artifact table when origin alignment fails. For other targets use the applicable REA comparison or fixture workflow from [target routing](references/targets.md). Do not treat static similarity as runtime parity.
8. Fix observed material differences, recapture the affected cases, and repeat until the requested journeys pass or a specific blocker makes further observation impossible. Keep an evidence-to-implementation checklist with `matched`, `different`, or `unknown` for each case. Never mark a case matched solely because code exists or tests pass without comparing the original.
9. Deliver the runnable files, the exact local run command, verification evidence, and a concise list of remaining differences or unknowns. State the scope of any “birebir” or exactness claim: the tested routes, states, inputs, viewports, and environment. If a prerequisite prevents the build or comparison, still finish all independent implementation and checks, then identify the precise blocked cases.

## Boundaries

- The first-pass helper opens a supplied web URL but does not execute local target programs. Running an original binary, attaching to a process, or using a live account can affect external state; use the user's authorization and environment constraints for those observations.
- Do not silently install native analysis engines, bypass access controls, or publish captured target data. REA may be downloaded by `npx` when the helper cannot find an installed CLI; `--no-download` prevents that.
- REA is maintained by [morluto/rea](https://github.com/morluto/rea). This independent skill orchestrates its public interfaces and does not bundle REA.
