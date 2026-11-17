package com.reel.player

import android.content.Context
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import org.json.JSONObject
import java.util.Locale
import kotlin.math.min
import kotlin.math.roundToInt

/** One downloadable option shown to the user, e.g. "1080p · MP4 · 152 MB". */
data class Choice(
    val label: String,
    val detail: String,
    val selector: String,   // yt-dlp -f format selector
    val kind: Kind,
    val estBytes: Long
) {
    enum class Kind { VIDEO, M4A, MP3 }
}

data class VideoMeta(
    val url: String,
    val title: String,
    val thumbnail: String?,
    val duration: Double,
    val uploader: String?,
    val site: String?,
    val choices: List<Choice>,
    /** Ways to stream it for playing inside the app, best first. */
    val streams: List<Stream> = emptyList()
)

/** One way to play an online video: a single URL, or separate video + audio URLs. */
data class Stream(
    val videoUrl: String,
    val audioUrl: String?,
    val hls: Boolean,
    val headers: Map<String, String>
)

/** One search result. */
data class YtItem(
    val id: String,
    val url: String,
    val title: String,
    val channel: String?,
    val duration: Double,
    val views: Long,
    val thumbnail: String
)

/**
 * Wraps yt-dlp (through youtubedl-android). yt-dlp supports YouTube, Facebook, Instagram,
 * TikTok, X/Twitter, Vimeo and hundreds of other sites.
 */
object Engine {

    private var ready = false
    private const val DAY = 24L * 60 * 60 * 1000

    /** Unpacks Python, yt-dlp and FFmpeg on first run (takes a few seconds once). Call off the main thread. */
    @Synchronized
    fun init(ctx: Context) {
        if (ready) return
        YoutubeDL.getInstance().init(ctx.applicationContext)
        FFmpeg.getInstance().init(ctx.applicationContext)
        ready = true
    }

    /**
     * Sites change often and old yt-dlp versions stop working, so refresh it at most once a day
     * (or whenever the user taps "Update downloader"). Returns a message for the user.
     */
    fun update(ctx: Context, force: Boolean = false): String {
        val sp = ctx.getSharedPreferences("reel", Context.MODE_PRIVATE)
        val last = sp.getLong("ytdlpUpdate", 0L)
        val now = System.currentTimeMillis()
        if (!force && now - last < DAY) return "Downloader is up to date"
        val status = YoutubeDL.getInstance().updateYoutubeDL(ctx.applicationContext, YoutubeDL.UpdateChannel.STABLE)
        sp.edit().putLong("ytdlpUpdate", now).apply()
        val version = YoutubeDL.getInstance().versionName(ctx.applicationContext) ?: ""
        return when (status) {
            YoutubeDL.UpdateStatus.DONE -> "Downloader updated $version".trim()
            else -> "Downloader is up to date $version".trim()
        }
    }

    /** Fetches title, thumbnail and every available quality for a link. Blocking; call off the main thread. */
    fun fetch(ctx: Context, url: String): VideoMeta {
        init(ctx)
        try { update(ctx) } catch (_: Exception) { /* offline or GitHub limit: use the bundled version */ }
        val request = YoutubeDLRequest(url)
            .addOption("--dump-single-json")
            .addOption("--no-playlist")
            .addOption("--no-warnings")
        val response = YoutubeDL.getInstance().execute(request, null, null)
        val json = response.out.lineSequence().firstOrNull { it.trimStart().startsWith("{") }
            ?: throw IllegalStateException("No video found at this link")
        return parse(url, JSONObject(json))
    }

    /** YouTube search through yt-dlp. Blocking; call off the main thread. */
    fun search(ctx: Context, query: String, count: Int = 30): List<YtItem> {
        init(ctx)
        try { update(ctx) } catch (_: Exception) {}
        val request = YoutubeDLRequest("ytsearch$count:$query")
            .addOption("--flat-playlist")
            .addOption("--dump-single-json")
            .addOption("--no-warnings")
        val out = YoutubeDL.getInstance().execute(request, null, null).out
        val json = out.lineSequence().firstOrNull { it.trimStart().startsWith("{") } ?: return emptyList()
        val entries = JSONObject(json).optJSONArray("entries") ?: return emptyList()
        val list = ArrayList<YtItem>()
        for (i in 0 until entries.length()) {
            val e = entries.optJSONObject(i) ?: continue
            val id = e.str("id") ?: continue
            // Skip channels and playlists in the results; keep only videos.
            val type = e.str("ie_key") ?: e.str("_type")
            if (id.length != 11 && type != "Youtube") continue
            list += YtItem(
                id = id,
                url = e.str("url")?.takeIf { it.startsWith("http") } ?: "https://www.youtube.com/watch?v=$id",
                title = e.str("title") ?: "Video",
                channel = e.str("channel") ?: e.str("uploader"),
                duration = e.num("duration"),
                views = e.num("view_count").toLong(),
                thumbnail = "https://i.ytimg.com/vi/$id/hqdefault.jpg"
            )
        }
        return list
    }

