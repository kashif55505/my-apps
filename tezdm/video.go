package main

// Video downloads: yt-dlp.exe + ffmpeg.exe (downloaded automatically on first run)

import (
	"archive/zip"
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	ytdlpURL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
	// Unpacked build: starts about 1 s faster than the single exe (no unpacking to a temp folder
	// on every run), but has no self-update, so TezDM updates it itself
	ytdlpZipURL    = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_win.zip"
	ytdlpLatestURL = "https://github.com/yt-dlp/yt-dlp/releases/latest"
	ffmpegURL      = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
	// YouTube needs a JavaScript runtime for yt-dlp (since yt-dlp 2025.11)
	denoURL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
)

type ToolState struct {
	mu     sync.Mutex
	Status string `json:"status"` // ready | downloading | error
	Detail string `json:"detail"`
}

var tools = &ToolState{Status: "checking"}

func (ts *ToolState) set(s, d string) {
	ts.mu.Lock()
	ts.Status, ts.Detail = s, d
	ts.mu.Unlock()
}

func (ts *ToolState) Get() (string, string) {
	ts.mu.Lock()
	defer ts.mu.Unlock()
	return ts.Status, ts.Detail
}

func binDir() string { return filepath.Join(dataDir(), "bin") }

func ytdlpFolder() string { return filepath.Join(binDir(), "yt-dlp") }

func ytdlpPath() string {
	if runtime.GOOS != "windows" {
		if p, err := exec.LookPath("yt-dlp"); err == nil {
			return p
		}
	}
	if p := filepath.Join(ytdlpFolder(), "yt-dlp.exe"); exists(p) {
		return p
	}
	return filepath.Join(binDir(), "yt-dlp.exe")
}

// Newest yt-dlp version on GitHub (from the redirect of /releases/latest)
func latestYtdlpVersion() (string, error) {
	c := *httpClient
	c.Timeout = 30 * time.Second
	c.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	resp, err := c.Get(ytdlpLatestURL)
	if err != nil {
		return "", err
	}
	resp.Body.Close()
	loc := resp.Header.Get("Location")
	i := strings.LastIndex(loc, "/tag/")
	if i < 0 {
		return "", fmt.Errorf("unexpected answer from GitHub (HTTP %d)", resp.StatusCode)
	}
	return loc[i+5:], nil
}

