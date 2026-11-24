#!/usr/bin/env python3
"""
Phone Data Recovery Tool
========================
Recovers photos, videos, voice notes / audio (MP3, M4A, OPUS, AMR, WAV, OGG)
and documents (PDF, Word, Excel, PowerPoint, TXT ...) from:

  android   Android phone over USB (ADB) - copies all media/docs, including
            files sitting in the hidden "trash" (.trashed-*) and thumbnails.
  ios       iPhone backup (Finder / iTunes / idevicebackup2), encrypted or not.
  carve     Deleted files on an SD card / USB drive / disk image, found by
            scanning raw bytes for file signatures ("file carving").
  drive     Hard drive / SSD / NVMe / USB: deleted files with original names
            (NTFS) plus a deep signature scan of free space.
  whatsapp  Decrypt your WhatsApp backup (.crypt14/.crypt15) and read the chats
            in an offline viewer (Android msgstore.db or iPhone ChatStorage).
  snapchat  Read your Snapchat "My Data" export (chats viewer) and download
            all Memories.
  update    Download and install the newest version from GitHub.
  image     Make a raw image of an SD card / drive (or a rooted Android's
            block device) so you can run `carve` on it safely.

Use only on devices you own or have permission to access.

Run without arguments for a simple menu, or see:  python phone_recovery.py -h
"""

import argparse
import io
import json
import mmap
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import OrderedDict

# --------------------------------------------------------------------------
# Version / update source
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
GITHUB_REPO = "kashif55505/my-apps"
APP_FOLDER = "phone-data-recovery"
RELEASE_TAG = "phone-data-recovery-latest"
RELEASE_ZIP = "PhoneDataRecovery.zip"


def local_version():
    try:
        with open(os.path.join(HERE, "VERSION")) as f:
            return f.read().strip() or "0"
    except OSError:
        return "0"


__version__ = local_version()

# --------------------------------------------------------------------------
# File categories
# --------------------------------------------------------------------------
CATEGORIES = {
    "Photos": {"jpg", "jpeg", "png", "gif", "heic", "heif", "webp", "bmp", "dng", "avif"},
    "Videos": {"mp4", "mov", "3gp", "3g2", "mkv", "avi", "webm", "m4v"},
    "Audio": {"mp3", "m4a", "aac", "wav", "ogg", "opus", "amr", "awb", "flac", "wma", "caf"},
    "Documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf",
                  "odt", "ods", "odp", "csv", "epub", "pages", "numbers", "key"},
    "Archives": {"zip", "rar", "7z", "apk"},
}
EXT_TO_CAT = {e: c for c, exts in CATEGORIES.items() for e in exts}


def category_of(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return EXT_TO_CAT.get(ext)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def safe_join(base, rel):
    """Join rel under base, never escaping base (strips .., drive letters, bad chars)."""
    parts = []
    for p in re.split(r"[\\/]+", rel):
        if p in ("", ".", ".."):
            continue
        p = re.sub(r'[<>:"|?*\x00-\x1f]', "_", p)
        parts.append(p)
    return os.path.join(base, *parts) if parts else base


def unique_path(path):
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{root}_{i}{ext}"):
        i += 1
    return f"{root}_{i}{ext}"


def parse_cats(value):
    if not value:
        return set(CATEGORIES)
    wanted = set()
    for v in value.split(","):
        v = v.strip().capitalize()
        if v not in CATEGORIES:
            sys.exit(f"Unknown category '{v}'. Choose from: {', '.join(CATEGORIES)}")
        wanted.add(v)
    return wanted


# ==========================================================================
# 1) ANDROID over ADB
# ==========================================================================
def find_adb():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "platform-tools", "adb"),
                 os.path.join(here, "platform-tools", "adb.exe"),
                 os.path.join(here, "adb"), os.path.join(here, "adb.exe")):
        if os.path.isfile(cand):
            return cand
    found = shutil.which("adb")
    if not found:
        sys.exit("ADB not found. Download Android 'platform-tools' from\n"
                 "https://developer.android.com/tools/releases/platform-tools\n"
                 "and unzip it next to this script (folder name: platform-tools).")
    return found


def adb_run(adb, args, serial=None):
    cmd = [adb] + (["-s", serial] if serial else []) + args
    return subprocess.run(cmd, capture_output=True)


def android_pick_device(adb, serial):
    out = adb_run(adb, ["devices"]).stdout.decode(errors="replace")
    devices, unauthorized = [], []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            (devices if parts[1] == "device" else unauthorized).append(parts[0])
    if serial:
        if serial not in devices:
            sys.exit(f"Device {serial} not connected/authorized.")
        return serial
    if not devices:
        msg = "No Android phone found.\n"
        if unauthorized:
            msg += "Phone is connected but not authorized: unlock it and tap 'Allow USB debugging'.\n"
        else:
            msg += ("1) Settings > About phone > tap 'Build number' 7 times\n"
                    "2) Settings > Developer options > turn on 'USB debugging'\n"
                    "3) Connect USB cable, choose 'File transfer', tap 'Allow'.\n")
        sys.exit(msg)
    if len(devices) > 1:
        sys.exit(f"Several phones connected: {devices}. Use --serial to choose one.")
    return devices[0]


def android_storage_roots(adb, serial):
    roots = ["/sdcard"]
    out = adb_run(adb, ["shell", "ls", "/storage"], serial).stdout.decode(errors="replace")
    for name in out.split():
        if name not in ("emulated", "self", "sdcard0") and re.fullmatch(r"[0-9A-Fa-f-]{4,}", name):
            roots.append(f"/storage/{name}")          # external SD card
    return roots


TRASH_RE = re.compile(r"^\.trashed-\d+-(.+)$")


def android_recover(out_dir, cats, serial=None, thumbnails=True):
    adb = find_adb()
    serial = android_pick_device(adb, serial)
    model = adb_run(adb, ["shell", "getprop", "ro.product.model"], serial).stdout.decode().strip()
    print(f"Connected: {model or serial}")

    jobs = []   # (remote_path, local_path, label)
    for root in android_storage_roots(adb, serial):
        print(f"Listing files in {root} ...")
        res = adb_run(adb, ["shell", f"find '{root}/' -type f 2>/dev/null"], serial)
        for remote in res.stdout.decode("utf-8", errors="replace").splitlines():
            remote = remote.strip()
            if not remote or "/Android/data/" in remote or "/Android/obb/" in remote:
                continue
            rel = remote[len(root):].lstrip("/")
            base = os.path.basename(remote)
            src_label = "SDCard" if root != "/sdcard" else ""

            m = TRASH_RE.match(base)
            if m:                                   # file in Android's trash
                cat = category_of(m.group(1))
                if cat in cats:
                    local = safe_join(os.path.join(out_dir, "Deleted_Trash", cat),
                                      os.path.join(os.path.dirname(rel), m.group(1)))
                    jobs.append((remote, local, "trash"))
                continue
            if "/.thumbnails/" in remote or "/.Thumbnails/" in remote:
                if thumbnails and "Photos" in cats:
                    local = safe_join(os.path.join(out_dir, "Thumbnails"), rel)
                    jobs.append((remote, local, "thumb"))
                continue
            if "/.Trash" in remote or "/.trash" in remote or "/.recycle" in remote.lower():
                cat = category_of(base)
                if cat in cats:
                    jobs.append((remote, safe_join(os.path.join(out_dir, "Deleted_Trash", cat), rel), "trash"))
                continue
            cat = category_of(base)
            if cat in cats:
                jobs.append((remote, safe_join(os.path.join(out_dir, cat, src_label), rel), "file"))

    if not jobs:
        print("No matching files found.")
        return
    counts = {}
    for _, local, label in jobs:
        counts[label] = counts.get(label, 0) + 1
    print(f"Found {len(jobs)} files  (normal: {counts.get('file', 0)}, "
          f"in trash: {counts.get('trash', 0)}, thumbnails: {counts.get('thumb', 0)})")

    ok = fail = skipped = 0
    for i, (remote, local, _) in enumerate(jobs, 1):
        if os.path.exists(local):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(local), exist_ok=True)
        r = adb_run(adb, ["pull", "-a", remote, local], serial)
        if r.returncode == 0 and os.path.exists(local):
            ok += 1
        else:
            fail += 1
        if i % 25 == 0 or i == len(jobs):
            print(f"\r  copied {i}/{len(jobs)}", end="", flush=True)
    print(f"\nDone. Copied {ok}, already there {skipped}, failed {fail}.  Output: {out_dir}")
    print("Note: deleted files that already left the trash cannot be read over ADB.\n"
          "      If the phone has an SD card, use 'image' + 'carve' on the SD card.")


