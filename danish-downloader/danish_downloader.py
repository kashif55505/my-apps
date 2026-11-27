# -*- coding: utf-8 -*-
"""Danish Downloader - Windows desktop app (GUI)."""
import os
import sys
import json
import time
import queue
import shutil
import subprocess
import threading
import datetime
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk

import danish_core as core
import danish_license as lic

# ---- colors (theme: Light green)
BG = "#F3FAF6"
PANEL = "#FFFFFF"
PANEL2 = "#ECF7F1"        # input fields
SEC = "#D1F0DF"           # secondary buttons
SEC_H = "#B7E4CB"
SEC_TEXT = "#065F46"
ACCENT = "#047857"
ACCENT_H = "#065F46"
PINK = "#0EA5E9"          # highlight action (sky blue)
PINK_H = "#0284C7"
GREEN = "#10B981"
GREEN_H = "#059669"
RED = "#DC2626"
RED_H = "#B91C1C"
TEXT = "#0F1F17"
MUTED = "#5B7A6A"
HEAD = "#065F46"
BORDER = "#D1F0DF"
FONT = "Segoe UI"

APP_UPDATE_EVERY = 6 * 3600      # check GitHub for a new app version every 6 hours
YTDLP_UPDATE_EVERY = 24 * 3600   # update yt-dlp once a day
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ctk.set_appearance_mode("light")
ICON = os.path.join(core.BASE_DIR, "danish.ico")
try:   # taskbar par apna logo (Python ka nahi)
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Danish.Downloader")
except Exception:
    pass


def btn(parent, text, cmd, color=ACCENT, hover=ACCENT_H, width=150):
    tc = SEC_TEXT if color == SEC else "#FFFFFF"
    return ctk.CTkButton(parent, text=text, command=cmd, fg_color=color, hover_color=hover, text_color=tc,
                         corner_radius=10, height=38, width=width, font=(FONT, 13, "bold"))


