// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.net.InetSocketAddress;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Date;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/** JVM check of CastAuth (no Android needed; test/jvm/L.java stands in for the app's L):
 *    cd android && javac -d build/check src/app/fling/CastAuth.java src/app/fling/Proto.java \
 *        test/jvm/L.java test/CastAuthCheck.java && java -ea -cp build/check app.fling.CastAuthCheck [cast-host]
 *  Without a host: the embedded roots parse, real device chains from openscreen's test data
 *  (test/castauth) pass or fail as they do in openscreen, a real Chromecast signature checks
 *  out, and a chain that isn't Google's is refused.
 *  With a host (a Chromecast / Google TV on the network): the real challenge passes, and the
 *  same signed reply checked against another TLS certificate (a man in the middle) fails. */
public final class CastAuthCheck {
    private static int failures;

    public static void main(String[] a) throws Exception {
        java.util.List<X509Certificate> roots = CastAuth.roots();
        check("two roots", roots.size() == 2);
        check("Cast Root CA", roots.get(0).getSubjectX500Principal().getName().contains("CN=Cast Root CA"));
        check("Eureka Root CA", roots.get(1).getSubjectX500Principal().getName().contains("CN=Eureka Root CA"));
        for (X509Certificate r : roots) r.verify(r.getPublicKey());               // self-signed, intact

        // a root alone as "device certificate" with nothing above it: still has to chain, and
        // a certificate that isn't issued by Google must not pass
        boolean refused = false;
        try { CastAuth.chain(selfSigned(), new ArrayList<>(), new Date()); }
        catch (CastAuth.Failed e) { refused = true; }
        check("foreign certificate refused", refused);

        vectors(new java.io.File("test/castauth"));
        if (a.length > 0) live(a[0]);
        System.out.println(failures == 0 ? "CastAuthCheck: OK" : "CastAuthCheck: " + failures + " FAILED");
        System.exit(failures == 0 ? 0 : 1);
    }

