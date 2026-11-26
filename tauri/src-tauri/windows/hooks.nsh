; AutoSim Studio installer hooks (Tauri NSIS).
;
; The backend (AutoSimWeb.exe) listens on every network interface (0.0.0.0:6789) so test systems on other PCs can
; use the Test API. Without a firewall rule, Windows shows a "Windows Security Alert" window the first time it
; listens, a second window next to the app. The installer runs with administrator rights (perMachine), so it adds
; the rule itself: inbound TCP to AutoSimWeb.exe (every network profile). Other machines still need the
; access token. The uninstaller removes the rule.

!define AUTOSIM_FW_RULE "AutoSim Studio backend"

!macro NSIS_HOOK_POSTINSTALL
  nsExec::Exec 'netsh advfirewall firewall delete rule name="${AUTOSIM_FW_RULE}"'
  Pop $0
  nsExec::Exec 'netsh advfirewall firewall add rule name="${AUTOSIM_FW_RULE}" dir=in action=allow protocol=TCP profile=any enable=yes program="$INSTDIR\backend\AutoSimWeb.exe"'
  Pop $0
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  nsExec::Exec 'netsh advfirewall firewall delete rule name="${AUTOSIM_FW_RULE}"'
  Pop $0
!macroend
