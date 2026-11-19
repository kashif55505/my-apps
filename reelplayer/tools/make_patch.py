#!/usr/bin/env python3
"""
Make a small update patch from an older ReelPlayer APK to a newer one.

Most of the APK (Python, FFmpeg and other native libraries) is identical between versions.
The patch says, region by region, either "copy these bytes from the old APK" or "here are new
bytes". The phone rebuilds the new APK byte for byte from its installed APK, checks the
SHA-256, and installs it, so only the changed parts are downloaded.

Format (all integers big-endian):
  b"RPD1"  u64 new_size  32-byte sha256(new apk)
  ops...   0x00 u64 old_offset u64 length   -> copy from old APK
           0x01 u64 length <bytes>          -> new bytes
           0xFF                             -> end
Usage: make_patch.py OLD.apk NEW.apk OUT.patch
"""
import hashlib
import struct
import sys
import zipfile


def entry_data_ranges(path):
    """name -> (data_offset, compressed_size) using each entry's local header."""
    out = {}
    with open(path, "rb") as f, zipfile.ZipFile(path) as z:
        for info in z.infolist():
            f.seek(info.header_offset)
            hdr = f.read(30)
            if hdr[:4] != b"PK\x03\x04":
                continue
            name_len, extra_len = struct.unpack("<HH", hdr[26:30])
            start = info.header_offset + 30 + name_len + extra_len
            out[info.filename] = (start, info.compress_size)
    return out


def main(old_path, new_path, out_path):
    old = open(old_path, "rb").read()
    new = open(new_path, "rb").read()
    old_entries = entry_data_ranges(old_path)
    new_entries = entry_data_ranges(new_path)

    # Regions of the new APK that can be copied from the old one.
    copies = []
    for name, (nstart, nlen) in new_entries.items():
        if nlen < 4096 or name not in old_entries:
            continue
        ostart, olen = old_entries[name]
        if olen == nlen and old[ostart:ostart + olen] == new[nstart:nstart + nlen]:
            copies.append((nstart, nlen, ostart))
    copies.sort()

    ops = bytearray()
    pos = 0
    literal = 0
    for nstart, nlen, ostart in copies:
        if nstart > pos:
            chunk = new[pos:nstart]
            ops += b"\x01" + struct.pack(">Q", len(chunk)) + chunk
            literal += len(chunk)
        ops += b"\x00" + struct.pack(">QQ", ostart, nlen)
        pos = nstart + nlen
    if pos < len(new):
        chunk = new[pos:]
        ops += b"\x01" + struct.pack(">Q", len(chunk)) + chunk
        literal += len(chunk)
    ops += b"\xff"

    head = b"RPD1" + struct.pack(">Q", len(new)) + hashlib.sha256(new).digest()
    with open(out_path, "wb") as f:
        f.write(head + ops)

    # Self-check: rebuild and compare.
    rebuilt = bytearray()
    i = 0
    body = bytes(ops)
    while True:
        t = body[i]; i += 1
        if t == 0xFF:
            break
        if t == 0:
            o, n = struct.unpack(">QQ", body[i:i + 16]); i += 16
            rebuilt += old[o:o + n]
        else:
            (n,) = struct.unpack(">Q", body[i:i + 8]); i += 8
            rebuilt += body[i:i + n]; i += n
    assert bytes(rebuilt) == new, "patch does not rebuild the new APK"
    print(f"{out_path}: {len(head) + len(ops)} bytes ({literal} new), full APK {len(new)} bytes")


if __name__ == "__main__":
    main(*sys.argv[1:4])
