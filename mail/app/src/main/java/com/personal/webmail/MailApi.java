package com.personal.webmail;

import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;

import androidx.core.content.FileProvider;

import com.personal.webmail.AccountStore.Account;
import com.sun.mail.imap.AppendUID;
import com.sun.mail.imap.IMAPFolder;
import com.sun.mail.imap.IMAPStore;

import org.json.JSONArray;
import org.json.JSONObject;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.safety.Cleaner;
import org.jsoup.safety.Safelist;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.security.cert.CertificateException;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.TimeZone;
import java.util.regex.Pattern;

import javax.activation.CommandMap;
import javax.activation.DataHandler;
import javax.activation.MailcapCommandMap;
import javax.mail.Address;
import javax.mail.AuthenticationFailedException;
import javax.mail.BodyPart;
import javax.mail.FetchProfile;
import javax.mail.Flags;
import javax.mail.Folder;
import javax.mail.FolderClosedException;
import javax.mail.Message;
import javax.mail.MessagingException;
import javax.mail.Multipart;
import javax.mail.Part;
import javax.mail.Session;
import javax.mail.StoreClosedException;
import javax.mail.Transport;
import javax.mail.UIDFolder;
import javax.mail.internet.InternetAddress;
import javax.mail.internet.MimeBodyPart;
import javax.mail.internet.MimeMessage;
import javax.mail.internet.MimeMultipart;
import javax.mail.internet.MimePart;
import javax.mail.internet.MimeUtility;
import javax.mail.search.BodyTerm;
import javax.mail.search.FromStringTerm;
import javax.mail.search.OrTerm;
import javax.mail.search.RecipientStringTerm;
import javax.mail.search.SearchTerm;
import javax.mail.search.SubjectTerm;
import javax.mail.util.ByteArrayDataSource;
import javax.net.ssl.SSLException;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.SSLPeerUnverifiedException;

/**
 * The phone's own mail engine. It answers the same requests (/api/...) that the web server version
 * answers, so the interface code is shared. Talks to IMAP/SMTP directly; no server in between.
 */
final class MailApi {

    static final class ApiException extends Exception {
        final int status;
        final String code;

        ApiException(int status, String message) {
            this(status, message, null);
        }

        ApiException(int status, String message, String code) {
            super(message);
            this.status = status;
            this.code = code;
        }
    }

    private interface StoreOp<T> {
        T run(IMAPStore s) throws Exception;
    }

    static {
        System.setProperty("mail.mime.decodefilename", "true");
        System.setProperty("mail.mime.encodefilename", "true");
        System.setProperty("mail.mime.decodeparameters", "true");
        System.setProperty("mail.mime.encodeparameters", "true");
        // Android does not always find Jakarta Mail's content handlers on its own
        MailcapCommandMap mc = (MailcapCommandMap) CommandMap.getDefaultCommandMap();
        mc.addMailcap("text/html;; x-java-content-handler=com.sun.mail.handlers.text_html");
        mc.addMailcap("text/xml;; x-java-content-handler=com.sun.mail.handlers.text_xml");
        mc.addMailcap("text/plain;; x-java-content-handler=com.sun.mail.handlers.text_plain");
        mc.addMailcap("multipart/*;; x-java-content-handler=com.sun.mail.handlers.multipart_mixed");
        mc.addMailcap("message/rfc822;; x-java-content-handler=com.sun.mail.handlers.message_rfc822");
        CommandMap.setDefaultCommandMap(mc);
    }

    /** Call at the start of every background thread that uses Jakarta Mail. */
    static void prepThread() {
        Thread.currentThread().setContextClassLoader(MailApi.class.getClassLoader());
    }

    private static MailApi instance;

    static synchronized MailApi get(Context ctx) {
        if (instance == null) instance = new MailApi(ctx.getApplicationContext());
        return instance;
    }

    private final Context ctx;
    private final AccountStore accounts;
    private final Map<String, IMAPStore> stores = new HashMap<>();
    private final Map<String, Object> locks = new HashMap<>();

    private MailApi(Context ctx) {
        this.ctx = ctx;
        this.accounts = AccountStore.get(ctx);
    }

    // =====================================================================================
    // Routing
    // =====================================================================================
    Object handle(String method, String url, String body) throws Exception {
        Uri u = Uri.parse("https://local" + url);
        List<String> seg = u.getPathSegments();
        String route = seg.size() > 1 ? seg.get(1) : "";
        JSONObject b = body == null || body.isEmpty() ? new JSONObject() : new JSONObject(body);

        switch (route) {
            case "login":
                return login(b);
            case "me":
                return me();
            case "logout":
                return logout();
            case "unread":
                return unread();
            case "accounts": {
                String id = seg.size() > 2 ? seg.get(2) : "";
                if ("DELETE".equals(method)) return removeAccount(id);
                if (seg.size() > 3 && "notify".equals(seg.get(3))) return setNotify(id, b.optBoolean("on"));
                throw new ApiException(404, "Not found");
            }
            default:
                break;
        }

        Account a = account(u.getQueryParameter("acc"));
        String folder = param(u, "folder", "INBOX");
        switch (route) {
            case "folders":
                return "POST".equals(method) ? createFolder(a, b.optString("name").trim()) : folders(a);
            case "messages":
                return messages(a, folder, intParam(u, "page", 1), intParam(u, "size", 50), param(u, "q", "").trim());
            case "message":
                return message(a, folder, longParam(u, "uid"), "1".equals(u.getQueryParameter("images")));
            case "attachment-save":
                return saveAttachment(a, folder, longParam(u, "uid"), intParam(u, "index", 0));
            case "flags":
                return flags(a, b);
            case "move":
                return move(a, b);
            case "delete":
                return delete(a, b);
            case "send":
                return send(a, b);
            case "draft":
                return draft(a, b);
            default:
                throw new ApiException(404, "Not found");
        }
    }

    private static String param(Uri u, String name, String def) {
        String v = u.getQueryParameter(name);
        return v == null || v.isEmpty() ? def : v;
    }

    private static int intParam(Uri u, String name, int def) {
        try { return Integer.parseInt(param(u, name, String.valueOf(def))); } catch (NumberFormatException e) { return def; }
    }

    private static long longParam(Uri u, String name) {
        try { return Long.parseLong(param(u, name, "0")); } catch (NumberFormatException e) { return 0; }
    }

    private Account account(String id) throws ApiException {
        if (accounts.isEmpty()) throw new ApiException(401, "Login required");
        if (id == null || id.isEmpty()) return accounts.first();
        Account a = accounts.find(id);
        if (a == null) throw new ApiException(404, "This account is not in the app.", "NO_ACCOUNT");
        return a;
    }

    // =====================================================================================
    // Connections
    // =====================================================================================
    static Properties props(Account a) {
        return props(a, 60_000);
    }

