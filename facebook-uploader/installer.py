"""
Facebook Uploader — Setup (next-next installer)
Program Files me install karta hai (default), location browse ki ja sakti hai,
desktop + start menu par logo wala shortcut banata hai, uninstaller bhi.
Ye khud aik .exe ban jata hai jisme app exe bundled hoti hai.
"""
import os
import sys
import shutil
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "Facebook Uploader"          # install folder (same rakha, updates ke liye)
DISPLAY_NAME = "DK Facebook Uploader"
APP_EXE = "Facebook Uploader.exe"
ICON = "app_icon.ico"


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def default_dir():
    # User ki apni jagah — admin ki zaroorat nahi (koi permission error nahi)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return os.path.join(local, "Programs", APP_NAME)
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    return os.path.join(pf, APP_NAME)


def make_shortcut(lnk_path, target_exe, icon, workdir):
    vbs = (
        'Set s = CreateObject("WScript.Shell")\r\n'
        f'Set lnk = s.CreateShortcut("{lnk_path}")\r\n'
        f'lnk.TargetPath = "{target_exe}"\r\n'
        f'lnk.WorkingDirectory = "{workdir}"\r\n'
        f'lnk.IconLocation = "{icon}"\r\n'
        'lnk.Save\r\n'
    )
    tmp = os.path.join(os.environ.get("TEMP", "."), "_mk_lnk.vbs")
    nowin = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(vbs)
        subprocess.call(["cscript", "//nologo", tmp], **nowin)
    except Exception:
        pass
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


class Setup(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{DISPLAY_NAME} Setup")
        self.geometry("580x380")
        self.resizable(False, False)
        try:
            self.iconbitmap(resource(ICON))
        except Exception:
            pass
        # crisp fonts
        try:
            self.tk.call("tk", "scaling", 1.25)
        except Exception:
            pass
        style = ttk.Style(self)
        try:
            if "vista" in style.theme_names():
                style.theme_use("vista")
        except Exception:
            pass
        self.option_add("*Font", ("Segoe UI", 11))
        style.configure(".", font=("Segoe UI", 11))
        style.configure("TButton", font=("Segoe UI", 11))
        style.configure("TLabel", font=("Segoe UI", 11))
        style.configure("TCheckbutton", font=("Segoe UI", 11))
        self.target = tk.StringVar(value=default_dir())
        self.desktop = tk.IntVar(value=1)
        self.launch = tk.IntVar(value=1)

        tk.Label(self, text=f"{DISPLAY_NAME} Setup", font=("Segoe UI", 16, "bold")).pack(pady=(22, 6))
        tk.Label(self, text="This will install the software on your computer.",
                 font=("Segoe UI", 10)).pack()

        box = ttk.LabelFrame(self, text="Install location")
        box.pack(fill="x", padx=24, pady=18)
        row = ttk.Frame(box); row.pack(fill="x", padx=10, pady=10)
        ttk.Entry(row, textvariable=self.target).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse", command=self._browse).pack(side="left", padx=6)
        tk.Label(box, text="Default is your user folder (no admin needed). Browse to change.",
                 font=("Segoe UI", 9), fg="#555").pack(anchor="w", padx=12, pady=(0, 6))

        ttk.Checkbutton(self, text="Create desktop shortcut", variable=self.desktop).pack(anchor="w", padx=28)
        ttk.Checkbutton(self, text="Open the software after install", variable=self.launch).pack(anchor="w", padx=28)

        self.status = tk.Label(self, text="", font=("Segoe UI", 9), fg="#555")
        self.status.pack(pady=6)

        btns = ttk.Frame(self); btns.pack(side="bottom", fill="x", pady=16, padx=24)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        self.install_btn = ttk.Button(btns, text="Install", command=self._install)
        self.install_btn.pack(side="right", padx=8)

    def _browse(self):
        d = filedialog.askdirectory(title="Install location")
        if d:
            self.target.set(os.path.join(d, APP_NAME))

    def _install(self):
        target = self.target.get().strip()
        if not target:
            messagebox.showwarning("Wait", "Please choose a location.")
            return
        self.install_btn.config(state="disabled")
        self.status.config(text="Installing...")
        self.update()
        try:
            os.makedirs(target, exist_ok=True)
            shutil.copy2(resource(APP_EXE), os.path.join(target, APP_EXE))
            icon_dest = os.path.join(target, ICON)
            try:
                shutil.copy2(resource(ICON), icon_dest)
            except Exception:
                icon_dest = os.path.join(target, APP_EXE)

            exe_path = os.path.join(target, APP_EXE)
            if self.desktop.get():
                desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
                for _old in (APP_NAME,):
                    try:
                        os.remove(os.path.join(desktop, _old + ".lnk"))
                    except Exception:
                        pass
                make_shortcut(os.path.join(desktop, DISPLAY_NAME + ".lnk"), exe_path, icon_dest, target)
            sm = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
            try:
                os.makedirs(sm, exist_ok=True)
                make_shortcut(os.path.join(sm, DISPLAY_NAME + ".lnk"), exe_path, icon_dest, target)
            except Exception:
                pass

            # uninstaller
            unin = os.path.join(target, "uninstall.bat")
            with open(unin, "w", encoding="utf-8") as f:
                f.write("@echo off\r\n")
                f.write(f'del "%USERPROFILE%\\Desktop\\{DISPLAY_NAME}.lnk" >nul 2>&1\r\n')
                f.write(f'del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{DISPLAY_NAME}.lnk" >nul 2>&1\r\n')
                f.write(f'rmdir /S /Q "{target}" >nul 2>&1\r\n')

            self.status.config(text="Installation complete!")
            messagebox.showinfo("Done", f"{DISPLAY_NAME} installed at:\n{target}")
            if self.launch.get():
                try:
                    os.startfile(exe_path)
                except Exception:
                    pass
            self.destroy()
        except PermissionError:
            messagebox.showerror("Permission",
                                 "No permission to write to Program Files. "
                                 "Run Setup as administrator, or choose another location (e.g. D:).")
            self.install_btn.config(state="normal")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.install_btn.config(state="normal")


def _dpi_fix():
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _is_admin():
    try:
        from ctypes import windll
        return bool(windll.shell32.IsUserAnAdmin())
    except Exception:
        return True  # non-Windows / unknown: proceed


def _relaunch_as_admin():
    """Agar admin nahi to khud ko admin (UAC) ke saath dobara chalayein."""
    try:
        from ctypes import windll
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = ""
        else:
            exe = sys.executable  # python
            params = f'"{os.path.abspath(__file__)}"'
        # ShellExecuteW runas -> UAC prompt
        rc = windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32
    except Exception:
        return False


if __name__ == "__main__":
    # Default install user ki apni jagah (LocalAppData) hai — admin ki zaroorat nahi.
    # Agar user Program Files chunta hai aur permission error aaye to message use
    # 'Run as administrator' ya doosri jagah chunne ko kehta hai.
    _dpi_fix()
    Setup().mainloop()
