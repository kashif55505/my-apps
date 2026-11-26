@echo off
chcp 65001 >nul
title Danish Downloader - Install
cd /d "%~dp0"
echo ============================================
echo   Danish Downloader - Install ho raha hai
echo ============================================
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo [!] Python nahi mila.
  echo     https://www.python.org/downloads/ se Python install karein
  echo     aur install ke waqt "Add python.exe to PATH" par tick zaroor lagayein.
  echo     Phir ye file dobara chalayein.
  start https://www.python.org/downloads/
  pause
  exit /b
)
python -m pip install --upgrade pip
python -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo [!] Install mein masla aaya. Internet check karke dobara chalayein.
  pause
  exit /b
)
echo.
echo  Install mukammal! Ab "2_Danish Downloader chalayein.bat" par double-click karein.
pause