def android_image(out_file, block, serial=None):
    """Raw image of a block device on a ROOTED phone (e.g. external SD card)."""
    adb = find_adb()
    serial = android_pick_device(adb, serial)
    test = adb_run(adb, ["shell", "su -c id"], serial).stdout.decode()
    if "uid=0" not in test:
        sys.exit("Root access is needed for raw imaging (not available on this phone).\n"
                 "Instead: put the SD card in a card reader and use 'image' on the computer.")
    print(f"Imaging {block} -> {out_file} (this can take a long time) ...")
    cmd = [adb, "-s", serial, "exec-out", f"su -c 'dd if={block} bs=4M 2>/dev/null'"]
    with open(out_file, "wb") as f, subprocess.Popen(cmd, stdout=subprocess.PIPE) as p:
        total = 0
        while True:
            chunk = p.stdout.read(4 << 20)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            print(f"\r  {human(total)}", end="", flush=True)
    print(f"\nSaved {human(total)}. Now run:  carve \"{out_file}\"")


# ==========================================================================
# 2) iPhone backup
# ==========================================================================
def default_backup_dirs():
    home = os.path.expanduser("~")
    return [os.path.join(home, "Library", "Application Support", "MobileSync", "Backup"),
            os.path.join(os.environ.get("APPDATA", ""), "Apple Computer", "MobileSync", "Backup"),
            os.path.join(home, "Apple", "MobileSync", "Backup")]


def locate_backup(path):
    """Accept a backup folder or a folder holding backups; return list of backup dirs."""
    if path and os.path.isfile(os.path.join(path, "Manifest.db")):
        return [path]
    search = [path] if path else default_backup_dirs()
    found = []
    for d in search:
        if d and os.path.isdir(d):
            for sub in sorted(os.listdir(d)):
                full = os.path.join(d, sub)
                if os.path.isfile(os.path.join(full, "Manifest.db")):
                    found.append(full)
    return found


def describe_backup(bdir):
    try:
        with open(os.path.join(bdir, "Info.plist"), "rb") as f:
            info = plistlib.load(f)
        return f"{info.get('Device Name', '?')} ({info.get('Product Type', '?')}, " \
               f"iOS {info.get('Product Version', '?')}, {info.get('Last Backup Date', '')})"
    except Exception:
        return os.path.basename(bdir)


def short_domain(domain, rel):
    if domain.startswith("CameraRollDomain"):
        return "CameraRoll"
    if "VoiceMemos" in domain or "Recordings" in rel:
        return "VoiceMemos"
    if "whatsapp" in domain.lower():
        return "WhatsApp"
    if domain.startswith(("AppDomain-", "AppDomainGroup-", "AppDomainPlugin-")):
        return domain.split("-", 1)[1]
    return domain


def ios_make_backup(target):
    tool = shutil.which("idevicebackup2")
    if not tool:
        sys.exit("idevicebackup2 not found. Make the backup with Finder (Mac) or iTunes /\n"
                 "Apple Devices app (Windows): connect iPhone > 'Back up all data to this computer'.\n"
                 "Then run:  python phone_recovery.py ios")
    os.makedirs(target, exist_ok=True)
    print("Unlock the iPhone and tap 'Trust'. Backing up ...")
    subprocess.run([tool, "backup", "--full", target])


def ios_recover(backup, out_dir, cats, password=None):
    dirs = locate_backup(backup)
    if not dirs:
        sys.exit("No iPhone backup found. Make one first (Finder / iTunes / 'ios-backup').")
    if len(dirs) > 1:
        print("Several backups found:")
        for i, d in enumerate(dirs, 1):
            print(f"  {i}) {describe_backup(d)}")
        choice = input("Choose number: ").strip()
        bdir = dirs[int(choice) - 1]
    else:
        bdir = dirs[0]
    print(f"Backup: {describe_backup(bdir)}")

    with open(os.path.join(bdir, "Manifest.plist"), "rb") as f:
        encrypted = plistlib.load(f).get("IsEncrypted", False)

    tmp = None
    eb = None
    manifest = os.path.join(bdir, "Manifest.db")
    if encrypted:
        try:
            from iphone_backup_decrypt import EncryptedBackup
        except ImportError:
            sys.exit("This backup is encrypted. Install support with:\n"
                     "  pip install iphone_backup_decrypt\nthen run again.")
        if password is None:
            import getpass
            password = getpass.getpass("Backup password: ")
        eb = EncryptedBackup(backup_directory=bdir, passphrase=password)
        tmp = tempfile.mkdtemp()
        manifest = os.path.join(tmp, "Manifest.db")
        eb.save_manifest_file(manifest)

    con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
    rows = con.execute("SELECT fileID, domain, relativePath FROM Files WHERE flags = 1").fetchall()
    con.close()

    jobs = []
    for file_id, domain, rel in rows:
        cat = category_of(rel or "")
        if cat in cats:
            jobs.append((file_id, domain, rel, cat))
    print(f"Found {len(jobs)} matching files in backup.")

    ok = fail = 0
    for i, (file_id, domain, rel, cat) in enumerate(jobs, 1):
        dest = unique_path(safe_join(os.path.join(out_dir, cat, short_domain(domain, rel)), rel))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            if eb:
                eb.extract_file(relative_path=rel, domain_like=domain, output_filename=dest)
            else:
                src = os.path.join(bdir, file_id[:2], file_id)
                if not os.path.exists(src):
                    src = os.path.join(bdir, file_id)     # very old backup layout
                shutil.copy2(src, dest)
            ok += 1
        except Exception:
            fail += 1
        if i % 50 == 0 or i == len(jobs):
            print(f"\r  extracted {i}/{len(jobs)}", end="", flush=True)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nDone. Extracted {ok}, failed {fail}.  Output: {out_dir}")
    print("Tip: photos in 'Recently Deleted' are included while they are still in that album.")


# ==========================================================================
# 3) FILE CARVING (SD card / drive image)
# ==========================================================================
u16le = lambda m, i: int.from_bytes(m[i:i + 2], "little")
u32le = lambda m, i: int.from_bytes(m[i:i + 4], "little")
u32be = lambda m, i: int.from_bytes(m[i:i + 4], "big")
MB = 1 << 20


def carve_jpeg(m, s, n):
    lim = min(n, s + 60 * MB)
    i, seen_sos = s + 2, False
    while i + 4 <= lim:
        if m[i] != 0xFF:
            return None
        mk = m[i + 1]
        if mk == 0xFF:
            i += 1
            continue
        if mk == 0xD9:
            return (i + 2, "jpg") if seen_sos else None
        if 0xD0 <= mk <= 0xD7 or mk in (0x01, 0xD8):
            i += 2
            continue
        seg = int.from_bytes(m[i + 2:i + 4], "big")
        if seg < 2:
            return None
        if mk != 0xDA:
            i += 2 + seg
            continue
        seen_sos = True                      # entropy-coded data follows
        j = i + 2 + seg
        while True:
            j = m.find(b"\xFF", j, lim)
            if j < 0 or j + 1 >= lim:
                return None
            b = m[j + 1]
            if b == 0x00 or 0xD0 <= b <= 0xD7:
                j += 2
            elif b == 0xFF:
                j += 1
            else:
                break
        i = j
    return None


def carve_png(m, s, n):
    i, lim = s + 8, min(n, s + 100 * MB)
    while i + 12 <= lim:
        length, ctype = u32be(m, i), bytes(m[i + 4:i + 8])
        if not ctype.isalpha():
            return None
        i += 12 + length
        if ctype == b"IEND":
            return (i, "png") if i <= n else None
    return None


def _skip_subblocks(m, i, lim):
    while i < lim:
        size = m[i]
        i += 1
        if size == 0:
            return i
        i += size
    return None


def carve_gif(m, s, n):
    lim = min(n, s + 50 * MB)
    flags = m[s + 10]
    i = s + 13 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)
    frames = 0
    while i is not None and i < lim:
        b = m[i]
        if b == 0x3B:
            return (i + 1, "gif") if frames else None
        if b == 0x21:
            i = _skip_subblocks(m, i + 2, lim)
        elif b == 0x2C:
            lf = m[i + 9]
            i += 10 + (3 * (2 << (lf & 7)) if lf & 0x80 else 0)
            i = _skip_subblocks(m, i + 1, lim)
            frames += 1
        else:
            return None
    return None


