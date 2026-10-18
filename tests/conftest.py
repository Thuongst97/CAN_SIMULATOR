import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # GUI tests run headless
# the offscreen platform has its own (empty) font folder: give it the Windows fonts so the UI font (Segoe UI)
# resolves exactly as in the real app
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
