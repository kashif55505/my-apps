// TezDM: "Download" button with a quality menu on every video
(() => {
  if (window.__tezdm) return;
  window.__tezdm = true;

  // Shadow DOM so website CSS cannot break or hide our button
  const host = document.createElement("tezdm-ui");
  host.style.cssText = "all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
  <style>
    * { box-sizing: border-box; font-family: "Segoe UI", Arial, sans-serif; }
    .btn { position: fixed; display: none; align-items: center; gap: 6px; padding: 6px 12px 6px 10px;
      background: linear-gradient(#2f86e0, #1a5fb4); color: #fff; font-size: 13px; font-weight: 600; line-height: 1.2;
      border: 1px solid #154f97; border-radius: 6px; box-shadow: 0 2px 10px rgba(0,0,0,.4); cursor: pointer;
      user-select: none; white-space: nowrap; pointer-events: auto; }
    .btn:hover { background: linear-gradient(#3d93ea, #2068c0); }
    .btn svg { width: 15px; height: 15px; }
    .btn .car { font-size: 9px; opacity: .85; margin-left: 2px; }
    .btn .grip { width: 8px; height: 16px; margin-right: 2px; opacity: .7; cursor: grab;
      background-image: radial-gradient(#fff 1.2px, transparent 1.3px); background-size: 4px 4px; }
    .btn.dragging { cursor: grabbing; opacity: .9; box-shadow: 0 6px 18px rgba(0,0,0,.5); }
    .btn.err { background: #c42b1c; border-color: #8e1d12; }
    .menu { position: fixed; display: none; width: 330px; max-height: 380px; overflow: auto; background: #fff; color: #1b2430;
      border: 1px solid #9fb2c8; border-radius: 8px; box-shadow: 0 8px 30px rgba(0,0,0,.35); font-size: 13px; pointer-events: auto; }
    .hd { padding: 9px 12px; background: linear-gradient(#fff, #e9eff6); border-bottom: 1px solid #d5dde7; }
    .hd b { display: block; font-size: 12px; color: #1a5fb4; }
    .hd span { display: block; font-size: 12px; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
    .it { display: flex; align-items: center; gap: 8px; padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #f0f2f5; }
    .it:last-child { border-bottom: 0; }
    .it:hover { background: #e3eefb; }
    .it .l { font-weight: 600; min-width: 92px; }
    .it .n { color: #5d6b7b; font-size: 12px; }
    .it .s { margin-left: auto; color: #5d6b7b; font-size: 12px; }
    .msg { padding: 14px 12px; color: #333; }
    .msg.bad { color: #c42b1c; }
    .spin { display: inline-block; width: 13px; height: 13px; border: 2px solid #b9cbe2; border-top-color: #1a5fb4;
      border-radius: 50%; animation: sp .8s linear infinite; vertical-align: -2px; margin-right: 7px; }
    @keyframes sp { to { transform: rotate(360deg); } }
  </style>
  <div class="btn" id="btn" title="Click: choose quality · Drag: move this button · Double-click the dots: reset position"><span class="grip" id="grip"></span><svg viewBox="0 0 24 24"><path d="M12 3v12m0 0l-5-5m5 5l5-5M4 20h16" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg><span id="lbl">Download</span><span class="car">▼</span></div>
  <div class="menu" id="menu"></div>`;
  const btn = root.getElementById("btn");
  const grip = root.getElementById("grip");
  const lbl = root.getElementById("lbl");
  const menu = root.getElementById("menu");

  let current = null;     // video element the button belongs to
  let menuOpen = false;
  let hideTimer = null;
  let lastData = null;    // {target, info}

  function attach() {
    const parent = document.fullscreenElement || document.documentElement;
    if (host.parentNode !== parent) parent.appendChild(host);
  }

  const inRect = (x, y, r, pad = 0) => x >= r.left - pad && x <= r.right + pad && y >= r.top - pad && y <= r.bottom + pad;

  // The user can drag the button anywhere. Its position is remembered per website as an
  // offset from the video's top-right corner, so it follows the video when the page scrolls.
  const POS_KEY = "btnpos:" + location.hostname;
  let offset = null;      // {dx, dy} or null = default (inside the video, top-right)
  let dragging = false;
  try {
    chrome.storage.local.get(POS_KEY, (o) => { offset = (o && o[POS_KEY]) || null; if (current) place(); });
    chrome.storage.onChanged.addListener((ch, area) => {
      if (area === "local" && POS_KEY in ch) { offset = ch[POS_KEY].newValue || null; if (current) place(); }
    });
  } catch (e) {}

  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));

  function place() {
    if (!current || !current.isConnected) return hide(true);
    const r = current.getBoundingClientRect();
    if (r.width < 160 || r.height < 90 || r.bottom < 0 || r.top > innerHeight) return hide(true);
    attach();
    btn.style.display = "flex";
    const bw = btn.offsetWidth || 130, bh = btn.offsetHeight || 30;
    let left, top;
    if (!dragging) {
      if (offset) { left = r.right + offset.dx; top = r.top + offset.dy; }
      else { left = r.right - bw - 10; top = r.top + 10; }
      left = clamp(left, 6, innerWidth - bw - 6);
      top = clamp(top, 6, innerHeight - bh - 6);
      btn.style.top = top + "px";
      btn.style.left = left + "px";
    } else {
      left = parseFloat(btn.style.left); top = parseFloat(btn.style.top);
    }
    if (menuOpen) {
      const mw = 330;
      const below = innerHeight - (top + bh) - 10, above = top - 10;
      menu.style.left = clamp(left + bw - mw, 6, innerWidth - mw - 6) + "px";
      if (below >= 220 || below >= above) {
        menu.style.top = (top + bh + 4) + "px";
        menu.style.bottom = "auto";
        menu.style.maxHeight = Math.max(140, below) + "px";
      } else { // button is low on the screen: open the menu upwards
        menu.style.top = "auto";
        menu.style.bottom = (innerHeight - top + 4) + "px";
        menu.style.maxHeight = Math.max(140, above) + "px";
      }
    }
  }

  // ----- drag to move (press and move more than 4 px; a short press is a normal click) -----
  let press = null, justDragged = false;
  btn.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    press = { x: e.clientX, y: e.clientY, left: parseFloat(btn.style.left) || 0, top: parseFloat(btn.style.top) || 0, id: e.pointerId };
    try { btn.setPointerCapture(e.pointerId); } catch (err) {}   // keep getting moves outside the button
  }, true);
  btn.addEventListener("pointermove", (e) => {
    if (!press || e.pointerId !== press.id) return;
    const dx = e.clientX - press.x, dy = e.clientY - press.y;
    if (!dragging && Math.abs(dx) + Math.abs(dy) < 5) return;
    if (!dragging) {
      dragging = true;
      btn.classList.add("dragging");
      menuOpen = false; menu.style.display = "none";
    }
    const bw = btn.offsetWidth, bh = btn.offsetHeight;
    btn.style.left = clamp(press.left + dx, 2, innerWidth - bw - 2) + "px";
    btn.style.top = clamp(press.top + dy, 2, innerHeight - bh - 2) + "px";
    clearTimeout(hideTimer); hideTimer = null;
  }, true);
  function endDrag(e) {
    if (!press || (e && e.pointerId !== press.id)) return;
    if (dragging) {
      dragging = false;
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 0);
      btn.classList.remove("dragging");
      if (current) {
        const r = current.getBoundingClientRect();
        offset = { dx: Math.round(parseFloat(btn.style.left) - r.right), dy: Math.round(parseFloat(btn.style.top) - r.top) };
        try { chrome.storage.local.set({ [POS_KEY]: offset }); } catch (err) {}
      }
    }
    try { btn.releasePointerCapture(press.id); } catch (err) {}
    press = null;
  }
  btn.addEventListener("pointerup", endDrag, true);
  btn.addEventListener("pointercancel", endDrag, true);
  // double-click the dots: put the button back in its default place
  // (pointer capture sends the double-click to the button, so check where it happened)
  btn.addEventListener("dblclick", (e) => {
    const g = grip.getBoundingClientRect();
    if (e.clientX > g.right + 6) return;   // only the dots area, not the rest of the button
    e.preventDefault(); e.stopPropagation();
    offset = null;
    try { chrome.storage.local.remove(POS_KEY); } catch (err) {}
    place();
  }, true);

  function hide(force) {
    if (menuOpen && !force) return;
    btn.style.display = "none";
    menu.style.display = "none";
    menuOpen = false;
    current = null;
  }

  function videoAt(x, y) {
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      if (r.width >= 160 && r.height >= 90 && inRect(x, y, r)) return v;
    }
    return null;
  }

  // Show while the mouse is over a video, the button or the menu; hide 1.5 s after it leaves
  document.addEventListener("mousemove", (e) => {
    const overUi = btn.style.display !== "none" && (inRect(e.clientX, e.clientY, btn.getBoundingClientRect(), 4) ||
      (menuOpen && inRect(e.clientX, e.clientY, menu.getBoundingClientRect(), 4)));
    const v = videoAt(e.clientX, e.clientY);
    if (v || overUi) {
      clearTimeout(hideTimer);
      hideTimer = null;
      if (v && v !== current && !menuOpen) { current = v; lastData = null; }
      if (!current && v) current = v;
      place();
      prefetch();
    } else if (!hideTimer && !menuOpen) {
      hideTimer = setTimeout(() => { hideTimer = null; if (!dragging) hide(); }, 1500);
    }
  }, { passive: true, capture: true });

  window.addEventListener("scroll", () => { if (current) place(); }, { passive: true, capture: true });
  window.addEventListener("resize", () => { if (current) place(); });
  document.addEventListener("fullscreenchange", () => { attach(); if (current) place(); });

  // close the menu when clicking elsewhere
  document.addEventListener("mousedown", (e) => {
    if (menuOpen && !e.composedPath().includes(host)) { menuOpen = false; menu.style.display = "none"; }
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menuOpen) { menuOpen = false; menu.style.display = "none"; }
  }, true);

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function size(n) {
    if (!n) return "";
    const u = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < 3) { n /= 1024; i++; }
    return "~" + n.toFixed(i > 1 ? 1 : 0) + " " + u[i];
  }

  function flash(text, bad) {
    lbl.textContent = text;
    btn.classList.toggle("err", !!bad);
    place();
    setTimeout(() => { lbl.textContent = "Download"; btn.classList.remove("err"); if (current) place(); }, 2500);
  }

  function showMenu(html) {
    menu.innerHTML = html;
    menu.style.display = "block";
    menuOpen = true;
    place();
  }

  let prefetchedFor = "";
  function videoSrc() {
    const v = current;
    return v ? (v.currentSrc || v.src || (v.querySelector("source") || {}).src || "") : "";
  }
  function prefetch() {
    if (prefetchedFor === location.href) return;
    prefetchedFor = location.href;
    try { chrome.runtime.sendMessage({ type: "prefetch", pageUrl: location.href, src: videoSrc() }, () => void chrome.runtime.lastError); } catch (e) {}
  }

  function renderOptions(data) {
    const info = data.info || {};
    let h = `<div class="hd"><b>TezDM: choose quality</b><span>${esc(info.title || "")}</span></div>`;
    (info.options || []).forEach((o, i) => {
      h += `<div class="it" data-i="${i}"><span class="l">${esc(o.label)}</span><span class="n">${esc(o.note)}</span><span class="s">${size(o.size)}</span></div>`;
    });
    showMenu(h);
    menu.querySelectorAll(".it").forEach(el => el.addEventListener("click", (e) => {
      e.stopPropagation();
      const o = info.options[+el.dataset.i];
      menuOpen = false; menu.style.display = "none";
      chrome.runtime.sendMessage({ type: "download", target: data.target, format: o.id }, (res) => {
        if (chrome.runtime.lastError || !res || !res.ok) flash((res && res.error) || "TezDM is not running", true);
        else flash("✔ Sent to TezDM");
      });
    }));
  }

  let reqId = 0;
  function loadQualities(attempt) {
    const my = ++reqId;
    const page = location.href;
    showMenu(`<div class="msg"><span class="spin"></span>Loading qualities...</div>`);
    const timer = setTimeout(() => { if (my === reqId) fail("This is taking too long."); }, 70000);
    function fail(text) {
      clearTimeout(timer);
      if (my !== reqId || !menuOpen) return;
      reqId++;
      showMenu(`<div class="msg bad">${esc(text)}</div><div class="it" id="retry"><span class="l">↻ Retry</span></div>`);
      menu.querySelector("#retry").addEventListener("click", (e) => { e.stopPropagation(); loadQualities(1); });
    }
    try {
      chrome.runtime.sendMessage({ type: "qualities", pageUrl: page, src: videoSrc() }, (res) => {
        const err = chrome.runtime.lastError;
        if (my !== reqId) return;
        if (err || !res) {
          // background was restarted by Chrome: ask once more (the app keeps the lookup running)
          if (attempt < 2) { clearTimeout(timer); return loadQualities(attempt + 1); }
          return fail("Lost connection to the TezDM extension. Please refresh this page.");
        }
        if (!res.ok) return fail(res.error || "Could not load qualities");
        clearTimeout(timer);
        if (location.href === page) lastData = res;
        if (menuOpen) renderOptions(res);
      });
    } catch (e) {
      fail("TezDM extension was updated. Please refresh this page.");
    }
  }

  btn.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (justDragged || dragging) return;
    if (menuOpen) { menuOpen = false; menu.style.display = "none"; reqId++; return; }
    if (lastData) return renderOptions(lastData);
    loadQualities(1);
  }, true);
  btn.addEventListener("mousedown", (e) => e.stopPropagation(), true);
  menu.addEventListener("mousedown", (e) => e.stopPropagation(), true);

  // YouTube and other single-page sites change video without reloading the page
  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href; lastData = null; reqId++;
      if (menuOpen) { menuOpen = false; menu.style.display = "none"; }
    }
  }, 700);
})();
