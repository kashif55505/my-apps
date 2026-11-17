package com.personal.webmail;

import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.ServiceCompat;
import androidx.core.content.ContextCompat;

import com.personal.webmail.AccountStore.Account;
import com.sun.mail.imap.IMAPFolder;
import com.sun.mail.imap.IMAPStore;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import javax.mail.Folder;
import javax.mail.event.MessageCountAdapter;
import javax.mail.event.MessageCountEvent;

/**
 * Runs while at least one account has notifications on. Keeps one IMAP IDLE connection per account,
 * so the mail server tells the phone about new mail the moment it arrives.
 */
public class MailService extends Service {

    /** Starts or stops the service to match the accounts' notification settings. */
    /** True between asking Android to start the service and the service calling startForeground. */
    private static volatile boolean starting;

    static void sync(Context ctx) {
        Context app = ctx.getApplicationContext();
        boolean needed = false;
        for (Account a : AccountStore.get(app).all()) needed |= a.notify;
        Intent i = new Intent(app, MailService.class);
        if (needed) {
            try {
                starting = true;
                ContextCompat.startForegroundService(app, i);
            } catch (Exception e) {
                starting = false;
                // Android may refuse to start it from the background; the 15-minute check still runs
            }
        } else if (!starting) {
            app.stopService(i);
        }
        // else: a start is pending; onStartCommand sees no accounts need it and stops itself safely
    }

    private final Map<String, Watcher> watchers = new HashMap<>();
    private final ScheduledExecutorService timer = Executors.newScheduledThreadPool(1);
    private final ExecutorService checks = Executors.newCachedThreadPool();

    @Override
    public void onCreate() {
        super.onCreate();
        Notifier.channels(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        List<Account> list = AccountStore.get(this).all();
        int n = 0;
        for (Account a : list) if (a.notify) n++;
        try {
            ServiceCompat.startForeground(this, Notifier.SERVICE_ID, Notifier.service(this, Math.max(1, n)),
                    Build.VERSION.SDK_INT >= 34 ? ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE : 0);
        } catch (Exception e) {
            starting = false;
            stopSelf();
            return START_NOT_STICKY;
        }
        starting = false;
        if (n == 0) {
            stopSelf();
            return START_NOT_STICKY;
        }
        refresh(list);
        return START_STICKY;
    }

    /** Starts watchers for new/changed accounts and stops those no longer needed. */
    private synchronized void refresh(List<Account> list) {
        Set<String> wanted = new HashSet<>();
        for (Account a : list) {
            if (!a.notify) continue;
            wanted.add(a.id());
            Watcher w = watchers.get(a.id());
            if (w != null && w.sameSettings(a)) continue;
            if (w != null) w.shutdown();
            w = new Watcher(a);
            watchers.put(a.id(), w);
            w.start();
        }
        for (Iterator<Map.Entry<String, Watcher>> it = watchers.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<String, Watcher> e = it.next();
            if (!wanted.contains(e.getKey())) {
                e.getValue().shutdown();
                it.remove();
            }
        }
    }

    @Override
    public synchronized void onDestroy() {
        for (Watcher w : watchers.values()) w.shutdown();
        watchers.clear();
        timer.shutdownNow();
        checks.shutdownNow();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private final class Watcher extends Thread {
        private final Account a;
        private volatile boolean stopped;
        private volatile IMAPStore store;

        Watcher(Account a) {
            super("idle-" + a.email);
            this.a = a;
            setDaemon(true);
        }

        boolean sameSettings(Account b) {
            return a.imapHost.equals(b.imapHost) && a.imapPort == b.imapPort && a.user.equals(b.user)
                    && a.pass.equals(b.pass) && a.insecure == b.insecure;
        }

        @Override
        public void run() {
            MailApi.prepThread();
            long delay = 5_000;
            while (!stopped) {
                try {
                    // Read timeout longer than the 9-minute NOOP, so a quiet IDLE isn't mistaken for a dead link
                    store = MailApi.connect(a, 12 * 60_000);
                    final IMAPFolder inbox = (IMAPFolder) store.getFolder("INBOX");
                    inbox.open(Folder.READ_ONLY);
                    MailCheck.check(MailService.this, a, inbox);
                    inbox.addMessageCountListener(new MessageCountAdapter() {
                        @Override
                        public void messagesAdded(MessageCountEvent e) {
                            checks.execute(() -> {
                                MailApi.prepThread();
                                try { MailCheck.check(MailService.this, a, inbox); } catch (Exception ignored) { }
                            });
                        }
                    });
                    delay = 5_000;
                    boolean idle = store.hasCapability("IDLE");
                    while (!stopped && inbox.isOpen()) {
                        if (idle) {
                            // Break IDLE every 9 minutes with a NOOP so routers don't drop the quiet connection
                            ScheduledFuture<?> kick = timer.schedule(() -> {
                                try { inbox.doCommand(p -> { p.noop(); return null; }); } catch (Exception ignored) { }
                            }, 9, TimeUnit.MINUTES);
                            try {
                                inbox.idle();
                            } finally {
                                kick.cancel(false);
                            }
                        } else {
                            Thread.sleep(2 * 60_000);
                            MailCheck.check(MailService.this, a, inbox);
                        }
                    }
                } catch (InterruptedException e) {
                    break;
                } catch (Exception e) {
                    // network change, server restart...: reconnect below
                } finally {
                    closeStore();
                }
                if (stopped) break;
                try {
                    Thread.sleep(delay);
                } catch (InterruptedException e) {
                    break;
                }
                delay = Math.min(delay * 2, 5 * 60_000);
            }
        }

        private void closeStore() {
            IMAPStore s = store;
            store = null;
            if (s != null) try { s.close(); } catch (Exception ignored) { }
        }

        void shutdown() {
            stopped = true;
            interrupt();
            new Thread(this::closeStore).start(); // closing the socket ends a blocking IDLE
        }
    }
}
