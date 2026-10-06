"""Dual-theme design system: one token template, two palettes.

    DARK_QSS   obsidian dark   (base #09090B, cards #141518, indigo primary)
    LIGHT_QSS  clean technical light (base #F8FAFC, cards #FFFFFF, royal-blue primary)

Every colour in the template is a palette token (@NAME@), so both themes share identical geometry:
32 px controls (30 px inside compact cards), 6 px radius, 1 px borders, 3 px active-tab bar.
Image tokens (@ARROW_DOWN@, @ARROW_UP@, @CHECK@) are PNGs painted at runtime by ui/icons.build_qss_assets.
"""
from __future__ import annotations

DARK = {
    "BASE": "#09090B",            # --bg-base
    "SURFACE": "#141518",         # --bg-surface (cards, docks, table rows)
    "RAISED": "#1A1B20",          # --bg-muted (headers, toolbars, group boxes)
    "RAISED_HOVER": "#24262C",
    "ROW_HOVER": "#1B1C21",
    "INPUT": "#111215",
    "ROW_ALT": "#111215",
    "BORDER": "#27272B",          # --border
    "BORDER_STRONG": "#2E3038",
    "BORDER_HOVER": "#3A3C44",
    "GRID": "#1C1D21",
    "TEXT": "#F4F4F5",
    "MUTED": "#8F92A1",
    "DISABLED": "#5B5E6B",
    "SELECTION": "#202227",
    "ACCENT": "#6366F1",          # active tab bar, slider
    "ACCENT_SOFT": "rgba(99, 102, 241, 0.16)",
    "FOCUS": "rgba(99, 102, 241, 0.60)",
    "PRIMARY": "#4F46E5",
    "PRIMARY_HOVER": "#6366F1",
    "PRIMARY_PRESSED": "#4338CA",
    "PRIMARY_TEXT": "#FFFFFF",
    "BTN": "#1F2026",             # secondary button
    "BTN_HOVER": "#2A2B33",
    "BTN_PRESSED": "#18181B",
    "BTN_BORDER": "#2E3038",
    "BTN_TEXT": "#E4E4E7",
    "GEAR_HOVER": "#27272A",
    "DANGER": "#EF4444",          # destructive button border
    "DANGER_BG": "#18181B",
    "DANGER_TEXT": "#F87171",
    "DANGER_HOVER": "#7F1D1D",
    "DANGER_HOVER_TEXT": "#FFFFFF",
    "DANGER_PRESSED": "#651818",
    "SUCCESS": "#34D399",
    "WARNING": "#FBBF24",
    "TAB_HOVER": "rgba(244, 244, 245, 0.04)",
    "SCROLL": "rgba(143, 146, 161, 0.30)",
    "SCROLL_HOVER": "rgba(143, 146, 161, 0.55)",
    "SCROLL_PRESSED": "rgba(143, 146, 161, 0.75)",
}

LIGHT = {
    "BASE": "#F8FAFC",            # --bg-base (slate-50)
    "SURFACE": "#FFFFFF",         # --bg-surface
    "RAISED": "#F1F5F9",          # --bg-muted
    "RAISED_HOVER": "#E2E8F0",
    "ROW_HOVER": "#F1F5F9",
    "INPUT": "#FFFFFF",
    "ROW_ALT": "#F8FAFC",
    "BORDER": "#E2E8F0",          # --border
    "BORDER_STRONG": "#CBD5E1",
    "BORDER_HOVER": "#94A3B8",
    "GRID": "#EEF2F6",
    "TEXT": "#0F172A",
    "MUTED": "#64748B",
    "DISABLED": "#94A3B8",
    "SELECTION": "#DBEAFE",
    "ACCENT": "#2563EB",
    "ACCENT_SOFT": "rgba(37, 99, 235, 0.10)",
    "FOCUS": "rgba(37, 99, 235, 0.55)",
    "PRIMARY": "#2563EB",
    "PRIMARY_HOVER": "#1D4ED8",
    "PRIMARY_PRESSED": "#1E40AF",
    "PRIMARY_TEXT": "#FFFFFF",
    "BTN": "#F8FAFC",
    "BTN_HOVER": "#F1F5F9",
    "BTN_PRESSED": "#E2E8F0",
    "BTN_BORDER": "#CBD5E1",
    "BTN_TEXT": "#334155",
    "GEAR_HOVER": "#E2E8F0",
    "DANGER": "#FCA5A5",
    "DANGER_BG": "#FEF2F2",
    "DANGER_TEXT": "#DC2626",
    "DANGER_HOVER": "#FEE2E2",
    "DANGER_HOVER_TEXT": "#B91C1C",
    "DANGER_PRESSED": "#FECACA",
    "SUCCESS": "#059669",
    "WARNING": "#B45309",
    "TAB_HOVER": "rgba(15, 23, 42, 0.04)",
    "SCROLL": "rgba(100, 116, 139, 0.35)",
    "SCROLL_HOVER": "rgba(100, 116, 139, 0.60)",
    "SCROLL_PRESSED": "rgba(100, 116, 139, 0.80)",
}

