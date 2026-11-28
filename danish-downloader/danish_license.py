# -*- coding: utf-8 -*-
"""Danish Downloader - license system.

Har PC ka ek Machine ID hota hai. Manzoor PCs ki list GitHub par licenses.json mein hai:
  {"machines": {"DD-XXXX-XXXX-XXXX": {"name": "Ali", "expires": "2027-01-31", "active": true, "added": "..."}}}
Owner (Kashif) apne GitHub token se ye list software ke andar se badal sakta hai.
"""
import os
import re
import json
import time
import base64
import hashlib
import datetime
import urllib.request
import urllib.error
import email.utils

OWNER = "kashif55505"
REPO = "my-apps"
PATH = "danish-downloader/licenses.json"
API = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}"
RAW = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/main/{PATH}"
OFFLINE_GRACE_DAYS = 3
PUBLIC_KEY = "yi3nDte8v3AqBAY5l8E944bXk1g26z05HTIDMD8XyZ0="   # owner ka public key (sirf check ke liye)
UA = {"User-Agent": "DanishDownloader", "Accept": "application/vnd.github+json"}


# ----------------------------------------------------------------- machine id
def machine_id():
    raw = ""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                           winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0))
        raw = winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        import uuid
        raw = str(uuid.getnode())
    h = hashlib.sha256(("danish-dl:" + raw).encode()).hexdigest().upper()
    return f"DD-{h[0:4]}-{h[4:8]}-{h[8:12]}"


# ----------------------------------------------------------------- license keys (signed)
import ed25519_min as _ed


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _ub64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_owner_secret(text):
    t = (text or "").strip()
    if not t.startswith("DDOWNER-"):
        return None
    try:
        seed = _ub64u(t[8:])
    except Exception:
        return None
    if len(seed) != 32 or _ed.public_key(seed) != base64.b64decode(PUBLIC_KEY):
        return None
    return seed


def make_key(secret_text, mid, name, days):
    """Student ke liye key. days=0 -> lifetime."""
    seed = parse_owner_secret(secret_text)
    if not seed:
        raise ValueError("Owner Secret is missing or wrong.")
    mid = mid.strip().upper()
    exp = (datetime.date.today() + datetime.timedelta(days=int(days))).isoformat() if int(days) > 0 else "0"
    payload = f"{mid}|{exp}|{name.strip().replace('|', ' ')[:40]}".encode("utf-8")
    key = "DDK-" + _b64u(payload + _ed.sign(seed, payload))
    return key, key_id(key), ("" if exp == "0" else exp)


def key_id(key):
    return hashlib.sha256((key or "").strip().encode()).hexdigest()[:12].upper()


def parse_key(key):
    """(info dict ya None, wajah)."""
    k = re.sub(r"\s+", "", key or "")
    if not k.startswith("DDK-"):
        return None, "This is not a Danish Downloader license key."
    try:
        raw = _ub64u(k[4:])
    except Exception:
        return None, "Key is broken (copy it again)."
    payload, sig = raw[:-64], raw[-64:]
    if not _ed.verify(base64.b64decode(PUBLIC_KEY), payload, sig):
        return None, "Key is not valid (copy it again)."
    try:
        mid, exp, name = payload.decode("utf-8").split("|", 2)
    except ValueError:
        return None, "Key is broken."
    return {"mid": mid, "expires": "" if exp == "0" else exp, "name": name, "id": key_id(k), "key": k}, ""


def key_state(info, today, revoked):
    if info["id"] in (revoked or []):
        return "cancelled"
    if info["expires"]:
        try:
            if today > datetime.date.fromisoformat(info["expires"]):
                return "expired"
        except ValueError:
            pass
    return "ok"


# ----------------------------------------------------------------- server time + list
def _server_date(resp):
    try:
        d = email.utils.parsedate_to_datetime(resp.headers.get("Date"))
        return d.date()
    except Exception:
        return datetime.date.today()


