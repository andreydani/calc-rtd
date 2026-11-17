# Using RTD servers from Python

`calc_rtd` lets a Python program receive data from **Excel RTD (RealTimeData) servers**
without Excel: the same components that market-data terminals and industrial tools install to
feed live values into spreadsheets.

```python
from calc_rtd import RtdSession

with RtdSession() as rtd:
    rtd.subscribe("Vendor.ProgID", ["ACME", "LAST"])      # =RTD("Vendor.ProgID";;"ACME";"LAST")
    for update in rtd.updates():
        print(update.subscription.topics, update.value)
```

It was written as the engine of [calc-rtd](../README.md), which brings `=RTD()` to LibreOffice
Calc, but it has no dependency on LibreOffice and can be used on its own.

## Install

```
pip install calc-rtd
```

- **Windows** is required for real RTD servers (they are COM components). On other systems
  only the built-in simulated source works, which is still handy for development and tests.
- **Python 3.9+**, with the same bitness (32/64-bit) as the Excel the RTD server was installed
  for, when the server is a DLL. `python -c "import struct; print(struct.calcsize('P') * 8)"`
  tells you which one you have.
- **Not elevated.** Run your program at the same privilege level as the application that feeds
  the RTD server; do not "Run as administrator".

## Finding the ProgID and the topics

Look at a formula that works in Excel:

```
=RTD("Vendor.ProgID";;"ACME";"LAST")
       ^ ProgID      ^^ server (almost always empty)   then the topic strings
```

You can pass the pieces, or the formula itself:

```python
rtd.subscribe("Vendor.ProgID", ["ACME", "LAST"])
rtd.subscribe_formula('=RTD("Vendor.ProgID";;"ACME";"LAST")')
```

Topics are converted to text, as Excel does, so `["walk", "ACME", 38.5]` is fine.

## Try it now, without any RTD server

The ProgID `calcrtd.sim` is a simulated source built into the library:

| Topics | Value |
|---|---|
| `["clock"]` | current time as text, every second |
| `["counter"]` or `["counter", 0.2]` | 1, 2, 3... every second (or every 0.2 s) |
| `["random", 10, 20]` | random number between 10 and 20, every second |
| `["walk", "ACME", 38.5]` | random walk starting at 38.5, twice a second |
| `["echo", "hello"]` | fixed text |

Every example below and in [`examples/`](../examples) uses it by default, so they run on any
machine; replace the ProgID and topics with your vendor's.

## Three ways to receive data

### 1. Iterate over updates

```python
with RtdSession() as rtd:
    rtd.subscribe("calcrtd.sim", ["walk", "ACME", 38.5])
    rtd.subscribe("calcrtd.sim", ["clock"])
    for update in rtd.updates(timeout=30):     # stops after 30 s without any update
        if update.error:
            print("problem:", update.error)
        else:
            print(update.subscription.topics, update.value)
```

`rtd.get(timeout=1.0)` returns one update, or `None` if nothing arrived in time; use it when
you need your own loop.

### 2. Read the latest value when you need it

Every subscription keeps its most recent value. If you only care about "the current price",
ignore the stream:

```python
with RtdSession() as rtd:
    acme = rtd.subscribe("calcrtd.sim", ["walk", "ACME", 38.5])
    acme.wait(timeout=10)          # block until the first value arrives
    while True:
        print(acme.value)
        time.sleep(1)
```

### 3. Callbacks

```python
def on_price(update):
    if update.value > 40:
        print("ACME above 40:", update.value)

with RtdSession() as rtd:
    rtd.subscribe("calcrtd.sim", ["walk", "ACME", 38.5], callback=on_price)
    rtd.run_for(60)                # keep the program alive for a minute
```

Callbacks run on the session's own thread: keep them short and never block in them. Hand slow
work (database writes, network calls) to a queue or another thread.

## Errors

A wrong ProgID, a server that refuses to start or a topic it rejects do not raise at
`subscribe()`, because the work happens in the background. You find out in any of these ways:

```python
sub = rtd.subscribe("No.Such.Server", ["x"])

try:
    sub.wait(timeout=10)                 # raises RtdError, or TimeoutError
except RtdError as exc:
    print(exc)

sub.error                                # the message, or None
update.error                             # same message, delivered through updates()/callbacks
```

Before the first value arrives, `sub.value` is `None` and `sub.has_value` is `False`. Some
servers first send an empty string and the real value a moment later.

