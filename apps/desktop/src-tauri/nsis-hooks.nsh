; The PC helper (`resources\smara-desktop.exe --browser-bridge`) keeps running after Smara quits, by design, and its
; Playwright driver (`resources\_internal\playwright\driver\node.exe`) holds files in the install folder. Stop that
; helper and every process it started before files are replaced or removed, or the update cannot overwrite them.
; Only processes running from this install folder are touched; the main window is left to Tauri's own running-app check.
; The background worker (`--native-goals`: phone tasks, routines) and everything it started are stopped the same way.
; After an update the app starts the helper and the worker again (and the helper's watchdog would within a few minutes).
!macro SMARA_STOP_HELPER
  Push $0
  System::Call 'Kernel32::SetEnvironmentVariable(t "SMARA_INSTDIR", t "$INSTDIR\")'
  nsExec::Exec `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$$d=$$env:SMARA_INSTDIR; $$all=@(Get-CimInstance Win32_Process); $$ids=@($$all | Where-Object { ($$_.CommandLine -like '*--browser-bridge*' -or $$_.CommandLine -like '*--native-goals*') -and $$_.ExecutablePath -and $$_.ExecutablePath.StartsWith($$d, 'OrdinalIgnoreCase') } | ForEach-Object { $$_.ProcessId }); for ($$i=0; $$i -lt $$ids.Count; $$i++) { $$ids += @($$all | Where-Object { $$_.ParentProcessId -eq $$ids[$$i] } | ForEach-Object { $$_.ProcessId }) }; $$ids | ForEach-Object { Stop-Process -Id $$_ -Force -ErrorAction SilentlyContinue }"`
  Pop $0
  Pop $0
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro SMARA_STOP_HELPER
!macroend

; Keep the desktop launcher explicit for the beta installer. Tauri creates the
; application files first; this hook adds a predictable shortcut for users.
!macro NSIS_HOOK_POSTINSTALL
  CreateShortCut "$DESKTOP\Smara Desktop.lnk" "$INSTDIR\smara-desktop.exe"
!macroend

; Remove only the shortcut created above when the user uninstalls Smara, and stop the helper so its files can go.
!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro SMARA_STOP_HELPER
  Delete "$DESKTOP\Smara Desktop.lnk"
!macroend
