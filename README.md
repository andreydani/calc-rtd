<p align="center">
  <img src="https://raw.githubusercontent.com/andreydani/calc-rtd/main/assets/logo.png" alt="calc-rtd logo" width="128">
</p>

# calc-rtd

**Real-time data from Excel RTD servers, in LibreOffice Calc.** Same formula as in Excel:

```
=RTD("Vendor.ProgID";;"TOPIC1";"TOPIC2")
```

Many market-data terminals and industrial tools feed live values to spreadsheets through an
*RTD (RealTimeData) server* made for Microsoft Excel. LibreOffice Calc has no `RTD` function,
so those formulas do not work there. calc-rtd adds the function to Calc and talks to the
vendor's RTD server on your behalf.

## Download and install

You need **Windows**, **LibreOffice 6.4 or later** (tested on 24.2) and the RTD server your
data vendor installs.

1. Download **`calc-rtd-win64.oxt`** from the
   [latest release](https://github.com/andreydani/calc-rtd/releases/latest).
2. In LibreOffice, open **Tools > Extensions**, click **Add** and select the file.
3. Restart LibreOffice (close the Quickstarter too, if you use it).

That is all. If your formulas show a "not registered" error, your RTD server is a 32-bit one:
install `calc-rtd-win32.oxt` instead.

## Use

Type the formula in a cell exactly as you would in Excel:

```
=RTD("Vendor.ProgID";;"ACME";"LAST")
```

Want to see it working before involving any vendor? This one needs nothing else:

```
=RTD("calcrtd.sim";;"clock")
```

More built-in test formulas:

```
=RTD("calcrtd.sim";;"counter";0.2)         1, 2, 3... every 0.2 s
=RTD("calcrtd.sim";;"random";10;20)        random number between 10 and 20
=RTD("calcrtd.sim";;"walk";"ACME";38.5)    random walk starting at 38.5
=RTD("calcrtd.sim";;"echo";"hello")        fixed text
```

### What the cells can show

| Cell shows | Meaning |
|---|---|
| `#N/A` | Waiting for the first value, as in Excel. The very first start takes a few seconds. |
| `#RTD: bridge offline` | The helper program could not be started. Check that your antivirus did not block `calc-rtd-bridge.exe`. |
| `#RTD: ...` | A message from the RTD server or about it, for example an unknown ProgID. |
| `#NAME?` | The extension is not installed, or the formula was copied from Excel (see below). |

## Good to know

- **Copying from Excel.** Cells copied from Excel arrive as `=_xlfn.RTD(...)` and show
  `#NAME?`, because that is how Excel puts the function on the clipboard. Fix them all at once
  with **Edit > Find & Replace**: search for `_xlfn.`, replace with nothing, and choose
  "Search in: Formulas". The same goes for RTD formulas in an opened `.xlsx` file. Save your
  work as `.ods`.
- **Do not use "Run as administrator".** Start LibreOffice the normal way, like the program
  that feeds your RTD server. Otherwise the server fails with "Class not registered".
- **Security warnings.** The extension includes a small helper program
  (`calc-rtd-bridge.exe`) that is not code-signed, so Windows SmartScreen or your antivirus
  may warn about it. It is built from this repository by GitHub Actions. If you prefer not to
  run it, install `calc-rtd.oxt` (no executable inside) together with Python 3.9+ and
  `pip install calc-rtd`.
- **Windows only.** RTD servers are a Windows technology. On Linux and macOS only the
  `calcrtd.sim` test formulas work.
- **Your vendor's rules still apply.** Some vendors tie their RTD server to Excel or restrict
  how their data may be used; check their terms.

## How it works, in one paragraph

The extension provides the `RTD` function. The first time it is used, it starts a helper
program (the "bridge") in the background; the bridge loads the vendor's RTD server and plays
the part Excel normally plays, passing every update to Calc. The bridge only accepts
connections from your own user account on your own computer, and it closes about a minute
after LibreOffice does.

Details, diagnostics and development notes are in [docs/TECHNICAL.md](docs/TECHNICAL.md), and
the version history in [CHANGELOG.md](CHANGELOG.md). Problems and questions:
[open an issue](https://github.com/andreydani/calc-rtd/issues).

## About

calc-rtd is an independent open-source project under the MIT license. It is not affiliated
with or endorsed by Microsoft or The Document Foundation, and contains no Microsoft code: it
implements the client side of the publicly documented `IRtdServer` interface. Microsoft and
Excel are trademarks of the Microsoft group of companies; LibreOffice is a trademark of
The Document Foundation.
