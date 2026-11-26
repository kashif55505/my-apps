# -*- coding: utf-8 -*-
"""Danish Downloader - Windows app (screen wala hissa)."""
import os
import sys
import queue
import threading
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk

import danish_core as core

# ---- rang (colors)
BG = "#0F0D1A"
PANEL = "#1A1726"
PANEL2 = "#241F35"
ACCENT = "#8B5CF6"
ACCENT_H = "#7C3AED"
PINK = "#EC4899"
PINK_H = "#DB2777"
GREEN = "#10B981"
GREEN_H = "#059669"
RED = "#EF4444"
RED_H = "#DC2626"
TEXT = "#EDE9FE"
MUTED = "#A39DBA"
FONT = "Segoe UI"

ctk.set_appearance_mode("dark")


def btn(parent, text, cmd, color=ACCENT, hover=ACCENT_H, width=150):
    return ctk.CTkButton(parent, text=text, command=cmd, fg_color=color, hover_color=hover,
                         corner_radius=10, height=38, width=width, font=(FONT, 13, "bold"))


class SourceDialog(ctk.CTkToplevel):
    def __init__(self, app, src=None):
        super().__init__(app)
        self.app, self.src = app, src
        self.title("Creator edit karein" if src else "Naya creator add karein")
        self.geometry("620x690")
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
                             justify="left", wraplength=540).pack(fill="x")

        def entry(val=""):
            e = ctk.CTkEntry(f, height=36, fg_color=PANEL2, border_color=ACCENT, font=(FONT, 12))
            e.pack(fill="x", pady=(4, 0))
            if val != "":
                e.insert(0, str(val))
            return e

        s = src or {}
        label("Creator ka link", "TikTok / YouTube / Facebook profile ka link. Jaise https://www.tiktok.com/@danish")
        self.e_url = entry(s.get("url", ""))

        label("Folder", "Wo folder jo Facebook page ke naam par bana hai. Naya naam likhenge to folder khud ban jayega.")
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self.e_folder = ctk.CTkEntry(row, height=36, fg_color=PANEL2, border_color=ACCENT, font=(FONT, 12))
        self.e_folder.pack(side="left", fill="x", expand=True)
        if s.get("folder"):
            self.e_folder.insert(0, s["folder"])
        btn(row, "Browse", self.browse, width=90).pack(side="left", padx=(8, 0))

        label("Ek din mein kitni videos lagti hain")
        self.e_day = entry(s.get("per_day", 3))
        label("Kitne din ka stock rakhna hai", "Maslan 3 videos × 15 din = folder mein 45 videos rahengi.")
        self.e_days = entry(s.get("stock_days", 15))
        label("Jab kitne din ki videos reh jayein to dobara bharein", "Maslan 3 = jab 9 videos reh jayein to folder phir se poora bhar do.")
        self.e_refill = entry(s.get("refill_days", 3))

        if not src:
            label("Pehle se kitni videos download kar chuke hain (upar se)",
                  "Jaise 60 likhein to upar ki 60 chhor kar aage se download hoga. Naya creator ho to 0.")
            self.e_skip = entry(0)
        else:
            self.e_skip = None
            ctk.CTkLabel(f, text=f"Ab tak yaad: {len(s.get('done_ids', []))} videos download ho chuki hain.",
                         font=(FONT, 12), text_color=GREEN, anchor="w").pack(fill="x", pady=(10, 0))

        label("Purane links (zaroori nahi)",
              "Agar Link Grabber se purane links nikale hue hain to yahan paste kar dein — wo videos dobara nahi aayengi.")
        self.t_links = ctk.CTkTextbox(f, height=90, fg_color=PANEL2, border_color=ACCENT, border_width=2, font=(FONT, 11))
        self.t_links.pack(fill="x", pady=(4, 0))

        self.v_enabled = tk.BooleanVar(value=s.get("enabled", True))
        ctk.CTkSwitch(f, text="Automatic check mein shamil", variable=self.v_enabled, progress_color=ACCENT,
                      font=(FONT, 12)).pack(anchor="w", pady=(12, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=12)
        btn(bar, "Save karein", self.save, color=GREEN, hover=GREEN_H).pack(side="right")
        btn(bar, "Cancel", self.destroy, color=PANEL2, hover="#332B4A", width=100).pack(side="right", padx=8)

    def browse(self):
        d = filedialog.askdirectory(parent=self, title="Facebook page wala folder chunein")
        if d:
            self.e_folder.delete(0, "end")
            self.e_folder.insert(0, os.path.normpath(d))

    def save(self):
        url = self.e_url.get().strip()
        folder = self.e_folder.get().strip()
        if not url.startswith("http"):
            return messagebox.showerror(core.APP_NAME, "Sahi link daalein (http se shuru).", parent=self)
        if not folder:
            return messagebox.showerror(core.APP_NAME, "Folder chunein ya naam likhein.", parent=self)
        if not os.path.isabs(folder):
            root = self.app.store.settings.get("root_folder") or os.path.join(os.path.expanduser("~"), "Videos", "Danish Downloader")
            folder = os.path.join(root, folder)
        try:
            per_day = int(self.e_day.get())
            days = int(self.e_days.get())
            refill = int(self.e_refill.get())
            skip = int(self.e_skip.get()) if self.e_skip else 0
            assert per_day > 0 and days > 0 and refill >= 0 and skip >= 0
        except Exception:
            return messagebox.showerror(core.APP_NAME, "Ginti wale khaanon mein sirf number likhein.", parent=self)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            return messagebox.showerror(core.APP_NAME, f"Folder nahi ban saka:\n{e}", parent=self)

        st = self.app.store
        if self.src:
            with st.lock:
                self.src.update(url=url, folder=folder, per_day=per_day, stock_days=days,
                                refill_days=refill, enabled=self.v_enabled.get())
            st.save()
            src = self.src
        else:
            src = st.add_source(url=url, folder=folder, per_day=per_day, stock_days=days,
                                refill_days=refill, skip_top=skip, enabled=self.v_enabled.get())
        n = st.add_old_links(src, self.t_links.get("1.0", "end"))
        if n:
            self.app.log(f"{os.path.basename(folder)}: {n} purane links 'ho chuke' mark kar diye.")
        self.app.refresh()
        self.destroy()


class LinksDialog(ctk.CTkToplevel):
    """4K Downloader jaisa: links paste karo, sab download."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Links se download")
        self.geometry("640x520")
        self.configure(fg_color=PANEL)
        self.transient(app)
        self.after(100, self.grab_set)
        ctk.CTkLabel(self, text="Links paste karein (har line mein ek)", font=(FONT, 14, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(18, 0))
        ctk.CTkLabel(self, text="TikTok, YouTube, Instagram reels, Facebook, Snapchat spotlight... sab ek saath, best quality mein.",
                     font=(FONT, 11), text_color=MUTED).pack(anchor="w", padx=18)
        self.t = ctk.CTkTextbox(self, fg_color=PANEL2, border_color=ACCENT, border_width=2, font=(FONT, 11))
        self.t.pack(fill="both", expand=True, padx=18, pady=8)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18)
        self.e_folder = ctk.CTkEntry(row, height=36, fg_color=PANEL2, border_color=ACCENT,
                                     placeholder_text="Kis folder mein? (Browse dabayein)")
        self.e_folder.pack(side="left", fill="x", expand=True)
        btn(row, "Browse", self.browse, width=90).pack(side="left", padx=(8, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=12)
        btn(bar, "⬇  Download shuru", self.go, color=PINK, hover=PINK_H, width=180).pack(side="right")

    def browse(self):
        d = filedialog.askdirectory(parent=self)
        if d:
            self.e_folder.delete(0, "end")
            self.e_folder.insert(0, os.path.normpath(d))

    def go(self):
        links = [l.strip() for l in self.t.get("1.0", "end").split() if l.strip().startswith("http")]
        folder = self.e_folder.get().strip()
        if not links:
            return messagebox.showerror(core.APP_NAME, "Koi link nahi mila.", parent=self)
        if not folder:
            return messagebox.showerror(core.APP_NAME, "Folder chunein.", parent=self)
        self.app.engine.run_async(links=links, links_folder=folder)
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{core.APP_NAME}  v{core.VERSION}")
        self.geometry("1180x760")
        self.minsize(980, 620)
        self.configure(fg_color=BG)
        try:
            self.iconbitmap(os.path.join(core.BASE_DIR, "danish.ico"))
        except Exception:
            pass

        self.logq = queue.Queue()
        self.store = core.Store()
        self.engine = core.Engine(self.store, log=self.log, on_change=lambda: None)

        self._build()
        self.refresh()
        self.after(200, self._drain_log)
        self.after(1500, self._tick)
        self.engine.start_scheduler()
        if not core.FFMPEG_PATH:
            self.log("⚠ FFmpeg nahi mila — 1080p/4K mein awaaz aur video jorne ke liye zaroori hai. 1_Install.bat dobara chalayein.")
        self.log(f"Danish Downloader shuru — {core.now_str()}. Automatic check har "
                 f"{self.store.settings.get('check_hours')} ghante baad.")

    # ------------------------------------------------------------ layout
    def _build(self):
        head = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=78)
        head.pack(fill="x")
        head.pack_propagate(False)
        ctk.CTkLabel(head, text="⬇", font=(FONT, 34, "bold"), text_color=PINK).pack(side="left", padx=(22, 8))
        tbox = ctk.CTkFrame(head, fg_color="transparent")
        tbox.pack(side="left")
        ctk.CTkLabel(tbox, text="Danish Downloader", font=(FONT, 24, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(tbox, text="TikTok • YouTube • Instagram • Facebook • Snapchat — best quality, khud-ba-khud",
                     font=(FONT, 12), text_color=MUTED).pack(anchor="w")
        self.l_state = ctk.CTkLabel(head, text="● Tayyar", font=(FONT, 14, "bold"), text_color=GREEN)
        self.l_state.pack(side="right", padx=22)

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", padx=18, pady=(14, 6))
        btn(tools, "+  Creator add", self.add_src, color=GREEN, hover=GREEN_H).pack(side="left")
        btn(tools, "✎  Edit", self.edit_src, width=100).pack(side="left", padx=6)
        btn(tools, "🗑  Hatayein", self.del_src, color=PANEL2, hover="#332B4A", width=120).pack(side="left")
        btn(tools, "📂  Folder kholein", self.open_folder, color=PANEL2, hover="#332B4A").pack(side="left", padx=6)
        btn(tools, "🔗  Links se download", lambda: LinksDialog(self), color=PANEL2, hover="#332B4A", width=170).pack(side="left")
        self.b_stop = btn(tools, "■  Rokein", self.engine.stop, color=RED, hover=RED_H, width=110)
        self.b_stop.pack(side="right")
        btn(tools, "⚡  Selected abhi bharein", self.fill_selected, color=PINK, hover=PINK_H, width=190).pack(side="right", padx=6)
        btn(tools, "⟳  Sab check karein", self.check_all, width=170).pack(side="right")

        # table
        tf = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=14)
        tf.pack(fill="both", expand=True, padx=18, pady=6)
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("D.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                     rowheight=34, borderwidth=0, font=(FONT, 11))
        st.configure("D.Treeview.Heading", background=PANEL2, foreground="#C4B5FD",
                     font=(FONT, 11, "bold"), relief="flat", padding=6)
        st.map("D.Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        st.map("D.Treeview.Heading", background=[("active", "#332B4A")])
        cols = ("folder", "platform", "link", "stock", "done", "status", "last")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", style="D.Treeview", selectmode="extended")
        for c, t, w in (("folder", "Folder / Page", 170), ("platform", "Platform", 90), ("link", "Creator link", 260),
                        ("stock", "Folder mein / Target", 150), ("done", "Ab tak download", 120),
                        ("status", "Haal", 220), ("last", "Aakhri check", 150)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c in ("folder", "link", "status") else "center")
        self.tree.tag_configure("low", foreground="#FCA5A5")
        self.tree.tag_configure("off", foreground="#6B6680")
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
        ctk.CTkSwitch(sf, text="Automatic", variable=self.v_auto, progress_color=GREEN, font=(FONT, 12, "bold"),
                      command=self.save_settings).pack(side="left", padx=14)
        lab("Har")
        self.e_hours = ctk.CTkEntry(sf, width=50, fg_color=PANEL2, border_color=ACCENT)
        self.e_hours.insert(0, str(s.get("check_hours", 3)))
        self.e_hours.pack(side="left")
        lab("ghante baad check   |   Ek saath downloads")
        self.o_par = ctk.CTkOptionMenu(sf, values=[str(i) for i in (1, 2, 3, 4, 6, 8, 10, 12)], width=70,
                                       fg_color=PANEL2, button_color=ACCENT, button_hover_color=ACCENT_H,
                                       command=lambda v: self.save_settings())
        self.o_par.set(str(s.get("parallel", 4)))
        self.o_par.pack(side="left")
        lab("|   Cookies (login)")
        self.o_ck = ctk.CTkOptionMenu(sf, values=["none", "firefox", "edge", "chrome", "brave", "opera"], width=100,
                                      fg_color=PANEL2, button_color=ACCENT, button_hover_color=ACCENT_H,
                                      command=lambda v: self.save_settings())
        self.o_ck.set(s.get("cookies_browser", "none"))
        self.o_ck.pack(side="left")
        btn(sf, "cookies.txt", self.pick_cookies, color=PANEL2, hover="#332B4A", width=100).pack(side="left", padx=8)
        btn(sf, "Save", self.save_settings, width=80).pack(side="right", padx=14)

        # log
        lf = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=14)
        lf.pack(fill="both", padx=18, pady=(6, 16))
        ctk.CTkLabel(lf, text="Kya ho raha hai", font=(FONT, 12, "bold"), text_color="#C4B5FD").pack(anchor="w", padx=14, pady=(8, 0))
        self.t_log = ctk.CTkTextbox(lf, height=170, fg_color="#0B0A12", text_color="#D1FAE5",
                                    font=("Consolas", 11), corner_radius=10)
        self.t_log.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.t_log.configure(state="disabled")

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
            vals = (os.path.basename(os.path.normpath(s["folder"])), core.platform_of(s["url"]), s["url"],
                    f"{have} / {target}  ({days_left:.0f} din)", len(s.get("done_ids", [])), status,
                    s.get("last_check") or "—")
            if self.tree.exists(s["id"]):
                self.tree.item(s["id"], values=vals, tags=tags)
            else:
                self.tree.insert("", "end", iid=s["id"], tags=tags, values=vals)
        if self.engine.running:
            self.l_state.configure(text="● Kaam chal raha hai...", text_color=PINK)
        else:
            self.l_state.configure(text="● Tayyar", text_color=GREEN)

    def selected(self):
        return [self.store.get(i) for i in self.tree.selection() if self.store.get(i)]

    # ------------------------------------------------------------ actions
    def add_src(self):
        SourceDialog(self)

    def edit_src(self):
        s = self.selected()
        if not s:
            return messagebox.showinfo(core.APP_NAME, "Pehle list mein se ek creator chunein.")
        SourceDialog(self, s[0])

    def del_src(self):
        s = self.selected()
        if not s:
            return
        names = ", ".join(os.path.basename(x["folder"]) for x in s)
        if messagebox.askyesno(core.APP_NAME, f"In ko list se hatana hai?\n{names}\n\n(Folder aur videos delete nahi hongi.)"):
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
            return messagebox.showinfo(core.APP_NAME, "Pehle list mein se creator chunein (Ctrl dabakar kai chun sakte hain).")
        self.engine.run_async(ids=[x["id"] for x in s], force=True)

    def pick_cookies(self):
        f = filedialog.askopenfilename(title="cookies.txt chunein", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        self.store.settings["cookies_file"] = f or ""
        self.store.save()
        self.log("cookies.txt " + ("lag gayi: " + f if f else "hata di gayi"))

    def save_settings(self):
        s = self.store.settings
        try:
            s["check_hours"] = max(float(self.e_hours.get()), 0.25)
        except ValueError:
            pass
        s["parallel"] = int(self.o_par.get())
        s["cookies_browser"] = self.o_ck.get()
        s["auto"] = bool(self.v_auto.get())
        self.store.save()
        self.log(f"Settings save: har {s['check_hours']} ghante, {s['parallel']} downloads ek saath, "
                 f"cookies: {s['cookies_browser']}, automatic: {'ON' if s['auto'] else 'OFF'}")


if __name__ == "__main__":
    App().mainloop()
