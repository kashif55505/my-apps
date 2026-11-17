// Auto-detects IMAP/SMTP settings for any email address.
// Order: known providers -> Thunderbird ISPDB / domain autoconfig -> MX/SRV records -> common hostnames.
const dns = require('dns').promises;
const net = require('net');
const tls = require('tls');

const KNOWN = {
  'gmail.com': ['imap.gmail.com', 'smtp.gmail.com'],
  'googlemail.com': ['imap.gmail.com', 'smtp.gmail.com'],
  'outlook.com': ['outlook.office365.com', 'smtp.office365.com', 587],
  'hotmail.com': ['outlook.office365.com', 'smtp.office365.com', 587],
  'live.com': ['outlook.office365.com', 'smtp.office365.com', 587],
  'msn.com': ['outlook.office365.com', 'smtp.office365.com', 587],
  'yahoo.com': ['imap.mail.yahoo.com', 'smtp.mail.yahoo.com'],
  'ymail.com': ['imap.mail.yahoo.com', 'smtp.mail.yahoo.com'],
  'aol.com': ['imap.aol.com', 'smtp.aol.com'],
  'icloud.com': ['imap.mail.me.com', 'smtp.mail.me.com', 587],
  'me.com': ['imap.mail.me.com', 'smtp.mail.me.com', 587],
  'zoho.com': ['imap.zoho.com', 'smtp.zoho.com'],
  'yandex.com': ['imap.yandex.com', 'smtp.yandex.com'],
  'gmx.com': ['imap.gmx.com', 'mail.gmx.com', 587],
  'mail.com': ['imap.mail.com', 'smtp.mail.com', 587],
};

// MX host patterns for hosted providers (custom domains on Google Workspace, Microsoft 365, etc.)
const MX_PROVIDERS = [
  [/google(mail)?\.com$|googlemail\.com$/i, ['imap.gmail.com', 'smtp.gmail.com']],
  [/outlook\.com$|protection\.outlook\.com$/i, ['outlook.office365.com', 'smtp.office365.com', 587]],
  [/zoho\.(com|eu|in)$/i, null], // handled below to keep region
  [/yahoodns\.net$/i, ['imap.mail.yahoo.com', 'smtp.mail.yahoo.com']],
  [/secureserver\.net$/i, ['imap.secureserver.net', 'smtpout.secureserver.net']],
  [/titan\.email$/i, ['imap.titan.email', 'smtp.titan.email']],
  [/hostinger\.(com|in)$/i, ['imap.hostinger.com', 'smtp.hostinger.com']],
  [/ionos\.(com|de|co\.uk)$|1and1/i, ['imap.ionos.com', 'smtp.ionos.com', 587]],
  [/messagingengine\.com$/i, ['imap.fastmail.com', 'smtp.fastmail.com']],
  [/mailbox\.org$/i, ['imap.mailbox.org', 'smtp.mailbox.org']],
];

const withTimeout = (p, ms) => Promise.race([p, new Promise((_, r) => setTimeout(() => r(new Error('timeout')), ms))]);

function fromPair(pair) {
  const [imapHost, smtpHost, smtpPort = 465] = pair;
  return {
    imap: { host: imapHost, port: 993, secure: true },
    smtp: { host: smtpHost, port: smtpPort, secure: smtpPort === 465 },
    source: 'provider',
  };
}

async function fetchText(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 5000);
  try {
    const res = await fetch(url, { signal: ctrl.signal, redirect: 'follow' });
    if (!res.ok) return null;
    return await res.text();
  } catch { return null; } finally { clearTimeout(t); }
}

// Parse Thunderbird autoconfig XML
function parseAutoconfig(xml, email) {
  if (!xml || !/clientConfig/i.test(xml)) return null;
  const [local, domain] = email.split('@');
  const pick = (tag) => {
    const re = new RegExp(`<${tag}Server[^>]*type="(imap|smtp)"[^>]*>([\\s\\S]*?)</${tag}Server>`, 'gi');
    const out = [];
    let m;
    while ((m = re.exec(xml))) {
      const body = m[2];
      const g = (n) => (body.match(new RegExp(`<${n}>([^<]*)</${n}>`, 'i')) || [])[1];
      out.push({ type: m[1].toLowerCase(), host: g('hostname'), port: +g('port'), socket: (g('socketType') || '').toUpperCase(), user: g('username') });
    }
    return out;
  };
  const imap = pick('incoming').filter((s) => s.type === 'imap' && s.host);
  const smtp = pick('outgoing').filter((s) => s.type === 'smtp' && s.host);
  if (!imap.length || !smtp.length) return null;
  const best = (list) => list.find((s) => s.socket === 'SSL') || list.find((s) => s.socket === 'STARTTLS') || list[0];
  const sub = (s) => (s || '').replace(/%EMAILADDRESS%/g, email).replace(/%EMAILLOCALPART%/g, local).replace(/%EMAILDOMAIN%/g, domain);
  const i = best(imap), s = best(smtp);
  return {
    imap: { host: sub(i.host), port: i.port || 993, secure: i.socket === 'SSL' },
    smtp: { host: sub(s.host), port: s.port || 465, secure: s.socket === 'SSL' },
    usernameStyle: /%EMAILLOCALPART%/.test(i.user || '') ? 'local' : 'email',
    source: 'autoconfig',
  };
}

