package com.reel.player

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.text.InputType
import android.view.LayoutInflater
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * Updates the app from the GitHub releases of kashif55505/my-apps ("ReelPlayer 1.<n>").
 * The repository is private, so the user's own read-only GitHub token is asked for once
 * and kept only on this phone.
 */
object AppUpdater {

    private const val REPO = "kashif55505/my-apps"
    private const val TITLE_PREFIX = "ReelPlayer "
    private val io = Executors.newSingleThreadExecutor()

    data class Release(
        val versionCode: Int, val name: String, val apkUrl: String, val size: Long, val notes: String,
        /** Small patch from the installed version, if the release has one. */
        val patchUrl: String? = null, val patchSize: Long = 0L
    ) {
        val downloadSize: Long get() = if (patchUrl != null) patchSize else size
    }

    private fun sp(c: Context) = c.getSharedPreferences("reel", Context.MODE_PRIVATE)
    fun token(c: Context): String? = sp(c).getString("ghToken", null)?.takeIf { it.isNotBlank() }
    private fun setToken(c: Context, t: String?) = sp(c).edit().putString("ghToken", t).apply()

    fun installedVersion(c: Context): Int = try {
        val pi = c.packageManager.getPackageInfo(c.packageName, 0)
        if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode.toInt() else @Suppress("DEPRECATION") pi.versionCode
    } catch (_: Exception) { 0 }

    fun installedName(c: Context): String = try {
        c.packageManager.getPackageInfo(c.packageName, 0).versionName ?: ""
    } catch (_: Exception) { "" }

    /** Newest ReelPlayer release, or throws. Blocking. */
    fun latest(token: String?, installed: Int = 0): Release? {
        val conn = URL("https://api.github.com/repos/$REPO/releases?per_page=50").openConnection() as HttpURLConnection
        if (token != null) conn.setRequestProperty("Authorization", "Bearer $token")
        conn.setRequestProperty("Accept", "application/vnd.github+json")
        conn.connectTimeout = 15000
        conn.readTimeout = 20000
        when (conn.responseCode) {
            200 -> {}
            401, 403, 404 -> throw SecurityException("token")
            else -> throw IllegalStateException("GitHub answered ${conn.responseCode}")
        }
        val arr = JSONArray(conn.inputStream.bufferedReader().use { it.readText() })
        var best: Release? = null
        for (i in 0 until arr.length()) {
            val r = arr.getJSONObject(i)
            if (r.optBoolean("draft") || r.optBoolean("prerelease")) continue
            val name = r.optString("name")
            if (!name.startsWith(TITLE_PREFIX)) continue
            val code = name.removePrefix(TITLE_PREFIX).substringAfterLast('.').toIntOrNull() ?: continue
            val assets = r.optJSONArray("assets") ?: continue
            if (best != null && code <= best.versionCode) continue
            var apk: org.json.JSONObject? = null
            var patch: org.json.JSONObject? = null
            for (k in 0 until assets.length()) {
                val a = assets.getJSONObject(k)
                val n = a.optString("name")
                if (n.endsWith(".apk")) apk = a
                if (installed > 0 && n == "ReelPlayer-from-1.$installed.patch") patch = a
            }
            if (apk != null) {
                best = Release(code, name.removePrefix(TITLE_PREFIX), apk.optString("url"), apk.optLong("size"), r.optString("body"),
                    patch?.optString("url"), patch?.optLong("size") ?: 0L)
            }
        }
        return best
    }

    /** Quiet check when the app opens (at most every 6 hours). Calls back with a newer release or null. */
    fun checkQuietly(a: AppCompatActivity, onResult: (Release?) -> Unit) {
        val t = token(a)
        val last = sp(a).getLong("updCheck", 0L)
        val cached = sp(a).getInt("updAvail", 0)
        if (System.currentTimeMillis() - last < 3600_000L) {
            onResult(if (cached > installedVersion(a)) Release(cached, "1.$cached", "", 0, "") else null)
            return
        }
        io.execute {
            val r = try { latest(t, installedVersion(a)) } catch (_: Exception) { null }
            sp(a).edit().putLong("updCheck", System.currentTimeMillis()).putInt("updAvail", r?.versionCode ?: 0).apply()
            a.runOnUiThread { if (!a.isDestroyed) onResult(r?.takeIf { it.versionCode > installedVersion(a) }) }
        }
    }

