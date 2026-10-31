---
name: rea-one-shot
description: Investigate a user-supplied binary, APK, .NET assembly, JavaScript or Electron package, archive, or website with REA and deliver an evidence-backed reverse-engineering report. Use when the user asks how a shipped application or feature works, wants decompilation or behavior reconstruction, or asks for a one-command reverse-engineering investigation. Skip ordinary source-only repository review.
---

# REA One Shot

Turn one target and one question into an investigation whose claims can be traced to REA Evidence. Work in the user's language. The bundled launcher performs an initial, static-first triage; your focused follow-up makes the result an investigation rather than a file inventory.

## Start

1. Identify the exact local path or HTTP(S) URL supplied by the user and the behavior they want explained. If they gave no target, ask for one. Treat a URL as permission to visit that page, not to probe unrelated hosts.
2. Run the bundled launcher once:

   ```bash
   python3 <this-skill-directory>/scripts/rea_once.py TARGET --output REPORT_DIR
   ```

   `REPORT_DIR` must be new. REA selects a sole configured native provider automatically; pass `--provider ghidra`, `hopper`, `ida`, or `kuna` to choose explicitly. For a website, pass `--browser-executable /absolute/path/to/chrome` if the launcher cannot find an installed Chrome-family browser. Use `--rea /absolute/path/to/rea` to select an existing CLI. See [target routing](references/targets.md) for prerequisites and limitations.
3. Read `REPORT_DIR/report.md`, `manifest.json`, and the raw JSON for every successful step. A successful exit does not mean complete coverage. Follow the Evidence's `limitations`, `coverage`, `unknowns`, and source identity. Preserve the raw files; do not replace them with a summary.
4. Investigate the user's question with focused REA MCP tools when connected, or the matching CLI commands in [REA's CLI guide](https://github.com/morluto/rea/blob/main/docs/cli.md). Search relevant functions, imports, references, methods, IPC routes, or browser events. A broad first pass alone rarely establishes how a feature works. Use only the tools available in the installed REA version.
5. Write a concise report in the user's language with: target identity and question; observed facts with exact Evidence locations or file paths; the inferred mechanism and why it follows; unknowns and coverage limits; steps another analyst can repeat. Label an inference as an inference. When a prerequisite blocks deeper analysis, state which one and retain the useful partial evidence.

## Boundaries

- The launcher does not execute an untrusted local program or materialize extracted target files. Native decompilation needs an already configured REA provider. APK code analysis needs an existing JADX and JDK setup. A website capture opens the supplied URL in a temporary browser profile and may trigger its ordinary page requests.
- Do not silently install analysis engines, change agent configuration, attach to a process, bypass protections, or publish captured data. Seek authorization for any additional mutation or target execution that the user did not request.
- Do not claim original source recovery from pseudocode, complete coverage from exit code zero, or observed runtime behavior from static inspection.
- REA is developed by [morluto/rea](https://github.com/morluto/rea). This skill orchestrates its public CLI; it does not bundle or claim ownership of REA.