FONT_UI = '"Inter", "Segoe UI", "Roboto", "Helvetica Neue", Arial, sans-serif'
FONT_MONO = '"JetBrains Mono", "Cascadia Mono", Consolas, "Courier New", monospace'

TEMPLATE = """
/* ============ base ============ */
* { outline: none; }
QWidget { background: transparent; color: @TEXT@; font-family: @FONT_UI@; font-size: 9.5pt; }
QMainWindow, QDialog, QMessageBox, QMenu, QToolTip { background: @BASE@; }
QDockWidget { background: @SURFACE@; }
QWidget:disabled { color: @DISABLED@; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QLabel[role="caption"] { color: @MUTED@; font-size: 8.5pt; font-weight: 600; }
QLabel[role="title"] { font-size: 11pt; font-weight: 700; }
QLabel[role="muted"] { color: @MUTED@; }
QLabel#status-ok { color: @SUCCESS@; font-weight: 600; }
QLabel#status-bad { color: @DANGER_TEXT@; font-weight: 600; }

/* ============ cards ============ */
QFrame#card { background: @SURFACE@; border: 1px solid @BORDER@; border-radius: 8px; }
QFrame#panel-card { background: @SURFACE@; border: 1px solid @BORDER@; border-radius: 8px; }
QWidget#panel-header { background: @RAISED@; border-bottom: 1px solid @BORDER@;
                       border-top-left-radius: 8px; border-top-right-radius: 8px; }
QLabel#panel-title { color: @MUTED@; font-weight: 700; }
QFrame#empty-state { background: @SURFACE@; border: 1px solid @BORDER@; border-radius: 12px; }
QGroupBox {
    background: @RAISED@; border: 1px solid @BORDER@; border-radius: 8px;
    margin-top: 16px; padding: 16px 12px 12px 12px; font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px;
    color: @MUTED@; font-weight: 700;
}

/* ============ buttons (32 px) ============ */
QPushButton {
    background: @BTN@; border: 1px solid @BTN_BORDER@; border-radius: 6px;
    padding: 0 14px; min-height: 30px; color: @BTN_TEXT@; font-weight: 500;
}
QPushButton:hover { background: @BTN_HOVER@; border-color: @BORDER_HOVER@; color: @TEXT@; }
QPushButton:pressed { background: @BTN_PRESSED@; border-color: @BTN_BORDER@; }
QPushButton:disabled { background: @RAISED@; color: @DISABLED@; border-color: @BORDER@; }
QPushButton:checked { background: @PRIMARY@; color: @PRIMARY_TEXT@; border-color: @PRIMARY@; }
QPushButton[kind="primary"] { background: @PRIMARY@; color: @PRIMARY_TEXT@; border: 1px solid @PRIMARY@; font-weight: 600; }
QPushButton[kind="primary"]:hover { background: @PRIMARY_HOVER@; border-color: @PRIMARY_HOVER@; color: @PRIMARY_TEXT@; }
QPushButton[kind="primary"]:pressed { background: @PRIMARY_PRESSED@; border-color: @PRIMARY_PRESSED@; }
QPushButton[kind="primary"]:disabled { background: @RAISED@; color: @DISABLED@; border-color: @BORDER@; }
QPushButton[kind="danger"] { background: @DANGER_BG@; color: @DANGER_TEXT@; border: 1px solid @DANGER@; font-weight: 600; }
QPushButton[kind="danger"]:hover { background: @DANGER_HOVER@; color: @DANGER_HOVER_TEXT@; border-color: @DANGER@; }
QPushButton[kind="danger"]:pressed { background: @DANGER_PRESSED@; color: @DANGER_HOVER_TEXT@; border-color: @DANGER@; }
QPushButton[kind="danger"]:disabled { background: @RAISED@; color: @DISABLED@; border-color: @BORDER@; }
QPushButton[kind="icon"] { padding: 0; min-width: 30px; max-width: 30px; }

QToolButton {
    background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 0 10px; min-height: 30px; color: @TEXT@;
}
QToolButton:hover { background: @RAISED_HOVER@; border-color: @BORDER@; }
QToolButton:pressed { background: @BTN_PRESSED@; }
QToolButton:checked { background: @ACCENT_SOFT@; border-color: @BORDER_HOVER@; color: @TEXT@; }
QToolButton:disabled { color: @DISABLED@; }
QToolButton::menu-indicator { image: none; }
QToolButton#settings-btn {
    margin: 4px 8px 4px 0; padding: 0; min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
    border: none; border-radius: 4px; background: transparent;
}
QToolButton#settings-btn:hover { background: @GEAR_HOVER@; }
QToolButton#settings-btn:pressed { background: @BTN_HOVER@; }
QToolButton#panel-btn { min-height: 22px; max-height: 22px; min-width: 22px; padding: 0 4px; border-radius: 4px; }

/* ============ inputs (32 px) ============ */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: @INPUT@; border: 1px solid @BORDER_STRONG@; border-radius: 6px; padding: 0 10px;
    min-height: 30px; selection-background-color: @PRIMARY@; selection-color: @PRIMARY_TEXT@;
}
QPlainTextEdit, QTextEdit {
    background: @INPUT@; border: 1px solid @BORDER_STRONG@; border-radius: 6px; padding: 6px 10px;
    selection-background-color: @PRIMARY@; selection-color: @PRIMARY_TEXT@;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: @BORDER_HOVER@; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid @FOCUS@;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: @RAISED@; color: @DISABLED@; border-color: @BORDER@;
}
QComboBox { padding-right: 26px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; }
QComboBox::down-arrow { image: url(@ARROW_DOWN@); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background: @SURFACE@; border: 1px solid @BORDER_STRONG@; border-radius: 6px; padding: 4px;
    selection-background-color: @SELECTION@; selection-color: @TEXT@; outline: none;
}
QSpinBox, QDoubleSpinBox { padding-right: 20px; max-height: 30px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: none; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: none; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(@ARROW_UP@); width: 9px; height: 9px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(@ARROW_DOWN@); width: 9px; height: 9px; }

/* compact cards (SOME/IP sidebar): 30 px controls */
QFrame[density="compact"] QLineEdit, QFrame[density="compact"] QComboBox, QFrame[density="compact"] QSpinBox,
QFrame[density="compact"] QPushButton { min-height: 28px; max-height: 28px; }

QCheckBox, QRadioButton { spacing: 8px; min-height: 24px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid @BORDER_STRONG@; border-radius: 4px; background: @INPUT@; }
QCheckBox::indicator:hover { border-color: @BORDER_HOVER@; }
QCheckBox::indicator:checked { background: @PRIMARY@; border-color: @PRIMARY@; image: url(@CHECK@); }
QCheckBox::indicator:disabled { background: @RAISED@; border-color: @BORDER@; }
QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid @BORDER_STRONG@; border-radius: 9px; background: @INPUT@; }
QRadioButton::indicator:hover { border-color: @BORDER_HOVER@; }
QRadioButton::indicator:checked {
    border-color: @PRIMARY@;
    background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                                stop: 0 #FFFFFF, stop: 0.36 #FFFFFF, stop: 0.40 @PRIMARY@, stop: 1 @PRIMARY@);
}

/* ============ sliders ============ */
QSlider::groove:horizontal { height: 4px; background: @BORDER_STRONG@; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @ACCENT@; border-radius: 2px; }
QSlider::handle:horizontal { background: @SURFACE@; width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; border: 2px solid @ACCENT@; }
QSlider::handle:horizontal:disabled, QSlider::sub-page:horizontal:disabled { background: @BORDER_STRONG@; border-color: @BORDER_STRONG@; }

/* ============ tabs: 3 px accent indicator ============ */
QTabWidget::pane { border: none; border-top: 1px solid @BORDER@; top: -1px; }
QTabBar { background: transparent; qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent; color: @MUTED@; padding: 9px 20px; margin-right: 2px; min-height: 18px;
    border: none; border-bottom: 3px solid transparent; font-weight: 600;
}
QTabBar::tab:hover { color: @TEXT@; background: @TAB_HOVER@; border-bottom: 3px solid @BORDER_STRONG@; }
QTabBar::tab:selected { color: @TEXT@; border-bottom: 3px solid @ACCENT@; background: transparent; }

/* ============ tables / trees ============ */
QTreeView, QTreeWidget, QTableView, QTableWidget, QListView {
    background: @SURFACE@; alternate-background-color: @ROW_ALT@; border: 1px solid @BORDER@; border-radius: 6px;
    gridline-color: @GRID@; selection-background-color: @SELECTION@; selection-color: @TEXT@;
}
QTreeView::item, QTableView::item { padding: 2px 6px; border: none; }
QTreeView::item:hover, QTableView::item:hover { background: @ROW_HOVER@; }
QTreeView::item:selected, QTableView::item:selected, QTreeView::item:selected:!active, QTableView::item:selected:!active {
    background: @SELECTION@; color: @TEXT@;
}
QHeaderView { background: @RAISED@; }
QHeaderView::section {
    background: @RAISED@; color: @MUTED@; font-weight: 600; padding: 0 10px; min-height: 34px;
    border: none; border-right: 1px solid @BORDER@; border-bottom: 1px solid @BORDER@;
}
QHeaderView::section:hover { color: @TEXT@; }
QTableCornerButton::section { background: @RAISED@; border: none; }
QPlainTextEdit#log { font-family: @FONT_MONO@; background: @INPUT@; }

/* ============ scrollbars ============ */
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @SCROLL@; border-radius: 5px; min-height: 28px; margin: 1px; }
QScrollBar::handle:horizontal { background: @SCROLL@; border-radius: 5px; min-width: 28px; margin: 1px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: @SCROLL_HOVER@; }
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed { background: @SCROLL_PRESSED@; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QAbstractScrollArea::corner { background: transparent; }
QScrollArea { border: none; }

/* ============ docks / splitters / toolbars ============ */
QDockWidget { border: 1px solid @BORDER@; titlebar-close-icon: none; titlebar-normal-icon: none; }
QDockWidget::title {
    background: @RAISED@; color: @MUTED@; padding: 8px 12px; font-weight: 700;
    border-bottom: 1px solid @BORDER@; text-align: left;
}
QDockWidget::close-button, QDockWidget::float-button { background: transparent; border-radius: 4px; padding: 2px; }
QDockWidget::close-button:hover, QDockWidget::float-button:hover { background: @RAISED_HOVER@; }
QMainWindow::separator { background: @BASE@; width: 6px; height: 6px; }
QMainWindow::separator:hover { background: @BORDER_HOVER@; }
QSplitter::handle { background: @BASE@; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QSplitter::handle:hover { background: @BORDER_HOVER@; }
QToolBar { background: @RAISED@; border: none; border-bottom: 1px solid @BORDER@; spacing: 8px; padding: 6px 10px; }
QToolBar::separator { background: @BORDER_STRONG@; width: 1px; margin: 6px 6px; }
QToolBar QLabel { padding: 0 2px; }

/* ============ menus / status / tooltips ============ */
QMenuBar { background: @BASE@; border-bottom: 1px solid @BORDER@; }
QMenu { background: @SURFACE@; border: 1px solid @BORDER@; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 7px 28px 7px 14px; border-radius: 4px; }
QMenu::item:selected { background: @SELECTION@; }
QMenu::item:disabled { color: @DISABLED@; }
QMenu::separator { height: 1px; background: @BORDER@; margin: 5px 8px; }
QStatusBar { background: @BASE@; color: @MUTED@; border-top: 1px solid @BORDER@; }
QToolTip { color: @TEXT@; border: 1px solid @BORDER@; border-radius: 4px; padding: 4px 8px; background: @SURFACE@; }
"""


def render(palette: dict[str, str]) -> str:
    """Substitute fonts + palette tokens (image tokens are left for build_qss)."""
    qss = TEMPLATE.replace("@FONT_UI@", FONT_UI).replace("@FONT_MONO@", FONT_MONO)
    for k in sorted(palette, key=len, reverse=True):          # longest first: PRIMARY_HOVER before PRIMARY
        qss = qss.replace(f"@{k}@", palette[k])
    return qss


DARK_QSS = render(DARK)
LIGHT_QSS = render(LIGHT)


def build_qss(palette: dict[str, str], assets: dict[str, str]) -> str:
    """Final stylesheet: palette + painted image assets (arrow / check PNG paths)."""
    qss = render(palette)
    for k, v in assets.items():
        qss = qss.replace(f"@{k.upper()}@", v)
    return qss
