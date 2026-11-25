// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;

/** One long-lived ADB connection with several streams at once (LIVE: the server's shell,
 *  `sync:` for the upload, `localabstract:scrcpy_<scid>` for video and control).
 *  A reader thread dispatches packets by stream id; each stream has one write in flight, as
 *  the ADB protocol requires (WRTE → wait for OKAY). Incoming data is acknowledged at once,
 *  so the TV never waits for us: the video is read as fast as it arrives. */
final class AdbLink implements AutoCloseable {
    private final Adb.Session s;
    private final Map<Integer, Stream> streams = new ConcurrentHashMap<>();
    private int nextId = 1;
    private volatile boolean closed;
    private volatile IOException failure;

    private AdbLink(Adb.Session s) {
        this.s = s;
        Thread t = new Thread(this::readLoop, "adb-link");
        t.setDaemon(true);
        t.start();
    }

    static AdbLink open(Adb adb, String host, int port) throws Exception {
        Adb.Session s = adb.connect(host, port);
        s.sock.setSoTimeout(0);   // streams can be quiet for long (a still TV screen); Live watches liveness
        try { s.sock.setKeepAlive(true); } catch (Exception ignored) {}
        return new AdbLink(s);
    }

    boolean isOpen() { return !closed; }

    private void readLoop() {
        try {
            while (!closed) {
                Adb.Msg m = Adb.read(s.in);
                Stream st = streams.get(m.a1);
                if (m.cmd == Adb.OKAY) {
                    if (st != null) st.okay(m.a0);
                } else if (m.cmd == Adb.WRTE) {
                    if (st != null) {
                        st.data(m.data);
                        write(Adb.OKAY, m.a1, m.a0, null);
                    } else write(Adb.CLSE, m.a1, m.a0, null);   // a stream we no longer know
                } else if (m.cmd == Adb.CLSE) {
                    if (st != null) st.closedByPeer();
                }
            }
        } catch (IOException e) {
            failure = e;
        } finally {
            close();
        }
    }

    private synchronized void write(int cmd, int a0, int a1, byte[] data) throws IOException {
        if (closed) throw failure != null ? failure : new IOException("ADB connection closed");
        Adb.send(s.out, cmd, a0, a1, data);
    }

    /** Opens a service ("shell:…", "sync:", "localabstract:…"). Throws if the TV refuses it
     *  (for a local socket: nothing listens there yet). */
    Stream open(String service, long timeoutMs) throws IOException {
        int id;
        synchronized (this) { id = nextId++; }
        Stream st = new Stream(id);
        streams.put(id, st);
        write(Adb.OPEN, id, 0, (service + "\0").getBytes(StandardCharsets.UTF_8));
        try {
            if (!st.opened.tryAcquire(timeoutMs, TimeUnit.MILLISECONDS)) throw new IOException("no answer for " + service);
        } catch (InterruptedException e) { throw new InterruptedIOException(); }
        if (st.remote == 0) { streams.remove(id); throw new IOException("refused: " + service); }
        return st;
    }

    @Override public void close() {
        if (closed) return;
        closed = true;
        try { s.sock.close(); } catch (IOException ignored) {}
        for (Stream st : streams.values()) st.closedByPeer();
        streams.clear();
    }

    // ------------------------------------------------------------ one stream

    private static final byte[] EOF = new byte[0];

    final class Stream implements AutoCloseable {
        final int local;
        volatile int remote;
        final Semaphore opened = new Semaphore(0), canWrite = new Semaphore(0);
        private final LinkedBlockingQueue<byte[]> queue = new LinkedBlockingQueue<>();
        private byte[] cur;
        private int pos;
        private volatile boolean done;

        Stream(int local) { this.local = local; }

        void okay(int remoteId) {
            if (remote == 0) { remote = remoteId; opened.release(); }
            canWrite.release();   // the first OKAY opens the stream, each later one acknowledges a WRTE
        }

        void data(byte[] d) { if (d.length > 0) queue.add(d); }

