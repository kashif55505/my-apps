// TezDM Integration - background service worker
const APP = "http://127.0.0.1:47615";
const EXT_HDR = { "X-TezDM-Ext": "1" };

// These sites are downloaded with yt-dlp (quality selectable)
const VIDEO_SITES = /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com|facebook\.com|fb\.watch|instagram\.com|tiktok\.com|twitter\.com|x\.com|vimeo\.com|dailymotion\.com|twitch\.tv|reddit\.com|bilibili\.com|soundcloud\.com)$/i;

const skipOnce = new Set();   // when the app is closed, let Chrome download normally

// ---------- helpers ----------
async function settings() {
  return Object.assign({ intercept: true, minSizeKB: 0 },
    await chrome.storage.local.get(["intercept", "minSizeKB"]));
}

// Tells the app the extension is installed; also reloads the extension once
// when the app has written a newer version of its files to disk.
async function appAlive() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(APP + "/ping", { signal: ctrl.signal, headers: EXT_HDR });
    clearTimeout(t);
    if (!r.ok) return false;
    const j = await r.json().catch(() => ({}));
    const mine = chrome.runtime.getManifest().version;
    if (j.ext_version && j.ext_version !== mine) {
      const { reloadedFor } = await chrome.storage.local.get("reloadedFor");
      if (reloadedFor !== j.ext_version) {
        await chrome.storage.local.set({ reloadedFor: j.ext_version });
        setTimeout(() => chrome.runtime.reload(), 200);
      }
    }
    return true;
  } catch (e) { return false; }
}

async function cookieHeader(url) {
  try {
    const list = await chrome.cookies.getAll({ url });
    return list.map(c => `${c.name}=${c.value}`).join("; ");
  } catch (e) { return ""; }
}

async function requestBody({ url, filename, referer, kind = "file", format = "" }) {
  return {
    url, filename: filename || "", referer: referer || "",
    cookies: await cookieHeader(url),
    userAgent: navigator.userAgent, kind, format
  };
}

async function sendToApp(opts) {
  const r = await fetch(APP + "/add", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...EXT_HDR },
    body: JSON.stringify(await requestBody(opts))
  });
  if (!r.ok) throw new Error("The app rejected the request");
  return true;
}

// Chrome stops an idle service worker after ~30 s; keep it awake during long lookups
async function keepAwake(promise) {
  const t = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
  try { return await promise; } finally { clearInterval(t); }
}

async function getFormats(target) {
  const r = await fetch(APP + "/formats", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...EXT_HDR },
    body: JSON.stringify(await requestBody(target))
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "Could not load qualities");
  return j;
}

function hostOf(u) { try { return new URL(u).hostname; } catch (e) { return ""; } }
function baseName(p) { return (p || "").split(/[\\/]/).pop(); }

// ---------- 1) Capture every Chrome download ----------
chrome.downloads.onCreated.addListener(async (item) => {
  const s = await settings();
  if (!s.intercept) return;
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url)) return;            // skip blob:, data: etc.
  if (skipOnce.has(url)) { skipOnce.delete(url); return; }
  if (s.minSizeKB > 0 && item.fileSize > 0 && item.fileSize < s.minSizeKB * 1024) return;
  if (!(await appAlive())) return;                // app closed -> normal Chrome download

  try { await chrome.downloads.cancel(item.id); } catch (e) {}
  try { await chrome.downloads.erase({ id: item.id }); } catch (e) {}

  try {
    await sendToApp({ url, filename: baseName(item.filename), referer: item.referrer, kind: "file" });
  } catch (e) {
    skipOnce.add(url);
    chrome.downloads.download({ url });
  }
});

// ---------- 2) Detect video/audio streams playing on the page ----------
async function getMedia(tabId) {
  const k = "m" + tabId;
  const o = await chrome.storage.session.get(k);
  return o[k] || [];
}
async function setMedia(tabId, list) {
  await chrome.storage.session.set({ ["m" + tabId]: list });
  const n = list.length;
  chrome.action.setBadgeText({ tabId, text: n ? String(n) : "" });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#1565c0" });
}

function hdr(headers, name) {
  const h = (headers || []).find(x => x.name.toLowerCase() === name);
  return h ? h.value : "";
}

chrome.webRequest.onHeadersReceived.addListener((d) => {
  if (d.tabId < 0) return;
  const u = d.url;
  if (!/^https?:/i.test(u)) return;
  const ct = hdr(d.responseHeaders, "content-type").toLowerCase();
  const len = parseInt(hdr(d.responseHeaders, "content-length") || "0", 10);
  let type = null;
  if (/\.m3u8(\?|$)/i.test(u) || ct.includes("mpegurl")) type = "hls";
  else if (/\.mpd(\?|$)/i.test(u) || ct.includes("dash+xml")) type = "dash";
  else if (/^(video|audio)\//.test(ct) || /\.(mp4|webm|mkv|mov|mp3|m4a|flv)(\?|$)/i.test(u)) {
    if (/\.(ts|m4s)(\?|$)/i.test(u) || ct.includes("mp2t")) return;     // HLS segments
    if (/googlevideo\.com/i.test(u)) return;                             // YouTube chunks
    if (len && len < 512 * 1024) return;                                 // tiny chunks
    type = ct.startsWith("audio") ? "audio" : "video";
  }
  if (!type) return;
  (async () => {
    const list = await getMedia(d.tabId);
    const key = u.split("#")[0];
    if (list.some(x => x.url === key)) return;
    list.unshift({ url: key, type, size: len || 0 });
    await setMedia(d.tabId, list.slice(0, 30));
  })();
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] }, ["responseHeaders"]);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) setMedia(tabId, []);
});
chrome.tabs.onRemoved.addListener(tabId => chrome.storage.session.remove("m" + tabId));

