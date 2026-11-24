package com.fbuploader.app

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.fbuploader.app.databinding.FragmentSettingsBinding
import kotlinx.coroutines.launch

class SettingsFragment : Fragment() {
    private var _b: FragmentSettingsBinding? = null
    private val b get() = _b!!
    private lateinit var prefs: Prefs

    override fun onCreateView(i: LayoutInflater, c: ViewGroup?, s: Bundle?): View {
        _b = FragmentSettingsBinding.inflate(i, c, false)
        prefs = Prefs(requireContext())
        return b.root
    }

    override fun onViewCreated(v: View, s: Bundle?) {
        b.edServer.setText(prefs.serverUrl)
        b.edKey.setText(prefs.apiKey)
        b.btnSaveSettings.setOnClickListener {
            prefs.serverUrl = b.edServer.text.toString().trim()
            prefs.apiKey = b.edKey.text.toString().trim()
            Toast.makeText(requireContext(), "Saved", Toast.LENGTH_SHORT).show()
            ping()
        }
    }

    private fun ping() {
        lifecycleScope.launch {
            b.tvPing.text = "Checking..."
            try {
                val ok = Api.ping(prefs.serverUrl, prefs.apiKey)
                b.tvPing.text = if (ok) "Connected to PC ✓" else "No response from PC"
            } catch (e: Exception) {
                b.tvPing.text = "Error: ${e.message}"
            }
        }
    }

    override fun onDestroyView() { super.onDestroyView(); _b = null }
}
