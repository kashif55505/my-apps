package com.fbuploader.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/** Networking for Remote Control (desktop software) and Graph API. */
object Api {

    data class Account(val id: String, val name: String, val email: String, val page: String, val status: String)

    // ---------- Remote control (desktop HTTP server) ----------
    private suspend fun req(base: String, key: String, path: String, method: String, body: String?): JSONObject =
        withContext(Dispatchers.IO) {
            val url = URL(base.trimEnd('/') + path)
            val c = url.openConnection() as HttpURLConnection
            c.requestMethod = method
            c.connectTimeout = 8000
            c.readTimeout = 15000
            c.setRequestProperty("X-API-Key", key)
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = c.responseCode
            val text = (if (code in 200..299) c.inputStream else c.errorStream)
                ?.bufferedReader()?.use(BufferedReader::readText) ?: "{}"
            JSONObject(if (text.isBlank()) "{}" else text)
        }

    suspend fun ping(base: String, key: String): Boolean = try {
        req(base, key, "/ping", "GET", null).optString("status") == "ok"
    } catch (e: Exception) { false }

    suspend fun accounts(base: String, key: String): List<Account> {
        val arr = req(base, key, "/accounts", "GET", null).optJSONArray("accounts") ?: JSONArray()
        val out = ArrayList<Account>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out.add(Account(o.optString("id"), o.optString("name"), o.optString("email"),
                o.optString("page"), o.optString("status")))
        }
        return out
    }

    suspend fun startPost(base: String, key: String, ids: List<String>, kind: String, threads: Int): String {
        val b = JSONObject().put("ids", JSONArray(ids)).put("kind", kind).put("threads", threads)
        return req(base, key, "/post", "POST", b.toString()).toString()
    }

    suspend fun startActive(base: String, key: String, ids: List<String>, threads: Int): String {
        val b = JSONObject().put("ids", JSONArray(ids)).put("threads", threads)
        return req(base, key, "/active", "POST", b.toString()).toString()
    }

    suspend fun stop(base: String, key: String): String =
        req(base, key, "/stop", "POST", "{}").toString()

    suspend fun log(base: String, key: String): List<String> {
        val arr = req(base, key, "/log", "GET", null).optJSONArray("log") ?: JSONArray()
        val out = ArrayList<String>()
        for (i in 0 until arr.length()) out.add(arr.getString(i))
        return out
    }

    // ---------- Facebook Graph API (official) ----------
    suspend fun graphPostPhoto(pageId: String, token: String, imageUrl: String, caption: String): String =
        withContext(Dispatchers.IO) {
            val url = URL("https://graph.facebook.com/$pageId/photos")
            val body = "url=${enc(imageUrl)}&caption=${enc(caption)}&access_token=${enc(token)}"
            postForm(url, body)
        }

    suspend fun graphPostVideo(pageId: String, token: String, videoUrl: String, caption: String): String =
        withContext(Dispatchers.IO) {
            val url = URL("https://graph-video.facebook.com/$pageId/videos")
            val body = "file_url=${enc(videoUrl)}&description=${enc(caption)}&access_token=${enc(token)}"
            postForm(url, body)
        }

    private fun postForm(url: URL, body: String): String {
        val c = url.openConnection() as HttpURLConnection
        c.requestMethod = "POST"
        c.doOutput = true
        c.connectTimeout = 10000
        c.readTimeout = 60000
        c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
        c.outputStream.use { it.write(body.toByteArray()) }
        val code = c.responseCode
        return (if (code in 200..299) c.inputStream else c.errorStream)
            ?.bufferedReader()?.use(BufferedReader::readText) ?: ""
    }

    private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")
}
