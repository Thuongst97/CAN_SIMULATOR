"""Settings popup: everything that used to live in the File / View / Tools menus."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDockWidget, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QVBoxLayout)

THEMES = (("dark", "Dark"), ("light", "Light"), ("auto", "Follow system"))


class SettingsDialog(QDialog):
    """Non-modal popup opened from the gear button in the tab bar corner.

    Sections: Appearance (theme), Files (open / save), Panels (show/hide, pop out, reset layout),
    Tools. All actions delegate to the main window, so behaviour is unchanged from the old menus.
    """

    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        self.setObjectName("settings-dialog")
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.setMinimumWidth(400)

        title = QLabel("Settings")
        title.setProperty("role", "title")

        # ---- Appearance ----
        self.theme_buttons: dict[str, QRadioButton] = {}
        group = QButtonGroup(self)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(16)
        for mode, label in THEMES:
            rb = QRadioButton(label)
            group.addButton(rb)
            rb.toggled.connect(lambda on, m=mode: on and self.win.set_theme(m))
            theme_row.addWidget(rb)
            self.theme_buttons[mode] = rb
        theme_row.addStretch()
        appearance = QGroupBox("Appearance")
        QVBoxLayout(appearance).addLayout(theme_row)

        # ---- Files ----
        files = QGroupBox("Files")
        grid = QGridLayout(files)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.file_buttons: dict[str, QPushButton] = {}
        for i, (key, text, fn) in enumerate((
                ("database", "Open database…", win.open_database),
                ("open_project", "Open project…", win.open_project),
                ("save_project", "Save project…", win.save_project))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, fn=fn: fn())
            grid.addWidget(b, i // 2, i % 2)
            self.file_buttons[key] = b

        # ---- Panels ----
        panels = QGroupBox("Panels")
        pv = QVBoxLayout(panels)
        pv.setSpacing(6)
        self.dock_checks: dict[str, QCheckBox] = {}
        for title_, dock in win.dock_registry().items():
            row = QHBoxLayout()
            cb = QCheckBox(title_)
            cb.toggled.connect(lambda on, d=dock: d.setVisible(on))
            # a panel on a tab that is not showing is still 'shown'; only an explicit hide/close unticks it
            dock.visibilityChanged.connect(lambda _vis, c=cb, d=dock: self._set_checked(c, not d.isHidden()))
            pop = QPushButton("Pop out")
            pop.setToolTip("Detach this panel into its own window, e.g. onto a second monitor")
            pop.setFixedWidth(90)
            if not dock.features() & QDockWidget.DockWidgetFloatable:
                pop.setEnabled(False)
                pop.setToolTip("The sidebar stays docked; collapse it with Ctrl+B instead")
            pop.clicked.connect(lambda _=False, t=title_: self.win.pop_out(t))
            row.addWidget(cb, 1)
            row.addWidget(pop)
            pv.addLayout(row)
            self.dock_checks[title_] = cb
        reset = QPushButton("Reset layout")
        reset.clicked.connect(win.reset_layout)
        rrow = QHBoxLayout()
        rrow.addStretch()
        rrow.addWidget(reset)
        pv.addLayout(rrow)

        # ---- Tools ----
        tools = QGroupBox("Tools")
        tv = QHBoxLayout(tools)
        self.stop_tx = QPushButton("Stop all cyclic TX")
        self.stop_tx.clicked.connect(win.signals_panel.stop_all)
        tv.addWidget(self.stop_tx)
        tv.addStretch()

        # ---- footer ----
        footer = QHBoxLayout()
        self.quit_btn = QPushButton("Quit")
        self.quit_btn.setToolTip("Exit AutoSim Studio")
        self.quit_btn.clicked.connect(win.close)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.close)
        footer.addWidget(self.quit_btn)
        footer.addStretch()
        footer.addWidget(close)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        lay.addWidget(title)
        for w in (appearance, files, panels, tools):
            lay.addWidget(w)
        lay.addLayout(footer)
        self.sync()

    @staticmethod
    def _set_checked(cb: QCheckBox, on: bool) -> None:
        cb.blockSignals(True)
        cb.setChecked(on)
        cb.blockSignals(False)

    def sync(self) -> None:
        """Refresh the controls from the live application state (theme, panel visibility)."""
        self.sync_theme()
        for title, cb in self.dock_checks.items():
            self._set_checked(cb, not self.win.dock_registry()[title].isHidden())

    def sync_theme(self) -> None:
        rb = self.theme_buttons.get(self.win.current_theme)
        if rb is not None and not rb.isChecked():
            rb.blockSignals(True)
            rb.setChecked(True)
            rb.blockSignals(False)

    def showEvent(self, e) -> None:
        self.sync()
        super().showEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)
