// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.Context;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.security.SecureRandom;

/** A LIVE session with scrcpy-server on the TV, over Fling's own ADB client (no adb binary,
 *  no port forwarding): the server is uploaded if the TV lacks this exact file, started with
 *  tunnel_forward=true, and its video and control sockets are opened as ADB
 *  `localabstract:scrcpy_<scid>` streams. Wire format: ScrcpyProto (scrcpy v5.0.1).
 *  Decoding and the screen are LiveActivity / VideoDecoder. */
final class Scrcpy {
    private Scrcpy() {}

    static final String VERSION = ScrcpyProto.VERSION;
    /** Size of every header on Session.video(). */
    static final int HEADER_SIZE = ScrcpyProto.HEADER_SIZE;

    /** Parses one 12-byte header read from Session.video(): .session (+ width, height) or a
     *  media packet (.config, .keyFrame, .pts in µs — -1 for config —, .size of the payload). */
    static ScrcpyProto.Packet parsePacket(byte[] header12) { return ScrcpyProto.parsePacket(header12); }

    // ------------------------------------------------------------ options

    static final class Options {
        int maxSize = 1280;              // longest side of the TV picture, px (0 = as is)
        int videoBitRate = 8_000_000;    // bit/s
        int maxFps = 60;
        String codec = "h264";           // "h264" | "h265" | "av1"
        boolean audio = false;           // no audio client yet: keep false
        int iFrameSeconds = 2;           // a lost or late picture heals within this

        /** Presets: a home Wi‑Fi carries more than a tailnet path through the internet. */
        static Options forNetwork(boolean tailscale) {
            Options o = new Options();
            if (tailscale) { o.maxSize = 1024; o.videoBitRate = 3_000_000; }
            return o;
        }

        /** One step down after stalls: bitrate first, then picture size. Null at the floor. */
        Options lower() {
            Options o = copy();
            if (videoBitRate > 1_500_000) o.videoBitRate = Math.max(1_000_000, videoBitRate * 6 / 10);
            else if (maxSize == 0 || maxSize > 640) o.maxSize = maxSize == 0 || maxSize > 1024 ? 1024 : maxSize > 800 ? 800 : 640;
            else return null;
            return o;
        }

        Options copy() {
            Options o = new Options();
            o.maxSize = maxSize; o.videoBitRate = videoBitRate; o.maxFps = maxFps;
            o.codec = codec; o.audio = audio; o.iFrameSeconds = iFrameSeconds;
            return o;
        }
    }

    /** What the server says before the first frame. */
    static final class Header {
        int codecId;                     // ScrcpyProto.CODEC_*
        int width, height;               // first capture session; later ones come in-stream
        String deviceName;
    }

    // ------------------------------------------------------------ session

    interface Session extends Closeable {
        Header header();
        /** "video/avc" | "video/hevc" | "video/av01" */
        String mime();
        /** The video socket right after the first session packet. From here on: 12-byte headers
         *  (ScrcpyProto.parsePacket) — a session packet (MSB of byte 0 set: new width/height,
         *  e.g. after rotation, no payload) or a media header [u64 pts_and_flags][u32 len] and
         *  len bytes of payload. Flags of v5.0.1: bit 63 session, bit 62 config, bit 61 key frame. */
        InputStream video();
        void key(int action, int androidKeycode, int repeat, int metaState) throws IOException;
        void text(String s) throws IOException;
        void touch(int action, long pointerId, int x, int y, int screenW, int screenH, float pressure,
                   int actionButton, int buttons) throws IOException;
        /** Asks the encoder for a new key frame (after a decoder error). */
        void resetVideo() throws IOException;
        /** Latest TCP round trip to the TV's adbd, ms; -1 until measured. */
        int rttMillis();
        /** Closes the streams: the server on the TV exits when its sockets close. */
        @Override void close();
    }

    /** Blocking (call off the main thread): connect ADB, check / upload / start the server,
     *  open video + control, read the dummy byte, device meta, codec id and first session. */
    static Session open(Context ctx, String host, int adbPort, Options opt) throws IOException {
        Impl s = new Impl(ctx.getApplicationContext(), host, adbPort);
        try {
            s.start(opt);
            return s;
        } catch (IOException e) {
            s.close();
            throw e;
        } catch (Exception e) {
            s.close();
            throw new IOException(message(e), e);
        }
    }