// Unpack a whole zip into dst (refusing paths that would leave dst)
func extractAll(zipPath, dst string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	root, _ := filepath.Abs(dst)
	for _, f := range r.File {
		target := filepath.Join(root, filepath.FromSlash(f.Name))
		if target != root && !strings.HasPrefix(target, root+string(os.PathSeparator)) {
			return fmt.Errorf("bad path in zip: %s", f.Name)
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		rc, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(target)
		if err == nil {
			_, err = io.Copy(out, rc)
			out.Close()
		}
		rc.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

// Install (or update) the unpacked yt-dlp build into bin/yt-dlp
func installYtdlpFolder(version string, progress func(done, total int64)) error {
	z := filepath.Join(binDir(), "yt-dlp_win.zip")
	defer os.Remove(z)
	if err := downloadTo(ytdlpZipURL, z, progress); err != nil {
		return err
	}
	tmp := ytdlpFolder() + ".new"
	os.RemoveAll(tmp)
	if err := extractAll(z, tmp); err != nil {
		os.RemoveAll(tmp)
		return err
	}
	if !exists(filepath.Join(tmp, "yt-dlp.exe")) {
		os.RemoveAll(tmp)
		return errors.New("yt-dlp.exe missing in the download")
	}
	os.WriteFile(filepath.Join(tmp, "VERSION"), []byte(version), 0o644)
	// swap while no yt-dlp is running
	if !lockForUpdate(2 * time.Hour) {
		os.RemoveAll(tmp)
		return errors.New("yt-dlp stayed busy; will try again later")
	}
	defer ytLock.Unlock()
	old := ytdlpFolder() + ".old"
	os.RemoveAll(old)
	if exists(ytdlpFolder()) {
		if err := os.Rename(ytdlpFolder(), old); err != nil {
			os.RemoveAll(tmp)
			return err
		}
	}
	if err := os.Rename(tmp, ytdlpFolder()); err != nil {
		os.Rename(old, ytdlpFolder())
		return err
	}
	os.RemoveAll(old)
	return nil
}

func installedYtdlpVersion() string {
	b, _ := os.ReadFile(filepath.Join(ytdlpFolder(), "VERSION"))
	return strings.TrimSpace(string(b))
}

func denoPath() string {
	p := filepath.Join(binDir(), "deno.exe")
	if exists(p) {
		return p
	}
	if lp, err := exec.LookPath("deno"); err == nil {
		return lp
	}
	return ""
}

// ytLock: the daily update replaces yt-dlp, so it must not run while yt-dlp is in use
var ytLock sync.RWMutex

// lockForUpdate waits until no yt-dlp is running, without ever making new quality
// lookups or downloads wait (a plain Lock would block them behind a long download)
func lockForUpdate(maxWait time.Duration) bool {
	deadline := time.Now().Add(maxWait)
	for time.Now().Before(deadline) {
		if ytLock.TryLock() {
			return true
		}
		time.Sleep(3 * time.Second)
	}
	return false
}

// Common arguments for every yt-dlp run
func ytBaseArgs() []string {
	a := []string{"--encoding", "utf-8", "--no-colors"}
	if d := denoPath(); d != "" {
		a = append(a, "--js-runtimes", "deno:"+d)
	}
	return a
}

// yt-dlp.exe starts a child process: kill the whole tree, and never wait forever for its output
func ytCommand(ctx context.Context, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, ytdlpPath(), append(ytBaseArgs(), args...)...)
	hideWindow(cmd)
	cmd.Cancel = func() error { return killTree(cmd) }
	cmd.WaitDelay = 5 * time.Second
	return cmd
}

func ffmpegPath() string {
	p := filepath.Join(binDir(), "ffmpeg.exe")
	if exists(p) {
		return p
	}
	if lp, err := exec.LookPath("ffmpeg"); err == nil {
		return lp
	}
	return ""
}

func downloadTo(u, dst string, progress func(done, total int64)) error {
	resp, err := httpClient.Get(u)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	tmp := dst + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	var done int64
	buf := make([]byte, 256*1024)
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			if _, err := f.Write(buf[:n]); err != nil {
				f.Close()
				return err
			}
			done += int64(n)
			if progress != nil {
				progress(done, resp.ContentLength)
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			f.Close()
			return rerr
		}
	}
	f.Close()
	return os.Rename(tmp, dst)
}

func extractFromZip(zipPath string, names map[string]string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	found := 0
	for _, f := range r.File {
		dst, ok := names[filepath.Base(f.Name)]
		if !ok {
			continue
		}
		rc, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(dst)
		if err != nil {
			rc.Close()
			return err
		}
		_, err = io.Copy(out, rc)
		out.Close()
		rc.Close()
		if err != nil {
			return err
		}
		found++
	}
	if found == 0 {
		return errors.New("ffmpeg not found in zip")
	}
	return nil
}

// Download video tools on first run; keep yt-dlp updated afterwards
func EnsureTools() {
	if runtime.GOOS != "windows" {
		if exists(ytdlpPath()) {
			tools.set("ready", "")
		} else {
			tools.set("error", "yt-dlp not found")
		}
		return
	}
	os.MkdirAll(binDir(), 0o755)
	mb := func(n int64) string { return fmt.Sprintf("%.1f MB", float64(n)/1048576) }
	for attempt := 1; ; attempt++ {
		var err error
		if !exists(ytdlpPath()) {
			prog := func(d, t int64) { tools.set("downloading", "Downloading video tools (yt-dlp): "+mb(d)) }
			v, verr := latestYtdlpVersion()
			if verr == nil {
				err = installYtdlpFolder(v, prog)
			}
			if verr != nil || err != nil {
				err = downloadTo(ytdlpURL, filepath.Join(binDir(), "yt-dlp.exe"), prog)
			}
		}
		if err == nil && ffmpegPath() == "" {
			z := filepath.Join(binDir(), "ffmpeg.zip")
			err = downloadTo(ffmpegURL, z, func(d, t int64) {
				msg := "Downloading ffmpeg (needed for HD video): " + mb(d)
				if t > 0 {
					msg += " / " + mb(t)
				}
				tools.set("downloading", msg)
			})
			if err == nil {
				err = extractFromZip(z, map[string]string{
					"ffmpeg.exe":  filepath.Join(binDir(), "ffmpeg.exe"),
					"ffprobe.exe": filepath.Join(binDir(), "ffprobe.exe"),
				})
				os.Remove(z)
			}
		}
		if err == nil {
			tools.set("ready", "")
			break
		}
		tools.set("error", "Could not download video tools ("+err.Error()+"). Retrying...")
		time.Sleep(time.Duration(min(attempt, 10)) * 30 * time.Second)
	}
	// JavaScript runtime for YouTube (in the background; yt-dlp uses it once present)
	if denoPath() == "" {
		for attempt := 1; attempt <= 5; attempt++ {
			z := filepath.Join(binDir(), "deno.zip")
			err := downloadTo(denoURL, z, nil)
			if err == nil {
				err = extractFromZip(z, map[string]string{"deno.exe": filepath.Join(binDir(), "deno.exe")})
				os.Remove(z)
			}
			if err == nil {
				break
			}
			time.Sleep(time.Duration(attempt) * 30 * time.Second)
		}
	}
	// Update yt-dlp once a day (needed to keep up with YouTube changes)
	go func() {
		for {
			stamp := filepath.Join(binDir(), "last_update")
			// older installs use the slower single exe: switch to the unpacked build right away
			migrate := !exists(filepath.Join(ytdlpFolder(), "yt-dlp.exe"))
			if st, err := os.Stat(stamp); migrate || err != nil || time.Since(st.ModTime()) > 24*time.Hour {
				ok := false
				if v, err := latestYtdlpVersion(); err == nil {
					// also moves older installs from the single exe to the faster unpacked build
					if v == installedYtdlpVersion() || installYtdlpFolder(v, nil) == nil {
						ok = true
					}
				}
				if !ok && !exists(filepath.Join(ytdlpFolder(), "yt-dlp.exe")) && lockForUpdate(2*time.Hour) {
					ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
					ok = ytCommand(ctx, "-U").Run() == nil
					cancel()
					ytLock.Unlock()
				}
				if ok {
					os.WriteFile(stamp, []byte(time.Now().String()), 0o644)
				}
			}
			time.Sleep(time.Hour)
		}
	}()
}

func parseNum(s string) float64 {
	f, err := strconv.ParseFloat(strings.TrimSpace(s), 64)
	if err != nil {
		return 0
	}
	return f
}

func (t *Task) runVideo(ctx context.Context, m *Manager) error {
	if err := os.MkdirAll(t.Dir, 0o755); err != nil {
		return err
	}
	t.mu.Lock()
	name := "%(title).150B"
	if t.Filename != "" {
		name = strings.TrimSuffix(t.Filename, filepath.Ext(t.Filename))
	}
	conns := t.Conns
	headers := t.Headers
	t.mu.Unlock()
	if err := waitTools(ctx); err != nil {
		return err
	}

	args := []string{"--newline", "--no-playlist",
		"-N", strconv.Itoa(conns), "--retries", "20", "--fragment-retries", "20",
		"--windows-filenames", "-P", t.Dir, "-o", name + ".%(ext)s",
		"--progress-template", "download:TEZ|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.filename)s|%(info.title)s",
		// --print implies --quiet, which hides progress; --progress turns it back on
		"--progress", "--no-simulate",
		"--print", "video:TEZINFO|%(title)s|%(filesize,filesize_approx)s",
		"--print", "video:TEZJSON|%()j", // chosen formats with their links: used to get the exact size
		"--print", "after_move:TEZFILE|%(filepath)s",
	}
	args = append(args, formatArgs(t.Format)...)
	ua := defaultUA
	for k, v := range headers {
		if v == "" {
			continue
		}
		switch strings.ToLower(k) {
		case "user-agent":
			ua = v
		case "referer":
			args = append(args, "--referer", v)
		case "cookie":
			// Sending browser cookies to YouTube often causes problems
			if !strings.Contains(t.URL, "youtu") {
				args = append(args, "--add-header", "Cookie:"+v)
			}
		}
	}
	args = append(args, "--user-agent", ua, "--", t.URL)

	ytLock.RLock()
	defer ytLock.RUnlock()
	cmd := ytCommand(ctx, args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	var errLines []string
	var errMu sync.Mutex
	stderr, _ := cmd.StderrPipe()
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("could not start yt-dlp: %v", err)
	}
	go func() {
		sc := bufio.NewScanner(stderr)
		for sc.Scan() {
			l := sc.Text()
			if strings.Contains(l, "ERROR") {
				errMu.Lock()
				errLines = append(errLines, l)
				errMu.Unlock()
			}
		}
	}()

	// Sizes reported while downloading are often estimates that change all the time
	// (HLS fragments, separate video and audio files). Keep the shown size steady:
	// exact sizes are used as they are; an estimate only replaces the current
	// value when it differs by more than 15%.
	type fileProg struct {
		done, total int64
		exact       bool
	}
	files := map[string]*fileProg{}
	var order []string
	t.mu.Lock()
	t.active = conns
	t.mu.Unlock()

	sc := bufio.NewScanner(stdout)
	sc.Buffer(make([]byte, 64*1024), 64*1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "TEZJSON|") {
			raw := []byte(strings.TrimPrefix(line, "TEZJSON|"))
			go func() { // ask the servers for the exact size, like IDM does
				if n, ok := exactVideoSize(ctx, raw); ok {
					t.mu.Lock()
					t.exactSize = n
					t.Size = n
					t.mu.Unlock()
				}
			}()
			continue
		}
		if strings.HasPrefix(line, "TEZFILE|") {
			p := strings.TrimPrefix(line, "TEZFILE|")
			t.mu.Lock()
			t.Filename = filepath.Base(p)
			t.mu.Unlock()
			continue
		}
		if strings.HasPrefix(line, "TEZINFO|") {
			f := strings.SplitN(line, "|", 3)
			if len(f) == 3 {
				t.mu.Lock()
				if f[1] != "NA" && f[1] != "" {
					t.Title = f[1]
				}
				if est := int64(parseNum(f[2])); est > 0 {
					t.estimate = est // whole video (video + audio), from the format list
					t.Size = est
				}
				t.mu.Unlock()
			}
			continue
		}
		if !strings.HasPrefix(line, "TEZ|") {
			continue
		}
		f := strings.SplitN(line, "|", 7)
		if len(f) < 7 {
			continue
		}
		done := int64(parseNum(f[1]))
		exactTotal := int64(parseNum(f[2]))
		estTotal := int64(parseNum(f[3]))
		fp := files[f[5]]
		if fp == nil {
			fp = &fileProg{}
			files[f[5]] = fp
			order = append(order, f[5])
		}
		fp.done = done
		switch {
		case exactTotal > 0:
			fp.total, fp.exact = exactTotal, true
		case estTotal > 0 && !fp.exact:
			if fp.total == 0 || absDiff(estTotal, fp.total)*100 > fp.total*15 {
				fp.total = estTotal
			}
		}
		if fp.total < fp.done {
			fp.total = fp.done
		}
		var sd, stt, proven int64
		for _, k := range order {
			sd += files[k].done
			stt += files[k].total
			if files[k].exact {
				proven += files[k].total // known for sure
			} else {
				proven += files[k].done // at least this much
			}
		}
		t.mu.Lock()
		t.Done = sd
		size := t.Size
		if t.exactSize > 0 {
			size = t.exactSize // exact, from the servers: never changes
		} else if t.estimate > 0 {
			// the whole-video estimate stays, unless facts (not estimates) show it is too small
			size = t.estimate
			if proven > size {
				size = proven
			}
		} else if size == 0 || absDiff(stt, size)*100 > size*15 {
			size = stt
		}
		if size < sd {
			size = sd
		}
		t.Size = size
		t.speed = parseNum(f[4])
		if t.Title == "" && f[6] != "NA" {
			t.Title = f[6]
		}
		t.mu.Unlock()
	}
	err = cmd.Wait()
	if ctx.Err() != nil {
		return errStopped
	}
	if err != nil {
		errMu.Lock()
		defer errMu.Unlock()
		if len(errLines) > 0 {
			return errors.New(errLines[len(errLines)-1])
		}
		return fmt.Errorf("yt-dlp fail: %v", err)
	}
	t.mu.Lock()
	if t.Size > 0 {
		t.Done = t.Size
	}
	if p := filepath.Join(t.Dir, t.Filename); t.Filename != "" && exists(p) {
		if st, e := os.Stat(p); e == nil {
			t.Size, t.Done = st.Size(), st.Size()
		}
	}
	t.mu.Unlock()
	return nil
}

