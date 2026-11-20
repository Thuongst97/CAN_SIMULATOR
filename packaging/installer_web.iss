; Inno Setup script of the web-mode-only installer (smaller download, no desktop app).
; Build: iscc /DAppVersion=1.0.0 packaging\installer_web.iss  (done by scripts/build.ps1)
;   AutoSim Studio Web, served to a browser (also the Test API for test systems) -> {app}\AutoSimWeb.exe
; The full installer (installer.iss) offers the desktop app and web mode together; this one can be installed
; next to it (own AppId and folder).
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{0B7E2C51-6A3D-4C1F-9E55-2F4A8D1B7C93}
AppName=AutoSim Studio Web
AppVersion={#AppVersion}
AppPublisher=AutoSim
DefaultDirName={autopf}\AutoSim Studio Web
DefaultGroupName=AutoSim Studio Web
OutputDir=..\dist\installer
OutputBaseFilename=AutoSimStudioWeb-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\AutoSimWeb.exe
SetupIconFile=autosim.ico

[Files]
Source: "..\dist\AutoSimWeb\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\docs\TEST_API_GUIDE.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\WEB_MODE.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\AutoSim Studio Web"; Filename: "{app}\AutoSimWeb.exe"; WorkingDir: "{app}"
Name: "{group}\AutoSim Studio Web (network access)"; Filename: "{app}\AutoSimWeb.exe"; Parameters: "--host 0.0.0.0"; WorkingDir: "{app}"
Name: "{group}\Test API guide"; Filename: "{app}\docs\TEST_API_GUIDE.md"
Name: "{group}\Uninstall AutoSim Studio Web"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AutoSim Studio Web"; Filename: "{app}\AutoSimWeb.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\AutoSimWeb.exe"; Description: "Launch AutoSim Studio Web"; Flags: nowait postinstall skipifsilent
