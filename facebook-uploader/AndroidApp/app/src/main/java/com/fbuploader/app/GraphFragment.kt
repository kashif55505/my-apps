package com.fbuploader.app

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.fbuploader.app.databinding.FragmentGraphBinding
import kotlinx.coroutines.launch

class GraphFragment : Fragment() {
    private var _b: FragmentGraphBinding? = null
    private val b get() = _b!!
    private lateinit var prefs: Prefs

    override fun onCreateView(i: LayoutInflater, c: ViewGroup?, s: Bundle?): View {
        _b = FragmentGraphBinding.inflate(i, c, false)
        prefs = Prefs(requireContext())
        return b.root
    }

    override fun onViewCreated(v: View, s: Bundle?) {
        b.edPageId.setText(prefs.graphPageId)
        b.edToken.setText(prefs.graphToken)
        b.btnGraphPost.setOnClickListener { post() }
    }

    private fun post() {
        val pageId = b.edPageId.text.toString().trim()
        val token = b.edToken.text.toString().trim()
        val url = b.edMediaUrl.text.toString().trim()
        val cap = b.edCaption.text.toString()
        if (pageId.isEmpty() || token.isEmpty() || url.isEmpty()) {
            Toast.makeText(requireContext(), "Fill Page ID, token and media URL", Toast.LENGTH_SHORT).show()
            return
        }
        prefs.graphPageId = pageId
        prefs.graphToken = token
        b.tvGraphResult.text = "Posting..."
        lifecycleScope.launch {
            try {
                val res = if (b.rbVideo.isChecked)
                    Api.graphPostVideo(pageId, token, url, cap)
                else
                    Api.graphPostPhoto(pageId, token, url, cap)
                b.tvGraphResult.text = res
            } catch (e: Exception) {
                b.tvGraphResult.text = "Error: ${e.message}"
            }
        }
    }

    override fun onDestroyView() { super.onDestroyView(); _b = null }
}