ISO_TOP = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"uuid", b"meta",
           b"moof", b"mfra", b"sidx", b"styp", b"pdin", b"udta", b"iinf", b"idat"}


def carve_isobmff(m, s, n):
    """MP4 / MOV / M4A / 3GP / HEIC (all start with '....ftyp')."""
    brand = bytes(m[s + 8:s + 12])
    if not re.fullmatch(rb"[a-zA-Z0-9 ]{4}", brand):
        return None
    lim = min(n, s + 8 * 1024 * MB)
    i, boxes, has_data = s, 0, False
    while i + 8 <= n:
        size, btype = u32be(m, i), bytes(m[i + 4:i + 8])
        if btype not in ISO_TOP:
            break
        if size == 1:
            size = int.from_bytes(m[i + 8:i + 16], "big")
        elif size == 0:
            size = n - i
        if size < 8 or i + size > lim:
            if btype == b"mdat":          # truncated video: keep what exists
                i = min(n, lim)
                has_data = True
                boxes += 1
            break
        has_data |= btype in (b"mdat", b"moov", b"meta")
        i += size
        boxes += 1
    if boxes < 2 or not has_data:
        return None
    b = brand.lower()
    if b in (b"qt  ",):
        ext = "mov"
    elif b in (b"m4a ", b"m4b ", b"m4p "):
        ext = "m4a"
    elif b.startswith((b"3gp", b"3g2", b"3ge")):
        ext = "3gp"
    elif b in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc"):
        ext = "heic"
    elif b in (b"avif", b"avis"):
        ext = "avif"
    else:
        ext = "mp4"
    return i, ext


MP3_BR = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
          2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
MP3_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def mp3_frame_len(m, i):
    if m[i] != 0xFF or (m[i + 1] & 0xE0) != 0xE0:
        return 0
    ver, layer = (m[i + 1] >> 3) & 3, (m[i + 1] >> 1) & 3
    br_i, sr_i, pad = m[i + 2] >> 4, (m[i + 2] >> 2) & 3, (m[i + 2] >> 1) & 1
    if ver == 1 or layer != 1 or br_i in (0, 15) or sr_i == 3:
        return 0
    br = MP3_BR[1 if ver == 3 else 2][br_i] * 1000
    sr = MP3_SR[ver][sr_i]
    return (144 if ver == 3 else 72) * br // sr + pad


def carve_mp3(m, s, n):
    size = (m[s + 6] << 21) | (m[s + 7] << 14) | (m[s + 8] << 7) | m[s + 9]
    i = s + 10 + size + (10 if m[s + 5] & 0x10 else 0)
    k = 0
    while i < n and m[i] == 0 and k < 4096:        # tolerate extra padding
        i += 1
        k += 1
    frames, lim = 0, min(n, s + 300 * MB)
    while i + 4 <= lim:
        fl = mp3_frame_len(m, i)
        if fl == 0:
            break
        i += fl
        frames += 1
    if frames < 10:
        return None
    if m[i:i + 3] == b"TAG":
        i += 128
    return min(i, n), "mp3"


def carve_ogg(m, s, n):
    i, first, ext, pages = s, True, "ogg", 0
    lim = min(n, s + 500 * MB)
    while i + 27 <= lim and m[i:i + 4] == b"OggS":
        nseg = m[i + 26]
        if i + 27 + nseg > n:
            break
        body = sum(m[i + 27:i + 27 + nseg])
        if first:
            head = bytes(m[i + 27 + nseg:i + 27 + nseg + 8])
            ext = "opus" if head == b"OpusHead" else "ogg"
            first = False
        flag = m[i + 5]
        i += 27 + nseg + body
        pages += 1
        if flag & 4:
            break
    if pages < 2:
        return None
    return min(i, n), ext


AMR_NB = [12, 13, 15, 17, 19, 20, 26, 31, 5, 6, 5, 5, 0, 0, 0, 0]
AMR_WB = [17, 23, 32, 36, 40, 46, 50, 58, 60, 5, 0, 0, 0, 0, 0, 0]


def carve_amr(m, s, n):
    wb = m[s + 5:s + 8] == b"-WB"
    table, i = (AMR_WB, s + 9) if wb else (AMR_NB, s + 6)
    frames, lim = 0, min(n, s + 100 * MB)
    while i < lim:
        toc = m[i]
        if toc & 0x83:
            break
        mode = (toc >> 3) & 0xF
        if (wb and 10 <= mode <= 13) or (not wb and 12 <= mode <= 14):
            break
        size = 1 + table[mode]
        if mode == 0 and not any(m[i:i + size]):       # zeroed free space
            break
        i += size
        frames += 1
    return (min(i, n), "awb" if wb else "amr") if frames >= 5 else None


def carve_riff(m, s, n):
    size, form = u32le(m, s + 4), bytes(m[s + 8:s + 12])
    ext = {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}.get(form)
    if not ext or size < 12 or size > 4096 * MB:
        return None
    return min(n, s + 8 + size + (size & 1)), ext


def carve_pdf(m, s, n):
    lim = min(n, s + 200 * MB)
    nxt = m.find(b"%PDF-", s + 5, lim)
    if nxt != -1:
        lim = nxt
    e = m.find(b"%%EOF", s, lim)
    if e < 0:
        return None
    end = e + 5
    while True:                                       # incremental updates
        e2 = m.find(b"%%EOF", end, min(lim, end + 5 * MB))
        if e2 < 0:
            break
        after = bytes(m[end:end + 40]).lstrip()
        if not (after[:1].isdigit() or after.startswith(b"xref")):
            break
        end = e2 + 5
    while end < n and m[end] in (0x0D, 0x0A):
        end += 1
    return end, "pdf"


def carve_zip(m, s, n):
    lim = min(n, s + 500 * MB)
    e = m.find(b"PK\x05\x06", s, lim)
    if e < 0 or e + 22 > n:
        return None
    end = min(n, e + 22 + u16le(m, e + 20))
    try:
        z = zipfile.ZipFile(io.BytesIO(m[s:end]))
        names = z.namelist()
    except Exception:
        return None
    nameset = set(names)
    ext = "zip"
    if "[Content_Types].xml" in nameset:
        if any(x.startswith("word/") for x in names):
            ext = "docx"
        elif any(x.startswith("xl/") for x in names):
            ext = "xlsx"
        elif any(x.startswith("ppt/") for x in names):
            ext = "pptx"
    elif "mimetype" in nameset:
        mt = z.read("mimetype")
        ext = {b"application/vnd.oasis.opendocument.text": "odt",
               b"application/vnd.oasis.opendocument.spreadsheet": "ods",
               b"application/vnd.oasis.opendocument.presentation": "odp",
               b"application/epub+zip": "epub"}.get(mt.strip(), "zip")
    elif "AndroidManifest.xml" in nameset:
        ext = "apk"
    return end, ext


def carve_ole(m, s, n):
    """Old Office files: .doc .xls .ppt"""
    shift = u16le(m, s + 0x1E)
    if shift not in (9, 12):
        return None
    ss = 1 << shift
    nfat = u32le(m, s + 0x2C)
    if nfat == 0 or nfat > 50000:
        return None
    max_used, per = -1, ss // 4
    for idx in range(min(nfat, 109)):
        fs = u32le(m, s + 0x4C + 4 * idx)
        off = s + ss * (fs + 1)
        if fs >= 0xFFFFFFFA or off + ss > n:
            return None
        sector = m[off:off + ss]
        for k in range(per - 1, -1, -1):
            if int.from_bytes(sector[4 * k:4 * k + 4], "little") != 0xFFFFFFFF:
                max_used = max(max_used, idx * per + k)
                break
    if max_used < 0:
        return None
    end = min(n, s + ss * (max_used + 2))
    blob = m[s:end]
    if "WordDocument".encode("utf-16-le") in blob:
        ext = "doc"
    elif "Workbook".encode("utf-16-le") in blob or "Book".encode("utf-16-le") in blob:
        ext = "xls"
    elif "PowerPoint Document".encode("utf-16-le") in blob:
        ext = "ppt"
    else:
        ext = "ole"
    return end, ext


def _gif_ok(m, s, n):
    return carve_gif(m, s, n) if m[s + 4] in (0x37, 0x39) and m[s + 5] == 0x61 else None


def _id3_ok(m, s, n):
    return carve_mp3(m, s, n) if 2 <= m[s + 3] <= 4 and m[s + 4] == 0 else None


def _amr_ok(m, s, n):
    return carve_amr(m, s, n) if m[s + 5] in (0x0A, 0x2D) else None


