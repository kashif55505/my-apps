package com.fbuploader.app

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import androidx.viewpager2.adapter.FragmentStateAdapter
import androidx.viewpager2.widget.ViewPager2
import com.fbuploader.app.databinding.ActivityMainBinding
import com.google.android.material.tabs.TabLayout
import com.google.android.material.tabs.TabLayoutMediator

class MainActivity : AppCompatActivity() {
    private lateinit var b: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        val titles = listOf("Remote", "Graph API", "Settings")
        b.pager.adapter = object : FragmentStateAdapter(this) {
            override fun getItemCount() = 3
            override fun createFragment(position: Int): Fragment = when (position) {
                0 -> RemoteFragment()
                1 -> GraphFragment()
                else -> SettingsFragment()
            }
        }
        TabLayoutMediator(b.tabs, b.pager) { tab: TabLayout.Tab, pos: Int ->
            tab.text = titles[pos]
        }.attach()
        b.pager.offscreenPageLimit = 2
    }
}
