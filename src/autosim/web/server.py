"""AutoSim Studio web mode: HTTP + WebSocket server around a WebSession.

    autosim-web                         # http://127.0.0.1:8765 on this PC only
    autosim-web --host 0.0.0.0          # other PCs too: a token is required (printed at start)

REST (JSON in, JSON out; a problem for the user answers 400 {"error": text}):

    GET  /api/state                     everything the UI shows (ports, channels, databases, generator, rules)
    POST /api/scan · /api/connect · /api/disconnect · /api/connect_all · /api/disconnect_all · /api/rename
    GET  /api/databases                 databases with nodes, messages and signals
    POST /api/databases/upload (multipart) · /add {path} · /use {path, channel|null} · /reload · /remove
    POST /api/tx/add · /update · /send · /signal · /generator · /remove · /stop_all
    PUT  /api/rules {rules, enabled} · POST /api/rules/enabled · /api/rules/reset
    POST /api/trace/clear · /api/trace/pause · /api/decode    GET /api/trace/export?format=csv|asc|blf
    GET  /ws                            pushes {"type": "update", rows, logs, nodes, version} 25 times per second,
                                        {"type": "status", ...} once per second and {"type": "api", live, log}
                                        (Test API values and calls) 5 times per second

Test API (signals published in the Test API tab, for test systems; see test_api.py):

    GET  /api/v1/signals[?names=A,B]    newest value of every (or the named) published signal on the bus
    GET  /api/v1/signals/<name>         newest value of one signal: value, raw, text, unit, age_ms, status
    PUT  /api/v1/signals/<name>         {"value": 3} | {"text": "D"} | {"raw": 3} -> one frame sent now (GET + SET)
    GET  /api/testapi · POST /api/testapi/add · /update · /remove · /log/clear · /import   GET /api/testapi/export

Adapter work that can take seconds (connect, scan, parsing a large ARXML) runs in a worker thread so the server
keeps pushing frames meanwhile.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import sys
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from autosim.web.session import APP_VERSION, WebError, WebSession
from autosim.web.test_api import ApiError

STATIC = Path(__file__).parent / "static"
PUSH_S = 0.04
STATUS_S = 1.0
VALUES_S = 0.2
COOKIE = "autosim_token"


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, separators=(",", ":")))


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except WebError as e:
        return _json({"error": str(e)}, 400)
    except ApiError as e:
        return _json({"error": str(e)}, e.status)
    except (KeyError, ValueError, TypeError) as e:
        if request.path.startswith("/api/"):
            return _json({"error": f"Bad request: {e}"}, 400)
        raise


@web.middleware
async def fresh_static(request: web.Request, handler):
    """The UI files are checked again on every load (304 when unchanged): after an update the browser / the
    desktop app's WebView never keeps the old app.js / app.css."""
    resp = await handler(request)
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def static_stamp() -> str:
    """Version + newest change of the UI files: a new value for each build / edit (cache busting)."""
    newest = max((f.stat().st_mtime_ns for f in STATIC.iterdir() if f.is_file()), default=0)
    return f"{APP_VERSION}-{newest // 1_000_000:x}"


@web.middleware
async def auth(request: web.Request, handler):
    token = request.app["token"]
    if token and request.path.startswith(("/api/", "/ws")):
        given = request.cookies.get(COOKIE) or request.headers.get("X-AutoSim-Token") or request.query.get("token")
        if not given or not secrets.compare_digest(given, token):
            return _json({"error": "Not authorised: open the address printed by autosim-web (with ?token=…)"}, 401)
    return await handler(request)


async def body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise WebError("Expected a JSON object")
    return data


