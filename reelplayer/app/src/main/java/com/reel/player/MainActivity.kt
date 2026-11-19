package com.reel.player

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.app.Activity
import android.content.ClipData
import android.provider.MediaStore
import android.view.Gravity
import android.widget.CheckBox
import android.widget.ImageButton
import android.widget.PopupMenu
import androidx.activity.result.IntentSenderRequest
import androidx.appcompat.view.ActionMode
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.GravityCompat
import androidx.drawerlayout.widget.DrawerLayout
import com.google.android.material.navigation.NavigationView
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private lateinit var toolbar: MaterialToolbar
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var list: RecyclerView
    private lateinit var empty: TextView
    private lateinit var nowBar: View
    private lateinit var nowTitle: TextView
    private lateinit var nowPlay: ImageButton
    private lateinit var nowTime: TextView
    private val uiHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val nowTicker = object : Runnable {
        override fun run() {
            updateNowTime()
            uiHandler.postDelayed(this, 1000)
        }
    }
    private lateinit var drawer: DrawerLayout
    private lateinit var nav: NavigationView

    private var folders: List<Folder> = emptyList()
    private var current: Folder? = null
    private var renderedKey: String? = null
    private var actionMode: ActionMode? = null
    private var selectAllBox: CheckBox? = null
    private var pendingDelete: List<Video> = emptyList()
    private val selection = Selection { onSelectionChanged() }

    /** Android 11+ shows its own "Allow Reel Player to delete…?" dialog. */
    private val deleteLauncher = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { r ->
        if (r.resultCode == Activity.RESULT_OK) {
            Toast.makeText(this, "Deleted", Toast.LENGTH_SHORT).show()
            actionMode?.finish()
        }
        scan()
    }

    /** Android 8–10 need storage write permission to delete. */
    private val askWrite = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) confirmDelete(pendingDelete)
        else Toast.makeText(this, "Allow storage access to delete videos", Toast.LENGTH_SHORT).show()
    }
    private var sort = 0 // 0 = name, 1 = newest, 2 = largest
    private var scanning = false
    private val io = Executors.newSingleThreadExecutor()

    private val permission: String
        get() = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_VIDEO
        else Manifest.permission.READ_EXTERNAL_STORAGE

    private val askPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) scan() else showNoPermission()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        toolbar = findViewById(R.id.toolbar)
        swipe = findViewById(R.id.swipe)
        list = findViewById(R.id.list)
        empty = findViewById(R.id.empty)
        nowBar = findViewById(R.id.nowBar)
        nowTitle = findViewById(R.id.nowTitle)
        nowPlay = findViewById(R.id.nowPlay)
        nowTime = findViewById(R.id.nowTime)
        nowBar.setOnClickListener { startActivity(Intent(this, PlayerActivity::class.java)) }
        nowPlay.setOnClickListener {
            PlayerHolder.peek()?.let { p -> if (p.isPlaying) p.pause() else p.play() }
        }
        findViewById<View>(R.id.nowClose).setOnClickListener {
            PlayerHolder.peek()?.let { p -> p.stop(); p.clearMediaItems() }
            try { stopService(Intent(this, PlaybackService::class.java)) } catch (_: Exception) {}
            updateNowBar()
        }

        setSupportActionBar(toolbar)
        drawer = findViewById(R.id.drawer)
        nav = findViewById(R.id.nav)
        toolbar.setNavigationOnClickListener {
            if (current != null) { current = null; render() } else drawer.openDrawer(GravityCompat.START)
        }
        nav.setNavigationItemSelectedListener { item ->
            drawer.closeDrawer(GravityCompat.START)
            when (item.itemId) {
                R.id.navVideos -> { current = null; render() }
                R.id.navYoutube -> startActivity(Intent(this, YouTubeActivity::class.java))
                R.id.navDownloader -> startActivity(Intent(this, DownloaderActivity::class.java))
                R.id.navDownloads -> openDownloadsFolder()
                R.id.navUpdate -> updateEngine()
                R.id.navAppUpdate -> AppUpdater.checkAndOffer(this) { refreshUpdateItem() }
            }
            item.itemId == R.id.navVideos
        }
        list.layoutManager = LinearLayoutManager(this)
        swipe.setColorSchemeColors(ContextCompat.getColor(this, R.color.accent))
        swipe.setProgressBackgroundColorSchemeColor(ContextCompat.getColor(this, R.color.panel2))
        swipe.setOnRefreshListener { scan() }
        sort = Prefs.getInt(this, "sort", 0)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (drawer.isDrawerOpen(GravityCompat.START)) {
                    drawer.closeDrawer(GravityCompat.START)
                } else if (current != null) {
                    current = null
                    render()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        if (!hasPermission()) askPermission.launch(permission)
        render()
    }

    private val nowListener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) = updateNowBar()
        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) = updateNowBar()
        override fun onPlaybackStateChanged(playbackState: Int) = updateNowBar()
    }

    /** Shows what is playing in the background, with play/pause and stop, like a music app. */
    private fun updateNowBar() {
        val p = PlayerHolder.peek()
        if (p == null || !PlayerHolder.isActive()) {
            nowBar.visibility = View.GONE
            list.setPadding(0, 0, 0, (24 * resources.displayMetrics.density).toInt())
            return
        }
        nowBar.visibility = View.VISIBLE
        nowTitle.text = p.currentMediaItem?.mediaMetadata?.title ?: ""
        nowPlay.setImageResource(if (p.isPlaying) R.drawable.ic_pause else R.drawable.ic_play)
        updateNowTime()
        list.setPadding(0, 0, 0, (96 * resources.displayMetrics.density).toInt())
    }

    /** "1:23 / 3:45" under NOW PLAYING. */
    private fun updateNowTime() {
        val p = PlayerHolder.peek()
        if (p == null || nowBar.visibility != View.VISIBLE) return
        val d = p.duration
        nowTime.visibility = View.VISIBLE
        nowTime.text = if (d > 0) "${fmtTime(p.currentPosition)} / ${fmtTime(d)}" else fmtTime(p.currentPosition)
    }

    override fun onStart() {
        super.onStart()
        refreshUpdateItem()
        uiHandler.post(nowTicker)
        PlayerHolder.peek()?.addListener(nowListener)
        updateNowBar()
    }

    override fun onStop() {
        super.onStop()
        uiHandler.removeCallbacks(nowTicker)
        PlayerHolder.peek()?.removeListener(nowListener)
    }

    override fun onResume() {
        super.onResume()
        // Rescan every time the app comes back, so new downloads show up and watch progress updates.
        if (hasPermission()) scan()
    }

    private fun hasPermission() =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun scan() {
        if (scanning) return
        scanning = true
        swipe.isRefreshing = true
        io.execute {
            val result = try { MediaRepo.load(this) } catch (e: Exception) { emptyList() }
            runOnUiThread {
                scanning = false
                swipe.isRefreshing = false
                folders = result
                current = current?.let { c -> result.find { it.id == c.id } }
                render()
                if (selection.active) {
                    selection.keys.retainAll(visibleKeys().toSet())
                    onSelectionChanged()
                }
            }
        }
    }

    private fun render() {
        val bar = supportActionBar ?: return
        val folder = current
        if (folder == null) {
            val total = folders.sumOf { it.videos.size }
            bar.title = "Folders"
            bar.subtitle = if (folders.isEmpty()) null else "$total videos in ${folders.size} folders"
            toolbar.setNavigationIcon(R.drawable.ic_menu)
            toolbar.navigationContentDescription = "Open menu"
            if (renderedKey != "") actionMode?.finish()
            renderedKey = ""
            list.adapter = FolderAdapter(MediaRepo.sortFolders(folders, sort), selection,
                onClick = {
                    current = it
                    render()
                    list.scrollToPosition(0)
                },
                onMore = { f, anchor -> folderMenu(f, anchor) })
            if (hasPermission()) {
                empty.text = if (scanning) "" else "No videos found on this device.\nPull down to scan again."
                empty.visibility = if (folders.isEmpty() && !scanning) View.VISIBLE else View.GONE
                empty.setOnClickListener(null)
            }
        } else {
            val videos = MediaRepo.sortVideos(folder.videos, sort)
            bar.title = folder.name
            bar.subtitle = "${videos.size} videos · ${fmtSize(folder.totalSize)}"
            toolbar.setNavigationIcon(R.drawable.ic_back)
            toolbar.navigationContentDescription = "Back to folders"
            if (renderedKey != folder.id) actionMode?.finish()
            renderedKey = folder.id
            list.adapter = VideoAdapter(videos, selection,
                onClick = { index -> if (index >= 0) play(videos, index) },
                onMore = { v, anchor -> videoMenu(v, anchor) })
            empty.visibility = View.GONE
        }
    }

    /** Opens the folder where the downloader saves videos (the phone's Download folder). */
    private fun openDownloadsFolder() {
        val f = folders.find { it.name == DownloadService.FOLDER_NAME }
        if (f == null) {
            Toast.makeText(this, "No downloaded videos yet. Use Video Downloader to get some.", Toast.LENGTH_LONG).show()
        } else {
            current = f
            render()
            list.scrollToPosition(0)
        }
    }

    /** Sidebar shows "App update · 1.15 available" when there is a newer version. */
    private fun refreshUpdateItem() {
        val item = nav.menu.findItem(R.id.navAppUpdate) ?: return
        val now = AppUpdater.installedName(this)
        item.title = "App update  (v$now)"
        AppUpdater.checkQuietly(this) { r ->
            item.title = if (r != null) "App update  ·  ${r.name} available" else "App update  (v$now)"
            if (r != null) item.setIcon(R.drawable.ic_download_dot) else item.setIcon(R.drawable.ic_download)
        }
    }

    private fun updateEngine() {
        Toast.makeText(this, "Updating the downloader…", Toast.LENGTH_SHORT).show()
        io.execute {
            val msg = try {
                Engine.init(applicationContext)
                Engine.update(applicationContext, force = true)
            } catch (e: Exception) {
                "Update failed: " + Engine.humanize(e)
            }
            runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
        }
    }

    private fun showNoPermission() {
        empty.text = "Reel Player needs access to your videos to list them.\n\nTap here to allow."
        empty.visibility = View.VISIBLE
        empty.setOnClickListener {
            if (shouldShowRequestPermissionRationale(permission)) {
                askPermission.launch(permission)
            } else {
                startActivity(
                    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
                )
            }
        }
    }

    private fun play(videos: List<Video>, index: Int) {
        val i = Intent(this, PlayerActivity::class.java)
            .putStringArrayListExtra(PlayerActivity.EXTRA_URIS, ArrayList(videos.map { it.uri.toString() }))
            .putStringArrayListExtra(PlayerActivity.EXTRA_NAMES, ArrayList(videos.map { it.name }))
            .putStringArrayListExtra(PlayerActivity.EXTRA_PATHS, ArrayList(videos.map { it.path ?: "" }))
            .putExtra(PlayerActivity.EXTRA_INDEX, index)
        startActivity(i)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_SORT, 0, "Sort").setIcon(R.drawable.ic_sort)
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        menu.add(0, MENU_REFRESH, 1, "Refresh").setIcon(R.drawable.ic_refresh)
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        android.R.id.home -> { current = null; render(); true }
        MENU_SORT -> {
            MaterialAlertDialogBuilder(this)
                .setTitle("Sort by")
                .setSingleChoiceItems(arrayOf("Name", "Newest", "Largest"), sort) { d, which ->
                    sort = which
                    Prefs.setInt(this, "sort", which)
                    render()
                    d.dismiss()
                }
                .show()
            true
        }
        MENU_REFRESH -> { if (hasPermission()) scan() else askPermission.launch(permission); true }
        else -> super.onOptionsItemSelected(item)
    }

    // ------------------------------------------------------------------ select, share, delete

    /** Keys of everything in the list on screen (folder IDs or video links). */
    private fun visibleKeys(): List<String> = when (val a = list.adapter) {
        is FolderAdapter -> a.keys
        is VideoAdapter -> a.keys
        else -> emptyList()
    }

    /** The videos behind the ticked items (all videos of ticked folders). */
    private fun selectedVideos(): List<Video> {
        val folder = current
        return if (folder == null) folders.filter { it.id in selection.keys }.flatMap { it.videos }
        else folder.videos.filter { it.uri.toString() in selection.keys }
    }

    private fun onSelectionChanged() {
        list.adapter?.notifyDataSetChanged()
        if (!selection.active) return
        if (selection.keys.isEmpty()) { actionMode?.finish(); return }
        if (actionMode == null) actionMode = startSupportActionMode(selectionCallback)
        actionMode?.title = "${selection.keys.size} selected"
        val all = visibleKeys()
        selectAllBox?.isChecked = all.isNotEmpty() && selection.keys.containsAll(all)
    }

    private val selectionCallback = object : ActionMode.Callback {
        override fun onCreateActionMode(mode: ActionMode, menu: Menu): Boolean {
            val box = CheckBox(this@MainActivity).apply {
                text = "All"
                setTextColor(ContextCompat.getColor(this@MainActivity, R.color.fg))
                buttonTintList = android.content.res.ColorStateList.valueOf(ContextCompat.getColor(this@MainActivity, R.color.accent))
                gravity = Gravity.CENTER_VERTICAL
                setOnClickListener {
                    if (isChecked) selection.setAll(visibleKeys()) else actionMode?.finish()
                }
            }
            selectAllBox = box
            menu.add(0, ACT_ALL, 0, "Select all").setActionView(box).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
            menu.add(0, ACT_SHARE, 1, "Share").setIcon(R.drawable.ic_share).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
            menu.add(0, ACT_DELETE, 2, "Delete").setIcon(R.drawable.ic_delete).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
            return true
        }

        override fun onPrepareActionMode(mode: ActionMode, menu: Menu) = false

        override fun onActionItemClicked(mode: ActionMode, item: MenuItem): Boolean {
            when (item.itemId) {
                ACT_SHARE -> share(selectedVideos())
                ACT_DELETE -> delete(selectedVideos())
                else -> return false
            }
            return true
        }

        override fun onDestroyActionMode(mode: ActionMode) {
            actionMode = null
            selectAllBox = null
            selection.clear()
            list.adapter?.notifyDataSetChanged()
        }
    }

    private fun folderMenu(f: Folder, anchor: View) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, "Share all videos")
            menu.add(0, 2, 1, "Delete folder")
            setOnMenuItemClickListener {
                when (it.itemId) {
                    1 -> share(f.videos)
                    2 -> delete(f.videos)
                }
                true
            }
        }.show()
    }

    private fun videoMenu(v: Video, anchor: View) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, "Share")
            menu.add(0, 2, 1, "Delete")
            menu.add(0, 3, 2, "Info")
            setOnMenuItemClickListener {
                when (it.itemId) {
                    1 -> share(listOf(v))
                    2 -> delete(listOf(v))
                    3 -> info(v)
                }
                true
            }
        }.show()
    }

    private fun share(videos: List<Video>) {
        if (videos.isEmpty()) return
        val uris = ArrayList(videos.map { it.uri })
        val send = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).putExtra(Intent.EXTRA_STREAM, uris[0])
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
        }
        send.type = "video/*"
        val clip = ClipData.newRawUri(null, uris[0])
        for (k in 1 until uris.size) clip.addItem(ClipData.Item(uris[k]))
        send.clipData = clip
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        startActivity(Intent.createChooser(send, "Share"))
    }

    private fun delete(videos: List<Video>) {
        if (videos.isEmpty()) return
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                val pi = MediaStore.createDeleteRequest(contentResolver, videos.map { it.uri })
                deleteLauncher.launch(IntentSenderRequest.Builder(pi.intentSender).build())
            } catch (e: Exception) {
                Toast.makeText(this, "Couldn't delete: ${e.message}", Toast.LENGTH_SHORT).show()
            }
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            pendingDelete = videos
            askWrite.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
            return
        }
        confirmDelete(videos)
    }

    private fun confirmDelete(videos: List<Video>) {
        if (videos.isEmpty()) return
        val n = videos.size
        MaterialAlertDialogBuilder(this)
            .setTitle(if (n == 1) "Delete this video?" else "Delete $n videos?")
            .setMessage(if (n == 1) videos[0].name else "They will be removed from your phone.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Delete") { _, _ ->
                io.execute {
                    for (v in videos) {
                        try { contentResolver.delete(v.uri, null, null) } catch (_: Exception) {}
                        try { v.path?.let { java.io.File(it).takeIf { f -> f.exists() }?.delete() } } catch (_: Exception) {}
                    }
                    runOnUiThread {
                        Toast.makeText(this, "Deleted", Toast.LENGTH_SHORT).show()
                        actionMode?.finish()
                        scan()
                    }
                }
            }
            .show()
    }

    private fun info(v: Video) {
        val date = java.text.DateFormat.getDateTimeInstance().format(java.util.Date(v.dateAdded * 1000))
        val lines = listOfNotNull(
            "Size: ${fmtSize(v.size)}",
            if (v.duration > 0) "Length: ${fmtTime(v.duration)}" else null,
            "Folder: ${v.folderName}",
            "Added: $date",
            v.path?.let { "Location: $it" }
        )
        MaterialAlertDialogBuilder(this)
            .setTitle(v.name)
            .setMessage(lines.joinToString("\n"))
            .setPositiveButton("OK", null)
            .show()
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdown()
    }

    companion object {
        private const val MENU_SORT = 1
        private const val MENU_REFRESH = 2
        private const val ACT_ALL = 10
        private const val ACT_SHARE = 11
        private const val ACT_DELETE = 12
    }
}
