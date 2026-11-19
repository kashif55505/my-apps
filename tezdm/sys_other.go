//go:build !windows

package main

// Linux/Mac: for testing only

import (
	"os"
	"os/exec"
	"path/filepath"
)

func hideWindow(cmd *exec.Cmd) {}

func dataDir() string {
	if d := os.Getenv("TEZDM_DATA"); d != "" {
		return d
	}
	h, _ := os.UserHomeDir()
	return filepath.Join(h, ".tezdm")
}

func defaultDownloadDir() string {
	h, _ := os.UserHomeDir()
	return filepath.Join(h, "Downloads", "TezDM")
}

func openAppWindow(url string, w, h int) { openURL(url) }
func openInChrome(url string)            { openURL(url) }
func openURL(u string)                   { exec.Command("xdg-open", u).Start() }
func openFile(p string)                  { openURL(p) }
func showInFolder(p string)              { openURL(filepath.Dir(p)) }
func openFolder(p string)                { os.MkdirAll(p, 0o755); openURL(p) }
func getAutostart() bool                 { return false }
func setAutostart(on bool)               {}
func setClipboard(s string)              {}
func drives() []string                   { return []string{"/"} }

func killTree(cmd *exec.Cmd) error {
	if cmd.Process == nil {
		return nil
	}
	return cmd.Process.Kill()
}
