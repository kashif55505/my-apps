# -*- coding: utf-8 -*-
"""
Danish Downloader - core engine (GUI ke baghair).
Saara download, naam rakhne, stock check aur "kahan tak download hua" yaad rakhne ka kaam yahan hota hai.
"""
import os
import re
import sys
import json
import time
import uuid
import shutil
import threading
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import yt_dlp
from yt_dlp.utils import DownloadCancelled

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = shutil.which("ffmpeg")

APP_NAME = "Danish Downloader"
VERSION = "1.0"

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(BASE_DIR, "danish_data.json")
LOG_FILE = os.path.join(BASE_DIR, "danish_log.txt")
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
TEMP_DIR_NAME = ".danish_temp"
MAX_TAGS = 4
MAX_NAME_LEN = 150
MAX_FAILS = 3  # itni baar fail ho to video chhor di jaye (maslan TikTok photo post)

DEFAULT_SETTINGS = {
    "check_hours": 3,
    "parallel": 4,
    "auto": True,
    "cookies_browser": "none",
    "cookies_file": "",
}

DEFAULT_SOURCE = {
    "url": "",
    "folder": "",
    "per_day": 3,
    "stock_days": 15,
    "refill_days": 3,
    "skip_top": 0,
    "initialized": False,
    "enabled": True,
    "done_ids": [],
    "fail_counts": {},
    "last_check": "",
    "last_result": "",
}

HASHTAG_RE = re.compile(r"#[^\s#]+")
ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
GENERIC_TITLE_RE = re.compile(
    r"^(tiktok video #?\d+|video by .+|instagram (video|post|reel).*|"
    r"facebook( video| reel)?.*|#?\d{8,}|untitled)$",
    re.I,
)
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}

ID_PATTERNS = [
    r"tiktok\.com/.*?/(?:video|photo)/(\d+)",
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)([\w-]{11})",
    r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p|tv)/([\w-]+)",
    r"facebook\.com/.*?/videos/(?:[^/]+/)?(\d+)",
    r"facebook\.com/reel/(\d+)",
    r"facebook\.com/watch/?\?(?:.*&)?v=(\d+)",
    r"fb\.watch/([\w-]+)",
    r"snapchat\.com/spotlight/([\w-]+)",
]


# ----------------------------------------------------------------- helpers

def now_str():
    return datetime.datetime.now().strftime("%d-%m-%Y %I:%M %p")


def id_from_url(url):
    if not url:
        return ""
    for p in ID_PATTERNS:
        m = re.search(p, url, re.I)
        if m:
            return m.group(1)
    return ""


def platform_of(url):
    u = (url or "").lower()
    for key, name in (("tiktok", "TikTok"), ("youtu", "YouTube"), ("instagram", "Instagram"),
                      ("facebook", "Facebook"), ("fb.watch", "Facebook"), ("snapchat", "Snapchat"),
                      ("twitter", "X / Twitter"), ("x.com", "X / Twitter")):
        if key in u:
            return name
    return "Doosra"


def normalize_source_url(url):
    """YouTube channel ka link ho to /videos laga do, warna yt-dlp tabs ki list deta hai."""
    url = url.strip()
    m = re.match(r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))/?$", url, re.I)
    if m:
        return m.group(1) + "/videos"
    return url


def count_videos(folder):
    try:
        return sum(
            1 for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS and os.path.isfile(os.path.join(folder, f))
        )
    except OSError:
        return 0


def caption_of(info):
    """Video ka asli caption/naam nikalna. Na mile to khaali string."""
    ek = (info.get("extractor_key") or info.get("extractor") or "").lower()
    title = (info.get("title") or "").strip()
    desc = (info.get("description") or "").strip()
    text = title if "youtube" in ek else (desc or title)
    vid = str(info.get("id") or "")
    if not text or GENERIC_TITLE_RE.match(text) or text == vid:
        return ""
    return text


def clean_caption(text, max_tags=MAX_TAGS, max_len=MAX_NAME_LEN):
    """Caption ko Windows file name bana do: zyada se zyada 4 hashtags, ghalat nishaan hata do."""
    text = re.sub(r"[\r\n\t]+", " ", text or "")
    kept = []

    def _tag(m):
        if len(kept) < max_tags:
            kept.append(m.group(0))
            return m.group(0)
        return ""

    text = HASHTAG_RE.sub(_tag, text)
    text = ILLEGAL_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")

    if len(text) > max_len:
        tags = " ".join(ILLEGAL_RE.sub("", t) for t in kept).strip()
        main = re.sub(r"\s+", " ", HASHTAG_RE.sub("", text)).strip()
        room = max(max_len - len(tags) - 1, 30)
        if len(main) > room:
            cut = main[:room]
            sp = cut.rfind(" ")
            main = cut[:sp] if sp > room // 2 else cut
        text = (main.rstrip() + " " + tags).strip()[:max_len].rstrip(". ")

    if text.upper() in RESERVED_NAMES:
        text = "_" + text
    return text


