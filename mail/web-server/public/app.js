'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const PAGE = 30; // messages per request; more load as you scroll
const state = {
  accounts: [], acc: null, unread: {}, pushOn: false, adding: false,
  folders: [], folder: 'INBOX', q: '',
  messages: [], total: 0, loadingMore: false, listToken: 0,
  selected: new Set(), current: null, compose: null, files: [],
};

// ---------- API ----------
// In the Android app, window.AndroidMail is the phone's own mail engine; in the browser, the server answers.
const NATIVE = window.AndroidMail || null;

async function api(path, opts = {}) {
  const acc = opts.acc || state.acc;
  let status, data;
  if (NATIVE) {
    ({ status, data } = await nativeCall(path, opts, acc));
  } else {
    const headers = { ...(opts.headers || {}) };
    if (acc) headers['X-Account'] = acc;
    const init = { credentials: 'same-origin', method: opts.method, body: opts.body, headers };
    if (opts.json) { init.method = init.method || 'POST'; headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.json); }
    const res = await fetch(path, init);
    status = res.status;
    data = res.headers.get('content-type')?.includes('json') ? await res.json() : await res.text();
  }
  if (status === 401 && path !== '/api/login') { showLogin(); throw new Error('Please sign in.'); }
  if (status === 404 && data && data.code === 'NO_ACCOUNT') { reloadAccounts(); }
  if (status < 200 || status >= 300) throw new Error((data && data.error) || `Error ${status}`);
  return data;
}

// ---------- Android bridge ----------
const nativePending = new Map();
let nativeSeq = 0;
window.__nativeReply = (id, status, text) => {
  const done = nativePending.get(id);
  if (done) { nativePending.delete(id); done(status, text); }
};
const fileToBase64 = (file) => new Promise((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result).split(',')[1] || '');
  r.onerror = () => reject(r.error);
  r.readAsDataURL(file);
});
async function formToJson(fd) {
  const out = { attachments: [], forward: [] };
  for (const [k, v] of fd.entries()) {
    if (k === 'attachments' && v instanceof Blob) out.attachments.push({ name: v.name || 'file', type: v.type || 'application/octet-stream', data: await fileToBase64(v) });
    else if (k === 'forward') out.forward.push(JSON.parse(v));
    else out[k] = v;
  }
  return out;
}
async function nativeCall(path, opts, acc) {
  let body = '';
  if (opts.json) body = JSON.stringify(opts.json);
  else if (opts.body instanceof FormData) body = JSON.stringify(await formToJson(opts.body));
  const method = opts.method || (body ? 'POST' : 'GET');
  const url = `${path}${path.includes('?') ? '&' : '?'}acc=${encodeURIComponent(acc || '')}`;
  const [status, text] = await new Promise((resolve) => {
    const id = String(++nativeSeq);
    nativePending.set(id, (s, t) => resolve([s, t]));
    NATIVE.call(id, method, url, body);
  });
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  return { status, data };
}
// Android back button: close whatever is open; returns false when there is nothing left to close
window.__back = () => {
  if (!$('#compose').hidden) { closeCompose(); return true; }
  if ($('#moveDialog').open) { $('#moveDialog').close(); return true; }
  if ($('#app').classList.contains('side-open')) { $('#app').classList.remove('side-open'); return true; }
  if (state.current || $('#app').classList.contains('reading')) { closeReader(); return true; }
  if (state.adding && !$('#login').hidden) { actions.cancelAdd(); return true; }
  if (state.selected.size) { state.selected.clear(); renderMessages(); return true; }
  if ((state.folder !== 'INBOX' || state.q) && !$('#app').hidden) { openFolder('INBOX'); return true; }
  return false;
};

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), ms);
}
function busy(btn, on) { btn.classList.toggle('busy', on); btn.disabled = on; }

const attUrl = (folder, uid, index) => `/api/attachment?acc=${encodeURIComponent(state.acc)}&folder=${encodeURIComponent(folder)}&uid=${uid}&index=${index}`;
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
  del: (k) => { try { localStorage.removeItem(k); } catch {} },
};

// ---------- Cache: show the last known list instantly, then refresh in the background ----------
const cache = { lists: {}, folders: {}, messages: new Map() };
const listKey = (acc, folder) => `${acc}|${folder}`;
try {
  const saved = JSON.parse(store.get('wm.cache') || '{}');
  cache.lists = saved.lists || {};
  cache.folders = saved.folders || {};
} catch {}
let saveTimer;
function saveCache() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    // Keep it small: first page of each list
    const lists = {};
    for (const [k, v] of Object.entries(cache.lists)) lists[k] = { total: v.total, messages: v.messages.slice(0, PAGE) };
    store.set('wm.cache', JSON.stringify({ lists, folders: cache.folders }));
  }, 500);
}
function rememberMessage(acc, m) {
  const k = `${acc}|${m.folder}|${m.uid}`;
  cache.messages.delete(k);
  cache.messages.set(k, m);
  if (cache.messages.size > 40) cache.messages.delete(cache.messages.keys().next().value);
}

// ---------- Login ----------
function showLogin(adding) {
  state.adding = !!adding && state.accounts.length > 0;
  $('#app').hidden = true; $('#login').hidden = false;
  $('#cancelAdd').hidden = !state.adding;
  $('#loginIntro').textContent = state.adding
    ? 'Add another account. Your other accounts stay signed in.'
    : 'Sign in with any email address: your own domain (cPanel), Gmail, Outlook, Zoho, Yahoo and more.';
  $('#loginForm').email.focus();
}
const accId = (a) => a.email.toLowerCase();

