# Changelog

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