    static Properties props(Account a, int readTimeoutMs) {
        Properties p = new Properties();
        String ip = a.imapSsl ? "imaps" : "imap";
        String sp = a.smtpSsl ? "smtps" : "smtp";
        p.put("mail." + ip + ".connectiontimeout", "15000");
        p.put("mail." + ip + ".timeout", String.valueOf(readTimeoutMs));
        p.put("mail." + ip + ".partialfetch", "false");
        if (!a.imapSsl) p.put("mail.imap.starttls.enable", "true");
        p.put("mail." + sp + ".auth", "true");
        p.put("mail." + sp + ".connectiontimeout", "15000");
        p.put("mail." + sp + ".timeout", "60000");
        p.put("mail." + sp + ".localhost", a.email.substring(a.email.indexOf('@') + 1));
        if (!a.smtpSsl) {
            p.put("mail.smtp.starttls.enable", "true");
            if (a.smtpPort == 587) p.put("mail.smtp.starttls.required", "true");
        }
        for (String proto : new String[]{ip, sp}) {
            if (a.insecure) {
                p.put("mail." + proto + ".ssl.trust", "*");
                p.put("mail." + proto + ".ssl.checkserveridentity", "false");
            } else {
                p.put("mail." + proto + ".ssl.checkserveridentity", "true");
            }
        }
        return p;
    }

    static IMAPStore connect(Account a) throws MessagingException {
        return connect(a, 60_000);
    }

    /** readTimeoutMs must be longer than any quiet period the caller expects (e.g. IDLE). */
    static IMAPStore connect(Account a, int readTimeoutMs) throws MessagingException {
        Session session = Session.getInstance(props(a, readTimeoutMs));
        IMAPStore s = (IMAPStore) session.getStore(a.imapSsl ? "imaps" : "imap");
        s.connect(a.imapHost, a.imapPort, a.user, a.pass);
        return s;
    }

    private Object lockFor(String id) {
        synchronized (locks) {
            Object l = locks.get(id);
            if (l == null) {
                l = new Object();
                locks.put(id, l);
            }
            return l;
        }
    }

    private IMAPStore store(Account a) throws MessagingException {
        IMAPStore s;
        synchronized (stores) {
            s = stores.get(a.id());
        }
        if (s != null && s.isConnected()) return s;
        if (s != null) closeQuietly(s);
        s = connect(a);
        synchronized (stores) {
            stores.put(a.id(), s);
        }
        return s;
    }

    private void drop(String id) {
        IMAPStore s;
        synchronized (stores) {
            s = stores.remove(id);
        }
        if (s != null) closeQuietly(s);
    }

    /** Runs an IMAP operation for one account; reconnects once if the connection had dropped. */
    private <T> T withStore(Account a, StoreOp<T> op) throws Exception {
        synchronized (lockFor(a.id())) {
            for (int attempt = 0; ; attempt++) {
                try {
                    return op.run(store(a));
                } catch (AuthenticationFailedException e) {
                    drop(a.id());
                    throw new ApiException(502, a.email + ": the password is wrong or was changed. Remove the account and add it again.");
                } catch (StoreClosedException | FolderClosedException e) {
                    drop(a.id());
                    if (attempt > 0) throw friendly(e, a);
                } catch (MessagingException e) {
                    // Only a broken connection is worth a fresh login; other errors (missing folder...) are reported
                    if (attempt > 0 || !(e.getNextException() instanceof IOException)) throw friendly(e, a);
                    drop(a.id());
                }
            }
        }
    }

    private static void closeQuietly(IMAPStore s) {
        try { s.close(); } catch (Exception ignored) { }
    }

    private static void closeQuietly(Folder f) {
        try { if (f != null && f.isOpen()) f.close(false); } catch (Exception ignored) { }
    }

    static boolean isCertProblem(Throwable e) {
        for (Throwable t = e; t != null; t = nextCause(t)) {
            if (t instanceof SSLHandshakeException || t instanceof SSLPeerUnverifiedException || t instanceof CertificateException) return true;
            String m = String.valueOf(t.getMessage());
            if (m.contains("certificate") || m.contains("Trust anchor") || m.contains("server's identity")
                    || m.contains("verify identity") || m.contains("Could not verify") || m.contains("not trusted")) return true;
        }
        return false;
    }

    private static Throwable nextCause(Throwable t) {
        if (t instanceof MessagingException && ((MessagingException) t).getNextException() != null) {
            return ((MessagingException) t).getNextException();
        }
        return t.getCause() == t ? null : t.getCause();
    }

    /** True when the secure (TLS / STARTTLS) part of the connection failed. */
    static boolean isTlsProblem(Throwable e) {
        if (isCertProblem(e)) return true;
        for (Throwable t = e; t != null; t = nextCause(t)) {
            if (t instanceof SSLException) return true;
            String m = String.valueOf(t.getMessage());
            if (m.contains("STARTTLS") || m.contains("SSL") || m.contains("TLS") || m.contains("handshake")) return true;
        }
        return false;
    }

    static boolean isConnectProblem(Throwable e) {
        for (Throwable t = e; t != null; t = nextCause(t)) {
            if (t instanceof ConnectException || t instanceof SocketTimeoutException || t instanceof java.net.SocketException) return true;
        }
        return false;
    }

    /** The whole chain of reasons, e.g. "STARTTLS failure: Can't verify identity of server". */
    static String describe(Throwable e) {
        List<String> parts = new ArrayList<>();
        for (Throwable t = e; t != null && parts.size() < 4; t = nextCause(t)) {
            String m = t.getMessage();
            if (m == null || m.trim().isEmpty()) continue;
            m = m.trim();
            boolean dup = false;
            for (String p : parts) if (p.contains(m) || m.contains(p)) dup = true;
            if (!dup) parts.add(m);
        }
        return parts.isEmpty() ? e.getClass().getSimpleName() : android.text.TextUtils.join(": ", parts);
    }

    private static ApiException friendly(Exception e, Account a) {
        if (e instanceof ApiException) return (ApiException) e;
        if (e instanceof AuthenticationFailedException) {
            String hint = "Wrong email or password.";
            String h = a.imapHost;
            if (h.contains("gmail") || h.contains("google")) hint += " For Gmail, create an \"App password\" under Google Account > Security and use that.";
            else if (h.contains("office365") || h.contains("outlook")) hint += " Microsoft accounts need IMAP turned on, and an app password if 2-step verification is on.";
            else if (h.contains("yahoo") || h.contains("aol") || h.contains("me.com")) hint += " This provider needs an \"app password\".";
            return new ApiException(401, hint);
        }
        for (Throwable t = e; t != null; t = nextCause(t)) {
            if (t instanceof UnknownHostException || t instanceof ConnectException || t instanceof SocketTimeoutException) {
                return new ApiException(502, "Could not connect to the mail server (" + a.imapHost + ":" + a.imapPort + "). Check your internet connection.");
            }
        }
        if (isTlsProblem(e)) {
            return new ApiException(502, "Could not open a secure connection to " + a.imapHost + ". "
                    + "Try your hosting server's name (e.g. server123.hostingcompany.com) in Manual settings. Reason: " + describe(e));
        }
        return new ApiException(502, describe(e));
    }

