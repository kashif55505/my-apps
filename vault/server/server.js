// My Vault — apni storage par chalne wala private vault server.
// Node.js 18+ chahiye. Koi extra package nahi.
// Server sirf encrypted (lock shuda) data save karta hai. Password aur
// encryption key kabhi server tak nahi pohanchte.
'use strict';
const http = require('http');
const fs = require('fs');
const fsp = fs.promises;
const path = require('path');
const crypto = require('crypto');

const ROOT = __dirname;
const cfgPath = path.join(ROOT, 'config.json');
const cfg = Object.assign({ port: 8080, host: '127.0.0.1', dataDir: './vault-data' },
  fs.existsSync(cfgPath) ? JSON.parse(fs.readFileSync(cfgPath, 'utf8')) : {});
const DATA = path.resolve(ROOT, cfg.dataDir);
const FILES = path.join(DATA, 'files');
const DBF = path.join(DATA, 'db.json');
// Web app: release zip mein ./public, repo mein Android app ke assets wala folder
const PUB = [path.join(ROOT, 'public'), path.join(ROOT, '..', 'app', 'src', 'main', 'assets', 'www')].find(p => fs.existsSync(p));
const MAX_CHUNK = 10 * 1024 * 1024; // encrypted chunk ki hadd (app 8 MB bhejti hai)
fs.mkdirSync(FILES, { recursive: true });

// ---------- chhota sa JSON database ----------
let db = { account: null, items: {}, deleted: {} };
if (fs.existsSync(DBF)) db = Object.assign(db, JSON.parse(fs.readFileSync(DBF, 'utf8')));
db.deleted = db.deleted || {};
let saving = Promise.resolve();
function save() {
  saving = saving.then(async () => {
    const tmp = DBF + '.tmp';
    await fsp.writeFile(tmp, JSON.stringify(db));
    await fsp.rename(tmp, DBF);
  }).catch(e => console.error('Save error:', e));
  return saving;
}

// ---------- helpers ----------
const scrypt = (pw, salt) => new Promise((res, rej) =>
  crypto.scrypt(pw, salt, 64, { N: 16384, r: 8, p: 1 }, (e, k) => e ? rej(e) : res(k)));
const sessions = new Map(); // token -> expiry
const SESSION_MS = 12 * 3600 * 1000;
const fails = new Map(); // ip -> {n, until}
const idOk = s => /^[a-z0-9]{8,40}$/.test(s);
const itemDir = id => path.join(FILES, id);

function send(res, code, body, headers = {}) {
  const isBuf = Buffer.isBuffer(body);
  const data = isBuf ? body : JSON.stringify(body ?? {});
  res.writeHead(code, Object.assign({
    'Content-Type': isBuf ? 'application/octet-stream' : 'application/json',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff'
  }, headers));
  res.end(data);
}
function readBody(req, limit) {
  return new Promise((res, rej) => {
    const parts = []; let n = 0;
    req.on('data', c => { n += c.length; if (n > limit) { rej(Object.assign(new Error('too big'), { code: 413 })); req.destroy(); } else parts.push(c); });
    req.on('end', () => res(Buffer.concat(parts)));
    req.on('error', rej);
  });
}
async function json(req) {
  const b = await readBody(req, 2 * 1024 * 1024);
  try { return JSON.parse(b.toString('utf8') || '{}'); } catch { throw Object.assign(new Error('bad json'), { code: 400 }); }
}
function authed(req) {
  const h = req.headers.authorization || '';
  const t = h.startsWith('Bearer ') ? h.slice(7) : '';
  const exp = sessions.get(t);
  if (!exp || exp < Date.now()) { sessions.delete(t); return false; }
  sessions.set(t, Date.now() + SESSION_MS);
  return true;
}
const ipOf = req => req.socket.remoteAddress || '?';
const pub = it => ({ id: it.id, k: it.k, c: it.c, u: it.u || it.c, m: it.m, th: it.th || null, n: it.n, sz: it.sz || 0 });

// Phone app (doosre origin se) bhi baat kar sake. Login token header mein jata hai, cookies nahi.
function cors(req, res) {
  const o = req.headers.origin;
  if (!o) return;
  res.setHeader('Access-Control-Allow-Origin', o);
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS');
  res.setHeader('Access-Control-Max-Age', '86400');
}

// ---------- static files ----------
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.json': 'application/json',
  '.webmanifest': 'application/manifest+json', '.png': 'image/png', '.svg': 'image/svg+xml', '.css': 'text/css' };
