package com.reel.player.yt

import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.dash.DashMediaSource
import androidx.media3.exoplayer.dash.DefaultDashChunkSource
import androidx.media3.exoplayer.dash.manifest.DashManifestParser
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import com.reel.player.Choice
import com.reel.player.YtItem
import com.reel.player.fmtSize
import org.schabi.newpipe.extractor.InfoItem
import org.schabi.newpipe.extractor.MediaFormat
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.Page
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.linkhandler.SearchQueryHandler
import org.schabi.newpipe.extractor.search.SearchInfo
import org.schabi.newpipe.extractor.services.youtube.dashmanifestcreators.YoutubeProgressiveDashManifestCreator
import org.schabi.newpipe.extractor.stream.AudioStream
import org.schabi.newpipe.extractor.stream.DeliveryMethod
import org.schabi.newpipe.extractor.stream.Stream
import org.schabi.newpipe.extractor.stream.StreamInfo
import org.schabi.newpipe.extractor.stream.StreamInfoItem
import org.schabi.newpipe.extractor.stream.StreamType
import org.schabi.newpipe.extractor.stream.VideoStream
import java.io.ByteArrayInputStream

/**
 * Fast YouTube search and playback with NewPipeExtractor (the engine of the NewPipe app).
 * It runs in the app itself, so a video opens in a second or two instead of starting Python.
 */
@androidx.annotation.OptIn(UnstableApi::class)
object Yt {

    @Volatile private var ready = false

    @Synchronized
    fun init() {
        if (ready) return
        NewPipe.init(NpDownloader)
        ready = true
    }

    private val yt get() = ServiceList.YouTube

    class Results(val items: List<YtItem>, val query: SearchQueryHandler, val next: Page?)

    fun search(q: String): Results {
        init()
        val handler = yt.searchQHFactory.fromQuery(q)
        val info = SearchInfo.getInfo(yt, handler)
        return Results(toItems(info.relatedItems), handler, info.nextPage)
    }

    fun more(r: Results): Results {
        val page = r.next ?: return Results(emptyList(), r.query, null)
        val p = SearchInfo.getMoreItems(yt, r.query, page)
        return Results(toItems(p.items), r.query, p.nextPage)
    }

    private fun toItems(list: List<InfoItem>): List<YtItem> = list.filterIsInstance<StreamInfoItem>()
        .filter { it.streamType == StreamType.VIDEO_STREAM || it.streamType == StreamType.LIVE_STREAM }
        .map { s ->
            val id = Regex("[?&]v=([\\w-]{11})").find(s.url)?.groupValues?.get(1)
                ?: s.url.substringAfterLast('/').take(11)
            YtItem(
                id = id,
                url = s.url,
                title = s.name,
                channel = s.uploaderName,
                duration = s.duration.toDouble().coerceAtLeast(0.0),
                views = s.viewCount.coerceAtLeast(0),
                thumbnail = s.thumbnails.maxByOrNull { it.height }?.url ?: "https://i.ytimg.com/vi/$id/hqdefault.jpg"
            )
        }

    fun info(url: String): StreamInfo {
        init()
        return StreamInfo.getInfo(yt, url)
    }

    fun thumbnail(info: StreamInfo): String? = info.thumbnails.maxByOrNull { it.height }?.url

    // ------------------------------------------------------------------ qualities

    /** Picture heights that can be played, highest first (e.g. 1080, 720, 480…). */
    fun playHeights(info: StreamInfo): List<Int> {
        val h = (info.videoOnlyStreams + info.videoStreams).map { it.height }.filter { it > 0 }.distinct().sortedDescending()
        return h
    }

    private fun bestVideoOnly(info: StreamInfo, height: Int): VideoStream? {
        val list = info.videoOnlyStreams.filter { it.height > 0 && it.isUrl && it.deliveryMethod == DeliveryMethod.PROGRESSIVE_HTTP }
        if (list.isEmpty()) return null
        val fit = list.filter { it.height <= height }.ifEmpty { list }
        val top = fit.maxOf { it.height }
        return fit.filter { it.height == top }.maxWithOrNull(
            compareBy<VideoStream> { it.format == MediaFormat.MPEG_4 }
                .thenBy { it.codec?.startsWith("avc") == true }
                .thenBy { it.fps }
                .thenBy { it.bitrate }
        )
    }

    private fun bestAudio(info: StreamInfo): AudioStream? = info.audioStreams
        .filter { it.isUrl && it.deliveryMethod == DeliveryMethod.PROGRESSIVE_HTTP }
        .maxWithOrNull(compareBy<AudioStream> { it.format == MediaFormat.M4A }.thenBy { it.averageBitrate })

    private fun size(s: Stream?): Long = when (s) {
        is VideoStream -> s.itagItem?.contentLength ?: 0L
        is AudioStream -> s.itagItem?.contentLength ?: 0L
        else -> 0L
    }.coerceAtLeast(0L)