func waitTools(ctx context.Context) error {
	for {
		st, _ := tools.Get()
		if st == "ready" {
			return nil
		}
		if st == "error" && runtime.GOOS != "windows" {
			return errors.New("yt-dlp not found")
		}
		select { // wait for tools to finish downloading
		case <-ctx.Done():
			return errStopped
		case <-time.After(time.Second):
		}
	}
}

// yt-dlp arguments for the chosen quality
func formatArgs(format string) []string {
	ff := ffmpegPath()
	var a []string
	if ff != "" {
		a = append(a, "--ffmpeg-location", ff)
	}
	switch {
	case format == "audio":
		if ff != "" {
			return append(a, "-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0")
		}
		return append(a, "-f", "ba[ext=m4a]/ba/b")
	case format != "" && format != "best":
		h := format
		if ff != "" {
			return append(a, "-f", "bv*[height<="+h+"]+ba/b[height<="+h+"]/bv*+ba/b", "--merge-output-format", "mp4")
		}
		return append(a, "-f", "b[height<="+h+"]/b")
	default:
		if ff != "" {
			return append(a, "-f", "bv*+ba/b", "--merge-output-format", "mp4")
		}
		return append(a, "-f", "b")
	}
}

// ---------- Video qualities (listed before download) ----------

type QualityOpt struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	Note  string `json:"note"`
	Size  int64  `json:"size"`
}

