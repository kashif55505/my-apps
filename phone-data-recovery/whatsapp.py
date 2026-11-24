"""
WhatsApp backup tools (for YOUR OWN WhatsApp data)
==================================================
  decrypt  msgstore.db.crypt15 / .crypt14 / .crypt12  ->  msgstore.db
  view     msgstore.db (Android) or ChatStorage.sqlite (iPhone)  ->  offline HTML chat viewer
  pull     copy WhatsApp backups + media from an Android phone over ADB
           (on rooted phones also the key file and the plain databases)

Keys:
  crypt15 (end-to-end encrypted backup): the 64-digit key you chose in
          WhatsApp > Settings > Chats > Chat backup > End-to-end encrypted backup,
          or the file /data/data/com.whatsapp/files/encrypted_backup.key (root).
  crypt14 / crypt12: the file /data/data/com.whatsapp/files/key (root).
A backup protected only with a PASSWORD cannot be opened offline (its key is kept
on WhatsApp's servers): in WhatsApp switch the E2E backup to a 64-digit key.
"""

import hashlib
import hmac
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import zlib
from datetime import datetime


# ==========================================================================
# Decryption
# ==========================================================================
def _need_crypto():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
    except ImportError:
        sys.exit("Decryption needs the 'cryptography' package. Install it with:\n"
                 "  pip install cryptography")


def _pb_fields(buf):
    """Minimal protobuf reader: {field_number: [values]} (bytes for length-delimited)."""
    out, i = {}, 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wtype = key >> 3, key & 7
        if wtype == 0:
            val, i = _varint(buf, i)
        elif wtype == 2:
            ln, i = _varint(buf, i)
            val, i = bytes(buf[i:i + ln]), i + ln
        elif wtype == 1:
            val, i = buf[i:i + 8], i + 8
        elif wtype == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError("bad protobuf")
        out.setdefault(field, []).append(val)
    return out


def _varint(buf, i):
    shift = result = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _hkdf_backup_key(root):
    """crypt15: AES key = HKDF-SHA256(root, salt=0*32, info='backup encryption')."""
    prk = hmac.new(b"\x00" * 32, root, hashlib.sha256).digest()
    return hmac.new(prk, b"backup encryption\x01", hashlib.sha256).digest()


def load_key(key_arg):
    """Return candidate 32-byte AES keys from a 64-digit hex key or a key file."""
    s = key_arg.strip().strip('"')
    hexs = re.sub(r"[\s\-:]", "", s)
    raws = []
    if re.fullmatch(r"[0-9a-fA-F]{64}", hexs):
        raws.append(bytes.fromhex(hexs))
    elif os.path.isfile(s):
        data = open(s, "rb").read()
        txt = re.sub(rb"[\s\-:]", b"", data)
        if re.fullmatch(rb"[0-9a-fA-F]{64}", txt):
            raws.append(bytes.fromhex(txt.decode()))
        elif len(data) >= 32:
            raws.append(data[-32:])            # Java-serialized byte[]: key is at the end
        else:
            sys.exit("Key file is too small / not a WhatsApp key.")
    else:
        sys.exit("Key must be the 64-digit key (hex) or a path to the 'key' / "
                 "'encrypted_backup.key' file.")
    keys = []
    for r in raws:
        keys += [_hkdf_backup_key(r), r]       # crypt15 uses HKDF, crypt14/12 the raw key
    return keys


def _header_candidates(data):
    cands = []
    try:                                       # crypt14 (new) / crypt15: protobuf header
        size, off = data[0], 1
        if data[1] == 1:
            off = 2
        pb = _pb_fields(data[off:off + size])
        start = off + size
        if 3 in pb:
            iv = _pb_fields(pb[3][0]).get(1, [b""])[0]
            if len(iv) == 16:
                cands.append(("crypt15", iv, start))
        if 2 in pb:
            iv = _pb_fields(pb[2][0]).get(5, [b""])[0]
            if len(iv) == 16:
                cands.append(("crypt14", iv, start))
    except (ValueError, IndexError):
        pass
    for st in (191, 190, 192, 189, 193):       # old crypt14 fixed layout
        cands.append(("crypt14-old", data[67:83], st))
    cands.append(("crypt12", data[51:67], 67))
    return cands


