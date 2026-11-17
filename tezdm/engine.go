package main

// TezDM download engine: multi-connection, IDM-style dynamic segmentation,
// pause/resume (even after restart), auto-retry.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	chunkSize  = 128 * 1024
	minSplit   = 1 << 20 // 1 MB
	maxRetries = 30
	defaultUA  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

var errStopped = errors.New("stopped")

type Seg struct {
	Start int64 `json:"s"`
	End   int64 `json:"e"`
	Pos   int64 `json:"p"`
}

type Task struct {
	ID        string            `json:"id"`
	URL       string            `json:"url"`
	FinalURL  string            `json:"final_url"`
	Dir       string            `json:"dir"`
	Filename  string            `json:"filename"`
	Kind      string            `json:"kind"` // file | video
	Status    string            `json:"status"`
	Error     string            `json:"error"`
	Headers   map[string]string `json:"headers"`
	Conns     int               `json:"conns"`
	Size      int64             `json:"size"` // -1 = unknown
	Resumable bool              `json:"resumable"`
	Segs      []*Seg            `json:"segs"`
	Added     int64             `json:"added"`
	Done      int64             `json:"done,omitempty"`   // for video
	Format    string            `json:"format,omitempty"` // video quality: "", "1080", "audio"...
	LastTry   int64             `json:"last_try,omitempty"`

	mu       sync.Mutex
	cancel   context.CancelFunc
	running  bool
	speed    float64
	active   int
	busy     map[*Seg]bool
	Title    string `json:"title,omitempty"`
	estimate int64  // video: expected total size
}

func newID() string { return strconv.FormatInt(time.Now().UnixNano(), 36) }

var badChars = regexp.MustCompile(`[\\/:*?"<>|\x00-\x1f]+`)

