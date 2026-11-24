// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Minimal protobuf wire encoder/decoder (enough for the TV protocols). */
final class Proto {
    private Proto() {}

    static final class W {
        private final ByteArrayOutputStream b = new ByteArrayOutputStream();

        void raw(long v) {
            while ((v & ~0x7FL) != 0) { b.write((int) ((v & 0x7F) | 0x80)); v >>>= 7; }
            b.write((int) v);
        }
        W varint(int field, long v) { raw((long) field << 3); raw(v); return this; }
        W bytes(int field, byte[] d) { raw(((long) field << 3) | 2); raw(d.length); b.write(d, 0, d.length); return this; }
        W str(int field, String s) { return bytes(field, s.getBytes(StandardCharsets.UTF_8)); }
        W msg(int field, W m) { return bytes(field, m.out()); }
        byte[] out() { return b.toByteArray(); }
    }

    static final class R {
        private final Map<Integer, List<Object>> f = new HashMap<>();

        R(byte[] d) throws IOException {
            int i = 0;
            while (i < d.length) {
                long[] kv = readVarint(d, i); i = (int) kv[1];
                int field = (int) (kv[0] >>> 3), wt = (int) (kv[0] & 7);
                Object val;
                switch (wt) {
                    case 0: { long[] v = readVarint(d, i); i = (int) v[1]; val = v[0]; break; }
                    case 1: i += 8; val = null; break;
                    case 5: i += 4; val = null; break;
                    case 2: {
                        long[] l = readVarint(d, i); i = (int) l[1];
                        int n = (int) l[0];
                        if (n < 0 || i + n > d.length) throw new IOException("bad length");
                        byte[] s = new byte[n]; System.arraycopy(d, i, s, 0, n); i += n; val = s; break;
                    }
                    default: throw new IOException("bad wire type " + wt);
                }
                if (val != null) f.computeIfAbsent(field, k -> new ArrayList<>()).add(val);
            }
        }
        boolean has(int n) { return f.containsKey(n); }
        Long varint(int n) {
            List<Object> l = f.get(n);
            if (l == null) return null;
            for (Object o : l) if (o instanceof Long) return (Long) o;
            return null;
        }
        byte[] bytes(int n) {
            List<Object> l = f.get(n);
            if (l == null) return null;
            for (Object o : l) if (o instanceof byte[]) return (byte[]) o;
            return null;
        }
        R msg(int n) throws IOException { byte[] b = bytes(n); return b == null ? null : new R(b); }
        String str(int n) { byte[] b = bytes(n); return b == null ? null : new String(b, StandardCharsets.UTF_8); }
    }

    static long[] readVarint(byte[] d, int i) throws IOException {
        long r = 0; int shift = 0;
        while (true) {
            if (i >= d.length) throw new IOException("truncated varint");
            int b = d[i++] & 0xFF;
            r |= (long) (b & 0x7F) << shift;
            if ((b & 0x80) == 0) return new long[]{r, i};
            shift += 7;
            if (shift > 63) throw new IOException("varint too long");
        }
    }

    /** Reads one varint from a stream; -1 on EOF before any byte. */
    static long readVarint(InputStream in) throws IOException {
        long r = 0; int shift = 0;
        while (true) {
            int b = in.read();
            if (b < 0) { if (shift == 0) return -1; throw new IOException("eof in varint"); }
            r |= (long) (b & 0x7F) << shift;
            if ((b & 0x80) == 0) return r;
            shift += 7;
        }
    }

    static byte[] readFully(InputStream in, int n) throws IOException {
        byte[] b = new byte[n];
        int o = 0;
        while (o < n) {
            int k = in.read(b, o, n - o);
            if (k < 0) throw new IOException("connection closed");
            o += k;
        }
        return b;
    }
}
