// Personal webmail: log in with any email + password, works with cPanel, Gmail, Outlook, Zoho, etc.
const path = require('path');
const crypto = require('crypto');
const express = require('express');
const session = require('express-session');
const multer = require('multer');
const sanitizeHtml = require('sanitize-html');
const { simpleParser } = require('mailparser');
const MailComposer = require('nodemailer/lib/mail-composer');
const { discover } = require('./lib/discover');
const { getClient, closeClient, adoptClient, openClient, smtpTransport, withMailbox, friendlyError } = require('./lib/mail');
const notify = require('./lib/notify');

const MAX_ACCOUNTS = 20;

const PORT = process.env.PORT || 3000;
const app = express();
app.set('trust proxy', 1);
app.disable('x-powered-by');

app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'SAMEORIGIN');
  res.setHeader('Referrer-Policy', 'no-referrer');
  next();
});
app.use(express.json({ limit: '2mb' }));
const store = new session.MemoryStore();
notify.startJanitor(store);
app.use(session({
  name: 'wm.sid',
  store,
  secret: process.env.SESSION_SECRET || crypto.randomBytes(32).toString('hex'),
  resave: false,
  saveUninitialized: false,
  rolling: true,
  cookie: { httpOnly: true, sameSite: 'lax', secure: 'auto', maxAge: 7 * 24 * 3600 * 1000 },
}));
app.use(express.static(path.join(__dirname, 'public')));

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 25 * 1024 * 1024, files: 20 } });
const wrap = (fn) => (req, res) => fn(req, res).catch((e) => {
  if (!e.status) console.error(e);
  res.status(e.status || 500).json({ error: e.message || 'Server error', detail: e.detail });
});

// Every API call names its account in the X-Account header (or ?acc= for plain links).
function auth(req, res, next) {
  const accs = req.session.accounts || [];
  if (!accs.length) return res.status(401).json({ error: 'Login required' });
  const want = String(req.get('X-Account') || req.query.acc || '').toLowerCase();
  const acc = want ? accs.find((a) => a.email.toLowerCase() === want) : accs[0];
  if (!acc) return res.status(404).json({ error: 'This account is not signed in.', code: 'NO_ACCOUNT' });
  req.acc = acc;
  next();
}
const poolKey = (req, email) => `${req.sessionID}|${email.toLowerCase()}`;
const client = (req) => getClient(poolKey(req, req.acc.email), req.acc);
const publicAccount = (c) => ({ email: c.email, notify: c.notify !== false, server: { imap: `${c.imap.host}:${c.imap.port}`, smtp: `${c.smtp.host}:${c.smtp.port}` } });

// ---------- Login ----------
const attempts = new Map();
function rateLimit(ip) {
  const now = Date.now();
  const list = (attempts.get(ip) || []).filter((t) => now - t < 15 * 60 * 1000);
  list.push(now);
  attempts.set(ip, list);
  return list.length > 10;
}

app.post('/api/login', wrap(async (req, res) => {
  const { email, password, manual } = req.body || {};
  if (rateLimit(req.ip)) return res.status(429).json({ error: 'Too many attempts. Please try again in 15 minutes.' });
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email || '') || !password) return res.status(400).json({ error: 'Enter a valid email and password.' });

  let cfg;
  if (manual && manual.imapHost) {
    const ip = +manual.imapPort || 993, sp = +manual.smtpPort || 465;
    cfg = {
      imap: { host: manual.imapHost.trim(), port: ip, secure: ip === 993 },
      smtp: { host: (manual.smtpHost || manual.imapHost).trim(), port: sp, secure: sp === 465 },
      source: 'manual',
    };
  } else {
    cfg = await discover(email.trim());
  }
  cfg.email = email.trim();
  cfg.user = (manual && manual.username) || cfg.email;
  cfg.pass = password;

  const id = cfg.email.toLowerCase();
  const existing = (req.session.accounts || []).findIndex((a) => a.email.toLowerCase() === id);
  if (existing < 0 && (req.session.accounts || []).length >= MAX_ACCOUNTS) return res.status(400).json({ error: `You can add up to ${MAX_ACCOUNTS} accounts.` });

  const c = await openClient(cfg);
  if (!req.session.accounts || !req.session.accounts.length) {
    // First account: fresh session id. Further accounts join the same session.
    await new Promise((r) => req.session.regenerate(r));
    req.session.accounts = [];
  }
  const accs = req.session.accounts;
  cfg.notify = existing >= 0 ? accs[existing].notify !== false : true;
  if (existing >= 0) accs[existing] = cfg; else accs.push(cfg);
  await closeClient(poolKey(req, id));
  adoptClient(poolKey(req, id), c); // keep the verified connection for the next requests
  notify.watch(req.sessionID, cfg);
  res.json(publicAccount(cfg));
}));

