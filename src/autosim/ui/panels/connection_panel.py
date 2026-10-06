"""Connection panels: CAN FD nodes (ESP32 + MCP2518FD) and the automotive Ethernet (RAD-MOON) NIC."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from autosim.core.common.config import EthSetting, NodeSetting, ProjectConfig
from autosim.core.common.frame import CanChannelConfig
from autosim.core.someip_engine.engine import NetConfig
from autosim.hardware.esp32_gateway.serial_node import NodeError
from autosim.hardware.eth_interface.nic import list_nics, validate_multicast
from dataclasses import replace

from autosim.ui.theme import palette, state_color
from autosim.ui.widgets import CONTROL_HEIGHT, StatusBadge, caption, set_kind


NOMINAL = [125_000, 250_000, 500_000, 1_000_000]
DATA = [1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000]
LOOPBACK = "Loopback (127.0.0.1) – local test"
SCAN_WIDTH = 80
ACTION_WIDTH = 130
ETH_ACTION_WIDTH = 130


def _rate(v: int) -> str:
    return f"{v / 1_000_000:g} Mbit/s" if v >= 1_000_000 else f"{v // 1000} kbit/s"


class CanConnectionPanel(QWidget):
    """Open/close CAN FD channels backed by ESP32 nodes (or the built-in SIM nodes).

    One primary button toggles the channel named in the form: blue *Connect* while it is closed,
    red *Disconnect* while it is open.
    """

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.port = QComboBox()
        self.port.setEditable(True)
        self.channel = QLineEdit("CAN1")
        self.nominal = QComboBox()
        for v in NOMINAL:
            self.nominal.addItem(_rate(v), v)
        self.nominal.setCurrentIndex(2)
        self.data = QComboBox()
        self.data.setToolTip("Data-phase bit rate. Only rates the MCP2518FD can reach are offered: an integer\n"
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
        self.badge = StatusBadge("Offline", "offline")
        # strict sizing: 32px inputs, 80px Scan, 130px Connect/Disconnect (never stretched)
        for w in (self.port, self.channel, self.nominal, self.data):
            w.setFixedHeight(CONTROL_HEIGHT)
        self.scan_btn.setFixedSize(SCAN_WIDTH, CONTROL_HEIGHT)
        self.toggle_btn.setFixedSize(ACTION_WIDTH, CONTROL_HEIGHT)

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
        grid.addWidget(caption("Port"), 0, 0, 1, 2)
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
        action_layout.addStretch()                       # pushes everything else to the right

        v.addLayout(hdr)
        v.addLayout(grid)
        v.addLayout(action_layout)
        v.addWidget(self.table, 1)

        self.scan_btn.clicked.connect(self.scan)
        self.toggle_btn.clicked.connect(self.toggle)
        self.channel.textChanged.connect(self._sync_button)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.cellDoubleClicked.connect(lambda r, c: c == 1 and self.rename_node(self.table.item(r, 0).text()))
        self.fd.toggled.connect(lambda on: (self.data.setEnabled(on), self.brs.setEnabled(on)))
        self.nominal.currentIndexChanged.connect(lambda _i: self._fill_data_rates(self.data.currentData()))
        bridge.nodeState.connect(self.on_node_state)
        self._open: dict[str, NodeSetting] = {}           # settings of every open channel (for the project file)
        self._warned_dups: set[str] = set()
        self.scan()
        self._sync_button()

    def _fill_data_rates(self, prefer: int | None) -> None:
        """List only data rates valid for the current nominal rate (integer multiple, ratio 1..10), keeping the
        previous choice when it is still valid, else the closest one."""
        nominal = self.nominal.currentData() or 500_000
        valid = [v for v in DATA if v >= nominal and v % nominal == 0 and v // nominal <= 10]
        if nominal not in valid:
            valid.insert(0, nominal)                      # 1x: data phase at the nominal rate
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
            self.channel.setText(self.table.item(rows.pop(), 0).text())

    # ---- ports -------------------------------------------------------------------------
    def scan(self) -> None:
        cur = self.port.currentText()
        self.port.clear()
        self.port.addItem("SIM1")          # built-in simulated node (loopback), no hardware needed
        self.port.addItem("SIM2")
        try:
            for p in self.ctx.nodes.scan():
                self.port.addItem(p["port"])
                self.port.setItemData(self.port.count() - 1, p["description"], 3)  # tooltip
        except Exception as e:
            self.ctx.events.publish("log", f"Port scan failed: {e}")
        if cur:
            self.port.setCurrentText(cur)

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


class EthConnectionPanel(QFrame):
    """Sidebar card (SOME/IP tab): adapter, addressing, status and Start/Stop in a compact form.
    Inputs are capped at 200 px wide; all controls in this card are 30 px high (density="compact")."""

    someipStateChanged = Signal(bool)
    FIELD_WIDTH = 200
    COMPACT_HEIGHT = 30

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setProperty("density", "compact")
        self.ctx = ctx
        self._nics = []
        self.nic = QComboBox()
        self.nic.setToolTip("Network adapter the SOME/IP sockets bind to (e.g. the RAD-MOON's adapter)")
        self.ip = QComboBox()
        self.ip.setEditable(True)
        self.mcast = QLineEdit("224.0.0.1")
        self.sd_port = QSpinBox()
        self.sd_port.setRange(1, 65535)
        self.sd_port.setValue(30490)
        for w in (self.nic, self.ip, self.mcast, self.sd_port):
            w.setMaximumWidth(self.FIELD_WIDTH)
            w.setFixedHeight(self.COMPACT_HEIGHT)
        self.eth_btn = QPushButton("Start SOME/IP")
        set_kind(self.eth_btn, "primary")
        self.eth_btn.setFixedSize(ETH_ACTION_WIDTH, self.COMPACT_HEIGHT)
        self.refresh_btn = QPushButton("Refresh")
        self.mc_btn = QPushButton("Check")
        self.mc_btn.setToolTip("Check that the multicast group can be joined on this adapter")
        self.refresh_btn.setToolTip("Re-read the network adapters")
        for b in (self.refresh_btn, self.mc_btn):
            b.setFixedHeight(self.COMPACT_HEIGHT)
        self.badge = StatusBadge("Stopped", "offline")

        title = QLabel("Automotive Ethernet")
        title.setProperty("role", "title")
        sub = QLabel("RAD-MOON / SOME/IP-SD")
        sub.setProperty("role", "muted")
        hdr = QHBoxLayout()
        tl = QVBoxLayout()
        tl.setSpacing(0)
        tl.addWidget(title)
        tl.addWidget(sub)
        hdr.addLayout(tl)
        hdr.addStretch()
        hdr.addWidget(self.badge, 0, Qt.AlignTop)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addWidget(self.refresh_btn)
        tools.addWidget(self.mc_btn)
        tools.addStretch()
        form.addRow(caption("Adapter"), self.nic)
        form.addRow("", tools)
        form.addRow(caption("Unicast IP"), self.ip)
        form.addRow(caption("SD multicast"), self.mcast)
        form.addRow(caption("SD port"), self.sd_port)

        action = QHBoxLayout()
        action.addWidget(self.eth_btn)
        action.addStretch()

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)
        v.addLayout(hdr)
        v.addLayout(form)
        v.addLayout(action)

        self.nic.currentIndexChanged.connect(self._nic_changed)
        self.eth_btn.clicked.connect(self.toggle_someip)
        self.mc_btn.clicked.connect(self.check_multicast)
        self.refresh_btn.clicked.connect(self.refresh_nics)
        self.refresh_nics()

    def refresh_nics(self) -> None:
        self.nic.blockSignals(True)
        self.nic.clear()
        self.nic.addItem(LOOPBACK, None)
        try:
            self._nics = list_nics()
        except Exception:
            self._nics = []
        for n in self._nics:
            if n.ipv4:
                self.nic.addItem(n.label, n)
        self.nic.blockSignals(False)
        if self.nic.count() > 1 and self.nic.itemData(1).likely_intrepid:
            self.nic.setCurrentIndex(1)
        self._nic_changed()

    def _nic_changed(self) -> None:
        n = self.nic.currentData()
        self.ip.clear()
        self.ip.addItems(n.ipv4 if n else ["127.0.0.1"])

    def net_config(self) -> NetConfig:
        return NetConfig(self.ip.currentText().strip(), self.mcast.text().strip(), self.sd_port.value())

    def toggle_someip(self) -> None:
        if self.ctx.someip.running:
            self.ctx.someip.stop()
            self.eth_btn.setText("Start SOME/IP")
            set_kind(self.eth_btn, "primary")
            self.badge.set_state("offline", "Stopped")
            self.badge.setToolTip("")
            self.someipStateChanged.emit(False)
            return
        cfg = self.net_config()
        try:
            self.ctx.someip.start(cfg)
        except Exception as e:
            QMessageBox.critical(self, "SOME/IP", f"Cannot start on {cfg.local_ip}:{cfg.sd_port}\n{e}")
            return
        mc = self.ctx.someip.transport.multicast_ok
        self.eth_btn.setText("Stop SOME/IP")
        set_kind(self.eth_btn, "danger")
        self.badge.set_state("online", "Running" if mc else "Running · local")
        self.badge.setToolTip(f"Bound to {cfg.local_ip}" + ("" if mc else " – multicast unavailable, local/unicast mode"))
        self.someipStateChanged.emit(True)

    def check_multicast(self) -> None:
        ok, msg = validate_multicast(self.ip.currentText().strip(), self.mcast.text().strip())
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Multicast", msg)

    def collect(self, cfg: ProjectConfig) -> None:
        cfg.eth = EthSetting(self.nic.currentText(), self.ip.currentText(), self.mcast.text(), self.sd_port.value())

    def apply(self, cfg: ProjectConfig) -> None:
        i = self.nic.findText(cfg.eth.nic_name)
        if i >= 0:
            self.nic.setCurrentIndex(i)
        self.ip.setCurrentText(cfg.eth.unicast_ip)
        self.mcast.setText(cfg.eth.sd_multicast_ip)
        self.sd_port.setValue(cfg.eth.sd_port)

