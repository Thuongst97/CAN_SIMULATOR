# PyInstaller spec (one-folder build -> dist/AutoSimStudio/). Run via scripts/build.ps1
# Only the Qt modules the app uses are shipped (Core, Gui, Widgets); the QML / Quick / virtual
# keyboard / PDF / network parts and the translations that Qt's plugins drag in are left out, and so is web mode.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("autosim", excludes=["web/**"])     # package data: ui/assets (app icon)
hidden = [m for m in collect_submodules("autosim") if not m.startswith("autosim.web")]
hidden += collect_submodules("can.interfaces")

a = Analysis(
    ["../src/autosim/__main__.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "numpy", "aiohttp", "autosim.web",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtOpenGL",
              "PySide6.QtOpenGLWidgets", "PySide6.QtTest", "PySide6.QtSvg"],
)
DROP = ("Qt6Qml", "Qt6Quick", "Qt6VirtualKeyboard", "Qt6Pdf", "Qt6Network", "Qt6Wayland", "Qt6WlShell", "Qt6EglFS",
        "platforminputcontexts", "virtualkeyboard", "qpdf", "/tls/", "/qmltooling/", "wayland", "translations",
        "networkinformation", "egldeviceintegrations")


def keep(entry):
    name = entry[0].replace("\\", "/")
    return not any(d in name for d in DROP)


a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AutoSimStudio", console=False,
          icon="autosim.ico")                        # tools/ui/make_app_icon.py
coll = COLLECT(exe, a.binaries, a.datas, name="AutoSimStudio")
