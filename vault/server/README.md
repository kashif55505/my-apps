# My Vault: laptop server

Yeh laptop par chalne wala hissa hai. Aap ki saari files yahin, aap ki apni hard disk par save hoti hain.
Phone ki **My Vault** app in se sync karti hai, aur har cheez ki ek copy phone mein bhi rakhti hai, taa ke laptop band ho tab bhi sab kuch khul jaye.

Har file phone ya browser mein hi password se **lock (encrypt)** ho kar aati hai. Laptop par sirf lock shuda data hota hai.

> ⚠️ **Password bhoolna mat.** Ise reset karne ka koi tareeqa nahi hai.

## 1. Laptop par chalana

1. https://nodejs.org se **LTS** wala Node.js install karein.
2. `MyVault-Server` folder ko us drive par rakhein jahan zyada jagah ho, masalan `D:\MyVault-Server`.
   Data kahin aur rakhna ho to `config.json` mein `dataDir` badal dein, masalan `"E:/MyVaultData"`.
3. **Windows:** `start-windows.bat` par double-click karein. **Mac/Linux:** `./start-mac-linux.sh` chalayein.
   Jo kaali window khule use band na karein.
4. Browser mein **http://localhost:8080** kholein, username aur password chunein, aur **Create vault** dabayein.

## 2. Phone ko laptop se jorna (Tailscale, free)

1. Laptop aur phone dono par **Tailscale** install karein (https://tailscale.com/download) aur **ek hi** account se login karein.
2. Laptop par Command Prompt khol kar yeh chalayein:
   ```
   tailscale serve --bg 8080
   ```
   Agar yeh HTTPS on karne ka link dikhaye, to link khol kar **Enable** karein aur command dobara chalayein.
   Ab aap ko ek address milega, masalan `https://my-laptop.tail1234.ts.net`
3. Phone par **My Vault** app kholein, yahi address daalein, aur apna username aur password likhein.

**Sirf ghar ke Wi-Fi par chalana ho (bina Tailscale):** `config.json` mein `"host": "0.0.0.0"` kar dein aur server dobara chalayein. Phir app mein laptop ka Wi-Fi address daalein, masalan `http://192.168.1.5:8080`. Laptop ka address Command Prompt mein `ipconfig` likh kar dekh sakte hain.

## Update

Nayi version aane par app mein upar ek patti aati hai: **"A new version of the laptop server is ready"**. **Update laptop** dabayein. Laptop khud nayi version download kar ke dobara chalu ho jayega, aur aap ka data waisa hi rahega.
Yahi kaam **Settings → Updates → Check for laptop update** se bhi hota hai, chahe phone se karein ya laptop ke browser se.

> Version 1.3 wala server khud update nahi ho sakta. Use ek dafa naya `MyVault-Server.zip` download kar ke badal dein (`vault-data` folder aur `config.json` ko mat chhedein). Is ke baad har update button se ho jayega.

## Achi aadatein

- **Laptop on hote hi vault chalu ho:** `Win + R` dabayein, `shell:startup` likhein, aur `start-windows.bat` ka **shortcut** us folder mein rakh dein.
- **Laptop band ho to bhi koi masla nahi.** Phone apni copy se chalta rahega, aur jo naya add karenge woh laptop on hote hi khud sync ho jayega.
- **Backup:** `vault-data` folder ko kabhi kabhi kisi doosri disk ya USB par copy kar lein. Yeh pehle se lock shuda hai.
