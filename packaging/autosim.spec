# PyInstaller spec (one-folder build -> dist/AutoSimStudio/). Run via scripts/build.ps1
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block = []
datas = collect_data_files("autosim")        # includes ui/styles/dark.qss
datas += collect_data_files("grpc_tools")      # bundled well-known .proto includes for runtime protoc
datas += collect_data_files("pyqtgraph")
hidden = collect_submodules("autosim") + collect_submodules("can.interfaces")
hidden += ["grpc_tools.protoc", "psutil"]

a = Analysis(
    ["../src/autosim/__main__.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AutoSimStudio", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="AutoSimStudio")
