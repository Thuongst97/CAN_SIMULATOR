"""WCAG 2.1 AA contrast of the design tokens, in both themes.

  * text tokens (primary / secondary / muted) on every surface        >= 4.5 : 1
  * coloured text (accent, semantic, Rx / Tx) on surface-1 and -2      >= 4.5 : 1
  * text on its filled backgrounds (ON_ACCENT, selection, error)       >= 4.5 : 1
  * focus ring against every surface                                    >= 3   : 1  (non-text, 1.4.11)
TEXT_DISABLED is exempt (WCAG 1.4.3 excludes inactive controls).
"""
import re

import pytest

from autosim.ui.styles.tokens import DARK_COLORS, LIGHT_COLORS

THEMES = {"dark": DARK_COLORS, "light": LIGHT_COLORS}
SURFACES = ("SURFACE_0", "SURFACE_1", "SURFACE_2", "SURFACE_3")
TEXT = ("TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED")
COLORED_TEXT = ("ACCENT", "SUCCESS", "WARNING", "ERROR", "INFO", "RX", "TX")


def rgb(value: str, over: str | None = None) -> tuple[float, float, float]:
    """'#RRGGBB' or 'rgba(r, g, b, a)' (alpha-composited over `over`) -> 0..255 floats."""
    if value.startswith("#"):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b, a = (float(x) for x in re.findall(r"[\d.]+", value))
    base = rgb(over) if over else (0.0, 0.0, 0.0)
    return tuple(a * c + (1 - a) * o for c, o in zip((r, g, b), base))


def luminance(c) -> float:
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg) -> float:
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def cases():
    for theme, t in THEMES.items():
        for txt in TEXT:
            for s in SURFACES:
                yield theme, txt, s, 4.5
        for txt in COLORED_TEXT:
            for s in ("SURFACE_1", "SURFACE_2"):
                yield theme, txt, s, 4.5
        for txt in ("SUCCESS", "WARNING", "TEXT_SECONDARY"):     # status bar text sits on surface-0
            yield theme, txt, "SURFACE_0", 4.5
        for txt in TEXT:                                          # inputs are recessed (INPUT_BG)
            yield theme, txt, "INPUT_BG", 4.5
        yield theme, "ON_ACCENT", "ACCENT_STRONG", 4.5
        yield theme, "ON_ACCENT", "ACCENT_STRONG_HOVER", 4.5
        yield theme, "ON_ACCENT", "ACCENT_STRONG_PRESSED", 4.5
        yield theme, "ON_ACCENT", "ERROR_STRONG", 4.5
        yield theme, "TEXT_PRIMARY", "SELECTION", 4.5
        yield theme, "TEXT_PRIMARY", "ROW_ALT", 4.5
        yield theme, "TEXT_PRIMARY", "ROW_HOVER", 4.5
        for s in SURFACES + ("INPUT_BG",):
            yield theme, "FOCUS_RING", s, 3.0


@pytest.mark.parametrize("theme, fg, bg, minimum", list(cases()),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_contrast(theme, fg, bg, minimum):
    t = THEMES[theme]
    background = rgb(t[bg], over=t["SURFACE_1"])
    value = ratio(rgb(t[fg], over=t[bg] if t[bg].startswith("#") else t["SURFACE_1"]), background)
    assert value >= minimum, f"{theme}: {fg} on {bg} = {value:.2f}:1 (needs {minimum}:1)"


def test_text_on_byte_highlight_and_tinted_rows():
    """Changed payload bytes stay readable on their highlight, over the plain and the alternate row colour."""
    for theme, t in THEMES.items():
        for row in ("SURFACE_1", "ROW_ALT"):
            bg = rgb(t["BYTE_CHANGED"], over=t[row])
            assert ratio(rgb(t["TEXT_PRIMARY"]), bg) >= 4.5, (theme, row)


def test_both_themes_define_the_same_tokens():
    assert set(DARK_COLORS) == set(LIGHT_COLORS)


def test_rx_and_tx_differ_for_colour_blind_viewers():
    """Simulated deuteranopia / protanopia (Machado 2009, severity 1): Rx and Tx must stay clearly apart."""
    mats = {
        "deuteranopia": ((0.367, 0.861, -0.228), (0.280, 0.673, 0.047), (-0.012, 0.043, 0.969)),
        "protanopia": ((0.152, 1.053, -0.205), (0.115, 0.786, 0.099), (-0.004, -0.048, 1.052)),
    }
    for theme, t in THEMES.items():
        a, b = rgb(t["RX"]), rgb(t["TX"])
        for name, m in mats.items():
            sa = [sum(m[i][j] * a[j] for j in range(3)) for i in range(3)]
            sb = [sum(m[i][j] * b[j] for j in range(3)) for i in range(3)]
            dist = sum((x - y) ** 2 for x, y in zip(sa, sb)) ** 0.5
            assert dist > 80, f"{theme} {name}: Rx/Tx too similar ({dist:.0f})"
