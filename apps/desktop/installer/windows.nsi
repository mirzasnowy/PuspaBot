; PuspaBot native desktop installer (NSIS, Windows).
; Packages the release binary built by `cargo build --release`
; (apps/desktop/target/release/puspabot.exe). The Python backend is
; NOT bundled: it is downloaded at first run by the app itself.
; v1 database: the full SQLite port (Nero, 23 Nov ~09:52 WIB);
; the installer installs no database — the backend creates its
; SQLite file on first run.
; NOTE: makensis resolves relative paths from the directory of
; this script (apps/desktop/installer/), NOT from the directory
; makensis was started in — so the paths below are relative to
; this script's own directory.

!define APPNAME "PuspaBot"
!define APPVERSION "0.1.0"
!define EXE "puspabot.exe"

; OUTDIR comes from the CI call (-DOUTDIR=...); the fallback keeps
; a local makensis run working without the define.
!ifndef OUTDIR
  !define OUTDIR "installer-out"
!endif

OutFile "${OUTDIR}/PuspaBot-Setup.exe"
InstallDir "$PROGRAMFILES\${APPNAME}"
RequestExecutionLevel admin
Icon "puspa.ico"
UninstallIcon "puspa.ico"

Section "Install"
  SetOutPath "$INSTDIR"
  File "../target/release/${EXE}"
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
