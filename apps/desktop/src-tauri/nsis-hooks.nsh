; Keep the desktop launcher explicit for the beta installer. Tauri creates the
; application files first; this hook adds a predictable shortcut for users.
!macro NSIS_HOOK_POSTINSTALL
  CreateShortCut "$DESKTOP\Smara Desktop.lnk" "$INSTDIR\smara-desktop.exe"
!macroend

; Remove only the shortcut created above when the user uninstalls Smara.
!macro NSIS_HOOK_PREUNINSTALL
  Delete "$DESKTOP\Smara Desktop.lnk"
!macroend
