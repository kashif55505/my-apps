package com.personal.webmail;

import android.content.Context;
import android.content.SharedPreferences;

import com.personal.webmail.AccountStore.Account;
import com.sun.mail.imap.IMAPFolder;
import com.sun.mail.imap.IMAPStore;

import java.util.ArrayList;
import java.util.List;

import javax.mail.Address;
import javax.mail.FetchProfile;
import javax.mail.Flags;
import javax.mail.Folder;
import javax.mail.Message;
import javax.mail.MessagingException;
import javax.mail.UIDFolder;
import javax.mail.internet.InternetAddress;

/**
 * Finds INBOX messages that arrived since the last check and announces them.
 * Remembers the newest UID per account, so each email is announced only once
 * (whether the live service or the 15-minute backup check sees it first).
 */
final class MailCheck {

    private static final Object LOCK = new Object();
    private static final int MAX_SINGLE = 5; // more than this at once: one summary notification

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("mailstate", Context.MODE_PRIVATE);
    }

    static void reset(Context c, String id) {
        prefs(c).edit().remove(id + ".last").remove(id + ".validity").apply();
    }

    static void resetAll(Context c) {
        prefs(c).edit().clear().apply();
    }

    /** One pass over every account with notifications on (used by the backup worker). */
    static void checkAll(Context c) {
        MailApi.prepThread();
        for (Account a : AccountStore.get(c).all()) {
            if (!a.notify) continue;
            IMAPStore s = null;
            try {
                s = MailApi.connect(a);
                IMAPFolder inbox = (IMAPFolder) s.getFolder("INBOX");
                inbox.open(Folder.READ_ONLY);
                try {
                    check(c, a, inbox);
                } finally {
                    try { inbox.close(false); } catch (Exception ignored) { }
                }
            } catch (Exception ignored) {
                // offline or server down: next run will catch up
            } finally {
                if (s != null) try { s.close(); } catch (Exception ignored) { }
            }
        }
    }

    /** Checks an already open INBOX. */
    static void check(Context c, Account a, IMAPFolder inbox) throws MessagingException {
        synchronized (LOCK) {
            Account cur = AccountStore.get(c).find(a.id());
            if (cur == null) return; // removed meanwhile
            SharedPreferences sp = prefs(c);
            String k = cur.id();
            long validity = inbox.getUIDValidity();
            long last = sp.getLong(k + ".last", -1);
            if (last < 0 || sp.getLong(k + ".validity", -1) != validity) {
                // First look at this mailbox: remember where we are, announce nothing old
                sp.edit().putLong(k + ".validity", validity).putLong(k + ".last", newestUid(inbox)).apply();
                return;
            }
            Message[] msgs = inbox.getMessagesByUID(last + 1, UIDFolder.LASTUID);
            if (msgs == null || msgs.length == 0) return;
            FetchProfile fp = new FetchProfile();
            fp.add(FetchProfile.Item.ENVELOPE);
            fp.add(FetchProfile.Item.FLAGS);
            fp.add(UIDFolder.FetchProfileItem.UID);
            inbox.fetch(msgs, fp);

            long max = last;
            List<Message> fresh = new ArrayList<>();
            for (Message m : msgs) {
                if (m == null) continue;
                long uid = inbox.getUID(m);
                if (uid <= last) continue; // "N:*" always returns the newest message
                max = Math.max(max, uid);
                if (!m.getFlags().contains(Flags.Flag.SEEN)) fresh.add(m);
            }
            if (max > last) sp.edit().putLong(k + ".last", max).apply();
            if (!cur.notify || fresh.isEmpty()) return;

            if (fresh.size() > MAX_SINGLE) {
                Notifier.summary(c, cur, fresh.size());
                return;
            }
            for (Message m : fresh) {
                String subject;
                try { subject = m.getSubject(); } catch (MessagingException e) { subject = null; }
                if (subject == null || subject.trim().isEmpty()) subject = "(no subject)";
                Notifier.newMail(c, cur, inbox.getUID(m), sender(m), subject);
            }
        }
    }

    private static String sender(Message m) {
        try {
            Address[] from = m.getFrom();
            if (from != null && from.length > 0) {
                if (from[0] instanceof InternetAddress) {
                    InternetAddress ia = (InternetAddress) from[0];
                    if (ia.getPersonal() != null && !ia.getPersonal().trim().isEmpty()) return ia.getPersonal();
                    return ia.getAddress();
                }
                return from[0].toString();
            }
        } catch (MessagingException ignored) {
        }
        return "New email";
    }

    private static long newestUid(IMAPFolder f) throws MessagingException {
        long next = f.getUIDNext();
        if (next > 0) return next - 1;
        int n = f.getMessageCount();
        return n > 0 ? f.getUID(f.getMessage(n)) : 0;
    }
}
