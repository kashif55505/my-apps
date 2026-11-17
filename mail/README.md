# Mail – Android app

A personal email app that signs in to any email account with just an **email and password**: your own domain (cPanel), Gmail, Outlook, Zoho, Hostinger, GoDaddy/Titan, Yahoo and more. Several accounts work side by side, and you get a notification for new mail in every account.

No server is needed. The phone talks directly to your mail servers (IMAP/SMTP).

## Build the APK

1. Extract the zip.
2. In Android Studio: **File > Open** and choose the `webmail` folder.
3. Android Studio runs the Gradle sync on its own. The first time it downloads what it needs, which can take 3–10 minutes. Wait for "Gradle sync finished".
   - If Android SDK 35 is missing, Studio offers to install it. Click Install.
4. **Build > Build App Bundle(s) / APK(s) > Build APK(s)**
5. When it finishes, click "locate". The APK is at
   `app/build/outputs/apk/debug/app-debug.apk`
6. Copy it to your phone and install it (allow "install unknown apps" when asked).

With the phone connected over USB you can also just press **Run ▶**.

### If the sync fails
- **"Unsupported Java" / "incompatible Java version"**: File > Settings > Build, Execution, Deployment > Build Tools > Gradle > **Gradle JDK**: choose 17 or 21 (e.g. "jbr-21"), then Sync Now.
- **"SDK location not found"**: reopen the folder, or install Android 15 (API 35) from Tools > SDK Manager.
- If Studio suggests upgrading the Android Gradle Plugin, you can ignore it; the project builds as is.

## Using the app

- Enter your email and password. The server settings are found automatically. If not, open **Manual settings** (on cPanel usually `mail.yourdomain.com`, ports 993 and 465).
- **+ Add account** adds more accounts. Tap an account in the menu to open its inbox. Switching is instant because each inbox is loaded in the background.
- One long list: scroll down for older mail, pull down from the top to refresh.
- **Select all** above the list, then Read / Unread / Move / Delete. While something is selected, tapping a message adds it to the selection.
- 🔔 next to an account turns its notifications on or off; ✕ removes the account from the app (your emails stay on the server).
- Tapping an attachment saves it to **Downloads/Mail** and opens it.

## Notifications

After the first sign-in the app asks for two permissions. Allow both:
1. **Notifications**.
2. **Ignore battery optimization**. Without it, Android stops the app in the background after a while.

The app keeps a connection open to each account (IMAP IDLE), so new mail shows up within a second or two, even when the app is closed. A backup check also runs every 15 minutes, and everything restarts by itself after a phone reboot. Tapping a notification opens that email.

Android requires an app that runs in the background to have a notification. The app puts it in a hidden channel ("Background sync"), so it does not appear in the status bar or the notification shade. On some older phones (Android 8–12) Android may still show its own small "Mail is running" note. Long-press it and turn it off; the app keeps running.

Some phones (Xiaomi, Oppo, Vivo, Realme, Huawei) stop apps more aggressively. On those, open Settings > Apps > Mail, turn on **Autostart**, and set battery to **No restrictions**.

## Good to know
- **Gmail / Yahoo / iCloud / Outlook with 2-step verification**: your normal password won't work. Create an **App password** in the provider's security settings and use that.
- Passwords are stored only on this phone, encrypted with the Android Keystore. Uninstalling the app removes them.
- Some cPanel servers use a certificate issued for the hosting server's name rather than your domain. The app still connects, like other mail apps do, and also tries the other secure port if one fails.

## Project layout
- `app/src/main/assets/www/` – the interface (HTML/CSS/JS)
- `app/src/main/java/com/personal/webmail/`
  - `MainActivity` – the screen, file picker, back button, permissions
  - `MailApi` – reading, sending, folders, attachments (IMAP/SMTP)
  - `Discover` – finds server settings from the email address
  - `AccountStore` – encrypted account storage
  - `MailService`, `MailCheck`, `Notifier`, `CheckWorker`, `BootReceiver` – new mail notifications
- `web-server/` – the same app for a web browser, with a Node.js server. Not needed for the APK.

## Automatic releases
Every change under `mail/` is built and published by `.github/workflows/mail.yml` as a GitHub Release titled **Mail 1.0.<n>** with the file `Mail-v1.0.<n>.apk`. See the main README of the repo for the Obtainium setup.
