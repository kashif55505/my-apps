# Phone Data Recovery Tool

Ye tool Android, iPhone, SD card aur computer ki Hard Drive / SSD / NVMe / USB drive se **photos, videos, voice notes, MP3/audio aur documents** (PDF, Word, Excel, PowerPoint) recover karta hai.

> Sirf apne phone par ya mālik ki ijazat se istemal karein.

---

## Download

| Windows program (Python ki zaroorat nahi) | Python scripts (Windows / Mac / Linux) |
|---|---|
| **[PhoneDataRecovery.exe](../../../releases/download/phone-data-recovery-latest/PhoneDataRecovery.exe)** | [PhoneDataRecovery.zip](../../../releases/download/phone-data-recovery-latest/PhoneDataRecovery.zip) |

**Exe kaise chalayein:**
1. `PhoneDataRecovery.exe` ko aik alag folder mein rakhein (jaise `C:\PhoneDataRecovery`) aur double-click karein.
2. Agar Windows ka neela "Windows protected your PC" message aaye to **More info → Run anyway** dabayein. Ye is liye aata hai ke program par paid signature nahi hai.
3. Hard drive aur SD card wale options (3, 4, 6) khud Administrator ke tor par restart hone ka poochte hain.
4. Android ke liye ADB pehli dafa Google se khud download ho jata hai.
5. Kaam khatam hone par menu wapas aata hai. Band karne ke liye **0** dabayein.

## Update kaise karein (Option 9)

Menu mein **9) Check for updates** chunein, ya ye command chalayein:
```
python phone_recovery.py update
```
Tool GitHub se naya version check karke khud install kar leta hai. **Exe** khud ko naye exe se badal leta hai, aur **Python** version pehle test karta hai ke naya code sahi hai. Purana version `_backup/` folder mein mehfooz rehta hai. Jab naya version aaye to menu khud bata deta hai.

> Agar repository **private** ho to update ke liye aik read-only token chahiye. GitHub par jayein: Settings → Developer settings → Fine-grained token → sirf `my-apps` repo → **Contents: Read-only**. Token ko `github_token.txt` naam ki file mein script ke saath rakh dein.

**Naya version publish karna:** `VERSION` file mein number barhayein (jaise `1.0.1`) aur push karein. GitHub khud test karke release bana deta hai.

---

## 1. Installation (aik dafa)

1. **Python 3.9+** install karein: https://www.python.org/downloads/
   (Windows par install karte waqt **"Add Python to PATH"** zaroor tick karein.)
2. Is folder mein `phone_recovery.py` rakhein.
3. **Android ke liye:** Google ke "platform-tools" download karein:
   https://developer.android.com/tools/releases/platform-tools
   Unzip karke `platform-tools` folder isi folder mein rakh dein.
4. **Optional libraries:**
   ```
   pip install cryptography iphone_backup_decrypt
   ```
   (`cryptography` WhatsApp backup ke liye, `iphone_backup_decrypt` encrypted iPhone backup ke liye.)

**Chalana:** Windows par `run.bat` par double-click karein, ya terminal mein ye command chalayein:
```
python phone_recovery.py
```
Menu khul jayega, wahan se 1 se 9 tak option chunein.

---

## 2. Android phone (Option 1)

1. Phone mein: **Settings → About phone → "Build number" par 7 dafa tap karein.**
2. **Settings → Developer options → "USB debugging" ON karein.**
3. USB cable lagayein, "File transfer" chunein, phone par **Allow** dabayein.
4. Tool mein Option 1 chunein.

**Kya milega:**
- Phone ki tamam photos, videos, audio, MP3, WhatsApp voice notes aur documents
- **`Deleted_Trash`** folder mein woh files jo delete hui thin magar abhi trash mein hain
  (`.trashed-...` files, jinke original naam wapas lagaye jate hain)
- **`Thumbnails`** mein delete hui photos ki chhoti copies, agar bachi hon

