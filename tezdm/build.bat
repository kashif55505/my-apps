@echo off
REM TezDM build on Windows: install Go (https://go.dev/dl) and NSIS (https://nsis.sourceforge.io)
REM Note: the exe icon/version resource is only added by build.sh (Linux/WSL/GitHub).
cd /d "%~dp0"
set /p V=<VERSION
set GOOS=windows
set GOARCH=amd64
set CGO_ENABLED=0
go build -trimpath -ldflags "-H=windowsgui -s -w -X main.Version=%V%" -o dist\TezDM.exe . || goto :err
cd installer
"%ProgramFiles(x86)%\NSIS\makensis.exe" /DVERSION=%V% tezdm.nsi || goto :err
echo Built: dist\TezDM_Setup_%V%.exe
pause
exit /b 0
:err
echo Build failed, see the error above.
pause
