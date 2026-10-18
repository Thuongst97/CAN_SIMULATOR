"""Command registry, command palette (Ctrl+K) and keyboard shortcuts.

Every user action that has a shortcut or should be reachable from the palette is registered once:

    reg.add("trace.pause", "Pause / resume trace", fn, shortcut="Space", category="Trace")

* Shortcuts with a modifier (Ctrl+L ...) are QShortcuts on the main window.
* Single-key shortcuts (Space ...) go through `SingleKeyFilter`: they never fire while the keyboard focus is in a
  text input, spin box, combo box or text editor (typing a space must type a space), nor on a focused button /
  checkbox / slider (Space activates those). They also only fire while the main window is active.
* `tooltip(text, id)` appends the shortcut to a tooltip, e.g. "Pause trace  (Space)".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QDialog,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QStyledItemDelegate,
                               QDockWidget, QTextEdit, QVBoxLayout, QWidget)

from autosim.ui.theme import data_font

TEXT_INPUTS = (QLineEdit, QAbstractSpinBox, QComboBox, QTextEdit, QPlainTextEdit)
KEY_ACTIVATED = (QAbstractButton, QAbstractSlider)        # Space / arrows belong to these when focused


def focus_takes_keys(widget) -> bool:
    """True when single-key shortcuts must leave the key to the focused widget."""
    w = widget
    while w is not None:                                   # an editor inside a view / spin box line edit
        if isinstance(w, TEXT_INPUTS):
            return True
        w = w.parentWidget() if not w.isWindow() else None
    return isinstance(widget, KEY_ACTIVATED)


@dataclass
class Command:
    id: str
    title: str
    fn: Callable[[], None]
    shortcut: str = ""
    category: str = ""
    enabled: Callable[[], bool] | None = None

    def is_enabled(self) -> bool:
        return self.enabled() if self.enabled else True


class SingleKeyFilter(QObject):
    def __init__(self, window, registry: "CommandRegistry") -> None:
        super().__init__(window)
        self.window = window
        self.registry = registry

    def _owned(self, win) -> bool:
        """The main window itself or one of its floating panels (a popped-out dock keeps it as parent).
        Dialogs and popups are other windows: their keys are their own."""
        if win is self.window:
            return True
        if not isinstance(win, QDockWidget):
            return False
        w = win
        while w is not None:
            if w is self.window:
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.KeyPress or ev.modifiers() not in (Qt.NoModifier, Qt.KeypadModifier):
            return False
        cmd = self.registry.single_keys.get(ev.key())
        if cmd is None or ev.isAutoRepeat():
            return False
        # the key press is delivered to the focused widget: it must belong to the main window (not a dialog or a
        # floating panel's own window) and must not be a widget that uses the key itself
        if not isinstance(obj, QWidget) or not self._owned(obj.window()) or QApplication.activeModalWidget():
            return False
        if focus_takes_keys(obj) or not cmd.is_enabled():
            return False
        cmd.fn()
        return True


class CommandRegistry:
    def __init__(self, window) -> None:
        self.window = window
        self.commands: dict[str, Command] = {}
        self.single_keys: dict[int, Command] = {}
        self.shortcuts: dict[str, QShortcut] = {}
        self._filter = SingleKeyFilter(window, self)
        QApplication.instance().installEventFilter(self._filter)

    def add(self, cid: str, title: str, fn: Callable[[], None], shortcut: str = "", category: str = "",
            enabled: Callable[[], bool] | None = None) -> Command:
        cmd = Command(cid, title, fn, shortcut, category, enabled)
        self.commands[cid] = cmd
        if shortcut:
            seq = QKeySequence(shortcut)
            k = seq[0]
            if seq.count() == 1 and k.keyboardModifiers() == Qt.NoModifier:
                self.single_keys[k.key()] = cmd
            else:
                sc = QShortcut(seq, self.window)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(lambda c=cmd: c.is_enabled() and c.fn())
                self.shortcuts[cid] = sc
        return cmd

    def run(self, cid: str) -> None:
        cmd = self.commands[cid]
        if cmd.is_enabled():
            cmd.fn()

    def tooltip(self, text: str, cid: str) -> str:
        sc = self.commands.get(cid).shortcut if cid in self.commands else ""
        return f"{text}  ({sc})" if sc else text

    def uninstall(self) -> None:
        QApplication.instance().removeEventFilter(self._filter)


# ---- palette -------------------------------------------------------------------------------------------------
def matches(query: str, cmd: Command) -> bool:
    hay = f"{cmd.category} {cmd.title} {cmd.id}".lower()
    return all(word in hay for word in query.lower().split())


class _CommandDelegate(QStyledItemDelegate):
    """Title on the left, category and shortcut right-aligned in muted mono text."""

    def paint(self, p, option, index):
        super().paint(p, option, index)
        cmd: Command = index.data(Qt.UserRole)
        if cmd is None:
            return
        right = "   ".join(x for x in (cmd.category, cmd.shortcut) if x)
        p.save()
        p.setFont(data_font())
        p.setPen(option.palette.placeholderText().color())
        p.drawText(QRect(option.rect).adjusted(0, 0, -12, 0), Qt.AlignRight | Qt.AlignVCenter, right)
        p.restore()

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), 30)


class CommandPalette(QDialog):
    def __init__(self, registry: CommandRegistry, parent) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("command-palette")
        self.registry = registry
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a command…  (Enter runs, Esc closes)")
        self.search.setAccessibleName("Command search")
        self.list = QListWidget()
        self.list.setObjectName("command-list")
        self.list.setItemDelegate(_CommandDelegate(self.list))
        self.list.setUniformItemSizes(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self.search)
        lay.addWidget(self.list)
        self.resize(560, 380)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.run_current)
        self.list.itemActivated.connect(lambda _it: self.run_current())
        self.search.installEventFilter(self)

    def open_centered(self) -> None:
        win = self.parentWidget()
        self.search.clear()
        self.refresh("")
        g = win.geometry()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 80)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def refresh(self, query: str) -> None:
        self.list.clear()
        for cmd in self.registry.commands.values():
            if matches(query, cmd):
                it = QListWidgetItem(cmd.title)
                it.setData(Qt.UserRole, cmd)
                if not cmd.is_enabled():
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                    it.setToolTip("Not available right now")
                self.list.addItem(it)
        for i in range(self.list.count()):
            if self.list.item(i).flags() & Qt.ItemIsEnabled:
                self.list.setCurrentRow(i)
                break

    def run_current(self) -> None:
        it = self.list.currentItem()
        if it is None or not (it.flags() & Qt.ItemIsEnabled):
            return
        cmd: Command = it.data(Qt.UserRole)
        self.close()
        cmd.fn()

    def eventFilter(self, obj, ev):
        if obj is self.search and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Up, Qt.Key_Down):
            step = 1 if ev.key() == Qt.Key_Down else -1
            row = self.list.currentRow()
            for _ in range(self.list.count()):
                row = (row + step) % max(1, self.list.count())
                if self.list.item(row) and self.list.item(row).flags() & Qt.ItemIsEnabled:
                    self.list.setCurrentRow(row)
                    break
            return True
        return False
