package com.reel.player

import android.content.ComponentName
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.media3.common.C
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.media3.ui.PlayerView
import com.bumptech.glide.Glide
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.common.util.concurrent.ListenableFuture
import com.reel.player.yt.Yt
import org.schabi.newpipe.extractor.stream.StreamInfo
import java.util.concurrent.Executors

/** Watch one online video inside the app, with quality choice and downloads right underneath. */
@androidx.annotation.OptIn(UnstableApi::class)
class YtVideoActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_URL = "url"
        const val EXTRA_TITLE = "title"
        const val EXTRA_CHANNEL = "channel"
        const val EXTRA_THUMB = "thumb"
    }

    private lateinit var playerView: PlayerView
    private lateinit var cover: ImageView
    private lateinit var coverSpin: View
    private lateinit var title: TextView
    private lateinit var sub: TextView
    private lateinit var playError: TextView
    private lateinit var choices: LinearLayout
    private lateinit var choicesLoading: TextView
    private lateinit var btnBg: TextView
    private lateinit var btnQuality: TextView

    private val io = Executors.newSingleThreadExecutor()
    private var url = ""
    private var info: StreamInfo? = null      // fast engine (NewPipe)
    private var meta: VideoMeta? = null       // fallback engine (yt-dlp)
    private var streamIndex = 0
    private var triedFallback = false
    private var goingFullScreen = false
    private var controllerFuture: ListenableFuture<MediaController>? = null
    private val player get() = PlayerHolder.get(this)

    private var height: Int
        get() = Prefs.getInt(this, "ytHeight", 720)
        set(v) = Prefs.setInt(this, "ytHeight", v)

    private val listener = object : Player.Listener {
        override fun onPlaybackStateChanged(state: Int) {
            if (state == Player.STATE_READY && isMine()) { cover.visibility = View.GONE; coverSpin.visibility = View.GONE }
        }

        override fun onPlayerError(error: PlaybackException) {
            if (!isMine()) return
            playNextWay(player.currentPosition)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_yt_video)
        playerView = findViewById(R.id.playerView)
        cover = findViewById(R.id.cover)
        coverSpin = findViewById(R.id.coverSpin)
        title = findViewById(R.id.title)
        sub = findViewById(R.id.sub)
        playError = findViewById(R.id.playError)
        choices = findViewById(R.id.choices)
        choicesLoading = findViewById(R.id.choicesLoading)
        btnBg = findViewById(R.id.btnBg)
        btnQuality = findViewById(R.id.btnQuality)

        // 16:9 player box at the top, like YouTube.
        findViewById<View>(R.id.playerBox).layoutParams.height = resources.displayMetrics.widthPixels * 9 / 16
        findViewById<View>(R.id.btnClose).setOnClickListener { finish() }

        player.addListener(listener)
        playerView.player = player
        playerView.setFullscreenButtonClickListener { openFullScreen() }
        try { startService(Intent(this, PlaybackService::class.java)) } catch (_: Exception) {}
        controllerFuture = MediaController.Builder(
            this, SessionToken(this, ComponentName(this, PlaybackService::class.java))
        ).buildAsync()

        updateBg()
        btnBg.setOnClickListener {
            val on = !bgOn()
            Prefs.setInt(this, "bgPlay", if (on) 1 else 0)
            updateBg()
            Toast.makeText(this, if (on) "Background play on: leave the app or lock the screen, it keeps playing"
                else "Background play off", Toast.LENGTH_SHORT).show()
        }
        btnQuality.setOnClickListener { chooseQuality() }
        findViewById<View>(R.id.btnFull).setOnClickListener { openFullScreen() }
        findViewById<View>(R.id.btnShare).setOnClickListener {
            startActivity(Intent.createChooser(
                Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, url), "Share"))
        }

        open(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val u = intent.getStringExtra(EXTRA_URL) ?: return
        if (u == url) return
        setIntent(intent)
        open(intent)
    }

    private fun open(i: Intent) {
        url = i.getStringExtra(EXTRA_URL) ?: run { finish(); return }
        info = null; meta = null; streamIndex = 0; triedFallback = false
        playError.visibility = View.GONE
        choices.removeAllViews()
        choicesLoading.visibility = View.VISIBLE
        choicesLoading.text = "Getting qualities…"
        title.text = i.getStringExtra(EXTRA_TITLE) ?: ""
        sub.text = i.getStringExtra(EXTRA_CHANNEL) ?: ""
        updateQualityLabel()

        // Coming back to a video that is still playing (from the notification or background).
        val resuming = isMine()
        if (resuming) {
            cover.visibility = View.GONE
            coverSpin.visibility = View.GONE
        } else {
            cover.visibility = View.VISIBLE
            coverSpin.visibility = View.VISIBLE
            i.getStringExtra(EXTRA_THUMB)?.let { Glide.with(cover).load(it).centerCrop().into(cover) }
        }
        load(resuming)
    }

    private fun load(resuming: Boolean) {
        val u = url
        io.execute {
            val r = runCatching { Yt.info(u) }
            runOnUiThread {
                if (isDestroyed || u != url) return@runOnUiThread
                r.onSuccess { inf ->
                    info = inf
                    title.text = inf.name
                    sub.text = listOfNotNull(inf.uploaderName, OnlinePlayer.views(inf.viewCount).ifEmpty { null }).joinToString(" · ")
                    if (cover.visibility == View.VISIBLE) Yt.thumbnail(inf)?.let { Glide.with(cover).load(it).centerCrop().into(cover) }
                    showChoices(Yt.downloadChoices(inf))
                    updateQualityLabel()
                    if (!resuming && !OnlinePlayer.playYt(this, inf, height)) playNextWay(0L)
                }
                r.onFailure { loadFallback(resuming, 0L) }
            }
        }
    }

    /** The slower yt-dlp engine, used when the fast one can't handle a video. */
    private fun loadFallback(resuming: Boolean, startMs: Long) {
        triedFallback = true
        val u = url
        io.execute {
            val r = runCatching { Engine.fetch(applicationContext, u) }
            runOnUiThread {
                if (isDestroyed || u != url) return@runOnUiThread
                r.onSuccess { m ->
                    meta = m
                    if (info == null) {
                        title.text = m.title
                        sub.text = listOfNotNull(m.uploader, m.site).joinToString(" · ")
                        showChoices(m.choices)
                    }
                    streamIndex = 0
                    if (!resuming && !OnlinePlayer.play(this, m, 0, startMs)) showPlayError()
                }
                r.onFailure {
                    coverSpin.visibility = View.GONE
                    if (info == null) choicesLoading.text = Engine.humanize(it)
                    showPlayError(Engine.humanize(it))
                }
            }
        }
    }

    /** Playback failed: quietly try the next way of streaming this video. */
    private fun playNextWay(pos: Long) {
        val m = meta
        when {
            m != null -> {
                streamIndex++
                if (!OnlinePlayer.play(this, m, streamIndex, pos)) showPlayError()
            }
            !triedFallback -> loadFallback(false, pos)
            else -> showPlayError()
        }
    }

    private fun showPlayError(msg: String? = null) {
        coverSpin.visibility = View.GONE
        playError.text = msg ?: "This video can't be played here right now. You can still download it below."
        playError.visibility = View.VISIBLE
    }

    private fun showChoices(list: List<Choice>) {
        choicesLoading.visibility = View.GONE
        choices.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (c in list) {
            val row = inflater.inflate(R.layout.item_choice, choices, false)
            row.findViewById<TextView>(R.id.label).text = c.label
            row.findViewById<TextView>(R.id.detail).text = c.detail
            row.setOnClickListener { YouTubeActivity.startDownload(this, url, title.text.toString(), c) }
            choices.addView(row)
        }
    }

    // ------------------------------------------------------------------ quality

    private fun updateQualityLabel() {
        val inf = info
        btnQuality.text = if (inf != null) {
            val h = Yt.actualHeight(inf, height)
            if (h > 0) "${h}p" else "Quality"
        } else "Quality"
    }

    private fun chooseQuality() {
        val inf = info
        if (inf == null) {
            Toast.makeText(this, if (meta != null) "This video plays in automatic quality" else "Still loading…", Toast.LENGTH_SHORT).show()
            return
        }
        val heights = Yt.playHeights(inf)
        if (heights.isEmpty()) return
        val current = Yt.actualHeight(inf, height)
        val labels = heights.map { h ->
            val name = when { h >= 2160 -> "  4K"; h >= 1440 -> "  2K"; h >= 1080 -> "  Full HD"; h >= 720 -> "  HD"; else -> "" }
            "${h}p$name"
        }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle("Play quality")
            .setSingleChoiceItems(labels, heights.indexOf(current)) { d, which ->
                d.dismiss()
                val h = heights[which]
                if (h == current) return@setSingleChoiceItems
                height = h
                updateQualityLabel()
                val pos = player.currentPosition
                val playing = player.playWhenReady
                if (OnlinePlayer.playYt(this, inf, h, pos)) player.playWhenReady = playing
            }
            .show()
    }

    // ------------------------------------------------------------------ lifecycle

    /** True while the shared player is still playing this page's video. */
    private fun isMine(): Boolean {
        val id = PlayerHolder.peek()?.currentMediaItem?.mediaId ?: return false
        return id == url || id == info?.url || id == meta?.url
    }

    private fun bgOn() = Prefs.getInt(this, "bgPlay", 0) == 1

    private fun updateBg() {
        val on = bgOn()
        btnBg.text = if (on) "Background ✓" else "Background"
        val color = ContextCompat.getColor(this, if (on) R.color.accent else R.color.fg)
        btnBg.setTextColor(color)
        btnBg.compoundDrawableTintList = android.content.res.ColorStateList.valueOf(color)
    }

    private fun openFullScreen() {
        if (!isMine()) return
        goingFullScreen = true
        startActivity(Intent(this, PlayerActivity::class.java).putExtra(PlayerActivity.EXTRA_ATTACH, true))
    }

    /** Stop decoding the picture while nobody can see it (saves battery in background play). */
    private fun setVideo(on: Boolean) {
        val p = player
        val off = p.trackSelectionParameters.disabledTrackTypes.contains(C.TRACK_TYPE_VIDEO)
        if (off == !on) return
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_VIDEO, !on).build()
    }

    override fun onStart() {
        super.onStart()
        goingFullScreen = false
        playerView.player = player
        setVideo(true)
    }

    override fun onStop() {
        super.onStop()
        if (goingFullScreen || isFinishing || !isMine()) return
        // Leaving the app: keep the sound only when background play is on.
        if (bgOn() && player.playWhenReady) setVideo(false) else player.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdown()
        PlayerHolder.peek()?.removeListener(listener)
        playerView.player = null
        if (isFinishing && isMine() && !(bgOn() && player.playWhenReady)) {
            player.stop()
            player.clearMediaItems()
            setVideo(true)
            try { stopService(Intent(this, PlaybackService::class.java)) } catch (_: Exception) {}
        }
        controllerFuture?.let { MediaController.releaseFuture(it) }
    }
}
