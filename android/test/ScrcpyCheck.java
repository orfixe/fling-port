// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import java.nio.ByteBuffer;
import java.util.Arrays;

/** JVM self-check of the scrcpy protocol code (ScrcpyProto.java), no device needed:
 *    cd android && javac -d build/check src/app/fling/ScrcpyProto.java test/ScrcpyCheck.java \
 *      && java -ea -cp build/check app.fling.ScrcpyCheck
 *  Expected bytes are copied from scrcpy v5.0.1 app/tests/test_control_msg_serialize.c; the
 *  stream fixture is built the way server/…/device/Streamer.java writes it. */
public final class ScrcpyCheck {
    private static int failures;

    public static void main(String[] a) {
        // INJECT_KEYCODE: UP, ENTER, repeat 5, meta SHIFT_ON|SHIFT_LEFT_ON
        same("key", ScrcpyProto.key(ScrcpyProto.KEY_UP, 66, 5, 0x41),
                0, 0x01, 0, 0, 0, 0x42, 0, 0, 0, 0x05, 0, 0, 0, 0x41);
        // INJECT_TEXT "hello, world!"
        same("text", ScrcpyProto.text("hello, world!"),
                1, 0, 0, 0, 0x0d, 'h', 'e', 'l', 'l', 'o', ',', ' ', 'w', 'o', 'r', 'l', 'd', '!');
        // long text is cut at 300 bytes
        char[] many = new char[400];
        Arrays.fill(many, 'a');
        byte[] t = ScrcpyProto.text(new String(many));
        check("text long size", t.length == 5 + 300);
        check("text long len", t[3] == 0x01 && t[4] == 0x2c);
        // a 2-byte character across the limit is dropped whole, not split
        byte[] cyr = ScrcpyProto.text(new String(many, 0, 299) + "ж");
        check("text utf-8 boundary", cyr.length == 5 + 299);
        // INJECT_TOUCH_EVENT
        same("touch", ScrcpyProto.touch(ScrcpyProto.MOTION_DOWN, 0x1234567887654321L, 100, 200, 1080, 1920, 1f, 1, 1),
                2, 0x00, 0x12, 0x34, 0x56, 0x78, 0x87, 0x65, 0x43, 0x21,
                0, 0, 0, 0x64, 0, 0, 0, 0xc8, 0x04, 0x38, 0x07, 0x80, 0xff, 0xff,
                0, 0, 0, 1, 0, 0, 0, 1);
        check("u16fp 0.5", ScrcpyProto.u16fp(0.5f) == 0x8000);
        same("reset video", ScrcpyProto.resetVideo(), 17);

        // session packet as Streamer.writeSessionMeta(1920, 1080, false) writes it
        byte[] sess = ByteBuffer.allocate(12).putInt((int) ((1L << 63) >> 32)).putInt(1920).putInt(1080).array();
        ScrcpyProto.Packet s = ScrcpyProto.parsePacket(sess);
        check("session flag", s.session);
        check("session size", s.width == 1920 && s.height == 1080);
        // config packet: PACKET_FLAG_CONFIG, 27 bytes
        ScrcpyProto.Packet c = ScrcpyProto.parsePacket(ByteBuffer.allocate(12).putLong(1L << 62).putInt(27).array());
        check("config", !c.session && c.config && !c.keyFrame && c.pts == -1 && c.size == 27);
        // key frame at pts 1 234 567 µs, 40 000 bytes
        ScrcpyProto.Packet k = ScrcpyProto.parsePacket(ByteBuffer.allocate(12).putLong(1234567L | (1L << 61)).putInt(40000).array());
        check("key frame", !k.session && !k.config && k.keyFrame && k.pts == 1234567L && k.size == 40000);
        // plain frame with a large pts (> 2^32)
        ScrcpyProto.Packet f = ScrcpyProto.parsePacket(ByteBuffer.allocate(12).putLong(5_000_000_000L).putInt(1).array());
        check("frame", !f.session && !f.keyFrame && f.pts == 5_000_000_000L && f.size == 1);

        // device meta: 64 bytes, NUL padded
        byte[] meta = new byte[64];
        byte[] name = "Chromecast HD".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        System.arraycopy(name, 0, meta, 0, name.length);
        check("device name", ScrcpyProto.deviceName(meta).equals("Chromecast HD"));

        check("socket name", ScrcpyProto.socketName(0x1a2b3c).equals("scrcpy_001a2b3c"));
        String cmd = ScrcpyProto.serverCommand(0x7fffffff, "h264", 1280, 8_000_000, 60, 2, false);
        check("command version", cmd.contains("com.genymobile.scrcpy.Server 5.0.1 scid=7fffffff "));
        check("command forward", cmd.contains(" tunnel_forward=true") && cmd.contains(" audio=false") && cmd.contains(" control=true"));
        check("mime", "video/avc".equals(ScrcpyProto.mime(0x68323634)) && "video/hevc".equals(ScrcpyProto.mime(0x68323635)));

        if (failures > 0) { System.out.println(failures + " check(s) FAILED"); System.exit(1); }
        System.out.println("ScrcpyCheck: all checks passed");
    }

    private static void same(String what, byte[] got, int... want) {
        byte[] w = new byte[want.length];
        for (int i = 0; i < want.length; i++) w[i] = (byte) want[i];
        check(what + " = " + ScrcpyProto.hex(got), Arrays.equals(got, w));
    }

    private static void check(String what, boolean ok) {
        if (!ok) { failures++; System.out.println("FAIL " + what); }
    }
}
