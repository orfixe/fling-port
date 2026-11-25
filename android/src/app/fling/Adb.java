// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.Context;
import android.os.SystemClock;
import android.util.Base64;

import java.io.DataInputStream;
import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URLEncoder;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.crypto.Cipher;

/** Minimal ADB-over-TCP client (port 5555): RSA auth + one shell command.
 *  Used to read what the TV is really playing (`dumpsys media_session`); AdbLink keeps a
 *  connection open with several streams for LIVE. */
final class Adb {
    static final int CNXN = 0x4e584e43, AUTH = 0x48545541, OPEN = 0x4e45504f,
            OKAY = 0x59414b4f, CLSE = 0x45534c43, WRTE = 0x45545257;
    private static final int VERSION = 0x01000001, MAX_PAYLOAD = 256 * 1024;
    private static final byte[] SHA1_PREFIX = {0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e, 0x03, 0x02, 0x1a,
            0x05, 0x00, 0x04, 0x14};

    /** "ok" | "unauthorized" (accept the prompt on the TV) | "closed" (ADB off) | error text */
    static volatile String lastState = "?";

    private final Context ctx;
    private PrivateKey priv;
    private RSAPublicKey pub;
    private final Map<String, String[]> lookups = new java.util.concurrent.ConcurrentHashMap<>();   // title → {videoId, duration}

    Adb(Context ctx) { this.ctx = ctx; }

    // ------------------------------------------------------------ keys

    private synchronized void keys() throws Exception {
        if (priv != null) return;
        File kf = new File(ctx.getFilesDir(), "adbkey"), pf = new File(ctx.getFilesDir(), "adbkey.pub.der");
        KeyFactory f = KeyFactory.getInstance("RSA");
        if (kf.exists() && pf.exists()) {
            priv = f.generatePrivate(new PKCS8EncodedKeySpec(Files.readAllBytes(kf.toPath())));
            pub = (RSAPublicKey) f.generatePublic(new X509EncodedKeySpec(Files.readAllBytes(pf.toPath())));
            return;
        }
        KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
        g.initialize(2048);
        KeyPair kp = g.generateKeyPair();
        Files.write(kf.toPath(), kp.getPrivate().getEncoded());
        Files.write(pf.toPath(), kp.getPublic().getEncoded());
        priv = kp.getPrivate();
        pub = (RSAPublicKey) kp.getPublic();
    }

