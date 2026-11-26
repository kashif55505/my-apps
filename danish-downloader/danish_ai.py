# -*- coding: utf-8 -*-
"""Danish Downloader - Gemini se video ke naye naam (AI Names)."""
import os
import re
import json
import time
import base64
import tempfile
import threading
import subprocess
import urllib.request
import urllib.error

DEFAULT_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_lock = threading.Lock()
_last_call = [0.0]
MIN_GAP = 4.5          # free tier: ~13 requests/minute se zyada nahi


def grab_frame(video_path, ffmpeg):
    """Video ke 1 second se ek chhoti tasveer (Gemini ko dikhane ke liye)."""
    if not ffmpeg:
        return None
    out = os.path.join(tempfile.gettempdir(), f"dd_frame_{os.getpid()}_{threading.get_ident()}.jpg")
    for ss in ("1", "0"):
        try:
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", ss, "-i", video_path, "-frames:v", "1",
                            "-vf", "scale=480:-2", "-q:v", "5", out],
                           capture_output=True, timeout=60, creationflags=NO_WINDOW)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                with open(out, "rb") as f:
                    data = f.read()
                os.remove(out)
                return data
        except Exception:
            pass
    return None


def _call(model, key, parts):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": 1.0, "maxOutputTokens": 1024}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    texts = []
    for c in data.get("candidates") or []:
        for p in (c.get("content") or {}).get("parts") or []:
            if p.get("text") and not p.get("thought"):
                texts.append(p["text"])
    return " ".join(texts).strip()


def make_title(settings, caption, frame_jpg, avoid, log=print):
    """Gemini se ek naya, alag naam. Na mile to None."""
    key = (settings.get("ai_key") or "").strip()
    if not key:
        return None
    lang = settings.get("ai_language") or "English"
    style = (settings.get("ai_style") or "").strip()
    avoid_txt = "\n".join(f"- {a}" for a in avoid[-40:]) or "(none)"
    prompt = (
        "You write titles for short videos that will be posted on a Facebook page.\n"
        f"Original caption (may be only hashtags): {caption or '(none)'}\n"
        "Look at the video frame and write ONE new, catchy, natural title that describes this video.\n"
        f"Rules: {lang} language. 4 to 10 words. NO hashtags. No quotes. No emojis. "
        "Do not copy the original caption. Must be clearly different from every title in this list:\n"
        f"{avoid_txt}\n"
        + (f"Extra style instruction: {style}\n" if style else "")
        + "Reply with the title only."
    )
    parts = [{"text": prompt}]
    if frame_jpg:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(frame_jpg).decode()}})
    models = [m.strip() for m in (settings.get("ai_model") or "").split(",") if m.strip()] or DEFAULT_MODELS
    last = None
    for model in models:
        for attempt in range(3):
            with _lock:
                wait = MIN_GAP - (time.time() - _last_call[0])
                if wait > 0:
                    time.sleep(wait)
                _last_call[0] = time.time()
            try:
                t = _call(model, key, parts)
                t = clean_title(t)
                if t:
                    return t
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "ignore")[:300]
                last = f"{model}: HTTP {e.code} {msg}"
                if e.code == 429 and "limit: 0" not in msg:
                    time.sleep(15 * (attempt + 1))      # zyada requests: thora ruko
                    continue
                break                                    # model nahi chala, agla try karo
            except Exception as e:
                last = f"{model}: {e}"
                time.sleep(3)
    if last:
        log(f"   ⚠ AI name failed ({last[:160]}) — using caption instead.")
    return None


def clean_title(t):
    t = (t or "").strip().splitlines()[0] if t and t.strip() else ""
    t = re.sub(r"#\S+", "", t)
    t = t.strip().strip('"\'“”‘’*` ').strip()
    t = re.sub(r"^(title|name)\s*:\s*", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def test_key(settings):
    t = make_title(settings, "#cat #funny", None, [], log=lambda m: None)
    return t
