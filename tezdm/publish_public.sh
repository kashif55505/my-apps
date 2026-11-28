#!/bin/sh
# Publish the current TezDM release to the PUBLIC download repository
# (kashif55505/tezdm), so everyone can download and update without a GitHub sign-in.
#
#   ./publish_public.sh /path/to/clone/of/kashif55505-tezdm ["release notes"]
#
# Takes TezDM_Setup.exe from the private "tezdm-latest" release, puts it into the
# public repository with version.json (version, link, SHA-256) and pushes.
# Needs: gh (signed in with access to the private repo), git with push access.
set -e
PUB="$1"
NOTES="${2:-}"
[ -d "$PUB/.git" ] || { echo "usage: $0 <clone of kashif55505/tezdm> [notes]"; exit 1; }
PRIVATE=kashif55505/my-apps
PUBLIC=kashif55505/tezdm

V=$(gh api "repos/$PRIVATE/releases/tags/tezdm-latest" --jq '.name' | sed -n 's/.*latest: \([0-9.]*\).*/\1/p')
[ -n "$V" ] || { echo "could not read the latest TezDM version"; exit 1; }
ID=$(gh api "repos/$PRIVATE/releases/tags/tezdm-latest" --jq '.assets[] | select(.name=="TezDM_Setup.exe") | .id')
gh api -H "Accept: application/octet-stream" "repos/$PRIVATE/releases/assets/$ID" > "$PUB/TezDM_Setup.exe"
head -c 2 "$PUB/TezDM_Setup.exe" | grep -q MZ || { echo "download is not an exe"; exit 1; }
SHA=$(sha256sum "$PUB/TezDM_Setup.exe" | cut -d' ' -f1)
SIZE=$(wc -c < "$PUB/TezDM_Setup.exe" | tr -d ' ')
URL="https://github.com/$PUBLIC/raw/main/TezDM_Setup.exe"
python3 - "$PUB/version.json" "$V" "$URL" "$SHA" "$SIZE" "$NOTES" <<'PY'
import json, sys
p, v, url, sha, size, notes = sys.argv[1:]
json.dump({"version": v, "url": url, "sha256": sha, "size": int(size), "notes": notes}, open(p, "w"), indent=2)
open(p, "a").write("\n")
PY
cd "$PUB"
git add TezDM_Setup.exe version.json
git commit -q -m "TezDM $V" && git push -q origin HEAD:main
echo "Published TezDM $V ($SIZE bytes, sha256 $SHA)"