    // =====================================================================================
    // Accounts
    // =====================================================================================
    private JSONObject login(JSONObject b) throws Exception {
        String email = b.optString("email").trim();
        String pass = b.optString("password");
        if (!email.matches("^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$") || pass.isEmpty()) {
            throw new ApiException(400, "Enter a valid email and password.");
        }
        JSONObject manual = b.optJSONObject("manual");
        Account a = new Account();
        a.email = email;
        a.pass = pass;
        boolean tryLocal = false;
        String manualUser = manual == null ? "" : manual.optString("username").trim();

        if (manual != null && !manual.optString("imapHost").trim().isEmpty()) {
            a.imapHost = manual.optString("imapHost").trim();
            a.imapPort = parsePort(manual.optString("imapPort"), 993);
            a.imapSsl = a.imapPort == 993;
            String sh = manual.optString("smtpHost").trim();
            a.smtpHost = sh.isEmpty() ? a.imapHost : sh;
            a.smtpPort = parsePort(manual.optString("smtpPort"), 465);
            a.smtpSsl = a.smtpPort == 465;
        } else {
            Discover.Result r = Discover.discover(email);
            a.imapHost = r.imapHost; a.imapPort = r.imapPort; a.imapSsl = r.imapSsl;
            a.smtpHost = r.smtpHost; a.smtpPort = r.smtpPort; a.smtpSsl = r.smtpSsl;
            a.insecure = r.insecure;
            tryLocal = r.localUser;
        }
        a.user = manualUser.isEmpty() ? email : manualUser;

        Account old = accounts.find(a.id());
        if (old == null && accounts.all().size() >= 20) throw new ApiException(400, "You can add up to 20 accounts.");

        IMAPStore s = verify(a, tryLocal && manualUser.isEmpty());
        a.notify = old == null || old.notify;
        synchronized (lockFor(a.id())) {
            drop(a.id());
            synchronized (stores) {
                stores.put(a.id(), s);
            }
        }
        accounts.put(a);
        MailCheck.reset(ctx, a.id());
        MailService.sync(ctx);
        return a.publicJson();
    }

    private static int parsePort(String s, int def) {
        try {
            int p = Integer.parseInt(s.trim());
            return p > 0 && p < 65536 ? p : def;
        } catch (Exception e) {
            return def;
        }
    }

    /** Logs in once (or twice: some hosts want only the part before @ as username). */
    private IMAPStore verify(Account a, boolean tryLocal) throws ApiException {
        List<String> users = new ArrayList<>();
        users.add(a.user);
        if (tryLocal) users.add(a.email.substring(0, a.email.indexOf('@')));
        Exception last = null;
        for (String user : users) {
            a.user = user;
            try {
                return connectAdapting(a);
            } catch (AuthenticationFailedException e) {
                last = e; // try the next username form
            } catch (MessagingException e) {
                throw friendly(e, a);
            }
        }
        a.user = users.get(0);
        throw friendly(last, a);
    }

    /**
     * Connects to IMAP, adjusting settings when the secure connection fails:
     * first accept a certificate issued for another name (common on cPanel shared hosting),
     * then try the other port (993 SSL <-> 143 STARTTLS). The working settings stay in the account.
     */
    private static IMAPStore connectAdapting(Account a) throws MessagingException {
        final int port0 = a.imapPort;
        final boolean ssl0 = a.imapSsl, insecure0 = a.insecure;
        int[][] variants = a.imapSsl ? new int[][]{{port0, 1}, {143, 0}} : new int[][]{{port0, 0}, {993, 1}};
        MessagingException first = null;
        for (int[] v : variants) {
            a.imapPort = v[0];
            a.imapSsl = v[1] == 1;
            boolean[] trustModes = insecure0 ? new boolean[]{true} : new boolean[]{false, true};
            for (boolean insecure : trustModes) {
                a.insecure = insecure;
                try {
                    return connect(a);
                } catch (AuthenticationFailedException e) {
                    throw e; // the connection itself worked
                } catch (MessagingException e) {
                    if (first == null) first = e;
                    if (!isTlsProblem(e)) break; // a certificate exception can't fix this; try the other port
                }
            }
            if (first != null && !isTlsProblem(first) && !isConnectProblem(first)) break;
        }
        a.imapPort = port0;
        a.imapSsl = ssl0;
        a.insecure = insecure0;
        throw first;
    }

    private final java.util.concurrent.ExecutorService background = java.util.concurrent.Executors.newCachedThreadPool();

    private JSONObject me() throws Exception {
        if (accounts.isEmpty()) throw new ApiException(401, "Login required");
        // Log in to every account in the background now, so opening or switching accounts is quick
        for (Account a : accounts.all()) {
            background.execute(() -> {
                prepThread();
                try { withStore(a, s -> null); } catch (Exception ignored) { }
            });
        }
        return new JSONObject().put("accounts", publicList());
    }

    private JSONArray publicList() throws Exception {
        JSONArray arr = new JSONArray();
        for (Account a : accounts.all()) arr.put(a.publicJson());
        return arr;
    }

    private JSONObject logout() throws Exception {
        for (Account a : accounts.all()) drop(a.id());
        accounts.clear();
        MailCheck.resetAll(ctx);
        MailService.sync(ctx);
        return new JSONObject().put("ok", true);
    }

    private JSONObject removeAccount(String id) throws Exception {
        drop(id.toLowerCase(Locale.ROOT));
        accounts.remove(id.toLowerCase(Locale.ROOT));
        MailCheck.reset(ctx, id.toLowerCase(Locale.ROOT));
        Notifier.clearAccount(ctx, id.toLowerCase(Locale.ROOT));
        MailService.sync(ctx);
        return new JSONObject().put("accounts", publicList());
    }

    private JSONObject setNotify(String id, boolean on) throws Exception {
        Account a = accounts.find(id);
        if (a == null) throw new ApiException(404, "Account not found.");
        a.notify = on;
        accounts.save();
        if (on) MailCheck.reset(ctx, a.id()); // start counting from now, don't replay old mail
        MailService.sync(ctx);
        return a.publicJson();
    }