def _looks_right(key, iv, ct):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pt = Cipher(algorithms.AES(key), modes.GCM(iv)).decryptor().update(ct[:4096])
    if pt[:1] != b"\x78":
        return False
    try:
        head = zlib.decompressobj().decompress(pt, 64)
    except zlib.error:
        return False
    return head.startswith(b"SQLite format 3") or head.startswith(b"PK")


def decrypt_backup(enc_path, key_arg, out_path=None):
    _need_crypto()
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.exceptions import InvalidTag
    data = open(enc_path, "rb").read()
    if len(data) < 300:
        sys.exit("File is too small to be a WhatsApp backup.")
    keys = load_key(key_arg)
    for key in keys:
        for kind, iv, start in _header_candidates(data):
            if not _looks_right(key, iv, data[start:start + 4096]):
                continue
            verified, plain = False, None
            for tail in (32, 16, 36):          # tag+md5 | tag | tag+md5+jid (crypt12)
                try:
                    dec = Cipher(algorithms.AES(key), modes.GCM(iv, data[-tail:len(data) - tail + 16])).decryptor()
                    plain = dec.update(data[start:-tail]) + dec.finalize()
                    verified = True
                    break
                except (InvalidTag, ValueError):
                    plain = None
            if plain is None:                  # damaged file: decrypt without the check
                plain = Cipher(algorithms.AES(key), modes.GCM(iv)).decryptor().update(data[start:-16])
            z = zlib.decompressobj()
            try:
                db = z.decompress(plain)
            except zlib.error as e:
                db = None
                print(f"  Decompression error: {e}")
            if not db:
                continue
            if out_path is None:
                base = os.path.basename(enc_path)
                out_path = os.path.join(os.path.dirname(os.path.abspath(enc_path)),
                                        re.sub(r"\.crypt\d+$", "", base) or "msgstore.db")
                root, ext = os.path.splitext(out_path)
                n = 0
                while os.path.exists(out_path) or os.path.abspath(out_path) == os.path.abspath(enc_path):
                    n += 1
                    out_path = f"{root}_decrypted{'' if n == 1 else n}{ext or '.db'}"
            with open(out_path, "wb") as f:
                f.write(db)
            ext = "zip" if db[:2] == b"PK" else "db"
            print(f"Decrypted ({kind}{'' if verified else ', integrity NOT verified - file may be damaged'})"
                  f" -> {out_path}  [{ext}, {len(db) / 1048576:.1f} MB]")
            return out_path
    sys.exit("Could not decrypt: the key does not match this backup.\n"
             "  - crypt15 needs the 64-digit key of THIS backup (or encrypted_backup.key)\n"
             "  - crypt14/12 needs the 'key' file from the same phone/WhatsApp install\n"
             "  - password-only E2E backups cannot be opened offline")


