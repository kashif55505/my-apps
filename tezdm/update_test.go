package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNewerVersion(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"2.5.0", "2.4.1", true}, {"2.4.1", "2.5.0", false}, {"2.4.1", "2.4.1", false},
		{"2.10.0", "2.9.9", true}, {"3", "2.99.99", true}, {"2.4.1", "dev", true}, {"v2.5.0", "2.4.9", true},
	}
	for _, c := range cases {
		if got := newerVersion(c.a, c.b); got != c.want {
			t.Errorf("newerVersion(%q, %q) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}

func waitUpdate(t *testing.T, want string, started chan string) (string, string) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case got := <-started:
			return "started", got
		default:
		}
		upd.mu.Lock()
		st, d := upd.status, upd.detail
		upd.mu.Unlock()
		if st == want && want == "error" {
			return st, d
		}
		time.Sleep(50 * time.Millisecond)
	}
	return "timeout", ""
}

// Update without any sign-in: version.json -> download with TezDM's engine -> checksum -> installer
func TestPublicUpdateFlow(t *testing.T) {
	installer := append([]byte("MZ"), bytes.Repeat([]byte{0x90}, 900*1024)...)
	sum := sha256.Sum256(installer)
	good := hex.EncodeToString(sum[:])
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "page") {
			w.Header().Set("Content-Type", "text/html")
			w.Write([]byte("<html>not found</html>"))
			return
		}
		http.ServeContent(w, r, "TezDM_Setup.exe", time.Time{}, bytes.NewReader(installer))
	}))
	defer srv.Close()

	t.Setenv("TEZDM_DATA", t.TempDir())
	t.Setenv("LOCALAPPDATA", t.TempDir())
	started := make(chan string, 1)
	startInstaller = func(path string, args ...string) error { started <- path + " " + strings.Join(args, " "); return nil }
	defer func() { startInstaller = startDetached }()
	info := &releaseInfo{Version: "99.0.0", URL: srv.URL + "/TezDM_Setup.exe", SHA256: good}
	oldOpen := openWindow
	openWindow = func(string, int, int) {} // no browser windows in tests
	defer func() { openWindow = oldOpen }()
	oldVersion := Version
	Version = "2.6.3"
	defer func() { Version = oldVersion }()
	origFetch := fetchReleaseInfo
	defer func() { fetchReleaseInfo = origFetch }()
	fetchReleaseInfo = func(context.Context) (*releaseInfo, error) { c := *info; return &c, nil }

	s := &Server{m: NewManager(filepath.Join(t.TempDir(), "data"))}
	h := s.routes()

	// check: the new version is reported
	r := uiRequest(t, h, "POST", "/api/update/check", map[string]string{})
	if r["latest"] != "99.0.0" || r["updateAvailable"] != true {
		t.Fatalf("check = %v", r)
	}

	// 1) good installer: verified and started silently
	uiRequest(t, h, "POST", "/api/update/start", map[string]string{})
	if st, got := waitUpdate(t, "", started); st != "started" || !strings.HasSuffix(got, "TezDM_Setup_99.0.0.exe /S") {
		t.Fatalf("installer not started: %s %q (%s)", st, got, upd.detail)
	}

	// 2) wrong checksum (damaged or replaced file): never run
	upd.set("", "")
	info.SHA256 = strings.Repeat("0", 64)
	upd.Check(context.Background())
	uiRequest(t, h, "POST", "/api/update/start", map[string]string{})
	if st, d := waitUpdate(t, "error", started); st != "error" || !strings.Contains(d, "checksum") {
		t.Fatalf("damaged update: %s %q", st, d)
	}

	// 3) a web page instead of the installer: never run
	upd.set("", "")
	info.URL, info.SHA256 = srv.URL+"/page/TezDM_Setup.exe", good
	upd.Check(context.Background())
	uiRequest(t, h, "POST", "/api/update/start", map[string]string{})
	if st, _ := waitUpdate(t, "error", started); st != "error" {
		t.Fatalf("web page update: %s", st)
	}

	// 4) already up to date: nothing is downloaded
	upd.set("", "")
	info.Version = "0.0.1"
	upd.Check(context.Background())
	req := httptest.NewRequest("POST", "/api/update/start", strings.NewReader("{}"))
	req.Header.Set("X-TezDM", "1")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 400 || !strings.Contains(rec.Body.String(), "newest version") {
		t.Fatalf("up to date: HTTP %d %s", rec.Code, rec.Body.String())
	}
}