async function showApp(me) {
  state.accounts = me.accounts;
  $('#login').hidden = true; $('#app').hidden = false;
  const params = new URLSearchParams(location.search);
  const want = (params.get('acc') || store.get('wm.acc') || '').toLowerCase();
  state.acc = state.accounts.some((a) => accId(a) === want) ? want : accId(state.accounts[0]);
  renderAccounts();
  await Promise.all([loadFolders().catch(() => {}), loadMessages()]);
  if (params.get('uid')) { history.replaceState(null, '', '/'); openMessage(+params.get('uid')); }
  startEvents();
  refreshUnread();
  setupNotify(false);
  if (NATIVE) {
    const launch = NATIVE.takeLaunch();
    if (launch) { try { const l = JSON.parse(launch); openFromNotification(l.account, l.uid); } catch {} }
    if (NATIVE.notifyStatus() === 'default') setupNotify(true); // ask once, right after the first sign-in
  }
  prefetchOtherAccounts();
}

// Load the other accounts' inboxes in the background so switching is instant
async function prefetchOtherAccounts() {
  for (const a of state.accounts) {
    const id = accId(a);
    if (id === state.acc) continue;
    try {
      const [folders, list] = await Promise.all([
        api('/api/folders', { acc: id }),
        api(`/api/messages?${new URLSearchParams({ folder: 'INBOX', page: 1, size: PAGE })}`, { acc: id }),
      ]);
      cache.folders[id] = folders;
      cache.lists[listKey(id, 'INBOX')] = { messages: list.messages, total: list.total };
      const inbox = folders.find((f) => f.specialUse === '\\Inbox');
      if (inbox) state.unread[id] = inbox.unseen;
      saveCache();
      renderAccounts();
    } catch {}
  }
}

async function reloadAccounts() {
  try { const me = await api('/api/me'); state.accounts = me.accounts; if (!state.accounts.some((a) => accId(a) === state.acc)) switchAccount(accId(state.accounts[0])); else renderAccounts(); } catch {}
}

// ---------- Accounts ----------
const COLORS = ['#1f6f5c', '#8a3b12', '#3b4ea8', '#7a2e6b', '#5b6b10', '#0f6a8a', '#a33d3d', '#545454'];
const colorFor = (id) => COLORS[Math.max(0, state.accounts.findIndex((a) => accId(a) === id)) % COLORS.length];

function renderAccounts() {
  $('#accounts').innerHTML = state.accounts.map((a) => {
    const id = accId(a), n = state.unread[id];
    return `<div class="acct ${id === state.acc ? 'active' : ''} ${a.notify ? '' : 'muted-acc'}" data-acc="${esc(id)}" title="${esc(a.email)} · ${esc(a.server.imap)}">
      <span class="av" style="background:${colorFor(id)}">${esc(a.email[0].toUpperCase())}</span>
      <span class="em">${esc(a.email)}</span>
      ${n ? `<span class="ct">${n}</span>` : ''}
      <span class="tools"><span data-bell title="${a.notify ? 'Turn off notifications for this account' : 'Turn on notifications'}">${a.notify ? '🔔' : '🔕'}</span><span data-remove title="Remove this account">✕</span></span>
    </div>`;
  }).join('');
  $('#curAccount').textContent = state.accounts.length > 1 ? state.acc : '';
  updateTitle();
}

async function switchAccount(id) {
  if (!id) return;
  state.acc = id; store.set('wm.acc', id);
  state.folder = 'INBOX'; state.q = ''; $('#searchInput').value = '';
  closeReader();
  $('#app').classList.remove('side-open');
  state.folders = cache.folders[id] || [];
  renderAccounts();
  renderFolders();
  await Promise.all([loadFolders().catch((e) => toast(e.message)), loadMessages()]);
}

$('#accounts').addEventListener('click', async (e) => {
  const row = e.target.closest('[data-acc]'); if (!row) return;
  const id = row.dataset.acc;
  const acc = state.accounts.find((a) => accId(a) === id);
  if (e.target.closest('[data-bell]')) {
    const r = await api(`/api/accounts/${encodeURIComponent(id)}/notify`, { json: { on: !acc.notify } });
    acc.notify = r.notify; renderAccounts();
    return toast(acc.notify ? `Notifications on for ${acc.email}` : `Notifications off for ${acc.email}`);
  }
  if (e.target.closest('[data-remove]')) {
    if (!confirm(`Remove ${acc.email} from this app? Your emails stay on the server.`)) return;
    const r = await api(`/api/accounts/${encodeURIComponent(id)}`, { method: 'DELETE' });
    state.accounts = r.accounts; delete state.unread[id];
    delete cache.folders[id];
    for (const k of Object.keys(cache.lists)) if (k.startsWith(id + '|')) delete cache.lists[k];
    saveCache();
    if (!state.accounts.length) { state.acc = null; return showLogin(); }
    if (id === state.acc) return switchAccount(accId(state.accounts[0]));
    return renderAccounts();
  }
  if (id !== state.acc) switchAccount(id);
});

async function refreshUnread() {
  try { Object.assign(state.unread, await api('/api/unread')); renderAccounts(); } catch {}
}
function updateTitle() {
  const total = Object.values(state.unread).reduce((s, n) => s + (n || 0), 0);
  const cur = state.folders.find((f) => f.path === state.folder);
  document.title = `${total ? `(${total}) ` : ''}${cur ? folderName(cur) : 'Mail'} · ${state.acc || 'Mail'}`;
}

