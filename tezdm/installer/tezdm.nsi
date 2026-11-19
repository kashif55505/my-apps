; TezDM installer (NSIS) - no Python or other runtime needed
Unicode true
Target amd64-unicode
!include "MUI2.nsh"

!define APPNAME "TezDM"
!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\TezDM"

Name "${APPNAME} ${VERSION}"
OutFile "..\dist\TezDM_Setup_${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\TezDM"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "TezDM - Fast Download Manager"

!define MUI_ICON "..\icon.ico"
!define MUI_UNICON "..\icon.ico"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TITLE "Welcome to TezDM Setup"
!define MUI_WELCOMEPAGE_TEXT "TezDM is a fast download manager.$\r$\n$\r$\n- Downloads every file over multiple connections$\r$\n- Captures Chrome downloads automatically$\r$\n- Download button on every video (YouTube too)$\r$\n- Pause / Resume$\r$\n$\r$\nClick Next to continue."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_TITLE "TezDM has been installed"
!define MUI_FINISHPAGE_TEXT "When you click Finish, TezDM will start and open the Chrome extension setup (one time only, about 30 seconds).$\r$\n$\r$\nTezDM will now start automatically in the background whenever your PC starts."
!define MUI_FINISHPAGE_RUN "$INSTDIR\TezDM.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--setup-extension"
!define MUI_FINISHPAGE_RUN_TEXT "Run TezDM and set up the Chrome extension"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "TezDM"
VIAddVersionKey "FileDescription" "TezDM Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "TezDM"
VIAddVersionKey "LegalCopyright" "TezDM"

Section "Install"
  ; Close a running TezDM first
  nsExec::Exec 'taskkill /f /im TezDM.exe'
  Sleep 500

  SetOutPath "$INSTDIR"
  File "..\dist\TezDM.exe"
  File "..\icon.ico"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Shortcuts
  CreateDirectory "$SMPROGRAMS\TezDM"
  CreateShortCut "$SMPROGRAMS\TezDM\TezDM.lnk" "$INSTDIR\TezDM.exe" "" "$INSTDIR\icon.ico"
  CreateShortCut "$SMPROGRAMS\TezDM\Set up Chrome Extension.lnk" "$INSTDIR\TezDM.exe" "--setup-extension" "$INSTDIR\icon.ico"
  CreateShortCut "$SMPROGRAMS\TezDM\Uninstall TezDM.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\TezDM.lnk" "$INSTDIR\TezDM.exe" "" "$INSTDIR\icon.ico"

  ; "Start TezDM" links (tezdm://start) open the app
  WriteRegStr HKCU "Software\Classes\tezdm" "" "URL:TezDM"
  WriteRegStr HKCU "Software\Classes\tezdm" "URL Protocol" ""
  WriteRegStr HKCU "Software\Classes\tezdm\DefaultIcon" "" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "Software\Classes\tezdm\shell\open\command" "" '"$INSTDIR\TezDM.exe" --minimized "%1"'

  ; Start in the background with Windows
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "TezDM" '"$INSTDIR\TezDM.exe" --minimized'

  ; Entry in Control Panel > Programs
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "TezDM - Fast Download Manager"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "TezDM"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" 8000
  ; Always start TezDM again after installing or updating, so the extension and open windows keep working
  Exec '"$INSTDIR\TezDM.exe" --minimized'
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /f /im TezDM.exe'
  Sleep 500
  Delete "$INSTDIR\TezDM.exe"
  Delete "$INSTDIR\icon.ico"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  RMDir /r "$SMPROGRAMS\TezDM"
  Delete "$DESKTOP\TezDM.lnk"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "TezDM"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\Classes\tezdm"
  ; App data (video tools, extension, list). Downloaded files are kept.
  RMDir /r "$LOCALAPPDATA\TezDM"
SectionEnd
