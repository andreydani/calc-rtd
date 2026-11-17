# Technical notes

For people who want to understand, diagnose or change calc-rtd. End users only need the
[README](../README.md).

## Architecture

```
 LibreOffice Calc                      bridge (Windows process)             RTD server
+------------------+   local TCP     +--------------------------+  COM  +----------------+
| calc-rtd.oxt     |<--------------->| calc-rtd-bridge serve    |<----->| IRtdServer     |
| =RTD(...)        |  JSON lines     | acts as Excel would      |       | (vendor's own) |
+------------------+ 127.0.0.1:47365 +--------------------------+       +----------------+
```

- **Extension** (`extension/`): a Python add-in that registers the `RTD` function and returns
  an `XVolatileResult`, Calc's mechanism for values that change on their own. Standard library
  only, so it runs on the Python bundled with LibreOffice.
- **Bridge** (`src/calc_rtd_bridge/`): a separate process that serves the extension over a
  local socket. Keeping it outside LibreOffice means it can match the RTD server's bitness
  (32/64-bit), and a misbehaving server cannot take the spreadsheet down.
- **RTD client** (`src/calc_rtd/`): the library the bridge is built on. It creates the RTD
  server through COM and drives it the way Excel does (`ServerStart`, `ConnectData`,
  `UpdateNotify`, `RefreshData`, `Heartbeat`...). It knows nothing about LibreOffice and can
  be used from any Python program: see [PYTHON.md](PYTHON.md).

The protocol between the two is documented in `src/calc_rtd_bridge/protocol.py`; any program
can be a client of the bridge.

## Running the bridge yourself

The extension starts the bridge on demand, trying in order: the command in
`CALC_RTD_BRIDGE_CMD`, the executable bundled with the extension, and `calc-rtd-bridge` found
on `PATH`. To see its log, start it before LibreOffice:

```bat
calc-rtd-bridge -v serve
```

| Variable | Meaning |
|---|---|
| `CALC_RTD_PORT` | TCP port on 127.0.0.1 (default 47365) |
| `CALC_RTD_AUTOSTART=0` | the extension never starts the bridge |
| `CALC_RTD_BRIDGE_CMD` | command line the extension uses to start the bridge |
| `CALC_RTD_TOKEN_FILE` | location of the authentication token |

Updates are delivered at most every `--throttle` milliseconds (default 500; Excel uses 2000).

### Security

The bridge listens on `127.0.0.1` only and requires a random token stored in a file that only
your user account can read (`%APPDATA%\calc-rtd\token`). Programs running under your own
account can still read it; the token keeps other accounts on the machine out.

## Checking an RTD server without LibreOffice

`monitor` loads an RTD server directly and prints what Calc would receive (`python -m calc_rtd`
does the same):

```bat
calc-rtd-bridge monitor "=RTD(Vendor.ProgID;;ACME;LAST)" --seconds 30
```

The formula can be pasted from Excel (`,` or `;` separators, quotes optional). For several
formulas use `--file formulas.txt` (one per line) or `--progid X --topics A B`. Put `-v` before
`monitor` for detailed logging. The exit code is 0 if updates were received.

`calc-rtd-bridge watch ...` does the same through the socket of a running bridge.

Things that commonly go wrong:

- **"Class not registered"**: wrong bitness (use the other build), a mistyped ProgID, or the
  bridge is running elevated while the application that feeds the RTD server is not.
- **Out-of-process RTD servers** need the RTD interfaces registered for marshalling, which
  installing Excel does.

## Sample RTD server

`samples/rtd-server` contains a minimal RTD server in C# (ProgID `CalcRtd.Sample`) that needs
only the .NET Framework included with Windows. From an elevated PowerShell:

```powershell
.\samples\rtd-server\build.ps1 -Arch x64      # add -Unregister to remove it
calc-rtd-bridge monitor "=RTD(CalcRtd.Sample;;counter)" --seconds 5
```

The CI uses it to test the COM layer, in 32 and 64 bits, on every commit.

## Development

```bash
pip install -e . pytest
pytest                                    # client, bridge, examples (COM tests need the sample server)
python tools/build_oxt.py                 # builds dist/calc-rtd.oxt (no LibreOffice SDK needed)
unopkg add -f dist/calc-rtd.oxt
python tests/integration_libreoffice.py   # end to end against a headless LibreOffice
```

`tools/build_oxt.py` writes the UNO type library (`.rdb`) directly in its binary format, so
`unoidl-write` from the SDK is not required. The project version lives only in
`src/calc_rtd/__init__.py`.

Releases: bump the version, update `CHANGELOG.md`, then create a GitHub release with the tag
`vX.Y.Z`. The workflow attaches the extensions and publishes the package to PyPI.