// Decide what to download for a video: the page (yt-dlp), a stream, or a direct file
async function resolveVideo(tabId, pageUrl, src) {
  const media = tabId >= 0 ? await getMedia(tabId) : [];
  const stream = media.find(m => m.type === "hls" || m.type === "dash");
  const direct = media.find(m => m.type === "video");
  if (VIDEO_SITES.test(hostOf(pageUrl))) return { url: pageUrl, referer: pageUrl, kind: "video" };
  if (src && /^https?:/i.test(src) && !/\.m3u8|\.mpd/i.test(src)) return { url: src, referer: pageUrl, kind: "file" };
  if (stream) return { url: stream.url, referer: pageUrl, kind: "video" };
  if (src && /\.m3u8|\.mpd/i.test(src)) return { url: src, referer: pageUrl, kind: "video" };
  if (direct) return { url: direct.url, referer: pageUrl, kind: "file" };
  return { url: pageUrl, referer: pageUrl, kind: "video" };   // last resort: let yt-dlp read the page
}

async function downloadVideo(tabId, pageUrl, src) {
  return sendToApp(await resolveVideo(tabId, pageUrl, src));
}

// ---------- 3) Messages from content script and popup ----------
const NOT_RUNNING = "TezDM is not running. Please start the app first.";
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    const tabId = msg.tabId ?? (sender.tab ? sender.tab.id : -1);
    try {
      if (msg.type === "qualities") {
        // Video button: list qualities for this video
        const target = await resolveVideo(tabId, msg.pageUrl, msg.src);
        if (target.kind === "file") {
          reply({ ok: true, target, info: { title: baseName(target.url.split("?")[0]),
            options: [{ id: "file", label: "Download video file", note: "Original quality", size: 0 }] } });
          return;
        }
        reply({ ok: true, target, info: await keepAwake(getFormats(target)) });
      } else if (msg.type === "prefetch") {
        // Mouse is over a video: start looking up qualities now so the menu opens fast
        const target = await resolveVideo(tabId, msg.pageUrl, msg.src);
        if (target.kind === "video") getFormats(target).catch(() => {});
        reply({ ok: true });
      } else if (msg.type === "download") {
        const t = msg.target;
        await sendToApp({ ...t, format: t.kind === "video" ? msg.format : "" });
        reply({ ok: true });
      } else if (msg.type === "video") {
        await downloadVideo(tabId, msg.pageUrl, msg.src);
        reply({ ok: true });
      } else if (msg.type === "media-list") {
        reply({ ok: true, list: await getMedia(msg.tabId), alive: await appAlive() });
      } else if (msg.type === "send") {
        await sendToApp(msg.data);
        reply({ ok: true });
      } else if (msg.type === "page-video") {
        await downloadVideo(msg.tabId, msg.pageUrl, null);
        reply({ ok: true });
      }
    } catch (e) {
      const offline = e instanceof TypeError;   // fetch failed = app not running
      reply({ ok: false, error: offline ? NOT_RUNNING : e.message });
    }
  })();
  return true;
});

// ---------- 4) Keep the app informed that the extension is installed ----------
function setupAlarm() { chrome.alarms.create("tezdm-ping", { periodInMinutes: 1 }); }
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "tezdm-ping") appAlive(); });
chrome.runtime.onStartup.addListener(() => { setupAlarm(); appAlive(); });
setupAlarm();
appAlive();

// ---------- 5) Right-click menu ----------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "link", title: "Download with TezDM", contexts: ["link"] });
    chrome.contextMenus.create({ id: "media", title: "Download this video/audio with TezDM", contexts: ["video", "audio"] });
    chrome.contextMenus.create({ id: "image", title: "Download image with TezDM", contexts: ["image"] });
    chrome.contextMenus.create({ id: "page", title: "TezDM: download video on this page", contexts: ["page"] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const page = info.frameUrl || info.pageUrl || (tab && tab.url) || "";
  try {
    if (info.menuItemId === "link") {
      const kind = VIDEO_SITES.test(hostOf(info.linkUrl)) ? "video" : "file";
      await sendToApp({ url: info.linkUrl, referer: page, kind });
    } else if (info.menuItemId === "image") {
      await sendToApp({ url: info.srcUrl, referer: page, kind: "file" });
    } else if (info.menuItemId === "media") {
      await downloadVideo(tab ? tab.id : -1, page, info.srcUrl);
    } else if (info.menuItemId === "page") {
      await downloadVideo(tab ? tab.id : -1, page, null);
    }
  } catch (e) {
    console.warn("TezDM app is not running", e);
  }
});
