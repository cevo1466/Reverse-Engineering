# Packaged targets: evidence to working reconstruction

The first-pass launcher classifies the supplied artifact. `--mode` overrides detection. Read the raw REA Evidence; its recorded coverage controls what can be inferred. REA requires Node.js `^22.23.4 || ^24.22.1 || >=26.12.1`; the helper uses installed `rea` or `npx -y rea-agents@6.8.0`.

| Target | First pass | Rebuild and verification |
| --- | --- | --- |
| JavaScript/Electron directory or ASAR | `analyze-javascript-application`; trace modules, IPC, routes, and assets. Linux needs `ps`. | Recreate the requested visible or CLI flow. Compare runtime interaction fixtures; `compare-application-versions` compares static graphs only and cannot prove behavior. |
| Other installation directory | `inspect-desktop-application`; identify the actual runtime and resources. | Follow the discovered code lane. Observe original inputs/outputs if execution is available, then run the same cases in the rebuild. |
| PE/CLI .NET assembly | `inspect-managed-artifact` and `inspect-managed-members`; select types, CIL, strings, and native boundaries. | Implement and test chosen public behavior. Compare original and rebuild input/output cases when the original can run. Header detection checks the PE CLI directory, not `.dll` extension. |
| APK | `inspect-artifact`, `inspect-apk-signature`; `--android-code` adds `inspect-android-package` with configured JADX and full JDK. | Rebuild the requested screens and state transitions. Inventory/signature alone do not reveal logic; compare on a permitted emulator/device when available. |
| ELF, native PE, thin Mach-O | `analyze` with configured Ghidra, Hopper, IDA, or Kuna; `inspect-artifact` is a fallback on failure. | Trace the required functions and data formats. Use observed process cases for behavior. A decompiler's pseudocode is not recovered source code. |
| Archive or other artifact | `inspect-artifact`; inspect relevant members through REA. | Reconstruct only the format or application behavior backed by the members and observed cases. |

For command-line or other executable targets with safe, authorized runtime cases, `rea capture-process SCENARIO.json --json` records an original case; capture the corresponding rebuild case and use `rea compare-process-captures ORIGINAL.json REBUILD.json --json`. Align the capture contract, including working directory and observation paths. For package inventories, `rea compare-artifacts` accepts two Evidence-file sources; it compares those inventories, not the runnable behavior. Check the installed CLI's help and the [REA CLI guide](https://github.com/morluto/rea/blob/main/docs/cli.md) for the exact request shape.

Static application graphs can identify candidate reconstruction obligations. [REA's obligation ledger](https://github.com/morluto/rea/blob/main/docs/reconstruction-obligation-ledgers.md) keeps a claim open until comparable original cases, rebuild fixtures, and verifier evidence exist. Use that model when the requested behavior warrants formal closure; do not manufacture a passing ledger from static data.
