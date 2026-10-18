"""DBC/ARXML loading (cantools) and the in-memory database registry."""
from __future__ import annotations

import threading
from pathlib import Path

import cantools

from autosim.core.common.frame import CanFrame

ANY = "*"


class DatabaseError(Exception):
    """A database file could not be loaded."""


class DatabaseRegistry:
    """Holds loaded cantools databases keyed by channel ('*' = applies to every channel)
    and resolves frame id -> Message."""

    def __init__(self) -> None:
        self._dbs: dict[str, list[cantools.database.Database]] = {}
        self._by_id: dict[str, dict[int, object]] = {}
        self._paths: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def load(self, path: str | Path, channel: str = ANY):
        """Load .dbc/.arxml/.kcd/.sym; raises DatabaseError with the file name on failure.

        DBC files that declare ids above 0x7FF without the extended-frame flag (seen in real production DBCs)
        are reinterpreted as 29-bit ids, like Vector tools do; `self.warnings` says which messages."""
        path = Path(path)
        self.warnings: list[str] = []
        try:
            db = cantools.database.load_file(str(path), strict=False)
        except Exception as e:  # cantools raises several unrelated exception types
            fixed = _fix_oversized_standard_ids(path, e)
            if fixed is None:
                raise DatabaseError(f"{path.name}: {e}") from e
            db, n_ext, skipped = fixed
            if n_ext:
                self.warnings.append(f"{path.name}: {n_ext} message id(s) above 0x7FF had no extended flag; "
                                     f"treated as 29-bit ids")
            if skipped:
                self.warnings.append(f"{path.name}: skipped {len(skipped)} message(s) whose id does not fit "
                                     f"29 bits (not valid on a CAN bus): {', '.join(skipped[:6])}"
                                     + (" ..." if len(skipped) > 6 else ""))
        with self._lock:
            self._dbs.setdefault(channel, []).append(db)
            self._paths.setdefault(channel, []).append(str(path))
            idx = self._by_id.setdefault(channel, {})
            for m in db.messages:
                idx[m.frame_id] = m
        return db

    def clear(self) -> None:
        with self._lock:
            self._dbs.clear()
            self._by_id.clear()
            self._paths.clear()

    @property
    def paths(self) -> list[str]:
        with self._lock:
            return [p for ps in self._paths.values() for p in ps]

    def databases(self):
        with self._lock:
            return [db for dbs in self._dbs.values() for db in dbs]

    def messages(self) -> list:
        return [m for db in self.databases() for m in db.messages]

    def message_by_name(self, name: str):
        for m in self.messages():
            if m.name == name:
                return m
        return None

    def message_by_id(self, channel: str, arbitration_id: int):
        with self._lock:
            for ch in (channel, ANY):
                m = self._by_id.get(ch, {}).get(arbitration_id)
                if m is not None:
                    return m
        return None

    def decode(self, frame: CanFrame):
        """-> (message, {signal: value}) or None if the id is unknown / the payload undecodable."""
        m = self.message_by_id(frame.channel, frame.arbitration_id)
        if m is None:
            return None
        data = bytes(frame.data)
        if len(data) < m.length:
            data = data + bytes(m.length - len(data))
        try:
            return m, m.decode(data, decode_choices=False, scaling=True,
                               allow_truncated=True, allow_excess=True)
        except Exception:
            return None

    @staticmethod
    def signal_info(sig) -> dict:
        return {
            "name": sig.name, "start": sig.start, "length": sig.length,
            "byte_order": sig.byte_order, "signed": sig.is_signed,
            "factor": sig.scale, "offset": sig.offset,
            "min": sig.minimum, "max": sig.maximum, "unit": sig.unit or "",
            "choices": {int(k): str(v) for k, v in sig.choices.items()} if sig.choices else {},
            "initial": sig.initial,
        }

    def tree(self) -> list[dict]:
        """Node -> messages -> signals for the DBC explorer."""
        nodes: dict[str, list[dict]] = {}
        for db in self.databases():
            for n in db.nodes:
                nodes.setdefault(n.name, [])
            for m in db.messages:
                entry = {
                    "name": m.name, "id": m.frame_id, "extended": m.is_extended_frame,
                    "length": m.length, "fd": m.is_fd, "cycle_time": m.cycle_time,
                    "signals": [self.signal_info(s) for s in m.signals],
                }
                for sender in (m.senders or ["(no node)"]):
                    nodes.setdefault(sender, []).append(entry)
        return [{"name": n, "messages": msgs} for n, msgs in nodes.items()]


# DBC statements that reference a message id (the number right after the keyword; bit 31 = extended flag)
_ID_KEYWORDS = r"(?:BO_|CM_ BO_|CM_ SG_|VAL_|SIG_VALTYPE_|BO_TX_BU_|SG_MUL_VAL_|SIG_GROUP_|BA_ \"[^\"]*\" BO_|BA_ \"[^\"]*\" SG_)"


def _fix_oversized_standard_ids(path: Path, error: Exception):
    """Repair DBCs that real tools accept but cantools rejects ("Standard frame id ... more than 11 bits"):
      * id 0x800..0x1FFFFFFF without the extended flag  -> treated as a 29-bit id (flag added)
      * id that cannot exist on a bus (> 29 bits)       -> message and every reference to it skipped
    Returns (database, extended_count, skipped_names) or None if this is not that problem."""
    import re
    if path.suffix.lower() != ".dbc" or "more than 11 bits" not in str(error):
        return None
    try:
        text = path.read_text(encoding="cp1252", errors="replace")
    except OSError:
        return None
    blocks = {int(i): name for i, name in re.findall(r"^BO_ (\d+) (\w+)", text, re.M)}
    to_ext = {i for i in blocks if 0x7FF < i <= 0x1FFFFFFF}
    to_drop = {i for i in blocks if 0x1FFFFFFF < i < 0x80000000}
    if not to_ext and not to_drop:
        return None
    for i in to_drop:                                         # the message block: BO_ line + its SG_ lines
        text = re.sub(rf"^BO_ {i} .*?(?=^\S|\Z)", "", text, flags=re.M | re.S)
        text = re.sub(rf"^{_ID_KEYWORDS} {i}\b.*?;[ \t]*\r?\n", "", text, flags=re.M | re.S)

    def to_extended(m):
        ident = int(m.group(1))
        return m.group(0).replace(m.group(1), str(ident | 0x80000000), 1) if ident in to_ext else m.group(0)
    text = re.sub(rf"(?m)^(?:\s*)?{_ID_KEYWORDS} (\d+)", to_extended, text)
    try:
        db = cantools.database.load_string(text, database_format="dbc", strict=False)
    except Exception:
        return None
    return db, len(to_ext), sorted(blocks[i] for i in to_drop)
