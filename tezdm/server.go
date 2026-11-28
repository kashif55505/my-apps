package main

import (
	"encoding/json"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type Server struct {
	m          *Manager
	lastExt    time.Time
	lastUIPoll time.Time
	mu         sync.Mutex
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

// Extension requests: only chrome-extension:// origin (websites are blocked)
func extOK(r *http.Request) bool {
	o := r.Header.Get("Origin")
	return o == "" || strings.HasPrefix(o, "chrome-extension://") || strings.HasPrefix(o, "moz-extension://")
}

// UI requests: only from our own pages (websites cannot send the custom header without CORS)
func uiOK(r *http.Request) bool {
	o := r.Header.Get("Origin")
	return r.Header.Get("X-TezDM") == "1" && (o == "" || o == baseURL())
}

// Any request from the extension proves it is installed
func (s *Server) extSeen(r *http.Request) {
	if r.Header.Get("X-TezDM-Ext") == "1" || strings.HasPrefix(r.Header.Get("Origin"), "chrome-extension://") {
		s.mu.Lock()
		s.lastExt = time.Now()
		s.mu.Unlock()
		s.m.MarkExtSeen()
	}
}

func (s *Server) openMain() { openAppWindow(baseURL()+"/", 920, 540) }

func (s *Server) setupExtension() {
	setClipboard(extDir())
	openInChrome("chrome://extensions/")
	time.Sleep(700 * time.Millisecond)
	openAppWindow(baseURL()+"/guide.html", 760, 820)
}

func (s *Server) routes() http.Handler {
	mux := http.NewServeMux()
	sub, _ := fs.Sub(webFS, "web")
	static := http.FileServer(http.FS(sub))

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Frame-Options", "DENY")
		static.ServeHTTP(w, r)
	})

	// ----- extension API -----
	mux.HandleFunc("/ping", func(w http.ResponseWriter, r *http.Request) {
		if !extOK(r) {
			writeJSON(w, 403, map[string]string{"error": "forbidden"})
			return
		}
		s.extSeen(r)
		writeJSON(w, 200, map[string]any{"app": "TezDM", "version": Version, "ext_version": extVersion()})
	})

	mux.HandleFunc("/formats", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || !extOK(r) {
			writeJSON(w, 403, map[string]string{"error": "forbidden"})
			return
		}
		s.extSeen(r)
		var p Pending
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2<<20)).Decode(&p); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		info, err := GetVideoInfo(r.Context(), p.URL, p.headers())
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, info)
	})

	mux.HandleFunc("/show", func(w http.ResponseWriter, r *http.Request) {
		if !extOK(r) {
			writeJSON(w, 403, map[string]string{"error": "forbidden"})
			return
		}
		s.extSeen(r)
		if r.URL.Query().Get("ext") == "1" && !s.m.Settings().ExtSeen {
			go s.setupExtension() // first install: help set up the extension
		} else {
			go s.openMain()
		}
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	mux.HandleFunc("/add", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || !extOK(r) {
			writeJSON(w, 403, map[string]string{"error": "forbidden"})
			return
		}
		var p Pending
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2<<20)).Decode(&p); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		lu := strings.ToLower(p.URL)
		if !strings.HasPrefix(lu, "http://") && !strings.HasPrefix(lu, "https://") {
			writeJSON(w, 400, map[string]string{"error": "invalid url"})
			return
		}
		s.extSeen(r)
		if p.Kind == "" {
			p.Kind = "file"
		}
		if p.Format != "" {
			// quality was already chosen on the video button: start right away
			s.addPending(&p, "", "", true)
		} else if s.m.Settings().Confirm {
			s.askUser(&p)
		} else {
			s.addPending(&p, "", "", true)
		}
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	// The extension reports the newest version it found on the release page
	// (older extensions report versions here; TezDM now checks by itself)
	mux.HandleFunc("/update-info", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	// ----- UI API -----
	api := func(path string, h func(w http.ResponseWriter, r *http.Request)) {
		mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
			if !uiOK(r) {
				writeJSON(w, 403, map[string]string{"error": "forbidden"})
				return
			}
			h(w, r)
		})
	}

	api("/api/state", func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		s.lastUIPoll = time.Now()
		extConnected := time.Since(s.lastExt) < 3*time.Minute
		s.mu.Unlock()
		var views []TaskView
		for _, t := range s.m.Sorted() {
			views = append(views, t.View())
		}
		ts, td := tools.Get()
		s.m.mu.Lock()
		pend := []string{}
		for id := range s.m.pending {
			pend = append(pend, id)
		}
		s.m.mu.Unlock()
		latest, avail := upd.Latest()
		upd.mu.Lock()
		ust, udetail, cerr := upd.status, upd.detail, upd.checkErr
		var notes string
		if upd.info != nil {
			notes = upd.info.Notes
		}
		upd.mu.Unlock()
		writeJSON(w, 200, map[string]any{"pending": pend,
			"latest": latest, "updateAvailable": avail, "updateNotes": notes, "updateCheckError": cerr,
			"updateStatus": ust, "updateDetail": udetail, "releasePage": ReleasePage,
			"tasks": views, "settings": s.m.Settings(), "autostart": getAutostart(),
			"tools": ts, "toolsDetail": td, "ext": extConnected, "extSeen": s.m.Settings().ExtSeen, "extPath": extDir(),
			"version": Version,
		})
	})

	api("/api/pending", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			ID, Action, Filename, Dir, Format, Kind string
		}
		if r.Method == "GET" {
			s.m.mu.Lock()
			p := s.m.pending[r.URL.Query().Get("id")]
			s.m.mu.Unlock()
			if p == nil {
				writeJSON(w, 404, map[string]string{"error": "not found"})
				return
			}
			dir := p.Dir
			if dir == "" {
				dir = s.m.Settings().DownloadDir
			}
			writeJSON(w, 200, map[string]any{"url": p.URL, "filename": p.Filename, "kind": p.Kind, "dir": dir})
			return
		}
		json.NewDecoder(r.Body).Decode(&q)
		s.m.mu.Lock()
		p := s.m.pending[q.ID]
		delete(s.m.pending, q.ID)
		s.m.mu.Unlock()
		if p != nil && q.Action != "cancel" {
			p.Format = q.Format
			if q.Kind == "file" || q.Kind == "video" {
				p.Kind = q.Kind
			}
			s.addPending(p, q.Filename, q.Dir, q.Action == "start")
		}
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	api("/api/add", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			URL, Kind, Dir string
		}
		json.NewDecoder(r.Body).Decode(&q)
		q.URL = strings.TrimSpace(q.URL)
		lu := strings.ToLower(q.URL)
		if !strings.HasPrefix(lu, "http://") && !strings.HasPrefix(lu, "https://") {
			writeJSON(w, 400, map[string]string{"error": "The link must start with http:// or https://"})
			return
		}
		if q.Kind != "video" {
			q.Kind = "file"
		}
		s.askUser(&Pending{URL: q.URL, Kind: q.Kind, Dir: q.Dir})
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	api("/api/formats", func(w http.ResponseWriter, r *http.Request) {
		s.m.mu.Lock()
		p := s.m.pending[r.URL.Query().Get("id")]
		s.m.mu.Unlock()
		if p == nil {
			writeJSON(w, 404, map[string]string{"error": "not found"})
			return
		}
		info, err := GetVideoInfo(r.Context(), p.URL, p.headers())
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, info)
	})

	api("/api/task", func(w http.ResponseWriter, r *http.Request) {
		t := s.m.Get(r.URL.Query().Get("id"))
		if t == nil {
			writeJSON(w, 404, map[string]string{"error": "not found"})
			return
		}
		writeJSON(w, 200, t.Detail())
	})

	api("/api/action", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			IDs    []string
			Action string
		}
		json.NewDecoder(r.Body).Decode(&q)
		if q.Action == "pauseall" || q.Action == "resumeall" {
			for _, t := range s.m.Sorted() {
				q.IDs = append(q.IDs, t.ID)
			}
			q.Action = strings.TrimSuffix(q.Action, "all")
		}
		for _, id := range q.IDs {
			t := s.m.Get(id)
			if t == nil {
				continue
			}
			switch q.Action {
			case "pause":
				t.Pause()
			case "resume":
				t.mu.Lock()
				if (t.Status == "paused" || t.Status == "error") && !t.running {
					t.Status = "queued"
				}
				t.mu.Unlock()
			case "delete":
				s.m.Remove(id, false)
			case "deletefile":
				s.m.Remove(id, true)
			case "open":
				if p := t.FullPath(); p != "" {
					openFile(p)
				}
			case "folder":
				if p := t.FullPath(); p != "" {
					showInFolder(p)
				} else {
					openFolder(t.Dir)
				}
			case "progress":
				go s.openProgress(id)
			case "redownload":
				t.mu.Lock()
				u, h, k, d, f := t.URL, t.Headers, t.Kind, t.Dir, t.Format
				t.mu.Unlock()
				nt := s.m.Add(u, "", h, k, d, f, true)
				go s.openProgress(nt.ID)
			}
		}
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	api("/api/settings", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			Settings  Settings
			Autostart bool
		}
		if err := json.NewDecoder(r.Body).Decode(&q); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		s.m.SetSettings(q.Settings)
		setAutostart(q.Autostart)
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	// Folder browser shown inside TezDM's own window (no separate dialog that can hide behind Chrome)
	api("/api/dirs", func(w http.ResponseWriter, r *http.Request) {
		p := strings.TrimSpace(r.URL.Query().Get("path"))
		if p == "" {
			writeJSON(w, 200, map[string]any{"path": "", "places": userPlaces(), "drives": drives()})
			return
		}
		p = filepath.Clean(p)
		entries, err := os.ReadDir(p)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "Cannot open this folder: " + err.Error()})
			return
		}
		dirs := []string{}
		for _, e := range entries {
			name := e.Name()
			if strings.HasPrefix(name, ".") || strings.HasPrefix(name, "$") || name == "System Volume Information" {
				continue
			}
			isDir := e.IsDir()
			if !isDir && e.Type()&os.ModeSymlink != 0 {
				if st, err := os.Stat(filepath.Join(p, name)); err == nil {
					isDir = st.IsDir()
				}
			}
			if isDir {
				dirs = append(dirs, name)
			}
		}
		sort.Slice(dirs, func(a, b int) bool { return strings.ToLower(dirs[a]) < strings.ToLower(dirs[b]) })
		parent := filepath.Dir(p)
		if parent == p {
			parent = "" // drive root: "up" goes to the list of places and drives
		}
		writeJSON(w, 200, map[string]any{"path": p, "parent": parent, "dirs": dirs})
	})

	api("/api/mkdir", func(w http.ResponseWriter, r *http.Request) {
		var q struct{ Path, Name string }
		json.NewDecoder(r.Body).Decode(&q)
		name := sanitize(strings.TrimSpace(q.Name))
		if q.Path == "" || name == "" {
			writeJSON(w, 400, map[string]string{"error": "Open a folder first, then type a name"})
			return
		}
		np := filepath.Join(filepath.Clean(q.Path), name)
		if err := os.Mkdir(np, 0o755); err != nil {
			writeJSON(w, 400, map[string]string{"error": "Could not create the folder: " + err.Error()})
			return
		}
		writeJSON(w, 200, map[string]string{"path": np})
	})

	api("/api/openfolder", func(w http.ResponseWriter, r *http.Request) {
		openFolder(s.m.Settings().DownloadDir)
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	api("/api/ext-setup", func(w http.ResponseWriter, r *http.Request) {
		go s.setupExtension()
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	api("/api/copy-ext-path", func(w http.ResponseWriter, r *http.Request) {
		setClipboard(extDir())
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	// Update: ask Chrome (signed in to GitHub) to download the latest installer
	// Update: download the newest installer from the public repository and install it
	api("/api/update/start", func(w http.ResponseWriter, r *http.Request) {
		if err := s.StartUpdate(r.Context()); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]bool{"ok": true})
	})

	api("/api/update/check", func(w http.ResponseWriter, r *http.Request) {
		upd.Check(r.Context())
		latest, avail := upd.Latest()
		writeJSON(w, 200, map[string]any{"latest": latest, "updateAvailable": avail})
	})

	api("/api/exit", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]bool{"ok": true})
		go func() {
			s.m.PauseAll()
			s.m.WaitStopped(3 * time.Second)
			s.m.Save()
			os.Exit(0)
		}()
	})
	return mux
}

