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
    private var home = REELS
    private var firstLoad = true

    companion object {
        private const val REELS = "https://m.facebook.com/reel/"
        private const val WATCH = "https://m.facebook.com/watch/"

        /** Pages that belong to videos, plus the pages needed to sign in. */
        private val allowed = Regex(
            "^/(reel|reels|watch|video\\.php|story\\.php|share/(r|v)/|login|checkpoint|recover|two_step|auth|confirmemail|cookie|privacy/consent|dialog/consent)" +
                "|/videos/"
        )
    }

    /** Hide Facebook's own top bar and menus so only videos are shown. */
    private val hideChrome = """
        (function(){
          if (window.__reelHide) return; window.__reelHide = true;
          var css = document.createElement('style');
          css.textContent = '[role=banner],[role=navigation],[role=tablist],#header,#MChromeHeader,' +
            '[data-sigil*=MTopBlueBarHeader],[aria-label*="Open app"],[aria-label*="Get the app"]{display:none!important}';
          document.documentElement.appendChild(css);
          function sweep(){
            if (location.pathname.indexOf('/reel') === 0) return;
            document.querySelectorAll('body *').forEach(function(e){
              var s = getComputedStyle(e);
              if ((s.position === 'fixed' || s.position === 'sticky') && e.getBoundingClientRect().top < 4 &&
                  e.offsetHeight < 140 && !e.querySelector('video')) e.style.display = 'none';
            });
          }
          sweep(); setInterval(sweep, 1500);
        })();
    """.trimIndent()

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
        findViewById<MaterialToolbar>(R.id.toolbar).setNavigationOnClickListener { finish() }
        // Only the video side of Facebook: Reels and Videos, like the Video tab of the Facebook app.
        val tabs = findViewById<com.google.android.material.tabs.TabLayout>(R.id.tabs)
        tabs.addTab(tabs.newTab().setText("Reels"))
        tabs.addTab(tabs.newTab().setText("Videos"))
        tabs.addOnTabSelectedListener(object : com.google.android.material.tabs.TabLayout.OnTabSelectedListener {
            override fun onTabSelected(t: com.google.android.material.tabs.TabLayout.Tab) { home = if (t.position == 0) REELS else WATCH; web.loadUrl(home) }
            override fun onTabUnselected(t: com.google.android.material.tabs.TabLayout.Tab) {}
            override fun onTabReselected(t: com.google.android.material.tabs.TabLayout.Tab) { web.loadUrl(home) }
        })

        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        web.settings.apply {
            // Look like normal Chrome: Facebook serves a cut-down page to in-app browsers.
            userAgentString = userAgentString.replace("; wv", "").replace(Regex("Version/[\\d.]+ "), "")
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            loadWithOverviewMode = true
            useWideViewPort = true
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, p: Int) {
                // Only the first page load shows progress; Facebook keeps loading in the background.
                if (firstLoad) { progress.visibility = if (p < 100) View.VISIBLE else View.GONE; progress.progress = p }
                if (p >= 100) firstLoad = false
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
            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) { if (keepToVideos(url)) check(url) }
            override fun onPageFinished(view: WebView, url: String) { view.evaluateJavascript(hideChrome, null) }
            // Reels change the address without loading a new page while you swipe.
            override fun doUpdateVisitedHistory(view: WebView, url: String, isReload: Boolean) {
                if (keepToVideos(url)) check(url)
                view.evaluateJavascript(hideChrome, null)
            }
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

        if (savedInstanceState == null) web.loadUrl(home) else web.restoreState(savedInstanceState)
    }

    /** Anything that isn't a video page (feed, friends, marketplace…) goes back to the videos. */
    private fun keepToVideos(url: String): Boolean {
        val uri = android.net.Uri.parse(url)
        val host = uri.host ?: return true
        if (!host.contains("facebook.com")) return true
        // Until the user has signed in, let Facebook show its login pages freely.
        val cookies = CookieManager.getInstance().getCookie("https://www.facebook.com") ?: ""
        if (!cookies.contains("c_user=")) return true
        val path = uri.path ?: "/"
        if (allowed.containsMatchIn(path)) return true
        web.post { web.loadUrl(home) }
        return false
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
