package main

// In-app updates.
//
// The repository is private, so GitHub only hands out the installer to a signed-in
// user. TezDM therefore asks Chrome (where the user is signed in to GitHub) to
// download the latest installer; the TezDM extension passes that download to the
// app as usual, and when it finishes the app runs the installer silently. The
// installer closes TezDM, installs the new version and starts it again.
//
// The extension also reads the "latest" release page (with the user's GitHub
// sign-in) to tell the app which version is newest, so the UI can show
// "Update available".

import (
	"bytes"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	updateRepo   = "kashif55505/my-apps"
	updateTag    = "tezdm-latest"
	UpdateURL    = "https://github.com/" + updateRepo + "/releases/download/" + updateTag + "/TezDM_Setup.exe"
	ReleasePage  = "https://github.com/" + updateRepo + "/releases/tag/" + updateTag
	updateWindow = 10 * time.Minute // an update download must arrive this soon after clicking Update
)

type updater struct {
	mu          sync.Mutex
	requested   time.Time
	latest      string // newest version seen by the extension
	checkWanted bool   // UI asked the extension to check now
	status      string // "", "downloading", "installing", "error"
	detail      string
}

var upd = &updater{}

// startInstaller runs the downloaded installer (replaced in tests)
var startInstaller = startDetached

// Hosts GitHub uses for release downloads
func isGitHubDownloadHost(h string) bool {
	h = strings.ToLower(h)
	return h == "github.com" || strings.HasSuffix(h, ".githubusercontent.com")
}

// Is this download the TezDM update the user just asked for?
func (u *updater) matches(p *Pending) bool {
	u.mu.Lock()
	recent := !u.requested.IsZero() && time.Since(u.requested) < updateWindow
	u.mu.Unlock()
	if !recent {
		return false
	}
	for _, raw := range []string{p.URL, p.OrigURL} {
		pu, err := url.Parse(raw)
		if err != nil || !isGitHubDownloadHost(pu.Hostname()) {
			continue
		}
		dec, _ := url.QueryUnescape(raw)
		if strings.Contains(dec, "/releases/download/"+updateTag+"/") || strings.Contains(dec, "TezDM_Setup") {
			return true
		}
	}
	return false
}

func (u *updater) set(status, detail string) {
	u.mu.Lock()
	u.status, u.detail = status, detail
	u.mu.Unlock()
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

// Only run a real Windows program that came in through the update flow
func checkInstaller(path string) error {
	st, err := os.Stat(path)
	if err != nil {
		return err
	}
	if st.Size() < 500*1024 {
		return errBadInstaller
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	head := make([]byte, 2)
	if _, err := f.Read(head); err != nil || !bytes.Equal(head, []byte("MZ")) {
		return errBadInstaller
	}
	return nil
}

// Called by the engine when the update download has finished
func installUpdate(path string) {
	if err := checkInstaller(path); err != nil {
		upd.set("error", "The downloaded update is not a valid installer. Make sure you are signed in to GitHub in Chrome, then try again.")
		return
	}
	upd.set("installing", "Installing the update. TezDM will close and start again by itself.")
	time.Sleep(1500 * time.Millisecond) // let the UI show the message
	if err := startInstaller(path, "/S"); err != nil {
		upd.set("error", "Could not start the installer: "+err.Error())
	}
}