    private JSONObject unread() throws Exception {
        if (accounts.isEmpty()) throw new ApiException(401, "Login required");
        List<Account> list = accounts.all();
        List<java.util.concurrent.Future<Integer>> counts = new ArrayList<>();
        for (Account a : list) {
            counts.add(background.submit(() -> {
                prepThread();
                return withStore(a, s -> s.getFolder("INBOX").getUnreadMessageCount());
            }));
        }
        JSONObject out = new JSONObject();
        for (int i = 0; i < list.size(); i++) {
            try {
                out.put(list.get(i).id(), (int) counts.get(i).get(30, java.util.concurrent.TimeUnit.SECONDS));
            } catch (Exception e) {
                out.put(list.get(i).id(), JSONObject.NULL);
            }
        }
        return out;
    }

    // =====================================================================================
    // Folders
    // =====================================================================================
    private static final String[] USES = {"\\Drafts", "\\Sent", "\\Archive", "\\Junk", "\\Trash", "\\All", "\\Flagged"};
    private static final Map<String, Pattern> USE_NAMES = new LinkedHashMap<>();
    private static final List<String> ORDER = Arrays.asList("\\Inbox", "\\Drafts", "\\Sent", "\\Archive", "\\Junk", "\\Trash");

    static {
        int ci = Pattern.CASE_INSENSITIVE;
        USE_NAMES.put("\\Sent", Pattern.compile("^(inbox[./])?(sent|sent items|sent messages|sent mail)$", ci));
        USE_NAMES.put("\\Trash", Pattern.compile("^(inbox[./])?(trash|deleted|deleted items|deleted messages|bin)$", ci));
        USE_NAMES.put("\\Drafts", Pattern.compile("^(inbox[./])?drafts?$", ci));
        USE_NAMES.put("\\Junk", Pattern.compile("^(inbox[./])?(junk|spam|junk e-mail|junk email)$", ci));
    }

    private static String attrUse(String path, String[] attrs) {
        if ("INBOX".equalsIgnoreCase(path)) return "\\Inbox";
        if (attrs != null) {
            for (String at : attrs) for (String u : USES) if (u.equalsIgnoreCase(at)) return u;
        }
        return null;
    }

    private static boolean hasAttr(String[] attrs, String want) {
        if (attrs == null) return false;
        for (String at : attrs) if (want.equalsIgnoreCase(at)) return true;
        return false;
    }

    /** Full name of the Sent/Trash/Drafts folder, or null. */
    private static String findSpecial(IMAPStore s, String use) throws MessagingException {
        Folder[] list = s.getDefaultFolder().list("*");
        for (Folder f : list) {
            if (use.equals(attrUse(f.getFullName(), ((IMAPFolder) f).getAttributes()))) return f.getFullName();
        }
        Pattern p = USE_NAMES.get(use);
        if (p != null) for (Folder f : list) if (p.matcher(f.getFullName()).matches()) return f.getFullName();
        return null;
    }

    private JSONArray folders(Account a) throws Exception {
        return withStore(a, s -> {
            Folder[] list = s.getDefaultFolder().list("*");
            List<JSONObject> items = new ArrayList<>();
            List<String> taken = new ArrayList<>();
            for (Folder f : list) {
                String[] attrs = ((IMAPFolder) f).getAttributes();
                if (hasAttr(attrs, "\\Noselect") || hasAttr(attrs, "\\NonExistent")) continue;
                if ((f.getType() & Folder.HOLDS_MESSAGES) == 0) continue;
                String use = attrUse(f.getFullName(), attrs);
                if (use != null) taken.add(use);
                int unseen = 0, total = 0;
                // Counts cost one server round trip per folder; only Inbox and Spam show them
                boolean counted = "\\Inbox".equals(use) || "\\Junk".equals(use);
                if (counted) {
                    try { unseen = f.getUnreadMessageCount(); } catch (MessagingException ignored) { }
                }
                items.add(new JSONObject()
                        .put("path", f.getFullName())
                        .put("name", f.getName())
                        .put("specialUse", use == null ? JSONObject.NULL : use)
                        .put("unseen", Math.max(0, unseen))
                        .put("total", Math.max(0, total))
                        .put("delimiter", String.valueOf(f.getSeparator())));
            }
            // Servers without SPECIAL-USE: recognise the usual names
            for (JSONObject it : items) {
                if (!it.isNull("specialUse")) continue;
                for (Map.Entry<String, Pattern> e : USE_NAMES.entrySet()) {
                    if (!taken.contains(e.getKey()) && e.getValue().matcher(it.getString("path")).matches()) {
                        it.put("specialUse", e.getKey());
                        taken.add(e.getKey());
                        if ("\\Junk".equals(e.getKey())) {
                            try { it.put("unseen", Math.max(0, s.getFolder(it.getString("path")).getUnreadMessageCount())); } catch (MessagingException ignored) { }
                        }
                        break;
                    }
                }
            }
            Collections.sort(items, (x, y) -> {
                int ox = x.isNull("specialUse") ? 9 : ORDER.indexOf(x.optString("specialUse"));
                int oy = y.isNull("specialUse") ? 9 : ORDER.indexOf(y.optString("specialUse"));
                if (ox < 0) ox = 9;
                if (oy < 0) oy = 9;
                return ox != oy ? Integer.compare(ox, oy) : x.optString("path").compareToIgnoreCase(y.optString("path"));
            });
            JSONArray out = new JSONArray();
            for (JSONObject it : items) out.put(it);
            return out;
        });
    }

    private JSONObject createFolder(Account a, String name) throws Exception {
        if (name.isEmpty()) throw new ApiException(400, "Enter a folder name.");
        return withStore(a, s -> {
            Folder f = s.getFolder(name);
            if (f.exists()) throw new ApiException(400, "A folder with this name already exists.");
            if (!f.create(Folder.HOLDS_MESSAGES)) throw new ApiException(502, "Could not create the folder.");
            return new JSONObject().put("ok", true);
        });
    }

    // =====================================================================================
    // Message list
    // =====================================================================================
    private static String iso(Date d) {
        if (d == null) return null;
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return f.format(d);
    }

    private static JSONArray addrs(Address[] list) throws Exception {
        JSONArray arr = new JSONArray();
        if (list == null) return arr;
        for (Address ad : list) {
            if (ad instanceof InternetAddress) {
                InternetAddress ia = (InternetAddress) ad;
                arr.put(new JSONObject().put("name", ia.getPersonal() == null ? "" : ia.getPersonal()).put("address", ia.getAddress() == null ? "" : ia.getAddress()));
            } else if (ad != null) {
                arr.put(new JSONObject().put("name", "").put("address", ad.toString()));
            }
        }
        return arr;
    }

    private static String subjectOf(Message m) {
        try {
            String s = m.getSubject();
            return s == null || s.trim().isEmpty() ? "(no subject)" : s;
        } catch (MessagingException e) {
            return "(no subject)";
        }
    }

    private static Address[] safeFrom(Message m) {
        try { return m.getFrom(); } catch (MessagingException e) { return null; }
    }

