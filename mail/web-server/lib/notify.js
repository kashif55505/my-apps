// New-mail notifications. For every logged-in account, a dedicated IMAP connection stays on INBOX
// in IDLE mode, so the server hears about new mail within seconds. Each new message is sent to:
//   - open browser tabs, over Server-Sent Events (/api/events)
//   - the browser's push service (Web Push), so a notification shows even when the tab is closed
const fs = require('fs');
const path = require('path');
const webpush = require('web-push');
const { ImapFlow } = require('imapflow');
const { imapOptions } = require('./mail');

// ---------- VAPID keys (identify this server to browser push services) ----------
function loadVapid() {
  if (process.env.VAPID_PUBLIC_KEY && process.env.VAPID_PRIVATE_KEY) {
    return { publicKey: process.env.VAPID_PUBLIC_KEY, privateKey: process.env.VAPID_PRIVATE_KEY };
  }
  const file = path.join(__dirname, '..', 'data', 'vapid.json');
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch {}
  const keys = webpush.generateVAPIDKeys();
  try { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(keys), { mode: 0o600 }); } catch (e) { console.warn('Could not save VAPID keys:', e.message); }
  return keys;
}
const vapid = loadVapid();
webpush.setVapidDetails(process.env.VAPID_SUBJECT || 'mailto:webmail@example.com', vapid.publicKey, vapid.privateKey);

// sid -> { accounts: Map(email -> Watch), streams: Set(res), subs: Map(endpoint -> subscription) }
const groups = new Map();
const group = (sid) => {
  if (!groups.has(sid)) groups.set(sid, { accounts: new Map(), streams: new Set(), subs: new Map() });
  return groups.get(sid);
};

class Watch {
  constructor(sid, cfg) {
    this.sid = sid;
    this.cfg = JSON.parse(JSON.stringify(cfg));
    this.id = cfg.email.toLowerCase();
    this.muted = cfg.notify === false;
    this.lastUid = 0;
    this.stopped = false;
    this.delay = 5000;
    this.start();
  }

  async start() {
    if (this.stopped) return;
    const client = new ImapFlow({
      ...imapOptions(this.cfg),
      maxIdleTime: 4 * 60 * 1000,   // restart IDLE every few minutes so NAT/firewalls don't drop it
      missingIdleCommand: 'NOOP',    // servers without IDLE get polled instead
      autoIdleDelay: 500,            // go back to IDLE right after each check
    });
    this.client = client;
    client.on('error', () => {});
    client.on('close', () => this.retry());
    client.on('exists', () => this.check().catch(() => {}));
    try {
      await client.connect();
      const box = await client.mailboxOpen('INBOX', { readOnly: true });
      if (!this.lastUid) this.lastUid = Number(box.uidNext) - 1;
      else await this.check(); // catch up on mail that arrived while reconnecting
      this.delay = 5000;
    } catch {
      try { client.close(); } catch {}
    }
  }

  retry() {
    if (this.stopped || this.retrying) return;
    this.retrying = true;
    setTimeout(() => { this.retrying = false; this.start(); }, this.delay);
    this.delay = Math.min(this.delay * 2, 5 * 60 * 1000);
  }

  async check() {
    if (this.checking) { this.again = true; return; }
    this.checking = true;
    try {
      const found = [];
      for await (const m of this.client.fetch(`${this.lastUid + 1}:*`, { uid: true, envelope: true, flags: true }, { uid: true })) {
        if (m.uid <= this.lastUid) continue; // "N:*" always returns the last message
        found.push(m);
      }
      for (const m of found.sort((a, b) => a.uid - b.uid)) {
        this.lastUid = Math.max(this.lastUid, m.uid);
        if (m.flags.has('\\Seen')) continue;
        const from = (m.envelope.from || [])[0] || {};
        emit(this.sid, {
          type: 'mail',
          account: this.id,
          uid: m.uid,
          from: from.name || from.address || '',
          fromAddress: from.address || '',
          subject: m.envelope.subject || '(no subject)',
          date: m.envelope.date,
          muted: this.muted,
        });
      }
    } finally {
      this.checking = false;
      if (this.again) { this.again = false; await this.check(); }
    }
  }

  stop() {
    this.stopped = true;
    if (this.client) { this.client.logout().catch(() => { try { this.client.close(); } catch {} }); }
  }
}

function emit(sid, ev) {
  const g = groups.get(sid);
  if (!g) return;
  const line = `event: mail\ndata: ${JSON.stringify(ev)}\n\n`;
  for (const res of g.streams) res.write(line);
  if (ev.muted) return;
  const payload = JSON.stringify({
    title: ev.from || 'New email',
    body: ev.subject,
    account: ev.account,
    uid: ev.uid,
  });
  for (const [endpoint, sub] of g.subs) {
    webpush.sendNotification(sub, payload, { TTL: 24 * 3600, urgency: 'high' }).catch((e) => {
      if (e.statusCode === 404 || e.statusCode === 410) g.subs.delete(endpoint); // browser unsubscribed
      else console.warn('Push failed:', e.statusCode || '', e.body || e.message);
    });
  }
}

// ---------- API used by server.js ----------
function watch(sid, cfg) {
  const g = group(sid);
  const id = cfg.email.toLowerCase();
  if (g.accounts.has(id)) g.accounts.get(id).stop();
  g.accounts.set(id, new Watch(sid, cfg));
}
function unwatch(sid, email) {
  const g = groups.get(sid);
  if (!g) return;
  const id = email.toLowerCase();
  const w = g.accounts.get(id);
  if (w) { w.stop(); g.accounts.delete(id); }
}
function setMuted(sid, email, muted) {
  const w = groups.get(sid) && groups.get(sid).accounts.get(email.toLowerCase());
  if (w) w.muted = muted;
}
function stopAll(sid) {
  const g = groups.get(sid);
  if (!g) return;
  for (const w of g.accounts.values()) w.stop();
  for (const res of g.streams) res.end();
  groups.delete(sid);
}
function addStream(sid, res) {
  const g = group(sid);
  g.streams.add(res);
  return () => g.streams.delete(res);
}
function addSubscription(sid, sub) { group(sid).subs.set(sub.endpoint, sub); }
function removeSubscription(sid, endpoint) { const g = groups.get(sid); if (g) g.subs.delete(endpoint); }

// Stop watchers whose session has expired
function startJanitor(store) {
  setInterval(() => {
    for (const sid of groups.keys()) {
      store.get(sid, (err, sess) => { if (!err && (!sess || !sess.accounts || !sess.accounts.length)) stopAll(sid); });
    }
  }, 5 * 60 * 1000).unref();
}

module.exports = { watch, unwatch, setMuted, stopAll, addStream, addSubscription, removeSubscription, startJanitor, publicKey: vapid.publicKey, _emit: emit };
