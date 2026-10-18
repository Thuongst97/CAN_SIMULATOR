"""The application's QSettings store.

QSettings("org", "app") always uses the native store (the Windows registry) and ignores
QSettings.setDefaultFormat(); this helper honours it, so tests can redirect every read and write to a
temporary INI folder (setDefaultFormat(IniFormat) + setPath(IniFormat, UserScope, tmp)) while the app
keeps using the registry location it always used."""
from __future__ import annotations

from PySide6.QtCore import QSettings

ORGANIZATION, APPLICATION = "AutoSim", "AutoSimStudio"


def app_settings() -> QSettings:
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, ORGANIZATION, APPLICATION)
