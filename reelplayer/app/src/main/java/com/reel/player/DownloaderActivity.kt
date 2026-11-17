package com.reel.player

import android.Manifest
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.bumptech.glide.Glide
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import java.util.concurrent.Executors

class DownloaderActivity : AppCompatActivity() {

    private lateinit var urlInput: EditText
    private lateinit var btnFind: MaterialButton
    private lateinit var statusRow: View
    private lateinit var statusText: TextView
    private lateinit var errorText: TextView
    private lateinit var result: View
    private lateinit var thumb: ImageView
    private lateinit var dur: TextView
    private lateinit var title: TextView
    private lateinit var sub: TextView
    private lateinit var choices: LinearLayout
    private lateinit var jobs: LinearLayout
    private lateinit var jobsEmpty: TextView

    private val io = Executors.newSingleThreadExecutor()
    private var meta: VideoMeta? = null
    private var pendingChoice: Choice? = null
    private var busy = false

    private val jobsListener: () -> Unit = { renderJobs() }

    private val askNotify = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val askStorage = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val c = pendingChoice
        pendingChoice = null
        if (granted && c != null) start(c)
        else if (!granted) toast("Storage permission is needed to save videos")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_downloader)
        val toolbar = findViewById<MaterialToolbar>(R.id.toolbar)
        toolbar.setNavigationOnClickListener { finish() }

        urlInput = findViewById(R.id.urlInput)
        btnFind = findViewById(R.id.btnFind)
        statusRow = findViewById(R.id.statusRow)
        statusText = findViewById(R.id.statusText)
        errorText = findViewById(R.id.errorText)
        result = findViewById(R.id.result)
        thumb = findViewById(R.id.thumb)
        dur = findViewById(R.id.dur)
        title = findViewById(R.id.title)
        sub = findViewById(R.id.sub)
        choices = findViewById(R.id.choices)
        jobs = findViewById(R.id.jobs)
        jobsEmpty = findViewById(R.id.jobsEmpty)
        findViewById<FrameLayout>(R.id.thumbFrame).clipToOutline = true

        findViewById<View>(R.id.btnPaste).setOnClickListener { pasteFromClipboard() }
        btnFind.setOnClickListener { find() }
        urlInput.setOnEditorActionListener { _, id, _ ->
            if (id == EditorInfo.IME_ACTION_GO) { find(); true } else false
        }

        // Unpack the download engine in the background so the first search is quicker.
        io.execute { try { Engine.init(applicationContext) } catch (_: Exception) {} }

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)

        handleShare(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    override fun onStart() {
        super.onStart()
        Downloads.addListener(jobsListener)
        renderJobs()
    }

    override fun onStop() {
        super.onStop()
        Downloads.removeListener(jobsListener)
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdown()
    }

    /** "Share → Reel Player" from the YouTube, Facebook or Instagram app lands here. */
    private fun handleShare(i: Intent?) {
        if (i?.action != Intent.ACTION_SEND) return
        val text = i.getStringExtra(Intent.EXTRA_TEXT) ?: return
        val url = extractUrl(text) ?: return
        urlInput.setText(url)
        find()
    }

    private fun extractUrl(text: String): String? =
        Regex("https?://\\S+").find(text)?.value?.trimEnd('.', ',', ')', '"', '\'')

    private fun pasteFromClipboard() {
        val cm = getSystemService(ClipboardManager::class.java)
        val text = cm?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        val url = text?.let { extractUrl(it) }
        if (url == null) {
            toast("Copy a video link first, then tap Paste")
        } else {
            urlInput.setText(url)
            urlInput.setSelection(url.length)
            find()
        }
    }

    // ------------------------------------------------------------------ find

    private fun find() {
        if (busy) return
        val url = extractUrl(urlInput.text.toString().trim())
        if (url == null) {
            toast("Paste a video link that starts with https://")
            return
        }
        hideKeyboard()
        busy = true
        btnFind.isEnabled = false
        errorText.visibility = View.GONE
        result.visibility = View.GONE
        statusRow.visibility = View.VISIBLE
        statusText.text = "Getting video details… The first time can take up to a minute."
        io.execute {
            val r = runCatching { Engine.fetch(applicationContext, url) }
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                busy = false
                btnFind.isEnabled = true
                statusRow.visibility = View.GONE
                r.onSuccess { showMeta(it) }
                r.onFailure {
                    errorText.text = Engine.humanize(it)
                    errorText.visibility = View.VISIBLE
                }
            }
        }
    }

    private fun showMeta(m: VideoMeta) {
        meta = m
        result.visibility = View.VISIBLE
        title.text = m.title
        sub.text = listOfNotNull(m.site, m.uploader).joinToString(" · ")
        dur.visibility = if (m.duration > 0) View.VISIBLE else View.GONE
        dur.text = fmtTime((m.duration * 1000).toLong())
        if (m.thumbnail != null) Glide.with(thumb).load(m.thumbnail).centerCrop().into(thumb)
        else thumb.setImageDrawable(null)

        choices.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (c in m.choices) {
            val row = inflater.inflate(R.layout.item_choice, choices, false)
            row.findViewById<TextView>(R.id.label).text = c.label
            row.findViewById<TextView>(R.id.detail).text = c.detail
            row.setOnClickListener { start(c) }
            choices.addView(row)
        }
    }

    private fun start(c: Choice) {
        val m = meta ?: return
        if (Build.VERSION.SDK_INT < 29 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED
        ) {
            pendingChoice = c
            askStorage.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
            return
        }
        DownloadService.enqueue(this, m.url, m.title, c)
        toast("Downloading ${c.label}")
    }

    // ------------------------------------------------------------------ download list

    private fun renderJobs() {
        val list = Downloads.jobs.toList()
        jobsEmpty.visibility = if (list.isEmpty()) View.VISIBLE else View.GONE
        // Reuse rows so progress updates don't flicker.
        while (jobs.childCount > list.size) jobs.removeViewAt(jobs.childCount - 1)
        val inflater = LayoutInflater.from(this)
        list.forEachIndexed { i, job ->
            val row = jobs.getChildAt(i) ?: inflater.inflate(R.layout.item_job, jobs, false).also { jobs.addView(it) }
            bindJob(row, job)
        }
    }

    private fun bindJob(row: View, job: Downloads.Job) {
        val status = row.findViewById<TextView>(R.id.jobStatus)
        val bar = row.findViewById<ProgressBar>(R.id.jobProgress)
        val action = row.findViewById<ImageButton>(R.id.jobAction)
        row.findViewById<TextView>(R.id.jobTitle).text = job.title
        when (job.status) {
            Downloads.Status.QUEUED -> {
                status.text = "${job.choice.label} · Waiting…"
                bar.visibility = View.VISIBLE
                bar.isIndeterminate = true
                action.setImageResource(R.drawable.ic_close)
                action.contentDescription = "Cancel"
                action.setOnClickListener { DownloadService.cancel(job.id) }
            }
            Downloads.Status.RUNNING -> {
                val p = job.progress
                val eta = if (job.eta > 0) " · ${fmtTime(job.eta * 1000)} left" else ""
                status.text = if (p > 0) "${job.choice.label} · ${p.toInt()}%$eta" else "${job.choice.label} · Starting…"
                bar.visibility = View.VISIBLE
                bar.isIndeterminate = p <= 0
                bar.progress = (p * 10).toInt()
                action.setImageResource(R.drawable.ic_close)
                action.contentDescription = "Cancel"
                action.setOnClickListener { DownloadService.cancel(job.id) }
            }
            Downloads.Status.DONE -> {
                status.text = "${job.choice.label} · Saved to Download folder"
                bar.visibility = View.GONE
                action.setImageResource(R.drawable.ic_play)
                action.contentDescription = "Play"
                action.setOnClickListener { play(job) }
            }
            Downloads.Status.FAILED, Downloads.Status.CANCELED -> {
                status.text = if (job.status == Downloads.Status.CANCELED) "Canceled" else (job.error ?: "Download failed")
                bar.visibility = View.GONE
                action.setImageResource(R.drawable.ic_refresh)
                action.contentDescription = "Try again"
                action.setOnClickListener {
                    Downloads.jobs.remove(job)
                    DownloadService.enqueue(this, job.url, job.title, job.choice)
                }
            }
        }
    }

    private fun play(job: Downloads.Job) {
        val uri = job.result ?: return
        startActivity(
            Intent(this, PlayerActivity::class.java)
                .putStringArrayListExtra(PlayerActivity.EXTRA_URIS, arrayListOf(uri.toString()))
                .putStringArrayListExtra(PlayerActivity.EXTRA_NAMES, arrayListOf(job.title))
                .putStringArrayListExtra(PlayerActivity.EXTRA_PATHS, arrayListOf(job.resultPath ?: ""))
                .putExtra(PlayerActivity.EXTRA_INDEX, 0)
        )
    }

    private fun hideKeyboard() {
        getSystemService(InputMethodManager::class.java)?.hideSoftInputFromWindow(urlInput.windowToken, 0)
        urlInput.clearFocus()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
