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
            '[data-sigil*=MTopBlueBarHeader],[aria-label*="Open app"],[aria-label*="Get the app"]{display:none!important}' +
            'html,body{padding-top:0!important;margin-top:0!important}';
          document.documentElement.appendChild(css);
          // Like, react, share, send, save and "more" buttons are hidden; Comment stays.
          var hideLabel = /^(like|unlike|remove like|react|reaction|share|send|save|more|remix|follow)/i;
          function sweep(){
            document.querySelectorAll('[aria-label]').forEach(function(e){
              var l = e.getAttribute('aria-label') || '';
              if (hideLabel.test(l.trim()) && !/comment/i.test(l)) e.style.display = 'none';
            });
            // Gap left at the top by Facebook's hidden header.
            var n = document.body, depth = 0;
            while (n && depth < 6) {
              var cs = getComputedStyle(n);
              if (parseFloat(cs.paddingTop) > 20) n.style.paddingTop = '0px';
              if (parseFloat(cs.marginTop) > 20) n.style.marginTop = '0px';
              n = n.firstElementChild; depth++;
            }
            if (location.pathname.indexOf('/reel') === 0) return;
            document.querySelectorAll('body *').forEach(function(e){
              var s = getComputedStyle(e);
              if ((s.position === 'fixed' || s.position === 'sticky') && e.getBoundingClientRect().top < 4 &&
                  e.offsetHeight < 140 && !e.querySelector('video')) e.style.display = 'none';
            });
          }
          sweep(); setInterval(sweep, 1000);
          new MutationObserver(function(){ clearTimeout(window.__reelT); window.__reelT = setTimeout(sweep, 150); })
            .observe(document.documentElement, {childList:true, subtree:true});
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
        findViewById<View>(R.id.btnBack).setOnClickListener { finish() }
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
            CookieManager.getInstance().flush()
            // Find the reel or video that is on screen right now.
            web.evaluateJavascript(findVideoJs) { raw ->
                val found = raw?.trim('"')?.replace("\\/", "/")?.takeIf { it.startsWith("http") }
                val u = found?.let { normalize(it) } ?: videoUrl
                if (u == null) {
                    android.widget.Toast.makeText(this, "Open a reel or video first, then tap Download", android.widget.Toast.LENGTH_SHORT).show()
                } else {
                    YouTubeActivity.pickQuality(this, io, u, "Facebook video")
                }
            }
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

    private fun normalize(u: String) =
        u.replace("://m.facebook.com", "://www.facebook.com").replace("://web.facebook.com", "://www.facebook.com")

    /** The Download button stays on the Reels and Videos pages (it finds the video on screen when tapped). */
    private fun check(url: String) {
        val isVideo = videoLink.containsMatchIn(url)
        videoUrl = if (isVideo) normalize(url) else null
        val path = android.net.Uri.parse(url).path ?: ""
        val videoPage = isVideo || path.startsWith("/reel") || path.startsWith("/watch") || path.contains("/videos")
        videoBar.visibility = if (videoPage) View.VISIBLE else View.GONE
    }

    /** JavaScript: the link of the video in the middle of the screen (or its file as a last resort). */
    private val findVideoJs = """
        (function(){
          var u = location.href;
          if (/\/reel\/\d+/.test(u) || /\/videos\/\d+/.test(u) || /[?&]v=\d+/.test(u)) return u;
          var mid = innerHeight / 2, best = null;
          document.querySelectorAll('video').forEach(function(v){
            var r = v.getBoundingClientRect();
            if (!best && r.top < mid && r.bottom > mid) best = v;
          });
          var el = best;
          for (var k = 0; el && k < 15; k++, el = el.parentElement) {
            var a = el.querySelector('a[href*="/reel/"],a[href*="/videos/"],a[href*="watch/?v="],a[href*="video.php"]');
            if (a) return a.href;
            var id = el.getAttribute && (el.getAttribute('data-video-id') || el.getAttribute('data-id'));
            if (id && /^\d{8,}$/.test(id)) return 'https://www.facebook.com/reel/' + id;
          }
          var m = document.documentElement.innerHTML.match(/"video_id":"(\d{8,})"/);
          if (m) return 'https://www.facebook.com/reel/' + m[1];
          if (best && best.currentSrc && best.currentSrc.indexOf('http') === 0) return best.currentSrc;
          return '';
        })();
    """.trimIndent()

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
