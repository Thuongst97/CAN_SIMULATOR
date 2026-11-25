"""Dual-theme stylesheet: one QSS template, rendered with the design tokens of styles/tokens.py.

    DARK / LIGHT       colour tokens of each theme (same keys)
    DARK_QSS / LIGHT_QSS  the template with colours + sizes substituted (image tokens left for build_qss)

Every colour in the template is a token (@NAME@) and every size comes from the token scale, so both themes
share identical geometry. Image tokens (@ARROW_DOWN@, @ARROW_UP@, @CHECK@) are PNGs painted at runtime by
ui/icons.build_qss_assets. Fonts are set on the QApplication (ui/theme.py: Segoe UI);
the template only sets sizes / weights that differ from the body text.
"""
from __future__ import annotations

from autosim.ui.styles import tokens as T

DARK = T.DARK_COLORS
LIGHT = T.LIGHT_COLORS

FONT_UI = ", ".join(f'"{f}"' for f in T.FONT_UI_FAMILIES) + ", sans-serif"

SIZES = {
    "FS_CAPTION": f"{T.TYPE['caption'][0]}px", "FW_CAPTION": str(T.TYPE["caption"][1]),
    "FS_BODY": f"{T.TYPE['body'][0]}px",
    "FS_TITLE": f"{T.TYPE['panel_title'][0]}px", "FW_TITLE": str(T.TYPE["panel_title"][1]),
    "FS_SECTION": f"{T.TYPE['section'][0]}px",
    "FS_APP": f"{T.TYPE['app_title'][0]}px", "FW_APP": str(T.TYPE["app_title"][1]),
    "R_SM": f"{T.RADIUS_SM}px", "R_MD": f"{T.RADIUS_MD}px", "R_LG": f"{T.RADIUS_LG}px",
    "CTRL_MIN": f"{T.CONTROL_H - 2 * T.BORDER_W}px",            # min-height excludes the 1 px borders
    "CTRL_MIN_COMPACT": f"{T.CONTROL_H_COMPACT}px",
}

