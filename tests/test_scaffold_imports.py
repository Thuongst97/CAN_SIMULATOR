import importlib
import pkgutil

import autosim


def test_all_modules_import():
    for m in pkgutil.walk_packages(autosim.__path__, "autosim."):
        if ".ui." in m.name or m.name.endswith((".app", "__main__")):
            continue  # need PySide6
        importlib.import_module(m.name)
