"""
Snapchat "My Data" export tools (for YOUR OWN Snapchat account)
================================================================
Snapchat keeps chats and Memories on its servers, not on the phone, so the
reliable way to get your data is Snapchat's own export:
  Snapchat > Settings > Privacy Controls > My Data  (or accounts.snapchat.com > My Data)
  tick Memories, Chat Media, Chat History ... and turn ON "Export JSON Files".
You get one or more mydata~*.zip files by e-mail.

  view       chats (chat_history.json + snap_history.json + chat_media) -> offline viewer,
             plus a Memories gallery
  memories   download every Memory photo/video listed in memories_history.json
             (with the original date as file time)
"""

import io
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
VID_EXT = {".mp4", ".mov", ".webm", ".m4v"}
AUD_EXT = {".m4a", ".aac", ".mp3", ".opus", ".ogg", ".wav"}


# ==========================================================================
# Locate / unpack the export
# ==========================================================================
def prepare_export(path, work_dir):
    """Accept an extracted folder, one zip, or a folder of zips. Return extracted root."""
    path = os.path.abspath(path)
    zips = []
    if os.path.isfile(path) and path.lower().endswith(".zip"):
        zips = [path]
        folder = os.path.dirname(path)
        zips += [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                 if f.lower().endswith(".zip") and f.startswith("mydata") and os.path.join(folder, f) != path]
    elif os.path.isdir(path):
        if _find(path, "chat_history.json") or _find(path, "memories_history.json"):
            return path
        zips = [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".zip")]
    if not zips:
        sys.exit("Snapchat export not found. Give the folder or the mydata~....zip file(s).")
    dest = os.path.join(work_dir, "_snapchat_export")
    os.makedirs(dest, exist_ok=True)
    for z in zips:
        print(f"Unzipping {os.path.basename(z)} ...")
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                target = os.path.abspath(os.path.join(dest, info.filename))
                if not target.startswith(os.path.abspath(dest) + os.sep):
                    continue                              # unsafe path in zip
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                elif not os.path.exists(target):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
    return dest


def _find(root, name):
    for r, _, files in os.walk(root):
        if name in files:
            return os.path.join(r, name)
    return None