def unique_path(folder, name, ext):
    path = os.path.join(folder, name + ext)
    i = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{name} ({i}){ext}")
        i += 1
    return path


def numbered_name(folder):
    """Jis video ka naam na ho: '1 Kashif', '2 Kashif' ..."""
    fname = os.path.basename(os.path.normpath(folder)) or "Video"
    fname = ILLEGAL_RE.sub("", fname).strip() or "Video"
    try:
        stems = {os.path.splitext(f)[0].lower() for f in os.listdir(folder)}
    except OSError:
        stems = set()
    n = 1
    while f"{n} {fname}".lower() in stems:
        n += 1
    return f"{n} {fname}"


# ------------------------------------------------------------------- store

class Store:
    def __init__(self, path=DATA_FILE):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"settings": dict(DEFAULT_SETTINGS), "sources": []}
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.data["settings"].update(d.get("settings", {}))
            self.data["sources"] = d.get("sources", [])
        except Exception:
            shutil.copy(self.path, self.path + ".kharab_backup")
        for s in self.data["sources"]:
            for k, v in DEFAULT_SOURCE.items():
                s.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
            s.setdefault("id", uuid.uuid4().hex[:10])

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    @property
    def settings(self):
        return self.data["settings"]

    @property
    def sources(self):
        return self.data["sources"]

    def get(self, sid):
        for s in self.sources:
            if s["id"] == sid:
                return s
        return None

    def add_source(self, **kw):
        s = {k: (v if not isinstance(v, (list, dict)) else type(v)()) for k, v in DEFAULT_SOURCE.items()}
        s.update(kw)
        s["id"] = uuid.uuid4().hex[:10]
        with self.lock:
            self.sources.append(s)
            self.save()
        return s

    def remove_source(self, sid):
        with self.lock:
            self.data["sources"] = [s for s in self.sources if s["id"] != sid]
            self.save()

    def mark_done(self, src, *ids):
        with self.lock:
            done = src["done_ids"]
            seen = set(done)
            for i in ids:
                i = str(i or "")
                if i and i not in seen:
                    done.append(i)
                    seen.add(i)
            self.save()

    def add_old_links(self, src, text):
        """Purane links (Link Grabber wale) paste kiye hon to unko 'ho chuki' mark kar do."""
        ids = []
        for line in re.split(r"[\s,]+", text or ""):
            line = line.strip()
            if line.startswith("http"):
                ids.append(id_from_url(line) or line)
        if ids:
            self.mark_done(src, *ids)
        return len(ids)


# ------------------------------------------------------------------ engine