func sanitize(name string) string {
	name = strings.Trim(badChars.ReplaceAllString(name, "_"), " .")
	if len([]rune(name)) > 180 {
		r := []rune(name)
		ext := filepath.Ext(name)
		name = string(r[:170]) + ext
	}
	if name == "" {
		return "download"
	}
	return name
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

func uniquePath(p string) string {
	if !exists(p) && !exists(p+".tezpart") {
		return p
	}
	ext := filepath.Ext(p)
	base := strings.TrimSuffix(p, ext)
	for i := 1; ; i++ {
		c := fmt.Sprintf("%s (%d)%s", base, i, ext)
		if !exists(c) && !exists(c+".tezpart") {
			return c
		}
	}
}

var extByType = map[string]string{
	"application/zip": ".zip", "application/x-msdownload": ".exe", "application/pdf": ".pdf",
	"video/mp4": ".mp4", "video/webm": ".webm", "audio/mpeg": ".mp3", "image/jpeg": ".jpg",
	"image/png": ".png", "application/x-rar-compressed": ".rar", "application/vnd.rar": ".rar",
	"application/x-7z-compressed": ".7z", "application/x-iso9660-image": ".iso",
}

func filenameFrom(resp *http.Response) string {
	if cd := resp.Header.Get("Content-Disposition"); cd != "" {
		if _, params, err := mime.ParseMediaType(cd); err == nil && params["filename"] != "" {
			return sanitize(params["filename"])
		}
		if m := regexp.MustCompile(`(?i)filename="?([^";]+)`).FindStringSubmatch(cd); m != nil {
			return sanitize(m[1])
		}
	}
	name, _ := url.PathUnescape(path.Base(resp.Request.URL.Path))
	if name == "" || name == "/" || name == "." {
		name = "download"
	}
	if filepath.Ext(name) == "" {
		ct := strings.TrimSpace(strings.Split(resp.Header.Get("Content-Type"), ";")[0])
		if e, ok := extByType[ct]; ok {
			name += e
		} else if exts, _ := mime.ExtensionsByType(ct); len(exts) > 0 {
			name += exts[0]
		}
	}
	return sanitize(name)
}

// ---------- info (for the UI) ----------

type TaskView struct {
	ID       string  `json:"id"`
	URL      string  `json:"url"`
	Name     string  `json:"name"`
	Dir      string  `json:"dir"`
	Kind     string  `json:"kind"`
	Status   string  `json:"status"`
	Error    string  `json:"error"`
	Size     int64   `json:"size"`
	Done     int64   `json:"done"`
	Speed    float64 `json:"speed"`
	Conns    int     `json:"conns"`
	Running  bool    `json:"running"`
	Added    int64   `json:"added"`
	LastTry  int64   `json:"last_try"`
	Format   string  `json:"format"`
	Progress float64 `json:"progress"`
}

type SegView struct {
	Start int64 `json:"s"`
	End   int64 `json:"e"`
	Pos   int64 `json:"p"`
	Busy  bool  `json:"busy"`
}

type TaskDetail struct {
	TaskView
	Resumable bool      `json:"resumable"`
	Segs      []SegView `json:"segs"`
	Path      string    `json:"path"`
}

func (t *Task) Detail() TaskDetail {
	d := TaskDetail{TaskView: t.View()}
	t.mu.Lock()
	defer t.mu.Unlock()
	d.Resumable = t.Resumable
	if t.Filename != "" {
		d.Path = filepath.Join(t.Dir, t.Filename)
	}
	if t.Kind != "video" {
		for _, s := range t.Segs {
			d.Segs = append(d.Segs, SegView{s.Start, s.End, s.Pos, t.running && t.busy[s]})
		}
		sort.Slice(d.Segs, func(i, j int) bool { return d.Segs[i].Start < d.Segs[j].Start })
	}
	return d
}

func (t *Task) downloadedLocked() int64 {
	if t.Kind == "video" {
		return t.Done
	}
	if t.Status == "completed" && t.Size > 0 {
		return t.Size
	}
	var n int64
	for _, s := range t.Segs {
		if s.Pos > s.Start {
			n += s.Pos - s.Start
		}
	}
	return n
}

func (t *Task) View() TaskView {
	t.mu.Lock()
	defer t.mu.Unlock()
	v := TaskView{ID: t.ID, URL: t.URL, Name: t.Filename, Dir: t.Dir, Kind: t.Kind, Status: t.Status,
		Error: t.Error, Size: t.Size, Done: t.downloadedLocked(), Speed: t.speed, Conns: t.active,
		Running: t.running, Added: t.Added, LastTry: t.LastTry, Format: t.Format}
	if v.Name == "" {
		v.Name = t.Title
	}
	if v.Name == "" {
		v.Name = t.URL
	}
	if t.Status == "completed" {
		v.Progress = 100
	} else if t.Size > 0 {
		v.Progress = float64(v.Done) * 100 / float64(t.Size)
		if v.Progress > 100 {
			v.Progress = 100
		}
	}
	return v
}

func (t *Task) FullPath() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.Filename == "" {
		return ""
	}
	return filepath.Join(t.Dir, t.Filename)
}

// ---------- control ----------

func (t *Task) Start(m *Manager) {
	t.mu.Lock()
	if t.running || t.Status == "completed" {
		t.mu.Unlock()
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.cancel = cancel
	t.running = true
	t.Status = "downloading"
	t.Error = ""
	t.LastTry = time.Now().UnixMilli()
	t.mu.Unlock()
	go t.run(ctx, m)
}

func (t *Task) Pause() {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.cancel != nil {
		t.cancel()
	}
	if !t.running && (t.Status == "queued" || t.Status == "downloading") {
		t.Status = "paused"
	}
}

func (t *Task) IsRunning() bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.running
}

func (t *Task) run(ctx context.Context, m *Manager) {
	var err error
	func() {
		defer func() {
			if r := recover(); r != nil {
				err = fmt.Errorf("internal error: %v", r)
			}
		}()
		if t.Kind == "video" {
			err = t.runVideo(ctx, m)
		} else {
			err = t.runFile(ctx)
		}
	}()
	t.mu.Lock()
	switch {
	case ctx.Err() != nil:
		t.Status = "paused"
	case err != nil:
		t.Status = "error"
		t.Error = err.Error()
	default:
		t.Status = "completed"
	}
	t.running = false
	t.speed = 0
	t.active = 0
	t.cancel = nil
	t.mu.Unlock()
	m.Save()
}

