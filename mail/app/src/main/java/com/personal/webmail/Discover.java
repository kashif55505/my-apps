package com.personal.webmail;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Finds IMAP/SMTP settings for any address. Order: known providers, autoconfig files
 * (domain + Thunderbird database), MX records (hosted providers), then probing common hostnames.
 */
final class Discover {

    static final class Result {
        String imapHost, smtpHost;
        int imapPort = 993, smtpPort = 465;
        boolean imapSsl = true, smtpSsl = true, insecure = false, localUser = false;

        static Result of(String imap, String smtp, int smtpPort) {
            Result r = new Result();
            r.imapHost = imap;
            r.smtpHost = smtp;
            r.smtpPort = smtpPort;
            r.smtpSsl = smtpPort == 465;
            return r;
        }
    }

    private static final Map<String, Result> KNOWN = new HashMap<>();
    private static final List<Object[]> MX_PROVIDERS = new ArrayList<>();

    static {
        known("imap.gmail.com", "smtp.gmail.com", 465, "gmail.com", "googlemail.com");
        known("outlook.office365.com", "smtp.office365.com", 587, "outlook.com", "hotmail.com", "live.com", "msn.com");
        known("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465, "yahoo.com", "ymail.com");
        known("imap.aol.com", "smtp.aol.com", 465, "aol.com");
        known("imap.mail.me.com", "smtp.mail.me.com", 587, "icloud.com", "me.com");
        known("imap.zoho.com", "smtp.zoho.com", 465, "zoho.com");
        known("imap.yandex.com", "smtp.yandex.com", 465, "yandex.com");
        known("imap.gmx.com", "mail.gmx.com", 587, "gmx.com");
        known("imap.mail.com", "smtp.mail.com", 587, "mail.com");

        mx("(^|\\.)google(mail)?\\.com$|googlemail\\.com$", "imap.gmail.com", "smtp.gmail.com", 465);
        mx("outlook\\.com$", "outlook.office365.com", "smtp.office365.com", 587);
        mx("yahoodns\\.net$", "imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465);
        mx("secureserver\\.net$", "imap.secureserver.net", "smtpout.secureserver.net", 465);
        mx("titan\\.email$", "imap.titan.email", "smtp.titan.email", 465);
        mx("hostinger\\.(com|in)$", "imap.hostinger.com", "smtp.hostinger.com", 465);
        mx("ionos\\.(com|de|co\\.uk)$|1and1", "imap.ionos.com", "smtp.ionos.com", 587);
        mx("messagingengine\\.com$", "imap.fastmail.com", "smtp.fastmail.com", 465);
        mx("mailbox\\.org$", "imap.mailbox.org", "smtp.mailbox.org", 465);
    }

    private static void known(String imap, String smtp, int smtpPort, String... domains) {
        for (String d : domains) KNOWN.put(d, Result.of(imap, smtp, smtpPort));
    }

    private static void mx(String re, String imap, String smtp, int smtpPort) {
        MX_PROVIDERS.add(new Object[]{Pattern.compile(re, Pattern.CASE_INSENSITIVE), Result.of(imap, smtp, smtpPort)});
    }

    private static Result copy(Result r) {
        Result c = Result.of(r.imapHost, r.smtpHost, r.smtpPort);
        c.imapPort = r.imapPort;
        c.imapSsl = r.imapSsl;
        return c;
    }

    static Result discover(String email) throws MailApi.ApiException {
        String domain = email.substring(email.indexOf('@') + 1).toLowerCase(Locale.ROOT).trim();
        if (KNOWN.containsKey(domain)) return copy(KNOWN.get(domain));

        ExecutorService pool = Executors.newFixedThreadPool(16);
        try {
            // 1. Autoconfig files
            String enc = urlEncode(email);
            List<Future<String>> ac = new ArrayList<>();
            ac.add(pool.submit(() -> fetch("https://autoconfig." + domain + "/mail/config-v1.1.xml?emailaddress=" + enc)));
            ac.add(pool.submit(() -> fetch("https://" + domain + "/.well-known/autoconfig/mail/config-v1.1.xml")));
            ac.add(pool.submit(() -> fetch("https://autoconfig.thunderbird.net/v1.1/" + domain)));
            Future<List<String>> mxF = pool.submit(() -> dns(domain, "MX"));
            for (Future<String> f : ac) {
                Result r = parseAutoconfig(get(f, 8), email);
                if (r != null) return r;
            }

            // 2. MX records
            List<String> mxHosts = get(mxF, 8);
            if (mxHosts == null) mxHosts = Collections.emptyList();
            for (String m : mxHosts) {
                Matcher z = Pattern.compile("zoho\\.(com|eu|in)$").matcher(m);
                if (z.find()) return Result.of("imappro.zoho." + z.group(1), "smtppro.zoho." + z.group(1), 465);
                for (Object[] p : MX_PROVIDERS) {
                    if (((Pattern) p[0]).matcher(m).find()) return copy((Result) p[1]);
                }
                String[] parts = m.split("\\.");
                if (parts.length >= 2) {
                    String base = parts[parts.length - 2] + "." + parts[parts.length - 1];
                    if (!base.equals(domain)) {
                        Result r = parseAutoconfig(fetch("https://autoconfig.thunderbird.net/v1.1/" + base), email);
                        if (r != null) return r;
                    }
                }
            }

            // 3. SRV records + common hostnames (cPanel: mail.domain)
            Future<List<String>> srvI = pool.submit(() -> dns("_imaps._tcp." + domain, "SRV"));
            Future<List<String>> srvS = pool.submit(() -> dns("_submission._tcp." + domain, "SRV"));
            List<String> si = get(srvI, 6), ss = get(srvS, 6);
            Set<String> imapHosts = new LinkedHashSet<>(), smtpHosts = new LinkedHashSet<>();
            if (si != null) imapHosts.addAll(si);
            if (ss != null) smtpHosts.addAll(ss);
            imapHosts.add("mail." + domain); imapHosts.add("imap." + domain);
            smtpHosts.add("mail." + domain); smtpHosts.add("smtp." + domain);
            imapHosts.addAll(mxHosts); smtpHosts.addAll(mxHosts);
            imapHosts.add(domain); smtpHosts.add(domain);

            Probe imap = findWorking(pool, new ArrayList<>(imapHosts), true);
            if (imap == null) {
                throw new MailApi.ApiException(502, "Could not find the mail server for this domain. Open \"Manual settings\" and enter the IMAP/SMTP server.");
            }
            Probe smtp = findWorking(pool, new ArrayList<>(smtpHosts), false);
            Result r = new Result();
            r.imapHost = imap.host; r.imapPort = imap.port; r.imapSsl = imap.ssl;
            if (smtp != null) {
                r.smtpHost = smtp.host; r.smtpPort = smtp.port; r.smtpSsl = smtp.ssl;
            } else {
                r.smtpHost = imap.host; r.smtpPort = 465; r.smtpSsl = true;
            }
            r.insecure = (imap.ssl && !imap.certValid) || (smtp != null && smtp.ssl && !smtp.certValid);
            return r;
        } finally {
            pool.shutdownNow();
        }
    }

    // ---------- Autoconfig ----------
    static Result parseAutoconfig(String xml, String email) {
        if (xml == null || !xml.contains("clientConfig")) return null;
        String local = email.substring(0, email.indexOf('@'));
        String domain = email.substring(email.indexOf('@') + 1);
        List<String[]> imap = servers(xml, "incoming", "imap"), smtp = servers(xml, "outgoing", "smtp");
        if (imap.isEmpty() || smtp.isEmpty()) return null;
        String[] i = best(imap), s = best(smtp);
        Result r = new Result();
        r.imapHost = i[0].replace("%EMAILDOMAIN%", domain).replace("%EMAILLOCALPART%", local);
        r.imapPort = parseInt(i[1], 993);
        r.imapSsl = "SSL".equals(i[2]);
        r.smtpHost = s[0].replace("%EMAILDOMAIN%", domain).replace("%EMAILLOCALPART%", local);
        r.smtpPort = parseInt(s[1], 465);
        r.smtpSsl = "SSL".equals(s[2]);
        r.localUser = i[3] != null && i[3].contains("%EMAILLOCALPART%");
        return r;
    }

    /** Returns [hostname, port, socketType, username] for each server of the given kind. */
    private static List<String[]> servers(String xml, String tag, String type) {
        List<String[]> out = new ArrayList<>();
        Matcher m = Pattern.compile("<" + tag + "Server[^>]*type=\"" + type + "\"[^>]*>([\\s\\S]*?)</" + tag + "Server>",
                Pattern.CASE_INSENSITIVE).matcher(xml);
        while (m.find()) {
            String body = m.group(1);
            String host = tagValue(body, "hostname");
            if (host == null || host.isEmpty()) continue;
            String sock = tagValue(body, "socketType");
            out.add(new String[]{host, tagValue(body, "port"), sock == null ? "" : sock.toUpperCase(Locale.ROOT), tagValue(body, "username")});
        }
        return out;
    }

    private static String[] best(List<String[]> list) {
        for (String[] s : list) if ("SSL".equals(s[2])) return s;
        for (String[] s : list) if ("STARTTLS".equals(s[2])) return s;
        return list.get(0);
    }

    private static String tagValue(String body, String name) {
        Matcher m = Pattern.compile("<" + name + ">([^<]*)</" + name + ">", Pattern.CASE_INSENSITIVE).matcher(body);
        return m.find() ? m.group(1).trim() : null;
    }

    private static int parseInt(String s, int def) {
        try { return Integer.parseInt(s.trim()); } catch (Exception e) { return def; }
    }

    // ---------- Network helpers ----------
    private static String urlEncode(String s) {
        try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return s; }
    }

    private static <T> T get(Future<T> f, int seconds) {
        try { return f.get(seconds, TimeUnit.SECONDS); } catch (Exception e) { f.cancel(true); return null; }
    }

    static String fetch(String url) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(5000);
            c.setReadTimeout(5000);
            c.setRequestProperty("Accept", "application/dns-json, text/xml, */*");
            if (c.getResponseCode() != 200) return null;
            try (InputStream in = c.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0 && out.size() < 1_000_000) out.write(buf, 0, n);
                return out.toString("UTF-8");
            }
        } catch (Exception e) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** DNS lookup over HTTPS (Android has no built-in MX/SRV lookup). Returns host names. */
    static List<String> dns(String name, String type) {
        List<String> hosts = new ArrayList<>();
        String q = "?name=" + urlEncode(name) + "&type=" + type;
        String json = fetch("https://dns.google/resolve" + q);
        if (json == null) json = fetch("https://cloudflare-dns.com/dns-query" + q);
        if (json == null) return hosts;
        try {
            JSONArray ans = new JSONObject(json).optJSONArray("Answer");
            if (ans == null) return hosts;
            List<long[]> order = new ArrayList<>();
            List<String> names = new ArrayList<>();
            int wantType = "MX".equals(type) ? 15 : 33;
            for (int i = 0; i < ans.length(); i++) {
                JSONObject a = ans.getJSONObject(i);
                if (a.optInt("type") != wantType) continue;
                String[] p = a.optString("data").trim().split("\\s+");
                String host = p[p.length - 1].toLowerCase(Locale.ROOT);
                if (host.endsWith(".")) host = host.substring(0, host.length() - 1);
                if (host.isEmpty()) continue;
                order.add(new long[]{parseInt(p[0], 99), names.size()});
                names.add(host);
            }
            Collections.sort(order, (x, y) -> Long.compare(x[0], y[0]));
            for (long[] o : order) hosts.add(names.get((int) o[1]));
        } catch (Exception ignored) {
        }
        return hosts;
    }

    // ---------- Probing ----------
    static final class Probe {
        String host;
        int port;
        boolean ssl, certValid;
        int rank;
    }

    private static Probe findWorking(ExecutorService pool, List<String> hosts, boolean imap) {
        int[][] combos = imap ? new int[][]{{993, 1}, {143, 0}} : new int[][]{{465, 1}, {587, 0}};
        List<Future<Probe>> tries = new ArrayList<>();
        for (int h = 0; h < hosts.size(); h++) {
            for (int c = 0; c < combos.length; c++) {
                final String host = hosts.get(h);
                final int port = combos[c][0], rank = h * 10 + c;
                final boolean ssl = combos[c][1] == 1;
                Callable<Probe> task = () -> probe(host, port, ssl, imap, rank);
                tries.add(pool.submit(task));
            }
        }
        Probe best = null;
        for (Future<Probe> f : tries) {
            Probe p = get(f, 15);
            if (p == null) continue;
            if (best == null
                    || (p.certValid && !best.certValid)
                    || (p.certValid == best.certValid && p.rank < best.rank)) best = p;
        }
        return best;
    }

    private static Probe probe(String host, int port, boolean ssl, boolean imap, int rank) {
        Socket s = null;
        try {
            boolean valid = false;
            if (ssl) {
                try {
                    s = tls(host, port, true);
                    valid = true;
                } catch (Exception e) {
                    s = tls(host, port, false);
                }
            } else {
                s = new Socket();
                s.connect(new InetSocketAddress(host, port), 6000);
                s.setSoTimeout(6000);
            }
            Pattern greet = imap ? Pattern.compile("^\\* (OK|PREAUTH)", Pattern.MULTILINE) : Pattern.compile("^220[ -]", Pattern.MULTILINE);
            InputStream in = s.getInputStream();
            StringBuilder sb = new StringBuilder();
            byte[] buf = new byte[512];
            int n;
            while (sb.length() < 2048 && (n = in.read(buf)) > 0) {
                sb.append(new String(buf, 0, n, StandardCharsets.ISO_8859_1));
                if (greet.matcher(sb).find()) {
                    Probe p = new Probe();
                    p.host = host; p.port = port; p.ssl = ssl; p.certValid = !ssl || valid; p.rank = rank;
                    return p;
                }
            }
            return null;
        } catch (Exception e) {
            return null;
        } finally {
            try { if (s != null) s.close(); } catch (Exception ignored) { }
        }
    }

    private static Socket tls(String host, int port, boolean validate) throws Exception {
        Socket raw = new Socket();
        raw.connect(new InetSocketAddress(host, port), 6000);
        SSLSocketFactory f = validate ? (SSLSocketFactory) SSLSocketFactory.getDefault() : trustAll();
        SSLSocket s = (SSLSocket) f.createSocket(raw, host, port, true);
        s.setSoTimeout(6000);
        s.startHandshake();
        if (validate && !HttpsURLConnection.getDefaultHostnameVerifier().verify(host, s.getSession())) {
            s.close();
            throw new javax.net.ssl.SSLPeerUnverifiedException("Certificate does not match " + host);
        }
        return s;
    }

    private static SSLSocketFactory trustAll() throws Exception {
        TrustManager[] tm = {new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String a) { }
            public void checkServerTrusted(X509Certificate[] c, String a) { }
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        }};
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, tm, new SecureRandom());
        return ctx.getSocketFactory();
    }
}
