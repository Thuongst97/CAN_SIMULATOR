# AutoSim Studio 1.0.0

**Downloads** (Windows 10 / 11, 64-bit; nothing else to install, Python and Qt are included):

| File | For |
|---|---|
| `AutoSimStudio-Setup-1.0.0.exe` | Desktop app **and** web mode (choose *Desktop and web*, *Desktop only* or *Web only*) |
| `AutoSimStudioWeb-Setup-1.0.0.exe` | **Web mode only**: smaller download, for a test bench PC or a server that test systems call |

Settings, databases and auto response rules of 0.2.0 are kept.

## New

- **Test API (web mode)** - a new tab (`Ctrl+4`) publishes CAN signals for test systems (pytest, Robot Framework,
  Jenkins, LabVIEW, Postman …):
  - `GET /api/v1/signals/<name>` returns the newest value **on the bus** as JSON (value, raw, value-table name,
    unit, age, status `ok` / `stale` / `no_data`); `PUT` with `{"value"}`, `{"text": "D"}` or `{"raw"}` makes
    AutoSim send one frame now. Several signals in one call: `GET /api/v1/signals?names=A,B`.
  - Double-click a signal in the database tree to publish it; new APIs start at the signal's DBC initial value.
    The table shows the live value, its age and status, and the GET / PUT calls.
  - **Call log**: every call with client, request, HTTP status, answer and the frame sent; **click a call** to see
    the whole request, JSON response and frame, with *Copy as curl*.
  - Works on its own, apart from the Interactive generator. APIs are saved and can be exported / imported (JSON).
  - Step-by-step guide: `docs/TEST_API_GUIDE.md` (also in the Start menu of the web installer).
- **Access token that stays the same**: on a network address the token is made once and kept (`token.txt` in the
  data folder), so test systems are configured once. The console and the Test API tab show it (with Copy);
  `--new-token` makes a new one.
- **Signal boards**: the Signals table (messages, channel, cycle, values, generators) is kept between runs; *File →
  Save signal board… / Open signal board…* stores it with the auto response rules and the databases they need.
  Project files include the Signals table too.
- **Bus trace name filter**: several names with `|` (`Speed|Gear|Status`), **signal names** (the fixed view lists
  only the matching signals), wildcards (`ADRV_*`, `Ge?r`), exclusions (`!CHECKSUM|!COUNTER`); the clock button
  keeps the **5 recent filters**. Desktop and web.
- **Simulation › Trace** has the full Bus trace toolbar and filters (Fixed, Pause, Clear, Export, Follow, Highlight).
- **Auto response editor**:
  - *Then send* edits the response signals like the Interactive generator: **Raw, Physical, Symbolic**, unit and
    range; signals left alone send their initial value (greyed); `rx` expressions still work.
  - Conditions and responses offer the **DBC value tables** (pick *D (3)*).
  - *Add scenario*, and an on / off switch for Auto response.
- **Database tree**: a **DLC** column; CAN FD messages read e.g. `32 FD` (a message over 8 bytes is always CAN FD).

## Changed

- **Faster start** with a saved Signals table: the window shows at once and the table is rebuilt in steps (88
  messages: usable in under 1 s instead of about 3 s).
- Simulation desktop: green *Add signals…*, Interactive generator and Auto response share the width equally.

## Fixed

- **DBC files saved as UTF-8** (with or without BOM) showed `°C`, `°F`, `µs` as `Â°C` …; a BOM made the file
  unreadable. Both UTF-8 and Windows-1252 DBCs now read correctly.
- **Crash** in the auto response editor when a signal's initial value has a value-table name; such signals also
  started at 0 in the generator instead of their initial value.
- A clicked signal row stayed highlighted after clicking elsewhere.

## Checked

396 automated tests (engine, adapters with simulated hardware, GUI, web API, Test API, browser end to end) on Linux,
and the engine / web / Test API tests on Windows in the build. Every example of the Test API guide was run against a
live server (curl and Python `requests`).