    /** Why ADB/LIVE failed, for the user. */
    static String message(Exception e) {
        if (e instanceof SecurityException) return L.t("Файл scrcpy-server не совпал с контрольной суммой, поэтому он не запущен.");
        if ("unauthorized".equals(Adb.lastState)) return L.t("Подтвердите на ТВ «Разрешить отладку» и нажмите LIVE ещё раз.");
        if ("closed".equals(Adb.lastState)) return L.t("LIVE работает через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети».");
        String m = e.getMessage();
        return m == null ? e.getClass().getSimpleName() : m;
    }

    private static final class Impl implements Session {
        private final Context ctx;
        private final String host;
        private final int port;
        private final Adb adb;
        private final StringBuilder log = new StringBuilder();
        private final Object sendLock = new Object();
        private volatile boolean closed;
        private volatile int rtt = -1;
        private AdbLink link;
        private AdbLink.Stream shell, video, control;
        private final Header header = new Header();
        private String mime;

        Impl(Context ctx, String host, int port) {
            this.ctx = ctx; this.host = host; this.port = port;
            this.adb = new Adb(ctx);
        }

        void start(Options o) throws Exception {
            byte[] jar = serverJar(ctx);
            link = AdbLink.open(adb, host, port);
            ensureServer(jar);
            int scid = new SecureRandom().nextInt() & 0x7FFFFFFF;   // 31 bits, as the scrcpy client does
            String cmd = ScrcpyProto.serverCommand(scid, o.codec, o.maxSize, o.videoBitRate, o.maxFps, o.iFrameSeconds, o.audio);
            shell = link.open("shell:" + cmd, 5000);
            daemon(this::drainShell, "scrcpy-log");
            String sock = "localabstract:" + ScrcpyProto.socketName(scid);
            video = connect(sock);                                  // sockets are accepted in this order:
            byte[] one = new byte[1];                               // video, (audio,) control
            video.readFully(one);                                   // dummy byte on the first socket
            byte[] meta = new byte[ScrcpyProto.DEVICE_NAME_LENGTH];
            video.readFully(meta);
            header.deviceName = ScrcpyProto.deviceName(meta);
            control = connect(sock);
            daemon(this::drainControl, "scrcpy-ctl");
            byte[] id = new byte[4];
            video.readFully(id);
            header.codecId = ScrcpyProto.readU32(id, 0);
            if (header.codecId == 0) throw new IOException(L.t("ТВ отключил видео."));
            if (header.codecId == 1) throw new IOException(L.t("ТВ не смог настроить видео."));
            mime = ScrcpyProto.mime(header.codecId);
            if (mime == null) throw new IOException("codec 0x" + Integer.toHexString(header.codecId));
            byte[] h = new byte[ScrcpyProto.HEADER_SIZE];
            video.readFully(h);
            ScrcpyProto.Packet p = ScrcpyProto.parsePacket(h);
            if (!p.session || p.width <= 0 || p.height <= 0) throw new IOException("no video session");
            header.width = p.width;
            header.height = p.height;
            daemon(this::measure, "scrcpy-rtt");
        }

        /** The server creates its socket a moment after it starts: retry like scrcpy (100 × 100 ms). */
        private AdbLink.Stream connect(String service) throws Exception {
            IOException last = null;
            for (int i = 0; i < 100 && !closed; i++) {
                if (!shell.isOpen()) break;                         // the server already exited
                try { return link.open(service, 3000); } catch (IOException e) { last = e; }
                Thread.sleep(100);
            }
            Thread.sleep(300);                                      // let its last words arrive
            String why = lastLogLine();
            throw new IOException(why != null ? L.f("ТВ не запустил scrcpy: %s", why)
                    : last != null ? last.getMessage() : "scrcpy");
        }

        /** Uploads the server only when the TV doesn't already hold this exact file. */
        private void ensureServer(byte[] jar) throws IOException {
            String have = "";
            try { have = link.shell("sha256sum " + ScrcpyProto.DEVICE_PATH + " 2>/dev/null", 5000).trim(); }
            catch (IOException ignored) {}
            if (have.startsWith(ScrcpyProto.SERVER_SHA256)) return;
            link.push(jar, ScrcpyProto.DEVICE_PATH, 0100644);
        }

        private void drainShell() {
            byte[] b = new byte[2048];
            try {
                for (int n; (n = shell.in.read(b, 0, b.length)) > 0; ) synchronized (log) {
                    log.append(new String(b, 0, n, "UTF-8"));
                    if (log.length() > 4096) log.delete(0, log.length() - 4096);
                }
            } catch (IOException ignored) {}
        }

