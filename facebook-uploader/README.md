# Facebook Uploader

Multi-account Facebook posting (Reels / Video / Photo), warm-up/active,
groups, LDPlayer automation, captcha OCR, 2FA, remote control (Telegram +
Android app). English / Roman Urdu.

## Install (Windows)
1. Download the latest **Facebook Uploader Setup** from the
   [Releases](https://github.com/kashif55505/my-apps/releases) (asset
   `Facebook_Uploader_Setup-vX.exe`).
2. Run it → installs to your user folder (no admin needed) with a desktop shortcut.

## Updates — one click, no re-download
Open the app → **Settings → Update → "Check for updates"**.
If a newer version exists it downloads and swaps itself, then restarts.
No need to download or reinstall manually.

## Run from source
```
pip install playwright pystray pillow pytesseract ixbrowser-local-api
python -m playwright install chromium   # (skip if only using ixBrowser)
python fb_uploader.py
```
Free captcha OCR needs Tesseract (one-time): https://github.com/UB-Mannheim/tesseract/wiki

## Android companion
`AndroidApp/` — open in Android Studio, build APK. Remote-controls the PC app
and can post via the official Graph API. See `AndroidApp/HOW_TO_BUILD_APK.txt`.

## Build / release
Pushing changes under `facebook-uploader/` triggers the GitHub Action
(`.github/workflows/facebook-uploader.yml`) which builds the Windows `.exe`
+ installer on a Windows runner and publishes a `fbu-vX` release. The app's
updater reads the newest `fbu-v*` release.
