@echo off
cd /d "%~dp0"
python phone_recovery.py
if errorlevel 1 pause