    /** Picks ways to stream a video for playback: separate video+audio, HLS, then single files. */
    private fun streamsOf(raw: List<JSONObject>): List<Stream> {
        fun headers(o: JSONObject): Map<String, String> {
            val h = o.optJSONObject("http_headers") ?: return emptyMap()
            return h.keys().asSequence().associateWith { h.optString(it) }
        }
        fun height(o: JSONObject) = o.num("height").toInt()
        fun none(o: JSONObject, k: String) = (o.str(k) ?: "none") == "none"
        val https = raw.filter { (it.str("protocol") ?: "").startsWith("http") && it.str("url") != null }
        val out = ArrayList<Stream>()

        // 1. Best H.264 video up to 1080p + best M4A audio, merged by the player.
        val vids = https.filter { !none(it, "vcodec") && none(it, "acodec") && height(it) in 1..1080 }
            .sortedWith(compareByDescending<JSONObject> { (it.str("vcodec") ?: "").startsWith("avc") }
                .thenByDescending { height(it).coerceAtMost(720) }   // 720p starts fast on mobile data
                .thenByDescending { it.num("tbr") })
        val aud = https.filter { none(it, "vcodec") && !none(it, "acodec") }
            .maxWithOrNull(compareBy<JSONObject> { it.str("ext") == "m4a" }.thenBy { it.num("tbr") })
        val v = vids.firstOrNull()
        if (v != null && aud != null) out += Stream(v.str("url")!!, aud.str("url")!!, false, headers(v))

        // 2. HLS (adaptive, video and sound together).
        val hls = raw.filter { (it.str("protocol") ?: "").startsWith("m3u8") && (it.str("manifest_url") ?: it.str("url")) != null }
            .maxByOrNull { height(it).coerceAtMost(1080) }
        if (hls != null) out += Stream((hls.str("manifest_url") ?: hls.str("url"))!!, null, true, headers(hls))

        // 3. A single file with both video and sound.
        https.filter { !none(it, "vcodec") && !none(it, "acodec") }
            .maxByOrNull { height(it) }
            ?.let { out += Stream(it.str("url")!!, null, false, headers(it)) }
        return out
    }

    /** Turns a yt-dlp error into one readable line. */
    fun humanize(e: Throwable): String {
        val msg = e.message ?: e.toString()
        val err = msg.lines().lastOrNull { it.contains("ERROR") }?.substringAfter("ERROR:")?.trim()
        val line = err ?: msg.lines().firstOrNull { it.isNotBlank() } ?: "Something went wrong"
        val low = line.lowercase(Locale.ROOT)
        return when {
            "login" in low || "cookies" in low || "private" in low ->
                "This video is private or needs a login on that site, so it can't be downloaded."
            "unable to resolve" in low || "network" in low || "timed out" in low || "connection" in low ->
                "No internet connection, or the site didn't respond. Try again."
            "unsupported url" in low -> "This link isn't a supported video page."
            else -> line.take(220)
        }
    }

    // ------------------------------------------------------------------ parsing

    private class F(
        val id: String, val ext: String, val w: Int, val h: Int, val fps: Double,
        val vcodec: String, val acodec: String, val size: Long, val tbr: Double, val protocol: String
    ) {
        val quality: Int get() = if (w > 0 && h > 0) min(w, h) else h
    }

    private fun JSONObject.str(k: String): String? =
        if (has(k) && !isNull(k)) optString(k).takeIf { it.isNotEmpty() } else null

    private fun JSONObject.num(k: String): Double =
        if (has(k) && !isNull(k)) optDouble(k, 0.0).let { if (it.isNaN()) 0.0 else it } else 0.0

