package main

import (
	"archive/zip"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestExtractAllRefusesEscapingPaths(t *testing.T) {
	dir := t.TempDir()
	z := filepath.Join(dir, "bad.zip")
	f, _ := os.Create(z)
	w := zip.NewWriter(f)
	ok, _ := w.Create("_internal/ok.txt")
	ok.Write([]byte("ok"))
	bad, _ := w.Create("../evil.txt")
	bad.Write([]byte("evil"))
	w.Close()
	f.Close()
	if err := extractAll(z, filepath.Join(dir, "out")); err == nil {
		t.Fatal("a zip entry outside the folder was accepted")
	}
	if exists(filepath.Join(dir, "evil.txt")) {
		t.Fatal("file written outside the folder")
	}
}

// Real download of the unpacked yt-dlp build (Windows CI only: TEZDM_NET_TESTS=1)
func TestYtdlpFolderInstall(t *testing.T) {
	if os.Getenv("TEZDM_NET_TESTS") == "" || runtime.GOOS != "windows" {
		t.Skip("network test, runs on the Windows CI machine")
	}
	t.Setenv("LOCALAPPDATA", t.TempDir())
	os.MkdirAll(binDir(), 0o755)
	v, err := latestYtdlpVersion()
	if err != nil {
		t.Fatal(err)
	}
	if err := installYtdlpFolder(v, nil); err != nil {
		t.Fatal(err)
	}
	p := ytdlpPath()
	if !strings.Contains(p, filepath.Join("bin", "yt-dlp", "yt-dlp.exe")) {
		t.Fatalf("not using the unpacked build: %s", p)
	}
	if installedYtdlpVersion() != v {
		t.Fatalf("installed version %q, want %q", installedYtdlpVersion(), v)
	}
	for i := 1; i <= 3; i++ {
		start := time.Now()
		out, err := exec.Command(p, "--version").Output()
		if err != nil {
			t.Fatal(err)
		}
		t.Logf("run %d: yt-dlp %s started in %.2f s", i, strings.TrimSpace(string(out)), time.Since(start).Seconds())
	}
	// installing again (daily update) must swap cleanly
	if err := installYtdlpFolder(v, nil); err != nil {
		t.Fatal("second install (update) failed:", err)
	}
	if !exists(ytdlpPath()) || exists(ytdlpFolder()+".old") || exists(ytdlpFolder()+".new") {
		t.Fatal("update left the folders in a bad state")
	}
}
