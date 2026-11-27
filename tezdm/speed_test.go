package main

// Speed behaviour: real parallel connections, and servers that limit connections

import (
	"bytes"
	"crypto/tls"
	"fmt"
	"math/rand"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Every part must get its own TCP connection, even on an HTTP/2 server
// (with HTTP/2 all parts would share one connection: no speed gain on servers
// that limit each connection).
func TestPartsUseSeparateConnections(t *testing.T) {
	data := make([]byte, 16*1024*1024)
	rand.New(rand.NewSource(3)).Read(data)
	var conns int64
	srv := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.ServeContent(w, r, "f.bin", time.Time{}, bytes.NewReader(data))
	}))
	srv.EnableHTTP2 = true
	srv.Config.ConnState = func(c net.Conn, st http.ConnState) {
		if st == http.StateNew {
			atomic.AddInt64(&conns, 1)
		}
	}
	srv.StartTLS()
	defer srv.Close()

	tr := httpClient.Transport.(*http.Transport)
	old := tr.TLSClientConfig
	tr.TLSClientConfig = &tls.Config{RootCAs: srv.Client().Transport.(*http.Transport).TLSClientConfig.RootCAs}
	tr.ForceAttemptHTTP2 = true // even when HTTP/2 is offered and forced, parts must not share a connection
	defer func() { tr.TLSClientConfig = old; tr.ForceAttemptHTTP2 = false; tr.CloseIdleConnections() }()

	dir := t.TempDir()
	m := NewManager(filepath.Join(dir, "data"))
	m.SetSettings(Settings{DownloadDir: filepath.Join(dir, "out"), Connections: 8, MaxParallel: 3})
	task := m.Add(srv.URL+"/f.bin", "f.bin", nil, "file", "", "", true)
	waitDone(t, task, time.Minute)
	got, _ := os.ReadFile(task.FullPath())
	if !bytes.Equal(got, data) {
		t.Fatal("file corrupted")
	}
	if n := atomic.LoadInt64(&conns); n < 6 {
		t.Fatalf("only %d TCP connection(s) used for 8 parts: parts are sharing connections", n)
	}
	t.Logf("8 parts used %d separate TCP connections", conns)
}

// A server that allows only 4 connections at a time and refuses the rest (503),
// like many video and file hosts
func TestServerConnectionLimit(t *testing.T) {
	data := make([]byte, 24*1024*1024)
	rand.New(rand.NewSource(4)).Read(data)
	var mu sync.Mutex
	active, refused, served := 0, 0, 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		if r.Header.Get("Range") != "" && r.Header.Get("Range") != "bytes=0-" && active >= 4 {
			refused++
			mu.Unlock()
			http.Error(w, "too many connections", 503)
			return
		}
		active++
		served++
		mu.Unlock()
		defer func() { mu.Lock(); active--; mu.Unlock() }()
		w.Header().Set("ETag", `"v1"`)
		// each connection limited to ~2 MB/s, like a throttled host
		http.ServeContent(&slowWriter{ResponseWriter: w, bps: 2 << 20}, r, "f.bin", time.Time{}, bytes.NewReader(data))
	}))
	defer srv.Close()
	dir := t.TempDir()
	m := NewManager(filepath.Join(dir, "data"))
	m.SetSettings(Settings{DownloadDir: filepath.Join(dir, "out"), Connections: 16, MaxParallel: 3})
	start := time.Now()
	task := m.Add(srv.URL+"/f.bin", "f.bin", nil, "file", "", "", true)
	waitDone(t, task, 3*time.Minute)
	took := time.Since(start)
	got, _ := os.ReadFile(task.FullPath())
	if !bytes.Equal(got, data) {
		t.Fatal("file corrupted")
	}
	mu.Lock()
	defer mu.Unlock()
	t.Logf("24 MB with a 4-connection server: %.1f s, %d refused requests, %d served", took.Seconds(), refused, served)
	// 4 connections x 2 MB/s = 8 MB/s -> about 3 s; allow generous slack, but far below
	// what endless retries with growing waits would cost
	if took > 20*time.Second {
		t.Fatalf("too slow: %.1f s", took.Seconds())
	}
	if refused > 60 {
		t.Fatalf("kept hammering the server: %d refused requests", refused)
	}
}

type slowWriter struct {
	http.ResponseWriter
	bps int
}

func (s *slowWriter) Write(p []byte) (int, error) {
	total := 0
	for len(p) > 0 {
		n := len(p)
		if n > 64*1024 {
			n = 64 * 1024
		}
		w, err := s.ResponseWriter.Write(p[:n])
		total += w
		if err != nil {
			return total, err
		}
		time.Sleep(time.Duration(float64(n) / float64(s.bps) * float64(time.Second)))
		p = p[n:]
	}
	return total, nil
}

var _ = fmt.Sprint
