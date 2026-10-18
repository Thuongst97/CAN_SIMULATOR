"""SOME/IP panel: services, method caller, event publisher/subscriber, provider responses, SD log."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QHeaderView, QMenu,
                               QVBoxLayout, QWidget)

from autosim.core.someip_engine.config_loader import (ConfigError, EventDef, MethodDef, ServiceDef, load_services)
from autosim.core.someip_engine.payload import PayloadError, make_codec
from autosim.core.someip_engine.proto_loader import ProtoError
from autosim.ui.panels.payload_editor import PayloadEditor
from autosim.ui.widgets import EmptyState, StatusBadge, centered, scrolling, set_kind


def _hex(v: int) -> str:
    return f"0x{v:04X}"


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _add_row(t: QTableWidget, cells: list[str], limit: int = 5000) -> None:
    r = t.rowCount()
    t.insertRow(r)
    for c, text in enumerate(cells):
        t.setItem(r, c, QTableWidgetItem(text))
    if r >= limit:
        t.removeRow(0)
    t.scrollToBottom()


class ServiceDialog(QDialog):
    """Create/edit a ServiceDef. Methods: 'Name;0x0001;request_spec;response_spec' per line.
    Events: 'Name;0x8001;0x0010;payload_spec' per line (id; eventgroup id)."""

    def __init__(self, svc: ServiceDef | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Service")
        s = svc or ServiceDef("MyService", 0x1234, 1, port=30501)
        self.name = QLineEdit(s.name)
        self.sid, self.iid = QLineEdit(_hex(s.service_id)), QLineEdit(_hex(s.instance_id))
        self.major = QSpinBox()
        self.major.setRange(0, 255)
        self.major.setValue(s.major)
        self.minor = QSpinBox()
        self.minor.setRange(0, 2**31 - 1)
        self.minor.setValue(s.minor)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(s.port)
        self.methods = QPlainTextEdit("\n".join(f"{m.name};{_hex(m.method_id)};{m.request_type};{m.response_type}"
                                                for m in s.methods))
        self.events = QPlainTextEdit("\n".join(f"{e.name};{_hex(e.event_id)};{_hex(e.eventgroup_id)};{e.payload_type}"
                                               for e in s.events))
        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Service ID", self.sid)
        form.addRow("Instance ID", self.iid)
        form.addRow("Major version", self.major)
        form.addRow("Minor version", self.minor)
        form.addRow("UDP port (offered)", self.port)
        form.addRow("Methods\n(name;id;req;resp)", self.methods)
        form.addRow("Events\n(name;id;group;spec)", self.events)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.result_def: ServiceDef | None = None

    def _accept(self) -> None:
        try:
            svc = ServiceDef(self.name.text().strip() or "Service", int(self.sid.text(), 0), int(self.iid.text(), 0),
                             self.major.value(), self.minor.value(), port=self.port.value())
            for line in self.methods.toPlainText().splitlines():
                if line.strip():
                    p = [x.strip() for x in line.split(";")] + ["", "", ""]
                    svc.methods.append(MethodDef(p[0], int(p[1], 0), p[2], p[3]))
            for line in self.events.toPlainText().splitlines():
                if line.strip():
                    p = [x.strip() for x in line.split(";")] + ["", "", "", ""]
                    svc.events.append(EventDef(p[0], int(p[1], 0), int(p[2], 0), p[3]))
        except (ValueError, IndexError) as e:
            QMessageBox.warning(self, "Invalid service", f"Cannot parse: {e}")
            return
        self.result_def = svc
        self.accept()


class SomeIpPanel(QWidget):
    availabilityChanged = Signal()
    protosChanged = Signal()
    responseReady = Signal(object)

    SIDEBAR_MIN, SIDEBAR_MAX = 280, 340

    def __init__(self, ctx, bridge, eth_panel=None, parent=None) -> None:
        """Master-detail layout: left sidebar (Ethernet card + Services explorer tree), right inspector
        (selected service header, Offer/Consume, tabs: Method Caller, Event Publisher, Provider Responses,
        SD Log Trace)."""
        super().__init__(parent)
        self.ctx = ctx
        self.providers: dict[int, object] = {}
        self.consumers: dict[int, object] = {}
        self._svc_combos: list[QComboBox] = []
        self._current: ServiceDef | None = None
        self.tabs = QTabWidget()
        self.banner = QLabel("Engine stopped - press \u201cStart SOME/IP\u201d to offer or consume services.")
        self.banner.setProperty("role", "muted")
        self.banner.setWordWrap(True)

        # build order: tabs first (they create the per-tab widgets the explorer selection drives)
        self._build_method_tab()
        self._build_event_tab()
        self._build_server_tab()
        self._build_sd_tab()

        # ---- left sidebar ----
        self.sidebar = QWidget()
        self.sidebar.setMinimumWidth(self.SIDEBAR_MIN)
        self.sidebar.setMaximumWidth(self.SIDEBAR_MAX)
        sv = QVBoxLayout(self.sidebar)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(10)
        if eth_panel is not None:
            sv.addWidget(eth_panel)
        sv.addWidget(self._build_explorer(), 1)
        self.sidebar_scroll = scrolling(self.sidebar, min_height=240)   # never forces the window taller
        self.sidebar_scroll.setMaximumWidth(self.SIDEBAR_MAX + 12)

        # ---- right inspector ----
        self.inspector = QFrame()
        self.inspector.setObjectName("card")
        iv = QVBoxLayout(self.inspector)
        iv.setContentsMargins(14, 12, 14, 12)
        iv.setSpacing(8)
        iv.addWidget(self._build_inspector_header())
        iv.addWidget(self.banner)
        self.empty = EmptyState("network", "No Services Configured",
                                "Load a vsomeip JSON / ARXML configuration or a .proto file, "
                                "or add a service manually to start offering or consuming.")
        self.empty.add_button("Load Config\u2026", "primary").clicked.connect(self.load_config)
        self.empty.add_button("Add Service\u2026", "").clicked.connect(lambda: self.edit_service(None))
        self.svc_stack = QStackedWidget()
        self.svc_stack.addWidget(centered(self.empty))     # page 0: nothing configured
        self.svc_stack.addWidget(self.tabs)                # page 1: inspector tabs
        iv.addWidget(self.svc_stack, 1)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.sidebar_scroll)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([self.SIDEBAR_MAX, 1000])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.splitter)

        self.availabilityChanged.connect(self.refresh_table)
        self.responseReady.connect(self._show_response)
        self._cyclic_payload: dict[int, bytes] = {}
        bridge.sd.connect(self._on_sd)
        bridge.someipEvent.connect(self._on_event)
        bridge.someipMethod.connect(self._on_method)

    # ======================================================================== helpers
    def _svc_combo(self) -> QComboBox:
        """Per-tab service selector. Not shown: it follows the service selected in the explorer tree."""
        c = QComboBox(self)
        c.hide()
        self._svc_combos.append(c)
        return c

    def _selected_svc(self, combo: QComboBox) -> ServiceDef | None:
        i = combo.currentIndex()
        return self.ctx.services[i] if 0 <= i < len(self.ctx.services) else None

    def _need_engine(self) -> bool:
        if not self.ctx.someip.running:
            QMessageBox.information(self, "SOME/IP", "Start SOME/IP in the Connection panel first.")
            return False
        return True

    def _provider(self, svc: ServiceDef, start: bool = True):
        p = self.providers.get(id(svc))
        if p is None:
            p = self.providers[id(svc)] = self.ctx.someip.add_provider(svc)
        if start and not p.offering:
            p.start()
        return p

    def _consumer(self, svc: ServiceDef, start: bool = True):
        c = self.consumers.get(id(svc))
        if c is None:
            c = self.consumers[id(svc)] = self.ctx.someip.add_consumer(svc)
            c.on_availability = lambda up: self.availabilityChanged.emit()
        if start and not c.running:
            c.start()
        return c

    def set_engine_running(self, up: bool) -> None:
        self.banner.setVisible(not up)
        if not up:
            self.providers.clear()
            self.consumers.clear()
        self.refresh_services()

    def _err(self, e: Exception) -> None:
        QMessageBox.warning(self, "SOME/IP", str(e))

    # ======================================================================== services explorer (sidebar)
    def _build_explorer(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setProperty("density", "compact")
        title = QLabel("Services")
        title.setProperty("role", "title")
        add, load, proto = QPushButton("+ Add"), QPushButton("Load Config"), QPushButton("Load .proto")
        load.setToolTip("vsomeip JSON or AUTOSAR ARXML")
        tools = QHBoxLayout()
        tools.setSpacing(6)
        for b in (add, load, proto):
            tools.addWidget(b)

        self.svc_tree = QTreeWidget()
        self.svc_tree.setHeaderLabels(["Name", "ID", "State"])
        self.svc_tree.setRootIsDecorated(True)
        self.svc_tree.setUniformRowHeights(True)
        self.svc_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        hh = self.svc_tree.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree_hint = QLabel("No services yet: add one or load a configuration.")
        self.tree_hint.setProperty("role", "muted")
        self.tree_hint.setWordWrap(True)
        self.protos_label = QLabel("")
        self.protos_label.setProperty("role", "muted")
        self.protos_label.setWordWrap(True)

        v = QVBoxLayout(card)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        v.addWidget(title)
        v.addLayout(tools)
        v.addWidget(self.svc_tree, 1)
        v.addWidget(self.tree_hint)
        v.addWidget(self.protos_label)

        add.clicked.connect(lambda: self.edit_service(None))
        load.clicked.connect(self.load_config)
        proto.clicked.connect(self.load_proto)
        self.svc_tree.currentItemChanged.connect(lambda cur, _prev: self._tree_selected(cur))
        self.svc_tree.customContextMenuRequested.connect(self._tree_menu)
        return card

    def _build_inspector_header(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.svc_title = QLabel("No service selected")
        self.svc_title.setProperty("role", "title")
        self.svc_sub = QLabel("")
        self.svc_sub.setProperty("role", "muted")
        titles.addWidget(self.svc_title)
        titles.addWidget(self.svc_sub)
        self.prov_badge = StatusBadge("Not offered", "offline")
        self.cons_badge = StatusBadge("Not consumed", "offline")
        self.prov_badge.setToolTip("Provider (server role)")
        self.cons_badge.setToolTip("Consumer (client role)")
        self.offer_btn = QPushButton("Offer Service")
        self.cons_btn = QPushButton("Consume Service")
        self.edit_btn, self.rem_btn = QPushButton("Edit\u2026"), QPushButton("Remove")
        set_kind(self.offer_btn, "primary")
        for b in (self.offer_btn, self.cons_btn):
            b.setFixedWidth(150)
        badges = QHBoxLayout()
        badges.setSpacing(8)
        badges.addWidget(self.prov_badge)
        badges.addWidget(self.cons_badge)
        badges.addStretch()
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addLayout(titles)
        left.addLayout(badges)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for x in (self.edit_btn, self.rem_btn, self.offer_btn, self.cons_btn):
            actions.addWidget(x)
        h.addLayout(left, 1)
        h.addLayout(actions)
        self.offer_btn.clicked.connect(self.toggle_offer)
        self.cons_btn.clicked.connect(self.toggle_consume)
        self.edit_btn.clicked.connect(lambda: self.edit_service(self._row_svc()))
        self.rem_btn.clicked.connect(self.remove_service)
        self.svc_table_utils = (self.edit_btn, self.rem_btn)
        return w

    # ---- selection --------------------------------------------------------------------------------
    def select_service(self, index: int) -> None:
        """Select the index-th service in the explorer (and therefore in every inspector tab)."""
        if 0 <= index < self.svc_tree.topLevelItemCount():
            self.svc_tree.setCurrentItem(self.svc_tree.topLevelItem(index))

    def _tree_selected(self, item) -> None:
        if item is None:
            return
        kind, svc_idx, detail = item.data(0, Qt.UserRole) or (None, -1, None)
        if not (0 <= svc_idx < len(self.ctx.services)):
            return
        self._current = self.ctx.services[svc_idx]
        for combo in self._svc_combos:                     # every tab follows the explorer
            if combo.currentIndex() != svc_idx:
                combo.setCurrentIndex(svc_idx)
        if kind == "method":
            self.m_method.setCurrentIndex(detail)
            self.tabs.setCurrentIndex(0)
        elif kind == "event":
            self.e_event.setCurrentIndex(detail)
            self.tabs.setCurrentIndex(1)
        self._update_header()

    def _tree_menu(self, pos) -> None:
        item = self.svc_tree.itemAt(pos)
        if item is None:
            return
        self._tree_selected(item)
        menu = QMenu(self)
        edit, rem = menu.addAction("Edit service\u2026"), menu.addAction("Remove service")
        chosen = menu.exec(self.svc_tree.viewport().mapToGlobal(pos))
        if chosen is edit:
            self.edit_service(self._row_svc())
        elif chosen is rem:
            self.remove_service()

    def _rebuild_tree(self) -> None:
        keep = self.ctx.services.index(self._current) if self._current in self.ctx.services else 0
        self.svc_tree.blockSignals(True)
        self.svc_tree.clear()
        for i, svc in enumerate(self.ctx.services):
            top = QTreeWidgetItem([svc.name, _hex(svc.service_id), ""])
            top.setData(0, Qt.UserRole, ("svc", i, None))
            top.setToolTip(0, f"{svc.name}: service {_hex(svc.service_id)}, instance {_hex(svc.instance_id)}, "
                              f"UDP port {svc.port}")
            if svc.methods:
                grp = QTreeWidgetItem(["Methods", str(len(svc.methods)), ""])
                grp.setData(0, Qt.UserRole, ("svc", i, None))
                for j, m in enumerate(svc.methods):
                    it = QTreeWidgetItem([m.name, _hex(m.method_id), ""])
                    it.setData(0, Qt.UserRole, ("method", i, j))
                    grp.addChild(it)
                top.addChild(grp)
            if svc.events:
                grp = QTreeWidgetItem(["Events", str(len(svc.events)), ""])
                grp.setData(0, Qt.UserRole, ("svc", i, None))
                for j, e in enumerate(svc.events):
                    it = QTreeWidgetItem([e.name, _hex(e.event_id), f"eg {_hex(e.eventgroup_id)}"])
                    it.setData(0, Qt.UserRole, ("event", i, j))
                    grp.addChild(it)
                top.addChild(grp)
            self.svc_tree.addTopLevelItem(top)
            top.setExpanded(True)
        self.svc_tree.blockSignals(False)
        if self.ctx.services:
            self.select_service(min(keep, len(self.ctx.services) - 1))
            self._tree_selected(self.svc_tree.currentItem())
        else:
            self._current = None

    def _update_header(self) -> None:
        svc = self._current
        if svc is None:
            self.svc_title.setText("No service selected")
            self.svc_sub.setText("")
        else:
            self.svc_title.setText(svc.name)
            self.svc_sub.setText(f"service {_hex(svc.service_id)}  \u00b7  instance {_hex(svc.instance_id)}  \u00b7  "
                                 f"v{svc.major}.{svc.minor}  \u00b7  UDP {svc.port}"
                                 + ("  \u00b7  TCP" if svc.reliable else ""))
        p = self.providers.get(id(svc)) if svc else None
        c = self.consumers.get(id(svc)) if svc else None
        if p and p.offering:
            self.prov_badge.set_state("online", f"Offering \u00b7 {len(p.subscribers)} sub")
        else:
            self.prov_badge.set_state("offline", "Not offered")
        if c and c.running:
            self.cons_badge.set_state(*(("online", "Available") if c.available else ("connecting", "Searching\u2026")))
        else:
            self.cons_badge.set_state("offline", "Not consumed")
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        """Offer/Consume labels follow the selected service's state; disabled when nothing is selected."""
        svc = self._row_svc()
        p = self.providers.get(id(svc)) if svc else None
        c = self.consumers.get(id(svc)) if svc else None
        offering, consuming = bool(p and p.offering), bool(c and c.running)
        self.offer_btn.setText("Stop Offering" if offering else "Offer Service")
        self.cons_btn.setText("Stop Consuming" if consuming else "Consume Service")
        set_kind(self.offer_btn, "danger" if offering else "primary")
        set_kind(self.cons_btn, "danger" if consuming else "")
        for b in (self.offer_btn, self.cons_btn, *self.svc_table_utils):
            b.setEnabled(svc is not None)

    def _row_svc(self) -> ServiceDef | None:
        """The service selected in the explorer tree."""
        return self._current if self._current in self.ctx.services else None

    def load_config(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Load service configuration", "",
                                                "Service config (*.json *.arxml);;All files (*)")
        for p in paths:
            self.load_config_file(p)

    def load_config_file(self, path: str) -> bool:
        try:
            svcs = load_services(path)
        except ConfigError as e:
            self._err(e)
            return False
        self.ctx.services.extend(svcs)
        if path not in self.ctx.config.someip_config_paths:
            self.ctx.config.someip_config_paths.append(path)
        self.refresh_services()
        return True

    def load_proto(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Load protobuf definitions", "", "Protobuf (*.proto)")
        for p in paths:
            self.load_proto_file(p)

    def load_proto_file(self, path: str) -> bool:
        try:
            self.ctx.protos.load(path)
        except (ProtoError, Exception) as e:
            self._err(ProtoError(f"{path}: {e}"))
            return False
        if path not in self.ctx.config.proto_paths:
            self.ctx.config.proto_paths.append(path)
        self.protosChanged.emit()
        self.refresh_services()
        return True

    def edit_service(self, svc: ServiceDef | None) -> None:
        dlg = ServiceDialog(svc, self)
        if dlg.exec() and dlg.result_def:
            if svc is None:
                self.ctx.services.append(dlg.result_def)
            else:
                i = self.ctx.services.index(svc)
                self.providers.pop(id(svc), None)
                self.consumers.pop(id(svc), None)
                self.ctx.services[i] = dlg.result_def
            self._current = dlg.result_def
            self.refresh_services()

    def remove_service(self) -> None:
        svc = self._row_svc()
        if svc is None:
            return
        for d in (self.providers, self.consumers):
            obj = d.pop(id(svc), None)
            if obj is not None and self.ctx.someip.running:
                self.ctx.someip.remove(obj)
        self.ctx.services.remove(svc)
        self._current = None
        self.refresh_services()

    def toggle_offer(self) -> None:
        svc = self._row_svc()
        if svc is None or not self._need_engine():
            return
        p = self._provider(svc, start=False)
        try:
            p.stop() if p.offering else p.start()
        except Exception as e:
            self._err(e)
        self.refresh_table()

    def toggle_consume(self) -> None:
        svc = self._row_svc()
        if svc is None or not self._need_engine():
            return
        c = self._consumer(svc, start=False)
        try:
            c.stop() if c.running else c.start()
        except Exception as e:
            self._err(e)
        self.refresh_table()

    def refresh_table(self) -> None:
        """Provider/consumer state changed: update the explorer's State column and the inspector header."""
        for i, svc in enumerate(self.ctx.services):
            item = self.svc_tree.topLevelItem(i)
            if item is None:
                continue
            p, c = self.providers.get(id(svc)), self.consumers.get(id(svc))
            states = []
            if p and p.offering:
                states.append("offered")
            if c and c.running:
                states.append("available" if c.available else "searching")
            item.setText(2, ", ".join(states))
        has = bool(self.ctx.services)
        self.svc_stack.setCurrentIndex(1 if has else 0)
        self.tree_hint.setVisible(not has)
        self._update_header()

    def refresh_services(self) -> None:
        """Service list changed: refill the per-tab selectors, rebuild the explorer, refresh state."""
        for combo in self._svc_combos:
            keep = combo.currentIndex()
            combo.clear()
            combo.addItems([f"{s.name} ({_hex(s.service_id)})" for s in self.ctx.services])
            if combo.count():
                combo.setCurrentIndex(min(max(keep, 0), combo.count() - 1))
        self._rebuild_tree()
        self.refresh_table()
        self.protos_label.setText("Protobuf messages: " + (", ".join(self.ctx.protos.messages()) or "none loaded"))

    # ======================================================================== method caller
    def _build_method_tab(self) -> None:
        w = QWidget()
        self.tabs.addTab(scrolling(w), "Method Caller")
        self.m_svc = self._svc_combo()
        self.m_method = QComboBox()
        self.m_method.setEditable(True)
        self.m_timeout = QSpinBox()
        self.m_timeout.setRange(100, 60000)
        self.m_timeout.setValue(2000)
        self.m_timeout.setSuffix(" ms")
        self.m_ff = QCheckBox("Fire && forget")       # "&&" = a literal ampersand (single & is a mnemonic)
        self.m_req = PayloadEditor(self.ctx.protos)
        self.m_resp = PayloadEditor(self.ctx.protos)
        self.m_status = QLabel("")
        send = QPushButton("Send request")
        form = QFormLayout()
        form.addRow("Method", self.m_method)
        r = QHBoxLayout()
        r.addWidget(self.m_timeout)
        r.addWidget(self.m_ff)
        r.addStretch()
        form.addRow("Timeout", r)
        req_box, resp_box = QGroupBox("Request"), QGroupBox("Response")
        QVBoxLayout(req_box).addWidget(self.m_req)
        v = QVBoxLayout(resp_box)
        v.addWidget(self.m_status)
        v.addWidget(self.m_resp)
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(req_box)
        lay.addWidget(send)
        lay.addWidget(resp_box)

        self.m_svc.currentIndexChanged.connect(self._fill_methods)
        self.m_method.currentIndexChanged.connect(self._method_picked)
        send.clicked.connect(self.send_request)
        self.protosChanged.connect(lambda: [e.refresh_messages() for e in (self.m_req, self.m_resp, self.e_payload,
                                                                           self.s_payload)])

    def _fill_methods(self) -> None:
        svc = self._selected_svc(self.m_svc)
        self.m_method.clear()
        for m in (svc.methods if svc else []):
            self.m_method.addItem(f"{m.name} ({_hex(m.method_id)})", m)
        self._method_picked()

    def _method_picked(self) -> None:
        m = self.m_method.currentData()
        if isinstance(m, MethodDef) and self.m_method.currentText() == self.m_method.itemText(self.m_method.currentIndex()):
            self.m_req.set_spec(m.request_type)
            self.m_resp.set_spec(m.response_type)
            self.m_ff.setChecked(m.fire_and_forget)

    def _method_id(self) -> int:
        m, text = self.m_method.currentData(), self.m_method.currentText()
        if isinstance(m, MethodDef) and text == self.m_method.itemText(self.m_method.currentIndex()):
            return m.method_id
        return int(text.split("(")[-1].strip(") ") if "(" in text else text, 0)

    def send_request(self) -> None:
        svc = self._selected_svc(self.m_svc)
        if svc is None or not self._need_engine():
            return
        try:
            mid, payload = self._method_id(), self.m_req.encode()
        except (PayloadError, ValueError, ProtoError) as e:
            self._err(e)
            return
        cons = self._consumer(svc)
        self.m_status.setText("waiting…")
        self.m_status.setObjectName("")
        cons.call_method_async(mid, payload, self.m_timeout.value() / 1000, self._response_cb, self.m_ff.isChecked())

    def _response_cb(self, res) -> None:   # runs on the engine thread; the signal hops to the GUI thread
        self.responseReady.emit(res)

    def _show_response(self, res) -> None:
        if res.ok:
            text = self.m_resp.show_bytes(res.payload)
            self.m_status.setText(f"OK – {res.latency_ms:.1f} ms – {len(res.payload)} byte(s): {text}")
        else:
            self.m_status.setText(f"FAILED – {res.error}")

    # ======================================================================== events tab
    def _build_event_tab(self) -> None:
        w = QWidget()
        self.tabs.addTab(scrolling(w), "Event Publisher")
        # publisher
        self.e_svc = self._svc_combo()
        self.e_event = QComboBox()
        self.e_event.setEditable(True)
        self.e_payload = PayloadEditor(self.ctx.protos)
        self.e_cycle = QSpinBox()
        self.e_cycle.setRange(10, 60000)
        self.e_cycle.setValue(1000)
        self.e_cycle.setSuffix(" ms")
        pub, cyc_on, cyc_off = QPushButton("Publish once"), QPushButton("Start cyclic"), QPushButton("Stop cyclic")
        pf = QFormLayout()
        pf.addRow("Event", self.e_event)
        pb = QHBoxLayout()
        for b in (pub, self.e_cycle, cyc_on, cyc_off):
            pb.addWidget(b)
        pb.addStretch()
        pub_box = QGroupBox("Publisher (server role)")
        pv = QVBoxLayout(pub_box)
        pv.addLayout(pf)
        pv.addWidget(self.e_payload)
        pv.addLayout(pb)
        # subscriber
        self.sub_svc = self._svc_combo()
        self.sub_group = QComboBox()
        self.sub_group.setEditable(True)
        sub, unsub = QPushButton("Subscribe"), QPushButton("Unsubscribe")
        self.ev_table = _table(["Time", "Service", "Event", "Payload (hex)", "Decoded"])
        sf = QHBoxLayout()
        for x in (QLabel("Eventgroup"), self.sub_group, sub, unsub):
            sf.addWidget(x)
        sf.addStretch()
        sub_box = QGroupBox("Subscriber (client role)")
        sv = QVBoxLayout(sub_box)
        sv.addLayout(sf)
        sv.addWidget(self.ev_table)
        lay = QVBoxLayout(w)
        lay.addWidget(pub_box)
        lay.addWidget(sub_box, 1)

        self.e_svc.currentIndexChanged.connect(self._fill_events)
        self.e_event.currentIndexChanged.connect(self._event_picked)
        self.sub_svc.currentIndexChanged.connect(self._fill_groups)
        pub.clicked.connect(self.publish_once)
        cyc_on.clicked.connect(self.start_cyclic)
        cyc_off.clicked.connect(self.stop_cyclic)
        sub.clicked.connect(lambda: self._subscribe(True))
        unsub.clicked.connect(lambda: self._subscribe(False))

    def _fill_events(self) -> None:
        svc = self._selected_svc(self.e_svc)
        self.e_event.clear()
        for e in (svc.events if svc else []):
            self.e_event.addItem(f"{e.name} ({_hex(e.event_id)})", e)
        self._event_picked()

    def _event_picked(self) -> None:
        e = self.e_event.currentData()
        if isinstance(e, EventDef):
            self.e_payload.set_spec(e.payload_type)

    def _fill_groups(self) -> None:
        svc = self._selected_svc(self.sub_svc)
        self.sub_group.clear()
        for g in (svc.eventgroups if svc else []):
            self.sub_group.addItem(_hex(g), g)

    def _event_id(self) -> int:
        e, text = self.e_event.currentData(), self.e_event.currentText()
        if isinstance(e, EventDef) and text == self.e_event.itemText(self.e_event.currentIndex()):
            return e.event_id
        return int(text.split("(")[-1].strip(") ") if "(" in text else text, 0)

    def publish_once(self) -> None:
        svc = self._selected_svc(self.e_svc)
        if svc is None or not self._need_engine():
            return
        try:
            eid, payload = self._event_id(), self.e_payload.encode()
        except (PayloadError, ValueError, ProtoError) as e:
            self._err(e)
            return
        self._provider(svc).publish_event(eid, payload)
        self.refresh_table()

    def start_cyclic(self) -> None:
        svc = self._selected_svc(self.e_svc)
        if svc is None or not self._need_engine():
            return
        try:
            eid, _ = self._event_id(), self.e_payload.encode()
        except (PayloadError, ValueError, ProtoError) as e:
            self._err(e)
            return

        self._cyclic_payload[eid] = self.e_payload.encode()    # snapshot; press Start again to change it
        self._provider(svc).start_cyclic(eid, self.e_cycle.value(), lambda: self._cyclic_payload.get(eid, b""))

    def stop_cyclic(self) -> None:
        svc = self._selected_svc(self.e_svc)
        if svc is not None and id(svc) in self.providers:
            try:
                self.providers[id(svc)].stop_cyclic(self._event_id())
            except ValueError as e:
                self._err(e)

    def _subscribe(self, on: bool) -> None:
        svc = self._selected_svc(self.sub_svc)
        if svc is None or not self._need_engine():
            return
        try:
            text = self.sub_group.currentText()
            g = self.sub_group.currentData() if text == self.sub_group.itemText(self.sub_group.currentIndex()) \
                else int(text, 0)
        except (TypeError, ValueError) as e:
            self._err(e)
            return
        c = self._consumer(svc)
        c.subscribe(g) if on else c.unsubscribe(g)

    def _on_event(self, ev: dict) -> None:
        svc = next((s for s in self.ctx.services if s.name == ev["service"]), None)
        spec = next((e.payload_type for e in (svc.events if svc else []) if e.event_id == ev["event_id"]), "")
        try:
            decoded = str(make_codec(spec, self.ctx.protos).decode(ev["payload"])) if spec else ""
        except Exception as e:
            decoded = f"<{e}>"
        _add_row(self.ev_table, [time.strftime("%H:%M:%S", time.localtime(ev["t"])), ev["service"],
                                 _hex(ev["event_id"]), ev["payload"].hex(" ").upper(), decoded])

    # ======================================================================== server tab
    def _build_server_tab(self) -> None:
        w = QWidget()
        self.tabs.addTab(scrolling(w), "Provider Responses")
        self.s_svc = self._svc_combo()
        self.s_method = QComboBox()
        self.s_method.setEditable(True)
        self.s_payload = PayloadEditor(self.ctx.protos)
        setb, echo = QPushButton("Set fixed response"), QPushButton("Echo request (default)")
        self.s_table = _table(["Time", "Service", "Method", "From", "Request", "Response", "Return code"])
        form = QFormLayout()
        form.addRow("Method", self.s_method)
        row = QHBoxLayout()
        row.addWidget(setb)
        row.addWidget(echo)
        row.addStretch()
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self.s_payload)
        lay.addLayout(row)
        lay.addWidget(QLabel("Handled requests"))
        lay.addWidget(self.s_table, 1)
        self.s_svc.currentIndexChanged.connect(self._fill_server_methods)
        setb.clicked.connect(lambda: self._set_response(True))
        echo.clicked.connect(lambda: self._set_response(False))

    def _fill_server_methods(self) -> None:
        svc = self._selected_svc(self.s_svc)
        self.s_method.clear()
        for m in (svc.methods if svc else []):
            self.s_method.addItem(f"{m.name} ({_hex(m.method_id)})", m)

    def _set_response(self, fixed: bool) -> None:
        svc = self._selected_svc(self.s_svc)
        if svc is None or not self._need_engine():
            return
        try:
            text = self.s_method.currentText()
            m = self.s_method.currentData()
            mid = m.method_id if isinstance(m, MethodDef) and text == self.s_method.itemText(
                self.s_method.currentIndex()) else int(text.split("(")[-1].strip(") ") if "(" in text else text, 0)
            payload = self.s_payload.encode() if fixed else None
        except (PayloadError, ValueError, ProtoError) as e:
            self._err(e)
            return
        self._provider(svc).set_response(mid, payload)

    def _on_method(self, m: dict) -> None:
        svc = next((s for s in self.ctx.services if s.name == m["service"]), None)
        name = next((x.name for x in (svc.methods if svc else []) if x.method_id == m["method_id"]), "")
        _add_row(self.s_table, [time.strftime("%H:%M:%S", time.localtime(m["t"])), m["service"],
                                f"{name} {_hex(m['method_id'])}".strip(), m["from"], m["request"].hex(" ").upper(),
                                m["response"].hex(" ").upper(), str(m["rc"])])

    # ======================================================================== SD log
    def _build_sd_tab(self) -> None:
        w = QWidget()
        self.tabs.addTab(scrolling(w), "SD Log Trace")
        self.sd_filter = QLineEdit(placeholderText="Filter (e.g. offer, 0x1234)")
        clear = QPushButton("Clear")
        self.sd_table = _table(["Time", "Dir", "Peer", "Entry"])
        self.sd_table.setColumnWidth(0, 90)
        self.sd_table.setColumnWidth(1, 40)
        self.sd_table.setColumnWidth(2, 150)
        row = QHBoxLayout()
        row.addWidget(self.sd_filter)
        row.addWidget(clear)
        lay = QVBoxLayout(w)
        lay.addLayout(row)
        lay.addWidget(self.sd_table)
        clear.clicked.connect(lambda: self.sd_table.setRowCount(0))

    def _on_sd(self, e: dict) -> None:
        f = self.sd_filter.text().strip().lower()
        if f and f not in e["entry"].lower():
            return
        _add_row(self.sd_table, [time.strftime("%H:%M:%S", time.localtime(e["t"])), e["dir"].upper(),
                                 e["peer"], e["entry"]], 3000)
        if e["dir"] == "rx" and ("OFFER" in e["entry"] or "ACK" in e["entry"]):
            self.availabilityChanged.emit()
