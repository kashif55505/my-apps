package com.personal.webmail;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Saved email accounts. The list (including passwords) is encrypted with an AES key that lives in the
 * Android Keystore, so it can only be read by this app on this phone.
 */
final class AccountStore {

    static final class Account {
        String email = "";
        String user = "";
        String pass = "";
        String imapHost = "";
        int imapPort = 993;
        boolean imapSsl = true;
        String smtpHost = "";
        int smtpPort = 465;
        boolean smtpSsl = true;
        boolean insecure = false;   // server certificate does not match its name (common on shared cPanel)
        boolean notify = true;

        String id() {
            return email.toLowerCase(Locale.ROOT);
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject()
                    .put("email", email).put("user", user).put("pass", pass)
                    .put("imapHost", imapHost).put("imapPort", imapPort).put("imapSsl", imapSsl)
                    .put("smtpHost", smtpHost).put("smtpPort", smtpPort).put("smtpSsl", smtpSsl)
                    .put("insecure", insecure).put("notify", notify);
        }

        static Account fromJson(JSONObject j) {
            Account a = new Account();
            a.email = j.optString("email");
            a.user = j.optString("user", a.email);
            a.pass = j.optString("pass");
            a.imapHost = j.optString("imapHost");
            a.imapPort = j.optInt("imapPort", 993);
            a.imapSsl = j.optBoolean("imapSsl", true);
            a.smtpHost = j.optString("smtpHost");
            a.smtpPort = j.optInt("smtpPort", 465);
            a.smtpSsl = j.optBoolean("smtpSsl", true);
            a.insecure = j.optBoolean("insecure", false);
            a.notify = j.optBoolean("notify", true);
            return a;
        }

        /** What the interface is allowed to see (no password). */
        JSONObject publicJson() throws JSONException {
            return new JSONObject()
                    .put("email", email)
                    .put("notify", notify)
                    .put("server", new JSONObject()
                            .put("imap", imapHost + ":" + imapPort)
                            .put("smtp", smtpHost + ":" + smtpPort));
        }
    }

    private static AccountStore instance;
    private final SharedPreferences prefs;
    private final List<Account> accounts = new ArrayList<>();

    static synchronized AccountStore get(Context ctx) {
        if (instance == null) instance = new AccountStore(ctx.getApplicationContext());
        return instance;
    }

    private AccountStore(Context ctx) {
        prefs = ctx.getSharedPreferences("accounts", Context.MODE_PRIVATE);
        String blob = prefs.getString("data", null);
        if (blob != null) {
            try {
                JSONArray arr = new JSONArray(Crypto.decrypt(blob));
                for (int i = 0; i < arr.length(); i++) accounts.add(Account.fromJson(arr.getJSONObject(i)));
            } catch (Exception e) {
                // Key lost (e.g. app data restored to another phone): start fresh
                accounts.clear();
            }
        }
    }

    synchronized List<Account> all() {
        return new ArrayList<>(accounts);
    }

    synchronized boolean isEmpty() {
        return accounts.isEmpty();
    }

    synchronized Account find(String id) {
        if (id == null) return null;
        String want = id.toLowerCase(Locale.ROOT);
        for (Account a : accounts) if (a.id().equals(want)) return a;
        return null;
    }

    synchronized Account first() {
        return accounts.isEmpty() ? null : accounts.get(0);
    }

    /** Adds the account, or replaces the one with the same address. */
    synchronized void put(Account acc) {
        for (int i = 0; i < accounts.size(); i++) {
            if (accounts.get(i).id().equals(acc.id())) {
                accounts.set(i, acc);
                save();
                return;
            }
        }
        accounts.add(acc);
        save();
    }

    synchronized void remove(String id) {
        for (int i = accounts.size() - 1; i >= 0; i--) if (accounts.get(i).id().equals(id)) accounts.remove(i);
        save();
    }

    synchronized void clear() {
        accounts.clear();
        save();
    }

    synchronized void save() {
        try {
            JSONArray arr = new JSONArray();
            for (Account a : accounts) arr.put(a.toJson());
            prefs.edit().putString("data", Crypto.encrypt(arr.toString())).apply();
        } catch (Exception e) {
            throw new RuntimeException("Could not save accounts", e);
        }
    }

    /** AES-GCM with a key that never leaves the Android Keystore. */
    static final class Crypto {
        private static final String ALIAS = "webmail_accounts";

        private static SecretKey key() throws Exception {
            KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
            ks.load(null);
            if (ks.containsAlias(ALIAS)) {
                return ((KeyStore.SecretKeyEntry) ks.getEntry(ALIAS, null)).getSecretKey();
            }
            KeyGenerator kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            kg.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build());
            return kg.generateKey();
        }

        static String encrypt(String plain) throws Exception {
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.ENCRYPT_MODE, key());
            byte[] iv = c.getIV();
            byte[] ct = c.doFinal(plain.getBytes(StandardCharsets.UTF_8));
            ByteBuffer buf = ByteBuffer.allocate(1 + iv.length + ct.length);
            buf.put((byte) iv.length).put(iv).put(ct);
            return Base64.encodeToString(buf.array(), Base64.NO_WRAP);
        }

        static String decrypt(String blob) throws Exception {
            ByteBuffer buf = ByteBuffer.wrap(Base64.decode(blob, Base64.NO_WRAP));
            byte[] iv = new byte[buf.get()];
            buf.get(iv);
            byte[] ct = new byte[buf.remaining()];
            buf.get(ct);
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
            return new String(c.doFinal(ct), StandardCharsets.UTF_8);
        }
    }
}
