"""
Phone Data Recovery - desktop app (graphical interface)

The window is an HTML page (ui/index.html) shown with pywebview (Edge WebView2 on
Windows). Every button calls a method of the Api class below; recovery work runs in
a background thread whose printed progress is captured and shown in the window.

  python gui.py              start the app
  python gui.py --selftest   check that the app's engine works (no window), used by CI
"""

import base64
import io
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime

if os.name == "nt":                         # never flash console windows (adb, powershell)
    _orig_popen_init = subprocess.Popen.__init__

    def _quiet_popen_init(self, *a, **k):
        k.setdefault("creationflags", 0x08000000)   # CREATE_NO_WINDOW
        _orig_popen_init(self, *a, **k)

    subprocess.Popen.__init__ = _quiet_popen_init

import phone_recovery as pr                 # noqa: E402  (the recovery engine)

UI_DIR = os.path.join(pr.BUNDLE, "ui")
THUMB_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


# ==========================================================================
# Background jobs with captured progress
# ==========================================================================
class Cancelled(BaseException):
    """Raised inside the job thread at its next progress print after Cancel."""


class Job:
    def __init__(self, kind, out_dir, title):
        self.kind, self.out_dir, self.title = kind, out_dir, title
        self.status = "Starting..."
        self.percent = None
        self.lines = deque(maxlen=400)
        self.done = False
        self.error = None
        self.result = {}
        self.cancel = False
        self.started = time.time()
        self.thread = None