    private static Address[] safeRcpt(Message m, Message.RecipientType t) {
        try { return m.getRecipients(t); } catch (MessagingException e) { return null; }
    }

    private static Date dateOf(Message m) {
        try {
            Date d = m.getSentDate();
            return d != null ? d : m.getReceivedDate();
        } catch (MessagingException e) {
            return null;
        }
    }

    private JSONObject messages(Account a, String folder, int page, int size, String q) throws Exception {
        final int pg = Math.max(1, page);
        final int sz = Math.min(100, Math.max(1, size));
        return withStore(a, s -> {
            IMAPFolder f = (IMAPFolder) s.getFolder(folder);
            f.open(Folder.READ_ONLY);
            try {
                int total;
                Message[] msgs;
                if (!q.isEmpty()) {
                    SearchTerm term = new OrTerm(new SearchTerm[]{
                            new SubjectTerm(q), new FromStringTerm(q),
                            new RecipientStringTerm(Message.RecipientType.TO, q), new BodyTerm(q)});
                    Message[] found = f.search(term);
                    FetchProfile ufp = new FetchProfile();
                    ufp.add(UIDFolder.FetchProfileItem.UID);
                    f.fetch(found, ufp);
                    List<Message> sorted = new ArrayList<>(Arrays.asList(found));
                    Collections.sort(sorted, (x, y) -> Long.compare(uidOf(f, y), uidOf(f, x)));
                    total = sorted.size();
                    int from = Math.min(total, (pg - 1) * sz), to = Math.min(total, pg * sz);
                    msgs = sorted.subList(from, to).toArray(new Message[0]);
                } else {
                    total = f.getMessageCount();
                    int end = total - (pg - 1) * sz;
                    if (end < 1) {
                        msgs = new Message[0];
                    } else {
                        msgs = f.getMessages(Math.max(1, end - sz + 1), end);
                    }
                }
                FetchProfile fp = new FetchProfile();
                fp.add(FetchProfile.Item.ENVELOPE);
                fp.add(FetchProfile.Item.FLAGS);
                fp.add("Content-Type"); // enough to spot attachments; much lighter than BODYSTRUCTURE
                fp.add(FetchProfile.Item.SIZE);
                fp.add(UIDFolder.FetchProfileItem.UID);
                if (msgs.length > 0) f.fetch(msgs, fp);

                List<JSONObject> items = new ArrayList<>();
                for (Message m : msgs) {
                    if (m == null) continue;
                    boolean attach = false;
                    try {
                        String[] ct = m.getHeader("Content-Type");
                        attach = ct != null && ct.length > 0 && ct[0].toLowerCase(Locale.ROOT).contains("multipart/mixed");
                    } catch (MessagingException ignored) { }
                    Flags fl = m.getFlags();
                    items.add(new JSONObject()
                            .put("uid", f.getUID(m))
                            .put("subject", subjectOf(m))
                            .put("from", addrs(safeFrom(m)))
                            .put("to", addrs(safeRcpt(m, Message.RecipientType.TO)))
                            .put("date", iso(dateOf(m)))
                            .put("seen", fl.contains(Flags.Flag.SEEN))
                            .put("flagged", fl.contains(Flags.Flag.FLAGGED))
                            .put("answered", fl.contains(Flags.Flag.ANSWERED))
                            .put("attachments", attach)
                            .put("size", Math.max(0, m.getSize())));
                }
                Collections.sort(items, (x, y) -> Long.compare(y.optLong("uid"), x.optLong("uid")));
                JSONArray arr = new JSONArray();
                for (JSONObject it : items) arr.put(it);
                return new JSONObject().put("total", total).put("messages", arr).put("page", pg).put("size", sz);
            } finally {
                closeQuietly(f);
            }
        });
    }

    private static long uidOf(IMAPFolder f, Message m) {
        try { return f.getUID(m); } catch (MessagingException e) { return 0; }
    }

    // =====================================================================================
    // Reading
    // =====================================================================================
    private static final class Parsed {
        final StringBuilder html = new StringBuilder();
        final StringBuilder text = new StringBuilder();
        final List<Part> atts = new ArrayList<>();
        final Map<String, String> cids = new HashMap<>();
    }

    private static Parsed parse(Part p) throws Exception {
        Parsed out = new Parsed();
        walk(p, out);
        return out;
    }

    private static void walk(Part p, Parsed out) throws Exception {
        if (p.isMimeType("multipart/alternative")) {
            Object c = p.getContent();
            if (c instanceof Multipart) {
                Multipart mp = (Multipart) c;
                Part html = null, text = null, rich = null;
                for (int i = 0; i < mp.getCount(); i++) {
                    BodyPart bp = mp.getBodyPart(i);
                    if (bp.isMimeType("text/html")) html = bp;
                    else if (bp.isMimeType("text/plain")) text = bp;
                    else if (bp.isMimeType("multipart/*")) rich = bp;
                }
                if (rich != null) walk(rich, out);
                else if (html != null) out.html.append(textOf(html));
                else if (text != null) out.text.append(textOf(text));
                return;
            }
        }
        if (p.isMimeType("multipart/*")) {
            Object c = p.getContent();
            if (c instanceof Multipart) {
                Multipart mp = (Multipart) c;
                for (int i = 0; i < mp.getCount(); i++) walk(mp.getBodyPart(i), out);
                return;
            }
        }
        String disp = p.getDisposition();
        boolean attachment = Part.ATTACHMENT.equalsIgnoreCase(disp);
        String name = fileName(p);
        String cid = p instanceof MimePart ? ((MimePart) p).getContentID() : null;
        if (cid != null && p.isMimeType("image/*") && !attachment) {
            out.cids.put(cid.replaceAll("^<|>$", ""), "data:" + baseType(p) + ";base64," + Base64.encodeToString(bytesOf(p), Base64.NO_WRAP));
            return;
        }
        if (!attachment && name == null && p.isMimeType("text/html")) {
            out.html.append(textOf(p));
            return;
        }
        if (!attachment && name == null && p.isMimeType("text/plain")) {
            if (out.text.length() > 0) out.text.append("\n\n");
            out.text.append(textOf(p));
            return;
        }
        out.atts.add(p);
    }

    private static String fileName(Part p) {
        try {
            String n = p.getFileName();
            if (n == null || n.trim().isEmpty()) return null;
            return n.contains("=?") ? MimeUtility.decodeText(n) : n;
        } catch (Exception e) {
            return null;
        }
    }

    private static String attachmentName(Part p, int index) {
        String n = fileName(p);
        if (n != null) return n;
        try {
            if (p.isMimeType("message/rfc822")) return "message-" + (index + 1) + ".eml";
        } catch (MessagingException ignored) {
        }
        return "attachment-" + (index + 1);
    }