def _load(root, name):
    p = _find(root, name)
    if not p:
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _ts(item):
    for k in ("Created(microseconds)", "Created(Microseconds)"):
        v = item.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v / 1_000_000) if v > 1e14 else int(v / 1000) if v > 1e11 else int(v)
    s = item.get("Created") or item.get("Date") or ""
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(datetime.strptime(s.strip(), fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


# ==========================================================================
# Chats
# ==========================================================================
KIND = {"TEXT": "text", "MEDIA": "image", "IMAGE": "image", "VIDEO": "video", "NOTE": "audio",
        "STICKER": "sticker", "SHARE": "share", "SHARESAVEDSTORY": "share", "LOCATION": "location",
        "SNAP": "snap", "STATUSERASEDMESSAGE": "deleted", "STATUSERASEDSNAPMESSAGE": "deleted",
        "STATUSCALLMISSEDAUDIO": "call", "STATUSCALLMISSEDVIDEO": "call", "STATUSPARTICIPANTREMOVED": "system",
        "STATUSSAVETOCAMERAROLL": "system"}


def _friends(root):
    names = {}
    data = _load(root, "friends.json") or {}
    for group in data.values():
        if isinstance(group, list):
            for f in group:
                if isinstance(f, dict) and f.get("Username"):
                    names[f["Username"]] = f.get("Display Name") or f["Username"]
    return names


def _media_index(root):
    idx = {}
    for r, _, files in os.walk(root):
        if "chat_media" not in r.replace("\\", "/").split("/"):
            continue
        for f in files:
            if "overlay" in f.lower() or "thumbnail" in f.lower():
                continue
            stem = os.path.splitext(f)[0]
            key = stem.split("_", 1)[1] if "_" in stem else stem
            idx[key] = os.path.join(r, f)
            idx[stem] = os.path.join(r, f)
    return idx


def _collect(data, kind_default, convs, me_label="IsSender"):
    """Handle both the new {conversation: [..]} and old {'Received ..': [..]} layouts."""
    if not isinstance(data, dict):
        return
    for key, items in data.items():
        if not isinstance(items, list):
            continue
        old_received = key.startswith("Received")
        old_sent = key.startswith("Sent")
        for it in items:
            if not isinstance(it, dict):
                continue
            if old_received or old_sent:
                other = it.get("From") if old_received else it.get("To")
                conv, me, title = other or "unknown", old_sent, None
            else:
                conv, me, title = key, bool(it.get(me_label)), it.get("Conversation Title")
            c = convs.setdefault(conv, {"key": conv, "title": title, "messages": []})
            if title and not c["title"]:
                c["title"] = title
            mtype = (it.get("Media Type") or "").upper()
            c["messages"].append({
                "t": _ts(it), "me": me, "s": "" if me else (it.get("From") or ""),
                "x": it.get("Content") or it.get("Text") or "",
                "k": kind_default or KIND.get(mtype, "text" if mtype == "" else "other"),
                "ids": it.get("Media IDs") or "",
            })


def read_chats(root):
    friends = _friends(root)
    convs = {}
    _collect(_load(root, "chat_history.json"), None, convs)
    _collect(_load(root, "snap_history.json"), "snap", convs)
    if not convs:
        return []
    media = _media_index(root)
    chats = []
    for c in convs.values():
        group = bool(c["title"])
        name = c["title"] or friends.get(c["key"], c["key"])
        msgs = sorted(c["messages"], key=lambda m: m["t"])
        for m in msgs:
            ids = [i.strip() for i in str(m.pop("ids")).split("|") if i.strip()]
            for i in ids:
                p = media.get(i)
                if p:
                    m["f"], m["n"] = p, os.path.basename(p)
                    ext = os.path.splitext(p)[1].lower()
                    if m["k"] in ("text", "other", "snap", "share"):
                        m["k"] = "image" if ext in IMG_EXT else "video" if ext in VID_EXT else \
                                 "audio" if ext in AUD_EXT else m["k"]
                    break
            if m["s"]:
                m["s"] = friends.get(m["s"], m["s"])
            if not group:
                m["s"] = ""
        chats.append({"name": name, "group": group, "messages": msgs})
    chats.sort(key=lambda c: c["messages"][-1]["t"] if c["messages"] else 0, reverse=True)
    return [c for c in chats if c["messages"]]


# ==========================================================================
# Memories
# ==========================================================================
def _mid(link):
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        return (q.get("mid") or q.get("sid") or [""])[0]
    except ValueError:
        return ""


def _memory_name(mem, i):
    ts = _ts(mem)
    stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") if ts else f"memory_{i:05d}"
    mid = re.sub(r"[^A-Za-z0-9-]", "", _mid(mem.get("Download Link", "")))[:12]
    return stamp + (f"_{mid}" if mid else f"_{i:05d}"), ts


def _sniff_ext(data, ctype, media_type):
    if data[:3] == b"\xFF\xD8\xFF":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[4:8] == b"ftyp":
        return ".mp4"
    if data[:4] == b"PK\x03\x04":
        return ".zip"
    if "video" in (ctype or ""):
        return ".mp4"
    return ".mp4" if str(media_type).upper() == "VIDEO" else ".jpg"


def _http(url, method="GET", timeout=60):
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"},
        data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def _download_one(mem, i, out_dir, merge):
    base, ts = _memory_name(mem, i)
    existing = [f for f in os.listdir(out_dir) if f.startswith(base)]
    if existing:
        return "skip"
    direct = mem.get("Media Download Url")
    link = mem.get("Download Link")
    data = ctype = None
    for attempt in range(3):
        try:
            if direct:
                data, ctype = _http(direct)
            elif link:
                if "proxy=true" in link or "/dmd/mm" in link:
                    data, ctype = _http(link)
                else:                               # older exports: POST returns the real URL
                    real, _ = _http(link, "POST")
                    data, ctype = _http(real.decode().strip())
            break
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(2 * (attempt + 1))
    if not data:
        return "fail"
    ext = _sniff_ext(data, ctype, mem.get("Media Type"))
    saved = []
    if ext == ".zip":                               # media + overlay
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            main = overlay = None
            for n in zf.namelist():
                if "overlay" in n.lower():
                    overlay = (n, zf.read(n))
                elif not n.endswith("/"):
                    main = (n, zf.read(n))
            if main:
                mext = os.path.splitext(main[0])[1] or _sniff_ext(main[1], "", mem.get("Media Type"))
                p = os.path.join(out_dir, base + mext)
                open(p, "wb").write(main[1])
                saved.append(p)
                if overlay and merge and mext.lower() in (".jpg", ".jpeg", ".png"):
                    merged = _merge_overlay(main[1], overlay[1])
                    if merged:
                        p2 = os.path.join(out_dir, base + "_with_text.jpg")
                        open(p2, "wb").write(merged)
                        saved.append(p2)
                elif overlay:
                    p2 = os.path.join(out_dir, base + "_overlay" + (os.path.splitext(overlay[0])[1] or ".png"))
                    open(p2, "wb").write(overlay[1])
                    saved.append(p2)
    else:
        p = os.path.join(out_dir, base + ext)
        open(p, "wb").write(data)
        saved.append(p)
    if ts:
        for p in saved:
            os.utime(p, (ts, ts))
    return "ok"


def _merge_overlay(img_bytes, overlay_bytes):
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        base = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        ov = Image.open(io.BytesIO(overlay_bytes)).convert("RGBA").resize(base.size)
        out = io.BytesIO()
        Image.alpha_composite(base, ov).convert("RGB").save(out, "JPEG", quality=92)
        return out.getvalue()
    except Exception:
        return None


def download_memories(export, out_dir, workers=4, merge=True):
    root = prepare_export(export, out_dir)
    data = _load(root, "memories_history.json")
    if not data:
        sys.exit("memories_history.json not found. Request your data again with 'Memories' "
                 "and 'Export JSON Files' ticked.")
    mems = data.get("Saved Media", []) if isinstance(data, dict) else data
    mem_dir = os.path.join(out_dir, "Memories")
    os.makedirs(mem_dir, exist_ok=True)
    print(f"{len(mems)} memories listed. Downloading to {mem_dir} ...")
    print("(Snapchat's download links expire a few days after the export e-mail.)")
    counts = {"ok": 0, "skip": 0, "fail": 0}
    lock = threading.Lock()

    def job(args):
        i, m = args
        r = _download_one(m, i, mem_dir, merge)
        with lock:
            counts[r] += 1
            n = sum(counts.values())
            print(f"\r  {n}/{len(mems)}  downloaded {counts['ok']}, already had {counts['skip']}, "
                  f"failed {counts['fail']}   ", end="", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(job, enumerate(mems)))
    print()
    if counts["fail"]:
        print(f"{counts['fail']} failed - run the same command again to retry (finished ones are skipped).\n"
              "If all fail, the links have expired: request a new export from Snapchat.")
    return mem_dir


def _gallery(out_dir, folders):
    items = []
    for d in folders:
        for r, _, files in os.walk(d):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if "overlay" in f.lower() or (ext not in IMG_EXT and ext not in VID_EXT):
                    continue
                p = os.path.join(r, f)
                m = re.match(r"(\d{4}-\d{2}-\d{2})", f)
                day = m.group(1) if m else datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d")
                items.append((day, f, p, ext in VID_EXT))
    if not items:
        return None
    from whatsapp import link_media
    names = {os.path.basename(p) for _, _, p, _ in items}
    items = [it for it in items                          # keep the "_with_text" version only
             if os.path.splitext(it[1])[0] + "_with_text.jpg" not in names]
    items.sort(reverse=True)
    parts, month = [], None
    for day, f, p, vid in items:
        if day[:7] != month:
            if month:
                parts.append("</div>")
            month = day[:7]
            parts.append(f"<h2>{datetime.strptime(month, '%Y-%m').strftime('%B %Y')}</h2><div class=\"g\">")
        u = link_media(p, out_dir).replace('"', "%22").replace("#", "%23")
        tag = (f'<video src="{u}" preload="metadata" controls></video>' if vid
               else f'<a href="{u}" target="_blank"><img loading="lazy" src="{u}" alt=""></a>')
        parts.append(f'<figure>{tag}<figcaption>{day}</figcaption></figure>')
    parts.append("</div>")
    page = GALLERY_HTML.replace("__COUNT__", str(len(items))).replace("__ITEMS__", "\n".join(parts))
    path = os.path.join(out_dir, "memories.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


GALLERY_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Snapchat Memories</title>
<style>body{margin:0;font:14px system-ui,sans-serif;background:#111;color:#eee;padding:12px 16px}
a.b{color:#fffc00}h1{font-size:20px}h2{font-size:16px;margin:22px 0 8px;color:#fffc00}
.g{display:flex;flex-wrap:wrap;gap:6px}figure{margin:0;width:180px}img,video{width:180px;height:240px;object-fit:cover;border-radius:6px;background:#222;display:block}
figcaption{font-size:11px;color:#999;margin-top:2px}@media(max-width:600px){figure,img,video{width:31vw;height:42vw}}</style></head>
<body><h1>Snapchat Memories (__COUNT__) <a class="b" href="index.html" style="font-size:13px;float:right">Chats &rarr;</a></h1>
__ITEMS__</body></html>"""


def build_viewer(export, out_dir, download=False, workers=4):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from whatsapp import write_viewer, link_media
    os.makedirs(out_dir, exist_ok=True)
    root = prepare_export(export, out_dir)
    chats = read_chats(root)
    total = sum(len(c["messages"]) for c in chats)
    linked = 0
    for c in chats:
        for m in c["messages"]:
            if m.get("f"):
                m["f"] = link_media(m["f"], out_dir)
                linked += 1
    print(f"Snapchat: {len(chats)} conversations, {total:,} messages/snaps, {linked} chat media linked")
    folders = [os.path.join(r, d) for r, ds, _ in os.walk(root) for d in ds if d.lower() == "memories"]
    if download:
        folders.append(download_memories(root, out_dir, workers))
    elif os.path.isdir(os.path.join(out_dir, "Memories")):
        folders.append(os.path.join(out_dir, "Memories"))
    gallery = _gallery(out_dir, folders)
    if gallery:
        print(f"Memories gallery: {gallery}")
    if chats:
        write_viewer(chats, out_dir, "Snapchat", ("memories.html", "Memories →") if gallery else None)
    elif not gallery:
        print("No chats or memories found in this export.")
    print("Note: Snapchat only exports SAVED chats; opened snaps and unsaved messages are deleted\n"
          "      by Snapchat and cannot be recovered by any tool.")
