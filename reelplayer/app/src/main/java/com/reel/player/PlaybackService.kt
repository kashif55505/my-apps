package com.reel.player

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.extractor.DefaultExtractorsFactory
import androidx.media3.session.MediaSession
import androidx.media3.session.CommandButton
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture

/**
 * One player for the whole app, shared by the player screen, the folder screen's
 * "now playing" bar and the background service. It is never released while the app runs,
 * so the screen and the service can never end up holding two different players.
 */
@androidx.annotation.OptIn(UnstableApi::class)
object PlayerHolder {
    private var player: ExoPlayer? = null
    var uris = ArrayList<String>()
    var names = ArrayList<String>()
    var paths = ArrayList<String>()

    fun peek(): ExoPlayer? = player

    fun get(ctx: Context): ExoPlayer = player ?: ExoPlayer.Builder(
        ctx.applicationContext,
        // Use the phone's own decoders first, and the built-in FFmpeg decoders when the phone lacks one.
        DefaultRenderersFactory(ctx.applicationContext)
            .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_ON)
            .setEnableDecoderFallback(true)
    )
        // Many MP3/AAC songs and some videos have no seek table. Without this they show no
        // duration and every skip or tap on the timeline restarts them from 0:00.
        .setMediaSourceFactory(
            DefaultMediaSourceFactory(
                ctx.applicationContext,
                DefaultExtractorsFactory()
                    .setConstantBitrateSeekingEnabled(true)
                    .setConstantBitrateSeekingAlwaysEnabled(true)
            )
        )
        .setSeekBackIncrementMs(10_000)
        .setSeekForwardIncrementMs(10_000)
        .setHandleAudioBecomingNoisy(true)          // pause when headphones are unplugged
        .setWakeMode(C.WAKE_MODE_LOCAL)             // keep playing with the screen off
        .setAudioAttributes(
            AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(),
            true
        )
        .build()
        .also { player = it }

    /** Whether something is loaded and meant to be playing. */
    fun isActive(): Boolean {
        val p = player ?: return false
        return p.mediaItemCount > 0 && p.playbackState != Player.STATE_ENDED && p.playbackState != Player.STATE_IDLE
    }
}

/**
 * Keeps playback alive while the player screen is closed or the app is in the background,
 * and shows the media notification (play / pause / next / previous, lock-screen and Bluetooth controls).
 */
@androidx.annotation.OptIn(UnstableApi::class)
class PlaybackService : MediaSessionService() {

    private var session: MediaSession? = null
    private lateinit var player: ExoPlayer
    private val handler = Handler(Looper.getMainLooper())
    private var currentId: String? = null

    private val closeCommand = SessionCommand(ACTION_CLOSE, Bundle.EMPTY)
    private val backCommand = SessionCommand(ACTION_BACK10, Bundle.EMPTY)
    private val fwdCommand = SessionCommand(ACTION_FWD10, Bundle.EMPTY)

    private val backButton = CommandButton.Builder()
        .setDisplayName("Back 10 seconds")
        .setIconResId(R.drawable.ic_replay10)
        .setSessionCommand(backCommand)
        .build()

    private val fwdButton = CommandButton.Builder()
        .setDisplayName("Forward 10 seconds")
        .setIconResId(R.drawable.ic_forward10)
        .setSessionCommand(fwdCommand)
        .build()

    /** The × button in the notification: stops playback and removes the notification. */
    private val closeButton = CommandButton.Builder()
        .setDisplayName("Close")
        .setIconResId(R.drawable.ic_close)
        .setSessionCommand(closeCommand)
        .build()

    private val sessionCallback = object : MediaSession.Callback {
        override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
            val commands = MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon()
                .add(closeCommand)
                .add(backCommand)
                .add(fwdCommand)
                .build()
            return MediaSession.ConnectionResult.AcceptedResultBuilder(session)
                .setAvailableSessionCommands(commands)
                .build()
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle
        ): ListenableFuture<SessionResult> {
            when (customCommand.customAction) {
                ACTION_CLOSE -> closeAll()
                ACTION_BACK10 -> if (player.isCurrentMediaItemSeekable) player.seekTo(maxOf(0L, player.currentPosition - 10_000))
                ACTION_FWD10 -> if (player.isCurrentMediaItemSeekable) {
                    val d = player.duration
                    val t = player.currentPosition + 10_000
                    player.seekTo(if (d > 0) minOf(t, d) else t)
                }
            }
            return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
        }
    }

    private fun closeAll() {
        savePosition()
        player.pause()
        player.stop()
        player.clearMediaItems()   // the player screen, if open, closes itself when the playlist empties
        stopSelf()
    }

    // Remember where each video stopped even when no screen is open.
    private val saver = object : Runnable {
        override fun run() {
            savePosition()
            handler.postDelayed(this, 10_000)
        }
    }

    private val listener = object : Player.Listener {
        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            val prev = currentId
            if (prev != null && prev != mediaItem?.mediaId && reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) {
                Prefs.setPos(this@PlaybackService, prev, 0L) // finished
            }
            currentId = mediaItem?.mediaId
        }

        override fun onPlaybackStateChanged(state: Int) {
            if (state == Player.STATE_ENDED) {
                player.currentMediaItem?.mediaId?.let { Prefs.setPos(this@PlaybackService, it, 0L) }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        player = PlayerHolder.get(this)
        currentId = player.currentMediaItem?.mediaId
        player.addListener(listener)
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        session = MediaSession.Builder(this, player)
            .setSessionActivity(open)
            .setCallback(sessionCallback)
            .setCustomLayout(ImmutableList.of(backButton, fwdButton, closeButton))
            .build()
        handler.postDelayed(saver, 10_000)
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    private fun savePosition() {
        if (!player.isPlaying) return
        val id = player.currentMediaItem?.mediaId ?: return
        val d = player.duration
        val p = player.currentPosition
        Prefs.setPos(this, id, if (d > 0 && p > d - 8000) 0L else p)
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Swiped away from recents: keep going only if background play is on and something is playing.
        val bg = Prefs.getInt(this, "bgPlay", 0) == 1
        if (!bg || !player.playWhenReady || player.mediaItemCount == 0) {
            player.pause()
            stopSelf()
        }
    }

    companion object {
        private const val ACTION_CLOSE = "com.reel.player.CLOSE"
        private const val ACTION_BACK10 = "com.reel.player.BACK10"
        private const val ACTION_FWD10 = "com.reel.player.FWD10"
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        if (player.isPlaying) savePosition()
        player.removeListener(listener)
        session?.release()
        session = null
        super.onDestroy()
    }
}