type VideoInfo struct {
	Title     string       `json:"title"`
	Thumbnail string       `json:"thumbnail"`
	Duration  float64      `json:"duration"`
	Uploader  string       `json:"uploader"`
	Options   []QualityOpt `json:"options"`
}

type ytFormat struct {
	FormatID       string  `json:"format_id"`
	Ext            string  `json:"ext"`
	Height         float64 `json:"height"`
	Fps            float64 `json:"fps"`
	Vcodec         string  `json:"vcodec"`
	Acodec         string  `json:"acodec"`
	Filesize       float64 `json:"filesize"`
	FilesizeApprox float64 `json:"filesize_approx"`
	Tbr            float64 `json:"tbr"`
	Abr            float64 `json:"abr"`
}

type infoCall struct {
	done chan struct{}
	info *VideoInfo
	err  error
}

var (
	infoCache   = map[string]*VideoInfo{}
	infoTime    = map[string]time.Time{}
	infoFlight  = map[string]*infoCall{}
	infoCacheMu sync.Mutex
)

// GetVideoInfo returns cached qualities, joins a lookup already running for the
// same URL (e.g. the extension's prefetch), or starts a new one.
func GetVideoInfo(ctx context.Context, u string, headers map[string]string) (*VideoInfo, error) {
	infoCacheMu.Lock()
	if v, ok := infoCache[u]; ok && time.Since(infoTime[u]) < 30*time.Minute {
		infoCacheMu.Unlock()
		return v, nil
	}
	c, running := infoFlight[u]
	if !running {
		c = &infoCall{done: make(chan struct{})}
		infoFlight[u] = c
		go func() {
			c.info, c.err = fetchVideoInfo(u, headers)
			infoCacheMu.Lock()
			delete(infoFlight, u)
			if c.err == nil {
				infoCache[u] = c.info
				infoTime[u] = time.Now()
			}
			infoCacheMu.Unlock()
			close(c.done)
		}()
	}
	infoCacheMu.Unlock()
	select {
	case <-c.done:
		return c.info, c.err
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func fsize(f ytFormat, dur float64) int64 {
	if f.Filesize > 0 {
		return int64(f.Filesize)
	}
	if f.FilesizeApprox > 0 {
		return int64(f.FilesizeApprox)
	}
	br := f.Tbr
	if br <= 0 {
		br = f.Abr
	}
	if br > 0 && dur > 0 {
		return int64(br * dur * 125) // kbit/s -> bytes
	}
	return 0
}

func heightLabel(h int) string {
	switch {
	case h >= 4320:
		return "8K"
	case h >= 2160:
		return "4K"
	case h >= 1440:
		return "2K"
	case h >= 1080:
		return "Full HD"
	case h >= 720:
		return "HD"
	}
	return ""
}

func fetchVideoInfo(u string, headers map[string]string) (*VideoInfo, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 75*time.Second)
	defer cancel()
	if err := waitTools(ctx); err != nil {
		return nil, errors.New("video tools are still downloading, please wait")
	}
	args := []string{"-J", "--no-playlist", "--no-warnings"}
	ua := defaultUA
	for k, v := range headers {
		switch strings.ToLower(k) {
		case "user-agent":
			if v != "" {
				ua = v
			}
		case "referer":
			if v != "" {
				args = append(args, "--referer", v)
			}
		case "cookie":
			if v != "" && !strings.Contains(u, "youtu") {
				args = append(args, "--add-header", "Cookie:"+v)
			}
		}
	}
	args = append(args, "--user-agent", ua, "--", u)
	ytLock.RLock()
	defer ytLock.RUnlock()
	cmd := ytCommand(ctx, args...)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if ctx.Err() == context.DeadlineExceeded {
		return nil, errors.New("YouTube took too long to answer. Please try again")
	}
	if err != nil {
		msg := strings.TrimSpace(stderr.String())
		if i := strings.LastIndex(msg, "ERROR:"); i >= 0 {
			msg = msg[i:]
		}
		if msg == "" {
			msg = err.Error()
		}
		return nil, errors.New(msg)
	}
	var raw struct {
		Title     string     `json:"title"`
		Thumbnail string     `json:"thumbnail"`
		Duration  float64    `json:"duration"`
		Uploader  string     `json:"uploader"`
		Formats   []ytFormat `json:"formats"`
		Height    float64    `json:"height"`
		Ext       string     `json:"ext"`
		Filesize  float64    `json:"filesize"`
		FilesizeA float64    `json:"filesize_approx"`
		Tbr       float64    `json:"tbr"`
	}
	if err := json.Unmarshal(out, &raw); err != nil {
		return nil, err
	}
	if len(raw.Formats) == 0 { // site with a single format
		raw.Formats = []ytFormat{{Height: raw.Height, Ext: raw.Ext, Vcodec: "x", Acodec: "x",
			Filesize: raw.Filesize, FilesizeApprox: raw.FilesizeA, Tbr: raw.Tbr}}
	}
	hasFF := ffmpegPath() != ""
	var bestAudio int64
	hasAudioOnly := false
	for _, f := range raw.Formats {
		if f.Vcodec == "none" && f.Acodec != "none" {
			hasAudioOnly = true
			if s := fsize(f, raw.Duration); s > bestAudio {
				bestAudio = s
			}
		}
	}
	type hv struct {
		size int64
		fps  float64
		tbr  float64
	}
	byH := map[int]hv{}
	for _, f := range raw.Formats {
		if f.Vcodec == "none" || f.Height <= 0 {
			continue
		}
		muxed := f.Acodec != "none" && f.Acodec != ""
		if !hasFF && !muxed {
			continue // without ffmpeg only formats with both audio+video
		}
		h := int(f.Height)
		size := fsize(f, raw.Duration)
		if !muxed && size > 0 {
			size += bestAudio
		}
		cur, ok := byH[h]
		if !ok || f.Tbr > cur.tbr {
			byH[h] = hv{size, f.Fps, f.Tbr}
		}
	}
	var hs []int
	for h := range byH {
		hs = append(hs, h)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(hs)))
	info := &VideoInfo{Title: raw.Title, Thumbnail: raw.Thumbnail, Duration: raw.Duration, Uploader: raw.Uploader}
	if len(hs) > 0 {
		info.Options = append(info.Options, QualityOpt{ID: "best", Label: "Best quality (" + strconv.Itoa(hs[0]) + "p)",
			Note: "Recommended", Size: byH[hs[0]].size})
	} else {
		info.Options = append(info.Options, QualityOpt{ID: "best", Label: "Best quality", Note: "Recommended"})
	}
	for _, h := range hs {
		v := byH[h]
		lbl := strconv.Itoa(h) + "p"
		if v.fps >= 50 {
			lbl += strconv.Itoa(int(v.fps))
		}
		info.Options = append(info.Options, QualityOpt{ID: strconv.Itoa(h), Label: lbl, Note: heightLabel(h), Size: v.size})
	}
	if hasAudioOnly {
		note := "M4A"
		if hasFF {
			note = "MP3"
		}
		info.Options = append(info.Options, QualityOpt{ID: "audio", Label: "Audio only", Note: note, Size: bestAudio})
	}
	return info, nil
}

