; Inno Setup script. Build: iscc /DAppVersion=0.1.0 packaging\installer.iss  (done by scripts/build.ps1)
; One installer, two components (both selected by default):
;   desktop  AutoSim Studio, the desktop app            -> {app}\AutoSimStudio.exe       (dist\AutoSimStudio)
;   web      AutoSim Studio Web, served to a browser    -> {app}\web\AutoSimWeb.exe      (dist\AutoSimWeb)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{FEA2B79A-309B-4FE6-A37F-D168F6E9C5E1}
AppName=AutoSim Studio
AppVersion={#AppVersion}
AppPublisher=AutoSim
DefaultDirName={autopf}\AutoSim Studio
DefaultGroupName=AutoSim Studio
OutputDir=..\dist\installer
OutputBaseFilename=AutoSimStudio-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\AutoSimStudio.exe
SetupIconFile=autosim.ico

[Types]
Name: "full"; Description: "Desktop app and web mode"
Name: "desktop"; Description: "Desktop app only"
Name: "web"; Description: "Web mode only"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "desktop"; Description: "AutoSim Studio (desktop app)"; Types: full desktop custom
Name: "web"; Description: "AutoSim Studio Web (use it in a browser, also from other PCs)"; Types: full web custom

[Files]
Source: "..\dist\AutoSimStudio\*"; DestDir: "{app}"; Components: desktop; Flags: recursesubdirs ignoreversion
Source: "..\dist\AutoSimWeb\*"; DestDir: "{app}\web"; Components: web; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AutoSim Studio"; Filename: "{app}\AutoSimStudio.exe"; Components: desktop
Name: "{group}\AutoSim Studio Web"; Filename: "{app}\web\AutoSimWeb.exe"; WorkingDir: "{app}\web"; Components: web
Name: "{group}\AutoSim Studio Web (network access)"; Filename: "{app}\web\AutoSimWeb.exe"; Parameters: "--host 0.0.0.0"; WorkingDir: "{app}\web"; Components: web
Name: "{autodesktop}\AutoSim Studio"; Filename: "{app}\AutoSimStudio.exe"; Tasks: desktopicon; Components: desktop
Name: "{autodesktop}\AutoSim Studio Web"; Filename: "{app}\web\AutoSimWeb.exe"; WorkingDir: "{app}\web"; Tasks: desktopicon; Components: web

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcuts"; Flags: unchecked

[Run]
Filename: "{app}\AutoSimStudio.exe"; Description: "Launch AutoSim Studio"; Flags: nowait postinstall skipifsilent; Components: desktop
Filename: "{app}\web\AutoSimWeb.exe"; Description: "Launch AutoSim Studio Web"; Flags: nowait postinstall skipifsilent unchecked; Components: web
