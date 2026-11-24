@echo off
cd /d "%~dp0"
python -c "import webview" 2>nul && (start "" pythonw gui.py & exit /b)
echo The app window needs: pip install -r requirements.txt
echo Starting the text menu instead...
python phone_recovery.py
if errorlevel 1 pause
