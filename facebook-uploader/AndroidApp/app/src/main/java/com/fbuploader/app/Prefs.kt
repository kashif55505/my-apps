package com.fbuploader.app

import android.content.Context

/** Simple saved settings. */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("fbu", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("serverUrl", "http://192.168.1.10:8700") ?: ""
        set(v) { sp.edit().putString("serverUrl", v).apply() }

    var apiKey: String
        get() = sp.getString("apiKey", "") ?: ""
        set(v) { sp.edit().putString("apiKey", v).apply() }

    var graphToken: String
        get() = sp.getString("graphToken", "") ?: ""
        set(v) { sp.edit().putString("graphToken", v).apply() }

    var graphPageId: String
        get() = sp.getString("graphPageId", "") ?: ""
        set(v) { sp.edit().putString("graphPageId", v).apply() }
}
