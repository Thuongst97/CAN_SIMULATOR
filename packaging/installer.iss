; Inno Setup script. Build: iscc /DAppVersion=0.1.0 packaging\installer.iss  (done by scripts/build.ps1)
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

[Files]
Source: "..\dist\AutoSimStudio\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AutoSim Studio"; Filename: "{app}\AutoSimStudio.exe"
Name: "{autodesktop}\AutoSim Studio"; Filename: "{app}\AutoSimStudio.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\AutoSimStudio.exe"; Description: "Launch AutoSim Studio"; Flags: nowait postinstall skipifsilent
