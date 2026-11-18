package main

// Download integrity tests: every file must arrive byte-for-byte identical,
// even with servers that misbehave. Run with: go test ./...

import (
	"bytes"
	"crypto/md5"
	"fmt"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

var rangeRe = regexp.MustCompile(`bytes=(\d+)-(\d*)`)

// testServer serves data and misbehaves according to mode:
//
//	normal    - correct server
//	drop      - connections break mid-stream
//	cap       - sends at most 700 KB per range request
//	ignoreend - ignores the end of the range, sends to EOF
//	align     - starts ranges at a 64 KB boundary before the requested start
//	norange   - no range support
func testServer(t *testing.T, data []byte, mode string) *httptest.Server {
	etag := fmt.Sprintf(`"%x"`, md5.Sum(data))
	var reqs int64
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := int64(len(data))
		a, b := int64(0), n-1
		m := rangeRe.FindStringSubmatch(r.Header.Get("Range"))
		ranged := m != nil && mode != "norange"
		if ranged {
			a, _ = strconv.ParseInt(m[1], 10, 64)
			if m[2] != "" {
				b, _ = strconv.ParseInt(m[2], 10, 64)
			}
			switch mode {
			case "cap":
				if b > a+700*1024-1 {
					b = a + 700*1024 - 1
				}
			case "ignoreend":
				b = n - 1
			case "align":
				a = a / 65536 * 65536
			}
		}
		body := data[a : b+1]
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("ETag", etag)
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		if ranged {
			w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", a, b, n))
			w.WriteHeader(206)
		} else {
			w.WriteHeader(200)
		}
		rnd := rand.New(rand.NewSource(atomic.AddInt64(&reqs, 1)))
		for i := 0; i < len(body); i += 32 * 1024 {
			j := i + 32*1024
			if j > len(body) {
				j = len(body)
			}
			if mode == "drop" && rnd.Intn(100) == 0 {
				panic(http.ErrAbortHandler) // break the connection mid-stream
			}
			if _, err := w.Write(body[i:j]); err != nil {
				return
			}
			if rnd.Intn(4) == 0 {
				time.Sleep(time.Millisecond)
			}
		}
	}))
}

func waitDone(t *testing.T, task *Task, limit time.Duration) {
	t.Helper()
	deadline := time.Now().Add(limit)
	for time.Now().Before(deadline) {
		task.mu.Lock()
		st, running, e := task.Status, task.running, task.Error
		task.mu.Unlock()
		if !running && (st == "completed" || st == "error") {
			if st == "error" {
				t.Fatalf("download failed: %s", e)
			}
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("download did not finish in %s", limit)
}

func TestDownloadIntegrity(t *testing.T) {
	data := make([]byte, 9*1024*1024+12345) // odd size on purpose
	rand.New(rand.NewSource(1)).Read(data)
	for _, mode := range []string{"normal", "drop", "cap", "ignoreend", "align", "norange"} {
		mode := mode
		t.Run(mode, func(t *testing.T) {
			srv := testServer(t, data, mode)
			defer srv.Close()
			dir := t.TempDir()
			m := NewManager(filepath.Join(dir, "data"))
			m.SetSettings(Settings{DownloadDir: filepath.Join(dir, "out"), Connections: 32, MaxParallel: 3})
			task := m.Add(srv.URL+"/setup.exe", "setup.exe", nil, "file", "", "", true)
			waitDone(t, task, 2*time.Minute)
			got, err := os.ReadFile(task.FullPath())
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(got, data) {
				t.Fatalf("file is corrupted (%d bytes, want %d)", len(got), len(data))
			}
		})
	}
}

// Pause half-way, then resume: the file must still be identical
func TestPauseResumeIntegrity(t *testing.T) {
	data := make([]byte, 12*1024*1024)
	rand.New(rand.NewSource(2)).Read(data)
	srv := testServer(t, data, "drop")
	defer srv.Close()
	dir := t.TempDir()
	m := NewManager(filepath.Join(dir, "data"))
	m.SetSettings(Settings{DownloadDir: filepath.Join(dir, "out"), Connections: 16, MaxParallel: 3})
	task := m.Add(srv.URL+"/big.exe", "big.exe", nil, "file", "", "", true)
	for i := 0; i < 200; i++ {
		if task.View().Progress > 20 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	task.Pause()
	for task.IsRunning() {
		time.Sleep(20 * time.Millisecond)
	}
	task.mu.Lock()
	task.Status = "queued"
	task.mu.Unlock()
	waitDone(t, task, 2*time.Minute)
	got, _ := os.ReadFile(task.FullPath())
	if !bytes.Equal(got, data) {
		t.Fatal("file is corrupted after pause/resume")
	}
}

// A web page instead of the file must be reported, not saved as an .exe
func TestErrorPageIsRejected(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write([]byte("<html>link expired</html>"))
	}))
	defer srv.Close()
	dir := t.TempDir()
	m := NewManager(filepath.Join(dir, "data"))
	m.SetSettings(Settings{DownloadDir: filepath.Join(dir, "out"), Connections: 8, MaxParallel: 3})
	task := m.Add(srv.URL+"/app.exe", "app.exe", nil, "file", "", "", true)
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) && task.View().Status != "error" {
		time.Sleep(50 * time.Millisecond)
	}
	if task.View().Status != "error" {
		t.Fatal("expected an error for a web page")
	}
	if _, err := os.Stat(filepath.Join(dir, "out", "app.exe")); err == nil {
		t.Fatal("web page was saved as app.exe")
	}
}
