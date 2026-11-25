// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Build;
import android.view.Surface;

import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.util.Arrays;

/** LIVE video: reads scrcpy media packets from the session's video stream and feeds a
 *  hardware MediaCodec that renders straight to a Surface.
 *
 *  Latency rules: every decoded frame is rendered at once (releaseOutputBuffer(i, true), no
 *  timestamps, no frame pacing), and nothing ever waits in a queue of ours. When the decoder
 *  has no free input buffer within ~10 ms, the packet is dropped, and so is every following
 *  non-key packet until the next key frame (a delta frame without its reference would only
 *  smear the picture). A new config packet (SPS/PPS: the TV rotated or the size changed)
 *  re-creates the codec.
 *
 *  Two threads per decoder: "live-in" reads the socket and queues input, "live-out" drains
 *  output so a decoded frame reaches the screen without waiting for the next packet. */
final class VideoDecoder {
    interface Listener {
        /** The capture size changed (a session packet). Any thread. */
        void onSize(int width, int height);
        /** The stream ended or failed; not called after stop(). Any thread. */
        void onEnded(Exception cause);
        /** Frames were dropped: ask the TV for a key frame now instead of the next periodic one. */
        void onKeyFrameNeeded();
    }

    /** The phone has no decoder for this stream (or it refused the size): reconnecting won't help. */
    static final class CodecError extends IOException {
        CodecError(Exception cause) { super(cause.getMessage(), cause); }
    }

    /** One second of statistics, see sample(). */
    static final class Stats {
        float fps;        // frames rendered per second
        float inFps;      // frames received per second
        int lagMs;        // how far arrival lags behind the TV's timestamps, above the best seen
        int stalls;       // total stalls since start: drops to the next key frame + lag spikes
        int width, height;
    }

    private static final int INPUT_WAIT_US = 10_000;      // ~10 ms for a free input buffer
    private static final int KEY_WAIT_US = 200_000;       // a key frame is worth waiting longer for
    private static final int MAX_PACKET = 16 << 20;       // anything bigger is a broken stream
    private static final int STALL_LAG_MS = 400, STALL_CLEAR_MS = 150;

    private final InputStream in;
    private final String mime;
    private final Surface surface;
    private final Listener listener;
    private final Object lock = new Object();

    private volatile boolean running;
    private Thread inThread;
    private volatile Thread outThread;
    private MediaCodec codec;       // guarded by lock
    private byte[] config;          // last config packet, for re-creation
    private int width, height;
    private boolean waitKey = true; // drop delta frames until a key frame
    private volatile boolean broken; // the codec failed while running: re-create it from `config`

    // statistics (written by the decoder threads, read by sample())
    private volatile int rendered, received, stalls, lagMs;
    private int lastRendered, lastReceived;
    private long lastSample;
    private long minOffsetUs = Long.MAX_VALUE;
    private boolean lagStall;

    VideoDecoder(InputStream in, String mime, int width, int height, Surface surface, Listener listener) {
        this.in = in;
        this.mime = mime;
        this.width = width > 0 ? width : 1280;
        this.height = height > 0 ? height : 720;
        this.surface = surface;
        this.listener = listener;
    }

    void start() {
        running = true;
        lastSample = System.nanoTime();
        inThread = new Thread(this::readLoop, "live-in");
        inThread.setPriority(Thread.MAX_PRIORITY);
        inThread.setDaemon(true);
        inThread.start();
    }

    /** Stops both threads and releases the codec, so the Surface is free when this returns.
     *  The caller closes the session too (that unblocks a read in progress). */
    void stop() {
        running = false;
        Thread t = inThread;
        if (t != null) { t.interrupt(); join(t, 300); }
        releaseCodec();
    }

    /** Counters since the previous call (call about once a second, from one thread). */
    Stats sample() {
        long now = System.nanoTime();
        float sec = Math.max(0.001f, (now - lastSample) / 1e9f);
        int r = rendered, q = received;
        Stats s = new Stats();
        s.fps = (r - lastRendered) / sec;
        s.inFps = (q - lastReceived) / sec;
        s.lagMs = lagMs;
        s.stalls = stalls;
        s.width = width;
        s.height = height;
        lastRendered = r;
        lastReceived = q;
        lastSample = now;
        return s;
    }

    // ------------------------------------------------------------ input side

    private void readLoop() {
        byte[] head = new byte[ScrcpyProto.HEADER_SIZE];
        byte[] buf = new byte[256 * 1024];
        try {
            while (running) {
                readFully(head, ScrcpyProto.HEADER_SIZE);
                ScrcpyProto.Packet h = ScrcpyProto.parsePacket(head);
                if (h.session) {   // new capture size; the matching config packet follows
                    if (h.width > 0 && h.height > 0 && (h.width != width || h.height != height)) {
                        width = h.width;
                        height = h.height;
                        listener.onSize(width, height);
                    }
                    continue;
                }
                if (h.size < 0 || h.size > MAX_PACKET) throw new IOException("bad packet size " + h.size);
                if (buf.length < h.size) buf = new byte[Math.max(h.size, buf.length * 2)];
                readFully(buf, h.size);
                if (h.config) { onConfig(Arrays.copyOf(buf, h.size)); continue; }
                received++;
                trackLag(h.pts);
                if (broken && config != null) {   // a codec error mid-stream: start over from the config
                    broken = false;
                    releaseCodec();
                    createCodec();
                    queue(config, config.length, 0, MediaCodec.BUFFER_FLAG_CODEC_CONFIG, KEY_WAIT_US);
                    if (!waitKey) listener.onKeyFrameNeeded();
                    waitKey = true;
                }
                if (waitKey && !h.keyFrame) continue;
                if (!queue(buf, h.size, h.pts, h.keyFrame ? MediaCodec.BUFFER_FLAG_KEY_FRAME : 0,
                        h.keyFrame ? KEY_WAIT_US : INPUT_WAIT_US)) {
                    if (!waitKey) {   // the decoder can't keep up: skip to the next key frame
                        stalls++;
                        listener.onKeyFrameNeeded();
                    }
                    waitKey = true;
                } else if (h.keyFrame) waitKey = false;
            }
        } catch (Exception e) {
            if (running) {
                running = false;
                releaseCodec();
                listener.onEnded(e);
            }
        }
    }