SIGNATURES = [   # (literal bytes, offset of literal inside file, parser, category hint)
    (b"\xFF\xD8\xFF", 0, carve_jpeg, "Photos"),
    (b"\x89PNG\r\n\x1a\n", 0, carve_png, "Photos"),
    (b"GIF8", 0, _gif_ok, "Photos"),
    (b"ftyp", 4, carve_isobmff, None),
    (b"ID3", 0, _id3_ok, "Audio"),
    (b"OggS\x00\x02", 0, carve_ogg, "Audio"),
    (b"#!AMR", 0, _amr_ok, "Audio"),
    (b"RIFF", 0, carve_riff, None),
    (b"%PDF-", 0, carve_pdf, "Documents"),
    (b"PK\x03\x04", 0, carve_zip, None),
    (b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 0, carve_ole, "Documents"),
]
MIN_SIZE = {"jpg": 2048, "png": 200, "gif": 100}


def _candidates(buf, base, limit, sigs):
    """Signature hits in buf (which starts at absolute offset base); starts < limit."""
    hits = []
    for lit, off, parser, _ in sigs:
        i = buf.find(lit, off)
        while i != -1:
            start = base + i - off
            if start >= limit:
                break
            if start >= base:
                hits.append((start, parser))
            i = buf.find(lit, i + 1)
    hits.sort(key=lambda h: h[0])
    return hits


def carve(image, out_dir, cats, regions=None, src=None, label=None):
    """Signature-scan a file/device. regions: list of (start, end) byte ranges to search."""
    own = src is None
    if own:
        src = open_source(image)
    n = len(src)
    if n == 0:
        sys.exit("Image is empty or size unknown.")
    regions = [(max(0, a), min(n, b)) for a, b in (regions or [(0, n)]) if b > a]
    total = sum(b - a for a, b in regions) or 1
    sigs = [s for s in SIGNATURES if s[3] is None or s[3] in cats]
    found, done = {}, 0
    print(f"Deep scan of {label or image} ({human(total)} to scan) ...")
    t0 = time.time()
    skip, WIN = 0, 64 * MB
    for ra, rb in regions:
        pos = ra
        while pos < rb:
            wend = min(rb, pos + WIN)
            buf = src[pos:min(n, wend + 16)]
            for s, parser in _candidates(buf, pos, wend, sigs):
                if s < skip:
                    continue
                try:
                    res = parser(src, s, n)
                except (IndexError, ValueError):
                    res = None
                if not res:
                    continue
                end, ext = res
                cat = EXT_TO_CAT.get(ext, "Other")
                if cat not in cats or end - s < MIN_SIZE.get(ext, 64):
                    continue
                dest = os.path.join(out_dir, "Carved", cat, f"{ext}_{s:012X}.{ext}")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as o:
                    for c in range(s, end, 16 * MB):
                        o.write(src[c:min(end, c + 16 * MB)])
                found[ext] = found.get(ext, 0) + 1
                skip = end
            done += wend - pos
            pos = wend
            el = time.time() - t0
            eta = el / done * (total - done)
            print(f"\r  {done * 100 // total:3d}%  found {sum(found.values())} files  "
                  f"(ETA {int(eta // 3600)}h{int(eta % 3600 // 60):02d}m{int(eta % 60):02d}s)   ",
                  end="", flush=True)
    print()
    if own:
        src.close()
    if not found:
        print("Nothing recoverable found.")
        return
    print("Recovered:", ", ".join(f"{v} {k}" for k, v in sorted(found.items())))
    print(f"Output: {os.path.join(out_dir, 'Carved')}")
    print("Note: carved files lose their original names. Some may be partly damaged\n"
          "      if that space was already reused.")


# ==========================================================================
# Reading files AND raw drives (HDD / SSD / NVMe / USB / SD) the same way
# ==========================================================================
def is_raw_device(path):
    p = path.replace("/", "\\")
    if p.startswith("\\\\.\\"):
        return True
    try:
        import stat
        return stat.S_ISBLK(os.stat(path).st_mode) or stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


def device_size(path, f):
    try:
        size = f.seek(0, 2)
        f.seek(0)
        if size > 0:
            return size
    except OSError:
        pass
    if os.name == "nt":
        import ctypes
        import ctypes.wintypes
        import msvcrt
        length, ret = ctypes.c_longlong(0), ctypes.wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            ctypes.wintypes.HANDLE(msvcrt.get_osfhandle(f.fileno())), 0x7405C,  # IOCTL_DISK_GET_LENGTH_INFO
            None, 0, ctypes.byref(length), 8, ctypes.byref(ret), None)
        if ok and length.value > 0:
            return length.value
    elif sys.platform == "darwin":
        try:
            info = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", path],
                                                 capture_output=True).stdout)
            return int(info.get("TotalSize") or info.get("Size") or 0)
        except Exception:
            pass
    else:
        name = os.path.basename(os.path.realpath(path))
        try:
            with open(f"/sys/class/block/{name}/size") as sf:
                return int(sf.read()) * 512
        except OSError:
            pass
    return 0


class DiskReader:
    """Byte-addressable, cached, sector-aligned reader for raw devices.
    Supports src[i], src[a:b], src.find(sub, a, b) and len(src), like mmap.
    Unreadable (bad) sectors are returned as zeros."""
    BLOCK = 4 * MB

    def __init__(self, path):
        try:
            self.f = open(path, "rb", buffering=0)
        except PermissionError:
            sys.exit("Permission denied. Run as Administrator (Windows) or with sudo (Linux/Mac).")
        self.size = device_size(path, self.f)
        self.cache = OrderedDict()
        self.bad_sectors = 0

    def __len__(self):
        return self.size

    def close(self):
        self.f.close()

    def _read_at(self, off, length):
        self.f.seek(off)
        return self.f.read(length)

    def _block(self, b):
        data = self.cache.get(b)
        if data is not None:
            self.cache.move_to_end(b)
            return data
        off = b * self.BLOCK
        want = min(self.BLOCK, self.size - off)
        aligned = (want + 4095) // 4096 * 4096
        try:
            data = self._read_at(off, aligned)
        except OSError:
            parts = []                                   # retry sector by sector
            for so in range(off, off + aligned, 4096):
                try:
                    parts.append(self._read_at(so, 4096))
                except OSError:
                    parts.append(b"\x00" * 4096)
                    self.bad_sectors += 1
            data = b"".join(parts)
        data = data[:want].ljust(want, b"\x00")
        self.cache[b] = data
        if len(self.cache) > 24:
            self.cache.popitem(last=False)
        return data

    def __getitem__(self, k):
        if isinstance(k, slice):
            a = 0 if k.start is None else max(0, k.start)
            e = self.size if k.stop is None else min(k.stop, self.size)
            if a >= e:
                return b""
            B = self.BLOCK
            first, last = a // B, (e - 1) // B
            if first == last:
                return self._block(first)[a - first * B:e - first * B]
            out = [self._block(first)[a - first * B:]]
            for blk in range(first + 1, last):
                out.append(self._block(blk))
            out.append(self._block(last)[:e - last * B])
            return b"".join(out)
        if k < 0:
            k += self.size
        if not 0 <= k < self.size:
            raise IndexError(k)
        return self._block(k // self.BLOCK)[k % self.BLOCK]

    def find(self, sub, a=0, b=None):
        b = self.size if b is None else min(b, self.size)
        L, pos = len(sub), a
        while pos < b:
            chunk = self[pos:min(b, pos + self.BLOCK + L - 1)]
            r = chunk.find(sub)
            if r >= 0:
                return pos + r
            pos += self.BLOCK
        return -1


class FileSource:
    """mmap-backed source for image files."""
    def __init__(self, path):
        self.f = open(path, "rb")
        self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ) if os.path.getsize(path) else b""

    def __len__(self):
        return len(self.m)

    def __getitem__(self, k):
        return self.m[k]

    def find(self, sub, a=0, b=None):
        return self.m.find(sub, a, len(self.m) if b is None else b)

    def close(self):
        if self.m:
            self.m.close()
        self.f.close()


def open_source(path):
    if os.name == "nt" and re.fullmatch(r"[A-Za-z]:\\?", path):
        path = "\\\\.\\" + path[:2]                    # D:  ->  \\.\D:
    return DiskReader(path) if is_raw_device(path) else FileSource(path)