class Engine:
    def __init__(self, store, log=print, on_change=lambda: None):
        self.store = store
        self.log = log
        self.on_change = on_change
        self.stop_event = threading.Event()
        self.run_lock = threading.Lock()
        self.name_lock = threading.Lock()
        self.status = {}          # source id -> live status text
        self.last_auto = 0.0
        self.running = False

    # ---- yt-dlp options
    def base_opts(self):
        s = self.store.settings
        o = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 3,
            "socket_timeout": 30,
            "noplaylist": True,
        }
        b = (s.get("cookies_browser") or "none").lower()
        if b != "none":
            o["cookiesfrombrowser"] = (b,)
        cf = s.get("cookies_file") or ""
        if cf and os.path.isfile(cf):
            o["cookiefile"] = cf
        return o

    def download_opts(self, outdir):
        o = self.base_opts()
        o.update({
            # Sab se best quality: jis quality mein upload hui, wohi (4K ho to 4K)
            "format": "bestvideo*+bestaudio/best",
            # Pehle resolution, phir fps; same quality mein H.264/AAC (Facebook par sab se aasaan)
            "format_sort": ["res", "fps", "vcodec:h264", "acodec:aac", "br"],
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
            "concurrent_fragment_downloads": 8,   # ek video ke tukre ek saath (tez speed)
            "http_chunk_size": 10 * 1024 * 1024,
            "progress_hooks": [self._hook],
            "overwrites": True,
            "writethumbnail": False,
        })
        if FFMPEG_PATH:
            o["ffmpeg_location"] = FFMPEG_PATH
        return o

    def _hook(self, d):
        if self.stop_event.is_set():
            raise DownloadCancelled("Roka gaya")

    def set_status(self, src, text):
        self.status[src["id"]] = text
        self.on_change()

    # ---- list videos of a creator (newest first)
    def list_entries(self, url, limit):
        o = self.base_opts()
        o.update({"extract_flat": "in_playlist", "playlistend": limit, "noplaylist": False})
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return []
        if info.get("_type") not in ("playlist", "multi_video") and not info.get("entries"):
            return [{"id": str(info.get("id") or id_from_url(url)), "url": info.get("webpage_url") or url}]
        out = []
        for e in info.get("entries") or []:
            if not e:
                continue
            vurl = e.get("webpage_url") or e.get("url") or ""
            vid = str(e.get("id") or id_from_url(vurl) or "")
            if vurl and not vurl.startswith("http"):
                if "youtube" in (e.get("ie_key") or "").lower():
                    vurl = "https://www.youtube.com/watch?v=" + vurl
                else:
                    continue
            if vurl:
                out.append({"id": vid, "url": vurl})
        return out

    @staticmethod
    def is_done(entry, done):
        return (entry["id"] and entry["id"] in done) or entry["url"] in done or \
               (id_from_url(entry["url"]) in done if id_from_url(entry["url"]) else False)

    # ---- one video
    def download_one(self, src, entry):
        if self.stop_event.is_set():
            return False
        folder = src["folder"]
        temp = os.path.join(folder, TEMP_DIR_NAME)
        os.makedirs(temp, exist_ok=True)
        try:
            with yt_dlp.YoutubeDL(self.download_opts(temp)) as ydl:
                info = ydl.extract_info(entry["url"], download=True)
            vid = str(info.get("id") or entry["id"])
            path = None
            for rd in info.get("requested_downloads") or []:
                if rd.get("filepath") and os.path.exists(rd["filepath"]):
                    path = rd["filepath"]
            if not path:
                for fn in os.listdir(temp):
                    if fn.startswith(vid + ".") and os.path.splitext(fn)[1].lower() in VIDEO_EXTS:
                        path = os.path.join(temp, fn)
            if not path:
                raise RuntimeError("File nahi mili (shayad ye video nahi, photo post hai)")
            ext = os.path.splitext(path)[1].lower()
            with self.name_lock:
                name = clean_caption(caption_of(info))
                if not name:
                    name = numbered_name(folder)
                final = unique_path(folder, name, ext)
                shutil.move(path, final)
            self.store.mark_done(src, vid, entry["id"], id_from_url(entry["url"]))
            h = info.get("height")
            self.log(f"   ✓ {os.path.basename(final)}" + (f"  [{h}p]" if h else ""))
            return True
        except DownloadCancelled:
            return False
        except Exception as e:
            msg = str(e).replace("ERROR: ", "").strip().splitlines()[0][:160]
            key = entry["id"] or entry["url"]
            with self.store.lock:
                fc = src.setdefault("fail_counts", {})
                fc[key] = fc.get(key, 0) + 1
                gave_up = fc[key] >= MAX_FAILS
            if gave_up:
                self.store.mark_done(src, key)
                self.log(f"   ✗ {entry['url']} — {MAX_FAILS} baar fail, ab chhor di. ({msg})")
            else:
                self.store.save()
                self.log(f"   ✗ {entry['url']} — {msg}")
            return False

    def _clean_temp(self, folder):
        t = os.path.join(folder, TEMP_DIR_NAME)
        if os.path.isdir(t):
            shutil.rmtree(t, ignore_errors=True)

    # ---- one creator/folder
    def check_source(self, src, force=False):
        folder = src["folder"]
        name = os.path.basename(os.path.normpath(folder))
        os.makedirs(folder, exist_ok=True)
        self._clean_temp(folder)
        per_day = max(int(src.get("per_day") or 1), 1)
        target = per_day * max(int(src.get("stock_days") or 1), 1)
        threshold = per_day * max(int(src.get("refill_days") or 0), 0)
        have = count_videos(folder)
        src["last_check"] = now_str()

        if not force and have > threshold:
            src["last_result"] = f"Stock theek ({have}/{target})"
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0
        need = target - have
        if need <= 0:
            src["last_result"] = f"Folder bhara hua ({have}/{target})"
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0

        self.log(f"▶ {name}: folder mein {have} videos, target {target} → {need} chahiyen")
        self.set_status(src, "Videos dhoondh raha hai...")
        url = normalize_source_url(src["url"])
        skip_top = 0 if src.get("initialized") else int(src.get("skip_top") or 0)
        done = set(src.get("done_ids", []))
        limit = len(done) + skip_top + need + 80
        try:
            entries = self.list_entries(url, limit)
        except Exception as e:
            msg = str(e).replace("ERROR: ", "").strip().splitlines()[0][:200]
            self.log(f"   ✗ Creator ki list nahi mili: {msg}")
            if "secondary user id" in msg.lower() or "tiktok" in url.lower():
                self.log("   ℹ TikTok ne list dene se mana kiya. '3_Update' chalayein, thori der baad dobara try karein, "
                         "ya Cookies mein 'firefox' chunein (Firefox mein TikTok login rakhein).")
            if "instagram" in url.lower():
                self.log("   ℹ Instagram profile se poori list abhi yt-dlp nahi nikal pata. "
                         "Instagram ke liye 'Links se download' wala option use karein.")
            src["last_result"] = "Error — log dekhein"
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0

        if not src.get("initialized"):
            if skip_top:
                first = entries[:skip_top]
                self.store.mark_done(src, *[e["id"] or e["url"] for e in first])
                self.log(f"   Upar ki {len(first)} videos 'pehle se download' mark kar di gayin.")
            src["initialized"] = True
            self.store.save()
            done = set(src.get("done_ids", []))

        # Upar se (sab se nayi) shuru: jo download nahi hui wo pehle -> nayi videos pehle aati hain,
        # phir wahin se aage jahan pichhli baar ruke the.
        pending = [e for e in entries if not self.is_done(e, done)][:need]
        if not pending:
            src["last_result"] = f"Koi nayi video nahi ({have}/{target})"
            self.log("   Is creator ki koi aisi video nahi mili jo pehle download na hui ho.")
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0

        self.log(f"   {len(pending)} videos download ho rahi hain...")
        par = max(1, min(int(self.store.settings.get("parallel") or 4), 16))
        ok = 0
        self.set_status(src, f"Download 0/{len(pending)}")
        with ThreadPoolExecutor(max_workers=par) as ex:
            futs = [ex.submit(self.download_one, src, e) for e in pending]
            for i, f in enumerate(as_completed(futs), 1):
                if f.result():
                    ok += 1
                self.set_status(src, f"Download {i}/{len(pending)}")
        self._clean_temp(folder)
        src["last_result"] = f"{ok} nayi videos ({count_videos(folder)}/{target})"
        if self.stop_event.is_set():
            src["last_result"] += " — roka gaya"
        self.set_status(src, src["last_result"])
        self.store.save()
        self.log(f"■ {name}: {ok} videos aa gayin.")
        return ok

    # ---- paste links mode
    def download_links(self, links, folder):
        src = {"id": "_links_", "folder": folder, "url": "", "done_ids": [], "fail_counts": {}}
        os.makedirs(folder, exist_ok=True)
        par = max(1, min(int(self.store.settings.get("parallel") or 4), 16))
        entries = [{"id": id_from_url(l), "url": l} for l in links]
        self.log(f"▶ {len(entries)} links download ho rahe hain → {folder}")
        ok = 0
        with ThreadPoolExecutor(max_workers=par) as ex:
            for f in as_completed([ex.submit(self.download_one, src, e) for e in entries]):
                ok += bool(f.result())
        self._clean_temp(folder)
        self.log(f"■ Links: {ok}/{len(entries)} videos aa gayin.")
        return ok

    # ---- run all / some
    def run(self, ids=None, force=False, links=None, links_folder=None):
        if not self.run_lock.acquire(blocking=False):
            self.log("⏳ Pehle wala kaam abhi chal raha hai, thora intezar karein.")
            return
        self.running = True
        self.stop_event.clear()
        self.on_change()
        try:
            if links:
                self.download_links(links, links_folder)
                return
            srcs = [s for s in self.store.sources if (ids is None and s.get("enabled", True)) or (ids and s["id"] in ids)]
            if not srcs:
                self.log("Koi creator list mein nahi hai.")
            for s in srcs:
                if self.stop_event.is_set():
                    break
                self.check_source(s, force=force)
            self.log(f"✔ Check mukammal — {now_str()}")
        except Exception as e:
            self.log(f"✗ Ghalti: {e}")
        finally:
            self.running = False
            self.run_lock.release()
            self.on_change()

    def run_async(self, **kw):
        threading.Thread(target=self.run, kwargs=kw, daemon=True).start()

    def stop(self):
        self.stop_event.set()
        self.log("■ Rok raha hoon... (chalti hui downloads band ho rahi hain)")

    # ---- automatic checks
    def scheduler_loop(self):
        time.sleep(15)
        while True:
            s = self.store.settings
            hours = max(float(s.get("check_hours") or 3), 0.25)
            if s.get("auto", True) and not self.running and time.time() - self.last_auto >= hours * 3600:
                self.last_auto = time.time()
                self.log(f"⟳ Automatic check shuru — {now_str()}")
                self.run(force=False)
            time.sleep(30)

    def start_scheduler(self):
        threading.Thread(target=self.scheduler_loop, daemon=True).start()