    private void onConfig(byte[] c) throws IOException {
        boolean same = config != null && Arrays.equals(config, c);
        config = c;
        minOffsetUs = Long.MAX_VALUE;   // a new encoder session may restart its timestamps
        lagStall = false;
        boolean have;
        synchronized (lock) { have = codec != null; }
        if (same && have) return;
        releaseCodec();
        createCodec();
        waitKey = true;
        if (!queue(c, c.length, 0, MediaCodec.BUFFER_FLAG_CODEC_CONFIG, KEY_WAIT_US))
            throw new IOException("decoder takes no config");
    }

    /** Queues one packet; false when no input buffer was free in time. */
    private boolean queue(byte[] data, int len, long ptsUs, int flags, int waitUs) {
        MediaCodec c;
        synchronized (lock) { c = codec; }
        if (c == null) return false;
        try {
            int i = c.dequeueInputBuffer(waitUs);
            if (i < 0) return false;
            ByteBuffer b = c.getInputBuffer(i);
            if (b == null || b.capacity() < len) { c.queueInputBuffer(i, 0, 0, 0, 0); return false; }
            b.clear();
            b.put(data, 0, len);
            c.queueInputBuffer(i, 0, len, Math.max(0, ptsUs), flags);
            return true;
        } catch (IllegalStateException e) {   // released by stop() meanwhile, or a codec error
            codecFailed(c);
            return false;
        }
    }

    /** An exception from a codec that is still the current one is a real decoder error. */
    private void codecFailed(MediaCodec c) {
        synchronized (lock) { if (running && codec == c) broken = true; }
    }

    /** Arrival time minus the TV's timestamp, relative to the best value seen: grows when the
     *  network (or the TV's encoder) falls behind. A spike over STALL_LAG_MS counts as one stall. */
    private void trackLag(long ptsUs) {
        if (ptsUs < 0) return;
        long offset = System.nanoTime() / 1000 - ptsUs;
        if (offset < minOffsetUs) minOffsetUs = offset;
        int lag = (int) ((offset - minOffsetUs) / 1000);
        lagMs = lag;
        if (!lagStall && lag > STALL_LAG_MS) { lagStall = true; stalls++; }
        else if (lagStall && lag < STALL_CLEAR_MS) lagStall = false;
    }

    // ------------------------------------------------------------ codec

    private void createCodec() throws IOException {
        MediaCodec c;
        try {
            c = MediaCodec.createDecoderByType(mime);
            try {
                c.configure(format(true), surface, null, 0);
            } catch (Exception e) {   // some decoders refuse the latency hints: plain format then
                c.release();
                c = MediaCodec.createDecoderByType(mime);
                c.configure(format(false), surface, null, 0);
            }
            c.start();
        } catch (Exception e) {
            throw new CodecError(e);
        }
        synchronized (lock) {
            if (!running) {   // stop() came first: the Surface may be gone already
                try { c.stop(); } catch (Exception ignored) {}
                c.release();
                throw new IOException("stopped");
            }
            codec = c;
        }
        final MediaCodec fc = c;
        Thread t = new Thread(() -> drainLoop(fc), "live-out");
        t.setPriority(Thread.MAX_PRIORITY);
        t.setDaemon(true);
        outThread = t;
        t.start();
    }

    private MediaFormat format(boolean hints) {
        MediaFormat f = MediaFormat.createVideoFormat(mime, width, height);
        f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, Math.max(1 << 20, width * height));   // room for a key frame
        if (hints) {
            if (Build.VERSION.SDK_INT >= 30) f.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
            f.setInteger(MediaFormat.KEY_PRIORITY, 0);                     // real time
            f.setInteger(MediaFormat.KEY_OPERATING_RATE, Short.MAX_VALUE); // as fast as it can
        }
        return f;
    }

    private void drainLoop(MediaCodec c) {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        try {
            while (running && outThread == Thread.currentThread()) {
                int i = c.dequeueOutputBuffer(info, 10_000);
                if (i >= 0) {
                    boolean show = info.size > 0 && (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0;
                    c.releaseOutputBuffer(i, show);
                    if (show) rendered++;
                }
            }
        } catch (IllegalStateException e) {   // released under us (re-created, stopped) or a decoder error
            codecFailed(c);
        }
    }

    private void releaseCodec() {
        MediaCodec c;
        Thread t = outThread;
        outThread = null;   // the drain loop sees it and ends
        if (t != null && t != Thread.currentThread()) join(t, 200);
        synchronized (lock) { c = codec; codec = null; }
        if (c == null) return;
        try { c.stop(); } catch (Exception ignored) {}
        try { c.release(); } catch (Exception ignored) {}
    }

    // ------------------------------------------------------------ helpers

    private void readFully(byte[] b, int len) throws IOException {
        int off = 0;
        while (off < len) {
            int n = in.read(b, off, len - off);
            if (n < 0) throw new EOFException("video stream closed");
            off += n;
        }
    }

    private static void join(Thread t, long ms) {
        try { t.join(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
