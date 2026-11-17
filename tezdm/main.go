package main

// TezDM - Fast Download Manager
// Runs in the background; the UI opens in a Chrome app window.

import (
	"embed"
	"encoding/json"
	"io/fs"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

const Port = 47615

// Version is set at build time from the VERSION file (-ldflags "-X main.Version=...")
var Version = "dev"

var (
	//go:embed web
	webFS embed.FS
	//go:embed extension
	extFS embed.FS
)

func itoa(i int) string { return strconv.Itoa(i) }

func baseURL() string { return "http://127.0.0.1:" + itoa(Port) }

func extDir() string { return filepath.Join(dataDir(), "extension") }

// Extension files are embedded; write them to disk on every start (keeps them updated)
func writeExtension() error {
	dst := extDir()
	return fs.WalkDir(extFS, "extension", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel("extension", filepath.FromSlash(p))
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		b, err := extFS.ReadFile(p)
		if err != nil {
			return err
		}
		if old, e := os.ReadFile(target); e == nil && string(old) == string(b) {
			return nil
		}
		return os.WriteFile(target, b, 0o644)
	})
}

// Version of the embedded extension (the extension reloads itself when it differs)
func extVersion() string {
	b, err := extFS.ReadFile("extension/manifest.json")
	if err != nil {
		return ""
	}
	var m struct {
		Version string `json:"version"`
	}
	json.Unmarshal(b, &m)
	return m.Version
}

func hasArg(a string) bool {
	for _, x := range os.Args[1:] {
		if x == a {
			return true
		}
	}
	return false
}

func main() {
	minimized := hasArg("--minimized")
	setupExt := hasArg("--setup-extension")

	ln, err := net.Listen("tcp", "127.0.0.1:"+itoa(Port))
	if err != nil {
		// TezDM is already running: just open its window
		c := http.Client{Timeout: 3 * time.Second}
		path := "/show"
		if setupExt {
			path = "/show?ext=1"
		}
		if !minimized {
			req, _ := http.NewRequest("GET", baseURL()+path, nil)
			req.Header.Set("X-TezDM", "1")
			c.Do(req)
		}
		return
	}

	os.MkdirAll(dataDir(), 0o755)
	writeExtension()
	m := NewManager(dataDir())
	go EnsureTools()

	srv := &Server{m: m}
	go func() {
		time.Sleep(300 * time.Millisecond)
		if setupExt {
			srv.setupExtension()
		}
		if !minimized {
			srv.openMain()
		}
	}()
	http.Serve(ln, srv.routes())
}
