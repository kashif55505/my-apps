@echo off
title My Vault
cd /d "%~dp0"
set VAULT_SUPERVISED=1
:run
node server.js
if %errorlevel%==3 goto run
pause
