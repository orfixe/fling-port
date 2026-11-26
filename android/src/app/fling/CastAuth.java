// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import java.io.ByteArrayInputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Date;
import java.util.List;

import javax.net.ssl.SSLSocket;

/** Cast device authentication: proves that the device on port 8009 is a real Cast device
 *  and that nobody sits between it and the phone.
 *
 *  A Chromecast's TLS certificate is self-signed and changes every day or so, so it can't be
 *  pinned. Instead (as Chrome and the Google Cast SDK do) Fling sends a random nonce on the
 *  deviceauth namespace; the device answers with its device certificate, issued by Google's
 *  Cast Root CA (or the older Eureka Root CA), and a signature over nonce + the TLS
 *  certificate it uses on this very connection. A man in the middle has neither a device key
 *  nor the device's TLS key, so the check fails for him.
 *
 *  Rules follow openscreen cast/sender/channel/cast_auth_util.cc (CRL optional, as there). */
final class CastAuth {
    private CastAuth() {}

    static final String NS_AUTH = "urn:x-cast:com.google.cast.tp.deviceauth";
    private static final long DAY = 24L * 3600 * 1000;
    private static final long SKEW = 10L * 60 * 1000;            // the phone clock a bit off

    // Cast Root CA (Google Inc, OU=Cast), 2014-04-02 .. 2034-03-28, SHA-256
    // 80:9A:F1:47:00:B3:FE:26:11:AD:59:7E:B1:58:4D:63:54:31:3B:64:CC:DB:33:90:F0:97:FA:3E:58:26:D6:CA
    private static final String CAST_ROOT =
            "MIIDxTCCAq2gAwIBAgIBAjANBgkqhkiG9w0BAQUFADB1MQswCQYDVQQGEwJVUzETMBEGA1UECAwK"
            + "Q2FsaWZvcm5pYTEWMBQGA1UEBwwNTW91bnRhaW4gVmlldzETMBEGA1UECgwKR29vZ2xlIEluYzEN"
            + "MAsGA1UECwwEQ2FzdDEVMBMGA1UEAwwMQ2FzdCBSb290IENBMB4XDTE0MDQwMjE3MzQyNloXDTM0"
            + "MDMyODE3MzQyNlowdTELMAkGA1UEBhMCVVMxEzARBgNVBAgMCkNhbGlmb3JuaWExFjAUBgNVBAcM"
            + "DU1vdW50YWluIFZpZXcxEzARBgNVBAoMCkdvb2dsZSBJbmMxDTALBgNVBAsMBENhc3QxFTATBgNV"
            + "BAMMDENhc3QgUm9vdCBDQTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALrZZZ3aOdPB"
            + "d/bU0K6PWAhoOUqV7XDP/XkIqarl6binLaBnR4qeyc9wswWHaRHscJiXw+bDw+u9xrA9/E/BXjif"
            + "2s9zMAZbeTfBXoyHR5SaQZIq1pXEcVwnXQixgMaSvRvjQZeh7HWfVZ4+n48cx2VkB9OzlqEEn5HE"
            + "3gp7bNnIwHgxoBlCqeiD48788c7CLiRGlQkZysBGsuUButdP87/2aa2ZBPqgBzkO5t9RRwfA5Klc"
            + "S5TFL7OgMH/nlWuyrzIN8YzVbct7R6cIq8sno03PSlrxBdH4YsUQKnRpquZLlvub2GPkWGbTrYpu"
            + "/3te+aVWHi2CMVvw4iTmQUofrhMCAwEAAaNgMF4wDwYDVR0TBAgwBgEB/wIBAjAdBgNVHQ4EFgQU"
            + "fJoefd95VLzXzF7KmYZFeWV0KBkwHwYDVR0jBBgwFoAUfJoefd95VLzXzF7KmYZFeWV0KBkwCwYD"
            + "VR0PBAQDAgEGMA0GCSqGSIb3DQEBBQUAA4IBAQCA9Fr7PSgZUSDX1PsSl0pl8lg1kncwavHXtlEa"
            + "f5rNx3sDQq1VagCv8OEGwr1reHXb/kERU0o5u5o6xlk0Lywz47LWXH/deOtxWznag5DFMeI/I+/a"
            + "6ystd17ew0PSyWtZgsrV7fqhZFvL8Q0aYuGc6KcYcPBfF5b47Ybbrh3gzz5dLu4WbZUrPP2X8wVa"
            + "JGhNObb45Fi69eAmeFHFW11OCeVsR4t6Wi6JU+bMNlsmPPhyQwKC0ivN8NOj7BM+UtWDPQfcHUNl"
            + "ejMCAaPOt9ZgUTsJwiOKMv6YGWBik4XNNEbb1SMPedp3ACoCbYNYzgN3NeGjIJPCSqKkRhx1LB9N";
    // Eureka Root CA (Google Inc, OU=Google TV), first Chromecasts, until 2032-12-12, SHA-256
    // CA:F6:D1:E3:7B:53:22:03:E0:1A:76:BF:07:18:7B:B7:31:CC:D3:88:01:56:5A:B2:21:1A:2C:0A:B7:F3:BC:46
    private static final String EUREKA_ROOT =
            "MIIDwzCCAqugAwIBAgIBATANBgkqhkiG9w0BAQUFADB8MQswCQYDVQQGEwJVUzETMBEGA1UECAwK"
            + "Q2FsaWZvcm5pYTEWMBQGA1UEBwwNTW91bnRhaW4gVmlldzETMBEGA1UECgwKR29vZ2xlIEluYzES"
            + "MBAGA1UECwwJR29vZ2xlIFRWMRcwFQYDVQQDDA5FdXJla2EgUm9vdCBDQTAeFw0xMjEyMTcyMjM5"
            + "MzNaFw0zMjEyMTIyMjM5MzNaMHwxCzAJBgNVBAYTAlVTMRMwEQYDVQQIDApDYWxpZm9ybmlhMRYw"
            + "FAYDVQQHDA1Nb3VudGFpbiBWaWV3MRMwEQYDVQQKDApHb29nbGUgSW5jMRIwEAYDVQQLDAlHb29n"
            + "bGUgVFYxFzAVBgNVBAMMDkV1cmVrYSBSb290IENBMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIB"
            + "CgKCAQEAuRHQ6hLcMuHfXDNrGXMdnZ7QOXa/pYQJpv1ubencjzZO6YgCvZ/06ET9TPWaAlZqRypj"
            + "bFhFzHxmJNx5ecMqpLKLoPeitc0Gftu+7AyG8g0kYHSEyikjhALYp+078ewmR1TjsS3mZA/2csXp"
            + "mFIXwPzyLCDIQPhHyTKeO5exi/WYJHBjZhnBUugEBT1fjbzYS693mG8feNG2UCdN5OwUaWcfWK+p"
            + "oBEmPJQyB3/X6Wkfrj9PY4qPidbyGXhcIY6xtlfYwOHufW7d8ToKavG6//mDL9y1pCAXYzbvyGIZ"
            + "zFbOsuoxiUt4WMG/AxOZ4BLyiKqblNrddnkXHjTRCsQHRQIDAQABo1AwTjAdBgNVHQ4EFgQURE4q"
            + "R1jYuUiR9k/OdKkdMpqNjekwHwYDVR0jBBgwFoAURE4qR1jYuUiR9k/OdKkdMpqNjekwDAYDVR0T"
            + "BAUwAwEB/zANBgkqhkiG9w0BAQUFAAOCAQEAP8gmoG5cBUB5oZipM95odIXurrccM1mwEd6f9E/T"
            + "61EJfUd+blGF9FTNg5glsbqwV+yT2xLi7FFJepZzm8iWbYWM0+E8+jLiWAx3bYcMNAGqMKl24MDn"
            + "214b6RAwpOAJSSa5WM1aB+VQdd6aO/ZTfrFTXkUnTxfjCDOyUAq79PwllyneQXUw+nc4qmWKc0/q"
            + "EXvrfBdgJw68PnZS2IvtGvjrN7sR/a5wFwr+4K0Gsx9pinIEwsAzC9YvY0wzERS4YjaIxQNlARmj"
            + "7wC7bw6S/zQcodYx0Fxen5l9x8q9fHIL9Fylfm4EqNKZLFEBFP6iSPB+voQNtNPi8w593ov1Mw==";

