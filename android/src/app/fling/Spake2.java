// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling;

import java.math.BigInteger;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;

/** SPAKE2 over edwards25519, byte-for-byte what BoringSSL's SPAKE2_* does (crypto/curve25519/
 *  spake25519.cc), as the client ("alice") of ADB's Wireless debugging pairing. Plain
 *  BigInteger arithmetic: a handful of scalar multiplications per pairing, so speed is moot. */
final class Spake2 {
    static final BigInteger P = BigInteger.ONE.shiftLeft(255).subtract(BigInteger.valueOf(19));
    static final BigInteger L = BigInteger.ONE.shiftLeft(252).add(new BigInteger("27742317777372353535851937790883648493"));
    private static final BigInteger D = BigInteger.valueOf(-121665).multiply(BigInteger.valueOf(121666).modInverse(P)).mod(P);
    private static final BigInteger D2 = D.add(D).mod(P);
    private static final BigInteger I = BigInteger.TWO.modPow(P.subtract(BigInteger.ONE).shiftRight(2), P);
    private static final BigInteger TWO = BigInteger.TWO;

    /** A point in extended coordinates (X:Y:Z:T), x = X/Z, y = Y/Z, xy = T/Z. */
    static final class Pt {
        final BigInteger x, y, z, t;
        Pt(BigInteger x, BigInteger y, BigInteger z, BigInteger t) { this.x = x; this.y = y; this.z = z; this.t = t; }
        static Pt affine(BigInteger x, BigInteger y) { return new Pt(x, y, BigInteger.ONE, x.multiply(y).mod(P)); }
    }

    static final Pt ZERO = Pt.affine(BigInteger.ZERO, BigInteger.ONE);
    static final Pt B;
    static final Pt M, N;

    static {
        BigInteger by = BigInteger.valueOf(4).multiply(BigInteger.valueOf(5).modInverse(P)).mod(P);
        B = Pt.affine(xrecover(by), by);
        M = genpoint("edwards25519 point generation seed (M)");
        N = genpoint("edwards25519 point generation seed (N)");
    }

    static Pt add(Pt p, Pt q) {   // add-2008-hwcd-3, a = -1
        BigInteger a = p.y.subtract(p.x).multiply(q.y.subtract(q.x)).mod(P);
        BigInteger b = p.y.add(p.x).multiply(q.y.add(q.x)).mod(P);
        BigInteger c = p.t.multiply(D2).multiply(q.t).mod(P);
        BigInteger d = p.z.multiply(TWO).multiply(q.z).mod(P);
        BigInteger e = b.subtract(a), f = d.subtract(c), g = d.add(c), h = b.add(a);
        return new Pt(e.multiply(f).mod(P), g.multiply(h).mod(P), f.multiply(g).mod(P), e.multiply(h).mod(P));
    }

    static Pt neg(Pt p) { return new Pt(p.x.negate().mod(P), p.y, p.z, p.t.negate().mod(P)); }

    /** k·p for any non-negative integer k (not reduced mod L: the small-order part matters). */
    static Pt mul(BigInteger k, Pt p) {
        Pt r = ZERO;
        for (int i = k.bitLength() - 1; i >= 0; i--) {
            r = add(r, r);
            if (k.testBit(i)) r = add(r, p);
        }
        return r;
    }

    static BigInteger xrecover(BigInteger y) {
        BigInteger xx = y.multiply(y).subtract(BigInteger.ONE).multiply(D.multiply(y).multiply(y).add(BigInteger.ONE).modInverse(P)).mod(P);
        BigInteger x = xx.modPow(P.add(BigInteger.valueOf(3)).shiftRight(3), P);
        if (!x.multiply(x).subtract(xx).mod(P).equals(BigInteger.ZERO)) x = x.multiply(I).mod(P);
        if (x.testBit(0)) x = P.subtract(x);
        return x;
    }

    static byte[] encode(Pt p) {
        BigInteger zi = p.z.modInverse(P);
        BigInteger x = p.x.multiply(zi).mod(P), y = p.y.multiply(zi).mod(P);
        byte[] out = le(y);
        if (x.testBit(0)) out[31] |= (byte) 0x80;
        return out;
    }

    /** ed25519.py decodepoint: null when it isn't a point on the curve. */
    static Pt decode(byte[] s) {
        byte[] c = s.clone();
        boolean sign = (c[31] & 0x80) != 0;
        c[31] &= 0x7f;
        BigInteger y = fromLe(c);
        if (y.compareTo(P) >= 0) return null;
        BigInteger x = xrecover(y);
        if (x.testBit(0) != sign) x = P.subtract(x);
        BigInteger lhs = y.multiply(y).subtract(x.multiply(x)).mod(P);
        BigInteger rhs = BigInteger.ONE.add(D.multiply(x).multiply(x).multiply(y).multiply(y)).mod(P);
        if (!lhs.equals(rhs)) return null;
        return Pt.affine(x, y);
    }

