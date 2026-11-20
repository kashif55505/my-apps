const $ = (s) => document.querySelector(s);
let state = { tasks: [] };
let selected = new Set();
let lastClicked = null;
let cat = "all";

function visible() {
  return state.tasks.filter(t => {
    if (cat === "all") return true;
    if (cat === "unfinished") return t.status !== "completed";
    if (cat === "finished") return t.status === "completed";
    return catOf(t).id === cat;
  });
}

function renderSide() {
  const all = state.tasks;
  const cnt = (f) => all.filter(f).length;
  const items = [
    ["all", "📥", "All Downloads", all.length],
    ["unfinished", "⏳", "Unfinished", cnt(t => t.status !== "completed")],
    ["finished", "✅", "Finished", cnt(t => t.status === "completed")],
  ];
  let h = "<h4>Downloads</h4>" + items.map(([id, ic, n, c]) =>
    `<div class="cat${cat === id ? " on" : ""}" data-cat="${id}"><span class="ic">${ic}</span>${n}<span class="cnt">${c || ""}</span></div>`).join("");
  h += "<h4>Categories</h4>";
  for (const c of [...CATS, { id: "general", name: "General", ic: "📁" }]) {
    const n = cnt(t => catOf(t).id === c.id);
    h += `<div class="cat${cat === c.id ? " on" : ""}" data-cat="${c.id}"><span class="ic">${c.ic}</span>${c.name}<span class="cnt">${n || ""}</span></div>`;
  }
  $("#side").innerHTML = h;
}
$("#side").addEventListener("click", (e) => {
  const el = e.target.closest("[data-cat]"); if (!el) return;
  cat = el.dataset.cat; selected.clear(); render();
});

function render() {
  renderSide();
  const list = visible();
  const tb = $("#rows");
  const ids = new Set(state.tasks.map(t => t.id));
  selected.forEach(id => { if (!ids.has(id)) selected.delete(id); });
  $("#empty").classList.toggle("hidden", list.length > 0);
  const existing = {};
  tb.querySelectorAll("tr").forEach(tr => existing[tr.dataset.id] = tr);
  list.forEach((t, i) => {
    let tr = existing[t.id];
    if (!tr) { tr = document.createElement("tr"); tr.dataset.id = t.id; }
    delete existing[t.id];
    const pct = t.progress.toFixed(1);
    tr.className = "st-" + t.status + (selected.has(t.id) ? " sel" : "");
    tr.title = t.error ? "Error: " + t.error : t.url;
    tr.innerHTML =
      `<td><div class="fn"><span class="ftype">${catOf(t).ic}</span><b>${esc(t.name)}</b></div></td>` +
      `<td>${size(t.size)}</td>` +
      `<td><div class="mini"><div class="bar"><i style="width:${pct}%"></i></div><em>${statusText(t)}</em></div></td>` +
      `<td>${timeLeft(t)}</td>` +
      `<td>${t.running && t.speed ? size(t.speed) + "/sec" : ""}</td>` +
      `<td>${dateStr(t.last_try || t.added)}</td>`;
    if (tb.children[i] !== tr) tb.insertBefore(tr, tb.children[i] || null);
  });
  Object.values(existing).forEach(tr => tr.remove());

  const active = state.tasks.filter(t => t.running);
  $("#sActive").textContent = `Active: ${active.length}`;
  $("#sSpeed").textContent = `Total speed: ${size(active.reduce((a, t) => a + (t.speed || 0), 0)) || "0 B"}/sec`;
  $("#sExt").innerHTML = state.ext ? '<span class="ok">● Chrome extension connected</span>'
    : state.extSeen ? '<span>● Chrome extension installed (Chrome is closed)</span>'
    : '<span class="bad">● Chrome extension not installed</span>';
  $("#sTools").textContent = state.tools === "downloading" ? state.toolsDetail : "";

  const b = $("#banner");
  if (!state.ext && !state.extSeen) {
    b.className = "banner";
    b.innerHTML = 'The Chrome extension is not installed yet. Without it, Chrome downloads and video buttons will not be captured. <button class="btn primary" id="bExt2">Install now</button>';
    $("#bExt2").onclick = extSetup;
  } else if (state.tools === "downloading" || state.tools === "error") {
    b.className = "banner info"; b.textContent = state.toolsDetail;
  } else b.className = "banner hidden";
}

let offline = false;
async function refresh() {
  try {
    state = await api("/api/state"); state.tasks = state.tasks || [];
    if (offline) { offline = false; $("#offline").classList.add("hidden"); }
    render();
    renderUpdate();
  } catch (e) {
    if (!offline) { offline = true; $("#offline").classList.remove("hidden"); $("#banner").className = "banner hidden"; }
  }
}
setInterval(refresh, 700);
refresh();

