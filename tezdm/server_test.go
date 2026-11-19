package main

// Folder browser API (used by Options and the download window)

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func uiRequest(t *testing.T, h http.Handler, method, path string, body any) map[string]any {
	t.Helper()
	var buf bytes.Buffer
	if body != nil {
		json.NewEncoder(&buf).Encode(body)
	}
	req := httptest.NewRequest(method, path, &buf)
	req.Header.Set("X-TezDM", "1")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var out map[string]any
	json.Unmarshal(rec.Body.Bytes(), &out)
	if rec.Code != 200 {
		t.Fatalf("%s %s: HTTP %d %v", method, path, rec.Code, out)
	}
	return out
}

func TestFolderBrowser(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "Movies"), 0o755)
	os.MkdirAll(filepath.Join(dir, "apps"), 0o755)
	os.WriteFile(filepath.Join(dir, "file.txt"), []byte("x"), 0o644)
	s := &Server{m: NewManager(filepath.Join(t.TempDir(), "data"))}
	h := s.routes()

	// list a folder: only sub-folders, sorted
	r := uiRequest(t, h, "GET", "/api/dirs?path="+url.QueryEscape(dir), nil)
	got, _ := json.Marshal(r["dirs"])
	if string(got) != `["apps","Movies"]` {
		t.Fatalf("dirs = %s", got)
	}

	// create a folder
	r = uiRequest(t, h, "POST", "/api/mkdir", map[string]string{"Path": dir, "Name": "New: folder"})
	np, _ := r["path"].(string)
	if st, err := os.Stat(np); err != nil || !st.IsDir() {
		t.Fatalf("mkdir did not create %q", np)
	}

	// top level: drives (C:\ on Windows) and common folders
	r = uiRequest(t, h, "GET", "/api/dirs", nil)
	drv, _ := json.Marshal(r["drives"])
	if runtime.GOOS == "windows" && !strings.Contains(string(drv), `C:\\`) {
		t.Fatalf("drives = %s, want C:\\", drv)
	}

	// a drive root has no parent, so "Up" goes back to the drive list
	root := "/"
	if runtime.GOOS == "windows" {
		root = `C:\`
	}
	r = uiRequest(t, h, "GET", "/api/dirs?path="+url.QueryEscape(root), nil)
	if r["parent"] != "" {
		t.Fatalf("parent of %s = %v, want empty", root, r["parent"])
	}

	// requests from other websites are refused
	req := httptest.NewRequest("GET", "/api/dirs", nil)
	req.Header.Set("Origin", "https://evil.example")
	req.Header.Set("X-TezDM", "1")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 403 {
		t.Fatalf("cross-site request got HTTP %d, want 403", rec.Code)
	}
}
