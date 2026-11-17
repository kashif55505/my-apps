# Reel Player (Android)

MX Player jaisa video player. App khulte hi phone ki saari videos apne aap folders mein dikhata hai.

## Features
- **Auto folder scan**: phone ki har video, folder ke hisaab se (MediaStore). Wapas aane par ya pull-to-refresh se dobara scan.
- Thumbnails, duration, size, **NEW** tag, aur dekhne ki progress bar
- Sort: Name / Newest / Largest
- **Select / Share / Delete**: video ya folder par lamba dabayein → checkbox, upar "All" (select all), Share aur Delete. Har item ke ⋮ se bhi Share, Delete, Info. Folder delete = us ki saari videos delete
- **Har format**: MP4, MKV, AVI, MOV, WEBM, FLV, TS, 3GP, M4V, MPG seedha chalte hain; AC3/DTS/TrueHD/EAC3 awaaz FFmpeg decoder se; jo file phone na chala sake (jaise WMV/RMVB) wo khamoshi se chalne layak bana di jati hai
- **Har file mein seek**: jin files mein index nahi hota (fragmented MP4, kuch MP3/TS/FLV/MKV), player unhein khud FFmpeg se chand second mein seek ke qabil bana deta hai, quality wahi rehti hai
- **Pinch zoom**: do ungliyon se zoom aur khiskana
- **Gestures**: left side upar-neeche = brightness, right side = volume (200% boost tak), side mein swipe = seek
- **Double-tap** left/right = 10 sec skip (baar baar tap = 20, 30 sec), center double-tap = play/pause
- **Long press** = 2× speed jab tak ungli rakhi ho
- **Screen lock**
- Aspect: Fit (poori video, sides par patti ho sakti hai) / Crop (poori screen bhare) / Stretch
- Auto-rotate video ke hisaab se + rotate button
- **Speed** 0.25× – 4×
- **Audio track** chunna (dual-audio MKV)
- **Subtitles**: andar wale tracks, same-naam ki .srt/.vtt/.ass khud lag jati hai, ya file khol kar lagayein, text size
- **Resume**: jahan chhori thi wahin se
- **Repeat**: Off / All (poora folder) / One (ek hi video bar bar)
- Folder ki agli/pichhli video, playlist khatam hone par wapas list
- **Pop-up player** (Picture-in-Picture)
- **Background play** (headphone button): Back, Home ya screen lock, har haal mein awaaz chalti rahe. Folder list mein neeche "Now playing" bar, notification aur lock screen se play/pause/next. Headphone nikalne par khud pause.
- Volume keys se boost, dusri apps se "Open with Reel Player"

## Sidebar (☰)
- **Videos**: folder list
- **YouTube**: search karein, video app ke andar chalti hai (background play aur notification controls ke saath), har video ke neeche quality ke hisaab se Download. Search list mein har video ke saath ⬇ button se seedha quality chunein. Link paste karne se bhi khul jati hai.
- **Video Downloader**: YouTube, Facebook, Instagram, TikTok, X, Vimeo aur sainkdon sites ka link paste karein → saari qualities (4K, 1080p, 720p, 480p…, Audio M4A, MP3) size ke saath → jo chahein download.
  Downloads background mein chalti hain (notification mein progress), aur phone ke **Download** folder mein save hoti hain, jo app ki folder list mein khud aa jata hai.
  YouTube/Facebook/Instagram app mein **Share → Download with Reel** se bhi seedha khulta hai.
- **Downloaded videos**: Download folder kholta hai
- **Update downloader**: sites badalti rehti hain, agar download kaam karna band kare to ye dabayein (app din mein ek baar khud bhi update karta hai)

Private ya login-wali videos (khaas tor par kuch Instagram posts) download nahi hoti.
Sirf wo videos download karein jinka aapko haq ho ya jo platform ke rules ke mutabiq hon.

## APK kaise banayein

### Tareeqa 1: GitHub (bina computer ke, phone se bhi)
1. github.com par free account banayein aur **New repository** banayein.
2. **Add file → Upload files** se is folder ki saari files upload karein (zip ke andar wali, zip khud nahi).
   `.github` folder bhi upload hona zaroori hai.
3. Repo mein **Actions** tab kholein. "Build APK" khud chal jayega (5-8 minute).
4. Mukammal hone par us run par click karein, neeche **Artifacts → ReelPlayer-apk** download karein.
5. Zip kholein aur `app-debug.apk` install karein. Ye ek hi APK har Android phone (8.0+) par chalti hai ("Install unknown apps" allow karna hoga).

### Tareeqa 2: Android Studio
1. Android Studio mein **Open** karke `ReelPlayer` folder chunein.
2. Sync hone dein, phir **Build → Build APK(s)**.
3. APK: `app/build/outputs/apk/debug/app-debug.apk`

## Note
- Android 8.0 ya naya chahiye.
- Kuch videos ki audio (AC3 / DTS / EAC3) ExoPlayer phone ke decoder par depend karti hai. Agar kisi MKV mein awaaz na aaye to Audio track button se dusra track chunein.
- Android 11+ par same-naam ki .srt khud tab hi lagti hai jab phone allow kare; warna Subtitles → "Open subtitle file…" se chunein.
