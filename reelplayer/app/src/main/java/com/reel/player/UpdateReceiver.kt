package com.reel.player

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.widget.Toast
import androidx.core.app.NotificationCompat

/**
 * Hears back from the app's own update install, and tells the user after the update
 * (the app is closed while it updates, so a notification brings them back).
 */
class UpdateReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_STATUS = "com.reel.player.UPDATE_STATUS"
        private const val CHANNEL = "app_update"
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.action) {
            ACTION_STATUS -> when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    // First update this way (or an older Android): Android asks once.
                    @Suppress("DEPRECATION")
                    val confirm = if (Build.VERSION.SDK_INT >= 33)
                        intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                    else intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                    confirm?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)?.let {
                        try { ctx.startActivity(it) } catch (_: Exception) { notify(ctx, "Tap to finish the update", it) }
                    }
                }
                PackageInstaller.STATUS_SUCCESS -> {}
                PackageInstaller.STATUS_FAILURE_ABORTED -> {}
                else -> Toast.makeText(ctx, "Update failed: " +
                    (intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "unknown error"), Toast.LENGTH_LONG).show()
            }
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                val open = Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                notify(ctx, "Reel Player updated to ${AppUpdater.installedName(ctx)} · tap to open", open)
            }
        }
    }

    private fun notify(ctx: Context, text: String, open: Intent) {
        try {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (Build.VERSION.SDK_INT >= 26) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL, "App updates", NotificationManager.IMPORTANCE_HIGH))
            }
            val pi = PendingIntent.getActivity(ctx, 1, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            nm.notify(4301, NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_download)
                .setContentTitle("Reel Player")
                .setContentText(text)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(pi)
                .build())
        } catch (_: Exception) {}
    }
}