# ==========================================================================
# 5) HARD DRIVES: list drives, partitions, NTFS deleted files
# ==========================================================================
def _kind(media, bus, rota=None):
    media, bus = str(media).upper(), str(bus).upper()
    if bus in ("NVME", "17") or "PCI" in bus:
        return "NVMe SSD"
    if bus in ("USB", "7"):
        return "USB"
    if media in ("SSD", "4") or rota is False:
        return "SSD"
    if media in ("HDD", "3") or rota is True:
        return "HDD"
    return "Disk"


def list_drives():
    drives = []
    try:
        if os.name == "nt":
            ps = ("Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,BusType,Size "
                  "| ConvertTo-Json")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True).stdout
            data = json.loads(out) if out.strip() else []
            for d in ([data] if isinstance(data, dict) else data):
                drives.append({"path": f"\\\\.\\PhysicalDrive{d['DeviceId']}",
                               "name": d.get("FriendlyName", ""), "size": int(d.get("Size") or 0),
                               "kind": _kind(d.get("MediaType"), d.get("BusType"))})
            drives.sort(key=lambda d: d["path"])
        elif sys.platform == "darwin":
            lst = plistlib.loads(subprocess.run(["diskutil", "list", "-plist", "physical"],
                                                capture_output=True).stdout)
            for disk in lst.get("WholeDisks", []):
                info = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", disk],
                                                     capture_output=True).stdout)
                drives.append({"path": f"/dev/r{disk}", "name": info.get("MediaName", ""),
                               "size": int(info.get("TotalSize") or info.get("Size") or 0),
                               "kind": _kind("SSD" if info.get("SolidState") else "HDD",
                                             info.get("BusProtocol", ""))})
        else:
            out = subprocess.run(["lsblk", "-J", "-b", "-d", "-o", "NAME,SIZE,MODEL,ROTA,TRAN,TYPE"],
                                 capture_output=True, text=True).stdout
            for d in json.loads(out).get("blockdevices", []):
                if d.get("type") != "disk" or d["name"].startswith(("loop", "ram", "zram")):
                    continue
                rota = d.get("rota") in (True, "1", 1)
                drives.append({"path": f"/dev/{d['name']}", "name": (d.get("model") or "").strip(),
                               "size": int(d.get("size") or 0),
                               "kind": _kind("", d.get("tran") or "", rota)})
    except Exception as e:
        print(f"(Could not list drives automatically: {e})")
    return drives


def print_drives(drives):
    if not drives:
        print("No drives found (try running as Administrator / sudo).")
    for i, d in enumerate(drives, 1):
        print(f"  {i}) {d['path']:<22} {d['kind']:<9} {human(d['size']):>10}  {d['name']}")


u64le = lambda m, i: int.from_bytes(m[i:i + 8], "little")


def fs_type(src, off):
    boot = src[off:off + 512]
    if len(boot) < 512:
        return "?"
    if boot[3:11] == b"NTFS    ":
        return "NTFS"
    if boot[3:11] == b"EXFAT   ":
        return "exFAT"
    if boot[82:90] == b"FAT32   " or boot[54:62] in (b"FAT16   ", b"FAT12   "):
        return "FAT"
    return "?"


def find_partitions(src):
    """Return [(offset, size, label, fstype)] from GPT or MBR (or a bare volume)."""
    n = len(src)
    if fs_type(src, 0) != "?":
        return [(0, n, "volume", fs_type(src, 0))]
    mbr = src[0:512]
    if len(mbr) < 512 or mbr[510:512] != b"\x55\xaa":
        return []
    parts = []
    entries = [mbr[446 + 16 * i:462 + 16 * i] for i in range(4)]
    if any(e[4] == 0xEE for e in entries):                       # GPT
        for sec in (512, 4096):
            h = src[sec:sec + 92]
            if h[:8] == b"EFI PART":
                break
        else:
            return []
        lba, cnt, esz = u64le(h, 72), u32le(h, 80), u32le(h, 84)
        tbl = src[lba * sec:lba * sec + min(cnt, 256) * esz]
        for i in range(min(cnt, 256)):
            e = tbl[i * esz:(i + 1) * esz]
            if len(e) < 128 or e[:16] == b"\x00" * 16:
                continue
            first, last = u64le(e, 32), u64le(e, 40)
            name = e[56:128].decode("utf-16-le", "replace").rstrip("\x00")
            parts.append((first * sec, (last - first + 1) * sec, name or f"partition {i + 1}"))
    else:                                                        # MBR (+ extended)
        for idx, e in enumerate(entries):
            ptype, lba, cnt = e[4], u32le(e, 8), u32le(e, 12)
            if ptype == 0 or cnt == 0:
                continue
            if ptype in (0x05, 0x0F, 0x85):
                ebr, guard = lba, 0
                while guard < 128:
                    guard += 1
                    rec = src[ebr * 512:ebr * 512 + 512]
                    if len(rec) < 512 or rec[510:512] != b"\x55\xaa":
                        break
                    e1, e2 = rec[446:462], rec[462:478]
                    if e1[4]:
                        parts.append(((ebr + u32le(e1, 8)) * 512, u32le(e1, 12) * 512, "logical"))
                    if not e2[4]:
                        break
                    ebr = lba + u32le(e2, 8)
            else:
                parts.append((lba * 512, cnt * 512, f"partition {idx + 1}"))
    return [(o, sz, lab, fs_type(src, o)) for o, sz, lab in parts if o < n]


def _decode_runs(b):
    runs, i, lcn = [], 0, 0
    while i < len(b) and b[i] != 0:
        h = b[i]
        ls, os_ = h & 0xF, h >> 4
        i += 1
        if ls == 0 or i + ls + os_ > len(b):
            break
        length = int.from_bytes(b[i:i + ls], "little")
        i += ls
        if os_:
            lcn += int.from_bytes(b[i:i + os_], "little", signed=True)
            i += os_
            runs.append((lcn, length))
        else:
            runs.append((None, length))                         # sparse
    return runs


