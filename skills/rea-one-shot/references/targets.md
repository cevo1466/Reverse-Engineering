# Target routing and prerequisites

The launcher classifies content before dispatch. `--mode` overrides its choice when the user knows the format. The raw REA result and stderr are retained per step.

| Target | Initial REA operations | Deeper analysis |
| --- | --- | --- |
| Extracted JavaScript/Electron directory or ASAR | `analyze-javascript-application` | Trace selected modules, imports, IPC, source maps and feature entry points. Linux needs a procps-compatible `ps`. |
| Other application installation directory | `inspect-desktop-application` | Inspect runtime candidates, then choose the relevant code or resource workflow. Use `--mode javascript` for a known JavaScript tree without top-level JS or package markers. |
| PE/CLI .NET assembly | `inspect-managed-artifact`, `inspect-managed-members` | Select exact types and inspect CIL, string literals and native boundaries. Detection uses the PE CLI header, not `.dll` alone. |
| APK | `inspect-artifact`, `inspect-apk-signature`; with `--android-code`, also `inspect-android-package` | JADX with a full JDK must already be configured for manifest, class and method analysis. The baseline inventory does not analyze code. |
| ELF, native PE, thin Mach-O | `analyze`, optionally with `--provider PROVIDER`; on failure, `inspect-artifact` | REA can select the sole configured provider. Ghidra, Hopper, IDA or Kuna must already be configured for pseudocode and cross references. The fallback inventory is not decompilation. |
| Other file or archive | `inspect-artifact` | Select members and inspect or extract them through REA when needed. The launcher itself does not extract. |
| HTTP(S) URL | `validate-browser-scenario`, then `capture-browser-scenario` with DOM, accessibility and URL at the end | Requires an installed Chrome-family browser. The capture observes one page load, not every flow or background state. |

REA itself requires Node.js `^22.23.4 || ^24.22.1 || >=26.12.1`. The launcher uses an existing `rea` executable when found; otherwise it invokes `npx -y rea-agents@6.8.0`. `npx` may download the package. Set `--no-download` to require a preinstalled `rea`.

The source for command behavior and requirements is the [REA README](https://github.com/morluto/rea), [CLI guide](https://github.com/morluto/rea/blob/main/docs/cli.md), [Android guide](https://github.com/morluto/rea/blob/main/docs/android-analysis.md), and [browser scenario contract](https://github.com/morluto/rea/blob/main/docs/browser-scenario-contract.md). Check the installed version's `--help` if a command differs.
