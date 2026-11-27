"""
FB Reels & Video & Photo Uploader — SINGLE FILE
================================================
Aapki apni group IDs se Facebook par Reels, Videos aur Photos automatic
post karne ka tool. Har page ke apne hashtags save hote hain, sessions
save rehte hain (2FA baar baar nahi), browser saamne khulta hai.

INSTALL (aik dafa):
    pip install playwright
    python -m playwright install chromium

CHALANA:
    python fb_uploader.py

ISTEMAAL:
    1. Accounts tab: har ID ka naam, email, password, page ka naam -> Save ID
    2. Hashtags tab: page chunein, hashtags likhein -> Save (aik dafa)
    3. Reels / Videos / Photos tab: ID chunein -> files add -> POST karein
    4. 2FA aaye to code browser mein daalein, phir 'Resume' dabayein

ZAROORI:
    - Passwords data/accounts.json mein plain save hote hain. Computer secure rakhein.
    - Facebook automation ko pakar sakta hai; tool har post ke baad 40-90 sec rukta hai.
      Aik din mein bohat zyada post na karein.
    - Agar koi button na mile to log batayega aur browser khula rahega; aakhri step
      (jaise Publish) khud daba kar Resume dabayein. Selectors neeche SELECTORS/methods
      mein hain, zaroorat par wahin theek karein.
"""

import os
import sys
import json
import time
import random
import threading
import queue
import subprocess
import importlib
import shutil

# Windows par background subprocess ki khali cmd window na khule
_NOWIN = {}
if sys.platform.startswith("win"):
    try:
        _NOWIN = {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    except Exception:
        _NOWIN = {}
    # Global: har subprocess (jaise pytesseract) ki window bhi chhup jaye
    try:
        _si = subprocess.STARTUPINFO()
        _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        _orig_popen = subprocess.Popen

        class _QuietPopen(_orig_popen):
            def __init__(self, *a, **kw):
                if "startupinfo" not in kw or kw["startupinfo"] is None:
                    kw["startupinfo"] = _si
                if "creationflags" not in kw:
                    kw["creationflags"] = 0x08000000
                super().__init__(*a, **kw)
        subprocess.Popen = _QuietPopen
    except Exception:
        pass


# ============================================================
# VERSION + AUTO-UPDATE (GitHub se)
# ============================================================
APP_VERSION = "1.62"
# Apni GitHub repo (update isi se aayega)
GITHUB_OWNER = "kashif55505"
GITHUB_REPO = "my-apps"
_SUBDIR = "facebook-uploader"
VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{_SUBDIR}/version.txt"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RAW_PY_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{_SUBDIR}/fb_uploader.py"


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except Exception:
        return (0,)


def _latest_release_version():
    """Releases me se Facebook Uploader ka newest tag (fbu-v...) ka version."""
    import urllib.request
    listing = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
    req = urllib.request.Request(listing, headers={"User-Agent": "fbuploader"})
    rels = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    for rel in rels:   # newest first
        tag = rel.get("tag_name", "")
        if tag.startswith("fbu-v"):
            return tag[len("fbu-v"):]
    return ""


def check_update():
    """GitHub se latest version check karo. Return (has_update, latest, notes)."""
    try:
        latest = _latest_release_version()
        if not latest:
            # fallback: version.txt
            import urllib.request
            req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "fbuploader"})
            latest = urllib.request.urlopen(req, timeout=15).read().decode().strip().split()[0]
        has = _ver_tuple(latest) > _ver_tuple(APP_VERSION)
        return has, latest, ""
    except Exception as e:
        return False, "", str(e)


def _download_resume(url, dest, log=print, tries=8):
    """Bari file download — internet toote to wahin se resume (Range)."""
    import urllib.request
    total = None
    for attempt in range(1, tries + 1):
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        headers = {"User-Agent": "dkfbu"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:      # server ne resume nahi kiya
                    have = 0
                    mode = "wb"
                else:
                    mode = "ab" if have else "wb"
                cl = r.headers.get("Content-Length")
                if cl:
                    total = have + int(cl)
                with open(dest, mode) as f:
                    while True:
                        ch = r.read(1024 * 256)
                        if not ch:
                            break
                        f.write(ch)
            if total is None or os.path.getsize(dest) >= total:
                return dest
        except Exception as e:
            log(f"Update: download toota ({e.__class__.__name__}) — dobara koshish {attempt}/{tries}...")
            time.sleep(3)
    raise RuntimeError("download incomplete after retries")


_UPDATE_LOCK = threading.Lock()


def do_update(log=print):
    """Ek waqt mein sirf ek update (manual + auto dono ek saath chalen to exe kharab hoti thi)."""
    if not _UPDATE_LOCK.acquire(blocking=False):
        log("Update: pehle se update chal raha hai — intezaar karein.")
        return False
    try:
        return _do_update(log)
    finally:
        _UPDATE_LOCK.release()


def _do_update(log=print):
    """Nayi version download kar ke apply karo.
    - Source (.py) mode: naya fb_uploader.py replace, restart bolo.
    - Frozen (.exe) mode: latest release se .exe download, helper se swap + restart."""
    import urllib.request
    try:
        if getattr(sys, "frozen", False):
            # .exe mode -> releases me se Facebook Uploader ka .exe dhoondo
            listing = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
            req = urllib.request.Request(listing, headers={"User-Agent": "fbuploader"})
            rels = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
            url = None
            want_size = 0
            for rel in rels:   # newest first
                for a in rel.get("assets", []):
                    nm = a.get("name", "").lower()
                    if nm.endswith(".exe") and "setup" not in nm and \
                       ("facebook" in nm or "fb_uploader" in nm or "fbu" in nm):
                        url = a["browser_download_url"]; want_size = int(a.get("size") or 0); break
                if url:
                    break
            if not url:
                log("Update: release me Facebook Uploader .exe nahi mila.")
                return False
            cur = sys.executable
            new = cur + ".new"
            log("Update: naya version download ho raha hai...")
            try:
                if os.path.exists(new):
                    os.remove(new)          # purani adhuri/doosri version ki file — fresh shuru
            except Exception:
                pass
            _download_resume(url, new, log)
            # poori aur sahi file? (size + exe header) — warna install mat karo
            got = os.path.getsize(new) if os.path.exists(new) else 0
            with open(new, "rb") as _f:
                head = _f.read(2)
            if head != b"MZ" or (want_size and got != want_size):
                log(f"Update: download kharab ({got}/{want_size} bytes) — install nahi kiya, baad mein dobara.")
                try:
                    os.remove(new)
                except Exception:
                    pass
                return False
            # helper bat: app band hone ka intezaar, swap, relaunch
            bat = os.path.join(os.path.dirname(cur), "_update.bat")
            with open(bat, "w", encoding="utf-8") as f:
                f.write("@echo off\r\nset n=0\r\n:loop\r\n")
                f.write("timeout /t 2 /nobreak >nul\r\n")
                # purani exe band hone tak baar baar koshish (onefile exe der se band hoti hai)
                f.write(f'move /Y "{new}" "{cur}" >nul 2>&1\r\n')
                f.write(f'if not exist "{new}" goto done\r\n')
                f.write("set /a n+=1\r\nif %n% LSS 300 goto loop\r\n:done\r\n")
                f.write(f'start "" "{cur}"\r\n')
                f.write('del "%~f0"\r\n')
            # PyInstaller ke andar wale env vars hata do, warna nayi exe purane
            # (delete ho chuke) temp folder ko dhoondti hai -> "python311.dll" error
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("_MEI", "_PYI", "PYINSTALLER"))}
            env.pop("TCL_LIBRARY", None); env.pop("TK_LIBRARY", None)
            subprocess.Popen(["cmd", "/c", bat], env=env, **_NOWIN)
            log("Update tayyar — app band ho kar nayi version se khulega.")
            return "restart_exe"
        else:
            # source mode -> naya fb_uploader.py
            here = os.path.abspath(__file__)
            req = urllib.request.Request(RAW_PY_URL, headers={"User-Agent": "fbuploader"})
            code = urllib.request.urlopen(req, timeout=20).read()
            if b"APP_VERSION" not in code:
                log("Update: file theek nahi mili.")
                return False
            with open(here + ".new", "wb") as f:
                f.write(code)
            os.replace(here + ".new", here)
            log("Update ho gaya — app dobara chalayein (restart).")
            return "restart_py"
    except Exception as e:
        log(f"Update error: {e}")
        return False


def _run(cmd, **kw):
    kw.update(_NOWIN)
    return subprocess.check_call(cmd, **kw)
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
# Har table ki heading usi column ki cheez ke bilkul upar (baayen se) — sab tables mein
_orig_tv_heading = ttk.Treeview.heading


def _tv_heading(self, column, option=None, **kw):
    if option is None and "text" in kw and "anchor" not in kw and column != "#0":
        kw["anchor"] = "w"
    return _orig_tv_heading(self, column, option, **kw)


ttk.Treeview.heading = _tv_heading

# --- .exe ke andar bundled Chromium ka raasta set karein (playwright import se PEHLE) ---
# PyInstaller --onefile chalne par files sys._MEIPASS mein khulti hain.
if getattr(sys, "frozen", False):
    _bundled_browsers = os.path.join(getattr(sys, "_MEIPASS", ""), "ms-playwright")
    if os.path.isdir(_bundled_browsers):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _bundled_browsers

# Playwright optional import — taake GUI bina install ke bhi khul jaye aur
# post dabane par saaf message mile.
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_OK = True
except Exception:
    PLAYWRIGHT_OK = False

# ============================================================
# STORAGE
# ============================================================
# Data writable jagah par: .exe mode mein AppData (Program Files me install ho to bhi chale),
# warna script ke paas "data" folder.
if getattr(sys, "frozen", False):
    _appdata = os.environ.get("APPDATA") or os.path.dirname(os.path.abspath(sys.executable))
    DATA_DIR = os.path.join(_appdata, "Facebook Uploader")
    BASE_DIR = DATA_DIR
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
HASHTAGS_FILE = os.path.join(DATA_DIR, "hashtags.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
PAGE_SETTINGS_FILE = os.path.join(DATA_DIR, "page_settings.json")
UPLOADED_FILE = os.path.join(DATA_DIR, "uploaded.json")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save(path, data):
    _ensure_dirs()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_accounts():
    return _load(ACCOUNTS_FILE, [])


def save_accounts(accounts):
    _save(ACCOUNTS_FILE, accounts)


def add_account(name, email, password, page_name, proxy="", ix_profile="", folder="", twofa="", ld_index=""):
    accounts = load_accounts()
    acc_id = email.strip().lower()
    old = next((a for a in accounts if a.get("id") == acc_id), {})
    pos = next((i for i, a in enumerate(accounts) if a.get("id") == acc_id), None)   # apni jagah par rahe
    accounts = [a for a in accounts if a.get("id") != acc_id]
    ix_profile = ix_profile or old.get("ix_profile", "")
    rec = {
        "id": acc_id,
        "name": name.strip(),
        "email": email.strip(),
        "password": password,
        "page_name": page_name.strip(),
        "proxy": (proxy or "").strip(),
        "ix_profile": (ix_profile or "").strip(),
        "folder": (folder or "").strip(),
        "twofa": (twofa or "").strip(),
        "ld_index": str(ld_index or "").strip(),
    }
    for _k, _v in old.items():           # baqi saari maloomat (UID, group, status, pages...) mehfooz
        if _k not in rec:
            rec[_k] = _v
    if old.get("ix_key"):
        rec["ix_key"] = old["ix_key"]    # ixBrowser free-mode link mehfooz
    if pos is None:
        accounts.append(rec)
    else:
        accounts.insert(pos, rec)        # tarteeb na badle (tick/edit se ID neeche na jaye)
    save_accounts(accounts)
    return acc_id


def update_account(acc_id, **fields):
    """Account ke kuch fields update karo (baqi waise hi)."""
    accounts = load_accounts()
    for a in accounts:
        if a.get("id") == acc_id:
            a.update(fields)
    save_accounts(accounts)


def get_account(acc_id):
    for a in load_accounts():
        if a.get("id") == acc_id:
            return a
    return None


def set_account_pages(acc_id, pages):
    """Detect kiye hue pages save karo — pehle se enabled pages ka tick yaad rakho."""
    acc = get_account(acc_id) or {}
    old = {p.get("url") or p.get("name"): p.get("enabled", False) for p in acc.get("pages", [])}
    out = []
    for p in pages:
        key = p.get("url") or p.get("name")
        out.append({"name": p.get("name", ""), "url": p.get("url", ""),
                    "enabled": old.get(key, False)})
    update_account(acc_id, pages=out)
    return out


def set_page_enabled(acc_id, key, enabled):
    acc = get_account(acc_id)
    if not acc:
        return
    pages = acc.get("pages", [])
    for p in pages:
        if (p.get("url") or p.get("name")) == key:
            p["enabled"] = bool(enabled)
    update_account(acc_id, pages=pages)


# ============================================================
# VPN TUNNELS — Surfshark (ya koi bhi) WireGuard .conf se har browser ka ALAG IP
# Har tunnel ek chhota "wireproxy" process: WireGuard -> local SOCKS5 (127.0.0.1:port)
# ============================================================
TUNNEL_DIR = os.path.join(DATA_DIR, "tunnels")
_WG_PROCS = {}          # name -> Popen
_WG_LOCK = threading.Lock()


def wg_list():
    return load_settings().get("wg_tunnels", [])


def wg_save(lst):
    save_settings({"wg_tunnels": lst})


def wg_import_files(paths):
    """WireGuard .conf files add karo. Return kitne add hue."""
    lst = wg_list()
    have = {t["name"] for t in lst}
    used_ports = {t["port"] for t in lst}
    port = 41000
    n = 0
    for p in paths:
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        if "[Interface]" not in txt or "[Peer]" not in txt:
            continue
        name = os.path.splitext(os.path.basename(p))[0][:40]
        base, k = name, 2
        while name in have:
            name = f"{base}-{k}"; k += 1
        while port in used_ports:
            port += 1
        ep = ""
        for line in txt.splitlines():
            if line.strip().lower().startswith("endpoint"):
                ep = line.split("=", 1)[1].strip()
        lst.append({"name": name, "conf": txt, "port": port, "endpoint": ep})
        have.add(name); used_ports.add(port); n += 1
    wg_save(lst)
    return n


def _wireproxy_exe(log=print):
    """wireproxy.exe dhoondo; na ho to GitHub se 1 dafa download."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(getattr(sys, "_MEIPASS", ""), "wireproxy.exe"))
        cands.append(os.path.join(os.path.dirname(sys.executable), "wireproxy.exe"))
    bindir = os.path.join(DATA_DIR, "bin")
    cands.append(os.path.join(bindir, "wireproxy.exe" if os.name == "nt" else "wireproxy"))
    for c in cands:
        if os.path.isfile(c):
            return c
    import urllib.request, tarfile, io
    log(T("Downloading tunnel engine (wireproxy, one time)...", "Tunnel engine (wireproxy) download ho raha hai (sirf 1 dafa)..."))
    want = "windows_amd64" if os.name == "nt" else "linux_amd64"
    url = None
    for repo in ("whyvl/wireproxy", "pufferffish/wireproxy"):
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                         headers={"User-Agent": "dkfbu"})
            rel = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
            for a in rel.get("assets", []):
                if want in a["name"] and a["name"].endswith((".tar.gz", ".zip")):
                    url = a["browser_download_url"]; break
            if url:
                break
        except Exception:
            continue
    if not url:
        raise RuntimeError("wireproxy download link not found")
    data = urllib.request.urlopen(url, timeout=120).read()
    os.makedirs(bindir, exist_ok=True)
    target = cands[-1]
    if url.endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.namelist():
                if os.path.basename(m).startswith("wireproxy"):
                    open(target, "wb").write(z.read(m)); break
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for m in tf.getmembers():
                if os.path.basename(m.name).startswith("wireproxy") and m.isfile():
                    open(target, "wb").write(tf.extractfile(m).read()); break
    if os.name != "nt":
        os.chmod(target, 0o755)
    log(T("Tunnel engine ready.", "Tunnel engine tayyar."))
    return target


def _port_open(port, timeout=1.0):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def wg_ensure(name, log=print):
    """Tunnel chalao (agar band hai). Return local port."""
    t = next((x for x in wg_list() if x["name"] == name), None)
    if not t:
        raise RuntimeError(f"tunnel '{name}' not found")
    with _WG_LOCK:
        pr = _WG_PROCS.get(name)
        if pr and pr.poll() is None and _port_open(t["port"]):
            return t["port"]
        exe = _wireproxy_exe(log)
        os.makedirs(TUNNEL_DIR, exist_ok=True)
        cfg = os.path.join(TUNNEL_DIR, f"{name}.conf")
        conf = "\n".join(l for l in t["conf"].splitlines()
                         if not l.strip().lower().startswith(("postup", "postdown", "predown", "preup", "table", "saveconfig")))
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(conf.strip() + f"\n\n[Socks5]\nBindAddress = 127.0.0.1:{t['port']}\n")
        _WG_PROCS[name] = subprocess.Popen([exe, "-c", cfg], **_NOWIN)
    for _ in range(30):
        if _port_open(t["port"]):
            log(T(f"VPN tunnel '{name}' up (127.0.0.1:{t['port']}).", f"VPN tunnel '{name}' chal gaya."))
            return t["port"]
        time.sleep(0.5)
    raise RuntimeError(f"tunnel '{name}' did not start (check the .conf file)")


def wg_stop_all():
    for p in list(_WG_PROCS.values()):
        try:
            p.terminate()
        except Exception:
            pass
    _WG_PROCS.clear()


import atexit as _atexit
_atexit.register(wg_stop_all)


def socks_ipinfo(port, timeout=15):
    """Local SOCKS5 (no auth) ke zariye ip-api.com se IP/city/timezone."""
    import socket, struct
    s = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout)
    try:
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            raise RuntimeError("socks handshake failed")
        host = b"ip-api.com"
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack(">H", 80))
        rep = s.recv(10)
        if len(rep) < 2 or rep[1] != 0:
            raise RuntimeError("socks connect failed")
        s.sendall(b"GET /json/?fields=status,query,city,regionName,country,countryCode,timezone,lat,lon,isp "
                  b"HTTP/1.0\r\nHost: ip-api.com\r\n\r\n")
        data = b""
        while True:
            ch = s.recv(4096)
            if not ch:
                break
            data += ch
        body = data.split(b"\r\n\r\n", 1)[-1]
        return json.loads(body.decode("utf-8", "ignore"))
    finally:
        s.close()


# ============================================================
# FB BROWSER — apna built-in anti-detect browser (ixBrowser ki zaroorat nahi)
# Har ID: alag profile folder, alag fingerprint (screen, CPU, RAM, WebGL,
# canvas/audio noise), proxy ke hisaab se timezone + language, WebRTC leak band.
# ============================================================
_GPUS = [
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Series Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]
_SCREENS = [(1920, 1080), (1366, 768), (1536, 864), (1440, 900), (1600, 900), (1280, 720), (1680, 1050)]


def make_fingerprint(acc_id):
    """Har ID ka apna pakka (hamesha same) fingerprint."""
    import random as _r, hashlib as _h
    rnd = _r.Random(int(_h.md5(str(acc_id).encode()).hexdigest()[:12], 16))
    w, h = rnd.choice(_SCREENS)
    vendor, renderer = rnd.choice(_GPUS)
    return {"screen_w": w, "screen_h": h, "cpu": rnd.choice([4, 6, 8, 8, 12, 16]),
            "ram": rnd.choice([4, 8, 8, 16]), "webgl_vendor": vendor, "webgl_renderer": renderer,
            "noise": rnd.randint(1, 10 ** 6)}


def _geo_for_proxy(proxy):
    """Proxy ke IP se timezone / language / location. Na mile to None."""
    import urllib.request
    try:
        handlers = []
        if proxy and proxy.get("server", "").startswith("http"):
            srv = proxy["server"]
            if proxy.get("username"):
                scheme, rest = srv.split("://", 1)
                srv = f"{scheme}://{proxy['username']}:{proxy.get('password', '')}@{rest}"
            handlers.append(urllib.request.ProxyHandler({"http": srv, "https": srv}))
        elif proxy and proxy.get("server", "").startswith("socks5://127.0.0.1:") and not proxy.get("username"):
            j = socks_ipinfo(proxy["server"].rsplit(":", 1)[1])
            return j if j.get("status") == "success" else None
        elif proxy:
            return None      # socks: python se check nahi; local settings
        op = urllib.request.build_opener(*handlers)
        j = json.loads(op.open("http://ip-api.com/json/?fields=status,countryCode,timezone,lat,lon,query",
                               timeout=12).read().decode())
        if j.get("status") == "success":
            return j
    except Exception:
        pass
    return None


_CC_LANG = {"US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU", "IN": "en-IN", "PK": "en-US",
            "DE": "de-DE", "FR": "fr-FR", "ES": "es-ES", "IT": "it-IT", "BR": "pt-BR", "TR": "tr-TR",
            "AE": "en-US", "SA": "en-US", "NL": "nl-NL", "PL": "pl-PL", "RU": "ru-RU", "JP": "ja-JP"}


def _stealth_js(fp, lang):
    langs = json.dumps([lang, lang.split("-")[0]] if "-" in lang else [lang])
    return """
(() => {
  const fp = %s;
  const def = (o, k, v) => { try { Object.defineProperty(o, k, {get: () => v, configurable: true}); } catch (e) {} };
  def(Navigator.prototype, 'webdriver', false);
  def(Navigator.prototype, 'hardwareConcurrency', fp.cpu);
  def(Navigator.prototype, 'deviceMemory', fp.ram);
  def(Navigator.prototype, 'languages', %s);
  def(Navigator.prototype, 'platform', 'Win32');
  def(Screen.prototype, 'width', fp.screen_w); def(Screen.prototype, 'height', fp.screen_h);
  def(Screen.prototype, 'availWidth', fp.screen_w); def(Screen.prototype, 'availHeight', fp.screen_h - 40);
  if (!window.chrome) { window.chrome = {runtime: {}}; }
  for (const P of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!P) continue;
    const gp = P.prototype.getParameter;
    P.prototype.getParameter = function (p) {
      if (p === 37445) return fp.webgl_vendor;
      if (p === 37446) return fp.webgl_renderer;
      return gp.apply(this, arguments);
    };
  }
  // canvas noise (har ID ka apna)
  const seed = fp.noise;
  const tdu = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function () {
    try {
      const c = this.getContext('2d');
      if (c && this.width && this.height) {
        const x = seed %% this.width, y = (seed >> 3) %% this.height;
        const d = c.getImageData(x, y, 1, 1); d.data[0] = (d.data[0] + (seed %% 3) + 1) %% 255;
        c.putImageData(d, x, y);
      }
    } catch (e) {}
    return tdu.apply(this, arguments);
  };
  const gcd = AudioBuffer.prototype.getChannelData;
  AudioBuffer.prototype.getChannelData = function () {
    const r = gcd.apply(this, arguments);
    try { for (let i = 0; i < r.length; i += 100) r[i] += ((seed %% 7) + 1) * 1e-7; } catch (e) {}
    return r;
  };
  const oq = window.navigator.permissions && window.navigator.permissions.query;
  if (oq) window.navigator.permissions.query = (p) =>
    p && p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : oq(p);
})();
""" % (json.dumps(fp), langs)


_TILE_N = 4          # kitne browser ek sath khulenge (pool set karta hai)


def set_tile_count(n):
    global _TILE_N
    try:
        _TILE_N = max(1, int(n))
    except Exception:
        pass


def _work_area_dip():
    """Screen ka kaam wala hissa (taskbar ke bagair), Chrome ke DIP units mein: (x, y, w, h)."""
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        k = 96.0 / (dpi or 96)
        return int(r.left * k), int(r.top * k), int((r.right - r.left) * k), int((r.bottom - r.top) * k)
    except Exception:
        return 0, 0, 1600, 860


def _tile_rect(slot, n):
    """n browsers ko poori screen mein barabar baanto. Har browser ka (x, y, w, h)."""
    import math
    X, Y, W, H = _work_area_dip()
    n = max(1, n)
    if n == 1:
        # akela browser poori screen cover na kare — fix size, upar baayen
        return X, Y, int(min(max(W * 0.5, 900), 1200, W)), int(min(H, 1000))
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        w, h = W / cols, H / rows
        score = abs((w / h) - 0.9)            # browser ka shape thora lamba (portrait/square) behtar
        score += (cols * rows - n) * 0.35     # khali khane kam
        if w < 420:
            score += 2 + (420 - w) / 100      # bohot patla na ho
        if h < 380:
            score += 2 + (380 - h) / 100      # bohot chhota na ho
        if best is None or score < best[0]:
            best = (score, cols, rows)
    _, cols, rows = best
    i = slot % (cols * rows)
    c, r = i % cols, i // cols
    w, h = int(W / cols), int(H / rows)
    w = min(w, 1200)                          # bohot chaura bhi na ho
    return X + c * w, Y + r * h, max(w, 420), max(h, 360)


_MEDIA_LOCK = threading.Lock()
_MEDIA_TAKEN = set()          # is run mein jo photo/video kisi ID ne le li (doosri ID ko alag mile)
_GRID_LOCK = threading.Lock()
_GRID_USED = set()


def _grid_slot():
    with _GRID_LOCK:
        i = 0
        while i in _GRID_USED:
            i += 1
        _GRID_USED.add(i)
        return i


def _grid_free(i):
    with _GRID_LOCK:
        _GRID_USED.discard(i)


def _fbb_prepare_profile(user_dir):
    """Chrome ka password manager / 'Sign in to facebook.com' dabba band karo, saved passwords mitao
    (bot khud login karta hai — ye dabbe bot ko rokte hain)."""
    d = os.path.join(user_dir, "Default")
    os.makedirs(d, exist_ok=True)
    for fn in ("Login Data", "Login Data-journal", "Login Data For Account", "Login Data For Account-journal"):
        try:
            fp = os.path.join(d, fn)
            if os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass
    for sd in ("Sessions", os.path.join("Default", "Sessions")):        # pichhle tabs ka record hatao
        try:
            sp = os.path.join(d, sd)
            if os.path.isdir(sp):
                shutil.rmtree(sp, ignore_errors=True)
        except Exception:
            pass
    pf = os.path.join(d, "Preferences")
    try:
        prefs = json.load(open(pf, encoding="utf-8")) if os.path.exists(pf) else {}
    except Exception:
        prefs = {}
    prefs["credentials_enable_service"] = False
    prefs.setdefault("session", {})["restore_on_startup"] = 5          # purane tabs wapas NA khulen
    prefs.setdefault("profile", {})["exit_type"] = "Normal"           # "Restore pages?" na pooche
    prefs["profile"]["exited_cleanly"] = True
    prefs.setdefault("ntp", {})["shortcust_visible"] = False      # new tab par shortcuts na dikhen (Chrome ka asli spelling)
    prefs["ntp"]["shortcuts_visible"] = False
    prefs["credentials_enable_autosignin"] = False
    prefs.setdefault("profile", {})["password_manager_enabled"] = False
    prefs["profile"]["password_manager_leak_detection"] = False
    prefs.setdefault("autofill", {})["profile_enabled"] = False
    prefs["autofill"]["credit_card_enabled"] = False
    try:
        with open(pf, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except Exception:
        pass


COUNTRIES = [tuple(x.split(":", 1)) for x in """AF:Afghanistan;AL:Albania;DZ:Algeria;AS:American Samoa;AD:Andorra;AO:Angola;AI:Anguilla;AQ:Antarctica;AG:Antigua and Barbuda;AR:Argentina;AM:Armenia;AW:Aruba;AU:Australia;AT:Austria;AZ:Azerbaijan;BS:Bahamas;BH:Bahrain;BD:Bangladesh;BB:Barbados;BY:Belarus;BE:Belgium;BZ:Belize;BJ:Benin;BM:Bermuda;BT:Bhutan;BO:Bolivia;BQ:Bonaire, Sint Eustatius and Saba;BA:Bosnia and Herzegovina;BW:Botswana;BV:Bouvet Island;BR:Brazil;IO:British Indian Ocean Territory;BN:Brunei Darussalam;BG:Bulgaria;BF:Burkina Faso;BI:Burundi;CV:Cabo Verde;KH:Cambodia;CM:Cameroon;CA:Canada;KY:Cayman Islands;CF:Central African Republic;TD:Chad;CL:Chile;CN:China;CX:Christmas Island;CC:Cocos (Keeling) Islands;CO:Colombia;KM:Comoros;CG:Congo;CD:Congo, The Democratic Republic of the;CK:Cook Islands;CR:Costa Rica;HR:Croatia;CU:Cuba;CW:Curaçao;CY:Cyprus;CZ:Czechia;CI:Côte d'Ivoire;DK:Denmark;DJ:Djibouti;DM:Dominica;DO:Dominican Republic;EC:Ecuador;EG:Egypt;SV:El Salvador;GQ:Equatorial Guinea;ER:Eritrea;EE:Estonia;SZ:Eswatini;ET:Ethiopia;FK:Falkland Islands (Malvinas);FO:Faroe Islands;FJ:Fiji;FI:Finland;FR:France;GF:French Guiana;PF:French Polynesia;TF:French Southern Territories;GA:Gabon;GM:Gambia;GE:Georgia;DE:Germany;GH:Ghana;GI:Gibraltar;GR:Greece;GL:Greenland;GD:Grenada;GP:Guadeloupe;GU:Guam;GT:Guatemala;GG:Guernsey;GN:Guinea;GW:Guinea-Bissau;GY:Guyana;HT:Haiti;HM:Heard Island and McDonald Islands;VA:Holy See (Vatican City State);HN:Honduras;HK:Hong Kong;HU:Hungary;IS:Iceland;IN:India;ID:Indonesia;IR:Iran;IQ:Iraq;IE:Ireland;IM:Isle of Man;IL:Israel;IT:Italy;JM:Jamaica;JP:Japan;JE:Jersey;JO:Jordan;KZ:Kazakhstan;KE:Kenya;KI:Kiribati;KW:Kuwait;KG:Kyrgyzstan;LA:Laos;LV:Latvia;LB:Lebanon;LS:Lesotho;LR:Liberia;LY:Libya;LI:Liechtenstein;LT:Lithuania;LU:Luxembourg;MO:Macao;MG:Madagascar;MW:Malawi;MY:Malaysia;MV:Maldives;ML:Mali;MT:Malta;MH:Marshall Islands;MQ:Martinique;MR:Mauritania;MU:Mauritius;YT:Mayotte;MX:Mexico;FM:Micronesia, Federated States of;MD:Moldova;MC:Monaco;MN:Mongolia;ME:Montenegro;MS:Montserrat;MA:Morocco;MZ:Mozambique;MM:Myanmar;NA:Namibia;NR:Nauru;NP:Nepal;NL:Netherlands;NC:New Caledonia;NZ:New Zealand;NI:Nicaragua;NE:Niger;NG:Nigeria;NU:Niue;NF:Norfolk Island;KP:North Korea;MK:North Macedonia;MP:Northern Mariana Islands;NO:Norway;OM:Oman;PK:Pakistan;PW:Palau;PS:Palestine, State of;PA:Panama;PG:Papua New Guinea;PY:Paraguay;PE:Peru;PH:Philippines;PN:Pitcairn;PL:Poland;PT:Portugal;PR:Puerto Rico;QA:Qatar;RO:Romania;RU:Russia;RW:Rwanda;RE:Réunion;BL:Saint Barthélemy;SH:Saint Helena, Ascension and Tristan da Cunha;KN:Saint Kitts and Nevis;LC:Saint Lucia;MF:Saint Martin (French part);PM:Saint Pierre and Miquelon;VC:Saint Vincent and the Grenadines;WS:Samoa;SM:San Marino;ST:Sao Tome and Principe;SA:Saudi Arabia;SN:Senegal;RS:Serbia;SC:Seychelles;SL:Sierra Leone;SG:Singapore;SX:Sint Maarten (Dutch part);SK:Slovakia;SI:Slovenia;SB:Solomon Islands;SO:Somalia;ZA:South Africa;GS:South Georgia and the South Sandwich Islands;KR:South Korea;SS:South Sudan;ES:Spain;LK:Sri Lanka;SD:Sudan;SR:Suriname;SJ:Svalbard and Jan Mayen;SE:Sweden;CH:Switzerland;SY:Syria;TW:Taiwan;TJ:Tajikistan;TZ:Tanzania;TH:Thailand;TL:Timor-Leste;TG:Togo;TK:Tokelau;TO:Tonga;TT:Trinidad and Tobago;TN:Tunisia;TM:Turkmenistan;TC:Turks and Caicos Islands;TV:Tuvalu;TR:Türkiye;UG:Uganda;UA:Ukraine;AE:United Arab Emirates;GB:United Kingdom;US:United States;UM:United States Minor Outlying Islands;UY:Uruguay;UZ:Uzbekistan;VU:Vanuatu;VE:Venezuela;VN:Vietnam;VG:Virgin Islands, British;VI:Virgin Islands, U.S.;WF:Wallis and Futuna;EH:Western Sahara;YE:Yemen;ZM:Zambia;ZW:Zimbabwe;AX:Åland Islands""".split(";")]   # (code, naam)


def ip_filter_verdict(cc, cfg=None):
    """Block IP / Allow IP (Bob Prime jaisa). Return '' = chalne do, warna wajah."""
    cfg = cfg or load_settings()
    cc = (cc or "").upper()
    if cfg.get("ipf_on") is None:            # kabhi set nahi kiya -> shuru mein Pakistan BLOCK
        return "blocked" if cc == "PK" else ""
    if not cfg.get("ipf_on"):
        return ""
    lst = [c.upper() for c in (cfg.get("ipf_list") or [])]
    if not lst:
        return ""
    if cfg.get("ipf_mode", "block") == "allow":
        return "" if cc in lst else "not in Allow list"
    return "blocked" if cc in lst else ""


def fb_browser_launch(pw, account, cfg, headless=False, log=print):
    """Apna browser kholo. Return (context, slot)."""
    acc = get_account(account["id"]) or account
    fp = acc.get("fingerprint")
    if not fp:
        fp = make_fingerprint(acc.get("profile") or acc["id"])
        update_account(acc["id"], fingerprint=fp)
    proxy = parse_proxy(acc.get("proxy", ""))
    if acc.get("proxy", "").strip() and not proxy:
        # asli IP se kabhi na kholo — ID bachao
        raise RuntimeError(T(f"Proxy/VPN '{acc['proxy']}' is not working — browser NOT opened (to protect the ID).",
                             f"Proxy/VPN '{acc['proxy']}' kaam nahi kar raha — ID bachane ke liye browser NAHI khola."))
    geo = acc.get("geo") if acc.get("geo_proxy") == acc.get("proxy", "") else None
    if not geo:
        geo = _geo_for_proxy(proxy)
        if geo:
            update_account(acc["id"], geo=geo, geo_proxy=acc.get("proxy", ""))
    lang = cfg.get("fbb_lang") or (_CC_LANG.get((geo or {}).get("countryCode", ""), "en-US"))
    tz = (geo or {}).get("timezone") or None
    per_row = max(1, int(cfg.get("fbb_per_row", 4) or 4))
    ww, wh = int(cfg.get("fbb_w", 900) or 900), int(cfg.get("fbb_h", 800) or 800)
    slot = _grid_slot()
    if cfg.get("fbb_auto_tile", True):
        x, y, ww, wh = _tile_rect(slot, _TILE_N)     # khud poori screen mein fit
    else:
        x, y = (slot % per_row) * (ww // 2), (slot // per_row % 3) * 60
    _fbb_prepare_profile(session_dir_for(acc["id"]))
    args = ["--no-first-run",
            "--no-default-browser-check", f"--lang={lang}",
            "--disable-save-password-bubble",
            "--disable-features=PasswordManagerOnboarding,PasswordLeakDetection,AutofillServerCommunication,"
            "FedCm,FedCmAutoReauthn,PasswordGenerationExperiment,Translate",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            f"--window-position={x},{y}", f"--window-size={ww},{wh}"]
    if cfg.get("fbb_mute", True):
        args.append("--mute-audio")
    kw = dict(user_data_dir=session_dir_for(acc["id"]), headless=headless, args=args,
              no_viewport=True, locale=lang, chromium_sandbox=True,
              ignore_default_args=["--enable-automation", "--no-sandbox"])
    if tz:
        kw["timezone_id"] = tz
    if geo and geo.get("lat") is not None:
        kw["geolocation"] = {"latitude": geo["lat"], "longitude": geo["lon"]}
        kw["permissions"] = ["geolocation"]
    if proxy:
        kw["proxy"] = proxy
    eng = cfg.get("fbb_engine", "chrome")      # asli Chrome = zyada natural
    if eng == "chrome":
        kw["channel"] = "chrome"
    elif eng == "msedge":
        kw["channel"] = "msedge"
    try:
        try:
            ctx = pw.chromium.launch_persistent_context(**kw)
        except Exception as e:
            if kw.pop("channel", None):
                log(T(f"[{acc['name']}] Chrome not found — using built-in Chromium.",
                      f"[{acc['name']}] Chrome nahi mila — built-in Chromium use ho raha hai."))
            try:
                ctx = pw.chromium.launch_persistent_context(**kw)
            except Exception as e2:
                if "Executable doesn't exist" in str(e2) or "playwright install" in str(e2):
                    _pw_install_chromium(log)
                    ctx = pw.chromium.launch_persistent_context(**kw)
                else:
                    raise
    except Exception:
        _grid_free(slot)
        raise
    ctx.add_init_script(_stealth_js(fp, lang))
    for i_, _pg in enumerate(list(ctx.pages)):     # sirf ek khaali tab rakho (purane tabs band)
        try:
            if i_ == 0:
                _pg.goto("about:blank")
            else:
                _pg.close()
        except Exception:
            pass
    # ---- CHECK IP (Bob Prime jaisa): browser khulte hi IP + city + country ----
    block_msg = ""
    ip_ok = False
    try:
        _pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        _pg.goto("http://ip-api.com/json/?fields=status,query,city,regionName,country,countryCode,timezone,isp",
                 timeout=25000)
        info = json.loads(_pg.inner_text("body"))
        if info.get("status") == "success":
            ip_ok = True
            update_account(acc["id"], ip_info=info, last_ip=info.get("query"))
            log(T(f"[{acc['name']}] CHECK IP: {info.get('query')} — {info.get('city')}, {info.get('regionName')}, "
                  f"{info.get('country')} ({info.get('isp', '')})",
                  f"[{acc['name']}] CHECK IP: {info.get('query')} — {info.get('city')}, {info.get('regionName')}, "
                  f"{info.get('country')} ({info.get('isp', '')})"))
            try:
                APP_INSTANCE.after(0, APP_INSTANCE._refresh_all_panels)
            except Exception:
                pass
            if tz and info.get("timezone") and info["timezone"] != tz:
                update_account(acc["id"], geo=None)   # agli dafa sahi timezone
            why = ip_filter_verdict(info.get("countryCode"), cfg)
            if why:
                msg = T(f"[{acc['name']}] BLOCK IP: {info.get('country')} IP ({why}) — nothing will run on this ID "
                        f"(no login / upload / active). Turn on the VPN / set a tunnel.",
                        f"[{acc['name']}] BLOCK IP: {info.get('country')} ka IP ({why}) — is ID par kuch nahi chalega "
                        f"(na login / upload / active). VPN on karein / tunnel lagayein.")
                log(msg)
                block_msg = msg
                if cfg.get("_manual_open"):
                    # manual: doosri sites chalen, Facebook domains band
                    def _block(route):
                        try:
                            route.abort()
                        except Exception:
                            pass
                    for pat in ("**/*facebook.com/**", "**/*facebook.net/**", "**/*fbcdn.net/**",
                                "**/*fb.com/**", "**/*messenger.com/**"):
                        ctx.route(pat, _block)
        _pg.goto("about:blank")
    except Exception as e:
        log(T(f"[{acc['name']}] CHECK IP failed: {e.__class__.__name__}", f"[{acc['name']}] CHECK IP nahi hua."))
        try:
            time.sleep(1)
            (ctx.pages[0] if ctx.pages else ctx.new_page()).goto("about:blank")
        except Exception:
            pass
    if not ip_ok and cfg.get("ipf_on", True) and not cfg.get("_manual_open"):
        block_msg = T(f"[{acc['name']}] BLOCK IP is ON but the IP could not be checked — not opening Facebook (safety).",
                      f"[{acc['name']}] BLOCK IP ON hai magar IP check nahi hua — hifazat ke liye Facebook nahi khola.")
        log(block_msg)
    if block_msg and not cfg.get("_manual_open"):
        try:
            ctx.close()
        except Exception:
            pass
        _grid_free(slot)
        raise RuntimeError(block_msg)
    log(T(f"[{acc['name']}] FB Browser opened — {fp['screen_w']}x{fp['screen_h']}, {fp['cpu']} CPU, "
          f"{lang}, {tz or 'local time'}{', proxy ' + proxy['server'] if proxy else ''}.",
          f"[{acc['name']}] FB Browser khula — {fp['screen_w']}x{fp['screen_h']}, {fp['cpu']} CPU, "
          f"{lang}, {tz or 'local time'}{', proxy ' + proxy['server'] if proxy else ''}."))
    return ctx, slot


def _pw_install_chromium(log=print):
    """Playwright ka Chromium download (exe mode mein bhi)."""
    log(T("Downloading built-in Chromium (one time, please wait)...",
          "Built-in Chromium download ho raha hai (sirf 1 dafa, intezaar karein)..."))
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        exe = compute_driver_executable()
        cmd = list(exe) if isinstance(exe, (tuple, list)) else [str(exe)]
        subprocess.run(cmd + ["install", "chromium"], env=get_driver_env(), check=True, **_NOWIN)
    except Exception:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, **_NOWIN)
    log(T("Chromium ready.", "Chromium tayyar."))


def check_proxy(text, timeout=12):
    """Proxy check: return (ok, latency_ms, ip/country or error)."""
    import urllib.request
    p = parse_proxy(text)
    if not p:
        return False, 0, "invalid"
    if p["server"].startswith("socks5://127.0.0.1:"):
        t0 = time.time()
        try:
            j = socks_ipinfo(p["server"].rsplit(":", 1)[1])
            return True, int((time.time() - t0) * 1000), f"{j.get('query')} {j.get('countryCode')} {j.get('city', '')}"
        except Exception as e:
            return False, 0, str(e)[:80]
    if not p["server"].startswith("http"):
        return None, 0, "socks (check in browser)"
    srv = p["server"]
    if p.get("username"):
        scheme, rest = srv.split("://", 1)
        srv = f"{scheme}://{p['username']}:{p.get('password', '')}@{rest}"
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": srv, "https": srv}))
    t0 = time.time()
    try:
        j = json.loads(op.open("http://ip-api.com/json/?fields=status,countryCode,query", timeout=timeout).read().decode())
        return True, int((time.time() - t0) * 1000), f"{j.get('query')} {j.get('countryCode')}"
    except Exception as e:
        return False, 0, str(e)[:80]


# ============ ixBrowser FREE mode (bina Local API) ============
# ixBrowser profile ko jab aap khud kholte ho, to uski poori launch command
# (fingerprint settings samet) Windows process se parh kar save kar lete hain.
# Baad mein app wahi command + debugging port laga kar profile khud kholti hai.
def _ix_api_up(port=53200):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.5):
            return True
    except Exception:
        return False


def _split_cmdline(cl):
    try:
        import ctypes
        from ctypes import wintypes
        f = ctypes.windll.shell32.CommandLineToArgvW
        f.restype = ctypes.POINTER(wintypes.LPWSTR)
        f.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        n = ctypes.c_int()
        ptr = f(cl, ctypes.byref(n))
        args = [ptr[i] for i in range(n.value)]
        ctypes.windll.kernel32.LocalFree(ptr)
        return args
    except Exception:
        import shlex
        return shlex.split(cl, posix=False)


def _argval(argv, name):
    for a in argv:
        if a.startswith(name + "="):
            return a.split("=", 1)[1].strip('"')
    return ""


def _ix_udd_key(udd):
    return os.path.basename(os.path.normpath(udd.replace("\\\\", "\\"))).lower()


def _ix_profile_fb_user(udd):
    """Profile folder ki choti files mein 'name,url,username,password' wali list hoti hai."""
    try:
        d = udd.replace("\\\\", "\\")
        for fn in os.listdir(d):
            fp = os.path.join(d, fn)
            if not os.path.isfile(fp) or os.path.getsize(fp) > 5000:
                continue
            with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            if not txt.startswith("name,url,username,password"):
                continue
            for line in txt.splitlines()[1:]:
                parts = line.split(",")
                if len(parts) >= 3 and "facebook" in (parts[0] + parts[1]).lower() and parts[2].strip():
                    return parts[2].strip()
    except Exception:
        pass
    return ""


def ix_running_profiles():
    """Abhi khule ixBrowser profiles: [{pid, argv, key, fb_user}]"""
    if os.name != "nt":
        return []
    try:
        cmd = ("Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
               "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                             text=True, timeout=40, creationflags=0x08000000).stdout.strip()
        rows = json.loads(out) if out else []
        if isinstance(rows, dict):
            rows = [rows]
    except Exception:
        return []
    res = []
    for r in rows:
        cl = (r or {}).get("CommandLine") or ""
        if "ixbrowser" not in cl.lower() or "--type=" in cl or "--user-data-dir" not in cl:
            continue
        argv = _split_cmdline(cl)
        udd = _argval(argv, "--user-data-dir")
        if not udd:
            continue
        res.append({"pid": r.get("ProcessId"), "argv": argv, "key": _ix_udd_key(udd),
                    "fb_user": _ix_profile_fb_user(udd)})
    return res


def ix_saved_cmds():
    return load_settings().get("ix_cmds", {})


def ix_save_cmd(key, argv):
    s = load_settings()
    s.setdefault("ix_cmds", {})[key] = argv
    save_settings(s)


def ix_capture_open_profiles():
    """Khule profiles ki command save karo aur email se IDs ke sath jor do.
    Return (matched[(acc_name, key)], unmatched[profile dict])"""
    accs = load_accounts()
    matched, unmatched = [], []
    for pr in ix_running_profiles():
        ix_save_cmd(pr["key"], pr["argv"])
        acc = None
        fu = (pr["fb_user"] or "").lower()
        for a in accs:
            if a.get("ix_key") == pr["key"] or (fu and a.get("email", "").strip().lower() == fu):
                acc = a; break
        if acc:
            update_account(acc["id"], ix_key=pr["key"])
            matched.append((acc["name"], pr["key"]))
        else:
            unmatched.append(pr)
    return matched, unmatched


def _kill_pid_tree(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       timeout=20, creationflags=0x08000000)
    except Exception:
        pass


def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def profiles_list():
    return load_settings().get("profiles", [])


def profiles_create(n):
    lst = profiles_list()
    k = len(lst)
    for _ in range(max(0, int(n))):
        k += 1
        name = f"P{k:02d}"
        while name in lst:
            k += 1; name = f"P{k:02d}"
        lst.append(name)
    save_settings({"profiles": lst})
    return lst


def profiles_auto_assign(acc_ids=None, per_profile=1):
    """Jin IDs ka profile nahi, unhein khali profiles do (tarteeb se). Kam pade to naye bana do."""
    accs = load_accounts()
    used = {}
    for a in accs:
        if a.get("profile"):
            used[a["profile"]] = used.get(a["profile"], 0) + 1
    need = [a for a in accs if not a.get("profile") and (acc_ids is None or a["id"] in acc_ids)]
    profs = profiles_list()
    free = [p for p in profs if used.get(p, 0) < per_profile]
    if len(free) * per_profile < len(need):
        profiles_create(-(-(len(need) - len(free) * per_profile) // per_profile))
        profs = profiles_list()
        free = [p for p in profs if used.get(p, 0) < per_profile]
    n = 0
    for a in need:
        while free and used.get(free[0], 0) >= per_profile:
            free.pop(0)
        if not free:
            break
        p = free[0]
        a["profile"] = p; used[p] = used.get(p, 0) + 1; n += 1
    save_accounts(accs)
    return n


ACCOUNT_FIELDS = [("no", "No", 50), ("profile", "Profile", 80), ("name", "Name", 150), ("uid", "UID", 150),
                  ("email", "Email", 230), ("email2", "Email2", 170), ("password", "Pass", 120),
                  ("twofa", "2FA", 210), ("primary_loc", "Primary location", 150), ("created", "Creation date", 120),
                  ("ip", "IP location", 230), ("status", "Status", 110), ("last_update", "Last update", 140),
                  ("group", "Group", 110), ("country", "Country", 90), ("dob", "DOB", 100), ("gender", "Gender", 80),
                  ("pages", "Pages (ticked)", 170)]


def account_row(a, i):
    ii = a.get("ip_info") or {}
    # IP location: sirf mulk + shehar (e.g. "US New York") — IP number nahi
    ip = " ".join(x for x in (ii.get("countryCode", ""), ii.get("city", "")) if x)
    pg = a.get("pages") or []
    on = [p["name"] for p in pg if p.get("enabled")]
    pages = f"{len(on)}/{len(pg)}" + (f" ✔ {on[0]}" if on else "") if pg else (a.get("page_name") or "-")
    vals = {"no": i, "ip": ip or "-", "pages": pages}
    return [vals.get(k, a.get(k, "")) for k, _, _ in ACCOUNT_FIELDS]


def export_accounts(path, accs, mode="full"):
    """mode: full (CSV/Excel), txt (email|pass|2fa), uid (sirf UID)."""
    import csv
    if mode == "uid":
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(a.get("uid", "") for a in accs if a.get("uid")) + "\n")
    elif mode == "txt":
        with open(path, "w", encoding="utf-8") as f:
            for a in accs:
                f.write("|".join([a.get("email", ""), a.get("password", ""), a.get("twofa", ""),
                                  a.get("uid", ""), a.get("name", "")]) + "\n")
    else:
        keys = [k for k, _, _ in ACCOUNT_FIELDS if k not in ("no", "pages")] + ["proxy", "page_name"]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([dict((k, h) for k, h, _ in ACCOUNT_FIELDS).get(k, k) for k in keys])
            for a in accs:
                row = account_row(a, 0)
                d = dict(zip([k for k, _, _ in ACCOUNT_FIELDS], row))
                w.writerow([d.get(k, a.get(k, "")) for k in keys])
    # har ID ki asli DP bhi saath wale folder mein (Bob Prime jaisa)
    try:
        dps = [a for a in accs if a.get("dp") and os.path.isfile(a["dp"])]
        if dps:
            dd = os.path.splitext(path)[0] + "_DP"
            os.makedirs(dd, exist_ok=True)
            for a in dps:
                nm = (a.get("uid") or a.get("email") or a.get("id", "id")).replace("@", "_")
                shutil.copy2(a["dp"], os.path.join(dd, f"{nm}.jpg"))
    except Exception:
        pass
    return len(accs)


IMPORT_SLOTS = ["Email", "Pass", "2FA", "UID", "Email2", "Name", "Skip"]


def parse_with_format(line, fmt):
    """fmt jaise ["UID","Pass","2FA","Email","Email2"]. Separator: | , ; tab ya :"""
    import re as _re
    line = (line or "").strip().lstrip("\ufeff")
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in _re.split(r"[|;,\t]+", line)] if _re.search(r"[|;,\t]", line) else line.split(":")
    d = {}
    for slot, val in zip(fmt, parts):
        if slot != "Skip" and val:
            d[slot] = val.replace(" ", "") if slot == "2FA" else val
    return d


def parse_account_line(line):
    """Ek line se (login, password, twofa, extra[]) nikalo.
    Formats: email|pass|2FA , email:pass:2FA , email pass 2FA (2FA me spaces bhi chal jaate hain)."""
    import re as _re
    line = (line or "").strip().lstrip("\ufeff")
    if not line or line.startswith("#"):
        return None
    if _re.search(r"[|;,\t]", line):
        parts = [p.strip() for p in _re.split(r"[|;,\t]+", line) if p.strip()]
    elif ":" in line and " " not in line:
        parts = [p for p in line.split(":") if p]
    else:
        parts = line.split()
    if len(parts) < 2:
        return None
    if "@" in parts[0] or _re.match(r"^\+?[\d\s-]{7,}$", parts[0]):
        li = 0                              # pehla token hi email/phone hai
    else:
        li = next((i for i, p in enumerate(parts) if "@" in p), 0)
    login = parts[li]
    rest = parts[:li] + parts[li + 1:]
    password = rest.pop(0) if rest else ""
    b32 = _re.compile(r"^[A-Za-z2-7]+$")
    twofa = ""
    for i, p in enumerate(rest):            # poora secret ek token me
        q = p.replace(" ", "")
        if len(q) >= 16 and b32.match(q):
            twofa = q.upper(); rest.pop(i); break
    if not twofa:                           # "ABCD EFGH IJKL MNOP" (4-4 ke group)
        i = 0
        while i < len(rest):
            j = i
            while j < len(rest) and len(rest[j]) == 4 and b32.match(rest[j]):
                j += 1
            if j - i >= 4:
                twofa = "".join(rest[i:j]).upper(); del rest[i:j]; break
            i = j + 1 if j == i else j
    return login, password, twofa, rest


def posting_targets(acc):
    """Is ID ke wo pages jin par posting ON hai. Koi tick nahi to purana 'page_name'."""
    on = [p for p in acc.get("pages", []) if p.get("enabled")]
    if on:
        return on
    if acc.get("page_name"):
        return [{"name": acc["page_name"], "url": ""}]
    return [{"name": "", "url": ""}]   # apni profile/feed par


# Folder se media files nikalna (kind ke hisaab se)
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def list_media_in_folder(folder, kind):
    folder = (folder or "").strip()
    if not folder or not os.path.isdir(folder):
        return []
    exts = IMAGE_EXT if kind == "photo" else VIDEO_EXT
    out = []
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith(exts):
            out.append(os.path.join(folder, fn))
    return out


def parse_proxy(text):
    """Flexible proxy -> Playwright dict. Return None agar khali/galat.
    Accepts:
      host:port
      host:port:user:pass
      http://host:port   |  http://user:pass@host:port
      socks5://host:port |  socks5://user:pass@host:port
    """
    s = (text or "").strip()
    if not s:
        return None
    if s.lower().startswith("wg:"):          # Surfshark/WireGuard tunnel
        try:
            port = wg_ensure(s[3:].strip())
            return {"server": f"socks5://127.0.0.1:{port}"}
        except Exception as e:
            try:
                APP_INSTANCE.log_q.put(f"[VPN] {e}")
            except Exception:
                pass
            return None
    scheme = "http"
    if "://" in s:
        scheme, s = s.split("://", 1)
    user = pwd = None
    if "@" in s:
        cred, s = s.rsplit("@", 1)
        if ":" in cred:
            user, pwd = cred.split(":", 1)
        else:
            user = cred
    parts = s.split(":")
    if len(parts) == 4 and user is None:   # host:port:user:pass
        host, port, user, pwd = parts
    elif len(parts) >= 2:
        host, port = parts[0], parts[1]
    else:
        return None
    proxy = {"server": f"{scheme}://{host}:{port}"}
    if user:
        proxy["username"] = user
    if pwd:
        proxy["password"] = pwd
    return proxy


def remove_account(acc_id):
    save_accounts([a for a in load_accounts() if a.get("id") != acc_id])


def restore_account(acc):
    """Delete ki hui ID waps (Ctrl+Z)."""
    accounts = load_accounts()
    if not any(a.get("id") == acc.get("id") for a in accounts):
        accounts.append(acc)
        save_accounts(accounts)


ACTIVE_FILE = os.path.join(DATA_DIR, "active_settings.json")
IX_USAGE_FILE = os.path.join(DATA_DIR, "ix_usage.json")


def _today_str():
    import datetime
    return datetime.date.today().isoformat()


def _ix_usage():
    u = _load(IX_USAGE_FILE, {})
    if u.get("date") != _today_str():
        u = {"date": _today_str(), "count": 0, "last_run": u.get("last_run", {})}
    u.setdefault("last_run", {})
    u.setdefault("count", 0)
    return u


def ix_remaining_today(max_per_day):
    return max(0, int(max_per_day) - _ix_usage().get("count", 0))


def ix_mark_open(acc_id):
    import time as _t
    u = _ix_usage()
    u["count"] = u.get("count", 0) + 1
    u["last_run"][acc_id] = _t.time()
    _save(IX_USAGE_FILE, u)


def ix_order_accounts(accounts):
    """Jo sab se pehle nahi chali / sab se purani — woh pehle (rotation)."""
    last = _ix_usage().get("last_run", {})
    return sorted(accounts, key=lambda a: last.get(a["id"], 0))


# ---------- Active: gandi posts pehchanne ke lafz + naam se gender ----------
UNSAFE_WORDS = ("nude", "nudity", "naked", "xxx", "porn", "18+", "onlyfans", "only fans", "sexy", "hot girl", "hot video",
                "bikini", "lingerie", "leaked", "mms", "boobs", "nsfw", "adult video", "erotic", "seduct", "bra ",
                "panty", "hookup", "escort", " sex ", "milf", "thirst trap", "viral video girl", "uncensored",
                "desi bhabhi", "bhabhi", "hot dance", "mujra")

_MALE_NAMES = set("""muhammad mohammad mohammed muhammed m syed sayed ali ahmad ahmed hassan hasan hussain husain usman umar omar
abdul abdullah bilal hamza zain zaid danish kashif imran irfan asif arif adnan faisal fahad farhan rizwan rehan rehman rahman
salman sohail suhail shahid shoaib tariq tahir talha waqas waseem wasim yasir yousaf yusuf zubair zeeshan junaid jawad kamran
khalid nadeem naveed nasir noman nouman qasim raza saad sajid saqib shahzad shakeel sharjeel shehzad sufyan taimoor taimur umair
uzair waleed walid adil akram altaf amir aamir anwar arsalan asad awais babar daniyal ehsan fawad haris haroon ibrahim ijaz ilyas
ishaq jamal javed khurram mansoor mubashir mudassar muneeb mustafa nabeel naeem obaid owais rashid rauf sami samiullah sarmad
shan sheraz tanveer usama yasin zafar zahid zia ghulam malik rana ch chaudhry chaudhary hafiz mian sheikh raja sardar
john james robert michael william david richard joseph thomas charles christopher daniel matthew anthony mark donald steven paul
andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob gary nicholas eric jonathan stephen larry
justin scott brandon benjamin samuel gregory alexander frank patrick raymond jack dennis jerry tyler aaron jose adam nathan henry
douglas zachary peter kyle ethan walter noah jeremy christian keith roger terry gerald harold sean austin carl arthur lawrence
dylan jesse jordan bryan billy joe bruce gabriel logan albert willie alan juan wayne elijah randy roy vincent ralph eugene russell
bobby mason philip louis mike chris dave steve tom tony jim bob bill rick matt josh nick alex ben sam jake luke liam lucas oliver
raj rahul amit vijay sanjay suresh ramesh rakesh manoj deepak ravi arjun rohit sachin sunil anil ajay akash vikas vivek
""".split())
_FEMALE_NAMES = set("""ayesha aisha fatima fatma zainab maryam mariam khadija sana sara sarah hina hira iqra amna aqsa asma
bushra farah fiza huma kiran laiba maira mahnoor mehwish nadia naila nimra noor nida rabia rida saba sadia saima samina sobia
sumaira sundas tayyaba uzma zara zoya anum alina aliza areeba arooj ayza dua eman emaan hafsa hania hareem javeria komal
kinza mahira mehak minahil momina nazia rimsha rubina sehrish shazia sidra sumbal tahira tooba urooj yasmin zubaida bibi begum
mary patricia jennifer linda elizabeth barbara susan jessica karen nancy lisa betty margaret sandra ashley kimberly emily donna
michelle carol amanda dorothy melissa deborah stephanie rebecca sharon laura cynthia kathleen amy angela shirley anna brenda
pamela emma nicole helen samantha katherine christine debra rachel carolyn janet catherine heather diane ruth julie olivia
joyce virginia victoria kelly lauren christina joan evelyn judith megan andrea cheryl hannah jacqueline martha gloria teresa
ann madison frances kathryn janice jean abigail alice judy sophia grace denise amber doris marilyn danielle beverly isabella
theresa diana natalie brittany charlotte marie kayla alexis lori jenny kate katie sophie mia ella chloe lily zoe ava
priya pooja neha anjali sunita kavita rekha meena deepika anita riya simran kaur
""".split())


def guess_gender(full_name):
    """Naam se andaza: 'male' / 'female' / '' (pakka nahi). Pakka na ho to friend request NAHI."""
    import re as _re
    toks = [t for t in _re.split(r"[^a-z]+", (full_name or "").lower()) if t]
    if not toks:
        return ""
    if any(t in ("bibi", "begum", "kaur", "khatoon", "bano", "parveen", "sultana") for t in toks):
        return "female"
    first = toks[0]
    if first in ("muhammad", "mohammad", "m", "syed", "malik", "rana", "ch", "hafiz", "mian", "raja", "sardar", "sheikh") and len(toks) > 1:
        return "male" if toks[1] not in _FEMALE_NAMES else "female"
    if first in _FEMALE_NAMES:
        return "female"
    if first in _MALE_NAMES:
        return "male"
    return ""


def load_active_settings():
    return _load(ACTIVE_FILE, {})


def save_active_settings(s):
    _save(ACTIVE_FILE, s)


def load_settings():
    return _load(SETTINGS_FILE, {"browser": "fbbrowser", "browser_path": "",
                                 "use_ixbrowser": False, "ix_max_per_day": 10,
                                 "ix_limit_on": False, "daily_auto": False,
                                 "daily_time": "20:00", "daily_kind": "reel",
                                 "daily_threads": 5, "autostart": False,
                                 "daily_active": False, "daily_active_time": "10:00",
                                 "language": "en", "captcha_key": "",
                                 "local_ocr": True, "api_keys": [], "api_port": 8700,
                                 "tg_token": "", "tg_chat": "", "tunnel_on": False,
                                 "captcha_compat80": False, "ld_console_path": "",
                                 "ldw_folder": "", "ldw_page": "", "ldw_action": "reel",
                                 "ai_key": "", "ai_model": "llama-3.1-8b-instant",
                                 "ai_base": "https://api.groq.com/openai/v1"})


def save_settings(s):
    cur = _load(SETTINGS_FILE, {})
    if isinstance(cur, dict):
        cur.update(s)
        s = cur
    _save(SETTINGS_FILE, s)


# ---------- Per-page limit (kitni videos aik run mein) ----------
def load_page_settings():
    return _load(PAGE_SETTINGS_FILE, {})


def get_page_limit(page_name):
    """0 ka matlab koi limit nahi."""
    try:
        return int(load_page_settings().get(page_name.strip(), {}).get("max_per_run", 0))
    except Exception:
        return 0


def set_page_limit(page_name, n):
    data = load_page_settings()
    data.setdefault(page_name.strip(), {})["max_per_run"] = int(n)
    _save(PAGE_SETTINGS_FILE, data)


def get_page_daily_limit(page_name):
    """Is page par roz max kitni videos (0 = unlimited)."""
    try:
        return int(load_page_settings().get(page_name.strip(), {}).get("daily_limit", 0))
    except Exception:
        return 0


def set_page_daily_limit(page_name, n):
    data = load_page_settings()
    data.setdefault(page_name.strip(), {})["daily_limit"] = int(n)
    _save(PAGE_SETTINGS_FILE, data)


PAGE_DAILY_FILE = os.path.join(DATA_DIR, "page_daily.json")


def _page_daily():
    d = _load(PAGE_DAILY_FILE, {})
    if d.get("date") != _today_str():
        d = {"date": _today_str(), "counts": {}}
    d.setdefault("counts", {})
    return d


def page_posted_today(page_name):
    return int(_page_daily().get("counts", {}).get(page_name.strip(), 0))


def inc_page_posted(page_name):
    d = _page_daily()
    d["counts"][page_name.strip()] = d["counts"].get(page_name.strip(), 0) + 1
    _save(PAGE_DAILY_FILE, d)


def page_daily_remaining(page_name):
    limit = get_page_daily_limit(page_name)
    if limit <= 0:
        return 10 ** 9
    return max(0, limit - page_posted_today(page_name))


# ---------- History (per page + per id) ----------
PAGE_HISTORY_FILE = os.path.join(DATA_DIR, "page_history.json")
ID_HISTORY_FILE = os.path.join(DATA_DIR, "id_history.json")


def _now_str():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# LICENSE (malik = Danish). Key har PC ki Machine ID se bandhi, RSA se signed — sirf malik bana sakta hai.
# Cancel (revoke) list GitHub par: koi key cancel ho to us PC par app band.
# ============================================================
LIC_N = int("23865847406916263419611540106437989866924156495821584680249183874285501448269043955563420687110555944714907474639275490018179532216331020459998737893362850467263349748894376167727566575465505139937693164376448891836031126700850106402320369894294254743875140102179023762571758946399923157544232251435063575188716620452289053160152925418619627195280136537747202619582541205148866147974037044104671227314820883778912596218830637055091416954478342377550898632660484191460876810133026582382692986622842677916970364468474883891069781537105864157956727632285337385114605455818382743466460444022248926201114937855738122253861")
LIC_E = 65537
LIC_REVOKE_URL = "https://raw.githubusercontent.com/kashif55505/my-apps/main/facebook-uploader/licenses_revoked.json"
LIC_REVOKE_API = "repos/kashif55505/my-apps/contents/facebook-uploader/licenses_revoked.json"
_LIC = {"ok": None, "owner": False, "info": {}, "why": ""}


def machine_id():
    import hashlib
    raw = ""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                           winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0))
        raw = winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        import uuid
        raw = str(uuid.getnode())
    h = hashlib.sha256(("DK|" + raw + "|" + os.environ.get("COMPUTERNAME", "")).encode()).hexdigest().upper()[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def _b64u(b):
    import base64
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s_):
    import base64
    return base64.urlsafe_b64decode(s_ + "=" * (-len(s_) % 4))


def _lic_hash(payload_b):
    import hashlib
    return int.from_bytes(hashlib.sha256(payload_b).digest(), "big")


def owner_key_path():
    return os.path.join(DATA_DIR, "owner.key")


def owner_key():
    try:
        k = json.load(open(owner_key_path(), encoding="utf-8"))
        if int(k["n"]) == LIC_N:
            return k
    except Exception:
        pass
    return None


def owner_key_import(path):
    k = json.load(open(path, encoding="utf-8"))
    if int(k["n"]) != LIC_N:
        raise ValueError("This is not the owner key of this app.")
    os.makedirs(DATA_DIR, exist_ok=True)
    shutil.copy2(path, owner_key_path())
    return True


def make_license(name, mid, days=0):
    """Sirf malik: student ke naam + Machine ID se key."""
    import secrets
    k = owner_key()
    if not k:
        raise RuntimeError("Owner key missing")
    exp = time.strftime("%Y-%m-%d", time.localtime(time.time() + days * 86400)) if days else ""
    pl = {"m": mid.strip().upper(), "n": name.strip(), "e": exp, "k": secrets.token_hex(4)}
    pb = json.dumps(pl, separators=(",", ":")).encode()
    sig = pow(_lic_hash(pb), int(k["d"]), LIC_N)
    key = "DK1." + _b64u(pb) + "." + _b64u(sig.to_bytes((LIC_N.bit_length() + 7) // 8, "big"))
    return key, pl


def parse_license(key):
    """Key sahi hai? -> payload dict, warna None."""
    try:
        key = "".join((key or "").split())
        tag, p, sg = key.split(".")
        if tag != "DK1":
            return None
        pb = _b64u_dec(p)
        sig = int.from_bytes(_b64u_dec(sg), "big")
        if pow(sig, LIC_E, LIC_N) != _lic_hash(pb) % LIC_N:
            return None
        return json.loads(pb.decode())
    except Exception:
        return None


def lic_revoked_list(force=False):
    cache = os.path.join(DATA_DIR, "lic_revoked_cache.json")
    try:
        c = json.load(open(cache, encoding="utf-8"))
    except Exception:
        c = {"t": 0, "ids": []}
    if force or time.time() - c.get("t", 0) > 3 * 3600:
        try:
            import urllib.request
            r = urllib.request.urlopen(LIC_REVOKE_URL + f"?t={int(time.time())}", timeout=15)
            j = json.loads(r.read().decode())
            c = {"t": time.time(), "ids": list(j.get("revoked", []))}
            json.dump(c, open(cache, "w", encoding="utf-8"))
        except Exception:
            pass
    return set(c.get("ids", []))


def license_check(force=False):
    """_LIC bharo: owner? license sahi? (machine, expiry, cancel)."""
    if not owner_key():
        for cand in (os.path.join(os.path.expanduser("~"), "Downloads", "DK_owner.key"),
                     os.path.join(os.path.expanduser("~"), "Desktop", "DK_owner.key")):
            try:
                if os.path.isfile(cand):
                    owner_key_import(cand)
                    break
            except Exception:
                pass
    if owner_key():
        _LIC.update(ok=True, owner=True, info={"n": "Owner"}, why="")
        return _LIC
    key = load_settings().get("license_key", "")
    pl = parse_license(key)
    why = ""
    if not key:
        why = T("No license key.", "License key nahi hai.")
    elif not pl:
        why = T("License key is not valid.", "License key ghalat hai.")
    elif pl.get("m") != machine_id():
        why = T("This key belongs to another computer.", "Ye key kisi aur computer ki hai.")
    elif pl.get("e") and time.strftime("%Y-%m-%d") > pl["e"]:
        why = T(f"License expired on {pl['e']}.", f"License {pl['e']} ko khatam ho gaya.")
    elif pl.get("k") in lic_revoked_list(force):
        why = T("This license was cancelled by the owner.", "Ye license malik ne cancel kar diya hai.")
    _LIC.update(ok=not why, owner=False, info=pl or {}, why=why)
    return _LIC


def license_ok():
    if _LIC["ok"] is None:
        license_check()
    return bool(_LIC["ok"])


def lic_issued_path():
    return os.path.join(DATA_DIR, "licenses_issued.json")


def lic_issued_load():
    try:
        return json.load(open(lic_issued_path(), encoding="utf-8"))
    except Exception:
        return []


def lic_issued_save(lst):
    json.dump(lst, open(lic_issued_path(), "w", encoding="utf-8"), indent=1)


def lic_push_revoked(ids, gh_token):
    """Cancel list GitHub par likho (malik ka GitHub token)."""
    import urllib.request, base64
    hdr = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json",
           "User-Agent": "DK-Uploader"}
    url = "https://api.github.com/" + LIC_REVOKE_API
    sha = None
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=20)
        sha = json.loads(r.read().decode()).get("sha")
    except Exception:
        pass
    body = {"message": "Update revoked licenses",
            "content": base64.b64encode(json.dumps({"revoked": sorted(set(ids))}, indent=1).encode()).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=dict(hdr, **{"Content-Type": "application/json"}),
                                 method="PUT")
    urllib.request.urlopen(req, timeout=30).read()
    return True


# ---------- POST LOG (Logs Viewer) + PAGE STATS (Page Manager) ----------
_PLOG_LOCK = threading.Lock()


def _plog_path():
    return os.path.join(DATA_DIR, "post_log.json")


def post_log_load():
    try:
        return json.load(open(_plog_path(), encoding="utf-8"))
    except Exception:
        return []


def acc_number(acc_id):
    for i, a in enumerate(load_accounts(), 1):
        if a.get("id") == acc_id:
            return i
    return ""


def acc_city(acc_id):
    a = get_account(acc_id) or {}
    ii = a.get("ip_info") or {}
    return ii.get("city") or ii.get("countryCode") or ""


def post_log_add(page, ok, posted="", files="", err="", acc_id="", via="", city=""):
    """Har post ki ek line (browser + API)."""
    try:
        e = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "acc": acc_number(acc_id) if acc_id else "",
             "page": page, "ip": city or (acc_city(acc_id) if acc_id else ""), "ok": bool(ok),
             "posted": posted, "files": files, "err": str(err or "")[:200], "via": via}
        with _PLOG_LOCK:
            lst = post_log_load()
            lst.insert(0, e)
            json.dump(lst[:3000], open(_plog_path(), "w", encoding="utf-8"))
    except Exception:
        pass


_PSTAT_LOCK = threading.Lock()


def _pstat_path():
    return os.path.join(DATA_DIR, "page_stats.json")


def page_stats_load():
    try:
        return json.load(open(_pstat_path(), encoding="utf-8"))
    except Exception:
        return {}


def page_stats_set(name, **kw):
    """Page ka data (followers / reach / posts / monetization / status). Key = page ka naam."""
    if not name:
        return
    with _PSTAT_LOCK:
        d = page_stats_load()
        cur = d.setdefault(name, {})
        cur.update({k: v for k, v in kw.items() if v not in (None, "")})
        cur["updated"] = time.strftime("%Y-%m-%d")
        json.dump(d, open(_pstat_path(), "w", encoding="utf-8"))


def short_num(n):
    try:
        n = float(n)
    except Exception:
        return str(n or "")
    for div, suf in ((1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            return (f"{v:.1f}".rstrip("0").rstrip(".")) + suf
    return str(int(n))


def short_status(flags_text):
    """Lamba status -> chhota: 'Good' ya asal masla (jaise 'Recommendation suspended')."""
    t = (flags_text or "").lower()
    if not t or t.startswith("ok") or "good" in t:
        return "Good"
    if "not recommended" in t or "recommend" in t:
        return "Recommendation suspended"
    for k, lab in (("verify", "Verify required"), ("restricted", "Restricted"), ("copyright", "Copyright issue"),
                   ("violat", "Violation"), ("flagged", "Flagged"), ("unpublished", "Unpublished"),
                   ("at risk", "At risk"), ("not eligible", "Not eligible")):
        if k in t:
            return lab
    return "Unknown" if "unknown" in t else flags_text[:40]


def short_monet(money_text):
    """Sirf 'Stars setup' / 'CM setup' / 'Unlock CM' (jo mile)."""
    t = (money_text or "").lower()
    out = []
    if "star" in t:
        out.append("Stars setup")
    if "content monetization" in t or "in-stream" in t or "reels ads" in t or " cm " in f" {t} ":
        out.append("Unlock CM" if ("unlock" in t or "not yet" in t or "requirement" in t) else "CM setup")
    return ", ".join(out)


def api_page_stats(pid, page):
    """API se followers / reach (28 din) / posts — Page Manager ke liye."""
    tok = page.get("token")
    if not tok:
        return {}
    out = {}
    try:
        j = graph_get(pid, tok, fields="followers_count,fan_count")
        out["followers"] = j.get("followers_count") or j.get("fan_count") or 0
    except Exception:
        pass
    try:
        j = graph_get(f"{pid}/insights", tok, metric="page_impressions_unique", period="days_28")
        vals = ((j.get("data") or [{}])[0].get("values") or [])
        if vals:
            out["reach"] = vals[-1].get("value", 0)
    except Exception:
        pass
    try:
        j = graph_get(f"{pid}/published_posts", tok, summary="total_count", limit=1)
        out["posts"] = (j.get("summary") or {}).get("total_count")
    except Exception:
        pass
    if out:
        page_stats_set(page.get("name", pid), page_id=pid, owner=page.get("owner", ""),
                       **{k: v for k, v in out.items() if v is not None})
    return out


def add_page_history(page_name, video, status, issue=""):
    d = _load(PAGE_HISTORY_FILE, {})
    d.setdefault(page_name.strip(), []).append(
        {"time": _now_str(), "video": os.path.basename(str(video)), "status": status, "issue": issue})
    _save(PAGE_HISTORY_FILE, d)


def get_page_history(page_name):
    return _load(PAGE_HISTORY_FILE, {}).get(page_name.strip(), [])


def all_page_history():
    return _load(PAGE_HISTORY_FILE, {})


def add_id_history(acc_id, event, issue=""):
    d = _load(ID_HISTORY_FILE, {})
    d.setdefault(acc_id, []).append({"time": _now_str(), "event": event, "issue": issue})
    _save(ID_HISTORY_FILE, d)


def all_id_history():
    return _load(ID_HISTORY_FILE, {})


def get_page_gap(page_name):
    """Do posts ke beech gap, SECONDS mein. 0 = default (random 40-90s)."""
    try:
        return int(load_page_settings().get(page_name.strip(), {}).get("gap_seconds", 0))
    except Exception:
        return 0


def set_page_gap(page_name, seconds):
    data = load_page_settings()
    data.setdefault(page_name.strip(), {})["gap_seconds"] = int(seconds)
    _save(PAGE_SETTINGS_FILE, data)


# ---------- Duplicate detection (file content ka hash) ----------
def file_hash(path):
    """Tez hash: file size + pehle 1MB. Same file = same hash."""
    import hashlib
    h = hashlib.md5()
    try:
        size = os.path.getsize(path)
        h.update(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(1024 * 1024))
        return h.hexdigest()
    except Exception:
        return None


def load_uploaded():
    return _load(UPLOADED_FILE, {})


def is_uploaded(page_name, fhash):
    return fhash in load_uploaded().get(page_name.strip(), [])


def mark_uploaded(page_name, fhash):
    data = load_uploaded()
    lst = data.setdefault(page_name.strip(), [])
    if fhash not in lst:
        lst.append(fhash)
    _save(UPLOADED_FILE, data)


def clear_uploaded(page_name):
    data = load_uploaded()
    data[page_name.strip()] = []
    _save(UPLOADED_FILE, data)


def ensure_runtime(log=print):
    """Pehli dafa Playwright + Chromium khud install karein. Return True agar tayyar."""
    global sync_playwright, PWTimeout, PLAYWRIGHT_OK
    # .exe mode mein sab bundled hota hai — install ki zaroorat nahi
    if getattr(sys, "frozen", False):
        return PLAYWRIGHT_OK
    _s = load_settings()
    use_ix = bool(_s.get("use_ixbrowser") or _s.get("browser") == "ixbrowser")
    try:
        if not PLAYWRIGHT_OK:
            log(T("First-time setup: installing Playwright... (please wait)", "Pehli dafa setup: Playwright install ho raha hai... (thoda intezaar)"))
            _run([sys.executable, "-m", "pip", "install", "playwright"])
            mod = importlib.import_module("playwright.sync_api")
            sync_playwright = mod.sync_playwright
            PWTimeout = mod.TimeoutError
            PLAYWRIGHT_OK = True
            log(T("Playwright installed.", "Playwright install ho gaya."))
        _ensure_dirs()
        if use_ix:
            # ixBrowser mode: uska API package chahiye; Chromium download ki zaroorat nahi
            try:
                importlib.import_module("ixbrowser_local_api")
            except Exception:
                log(T("First-time setup: installing ixBrowser API package...", "Pehli dafa setup: ixBrowser API package install ho raha hai..."))
                _run([sys.executable, "-m", "pip", "install", "ixbrowser-local-api"])
                log(T("ixBrowser API ready.", "ixBrowser API tayyar."))
        else:
            marker = os.path.join(DATA_DIR, ".chromium_ok")
            if not os.path.exists(marker):
                log(T("First-time setup: downloading Chromium browser... (one time, takes a while)", "Pehli dafa setup: Chromium browser download ho raha hai... (1 dafa, time lagega)"))
                _run([sys.executable, "-m", "playwright", "install", "chromium"])
                with open(marker, "w") as f:
                    f.write("ok")
                log(T("Chromium ready. It will run directly from now on.", "Chromium tayyar. Ab aage se seedha chalega."))
        return True
    except Exception as e:
        log(T(f"Setup problem: {e}", f"Setup mein masla: {e}"))
        log(T("If it gets stuck, run once: pip install playwright  &&  python -m playwright install chromium", "Agar ruk jaye to aik dafa khud chalayein: pip install playwright  &&  python -m playwright install chromium"))
        return False


def profile_of(acc_id):
    try:
        a = get_account(acc_id) or {}
        return (a.get("profile") or "").strip()
    except Exception:
        return ""


def session_dir_for(acc_id):
    _ensure_dirs()
    prof = profile_of(acc_id)
    if not prof:
        # profile nahi -> khud do (table mein nazar aaye). Purana session folder sath le jao (login na tootay)
        try:
            profiles_auto_assign([acc_id])
            prof = profile_of(acc_id)
            if prof:
                old = os.path.join(SESSIONS_DIR, "".join(c if c.isalnum() else "_" for c in acc_id))
                new = os.path.join(SESSIONS_DIR, "".join(c if c.isalnum() else "_" for c in "profile_" + prof))
                if os.path.isdir(old) and not os.path.exists(new):
                    os.rename(old, new)
                try:
                    APP_INSTANCE.after(0, APP_INSTANCE._refresh_all_panels)
                except Exception:
                    pass
        except Exception:
            pass
    key = ("profile_" + prof) if prof else acc_id     # browser profile (Bob Prime ke LD jaisa)
    safe = "".join(c if c.isalnum() else "_" for c in key)
    path = os.path.join(SESSIONS_DIR, safe)
    os.makedirs(path, exist_ok=True)
    return path


def load_hashtags():
    return _load(HASHTAGS_FILE, {})


def save_hashtags(mapping):
    _save(HASHTAGS_FILE, mapping)


def get_hashtags_for_page(page_name):
    return load_hashtags().get(page_name.strip(), "")


def set_hashtags_for_page(page_name, text):
    mapping = load_hashtags()
    mapping[page_name.strip()] = text.strip()
    save_hashtags(mapping)


# ============================================================
# AUTOMATION (Playwright)
# ============================================================
FB_HOME = "https://www.facebook.com/"

# ===================== Language =====================
_LANG = "en"   # "en" (default) ya "ur" (roman urdu)


def T(en, ur):
    """English default; Roman Urdu tab jab language 'ur' ho."""
    return en if _LANG == "en" else ur


def apply_language():
    global _LANG
    try:
        _LANG = load_settings().get("language", "en")
    except Exception:
        _LANG = "en"


def gen_api_key():
    import secrets
    return "fbu_" + secrets.token_hex(16)


def get_api_keys():
    return load_settings().get("api_keys", [])


def add_api_key():
    s = load_settings()
    keys = s.get("api_keys", [])
    k = gen_api_key()
    keys.append(k)
    s["api_keys"] = keys
    save_settings(s)
    return k


def remove_api_key(k):
    s = load_settings()
    s["api_keys"] = [x for x in s.get("api_keys", []) if x != k]
    save_settings(s)


def _configure_tesseract():
    """Tesseract exe ko dhoondo: PATH, bundled folder, ya standard install path."""
    try:
        import pytesseract
    except Exception:
        return False
    import shutil as _sh
    candidates = []
    # bundled (exe ke paas tesseract\ folder)
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates.append(os.path.join(base, "tesseract", "tesseract.exe"))
    candidates.append(os.path.join(base, "tesseract.exe"))
    # standard Windows installs
    candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    candidates.append(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")
    found = _sh.which("tesseract")
    if found:
        return True
    for c in candidates:
        if os.path.exists(c):
            pytesseract.pytesseract.tesseract_cmd = c
            return True
    return False


def _ocr_bytes(png_bytes):
    """Standalone OCR (API server ke liye) — Automation ke bina."""
    try:
        from PIL import Image, ImageOps, ImageFilter
        import io, re
        import pytesseract
    except Exception:
        return ""
    _configure_tesseract()
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        w, h = img.size
        img = img.resize((w * 3, h * 3), Image.LANCZOS)
        img = ImageOps.autocontrast(img)
        img = img.point(lambda p: 0 if p < 110 else 255)
        img = img.filter(ImageFilter.MedianFilter(size=3))
        cfg = "--psm 7 -c tessedit_char_whitelist=ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz0123456789"
        raw = pytesseract.image_to_string(img, config=cfg)
        return re.sub(r"[^A-Za-z0-9]", "", raw).strip()
    except Exception:
        return ""


TWOCAP_JOBS = {}   # 2captcha-compatible job results


def start_api_server(log=print):
    """Local HTTP API: POST /solve  (header X-API-Key, body = image bytes ya base64)
    -> JSON {status, text}. Taake ye OCR doosre software bhi use kar sakein."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json as _json
    import base64

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            return self.headers.get("X-API-Key", "") in get_api_keys()

        def _text(self, s):
            body = s.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---- 2captcha-compatible (in.php / res.php) ----
        def _twocaptcha_in(self, qs, formbody):
            import urllib.parse, base64
            p = urllib.parse.parse_qs(qs)
            fb = urllib.parse.parse_qs(formbody) if formbody else {}
            def g(k, d=""):
                return (fb.get(k, p.get(k, [d])) or [d])[0]
            key = g("key")
            if key not in get_api_keys():
                self._text("ERROR_WRONG_USER_KEY"); return
            b64 = g("body")
            if not b64:
                self._text("ERROR_ZERO_CAPTCHA_FILESIZE"); return
            try:
                raw = base64.b64decode(b64)
            except Exception:
                self._text("ERROR_ZERO_CAPTCHA_FILESIZE"); return
            text = _ocr_bytes(raw)
            import time as _t, random as _r
            cid = str(int(_t.time() * 1000) + _r.randint(0, 999))
            TWOCAP_JOBS[cid] = text or ""
            if g("json") == "1":
                self._send(200, {"status": 1, "request": cid})
            else:
                self._text(f"OK|{cid}")

        def _twocaptcha_res(self, qs):
            import urllib.parse
            p = urllib.parse.parse_qs(qs)
            def g(k, d=""):
                return (p.get(k, [d]) or [d])[0]
            key = g("key")
            if key not in get_api_keys():
                self._text("ERROR_WRONG_USER_KEY"); return
            cid = g("id")
            txt = TWOCAP_JOBS.get(cid)
            if txt is None:
                self._text("ERROR_NO_SUCH_CAPCHA_ID"); return
            if txt == "":
                self._text("ERROR_CAPTCHA_UNSOLVABLE"); return
            if g("json") == "1":
                self._send(200, {"status": 1, "request": txt})
            else:
                self._text(f"OK|{txt}")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            if path.endswith("/in.php"):
                self._twocaptcha_in(qs, "")
                return
            if path.endswith("/res.php"):
                self._twocaptcha_res(qs)
                return
            if self.path.startswith("/ping"):
                self._send(200, {"status": "ok", "service": "fbuploader"})
                return
            if not self._auth():
                self._send(401, {"status": "error", "error": "invalid api key"})
                return
            app = globals().get("APP_INSTANCE")
            if self.path.startswith("/accounts"):
                self._send(200, {"status": "ok", "accounts": app.api_list_accounts() if app else []})
            elif self.path.startswith("/status"):
                self._send(200, {"status": "ok", "data": app.api_status() if app else {}})
            elif self.path.startswith("/log"):
                self._send(200, {"status": "ok", "log": app.api_log() if app else []})
            else:
                self._send(404, {"status": "error", "error": "not found"})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            # 2captcha-compatible POST (Bomb Prime etc.)
            if path.endswith("/in.php"):
                ln = int(self.headers.get("Content-Length", "0"))
                formbody = self.rfile.read(ln).decode("utf-8", "ignore") if ln else ""
                self._twocaptcha_in(qs, formbody)
                return
            if path.endswith("/res.php"):
                self._twocaptcha_res(qs)
                return

            if not self._auth():
                self._send(401, {"status": "error", "error": "invalid api key"})
                return
            try:
                ln = int(self.headers.get("Content-Length", "0"))
                data = self.rfile.read(ln)
                ctype = self.headers.get("Content-Type", "")
                app = globals().get("APP_INSTANCE")

                # OCR solve
                if self.path.startswith("/solve"):
                    if "json" in ctype:
                        obj = _json.loads(data.decode())
                        raw = base64.b64decode(obj.get("image", "").split(",")[-1])
                    else:
                        try:
                            raw = base64.b64decode(data)
                        except Exception:
                            raw = data
                    text = _ocr_bytes(raw)
                    self._send(200, {"status": "ok" if text else "empty", "text": text})
                    return

                body = {}
                if data:
                    try:
                        body = _json.loads(data.decode())
                    except Exception:
                        body = {}

                if self.path.startswith("/post"):
                    ids = body.get("ids", [])
                    kind = body.get("kind", "reel")
                    threads = int(body.get("threads", 5) or 5)
                    r = app.api_start_post(ids, kind, threads) if app else {"ok": False}
                    self._send(200, {"status": "ok", "result": r})
                elif self.path.startswith("/active"):
                    ids = body.get("ids", [])
                    threads = int(body.get("threads", 5) or 5)
                    r = app.api_start_active(ids, threads) if app else {"ok": False}
                    self._send(200, {"status": "ok", "result": r})
                elif self.path.startswith("/stop"):
                    if app:
                        app.api_stop()
                    self._send(200, {"status": "ok"})
                else:
                    self._send(404, {"status": "error", "error": "not found"})
            except Exception as e:
                self._send(400, {"status": "error", "error": str(e)})

    def run():
        try:
            port = int(load_settings().get("api_port", 8700))
        except Exception:
            port = 8700
        try:
            # 0.0.0.0 = same-WiFi phone bhi PC ke IP se connect kar sake (API key se protected)
            srv = ThreadingHTTPServer(("0.0.0.0", port), H)
            ip = "127.0.0.1"
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
            except Exception:
                pass
            log(T(f"API server running. Phone (same WiFi): http://{ip}:{port}",
                  f"API server chal raha hai. Phone (same WiFi): http://{ip}:{port}"))
            srv.serve_forever()
        except Exception as e:
            log(f"API server error: {e}")

    def run80():
        # Port 80 par bhi 2captcha-compatible (Bomb Prime -> hosts file -> 2captcha.com)
        try:
            srv80 = ThreadingHTTPServer(("0.0.0.0", 80), H)
            log(T("2captcha-compatible server on port 80 (for Bomb Prime via hosts file).",
                  "2captcha-compatible server port 80 par (Bomb Prime ke liye, hosts file se)."))
            srv80.serve_forever()
        except Exception as e:
            log(T(f"Port 80 bind failed ({e}). Run the app as administrator for Bomb Prime captcha.",
                  f"Port 80 nahi mila ({e}). Bomb Prime captcha ke liye app ko administrator se chalayein."))

    threading.Thread(target=run, daemon=True).start()
    if load_settings().get("captcha_compat80"):
        threading.Thread(target=run80, daemon=True).start()


# ===================== LDPlayer (Android emulator) control =====================
class LD:
    """LDPlayer instances ko control karta hai (ldconsole + adb ke zariye).
    Har account aik LD instance (index) par kaam kar sakta hai."""

    FB_PKG = "com.facebook.katana"
    FB_LITE_PKG = "com.facebook.lite"

    @staticmethod
    def find_console():
        import shutil as _sh
        for name in ("ldconsole", "dnconsole"):
            w = _sh.which(name)
            if w:
                return w
        cands = [
            r"C:\LDPlayer\LDPlayer9\ldconsole.exe",
            r"C:\LDPlayer\LDPlayer64\ldconsole.exe",
            r"C:\LDPlayer\LDPlayer4.0\ldconsole.exe",
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\LDPlayer\ldconsole.exe",
            r"C:\LDPlayer\ldconsole.exe",
            r"D:\LDPlayer\LDPlayer9\ldconsole.exe",
        ]
        s = load_settings()
        if s.get("ld_console_path"):
            cands.insert(0, s["ld_console_path"])
        for c in cands:
            if c and os.path.exists(c):
                return c
        return None

    @staticmethod
    def _run(args, timeout=60):
        console = LD.find_console()
        if not console:
            return None
        try:
            out = subprocess.run([console] + args, capture_output=True, text=True,
                                 timeout=timeout, **_NOWIN)
            return out.stdout
        except Exception:
            return None

    @staticmethod
    def list_instances():
        """Return list of {index, title, running}."""
        out = LD._run(["list2"])
        res = []
        if not out:
            return res
        for line in out.splitlines():
            parts = line.split(",")
            if len(parts) >= 6 and parts[0].strip().isdigit():
                idx = int(parts[0])
                title = parts[1]
                running = parts[4].strip() not in ("0", "", "-1")  # android started flag
                res.append({"index": idx, "title": title, "running": running})
        return res

    @staticmethod
    def launch(index):
        LD._run(["launch", "--index", str(index)])

    @staticmethod
    def quit(index):
        LD._run(["quit", "--index", str(index)])

    @staticmethod
    def adb(index, command, timeout=60):
        """ldconsole adb passthrough — adb port yaad rakhne ki zaroorat nahi."""
        return LD._run(["adb", "--index", str(index), "--command", command], timeout=timeout)

    @staticmethod
    def run_app(index, package):
        LD._run(["runapp", "--index", str(index), "--packagename", package])

    @staticmethod
    def open_facebook(index, lite=False):
        LD.run_app(index, LD.FB_LITE_PKG if lite else LD.FB_PKG)

    @staticmethod
    def tap(index, x, y):
        LD.adb(index, f"shell input tap {x} {y}")

    @staticmethod
    def screen_size(index):
        out = LD._run(["adb", "--index", str(index), "--command", "shell wm size"], timeout=20) or ""
        import re
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return (720, 1280)

    @staticmethod
    def tap_frac(index, fx, fy):
        """Screen ke fraction par tap (resolution se independent)."""
        w, h = LD.screen_size(index)
        LD.tap(index, int(w * fx), int(h * fy))

    @staticmethod
    def swipe(index, x1, y1, x2, y2, ms=300):
        LD.adb(index, f"shell input swipe {x1} {y1} {x2} {y2} {ms}")

    @staticmethod
    def type_text(index, text):
        safe = text.replace(" ", "%s").replace("'", "")
        LD.adb(index, f"shell input text {safe}")

    @staticmethod
    def keyevent(index, code):
        LD.adb(index, f"shell input keyevent {code}")

    @staticmethod
    def back(index):
        LD.keyevent(index, 4)

    @staticmethod
    def home(index):
        LD.keyevent(index, 3)

    @staticmethod
    def screenshot(index, out_path):
        """Screenshot le kar out_path par save karo (PNG)."""
        console = LD.find_console()
        if not console:
            return False
        try:
            r = subprocess.run([console, "adb", "--index", str(index),
                                "--command", "exec-out screencap -p"],
                               capture_output=True, timeout=60, **_NOWIN)
            data = r.stdout
            # kuch adb builds \r\n ko normalize kar dete hain; raw rakhein
            if data[:8] != b"\x89PNG\r\n\x1a\n" and b"\x89PNG" in data[:40]:
                data = data[data.index(b"\x89PNG"):]
            if data[:4] == b"\x89PNG":
                with open(out_path, "wb") as f:
                    f.write(data)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def push_file(index, local, remote=None):
        """Local video/photo ko emulator ki gallery me daalo (FB ise dekh sake)."""
        ext = os.path.splitext(local)[1] or ".mp4"
        base = os.path.basename(local)
        # DCIM/Camera me daalein (gallery yahan se scan karti hai)
        remote = remote or f"/sdcard/DCIM/Camera/{base}"
        LD.adb(index, "shell mkdir -p /sdcard/DCIM/Camera")
        LD._run(["adb", "--index", str(index), "--command", f"push \"{local}\" {remote}"])
        # media scan — poore DCIM par (taake gallery turant dekhe)
        LD.adb(index, f"shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{remote}")
        LD.adb(index, "shell am broadcast -a android.intent.action.MEDIA_MOUNTED -d file:///sdcard")
        time.sleep(2)
        return remote

    @staticmethod
    def open_url(index, url):
        """FB app me deep-link se page/post seedha kholo (menu dhoondne se behtar)."""
        LD.adb(index, f"shell am start -a android.intent.action.VIEW -d \"{url}\" {LD.FB_PKG}")

    # ---------- UIAutomator: screen ka UI-tree parh kar button text se dhoondo ----------
    @staticmethod
    def ui_xml(index):
        """Current screen ka UI dump (XML text)."""
        LD.adb(index, "shell uiautomator dump /sdcard/ui.xml", timeout=30)
        out = LD._run(["adb", "--index", str(index), "--command", "shell cat /sdcard/ui.xml"], timeout=30)
        return out or ""

    @staticmethod
    def _nodes(xml, text=None, desc=None, rid=None, contains=True):
        """XML me se matching nodes ke center coordinates nikaalo."""
        import re
        res = []
        for m in re.finditer(r"<node[^>]*>", xml):
            tag = m.group(0)
            def attr(name):
                mm = re.search(name + r'="([^"]*)"', tag)
                return mm.group(1) if mm else ""
            t = attr("text"); d = attr("content-desc"); r = attr("resource-id")
            ok = True
            if text is not None:
                ok = ok and (text.lower() in t.lower() if contains else t.lower() == text.lower())
            if desc is not None:
                ok = ok and (desc.lower() in d.lower())
            if rid is not None:
                ok = ok and (rid.lower() in r.lower())
            if ok and (text or desc or rid):
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
                if b:
                    x1, y1, x2, y2 = map(int, b.groups())
                    res.append(((x1 + x2) // 2, (y1 + y2) // 2, t or d or r))
        return res

    @staticmethod
    def find(index, text=None, desc=None, rid=None):
        xml = LD.ui_xml(index)
        n = LD._nodes(xml, text=text, desc=desc, rid=rid)
        return n[0] if n else None

    @staticmethod
    def tap_text(index, text=None, desc=None, rid=None, log=None):
        n = LD.find(index, text=text, desc=desc, rid=rid)
        if n:
            LD.tap(index, n[0], n[1])
            if log:
                log(f"  tapped: {n[2]}")
            return True
        if log:
            log(f"  not found: {text or desc or rid}")
        return False

    @staticmethod
    def wait_text(index, text=None, desc=None, rid=None, timeout=30, log=None):
        import time as _t
        end = _t.time() + timeout
        while _t.time() < end:
            if LD.find(index, text=text, desc=desc, rid=rid):
                return True
            _t.sleep(2)
        if log:
            log(f"  timeout waiting: {text or desc or rid}")
        return False

    # ---------- Facebook app flows (UIAutomator, text-based) ----------
    @staticmethod
    def fb_post(index, local_file, kind, caption, log=print, page=None):
        """FB app me post. kind=reel -> reel to page; video/photo -> normal post.
        page = page ka naam ya ID/URL (reel ke liye zaroori)."""
        import time as _t
        try:
            log(f"LD[{index}] push file to gallery...")
            LD.push_file(index, local_file)

            if kind == "reel":
                return LD.fb_post_reel(index, caption, log=log, page=page)

            # ---- normal video/photo post (profile ya page) ----
            if page:
                LD.open_url(index, LD._page_url(page)); _t.sleep(6)
            else:
                LD.open_facebook(index); _t.sleep(6)
            if not LD.tap_text(index, text="What's on your mind", log=log):
                LD.tap_text(index, desc="What's on your mind", log=log) or LD.tap_text(index, text="Create post", log=log)
            _t.sleep(3)
            LD.tap_text(index, text="Photo/video", log=log) or LD.tap_text(index, text="Photo", log=log) \
                or LD.tap_text(index, desc="Photo", log=log)
            _t.sleep(3)
            LD.tap(index, 180, 520)   # gallery pehla thumbnail
            _t.sleep(2)
            LD.tap_text(index, text="Done", log=log) or LD.tap_text(index, text="Next", log=log)
            _t.sleep(2)
            if caption:
                LD.type_text(index, caption); _t.sleep(1)
            ok = LD.tap_text(index, text="Post", log=log) or LD.tap_text(index, desc="Post", log=log)
            _t.sleep(4)
            log(f"LD[{index}] post sent ({'ok' if ok else 'check'}).")
            return ok
        except Exception as e:
            log(f"LD[{index}] fb_post error: {e}")
            return False

    @staticmethod
    def _page_url(page):
        p = str(page).strip()
        if p.startswith("http") or p.startswith("fb://"):
            return p
        return f"https://www.facebook.com/{p}"

    @staticmethod
    def fb_post_reel(index, caption, log=print, page=None):
        """Reel ko PAGE par post karo (asli screenshots ke mutabiq exact flow)."""
        import time as _t
        try:
            # 1) Page kholo (deep link)
            if page:
                log(f"LD[{index}] open page: {page}")
                LD.open_url(index, LD._page_url(page)); _t.sleep(7)
            else:
                LD.open_facebook(index); _t.sleep(6)
            # 2) Create reel shuru — "Create reel" / "+ Create" -> "Reel" / Reels tab -> "Create reel"
            if not LD.tap_text(index, text="Create reel", log=log):
                if LD.tap_text(index, text="Create", log=log):
                    _t.sleep(2); LD.tap_text(index, text="Reel", log=log)
                elif LD.tap_text(index, text="Reels", log=log):
                    _t.sleep(2); LD.tap_text(index, text="Create reel", log=log)
            _t.sleep(4)
            # 3) Create reel screen: upar-baayein gallery thumbnail (nai push video)
            if not LD.wait_text(index, text="Create reel", timeout=10, log=log):
                LD.wait_text(index, text="Gallery", timeout=5, log=log)
            LD.tap_frac(index, 0.22, 0.46)   # top-left video thumbnail
            _t.sleep(4)
            # 4) Editor: neeche-daayein neela "Next"
            LD.wait_text(index, text="Next", timeout=15, log=log)
            if not LD.tap_text(index, text="Next", log=log):
                LD.tap_frac(index, 0.90, 0.93)   # blue arrow bottom-right
            _t.sleep(4)
            # 5) Reel Settings: caption "Describe your reel" + Share now
            LD.wait_text(index, text="Reel Settings", timeout=20, log=log) or \
                LD.wait_text(index, text="Describe your reel", timeout=5, log=log)
            if caption:
                if LD.tap_text(index, text="Describe your reel", log=log):
                    _t.sleep(1)
                    LD.type_text(index, caption); _t.sleep(1)
            # 6) Share now
            ok = LD.tap_text(index, text="Share now", log=log) or LD.tap_text(index, text="Share", log=log) \
                or LD.tap_text(index, text="Publish", log=log)
            if not ok:
                LD.tap_frac(index, 0.5, 0.96)   # bottom full-width Share now
                ok = True
            _t.sleep(6)
            log(f"LD[{index}] reel share command sent.")
            return ok
        except Exception as e:
            log(f"LD[{index}] reel error: {e}")
            return False

    @staticmethod
    def fb_active(index, settings, log=print):
        """FB app me warm-up: feed scroll + thori der."""
        import time as _t, random as _r
        try:
            LD.open_facebook(index)
            _t.sleep(6)
            feed = settings.get("scroll_feed", {})
            mins = _r.uniform(float(feed.get("min", 2)), float(feed.get("max", 3))) if feed.get("on", True) else 1
            end = _t.time() + mins * 60
            log(f"LD[{index}] feed scroll ~{round(mins,1)} min")
            while _t.time() < end:
                LD.swipe(index, 360, 900, 360, 300, 400)
                _t.sleep(_r.uniform(2, 5))
            log(f"LD[{index}] active done.")
            return True
        except Exception as e:
            log(f"LD[{index}] fb_active error: {e}")
            return False


def totp_now(secret, digits=6, period=30):
    """2FA secret key se abhi ka 6-digit OTP banao (TOTP, Google Authenticator jaisa).
    Koi internet/2fa.live ki zaroorat nahi."""
    import base64, hmac, hashlib, struct, time as _t
    s = (secret or "").strip().replace(" ", "").replace("-", "").upper()
    if not s:
        return ""
    # base32 padding
    s += "=" * ((8 - len(s) % 8) % 8)
    try:
        key = base64.b32decode(s, casefold=True)
    except Exception:
        return ""
    counter = int(_t.time()) // period
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    off = h[-1] & 0x0F
    code = (struct.unpack(">I", h[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def spin(text):
    """Spintax: {Hi|Hey|Holla} me se random choose. Nested bhi chalega."""
    import re
    if not text:
        return ""
    pattern = re.compile(r"\{([^{}]*)\}")
    while True:
        m = pattern.search(text)
        if not m:
            break
        choice = random.choice(m.group(1).split("|"))
        text = text[:m.start()] + choice + text[m.end():]
    return text


_NOT_NAMES = {"facebook", "notifications", "chats", "home", "manage profile", "edit profile", "your profile", "profile",
              "friends", "menu", "watch", "video", "reels", "marketplace", "groups", "gaming", "see all", "create",
              "messenger", "feeds", "pages", "news feed", "log in", "log into facebook", "welcome", "settings",
              "settings & privacy", "account", "accounts center", "professional dashboard", "meta"}


class Automation:
    def __init__(self, log=print, headless=False, browser_cfg=None):
        self.log = log
        self.headless = headless
        self.browser_cfg = browser_cfg or {"browser": "chromium", "browser_path": ""}
        self._pw = None
        self.context = None
        self.page = None
        self.current_acc = None
        self.upload_wait = None   # (min,max) seconds; None = default
        self.last_error = ""
        self.target_page = None   # {name,url} — jis page par post karni hai

    def start(self, account):
        self.current_acc = account
        self._pw = sync_playwright().start()

        # ---- ixBrowser mode: profile ixBrowser kholega (apna IP + fingerprint) ----
        cfg = self.browser_cfg or {}
        if cfg.get("use_ixbrowser") or cfg.get("browser") == "ixbrowser":
            api_ok = _ix_api_up()
            if api_ok and self._start_ixbrowser(account):
                return
            if self._start_ix_free(account):
                return
            if not api_ok:
                self.log(T(f"[{account['name']}] ixBrowser API is off (free plan). Open this ID's profile once in ixBrowser, "
                           f"then press 'Capture open ixBrowser profiles' in Accounts.",
                           f"[{account['name']}] ixBrowser API band hai (free plan). Is ID ka profile ek dafa ixBrowser mein kholein, "
                           f"phir Accounts mein 'Capture open ixBrowser profiles' dabayein."))
            self.log(T(f"[{account['name']}] Could not connect to ixBrowser — using normal browser.", f"[{account['name']}] ixBrowser se connect nahi hua — normal browser par chal rahe hain."))

        b0 = cfg.get("browser", "fbbrowser")
        if b0 in ("fbbrowser", "chromium", "") or b0 is None:
            self.context, self._grid_slot = fb_browser_launch(self._pw, account, cfg, self.headless, self.log)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            return

        user_dir = session_dir_for(account["id"])

        launch_kwargs = dict(
            user_data_dir=user_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Har ID ka apna proxy (alag IP) — account safe rakhne ke liye
        proxy = parse_proxy(account.get("proxy", ""))
        if proxy:
            launch_kwargs["proxy"] = proxy
            self.log(T(f"[{account['name']}] Using proxy: {proxy['server']}", f"[{account['name']}] Proxy use ho raha hai: {proxy['server']}"))
        # Browser choice: bundled chromium (default), Chrome, Edge, ya custom path
        b = (self.browser_cfg or {}).get("browser", "chromium")
        path = (self.browser_cfg or {}).get("browser_path", "")
        if b == "chrome":
            launch_kwargs["channel"] = "chrome"
        elif b == "msedge":
            launch_kwargs["channel"] = "msedge"
        elif b == "custom" and path:
            launch_kwargs["executable_path"] = path
        try:
            self.context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as e:
            self.log(T(f"[{account['name']}] '{b}' browser did not open ({e}). Falling back to default Chromium.", f"[{account['name']}] '{b}' browser nahi khula ({e}). Default Chromium par chal rahe hain."))
            launch_kwargs.pop("channel", None)
            launch_kwargs.pop("executable_path", None)
            self.context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.log(T(f"[{account['name']}] Browser opened.", f"[{account['name']}] Browser khul gaya."))

    def _resolve_ix_profile(self, client, value):
        """value ID (number) ho to wahi; warna profile NAME se ID dhoondo."""
        value = str(value).strip()
        if value.isdigit():
            return value
        # naam se match — saari profiles list karke dekho
        try:
            page = 1
            while True:
                lst = client.get_profile_list(page=page, limit=100)
                if not lst:
                    break
                for p in lst:
                    nm = str(p.get("name", p.get("title", ""))).strip()
                    if nm.lower() == value.lower():
                        return str(p.get("profile_id", p.get("id", "")))
                if len(lst) < 100:
                    break
                page += 1
        except Exception as e:
            self.log(T(f"Could not get profile list: {e}", f"Profile list nahi mili: {e}"))
        return None

    def _ix_auto_create(self, account):
        """ixBrowser me is ID ke naam se naya profile banao aur uska ID save karo."""
        try:
            from ixbrowser_local_api.entities import Profile
            name = account.get("name") or account.get("email")
            # Pehle se isi naam ka profile ho to wahi use karo
            existing = self._resolve_ix_profile(self._ix_client, name)
            if existing:
                update_account(account["id"], ix_profile=str(existing))
                return str(existing)
            p = Profile()
            p.name = name
            p.site_url = "https://www.facebook.com/"
            p.username = account.get("email", "")
            p.password = account.get("password", "")
            if account.get("twofa"):
                p.tfa_secret = account.get("twofa")
            res = self._ix_client.create_profile(p)
            pid = None
            if isinstance(res, dict):
                pid = res.get("profile_id") or res.get("id")
            elif res:
                pid = res
            if pid:
                update_account(account["id"], ix_profile=str(pid))
                account["ix_profile"] = str(pid)
                self.log(T(f"[{account['name']}] New ixBrowser profile created: {pid}",
                           f"[{account['name']}] Naya ixBrowser profile bana: {pid}"))
                return str(pid)
            msg = getattr(self._ix_client, "message", "")
            self.log(f"ixBrowser create_profile: {msg}")
        except Exception as e:
            self.log(f"ixBrowser create error: {e}")
        return None

    def _start_ixbrowser(self, account):
        """ixBrowser local API se profile kholo aur CDP par connect karo."""
        pid = account.get("ix_profile", "").strip()
        try:
            from ixbrowser_local_api import IXBrowserClient
        except Exception:
            self.log(T("ixbrowser-local-api package not found. (should have auto-installed)", "ixbrowser-local-api package nahi mila. (auto-install hona chahiye tha)"))
            return False
        try:
            self._ix_client = IXBrowserClient()
            if not pid:
                # Profile set nahi — is ID ke liye ixBrowser me khud naya profile banao
                pid = self._ix_auto_create(account)
                if not pid:
                    self.log(T(f"[{account['name']}] Could not create an ixBrowser profile automatically.",
                               f"[{account['name']}] ixBrowser profile khud nahi ban saka."))
                    return False
            resolved = self._resolve_ix_profile(self._ix_client, pid)
            if not resolved:
                self.log(T(f"[{account['name']}] No ixBrowser profile found with name/ID '{pid}'.", f"[{account['name']}] ixBrowser mein '{pid}' naam/ID ki profile nahi mili."))
                return False
            pid = resolved
            self.log(T(f"[{account['name']}] Opening ixBrowser profile {pid}...", f"[{account['name']}] ixBrowser profile {pid} khol rahe hain..."))
            r = self._ix_client.open_profile(pid, load_profile_info_page=False)
            if not r:
                self.log(T(f"[{account['name']}] ixBrowser open failed: {getattr(self._ix_client,'message','')}", f"[{account['name']}] ixBrowser open fail: {getattr(self._ix_client,'message','')}"))
                return False
            ws = r.get("ws") or r.get("webSocketDebuggerUrl")
            if not ws:
                port = r.get("debugging_port") or r.get("debuggingPort")
                if port:
                    ws = f"http://127.0.0.1:{port}"
            if not ws:
                self.log(T(f"[{account['name']}] ixBrowser did not return a connection endpoint.", f"[{account['name']}] ixBrowser ne connection endpoint nahi diya."))
                return False
            browser = self._pw.chromium.connect_over_cdp(ws)
            self._ix_browser = browser
            self._ix_profile_id = pid
            self.context = browser.contexts[0] if browser.contexts else browser.new_context()
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            ix_mark_open(account["id"])   # roz ki limit/rotation ke liye ginti
            self.log(T(f"[{account['name']}] Connected to ixBrowser profile.", f"[{account['name']}] ixBrowser profile se connect ho gaya."))
            return True
        except Exception as e:
            self.log(T(f"[{account['name']}] ixBrowser error: {e}", f"[{account['name']}] ixBrowser error: {e}"))
            return False

    def _start_ix_free(self, account):
        """Free plan: saved launch command + debugging port se profile khud kholo."""
        key = (account.get("ix_key") or "").strip()
        running = ix_running_profiles()
        if not key:
            fu = account.get("email", "").strip().lower()
            for pr in running:
                if fu and pr["fb_user"].lower() == fu:
                    key = pr["key"]; ix_save_cmd(key, pr["argv"])
                    update_account(account["id"], ix_key=key); break
        if not key:
            return False
        argv = None
        for pr in running:
            if pr["key"] == key:
                argv = pr["argv"]; ix_save_cmd(key, argv)
                self.log(T(f"[{account['name']}] Profile is open in ixBrowser — reopening it with control...",
                           f"[{account['name']}] Profile ixBrowser mein khula hai — control ke sath dobara khol rahe hain..."))
                _kill_pid_tree(pr["pid"]); time.sleep(3)
                break
        if argv is None:
            argv = ix_saved_cmds().get(key)
        if not argv:
            return False
        port = _free_port()
        new = [a for a in argv if not a.startswith("--remote-debugging-port")
               and not a.startswith("--remote-allow-origins")]
        new = [(f"--protected-starttime={int(time.time())}" if a.startswith("--protected-starttime=") else a) for a in new]
        new += [f"--remote-debugging-port={port}", "--remote-allow-origins=*"]
        try:
            si = None
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 1          # SW_SHOWNORMAL — browser nazar aaye
            self._ix_proc = subprocess.Popen(new, startupinfo=si, creationflags=0)
        except Exception as e:
            self.log(f"[{account['name']}] ixBrowser (free) launch error: {e}")
            return False
        import urllib.request
        ok = False
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
                ok = True; break
            except Exception:
                time.sleep(1)
        if not ok:
            self.log(T(f"[{account['name']}] ixBrowser profile opened but control port did not answer.",
                       f"[{account['name']}] ixBrowser profile khula lekin control port ne jawab nahi diya."))
            return False
        browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        self._ix_browser = browser
        self.context = browser.contexts[0] if browser.contexts else browser.new_context()
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        ix_mark_open(account["id"])
        self.log(T(f"[{account['name']}] Connected to ixBrowser profile (free mode).",
                   f"[{account['name']}] ixBrowser profile se connect (free mode)."))
        return True

    def stop(self):
        try:
            # ixBrowser free mode: apna khola hua process band karo
            if getattr(self, "_ix_proc", None):
                try:
                    if self._ix_browser:
                        self._ix_browser.close()
                except Exception:
                    pass
                self._ix_browser = None
                _kill_pid_tree(self._ix_proc.pid)
                self._ix_proc = None
            # ixBrowser mode: profile band karo
            if getattr(self, "_ix_profile_id", None) and getattr(self, "_ix_client", None):
                try:
                    self._ix_client.close_profile(self._ix_profile_id)
                except Exception:
                    pass
                self._ix_profile_id = None
            if getattr(self, "_ix_browser", None):
                try:
                    self._ix_browser.close()
                except Exception:
                    pass
                self._ix_browser = None
            elif self.context:
                self.context.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        if getattr(self, "_grid_slot", None) is not None:
            _grid_free(self._grid_slot); self._grid_slot = None
        self.context = self.page = self._pw = None

    # ---------- Login ----------
    def _fb_uid(self):
        """Logged-in Facebook ka user id (c_user cookie). Na ho to ''."""
        try:
            for c in self.context.cookies("https://www.facebook.com"):
                if c.get("name") == "c_user" and c.get("value"):
                    return c["value"]
        except Exception:
            pass
        return ""

    def account_state(self):
        """Login na ho to wajah: Disabled / Suspended / Verification / Not login.
        'Die' sirf tab jab account sach mein mojood na ho."""
        try:
            url = (self.page.url or "").lower()
            txt = self._page_text().lower() if hasattr(self, "_page_text") else ""
        except Exception:
            return "Not login"
        if "download your information" in txt or "we disabled your account" in txt \
                or "account has been disabled" in txt or "your account has been disabled" in txt:
            return "Disabled"
        if "suspended" in txt or "/1501092823525282" in url:
            return "Suspended"
        if "checkpoint" in url or "confirm your identity" in txt or "we suspect" in txt \
                or "verify your identity" in txt or "upload a photo of yourself" in txt or "verification" in txt:
            return "Verification"
        if "account doesn't exist" in txt or "no account found" in txt or "isn't connected to an account" in txt \
                or "account no longer exists" in txt:
            return "Die"
        return "Not login"

    def is_logged_in(self):
        self.page.goto(FB_HOME, wait_until="domcontentloaded")
        for _ in range(8):
            time.sleep(1.5)
            url = self.page.url
            if "login" in url or "/checkpoint" in url:
                return False
            try:
                # Login form nazar aa raha hai = logged in NAHI (zaban koi bhi ho)
                if self.page.query_selector("input[name='pass']") or \
                   self.page.query_selector("form[action*='login']"):
                    return False
            except Exception:
                pass
            uid = self._fb_uid()
            if uid:
                try:
                    a = get_account(self.current_acc["id"]) or {}
                    if a.get("uid") != uid:
                        update_account(self.current_acc["id"], uid=uid)
                except Exception:
                    pass
                return True
        return bool(self._fb_uid())

    def _find_login_inputs(self):
        e = self.page.query_selector("input[name='email']") or \
            self.page.query_selector("input[type='email']") or \
            self.page.query_selector("input[autocomplete='username']")
        p = self.page.query_selector("input[name='pass']") or \
            self.page.query_selector("input[type='password']")
        return e, p

    def _fill_login_form(self, acc):
        """Login form bharo. 'Saved account' wali screen ho to doosre tareeqe aazmao."""
        for attempt in range(4):
            e, p = self._find_login_inputs()
            if not e and not p:
                if attempt == 0:
                    # saved-profile screen: "Use another profile / Log into another account"
                    self._click_button(["Use another profile", "Log into another account",
                                        "Log in to another account", "Not you?", "Switch account"])
                elif attempt == 1:
                    self.page.goto("https://www.facebook.com/login/", wait_until="domcontentloaded")
                else:
                    self.page.goto("https://www.facebook.com/login.php?login_attempt=1", wait_until="domcontentloaded")
                time.sleep(3)
                continue
            try:
                if e:
                    e.click(); e.fill(""); e.type(acc["email"], delay=40)
                    time.sleep(0.5)
                if not p:
                    # do-step login: pehle email, phir Next/Continue
                    self._click_button(["Continue", "Next"]); time.sleep(3)
                    _, p = self._find_login_inputs()
                if p:
                    p.click(); p.fill(""); p.type(acc["password"], delay=40)
                    time.sleep(0.5)
                    if not self._click_css("button[name='login']"):
                        p.press("Enter")
                    return True
            except Exception as ex:
                self.log(f"[{acc['name']}] login form: {ex}")
            time.sleep(2)
        return False

    def api_connect_flow(self, app_id, timeout=150):
        """Is ID ke browser mein: tester invite accept -> OAuth Continue/Allow -> token (URL se)."""
        import urllib.parse as _up
        try:
            self.page.goto("https://developers.facebook.com/requests/", wait_until="domcontentloaded")
            time.sleep(random.uniform(4, 6))
            for _ in range(3):
                if not self._click_exact(["Confirm", "Accept"]):
                    break
                time.sleep(2)
        except Exception:
            pass
        url = (f"https://www.facebook.com/{_gv()}/dialog/oauth?client_id={app_id}"
               f"&redirect_uri={_up.quote(API_REDIRECT, safe='')}&response_type=token&scope={API_SCOPE}")
        self.page.goto(url, wait_until="domcontentloaded")
        t0 = time.time()
        while time.time() - t0 < timeout:
            u = self.page.url or ""
            if "access_token=" in u:
                frag = u.split("#", 1)[-1] if "#" in u else u.split("?", 1)[-1]
                tok = _up.parse_qs(frag).get("access_token", [""])[0]
                if tok:
                    return tok
            txt = self._page_text().lower()
            if "url blocked" in txt or "can't load url" in txt or "invalid app id" in txt or "app not active" in txt:
                self.log(T("App setting needed: in your app → Use cases → Customize → Settings, add "
                           f"{API_REDIRECT} to 'Valid OAuth Redirect URIs' and turn ON 'Client OAuth login' + 'Web OAuth login'.",
                           "App setting chahiye: app → Use cases → Customize → Settings mein "
                           f"{API_REDIRECT} ko 'Valid OAuth Redirect URIs' mein daalein aur 'Client OAuth login' + 'Web OAuth login' ON karein."))
                return ""
            try:   # saare (current + future) pages chuno
                for el in self.page.query_selector_all("[role='radio']:has-text('current and future'), "
                                                       "label:has-text('current and future')"):
                    if el.is_visible() and (el.get_attribute("aria-checked") or "") != "true":
                        el.click(timeout=3000); time.sleep(1); break
            except Exception:
                pass
            if not self._click_button(["Continue", "Save", "Allow", "Got it", "OK", "Next", "Done"]):
                try:
                    for el in self.page.query_selector_all("[role='button']:has-text('Continue as'), button:has-text('Continue as')"):
                        if el.is_visible():
                            el.click(timeout=3000); break
                except Exception:
                    pass
            time.sleep(2.5)
        return ""

    def _clear_browser_data(self):
        """Is browser ka cache + cookies + storage saaf karo (fresh session jaisa)."""
        try:
            cdp = self.page.context.new_cdp_session(self.page)
            cdp.send("Network.clearBrowserCache")
            cdp.send("Network.clearBrowserCookies")
            try:
                cdp.send("Storage.clearDataForOrigin", {"origin": "https://www.facebook.com", "storageTypes": "all"})
            except Exception:
                pass
            cdp.detach()
        except Exception:
            pass
        try:
            self.page.context.clear_cookies()
        except Exception:
            pass
        try:
            self.page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
        except Exception:
            pass

    def _choose_auth_app_in_dialog(self):
        """FB ka 'Choose a way to confirm it's you' dialog: usi dialog ke andar 'Authentication app' chuno + Continue."""
        try:
            dlg = None
            for d in self.page.query_selector_all("[role='dialog']"):
                try:
                    if d.is_visible() and "authentication app" in (d.inner_text() or "").lower():
                        dlg = d
                except Exception:
                    continue
            if not dlg:
                return False
            opt = None
            for sel in ("[role='radio']:has-text('Authentication app')", "label:has-text('Authentication app')",
                        "[role='button']:has-text('Authentication app')", "text=/^\\s*Authentication app\\s*$/i",
                        "text=/authentication app/i"):
                try:
                    opt = dlg.query_selector(sel)
                    if opt and opt.is_visible():
                        break
                    opt = None
                except Exception:
                    opt = None
            if not opt:
                return False
            opt.click()
            time.sleep(0.8)
            # radio sach mein laga? warna radio input khud dabao
            try:
                r = dlg.query_selector("input[type='radio'][value*='totp' i], input[type='radio'][value*='app' i]")
                if r and not r.is_checked():
                    r.check(force=True)
            except Exception:
                pass
            cont = None
            for sel in ("[role='button']:has-text('Continue')", "button:has-text('Continue')",
                        "[role='button']:has-text('Next')", "button:has-text('Next')"):
                try:
                    cont = dlg.query_selector(sel)
                    if cont and cont.is_visible():
                        break
                    cont = None
                except Exception:
                    cont = None
            if cont:
                cont.click()
                return True
        except Exception:
            pass
        return False

    def _click_css(self, sel):
        try:
            el = self.page.query_selector(sel)
            if el:
                el.click(); return True
        except Exception:
            pass
        return False

    def login(self, wait_for_2fa_callback=None):
        acc = self.current_acc
        if self.is_logged_in():
            self.log(T(f"[{acc['name']}] Already logged in — skipping login.", f"[{acc['name']}] Pehle se logged in — login skip."))
            return True

        self.log(T(f"[{acc['name']}] Logging in...", f"[{acc['name']}] Login kar rahe hain..."))
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        self.page.goto(FB_HOME, wait_until="domcontentloaded")
        time.sleep(3)
        if not self._fill_login_form(acc):
            self.log(T(f"[{acc['name']}] Login form not found. Please log in manually in the browser.", f"[{acc['name']}] Login form nahi mila. Browser mein khud login karein."))
            if wait_for_2fa_callback:
                wait_for_2fa_callback(acc)
            return self.is_logged_in()

        ok = self._login_flow(acc, wait_for_2fa_callback)
        if ok:
            self.log(T(f"[{acc['name']}] Login successful. Session saved.", f"[{acc['name']}] Login kamyab. Session save ho gaya."))
            self.is_logged_in()          # UID save
            return True
        self.last_error = T("Login not confirmed (wrong password / checkpoint / 2FA).", "Login confirm nahi hua (galat password / checkpoint / 2FA).")
        self.log(T(f"[{acc['name']}] {self.last_error}", f"[{acc['name']}] {self.last_error}"))
        return False

    def _ptext(self):
        try:
            return (self.page.inner_text("body") or "").lower()
        except Exception:
            return ""

    def _login_flow(self, acc, wait_cb=None, max_secs=420):
        """Login ke baad har 2 sec page dekho aur jo saamne ho wahi karo:
        captcha -> solve, 'check notifications / another device' -> Try another way -> Authentication app,
        code box -> TOTP, 'Trust this device / Save browser' -> Continue. Logged in -> done."""
        t0 = time.time()
        done_2fa_codes = set()
        captcha_tries = 0
        other_way_tries = 0
        told_user = False
        last_state = ""
        relogins = 0
        last_fill = time.time()
        uid_since = 0
        busy_since = 0
        busy_told = False
        empty_cap_since = 0
        cap_reloads = 0
        while time.time() - t0 < max_secs:
            if getattr(self, "stop_event", None) and self.stop_event.is_set():
                return False
            txt = self._ptext()
            url = (self.page.url or "").lower()

            # 0) "Running security checks / Please wait / Verifying" — Facebook khud check kar raha hai:
            #    kuch mat chhero (na relogin, na page badlo), bas intezaar (2 min tak, phir 1 dafa reload)
            if any(k in txt for k in ("running security check", "security checks", "please wait",
                                      "verifying", "just a moment", "checking your browser")) \
                    and len(txt) < 1500 and "code" not in txt and "password" not in txt:
                busy_since = busy_since or time.time()
                if not busy_told:
                    busy_told = True
                    self.log(T(f"[{acc['name']}] Facebook is running security checks — waiting patiently...",
                               f"[{acc['name']}] Facebook security check chala raha hai — sabr se intezaar..."))
                if time.time() - busy_since < 120:
                    last_fill = time.time()          # is dauran relogin bilkul nahi
                    time.sleep(3); continue
                try:
                    self.log(T(f"[{acc['name']}] Security check stuck for 2 min — reloading the page once.",
                               f"[{acc['name']}] Security check 2 min se atka — page 1 dafa reload."))
                    self.page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
                busy_since = time.time() - 60       # agli dafa 1 min baad
                time.sleep(5); continue
            busy_since = 0

            if self._fb_uid():
                if "checkpoint" not in url and "two_step" not in url and "two_factor" not in url:
                    return True
                # login ho chuka (cookie mil gayi) magar koi beech ka page atka.
                # 2FA / captcha / code wala page ho to BILKUL na chhoro — pehle use poora karo.
                actionable = ("two_step" in url or "two_factor" in url or "code" in txt
                              or "authentication" in txt or "recaptcha" in txt or "robot" in txt
                              or self._has_captcha())
                uid_since = uid_since or time.time()
                if not actionable and time.time() - uid_since > 20:
                    self.log(T(f"[{acc['name']}] Logged in — skipping the leftover screen.",
                               f"[{acc['name']}] Login ho gaya — bacha hua page chhor kar aage."))
                    try:
                        self.page.goto(FB_HOME, wait_until="domcontentloaded"); time.sleep(3)
                    except Exception:
                        pass
                    u2 = (self.page.url or "").lower()
                    if self._fb_uid() and "checkpoint" not in u2 and "two_step" not in u2 and "two_factor" not in u2:
                        return True
                    uid_since = time.time()

            # 0b) Meta ka reCAPTCHA page khula magar "I'm not a robot" ka dabba load hi nahi hua
            #     (khaali page) -> 12 sec intezaar, phir page reload (5 dafa tak), dabba aate hi hal karo
            cap_page = ("recaptcha enterprise" in txt or "combat harmful conduct" in txt
                        or "i'm not a robot" in txt)
            if cap_page:
                a_el, _b = self._find_iframe_el("anchor")
                c_el, _b2 = self._find_iframe_el("challenge")
                if not a_el and not c_el:
                    empty_cap_since = empty_cap_since or time.time()
                    last_fill = time.time()
                    if time.time() - empty_cap_since > 12:
                        if cap_reloads < 5:
                            cap_reloads += 1
                            self.log(T(f"[{acc['name']}] Captcha box did not load — reloading page ({cap_reloads}/5)...",
                                       f"[{acc['name']}] Captcha ka dabba load nahi hua — page reload ({cap_reloads}/5)..."))
                            try:
                                self.page.reload(wait_until="domcontentloaded")
                            except Exception:
                                pass
                            empty_cap_since = time.time()
                            captcha_tries = min(captcha_tries, 2)
                            time.sleep(6); continue
                        elif cap_reloads == 5:
                            cap_reloads += 1
                            try:
                                self.page.bring_to_front()
                            except Exception:
                                pass
                            self.log(T(f"[{acc['name']}] Captcha box still not loading after 5 reloads — please tick it in the browser if it shows (I keep watching).",
                                       f"[{acc['name']}] 5 reload ke baad bhi captcha ka dabba nahi aaya — aaye to browser mein tick kar dein (main dekh raha hoon)."))
                    time.sleep(2); continue
                empty_cap_since = 0
                if captcha_tries >= 6 and cap_reloads < 5:
                    # 6 koshishen ho chukin — fresh captcha ke liye reload, phir dobara
                    cap_reloads += 1
                    self.log(T(f"[{acc['name']}] Fresh captcha — reloading ({cap_reloads}/5)...",
                               f"[{acc['name']}] Naya captcha — reload ({cap_reloads}/5)..."))
                    try:
                        self.page.reload(wait_until="domcontentloaded")
                    except Exception:
                        pass
                    captcha_tries = 2
                    time.sleep(6); continue
            else:
                empty_cap_since = 0

            # 1) Text captcha (free OCR) / reCAPTCHA (2captcha)
            if captcha_tries < 3 and self._has_text_captcha():
                captcha_tries += 1
                self.log(T(f"[{acc['name']}] Text captcha — solving (free OCR)...", f"[{acc['name']}] Text captcha — OCR se hal..."))
                self._solve_text_captcha(); time.sleep(3); continue
            if captcha_tries < 6 and self._has_captcha():
                captcha_tries += 1
                self.log(T(f"[{acc['name']}] Captcha — solving with 2Captcha...", f"[{acc['name']}] Captcha — 2Captcha se hal..."))
                if self._solve_captcha():
                    time.sleep(3); continue

            # 2) Code box -> TOTP
            code_box = None
            for sel in ("input[name='approvals_code']", "input[autocomplete='one-time-code']",
                        "input[type='text'][maxlength='6']", "input[inputmode='numeric']", "input[type='tel']",
                        "input[aria-label*='code' i]", "input[placeholder*='code' i]"):
                try:
                    el = self.page.query_selector(sel)
                    if el and el.is_visible():
                        code_box = el; break
                except Exception:
                    continue
            auth_page = (("authentication app" in txt or "6-digit" in txt or "login code" in txt)
                         and "code" in txt and "choose a way to confirm" not in txt)
            if not code_box and auth_page:
                # FB ka naya 2FA page: saada text input (koi khaas attribute nahi)
                for sel in ("input[type='text']", "input[type='number']", "input:not([type])"):
                    try:
                        for el in self.page.query_selector_all(sel):
                            if el.is_visible() and el.is_enabled():
                                code_box = el; break
                    except Exception:
                        pass
                    if code_box:
                        break
            if code_box and acc.get("twofa"):
                code = totp_now(acc.get("twofa", ""))
                if code and code not in done_2fa_codes:
                    done_2fa_codes.add(code)
                    self.log(T(f"[{acc['name']}] 2FA code: {code}", f"[{acc['name']}] 2FA code: {code}"))
                    try:
                        code_box.click(); code_box.fill(""); code_box.type(code, delay=60)
                        time.sleep(0.4)
                        if not self._click_button(["Continue", "Submit", "Submit code", "Next", "Confirm", "Log in", "Log In"]):
                            self.page.keyboard.press("Enter")
                    except Exception as e:
                        self.log(f"2FA: {e}")
                    time.sleep(3); continue

            # 3) "Check your notifications" / "another device" -> Try another way -> (dialog) Authentication app -> Continue
            if acc.get("twofa") and not auth_page and other_way_tries < 5:
                if self._choose_auth_app_in_dialog():
                    other_way_tries += 1
                    self.log(T(f"[{acc['name']}] 2FA: chose 'Authentication app'", f"[{acc['name']}] 2FA: 'Authentication app' chuna"))
                    time.sleep(3); continue
                if any(k in txt for k in (
                        "check your notifications", "another device", "approve from", "approve this login",
                        "waiting for approval", "go to your facebook app", "check your other device",
                        "we sent a notification", "approve on another", "try another way")):
                    if self._click_button(["Try another way", "Try Another Way", "Use another way", "Choose another option"]):
                        other_way_tries += 1
                        self.log(T(f"[{acc['name']}] 'Try another way' → Authentication app", f"[{acc['name']}] 'Try another way' → Authentication app"))
                        for _ in range(6):
                            time.sleep(1)
                            if self._choose_auth_app_in_dialog():
                                break
                        time.sleep(3); continue

            # 4) Trust device / save browser / continue screens
            if any(k in txt for k in ("trust this device", "save browser", "remember browser", "save your login info",
                                      "review recent login", "this was me", "continue as")):
                if self._click_button(["Trust this device", "Save Browser", "Save browser", "This was me",
                                       "Continue", "OK", "Not now", "Don't save"]):
                    time.sleep(3); continue

            # 4b) Captcha ke baad FB aksar wapas login form dikhata hai ("login information incorrect")
            #     -> email/password dobara bharo (2 dafa tak)
            if relogins < 3 and time.time() - last_fill > 10 and not any(
                    k in url for k in ("two_step", "two_factor", "checkpoint")):
                try:
                    e_in, p_in = self._find_login_inputs()
                    # captcha screen par neeche password box dikhta hai — tab dobara login NAHI (pehle captcha)
                    if p_in and p_in.is_visible() and not self._has_captcha():
                        relogins += 1
                        last_fill = time.time()
                        self.log(T(f"[{acc['name']}] Login form shown again — clearing browser cache/cookies and retrying fresh ({relogins}/3)...",
                                   f"[{acc['name']}] Login form dobara aaya — browser cache/cookies saaf kar ke fresh dobara ({relogins}/3)..."))
                        if relogins > 1:
                            time.sleep(random.uniform(15, 30))      # thora ruk kar (Facebook ka shak kam)
                        self._clear_browser_data()
                        try:
                            self.page.goto(FB_HOME, wait_until="domcontentloaded")
                        except Exception:
                            pass
                        time.sleep(random.uniform(4, 7))
                        e_in, p_in = self._find_login_inputs()
                        if not p_in:
                            continue
                        if e_in and e_in.is_visible():
                            self._fill_login_form(acc)
                        else:
                            p_in.click(); p_in.fill(""); p_in.type(acc["password"], delay=40)
                            if not self._click_css("button[name='login']"):
                                p_in.press("Enter")
                        captcha_tries = min(captcha_tries, 1)
                        time.sleep(4); continue
                except Exception:
                    pass

            # 5) kuch samajh nahi aaya -> user ko batao (ek dafa), lekin chalte raho
            state = url.split("?")[0][-60:]
            if not told_user and time.time() - t0 > 25:
                told_user = True
                try:
                    self.page.bring_to_front()
                except Exception:
                    pass
                self.log(T(f"[{acc['name']}] Waiting on Facebook check — if you see something I can't do, finish it in the browser "
                           f"(I keep watching and continue by myself).",
                           f"[{acc['name']}] Facebook check par ruke hain — jo main na kar sakun browser mein kar dein "
                           f"(main dekh raha hoon, khud aage barhunga)."))
            last_state = state
            time.sleep(2)
        return bool(self._fb_uid())

    def _looks_like_2fa(self):
        try:
            txt = self.page.content().lower()
            return any(k in txt for k in ["two-factor", "login code", "authentication code", "code we sent"])
        except Exception:
            return False

    def _auto_2fa(self, acc):
        """Secret key se OTP bana kar 2FA box me daalo aur Continue."""
        code = totp_now(acc.get("twofa", ""))
        if not code:
            return False
        self.log(T(f"[{acc['name']}] Auto 2FA code: {code}", f"[{acc['name']}] Auto 2FA code: {code}"))
        selectors = [
            "input[name='approvals_code']",
            "input[autocomplete='one-time-code']",
            "input[inputmode='numeric']",
            "input[type='text'][maxlength='6']",
            "input[type='tel']",
            "input[aria-label*='code' i]",
        ]
        box = None
        for sel in selectors:
            box = self.page.query_selector(sel)
            if box:
                break
        if not box:
            return False
        try:
            box.click()
            box.fill("")
            box.type(code, delay=80)
            time.sleep(0.5)
            if not self._click_button(["Continue", "Submit", "Next", "Confirm", "Log In", "Log in"]):
                self.page.keyboard.press("Enter")
            time.sleep(3)
            # kabhi "trust this device" / "continue" aata hai
            self._click_button(["Continue", "Trust this device", "Don't save", "OK"])
            return True
        except Exception as e:
            self.log(f"2FA auto error: {e}")
            return False

    # ---------- Text captcha ("Enter the characters you see") — FREE local OCR ----------
    def _has_text_captcha(self):
        try:
            txt = self.page.content().lower()
            if "enter the characters you see" in txt or "characters you see" in txt:
                return True
            # input placeholder 'Enter characters'
            if self.page.query_selector("input[placeholder*='characters' i]"):
                return True
        except Exception:
            pass
        return False

    def _find_captcha_image_bytes(self):
        """Captcha ki tasveer (img) ko bytes me nikalo."""
        try:
            # sab se bara img jo captcha jaisa ho
            imgs = self.page.query_selector_all("img")
            best = None; best_area = 0
            for im in imgs:
                try:
                    box = im.bounding_box()
                    if not box:
                        continue
                    area = box["width"] * box["height"]
                    # captcha images chaudi-si hoti hain
                    if box["width"] >= 120 and 0.15 < (box["height"] / max(1, box["width"])) < 0.9 and area > best_area:
                        best = im; best_area = area
                except Exception:
                    continue
            if best is None:
                return None
            return best.screenshot()
        except Exception:
            return None

    def solve_text_image(self, png_bytes):
        """OCR engine: distorted text/number captcha ko parho. Return string ya ''."""
        try:
            from PIL import Image, ImageOps, ImageFilter
            import io
            import pytesseract
        except Exception:
            self.log(T("Local OCR needs pytesseract+Tesseract (installer adds it).",
                       "Local OCR ke liye pytesseract+Tesseract chahiye (installer add karta hai)."))
            return ""
        if not _configure_tesseract():
            self.log(T("Tesseract not found. Install it free from UB Mannheim, or use 2captcha.",
                       "Tesseract nahi mila. UB Mannheim se free install karein, ya 2captcha use karein."))
            return ""
        try:
            import io
            img = Image.open(io.BytesIO(png_bytes)).convert("L")
            # upscale
            w, h = img.size
            img = img.resize((w * 3, h * 3), Image.LANCZOS)
            # contrast + threshold
            img = ImageOps.autocontrast(img)
            img = img.point(lambda p: 0 if p < 110 else 255)
            # strike-through lines halka karne ke liye median filter
            img = img.filter(ImageFilter.MedianFilter(size=3))
            cfg = "--psm 7 -c tessedit_char_whitelist=ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz0123456789"
            raw = pytesseract.image_to_string(img, config=cfg)
            import re
            out = re.sub(r"[^A-Za-z0-9]", "", raw).strip()
            return out
        except Exception as e:
            self.log(f"OCR error: {e}")
            return ""

    def _solve_text_captcha(self, max_tries=4):
        if not load_settings().get("local_ocr", True):
            return False
        for attempt in range(1, max_tries + 1):
            png = self._find_captcha_image_bytes()
            if not png:
                self.log(T("Captcha image not found.", "Captcha image nahi mili."))
                return False
            text = self.solve_text_image(png)
            self.log(T(f"OCR read: '{text}' (try {attempt})", f"OCR ne parha: '{text}' (koshish {attempt})"))
            if 4 <= len(text) <= 10:
                box = (self.page.query_selector("input[placeholder*='characters' i]")
                       or self.page.query_selector("input[type='text']")
                       or self.page.query_selector("input[name='captcha_response']"))
                if not box:
                    return False
                try:
                    box.click(); box.fill("")
                    box.type(text, delay=60)
                    time.sleep(0.5)
                    # Continue dabao
                    if not self._click_button(["Continue", "Submit", "Confirm"]):
                        self.page.keyboard.press("Enter")
                    time.sleep(4)
                    # agar captcha gaya to kamyab
                    if not self._has_text_captcha():
                        self.log(T("Text captcha solved (free).", "Text captcha solve ho gaya (free)."))
                        return True
                except Exception:
                    pass
            # galat/na-mila: naya captcha generate karke dobara
            try:
                gen = self.page.query_selector("a:has-text('Generate new'), [role='button']:has-text('Generate new')")
                if gen:
                    gen.click(); time.sleep(2)
            except Exception:
                pass
        self.log(T("Could not auto-solve captcha — please solve it manually, then Resume.",
                   "Captcha auto solve nahi hua — khud solve karein, phir Resume."))
        return False

    # ---------- Captcha (2captcha.com) ----------
    def _has_captcha(self):
        try:
            # sab frames ke URL dekho (Facebook ka recaptcha frame ke andar frame mein hota hai)
            for fr in self.page.frames:
                fu = (fr.url or "").lower()
                if ("recaptcha" in fu or "hcaptcha" in fu or "/captcha/" in fu) and "about:blank" not in fu:
                    return True
            for fr in self.page.frames:
                try:
                    if fr.query_selector("iframe[title*='reCAPTCHA' i], iframe[src*='captcha' i], [data-sitekey]"):
                        return True
                except Exception:
                    continue
            if self.page.query_selector("iframe[src*='recaptcha'], iframe[title*='recaptcha' i]"):
                return True
            if self.page.query_selector("iframe[src*='hcaptcha'], iframe[title*='hcaptcha' i]"):
                return True
            # sirf asli nazar aane wala captcha (script ke andar ka lafz nahi — 2Captcha ke paise zaya na hon)
            if self.page.query_selector("iframe[src*='captcha' i]") or self.page.query_selector(".g-recaptcha, .h-captcha"):
                return True
            vis = (self.page.inner_text("body") or "").lower()
            return ("i'm not a robot" in vis or "security check" in vis) and "captcha" in self.page.content().lower()
        except Exception:
            return False

    # ----- reCAPTCHA: apne browser mein checkbox click + image challenge 2Captcha (grid) se -----
    # Token wala tareeqa (worker kisi aur IP se solve kare) Facebook reject kar deta hai
    # ("login information incorrect"). Is liye pehle asli browser mein khud click karo —
    # token isi browser/IP ka banta hai aur FB accept karta hai.
    def _cap_frames(self):
        anchor = bframe = None
        for fr in self.page.frames:
            fu = (fr.url or "").lower()
            if "recaptcha" in fu and "/anchor" in fu:
                anchor = fr
            elif "recaptcha" in fu and "/bframe" in fu:
                bframe = fr
        return anchor, bframe

    def _anchor_checked(self, anchor):
        try:
            return (anchor.get_attribute("#recaptcha-anchor", "aria-checked", timeout=2000) or "") == "true"
        except Exception:
            return False

    def _human_click(self, frame, el):
        try:
            box = el.bounding_box()
            if box:
                x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                self.page.mouse.move(x - random.randint(40, 120), y - random.randint(20, 60), steps=8)
                time.sleep(random.uniform(0.1, 0.3))
                self.page.mouse.move(x, y, steps=random.randint(6, 14))
                time.sleep(random.uniform(0.05, 0.2))
                self.page.mouse.click(x, y)
                return True
        except Exception:
            pass
        try:
            el.click(); return True
        except Exception:
            return False

    def _twocap(self, key, fields, wait_secs=150):
        """2Captcha: POST job, poll answer. Return answer string ya None."""
        import urllib.request, urllib.parse, json as _json
        body = dict(fields); body.update({"key": key, "json": 1})
        r = urllib.request.urlopen("https://2captcha.com/in.php",
                                   data=urllib.parse.urlencode(body).encode(), timeout=40)
        d = _json.loads(r.read().decode())
        if str(d.get("status")) != "1":
            self.log(f"2captcha submit error: {d.get('request')}")
            return None
        cid = d["request"]
        t0 = time.time()
        time.sleep(4)
        while time.time() - t0 < wait_secs:
            time.sleep(3)
            rr = urllib.request.urlopen(
                f"https://2captcha.com/res.php?key={key}&action=get&id={cid}&json=1", timeout=30)
            rd = _json.loads(rr.read().decode())
            if str(rd.get("status")) == "1":
                return str(rd.get("request") or "")
            if rd.get("request") != "CAPCHA_NOT_READY":
                self.log(f"2captcha: {rd.get('request')}")
                return None
        self.log("2captcha timeout.")
        return None

    def _find_iframe_el(self, kinds):
        """Sab frames mein captcha iframe ELEMENT dhoondo (frame khud list na ho tab bhi)."""
        sels = {"anchor": ["iframe[src*='anchor']", "iframe[title='reCAPTCHA' i]",
                           "iframe[title*='reCAPTCHA' i]:not([title*='challenge' i])"],
                "challenge": ["iframe[src*='bframe']", "iframe[title*='challenge' i]"]}[kinds]
        for fr in self.page.frames:
            for s in sels:
                try:
                    for el in fr.query_selector_all(s):
                        b = el.bounding_box()
                        if b and b["width"] > 50 and b["height"] > 40 and b["y"] > -50 and b["x"] > -50 \
                                and el.is_visible():
                            try:      # chhupa hua challenge (visibility:hidden parent) ignore
                                hidden = el.evaluate("e => { let n = e; while (n) { const cs = getComputedStyle(n); "
                                                     "if (cs.visibility === 'hidden' || cs.display === 'none' || "
                                                     "parseFloat(cs.opacity) === 0) return true; n = n.parentElement; } return false; }")
                            except Exception:
                                hidden = False
                            if not hidden:
                                return el, b
                except Exception:
                    continue
        return None, None

    def _click_xy(self, x, y):
        self.page.mouse.move(x - random.randint(30, 90), y - random.randint(15, 45), steps=8)
        time.sleep(random.uniform(0.1, 0.3))
        self.page.mouse.move(x, y, steps=random.randint(6, 12))
        time.sleep(random.uniform(0.05, 0.2))
        self.page.mouse.click(x, y)

    def _anchor_ticked(self, el):
        """reCAPTCHA box mein hara tick hai? (frame list na ho tab bhi — tasveer ke rang se)"""
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(el.screenshot(scale="css"))).convert("RGB")
            w, h = im.size
            im = im.crop((0, 0, min(80, w), h))
            green = sum(1 for (r, g, b) in im.getdata() if g > 120 and r < 90 and b < 90)
            return green > 25
        except Exception:
            return False

    def _solve_recaptcha_by_position(self, key):
        """Google frame list na ho to: iframe ki jagah par click + tasveerein 2Captcha (coordinates)."""
        import base64
        import re
        el, b = self._find_iframe_el("anchor")
        if not el:
            self.log(T("Captcha box not found on screen.", "Captcha box screen par nahi mila."))
            return False
        if self._anchor_ticked(el):
            return True
        clicks = 0
        rounds = 0
        while clicks < 3 and rounds < 8:
            el, b = self._find_iframe_el("anchor")
            if el and self._anchor_ticked(el):
                self.log(T("Captcha ticked ✔", "Captcha tick ho gaya ✔"))
                return True
            ch, cb = self._find_iframe_el("challenge")
            if not ch:
                if not el:
                    return not self._has_captcha()
                clicks += 1
                self.log(T(f"Captcha: clicking 'I'm not a robot' ({clicks})...",
                           f"Captcha: 'I'm not a robot' click ({clicks})..."))
                self._click_xy(b["x"] + min(30, b["width"] * 0.1), b["y"] + b["height"] / 2)
                # tasveerein ya tick aane ka intezaar (12 sec tak)
                for _ in range(12):
                    time.sleep(1)
                    if self._find_iframe_el("challenge")[0]:
                        break
                    e2, _b2 = self._find_iframe_el("anchor")
                    if e2 and self._anchor_ticked(e2):
                        self.log(T("Captcha ticked ✔", "Captcha tick ho gaya ✔"))
                        return True
                continue
            # tasveerein aayi hain -> 2Captcha
            rounds += 1
            time.sleep(1.2)                       # tasveerein poori load hon
            ch, cb = self._find_iframe_el("challenge")
            if not ch:
                continue
            if rounds in (3, 6):
                # 2 dafa ghalat — naya (aasaan) challenge lo
                self.log(T("Captcha: taking a new challenge...", "Captcha: naya challenge le rahe hain..."))
                self._click_xy(cb["x"] + 28, cb["y"] + cb["height"] - 28)
                time.sleep(3)
                ch, cb = self._find_iframe_el("challenge")
                if not ch:
                    continue
            try:
                png = ch.screenshot(scale="css")
            except Exception:
                time.sleep(1); continue
            self.log(T(f"Captcha images → 2Captcha (round {rounds})...",
                       f"Captcha tasveerein → 2Captcha (round {rounds})..."))
            ans = self._twocap(key, {"method": "base64", "body": base64.b64encode(png).decode(),
                                     "coordinatescaptcha": 1,
                                     "textinstructions": "Follow the instruction in the blue header and click every matching "
                                                         "image tile. If none match, click the Skip/Verify button."},
                               wait_secs=90)
            ch, cb = self._find_iframe_el("challenge")
            if not ch:
                continue
            if ans is None:
                # naya challenge lo (reload button: neeche baayen)
                self._click_xy(cb["x"] + 28, cb["y"] + cb["height"] - 28)
                time.sleep(3); continue
            pts = re.findall(r"x=(\d+),y=(\d+)", ans)
            for xs, ys in pts:
                self._click_xy(cb["x"] + int(xs), cb["y"] + int(ys))
                time.sleep(random.uniform(0.25, 0.6))
            time.sleep(1.2)
            ch2, cb2 = self._find_iframe_el("challenge")
            if ch2:     # Verify / Skip (neeche daayen)
                self._click_xy(cb2["x"] + cb2["width"] - 50, cb2["y"] + cb2["height"] - 28)
            time.sleep(4)
        el, b = self._find_iframe_el("anchor")
        return bool(el and self._anchor_ticked(el))

    def _solve_recaptcha_click(self, key):
        import base64
        anchor, bframe = self._cap_frames()
        if not anchor:
            return self._solve_recaptcha_by_position(key)
        try:
            cb = anchor.wait_for_selector("#recaptcha-anchor", timeout=8000)
        except Exception:
            cb = None
        if not cb:
            return False
        if not self._anchor_checked(anchor):
            self.log(T("Captcha: clicking 'I'm not a robot' in the browser...",
                       "Captcha: browser mein 'I'm not a robot' click..."))
            self._human_click(anchor, cb)
            time.sleep(4)
        for rnd in range(10):
            if self._anchor_checked(anchor):
                self.log(T("Captcha passed in the browser.", "Captcha browser mein pass ho gaya."))
                return True
            anchor, bframe = self._cap_frames()
            if not bframe:
                time.sleep(2)
                if anchor and self._anchor_checked(anchor):
                    continue
                return False
            try:
                tbl = bframe.query_selector("table[class*='rc-imageselect-table']")
                if not tbl or not tbl.is_visible():
                    time.sleep(2)
                    if rnd >= 2:
                        return self._anchor_checked(anchor)
                    continue
                inst_el = bframe.query_selector(".rc-imageselect-desc-wrapper, .rc-imageselect-instructions")
                inst = " ".join((inst_el.inner_text() if inst_el else "").split())
                rows = tbl.query_selector_all("tr")
                n_rows = len(rows) or 3
                n_cols = len(rows[0].query_selector_all("td")) if rows else 3
                png = tbl.screenshot(scale="css")
                dynamic = "none left" in inst.lower()
                self.log(T(f"Captcha images ({n_rows}x{n_cols}): '{inst[:60]}' → 2Captcha...",
                           f"Captcha tasveerein ({n_rows}x{n_cols}): '{inst[:60]}' → 2Captcha..."))
                ans = self._twocap(key, {"method": "base64", "body": base64.b64encode(png).decode(),
                                         "recaptcha": 1, "textinstructions": inst or "Select all matching images",
                                         "recaptcharows": n_rows, "recaptchacols": n_cols,
                                         "can_no_answer": 1}, wait_secs=120)
                if ans is None:
                    # na hal hua — naya challenge (reload) le kar dobara
                    try:
                        bframe.click("#recaptcha-reload-button", timeout=3000)
                    except Exception:
                        pass
                    time.sleep(3); continue
                tiles = tbl.query_selector_all("td")
                picks = []
                if ans.lower().startswith("click:"):
                    for part in ans.split(":", 1)[1].split("/"):
                        try:
                            picks.append(int(part.strip()))
                        except Exception:
                            pass
                for n in picks:
                    if 1 <= n <= len(tiles):
                        self._human_click(bframe, tiles[n - 1])
                        time.sleep(random.uniform(0.3, 0.8))
                if dynamic and picks:
                    time.sleep(5)            # nayi tasveerein aane do, phir dobara dekho
                    continue
                time.sleep(1)
                try:
                    vb = bframe.query_selector("#recaptcha-verify-button")
                    if vb:
                        self._human_click(bframe, vb)
                except Exception:
                    pass
                time.sleep(4)
            except Exception as e:
                self.log(f"captcha click-solve: {e}")
                time.sleep(2)
        return self._anchor_checked(anchor) if anchor else False

    def _solve_captcha(self):
        """reCAPTCHA: pehle browser mein khud (click + image grid via 2Captcha);
        na ho to purana token tareeqa."""
        key = (load_settings().get("captcha_key") or "").strip()
        if key:
            anchor, _ = self._cap_frames()
            if anchor or self._find_iframe_el("anchor")[0]:
                try:
                    if self._solve_recaptcha_click(key):
                        time.sleep(2)
                        for lbl in ["Continue", "Submit", "Next"]:
                            b = self.page.query_selector(f"button:has-text('{lbl}'), [role='button']:has-text('{lbl}')")
                            if b:
                                try:
                                    b.click(); break
                                except Exception:
                                    pass
                        return True
                except Exception as e:
                    self.log(f"captcha click-solve error: {e}")
                self.log(T("Captcha not solved this round — trying again...",
                           "Captcha is round mein hal nahi hua — dobara koshish..."))
                return False        # token tareeqa Facebook reject karta hai — browser mein hi dobara
        return self._solve_captcha_token()

    def _solve_captcha_token(self):
        """2captcha.com se reCAPTCHA/hCaptcha solve kare. Key settings se."""
        key = (load_settings().get("captcha_key") or "").strip()
        if not key:
            self.log(T("No 2captcha API key set (Settings). Captcha must be solved manually.",
                       "2captcha API key nahi di (Settings). Captcha khud solve karna hoga."))
            return False
        try:
            import urllib.request, urllib.parse, json as _json
        except Exception:
            return False
        try:
            # sitekey + type dhoondo — Facebook ka captcha frame ke andar frame mein hota hai,
            # is liye SAARE frames dekho (main page + fbsbx iframe + google iframe)
            import re as _re
            sitekey = captype = None
            host_frame = None            # jis frame mein widget laga hai (token yahin jayega)
            for fr in self.page.frames:
                furl = fr.url or ""
                m = _re.search(r"(?:recaptcha|hcaptcha)[^?]*/(?:api2|enterprise)?/?anchor.*?[?&]k=([^&]+)", furl) \
                    or _re.search(r"recaptcha/(?:api2|enterprise)/anchor\?.*?k=([^&]+)", furl)
                if m:
                    sitekey = m.group(1); captype = "recaptcha"
                    host_frame = fr.parent_frame or self.page.main_frame
                    if "enterprise" in furl:
                        captype = "recaptcha_ent"
                    break
                if "hcaptcha.com" in furl and "sitekey=" in furl:
                    m = _re.search(r"sitekey=([^&#]+)", furl)
                    if m:
                        sitekey = m.group(1); captype = "hcaptcha"
                        host_frame = fr.parent_frame or self.page.main_frame
                        break
            if not sitekey:
                for fr in self.page.frames:
                    try:
                        el = fr.query_selector("[data-sitekey]")
                        if el and el.get_attribute("data-sitekey"):
                            sitekey = el.get_attribute("data-sitekey")
                            cls = (el.get_attribute("class") or "")
                            captype = "hcaptcha" if "h-captcha" in cls else "recaptcha"
                            host_frame = fr
                            break
                    except Exception:
                        continue
            if not sitekey:
                self.log(T("Captcha sitekey not found — solve manually.",
                           "Captcha sitekey nahi mili — khud solve karein."))
                return False
            self.log(f"Captcha: {captype}, key {sitekey[:12]}…")
            method = "hcaptcha" if captype == "hcaptcha" else "userrecaptcha"
            page_url = (host_frame.url if host_frame and (host_frame.url or "").startswith("http") else self.page.url)
            # 1) submit job
            q = {"key": key, "method": method,
                 ("sitekey" if method == "hcaptcha" else "googlekey"): sitekey,
                 "pageurl": page_url, "json": 1}
            if captype == "recaptcha_ent":
                q["enterprise"] = 1
            params = urllib.parse.urlencode(q)
            r = urllib.request.urlopen(f"https://2captcha.com/in.php?{params}", timeout=30)
            data = _json.loads(r.read().decode())
            if str(data.get("status")) != "1":
                self.log(f"2captcha submit error: {data.get('request')}")
                return False
            cap_id = data["request"]
            self.log(T("Captcha sent to 2captcha, waiting for solution...",
                       "Captcha 2captcha ko bheja, hal ka intezaar..."))
            # 2) poll result
            token = None
            for _ in range(24):   # ~2 min
                time.sleep(5)
                res = urllib.request.urlopen(
                    f"https://2captcha.com/res.php?key={key}&action=get&id={cap_id}&json=1", timeout=30)
                rd = _json.loads(res.read().decode())
                if str(rd.get("status")) == "1":
                    token = rd["request"]; break
                if rd.get("request") != "CAPCHA_NOT_READY":
                    self.log(f"2captcha error: {rd.get('request')}")
                    return False
            if not token:
                self.log(T("2captcha timeout.", "2captcha timeout."))
                return False
            # 3) token us frame me daalo jahan widget hai + callback (sab frames try)
            _js = """(tok) => {
                document.querySelectorAll('textarea[name="g-recaptcha-response"], textarea#g-recaptcha-response, textarea[name="h-captcha-response"]')
                    .forEach(t => { t.style.display = 'block'; t.value = tok; t.innerHTML = tok; });
                let called = 0;
                const walk = (o, d) => {
                    if (!o || d > 5 || typeof o !== 'object') return;
                    for (const k in o) {
                        try {
                            const v = o[k];
                            if (k === 'callback' && typeof v === 'function') { v(tok); called++; }
                            else if (k === 'callback' && typeof v === 'string' && typeof window[v] === 'function') { window[v](tok); called++; }
                            else if (v && typeof v === 'object') walk(v, d + 1);
                        } catch (e) {}
                    }
                };
                try { if (typeof ___grecaptcha_cfg !== 'undefined') walk(___grecaptcha_cfg.clients, 0); } catch (e) {}
                return called;
            }"""
            frames = ([host_frame] if host_frame else []) + [f for f in self.page.frames if f is not host_frame]
            for fr in frames:
                try:
                    fr.evaluate(_js, token)
                except Exception:
                    pass
            self.log(T("Captcha solved.", "Captcha hal ho gaya."))
            time.sleep(2)
            # agar koi submit/continue button ho to dabao
            for lbl in ["Continue", "Submit", "Log In", "Log in"]:
                b = self.page.query_selector(f"button:has-text('{lbl}'), [role='button']:has-text('{lbl}')")
                if b:
                    try:
                        b.click(); break
                    except Exception:
                        pass
            return True
        except Exception as e:
            self.log(f"2captcha solve error: {e}")
            return False

    # ---------- Active / warm-up actions ----------
    def _scroll_for(self, seconds, label=""):
        end = time.time() + seconds
        while time.time() < end:
            try:
                delta = random.randint(300, 900)
                if random.random() < 0.2:
                    delta = -random.randint(200, 500)
                self.page.mouse.wheel(0, delta)
            except Exception:
                pass
            time.sleep(min(random.uniform(2, 6), max(0, end - time.time())))

    def act_check_notification(self, count):
        self.log("  • Notifications check...")
        try:
            self._nav("notifications")
            time.sleep(random.uniform(3, 6))
            self._scroll_for(random.uniform(4, 8))
        except Exception as e:
            self.log(T(f"    notification skipped: {e}", f"    notification skip: {e}"))

    # ---------- insaan jaisi harkat ----------
    # ---------- insaan ki tarah navigation (seedha link nahi, Facebook ke andar click) ----------
    _NAV = {
        "home": (["a[aria-label='Facebook']", "a[aria-label='Home']", "[role='navigation'] a[href='/']"], "https://www.facebook.com/"),
        "notifications": (["[role='navigation'] a[href*='/notifications']", "div[aria-label='Notifications'][role='button']",
                           "a[aria-label='Notifications']"], "https://www.facebook.com/notifications"),
        "messages": (["[role='navigation'] a[href*='/messages']", "div[aria-label='Messenger'][role='button']",
                      "a[aria-label='Messenger']"], "https://www.facebook.com/messages/t/"),
        "friends": (["a[href$='/friends/']", "a[href$='/friends']", "a[aria-label='Friends']"], "https://www.facebook.com/friends"),
        "watch": (["a[href*='/watch']", "a[aria-label='Video']", "a[aria-label='Watch']"], "https://www.facebook.com/watch/"),
        "reels": (["a[href*='/reel/']", "a[href$='/reels/']", "a[aria-label='Reels']"], "https://www.facebook.com/reel/"),
        "me": (["a[aria-label='Your profile']", "[role='navigation'] a[href*='/me/']",
                "[role='navigation'] a[href*='profile.php']"], "https://www.facebook.com/me"),
    }

    def _nav(self, key):
        """Facebook ke andar button/link par click kar ke jao; na mile to aakhir mein seedha khol do."""
        try:
            if "facebook.com" not in (self.page.url or ""):
                self.page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                time.sleep(random.uniform(3, 5))
            if key in ("friend_requests", "friend_suggestions"):
                self._nav("friends")
                sub = "/friends/requests" if key == "friend_requests" else "/friends/suggestions"
                for el in self.page.query_selector_all(f"a[href*='{sub}']"):
                    if el.is_visible():
                        self._human_click(None, el); time.sleep(random.uniform(3, 5)); return
                self.page.goto("https://www.facebook.com" + sub, wait_until="domcontentloaded"); time.sleep(3)
                return
            sels, fallback = self._NAV[key]
            for sel in sels:
                for el in self.page.query_selector_all(sel):
                    try:
                        if not el.is_visible():
                            continue
                        self._human_click(None, el)
                        time.sleep(random.uniform(2.5, 4))
                        if key in ("notifications", "messages") and "role" in sel:
                            # dropdown khula — 'See all' se poora page
                            for a in self.page.query_selector_all(
                                    "a[href*='/notifications']" if key == "notifications" else "a[href*='/messages/t']"):
                                if a.is_visible():
                                    self._human_click(None, a); time.sleep(random.uniform(2.5, 4)); break
                        return
                    except Exception:
                        continue
            self.page.goto(fallback, wait_until="domcontentloaded"); time.sleep(random.uniform(3, 5))
        except Exception:
            try:
                self.page.goto(self._NAV.get(key, (None, "https://www.facebook.com/"))[1], wait_until="domcontentloaded")
            except Exception:
                pass

    def _unsafe_text(self, txt):
        t = (txt or "").lower()
        return any(w in t for w in UNSAFE_WORDS)

    def _visible_posts(self):
        """Screen par nazar aane wali posts (article)."""
        out = []
        try:
            vh = self.page.viewport_size["height"] if self.page.viewport_size else 900
        except Exception:
            vh = 900
        try:
            for el in self.page.query_selector_all("div[role='article'], div[aria-posinset]"):
                try:
                    b = el.bounding_box()
                    if b and b["y"] < vh * 0.7 and b["y"] + b["height"] > vh * 0.25 and b["height"] > 120:
                        out.append(el)
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _like_visible_post(self):
        """Saamne wali (saaf) post ko Like. Gandi / nudity wali skip."""
        for post in self._visible_posts():
            try:
                if self._unsafe_text(post.inner_text()):
                    continue
                btn = None
                for b in post.query_selector_all("div[aria-label='Like'][role='button'], span[aria-label='Like'][role='button']"):
                    if b.is_visible():
                        btn = b; break
                if not btn:
                    continue
                self._human_click(None, btn)
                time.sleep(random.uniform(1, 2.5))
                return True
            except Exception:
                continue
        return False

    def _comment_visible_post(self, text):
        for post in self._visible_posts():
            try:
                if self._unsafe_text(post.inner_text()):
                    continue
                box = post.query_selector("div[contenteditable='true'][role='textbox']")
                if not box:
                    cb = post.query_selector("div[aria-label='Leave a comment'][role='button'], div[aria-label='Comment'][role='button']")
                    if cb:
                        cb.click(); time.sleep(random.uniform(1.5, 3))
                    box = self.page.query_selector("div[contenteditable='true'][role='textbox'][aria-label*='comment' i]")
                if not box:
                    continue
                box.click(); time.sleep(random.uniform(0.6, 1.5))
                box.type(spin(text), delay=random.randint(40, 110))
                time.sleep(random.uniform(0.5, 1.2))
                self.page.keyboard.press("Enter")
                time.sleep(random.uniform(2, 4))
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                return True
            except Exception:
                continue
        return False

    def _human_feed(self, seconds, like_max=0, comment_max=0, comment_text=""):
        """Feed insaan ki tarah: thora scroll, kabhi ruk kar parhna, kabhi upar, kabhi like/comment."""
        end = time.time() + seconds
        likes = comments = 0
        while time.time() < end:
            try:
                if random.random() < 0.12:
                    self.page.mouse.wheel(0, -random.randint(150, 400))       # thora wapas
                else:
                    self.page.mouse.wheel(0, random.randint(300, 750))
            except Exception:
                pass
            time.sleep(random.uniform(1.2, 3.5))
            if random.random() < 0.25:
                time.sleep(random.uniform(3, 9))                                # post parh raha hai
            if likes < like_max and random.random() < 0.35:
                if self._like_visible_post():
                    likes += 1
                    self.log(f"    like {likes}/{like_max}")
            if comments < comment_max and comment_text and random.random() < 0.25:
                if self._comment_visible_post(comment_text):
                    comments += 1
                    self.log(f"    comment {comments}/{comment_max}")
        return likes, comments

    def _scroll_for(self, seconds, label=""):
        self._human_feed(seconds)

    def act_scroll_feed(self, minutes):
        secs = int(minutes * 60)
        self.log(T(f"  • Scrolling feed ~{minutes:.1f} min (1-2 likes)...", f"  • Feed scroll ~{minutes:.1f} min (1-2 like)..."))
        try:
            self._nav("home")
            time.sleep(random.uniform(2, 4))
            self._human_feed(secs, like_max=random.randint(1, 2))
        except Exception as e:
            self.log(T(f"    feed skipped: {e}", f"    feed skip: {e}"))

    def act_check_stories(self, minutes):
        secs = int(minutes * 60)
        self.log(T(f"  • Watching stories ~{minutes:.1f} min...", f"  • Stories dekh rahe ~{minutes:.1f} min..."))
        try:
            self._nav("home")
            time.sleep(random.uniform(3, 5))
            st = None
            for el in self.page.query_selector_all("a[href*='/stories/']"):
                try:
                    if el.is_visible() and "create" not in (el.get_attribute("href") or ""):
                        st = el; break
                except Exception:
                    continue
            if not st:
                self._human_feed(min(secs, 20)); return
            st.click(); time.sleep(random.uniform(2, 4))
            end = time.time() + secs
            while time.time() < end:
                time.sleep(random.uniform(3, 9))                 # story dekho
                if random.random() < 0.3:
                    self.page.keyboard.press("ArrowRight")       # kabhi agli par
            self.page.keyboard.press("Escape")
        except Exception as e:
            self.log(T(f"    stories skipped: {e}", f"    stories skip: {e}"))

    def _watch_secs(self, short=False):
        r = random.random()
        if short:                    # reels
            return random.uniform(2, 7) if r < 0.6 else (random.uniform(10, 25) if r < 0.92 else random.uniform(30, 60))
        return random.uniform(3, 12) if r < 0.55 else (random.uniform(20, 60) if r < 0.88 else random.uniform(60, 150))

    def _media_watch(self, secs, reels):
        """Videos/Reels insaan ki tarah: koi skip, koi lambi, achhi par kabhi like (max 2), gandi foran skip."""
        end = time.time() + secs
        likes, like_max = 0, random.randint(1, 2)
        while time.time() < end:
            try:
                main = self.page.query_selector("[role='main']")
                txt = (main.inner_text() if main else "")[:1500]
            except Exception:
                txt = ""
            if self._unsafe_text(txt):
                time.sleep(random.uniform(0.5, 1.5))              # gandi — foran aage
            else:
                w = min(self._watch_secs(short=reels), max(1, end - time.time()))
                time.sleep(w)
                if likes < like_max and w > 12 and random.random() < 0.4:
                    try:
                        for b in self.page.query_selector_all("div[aria-label='Like'][role='button']"):
                            if b.is_visible():
                                self._human_click(None, b); likes += 1
                                self.log(f"    like {likes}"); time.sleep(random.uniform(1, 2)); break
                    except Exception:
                        pass
            try:
                if reels:
                    self.page.keyboard.press("ArrowDown")
                else:
                    self.page.mouse.wheel(0, random.randint(550, 900))
            except Exception:
                pass
            time.sleep(random.uniform(1, 2.5))

    def act_scroll_videos(self, minutes):
        secs = int(minutes * 60)
        self.log(T(f"  • Watching videos ~{minutes:.1f} min...", f"  • Videos dekh rahe ~{minutes:.1f} min..."))
        try:
            self._nav("watch")
            time.sleep(random.uniform(3, 5))
            self._media_watch(secs, reels=False)
        except Exception as e:
            self.log(T(f"    videos skipped: {e}", f"    videos skip: {e}"))

    def act_scroll_reels(self, minutes):
        secs = int(minutes * 60)
        self.log(T(f"  • Watching reels ~{minutes:.1f} min...", f"  • Reels dekh rahe ~{minutes:.1f} min..."))
        try:
            self._nav("reels")
            time.sleep(random.uniform(3, 5))
            self._media_watch(secs, reels=True)
        except Exception as e:
            self.log(T(f"    reels skipped: {e}", f"    reels skip: {e}"))

    def act_reactions(self, count):
        self.log(T(f"  • Liking {count} posts (clean posts only)...", f"  • {count} post like (sirf saaf posts)..."))
        try:
            self._nav("home")
            time.sleep(random.uniform(3, 5))
            done = 0
            for _ in range(count * 8):
                if done >= count:
                    break
                self.page.mouse.wheel(0, random.randint(400, 900))
                time.sleep(random.uniform(1.5, 4))
                if random.random() < 0.5 and self._like_visible_post():     # har post nahi — kuch chhor ke
                    done += 1
                    self.log(T(f"    Like {done}/{count}", f"    Like {done}/{count}"))
                    time.sleep(random.uniform(2, 5))
        except Exception as e:
            self.log(T(f"    reaction skipped: {e}", f"    reaction skip: {e}"))

    def act_comments(self, count, text):
        self.log(T(f"  • Commenting on {count} posts...", f"  • {count} post par comment..."))
        try:
            self._nav("home")
            time.sleep(random.uniform(3, 5))
            done = 0
            for _ in range(count * 8):
                if done >= count:
                    break
                self.page.mouse.wheel(0, random.randint(500, 1000))
                time.sleep(random.uniform(2, 4))
                if random.random() < 0.45 and self._comment_visible_post(text):
                    done += 1
                    self.log(T(f"    Comment {done}/{count}", f"    Comment {done}/{count}"))
        except Exception as e:
            self.log(T(f"    comment skipped: {e}", f"    comment skip: {e}"))

    def _msg_today(self, add=0):
        acc = get_account(self.current_acc["id"]) or {}
        d = acc.get("msg_daily") or {}
        today = time.strftime("%Y-%m-%d")
        n = d.get("n", 0) if d.get("date") == today else 0
        if add:
            n += add
            update_account(self.current_acc["id"], msg_daily={"date": today, "n": n})
        return n

    def act_messages_reply(self, count, reply_on, text):
        """Naye messages hon to un mein se (max 2) ka jawab. Na hon to kisi friend ko halka sa message
        (din mein 1-2 se zyada nahi). Friends hi na hon to pehle 2 naye friends add."""
        self.log(T(f"  • Checking messages...", f"  • Messages check..."))
        text = text or "{Hi|Hey|Hello} {how are you?|kaise ho?|🙂}"
        try:
            self._nav("messages")
            time.sleep(random.uniform(4, 6))
            unread = self.page.evaluate("""() => {
                const out = [];
                document.querySelectorAll("a[href*='/messages/t/']").forEach((a, i) => {
                    const spans = Array.from(a.querySelectorAll('span')).filter(s => s.children.length === 0 && s.innerText.trim());
                    if (spans.length < 2) return;
                    const last = spans[spans.length - 1];
                    const w = parseInt(getComputedStyle(last).fontWeight || '400');
                    if (w >= 600 || /unread/i.test(a.getAttribute('aria-label') || '')) out.push(a.getAttribute('href'));
                });
                return out;
            }""") or []
            replied = 0
            if unread and reply_on:
                for href in unread[:min(max(1, count), 2)]:
                    try:
                        self.page.goto("https://www.facebook.com" + href if href.startswith("/") else href,
                                       wait_until="domcontentloaded")
                        time.sleep(random.uniform(3, 6))           # message parho
                        box = self.page.query_selector("div[aria-label*='Message' i][contenteditable='true']")
                        if box:
                            box.click(); box.type(spin(text), delay=random.randint(50, 120))
                            time.sleep(random.uniform(0.5, 1.5)); self.page.keyboard.press("Enter")
                            replied += 1
                            self.log(T(f"    Reply {replied}", f"    Reply {replied}"))
                            time.sleep(random.uniform(3, 6))
                    except Exception:
                        continue
                return
            if not unread:
                self._scroll_for(random.uniform(3, 6))
            if not reply_on or self._msg_today() >= 2:
                return
            # kisi friend ko halka message
            self.page.goto("https://www.facebook.com/me/friends", wait_until="domcontentloaded")
            time.sleep(random.uniform(4, 6))
            links = self.page.evaluate("""() => Array.from(document.querySelectorAll("[role='main'] a[href]"))
                .map(a => a.href).filter(h => /facebook\\.com\\/(profile\\.php\\?id=\\d+|[A-Za-z0-9.]+)$/.test(h.split('&')[0])
                    && !/\\/(friends|me|photos|videos|about)/.test(h))""") or []
            links = list(dict.fromkeys(links))
            if not links:
                self.log(T("    no friends yet — adding 2 new friends first", "    abhi friends nahi — pehle 2 naye friends add"))
                self.act_add_friends_suggest(2)
                return
            target = random.choice(links[:30])
            self.page.goto(target, wait_until="domcontentloaded"); time.sleep(random.uniform(4, 7))
            if self._click_button(["Message"]):
                time.sleep(random.uniform(3, 5))
                box = self.page.query_selector("div[aria-label*='Message' i][contenteditable='true']")
                if box:
                    box.click(); box.type(spin(text), delay=random.randint(50, 120))
                    time.sleep(random.uniform(0.5, 1.2)); self.page.keyboard.press("Enter")
                    self._msg_today(add=1)
                    self.log(T("    sent a friendly message to a friend", "    ek friend ko message bheja"))
                    time.sleep(random.uniform(2, 4))
                    try:
                        self.page.keyboard.press("Escape")
                    except Exception:
                        pass
        except Exception as e:
            self.log(T(f"    message skipped: {e}", f"    message skip: {e}"))

    def act_confirm_friends(self, count):
        self.log(T(f"  • Confirming friend requests ({count})...", f"  • Friend requests confirm ({count})..."))
        try:
            self._nav("friend_requests")
            time.sleep(4)
            done = 0
            btns = self.page.query_selector_all("div[aria-label='Confirm'][role='button']")
            for b in btns:
                if done >= count:
                    break
                try:
                    b.click()
                    done += 1
                    self.log(T(f"    Confirm {done}/{count}", f"    Confirm {done}/{count}"))
                    time.sleep(random.uniform(2, 4))
                except Exception:
                    continue
        except Exception as e:
            self.log(T(f"    confirm skipped: {e}", f"    confirm skip: {e}"))

    # ================= Bob Prime jaise naye Active kaam =================
    def _read_list(self, path_or_text):
        """File path ho to file ki lines, warna text ki lines/commas."""
        src = (path_or_text or "").strip()
        if src and os.path.isfile(src):
            try:
                src = open(src, encoding="utf-8", errors="ignore").read()
            except Exception:
                src = ""
        out = []
        for part in src.replace(",", "\n").splitlines():
            part = part.strip()
            if part and not part.startswith("#"):
                out.append(part)
        return out

    def _done_set(self, key):
        acc = get_account(self.current_acc["id"]) or {}
        return set(acc.get(key, []))

    def _mark_done(self, key, val):
        acc = get_account(self.current_acc["id"]) or {}
        lst = list(acc.get(key, []))
        if val not in lst:
            lst.append(val)
        update_account(self.current_acc["id"], **{key: lst[-2000:]})

    def act_add_friends_suggest(self, count):
        """People You May Know se friend requests — SIRF larkon ko (naam se pehchaan; shak ho to skip),
        har dafa alag tarteeb (kabhi pehla, kabhi chautha...)."""
        self.log(T(f"  • Adding friends from suggestions — boys only ({count})...",
                   f"  • Suggestions se friends add — sirf larke ({count})..."))
        done = 0
        seen = set()
        try:
            self._nav("friend_suggestions")
            time.sleep(random.uniform(4, 6))
            for _ in range(count * 6):
                if done >= count:
                    break
                cards = self.page.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll("div[aria-label='Add friend'][role='button'], div[aria-label='Add Friend'][role='button']")
                      .forEach((b, i) => {
                        let n = b, name = '';
                        for (let k = 0; k < 8 && n; k++) {
                            n = n.parentElement;
                            if (!n) break;
                            const a = n.querySelector("a[href*='facebook.com/'] span, a[role='link'] span");
                            if (a && a.innerText.trim()) { name = a.innerText.trim().split('\\n')[0]; break; }
                        }
                        const r = b.getBoundingClientRect();
                        out.push({i, name, vis: r.width > 0 && r.top > 0 && r.top < innerHeight});
                      });
                    return out;
                }""") or []
                btns = self.page.query_selector_all("div[aria-label='Add friend'][role='button'], div[aria-label='Add Friend'][role='button']")
                picked = None
                for c in cards:
                    if not c.get("vis") or c["name"] in seen:
                        continue
                    seen.add(c["name"])
                    g = guess_gender(c["name"])
                    if g != "male":
                        self.log(f"    skip ({'girl' if g == 'female' else 'not sure'}): {c['name']}")
                        continue
                    if random.random() < 0.4:             # kuch chhor ke (har dafa alag tarteeb)
                        continue
                    picked = c; break
                if picked and picked["i"] < len(btns):
                    try:
                        b = btns[picked["i"]]
                        b.scroll_into_view_if_needed(); time.sleep(random.uniform(0.8, 2))
                        self._human_click(None, b); done += 1
                        self.log(f"    add friend {done}/{count}: {picked['name']}")
                        time.sleep(random.uniform(3, 7))
                        continue
                    except Exception:
                        pass
                self.page.mouse.wheel(0, random.randint(400, 800)); time.sleep(random.uniform(1.5, 3))
        except Exception as e:
            self.log(T(f"    add friends skipped: {e}", f"    add friends skip: {e}"))
        return done

    def act_add_friends_uid(self, count, src):
        """UID / profile link list se friend request (jo pehle bheji, dobara nahi)."""
        lst = self._read_list(src)
        sent = self._done_set("friend_sent")
        todo = [u for u in lst if u not in sent]
        random.shuffle(todo)
        self.log(T(f"  • Add friends from UID list ({min(count, len(todo))})...",
                   f"  • UID list se friends add ({min(count, len(todo))})..."))
        done = 0
        for u in todo:
            if done >= count:
                break
            try:
                self.page.goto(self._norm_target(u), wait_until="domcontentloaded")
                time.sleep(random.uniform(4, 7))
                self._scroll_for(random.uniform(2, 5))
                self.page.mouse.wheel(0, -3000); time.sleep(1)
                if self._click_button(["Add friend", "Add Friend"]):
                    done += 1
                    self.log(f"    request sent {done}/{count}: {u}")
                self._mark_done("friend_sent", u)
                time.sleep(random.uniform(6, 14))
            except Exception as e:
                self.log(f"    {u}: {e}")
        return done

    def act_follow_pages(self, count, src):
        """Page / profile list ko follow ya like karo (jo pehle kiye, dobara nahi)."""
        lst = self._read_list(src)
        dn = self._done_set("followed")
        todo = [u for u in lst if u not in dn]
        random.shuffle(todo)
        self.log(T(f"  • Following pages ({min(count, len(todo))})...", f"  • Pages follow ({min(count, len(todo))})..."))
        done = 0
        for u in todo:
            if done >= count:
                break
            try:
                self.page.goto(self._norm_target(u), wait_until="domcontentloaded")
                time.sleep(random.uniform(4, 7))
                self._scroll_for(random.uniform(3, 8))
                self.page.mouse.wheel(0, -4000); time.sleep(1)
                if self._click_button(["Follow", "Like"]):
                    done += 1
                    self.log(f"    followed {done}/{count}: {u}")
                self._mark_done("followed", u)
                time.sleep(random.uniform(5, 12))
            except Exception as e:
                self.log(f"    {u}: {e}")
        return done

    # ---------- Page status / copyright / monetization ----------
    _STATUS_URLS = ["https://www.facebook.com/professional_dashboard/account_status/",
                    "https://www.facebook.com/professional_dashboard/",
                    "https://www.facebook.com/accountquality/"]
    _MON_URLS = ["https://www.facebook.com/professional_dashboard/monetization/",
                 "https://www.facebook.com/professional_dashboard/monetization_overview/"]

    def _page_text(self):
        try:
            return (self.page.inner_text("[role='main']") or "")
        except Exception:
            try:
                return self.page.inner_text("body")
            except Exception:
                return ""

    def check_page_status(self, delete_copyright=False):
        """Har tick kiye page par: Flagged / Not recommended / restrictions + monetization maloomat.
        delete_copyright=True ho to copyright wali flagged videos delete."""
        acc = self.current_acc
        targets = [{"name": f"ID: {acc['name']}", "url": "", "_profile": True}] + \
            [p for p in (get_account(acc["id"]) or {}).get("pages", []) if p.get("enabled")]
        report = {}
        for tgt in targets:
            name = tgt.get("name") or tgt.get("url") or "profile"
            self.log(T(f"  • Page status: {name}", f"  • Page status: {name}"))
            try:
                self.target_page = tgt if tgt.get("url") else None
                if tgt.get("url"):
                    self._go_to_page()
                elif tgt.get("_profile"):
                    self._switch_to_profile()
                txt = ""
                for u in self._STATUS_URLS:
                    try:
                        self.page.goto(u, wait_until="domcontentloaded"); time.sleep(random.uniform(5, 8))
                        txt += "\n" + self._page_text()
                    except Exception:
                        pass
                low = txt.lower()
                flags = []
                for k, lab in (("not recommended", "Not Recommended"), ("isn't recommended", "Not Recommended"),
                               ("not eligible", "Not eligible"), ("restricted", "Restricted"),
                               ("flagged", "Flagged"), ("copyright", "Copyright issue"),
                               ("intellectual property", "Copyright issue"), ("violat", "Violation"),
                               ("at risk", "At risk"), ("unpublished", "Unpublished")):
                    if k in low and lab not in flags:
                        flags.append(lab)
                ok_words = ("no restrictions" in low or "you're all set" in low or "no issues" in low
                            or "can be recommended" in low)
                status = ", ".join(flags) if flags else ("OK" if ok_words else "Unknown")
                removed = 0
                if delete_copyright and ("copyright" in low or "intellectual property" in low):
                    removed = self._delete_copyright_items()
                money = ""
                for u in self._MON_URLS:
                    try:
                        self.page.goto(u, wait_until="domcontentloaded"); time.sleep(random.uniform(5, 8))
                        mt = self._page_text()
                        lines = [l.strip() for l in mt.splitlines() if l.strip()]
                        keep = [l for l in lines if any(w in l.lower() for w in
                                ("$", "earning", "eligib", "monetiz", "payout", "estimated", "in-stream", "reels",
                                 "bonus", "followers", "minutes viewed", "requirement"))]
                        if keep:
                            money = " | ".join(keep[:10])[:600]
                            break
                    except Exception:
                        pass
                ml = money.lower()
                if any(k in ml for k in ("not eligible", "isn't eligible", "not yet eligible", "doesn't meet",
                                         "don't meet", "not meet")):
                    mon = "Monetization: NOT eligible"
                elif any(k in ml for k in ("you're eligible", "you are eligible", "eligible to", "get started",
                                           "start earning", "earnings", "onboard", "payout")):
                    mon = "Monetization: ELIGIBLE / active"
                else:
                    mon = "Monetization: unknown"
                status = f"{status} | {mon}"
                report[name] = {"time": time.strftime("%Y-%m-%d %H:%M"), "status": status,
                                "monetization": money, "copyright_removed": removed}
                if not tgt.get("_profile"):
                    page_stats_set(name, status=short_status(", ".join(flags) if flags else ("OK" if ok_words else "Unknown")),
                                   monetization=short_monet(money), acc_id=acc["id"])
                self.log(T(f"    → {name}: {status}" + (f" | removed {removed} copyright item(s)" if removed else ""),
                           f"    → {name}: {status}" + (f" | {removed} copyright item delete" if removed else "")))
                if money:
                    self.log(f"    $ {money[:200]}")
                add_page_history(name, "-", "status", status)
            except Exception as e:
                self.log(f"    status error: {e}")
        self.target_page = None
        if report:
            old = (get_account(acc["id"]) or {}).get("page_status", {})
            old.update(report)
            update_account(acc["id"], page_status=old)
            # pages list mein bhi status dikhao
            a2 = get_account(acc["id"]) or {}
            pg = a2.get("pages") or []
            for p in pg:
                r = report.get(p.get("name") or p.get("url"))
                if r:
                    p["status"] = r["status"]
            if pg:
                update_account(acc["id"], pages=pg)
        return report

    def _switch_to_profile(self):
        """Agar page ke taur par switched hain to wapas apni profile par."""
        try:
            self._nav("home"); time.sleep(3)
            if self._click_button(["Your profile", "Account controls and settings"]):
                time.sleep(2)
                self._click_button(["Switch to " + self.current_acc.get("name", ""), "See all profiles"])
                time.sleep(3)
        except Exception:
            pass

    def detect_profile_info(self):
        """Facebook se khud: naam, DOB, gender, city/country. Account mein save."""
        import re as _re
        acc = self.current_acc
        self.log(T("  • Detecting profile info (name, DOB, gender, location)...",
                   "  • Profile info dhoondh rahe hain (naam, DOB, gender, location)..."))
        upd = {}
        try:
            self._nav("me"); time.sleep(random.uniform(4, 6))
            u0 = self._fb_uid()
            cur = (self.page.url or "").lower()
            if u0 and not ("/me" in cur or u0 in cur or "profile.php" in cur or
                           self.page.query_selector("a[href*='/friends'][href*='sk=friends'], [aria-label='Edit profile']")):
                self.page.goto(f"https://www.facebook.com/profile.php?id={u0}", wait_until="domcontentloaded")
                time.sleep(random.uniform(4, 6))
            nm = ""
            for sel in ("[role='main'] h1", "h1"):
                try:
                    for h1 in self.page.query_selector_all(sel):
                        t = (h1.inner_text() or "").split("\n")[0].strip()
                        if t and len(t) < 80 and t.lower() not in _NOT_NAMES:
                            nm = t; break
                except Exception:
                    pass
                if nm:
                    break
            if not nm:
                try:      # tab title: "Kashif Iqbal | Facebook"
                    t = (self.page.title() or "").split("|")[0].strip()
                    t = _re.sub(r"^\(\d+\)\s*", "", t)
                    if t and t.lower() not in _NOT_NAMES and "@" not in t:
                        nm = t
                except Exception:
                    pass
            if nm:
                upd["name"] = nm
            # asli DP (profile picture) save — export ke waqt folder mein copy hogi
            try:
                dp_url = self.page.evaluate("""() => {
                    const m = document.querySelector('[role="main"]') || document;
                    const im = m.querySelector('svg[aria-label] image, a[href*="photo"] svg image');
                    if (im) return im.getAttribute('xlink:href') || im.getAttribute('href');
                    const og = document.querySelector('meta[property="og:image"]');
                    return og ? og.content : '';
                }""") or ""
                if dp_url.startswith("http"):
                    body = self.page.request.get(dp_url, timeout=20000).body()
                    if body and len(body) > 2000:
                        dd = os.path.join(DATA_DIR, "dp"); os.makedirs(dd, exist_ok=True)
                        key = (self._fb_uid() or acc.get("id", "x")).replace("@", "_")
                        fp = os.path.join(dd, key + ".jpg")
                        with open(fp, "wb") as f:
                            f.write(body)
                        upd["dp"] = fp
            except Exception:
                pass
            uid = self._fb_uid()
            if uid:
                upd["uid"] = uid
            for slug in ("about_contact_and_basic_info", "about_places", "about_overview"):
                try:
                    if uid:
                        self.page.goto(f"https://www.facebook.com/profile.php?id={uid}&sk={slug}", wait_until="domcontentloaded")
                    else:
                        self.page.goto(f"https://www.facebook.com/me/{slug}", wait_until="domcontentloaded")
                    time.sleep(random.uniform(3, 5))
                    lines = [l.strip() for l in self._page_text().splitlines() if l.strip()]
                except Exception:
                    continue
                for i, l in enumerate(lines):
                    low = l.lower()
                    nxt = lines[i + 1] if i + 1 < len(lines) else ""
                    prv = lines[i - 1] if i > 0 else ""
                    if low in ("birth date", "birthday", "date of birth") and "dob" not in upd:
                        v = prv if prv and not prv.lower().startswith(("basic info", "contact")) else nxt
                        if v and _re.search(r"\d", v):
                            upd["dob"] = v
                    elif low == "birth year" and prv:
                        upd["dob"] = (upd.get("dob", "") + " " + prv).strip()
                    elif low == "gender" and prv in ("Male", "Female", "Non-binary", "Custom"):
                        upd["gender"] = prv
                    elif low in ("current city", "lives in") and prv and "city" not in upd:
                        upd["city"] = prv
                    elif low.startswith("lives in "):
                        upd["city"] = l[9:].strip()
                    elif low == "hometown" and prv and "hometown" not in upd:
                        upd["hometown"] = prv
                    if low.startswith(("joined facebook", "joined on")) and "created" not in upd:
                        m = _re.search(r"(\w+ \d{4}|\d{4})", l)
                        if m:
                            upd["created"] = m.group(1)
            if upd.get("city") and not upd.get("country"):
                parts = [x.strip() for x in upd["city"].split(",")]
                if len(parts) >= 2:
                    upd["country"] = parts[-1]
            # IP location (isi browser ke IP/VPN se)
            try:
                if not (get_account(acc["id"]) or {}).get("ip_info"):
                    r = self.page.request.get("http://ip-api.com/json/?fields=status,country,countryCode,regionName,city,query",
                                              timeout=15000)
                    jj = r.json()
                    if jj.get("status") == "success":
                        update_account(acc["id"], ip_info=jj)
            except Exception:
                pass
            if not upd.get("country"):
                ii = (get_account(acc["id"]) or {}).get("ip_info") or {}
                if ii.get("country"):
                    upd.setdefault("country", ii["country"])
        except Exception as e:
            self.log(f"    profile info error: {e}")
        if upd:
            upd["last_update"] = _now_str()[:16]
            update_account(acc["id"], **upd)
            self.log("    → " + ", ".join(f"{k}: {v}" for k, v in upd.items() if k != "last_update"))
            try:
                APP_INSTANCE.after(0, APP_INSTANCE._refresh_all_panels)
            except Exception:
                pass
        return upd

    def check_primary_location(self):
        """Account ki PRIMARY LOCATION (Facebook jo mulk maanta hai)."""
        acc = self.current_acc
        self.log(T("  • Checking primary location...", "  • Primary location check..."))
        urls = [u for u in [load_settings().get("primary_loc_url", "").strip()] if u] + [
            "https://www.facebook.com/primary_location/info",
            "https://accountscenter.facebook.com/info_and_permissions",
            "https://www.facebook.com/professional_dashboard/account_status/",
            "https://www.facebook.com/settings?tab=account_status"]
        found = ""
        for u in urls:
            try:
                self.page.goto(u, wait_until="domcontentloaded"); time.sleep(random.uniform(5, 7))
                full = self._page_text()
                import re as _re
                m = _re.search(r"primary (?:location|country)[^\n]*?\b(?:is|:)\s*([A-Z][A-Za-z .,'-]{2,50})", full)
                if m:
                    found = m.group(1).strip().rstrip("."); break
                lines = [l.strip() for l in full.splitlines() if l.strip()]
                for i, l in enumerate(lines):
                    if "primary location" in l.lower() or "primary country" in l.lower():
                        cand = [x for x in lines[i + 1:i + 4] if 1 < len(x) < 60]
                        if cand:
                            found = cand[0]; break
                if found:
                    break
            except Exception:
                continue
        if found:
            update_account(acc["id"], primary_loc=found, last_update=_now_str()[:16])
            self.log(T(f"    → Primary location: {found}", f"    → Primary location: {found}"))
            try:
                APP_INSTANCE.after(0, APP_INSTANCE._refresh_all_panels)
            except Exception:
                pass
        else:
            self.log(T("    primary location not found (send me the page link to add it)",
                       "    primary location nahi mili (link do to add kar dunga)"))
        return found

    def act_professional_mode(self):
        """ID par Professional mode ON."""
        self.log(T("  • Turning on Professional mode...", "  • Professional mode ON kar rahe hain..."))
        try:
            self._nav("me"); time.sleep(random.uniform(5, 7))
            txt = self._page_text().lower()
            if "professional dashboard" in txt or "turn off professional mode" in txt:
                self.log(T("    already ON ✔", "    pehle se ON ✔"))
                update_account(self.current_acc["id"], pro_mode=True)
                return True
            opened = self._click_button(["Profile settings see more options", "See more options", "More"])
            time.sleep(2)
            if not self._click_button(["Turn on professional mode", "Turn on Professional mode"]):
                if not opened:
                    self.log(T("    option not found", "    option nahi mila")); return False
                self.log(T("    'Turn on professional mode' not found", "    'Turn on professional mode' nahi mila"))
                return False
            time.sleep(3)
            for _ in range(3):
                if self._click_button(["Turn on", "Continue", "Next", "Done"]):
                    time.sleep(3)
            update_account(self.current_acc["id"], pro_mode=True)
            self.log(T("    Professional mode ON ✔", "    Professional mode ON ✔"))
            return True
        except Exception as e:
            self.log(f"    professional mode error: {e}")
            return False

    def _delete_copyright_items(self):
        """Account status / support inbox mein copyright wale items ke 'Delete/Remove' dabao."""
        removed = 0
        for u in ["https://www.facebook.com/professional_dashboard/account_status/",
                  "https://www.facebook.com/support/"]:
            try:
                self.page.goto(u, wait_until="domcontentloaded"); time.sleep(6)
                for _ in range(10):
                    items = self.page.query_selector_all("[role='main'] [role='listitem'], [role='main'] [role='row'], "
                                                         "[role='main'] div[role='button']")
                    hit = None
                    for it in items:
                        try:
                            t = (it.inner_text() or "").lower()
                        except Exception:
                            continue
                        if "copyright" in t or "intellectual property" in t:
                            hit = it; break
                    if not hit:
                        break
                    hit.click(); time.sleep(4)
                    if self._click_button(["Delete video", "Remove video", "Delete post", "Remove post",
                                           "Delete", "Remove"]):
                        time.sleep(2)
                        self._click_button(["Delete", "Remove", "Confirm"])
                        removed += 1
                        self.log(f"    copyright item removed ({removed})")
                        time.sleep(4)
                        self.page.goto(u, wait_until="domcontentloaded"); time.sleep(5)
                    else:
                        break
            except Exception as e:
                self.log(f"    copyright cleanup: {e}")
        return removed

    def act_checkin_post(self, caption="", photo_folder="", with_photo=False, do_checkin=True):
        """Profile par post: CHECK-IN (IP wali city) aur/ya photo + caption.
        do_checkin=False -> sirf photo post (folder se, har ID ki alag photo)."""
        acc = get_account(self.current_acc["id"]) or self.current_acc
        info = acc.get("ip_info") or {}
        city = info.get("city") or "" if do_checkin else ""
        place = ", ".join(x for x in (city, info.get("regionName", "")) if x) if do_checkin else ""
        photo = ""
        if with_photo and photo_folder and os.path.isdir(photo_folder):
            photo = self._pick_media(photo_folder, "checkin_photos", "photo")
        if not do_checkin and not photo:
            self.log(T("  • Photo post: no unused photo in folder — skipped.", "  • Photo post: folder mein nayi photo nahi — skip."))
            return False
        self.log(T((f"  • Check-in post: {place or '(no IP city)'}" if do_checkin else "  • Photo post") + (f" + photo {os.path.basename(photo)}" if photo else ""),
                   (f"  • Check-in post: {place or '(IP city nahi)'}" if do_checkin else "  • Photo post") + (f" + photo {os.path.basename(photo)}" if photo else "")))
        try:
            self._nav("home"); time.sleep(random.uniform(4, 7))
            if not self._click_button(["What's on your mind", "Create a post", "Create post"]):
                el = self.page.query_selector("div[role='button']:has-text(\"What's on your mind\")")
                if el:
                    el.click()
            time.sleep(3)
            if photo:
                fi = self._find_file_input(media="image")
                if not fi:
                    self._click_button(["Photo/video", "Photo/Video"]); time.sleep(2)
                    fi = self._find_file_input(media="image")
                if fi:
                    fi.set_input_files(photo); time.sleep(6)
            if place:
                if self._click_button(["Check in", "Check In", "Check-in"]):
                    time.sleep(3)
                    box = self.page.query_selector("input[placeholder*='Where are you' i]") or \
                        self.page.query_selector("input[aria-label*='Search' i][type='search']") or \
                        self.page.query_selector("[role='dialog'] input[type='text']")
                    if box:
                        box.click(); box.type(city or place, delay=60); time.sleep(4)
                        opt = self.page.query_selector("[role='dialog'] [role='listbox'] [role='option'], "
                                                       "[role='dialog'] ul [role='button']")
                        if opt:
                            opt.click(); time.sleep(3)
                        else:
                            self.page.keyboard.press("Enter"); time.sleep(3)
            if caption:
                self._fill_caption(spin(caption))
            time.sleep(2)
            ok = self._click_button(["Post", "Share now", "Share"])
            time.sleep(6)
            if ok and photo:
                self._mark_done("checkin_photos", os.path.basename(photo))
            self.log(T(f"    check-in post {'done ✔' if ok else 'not confirmed'}",
                       f"    check-in post {'ho gayi ✔' if ok else 'confirm nahi hui'}"))
            return ok
        except Exception as e:
            self.log(f"    check-in error: {e}")
            return False

    def _pick_media(self, folder, done_key, kind):
        """Folder se ek file jo is ID par pehle use nahi hui (har ID ki alag). kind: photo / video / any."""
        if not folder or not os.path.isdir(folder):
            return ""
        used = self._done_set(done_key)
        files = []
        for k in (("photo", "video") if kind == "any" else (kind,)):
            try:
                files += list_media_in_folder(folder, k)
            except Exception:
                pass
        fresh = [p for p in files if os.path.basename(p) not in used]
        # doosri IDs ne jo abhi isi run mein li, woh bhi kam se kam chuno
        with _MEDIA_LOCK:
            pool = [p for p in fresh if p not in _MEDIA_TAKEN] or fresh
            if not pool:
                return ""
            p = random.choice(pool)
            _MEDIA_TAKEN.add(p)
        return p

    def act_create_story(self, folder, count=1):
        """Story lagao: folder se photo ya video (har ID ki alag)."""
        done = 0
        for _ in range(max(1, int(count or 1))):
            media = self._pick_media(folder, "story_media", "any")
            if not media:
                self.log(T("  • Story: no unused photo/video in folder — skipped.", "  • Story: folder mein nayi photo/video nahi — skip."))
                break
            self.log(T(f"  • Creating story: {os.path.basename(media)}", f"  • Story laga rahe hain: {os.path.basename(media)}"))
            try:
                self.page.goto("https://www.facebook.com/stories/create", wait_until="domcontentloaded")
                time.sleep(random.uniform(4, 6))
                fi = None
                for el in self.page.query_selector_all("input[type='file']"):
                    fi = el; break
                if not fi:
                    self._click_button(["Create a photo story", "Create a Photo Story", "Create photo story"])
                    time.sleep(2)
                    fi = self.page.query_selector("input[type='file']")
                if not fi:
                    self.log(T("    story upload box not found", "    story upload box nahi mila")); break
                fi.set_input_files(media)
                time.sleep(random.uniform(8, 14) if media.lower().endswith((".mp4", ".mov", ".m4v", ".webm")) else random.uniform(5, 8))
                ok = self._click_button(["Share to story", "Share to Story", "Share"])
                time.sleep(random.uniform(5, 8))
                if ok:
                    done += 1
                    self._mark_done("story_media", os.path.basename(media))
                    self.log(T("    story shared ✔", "    story lag gayi ✔"))
            except Exception as e:
                self.log(f"    story error: {e}")
        return done

    def run_active(self, s, stop_check=None):
        """Warm-up / Active. Har ID (har browser) ka apna RANDOM plan:
        kaamon ki tarteeb, ginti aur waqfe sab alag — taake insaan jaisa lage."""
        acc = self.current_acc
        rnd = random.Random(f"{acc.get('id')}-{time.time()}")
        loops = max(1, int(s.get("loops", 1)))

        def cnt(c):
            c = max(1, int(c or 1))
            return rnd.randint(max(1, int(round(c * 0.6))), c)

        for lp in range(1, loops + 1):
            if stop_check and stop_check():
                break
            tasks = []
            if s.get("check_notification", {}).get("on"):
                tasks.append(("notifications", lambda: self.act_check_notification(cnt(s["check_notification"].get("count", 1)))))
            if s.get("scroll_feed", {}).get("on"):
                tasks.append(("feed", lambda: self.act_scroll_feed(self._rng(s["scroll_feed"]))))
            if s.get("check_stories", {}).get("on"):
                tasks.append(("stories", lambda: self.act_check_stories(self._rng(s["check_stories"]))))
            if s.get("scroll_videos", {}).get("on"):
                tasks.append(("videos", lambda: self.act_scroll_videos(self._rng(s["scroll_videos"]))))
            if s.get("scroll_reels", {}).get("on"):
                tasks.append(("reels", lambda: self.act_scroll_reels(self._rng(s["scroll_reels"]))))
            if s.get("confirm_friends", {}).get("on"):
                tasks.append(("friends", lambda: self.act_confirm_friends(cnt(s["confirm_friends"].get("count", 1)))))
            if s.get("reaction", {}).get("on"):
                tasks.append(("likes", lambda: self.act_reactions(cnt(s["reaction"].get("count", 1)))))
            if s.get("check_message", {}).get("on"):
                cm = s["check_message"]
                tasks.append(("messages", lambda: self.act_messages_reply(cnt(cm.get("count", 1)), cm.get("reply_on", False),
                                                                           cm.get("reply_text", ""))))
            if s.get("comments", {}).get("on"):
                tasks.append(("comments", lambda: self.act_comments(cnt(s["comments"].get("count", 1)),
                                                                     s["comments"].get("text", ""))))
            if s.get("detect_info", {}).get("on", True) and lp == 1:
                tasks.append(("profile info", self.detect_profile_info))
            if s.get("primary_loc", {}).get("on") and lp == 1:
                tasks.append(("primary location", self.check_primary_location))
            if s.get("pro_mode", {}).get("on") and not (get_account(acc["id"]) or {}).get("pro_mode"):
                tasks.append(("professional mode", self.act_professional_mode))
            if s.get("checkin", {}).get("on"):
                ck = s["checkin"]
                tasks.append(("check-in post", lambda: self.act_checkin_post(ck.get("caption", ""),
                                                                            ck.get("folder", "") or s.get("photo_post", {}).get("folder", ""),
                                                                            bool(ck.get("photo")))))
            if s.get("photo_post", {}).get("on"):
                pp = s["photo_post"]
                tasks.append(("photo post", lambda: self.act_checkin_post(pp.get("caption", ""), pp.get("folder", ""),
                                                                         True, do_checkin=False)))
            if s.get("story", {}).get("on"):
                sy = s["story"]
                tasks.append(("story", lambda: self.act_create_story(sy.get("folder", ""), cnt(sy.get("count", 1)))))
            if s.get("add_friends", {}).get("on"):
                tasks.append(("add friends", lambda: self.act_add_friends_suggest(cnt(s["add_friends"].get("count", 3)))))
            if s.get("add_friends_uid", {}).get("on"):
                fu = s["add_friends_uid"]
                tasks.append(("add friends (UID list)", lambda: self.act_add_friends_uid(cnt(fu.get("count", 3)), fu.get("src", ""))))
            if s.get("follow_pages", {}).get("on"):
                fp = s["follow_pages"]
                tasks.append(("follow pages", lambda: self.act_follow_pages(cnt(fp.get("count", 3)), fp.get("src", ""))))
            if s.get("group_join", {}).get("on"):
                tasks.append(("join groups", lambda: self.join_trending_groups(cnt(s["group_join"].get("count", 5)))))
            gp_text = s.get("group_post", {}).get("text", "") if s.get("group_post", {}).get("on") else ""
            ga = s.get("group_active", {})
            if ga.get("on") or gp_text:
                tasks.append(("groups", lambda: self.groups_active(ga.get("min", 3) if ga.get("on") else 0,
                                                                   ga.get("like", True), ga.get("comment", True), gp_text)))
            rnd.shuffle(tasks)
            # page status / copyright / monetization — loop ke aakhir mein (har loop nahi, sirf pehle)
            if lp == 1 and s.get("page_status", {}).get("on"):
                tasks.append(("page status", lambda: self.check_page_status(
                    bool(s["page_status"].get("delete_copyright")))))
            # jo tick kiya woh har dafa hoga (koi kaam skip nahi) — sirf tarteeb random
            self.log(T(f"[{acc['name']}] Active loop {lp}/{loops} — plan: " + " → ".join(t[0] for t in tasks),
                       f"[{acc['name']}] Active loop {lp}/{loops} — plan: " + " → ".join(t[0] for t in tasks)))
            for name, fn in tasks:
                if stop_check and stop_check():
                    break
                try:
                    fn()
                except Exception as e:
                    self.log(f"[{acc['name']}] {name}: {e}")
                # insaani waqfa (har ID ka alag) — chhota, taake kaam jaldi ho
                time.sleep(rnd.uniform(3, 10))
        self.log(T(f"[{acc['name']}] Active session complete.", f"[{acc['name']}] Active session mukammal."))

    @staticmethod
    def _rng(d):
        lo = float(d.get("min", 1))
        hi = float(d.get("max", lo))
        if hi < lo:
            hi = lo
        return random.uniform(lo, hi)

    # ---------- Warm-up / active rakhna (post gap scroll) ----------
    # ---------- Bomb Prime jaise extra features (web) ----------
    def _norm_target(self, s):
        s = s.strip()
        if not s:
            return ""
        if s.startswith("http"):
            return s
        if s.isdigit():
            return f"https://www.facebook.com/{s}"
        return f"https://www.facebook.com/{s}"

    def join_group(self, group):
        acc = self.current_acc
        url = self._norm_target(group)
        self.log(f"[{acc['name']}] Join group: {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            time.sleep(4)
            ok = self._click_button(["Join group", "Join Group", "Join", "Request to join"])
            time.sleep(2)
            return ok
        except Exception as e:
            self.log(f"  join error: {e}")
            return False

    def post_to_group(self, group, text, image_path=None):
        acc = self.current_acc
        url = self._norm_target(group)
        self.log(f"[{acc['name']}] Post to group: {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            time.sleep(4)
            # composer kholo
            self._click_button(["Write something", "Discussion", "Anything on your mind", "Create post", "Write"])
            time.sleep(2)
            if image_path and os.path.exists(image_path):
                fi = self._find_file_input(media="image")
                if fi:
                    fi.set_input_files(image_path); time.sleep(5)
            self._fill_caption(spin(text))
            ok = self._click_button(["Post", "Publish", "Share"])
            time.sleep(3)
            return ok
        except Exception as e:
            self.log(f"  group post error: {e}")
            return False

    def comment_on_post(self, post, text):
        acc = self.current_acc
        url = self._norm_target(post)
        self.log(f"[{acc['name']}] Comment on: {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            time.sleep(4)
            box = self.page.query_selector("div[aria-label*='Comment' i][contenteditable='true']") \
                or self.page.query_selector("div[contenteditable='true'][role='textbox']")
            if not box:
                self.log("  comment box not found")
                return False
            box.click(); time.sleep(1)
            box.type(spin(text), delay=20)
            self.page.keyboard.press("Enter")
            time.sleep(2)
            return True
        except Exception as e:
            self.log(f"  comment error: {e}")
            return False

    def react_on_post(self, post, reaction="Like"):
        acc = self.current_acc
        url = self._norm_target(post)
        self.log(f"[{acc['name']}] React on: {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            time.sleep(4)
            return self._click_button([reaction, "Like"])
        except Exception as e:
            self.log(f"  react error: {e}")
            return False

    # ---------- AI: relevant comment + hate/nudity check ----------
    def ai_decide(self, post_text):
        """Return (skip, comment). skip=True agar hate/nudity/unsafe.
        AI key ho to post parh kar relevant comment; warna basic."""
        s = load_settings()
        key = (s.get("ai_key") or "").strip()
        text = (post_text or "").strip()[:1200]
        # Basic bina-AI fallback: keyword hate skip + generic comment
        bad = ["nude", "nudity", "sex", "porn", "xxx", "kill", "hate", "racist", "abuse",
               "fuck", "bitch", "nigger", "terrorist", "gaali"]
        if not key:
            if any(b in text.lower() for b in bad):
                return True, ""
            generic = ["Nice post!", "Great share 👍", "Love this!", "Amazing 🔥", "Well said!"]
            return False, random.choice(generic)
        # AI (OpenAI-compatible)
        try:
            import urllib.request, json as _json
            prompt = ("You are a friendly Facebook commenter. Read the post. "
                      "If it contains hate speech, nudity, sexual, violent or abusive content, "
                      "respond EXACTLY with SKIP. Otherwise write ONE short, positive, relevant "
                      "comment (max 12 words, you may add 1 emoji). Post:\n\n" + text)
            body = _json.dumps({
                "model": s.get("ai_model", "llama-3.1-8b-instant"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7, "max_tokens": 40,
            }).encode()
            req = urllib.request.Request(
                s.get("ai_base", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            r = urllib.request.urlopen(req, timeout=30)
            out = _json.loads(r.read().decode())
            msg = out["choices"][0]["message"]["content"].strip()
            if msg.upper().startswith("SKIP") or "SKIP" == msg.upper():
                return True, ""
            return False, msg.strip().strip('"')
        except Exception as e:
            self.log(f"  AI error: {e} (using generic)")
            return (any(b in text.lower() for b in bad)), "Nice post! 👍"

    # ---------- Simple auto groups: join trending + active + optional post ----------
    def join_trending_groups(self, count=5):
        acc = self.current_acc
        self.log(f"[{acc['name']}] Joining trending groups (up to {count})...")
        joined = 0
        try:
            for disc in ["https://www.facebook.com/groups/discover/",
                         "https://www.facebook.com/groups/"]:
                self.page.goto(disc, wait_until="domcontentloaded")
                time.sleep(5)
                for _ in range(count * 3):
                    if joined >= count:
                        break
                    btn = self.page.query_selector("div[aria-label='Join'][role='button']") \
                        or self.page.query_selector("div[aria-label='Join group'][role='button']")
                    if btn:
                        try:
                            btn.click(); joined += 1
                            self.log(f"    joined {joined}/{count}")
                            time.sleep(random.uniform(3, 6))
                        except Exception:
                            pass
                    self.page.mouse.wheel(0, random.randint(700, 1100))
                    time.sleep(random.uniform(2, 4))
                if joined >= count:
                    break
        except Exception as e:
            self.log(f"  join trending error: {e}")
        return joined

    def groups_active(self, minutes=3, do_like=True, do_comment=True, post_text=""):
        """Groups feed me scroll + relevant like/comment (hate/nudity skip). Post optional."""
        acc = self.current_acc
        self.log(f"[{acc['name']}] Active in groups ~{minutes} min...")
        try:
            self.page.goto("https://www.facebook.com/groups/feed/", wait_until="domcontentloaded")
            time.sleep(5)
        except Exception:
            pass
        end = time.time() + minutes * 60
        done_c = 0
        while time.time() < end:
            # aik post ka text parho (visible article)
            post_text_read = ""
            try:
                art = self.page.query_selector("div[role='article']")
                if art:
                    post_text_read = (art.inner_text() or "")[:800]
            except Exception:
                pass
            skip, comment = self.ai_decide(post_text_read)
            if skip:
                self.log("    skipped (hate/nudity/unsafe)")
            else:
                if do_like:
                    b = self.page.query_selector("div[aria-label='Like'][role='button']")
                    if b:
                        try: b.click()
                        except Exception: pass
                if do_comment and comment and done_c < 20:
                    box = self.page.query_selector("div[aria-label*='Comment' i][contenteditable='true']")
                    if box:
                        try:
                            box.click(); time.sleep(0.6)
                            box.type(comment, delay=20)
                            self.page.keyboard.press("Enter")
                            done_c += 1
                            self.log(f"    commented: {comment[:30]}")
                        except Exception:
                            pass
            self.page.mouse.wheel(0, random.randint(800, 1300))
            time.sleep(random.uniform(3, 7))
        # optional: group me post
        if post_text:
            try:
                self.page.goto("https://www.facebook.com/groups/feed/", wait_until="domcontentloaded")
                time.sleep(4)
                self._click_button(["Write something", "Discussion", "Create post", "Anything on your mind"])
                time.sleep(2)
                self._fill_caption(spin(post_text))
                self._click_button(["Post", "Publish", "Share"])
                self.log("    posted in group.")
            except Exception as e:
                self.log(f"  group post error: {e}")
        return True

    def warm_scroll(self, seconds):
        """Diye gaye waqt tak feed par neeche-upar scroll kar ke ID ko active dikhaye."""
        acc = self.current_acc
        self.log(T(f"[{acc['name']}] {seconds} sec: scrolling feed to stay active...", f"[{acc['name']}] {seconds} sec: feed scroll kar ke active rakh rahe hain..."))
        end = time.time() + seconds
        try:
            self.page.goto(FB_HOME, wait_until="domcontentloaded")
            time.sleep(2)
        except Exception:
            pass
        while time.time() < end:
            try:
                # zyada tar neeche, kabhi kabhi upar — insani lagta hai
                delta = random.randint(300, 900)
                if random.random() < 0.25:
                    delta = -random.randint(200, 600)
                self.page.mouse.wheel(0, delta)
            except Exception:
                pass
            # chhoti random thehrav
            time.sleep(min(random.uniform(3, 8), max(0, end - time.time())))
        self.log(T(f"[{acc['name']}] Scrolling done.", f"[{acc['name']}] Scroll khatam."))

    def _wait_after_upload(self, default=8):
        if self.upload_wait:
            lo, hi = self.upload_wait
            time.sleep(random.uniform(lo, hi))
        else:
            time.sleep(default)

    # ---------- Pages: auto-detect ----------
    def detect_pages(self):
        """Is ID ke saare Facebook pages khud dhoondo. Return [{name,url}]."""
        acc = self.current_acc
        self.log(T(f"[{acc['name']}] Detecting your Facebook pages...",
                   f"[{acc['name']}] Aapke Facebook pages dhoond rahe hain..."))
        if not self._fb_uid():
            self.log(T(f"[{acc['name']}] Not logged in — cannot detect pages.",
                       f"[{acc['name']}] Login nahi — pages nahi dhoond sakte."))
            return []
        import re as _re
        cands = {}            # url -> [naam ke umeedwar]
        bad_url = ("/pages/", "/recover", "/r.php", "/reg/", "/signup", "/login", "/groups", "/events", "/watch",
                   "/marketplace", "/gaming", "/settings", "/help", "/privacy", "/policies", "/notifications",
                   "/messages", "/friends", "/bookmarks", "/saved", "/reel", "/stories", "/ads", "/ad_center",
                   "adsmanager", "/business", "/latest", "/memories", "/hashtag", "/photo", "/search",
                   "/professional_dashboard", "/insights", "/boost", "/promote", "/create", "/inbox", "/l.php",
                   "/sharer", "/dialog", "/composer", "/videos", "/live", "/meta", "/policy")
        junk = ("see all", "create new page", "create", "invites", "your pages", "pages", "discover", "liked pages",
                "followed pages", "promote", "create ad", "boost", "boost post", "messages", "notifications",
                "insights", "manage", "switch", "switch now", "posts", "post", "more", "verified", "edit",
                "ad center", "meta business suite", "professional dashboard", "followers", "likes")

        def _clean(t):
            t = (t or "").split("\n")[0].strip()
            t = _re.sub(r"^(profile picture (for|of)\s+)", "", t, flags=_re.I)
            t = _re.sub(r"('s)?\s*profile picture$", "", t, flags=_re.I).strip()
            t = t.strip(" ·•✓✔")
            if not t or len(t) > 80 or t.lower() in junk or _re.fullmatch(r"[\d\s.,kKmM+]+", t):
                return ""
            if any(w in t.lower() for w in ("promote", "create ad", "boost", "ad center")):
                return ""
            return t

        for url in ["https://www.facebook.com/pages/?category=your_pages",
                    "https://www.facebook.com/bookmarks/pages/"]:
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                time.sleep(5)
                for _ in range(3):
                    self.page.mouse.wheel(0, 900); time.sleep(1.5)
                links = self.page.evaluate("""() => {
                    const root = document.querySelector('[role="main"]') || document.body;
                    return Array.from(root.querySelectorAll('a[href]')).map(a => [
                        a.href, (a.innerText || '').trim(), (a.getAttribute('aria-label') || '').trim(),
                        (a.querySelector('svg image, img') && a.innerText.trim() === '') ? 1 : 0]);
                }""")
                for href, text, aria, is_pic in links:
                    if not href or "facebook.com" not in href:
                        continue
                    if "profile.php" in href:
                        m = _re.search(r"[?&]id=(\d+)", href)
                        if not m:
                            continue
                        h = "https://www.facebook.com/profile.php?id=" + m.group(1)
                    else:
                        h = href.split("?")[0].split("#")[0].rstrip("/")
                        path = h.split("facebook.com", 1)[-1].strip("/")
                        if not path or "/" in path:      # page ka link ek hi hissa hota hai (facebook.com/NaamPage)
                            continue
                        if any(b in "/" + path.lower() for b in bad_url):
                            continue
                    if any(b in href.lower() for b in ("ad_center", "adsmanager", "/promote", "boost")):
                        continue
                    for t in (text, aria):
                        c = _clean(t)
                        if c:
                            cands.setdefault(h, []).append((1 if t is text else 0, c))
            except Exception as e:
                self.log(f"  page detect error: {e}")
            if cands:
                break
        found = {}
        for h, lst in cands.items():
            # text wala naam behtar (aria 'Profile picture for X' se bhi X hi aata hai)
            lst.sort(key=lambda x: (-x[0], len(x[1])))
            found[h] = lst[0][1]
        pages = [{"name": n, "url": u} for u, n in found.items()]
        self.log(T(f"[{acc['name']}] Found {len(pages)} page(s).",
                   f"[{acc['name']}] {len(pages)} page mile."))
        return pages

    # ---------- Navigation ----------
    def _go_to_page(self, page_name=None):
        """Target page kholo aur us page par switch karo (taake post page ki taraf se ho)."""
        tgt = getattr(self, "target_page", None) or {}
        url = tgt.get("url") or ""
        name = tgt.get("name") or page_name or ""
        if not url and name:
            url = name if name.startswith("http") else ""
        if not url:
            self.page.goto(FB_HOME, wait_until="domcontentloaded")
            time.sleep(2)
            return
        self.log(T(f"Opening page: {name or url}", f"Page khol rahe hain: {name or url}"))
        self.page.goto(url, wait_until="domcontentloaded")
        time.sleep(4)
        # Page par switch (admin ke taur par)
        if self._click_button(["Switch now", "Switch Now", "Switch", "Switch profile"]):
            time.sleep(3)
            self._click_button(["Switch", "Switch now", "Continue"])
            time.sleep(4)
            self.page.goto(url, wait_until="domcontentloaded")
            time.sleep(4)

    # ---------- Public upload methods ----------
    def upload_reel(self, video_path, caption):
        return self._upload_video(video_path, caption, is_reel=True)

    def upload_video(self, video_path, caption):
        return self._upload_video(video_path, caption, is_reel=False)

    def upload_photo(self, image_path, caption):
        acc = self.current_acc
        self.log(T(f"[{acc['name']}] Photo upload started: {image_path}", f"[{acc['name']}] Photo upload shuru: {image_path}"))
        try:
            self._go_to_page(acc.get("page_name", ""))
            time.sleep(3)
            self._click_button(["Photo/video", "Photo/Video", "Create post", "What's on your mind"])
            time.sleep(2)
            file_input = self._find_file_input(media="image")
            if not file_input:
                self._click_button(["Photo/video", "Photo/Video", "Add photos/videos"])
                time.sleep(2)
                file_input = self._find_file_input(media="image")
            if not file_input:
                self.log(T(f"[{acc['name']}] Photo box not found. Choose the file in the browser, then Resume.", f"[{acc['name']}] Photo box nahi mila. Browser mein khud choose karein, phir Resume."))
                return False
            file_input.set_input_files(image_path)
            self.log(T(f"[{acc['name']}] Image selected, processing...", f"[{acc['name']}] Tasveer choose ho gayi, process ho rahi hai..."))
            self._wait_after_upload(5)
            self._fill_caption(caption)
            if not self._click_button(["Post", "Publish", "Share"]):
                self._click_through_publish()
            self.log(T(f"[{acc['name']}] Photo post command sent. Check the browser to confirm.", f"[{acc['name']}] Photo post command bhej di. Browser mein confirm dekhein."))
            return True
        except Exception as e:
            self.log(f"[{acc['name']}] Error: {e}")
            return False

    def _upload_video(self, video_path, caption, is_reel):
        acc = self.current_acc
        kind = "Reel" if is_reel else "Video"
        self.log(T(f"[{acc['name']}] {kind} upload started: {video_path}", f"[{acc['name']}] {kind} upload shuru: {video_path}"))
        try:
            if is_reel:
                if getattr(self, "target_page", None):
                    self._go_to_page()
                self.page.goto("https://www.facebook.com/reels/create/", wait_until="domcontentloaded")
            else:
                self._go_to_page(acc.get("page_name", ""))
            time.sleep(4)
            file_input = self._find_file_input(media="video")
            if not file_input:
                self.log(T(f"[{acc['name']}] Upload box not found. Choose the file in the browser, then Resume.", f"[{acc['name']}] Upload box nahi mila. Browser mein khud choose karein, phir Resume."))
                return False
            file_input.set_input_files(video_path)
            self.log(T(f"[{acc['name']}] Video selected, processing...", f"[{acc['name']}] Video choose ho gayi, process ho rahi hai..."))
            self._wait_after_upload(8)
            filled = self._fill_caption(caption)
            if not self._click_through_publish(caption if not filled else ""):
                self.last_error = T("Publish button not found", "Publish button nahi mila")
                self.last_post_state = "failed"
                return False
            st = self._verify_posted()
            self.last_post_state = st
            if st == "ok":
                self.log(T(f"[{acc['name']}] {kind} PUBLISHED ✔", f"[{acc['name']}] {kind} PUBLISH ho gayi ✔"))
                return True
            self.last_error = {"scheduled": T("Facebook SCHEDULED it instead of publishing", "Facebook ne publish ki jagah SCHEDULE kar di"),
                               "failed": T("Facebook showed an error", "Facebook ne error diya"),
                               }.get(st, T("Not confirmed — publish message not seen", "Confirm nahi hui — publish ka message nahi aaya"))
            self.log(f"[{acc['name']}] {kind}: {self.last_error}")
            return False
        except Exception as e:
            self.log(f"[{acc['name']}] Error: {e}")
            return False

    # ---------- Helpers ----------
    def _find_file_input(self, media="video"):
        if media == "image":
            sels = ["input[type='file'][accept*='image']", "input[type='file']"]
        else:
            sels = ["input[type='file'][accept*='video']", "input[type='file']"]
        for sel in sels:
            el = self.page.query_selector(sel)
            if el:
                return el
        return None

    def _fill_caption(self, caption):
        if not caption:
            return True
        selectors = [
            "div[aria-label*='description' i][contenteditable='true']",
            "div[aria-label*='caption' i][contenteditable='true']",
            "div[aria-label*='Describe your reel' i][contenteditable='true']",
            "div[contenteditable='true'][role='textbox']",
        ]
        for sel in selectors:
            for el in self.page.query_selector_all(sel):
                try:
                    if not el.is_visible():
                        continue
                    if "comment" in (el.get_attribute("aria-label") or "").lower():
                        continue
                    el.click()
                    time.sleep(0.5)
                    el.type(caption, delay=15)
                    self.log(T("Caption/hashtags written.", "Caption/hashtags likh diye."))
                    return True
                except Exception:
                    continue
        return False

    def _click_exact(self, labels):
        """Sirf usi naam ka button (poora naam) — 'Schedule' wale kabhi nahi."""
        for label in labels:
            lab = label.replace("'", "\\'")
            for sel in (f"[role='button'][aria-label='{lab}']", f"button:text-is('{lab}')",
                        f"[role='button']:text-is('{lab}')", f"div[role='button'] span:text-is('{lab}')"):
                try:
                    els = self.page.query_selector_all(sel)
                except Exception:
                    continue
                for el in reversed(els):
                    try:
                        if not el.is_visible():
                            continue
                        if "schedul" in (el.inner_text() or "").lower():
                            continue
                        if (el.get_attribute("aria-disabled") or "") == "true":
                            continue
                        el.click(timeout=4000)
                        self.log(T(f"Clicked '{label}'.", f"'{label}' dabaya."))
                        time.sleep(2)
                        return True
                    except Exception:
                        continue
        return False

    def _ensure_publish_now(self):
        """Agar 'Scheduling options' mein schedule chuna ho to 'Publish now / Share now' chuno."""
        for lbl in ("Publish now", "Share now", "Post now", "Now"):
            try:
                for el in self.page.query_selector_all(f"[role='radio']:has-text('{lbl}'), label:has-text('{lbl}'), "
                                                       f"[role='menuitemradio']:has-text('{lbl}')"):
                    if el.is_visible() and (el.get_attribute("aria-checked") or "") != "true":
                        el.click(timeout=3000); time.sleep(1)
                        self.log(T(f"Selected '{lbl}' (not schedule).", f"'{lbl}' chuna (schedule nahi)."))
                        return
            except Exception:
                continue

    def _click_through_publish(self, caption=""):
        for _ in range(7):
            if caption and self._fill_caption(caption):
                caption = ""
            self._ensure_publish_now()
            if self._click_exact(["Publish", "Share", "Post", "Share now", "Publish now", "Post now"]):
                return True
            if not self._click_exact(["Next"]):
                break
            time.sleep(2.5)
        self._ensure_publish_now()
        return self._click_exact(["Publish", "Share", "Post", "Share now", "Publish now"])

    def _verify_posted(self, timeout=150):
        """Publish ke baad pakka karo: 'ok' / 'scheduled' / 'failed' / 'unconfirmed'."""
        ok_words = ("is now published", "has been published", "your reel is published", "reel published",
                    "is being processed", "we'll let you know when", "we'll notify you when", "your reel is live",
                    "your video is live", "post is live", "shared to your", "has been shared", "reel shared",
                    "your post is now published")
        sched_words = ("scheduled for", "your post is scheduled", "reel is scheduled", "will be published on",
                       "has been scheduled")
        err_words = ("something went wrong", "couldn't upload", "couldn't post", "upload failed",
                     "couldn't publish", "can't upload", "try again later", "video couldn't be")
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                txt = self._page_text().lower()
            except Exception:
                txt = ""
            if any(w in txt for w in sched_words):
                return "scheduled"
            if any(w in txt for w in err_words):
                return "failed"
            if any(w in txt for w in ok_words):
                return "ok"
            time.sleep(3)
        return "unconfirmed"

    def _click_button(self, labels):
        """Asli BUTTON dabao (heading/paragraph mein wahi lafz hon to unhe nahi).
        Har selector ke sab matches mein se sab se andar wala (aakhri) nazar aane wala button."""
        for label in labels:
            lab = label.replace("'", "\\'")
            for sel in [
                f"[role='button'][aria-label='{lab}']",
                f"button:text-is('{lab}')",
                f"[role='button']:text-is('{lab}')",
                f"button:has-text('{lab}')",
                f"[role='button']:has-text('{lab}')",
                f"span:text-is('{lab}')",
            ]:
                try:
                    els = self.page.query_selector_all(sel)
                except Exception:
                    continue
                for el in reversed(els):
                    try:
                        if not el.is_visible():
                            continue
                        el.click(timeout=4000)
                        self.log(T(f"Clicked '{label}'.", f"'{label}' dabaya."))
                        time.sleep(2)
                        return True
                    except Exception:
                        continue
        return False


# ============================================================
# GUI (Tkinter)
# ============================================================
class DurationRange:
    UNITS = {"sec": 1, "min": 60, "hour": 3600}
    def __init__(self, parent, label, dmin, dmax, unit="sec", width=30):
        row = ttk.Frame(parent); row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=width).pack(side="left")
        self.vmin = tk.StringVar(value=str(dmin)); self.vmax = tk.StringVar(value=str(dmax))
        tk.Spinbox(row, from_=0, to=100000, textvariable=self.vmin, width=6).pack(side="left")
        ttk.Label(row, text="to").pack(side="left", padx=3)
        tk.Spinbox(row, from_=0, to=100000, textvariable=self.vmax, width=6).pack(side="left")
        self.unit = tk.StringVar(value=unit)
        ttk.Combobox(row, textvariable=self.unit, values=["sec", "min", "hour"],
                     width=5, state="readonly").pack(side="left", padx=5)

    def seconds(self):
        f = self.UNITS.get(self.unit.get(), 1)
        def iv(s):
            try: return float(s)
            except Exception: return 0
        lo = int(iv(self.vmin.get()) * f); hi = int(iv(self.vmax.get()) * f)
        if hi < lo: hi = lo
        return (lo, hi)


class DurationSingle:
    UNITS = {"sec": 1, "min": 60, "hour": 3600}
    def __init__(self, parent, label, default, unit="min", width=30):
        row = ttk.Frame(parent); row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=width).pack(side="left")
        self.val = tk.StringVar(value=str(default))
        tk.Spinbox(row, from_=0, to=100000, textvariable=self.val, width=6).pack(side="left")
        self.unit = tk.StringVar(value=unit)
        ttk.Combobox(row, textvariable=self.unit, values=["sec", "min", "hour"],
                     width=5, state="readonly").pack(side="left", padx=5)

    def seconds(self):
        try: v = float(self.val.get())
        except Exception: v = 0
        return int(v * self.UNITS.get(self.unit.get(), 1))


class TimePicker:
    def __init__(self, parent, default_h=8, default_m=0, default_ap="PM"):
        self.frame = ttk.Frame(parent)
        self.h = tk.StringVar(value=str(default_h))
        self.m = tk.StringVar(value=f"{default_m:02d}")
        self.ap = tk.StringVar(value=default_ap)
        tk.Spinbox(self.frame, from_=1, to=12, textvariable=self.h, width=3).pack(side="left")
        ttk.Label(self.frame, text=":").pack(side="left")
        tk.Spinbox(self.frame, from_=0, to=59, textvariable=self.m, width=3, format="%02.0f").pack(side="left")
        ttk.Combobox(self.frame, textvariable=self.ap, values=["AM", "PM"], width=4, state="readonly").pack(side="left", padx=4)

    def pack(self, **k): self.frame.pack(**k)

    def hhmm24(self):
        try:
            h = int(self.h.get()); m = int(self.m.get()); ap = self.ap.get()
        except Exception:
            return "20:00"
        if ap == "PM" and h != 12: h += 12
        if ap == "AM" and h == 12: h = 0
        return f"{h:02d}:{m:02d}"


class AccountPanel:
    """Account table: checkbox select + Total + buttons (configurable)."""
    def __init__(self, parent, app, on_start=None, start_label="START",
                 show_startstop=True, show_delete=False, on_pick=None, show_pages=True, detail=False):
        self.app = app
        self.detail = detail
        self.filter_group = tk.StringVar(value="All")
        self.filter_text = tk.StringVar()
        self.show_pages = show_pages
        self.cur_acc_id = None
        self.on_start = on_start
        self.on_pick = on_pick
        self.checked = set()
        self.status = {}

        wrap = ttk.LabelFrame(parent, text=T("ACCOUNTS", "ACCOUNTS"))
        wrap.pack(side="right", fill="both", expand=True, padx=6, pady=6)

        head = ttk.Frame(wrap); head.pack(fill="x", padx=6, pady=4)
        self.lbl_total = ttk.Label(head, text="Total 0  |  Selected 0")
        self.lbl_total.pack(side="left", padx=6)
        ttk.Button(head, text="Select all", command=self._select_all).pack(side="left", padx=2)
        ttk.Button(head, text="Unselect all", command=self._unselect_all).pack(side="left", padx=2)
        if show_delete:
            ttk.Button(head, text="Delete selected", command=self.app._delete_checked).pack(side="left", padx=2)
        if detail:
            ttk.Button(head, text=T("✎ Edit", "✎ Edit"), style="Accent.TButton",
                       command=self._edit_selected).pack(side="left", padx=2)
        if detail:
            ttk.Label(head, text="  Group:").pack(side="left")
            self.group_cb = ttk.Combobox(head, textvariable=self.filter_group, width=12, state="readonly")
            self.group_cb.pack(side="left", padx=2)
            self.group_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            ttk.Label(head, text="  Search:").pack(side="left")
            se = ttk.Entry(head, textvariable=self.filter_text, width=16); se.pack(side="left", padx=2)
            se.bind("<KeyRelease>", lambda e: self.refresh())
            mb = ttk.Menubutton(head, text=T("Export ▾", "Export ▾"))
            em = tk.Menu(mb, tearoff=0)
            em.add_command(label=T("Export FULL (Excel/CSV) — ticked or all", "Export FULL (Excel/CSV)"),
                           command=lambda: self.app._export_accounts("full"))
            em.add_command(label="Export TXT  (email|pass|2fa|uid|name)", command=lambda: self.app._export_accounts("txt"))
            em.add_command(label="Export UID", command=lambda: self.app._export_accounts("uid"))
            mb["menu"] = em; mb.pack(side="right", padx=2)
        if on_start:
            ttk.Button(head, text=start_label, command=self._start).pack(side="right", padx=4)
            if show_startstop:
                ttk.Button(head, text="STOP", command=self.app._stop_all).pack(side="right", padx=2)

        k = ui_px(app) * 0.8
        if detail:
            cols = tuple(c for c, _, _ in ACCOUNT_FIELDS)
            widths = {c: w for c, _, w in ACCOUNT_FIELDS}
            heads = {c: h for c, h, _ in ACCOUNT_FIELDS}
        else:
            cols = ("no", "name", "email", "page", "mode", "status")
            widths = {"no": 50, "name": 130, "email": 210, "page": 380, "mode": 130, "status": 90}
            heads = {"no": "No", "name": "Account", "email": "Email",
                     "page": "Pages (ticked)", "mode": "IP city", "status": "Status"}
        self._pw = ttk.PanedWindow(wrap, orient="vertical")
        self._pw.pack(fill="both", expand=True, padx=6, pady=4)
        tf = ttk.Frame(self._pw)
        self._pw.add(tf, weight=3)
        self.tree = ttk.Treeview(tf, columns=cols, show="tree headings",
                                 height=(7 if show_pages else 14), selectmode="none")
        self.tree.column("#0", width=int(48 * k), minwidth=int(40 * k), anchor="center", stretch=False)
        self.tree.heading("#0", text="✔")
        for c in cols:
            self.tree.heading(c, text=heads[c], anchor="w")   # heading usi column ke upar seedha
            self.tree.column(c, width=int(widths[c] * k), minwidth=int(40 * k), anchor="w",
                             stretch=(not detail and c == "page"))
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.grid_rowconfigure(0, weight=1); tf.grid_columnconfigure(0, weight=1)
        if detail:
            self.tree.bind("<Button-3>", self._row_menu)
            self.tree.bind("<Double-1>", lambda e: (lambda r: r and self.on_pick and get_account(r) and
                                                    self.on_pick(get_account(r)))(self.tree.identify_row(e.y)))
        self.tree.bind("<Button-1>", self._on_click)

        if show_pages:
            pb = ttk.LabelFrame(self._pw, text=T("PAGES OF CLICKED ID  (✔ = post on this page)  — drag the bar above to resize",
                                                 "CLICK KI GAYI ID KE PAGES  (✔ = is page par post)  — upar wali patti kheench kar bada karein"))
            self._pw.add(pb, weight=2)
            ph = ttk.Frame(pb); ph.pack(fill="x", padx=4, pady=2)
            self.lbl_pages = ttk.Label(ph, text=T("Click an ID row above to see its pages.",
                                                  "Upar kisi ID ki row par click karein, pages yahan aayenge."))
            self.lbl_pages.pack(side="left")
            ttk.Button(ph, text=T("Detect pages", "Pages dhoondo"), command=self._detect_cur).pack(side="right", padx=2)
            ttk.Button(ph, text=T("Untick all", "Sab untick"), command=lambda: self._tick_all_pages(False)).pack(side="right", padx=2)
            ttk.Button(ph, text=T("Tick all", "Sab tick"), command=lambda: self._tick_all_pages(True)).pack(side="right", padx=2)
            self.ptree = ttk.Treeview(pb, columns=("pname", "purl"), show="tree headings", height=3, selectmode="none")
            self.ptree.column("#0", width=46, anchor="center", stretch=False)
            self.ptree.heading("#0", text="Post", anchor="center")
            self.ptree.heading("pname", text=T("Page name", "Page ka naam"), anchor="w")
            self.ptree.heading("purl", text="Link", anchor="w")
            self.ptree.column("pname", width=280, minwidth=160, anchor="w", stretch=True)
            self.ptree.column("purl", width=380, minwidth=200, anchor="w", stretch=True)
            pys = ttk.Scrollbar(pb, orient="vertical", command=self.ptree.yview)
            self.ptree.configure(yscrollcommand=pys.set)
            pys.pack(side="right", fill="y")
            self.ptree.pack(fill="both", expand=True, padx=4, pady=2)
            # table aur pages ke beech aadha aadha
            self.tree.after(600, lambda: self._pw.sashpos(0, int(self._pw.winfo_height() * 0.5))
                            if self._pw.winfo_height() > 50 else None)
            self.ptree.bind("<Button-1>", self._on_page_click)

        self.app._panels.append(self)
        self.refresh()

    def _edit_selected(self):
        ids = [i for i in self.checked] or ([self.cur_acc_id] if self.cur_acc_id else [])
        if not ids:
            messagebox.showwarning(T("Edit", "Edit"), T("Click or tick one ID, then press Edit.",
                                                       "Kisi ID par click ya tick karein, phir Edit dabayein."))
            return
        a = get_account(ids[0])
        if a and self.on_pick:
            self.on_pick(a)

    def _row_menu(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        a = get_account(row)
        if not a:
            return
        m = tk.Menu(self.tree, tearoff=0)
        m.add_command(label=T("Open browser (this ID)", "Browser kholo (is ID ka)"), command=lambda: self.app._open_browser_for(a))
        m.add_command(label=T("Open browser — I'll log in myself (manual)", "Browser kholo — main khud login karunga (manual)"),
                      command=lambda: self.app._open_browser_manual(a))
        m.add_command(label=T("Edit", "Edit"), command=lambda: self.on_pick and self.on_pick(a))
        m.add_separator()
        m.add_command(label=T("Select", "Select"), command=lambda: (self.checked.add(row), self.refresh()))
        m.add_command(label=T("Unselect", "Unselect"), command=lambda: (self.checked.discard(row), self.refresh()))
        m.add_command(label=T("Set group for ticked…", "Ticked IDs ka group…"), command=self.app._set_group_ticked)
        m.add_separator()
        m.add_command(label="Export UID (ticked)", command=lambda: self.app._export_accounts("uid"))
        m.add_command(label="Export FULL (ticked)", command=lambda: self.app._export_accounts("full"))
        m.add_command(label=T("Delete ticked", "Ticked delete"), command=self.app._delete_checked)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        if self.detail:
            accs = load_accounts()
            groups = sorted({a.get("group", "") for a in accs if a.get("group")})
            try:
                self.group_cb.config(values=["All"] + groups)
            except Exception:
                pass
            fg, ft = self.filter_group.get(), self.filter_text.get().strip().lower()
            for i, a in enumerate(accs, 1):
                if fg not in ("All", "") and a.get("group", "") != fg:
                    continue
                if ft and ft not in " ".join(str(v) for v in (a.get("name"), a.get("email"), a.get("uid"),
                                                              a.get("profile"), a.get("status"))).lower():
                    continue
                img = self.app.img_check if a["id"] in self.checked else self.app.img_empty
                row = account_row(a, i)
                live = self.status.get(a["id"])
                if live:
                    row[[c for c, _, _ in ACCOUNT_FIELDS].index("status")] = live
                self.tree.insert("", "end", iid=a["id"], image=img, values=row)
            self.lbl_total.config(text=f"Total {len(accs)}  |  Selected {len(self.checked)}")
            if self.show_pages:
                self._render_pages()
            return
        for i, a in enumerate(load_accounts(), 1):
            _ii = a.get("ip_info") or {}
            mode = _ii.get("city") or ("VPN " + a.get("proxy", "")[3:] if a.get("proxy", "").lower().startswith("wg:")
                                       else ("proxy" if a.get("proxy") else "-"))   # sirf shehar (jaise New York)
            img = self.app.img_check if a["id"] in self.checked else self.app.img_empty
            self.tree.insert("", "end", iid=a["id"], image=img,
                             values=(i, a["name"], a["email"], self._pages_summary(a),
                                     mode, self.status.get(a["id"], "") or a.get("status", "")))
        accs = load_accounts()
        self.lbl_total.config(text=f"Total {len(accs)}  |  Selected {len(self.checked)}")
        if self.show_pages:
            self._render_pages()

    @staticmethod
    def _pages_summary(a):
        pg = a.get("pages") or []
        if pg:
            on = [p["name"] for p in pg if p.get("enabled")]
            return f"{len(on)}/{len(pg)} ✔" + ((": " + ", ".join(on)) if on else "")
        return a.get("page_name", "") or T("(auto)", "(auto)")

    def _render_pages(self):
        self.ptree.delete(*self.ptree.get_children())
        a = get_account(self.cur_acc_id) if self.cur_acc_id else None
        if not a:
            return
        pg = a.get("pages") or []
        if not pg:
            self.lbl_pages.config(text=T(f"{a['name']}: no pages yet — press 'Detect pages' (or LOGIN with detect on).",
                                         f"{a['name']}: abhi pages nahi — 'Pages dhoondo' dabayein (ya LOGIN detect ke sath)."))
            return
        on = sum(1 for p in pg if p.get("enabled"))
        self.lbl_pages.config(text=T(f"{a['name']}: {len(pg)} pages, {on} ticked",
                                     f"{a['name']}: {len(pg)} pages, {on} tick"))
        for i, p in enumerate(pg):
            img = self.app.img_check if p.get("enabled") else self.app.img_empty
            nm = p.get("name", "") + (f"   [{p['status']}]" if p.get("status") else "")
            self.ptree.insert("", "end", iid=str(i), image=img, values=(nm, p.get("url", "")))

    def show_pages_of(self, acc_id):
        if self.show_pages:
            self.cur_acc_id = acc_id
            self._render_pages()

    def _on_page_click(self, e):
        row = self.ptree.identify_row(e.y)
        a = get_account(self.cur_acc_id) if self.cur_acc_id else None
        if not row or not a:
            return
        pg = a.get("pages") or []
        i = int(row)
        if i >= len(pg):
            return
        p = pg[i]
        set_page_enabled(a["id"], p.get("url") or p.get("name"), not p.get("enabled"))
        self.app._refresh_all_panels()

    def _tick_all_pages(self, val):
        a = get_account(self.cur_acc_id) if self.cur_acc_id else None
        if not a or not a.get("pages"):
            return
        for p in a["pages"]:
            p["enabled"] = val
        update_account(a["id"], pages=a["pages"])
        self.app._refresh_all_panels()

    def _detect_cur(self):
        a = get_account(self.cur_acc_id) if self.cur_acc_id else None
        if not a:
            messagebox.showwarning(T("Wait", "Ruko"), T("Click an ID row first.", "Pehle kisi ID ki row par click karein."))
            return
        self.app._start_login_job([a], threads=1, detect=True)

    def _on_click(self, e):
        row = self.tree.identify_row(e.y); col = self.tree.identify_column(e.x)
        if not row:
            return
        self.show_pages_of(row)
        if col == "#0":
            self.checked.discard(row) if row in self.checked else self.checked.add(row)
            self.tree.item(row, image=(self.app.img_check if row in self.checked else self.app.img_empty))
            accs = load_accounts()
            self.lbl_total.config(text=f"Total {len(accs)}  |  Selected {len(self.checked)}")
        elif self.on_pick:
            for a in load_accounts():
                if a["id"] == row:
                    self.on_pick(a); break

    def _select_all(self):
        self.checked = {a["id"] for a in load_accounts()}; self.refresh()

    def _unselect_all(self):
        self.checked.clear(); self.refresh()

    def set_status(self, acc_id, text):
        self.status[acc_id] = text
        if text and text not in ("Login...", "LD"):
            try:
                update_account(acc_id, status=text, last_update=_now_str()[:16])
            except Exception:
                pass
        try:
            self.app.api_status_map[acc_id] = text
        except Exception:
            pass
        try:
            if self.tree.exists(acc_id):
                self.tree.set(acc_id, "status", text)
        except Exception:
            pass

    def checked_accounts(self):
        by_id = {a["id"]: a for a in load_accounts()}
        return [by_id[i] for i in self.checked if i in by_id]

    def _start(self):
        chosen = self.checked_accounts()
        if not chosen:
            messagebox.showwarning(T("Wait", "Ruko"), T("Select IDs with ✔ in the table.", "Table mein ✔ se IDs chunein."))
            return
        self.on_start(chosen)


# ============================================================
# API PAGES — official Facebook Graph API se page posting
# (browser / login / captcha / 2FA ki zaroorat nahi)
# ============================================================
API_FILE = os.path.join(DATA_DIR, "api_pages.json")
GRAPH = "https://graph.facebook.com"
GRAPH_VIDEO = "https://graph-video.facebook.com"
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def api_load():
    d = _load(API_FILE, {})
    d.setdefault("tokens", [])
    d.setdefault("pages", {})
    d.setdefault("history", [])
    d.setdefault("graph_version", "v22.0")
    return d


def api_save(d):
    _save(API_FILE, d)


_API_LOCK = threading.Lock()


def api_update_page(pid, **fields):
    with _API_LOCK:
        d = api_load()
        d["pages"].setdefault(pid, {}).update(fields)
        api_save(d)


def api_add_history(pid, name, kind, file, ok, info, prog=""):
    with _API_LOCK:
        d = api_load()
        d["history"].insert(0, {"time": time.strftime("%Y-%m-%d %H:%M"), "page_id": pid, "page": name,
                                "kind": kind, "file": file, "ok": bool(ok), "info": str(info)[:300], "prog": prog})
        d["history"] = d["history"][:300]
        api_save(d)


def _gv():
    return api_load().get("graph_version") or "v22.0"


def _http(url, data=None, headers=None, method=None, timeout=600):
    import urllib.request, urllib.error
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "ignore")
    try:
        j = json.loads(txt)
    except Exception:
        j = {"raw": txt}
    if isinstance(j, dict) and j.get("error"):
        err = j["error"]
        raise RuntimeError(f"{err.get('message')} (code {err.get('code')})")
    return j


def _form(fields):
    import urllib.parse
    return urllib.parse.urlencode(fields).encode()


def graph_get(path, token, **params):
    import urllib.parse
    params["access_token"] = token
    return _http(f"{GRAPH}/{_gv()}/{path}?{urllib.parse.urlencode(params)}")


def graph_post(path, token, base=None, **fields):
    fields["access_token"] = token
    return _http(f"{base or GRAPH}/{_gv()}/{path}", data=_form(fields), method="POST")


def _multipart(fields, file_field, file_path):
    import uuid, mimetypes
    b = "----fbu" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    mt = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    fn = os.path.basename(file_path).replace('"', "")
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{fn}"\r\n'
                 f'Content-Type: {mt}\r\n\r\n'.encode())
    with open(file_path, "rb") as f:
        parts.append(f.read())
    parts.append(f"\r\n--{b}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={b}"}


def api_fetch_pages(token):
    """Token se saare pages + page tokens. Return [{id,name,token}]"""
    out, url = [], None
    j = graph_get("me/accounts", token, fields="id,name,access_token", limit=100)
    while True:
        for p in j.get("data", []):
            if p.get("access_token"):
                out.append({"id": p["id"], "name": p.get("name", p["id"]), "token": p["access_token"]})
        url = (j.get("paging") or {}).get("next")
        if not url:
            break
        j = _http(url)
    return out


API_SCOPE = "pages_show_list,pages_read_engagement,pages_manage_posts,business_management"
API_REDIRECT = "https://www.facebook.com/connect/login_success.html"


def api_long_token(app_id, secret, short):
    """Chhota (1-2 ghante) user token -> lamba (~60 din)."""
    j = _http(f"{GRAPH}/{_gv()}/oauth/access_token?grant_type=fb_exchange_token&client_id={app_id}"
              f"&client_secret={secret}&fb_exchange_token={short}")
    return j.get("access_token") or short


def api_add_tester(app_id, secret, uid):
    """App ke Roles mein ID ko Tester banao (app token se)."""
    return graph_post(f"{app_id}/roles", f"{app_id}|{secret}", user=uid, role="testers")


def api_merge_pages(pages, owner_acc_id="", token=""):
    with _API_LOCK:
        d = api_load()
        for p in pages:
            cur = d["pages"].setdefault(p["id"], {"enabled": False, "kind": "reel", "times": "",
                                                  "days": list(range(7)), "folders": [],
                                                  "rotation": "order", "caption_mode": "auto"})
            cur["name"], cur["token"] = p["name"], p["token"]
            if owner_acc_id:
                cur["owner"] = owner_acc_id
        if token and token not in [t.get("token") for t in d["tokens"]]:
            d["tokens"].append({"token": token, "added": time.strftime("%Y-%m-%d"), "owner": owner_acc_id})
        api_save(d)


def api_publish(page, file_path, caption, kind):
    """Ek file page par post. Return post/video id."""
    pid, tok = page["id"], page["token"]
    if kind == "text":
        return graph_post(f"{pid}/feed", tok, message=caption).get("id")
    if kind == "photo":
        body, hdr = _multipart({"message": caption, "access_token": tok}, "source", file_path)
        return _http(f"{GRAPH}/{_gv()}/{pid}/photos", data=body, headers=hdr, method="POST").get("post_id") or "ok"
    if kind == "video":
        title = os.path.splitext(os.path.basename(file_path))[0][:250]
        body, hdr = _multipart({"description": caption, "title": title, "access_token": tok}, "source", file_path)
        return _http(f"{GRAPH_VIDEO}/{_gv()}/{pid}/videos", data=body, headers=hdr, method="POST").get("id")
    # reel (3 step)
    st = graph_post(f"{pid}/video_reels", tok, upload_phase="start")
    vid = st.get("video_id")
    up_url = st.get("upload_url") or f"https://rupload.facebook.com/video-upload/{_gv()}/{vid}"
    size = os.path.getsize(file_path)
    with open(file_path, "rb") as f:
        data = f.read()
    _http(up_url, data=data, method="POST",
          headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)})
    graph_post(f"{pid}/video_reels", tok, upload_phase="finish", video_id=vid,
               video_state="PUBLISHED", description=caption)
    return vid


def api_caption_for(page, file_path):
    """Caption: same-name .txt  >  captions file (random line)  >  filename  >  fallback. + page hashtags."""
    import random as _r
    cap = ""
    mode = page.get("caption_mode", "auto")
    side = os.path.splitext(file_path)[0] + ".txt" if file_path else ""
    if mode in ("auto", "txt") and file_path:
        sc = sidecar_caption(file_path)       # apni .txt ya folder ki captions.txt
        cap = sc or ""
    if not cap and mode in ("auto", "captions_file") and page.get("captions_file") and \
            os.path.isfile(page["captions_file"]):
        try:
            lines = [l.strip() for l in open(page["captions_file"], encoding="utf-8", errors="ignore") if l.strip()]
            if lines:
                cap = _r.choice(lines)
        except Exception:
            pass
    if not cap and mode in ("auto", "filename") and file_path:
        cap = os.path.splitext(os.path.basename(file_path))[0].replace("_", " ").strip()
    from_side = bool(cap)
    if not cap:
        cap = page.get("fallback", "")
    tags = "" if from_side else get_hashtags_for_page(page.get("name", ""))
    if tags:
        cap = (cap + "\n\n" + tags).strip()
    return cap


def api_fresh_files(page):
    """Is page ke folders ki wo files jo abhi post nahi huin (captions.txt ki tarteeb = nayi pehle)."""
    kind = page.get("kind", "reel")
    files = []
    fos = list(page.get("folders", []))
    pfo = page_media_folder(page.get("name", ""))
    if pfo and pfo not in fos:
        fos.append(pfo)
    for fo in fos:
        fl = list_media_in_folder(fo, "photo" if kind == "photo" else "video")
        order = list(captions_load(fo).keys())
        if order:
            pos = {n: i for i, n in enumerate(order)}
            fl.sort(key=lambda p: (pos.get(os.path.basename(p), 10 ** 6), os.path.basename(p)))
        files += fl
    key = "API:" + page["id"]
    return [f for f in files if not is_uploaded(key, file_hash(f) or f)]


def api_next_file(page):
    """Rotation ke hisaab se agli file jo is page par pehle post nahi hui."""
    import random as _r
    fresh = api_fresh_files(page)
    if not fresh:
        return None
    if page.get("rotation") == "random":
        return _r.choice(fresh)
    if page.get("rotation") == "newest":
        return max(fresh, key=lambda f: os.path.getmtime(f))
    return fresh[0]


def api_run_page(pid, log=print, file_path=None, prog=""):
    page = dict(api_load()["pages"].get(pid) or {})
    if not page:
        return False
    page["id"] = pid
    kind = page.get("kind", "reel")
    if kind != "text":
        file_path = file_path or api_next_file(page)
        if not file_path:
            log(T(f"[API] {page.get('name')}: no new file in folders.",
                  f"[API] {page.get('name')}: folders mein koi nayi file nahi."))
            api_update_page(pid, status="No new file")
            post_log_add(page.get("name"), False, prog, 0, T("No video left in folder", "Folder mein video nahi bachi"),
                         page.get("owner", ""), "API")
            return False
    cap = api_caption_for(page, file_path)
    prog_ok = prog
    try:      # prog = "pehle se ho chuki|kul" -> kamyabi par +1
        a_, b_ = prog.split("|"); prog_ok = f"{int(a_) + 1}|{b_}"
    except Exception:
        pass
    log(T(f"[API] {page.get('name')}: posting {kind} {os.path.basename(file_path or '')}...",
          f"[API] {page.get('name')}: {kind} post ho rahi hai {os.path.basename(file_path or '')}..."))
    api_update_page(pid, status="Posting...")
    try:
        rid = api_publish(page, file_path, cap, kind)
        if file_path:
            mark_uploaded("API:" + pid, file_hash(file_path) or file_path)
        api_add_history(pid, page.get("name"), kind, file_path or "", True, rid, prog_ok)
        api_update_page(pid, status="Posted " + time.strftime("%H:%M"))
        try:
            _left = len(api_fresh_files(page))
        except Exception:
            _left = ""
        post_log_add(page.get("name"), True, prog_ok, _left, "", page.get("owner", ""), "API")
        log(T(f"[API] {page.get('name')}: done ✔ ({rid})", f"[API] {page.get('name')}: ho gaya ✔ ({rid})"))
        if file_path and page.get("delete_after"):
            try:
                os.remove(file_path)
                captions_remove(os.path.dirname(file_path), os.path.basename(file_path))
            except Exception:
                pass
        return True
    except Exception as e:
        api_add_history(pid, page.get("name"), kind, file_path or "", False, e, prog)
        api_update_page(pid, status="Error")
        post_log_add(page.get("name"), False, prog, "", e, page.get("owner", ""), "API")
        log(T(f"[API] {page.get('name')}: FAILED — {e}", f"[API] {page.get('name')}: FAIL — {e}"))
        return False


def api_token_watch(log):
    """Din mein ek dafa: jis token ki muddat 7 din se kam reh gayi, us ID ko khud dobara CONNECT."""
    d = api_load()
    if not d.get("app_id") or not d.get("app_secret"):
        return
    due = []
    for t_ in d.get("tokens", []):
        exp = t_.get("expires")
        if not exp:
            try:     # Facebook se poochho kab khatam hoga
                j = _http(f"{GRAPH}/{_gv()}/debug_token?input_token={t_['token']}&access_token={d['app_id']}|{d['app_secret']}")
                exp = (j.get("data") or {}).get("data_access_expires_at") or (j.get("data") or {}).get("expires_at") or 0
                if exp:
                    t_["expires"] = exp
            except Exception:
                continue
        if exp and exp - time.time() < 7 * 86400:
            if t_.get("owner"):
                due.append(t_["owner"])
            else:
                log(T("[API] A token expires in < 7 days and has no linked ID — make a new token (Graph API Explorer).",
                      "[API] Ek token 7 din mein khatam — is ki ID maloom nahi, naya token banayein (Graph API Explorer)."))
    with _API_LOCK:
        cur = api_load(); cur["tokens"] = d.get("tokens", []); api_save(cur)
    if due:
        accs = [a for a in load_accounts() if a["id"] in due]
        log(T(f"[API] Renewing tokens for {len(accs)} ID(s) (expire in < 7 days)...",
              f"[API] {len(accs)} ID ke token naye kar rahe hain (7 din se kam baqi)..."))
        try:
            APP_INSTANCE.after(0, lambda: APP_INSTANCE._api_connect_ids(accs, silent=True))
        except Exception:
            pass


def api_scheduler_loop(log, stop_event):
    """Har 20 second: jis page ka waqt + din aa gaya, us par post. Din mein ek dafa token check."""
    busy = set()
    last_watch = 0
    while not stop_event.is_set():
        if not license_ok():
            time.sleep(30); continue
        if time.time() - last_watch > 24 * 3600:
            last_watch = time.time()
            try:
                api_token_watch(log)
            except Exception:
                pass
        try:
            now = time.localtime()
            hm = time.strftime("%H:%M", now)
            stamp = time.strftime("%Y-%m-%d ", now) + hm
            d = api_load()
            for pid, p in d["pages"].items():
                if not p.get("enabled") or p.get("paused") or pid in busy:
                    continue
                days = p.get("days", list(range(7)))
                if now.tm_wday not in days:
                    continue
                times = [t.strip().zfill(5) for t in (p.get("times") or "").replace(";", ",").split(",") if t.strip()]
                if hm not in times or p.get("last_slot") == stamp:
                    continue
                api_update_page(pid, last_slot=stamp)
                busy.add(pid)

                def work(pid=pid):
                    try:
                        api_run_page(pid, log)
                    finally:
                        busy.discard(pid)
                threading.Thread(target=work, daemon=True).start()
        except Exception as e:
            log(f"[API] scheduler error: {e}")
        stop_event.wait(20)


API_HELP = """HOW TO CONNECT (one time):
1. Open business.facebook.com → Settings → Users → System users → Add (Admin).
2. Click "Assign assets" → choose your Pages → Full control.
3. Click "Generate token" → pick your App → tick: pages_show_list, pages_manage_posts,
   pages_read_engagement, publish_video, business_management → Never expire.
4. Paste the token here and press "Fetch pages". (A normal user token from Graph API
   Explorer with the same permissions also works, but expires.)"""


class ApiPagesMixin:
    """API PAGES tab (Pages Automation + Tasks + Recovery)."""

    def _build_api_page(self):
        f = self.pages["API"]
        top = ttk.LabelFrame(f, text=T("CONNECT (official Facebook API — no browser, no captcha)",
                                       "CONNECT (official Facebook API — browser/captcha nahi)"))
        top.pack(fill="x", padx=8, pady=6)
        r = ttk.Frame(top); r.pack(fill="x", padx=6, pady=4)
        ttk.Label(r, text="Access token:").pack(side="left")
        self.api_tok = tk.StringVar()
        ttk.Entry(r, textvariable=self.api_tok, show="•", width=70).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(r, text=T("Fetch pages", "Pages lao"), command=self._api_fetch).pack(side="left", padx=4)
        ttk.Label(r, text="Graph:").pack(side="left")
        self.api_gv = tk.StringVar(value=api_load().get("graph_version", "v22.0"))
        ttk.Entry(r, textvariable=self.api_gv, width=7).pack(side="left", padx=2)
        ttk.Button(r, text=T("How to get token?", "Token kaise milega?"),
                   command=lambda: messagebox.showinfo("API", API_HELP)).pack(side="left", padx=4)

        # ---- saari IDs ko app se khud jodo ----
        con = ttk.LabelFrame(f, text=T("CONNECT ALL IDs TO YOUR APP (bot does it in each ID's own browser)",
                                       "SAARI IDs APP SE JODO (bot har ID ke apne browser mein khud karta hai)"))
        con.pack(fill="x", padx=8, pady=(0, 6))
        cr = ttk.Frame(con); cr.pack(fill="x", padx=6, pady=4)
        dd = api_load()
        ttk.Label(cr, text="App ID:").pack(side="left")
        self.api_appid = tk.StringVar(value=dd.get("app_id", ""))
        ttk.Entry(cr, textvariable=self.api_appid, width=20).pack(side="left", padx=4)
        ttk.Label(cr, text="App Secret:").pack(side="left")
        self.api_secret = tk.StringVar(value=dd.get("app_secret", ""))
        ttk.Entry(cr, textvariable=self.api_secret, show="•", width=34).pack(side="left", padx=4)
        ttk.Button(cr, text=T("CONNECT ticked IDs", "Ticked IDs JODO"), style="Accent.TButton",
                   command=self._api_connect_ids).pack(side="left", padx=6)
        ttk.Button(cr, text="Export", command=self._api_export_app).pack(side="left", padx=2)
        ttk.Button(cr, text="Pages", style="Green.TButton", command=self._page_manager).pack(side="right", padx=6)
        ttk.Button(cr, text="Import", command=self._api_import_app).pack(side="left", padx=2)
        ttk.Label(con, text=T("Tick IDs in Accounts (✔), then press CONNECT. For each ID the bot: adds it as Tester in your app → "
                              "accepts the invite → presses Continue/Allow → saves a 60-day token → brings its pages here.\n"
                              "App ID + App Secret: developers.facebook.com → My Apps → DK Uploader → App settings → Basic.",
                              "Accounts mein IDs ✔ karein, phir CONNECT dabayein. Har ID par bot: app mein Tester banata hai → "
                              "invite accept → Continue/Allow → 60 din ka token save → us ke pages yahan le aata hai.\n"
                              "App ID + App Secret: developers.facebook.com → My Apps → DK Uploader → App settings → Basic."),
                  style="Muted.TLabel", wraplength=1200, justify="left").pack(anchor="w", padx=8, pady=(0, 4))

        body = ttk.Frame(f); body.pack(fill="both", expand=True, padx=8)
        left = ttk.LabelFrame(body, text=T("PAGES AUTOMATION  (✔ = automation ON)", "PAGES AUTOMATION  (✔ = automation ON)"))
        left.pack(side="left", fill="both", expand=True)
        cols = ("name", "kind", "times", "files", "status")
        self.api_tree = ttk.Treeview(left, columns=cols, show="tree headings", height=6, selectmode="extended")
        self.api_tree.column("#0", width=50, anchor="center", stretch=False); self.api_tree.heading("#0", text="On")
        for c, t, w in (("name", "Page", 220), ("kind", "Type", 70), ("times", "Times", 140),
                        ("files", "New files", 80), ("status", "Status", 140)):
            self.api_tree.heading(c, text=t); self.api_tree.column(c, width=w, anchor="w")
        self.api_tree.pack(fill="x", padx=4, pady=4)
        self.api_tree.bind("<Button-1>", self._api_tree_click)
        bb = ttk.Frame(left); bb.pack(fill="x", padx=4, pady=4)
        ttk.Button(bb, text=T("POST NOW (selected pages)", "ABHI POST (selected pages)"), command=self._api_post_now).pack(side="left")
        ttk.Label(bb, text=T(" videos per page:", " har page par videos:")).pack(side="left")
        self.api_pn_count = tk.StringVar(value=str(load_settings().get("api_pn_count", 3)))
        tk.Spinbox(bb, from_=1, to=20, textvariable=self.api_pn_count, width=4).pack(side="left")
        ttk.Label(bb, text=T(" gap (sec):", " gap (sec):")).pack(side="left")
        self.api_pn_gap = tk.StringVar(value=str(load_settings().get("api_pn_gap", 30)))
        tk.Spinbox(bb, from_=0, to=3600, textvariable=self.api_pn_gap, width=5).pack(side="left", padx=(0, 6))
        ttk.Button(bb, text=T("Remove page", "Page hatao"), command=self._api_remove).pack(side="left", padx=4)
        ttk.Button(bb, text=T("Refresh", "Refresh"), command=self._api_refresh).pack(side="left")

        edwrap = self._scroll_area(body, width=520)
        ed = ttk.LabelFrame(edwrap, text=T("PAGE SETTINGS (click a page)", "PAGE SETTINGS (page par click karein)"))
        ed.pack(fill="both", expand=True)
        self.api_cur = None
        self.api_lbl = ttk.Label(ed, text="-", font=("Segoe UI", 12, "bold")); self.api_lbl.pack(anchor="w", padx=6, pady=4)
        g = ttk.Frame(ed); g.pack(fill="x", padx=6)
        self.api_kind = tk.StringVar(value="reel")
        self.api_times = tk.StringVar(); self.api_rot = tk.StringVar(value="order")
        self.api_capmode = tk.StringVar(value="auto"); self.api_capfile = tk.StringVar()
        self.api_fallback = tk.StringVar(); self.api_del = tk.IntVar(); self.api_pause = tk.IntVar()
        rows = [(T("Post type:", "Post type:"), ttk.Combobox(g, textvariable=self.api_kind, state="readonly", width=18,
                                                       values=("reel", "video", "photo", "text"))),
                (T("Times (HH:MM, comma):", "Waqt (HH:MM, comma):"), ttk.Entry(g, textvariable=self.api_times, width=22)),
                (T("Rotation:", "Rotation:"), ttk.Combobox(g, textvariable=self.api_rot, state="readonly", width=18,
                                                     values=("order", "random", "newest"))),
                (T("Caption from:", "Caption kahan se:"), ttk.Combobox(g, textvariable=self.api_capmode, state="readonly", width=18,
                                                               values=("auto", "txt", "captions_file", "filename", "fallback")))]
        for i, (lab, w) in enumerate(rows):
            ttk.Label(g, text=lab).grid(row=i, column=0, sticky="w", pady=2); w.grid(row=i, column=1, sticky="w", pady=2)
        dfr = ttk.Frame(ed); dfr.pack(fill="x", padx=6, pady=2)
        ttk.Label(dfr, text=T("Days:", "Din:")).pack(side="left")
        self.api_days = []
        for i, dn in enumerate(DAY_NAMES):
            v = tk.IntVar(value=1); self.api_days.append(v)
            ttk.Checkbutton(dfr, text=dn, variable=v).pack(side="left")
        ttk.Label(ed, text=T("Source folders:", "Source folders:")).pack(anchor="w", padx=6)
        self.api_folders = tk.Listbox(ed, height=3, width=48); self.api_folders.pack(padx=6, fill="x")
        fb = ttk.Frame(ed); fb.pack(fill="x", padx=6, pady=2)
        ttk.Button(fb, text=T("Add folder", "Folder add"), command=self._api_add_folder).pack(side="left")
        ttk.Button(fb, text=T("Remove folder", "Folder hatao"), command=lambda: [self.api_folders.delete(i) for i in self.api_folders.curselection()[::-1]]).pack(side="left", padx=4)
        cf = ttk.Frame(ed); cf.pack(fill="x", padx=6, pady=2)
        ttk.Label(cf, text=T("Captions file:", "Captions file:")).pack(side="left")
        ttk.Entry(cf, textvariable=self.api_capfile, width=28).pack(side="left", padx=3)
        ttk.Button(cf, text="...", width=3, command=lambda: self.api_capfile.set(
            filedialog.askopenfilename(filetypes=[("Text", "*.txt")]) or self.api_capfile.get())).pack(side="left")
        ff = ttk.Frame(ed); ff.pack(fill="x", padx=6, pady=2)
        ttk.Label(ff, text=T("Fallback caption:", "Fallback caption:")).pack(side="left")
        ttk.Entry(ff, textvariable=self.api_fallback, width=32).pack(side="left", padx=3)
        ttk.Checkbutton(ed, text=T("Delete file after successful post", "Post ke baad file delete"), variable=self.api_del).pack(anchor="w", padx=6)
        ttk.Checkbutton(ed, text=T("Pause this page", "Is page ko pause"), variable=self.api_pause).pack(anchor="w", padx=6)
        ttk.Button(ed, text=T("SAVE PAGE SETTINGS", "PAGE SETTINGS SAVE"), command=self._api_save_page).pack(anchor="w", padx=6, pady=6)

        hist = ttk.LabelFrame(left, text=T("PUBLISHING TASKS / RECOVERY (last 300)", "PUBLISHING TASKS / RECOVERY (aakhri 300)"))
        hist.pack(fill="both", expand=True, padx=4, pady=4)
        self.api_hist = ttk.Treeview(hist, columns=("time", "page", "kind", "posted", "file", "res"), show="headings", height=4)
        for c, t, w in (("time", "Time", 130), ("page", "Page", 150), ("kind", "Type", 50), ("posted", "Posted", 70),
                        ("file", "File", 170), ("res", "Result", 300)):
            self.api_hist.heading(c, text=t); self.api_hist.column(c, width=w, anchor="w")
        self.api_hist.pack(fill="both", expand=True, padx=4, pady=4)
        hb = ttk.Frame(hist); hb.pack(fill="x", padx=4, pady=2)
        ttk.Button(hb, text=T("Retry selected failed", "Fail wali dobara"), command=self._api_retry).pack(side="left")
        self._api_refresh()
        self._api_stop = threading.Event()
        threading.Thread(target=api_scheduler_loop, args=(lambda m: self.log_q.put(m), self._api_stop),
                         daemon=True).start()
        self.after(30000, self._api_tick)

    def _api_tick(self):
        try:
            self._api_refresh()
        finally:
            self.after(30000, self._api_tick)

    def _api_refresh(self):
        d = api_load()
        sel = self.api_cur
        self.api_tree.delete(*self.api_tree.get_children())
        for pid, p in d["pages"].items():
            pp = dict(p); pp["id"] = pid
            n = 0
            if pp.get("kind") != "text":
                kind = "photo" if pp.get("kind") == "photo" else "video"
                key = "API:" + pid
                for fo in pp.get("folders", []):
                    n += sum(1 for f in list_media_in_folder(fo, kind) if not is_uploaded(key, file_hash(f) or f))
            img = self.img_check if p.get("enabled") else self.img_empty
            self.api_tree.insert("", "end", iid=pid, image=img,
                                 values=(p.get("name", pid), p.get("kind", "reel"), p.get("times", ""),
                                         "-" if pp.get("kind") == "text" else n,
                                         ("PAUSED " if p.get("paused") else "") + p.get("status", "")))
        if sel and self.api_tree.exists(sel):
            self.api_tree.selection_set(sel)
        self.api_hist.delete(*self.api_hist.get_children())
        for i, h in enumerate(d["history"]):
            self.api_hist.insert("", "end", iid=str(i), values=(
                h["time"], h["page"], h["kind"], h.get("prog", ""), os.path.basename(h["file"]),
                ("✔ " if h["ok"] else "✖ ") + h["info"]))

    def _api_fetch(self):
        tok = self.api_tok.get().strip()
        if not tok:
            messagebox.showwarning("API", T("Paste the access token first.", "Pehle access token paste karein."))
            return
        d = api_load(); d["graph_version"] = self.api_gv.get().strip() or "v22.0"; api_save(d)
        self.log_q.put(T("[API] Fetching your pages...", "[API] Aapke pages la rahe hain..."))

        def work():
            try:
                pages = api_fetch_pages(tok)
                with _API_LOCK:
                    d = api_load()
                    for p in pages:
                        cur = d["pages"].setdefault(p["id"], {"enabled": False, "kind": "reel", "times": "",
                                                              "days": list(range(7)), "folders": [],
                                                              "rotation": "order", "caption_mode": "auto"})
                        cur["name"], cur["token"] = p["name"], p["token"]
                    if tok not in [t.get("token") for t in d["tokens"]]:
                        d["tokens"].append({"token": tok, "added": time.strftime("%Y-%m-%d")})
                    api_save(d)
                self.log_q.put(T(f"[API] {len(pages)} page(s) connected.", f"[API] {len(pages)} page jur gaye."))
                self.after(0, self._api_refresh)
                self.after(0, lambda: self.api_tok.set(""))
            except Exception as e:
                self.log_q.put(f"[API] Fetch failed: {e}")
                self.after(0, lambda: messagebox.showerror("API", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _api_tree_click(self, e):
        row = self.api_tree.identify_row(e.y)
        if not row:
            return
        if self.api_tree.identify_column(e.x) == "#0":
            p = api_load()["pages"].get(row, {})
            api_update_page(row, enabled=not p.get("enabled"))
        self.api_cur = row
        self._api_load_editor(row)
        self._api_refresh()

    def _api_load_editor(self, pid):
        p = api_load()["pages"].get(pid, {})
        self.api_lbl.config(text=p.get("name", pid))
        self.api_kind.set(p.get("kind", "reel")); self.api_times.set(p.get("times", ""))
        self.api_rot.set(p.get("rotation", "order")); self.api_capmode.set(p.get("caption_mode", "auto"))
        self.api_capfile.set(p.get("captions_file", "")); self.api_fallback.set(p.get("fallback", ""))
        self.api_del.set(1 if p.get("delete_after") else 0); self.api_pause.set(1 if p.get("paused") else 0)
        days = p.get("days", list(range(7)))
        for i, v in enumerate(self.api_days):
            v.set(1 if i in days else 0)
        self.api_folders.delete(0, "end")
        for fo in p.get("folders", []):
            self.api_folders.insert("end", fo)

    def _api_add_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.api_folders.insert("end", d)

    def _api_save_page(self):
        if not self.api_cur:
            messagebox.showwarning("API", T("Click a page first.", "Pehle page par click karein."))
            return
        import re as _re
        times = []
        for t in self.api_times.get().replace(";", ",").split(","):
            t = t.strip()
            if _re.match(r"^\d{1,2}:\d{2}$", t):
                h, m = t.split(":")
                if int(h) < 24 and int(m) < 60:
                    times.append(f"{int(h):02d}:{m}")
        api_update_page(self.api_cur, kind=self.api_kind.get(), times=", ".join(times),
                        rotation=self.api_rot.get(), caption_mode=self.api_capmode.get(),
                        captions_file=self.api_capfile.get().strip(), fallback=self.api_fallback.get(),
                        delete_after=bool(self.api_del.get()), paused=bool(self.api_pause.get()),
                        days=[i for i, v in enumerate(self.api_days) if v.get()],
                        folders=list(self.api_folders.get(0, "end")))
        self.log_q.put(T("[API] Page settings saved.", "[API] Page settings save ho gayi."))
        self._api_load_editor(self.api_cur)
        self._api_refresh()

    def _api_post_now(self):
        pids = list(self.api_tree.selection()) or ([self.api_cur] if self.api_cur else [])
        if not pids:
            messagebox.showwarning("API", T("Select page(s) first (Ctrl+click for many).",
                                            "Pehle page(s) chunein (zyada ke liye Ctrl+click)."))
            return
        try:
            n = max(1, int(self.api_pn_count.get() or 1)); gap = max(0, int(self.api_pn_gap.get() or 0))
        except ValueError:
            n, gap = 1, 30
        save_settings({"api_pn_count": n, "api_pn_gap": gap})
        log = lambda m: self.log_q.put(m)
        log(T(f"[API] POST NOW: {len(pids)} page(s) × {n} video(s), gap {gap}s.",
              f"[API] ABHI POST: {len(pids)} page × {n} video, gap {gap}s."))

        def one_page(pid):
            done = 0
            for k in range(n):
                ok = api_run_page(pid, log, prog=f"{done}|{n}")
                done += 1 if ok else 0
                self.after(0, self._api_refresh)
                if not ok and api_load()["pages"].get(pid, {}).get("status") == "No new file":
                    break
                if k < n - 1:
                    time.sleep(gap + random.randint(0, max(1, gap // 4)))

        def run_all():
            ths = []
            for pid in pids:
                while len([t for t in ths if t.is_alive()]) >= 4:
                    time.sleep(1)
                t = threading.Thread(target=one_page, args=(pid,), daemon=True); t.start(); ths.append(t)
                time.sleep(random.uniform(2, 5))
            for t in ths:
                t.join()
            log(T("[API] POST NOW finished.", "[API] ABHI POST mukammal."))
        threading.Thread(target=run_all, daemon=True).start()

    def _api_export_app(self):
        """Sirf App ID + App Secret ek file mein (doosre PC ke liye). Token / pages NAHI."""
        app_id = self.api_appid.get().strip(); secret = self.api_secret.get().strip()
        if not app_id or not secret:
            messagebox.showwarning("API", T("Enter App ID and App Secret first.", "Pehle App ID aur App Secret daalein."))
            return
        p = filedialog.asksaveasfilename(defaultextension=".dkapp", initialfile="DK_app_settings.dkapp",
                                         filetypes=[("DK app settings", "*.dkapp")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"dk_app": 1, "app_id": app_id, "app_secret": secret}, f)
        messagebox.showinfo("API", T("Saved. Only App ID + Secret are in this file (no token, no pages).\n"
                                     "Keep it private — it is like a password.",
                                     "Save ho gaya. Is file mein sirf App ID + Secret hain (token / pages nahi).\n"
                                     "Ise sambhal kar rakhein — ye password jaisi hai."))

    def _api_import_app(self):
        p = filedialog.askopenfilename(filetypes=[("DK app settings", "*.dkapp"), ("All", "*.*")])
        if not p:
            return
        try:
            j = json.load(open(p, encoding="utf-8"))
            self.api_appid.set(j.get("app_id", "")); self.api_secret.set(j.get("app_secret", ""))
            with _API_LOCK:
                d = api_load(); d["app_id"], d["app_secret"] = j.get("app_id", ""), j.get("app_secret", ""); api_save(d)
            messagebox.showinfo("API", T("App ID + Secret loaded. Now tick IDs in Accounts and press CONNECT.",
                                         "App ID + Secret lag gaye. Ab Accounts mein IDs tick kar ke CONNECT dabayein."))
        except Exception as e:
            messagebox.showerror("API", str(e))

    def _api_connect_ids(self, accs=None, silent=False):
        app_id = self.api_appid.get().strip(); secret = self.api_secret.get().strip()
        if not app_id or not secret:
            if not silent:
                messagebox.showwarning("API", T("Enter App ID and App Secret first.", "Pehle App ID aur App Secret daalein."))
            return
        if accs is None:
            accs = self.acc_table.checked_accounts() if hasattr(self, "acc_table") else []
        if not accs:
            if not silent:
                messagebox.showwarning("API", T("Tick IDs (✔) in Accounts first.", "Pehle Accounts mein IDs ✔ karein."))
            return
        with _API_LOCK:
            d = api_load(); d["app_id"], d["app_secret"] = app_id, secret; api_save(d)
        log = lambda m: self.log_q.put(m)
        log(T(f"[API] CONNECT: {len(accs)} ID(s)...", f"[API] CONNECT: {len(accs)} ID..."))
        self.stop_flag.clear()

        def one(acc):
            bot = Automation(log=log, headless=False, browser_cfg=load_settings())
            try:
                bot.start(acc)
                if not bot.login(wait_for_2fa_callback=self._wait_2fa):
                    log(f"[API] {acc['name']}: login failed — skipped."); return
                uid = bot._fb_uid()
                if uid:
                    try:
                        api_add_tester(app_id, secret, uid)
                        log(T(f"[API] {acc['name']}: added as Tester.", f"[API] {acc['name']}: Tester ban gayi."))
                    except Exception as e:
                        log(f"[API] {acc['name']}: tester: {str(e)[:120]}")
                short = bot.api_connect_flow(app_id)
                if not short:
                    log(T(f"[API] {acc['name']}: token NOT received (see browser).",
                          f"[API] {acc['name']}: token NAHI mila (browser dekhein).")); return
                tok = api_long_token(app_id, secret, short)
                pages = api_fetch_pages(tok)
                api_merge_pages(pages, acc["id"], tok)
                with _API_LOCK:
                    d2 = api_load()
                    for t_ in d2["tokens"]:
                        if t_.get("token") == tok:
                            t_["expires"] = time.time() + 59 * 86400
                    # isi ID ke purane tokens hata do
                    d2["tokens"] = [t_ for t_ in d2["tokens"] if t_.get("owner") != acc["id"] or t_.get("token") == tok]
                    api_save(d2)
                update_account(acc["id"], api_connected=time.strftime("%Y-%m-%d"))
                log(T(f"[API] {acc['name']}: connected ✔ — {len(pages)} page(s).",
                      f"[API] {acc['name']}: jur gayi ✔ — {len(pages)} page."))
                self.after(0, self._api_refresh)
            except Exception as e:
                log(f"[API] {acc['name']}: {str(e)[:160]}")
            finally:
                try:
                    bot.stop()
                except Exception:
                    pass

        def run():
            set_tile_count(min(2, len(accs)))
            ths = []
            for a in accs:
                if self.stop_flag.is_set():
                    break
                while len([t for t in ths if t.is_alive()]) >= 2:
                    time.sleep(2)
                t = threading.Thread(target=one, args=(a,), daemon=True); t.start(); ths.append(t)
                time.sleep(random.uniform(5, 10))
            for t in ths:
                t.join()
            log(T("[API] CONNECT finished.", "[API] CONNECT mukammal."))
        threading.Thread(target=run, daemon=True).start()

    def _api_remove(self):
        pid = self.api_cur
        if pid and messagebox.askyesno("API", T("Remove this page from API list?", "Ye page API list se hatayein?")):
            with _API_LOCK:
                d = api_load(); d["pages"].pop(pid, None); api_save(d)
            self.api_cur = None
            self._api_refresh()

    def _api_retry(self):
        sel = self.api_hist.selection()
        if not sel:
            return
        h = api_load()["history"][int(sel[0])]
        if h.get("ok"):
            messagebox.showinfo("API", T("This one already posted.", "Ye pehle hi post ho chuki hai."))
            return
        fp = h.get("file") or None
        if fp and not os.path.isfile(fp):
            messagebox.showwarning("API", T("File not found anymore.", "File ab mojood nahi."))
            return
        threading.Thread(target=lambda: (api_run_page(h["page_id"], lambda m: self.log_q.put(m), fp),
                                         self.after(0, self._api_refresh)), daemon=True).start()


# ============================================================
# PRO DARK UI — sidebar, header, dashboard, proxy manager, logs
# ============================================================
def ui_zoom():
    """Text/screen size (Settings → Text size). 1.0 = 100%."""
    try:
        z = float(load_settings().get("ui_zoom", 1.5))
        return min(2.5, max(0.8, z))
    except Exception:
        return 1.5


PAL = {"bg": "#0b1220", "side": "#0a101c", "panel": "#111a2b", "panel2": "#16213a", "border": "#22304d",
       "text": "#e5edf7", "muted": "#8ea0bd", "accent": "#22c3e6", "accent2": "#1aa6c6",
       "ok": "#34d399", "warn": "#fbbf24", "bad": "#f87171", "field": "#0e1626"}


def ui_px(root):
    """Pixel multiplier = Text size x screen DPI."""
    try:
        d = root.winfo_fpixels("1i") / 96.0
    except Exception:
        d = 1.0
    return ui_zoom() * d


def apply_dark_theme(root, style):
    P = PAL
    try:
        style.theme_use("clam")
    except Exception:
        pass
    F = ("Segoe UI", 11, "bold")
    root.configure(bg=P["bg"])
    for k, v in {"*Font": F, "*Background": P["bg"], "*Foreground": P["text"],
                 "*Listbox.background": P["field"], "*Listbox.foreground": P["text"],
                 "*Listbox.selectBackground": P["accent2"], "*Text.background": P["field"],
                 "*Text.foreground": P["text"], "*Text.insertBackground": P["text"],
                 "*Spinbox.background": P["field"], "*Spinbox.foreground": P["text"],
                 "*Spinbox.buttonBackground": P["panel2"], "*Entry.background": P["field"],
                 "*Entry.foreground": P["text"], "*TCombobox*Listbox.background": P["field"],
                 "*TCombobox*Listbox.foreground": P["text"], "*TCombobox*Listbox.font": F,
                 "*Menu.background": P["panel"], "*Menu.foreground": P["text"],
                 "*Button.background": P["panel2"], "*Button.foreground": P["text"],
                 "*Label.background": P["bg"], "*Label.foreground": P["text"],
                 "*Checkbutton.background": P["bg"], "*Radiobutton.background": P["bg"]}.items():
        root.option_add(k, v)
    style.configure(".", background=P["bg"], foreground=P["text"], fieldbackground=P["field"],
                    bordercolor=P["border"], lightcolor=P["panel"], darkcolor=P["bg"],
                    troughcolor=P["panel"], focuscolor=P["accent"], font=F,
                    selectbackground=P["accent2"], selectforeground="#ffffff", insertcolor=P["text"])
    style.configure("TFrame", background=P["bg"])
    style.configure("Card.TFrame", background=P["panel"])
    style.configure("TLabel", background=P["bg"], foreground=P["text"], font=F)
    style.configure("Muted.TLabel", foreground=P["muted"], font=("Segoe UI", 10, "bold"))
    style.configure("TLabelframe", background=P["bg"], bordercolor=P["border"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=P["bg"], foreground=P["accent"], font=("Segoe UI", 11, "bold"))
    style.configure("TButton", background=P["panel2"], foreground=P["text"], bordercolor=P["border"],
                    padding=(12, 6), font=F, relief="flat")
    style.map("TButton", background=[("pressed", P["accent2"]), ("active", "#1e2c4a")],
              foreground=[("disabled", P["muted"])])
    style.configure("Accent.TButton", background=P["accent2"], foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", P["accent"])])
    style.configure("Green.TButton", background="#16803c", foreground="#ffffff")
    style.map("Green.TButton", background=[("active", "#1fa34f")])
    for w in ("TCheckbutton", "TRadiobutton"):
        style.configure(w, background=P["bg"], foreground=P["text"], font=F, indicatorcolor=P["field"],
                        indicatorbackground=P["field"])
        style.map(w, background=[("active", P["bg"])],
                  indicatorcolor=[("selected", P["accent2"]), ("pressed", P["accent"])])
    style.configure("TEntry", fieldbackground=P["field"], foreground=P["text"], bordercolor=P["border"],
                    insertcolor=P["text"], padding=5)
    style.configure("TCombobox", fieldbackground=P["field"], background=P["panel2"], foreground=P["text"],
                    arrowcolor=P["text"], bordercolor=P["border"], padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", P["field"])], foreground=[("readonly", P["text"])],
              selectbackground=[("readonly", P["field"])])
    style.configure("Treeview", background=P["panel"], fieldbackground=P["panel"], foreground=P["text"],
                    rowheight=int(24 * ui_px(root)), font=("Segoe UI", 11), bordercolor=P["border"])
    style.map("Treeview", background=[("selected", "#1b3a5c")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=P["panel2"], foreground=P["muted"],
                    font=("Segoe UI", 10, "bold"), relief="flat", padding=6)
    style.map("Treeview.Heading", background=[("active", P["panel2"])])
    style.configure("Vertical.TScrollbar", background=P["panel2"], troughcolor=P["bg"], arrowcolor=P["muted"],
                    bordercolor=P["bg"])
    style.configure("TNotebook", background=P["bg"], bordercolor=P["border"])
    style.configure("TNotebook.Tab", background=P["panel"], foreground=P["muted"], padding=(14, 6))
    style.map("TNotebook.Tab", background=[("selected", P["panel2"])], foreground=[("selected", P["text"])])
    style.configure("TSpinbox", fieldbackground=P["field"], foreground=P["text"], arrowcolor=P["text"])


NAV = [("WORKSPACE", [("Dashboard", "Dashboard", "▦"), ("Accounts", "Accounts", "👤"),
                      ("Proxy", "Proxy Management", "⇄")]),
       ("AUTOMATION", [("Sources", "Content Sources", "⬇"), ("Post", "Posting (browser)", "⚡"), ("API", "API Pages", "☁"),
                       ("Interact", "Active & Groups", "♥"), ("Schedule", "Schedule", "⏱")]),
       # LDPlayer (mobile) abhi chhupa hai — code mojood, zaroorat par wapas: ("LDPlayer", "LDPlayer (mobile)", "📱")
       ("SYSTEM", [("Logs", "Logs", "≡"), ("Settings", "System Settings", "⚙"), ("License", "License", "🔑"),
                   ("Guide", "Guide", "?")])]

WG_HELP = """SURFSHARK — different IP for every browser (one time setup):
1. Log in at my.surfshark.com → VPN → Manual setup → Desktop or mobile → WireGuard.
2. "I don't have a key pair" → Generate a new key pair (save it).
3. Choose a location (e.g. United States – Dallas) → Download. Repeat for many
   US cities/servers (one file = one IP). 10 IDs → ~10 files.
4. Here: Import .conf files → Check tunnels → tick IDs in Accounts →
   Assign tunnels to ticked IDs. Each browser now opens through its own tunnel.
No Surfshark app / ExpressVPN needs to be running for this."""

TITLES = {k: (sec, lab) for sec, items in NAV for k, lab, _ in items}


class ProUIMixin:
    def _build_sidebar(self):
        P = PAL
        try:
            _dpi = self.winfo_fpixels("1i") / 96.0
        except Exception:
            _dpi = 1.0
        side = tk.Frame(self, bg=P["side"], width=int(205 * ui_zoom() * _dpi))
        side.pack(side="left", fill="y"); side.pack_propagate(False)
        brand = tk.Frame(side, bg=P["side"]); brand.pack(fill="x", padx=16, pady=(16, 10))
        tk.Label(brand, text="DK", bg=P["accent2"], fg="white", font=("Segoe UI", 15, "bold"),
                 width=3).pack(side="left")
        bt = tk.Frame(brand, bg=P["side"]); bt.pack(side="left", padx=10)
        tk.Label(bt, text="DK Facebook Uploader", bg=P["side"], fg=P["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(bt, text=f"FACEBOOK SUITE  ·  v{APP_VERSION}", bg=P["side"], fg=P["accent"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self._nav_items = {}
        for sec, items in NAV:
            tk.Label(side, text=sec, bg=P["side"], fg=P["muted"], font=("Segoe UI", 9, "bold")).pack(
                anchor="w", padx=18, pady=(14, 4))
            for key, label, icon in items:
                it = tk.Label(side, text=f"  {icon}   {label}", anchor="w", bg=P["side"], fg="#c9d6ea",
                              font=("Segoe UI", 11, "bold"), padx=10, pady=7, cursor="hand2")
                it.pack(fill="x", padx=10, pady=1)
                it.bind("<Button-1>", lambda e, k=key: self._show(k))
                it.bind("<Enter>", lambda e, w=it, k=key: w.config(bg="#141f36") if self._cur_page != k else None)
                it.bind("<Leave>", lambda e, w=it, k=key: w.config(bg=P["side"]) if self._cur_page != k else None)
                self._nav_items[key] = it
        foot = tk.Frame(side, bg=P["side"]); foot.pack(side="bottom", fill="x", padx=14, pady=12)
        self._side_foot = tk.Label(foot, text="", bg=P["side"], fg=P["muted"], font=("Segoe UI", 9, "bold"),
                                   justify="left", anchor="w")
        self._side_foot.pack(fill="x")
        self.right = tk.Frame(self, bg=P["bg"]); self.right.pack(side="left", fill="both", expand=True)
        hdr = tk.Frame(self.right, bg=P["bg"]); hdr.pack(fill="x", padx=22, pady=(14, 4))
        self._hdr_crumb = tk.Label(hdr, text="", bg=P["bg"], fg=P["muted"], font=("Segoe UI", 9, "bold"))
        self._hdr_crumb.pack(anchor="w")
        self._hdr_title = tk.Label(hdr, text="", bg=P["bg"], fg=P["text"], font=("Segoe UI", 20, "bold"))
        self._hdr_title.pack(anchor="w")
        self._cur_page = None

    def _show(self, name):
        if name not in self.pages:
            return
        self.pages[name].tkraise()
        self._cur_page = name
        P = PAL
        for k, w in getattr(self, "_nav_items", {}).items():
            if k == name:
                w.config(bg="#12304a", fg="#ffffff")
            else:
                w.config(bg=P["side"], fg="#c9d6ea")
        sec, lab = TITLES.get(name, ("", name))
        try:
            self._hdr_crumb.config(text=f"{sec} / {lab.upper()}")
            self._hdr_title.config(text=lab)
        except Exception:
            pass
        if name == "Dashboard":
            self._dash_refresh()
        elif name == "Proxy":
            self._proxy_refresh()
        elif name == "Logs":
            self._logs_refresh()
        elif name == "Sources":
            self._src_refresh()

    def _build_statusbar(self):
        P = PAL
        sb = tk.Frame(self.right, bg=P["side"]); sb.pack(side="bottom", fill="x")
        self._status_lbl = tk.Label(sb, text="", bg=P["side"], fg=P["muted"], font=("Segoe UI", 9, "bold"), anchor="w")
        self._status_lbl.pack(side="left", padx=12, pady=4)
        self.after(1500, self._status_tick)

    def _status_tick(self):
        try:
            accs = load_accounts()
            live = sum(1 for a in accs if str(self.api_status_map.get(a["id"], "")).lower().startswith("live"))
            br = {"fbbrowser": "FB Browser", "ixbrowser": "ixBrowser", "chrome": "Chrome", "custom": "Custom"}.get(
                load_settings().get("browser", "fbbrowser"), "FB Browser")
            self._status_lbl.config(text=f"●  Ready   ·   {len(accs)} profiles   ·   {live} live   ·   {br}")
            self._side_foot.config(text=f"Workspace\n{len(accs)} IDs  ·  {br}")
        except Exception:
            pass
        self.after(5000, self._status_tick)

    # ---------- Dashboard ----------
    def _card(self, parent, title, icon="", key=None):
        P = PAL
        c = tk.Frame(parent, bg=P["panel"], highlightbackground=P["border"], highlightthickness=1)
        tk.Label(c, text=f"{icon}  {title}", bg=P["panel"], fg=P["muted"], font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=16, pady=(14, 2))
        v = tk.Label(c, text="0", bg=P["panel"], fg=P["text"], font=("Segoe UI", 26, "bold"))
        v.pack(anchor="w", padx=16)
        lk = tk.Label(c, text="Open workspace  ↗", bg=P["panel"], fg=P["accent"], font=("Segoe UI", 9, "bold"),
                      cursor="hand2")
        lk.pack(anchor="w", padx=16, pady=(2, 14))
        if key:
            lk.bind("<Button-1>", lambda e: self._show(key))
        return c, v

    def _build_dashboard_page(self):
        P = PAL
        f = self.pages["Dashboard"]
        f.configure(style="TFrame")
        row = tk.Frame(f, bg=P["bg"]); row.pack(fill="x", padx=14, pady=(6, 10))
        self._dash_vals = {}
        for i, (k, t, ic, go) in enumerate([("acc", "Accounts", "👤", "Accounts"), ("live", "Live IDs", "●", "Accounts"),
                                            ("pages", "Pages (ticked)", "⚑", "Accounts"),
                                            ("api", "API pages ON", "☁", "API"),
                                            ("today", "Posted today", "✔", "Logs"),
                                            ("fail", "Failed today", "✖", "Logs")]):
            c, v = self._card(row, t, ic, go)
            c.grid(row=0, column=i, sticky="nsew", padx=6)
            row.grid_columnconfigure(i, weight=1)
            self._dash_vals[k] = v
        low = tk.Frame(f, bg=P["bg"]); low.pack(fill="both", expand=True, padx=20, pady=4)
        nm = tk.Frame(low, bg=P["panel"], highlightbackground=P["border"], highlightthickness=1)
        nm.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(nm, text="Your next move", bg=P["panel"], fg=P["text"], font=("Segoe UI", 13, "bold")).pack(
            anchor="w", padx=16, pady=(14, 2))
        tk.Label(nm, text="Add IDs, login, pick pages, then let the scheduler post.", bg=P["panel"],
                 fg=P["muted"], font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(0, 8))
        for t, sub, k in [("Manage accounts", "Import .txt, login with 2FA, FB Browser profiles", "Accounts"),
                          ("Schedule page posts (API)", "Official API · no browser · no captcha", "API"),
                          ("Post with browser", "Reels / video / photo to ticked pages", "Post"),
                          ("Keep IDs active", "Warm-up, groups, comments", "Interact"),
                          ("Proxies", "Check speed, assign to IDs", "Proxy")]:
            b = tk.Frame(nm, bg=P["panel2"], cursor="hand2"); b.pack(fill="x", padx=14, pady=4)
            l1 = tk.Label(b, text=t, bg=P["panel2"], fg=P["text"], font=("Segoe UI", 11, "bold"), cursor="hand2")
            l1.pack(anchor="w", padx=12, pady=(8, 0))
            l2 = tk.Label(b, text=sub, bg=P["panel2"], fg=P["muted"], font=("Segoe UI", 9, "bold"), cursor="hand2")
            l2.pack(anchor="w", padx=12, pady=(0, 8))
            for w in (b, l1, l2):
                w.bind("<Button-1>", lambda e, k=k: self._show(k))
        ra = tk.Frame(low, bg=P["panel"], highlightbackground=P["border"], highlightthickness=1)
        ra.pack(side="left", fill="both", expand=True, padx=(8, 0))
        hr = tk.Frame(ra, bg=P["panel"]); hr.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(hr, text="Recent activity", bg=P["panel"], fg=P["text"], font=("Segoe UI", 13, "bold")).pack(side="left")
        vl = tk.Label(hr, text="View logs ↗", bg=P["panel"], fg=P["accent"], font=("Segoe UI", 9, "bold"), cursor="hand2")
        vl.pack(side="right"); vl.bind("<Button-1>", lambda e: self._show("Logs"))
        self._dash_recent = tk.Frame(ra, bg=P["panel"]); self._dash_recent.pack(fill="both", expand=True, padx=12)

    def _all_post_events(self):
        """API history + browser post history ek list mein: [(time, ok, title, sub)]"""
        ev = []
        try:
            for h in api_load().get("history", []):
                ev.append((h["time"], h["ok"], f"{h['kind'].title()} {'posted' if h['ok'] else 'failed'}",
                           f"{h['page']} · API"))
        except Exception:
            pass
        try:
            for pg, lst in all_page_history().items():
                for h in lst[-200:]:
                    ok = h.get("status") == "success"
                    ev.append((h.get("time", "")[:16], ok, f"Post {'published' if ok else 'failed'}",
                               f"{pg} · {h.get('video', '')}" + ("" if ok else f" · {h.get('issue', '')}")))
        except Exception:
            pass
        ev.sort(key=lambda x: str(x[0]), reverse=True)
        return ev

    def _dash_refresh(self):
        P = PAL
        try:
            accs = load_accounts()
            live = sum(1 for a in accs if str(self.api_status_map.get(a["id"], "")).lower().startswith("live"))
            pages = sum(1 for a in accs for p in (a.get("pages") or []) if p.get("enabled"))
            api_on = sum(1 for p in api_load()["pages"].values() if p.get("enabled"))
            today = time.strftime("%Y-%m-%d")
            ev = self._all_post_events()
            ok_t = sum(1 for e in ev if str(e[0]).startswith(today) and e[1])
            bad_t = sum(1 for e in ev if str(e[0]).startswith(today) and not e[1])
            for k, v in (("acc", len(accs)), ("live", live), ("pages", pages), ("api", api_on),
                         ("today", ok_t), ("fail", bad_t)):
                self._dash_vals[k].config(text=f"{v:02d}")
            for w in self._dash_recent.winfo_children():
                w.destroy()
            if not ev:
                tk.Label(self._dash_recent, text="No activity yet.", bg=P["panel"], fg=P["muted"]).pack(anchor="w", pady=8)
            for t, ok, title, sub in ev[:7]:
                r = tk.Frame(self._dash_recent, bg=P["panel"]); r.pack(fill="x", pady=3)
                tk.Label(r, text="✔" if ok else "✖", bg=P["panel"], fg=P["ok"] if ok else P["bad"],
                         font=("Segoe UI", 12, "bold")).pack(side="left", padx=(4, 10))
                tx = tk.Frame(r, bg=P["panel"]); tx.pack(side="left", fill="x", expand=True)
                tk.Label(tx, text=title, bg=P["panel"], fg=P["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
                tk.Label(tx, text=sub, bg=P["panel"], fg=P["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
                tk.Label(r, text=str(t)[-5:], bg=P["panel"], fg=P["muted"], font=("Segoe UI", 9, "bold")).pack(side="right")
        except Exception as e:
            self.log_q.put(f"dashboard: {e}")

    # ---------- Proxy Management ----------
    def _build_proxy_page(self):
        f = self.pages["Proxy"]
        top = ttk.Frame(f); top.pack(fill="x", padx=20, pady=6)
        ttk.Label(top, text=T("Add proxies (one per line: host:port or host:port:user:pass or http://user:pass@host:port)",
                              "Proxies daalo (har line mein: host:port ya host:port:user:pass)"),
                  style="Muted.TLabel").pack(anchor="w")
        self.px_text = tk.Text(top, height=3, font=("Consolas", 11), relief="flat")
        self.px_text.pack(fill="x", pady=4)
        bb = ttk.Frame(top); bb.pack(fill="x")
        ttk.Button(bb, text=T("Add to pool", "Pool mein daalo"), command=self._proxy_add).pack(side="left")
        ttk.Button(bb, text=T("Check selected", "Selected check"), command=lambda: self._proxy_check(True)).pack(side="left", padx=4)
        ttk.Button(bb, text=T("Check all", "Sab check"), command=lambda: self._proxy_check(False)).pack(side="left")
        ttk.Button(bb, text=T("Assign to ticked IDs (1 each)", "Tick IDs ko do (1-1)"), style="Accent.TButton",
                   command=self._proxy_assign).pack(side="left", padx=4)
        ttk.Button(bb, text=T("Remove selected", "Selected hatao"), command=self._proxy_remove).pack(side="left")
        cols = ("proxy", "type", "status", "lat", "ip", "used")
        # ---- VPN tunnels (Surfshark WireGuard) ----
        wg = ttk.LabelFrame(f, text=T("VPN TUNNELS — Surfshark / WireGuard (different IP for every browser)",
                                      "VPN TUNNELS — Surfshark / WireGuard (har browser ka alag IP)"))
        wg.pack(fill="x", padx=20, pady=(4, 6))
        wb = ttk.Frame(wg); wb.pack(fill="x", padx=6, pady=4)
        ttk.Button(wb, text=T("Import .conf files", ".conf files daalo"), command=self._wg_import).pack(side="left")
        ttk.Button(wb, text=T("Check tunnels (IP/city)", "Tunnels check (IP/city)"), command=self._wg_check).pack(side="left", padx=4)
        ttk.Button(wb, text=T("Assign tunnels to ticked IDs", "Tick IDs ko tunnels do"), style="Accent.TButton",
                   command=self._wg_assign).pack(side="left")
        ttk.Button(wb, text=T("Remove selected", "Selected hatao"), command=self._wg_remove).pack(side="left", padx=4)
        ttk.Button(wb, text=T("How to get Surfshark files?", "Surfshark files kaise?"),
                   command=lambda: messagebox.showinfo("Surfshark", WG_HELP)).pack(side="left")
        self.wg_tree = ttk.Treeview(wg, columns=("name", "ep", "port", "ip", "used"), show="headings", height=4)
        for c, t_, w in (("name", "Tunnel", 180), ("ep", "Server", 260), ("port", "Local port", 100),
                         ("ip", "IP / City", 280), ("used", "Used by", 260)):
            self.wg_tree.heading(c, text=t_); self.wg_tree.column(c, width=w, anchor="w")
        self.wg_tree.pack(fill="x", padx=6, pady=4)
        self.px_tree = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c, t, w in (("proxy", "Proxy", 300), ("type", "Protocol", 90), ("status", "Status", 110),
                        ("lat", "Latency", 90), ("ip", "IP / Country", 200), ("used", "Used by", 260)):
            self.px_tree.heading(c, text=t); self.px_tree.column(c, width=w, anchor="w")
        self.px_tree.pack(fill="both", expand=True, padx=20, pady=8)

    def _proxy_pool(self):
        s = load_settings()
        pool = s.get("proxy_pool", [])
        for a in load_accounts():
            if a.get("proxy") and not a["proxy"].lower().startswith("wg:") and a["proxy"] not in [p["proxy"] for p in pool]:
                pool.append({"proxy": a["proxy"]})
        return pool

    def _wg_refresh(self):
        used = {}
        for a in load_accounts():
            if str(a.get("proxy", "")).lower().startswith("wg:"):
                used.setdefault(a["proxy"][3:], []).append(a["name"])
        st = getattr(self, "_wg_status", {})
        self.wg_tree.delete(*self.wg_tree.get_children())
        for t_ in wg_list():
            self.wg_tree.insert("", "end", iid=t_["name"], values=(t_["name"], t_.get("endpoint", ""), t_["port"],
                                st.get(t_["name"], "-"), ", ".join(used.get(t_["name"], [])) or "-"))

    def _wg_import(self):
        paths = filedialog.askopenfilenames(title="WireGuard .conf", filetypes=[("WireGuard", "*.conf"), ("All", "*.*")])
        if paths:
            n = wg_import_files(paths)
            self.log_q.put(T(f"[VPN] {n} tunnel(s) added.", f"[VPN] {n} tunnel add hue."))
            self._wg_refresh()

    def _wg_check(self):
        self._wg_status = getattr(self, "_wg_status", {})
        names = list(self.wg_tree.selection()) or [t_["name"] for t_ in wg_list()]

        def work():
            for n in names:
                try:
                    port = wg_ensure(n, lambda m: self.log_q.put(m))
                    j = socks_ipinfo(port)
                    self._wg_status[n] = f"{j.get('query')} · {j.get('city')}, {j.get('countryCode')}"
                except Exception as e:
                    self._wg_status[n] = f"✖ {str(e)[:60]}"
                self.after(0, self._wg_refresh)
            self.log_q.put(T("[VPN] Tunnel check finished.", "[VPN] Tunnel check khatam."))
        threading.Thread(target=work, daemon=True).start()

    def _wg_assign(self):
        accs = self.acc_table.checked_accounts()
        tl = [t_["name"] for t_ in wg_list()]
        if not accs or not tl:
            messagebox.showwarning("VPN", T("Tick IDs in Accounts and import tunnels first.",
                                            "Pehle Accounts mein IDs tick karein aur tunnels daalein."))
            return
        for k, a in enumerate(accs):
            update_account(a["id"], proxy="wg:" + tl[k % len(tl)], geo=None)
        if len(accs) > len(tl):
            messagebox.showinfo("VPN", T(f"{len(accs)} IDs but only {len(tl)} tunnels — some IDs share a tunnel (same IP). "
                                         "Import more locations for a unique IP each.",
                                         f"{len(accs)} IDs aur sirf {len(tl)} tunnels — kuch IDs ka IP same hoga. "
                                         "Har ID ka alag IP chahiye to aur locations ki files daalein."))
        self.log_q.put(T(f"[VPN] Tunnels assigned to {len(accs)} ID(s).", f"[VPN] {len(accs)} IDs ko tunnels mil gaye."))
        self._refresh_all_panels(); self._wg_refresh()

    def _wg_remove(self):
        sel = set(self.wg_tree.selection())
        wg_save([t_ for t_ in wg_list() if t_["name"] not in sel])
        self._wg_refresh()

    def _proxy_refresh(self):
        self._wg_refresh()
        pool = self._proxy_pool()
        used = {}
        for a in load_accounts():
            if a.get("proxy"):
                used.setdefault(a["proxy"], []).append(a["name"])
        self.px_tree.delete(*self.px_tree.get_children())
        for i, p in enumerate(pool):
            pr = parse_proxy(p["proxy"]) or {}
            typ = (pr.get("server", "").split("://")[0] or "?").upper()
            st = p.get("status", "Unchecked")
            self.px_tree.insert("", "end", iid=str(i), values=(p["proxy"], typ, st,
                                f"{p['lat']} ms" if p.get("lat") else "-", p.get("ip", ""),
                                ", ".join(used.get(p["proxy"], [])) or "-"))

    def _proxy_save_pool(self, pool):
        save_settings({"proxy_pool": pool})

    def _proxy_add(self):
        pool = self._proxy_pool()
        have = {p["proxy"] for p in pool}
        n = 0
        for line in self.px_text.get("1.0", "end").splitlines():
            line = line.strip()
            if line and line not in have and parse_proxy(line):
                pool.append({"proxy": line}); have.add(line); n += 1
        self._proxy_save_pool(pool)
        self.px_text.delete("1.0", "end")
        self.log_q.put(T(f"[Proxy] {n} added.", f"[Proxy] {n} add hue."))
        self._proxy_refresh()

    def _proxy_check(self, only_sel):
        pool = self._proxy_pool()
        idx = [int(i) for i in self.px_tree.selection()] if only_sel else list(range(len(pool)))

        def work():
            for i in idx:
                ok, lat, info = check_proxy(pool[i]["proxy"])
                pool[i].update(status="Ready" if ok else ("Unknown" if ok is None else "Dead"),
                               lat=lat or None, ip=info)
                self._proxy_save_pool(pool)
                self.after(0, self._proxy_refresh)
            self.log_q.put(T("[Proxy] Check finished.", "[Proxy] Check khatam."))
        threading.Thread(target=work, daemon=True).start()

    def _proxy_assign(self):
        accs = self.acc_table.checked_accounts()
        pool = [p for p in self._proxy_pool() if p.get("status") != "Dead"]
        if not accs or not pool:
            messagebox.showwarning("Proxy", T("Tick IDs in Accounts and add proxies first.",
                                              "Pehle Accounts mein IDs tick karein aur proxies daalein."))
            return
        used = {a.get("proxy") for a in load_accounts() if a.get("proxy")}
        free = [p["proxy"] for p in pool if p["proxy"] not in used] or [p["proxy"] for p in pool]
        for k, a in enumerate(accs):
            update_account(a["id"], proxy=free[k % len(free)])
        self.log_q.put(T(f"[Proxy] Assigned to {len(accs)} ID(s).", f"[Proxy] {len(accs)} IDs ko de diye."))
        self._refresh_all_panels(); self._proxy_refresh()

    def _proxy_remove(self):
        pool = self._proxy_pool()
        sel = {int(i) for i in self.px_tree.selection()}
        self._proxy_save_pool([p for i, p in enumerate(pool) if i not in sel])
        self._proxy_refresh()

    # ---------- Logs ----------
    def _build_logs_page(self):
        f = self.pages["Logs"]
        nb = ttk.Notebook(f); nb.pack(fill="both", expand=True, padx=12, pady=6)
        lv = ttk.Frame(nb)
        nb.add(lv, text="  Logs Viewer  ")
        tb = ttk.Frame(lv); tb.pack(fill="x", padx=4, pady=4)
        self.lg_q = tk.StringVar()
        ttk.Entry(tb, textvariable=self.lg_q, width=28).pack(side="left")
        ttk.Label(tb, text=T(" ← search", " ← search")).pack(side="left")
        self.lg_q.trace_add("write", lambda *_: self._logs_refresh())
        self.lg_day = tk.StringVar(value="All days")
        self.lg_day_cb = ttk.Combobox(tb, textvariable=self.lg_day, width=14, state="readonly")
        self.lg_day_cb.pack(side="left", padx=6)
        self.lg_day_cb.bind("<<ComboboxSelected>>", lambda e: self._logs_refresh())
        self._lg_err_top = False
        ttk.Button(tb, text=T("Errors ↑↓", "Errors ↑↓"), command=self._logs_toggle_err).pack(side="left", padx=4)
        ttk.Button(tb, text=T("Refresh", "Refresh"), command=self._logs_refresh).pack(side="left", padx=4)
        ttk.Button(tb, text=T("Clear log", "Log saaf"), command=self._logs_clear).pack(side="left", padx=4)
        ttk.Button(tb, text="Pages", style="Green.TButton", command=self._page_manager).pack(side="right")
        cols = (("no", "No.", 50), ("t", "Time", 150), ("acc", "ID", 50), ("page", "Page", 200), ("ip", "IP", 110),
                ("st", "✔/✘", 50), ("posted", "Posted", 70), ("files", "Files", 60), ("mon", "Monetization", 140),
                ("reach", "Reach", 90), ("fol", "Followers", 90), ("pst", "Page status", 190), ("err", "Error", 260))
        tf = ttk.Frame(lv); tf.pack(fill="both", expand=True, padx=4)
        self.lg_posts = ttk.Treeview(tf, columns=[c for c, _, _ in cols], show="headings")
        for c, t, w in cols:
            self.lg_posts.heading(c, text=t)
            self.lg_posts.column(c, width=w, minwidth=40, anchor=("center" if c in ("no", "acc", "st", "posted", "files") else "w"),
                                 stretch=c in ("page", "err"))
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.lg_posts.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.lg_posts.xview)
        self.lg_posts.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.lg_posts.grid(row=0, column=0, sticky="nsew"); ys.grid(row=0, column=1, sticky="ns"); xs.grid(row=1, column=0, sticky="ew")
        tf.grid_rowconfigure(0, weight=1); tf.grid_columnconfigure(0, weight=1)
        self.lg_posts.tag_configure("mon", background="#0f7a3a", foreground="white")
        self.lg_posts.tag_configure("issue", background="#7a6a00", foreground="white")
        self.lg_posts.tag_configure("fail", foreground="#ff8080")
        self.lg_posts.bind("<Double-1>", lambda e: self._page_detail(self.lg_posts.set(self.lg_posts.identify_row(e.y), "page"))
                           if self.lg_posts.identify_row(e.y) else None)
        self.lg_act = tk.Text(nb, font=("Consolas", 10), relief="flat")
        nb.add(self.lg_act, text="  Activity Log  ")

    def _logs_toggle_err(self):
        self._lg_err_top = not self._lg_err_top
        self._logs_refresh()

    def _logs_clear(self):
        if messagebox.askyesno("Logs", T("Clear the Logs Viewer list?", "Logs Viewer ki list saaf karein?")):
            with _PLOG_LOCK:
                json.dump([], open(_plog_path(), "w", encoding="utf-8"))
            self._logs_refresh()

    def _logs_refresh(self):
        lst = post_log_load()
        days = sorted({e["t"][:10] for e in lst}, reverse=True)
        try:
            self.lg_day_cb.config(values=["All days"] + days)
        except Exception:
            pass
        day = self.lg_day.get() if hasattr(self, "lg_day") else "All days"
        q = (self.lg_q.get() if hasattr(self, "lg_q") else "").strip().lower()
        stats = page_stats_load()
        rows = []
        for e in lst:
            if day not in ("", "All days") and not e["t"].startswith(day):
                continue
            ps = stats.get(e.get("page", ""), {})
            vals = [e["t"], e.get("acc", ""), e.get("page", ""), e.get("ip", ""), "✔" if e.get("ok") else "✘",
                    e.get("posted", ""), e.get("files", ""), ps.get("monetization", ""),
                    short_num(ps["reach"]) if ps.get("reach") not in (None, "") else "",
                    short_num(ps["followers"]) if ps.get("followers") not in (None, "") else "",
                    ps.get("status", ""), e.get("err", "")]
            if q and q not in " ".join(str(v) for v in vals).lower():
                continue
            rows.append((e, ps, vals))
        if self._lg_err_top:
            rows.sort(key=lambda r: 0 if (not r[0].get("ok") or (r[1].get("status") not in (None, "", "Good"))) else 1)
        else:
            rows.sort(key=lambda r: 1 if (not r[0].get("ok") or (r[1].get("status") not in (None, "", "Good"))) else 0)
        self.lg_posts.delete(*self.lg_posts.get_children())
        for i, (e, ps, vals) in enumerate(rows[:1500], 1):
            tag = ()
            if not e.get("ok"):
                tag = ("fail",)
            elif ps.get("status") and ps.get("status") not in ("Good", "Unknown"):
                tag = ("issue",)
            elif ps.get("monetization"):
                tag = ("mon",)
            self.lg_posts.insert("", "end", values=[i] + vals, tags=tag)
        self.lg_act.delete("1.0", "end")
        self.lg_act.insert("end", "\n".join(self.log_buffer[-300:]))

    # ---------- PAGE MANAGER (Bob Prime jaisa) ----------
    def _pm_rows(self):
        import re as _re
        stats = page_stats_load()
        rows, seen = [], set()
        accs = load_accounts()
        api_pages = api_load().get("pages", {})
        owner_of = {}
        for pid, p in api_pages.items():
            owner_of[p.get("name", "")] = (pid, p.get("owner", ""))
        for i, a in enumerate(accs, 1):
            for p in a.get("pages") or []:
                nm = p.get("name", "")
                if not nm or nm in seen:
                    continue
                seen.add(nm)
                m = _re.search(r"id=(\d+)", p.get("url", ""))
                pid = (stats.get(nm, {}).get("page_id") or (m.group(1) if m else "") or owner_of.get(nm, ("", ""))[0])
                rows.append({"no": i, "acc_id": a["id"], "name": nm, "pid": pid, "group": a.get("group", ""),
                             "st": stats.get(nm, {})})
        for pid, p in api_pages.items():
            nm = p.get("name", "")
            if not nm or nm in seen:
                continue
            seen.add(nm)
            ow = p.get("owner", "")
            no = next((i for i, a in enumerate(accs, 1) if a["id"] == ow), "")
            grp = next((a.get("group", "") for a in accs if a["id"] == ow), "")
            rows.append({"no": no, "acc_id": ow, "name": nm, "pid": pid, "group": grp, "st": stats.get(nm, {})})
        return rows

    def _page_manager(self):
        w = tk.Toplevel(self); w.title("Page Manager"); w.configure(bg=PAL["bg"]); w.transient(self)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        ww, wh = min(1500, int(sw * 0.85)), min(800, int(sh * 0.85))
        w.geometry(f"{ww}x{wh}+{max(0, (sw - ww) // 2)}+{max(0, (sh - wh) // 4)}")
        hdr = tk.Frame(w, bg="#0d2a4f"); hdr.pack(fill="x")
        tk.Label(hdr, text="  Page Manager", bg="#0d2a4f", fg="#ffd34d", font=("Segoe UI", 16, "bold")).pack(side="left", pady=8)
        tb = ttk.Frame(w); tb.pack(fill="x", padx=8, pady=6)
        sort_v = tk.StringVar(value="Sort by ID"); grp_v = tk.StringVar(value="All Groups"); q_v = tk.StringVar()
        ttk.Combobox(tb, textvariable=sort_v, state="readonly", width=16,
                     values=["Sort by ID", "Sort by Name", "Sort by Followers", "Sort by Reach", "Sort by Posts"]).pack(side="left")
        groups = sorted({a.get("group", "") for a in load_accounts() if a.get("group")})
        ttk.Combobox(tb, textvariable=grp_v, state="readonly", width=16, values=["All Groups"] + groups).pack(side="left", padx=6)
        ttk.Entry(tb, textvariable=q_v, width=24).pack(side="left", padx=6)
        ttk.Label(tb, text="← search").pack(side="left")
        cols = (("no", "ID", 50), ("name", "Page Name", 230), ("pid", "Page ID", 160), ("fol", "Followers", 90),
                ("mon", "Monetization", 150), ("reach", "Reach", 100), ("st", "Status", 200), ("upd", "Last Update", 110),
                ("grp", "Group", 110), ("posts", "Posts", 70))
        tf = ttk.Frame(w); tf.pack(fill="both", expand=True, padx=8, pady=4)
        tv = ttk.Treeview(tf, columns=[c for c, _, _ in cols], show="headings", selectmode="extended")
        for c, t, wd in cols:
            tv.heading(c, text=t)
            tv.column(c, width=wd, minwidth=40, anchor=("center" if c in ("no", "fol", "reach", "posts", "upd") else "w"),
                      stretch=c in ("name", "st"))
        ys = ttk.Scrollbar(tf, orient="vertical", command=tv.yview); tv.configure(yscrollcommand=ys.set)
        tv.pack(side="left", fill="both", expand=True); ys.pack(side="right", fill="y")
        tv.tag_configure("mon", background="#0f7a3a", foreground="white")
        tv.tag_configure("issue", background="#7a6a00", foreground="white")
        info = ttk.Label(w, text="", style="Muted.TLabel"); info.pack(anchor="w", padx=10)

        def num(v):
            try:
                return float(v)
            except Exception:
                return -1

        def draw(*_):
            rows = self._pm_rows()
            if grp_v.get() not in ("", "All Groups"):
                rows = [r for r in rows if r["group"] == grp_v.get()]
            q = q_v.get().strip().lower()
            if q:
                rows = [r for r in rows if q in (r["name"] + " " + str(r["pid"])).lower()]
            sk = sort_v.get()
            if sk == "Sort by Name":
                rows.sort(key=lambda r: r["name"].lower())
            elif sk == "Sort by Followers":
                rows.sort(key=lambda r: -num(r["st"].get("followers")))
            elif sk == "Sort by Reach":
                rows.sort(key=lambda r: -num(r["st"].get("reach")))
            elif sk == "Sort by Posts":
                rows.sort(key=lambda r: -num(r["st"].get("posts")))
            else:
                rows.sort(key=lambda r: (r["no"] if isinstance(r["no"], int) else 10 ** 6, r["name"].lower()))
            tv.delete(*tv.get_children())
            for k, r in enumerate(rows):
                st = r["st"]
                tag = ("issue",) if st.get("status") not in (None, "", "Good", "Unknown") else (("mon",) if st.get("monetization") else ())
                tv.insert("", "end", iid=f"r{k}", tags=tag, values=(
                    r["no"], r["name"], r["pid"],
                    short_num(st["followers"]) if st.get("followers") not in (None, "") else "",
                    st.get("monetization", ""),
                    f"{int(st['reach']):,}" if str(st.get("reach", "")).isdigit() else st.get("reach", ""),
                    st.get("status", ""), st.get("updated", ""), r["group"],
                    st.get("posts", "") if st.get("posts") is not None else ""))
            tv._rows = rows
            info.config(text=T(f"{len(rows)} page(s).  Followers / Reach / Posts come from the API.  "
                               f"Monetization + Status: tick IDs and press 'Check status (browser)'.",
                               f"{len(rows)} page.  Followers / Reach / Posts API se.  "
                               f"Monetization + Status: 'Check status (browser)' dabayein."))
        for v in (sort_v, grp_v, q_v):
            v.trace_add("write", draw)

        def refresh_api():
            pages = api_load().get("pages", {})
            if not pages:
                messagebox.showinfo("Pages", T("No API pages yet (API Pages → Fetch / CONNECT).",
                                               "Abhi koi API page nahi (API Pages → Fetch / CONNECT)."), parent=w)
                return
            self.log_q.put(T(f"[Pages] Updating {len(pages)} page(s) from API...", f"[Pages] {len(pages)} page API se update..."))

            def work():
                for pid, p in pages.items():
                    p = dict(p); p["id"] = pid
                    api_page_stats(pid, p)
                self.log_q.put(T("[Pages] Updated.", "[Pages] Update ho gaye."))
                try:
                    w.after(0, draw)
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()

        def check_browser():
            sel = [tv._rows[int(i[1:])] for i in tv.selection()] if getattr(tv, "_rows", None) else []
            ids = list(dict.fromkeys(r["acc_id"] for r in sel if r.get("acc_id")))
            if not ids:
                messagebox.showinfo("Pages", T("Select page rows (Ctrl+click), then press this button.",
                                               "Page rows chunein (Ctrl+click), phir ye button dabayein."), parent=w)
                return
            accs = [a for a in load_accounts() if a["id"] in ids]
            self._status_selected(False, accs)
        ttk.Button(tb, text=T("⟳ Refresh (API)", "⟳ Refresh (API)"), command=refresh_api).pack(side="right", padx=4)
        ttk.Button(tb, text=T("Check status (browser)", "Status check (browser)"), command=check_browser).pack(side="right", padx=4)
        tv.bind("<Double-1>", lambda e: (lambda r: r and self._page_detail(tv.set(r, "name")))(tv.identify_row(e.y)))
        draw()

    def _page_detail(self, name):
        if not name:
            return
        st = page_stats_load().get(name, {})
        lines = [f"{k}: {v}" for k, v in st.items()]
        hist = [e for e in post_log_load() if e.get("page") == name][:15]
        lines += ["", T("Last posts:", "Aakhri posts:")] + [
            f"{e['t']}  {'✔' if e.get('ok') else '✘'}  {e.get('posted', '')}  {e.get('err', '')}" for e in hist]
        messagebox.showinfo(name, "\n".join(lines) or name)


# ============================================================
# CONTENT SOURCES — page ke naam wale folders + TikTok creator se HD videos
# ============================================================
def _norm_name(s):
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", (s or "").lower())


def media_root():
    return (load_settings().get("media_root") or "").strip()


def page_media_folder(page_name):
    """Page ka folder: (1) khud chuna hua mapping, (2) media root mein page ke naam wala folder."""
    mp = load_settings().get("page_folders", {})
    if page_name in mp and os.path.isdir(mp[page_name]):
        return mp[page_name]
    root = media_root()
    if root and os.path.isdir(root) and page_name:
        want = _norm_name(page_name)
        try:
            subs = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
        except Exception:
            subs = []
        for d in subs:
            if _norm_name(d) == want:
                return os.path.join(root, d)
        for d in subs:                         # thora sa farq bhi chal jaye
            nd = _norm_name(d)
            if nd and (nd in want or want in nd):
                return os.path.join(root, d)
    return ""


CAPTIONS_FILE = "captions.txt"      # ek hi file: har line = video file naam <TAB> caption (tarteeb = post ki tarteeb)


def captions_load(folder):
    out = {}
    try:
        fp = os.path.join(folder, CAPTIONS_FILE)
        if os.path.isfile(fp):
            for ln in open(fp, encoding="utf-8", errors="ignore"):
                ln = ln.rstrip("\n")
                if "\t" in ln:
                    f_, c_ = ln.split("\t", 1)
                    out[f_] = c_.replace("\\n", "\n")
    except Exception:
        pass
    return out


def captions_save(folder, mp):
    try:
        with open(os.path.join(folder, CAPTIONS_FILE), "w", encoding="utf-8") as f:
            for k, v in mp.items():
                f.write(k + "\t" + (v or "").replace("\r", "").replace("\n", "\\n") + "\n")
    except Exception:
        pass


def captions_add(folder, fname, cap, front=False):
    mp = captions_load(folder)
    mp.pop(fname, None)
    if front:
        mp = dict([(fname, cap)] + list(mp.items()))
    else:
        mp[fname] = cap
    captions_save(folder, mp)


def captions_remove(folder, fname):
    mp = captions_load(folder)
    if fname in mp:
        cap = mp.pop(fname); captions_save(folder, mp); return cap
    return None


def page_files(page_name, kind):
    fo = page_media_folder(page_name)
    files = list_media_in_folder(fo, "photo" if kind == "photo" else "video") if fo else []
    order = list(captions_load(fo).keys()) if fo else []
    if order:
        pos = {n: i for i, n in enumerate(order)}
        files.sort(key=lambda p: (pos.get(os.path.basename(p), 10 ** 6), os.path.basename(p)))
    return files


def sidecar_caption(path):
    """Caption: video ki apni .txt (purana tareeqa) ya folder ki captions.txt. None = koi record nahi."""
    try:
        side = os.path.splitext(path)[0] + ".txt"
        if os.path.isfile(side):
            return open(side, encoding="utf-8", errors="ignore").read().strip()
        mp = captions_load(os.path.dirname(path))
        if os.path.basename(path) in mp:
            return mp[os.path.basename(path)]
    except Exception:
        pass
    return None


def clean_caption(text, max_tags=4):
    """Caption: text wahi, hashtags zyada se zyada max_tags (pehle wale)."""
    import re as _re
    text = text or ""
    tags = []
    for t in _re.findall(r"#[^\s#]+", text):
        if t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    body = _re.sub(r"#[^\s#]+", "", text)
    body = _re.sub(r"[ \t]+", " ", body).strip()
    return (body + ("\n\n" if body and tags else "") + " ".join(tags[:max_tags])).strip()


def clean_page_label(name):
    """Purani version ke ghalat page naam saaf: 'Profile picture for X' -> 'X'. Ad/Promote -> '' (hata do)."""
    import re as _re
    t = (name or "").split("\n")[0].strip()
    t = _re.sub(r"^(profile picture (for|of)\s+)", "", t, flags=_re.I)
    t = _re.sub(r"('s)?\s*profile picture$", "", t, flags=_re.I).strip(" ·•✓✔")
    if t.lower() in ("promote", "create ad", "boost", "boost post", "ad center", "see all", "posts", "create new page"):
        return ""
    return t


def migrate_profile_names():
    """P001 -> P01 (naam aur session folder dono)."""
    import re as _re
    try:
        profs = profiles_list()
        mp = {}
        for p in profs:
            m = _re.fullmatch(r"P0*(\d+)", p)
            if m and p != f"P{int(m.group(1)):02d}":
                mp[p] = f"P{int(m.group(1)):02d}"
        if not mp:
            return
        save_settings({"profiles": [mp.get(p, p) for p in profs]})
        accs = load_accounts()
        for a in accs:
            if a.get("profile") in mp:
                a["profile"] = mp[a["profile"]]
        save_accounts(accs)
        for o, n in mp.items():
            od = os.path.join(SESSIONS_DIR, "profile_" + o); nd = os.path.join(SESSIONS_DIR, "profile_" + n)
            if os.path.isdir(od) and not os.path.exists(nd):
                try:
                    os.rename(od, nd)
                except Exception:
                    pass
    except Exception:
        pass


def migrate_page_names():
    """Ek dafa: accounts ke pages, content sources aur page_folders ke naam saaf karo."""
    try:
        accs = load_accounts(); ch = False
        for a in accs:
            pg = a.get("pages") or []
            out = []
            for p in pg:
                u = (p.get("url") or "").lower()
                if any(b in u for b in ("ad_center", "adsmanager", "/promote", "boost")):
                    ch = True; continue
                nm = clean_page_label(p.get("name", ""))
                if not nm:
                    ch = True; continue
                if nm != p.get("name"):
                    p["name"] = nm; ch = True
                out.append(p)
            if ch:
                a["pages"] = out
        if ch:
            save_accounts(accs)
        st = load_settings()
        srcs = st.get("content_sources", []); ch2 = False
        for x in srcs:
            nm = clean_page_label(x.get("page", ""))
            if nm and nm != x.get("page"):
                x["page"] = nm; ch2 = True
        pf = st.get("page_folders", {}); npf = {}
        for k, v in pf.items():
            nk = clean_page_label(k) or k
            npf[nk] = v; ch2 = ch2 or nk != k
        if ch2:
            save_settings({"content_sources": srcs, "page_folders": npf})
    except Exception:
        pass


def sources_list():
    return load_settings().get("content_sources", [])


def sources_save(lst):
    save_settings({"content_sources": lst})


def _safe_fname(text, n=70):
    import re as _re
    t = _re.sub(r"#[^\s#]+", "", text or "")
    t = _re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", t)
    t = _re.sub(r"\s+", " ", t).strip(" .")
    return t[:n].strip() or "video"


def _tt_id(url):
    import re as _re
    m = _re.search(r"/video/(\d+)", url or "") or _re.search(r"(\d{15,})", url or "")
    return m.group(1) if m else ""


_FETCH_LOCK = threading.Lock()


def fetch_tiktok(src, log=print, stop=None):
    """TikTok creator se videos HD (bina watermark) page ke folder mein.
    Tarteeb: pehle sab se NAYI (upar wali) jo abhi nahi lagi, phir purani wahin se jahan ruke the.
    Koi video do dafa nahi. Har video ka naam = TikTok caption, saath .txt (caption + max 4 hashtags).
    'last_link' diya ho to us video tak (aur us se upar ki) sab 'ho chuki' maan li jati hain."""
    try:
        import yt_dlp
    except Exception:
        log(T("Installing video downloader (yt-dlp)...", "Video downloader (yt-dlp) install ho raha hai..."))
        subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], check=False, **_NOWIN)
        import yt_dlp
    page = src.get("page", "")
    folder = page_media_folder(page) or src.get("folder") or ""
    if not folder:
        root = media_root() or os.path.join(DATA_DIR, "media")
        folder = os.path.join(root, _safe_fname(page, 60) or "downloads")
    os.makedirs(folder, exist_ok=True)
    max_n = int(src.get("max", 10) or 10)
    max_tags = int(src.get("max_tags", 4) or 4)
    done = list(src.get("done") or [])
    # purani version ki archive file bhi parh lo (phir hata do)
    old_arc = os.path.join(folder, ".downloaded.txt")
    if os.path.isfile(old_arc):
        try:
            for ln in open(old_arc, encoding="utf-8", errors="ignore"):
                p_ = ln.split()
                if len(p_) >= 2 and p_[1] not in done:
                    done.append(p_[1])
            os.remove(old_arc)
        except Exception:
            pass
    done_set = set(done)
    url = src.get("url", "")
    log(T(f"[Source] {page}: fetch started — reading creator's video list...",
          f"[Source] {page}: fetch shuru — creator ki video list parh rahe hain..."))
    t0 = time.time()

    def list_ids(limit):
        o = {"extract_flat": "in_playlist", "playlistend": limit, "quiet": True, "no_warnings": True,
             "ignoreerrors": True, "skip_download": True}
        with yt_dlp.YoutubeDL(o) as y:
            inf = y.extract_info(url, download=False) or {}
        ents = inf.get("entries") or ([inf] if inf.get("id") else [])
        out = []
        for e in ents:
            if e and e.get("id"):
                out.append((str(e["id"]), e.get("url") or e.get("webpage_url") or
                            f"https://www.tiktok.com/@{(inf.get('uploader_id') or inf.get('uploader') or 'x')}/video/{e['id']}"))
        return out

    # kitni list chahiye: done + max se thora zyada (purani jagah tak pohanchne ke liye)
    limit = max(30, len(done_set) + max_n * 2)
    cut_id = _tt_id(src.get("last_link", "")) if src.get("last_link") and not src.get("cut_applied") else ""
    ids = []
    try:
        ids = list_ids(limit)
        if cut_id and cut_id not in [i for i, _ in ids]:
            for lim in (200, 600, 1500):              # cutoff wali video tak list barhao
                ids = list_ids(lim)
                if cut_id in [i for i, _ in ids] or len(ids) < lim:
                    break
    except Exception as e:
        log(f"[Source] {page}: list error: {e}")
    if not ids:
        log(T(f"[Source] {page}: no videos found (check the link).", f"[Source] {page}: koi video nahi mili (link check karein)."))
        src["last"] = time.strftime("%Y-%m-%d %H:%M"); src["last_new"] = 0
        return 0
    if cut_id:
        lst_ids = [i for i, _ in ids]
        if cut_id in lst_ids:
            for i in lst_ids[:lst_ids.index(cut_id) + 1]:     # upar se cutoff tak = ho chuki
                if i not in done_set:
                    done.append(i); done_set.add(i)
            src["cut_applied"] = True
            log(T(f"[Source] {page}: marked {lst_ids.index(cut_id) + 1} already-uploaded video(s) as done.",
                  f"[Source] {page}: {lst_ids.index(cut_id) + 1} pehle se lagi videos 'done' kar di."))
        else:
            log(T(f"[Source] {page}: 'last uploaded' video not found in the list — starting from newest.",
                  f"[Source] {page}: 'last uploaded' video list mein nahi mili — sab se nayi se shuru."))
    todo = [(i, u) for i, u in ids if i not in done_set][:max_n]
    # creator ne naye daale (pichhli sab se upar wali se upar) -> post mein sab se aage
    prev_top = src.get("top_id", "")
    lst_all = [i for i, _ in ids]
    top_new = set(lst_all[:lst_all.index(prev_top)]) if prev_top in lst_all else set()
    if lst_all:
        src["top_id"] = lst_all[0]
    log(T(f"[Source] {page}: list ready ({len(ids)} videos, {int(time.time() - t0)}s). Downloading {len(todo)} new...",
          f"[Source] {page}: list tayyar ({len(ids)} videos, {int(time.time() - t0)}s). {len(todo)} nayi download..."))
    seq = int(src.get("seq", 0) or 0)
    n_new = 0
    dl_opts = {"format": "best[vcodec^=h264][ext=mp4]/best[ext=mp4]/best", "quiet": True, "no_warnings": True, "noprogress": True,
               "retries": 3, "ignoreerrors": False}
    for k, (vid, vurl) in enumerate(todo, 1):
        if stop and stop():
            break
        try:
            seq += 1
            tmpl = os.path.join(folder, f"_dl_{vid}.%(ext)s")
            with yt_dlp.YoutubeDL(dict(dl_opts, outtmpl=tmpl)) as y:
                info = y.extract_info(vurl, download=True) or {}
                fn = (info.get("requested_downloads") or [{}])[0].get("filepath") or y.prepare_filename(info)
            if not fn or not os.path.isfile(fn):
                cand = [os.path.join(folder, f) for f in os.listdir(folder) if f.startswith(f"_dl_{vid}.")]
                fn = cand[0] if cand else ""
            if not fn:
                raise RuntimeError("file not saved")
            raw = info.get("description") or ""
            if raw.strip().lower() in ("", (info.get("title") or "").strip().lower()) and not raw.strip():
                raw = ""
            cap = clean_caption(raw, max_tags)          # hashtags na hon to koi nahi lagenge
            body = _safe_fname(cap) if _safe_fname(cap) != "video" else ""
            if not body:                                  # naam hi nahi -> page ka naam
                cap = cap or page
                body = _safe_fname(page, 60) or "video"
            ext = os.path.splitext(fn)[1]
            final = os.path.join(folder, body + ext); k2 = 2
            while os.path.exists(final):
                final = os.path.join(folder, f"{body} ({k2}){ext}"); k2 += 1
            os.replace(fn, final)
            captions_add(folder, os.path.basename(final), cap, front=vid in top_new)
            done.append(vid); done_set.add(vid); n_new += 1
            src["seq"] = seq
            log(T(f"[Source] {page}: {k}/{len(todo)} downloaded — {os.path.basename(final)}",
                  f"[Source] {page}: {k}/{len(todo)} download — {os.path.basename(final)}"))
        except Exception as e:
            log(f"[Source] {page}: {k}/{len(todo)} skip ({vid}): {str(e)[:120]}")
    src["done"] = done[-5000:]
    src["last"] = time.strftime("%Y-%m-%d %H:%M"); src["last_new"] = n_new; src["folder"] = folder
    log(T(f"[Source] {page}: finished — {n_new} new video(s) → {folder}",
          f"[Source] {page}: mukammal — {n_new} nayi video(s) → {folder}"))
    return n_new


def sources_auto_loop(log, stop_event):
    """Har X ghante TikTok sources se nayi videos."""
    while not stop_event.is_set():
        try:
            s = load_settings()
            hrs = float(s.get("sources_every_h", 0) or 0)
            if hrs > 0:
                lst = sources_list(); changed = False
                for src in lst:
                    last = src.get("last_ts", 0)
                    if time.time() - last >= hrs * 3600 and not _FETCH_LOCK.locked():
                        with _FETCH_LOCK:
                            fetch_tiktok(src, log)
                        src["last_ts"] = time.time(); changed = True
                if changed:
                    sources_save(lst)
        except Exception as e:
            log(f"[Source] auto: {e}")
        stop_event.wait(300)


class SourcesMixin:
    def _build_sources_page(self):
        f = self._scroll_area(self.pages["Sources"], full=True)
        top = ttk.LabelFrame(f, text=T("PAGE FOLDERS — one main folder, inside it a folder for each page (same name as the page)",
                                       "PAGE FOLDERS — ek main folder, andar har page ke naam ka folder"))
        top.pack(fill="x", padx=20, pady=6)
        r = ttk.Frame(top); r.pack(fill="x", padx=6, pady=4)
        ttk.Label(r, text=T("Main media folder:", "Main media folder:")).pack(side="left")
        self.src_root = tk.StringVar(value=media_root())
        ttk.Entry(r, textvariable=self.src_root, width=60).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(r, text="...", width=3, command=self._src_pick_root).pack(side="left")
        ttk.Button(r, text=T("Match folders to pages", "Folders pages se milao"), style="Accent.TButton",
                   command=self._src_match).pack(side="left", padx=4)
        self.src_map = ttk.Treeview(top, columns=("page", "folder", "files"), show="headings", height=4)
        for c, t_, w in (("page", "Page", 260), ("folder", "Folder (auto)", 520), ("files", "Videos", 90)):
            self.src_map.heading(c, text=t_, anchor="w"); self.src_map.column(c, width=w, anchor="w")
        self.src_map.pack(fill="x", padx=6, pady=4)
        mb = ttk.Frame(top); mb.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(mb, text=T("Set folder for selected page", "Selected page ka folder chuno"),
                   command=self._src_set_folder).pack(side="left")
        ttk.Label(mb, text=T("Posting + API Pages take videos from these folders automatically.",
                             "Posting + API Pages in folders se videos khud uthate hain."), style="Muted.TLabel").pack(side="left", padx=8)

        tk_ = ttk.LabelFrame(f, text=T("TIKTOK CREATORS → download HD → upload with caption (max 4 hashtags)",
                                       "TIKTOK CREATORS → HD download → caption ke sath upload (max 4 hashtags)"))
        tk_.pack(fill="x", padx=20, pady=6)
        a = ttk.Frame(tk_); a.pack(fill="x", padx=6, pady=4)
        ttk.Label(a, text=T("Page:", "Page:")).pack(side="left")
        self.src_page = tk.StringVar()
        self.src_page_cb = ttk.Combobox(a, textvariable=self.src_page, width=26)
        self.src_page_cb.pack(side="left", padx=4)
        ttk.Label(a, text=T("TikTok link:", "TikTok link:")).pack(side="left")
        self.src_url = tk.StringVar()
        ttk.Entry(a, textvariable=self.src_url, width=40).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Label(a, text=T("Max/fetch:", "Max/fetch:")).pack(side="left")
        self.src_max = tk.StringVar(value="10")
        tk.Spinbox(a, from_=1, to=100, textvariable=self.src_max, width=4).pack(side="left", padx=3)
        ttk.Button(a, text=T("Add", "Add"), command=self._src_add).pack(side="left", padx=4)
        a2 = ttk.Frame(tk_); a2.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Label(a2, text=T("Last uploaded video link (optional):", "Aakhri lagi hui video ka link (optional):")).pack(side="left")
        self.src_last = tk.StringVar()
        ttk.Entry(a2, textvariable=self.src_last, width=50).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(a2, text=T("Set for selected row", "Selected row par lagao"), command=self._src_set_last).pack(side="left", padx=4)
        ttk.Label(tk_, text=T("Order: newest videos first → then older ones from where it stopped. A video is never used twice. "
                              "If you were uploading this creator manually, paste the link of the last video you uploaded — "
                              "that video and the ones above it are treated as done.",
                              "Tarteeb: pehle sab se nayi videos → phir purani wahin se jahan ruke the. Koi video do dafa nahi. "
                              "Agar ye creator pehle manually lagate the to aakhri lagi hui video ka link do — "
                              "woh aur us se upar wali 'done' maan li jayengi."),
                  style="Muted.TLabel", wraplength=1100, justify="left").pack(anchor="w", padx=8)
        self.src_tree = ttk.Treeview(tk_, columns=("page", "url", "max", "done", "last", "new"), show="headings", height=3)
        for c, t_, w in (("page", "Page", 220), ("url", "TikTok link", 380), ("max", "Max", 60),
                         ("done", "Done", 70), ("last", "Last fetch", 150), ("new", "New", 60)):
            self.src_tree.heading(c, text=t_, anchor="w"); self.src_tree.column(c, width=w, anchor="w")
        self.src_tree.pack(fill="both", expand=True, padx=6, pady=4)
        b = ttk.Frame(tk_); b.pack(fill="x", padx=6, pady=4)
        ttk.Button(b, text=T("Fetch selected now", "Selected abhi lao"), command=lambda: self._src_fetch(True)).pack(side="left")
        ttk.Button(b, text=T("Fetch all now", "Sab abhi lao"), command=lambda: self._src_fetch(False)).pack(side="left", padx=4)
        ttk.Button(b, text=T("Remove", "Hatao"), command=self._src_remove).pack(side="left")
        ttk.Label(b, text=T("   Auto fetch every", "   Har")).pack(side="left")
        self.src_every = tk.StringVar(value=str(load_settings().get("sources_every_h", 0)))
        tk.Spinbox(b, from_=0, to=72, textvariable=self.src_every, width=4,
                   command=lambda: save_settings({"sources_every_h": self._iv(self.src_every.get())})).pack(side="left", padx=3)
        ttk.Label(b, text=T("hours (0 = off)", "ghante (0 = band)")).pack(side="left")

        def _save_every(*_):
            try:
                save_settings({"sources_every_h": float(self.src_every.get() or 0)})
            except Exception:
                pass
        self.src_every.trace_add("write", _save_every)
        ttk.Label(tk_, text=T("Use only your own creator accounts / content you have rights to.",
                              "Sirf apne creator accounts / apna content use karein."), style="Muted.TLabel").pack(anchor="w", padx=8)
        self._src_stop = threading.Event()
        threading.Thread(target=sources_auto_loop, args=(lambda m: self.log_q.put(m), self._src_stop), daemon=True).start()

    def _all_page_names(self):
        names = []
        try:
            ticked = set(self.acc_table.checked) if hasattr(self, "acc_table") else set()
        except Exception:
            ticked = set()
        for a in load_accounts():
            if ticked and a["id"] not in ticked:
                continue
            for p in a.get("pages") or []:
                if p.get("name") and p["name"] not in names:
                    names.append(p["name"])
            if a.get("page_name") and a["page_name"] not in names:
                names.append(a["page_name"])
        for p in api_load().get("pages", {}).values():
            if ticked and p.get("owner") and p["owner"] not in ticked:
                continue
            if ticked and not p.get("owner"):
                continue
            if p.get("name") and p["name"] not in names:
                names.append(p["name"])
        return names

    def _src_refresh(self):
        names = self._all_page_names()
        self.src_page_cb.config(values=names)
        self.src_map.delete(*self.src_map.get_children())
        for i, n in enumerate(names):
            fo = page_media_folder(n)
            cnt = len(list_media_in_folder(fo, "video")) if fo else 0
            self.src_map.insert("", "end", iid=f"p{i}", values=(n, fo or "—", cnt))
        self.src_tree.delete(*self.src_tree.get_children())
        for i, s_ in enumerate(sources_list()):
            self.src_tree.insert("", "end", iid=str(i), values=(s_.get("page"), s_.get("url"), s_.get("max", 10),
                                                                len(s_.get("done") or []),
                                                                s_.get("last", "-"), s_.get("last_new", "-")))

    def _src_pick_root(self):
        d = filedialog.askdirectory()
        if d:
            self.src_root.set(d); save_settings({"media_root": d}); self._src_refresh()

    def _src_match(self):
        save_settings({"media_root": self.src_root.get().strip()})
        # API pages ke folders bhi bhar do (agar khali)
        n = 0
        with _API_LOCK:
            d = api_load()
            for pid, p in d["pages"].items():
                fo = page_media_folder(p.get("name", ""))
                if fo and fo not in p.get("folders", []):
                    p["folders"] = [fo] + list(p.get("folders", [])); n += 1
            api_save(d)
        self._src_refresh()
        self.log_q.put(T(f"[Source] Folders matched. API pages updated: {n}", f"[Source] Folders mil gaye. API pages update: {n}"))

    def _src_set_folder(self):
        sel = self.src_map.selection()
        if not sel:
            return
        page = self.src_map.set(sel[0], "page")
        d = filedialog.askdirectory()
        if d:
            mp = load_settings().get("page_folders", {}); mp[page] = d
            save_settings({"page_folders": mp}); self._src_refresh()

    def _src_add(self):
        page, url = self.src_page.get().strip(), self.src_url.get().strip()
        if not page or not url.startswith("http"):
            messagebox.showwarning("Source", T("Choose a page and paste a TikTok link.", "Page chuno aur TikTok link daalo."))
            return
        lst = sources_list()
        lst.append({"page": page, "url": url, "max": self._iv(self.src_max.get()) or 10, "max_tags": 4,
                    "last_link": self.src_last.get().strip()})
        sources_save(lst); self.src_url.set(""); self.src_last.set(""); self._src_refresh()
        self.log_q.put(T(f"[Source] Added: {page} ← {url}. Select the row and press 'Fetch selected now'.",
                         f"[Source] Add ho gaya: {page} ← {url}. Row select kar ke 'Fetch selected now' dabayein."))

    def _src_set_last(self):
        sel = self.src_tree.selection()
        link = self.src_last.get().strip()
        if not sel or not link:
            messagebox.showwarning("Source", T("Select a row and paste the last uploaded video link.",
                                               "Row select karein aur aakhri lagi hui video ka link daalein."))
            return
        lst = sources_list()
        for i in sel:
            lst[int(i)]["last_link"] = link
            lst[int(i)]["cut_applied"] = False
        sources_save(lst); self.src_last.set(""); self._src_refresh()
        self.log_q.put(T("[Source] Last uploaded link saved — applied on next fetch.",
                         "[Source] Aakhri link save — agli fetch par lagega."))

    def _src_remove(self):
        sel = {int(i) for i in self.src_tree.selection()}
        sources_save([s_ for i, s_ in enumerate(sources_list()) if i not in sel]); self._src_refresh()

    def _src_fetch(self, only_sel):
        lst = sources_list()
        if not lst:
            messagebox.showinfo("Source", T("Add a TikTok link first.", "Pehle TikTok link add karein."))
            return
        if only_sel:
            idx = [int(i) for i in self.src_tree.selection()]
            if not idx and len(lst) == 1:
                idx = [0]
            if not idx:
                messagebox.showinfo("Source", T("Click a row in the TikTok table first, then 'Fetch selected now'.",
                                                "Pehle TikTok table mein row par click karein, phir 'Fetch selected now'."))
                return
        else:
            idx = list(range(len(lst)))
        if _FETCH_LOCK.locked():
            self.log_q.put(T("[Source] A fetch is already running — please wait.", "[Source] Fetch pehle se chal raha hai — intezaar karein."))
            return
        self.log_q.put(T(f"[Source] Fetch started for {len(idx)} source(s)...", f"[Source] {len(idx)} source ka fetch shuru..."))

        def work():
            with _FETCH_LOCK:
                for i in idx:
                    cur = sources_list()
                    if i >= len(cur):
                        continue
                    fetch_tiktok(cur[i], lambda m: self.log_q.put(m)); cur[i]["last_ts"] = time.time()
                    sources_save(cur); self.after(0, self._src_refresh)
        threading.Thread(target=work, daemon=True).start()


class App(SourcesMixin, ProUIMixin, ApiPagesMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DK Facebook Uploader")
        self.geometry("1240x720")
        try:
            self.state("zoomed")          # poori screen par khule
        except Exception:
            pass
        self.minsize(1000, 600)
        try:
            self.iconphoto(True, self._make_logo(48, "#ffffff"))
        except Exception:
            pass
        try:
            # DPI ke hisaab se + user ka Text size
            dpi = self.winfo_fpixels("1i")
            self.tk.call("tk", "scaling", (dpi / 72.0) * ui_zoom())
        except Exception:
            pass
        style = ttk.Style(self)
        apply_dark_theme(self, style)

        self.log_q = queue.Queue()
        migrate_page_names()
        migrate_profile_names()
        self.resume_events = {}
        self.workers = []
        self.stop_flag = threading.Event()
        self.ld_stop = threading.Event()     # LD working ka apna stop (web se alag)
        self._panels = []
        self._undo_stack = []
        self.log_buffer = []        # remote app ke liye recent log
        self.api_status_map = {}    # acc_id -> status text
        globals()["APP_INSTANCE"] = self
        self.img_check, self.img_empty = self._make_check_icons()
        # Checkbox par asli TICK (cross nahi) — apni image wala indicator
        try:
            style.element_create("tick.indicator", "image", self.img_empty, ("selected", self.img_check),
                                 sticky="w", width=int(26 * max(1, round(ui_px(self) * 0.75))))
            style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("tick.indicator", {"side": "left", "sticky": ""}),
                ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                    ("Checkbutton.label", {"sticky": "nswe"})]})]})])
        except Exception:
            pass
        try:
            def _circ(fill, dot):
                n = 18
                im = tk.PhotoImage(width=n, height=n)
                for yy in range(n):
                    for xx in range(n):
                        d = ((xx - 8.5) ** 2 + (yy - 8.5) ** 2) ** 0.5
                        if d <= 8.5:
                            c = "#4b5d7d" if d > 7.3 else (dot if (dot and d <= 4.2) else fill)
                            im.put(c, (xx, yy))
                return im
            self.img_r0, self.img_r1 = _circ("#0e1626", None), _circ("#0e1626", "#22c3e6")
            _f = max(1, int(round(ui_px(self) * 0.75)))
            if _f > 1:
                self.img_r0, self.img_r1 = self.img_r0.zoom(_f, _f), self.img_r1.zoom(_f, _f)
            style.element_create("dot.indicator", "image", self.img_r0, ("selected", self.img_r1),
                                 sticky="w", width=int(26 * max(1, round(ui_px(self) * 0.75))))
            style.layout("TRadiobutton", [("Radiobutton.padding", {"sticky": "nswe", "children": [
                ("dot.indicator", {"side": "left", "sticky": ""}),
                ("Radiobutton.focus", {"side": "left", "sticky": "w", "children": [
                    ("Radiobutton.label", {"sticky": "nswe"})]})]})])
        except Exception:
            pass

        self._build_sidebar()
        self._build_statusbar()
        self.container = ttk.Frame(self.right)
        self.container.pack(fill="both", expand=True)
        self.pages = {}
        for name in ("Dashboard", "Accounts", "Proxy", "Sources", "Post", "API", "Interact", "LDPlayer", "Schedule",
                     "Logs", "Settings", "License", "Guide"):
            fr = ttk.Frame(self.container)
            fr.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.pages[name] = fr
        self._build_dashboard_page()
        self._build_accounts_page()
        self._build_proxy_page()
        self._build_sources_page()
        self._build_logs_page()
        self._build_post_page()
        self._build_api_page()
        self._build_interact_page()
        self._build_ldwork_page()      # LDPlayer tab (list + working merged)
        self._build_schedule_page()
        self._build_settings_page()
        self._build_guide_page()
        self._build_license_page()
        self._build_logbar()
        self._show("Dashboard")
        self.after(400, self._license_gate)
        self.after(60 * 1000, self._auto_update_tick)      # khud update (default ON)
        self.after(300, lambda: self.state("zoomed") if os.name == "nt" else None)   # hamesha poori screen
        self.bind_all("<Control-z>", lambda e: self._undo_delete())

        if not PLAYWRIGHT_OK:
            self.log_q.put(T("NOTE: On the first START, required components will auto-install (internet needed).", "NOTE: Pehli dafa START par zaroori cheezein khud install hongi (internet chahiye)."))
        self._start_daily_watcher()
        self._setup_tray()
        try:
            if get_api_keys() or load_settings().get("captcha_compat80"):
                start_api_server(log=lambda m: self.log_q.put(m))
        except Exception:
            pass
        self._start_telegram()
        self._start_tunnel()
        self.after(200, self._drain_log)

    # ---------- Logo + icons ----------
    def _make_logo(self, size=34, bg="#16233f"):
        """Blue rounded square with white 'f' — clean Facebook-style logo.
        bg = corners ka color (jahan logo lagega us ka background)."""
        img = tk.PhotoImage(width=size, height=size)
        blue = "#1877f2"
        r = size * 0.22
        grid = []
        for y in range(size):
            row = []
            for x in range(size):
                corner_ok = True
                for cx, cy in [(r, r), (size - 1 - r, r), (r, size - 1 - r), (size - 1 - r, size - 1 - r)]:
                    if ((x < r and y < r) or (x > size - 1 - r and y < r) or
                            (x < r and y > size - 1 - r) or (x > size - 1 - r and y > size - 1 - r)):
                        if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                            corner_ok = False
                row.append(blue if corner_ok else bg)
            grid.append(row)

        def setpx(x, y):
            if 0 <= x < size and 0 <= y < size:
                grid[y][x] = "#ffffff"
        # clean 'f': vertical stem + top hook + middle bar
        stem = int(size * 0.46)
        th = max(2, int(size * 0.09))
        for y in range(int(size * 0.26), int(size * 0.80)):
            for t in range(th):
                setpx(stem + t, y)
        for x in range(stem, int(size * 0.66)):            # top hook
            for t in range(th):
                setpx(x, int(size * 0.26) + t)
        for x in range(int(size * 0.32), int(size * 0.64)):  # middle bar
            for t in range(th):
                setpx(x, int(size * 0.47) + t)
        try:
            img.put(" ".join("{" + " ".join(r) + "}" for r in grid))
        except Exception:
            pass
        return img

    def _make_check_icons(self):
        """Checkbox ke liye asli image (font glyph ka masla khatam)."""
        size = 18
        def blank(border):
            g = [["#0e1626"] * size for _ in range(size)]
            for i in range(size):
                g[0][i] = g[size - 1][i] = border
                g[i][0] = g[i][size - 1] = border
            return g
        def to_img(g):
            im = tk.PhotoImage(width=size, height=size)
            try:
                im.put(" ".join("{" + " ".join(r) + "}" for r in g))
            except Exception:
                pass
            return im
        empty = to_img(blank("#4b5d7d"))
        g = blank("#22c3e6")
        green = "#34d399"
        pts = []
        for k in range(4):
            pts += [(4 + k, 8 + k), (4 + k, 9 + k)]
        for k in range(7):
            pts += [(7 + k, 11 - k), (7 + k, 12 - k)]
        for (x, y) in pts:
            if 0 <= x < size and 0 <= y < size:
                g[y][x] = green
        checked = to_img(g)
        f = max(1, int(round(ui_px(self) * 0.75)))
        if f > 1:
            checked, empty = checked.zoom(f, f), empty.zoom(f, f)
        return checked, empty

    # ---------- Top tabs ----------
    def _build_topbar(self):
        bar = tk.Frame(self, bg="#16233f"); bar.pack(fill="x")
        self._tab_btns = {}
        tabs = [("Accounts", "ACCOUNT MANAGEMENT"), ("Post", "POST (REELS/VIDEO/PHOTO)"),
                ("API", "API PAGES"),
                ("Interact", "INTERACT / GROUPS"),
                ("Schedule", "SCHEDULE"), ("Settings", "SETTINGS"), ("Guide", "GUIDE")]
        for key, label in tabs:
            b = tk.Button(bar, text=label, relief="flat", padx=18, pady=12, bd=0,
                          bg="#16233f", fg="#cfe0ff", activebackground="#2d7de0",
                          font=("Segoe UI", 12, "bold"), command=lambda k=key: self._show(k))
            b.pack(side="left")
            self._tab_btns[key] = b

    def _show_old(self, name):
        self.pages[name].tkraise()
        for k, b in self._tab_btns.items():
            if k == name:
                b.config(bg="#2d7de0", fg="white")
            else:
                b.config(bg="#16233f", fg="#cfe0ff")

    # ---------- Accounts page ----------
    def _build_accounts_page(self):
        f = self.pages["Accounts"]
        left = self._scroll_area(f, width=470)
        lg = ttk.LabelFrame(left, text=T("LOGIN SELECTED IDs  (auto 2FA + own FB Browser profile)",
                                         "LOGIN SELECTED IDs  (auto 2FA + alag FB Browser profile)"))
        lg.pack(fill="x", pady=(0, 6))
        lr = ttk.Frame(lg); lr.pack(fill="x", padx=8, pady=3)
        ttk.Label(lr, text=T("Open at once:", "Aik waqt khulein:")).pack(side="left")
        self.login_threads = tk.StringVar(value="5")
        ttk.Combobox(lr, textvariable=self.login_threads, values=("1", "2", "3", "5", "10", "15", "20"),
                     width=5).pack(side="left", padx=4)
        self.login_detect = tk.IntVar(value=1)
        ttk.Checkbutton(lr, text=T("Detect pages after login", "Login ke baad pages dhoondo"),
                        variable=self.login_detect).pack(side="left", padx=6)
        lb = ttk.Frame(lg); lb.pack(fill="x", padx=8, pady=3)
        ttk.Button(lb, text=T("LOGIN selected IDs", "LOGIN selected IDs"), command=self._login_selected).pack(side="left")
        ttk.Button(lb, text="STOP", command=self._stop_all).pack(side="left", padx=4)
        self._build_ipf_controls(lb)
        ttk.Button(lb, text="Pages", style="Green.TButton", command=self._page_manager).pack(side="left", padx=(8, 0))
        lm = ttk.Frame(lg); lm.pack(fill="x", padx=8, pady=3)
        ttk.Button(lm, text=T("Check status + monetization", "Status + monetization check"),
                   command=lambda: self._status_selected(False)).pack(side="left")
        ttk.Button(lm, text=T("Professional mode ON", "Professional mode ON"),
                   command=lambda: self._status_selected(True)).pack(side="left", padx=4)
        lc = ttk.Frame(lg); lc.pack(fill="x", padx=8, pady=3)
        ttk.Button(lc, text=T("Capture open ixBrowser profiles (free plan)",
                              "Khule ixBrowser profiles capture (free plan)"),
                   command=self._ix_capture_click).pack(side="left")

        form = ttk.LabelFrame(left, text=T("Add / edit ID", "ID add / edit karein"))
        self.a_form = form
        form.pack(fill="y")

        self.a_name = self._row(form, T("Name:", "Name:"))
        self.a_email = self._row(form, T("Email / Phone:", "Email / Phone:"))
        self.a_pass = self._row(form, T("Password:", "Password:"), show=None)
        # Page name / ixBrowser / LDPlayer khaane hata diye (purana data mehfooz — sirf nazar nahi aate)
        self.a_page = tk.StringVar(); self.a_ix = tk.StringVar(); self.a_ld = tk.StringVar()
        self.a_proxy = self._row(form, T("Proxy (optional):", "Proxy (optional):"))
        self.a_twofa = self._row(form, T("2FA secret key (optional):", "2FA secret key (optional):"))
        ttk.Label(form, text=T("— filled automatically by the bot on login / Active —",
                               "— ye bot login / Active par khud bharta hai —"), style="Muted.TLabel").pack(anchor="w", padx=8, pady=(6, 0))
        self.a_uid = self._auto_row(form, "UID:")
        self.a_email2 = self._row(form, T("Email2 / recovery:", "Email2 / recovery:"))
        pr_ = ttk.Frame(form); pr_.pack(fill="x", padx=8, pady=3)
        ttk.Label(pr_, text=T("Browser profile:", "Browser profile:"), width=18).pack(side="left")
        self.a_profile = tk.StringVar()
        self.a_profile_cb = ttk.Combobox(pr_, textvariable=self.a_profile, width=24, state="readonly")
        self.a_profile_cb.pack(side="left", fill="x", expand=True)
        self.a_group = self._auto_row(form, T("Group:", "Group:"))
        self.a_primary = self._auto_row(form, T("Primary location:", "Primary location:"))
        self.a_created = self._auto_row(form, T("Creation date:", "Banne ki tareekh:"))
        self.a_country = self._auto_row(form, T("Country:", "Country:"))
        self.a_dob = self._auto_row(form, T("DOB (date of birth):", "DOB (paidaish):"))
        gr_ = ttk.Frame(form); gr_.pack(fill="x", padx=8, pady=3)
        ttk.Label(gr_, text=T("Gender:", "Gender:"), width=18).pack(side="left")
        self.a_gender = tk.StringVar()
        ttk.Entry(gr_, textvariable=self.a_gender, width=26, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Label(gr_, text=" auto", style="Muted.TLabel").pack(side="left")

        # Videos folder / Hashtags hata diye: videos page ke folder (Content Sources) se, caption + hashtags video ke saath
        self.a_folder = tk.StringVar()
        self.a_video_list = tk.Listbox(form, height=1, width=1)        # chhupa (pack nahi)

        dr = ttk.Frame(form); dr.pack(fill="x", padx=8, pady=3)
        ttk.Label(dr, text=T("DAILY max videos for this page (0=unlimited):", "Is page par DAILY max videos (0=unlimited):")).pack(side="left")
        self.a_daily = tk.StringVar(value="0")
        tk.Spinbox(dr, from_=0, to=1000, textvariable=self.a_daily, width=6).pack(side="left", padx=4)

        self.a_hash = tk.Text(form, height=1, width=1)                 # chhupa (pack nahi)

        btns = ttk.Frame(form); btns.pack(fill="x", padx=8, pady=6)
        self.a_save_btn = ttk.Button(btns, text=T("Save ID", "Save ID"), style="Accent.TButton", command=self._save_account)
        self.a_save_btn.pack(side="left")
        ttk.Button(btns, text=T("New / clear form", "New / clear form"), command=self._clear_form).pack(side="left", padx=4)

        # ---- ADD ACCOUNTS (Bob Prime jaisa: format khud chuno) ----
        imf = ttk.LabelFrame(left, text=T("ADD ACCOUNTS (.txt import — choose the column order)",
                                          "ADD ACCOUNTS (.txt import — columns ki tarteeb chuno)"))
        imf.pack(fill="x", pady=8, before=form)
        fr1 = ttk.Frame(imf); fr1.pack(fill="x", padx=8, pady=6)
        ttk.Label(fr1, text=T("Format:", "Format:")).pack(side="left")
        saved_fmt = load_settings().get("import_fmt", ["Email", "Pass", "2FA", "Skip", "Skip"])
        self.imp_fmt = []
        for k_ in range(5):
            v_ = tk.StringVar(value=saved_fmt[k_] if k_ < len(saved_fmt) else "Skip")
            ttk.Combobox(fr1, textvariable=v_, values=IMPORT_SLOTS, width=6, state="readonly").pack(side="left", padx=1)
            self.imp_fmt.append(v_)
        fr2 = ttk.Frame(imf); fr2.pack(fill="x", padx=8, pady=6)
        self.imp_auto = tk.IntVar(value=1 if load_settings().get("import_auto", True) else 0)
        ttk.Checkbutton(fr2, text=T("Auto-detect format", "Format khud pehchano"), variable=self.imp_auto).pack(side="left")
        self.imp_profiles = tk.IntVar(value=1)
        ttk.Checkbutton(fr2, text=T("Put each ID in its own browser profile", "Har ID apne browser profile mein"),
                        variable=self.imp_profiles).pack(side="left", padx=6)
        fr3 = ttk.Frame(imf); fr3.pack(fill="x", padx=8, pady=6)
        ttk.Label(fr3, text=T("Group:", "Group:")).pack(side="left")
        self.imp_group = tk.StringVar()
        ttk.Entry(fr3, textvariable=self.imp_group, width=12).pack(side="left", padx=3)
        ttk.Button(fr3, text=T("Choose .txt & ADD NOW", ".txt chuno aur ADD NOW"), style="Accent.TButton",
                   command=self._import_accounts_file).pack(side="left", padx=4)
        fr4 = ttk.Frame(imf); fr4.pack(fill="x", padx=8, pady=6)
        ttk.Label(fr4, text=T("Browser profiles:", "Browser profiles:")).pack(side="left")
        self.prof_n = tk.StringVar(value="10")
        tk.Spinbox(fr4, from_=1, to=500, textvariable=self.prof_n, width=5).pack(side="left", padx=3)
        ttk.Button(fr4, text=T("Create profiles", "Profiles banao"), command=self._create_profiles).pack(side="left")
        ttk.Button(fr4, text=T("Auto-assign IDs → profiles", "IDs → profiles khud"), command=self._assign_profiles).pack(side="left", padx=4)
        ib = ttk.Frame(form)
        ttk.Label(form, text=T("Text file, one ID per line:  email|password|2FA secret\n"
                               "(also works with :  ,  ;  space or tab — 2FA optional)",
                               "Text file, har line me aik ID:  email|password|2FA secret\n"
                               "(:  ,  ;  space ya tab bhi chalega — 2FA optional)"),
                  foreground="#8ea0bd").pack(anchor="w", padx=8)

        hb = ttk.Frame(left); hb.pack(fill="x", pady=6)
        ttk.Button(hb, text=T("IP check (selected)", "IP check (selected)"), command=self._check_ip_selected).pack(side="left")
        ttk.Button(hb, text=T("Page History", "Page History"), command=self._show_page_history).pack(side="left", padx=4)
        ttk.Button(hb, text=T("ID History", "ID History"), command=self._show_id_history).pack(side="left")

        self.acc_table = AccountPanel(f, self, on_start=None, show_delete=True, on_pick=self._load_account_into_form,
                                      detail=True)
        self._refresh_profiles_cb()

    def _scroll_area(self, parent, width=470, full=False):
        try:
            _dpi = self.winfo_fpixels("1i") / 96.0
        except Exception:
            _dpi = 1.0
        width = int(width * ui_zoom() * _dpi / 1.25)
        """Left side ko scroll wala banao (chhoti screen par sab nazar aaye)."""
        outer = ttk.Frame(parent)
        if full:
            outer.pack(fill="both", expand=True, padx=8, pady=4)
        else:
            outer.pack(side="left", fill="y", padx=8, pady=8)
        cv = tk.Canvas(outer, width=width, highlightthickness=0, bd=0, bg=PAL["bg"])
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        inner = ttk.Frame(cv)
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win = cv.create_window((0, 0), window=inner, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        if full:
            cv.pack(side="left", fill="both", expand=True)
        else:
            cv.pack(side="left", fill="y", expand=False)
        def _wheel(e):
            try:
                cv.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
            except Exception:
                pass
        cv.bind("<Enter>", lambda e: cv.bind_all("<MouseWheel>", _wheel))
        cv.bind("<Leave>", lambda e: cv.unbind_all("<MouseWheel>"))
        return inner

    # ---------- Login selected IDs (pool) ----------
    def _login_selected(self):
        accs = self.acc_table.checked_accounts()
        if not accs:
            messagebox.showwarning(T("Wait", "Ruko"), T("Select IDs with ✔ in the table.", "Table mein ✔ se IDs chunein."))
            return
        try:
            n = max(1, int(self.login_threads.get().strip() or "5"))
        except ValueError:
            n = 5
        self._start_login_job(accs, n, bool(self.login_detect.get()))

    def _start_login_job(self, accs, threads=5, detect=True):
        self.stop_flag.clear()
        self.log_q.put(T(f"=== LOGIN START: {len(accs)} IDs, {threads} at once ===",
                         f"=== LOGIN START: {len(accs)} IDs, aik waqt {threads} ==="))
        mgr = threading.Thread(target=self._run_login_pool, args=(list(accs), threads, detect), daemon=True)
        mgr.start(); self.workers.append(mgr)

    def _ix_capture_click(self):
        self.log_q.put(T("Reading open ixBrowser profiles...", "Khule ixBrowser profiles parh rahe hain..."))
        def work():
            matched, unmatched = ix_capture_open_profiles()
            self.after(0, lambda: self._ix_capture_done(matched, unmatched))
        threading.Thread(target=work, daemon=True).start()

    def _ix_capture_done(self, matched, unmatched):
        for name, key in matched:
            self.log_q.put(T(f"ixBrowser profile linked: {name}", f"ixBrowser profile jur gaya: {name}"))
        if not matched and not unmatched:
            messagebox.showinfo("ixBrowser", T(
                "No open ixBrowser profile found.\nOpen the profiles in ixBrowser first (Open / Open in Batches), then press Capture again.",
                "Koi khula ixBrowser profile nahi mila.\nPehle ixBrowser mein profiles kholein (Open / Open in Batches), phir dobara Capture dabayein."))
            return
        if unmatched:
            self._ix_assign_dialog(unmatched)
        else:
            messagebox.showinfo("ixBrowser", T(f"{len(matched)} profile(s) linked. You can close them now — the app will open them itself.",
                                               f"{len(matched)} profile jur gaye. Ab inhe band kar sakte ho — app khud kholegi."))
        self._refresh_all_panels()

    def _ix_assign_dialog(self, unmatched):
        accs = load_accounts()
        names = [f"{a['name']}  <{a['email']}>" for a in accs]
        win = tk.Toplevel(self); win.title(T("Link ixBrowser profiles", "ixBrowser profiles jorein"))
        ttk.Label(win, text=T("These open profiles have no matching email. Choose the ID for each:",
                              "In khule profiles ka email match nahi hua. Har ek ke liye ID chunein:")).pack(anchor="w", padx=10, pady=8)
        vars_ = []
        for pr in unmatched:
            row = ttk.Frame(win); row.pack(fill="x", padx=10, pady=3)
            lbl = pr["fb_user"] or T("(no username saved in profile)", "(profile mein username nahi)")
            ttk.Label(row, text=f"{pr['key'][:8]}…  {lbl}", width=42).pack(side="left")
            v = tk.StringVar(); vars_.append((pr, v))
            ttk.Combobox(row, textvariable=v, values=names, width=40, state="readonly").pack(side="left", padx=4)
        def save():
            n = 0
            for pr, v in vars_:
                if v.get() in names:
                    a = accs[names.index(v.get())]
                    update_account(a["id"], ix_key=pr["key"]); n += 1
            win.destroy(); self._refresh_all_panels()
            self.log_q.put(T(f"{n} ixBrowser profile(s) linked.", f"{n} ixBrowser profile jur gaye."))
        ttk.Button(win, text=T("Save", "Save"), command=save).pack(pady=10)

    def _status_selected(self, pro, accs=None):
        accs = accs if accs is not None else self.acc_table.checked_accounts()
        if not accs:
            messagebox.showwarning(T("Wait", "Ruko"), T("Select IDs with ✔ in the table.", "Table mein ✔ se IDs chunein."))
            return
        try:
            n = max(1, int(self.login_threads.get().strip() or "3"))
        except ValueError:
            n = 3
        self.stop_flag.clear()
        what = "PROFESSIONAL MODE" if pro else "STATUS CHECK"
        self.log_q.put(f"=== {what}: {len(accs)} IDs ===")

        def one(acc):
            log = lambda m: self.log_q.put(m)
            bot = Automation(log=log, headless=False, browser_cfg=load_settings())
            try:
                bot.start(acc)
                if not bot.login(wait_for_2fa_callback=self._wait_2fa):
                    _st = bot.account_state()
                    self.after(0, lambda: self._set_status_all(acc["id"], _st)); return
                self.after(0, lambda: self._set_status_all(acc["id"], "Live"))
                if pro:
                    bot.act_professional_mode()
                else:
                    bot.check_page_status(False)
                self.after(0, self._refresh_all_panels)
            except Exception as e:
                log(f"[{acc['name']}] {e}")
            finally:
                time.sleep(2); bot.stop()

        def pool():
            if not ensure_runtime(log=lambda m: self.log_q.put(m)):
                return
            set_tile_count(min(n, len(accs)))
            act, pend = [], list(accs)
            while (pend or act) and not self.stop_flag.is_set():
                act = [t for t in act if t.is_alive()]
                while pend and len(act) < n and not self.stop_flag.is_set():
                    t = threading.Thread(target=one, args=(pend.pop(0),), daemon=True); t.start(); act.append(t)
                    self.stop_flag.wait(random.uniform(4, 10))
                time.sleep(2)
            self.log_q.put(f"=== {what} finished ===")
        threading.Thread(target=pool, daemon=True).start()

    def _set_status_all(self, acc_id, text):
        for p in self._panels:
            try:
                p.set_status(acc_id, text)
            except Exception:
                pass

    def _run_login_pool(self, accs, limit, detect):
        log = lambda m: self.log_q.put(m)
        set_tile_count(min(limit, len(accs)))
        if not ensure_runtime(log=log):
            return
        active, pending = [], list(accs)
        while (pending or active) and not self.stop_flag.is_set():
            active = [t for t in active if t.is_alive()]
            while pending and len(active) < limit and not self.stop_flag.is_set():
                acc = pending.pop(0)
                t = threading.Thread(target=self._run_login_one, args=(acc, detect), daemon=True)
                t.start(); active.append(t); self.workers.append(t)
                time.sleep(2)
            time.sleep(2)
        log(T("=== LOGIN finished ===", "=== LOGIN khatam ==="))
        self.after(0, self._refresh_all_panels)

    def _run_login_one(self, acc, detect):
        log = lambda m: self.log_q.put(m)
        bot = Automation(log=log, headless=False, browser_cfg=load_settings())
        self.after(0, lambda: self._set_status_all(acc["id"], "Login..."))
        try:
            bot.start(acc)
            if not bot.login(wait_for_2fa_callback=self._wait_2fa):
                st = bot.account_state()
                self.after(0, lambda: self._set_status_all(acc["id"], st))
                add_id_history(acc["id"], "login failed", f"{st}: " + (bot.last_error or "login failed"))
                return
            self.after(0, lambda: self._set_status_all(acc["id"], "Live"))
            add_id_history(acc["id"], "login ok", "")
            if not self.stop_flag.is_set():
                try:      # insaan ki tarah thora feed scroll, phir details
                    bot.page.goto(FB_HOME, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 4))
                    bot._scroll_for(random.uniform(10, 20))
                except Exception:
                    pass
                try:
                    bot.detect_profile_info()
                    bot.check_primary_location()
                except Exception:
                    pass
            if detect and not self.stop_flag.is_set():
                pages = bot.detect_pages()
                if pages:
                    set_account_pages(acc["id"], pages)
                self.after(0, self._refresh_all_panels)
        except Exception as e:
            log(T(f"[{acc['name']}] Login error: {e}", f"[{acc['name']}] Login error: {e}"))
            self.after(0, lambda: self._set_status_all(acc["id"], "Error"))
        finally:
            time.sleep(3); bot.stop()

    def _pick_account_folder(self):
        d = filedialog.askdirectory(title=T("Videos folder", "Videos folder"))
        if d:
            self.a_folder.set(d); self._refresh_folder_videos()

    def _refresh_folder_videos(self):
        self.a_video_list.delete(0, tk.END)
        folder = self.a_folder.get().strip()
        vids = list_media_in_folder(folder, "video") + list_media_in_folder(folder, "photo")
        for p in vids:
            self.a_video_list.insert(tk.END, os.path.basename(p))
        if not vids and folder:
            self.a_video_list.insert(tk.END, T("(folder empty or invalid path)", "(folder khali ya galat path)"))

    def _auto_row(self, parent, label):
        """Sirf dikhane ke liye — bot khud bharta hai."""
        row = ttk.Frame(parent); row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text=label, width=18).pack(side="left")
        var = tk.StringVar()
        ttk.Entry(row, textvariable=var, width=26, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Label(row, text=" auto", style="Muted.TLabel").pack(side="left")
        return var

    def _row(self, parent, label, show=None):
        row = ttk.Frame(parent); row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=18).pack(side="left")
        var = tk.StringVar()
        ttk.Entry(row, textvariable=var, show=show, width=26).pack(side="left", fill="x", expand=True)
        return var

    def _clear_form(self):
        for v in (self.a_name, self.a_email, self.a_pass, self.a_page, self.a_ix, self.a_proxy, self.a_twofa, self.a_ld, self.a_folder, self.a_daily,
                  self.a_uid, self.a_email2, self.a_profile, self.a_group, self.a_primary, self.a_created,
                  self.a_country, self.a_dob, self.a_gender):
            v.set("")
        self.a_daily.set("0")
        self._editing_id = None
        try:
            self.a_form.config(text=T("Add / edit ID", "ID add / edit karein"))
            self.a_save_btn.config(text=T("Save ID", "Save ID"))
        except Exception:
            pass
        self.a_hash.delete("1.0", tk.END)
        self.a_video_list.delete(0, tk.END)

    def _load_account_into_form(self, a):
        self._editing_id = a.get("id")
        try:
            self.a_form.config(text=T(f"✎ EDITING: {a.get('name') or a.get('email')}  (change anything → SAVE CHANGES)",
                                      f"✎ EDIT: {a.get('name') or a.get('email')}  (jo badalna ho badlo → SAVE CHANGES)"))
            self.a_save_btn.config(text=T("SAVE CHANGES", "SAVE CHANGES"))
            self._show("Accounts")
        except Exception:
            pass
        self.a_name.set(a.get("name", "")); self.a_email.set(a.get("email", ""))
        self.a_pass.set(a.get("password", "")); self.a_page.set(a.get("page_name", ""))
        self.a_ix.set(a.get("ix_profile", "")); self.a_proxy.set(a.get("proxy", ""))
        self.a_twofa.set(a.get("twofa", "")); self.a_ld.set(a.get("ld_index", ""))
        self.a_folder.set(a.get("folder", ""))
        for var, key in ((self.a_uid, "uid"), (self.a_email2, "email2"), (self.a_profile, "profile"),
                         (self.a_group, "group"), (self.a_primary, "primary_loc"), (self.a_created, "created"),
                         (self.a_country, "country"), (self.a_dob, "dob"), (self.a_gender, "gender")):
            var.set(a.get(key, ""))
        self.a_daily.set(str(get_page_daily_limit(a.get("page_name", ""))))
        self.a_hash.delete("1.0", tk.END)
        self.a_hash.insert("1.0", get_hashtags_for_page(a.get("page_name", "")))
        self._refresh_folder_videos()

    def _save_account(self):
        if not self.a_email.get().strip() or not self.a_pass.get().strip():
            messagebox.showwarning(T("Wait", "Ruko"), T("Email and password are required.", "Email aur password zaroori hain."))
            return
        page = self.a_page.get().strip()
        old_id = getattr(self, "_editing_id", None)
        new_id = self.a_email.get().strip().lower()
        if old_id and old_id != new_id:
            # email badli: purani entry ki saari maloomat (pages, status, profile...) nayi ID par le jao
            old = get_account(old_id) or {}
            accs = [x for x in load_accounts() if x.get("id") != old_id]
            save_accounts(accs)
            carry = {k: v for k, v in old.items() if k not in ("id", "email")}
        else:
            carry = {}
        aid = add_account(self.a_name.get() or self.a_email.get(), self.a_email.get(),
                          self.a_pass.get(), page, self.a_proxy.get(), self.a_ix.get(),
                          self.a_folder.get(), self.a_twofa.get(), self.a_ld.get())
        _upd = {"email2": self.a_email2.get().strip(), "last_update": _now_str()[:16]}
        if self.a_profile.get().strip():
            _upd["profile"] = self.a_profile.get().strip()
        update_account(aid, **_upd)
        if not (get_account(aid) or {}).get("profile"):
            profiles_auto_assign({aid})
        if carry:
            keep = {k: v for k, v in carry.items() if k not in (
                "name", "password", "page_name", "proxy", "ix_profile", "folder", "twofa", "ld_index", "email2",
                "last_update")}
            update_account(aid, **keep)
        self.log_q.put(T(f"ID saved: {self.a_name.get() or self.a_email.get()}", f"ID save ho gayi: {self.a_name.get() or self.a_email.get()}"))
        if page:
            set_hashtags_for_page(page, self.a_hash.get("1.0", tk.END))
            try:
                set_page_daily_limit(page, int(self.a_daily.get().strip() or "0"))
            except ValueError:
                pass
        self._clear_form()
        self._refresh_all_panels()

    def _busy(self):
        """Koi kaam (posting/active/login) chal raha hai?"""
        try:
            return any(t.is_alive() for t in self.workers)
        except Exception:
            return False

    def _auto_update_tick(self):
        """Har 15 min: nayi version ho aur koi kaam na chal raha ho to KHUD update + restart."""
        def work():
            try:
                if not load_settings().get("auto_update", True) or not getattr(sys, "frozen", False):
                    return
                has, latest, _ = check_update()
                if not has:
                    return
                if self._busy():
                    self.log_q.put(T(f"Update {latest} ready — will install when current work finishes.",
                                     f"Update {latest} tayyar — kaam khatam hote hi lagegi."))
                    return
                self.log_q.put(T(f"Auto-update: installing {latest}...", f"Auto-update: {latest} lag rahi hai..."))
                res = do_update(log=lambda m: self.log_q.put(m))
                if res == "restart_exe":
                    self.after(0, lambda: (self.destroy(), os._exit(0)))
            except Exception as e:
                self.log_q.put(f"auto-update: {e}")
        threading.Thread(target=work, daemon=True).start()
        self.after(15 * 60 * 1000, self._auto_update_tick)

    def _restart_app(self):
        """App band kar ke khud dobara kholo."""
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("_MEI", "_PYI", "PYINSTALLER"))}
        env.pop("TCL_LIBRARY", None); env.pop("TK_LIBRARY", None)
        if getattr(sys, "frozen", False):
            cmd = f'timeout /t 3 /nobreak >nul & start "" "{sys.executable}"'
        else:
            cmd = f'timeout /t 3 /nobreak >nul & start "" "{sys.executable}" "{os.path.abspath(__file__)}"'
        try:
            subprocess.Popen(["cmd", "/c", cmd], env=env, **_NOWIN)
        except Exception:
            pass
        try:
            self.destroy()
        finally:
            os._exit(0)

    def _refresh_profiles_cb(self):
        try:
            self.a_profile_cb.config(values=[""] + profiles_list())
        except Exception:
            pass

    def _create_profiles(self):
        n = self._iv(self.prof_n.get()) or 1
        profiles_create(n); self._refresh_profiles_cb()
        self.log_q.put(T(f"{n} browser profile(s) created. Total: {len(profiles_list())}",
                         f"{n} browser profile ban gaye. Kul: {len(profiles_list())}"))

    def _assign_profiles(self):
        ids = set(self.acc_table.checked) or None
        n = profiles_auto_assign(ids)
        self._refresh_profiles_cb(); self._refresh_all_panels()
        self.log_q.put(T(f"{n} ID(s) placed into browser profiles.", f"{n} IDs browser profiles mein chali gayin."))

    def _export_accounts(self, mode):
        accs = self.acc_table.checked_accounts() or load_accounts()
        ext = {"full": ".csv", "txt": ".txt", "uid": ".txt"}[mode]
        path = filedialog.asksaveasfilename(defaultextension=ext, initialfile=f"accounts_{mode}_{_today_str()}{ext}",
                                            filetypes=[("CSV / Excel", "*.csv"), ("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        n = export_accounts(path, accs, mode)
        self.log_q.put(T(f"Exported {n} ID(s) → {path}", f"{n} IDs export → {path}"))
        messagebox.showinfo("Export", T(f"{n} ID(s) saved:\n{path}", f"{n} IDs save ho gayin:\n{path}"))

    def _set_group_ticked(self):
        accs = self.acc_table.checked_accounts()
        if not accs:
            messagebox.showwarning(T("Wait", "Ruko"), T("Tick IDs first.", "Pehle IDs tick karein.")); return
        win = tk.Toplevel(self); win.title("Group")
        ttk.Label(win, text=T("Group name for ticked IDs:", "Tick IDs ka group naam:")).pack(padx=12, pady=8)
        v = tk.StringVar(); ttk.Entry(win, textvariable=v, width=24).pack(padx=12)
        def ok():
            for a in accs:
                update_account(a["id"], group=v.get().strip())
            win.destroy(); self._refresh_all_panels()
        ttk.Button(win, text="OK", command=ok).pack(pady=10)

    def _open_browser_for(self, acc):
        """Is ID ka browser kholo (login + khula chhor do, aap khud kaam kar sakte ho)."""
        def work():
            log = lambda m: self.log_q.put(m)
            if not ensure_runtime(log=log):
                return
            bot = Automation(log=log, headless=False, browser_cfg=load_settings())
            try:
                bot.start(acc)
                ok = bot.login(wait_for_2fa_callback=self._wait_2fa)
                self.after(0, lambda: self._set_status_all(acc["id"], "Live" if ok else (bot.account_state() or "Not login")))
                log(T(f"[{acc['name']}] Browser is open — close the window when done.",
                      f"[{acc['name']}] Browser khula hai — kaam ke baad window band kar dein."))
                while bot.context and bot.context.pages:
                    time.sleep(2)
            except Exception as e:
                log(f"[{acc['name']}] {e}")
            finally:
                bot.stop()
        threading.Thread(target=work, daemon=True).start()

    # ---------- LICENSE ----------
    def _license_gate(self):
        st = license_check()
        if st["ok"]:
            try:
                self._lic_refresh()
            except Exception:
                pass
            self.after(3 * 3600 * 1000, self._license_recheck)
            return
        self.withdraw()
        w = tk.Toplevel(self); w.title("DK Facebook Uploader — License"); w.configure(bg=PAL["bg"])
        w.geometry("720x460"); w.protocol("WM_DELETE_WINDOW", lambda: (w.destroy(), self._quit_app()))
        tk.Label(w, text="  🔑  License", bg="#0d2a4f", fg="#ffd34d", font=("Segoe UI", 16, "bold"),
                 anchor="w").pack(fill="x", ipady=8)
        ttk.Label(w, text=st["why"] or "", foreground="#ff8080").pack(anchor="w", padx=12, pady=(10, 2))
        ttk.Label(w, text=T("1) Send this Machine ID to the owner (Danish):", "1) Ye Machine ID malik (Danish) ko bhejein:")
                  ).pack(anchor="w", padx=12, pady=(8, 2))
        mr = ttk.Frame(w); mr.pack(fill="x", padx=12)
        mid = machine_id()
        mv = tk.StringVar(value=mid)
        ttk.Entry(mr, textvariable=mv, width=30, state="readonly", font=("Consolas", 13)).pack(side="left")
        ttk.Button(mr, text=T("Copy", "Copy"), command=lambda: (self.clipboard_clear(), self.clipboard_append(mid))
                   ).pack(side="left", padx=6)
        ttk.Label(w, text=T("2) Paste the license key you receive:", "2) Jo license key mile woh yahan paste karein:")
                  ).pack(anchor="w", padx=12, pady=(14, 2))
        kt = tk.Text(w, height=5, font=("Consolas", 10)); kt.pack(fill="x", padx=12)

        def activate():
            key = "".join(kt.get("1.0", "end").split())
            save_settings({"license_key": key})
            r = license_check(force=True)
            if r["ok"]:
                messagebox.showinfo("License", T(f"Activated ✔ — licensed to {r['info'].get('n', '')}",
                                                 f"Activate ho gaya ✔ — {r['info'].get('n', '')} ke naam"), parent=w)
                w.destroy(); self.deiconify(); self._lic_refresh()
                self.after(3 * 3600 * 1000, self._license_recheck)
            else:
                messagebox.showerror("License", r["why"], parent=w)

        def owner_file():
            p = filedialog.askopenfilename(parent=w, filetypes=[("Owner key", "*.key"), ("All", "*.*")])
            if not p:
                return
            try:
                owner_key_import(p); license_check(force=True)
                w.destroy(); self.deiconify(); self._lic_refresh()
            except Exception as e:
                messagebox.showerror("License", str(e), parent=w)
        br = ttk.Frame(w); br.pack(fill="x", padx=12, pady=12)
        ttk.Button(br, text=T("ACTIVATE", "ACTIVATE"), style="Accent.TButton", command=activate).pack(side="left")
        ttk.Button(br, text=T("I am the owner — load key file", "Main malik hoon — key file"), command=owner_file).pack(side="left", padx=8)
        ttk.Button(br, text=T("Exit", "Band"), command=lambda: (w.destroy(), self._quit_app())).pack(side="right")

    def _quit_app(self):
        try:
            self.stop_flag.set()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)

    def _license_recheck(self):
        r = license_check(force=True)
        if not r["ok"]:
            try:
                self.stop_flag.set()
            except Exception:
                pass
            messagebox.showerror("License", r["why"])
            self._license_gate()
            return
        self.after(3 * 3600 * 1000, self._license_recheck)

    def _build_license_page(self):
        f = self.pages["License"]
        self._lic_box = ttk.Frame(f); self._lic_box.pack(fill="both", expand=True, padx=16, pady=10)
        self._lic_refresh()

    def _lic_refresh(self):
        box = getattr(self, "_lic_box", None)
        if box is None:
            return
        for c in box.winfo_children():
            c.destroy()
        st = _LIC if _LIC["ok"] is not None else license_check()
        if not st.get("owner"):
            info = st.get("info") or {}
            ttk.Label(box, text=T("This copy is licensed ✔", "Ye copy licensed hai ✔") if st.get("ok") else st.get("why", ""),
                      font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=4)
            ttk.Label(box, text=f"Name: {info.get('n', '')}    Expiry: {info.get('e') or 'lifetime'}    "
                                f"Machine ID: {machine_id()}").pack(anchor="w")
            return
        # ---- MALIK (owner) ----
        ttk.Label(box, text=T("OWNER — make and cancel licenses for your students",
                              "MALIK — students ke liye license banao / cancel karo"),
                  font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 6))
        g = ttk.LabelFrame(box, text=T("New license", "Naya license")); g.pack(fill="x", pady=4)
        r1 = ttk.Frame(g); r1.pack(fill="x", padx=8, pady=5)
        ttk.Label(r1, text=T("Student name:", "Student ka naam:"), width=16).pack(side="left")
        nm = tk.StringVar(); ttk.Entry(r1, textvariable=nm, width=24).pack(side="left")
        ttk.Label(r1, text="  Machine ID:").pack(side="left")
        mi = tk.StringVar(); ttk.Entry(r1, textvariable=mi, width=24, font=("Consolas", 11)).pack(side="left")
        ttk.Label(r1, text=T("  Days (0 = lifetime):", "  Din (0 = hamesha):")).pack(side="left")
        dv = tk.StringVar(value="0"); tk.Spinbox(r1, from_=0, to=3650, textvariable=dv, width=6).pack(side="left")
        out = tk.Text(g, height=4, font=("Consolas", 10)); out.pack(fill="x", padx=8, pady=4)

        def gen():
            if not nm.get().strip() or len(mi.get().strip()) < 10:
                messagebox.showwarning("License", T("Enter name and the student's Machine ID.",
                                                    "Naam aur student ki Machine ID daalein."))
                return
            try:
                days = int(dv.get() or 0)
            except ValueError:
                days = 0
            key, pl = make_license(nm.get(), mi.get(), days)
            lst = lic_issued_load()
            lst.insert(0, {"id": pl["k"], "name": pl["n"], "machine": pl["m"], "exp": pl["e"] or "lifetime",
                           "made": time.strftime("%Y-%m-%d"), "status": "active", "key": key})
            lic_issued_save(lst)
            out.delete("1.0", "end"); out.insert("1.0", key)
            self.clipboard_clear(); self.clipboard_append(key)
            self.log_q.put(T(f"[License] Made for {pl['n']} (copied — send it to the student).",
                             f"[License] {pl['n']} ke liye bana (copy ho gaya — student ko bhej dein)."))
            draw()
        ttk.Button(g, text=T("MAKE LICENSE KEY (copies it)", "LICENSE KEY BANAO (copy ho jayegi)"), style="Accent.TButton",
                   command=gen).pack(anchor="w", padx=8, pady=(0, 6))
        lf = ttk.LabelFrame(box, text=T("Issued licenses", "Diye gaye licenses")); lf.pack(fill="both", expand=True, pady=6)
        tv = ttk.Treeview(lf, columns=("id", "name", "machine", "exp", "made", "status"), show="headings", height=8)
        for c, t, w_ in (("id", "Key ID", 90), ("name", "Student", 180), ("machine", "Machine ID", 200),
                         ("exp", "Expiry", 100), ("made", "Made", 100), ("status", "Status", 100)):
            tv.heading(c, text=t); tv.column(c, width=w_, anchor="w")
        tv.pack(fill="both", expand=True, padx=6, pady=4)
        tv.tag_configure("rev", foreground="#ff8080")

        def draw():
            tv.delete(*tv.get_children())
            for x in lic_issued_load():
                tv.insert("", "end", iid=x["id"], values=(x["id"], x["name"], x["machine"], x["exp"], x["made"], x["status"]),
                          tags=(("rev",) if x["status"] != "active" else ()))
        tr = ttk.Frame(box); tr.pack(fill="x")
        ttk.Label(tr, text=T("GitHub token (to publish cancels):", "GitHub token (cancel bhejne ke liye):")).pack(side="left")
        gt = tk.StringVar(value=load_settings().get("lic_gh_token", ""))
        ttk.Entry(tr, textvariable=gt, show="•", width=34).pack(side="left", padx=4)

        def set_status(new):
            sel = list(tv.selection())
            if not sel:
                messagebox.showinfo("License", T("Select a license row.", "License ki row chunein."))
                return
            lst = lic_issued_load()
            for x in lst:
                if x["id"] in sel:
                    x["status"] = new
            lic_issued_save(lst); draw()
            ids = [x["id"] for x in lst if x["status"] != "active"]
            tok = gt.get().strip()
            save_settings({"lic_gh_token": tok})
            if not tok:
                messagebox.showinfo("License", T("Saved here. To make it take effect on the student's PC, a GitHub token is "
                                                 "needed (or tell Claude: 'cancel license " + ", ".join(sel) + "').",
                                                 "Yahan save ho gaya. Student ke PC par asar ke liye GitHub token chahiye "
                                                 "(ya Claude ko kahein: 'license cancel " + ", ".join(sel) + "')."))
                return
            try:
                lic_push_revoked(ids, tok)
                messagebox.showinfo("License", T("Done ✔ — the student's app stops within 3 hours (or on next start).",
                                                 "Ho gaya ✔ — student ki app 3 ghante mein (ya agli dafa khulne par) band."))
            except Exception as e:
                messagebox.showerror("License", f"GitHub: {e}")
        ttk.Button(tr, text=T("CANCEL selected", "Selected CANCEL"), command=lambda: set_status("cancelled")).pack(side="left", padx=6)
        ttk.Button(tr, text=T("Re-activate selected", "Selected dobara chalu"), command=lambda: set_status("active")).pack(side="left")
        draw()

    # ---------- BLOCK IP (Bob Prime jaisa) ----------
    def _build_ipf_controls(self, parent):
        st = load_settings()
        var = tk.IntVar(value=1 if st.get("ipf_on", True) else 0)
        self._ipf_vars = getattr(self, "_ipf_vars", []) + [var]

        def toggle():
            v = var.get()
            for x in self._ipf_vars:
                if x is not var:
                    x.set(v)
            cur = load_settings()
            upd = {"ipf_on": bool(v)}
            if cur.get("ipf_on") is None:            # pehli dafa: default list sath save
                upd.update(ipf_mode="block", ipf_list=["PK"])
            save_settings(upd)
            self.log_q.put(T(f"BLOCK IP {'ON' if v else 'OFF'}.", f"BLOCK IP {'ON' if v else 'OFF'}."))
        ttk.Checkbutton(parent, variable=var, command=toggle).pack(side="left")
        ttk.Button(parent, text="Block IP", command=self._ipf_dialog).pack(side="left", padx=(2, 0))

    def _ipf_dialog(self):
        st = load_settings()
        names = {c: n for c, n in COUNTRIES}
        sel = list(st.get("ipf_list") if st.get("ipf_on") is not None else ["PK"]) or []
        w = tk.Toplevel(self); w.title("Block IP Settings"); w.configure(bg=PAL["bg"]); w.transient(self)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        ww, wh = min(760, int(sw * 0.6)), min(760, int(sh * 0.85))
        w.geometry(f"{ww}x{wh}+{max(0, (sw - ww) // 2)}+{max(0, (sh - wh) // 3)}")
        w.minsize(520, 520)
        hdr = tk.Frame(w, bg="#1565c0"); hdr.pack(fill="x")
        tk.Label(hdr, text="  Block IP Settings", bg="#1565c0", fg="white", font=("Segoe UI", 15, "bold")).pack(side="left", pady=8)
        top = ttk.Frame(w); top.pack(fill="x", padx=8, pady=8)
        mode = tk.StringVar(value=st.get("ipf_mode", "block"))
        ttk.Radiobutton(top, text="Block", value="block", variable=mode).pack(side="left")
        ttk.Radiobutton(top, text="Allow", value="allow", variable=mode).pack(side="left", padx=8)
        q = tk.StringVar()
        ent = ttk.Entry(top, textvariable=q, width=22); ent.pack(side="left", padx=6, fill="x", expand=True)
        ent.insert(0, ""); ent.focus_set()
        info = ttk.Label(w, text=T("Type to search → double-click a country to add it. Double-click in the list below to remove.\n"
                                   "Block = these countries' IPs are stopped.  Allow = ONLY these countries run.",
                                   "Search mein likho → mulk par double-click se add. Neeche list mein double-click se hatao.\n"
                                   "Block = in mulkon ke IP band.  Allow = SIRF yehi mulk chalenge."),
                         style="Muted.TLabel", justify="left")
        info.pack(anchor="w", padx=8)
        res = tk.Listbox(w, height=8, bg=PAL.get("field", "#1b2740"), fg=PAL.get("text", "white"),
                         selectbackground="#1565c0", font=("Segoe UI", 11))
        res.pack(fill="x", padx=8, pady=4)
        tv = ttk.Treeview(w, columns=("no", "country"), show="headings", height=8)
        tv.heading("no", text="No."); tv.heading("country", text="Country")
        tv.column("no", width=60, anchor="center", stretch=False); tv.column("country", width=300, anchor="w")
        tv.pack(fill="both", expand=True, padx=8, pady=4)

        def redraw():
            tv.delete(*tv.get_children())
            for i, c in enumerate(sel, 1):
                tv.insert("", "end", iid=c, values=(i, f"{names.get(c, c)} ({c})"))

        def search(*_):
            t = q.get().strip().lower()
            res.delete(0, "end")
            for c, n in COUNTRIES:
                if not t or t in n.lower() or t == c.lower():
                    res.insert("end", f"{n} ({c})")
        q.trace_add("write", search)

        def add(_e=None):
            for i in res.curselection():
                c = res.get(i).rsplit("(", 1)[-1].rstrip(")")
                if c not in sel:
                    sel.append(c)
            redraw()

        def remove(_e=None):
            for c in tv.selection():
                if c in sel:
                    sel.remove(c)
            redraw()
        res.bind("<Double-Button-1>", add); res.bind("<Return>", add)
        tv.bind("<Double-Button-1>", remove); tv.bind("<Delete>", remove)

        def save():
            save_settings({"ipf_mode": mode.get(), "ipf_list": list(sel),
                           "ipf_on": bool(load_settings().get("ipf_on", True))})
            self.log_q.put(T(f"BLOCK IP saved: {mode.get().upper()} — {', '.join(names.get(c, c) for c in sel) or '(empty)'}",
                             f"BLOCK IP save: {mode.get().upper()} — {', '.join(names.get(c, c) for c in sel) or '(khaali)'}"))
            w.destroy()
        ttk.Button(top, text="Save", style="Accent.TButton", command=save).pack(side="left")
        bot_ = ttk.Frame(w); bot_.pack(fill="x", side="bottom", padx=8, pady=8)
        ttk.Button(bot_, text=T("Add selected ↓", "Chuna hua add ↓"), command=add).pack(side="left")
        ttk.Button(bot_, text=T("Remove selected", "Chuna hua hatao"), command=remove).pack(side="left", padx=6)
        ttk.Button(bot_, text="Save", style="Accent.TButton", command=save).pack(side="right")
        search(); redraw()

    def _open_browser_manual(self, acc):
        """(neeche) — browser_cfg mein _manual_open=True: non-US IP par sirf Facebook band, baqi sites chalengi."""
        return self._open_browser_manual2(acc)

    def _open_browser_manual2(self, acc):
        """Is ID ka FB Browser (apni profile + proxy/VPN + fingerprint) kholo, bot login NAHI karega.
        Aap khud login karein; login hote hi bot UID save karke ID 'Live' kar dega."""
        def work():
            log = lambda m: self.log_q.put(m)
            if not ensure_runtime(log=log):
                return
            cfg_m = dict(load_settings()); cfg_m["_manual_open"] = True
            bot = Automation(log=log, headless=False, browser_cfg=cfg_m)
            try:
                bot.start(acc)
                try:        # khaali tab — aap jo chahein kholein (Facebook bhi)
                    if "facebook.com" in (bot.page.url or "") or "ip" in (bot.page.url or ""):
                        bot.page.goto("about:blank")
                except Exception:
                    pass
                log(T(f"[{acc['name']}] Browser open — log in yourself. I'll save it when you're in. Close the window when done.",
                      f"[{acc['name']}] Browser khul gaya — khud login karein. Login hote hi main save kar dunga. Kaam ke baad window band kar dein."))
                marked = False
                while bot.context and bot.context.pages:
                    time.sleep(4)
                    if not marked:
                        try:
                            uid = bot._fb_uid()
                            url = (bot.page.url or "").lower()
                            if uid and "checkpoint" not in url and "two_step" not in url:
                                marked = True
                                # page ko haath nahi lagana (aap jo khol rahe hain wahi rahe) — sirf cookie se UID
                                try:
                                    if (get_account(acc["id"]) or {}).get("uid") != uid:
                                        update_account(acc["id"], uid=uid)
                                except Exception:
                                    pass
                                self.after(0, lambda: self._set_status_all(acc["id"], "Live"))
                                log(T(f"[{acc['name']}] Logged in (manual) — session saved.",
                                      f"[{acc['name']}] Login ho gaya (manual) — session save."))
                        except Exception:
                            pass
            except Exception as e:
                log(f"[{acc['name']}] {e}")
            finally:
                bot.stop()
        threading.Thread(target=work, daemon=True).start()

    def _delete_checked(self):
        ids = list(self.acc_table.checked)
        if not ids:
            messagebox.showwarning(T("Wait", "Ruko"), T("Select ID(s) with ✔ in the table.", "Table mein ✔ se ID(s) chunein."))
            return
        if not messagebox.askyesno(T("Confirm", "Confirm"), T(f"Delete {len(ids)} ID(s)? (You can undo with Ctrl+Z)", f"{len(ids)} ID(s) delete karein? (Ctrl+Z se waps aa sakti hain)")):
            return
        by_id = {a["id"]: a for a in load_accounts()}
        removed = []
        for i in ids:
            if i in by_id:
                removed.append(by_id[i]); remove_account(i)
        if removed:
            self._undo_stack.append(removed)
        self.acc_table.checked.clear()
        self._refresh_all_panels()
        self.log_q.put(T(f"{len(removed)} ID(s) deleted. (Ctrl+Z to undo)", f"{len(removed)} ID delete. (Ctrl+Z se undo)"))

    def _undo_delete(self):
        if not self._undo_stack:
            return
        batch = self._undo_stack.pop()
        for a in batch:
            restore_account(a)
        self._refresh_all_panels()
        self.log_q.put(T(f"{len(batch)} ID(s) restored (undo).", f"{len(batch)} ID waps aa gayi (undo)."))

    def _import_accounts_file(self):
        path = filedialog.askopenfilename(title=T("Accounts text file", "Accounts text file"),
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception as e:
            messagebox.showerror("Error", T(f"Could not open file: {e}", f"File nahi khuli: {e}"))
            return
        added = 0; skipped = 0
        fmt = [v.get() for v in getattr(self, "imp_fmt", [])]
        auto = bool(getattr(self, "imp_auto", None) is None or self.imp_auto.get())
        save_settings({"import_fmt": fmt, "import_auto": auto})
        group = (getattr(self, "imp_group", None) and self.imp_group.get().strip()) or \
            os.path.splitext(os.path.basename(path))[0][:30]
        new_ids = []
        for raw in lines:
            extra = {}
            if auto or not fmt:
                r = parse_account_line(raw)
                if not r or not r[1]:
                    if raw.strip():
                        skipped += 1
                    continue
                email, password, twofa, rest = r
                name = rest[0] if rest else (email.split("@")[0] if "@" in email else email)
                page = rest[1] if len(rest) > 1 else ""
                ixp = rest[2] if len(rest) > 2 else ""
                proxy = rest[3] if len(rest) > 3 else ""
            else:
                d = parse_with_format(raw, fmt)
                if not d:
                    continue
                email = d.get("Email") or d.get("UID") or ""
                password = d.get("Pass", "")
                if not email or not password:
                    skipped += 1; continue
                twofa = d.get("2FA", "")
                name = d.get("Name") or (email.split("@")[0] if "@" in email else email)
                page = ixp = proxy = ""
                if d.get("UID"):
                    extra["uid"] = d["UID"]
                if d.get("Email2"):
                    extra["email2"] = d["Email2"]
            aid = add_account(name, email, password, page, proxy, ixp, "", twofa)
            extra["last_update"] = _now_str()[:16]
            if group:
                extra["group"] = group
            update_account(aid, **extra)
            new_ids.append(aid)
            added += 1
        if new_ids and getattr(self, "imp_profiles", None) and self.imp_profiles.get():
            profiles_auto_assign(set(new_ids))
            self._refresh_profiles_cb()
        self._refresh_all_panels()
        messagebox.showinfo(T("Import", "Import"), T(f"{added} IDs added, {skipped} skipped (no email/password).", f"{added} IDs add hui, {skipped} skip (email/password nahi mili)."))
        self.log_q.put(T(f"Bulk import: {added} added, {skipped} skipped.", f"Bulk import: {added} added, {skipped} skipped."))

    def _check_ip_selected(self):
        accs = self.acc_table.checked_accounts()
        if not accs:
            messagebox.showwarning(T("Wait", "Ruko"), T("Select one ID with ✔.", "Aik ID ✔ karein."))
            return
        threading.Thread(target=self._run_ip_check, args=(accs[0],), daemon=True).start()

    def _refresh_all_panels(self):
        for p in self._panels:
            p.refresh()

    # ---------- History windows ----------
    def _show_page_history(self):
        self._history_window(T("Page History", "Page History"), all_page_history(),
                             ("Page", "Time", "Video", "Status", "Issue"),
                             lambda page, rec: (page, rec["time"], rec.get("video", ""), rec.get("status", ""), rec.get("issue", "")))

    def _show_id_history(self):
        names = {a["id"]: a["name"] for a in load_accounts()}
        self._history_window(T("ID History", "ID History"), all_id_history(),
                             ("ID", "Time", "Event", "Issue"),
                             lambda k, rec: (names.get(k, k), rec["time"], rec.get("event", ""), rec.get("issue", "")))

    def _history_window(self, title, data, cols, rowfn):
        win = tk.Toplevel(self); win.title(title); win.geometry("820x440")
        tv = ttk.Treeview(win, columns=cols, show="headings")
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=160 if c in ("Issue", "Time") else 120, anchor="w")
        tv.pack(fill="both", expand=True)
        rows = []
        for key, recs in data.items():
            for rec in recs:
                r = rowfn(key, rec)
                rows.append(r)
                tv.insert("", "end", values=r)
        bar = ttk.Frame(win); bar.pack(fill="x", pady=4)
        ttk.Button(bar, text=T("Export CSV", "CSV export"),
                   command=lambda: self._export_csv(cols, rows, title)).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right", padx=6)

    def _export_csv(self, cols, rows, title):
        import csv
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            initialfile=f"{title.replace(' ', '_')}.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                for r in rows:
                    w.writerow(r)
            messagebox.showinfo(T("Export", "Export"), T(f"Saved: {path}", f"Save ho gaya: {path}"))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---------- Post page ----------
    def _build_post_page(self):
        f = self.pages["Post"]
        side = tk.Frame(f, bg=PAL["panel"], width=190); side.pack(side="left", fill="y"); side.pack_propagate(False)
        tk.Label(side, text="POST", bg=PAL["panel"], fg="white", font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=14, pady=(14, 10))
        self.post_kind = tk.StringVar(value="reel")
        self._side_btns = {}
        for key, label in [("reel", "Post Reels"), ("video", "Post Video"), ("photo", "Post Photo")]:
            b = tk.Button(side, text=label, anchor="w", relief="flat", bd=0,
                          font=("Segoe UI", 13, "bold"), padx=14, pady=10,
                          command=lambda k=key: self._select_post_kind(k))
            b.pack(fill="x", padx=8, pady=3)
            self._side_btns[key] = b
        self._select_post_kind("reel")

        cfg = ttk.LabelFrame(f, text=T("CONFIGURE POST", "CONFIGURE POST")); cfg.pack(side="left", fill="y", padx=8, pady=8)
        self.p_threads = self._spin_row(cfg, T("Concurrent threads (IDs at once):", "Concurrent threads (aik waqt IDs):"), 5, 1, 100)
        self.p_interval = DurationRange(cfg, T("Interval between 2 posts:", "Interval between 2 posts:"), 40, 90, "sec")
        self.p_scroll_between = tk.IntVar(value=0)
        ttk.Checkbutton(cfg, text=T("During the gap between posts, keep the ID active (scroll feed)",
                                    "Do posts ke beech gap me usi ID ko active rakhein (feed scroll)"),
                        variable=self.p_scroll_between).pack(anchor="w", padx=8, pady=2)
        rrow = ttk.Frame(cfg); rrow.pack(fill="x", padx=8, pady=3)
        self.p_rotation_on = tk.IntVar(value=0)
        ttk.Checkbutton(rrow, text=T("Repeat rounds (rotation)", "Dobara round (rotation)"), variable=self.p_rotation_on).pack(side="left")
        ttk.Label(rrow, text=T("Rounds:", "Round:")).pack(side="left", padx=(8, 2))
        self.p_rotation = tk.StringVar(value="2")
        tk.Spinbox(rrow, from_=1, to=50, textvariable=self.p_rotation, width=5).pack(side="left")
        ttk.Label(cfg, text=T("Rotation = after posting all files, start again from the 1st (same files again) this many rounds.\n"
                              "Normally keep it OFF. ON is only useful with 'Allow duplicate'.",
                              "Rotation = saari files post karne ke baad phir pehli se dobara (wahi files) itne round.\n"
                              "Aam taur par BAND rakhein. Sirf 'Allow duplicate' ke sath kaam ka hai."),
                  style="Muted.TLabel", justify="left").pack(anchor="w", padx=8)
        self.p_rot_gap = DurationSingle(cfg, T("Gap between rounds:", "Rounds ke beech gap:"), 1, "min")
        self.p_allow_dup = tk.IntVar(value=0)
        ttk.Checkbutton(cfg, text=T("Allow duplicate video posting", "Allow duplicate video posting"), variable=self.p_allow_dup).pack(anchor="w", padx=8, pady=2)
        self.p_delete_after = tk.IntVar(value=0)
        ttk.Checkbutton(cfg, text=T("Delete posted video from device (only successfully posted)", "Delete posted video from device (sirf jo successfully post ho)"),
                        variable=self.p_delete_after).pack(anchor="w", padx=8, pady=2)
        ttk.Label(cfg, text=T("Videos come from each PAGE's folder (Content Sources) first; the ID's own folder is only a fallback.\n"
                               "Caption = the video's own .txt (TikTok caption, max 4 hashtags); if none, page hashtags.\n"
                               "Failed / not-confirmed posts are NOT deleted — they go to a 'Failed' folder inside the page folder.",
                               "Videos pehle PAGE ke folder (Content Sources) se; ID ka apna folder sirf backup.\n"
                               "Caption = video ki apni .txt (TikTok caption, max 4 hashtags); na ho to page hashtags.\n"
                               "Fail / confirm na hone wali post DELETE nahi hoti — page folder ke andar 'Failed' folder mein jati hai."),
                  foreground="#8ea0bd", justify="left").pack(anchor="w", padx=8, pady=6)
        ttk.Button(cfg, text=T("Page history", "Page history"), command=self._show_page_history).pack(anchor="w", padx=8, pady=4)

        self.post_panel = AccountPanel(f, self, on_start=self._start_post_batch, start_label="START")

    def _select_post_kind(self, k):
        self.post_kind.set(k)
        for key, b in self._side_btns.items():
            if key == k:
                b.config(bg="#2d7de0", fg="white")
            else:
                b.config(bg=PAL["panel2"], fg="#cfe0ff")

    def _start_post_batch(self, accounts):
        kind = self.post_kind.get()
        cfg = {
            "interval": self.p_interval.seconds(),
            "upload_wait": None,
            "rotation": (max(1, self._iv(self.p_rotation.get())) if self.p_rotation_on.get() else 1),
            "rot_gap": self.p_rot_gap.seconds(),
            "allow_dup": bool(self.p_allow_dup.get()),
            "scroll_between": bool(self.p_scroll_between.get()),
            "delete_after": bool(self.p_delete_after.get()),
        }
        jobs = []
        for a in accounts:
            files = list_media_in_folder(a.get("folder", ""), kind)
            has_page_folder = any(page_files(t.get("name") or "", kind) for t in posting_targets(a))
            if files or has_page_folder:
                jobs.append((a, files))
            else:
                self.log_q.put(T(f"[{a['name']}] folder not set/empty — skipped.", f"[{a['name']}] folder set/khali nahi — skip."))
        if not jobs:
            messagebox.showwarning(T("Wait", "Ruko"), T("IDs have no folder set or it's empty (set a folder in Accounts).", "IDs ke folder set nahi ya khali hain (Accounts mein folder dein)."))
            return
        ready = self._apply_ix_daily_limit([a for a, _ in jobs])
        rid = {a["id"] for a in ready}
        jobs = [(a, fl) for a, fl in jobs if a["id"] in rid]
        if not jobs:
            return
        limit = self._iv(self.p_threads.get()) or 1
        self.stop_flag.clear()
        self.log_q.put(T(f"=== POST START: {len(jobs)} IDs, threads {limit}, {kind} ===", f"=== POST START: {len(jobs)} IDs, threads {limit}, {kind} ==="))
        mgr = threading.Thread(target=self._run_pool, args=(jobs, kind, None, limit, cfg), daemon=True)
        mgr.start(); self.workers.append(mgr)

    # ---------- Interact page ----------
    def _build_interact_page(self):
        f = self.pages["Interact"]
        side = tk.Frame(f, bg=PAL["panel"], width=200); side.pack(side="left", fill="y"); side.pack_propagate(False)
        tk.Label(side, text="INTERACT", bg=PAL["panel"], fg="white", font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=14, pady=(14, 8))
        tk.Label(side, text=T("Tick the activities on the\nright, then START.\nNO posting on your wall.",
                              "Daayein activities ✔ karein,\nphir START. Wall par\nposting NAHI hogi."),
                 bg=PAL["panel"], fg="#cfe0ff", font=("Segoe UI", 11, "bold"), justify="left").pack(anchor="w", padx=14)
        body = ttk.Frame(f); body.pack(side="left", fill="both", expand=True)
        self._build_active_config(body)
        self.interact_panel = AccountPanel(f, self, on_start=self._start_active_batch,
                                           start_label="START", show_pages=False)

    def _build_active_config(self, f):
        _sc = self._scroll_area(f, width=640)
        grid = ttk.LabelFrame(_sc, text=T("ACTIVITIES", "ACTIVITIES")); grid.pack(fill="both", expand=True, padx=4, pady=4)
        self._active_grid = grid
        ttk.Label(grid, text=T("Tick IDs in the table, then press START.\n"
                               "These IDs will only scroll/like/comment etc. — they will NOT post anything.",
                               "Table me IDs ✔ karein, phir START dabayein.\n"
                               "Ye IDs sirf scroll/like/comment karengi — koi POSTING nahi hogi."),
                  foreground="#8ea0bd").pack(anchor="w", padx=8, pady=(4, 2))
        self.active_vars = {}
        saved = load_active_settings()

        def count_row(key, label, d_on, d_c):
            d = saved.get(key, {})
            row = ttk.Frame(grid); row.pack(fill="x", padx=6, pady=2)
            onv = tk.IntVar(value=1 if d.get("on", d_on) else 0)
            cnt = tk.StringVar(value=str(d.get("count", d_c)))
            ttk.Checkbutton(row, text=label, variable=onv, width=20).pack(side="left")
            tk.Spinbox(row, from_=0, to=100, textvariable=cnt, width=5).pack(side="left")
            self.active_vars[key] = {"on": onv, "count": cnt}

        def range_row(key, label, d_on, dmn, dmx):
            d = saved.get(key, {})
            row = ttk.Frame(grid); row.pack(fill="x", padx=6, pady=2)
            onv = tk.IntVar(value=1 if d.get("on", d_on) else 0)
            vmn = tk.StringVar(value=str(d.get("min", dmn))); vmx = tk.StringVar(value=str(d.get("max", dmx)))
            ttk.Checkbutton(row, text=label, variable=onv, width=20).pack(side="left")
            tk.Spinbox(row, from_=0, to=120, textvariable=vmn, width=4).pack(side="left")
            ttk.Label(row, text="to").pack(side="left", padx=2)
            tk.Spinbox(row, from_=0, to=120, textvariable=vmx, width=4).pack(side="left")
            ttk.Label(row, text="min").pack(side="left")
            self.active_vars[key] = {"on": onv, "min": vmn, "max": vmx}

        count_row("check_notification", "Check Notification", True, 3)
        range_row("scroll_feed", "Scroll New Feed", True, 2, 3)
        range_row("check_stories", "Check Stories", True, 1, 1)
        range_row("scroll_videos", "Scroll Videos", True, 2, 3)
        range_row("scroll_reels", "Scroll Reels", True, 1, 3)
        count_row("confirm_friends", "Confirm Friends", True, 4)
        count_row("reaction", "Reaction on Post", True, 2)

        d = saved.get("check_message", {})
        mrow = ttk.Frame(grid); mrow.pack(fill="x", padx=6, pady=2)
        self._m_on = tk.IntVar(value=1 if d.get("on", True) else 0)
        self._m_cnt = tk.StringVar(value=str(d.get("count", 2)))
        self._m_rep = tk.IntVar(value=1 if d.get("reply_on", True) else 0)
        self._m_txt = tk.StringVar(value=d.get("reply_text", "{Hi|Hey|Holla}{\U0001F642|\U0001F60D}"))
        ttk.Checkbutton(mrow, text="Check message", variable=self._m_on, width=20).pack(side="left")
        tk.Spinbox(mrow, from_=0, to=100, textvariable=self._m_cnt, width=5).pack(side="left")
        ttk.Checkbutton(mrow, text="Reply", variable=self._m_rep).pack(side="left", padx=4)
        ttk.Entry(mrow, textvariable=self._m_txt, width=22).pack(side="left")

        d = saved.get("comments", {})
        crow = ttk.Frame(grid); crow.pack(fill="x", padx=6, pady=2)
        self._c_on = tk.IntVar(value=1 if d.get("on", True) else 0)
        self._c_cnt = tk.StringVar(value=str(d.get("count", 1)))
        self._c_txt = tk.StringVar(value=d.get("text", "good, I agree with you"))
        ttk.Checkbutton(crow, text="Comments on Post", variable=self._c_on, width=20).pack(side="left")
        tk.Spinbox(crow, from_=0, to=100, textvariable=self._c_cnt, width=5).pack(side="left")
        ttk.Entry(crow, textvariable=self._c_txt, width=24).pack(side="left", padx=4)

        count_row("add_friends", T("Add friends (suggest)", "Friends add (suggest)"), False, 3)

        def list_row(key, label, d_c, hint):
            d = saved.get(key, {})
            row = ttk.Frame(grid); row.pack(fill="x", padx=6, pady=2)
            onv = tk.IntVar(value=1 if d.get("on") else 0)
            cnt = tk.StringVar(value=str(d.get("count", d_c)))
            src = tk.StringVar(value=d.get("src", ""))
            ttk.Checkbutton(row, text=label, variable=onv, width=20).pack(side="left")
            tk.Spinbox(row, from_=0, to=100, textvariable=cnt, width=5).pack(side="left")
            ttk.Entry(row, textvariable=src, width=18).pack(side="left", padx=4)
            ttk.Button(row, text="...", width=3, command=lambda: src.set(
                filedialog.askopenfilename(title=hint, filetypes=[("Text", "*.txt"), ("All", "*.*")]) or src.get())
                       ).pack(side="left")
            self.active_vars[key] = {"on": onv, "count": cnt, "src": src}

        list_row("add_friends_uid", T("Add friends (UID list)", "Friends add (UID list)"), 3, "UID / profile links .txt")
        list_row("follow_pages", T("Follow pages (list)", "Pages follow (list)"), 3, "Page links / IDs .txt")
        ck = saved.get("checkin", {})
        ckr = ttk.Frame(grid); ckr.pack(fill="x", padx=6, pady=2)
        self._ck_on = tk.IntVar(value=1 if ck.get("on") else 0)
        self._ck_photo = tk.IntVar(value=1 if ck.get("photo") else 0)
        self._ck_cap = tk.StringVar(value=ck.get("caption", ""))
        self._ck_folder = tk.StringVar(value=ck.get("folder", ""))
        ttk.Checkbutton(ckr, text=T("Check-in post (IP city)", "Check-in post (IP city)"), variable=self._ck_on,
                        width=20).pack(side="left")
        ttk.Entry(ckr, textvariable=self._ck_cap, width=18).pack(side="left", padx=2)
        ttk.Checkbutton(ckr, text=T("+ photo (from photo folder)", "+ photo (photo folder se)"),
                        variable=self._ck_photo).pack(side="left", padx=2)

        # ---- Photo post (bina check-in) + photo folder ----
        pp = saved.get("photo_post", {})
        self._pp_on = tk.IntVar(value=1 if pp.get("on") else 0)
        self._pp_cap = tk.StringVar(value=pp.get("caption", ""))
        if not self._ck_folder.get():
            self._ck_folder.set(pp.get("folder", ""))
        ppr = ttk.Frame(grid); ppr.pack(fill="x", padx=6, pady=2)
        ttk.Checkbutton(ppr, text=T("Photo post (no check-in)", "Photo post (bina check-in)"), variable=self._pp_on,
                        width=20).pack(side="left")
        ttk.Entry(ppr, textvariable=self._pp_cap, width=18).pack(side="left", padx=2)
        pfr = ttk.Frame(grid); pfr.pack(fill="x", padx=6, pady=2)
        ttk.Label(pfr, text=T("   Photo folder:", "   Photo folder:"), width=20).pack(side="left")
        ttk.Entry(pfr, textvariable=self._ck_folder, width=30).pack(side="left", padx=2)
        ttk.Button(pfr, text=T("Select folder", "Folder chuno"), command=lambda: self._ck_folder.set(
            filedialog.askdirectory(title=T("Photo folder (one photo per ID)", "Photo folder (har ID ki alag photo)"))
            or self._ck_folder.get())).pack(side="left", padx=2)

        # ---- Create story (photo ya video) ----
        sy = saved.get("story", {})
        self._sy_on = tk.IntVar(value=1 if sy.get("on") else 0)
        self._sy_cnt = tk.StringVar(value=str(sy.get("count", 1)))
        self._sy_folder = tk.StringVar(value=sy.get("folder", ""))
        syr = ttk.Frame(grid); syr.pack(fill="x", padx=6, pady=2)
        ttk.Checkbutton(syr, text=T("Create story", "Story lagao"), variable=self._sy_on, width=20).pack(side="left")
        tk.Spinbox(syr, from_=1, to=10, textvariable=self._sy_cnt, width=5).pack(side="left")
        ttk.Entry(syr, textvariable=self._sy_folder, width=22).pack(side="left", padx=4)
        ttk.Button(syr, text=T("Select folder", "Folder chuno"), command=lambda: self._sy_folder.set(
            filedialog.askdirectory(title=T("Story folder (photos or videos)", "Story folder (photo ya video)"))
            or self._sy_folder.get())).pack(side="left", padx=2)
        ps = saved.get("page_status", {})
        psr = ttk.Frame(grid); psr.pack(fill="x", padx=6, pady=2)
        self._ps_on = tk.IntVar(value=1 if ps.get("on") else 0)
        self._ps_del = tk.IntVar(value=1 if ps.get("delete_copyright") else 0)
        di = saved.get("detect_info", {"on": True})
        dir_ = ttk.Frame(grid); dir_.pack(fill="x", padx=6, pady=2)
        self._di_on = tk.IntVar(value=1 if di.get("on", True) else 0)
        ttk.Checkbutton(dir_, text=T("Detect name, DOB, gender, location", "Naam, DOB, gender, location khud lao"),
                        variable=self._di_on).pack(side="left")
        pl = saved.get("primary_loc", {})
        plr = ttk.Frame(grid); plr.pack(fill="x", padx=6, pady=2)
        self._pl_on = tk.IntVar(value=1 if pl.get("on") else 0)
        ttk.Checkbutton(plr, text=T("Check primary location", "Primary location check"),
                        variable=self._pl_on).pack(side="left")
        pm = saved.get("pro_mode", {})
        pmr = ttk.Frame(grid); pmr.pack(fill="x", padx=6, pady=2)
        self._pm_on = tk.IntVar(value=1 if pm.get("on") else 0)
        ttk.Checkbutton(pmr, text=T("Turn ON Professional mode (ID)", "Professional mode ON (ID)"),
                        variable=self._pm_on).pack(side="left")
        ttk.Checkbutton(psr, text=T("Status + monetization (ID + pages)", "Status + monetization (ID + pages)"),
                        variable=self._ps_on, width=26).pack(side="left")
        ttk.Checkbutton(psr, text=T("Auto-delete copyright flagged", "Copyright flagged khud delete"),
                        variable=self._ps_del).pack(side="left", padx=4)

        # ---- GROUP activities (ab isi list me) ----
        ttk.Separator(grid, orient="horizontal").pack(fill="x", padx=6, pady=4)
        gj = saved.get("group_join", {})
        jr = ttk.Frame(grid); jr.pack(fill="x", padx=6, pady=2)
        self._gj_on = tk.IntVar(value=1 if gj.get("on") else 0)
        self._gj_cnt = tk.StringVar(value=str(gj.get("count", 5)))
        ttk.Checkbutton(jr, text=T("Join trending groups", "Trending groups join"), variable=self._gj_on, width=20).pack(side="left")
        tk.Spinbox(jr, from_=1, to=50, textvariable=self._gj_cnt, width=5).pack(side="left")

        ga = saved.get("group_active", {})
        gar = ttk.Frame(grid); gar.pack(fill="x", padx=6, pady=2)
        self._ga_on = tk.IntVar(value=1 if ga.get("on") else 0)
        self._ga_min = tk.StringVar(value=str(ga.get("min", 3)))
        ttk.Checkbutton(gar, text=T("Active in groups (min)", "Groups me active (min)"), variable=self._ga_on, width=20).pack(side="left")
        tk.Spinbox(gar, from_=1, to=120, textvariable=self._ga_min, width=5).pack(side="left")
        self._ga_like = tk.IntVar(value=1 if ga.get("like", True) else 0)
        ttk.Checkbutton(gar, text="Like", variable=self._ga_like).pack(side="left", padx=4)
        self._ga_comment = tk.IntVar(value=1 if ga.get("comment", True) else 0)
        ttk.Checkbutton(gar, text=T("Comment", "Comment"), variable=self._ga_comment).pack(side="left")

        gp = saved.get("group_post", {})
        gpr = ttk.Frame(grid); gpr.pack(fill="x", padx=6, pady=2)
        self._gp_on = tk.IntVar(value=1 if gp.get("on") else 0)
        ttk.Checkbutton(gpr, text=T("Post in groups (text →)", "Groups me post (text →)"), variable=self._gp_on, width=20).pack(side="left")
        self._gp_txt = tk.StringVar(value=gp.get("text", ""))
        ttk.Entry(gpr, textvariable=self._gp_txt, width=24).pack(side="left", padx=4)
        ttk.Label(grid, text=T("Groups: bot writes relevant comments & skips hate/nudity (AI key in Settings = better).",
                               "Groups: bot relevant comment likhta hai aur hate/nudity skip karta hai (AI key = behtar)."),
                  foreground="#8ea0bd").pack(anchor="w", padx=8, pady=(2, 0))

        opt = ttk.Frame(grid); opt.pack(fill="x", padx=6, pady=4)
        ttk.Label(opt, text="Loops:").pack(side="left")
        self.active_loops = tk.StringVar(value=str(saved.get("loops", 1)))
        tk.Spinbox(opt, from_=1, to=50, textvariable=self.active_loops, width=5).pack(side="left", padx=4)
        ttk.Label(opt, text="Threads:").pack(side="left", padx=(10, 2))
        self.active_threads = tk.StringVar(value="5")
        tk.Spinbox(opt, from_=1, to=50, textvariable=self.active_threads, width=5).pack(side="left")
        ttk.Button(grid, text=T("Save settings", "Save settings"), command=self._save_active_settings).pack(anchor="e", padx=8, pady=4)

    def _collect_active_settings(self):
        v = self.active_vars
        def iv(x): return self._iv(x)
        return {
            "check_notification": {"on": bool(v["check_notification"]["on"].get()), "count": iv(v["check_notification"]["count"].get())},
            "scroll_feed": {"on": bool(v["scroll_feed"]["on"].get()), "min": iv(v["scroll_feed"]["min"].get()), "max": iv(v["scroll_feed"]["max"].get())},
            "check_stories": {"on": bool(v["check_stories"]["on"].get()), "min": iv(v["check_stories"]["min"].get()), "max": iv(v["check_stories"]["max"].get())},
            "scroll_videos": {"on": bool(v["scroll_videos"]["on"].get()), "min": iv(v["scroll_videos"]["min"].get()), "max": iv(v["scroll_videos"]["max"].get())},
            "scroll_reels": {"on": bool(v["scroll_reels"]["on"].get()), "min": iv(v["scroll_reels"]["min"].get()), "max": iv(v["scroll_reels"]["max"].get())},
            "confirm_friends": {"on": bool(v["confirm_friends"]["on"].get()), "count": iv(v["confirm_friends"]["count"].get())},
            "reaction": {"on": bool(v["reaction"]["on"].get()), "count": iv(v["reaction"]["count"].get())},
            "check_message": {"on": bool(self._m_on.get()), "count": iv(self._m_cnt.get()),
                               "reply_on": bool(self._m_rep.get()), "reply_text": self._m_txt.get()},
            "comments": {"on": bool(self._c_on.get()), "count": iv(self._c_cnt.get()), "text": self._c_txt.get()},
            "group_join": {"on": bool(self._gj_on.get()), "count": iv(self._gj_cnt.get()) or 5},
            "group_active": {"on": bool(self._ga_on.get()), "min": iv(self._ga_min.get()) or 3,
                             "like": bool(self._ga_like.get()), "comment": bool(self._ga_comment.get())},
            "group_post": {"on": bool(self._gp_on.get()), "text": self._gp_txt.get()},
            "add_friends": {"on": bool(v["add_friends"]["on"].get()), "count": iv(v["add_friends"]["count"].get())},
            "add_friends_uid": {"on": bool(v["add_friends_uid"]["on"].get()), "count": iv(v["add_friends_uid"]["count"].get()),
                                "src": v["add_friends_uid"]["src"].get().strip()},
            "follow_pages": {"on": bool(v["follow_pages"]["on"].get()), "count": iv(v["follow_pages"]["count"].get()),
                             "src": v["follow_pages"]["src"].get().strip()},
            "page_status": {"on": bool(self._ps_on.get()), "delete_copyright": bool(self._ps_del.get())},
            "pro_mode": {"on": bool(self._pm_on.get())},
            "detect_info": {"on": bool(self._di_on.get())},
            "primary_loc": {"on": bool(self._pl_on.get())},
            "checkin": {"on": bool(self._ck_on.get()), "photo": bool(self._ck_photo.get()),
                        "caption": self._ck_cap.get(), "folder": self._ck_folder.get().strip()},
            "photo_post": {"on": bool(self._pp_on.get()), "caption": self._pp_cap.get(),
                           "folder": self._ck_folder.get().strip()},
            "story": {"on": bool(self._sy_on.get()), "count": iv(self._sy_cnt.get()) or 1,
                      "folder": self._sy_folder.get().strip()},
            "loops": iv(self.active_loops.get()) or 1,
        }

    def _save_active_settings(self):
        save_active_settings(self._collect_active_settings())
        messagebox.showinfo(T("Done", "Done"), T("Active settings saved.", "Active settings save ho gayin."))

    def _start_active_batch(self, accounts):
        s = self._collect_active_settings(); save_active_settings(s)
        ready = self._apply_ix_daily_limit(accounts)
        if not ready:
            return
        limit = self._iv(self.active_threads.get()) or 1
        self.stop_flag.clear()
        self.log_q.put(T(f"=== ACTIVE START: {len(ready)} IDs, threads {limit} ===", f"=== ACTIVE START: {len(ready)} IDs, threads {limit} ==="))
        mgr = threading.Thread(target=self._run_active_pool, args=(ready, s, limit), daemon=True)
        mgr.start(); self.workers.append(mgr)

    # ---------- LD Working page (multiple LD instances, independent of web) ----------
    def _build_ldwork_page(self):
        f = self.pages["LDPlayer"]
        top = ttk.Frame(f); top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text=T("LDPLAYER — run multiple instances together",
                              "LDPLAYER — kai instances aik saath chalayein"),
                  font=("Segoe UI", 13, "bold")).pack(side="left")
        ttk.Button(top, text=T("Detect instances", "Instances detect karein"),
                   command=self._ldw_detect).pack(side="right")

        s = load_settings()
        mid = ttk.Frame(f); mid.pack(fill="both", expand=True, padx=10)
        left = ttk.LabelFrame(mid, text=T("LD players (tick to select)", "LD players (tick karein)"))
        left.pack(side="left", fill="both", expand=True, pady=4)
        selrow = ttk.Frame(left); selrow.pack(fill="x", padx=6, pady=(4, 0))
        ttk.Button(selrow, text=T("Select all", "Sab select"), command=self._ldw_all).pack(side="left")
        ttk.Button(selrow, text=T("Unselect all", "Sab unselect"), command=self._ldw_none).pack(side="left", padx=4)
        self.ldw_check_frame = ttk.Frame(left)
        self.ldw_check_frame.pack(fill="both", expand=True, padx=6, pady=6)
        self._ldw_vars = {}

        right = ttk.LabelFrame(mid, text=T("Settings (saved)", "Settings (save rehti hain)"))
        right.pack(side="left", fill="y", padx=6, pady=4)
        ttk.Label(right, text=T("Action:", "Kaam:")).pack(anchor="w", padx=8, pady=(6, 2))
        self.ldw_action = tk.StringVar(value=s.get("ldw_action", "reel"))
        for v, t in [("reel", "Post Reels"), ("video", "Post Video"), ("photo", "Post Photo"),
                     ("active", T("Active / warm-up", "Active / warm-up"))]:
            ttk.Radiobutton(right, text=t, value=v, variable=self.ldw_action).pack(anchor="w", padx=12)
        fr = ttk.Frame(right); fr.pack(fill="x", padx=8, pady=6)
        ttk.Label(fr, text=T("Videos folder:", "Videos folder:")).pack(side="left")
        self.ldw_folder = tk.StringVar(value=s.get("ldw_folder", ""))
        ttk.Entry(fr, textvariable=self.ldw_folder, width=16).pack(side="left", padx=4)
        ttk.Button(fr, text="...", width=3, command=self._ldw_pick_folder).pack(side="left")
        ttk.Label(right, text=T("Pick once — it stays saved. Videos auto-push to each LD and auto-select.",
                                "Aik dafa chunein — save rehta hai. Videos khud LD me push + auto-select hoti hain."),
                  foreground="#8ea0bd").pack(anchor="w", padx=8)
        gp = ttk.Frame(right); gp.pack(fill="x", padx=8, pady=2)
        ttk.Label(gp, text=T("Page (for reels/page post):", "Page (reel/page post ke liye):")).pack(side="left")
        self.ldw_page = tk.StringVar(value=s.get("ldw_page", ""))
        ttk.Entry(gp, textvariable=self.ldw_page, width=16).pack(side="left", padx=4)
        ttk.Label(right, text=T("Caption / hashtags:", "Caption / hashtags:")).pack(anchor="w", padx=8)
        self.ldw_caption = tk.Text(right, height=3, width=26)
        self.ldw_caption.pack(fill="x", padx=8, pady=2)
        pr = ttk.Frame(right); pr.pack(fill="x", padx=8, pady=4)
        ttk.Label(pr, text=T("Max at once:", "Aik waqt max:")).pack(side="left")
        self.ldw_threads = tk.StringVar(value="3")
        tk.Spinbox(pr, from_=1, to=30, textvariable=self.ldw_threads, width=5).pack(side="left", padx=4)

        # Instance control buttons (checked players par)
        ctl = ttk.Frame(f); ctl.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Label(ctl, text=T("Checked players:", "Chuni players:")).pack(side="left")
        ttk.Button(ctl, text=T("Start", "Start"), command=lambda: self._ld_bulk("launch")).pack(side="left", padx=2)
        ttk.Button(ctl, text=T("Stop", "Stop"), command=lambda: self._ld_bulk("quit")).pack(side="left", padx=2)
        ttk.Button(ctl, text=T("Open Facebook", "Facebook kholein"), command=lambda: self._ld_bulk("fb")).pack(side="left", padx=2)
        ttk.Button(ctl, text="Screenshot", command=lambda: self._ld_bulk("shot")).pack(side="left", padx=2)

        act = ttk.Frame(f); act.pack(fill="x", padx=10, pady=8)
        ttk.Button(act, text="START (work)", command=self._ldw_start).pack(side="left")
        ttk.Button(act, text="STOP (work)", command=lambda: self.ld_stop.set()).pack(side="left", padx=6)
        ttk.Label(act, text=T("Working runs independently from the web Post tab — both can run together.",
                              "Working web Post tab se alag chalta hai — dono aik saath chal sakte hain."),
                  foreground="#8ea0bd").pack(side="left", padx=10)
        self._ldw_instances = []

    def _ld_bulk(self, what):
        idxs = self._ldw_indexes()
        if not idxs:
            messagebox.showwarning("LDPlayer", T("Tick at least one player.", "Kam az kam aik player tick karein."))
            return
        def run():
            log = lambda m: self.log_q.put(m)
            import tempfile
            for idx in idxs:
                try:
                    if what == "launch":
                        LD.launch(idx); log(f"LD[{idx}] start.")
                    elif what == "quit":
                        LD.quit(idx); log(f"LD[{idx}] stop.")
                    elif what == "fb":
                        LD.open_facebook(idx); log(f"LD[{idx}] Facebook opened.")
                    elif what == "shot":
                        p = os.path.join(tempfile.gettempdir(), f"ld_{idx}.png")
                        ok = LD.screenshot(idx, p)
                        log(f"LD[{idx}] screenshot: {p}" if ok else f"LD[{idx}] screenshot failed.")
                except Exception as e:
                    log(f"LD[{idx}] error: {e}")
        threading.Thread(target=run, daemon=True).start()
        # auto-detect jab tab bane
        self.after(600, lambda: threading.Thread(target=self._ldw_detect, daemon=True).start())

    def _ldw_pick_folder(self):
        d = filedialog.askdirectory(title="Videos folder")
        if d:
            self.ldw_folder.set(d)
            self._ldw_save()

    def _ldw_save(self):
        s = load_settings()
        s["ldw_folder"] = self.ldw_folder.get().strip()
        s["ldw_page"] = self.ldw_page.get().strip()
        s["ldw_action"] = self.ldw_action.get()
        save_settings(s)

    def _ldw_detect(self):
        if not LD.find_console():
            self.log_q.put(T("LDPlayer: ldconsole.exe not found. Set its path in Settings.",
                             "LDPlayer: ldconsole.exe nahi mila. Settings me path dein."))
            return
        self._ldw_instances = LD.list_instances()
        self.after(0, self._ldw_render)

    def _ldw_render(self):
        for w in self.ldw_check_frame.winfo_children():
            w.destroy()
        new = {}
        for it in self._ldw_instances:
            var = self._ldw_vars.get(it["index"], tk.IntVar(value=0))
            new[it["index"]] = var
            st = "running" if it["running"] else "stopped"
            ttk.Checkbutton(self.ldw_check_frame, variable=var,
                            text=f"[{it['index']}]  {it['title']}  ({st})").pack(anchor="w", pady=1)
        self._ldw_vars = new
        if not self._ldw_instances:
            ttk.Label(self.ldw_check_frame, text=T("(no instances found)", "(koi instance nahi mila)")).pack(anchor="w")

    def _ldw_all(self):
        for v in self._ldw_vars.values():
            v.set(1)

    def _ldw_none(self):
        for v in self._ldw_vars.values():
            v.set(0)

    def _ldw_indexes(self):
        return sorted([i for i, v in self._ldw_vars.items() if v.get()])

    def _ldw_start(self):
        idxs = self._ldw_indexes()
        if not idxs:
            messagebox.showwarning("LDPlayer", T("Tick at least one LD player.",
                                                 "Kam az kam aik LD player tick karein."))
            return
        self._ldw_save()
        action = self.ldw_action.get()
        folder = self.ldw_folder.get().strip()
        caption = self.ldw_caption.get("1.0", tk.END).strip()
        page = self.ldw_page.get().strip()
        if action != "active" and not folder:
            messagebox.showwarning("LDPlayer", T("Choose a videos folder.", "Videos folder chunein."))
            return
        limit = self._iv(self.ldw_threads.get()) or 1
        self.ld_stop.clear()
        self.log_q.put(f"=== LD WORKING START: {len(idxs)} players, {action} ===")
        mgr = threading.Thread(target=self._ldw_pool, args=(idxs, action, folder, caption, page, limit), daemon=True)
        mgr.start(); self.workers.append(mgr)

    def _ldw_pool(self, idxs, action, folder, caption, page, limit):
        log = lambda m: self.log_q.put(m)
        active, pending = [], list(idxs)
        while (pending or active) and not self.ld_stop.is_set():
            active = [t for t in active if t.is_alive()]
            while pending and len(active) < limit and not self.ld_stop.is_set():
                idx = pending.pop(0)
                t = threading.Thread(target=self._ldw_run_one, args=(idx, action, folder, caption, page), daemon=True)
                t.start(); active.append(t); self.workers.append(t)
                log(f"LD[{idx}] started (running {len(active)}, queued {len(pending)})")
            time.sleep(2)
        log("=== LD WORKING finished ===")

    def _ldw_run_one(self, idx, action, folder, caption, page):
        log = lambda m: self.log_q.put(m)
        page = f"LD:{idx}"
        try:
            LD.launch(idx); time.sleep(3)
            if action == "active":
                LD.fb_active(idx, load_active_settings() or {}, log=log)
                return
            files = list_media_in_folder(folder, action if action == "photo" else "video")
            seen, todo = set(), []
            for p in files:
                h = file_hash(p)
                if is_uploaded(page, h) or h in seen:
                    continue
                seen.add(h); todo.append((p, h))
            if not todo:
                log(f"LD[{idx}] no new file.")
                return
            for i, (p, h) in enumerate(todo, 1):
                if self.ld_stop.is_set():
                    break
                log(f"LD[{idx}] {i}/{len(todo)}: {os.path.basename(p)}")
                ok = LD.fb_post(idx, p, action, caption, log=log, page=page)
                if ok:
                    mark_uploaded(page, h)
                    add_page_history(page, p, "success", "")
                else:
                    add_page_history(page, p, "fail", "not confirmed")
                if i < len(todo):
                    time.sleep(random.randint(30, 60))
            log(f"LD[{idx}] done.")
        except Exception as e:
            log(f"LD[{idx}] error: {e}")

    # ---------- Schedule page ----------
    def _build_schedule_page(self):
        f = self.pages["Schedule"]
        cfg = ttk.LabelFrame(f, text="SCHEDULE"); cfg.pack(side="left", fill="y", padx=8, pady=8)
        ttk.Label(cfg, text=T("Action:", "Kaam:")).pack(anchor="w", padx=8, pady=(8, 2))
        self.sch_action = tk.StringVar(value="post")
        for v, t in [("post", T("Post (video/reel/photo)", "Post (video/reel/photo)")), ("active", T("Active / warm-up", "Active / warm-up"))]:
            ttk.Radiobutton(cfg, text=t, value=v, variable=self.sch_action).pack(anchor="w", padx=12)
        ttk.Label(cfg, text=T("Post kind:", "Post kind:")).pack(anchor="w", padx=8, pady=(8, 2))
        self.sch_kind = tk.StringVar(value="reel")
        for v, t in [("reel", T("Reels", "Reels")), ("video", T("Videos", "Videos")), ("photo", T("Photos", "Photos"))]:
            ttk.Radiobutton(cfg, text=t, value=v, variable=self.sch_kind).pack(anchor="w", padx=12)

        tr = ttk.Frame(cfg); tr.pack(fill="x", padx=8, pady=8)
        ttk.Label(tr, text=T("Time (for all IDs):", "Time (sab IDs ke liye):")).pack(side="left")
        self.sch_timepicker = TimePicker(tr, 8, 0, "PM")
        self.sch_timepicker.pack(side="left", padx=6)

        self.sch_diff = tk.IntVar(value=0)
        ttk.Checkbutton(cfg, text=T("DIFFERENT time per ID (set below)", "Har ID ke liye ALAG time (neeche set karein)"),
                        variable=self.sch_diff, command=self._toggle_sch_diff).pack(anchor="w", padx=8, pady=2)
        self.sch_diff_frame = ttk.LabelFrame(cfg, text=T("Per-ID time (checked IDs)", "Per-ID time (checked IDs)"))
        self._sch_time_pickers = {}

        pr = ttk.Frame(cfg); pr.pack(fill="x", padx=8, pady=4)
        ttk.Label(pr, text="Threads:").pack(side="left")
        self.sch_threads = tk.StringVar(value="5")
        tk.Spinbox(pr, from_=1, to=50, textvariable=self.sch_threads, width=5).pack(side="left", padx=4)

        self.schedule_panel = AccountPanel(f, self, on_start=self._start_schedule_batch, start_label="SCHEDULE START", show_pages=False)

    def _toggle_sch_diff(self):
        if self.sch_diff.get():
            self.sch_diff_frame.pack(fill="x", padx=8, pady=4)
            self._rebuild_sch_time_rows()
        else:
            self.sch_diff_frame.pack_forget()

    def _rebuild_sch_time_rows(self):
        for w in self.sch_diff_frame.winfo_children():
            w.destroy()
        self._sch_time_pickers = {}
        chosen = self.schedule_panel.checked_accounts() if hasattr(self, "schedule_panel") else []
        if not chosen:
            ttk.Label(self.sch_diff_frame, text=T("(First tick IDs in the right table, then re-check this)", "(Pehle right table mein IDs ✔ karein, phir ye dobara tick karein)")).pack(anchor="w", padx=6)
            return
        for a in chosen:
            row = ttk.Frame(self.sch_diff_frame); row.pack(fill="x", padx=6, pady=2)
            ttk.Label(row, text=a["name"][:18], width=18).pack(side="left")
            tp = TimePicker(row, 8, 0, "PM"); tp.pack(side="left")
            self._sch_time_pickers[a["id"]] = tp

    def _start_schedule_batch(self, accounts):
        ready = self._apply_ix_daily_limit(accounts)
        if not ready:
            return
        limit = self._iv(self.sch_threads.get()) or 1
        self.stop_flag.clear()

        def time_for(acc):
            if self.sch_diff.get() and acc["id"] in self._sch_time_pickers:
                return self._sch_time_pickers[acc["id"]].hhmm24()
            return self.sch_timepicker.hhmm24()

        if self.sch_action.get() == "active":
            s = self._collect_active_settings()
            self.log_q.put(T(f"=== SCHEDULE ACTIVE: {len(ready)} IDs ===", f"=== SCHEDULE ACTIVE: {len(ready)} IDs ==="))
            for a in ready:
                t = threading.Thread(target=self._run_active, args=(a, s, time_for(a)), daemon=True)
                t.start(); self.workers.append(t)
        else:
            kind = self.sch_kind.get()
            cfg = {"interval": (40, 90), "upload_wait": None, "rotation": 1, "rot_gap": 0,
                   "allow_dup": False, "scroll_between": False, "delete_after": False}
            self.log_q.put(T(f"=== SCHEDULE POST: {len(ready)} IDs ===", f"=== SCHEDULE POST: {len(ready)} IDs ==="))
            count = 0
            for a in ready:
                files = list_media_in_folder(a.get("folder", ""), kind)
                if not files:
                    self.log_q.put(T(f"[{a['name']}] folder empty — skipped.", f"[{a['name']}] folder khali — skip."))
                    continue
                t = threading.Thread(target=self._run_post, args=(a, files, kind, time_for(a), cfg), daemon=True)
                t.start(); self.workers.append(t); count += 1
            self.log_q.put(T(f"{count} IDs scheduled.", f"{count} IDs schedule ho gayin."))

    # ---------- Guide page (in-app help, English) ----------
    GUIDE_TEXT = """
ACCOUNTS (Bob Prime style):
 - Table columns: No, Profile, Name, UID, Email, Email2, Pass, 2FA, Primary
   location, Creation date, IP location, Status (Live / Checkpoint / Disabled /
   Suspended / Die), Last update, Group, Country, DOB, Gender, Pages.
 - Right-click a row: Open browser, Edit, Select/Unselect, Set group, Export,
   Delete. Group filter + Search at the top. Export ▾ = FULL (Excel/CSV),
   TXT (email|pass|2fa|uid|name) or UID — of ticked IDs (or all).
 - ADD ACCOUNTS: choose the column order (e.g. UID | Pass | 2FA | Email |
   Email2) or Auto-detect, optional Group → "Choose .txt & ADD NOW".
   Each ID goes into its own browser profile (P001, P002 …).
 - Browser profiles: "Create profiles" (e.g. 10), "Auto-assign IDs → profiles".
 - UID is saved automatically after login. Edit any field in "Add / edit ID".

CONTENT SOURCES (sidebar):
 - Main media folder: make one folder, inside it one folder per page with the
   page's name (e.g. "Funny Cats Page"). "Match folders to pages" — Posting and
   API Pages then take each page's videos from its own folder automatically.
 - TikTok creators: choose a page + paste YOUR TikTok profile link → Add →
   Fetch. Videos download in HD into that page's folder, each with a .txt
   caption (original caption, max 4 hashtags) which is used when posting.
   Auto fetch every X hours.
ACTIVE → Check-in post: posts on the profile with the location = the city of
 the ID's IP (e.g. Dallas, Texas), your caption, + a photo from a folder.

SURFSHARK — different IP for every browser (no proxies to buy):
 Proxy Management → "How to get Surfshark files?" → download WireGuard .conf
 files for many US cities → Import .conf files → Check tunnels → tick IDs →
 "Assign tunnels to ticked IDs". Each browser shows CHECK IP (IP, city,
 country) when it opens. If a tunnel fails, that ID's browser is NOT opened.

ACTIVE (Bob Prime style) — every browser does a different random plan:
 notifications, feed, stories, videos, reels, likes, comments, inbox + reply,
 confirm friends, add friends (suggestions), add friends from a UID list,
 follow pages from a list, groups join/active/post, page status
 (Flagged / Not Recommended / restrictions) + monetization info, optional
 auto-delete of copyright-flagged items. Lists = .txt, one link/UID per line;
 already-done entries are skipped next time.

NEW LOOK: left sidebar → Dashboard (cards + recent activity), Accounts,
Proxy Management, Posting, API Pages, Active & Groups, Schedule, LDPlayer,
Logs, System Settings, Guide.

FB BROWSER (built-in, no ixBrowser needed) — System Settings → Browser:
 - Every ID gets its own profile folder + its own fingerprint (screen size,
   CPU, RAM, graphics card, canvas/audio noise) that stays the same always.
 - With a proxy, timezone + language + location follow the proxy's country.
   WebRTC IP leak is blocked. Engine: chrome (your installed Chrome, most
   natural) / chromium (built-in, downloads once) / msedge.
 - Per row + Window W x H: browsers open side by side in a grid. Mute audio.

PROXY MANAGEMENT: paste proxies → Add to pool → Check all (shows Ready/Dead,
latency, IP + country) → tick IDs in Accounts → "Assign to ticked IDs".

LOGS: Post Logs (every post, browser + API, OK/Failed) and Activity Log.

API PAGES tab (official Facebook API — safest, no browser/captcha/2FA):
 1. "How to get token?" shows the steps: Business Manager → System user →
    assign your Pages → Generate token (pages_show_list, pages_manage_posts,
    pages_read_engagement, publish_video, business_management, Never expire).
 2. Paste token → Fetch pages. All your pages appear in the list.
 3. Click a page → set Post type (reel/video/photo/text), Times (e.g.
    10:00, 18:30), Days, Rotation (order/random/newest), source folders,
    caption rule → SAVE PAGE SETTINGS. Tick ✔ in "On" to turn automation on.
 4. Caption: same-name .txt next to the video > captions file (random line)
    > file name > fallback caption. Page hashtags are added at the end.
 5. Same file never posts twice on a page. POST NOW posts immediately.
    Failed ones show ✖ in Tasks — select and "Retry selected failed".
 (Keep the app open — it checks the times every 20 seconds.)
FACEBOOK UPLOADER — FEATURES & HOW TO USE
==========================================================

This app posts to Facebook from many accounts (Reels / Video / Photo),
keeps accounts active (warm-up), works in Groups, controls LDPlayer
emulators, solves captchas, handles 2FA, and can be controlled remotely.

----------------------------------------------------------
GETTING STARTED
----------------------------------------------------------
1. ixBrowser (recommended): install ixBrowser, turn ON its Local API,
   and create one profile per account (remember each profile NAME).
   ixBrowser gives each profile its own IP + fingerprint — no proxy needed.
2. Best practice: log in to each account once, by hand, inside its
   ixBrowser profile. This clears captcha/2FA once; the app then skips login.

----------------------------------------------------------
1) ACCOUNTS (Account Management tab)
----------------------------------------------------------
Add / edit IDs on the left form:
 - Name: a label for the ID.
 - Email / Phone, Password: Facebook login.
 - Page name (optional): not needed — pages are detected automatically.
 - ixBrowser Profile: optional. Leave empty and the app CREATES a separate
   ixBrowser profile for this ID by itself (email, password, 2FA saved in it).
 - Proxy: leave empty in ixBrowser mode.
 - 2FA secret key: if 2-factor is ON, paste the secret key (same one you
   put in Google Authenticator / 2fa.live). The app makes the OTP itself —
   no website needed, works offline.
 - LDPlayer index: set only if this ID runs inside an LDPlayer emulator.
 - Videos folder: pick once — the folder where this ID's videos are.
   The folder's videos show below. Daily max: max videos/day for this page.
 - Hashtags: write once; they attach to every post automatically.

Buttons:
 - Save ID, New/clear form.
 - Bulk import from file (.txt): one ID per line:
       email|password|2FA secret
   (also works with : , ; space or tab; 2FA optional; a 2FA secret with
   spaces like "ABCD EFGH IJKL MNOP" is fine; phone number instead of email ok).
 - IP check (selected): shows that ID's real IP (confirm proxy/profile).
 - Page History / ID History: every post (date/time, video, success/fail +
   reason) and every login/error. Export to CSV from the window.

Table (right): tick the ✔ box to select. Select all / Unselect all.
 Delete selected (Ctrl+Z restores a deleted ID). Status shows Live / Die.

LOGIN SELECTED IDs (top-left box):
 1. Import your .txt, tick the IDs in the table (or Select all).
 2. "Open at once": how many browsers open together (5 or 10 etc.).
 3. Press LOGIN selected IDs. Each ID opens in its OWN ixBrowser profile
    (created automatically if missing), logs in, types the 2FA code itself.
 FREE ixBrowser plan (no API): open the profiles in ixBrowser yourself
    (Open in Batches), press "Capture open ixBrowser profiles". Profiles whose
    saved username = ID email link automatically; others you pick in a list.
    After that the app opens those profiles itself (same fingerprint).
 4. With "Detect pages after login" ON, the app finds all Facebook pages
    of that ID and saves them. STOP stops the queue.

PAGES (under the table, also on the POST tab):
 - Click any ID row → its pages appear below.
 - Tick ✔ the pages you want to post on (Tick all / Untick all).
 - Posting goes ONLY to the ticked pages of the ticked IDs. Nothing is
   posted to unticked IDs or unticked pages.
 - "Detect pages" refreshes the page list of the clicked ID (your ticks stay).
 - No page ticked → old behaviour (Page name field, else the profile itself).

----------------------------------------------------------
2) POST (Reels / Video / Photo)
----------------------------------------------------------
Left sidebar: pick Post Reels / Video / Photo.
Options:
 - Concurrent threads: how many IDs run at the same time.
 - Interval between 2 posts: wait between videos (sec/min/hour).
 - During the gap, keep the ID active (scroll feed): looks human.
 - Rotation: repeat all videos N times.
 - Allow duplicate: by default already-posted / duplicate files are skipped.
 - Delete posted video from device: removes only successfully posted files.
Videos come from each ID's own folder (set in Accounts). Caption = that
page's hashtags. Tick IDs in the table, then START. STOP ends after the
current post.

----------------------------------------------------------
3) INTERACT / GROUPS
----------------------------------------------------------
Tick the activities on the right, then START. These IDs will NOT post on
your wall — they only stay active / work in groups.
Activities: Check Notification, Scroll Feed/Stories/Videos/Reels,
Confirm Friends, Reaction (Like), Check message + Reply (spintax
{Hi|Hey} picks one at random), Comments on Post.
Group activities (also here):
 - Join trending groups (count): bot auto-joins suggested/trending groups.
 - Active in groups (min) + Like + Comment: scrolls group feed, likes and
   writes a RELEVANT comment, and SKIPS hate speech / nudity posts.
 - Post in groups (text): also posts your text in groups.
Loops: repeat all activities N times. Threads: how many IDs at once.

----------------------------------------------------------
4) LDPLAYER (Android emulator)
----------------------------------------------------------
Detect instances (auto). Tick the players (Select all / Unselect all).
Controls: Start / Stop / Open Facebook / Screenshot.
Working: choose action (Reel/Video/Photo/Active), a Videos folder (saved),
a Page (for reels), caption, and how many at once. START (work) runs them;
it is independent from the web Post tab, so both can run together.
Videos auto-push into each emulator's gallery and auto-select.

----------------------------------------------------------
5) SCHEDULE
----------------------------------------------------------
Pick action (Post or Active), kind, and a time (with AM/PM). Use the same
time for all IDs, or tick "different time per ID" and set each. Threads sets
how many at once. SCHEDULE START waits for the time, then runs.

----------------------------------------------------------
6) SETTINGS
----------------------------------------------------------
 - Update: "Check for updates" — one click updates the app from GitHub,
   no re-download/reinstall.
 - Language: English (default) or Roman Urdu (applies after restart).
 - Browser: ixBrowser (recommended), Chrome, or custom. ixBrowser daily
   limit optional.
 - AI (smart comments + skip hate/nudity): pick a FREE provider (Groq or
   Google Gemini), paste its free key. Used by Groups mode.
   Groq keys: console.groq.com/keys  |  Gemini: aistudio.google.com/apikey
 - Captcha: free local OCR solves the "Enter the characters" captcha (needs
   Tesseract OCR installed once, free). 2captcha key = backup for reCAPTCHA.
   Your own OCR API keys let other software use this solver.
 - Daily auto-upload / Daily active: run automatically every day at a time.
 - Auto-start with Windows: opens in the tray on boot.
 - Remote access: Telegram bot (control + alerts from anywhere) and a public
   tunnel for the phone app. LDPlayer path if not auto-found.

----------------------------------------------------------
LOGIN / SESSIONS
----------------------------------------------------------
After the first login the app does NOT log in again — each account's
session is saved in its profile, so next time it starts work directly.
You can also log in yourself beforehand (safest). If 2FA appears and a
secret key is set, the app enters the code automatically.

----------------------------------------------------------
STAYING SAFE
----------------------------------------------------------
Don't post too fast from too many IDs at once. Use gaps + warm-up. Keep
one profile/IP per ID. Warm up new IDs for a few days before posting.

----------------------------------------------------------
UPDATES
----------------------------------------------------------
Settings -> Update -> Check for updates. If a newer version exists it
downloads and replaces itself, then restarts. No manual download needed.
"""

    def _build_guide_page(self):
        f = self.pages["Guide"]
        bar = ttk.Frame(f); bar.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(bar, text="GUIDE — how everything works",
                  font=("Segoe UI", 13, "bold")).pack(side="left")
        wrap = ttk.Frame(f); wrap.pack(fill="both", expand=True, padx=10, pady=8)
        sb = ttk.Scrollbar(wrap)
        sb.pack(side="right", fill="y")
        txt = tk.Text(wrap, wrap="word", yscrollcommand=sb.set,
                      font=("Consolas", 11), bg=PAL["field"], fg=PAL["text"], relief="flat", padx=10, pady=8)
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.insert("1.0", self.GUIDE_TEXT)
        txt.config(state="disabled")

    # ---------- Settings page ----------
    def _build_settings_page(self):
        f = self._scroll_area(self.pages["Settings"], full=True)
        s = load_settings()

        # Update section (sab se upar)
        upd = ttk.LabelFrame(f, text=T("Update", "Update"))
        upd.pack(fill="x", padx=12, pady=(12, 0))
        ur = ttk.Frame(upd); ur.pack(fill="x", padx=10, pady=6)
        ttk.Label(ur, text=T(f"Current version: {APP_VERSION}", f"Mojooda version: {APP_VERSION}"),
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(ur, text=T("Check for updates", "Update check karein"),
                   command=self._check_update_click).pack(side="left", padx=10)
        self.update_lbl = ttk.Label(ur, text="", foreground="#06c")
        self.update_lbl.pack(side="left", padx=6)
        self.set_auto_upd = tk.IntVar(value=1 if s.get("auto_update", True) else 0)
        ttk.Checkbutton(ur, text=T("Auto-update (install new versions by itself)", "Auto-update (nayi version khud lagao)"),
                        variable=self.set_auto_upd,
                        command=lambda: save_settings({"auto_update": bool(self.set_auto_upd.get())})).pack(side="left", padx=12)
        ttk.Label(upd, text=T("One click checks GitHub and updates — no need to re-download.",
                              "Aik click GitHub se check kar ke update kar deta hai — dobara download nahi."),
                  foreground="#8ea0bd").pack(anchor="w", padx=10, pady=(0, 6))

        lang = ttk.LabelFrame(f, text="Language")
        lang.pack(fill="x", padx=12, pady=(12, 0))
        lr0 = ttk.Frame(lang); lr0.pack(fill="x", padx=10, pady=6)
        ttk.Label(lr0, text="Language:").pack(side="left")
        self.set_lang_disp = tk.StringVar(value=("English" if s.get("language", "en") == "en" else "Roman Urdu"))
        ttk.Combobox(lr0, textvariable=self.set_lang_disp, values=["English", "Roman Urdu"],
                     width=14, state="readonly").pack(side="left", padx=6)
        ttk.Label(lr0, text=T("(applies after restart)", "(tabdeeli restart ke baad)"),
                  foreground="#8ea0bd").pack(side="left")
        ttk.Label(lr0, text=T("   Text size:", "   Text size:")).pack(side="left")
        self.set_zoom = tk.StringVar(value=f"{int(round(ui_zoom() * 100))}%")
        zc = ttk.Combobox(lr0, textvariable=self.set_zoom, width=6, state="readonly",
                          values=("100%", "125%", "150%", "175%", "200%", "225%"))
        zc.pack(side="left", padx=4)
        def _zoom_now(e=None):
            try:
                save_settings({"ui_zoom": int(self.set_zoom.get().rstrip("%")) / 100.0})
                if messagebox.askyesno("Text size", T("Saved. Restart the app now to apply the new size?",
                                                      "Save ho gaya. Naya size lagane ke liye app abhi restart karein?")):
                    self._restart_app()
            except Exception:
                pass
        zc.bind("<<ComboboxSelected>>", _zoom_now)

        box = ttk.LabelFrame(f, text=T("Browser (default: FB Browser — built-in)", "Browser (default: FB Browser — built-in)"))
        box.pack(fill="x", padx=12, pady=12)
        self.set_browser = tk.StringVar(value=s.get("browser", "fbbrowser"))
        for val, text in [("fbbrowser", T("FB Browser (built-in anti-detect: own fingerprint + proxy timezone per ID) — recommended",
                                          "FB Browser (built-in anti-detect: har ID ka fingerprint + proxy timezone) — recommended")),
                          ("ixbrowser", T("ixBrowser (needs ixBrowser app)", "ixBrowser (ixBrowser app chahiye)")),
                          ("chrome", T("Google Chrome (installed on PC)", "Google Chrome (PC par installed)")),
                          ("custom", T("Custom browser (path below)", "Custom browser (neeche path)"))]:
            ttk.Radiobutton(box, text=text, value=val, variable=self.set_browser).pack(anchor="w", padx=12, pady=3)
        pr = ttk.Frame(box); pr.pack(fill="x", padx=12, pady=4)
        ttk.Label(pr, text=T("Custom path:", "Custom path:")).pack(side="left")
        self.set_path = tk.StringVar(value=s.get("browser_path", ""))
        ttk.Entry(pr, textvariable=self.set_path, width=45).pack(side="left", padx=4)
        ttk.Button(pr, text="Browse", command=self._pick_browser_path).pack(side="left")

        fr = ttk.Frame(box); fr.pack(fill="x", padx=12, pady=4)
        ttk.Label(fr, text=T("FB Browser engine:", "FB Browser engine:")).pack(side="left")
        self.set_fbb_engine = tk.StringVar(value=s.get("fbb_engine", "chrome"))
        ttk.Combobox(fr, textvariable=self.set_fbb_engine, values=("chrome", "chromium", "msedge"),
                     width=9, state="readonly").pack(side="left", padx=4)
        ttk.Label(fr, text=T("Per row:", "Ek line mein:")).pack(side="left", padx=(10, 0))
        self.set_fbb_row = tk.StringVar(value=str(s.get("fbb_per_row", 4)))
        tk.Spinbox(fr, from_=1, to=10, textvariable=self.set_fbb_row, width=4).pack(side="left", padx=3)
        ttk.Label(fr, text=T("Window W x H:", "Window W x H:")).pack(side="left", padx=(10, 0))
        self.set_fbb_w = tk.StringVar(value=str(s.get("fbb_w", 900)))
        self.set_fbb_h = tk.StringVar(value=str(s.get("fbb_h", 800)))
        ttk.Entry(fr, textvariable=self.set_fbb_w, width=5).pack(side="left", padx=2)
        ttk.Entry(fr, textvariable=self.set_fbb_h, width=5).pack(side="left", padx=2)
        self.set_fbb_tile = tk.IntVar(value=1 if s.get("fbb_auto_tile", True) else 0)
        ttk.Checkbutton(fr, text=T("Auto-fit all browsers on screen", "Sab browsers screen par khud fit"),
                        variable=self.set_fbb_tile).pack(side="left", padx=8)
        self.set_fbb_mute = tk.IntVar(value=1 if s.get("fbb_mute", True) else 0)
        ttk.Checkbutton(fr, text=T("Mute audio", "Awaz band"), variable=self.set_fbb_mute).pack(side="left", padx=8)
        ttk.Label(fr, text=T("Language (empty = from proxy):", "Zaban (khali = proxy se):")).pack(side="left", padx=(10, 0))
        self.set_fbb_lang = tk.StringVar(value=s.get("fbb_lang", ""))
        ttk.Entry(fr, textvariable=self.set_fbb_lang, width=7).pack(side="left", padx=2)
        ipr = ttk.Frame(fr); ipr.pack(side="left", padx=(12, 0))
        self._build_ipf_controls(ipr)

        lr = ttk.Frame(box); lr.pack(fill="x", padx=12, pady=4)
        self.set_ix_limit_on = tk.IntVar(value=1 if s.get("ix_limit_on") else 0)
        ttk.Checkbutton(lr, text=T("Enable ixBrowser daily limit:", "ixBrowser roz ki limit lagao:"), variable=self.set_ix_limit_on).pack(side="left")
        self.set_ix_max = tk.StringVar(value=str(s.get("ix_max_per_day", 10)))
        tk.Spinbox(lr, from_=1, to=1000, textvariable=self.set_ix_max, width=6).pack(side="left", padx=4)
        ttk.Label(lr, text=T("profiles/day (OFF = no limit)", "profile/din (OFF = koi limit nahi)"), foreground="#8ea0bd").pack(side="left")

        # AI (relevant comments + hate/nudity skip)
        aib = ttk.LabelFrame(f, text=T("AI (smart comments + skip hate/nudity)",
                                       "AI (smart comments + hate/nudity skip)"))
        aib.pack(fill="x", padx=12, pady=10)
        # FREE providers preset
        self._ai_presets = {
            "Groq (FREE)": ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant"),
            "Google Gemini (FREE)": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.0-flash"),
            "OpenRouter (free models)": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.1-8b-instruct:free"),
            "OpenAI (paid)": ("https://api.openai.com/v1", "gpt-4o-mini"),
        }
        pr0 = ttk.Frame(aib); pr0.pack(fill="x", padx=10, pady=(6, 2))
        ttk.Label(pr0, text=T("Provider:", "Provider:")).pack(side="left")
        self.set_ai_provider = ttk.Combobox(pr0, values=list(self._ai_presets.keys()), state="readonly", width=24)
        self.set_ai_provider.pack(side="left", padx=6)
        self.set_ai_provider.bind("<<ComboboxSelected>>", lambda e: self._ai_apply_preset())

        ar = ttk.Frame(aib); ar.pack(fill="x", padx=10, pady=6)
        ttk.Label(ar, text=T("API key:", "API key:")).pack(side="left")
        self.set_ai_key = tk.StringVar(value=s.get("ai_key", ""))
        ttk.Entry(ar, textvariable=self.set_ai_key, width=34).pack(side="left", padx=6, fill="x", expand=True)
        ar2 = ttk.Frame(aib); ar2.pack(fill="x", padx=10, pady=2)
        ttk.Label(ar2, text=T("Model:", "Model:")).pack(side="left")
        self.set_ai_model = tk.StringVar(value=s.get("ai_model", "llama-3.1-8b-instant"))
        ttk.Entry(ar2, textvariable=self.set_ai_model, width=22).pack(side="left", padx=6)
        ttk.Label(ar2, text="Base:").pack(side="left", padx=(8, 0))
        self.set_ai_base = tk.StringVar(value=s.get("ai_base", "https://api.groq.com/openai/v1"))
        ttk.Entry(ar2, textvariable=self.set_ai_base, width=30).pack(side="left", padx=6)
        ttk.Label(aib, text=T("FREE keys: Groq → console.groq.com/keys  |  Gemini → aistudio.google.com/apikey\n"
                              "Pick a provider above, paste its free key. Used for relevant comments + hate/nudity skip.",
                              "FREE keys: Groq → console.groq.com/keys  |  Gemini → aistudio.google.com/apikey\n"
                              "Upar provider chunein, uski free key paste karein. Relevant comments + hate/nudity skip ke liye."),
                  foreground="#8ea0bd").pack(anchor="w", padx=10, pady=(0, 6))

        # Captcha solver
        cap = ttk.LabelFrame(f, text=T("Captcha solver", "Captcha solver"))
        cap.pack(fill="x", padx=12, pady=10)
        self.set_local_ocr = tk.IntVar(value=1 if s.get("local_ocr", True) else 0)
        ttk.Checkbutton(cap, text=T("Solve text captcha locally for FREE (OCR) — 'Enter the characters you see'",
                                    "Text captcha locally FREE solve karein (OCR) — 'Enter the characters you see'"),
                        variable=self.set_local_ocr).pack(anchor="w", padx=10, pady=3)
        cr = ttk.Frame(cap); cr.pack(fill="x", padx=10, pady=4)
        ttk.Label(cr, text=T("2captcha API key (only for reCAPTCHA/hCaptcha, optional):",
                             "2captcha API key (sirf reCAPTCHA/hCaptcha ke liye, optional):")).pack(side="left")
        self.set_captcha_key = tk.StringVar(value=s.get("captcha_key", ""))
        ttk.Entry(cr, textvariable=self.set_captcha_key, width=30).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(cap, text=T("The 'Enter the characters' captcha is solved free by built-in OCR. "
                              "2captcha is only a fallback for image-selection (reCAPTCHA) captchas.",
                              "'Enter the characters' wala captcha built-in OCR free solve karta hai. "
                              "2captcha sirf reCAPTCHA (tasveer chunne wale) ke liye backup hai."),
                  foreground="#8ea0bd").pack(anchor="w", padx=10, pady=(0, 4))

        # Our own API keys (doosre software ke liye)
        ap = ttk.LabelFrame(f, text=T("Your OCR API keys (use in other software)",
                                      "Aapki OCR API keys (doosre software me use karein)"))
        ap.pack(fill="both", padx=12, pady=10)
        arow = ttk.Frame(ap); arow.pack(fill="x", padx=10, pady=4)
        ttk.Button(arow, text=T("Generate key", "Nayi key banayein"), command=self._gen_api_key).pack(side="left")
        ttk.Button(arow, text=T("Copy selected", "Selected copy"), command=self._copy_api_key).pack(side="left", padx=4)
        ttk.Button(arow, text=T("Delete selected", "Selected delete"), command=self._del_api_key).pack(side="left")
        self.api_keys_list = tk.Listbox(ap, height=3)
        self.api_keys_list.pack(fill="x", padx=10, pady=4)
        self._refresh_api_keys()
        try:
            port = int(s.get("api_port", 8700))
        except Exception:
            port = 8700
        ttk.Label(ap, text=T(f"Local API: POST http://127.0.0.1:{port}/solve  header X-API-Key, body = captcha image.\n"
                             "Returns JSON: {{\"text\": \"solved\"}}. Server starts when at least one key exists (restart after first key).",
                             f"Local API: POST http://127.0.0.1:{port}/solve  header X-API-Key, body = captcha image.\n"
                             "JSON return: {{\"text\": \"solved\"}}. Pehli key banane ke baad restart par server chalu."),
                  foreground="#8ea0bd").pack(anchor="w", padx=10, pady=(0, 6))
        self.set_compat80 = tk.IntVar(value=1 if s.get("captcha_compat80") else 0)
        ttk.Checkbutton(ap, text=T("2captcha-compatible mode on port 80 (for Bomb Prime etc.) — needs admin + hosts file",
                                   "2captcha-compatible mode port 80 par (Bomb Prime waghera ke liye) — admin + hosts file chahiye"),
                        variable=self.set_compat80).pack(anchor="w", padx=10, pady=(0, 2))
        ttk.Label(ap, text=T("How: 1) tick above, Save, run this app as Administrator.  2) In C:\\Windows\\System32\\drivers\\etc\\hosts\n"
                             "add a line:  127.0.0.1  2captcha.com   3) In Bomb Prime use your generated key as the 2captcha key.",
                             "Tareeqa: 1) upar tick, Save, app ko Administrator se chalayein.  2) C:\\Windows\\System32\\drivers\\etc\\hosts\n"
                             "me line add karein:  127.0.0.1  2captcha.com   3) Bomb Prime me 2captcha key ki jagah apni generated key daalein."),
                  foreground="#8ea0bd").pack(anchor="w", padx=10, pady=(0, 6))

        # Remote access from anywhere: Telegram + Tunnel
        rem = ttk.LabelFrame(f, text=T("Remote access (control from anywhere)",
                                       "Remote access (kahin se control)"))
        rem.pack(fill="x", padx=12, pady=10)
        t1 = ttk.Frame(rem); t1.pack(fill="x", padx=10, pady=4)
        ttk.Label(t1, text=T("Telegram Bot Token:", "Telegram Bot Token:")).pack(side="left")
        self.set_tg_token = tk.StringVar(value=s.get("tg_token", ""))
        ttk.Entry(t1, textvariable=self.set_tg_token, width=34).pack(side="left", padx=6, fill="x", expand=True)
        t2 = ttk.Frame(rem); t2.pack(fill="x", padx=10, pady=4)
        ttk.Label(t2, text=T("Telegram Chat ID (auto-set on /start):", "Telegram Chat ID (/start par khud set):")).pack(side="left")
        self.set_tg_chat = tk.StringVar(value=s.get("tg_chat", ""))
        ttk.Entry(t2, textvariable=self.set_tg_chat, width=18).pack(side="left", padx=6)
        ttk.Label(rem, text=T("Make a bot with @BotFather on Telegram, paste its token here, Save, restart the app,\n"
                              "then send /start to your bot. Commands: /status /log /accounts /post all reel 5 /active all 5 /stop",
                              "Telegram par @BotFather se bot banayein, uska token yahan paste karein, Save, app restart,\n"
                              "phir bot ko /start bhejein. Commands: /status /log /accounts /post all reel 5 /active all 5 /stop"),
                  foreground="#8ea0bd").pack(anchor="w", padx=10, pady=(0, 4))
        self.set_tunnel_on = tk.IntVar(value=1 if s.get("tunnel_on") else 0)
        ttk.Checkbutton(rem, text=T("Public tunnel (needs cloudflared.exe next to the app) — gives a public link for the phone app",
                                    "Public tunnel (cloudflared.exe app ke paas ho) — phone app ke liye public link deta hai"),
                        variable=self.set_tunnel_on).pack(anchor="w", padx=10, pady=4)

        # LDPlayer
        ld = ttk.LabelFrame(f, text=T("LDPlayer (Android emulator)", "LDPlayer (Android emulator)"))
        ld.pack(fill="x", padx=12, pady=10)
        lr = ttk.Frame(ld); lr.pack(fill="x", padx=10, pady=6)
        ttk.Label(lr, text=T("ldconsole.exe path:", "ldconsole.exe path:")).pack(side="left")
        self.set_ld_path = tk.StringVar(value=s.get("ld_console_path", ""))
        ttk.Entry(lr, textvariable=self.set_ld_path, width=36).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(lr, text="Browse", command=self._pick_ld_path).pack(side="left")
        ttk.Label(ld, text=T("Usually auto-found. If not, pick ldconsole.exe (inside your LDPlayer folder). "
                             "Then use the LDPlayer tab to detect and control instances.",
                             "Aksar khud mil jata hai. Na mile to ldconsole.exe chunein (LDPlayer folder me). "
                             "Phir LDPlayer tab se instances detect/control karein."),
                  foreground="#8ea0bd").pack(anchor="w", padx=10, pady=(0, 6))

        # Daily auto-upload
        da = ttk.LabelFrame(f, text=T("Daily auto-upload (runs automatically at this time)", "Daily auto-upload (roz isi time khud chale)"))
        da.pack(fill="x", padx=12, pady=10)
        self.set_daily_auto = tk.IntVar(value=1 if s.get("daily_auto") else 0)
        ttk.Checkbutton(da, text=T("Auto-upload on all IDs every day at this time", "Har roz isi waqt sab IDs par auto-upload karein"),
                        variable=self.set_daily_auto).pack(anchor="w", padx=10, pady=3)
        tr = ttk.Frame(da); tr.pack(fill="x", padx=10, pady=3)
        ttk.Label(tr, text=T("Time:", "Time:")).pack(side="left")
        dt = s.get("daily_time", "20:00")
        try:
            dh, dm = [int(x) for x in dt.split(":")]
        except Exception:
            dh, dm = 20, 0
        ap = "AM" if dh < 12 else "PM"
        h12 = dh % 12 or 12
        self.set_daily_time = TimePicker(tr, h12, dm, ap)
        self.set_daily_time.pack(side="left", padx=6)
        ttk.Label(tr, text=T("Kind:", "Kind:")).pack(side="left", padx=(10, 2))
        self.set_daily_kind = tk.StringVar(value=s.get("daily_kind", "reel"))
        ttk.Combobox(tr, textvariable=self.set_daily_kind, values=["reel", "video", "photo"],
                     width=7, state="readonly").pack(side="left")
        # Daily active (warm-up)
        ar = ttk.Frame(da); ar.pack(fill="x", padx=10, pady=3)
        self.set_daily_active = tk.IntVar(value=1 if s.get("daily_active") else 0)
        ttk.Checkbutton(ar, text=T("Daily ACTIVE (warm-up) auto — time:", "Daily ACTIVE (warm-up) auto — time:"), variable=self.set_daily_active).pack(side="left")
        at = s.get("daily_active_time", "10:00")
        try:
            ah, am = [int(x) for x in at.split(":")]
        except Exception:
            ah, am = 10, 0
        aap = "AM" if ah < 12 else "PM"
        self.set_daily_active_time = TimePicker(ar, ah % 12 or 12, am, aap)
        self.set_daily_active_time.pack(side="left", padx=6)

        self.set_autostart = tk.IntVar(value=1 if s.get("autostart") else 0)
        ttk.Checkbutton(da, text=T("Auto-start with Windows (opens in tray on boot)", "Windows ke saath software auto-start (boot par tray me khule)"),
                        variable=self.set_autostart).pack(anchor="w", padx=10, pady=3)
        ttk.Label(da, text=T("Note: Auto-upload works while the software is open. With auto-start on, "
                             "it opens on PC startup, then starts at the set time.",
                             "Note: Auto-upload tab chalega jab software khula ho. Auto-start on karne se "
                             "PC on hote hi khud khul jayega, phir time par kaam shuru."),
                  foreground="#8ea0bd").pack(anchor="w", padx=10)

        ttk.Button(box, text=T("Save settings", "Settings save"), command=self._save_settings).pack(anchor="e", padx=12, pady=8)
        ttk.Label(f, text=T("Hashtags and daily limit are now set per ID/page on the Accounts page.", "Hashtags, daily limit ab har ID/page ke saath Accounts page par set hote hain."),
                  foreground="#8ea0bd").pack(anchor="w", padx=14)

    def _ai_apply_preset(self):
        name = self.set_ai_provider.get()
        if name in self._ai_presets:
            base, model = self._ai_presets[name]
            self.set_ai_base.set(base)
            self.set_ai_model.set(model)

    def _refresh_api_keys(self):
        try:
            self.api_keys_list.delete(0, tk.END)
            for k in get_api_keys():
                self.api_keys_list.insert(tk.END, k)
        except Exception:
            pass

    def _gen_api_key(self):
        k = add_api_key()
        self._refresh_api_keys()
        try:
            self.clipboard_clear(); self.clipboard_append(k)
        except Exception:
            pass
        messagebox.showinfo(T("API key", "API key"),
                            T(f"New key (copied):\n{k}\n\nRestart the app once so the local API server starts.",
                              f"Nayi key (copy ho gayi):\n{k}\n\nAik dafa app restart karein taake API server chalu ho."))

    def _copy_api_key(self):
        sel = self.api_keys_list.curselection()
        if not sel:
            return
        k = self.api_keys_list.get(sel[0])
        try:
            self.clipboard_clear(); self.clipboard_append(k)
            messagebox.showinfo(T("Copied", "Copy"), T("Key copied to clipboard.", "Key clipboard me copy ho gayi."))
        except Exception:
            pass

    def _del_api_key(self):
        sel = self.api_keys_list.curselection()
        if not sel:
            return
        remove_api_key(self.api_keys_list.get(sel[0]))
        self._refresh_api_keys()

    def _pick_browser_path(self):
        p = filedialog.askopenfilename(title=T("Browser exe", "Browser exe"), filetypes=[("Programs", "*.exe"), ("All", "*.*")])
        if p:
            self.set_path.set(p)

    def _pick_ld_path(self):
        p = filedialog.askopenfilename(title="ldconsole.exe", filetypes=[("ldconsole", "ldconsole.exe"), ("Programs", "*.exe"), ("All", "*.*")])
        if p:
            self.set_ld_path.set(p)

    def _check_update_click(self):
        self.update_lbl.config(text=T("Checking...", "Check ho raha..."))
        threading.Thread(target=self._run_update_check, daemon=True).start()

    def _run_update_check(self):
        has, latest, err = check_update()
        if err:
            self.after(0, lambda: self.update_lbl.config(
                text=T(f"Check failed: {err[:40]}", f"Check fail: {err[:40]}")))
            return
        if not has:
            self.after(0, lambda: self.update_lbl.config(
                text=T("You have the latest version.", "Aap ke paas latest version hai.")))
            return
        def ask():
            if messagebox.askyesno(T("Update", "Update"),
                                   T(f"New version {latest} available. Update now?",
                                     f"Nayi version {latest} mojood hai. Abhi update karein?")):
                self.update_lbl.config(text=T("Updating...", "Update ho raha..."))
                threading.Thread(target=self._do_update_thread, daemon=True).start()
        self.after(0, ask)

    def _do_update_thread(self):
        res = do_update(log=lambda m: self.log_q.put(m))
        def done():
            if res == "restart_exe":
                messagebox.showinfo(T("Update", "Update"),
                                    T("Updated! The app will restart.", "Update ho gaya! App dobara khulega."))
                try:
                    self.destroy()
                except Exception:
                    pass
                os._exit(0)   # tray/server threads samet poori tarah band (taake exe swap ho sake)
            elif res == "restart_py":
                messagebox.showinfo(T("Update", "Update"),
                                    T("Updated! Please close and reopen the app.",
                                      "Update ho gaya! App band kar ke dobara kholein."))
            else:
                self.update_lbl.config(text=T("Update failed (see log).", "Update fail (log dekhein)."))
        self.after(0, done)

    def _save_settings(self):
        try:
            ix_max = int(self.set_ix_max.get().strip() or "10")
        except ValueError:
            ix_max = 10
        br = self.set_browser.get()
        autostart = bool(self.set_autostart.get())
        def _int(v, d):
            try:
                return int(str(v).strip())
            except Exception:
                return d
        save_settings({"fbb_engine": self.set_fbb_engine.get(), "fbb_per_row": _int(self.set_fbb_row.get(), 4),
                       "fbb_w": _int(self.set_fbb_w.get(), 900), "fbb_h": _int(self.set_fbb_h.get(), 800),
                       "fbb_mute": bool(self.set_fbb_mute.get()), "fbb_auto_tile": bool(self.set_fbb_tile.get()), "fbb_lang": self.set_fbb_lang.get().strip()})
        save_settings({"browser": br, "browser_path": self.set_path.get().strip(),
                       "use_ixbrowser": br == "ixbrowser", "ix_max_per_day": ix_max,
                       "ix_limit_on": bool(self.set_ix_limit_on.get()),
                       "daily_auto": bool(self.set_daily_auto.get()),
                       "daily_time": self.set_daily_time.hhmm24(),
                       "daily_kind": self.set_daily_kind.get(),
                       "daily_threads": 5,
                       "daily_active": bool(self.set_daily_active.get()),
                       "daily_active_time": self.set_daily_active_time.hhmm24(),
                       "language": ("en" if self.set_lang_disp.get() == "English" else "ur"),
                       "captcha_key": self.set_captcha_key.get().strip(),
                       "local_ocr": bool(self.set_local_ocr.get()),
                       "api_keys": get_api_keys(),
                       "api_port": load_settings().get("api_port", 8700),
                       "tg_token": self.set_tg_token.get().strip(),
                       "tg_chat": self.set_tg_chat.get().strip(),
                       "tunnel_on": bool(self.set_tunnel_on.get()),
                       "captcha_compat80": bool(self.set_compat80.get()),
                       "ld_console_path": self.set_ld_path.get().strip(),
                       "ai_key": self.set_ai_key.get().strip(),
                       "ai_model": self.set_ai_model.get().strip() or "llama-3.1-8b-instant",
                       "ai_base": self.set_ai_base.get().strip() or "https://api.groq.com/openai/v1",
                       "ldw_folder": load_settings().get("ldw_folder", ""),
                       "ldw_page": load_settings().get("ldw_page", ""),
                       "ldw_action": load_settings().get("ldw_action", "reel"),
                       "autostart": autostart})
        apply_language()
        self._apply_autostart(autostart)
        messagebox.showinfo(T("Done", "Done"), T(f"Settings saved (browser: {br}).", f"Settings save (browser: {br})."))

    # ---------- helpers ----------
    def _spin_row(self, parent, label, default, lo, hi):
        row = ttk.Frame(parent); row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=30).pack(side="left")
        var = tk.StringVar(value=str(default))
        tk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=6).pack(side="left")
        return var

    @staticmethod
    def _iv(x):
        try:
            return int(float(str(x)))
        except Exception:
            return 0

    def _stop_all(self):
        self.stop_flag.set()
        self.log_q.put(T("STOP pressed — running work will stop after the current post.", "STOP dabaya — chalta kaam abhi wali post ke baad ruk jayega."))

    # ---------- System tray (wifi/clock ke paas) ----------
    def _setup_tray(self):
        self._tray = None
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            # pystray/Pillow nahi — window close par bas chhupa dein
            try:
                self.protocol("WM_DELETE_WINDOW", self.withdraw)
            except Exception:
                pass
            return
        try:
            img = Image.new("RGBA", (64, 64), (24, 119, 242, 255))
            d = ImageDraw.Draw(img)
            d.rectangle([28, 16, 34, 50], fill="white")
            d.rectangle([22, 28, 42, 34], fill="white")
            menu = pystray.Menu(
                pystray.MenuItem("Open", self._tray_show),
                pystray.MenuItem("Quit", self._tray_quit),
            )
            self._tray = pystray.Icon("fbuploader", img, "DK Facebook Uploader", menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
            self.protocol("WM_DELETE_WINDOW", self._tray_hide)
            if load_settings().get("autostart"):
                self.after(300, self.withdraw)  # boot par tray me chhupa shuru
        except Exception:
            pass

    def _tray_hide(self):
        try:
            self.withdraw()
            self.log_q.put(T("Minimized to tray (icon near wifi/clock). Click the icon to open.", "Tray me chhupa diya (wifi/clock ke paas icon). Open karne ke liye icon par click."))
        except Exception:
            pass

    def _tray_show(self, *a):
        try:
            self.after(0, self.deiconify)
        except Exception:
            pass

    def _tray_quit(self, *a):
        try:
            if self._tray:
                self._tray.stop()
        except Exception:
            pass
        try:
            self.after(0, self.destroy)
        except Exception:
            pass

    # ---------- Autostart (Windows startup) ----------
    def _apply_autostart(self, enable):
        try:
            if not sys.platform.startswith("win"):
                return
            startup = os.path.join(os.environ.get("APPDATA", ""),
                                   "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
            if not os.path.isdir(startup):
                return
            launcher = os.path.join(startup, "FBUploader.bat")
            if enable:
                if getattr(sys, "frozen", False):
                    cmd = f'start "" "{sys.executable}"'
                else:
                    cmd = f'start "" pythonw "{os.path.abspath(__file__)}"'
                with open(launcher, "w", encoding="utf-8") as fh:
                    fh.write("@echo off\r\n" + cmd + "\r\n")
                self.log_q.put(T("Auto-start on: software will open on PC boot.", "Auto-start on: PC boot par software khud khulega."))
            else:
                if os.path.exists(launcher):
                    os.remove(launcher)
                self.log_q.put(T("Auto-start off.", "Auto-start off."))
        except Exception as e:
            self.log_q.put(T(f"Could not set auto-start: {e}", f"Auto-start set nahi hua: {e}"))

    # ---------- Daily auto-upload watcher ----------
    def _start_daily_watcher(self):
        self._last_daily_run = ""
        self._last_active_run = ""
        threading.Thread(target=self._daily_loop, daemon=True).start()

    def _daily_loop(self):
        import datetime
        while True:
            try:
                s = load_settings()
                now = datetime.datetime.now().strftime("%H:%M")
                today = datetime.date.today().isoformat()
                if s.get("daily_auto") and now == s.get("daily_time", "20:00") and self._last_daily_run != today:
                    self._last_daily_run = today
                    self._run_daily_now(s.get("daily_kind", "reel"), int(s.get("daily_threads", 5) or 5))
                if s.get("daily_active") and now == s.get("daily_active_time", "10:00") and self._last_active_run != today:
                    self._last_active_run = today
                    self._run_daily_active(int(s.get("daily_threads", 5) or 5))
            except Exception:
                pass
            time.sleep(20)

    def _run_daily_active(self, threads):
        log = lambda m: self.log_q.put(m)
        accounts = load_accounts()
        ready = self._apply_ix_daily_limit(accounts)
        if not ready:
            return
        s = load_active_settings() or self._collect_active_settings()
        self.stop_flag.clear()
        log(T(f"=== DAILY ACTIVE START: {len(ready)} IDs ===", f"=== DAILY ACTIVE START: {len(ready)} IDs ==="))
        mgr = threading.Thread(target=self._run_active_pool, args=(ready, s, threads), daemon=True)
        mgr.start(); self.workers.append(mgr)

    def _run_daily_now(self, kind, threads):
        log = lambda m: self.log_q.put(m)
        accounts = load_accounts()
        jobs = []
        for a in accounts:
            files = list_media_in_folder(a.get("folder", ""), kind)
            if files:
                jobs.append((a, files))
        if not jobs:
            log(T("Daily auto: no ID has a folder set / all empty.", "Daily auto: kisi ID ka folder set/khali nahi."))
            return
        ready = self._apply_ix_daily_limit([a for a, _ in jobs])
        rid = {a["id"] for a in ready}
        jobs = [(a, fl) for a, fl in jobs if a["id"] in rid]
        if not jobs:
            return
        cfg = {"interval": (40, 90), "upload_wait": None, "rotation": 1, "rot_gap": 0,
               "allow_dup": False, "scroll_between": False, "delete_after": False}
        self.stop_flag.clear()
        log(T(f"=== DAILY AUTO START: {len(jobs)} IDs, {kind} ===", f"=== DAILY AUTO START: {len(jobs)} IDs, {kind} ==="))
        mgr = threading.Thread(target=self._run_pool, args=(jobs, kind, None, threads, cfg), daemon=True)
        mgr.start(); self.workers.append(mgr)

    # ---------- 2FA ----------
    def _resume_event_for(self, acc_id):
        ev = self.resume_events.get(acc_id)
        if ev is None:
            ev = threading.Event(); self.resume_events[acc_id] = ev
        return ev

    def _wait_2fa(self, account):
        ev = self._resume_event_for(account["id"]); ev.clear()
        self.log_q.put(T(f"[{account['name']}] Enter 2FA code in the browser (60s auto-resume)...", f"[{account['name']}] 2FA code browser mein daalein (60s auto-resume)..."))
        ev.wait(timeout=60); ev.clear()

    # ---------- daily limit ----------
    def _apply_ix_daily_limit(self, accounts):
        s = load_settings()
        if not (s.get("use_ixbrowser") or s.get("browser") == "ixbrowser") or not s.get("ix_limit_on"):
            return accounts
        maxpd = int(s.get("ix_max_per_day", 10))
        remaining = ix_remaining_today(maxpd)
        ordered = ix_order_accounts(accounts)
        if remaining <= 0:
            self.log_q.put(T(f"ixBrowser: today's limit ({maxpd}) reached.", f"ixBrowser: aaj ki limit ({maxpd}) poori."))
            return []
        if len(ordered) > remaining:
            self.log_q.put(T(f"ixBrowser: {remaining} IDs will run today.", f"ixBrowser: aaj {remaining} IDs chalengi."))
        return ordered[:remaining]

    def _wait_until(self, hhmm, account, log):
        import datetime
        try:
            hh, mm = [int(x) for x in str(hhmm).split(":")]
        except Exception:
            return
        now = datetime.datetime.now()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        log(T(f"[{account['name']}] Waiting for {hhmm}...", f"[{account['name']}] {hhmm} ka intezaar..."))
        while datetime.datetime.now() < target:
            if self.stop_flag.is_set():
                return
            time.sleep(min(20, (target - datetime.datetime.now()).total_seconds()))

    # ---------- posting ----------
    def _run_pool(self, jobs, kind, start_at, limit, cfg):
        log = lambda m: self.log_q.put(m)
        set_tile_count(min(limit, len(jobs)))
        active, pending = [], list(jobs)
        while (pending or active) and not self.stop_flag.is_set():
            active = [t for t in active if t.is_alive()]
            while pending and len(active) < limit and not self.stop_flag.is_set():
                acc, files = pending.pop(0)
                t = threading.Thread(target=self._run_post, args=(acc, files, kind, start_at, cfg), daemon=True)
                t.start(); active.append(t); self.workers.append(t)
                log(T(f"Opened: {acc['name']} (running {len(active)}, queued {len(pending)})", f"Khuli: {acc['name']} (chal rahi {len(active)}, qatar {len(pending)})"))
            time.sleep(2)
        log(T("=== POST pool finished ===", "=== POST pool khatam ==="))

    def _run_post(self, account, files, kind, start_at, cfg):
        log = lambda m: self.log_q.put(m)
        if self.stop_flag.is_set():
            return
        if start_at:
            self._wait_until(start_at, account, log)
        # LDPlayer engine (agar is account ka ld_index set hai)
        ld = (account.get("ld_index") or "").strip()
        if ld.isdigit():
            self._run_post_ld(account, files, kind, cfg, int(ld), log)
            return
        if not ensure_runtime(log=log):
            return
        bot = Automation(log=log, headless=False, browser_cfg=load_settings())
        if cfg.get("upload_wait"):
            bot.upload_wait = cfg["upload_wait"]
        panel = getattr(self, "post_panel", None)
        page = account.get("page_name", "")
        try:
            bot.start(account)
            if not bot.login(wait_for_2fa_callback=self._wait_2fa):
                if panel: panel.set_status(account["id"], bot.account_state())
                add_id_history(account["id"], "login failed", bot.last_error or T("login did not happen", "login nahi hui"))
                return
            if panel: panel.set_status(account["id"], "Live")
            add_id_history(account["id"], "login ok", "")
            targets = posting_targets(account)
            if len(targets) > 1 or targets[0].get("url"):
                log(T(f"[{account['name']}] Posting to {len(targets)} ticked page(s).",
                      f"[{account['name']}] {len(targets)} tick kiye pages par post hogi."))
            for tgt in targets:
                if self.stop_flag.is_set():
                    break
                bot.target_page = tgt if (tgt.get("url") or tgt.get("name")) else None
                page = tgt.get("name") or tgt.get("url") or account.get("page_name", "")
                tags = get_hashtags_for_page(page) or get_hashtags_for_page(account.get("page_name", ""))
                if tgt.get("name"):
                    log(T(f"[{account['name']}] >>> Page: {tgt['name']}", f"[{account['name']}] >>> Page: {tgt['name']}"))
                allow_dup = cfg.get("allow_dup")
                remaining_daily = page_daily_remaining(page)
                seen, todo = set(), []
                pf = page_files(page, kind)
                if pf:
                    log(T(f"[{account['name']}] Using page folder: {page_media_folder(page)}",
                          f"[{account['name']}] Page ka folder: {page_media_folder(page)}"))
                for path in (pf or files):
                    if not os.path.exists(path):
                        continue
                    h = file_hash(path)
                    if not allow_dup and (is_uploaded(page, h) or h in seen):
                        continue
                    seen.add(h); todo.append((path, h))
                    if len(todo) >= remaining_daily:
                        log(T(f"[{account['name']}] page daily limit reached.", f"[{account['name']}] page daily limit poori."))
                        break
                if not todo:
                    log(T(f"[{account['name']}] no new file / daily limit.", f"[{account['name']}] koi nayi file nahi / daily limit."))
                    continue

                rotation = max(1, cfg.get("rotation", 1))
                imn, imx = cfg.get("interval", (40, 90))
                for lp in range(1, rotation + 1):
                    if self.stop_flag.is_set():
                        break
                    n_ok_pg = 0
                    for i, (path, h) in enumerate(todo, 1):
                        if self.stop_flag.is_set():
                            break
                        log(T(f"[{account['name']}] {i}/{len(todo)}: {os.path.basename(path)}", f"[{account['name']}] {i}/{len(todo)}: {os.path.basename(path)}"))
                        try:
                            sc = sidecar_caption(path)
                            # video ka apna caption (TikTok) — jaisa hai waisa (hashtags na hon to nahi); warna page hashtags
                            cap = clean_caption(sc if sc is not None else tags, 4)
                            if kind == "reel":
                                ok = bot.upload_reel(path, cap)
                            elif kind == "photo":
                                ok = bot.upload_photo(path, cap)
                            else:
                                ok = bot.upload_video(path, cap)
                        except Exception as e:
                            ok = False; bot.last_error = str(e)
                        if ok:
                            if not allow_dup:
                                mark_uploaded(page, h)
                            inc_page_posted(page)
                            add_page_history(page, path, "success", "")
                            n_ok_pg += 1
                            post_log_add(page, True, f"{n_ok_pg}|{len(todo)}", max(0, len(todo) - i), "", account["id"], "Browser")
                            if cfg.get("delete_after") and tgt is targets[-1]:
                                try:
                                    os.remove(path); log(T(f"[{account['name']}] deleted from device: {os.path.basename(path)}", f"[{account['name']}] device se delete: {os.path.basename(path)}"))
                                    side = os.path.splitext(path)[0] + ".txt"
                                    if os.path.exists(side):
                                        os.remove(side)
                                    captions_remove(os.path.dirname(path), os.path.basename(path))
                                except Exception as e:
                                    log(T(f"delete failed: {e}", f"delete fail: {e}"))
                        else:
                            add_page_history(page, path, "fail", bot.last_error or T("post did not happen", "post nahi hui"))
                            post_log_add(page, False, f"{n_ok_pg}|{len(todo)}",
                                         max(0, len(todo) - i), bot.last_error or "", account["id"], "Browser")
                            # fail / confirm nahi -> delete NAHI, page folder ke andar 'Failed' folder mein
                            if tgt is targets[-1] and os.path.exists(path):
                                try:
                                    fdir = os.path.join(os.path.dirname(path), "Failed")
                                    os.makedirs(fdir, exist_ok=True)
                                    shutil.move(path, os.path.join(fdir, os.path.basename(path)))
                                    side = os.path.splitext(path)[0] + ".txt"
                                    if os.path.exists(side):
                                        shutil.move(side, os.path.join(fdir, os.path.basename(side)))
                                    c_ = captions_remove(os.path.dirname(path), os.path.basename(path))
                                    if c_ is not None:
                                        captions_add(fdir, os.path.basename(path), c_)
                                    log(T(f"[{account['name']}] moved to 'Failed' folder (kept, not deleted): {os.path.basename(path)}",
                                          f"[{account['name']}] 'Failed' folder mein rakh di (delete nahi): {os.path.basename(path)}"))
                                except Exception as e:
                                    log(f"move to Failed: {e}")
                        if i < len(todo):
                            gap = random.randint(min(imn, imx), max(imn, imx)) if (imn or imx) else random.randint(40, 90)
                            if cfg.get("scroll_between"):
                                bot.warm_scroll(gap)
                            elif gap > 120:
                                bot.warm_scroll(gap)
                            else:
                                time.sleep(gap)
                    if lp < rotation:
                        time.sleep(max(0, cfg.get("rot_gap", 0)))
            log(T(f"[{account['name']}] Done.", f"[{account['name']}] Ho gaya."))
        except Exception as e:
            log(T(f"[{account['name']}] Error: {e}", f"[{account['name']}] Error: {e}"))
            add_id_history(account["id"], "error", str(e))
            if panel: panel.set_status(account["id"], "Error")
        finally:
            time.sleep(3); bot.stop()

    # ---------- active ----------
    def _run_active_pool(self, accounts, settings, limit, start_at=None):
        log = lambda m: self.log_q.put(m)
        set_tile_count(min(limit, len(accounts)))
        active, pending = [], list(accounts)
        while (pending or active) and not self.stop_flag.is_set():
            active = [t for t in active if t.is_alive()]
            while pending and len(active) < limit and not self.stop_flag.is_set():
                acc = pending.pop(0)
                t = threading.Thread(target=self._run_active, args=(acc, settings, start_at), daemon=True)
                t.start(); active.append(t); self.workers.append(t)
                log(T(f"Active: {acc['name']} (running {len(active)}, queued {len(pending)})", f"Active: {acc['name']} (chal rahi {len(active)}, qatar {len(pending)})"))
                # har browser alag waqt par khule (sab ek sath nahi)
                self.stop_flag.wait(random.uniform(6, 20))
            time.sleep(2)
        log(T("=== ACTIVE pool finished ===", "=== ACTIVE pool khatam ==="))

    def _run_post_ld(self, account, files, kind, cfg, index, log):
        panel = getattr(self, "post_panel", None)
        page = account.get("page_name", "")
        try:
            if panel: panel.set_status(account["id"], "LD")
            LD.launch(index)
            time.sleep(3)
            tags = get_hashtags_for_page(page)
            allow_dup = cfg.get("allow_dup")
            seen, todo = set(), []
            rem = page_daily_remaining(page)
            for path in files:
                if not os.path.exists(path):
                    continue
                h = file_hash(path)
                if not allow_dup and (is_uploaded(page, h) or h in seen):
                    continue
                seen.add(h); todo.append((path, h))
                if len(todo) >= rem:
                    break
            if not todo:
                log(T(f"[{account['name']}] no new file / daily limit.", f"[{account['name']}] koi nayi file nahi / daily limit."))
                return
            imn, imx = cfg.get("interval", (40, 90))
            for i, (path, h) in enumerate(todo, 1):
                if self.stop_flag.is_set():
                    break
                log(f"[{account['name']}] LD {i}/{len(todo)}: {os.path.basename(path)}")
                ok = LD.fb_post(index, path, kind, tags, log=log, page=account.get("page_name",""))
                if ok:
                    if not allow_dup:
                        mark_uploaded(page, h)
                    inc_page_posted(page)
                    add_page_history(page, path, "success", "")
                    if cfg.get("delete_after"):
                        try: os.remove(path)
                        except Exception: pass
                else:
                    add_page_history(page, path, "fail", "LD post not confirmed")
                if i < len(todo):
                    time.sleep(random.randint(min(imn, imx), max(imn, imx)))
            log(f"[{account['name']}] LD done.")
        except Exception as e:
            log(f"[{account['name']}] LD error: {e}")
            if panel: panel.set_status(account["id"], "Error")

    def _run_active(self, account, settings, start_at=None):
        log = lambda m: self.log_q.put(m)
        if self.stop_flag.is_set():
            return
        if start_at:
            self._wait_until(start_at, account, log)
        ld = (account.get("ld_index") or "").strip()
        if ld.isdigit():
            panel = getattr(self, "interact_panel", None)
            try:
                if panel: panel.set_status(account["id"], "LD")
                LD.launch(int(ld)); time.sleep(3)
                LD.fb_active(int(ld), settings, log=log)
            except Exception as e:
                log(f"[{account['name']}] LD active error: {e}")
            return
        if not ensure_runtime(log=log):
            return
        bot = Automation(log=log, headless=False, browser_cfg=load_settings())
        panel = getattr(self, "interact_panel", None)
        try:
            bot.start(account)
            if not bot.login(wait_for_2fa_callback=self._wait_2fa):
                if panel: panel.set_status(account["id"], bot.account_state())
                add_id_history(account["id"], "login failed", bot.last_error or T("login did not happen", "login nahi hui"))
                return
            if panel: panel.set_status(account["id"], "Live")
            bot.run_active(settings, stop_check=self.stop_flag.is_set)
        except Exception as e:
            log(T(f"[{account['name']}] Active error: {e}", f"[{account['name']}] Active error: {e}"))
            add_id_history(account["id"], "active error", str(e))
        finally:
            time.sleep(3); bot.stop()

    # ---------- IP check ----------
    def _run_ip_check(self, account):
        log = lambda m: self.log_q.put(m)
        if not ensure_runtime(log=log):
            return
        bot = Automation(log=log, headless=True, browser_cfg=load_settings())
        try:
            bot.start(account)
            bot.page.goto("https://api.ipify.org", wait_until="domcontentloaded", timeout=30000)
            log(T(f"[{account['name']}] IP: {bot.page.inner_text('body').strip()}", f"[{account['name']}] IP: {bot.page.inner_text('body').strip()}"))
        except Exception as e:
            log(T(f"[{account['name']}] IP check failed: {e}", f"[{account['name']}] IP check fail: {e}"))
        finally:
            bot.stop()

    # ---------- Log ----------
    def _build_logbar(self):
        lh = tk.Frame(self.right, bg=PAL["bg"]); lh.pack(fill="x", padx=16)
        self._log_open = load_settings().get("log_open", True)
        self._log_btn = tk.Label(lh, text="", bg=PAL["bg"], fg=PAL["accent"], font=("Segoe UI", 10, "bold"), cursor="hand2")
        self._log_btn.pack(side="left")
        lf = ttk.Frame(self.right); self._log_frame = lf
        def _toggle(e=None, init=False):
            if not init:
                self._log_open = not self._log_open
                save_settings({"log_open": self._log_open})
            if self._log_open:
                lf.pack(fill="x", padx=16, pady=(0, 6), after=lh)
                self._log_btn.config(text="▾ Live log  (click to hide)")
            else:
                lf.pack_forget()
                self._log_btn.config(text="▸ Live log  (click to show)")
        self._log_btn.bind("<Button-1>", _toggle)
        self.after(50, lambda: _toggle(init=True))
        self.log_text = tk.Text(lf, height=5, state="disabled", bg="#060b14", fg="#5eead4", font=("Consolas", 10, "bold"),
                                relief="flat", highlightthickness=0)
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _drain_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert(tk.END, str(msg) + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state="disabled")
                sm = str(msg)
                self.log_buffer.append(sm)
                if len(self.log_buffer) > 300:
                    self.log_buffer = self.log_buffer[-300:]
                # ahem events Telegram par bhejo
                if sm.startswith("===") or "finished" in sm or "khatam" in sm or "Error" in sm:
                    try:
                        self.tg_notify(sm)
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self.after(200, self._drain_log)

    # ---------- Remote API methods (mobile app ke liye) ----------
    def api_list_accounts(self):
        out = []
        for a in load_accounts():
            out.append({"id": a["id"], "name": a.get("name", ""), "email": a.get("email", ""),
                        "page": a.get("page_name", ""), "status": self.api_status_map.get(a["id"], "")})
        return out

    def api_log(self):
        return self.log_buffer[-120:]

    def api_status(self):
        running = any(w.is_alive() for w in self.workers)
        return {"running": running, "statuses": self.api_status_map}

    def _accounts_by_ids(self, ids):
        by = {a["id"]: a for a in load_accounts()}
        return [by[i] for i in ids if i in by]

    def api_start_post(self, ids, kind, threads):
        accs = self._accounts_by_ids(ids)
        jobs = []
        for a in accs:
            files = list_media_in_folder(a.get("folder", ""), kind)
            if files:
                jobs.append((a, files))
        if not jobs:
            return {"ok": False, "msg": "no files/folder for selected IDs"}
        jobs = [(a, fl) for a, fl in jobs if a["id"] in {x["id"] for x in self._apply_ix_daily_limit([j[0] for j in jobs])}]
        if not jobs:
            return {"ok": False, "msg": "daily limit"}
        cfg = {"interval": (40, 90), "upload_wait": None, "rotation": 1, "rot_gap": 0,
               "allow_dup": False, "scroll_between": False, "delete_after": False}
        self.stop_flag.clear()
        mgr = threading.Thread(target=self._run_pool, args=(jobs, kind, None, max(1, threads), cfg), daemon=True)
        mgr.start(); self.workers.append(mgr)
        return {"ok": True, "count": len(jobs)}

    def api_start_active(self, ids, threads):
        accs = self._apply_ix_daily_limit(self._accounts_by_ids(ids))
        if not accs:
            return {"ok": False, "msg": "no IDs"}
        s = load_active_settings() or {}
        self.stop_flag.clear()
        mgr = threading.Thread(target=self._run_active_pool, args=(accs, s, max(1, threads)), daemon=True)
        mgr.start(); self.workers.append(mgr)
        return {"ok": True, "count": len(accs)}

    def api_stop(self):
        self.stop_flag.set()
        return {"ok": True}

    # ---------- Telegram bot (kahin se control + notifications) ----------
    def _tg(self, method, params):
        import urllib.request, urllib.parse, json as _json
        s = load_settings()
        token = (s.get("tg_token") or "").strip()
        if not token:
            return None
        try:
            url = f"https://api.telegram.org/bot{token}/{method}"
            data = urllib.parse.urlencode(params).encode()
            r = urllib.request.urlopen(url, data=data, timeout=35)
            return _json.loads(r.read().decode())
        except Exception:
            return None

    def tg_notify(self, text):
        s = load_settings()
        chat = (s.get("tg_chat") or "").strip()
        if not chat or not (s.get("tg_token") or "").strip():
            return
        self._tg("sendMessage", {"chat_id": chat, "text": text[:3900]})

    def _start_telegram(self):
        s = load_settings()
        if not (s.get("tg_token") or "").strip():
            return
        threading.Thread(target=self._tg_loop, daemon=True).start()
        self.log_q.put(T("Telegram bot started. Send /start to your bot.",
                         "Telegram bot chal raha hai. Apne bot ko /start bhejein."))

    def _tg_loop(self):
        offset = 0
        while True:
            try:
                if not (load_settings().get("tg_token") or "").strip():
                    time.sleep(10); continue
                res = self._tg("getUpdates", {"offset": offset, "timeout": 30})
                if not res or not res.get("ok"):
                    time.sleep(3); continue
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("channel_post")
                    if not msg:
                        continue
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = (msg.get("text") or "").strip()
                    self._tg_handle(chat_id, text)
            except Exception:
                time.sleep(5)

    def _tg_handle(self, chat_id, text):
        s = load_settings()
        allowed = (s.get("tg_chat") or "").strip()
        # pehli dafa: /start -> is chat ko bind kar dein agar koi chat set nahi
        if text.startswith("/start"):
            if not allowed:
                s["tg_chat"] = chat_id; save_settings(s)
                self._tg("sendMessage", {"chat_id": chat_id,
                    "text": "Linked! Ab aap commands bhej sakte hain.\n"
                            "/status  /log  /accounts\n"
                            "/post all reel 5\n/active all 5\n/stop"})
            else:
                self._tg("sendMessage", {"chat_id": chat_id, "text": "Already linked. /status /log /post /active /stop"})
            return
        if allowed and chat_id != allowed:
            return  # sirf bound chat
        reply = self._tg_command(text)
        if reply:
            self._tg("sendMessage", {"chat_id": chat_id, "text": reply[:3900]})

    def _tg_command(self, text):
        parts = text.split()
        if not parts:
            return None
        cmd = parts[0].lower().lstrip("/")
        if cmd == "status":
            st = self.api_status()
            lines = [f"Running: {st['running']}"]
            for k, v in st["statuses"].items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines) or "No status."
        if cmd == "log":
            return "\n".join(self.api_log()[-25:]) or "No log."
        if cmd == "accounts":
            return "\n".join(f"{a['name']} ({a['page']}) {a['status']}" for a in self.api_list_accounts()) or "No accounts."
        if cmd == "stop":
            self.api_stop(); return "STOP sent."
        if cmd in ("post", "active"):
            # /post all reel 5   ya  /post name1,name2 reel 5
            who = parts[1] if len(parts) > 1 else "all"
            accs = self.api_list_accounts()
            if who == "all":
                ids = [a["id"] for a in accs]
            else:
                wanted = [w.lower() for w in who.split(",")]
                ids = [a["id"] for a in accs if a["name"].lower() in wanted or a["email"].lower() in wanted]
            if cmd == "post":
                kind = parts[2] if len(parts) > 2 else "reel"
                threads = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 5
                r = self.api_start_post(ids, kind, threads)
                return f"POST: {r}"
            else:
                threads = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
                r = self.api_start_active(ids, threads)
                return f"ACTIVE: {r}"
        return "Commands: /status /log /accounts /post all reel 5 /active all 5 /stop"

    # ---------- Tunnel (public link via cloudflared, free) ----------
    def _start_tunnel(self):
        if not load_settings().get("tunnel_on"):
            return
        threading.Thread(target=self._tunnel_run, daemon=True).start()

    def _tunnel_run(self):
        import shutil, subprocess, re
        exe = shutil.which("cloudflared")
        if not exe:
            base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
            for c in [os.path.join(base, "cloudflared.exe"), os.path.join(base, "cloudflared")]:
                if os.path.exists(c):
                    exe = c; break
        if not exe:
            self.log_q.put(T("Tunnel: cloudflared not found. Put cloudflared.exe next to the app.",
                             "Tunnel: cloudflared nahi mila. cloudflared.exe app ke paas rakhein."))
            return
        try:
            port = int(load_settings().get("api_port", 8700))
            p = subprocess.Popen([exe, "tunnel", "--url", f"http://localhost:{port}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **_NOWIN)
            for line in p.stdout:
                m = re.search(r"https://[-\w]+\.trycloudflare\.com", line)
                if m:
                    url = m.group(0)
                    self.log_q.put(T(f"Public tunnel URL: {url}  (use this in the phone app, from anywhere)",
                                     f"Public tunnel URL: {url}  (phone app me kahin se bhi ye daalein)"))
                    self.tg_notify(f"Tunnel URL: {url}")
        except Exception as e:
            self.log_q.put(f"Tunnel error: {e}")


def _single_instance_lock():
    """Aik hi copy chale. Dobara khulne par False return (phir exit)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", 50573))   # app-specific lock port
        s.listen(1)
        globals()["_INSTANCE_LOCK"] = s   # ref rakho taake close na ho
        return True
    except Exception:
        return False


def main():
    # Pehle se chal rahi ho to dubara na khule (tray me kai icon na aayein)
    if sys.platform.startswith("win") and not _single_instance_lock():
        try:
            from ctypes import windll
            # purani window saamne lao
            hwnd = windll.user32.FindWindowW(None, "DK Facebook Uploader")
            if hwnd:
                windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return
    # Blur/DPI fix (Windows) — text sharp karega
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    apply_language()
    try:
        App().mainloop()
    except Exception:
        import traceback
        err = traceback.format_exc()
        # Error ko file mein likho taake nazar aaye
        try:
            with open(os.path.join(BASE_DIR, "error_log.txt"), "w", encoding="utf-8") as f:
                f.write(err)
        except Exception:
            pass
        # Messagebox mein dikhao
        try:
            import tkinter.messagebox as _mb
            _mb.showerror("App Error", err[-1800:])
        except Exception:
            print(err)
        # Console khula rakho (double-click par turant band na ho)
        try:
            input(T("\nAn error occurred. See error_log.txt. Press Enter to close...",
                    "\nError aaya. error_log.txt dekhein. Band karne ke liye Enter dabayein..."))
        except Exception:
            pass


if __name__ == "__main__":
    main()