    /** Download options straight from NewPipe's data, so they show instantly. */
    fun downloadChoices(info: StreamInfo): List<Choice> {
        val audio = bestAudio(info)
        val audioSize = size(audio)
        val out = ArrayList<Choice>()
        val byHeight = info.videoOnlyStreams.filter { it.height > 0 }.groupBy { it.height }.toSortedMap(compareByDescending { it })
        for ((h, list) in byHeight) {
            val v = list.maxWithOrNull(compareBy<VideoStream> { it.format == MediaFormat.MPEG_4 }.thenBy { it.bitrate }) ?: continue
            val total = size(v) + audioSize
            val fps = if (v.fps >= 48) " ${v.fps}fps" else ""
            val name = when { h >= 2160 -> "4K"; h >= 1440 -> "2K"; h >= 1080 -> "Full HD"; h >= 720 -> "HD"; else -> "" }
            val detail = listOf(name, "MP4", if (total > 0) "~" + fmtSize(total) else "").filter { it.isNotEmpty() }.joinToString(" · ")
            out += Choice("${h}p$fps", detail,
                "${v.itag}+bestaudio[ext=m4a]/${v.itag}+bestaudio/bv*[height<=$h]+ba/b[height<=$h]", Choice.Kind.VIDEO, total)
        }
        if (out.isEmpty()) out += Choice("Best quality", "MP4", "bv*+ba/b", Choice.Kind.VIDEO, 0L)
        val a = if (audioSize > 0) "~" + fmtSize(audioSize) else ""
        out += Choice("Audio only", listOf("M4A", a).filter { it.isNotEmpty() }.joinToString(" · "),
            "bestaudio[ext=m4a]/bestaudio/best", Choice.Kind.M4A, audioSize)
        out += Choice("MP3", listOf("Audio", a).filter { it.isNotEmpty() }.joinToString(" · "),
            "bestaudio/best", Choice.Kind.MP3, audioSize)
        return out
    }

    // ------------------------------------------------------------------ playback

    private fun ytFactory(range: Boolean, rn: Boolean) =
        YoutubeHttpDataSource.Factory().setRangeParameterEnabled(range).setRnParameterEnabled(rn)

    private fun item(uri: String, url: String, info: StreamInfo?) = MediaItem.Builder()
        .setUri(uri)
        .setMediaId(url)
        .apply {
            if (info != null) setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(info.name)
                    .setArtist(info.uploaderName)
                    .setArtworkUri(thumbnail(info)?.let { Uri.parse(it) })
                    .build()
            )
        }
        .build()

    private fun sourceFor(s: Stream, info: StreamInfo, withMeta: Boolean): MediaSource {
        val mi = item(s.content, info.url, if (withMeta) info else null)
        val itag = (s as? VideoStream)?.itagItem ?: (s as? AudioStream)?.itagItem
        if (s.deliveryMethod == DeliveryMethod.PROGRESSIVE_HTTP && itag != null) {
            try {
                val manifest = YoutubeProgressiveDashManifestCreator.fromProgressiveStreamingUrl(s.content, itag, info.duration)
                val parsed = DashManifestParser().parse(Uri.parse(s.content), ByteArrayInputStream(manifest.toByteArray()))
                val f = ytFactory(range = true, rn = true)
                return DashMediaSource.Factory(DefaultDashChunkSource.Factory(f), f).createMediaSource(parsed, mi)
            } catch (_: Exception) {
                // fall through to a plain progressive stream
            }
        }
        return ProgressiveMediaSource.Factory(ytFactory(range = false, rn = true)).createMediaSource(mi)
    }

    /** Builds what the player needs for a picture height (or null if nothing playable). */
    fun mediaSource(info: StreamInfo, height: Int): MediaSource? {
        if (info.streamType == StreamType.LIVE_STREAM && info.hlsUrl.isNotEmpty()) {
            return HlsMediaSource.Factory(ytFactory(range = false, rn = false))
                .createMediaSource(item(info.hlsUrl, info.url, info))
        }
        val v = bestVideoOnly(info, height)
        val a = bestAudio(info)
        if (v != null && a != null) {
            return MergingMediaSource(sourceFor(v, info, true), sourceFor(a, info, false))
        }
        val muxed = info.videoStreams.filter { it.isUrl }
            .filter { it.height <= height }.ifEmpty { info.videoStreams.filter { it.isUrl } }
            .maxByOrNull { it.height }
        if (muxed != null) return sourceFor(muxed, info, true)
        if (info.hlsUrl.isNotEmpty()) {
            return HlsMediaSource.Factory(ytFactory(range = false, rn = false)).createMediaSource(item(info.hlsUrl, info.url, info))
        }
        return null
    }

    /** The height that will actually play for a requested one. */
    fun actualHeight(info: StreamInfo, height: Int): Int =
        bestVideoOnly(info, height)?.height
            ?: info.videoStreams.filter { it.height <= height }.maxOfOrNull { it.height }
            ?: 0
}