    private static String baseType(Part p) {
        try {
            String ct = p.getContentType();
            if (ct == null) return "application/octet-stream";
            String b = ct.split(";")[0].trim().toLowerCase(Locale.ROOT);
            return b.contains("/") ? b : "application/octet-stream";
        } catch (MessagingException e) {
            return "application/octet-stream";
        }
    }

    private static byte[] bytesOf(Part p) throws Exception {
        try (InputStream in = p.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        }
    }

    private static String textOf(Part p) throws Exception {
        try {
            Object c = p.getContent();
            if (c instanceof String) return (String) c;
        } catch (UnsupportedEncodingException | RuntimeException ignored) {
            // unknown charset: fall back to UTF-8 below
        }
        return new String(bytesOf(p), StandardCharsets.UTF_8);
    }

    private static String escape(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    private static final Safelist SAFE = Safelist.relaxed()
            .addTags("font", "center", "hr", "style", "del", "ins", "s", "u")
            .addAttributes(":all", "style", "class", "align", "width", "height", "bgcolor", "color", "border",
                    "cellpadding", "cellspacing", "valign", "dir", "face", "size", "colspan", "rowspan")
            .addAttributes("img", "alt", "title")
            .addProtocols("img", "src", "data", "cid")
            .addProtocols("a", "href", "tel");

    /** Removes scripts and dangerous markup; inline images become data URIs; remote images wait for permission. */
    private static String[] cleanHtml(String html, Map<String, String> cids, boolean showImages) {
        Document dirty = Jsoup.parse(html);
        for (Element st : dirty.head().select("style")) dirty.body().prependChild(st.clone());
        for (Element img : dirty.select("img")) {
            String src = img.attr("src");
            if (src.regionMatches(true, 0, "cid:", 0, 4)) {
                String data = cids.get(src.substring(4));
                img.attr("src", data == null ? "" : data);
            }
        }
        Document clean = new Cleaner(SAFE).clean(dirty);
        boolean blocked = false;
        for (Element img : clean.select("img")) {
            String src = img.attr("src").toLowerCase(Locale.ROOT);
            if ((src.startsWith("http:") || src.startsWith("https:")) && !showImages) {
                img.attr("src", "");
                img.attr("data-blocked", "1");
                blocked = true;
            }
        }
        for (Element link : clean.select("a[href]")) {
            link.attr("target", "_blank");
            link.attr("rel", "noopener noreferrer");
        }
        return new String[]{clean.body().html(), blocked ? "1" : "0"};
    }

    /** Opens the folder for writing when possible (so reading marks as seen), else read-only. */
    private static void openBest(Folder f) throws MessagingException {
        try {
            f.open(Folder.READ_WRITE);
        } catch (MessagingException e) {
            f.open(Folder.READ_ONLY);
        }
    }

    private JSONObject message(Account a, String folder, long uid, boolean images) throws Exception {
        return withStore(a, s -> {
            IMAPFolder f = (IMAPFolder) s.getFolder(folder);
            openBest(f);
            try {
                Message raw = f.getMessageByUID(uid);
                if (raw == null) throw new ApiException(404, "Message not found.");
                boolean flagged = raw.isSet(Flags.Flag.FLAGGED);
                MimeMessage m = (MimeMessage) raw; // parts are fetched one by one; attachments aren't downloaded here
                Parsed p = parse(m);
                if (f.getMode() == Folder.READ_WRITE) raw.setFlag(Flags.Flag.SEEN, true);

                String html;
                boolean blocked = false;
                if (p.html.length() > 0) {
                    String[] c = cleanHtml(p.html.toString(), p.cids, images);
                    html = c[0];
                    blocked = "1".equals(c[1]);
                } else {
                    html = "<pre style=\"white-space:pre-wrap;font-family:inherit;margin:0\">" + escape(p.text.toString()) + "</pre>";
                }

                JSONArray atts = new JSONArray();
                for (int i = 0; i < p.atts.size(); i++) {
                    Part ap = p.atts.get(i);
                    int sz = ap.getSize();
                    String enc = ap instanceof MimePart ? ((MimePart) ap).getEncoding() : null;
                    if (sz > 0 && "base64".equalsIgnoreCase(enc)) sz = sz * 3 / 4;
                    atts.put(new JSONObject().put("index", i).put("filename", attachmentName(ap, i))
                            .put("contentType", baseType(ap)).put("size", Math.max(0, sz)).put("inline", false));
                }
                JSONArray refs = new JSONArray();
                String[] refHeader = m.getHeader("References");
                if (refHeader != null) {
                    for (String r : refHeader[0].trim().split("\\s+")) if (!r.isEmpty()) refs.put(r);
                }
                Address[] replyTo = m.getHeader("Reply-To") == null ? null : m.getReplyTo();
                return new JSONObject()
                        .put("uid", uid)
                        .put("folder", folder)
                        .put("subject", subjectOf(m))
                        .put("from", addrs(m.getFrom()))
                        .put("to", addrs(m.getRecipients(Message.RecipientType.TO)))
                        .put("cc", addrs(m.getRecipients(Message.RecipientType.CC)))
                        .put("replyTo", addrs(replyTo))
                        .put("date", iso(dateOf(m)))
                        .put("messageId", m.getMessageID() == null ? JSONObject.NULL : m.getMessageID())
                        .put("references", refs)
                        .put("html", html)
                        .put("imagesBlocked", blocked)
                        .put("text", p.text.toString())
                        .put("flagged", flagged)
                        .put("attachments", atts);
            } finally {
                closeQuietly(f);
            }
        });
    }

    /** Downloads one attachment of a message: [name, mime, bytes]. Caller holds the store. */
    private static Object[] attachmentOf(IMAPStore s, String folder, long uid, int index) throws Exception {
        IMAPFolder f = (IMAPFolder) s.getFolder(folder);
        f.open(Folder.READ_ONLY);
        try {
            Message raw = f.getMessageByUID(uid);
            if (raw == null) throw new ApiException(404, "Message not found.");
            Parsed p = parse(raw);
            if (index < 0 || index >= p.atts.size()) throw new ApiException(404, "Attachment not found.");
            Part part = p.atts.get(index);
            return new Object[]{attachmentName(part, index), baseType(part), bytesOf(part)};
        } finally {
            closeQuietly(f);
        }
    }

    private JSONObject saveAttachment(Account a, String folder, long uid, int index) throws Exception {
        Object[] att = withStore(a, s -> attachmentOf(s, folder, uid, index));
        String name = ((String) att[0]).replaceAll("[\\\\/:*?\"<>|\\r\\n]", "_");
        String mime = (String) att[1];
        Uri uri = saveToDownloads(name, mime, (byte[]) att[2]);
        MainActivity act = MainActivity.visible;
        if (act != null) act.openFile(uri, mime);
        String where = Build.VERSION.SDK_INT >= 29 ? "Downloads/Mail" : "the app's Download folder";
        return new JSONObject().put("ok", true).put("name", name).put("where", where);
    }

    private Uri saveToDownloads(String name, String mime, byte[] data) throws IOException {
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues v = new ContentValues();
            v.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            v.put(MediaStore.MediaColumns.MIME_TYPE, mime);
            v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Mail");
            Uri uri = ctx.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (uri == null) throw new IOException("Could not create the file");
            try (OutputStream os = ctx.getContentResolver().openOutputStream(uri)) {
                if (os == null) throw new IOException("Could not open the file");
                os.write(data);
            }
            return uri;
        }
        File base = ctx.getExternalFilesDir(null);
        if (base == null) throw new IOException("Storage is not available right now");
        File dir = new File(base, "Download");
        if (!dir.exists() && !dir.mkdirs()) throw new IOException("Could not create the folder");
        File f = new File(dir, name);
        for (int i = 1; f.exists(); i++) {
            int dot = name.lastIndexOf('.');
            f = new File(dir, dot > 0 ? name.substring(0, dot) + " (" + i + ")" + name.substring(dot) : name + " (" + i + ")");
        }
        try (OutputStream os = new FileOutputStream(f)) {
            os.write(data);
        }
        return FileProvider.getUriForFile(ctx, ctx.getPackageName() + ".files", f);
    }

