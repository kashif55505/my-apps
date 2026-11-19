package com.reel.player

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import java.io.File
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.roundToInt

/** Live list of downloads, shared by the service and the downloader screen. */
object Downloads {
    enum class Status { QUEUED, RUNNING, DONE, FAILED, CANCELED }

    class Job(val id: String, val url: String, val title: String, val choice: Choice) {
        @Volatile var status = Status.QUEUED
        @Volatile var progress = 0f          // 0..100
        @Volatile var eta = 0L               // seconds
        @Volatile var error: String? = null
        @Volatile var result: Uri? = null
        @Volatile var resultPath: String? = null
        @Volatile var lastUiUpdate = 0L
    }

    val jobs = CopyOnWriteArrayList<Job>()
    private val listeners = CopyOnWriteArrayList<() -> Unit>()
    private val main = Handler(Looper.getMainLooper())

    fun addListener(l: () -> Unit) { listeners += l }
    fun removeListener(l: () -> Unit) { listeners -= l }
    fun changed() { main.post { listeners.forEach { it() } } }
    fun find(id: String?) = jobs.firstOrNull { it.id == id }
    fun busy() = jobs.any { it.status == Status.QUEUED || it.status == Status.RUNNING }
}

/**
 * Runs downloads as a foreground service so they keep going when the user leaves the app.
 * Finished files are saved to the phone's Download folder,
 * so they show up in the Videos list right away.
 */
class DownloadService : Service() {

    companion object {
        /** Downloads go straight into the phone's normal Download folder. */
        const val FOLDER_NAME = "Download"
        private const val CHANNEL = "downloads"
        private const val CHANNEL_DONE = "downloads_done"
        private const val NOTIF_ID = 4201
        private const val EXTRA_ID = "job"

        fun enqueue(ctx: Context, url: String, title: String, choice: Choice): Downloads.Job {
            val job = Downloads.Job(UUID.randomUUID().toString(), url, title, choice)
            Downloads.jobs.add(0, job)
            Downloads.changed()
            ContextCompat.startForegroundService(ctx, Intent(ctx, DownloadService::class.java).putExtra(EXTRA_ID, job.id))
            return job
        }

        fun cancel(id: String) {
            val job = Downloads.find(id) ?: return
            if (job.status == Downloads.Status.RUNNING || job.status == Downloads.Status.QUEUED) {
                job.status = Downloads.Status.CANCELED
                try { YoutubeDL.getInstance().destroyProcessById(id) } catch (_: Exception) {}
                Downloads.changed()
            }
        }
    }