class JobOutput(io.TextIOBase):
    """Replaces sys.stdout/stderr while a job runs: keeps a log, the current
    status line and a progress percentage; raises Cancelled when requested."""

    def __init__(self, job):
        super().__init__()
        self.job, self.buf = job, ""

    def writable(self):
        return True

    def write(self, s):
        job = self.job
        if job.cancel and threading.current_thread() is job.thread:
            raise Cancelled()
        self.buf += s
        while True:
            m = re.search(r"[\r\n]", self.buf)
            if not m:
                break
            piece, sep, self.buf = self.buf[:m.start()], self.buf[m.start()], self.buf[m.end():]
            self._line(piece, final=(sep == "\n"))
        if self.buf.strip():
            self._status(self.buf)
        return len(s)

    def _status(self, text):
        t = text.strip()
        if not t:
            return
        self.job.status = t
        p = re.search(r"(\d{1,3})%", t)
        if p:
            self.job.percent = min(100, int(p.group(1)))
            return
        p = re.search(r"(\d+)/(\d+)", t)
        if p and int(p.group(2)) > 0:
            self.job.percent = min(100, int(p.group(1)) * 100 // int(p.group(2)))

    def _line(self, text, final):
        self._status(text)
        if final and text.strip():
            self.job.lines.append(text.rstrip())

    def flush(self):
        pass


# ==========================================================================
# The API the window talks to
# ==========================================================================
class Api:
    def __init__(self):
        self._window = None
        self._job = None
        self._lock = threading.Lock()
        self._thumbs = {}

    def set_window(self, w):
        self._window = w

    # ---------------- general ----------------
    def info(self):
        return {"version": pr.__version__, "os": "windows" if os.name == "nt" else sys.platform,
                "admin": pr.is_admin(), "frozen": pr.FROZEN,
                "default_out": default_output_root(), "home": os.path.expanduser("~")}

    def pick_folder(self, start=""):
        r = self._window.create_file_dialog(_dialog("FOLDER"), directory=start or "")
        return r[0] if r else None

    def pick_file(self, kind="any"):
        types = {
            "image": ("Disk images (*.img;*.dd;*.raw;*.bin;*.iso)", "All files (*.*)"),
            "whatsapp": ("WhatsApp backup (*.crypt15;*.crypt14;*.crypt12;*.db;*.sqlite)", "All files (*.*)"),
            "key": ("All files (*.*)",),
            "zip": ("Snapchat export (*.zip)", "All files (*.*)"),
        }.get(kind, ("All files (*.*)",))
        r = self._window.create_file_dialog(_dialog("OPEN"), file_types=types)
        return r[0] if r else None

    def open_path(self, path):
        if not path or not os.path.exists(path):
            return False
        if os.name == "nt":
            os.startfile(path)                     # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True

    def open_viewer(self, path):
        """Open a chat viewer (index.html) in its own app window."""
        if not path or not os.path.isfile(path):
            return False
        try:
            import webview
            webview.create_window(os.path.basename(os.path.dirname(path)).replace("_", " "),
                                  url=path, width=1150, height=760, min_size=(700, 500))
            return True
        except Exception:
            return self.open_path(path)

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)
        return True

    def relaunch_admin(self):
        if os.name == "nt" and pr.relaunch_as_admin():
            self._close_soon()
            return True
        return False

    def restart(self):
        try:
            args = [sys.executable] + ([] if pr.FROZEN else [os.path.abspath(__file__)])
            subprocess.Popen(args, cwd=pr.HERE)
        finally:
            self._close_soon()
        return True

    def _close_soon(self):
        def later():
            time.sleep(0.5)
            if self._window:
                self._window.destroy()
        threading.Thread(target=later, daemon=True).start()

    # ---------------- device / source discovery ----------------
    def android_status(self):
        adb = _adb_path()
        if not adb:
            return {"adb": False}
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, timeout=20).stdout.decode(errors="replace")
        except Exception as e:
            return {"adb": True, "error": str(e), "devices": []}
        devs = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                d = {"serial": parts[0], "state": parts[1], "model": ""}
                if parts[1] == "device":
                    try:
                        d["model"] = subprocess.run([adb, "-s", parts[0], "shell", "getprop", "ro.product.model"],
                                                    capture_output=True, timeout=10).stdout.decode().strip()
                    except Exception:
                        pass
                devs.append(d)
        return {"adb": True, "devices": devs}

    def ios_backups(self, path=None):
        out = []
        for d in pr.locate_backup(path or None):
            try:
                import plistlib
                with open(os.path.join(d, "Manifest.plist"), "rb") as f:
                    enc = bool(plistlib.load(f).get("IsEncrypted", False))
            except Exception:
                enc = False
            out.append({"dir": d, "desc": pr.describe_backup(d), "encrypted": enc})
        return out

    def drives(self):
        return {"admin": pr.is_admin(), "drives": pr.list_drives()}

    def check_update(self):
        try:
            remote, _ = pr.latest_release()
        except Exception as e:
            return {"current": pr.__version__, "error": str(e)}
        return {"current": pr.__version__, "latest": remote,
                "newer": pr._vtuple(remote) > pr._vtuple(pr.__version__)}

    # ---------------- jobs ----------------
    def start(self, kind, p):
        with self._lock:
            if self._job and not self._job.done:
                return {"error": "Another task is still running."}
            p = p or {}
            cats = set(c for c in (p.get("types") or pr.CATEGORIES) if c in pr.CATEGORIES) or set(pr.CATEGORIES)
            root = p.get("out") or default_output_root()
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
            out = os.path.join(root, f"{TITLES.get(kind, kind).split(' ')[0]}_{stamp}") if kind != "update" else root
            fn, open_target = _job_function(kind, p, cats, out)
            job = Job(kind, out, TITLES.get(kind, kind))
            job.result["open"] = open_target
            self._job = job
            job.thread = threading.Thread(target=self._run, args=(job, fn), daemon=True)
            job.thread.start()
            return {"ok": True, "out": out}

    def _run(self, job, fn):
        sink = JobOutput(job)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = sink
        try:
            fn()
            job.status = "Finished"
            job.percent = 100
        except Cancelled:
            job.error = "Stopped by you."
        except SystemExit as e:
            if isinstance(e.code, str) and e.code.strip():
                job.error = e.code.strip()
            elif e.code not in (0, None):
                job.error = "\n".join(list(job.lines)[-6:]) or "The task stopped."
            else:
                job.status = "Finished"
                job.percent = 100
        except Exception as e:                    # show, never crash the window
            job.error = f"{type(e).__name__}: {e}"
            job.lines.append(traceback.format_exc())
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            job.done = True
            job.ended = time.time()

    def poll(self):
        j = self._job
        if not j:
            return {"idle": True}
        return {"kind": j.kind, "title": j.title, "status": j.status, "percent": j.percent,
                "log": list(j.lines)[-120:], "done": j.done, "error": j.error,
                "elapsed": int((getattr(j, "ended", None) or time.time()) - j.started),
                "out": j.out_dir, "open": j.result.get("open")}

    def cancel(self):
        if self._job and not self._job.done:
            self._job.cancel = True
            return True
        return False

    # ---------------- results ----------------
    def results(self, folder, limit=400):
        cats = {}
        total_size = 0
        if folder and os.path.isdir(folder):
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d not in ("chats_txt", "_snapchat_export", "media", "_backup")]
                for f in files:
                    if f in ("index.html", "memories.html"):
                        continue
                    p = os.path.join(root, f)
                    cat = pr.category_of(f) or "Other"
                    try:
                        size = os.path.getsize(p)
                    except OSError:
                        continue
                    total_size += size
                    c = cats.setdefault(cat, {"count": 0, "size": 0, "files": []})
                    c["count"] += 1
                    c["size"] += size
                    if len(c["files"]) < limit:
                        rel = os.path.relpath(p, folder)
                        c["files"].append({"path": p, "name": f, "rel": rel, "size": size,
                                           "damaged": "_Maybe_Damaged" in rel,
                                           "deleted": any(k in rel for k in ("Deleted", "Carved", "Trash"))})
        order = ["Photos", "Videos", "Audio", "Documents", "Archives", "Other"]
        return {"folder": folder, "total_size": total_size,
                "categories": [dict(name=k, **cats[k]) for k in order if k in cats]}

    def thumb(self, path):
        if path in self._thumbs:
            return self._thumbs[path]
        url = None
        if os.path.splitext(path)[1].lower() in THUMB_EXT:
            try:
                from PIL import Image
                with Image.open(path) as im:
                    im.thumbnail((220, 220))
                    b = io.BytesIO()
                    im.convert("RGB").save(b, "JPEG", quality=72)
                url = "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
            except Exception:
                url = None
        if len(self._thumbs) > 2000:
            self._thumbs.clear()
        self._thumbs[path] = url
        return url


