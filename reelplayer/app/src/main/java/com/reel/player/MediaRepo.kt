package com.reel.player

import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import java.io.File
import java.util.Locale

data class Video(
    val uri: Uri,
    val name: String,
    val size: Long,
    val duration: Long,
    val dateAdded: Long,
    val folderId: String,
    val folderName: String,
    val path: String?
)

data class Folder(val id: String, val name: String, val path: String?, val videos: List<Video>) {
    val totalSize: Long get() = videos.sumOf { it.size }
    val newest: Long get() = videos.maxOfOrNull { it.dateAdded } ?: 0L
}

/** Reads every video on the device from MediaStore and groups them by folder, like MX Player. */
object MediaRepo {

    fun load(ctx: Context): List<Folder> {
        val collection: Uri = if (Build.VERSION.SDK_INT >= 29) {
            MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
        } else {
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI
        }
        val projection = arrayOf(
            MediaStore.Video.Media._ID,
            MediaStore.Video.Media.DISPLAY_NAME,
            MediaStore.Video.Media.SIZE,
            MediaStore.Video.Media.DURATION,
            MediaStore.Video.Media.DATE_ADDED,
            MediaStore.Video.Media.BUCKET_ID,
            MediaStore.Video.Media.BUCKET_DISPLAY_NAME,
            MediaStore.Video.Media.DATA
        )
        val videos = ArrayList<Video>()
        ctx.contentResolver.query(collection, projection, null, null, null)?.use { c ->
            val iId = c.getColumnIndexOrThrow(MediaStore.Video.Media._ID)
            val iName = c.getColumnIndexOrThrow(MediaStore.Video.Media.DISPLAY_NAME)
            val iSize = c.getColumnIndexOrThrow(MediaStore.Video.Media.SIZE)
            val iDur = c.getColumnIndexOrThrow(MediaStore.Video.Media.DURATION)
            val iDate = c.getColumnIndexOrThrow(MediaStore.Video.Media.DATE_ADDED)
            val iBucket = c.getColumnIndexOrThrow(MediaStore.Video.Media.BUCKET_ID)
            val iBucketName = c.getColumnIndexOrThrow(MediaStore.Video.Media.BUCKET_DISPLAY_NAME)
            val iData = c.getColumnIndexOrThrow(MediaStore.Video.Media.DATA)
            while (c.moveToNext()) {
                val id = c.getLong(iId)
                val path = c.getString(iData)
                videos += Video(
                    uri = ContentUris.withAppendedId(collection, id),
                    name = c.getString(iName) ?: path?.let { File(it).name } ?: "Video",
                    size = c.getLong(iSize),
                    duration = c.getLong(iDur),
                    dateAdded = c.getLong(iDate),
                    folderId = c.getString(iBucket) ?: "0",
                    folderName = c.getString(iBucketName) ?: "Internal storage",
                    path = path
                )
            }
        }
        return videos.groupBy { it.folderId }.map { (id, list) ->
            Folder(id, list.first().folderName, list.first().path?.let { File(it).parent }, list)
        }
    }

    fun sortFolders(list: List<Folder>, sort: Int): List<Folder> = when (sort) {
        1 -> list.sortedByDescending { it.newest }
        2 -> list.sortedByDescending { it.totalSize }
        else -> list.sortedBy { it.name.lowercase(Locale.ROOT) }
    }

    fun sortVideos(list: List<Video>, sort: Int): List<Video> = when (sort) {
        1 -> list.sortedByDescending { it.dateAdded }
        2 -> list.sortedByDescending { it.size }
        else -> list.sortedWith(NaturalOrder)
    }

    /** "Episode 2" before "Episode 10". */
    private object NaturalOrder : Comparator<Video> {
        private val chunk = Regex("\\d+|\\D+")
        override fun compare(a: Video, b: Video): Int {
            val x = chunk.findAll(a.name.lowercase(Locale.ROOT)).map { it.value }.toList()
            val y = chunk.findAll(b.name.lowercase(Locale.ROOT)).map { it.value }.toList()
            for (i in 0 until minOf(x.size, y.size)) {
                val p = x[i]; val q = y[i]
                val r = if (p[0].isDigit() && q[0].isDigit()) {
                    val n = p.trimStart('0'); val m = q.trimStart('0')
                    if (n.length != m.length) n.length - m.length else n.compareTo(m)
                } else p.compareTo(q)
                if (r != 0) return r
            }
            return x.size - y.size
        }
    }
}
