package com.reel.player

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import java.util.concurrent.Executors

/** Search YouTube, play inside the app, or pick a quality and download. */
class YouTubeActivity : AppCompatActivity() {

    private lateinit var query: EditText
    private lateinit var results: RecyclerView
    private lateinit var loading: View
    private lateinit var loadingText: TextView
    private lateinit var message: TextView
    private val io = Executors.newSingleThreadExecutor()
    private var items: List<YtItem> = emptyList()
    private var searching = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_youtube)
        findViewById<MaterialToolbar>(R.id.toolbar).setNavigationOnClickListener { finish() }
        query = findViewById(R.id.query)
        results = findViewById(R.id.results)
        loading = findViewById(R.id.loading)
        loadingText = findViewById(R.id.loadingText)
        message = findViewById(R.id.message)
        results.layoutManager = LinearLayoutManager(this)
        results.adapter = Adapter()

        findViewById<View>(R.id.btnSearch).setOnClickListener { go() }
        query.setOnEditorActionListener { _, id, _ ->
            if (id == EditorInfo.IME_ACTION_SEARCH || id == EditorInfo.IME_ACTION_DONE) { go(); true } else false
        }

        val last = getSharedPreferences("reel", MODE_PRIVATE).getString("ytQuery", null)
        query.setText(last ?: "")
        search(last ?: "new songs")
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdown()
    }

    private fun go() {
        val text = query.text.toString().trim()
        if (text.isEmpty()) return
        getSystemService(InputMethodManager::class.java)?.hideSoftInputFromWindow(query.windowToken, 0)
        query.clearFocus()
        // A pasted link opens the video straight away.
        val link = Regex("https?://\\S+").find(text)?.value
        if (link != null) { openVideo(this, link, null, null, null); return }
        getSharedPreferences("reel", MODE_PRIVATE).edit().putString("ytQuery", text).apply()
        search(text)
    }

    private fun search(q: String) {
        if (searching) return
        searching = true
        message.visibility = View.GONE
        loading.visibility = View.VISIBLE
        loadingText.text = if (items.isEmpty()) "Loading videos… The first time can take up to a minute." else "Searching…"
        io.execute {
            val r = runCatching { Engine.search(applicationContext, q) }
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                searching = false
                loading.visibility = View.GONE
                r.onSuccess {
                    items = it
                    results.adapter?.notifyDataSetChanged()
                    results.scrollToPosition(0)
                    if (it.isEmpty()) { message.text = "No videos found for “$q”"; message.visibility = View.VISIBLE }
                }
                r.onFailure {
                    message.text = Engine.humanize(it) + "\n\nTap Search to try again."
                    message.visibility = View.VISIBLE
                }
            }
        }
    }

    private inner class Adapter : RecyclerView.Adapter<Adapter.VH>() {
        inner class VH(v: View) : RecyclerView.ViewHolder(v) {
            val thumb: ImageView = v.findViewById(R.id.thumb)
            val dur: TextView = v.findViewById(R.id.dur)
            val title: TextView = v.findViewById(R.id.title)
            val meta: TextView = v.findViewById(R.id.meta)
            val download: View = v.findViewById(R.id.download)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            VH(LayoutInflater.from(parent.context).inflate(R.layout.item_yt, parent, false))

        override fun getItemCount() = items.size

        override fun onBindViewHolder(h: VH, position: Int) {
            val it = items[position]
            h.title.text = it.title
            h.meta.text = listOfNotNull(it.channel, OnlinePlayer.views(it.views).ifEmpty { null }).joinToString(" · ")
            h.dur.text = fmtTime((it.duration * 1000).toLong())
            h.dur.visibility = if (it.duration > 0) View.VISIBLE else View.GONE
            Glide.with(h.thumb).load(it.thumbnail).centerCrop().into(h.thumb)
            h.itemView.setOnClickListener { _ -> openVideo(this@YouTubeActivity, it.url, it.title, it.channel, it.thumbnail) }
            h.download.setOnClickListener { _ -> pickQuality(this@YouTubeActivity, io, it.url, it.title) }
        }
    }

    companion object {
        fun openVideo(a: Activity, url: String, title: String?, channel: String?, thumb: String?) {
            a.startActivity(
                Intent(a, YtVideoActivity::class.java)
                    .putExtra(YtVideoActivity.EXTRA_URL, url)
                    .putExtra(YtVideoActivity.EXTRA_TITLE, title)
                    .putExtra(YtVideoActivity.EXTRA_CHANNEL, channel)
                    .putExtra(YtVideoActivity.EXTRA_THUMB, thumb)
            )
        }

        /** Quality list in a dialog, straight from the search results. */
        fun pickQuality(a: AppCompatActivity, io: java.util.concurrent.ExecutorService, url: String, title: String) {
            val dialog = MaterialAlertDialogBuilder(a)
                .setTitle("Download")
                .setMessage("Getting qualities…")
                .setNegativeButton("Cancel", null)
                .show()
            io.execute {
                val r = runCatching { Engine.fetch(a.applicationContext, url) }
                a.runOnUiThread {
                    if (a.isDestroyed || !dialog.isShowing) return@runOnUiThread
                    dialog.dismiss()
                    r.onFailure { Toast.makeText(a, Engine.humanize(it), Toast.LENGTH_LONG).show() }
                    r.onSuccess { meta ->
                        val labels = meta.choices.map { "${it.label}   ·   ${it.detail}" }.toTypedArray()
                        MaterialAlertDialogBuilder(a)
                            .setTitle(meta.title)
                            .setItems(labels) { _, which -> startDownload(a, meta, meta.choices[which]) }
                            .setNegativeButton("Cancel", null)
                            .show()
                    }
                }
            }
        }

        fun startDownload(a: Activity, meta: VideoMeta, c: Choice) {
            if (Build.VERSION.SDK_INT < 29 &&
                ContextCompat.checkSelfPermission(a, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED
            ) {
                a.requestPermissions(arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), 7)
                Toast.makeText(a, "Allow storage, then tap the quality again", Toast.LENGTH_LONG).show()
                return
            }
            DownloadService.enqueue(a, meta.url, meta.title, c)
            Toast.makeText(a, "Downloading ${c.label} · see progress in notifications", Toast.LENGTH_SHORT).show()
        }
    }
}