TITLES = {"android": "Android recovery", "ios": "iPhone recovery", "drive": "Drive recovery",
          "sdcard": "SDCard recovery", "image": "Image scan", "makeimage": "Disk image",
          "wa_pull": "WhatsApp copy from phone", "wa_view": "WhatsApp chats", "sc_view": "Snapchat data",
          "sc_memories": "Snapchat memories", "install_adb": "Installing ADB", "update": "Update"}


def _dialog(kind):
    import webview
    fd = getattr(webview, "FileDialog", None)
    if fd is not None and hasattr(fd, kind):
        return getattr(fd, kind)
    return getattr(webview, f"{kind}_DIALOG")


def default_output_root():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "PhoneDataRecovery")


def _adb_path():
    for cand in (os.path.join(pr.HERE, "platform-tools", "adb.exe"), os.path.join(pr.HERE, "platform-tools", "adb"),
                 os.path.join(pr.HERE, "adb.exe"), os.path.join(pr.HERE, "adb")):
        if os.path.isfile(cand):
            return cand
    import shutil
    return shutil.which("adb")


def _need(v, msg):
    if not v:
        raise SystemExit(msg)
    return v


def _job_function(kind, p, cats, out):
    """Return (callable, path-to-open-when-done)."""
    if kind == "android":
        def fn():
            _need(_adb_path(), "ADB is not installed yet - click 'Install ADB' first.")
            pr.android_recover(out, cats, p.get("serial") or None, p.get("thumbnails", True))
        return fn, out
    if kind == "ios":
        def fn():
            bdir = _need(p.get("backup"), "Choose an iPhone backup first.")
            pr.ios_recover(bdir, out, cats, p.get("password") or None)
        return fn, out
    if kind in ("drive", "sdcard"):
        def fn():
            dev = _need(p.get("device"), "Choose a drive first.")
            pr.drive_recover(dev, out, cats, p.get("mode") or ("deep" if kind == "sdcard" else "quick"))
        return fn, out
    if kind == "image":
        def fn():
            pr.carve(_need(p.get("file"), "Choose an image file first."), out, cats)
        return fn, out
    if kind == "makeimage":
        target = p.get("save") or os.path.join(p.get("out") or default_output_root(), "disk.img")

        def fn():
            os.makedirs(os.path.dirname(target), exist_ok=True)
            pr.make_image(_need(p.get("device"), "Choose a drive first."), target)
        return fn, os.path.dirname(target)
    if kind == "install_adb":
        return (lambda: pr.download_platform_tools()), None
    if kind == "wa_pull":
        def fn():
            adb = _need(_adb_path(), "ADB is not installed yet - click 'Install ADB' first.")
            sys.path.insert(0, pr.HERE)
            import whatsapp
            whatsapp.pull_android(out, adb, pr.android_pick_device(adb, None), pr.adb_run)
        return fn, out
    if kind == "wa_view":
        html_out = os.path.join(out, "index.html")

        def fn():
            import whatsapp
            f = _need(p.get("file"), "Choose your WhatsApp backup file first.")
            if os.path.isdir(f):
                f = _need(whatsapp.newest_backup(f), "No msgstore backup found in that folder.")
                print(f"Using: {f}")
            key = (p.get("key") or "").strip() or None
            if re.search(r"\.crypt\d+$", f) and not key:
                raise SystemExit("This backup is encrypted: enter your 64-digit key or choose the key file.")
            media = [m for m in (p.get("media"),) if m]
            whatsapp.build_viewer(f, out, media, p.get("wadb") or None, False, key)
        return fn, html_out
    if kind in ("sc_view", "sc_memories"):
        def fn():
            import snapchat
            src = _need(p.get("export"), "Choose your Snapchat export (zip or folder) first.")
            if kind == "sc_memories":
                snapchat.download_memories(src, out)
                snapchat.build_viewer(src, out)
            else:
                snapchat.build_viewer(src, out, bool(p.get("download")))
        return fn, os.path.join(out, "index.html")
    if kind == "update":
        return (lambda: pr.self_update()), None
    raise SystemExit(f"Unknown task: {kind}")


