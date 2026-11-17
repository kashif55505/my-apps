const $ = (id) => document.getElementById(id);

function fmt(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < 3) { n /= 1024; i++; }
  return n.toFixed(1) + " " + u[i];
}

function say(t, ok = true) { $("msg").textContent = t; $("msg").className = ok ? "ok" : "bad"; }

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const { intercept = true } = await chrome.storage.local.get("intercept");
  $("intercept").checked = intercept;
  $("intercept").onchange = (e) => chrome.storage.local.set({ intercept: e.target.checked });

  chrome.runtime.sendMessage({ type: "media-list", tabId: tab.id }, (res) => {
    if (!res) return;
    $("status").textContent = res.alive ? "Running" : "Not running (open the app)";
    $("status").className = res.alive ? "ok" : "bad";
    const list = res.list || [];
    if (!list.length) {
      $("list").innerHTML = '<div class="empty">No media found yet. Play the video, then open this popup again.</div>';
      return;
    }
    for (const m of list) {
      const d = document.createElement("div");
      d.className = "item";
      const name = decodeURIComponent((m.url.split("?")[0].split("/").pop()) || m.url);
      d.innerHTML = `<span class="tag">${m.type}</span><span class="u" title="${m.url.replace(/"/g, "&quot;")}"></span><span>${fmt(m.size)}</span>`;
      d.querySelector(".u").textContent = name;
      const b = document.createElement("button");
      b.textContent = "Download";
      b.onclick = () => {
        const kind = (m.type === "hls" || m.type === "dash") ? "video" : "file";
        chrome.runtime.sendMessage({ type: "send", data: { url: m.url, referer: tab.url, kind } },
          (r) => say(r && r.ok ? "Sent to TezDM" : (r && r.error) || "Error", r && r.ok));
      };
      d.appendChild(b);
      $("list").appendChild(d);
    }
  });

  $("open").onclick = () => fetch("http://127.0.0.1:47615/show", { headers: { "X-TezDM-Ext": "1" } }).then(() => window.close())
    .catch(() => say("TezDM is not running. Start it from the Start menu.", false));

  $("pagevid").onclick = () => {
    chrome.runtime.sendMessage({ type: "page-video", tabId: tab.id, pageUrl: tab.url },
      (r) => say(r && r.ok ? "Sent to TezDM" : (r && r.error) || "Error", r && r.ok));
  };
})();