async function serveStatic(req, res, p) {
  if (p === '/') p = '/index.html';
  const f = path.normalize(path.join(PUB, p));
  if (!f.startsWith(PUB)) return send(res, 404, { error: 'not found' });
  try {
    const data = await fsp.readFile(f);
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(f)] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
      'Content-Security-Policy': "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'"
    });
    res.end(data);
  } catch { send(res, 404, { error: 'not found' }); }
}

// ---------- API ----------
async function api(req, res, p) {
  const m = req.method;
  const seg = p.split('/').filter(Boolean).slice(1); // after 'api'

  if (p === '/api/state' && m === 'GET')
    return send(res, 200, { setup: !!db.account });

  if (p === '/api/setup' && m === 'POST') {
    if (db.account) return send(res, 409, { error: 'Account pehle se bana hua hai' });
    const b = await json(req);
    if (!b.username || !b.salt || !b.authHash || !b.wk || !b.iter) return send(res, 400, { error: 'missing' });
    const s = crypto.randomBytes(16);
    db.account = { username: String(b.username).toLowerCase().trim(), salt: b.salt, iter: b.iter, wk: b.wk,
      ah: (await scrypt(b.authHash, s)).toString('base64'), as: s.toString('base64'), c: Date.now() };
    await save();
    return send(res, 200, { ok: true });
  }

  if (p === '/api/prelogin' && m === 'POST') {
    const b = await json(req);
    const a = db.account;
    const u = String(b.username || '').toLowerCase().trim();
    if (a && u === a.username) return send(res, 200, { salt: a.salt, iter: a.iter });
    // ghalat naam par bhi jawab do taa ke koi naam guess na kar sake
    const fake = crypto.createHash('sha256').update('x' + u + (a ? a.as : '')).digest('base64').slice(0, 22);
    return send(res, 200, { salt: fake, iter: a ? a.iter : 310000 });
  }

  if (p === '/api/login' && m === 'POST') {
    const ip = ipOf(req), f = fails.get(ip) || { n: 0, until: 0 };
    if (f.until > Date.now()) return send(res, 429, { error: 'wait', wait: Math.ceil((f.until - Date.now()) / 1000) });
    const b = await json(req);
    const a = db.account;
    let ok = false;
    if (a && String(b.username || '').toLowerCase().trim() === a.username && b.authHash) {
      const h = await scrypt(String(b.authHash), Buffer.from(a.as, 'base64'));
      ok = crypto.timingSafeEqual(h, Buffer.from(a.ah, 'base64'));
    } else await scrypt('dummy', 'dummy-salt-0000');
    if (!ok) {
      f.n++; if (f.n >= 5) { f.until = Date.now() + Math.min(15 * 60, 30 * 2 ** (f.n - 5)) * 1000; }
      fails.set(ip, f);
      return send(res, 401, { error: 'Ghalat username ya password' });
    }
    fails.delete(ip);
    const t = crypto.randomBytes(32).toString('base64url');
    sessions.set(t, Date.now() + SESSION_MS);
    return send(res, 200, { token: t, wk: a.wk });
  }

  if (!authed(req)) return send(res, 401, { error: 'login' });

  if (p === '/api/logout' && m === 'POST') {
    sessions.delete((req.headers.authorization || '').slice(7));
    return send(res, 200, { ok: true });
  }

  if (p === '/api/password' && m === 'POST') {
    const b = await json(req), a = db.account;
    const h = await scrypt(String(b.oldAuthHash || ''), Buffer.from(a.as, 'base64'));
    if (!crypto.timingSafeEqual(h, Buffer.from(a.ah, 'base64'))) return send(res, 403, { error: 'Purana password ghalat hai' });
    if (!b.salt || !b.authHash || !b.wk || !b.iter) return send(res, 400, { error: 'missing' });
    const s = crypto.randomBytes(16);
    Object.assign(a, { salt: b.salt, iter: b.iter, wk: b.wk, ah: (await scrypt(b.authHash, s)).toString('base64'), as: s.toString('base64') });
    await save();
    return send(res, 200, { ok: true });
  }

  if (p === '/api/usage' && m === 'GET') {
    let used = 0; for (const it of Object.values(db.items)) used += it.sz || 0;
    let free = null, total = null;
    try { const s = await fsp.statfs(DATA); free = s.bavail * s.bsize; total = s.blocks * s.bsize; } catch {}
    return send(res, 200, { used, free, total });
  }

  if (p === '/api/items' && m === 'GET')
    return send(res, 200, { items: Object.values(db.items).filter(i => i.done).map(pub), deleted: db.deleted, now: Date.now() });

  if (p === '/api/items' && m === 'POST') {
    const b = await json(req);
    if (!['photo', 'video', 'note', 'file'].includes(b.k) || typeof b.m !== 'string') return send(res, 400, { error: 'bad item' });
    const n = Math.max(0, Math.min(100000, b.n | 0));
    // phone app apni id khud banati hai (offline bhi), warna server banata hai
    const id = b.id === undefined ? Date.now().toString(36) + crypto.randomBytes(6).toString('hex') : String(b.id);
    if (!idOk(id)) return send(res, 400, { error: 'bad id' });
    if (db.items[id] && db.items[id].done) return send(res, 200, { id, exists: true });
    if (db.deleted[id]) return send(res, 410, { error: 'deleted' });
    const c = Number.isFinite(b.c) ? b.c : Date.now(), u = Number.isFinite(b.u) ? b.u : c;
    db.items[id] = { id, k: b.k, c, u, m: b.m, th: typeof b.th === 'string' ? b.th : null, n, sz: 0, done: n === 0 };
    if (n) { await fsp.rm(itemDir(id), { recursive: true, force: true }); await fsp.mkdir(itemDir(id), { recursive: true }); }
    await save();
    return send(res, 200, { id });
  }

  const id = seg[1];
  if (seg[0] === 'items' && id) {
    if (!idOk(id)) return send(res, 404, { error: 'not found' });
    if (!db.items[id]) {
      if (seg.length === 2 && m === 'DELETE') { db.deleted[id] = db.deleted[id] || Date.now(); await save(); return send(res, 200, { ok: true }); }
      return send(res, 404, { error: 'not found' });
    }
    const it = db.items[id];

    if (seg.length === 2 && m === 'PATCH') {
      const b = await json(req);
      if (typeof b.m === 'string') it.m = b.m;
      it.u = Number.isFinite(b.u) ? b.u : Date.now();
      if (b.touch) it.c = it.u;
      await save();
      return send(res, 200, { ok: true });
    }
    if (seg.length === 2 && m === 'DELETE') {
      delete db.items[id];
      db.deleted[id] = Date.now(); // doosre devices ko pata chale ke yeh delete hua
      await save();
      await fsp.rm(itemDir(id), { recursive: true, force: true });
      return send(res, 200, { ok: true });
    }
    if (seg.length === 3 && seg[2] === 'done' && m === 'POST') {
      let sz = 0;
      for (let i = 0; i < it.n; i++) {
        try { sz += (await fsp.stat(path.join(itemDir(id), String(i)))).size; } catch { return send(res, 400, { error: 'missing chunk ' + i }); }
      }
      it.sz = sz; it.done = true; await save();
      return send(res, 200, { ok: true });
    }
    if (seg.length === 4 && seg[2] === 'chunk') {
      const i = Number(seg[3]);
      if (!Number.isInteger(i) || i < 0 || i >= it.n) return send(res, 400, { error: 'bad chunk' });
      const f = path.join(itemDir(id), String(i));
      if (m === 'PUT') {
        if (it.done) return send(res, 409, { error: 'already done' });
        const body = await readBody(req, MAX_CHUNK);
        await fsp.writeFile(f + '.part', body);
        await fsp.rename(f + '.part', f);
        return send(res, 200, { ok: true });
      }
      if (m === 'GET') {
        try { return send(res, 200, await fsp.readFile(f)); } catch { return send(res, 404, { error: 'missing' }); }
      }
    }
  }
  return send(res, 404, { error: 'not found' });
}

// adhoori uploads saaf karo (24 ghante se purani)
setInterval(async () => {
  let ch = false;
  for (const it of Object.values(db.items)) if (!it.done && Date.now() - it.c > 86400000) {
    delete db.items[it.id]; ch = true; await fsp.rm(itemDir(it.id), { recursive: true, force: true });
  }
  for (const [t, e] of sessions) if (e < Date.now()) sessions.delete(t);
  if (ch) save();
}, 3600000).unref();

http.createServer(async (req, res) => {
  try {
    const p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (p.startsWith('/api/')) {
      cors(req, res);
      if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
      await api(req, res, p);
    }
    else if (req.method === 'GET') await serveStatic(req, res, p);
    else send(res, 405, { error: 'method' });
  } catch (e) {
    if (!res.headersSent) send(res, e.code === 413 ? 413 : e.code === 400 ? 400 : 500, { error: e.code === 413 ? 'too big' : 'server error' });
    if (!e.code) console.error(e);
  }
}).listen(cfg.port, cfg.host, () => {
  console.log('\n  My Vault chal raha hai!');
  console.log('  Is computer par kholein:  http://localhost:' + cfg.port);
  console.log('  Data yahan save ho raha hai: ' + DATA + '\n');
});