class NTFS:
    def __init__(self, src, off):
        self.src, self.off = src, off
        bs = src[off:off + 512]
        self.bps = u16le(bs, 0x0B)
        spc = bs[0x0D]
        spc = 1 << (256 - spc) if spc > 0x80 else spc
        self.cl = self.bps * spc
        self.clusters = u64le(bs, 0x28) // spc
        c = int.from_bytes(bs[0x40:0x41], "little", signed=True)
        self.rs = c * self.cl if c > 0 else 1 << (-c)
        mft_lcn = u64le(bs, 0x30)
        rec0 = self.fixup(src[off + mft_lcn * self.cl:off + mft_lcn * self.cl + self.rs])
        if rec0 is None:
            raise ValueError("MFT not readable")
        data = self.data_attr(self.attrs(rec0))
        if not data or not data[0]:
            raise ValueError("MFT has no data runs")
        self.mft_runs, self.mft_size = data[1], data[2]
        self.bitmap = None
        rec6 = self.fixup(self.read_runs(self.mft_runs, 6 * self.rs, self.rs))
        if rec6:
            d6 = self.data_attr(self.attrs(rec6))
            if d6 and d6[0]:
                self.bitmap = self.read_runs(d6[1], 0, d6[2])

    def fixup(self, rec):
        if not rec or rec[:4] != b"FILE":
            return None
        rec = bytearray(rec)
        uo, uc = u16le(rec, 4), u16le(rec, 6)
        if uc < 2:
            return rec
        stride = len(rec) // (uc - 1)
        for i in range(1, uc):
            p = i * stride - 2
            if p + 2 <= len(rec) and uo + 2 * i + 2 <= len(rec):
                rec[p:p + 2] = rec[uo + 2 * i:uo + 2 * i + 2]
        return rec

    @staticmethod
    def attrs(rec):
        out, off = [], u16le(rec, 0x14)
        while off + 16 <= len(rec):
            t = u32le(rec, off)
            if t == 0xFFFFFFFF:
                break
            L = u32le(rec, off + 4)
            if L < 16 or off + L > len(rec):
                break
            nonres, nlen, noff = rec[off + 8], rec[off + 9], u16le(rec, off + 10)
            name = rec[off + noff:off + noff + 2 * nlen].decode("utf-16-le", "replace") if nlen else ""
            if nonres == 0:
                clen, coff = u32le(rec, off + 16), u16le(rec, off + 20)
                out.append((t, name, False, bytes(rec[off + coff:off + coff + clen]), clen, 0))
            else:
                roff = u16le(rec, off + 32)
                out.append((t, name, True, _decode_runs(rec[off + roff:off + L]),
                            u64le(rec, off + 48), u64le(rec, off + 16)))
            off += L
        return out

    @staticmethod
    def data_attr(attrs):
        """(nonresident, runs_or_bytes, size) of the unnamed $DATA, or None."""
        for t, name, nonres, val, size, start_vcn in attrs:
            if t == 0x80 and name == "" and start_vcn == 0:
                return nonres, val, size
        return None

    def read_runs(self, runs, voff, length):
        out, v = bytearray(), 0
        end = voff + length
        for lcn, cnt in runs:
            rs, re_ = v * self.cl, (v + cnt) * self.cl
            v += cnt
            if re_ <= voff:
                continue
            if rs >= end:
                break
            a, b = max(rs, voff), min(re_, end)
            if lcn is None:
                out += b"\x00" * (b - a)
            else:
                p = self.off + lcn * self.cl + (a - rs)
                out += self.src[p:p + (b - a)]
        return bytes(out)

    def cluster_used(self, c):
        if self.bitmap is None or c // 8 >= len(self.bitmap):
            return False
        return bool(self.bitmap[c // 8] & (1 << (c % 8)))

    def free_regions(self):
        """Absolute byte ranges of unallocated clusters (where deleted data lives)."""
        if self.bitmap is None:
            return [(self.off, self.off + self.clusters * self.cl)]
        regs = []

        def add(a, b):                                   # cluster range [a, b)
            b = min(b, self.clusters)
            if a >= b:
                return
            if regs and regs[-1][1] == a:
                regs[-1][1] = b
            else:
                regs.append([a, b])

        for mt in re.finditer(rb"\x00+|[^\x00\xff]", self.bitmap):
            if self.bitmap[mt.start()] == 0:
                add(mt.start() * 8, mt.end() * 8)
            else:
                byte, c0 = self.bitmap[mt.start()], mt.start() * 8
                for bit in range(8):
                    if not byte & (1 << bit):
                        add(c0 + bit, c0 + bit + 1)
        return [(self.off + a * self.cl, self.off + b * self.cl) for a, b in regs]

    def records(self):
        total = self.mft_size // self.rs
        batch = 1024
        for first in range(0, total, batch):
            chunk = self.read_runs(self.mft_runs, first * self.rs, batch * self.rs)
            for k in range(len(chunk) // self.rs):
                yield first + k, chunk[k * self.rs:(k + 1) * self.rs]


def ntfs_recover(src, part_off, out_dir, cats, label=""):
    try:
        fs = NTFS(src, part_off)
    except Exception as e:
        print(f"  NTFS read failed: {e}")
        return None
    total = fs.mft_size // fs.rs
    print(f"Reading NTFS file table {label} ({total:,} records) ...")
    dirs, deleted = {}, []
    for recno, raw in fs.records():
        rec = fs.fixup(raw)
        if rec is None or u64le(rec, 0x20) & 0xFFFFFFFFFFFF:      # empty / extension record
            continue
        flags = u16le(rec, 0x16)
        attrs = fs.attrs(rec)
        fname, parent, best = None, 5, 9
        for t, _, nonres, val, _, _ in attrs:
            if t == 0x30 and not nonres and len(val) >= 0x42:
                ns, nl = val[0x41], val[0x40]
                rank = {1: 0, 3: 1, 0: 2, 2: 3}.get(ns, 4)          # prefer Win32 long names
                if rank < best:
                    best = rank
                    fname = val[0x42:0x42 + 2 * nl].decode("utf-16-le", "replace")
                    parent = u64le(val, 0) & 0xFFFFFFFFFFFF
        if fname is None:
            continue
        if flags & 0x02:
            dirs[recno] = (fname, parent, bool(flags & 0x01))
        elif not flags & 0x01:
            cat = category_of(fname)
            data = fs.data_attr(attrs)
            if cat in cats and data:
                deleted.append((recno, fname, parent, cat, data))
        if recno % 20000 == 0:
            print(f"\r  {recno * 100 // max(1, total):3d}%  deleted candidates: {len(deleted)}",
                  end="", flush=True)
    print(f"\r  100%  deleted candidates: {len(deleted)}          ")

    def path_of(parent):
        parts, guard = [], 0
        while parent != 5 and guard < 40:
            guard += 1
            if parent not in dirs:
                parts.append(f"_lost_folder_{parent}")
                break
            name, nxt, alive = dirs[parent]
            parts.append(name if alive else name + " (deleted folder)")
            parent = nxt
        return os.path.join(*reversed(parts)) if parts else ""

    ok = damaged = empty = 0
    base = os.path.join(out_dir, "Drive_Deleted" + (f"_{label}" if label else ""))
    for recno, fname, parent, cat, (nonres, val, size) in deleted:
        if nonres:
            runs = [(lcn, c) for lcn, c in val if lcn is None or lcn + c <= fs.clusters]
            if not runs or size == 0:
                continue
            head = fs.read_runs(runs, 0, min(size, MB))
            if not any(head):
                empty += 1                                 # zeroed (SSD TRIM) - nothing left
                continue
            reused = any(fs.cluster_used(l) or fs.cluster_used(l + c - 1) or fs.cluster_used(l + c // 2)
                         for l, c in runs if l is not None)
        else:
            if not val:
                continue
            reused = False
        rel = os.path.join("_Maybe_Damaged" if reused else "", cat, path_of(parent), fname)
        dest = unique_path(safe_join(base, rel))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as o:
            if nonres:
                for v in range(0, size, 16 * MB):
                    o.write(fs.read_runs(runs, v, min(16 * MB, size - v)))
            else:
                o.write(val)
        if reused:
            damaged += 1
        else:
            ok += 1
    print(f"Recovered with original names: {ok} good, {damaged} maybe damaged "
          f"(space reused), {empty} empty (erased by SSD TRIM).")
    print(f"Output: {base}")
    return fs


# ---------- safety: never save onto the drive being recovered ----------
def _disk_id(dev):
    d = dev.replace("/", "\\").lower()
    m = re.search(r"physicaldrive(\d+)", d)
    if m:
        return f"win{m.group(1)}"
    d = os.path.basename(dev).lower()
    m = re.match(r"r?(disk\d+)", d)
    if m:
        return m.group(1)
    m = re.match(r"(nvme\d+n\d+|mmcblk\d+|sd[a-z]+|vd[a-z]+|hd[a-z]+)", d)
    return m.group(1) if m else d


def disk_of_path(path):
    path = os.path.abspath(path)
    try:
        if os.name == "nt":
            letter = os.path.splitdrive(path)[0][:1]
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-Partition -DriveLetter {letter}).DiskNumber"],
                                 capture_output=True, text=True).stdout.strip()
            return f"win{int(out)}" if out.isdigit() else None
        if sys.platform == "darwin":
            info = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", path],
                                                 capture_output=True).stdout)
            return info.get("ParentWholeDisk")
        st = os.stat(path)
        real = os.path.realpath(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}")
        if os.path.exists(os.path.join(real, "partition")):
            real = os.path.dirname(real)
        return _disk_id(os.path.basename(real))
    except Exception:
        return None


def check_output_drive(device, out_dir, force=False):
    os.makedirs(out_dir, exist_ok=True)
    src_id = _disk_id(device)
    if os.name == "nt" and re.fullmatch(r"(\\\\\.\\)?[A-Za-z]:\\?", device):
        same = os.path.splitdrive(os.path.abspath(out_dir))[0][:1].lower() == device.replace("\\\\.\\", "")[:1].lower()
    else:
        same = disk_of_path(out_dir) == src_id
    if same:
        print("\n!!! WARNING: output folder is ON THE SAME DRIVE you are recovering.\n"
              "    Saving there will OVERWRITE the deleted files you want back.\n"
              "    Choose an output folder on another drive / USB disk (-o E:\\Recovered).")
        if not force:
            sys.exit(1)


def drive_recover(device, out_dir, cats, mode="quick", force=False):
    check_output_drive(device, out_dir, force)
    src = open_source(device)
    if len(src) == 0:
        sys.exit("Could not read drive size. Run as Administrator / sudo.")
    print(f"Drive: {device}  ({human(len(src))})")
    parts = find_partitions(src)
    if parts:
        print("Partitions:")
        for i, (o, sz, lab, fst) in enumerate(parts, 1):
            print(f"  {i}) {fst:<6} {human(sz):>10}  {lab}")
    else:
        print("No partition table found - the whole drive will be deep scanned.")
    regions = []
    for i, (o, sz, lab, fst) in enumerate(parts, 1):
        fs = None
        if fst == "NTFS" and mode in ("quick", "both"):
            fs = ntfs_recover(src, o, out_dir, cats, label=f"P{i}")
        if mode in ("deep", "both"):
            if fst == "NTFS":
                fs = fs or _try_ntfs(src, o)
            regions += fs.free_regions() if fs else [(o, o + sz)]
    if mode in ("deep", "both"):
        carve(device, out_dir, cats, regions=regions or None, src=src, label=device)
    elif not any(p[3] == "NTFS" for p in parts):
        print("No NTFS partition for quick scan. Use deep scan:  --mode deep")
    if getattr(src, "bad_sectors", 0):
        print(f"Note: {src.bad_sectors} unreadable sectors were skipped (drive may be failing).")
    src.close()
    print("\nSSD / NVMe note: if TRIM already erased the deleted blocks, no software can bring\n"
          "them back. HDDs and USB drives usually give much better results.")


