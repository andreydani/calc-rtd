# calc-rtd

**Use Excel RTD (RealTimeData) servers from LibreOffice Calc**, with the formula you already know:

```
=RTD("Vendor.ProgID";;"TOPIC1";"TOPIC2")
```

Many market-data terminals and industrial tools publish live data to spreadsheets through an
*RTD server*, a COM component that Microsoft Excel loads for its `RTD()` function. LibreOffice
Calc has no such function. calc-rtd adds it: a Calc extension provides `RTD()`, and a small
Windows bridge loads the vendor's RTD server and plays Excel's part of the conversation.

> **Status: experimental (0.1).** Verified end to end on Windows with LibreOffice and a
> third-party .NET RTD server, and on Linux with the built-in simulated source.

calc-rtd is an independent project. It is not affiliated with or endorsed by Microsoft or
The Document Foundation. It contains no Microsoft code: it implements the client side of the
publicly documented `IRtdServer` interface.

## How it works

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
- **Bridge** (`src/calc_rtd_bridge/`): a separate process that creates the RTD server through
  COM and drives it the way Excel does (`ServerStart`, `ConnectData`, `UpdateNotify`,
  `RefreshData`, `Heartbeat`...). Keeping it outside LibreOffice means it can match the RTD
  server's bitness (32/64-bit), and a misbehaving server cannot take the spreadsheet down.

## Install

Requirements: Windows and LibreOffice 4.0 or later.

1. Download the extension from the [Releases](https://github.com/andreydani/calc-rtd/releases)
   page:
   - `calc-rtd-win64.oxt` or `calc-rtd-win32.oxt` include the bridge as a standalone
     program; nothing else to install. Pick the one that matches your RTD server (see below).
   - `calc-rtd.oxt` contains no executable. It needs Python 3.9+ and
     `pip install calc-rtd`; choose it if you prefer not to run a downloaded `.exe`.
2. In LibreOffice: **Tools > Extensions > Add**, select the file, then restart LibreOffice
   (including the Quickstarter, if you use it).

> **32-bit or 64-bit?** An RTD server shipped as a DLL only loads into a process of the same
> bitness as the Excel it was installed for; that is the bridge you need (it does not have to
> match LibreOffice). If unsure, try `win64` first. Out-of-process (EXE) servers work with both.

> **The bundled program is not code-signed.** Windows SmartScreen or your antivirus may warn
> about it. It is built from this repository by GitHub Actions; use the Python-only extension
> if you would rather not run it.

> **Do not "Run as administrator".** Run LibreOffice (and the bridge) at the same privilege
> level as the application that feeds the RTD server. An elevated process cannot see the COM
> objects of a normal one, and the server fails with "Class not registered".

## Use

In Calc, type the formula as you would in Excel:

```
=RTD("Vendor.ProgID";;"ACME";"LAST")
```

The extension starts the bridge in the background the first time an `RTD` formula is
calculated, and the bridge exits about a minute after LibreOffice closes. Before the first
value arrives a cell shows `#N/A`, as in Excel. Server errors appear as text starting with
`#RTD:`, and `#RTD: bridge offline` means the bridge could not be started or reached.

Formulas pasted from Excel arrive as `=_xlfn.RTD(...)`; that spelling works too.

You can also run the bridge yourself, for example to see its log:

```bat
calc-rtd-bridge -v serve
```

Environment variables, read by the extension and the bridge:

| Variable | Meaning |
|---|---|
| `CALC_RTD_PORT` | TCP port on 127.0.0.1 (default 47365) |
| `CALC_RTD_AUTOSTART=0` | the extension never starts the bridge |
| `CALC_RTD_BRIDGE_CMD` | command line the extension uses to start the bridge |
| `CALC_RTD_TOKEN_FILE` | location of the authentication token |

### Simulated source (any OS, no RTD server needed)

```
=RTD("calcrtd.sim";;"clock")               current time, every second
=RTD("calcrtd.sim";;"counter";0.2)         1, 2, 3... every 0.2 s
=RTD("calcrtd.sim";;"random";10;20)        random number between 10 and 20
=RTD("calcrtd.sim";;"walk";"ACME";38.5)    random walk starting at 38.5
=RTD("calcrtd.sim";;"echo";"hello")        fixed text
```

## Try an RTD server without LibreOffice

`monitor` loads an RTD server directly and prints what Calc would receive. Use it to check
that a given server works with the bridge:

```bat
calc-rtd-bridge monitor "=RTD(Vendor.ProgID;;ACME;LAST)" --seconds 30
```

The formula can be pasted from Excel (`,` or `;` separators, quotes optional). For several
formulas use `--file formulas.txt` (one per line) or `--progid X --topics A B`. Put `-v` before
`monitor` for detailed logging. The exit code is 0 if updates were received.

`calc-rtd-bridge watch ...` does the same through the socket of a running bridge.

## Known limitations

- **Windows only** for real servers: RTD is a COM technology.
- **Excel files:** Calc imports RTD formulas in `.xlsx` files as `#NAME?`; re-enter them
  (copy and paste from Excel works). Saving to `.xlsx` does not produce a formula Excel
  understands. Use `.ods`.
- Some vendors tie their RTD server to their own Excel add-in (login, licensing) and may
  refuse a host other than Excel. Your data vendor's terms of use still apply.
- Out-of-process RTD servers need the RTD interfaces registered for marshalling, which
  installing Excel does.
- The bridge listens on `127.0.0.1` only and requires a random token stored in a file that
  only your user account can read (`%APPDATA%\calc-rtd\token`). Programs running under your
  own account can still read it; the token keeps other accounts on the machine out.
- Updates are delivered at most every `--throttle` milliseconds (default 500; Excel uses 2000).

## Development

```bash
pip install -e . pytest
pytest                                    # bridge, protocol, RTD host with a fake driver
python tools/build_oxt.py                 # builds dist/calc-rtd.oxt (no LibreOffice SDK needed)
unopkg add -f dist/calc-rtd.oxt
python tests/integration_libreoffice.py   # end to end against a headless LibreOffice
```

To use the bridge from source instead of a release:

```bat
pip install git+https://github.com/andreydani/calc-rtd
```

`tools/build_oxt.py` writes the UNO type library (`.rdb`) directly in its binary format, so
`unoidl-write` from the SDK is not required. The protocol between extension and bridge is
documented in `src/calc_rtd_bridge/protocol.py`; any program can be a client of the bridge.

## License

MIT. Microsoft and Excel are trademarks of the Microsoft group of companies; LibreOffice is a
trademark of The Document Foundation.