// Log out of every account
app.post('/api/logout', wrap(async (req, res) => {
  for (const a of req.session.accounts || []) await closeClient(poolKey(req, a.email));
  notify.stopAll(req.sessionID);
  req.session.destroy(() => res.json({ ok: true }));
}));

app.get('/api/me', (req, res) => {
  const accs = req.session.accounts || [];
  if (!accs.length) return res.status(401).json({ error: 'Login required' });
  res.json({ accounts: accs.map(publicAccount) });
});

// Remove one account
app.delete('/api/accounts/:email', wrap(async (req, res) => {
  const id = String(req.params.email).toLowerCase();
  const accs = req.session.accounts || [];
  await closeClient(poolKey(req, id));
  notify.unwatch(req.sessionID, id);
  req.session.accounts = accs.filter((a) => a.email.toLowerCase() !== id);
  if (!req.session.accounts.length) { notify.stopAll(req.sessionID); return req.session.destroy(() => res.json({ accounts: [] })); }
  res.json({ accounts: req.session.accounts.map(publicAccount) });
}));

// Turn notifications on/off for one account
app.post('/api/accounts/:email/notify', wrap(async (req, res) => {
  const acc = (req.session.accounts || []).find((a) => a.email.toLowerCase() === String(req.params.email).toLowerCase());
  if (!acc) return res.status(404).json({ error: 'Account not found.' });
  acc.notify = !!req.body.on;
  notify.setMuted(req.sessionID, acc.email, !acc.notify);
  res.json(publicAccount(acc));
}));

// Unread INBOX count for every account (sidebar badges)
app.get('/api/unread', wrap(async (req, res) => {
  const accs = req.session.accounts || [];
  if (!accs.length) return res.status(401).json({ error: 'Login required' });
  const out = {};
  await Promise.all(accs.map(async (a) => {
    try { out[a.email.toLowerCase()] = (await (await getClient(poolKey(req, a.email), a)).status('INBOX', { unseen: true })).unseen; }
    catch { out[a.email.toLowerCase()] = null; }
  }));
  res.json(out);
}));

// ---------- Live events & push ----------
app.get('/api/events', (req, res) => {
  if (!(req.session.accounts || []).length) return res.status(401).end();
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' });
  res.write('retry: 5000\n\n');
  const off = notify.addStream(req.sessionID, res);
  const ping = setInterval(() => res.write(': ping\n\n'), 25000);
  req.on('close', () => { clearInterval(ping); off(); });
});

app.get('/api/push/key', (req, res) => res.json({ key: notify.publicKey }));

