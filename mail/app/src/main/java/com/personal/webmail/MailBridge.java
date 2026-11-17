package com.personal.webmail;

import android.webkit.JavascriptInterface;

import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * What the interface (assets/www/app.js) can call as window.AndroidMail.
 * Requests run on background threads; answers go back through window.__nativeReply.
 */
public final class MailBridge {

    private final MainActivity activity;
    private final MailApi api;
    private final ExecutorService pool = Executors.newFixedThreadPool(4);

    MailBridge(MainActivity activity) {
        this.activity = activity;
        this.api = MailApi.get(activity.getApplicationContext());
    }

    @JavascriptInterface
    public void call(String id, String method, String url, String body) {
        pool.execute(() -> {
            MailApi.prepThread();
            int status = 200;
            String out;
            try {
                out = String.valueOf(api.handle(method, url, body));
                if (url.startsWith("/api/login")) activity.afterLogin();
            } catch (MailApi.ApiException e) {
                status = e.status;
                out = error(e.getMessage(), e.code);
            } catch (Throwable t) {
                status = 500;
                out = error(MailApi.describe(t), null);
            }
            activity.reply(id, status, out);
        });
    }

    private static String error(String message, String code) {
        try {
            JSONObject j = new JSONObject().put("error", message == null ? "Error" : message);
            if (code != null) j.put("code", code);
            return j.toString();
        } catch (Exception e) {
            return "{\"error\":\"Error\"}";
        }
    }

    @JavascriptInterface
    public String notifyStatus() {
        return activity.notifyStatus();
    }

    @JavascriptInterface
    public void requestNotify() {
        activity.requestNotify();
    }

    @JavascriptInterface
    public String takeLaunch() {
        return activity.takeLaunch();
    }

    void shutdown() {
        pool.shutdown();
    }
}