    // =====================================================================================
    // Flags, move, delete
    // =====================================================================================
    private static Message[] byUids(IMAPFolder f, JSONArray uids) throws MessagingException {
        if (uids == null || uids.length() == 0) return new Message[0];
        long[] arr = new long[uids.length()];
        for (int i = 0; i < arr.length; i++) arr[i] = uids.optLong(i);
        List<Message> out = new ArrayList<>();
        for (Message m : f.getMessagesByUID(arr)) if (m != null) out.add(m);
        return out.toArray(new Message[0]);
    }

    private static void expunge(IMAPStore s, IMAPFolder f, Message[] ms) throws MessagingException {
        if (s.hasCapability("UIDPLUS")) f.expunge(ms);
        else f.expunge();
    }

    private static void moveTo(IMAPStore s, IMAPFolder f, Message[] ms, String dest) throws MessagingException {
        if (ms.length == 0) return;
        Folder d = s.getFolder(dest);
        if (s.hasCapability("MOVE")) {
            f.moveMessages(ms, d);
        } else {
            f.copyMessages(ms, d);
            f.setFlags(ms, new Flags(Flags.Flag.DELETED), true);
            expunge(s, f, ms);
        }
    }

    private JSONObject flags(Account a, JSONObject b) throws Exception {
        return withStore(a, s -> {
            IMAPFolder f = (IMAPFolder) s.getFolder(b.optString("folder", "INBOX"));
            f.open(Folder.READ_WRITE);
            try {
                Message[] ms = byUids(f, b.optJSONArray("uids"));
                if (ms.length > 0) {
                    if (b.has("seen") && !b.isNull("seen")) f.setFlags(ms, new Flags(Flags.Flag.SEEN), b.getBoolean("seen"));
                    if (b.has("flagged") && !b.isNull("flagged")) f.setFlags(ms, new Flags(Flags.Flag.FLAGGED), b.getBoolean("flagged"));
                }
                return new JSONObject().put("ok", true);
            } finally {
                closeQuietly(f);
            }
        });
    }

    private JSONObject move(Account a, JSONObject b) throws Exception {
        return withStore(a, s -> {
            if (b.optString("dest").trim().isEmpty()) throw new ApiException(400, "Choose a folder.");
            IMAPFolder f = (IMAPFolder) s.getFolder(b.optString("folder", "INBOX"));
            f.open(Folder.READ_WRITE);
            try {
                moveTo(s, f, byUids(f, b.optJSONArray("uids")), b.optString("dest"));
                return new JSONObject().put("ok", true);
            } finally {
                closeQuietly(f);
            }
        });
    }

    private JSONObject delete(Account a, JSONObject b) throws Exception {
        return withStore(a, s -> {
            String folder = b.optString("folder", "INBOX");
            String trash = findSpecial(s, "\\Trash");
            IMAPFolder f = (IMAPFolder) s.getFolder(folder);
            f.open(Folder.READ_WRITE);
            try {
                Message[] ms = byUids(f, b.optJSONArray("uids"));
                boolean permanent = trash == null || trash.equals(folder);
                if (ms.length > 0) {
                    if (permanent) {
                        f.setFlags(ms, new Flags(Flags.Flag.DELETED), true);
                        expunge(s, f, ms);
                    } else {
                        moveTo(s, f, ms, trash);
                    }
                }
                return new JSONObject().put("ok", true).put("permanent", permanent);
            } finally {
                closeQuietly(f);
            }
        });
    }

    // =====================================================================================
    // Sending and drafts
    // =====================================================================================
    private static void setRecipients(MimeMessage msg, Message.RecipientType t, String list) throws MessagingException {
        if (list == null || list.trim().isEmpty()) return;
        msg.setRecipients(t, InternetAddress.parse(list.trim(), false));
    }

    private MimeMessage build(Account a, JSONObject b) throws Exception {
        MimeMessage msg = new MimeMessage(Session.getInstance(props(a)));
        msg.setFrom(new InternetAddress(a.email));
        setRecipients(msg, Message.RecipientType.TO, b.optString("to"));
        setRecipients(msg, Message.RecipientType.CC, b.optString("cc"));
        setRecipients(msg, Message.RecipientType.BCC, b.optString("bcc"));
        msg.setSubject(b.optString("subject"), "UTF-8");
        msg.setSentDate(new Date());
        String irt = b.optString("inReplyTo").trim();
        if (!irt.isEmpty()) msg.setHeader("In-Reply-To", irt);
        String refs = b.optString("references").trim();
        if (!refs.isEmpty()) msg.setHeader("References", MimeUtility.fold(12, refs));

        MimeMultipart alt = new MimeMultipart("alternative");
        MimeBodyPart text = new MimeBodyPart();
        text.setText(b.optString("text"), "UTF-8");
        alt.addBodyPart(text);
        MimeBodyPart html = new MimeBodyPart();
        html.setText(b.optString("html"), "UTF-8", "html");
        alt.addBodyPart(html);

        List<MimeBodyPart> files = new ArrayList<>();
        JSONArray up = b.optJSONArray("attachments");
        if (up != null) {
            for (int i = 0; i < up.length(); i++) {
                JSONObject f = up.getJSONObject(i);
                byte[] data = Base64.decode(f.optString("data"), Base64.DEFAULT);
                files.add(filePart(f.optString("name", "file"), f.optString("type", "application/octet-stream"), data));
            }
        }
        JSONArray fw = b.optJSONArray("forward");
        if (fw != null && fw.length() > 0) {
            withStore(a, s -> {
                for (int i = 0; i < fw.length(); i++) {
                    JSONObject r = fw.getJSONObject(i);
                    Object[] att = attachmentOf(s, r.optString("folder", "INBOX"), r.optLong("uid"), r.optInt("index"));
                    files.add(filePart((String) att[0], (String) att[1], (byte[]) att[2]));
                }
                return null;
            });
        }

        if (files.isEmpty()) {
            msg.setContent(alt);
        } else {
            MimeMultipart mixed = new MimeMultipart("mixed");
            MimeBodyPart body = new MimeBodyPart();
            body.setContent(alt);
            mixed.addBodyPart(body);
            for (MimeBodyPart f : files) mixed.addBodyPart(f);
            msg.setContent(mixed);
        }
        msg.saveChanges();
        return msg;
    }

