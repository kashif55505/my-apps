package com.reel.player

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar
import java.util.concurrent.Executors

/**
 * Facebook inside the app (the real site, signed in with the user's own account).
 * When a reel or video is open, buttons appear to download it or play it in Reel Player.
 */
class FacebookActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var videoBar: View
    private lateinit var progress: ProgressBar
    private val io = Executors.newSingleThreadExecutor()
    private var videoUrl: String? = null

    private val videoLink = Regex(
        "facebook\\.com/(reel/\\d+|watch/?\\?v=\\d+|[^/?#]+/videos/|video\\.php|share/(r|v)/|story\\.php\\?story_fbid)|fb\\.watch/"
    )

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_facebook)
        web = findViewById(R.id.web)
        videoBar = findViewById(R.id.videoBar)
        progress = findViewById(R.id.progress)
        findViewById<MaterialToolbar>(R.id.toolbar).apply {
            setNavigationOnClickListener { finish() }
            menu.add(0, 1, 0, "Reels").setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_ALWAYS)
            menu.add(0, 2, 1, "Videos").setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_ALWAYS)
            setOnMenuItemClickListener {
                web.loadUrl(if (it.itemId == 1) "https://m.facebook.com/reel/" else "https://m.facebook.com/watch/")
                true
            }
        }

        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            loadWithOverviewMode = true
            useWideViewPort = true
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, p: Int) {
                progress.visibility = if (p < 100) View.VISIBLE else View.GONE
                progress.progress = p
            }
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val host = request.url.host ?: return false
                // Stay on Facebook; other links open in the phone's browser.
                if (host.contains("facebook.com") || host.contains("fb.com") || host.contains("fb.watch") || host.contains("fbcdn")) return false
                runCatching { startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, request.url)) }
                return true
            }
            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) = check(url)
            // Reels change the address without loading a new page while you swipe.
            override fun doUpdateVisitedHistory(view: WebView, url: String, isReload: Boolean) = check(url)
        }

        findViewById<View>(R.id.btnDownload).setOnClickListener {
            val u = videoUrl ?: return@setOnClickListener
            CookieManager.getInstance().flush()
            YouTubeActivity.pickQuality(this, io, u, "Facebook video")
        }
        findViewById<View>(R.id.btnPlay).setOnClickListener {
            val u = videoUrl ?: return@setOnClickListener
            CookieManager.getInstance().flush()
            web.onPause()   // stop Facebook's own sound
            YouTubeActivity.openVideo(this, u, "Facebook video", null, null)
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else { isEnabled = false; onBackPressedDispatcher.onBackPressed() }
            }
        })

        if (savedInstanceState == null) web.loadUrl("https://m.facebook.com/reel/") else web.restoreState(savedInstanceState)
    }

    private fun check(url: String) {
        val isVideo = videoLink.containsMatchIn(url)
        videoUrl = if (isVideo) url.replace("://m.facebook.com", "://www.facebook.com").replace("://web.facebook.com", "://www.facebook.com") else null
        videoBar.visibility = if (isVideo) View.VISIBLE else View.GONE
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onResume() { super.onResume(); web.onResume() }
    override fun onPause() { super.onPause(); web.onPause(); CookieManager.getInstance().flush() }

    override fun onDestroy() {
        io.shutdown()
        web.destroy()
        super.onDestroy()
    }
}
