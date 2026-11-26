// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.Context;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/** Client identity (self-signed RSA cert, as the Android TV Remote protocol
 *  requires) and TLS sockets to TVs, whose certificates are self-signed.
 *
 *  TV certificates can't be checked against a CA, so they are pinned on first use: the
 *  remote (6466/6467) and Samsung (8002) keep the certificate seen the first time and refuse
 *  any other one afterwards (a man in the middle on the Wi-Fi), until the TV is paired again
 *  or forgotten (forget). Cast (8009) is the exception: Chromecast devices replace their
 *  TLS certificate on their own every day or so, so it can't be pinned. */
final class Tls {
    private static PrivateKey key;
    private static X509Certificate cert;
    private static File pinFile;                                   // host:port=sha256 per line
    private static final java.util.Properties PINS = new java.util.Properties();
    static final int CAST_PORT = 8009;

    /** Where the pins live; called by Atv and Samsung when they are created. */
    static synchronized void init(Context ctx) {
        if (pinFile != null) return;
        pinFile = new File(ctx.getFilesDir(), "tls-pins.properties");
        try (java.io.FileInputStream in = new java.io.FileInputStream(pinFile)) {
            PINS.load(in);
        } catch (IOException ignored) {                            // first run: no pins yet
        }
    }

    /** Pairing again / forgetting a TV: its certificates may be new, learn them again. */
    static synchronized void forget(String host) {
        PINS.keySet().removeIf(k -> ((String) k).startsWith(host + ":"));
        savePins();
    }

    private static synchronized void savePins() {
        if (pinFile == null) return;
        try (java.io.FileOutputStream out = new java.io.FileOutputStream(pinFile)) {
            PINS.store(out, "Fling: TV certificate pins (SHA-256)");
        } catch (IOException ignored) {
        }
    }

    private static synchronized void checkPin(String host, int port, X509Certificate c)
            throws java.security.cert.CertificateException {
        String fp;
        try {
            byte[] d = java.security.MessageDigest.getInstance("SHA-256").digest(c.getEncoded());
            StringBuilder b = new StringBuilder();
            for (byte x : d) b.append(String.format(Locale.ROOT, "%02x", x));
            fp = b.toString();
        } catch (Exception e) {
            throw new java.security.cert.CertificateException(e);
        }
        String k = host + ":" + port, known = PINS.getProperty(k);
        if (known == null) {
            PINS.setProperty(k, fp);                               // trust on first use
            savePins();
        } else if (!known.equals(fp)) {
            throw new java.security.cert.CertificateException(
                    "the TV's certificate changed: pair the TV again (or forget it and add it again)");
        }
    }