$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target, btn = f.querySelector('button[type=submit]'), st = $('#loginStatus');
  const v = Object.fromEntries(new FormData(f));
  const manual = v.imapHost ? { imapHost: v.imapHost, imapPort: v.imapPort, smtpHost: v.smtpHost, smtpPort: v.smtpPort, username: v.username } : (v.username ? { username: v.username } : null);
  busy(btn, true); st.className = 'status'; st.textContent = 'Finding your mail server…';
  try {
    const me = await api('/api/login', { json: { email: v.email, password: v.password, manual } });
    f.reset(); $('#manual').open = false;
    st.textContent = '';
    if (state.adding) {
      state.accounts = state.accounts.filter((a) => accId(a) !== accId(me)).concat(me);
      state.adding = false;
      $('#login').hidden = true; $('#app').hidden = false;
      await switchAccount(accId(me));
      refreshUnread();
      toast(`${me.email} added`);
    } else {
      await showApp({ accounts: [me] });
    }
  } catch (err) {
    st.className = 'status err'; st.textContent = err.message;
    if (/server|connect/i.test(err.message)) $('#manual').open = true;
  } finally { busy(btn, false); }
});

// ---------- Folders ----------
const ICONS = { '\\Inbox': '📥', '\\Drafts': '📝', '\\Sent': '📤', '\\Archive': '🗄', '\\Junk': '⚠', '\\Trash': '🗑', '\\Flagged': '★', '\\All': '📚' };
const LABELS = { '\\Inbox': 'Inbox', '\\Drafts': 'Drafts', '\\Sent': 'Sent', '\\Archive': 'Archive', '\\Junk': 'Spam', '\\Trash': 'Trash' };
const folderName = (f) => LABELS[f.specialUse] || f.path.replace(/^INBOX[./]/i, '');

async function loadFolders() {
  const acc = state.acc;
  const folders = await api('/api/folders', { acc });
  cache.folders[acc] = folders;
  saveCache();
  if (acc !== state.acc) return;
  state.folders = folders;
  const inbox = state.folders.find((f) => f.specialUse === '\\Inbox');
  if (inbox) state.unread[state.acc] = inbox.unseen;
  renderFolders();
  renderAccounts();
}
function renderFolders() {
  $('#folders').innerHTML = state.folders.map((f) => {
    const depth = f.delimiter ? f.path.split(f.delimiter).length - 1 : 0;
    return `<button class="folder ${f.path === state.folder ? 'active' : ''} ${depth && !f.specialUse ? 'sub' : ''}" data-folder="${esc(f.path)}">
      <span class="ico">${ICONS[f.specialUse] || '📁'}</span><span class="nm">${esc(folderName(f))}</span>
      ${f.unseen && f.specialUse !== '\\Sent' && f.specialUse !== '\\Trash' ? `<span class="ct">${f.unseen}</span>` : ''}</button>`;
  }).join('');
  const cur = state.folders.find((f) => f.path === state.folder);
  $('#folderTitle').textContent = state.q ? `Search: ${state.q}` : (cur ? folderName(cur) : (state.folder === 'INBOX' ? 'Inbox' : state.folder));
  updateTitle();
}
function openFolder(path) {
  state.folder = path; state.q = ''; $('#searchInput').value = '';
  $('#app').classList.remove('side-open', 'reading');
  closeReader();
  renderFolders(); loadMessages();
}
$('#folders').addEventListener('click', (e) => {
  const b = e.target.closest('[data-folder]'); if (b) openFolder(b.dataset.folder);
});

// ---------- Message list (one long list: scroll down for older mail, pull down to refresh) ----------
function fmtDate(d) {
  const dt = new Date(d), now = new Date();
  if (isNaN(dt)) return '';
  if (dt.toDateString() === now.toDateString()) return dt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (dt.getFullYear() === now.getFullYear()) return dt.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return dt.toLocaleDateString();
}
const who = (list) => (list || []).map((a) => a.name || a.address).join(', ');
const isOutgoing = () => ['\\Sent', '\\Drafts'].includes((state.folders.find((f) => f.path === state.folder) || {}).specialUse);
const hasMore = () => state.messages.length < state.total;
const listParams = (page) => new URLSearchParams({ folder: state.folder, page, size: PAGE, q: state.q });

// Newest page; merged with older messages already on screen so scrolling position survives
async function loadMessages(silent) {
  const token = ++state.listToken;
  const acc = state.acc, folder = state.folder, q = state.q;
  const key = listKey(acc, folder);
  const cached = !q && cache.lists[key];
  if (!silent) {
    state.selected.clear();
    if (cached) { state.messages = cached.messages.slice(); state.total = cached.total; renderMessages(); }
    else { state.messages = []; state.total = 0; $('#messages').innerHTML = '<li class="list-msg"><span class="spinner inline"></span> Loading…</li>'; renderTools(); }
  }
  try {
    const data = await api(`/api/messages?${listParams(1)}`, { acc });
    if (token !== state.listToken) return; // user moved on meanwhile
    const fresh = data.messages;
    const minUid = fresh.length ? Math.min(...fresh.map((m) => m.uid)) : Infinity;
    const older = state.messages.filter((m) => m.uid < minUid && !fresh.some((f) => f.uid === m.uid));
    state.messages = fresh.concat(older);
    state.total = Math.max(data.total, state.messages.length);
    for (const uid of [...state.selected]) if (!state.messages.some((m) => m.uid === uid)) state.selected.delete(uid);
    if (!q) { cache.lists[key] = { messages: state.messages.slice(0, PAGE * 3), total: state.total }; saveCache(); }
    renderMessages();
    fillScreen();
  } catch (err) {
    if (token !== state.listToken) return;
    if (!state.messages.length) $('#messages').innerHTML = `<li class="list-msg">${esc(err.message)}</li>`;
    else if (!silent) toast(err.message);
  }
}