    private static MimeBodyPart filePart(String name, String type, byte[] data) throws MessagingException {
        MimeBodyPart p = new MimeBodyPart();
        p.setDataHandler(new DataHandler(new ByteArrayDataSource(data, type.isEmpty() ? "application/octet-stream" : type)));
        p.setFileName(name);
        p.setDisposition(Part.ATTACHMENT);
        return p;
    }

    /** Opens an SMTP connection, adjusting like connectAdapting: certificate, then 465 SSL <-> 587 STARTTLS. */
    private Transport smtpConnect(Account a) throws ApiException {
        final int port0 = a.smtpPort;
        final boolean ssl0 = a.smtpSsl, insecure0 = a.insecure;
        int[][] variants = a.smtpSsl ? new int[][]{{port0, 1}, {587, 0}} : new int[][]{{port0, 0}, {465, 1}};
        MessagingException first = null;
        for (int[] v : variants) {
            a.smtpPort = v[0];
            a.smtpSsl = v[1] == 1;
            boolean[] trustModes = insecure0 ? new boolean[]{true} : new boolean[]{false, true};
            for (boolean insecure : trustModes) {
                a.insecure = insecure;
                Transport t = null;
                try {
                    t = Session.getInstance(props(a)).getTransport(a.smtpSsl ? "smtps" : "smtp");
                    t.connect(a.smtpHost, a.smtpPort, a.user, a.pass);
                    if (a.smtpPort != port0 || a.smtpSsl != ssl0 || a.insecure != insecure0) accounts.save(); // remember what works
                    return t;
                } catch (AuthenticationFailedException e) {
                    throw new ApiException(502, "Could not send: the outgoing server (" + a.smtpHost + ") did not accept the password.");
                } catch (MessagingException e) {
                    if (t != null) try { t.close(); } catch (Exception ignored) { }
                    if (first == null) first = e;
                    if (!isTlsProblem(e)) break;
                }
            }
            if (first != null && !isTlsProblem(first) && !isConnectProblem(first)) break;
        }
        a.smtpPort = port0;
        a.smtpSsl = ssl0;
        a.insecure = insecure0;
        String why = first == null ? "" : describe(first);
        if (first != null && isTlsProblem(first)) {
            throw new ApiException(502, "Could not send: no secure connection to the outgoing server (" + a.smtpHost + "). Reason: " + why);
        }
        throw new ApiException(502, "Could not send: " + why);
    }

    private void smtpSend(Account a, MimeMessage msg) throws Exception {
        Transport t = smtpConnect(a);
        try {
            t.sendMessage(msg, msg.getAllRecipients());
        } catch (MessagingException e) {
            throw new ApiException(502, "Could not send: " + describe(e));
        } finally {
            try { t.close(); } catch (Exception ignored) { }
        }
    }

    private JSONObject send(Account a, JSONObject b) throws Exception {
        if (b.optString("to").trim().isEmpty() && b.optString("cc").trim().isEmpty() && b.optString("bcc").trim().isEmpty()) {
            throw new ApiException(400, "Add at least one recipient.");
        }
        MimeMessage msg;
        try {
            msg = build(a, b);
        } catch (javax.mail.internet.AddressException e) {
            throw new ApiException(400, "Invalid email address: " + e.getRef());
        }
        smtpSend(a, msg);

        // Housekeeping after sending; failures here must not report the email as unsent
        final boolean savesItself = a.smtpHost.contains("gmail.com") || a.smtpHost.contains("office365.com") || a.smtpHost.contains("outlook.com");
        try {
            withStore(a, s -> {
                if (!savesItself) {
                    String sent = findSpecial(s, "\\Sent");
                    if (sent != null) {
                        msg.setFlag(Flags.Flag.SEEN, true);
                        s.getFolder(sent).appendMessages(new Message[]{msg});
                    }
                }
                long replyUid = b.optLong("replyUid");
                String replyFolder = b.optString("replyFolder");
                if (replyUid > 0 && !replyFolder.isEmpty()) {
                    IMAPFolder f = (IMAPFolder) s.getFolder(replyFolder);
                    f.open(Folder.READ_WRITE);
                    try {
                        Message m = f.getMessageByUID(replyUid);
                        if (m != null) m.setFlag(Flags.Flag.ANSWERED, true);
                    } finally {
                        closeQuietly(f);
                    }
                }
                deleteDraft(s, b.optLong("draftUid"));
                return null;
            });
        } catch (Exception ignored) {
        }
        return new JSONObject().put("ok", true);
    }

    private static void deleteDraft(IMAPStore s, long uid) throws MessagingException {
        if (uid <= 0) return;
        String drafts = findSpecial(s, "\\Drafts");
        if (drafts == null) return;
        IMAPFolder f = (IMAPFolder) s.getFolder(drafts);
        f.open(Folder.READ_WRITE);
        try {
            Message m = f.getMessageByUID(uid);
            if (m != null) {
                m.setFlag(Flags.Flag.DELETED, true);
                expunge(s, f, new Message[]{m});
            }
        } finally {
            closeQuietly(f);
        }
    }

    private JSONObject draft(Account a, JSONObject b) throws Exception {
        MimeMessage msg = build(a, b);
        Flags fl = new Flags(Flags.Flag.DRAFT);
        fl.add(Flags.Flag.SEEN);
        msg.setFlags(fl, true);
        return withStore(a, s -> {
            String drafts = findSpecial(s, "\\Drafts");
            if (drafts == null) drafts = "Drafts";
            IMAPFolder df = (IMAPFolder) s.getFolder(drafts);
            if (!df.exists()) df.create(Folder.HOLDS_MESSAGES);
            AppendUID[] r = df.appendUIDMessages(new Message[]{msg});
            long uid = r != null && r.length > 0 && r[0] != null ? r[0].uid : 0;
            deleteDraft(s, b.optLong("draftUid"));
            return new JSONObject().put("ok", true).put("uid", uid).put("folder", drafts);
        });
    }
}