## Values

`update.value` is what the server sent, converted to the matching Python type: `float` or
`int`, `str`, `bool`, `datetime`, or `None` for an empty or error value. Most servers send
numbers as `float`. Unchanged values are not delivered again.

## Recipes

Complete, tested scripts are in [`examples/`](../examples):

| Script | What it shows |
|---|---|
| [`print_updates.py`](../examples/print_updates.py) | print every update of formulas given on the command line |
| [`log_to_csv.py`](../examples/log_to_csv.py) | append time, topic and value to a CSV file |
| [`latest_values.py`](../examples/latest_values.py) | poll the latest values once a second |
| [`alert_callback.py`](../examples/alert_callback.py) | react to a value with a callback |
| [`to_pandas.py`](../examples/to_pandas.py) | collect updates into a pandas DataFrame, one row per second |

## Checking a server from the command line

```
python -m calc_rtd "=RTD(Vendor.ProgID;;ACME;LAST)" --seconds 30
```

prints every update with a timestamp and finishes with exit code 0 if any arrived. Quotes are
optional, `,` and `;` both work, `-v` adds detailed logging, and `--file formulas.txt` reads
one formula per line. It is the quickest way to find out whether a given RTD server works
outside Excel.

## Reference

Everything importable directly from `calc_rtd` is the public API. The submodules
(`calc_rtd.engine`, `calc_rtd.host`...) are internal and may change in any release.

**`RtdSession(throttle=0.5, simulated=True, queue_size=10000)`**

| Member | Description |
|---|---|
| `subscribe(progid, topics, server="", callback=None)` | Start receiving one topic. Returns a `Subscription`. |
| `subscribe_formula(formula, callback=None)` | Same, from the text of a spreadsheet formula. |
| `updates(timeout=None)` | Iterator of `Update`. Ends when the session closes or `timeout` seconds pass in silence. |
| `get(timeout=None)` | Next `Update`, or `None`. |
| `run_for(seconds)` | Sleep while callbacks run. |
| `subscriptions` | The active subscriptions. |
| `com_available` | `True` when real RTD servers can be hosted (Windows with comtypes). |
| `dropped` | Updates discarded because your code did not read them fast enough. |
| `start()` / `close()` | Called for you by `with`. A closed session cannot be reused. |

`throttle` is the minimum time between two refreshes of one server, in seconds (Excel uses 2).
When the queue is full the oldest updates are dropped; each subscription still has its latest
value.

**`Subscription`**: `progid`, `topics`, `server`, `value`, `error`, `has_value`, `active`,
`wait(timeout=None)`, `unsubscribe()`.

**`Update`**: a named tuple `(subscription, value, error)`.

**`RtdError`**: raised by `Subscription.wait()` when the server or the topic failed.

**`parse_formula(text)`**: returns `(progid, server, topics)`.

## How it works, and what to expect

- A session owns one background thread. On Windows it is a COM single-threaded apartment with
  a message loop, which is what RTD servers expect from Excel. The servers are created, called
  and released on that thread, never on yours, so you can use a session from any thread.
- Starting a session imports `comtypes`, which initialises COM as a single-threaded apartment
  on the thread that called `start()`/`subscribe()` (the usual `comtypes` behaviour). If your
  program needs that thread in another COM mode, set `sys.coinit_flags` before.
- One server instance is created per ProgID per session, and shared by all its topics.
  Subscribing twice to the same topic costs nothing extra.
- Closing the session disconnects every topic and calls `ServerTerminate`, as Excel does when
  the last formula goes away.
- If a server stops answering its heartbeat, it is restarted and its topics are reconnected.

Limitations:

- Some vendors tie their RTD server to their own Excel add-in (login, licensing) and it may
  refuse a host other than Excel.
- Out-of-process (EXE) RTD servers need the RTD interfaces registered for marshalling, which
  installing Excel does.
- **Your data vendor's terms of use still apply.** Taking data out of a terminal into your own
  programs is often more restricted than displaying it in a spreadsheet. Check before you do.

## Testing your own code

Use the simulated source in unit tests; it needs no Windows and no server. To test against a
real COM server, [`samples/rtd-server`](../samples/rtd-server) has a minimal one in C# that
builds with the .NET Framework included in Windows (ProgID `CalcRtd.Sample`, topics `counter`
and `echo`). This project's own CI uses both.
