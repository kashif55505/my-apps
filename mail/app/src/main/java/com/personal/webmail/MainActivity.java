package com.personal.webmail;

import android.Manifest;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.ViewGroup;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.webkit.WebViewAssetLoader;

import org.json.JSONObject;

/** Hosts the mail interface (assets/www) in a WebView and connects it to the phone's mail engine. */
public class MainActivity extends ComponentActivity {

    private static final String HOST = "appassets.androidplatform.net";
    private static final String START_URL = "https://" + HOST + "/assets/www/index.html";

    /** The activity while it is on screen; new mail then shows inside the app instead of as a notification. */
    static volatile MainActivity visible;

    private WebView web;
    private MailBridge bridge;
    private SharedPreferences prefs;
    private ValueCallback<Uri[]> fileCallback;
    private volatile String pendingLaunch = "";
    private boolean pageLoaded;

    private final ActivityResultLauncher<Intent> pickFiles = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), r -> {
                if (fileCallback == null) return;
                Uri[] result = null;
                Intent data = r.getData();
                if (r.getResultCode() == RESULT_OK && data != null) {
                    ClipData clip = data.getClipData();
                    if (clip != null) {
                        result = new Uri[clip.getItemCount()];
                        for (int i = 0; i < clip.getItemCount(); i++) result[i] = clip.getItemAt(i).getUri();
                    } else if (data.getData() != null) {
                        result = new Uri[]{data.getData()};
                    }
                }
                fileCallback.onReceiveValue(result);
                fileCallback = null;
            });

    private final ActivityResultLauncher<String> askNotify = registerForActivityResult(
            new ActivityResultContracts.RequestPermission(), granted -> {
                prefs.edit().putBoolean("askedNotify", true).apply();
                if (granted) askBatteryExemptionOnce();
                MailService.sync(this);
                notifyChanged();
            });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        MailApi.prepThread();
        prefs = getSharedPreferences("app", MODE_PRIVATE);
        Notifier.channels(this);

        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(night ? 0xFF12151A : 0xFFF4F2EE);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        // Draw edge to edge and keep the page clear of the status bar, navigation bar and keyboard
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        if (Build.VERSION.SDK_INT < 35) {
            getWindow().setStatusBarColor(Color.TRANSPARENT);
            getWindow().setNavigationBarColor(Color.TRANSPARENT);
        }
        WindowInsetsControllerCompat bars = WindowCompat.getInsetsController(getWindow(), root);
        bars.setAppearanceLightStatusBars(!night);
        bars.setAppearanceLightNavigationBars(!night);
        ViewCompat.setOnApplyWindowInsetsListener(root, (v, insets) -> {
            Insets i = insets.getInsets(WindowInsetsCompat.Type.systemBars()
                    | WindowInsetsCompat.Type.ime()
                    | WindowInsetsCompat.Type.displayCutout());
            v.setPadding(i.left, i.top, i.right, i.bottom);
            return WindowInsetsCompat.CONSUMED;
        });

        setupWebView();
        handleIntent(getIntent());

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (web == null) return;
                web.evaluateJavascript("(window.__back ? window.__back() : false)", v -> {
                    if (!"true".equals(v)) moveTaskToBack(true);
                });
            }
        });

        CheckWorker.schedule(this);
        MailService.sync(this);
    }

    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setSupportMultipleWindows(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE); // http images inside emails

        WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
                .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return loader.shouldInterceptRequest(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if (HOST.equals(u.getHost())) return false;
                openExternal(u); // links in emails open in the browser / dialer / mail app
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                pageLoaded = true;
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT)
                        .addCategory(Intent.CATEGORY_OPENABLE)
                        .setType("*/*");
                if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    pickFiles.launch(Intent.createChooser(pick, "Choose files"));
                    return true;
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    return false;
                }
            }
        });

        bridge = new MailBridge(this);
        web.addJavascriptInterface(bridge, "AndroidMail");
        web.loadUrl(START_URL);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    /** A tapped notification carries the account and message to open. */
    private void handleIntent(Intent intent) {
        if (intent == null || !intent.hasExtra("acc")) return;
        String acc = intent.getStringExtra("acc");
        long uid = intent.getLongExtra("uid", 0);
        intent.removeExtra("acc");
        if (pageLoaded) {
            js("window.openFromNotification && openFromNotification(" + JSONObject.quote(acc) + "," + uid + ")");
        } else {
            try {
                pendingLaunch = new JSONObject().put("account", acc).put("uid", uid).toString();
            } catch (Exception ignored) {
            }
        }
    }

    String takeLaunch() {
        String l = pendingLaunch;
        pendingLaunch = "";
        return l;
    }

    @Override
    protected void onResume() {
        super.onResume();
        visible = this;
        if (pageLoaded) {
            js("window.__resume && window.__resume()");
            notifyChanged();
        }
    }

    @Override
    protected void onPause() {
        if (visible == this) visible = null;
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (visible == this) visible = null;
        if (bridge != null) bridge.shutdown();
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    // ---------- Called by the bridge and the mail engine ----------
    void js(String code) {
        runOnUiThread(() -> {
            if (web != null) web.evaluateJavascript(code, null);
        });
    }

    void reply(String id, int status, String body) {
        js("window.__nativeReply(" + JSONObject.quote(id) + "," + status + "," + JSONObject.quote(body) + ")");
    }

    void dispatchMail(JSONObject ev) {
        js("window.__nativeEvent && window.__nativeEvent(" + ev + ")");
    }

    private void notifyChanged() {
        js("window.__notifyChanged && window.__notifyChanged()");
    }

    void afterLogin() {
        runOnUiThread(() -> {
            if ("granted".equals(notifyStatus())) askBatteryExemptionOnce();
        });
    }

    String notifyStatus() {
        if (NotificationManagerCompat.from(this).areNotificationsEnabled()) return "granted";
        if (Build.VERSION.SDK_INT >= 33 && !prefs.getBoolean("askedNotify", false)) return "default";
        return "denied";
    }

    void requestNotify() {
        runOnUiThread(() -> {
            boolean enabled = NotificationManagerCompat.from(this).areNotificationsEnabled();
            if (!enabled && Build.VERSION.SDK_INT >= 33
                    && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                    && (!prefs.getBoolean("askedNotify", false) || shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS))) {
                askNotify.launch(Manifest.permission.POST_NOTIFICATIONS);
                return;
            }
            if (!enabled) {
                // Permission was refused before: only the settings screen can turn it on now
                try {
                    startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
                } catch (ActivityNotFoundException ignored) {
                }
                return;
            }
            askBatteryExemptionOnce();
            MailService.sync(this);
            notifyChanged();
        });
    }

    /** Asks once to let the app run in the background, so Android doesn't cut the mail connection. */
    private void askBatteryExemptionOnce() {
        if (prefs.getBoolean("askedBattery", false)) return;
        PowerManager pm = getSystemService(PowerManager.class);
        if (pm == null || pm.isIgnoringBatteryOptimizations(getPackageName())) return;
        prefs.edit().putBoolean("askedBattery", true).apply();
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (Exception ignored) {
        }
    }

    void openFile(Uri uri, String mime) {
        runOnUiThread(() -> {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW)
                        .setDataAndType(uri, mime)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
            } catch (ActivityNotFoundException ignored) {
                // no app for this type; the file is still in Downloads/Mail
            }
        });
    }

    private void openExternal(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (ActivityNotFoundException ignored) {
        }
    }
}
