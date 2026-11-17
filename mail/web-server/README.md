# Mail – web version

The same mail app for a web browser. Sign in to any email account with just an email and password (cPanel, Gmail, Outlook, Zoho, Yahoo…). Multiple accounts, new mail notifications, one long scrolling list, select all, pull to refresh.

## Run it
Needs [Node.js](https://nodejs.org) 18 or newer.
```bash
npm install
npm start
```
Open http://localhost:3000

### On cPanel
1. cPanel > **Setup Node.js App** > Create Application (Node 18+, startup file `server.js`).
2. Upload these files (without `node_modules`) to the application folder.
3. Click **Run NPM Install**, then **Restart**.
4. Add an environment variable `SESSION_SECRET` with a long random value.

### Docker / VPS
```bash
docker build -t webmail .
docker run -d --restart unless-stopped -p 3000:3000 -v webmail-data:/app/data \
  -e SESSION_SECRET=long-random-value -e VAPID_SUBJECT=mailto:you@example.com webmail
```
Put it behind HTTPS (Nginx/Caddy). Browsers only allow push notifications over HTTPS.

## Notes
- Notifications come from the server, so it must keep running. After a server restart, sign in again.
- Passwords are kept only in server memory for the session.
- Gmail, Yahoo, iCloud and Outlook with 2-step verification need an **App password**.
- Shared cPanel hosting may put idle Node apps to sleep, which pauses notifications. A VPS is more reliable.
