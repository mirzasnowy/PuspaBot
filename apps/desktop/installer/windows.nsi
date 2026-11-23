; PuspaBot native desktop installer (NSIS, Windows).
; Packages the release binary built by `cargo build --release`
; (apps/desktop/target/release/puspabot.exe). The Python backend is
; NOT bundled: it is downloaded at first run by the app itself.
; The database for v1 (SQLite port vs Postgres first-run) is still
; an open decision — this installer deliberately does not install
; either one.

!define APPNAME "PuspaBot"
!define APPVERSION "0.1.0"
!define EXE "puspabot.exe"

OutFile "${OUTDIR}\PuspaBot-Setup.exe"
InstallDir "$PROGRAMFILES\${APPNAME}"
RequestExecutionLevel admin

Section "Install"
  SetOutPath "$INSTDIR"
  File "target\release\${EXE}"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${EXE}"
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXE}"
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\uninstall.exe"
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
  RMDir "$INSTDIR"
SectionEnd