func (s *Server) askUser(p *Pending) {
	p.ID = newID()
	s.m.mu.Lock()
	s.m.pending[p.ID] = p
	s.m.mu.Unlock()
	h := 430
	if p.Kind == "video" {
		h = 560
	}
	go openAppWindow(baseURL()+"/confirm.html?id="+p.ID, 640, h)
}

func (s *Server) openProgress(id string) {
	openAppWindow(baseURL()+"/progress.html?id="+id, 600, 580)
}

func (s *Server) addPending(p *Pending, filename, dir string, start bool) *Task {
	if filename == "" {
		filename = p.Filename
	}
	if dir == "" {
		dir = p.Dir
	}
	t := s.m.Add(p.URL, filename, p.headers(), p.Kind, dir, p.Format, start)
	if start && s.m.Settings().ShowProg {
		go s.openProgress(t.ID)
	}
	return t
}

type place struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

// Common folders shown at the top of the folder browser
func userPlaces() []place {
	home, _ := os.UserHomeDir()
	var out []place
	for _, n := range []string{"Desktop", "Downloads", "Documents", "Videos", "Music", "Pictures"} {
		p := filepath.Join(home, n)
		if st, err := os.Stat(p); err == nil && st.IsDir() {
			out = append(out, place{n, p})
		}
	}
	if def := defaultDownloadDir(); exists(def) {
		out = append([]place{{"TezDM downloads", def}}, out...)
	}
	return append(out, place{"Home", home})
}