    private static void live(String host) throws Exception {
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[]{new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String s) {}
            public void checkServerTrusted(X509Certificate[] c, String s) {}
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        }}, new SecureRandom());
        java.net.Socket raw = new java.net.Socket();
        raw.connect(new InetSocketAddress(host, 8009), 8000);
        SSLSocket s = (SSLSocket) ctx.getSocketFactory().createSocket(raw, host, 8009, true);
        s.setSoTimeout(8000);
        s.startHandshake();
        DataInputStream in = new DataInputStream(s.getInputStream());
        DataOutputStream out = new DataOutputStream(s.getOutputStream());

        // the challenge by hand, so the reply can be replayed against a wrong certificate too
        byte[] nonce = new byte[16];
        new SecureRandom().nextBytes(nonce);
        byte[] ch = new Proto.W().msg(1, new Proto.W().varint(1, 1).bytes(2, nonce).varint(3, 1)).out();
        byte[] msg = new Proto.W().varint(1, 0).str(2, "sender-0").str(3, "receiver-0")
                .str(4, CastAuth.NS_AUTH).varint(5, 1).bytes(7, ch).out();
        out.writeInt(msg.length); out.write(msg); out.flush();
        Proto.R reply = null;
        while (reply == null) {
            Proto.R m = new Proto.R(Proto.readFully(in, in.readInt()));
            if (CastAuth.NS_AUTH.equals(m.str(4))) reply = m;
        }
        Proto.R auth = new Proto.R(reply.bytes(7));
        X509Certificate peer = (X509Certificate) s.getSession().getPeerCertificates()[0];
        Proto.R resp = auth.msg(2);
        if (resp != null) {
            X509Certificate leaf = (X509Certificate) java.security.cert.CertificateFactory.getInstance("X.509")
                    .generateCertificate(new java.io.ByteArrayInputStream(resp.bytes(2)));
            System.out.println("device:   " + leaf.getSubjectX500Principal());
            for (byte[] d : resp.all(3))
                System.out.println("  via:    " + ((X509Certificate) java.security.cert.CertificateFactory.getInstance("X.509")
                        .generateCertificate(new java.io.ByteArrayInputStream(d))).getSubjectX500Principal());
            System.out.println("TLS cert: " + peer.getNotBefore() + " .. " + peer.getNotAfter()
                    + "; nonce echoed: " + (resp.bytes(5) != null) + "; hash " + resp.varint(6));
        }
        try { CastAuth.verify(auth, nonce, peer, System.currentTimeMillis()); check("live device passes", true); }
        catch (Exception e) { System.out.println("  " + e.getMessage()); check("live device passes", false); }

        boolean mitm = false;
        try { CastAuth.verify(auth, nonce, selfSigned(), System.currentTimeMillis()); }
        catch (CastAuth.Failed e) { mitm = true; }
        check("other TLS certificate (man in the middle) refused", mitm);

        byte[] other = nonce.clone(); other[0] ^= 1;
        boolean replay = false;
        try { CastAuth.verify(auth, other, peer, System.currentTimeMillis()); }
        catch (CastAuth.Failed e) { replay = true; }
        check("reply to another nonce refused", replay || resp == null || resp.bytes(5) == null);

        // and the whole thing as Cast.ensure runs it, on a fresh connection
        java.net.Socket raw2 = new java.net.Socket();
        raw2.connect(new InetSocketAddress(host, 8009), 8000);
        SSLSocket s2 = (SSLSocket) ctx.getSocketFactory().createSocket(raw2, host, 8009, true);
        s2.setSoTimeout(8000);
        s2.startHandshake();
        try {
            CastAuth.authenticate(s2, new DataInputStream(s2.getInputStream()),
                    new DataOutputStream(s2.getOutputStream()), "sender-0", "receiver-0");
            check("authenticate()", true);
        } catch (Exception e) { System.out.println("  " + e.getMessage()); check("authenticate()", false); }
        s.close(); s2.close();
    }

    private static final Date APRIL_2016 = utc(2016, 4, 1), JAN_2015 = utc(2015, 1, 1), MARCH_2037 = utc(2037, 3, 1);

    private static Date utc(int y, int m, int d) {
        java.util.Calendar c = java.util.Calendar.getInstance(java.util.TimeZone.getTimeZone("UTC"));
        c.clear();
        c.set(y, m - 1, d);
        return c.getTime();
    }

    /** openscreen cast_cert_validator_unittest.cc, the cases that apply to Fling's two roots. */
    private static void vectors(java.io.File dir) throws Exception {
        for (String ok : new String[]{"chromecast_gen1", "chromecast_gen1_reissue", "chromecast_gen2",
                "chromecast_audio", "fugu", "vizio"})
            check(ok + " chains to a Cast root", chains(pem(new java.io.File(dir, ok + ".pem"), "CERTIFICATE"), APRIL_2016));
        check("unchained refused", !chains(pem(new java.io.File(dir, "unchained.pem"), "CERTIFICATE"), APRIL_2016));
        java.util.List<byte[]> gen2 = pem(new java.io.File(dir, "chromecast_gen2.pem"), "CERTIFICATE");
        check("gen2 before its validity refused", !chains(gen2, JAN_2015));
        check("gen2 after its validity refused", !chains(gen2, MARCH_2037));

        // data signed by that Chromecast's device key, SHA-1 and SHA-256, as in a real reply
        java.io.File sd = new java.io.File(dir, "2ZZBG9_FA8FCA3EF91A.pem");
        byte[] data = pem(sd, "MESSAGE").get(0);
        X509Certificate leaf = cert(pem(new java.io.File(dir, "chromecast_gen1.pem"), "CERTIFICATE").get(0));
        check("SHA1 signature of a real Chromecast", sig("SHA1withRSA", leaf, data, pem(sd, "SIGNATURE SHA1").get(0)));
        check("SHA256 signature of a real Chromecast", sig("SHA256withRSA", leaf, data, pem(sd, "SIGNATURE SHA256").get(0)));
        byte[] bad = data.clone(); bad[0] ^= 1;
        check("changed data refused", !sig("SHA256withRSA", leaf, bad, pem(sd, "SIGNATURE SHA256").get(0)));
    }

    private static boolean chains(java.util.List<byte[]> ders, Date at) throws Exception {
        java.util.List<X509Certificate> inter = new ArrayList<>();
        for (int i = 1; i < ders.size(); i++) inter.add(cert(ders.get(i)));
        try { CastAuth.chain(cert(ders.get(0)), inter, at); return true; }
        catch (CastAuth.Failed e) { return false; }
    }

    private static boolean sig(String alg, X509Certificate leaf, byte[] data, byte[] s) throws Exception {
        java.security.Signature v = java.security.Signature.getInstance(alg);
        v.initVerify(leaf.getPublicKey());
        v.update(data);
        return v.verify(s);
    }

    private static X509Certificate cert(byte[] der) throws Exception {
        return (X509Certificate) java.security.cert.CertificateFactory.getInstance("X.509")
                .generateCertificate(new java.io.ByteArrayInputStream(der));
    }

    /** The blocks of one PEM type in a file, in order. */
    private static java.util.List<byte[]> pem(java.io.File f, String type) throws Exception {
        String t = new String(java.nio.file.Files.readAllBytes(f.toPath()), java.nio.charset.StandardCharsets.US_ASCII);
        java.util.List<byte[]> r = new ArrayList<>();
        String b = "-----BEGIN " + type + "-----", e = "-----END " + type + "-----";
        for (int i = t.indexOf(b); i >= 0; i = t.indexOf(b, i + 1)) {
            int j = t.indexOf(e, i);
            r.add(java.util.Base64.getMimeDecoder().decode(t.substring(i + b.length(), j)));
        }
        if (r.isEmpty()) throw new IllegalStateException("no " + type + " in " + f);
        return r;
    }

    /** A throwaway self-signed certificate (the JDK's keytool code path, via a keystore). */
    private static X509Certificate selfSigned() throws Exception {
        java.io.File ks = java.io.File.createTempFile("castauth", ".p12");
        ks.delete();
        Process p = new ProcessBuilder(System.getProperty("java.home") + "/bin/keytool", "-genkeypair",
                "-keystore", ks.getPath(), "-storepass", "changeit", "-alias", "x", "-keyalg", "RSA",
                "-keysize", "2048", "-validity", "2", "-dname", "CN=Not a Chromecast").inheritIO().start();
        if (p.waitFor() != 0) throw new IllegalStateException("keytool failed");
        java.security.KeyStore k = java.security.KeyStore.getInstance("PKCS12");
        try (java.io.FileInputStream f = new java.io.FileInputStream(ks)) { k.load(f, "changeit".toCharArray()); }
        ks.delete();
        return (X509Certificate) k.getCertificate("x");
    }

    private static void check(String what, boolean ok) {
        System.out.println((ok ? "ok    " : "FAIL  ") + what);
        if (!ok) failures++;
    }

    private CastAuthCheck() {}
}
