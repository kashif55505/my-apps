package com.personal.webmail;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.service.notification.StatusBarNotification;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import com.personal.webmail.AccountStore.Account;

import org.json.JSONObject;

/** System notifications: one per new email, plus the quiet "watching for mail" notice of the service. */
final class Notifier {

    static final String CH_MAIL = "new_mail";
    // Silent channel for the background service. Android requires a running service to have a
    // notification; with importance NONE it is never shown in the status bar or notification shade.
    static final String CH_SERVICE = "background_silent";
    static final int SERVICE_ID = 1;

    static void channels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return;
        NotificationChannel mail = new NotificationChannel(CH_MAIL, "New email", NotificationManager.IMPORTANCE_HIGH);
        mail.setDescription("A new email arrived in one of your accounts");
        nm.createNotificationChannel(mail);
        if (nm.getNotificationChannel(CH_SERVICE) == null) {
            NotificationChannel svc;
            try {
                svc = new NotificationChannel(CH_SERVICE, "Background sync", NotificationManager.IMPORTANCE_NONE);
            } catch (IllegalArgumentException e) {
                svc = new NotificationChannel(CH_SERVICE, "Background sync", NotificationManager.IMPORTANCE_MIN);
            }
            svc.setDescription("Keeps the app checking for new mail. Hidden by default.");
            svc.setShowBadge(false);
            nm.createNotificationChannel(svc);
        }
        try { nm.deleteNotificationChannel("watching"); } catch (Exception ignored) { } // older version's visible channel
    }

    static Notification service(Context c, int accounts) {
        channels(c);
        return new NotificationCompat.Builder(c, CH_SERVICE)
                .setSmallIcon(R.drawable.ic_stat_mail)
                .setContentTitle("Checking for new mail")
                .setContentText(accounts == 1 ? "1 account" : accounts + " accounts")
                .setPriority(NotificationCompat.PRIORITY_MIN)
                .setOngoing(true)
                .setSilent(true)
                .setShowWhen(false)
                .setContentIntent(open(c, null, 0))
                .build();
    }

    /** New email: shown inside the app if it is open, otherwise as a system notification. */
    static void newMail(Context c, Account a, long uid, String from, String subject) {
        MainActivity act = MainActivity.visible;
        if (act != null) {
            try {
                act.dispatchMail(new JSONObject()
                        .put("type", "mail").put("account", a.id()).put("uid", uid)
                        .put("from", from).put("subject", subject));
                return;
            } catch (Exception ignored) {
                // fall through to a normal notification
            }
        }
        channels(c);
        Notification n = new NotificationCompat.Builder(c, CH_MAIL)
                .setSmallIcon(R.drawable.ic_stat_mail)
                .setContentTitle(from)
                .setContentText(subject)
                .setSubText(a.email)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(subject))
                .setCategory(NotificationCompat.CATEGORY_EMAIL)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setGroup(a.id())
                .setContentIntent(open(c, a.id(), uid))
                .build();
        post(c, (a.id() + ":" + uid).hashCode(), n);
    }

    static void summary(Context c, Account a, int count) {
        MainActivity act = MainActivity.visible;
        if (act != null) {
            try {
                act.dispatchMail(new JSONObject().put("type", "mail").put("account", a.id()).put("uid", 0)
                        .put("from", a.email).put("subject", count + " new emails"));
                return;
            } catch (Exception ignored) {
            }
        }
        channels(c);
        Notification n = new NotificationCompat.Builder(c, CH_MAIL)
                .setSmallIcon(R.drawable.ic_stat_mail)
                .setContentTitle(count + " new emails")
                .setContentText(a.email)
                .setCategory(NotificationCompat.CATEGORY_EMAIL)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setGroup(a.id())
                .setContentIntent(open(c, a.id(), 0))
                .build();
        post(c, (a.id() + ":summary").hashCode(), n);
    }

    /** Removes the notifications of an account that was taken out of the app. */
    static void clearAccount(Context c, String id) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return;
        try {
            for (StatusBarNotification s : nm.getActiveNotifications()) {
                if (id.equals(s.getNotification().getGroup())) nm.cancel(s.getId());
            }
        } catch (Exception ignored) {
        }
    }

    static PendingIntent open(Context c, String account, long uid) {
        Intent i = new Intent(c, MainActivity.class)
                .setAction("open:" + account + ":" + uid)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (account != null) i.putExtra("acc", account).putExtra("uid", uid);
        int code = account == null ? 0 : (account + ":" + uid).hashCode();
        return PendingIntent.getActivity(c, code, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static void post(Context c, int id, Notification n) {
        NotificationManagerCompat nm = NotificationManagerCompat.from(c);
        if (!nm.areNotificationsEnabled()) return;
        try {
            nm.notify(id, n);
        } catch (SecurityException ignored) {
            // notification permission was withdrawn
        }
    }
}
