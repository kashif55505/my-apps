// Shared UI helpers
const OFFLINE_MSG = "TezDM is not running (it was closed or is being updated). Start TezDM from the desktop icon or the Start menu, then try again.";
async function api(path, body) {
  const opt = { headers: { "X-TezDM": "1" } };
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, opt);
  } catch (e) {
    // the TezDM program is not running (closed, or being updated)
    throw new Error(OFFLINE_MSG);
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

function size(n) {
  if (!n || n < 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1024 && i < 4) { n /= 1024; i++; }
  return (i ? n.toFixed(2) : n.toFixed(0)) + " " + u[i];
}

function timeLeft(t) {
  if (!t.running || !t.speed || !t.size || t.size <= t.done) return "";
  let s = Math.round((t.size - t.done) / t.speed);
  if (s < 0 || s > 360000) return "";
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); s %= 60;
  return h ? `${h} hr ${m} min` : m ? `${m} min ${s} sec` : `${s} sec`;
}

function dateStr(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  const p = (x) => String(x).padStart(2, "0");
  const mon = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getMonth()];
  return `${mon} ${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const CATS = [
  { id: "video", name: "Video", ic: "🎬", ext: /\.(mp4|mkv|webm|avi|mov|flv|wmv|m4v|3gp|ts)$/i },
  { id: "music", name: "Music", ic: "🎵", ext: /\.(mp3|m4a|wav|flac|aac|ogg|opus|wma)$/i },
  { id: "compressed", name: "Compressed", ic: "🗜️", ext: /\.(zip|rar|7z|tar|gz|bz2|xz|iso|cab)$/i },
  { id: "programs", name: "Programs", ic: "⚙️", ext: /\.(exe|msi|apk|dmg|bat|msix|appx)$/i },
  { id: "documents", name: "Documents", ic: "📄", ext: /\.(pdf|docx?|xlsx?|pptx?|txt|csv|epub|rtf|odt)$/i },
  { id: "images", name: "Images", ic: "🖼️", ext: /\.(jpe?g|png|gif|webp|bmp|svg|ico)$/i },
];

function catOf(t) {
  for (const c of CATS) if (c.ext.test(t.name || "")) return c;
  if (t.kind === "video") return CATS[0];
  return { id: "general", name: "General", ic: "📁" };
}

function statusText(t) {
  switch (t.status) {
    case "completed": return "Complete";
    case "error": return "Error";
    case "paused": return t.progress > 0 ? t.progress.toFixed(2) + "%" : "Stopped";
    case "queued": return "Queue";
    default: return t.size > 0 ? t.progress.toFixed(2) + "%" : (t.done ? size(t.done) : "Connecting...");
  }
}

// ---------- Folder browser (inside TezDM's own window) ----------
function folderPicker(initial) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "fp";
    d.innerHTML = `
      <div class="dlgh">Choose a folder</div>
      <div class="fpbar">
        <button class="btn" data-a="up" title="Up one level">⬆ Up</button>
        <button class="btn" data-a="home" title="Drives and common folders">🖥 This PC</button>
        <input class="fppath" spellcheck="false" placeholder="Type a folder path and press Enter">
      </div>
      <div class="fplist"></div>
      <div class="fperr bad"></div>
      <div class="fpfoot">
        <button class="btn" data-a="new">📁 New folder</button>
        <span class="grow"></span>
        <button class="btn primary" data-a="ok">Select this folder</button>
        <button class="btn" data-a="cancel">Cancel</button>
      </div>`;
    document.body.appendChild(d);
    const list = d.querySelector(".fplist"), input = d.querySelector(".fppath"), err = d.querySelector(".fperr");
    const okBtn = d.querySelector('[data-a="ok"]');
    let cur = { path: "", parent: "" };

    function done(v) { d.close(); d.remove(); resolve(v); }
    function row(icon, name, path, hint) {
      const el = document.createElement("div");
      el.className = "fpitem";
      el.innerHTML = `<span class="fpi">${icon}</span><span class="fpn">${esc(name)}</span>${hint ? `<span class="fph">${esc(hint)}</span>` : ""}`;
      el.title = path;
      el.onclick = () => go(path);
      list.appendChild(el);
    }
    async function go(path) {
      err.textContent = "";
      try {
        const r = await api("/api/dirs?path=" + encodeURIComponent(path || ""));
        cur = r;
        input.value = r.path;
        list.innerHTML = "";
        okBtn.disabled = !r.path;
        if (!r.path) {
          (r.places || []).forEach(p => row("⭐", p.name, p.path, p.path));
          (r.drives || []).forEach(dr => row("💽", dr, dr, "Drive"));
          return;
        }
        if (!r.dirs.length) list.innerHTML = '<div class="fpempty">No folders here. Click "Select this folder" to use it.</div>';
        r.dirs.forEach(n => row("📁", n, r.path.replace(/[\\/]$/, "") + (r.path.includes("\\") ? "\\" : "/") + n));
        list.scrollTop = 0;
      } catch (e) { err.textContent = e.message; }
    }
    d.addEventListener("click", async (e) => {
      const a = e.target.closest("[data-a]"); if (!a) return;
      switch (a.dataset.a) {
        case "up": go(cur.path ? cur.parent : ""); break;
        case "home": go(""); break;
        case "cancel": done(null); break;
        case "ok": if (cur.path) done(cur.path); break;
        case "new": {
          if (!cur.path) { err.textContent = "Open a drive or folder first."; return; }
          const name = prompt("Name of the new folder:", "New folder");
          if (!name) return;
          try { const r = await api("/api/mkdir", { path: cur.path, name }); go(r.path); }
          catch (ex) { err.textContent = ex.message; }
        }
      }
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(input.value.trim()); } });
    d.addEventListener("cancel", (e) => { e.preventDefault(); done(null); });
    d.showModal();
    go(initial || "");
  });
}