        private String lastLogLine() {
            synchronized (log) {
                String[] lines = log.toString().trim().split("\n");
                for (int i = lines.length - 1; i >= 0; i--) {
                    String s = lines[i].trim();
                    if (s.startsWith("ERROR:") || s.contains("Exception")) return s.replaceFirst("^ERROR:\\s*", "");
                }
                String t = lines[lines.length - 1].trim();
                return t.isEmpty() ? null : t;
            }
        }

        /** Device messages (clipboard, acks) are not used, but are read so the socket never fills. */
        private void drainControl() {
            byte[] b = new byte[4096];
            try { while (control.in.read(b, 0, b.length) > 0) { /* discard */ } } catch (Exception ignored) {}
        }

        /** TCP connect time to adbd every 3 s: the path's round trip, without touching the TV. */
        private void measure() {
            while (!closed) {
                long ms = Tailnet.connectMs(host, port, 2000);
                rtt = ms < 0 ? -1 : (int) ms;
                try { Thread.sleep(3000); } catch (InterruptedException e) { return; }
            }
        }

        private void send(byte[] msg) throws IOException {
            AdbLink.Stream c = control;
            if (c == null || !c.isOpen()) throw new IOException(L.t("Нет соединения с ТВ"));
            synchronized (sendLock) { c.write(msg); }
        }

        @Override public Header header() { return header; }
        @Override public String mime() { return mime; }
        @Override public InputStream video() { return video.in; }
        @Override public int rttMillis() { return rtt; }

        @Override public void key(int action, int code, int repeat, int meta) throws IOException {
            send(ScrcpyProto.key(action, code, repeat, meta));
        }

        @Override public void text(String s) throws IOException { if (!s.isEmpty()) send(ScrcpyProto.text(s)); }

        @Override public void touch(int action, long pointerId, int x, int y, int w, int h, float pressure,
                                    int actionButton, int buttons) throws IOException {
            send(ScrcpyProto.touch(action, pointerId, x, y, w, h, pressure, actionButton, buttons));
        }

        @Override public void resetVideo() throws IOException { send(ScrcpyProto.resetVideo()); }

        @Override public synchronized void close() {
            closed = true;
            for (AdbLink.Stream s : new AdbLink.Stream[]{control, video, shell}) if (s != null) s.close();
            if (link != null) link.close();
        }
    }

    private static void daemon(Runnable r, String name) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        t.start();
    }

    // ------------------------------------------------------------ the server file

    /** scrcpy-server of exactly this VERSION from the official release, checked against the
     *  pinned SHA-256 each time it is used; downloaded once and kept in the app's files. */
    static byte[] serverJar(Context ctx) throws Exception {
        File f = new File(ctx.getFilesDir(), "scrcpy-server-v" + VERSION);
        if (f.isFile()) {
            byte[] b = Files.readAllBytes(f.toPath());
            if (sha256(b).equals(ScrcpyProto.SERVER_SHA256)) return b;
            f.delete();                                             // damaged or replaced: fetch again
        }
        byte[] b;
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(ScrcpyProto.SERVER_URL).openConnection();
            c.setConnectTimeout(10000);
            c.setReadTimeout(20000);
            c.setInstanceFollowRedirects(true);                     // github.com → its download host
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            try (InputStream in = c.getInputStream()) {
                byte[] buf = new byte[16384];
                for (int n; (n = in.read(buf)) > 0; ) {
                    out.write(buf, 0, n);
                    if (out.size() > 4 << 20) throw new IOException("file too large");
                }
            } finally { c.disconnect(); }
            b = out.toByteArray();
        } catch (IOException e) {
            throw new IOException(L.t("Не удалось скачать scrcpy-server: ") + e.getMessage(), e);
        }
        if (!sha256(b).equals(ScrcpyProto.SERVER_SHA256)) throw new SecurityException("checksum");
        File tmp = new File(f.getPath() + ".part");
        try (FileOutputStream o = new FileOutputStream(tmp)) { o.write(b); }
        if (!tmp.renameTo(f)) throw new IOException("cannot save scrcpy-server");
        return b;
    }

    static String sha256(byte[] b) throws Exception {
        return ScrcpyProto.hex(MessageDigest.getInstance("SHA-256").digest(b));
    }
}
