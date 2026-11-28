# My apps

All my apps in one place. Each app lives in its own folder and has its own automatic build: every change to an app's folder builds the app and publishes it under **Releases**, so the latest version can always be downloaded here. Android apps can also be installed and updated with [Obtainium](https://github.com/ImranR98/Obtainium).

| App | Folder | Releases titled | APK file |
|---|---|---|---|
| Mail – multi-account email with notifications | [`mail/`](mail/) | `Mail 1.0.<n>` | `Mail-v1.0.<n>.apk` |
| ReelPlayer – video player, downloader, YouTube | [`reelplayer/`](reelplayer/) | `ReelPlayer 1.<n>` | `ReelPlayer-v1.<n>.apk` |
| My Vault – private locked vault for photos, videos, files and notes, synced with your laptop | [`vault/`](vault/) | `Vault 1.<n>` | `MyVault-v1.<n>.apk` |

## Windows apps

| App | Folder | Download the latest version |
|---|---|---|
| TezDM – fast download manager with a Chrome extension (multi-connection downloads, pause/resume, video downloads with quality selection) | [`tezdm/`](tezdm/) | [**TezDM_Setup.exe**](https://github.com/kashif55505/tezdm/raw/main/TezDM_Setup.exe) (public, no sign-in) · [private release](../../releases/tag/tezdm-latest) |

## Windows / Mac / Linux tools (Python)

| App | Folder | Download the latest version |
|---|---|---|
| Phone Data Recovery – recover photos, videos, voice notes and documents from Android, iPhone backups, SD cards and HDD/SSD/NVMe; read your WhatsApp backups and Snapchat export (built-in updater) | [`phone-data-recovery/`](phone-data-recovery/) | [**PhoneDataRecovery.exe**](../../releases/download/phone-data-recovery-latest/PhoneDataRecovery.exe) · [Python zip](../../releases/download/phone-data-recovery-latest/PhoneDataRecovery.zip) · [release page](../../releases/tag/phone-data-recovery-latest) |

To publish a new Phone Data Recovery version, change the number in `phone-data-recovery/VERSION` and push.

The `tezdm-latest` release always holds the newest build with fixed file names, so the link above never changes. Each version is also kept as its own release (`TezDM 2.3.0`, ...). To publish a new TezDM version, change the number in `tezdm/VERSION` and push. Downloads need you to be signed in to GitHub, because the repository is private.

## Install and update with Obtainium

This repository is private, so Obtainium needs a read-only access token once.

1. **Create the token** on GitHub: Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate new token
   - Repository access: *Only select repositories* > `my-apps`
   - Permissions > Repository permissions > **Contents: Read-only**
   - Expiration: as long as you like (when it expires, make a new one and paste it in Obtainium again)
2. **Give it to Obtainium**: Obtainium > Settings > *Source-specific* > GitHub > **Personal access token** > paste.
3. **Add an app**: Obtainium > Add app
   - App source URL: `https://github.com/kashif55505/my-apps`
   - Open the additional options and set **Filter release titles by regular expression** to the app's name, e.g. `^Mail ` or `^ReelPlayer ` (with the space). This keeps each app's updates separate, since all apps share this repository.
   - Add, then Install.

Obtainium then checks for new releases and offers updates. Repeat step 3 for each app with its own title filter.

## Adding another app

Android app:

1. Put the Android project in a new folder, e.g. `notes/`.
2. Copy `.github/workflows/mail.yml` to `notes.yml` and replace `mail`/`Mail` with `notes`/`Notes` (folder, paths, tag prefix, title, APK name, concurrency group).
3. The signing secrets are shared, so nothing else is needed. In Obtainium use the filter `^Notes `.

Windows app: copy `.github/workflows/tezdm.yml` instead and change the folder, build command, file names and tag prefix.

## Signing

All APKs are signed with one release key, kept in this private repository under `.signing/` (used when the `KEYSTORE_*` repository secrets are not set). Keep a backup of `.signing/release.jks`: updates only install over an app signed with the same key. Never make this repository public while the key is in it.