        void closedByPeer() {
            if (done) return;
            done = true;
            streams.remove(local);
            queue.add(EOF);
            opened.release();
            canWrite.release(1000);   // wake any writer; write() then sees done
        }

        final InputStream in = new InputStream() {
            @Override public int read() throws IOException {
                byte[] one = new byte[1];
                return read(one, 0, 1) < 0 ? -1 : one[0] & 0xFF;
            }

            @Override public int read(byte[] b, int off, int len) throws IOException {
                if (len == 0) return 0;
                try {
                    if (cur == null || pos >= cur.length) {
                        cur = queue.take();
                        pos = 0;
                        if (cur == EOF) { queue.add(EOF); cur = null; return -1; }
                    }
                } catch (InterruptedException e) { throw new InterruptedIOException(); }
                int n = Math.min(len, cur.length - pos);
                System.arraycopy(cur, pos, b, off, n);
                pos += n;
                return n;
            }

            @Override public int available() {
                return cur != null && cur != EOF ? cur.length - pos : 0;
            }
        };

        void readFully(byte[] b) throws IOException {
            int o = 0;
            while (o < b.length) {
                int n = in.read(b, o, b.length - o);
                if (n < 0) throw new IOException("stream ended");
                o += n;
            }
        }

        /** Writes everything, in packets the TV accepts, one at a time. */
        synchronized void write(byte[] b, int off, int len) throws IOException {
            while (len > 0) {
                int n = Math.min(len, s.maxData);
                byte[] chunk = new byte[n];
                System.arraycopy(b, off, chunk, 0, n);
                try {
                    if (!canWrite.tryAcquire(15, TimeUnit.SECONDS)) throw new IOException("ADB write timed out");
                } catch (InterruptedException e) { throw new InterruptedIOException(); }
                if (done) throw new IOException("stream closed");
                AdbLink.this.write(Adb.WRTE, local, remote, chunk);
                off += n; len -= n;
            }
        }

        void write(byte[] b) throws IOException { write(b, 0, b.length); }

        boolean isOpen() { return !done && !closed; }

        @Override public void close() {
            if (done) return;
            try { AdbLink.this.write(Adb.CLSE, local, remote, null); } catch (IOException ignored) {}
            closedByPeer();
        }
    }

    // ------------------------------------------------------------ helpers

    /** Runs a shell command to its end and returns the output (small outputs only). */
    String shell(String command, long timeoutMs) throws IOException {
        Stream st = open("shell:" + command, timeoutMs);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] b = new byte[8192];
        for (int n; (n = st.in.read(b, 0, b.length)) > 0; ) out.write(b, 0, n);
        st.close();
        return out.toString("UTF-8");
    }

    /** `adb push` over the sync protocol: SEND "path,mode", DATA chunks of ≤ 64 KiB, DONE mtime. */
    void push(byte[] file, String remotePath, int mode) throws IOException {
        Stream st = open("sync:", 5000);
        try {
            byte[] pm = (remotePath + "," + mode).getBytes(StandardCharsets.UTF_8);
            st.write(syncHeader("SEND", pm.length));
            st.write(pm);
            for (int o = 0; o < file.length; o += 65536) {
                int n = Math.min(65536, file.length - o);
                st.write(syncHeader("DATA", n));
                st.write(file, o, n);
            }
            st.write(syncHeader("DONE", (int) (System.currentTimeMillis() / 1000)));
            byte[] r = new byte[8];
            st.readFully(r);
            String id = new String(r, 0, 4, StandardCharsets.US_ASCII);
            if (!id.equals("OKAY")) {
                int len = ByteBuffer.wrap(r, 4, 4).order(ByteOrder.LITTLE_ENDIAN).getInt();
                byte[] msg = new byte[Math.max(0, Math.min(len, 4096))];
                st.readFully(msg);
                throw new IOException("push failed: " + new String(msg, StandardCharsets.UTF_8));
            }
            st.write(syncHeader("QUIT", 0));
        } finally {
            st.close();
        }
    }

    private static byte[] syncHeader(String id, int value) {
        return ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
                .put(id.getBytes(StandardCharsets.US_ASCII)).putInt(value).array();
    }
}
