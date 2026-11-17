# calc-rtd: notes for maintainers

calc-rtd brings Excel's `=RTD()` (RealTimeData) function to LibreOffice Calc, and is also a
Python library for reading RTD servers without Excel. Users read `README.md`; architecture and
diagnostics are in `docs/TECHNICAL.md`; the Python API is documented in `docs/PYTHON.md`.

## Layout

| Path | What it is |
|---|---|
| `src/calc_rtd/` | RTD client library. Public API = what `__init__.py` exports (`RtdSession`, `Subscription`, `Update`, `RtdError`, `parse_formula`, `SIM_PROGID`). Every submodule is internal. |
| `src/calc_rtd/com_driver.py` | The only Windows/COM code (comtypes). Plays Excel's side of `IRtdServer`. |
| `src/calc_rtd_bridge/` | Process the Calc extension talks to: localhost socket, per-user token, CLI (`serve`, `monitor`, `watch`). Built on `calc_rtd`. |
| `extension/src/` | The LibreOffice add-in (`calcrtd.py`, standard library only), its manifest, description and config. |
| `tools/build_oxt.py` | Builds the `.oxt`; writes the UNO type library (`.rdb`) itself, no LibreOffice SDK needed. `--bridge-exe` bundles the standalone bridge. |
| `samples/rtd-server/` | Minimal RTD server in C# (ProgID `CalcRtd.Sample`) used by the Windows CI. |
| `examples/` | Runnable scripts for the Python guide; `tests/test_examples.py` runs them. |
| `site/` | Static website (English and `pt-br/`), deployed by `.github/workflows/pages.yml`. |

## Everyday commands

```bash
pip install -e . pytest
pytest                                    # COM tests are skipped outside Windows
python tools/build_oxt.py                 # -> dist/calc-rtd.oxt
python -m calc_rtd '=RTD("calcrtd.sim";;"clock")' --seconds 3
```

End to end against LibreOffice (Linux, needs `python3-uno` and the extension installed with
`unopkg add`): `python3 tests/integration_libreoffice.py`. CI runs it on every push.

## Conventions

- **Version** lives only in `src/calc_rtd/__init__.py`. The package metadata and the
  extension's `description.xml` (`@VERSION@`) are derived from it.
- **Releases**: bump the version and `CHANGELOG.md` in a PR. After merging, the maintainer
  creates a GitHub release with tag `vX.Y.Z`; `ci.yml` then attaches the three `.oxt` files and
  publishes to PyPI (trusted publishing, environment `pypi`). The workflow refuses a tag that
  does not match the version. A PyPI version can never be replaced.
- **Work on a branch and open a PR**; CI must be green before merging.
- **Language**: code, comments, docs and commit messages in English. The website and the
  extension's display strings also have Portuguese versions: keep both in step.
- **Neutral examples only**: `Vendor.ProgID`, `ACME`, `GLOBEX`, `LAST`, or the simulated source
  `calcrtd.sim`. Never name a real data vendor, its products or ProgIDs anywhere in the
  repository, the website or commit messages.
- **Trademarks**: "compatible with Excel RTD servers" is fine; no "Excel" in product names, no
  Microsoft or LibreOffice logos.

## Things that already cost time

- **Elevation.** A process run as administrator cannot reach the COM objects of a non-elevated
  application, so many RTD servers fail with "Class not registered". The bridge warns about it.
- **Bitness.** An RTD server shipped as a DLL loads only into a process of the same bitness as
  the Excel it was installed for; hence the win32 and win64 builds.
- **comtypes SAFEARRAY arguments**: pass the array object itself to `ConnectData` /
  `RefreshData`, not `byref(array)`; comtypes adds the indirection.
- **.NET RTD servers keep the callback object until process exit**, after Python is gone. On
  termination `com_driver.retire()` swaps its vtable for native no-op stubs (x86/x64 only) so
  late calls never enter Python. `tests/test_retire_windows.py` and the repeated-exit tests in
  `tests/test_com_windows.py` cover it.
- **COM apartment ownership**: `RtdSession` creates its `Runtime` on the caller's thread (that is
  where comtypes is imported and initialises COM) and the session thread owns exactly one
  `CoInitialize`/`CoUninitialize` pair.
- **Headless Calc computes cells lazily**: tests call `doc.calculateAll()` before reading.
- **Passive UNO registration** (`calcrtd.components`): the add-in is not imported at install
  time, when its type library is not loaded yet.
- **`=_xlfn.RTD(...)`**: cells copied from Excel arrive like this and show `#NAME?`. The alias
  `xlfnRtd` exists but Calc does not re-parse pasted text; the documented workaround is Find &
  Replace. Accepted as a known limitation.
- **Extension auto-start**: while a bridge it just launched is starting, cells show `#N/A`, not
  "bridge offline" (20 s grace). The bridge exits after `--idle-exit` seconds without an
  authenticated client.
- **`site/logo.*`** are copied from `assets/` by the Pages workflow and are git-ignored.