    fun parse(url: String, j: JSONObject): VideoMeta {
        val duration = j.num("duration")
        val raw = ArrayList<JSONObject>()
        val arr = j.optJSONArray("formats")
        if (arr != null && arr.length() > 0) {
            for (i in 0 until arr.length()) arr.optJSONObject(i)?.let { raw += it }
        } else raw += j

        val formats = raw.mapNotNull {
            val id = it.str("format_id") ?: return@mapNotNull null
            val size = it.num("filesize").takeIf { s -> s > 0 } ?: it.num("filesize_approx")
            F(
                id = id,
                ext = it.str("ext") ?: "mp4",
                w = it.num("width").toInt(),
                h = it.num("height").toInt(),
                fps = it.num("fps"),
                vcodec = it.str("vcodec") ?: "unknown",
                acodec = it.str("acodec") ?: "unknown",
                size = size.toLong(),
                tbr = it.num("tbr"),
                protocol = it.str("protocol") ?: ""
            )
        }.filter { it.ext != "mhtml" && !it.protocol.startsWith("mhtml") } // skip storyboard images

        fun est(f: F): Long = when {
            f.size > 0 -> f.size
            f.tbr > 0 && duration > 0 -> (f.tbr * duration * 125).toLong() // kbit/s → bytes
            else -> 0L
        }

        val audios = formats.filter { it.vcodec == "none" && it.acodec != "none" }
        val bestAudio = audios.maxByOrNull { (if (it.ext == "m4a") 1_000_000.0 else 0.0) + it.tbr }
        val audioSize = bestAudio?.let { est(it) } ?: 0L
        val videos = formats.filter { it.vcodec != "none" && it.h > 0 }

        val choices = ArrayList<Choice>()
        videos.groupBy { it.quality }.toSortedMap(compareByDescending { it }).forEach { (q, list) ->
            // Prefer H.264 (plays everywhere), then VP9, then the rest; then MP4; then highest bitrate.
            val f = list.maxWith(compareBy<F>({ codecScore(it.vcodec) }, { if (it.ext == "mp4") 1 else 0 }, { it.tbr }))
            val hasAudio = f.acodec != "none" && f.acodec != "unknown"
            val selector = if (hasAudio) f.id else "${f.id}+bestaudio[ext=m4a]/${f.id}+bestaudio/${f.id}"
            val size = est(f) + if (hasAudio) 0L else audioSize
            val fps = if (f.fps >= 48) " ${f.fps.roundToInt()}fps" else ""
            val detail = listOf(qualityName(q), "MP4", codecName(f.vcodec), sizeText(size))
                .filter { it.isNotEmpty() }.joinToString(" · ")
            choices += Choice("${q}p$fps", detail, selector, Choice.Kind.VIDEO, size)
        }
        if (choices.isEmpty()) {
            choices += Choice("Best quality", "MP4 · best available", "bv*+ba/b", Choice.Kind.VIDEO, 0L)
        }
        if (bestAudio != null || videos.isNotEmpty()) {
            choices += Choice("Audio only", listOf("M4A", sizeText(audioSize)).filter { it.isNotEmpty() }.joinToString(" · "),
                "bestaudio[ext=m4a]/bestaudio/best", Choice.Kind.M4A, audioSize)
            choices += Choice("MP3", listOf("Audio", sizeText(audioSize)).filter { it.isNotEmpty() }.joinToString(" · "),
                "bestaudio/best", Choice.Kind.MP3, audioSize)
        }

        return VideoMeta(
            url = j.str("webpage_url") ?: url,
            title = j.str("title") ?: "Video",
            thumbnail = j.str("thumbnail"),
            duration = duration,
            uploader = j.str("uploader") ?: j.str("channel"),
            site = j.str("extractor_key"),
            choices = choices,
            streams = try { streamsOf(raw) } catch (_: Exception) { emptyList() }
        )
    }

    private fun codecScore(v: String): Int = when {
        v.startsWith("avc") || v.startsWith("h264") -> 3
        v.startsWith("vp9") || v.startsWith("vp09") -> 2
        v.startsWith("hev") || v.startsWith("hvc") || v.startsWith("h265") -> 1
        v.startsWith("av01") -> 0
        else -> 2
    }

    private fun codecName(v: String): String = when {
        v.startsWith("avc") || v.startsWith("h264") -> "H.264"
        v.startsWith("vp9") || v.startsWith("vp09") -> "VP9"
        v.startsWith("av01") -> "AV1"
        v.startsWith("hev") || v.startsWith("hvc") || v.startsWith("h265") -> "H.265"
        else -> ""
    }

    private fun qualityName(q: Int): String = when {
        q >= 2160 -> "4K"
        q >= 1440 -> "2K"
        q >= 1080 -> "Full HD"
        q >= 720 -> "HD"
        else -> ""
    }

    private fun sizeText(b: Long) = if (b > 0) "~" + fmtSize(b) else ""
}