func absDiff(a, b int64) int64 {
	if a > b {
		return a - b
	}
	return b - a
}

// ---------- exact size of a video before it downloads ----------

type ytPart struct {
	URL            string            `json:"url"`
	Protocol       string            `json:"protocol"`
	Filesize       float64           `json:"filesize"`
	FilesizeApprox float64           `json:"filesize_approx"`
	HTTPHeaders    map[string]string `json:"http_headers"`
}

// exactVideoSize adds up the exact sizes of the parts yt-dlp will download
// (video and audio). Parts without a known size are asked from their server
// (Content-Range / Content-Length), the way IDM shows an exact size.
// Streams made of many small pieces (HLS/DASH segments) have no exact size.
func exactVideoSize(ctx context.Context, raw []byte) (int64, bool) {
	var info struct {
		ytPart
		RequestedFormats []ytPart `json:"requested_formats"`
	}
	if json.Unmarshal(raw, &info) != nil {
		return 0, false
	}
	parts := info.RequestedFormats
	if len(parts) == 0 {
		parts = []ytPart{info.ytPart}
	}
	var total int64
	for _, p := range parts {
		proto := strings.ToLower(p.Protocol)
		if proto != "" && proto != "https" && proto != "http" {
			return 0, false // m3u8, dash segments, etc.
		}
		if p.Filesize > 0 {
			total += int64(p.Filesize)
			continue
		}
		n, ok := remoteSize(ctx, p.URL, p.HTTPHeaders)
		if !ok {
			return 0, false
		}
		total += n
	}
	return total, total > 0
}

func remoteSize(ctx context.Context, u string, headers map[string]string) (int64, bool) {
	if u == "" {
		return 0, false
	}
	cctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(cctx, "GET", u, nil)
	if err != nil {
		return 0, false
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	req.Header.Set("Range", "bytes=0-0")
	req.Header.Set("Accept-Encoding", "identity")
	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, false
	}
	defer resp.Body.Close()
	if resp.StatusCode == 206 {
		if _, _, total, ok := parseContentRange(resp.Header.Get("Content-Range")); ok && total > 0 {
			return total, true
		}
	}
	if resp.StatusCode == 200 && resp.ContentLength > 0 {
		return resp.ContentLength, true
	}
	return 0, false
}