app.post('/api/push/subscribe', (req, res) => {
  if (!(req.session.accounts || []).length) return res.status(401).json({ error: 'Login required' });
  const sub = req.body && req.body.subscription;
  if (!sub || !/^https:\/\//.test(sub.endpoint || '') || !sub.keys || !sub.keys.p256dh || !sub.keys.auth) return res.status(400).json({ error: 'Invalid subscription' });
  notify.addSubscription(req.sessionID, { endpoint: sub.endpoint, keys: { p256dh: sub.keys.p256dh, auth: sub.keys.auth } });
  res.json({ ok: true });
});

app.post('/api/push/unsubscribe', (req, res) => {
  if (req.body && req.body.endpoint) notify.removeSubscription(req.sessionID, req.body.endpoint);
  res.json({ ok: true });
});

// ---------- Folders ----------
const ORDER = { '\\Inbox': 0, '\\Drafts': 1, '\\Sent': 2, '\\Archive': 3, '\\Junk': 4, '\\Trash': 5 };

async function specialFolder(c, use) {
  const list = await c.list();
  const byUse = list.find((f) => f.specialUse === use);
  if (byUse) return byUse.path;
  const names = { '\\Sent': /^(inbox[./])?(sent|sent items|sent messages|sent mail)$/i, '\\Trash': /^(inbox[./])?(trash|deleted|deleted items|deleted messages|bin)$/i, '\\Drafts': /^(inbox[./])?drafts?$/i };
  const byName = list.find((f) => names[use] && names[use].test(f.path));
  return byName ? byName.path : null;
}

app.get('/api/folders', auth, wrap(async (req, res) => {
  const c = await client(req);
  const list = await c.list({ statusQuery: { unseen: true, messages: true } });
  const folders = list
    .filter((f) => !f.flags.has('\\Noselect') && !f.flags.has('\\NonExistent'))
    .map((f) => ({ path: f.path, name: f.name, specialUse: f.specialUse || (f.path.toUpperCase() === 'INBOX' ? '\\Inbox' : null), unseen: f.status ? f.status.unseen : 0, total: f.status ? f.status.messages : 0, delimiter: f.delimiter }))
    .sort((a, b) => (ORDER[a.specialUse] ?? 9) - (ORDER[b.specialUse] ?? 9) || a.path.localeCompare(b.path));
  res.json(folders);
}));

app.post('/api/folders', auth, wrap(async (req, res) => {
  const c = await client(req);
  const name = String(req.body.name || '').trim();
  if (!name) return res.status(400).json({ error: 'Enter a folder name.' });
  await c.mailboxCreate(name);
  res.json({ ok: true });
}));

// ---------- Message list ----------
function addr(list) { return (list || []).map((a) => ({ name: a.name || '', address: a.address || '' })); }
function hasAttachments(node) {
  if (!node) return false;
  if (node.disposition === 'attachment') return true;
  return (node.childNodes || []).some(hasAttachments);
}

app.get('/api/messages', auth, wrap(async (req, res) => {
  const folder = req.query.folder || 'INBOX';
  const page = Math.max(1, +req.query.page || 1);
  const size = Math.min(100, +req.query.size || 50);
  const q = String(req.query.q || '').trim();
  const c = await client(req);

  const out = await withMailbox(c, folder, async () => {
    await c.noop(); // pick up mail that arrived since this connection last looked
    const total = c.mailbox.exists;
    let range, useUid = false, count = total;
    if (q) {
      const uids = (await c.search({ or: [{ subject: q }, { from: q }, { to: q }, { body: q }] }, { uid: true })) || [];
      uids.sort((a, b) => b - a);
      count = uids.length;
      const slice = uids.slice((page - 1) * size, page * size);
      if (!slice.length) return { total: count, messages: [] };
      range = slice.join(','); useUid = true;
    } else {
      if (!total) return { total: 0, messages: [] };
      const end = total - (page - 1) * size;
      if (end < 1) return { total, messages: [] };
      const start = Math.max(1, end - size + 1);
      range = `${start}:${end}`;
    }
    const msgs = [];
    for await (const m of c.fetch(range, { uid: true, flags: true, envelope: true, internalDate: true, size: true, bodyStructure: true }, { uid: useUid })) {
      msgs.push({
        uid: m.uid,
        subject: m.envelope.subject || '(no subject)',
        from: addr(m.envelope.from),
        to: addr(m.envelope.to),
        date: m.envelope.date || m.internalDate,
        seen: m.flags.has('\\Seen'),
        flagged: m.flags.has('\\Flagged'),
        answered: m.flags.has('\\Answered'),
        attachments: hasAttachments(m.bodyStructure),
        size: m.size,
      });
    }
    msgs.sort((a, b) => b.uid - a.uid);
    return { total: count, messages: msgs };
  });
  res.json({ ...out, page, size });
}));

// ---------- Read one message ----------
async function loadParsed(c, folder, uid) {
  return withMailbox(c, folder, async () => {
    const m = await c.fetchOne(String(uid), { source: true, flags: true }, { uid: true });
    if (!m) { const e = new Error('Message not found.'); e.status = 404; throw e; }
    return { parsed: await simpleParser(m.source), flags: m.flags };
  });
}

function cleanHtml(html, attachments, showImages) {
  // Inline cid: images become data URIs; remote images are held back until the user allows them
  const cid = {};
  for (const a of attachments) if (a.cid) cid[a.cid] = `data:${a.contentType};base64,${a.content.toString('base64')}`;
  let blocked = false;
  const out = sanitizeHtml(html, {
    allowedTags: sanitizeHtml.defaults.allowedTags.concat(['img', 'style', 'center', 'font', 'span', 'u', 's', 'hr', 'h1', 'h2', 'del', 'ins', 'sup', 'sub']),
    allowedAttributes: { '*': ['style', 'class', 'align', 'width', 'height', 'bgcolor', 'color', 'border', 'cellpadding', 'cellspacing', 'valign', 'dir', 'face', 'size', 'colspan', 'rowspan'], a: ['href', 'title'], img: ['src', 'alt', 'title', 'width', 'height', 'style', 'data-blocked'] },
    allowedSchemes: ['http', 'https', 'mailto', 'tel', 'data', 'cid'],
    allowVulnerableTags: true, // <style> is allowed; output is shown in a script-free sandboxed iframe
    transformTags: {
      a: (tag, attribs) => ({ tagName: 'a', attribs: { ...attribs, target: '_blank', rel: 'noopener noreferrer' } }),
      img: (tag, attribs) => {
        let src = attribs.src || '';
        if (src.startsWith('cid:')) src = cid[src.slice(4)] || '';
        else if (/^https?:/i.test(src) && !showImages) { blocked = true; return { tagName: 'img', attribs: { ...attribs, src: '', 'data-blocked': '1', alt: attribs.alt || '' } }; }
        return { tagName: 'img', attribs: { ...attribs, src } };
      },
    },
  });
  return { html: out, blocked };
}

app.get('/api/message', auth, wrap(async (req, res) => {
  const folder = req.query.folder || 'INBOX';
  const uid = +req.query.uid;
  const c = await client(req);
  const { parsed, flags } = await loadParsed(c, folder, uid);
  if (!flags.has('\\Seen')) await withMailbox(c, folder, () => c.messageFlagsAdd(String(uid), ['\\Seen'], { uid: true }));

  const atts = parsed.attachments || [];
  const body = parsed.html
    ? cleanHtml(parsed.html, atts, req.query.images === '1')
    : { html: `<pre style="white-space:pre-wrap;font-family:inherit;margin:0">${sanitizeHtml(parsed.text || '', { allowedTags: [], allowedAttributes: {} })}</pre>`, blocked: false };

  res.json({
    uid, folder,
    subject: parsed.subject || '(no subject)',
    from: addr(parsed.from && parsed.from.value),
    to: addr(parsed.to && parsed.to.value),
    cc: addr(parsed.cc && parsed.cc.value),
    replyTo: addr(parsed.replyTo && parsed.replyTo.value),
    date: parsed.date,
    messageId: parsed.messageId,
    references: [].concat(parsed.references || []),
    html: body.html,
    imagesBlocked: body.blocked,
    text: parsed.text || '',
    flagged: flags.has('\\Flagged'),
    attachments: atts.map((a, i) => ({ index: i, filename: a.filename || `attachment-${i + 1}`, contentType: a.contentType, size: a.size, inline: !!a.cid && a.contentDisposition === 'inline' })).filter((a) => !a.inline),
  });
}));

app.get('/api/attachment', auth, wrap(async (req, res) => {
  const c = await client(req);
  const { parsed } = await loadParsed(c, req.query.folder || 'INBOX', +req.query.uid);
  const a = (parsed.attachments || [])[+req.query.index];
  if (!a) return res.status(404).json({ error: 'Attachment not found.' });
  const name = (a.filename || 'attachment').replace(/["\r\n]/g, '');
  res.setHeader('Content-Type', 'application/octet-stream');
  res.setHeader('Content-Disposition', `attachment; filename="${name.replace(/[^\x20-\x7e]/g, '_')}"; filename*=UTF-8''${encodeURIComponent(name)}`);
  res.send(a.content);
}));

app.get('/api/source', auth, wrap(async (req, res) => {
  const c = await client(req);
  const src = await withMailbox(c, req.query.folder || 'INBOX', async () => (await c.fetchOne(String(+req.query.uid), { source: true }, { uid: true })).source);
  res.type('text/plain').send(src);
}));

// ---------- Flags, move, delete ----------
const uidList = (u) => [].concat(u || []).map(Number).filter(Boolean).join(',');

app.post('/api/flags', auth, wrap(async (req, res) => {
  const { folder, uids, seen, flagged } = req.body;
  const c = await client(req);
  const set = uidList(uids);
  await withMailbox(c, folder, async () => {
    if (seen === true) await c.messageFlagsAdd(set, ['\\Seen'], { uid: true });
    if (seen === false) await c.messageFlagsRemove(set, ['\\Seen'], { uid: true });
    if (flagged === true) await c.messageFlagsAdd(set, ['\\Flagged'], { uid: true });
    if (flagged === false) await c.messageFlagsRemove(set, ['\\Flagged'], { uid: true });
  });
  res.json({ ok: true });
}));

app.post('/api/move', auth, wrap(async (req, res) => {
  const { folder, uids, dest } = req.body;
  const c = await client(req);
  await withMailbox(c, folder, () => c.messageMove(uidList(uids), dest, { uid: true }));
  res.json({ ok: true });
}));

app.post('/api/delete', auth, wrap(async (req, res) => {
  const { folder, uids } = req.body;
  const c = await client(req);
  const trash = await specialFolder(c, '\\Trash');
  const set = uidList(uids);
  await withMailbox(c, folder, async () => {
    if (trash && trash !== folder) await c.messageMove(set, trash, { uid: true });
    else await c.messageDelete(set, { uid: true });
  });
  res.json({ ok: true, permanent: !trash || trash === folder });
}));

// ---------- Send & drafts ----------
function buildMail(req) {
  const b = req.body;
  const cfg = req.acc;
  const fromName = (b.fromName || '').trim();
  const mail = {
    from: fromName ? { name: fromName, address: cfg.email } : cfg.email,
    to: b.to || undefined, cc: b.cc || undefined, bcc: b.bcc || undefined,
    subject: b.subject || '',
    html: b.html || undefined,
    text: b.text || undefined,
    inReplyTo: b.inReplyTo || undefined,
    references: b.references || undefined,
    attachments: (req.files || []).map((f) => ({ filename: f.originalname, content: f.buffer, contentType: f.mimetype })),
  };
  return mail;
}

async function appendTo(c, use, raw, flags) {
  const folder = await specialFolder(c, use);
  if (folder) await c.append(folder, raw, flags);
  return folder;
}

app.post('/api/send', auth, upload.array('attachments'), wrap(async (req, res) => {
  const cfg = req.acc;
  const mail = buildMail(req);
  if (!mail.to && !mail.cc && !mail.bcc) return res.status(400).json({ error: 'Add at least one recipient.' });
  const raw = await new MailComposer(mail).compile().build();
  const transport = smtpTransport(cfg);
  try {
    await transport.sendMail({ envelope: { from: cfg.email, to: [mail.to, mail.cc, mail.bcc].filter(Boolean).join(',') }, raw });
  } catch (e) {
    if (/certificate|self.signed|altnames/i.test(e.message) && !cfg.smtp.insecureCert) {
      cfg.smtp.insecureCert = true;
      await smtpTransport(cfg).sendMail({ envelope: { from: cfg.email, to: [mail.to, mail.cc, mail.bcc].filter(Boolean).join(',') }, raw });
    } else {
      const err = new Error(`Could not send: ${e.response || e.message}`); err.status = 502; throw err;
    }
  }
  const c = await client(req);
  // Gmail and Microsoft save sent mail automatically; everyone else (cPanel etc.) needs a copy appended
  if (!/gmail\.com|office365\.com|outlook\.com/.test(cfg.smtp.host)) {
    try { await appendTo(c, '\\Sent', raw, ['\\Seen']); } catch (e) { console.warn('Sent copy failed:', e.message); }
  }
  if (req.body.replyUid && req.body.replyFolder) {
    try { await withMailbox(c, req.body.replyFolder, () => c.messageFlagsAdd(String(+req.body.replyUid), ['\\Answered'], { uid: true })); } catch {}
  }
  if (req.body.draftUid) {
    try { const d = await specialFolder(c, '\\Drafts'); if (d) await withMailbox(c, d, () => c.messageDelete(String(+req.body.draftUid), { uid: true })); } catch {}
  }
  res.json({ ok: true });
}));

app.post('/api/draft', auth, upload.array('attachments'), wrap(async (req, res) => {
  const raw = await new MailComposer(buildMail(req)).compile().build();
  const c = await client(req);
  const folder = await specialFolder(c, '\\Drafts') || 'Drafts';
  if (!(await c.list()).some((f) => f.path === folder)) await c.mailboxCreate(folder);
  const r = await c.append(folder, raw, ['\\Draft', '\\Seen']);
  if (req.body.draftUid) { try { await withMailbox(c, folder, () => c.messageDelete(String(+req.body.draftUid), { uid: true })); } catch {} }
  res.json({ ok: true, uid: r && r.uid, folder });
}));

app.use('/api', (req, res) => res.status(404).json({ error: 'Not found' }));

if (require.main === module) {
  app.listen(PORT, () => console.log(`Webmail running on http://localhost:${PORT}`));
}
module.exports = app;
