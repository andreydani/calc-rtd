# Changelog

## Unreleased

- Project website at https://andreydani.github.io/calc-rtd/ (English and Portuguese).
- README rewritten for spreadsheet users; technical material moved to `docs/TECHNICAL.md`.
- The extension now declares LibreOffice 6.4 as its minimum version (it was 4.0, which the
  bundled Python of such old versions cannot run).

## 0.1.1

- Project logo; the extension shows it as its icon in the Extension Manager.
- Sample RTD server (`samples/rtd-server`) and automated COM round-trip tests on Windows,
  for both the Python package and the standalone 32/64-bit executables.
- Documentation: cells copied from Excel arrive as `=_xlfn.RTD(...)` and need a
  Find & Replace; this is now listed under known limitations.
- The release workflow refuses a tag that does not match the version in the code.

No change in the behaviour of the `RTD` function or the bridge.

## 0.1.0

First public release.

- `RTD()` function for LibreOffice Calc with Excel's syntax.
- Bridge that hosts RTD (RealTimeData) COM servers, started on demand by the extension
  and protected by a per-user token.
- Simulated data source (`calcrtd.sim`) and the `monitor` / `watch` diagnostic commands.
- Extension builds with a bundled standalone bridge for 32-bit and 64-bit Windows.
