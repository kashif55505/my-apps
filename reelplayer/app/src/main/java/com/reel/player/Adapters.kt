package com.reel.player

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide

/** Which items are ticked. Long-press an item to start selecting. */
class Selection(private val onChanged: () -> Unit) {
    val keys = LinkedHashSet<String>()
    var active = false
        private set

    fun start(key: String) {
        active = true
        keys += key
        onChanged()
    }

    fun toggle(key: String) {
        if (!keys.remove(key)) keys += key
        onChanged()
    }

    fun setAll(all: Collection<String>) {
        keys.clear()
        keys += all
        onChanged()
    }

    fun clear() {
        if (!active && keys.isEmpty()) return
        active = false
        keys.clear()
        onChanged()
    }
}

class FolderAdapter(
    private val items: List<Folder>,
    private val selection: Selection,
    private val onClick: (Folder) -> Unit,
    private val onMore: (Folder, View) -> Unit
) : RecyclerView.Adapter<FolderAdapter.VH>() {

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val check: CheckBox = v.findViewById(R.id.check)
        val title: TextView = v.findViewById(R.id.title)
        val meta: TextView = v.findViewById(R.id.meta)
        val tag: TextView = v.findViewById(R.id.tag)
        val more: View = v.findViewById(R.id.more)
    }

    val keys: List<String> get() = items.map { it.id }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_folder, parent, false))

    override fun getItemCount() = items.size

    override fun onBindViewHolder(h: VH, position: Int) {
        val f = items[position]
        val ctx = h.itemView.context
        val n = f.videos.size
        h.title.text = f.name
        h.meta.text = "$n video${if (n == 1) "" else "s"} · ${fmtSize(f.totalSize)}"
        val fresh = f.videos.count { Prefs.getPos(ctx, it.uri.toString()) < 0 }
        h.tag.visibility = if (fresh > 0) View.VISIBLE else View.GONE
        h.tag.text = "$fresh NEW"

        h.check.visibility = if (selection.active) View.VISIBLE else View.GONE
        h.check.isChecked = f.id in selection.keys
        h.more.visibility = if (selection.active) View.GONE else View.VISIBLE
        h.more.setOnClickListener { onMore(f, it) }
        h.itemView.setOnClickListener { if (selection.active) selection.toggle(f.id) else onClick(f) }
        h.itemView.setOnLongClickListener {
            if (selection.active) selection.toggle(f.id) else selection.start(f.id)
            true
        }
    }
}

class VideoAdapter(
    private val items: List<Video>,
    private val selection: Selection,
    private val onClick: (Int) -> Unit,
    private val onMore: (Video, View) -> Unit
) : RecyclerView.Adapter<VideoAdapter.VH>() {

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val check: CheckBox = v.findViewById(R.id.check)
        val frame: FrameLayout = v.findViewById(R.id.thumbFrame)
        val thumb: ImageView = v.findViewById(R.id.thumb)
        val dur: TextView = v.findViewById(R.id.dur)
        val progress: ProgressBar = v.findViewById(R.id.progress)
        val title: TextView = v.findViewById(R.id.title)
        val meta: TextView = v.findViewById(R.id.meta)
        val tag: TextView = v.findViewById(R.id.tag)
        val more: View = v.findViewById(R.id.more)
    }

    val keys: List<String> get() = items.map { it.uri.toString() }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val h = VH(LayoutInflater.from(parent.context).inflate(R.layout.item_video, parent, false))
        h.frame.clipToOutline = true
        return h
    }

    override fun getItemCount() = items.size

    override fun onBindViewHolder(h: VH, position: Int) {
        val v = items[position]
        val ctx = h.itemView.context
        val key = v.uri.toString()
        h.title.text = v.name
        h.meta.text = fmtSize(v.size)
        h.dur.text = fmtTime(v.duration)
        h.dur.visibility = if (v.duration > 0) View.VISIBLE else View.GONE

        val pos = Prefs.getPos(ctx, key)
        h.tag.visibility = if (pos < 0) View.VISIBLE else View.GONE
        if (pos > 0 && v.duration > 0) {
            h.progress.visibility = View.VISIBLE
            h.progress.progress = (pos * 1000 / v.duration).toInt().coerceIn(0, 1000)
        } else {
            h.progress.visibility = View.GONE
        }

        Glide.with(h.thumb)
            .load(v.uri)
            .frame(2_000_000L)
            .centerCrop()
            .into(h.thumb)

        h.check.visibility = if (selection.active) View.VISIBLE else View.GONE
        h.check.isChecked = key in selection.keys
        h.more.visibility = if (selection.active) View.GONE else View.VISIBLE
        h.more.setOnClickListener { onMore(v, it) }
        h.itemView.setOnClickListener {
            if (selection.active) selection.toggle(key) else onClick(h.bindingAdapterPosition)
        }
        h.itemView.setOnLongClickListener {
            if (selection.active) selection.toggle(key) else selection.start(key)
            true
        }
    }
}