# ==========================================================================
# Reading chats from the databases
# ==========================================================================
def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def _cols(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info('{table}')")}


def _pretty_jid(jid):
    if not jid:
        return "Unknown"
    user = jid.split("@")[0]
    return "+" + user if user.isdigit() else user


ANDROID_TYPES = {0: "text", 1: "image", 2: "audio", 3: "video", 4: "contact", 5: "location",
                 7: "system", 8: "audio", 9: "document", 13: "gif", 15: "deleted",
                 16: "location", 20: "sticker", 42: "image", 43: "video", 64: "deleted",
                 66: "poll", 82: "audio", 90: "call"}


def load_contacts(wa_db):
    names = {}
    if not wa_db or not os.path.isfile(wa_db):
        return names
    con = sqlite3.connect(f"file:{wa_db}?mode=ro", uri=True)
    try:
        cols = _cols(con, "wa_contacts")
        pick = [c for c in ("display_name", "wa_name", "given_name") if c in cols]
        for row in con.execute(f"SELECT jid, {', '.join(pick)} FROM wa_contacts"):
            name = next((v for v in row[1:] if v), None)
            if row[0] and name:
                names[row[0]] = name
    except sqlite3.Error:
        pass
    con.close()
    return names


def read_android(db, contacts):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    t = _tables(con)
    chats = {}
    name_of = lambda jid: contacts.get(jid) or _pretty_jid(jid)

    if "message" in t and "chat" in t and "jid" in t:              # modern schema (2022+)
        jids = {r["_id"]: r["raw_string"] for r in con.execute("SELECT _id, raw_string FROM jid")}
        for r in con.execute("SELECT _id, jid_row_id, subject FROM chat"):
            jid = jids.get(r["jid_row_id"], "")
            if jid.endswith("@broadcast") and jid != "status@broadcast":
                continue
            chats[r["_id"]] = {"jid": jid, "group": jid.endswith("@g.us"),
                               "name": r["subject"] or name_of(jid), "messages": []}
        media = {}
        if "message_media" in t:
            mc = _cols(con, "message_media")
            extra = ", media_name" if "media_name" in mc else ""
            for r in con.execute(f"SELECT message_row_id, file_path{extra} FROM message_media"):
                media[r[0]] = r[1] or (r[2] if extra else None)
        mcols = _cols(con, "message")
        sender_col = "sender_jid_row_id" if "sender_jid_row_id" in mcols else "NULL"
        q = (f"SELECT _id, chat_row_id, from_me, timestamp, message_type, text_data, "
             f"{sender_col} AS sender FROM message ORDER BY chat_row_id, timestamp, _id")
        for r in con.execute(q):
            chat = chats.get(r["chat_row_id"])
            if chat is None:
                continue
            mtype = ANDROID_TYPES.get(r["message_type"], "other")
            sender = "" if r["from_me"] else (name_of(jids.get(r["sender"], "")) if chat["group"] and r["sender"] else "")
            chat["messages"].append({"t": int((r["timestamp"] or 0) / 1000), "me": bool(r["from_me"]),
                                     "s": sender, "x": r["text_data"] or "", "k": mtype,
                                     "f": media.get(r["_id"])})
    elif "messages" in t:                                           # legacy schema
        subj = {}
        if "chat_list" in t:
            cc = _cols(con, "chat_list")
            if "subject" in cc:
                subj = {r[0]: r[1] for r in con.execute("SELECT key_remote_jid, subject FROM chat_list")}
        mc = _cols(con, "messages")
        cap = "media_caption" if "media_caption" in mc else "NULL"
        mname = "media_name" if "media_name" in mc else "NULL"
        for r in con.execute(f"SELECT key_remote_jid, key_from_me, data, timestamp, media_wa_type, "
                             f"{mname} AS mname, {cap} AS cap, remote_resource FROM messages "
                             f"ORDER BY key_remote_jid, timestamp"):
            jid = r["key_remote_jid"]
            if not jid or jid == "-1" or (jid.endswith("@broadcast") and jid != "status@broadcast"):
                continue
            chat = chats.setdefault(jid, {"jid": jid, "group": jid.endswith("@g.us"),
                                          "name": subj.get(jid) or name_of(jid), "messages": []})
            wt = int(r["media_wa_type"] or 0)
            chat["messages"].append({"t": int((r["timestamp"] or 0) / 1000), "me": bool(r["key_from_me"]),
                                     "s": "" if r["key_from_me"] or not chat["group"] else name_of(r["remote_resource"]),
                                     "x": r["data"] or r["cap"] or "", "k": ANDROID_TYPES.get(wt, "other"),
                                     "f": r["mname"] if wt else None})
    else:
        con.close()
        sys.exit("This does not look like a WhatsApp msgstore.db.")
    con.close()
    return [c for c in chats.values() if c["messages"]]


IOS_TYPES = {0: "text", 1: "image", 2: "video", 3: "audio", 4: "contact", 5: "location",
             6: "system", 7: "text", 8: "document", 10: "system", 11: "gif", 14: "deleted",
             15: "sticker", 46: "poll", 59: "call"}


def read_ios(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    t = _tables(con)
    if "ZWAMESSAGE" not in t:
        con.close()
        sys.exit("This does not look like a WhatsApp ChatStorage.sqlite.")
    chats = {}
    for r in con.execute("SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME FROM ZWACHATSESSION"):
        jid = r["ZCONTACTJID"] or ""
        chats[r["Z_PK"]] = {"jid": jid, "group": jid.endswith("@g.us"),
                            "name": r["ZPARTNERNAME"] or _pretty_jid(jid), "messages": []}
    members = {}
    if "ZWAGROUPMEMBER" in t:
        gm = _cols(con, "ZWAGROUPMEMBER")
        cname = "ZCONTACTNAME" if "ZCONTACTNAME" in gm else "NULL"
        for r in con.execute(f"SELECT Z_PK, ZMEMBERJID, {cname} AS n FROM ZWAGROUPMEMBER"):
            members[r["Z_PK"]] = r["n"] or _pretty_jid(r["ZMEMBERJID"])
    media = {}
    if "ZWAMEDIAITEM" in t:
        for r in con.execute("SELECT Z_PK, ZMEDIALOCALPATH FROM ZWAMEDIAITEM"):
            media[r[0]] = r[1]
    mc = _cols(con, "ZWAMESSAGE")
    gcol = "ZGROUPMEMBER" if "ZGROUPMEMBER" in mc else "NULL"
    for r in con.execute(f"SELECT ZCHATSESSION, ZISFROMME, ZTEXT, ZMESSAGEDATE, ZMESSAGETYPE, ZMEDIAITEM, "
                         f"{gcol} AS gm FROM ZWAMESSAGE ORDER BY ZCHATSESSION, ZMESSAGEDATE"):
        chat = chats.get(r["ZCHATSESSION"])
        if chat is None:
            continue
        chat["messages"].append({"t": int((r["ZMESSAGEDATE"] or 0) + 978307200), "me": bool(r["ZISFROMME"]),
                                 "s": "" if r["ZISFROMME"] or not chat["group"] else members.get(r["gm"], ""),
                                 "x": r["ZTEXT"] or "", "k": IOS_TYPES.get(r["ZMESSAGETYPE"], "other"),
                                 "f": media.get(r["ZMEDIAITEM"])})
    con.close()
    return [c for c in chats.values() if c["messages"]]


# ==========================================================================
# HTML viewer
# ==========================================================================
def _index_media(folders):
    idx = {}
    for root_dir in folders:
        for root, _, files in os.walk(root_dir):
            for f in files:
                idx.setdefault(f.lower(), os.path.join(root, f))
    return idx


def build_viewer(db, out_dir, media_dirs=(), wa_db=None, copy_media=False, key=None):
    if re.search(r"\.crypt\d+$", db):
        if not key:
            sys.exit("This backup is encrypted. Give the key with --key (64-digit key or key file).")
        db = decrypt_backup(db, key)
    with open(db, "rb") as f:
        if f.read(15) != b"SQLite format 3":
            sys.exit("Not an SQLite database.")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    is_ios = "ZWAMESSAGE" in _tables(con)
    con.close()
    if not is_ios and not wa_db:
        guess = os.path.join(os.path.dirname(os.path.abspath(db)), "wa.db")
        wa_db = guess if os.path.isfile(guess) else None
    chats = read_ios(db) if is_ios else read_android(db, load_contacts(wa_db))
    chats.sort(key=lambda c: c["messages"][-1]["t"], reverse=True)
    total = sum(len(c["messages"]) for c in chats)
    print(f"{'iPhone' if is_ios else 'Android'} database: {len(chats)} chats, {total:,} messages")

    os.makedirs(out_dir, exist_ok=True)
    media_dirs = [d for d in media_dirs if d and os.path.isdir(d)]
    idx = _index_media(media_dirs) if media_dirs else {}
    found = missing = 0
    for c in chats:
        for m in c["messages"]:
            f = m.get("f")
            if not f:
                m.pop("f", None)
                continue
            path = idx.get(os.path.basename(f.replace("\\", "/")).lower())
            if not path:
                m["f"] = None
                m["n"] = os.path.basename(f)
                missing += 1
                continue
            m["f"] = link_media(path, out_dir, copy_media)
            m["n"] = os.path.basename(path)
            found += 1
    if found or missing:
        print(f"Media linked: {found}, not found: {missing}"
              + ("" if media_dirs else "  (tip: add --media <WhatsApp Media folder>)"))

    return write_viewer(chats, out_dir, "WhatsApp backup")


def link_media(path, out_dir, copy_media=False):
    """Return a URL for path usable from out_dir/index.html."""
    if copy_media:
        dest = os.path.join(out_dir, "media", os.path.basename(path))
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(path, dest)
        path = dest
    try:
        return os.path.relpath(path, out_dir).replace("\\", "/")
    except ValueError:                                # other drive on Windows
        return "file:///" + os.path.abspath(path).replace("\\", "/")


def write_viewer(chats, out_dir, header, extra_link=None):
    """chats: [{name, group, messages:[{t, me, s, x, k, f?, n?}]}] -> index.html + chats_txt/"""
    os.makedirs(out_dir, exist_ok=True)
    txt_dir = os.path.join(out_dir, "chats_txt")
    os.makedirs(txt_dir, exist_ok=True)
    for c in chats:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", c["name"])[:80] or "chat"
        with open(os.path.join(txt_dir, f"{name}.txt"), "w", encoding="utf-8") as f:
            for m in c["messages"]:
                who = "Me" if m["me"] else (m["s"] or c["name"])
                body = m["x"] or ((f"<{m['k']}: {m['n']}>" if m.get("n") else f"<{m['k']}>") if m["k"] != "text" else "")
                f.write(f"{datetime.fromtimestamp(m['t']).strftime('%d/%m/%Y %H:%M')} - {who}: {body}\n")

    data = json.dumps(chats, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    link = (f'<a href="{html.escape(extra_link[0])}" style="float:right;font-size:13px;color:var(--acc)">'
            f'{html.escape(extra_link[1])}</a>') if extra_link else ""
    page = (VIEWER_HTML.replace("__HEADER__", html.escape(header) + link)
            .replace("__TITLE__", html.escape(f"{header} - {len(chats)} chats"))
            .replace("__DATA__", data))
    out_html = os.path.join(out_dir, "index.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Viewer: {out_html}   (open it in any browser - works offline)")
    print(f"Text copies of every chat: {txt_dir}")
    return out_html


VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0b141a;--panel:#111b21;--line:#222d34;--text:#e9edef;--muted:#8696a0;--me:#005c4b;--them:#202c33;--acc:#00a884}
@media (prefers-color-scheme: light){:root{--bg:#efeae2;--panel:#fff;--line:#e9edef;--text:#111b21;--muted:#667781;--me:#d9fdd3;--them:#fff;--acc:#008069}}
*{box-sizing:border-box}body{margin:0;font:14.5px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;overflow:hidden}
#side{width:340px;min-width:260px;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column}
#side header{padding:14px 16px;font-weight:600;font-size:17px}
input{width:calc(100% - 24px);margin:0 12px 10px;padding:9px 12px;border-radius:8px;border:0;background:var(--line);color:var(--text);font:inherit}
#list{overflow-y:auto;flex:1}
.chat{padding:11px 16px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;gap:12px;align-items:center}
.chat:hover,.chat.on{background:var(--line)}
.av{width:42px;height:42px;border-radius:50%;background:var(--acc);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600;flex:none}
.ci{min-width:0;flex:1}.cn{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cp{color:var(--muted);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cd{color:var(--muted);font-size:12px;flex:none}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
#top{padding:12px 16px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center;min-height:56px}
#top b{flex:1}#back{display:none;background:none;border:0;color:var(--text);font-size:22px;cursor:pointer}
#msgs{flex:1;overflow-y:auto;padding:14px 6% 24px}
.day{text-align:center;margin:14px 0}.day span{background:var(--them);color:var(--muted);font-size:12px;padding:5px 10px;border-radius:7px}
.m{max-width:min(75%,560px);margin:3px 0;padding:6px 9px 4px;border-radius:8px;background:var(--them);clear:both;float:left;word-wrap:break-word;white-space:pre-wrap}
.m.me{background:var(--me);float:right}.wrap::after{content:"";display:block;clear:both}
.s{font-size:12.5px;font-weight:600;color:var(--acc);margin-bottom:2px}
.tm{font-size:11px;color:var(--muted);text-align:right;margin-top:2px}
.m img,.m video{max-width:100%;max-height:340px;border-radius:6px;display:block}.m audio{width:260px;max-width:100%}
.sys{text-align:center;color:var(--muted);font-size:12.5px;margin:8px auto;clear:both;font-style:italic}
.tag{color:var(--muted);font-style:italic}
.more{display:block;margin:6px auto 12px;padding:7px 14px;border-radius:16px;border:0;background:var(--them);color:var(--text);cursor:pointer}
mark{background:#ffd27a;color:#000}
#empty{margin:auto;color:var(--muted);text-align:center;padding:20px}
@media (max-width:720px){#side{width:100%}#main{display:none}body.open #side{display:none}body.open #main{display:flex}#back{display:block}#msgs{padding:10px 3%}}
</style></head><body>
<aside id="side"><header>__HEADER__</header>
<input id="q" placeholder="Search chats and messages..." autocomplete="off"><div id="list"></div></aside>
<section id="main"><div id="top"><button id="back" aria-label="Back">&#8592;</button><b id="title">Select a chat</b><span id="count" class="cd"></span></div>
<div id="msgs"><div id="empty">Choose a chat on the left.<br>Everything stays on this computer.</div></div></section>
<script>
const CHATS=__DATA__;
const $=s=>document.querySelector(s),esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtD=t=>new Date(t*1000).toLocaleDateString(undefined,{day:"numeric",month:"short",year:"numeric"});
const fmtT=t=>new Date(t*1000).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"});
const ICON={image:"📷 Photo",video:"🎥 Video",audio:"🎤 Voice / audio",document:"📄 Document",sticker:"Sticker",gif:"GIF",contact:"👤 Contact",location:"📍 Location",deleted:"🚫 This message was deleted",poll:"📊 Poll",call:"📞 Call",snap:"👻 Snap",share:"🔗 Shared",other:"Message"};
let cur=null,shown=0,query="";
function preview(m){return m.x||ICON[m.k]||""}
function hl(t){if(!query)return esc(t);const i=t.toLowerCase().indexOf(query);if(i<0)return esc(t);return esc(t.slice(0,i))+"<mark>"+esc(t.slice(i,i+query.length))+"</mark>"+esc(t.slice(i+query.length))}
function renderList(){
  const L=$("#list");L.innerHTML="";
  CHATS.forEach((c,i)=>{
    let hits=0;
    if(query){if(!c.name.toLowerCase().includes(query)){hits=c.messages.filter(m=>m.x&&m.x.toLowerCase().includes(query)).length;if(!hits)return}}
    const last=c.messages[c.messages.length-1],d=document.createElement("div");
    d.className="chat"+(i===cur?" on":"");
    d.innerHTML=`<div class="av">${esc((c.name.replace(/[^\p{L}\p{N}]/gu,"")[0]||"#").toUpperCase())}</div><div class="ci"><div class="cn">${esc(c.name)}</div><div class="cp">${hits?hits+" matching messages":esc(preview(last))}</div></div><div class="cd">${fmtD(last.t)}</div>`;
    d.onclick=()=>openChat(i);L.appendChild(d);
  });
  if(!L.children.length)L.innerHTML='<div id="empty">No matches</div>';
}
function media(m){
  if(!m.f)return m.k!=="text"?`<div class="tag">${ICON[m.k]||""}${m.n?" ("+esc(m.n)+" - file not found)":""}</div>`:"";
  const f=encodeURI(m.f).replace(/#/g,"%23"),ext=m.f.split(".").pop().toLowerCase();
  if(["jpg","jpeg","png","gif","webp","heic"].includes(ext)||m.k==="image"||m.k==="sticker")return `<a href="${f}" target="_blank"><img loading="lazy" src="${f}" alt=""></a>`;
  if(["mp4","3gp","mov","webm","mkv"].includes(ext)||m.k==="video"||m.k==="gif")return `<video controls preload="none" src="${f}"></video>`;
  if(["opus","ogg","mp3","m4a","aac","amr","wav"].includes(ext)||m.k==="audio")return `<audio controls preload="none" src="${f}"></audio><div><a href="${f}" download>${esc(m.n||"audio")}</a></div>`;
  return `<a href="${f}" target="_blank">📄 ${esc(m.n||"file")}</a>`;
}
function bubble(c,m){
  if(m.k==="system"||m.k==="call")return `<div class="sys">${m.x?hl(m.x):ICON[m.k]}</div>`;
  const body=m.k==="deleted"?`<span class="tag">${ICON.deleted}</span>`:media(m)+(m.x?`<div>${hl(m.x)}</div>`:"");
  return `<div class="m${m.me?" me":""}">${c.group&&!m.me&&m.s?`<div class="s">${esc(m.s)}</div>`:""}${body}<div class="tm">${fmtT(m.t)}</div></div>`;
}
function renderMsgs(keepPos){
  const c=CHATS[cur],box=$("#msgs"),start=Math.max(0,c.messages.length-shown),old=box.scrollHeight-box.scrollTop;
  let h=start>0?`<button class="more" id="more">Load earlier messages (${start})</button>`:"",lastDay="";
  for(let i=start;i<c.messages.length;i++){const m=c.messages[i],d=fmtD(m.t);if(d!==lastDay){h+=`<div class="day"><span>${d}</span></div>`;lastDay=d}h+=`<div class="wrap">${bubble(c,m)}</div>`}
  box.innerHTML=h;
  const mb=$("#more");if(mb)mb.onclick=()=>{shown+=500;renderMsgs(true)};
  box.scrollTop=keepPos?box.scrollHeight-old:box.scrollHeight;
}
function openChat(i){
  cur=i;const c=CHATS[i];shown=500;
  if(query){const idx=c.messages.findIndex(m=>m.x&&m.x.toLowerCase().includes(query));if(idx>=0)shown=Math.max(500,c.messages.length-idx+20)}
  $("#title").textContent=c.name;$("#count").textContent=c.messages.length.toLocaleString()+" messages";
  document.body.classList.add("open");renderMsgs();renderList();
  if(query){const mk=document.querySelector("#msgs mark");if(mk)mk.scrollIntoView({block:"center"})}
}
$("#q").oninput=e=>{query=e.target.value.trim().toLowerCase();renderList()};
$("#back").onclick=()=>document.body.classList.remove("open");
renderList();
</script></body></html>
"""


# ==========================================================================
# Pull from Android phone (ADB)
# ==========================================================================
WA_PKGS = [("com.whatsapp", "WhatsApp"), ("com.whatsapp.w4b", "WhatsApp Business")]


def pull_android(out_dir, adb, serial, adb_run):
    got_any = False
    for pkg, label in WA_PKGS:
        dest = os.path.join(out_dir, label.replace(" ", "_"))
        for base in (f"/sdcard/Android/media/{pkg}/{label}", f"/sdcard/{label}"):
            check = adb_run(adb, ["shell", f"ls -d '{base}/Databases' 2>/dev/null"], serial).stdout.strip()
            if not check:
                continue
            for sub in ("Databases", "Backups", "Media"):
                print(f"Copying {label}/{sub} ...")
                os.makedirs(dest, exist_ok=True)
                adb_run(adb, ["pull", "-a", f"{base}/{sub}", dest], serial)
            got_any = True
            break
        # rooted phone: key file + plain databases
        if "uid=0" in adb_run(adb, ["shell", "su -c id"], serial).stdout.decode(errors="replace"):
            for remote, name in ((f"/data/data/{pkg}/files/key", "key"),
                                 (f"/data/data/{pkg}/files/encrypted_backup.key", "encrypted_backup.key"),
                                 (f"/data/data/{pkg}/databases/msgstore.db", "msgstore.db"),
                                 (f"/data/data/{pkg}/databases/wa.db", "wa.db")):
                r = subprocess.run([adb, "-s", serial, "exec-out", f"su -c 'cat {remote}'"], capture_output=True)
                if r.returncode == 0 and r.stdout and b"No such file" not in r.stdout[:200]:
                    os.makedirs(dest, exist_ok=True)
                    with open(os.path.join(dest, name), "wb") as f:
                        f.write(r.stdout)
                    print(f"  (root) saved {name}")
                    got_any = True
    if not got_any:
        sys.exit("No WhatsApp folders found on the phone.")
    print(f"Done: {out_dir}")
    for root, _, files in os.walk(out_dir):
        for f in sorted(files):
            if re.match(r"msgstore.*\.crypt\d+$", f) or f == "msgstore.db":
                print("  database:", os.path.join(root, f))
    print("Next:  python phone_recovery.py whatsapp view <msgstore file> --key <64-digit key or key file>"
          " --media <WhatsApp/Media folder>")


def newest_backup(folder):
    best = None
    for root, _, files in os.walk(folder):
        for f in files:
            if f == "msgstore.db" or re.match(r"msgstore(-\d{4}-\d{2}-\d{2}\.\d+)?\.db\.crypt\d+$", f):
                p = os.path.join(root, f)
                if f == "msgstore.db":
                    return p
                if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    return best


if __name__ == "__main__":
    print("Run through phone_recovery.py:  python phone_recovery.py whatsapp -h")
