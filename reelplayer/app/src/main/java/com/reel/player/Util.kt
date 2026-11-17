package com.reel.player

import android.content.Context
import java.util.Locale

object Prefs {
    private fun sp(c: Context) = c.getSharedPreferences("reel", Context.MODE_PRIVATE)

    /** Saved playback position in ms, or -1 if the video was never opened. */
    fun getPos(c: Context, key: String): Long = sp(c).getLong("pos:$key", -1L)
    fun setPos(c: Context, key: String, pos: Long) = sp(c).edit().putLong("pos:$key", pos).apply()

    fun getInt(c: Context, key: String, def: Int): Int = sp(c).getInt(key, def)
    fun setInt(c: Context, key: String, v: Int) = sp(c).edit().putInt(key, v).apply()
}

fun fmtTime(ms: Long): String {
    val s = (if (ms < 0) 0 else ms) / 1000
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    return if (h > 0) String.format(Locale.US, "%d:%02d:%02d", h, m, sec)
    else String.format(Locale.US, "%d:%02d", m, sec)
}

fun fmtSize(b: Long): String = when {
    b >= 1_000_000_000L -> String.format(Locale.US, "%.2f GB", b / 1e9)
    b >= 1_000_000L -> String.format(Locale.US, "%.1f MB", b / 1e6)
    b > 0 -> String.format(Locale.US, "%d KB", b / 1000)
    else -> ""
}
