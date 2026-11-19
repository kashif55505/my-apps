package com.reel.player

import android.app.PictureInPictureParams
import android.content.ComponentName
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Color
import android.media.AudioManager
import android.media.audiofx.LoudnessEnhancer
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.OpenableColumns
import android.provider.Settings
import android.util.Rational
import android.util.TypedValue
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Timeline
import androidx.media3.common.Tracks
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.CaptionStyleCompat
import androidx.media3.ui.PlayerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import java.io.File
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

@androidx.annotation.OptIn(UnstableApi::class)
class PlayerActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_URIS = "uris"
        const val EXTRA_NAMES = "names"
        const val EXTRA_PATHS = "paths"
        const val EXTRA_INDEX = "index"
        /** Opened as full screen for something another screen is playing; closing it must not stop that. */
        const val EXTRA_ATTACH = "attach"
        private val SPEEDS = floatArrayOf(0.25f, 0.5f, 0.75f, 1f, 1.25f, 1.5f, 1.75f, 2f, 2.5f, 3f, 4f)
        private val ASPECTS = intArrayOf(
            AspectRatioFrameLayout.RESIZE_MODE_FIT,
            AspectRatioFrameLayout.RESIZE_MODE_ZOOM,
            AspectRatioFrameLayout.RESIZE_MODE_FILL
        )
        private val ASPECT_NAMES = arrayOf("Fit", "Crop", "Stretch")
        private val SUB_SIZES = floatArrayOf(16f, 20f, 26f, 32f)
        private val SUB_SIZE_NAMES = arrayOf("Small", "Medium", "Large", "Extra large")
        private const val SKIP_MS = 10_000L
    }

    private lateinit var player: ExoPlayer
    private lateinit var playerView: PlayerView
    private lateinit var root: View
    private lateinit var touch: View
    private lateinit var topBar: LinearLayout
    private lateinit var bottomBar: LinearLayout
    private lateinit var tvTitle: TextView
    private lateinit var tvPos: TextView
    private lateinit var tvDur: TextView
    private lateinit var seek: SeekBar
    private lateinit var btnPlay: ImageButton
    private lateinit var btnAspect: TextView
    private lateinit var btnUnlock: ImageButton
    private lateinit var hud: View
    private lateinit var hudIcon: ImageView
    private lateinit var hudText: TextView
    private lateinit var rippleL: TextView
    private lateinit var rippleR: TextView
    private lateinit var speedBadge: TextView
    private lateinit var resumeChip: TextView
    private lateinit var btnBg: ImageButton
    private var bgPlay = false
    private var controllerFuture: ListenableFuture<MediaController>? = null
    private var tick = 0

    private val handler = Handler(Looper.getMainLooper())
    private var uris = ArrayList<String>()
    private var names = ArrayList<String>()
    private var paths = ArrayList<String>()

    private var controlsVisible = true
    private var locked = false
    private var speed = 1f
    private var aspectIdx = 0
    private var userRotated = false
    private var seekDragging = false
    private var subSizeIdx = 1
    private var lastId: String? = null
    private var lastPos = 0L

    private lateinit var audio: AudioManager
    private var maxVol = 15
    private var volLevel = 0f            // 0 .. 2*maxVol (above maxVol = boost)
    private var enhancer: LoudnessEnhancer? = null
    private var enhancerSession = C.AUDIO_SESSION_ID_UNSET
    private var brightness = -1f

    // gesture state
    private enum class Mode { NONE, SEEK, BRIGHT, VOL, LONG, ZOOM, IGNORE }
    private var mode = Mode.NONE
    private var downX = 0f
    private var downY = 0f
    private var startPos = 0L
    private var startBright = 0.5f
    private var startVol = 0f
    private var seekTarget = -1L
    private var touchSlop = 24
    private var lastTapTime = 0L
    private var lastTapX = 0f
    private var streakSide = 0
    private var streakCount = 0
    private var pendingSingleTap: Runnable? = null
    private var lastLiveSeek = 0L
    private var attachOnly = false
    private val bg = java.util.concurrent.Executors.newSingleThreadExecutor()
    private var fallbackDurationMs = 0L
    private var fixingPath: String? = null
    private var pendingSeek = -1L   // where the user asked to go while the file was being prepared
    private lateinit var scaleDetector: ScaleGestureDetector
    private var zoom = 1f
    private var panX = 0f
    private var panY = 0f
    private var lastFocusX = 0f
    private var lastFocusY = 0f

    private val askNotify = registerForActivityResult(ActivityResultContracts.RequestPermission()) { hideSystemUi() }

    private val subPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) attachSubtitle(uri)
        hideSystemUi()
    }

    // ---------------------------------------------------------------- lifecycle

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_player)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Build.VERSION.SDK_INT >= 28) {
            window.attributes = window.attributes.apply {
                // Use the whole screen, including the area beside the camera notch.
                layoutInDisplayCutoutMode = if (Build.VERSION.SDK_INT >= 30)
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                else WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
        hideSystemUi()
        bindViews()
        audio = getSystemService(AUDIO_SERVICE) as AudioManager
        maxVol = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
        volLevel = audio.getStreamVolume(AudioManager.STREAM_MUSIC).toFloat()
        touchSlop = ViewConfiguration.get(this).scaledTouchSlop * 2
        subSizeIdx = Prefs.getInt(this, "subSize", 1).coerceIn(0, SUB_SIZES.size - 1)
        bgPlay = Prefs.getInt(this, "bgPlay", 0) == 1

        // The player lives in PlayerHolder so it can keep running in PlaybackService
        // after this screen goes to the background.
        player = PlayerHolder.get(this)
        player.addListener(listener)
        playerView.player = player
        styleSubtitles()

        player.repeatMode = Prefs.getInt(this, "repeat", Player.REPEAT_MODE_OFF)

        // Notification or "Now playing" tapped while a YouTube video plays: go back to its own page.
        if (!intent.hasExtra(EXTRA_URIS) && intent.action != Intent.ACTION_VIEW &&
            !intent.getBooleanExtra(EXTRA_ATTACH, false) && OnlinePlayer.isOnline()
        ) {
            attachOnly = true
            val id = player.currentMediaItem?.mediaId ?: ""
            startActivity(
                Intent(this, YtVideoActivity::class.java)
                    .putExtra(YtVideoActivity.EXTRA_URL, id)
                    .putExtra(YtVideoActivity.EXTRA_TITLE, player.currentMediaItem?.mediaMetadata?.title?.toString())
                    .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            )
            finish()
            return
        }

        if (!loadFromIntent(intent)) { finish(); return }

        // Start the service directly too, so it outlives this screen when background play is on.
        try { startService(Intent(this, PlaybackService::class.java)) } catch (_: Exception) {}
        // Connecting a controller starts PlaybackService, which shows the media notification.
        controllerFuture = MediaController.Builder(
            this, SessionToken(this, ComponentName(this, PlaybackService::class.java))
        ).buildAsync()

        setupControls()
        updateBgButton()
        updateRepeatButton()
        setupGestures()
        applyInsets()
        handler.post(ticker)
        showControls(false)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        loadFromIntent(intent)
    }

    /**
     * Starts the playlist from the intent. An intent with nothing in it (for example a tap on the
     * media notification) reattaches to whatever is already playing. Returns false if there is nothing to play.
     */
    private fun loadFromIntent(i: Intent): Boolean {
        val data = i.data
        val hasList = i.hasExtra(EXTRA_URIS)
        attachOnly = i.getBooleanExtra(EXTRA_ATTACH, false)
        if (!hasList && !(i.action == Intent.ACTION_VIEW && data != null)) {
            if (player.mediaItemCount == 0) return false
            uris = ArrayList(PlayerHolder.uris); names = ArrayList(PlayerHolder.names); paths = ArrayList(PlayerHolder.paths)
            lastId = player.currentMediaItem?.mediaId
            tvTitle.text = player.currentMediaItem?.mediaMetadata?.title ?: ""
            btnPlay.setImageResource(if (player.isPlaying) R.drawable.ic_pause else R.drawable.ic_play)
            return true
        }
        if (player.mediaItemCount > 0) savePosition()
        readIntent(i)
        if (uris.isEmpty()) return false
        PlayerHolder.uris = ArrayList(uris); PlayerHolder.names = ArrayList(names); PlayerHolder.paths = ArrayList(paths)
        lastId = null
        speed = 1f
        player.setPlaybackSpeed(1f)
        val start = i.getIntExtra(EXTRA_INDEX, 0).coerceIn(0, uris.size - 1)
        val saved = Prefs.getPos(this, uris[start])
        player.setMediaItems(uris.indices.map { buildItem(it, null) }, start, if (saved > 5000) saved else 0L)
        player.prepare()
        player.playWhenReady = true
        if (saved > 5000) showResumeChip(saved)
        return true
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    override fun onStart() {
        super.onStart()
        if (::player.isInitialized) setVideoEnabled(true)
    }

    override fun onPause() {
        super.onPause()
        if (::player.isInitialized) savePosition()
    }

    override fun onStop() {
        super.onStop()
        if (!::player.isInitialized || isInPictureInPictureMode) return
        if (attachOnly && isFinishing) return
        if (keepPlaying()) {
            // Keep the sound going; stop decoding video nobody can see to save battery.
            setVideoEnabled(false)
        } else {
            player.pause()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacksAndMessages(null)
        bg.shutdown()
        try { enhancer?.release() } catch (_: Exception) {}
        enhancer = null
        if (::player.isInitialized) {
            player.removeListener(listener)
            playerView.player = null
            if (isFinishing && !keepPlaying() && !attachOnly) {
                savePosition()
                player.stop()
                player.clearMediaItems()
                setVideoEnabled(true)
                try { stopService(Intent(this, PlaybackService::class.java)) } catch (_: Exception) {}
            }
        }
        controllerFuture?.let { MediaController.releaseFuture(it) }
    }

    /** True when background play is on and something is actually playing: leaving the screen keeps the sound. */
    private fun keepPlaying(): Boolean =
        bgPlay && player.playWhenReady && player.mediaItemCount > 0 && player.playbackState != Player.STATE_ENDED

    private fun setVideoEnabled(on: Boolean) {
        if (!on && !player.isCurrentMediaItemSeekable) return
        val disabled = player.trackSelectionParameters.disabledTrackTypes.contains(C.TRACK_TYPE_VIDEO)
        if (disabled == !on) return
        player.trackSelectionParameters = player.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_VIDEO, !on).build()
    }

    private fun updateBgButton() {
        btnBg.imageTintList = android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(this, if (bgPlay) R.color.accent else android.R.color.white)
        )
    }

    override fun onPictureInPictureModeChanged(isInPictureInPictureMode: Boolean, newConfig: Configuration) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        if (isInPictureInPictureMode) {
            hideControls()
            resumeChip.visibility = View.GONE
        } else {
            showControls(false)
        }
    }

    // ---------------------------------------------------------------- setup

    private fun bindViews() {
        root = findViewById(R.id.root)
        playerView = findViewById(R.id.playerView)
        touch = findViewById(R.id.touch)
        topBar = findViewById(R.id.topBar)
        bottomBar = findViewById(R.id.bottomBar)
        tvTitle = findViewById(R.id.tvTitle)
        tvPos = findViewById(R.id.tvPos)
        tvDur = findViewById(R.id.tvDur)
        seek = findViewById(R.id.seek)
        btnPlay = findViewById(R.id.btnPlay)
        btnAspect = findViewById(R.id.btnAspect)
        btnUnlock = findViewById(R.id.btnUnlock)
        hud = findViewById(R.id.hud)
        hudIcon = findViewById(R.id.hudIcon)
        hudText = findViewById(R.id.hudText)
        rippleL = findViewById(R.id.rippleL)
        rippleR = findViewById(R.id.rippleR)
        speedBadge = findViewById(R.id.speedBadge)
        resumeChip = findViewById(R.id.resumeChip)
        btnBg = findViewById(R.id.btnBg)
    }

    private fun readIntent(intent: Intent) {
        val data = intent.data
        if (intent.action == Intent.ACTION_VIEW && data != null) {
            uris = arrayListOf(data.toString())
            names = arrayListOf(displayName(data) ?: data.lastPathSegment ?: "Video")
            paths = arrayListOf(if (data.scheme == "file") data.path ?: "" else "")
        } else {
            uris = intent.getStringArrayListExtra(EXTRA_URIS) ?: ArrayList()
            names = intent.getStringArrayListExtra(EXTRA_NAMES) ?: ArrayList()
            paths = intent.getStringArrayListExtra(EXTRA_PATHS) ?: ArrayList()
        }
    }

    private fun displayName(uri: Uri): String? = try {
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
    } catch (_: Exception) { null }

    private fun hideSystemUi() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun applyInsets() {
        val base = dp(8)
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val c = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            topBar.setPadding(base + c.left, base + c.top, base + c.right, topBar.paddingBottom)
            bottomBar.setPadding(base + c.left, bottomBar.paddingTop, base + c.right, base + c.bottom)
            insets
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).roundToInt()

    private fun styleSubtitles() {
        playerView.subtitleView?.apply {
            setStyle(
                CaptionStyleCompat(
                    Color.WHITE, 0x66000000, Color.TRANSPARENT,
                    CaptionStyleCompat.EDGE_TYPE_DROP_SHADOW, Color.BLACK, null
                )
            )
            setFixedTextSize(TypedValue.COMPLEX_UNIT_SP, SUB_SIZES[subSizeIdx])
        }
    }

    // ---------------------------------------------------------------- media items & subtitles

    private fun buildItem(i: Int, externalSub: Uri?): MediaItem {
        val subs = ArrayList<MediaItem.SubtitleConfiguration>()
        if (externalSub != null) {
            subs += subConfig(externalSub, subMime(displayName(externalSub) ?: externalSub.toString()))
        } else {
            findSidecar(paths.getOrNull(i))?.let { subs += subConfig(Uri.fromFile(it), subMime(it.name)) }
        }
        return MediaItem.Builder()
            .setUri(playableUri(i))
            .setMediaId(uris[i])
            .setMediaMetadata(MediaMetadata.Builder().setTitle(names.getOrNull(i) ?: "").build())
            .setSubtitleConfigurations(subs)
            .build()
    }

    /**
     * Open the file by its real path when we can. Many phones don't report the file size through
     * the media-library link, and without a size the player can't find the duration or jump around.
     * The library link is still used as the ID for "resume where I left off".
     */
    private fun playableUri(i: Int): Uri {
        val path = paths.getOrNull(i)
        if (!path.isNullOrEmpty()) {
            SeekFixer.fixedPathFor(path)?.let { return Uri.fromFile(File(it)) }
            try {
                val f = File(path)
                if (f.isFile && f.canRead()) return Uri.fromFile(f)
            } catch (_: Exception) {}
        }
        return Uri.parse(uris[i])
    }

    private fun subConfig(uri: Uri, mime: String) =
        MediaItem.SubtitleConfiguration.Builder(uri)
            .setMimeType(mime)
            .setLabel("External")
            .setSelectionFlags(C.SELECTION_FLAG_DEFAULT)
            .build()

    private fun subMime(name: String): String {
        val n = name.lowercase(Locale.ROOT)
        return when {
            n.endsWith(".vtt") -> MimeTypes.TEXT_VTT
            n.endsWith(".ass") || n.endsWith(".ssa") -> MimeTypes.TEXT_SSA
            else -> MimeTypes.APPLICATION_SUBRIP
        }
    }

    /** Looks for "movie.srt" next to "movie.mp4", like MX Player does. */
    private fun findSidecar(path: String?): File? {
        if (path.isNullOrEmpty()) return null
        return try {
            val f = File(path)
            val dir = f.parentFile ?: return null
            val base = f.nameWithoutExtension
            listOf("srt", "vtt", "ass", "ssa", "SRT", "VTT").map { File(dir, "$base.$it") }
                .firstOrNull { it.exists() && it.canRead() }
        } catch (_: Exception) { null }
    }

    private fun attachSubtitle(uri: Uri) {
        val i = player.currentMediaItemIndex
        val pos = player.currentPosition
        val wasPlaying = player.playWhenReady
        player.replaceMediaItem(i, buildItem(i, uri))
        player.seekTo(i, pos)
        player.playWhenReady = wasPlaying
        player.trackSelectionParameters = player.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false).build()
        toast("Subtitle loaded")
    }

    // ---------------------------------------------------------------- player events

    private val listener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            btnPlay.setImageResource(if (isPlaying) R.drawable.ic_pause else R.drawable.ic_play)
            if (isPlaying) showControls(false) else if (!locked && !player.playWhenReady) showControls(true)
        }

        override fun onTimelineChanged(timeline: Timeline, reason: Int) {
            handler.post { checkSeekable() }
        }

        override fun onRepeatModeChanged(repeatMode: Int) = updateRepeatButton()

        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            fixingPathForOtherItem()
            handler.post { checkSeekable() }
            if (mediaItem == null && player.mediaItemCount == 0) {
                // Closed from the notification's × button.
                lastId = null
                if (!isFinishing) finish()
                return
            }
            tvTitle.text = mediaItem?.mediaMetadata?.title ?: ""
            val prev = lastId
            if (prev != null && prev != mediaItem?.mediaId) {
                // The previous video either finished (AUTO) or the user skipped away from it.
                Prefs.setPos(this@PlayerActivity, prev, if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) 0L else lastPos)
            }
            lastId = mediaItem?.mediaId
            lastPos = 0L
            fallbackDurationMs = 0L
            pendingSeek = -1L
            if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO || reason == Player.MEDIA_ITEM_TRANSITION_REASON_SEEK) {
                userRotated = false
                val id = mediaItem?.mediaId ?: return
                val p = Prefs.getPos(this@PlayerActivity, id)
                if (p > 5000) { player.seekTo(p); showResumeChip(p) }
            }
        }

        override fun onPlaybackStateChanged(state: Int) {
            if (state == Player.STATE_READY) checkSeekable()
            if (state == Player.STATE_ENDED) {
                player.currentMediaItem?.mediaId?.let { Prefs.setPos(this@PlayerActivity, it, 0L) }
                lastId = null
                if (!isFinishing) finish()
            }
        }

        override fun onVideoSizeChanged(videoSize: VideoSize) {
            if (userRotated || videoSize.width <= 0 || videoSize.height <= 0) return
            val w = videoSize.width * videoSize.pixelWidthHeightRatio
            requestedOrientation = if (w >= videoSize.height) ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            else ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
        }

        override fun onPlayerError(error: PlaybackException) {
            // The phone can't open or decode this file: quietly make a playable copy with FFmpeg.
            if (!convertCurrent() && !attachOnly) toast("Can't play this file")
        }
    }

    private val ticker = object : Runnable {
        override fun run() {
            updateProgress()
            handler.postDelayed(this, 500)
        }
    }

    private fun updateProgress() {
        val d = player.duration
        val p = player.currentPosition
        if (!seekDragging) {
            seek.progress = if (d > 0) (p * 1000 / d).toInt() else 0
            tvPos.text = fmtTime(p)
        }
        seek.secondaryProgress = if (d > 0) (player.bufferedPosition * 1000 / d).toInt() else 0
        val shownD = if (d > 0) d else fallbackDurationMs
        tvDur.text = if (shownD > 0) fmtTime(shownD) else "--:--"
        if (d <= 0 && shownD > 0 && !seekDragging) seek.progress = (p * 1000 / shownD).toInt().coerceIn(0, 1000)
        if (player.currentMediaItem?.mediaId == lastId) lastPos = p
        if (++tick % 20 == 0 && player.isPlaying) savePosition()
    }

    private fun savePosition() {
        val id = player.currentMediaItem?.mediaId ?: return
        val d = player.duration
        val p = player.currentPosition
        Prefs.setPos(this, id, if (d > 0 && p > d - 8000) 0L else p)
    }

    // ---------------------------------------------------------------- controls

    private fun setupControls() {
        findViewById<View>(R.id.btnBack).setOnClickListener { finish() }
        btnPlay.setOnClickListener { togglePlay(); showControls(false) }
        findViewById<View>(R.id.btnPrev).setOnClickListener {
            when {
                player.currentPosition > 3000 && player.isCurrentMediaItemSeekable -> player.seekTo(0)
                player.hasPreviousMediaItem() -> player.seekToPreviousMediaItem()
                else -> player.seekTo(0)
            }
            showControls(false)
        }
        findViewById<View>(R.id.btnNext).setOnClickListener {
            if (player.hasNextMediaItem()) player.seekToNextMediaItem() else toast("This is the last video in the folder")
            showControls(false)
        }
        findViewById<View>(R.id.btnLock).setOnClickListener { setLocked(true) }
        findViewById<View>(R.id.btnRepeat).setOnClickListener {
            // Off → repeat all → repeat one → off
            player.repeatMode = when (player.repeatMode) {
                Player.REPEAT_MODE_OFF -> Player.REPEAT_MODE_ALL
                Player.REPEAT_MODE_ALL -> Player.REPEAT_MODE_ONE
                else -> Player.REPEAT_MODE_OFF
            }
            Prefs.setInt(this, "repeat", player.repeatMode)
            showHud(if (player.repeatMode == Player.REPEAT_MODE_ONE) R.drawable.ic_repeat_one else R.drawable.ic_repeat,
                when (player.repeatMode) {
                    Player.REPEAT_MODE_ALL -> "Repeat all"
                    Player.REPEAT_MODE_ONE -> "Repeat one"
                    else -> "Repeat off"
                })
            showControls(false)
        }
        btnUnlock.setOnClickListener { setLocked(false) }
        btnAspect.setOnClickListener {
            aspectIdx = (aspectIdx + 1) % ASPECTS.size
            playerView.resizeMode = ASPECTS[aspectIdx]
            btnAspect.text = ASPECT_NAMES[aspectIdx]
            resetZoom()
            showHud(0, ASPECT_NAMES[aspectIdx])
            showControls(false)
        }
        findViewById<View>(R.id.btnRotate).setOnClickListener {
            userRotated = true
            requestedOrientation =
                if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE)
                    ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
                else ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            showControls(false)
        }
        findViewById<View>(R.id.btnSpeed).setOnClickListener { speedDialog() }
        findViewById<View>(R.id.btnAudio).setOnClickListener { audioDialog() }
        findViewById<View>(R.id.btnSubs).setOnClickListener { subtitleDialog() }
        findViewById<View>(R.id.btnPip).setOnClickListener { enterPip() }
        btnBg.setOnClickListener {
            bgPlay = !bgPlay
            Prefs.setInt(this, "bgPlay", if (bgPlay) 1 else 0)
            updateBgButton()
            showHud(R.drawable.ic_headphones, if (bgPlay) "Background play ON" else "Background play OFF")
            if (bgPlay) {
                toast("Back, Home or screen lock: the sound keeps playing. Control it from the notification.")
                if (Build.VERSION.SDK_INT >= 33 &&
                    ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                ) askNotify.launch(android.Manifest.permission.POST_NOTIFICATIONS)
            }
            showControls(false)
        }

        seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar, progress: Int, fromUser: Boolean) {
                if (!fromUser) return
                if (!canSeek()) { updateProgress(); return }
                val d = effectiveDuration()
                if (d > 0) {
                    val t = d * progress / 1000
                    tvPos.text = fmtTime(t)
                    seekOrQueue(t)
                }
            }
            override fun onStartTrackingTouch(sb: SeekBar) {
                seekDragging = true
                showControls(true)
            }
            override fun onStopTrackingTouch(sb: SeekBar) {
                seekDragging = false
                showControls(false)
            }
        })
    }

    private fun togglePlay() {
        when {
            player.playbackState == Player.STATE_ENDED -> { player.seekTo(0); player.play() }
            player.isPlaying -> player.pause()
            else -> player.play()
        }
    }

    private val hideControlsRunnable = Runnable { hideControls() }

    private fun showControls(stay: Boolean) {
        if (locked || isInPictureInPictureMode) return
        controlsVisible = true
        topBar.visibility = View.VISIBLE
        bottomBar.visibility = View.VISIBLE
        topBar.animate().alpha(1f).setDuration(150).start()
        bottomBar.animate().alpha(1f).setDuration(150).start()
        handler.removeCallbacks(hideControlsRunnable)
        if (!stay && player.isPlaying) handler.postDelayed(hideControlsRunnable, 3500)
    }

    private fun hideControls() {
        controlsVisible = false
        handler.removeCallbacks(hideControlsRunnable)
        topBar.animate().alpha(0f).setDuration(150).withEndAction { if (!controlsVisible) topBar.visibility = View.GONE }.start()
        bottomBar.animate().alpha(0f).setDuration(150).withEndAction { if (!controlsVisible) bottomBar.visibility = View.GONE }.start()
    }

    private val fadeUnlock = Runnable { btnUnlock.animate().alpha(0f).setDuration(300).start() }

    private fun setLocked(on: Boolean) {
        locked = on
        if (on) {
            hideControls()
            btnUnlock.visibility = View.VISIBLE
            flashUnlock()
            toast("Screen locked")
        } else {
            handler.removeCallbacks(fadeUnlock)
            btnUnlock.visibility = View.GONE
            showControls(false)
        }
    }

    private fun flashUnlock() {
        btnUnlock.animate().cancel()
        btnUnlock.alpha = 1f
        handler.removeCallbacks(fadeUnlock)
        handler.postDelayed(fadeUnlock, 2500)
    }

    private fun showResumeChip(pos: Long) {
        resumeChip.text = "Resumed from ${fmtTime(pos)}   ·   START OVER"
        resumeChip.visibility = View.VISIBLE
        resumeChip.setOnClickListener {
            player.seekTo(0)
            resumeChip.visibility = View.GONE
        }
        handler.removeCallbacks(hideResume)
        handler.postDelayed(hideResume, 6000)
    }

    private val hideResume = Runnable { resumeChip.visibility = View.GONE }

    // ---------------------------------------------------------------- HUD

    private val hideHudRunnable = Runnable { hud.visibility = View.GONE }

    private fun showHud(icon: Int, text: String) {
        if (icon != 0) {
            hudIcon.setImageResource(icon)
            hudIcon.visibility = View.VISIBLE
        } else hudIcon.visibility = View.GONE
        hudText.text = text
        hud.visibility = View.VISIBLE
        handler.removeCallbacks(hideHudRunnable)
        handler.postDelayed(hideHudRunnable, 800)
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    // ---------------------------------------------------------------- gestures

    private val longPressRunnable = Runnable {
        if (mode == Mode.NONE && !locked && player.isPlaying) {
            mode = Mode.LONG
            player.setPlaybackSpeed(2f)
            speedBadge.visibility = View.VISIBLE
        }
    }

    private fun setupGestures() {
        scaleDetector = ScaleGestureDetector(this, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScaleBegin(d: ScaleGestureDetector): Boolean {
                lastFocusX = d.focusX
                lastFocusY = d.focusY
                return !locked
            }
            override fun onScale(d: ScaleGestureDetector): Boolean {
                zoom = (zoom * d.scaleFactor).coerceIn(1f, 5f)
                panX += d.focusX - lastFocusX
                panY += d.focusY - lastFocusY
                lastFocusX = d.focusX
                lastFocusY = d.focusY
                applyZoom()
                showHud(0, "Zoom ${(zoom * 100).roundToInt()}%")
                return true
            }
        })
        touch.setOnTouchListener { v, ev ->
            scaleDetector.onTouchEvent(ev)
            when (ev.actionMasked) {
                MotionEvent.ACTION_POINTER_DOWN -> {
                    // Second finger: switch to pinch-zoom and cancel any other gesture.
                    handler.removeCallbacks(longPressRunnable)
                    pendingSingleTap?.let { handler.removeCallbacks(it) }
                    if (mode == Mode.LONG) { player.setPlaybackSpeed(speed); speedBadge.visibility = View.GONE }
                    if (mode == Mode.SEEK) endSeekDrag(apply = false)
                    if (!locked) mode = Mode.ZOOM
                }
                MotionEvent.ACTION_DOWN -> {
                    downX = ev.x; downY = ev.y
                    mode = Mode.NONE
                    seekTarget = -1L
                    startPos = effectivePosition()
                    startBright = currentBrightness()
                    startVol = volLevel
                    handler.postDelayed(longPressRunnable, 550)
                }
                MotionEvent.ACTION_MOVE -> onDrag(ev)
                MotionEvent.ACTION_UP -> {
                    handler.removeCallbacks(longPressRunnable)
                    when (mode) {
                        Mode.LONG -> { player.setPlaybackSpeed(speed); speedBadge.visibility = View.GONE }
                        Mode.SEEK -> endSeekDrag(apply = true)
                        Mode.ZOOM -> if (zoom < 1.05f) resetZoom()
                        Mode.NONE -> { v.performClick(); onTap(ev.x) }
                        else -> {}
                    }
                    mode = Mode.NONE
                }
                MotionEvent.ACTION_CANCEL -> {
                    handler.removeCallbacks(longPressRunnable)
                    if (mode == Mode.LONG) { player.setPlaybackSpeed(speed); speedBadge.visibility = View.GONE }
                    if (mode == Mode.SEEK) endSeekDrag(apply = false)
                    mode = Mode.NONE
                }
            }
            true
        }
    }

    private fun applyZoom() {
        val target = playerView.videoSurfaceView ?: return
        // Let the zoomed picture grow into the black bars instead of being cut off at the video's edge.
        (target.parent as? android.view.ViewGroup)?.clipChildren = false
        playerView.clipChildren = false
        // Allow panning only as far as the picture overflows the screen.
        val sw = touch.width.toFloat()
        val sh = touch.height.toFloat()
        val maxX = max(0f, (target.width * zoom - sw) / 2f)
        val maxY = max(0f, (target.height * zoom - sh) / 2f)
        panX = panX.coerceIn(-maxX, maxX)
        panY = panY.coerceIn(-maxY, maxY)
        target.scaleX = zoom
        target.scaleY = zoom
        target.translationX = panX
        target.translationY = panY
    }

    private fun resetZoom() {
        zoom = 1f; panX = 0f; panY = 0f
        applyZoom()
    }

    /** Ends a sideways swipe: jump exactly to where the finger stopped. */
    private fun endSeekDrag(apply: Boolean) {
        player.setSeekParameters(SeekParameters.DEFAULT)
        if (apply && seekTarget >= 0) seekOrQueue(seekTarget)
        seekTarget = -1L
    }

    private fun onDrag(ev: MotionEvent) {
        if (locked || mode == Mode.ZOOM || ev.pointerCount > 1) return
        val dx = ev.x - downX
        val dy = ev.y - downY
        val w = touch.width.toFloat().coerceAtLeast(1f)
        val h = touch.height.toFloat().coerceAtLeast(1f)
        if (mode == Mode.NONE) {
            mode = when {
                abs(dx) > touchSlop && abs(dx) > abs(dy) -> if (canSeek()) Mode.SEEK else Mode.IGNORE
                abs(dy) > touchSlop -> if (downX < w / 2) Mode.BRIGHT else Mode.VOL
                else -> Mode.NONE
            }
            if (mode != Mode.NONE) handler.removeCallbacks(longPressRunnable)
        }
        when (mode) {
            Mode.SEEK -> {
                val d = effectiveDuration()
                if (d > 0) {
                    val span = min(d, max(60_000L, d / 4))
                    seekTarget = (startPos + dx / w * span).toLong().coerceIn(0L, d)
                    val diff = seekTarget - startPos
                    showHud(0, "${fmtTime(seekTarget)}   ${if (diff >= 0) "+" else "−"}${fmtTime(abs(diff))}")
                    // Move the picture along with the finger (fast keyframe seeks while dragging).
                    val now = SystemClock.uptimeMillis()
                    if (now - lastLiveSeek > 120 && player.isCurrentMediaItemSeekable) {
                        lastLiveSeek = now
                        player.setSeekParameters(SeekParameters.CLOSEST_SYNC)
                        player.seekTo(seekTarget)
                    }
                }
            }
            Mode.BRIGHT -> {
                val b = (startBright - dy / (h * 0.7f)).coerceIn(0.01f, 1f)
                setBrightness(b)
                showHud(R.drawable.ic_brightness, "${(b * 100).roundToInt()}%")
            }
            Mode.VOL -> {
                val lvl = (startVol - dy / (h * 0.7f) * maxVol * 2).coerceIn(0f, maxVol * 2f)
                setVolume(lvl)
            }
            else -> {}
        }
    }

    /** Single tap toggles controls; double tap on the sides skips 10 s; keep tapping to skip more. */
    private fun onTap(x: Float) {
        if (locked) { flashUnlock(); return }
        val now = SystemClock.uptimeMillis()
        val w = touch.width.toFloat()
        val side = when { x < w * 0.38f -> -1; x > w * 0.62f -> 1; else -> 0 }

        if (streakSide != 0 && side == streakSide && now - lastTapTime < 700) {
            streakCount++
            skip(side)
            lastTapTime = now
            return
        }
        if (now - lastTapTime < 300 && abs(x - lastTapX) < w * 0.25f) {
            pendingSingleTap?.let { handler.removeCallbacks(it) }
            lastTapTime = now
            if (side == 0) {
                togglePlay()
                showHud(if (player.playWhenReady) R.drawable.ic_play else R.drawable.ic_pause, "")
                streakSide = 0
            } else {
                streakSide = side
                streakCount = 1
                skip(side)
            }
            return
        }
        lastTapTime = now
        lastTapX = x
        streakSide = 0
        val r = Runnable { if (controlsVisible) hideControls() else showControls(false) }
        pendingSingleTap = r
        handler.postDelayed(r, 300)
    }

    private val hideRipple = Runnable {
        rippleL.visibility = View.GONE
        rippleR.visibility = View.GONE
        streakSide = 0
    }

    /** Some files (certain MP3, AAC, MKV or TS files) can't jump; seeking them would restart from 0:00. */
    /**
     * If the file playing has no seek index, read its duration directly and rebuild the index
     * in the background with FFmpeg, then continue from the same spot in the fixed copy.
     */
    /** A fix started for the previous song must not count for the new one. */
    private fun fixingPathForOtherItem() {
        val p = fixingPath ?: return
        if (paths.getOrNull(player.currentMediaItemIndex) != p) fixingPath = null
    }

    private fun checkSeekable() {
        if (player.isCurrentMediaItemSeekable && player.duration > 0) return
        val i = player.currentMediaItemIndex
        // Wait until the player has actually opened this item; before that it always looks unseekable.
        val tl = player.currentTimeline
        if (tl.isEmpty || i >= tl.windowCount || tl.getWindow(i, Timeline.Window()).isPlaceholder) return
        if (player.playbackState != Player.STATE_READY && player.playbackState != Player.STATE_BUFFERING) return
        val path = paths.getOrNull(i)
        if (path.isNullOrEmpty()) return
        // Always show the length, even when the player itself can't work it out.
        if (player.duration <= 0 && fallbackDurationMs == 0L) bg.execute {
            val dur = SeekFixer.durationMs(path, applicationContext)
            runOnUiThread { if (paths.getOrNull(player.currentMediaItemIndex) == path) fallbackDurationMs = dur }
        }
        if (player.isCurrentMediaItemSeekable || fixingPath == path || SeekFixer.alreadyTried(path)) return
        fixingPath = path
        bg.execute {
            val out = SeekFixer.fix(applicationContext, path)
            runOnUiThread {
                if (fixingPath == path) fixingPath = null
                if (isDestroyed) return@runOnUiThread
                if (out != null && player.currentMediaItemIndex == i && paths.getOrNull(i) == path) {
                    val pos = player.currentPosition
                    val playing = player.playWhenReady
                    player.replaceMediaItem(i, buildItem(i, null))
                    player.seekTo(i, if (pendingSeek >= 0) pendingSeek else pos)
                    pendingSeek = -1L
                    player.playWhenReady = playing
                }
            }
        }
    }

    /** Returns false when there's nothing more we can try for the current file. */
    private fun convertCurrent(): Boolean {
        val i = player.currentMediaItemIndex
        val path = paths.getOrNull(i)
        if (path.isNullOrEmpty() || SeekFixer.exhausted(path)) return false
        if (fixingPath == path) return true
        fixingPath = path
        val pos = player.currentPosition
        findViewById<View>(R.id.busy).visibility = View.VISIBLE
        bg.execute {
            if (fallbackDurationMs == 0L) {
                val dur = SeekFixer.durationMs(path, applicationContext)
                runOnUiThread { if (paths.getOrNull(player.currentMediaItemIndex) == path) fallbackDurationMs = dur }
            }
            val out = SeekFixer.convert(applicationContext, path)
            runOnUiThread {
                if (fixingPath == path) fixingPath = null
                if (isDestroyed) return@runOnUiThread
                findViewById<View>(R.id.busy).visibility = View.GONE
                if (player.currentMediaItemIndex != i || paths.getOrNull(i) != path) return@runOnUiThread
                if (out == null) { toast("Can't play this file"); return@runOnUiThread }
                player.replaceMediaItem(i, buildItem(i, null))
                player.seekTo(i, if (pendingSeek >= 0) pendingSeek else pos)
                pendingSeek = -1L
                player.prepare()
                player.playWhenReady = true
            }
        }
        return true
    }

    private fun updateRepeatButton() {
        val b = findViewById<ImageButton>(R.id.btnRepeat) ?: return
        b.setImageResource(if (player.repeatMode == Player.REPEAT_MODE_ONE) R.drawable.ic_repeat_one else R.drawable.ic_repeat)
        b.imageTintList = android.content.res.ColorStateList.valueOf(
            if (player.repeatMode == Player.REPEAT_MODE_OFF) 0x99FFFFFF.toInt()
            else ContextCompat.getColor(this, R.color.accent)
        )
        b.contentDescription = when (player.repeatMode) {
            Player.REPEAT_MODE_ALL -> "Repeat all"
            Player.REPEAT_MODE_ONE -> "Repeat one"
            else -> "Repeat off"
        }
    }

    private fun canSeek(): Boolean = player.isCurrentMediaItemSeekable ||
        (fixingPath != null && fixingPath == paths.getOrNull(player.currentMediaItemIndex))

    private fun effectiveDuration(): Long = player.duration.takeIf { it > 0 } ?: fallbackDurationMs

    private fun effectivePosition(): Long =
        if (pendingSeek >= 0 && !player.isCurrentMediaItemSeekable) pendingSeek else player.currentPosition

    /** Seeks now, or remembers the spot and jumps there the moment the file is ready. */
    private fun seekOrQueue(t: Long) {
        if (player.isCurrentMediaItemSeekable) player.seekTo(t) else if (fixingPath != null) pendingSeek = t
    }

    private fun skip(side: Int) {
        if (!canSeek()) return
        val d = effectiveDuration()
        val target = effectivePosition() + side * SKIP_MS
        seekOrQueue(target.coerceIn(0L, if (d > 0) d else Long.MAX_VALUE))
        val view = if (side < 0) rippleL else rippleR
        (if (side < 0) rippleR else rippleL).visibility = View.GONE
        view.text = "${streakCount * SKIP_MS / 1000} seconds"
        view.visibility = View.VISIBLE
        handler.removeCallbacks(hideRipple)
        handler.postDelayed(hideRipple, 700)
    }

    // ---------------------------------------------------------------- brightness & volume

    private fun currentBrightness(): Float {
        if (brightness >= 0) return brightness
        return try {
            Settings.System.getInt(contentResolver, Settings.System.SCREEN_BRIGHTNESS) / 255f
        } catch (_: Exception) { 0.5f }
    }

    private fun setBrightness(b: Float) {
        brightness = b
        window.attributes = window.attributes.apply { screenBrightness = b }
    }

    private fun setVolume(level: Float) {
        volLevel = level
        val sys = min(level, maxVol.toFloat()).roundToInt()
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, sys, 0)
        val boost = level - maxVol
        val fx = if (boost > 0) ensureEnhancer() else enhancer
        try {
            if (boost > 0 && fx != null) {
                fx.setTargetGain((boost / maxVol * 1500).roundToInt()) // up to +15 dB
                fx.setEnabled(true)
            } else {
                fx?.setEnabled(false)
            }
        } catch (_: Exception) {}
        val pct = (level / maxVol * 100).roundToInt()
        showHud(if (level <= 0f) R.drawable.ic_volume_off else R.drawable.ic_volume,
            if (boost > 0) "$pct%  BOOST" else "$pct%")
    }

    private fun ensureEnhancer(): LoudnessEnhancer? {
        val sid = player.audioSessionId
        if (sid == C.AUDIO_SESSION_ID_UNSET) return null
        if (enhancer == null || enhancerSession != sid) {
            try { enhancer?.release() } catch (_: Exception) {}
            enhancer = try { LoudnessEnhancer(sid) } catch (_: Exception) { null }
            enhancerSession = sid
        }
        return enhancer
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        return when (keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> { setVolume((volLevel + 1).coerceAtMost(maxVol * 2f)); true }
            KeyEvent.KEYCODE_VOLUME_DOWN -> { setVolume((volLevel - 1).coerceAtLeast(0f)); true }
            KeyEvent.KEYCODE_SPACE, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> { togglePlay(); true }
            KeyEvent.KEYCODE_DPAD_RIGHT -> { player.seekForward(); true }
            KeyEvent.KEYCODE_DPAD_LEFT -> { player.seekBack(); true }
            else -> super.onKeyDown(keyCode, event)
        }
    }

    // ---------------------------------------------------------------- dialogs

    private fun dialog() = MaterialAlertDialogBuilder(this).also {
        handler.removeCallbacks(hideControlsRunnable)
    }

    private fun speedDialog() {
        val labels = SPEEDS.map { "${trim(it)}×" }.toTypedArray()
        val checked = SPEEDS.indexOfFirst { it == speed }
        dialog().setTitle("Playback speed")
            .setSingleChoiceItems(labels, checked) { d, which ->
                speed = SPEEDS[which]
                player.setPlaybackSpeed(speed)
                showHud(R.drawable.ic_speed, "${trim(speed)}×")
                d.dismiss()
            }
            .setOnDismissListener { hideSystemUi(); showControls(false) }
            .show()
    }

    private fun trim(f: Float) = if (f == f.toInt().toFloat()) f.toInt().toString() else f.toString()

    private data class TrackOpt(val group: Tracks.Group, val index: Int, val label: String, val selected: Boolean)

    private fun tracksOf(type: Int): List<TrackOpt> {
        val out = ArrayList<TrackOpt>()
        for (g in player.currentTracks.groups) {
            if (g.type != type) continue
            for (i in 0 until g.length) {
                if (!g.isTrackSupported(i)) continue
                out += TrackOpt(g, i, label(g.getTrackFormat(i), out.size + 1), g.isTrackSelected(i))
            }
        }
        return out
    }

    private fun label(f: Format, n: Int): String {
        val parts = ArrayList<String>()
        f.label?.takeIf { it.isNotBlank() }?.let { parts += it }
        f.language?.takeIf { it != "und" && it.isNotBlank() }?.let {
            val name = Locale(it).displayLanguage
            parts += if (name.isNullOrBlank()) it else name
        }
        if (f.channelCount > 0) parts += when (f.channelCount) { 1 -> "Mono"; 2 -> "Stereo"; 6 -> "5.1"; 8 -> "7.1"; else -> "${f.channelCount}ch" }
        return if (parts.isEmpty()) "Track $n" else "Track $n · " + parts.distinct().joinToString(" · ")
    }

    private fun select(type: Int, opt: TrackOpt?) {
        player.trackSelectionParameters = player.trackSelectionParameters.buildUpon().apply {
            if (opt == null) {
                setTrackTypeDisabled(type, true)
            } else {
                setTrackTypeDisabled(type, false)
                setOverrideForType(TrackSelectionOverride(opt.group.mediaTrackGroup, opt.index))
            }
        }.build()
    }

    private fun audioDialog() {
        val tracks = tracksOf(C.TRACK_TYPE_AUDIO)
        if (tracks.isEmpty()) {
            toast("No playable audio track in this video")
            return
        }
        dialog().setTitle("Audio track")
            .setSingleChoiceItems(tracks.map { it.label }.toTypedArray(), tracks.indexOfFirst { it.selected }) { d, which ->
                select(C.TRACK_TYPE_AUDIO, tracks[which])
                d.dismiss()
            }
            .setOnDismissListener { hideSystemUi(); showControls(false) }
            .show()
    }

    private fun subtitleDialog() {
        val tracks = tracksOf(C.TRACK_TYPE_TEXT)
        val disabled = player.trackSelectionParameters.disabledTrackTypes.contains(C.TRACK_TYPE_TEXT)
        val none = disabled || tracks.none { it.selected }
        val labels = ArrayList<String>()
        labels += (if (none) "✓  " else "     ") + "Off"
        tracks.forEach { labels += (if (!disabled && it.selected) "✓  " else "     ") + it.label }
        labels += "     Open subtitle file…"
        labels += "     Text size: ${SUB_SIZE_NAMES[subSizeIdx]}"
        dialog().setTitle("Subtitles")
            .setItems(labels.toTypedArray()) { _, which ->
                when {
                    which == 0 -> select(C.TRACK_TYPE_TEXT, null)
                    which <= tracks.size -> select(C.TRACK_TYPE_TEXT, tracks[which - 1])
                    which == tracks.size + 1 -> subPicker.launch(arrayOf("*/*"))
                    else -> {
                        subSizeIdx = (subSizeIdx + 1) % SUB_SIZES.size
                        Prefs.setInt(this, "subSize", subSizeIdx)
                        styleSubtitles()
                        showHud(R.drawable.ic_subtitle, SUB_SIZE_NAMES[subSizeIdx])
                    }
                }
            }
            .setOnDismissListener { hideSystemUi(); showControls(false) }
            .show()
    }

    // ---------------------------------------------------------------- picture in picture

    private fun enterPip() {
        if (!packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)) {
            toast("Pop-up player is not supported on this phone")
            return
        }
        val vs = player.videoSize
        val ratio = if (vs.width > 0 && vs.height > 0) vs.width.toFloat() / vs.height else 16f / 9f
        val r = when {
            ratio > 2.39f -> Rational(239, 100)
            ratio < 1 / 2.39f -> Rational(100, 239)
            vs.width > 0 && vs.height > 0 -> Rational(vs.width, vs.height)
            else -> Rational(16, 9)
        }
        try {
            enterPictureInPictureMode(PictureInPictureParams.Builder().setAspectRatio(r).build())
        } catch (_: Exception) {
            toast("Pop-up player is not available right now")
        }
    }
}