async function loadMore() {
  if (state.loadingMore || !hasMore()) return;
  state.loadingMore = true;
  const token = state.listToken;
  renderFooter();
  try {
    const page = Math.floor(state.messages.length / PAGE) + 1;
    const data = await api(`/api/messages?${listParams(page)}`);
    if (token !== state.listToken) return;
    const seen = new Set(state.messages.map((m) => m.uid));
    const add = data.messages.filter((m) => !seen.has(m.uid));
    state.messages = state.messages.concat(add);
    state.total = add.length ? Math.max(data.total, state.messages.length) : state.messages.length;
    if (!state.q) { cache.lists[listKey(state.acc, state.folder)] = { messages: state.messages.slice(0, PAGE * 3), total: state.total }; saveCache(); }
  } catch (err) {
    toast(err.message);
  } finally {
    state.loadingMore = false;
    if (token === state.listToken) { renderMessages(); fillScreen(); }
  }
}
// If the list doesn't fill the screen yet, keep loading
function fillScreen() {
  const ul = $('#messages');
  if (hasMore() && ul.scrollHeight <= ul.clientHeight + 100) loadMore();
}

function msgRow(m) {
  return `<li class="msg ${m.seen ? '' : 'unread'} ${state.current && state.current.uid === m.uid ? 'active' : ''} ${state.selected.has(m.uid) ? 'selected' : ''}" data-uid="${m.uid}">
      <input type="checkbox" ${state.selected.has(m.uid) ? 'checked' : ''} aria-label="Select">
      <span class="from">${esc(isOutgoing() ? 'To: ' + who(m.to) : who(m.from) || '(unknown)')}</span>
      <span class="date">${m.flagged ? '<span class="star">★</span> ' : ''}${m.attachments ? '📎 ' : ''}${fmtDate(m.date)}</span>
      <span class="subj">${esc(m.subject)}</span>
    </li>`;
}
function renderMessages() {
  const ul = $('#messages');
  if (!state.messages.length) ul.innerHTML = `<li class="list-msg">${state.q ? 'No results' : 'No messages'}</li>`;
  else ul.innerHTML = state.messages.map(msgRow).join('') + '<li class="list-foot" id="listFoot"></li>';
  renderFooter();
  renderTools();
}
function renderFooter() {
  const foot = $('#listFoot');
  if (!foot) return;
  foot.innerHTML = state.loadingMore ? '<span class="spinner inline"></span> Loading older messages…'
    : hasMore() ? '' : (state.messages.length > 8 ? 'No more messages' : '');
}
function renderTools() {
  const n = state.selected.size;
  const all = $('#selectAll');
  all.checked = n > 0 && n === state.messages.length;
  all.indeterminate = n > 0 && n < state.messages.length;
  all.disabled = !state.messages.length;
  $('#selectLabel').textContent = n ? `${n} selected` : 'Select all';
  $('#bulkActions').hidden = !n;
}
$('#selectAll').addEventListener('change', (e) => {
  if (e.target.checked) state.messages.forEach((m) => state.selected.add(m.uid));
  else state.selected.clear();
  renderMessages();
});
$('#messages').addEventListener('click', (e) => {
  const li = e.target.closest('.msg'); if (!li) return;
  const uid = +li.dataset.uid;
  if (e.target.matches('input[type=checkbox]')) {
    e.target.checked ? state.selected.add(uid) : state.selected.delete(uid);
    li.classList.toggle('selected', e.target.checked);
    return renderTools();
  }
  if (state.selected.size) { // while selecting, a tap toggles selection
    state.selected.has(uid) ? state.selected.delete(uid) : state.selected.add(uid);
    return renderMessages();
  }
  openMessage(uid);
});
$('#messages').addEventListener('scroll', () => {
  const ul = $('#messages');
  if (ul.scrollTop + ul.clientHeight > ul.scrollHeight - 400) loadMore();
}, { passive: true });
$('#searchForm').addEventListener('submit', (e) => {
  e.preventDefault();
  state.q = $('#searchInput').value.trim();
  renderFolders(); loadMessages();
});

// Pull down from the top of the list to refresh
(() => {
  const ul = $('#messages'), ptr = $('#ptr');
  let startY = null, pull = 0;
  ul.addEventListener('touchstart', (e) => { startY = ul.scrollTop <= 0 ? e.touches[0].clientY : null; pull = 0; }, { passive: true });
  ul.addEventListener('touchmove', (e) => {
    if (startY === null) return;
    pull = Math.max(0, Math.min(90, (e.touches[0].clientY - startY) * 0.5));
    ptr.style.height = pull + 'px';
    ptr.classList.toggle('ready', pull > 55);
  }, { passive: true });
  ul.addEventListener('touchend', async () => {
    if (startY === null) return;
    startY = null;
    if (pull > 55) {
      ptr.classList.add('loading'); ptr.style.height = '48px';
      await Promise.all([loadMessages(true), loadFolders().catch(() => {}), refreshUnread()]);
      ptr.classList.remove('loading');
    }
    ptr.classList.remove('ready'); ptr.style.height = '0';
  });
})();

