//go:build windows

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

const createNoWindow = 0x08000000

func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
}

func dataDir() string {
	if d := os.Getenv("LOCALAPPDATA"); d != "" {
		return filepath.Join(d, "TezDM")
	}
	h, _ := os.UserHomeDir()
	return filepath.Join(h, "AppData", "Local", "TezDM")
}

func defaultDownloadDir() string {
	h, _ := os.UserHomeDir()
	return filepath.Join(h, "Downloads", "TezDM")
}

func findBrowser() (string, bool) {
	var chromes, edges []string
	for _, env := range []string{"ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"} {
		if b := os.Getenv(env); b != "" {
			chromes = append(chromes, filepath.Join(b, "Google", "Chrome", "Application", "chrome.exe"))
			edges = append(edges, filepath.Join(b, "Microsoft", "Edge", "Application", "msedge.exe"))
		}
	}
	for _, p := range chromes {
		if exists(p) {
			return p, true
		}
	}
	for _, p := range edges {
		if exists(p) {
			return p, false
		}
	}
	return "", false
}

func run(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	hideWindow(cmd)
	return cmd.Start()
}

// Open the app as its own window (no address bar)
func openAppWindow(url string, w, h int) {
	if b, _ := findBrowser(); b != "" {
		exec.Command(b, "--app="+url, "--window-size="+itoa(w)+","+itoa(h)).Start()
		return
	}
	openURL(url)
}

func openInChrome(url string) {
	if b, isChrome := findBrowser(); b != "" && isChrome {
		exec.Command(b, url).Start()
		return
	}
	openURL(url)
}

func openURL(u string) { run("rundll32", "url.dll,FileProtocolHandler", u) }

func openFile(p string) { run("rundll32", "url.dll,FileProtocolHandler", p) }

func showInFolder(p string) {
	if exists(p) {
		exec.Command("explorer", "/select,", p).Start()
	} else {
		exec.Command("explorer", filepath.Dir(p)).Start()
	}
}

func openFolder(p string) {
	os.MkdirAll(p, 0o755)
	exec.Command("explorer", p).Start()
}

const runKey = `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

func getAutostart() bool {
	cmd := exec.Command("reg", "query", runKey, "/v", "TezDM")
	hideWindow(cmd)
	return cmd.Run() == nil
}

func setAutostart(on bool) {
	exe, _ := os.Executable()
	var cmd *exec.Cmd
	if on {
		cmd = exec.Command("reg", "add", runKey, "/v", "TezDM", "/t", "REG_SZ", "/d", `"`+exe+`" --minimized`, "/f")
	} else {
		cmd = exec.Command("reg", "delete", runKey, "/v", "TezDM", "/f")
	}
	hideWindow(cmd)
	cmd.Run()
}

func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

func setClipboard(s string) {
	cmd := exec.Command("powershell", "-NoProfile", "-Command", "Set-Clipboard -Value "+psQuote(s))
	hideWindow(cmd)
	cmd.Run()
}

// Drive letters that exist (C:\, D:\, ...)
func drives() []string {
	var out []string
	for c := 'A'; c <= 'Z'; c++ {
		d := string(c) + `:\`
		if _, err := os.Stat(d); err == nil {
			out = append(out, d)
		}
	}
	return out
}

// Kill a process and all its children (yt-dlp.exe runs a child process)
func killTree(cmd *exec.Cmd) error {
	if cmd.Process == nil {
		return nil
	}
	k := exec.Command("taskkill", "/T", "/F", "/PID", itoa(cmd.Process.Pid))
	hideWindow(k)
	if k.Run() != nil {
		return cmd.Process.Kill()
	}
	return nil
}
