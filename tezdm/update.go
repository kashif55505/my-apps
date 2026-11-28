package main

// In-app updates, for everyone (no GitHub sign-in needed).
//
// The code lives in a private repository, but every release is also published to
// the PUBLIC repository kashif55505/tezdm, which holds only the installer and a
// small version.json. TezDM reads version.json, downloads the installer with its
// own engine, checks its SHA-256, runs it silently, and the installer starts the
// new TezDM.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	publicRepo  = "kashif55505/tezdm"
	versionURL  = "https://raw.githubusercontent.com/" + publicRepo + "/main/version.json"
	ReleasePage = "https://github.com/" + publicRepo + "/releases/latest"
)

// version.json in the public repository
type releaseInfo struct {
	Version string `json:"version"`
	URL     string `json:"url"`
	SHA256  string `json:"sha256"`
	Size    int64  `json:"size"`
	Notes   string `json:"notes"`
}

type updater struct {
	mu       sync.Mutex
	info     *releaseInfo
	checked  time.Time
	checkErr string
	status   string // "", "checking", "downloading", "installing", "error"
	detail   string
	taskID   string
}

var upd = &updater{}

// startInstaller runs the downloaded installer (replaced in tests)
var startInstaller = startDetached

// fetchReleaseInfo reads version.json (replaced in tests)
var fetchReleaseInfo = func(ctx context.Context) (*releaseInfo, error) {
	cctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(cctx, "GET", versionURL+"?t="+strconv.FormatInt(time.Now().Unix(), 10), nil)
	req.Header.Set("Cache-Control", "no-cache")
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("update server answered HTTP %d", resp.StatusCode)
	}
	var ri releaseInfo
	if err := json.NewDecoder(io.LimitReader(resp.Body, 64*1024)).Decode(&ri); err != nil {
		return nil, err
	}
	if ri.Version == "" || !strings.HasPrefix(ri.URL, "https://") {
		return nil, errors.New("update information is incomplete")
	}
	return &ri, nil
}

func (u *updater) set(status, detail string) {
	u.mu.Lock()
	u.status, u.detail = status, detail
	u.mu.Unlock()
}

// Check reads the newest version from the public repository
func (u *updater) Check(ctx context.Context) {
	u.mu.Lock()
	busy := u.status == "downloading" || u.status == "installing"
	if !busy {
		u.status = "checking"
	}
	u.mu.Unlock()
	ri, err := fetchReleaseInfo(ctx)
	u.mu.Lock()
	defer u.mu.Unlock()
	u.checked = time.Now()
	if err != nil {
		u.checkErr = "Could not check for updates: " + err.Error()
	} else {
		u.info, u.checkErr = ri, ""
	}
	if !busy {
		u.status, u.detail = "", ""
	}
}

// AutoCheck: shortly after start, then every 6 hours
func (u *updater) AutoCheck() {
	time.Sleep(20 * time.Second)
	for {
		u.Check(context.Background())
		time.Sleep(6 * time.Hour)
	}
}

func (u *updater) Latest() (string, bool) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.info == nil {
		return "", false
	}
	return u.info.Version, newerVersion(u.info.Version, Version)
}

// Compare dotted versions: is a newer than b?
func newerVersion(a, b string) bool {
	pa, pb := strings.Split(strings.TrimPrefix(a, "v"), "."), strings.Split(strings.TrimPrefix(b, "v"), ".")
	for i := 0; i < len(pa) || i < len(pb); i++ {
		var x, y int
		if i < len(pa) {
			x, _ = strconv.Atoi(pa[i])
		}
		if i < len(pb) {
			y, _ = strconv.Atoi(pb[i])
		}
		if x != y {
			return x > y
		}
	}
	return false
}

func updatesDir() string { return filepath.Join(dataDir(), "updates") }

var errBadInstaller = errors.New("not a valid installer")

// Only run a real Windows program whose checksum matches version.json
func checkInstaller(path, wantSHA string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	head := make([]byte, 2)
	if _, err := io.ReadFull(f, head); err != nil || !bytes.Equal(head, []byte("MZ")) {
		return errBadInstaller
	}
	if wantSHA == "" {
		return errors.New("no checksum for this update")
	}
	f.Seek(0, io.SeekStart)
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return err
	}
	if !strings.EqualFold(hex.EncodeToString(h.Sum(nil)), wantSHA) {
		return errors.New("checksum does not match (damaged download)")
	}
	return nil
}

// Called by the engine when the update download has finished
func installUpdate(path, wantSHA string) {
	if err := checkInstaller(path, wantSHA); err != nil {
		os.Remove(path)
		upd.set("error", "The downloaded update could not be verified ("+err.Error()+"). Please try again.")
		return
	}
	upd.set("installing", "Installing the update. TezDM will close and start again by itself.")
	time.Sleep(1500 * time.Millisecond) // let the UI show the message
	if err := startInstaller(path, "/S"); err != nil {
		upd.set("error", "Could not start the installer: "+err.Error())
	}
}

// StartUpdate downloads the newest installer with TezDM's own engine
func (s *Server) StartUpdate(ctx context.Context) error {
	upd.mu.Lock()
	info := upd.info
	stale := info == nil || time.Since(upd.checked) > 10*time.Minute
	busy := upd.status == "downloading" || upd.status == "installing"
	upd.mu.Unlock()
	if busy {
		return nil
	}
	if stale {
		upd.Check(ctx)
		upd.mu.Lock()
		info = upd.info
		errText := upd.checkErr
		upd.mu.Unlock()
		if info == nil {
			return errors.New(errText)
		}
	}
	if !newerVersion(info.Version, Version) {
		return fmt.Errorf("TezDM %s is already the newest version", Version)
	}
	os.MkdirAll(updatesDir(), 0o755)
	name := "TezDM_Setup_" + sanitize(info.Version) + ".exe"
	os.Remove(filepath.Join(updatesDir(), name))
	t := s.m.Add(info.URL, name, map[string]string{}, "file", updatesDir(), "", false)
	t.mu.Lock()
	t.Update = true
	t.UpdateSHA = info.SHA256
	t.Status = "queued"
	t.mu.Unlock()
	upd.mu.Lock()
	upd.status, upd.detail, upd.taskID = "downloading", "Downloading TezDM "+info.Version+"...", t.ID
	upd.mu.Unlock()
	go s.openProgress(t.ID)
	return nil
}
