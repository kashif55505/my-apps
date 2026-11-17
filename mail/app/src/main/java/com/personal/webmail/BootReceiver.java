package com.personal.webmail;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Restarts mail watching after the phone reboots or the app is updated. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            CheckWorker.schedule(context);
            MailService.sync(context);
        }
    }
}