# ==========================================================================
# Start
# ==========================================================================
def selftest(report):
    """No window: run a real scan through the job system (for CI)."""
    import tempfile
    lines = []
    try:
        import webview
        lines.append(f"webview import OK ({getattr(webview, '__name__', '')})")
        assert os.path.isfile(os.path.join(UI_DIR, "index.html")), "ui/index.html missing from package"
        lines.append("ui OK")
        tmp = tempfile.mkdtemp()
        img = os.path.join(tmp, "card.img")
        from PIL import Image
        b = io.BytesIO()
        Image.new("RGB", (300, 200), (30, 140, 90)).save(b, "PNG")
        with open(img, "wb") as f:
            f.write(os.urandom(70000) + b.getvalue() + os.urandom(50000))
        api = Api()
        r = api.start("image", {"file": img, "out": tmp})
        assert r.get("ok"), r
        for _ in range(300):
            st = api.poll()
            if st["done"]:
                break
            time.sleep(0.1)
        assert st["done"] and not st["error"], st
        res = api.results(st["out"])
        n = sum(c["count"] for c in res["categories"])
        assert n == 1, res
        assert api.thumb(res["categories"][0]["files"][0]["path"]).startswith("data:image/jpeg")
        lines.append(f"scan OK ({n} file)")
        lines.append(f"version {pr.__version__}")
        ok = True
    except BaseException as e:
        lines.append("FAILED: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)))
        ok = False
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\nSELFTEST OK\n" if ok else "\n"))
    return 0 if ok else 1


def _message_box(text):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Phone Data Recovery", 0x10)
    else:
        print(text)


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        report = sys.argv[i + 1] if len(sys.argv) > i + 1 else os.path.join(pr.HERE, "selftest.txt")
        sys.exit(selftest(report))
    try:
        import webview
    except ImportError:
        _message_box("The window component (pywebview) is missing.\nInstall it with:  pip install pywebview")
        sys.exit(1)
    api = Api()
    window = webview.create_window("Phone Data Recovery", url=os.path.join(UI_DIR, "index.html"),
                                   js_api=api, width=1200, height=780, min_size=(1000, 660),
                                   background_color="#0f172a")
    api.set_window(window)
    try:
        webview.start(debug="--debug" in sys.argv)
    except Exception as e:
        _message_box("Could not open the window.\n\n"
                     "On Windows install 'Microsoft Edge WebView2 Runtime' from\n"
                     "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                     f"Details: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