def _try_ntfs(src, off):
    try:
        return NTFS(src, off)
    except Exception:
        return None


# ==========================================================================
# 4) Raw imaging of a drive / SD card
# ==========================================================================
def make_image(device, out_file):
    if os.path.abspath(device) == os.path.abspath(out_file):
        sys.exit("Output must be different from the device.")
    print(f"Imaging {device} -> {out_file}  (read-only on the source)")
    total, bad, BS = 0, 0, 4 * MB
    try:
        src = open(device, "rb", buffering=0)
    except PermissionError:
        sys.exit("Permission denied. Run as Administrator (Windows) or with sudo (Linux/Mac).")
    with src, open(out_file, "wb") as dst:
        while True:
            try:
                chunk = src.read(BS)
            except OSError:
                # unreadable area: write zeros and move on
                chunk = b"\x00" * BS
                bad += 1
                try:
                    src.seek(total + BS)
                except OSError:
                    break
            if not chunk:
                break
            dst.write(chunk)
            total += len(chunk)
            print(f"\r  {human(total)} copied", end="", flush=True)
    print(f"\nDone: {human(total)}" + (f", {bad} unreadable blocks filled with zeros" if bad else ""))
    print(f"Now run:  python phone_recovery.py carve \"{out_file}\"")


# ==========================================================================
# 6) WhatsApp
# ==========================================================================
def _wa():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import whatsapp
    return whatsapp


def whatsapp_menu(out):
    wa = _wa()
    print(" 1) Copy WhatsApp backup + media from Android phone (USB)")
    print(" 2) Open / read a backup (decrypt + chat viewer)")
    print(" 3) Only decrypt a .crypt14 / .crypt15 file")
    c = input("Choose 1-3: ").strip()
    if c == "1":
        adb = find_adb()
        wa.pull_android(os.path.join(out, "WhatsApp_Backup"), adb, android_pick_device(adb, None), adb_run)
        return
    path = input("Backup file (msgstore.db.crypt15 / msgstore.db / ChatStorage.sqlite)\n"
                 "  or a folder to search: ").strip().strip('"')
    if os.path.isdir(path):
        found = wa.newest_backup(path)
        if not found:
            sys.exit("No msgstore backup found in that folder.")
        print(f"Using: {found}")
        path = found
    key = None
    if re.search(r"\.crypt\d+$", path):
        key = input("64-digit key (or path to key / encrypted_backup.key file): ").strip()
    if c == "3":
        wa.decrypt_backup(path, key)
        return
    media = input("WhatsApp Media folder (Enter to skip): ").strip().strip('"')
    wa.build_viewer(path, os.path.join(out, "WhatsApp_Viewer"), [media] if media else [], key=key)


def _sc():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import snapchat
    return snapchat


def snapchat_menu(out):
    sc = _sc()
    print("First request your data: Snapchat > Settings > Privacy Controls > My Data")
    print("(tick Memories, Chat Media, Chat History and turn ON 'Export JSON Files').")
    print(" 1) Open my Snapchat export (chats + memories viewer)")
    print(" 2) Download ALL Memories (photos/videos) from the export links")
    c = input("Choose 1-2: ").strip()
    path = input("Export folder or mydata~....zip file: ").strip().strip('"')
    dest = os.path.join(out, "Snapchat")
    if c == "2":
        sc.download_memories(path, dest)
        sc.build_viewer(path, dest)
    else:
        dl = input("Also download all Memories now? (y/N): ").strip().lower() == "y"
        sc.build_viewer(path, dest, download=dl)


# ==========================================================================
# 7) Self-update from GitHub
# ==========================================================================
def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def _gh_token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(os.path.join(HERE, "github_token.txt")) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _gh_get(url, accept="application/vnd.github+json", timeout=20):
    import urllib.request
    headers = {"Accept": accept, "User-Agent": "PhoneDataRecovery/" + __version__}
    tok = _gh_token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_release():
    """(version, zip_api_url) of the newest published build, or raises."""
    data = json.loads(_gh_get(f"{os.environ.get('PDR_API_BASE', 'https://api.github.com')}/repos/{GITHUB_REPO}/releases/tags/{RELEASE_TAG}"))
    m = re.search(r"latest:\s*([\d.]+)", data.get("name") or "")
    version = m.group(1) if m else None
    zip_url = None
    for a in data.get("assets", []):
        if a.get("name") == "VERSION.txt" and not version:
            version = _gh_get(a["url"], "application/octet-stream").decode().strip()
        if a.get("name") == RELEASE_ZIP:
            zip_url = a["url"]
    if not version or not zip_url:
        raise ValueError("release has no version or zip")
    return version, zip_url


def check_for_update(quiet=False):
    try:
        remote, _ = latest_release()
    except Exception as e:
        if not quiet:
            import urllib.error
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                print("Could not read the update (no release yet, or the repository is private).\n"
                      "For a private repository put a read-only GitHub token in github_token.txt\n"
                      "next to this script (Settings > Developer settings > Fine-grained token,\n"
                      "Contents: Read-only).")
            else:
                print(f"Could not check for updates: {e}")
        return None
    return remote if _vtuple(remote) > _vtuple(__version__) else None


def self_update(force=False):
    import urllib.error
    print(f"Installed version: {__version__}")
    try:
        remote, zip_url = latest_release()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            check_for_update()                       # prints the token help
            return
        sys.exit(f"Update check failed: {e}")
    except Exception as e:
        sys.exit(f"Update check failed (internet?): {e}")
    print(f"Latest version:    {remote}")
    if _vtuple(remote) <= _vtuple(__version__) and not force:
        print("You already have the latest version.")
        return
    print("Downloading ...")
    blob = _gh_get(zip_url, "application/octet-stream", timeout=120)
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        sys.exit("Downloaded file is damaged - try again later.")
    new_files = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/").split("/")[-1]
        if not name or name.startswith(".") or name in ("github_token.txt",):
            continue
        new_files[name] = zf.read(info)
    if "phone_recovery.py" not in new_files:
        sys.exit("Update package is incomplete - nothing was changed.")
    for name, data in new_files.items():            # never install code that does not compile
        if name.endswith(".py"):
            try:
                compile(data, name, "exec")
            except SyntaxError as e:
                sys.exit(f"Update package has an error ({name}: {e}) - nothing was changed.")
    backup = os.path.join(HERE, "_backup", f"v{__version__}")
    os.makedirs(backup, exist_ok=True)
    for name, data in new_files.items():
        dest = os.path.join(HERE, name)
        if os.path.exists(dest):
            shutil.copy2(dest, os.path.join(backup, name))
        tmp = dest + ".new"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    print(f"Updated {__version__} -> {remote} ({len(new_files)} files). Old files saved in {backup}")
    print("Start the tool again to use the new version.")