    /** Sidebar → App update. */
    fun checkAndOffer(a: AppCompatActivity, onChecked: () -> Unit = {}) {
        // A public repository needs no token; a private one asks for it once.
        val t = token(a)
        val wait = MaterialAlertDialogBuilder(a).setTitle("App update").setMessage("Checking for a new version…").show()
        io.execute {
            val result = runCatching { latest(t, installedVersion(a)) }
            a.runOnUiThread {
                wait.dismiss()
                if (a.isDestroyed) return@runOnUiThread
                result.onFailure {
                    if (it is SecurityException) {
                        Toast.makeText(a, "The GitHub token doesn't work any more. Enter a new one.", Toast.LENGTH_LONG).show()
                        askToken(a) { checkAndOffer(a, onChecked) }
                    } else {
                        Toast.makeText(a, "Couldn't check for updates. Check your internet and try again.", Toast.LENGTH_LONG).show()
                    }
                }
                result.onSuccess { r ->
                    sp(a).edit().putLong("updCheck", System.currentTimeMillis()).putInt("updAvail", r?.versionCode ?: 0).apply()
                    onChecked()
                    val mine = installedVersion(a)
                    if (r == null || r.versionCode <= mine) {
                        MaterialAlertDialogBuilder(a)
                            .setTitle("You're up to date")
                            .setMessage("Reel Player ${installedName(a)} is the newest version.")
                            .setPositiveButton("OK", null)
                            .show()
                    } else {
                        val notes = r.notes.lines().firstOrNull { it.isNotBlank() && !it.startsWith("Automatic build") } ?: ""
                        MaterialAlertDialogBuilder(a)
                            .setTitle("Update to ${r.name}")
                            .setMessage("You have ${installedName(a)}. Download size: ${fmtSize(r.downloadSize)}" +
                                (if (r.patchUrl != null) " (only the changes)." else ".") +
                                if (notes.isNotEmpty()) "\n\nWhat's new: $notes" else "")
                            .setPositiveButton("Update") { _, _ -> download(a, t, r) }
                            .setNegativeButton("Later", null)
                            .show()
                    }
                }
            }
        }
    }