    private static List<X509Certificate> roots;

    static synchronized List<X509Certificate> roots() throws Exception {
        if (roots == null) {
            List<X509Certificate> r = new ArrayList<>();
            r.add(cert(Base64.getDecoder().decode(CAST_ROOT)));
            r.add(cert(Base64.getDecoder().decode(EUREKA_ROOT)));
            roots = r;
        }
        return roots;
    }

    private static X509Certificate cert(byte[] der) throws Exception {
        return (X509Certificate) CertificateFactory.getInstance("X.509")
                .generateCertificate(new ByteArrayInputStream(der));
    }

    /** The device failed the check: not a genuine Cast device, or a man in the middle. */
    static final class Failed extends IOException {
        Failed(String why) {
            super(L.t("Устройство не подтвердило, что это настоящий Chromecast, поэтому Fling не стал с ним соединяться.") + " (" + why + ")");
        }
    }

    /** Runs the challenge on a fresh connection, before anything else is sent. */
    static void authenticate(SSLSocket s, DataInputStream in, DataOutputStream out,
                             String sender, String receiver) throws Exception {
        byte[] nonce = new byte[16];
        new SecureRandom().nextBytes(nonce);
        byte[] challenge = new Proto.W().msg(1, new Proto.W()
                .varint(1, 1)                                       // RSASSA_PKCS1v15
                .bytes(2, nonce)
                .varint(3, 1)).out();                               // SHA256
        byte[] msg = new Proto.W().varint(1, 0).str(2, sender).str(3, receiver)
                .str(4, NS_AUTH).varint(5, 1).bytes(7, challenge).out();
        out.writeInt(msg.length);
        out.write(msg);
        out.flush();
        Proto.R reply = null;
        for (int i = 0; i < 16 && reply == null; i++) {             // skip anything sent before it
            int n = in.readInt();
            if (n <= 0 || n > 4 * 1024 * 1024) throw new IOException("bad frame");
            Proto.R m = new Proto.R(Proto.readFully(in, n));
            if (NS_AUTH.equals(m.str(4))) reply = m;
        }
        if (reply == null || reply.bytes(7) == null) throw new Failed("no deviceauth reply");
        X509Certificate peer = (X509Certificate) s.getSession().getPeerCertificates()[0];
        verify(new Proto.R(reply.bytes(7)), nonce, peer, System.currentTimeMillis());
    }

