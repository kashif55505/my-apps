// Shared UI helpers
async function api(path, body) {
  const opt = { headers: { "X-TezDM": "1" } };
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
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
