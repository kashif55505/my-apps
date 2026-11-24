package com.personal.vault;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Updates the app from the GitHub releases of kashif55505/my-apps titled "Vault 1.<n>".
 * The page asks; results go back to it as JSON through {@link Reply}.
 * If the repository is private, the user's read-only GitHub token (kept only on this phone) is used.
 */
final class Updater {

    static final String REPO = "kashif55505/my-apps";
    static final String TITLE_PREFIX = "Vault ";

    interface Reply { void send(String fn, String json); }

    private static final ExecutorService io = Executors.newSingleThreadExecutor();
    private static volatile String apkUrl;
    private static volatile long apkSize;

    private Updater() { }

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("app", Context.MODE_PRIVATE); }

    static String token(Context c) {
        String t = prefs(c).getString("ghToken", "");
        return t == null || t.trim().isEmpty() ? null : t.trim();
    }

    static void setToken(Context c, String t) { prefs(c).edit().putString("ghToken", t == null ? "" : t.trim()).apply(); }

    static int versionCode(Context c) {
        try {
            PackageInfo pi = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? (int) pi.getLongVersionCode() : pi.versionCode;
        } catch (Exception e) { return 0; }
    }

    static String versionName(Context c) {
        try { return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName; }
        catch (Exception e) { return ""; }
    }

    /** Looks for the newest release. Answers window.__updResult({...}). */
    static void check(Context c, Reply reply) {
        io.execute(() -> {
            JSONObject out = new JSONObject();
            try {
                out.put("current", versionCode(c));
                out.put("currentName", versionName(c));
                HttpURLConnection conn = (HttpURLConnection) new URL("https://api.github.com/repos/" + REPO + "/releases?per_page=50").openConnection();
                String t = token(c);
                if (t != null) conn.setRequestProperty("Authorization", "Bearer " + t);
                conn.setRequestProperty("Accept", "application/vnd.github+json");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(20000);
                int code = conn.getResponseCode();
                if (code == 401 || code == 403 || code == 404) { out.put("error", "auth"); reply.send("__updResult", out.toString()); return; }
                if (code != 200) { out.put("error", "net"); reply.send("__updResult", out.toString()); return; }
                JSONArray arr = new JSONArray(read(conn.getInputStream()));
                int best = 0; String bestName = "", url = null, notes = ""; long size = 0;
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject r = arr.getJSONObject(i);
                    if (r.optBoolean("draft") || r.optBoolean("prerelease")) continue;
                    String name = r.optString("name");
                    if (!name.startsWith(TITLE_PREFIX)) continue;
                    String ver = name.substring(TITLE_PREFIX.length()).trim();
                    int n;
                    try { n = Integer.parseInt(ver.substring(ver.lastIndexOf('.') + 1)); } catch (Exception e) { continue; }
                    if (n <= best) continue;
                    JSONArray assets = r.optJSONArray("assets");
                    if (assets == null) continue;
                    for (int k = 0; k < assets.length(); k++) {
                        JSONObject a = assets.getJSONObject(k);
                        if (a.optString("name").endsWith(".apk")) {
                            best = n; bestName = ver; url = a.optString("url"); size = a.optLong("size"); notes = r.optString("body");
                        }
                    }
                }
                apkUrl = url;
                apkSize = size;
                out.put("latest", best);
                out.put("latestName", bestName);
                out.put("size", size);
                out.put("notes", notes.length() > 600 ? notes.substring(0, 600) : notes);
            } catch (Exception e) {
                try { out.put("error", "net"); } catch (Exception ignored) { }
            }
            reply.send("__updResult", out.toString());
        });
    }

    /** Downloads the newest APK (after {@link #check}) and installs it. Progress: window.__updProgress({...}). */
    static void downloadAndInstall(Activity a, Reply reply) {
        io.execute(() -> {
            String url = apkUrl;
            if (url == null) { reply.send("__updProgress", "{\"error\":\"Check for updates first.\"}"); return; }
            File apk = new File(a.getCacheDir(), "update.apk");
            try {
                HttpURLConnection first = (HttpURLConnection) new URL(url).openConnection();
                first.setInstanceFollowRedirects(false);
                String t = token(a);
                if (t != null) first.setRequestProperty("Authorization", "Bearer " + t);
                first.setRequestProperty("Accept", "application/octet-stream");
                first.setConnectTimeout(20000);
                HttpURLConnection conn = first;
                int code = first.getResponseCode();
                if (code >= 300 && code < 400) {
                    // The file lives on a storage server that must not get the token
                    String loc = first.getHeaderField("Location");
                    first.disconnect();
                    conn = (HttpURLConnection) new URL(loc).openConnection();
                    conn.setConnectTimeout(20000);
                    code = conn.getResponseCode();
                }
                conn.setReadTimeout(30000);
                if (code < 200 || code > 299) throw new IllegalStateException("Download failed (" + code + ")");
                long total = conn.getContentLengthLong() > 0 ? conn.getContentLengthLong() : apkSize;
                long done = 0, lastUi = 0;
                try (InputStream in = conn.getInputStream(); OutputStream o = new FileOutputStream(apk)) {
                    byte[] buf = new byte[256 * 1024];
                    int n;
                    while ((n = in.read(buf)) >= 0) {
                        o.write(buf, 0, n);
                        done += n;
                        long now = System.currentTimeMillis();
                        if (now - lastUi > 300) {
                            lastUi = now;
                            reply.send("__updProgress", "{\"done\":" + done + ",\"total\":" + total + "}");
                        }
                    }
                }
                reply.send("__updProgress", "{\"done\":" + done + ",\"total\":" + total + ",\"installing\":true}");
                a.runOnUiThread(() -> install(a, apk, reply));
            } catch (Exception e) {
                apk.delete();
                reply.send("__updProgress", "{\"error\":\"Download failed. Check your internet and try again.\"}");
            }
        });
    }

    private static void install(Activity a, File apk, Reply reply) {
        if (Build.VERSION.SDK_INT >= 26 && !a.getPackageManager().canRequestPackageInstalls()) {
            reply.send("__updProgress", "{\"error\":\"Allow My Vault to install updates, then tap Update again.\",\"permission\":true}");
            try {
                a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + a.getPackageName())));
            } catch (Exception ignored) { }
            return;
        }
        try {
            PackageInstaller pi = a.getPackageManager().getPackageInstaller();
            PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            params.setAppPackageName(a.getPackageName());
            params.setSize(apk.length());
            // Android 12+: after the first time, updates from the app itself need no extra tap
            if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
            if (Build.VERSION.SDK_INT >= 34) params.setRequestUpdateOwnership(true);
            int id = pi.createSession(params);
            try (PackageInstaller.Session session = pi.openSession(id)) {
                try (OutputStream out = session.openWrite("base.apk", 0, apk.length()); InputStream in = new FileInputStream(apk)) {
                    byte[] buf = new byte[1 << 16];
                    int n;
                    while ((n = in.read(buf)) >= 0) out.write(buf, 0, n);
                    session.fsync(out);
                }
                int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
                PendingIntent status = PendingIntent.getBroadcast(a, id,
                        new Intent(a, UpdateReceiver.class).setAction(UpdateReceiver.ACTION_STATUS), flags);
                session.commit(status.getIntentSender());
            }
        } catch (Exception e) {
            reply.send("__updProgress", "{\"error\":\"Install failed. Try again, or download MyVault.apk from GitHub.\"}");
        }
    }

    private static String read(InputStream in) throws java.io.IOException {
        try (InputStream i = in) {
            java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            int n;
            while ((n = i.read(buf)) >= 0) b.write(buf, 0, n);
            return b.toString("UTF-8");
        }
    }
}