    /** The checks themselves (DeviceAuthMessage in `auth`). */
    static void verify(Proto.R auth, byte[] nonce, X509Certificate peer, long now) throws Exception {
        if (auth.has(3)) throw new Failed("the device reported an auth error");
        Proto.R r = auth.msg(2);
        if (r == null) throw new Failed("empty reply");
        byte[] sig = r.bytes(1), leafDer = r.bytes(2);
        if (sig == null || sig.length == 0 || leafDer == null) throw new Failed("no signature");

        // the TLS certificate only carries this signature: it has to be short-lived
        long nb = peer.getNotBefore().getTime(), na = peer.getNotAfter().getTime();
        if (now + SKEW < nb) throw new Failed("TLS certificate not valid yet");
        if (na < now - SKEW) throw new Failed("TLS certificate expired");
        if (na > now + 4 * DAY + SKEW) throw new Failed("TLS certificate lives too long");

        // newer devices echo the nonce and sign it; older ones sign the certificate alone
        byte[] echoed = r.bytes(5);
        if (echoed != null && !MessageDigest.isEqual(echoed, nonce)) throw new Failed("wrong nonce");

        X509Certificate leaf = cert(leafDer);
        List<X509Certificate> inter = new ArrayList<>();
        for (byte[] d : r.all(3)) inter.add(cert(d));
        chain(leaf, inter, new Date(now));

        Long hash = r.varint(6), alg = r.varint(4);
        boolean sha256 = hash != null && hash == 1;
        boolean pss = alg != null && alg == 2;
        String name = (sha256 ? "SHA256" : "SHA1") + "withRSA" + (pss ? "/PSS" : "");
        byte[] peerDer = peer.getEncoded();
        int k = echoed == null ? 0 : echoed.length;
        byte[] signed = new byte[k + peerDer.length];
        if (k > 0) System.arraycopy(echoed, 0, signed, 0, k);
        System.arraycopy(peerDer, 0, signed, k, peerDer.length);
        Signature v = Signature.getInstance(name);
        v.initVerify(leaf.getPublicKey());
        v.update(signed);
        if (!v.verify(sig)) throw new Failed("bad signature");
    }

    /** device certificate -> intermediates -> one of the Cast roots, each valid at `at`. */
    static void chain(X509Certificate leaf, List<X509Certificate> inter, Date at) throws Exception {
        boolean[] ku = leaf.getKeyUsage();
        if (ku != null && !ku[0]) throw new Failed("the device key may not sign");
        X509Certificate cur = leaf;
        for (int depth = 0; depth < 6; depth++) {
            try { cur.checkValidity(at); } catch (Exception e) { throw new Failed("certificate out of date"); }
            for (X509Certificate root : roots())
                if (signedBy(cur, root)) return;
            X509Certificate next = null;
            for (X509Certificate c : inter)
                if (c != cur && c.getBasicConstraints() >= 0 && signedBy(cur, c)) { next = c; break; }
            if (next == null) throw new Failed("not issued by Google's Cast CA");
            cur = next;
        }
        throw new Failed("certificate chain too long");
    }

    private static boolean signedBy(X509Certificate c, X509Certificate issuer) {
        if (!c.getIssuerX500Principal().equals(issuer.getSubjectX500Principal())) return false;
        try { c.verify(issuer.getPublicKey()); return true; } catch (Exception e) { return false; }
    }
}
