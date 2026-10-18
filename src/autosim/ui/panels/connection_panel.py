"""CAN connection panel: CANable 2.0 adapters (and the built-in simulated nodes), one channel per adapter."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from autosim.core.common.config import NodeSetting, ProjectConfig
from autosim.core.common.frame import CanChannelConfig
from autosim.hardware.esp32_gateway.serial_node import NodeError
from dataclasses import replace

from autosim.ui.theme import palette, state_color
from autosim.ui.widgets import CONTROL_HEIGHT, StatusBadge, caption, set_kind


NOMINAL = [125_000, 250_000, 500_000, 1_000_000]
DATA = [1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000]
SCAN_WIDTH = 80
ACTION_WIDTH = 130


def _rate(v: int) -> str:
    return f"{v / 1_000_000:g} Mbit/s" if v >= 1_000_000 else f"{v // 1000} kbit/s"


class CanConnectionPanel(QWidget):
    channelsChanged = Signal(list)              # open channel names, after every connect / disconnect

    """Open/close CAN FD channels backed by CANable 2.0 adapters (or the built-in SIM nodes).

    One primary button toggles the channel named in the form: blue *Connect* while it is closed,
    red *Disconnect* while it is open. Any number of adapters can be open at once (CAN1, CAN2, ...), each on
    its own port, sending and receiving in parallel: after a connect the form moves on to the next free port
    and channel name, and *Connect all* opens every free adapter in one go with the form's bit rates.
    """

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._open: dict[str, NodeSetting] = {}           # settings of every open channel (for the project file)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.channel = QLineEdit("CAN1")
        self.nominal = QComboBox()
        for v in NOMINAL:
            self.nominal.addItem(_rate(v), v)
        self.nominal.setCurrentIndex(2)
        self.data = QComboBox()
        self.data.setToolTip("Data-phase bit rate. CANable: 2 or 5 Mbit/s (its firmware presets). Simulated "
                             "nodes: an integer\n"
                             "multiple (1-10x) of the nominal rate, e.g. 500 kbit/s -> up to 5 Mbit/s, 1 Mbit/s -> up to 8.")
        self._fill_data_rates(2_000_000)
        self.fd, self.brs, self.iso = QCheckBox("FD"), QCheckBox("BRS"), QCheckBox("ISO")
        self.loop = QCheckBox("Loop")
        self.loop.setToolTip("Internal loopback: the board receives its own frames, no CAN bus needed (bench test)")
        self.fd.setToolTip("CAN FD")
        self.iso.setToolTip("ISO 11898-1:2015 CRC (uncheck for non-ISO / Bosch)")
        for c in (self.fd, self.brs, self.iso):
            c.setChecked(True)
        self.scan_btn = QPushButton("Scan")
        self.toggle_btn = QPushButton("Connect")
        self.all_btn = QPushButton("Connect all")
        self.all_btn.setToolTip("Open every detected adapter that is not connected yet (CAN1, CAN2, ...)\n"
                                "with the bit rates and mode of the form")
        self.badge = StatusBadge("Offline", "offline")
        # strict sizing: 32px inputs, 80px Scan, 130px Connect/Disconnect (never stretched)
        for w in (self.port, self.channel, self.nominal, self.data):
            w.setFixedHeight(CONTROL_HEIGHT)
        self.scan_btn.setFixedSize(SCAN_WIDTH, CONTROL_HEIGHT)
        self.toggle_btn.setFixedSize(ACTION_WIDTH, CONTROL_HEIGHT)
        self.all_btn.setFixedSize(ACTION_WIDTH, CONTROL_HEIGHT)

        title = QLabel("CAN FD node")
        title.setProperty("role", "title")
        hdr = QHBoxLayout()
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self.badge)

        port_row = QHBoxLayout()                                   # Port + Scan: one tight row
        port_row.setSpacing(8)
        port_row.addWidget(self.port, 1)
        port_row.addWidget(self.scan_btn)
        flags = QHBoxLayout()
        flags.setSpacing(12)
        for c in (self.fd, self.brs, self.iso, self.loop):
            flags.addWidget(c)
        flags.addStretch()

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.addWidget(caption("Port"), 0, 0)
        self.port_hint = QLabel("")                       # recognised hardware, e.g. "CANable 2.0 (slcan)"
        self.port_hint.setProperty("role", "caption")
        grid.addWidget(self.port_hint, 0, 1, Qt.AlignRight)
        grid.addLayout(port_row, 1, 0, 1, 2)
        grid.addWidget(caption("Channel name"), 2, 0)
        grid.addWidget(caption("Nominal rate"), 2, 1)
        grid.addWidget(self.channel, 3, 0)
        grid.addWidget(self.nominal, 3, 1)
        grid.addWidget(caption("Data rate"), 4, 0)
        grid.addWidget(caption("Mode"), 4, 1)
        grid.addWidget(self.data, 5, 0)
        grid.addLayout(flags, 5, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Channel", "Node", "Port", "State", "Rx", "Tx", "Err"])
        self.table.setToolTip("Double-click a Node name (or right-click) to rename the board")
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setFixedHeight(32)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)
        action_layout = QHBoxLayout()                    # Connect / Disconnect: compact, LEFT-aligned
        action_layout.addWidget(self.toggle_btn)         # same left edge as the Port / Data rate column
        action_layout.addWidget(self.all_btn)
        action_layout.addStretch()                       # pushes everything else to the right

        v.addLayout(hdr)
        v.addLayout(grid)
        v.addLayout(action_layout)
        v.addWidget(self.table, 1)

        self.scan_btn.clicked.connect(self.scan)
        self.port.currentTextChanged.connect(lambda _t: self._device_changed())
        self.toggle_btn.clicked.connect(self.toggle)
        self.all_btn.clicked.connect(lambda: self.connect_all())
        self.channel.textChanged.connect(self._sync_button)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.cellDoubleClicked.connect(lambda r, c: c == 1 and self.rename_node(self.table.item(r, 0).text()))
        self.fd.toggled.connect(lambda on: (self.data.setEnabled(on), self.brs.setEnabled(on)))
        self.nominal.currentIndexChanged.connect(lambda _i: self._fill_data_rates(self.data.currentData()))
        bridge.nodeState.connect(self.on_node_state)
        self._warned_dups: set[str] = set()
        self.scan()
        self._sync_button()

    def _fill_data_rates(self, prefer: int | None) -> None:
        """List only data rates valid for the current nominal rate (integer multiple, ratio 1..10), keeping the
        previous choice when it is still valid, else the closest one."""
        nominal = self.nominal.currentData() or 500_000
        if hasattr(self, "port_hint") and self.device_kind() == "slcan":
            valid = [2_000_000, 5_000_000]                # CANable 2.0 firmware presets (independent of nominal)
        else:
            valid = [v for v in DATA if v >= nominal and v % nominal == 0 and v // nominal <= 10]
            if nominal not in valid:
                valid.insert(0, nominal)                  # 1x: data phase at the nominal rate
        self.data.blockSignals(True)
        self.data.clear()
        for v in valid:
            self.data.addItem(_rate(v) + ("  (= nominal)" if v == nominal else ""), v)
        best = min(valid, key=lambda v: abs(v - (prefer or 2_000_000)))
        self.data.setCurrentIndex(valid.index(best))
        self.data.blockSignals(False)

    # ---- primary action ----------------------------------------------------------
    def _is_open(self) -> bool:
        return self.channel.text().strip() in self.ctx.can.bus.channels

    def _sync_button(self) -> None:
        if self._is_open():
            self.toggle_btn.setText("Disconnect")
            set_kind(self.toggle_btn, "danger")
        else:
            self.toggle_btn.setText("Connect")
            set_kind(self.toggle_btn, "primary")

    def toggle(self) -> None:
        if self._is_open():
            self._close_channel(self.channel.text().strip())
        else:
            self.connect_node()

    def _row_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) == 1:
            ch = self.table.item(rows.pop(), 0).text()
            n = self._open.get(ch)
            if n is not None:
                self._load_form(n)                       # shows that channel's port and bit rates
            self.channel.setText(ch)

    # ---- multiple adapters ----------------------------------------------------------------
    @staticmethod
    def _is_sim(port: str) -> bool:
        return port.upper().startswith("SIM")

    def port_owner(self, port: str) -> str | None:
        """Channel that already uses this physical port (SIM ports can be opened any number of times)."""
        if self._is_sim(port):
            return None
        return next((ch for ch, n in self._open.items() if n.port.upper() == port.upper()), None)

    def free_ports(self) -> list[str]:
        """Detected USB adapters (CANable) that are not open yet, in scan order. Built-in serial ports
        such as a motherboard COM1 are left out."""
        out = []
        for i in range(self.port.count()):
            p, kind = self.port.itemText(i), self.port.itemData(i, self.KIND_ROLE)
            usb = kind == "slcan" or "USB" in str(self.port.itemData(i, Qt.ToolTipRole) or "")
            if usb and not self._is_sim(p) and self.port_owner(p) is None:
                out.append(p)
        return out

    def next_channel_name(self) -> str:
        used = set(self.ctx.can.bus.channels) | set(self._open)
        n = 1
        while f"CAN{n}" in used:
            n += 1
        return f"CAN{n}"

    def _advance_form(self) -> None:
        """After a connect: when another adapter is still free, propose it with the next channel name so it is one
        click away; otherwise the form keeps showing the channel just opened (button = Disconnect)."""
        free = self.free_ports()
        if free:
            self.port.setCurrentText(free[0])
            self.channel.setText(self.next_channel_name())

    def connect_all(self, interactive: bool = True) -> list[str]:
        """Open every free real adapter with the form's settings; returns the channels opened."""
        opened, failed = [], []
        for port in self.free_ports():
            self.port.setCurrentText(port)
            ch = self.next_channel_name()
            self.channel.setText(ch)
            if self.connect_node(interactive=False):
                opened.append(ch)
            else:
                failed.append(port)
        if not opened and not failed:
            self._report(interactive, "warning", "Connect all", "No free adapter found - plug one in and press Scan.")
        elif failed and interactive:
            QMessageBox.warning(self, "Connect all", f"Could not open {', '.join(failed)}; see the log for details.")
        return opened

    # ---- ports -------------------------------------------------------------------------
    KIND_ROLE = Qt.UserRole + 1

    def scan(self) -> None:
        """Real adapters first (a CANable is picked automatically when present), then the simulated nodes."""
        cur = self.port.currentText()
        self.port.blockSignals(True)
        self.port.clear()
        first_hw = None
        try:
            for p in self.ctx.nodes.scan():
                self.port.addItem(p["port"])
                i = self.port.count() - 1
                self.port.setItemData(i, f"{p['label']}  ({p['hwid']})", Qt.ToolTipRole)
                self.port.setItemData(i, p["kind"], self.KIND_ROLE)
                if p["kind"] == "slcan" and first_hw is None:
                    first_hw = p["port"]
        except Exception as e:
            self.ctx.events.publish("log", f"Port scan failed: {e}")
        for sim in ("SIM1", "SIM2"):                       # built-in simulated nodes (loopback), no hardware
            self.port.addItem(sim)
            self.port.setItemData(self.port.count() - 1, "sim", self.KIND_ROLE)
        self.port.blockSignals(False)
        keep_real = cur and not cur.upper().startswith("SIM") and self.port.findText(cur) >= 0
        if keep_real or (cur and self.port.findText(cur) >= 0 and not first_hw):
            self.port.setCurrentText(cur)                  # keep the user's choice ...
        elif first_hw:                                     # ... but prefer a real adapter over a simulated node
            self.port.setCurrentText(first_hw)
        else:
            self.port.setCurrentText(cur or "SIM1")
        self._device_changed()

    def device_kind(self) -> str:
        """'slcan' (CANable), 'sim' (built-in) or 'autosim' (another serial port: speaks the AutoSim serial
        protocol only if something on it implements docs/ESP32_PROTOCOL.md) for the chosen port."""
        i = self.port.findText(self.port.currentText())
        kind = self.port.itemData(i, self.KIND_ROLE) if i >= 0 else None
        if kind:
            return kind
        return "sim" if self.port.currentText().upper().startswith("SIM") else "autosim"

    def _device_changed(self) -> None:
        """Adapt the form to what the selected hardware can do."""
        kind = self.device_kind()
        owner = self.port_owner(self.port.currentText())
        if owner:                                         # selecting an open adapter shows its channel (Disconnect)
            self.channel.setText(owner)
        elif self.channel.text().strip() in self._open:   # a free adapter gets a fresh channel name (Connect)
            self.channel.setText(self.next_channel_name())
        self.port_hint.setText({"slcan": "CANable 2.0 \u00b7 slcan", "sim": "simulated node",
                                "autosim": "serial port - not a CANable"}.get(kind, ""))
        if kind == "slcan":                               # firmware: ISO CAN FD only, data 2 / 5 Mbit/s
            self.iso.setChecked(True)
            self.iso.setEnabled(False)
            self.iso.setToolTip("The CANable 2.0 firmware supports ISO CAN FD only")
            self.loop.setToolTip("The CANable has no internal loopback: frames are echoed by the PC and nothing "
                                 "is sent on the bus")
        else:
            self.iso.setEnabled(True)
            self.iso.setToolTip("ISO 11898-1:2015 CRC (uncheck for non-ISO / Bosch)")
            self.loop.setToolTip("Internal loopback: the board receives its own frames, no CAN bus needed (bench test)")
        self._fill_data_rates(self.data.currentData())

    def cfg(self) -> CanChannelConfig:
        return CanChannelConfig(self.nominal.currentData(), self.data.currentData(), self.fd.isChecked(),
                                self.brs.isChecked() and self.fd.isChecked(), self.iso.isChecked(),
                                extras={"loopback": self.loop.isChecked()})

    def _report(self, interactive: bool, kind: str, title: str, text: str) -> bool:
        if interactive:
            (QMessageBox.critical if kind == "critical" else QMessageBox.warning)(self, title, text)
        else:
            self.ctx.events.publish("log", f"{title}: {text}")
        return False

    def connect_node(self, interactive: bool = True) -> bool:
        """Open the channel described by the form. interactive=False (project restore) reports problems
        in the log instead of dialogs. Returns True when the channel is open."""
        port, ch = self.port.currentText().strip(), self.channel.text().strip()
        if not port or not ch:
            return self._report(interactive, "warning", "Connect", "Select a port and enter a channel name.")
        if ch in self.ctx.can.bus.channels:
            return self._report(interactive, "warning", "Connect", f"Channel {ch!r} is already open.")
        owner = self.port_owner(port)
        if owner:
            return self._report(interactive, "warning", "Connect", f"{port} is already open as channel {owner}.")
        try:
            backend = self.ctx.nodes.create_backend(port, ch)
            self.ctx.can.open_channel(ch, backend, self.cfg())
        except Exception as e:
            self.ctx.nodes.release(ch)
            self._drop_row(ch)                       # no half-open row / "connecting" badge after a failure
            self.update_badge()
            self._sync_button()
            return self._report(interactive, "critical", "Connect failed", f"{port} ({ch}): {e}")
        self._open[ch] = NodeSetting(port, ch, self.nominal.currentData(), self.data.currentData(),
                                     self.fd.isChecked(), self.brs.isChecked(), self.iso.isChecked(),
                                     self.loop.isChecked(), True)
        self._row(ch)
        self.update_badge()
        self.channelsChanged.emit(list(self.ctx.can.bus.channels))
        self._advance_form()
        self._sync_button()
        return True

    def _row(self, ch: str) -> int:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == ch:
                return r
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c in range(7):
            self.table.setItem(r, c, QTableWidgetItem(""))
        self.table.item(r, 0).setText(ch)
        return r

    def on_node_state(self, st: dict) -> None:
        if st["channel"] not in self.ctx.nodes.nodes:
            return
        r = self._row(st["channel"])
        self.table.item(r, 1).setText((st.get("info") or {}).get("node_id", ""))
        for c, key in ((2, "port"), (3, "state"), (4, "rx"), (5, "tx"), (6, "errors")):
            self.table.item(r, c).setText(str(st[key]))
        color = state_color(st["state"])
        self.table.item(r, 3).setData(Qt.ForegroundRole, QColor(color) if color else None)
        self._mark_duplicates()
        self.update_badge()

    def _mark_duplicates(self) -> None:
        """Two boards reporting the same name cannot be told apart: highlight them and warn once."""
        dups = self.ctx.nodes.duplicate_ids()
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 1)
            clash = it.text() in dups
            # clearing must REMOVE the override: an invalid QColor would paint the text black
            it.setData(Qt.ForegroundRole, QColor(palette()["WARNING"]) if clash else None)
            it.setToolTip("Another node reports the same name - rename one of them" if clash else "")
        for name, chans in dups.items():
            if name not in self._warned_dups:
                self._warned_dups.add(name)
                self.ctx.events.publish("log", f"Warning: channels {', '.join(chans)} report the same node name "
                                               f"{name!r}; rename one (double-click the Node cell).")
        self._warned_dups &= set(dups)

    def update_badge(self) -> None:
        states = [self.table.item(r, 3).text() for r in range(self.table.rowCount())]
        up = sum(1 for s in states if s == "running")
        if any(s == "error" for s in states):
            self.badge.set_state("error", "Error")
        elif up:
            self.badge.set_state("online", f"{up} connected")
        elif states:
            self.badge.set_state("connecting", "Connecting…")
        else:
            self.badge.set_state("offline", "Offline")

    # ---- rename ---------------------------------------------------------------------------------
    def _table_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        ch = self.table.item(item.row(), 0).text()
        menu = QMenu(self)
        rename = menu.addAction("Rename node…")
        disconnect = menu.addAction("Disconnect")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is rename:
            self.rename_node(ch)
        elif chosen is disconnect:
            self._close_channel(ch)

    def rename_node(self, ch: str) -> None:
        node = self.ctx.nodes.nodes.get(ch)
        if node is None:
            return
        name, ok = QInputDialog.getText(self, "Rename node", f"Name stored on the board behind {ch}\n"
                                        "(1-15 characters: A-Z a-z 0-9 _ . -)", text=node.info.get("node_id", ""))
        if ok:
            self.rename_node_to(ch, name.strip())

    def rename_node_to(self, ch: str, name: str) -> bool:
        try:
            self.ctx.nodes.rename(ch, name)
        except (NodeError, KeyError) as e:
            QMessageBox.warning(self, "Rename node", str(e))
            return False
        self.table.item(self._row(ch), 1).setText(name)
        self._mark_duplicates()
        return True

    # ---- closing ---------------------------------------------------------------------------------
    def _drop_row(self, ch: str) -> None:
        self._open.pop(ch, None)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == ch:
                self.table.removeRow(r)
                break

    def _close_channel(self, ch: str) -> None:
        try:
            self.ctx.can.close_channel(ch)
        finally:
            self.ctx.nodes.release(ch)
        self._drop_row(ch)
        self._mark_duplicates()
        self.update_badge()
        self._sync_button()
        self.channelsChanged.emit(list(self.ctx.can.bus.channels))

    def disconnect_all(self) -> int:
        """Close every open channel; returns how many were closed."""
        chans = list(self._open)
        for ch in chans:
            self._close_channel(ch)
        return len(chans)

    def disconnect_node(self) -> None:
        """Close every channel selected in the table."""
        for ch in [self.table.item(r, 0).text() for r in sorted({i.row() for i in self.table.selectedIndexes()})]:
            self._close_channel(ch)

    # ---- project ---------------------------------------------------------------------------------
    def collect(self, cfg: ProjectConfig) -> None:
        """Every open channel is saved (and reconnected on open); with none open, the form is saved."""
        if self._open:
            cfg.nodes = [replace(n) for n in self._open.values()]
        else:
            cfg.nodes = [NodeSetting(self.port.currentText(), self.channel.text(), self.nominal.currentData(),
                                     self.data.currentData(), self.fd.isChecked(), self.brs.isChecked(),
                                     self.iso.isChecked(), self.loop.isChecked())]

    def _load_form(self, n: NodeSetting) -> None:
        self.port.setCurrentText(n.port)
        self.channel.setText(n.channel_name)
        for combo, val in ((self.nominal, n.nominal_bitrate), (self.data, n.data_bitrate)):
            i = combo.findData(val)
            if i >= 0:
                combo.setCurrentIndex(i)
        self.fd.setChecked(n.fd)
        self.brs.setChecked(n.brs)
        self.iso.setChecked(n.iso)
        self.loop.setChecked(n.loopback)

    def apply(self, cfg: ProjectConfig) -> None:
        if not cfg.nodes:
            return
        for n in cfg.nodes:
            if n.autoconnect and n.channel_name not in self.ctx.can.bus.channels:
                self._load_form(n)
                self.connect_node(interactive=False)          # failures (unplugged board ...) go to the log
        self._load_form(cfg.nodes[0])