Command line se sirf kuch types chahiye hon to:
```
python phone_recovery.py android --types Photos,Audio
```

---

## 3. iPhone (Option 2)

1. **Pehle computer par backup banayein:**
   - **Mac:** Finder → iPhone select karein → "Back up all of the data on your iPhone to this Mac" → Back Up Now
   - **Windows:** iTunes ya "Apple Devices" app → Back Up Now
   - (Behtar hai "Encrypt local backup" ON karein. Is se zyada data aata hai, password yaad rakhein.)
2. Tool mein Option 2 chunein. Backup apne aap dhoond liya jayega.
   Agar backup encrypted hai to tool password poochega.

**Kya milega:** Camera Roll ki photos aur videos ("Recently Deleted" wali bhi), Voice Memos, WhatsApp media aur voice notes, aur apps ke documents.

---

## 4. SD card / memory card se DELETED files (Option 4 → Option 3)

Ye asal "deleted recovery" hai. Is mein raw data scan hota hai aur file signatures se files wapas banayi jati hain.

1. SD card ko phone se nikaal kar **card reader** se computer mein lagayein.
2. **Option 4: card ka image banayein.** Ye card ko sirf parhta hai, us par kuch nahi likhta.
   - Windows (Administrator ke tor par chalayein): `\\.\PhysicalDrive2`
     (number "Disk Management" mein dekhein)
   - Linux: `/dev/sdb`, Mac: `/dev/rdisk4` (sudo ke saath chalayein)
3. **Option 3:** image file ka path dein, phir scan shuru ho jayega.

Recover hone wali files `Recovered/Carved/` mein milengi:

| Category  | Formats |
|-----------|---------|
| Photos    | JPG, PNG, GIF, HEIC, WEBP |
| Videos    | MP4, MOV, 3GP, AVI |
| Audio     | MP3, M4A, OPUS (WhatsApp voice), OGG, AMR (call recordings), WAV |
| Documents | PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, ODT |

Speed taqreeban 100 MB/s hai, yaani 32 GB card 5–10 minute mein scan ho jata hai.

**Rooted phone ho to** card ko nikale baghair image bana sakte hain:
```
python phone_recovery.py android-image sdcard.img
```

---

## 5. Hard Drive / SSD / NVMe / USB drive (Option 6)

Computer ki internal ya external drive se deleted files wapas laane ke liye.

1. **Tool ko Administrator ke tor par chalayein:** `run.bat` par right-click karein → "Run as administrator".
   Linux/Mac par `sudo python3 phone_recovery.py` chalayein.
2. Option 6 chunein. Tool tamam drives ki list dikhayega, maslan:
   ```
   1) \\.\PhysicalDrive0   NVMe SSD    476.9 GB  Samsung 980
   2) \\.\PhysicalDrive1   HDD         931.5 GB  WD Blue
   3) \\.\PhysicalDrive2   USB          58.6 GB  SanDisk
   ```
   Drive ka number dein. Sirf aik partition chahiye to drive letter bhi likh sakte hain (jaise `D:`).
3. **Output folder KISI DOOSRI drive par rakhein** (jaise USB disk `E:\Recovered`).
   Usi drive par save karne se deleted data overwrite ho jata hai, is liye tool aisa karne se rok deta hai.
4. Scan ka tareeqa chunein:
   - **Quick:** Windows (NTFS) drives par deleted files **original naam aur folder ke saath** milti hain. Bari aur tukron mein bikhri (fragmented) files bhi aa jati hain. Ye scan chand minute leta hai.
   - **Deep:** drive ki sirf **khaali jagah** scan hoti hai (photos, videos, audio, documents). Is se purani ya format ki hui drive se bhi files mil jati hain. Ye scan ghante bhi le sakta hai (1 TB par 2–3 ghante).
   - **Both:** dono scan ek saath. Kuch files dono jagah aa sakti hain.