// Connects and waits for a server greeting. Returns { ok, certValid } without sending credentials.
function probe(host, port, mode, expect) {
  return new Promise((resolve) => {
    let done = false, buf = '';
    const finish = (r) => { if (!done) { done = true; try { sock.destroy(); } catch {} resolve(r); } };
    const opts = { host, port, servername: net.isIP(host) ? undefined : host, rejectUnauthorized: false, timeout: 6000 };
    const sock = mode === 'tls' ? tls.connect(opts) : net.connect(opts);
    sock.setTimeout(6000, () => finish({ ok: false }));
    sock.on('error', () => finish({ ok: false }));
    sock.on('data', (d) => {
      buf += d.toString();
      if (expect.test(buf)) {
        const certValid = mode === 'tls' ? sock.authorized === true : null;
        finish({ ok: true, certValid });
      }
    });
  });
}

const IMAP_GREETING = /^\* (OK|PREAUTH)/m;
const SMTP_GREETING = /^220[ -]/m;

async function findWorking(hosts, kind) {
  const combos = kind === 'imap'
    ? [[993, 'tls', true], [143, 'plain', false]]
    : [[465, 'tls', true], [587, 'plain', false], [25, 'plain', false]];
  const tries = [];
  hosts.forEach((host, hi) => combos.forEach(([port, mode, secure], ci) => {
    tries.push(probe(host, port, mode, kind === 'imap' ? IMAP_GREETING : SMTP_GREETING)
      .then((r) => ({ ...r, host, port, secure, rank: hi * 10 + ci })));
  }));
  const results = (await Promise.all(tries)).filter((r) => r.ok);
  if (!results.length) return null;
  // Prefer valid certificate, then host order, then port order
  results.sort((a, b) => (b.certValid === true) - (a.certValid === true) || a.rank - b.rank);
  const r = results[0];
  return { host: r.host, port: r.port, secure: r.secure, insecureCert: r.secure && r.certValid === false };
}

async function discover(email, log = () => {}) {
  const domain = email.split('@')[1].toLowerCase();
  if (KNOWN[domain]) { log('known provider'); return fromPair(KNOWN[domain]); }

  // Domain-published autoconfig, then Thunderbird's public database
  const ac = await Promise.all([
    fetchText(`https://autoconfig.${domain}/mail/config-v1.1.xml?emailaddress=${encodeURIComponent(email)}`),
    fetchText(`https://${domain}/.well-known/autoconfig/mail/config-v1.1.xml`),
    fetchText(`https://autoconfig.thunderbird.net/v1.1/${domain}`),
  ]);
  for (const xml of ac) {
    const cfg = parseAutoconfig(xml, email);
    if (cfg) { log('autoconfig'); return cfg; }
  }

  // MX records
  let mx = [];
  try { mx = (await withTimeout(dns.resolveMx(domain), 5000)).sort((a, b) => a.priority - b.priority).map((m) => m.exchange.toLowerCase()); } catch {}
  for (const m of mx) {
    if (/zoho\.(com|eu|in)$/i.test(m)) {
      const tld = m.match(/zoho\.(com|eu|in)$/i)[1];
      return { ...fromPair([`imappro.zoho.${tld}`, `smtppro.zoho.${tld}`]), source: 'mx' };
    }
    for (const [re, pair] of MX_PROVIDERS) if (pair && re.test(m)) { log('mx provider'); return { ...fromPair(pair), source: 'mx' }; }
    // Domain MX may itself be an ISPDB-known host
    const base = m.split('.').slice(-2).join('.');
    if (base !== domain) {
      const cfg = parseAutoconfig(await fetchText(`https://autoconfig.thunderbird.net/v1.1/${base}`), email);
      if (cfg) return { ...cfg, source: 'mx-autoconfig' };
    }
  }

  // SRV records (RFC 6186)
  const srv = async (name) => { try { const r = await withTimeout(dns.resolveSrv(`${name}.${domain}`), 4000); return r.filter((x) => x.name && x.name !== '.').map((x) => x.name); } catch { return []; } };
  const [srvImap, srvSmtp] = await Promise.all([srv('_imaps._tcp'), srv('_submission._tcp')]);

  // cPanel and most hosts: mail.domain works; MX host usually has a valid cert for the shared server
  const uniq = (a) => [...new Set(a.filter(Boolean))];
  const imapHosts = uniq([...srvImap, `mail.${domain}`, `imap.${domain}`, ...mx, domain]);
  const smtpHosts = uniq([...srvSmtp, `mail.${domain}`, `smtp.${domain}`, ...mx, domain]);

  log('probing hosts');
  const [imap, smtp] = await Promise.all([findWorking(imapHosts, 'imap'), findWorking(smtpHosts, 'smtp')]);
  if (!imap) throw new Error('Could not find the mail server for this domain. Open "Manual settings" and enter the IMAP/SMTP server.');
  return { imap, smtp: smtp || { host: imap.host, port: 465, secure: true, insecureCert: imap.insecureCert }, source: 'probe' };
}

module.exports = { discover, parseAutoconfig };
