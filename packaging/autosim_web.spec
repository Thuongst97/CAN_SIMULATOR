# PyInstaller spec for web mode (one-folder build -> dist/AutoSimWeb/). Run via scripts/build.ps1
# A console program: it starts the server, opens the browser and shows the address; closing the window stops it.
# Web mode does not use Qt, so PySide6 / pyqtgraph are left out (a much smaller folder than the desktop app).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("autosim.web")      # static/: index.html, app.js, app.css
hidden = (collect_submodules("autosim.web") + collect_submodules("autosim.core") +
          collect_submodules("autosim.hardware") + collect_submodules("can.interfaces") +
          ["autosim.ui.shell.bus_load"])

a = Analysis(
    ["../src/autosim/web/__main__.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PySide6", "shiboken6", "pyqtgraph", "numpy", "autosim.ui.main_window", "autosim.app"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AutoSimWeb", console=True, icon="autosim.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="AutoSimWeb")