    private val pool = Executors.newFixedThreadPool(2)
    private val active = AtomicInteger(0)
    private val main = Handler(Looper.getMainLooper())
    private var lastNotif = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Downloads in progress", NotificationManager.IMPORTANCE_LOW))
            nm.createNotificationChannel(NotificationChannel(CHANNEL_DONE, "Finished downloads", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startInForeground()
        val job = Downloads.find(intent?.getStringExtra(EXTRA_ID))
        if (job != null && job.status == Downloads.Status.QUEUED) {
            active.incrementAndGet()
            pool.execute {
                try { run(job) } finally {
                    if (active.decrementAndGet() == 0) main.post { stopIfIdle() }
                }
            }
        } else if (active.get() == 0) {
            stopIfIdle()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        pool.shutdownNow()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ download

    private fun run(job: Downloads.Job) {
        if (job.status == Downloads.Status.CANCELED) return
        job.status = Downloads.Status.RUNNING
        Downloads.changed()
        updateNotification(force = true)
        val dir = File(getExternalFilesDir(null) ?: filesDir, "dl/${job.id}")
        dir.mkdirs()
        try {
            Engine.init(applicationContext)
            val request = YoutubeDLRequest(job.url)
                .addOption("-f", job.choice.selector)
                .addOption("-o", File(dir, "%(title).80B.%(ext)s").absolutePath)
                .addOption("--no-playlist")
                .addOption("--no-mtime")
            Engine.addCookies(applicationContext, request, job.url)
            when (job.choice.kind) {
                Choice.Kind.VIDEO -> request.addOption("--merge-output-format", "mp4/mkv")
                Choice.Kind.MP3 -> {
                    request.addOption("-x")
                    request.addOption("--audio-format", "mp3")
                    request.addOption("--audio-quality", "0")
                }
                Choice.Kind.M4A -> {}
            }

            YoutubeDL.getInstance().execute(request, job.id) { progress, eta, _ ->
                if (progress >= 0) job.progress = progress
                job.eta = eta
                val now = SystemClock.uptimeMillis()
                if (now - job.lastUiUpdate > 400) {
                    job.lastUiUpdate = now
                    Downloads.changed()
                    updateNotification(force = false)
                }
            }
            if (job.status == Downloads.Status.CANCELED) return

            val file = dir.listFiles()
                ?.filter { it.isFile && !it.name.endsWith(".part") && !it.name.endsWith(".ytdl") && !it.name.contains(".temp.") }
                ?.maxByOrNull { it.length() }
                ?: throw IllegalStateException("The download finished but no file was saved")

            job.result = save(file, job)
            job.progress = 100f
            job.status = Downloads.Status.DONE
            notifyFinished(job)
        } catch (e: YoutubeDL.CanceledException) {
            job.status = Downloads.Status.CANCELED
        } catch (e: Exception) {
            if (job.status != Downloads.Status.CANCELED) {
                job.status = Downloads.Status.FAILED
                job.error = Engine.humanize(e)
                notifyFinished(job)
            }
        } finally {
            dir.deleteRecursively()
            Downloads.changed()
            updateNotification(force = true)
        }
    }

    /** Copies the finished file into shared storage so the gallery and the Videos list see it. */
    private fun save(file: File, job: Downloads.Job): Uri {
        val audio = job.choice.kind != Choice.Kind.VIDEO
        val mime = when (file.extension.lowercase()) {
            "mp4" -> "video/mp4"
            "mkv" -> "video/x-matroska"
            "webm" -> if (audio) "audio/webm" else "video/webm"
            "m4a" -> "audio/mp4"
            "mp3" -> "audio/mpeg"
            "opus", "ogg" -> "audio/ogg"
            else -> if (audio) "audio/mp4" else "video/mp4"
        }
        val base = Environment.DIRECTORY_DOWNLOADS

        if (Build.VERSION.SDK_INT >= 29) {
            val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
                put(MediaStore.MediaColumns.MIME_TYPE, mime)
                put(MediaStore.MediaColumns.RELATIVE_PATH, base)
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val uri = contentResolver.insert(collection, values)
                ?: throw IllegalStateException("Couldn't save the file to your phone's storage")
            try {
                contentResolver.openOutputStream(uri)?.use { out -> file.inputStream().use { it.copyTo(out) } }
                    ?: throw IllegalStateException("Couldn't write the file")
                values.clear()
                values.put(MediaStore.MediaColumns.IS_PENDING, 0)
                contentResolver.update(uri, values, null, null)
                job.resultPath = filePathOf(uri)
            } catch (e: Exception) {
                contentResolver.delete(uri, null, null)
                throw e
            }
            return uri
        }

        val outDir = Environment.getExternalStoragePublicDirectory(base)
        outDir.mkdirs()
        var target = File(outDir, file.name)
        var n = 1
        while (target.exists()) {
            target = File(outDir, "${file.nameWithoutExtension} ($n).${file.extension}")
            n++
        }
        file.copyTo(target)
        job.resultPath = target.absolutePath
        MediaScannerConnection.scanFile(this, arrayOf(target.absolutePath), arrayOf(mime), null)
        return Uri.fromFile(target)
    }

    private fun filePathOf(uri: Uri): String? = try {
        contentResolver.query(uri, arrayOf(MediaStore.MediaColumns.DATA), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
    } catch (_: Exception) { null }

    // ------------------------------------------------------------------ notifications

    private fun openDownloader(): PendingIntent = PendingIntent.getActivity(
        this, 0, Intent(this, DownloaderActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
    )

    private fun progressNotification(): Notification {
        val running = Downloads.jobs.filter { it.status == Downloads.Status.RUNNING }
        val queued = Downloads.jobs.count { it.status == Downloads.Status.QUEUED }
        val b = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_download)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setContentIntent(openDownloader())
        if (running.isEmpty()) {
            b.setContentTitle("Preparing download…").setProgress(0, 0, true)
        } else {
            val first = running.first()
            val pct = running.map { it.progress }.average().roundToInt()
            val title = if (running.size == 1) first.title else "Downloading ${running.size} videos"
            val eta = if (running.size == 1 && first.eta > 0) " · ${fmtTime(first.eta * 1000)} left" else ""
            val more = if (queued > 0) " · $queued waiting" else ""
            b.setContentTitle(title)
                .setContentText("$pct%$eta$more")
                .setProgress(100, pct, pct <= 0)
        }
        return b.build()
    }

    private fun startInForeground() {
        val type = if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0
        ServiceCompat.startForeground(this, NOTIF_ID, progressNotification(), type)
    }

    private fun updateNotification(force: Boolean) {
        val now = SystemClock.uptimeMillis()
        if (!force && now - lastNotif < 1000) return
        lastNotif = now
        if (!Downloads.busy()) return
        try {
            getSystemService(NotificationManager::class.java).notify(NOTIF_ID, progressNotification())
        } catch (_: Exception) {}
    }

    private fun notifyFinished(job: Downloads.Job) {
        val ok = job.status == Downloads.Status.DONE
        val intent = if (ok && job.result != null) {
            Intent(this, PlayerActivity::class.java)
                .putStringArrayListExtra(PlayerActivity.EXTRA_URIS, arrayListOf(job.result.toString()))
                .putStringArrayListExtra(PlayerActivity.EXTRA_NAMES, arrayListOf(job.title))
                .putStringArrayListExtra(PlayerActivity.EXTRA_PATHS, arrayListOf(job.resultPath ?: ""))
                .putExtra(PlayerActivity.EXTRA_INDEX, 0)
        } else Intent(this, DownloaderActivity::class.java)
        val pi = PendingIntent.getActivity(
            this, job.id.hashCode(), intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val n = NotificationCompat.Builder(this, CHANNEL_DONE)
            .setSmallIcon(R.drawable.ic_download)
            .setContentTitle(if (ok) "Downloaded" else "Download failed")
            .setContentText(if (ok) job.title else (job.error ?: job.title))
            .setStyle(NotificationCompat.BigTextStyle().bigText(if (ok) "${job.title}\nTap to play" else "${job.title}\n${job.error ?: ""}"))
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()
        try {
            getSystemService(NotificationManager::class.java).notify(job.id.hashCode(), n)
        } catch (_: Exception) {}
    }

    private fun stopIfIdle() {
        if (Downloads.busy() || active.get() > 0) return
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }
}