class SourceDialog(ctk.CTkToplevel):
    def __init__(self, app, src=None):
        super().__init__(app)
        self.app, self.src = app, src
        self.title("Edit Creator" if src else "Add Creator")
        self.geometry("640x800")
        self.configure(fg_color=PANEL)
        self.resizable(False, True)
        self.transient(app)
        self.after(100, self.grab_set)

        f = ctk.CTkScrollableFrame(self, fg_color=PANEL)
        f.pack(fill="both", expand=True, padx=18, pady=(18, 6))

        def label(t, sub=None):
            ctk.CTkLabel(f, text=t, font=(FONT, 13, "bold"), text_color=TEXT, anchor="w").pack(fill="x", pady=(10, 0))
            if sub:
                ctk.CTkLabel(f, text=sub, font=(FONT, 11), text_color=MUTED, anchor="w",
                             justify="left", wraplength=560).pack(fill="x")

        def entry(val=""):
            e = ctk.CTkEntry(f, height=36, fg_color=PANEL2, border_color=BORDER, font=(FONT, 12))
            e.pack(fill="x", pady=(4, 0))
            if val != "":
                e.insert(0, str(val))
            return e

        s = src or {}
        label("Creator Profile Link", "TikTok / YouTube / Facebook profile, e.g. https://www.tiktok.com/@danish")
        self.e_url = entry(s.get("url", ""))

        label("Save Folder", "The Facebook page folder. Type a new name and it will be created.")
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self.e_folder = ctk.CTkEntry(row, height=36, fg_color=PANEL2, border_color=BORDER, font=(FONT, 12))
        self.e_folder.pack(side="left", fill="x", expand=True)
        if s.get("folder"):
            self.e_folder.insert(0, s["folder"])
        btn(row, "Browse", self.browse, width=90).pack(side="left", padx=(8, 0))

        label("Videos Per Day")
        self.e_day = entry(s.get("per_day", 3))
        label("Stock Days", "e.g. 3 videos × 15 days = keep 45 videos in the folder.")
        self.e_days = entry(s.get("stock_days", 15))
        label("Refill When Days Left", "e.g. 3 = refill to full when only 9 videos are left.")
        self.e_refill = entry(s.get("refill_days", 3))

        label("Start Link (optional)", "The video you STARTED downloading from. Newer videos above it are downloaded first.")
        self.e_start = entry(s.get("start_link", ""))
        label("Last Downloaded Link (optional)",
              "The video you downloaded UP TO. Everything between the two links is skipped; "
              "older videos below it come next. Leave both empty for a new creator.")
        self.e_end = entry(s.get("end_link", ""))
        label("Any Video Link (TikTok fix, optional)",
              "Any one video of this creator. Used only if TikTok refuses the profile list.")
        self.e_sample = entry(s.get("sample_link", ""))
        if src:
            ctk.CTkLabel(f, text=f"Downloaded so far: {len(s.get('done_ids', []))} videos",
                         font=(FONT, 12), text_color=GREEN, anchor="w").pack(fill="x", pady=(10, 0))

        label("Already Downloaded Links (optional)",
              "Paste old links (e.g. from Link Grabber) — these will never be downloaded again.")
        self.t_links = ctk.CTkTextbox(f, height=90, fg_color=PANEL2, border_color=BORDER, border_width=2, font=(FONT, 11))
        self.t_links.pack(fill="x", pady=(4, 0))

        self.v_enabled = tk.BooleanVar(value=s.get("enabled", True))
        ctk.CTkSwitch(f, text="Include in Auto Check", variable=self.v_enabled, progress_color=GREEN,
                      font=(FONT, 12)).pack(anchor="w", pady=(12, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=12)
        btn(bar, "Save", self.save, color=GREEN, hover=GREEN_H, width=110).pack(side="right")
        btn(bar, "Cancel", self.destroy, color=SEC, hover=SEC_H, width=100).pack(side="right", padx=8)

    def browse(self):
        d = filedialog.askdirectory(parent=self, title="Select the Facebook page folder")
        if d:
            self.e_folder.delete(0, "end")
            self.e_folder.insert(0, os.path.normpath(d))

    def save(self):
        url = self.e_url.get().strip()
        folder = self.e_folder.get().strip()
        if not url.startswith("http"):
            return messagebox.showerror(core.APP_NAME, "Enter a valid profile link (starting with http).", parent=self)
        if not folder:
            return messagebox.showerror(core.APP_NAME, "Select or type a folder.", parent=self)
        if not os.path.isabs(folder):
            root = self.app.store.settings.get("root_folder") or os.path.join(os.path.expanduser("~"), "Videos", "Danish Downloader")
            folder = os.path.join(root, folder)
        try:
            per_day = int(self.e_day.get())
            days = int(self.e_days.get())
            refill = int(self.e_refill.get())
            assert per_day > 0 and days > 0 and refill >= 0
        except Exception:
            return messagebox.showerror(core.APP_NAME, "Please enter numbers only in the number fields.", parent=self)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            return messagebox.showerror(core.APP_NAME, f"Could not create folder:\n{e}", parent=self)
        start = self.e_start.get().strip()
        end = self.e_end.get().strip()
        sample = self.e_sample.get().strip()
        for l in (start, end, sample):
            if l and not l.startswith("http"):
                return messagebox.showerror(core.APP_NAME, "Start/Last link must start with http.", parent=self)

        st = self.app.store
        if self.src:
            with st.lock:
                if url != self.src.get("url"):
                    self.src["tiktok_secuid"] = ""
                if (start, end) != (self.src.get("start_link", ""), self.src.get("end_link", "")) and (start or end):
                    self.src["initialized"] = False   # new links: recalculate on next check
                self.src.update(url=url, folder=folder, per_day=per_day, stock_days=days, refill_days=refill,
                                start_link=start, end_link=end, sample_link=sample,
                                enabled=self.v_enabled.get())
            st.save()
            src = self.src
        else:
            src = st.add_source(url=url, folder=folder, per_day=per_day, stock_days=days, refill_days=refill,
                                start_link=start, end_link=end, sample_link=sample,
                                enabled=self.v_enabled.get())
        n = st.add_old_links(src, self.t_links.get("1.0", "end"))
        if n:
            self.app.log(f"{os.path.basename(folder)}: {n} old links marked as downloaded.")
        self.app.refresh()
        self.destroy()


class LinksDialog(ctk.CTkToplevel):
    """Like 4K Downloader: paste links, download all."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Download from Links")
        self.geometry("640x520")
        self.configure(fg_color=PANEL)
        self.transient(app)
        self.after(100, self.grab_set)
        ctk.CTkLabel(self, text="Paste links (one per line)", font=(FONT, 14, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(18, 0))
        ctk.CTkLabel(self, text="TikTok, YouTube, Instagram reels, Facebook, Snapchat Spotlight — all in best quality.",
                     font=(FONT, 11), text_color=MUTED).pack(anchor="w", padx=18)
        self.t = ctk.CTkTextbox(self, fg_color=PANEL2, border_color=BORDER, border_width=2, font=(FONT, 11))
        self.t.pack(fill="both", expand=True, padx=18, pady=8)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18)
        self.e_folder = ctk.CTkEntry(row, height=36, fg_color=PANEL2, border_color=BORDER,
                                     placeholder_text="Save to folder (click Browse)")
        self.e_folder.pack(side="left", fill="x", expand=True)
        btn(row, "Browse", self.browse, width=90).pack(side="left", padx=(8, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=12)
        btn(bar, "⬇  Start Download", self.go, color=PINK, hover=PINK_H, width=180).pack(side="right")

    def browse(self):
        d = filedialog.askdirectory(parent=self)
        if d:
            self.e_folder.delete(0, "end")
            self.e_folder.insert(0, os.path.normpath(d))

    def go(self):
        links = [l.strip() for l in self.t.get("1.0", "end").split() if l.strip().startswith("http")]
        folder = self.e_folder.get().strip()
        if not links:
            return messagebox.showerror(core.APP_NAME, "No links found.", parent=self)
        if not folder:
            return messagebox.showerror(core.APP_NAME, "Please select a folder.", parent=self)
        self.app.engine.run_async(links=links, links_folder=folder)
        self.destroy()


class AIDialog(ctk.CTkToplevel):
    """Gemini se naye naam (hashtags ke baghair)."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        s = app.store.settings
        self.title("AI Names")
        self.geometry("640x760")
        self.configure(fg_color=PANEL)
        self.transient(app)
        self.after(100, self.grab_set)
        f = ctk.CTkScrollableFrame(self, fg_color=PANEL)
        f.pack(fill="both", expand=True, padx=18, pady=14)

        def label(t, sub=None, parent=None):
            parent = parent or f
            ctk.CTkLabel(parent, text=t, font=(FONT, 13, "bold"), text_color=TEXT, anchor="w").pack(fill="x", pady=(10, 0))
            if sub:
                ctk.CTkLabel(parent, text=sub, font=(FONT, 11), text_color=MUTED, anchor="w",
                             justify="left", wraplength=540).pack(fill="x")

        self.v_on = tk.BooleanVar(value=s.get("ai_names", False))
        ctk.CTkSwitch(f, text="Use AI to name videos (no hashtags, every name different)", variable=self.v_on,
                      progress_color=GREEN, font=(FONT, 13, "bold")).pack(anchor="w")
        ctk.CTkLabel(f, text="Only videos whose caption is just hashtags (or empty) get an AI name. The AI first studies "
                     "the creator (bio, a few videos and their captions), then names each video.", font=(FONT, 11),
                     text_color=MUTED, anchor="w", justify="left", wraplength=540).pack(fill="x", pady=(4, 0))
        label("AI Provider")
        self.o_prov = ctk.CTkSegmentedButton(f, values=["Claude", "Gemini"], command=self._show,
                                             selected_color=GREEN, selected_hover_color=GREEN_H, font=(FONT, 13, "bold"))
        self.o_prov.set(core.danish_ai.provider(s))
        self.o_prov.pack(anchor="w", pady=(4, 0))

        # --- Claude
        self.fc = ctk.CTkFrame(f, fg_color=BG, corner_radius=12)
        label("Claude API Key", "platform.claude.com → API Keys → Create Key → copy (starts with sk-ant-).", self.fc)
        row = ctk.CTkFrame(self.fc, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0), padx=2)
        self.e_ckey = ctk.CTkEntry(row, height=36, fg_color=PANEL, border_color=BORDER, show="•")
        self.e_ckey.pack(side="left", fill="x", expand=True)
        self.e_ckey.insert(0, s.get("claude_key", ""))
        btn(row, "Get Key", lambda: __import__("webbrowser").open("https://platform.claude.com/settings/keys"),
            color=SEC, hover=SEC_H, width=90).pack(side="left", padx=(8, 0))
        label("Claude Model", "Haiku = fast and cheap (recommended). Sonnet/Opus = smarter, costs more.", self.fc)
        self.o_cmodel = ctk.CTkComboBox(self.fc, values=core.danish_ai.CLAUDE_MODELS, width=260, fg_color=PANEL,
                                        border_color=BORDER, button_color=ACCENT, button_hover_color=ACCENT_H)
        self.o_cmodel.set(s.get("claude_model") or core.danish_ai.CLAUDE_MODELS[0])
        self.o_cmodel.pack(anchor="w", pady=(4, 12))

        # --- Gemini
        self.fg = ctk.CTkFrame(f, fg_color=BG, corner_radius=12)
        label("Gemini API Key", "aistudio.google.com/apikey → Create API key → copy and paste here.", self.fg)
        row = ctk.CTkFrame(self.fg, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0), padx=2)
        self.e_key = ctk.CTkEntry(row, height=36, fg_color=PANEL, border_color=BORDER, show="•")
        self.e_key.pack(side="left", fill="x", expand=True)
        self.e_key.insert(0, s.get("ai_key", ""))
        btn(row, "Get Key", lambda: __import__("webbrowser").open("https://aistudio.google.com/apikey"),
            color=SEC, hover=SEC_H, width=90).pack(side="left", padx=(8, 0))
        self.v_paid = tk.BooleanVar(value=s.get("ai_paid", False))
        ctk.CTkSwitch(self.fg, text="Paid key (fast — no waiting, many names at once)", variable=self.v_paid,
                      progress_color=GREEN, font=(FONT, 12)).pack(anchor="w", pady=(8, 0))
        label("Gemini Model", "Auto uses Gemini Flash. Choose a Pro model if your key has it.", self.fg)
        self.o_model = ctk.CTkComboBox(self.fg, values=core.danish_ai.MODEL_CHOICES, width=260, fg_color=PANEL,
                                       border_color=BORDER, button_color=ACCENT, button_hover_color=ACCENT_H)
        self.o_model.set(s.get("ai_model") or "Auto (Flash)")
        self.o_model.pack(anchor="w", pady=(4, 12))

        self.anchor = ctk.CTkFrame(f, fg_color="transparent", height=1)
        self.anchor.pack(fill="x")
        label("Name Language")
        self.o_lang = ctk.CTkOptionMenu(f, values=[core.danish_ai.AUTO_LANG, "English", "Roman Urdu", "Urdu", "Hindi",
                                                   "Spanish", "Portuguese", "Arabic", "Same as original caption"],
                                        fg_color=SEC, button_color=ACCENT, button_hover_color=ACCENT_H, text_color=SEC_TEXT)
        self.o_lang.set(s.get("ai_language") or core.danish_ai.AUTO_LANG)
        self.o_lang.pack(anchor="w", pady=(4, 0))
        label("Extra instruction (optional)", "Leave empty — the AI decides the style from the creator.")
        self.e_style = ctk.CTkEntry(f, height=36, fg_color=PANEL2, border_color=BORDER)
        self.e_style.pack(fill="x", pady=(4, 0))
        self.e_style.insert(0, s.get("ai_style", ""))
        self.l_test = ctk.CTkLabel(f, text="", font=(FONT, 12), text_color=ACCENT, anchor="w", wraplength=540, justify="left")
        self.l_test.pack(fill="x", pady=(10, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=12)
        btn(bar, "Save", self.save, color=GREEN, hover=GREEN_H, width=110).pack(side="right")
        btn(bar, "Test Key", self.test, color=SEC, hover=SEC_H, width=110).pack(side="right", padx=8)
        self._show(self.o_prov.get())

    def _show(self, prov):
        self.fc.pack_forget()
        self.fg.pack_forget()
        (self.fc if prov == "Claude" else self.fg).pack(fill="x", pady=(10, 0), before=self.anchor, ipadx=10)

    def _collect(self):
        return {"ai_names": bool(self.v_on.get()), "ai_provider": self.o_prov.get(),
                "claude_key": self.e_ckey.get().strip(), "claude_model": self.o_cmodel.get().strip(),
                "ai_key": self.e_key.get().strip(),
                "ai_language": self.o_lang.get(), "ai_style": self.e_style.get().strip(),
                "ai_model": self.o_model.get().strip(), "ai_paid": bool(self.v_paid.get())}

    def test(self):
        cfg = dict(self.app.store.settings)
        cfg.update(self._collect())
        if not core.danish_ai.has_key(cfg):
            return self.l_test.configure(text="Paste your API key first.", text_color=RED)
        self.l_test.configure(text="Testing...", text_color=ACCENT)

        def work():
            try:
                t = core.danish_ai.test_key(cfg)
                msg, col = (f"✓ Working! Example name: {t}", GREEN_H) if t else ("✗ Key not working (see Activity Log).", RED)
            except Exception as e:
                msg, col = (f"✗ {e}", RED)
            self.after(0, lambda: self.l_test.configure(text=msg, text_color=col))
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        self.app.store.settings.update(self._collect())
        self.app.store.save()
        on = self.app.store.settings["ai_names"]
        st = self.app.store.settings
        self.app.log(f"AI Names: {'ON' if on else 'OFF'} — {core.danish_ai.provider(st)} ({st['ai_language']})")
        self.app.b_ai.configure(text="✨ AI Names: ON" if on else "✨ AI Names")
        self.destroy()


class LockWindow(ctk.CTkToplevel):
    """License na ho to ye screen: Machine ID dikhao, owner login."""
    def __init__(self, app, message):
        super().__init__(app)
        self.app = app
        self.title("Activate Danish Downloader")
        self.geometry("560x470")
        self.resizable(False, False)
        self.configure(fg_color=PANEL)
        self.protocol("WM_DELETE_WINDOW", app.destroy)
        self.transient(app)
        self.after(100, self.grab_set)
        self.mid = lic.machine_id()
        f = ctk.CTkFrame(self, fg_color=PANEL)
        f.pack(fill="both", expand=True, padx=26, pady=20)
        ctk.CTkLabel(f, text="🔒  Activation needed", font=(FONT, 22, "bold"), text_color=TEXT).pack(anchor="w")
        self.l_msg = ctk.CTkLabel(f, text=message, font=(FONT, 13), text_color=RED, anchor="w", justify="left",
                                  wraplength=500)
        self.l_msg.pack(fill="x", pady=(6, 14))
        ctk.CTkLabel(f, text="Your Machine ID — send it to your teacher (WhatsApp):", font=(FONT, 13),
                     text_color=MUTED, anchor="w").pack(fill="x")
        row = ctk.CTkFrame(f, fg_color=SEC, corner_radius=12)
        row.pack(fill="x", pady=(6, 4))
        ctk.CTkLabel(row, text=self.mid, font=("Consolas", 24, "bold"), text_color=SEC_TEXT).pack(side="left", padx=16, pady=14)
        btn(row, "Copy", self.copy, width=80).pack(side="right", padx=12)
        ctk.CTkLabel(f, text="After your teacher activates it, this window unlocks by itself (checks every 90 seconds).",
                     font=(FONT, 11), text_color=MUTED, anchor="w", justify="left", wraplength=500).pack(fill="x", pady=(4, 14))
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.pack(fill="x")
        btn(bar, "⟳  Check now", self.check_now, color=GREEN, hover=GREEN_H, width=140).pack(side="left")
        btn(bar, "Owner login", self.owner, color=SEC, hover=SEC_H, width=120).pack(side="right")
        self.after(90000, self.auto)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.mid)
        self.l_msg.configure(text="Machine ID copied ✓  Paste it in WhatsApp to your teacher.", text_color=GREEN_H)

    def auto(self):
        if self.winfo_exists():
            self.check_now()
            self.after(90000, self.auto)

    def check_now(self):
        self.l_msg.configure(text="Checking...", text_color=ACCENT)
        threading.Thread(target=lambda: self.app.license_check(from_lock=True), daemon=True).start()

    def owner(self):
        d = ctk.CTkInputDialog(text="Owner only: paste your GitHub token", title="Owner login")
        tok = (d.get_input() or "").strip()
        if not tok:
            return
        ok, why = lic.check_owner_token(tok)
        if not ok:
            return self.l_msg.configure(text=why, text_color=RED)
        s = self.app.store.settings
        s["owner_token"], s["owner_machine"] = tok, self.mid
        self.app.store.save()
        self.app.unlock("Owner")