func (t *Task) reqHeaders(req *http.Request) {
	req.Header.Set("User-Agent", defaultUA)
	req.Header.Set("Accept", "*/*")
	req.Header.Set("Accept-Encoding", "identity")
	for k, v := range t.Headers {
		if v != "" {
			req.Header.Set(k, v)
		}
	}
}

var httpClient = &http.Client{Transport: &http.Transport{
	Proxy:                 http.ProxyFromEnvironment,
	MaxIdleConnsPerHost:   64,
	IdleConnTimeout:       60 * time.Second,
	DisableCompression:    true,
	TLSHandshakeTimeout:   20 * time.Second,
	ResponseHeaderTimeout: 30 * time.Second,
}}

func (t *Task) probe(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, "GET", t.URL, nil)
	if err != nil {
		return err
	}
	t.reqHeaders(req)
	req.Header.Set("Range", "bytes=0-")
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("server returned HTTP %d", resp.StatusCode)
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	t.FinalURL = resp.Request.URL.String()
	if t.Filename == "" {
		os.MkdirAll(t.Dir, 0o755)
		t.Filename = filepath.Base(uniquePath(filepath.Join(t.Dir, filenameFrom(resp))))
	}
	t.Size = -1
	t.Resumable = false
	if resp.StatusCode == 206 {
		cr := resp.Header.Get("Content-Range")
		if i := strings.LastIndex(cr, "/"); i >= 0 {
			if n, err := strconv.ParseInt(cr[i+1:], 10, 64); err == nil {
				t.Size, t.Resumable = n, true
			}
		}
	} else if resp.ContentLength > 0 {
		t.Size = resp.ContentLength
	}
	return nil
}

func (t *Task) speedMeter(ctx context.Context, done chan struct{}) {
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	t.mu.Lock()
	last, lastT := t.downloadedLocked(), time.Now()
	t.mu.Unlock()
	for {
		select {
		case <-done:
			return
		case <-ctx.Done():
			return
		case now := <-tick.C:
			t.mu.Lock()
			cur := t.downloadedLocked()
			inst := float64(cur-last) / now.Sub(lastT).Seconds()
			if t.speed == 0 {
				t.speed = inst
			} else {
				t.speed = t.speed*0.6 + inst*0.4
			}
			t.mu.Unlock()
			last, lastT = cur, now
		}
	}
}

func (t *Task) runFile(ctx context.Context) error {
	t.mu.Lock()
	needProbe := t.Filename == "" || t.Size == 0 || t.FinalURL == ""
	t.mu.Unlock()
	if needProbe {
		if err := t.probe(ctx); err != nil {
			return err
		}
	}
	if err := os.MkdirAll(t.Dir, 0o755); err != nil {
		return err
	}
	final := t.FullPath()
	part := final + ".tezpart"

	done := make(chan struct{})
	go t.speedMeter(ctx, done)
	var err error
	if t.Resumable && t.Size > 0 {
		err = t.segmented(ctx, part)
	} else {
		// No range support: on failure restart from zero (up to 5 times)
		for try := 1; ; try++ {
			err = t.single(ctx, part)
			if err == nil || ctx.Err() != nil || try >= 5 {
				break
			}
			select {
			case <-ctx.Done():
			case <-time.After(time.Duration(try) * 2 * time.Second):
			}
		}
	}
	close(done)
	if ctx.Err() != nil {
		return errStopped
	}
	if err != nil {
		return err
	}
	if exists(final) {
		final = uniquePath(final)
		t.mu.Lock()
		t.Filename = filepath.Base(final)
		t.mu.Unlock()
	}
	return os.Rename(part, final)
}