    private static Pt genpoint(String seed) {
        byte[] v = sha("SHA-256", seed.getBytes(java.nio.charset.StandardCharsets.US_ASCII));
        while (true) {
            Pt p = decode(v);
            if (p != null) return p;
            v = sha("SHA-256", v);
        }
    }

    static byte[] le(BigInteger v) {
        byte[] be = v.toByteArray(), out = new byte[32];
        for (int i = 0; i < be.length && i < 32; i++) out[i] = be[be.length - 1 - i];
        return out;
    }

    static BigInteger fromLe(byte[] b) {
        byte[] be = new byte[b.length + 1];
        for (int i = 0; i < b.length; i++) be[be.length - 1 - i] = b[i];
        return new BigInteger(be);
    }

    static byte[] sha(String alg, byte[]... parts) {
        try {
            MessageDigest md = MessageDigest.getInstance(alg);
            for (byte[] p : parts) md.update(p);
            return md.digest();
        } catch (Exception e) { throw new IllegalStateException(e); }
    }

    // ------------------------------------------------------------ the protocol (we are alice)

    private final byte[] myName, theirName, passwordHash = new byte[64];
    private BigInteger priv, pwScalar;
    private byte[] myMsg;

    private final boolean alice;
    Spake2(byte[] myName, byte[] theirName) { this(true, myName, theirName); }
    Spake2(boolean alice, byte[] myName, byte[] theirName) { this.alice = alice; this.myName = myName; this.theirName = theirName; }

    byte[] generateMsg(byte[] password) {
        byte[] r = new byte[64];
        new SecureRandom().nextBytes(r);
        priv = fromLe(r).mod(L).shiftLeft(3);                                  // ×8: clears the cofactor later
        byte[] h = sha("SHA-512", password);
        System.arraycopy(h, 0, passwordHash, 0, 64);
        BigInteger s = fromLe(h).mod(L);
        // BoringSSL's "password scalar hack": add L, 2L, 4L by the low bits so s ≡ 0 (mod 8)
        if (s.testBit(0)) s = s.add(L);
        if (s.testBit(1)) s = s.add(L.shiftLeft(1));
        if (s.testBit(2)) s = s.add(L.shiftLeft(2));
        pwScalar = s;
        Pt pstar = add(mul(priv, B), mul(pwScalar, alice ? M : N));           // alice masks with M, bob with N
        myMsg = encode(pstar);
        return myMsg.clone();
    }

    /** The 64-byte key; null when their message isn't a point. */
    byte[] processMsg(byte[] theirMsg) {
        Pt q = decode(theirMsg);
        if (q == null || theirMsg.length != 32) return null;
        Pt unmasked = add(q, neg(mul(pwScalar, alice ? N : M)));
        byte[] dh = encode(mul(priv, unmasked));
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-512");
            if (alice) { lp(md, myName); lp(md, theirName); lp(md, myMsg); lp(md, theirMsg); }
            else { lp(md, theirName); lp(md, myName); lp(md, theirMsg); lp(md, myMsg); }
            lp(md, dh); lp(md, passwordHash);
            return md.digest();
        } catch (Exception e) { throw new IllegalStateException(e); }
    }

    private static void lp(MessageDigest md, byte[] d) {
        long l = d.length;
        byte[] len = new byte[8];
        for (int i = 0; i < 8; i++) { len[i] = (byte) (l & 0xff); l >>= 8; }
        md.update(len);
        md.update(d);
    }

    /** M and N as BoringSSL's precomputed table starts them: x ‖ y, little-endian. */
    static String hexXY(Pt p) {
        BigInteger zi = p.z.modInverse(P);
        StringBuilder sb = new StringBuilder();
        for (byte b : le(p.x.multiply(zi).mod(P))) sb.append(String.format("%02x", b));
        for (byte b : le(p.y.multiply(zi).mod(P))) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    public static void main(String[] a) {
        System.out.println(hexXY(M));
        System.out.println(hexXY(N));
        // the second table entry is (2^64)·M
        System.out.println(hexXY(mul(BigInteger.ONE.shiftLeft(64), M)).substring(0, 16));
        // round trip: two parties agree
        Spake2 al = new Spake2(true, "a".getBytes(), "b".getBytes());
        Spake2 bo = new Spake2(false, "b".getBytes(), "a".getBytes());
        byte[] m1 = al.generateMsg("123456".getBytes()), m2 = bo.generateMsg("123456".getBytes());
        System.out.println("agree " + Arrays.equals(al.processMsg(m2), bo.processMsg(m1)));
        Spake2 a2 = new Spake2(true, "a".getBytes(), "b".getBytes());
        Spake2 b2 = new Spake2(false, "b".getBytes(), "a".getBytes());
        byte[] n1 = a2.generateMsg("123456".getBytes()), n2 = b2.generateMsg("654321".getBytes());
        System.out.println("wrong pw differs " + !Arrays.equals(a2.processMsg(n2), b2.processMsg(n1)));
    }
}