    static synchronized void ensureIdentity(Context ctx) throws Exception {
        if (cert != null) return;
        File kf = new File(ctx.getFilesDir(), "client.key"), cf = new File(ctx.getFilesDir(), "client.crt");
        if (kf.exists() && cf.exists()) {
            key = KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(Files.readAllBytes(kf.toPath())));
            cert = (X509Certificate) CertificateFactory.getInstance("X.509")
                    .generateCertificate(new ByteArrayInputStream(Files.readAllBytes(cf.toPath())));
            return;
        }
        KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
        g.initialize(2048, new SecureRandom());
        KeyPair kp = g.generateKeyPair();
        byte[] der = selfSigned(kp, "atvremote");
        Files.write(kf.toPath(), kp.getPrivate().getEncoded());
        Files.write(cf.toPath(), der);
        key = kp.getPrivate();
        cert = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(der));
    }

    static RSAPublicKey clientPublic() { return (RSAPublicKey) cert.getPublicKey(); }

    static SSLSocket connect(String host, int port, boolean withClientCert, int timeoutMs) throws Exception {
        SSLContext ctx = SSLContext.getInstance("TLS");
        KeyManagerFactory kmf = null;
        if (withClientCert) {
            KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
            ks.load(null, null);
            ks.setKeyEntry("me", key, new char[0], new Certificate[]{cert});
            kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
            kmf.init(ks, new char[0]);
        }
        TrustManager[] pinned = {new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String a)
                    throws java.security.cert.CertificateException {
                throw new java.security.cert.CertificateException("Fling is the client here");
            }
            public void checkServerTrusted(X509Certificate[] c, String a)
                    throws java.security.cert.CertificateException {
                if (c == null || c.length == 0)
                    throw new java.security.cert.CertificateException("the TV sent no certificate");
                if (port != CAST_PORT) checkPin(host, port, c[0]);  // Cast rotates its certificate
            }
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        }};
        ctx.init(kmf == null ? null : kmf.getKeyManagers(), pinned, new SecureRandom());
        java.net.Socket raw = new java.net.Socket();
        raw.connect(new InetSocketAddress(host, port), timeoutMs);
        raw.setTcpNoDelay(true);
        SSLSocket s = (SSLSocket) ctx.getSocketFactory().createSocket(raw, host, port, true);
        s.setSoTimeout(timeoutMs);
        s.startHandshake();
        s.setSoTimeout(0);
        return s;
    }

    // ---- minimal DER encoder for a v3 self-signed certificate

    private static byte[] tlv(int tag, byte[] content) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        o.write(tag);
        int n = content.length;
        if (n < 0x80) o.write(n);
        else if (n < 0x100) { o.write(0x81); o.write(n); }
        else if (n < 0x10000) { o.write(0x82); o.write(n >> 8); o.write(n & 0xFF); }
        else { o.write(0x83); o.write(n >> 16); o.write((n >> 8) & 0xFF); o.write(n & 0xFF); }
        o.write(content, 0, n);
        return o.toByteArray();
    }

    private static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        for (byte[] p : parts) o.write(p, 0, p.length);
        return o.toByteArray();
    }

    private static byte[] seq(byte[]... parts) { return tlv(0x30, cat(parts)); }

    private static byte[] time(Date d) {
        SimpleDateFormat f = new SimpleDateFormat("yyMMddHHmmss'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return tlv(0x17, f.format(d).getBytes());
    }

    private static byte[] selfSigned(KeyPair kp, String cn) throws Exception {
        byte[] sha256Rsa = seq(tlv(0x06, new byte[]{0x2A, (byte) 0x86, 0x48, (byte) 0x86, (byte) 0xF7, 0x0D, 0x01, 0x01, 0x0B}),
                new byte[]{0x05, 0x00});
        byte[] name = seq(tlv(0x31, seq(tlv(0x06, new byte[]{0x55, 0x04, 0x03}), tlv(0x0C, cn.getBytes()))));
        long now = System.currentTimeMillis();
        byte[] validity = seq(time(new Date(now - 86400000L)), time(new Date(now + 20L * 365 * 86400000L)));
        byte[] serial = tlv(0x02, new BigInteger(63, new SecureRandom()).add(BigInteger.ONE).toByteArray());
        byte[] version = tlv(0xA0, tlv(0x02, new byte[]{2}));
        byte[] tbs = seq(version, serial, sha256Rsa, name, validity, name, kp.getPublic().getEncoded());
        Signature sig = Signature.getInstance("SHA256withRSA");
        sig.initSign(kp.getPrivate());
        sig.update(tbs);
        byte[] s = sig.sign();
        byte[] bits = new byte[s.length + 1];
        System.arraycopy(s, 0, bits, 1, s.length);
        return seq(tbs, sha256Rsa, tlv(0x03, bits));
    }

    /** Minimal big-endian magnitude bytes, as the pairing hash expects. */
    static byte[] unsigned(BigInteger v) {
        byte[] b = v.toByteArray();
        if (b.length > 1 && b[0] == 0) {
            byte[] t = new byte[b.length - 1];
            System.arraycopy(b, 1, t, 0, t.length);
            return t;
        }
        return b;
    }

    static void closeQuietly(java.io.Closeable c) {
        if (c != null) try { c.close(); } catch (IOException ignored) {}
    }
}
