@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "Danish Downloader" --collect-all customtkinter --collect-all imageio_ffmpeg --collect-all yt_dlp danish_downloader.py
echo.
echo EXE "dist" folder mein ban gayi hai. Usay kisi bhi folder mein rakh kar chala sakte hain.
pause
