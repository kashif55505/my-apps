#!/bin/sh
# TezDM build on Linux/WSL/GitHub Actions: Windows exe + installer
# Requires: Go 1.22+, nsis, gcc-mingw-w64-x86-64
#   sudo apt install golang nsis gcc-mingw-w64-x86-64
# The version comes from the VERSION file.
set -e
cd "$(dirname "$0")"
V=$(tr -d ' \r\n' < VERSION)
VC=$(echo "$V" | tr . ,)
sed "s/@VERSION@/$V/g" winres/tezdm.manifest.in > winres/tezdm.manifest
sed "s/@VERSIONC@/$VC/g; s/@VERSION@/$V/g" winres/tezdm.rc.in > winres/tezdm.rc
(cd winres && x86_64-w64-mingw32-windres -O coff -o ../rsrc_windows_amd64.syso tezdm.rc)
mkdir -p dist
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-H=windowsgui -s -w -X main.Version=$V" -o dist/TezDM.exe .
(cd installer && makensis -V2 -DVERSION="$V" tezdm.nsi)
echo "Built: dist/TezDM_Setup_$V.exe"