**Nateeje kahan milenge:**
- `Drive_Deleted_P1/` mein original naam wali files. Jo folder bhi delete ho gaya tha, us ke naam ke saath "(deleted folder)" likha hota hai.
- `Drive_Deleted_P1/_Maybe_Damaged/` mein woh files jin ki jagah par Windows naya data likh chuka hai.
- `Carved/` mein deep scan wali files.

**HDD vs SSD:**
| Drive | Recovery ke chances |
|-------|--------------------|
| HDD (purani ghoomne wali disk) | Bohat achhe, jab tak naya data na likha jaye |
| USB / external disk / SD card | Achhe |
| SSD / NVMe | Kam, kyunke **TRIM** feature delete hote hi blocks saaf kar deta hai. Tool aisi files ko "empty (erased by SSD TRIM)" bata dega. |

Command line se bhi chala sakte hain:
```
python phone_recovery.py drive --list
python phone_recovery.py drive \\.\PhysicalDrive1 --mode both -o E:\Recovered
python phone_recovery.py drive D: --types Photos,Videos -o E:\Recovered
```

---

## 6. WhatsApp backup parhna (Option 7)

Apna WhatsApp backup (`msgstore.db.crypt15` / `.crypt14`) decrypt karke chats ko **WhatsApp jaisi shakal mein** dekhein. Is mein photos, videos, voice notes, documents, group members ke naam aur search sab hota hai. Ye bilkul offline chalta hai aur aapka data computer se bahar nahi jata.

Pehli dafa ye install karein:
```
pip install cryptography
```

### Qadam 1: Backup phone se computer par laayein
- **Android:** Option 7 → 1 (USB debugging ON hona chahiye). Ye `Databases`, `Backups` aur `Media` folders copy kar deta hai.
  Hath se copy karna ho to backup yahan milta hai: `Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15`
- **iPhone:** Option 2 se iPhone backup extract karein. WhatsApp ki `ChatStorage.sqlite` encrypted nahi hoti, is liye wahan key ki zaroorat nahi.

### Qadam 2: Key
| Backup | Key kahan se milegi |
|---|---|
| `.crypt15` (end-to-end encrypted) | WhatsApp → Settings → Chats → Chat backup → **End-to-end encrypted backup** → "Use 64-digit encryption key". Yehi 64 digits tool mein dein (spaces ke saath bhi chalega). |
| `.crypt14` / `.crypt12` | Phone ki file `/data/data/com.whatsapp/files/key`, jo sirf **rooted** phone se milti hai. Option 7 → 1 rooted phone par ye khud nikaal leta hai. |

> **Password wala E2E backup** offline nahi khul sakta, kyunke us ki key WhatsApp ke servers par hoti hai.
> Hal: WhatsApp mein E2E backup ko **64-digit key** par switch karein, naya backup banayein, phir is tool se kholein.
> Google Drive wala backup bhi pehle phone par restore karna hoga, phir local backup tool se khulega.

### Qadam 3: Chats dekhein
Option 7 → 2 chunein, phir backup file, key aur `WhatsApp/Media` folder dein. Tool ye cheezein banayega:
- `Recovered/WhatsApp_Viewer/index.html`: isay browser mein kholein. Chat list, messages, tasveerein, voice notes aur search sab is mein hai.
- `Recovered/WhatsApp_Viewer/chats_txt/`: har chat ki alag text file.

Command line se:
```
python phone_recovery.py whatsapp pull
python phone_recovery.py whatsapp view msgstore.db.crypt15 --key "1234 abcd ..." --media "WhatsApp/Media"
python phone_recovery.py whatsapp decrypt msgstore.db.crypt15 --key encrypted_backup.key
```
Agar `wa.db` bhi saath ho (rooted phone), to numbers ki jagah contacts ke naam dikhte hain.

---

## 7. Snapchat (Option 8)

Snapchat chats aur Memories phone mein nahi, **Snapchat ke servers par** hoti hain. Is liye apna data dekhne ka sahi tareeqa Snapchat ka apna **"My Data" export** hai. Ye tool us export ko parhne aur Memories download karne mein madad karta hai.

