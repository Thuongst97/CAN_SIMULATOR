"""Design tokens: the single source of every colour, size and duration in the UI.

Colour tokens exist once per theme (DARK_COLORS / LIGHT_COLORS, identical keys). Everything else (spacing,
radii, type scale, control heights, motion) is theme independent. The QSS template (styles/themes.py), the Qt
palette, item models, custom-painted widgets and pyqtgraph all read from here, so the themes cannot drift.

Rules
  * ACCENT (indigo) is the only colour for primary actions, selection, focus and the active tab.
    ACCENT_STRONG is its filled-background variant (white ON_ACCENT text on it passes 4.5:1).
  * ERROR is reserved for errors and destructive actions. Payload bytes and IDs use neutral text colours.
  * RX / TX are cyan / orange (distinguishable with the common colour-vision deficiencies) and are always
    accompanied by the "Rx" / "Tx" label.
  * WCAG AA is enforced by tests/test_theme_contrast.py (text 4.5:1, focus ring 3:1).
Sizes are integer pixels (Qt font sizes must be integers); they are device-independent, so Windows display
scaling (125 %, 150 %) scales them uniformly.
"""
from __future__ import annotations

# ---- colour ----------------------------------------------------------------------------------------
DARK_COLORS: dict[str, str] = {
    # graphite / slate dark (cool tint, no pure greys, no pure black)
    # surfaces: 0 = window and gaps between docks, 1 = panels / tables / trace, 2 = panel headers / toolbars,
    #           3 = hover / popovers / menus; inputs are recessed (INPUT_BG) inside panels
    "SURFACE_0": "#1B1D22",
    "SURFACE_1": "#212329",
    "SURFACE_2": "#272A31",
    "SURFACE_3": "#30333B",
    "INPUT_BG": "#1B1D22",
    "BORDER": "#33363E",
    "BORDER_STRONG": "#41454F",
    "BORDER_HOVER": "#5A5F6B",
    # text
    "TEXT_PRIMARY": "#E6E8EC",
    "TEXT_SECONDARY": "#B4B8C4",
    "TEXT_MUTED": "#9EA2B0",
    "TEXT_DISABLED": "#656978",
    # accent
    "ACCENT": "#8C91FA",                      # text, indicators, focus ring, slider
    "ACCENT_STRONG": "#4F46E5",               # filled primary buttons, checked boxes
    "ACCENT_STRONG_HOVER": "#5B53EA",
    "ACCENT_STRONG_PRESSED": "#4338CA",
    "ON_ACCENT": "#FFFFFF",
    "ACCENT_SOFT": "rgba(140, 145, 250, 0.18)",
    "FOCUS_RING": "#8C91FA",                  # 2 px, solid
    "SELECTION": "rgba(140, 145, 250, 0.18)", # accent-soft on surface-1 (text stays TEXT_PRIMARY)
    # semantic
    "SUCCESS": "#4ADE80",
    "WARNING": "#FACC15",
    "ERROR": "#F87171",
    "INFO": "#60A5FA",
    "SUCCESS_SOFT": "rgba(74, 222, 128, 0.14)",
    "WARNING_SOFT": "rgba(250, 204, 21, 0.14)",
    "ERROR_SOFT": "rgba(248, 113, 113, 0.14)",
    "INFO_SOFT": "rgba(96, 165, 250, 0.14)",
    "ERROR_STRONG": "#B91C1C",                # filled destructive (hover / confirm), white text
    # bus
    "RX": "#22D3EE",
    "TX": "#FB923C",
    "BYTE_CHANGED": "rgba(140, 145, 250, 0.24)",
    # tables
    "ROW_ALT": "#24262C",
    "ROW_HOVER": "#2A2D34",
    "GRID": "#2C2F37",
    # scrollbars
    "SCROLL": "rgba(158, 162, 176, 0.30)",
    "SCROLL_HOVER": "rgba(158, 162, 176, 0.55)",
    "SCROLL_PRESSED": "rgba(158, 162, 176, 0.75)",
    # plot series (ordered for maximum contrast between neighbours)
    "SERIES": "#60A5FA,#4ADE80,#FACC15,#F472B6,#A78BFA,#2DD4BF,#FB923C,#E5E7EB",
}

