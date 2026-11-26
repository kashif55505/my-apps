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
AUTO_LANG = "Auto (Spanish creator → Spanish, others → English)"
MODEL_CHOICES = ["Auto (Flash)", "gemini-pro-latest", "gemini-2.5-pro", "gemini-flash-latest", "gemini-2.5-flash"]
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


def _models(settings):
    m = (settings.get("ai_model") or "").strip()
    if not m or m.startswith("Auto"):
        return DEFAULT_MODELS
    return [x.strip() for x in m.split(",") if x.strip()] + DEFAULT_MODELS


def _gap(settings):
    return 0.0 if settings.get("ai_paid") else MIN_GAP


def _ask(settings, parts, log, what):
    key = (settings.get("ai_key") or "").strip()
    last = None
    for model in _models(settings):
        for attempt in range(3):
            gap = _gap(settings)
            if gap:
                with _lock:
                    wait = gap - (time.time() - _last_call[0])
                    if wait > 0:
                        time.sleep(wait)
                    _last_call[0] = time.time()
            try:
                t = _call(model, key, parts)
                if t:
                    return t
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "ignore")[:300]
                last = f"{model}: HTTP {e.code} {msg}"
                if e.code in (429, 500, 503) and "limit: 0" not in msg:
                    time.sleep(5 * (attempt + 1))
                    continue
                break
            except Exception as e:
                last = f"{model}: {e}"
                time.sleep(2)
    if last:
        log(f"   ⚠ AI {what} failed ({last[:160]})")
    return None


def build_profile(settings, handle, bio, captions, frames, log=print):
    """Creator ko samjho: bio + kuch videos ki tasveerein + naam -> chhota sa description."""
    caps = "\n".join(f"- {c}" for c in captions[:20]) or "(none)"
    prompt = (
        "You are studying a short-video creator before writing titles for their videos.\n"
        f"Creator handle: {handle or '(unknown)'}\n"
        f"Creator bio: {bio or '(not available)'}\n"
        f"Some of their video captions:\n{caps}\n"
        f"{len(frames)} frames from their videos are attached.\n"
        "In 4-6 short lines describe: what kind of content this is (niche), what typically happens in the videos, "
        "the tone, the audience, and what makes a good title for THIS creator. Be specific. No hashtags.\n"
        "Then add a LAST line exactly like 'LANGUAGE: Spanish' or 'LANGUAGE: English' etc. — the main language "
        "the creator speaks/writes in (judge from bio and captions)."
    )
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f).decode()}}
                                  for f in frames if f]
    t = _ask(settings, parts, log, "creator profile")
    return (t or "").strip()


def make_title(settings, caption, frame_jpg, avoid, log=print, profile=""):
    """Gemini se ek naya, alag naam. Na mile to None."""
    if not (settings.get("ai_key") or "").strip():
        return None
    lang = pick_language(settings, profile)
    style = (settings.get("ai_style") or "").strip()
    avoid_txt = "\n".join(f"- {a}" for a in avoid[-40:]) or "(none)"
    prompt = (
        "You write titles for short videos that will be posted on a Facebook page.\n"
        + (f"About this creator (use it to understand the content):\n{profile}\n" if profile else "")
        + f"Original caption (may be only hashtags): {caption or '(none)'}\n"
        "Look at the video frame and write ONE new, natural title that describes what happens in THIS video, "
        "in the style that fits this creator.\n"
        f"Rules: {lang} language. SHORT: 3 to 8 words, one line, under 60 characters. "
        "NO hashtags. No @mentions. No quotes. No emojis. "
        "Do not copy the original caption. Must be clearly different from every title in this list:\n"
        f"{avoid_txt}\n"
        + (f"Extra style instruction: {style}\n" if style else "")
        + "Reply with the title only."
    )
    parts = [{"text": prompt}]
    if frame_jpg:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(frame_jpg).decode()}})
    t = _ask(settings, parts, log, "name")
    t = clean_title(t)
    if not t:
        log("   ⚠ AI name failed — using caption instead.")
    return t or None


def profile_language(profile):
    m = re.search(r"LANGUAGE\s*:\s*([A-Za-z ]+)", profile or "", re.I)
    return m.group(1).strip().title() if m else ""


def pick_language(settings, profile):
    lang = settings.get("ai_language") or AUTO_LANG
    if lang.startswith("Auto"):
        return "Spanish" if profile_language(profile).startswith("Spanish") else "English"
    return lang


def short(t, max_len=60):
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 25 else cut).rstrip(" ,.-–:;")


def clean_title(t):
    t = (t or "").strip().splitlines()[0] if t and t.strip() else ""
    t = re.sub(r"#\S+", "", t)
    t = re.sub(r"^\s*\**\s*(title|name)\s*:\s*", "", t, flags=re.I)
    t = t.strip().strip('"\'“”‘’*` ').strip()
    t = re.sub(r"@\S+", "", t)
    return short(re.sub(r"\s+", " ", t).strip())


def test_key(settings):
    t = make_title(settings, "#cat #funny", None, [], log=lambda m: None)
    return t