def fetch_list(token=None):
    """(data, sha, server_date). Pehle API (taaza), phir raw."""
    hdr = dict(UA)
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(API + f"?ref=main&t={int(time.time())}", headers=hdr)
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode())
            data = json.loads(base64.b64decode(j["content"]).decode("utf-8") or "{}")
            return data, j.get("sha"), _server_date(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"machines": {}}, None, datetime.date.today()
        last = e
    except Exception as e:
        last = e
    try:
        req = urllib.request.Request(RAW + f"?t={int(time.time())}", headers={"User-Agent": "DanishDownloader"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8") or "{}"), None, _server_date(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"machines": {}}, None, datetime.date.today()
        raise
    except Exception:
        raise last


def save_list(token, data, sha, message):
    body = {"message": message, "branch": "main",
            "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="PUT",
                                 headers={**UA, "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["content"]["sha"]


def check_owner_token(token):
    """Token ko repo par likhne ki ijazat hai? (True, '') ya (False, wajah)."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{OWNER}/{REPO}",
                                     headers={**UA, "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode())
        if (j.get("permissions") or {}).get("push"):
            return True, ""
        return False, "This token cannot write to the repository."
    except urllib.error.HTTPError as e:
        return False, f"GitHub said {e.code} — token wrong or expired."
    except Exception as e:
        return False, f"Could not reach GitHub: {e}"


# ----------------------------------------------------------------- status
def entry_state(entry, today):
    """'ok' | 'disabled' | 'expired'."""
    if not entry or not entry.get("active", True):
        return "disabled"
    exp = (entry.get("expires") or "").strip()
    if exp:
        try:
            if today > datetime.date.fromisoformat(exp):
                return "expired"
        except ValueError:
            pass
    return "ok"


def check(settings):
    """Return (allowed: bool, message: str, info: dict). Owner PC hamesha allowed."""
    mid = machine_id()
    if settings.get("owner_token") and settings.get("owner_machine") == mid:
        return True, "Owner", {"owner": True, "mid": mid}
    kinfo = None
    if settings.get("license_key"):
        kinfo, _ = parse_key(settings["license_key"])
        if kinfo and kinfo["mid"] != mid:
            kinfo = None
    try:
        data, _, today = fetch_list()
    except Exception as e:
        last = settings.get("lic_ok_until") or ""
        if last:
            try:
                if datetime.date.today() <= datetime.date.fromisoformat(last):
                    return True, "Offline (license cached)", {"mid": mid}
            except ValueError:
                pass
        return False, f"Cannot check license (no internet?): {e}", {"mid": mid}
    if mid in (data.get("owners") or []):
        settings["lic_ok_until"] = (today + datetime.timedelta(days=30)).isoformat()
        return True, "Owner", {"owner": True, "mid": mid}
    if kinfo:
        ks = key_state(kinfo, today, data.get("revoked"))
        if ks == "ok":
            grace = today + datetime.timedelta(days=OFFLINE_GRACE_DAYS)
            if kinfo["expires"]:
                grace = min(grace, datetime.date.fromisoformat(kinfo["expires"]))
            settings["lic_ok_until"] = grace.isoformat()
            return True, f"Licensed to {kinfo['name']}" + (f" (until {kinfo['expires']})" if kinfo["expires"]
                                                          else " (lifetime)"), {"mid": mid, **kinfo}
        settings["lic_ok_until"] = ""
        return False, ("License cancelled." if ks == "cancelled" else f"License expired on {kinfo['expires']}."), {"mid": mid}
    entry = (data.get("machines") or {}).get(mid)
    st = entry_state(entry, today)
    if st == "ok":
        grace = today + datetime.timedelta(days=OFFLINE_GRACE_DAYS)
        exp = (entry.get("expires") or "").strip()
        if exp:
            try:
                grace = min(grace, datetime.date.fromisoformat(exp))
            except ValueError:
                pass
        settings["lic_ok_until"] = grace.isoformat()
        return True, f"Licensed to {entry.get('name', '')}" + (f" (until {exp})" if exp else ""), {"mid": mid, **entry}
    settings["lic_ok_until"] = ""
    if st == "expired":
        return False, f"License expired on {entry.get('expires')}.", {"mid": mid}
    if entry:
        return False, "License cancelled.", {"mid": mid}
    return False, "This PC is not activated yet. Paste your license key below.", {"mid": mid}


def valid_mid(s):
    return bool(re.fullmatch(r"DD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}", (s or "").strip().upper()))
