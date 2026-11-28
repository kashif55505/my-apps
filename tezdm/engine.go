package main

// TezDM download engine: multi-connection, IDM-style dynamic segmentation,
// pause/resume (even after restart), auto-retry.

import (
	"compress/flate"
	"compress/gzip"
	"context"
	"crypto/tls"
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

var (
	errStopped     = errors.New("stopped")
	errFileChanged = errors.New("the file changed on the server")
)

// Parse "bytes a-b/total" (total may be "*")
func parseContentRange(cr string) (start, end, total int64, ok bool) {
	cr = strings.TrimSpace(cr)
	if !strings.HasPrefix(cr, "bytes ") {
		return
	}
	cr = strings.TrimPrefix(cr, "bytes ")
	slash := strings.Index(cr, "/")
	dash := strings.Index(cr, "-")
	if slash < 0 || dash < 0 || dash > slash {
		return
	}
	var err error
	if start, err = strconv.ParseInt(cr[:dash], 10, 64); err != nil {
		return
	}
	if end, err = strconv.ParseInt(cr[dash+1:slash], 10, 64); err != nil {
		return
	}
	total = -1
	if t := cr[slash+1:]; t != "*" {
		if total, err = strconv.ParseInt(t, 10, 64); err != nil {
			return
		}
	}
	return start, end, total, true
}

func validatorOf(h http.Header) string {
	if e := h.Get("ETag"); e != "" && !strings.HasPrefix(e, "W/") {
		return e
	}
	return h.Get("Last-Modified")
}

// A web page arriving for a file that is not a web page means the link expired or needs a login
func looksLikeErrorPage(resp *http.Response, filename string) bool {
	ct := strings.ToLower(resp.Header.Get("Content-Type"))
	if !strings.HasPrefix(ct, "text/html") {
		return false
	}
	ext := strings.ToLower(filepath.Ext(filename))
	return ext != ".html" && ext != ".htm" && ext != ".php" && ext != ".asp" && ext != ".aspx" && ext != ""
}

var errWebPage = errors.New("the website sent a web page instead of the file (the link may have expired or need you to be signed in). Please start the download again from the website")

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
	Done      int64             `json:"done,omitempty"`       // for video
	Format    string            `json:"format,omitempty"`     // video quality: "", "1080", "audio"...
	Validator string            `json:"validator,omitempty"`  // ETag / Last-Modified: detects a file that changed on the server
	Encoding  string            `json:"encoding,omitempty"`   // gzip etc. when the server compresses the download
	Update    bool              `json:"update,omitempty"`     // TezDM's own update: run the installer when done
	UpdateSHA string            `json:"update_sha,omitempty"` // expected SHA-256 of the update installer
	LastTry   int64             `json:"last_try,omitempty"`

	mu        sync.Mutex
	cancel    context.CancelFunc
	running   bool
	speed     float64
	active    int
	busy      map[*Seg]bool
	Title     string `json:"title,omitempty"`
	estimate  int64  // video: expected total size (estimate)
	exactSize int64  // video: exact total size from the servers
	streaming int    // parts receiving data right now
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
	Approx   bool    `json:"approx"` // size is an estimate (video still downloading)
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
		Running: t.running, Added: t.Added, LastTry: t.LastTry, Format: t.Format,
		Approx: t.Kind == "video" && t.Status != "completed" && t.Size > 0 && t.exactSize == 0}
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
		if t.Update && t.Filename != "" {
			go installUpdate(filepath.Join(t.Dir, t.Filename), t.UpdateSHA)
		}
	}
	if t.Update && t.Status == "error" {
		upd.set("error", "Downloading the update failed: "+t.Error)
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

// HTTP/1.1 only, on purpose: with HTTP/2 all parts of a download would share ONE
// TCP connection (streams), so servers that limit speed per connection would give
// no more speed than a single connection. Like IDM, every part gets its own connection.
var httpClient = &http.Client{Transport: &http.Transport{
	Proxy:                 http.ProxyFromEnvironment,
	MaxIdleConnsPerHost:   64,
	IdleConnTimeout:       60 * time.Second,
	DisableCompression:    true,
	TLSHandshakeTimeout:   20 * time.Second,
	ResponseHeaderTimeout: 30 * time.Second,
	ForceAttemptHTTP2:     false,
	TLSNextProto:          map[string]func(string, *tls.Conn) http.RoundTripper{},
}}

// errConnLimit: the server refuses more connections while others are downloading;
// the extra connection stops and its part goes to a connection that works
var errConnLimit = errors.New("server allows fewer connections")

func refusalStatus(code int) bool {
	return code == 403 || code == 429 || code == 503 || code == 509 || code == 421
}

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
	if looksLikeErrorPage(resp, t.Filename) {
		return errWebPage
	}
	oldSize, oldValidator, hadData := t.Size, t.Validator, len(t.Segs) > 0
	size, resumable := int64(-1), false
	if resp.StatusCode == 206 {
		if _, _, total, ok := parseContentRange(resp.Header.Get("Content-Range")); ok && total > 0 {
			size, resumable = total, true
		}
	} else if resp.ContentLength > 0 {
		size = resp.ContentLength
	}
	enc := strings.ToLower(resp.Header.Get("Content-Encoding"))
	if enc == "identity" {
		enc = ""
	}
	if enc != "" { // compressed on the wire: download in one piece and decompress
		size, resumable = -1, false
	}
	validator := validatorOf(resp.Header)
	// Resuming, but the file on the server is not the same any more: start again
	if hadData && (size != oldSize || resumable != t.Resumable || (oldValidator != "" && validator != "" && validator != oldValidator)) {
		t.Segs = nil
		os.Remove(filepath.Join(t.Dir, t.Filename) + ".tezpart")
	}
	t.Size, t.Resumable, t.Validator, t.Encoding = size, resumable, validator, enc
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
	// Probe every time: refreshes expired links and detects a file that changed on the server
	if err := t.probe(ctx); err != nil {
		return err
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
		if err == errFileChanged && ctx.Err() == nil { // changed while downloading: start again once
			t.mu.Lock()
			t.Segs = nil
			t.mu.Unlock()
			os.Remove(part)
			if err = t.probe(ctx); err == nil {
				err = t.segmented(ctx, part)
			}
		}
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
	if looksLikeErrorPage(resp, t.Filename) {
		return errWebPage
	}
	var body io.Reader = resp.Body
	switch strings.ToLower(resp.Header.Get("Content-Encoding")) {
	case "gzip", "x-gzip":
		zr, err := gzip.NewReader(resp.Body)
		if err != nil {
			return err
		}
		defer zr.Close()
		body = zr
	case "deflate":
		fr := flate.NewReader(resp.Body)
		defer fr.Close()
		body = fr
	case "", "identity":
	default:
		return fmt.Errorf("unsupported compression %q from server", resp.Header.Get("Content-Encoding"))
	}
	f, err := os.Create(part)
	if err != nil {
		return err
	}
	defer f.Close()
	buf := make([]byte, chunkSize)
	for {
		n, rerr := body.Read(buf)
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
	defer t.mu.Unlock()
	if t.Size > 0 && seg.Pos != t.Size && t.Encoding == "" {
		return fmt.Errorf("incomplete download: got %d of %d bytes", seg.Pos, t.Size)
	}
	t.Size = seg.Pos
	seg.End = seg.Pos - 1
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

	wctx, cancelWorkers := context.WithCancel(ctx)
	defer cancelWorkers()
	var wg sync.WaitGroup
	var lastErr error
	var changed bool
	var errMu sync.Mutex
	for i := 0; i < conns; i++ {
		seg := t.nextSeg()
		if seg == nil {
			break
		}
		wg.Add(1)
		go func(seg *Seg) {
			defer wg.Done()
			if e := t.worker(wctx, f, seg); e != nil {
				errMu.Lock()
				lastErr = e
				if e == errFileChanged {
					changed = true
					cancelWorkers()
				}
				errMu.Unlock()
			}
		}(seg)
	}
	wg.Wait()
	if ctx.Err() != nil {
		return errStopped
	}
	if changed {
		return errFileChanged
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
	return t.verifyComplete(f)
}

// Final check: the parts cover the whole file exactly once and the file has the right size
func (t *Task) verifyComplete(f *os.File) error {
	t.mu.Lock()
	segs := append([]*Seg(nil), t.Segs...)
	size := t.Size
	t.mu.Unlock()
	sort.Slice(segs, func(i, j int) bool { return segs[i].Start < segs[j].Start })
	var next int64
	for _, s := range segs {
		if s.Start != next || s.Pos != s.End+1 {
			return errors.New("download check failed (missing part); please download again")
		}
		next = s.End + 1
	}
	if next != size {
		return errors.New("download check failed (wrong size); please download again")
	}
	if st, err := f.Stat(); err != nil || st.Size() != size {
		return errors.New("download check failed (file size); please download again")
	}
	return f.Sync()
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

// workLeft: any part not fully downloaded yet
func (t *Task) workLeft() bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	for _, s := range t.Segs {
		if s.Pos <= s.End {
			return true
		}
	}
	return false
}

// partsWaiting: some unfinished parts are taken, but fewer connections are
// receiving data than there are taken parts
func (t *Task) partsWaiting() bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	taken := 0
	for _, s := range t.Segs {
		if s.Pos <= s.End && t.busy[s] {
			taken++
		}
	}
	return taken > t.streaming
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
	parked := 0
	for seg != nil {
		err := t.fetchSeg(ctx, f, seg)
		t.mu.Lock()
		delete(t.busy, seg)
		unfinished := seg.Pos <= seg.End
		t.mu.Unlock()
		if ctx.Err() != nil {
			return nil
		}
		if err == errConnLimit {
			// The server is full: the part was released above, so working connections
			// can take it. Wait a little (longer each time), then try again if work is left.
			parked++
			wait := time.Duration(1<<min(parked-1, 4)) * time.Second // 1, 2, 4, 8, 16, 16 ... s
			for until := time.Now().Add(wait); time.Now().Before(until); {
				if ctx.Err() != nil || !t.workLeft() {
					return nil // finished meanwhile: don't keep the download waiting
				}
				time.Sleep(250 * time.Millisecond)
			}
			seg = t.nextSeg()
			continue
		}
		if unfinished {
			return err
		}
		parked = 0
		seg = t.nextSeg()
		// No free part right now, but some parts are held by connections that are not
		// downloading (waiting after a refusal, or still connecting): wait for them
		// instead of quitting, or the download would slow to a single connection
		for seg == nil && ctx.Err() == nil && t.partsWaiting() {
			select {
			case <-ctx.Done():
			case <-time.After(250 * time.Millisecond):
			}
			seg = t.nextSeg()
		}
	}
	return nil
}

func (t *Task) fetchSeg(ctx context.Context, f *os.File, seg *Seg) error {
	retries, refusals := 0, 0
	buf := make([]byte, chunkSize)
	var lastErr error
	for ctx.Err() == nil {
		var got int64    // bytes received in this attempt
		refused := false // server said no (or gave nothing) to this connection
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
			t.mu.Lock()
			validator, size := t.Validator, t.Size
			t.mu.Unlock()
			if validator != "" {
				req.Header.Set("If-Range", validator) // server sends the whole new file if it changed
			}
			resp, err := httpClient.Do(req)
			if err != nil {
				refused = true
				return err
			}
			defer resp.Body.Close()
			if resp.StatusCode == 200 && validator != "" {
				return errFileChanged
			}
			if resp.StatusCode != 206 {
				refused = refusalStatus(resp.StatusCode)
				return fmt.Errorf("range request failed (HTTP %d)", resp.StatusCode)
			}
			// The server must say which bytes it sends; never trust that it is exactly what we asked for
			rs, _, total, ok := parseContentRange(resp.Header.Get("Content-Range"))
			if !ok {
				return errors.New("server sent no valid Content-Range")
			}
			if total > 0 && total != size {
				return errFileChanged
			}
			if rs > start {
				return fmt.Errorf("server skipped bytes (asked from %d, got from %d)", start, rs)
			}
			if rs < start { // server started earlier than asked: drop the extra bytes
				if _, err := io.CopyN(io.Discard, resp.Body, start-rs); err != nil {
					return err
				}
			}
			if ce := strings.ToLower(resp.Header.Get("Content-Encoding")); ce != "" && ce != "identity" {
				return errors.New("server compressed a range response")
			}
			t.mu.Lock()
			t.streaming++
			t.mu.Unlock()
			defer func() {
				t.mu.Lock()
				t.streaming--
				t.mu.Unlock()
			}()
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
						got += int64(n)
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
					if got == 0 {
						refused = true // connection dropped before any data
					}
					return rerr
				}
			}
		}()
		if err == errFileChanged {
			return err
		}
		// Too many connections for this server? If this one keeps being refused while
		// other parts are downloading, stop it so its part goes to a working connection.
		if err != nil && refused && got == 0 {
			refusals++
			if refusals >= 1 {
				select {
				case <-ctx.Done():
					return nil
				case <-time.After(300 * time.Millisecond):
				}
				t.mu.Lock()
				others := t.streaming
				t.mu.Unlock()
				if others > 0 {
					return errConnLimit
				}
			}
		} else if got > 0 {
			refusals = 0
		}
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
	OrigURL  string `json:"origUrl"` // the link before redirects (from Chrome)
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
