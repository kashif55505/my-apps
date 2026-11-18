# TezDM - Fast Download Manager

No Python required. The installer installs directly on any 64-bit Windows 10/11 PC.

## Install
1. Download the latest installer from the [TezDM latest release](../../../releases/tag/tezdm-latest) (`TezDM_Setup.exe`) and run it and click **Next**, then **Finish**. No admin password is needed.
2. After Finish, Chrome's Extensions page and a setup guide open automatically, with the extension path already copied:
   - Turn on **Developer mode**
   - Click **Load unpacked**
   - In the folder window, click the address bar, press `Ctrl+V`, press Enter, then click **Select Folder**
   - The guide window confirms: "Extension installed!"
3. Done. The extension updates itself whenever TezDM is updated. TezDM now starts in the background every time your PC starts.

> If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. This happens with any new unsigned software.
> If Chrome shows a "Disable developer mode extensions" message, close it with X (do not click Disable).

## Features
- Every file downloads over **16 connections** (up to 32 in Options). When a connection finishes its part, it takes over half of the largest remaining part (dynamic segmentation)
- **Pause / Resume**, even after a PC restart. Auto-retry when the connection drops
- Captures **every Chrome download** (zip, exe, rar, pdf, iso...)
- **Download button on every video**: YouTube, Facebook, Instagram, TikTok, X, Vimeo, and any site with m3u8 / mp4 streams. Drag the button anywhere, inside or outside the video; its place is remembered per website (double-click its dots, or use the extension popup, to reset it)
- **Quality menu on the video's Download button**, and in the download window: choose video quality before downloading (4K, 1080p, 720p, 480p... or audio-only MP3), with the estimated size of each
- **Progress window** for every download: downloaded size, percentage, transfer rate, time left, resume capability, and per-connection progress
- **Download complete** window with Open / Open folder
- Main window with categories (Video, Music, Compressed, Programs, Documents...), toolbar, right-click menu, queue, and options
- Closing the main window keeps TezDM running in the background. Reopen it from the desktop icon, the Start menu, or the extension popup's "Open TezDM" button. Use **Exit** to close it completely

## Video tools on first run
On first run TezDM downloads **yt-dlp**, **ffmpeg** and **Deno** (about 200 MB, one time only; Deno is the JavaScript runtime yt-dlp needs for YouTube). They are required for video downloads. yt-dlp updates itself daily so downloads keep working when YouTube changes.

## About speed
Multiple connections greatly increase speed on servers that limit speed per connection. Speed cannot exceed your internet plan. Some premium file hosts (Mega etc.) limit free users on their own servers; no software can bypass that. DRM-protected streams (Netflix, Prime) cannot be downloaded.

## Releases (automatic)
Every push that changes the `tezdm/` folder builds TezDM on GitHub Actions and publishes:
- release `tezdm-latest`: always the newest `TezDM_Setup.exe` and `TezDM_Chrome_Extension.zip`
- release `tezdm-vX.Y.Z`: one per version, kept as history

Before every release, download-integrity tests (`engine_test.go`) run on a Windows machine; if any downloaded file comes out different from the original, nothing is released.

To publish a new version, change the number in `tezdm/VERSION` (for example `2.3.0` to `2.4.0`) and push.

## Build from source (developers)
- **Windows:** install Go and NSIS, then run `build.bat`
- **Linux/WSL:** run `./build.sh` (the version comes from the `VERSION` file)
- Code: `engine.go` (download engine), `video.go` (yt-dlp), `server.go` (API), `web/` (app UI), `extension/` (Chrome extension, embedded in the exe), `installer/tezdm.nsi`

## Where data is stored
- App: `%LOCALAPPDATA%\Programs\TezDM`
- Settings, download list, video tools, extension: `%LOCALAPPDATA%\TezDM`
- Downloads: `Downloads\TezDM` (change it in Options)
