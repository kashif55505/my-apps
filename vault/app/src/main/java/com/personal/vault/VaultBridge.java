package com.personal.vault;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.webkit.JavascriptInterface;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.HashMap;
import java.util.Map;

/**
 * Lets the page save an unlocked file to the phone's Downloads folder. The page sends the file in
 * pieces (base64), because a WebView cannot download a blob: link on its own.
 */
public class VaultBridge {

    private final Context ctx;
    private final Map<Integer, Save> open = new HashMap<>();
    private int next = 1;

    private static class Save {
        OutputStream out;
        Uri uri;
        File file;
    }

    VaultBridge(Context ctx) {
        this.ctx = ctx.getApplicationContext();
    }

    /** Starts a file in Downloads. Returns a handle, or 0 if it could not be created. */
    @JavascriptInterface
    public synchronized int saveStart(String name, String mime) {
        String safe = (name == null || name.isEmpty()) ? "file" : name.replaceAll("[\\\\/:*?\"<>|]", "_");
        String type = (mime == null || mime.isEmpty()) ? "application/octet-stream" : mime;
        Save s = new Save();
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.Downloads.DISPLAY_NAME, safe);
                v.put(MediaStore.Downloads.MIME_TYPE, type);
                v.put(MediaStore.Downloads.IS_PENDING, 1);
                ContentResolver cr = ctx.getContentResolver();
                s.uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                if (s.uri == null) return 0;
                s.out = cr.openOutputStream(s.uri);
            } else {
                // Android 8–9: the app's own Downloads folder, no storage permission needed
                File dir = ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
                if (dir == null) return 0;
                dir.mkdirs();
                s.file = new File(dir, safe);
                s.out = new FileOutputStream(s.file);
            }
            if (s.out == null) return 0;
        } catch (Exception e) {
            return 0;
        }
        int h = next++;
        open.put(h, s);
        return h;
    }

    @JavascriptInterface
    public synchronized boolean saveChunk(int handle, String base64) {
        Save s = open.get(handle);
        if (s == null) return false;
        try {
            s.out.write(Base64.decode(base64, Base64.DEFAULT));
            return true;
        } catch (Exception e) {
            abort(handle);
            return false;
        }
    }

    @JavascriptInterface
    public synchronized boolean saveEnd(int handle) {
        Save s = open.remove(handle);
        if (s == null) return false;
        try {
            s.out.close();
            if (s.uri != null && Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.Downloads.IS_PENDING, 0);
                ctx.getContentResolver().update(s.uri, v, null, null);
            }
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private void abort(int handle) {
        Save s = open.remove(handle);
        if (s == null) return;
        try { s.out.close(); } catch (Exception ignored) { }
        try {
            if (s.uri != null) ctx.getContentResolver().delete(s.uri, null, null);
            if (s.file != null) s.file.delete();
        } catch (Exception ignored) { }
    }
}
