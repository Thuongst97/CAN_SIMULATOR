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
    GET  /ws                            pushes {"type": "update", rows, logs, nodes, version} 25 times per second
                                        and {"type": "status", ...} once per second

Adapter work that can take seconds (connect, scan, parsing a large ARXML) runs in a worker thread so the server
keeps pushing frames meanwhile.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
from pathlib import Path

from aiohttp import WSMsgType, web

from autosim.web.session import WebError, WebSession

STATIC = Path(__file__).parent / "static"
PUSH_S = 0.04
STATUS_S = 1.0
COOKIE = "autosim_token"


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, separators=(",", ":")))


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except WebError as e:
        return _json({"error": str(e)}, 400)
    except (KeyError, ValueError, TypeError) as e:
        if request.path.startswith("/api/"):
            return _json({"error": f"Bad request: {e}"}, 400)
        raise


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
    app = web.Application(middlewares=[auth, errors], client_max_size=64 * 1024 * 1024)
    app["session"] = session
    app["token"] = token
    app["clients"] = set()
    s = session
    r = app.router

    async def index(request):
        resp = web.FileResponse(STATIC / "index.html")
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
        return _json({"databases": await asyncio.to_thread(s.databases)})

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
    r.add_post("/api/trace/clear", trace_clear)
    r.add_post("/api/trace/pause", trace_pause)
    r.add_get("/api/trace/export", trace_export)
    r.add_post("/api/decode", decode)
    r.add_get("/ws", ws_handler)

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
    last_version = s.version
    while True:
        await asyncio.sleep(PUSH_S)
        u = s.drain()
        if u["rows"] or u["logs"] or u["nodes"] or u["version"] != last_version or u["dropped"]:
            last_version = u["version"]
            await broadcast(app, {"type": "update", **u})
        if loop.time() >= next_status:
            next_status = loop.time() + STATUS_S
            await broadcast(app, {"type": "status", **s.status()})


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> None:
    import argparse
    import logging
    ap = argparse.ArgumentParser(prog="autosim-web", description="AutoSim Studio in the browser")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0: every network)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default=str(Path.home() / ".autosim-web"),
                    help="settings and uploaded databases (default: ~/.autosim-web)")
    ap.add_argument("--token", default="", help="access token (default: random when not listening on loopback)")
    ap.add_argument("--no-token", action="store_true", help="no token even on a network address (trusted LAN)")
    ap.add_argument("--dbc", action="append", default=[], help="database to add at start (repeatable)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    token = "" if a.no_token else (a.token or ("" if is_loopback(a.host) else secrets.token_urlsafe(16)))
    session = WebSession(a.data_dir)
    for p in a.dbc:
        try:
            session.add_database(p)
        except WebError as e:
            print(f"warning: {e}")
    app = make_app(session, token)
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    url = f"http://{shown}:{a.port}/" + (f"?token={token}" if token else "")
    print(f"AutoSim Studio web mode: {url}", flush=True)
    if a.host in ("0.0.0.0", "::"):
        print("Listening on every network interface: use this PC's IP address from other machines.", flush=True)
    try:
        web.run_app(app, host=a.host, port=a.port, print=None)
    finally:
        session.shutdown()


if __name__ == "__main__":
    main()