### Qadam 1: Data request karein
1. Snapchat app kholein → Profile → ⚙️ Settings → **Privacy Controls → My Data**
   (ya computer par accounts.snapchat.com → My Data).
2. Ye tick karein: **Memories, Chat Media, Chat History, Friends**.
3. **"Export JSON Files"** ON karein. Ye zaroori hai.
4. Date range "All time" rakhein aur Submit karein.
5. Kuch ghanton ya dino mein email aayegi. `mydata~....zip` file(s) download kar lein. Ye kai hisson mein bhi aa sakti hain.

### Qadam 2: Tool chalayein
Option 8 chunein, phir zip file ya folder dein:
- **1) Open my export:** `Recovered/Snapchat/index.html` mein chats WhatsApp jaise viewer mein khulengi, jin mein tasveerein, videos, voice notes, Snaps ki history aur group names hote hain. `memories.html` mein saari Memories mahine ke hisaab se gallery mein dikhti hain.
- **2) Download ALL Memories:** export ke links se har photo aur video download karta hai. Har file ki tareekh asal Memory wali hoti hai, aur text/sticker wali Memories ka `_with_text.jpg` version bhi ban jata hai. Agar beech mein ruk jaye to dobara chalayein, pehle se download hui files skip ho jayengi.

Command line se:
```
python phone_recovery.py snapchat view "mydata~1732.zip" --download-memories
python phone_recovery.py snapchat memories "C:\Downloads\mydata~1732.zip" --workers 6
```

> Snapchat sirf **saved** chats export karta hai. Khule hue snaps aur unsaved messages Snapchat khud mita deta hai, aur woh kisi bhi tool se wapas nahi aate.
> Memories ke download links email ke **kuch din baad expire** ho jate hain. Expire ho jayen to naya export request karein.
> Phone mein "Save" ki hui Snapchat photos (`Pictures/Snapchat`) Option 1 (Android) se bhi mil jati hain.

---

## 8. Zaroori haqeeqat (limitations)

- **Data delete hote hi phone istemal karna band kar dein.** Nayi photos ya apps purane data ke upar likh deti hain.
- Android 10+ aur iPhone ki **internal memory encrypted** hoti hai. Jo file trash se bhi delete ho chuki ho, woh internal memory se **koi bhi software** wapas nahi la sakta, chahe paid ho ya free. Is surat mein umeed ki jagah backups hain:
  Google Photos ka Trash (60 din), Google Drive/One backup, iCloud, WhatsApp backup.
- SD card par recovery ke chances sab se zyada hote hain.
- Carved files ke original naam nahi milte (jaise `jpg_00004A3C00.jpg`). Kuch files aadhi kharab bhi ho sakti hain.

---

## Commands (advanced)

```
python phone_recovery.py android  [-o folder] [--types ...] [--serial ID] [--no-thumbnails]
python phone_recovery.py ios      [backup_folder] [-o folder] [--password PASS]
python phone_recovery.py ios-backup [folder]          # Linux/Mac par libimobiledevice ke saath
python phone_recovery.py image    DEVICE  output.img
python phone_recovery.py carve    image.img [-o folder] [--types ...]
python phone_recovery.py android-image output.img [--block /dev/block/mmcblk1]
python phone_recovery.py drive [DEVICE] [--list] [--mode quick|deep|both] [-o folder] [--types ...]
python phone_recovery.py whatsapp pull    [-o folder]
python phone_recovery.py whatsapp view    FILE [--key KEY] [--media FOLDER] [--wa-db wa.db] [--copy-media]
python phone_recovery.py whatsapp decrypt FILE --key KEY [-o msgstore.db]
python phone_recovery.py snapchat view     EXPORT [--download-memories] [-o folder]
python phone_recovery.py snapchat memories EXPORT [--workers 4] [--no-merge]
python phone_recovery.py update [--check] [--force]
```