// ---------- Reader ----------
function closeReader() {
  state.current = null;
  $('#reader').hidden = true; $('#emptyRead').hidden = false;
  $('#app').classList.remove('reading');
  $$('.msg.active').forEach((x) => x.classList.remove('active'));
}

function showMessage(m) {
  state.current = m;
  const from = m.from[0] || {};
  $('#rSubject').textContent = m.subject;
  $('#rAvatar').textContent = (from.name || from.address || '?').trim()[0].toUpperCase();
  $('#rFrom').innerHTML = `${esc(from.name || from.address)} ${from.name ? `<span>&lt;${esc(from.address)}&gt;</span>` : ''}`;
  $('#rTo').textContent = `To: ${who(m.to)}${m.cc.length ? ` · Cc: ${who(m.cc)}` : ''}`;
  $('#rDate').textContent = m.date ? new Date(m.date).toLocaleString() : '';
  $('#rDraft').hidden = (state.folders.find((f) => f.path === m.folder) || {}).specialUse !== '\\Drafts';
  $('#flagBtn').textContent = m.flagged ? '★' : '☆';
  renderBody(m.html);
  $('#rAtts').innerHTML = m.attachments.map((a) => `<a class="att" href="${attUrl(m.folder, m.uid, a.index)}" data-att="${a.index}"><span>📎</span><span class="n">${esc(a.filename)}</span><span class="muted small">${size(a.size)}</span></a>`).join('');
}

