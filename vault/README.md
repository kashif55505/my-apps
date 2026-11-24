# My Vault

Aap ka private vault: photos, videos, files aur notes, password se lock.

- **Phone app (Android):** har cheez phone mein bhi save rehti hai. Laptop band ho tab bhi sab kuch khulta hai, aur naya bhi add ho sakta hai.
- **Laptop server:** aap ki apni hard disk par asli storage. Phone us se khud sync karta hai.
- **Lock:** har file phone ya browser ke andar hi encrypt hoti hai. Laptop, phone ki storage, ya network par sirf lock shuda data hota hai.
- **App mein:** screenshot band hain, aur recent apps mein vault ki screen chhupi rehti hai.

## Download

| Kya | Link |
|---|---|
| Phone app | [**MyVault.apk**](../../../releases/download/vault-latest/MyVault.apk) |
| Laptop server | [**MyVault-Server.zip**](../../../releases/download/vault-latest/MyVault-Server.zip) |

Obtainium mein update ke liye filter `^Vault ` use karein.

## Shuru kaise karein

1. Laptop par server chalayein. Is ke steps `MyVault-Server.zip` ke andar README mein hain (yahan bhi: [`server/README.md`](server/README.md)).
2. Laptop par http://localhost:8080 khol kar vault banayein (username + password).
3. Laptop aur phone par Tailscale on karein, aur laptop par `tailscale serve --bg 8080` chalayein.
4. Phone par **MyVault.apk** install karein, phir laptop ka address (`https://...ts.net`), username aur password daalein.

## Kaise kaam karta hai

- Phone par jo bhi add karte hain, woh pehle phone mein lock ho kar save hota hai, phir laptop milte hi wahan bhi chala jata hai.
- Laptop par jo add hota hai, woh phone par aa jata hai. **Settings → Keep a copy on this device** on ho (app mein yeh pehle se on hai) to poori file phone mein bhi download ho jati hai.
- Delete ya rename ek jagah karein to doosri jagah bhi ho jata hai.
- Upar ka label batata hai: **Synced with laptop**, **Syncing…**, ya **Offline · saved on this device**.
- Laptop ke bina login bhi phone par hi check hota hai. Password badalne ke liye laptop ka on hona zaroori hai.

## Update

- **Phone app:** nayi version aate hi app mein upar patti aati hai. **Update app** dabayein: app download hogi aur khud install ho jayegi. Pehli dafa Android "install unknown apps" ki ijazat maangta hai. Settings → Updates mein bhi check kar sakte hain.
- **Laptop server:** isi tarah **Update laptop** dabane se laptop khud update ho kar dobara chalu ho jata hai.

## Folders

- `app/`: Android app (WebView). Asli app `app/src/main/assets/www/index.html` mein hai. Laptop ka browser bhi yahi page istemal karta hai.
- `server/`: laptop server (Node.js, koi extra package nahi).