def make_app(session: WebSession, token: str = "") -> web.Application:
    app = web.Application(middlewares=[fresh_static, auth, errors], client_max_size=64 * 1024 * 1024)
    app["session"] = session
    app["token"] = token
    app["clients"] = set()
    s = session
    r = app.router

    async def index(request):
        v = static_stamp()                           # app.js?v=…: a new version is always loaded, never a cached one
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for f in ("app.css", "app.js", "icon.svg"):
            html = html.replace(f'"/static/{f}"', f'"/static/{f}?v={v}"')
        resp = web.Response(text=html, content_type="text/html")
        if token and request.query.get("token") == token:
            resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def state(_r):
        return _json(s.state())

    async def scan(_r):
        await asyncio.to_thread(s.scan)
        return _json(s.state())

    async def connect(request):
        b = await body(request)
        ch = await asyncio.to_thread(s.connect, b["port"], b.get("channel", ""), b.get("nominal", 500_000),
                                     b.get("data", 2_000_000), b.get("fd", True), b.get("brs", True),
                                     b.get("iso", True), b.get("loop", False))
        return _json({"channel": ch})

    async def connect_all(request):
        b = await body(request)
        keys = ("nominal", "data", "fd", "brs", "iso", "loop")
        return _json(await asyncio.to_thread(s.connect_all, **{k: b[k] for k in keys if k in b}))

    async def disconnect(request):
        await asyncio.to_thread(s.disconnect, (await body(request))["channel"])
        return _json({"ok": True})

    async def disconnect_all(_r):
        return _json({"closed": await asyncio.to_thread(s.disconnect_all)})

    async def rename(request):
        b = await body(request)
        await asyncio.to_thread(s.rename, b["channel"], b["name"])
        return _json({"ok": True})

    async def rates(request):
        return _json({"rates": s.data_rates(request.query["port"], int(request.query.get("nominal", 500_000)))})

    async def databases(_r):
        resp = _json({"databases": await asyncio.to_thread(s.databases)})
        resp.enable_compression()                    # a large database is MBs of JSON: ~10x smaller gzipped
        return resp

    async def db_upload(request):
        reader = await request.multipart()
        added = []
        while (part := await reader.next()) is not None:
            if part.filename:
                content = await part.read(decode=False)
                added.append((await asyncio.to_thread(s.upload_database, part.filename, bytes(content))).name)
        if not added:
            raise WebError("No file received")
        return _json({"added": added})

    async def db_add(request):
        e = await asyncio.to_thread(s.add_database, (await body(request))["path"])
        return _json({"added": [e.name]})

    async def db_use(request):
        b = await body(request)
        await asyncio.to_thread(s.use_database, b["path"], b.get("channel"))
        return _json({"ok": True})

    async def db_reload(request):
        await asyncio.to_thread(s.reload_database, (await body(request))["path"])
        return _json({"ok": True})

    async def db_remove(request):
        await asyncio.to_thread(s.remove_database, (await body(request))["path"])
        return _json({"ok": True})

    async def tx_add(request):
        b = await body(request)
        names = b.get("messages") or [b["message"]]
        ids = [s.add_message(n, b.get("channel", "")) for n in names]
        return _json({"ids": ids})

    async def tx_update(request):
        b = await body(request)
        cid = b.pop("id")
        s.update_card(cid, **{k: v for k, v in b.items() if k in ("channel", "cycle", "fd", "brs", "enabled")})
        return _json({"ok": True})

    async def tx_send(request):
        s.send_once((await body(request))["id"])
        return _json({"ok": True})

    async def tx_signal(request):
        b = await body(request)
        return _json({"value": s.set_signal(b["id"], b["name"], b.get("value"), b.get("raw"))})

    async def tx_generator(request):
        b = await body(request)
        s.set_generator(b["id"], b["name"], b.get("kind"), b.get("params"))
        return _json({"ok": True})

    async def tx_remove(request):
        s.remove_card((await body(request))["id"])
        return _json({"ok": True})

    async def tx_stop_all(_r):
        return _json({"stopped": s.stop_all()})

    async def rules_get(_r):
        return _json(s.rules())

    async def rules_put(request):
        b = await body(request)
        return _json({"problems": s.set_rules(b.get("rules", []), b.get("enabled"))})

    async def rules_enabled(request):
        s.set_auto_response((await body(request))["enabled"])
        return _json({"ok": True})

    async def rules_reset(_r):
        s.reset_hits()
        return _json({"ok": True})

    async def measurement(request):
        b = await body(request)
        await asyncio.to_thread(s.set_measuring, bool(b.get("running")))
        return _json({"measuring": s.measuring})

    async def prefs(request):
        return _json({"prefs": s.set_prefs(**(await body(request)))})

    async def trace_clear(_r):
        s.clear_trace()
        await broadcast(app, {"type": "clear"})
        return _json({"ok": True})

    async def trace_pause(request):
        s.set_paused((await body(request))["paused"])
        return _json({"ok": True})

    async def trace_export(request):
        data, name = await asyncio.to_thread(s.export, request.query.get("format", "csv"))
        return web.Response(body=data, content_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def decode(request):
        b = await body(request)
        return _json(s.decode(b["channel"], int(b["id"]), b["data"]))

    # ---- Test API: calls of test systems (each one in the call log) ------------------------------------------
    async def api_call(request, fn, req_text: str = ""):
        client = request.headers.get("X-Forwarded-For", "") or (request.remote or "")
        path = request.path_qs
        heads = {k: request.headers[k] for k in ("Content-Type", "User-Agent") if k in request.headers}
        if "X-AutoSim-Token" in request.headers:
            heads["X-AutoSim-Token"] = "(given)"                    # never shown in the log
        t0 = time.perf_counter()
        try:
            out = fn()
        except ApiError as e:
            err = {"error": str(e)}
            s.api.record(client, request.method, path, e.status, req_text, str(e),
                         response=json.dumps(err, indent=2, ensure_ascii=False), headers=heads,
                         duration_ms=(time.perf_counter() - t0) * 1000)
            return _json(err, e.status)
        if "signals" in out:
            answer = ", ".join(f"{k}={v.get('value')} ({v.get('status')})" for k, v in out["signals"].items())
        else:
            answer = f"{out.get('value')}" + (f" ({out['text']})" if out.get("text") else "") + \
                f" {out.get('unit') or ''} · {out.get('status')}"
        s.api.record(client, request.method, path, 200, req_text, answer.strip(), out.get("sent"),
                     response=json.dumps(out, indent=2, ensure_ascii=False), headers=heads,
                     duration_ms=(time.perf_counter() - t0) * 1000)
        return _json(out)

    async def v1_list(request):
        names = [n for n in request.query.get("names", "").split(",") if n.strip()]
        return await api_call(request, lambda: s.api.read_many([n.strip() for n in names] or None))

    async def v1_get(request):
        return await api_call(request, lambda: s.api.read(request.match_info["name"]))

    async def v1_put(request):
        text = await request.text() if request.can_read_body else ""
        try:
            data = json.loads(text) if text.strip() else {}
        except ValueError:
            data = None

        def call():
            if data is None:
                raise ApiError(400, "Body is not JSON")
            return s.api.write(request.match_info["name"], data)
        return await api_call(request, call, text.strip())

    # ---- Test API: configuration (Test API tab) ---------------------------------------------------------------
    async def testapi_get(_r):
        # the browser asking is already authorised: it may show the token for test systems (curl commands, copy)
        return _json({"apis": s.api.live(), "log": list(s.api.log)[-300:], "auth": bool(token), "token": token})

    async def testapi_add(request):
        b = await body(request)
        ep = s.api.add(b["message"], b["signal"], b.get("channel", ""), b.get("name", ""), b.get("access", "get"),
                       int(b.get("stale_ms", 1000)), b.get("value"))
        return _json({"name": ep.name})

    async def testapi_update(request):
        b = await body(request)
        ep = s.api.update(b.pop("name"), **{k: v for k, v in b.items()
                                            if k in ("new_name", "channel", "access", "stale_ms", "value")})
        return _json({"name": ep.name})

    async def testapi_remove(request):
        s.api.remove((await body(request))["name"])
        return _json({"ok": True})

    async def testapi_log_clear(_r):
        s.api.clear_log()
        return _json({"ok": True})

    async def testapi_export(_r):
        return web.Response(text=json.dumps(s.api.export(), indent=2), content_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="autosim_test_api.json"'})

    async def testapi_import(request):
        b = await body(request)
        return _json({"problems": s.api.import_(b.get("data") or b, bool(b.get("replace", True)))})

    def _download(data: dict, name: str) -> web.Response:
        return web.Response(text=json.dumps(data, indent=2), content_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def board_get(_r):
        return _download(s.board_config(), "autosim.board.json")

    async def board_load(request):
        return _json(await asyncio.to_thread(s.load_board, await body(request)))

    async def project_get(_r):
        return _download(s.project_config(), "autosim.project.json")

    async def project_load(request):
        return _json(await asyncio.to_thread(s.load_project, await body(request)))

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 20)
        await ws.prepare(request)
        await ws.send_str(json.dumps({"type": "hello", "version": s.version, "rows": s.recent(),
                                      "logs": [list(x) for x in list(s.logs)[-300:]],
                                      "status": s.status_light()}))
        app["clients"].add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            app["clients"].discard(ws)
        return ws

    r.add_get("/", index)
    r.add_static("/static/", STATIC, show_index=False)
    r.add_get("/api/state", state)
    r.add_post("/api/scan", scan)
    r.add_get("/api/rates", rates)
    r.add_post("/api/connect", connect)
    r.add_post("/api/connect_all", connect_all)
    r.add_post("/api/disconnect", disconnect)
    r.add_post("/api/disconnect_all", disconnect_all)
    r.add_post("/api/rename", rename)
    r.add_get("/api/databases", databases)
    r.add_post("/api/databases/upload", db_upload)
    r.add_post("/api/databases/add", db_add)
    r.add_post("/api/databases/use", db_use)
    r.add_post("/api/databases/reload", db_reload)
    r.add_post("/api/databases/remove", db_remove)
    r.add_post("/api/tx/add", tx_add)
    r.add_post("/api/tx/update", tx_update)
    r.add_post("/api/tx/send", tx_send)
    r.add_post("/api/tx/signal", tx_signal)
    r.add_post("/api/tx/generator", tx_generator)
    r.add_post("/api/tx/remove", tx_remove)
    r.add_post("/api/tx/stop_all", tx_stop_all)
    r.add_get("/api/rules", rules_get)
    r.add_put("/api/rules", rules_put)
    r.add_post("/api/rules/enabled", rules_enabled)
    r.add_post("/api/rules/reset", rules_reset)
    r.add_post("/api/measurement", measurement)
    r.add_get("/api/board", board_get)
    r.add_post("/api/board", board_load)
    r.add_get("/api/project", project_get)
    r.add_post("/api/project", project_load)
    r.add_post("/api/prefs", prefs)
    r.add_post("/api/trace/clear", trace_clear)
    r.add_post("/api/trace/pause", trace_pause)
    r.add_get("/api/trace/export", trace_export)
    r.add_post("/api/decode", decode)
    r.add_get("/ws", ws_handler)
    r.add_get("/api/v1/signals", v1_list)
    r.add_get("/api/v1/signals/{name}", v1_get)
    r.add_put("/api/v1/signals/{name}", v1_put)
    r.add_post("/api/v1/signals/{name}", v1_put)
    r.add_get("/api/testapi", testapi_get)
    r.add_post("/api/testapi/add", testapi_add)
    r.add_post("/api/testapi/update", testapi_update)
    r.add_post("/api/testapi/remove", testapi_remove)
    r.add_post("/api/testapi/log/clear", testapi_log_clear)
    r.add_get("/api/testapi/export", testapi_export)
    r.add_post("/api/testapi/import", testapi_import)

    async def start_push(app_):
        app_["push"] = asyncio.create_task(push_loop(app_))

    async def stop_push(app_):
        app_["push"].cancel()
        for ws in list(app_["clients"]):
            await ws.close()

    app.on_startup.append(start_push)
    app.on_shutdown.append(stop_push)
    return app


async def broadcast(app: web.Application, msg: dict) -> None:
    clients = list(app["clients"])
    if not clients:
        return
    text = json.dumps(msg, separators=(",", ":"))

    async def send(ws):
        try:
            await asyncio.wait_for(ws.send_str(text), 2.0)
        except Exception:  # noqa: BLE001  a slow or gone client is dropped, the others keep going
            app["clients"].discard(ws)
            await ws.close()
    await asyncio.gather(*(send(ws) for ws in clients))


async def push_loop(app: web.Application) -> None:
    s: WebSession = app["session"]
    loop = asyncio.get_running_loop()
    next_status = loop.time() + STATUS_S
    next_values = loop.time() + VALUES_S
    last_version = s.version
    while True:
        await asyncio.sleep(PUSH_S)
        if loop.time() >= next_values:                  # generator-driven signal values, 5 times per second
            next_values = loop.time() + VALUES_S
            if any(c.enabled for c in s.cards.values()):
                await broadcast(app, {"type": "values", "cards": s.card_values()})
            if s.api.endpoints or s.api.log:                     # Test API tab: live values + new calls
                await broadcast(app, {"type": "api", "live": s.api.live(), "log": s.api.drain_log()})
        u = s.drain()
        if u["rows"] or u["logs"] or u["nodes"] or u["version"] != last_version or u["dropped"]:
            last_version = u["version"]
            await broadcast(app, {"type": "update", **u})
        if loop.time() >= next_status:
            next_status = loop.time() + STATUS_S
            await broadcast(app, {"type": "status", **s.status()})


TOKEN_FILE = "token.txt"


def stored_token(data_dir: str | Path, renew: bool = False) -> str:
    """The access token of this installation: made once and kept in <data-dir>/token.txt, so test systems can
    keep using it across restarts. `renew` makes a new one (the old one stops working)."""
    p = Path(data_dir) / TOKEN_FILE
    if not renew:
        try:
            tok = p.read_text(encoding="utf-8").strip()
            if tok and len(tok) >= 8 and tok.isprintable() and " " not in tok:
                return tok
        except OSError:
            pass
    tok = secrets.token_urlsafe(16)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tok + "\n", encoding="utf-8")
        try:
            p.chmod(0o600)                                    # readable by this user only (where supported)
        except OSError:
            pass
    except OSError as e:
        print(f"warning: token not saved ({e}); a new one will be made at the next start", flush=True)
    return tok


def process_alive(pid: int) -> bool:
    """Is process `pid` still running? (Windows: OpenProcess / WaitForSingleObject; elsewhere: signal 0.)"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(handle)
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _exit_with(pid: int) -> None:
    """The desktop app that started this server is gone (closed, crashed, killed): stop too, so no backend is
    left running in the background holding the port."""
    import signal
    while process_alive(pid):
        await asyncio.sleep(1.0)
    print(f"Process {pid} ended: stopping.", flush=True)
    signal.raise_signal(signal.SIGINT)                    # as Ctrl+C: aiohttp shuts down, settings are saved


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _open_tab(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


def open_window(url: str) -> None:
    """The UI in its own window without browser bars (Edge / Chrome / Chromium --app), else in a browser tab."""
    import shutil
    import subprocess
    names = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]
    paths = [shutil.which(n) for n in names]
    if sys.platform == "win32":
        import os
        for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
            paths += [str(Path(base) / "Microsoft/Edge/Application/msedge.exe"), str(Path(base) / "Google/Chrome/Application/chrome.exe")]
    elif sys.platform == "darwin":
        paths += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for exe in paths:
        if exe and Path(exe).is_file():
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1440,900"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    _open_tab(url)


def desktop_main() -> None:
    """`autosim`: the ICAN app from the sources - the backend (measurement stopped, like the desktop app) shown in an
    app window. The installed app is the Tauri one; the PySide6 app of 1.0 is `autosim-qt`."""
    main(["--start-stopped", "--app-window", *sys.argv[1:]])


def main(argv: list[str] | None = None) -> None:
    import argparse
    import logging
    ap = argparse.ArgumentParser(prog="autosim-web", description="AutoSim Studio in the browser")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0: every network)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default=str(Path.home() / ".autosim-web"),
                    help="settings and uploaded databases (default: ~/.autosim-web)")
    ap.add_argument("--token", default="", help="access token (default on a network address: made once, kept in "
                                                "<data-dir>/token.txt and reused at every start)")
    ap.add_argument("--new-token", action="store_true", help="make a new stored token (the old one stops working)")
    ap.add_argument("--no-token", action="store_true", help="no token even on a network address (trusted LAN)")
    ap.add_argument("--dbc", action="append", default=[], help="database to add at start (repeatable)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the web browser at start")
    ap.add_argument("--app-window", action="store_true",
                    help="open the UI in an app window (Edge / Chrome --app) instead of a browser tab")
    ap.add_argument("--start-stopped", action="store_true",
                    help="the measurement starts stopped (the desktop app; Settings can change it)")
    ap.add_argument("--exit-with-pid", type=int, default=0,
                    help="stop when this process ends (the desktop app that started the server)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("can.pcan").setLevel(logging.ERROR)     # "uptime library not available": not for users
    if a.no_token:
        token = ""
    elif a.token:
        token = a.token
    elif not is_loopback(a.host):
        token = stored_token(a.data_dir, renew=a.new_token)
    else:                                                     # this PC only: no token (--new-token still renews it)
        if a.new_token:
            stored_token(a.data_dir, renew=True)
        token = ""
    import socket
    try:                                                      # a clear message instead of a traceback
        with socket.create_server((a.host, a.port)):
            pass
    except OSError:
        print(f"Port {a.port} on {a.host} is already in use - is AutoSim Studio Web already running? "
              f"(another port: --port 8766)", flush=True)
        if getattr(sys, "frozen", False):
            try:
                input("Press Enter to close.")           # keep the console window open to be read
            except EOFError:
                pass
        raise SystemExit(1) from None
    session = WebSession(a.data_dir)
    run = session.prefs.get("measure_on_start")
    if not (run if run is not None else not a.start_stopped):
        session.ctx.can.set_measuring(False)          # like CANoe: press Start (F9) to send and trace
    for p in a.dbc:
        try:
            session.add_database(p)
        except WebError as e:
            print(f"warning: {e}")
    app = make_app(session, token)
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    url = f"http://{shown}:{a.port}/" + (f"?token={token}" if token else "")
    print(f"AutoSim Studio web mode: {url}", flush=True)
    if token:
        how = "given with --token" if a.token else f"kept in {Path(a.data_dir) / TOKEN_FILE}; --new-token makes a new one"
        print(f"Token for test systems (header X-AutoSim-Token): {token}   ({how})", flush=True)
    if a.host in ("0.0.0.0", "::"):
        print("Listening on every network interface: use this PC's IP address from other machines.", flush=True)
    print("Close this window (or press Ctrl+C) to stop.", flush=True)
    if not a.no_browser:
        async def open_browser(_app):
            asyncio.get_running_loop().call_later(0.5, open_window if a.app_window else _open_tab, url)
        app.on_startup.append(open_browser)
    try:
        if a.exit_with_pid:
            async def watch_parent(app_):
                app_["parent_watch"] = asyncio.create_task(_exit_with(a.exit_with_pid))
            app.on_startup.append(watch_parent)
        web.run_app(app, host=a.host, port=a.port, print=None)
    finally:
        session.shutdown()


if __name__ == "__main__":
    main()