// ----- selection -----
$("#rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr"); if (!tr) return;
  const id = tr.dataset.id;
  if (e.shiftKey && lastClicked) {
    const ids = visible().map(t => t.id);
    const [a, b] = [ids.indexOf(lastClicked), ids.indexOf(id)].sort((x, y) => x - y);
    ids.slice(a, b + 1).forEach(x => selected.add(x));
  } else if (e.ctrlKey) {
    selected.has(id) ? selected.delete(id) : selected.add(id);
  } else selected = new Set([id]);
  lastClicked = id;
  render();
});
$("#rows").addEventListener("dblclick", (e) => {
  const tr = e.target.closest("tr"); if (!tr) return;
  const t = state.tasks.find(x => x.id === tr.dataset.id);
  act(t && t.status === "completed" ? "open" : "progress", [tr.dataset.id]);
});

async function act(action, ids) {
  ids = ids || [...selected];
  if (!ids.length && !action.endsWith("all")) { alert("Please select a download from the list first."); return; }
  if (action === "delete" || action === "deletefile") {
    const msg = `Remove ${ids.length} download(s) from the list?` + (action === "deletefile" ? "\nThe file will also be deleted from disk!" : "");
    if (!confirm(msg)) return;
  }
  await api("/api/action", { ids, action }).catch(e => alert(e.message));
  refresh();
}
document.querySelectorAll("[data-act]").forEach(el => el.addEventListener("click", () => { hideMenu(); act(el.dataset.act); }));
$("#bDelDone").onclick = () => {
  const ids = state.tasks.filter(t => t.status === "completed").map(t => t.id);
  if (!ids.length) return alert("There are no completed downloads.");
  if (confirm(`Remove ${ids.length} completed download(s) from the list? (Files stay on disk)`)) api("/api/action", { ids, action: "delete" }).then(refresh);
};

// ----- context menu -----
const menu = $("#menu");
function hideMenu() { menu.classList.add("hidden"); }
$("#rows").addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const tr = e.target.closest("tr"); if (!tr) return;
  if (!selected.has(tr.dataset.id)) { selected = new Set([tr.dataset.id]); render(); }
  menu.classList.remove("hidden");
  menu.style.left = Math.min(e.clientX, innerWidth - 200) + "px";
  menu.style.top = Math.min(e.clientY, innerHeight - 320) + "px";
});
document.addEventListener("click", (e) => { if (!menu.contains(e.target)) hideMenu(); });
menu.querySelector('[data-m="copy"]').onclick = () => {
  const t = state.tasks.find(x => selected.has(x.id)); if (t) navigator.clipboard.writeText(t.url); hideMenu();
};
menu.querySelector('[data-m="error"]').onclick = () => {
  const t = state.tasks.find(x => selected.has(x.id)); hideMenu(); if (t) alert(t.error || "No error");
};
document.addEventListener("keydown", (e) => {
  if (document.querySelector("dialog[open]")) return;
  if (e.key === "Delete") act("delete");
  if (e.key === "a" && e.ctrlKey) { e.preventDefault(); selected = new Set(visible().map(t => t.id)); render(); }
  if (e.key === "Enter" && selected.size) act("progress");
});

// ----- add url -----
const VIDEO = /(youtube\.com|youtu\.be|facebook\.com|fb\.watch|instagram\.com|tiktok\.com|twitter\.com|x\.com|vimeo\.com|dailymotion\.com|twitch\.tv|reddit\.com|\.m3u8|\.mpd)/i;
async function openAdd() {
  let clip = "";
  try { clip = (await navigator.clipboard.readText()).trim(); } catch (e) {}
  $("#aUrl").value = /^https?:\/\//i.test(clip) ? clip : "";
  $("#aKind").value = "auto";
  $("#dAdd").showModal(); $("#aUrl").focus(); $("#aUrl").select();
}
$("#bAdd").onclick = openAdd;
$("#mTasks").onclick = openAdd;
async function addGo() {
  const url = $("#aUrl").value.trim();
  let kind = $("#aKind").value;
  if (kind === "auto") kind = VIDEO.test(url) ? "video" : "file";
  try { await api("/api/add", { url, kind }); $("#dAdd").close(); } catch (e) { alert(e.message); }
}
$("#aOk").onclick = addGo;
$("#aCancel").onclick = () => $("#dAdd").close();
$("#aUrl").addEventListener("keydown", e => { if (e.key === "Enter") addGo(); });

