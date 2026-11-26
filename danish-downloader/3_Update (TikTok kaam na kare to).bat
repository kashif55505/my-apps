@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo yt-dlp update ho raha hai (TikTok/Instagram apna system badalte rehte hain)...
python -m pip install --upgrade "yt-dlp[default]"
echo Ho gaya. Ab software dobara kholein.
pause
