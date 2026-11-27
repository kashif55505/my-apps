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
    return False, "This PC is not activated yet.", {"mid": mid}


def valid_mid(s):
    return bool(re.fullmatch(r"DD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}", (s or "").strip().upper()))
