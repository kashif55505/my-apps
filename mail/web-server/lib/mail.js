// IMAP/SMTP helpers. One live IMAP connection per logged-in session, reused across requests.
const { ImapFlow } = require('imapflow');
const nodemailer = require('nodemailer');

const clients = new Map(); // sessionId -> { client, cfg, lastUsed }

function imapOptions(cfg) {
  return {
    host: cfg.imap.host,
    port: cfg.imap.port,
    secure: cfg.imap.secure,
    auth: { user: cfg.user, pass: cfg.pass },
    tls: { rejectUnauthorized: !cfg.imap.insecureCert, servername: cfg.imap.host },
    logger: false,
    connectionTimeout: 15000,
    greetingTimeout: 10000,
    socketTimeout: 5 * 60 * 1000,
  };
}

function smtpTransport(cfg) {
  return nodemailer.createTransport({
    host: cfg.smtp.host,
    port: cfg.smtp.port,
    secure: cfg.smtp.secure,
    requireTLS: !cfg.smtp.secure && cfg.smtp.port !== 25,
    auth: { user: cfg.user, pass: cfg.pass },
    tls: { rejectUnauthorized: !cfg.smtp.insecureCert, servername: cfg.smtp.host },
    connectionTimeout: 15000,
  });
}

// Try to log in once; returns an open client. If the full address fails, try the local part
// (some older hosts use it as the username). Only two attempts, to avoid tripping lockouts.
async function openClient(cfg) {
  const users = cfg.userTried ? [cfg.user] : [...new Set([cfg.user, cfg.usernameStyle === 'local' ? cfg.email.split('@')[0] : null].filter(Boolean))];
  let lastErr;
  for (const user of users) {
    const client = new ImapFlow(imapOptions({ ...cfg, user }));
    client.on('error', () => {});
    try {
      await client.connect();
      cfg.user = user;
      cfg.userTried = true;
      return client;
    } catch (e) {
      lastErr = e;
      // Retry once with insecure cert when the server's certificate doesn't match (common on cPanel)
      if (/certificate|self.signed|altnames|SELF_SIGNED|UNABLE_TO_VERIFY/i.test(e.message + (e.code || '')) && !cfg.imap.insecureCert) {
        cfg.imap.insecureCert = true;
        cfg.smtp.insecureCert = true;
        return openClient(cfg);
      }
      if (!e.authenticationFailed) break;
    }
  }
  throw friendlyError(lastErr, cfg);
}

function friendlyError(e, cfg) {
  const msg = (e && (e.responseText || e.message)) || 'Unknown error';
  let hint = '';
  if (e && e.authenticationFailed || /auth|credential|password|LOGIN failed/i.test(msg)) {
    hint = 'Wrong email or password.';
    if (/gmail|google/.test(cfg.imap.host)) hint += ' For Gmail, create an "App password" under Google Account > Security and use that.';
    else if (/office365|outlook/.test(cfg.imap.host)) hint += ' Microsoft accounts need IMAP turned on, and an app password if 2-step verification is on.';
    else if (/yahoo|aol|icloud|me\.com/.test(cfg.imap.host)) hint += ' This provider needs an "app password".';
  } else if (/ENOTFOUND|ECONNREFUSED|ETIMEDOUT|timeout/i.test(msg)) {
    hint = `Could not connect to the mail server (${cfg.imap.host}:${cfg.imap.port}).`;
  }
  const err = new Error(hint || msg);
  err.status = hint.startsWith('Wrong email') ? 401 : 502;
  err.detail = msg;
  return err;
}

async function getClient(sid, cfg) {
  const entry = clients.get(sid);
  if (entry && entry.client.usable) { entry.lastUsed = Date.now(); return entry.client; }
  if (entry) { try { entry.client.close(); } catch {} }
  const client = await openClient(cfg);
  clients.set(sid, { client, lastUsed: Date.now() });
  return client;
}

function adoptClient(sid, client) {
  clients.set(sid, { client, lastUsed: Date.now() });
}

async function closeClient(sid) {
  const entry = clients.get(sid);
  clients.delete(sid);
  if (entry) { try { await entry.client.logout(); } catch { try { entry.client.close(); } catch {} } }
}

// Close idle connections after 10 minutes; the next request reopens them.
setInterval(() => {
  const cutoff = Date.now() - 10 * 60 * 1000;
  for (const [sid, e] of clients) if (e.lastUsed < cutoff) closeClient(sid);
}, 60 * 1000).unref();

async function withMailbox(client, path, fn) {
  const lock = await client.getMailboxLock(path);
  try { return await fn(); } finally { lock.release(); }
}

module.exports = { imapOptions, getClient, closeClient, adoptClient, openClient, smtpTransport, withMailbox, friendlyError };
