# My apps

All my Android apps in one place. Each app lives in its own folder and has its own automatic build: every change to an app's folder builds a signed APK and publishes it under **Releases**, so the latest version can always be downloaded here or installed and updated with [Obtainium](https://github.com/ImranR98/Obtainium).

| App | Folder | Releases titled | APK file |
|---|---|---|---|
| Mail – multi-account email with notifications | [`mail/`](mail/) | `Mail 1.0.<n>` | `Mail-v1.0.<n>.apk` |

## Install and update with Obtainium

This repository is private, so Obtainium needs a read-only access token once.

1. **Create the token** on GitHub: Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate new token
   - Repository access: *Only select repositories* > `my-apps`
   - Permissions > Repository permissions > **Contents: Read-only**
   - Expiration: as long as you like (when it expires, make a new one and paste it in Obtainium again)
2. **Give it to Obtainium**: Obtainium > Settings > *Source-specific* > GitHub > **Personal access token** > paste.
3. **Add an app**: Obtainium > Add app
   - App source URL: `https://github.com/kashif55505/my-apps`
   - Open the additional options and set **Filter release titles by regular expression** to the app's name, e.g. `^Mail ` (with the space). This keeps each app's updates separate, since all apps share this repository.
   - Add, then Install.

Obtainium then checks for new releases and offers updates. Repeat step 3 for each app with its own title filter.

## Adding another app

1. Put the Android project in a new folder, e.g. `notes/`.
2. Copy `.github/workflows/mail.yml` to `notes.yml` and replace `mail`/`Mail` with `notes`/`Notes` (folder, paths, tag prefix, title, APK name, concurrency group).
3. The signing secrets are shared, so nothing else is needed. In Obtainium use the filter `^Notes `.

## Signing

All APKs are signed with one release key stored in the repository secrets (`KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`). Keep the backup of that key: updates only install over an app signed with the same key.
