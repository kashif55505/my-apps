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
import android.text.Editable
import android.text.TextWatcher
import android.widget.ArrayAdapter
import android.widget.EditText
import androidx.appcompat.widget.ListPopupWindow
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
import com.reel.player.yt.Yt
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/** Search YouTube, play inside the app, or pick a quality and download. */
class YouTubeActivity : AppCompatActivity() {

    private lateinit var query: EditText
    private lateinit var results: RecyclerView
    private lateinit var loading: View
    private lateinit var loadingText: TextView
    private lateinit var message: TextView
    private val io = Executors.newSingleThreadExecutor()
    private val items = ArrayList<YtItem>()
    private var page: Yt.Results? = null
    private var searching = false
    private var loadingMore = false
    private lateinit var nowBar: NowBar
    private lateinit var popup: ListPopupWindow
    private val suggestIo = Executors.newSingleThreadExecutor()
    private val ui = android.os.Handler(android.os.Looper.getMainLooper())
    private var suggestRun: Runnable? = null
    private var settingText = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_youtube)
        findViewById<MaterialToolbar>(R.id.toolbar).setNavigationOnClickListener { finish() }
        query = findViewById(R.id.query)
        results = findViewById(R.id.results)
        loading = findViewById(R.id.loading)
        loadingText = findViewById(R.id.loadingText)
        message = findViewById(R.id.message)
        val lm = LinearLayoutManager(this)
        results.layoutManager = lm
        results.adapter = Adapter()
        nowBar = NowBar(this, results)
        results.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(rv: RecyclerView, dx: Int, dy: Int) {
                if (dy > 0 && lm.findLastVisibleItemPosition() >= items.size - 4) loadMore()
            }
        })

        setupSuggestions()
        findViewById<View>(R.id.btnSearch).setOnClickListener { go() }
        query.setOnEditorActionListener { _, id, _ ->
            if (id == EditorInfo.IME_ACTION_SEARCH || id == EditorInfo.IME_ACTION_DONE) { go(); true } else false
        }

        val last = getSharedPreferences("reel", MODE_PRIVATE).getString("ytQuery", null)
        settingText = true
        query.setText(last ?: "")
        settingText = false
        search(last ?: "new songs")
    }

    override fun onStart() {
        super.onStart()
        nowBar.start()
    }

    override fun onStop() {
        super.onStop()
        nowBar.stop()
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdown()
        suggestIo.shutdown()
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
        addHistory(text)
        popup.dismiss()
        search(text)
    }

    // ------------------------------------------------------------------ suggestions while typing

    private fun history(): List<String> =
        getSharedPreferences("reel", MODE_PRIVATE).getString("ytHistory", "")!!.split('\n').filter { it.isNotBlank() }

    private fun addHistory(q: String) {
        val list = (listOf(q) + history().filter { !it.equals(q, true) }).take(15)
        getSharedPreferences("reel", MODE_PRIVATE).edit().putString("ytHistory", list.joinToString("\n")).apply()
    }

    private fun setupSuggestions() {
        popup = ListPopupWindow(this)
        popup.anchorView = query
        popup.isModal = false
        popup.inputMethodMode = ListPopupWindow.INPUT_METHOD_NEEDED
        popup.setOnItemClickListener { parent, _, pos, _ ->
            val text = (parent.getItemAtPosition(pos) as String).removePrefix("🕘  ")
            settingText = true
            query.setText(text)
            query.setSelection(text.length)
            settingText = false
            go()
        }
        query.setOnFocusChangeListener { _, has -> if (has && query.text.isBlank()) showList(history().map { "🕘  $it" }) }
        query.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) {
                if (settingText) return
                val q = s?.toString()?.trim() ?: ""
                suggestRun?.let { ui.removeCallbacks(it) }
                if (q.isEmpty()) { showList(history().map { "🕘  $it" }); return }
                if (q.startsWith("http")) { popup.dismiss(); return }
                val r = Runnable {
                    suggestIo.execute {
                        val found = runCatching { Yt.suggestions(q) }.getOrDefault(emptyList())
                        val past = history().filter { it.contains(q, true) }.take(3).map { "🕘  $it" }
                        runOnUiThread {
                            if (isDestroyed || query.text.toString().trim() != q) return@runOnUiThread
                            showList((past + found.filter { f -> past.none { it.endsWith(f) } }).take(10))
                        }
                    }
                }
                suggestRun = r
                ui.postDelayed(r, 200)   // wait for a short pause in typing
            }
        })
    }

    private fun showList(items: List<String>) {
        if (items.isEmpty() || !query.hasFocus()) { popup.dismiss(); return }
        popup.setAdapter(ArrayAdapter(this, android.R.layout.simple_list_item_1, items))
        popup.width = query.width
        if (!popup.isShowing) popup.show() else popup.listView?.invalidateViews()
    }

    private fun search(q: String) {
        if (searching) return
        searching = true
        message.visibility = View.GONE
        loading.visibility = View.VISIBLE
        loadingText.text = "Searching…"
        io.execute {
            // Fast engine first; the slower yt-dlp search only if that fails.
            val r = runCatching { Yt.search(q) }
            val fallback = if (r.isFailure) runCatching { Engine.search(applicationContext, q) } else null
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                searching = false
                loading.visibility = View.GONE
                val list = r.getOrNull()?.items ?: fallback?.getOrNull()
                page = r.getOrNull()
                if (list == null) {
                    val e = fallback?.exceptionOrNull() ?: r.exceptionOrNull()
                    message.text = (e?.let { Engine.humanize(it) } ?: "Search failed") + "\n\nTap Search to try again."
                    message.visibility = View.VISIBLE
                    return@runOnUiThread
                }
                items.clear()
                items += list
                results.adapter?.notifyDataSetChanged()
                results.scrollToPosition(0)
                if (list.isEmpty()) { message.text = "No videos found for “$q”"; message.visibility = View.VISIBLE }
            }
        }
    }

    private fun loadMore() {
        val p = page ?: return
        if (loadingMore || searching || p.next == null) return
        loadingMore = true
        io.execute {
            val r = runCatching { Yt.more(p) }
            runOnUiThread {
                loadingMore = false
                if (isDestroyed) return@runOnUiThread
                r.onSuccess {
                    page = it
                    val start = items.size
                    val seen = items.map { x -> x.id }.toHashSet()
                    val add = it.items.filter { x -> x.id !in seen }
                    items += add
                    results.adapter?.notifyItemRangeInserted(start, add.size)
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
        fun pickQuality(a: AppCompatActivity, io: ExecutorService, url: String, title: String) {
            val dialog = MaterialAlertDialogBuilder(a)
                .setTitle("Download")
                .setMessage("Getting qualities…")
                .setNegativeButton("Cancel", null)
                .show()
            io.execute {
                val choices = runCatching { Yt.downloadChoices(Yt.info(url)) }.getOrNull()
                val meta = if (choices == null) runCatching { Engine.fetch(a.applicationContext, url) } else null
                a.runOnUiThread {
                    if (a.isDestroyed || !dialog.isShowing) return@runOnUiThread
                    dialog.dismiss()
                    val list = choices ?: meta?.getOrNull()?.choices
                    if (list == null) {
                        Toast.makeText(a, meta?.exceptionOrNull()?.let { Engine.humanize(it) } ?: "Couldn't get qualities", Toast.LENGTH_LONG).show()
                        return@runOnUiThread
                    }
                    val labels = list.map { "${it.label}   ·   ${it.detail}" }.toTypedArray()
                    MaterialAlertDialogBuilder(a)
                        .setTitle(title)
                        .setItems(labels) { _, which -> startDownload(a, url, title, list[which]) }
                        .setNegativeButton("Cancel", null)
                        .show()
                }
            }
        }

        fun startDownload(a: Activity, url: String, title: String, c: Choice) {
            if (Build.VERSION.SDK_INT < 29 &&
                ContextCompat.checkSelfPermission(a, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED
            ) {
                a.requestPermissions(arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), 7)
                Toast.makeText(a, "Allow storage, then tap the quality again", Toast.LENGTH_LONG).show()
                return
            }
            DownloadService.enqueue(a, url, title, c)
            Toast.makeText(a, "Downloading ${c.label} · see progress in notifications", Toast.LENGTH_SHORT).show()
        }
    }
}
