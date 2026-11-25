// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/** The scrcpy client/server protocol of one exact scrcpy version (no Android classes here: Scrcpy.java holds the session,
 *  so android/test/ScrcpyCheck.java can check it on a plain JVM).
 *  Source of truth: Genymobile/scrcpy tag v5.0.1 — doc/develop.md, app/src/demuxer.c,
 *  app/src/control_msg.c, server/…/device/Streamer.java, DesktopConnection.java,
 *  control/ControlMessageReader.java. The server refuses any other client version. */
final class ScrcpyProto {
    private ScrcpyProto() {}

    static final String VERSION = "5.0.1";
    /** scrcpy-server-v5.0.1 from the release's SHA256SUMS.txt. */
    static final String SERVER_SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536";
    static final String SERVER_URL = "https://github.com/Genymobile/scrcpy/releases/download/v" + VERSION
            + "/scrcpy-server-v" + VERSION;
    /** Our own name, not scrcpy-server.jar: a desktop scrcpy of another version pushes that one. */
    static final String DEVICE_PATH = "/data/local/tmp/fling-scrcpy-server-" + VERSION + ".jar";

    static final int CODEC_H264 = 0x68323634, CODEC_H265 = 0x68323635, CODEC_AV1 = 0x00617631;
    static final int DEVICE_NAME_LENGTH = 64;
    static final int HEADER_SIZE = 12;
    static final int TEXT_MAX = 300;   // SC_CONTROL_MSG_INJECT_TEXT_MAX_LENGTH, bytes of UTF-8

    private static final long FLAG_SESSION = 1L << 63, FLAG_CONFIG = 1L << 62, FLAG_KEY_FRAME = 1L << 61;
    private static final long PTS_MASK = FLAG_KEY_FRAME - 1;

    // control message types (ControlMessage.java)
    static final int TYPE_INJECT_KEYCODE = 0, TYPE_INJECT_TEXT = 1, TYPE_INJECT_TOUCH_EVENT = 2,
            TYPE_BACK_OR_SCREEN_ON = 4, TYPE_RESET_VIDEO = 17;
    static final int KEY_DOWN = 0, KEY_UP = 1;
    static final int MOTION_DOWN = 0, MOTION_UP = 1, MOTION_MOVE = 2;
    /** SC_POINTER_ID_GENERIC_FINGER: a touch, not a mouse. */
    static final long POINTER_FINGER = -2;

    static String socketName(int scid) { return String.format(Locale.ROOT, "scrcpy_%08x", scid); }

    static String mime(int codecId) {
        return codecId == CODEC_H264 ? "video/avc" : codecId == CODEC_H265 ? "video/hevc"
                : codecId == CODEC_AV1 ? "video/av01" : null;
    }

    // ------------------------------------------------------------ server start

    /** The shell command that starts the server (tunnel_forward: the TV listens, we connect
     *  through ADB streams, no adb forward needed). cleanup=false: Fling changes no TV setting
     *  that needs restoring, and the jar stays for the next start (its hash is checked).
     *  codec: "h264" | "h265" | "av1". */
    static String serverCommand(int scid, String codec, int maxSize, int bitRate, int maxFps, int iFrameSeconds, boolean audio) {
        return "CLASSPATH=" + DEVICE_PATH + " app_process / com.genymobile.scrcpy.Server " + VERSION
                + String.format(Locale.ROOT, " scid=%08x", scid)
                + " log_level=info tunnel_forward=true"
                + " video_codec=" + codec
                + " max_size=" + maxSize
                + " video_bit_rate=" + bitRate
                + " max_fps=" + maxFps
                + " video_codec_options=i-frame-interval=" + iFrameSeconds
                + " audio=" + audio + " control=true cleanup=false power_off_on_close=false"
                + " clipboard_autosync=false stay_awake=false";
    }

    // ------------------------------------------------------------ video socket

    /** The 64-byte device meta (NUL-padded UTF-8 name). */
    static String deviceName(byte[] meta) {
        int n = 0;
        while (n < meta.length && meta[n] != 0) n++;
        return new String(meta, 0, n, StandardCharsets.UTF_8);
    }

    static int readU32(byte[] b, int o) {
        return ((b[o] & 0xFF) << 24) | ((b[o + 1] & 0xFF) << 16) | ((b[o + 2] & 0xFF) << 8) | (b[o + 3] & 0xFF);
    }

    /** One 12-byte header: a session packet (new capture size) or a media packet header. */
    static final class Packet {
        boolean session;
        int width, height;        // session
        boolean config, keyFrame; // media
        long pts;                 // media, µs; -1 for config packets
        int size;                 // media payload bytes
    }

    static Packet parsePacket(byte[] h) {
        Packet r = new Packet();
        if ((h[0] & 0x80) != 0) {
            r.session = true;
            r.width = readU32(h, 4);
            r.height = readU32(h, 8);
            return r;
        }
        long v = ByteBuffer.wrap(h, 0, 8).getLong();
        r.config = (v & FLAG_CONFIG) != 0;
        r.keyFrame = (v & FLAG_KEY_FRAME) != 0;
        r.pts = r.config ? -1 : v & PTS_MASK;
        r.size = readU32(h, 8);
        return r;
    }

    // ------------------------------------------------------------ control socket (client → TV)

    static byte[] key(int action, int keycode, int repeat, int metaState) {
        return ByteBuffer.allocate(14).put((byte) TYPE_INJECT_KEYCODE).put((byte) action)
                .putInt(keycode).putInt(repeat).putInt(metaState).array();
    }

    /** Text as UTF-8, cut at TEXT_MAX bytes without splitting a character. */
    static byte[] text(String s) {
        byte[] u = s.getBytes(StandardCharsets.UTF_8);
        int len = Math.min(u.length, TEXT_MAX);
        while (len > 0 && len < u.length && (u[len] & 0xC0) == 0x80) len--;   // don't end inside a sequence
        return ByteBuffer.allocate(5 + len).put((byte) TYPE_INJECT_TEXT).putInt(len).put(u, 0, len).array();
    }

    /** Touch at (x, y) in video pixels; w × h must be the current video size or the server
     *  drops the event (the TV may have rotated meanwhile). */
    static byte[] touch(int action, long pointerId, int x, int y, int w, int h, float pressure, int actionButton, int buttons) {
        return ByteBuffer.allocate(32).put((byte) TYPE_INJECT_TOUCH_EVENT).put((byte) action)
                .putLong(pointerId).putInt(x).putInt(y).putShort((short) w).putShort((short) h)
                .putShort((short) u16fp(pressure)).putInt(actionButton).putInt(buttons).array();
    }

    /** Asks the encoder for a fresh key frame (after a decoder error). */
    static byte[] resetVideo() { return new byte[]{(byte) TYPE_RESET_VIDEO}; }

    /** sc_float_to_u16fp: [0, 1] → 0..0xFFFF, 1.0 exactly 0xFFFF. */
    static int u16fp(float f) {
        if (f <= 0) return 0;
        if (f >= 1) return 0xFFFF;
        return Math.min(0xFFFF, (int) (f * 65536f));
    }

    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format(Locale.ROOT, "%02x", x & 0xFF));
        return s.toString();
    }
}
