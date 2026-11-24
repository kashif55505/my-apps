@echo off
REM ============================================================
REM  Facebook Uploader -> Setup.exe (installer) builder
REM  Produces:  "Facebook Uploader Setup.exe"
REM  Run that Setup on any PC to install the software
REM  (into Program Files, with a desktop shortcut, next-next style).
REM
REM  Keep this in the SAME folder as:
REM     fb_uploader.py , installer.py , app_icon.ico
REM  Requirement: Python installed (with "Add Python to PATH").
REM ============================================================

cd /d "%~dp0"

REM --- Find a real Python (prefer the 'py' launcher, then 'python') ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY goto :nopython

echo Using Python:
%PY% --version

echo.
echo [1/4] Installing required packages...
%PY% -m pip install --upgrade pip
%PY% -m pip install pyinstaller playwright ixbrowser-local-api pystray pillow pytesseract
if errorlevel 1 goto :error

echo.
echo [2/4] Building the app (Facebook Uploader.exe)...
%PY% -m PyInstaller --onefile --noconsole --name "Facebook Uploader" --icon app_icon.ico ^
  --collect-all playwright ^
  --hidden-import ixbrowser_local_api --collect-all ixbrowser_local_api ^
  --hidden-import pystray --hidden-import PIL --hidden-import pytesseract ^
  fb_uploader.py
if errorlevel 1 goto :error
if not exist "dist\Facebook Uploader.exe" goto :error
copy /Y "dist\Facebook Uploader.exe" "Facebook Uploader.exe" >nul

echo.
echo [3/4] Building the Setup (installer)...
%PY% -m PyInstaller --onefile --noconsole --name "Facebook Uploader Setup" --icon app_icon.ico ^
  --add-data "Facebook Uploader.exe;." ^
  --add-data "app_icon.ico;." ^
  installer.py
if errorlevel 1 goto :error
if not exist "dist\Facebook Uploader Setup.exe" goto :error
move /Y "dist\Facebook Uploader Setup.exe" "Facebook Uploader Setup.exe" >nul

echo.
echo [4/4] Cleaning up...
rmdir /S /Q build >nul 2>&1
rmdir /S /Q dist >nul 2>&1
del /Q "Facebook Uploader.spec" >nul 2>&1
del /Q "Facebook Uploader Setup.spec" >nul 2>&1
del /Q "Facebook Uploader.exe" >nul 2>&1

echo.
echo ============================================================
echo DONE!
echo Installer created:   "Facebook Uploader Setup.exe"
echo.
echo Copy this one file to any Windows PC and run it:
echo   - click Install
echo   - default location C:\Program Files (change it with Browse)
echo   - a desktop shortcut with the logo will be created
echo ============================================================
echo.
pause
exit /b 0

:nopython
echo.
echo ============================================================
echo  PYTHON NOT FOUND
echo ============================================================
echo  This PC does not have Python installed (the message about the
echo  "Microsoft Store" is Windows' fake python alias).
echo.
echo  Fix (one time):
echo   1. Go to:  https://www.python.org/downloads/
echo   2. Download Python 3 and run the installer.
echo   3. IMPORTANT: tick "Add Python to PATH" at the bottom, then Install.
echo   4. Close this window and run build_setup.bat again.
echo.
echo  (Optional) If it still says Store: Windows Settings -> Apps ->
echo   Advanced app settings -> App execution aliases -> turn OFF the
echo   two "python" entries.
echo ============================================================
echo.
pause
exit /b 1

:error
echo.
echo *** Something went wrong. See the error above. ***
echo *** If you see "python is not recognized", install Python
echo *** from python.org and tick "Add Python to PATH". ***
echo.
pause
exit /b 1
