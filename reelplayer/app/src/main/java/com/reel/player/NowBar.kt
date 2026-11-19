package com.reel.player

import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.ImageButton
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.recyclerview.widget.RecyclerView

/**
 * The "Now playing" strip at the bottom of a screen: shows what plays in the background,
 * with play/pause and stop. Tapping it opens the video again (its YouTube page for online videos).
 */
class NowBar(private val a: AppCompatActivity, private val list: RecyclerView?) {

    private val bar: View = a.findViewById(R.id.nowBar)
    private val title: TextView = a.findViewById(R.id.nowTitle)
    private val time: TextView = a.findViewById(R.id.nowTime)
    private val play: ImageButton = a.findViewById(R.id.nowPlay)
    private val handler = Handler(Looper.getMainLooper())
    private val density = a.resources.displayMetrics.density

    private val ticker = object : Runnable {
        override fun run() {
            updateTime()
            handler.postDelayed(this, 1000)
        }
    }

    private val listener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) = update()
        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) = update()
        override fun onPlaybackStateChanged(playbackState: Int) = update()
    }

    init {
        bar.setOnClickListener { open() }
        play.setOnClickListener { PlayerHolder.peek()?.let { p -> if (p.isPlaying) p.pause() else p.play() } }
        a.findViewById<View>(R.id.nowClose).setOnClickListener {
            PlayerHolder.peek()?.let { p -> p.stop(); p.clearMediaItems() }
            try { a.stopService(Intent(a, PlaybackService::class.java)) } catch (_: Exception) {}
            update()
        }
    }

    private fun open() {
        val p = PlayerHolder.peek() ?: return
        if (OnlinePlayer.isOnline()) {
            a.startActivity(
                Intent(a, YtVideoActivity::class.java)
                    .putExtra(YtVideoActivity.EXTRA_URL, p.currentMediaItem?.mediaId ?: return)
                    .putExtra(YtVideoActivity.EXTRA_TITLE, p.currentMediaItem?.mediaMetadata?.title?.toString())
            )
        } else {
            a.startActivity(Intent(a, PlayerActivity::class.java))
        }
    }

    fun start() {
        PlayerHolder.peek()?.addListener(listener)
        handler.post(ticker)
        update()
    }

    fun stop() {
        handler.removeCallbacks(ticker)
        PlayerHolder.peek()?.removeListener(listener)
    }

    fun update() {
        val p = PlayerHolder.peek()
        if (p == null || !PlayerHolder.isActive()) {
            bar.visibility = View.GONE
            list?.setPadding(0, 0, 0, (16 * density).toInt())
            return
        }
        bar.visibility = View.VISIBLE
        title.text = p.currentMediaItem?.mediaMetadata?.title ?: ""
        play.setImageResource(if (p.isPlaying) R.drawable.ic_pause else R.drawable.ic_play)
        list?.setPadding(0, 0, 0, (96 * density).toInt())
        updateTime()
    }

    private fun updateTime() {
        val p = PlayerHolder.peek() ?: return
        if (bar.visibility != View.VISIBLE) return
        val d = p.duration
        time.visibility = View.VISIBLE
        time.text = if (d > 0) "${fmtTime(p.currentPosition)} / ${fmtTime(d)}" else fmtTime(p.currentPosition)
    }
}
