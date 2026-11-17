package com.reel.player

import android.content.Context
import android.media.MediaMetadataRetriever
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/**
 * Makes "difficult" files playable and seekable using the FFmpeg that ships with the app.
 *
 *  - fix():     files that play but can't seek (fragmented MP4 from many downloader apps and
 *               Facebook / Instagram, MP3 without a header, TS / FLV recordings, MKV without cues).
 *               The streams are copied into a new MKV that has a seek index: no re-encoding, same quality.
 *  - convert(): files the player can't open or decode at all (WMV, RMVB, unusual codecs).
 *               First a fast stream copy into MKV; if that still doesn't play, a real conversion
 *               to H.264 + AAC.
 *
 * Results go to a temporary folder; only one converted copy is kept at a time.
 */
object SeekFixer {

    /** Original file path → playable copy. */
    private val fixed = ConcurrentHashMap<String, String>()
    /** How far we've gone for each file: 1 = stream copy done, 2 = full conversion done. */
    private val stage = ConcurrentHashMap<String, Int>()

    fun fixedPathFor(original: String): String? = fixed[original]?.takeIf { File(it).isFile }

    fun alreadyTried(original: String) = (stage[original] ?: 0) >= 1

    /** True when there is nothing left to try for this file. */
    fun exhausted(original: String) = (stage[original] ?: 0) >= 2

    /** Reads the duration straight from the file, so it can be shown before anything else is done. */
    fun durationMs(path: String, ctx: Context? = null): Long {
        val fromAndroid = try {
            val r = MediaMetadataRetriever()
            try {
                r.setDataSource(path)
                r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
            } finally {
                r.release()
            }
        } catch (_: Exception) { 0L }
        if (fromAndroid > 0 || ctx == null) return fromAndroid
        return probeDurationMs(ctx, path)
    }

    /** Asks FFmpeg for the duration ("Duration: 00:03:45.12") when Android can't tell. */
    private fun probeDurationMs(ctx: Context, path: String): Long = try {
        Engine.init(ctx.applicationContext)
        val base = File(ctx.noBackupFilesDir, "youtubedl-android/packages")
        val ffmpeg = File(ctx.applicationInfo.nativeLibraryDir, "libffmpeg.so")
        val pb = ProcessBuilder(listOf(ffmpeg.absolutePath, "-hide_banner", "-nostdin", "-i", path)).redirectErrorStream(true)
        pb.environment()["LD_LIBRARY_PATH"] =
            File(base, "ffmpeg/usr/lib").absolutePath + ":" + File(base, "python/usr/lib").absolutePath
        val p = pb.start()
        val text = p.inputStream.bufferedReader().use { it.readText() }
        p.waitFor(20, TimeUnit.SECONDS)
        val m = Regex("Duration: (\\d+):(\\d+):(\\d+(?:\\.\\d+)?)").find(text)
        if (m == null) 0L else {
            val (h, mi, sec) = m.destructured
            ((h.toLong() * 3600 + mi.toLong() * 60) * 1000 + (sec.toDouble() * 1000).toLong())
        }
    } catch (_: Exception) { 0L }

    /** Stream copy into MKV with a proper index. Blocking. */
    fun fix(ctx: Context, original: String): File? {
        if (alreadyTried(original)) return fixedPathFor(original)?.let { File(it) }
        stage[original] = 1
        return run(ctx, original, listOf("-map", "0:v?", "-map", "0:a?", "-c", "copy"))
    }

    /**
     * Called when the player can't play a file. Each call goes one step further:
     * stream copy first, then a full conversion. Returns null when nothing else can be done.
     */
    fun convert(ctx: Context, original: String): File? {
        val s = stage[original] ?: 0
        if (s == 0) {
            stage[original] = 1
            run(ctx, original, listOf("-map", "0:v?", "-map", "0:a?", "-c", "copy"))?.let { return it }
        }
        if ((stage[original] ?: 0) >= 2) return null
        stage[original] = 2
        val audio = listOf("-c:a", "aac", "-b:a", "160k", "-ac", "2")
        val maps = listOf("-map", "0:v:0?", "-map", "0:a:0?")
        return run(ctx, original, maps + listOf("-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p") + audio)
            ?: run(ctx, original, maps + listOf("-c:v", "mpeg4", "-q:v", "3") + audio)
    }

    private fun run(ctx: Context, original: String, codecArgs: List<String>): File? {
        val input = File(original)
        if (!input.isFile || !input.canRead()) return null

        val dir = File(ctx.cacheDir, "seekfix")
        // Converted copies are as big as the video, so keep just one.
        dir.listFiles()?.forEach { it.delete() }
        fixed.clear()
        dir.mkdirs()
        val out = File(dir, "${original.hashCode().toUInt().toString(16)}.mkv")
        val tmp = File(dir, out.name + ".tmp")

        return try {
            Engine.init(ctx.applicationContext) // unpacks FFmpeg the first time
            val base = File(ctx.noBackupFilesDir, "youtubedl-android/packages")
            val ffmpeg = File(ctx.applicationInfo.nativeLibraryDir, "libffmpeg.so")
            val cmd = listOf(ffmpeg.absolutePath, "-hide_banner", "-nostdin", "-y", "-fflags", "+genpts", "-i", input.absolutePath) +
                codecArgs + listOf("-f", "matroska", tmp.absolutePath)
            val pb = ProcessBuilder(cmd).redirectErrorStream(true)
            pb.environment()["LD_LIBRARY_PATH"] =
                File(base, "ffmpeg/usr/lib").absolutePath + ":" + File(base, "python/usr/lib").absolutePath
            pb.environment()["TMPDIR"] = ctx.cacheDir.absolutePath
            val p = pb.start()
            // Drain output so FFmpeg never blocks on a full pipe.
            p.inputStream.bufferedReader().use { r -> while (r.readLine() != null) { /* ignore */ } }
            if (!p.waitFor(60, TimeUnit.MINUTES)) { p.destroy(); tmp.delete(); return null }
            if (p.exitValue() != 0 || tmp.length() == 0L) { tmp.delete(); return null }
            if (!tmp.renameTo(out)) { tmp.delete(); return null }
            fixed[original] = out.absolutePath
            out
        } catch (_: Exception) {
            tmp.delete()
            null
        }
    }
}