    /** Android's mincrypt RSAPublicKey struct, base64 + " name". */
    private byte[] androidPubKey() {
        BigInteger n = pub.getModulus(), r32 = BigInteger.ONE.shiftLeft(32);
        int words = 64;
        BigInteger n0inv = n.mod(r32).modInverse(r32).negate().mod(r32);
        BigInteger rr = BigInteger.ONE.shiftLeft(words * 32).pow(2).mod(n);
        ByteBuffer b = ByteBuffer.allocate(4 + 4 + words * 4 * 2 + 4).order(ByteOrder.LITTLE_ENDIAN);
        b.putInt(words);
        b.putInt(n0inv.intValue());
        for (int i = 0; i < words; i++) b.putInt(n.shiftRight(32 * i).mod(r32).intValue());
        for (int i = 0; i < words; i++) b.putInt(rr.shiftRight(32 * i).mod(r32).intValue());
        b.putInt(pub.getPublicExponent().intValue());
        String s = Base64.encodeToString(b.array(), Base64.NO_WRAP) + " fling@android\0";
        return s.getBytes(StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------ wire

    static void send(OutputStream o, int cmd, int a0, int a1, byte[] data) throws IOException {
        if (data == null) data = new byte[0];
        int sum = 0;
        for (byte x : data) sum += x & 0xFF;
        ByteBuffer h = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN);
        h.putInt(cmd).putInt(a0).putInt(a1).putInt(data.length).putInt(sum).putInt(~cmd);
        o.write(h.array());
        o.write(data);
        o.flush();
    }

    static final class Msg { int cmd, a0, a1; byte[] data; }

    static Msg read(DataInputStream in) throws IOException {
        byte[] h = new byte[24];
        in.readFully(h);
        ByteBuffer b = ByteBuffer.wrap(h).order(ByteOrder.LITTLE_ENDIAN);
        Msg m = new Msg();
        m.cmd = b.getInt(); m.a0 = b.getInt(); m.a1 = b.getInt();
        int len = b.getInt();
        if (len < 0 || len > MAX_PAYLOAD * 4) throw new IOException("bad adb length");
        m.data = new byte[len];
        in.readFully(m.data);
        return m;
    }

    /** Host → until-elapsedRealtime: do not send AUTH pubkey again (that re-prompts the TV). */
    private final Map<String, Long> noPubkeyUntil = new HashMap<>();

    /** Runs one shell command; returns its output. Throws with a readable state on failure. */
    synchronized String shell(String host, String command) throws Exception {
        return new String(shellBytes(host, command), StandardCharsets.UTF_8);
    }

    /** An authenticated ADB transport: what CNXN returned, ready for OPEN. */
    static final class Session {
        final Socket sock; final DataInputStream in; final OutputStream out;
        final int maxData;   // the TV's largest payload per WRTE
        Session(Socket sock, DataInputStream in, OutputStream out, int maxData) {
            this.sock = sock; this.in = in; this.out = out; this.maxData = maxData;
        }
    }

    /** Connects to adbd on host:5555 and authenticates with this phone's key (the TV shows
     *  "Allow debugging?" the first time). The caller owns the socket. */
    Session connect(String host) throws Exception { return connect(host, 5555); }

    /** Same, on another adbd port (wireless debugging, a Tailscale port map). */
    synchronized Session connect(String host, int port) throws Exception {
        if (host != null && host.indexOf(':') >= 0) {
            lastState = "closed";
            throw new IOException("IPv6 ADB is not used");
        }
        boolean allowPubkey = true;
        Long quiet = noPubkeyUntil.get(host);
        if (quiet != null) {
            if (SystemClock.elapsedRealtime() < quiet) allowPubkey = false;
            else noPubkeyUntil.remove(host);
        }
        keys();
        Socket s = new Socket();
        boolean ok = false;
        try {
            try {
                // Numeric IPv4 host → IPv4 socket; never open an IPv6 path here
                s.connect(new InetSocketAddress(java.net.InetAddress.getByName(host), port), 2500);
            } catch (IOException e) {
                lastState = "closed";
                throw new IOException("ADB is off on the TV");
            }
            s.setTcpNoDelay(true);   // keys and touches must not wait for Nagle
            s.setSoTimeout(8000);
            DataInputStream in = new DataInputStream(new java.io.BufferedInputStream(s.getInputStream(), 64 * 1024));
            OutputStream o = s.getOutputStream();
            send(o, CNXN, VERSION, MAX_PAYLOAD, "host::fling\0".getBytes(StandardCharsets.UTF_8));
            boolean sentSig = false, sentKey = false;
            Msg m;
            while (true) {
                try { m = read(in); } catch (java.net.SocketTimeoutException e) {
                    lastState = "unauthorized";
                    noPubkeyUntil.put(host, SystemClock.elapsedRealtime() + 300_000L);
                    throw new IOException("Accept the debugging prompt on the TV");
                }
                if (m.cmd == CNXN) break;
                if (m.cmd != AUTH || m.a0 != 1) throw new IOException("unexpected adb reply");
                if (!sentSig) {
                    Cipher c = Cipher.getInstance("RSA/ECB/PKCS1Padding");
                    c.init(Cipher.ENCRYPT_MODE, priv);
                    byte[] payload = new byte[SHA1_PREFIX.length + m.data.length];
                    System.arraycopy(SHA1_PREFIX, 0, payload, 0, SHA1_PREFIX.length);
                    System.arraycopy(m.data, 0, payload, SHA1_PREFIX.length, m.data.length);
                    send(o, AUTH, 2, 0, c.doFinal(payload));
                    sentSig = true;
                } else if (!sentKey && allowPubkey) {
                    // Public key → TV "Allow debugging?" dialog. Only once per quiet window.
                    send(o, AUTH, 3, 0, androidPubKey());
                    sentKey = true;
                    s.setSoTimeout(30000);
                } else {
                    lastState = "unauthorized";
                    noPubkeyUntil.put(host, SystemClock.elapsedRealtime() + 300_000L);
                    throw new IOException("Accept the debugging prompt on the TV");
                }
            }
            noPubkeyUntil.remove(host);
            s.setSoTimeout(8000);
            lastState = "ok";
            ok = true;
            return new Session(s, in, o, Math.max(4096, Math.min(m.a1, MAX_PAYLOAD)));
        } finally {
            if (!ok) try { s.close(); } catch (IOException ignored) {}
        }
    }

    /** Raw shell output (binary-safe, e.g. `screencap -p`). */
    synchronized byte[] shellBytes(String host, String command) throws Exception {
        Session ss = connect(host);
        try (Socket s = ss.sock) {
            DataInputStream in = ss.in;
            OutputStream o = ss.out;
            int local = 1;
            // "exec:" is raw (no PTY newline mangling) — needed for binary output like PNGs
            String service = command.startsWith("exec:") ? command : "shell:" + command;
            send(o, OPEN, local, 0, (service + "\0").getBytes(StandardCharsets.UTF_8));
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            int remote = 0;
            while (true) {
                Msg m = read(in);
                if (m.cmd == OKAY) { remote = m.a0; continue; }
                if (m.cmd == WRTE) {
                    out.write(m.data, 0, m.data.length);
                    send(o, OKAY, local, m.a0, null);
                    continue;
                }
                if (m.cmd == CLSE) { send(o, CLSE, local, remote, null); break; }
            }
            return out.toByteArray();
        }
    }

    // ------------------------------------------------------------ now playing

    private static final Pattern STATE = Pattern.compile("state=(\\d+), position=(-?\\d+).*?speed=([\\d.]+), updated=(\\d+)");

    /** Real now-playing as a Cast.Status (state IDLE when nothing plays). */
    Cast.Status nowPlaying(String host) throws Exception {
        String raw = shell(host, "dumpsys media_session; echo __UP__; cat /proc/uptime");
        int cut = raw.indexOf("__UP__");
        if (cut < 0) throw new IOException("no output");
        double uptimeMs = Double.parseDouble(raw.substring(cut + 6).trim().split("\\s+")[0]) * 1000;
        List<Map<String, Object>> sessions = new ArrayList<>();
        Map<String, Object> cur = null;
        for (String line : raw.substring(0, cut).split("\n")) {
            String l = line.trim();
            if (l.startsWith("package=")) { cur = new HashMap<>(); cur.put("pkg", l.substring(8)); sessions.add(cur); }
            else if (cur == null) continue;
            else if (l.startsWith("state=PlaybackState")) {
                Matcher m = STATE.matcher(l);
                if (m.find()) {
                    cur.put("st", Integer.parseInt(m.group(1)));
                    cur.put("pos", Long.parseLong(m.group(2)));
                    cur.put("speed", Double.parseDouble(m.group(3)));
                    cur.put("upd", Long.parseLong(m.group(4)));
                }
            } else if (l.startsWith("metadata:") && l.contains("description=")) {
                String[] p = l.substring(l.indexOf("description=") + 12).split(",");
                if (p.length >= 3) {
                    StringBuilder t = new StringBuilder();
                    for (int i = 0; i < p.length - 2; i++) { if (i > 0) t.append(","); t.append(p[i]); }
                    cur.put("title", t.toString().trim());
                    String a = p[p.length - 2].trim();
                    if (!a.equals("null")) cur.put("artist", a);
                } else cur.put("title", p[0].trim());
            }
        }
        Map<String, Object> best = null;
        int bestRank = 99;
        for (Map<String, Object> x : sessions) {
            Object st = x.get("st"), title = x.get("title");
            if (st == null || title == null || "null".equals(title)) continue;
            int rank = (int) st == 3 ? 0 : (int) st == 6 ? 1 : (int) st == 2 ? 2 : 99;
            if ("com.google.android.apps.mediashell".equals(x.get("pkg"))) rank += 3;   // Cast's stale session last
            if (rank < bestRank) { bestRank = rank; best = x; }
        }
        Cast.Status s = new Cast.Status();
        if (best == null || bestRank >= 99) { s.state = "IDLE"; return s; }
        int st = (int) best.get("st");
        s.state = st == 3 ? "PLAYING" : st == 6 ? "BUFFERING" : "PAUSED";
        double pos = (long) best.get("pos") / 1000.0;
        if (st == 3) pos += Math.max(0, uptimeMs - (long) best.get("upd")) / 1000.0 * (double) best.get("speed");
        s.elapsed = pos;
        s.updatedAt = SystemClock.elapsedRealtime();
        s.title = (String) best.get("title");
        s.appId = (String) best.get("pkg");
        String artist = (String) best.get("artist");
        s.subtitle = artist;
        String[] look = lookup(s.title, artist);
        if (look != null) {
            s.contentId = look[0];
            s.imageUrl = "https://img.youtube.com/vi/" + look[0] + "/hqdefault.jpg";
            if (look[1] != null) s.duration = Double.parseDouble(look[1]);
        }
        return s;
    }

    private static final Pattern VID = Pattern.compile("\"videoRenderer\":\\{\"videoId\":\"([\\w-]{11})\"");
    private static final Pattern LEN = Pattern.compile("\"simpleText\":\"(\\d+(?::\\d+)+)\"");

    /** First YouTube search hit for artwork + duration (cached). */
    String[] lookup(String title, String artist) {
        String key = title + "|" + artist;
        String[] hit = lookups.get(key);
        if (hit != null) return hit.length == 0 ? null : hit;
        String[] res = null;
        try {
            String q = URLEncoder.encode(title + (artist != null ? " " + artist : ""), "UTF-8");
            java.net.HttpURLConnection c = (java.net.HttpURLConnection)
                    new java.net.URL("https://www.youtube.com/results?search_query=" + q).openConnection();
            c.setConnectTimeout(5000); c.setReadTimeout(6000);
            c.setRequestProperty("User-Agent", "Mozilla/5.0");
            c.setRequestProperty("Accept-Language", "en");
            String html = new String(Cast.readAll(c.getInputStream()), StandardCharsets.UTF_8);
            Matcher m = VID.matcher(html);
            if (m.find()) {
                String dur = null;
                Matcher d = LEN.matcher(html.substring(m.start(), Math.min(html.length(), m.start() + 6000)));
                if (d.find()) {
                    int sec = 0;
                    for (String part : d.group(1).split(":")) sec = sec * 60 + Integer.parseInt(part);
                    dur = String.valueOf(sec);
                }
                res = new String[]{m.group(1), dur};
            }
        } catch (Exception ignored) {}
        lookups.put(key, res == null ? new String[0] : res);
        return res;
    }
}