# ==========================================================================
# Menu + CLI
# ==========================================================================
def menu():
    print("=" * 52)
    print(f"  Phone Data Recovery v{__version__}  (use only on your own devices)")
    print("=" * 52)
    newer = check_for_update(quiet=True)
    if newer:
        print(f"  ** New version {newer} available - choose 9 to update **")
    print(" 1) Android phone (USB)   - copy files + trash")
    print(" 2) iPhone                - from computer backup")
    print(" 3) SD card / drive image - recover DELETED files")
    print(" 4) Make image of SD card / drive")
    print(" 5) Make iPhone backup (needs libimobiledevice)")
    print(" 6) Hard drive / SSD / NVMe / USB - recover DELETED files")
    print(" 7) WhatsApp backup - decrypt and read your chats")
    print(" 8) Snapchat - read your data export, download all Memories")
    print(" 9) Check for updates")
    c = input("Choose 1-9: ").strip()
    if c == "9":
        self_update()
        if os.name == "nt":
            input("\nPress Enter to close...")
        return
    out = input("Output folder [Recovered]: ").strip() or "Recovered"
    cats = set(CATEGORIES)
    if c == "6":
        print("IMPORTANT: choose an output folder on a DIFFERENT drive (e.g. USB disk E:\\Recovered)")
        out = input("Output folder again (other drive): ").strip() or out
    if c == "1":
        android_recover(out, cats)
    elif c == "2":
        ios_recover(input("Backup folder (Enter = default location): ").strip() or None, out, cats)
    elif c == "3":
        carve(input("Image file path: ").strip().strip('"'), out, cats)
    elif c == "4":
        print(r"Examples: Windows \\.\PhysicalDrive2   Linux /dev/sdb   Mac /dev/rdisk4")
        make_image(input("Device: ").strip(), input("Save image as [sdcard.img]: ").strip() or "sdcard.img")
    elif c == "5":
        ios_make_backup(input("Backup folder [iphone_backup]: ").strip() or "iphone_backup")
    elif c == "6":
        drives = list_drives()
        print_drives(drives)
        pick = input("Drive number (or type a path / drive letter like D:): ").strip()
        dev = drives[int(pick) - 1]["path"] if pick.isdigit() and drives else pick
        print(" 1) Quick  - deleted files WITH original names (NTFS / Windows drives)")
        print(" 2) Deep   - scan every free sector for photos/videos/audio/docs (slow)")
        print(" 3) Both")
        mode = {"1": "quick", "2": "deep", "3": "both"}.get(input("Choose 1-3 [1]: ").strip() or "1", "quick")
        drive_recover(dev, out, cats, mode)
    elif c == "7":
        whatsapp_menu(out)
    elif c == "8":
        snapchat_menu(out)
    else:
        print("Invalid choice.")
    if os.name == "nt":
        input("\nPress Enter to close...")


def main():
    if len(sys.argv) == 1:
        return menu()
    ap = argparse.ArgumentParser(description="Recover photos, videos, audio and documents from phones.")
    ap.add_argument("--version", action="version", version=f"Phone Data Recovery {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    types_help = "comma list: Photos,Videos,Audio,Documents,Archives (default: all)"

    a = sub.add_parser("android", help="copy files (incl. trash) from Android via ADB")
    a.add_argument("-o", "--out", default="Recovered")
    a.add_argument("--types", help=types_help)
    a.add_argument("--serial")
    a.add_argument("--no-thumbnails", action="store_true")

    i = sub.add_parser("ios", help="extract files from an iPhone backup")
    i.add_argument("backup", nargs="?", help="backup folder (default: standard location)")
    i.add_argument("-o", "--out", default="Recovered")
    i.add_argument("--types", help=types_help)
    i.add_argument("--password", help="backup password if encrypted (asked if omitted)")

    b = sub.add_parser("ios-backup", help="make a full iPhone backup with idevicebackup2")
    b.add_argument("folder", nargs="?", default="iphone_backup")

    c = sub.add_parser("carve", help="recover deleted files from an SD card/drive image")
    c.add_argument("image")
    c.add_argument("-o", "--out", default="Recovered")
    c.add_argument("--types", help=types_help)

    im = sub.add_parser("image", help="raw-copy an SD card / drive into an image file")
    im.add_argument("device")
    im.add_argument("output")

    ai = sub.add_parser("android-image", help="image a block device of a ROOTED phone")
    ai.add_argument("output")
    ai.add_argument("--block", default="/dev/block/mmcblk1", help="default: external SD card")
    ai.add_argument("--serial")

    d = sub.add_parser("drive", help="recover deleted files from HDD / SSD / NVMe / USB")
    d.add_argument("device", nargs="?", help=r"e.g. \\.\PhysicalDrive1, D:, /dev/sdb, /dev/rdisk2")
    d.add_argument("--list", action="store_true", help="show connected drives")
    d.add_argument("--mode", choices=["quick", "deep", "both"], default="quick",
                   help="quick = NTFS deleted files with names; deep = signature scan of free space")
    d.add_argument("-o", "--out", default="Recovered")
    d.add_argument("--types", help=types_help)
    d.add_argument("--force", action="store_true", help="allow output on the same drive (NOT advised)")

    w = sub.add_parser("whatsapp", help="decrypt and view your WhatsApp backup")
    wsub = w.add_subparsers(dest="wcmd", required=True)
    wd = wsub.add_parser("decrypt", help=".crypt15/.crypt14/.crypt12 -> msgstore.db")
    wd.add_argument("file")
    wd.add_argument("--key", required=True, help="64-digit key, or key / encrypted_backup.key file")
    wd.add_argument("-o", "--out", help="output .db file")
    wv = wsub.add_parser("view", help="make an offline HTML chat viewer")
    wv.add_argument("file", help="msgstore.db(.crypt15) / ChatStorage.sqlite, or a folder to search")
    wv.add_argument("--key", help="needed for .crypt files")
    wv.add_argument("--media", action="append", default=[], help="WhatsApp Media folder (repeatable)")
    wv.add_argument("--wa-db", help="wa.db for contact names (Android, optional)")
    wv.add_argument("--copy-media", action="store_true", help="copy media into the viewer folder")
    wv.add_argument("-o", "--out", default=os.path.join("Recovered", "WhatsApp_Viewer"))
    wp = wsub.add_parser("pull", help="copy WhatsApp backups + media from Android (ADB)")
    wp.add_argument("-o", "--out", default=os.path.join("Recovered", "WhatsApp_Backup"))
    wp.add_argument("--serial")

    sc = sub.add_parser("snapchat", help="read your Snapchat data export / download Memories")
    scs = sc.add_subparsers(dest="scmd", required=True)
    sv = scs.add_parser("view", help="chats + memories viewer from the My Data export")
    sv.add_argument("export", help="export folder, or a mydata~....zip (other parts found automatically)")
    sv.add_argument("--download-memories", action="store_true")
    sv.add_argument("--workers", type=int, default=4)
    sv.add_argument("-o", "--out", default=os.path.join("Recovered", "Snapchat"))
    sm = scs.add_parser("memories", help="download all Memories listed in memories_history.json")
    sm.add_argument("export")
    sm.add_argument("--workers", type=int, default=4)
    sm.add_argument("--no-merge", action="store_true", help="keep text/sticker overlay as a separate file")
    sm.add_argument("-o", "--out", default=os.path.join("Recovered", "Snapchat"))

    up = sub.add_parser("update", help="download and install the newest version from GitHub")
    up.add_argument("--check", action="store_true", help="only check, do not install")
    up.add_argument("--force", action="store_true", help="reinstall even if up to date")

    args = ap.parse_args()
    if args.cmd == "android":
        android_recover(args.out, parse_cats(args.types), args.serial, not args.no_thumbnails)
    elif args.cmd == "ios":
        ios_recover(args.backup, args.out, parse_cats(args.types), args.password)
    elif args.cmd == "ios-backup":
        ios_make_backup(args.folder)
    elif args.cmd == "carve":
        carve(args.image, args.out, parse_cats(args.types))
    elif args.cmd == "image":
        make_image(args.device, args.output)
    elif args.cmd == "drive":
        if args.list or not args.device:
            print_drives(list_drives())
        else:
            drive_recover(args.device, args.out, parse_cats(args.types), args.mode, args.force)
    elif args.cmd == "whatsapp":
        wa = _wa()
        if args.wcmd == "decrypt":
            wa.decrypt_backup(args.file, args.key, args.out)
        elif args.wcmd == "view":
            f = args.file
            if os.path.isdir(f):
                f = wa.newest_backup(f) or sys.exit("No msgstore backup found in that folder.")
                print(f"Using: {f}")
            wa.build_viewer(f, args.out, args.media, args.wa_db, args.copy_media, args.key)
        elif args.wcmd == "pull":
            adb = find_adb()
            wa.pull_android(args.out, adb, android_pick_device(adb, args.serial), adb_run)
    elif args.cmd == "snapchat":
        snap = _sc()
        if args.scmd == "view":
            snap.build_viewer(args.export, args.out, args.download_memories, args.workers)
        else:
            snap.download_memories(args.export, args.out, args.workers, not args.no_merge)
            snap.build_viewer(args.export, args.out)
    elif args.cmd == "update":
        if args.check:
            newer = check_for_update()
            print(f"New version available: {newer}" if newer else f"Up to date ({__version__}).")
        else:
            self_update(args.force)
    elif args.cmd == "android-image":
        android_image(args.output, args.block, args.serial)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
