package com.fbuploader.app

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.fbuploader.app.databinding.FragmentRemoteBinding
import kotlinx.coroutines.launch

class RemoteFragment : Fragment() {
    private var _b: FragmentRemoteBinding? = null
    private val b get() = _b!!
    private lateinit var prefs: Prefs
    private var accounts: List<Api.Account> = emptyList()
    private val checked = HashSet<String>()

    override fun onCreateView(i: LayoutInflater, c: ViewGroup?, s: Bundle?): View {
        _b = FragmentRemoteBinding.inflate(i, c, false)
        prefs = Prefs(requireContext())
        return b.root
    }

    override fun onViewCreated(v: View, s: Bundle?) {
        b.spKind.adapter = ArrayAdapter(requireContext(),
            android.R.layout.simple_spinner_dropdown_item, listOf("reel", "video", "photo"))

        b.btnConnect.setOnClickListener { refresh() }
        b.btnSelAll.setOnClickListener { checked.addAll(accounts.map { it.id }); render() }
        b.btnSelNone.setOnClickListener { checked.clear(); render() }
        b.btnPost.setOnClickListener { startPost() }
        b.btnActive.setOnClickListener { startActive() }
        b.btnStop.setOnClickListener { doStop() }
        refresh()
    }

    private fun base() = prefs.serverUrl
    private fun key() = prefs.apiKey

    private fun refresh() {
        if (base().isBlank() || key().isBlank()) {
            b.lblStatus.text = "Set Server URL + API key in Settings"
            return
        }
        lifecycleScope.launch {
            try {
                val ok = Api.ping(base(), key())
                b.lblStatus.text = if (ok) "Connected" else "No response"
                if (ok) {
                    accounts = Api.accounts(base(), key())
                    render()
                    pullLog()
                }
            } catch (e: Exception) {
                b.lblStatus.text = "Error: ${e.message}"
            }
        }
    }

    private fun render() {
        val items = accounts.map {
            val mark = if (checked.contains(it.id)) "☑" else "☐"
            "$mark  ${it.name}  (${it.page})  ${it.status}"
        }
        b.lvAccounts.adapter = ArrayAdapter(requireContext(),
            android.R.layout.simple_list_item_1, items)
        b.lvAccounts.setOnItemClickListener { _, _, pos, _ ->
            val id = accounts[pos].id
            if (checked.contains(id)) checked.remove(id) else checked.add(id)
            render()
        }
    }

    private fun selectedIds() = accounts.map { it.id }.filter { checked.contains(it) }

    private fun threads() = b.edThreads.text.toString().toIntOrNull() ?: 5

    private fun startPost() {
        val ids = selectedIds()
        if (ids.isEmpty()) { toast("Select IDs"); return }
        lifecycleScope.launch {
            try { toast(Api.startPost(base(), key(), ids, b.spKind.selectedItem.toString(), threads())); pullLog() }
            catch (e: Exception) { toast("Error: ${e.message}") }
        }
    }

    private fun startActive() {
        val ids = selectedIds()
        if (ids.isEmpty()) { toast("Select IDs"); return }
        lifecycleScope.launch {
            try { toast(Api.startActive(base(), key(), ids, threads())); pullLog() }
            catch (e: Exception) { toast("Error: ${e.message}") }
        }
    }

    private fun doStop() {
        lifecycleScope.launch {
            try { toast(Api.stop(base(), key())); pullLog() }
            catch (e: Exception) { toast("Error: ${e.message}") }
        }
    }

    private fun pullLog() {
        lifecycleScope.launch {
            try {
                val lines = Api.log(base(), key())
                b.tvLog.text = lines.joinToString("\n")
            } catch (_: Exception) {}
        }
    }

    private fun toast(m: String) = Toast.makeText(requireContext(), m, Toast.LENGTH_SHORT).show()

    override fun onDestroyView() { super.onDestroyView(); _b = null }
}