TEMPLATE = """
/* ============ base ============ */
* { outline: none; }
QWidget { background: transparent; color: @TEXT_PRIMARY@; }
QMainWindow, QDialog, QMessageBox { background: @SURFACE_0@; }
QDockWidget { background: @SURFACE_1@; }
QWidget:disabled { color: @TEXT_DISABLED@; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QLabel[role="caption"] { color: @TEXT_SECONDARY@; font-size: @FS_CAPTION@; font-weight: @FW_CAPTION@; }
QLabel[role="title"] { font-size: @FS_TITLE@; font-weight: @FW_TITLE@; }
QLabel[role="muted"] { color: @TEXT_MUTED@; }
QLabel#status-ok { color: @SUCCESS@; font-weight: 600; }
QLabel#status-bad { color: @ERROR@; font-weight: 600; }

/* ============ cards ============ */
QFrame#card { background: @SURFACE_1@; border: 1px solid @BORDER@; border-radius: @R_LG@; }
QFrame#panel-card { background: @SURFACE_1@; border: 1px solid @BORDER@; border-radius: @R_LG@; }
QWidget#panel-header { background: @SURFACE_2@; border-bottom: 1px solid @BORDER@;
                       border-top-left-radius: @R_LG@; border-top-right-radius: @R_LG@; }
QLabel#panel-title { color: @TEXT_PRIMARY@; font-size: @FS_TITLE@; font-weight: @FW_TITLE@; }
QFrame#empty-state { background: @SURFACE_1@; border: 1px solid @BORDER@; border-radius: @R_LG@; }
QGroupBox {
    background: @SURFACE_2@; border: 1px solid @BORDER@; border-radius: @R_LG@;
    margin-top: 16px; padding: 16px 12px 12px 12px; font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 4px;
    color: @TEXT_SECONDARY@; font-weight: 600;
}

/* ============ buttons (32 px) ============ */
QPushButton {
    background: @SURFACE_2@; border: 1px solid @BORDER_STRONG@; border-radius: @R_MD@;
    padding: 0 14px; min-height: @CTRL_MIN@; color: @TEXT_PRIMARY@; font-weight: 500;
}
QPushButton:hover { background: @SURFACE_3@; border-color: @BORDER_HOVER@; }
QPushButton:pressed { background: @SURFACE_1@; border-color: @BORDER_STRONG@; }
QPushButton:focus { border: 2px solid @FOCUS_RING@; padding: 0 13px; }
QPushButton:disabled { background: @SURFACE_2@; color: @TEXT_DISABLED@; border-color: @BORDER@; }
QPushButton:checked { background: @ACCENT_STRONG@; color: @ON_ACCENT@; border-color: @ACCENT_STRONG@; }
QPushButton[kind="primary"] { background: @ACCENT_STRONG@; color: @ON_ACCENT@; border: 1px solid @ACCENT_STRONG@; font-weight: 600; }
QPushButton[kind="primary"]:hover { background: @ACCENT_STRONG_HOVER@; border-color: @ACCENT_STRONG_HOVER@; color: @ON_ACCENT@; }
QPushButton[kind="primary"]:pressed { background: @ACCENT_STRONG_PRESSED@; border-color: @ACCENT_STRONG_PRESSED@; }
QPushButton[kind="primary"]:focus { border: 2px solid @FOCUS_RING@; padding: 0 13px; }
QPushButton[kind="primary"]:disabled { background: @SURFACE_2@; color: @TEXT_DISABLED@; border-color: @BORDER@; }
QPushButton[kind="danger"] { background: transparent; color: @ERROR@; border: 1px solid @ERROR@; font-weight: 600; }
QPushButton[kind="danger"]:hover { background: @ERROR_STRONG@; color: @ON_ACCENT@; border-color: @ERROR_STRONG@; }
QPushButton[kind="danger"]:pressed { background: @ERROR_STRONG@; color: @ON_ACCENT@; border-color: @ERROR_STRONG@; }
QPushButton[kind="danger"]:focus { border: 2px solid @FOCUS_RING@; padding: 0 13px; }
QPushButton[kind="danger"]:disabled { background: @SURFACE_2@; color: @TEXT_DISABLED@; border-color: @BORDER@; }
QPushButton[kind="ghost"] { background: transparent; border-color: transparent; }
QPushButton[kind="ghost"]:hover { background: @SURFACE_3@; border-color: @BORDER@; }
QPushButton[kind="ghost-danger"] { background: transparent; color: @TEXT_SECONDARY@; border: 1px solid @BORDER_STRONG@; }
QPushButton[kind="ghost-danger"]:hover, QPushButton[kind="ghost-danger"]:pressed {
    background: @ERROR_STRONG@; color: @ON_ACCENT@; border-color: @ERROR_STRONG@;
}
QPushButton[kind="ghost-danger"]:focus { border: 2px solid @FOCUS_RING@; padding: 0 13px; }
QPushButton[kind="icon"] { padding: 0; min-width: 30px; max-width: 30px; }

QToolButton {
    background: transparent; border: 1px solid transparent; border-radius: @R_MD@;
    padding: 0 10px; min-height: @CTRL_MIN@; color: @TEXT_PRIMARY@;
}
QToolButton:hover { background: @SURFACE_3@; border-color: @BORDER@; }
QToolButton:pressed { background: @SURFACE_1@; }
QToolButton:focus { border: 2px solid @FOCUS_RING@; padding: 0 9px; }
QToolButton:checked { background: @SURFACE_3@; border-color: @BORDER@; color: @TEXT_PRIMARY@; }
QToolButton:disabled { color: @TEXT_DISABLED@; }
QToolButton::menu-indicator { image: none; }
QToolButton#meas-start, QToolButton#meas-stop { padding: 0; min-height: 30px; border-radius: @R_MD@;
    border: 1px solid @BORDER@; background: @SURFACE_2@; }
QToolButton#meas-start:hover, QToolButton#meas-stop:hover { background: @SURFACE_3@; }
QToolButton#meas-start:disabled, QToolButton#meas-stop:disabled { background: transparent; border-color: transparent; }
QFrame#meas-sep { background: @BORDER@; border: none; }
QToolButton#meas-start:focus, QToolButton#meas-stop:focus { padding: 0; }
QToolButton#settings-btn {
    margin: 4px 8px 4px 0; padding: 0; min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
    border: none; border-radius: @R_SM@; background: transparent;
}
QToolButton#settings-btn:hover { background: @SURFACE_3@; }
QToolButton#settings-btn:pressed { background: @SURFACE_2@; }
QToolButton#settings-btn:focus { border: 2px solid @FOCUS_RING@; }
/* Interactive generator header: Connect / Disconnect all next to the status, a divider before the tools */
QPushButton#header-connect { min-height: 26px; max-height: 26px; padding: 0 12px; }
QPushButton#header-connect:focus { padding: 0 11px; }
QFrame#header-sep { background: @BORDER_STRONG@; border: none; }
/* buttons in window headers (Collapse messages, Add signals…, Full view, Clear): small outlined buttons */
QPushButton#panel-text-btn {
    background: @SURFACE_1@; border: 1px solid @BORDER_STRONG@; border-radius: @R_MD@; padding: 0 10px;
    min-height: 26px; max-height: 26px; color: @TEXT_PRIMARY@; font-weight: 400;
}
QPushButton#panel-text-btn:hover { background: @SURFACE_3@; border-color: @BORDER_HOVER@; }
QPushButton#panel-text-btn:pressed { background: @SURFACE_2@; }
QPushButton#panel-text-btn:focus { border: 2px solid @FOCUS_RING@; padding: 0 9px; }
/* the header's main action (Add signals…): green like the other primary buttons (Add scenario), same small size */
QPushButton#panel-text-btn[kind="primary"] { background: @ACCENT_STRONG@; color: @ON_ACCENT@;
    border-color: @ACCENT_STRONG@; font-weight: 600; }
QPushButton#panel-text-btn[kind="primary"]:hover { background: @ACCENT_STRONG_HOVER@; border-color: @ACCENT_STRONG_HOVER@; }
QPushButton#panel-text-btn[kind="primary"]:pressed { background: @ACCENT_STRONG_PRESSED@; border-color: @ACCENT_STRONG_PRESSED@; }
QToolButton#msg-fold { padding: 0; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; border: none; }
QToolButton#msg-fold:hover { background: @ROW_HOVER@; }
QToolButton#panel-btn { min-height: 22px; max-height: 22px; min-width: 22px; padding: 0 4px; border-radius: @R_SM@; }

/* ============ inputs (32 px) ============ */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: @INPUT_BG@; border: 1px solid @BORDER_STRONG@; border-radius: @R_MD@; padding: 0 10px;
    min-height: @CTRL_MIN@; selection-background-color: @ACCENT_STRONG@; selection-color: @ON_ACCENT@;
}
QPlainTextEdit, QTextEdit {
    background: @INPUT_BG@; border: 1px solid @BORDER_STRONG@; border-radius: @R_MD@; padding: 6px 10px;
    selection-background-color: @ACCENT_STRONG@; selection-color: @ON_ACCENT@;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: @BORDER_HOVER@; }
/* fields: a quiet 1 px accent border while typing (the 2 px ring is for keyboard focus on buttons) */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid @ACCENT@; }
QPlainTextEdit:focus, QTextEdit:focus { border: 1px solid @ACCENT@; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: @SURFACE_1@; color: @TEXT_DISABLED@; border-color: @BORDER@;
}
QComboBox { padding-right: 26px; }
QComboBox:focus { padding-right: 25px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; }
QComboBox::down-arrow { image: url(@ARROW_DOWN@); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background: @SURFACE_1@; border: 1px solid @BORDER_STRONG@; border-radius: @R_MD@; padding: 4px;
    selection-background-color: @SELECTION@; selection-color: @TEXT_PRIMARY@; outline: none;
}
QSpinBox, QDoubleSpinBox { padding-right: 20px; max-height: @CTRL_MIN@; }
QSpinBox:focus, QDoubleSpinBox:focus { padding-right: 19px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: none; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: none; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(@ARROW_UP@); width: 9px; height: 9px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(@ARROW_DOWN@); width: 9px; height: 9px; }

/* compact cards (SOME/IP sidebar): 28 px controls */
QFrame[density="compact"] QLineEdit, QFrame[density="compact"] QComboBox, QFrame[density="compact"] QSpinBox,
QFrame[density="compact"] QPushButton { min-height: @CTRL_MIN_COMPACT@; max-height: @CTRL_MIN_COMPACT@; }

QCheckBox, QRadioButton { spacing: 8px; min-height: 24px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid @BORDER_STRONG@; border-radius: @R_SM@; background: @INPUT_BG@; }
QCheckBox::indicator:hover { border-color: @BORDER_HOVER@; }
QCheckBox::indicator:checked { background: @ACCENT_STRONG@; border-color: @ACCENT_STRONG@; image: url(@CHECK@); }
QCheckBox::indicator:focus { border: 2px solid @FOCUS_RING@; width: 14px; height: 14px; }
QCheckBox::indicator:disabled { background: @SURFACE_1@; border-color: @BORDER@; }
QCheckBox::indicator:checked:disabled { background: @TEXT_MUTED@; border-color: @TEXT_MUTED@; image: url(@CHECK@); }
/* check boxes inside trees (signal selection): same look as QCheckBox */
QTreeView::indicator { width: 14px; height: 14px; border: 1px solid @BORDER_HOVER@; border-radius: @R_SM@; background: @INPUT_BG@; }
QTreeView::indicator:checked { background: @ACCENT_STRONG@; border-color: @ACCENT_STRONG@; image: url(@CHECK@); }
QTreeView::indicator:indeterminate { background: @ACCENT_SOFT@; border-color: @ACCENT_STRONG@; }
QTreeView::indicator:disabled { background: @SURFACE_3@; border-color: @BORDER@; }
QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid @BORDER_STRONG@; border-radius: 9px; background: @INPUT_BG@; }
QRadioButton::indicator:hover { border-color: @BORDER_HOVER@; }
QRadioButton::indicator:focus { border: 2px solid @FOCUS_RING@; width: 14px; height: 14px; }
QRadioButton::indicator:checked {
    border-color: @ACCENT_STRONG@;
    background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                                stop: 0 @ON_ACCENT@, stop: 0.36 @ON_ACCENT@, stop: 0.40 @ACCENT_STRONG@, stop: 1 @ACCENT_STRONG@);
}

/* ============ sliders ============ */
QSlider::groove:horizontal { height: 4px; background: @BORDER_STRONG@; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @ACCENT@; border-radius: 2px; }
QSlider::handle:horizontal { background: @SURFACE_1@; width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; border: 2px solid @ACCENT@; }
QSlider::handle:horizontal:focus { background: @ACCENT@; }
QSlider::handle:horizontal:disabled, QSlider::sub-page:horizontal:disabled { background: @BORDER_STRONG@; border-color: @BORDER_STRONG@; }

/* ============ tabs: 3 px accent indicator ============ */
QTabWidget::pane { border: none; border-top: 1px solid @BORDER@; top: -1px; }
QTabBar { background: transparent; qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent; color: @TEXT_SECONDARY@; padding: 8px 20px; margin-right: 2px; min-height: 18px;
    border: none; border-bottom: 3px solid transparent; font-weight: 600;
}
QTabBar::tab:hover { color: @TEXT_PRIMARY@; background: @SURFACE_2@; border-bottom: 3px solid @BORDER_STRONG@; }
QTabBar::tab:selected { color: @TEXT_PRIMARY@; border-bottom: 3px solid @ACCENT@; background: transparent; }
/* desktop rail (Setup / Simulation / Analysis) on the left */
QWidget#desktop-rail { background: @SURFACE_0@; border-right: 1px solid @BORDER@; }
QToolButton#rail-btn { border: none; border-radius: @R_MD@; padding: 4px 0 2px 0; color: @TEXT_SECONDARY@;
    font-weight: @FW_TITLE@; background: transparent; }
QToolButton#rail-btn:hover { background: @SURFACE_3@; color: @TEXT_PRIMARY@; }
QToolButton#rail-btn:checked { background: @ACCENT_SOFT@; color: @ACCENT@; }
QToolButton#rail-btn:focus { border: 2px solid @FOCUS_RING@; }
QToolButton#rail-tool { border: none; border-radius: @R_MD@; background: transparent; padding: 0; }
QToolButton#rail-tool:hover { background: @SURFACE_3@; }
QToolButton#rail-tool:focus { border: 2px solid @FOCUS_RING@; }
/* Signal plot: compact controls in the window header */
QWidget#plot-controls QComboBox, QWidget#plot-controls QDoubleSpinBox { min-height: 26px; max-height: 26px; }
/* Databases window */
QLabel#db-section { color: @TEXT_SECONDARY@; font-weight: @FW_TITLE@; padding: 2px 2px; }
QToolButton#db-tool { min-width: 32px; max-width: 32px; padding: 0; }

/* ============ tables / trees ============ */
QTreeView, QTreeWidget, QTableView, QTableWidget, QListView {
    background: @SURFACE_1@; alternate-background-color: @ROW_ALT@; border: 1px solid @BORDER@; border-radius: @R_MD@;
    gridline-color: @GRID@; selection-background-color: @SELECTION@; selection-color: @TEXT_PRIMARY@;
}
QTreeView:focus, QTreeWidget:focus, QTableView:focus, QTableWidget:focus, QListView:focus { border-color: @BORDER_STRONG@; }
QTreeView::item, QTableView::item { padding: 2px 6px; border: none; }
QTreeView::item:hover, QTableView::item:hover { background: @ROW_HOVER@; }
QTreeView::item:selected, QTableView::item:selected, QTreeView::item:selected:!active, QTableView::item:selected:!active {
    background: @SELECTION@; color: @TEXT_PRIMARY@;
}
QHeaderView { background: @SURFACE_2@; }
QHeaderView::section {
    background: @SURFACE_2@; color: @TEXT_SECONDARY@; font-weight: 600; padding: 0 10px; min-height: 34px;
    border: none; border-right: 1px solid @BORDER@; border-bottom: 1px solid @BORDER@;
}
QHeaderView::section:hover { color: @TEXT_PRIMARY@; }
QTableCornerButton::section { background: @SURFACE_2@; border: none; }
QPlainTextEdit#log { background: @SURFACE_1@; }

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
    background: @SURFACE_2@; color: @TEXT_PRIMARY@; padding: 8px 12px; font-weight: @FW_TITLE@;
    border-bottom: 1px solid @BORDER@; text-align: left;
}
QDockWidget::close-button, QDockWidget::float-button { background: transparent; border-radius: @R_SM@; padding: 2px; }
QDockWidget::close-button:hover, QDockWidget::float-button:hover { background: @SURFACE_3@; }
/* window dividers: a 1 px line in the middle of a 9 px grab area (accent while hovered / dragged) */
QMainWindow::separator { width: 9px; height: 9px; }
QMainWindow::separator:vertical { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 @SURFACE_0@,
    stop:0.444 @SURFACE_0@, stop:0.445 @BORDER_STRONG@, stop:0.555 @BORDER_STRONG@, stop:0.556 @SURFACE_0@,
    stop:1 @SURFACE_0@); }
QMainWindow::separator:horizontal { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 @SURFACE_0@,
    stop:0.444 @SURFACE_0@, stop:0.445 @BORDER_STRONG@, stop:0.555 @BORDER_STRONG@, stop:0.556 @SURFACE_0@,
    stop:1 @SURFACE_0@); }
QMainWindow::separator:vertical:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 @SURFACE_0@,
    stop:0.333 @SURFACE_0@, stop:0.334 @ACCENT_STRONG@, stop:0.666 @ACCENT_STRONG@, stop:0.667 @SURFACE_0@,
    stop:1 @SURFACE_0@); }
QMainWindow::separator:horizontal:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 @SURFACE_0@,
    stop:0.333 @SURFACE_0@, stop:0.334 @ACCENT_STRONG@, stop:0.666 @ACCENT_STRONG@, stop:0.667 @SURFACE_0@,
    stop:1 @SURFACE_0@); }
QSplitter::handle { background: @SURFACE_0@; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QSplitter::handle:hover { background: @BORDER_HOVER@; }
/* panel toolbars: one row of 32 px items, vertically centred */
QToolBar { background: @SURFACE_2@; border: none; border-bottom: 1px solid @BORDER@; spacing: 8px; padding: 0 10px; }
QToolBar QToolButton { min-height: @CTRL_MIN@; max-height: @CTRL_MIN@; }
QToolBar::separator { background: @BORDER_STRONG@; width: 1px; margin: 6px 6px; }
QToolBar QLabel { padding: 0 2px; }
QFrame#toolbar-divider { background: @BORDER_STRONG@; border: none; margin: 0 6px; }

/* ============ app shell ============ */
QToolButton#appbar-btn {
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; padding: 0;
    border: 1px solid transparent; border-radius: @R_MD@; background: transparent;
}
QToolButton#appbar-btn:hover { background: @SURFACE_3@; }
QToolButton#appbar-btn:checked { background: @ACCENT_SOFT@; border-color: transparent; }
QToolButton#appbar-btn:focus { border: 2px solid @FOCUS_RING@; padding: 0; }
QPushButton#appbar-connect { min-height: 26px; max-height: 26px; padding: 0 12px; }
QPushButton#appbar-connect:focus { padding: 0 11px; }
/* window headers: one hairline, no coloured rules; the focused window only gets a brighter title */
QWidget#panel-header, QWidget#panel-header[primary="true"], QWidget#panel-header[focused="true"] {
    border-bottom: 1px solid @BORDER@; }
QWidget#panel-header QLabel#panel-title { color: @TEXT_SECONDARY@; }
QWidget#panel-header[focused="true"] QLabel#panel-title { color: @TEXT_PRIMARY@; }
QScrollArea#sidebar, QWidget#sidebar-host { background: @SURFACE_1@; }
QWidget#section-header { background: @SURFACE_2@; border-top: 1px solid @BORDER@; border-bottom: 1px solid @BORDER@; }
QLabel#section-title { color: @TEXT_PRIMARY@; font-size: @FS_TITLE@; font-weight: @FW_TITLE@; }
QLabel#status-seg { color: @TEXT_SECONDARY@; padding: 0 4px; }
QLabel#status-seg[tone="warning"] { color: @WARNING@; }
QLabel#status-seg[tone="success"] { color: @SUCCESS@; }
QToolButton#status-btn { min-height: 22px; max-height: 22px; padding: 0 10px; border-radius: @R_SM@; color: @TEXT_SECONDARY@; }
QToolButton#status-btn:checked { background: @SURFACE_3@; border-color: @BORDER@; color: @TEXT_PRIMARY@; }
QToolButton#status-btn:focus { border: 2px solid @FOCUS_RING@; padding: 0 9px; }
QStatusBar::item { border: none; }
QDialog#command-palette { background: @SURFACE_1@; border: 1px solid @BORDER_STRONG@; border-radius: @R_LG@; }
QListWidget#command-list { border: none; background: transparent; }
QListWidget#command-list::item { padding: 0 8px; border-radius: @R_SM@; color: @TEXT_PRIMARY@; }
QListWidget#command-list::item:selected { background: @SELECTION@; }
QListWidget#command-list::item:disabled { color: @TEXT_DISABLED@; }

/* ============ signals panel: tree table (message group rows, signal rows, detail rows) ============ */
QFrame#signals-panel { background: @SURFACE_1@; }
QWidget#msg-row { background: @SURFACE_2@; border-bottom: 1px solid @BORDER@; }
QLabel#msg-title { font-size: @FS_TITLE@; font-weight: @FW_TITLE@; }
QLabel#msg-data { color: @TEXT_SECONDARY@; padding-left: 6px; }
QWidget#msg-row QComboBox, QWidget#msg-row QSpinBox { min-height: 26px; max-height: 26px; }
QToolButton#msg-btn { min-height: 26px; max-height: 26px; padding: 0 8px; border: 1px solid @BORDER_STRONG@; }
QToolButton#msg-btn:hover { background: @SURFACE_3@; }
QTreeWidget#signal-table::item { padding: 3px 6px; }

/* ============ menus / status / tooltips ============ */
QMenuBar { background: @SURFACE_0@; border-bottom: 1px solid @BORDER@; }
QMenuBar#app-menubar { background: @SURFACE_0@; border-bottom: 1px solid @BORDER@; spacing: 2px; padding-left: 6px; }
/* custom title bar: icon · menus · title · window buttons in one themed row */
QWidget#title-bar { background: @SURFACE_0@; border-bottom: 1px solid @BORDER@; }
QWidget#title-bar QMenuBar#app-menubar { background: transparent; border: none; padding-left: 0; }
QLabel#title-text { color: @TEXT_SECONDARY@; }
QToolButton#win-btn, QToolButton#win-close { border: none; border-radius: 0; background: transparent; padding: 0; }
QToolButton#win-btn:hover { background: @SURFACE_3@; }
QToolButton#win-close:hover { background: #C42B1C; }
QToolButton#win-close:pressed { background: #A1251B; }
QMenuBar#app-menubar::item { padding: 5px 10px; margin: 1px 0; border-radius: @R_SM@; color: @TEXT_PRIMARY@; background: transparent; }
QMenuBar#app-menubar::item:selected { background: @SURFACE_3@; color: @TEXT_PRIMARY@; }
QMenuBar#app-menubar::item:pressed { background: @ACCENT_SOFT@; color: @TEXT_PRIMARY@; }
QMenu { background: @SURFACE_1@; border: 1px solid @BORDER_STRONG@; border-radius: @R_LG@; padding: 4px; }
QMenu::item { padding: 6px 28px 6px 12px; border-radius: @R_SM@; }
QMenu::item:selected { background: @SELECTION@; }
QMenu::item:disabled { color: @TEXT_DISABLED@; }
QMenu::separator { height: 1px; background: @BORDER@; margin: 4px 8px; }
QStatusBar { background: @SURFACE_0@; color: @TEXT_SECONDARY@; border-top: 1px solid @BORDER@; }
QToolTip { color: @TEXT_PRIMARY@; border: 1px solid @BORDER_STRONG@; border-radius: @R_SM@; padding: 4px 8px; background: @SURFACE_3@; }
"""


def render(palette: dict[str, str]) -> str:
    """Substitute fonts, sizes and colour tokens (image tokens are left for build_qss)."""
    qss = TEMPLATE.replace("@FONT_UI@", FONT_UI)
    values = {**SIZES, **palette}
    for k in sorted(values, key=len, reverse=True):           # longest first: ACCENT_STRONG before ACCENT
        qss = qss.replace(f"@{k}@", values[k])
    return qss


DARK_QSS = render(DARK)
LIGHT_QSS = render(LIGHT)


def build_qss(palette: dict[str, str], assets: dict[str, str]) -> str:
    """Final stylesheet: palette + painted image assets (arrow / check PNG paths)."""
    qss = render(palette)
    for k, v in assets.items():
        qss = qss.replace(f"@{k.upper()}@", v)
    return qss