    private fun askToken(a: AppCompatActivity, then: () -> Unit) {
        val pad = (20 * a.resources.displayMetrics.density).toInt()
        val box = LinearLayout(a).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad / 2, pad, 0) }
        val help = TextView(a).apply {
            text = "Paste your GitHub access token once (the same read-only token you use in Obtainium). " +
                "It stays only on this phone.\n\nGitHub → Settings → Developer settings → Fine-grained tokens → " +
                "repository my-apps → Contents: Read-only."
            setTextColor(a.getColor(R.color.muted))
        }
        val input = EditText(a).apply {
            hint = "github_pat_…"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            setText(token(a) ?: "")
        }
        box.addView(help)
        box.addView(input)
        MaterialAlertDialogBuilder(a)
            .setTitle("GitHub token")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                val t = input.text.toString().trim()
                if (t.isEmpty()) return@setPositiveButton
                setToken(a, t)
                sp(a).edit().putLong("updCheck", 0L).apply()
                then()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------------ download and install

    private fun download(a: AppCompatActivity, token: String?, r: Release) {
        val view = LayoutInflater.from(a).inflate(R.layout.dialog_update, null)
        val bar = view.findViewById<LinearProgressIndicator>(R.id.bar)
        val text = view.findViewById<TextView>(R.id.text)
        val canceled = java.util.concurrent.atomic.AtomicBoolean(false)
        val dialog = MaterialAlertDialogBuilder(a)
            .setTitle("Updating to ${r.name}")
            .setView(view)
            .setCancelable(false)
            .setNegativeButton("Cancel") { _, _ -> canceled.set(true) }
            .show()
        val dir = File(a.cacheDir, "update").apply { mkdirs(); listFiles()?.forEach { it.delete() } }
        val out = File(dir, "ReelPlayer-${r.name}.apk")
        val progress = { label: String, done: Long, total: Long ->
            val pct = if (total > 0) (done * 100 / total).toInt() else 0
            a.runOnUiThread {
                bar.isIndeterminate = total <= 0
                bar.progress = pct
                text.text = "$label  ${fmtSize(done)} of ${fmtSize(total)}  ·  $pct%"
            }
        }
        io.execute {
            val ok = runCatching {
                var built = false
                if (r.patchUrl != null) {
                    // Small way: download only the changes and rebuild the new APK from the installed one.
                    built = runCatching {
                        val patch = File(dir, "update.patch")
                        fetch(r.patchUrl, token, patch, r.patchSize, canceled) { d, t -> progress("Downloading changes", d, t) }
                        a.runOnUiThread { bar.isIndeterminate = true; text.text = "Preparing update…" }
                        val ok = applyPatch(File(a.applicationInfo.sourceDir), patch, out)
                        patch.delete()
                        ok
                    }.getOrElse { if (canceled.get()) throw it; false }
                }
                if (!built) {
                    fetch(r.apkUrl, token, out, r.size, canceled) { d, t -> progress("Downloading", d, t) }
                }
            }
            a.runOnUiThread {
                if (dialog.isShowing) dialog.dismiss()
                if (a.isDestroyed) return@runOnUiThread
                ok.onSuccess { install(a, out) }
                ok.onFailure {
                    out.delete()
                    if (it.message != "canceled") Toast.makeText(a, "Update download failed. Try again.", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    /** Downloads a release file. The asset link redirects to a storage server that must not get the token. */
    private fun fetch(
        url: String, token: String?, out: File, size: Long,
        canceled: java.util.concurrent.atomic.AtomicBoolean, onProgress: (Long, Long) -> Unit
    ) {
        val first = URL(url).openConnection() as HttpURLConnection
        first.instanceFollowRedirects = false
        if (token != null) first.setRequestProperty("Authorization", "Bearer $token")
        first.setRequestProperty("Accept", "application/octet-stream")
        val conn = if (first.responseCode in 300..399) {
            val loc = first.getHeaderField("Location") ?: error("No download link")
            first.disconnect()
            URL(loc).openConnection() as HttpURLConnection
        } else first
        conn.connectTimeout = 20000
        conn.readTimeout = 30000
        if (conn.responseCode !in 200..299) error("Download failed (${conn.responseCode})")
        val total = conn.contentLengthLong.takeIf { it > 0 } ?: size
        var done = 0L
        var lastUi = 0L
        conn.inputStream.use { inp ->
            out.outputStream().use { o ->
                val buf = ByteArray(256 * 1024)
                while (true) {
                    if (canceled.get()) error("canceled")
                    val n = inp.read(buf)
                    if (n < 0) break
                    o.write(buf, 0, n)
                    done += n
                    val now = System.currentTimeMillis()
                    if (now - lastUi > 300) { lastUi = now; onProgress(done, total) }
                }
            }
        }
        onProgress(done, total)
    }

    /**
     * Rebuilds the new APK: copy unchanged parts from the installed APK, take new parts from the patch.
     * Returns false (and the full APK is downloaded instead) if the result isn't exactly right.
     */
    private fun applyPatch(oldApk: File, patch: File, out: File): Boolean {
        val md = java.security.MessageDigest.getInstance("SHA-256")
        java.io.DataInputStream(java.io.BufferedInputStream(patch.inputStream(), 1 shl 16)).use { inp ->
            val magic = ByteArray(4); inp.readFully(magic)
            if (String(magic, Charsets.US_ASCII) != "RPD1") return false
            val newSize = inp.readLong()
            val expected = ByteArray(32); inp.readFully(expected)
            java.io.RandomAccessFile(oldApk, "r").use { old ->
                java.io.BufferedOutputStream(out.outputStream(), 1 shl 16).use { o ->
                    val buf = ByteArray(1 shl 16)
                    var written = 0L
                    while (true) {
                        when (inp.readUnsignedByte()) {
                            0xFF -> break
                            0x00 -> {
                                val off = inp.readLong(); var len = inp.readLong()
                                old.seek(off)
                                while (len > 0) {
                                    val n = old.read(buf, 0, minOf(buf.size.toLong(), len).toInt())
                                    if (n < 0) return false
                                    o.write(buf, 0, n); md.update(buf, 0, n); len -= n; written += n
                                }
                            }
                            0x01 -> {
                                var len = inp.readLong()
                                while (len > 0) {
                                    val n = inp.read(buf, 0, minOf(buf.size.toLong(), len).toInt())
                                    if (n < 0) return false
                                    o.write(buf, 0, n); md.update(buf, 0, n); len -= n; written += n
                                }
                            }
                            else -> return false
                        }
                    }
                    if (written != newSize) return false
                }
            }
            return md.digest().contentEquals(expected)
        }
    }

    private fun install(a: AppCompatActivity, apk: File) {
        if (Build.VERSION.SDK_INT >= 26 && !a.packageManager.canRequestPackageInstalls()) {
            Toast.makeText(a, "Allow Reel Player to install updates, then tap App update again.", Toast.LENGTH_LONG).show()
            a.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${a.packageName}")))
            return
        }
        try {
            // Install through the app's own installer session. On Android 12+, once Reel Player has
            // installed itself one time, later updates need no tap: the app closes and updates.
            val pi = a.packageManager.packageInstaller
            val params = android.content.pm.PackageInstaller.SessionParams(
                android.content.pm.PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            params.setAppPackageName(a.packageName)
            params.setSize(apk.length())
            if (Build.VERSION.SDK_INT >= 31) {
                params.setRequireUserAction(android.content.pm.PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
            val id = pi.createSession(params)
            pi.openSession(id).use { session ->
                session.openWrite("base.apk", 0, apk.length()).use { out ->
                    apk.inputStream().use { it.copyTo(out, 1 shl 16) }
                    session.fsync(out)
                }
                val flags = android.app.PendingIntent.FLAG_UPDATE_CURRENT or
                    (if (Build.VERSION.SDK_INT >= 31) android.app.PendingIntent.FLAG_MUTABLE else 0)
                val status = android.app.PendingIntent.getBroadcast(
                    a, id, Intent(a, UpdateReceiver::class.java).setAction(UpdateReceiver.ACTION_STATUS), flags)
                Toast.makeText(a, "Updating… Reel Player will close and update.", Toast.LENGTH_LONG).show()
                session.commit(status.intentSender)
            }
        } catch (e: Exception) {
            // Old way as a last resort: Android's own install screen.
            val uri = FileProvider.getUriForFile(a, "${a.packageName}.files", apk)
            a.startActivity(
                Intent(Intent.ACTION_VIEW)
                    .setDataAndType(uri, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }
}
