package com.reel.player

import android.content.Context
import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import java.util.Locale

/**
 * Plays an online video (YouTube and others) in the app's shared player, so it gets the same
 * background play, notification and lock-screen controls as videos on the phone.
 */
@androidx.annotation.OptIn(UnstableApi::class)
object OnlinePlayer {

    fun play(ctx: Context, meta: VideoMeta, streamIndex: Int, startMs: Long = 0L): Boolean {
        val stream = meta.streams.getOrNull(streamIndex) ?: return false
        val player = PlayerHolder.get(ctx)
        PlayerHolder.uris = arrayListOf(meta.url)
        PlayerHolder.names = arrayListOf(meta.title)
        PlayerHolder.paths = arrayListOf("")

        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
            .setDefaultRequestProperties(stream.headers.filterKeys { !it.equals("User-Agent", true) })
        stream.headers.entries.firstOrNull { it.key.equals("User-Agent", true) }?.let { http.setUserAgent(it.value) }

        val item = MediaItem.Builder()
            .setUri(stream.videoUrl)
            .setMediaId(meta.url)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(meta.title)
                    .setArtist(meta.uploader)
                    .setArtworkUri(meta.thumbnail?.let { Uri.parse(it) })
                    .build()
            )
            .build()

        val source: MediaSource = when {
            stream.hls -> HlsMediaSource.Factory(http).createMediaSource(item)
            stream.audioUrl != null -> MergingMediaSource(
                ProgressiveMediaSource.Factory(http).createMediaSource(item),
                ProgressiveMediaSource.Factory(http).createMediaSource(MediaItem.fromUri(stream.audioUrl))
            )
            else -> ProgressiveMediaSource.Factory(http).createMediaSource(item)
        }
        player.setPlaybackSpeed(1f)
        player.setMediaSource(source, startMs)
        player.prepare()
        player.playWhenReady = true
        return true
    }

    /** "1.2M views" */
    fun views(n: Long): String = when {
        n >= 1_000_000_000L -> String.format(Locale.US, "%.1fB views", n / 1e9)
        n >= 1_000_000L -> String.format(Locale.US, "%.1fM views", n / 1e6)
        n >= 1_000L -> String.format(Locale.US, "%.0fK views", n / 1e3)
        n > 0 -> "$n views"
        else -> ""
    }
}
