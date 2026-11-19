package com.reel.player.yt

import org.schabi.newpipe.extractor.downloader.Downloader
import org.schabi.newpipe.extractor.downloader.Request
import org.schabi.newpipe.extractor.downloader.Response
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException
import java.net.HttpURLConnection
import java.net.URL

/** Network access for NewPipeExtractor, using plain HttpURLConnection. */
object NpDownloader : Downloader() {

    const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"

    override fun execute(request: Request): Response {
        val conn = URL(request.url()).openConnection() as HttpURLConnection
        conn.connectTimeout = 15000
        conn.readTimeout = 20000
        conn.requestMethod = request.httpMethod()
        conn.setRequestProperty("User-Agent", USER_AGENT)
        for ((name, values) in request.headers()) {
            if (values.isEmpty()) continue
            conn.setRequestProperty(name, values.joinToString(","))
        }
        val body = request.dataToSend()
        if (body != null) {
            conn.doOutput = true
            conn.setFixedLengthStreamingMode(body.size)
            conn.outputStream.use { it.write(body) }
        }
        val code = conn.responseCode
        if (code == 429) {
            conn.disconnect()
            throw ReCaptchaException("reCaptcha Challenge requested", request.url())
        }
        val stream = if (code >= 400) conn.errorStream else conn.inputStream
        val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
        val headers = conn.headerFields.filterKeys { it != null }
        return Response(code, conn.responseMessage, headers, text, conn.url.toString())
    }
}
