package main

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"os"
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

func TestUpdateMatching(t *testing.T) {
	signed := "https://release-assets.githubusercontent.com/github-production-release-asset/1/abc?sp=r&rscd=attachment%3B+filename%3DTezDM_Setup.exe&rsct=application%2Foctet-stream"
	u := &updater{}
	if u.matches(&Pending{URL: signed}) {
		t.Fatal("matched without the user clicking Update")
	}
	u.requested = time.Now()
	if !u.matches(&Pending{URL: signed}) {
		t.Fatal("signed GitHub download not recognised")
	}
	if !u.matches(&Pending{URL: "https://x.githubusercontent.com/a", OrigURL: UpdateURL}) {
		t.Fatal("original release link not recognised")
	}
	if u.matches(&Pending{URL: "https://evil.example/TezDM_Setup.exe"}) {
		t.Fatal("a non-GitHub site must never be treated as the update")
	}
	u.requested = time.Now().Add(-updateWindow - time.Minute)
	if u.matches(&Pending{URL: signed}) {
		t.Fatal("matched long after Update was clicked")
	}
}

// Full flow: click Update -> Chrome download arrives -> downloaded -> installer started
func TestUpdateFlow(t *testing.T) {
	installer := append([]byte("MZ"), bytes.Repeat([]byte{0x90}, 700*1024)...)
	page := []byte("<html>not signed in</html>")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "html") {
			w.Header().Set("Content-Type", "text/html")
			w.Write(page)
			return
		}
		w.Header().Set("Content-Disposition", "attachment; filename=TezDM_Setup.exe")
		http.ServeContent(w, r, "TezDM_Setup.exe", time.Time{}, bytes.NewReader(installer))
	}))
	defer srv.Close()

	t.Setenv("TEZDM_DATA", t.TempDir()) // updates folder (non-Windows test builds)
	t.Setenv("LOCALAPPDATA", t.TempDir())
	started := make(chan string, 1)
	startInstaller = func(path string, args ...string) error { started <- path + " " + strings.Join(args, " "); return nil }
	defer func() { startInstaller = startDetached }()

	s := &Server{m: NewManager(filepath.Join(t.TempDir(), "data"))}
	h := s.routes()
	uiRequest(t, h, "POST", "/api/update/start", map[string]string{})

	// the download Chrome hands over (test server stands in for GitHub's file host)
	p := &Pending{URL: srv.URL + "/asset?rscd=attachment%3B+filename%3DTezDM_Setup.exe", OrigURL: UpdateURL}
	upd.mu.Lock()
	upd.requested = time.Now()
	upd.mu.Unlock()
	if !strings.HasPrefix(p.URL, "http://127.0.0.1") {
		t.Skip("unexpected test server address")
	}
	s.startUpdateDownload(p)
	select {
	case got := <-started:
		if !strings.HasSuffix(got, "TezDM_Setup.exe /S") {
			t.Fatalf("installer started as %q", got)
		}
		if !strings.Contains(got, filepath.Join("TezDM", "updates")) && !strings.Contains(got, "updates") {
			t.Fatalf("installer not in the updates folder: %q", got)
		}
	case <-time.After(30 * time.Second):
		t.Fatalf("installer was not started (status %q: %s)", upd.status, upd.detail)
	}

	// a web page (not signed in to GitHub) must never be run
	os.Remove(filepath.Join(updatesDir(), "TezDM_Setup.exe"))
	upd.set("", "")
	s.startUpdateDownload(&Pending{URL: srv.URL + "/html/TezDM_Setup.exe"})
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		upd.mu.Lock()
		st := upd.status
		upd.mu.Unlock()
		if st == "error" {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	select {
	case got := <-started:
		t.Fatalf("ran something that is not an installer: %s", got)
	default:
	}
	if upd.status != "error" {
		t.Fatalf("status = %q, want error", upd.status)
	}
}
