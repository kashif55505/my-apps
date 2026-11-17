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
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.media3.ui.PlayerView
import com.bumptech.glide.Glide
import com.google.android.material.button.MaterialButton
import com.google.common.util.concurrent.ListenableFuture
import java.util.concurrent.Executors

/** Watch one online video inside the app, with its download qualities right underneath. */
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
    private lateinit var btnBg: MaterialButton

    private val io = Executors.newSingleThreadExecutor()
    private var meta: VideoMeta? = null
    private var streamIndex = 0
    private var goingFullScreen = false
    private var controllerFuture: ListenableFuture<MediaController>? = null
    private val player get() = PlayerHolder.get(this)

    private val listener = object : Player.Listener {
        override fun onPlaybackStateChanged(state: Int) {
            if (state == Player.STATE_READY) { cover.visibility = View.GONE; coverSpin.visibility = View.GONE }
        }

        override fun onPlayerError(error: PlaybackException) {
            val m = meta ?: return
            if (!isMine()) return
            // That way of streaming didn't work: quietly try the next one.
            val pos = player.currentPosition
            streamIndex++
            if (!OnlinePlayer.play(this@YtVideoActivity, m, streamIndex, pos)) {
                coverSpin.visibility = View.GONE
                playError.text = "This video can't be played here right now. You can still download it below."
                playError.visibility = View.VISIBLE
            }
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

        // 16:9 player box at the top, like YouTube.
        val box = findViewById<View>(R.id.playerBox)
        box.layoutParams.height = resources.displayMetrics.widthPixels * 9 / 16
        findViewById<View>(R.id.btnClose).setOnClickListener { finish() }

        val url = intent.getStringExtra(EXTRA_URL)
        if (url == null) { finish(); return }
        title.text = intent.getStringExtra(EXTRA_TITLE) ?: ""
        sub.text = intent.getStringExtra(EXTRA_CHANNEL) ?: ""
        intent.getStringExtra(EXTRA_THUMB)?.let { Glide.with(cover).load(it).centerCrop().into(cover) }

        player.addListener(listener)
        playerView.player = player
        playerView.setFullscreenButtonClickListener { openFullScreen() }
        try { startService(Intent(this, PlaybackService::class.java)) } catch (_: Exception) {}
        controllerFuture = MediaController.Builder(
            this, SessionToken(this, ComponentName(this, PlaybackService::class.java))
        ).buildAsync()

        updateBg()
        btnBg.setOnClickListener {
            val on = Prefs.getInt(this, "bgPlay", 0) != 1
            Prefs.setInt(this, "bgPlay", if (on) 1 else 0)
            updateBg()
            Toast.makeText(this, if (on) "Background play on: leave the app or lock the screen, it keeps playing"
                else "Background play off", Toast.LENGTH_SHORT).show()
        }
        findViewById<View>(R.id.btnFull).setOnClickListener { openFullScreen() }
        findViewById<View>(R.id.btnShare).setOnClickListener {
            startActivity(Intent.createChooser(
                Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, meta?.url ?: url), "Share"))
        }

        load(url)
    }

    private fun load(url: String) {
        io.execute {
            val r = runCatching { Engine.fetch(applicationContext, url) }
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                r.onSuccess { m ->
                    meta = m
                    title.text = m.title
                    sub.text = listOfNotNull(m.uploader, m.site).joinToString(" · ")
                    if (m.thumbnail != null && cover.drawable == null) Glide.with(cover).load(m.thumbnail).centerCrop().into(cover)
                    showChoices(m)
                    streamIndex = 0
                    if (!OnlinePlayer.play(this, m, 0)) {
                        coverSpin.visibility = View.GONE
                        playError.text = "This video can't be played here. You can still download it below."
                        playError.visibility = View.VISIBLE
                    }
                }
                r.onFailure {
                    coverSpin.visibility = View.GONE
                    choicesLoading.text = Engine.humanize(it)
                    playError.text = Engine.humanize(it)
                    playError.visibility = View.VISIBLE
                }
            }
        }
    }

    private fun showChoices(m: VideoMeta) {
        choicesLoading.visibility = View.GONE
        choices.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (c in m.choices) {
            val row = inflater.inflate(R.layout.item_choice, choices, false)
            row.findViewById<TextView>(R.id.label).text = c.label
            row.findViewById<TextView>(R.id.detail).text = c.detail
            row.setOnClickListener { YouTubeActivity.startDownload(this, m, c) }
            choices.addView(row)
        }
    }

    /** True while the shared player is still playing this page's video. */
    private fun isMine(): Boolean = player.currentMediaItem?.mediaId == meta?.url

    private fun bgOn() = Prefs.getInt(this, "bgPlay", 0) == 1

    private fun updateBg() {
        val on = bgOn()
        btnBg.text = if (on) "Background: On" else "Background"
        btnBg.iconTint = android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(this, if (on) R.color.accent else R.color.fg))
    }

    private fun openFullScreen() {
        if (!isMine()) return
        goingFullScreen = true
        startActivity(Intent(this, PlayerActivity::class.java).putExtra(PlayerActivity.EXTRA_ATTACH, true))
    }

    override fun onStart() {
        super.onStart()
        goingFullScreen = false
        playerView.player = player
        setVideo(true)
    }

    /** Stop decoding the picture while nobody can see it (saves battery in background play). */
    private fun setVideo(on: Boolean) {
        val p = player
        val off = p.trackSelectionParameters.disabledTrackTypes.contains(androidx.media3.common.C.TRACK_TYPE_VIDEO)
        if (off == !on) return
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(androidx.media3.common.C.TRACK_TYPE_VIDEO, !on).build()
    }

    override fun onStop() {
        super.onStop()
        if (goingFullScreen || isFinishing) return
        // Leaving the app: keep the sound only when background play is on.
        if (!isMine()) return
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
