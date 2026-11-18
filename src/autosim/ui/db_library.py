"""Database library: every database file the user added, whether it is used (loaded into the engine) and on which
channel - CANoe's "databases of the configuration".

    Database desktop  adds / removes / reloads files and browses them (DatabaseExplorer)
    Setup desktop     ticks the databases to use and assigns each to a channel or to all channels (DatabaseSetup)
    Simulation        picks messages / signals of the databases in use (SignalSelectDialog)

Unticking a database unloads it from the engine but keeps it in the library; removing it drops it altogether.
Files loaded by other paths (a project, a drop on the Signals window) join the library automatically. The library
persists in QSettings ("dblib/entries"); the files in use are also kept in "last/dbc" for older versions.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from autosim.core.can_engine.database import ANY, DatabaseError, DatabaseRegistry


@dataclass
class DbEntry:
    path: str
    active: bool = True
    channel: str = ANY                        # "*" = every channel

    @property
    def name(self) -> str:
        return Path(self.path).name


class DatabaseLibrary(QObject):
    changed = Signal()

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.entries: list[DbEntry] = []
        self._parsed: dict[str, object] = {}          # cantools databases of files not in use (browsing only)
        self._busy = False                            # our own load / unload: the bridge echo is not news
        bridge.dbLoaded.connect(self._engine_loaded)
        bridge.dbUnloaded.connect(self._engine_unloaded)

    # ---- queries ------------------------------------------------------------------------------
    def entry(self, path: str) -> DbEntry | None:
        return next((e for e in self.entries if DatabaseRegistry._same(e.path, path)), None)

    def database(self, path: str):
        """The cantools database of a library file (the engine's copy when in use, else parsed on demand)."""
        reg = self.ctx.can.registry
        for ch, paths in reg._paths.items():
            for p, db in zip(paths, reg._dbs[ch]):
                if DatabaseRegistry._same(p, path):
                    return db
        key = str(Path(path).resolve())
        if key not in self._parsed:
            try:
                self._parsed[key] = DatabaseRegistry().load(path)
            except DatabaseError:
                self._parsed[key] = None
        return self._parsed[key]

    def active(self) -> list[DbEntry]:
        return [e for e in self.entries if e.active]

    # ---- changes ------------------------------------------------------------------------------
    def add(self, path: str, active: bool = True, channel: str = ANY) -> DbEntry:
        """Add a file (raises DatabaseError if it does not parse); in use right away unless active=False.
        Adding a file already in the library reloads it."""
        e = self.entry(path)
        new = e is None
        if new:
            e = DbEntry(str(path), False, channel)
        if active:
            self._load(e, e.channel if not new else channel)     # raises before a broken file is listed
        elif new and self.database(path) is None:
            raise DatabaseError(f"{Path(path).name}: cannot be read")
        if new:
            self.entries.append(e)
        self.changed.emit()
        return e

    def set_active(self, path: str, on: bool) -> bool:
        e = self.entry(path)
        if e is None or e.active == on:
            return False
        if on:
            self._load(e, e.channel)
        else:
            self._unload(e)
        self.changed.emit()
        return True

    def set_channel(self, path: str, channel: str) -> None:
        e = self.entry(path)
        if e is None or e.channel == channel:
            return
        e.channel = channel
        if e.active:                                  # move it: unload from the old channel, load on the new one
            self._unload(e)
            self._load(e, channel)
        self.changed.emit()

    def remove(self, path: str) -> None:
        e = self.entry(path)
        if e is None:
            return
        if e.active:
            self._unload(e)
        self.entries.remove(e)
        self._parsed.pop(str(Path(path).resolve()), None)
        self.changed.emit()

    def reload(self, path: str) -> None:
        e = self.entry(path)
        if e is None:
            return
        self._parsed.pop(str(Path(path).resolve()), None)
        if e.active:
            self._load(e, e.channel)
        self.changed.emit()

    def _load(self, e: DbEntry, channel: str) -> None:
        self._busy = True
        try:
            self.ctx.can.load_database(e.path, channel)
        finally:
            self._busy = False
        e.active, e.channel = True, channel
        self._parsed.pop(str(Path(e.path).resolve()), None)

    def _unload(self, e: DbEntry) -> None:
        self._busy = True
        try:
            if self.ctx.can.registry.is_loaded(e.path):
                self.ctx.can.unload_database(e.path)
        finally:
            self._busy = False
        e.active = False

    # ---- engine changes made elsewhere ----------------------------------------------------------------
    def _engine_loaded(self, path: str) -> None:
        if self._busy:
            return
        e = self.entry(path)
        if e is None:
            self.entries.append(DbEntry(str(path), True, self._channel_of(path)))
        else:
            e.active = True
        self.changed.emit()

    def _engine_unloaded(self, path: str) -> None:
        if self._busy:
            return
        e = self.entry(path)
        if e is not None:
            e.active = False
            self.changed.emit()

    def _channel_of(self, path: str) -> str:
        for f in self.ctx.can.registry.loaded():
            if DatabaseRegistry._same(f["path"], path):
                return f["channel"]
        return ANY

    # ---- persistence ---------------------------------------------------------------------------------
    def save(self, settings) -> None:
        settings.setValue("dblib/entries", json.dumps([asdict(e) for e in self.entries]))
        settings.setValue("last/dbc", [e.path for e in self.entries if e.active])

    def restore(self, settings) -> list[str]:
        """Re-create the library; returns the files that could not be loaded."""
        raw = settings.value("dblib/entries")
        try:
            items = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            items = None
        if items is None:                             # older versions: only the files in use
            dbcs = settings.value("last/dbc") or []
            items = [{"path": p, "active": True, "channel": ANY} for p in ([dbcs] if isinstance(dbcs, str) else dbcs)]
        missing = []
        for it in items:
            p = it.get("path", "")
            if not p or not Path(p).exists():
                if p:
                    missing.append(p)
                continue
            try:
                self.add(p, bool(it.get("active", True)), str(it.get("channel", ANY)))
            except DatabaseError:
                missing.append(p)
        return missing