LIGHT_COLORS: dict[str, str] = {
    "SURFACE_0": "#F4F6F9",
    "SURFACE_1": "#FFFFFF",
    "SURFACE_2": "#F8FAFC",
    "SURFACE_3": "#EEF2F6",
    "INPUT_BG": "#F8FAFC",                    # light theme unchanged: inputs as before
    "BORDER": "#E2E8F0",
    "BORDER_STRONG": "#CBD5E1",
    "BORDER_HOVER": "#94A3B8",
    "TEXT_PRIMARY": "#0F172A",
    "TEXT_SECONDARY": "#475569",
    "TEXT_MUTED": "#5B6779",
    "TEXT_DISABLED": "#A0AEC0",
    "ACCENT": "#4F46E5",
    "ACCENT_STRONG": "#4F46E5",
    "ACCENT_STRONG_HOVER": "#4338CA",
    "ACCENT_STRONG_PRESSED": "#3730A3",
    "ON_ACCENT": "#FFFFFF",
    "ACCENT_SOFT": "rgba(79, 70, 229, 0.10)",
    "FOCUS_RING": "#4F46E5",
    "SELECTION": "#E0E7FF",
    "SUCCESS": "#15803D",
    "WARNING": "#A16207",
    "ERROR": "#DC2626",
    "INFO": "#2563EB",
    "SUCCESS_SOFT": "rgba(21, 128, 61, 0.10)",
    "WARNING_SOFT": "rgba(161, 98, 7, 0.10)",
    "ERROR_SOFT": "rgba(220, 38, 38, 0.08)",
    "INFO_SOFT": "rgba(37, 99, 235, 0.10)",
    "ERROR_STRONG": "#B91C1C",
    "RX": "#0E7490",
    "TX": "#C2410C",
    "BYTE_CHANGED": "rgba(79, 70, 229, 0.14)",
    "ROW_ALT": "#F8FAFC",
    "ROW_HOVER": "#F1F5F9",
    "GRID": "#EEF2F6",
    "SCROLL": "rgba(91, 103, 121, 0.35)",
    "SCROLL_HOVER": "rgba(91, 103, 121, 0.60)",
    "SCROLL_PRESSED": "rgba(91, 103, 121, 0.80)",
    "SERIES": "#2563EB,#15803D,#B45309,#DB2777,#7C3AED,#0F766E,#C2410C,#334155",
}

# ---- spacing / shape ----------------------------------------------------------------------------------
SPACE = (0, 4, 8, 12, 16, 24, 32)          # SPACE[1] = 4 px ... the 4 px grid
RADIUS_SM, RADIUS_MD, RADIUS_LG = 4, 6, 8  # badges & chips / buttons & inputs / cards, panels, popovers
CONTROL_H = 32                             # default control height
CONTROL_H_COMPACT = 28
ROW_H = 24                                 # tables and trees
TOOLBAR_H = 48                             # panel toolbars: 32 px controls + 8 px above and below
ROW_H_DENSE = 22                           # bus trace
BORDER_W = 1
FOCUS_W = 2

# ---- typography (px, weight) ------------------------------------------------------------------------------
# One family everywhere (Inter, with tabular figures so numbers line up in columns) and one text size:
# every label, input, table, trace cell, status segment and log line is 13 px; headers are 14 px semibold;
# the app title is 18 px.
FONT_UI_FAMILIES = ["Inter", "Segoe UI", "Roboto", "Arial"]
TYPE = {
    "body": (13, 400),
    "body_strong": (13, 600),
    "caption": (13, 400),          # field labels: body size, secondary colour
    "panel_title": (14, 600),      # dock / panel / section headers
    "section": (14, 600),
    "app_title": (18, 700),
}

# ---- motion (ms, ease-out) ---------------------------------------------------------------------------------
MOTION_FAST = 120          # hover, toggles
MOTION_BASE = 160          # drawers, sidebar collapse, toasts
MOTION_SLOW = 180          # dock moves
FLASH_MS = 600             # trace update highlight (computed from timestamps at paint time)


def colors(dark: bool) -> dict[str, str]:
    return DARK_COLORS if dark else LIGHT_COLORS


def series(dark: bool) -> list[str]:
    return colors(dark)["SERIES"].split(",")
