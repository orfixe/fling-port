// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.os.Build;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.security.interfaces.RSAPublicKey;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/** Wireless debugging (Android 11+): pairing with the TV's 6-digit code, the TLS that the
 *  paired connection then speaks, and finding both ports over mDNS. What `adb pair` and
 *  `adb connect` do on a computer — so a TV works with Fling alone, no PC and no port 5555.
 *  Protocol: AOSP packages/modules/adb pairing_connection + pairing_auth, SPAKE2 in Spake2. */
final class AdbPair {
    private AdbPair() {}

    private static final byte[] CLIENT = "adb pair client\0".getBytes(StandardCharsets.US_ASCII);
    private static final byte[] SERVER = "adb pair server\0".getBytes(StandardCharsets.US_ASCII);
    private static final int PEER_INFO_SIZE = 8192;

    // ------------------------------------------------------------ the certificate (our ADB key)

    private static volatile SSLContext tls;

    /** TLS with our ADB RSA key as the client certificate — the TV recognises the key it paired
     *  with; the TV's own certificate isn't checked (adb doesn't either: the pairing is the trust). */
    static synchronized SSLContext tls(PrivateKey priv, RSAPublicKey pub) throws Exception {
        if (tls != null) return tls;
        X509Certificate cert = selfSigned(priv, pub);
        // adbd lists its known keys as "certificate authorities"; the stock key manager would then
        // offer nothing (PEER_DID_NOT_RETURN_A_CERTIFICATE) — always present our ADB key instead
        final X509Certificate[] chain = {cert};
        javax.net.ssl.X509ExtendedKeyManager km = new javax.net.ssl.X509ExtendedKeyManager() {
            public String[] getClientAliases(String t, java.security.Principal[] i) { return new String[]{"adb"}; }
            public String chooseClientAlias(String[] t, java.security.Principal[] i, Socket s) { return "adb"; }
            public String chooseEngineClientAlias(String[] t, java.security.Principal[] i, javax.net.ssl.SSLEngine e) { return "adb"; }
            public String[] getServerAliases(String t, java.security.Principal[] i) { return null; }
            public String chooseServerAlias(String t, java.security.Principal[] i, Socket s) { return null; }
            public X509Certificate[] getCertificateChain(String a) { return chain; }
            public PrivateKey getPrivateKey(String a) { return priv; }
        };
        TrustManager trustAll = new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String a) {}
            public void checkServerTrusted(X509Certificate[] c, String a) {}
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        };
        SSLContext c = SSLContext.getInstance("TLSv1.3");
        c.init(new javax.net.ssl.KeyManager[]{km}, new TrustManager[]{trustAll}, null);
        return tls = c;
    }

    /** TLS 1.3 over an open socket (the STLS step of a paired connection, or pairing itself). */
    static SSLSocket wrap(SSLContext c, Socket s, String host, int port) throws IOException {
        SSLSocket t = (SSLSocket) c.getSocketFactory().createSocket(s, host, port, true);
        t.setUseClientMode(true);
        t.setEnabledProtocols(new String[]{"TLSv1.3"});
        t.startHandshake();
        return t;
    }

    /** A minimal self-signed X.509 v3 certificate, DER by hand (CN=fling, sha256WithRSA). */
    private static X509Certificate selfSigned(PrivateKey priv, RSAPublicKey pub) throws Exception {
        byte[] alg = seq(oid(new int[]{1, 2, 840, 113549, 1, 1, 11}), new byte[]{0x05, 0x00});
        byte[] name = seq(set(seq(oid(new int[]{2, 5, 4, 3}), tlv(0x0c, "fling".getBytes(StandardCharsets.US_ASCII)))));
        byte[] validity = seq(tlv(0x17, "250101000000Z".getBytes(StandardCharsets.US_ASCII)),
                tlv(0x18, "20500101000000Z".getBytes(StandardCharsets.US_ASCII)));
        byte[] tbs = seq(tlv(0xa0, tlv(0x02, new byte[]{2})), tlv(0x02, new byte[]{1}), alg, name, validity, name, pub.getEncoded());
        Signature s = Signature.getInstance("SHA256withRSA");
        s.initSign(priv);
        s.update(tbs);
        byte[] sig = s.sign(), bits = new byte[sig.length + 1];
        System.arraycopy(sig, 0, bits, 1, sig.length);
        byte[] der = seq(tbs, alg, tlv(0x03, bits));
        return (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(new java.io.ByteArrayInputStream(der));
    }

    private static byte[] tlv(int tag, byte[] v) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        o.write(tag);
        int n = v.length;
        if (n < 128) o.write(n);
        else if (n < 256) { o.write(0x81); o.write(n); }
        else if (n < 65536) { o.write(0x82); o.write(n >> 8); o.write(n & 0xff); }
        else { o.write(0x83); o.write(n >> 16); o.write((n >> 8) & 0xff); o.write(n & 0xff); }
        o.write(v, 0, n);
        return o.toByteArray();
    }

    private static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : parts) o.write(p, 0, p.length);
        return o.toByteArray();
    }

    private static byte[] seq(byte[]... parts) { return tlv(0x30, cat(parts)); }
    private static byte[] set(byte[]... parts) { return tlv(0x31, cat(parts)); }

    private static byte[] oid(int[] arcs) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        o.write(arcs[0] * 40 + arcs[1]);
        for (int i = 2; i < arcs.length; i++) {
            int v = arcs[i];
            byte[] tmp = new byte[5];
            int n = 0;
            do { tmp[n++] = (byte) (v & 0x7f); v >>>= 7; } while (v != 0);
            for (int j = n - 1; j >= 0; j--) o.write(tmp[j] | (j > 0 ? 0x80 : 0));
        }
        return tlv(0x06, o.toByteArray());
    }

    // ------------------------------------------------------------ pairing

    /** Pairs this phone's ADB key with the TV: host:port from "Pair device with pairing code".
     *  Throws with a readable message (a wrong code closes the connection on the TV's side). */
    static void pair(SSLContext c, String host, int port, String code, byte[] adbPubKey) throws Exception {
        if (Build.VERSION.SDK_INT < 29) throw new IOException(L.t("Сопряжение по коду требует Android 10 или новее."));
        Socket raw = new Socket();
        raw.connect(new InetSocketAddress(InetAddress.getByName(host), port), 4000);
        raw.setSoTimeout(15000);
        try (SSLSocket s = wrap(c, raw, host, port)) {
            byte[] ekm = android.net.ssl.SSLSockets.exportKeyingMaterial(s, "adb-label\0", null, 64);
            if (ekm == null) throw new IOException("TLS exporter");
            byte[] pw = cat(code.trim().getBytes(StandardCharsets.US_ASCII), ekm);
            Spake2 sp = new Spake2(true, CLIENT, SERVER);
            byte[] mine = sp.generateMsg(pw);
            OutputStream out = s.getOutputStream();
            DataInputStream in = new DataInputStream(s.getInputStream());
            writePacket(out, 0, mine);                                            // SPAKE2_MSG
            byte[] theirs = readPacket(in, 0);
            byte[] key = sp.processMsg(theirs);
            if (key == null) throw new IOException(L.t("Код не подошёл — проверьте его на экране ТВ."));
            byte[] aes = hkdf(key, "adb pairing_auth aes-128-gcm key".getBytes(StandardCharsets.US_ASCII), 16);

            byte[] info = new byte[PEER_INFO_SIZE];                               // type 0: ADB_RSA_PUB_KEY
            System.arraycopy(adbPubKey, 0, info, 1, Math.min(adbPubKey.length, PEER_INFO_SIZE - 1));
            writePacket(out, 1, gcm(Cipher.ENCRYPT_MODE, aes, info));             // PEER_INFO
            byte[] reply;
            try { reply = readPacket(in, 1); }
            catch (IOException e) { throw new IOException(L.t("Код не подошёл — проверьте его на экране ТВ.")); }
            try { gcm(Cipher.DECRYPT_MODE, aes, reply); }
            catch (Exception e) { throw new IOException(L.t("Код не подошёл — проверьте его на экране ТВ.")); }
        }
    }

    private static void writePacket(OutputStream o, int type, byte[] payload) throws IOException {
        ByteBuffer h = ByteBuffer.allocate(6).order(ByteOrder.BIG_ENDIAN);
        h.put((byte) 1).put((byte) type).putInt(payload.length);
        o.write(h.array());
        o.write(payload);
        o.flush();
    }

    private static byte[] readPacket(DataInputStream in, int type) throws IOException {
        byte[] h = new byte[6];
        in.readFully(h);
        ByteBuffer b = ByteBuffer.wrap(h).order(ByteOrder.BIG_ENDIAN);
        int version = b.get(), t = b.get(), len = b.getInt();
        if (version != 1 || t != type || len <= 0 || len > PEER_INFO_SIZE * 2) throw new IOException("pairing packet");
        byte[] p = new byte[len];
        in.readFully(p);
        return p;
    }

    /** AES-128-GCM with the nonce = the message counter (0 for the one message each way). */
    private static byte[] gcm(int mode, byte[] key, byte[] data) throws Exception {
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(mode, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, new byte[12]));
        return c.doFinal(data);
    }

    /** HKDF-SHA256 with no salt (RFC 5869: a zero salt of hash length). */
    private static byte[] hkdf(byte[] ikm, byte[] info, int len) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(new byte[32], "HmacSHA256"));
        byte[] prk = mac.doFinal(ikm);
        mac.init(new SecretKeySpec(prk, "HmacSHA256"));
        byte[] out = new byte[len], t = new byte[0];
        int done = 0;
        for (int i = 1; done < len; i++) {
            mac.update(t);
            mac.update(info);
            mac.update((byte) i);
            t = mac.doFinal();
            int n = Math.min(t.length, len - done);
            System.arraycopy(t, 0, out, done, n);
            done += n;
        }
        return out;
    }

    // ------------------------------------------------------------ ports over mDNS

    /** host → the TV's "connect" port (TLS, changes when Wireless debugging is turned on again). */
    static final Map<String, Integer> connectPorts = new ConcurrentHashMap<>();
    /** host → the port of the open "Pair device with pairing code" window. */
    static final Map<String, Integer> pairPorts = new ConcurrentHashMap<>();

    private static SharedPreferences prefs(Context c) { return c.getSharedPreferences("adb-tls", Context.MODE_PRIVATE); }

    static int connectPort(Context c, String host) {
        Integer p = connectPorts.get(host);
        return p != null ? p : prefs(c).getInt(host, 0);
    }

    private static NsdManager.DiscoveryListener connectL, pairL;

    /** Watches the Wi‑Fi for _adb-tls-connect and _adb-tls-pairing (until stop()). */
    static synchronized void start(Context ctx) {
        if (connectL != null) return;
        Context c = ctx.getApplicationContext();
        NsdManager nsd = c.getSystemService(NsdManager.class);
        connectL = listener(c, nsd, connectPorts, true);
        pairL = listener(c, nsd, pairPorts, false);
        try { nsd.discoverServices("_adb-tls-connect._tcp", NsdManager.PROTOCOL_DNS_SD, connectL); } catch (Exception ignored) {}
        try { nsd.discoverServices("_adb-tls-pairing._tcp", NsdManager.PROTOCOL_DNS_SD, pairL); } catch (Exception ignored) {}
    }

    static synchronized void stop(Context ctx) {
        NsdManager nsd = ctx.getApplicationContext().getSystemService(NsdManager.class);
        for (NsdManager.DiscoveryListener l : new NsdManager.DiscoveryListener[]{connectL, pairL})
            if (l != null) try { nsd.stopServiceDiscovery(l); } catch (Exception ignored) {}
        connectL = pairL = null;
    }

    private static NsdManager.DiscoveryListener listener(Context c, NsdManager nsd, Map<String, Integer> into, boolean keep) {
        return new NsdManager.DiscoveryListener() {
            public void onStartDiscoveryFailed(String t, int e) {}
            public void onStopDiscoveryFailed(String t, int e) {}
            public void onDiscoveryStarted(String t) {}
            public void onDiscoveryStopped(String t) {}
            public void onServiceLost(NsdServiceInfo s) {}
            @SuppressWarnings("deprecation")
            public void onServiceFound(NsdServiceInfo s) {
                try {
                    nsd.resolveService(s, new NsdManager.ResolveListener() {
                        public void onResolveFailed(NsdServiceInfo i, int e) {}
                        public void onServiceResolved(NsdServiceInfo i) {
                            InetAddress a = i.getHost();
                            if (a == null || a.getHostAddress() == null || a.getHostAddress().indexOf(':') >= 0) return;
                            String host = a.getHostAddress();
                            into.put(host, i.getPort());
                            if (keep) prefs(c).edit().putInt(host, i.getPort()).apply();
                        }
                    });
                } catch (Exception ignored) {}   // "listener already in use": the next sighting resolves it
            }
        };
    }
}