async function openMessage(uid) {
  const folder = state.folder, acc = state.acc;
  $$('.msg').forEach((x) => x.classList.toggle('active', +x.dataset.uid === uid));
  $('#app').classList.add('reading');
  $('#emptyRead').hidden = true; $('#reader').hidden = false;
  const cached = cache.messages.get(`${acc}|${folder}|${uid}`);
  const item = state.messages.find((x) => x.uid === uid);
  if (cached) showMessage(cached);
  else {
    state.current = { uid, folder, pending: true };
    $('#rSubject').textContent = item ? item.subject : '';
    $('#rFrom').textContent = item ? who(item.from) : '';
    $('#rTo').textContent = ''; $('#rDate').textContent = ''; $('#rAtts').innerHTML = ''; $('#rDraft').hidden = true;
    renderBody('<p style="color:#888">Loading…</p>');
  }
  if (item && !item.seen) {
    item.seen = true; renderMessages();
    const f = state.folders.find((x) => x.path === folder);
    if (f && f.unseen) { f.unseen--; if (f.specialUse === '\\Inbox') state.unread[acc] = f.unseen; renderFolders(); renderAccounts(); }
  }
  if (cached) return;
  try {
    const m = await api(`/api/message?folder=${encodeURIComponent(folder)}&uid=${uid}&images=1`, { acc });
    rememberMessage(acc, m);
    if (state.current && state.current.uid === uid && state.acc === acc) showMessage(m);
  } catch (err) {
    if (state.current && state.current.uid === uid) { $('#rSubject').textContent = err.message; renderBody(''); }
  }
}
function renderBody(html) {
  const frame = $('#rBody');
  frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base target="_blank"><style>
    body{margin:0;padding:16px;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1d232b;background:#fff;overflow-wrap:anywhere}
    img{max-width:100%;height:auto} table{max-width:100%} pre{white-space:pre-wrap} a{color:#1f5fbf}
    blockquote{border-left:3px solid #ddd;margin:0 0 0 4px;padding-left:12px;color:#555}
  </style></head><body>${html}</body></html>`;
  frame.style.height = '200px';
  frame.onload = () => {
    const resize = () => { try { frame.style.height = Math.max(200, frame.contentDocument.documentElement.scrollHeight + 8) + 'px'; } catch {} };
    resize(); setTimeout(resize, 400); setTimeout(resize, 1500); setTimeout(resize, 4000);
    try { frame.contentDocument.querySelectorAll('img').forEach((img) => img.addEventListener('load', resize)); } catch {}
  };
}
const size = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');

$('#rAtts').addEventListener('click', async (e) => {
  const a = e.target.closest('[data-att]');
  if (!a || !NATIVE || !state.current) return;
  e.preventDefault();
  toast('Downloading…');
  try {
    const r = await api(`/api/attachment-save?folder=${encodeURIComponent(state.current.folder)}&uid=${state.current.uid}&index=${a.dataset.att}`);
    toast(`Saved to ${r.where || 'Downloads'}: ${r.name}`, 4000);
  } catch (err) { toast(err.message); }
});

// ---------- Actions ----------
async function setFlags(uids, flags) {
  await api('/api/flags', { json: { folder: state.folder, uids: [...uids], ...flags } });
}
// Remove from the list right away, then let the server catch up
function dropFromList(uids) {
  const set = new Set([...uids]);
  state.messages = state.messages.filter((m) => !set.has(m.uid));
  state.total = Math.max(0, state.total - set.size);
  set.forEach((u) => state.selected.delete(u));
  if (state.current && set.has(state.current.uid)) closeReader();
  renderMessages();
}
async function moveTo(uids, dest) {
  const list = [...uids];
  dropFromList(list);
  try {
    await api('/api/move', { json: { folder: state.folder, uids: list, dest } });
    toast(list.length === 1 ? 'Moved' : `${list.length} messages moved`);
  } catch (e) { toast(e.message); }
  loadMessages(true); loadFolders().catch(() => {});
}
async function del(uids) {
  const list = [...uids];
  dropFromList(list);
  try {
    const r = await api('/api/delete', { json: { folder: state.folder, uids: list } });
    toast(r.permanent ? 'Deleted permanently' : 'Moved to Trash');
  } catch (e) { toast(e.message); }
  loadMessages(true); loadFolders().catch(() => {});
}
async function markSeen(uids, seen) {
  const set = new Set([...uids]);
  state.messages.forEach((m) => { if (set.has(m.uid)) m.seen = seen; });
  state.selected.clear();
  renderMessages();
  try { await setFlags(set, { seen }); } catch (e) { toast(e.message); }
  loadFolders().catch(() => {});
}
function pickFolder() {
  return new Promise((resolve) => {
    const d = $('#moveDialog');
    $('#moveList').innerHTML = state.folders.filter((f) => f.path !== state.folder).map((f) => `<button class="folder" value="${esc(f.path)}"><span class="ico">${ICONS[f.specialUse] || '📁'}</span><span class="nm">${esc(folderName(f))}</span></button>`).join('');
    d.onclose = () => resolve(d.returnValue && d.returnValue !== 'cancel' ? d.returnValue : null);
    d.returnValue = ''; d.showModal();
  });
}

const actions = {
  openSide: () => $('#app').classList.add('side-open'),
  closeSide: () => $('#app').classList.remove('side-open'),
  refresh: () => { loadFolders().catch(() => {}); loadMessages(true); refreshUnread(); },
  back: closeReader,
  logout: async () => {
    if (!confirm('Sign out of all accounts?')) return;
    await api('/api/logout', { method: 'POST' }).catch(() => {});
    store.del('wm.cache');
    if (NATIVE) location.reload(); else location.replace('/');
  },
  addAccount: () => { $('#app').classList.remove('side-open'); showLogin(true); },
  cancelAdd: () => { state.adding = false; $('#login').hidden = true; $('#app').hidden = false; },
  enableNotify: () => setupNotify(true),
  newFolder: async () => {
    const name = prompt('New folder name:'); if (!name) return;
    try { await api('/api/folders', { json: { name } }); await loadFolders(); toast('Folder created'); } catch (e) { toast(e.message); }
  },
  flag: async () => {
    const m = state.current; if (!m || m.pending) return;
    m.flagged = !m.flagged; $('#flagBtn').textContent = m.flagged ? '★' : '☆';
    const it = state.messages.find((x) => x.uid === m.uid); if (it) { it.flagged = m.flagged; renderMessages(); }
    try { await setFlags([m.uid], { flagged: m.flagged }); } catch (e) { toast(e.message); }
  },
  markUnread: () => { const m = state.current; if (!m) return; closeReader(); markSeen([m.uid], false); },
  moveOne: async () => { const m = state.current; const d = await pickFolder(); if (d && m) moveTo([m.uid], d); },
  deleteOne: () => state.current && del([state.current.uid]),
  bulkRead: () => markSeen(state.selected, true),
  bulkUnread: () => markSeen(state.selected, false),
  bulkMove: async () => { const d = await pickFolder(); if (d) moveTo(new Set(state.selected), d); },
  bulkDelete: () => { const n = state.selected.size; if (confirm(n === 1 ? 'Delete this message?' : `Delete ${n} messages?`)) del(new Set(state.selected)); },
  compose: () => { $('#app').classList.remove('side-open'); openCompose({}); },
  reply: () => reply(false),
  replyAll: () => reply(true),
  forward: () => forward(),
  editDraft: () => editDraft(),
  closeCompose: () => closeCompose(),
  showCc: () => $$('.compose .cc').forEach((x) => (x.hidden = false)),
  saveDraft: () => saveDraft(),
};
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-act]');
  if (b && actions[b.dataset.act]) { e.preventDefault(); actions[b.dataset.act](); }
});

// ---------- Compose ----------
const fmtAddr = (list) => (list || []).map((a) => (a.name ? `"${a.name.replace(/"/g, '')}" <${a.address}>` : a.address)).join(', ');
const quoteHeader = (m) => `On ${new Date(m.date).toLocaleString()}, ${esc(fmtAddr(m.from))} wrote:`;

function openCompose(o) {
  state.compose = o; state.files = [];
  const f = $('#composeForm');
  f.reset();
  f.to.value = o.to || ''; f.cc.value = o.cc || ''; f.bcc.value = o.bcc || ''; f.subject.value = o.subject || '';
  $$('.compose .cc').forEach((x) => (x.hidden = !(o.cc || o.bcc)));
  $('#editor').innerHTML = o.html || '<br>';
  $('#composeTitle').textContent = o.title || 'New message';
  $('#composeStatus').textContent = '';
  renderFiles();
  $('#compose').hidden = false;
  (o.to ? $('#editor') : f.to).focus();
  if (o.to) { const r = document.createRange(); r.setStart($('#editor'), 0); r.collapse(true); getSelection().removeAllRanges(); getSelection().addRange(r); }
}
function closeCompose(force) {
  const dirty = $('#editor').innerText.trim() && !force && state.compose && !state.compose.sent;
  if (dirty && !confirm('Discard this message? You can also save it as a draft.')) return;
  $('#compose').hidden = true; state.compose = null;
}
function reply(all) {
  const m = state.current; if (!m || m.pending) return;
  const me = state.acc;
  const to = m.replyTo.length ? m.replyTo : m.from;
  const cc = all ? [...m.to, ...m.cc].filter((a) => a.address.toLowerCase() !== me && !to.some((t) => t.address === a.address)) : [];
  openCompose({
    title: all ? 'Reply all' : 'Reply',
    to: fmtAddr(to), cc: fmtAddr(cc),
    subject: /^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`,
    html: `<br><br><div>${quoteHeader(m)}</div><blockquote>${m.html}</blockquote>`,
    inReplyTo: m.messageId, references: [...m.references, m.messageId].filter(Boolean).join(' '),
    replyUid: m.uid, replyFolder: m.folder,
  });
}
async function forward() {
  const m = state.current; if (!m || m.pending) return;
  openCompose({
    title: 'Forward',
    subject: /^fwd?:/i.test(m.subject) ? m.subject : `Fwd: ${m.subject}`,
    html: `<br><br><div>---------- Forwarded message ----------<br>From: ${esc(fmtAddr(m.from))}<br>Date: ${esc(new Date(m.date).toLocaleString())}<br>Subject: ${esc(m.subject)}<br>To: ${esc(fmtAddr(m.to))}</div><br>${m.html}`,
  });
  await carryAttachments(m); // forward the original attachments too
}
async function carryAttachments(m) {
  for (const a of m.attachments) {
    if (NATIVE) { state.files.push({ name: a.filename, size: a.size, native: { folder: m.folder, uid: m.uid, index: a.index } }); renderFiles(); continue; }
    try {
      const blob = await (await fetch(attUrl(m.folder, m.uid, a.index))).blob();
      state.files.push(new File([blob], a.filename, { type: a.contentType }));
      renderFiles();
    } catch {}
  }
}
async function editDraft() {
  const m = state.current; if (!m || m.pending) return;
  openCompose({ title: 'Draft', to: fmtAddr(m.to), cc: fmtAddr(m.cc), subject: m.subject === '(no subject)' ? '' : m.subject, html: m.html, draftUid: m.uid });
  await carryAttachments(m);
}
function renderFiles() {
  $('#composeAtts').innerHTML = state.files.map((f, i) => `<span class="att"><span>📎</span><span class="n">${esc(f.name)}</span><span class="muted small">${size(f.size)}</span><button type="button" data-rm="${i}" aria-label="Remove">✕</button></span>`).join('');
}
$('#composeAtts').addEventListener('click', (e) => { const b = e.target.closest('[data-rm]'); if (b) { state.files.splice(+b.dataset.rm, 1); renderFiles(); } });
$('#fileInput').addEventListener('change', (e) => {
  for (const f of e.target.files) {
    if (state.files.reduce((s, x) => s + x.size, 0) + f.size > 25 * 1024 * 1024) { toast('Attachments can be 25 MB at most'); break; }
    state.files.push(f);
  }
  e.target.value = ''; renderFiles();
});
$$('.toolbar [data-cmd]').forEach((b) => b.addEventListener('click', () => {
  const cmd = b.dataset.cmd;
  if (cmd === 'createLink') { const u = prompt('Link URL:', 'https://'); if (u) document.execCommand(cmd, false, u); }
  else document.execCommand(cmd, false, null);
  $('#editor').focus();
}));

function composeData() {
  const f = $('#composeForm'), o = state.compose || {};
  const fd = new FormData();
  fd.append('to', f.to.value.trim()); fd.append('cc', f.cc.value.trim()); fd.append('bcc', f.bcc.value.trim());
  fd.append('subject', f.subject.value);
  fd.append('html', `<div style="font-family:Arial,sans-serif;font-size:14px">${$('#editor').innerHTML}</div>`);
  fd.append('text', $('#editor').innerText);
  for (const k of ['inReplyTo', 'references', 'replyUid', 'replyFolder', 'draftUid']) if (o[k]) fd.append(k, o[k]);
  state.files.forEach((file) => (file.native ? fd.append('forward', JSON.stringify(file.native)) : fd.append('attachments', file, file.name)));
  return fd;
}
$('#composeForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type=submit]'), st = $('#composeStatus');
  if (!e.target.to.value.trim() && !e.target.cc.value.trim() && !e.target.bcc.value.trim()) { st.className = 'status err'; st.textContent = 'Add at least one recipient'; return; }
  busy(btn, true); st.className = 'status'; st.textContent = 'Sending…';
  try {
    await api('/api/send', { method: 'POST', body: composeData() });
    state.compose.sent = true;
    closeCompose(true); toast('Sent ✓');
    loadFolders().catch(() => {}); if (isOutgoing()) loadMessages(true);
  } catch (err) { st.className = 'status err'; st.textContent = err.message; }
  finally { busy(btn, false); }
});
async function saveDraft() {
  const st = $('#composeStatus');
  st.className = 'status'; st.textContent = 'Saving…';
  try {
    const r = await api('/api/draft', { method: 'POST', body: composeData() });
    state.compose.draftUid = r.uid;
    st.textContent = 'Draft saved';
    loadFolders().catch(() => {}); if (isOutgoing()) loadMessages(true);
  } catch (err) { st.className = 'status err'; st.textContent = err.message; }
}

// Keyboard shortcuts (desktop)
document.addEventListener('keydown', (e) => {
  if (e.target.closest('input, [contenteditable], textarea') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'c') actions.compose();
  else if (e.key === 'r' && state.current) actions.reply();
  else if (e.key === 'Escape') { if (!$('#compose').hidden) closeCompose(); else closeReader(); }
  else if (e.key === '/') { e.preventDefault(); $('#searchInput').focus(); }
});

// Fallback refresh every minute while the app is on screen (new mail normally arrives within seconds)
setInterval(() => {
  if (!state.acc || document.hidden) return;
  refreshUnread();
  if (!state.q) { loadFolders().catch(() => {}); loadMessages(true); }
}, 60000);

// ---------- New mail ----------
let events;
function startEvents() {
  if (NATIVE) {
    window.__nativeEvent = (ev) => onNewMail(ev);
    // App brought back to the screen: catch up on anything that arrived meanwhile
    window.__resume = () => {
      if (!state.acc) return;
      refreshUnread();
      if (!state.q) { loadFolders().catch(() => {}); loadMessages(true); }
    };
    return;
  }
  if (events) return;
  events = new EventSource('/api/events');
  events.addEventListener('mail', (e) => { try { onNewMail(JSON.parse(e.data)); } catch {} });
  events.onerror = () => { if (!state.accounts.length) { events.close(); events = null; } };
}

function onNewMail(ev) {
  const acc = state.accounts.find((a) => accId(a) === ev.account);
  if (!acc) return;
  state.unread[ev.account] = (state.unread[ev.account] || 0) + 1;
  renderAccounts();
  if (ev.account === state.acc) {
    loadFolders().catch(() => {});
    if (state.folder.toUpperCase() === 'INBOX' && !state.q) loadMessages(true);
  } else {
    delete cache.lists[listKey(ev.account, 'INBOX')]; // refreshed when that account is opened
  }
  if (!acc.notify) return;
  const where = state.accounts.length > 1 ? ` (${acc.email})` : '';
  if (!document.hidden) { toast(`📩 ${ev.from}: ${ev.subject}${where}`, 5000); beep(); return; }
  // Tab is in the background: the service worker shows push notifications. Without push, show one from the page.
  if (!state.pushOn && 'Notification' in window && Notification.permission === 'granted') {
    const n = new Notification(ev.from || 'New email', { body: `${ev.subject}\n${acc.email}`, tag: `${ev.account}-${ev.uid}`, icon: '/icon-192.png' });
    n.onclick = () => { window.focus(); n.close(); openFromNotification(ev.account, ev.uid); };
  }
}

async function openFromNotification(account, uid) {
  if (!account || !state.accounts.some((a) => accId(a) === account)) return;
  if (account !== state.acc) await switchAccount(account);
  else if (state.folder.toUpperCase() !== 'INBOX' || state.q) { openFolder('INBOX'); }
  if (uid) openMessage(+uid);
}

function beep() {
  try {
    const ctx = beep.ctx || (beep.ctx = new (window.AudioContext || window.webkitAudioContext)());
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 880; g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    o.connect(g).connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.4);
  } catch {}
}

// ---------- Notifications permission & Web Push ----------
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;

async function setupNotify(ask) {
  const btn = $('#notifyBtn'), st = $('#notifyState');
  if (NATIVE) {
    state.pushOn = true; // the phone shows notifications itself
    window.__notifyChanged = () => setupNotify(false);
    if (ask) { NATIVE.requestNotify(); return; }
    const p = NATIVE.notifyStatus();
    btn.hidden = p === 'granted';
    st.textContent = p === 'granted' ? '🔔 Notifications on'
      : p === 'denied' ? 'Notifications are off. Tap the button, or turn them on in Settings > Apps > Mail.' : '';
    if (p === 'denied') btn.hidden = false;
    return;
  }
  if (!('Notification' in window)) {
    btn.hidden = true;
    st.textContent = isIOS && !standalone ? 'On iPhone: tap Share ▸ "Add to Home Screen", then open the app from there to get notifications.' : 'This browser does not support notifications.';
    return;
  }
  if (Notification.permission === 'denied') { btn.hidden = true; st.textContent = 'Notifications are blocked in your browser settings for this site.'; return; }
  if (Notification.permission === 'default' && !ask) { btn.hidden = false; st.textContent = ''; return; }
  if (ask && Notification.permission !== 'granted') {
    const p = await Notification.requestPermission();
    if (p !== 'granted') return setupNotify(false);
  }
  btn.hidden = true;
  state.pushOn = await subscribePush();
  st.textContent = state.pushOn ? '🔔 Notifications on (even when closed)' : '🔔 Notifications on (while this tab is open)';
}

const b64u = {
  toBytes: (s) => { const p = '='.repeat((4 - (s.length % 4)) % 4); const b = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(b, (c) => c.charCodeAt(0)); },
  fromBuf: (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''),
};

async function subscribePush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !window.isSecureContext) return false;
  try {
    const reg = await navigator.serviceWorker.register('/sw.js');
    await navigator.serviceWorker.ready;
    const { key } = await api('/api/push/key');
    let sub = await reg.pushManager.getSubscription();
    if (sub && sub.options.applicationServerKey && b64u.fromBuf(sub.options.applicationServerKey) !== key) { await sub.unsubscribe(); sub = null; }
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64u.toBytes(key) });
    await api('/api/push/subscribe', { json: { subscription: sub.toJSON() } });
    return true;
  } catch (e) {
    console.warn('Push not available:', e);
    return false;
  }
}

if (!NATIVE && 'serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'open') openFromNotification(e.data.account, e.data.uid);
  });
}

// ---------- Start ----------
api('/api/me').then(showApp).catch(() => showLogin());