// Server does not support ranges: single connection
func (t *Task) single(ctx context.Context, part string) error {
	t.mu.Lock()
	t.Segs = []*Seg{{0, t.Size - 1, 0}}
	t.active = 1
	seg := t.Segs[0]
	t.mu.Unlock()
	req, _ := http.NewRequestWithContext(ctx, "GET", t.FinalURL, nil)
	t.reqHeaders(req)
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("server returned HTTP %d", resp.StatusCode)
	}
	f, err := os.Create(part)
	if err != nil {
		return err
	}
	defer f.Close()
	buf := make([]byte, chunkSize)
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := f.Write(buf[:n]); werr != nil {
				return werr
			}
			t.mu.Lock()
			seg.Pos += int64(n)
			t.mu.Unlock()
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			return rerr
		}
	}
	t.mu.Lock()
	if t.Size <= 0 {
		t.Size = seg.Pos
		seg.End = seg.Pos - 1
	}
	t.mu.Unlock()
	return nil
}

func (t *Task) segmented(ctx context.Context, part string) error {
	f, err := os.OpenFile(part, os.O_RDWR|os.O_CREATE, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	st, _ := f.Stat()
	t.mu.Lock()
	if st.Size() != t.Size {
		if st.Size() == 0 {
			t.Segs = nil
		}
		if err := f.Truncate(t.Size); err != nil {
			t.mu.Unlock()
			return err
		}
	}
	if len(t.Segs) == 0 {
		n := int64(t.Conns)
		if t.Size < minSplit*2 {
			n = 1
		}
		step := t.Size / n
		for i := int64(0); i < n; i++ {
			end := (i+1)*step - 1
			if i == n-1 {
				end = t.Size - 1
			}
			t.Segs = append(t.Segs, &Seg{i * step, end, i * step})
		}
	}
	t.busy = map[*Seg]bool{}
	t.active = 0
	conns := t.Conns
	t.mu.Unlock()

	var wg sync.WaitGroup
	var lastErr error
	var errMu sync.Mutex
	for i := 0; i < conns; i++ {
		seg := t.nextSeg()
		if seg == nil {
			break
		}
		wg.Add(1)
		go func(seg *Seg) {
			defer wg.Done()
			if e := t.worker(ctx, f, seg); e != nil {
				errMu.Lock()
				lastErr = e
				errMu.Unlock()
			}
		}(seg)
	}
	wg.Wait()
	if ctx.Err() != nil {
		return errStopped
	}
	t.mu.Lock()
	var remaining int64
	for _, s := range t.Segs {
		if s.Pos <= s.End {
			remaining += s.End - s.Pos + 1
		}
	}
	t.mu.Unlock()
	if remaining > 0 {
		if lastErr != nil {
			return lastErr
		}
		return errors.New("download incomplete")
	}
	return nil
}

// Give an idle connection work: a free segment first, otherwise split the largest remaining part (IDM style)
func (t *Task) nextSeg() *Seg {
	t.mu.Lock()
	defer t.mu.Unlock()
	for _, s := range t.Segs {
		if s.Pos <= s.End && !t.busy[s] {
			t.busy[s] = true
			return s
		}
	}
	var best *Seg
	var bestRem int64
	for _, s := range t.Segs {
		if rem := s.End - s.Pos + 1; rem > bestRem {
			best, bestRem = s, rem
		}
	}
	if best == nil || bestRem < minSplit*2 {
		return nil
	}
	mid := best.Pos + bestRem/2
	ns := &Seg{mid, best.End, mid}
	best.End = mid - 1
	t.Segs = append(t.Segs, ns)
	t.busy[ns] = true
	return ns
}

func (t *Task) worker(ctx context.Context, f *os.File, seg *Seg) error {
	t.mu.Lock()
	t.active++
	t.mu.Unlock()
	defer func() {
		t.mu.Lock()
		t.active--
		t.mu.Unlock()
	}()
	for seg != nil {
		err := t.fetchSeg(ctx, f, seg)
		t.mu.Lock()
		delete(t.busy, seg)
		unfinished := seg.Pos <= seg.End
		t.mu.Unlock()
		if ctx.Err() != nil {
			return nil
		}
		if unfinished {
			return err
		}
		seg = t.nextSeg()
	}
	return nil
}

func (t *Task) fetchSeg(ctx context.Context, f *os.File, seg *Seg) error {
	retries := 0
	buf := make([]byte, chunkSize)
	var lastErr error
	for ctx.Err() == nil {
		t.mu.Lock()
		start, end := seg.Pos, seg.End
		t.mu.Unlock()
		if start > end {
			return nil
		}
		err := func() error {
			req, _ := http.NewRequestWithContext(ctx, "GET", t.FinalURL, nil)
			t.reqHeaders(req)
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", start, end))
			resp, err := httpClient.Do(req)
			if err != nil {
				return err
			}
			defer resp.Body.Close()
			if resp.StatusCode != 206 {
				return fmt.Errorf("range request failed (HTTP %d)", resp.StatusCode)
			}
			for {
				n, rerr := resp.Body.Read(buf)
				if n > 0 {
					t.mu.Lock()
					rem := seg.End - seg.Pos + 1
					if rem <= 0 {
						t.mu.Unlock()
						return nil
					}
					if int64(n) > rem {
						n = int(rem)
					}
					_, werr := f.WriteAt(buf[:n], seg.Pos)
					if werr == nil {
						seg.Pos += int64(n)
					}
					t.mu.Unlock()
					if werr != nil {
						return werr
					}
					retries = 0
				}
				if rerr == io.EOF {
					return nil
				}
				if rerr != nil {
					return rerr
				}
			}
		}()
		if err == nil {
			t.mu.Lock()
			fin := seg.Pos > seg.End
			t.mu.Unlock()
			if fin {
				return nil
			}
			err = errors.New("connection closed unexpectedly")
		}
		if ctx.Err() != nil {
			return nil
		}
		lastErr = err
		retries++
		if retries > maxRetries {
			return lastErr
		}
		wait := time.Duration(retries) * 500 * time.Millisecond
		if wait > 10*time.Second {
			wait = 10 * time.Second
		}
		select {
		case <-ctx.Done():
		case <-time.After(wait):
		}
	}
	return nil
}

// ---------- Manager ----------

type Settings struct {
	DownloadDir string `json:"download_dir"`
	Connections int    `json:"connections"`
	MaxParallel int    `json:"max_parallel"`
	Confirm     bool   `json:"confirm_dialog"`
	ShowProg    bool   `json:"show_progress"`
	ExtSeen     bool   `json:"ext_seen"` // extension has connected at least once
}

type Pending struct {
	ID       string `json:"id"`
	URL      string `json:"url"`
	Filename string `json:"filename"`
	Kind     string `json:"kind"`
	Referer  string `json:"referer"`
	Cookies  string `json:"cookies"`
	UA       string `json:"userAgent"`
	Format   string `json:"format"`
	Dir      string `json:"dir"`
}

func (p *Pending) headers() map[string]string {
	h := map[string]string{}
	if p.Referer != "" {
		h["Referer"] = p.Referer
	}
	if p.Cookies != "" {
		h["Cookie"] = p.Cookies
	}
	if p.UA != "" {
		h["User-Agent"] = p.UA
	}
	return h
}

type Manager struct {
	dir      string
	mu       sync.Mutex
	tasks    map[string]*Task
	settings Settings
	pending  map[string]*Pending
	saveMu   sync.Mutex
}

func NewManager(dataDir string) *Manager {
	os.MkdirAll(dataDir, 0o755)
	m := &Manager{dir: dataDir, tasks: map[string]*Task{}, pending: map[string]*Pending{},
		settings: Settings{DownloadDir: defaultDownloadDir(), Connections: 16, MaxParallel: 3, Confirm: true, ShowProg: true}}
	if b, err := os.ReadFile(filepath.Join(dataDir, "settings.json")); err == nil {
		json.Unmarshal(b, &m.settings)
	}
	if b, err := os.ReadFile(filepath.Join(dataDir, "tasks.json")); err == nil {
		var list []*Task
		if json.Unmarshal(b, &list) == nil {
			for _, t := range list {
				if t.Status == "downloading" || t.Status == "queued" {
					t.Status = "paused"
				}
				m.tasks[t.ID] = t
			}
		}
	}
	go m.scheduler()
	return m
}

func (m *Manager) Settings() Settings {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.settings
}

func (m *Manager) SetSettings(s Settings) {
	if s.Connections < 1 {
		s.Connections = 1
	}
	if s.Connections > 32 {
		s.Connections = 32
	}
	if s.MaxParallel < 1 {
		s.MaxParallel = 1
	}
	if s.MaxParallel > 10 {
		s.MaxParallel = 10
	}
	if s.DownloadDir == "" {
		s.DownloadDir = defaultDownloadDir()
	}
	m.mu.Lock()
	s.ExtSeen = m.settings.ExtSeen
	m.settings = s
	m.mu.Unlock()
	m.Save()
}

func (m *Manager) MarkExtSeen() {
	m.mu.Lock()
	changed := !m.settings.ExtSeen
	m.settings.ExtSeen = true
	m.mu.Unlock()
	if changed {
		m.Save()
	}
}

func (m *Manager) Sorted() []*Task {
	m.mu.Lock()
	list := make([]*Task, 0, len(m.tasks))
	for _, t := range m.tasks {
		list = append(list, t)
	}
	m.mu.Unlock()
	sort.Slice(list, func(i, j int) bool { return list[i].Added < list[j].Added })
	return list
}

func (m *Manager) Get(id string) *Task {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.tasks[id]
}

func (m *Manager) Add(u, filename string, headers map[string]string, kind, dir, format string, start bool) *Task {
	s := m.Settings()
	if dir == "" {
		dir = s.DownloadDir
	}
	if kind == "" {
		kind = "file"
	}
	if filename != "" {
		filename = sanitize(filename)
	}
	t := &Task{ID: newID(), URL: u, Dir: dir, Filename: filename, Kind: kind, Headers: headers,
		Conns: s.Connections, Size: 0, Added: time.Now().UnixMilli(), Status: "paused", Format: format}
	if start {
		t.Status = "queued"
	}
	m.mu.Lock()
	m.tasks[t.ID] = t
	m.mu.Unlock()
	m.Save()
	return t
}

func (m *Manager) Remove(id string, deleteFile bool) {
	m.mu.Lock()
	t := m.tasks[id]
	delete(m.tasks, id)
	m.mu.Unlock()
	if t == nil {
		return
	}
	t.Pause()
	for i := 0; i < 30 && t.IsRunning(); i++ {
		time.Sleep(100 * time.Millisecond)
	}
	if p := t.FullPath(); p != "" {
		os.Remove(p + ".tezpart")
		if deleteFile {
			os.Remove(p)
		}
	}
	m.Save()
}

func (m *Manager) Save() {
	m.saveMu.Lock()
	defer m.saveMu.Unlock()
	list := m.Sorted()
	var parts []json.RawMessage
	for _, t := range list {
		t.mu.Lock()
		b, err := json.Marshal(t)
		t.mu.Unlock()
		if err == nil {
			parts = append(parts, b)
		}
	}
	b, _ := json.Marshal(parts)
	tmp := filepath.Join(m.dir, "tasks.json.tmp")
	if os.WriteFile(tmp, b, 0o644) == nil {
		os.Rename(tmp, filepath.Join(m.dir, "tasks.json"))
	}
	s := m.Settings()
	sb, _ := json.MarshalIndent(s, "", "  ")
	os.WriteFile(filepath.Join(m.dir, "settings.json"), sb, 0o644)
}

func (m *Manager) scheduler() {
	lastSave := time.Now()
	for {
		time.Sleep(500 * time.Millisecond)
		max := m.Settings().MaxParallel
		list := m.Sorted()
		running := 0
		for _, t := range list {
			if t.IsRunning() {
				running++
			}
		}
		for _, t := range list {
			if running >= max {
				break
			}
			t.mu.Lock()
			q := t.Status == "queued" && !t.running
			t.mu.Unlock()
			if q {
				t.Start(m)
				running++
			}
		}
		if time.Since(lastSave) > 3*time.Second {
			m.Save()
			lastSave = time.Now()
		}
	}
}

func (m *Manager) PauseAll() {
	for _, t := range m.Sorted() {
		t.Pause()
	}
}

func (m *Manager) WaitStopped(d time.Duration) {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		any := false
		for _, t := range m.Sorted() {
			if t.IsRunning() {
				any = true
			}
		}
		if !any {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
}