// ----- settings -----
async function pick(input) {
  const p = await folderPicker(input.value.trim());
  if (p) input.value = p;
}
function openSettings() {
  const s = state.settings;
  $("#sDir").value = s.download_dir; $("#sConn").value = s.connections; $("#sPar").value = s.max_parallel;
  $("#sConfirm").checked = s.confirm_dialog; $("#sProg").checked = s.show_progress; $("#sAuto").checked = state.autostart;
  $("#dSet").showModal();
}
$("#bSettings").onclick = openSettings;
$("#mDl").onclick = openSettings;
$("#sPick").onclick = () => pick($("#sDir"));
$("#sCancel").onclick = () => $("#dSet").close();
$("#sSave").onclick = async () => {
  await api("/api/settings", {
    settings: { download_dir: $("#sDir").value.trim(), connections: +$("#sConn").value, max_parallel: +$("#sPar").value,
                confirm_dialog: $("#sConfirm").checked, show_progress: $("#sProg").checked },
    autostart: $("#sAuto").checked
  }).catch(e => alert(e.message));
  $("#dSet").close(); refresh();
};
$("#mFile").onclick = () => api("/api/openfolder", {});
$("#mHelp").onclick = () => alert("TezDM " + (state.version || "") + "\n\n• Every Chrome download is captured by TezDM automatically\n• Click the Download button on a video, then choose a quality\n• Double-click a download: progress window\n• TezDM keeps running in the background when you close this window");

// ----- updates -----
let firstVersion = null;
function renderUpdate() {
  if (firstVersion === null && state.version) firstVersion = state.version;
  if (firstVersion && state.version && state.version !== firstVersion) {
    // TezDM was updated while this window was open: reload it to get the new screens
    try { sessionStorage.setItem("tezdmUpdatedTo", state.version); } catch (e) {}
    location.reload();
    return;
  }
  let justUpdated = null;
  try { justUpdated = sessionStorage.getItem("tezdmUpdatedTo"); } catch (e) {}
  if (justUpdated && justUpdated === state.version) {
    const u = $("#updated");
    u.classList.remove("hidden");
    u.innerHTML = `✔ TezDM was updated to version <b>${esc(state.version)}</b>. <button class="btn" id="updOk">OK</button>`;
    $("#updOk").onclick = () => { try { sessionStorage.removeItem("tezdmUpdatedTo"); } catch (e) {} u.classList.add("hidden"); };
  }
  $("#sVer").textContent = state.version ? "TezDM " + state.version : "";
  $("#updDot").style.display = state.updateAvailable ? "" : "none";
  $("#updLbl").textContent = state.updateAvailable ? "Update!" : "Update";
  $("#bUpdate").classList.toggle("warn", !!state.updateAvailable);
  if (!$("#dUpd").open) return;
  $("#uCur").textContent = state.version || "-";
  $("#uLatest").textContent = state.latest ? state.latest + (state.updateAvailable ? "  (new!)" : "  (you have it)") : "Not checked yet";
  const msg = {
    downloading: state.updateDetail || "Downloading the update...",
    installing: state.updateDetail,
    error: state.updateDetail,
  }[state.updateStatus] || (state.updateAvailable ? "A new version is available." : (state.latest ? "TezDM is up to date." : ""));
  const m = $("#uMsg");
  m.textContent = msg;
  m.className = state.updateStatus === "error" ? "bad" : (state.updateAvailable || state.updateStatus ? "" : "ok");
  $("#uGo").disabled = state.updateStatus === "downloading" || state.updateStatus === "installing";
}
function openUpdate() { $("#dUpd").showModal(); renderUpdate(); api("/api/update/check", {}).catch(() => {}); }
$("#bUpdate").onclick = openUpdate;
$("#mUpd").onclick = openUpdate;
$("#uClose").onclick = () => $("#dUpd").close();
$("#uCheck").onclick = async () => {
  await api("/api/update/check", {}).catch(e => alert(e.message));
  $("#uMsg").textContent = "Checking... (takes up to a minute; needs Chrome open and signed in to GitHub)";
  $("#uMsg").className = "";
};
$("#uPage").onclick = () => window.open(state.releasePage, "_blank");
$("#uGo").onclick = async () => {
  try { await api("/api/update/start", {}); renderUpdate(); } catch (e) { alert(e.message); }
};

// ----- extension & exit -----
async function extSetup() { await api("/api/ext-setup", {}); }
$("#bExt").onclick = extSetup;
$("#bExit").onclick = async () => {
  const running = state.tasks.some(t => t.running);
  if (!confirm((running ? "Running downloads will be stopped (you can resume them later).\n" : "") +
    "Exit TezDM completely? While it is closed, the extension cannot capture downloads.")) return;
  await api("/api/exit", {}).catch(() => {});
  window.close();
};