class LicensesDialog(ctk.CTkToplevel):
    """Owner panel: students add / cancel / extend."""
    DURS = ["1 month", "3 months", "6 months", "1 year", "Never expires"]

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.tok = app.store.settings.get("owner_token", "")
        self.title("Licenses (students)")
        self.geometry("900x560")
        self.configure(fg_color=PANEL)
        self.transient(app)
        self.after(100, self.grab_set)
        add = ctk.CTkFrame(self, fg_color=BG, corner_radius=12)
        add.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(add, text="Add student:", font=(FONT, 13, "bold"), text_color=TEXT).pack(side="left", padx=(12, 8), pady=12)
        self.e_name = ctk.CTkEntry(add, width=170, placeholder_text="Student name", fg_color=PANEL, border_color=BORDER)
        self.e_name.pack(side="left")
        self.e_mid = ctk.CTkEntry(add, width=210, placeholder_text="DD-XXXX-XXXX-XXXX", fg_color=PANEL, border_color=BORDER)
        self.e_mid.pack(side="left", padx=8)
        self.o_dur = ctk.CTkOptionMenu(add, values=self.DURS, width=140, fg_color=SEC, button_color=ACCENT,
                                       button_hover_color=ACCENT_H, text_color=SEC_TEXT)
        self.o_dur.set("1 month")
        self.o_dur.pack(side="left")
        btn(add, "+ Activate", self.add, color=GREEN, hover=GREEN_H, width=110).pack(side="left", padx=10)

        tf = ctk.CTkFrame(self, fg_color=PANEL)
        tf.pack(fill="both", expand=True, padx=16)
        cols = ("name", "mid", "expires", "status", "added")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", style="D.Treeview")
        for c, t, w in (("name", "Student", 180), ("mid", "Machine ID", 200), ("expires", "Expires", 120),
                        ("status", "Status", 110), ("added", "Added", 120)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c == "name" else "center")
        self.tree.tag_configure("bad", foreground=RED)
        self.tree.pack(fill="both", expand=True)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=12)
        btn(bar, "+1 Month", lambda: self.change("extend"), width=110).pack(side="left")
        btn(bar, "Enable", lambda: self.change("enable"), color=GREEN, hover=GREEN_H, width=100).pack(side="left", padx=6)
        btn(bar, "Cancel License", lambda: self.change("disable"), color=RED, hover=RED_H, width=140).pack(side="left")
        btn(bar, "Delete", lambda: self.change("delete"), color=SEC, hover=SEC_H, width=90).pack(side="left", padx=6)
        btn(bar, "⟳ Refresh", self.load, color=SEC, hover=SEC_H, width=100).pack(side="right")
        self.l = ctk.CTkLabel(self, text="", font=(FONT, 12), text_color=MUTED)
        self.l.pack(anchor="w", padx=18, pady=(0, 10))
        self.load()

    def _bg(self, fn, done_msg):
        self.l.configure(text="Working...", text_color=ACCENT)

        def work():
            try:
                fn()
                msg, col = done_msg, GREEN_H
            except Exception as e:
                msg, col = f"✗ {e}", RED
            self.after(0, lambda: (self.l.configure(text=msg, text_color=col), self.load(quiet=True)))
        threading.Thread(target=work, daemon=True).start()

    def load(self, quiet=False):
        def work():
            try:
                data, _, today = lic.fetch_list(self.tok)
                rows = []
                for mid, e in sorted((data.get("machines") or {}).items(), key=lambda x: x[1].get("name", "")):
                    st = lic.entry_state(e, today)
                    rows.append((mid, (e.get("name", ""), mid, e.get("expires") or "never",
                                       {"ok": "Active", "expired": "Expired", "disabled": "Cancelled"}[st],
                                       e.get("added", "")), st != "ok"))
                self.after(0, lambda: self._fill(rows, quiet))
            except Exception as e:
                self.after(0, lambda: self.l.configure(text=f"✗ {e}", text_color=RED))
        threading.Thread(target=work, daemon=True).start()

    def _fill(self, rows, quiet):
        self.tree.delete(*self.tree.get_children())
        for mid, vals, bad in rows:
            self.tree.insert("", "end", iid=mid, values=vals, tags=("bad",) if bad else ())
        if not quiet:
            self.l.configure(text=f"{len(rows)} students. Changes reach the student's PC within a few minutes.",
                             text_color=MUTED)

    @staticmethod
    def _until(dur, start=None):
        start = start or datetime.date.today()
        months = {"1 month": 1, "3 months": 3, "6 months": 6, "1 year": 12}.get(dur)
        if not months:
            return ""
        y, m = start.year + (start.month - 1 + months) // 12, (start.month - 1 + months) % 12 + 1
        import calendar
        return datetime.date(y, m, min(start.day, calendar.monthrange(y, m)[1])).isoformat()

    def add(self):
        name = self.e_name.get().strip()
        mid = self.e_mid.get().strip().upper()
        if not name or not lic.valid_mid(mid):
            return self.l.configure(text="Enter a name and a Machine ID like DD-1A2B-3C4D-5E6F.", text_color=RED)
        exp = self._until(self.o_dur.get())

        def fn():
            data, sha, _ = lic.fetch_list(self.tok)
            data.setdefault("machines", {})[mid] = {"name": name, "expires": exp, "active": True,
                                                    "added": datetime.date.today().isoformat()}
            lic.save_list(self.tok, data, sha, f"License: add {name}")
        self.e_name.delete(0, "end")
        self.e_mid.delete(0, "end")
        self._bg(fn, f"✓ {name} activated" + (f" until {exp}" if exp else " (never expires)"))

    def change(self, what):
        sel = list(self.tree.selection())
        if not sel:
            return self.l.configure(text="Select a student first.", text_color=RED)
        if what == "delete" and not messagebox.askyesno(core.APP_NAME, f"Delete {len(sel)} license(s)?", parent=self):
            return

        def fn():
            data, sha, today = lic.fetch_list(self.tok)
            ms = data.setdefault("machines", {})
            for mid in sel:
                e = ms.get(mid)
                if not e:
                    continue
                if what == "delete":
                    ms.pop(mid)
                elif what == "disable":
                    e["active"] = False
                elif what == "enable":
                    e["active"] = True
                elif what == "extend":
                    base = today
                    try:
                        if e.get("expires"):
                            base = max(today, datetime.date.fromisoformat(e["expires"]))
                    except ValueError:
                        pass
                    e["expires"] = self._until("1 month", base)
                    e["active"] = True
            lic.save_list(self.tok, data, sha, f"License: {what} {len(sel)}")
        self._bg(fn, {"delete": "✓ Deleted", "disable": "✓ License cancelled", "enable": "✓ Enabled",
                      "extend": "✓ Extended by 1 month"}[what])


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{core.APP_NAME}  v{core.VERSION}")
        self.geometry("1400x800")
        self.minsize(1100, 640)
        try:
            self.after(50, lambda: self.state("zoomed"))   # poori screen par khule
        except Exception:
            pass
        self.configure(fg_color=BG)
        self._set_icon()
        self.after(300, self._set_icon)      # customtkinter apna icon baad mein lagata hai, us ke baad dobara

        self.logq = queue.Queue()
        self.store = core.Store()
        self.engine = core.Engine(self.store, log=self.log, on_change=lambda: None)
        self.updating = False
        self.restart_pending = None

        self._build()
        self.refresh()
        self.after(200, self._drain_log)
        self.after(1500, self._tick)
        self.engine.locked = True
        self.lock_win = None
        self.lic_text = ""
        threading.Thread(target=self.license_check, daemon=True).start()
        self.engine.start_scheduler()
        threading.Thread(target=self._auto_update_loop, daemon=True).start()
        threading.Thread(target=self.make_shortcut, daemon=True).start()
        if not core.FFMPEG_PATH:
            self.log("⚠ FFmpeg not found — needed for 1080p/4K. Run 1_Install again.")
        self.log(f"Danish Downloader v{core.VERSION} started — {core.now_str()}. "
                 f"Auto check every {self.store.settings.get('check_hours')} hours.")

    # ------------------------------------------------------------ layout
    def _build(self):
        head = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=78, border_width=1, border_color=BORDER)
        head.pack(fill="x")
        head.pack_propagate(False)
        ctk.CTkLabel(head, text="⬇", font=(FONT, 34, "bold"), text_color=GREEN).pack(side="left", padx=(22, 8))
        tbox = ctk.CTkFrame(head, fg_color="transparent")
        tbox.pack(side="left")
        ctk.CTkLabel(tbox, text="Danish Downloader", font=(FONT, 24, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(tbox, text="TikTok • YouTube • Instagram • Facebook • Snapchat — best quality, fully automatic",
                     font=(FONT, 12), text_color=MUTED).pack(anchor="w")
        btn(head, "⬆  Check for Updates", self.do_update, color=SEC, hover=SEC_H, width=170).pack(side="right", padx=(0, 22))
        self.b_lic = btn(head, "🔑  Licenses", lambda: LicensesDialog(self), color=PINK, hover=PINK_H, width=130)
        self.v_autoupd = tk.BooleanVar(value=self.store.settings.get("auto_update", True))
        ctk.CTkSwitch(head, text="Auto Update", variable=self.v_autoupd, progress_color=GREEN,
                      font=(FONT, 12, "bold"), command=self.save_settings).pack(side="right", padx=12)
        self.l_state = ctk.CTkLabel(head, text="● Ready", font=(FONT, 14, "bold"), text_color=GREEN)
        self.l_state.pack(side="right", padx=16)

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", padx=18, pady=(14, 6))
        btn(tools, "+  Add Creator", self.add_src, color=GREEN, hover=GREEN_H, width=140).pack(side="left")
        btn(tools, "✎  Edit", self.edit_src, width=90).pack(side="left", padx=6)
        btn(tools, "🗑  Delete", self.del_src, color=SEC, hover=SEC_H, width=100).pack(side="left")
        btn(tools, "📂  Open Folder", self.open_folder, color=SEC, hover=SEC_H, width=140).pack(side="left", padx=6)
        btn(tools, "🔗  Download from Links", lambda: LinksDialog(self), color=SEC, hover=SEC_H, width=190).pack(side="left")
        btn(tools, "✨  Rename with AI", self.rename_ai, color=PINK, hover=PINK_H, width=160).pack(side="left", padx=6)
        btn(tools, "■  Stop", self.engine.stop, color=RED, hover=RED_H, width=90).pack(side="right")
        btn(tools, "⚡  Fill Selected", self.fill_selected, color=PINK, hover=PINK_H, width=150).pack(side="right", padx=6)
        btn(tools, "⟳  Check All", self.check_all, width=130).pack(side="right")

        # table
        tf = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=14)
        tf.pack(fill="both", expand=True, padx=18, pady=6)
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("D.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                     rowheight=34, borderwidth=0, font=(FONT, 11))
        st.configure("D.Treeview.Heading", background="#DCFCE7", foreground=HEAD,
                     font=(FONT, 11, "bold"), relief="flat", padding=6)
        st.map("D.Treeview", background=[("selected", GREEN)], foreground=[("selected", "white")])
        st.map("D.Treeview.Heading", background=[("active", SEC_H)])
        cols = ("folder", "platform", "stock", "status", "done", "last", "link")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", style="D.Treeview", selectmode="extended")
        for c, t, w in (("folder", "Folder / Page", 160), ("platform", "Platform", 80),
                        ("stock", "In Folder / Target", 150), ("status", "Status", 300),
                        ("done", "Total Done", 90), ("last", "Last Check", 150), ("link", "Creator Link", 260)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c in ("folder", "link", "status") else "center")
        self.tree.tag_configure("low", foreground=RED)
        self.tree.tag_configure("off", foreground="#9CA3AF")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        sb.pack(side="right", fill="y", pady=10, padx=(0, 6))
        self.tree.bind("<Double-1>", lambda e: self.edit_src())

        # settings
        sf = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=14)
        sf.pack(fill="x", padx=18, pady=6)
        s = self.store.settings

        def lab(t):
            ctk.CTkLabel(sf, text=t, font=(FONT, 12), text_color=MUTED).pack(side="left", padx=(14, 6), pady=12)

        self.v_auto = tk.BooleanVar(value=s.get("auto", True))
        ctk.CTkSwitch(sf, text="Auto Check", variable=self.v_auto, progress_color=GREEN, font=(FONT, 12, "bold"),
                      command=self.save_settings).pack(side="left", padx=14)
        lab("Every")
        self.e_hours = ctk.CTkEntry(sf, width=50, fg_color=PANEL2, border_color=BORDER)
        self.e_hours.insert(0, str(s.get("check_hours", 3)))
        self.e_hours.pack(side="left")
        lab("hours   |   Parallel")
        self.o_par = ctk.CTkOptionMenu(sf, values=[str(i) for i in (1, 2, 3, 4, 6, 8, 10, 12)], width=70,
                                       fg_color=SEC, button_color=ACCENT, button_hover_color=ACCENT_H, text_color=SEC_TEXT,
                                       command=lambda v: self.save_settings())
        self.o_par.set(str(s.get("parallel", 4)))
        self.o_par.pack(side="left")
        lab("|   Quality")
        self.o_q = ctk.CTkOptionMenu(sf, values=["1080p", "720p", "Best"], width=90,
                                     fg_color=SEC, button_color=ACCENT, button_hover_color=ACCENT_H, text_color=SEC_TEXT,
                                     command=lambda v: self.save_settings())
        self.o_q.set(s.get("quality", "1080p"))
        self.o_q.pack(side="left")
        lab("|   Max MB")
        self.e_mb = ctk.CTkEntry(sf, width=50, fg_color=PANEL2, border_color=BORDER)
        self.e_mb.insert(0, str(s.get("max_mb", 40)))
        self.e_mb.pack(side="left")
        self.v_photos = tk.BooleanVar(value=s.get("photos", True))
        ctk.CTkSwitch(sf, text="Photos", variable=self.v_photos, progress_color=GREEN, font=(FONT, 12),
                      width=60, command=self.save_settings).pack(side="left", padx=(12, 0))
        lab("|   Cookies")
        self.o_ck = ctk.CTkOptionMenu(sf, values=["none", "firefox", "edge", "chrome", "brave", "opera"], width=100,
                                      fg_color=SEC, button_color=ACCENT, button_hover_color=ACCENT_H, text_color=SEC_TEXT,
                                      command=lambda v: self.save_settings())
        self.o_ck.set(s.get("cookies_browser", "none"))
        self.o_ck.pack(side="left")
        btn(sf, "cookies.txt", self.pick_cookies, color=SEC, hover=SEC_H, width=100).pack(side="left", padx=8)
        self.b_ai = btn(sf, "✨ AI Names: ON" if s.get("ai_names") else "✨ AI Names", lambda: AIDialog(self),
                        color=PINK, hover=PINK_H, width=140)
        self.b_ai.pack(side="left", padx=4)
        btn(sf, "Save Settings", self.save_settings, width=120).pack(side="right", padx=14)

        # log
        lf = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=14)
        lf.pack(fill="both", padx=18, pady=(6, 16))
        ctk.CTkLabel(lf, text="Activity Log", font=(FONT, 12, "bold"), text_color=HEAD).pack(anchor="w", padx=14, pady=(8, 0))
        self.t_log = ctk.CTkTextbox(lf, height=170, fg_color="#F7FCF9", text_color=ACCENT_H, border_width=1, border_color=BORDER,
                                    font=("Consolas", 11), corner_radius=10)
        self.t_log.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.t_log.configure(state="disabled")

    # ------------------------------------------------------------ license
    def license_check(self, from_lock=False):
        ok, msg, info = lic.check(self.store.settings)
        self.store.save()
        self.after(0, lambda: self.unlock(msg) if ok else self.lock(msg))
        if not getattr(self, "_lic_loop", False):
            self._lic_loop = True
            threading.Thread(target=self._license_loop, daemon=True).start()

    def _license_loop(self):
        while True:
            time.sleep(3600)
            ok, msg, _ = lic.check(self.store.settings)
            self.store.save()
            if not ok:
                self.after(0, lambda m=msg: self.lock(m))

    def lock(self, msg):
        self.engine.locked = True
        if self.engine.running:
            self.engine.stop()
        if self.lock_win is not None and self.lock_win.winfo_exists():
            self.lock_win.l_msg.configure(text=msg, text_color=RED)
        else:
            self.lock_win = LockWindow(self, msg)
        self.log(f"🔒 {msg}  Machine ID: {lic.machine_id()}")

    def unlock(self, msg):
        was = self.engine.locked
        self.engine.locked = False
        if self.lock_win is not None and self.lock_win.winfo_exists():
            self.lock_win.grab_release()
            self.lock_win.destroy()
        self.lock_win = None
        if msg == "Owner" and self.store.settings.get("owner_token"):
            self.b_lic.pack(side="right", padx=(0, 10))
        if was:
            self.log(f"🔓 {msg}")

    def _set_icon(self):
        try:
            if os.path.exists(ICON):
                self.iconbitmap(ICON)
        except Exception:
            pass

    def make_shortcut(self, force=False):
        """Desktop par 'Danish Downloader' shortcut, apne logo ke saath (ek dafa)."""
        st = self.store.settings
        if (st.get("shortcut_v3") and not force) or getattr(sys, "frozen", False) or os.name != "nt":
            return
        if not os.path.exists(ICON):
            return
        py = sys.executable
        if py.lower().endswith("python.exe"):
            alt = py[:-10] + "pythonw.exe"
            py = alt if os.path.exists(alt) else py
        script = os.path.join(core.BASE_DIR, "danish_downloader.py")
        q = lambda x: x.replace("'", "''")
        ps = ("$d=[Environment]::GetFolderPath('Desktop');"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Danish Downloader.lnk'));"
              f"$s.TargetPath='{q(py)}';$s.Arguments='\"{q(script)}\"';"
              f"$s.WorkingDirectory='{q(core.BASE_DIR)}';$s.IconLocation='{q(ICON)},0';"
              "$s.Description='Danish Downloader';$s.Save()")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                               capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
            if r.returncode == 0:
                st["shortcut_v3"] = True
                self.store.save()
                self.log("✓ Desktop shortcut 'Danish Downloader' created with the app logo. "
                         "You can delete the old shortcut.")
            else:
                self.log("⚠ Could not create desktop shortcut: " + (r.stderr or "").strip()[:150])
        except Exception as e:
            self.log(f"⚠ Could not create desktop shortcut: {e}")

    # ------------------------------------------------------------ helpers
    def log(self, msg):
        self.logq.put(msg)

    def _drain_log(self):
        lines = []
        try:
            while True:
                lines.append(self.logq.get_nowait())
        except queue.Empty:
            pass
        if lines:
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            text = "".join(f"[{stamp}] {l}\n" for l in lines)
            self.t_log.configure(state="normal")
            self.t_log.insert("end", text)
            if int(self.t_log.index("end-1c").split(".")[0]) > 3000:
                self.t_log.delete("1.0", "1000.0")
            self.t_log.see("end")
            self.t_log.configure(state="disabled")
            try:
                with open(core.LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass
        self.after(200, self._drain_log)

    def _tick(self):
        self.refresh()
        # an auto update finished while a job was running -> restart as soon as idle
        if self.restart_pending and not self.engine.running:
            v, self.restart_pending = self.restart_pending, None
            self.log(f"⟳ Restarting to finish update v{v}...")
            self.after(1500, self._restart)
        self.after(1500, self._tick)

    def refresh(self):
        ids = {s["id"] for s in self.store.sources}
        for i in self.tree.get_children():
            if i not in ids:
                self.tree.delete(i)
        for s in self.store.sources:
            have = core.count_videos(s["folder"])
            target = int(s["per_day"]) * int(s["stock_days"])
            low = have <= int(s["per_day"]) * int(s["refill_days"])
            tags = ("off",) if not s.get("enabled", True) else (("low",) if low else ())
            status = self.engine.status.get(s["id"]) or s.get("last_result") or "—"
            days_left = have / max(int(s["per_day"]), 1)
            vals = (os.path.basename(os.path.normpath(s["folder"])), core.platform_of(s["url"]),
                    f"{have} / {target}  ({days_left:.0f} days)", status, len(s.get("done_ids", [])),
                    s.get("last_check") or "—", s["url"])
            if self.tree.exists(s["id"]):
                self.tree.item(s["id"], values=vals, tags=tags)
            else:
                self.tree.insert("", "end", iid=s["id"], tags=tags, values=vals)
        live = [v for v in self.engine.status.values() if v.startswith("⬇")]
        if self.updating:
            self.l_state.configure(text="● Updating...", text_color=ACCENT)
        elif self.engine.running:
            self.l_state.configure(text="● " + (live[0] if live else "Working..."), text_color=PINK)
        else:
            self.l_state.configure(text="● Ready", text_color=GREEN)

    def selected(self):
        return [self.store.get(i) for i in self.tree.selection() if self.store.get(i)]

    # ------------------------------------------------------------ actions
    def add_src(self):
        SourceDialog(self)

    def edit_src(self):
        s = self.selected()
        if not s:
            return messagebox.showinfo(core.APP_NAME, "Select a creator from the list first.")
        SourceDialog(self, s[0])

    def del_src(self):
        s = self.selected()
        if not s:
            return
        names = ", ".join(os.path.basename(x["folder"]) for x in s)
        if messagebox.askyesno(core.APP_NAME, f"Remove from list?\n{names}\n\n(Folders and videos will NOT be deleted.)"):
            for x in s:
                self.store.remove_source(x["id"])
            self.refresh()

    def open_folder(self):
        s = self.selected()
        if s and os.path.isdir(s[0]["folder"]):
            os.startfile(s[0]["folder"])

    def check_all(self):
        self.engine.run_async(force=False)

    def fill_selected(self):
        s = self.selected()
        if not s:
            return messagebox.showinfo(core.APP_NAME, "Select creators first (hold Ctrl to select multiple).")
        self.engine.run_async(ids=[x["id"] for x in s], force=True)

    def rename_ai(self):
        st = self.store.settings
        if not core.danish_ai.has_key(st):
            messagebox.showinfo(core.APP_NAME, "First set up AI Names (paste your Claude or Gemini key).")
            return AIDialog(self)
        s = self.selected()
        if not s:
            return messagebox.showinfo(core.APP_NAME, "Select creators first (hold Ctrl to select multiple).")
        names = ", ".join(os.path.basename(x["folder"]) for x in s)
        if messagebox.askyesno(core.APP_NAME, f"Give new AI names to the videos already in:\n{names}\n\n"
                               f"Only videos whose name is just hashtags (or a number) will be renamed.\n"
                               f"Videos with real names stay the same."):
            self.engine.run_async(ids=[x["id"] for x in s], rename=True)

    # ------------------------------------------------------------ updates
    def do_update(self):
        if self.engine.running:
            return messagebox.showinfo(core.APP_NAME, "A download is running. Wait for it to finish or click Stop, then update.")
        live = [v for v in self.engine.status.values() if v.startswith("⬇")]
        if self.updating:
            return
        self.log("⬆ Checking for updates...")
        threading.Thread(target=self._update_worker, kwargs={"manual": True}, daemon=True).start()

    def _auto_update_loop(self):
        time.sleep(20)
        last_app = 0.0
        while True:
            s = self.store.settings
            if s.get("auto_update", True) and not self.engine.running and not self.updating \
                    and time.time() - last_app >= APP_UPDATE_EVERY:
                last_app = time.time()
                self._update_worker(manual=False)
            time.sleep(300)

    def _python(self):
        py = sys.executable
        if py.lower().endswith("pythonw.exe"):
            alt = py[:-11] + "python.exe"
            if os.path.exists(alt):
                return alt
        return py

    def _update_ytdlp(self, manual):
        if getattr(sys, "frozen", False):
            return
        s = self.store.settings
        if not manual and time.time() - float(s.get("last_ytdlp_update", 0)) < YTDLP_UPDATE_EVERY:
            return
        try:
            r = subprocess.run([self._python(), "-m", "pip", "install", "--upgrade", "yt-dlp[default]"],
                               capture_output=True, text=True, creationflags=NO_WINDOW)
            if r.returncode == 0:
                s["last_ytdlp_update"] = time.time()
                self.store.save()
                if manual or "Successfully installed" in r.stdout:
                    self.log("   ✓ yt-dlp is up to date.")
            else:
                self.log("   ✗ yt-dlp update failed (check internet).")
        except Exception as e:
            self.log(f"   ✗ yt-dlp update: {e}")

    def _fetch(self, name):
        last = None
        for base in core.UPDATE_BASES:
            try:
                return urllib.request.urlopen(f"{base}{name}?t={int(time.time())}", timeout=40).read()
            except Exception as e:
                last = e
        raise last

    def _update_worker(self, manual=False):
        self.updating = True
        try:
            self._update_ytdlp(manual)
            try:
                info = json.loads(self._fetch("version.json").decode("utf-8"))
            except Exception as e:
                if manual:
                    self.log(f"   ✗ Could not reach GitHub update ({e}). Are the files uploaded to "
                             f"{core.GITHUB_USER}/{core.GITHUB_REPO}/{core.GITHUB_FOLDER}?")
                return
            remote = str(info.get("version", "0"))
            if self._ver(remote) <= self._ver(core.VERSION):
                if manual:
                    self.log(f"   ✓ You have the latest version (v{core.VERSION}).")
                return
            if getattr(sys, "frozen", False):
                self.log(f"   ℹ New version v{remote} available. Rebuild the EXE to get it.")
                return
            self.log(f"   New version v{remote} found, downloading...")
            new = {}
            try:
                for fn in info.get("files", ["danish_core.py", "danish_downloader.py"]):
                    data = self._fetch(fn)
                    if fn.endswith(".py"):
                        compile(data, fn, "exec")   # refuse a broken file
                    new[fn] = data
            except Exception as e:
                self.log(f"   ✗ Update download failed: {e}")
                return
            for fn, data in new.items():
                path = os.path.join(core.BASE_DIR, os.path.basename(fn))
                if os.path.exists(path):
                    shutil.copy(path, path + ".old")
                with open(path + ".new", "wb") as f:
                    f.write(data)
                os.replace(path + ".new", path)
            self.log(f"   ✓ Version v{remote} installed. {info.get('notes', '')}")
            if manual:
                self.after(0, self._ask_restart, remote)
            else:
                self.restart_pending = remote    # restarts automatically when no download is running
        finally:
            self.updating = False

    @staticmethod
    def _ver(v):
        try:
            return tuple(int(x) for x in str(v).split("."))
        except ValueError:
            return (0,)

    def _ask_restart(self, remote):
        if messagebox.askyesno(core.APP_NAME, f"Updated to v{remote}!\nRestart now?"):
            self._restart()

    def _restart(self):
        subprocess.Popen([sys.executable, os.path.join(core.BASE_DIR, "danish_downloader.py")], cwd=core.BASE_DIR)
        self.destroy()

    # ------------------------------------------------------------ settings
    def pick_cookies(self):
        f = filedialog.askopenfilename(title="Select cookies.txt", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        self.store.settings["cookies_file"] = f or ""
        self.store.save()
        self.log("cookies.txt " + ("set: " + f if f else "removed"))

    def save_settings(self):
        s = self.store.settings
        try:
            s["check_hours"] = max(float(self.e_hours.get()), 0.25)
        except ValueError:
            pass
        s["parallel"] = int(self.o_par.get())
        s["cookies_browser"] = self.o_ck.get()
        s["quality"] = self.o_q.get()
        s["photos"] = bool(self.v_photos.get())
        try:
            s["max_mb"] = max(float(self.e_mb.get()), 0)
        except ValueError:
            pass
        s["auto"] = bool(self.v_auto.get())
        s["auto_update"] = bool(self.v_autoupd.get())
        self.store.save()
        self.log(f"Settings saved: check every {s['check_hours']} h, {s['parallel']} parallel, "
                 f"quality: {s['quality']}, max {s['max_mb']:.0f} MB, cookies: {s['cookies_browser']}, auto check: {'ON' if s['auto'] else 'OFF'}, "
                 f"auto update: {'ON' if s['auto_update'] else 'OFF'}")


if __name__ == "__main__":
    App().mainloop()
