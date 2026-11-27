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
import danish_ai
from yt_dlp.utils import DownloadCancelled

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = shutil.which("ffmpeg")

APP_NAME = "Danish Downloader"
VERSION = "1.13"
GITHUB_USER = "kashif55505"
GITHUB_REPO = "my-apps"
GITHUB_FOLDER = "danish-downloader"
UPDATE_BASES = [f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{b}/{GITHUB_FOLDER}/" for b in ("main", "master")]

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(BASE_DIR, "danish_data.json")
LOG_FILE = os.path.join(BASE_DIR, "danish_log.txt")
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
TEMP_DIR_NAME = ".danish_temp"
PHOTOS_DIR = "Photos"
MAX_TAGS = 4
MAX_NAME_LEN = 150
MAX_FAILS = 3  # itni baar fail ho to video chhor di jaye (maslan TikTok photo post)

DEFAULT_SETTINGS = {
    "check_hours": 3,
    "parallel": 4,
    "auto": True,
    "cookies_browser": "none",
    "cookies_file": "",
    "auto_update": True,
    "quality": "1080p",
    "ai_names": False,
    "ai_key": "",
    "ai_language": "Auto (Spanish creator → Spanish, others → English)",
    "ai_style": "",
    "ai_model": "",
    "ai_paid": False,
    "max_mb": 40,
    "photos": True,
}

DEFAULT_SOURCE = {
    "url": "",
    "folder": "",
    "per_day": 3,
    "stock_days": 15,
    "refill_days": 3,
    "skip_top": 0,
    "start_link": "",
    "end_link": "",
    "sample_link": "",
    "tiktok_secuid": "",
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


class EmptyList(Exception):
    pass


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


def is_hashtag_only(text):
    """Caption mein sirf hashtags (ya kuch bhi nahi) — tab AI naam banayega."""
    t = HASHTAG_RE.sub("", text or "")
    t = re.sub(r"\(\d+\)\s*$", "", t)
    return not re.search(r"[^\W_]", t)


def is_numbered_name(stem, folder):
    fname = os.path.basename(os.path.normpath(folder))
    return bool(re.fullmatch(r"\d+ " + re.escape(fname) + r"( \(\d+\))?", stem.strip(), re.I))


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
        st = self.data["settings"]
        if not st.get("_lang_auto_v19"):
            st["ai_language"] = DEFAULT_SETTINGS["ai_language"]
            st["_lang_auto_v19"] = True
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
        self._recent = {}
        self._plocks = {}

    def _recent_names(self, folder):
        try:
            names = [os.path.splitext(f)[0] for f in os.listdir(folder)
                     if os.path.splitext(f)[1].lower() in VIDEO_EXTS]
        except OSError:
            names = []
        return (names + self._recent.get(folder, []))[-40:]

    # ---- yt-dlp options
    def base_opts(self):
        s = self.store.settings
        o = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 5,
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
            "format": "bestvideo*+bestaudio/best",
            # 'res' = chhoti side (1080x1920 TikTok = 1080p). Quality setting tak, us se upar nahi.
            "format_sort": self._format_sort(),
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
            "concurrent_fragment_downloads": 8,   # ek video ke tukre ek saath (tez speed)
            "progress_hooks": [self._hook],
            "overwrites": True,
            "writethumbnail": False,
        })
        if FFMPEG_PATH:
            o["ffmpeg_location"] = FFMPEG_PATH
        return o

    def _format_sort(self):
        q = str(self.store.settings.get("quality") or "1080p").lower()
        cap = {"1080p": "res:1080", "720p": "res:720", "480p": "res:480"}.get(q, "res")
        return [cap, "fps", "vcodec:h264", "acodec:aac"]

    @staticmethod
    def _is_photo_post(info, url):
        if "/photo/" in (url or ""):
            return True
        fmts = info.get("formats") or []
        return "tiktok" in (info.get("extractor_key") or "").lower() and bool(fmts) and \
            all((f.get("vcodec") or "") == "none" for f in fmts)

    def _download_photos(self, ydl, src, entry, info):
        """TikTok photo post: saari tasveerein  <folder>/Photos/  mein."""
        vid = str(info.get("id") or entry["id"])
        if not self.store.settings.get("photos", True):
            self.store.mark_done(src, vid, entry["id"])
            self.log(f"   ⏭ Photo post skipped (photos OFF): {entry['url']}")
            return "photo"
        folder = os.path.join(src["folder"], PHOTOS_DIR)
        os.makedirs(folder, exist_ok=True)
        ie = ydl.get_info_extractor("TikTok")
        user = info.get("uploader") or (re.search(r"@([\w.-]+)", entry["url"]) or [None, "_"])[1]
        page = f"https://www.tiktok.com/@{user}/photo/{vid}"
        data, status = ie._extract_web_data_and_status(page, vid)
        imgs = []
        for im in ((data or {}).get("imagePost") or {}).get("images") or []:
            urls = (im.get("imageURL") or {}).get("urlList") or []
            if urls:
                imgs.append(urls)
        if not imgs:
            raise RuntimeError("Photo post: no images found")
        cap = caption_of(info)
        title = None
        st = self.store.settings
        if st.get("ai_names") and st.get("ai_key") and is_hashtag_only(cap):
            try:
                first = ydl.urlopen(imgs[0][0]).read()
                profile = self.get_profile(src, info) if src.get("id") != "_links_" else ""
                title = danish_ai.make_title(st, cap, first, self._recent_names(src["folder"]),
                                             log=self.log, profile=profile)
            except Exception as e:
                self.log(f"   ⚠ AI name error: {e}")
        base = clean_caption(title, max_tags=0) if title else clean_caption(cap)
        saved = 0
        with self.name_lock:
            if not base:
                base = numbered_name(folder).replace(PHOTOS_DIR, os.path.basename(os.path.normpath(src["folder"])))
            stem = base
            k = 2
            while any(os.path.exists(os.path.join(folder, f"{stem} {i}.jpg")) for i in (1,)) or \
                    os.path.exists(os.path.join(folder, stem + ".jpg")):
                stem = f"{base} ({k})"
                k += 1
            multi = len(imgs) > 1
            for i, urls in enumerate(imgs, 1):
                raw = None
                for u in urls:
                    try:
                        raw = ydl.urlopen(u).read()
                        break
                    except Exception:
                        continue
                if not raw:
                    continue
                out = os.path.join(folder, f"{stem} {i}.jpg" if multi else f"{stem}.jpg")
                if raw[:4] == b"RIFF" or raw[:4] == b"\x00\x00\x00\x1c" or raw[4:8] == b"ftyp":
                    tmp = out + ".src"
                    with open(tmp, "wb") as f:
                        f.write(raw)
                    try:
                        import subprocess
                        subprocess.run([FFMPEG_PATH, "-y", "-loglevel", "error", "-i", tmp, out], capture_output=True,
                                       timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    finally:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    if not os.path.exists(out):
                        continue
                else:
                    with open(out, "wb") as f:
                        f.write(raw)
                saved += 1
            self._recent.setdefault(src["folder"], []).append(stem)
        self.store.mark_done(src, vid, entry["id"])
        self.log(f"   🖼 {stem}  [{saved} photo{'s' if saved != 1 else ''} → {PHOTOS_DIR}]")
        return "photo"

    @staticmethod
    def _chosen_size(info):
        fmts = info.get("requested_formats") or [info]
        tot = 0
        for f in fmts:
            sz = f.get("filesize") or f.get("filesize_approx")
            if not sz:
                return 0
            tot += sz
        return tot

    def get_profile(self, src, info=None):
        """Creator ki samajh (bio + videos ki tasveerein + naam). Ek baar bana kar yaad rakho."""
        st = self.store.settings
        folder = src["folder"]
        lock = self._plocks.setdefault(src["id"], threading.Lock())
        with lock:
            have = count_videos(folder)
            auto = (st.get("ai_language") or "Auto").startswith("Auto")
            has_lang = bool(danish_ai.profile_language(src.get("ai_profile", "")))
            if src.get("ai_profile") and (src.get("ai_profile_n", 0) >= 3 or have < 3) and (has_lang or not auto):
                return src["ai_profile"]
            files = sorted((f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in VIDEO_EXTS),
                           key=lambda f: os.path.getmtime(os.path.join(folder, f)), reverse=True)
            pick = files[:: max(1, len(files) // 4)][:4]
            frames = [danish_ai.grab_frame(os.path.join(folder, f), FFMPEG_PATH) for f in pick]
            frames = [f for f in frames if f]
            caps = [os.path.splitext(f)[0] for f in files[:20]]
            if info is not None and caption_of(info):
                caps.insert(0, caption_of(info))
            handle = (info or {}).get("uploader") or (info or {}).get("channel") or \
                (re.search(r"@([\w.-]+)", src.get("url", "")) or [None, ""])[1]
            bio = src.get("bio") or self._fetch_bio(src.get("url", ""))
            if bio:
                src["bio"] = bio
            self.log(f"   ✨ Studying creator '{handle}' ({len(frames)} videos{', bio' if bio else ''})...")
            prof = danish_ai.build_profile(st, handle, bio, caps, frames, log=self.log)
            if prof:
                src["ai_profile"] = prof
                src["ai_profile_n"] = len(frames)
                self.store.save()
                lang = danish_ai.pick_language(st, prof)
                self.log("   ✨ Creator understood: " + prof.replace("\n", " ")[:140] + f"...  → names in {lang}")
            return prof

    @staticmethod
    def _fetch_bio(url):
        if "tiktok.com/@" not in (url or ""):
            return ""
        try:
            import urllib.request, html
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
            page = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
            m = re.search(r'"signature":"(.*?)(?<!\\)"', page)
            if m:
                return html.unescape(json.loads('"' + m.group(1) + '"'))[:400]
        except Exception:
            pass
        return ""

    def _hook(self, d):
        if self.stop_event.is_set():
            raise DownloadCancelled("Stopped")

    def set_status(self, src, text):
        self.status[src["id"]] = text
        self.on_change()

    # ---- list videos of a creator (newest first), STREAMING: pehli videos foran milti hain
    @staticmethod
    def _norm_entry(e):
        vurl = e.get("webpage_url") or e.get("url") or ""
        vid = str(e.get("id") or id_from_url(vurl) or "")
        if vurl and not vurl.startswith("http"):
            if "youtube" in (e.get("ie_key") or e.get("extractor_key") or "").lower() or len(vurl) == 11:
                vurl = "https://www.youtube.com/watch?v=" + vurl
            else:
                return None
        return {"id": vid, "url": vurl} if vurl else None

    def _tiktok_secuid_entries(self, sec_uid):
        """yt-dlp ka 'tiktokuser:' rasta pehle hafte mein video na mile to ghalat 'login' error deta hai.
        Yahan wahi list khud mangwate hain, bina us jaanch ke (purane hafte bhi dekhta hai)."""
        o = self.base_opts()
        with yt_dlp.YoutubeDL(o) as ydl:
            ie = ydl.get_info_extractor("TikTokUser")
            for r in ie._entries(sec_uid, None, fail_early=False):
                n = self._norm_entry(r or {})
                if n:
                    yield n

    def iter_entries(self, url, _depth=0):
        if url.startswith("tiktokuser:"):
            try:
                yield from self._tiktok_secuid_entries(url.split(":", 1)[1])
                return
            except TypeError:
                pass                      # yt-dlp ka andaaz badal gaya ho to purana rasta
        o = self.base_opts()
        o.update({"extract_flat": "in_playlist", "noplaylist": False, "lazy_playlist": True})
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            if not info:
                return
            t = info.get("_type") or "video"
            if t in ("url", "url_transparent") and _depth < 3 and info.get("url") and info["url"] != url:
                yield from self.iter_entries(info["url"], _depth + 1)
                return
            if t not in ("playlist", "multi_video"):
                yield {"id": str(info.get("id") or id_from_url(url)), "url": info.get("webpage_url") or url}
                return
            ents = info.get("entries") or []
            if hasattr(ents, "getslice"):          # yt-dlp PagedList
                ents = ents.getslice()
            for e in ents:
                if e:
                    n = self._norm_entry(e)
                    if n:
                        yield n

    def list_entries(self, url, limit):
        out = []
        for e in self.iter_entries(url):
            out.append(e)
            if limit and len(out) >= limit:
                break
        return out

    def _open(self, url):
        """Iterator kholo aur pehli video tak pahuncho (error yahin pakda jaye)."""
        it = iter(self.iter_entries(url))
        first = next(it, None)
        if first is None:
            raise EmptyList("empty list")

        def chain():
            if first is not None:
                yield first
            yield from it
        return chain()

    @staticmethod
    def is_done(entry, done):
        return (entry["id"] and entry["id"] in done) or entry["url"] in done or \
               (id_from_url(entry["url"]) in done if id_from_url(entry["url"]) else False)

    def _secuid_from_video(self, link):
        o = self.base_opts()
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(link, download=False)
        sid = info.get("channel_id") or ""
        return sid if sid.startswith("MS4w") else ""

    _tikwm_lock = threading.Lock()
    _tikwm_last = [0.0]

    def _tikwm_entries(self, user):
        """Backup list (TikWM, bina login). Newest first, 35 per page, 1 request/second."""
        import urllib.request
        cursor = 0
        seen = set()
        while True:
            q = f"https://www.tikwm.com/api/user/posts?unique_id=@{user}&count=35&cursor={cursor}"
            data = None
            for attempt in range(6):
                with self._tikwm_lock:
                    wait = 1.2 - (time.time() - self._tikwm_last[0])
                    if wait > 0:
                        time.sleep(wait)
                    self._tikwm_last[0] = time.time()
                req = urllib.request.Request(q, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
                try:
                    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore"))
                except Exception as e:
                    data = {"code": -1, "msg": str(e)}
                if data.get("code") == 0:
                    break
                time.sleep(1.5 * (attempt + 1))
            if not data or data.get("code") != 0:
                raise RuntimeError(f"TikWM: {(data or {}).get('msg', 'no answer')}")
            d = data.get("data") or {}
            for v in d.get("videos") or []:
                vid = str(v.get("video_id") or v.get("aweme_id") or v.get("id") or "")
                if vid and vid not in seen:
                    seen.add(vid)
                    yield {"id": vid, "url": f"https://www.tiktok.com/@{user}/video/{vid}"}
            if not d.get("hasMore") or not d.get("cursor") or str(d.get("cursor")) == str(cursor):
                return
            cursor = d.get("cursor")

    def _iter_with_fallback(self, src, url):
        """TikTok list ke 3 raste: (1) TikTok profile, (2) TikWM backup (bina login),
        (3) creator ID se TikTok list. Jo rasta chale, agli baar wahi pehle."""
        is_tt = "tiktok.com/@" in url.lower()
        if not is_tt:
            return self._open(url)
        user = (re.search(r"tiktok\.com/@([\w.-]+)", url) or [None, ""])[1]
        tried = []

        def via_profile():
            return self._open(url)

        def via_tikwm():
            it = iter(self._tikwm_entries(user))
            first = next(it, None)
            if first is None:
                raise EmptyList("TikWM returned no videos")

            def chain():
                yield first
                yield from it
            return chain()

        def via_secuid():
            sid = src.get("tiktok_secuid")
            if not sid:
                links = [src.get("sample_link"), src.get("start_link"), src.get("end_link")]
                for l in [l.strip() for l in links if l and "/video/" in l]:
                    try:
                        sid = self._secuid_from_video(l)
                    except Exception:
                        sid = ""
                    if sid:
                        with self.store.lock:
                            src["tiktok_secuid"] = sid
                        self.store.save()
                        break
            if not sid:
                raise EmptyList("no creator ID")
            return self._open("tiktokuser:" + sid)

        ways = {"profile": via_profile, "tikwm": via_tikwm, "secuid": via_secuid}
        order = ["profile", "tikwm", "secuid"]
        pref = src.get("list_via")
        if pref in order:
            order.remove(pref)
            order.insert(0, pref)
        last = None
        for w in order:
            try:
                it = ways[w]()
                if w != "profile":
                    self.log(f"   ✓ Got the video list via {'TikWM (backup, no login)' if w == 'tikwm' else 'creator ID'}.")
                if src.get("list_via") != w:
                    src["list_via"] = w
                    self.store.save()
                return it
            except Exception as e:
                last = e
                tried.append(w)
                if w == "profile":
                    self.log("   TikTok refused the profile page — trying backup ways...")
        raise last

    @staticmethod
    def _match(e, link):
        lid = id_from_url(link) or ""
        clean = link.strip().split("?")[0].rstrip("/")
        return bool((lid and (e["id"] == lid or id_from_url(e["url"]) == lid)) or
                    e["url"].split("?")[0].rstrip("/") == clean)

    @staticmethod
    def _find(entries, link):
        lid = id_from_url(link) or ""
        clean = link.strip().split("?")[0].rstrip("/")
        for i, e in enumerate(entries):
            if (lid and e["id"] == lid) or (lid and id_from_url(e["url"]) == lid) or \
               e["url"].split("?")[0].rstrip("/") == clean:
                return i
        return -1

    def _mark_range(self, src, entries):
        """Pehla link (jahan se shuru kiya) se aakhri link (jahan tak pahunche) tak sab 'ho chuki'.
        Pehle link se UPAR wali (nayi) aur aakhri se NEECHE wali baqi rahengi."""
        sl = (src.get("start_link") or "").strip()
        el = (src.get("end_link") or "").strip()
        i = self._find(entries, sl) if sl else 0
        j = self._find(entries, el) if el else i
        if i < 0 or j < 0:
            bad = sl if i < 0 else el
            self.log(f"   ✗ Link not found in this creator's video list: {bad}")
            self.log("     Make sure the link belongs to this creator and the video was not deleted.")
            return False
        if i > j:
            i, j = j, i
        rng = entries[i:j + 1]
        self.store.mark_done(src, *[e["id"] or e["url"] for e in rng])
        self.log(f"   {len(rng)} videos (first link → last link) marked as already downloaded. "
                 f"{i} newer above, {len(entries) - j - 1} older below remaining.")
        return True

    # ---- one video
    def download_one(self, src, entry, prog=None):
        res = self._download_one(src, entry)
        if res == "photo":
            if prog is not None:
                with prog["lock"]:
                    prog["finished"] += 1
                    prog["photos"] = prog.get("photos", 0) + 1
            return False
        ok = res
        if prog is not None:
            with prog["lock"]:
                prog["finished"] += 1
                prog["ok"] += int(ok)
                k, tot = prog["ok"], prog["submitted"]
                active = prog["submitted"] - prog["finished"]
            if ok:
                self.log(f"     ↳ {k}/{prog['need']} downloaded")
            if src.get("id") != "_links_":
                self.set_status(src, f"⬇ {k}/{prog['need']} downloaded • {active} active")
        return ok

    def _download_one(self, src, entry):
        if self.stop_event.is_set():
            return False
        folder = src["folder"]
        temp = os.path.join(folder, TEMP_DIR_NAME)
        os.makedirs(temp, exist_ok=True)
        t0 = time.time()
        try:
            max_mb = float(self.store.settings.get("max_mb") or 0)
            opts = self.download_opts(temp)
            if max_mb > 0:
                opts["max_filesize"] = int(max_mb * 1048576)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(entry["url"], download=False)
                if self._is_photo_post(info, entry["url"]):
                    return self._download_photos(ydl, src, entry, info)
                size = self._chosen_size(info)
                if max_mb > 0 and size and size > max_mb * 1048576:
                    self.store.mark_done(src, str(info.get("id") or entry["id"]), entry["id"])
                    self.log(f"   ⏭ Skipped (too big: {size / 1048576:.0f} MB > {max_mb:.0f} MB): {entry['url']}")
                    return False
                info = ydl.process_ie_result(info, download=True)
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
                if max_mb > 0:
                    self.store.mark_done(src, vid, entry["id"])
                    self.log(f"   ⏭ Skipped (bigger than {max_mb:.0f} MB or not a video): {entry['url']}")
                    return False
                raise RuntimeError("Output file not found (maybe a photo post, not a video)")
            dl_secs = time.time() - t0
            ext = os.path.splitext(path)[1].lower()
            cap = caption_of(info)
            title = None
            st = self.store.settings
            if st.get("ai_names") and st.get("ai_key") and is_hashtag_only(cap):
                try:
                    frame = danish_ai.grab_frame(path, FFMPEG_PATH)
                    profile = self.get_profile(src, info) if src.get("id") != "_links_" else ""
                    avoid = self._recent_names(folder)
                    title = danish_ai.make_title(st, cap, frame, avoid, log=self.log, profile=profile)
                except Exception as e:
                    self.log(f"   ⚠ AI name error: {e}")
            with self.name_lock:
                name = clean_caption(title, max_tags=0) if title else clean_caption(cap)
                if not name:
                    name = numbered_name(folder)
                final = unique_path(folder, name, ext)
                shutil.move(path, final)
                self._recent.setdefault(folder, []).append(os.path.splitext(os.path.basename(final))[0])
            if title and src.get("id") != "_links_":
                with self.store.lock:
                    src.setdefault("ai_renamed", []).append(os.path.basename(final))
            self.store.mark_done(src, vid, entry["id"], id_from_url(entry["url"]))
            cid = str(info.get("channel_id") or "")
            if src.get("id") != "_links_" and cid.startswith("MS4w") and not src.get("tiktok_secuid"):
                src["tiktok_secuid"] = cid
                self.store.save()
            w, h = info.get("width"), info.get("height")
            q = min(w, h) if w and h else h
            mb = os.path.getsize(final) / 1048576
            spd = mb / dl_secs if dl_secs > 0 else 0
            meta = (f"{q}p, " if q else "") + f"{mb:.1f} MB, {dl_secs:.0f}s, {spd:.1f} MB/s" + (", AI name" if title else "")
            self.log(f"   ✓ {os.path.basename(final)}  [{meta}]")
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
                self.log(f"   ✗ {entry['url']} — failed {MAX_FAILS} times, skipped. ({msg})")
            else:
                self.store.save()
                self.log(f"   ✗ {entry['url']} — {msg}")
            return False

    def _clean_temp(self, folder):
        t = os.path.join(folder, TEMP_DIR_NAME)
        if os.path.isdir(t):
            shutil.rmtree(t, ignore_errors=True)

    # ---- one creator/folder
    def check_source(self, src, force=False, _round=0):
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
            src["last_result"] = f"Stock OK ({have}/{target})"
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0
        need = target - have
        if need <= 0:
            src["last_result"] = f"Folder full ({have}/{target})"
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0

        self.log(f"▶ {name}: {have} videos in folder, target {target} → need {need}")
        self.set_status(src, "Fetching video list...")
        url = normalize_source_url(src["url"])
        try:
            it = self._iter_with_fallback(src, url)
        except Exception as e:
            msg = str(e).replace("ERROR: ", "").strip().splitlines()[0][:200]
            self.log(f"   ✗ Could not get creator's video list: {msg}")
            low = msg.lower()
            has_link = any("/video/" in (src.get(k) or "") for k in ("sample_link", "start_link", "end_link"))
            if "tiktok" in url.lower() and ("private" in low or "log in" in low or "login" in low):
                self.log("   ℹ TikTok wants a LOGIN to show this creator's videos. Fix: open Firefox, log in to "
                         "TikTok, then set Cookies = firefox below (or load a cookies.txt). Then try again.")
            elif "tiktok" in url.lower() and not has_link:
                self.log("   ℹ Fix: Edit this creator and paste any one video link of this creator "
                         "(Start Link, Last Link or 'Any Video Link').")
            if "instagram" in url.lower():
                self.log("   ℹ yt-dlp currently cannot list full Instagram profiles. "
                         "Use 'Download from Links' for Instagram reels.")
            src["last_result"] = "Error — see log"
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0

        # --- pehli baar: Start/Last link (ya 'upar se N') wali videos ko 'ho chuki' mark karna,
        #     list aate aate hi (poori list ka intezar nahi)
        init = not src.get("initialized")
        sl = (src.get("start_link") or "").strip() if init else ""
        el = (src.get("end_link") or "").strip() if init else ""
        links = [l for l in (sl, el) if l]
        skip_top = int(src.get("skip_top") or 0) if init and not links else 0
        zone = bool(el and not sl)          # sirf Last link: upar se us tak sab ho chuki
        found = set()
        to_mark = []
        if init and not links and not skip_top:
            src["initialized"] = True
            self.store.save()
            init = False

        done = set(src.get("done_ids", []))
        par = max(1, min(int(self.store.settings.get("parallel") or 4), 16))
        prog = {"lock": threading.Lock(), "submitted": 0, "finished": 0, "ok": 0, "need": need}
        keys = set()
        futs = []
        scanned = 0
        list_err = None
        self.log(f"   Downloads start as soon as videos are found ({par} at a time)...")
        ex = ThreadPoolExecutor(max_workers=par)
        try:
            for e in it:
                if self.stop_event.is_set():
                    break
                scanned += 1
                key = e["id"] or e["url"]
                if init:
                    hit = [l for l in links if l not in found and self._match(e, l)]
                    if hit:
                        found.update(hit)
                        to_mark.append(key)
                        zone = (len(links) == 2 and len(found) == 1)
                        if len(found) == len(links):
                            self.store.mark_done(src, *to_mark)
                            to_mark = []
                            src["initialized"] = True
                            self.store.save()
                            init = False
                            self.log(f"   ✓ Found your link(s) after {scanned} videos — older videos continue from here.")
                        continue
                    if zone or (skip_top and scanned <= skip_top):
                        to_mark.append(key)
                        if skip_top and scanned == skip_top:
                            self.store.mark_done(src, *to_mark)
                            to_mark = []
                            src["initialized"] = True
                            self.store.save()
                            init = False
                            self.log(f"   Top {skip_top} videos marked as already downloaded.")
                        continue
                if self.is_done(e, done) or key in keys:
                    continue
                if prog["submitted"] >= need:
                    break
                keys.add(key)
                with prog["lock"]:
                    prog["submitted"] += 1
                futs.append(ex.submit(self.download_one, src, e, prog))
                if prog["submitted"] >= need:
                    break
                if scanned % 15 == 0:
                    self.set_status(src, f"⬇ {prog['ok']}/{need} downloaded • scanned {scanned}")
        except Exception as e:
            list_err = str(e).replace("ERROR: ", "").strip().splitlines()[0][:200]
        if to_mark:
            self.store.mark_done(src, *to_mark)
        if init and links and len(found) < len(links):
            missing = [l for l in links if l not in found]
            self.log(f"   ✗ Link not found in this creator's list: {missing[0]}")
            self.log("     Check the link belongs to this creator (and wasn't deleted). Newer videos were still downloaded.")
        if list_err:
            self.log(f"   ⚠ Video list stopped early: {list_err}")
        if not futs:
            ex.shutdown(wait=True)
            if scanned == 0 and not list_err:
                self.log("   ⚠ TikTok returned an empty list for this creator. If the profile is public, try again later "
                         "or set Cookies = firefox (logged in to TikTok).")
            src["last_result"] = f"No new videos ({have}/{target})"
            self.log("   No videos left that haven't been downloaded already.")
            self.set_status(src, src["last_result"])
            self.store.save()
            return 0
        self.log(f"   List done ({scanned} scanned) — {len(futs)} videos queued, finishing downloads...")
        ex.shutdown(wait=True)
        ok = prog["ok"]
        self._clean_temp(folder)
        src["last_result"] = f"✓ {ok} new videos ({count_videos(folder)}/{target})"
        if self.stop_event.is_set():
            src["last_result"] += " — stopped"
        self.set_status(src, src["last_result"])
        self.store.save()
        ph = prog.get("photos", 0)
        self.log(f"■ {name}: {ok} videos downloaded" + (f", {ph} photo posts saved in {PHOTOS_DIR}." if ph else "."))
        if ph and _round < 3 and not self.stop_event.is_set() and count_videos(folder) < target:
            self.log("   Photo posts used some slots — getting more videos to fill the folder...")
            return ok + self.check_source(src, force=True, _round=_round + 1)
        return ok

    # ---- paste links mode
    def download_links(self, links, folder):
        src = {"id": "_links_", "folder": folder, "url": "", "done_ids": [], "fail_counts": {}}
        os.makedirs(folder, exist_ok=True)
        par = max(1, min(int(self.store.settings.get("parallel") or 4), 16))
        entries = [{"id": id_from_url(l), "url": l} for l in links]
        self.log(f"▶ Downloading {len(entries)} links → {folder}")
        ok = 0
        with ThreadPoolExecutor(max_workers=par) as ex:
            for f in as_completed([ex.submit(self.download_one, src, e) for e in entries]):
                ok += bool(f.result())
        self._clean_temp(folder)
        self.log(f"■ Links: {ok}/{len(entries)} videos downloaded.")
        return ok

    # ---- pehle se padi videos ke naam AI se badalna
    def rename_folder(self, src):
        folder = src["folder"]
        name = os.path.basename(os.path.normpath(folder))
        st = self.store.settings
        files = sorted(f for f in os.listdir(folder)
                       if os.path.splitext(f)[1].lower() in VIDEO_EXTS and os.path.isfile(os.path.join(folder, f)))
        done_set = set(src.get("ai_renamed", []))
        todo = [f for f in files if f not in done_set and
                (is_hashtag_only(os.path.splitext(f)[0]) or is_numbered_name(os.path.splitext(f)[0], folder))]
        self.log(f"▶ {name}: {len(todo)} hashtag-only videos to rename "
                 f"({len(files) - len(todo)} have real names — kept as they are).")
        if not todo:
            src["last_result"] = "Nothing to rename"
            self.set_status(src, src["last_result"])
            return
        profile = self.get_profile(src)
        cnt = {"ok": 0, "n": 0}
        lk = threading.Lock()

        def one(fn):
            if self.stop_event.is_set():
                return
            path = os.path.join(folder, fn)
            stem, ext = os.path.splitext(fn)
            frame = danish_ai.grab_frame(path, FFMPEG_PATH)
            title = danish_ai.make_title(st, stem, frame, self._recent_names(folder), log=self.log, profile=profile)
            with lk:
                cnt["n"] += 1
            if not title:
                return
            with self.name_lock:
                new = clean_caption(title, max_tags=0)
                if not new or new == stem:
                    return
                final = unique_path(folder, new, ext)
                try:
                    os.rename(path, final)
                except OSError as e:
                    self.log(f"   ✗ {fn}: {e} (file open in another program?)")
                    return
                self._recent.setdefault(folder, []).append(os.path.splitext(os.path.basename(final))[0])
            with self.store.lock:
                src.setdefault("ai_renamed", []).append(os.path.basename(final))
            self.store.save()
            with lk:
                cnt["ok"] += 1
                k = cnt["ok"]
            self.log(f"   ✓ {fn}  →  {os.path.basename(final)}   ({k}/{len(todo)})")
            self.set_status(src, f"✨ Renamed {k}/{len(todo)}")

        workers = 6 if st.get("ai_paid") else 1
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, todo))
        src["last_result"] = f"✨ {cnt['ok']} videos renamed"
        self.set_status(src, src["last_result"])
        self.store.save()
        self.log(f"■ {name}: {cnt['ok']}/{len(todo)} videos renamed.")

    # ---- run all / some
    def run(self, ids=None, force=False, links=None, links_folder=None, rename=False):
        if not self.run_lock.acquire(blocking=False):
            self.log("⏳ A job is already running, please wait.")
            return
        self.running = True
        self.stop_event.clear()
        self.on_change()
        try:
            if links:
                self.download_links(links, links_folder)
                return
            if rename:
                for sid in ids or []:
                    s = self.store.get(sid)
                    if s and not self.stop_event.is_set():
                        self.rename_folder(s)
                return
            srcs = [s for s in self.store.sources if (ids is None and s.get("enabled", True)) or (ids and s["id"] in ids)]
            if not srcs:
                self.log("No creators in the list.")
            for s in srcs:
                if self.stop_event.is_set():
                    break
                self.check_source(s, force=force)
            self.log(f"✔ Check complete — {now_str()}")
        except Exception as e:
            self.log(f"✗ Error: {e}")
        finally:
            self.running = False
            self.run_lock.release()
            self.on_change()

    def run_async(self, **kw):
        threading.Thread(target=self.run, kwargs=kw, daemon=True).start()

    def stop(self):
        self.stop_event.set()
        self.log("■ Stopping... (cancelling active downloads)")

    # ---- automatic checks
    def scheduler_loop(self):
        time.sleep(15)
        while True:
            s = self.store.settings
            hours = max(float(s.get("check_hours") or 3), 0.25)
            if s.get("auto", True) and not self.running and time.time() - self.last_auto >= hours * 3600:
                self.last_auto = time.time()
                self.log(f"⟳ Auto check started — {now_str()}")
                self.run(force=False)
            time.sleep(30)

    def start_scheduler(self):
        threading.Thread(target=self.scheduler_loop, daemon=True).start()
