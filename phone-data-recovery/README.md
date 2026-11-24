# Phone & Drive Data Recovery

Recover **photos, videos, voice notes / audio (MP3, M4A, OPUS, AMR, WAV) and documents (PDF, Word, Excel, PowerPoint)** from:

| Source | What it does |
|---|---|
| **Android** (USB / ADB) | Copies all media & docs, restores files from the hidden trash (`.trashed-*`) with original names, grabs thumbnails |
| **iPhone** | Extracts Camera Roll, Recently Deleted, Voice Memos, WhatsApp media, app documents from a Finder/iTunes backup (encrypted supported) |
| **SD card / image** | Signature-based carving of deleted files |
| **HDD / SSD / NVMe / USB** | NTFS deleted files **with original names & folders**, plus deep scan of free space only |
| **WhatsApp backup** | Decrypts your own `msgstore.db.crypt15` / `.crypt14` / `.crypt12` and opens chats in an offline WhatsApp-style HTML viewer (media, group senders, search, text export). Also reads iPhone `ChatStorage.sqlite` |
| **Snapchat** | Reads your *My Data* export (saved chats, snap history, chat media, group names) in the same offline viewer, builds a Memories gallery, and bulk-downloads all Memories with original dates (text overlays merged) |

Python 3.9+, no required dependencies (`pip install -r requirements.txt` for WhatsApp decryption and encrypted iPhone backups). Works on Windows, macOS and Linux.

> Use only on devices you own or have permission to access.

## Download

| Windows program (no Python needed) | Python scripts (Windows / Mac / Linux) |
|---|---|
| **[PhoneDataRecovery.exe](../../../releases/download/phone-data-recovery-latest/PhoneDataRecovery.exe)** | [PhoneDataRecovery.zip](../../../releases/download/phone-data-recovery-latest/PhoneDataRecovery.zip) |

Always the newest version · [release page](../../../releases/tag/phone-data-recovery-latest)

Put `PhoneDataRecovery.exe` in its own folder (e.g. `C:\PhoneDataRecovery`) and double-click it. Windows SmartScreen may warn because the program is not code-signed: click *More info → Run anyway*. Options that read drives (3, 4, 6) ask to restart as Administrator. ADB for Android is downloaded from Google automatically the first time.

## Updating

The tool updates itself from this repository's releases:

```
python phone_recovery.py update          # download + install the newest version
python phone_recovery.py update --check  # only check
```
or menu option **9**. The menu also tells you when a new version is out. The `.exe` replaces itself with the new `.exe`; the Python version replaces its scripts after checking that all code compiles. The old version is kept in `_backup/`. If this repository is private, put a read-only fine-grained token (Contents: Read-only) in `github_token.txt` next to the script.

**Publishing a new version:** change the number in `VERSION` and push to `main`; the *Phone Data Recovery* workflow tests the tool on Windows and Linux, builds the `.exe` and publishes the release.

## Quick start

```bash
python phone_recovery.py          # interactive menu
python phone_recovery.py -h       # all commands
```
Windows: double-click `run.bat` (right-click → *Run as administrator* for drive recovery).

Optional: `pip install -r requirements.txt`
For Android, put Google's [platform-tools](https://developer.android.com/tools/releases/platform-tools) next to the script.

## Commands

```
python phone_recovery.py android  [-o folder] [--types Photos,Audio]
python phone_recovery.py ios      [backup_folder] [--password PASS]
python phone_recovery.py image    DEVICE output.img
python phone_recovery.py carve    image.img
python phone_recovery.py drive    --list
python phone_recovery.py drive    \\.\PhysicalDrive1 --mode both -o E:\Recovered
python phone_recovery.py whatsapp pull
python phone_recovery.py whatsapp view msgstore.db.crypt15 --key "<64-digit key>" --media WhatsApp/Media
python phone_recovery.py snapchat view mydata~123.zip --download-memories
```

### WhatsApp keys
- **crypt15**: the 64-digit key from *Settings → Chats → Chat backup → End-to-end encrypted backup*, or `files/encrypted_backup.key` (root).
- **crypt14 / crypt12**: `/data/data/com.whatsapp/files/key` (root).
- Password-only E2E backups can't be opened offline (the key lives on WhatsApp's servers) — switch to a 64-digit key.

### Snapchat export
Snapchat → Settings → Privacy Controls → **My Data** → tick Memories, Chat Media, Chat History, Friends and turn on **Export JSON Files**. Memory download links expire a few days after the e-mail. Only *saved* chats are exported.

## Supported carving formats
JPG, PNG, GIF, HEIC, WEBP · MP4, MOV, 3GP, AVI · MP3, M4A, OPUS, OGG, AMR, WAV · PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, ODT, EPUB

## Limitations
- Modern Android (10+) and iPhone internal storage is encrypted: files deleted beyond the trash cannot be recovered from internal storage by any software.
- SSD/NVMe TRIM erases deleted blocks quickly; HDDs, USB drives and SD cards give much better results.
- Always save recovered files to a **different** drive (the tool enforces this).

📖 Full guide in Roman Urdu: [README.ur.md](README.ur.md)
