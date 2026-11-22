package app.fling;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import java.security.interfaces.RSAPublicKey;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.SSLSocket;

/** Android TV Remote protocol v2: pairing on 6467, session on 6466.
 *  Port of Fling's ATVPairingClient / ATVRemoteClient. */
final class Atv {
    interface Listener {
        void onPower(Boolean on);
        void onApp(String pkg);
        void onPairing(String state, String message);   // starting | pin | verifying | idle | failed
        void onError(String message);
        void onVoice(boolean on);
    }

    static final int POWER = 26, HOME = 3, BACK = 4, UP = 19, DOWN = 20, LEFT = 21, RIGHT = 22, OK = 23,
            VOL_UP = 24, VOL_DOWN = 25, MUTE = 164, PLAY_PAUSE = 85, SEARCH = 84, REWIND = 89, FFWD = 90;

    private static final long F_PING = 1, F_KEY = 2, F_IME = 4, F_VOICE = 8, F_POWER = 32, F_VOLUME = 64, F_APPLINK = 512;

    private final Context ctx;
    private final Listener l;
    private final SharedPreferences prefs;
    private final ExecutorService io = Executors.newSingleThreadExecutor();

    private SSLSocket sock;
    private OutputStream out;
    private volatile boolean connected;
    private String host;
    private long features = F_PING | F_KEY | F_IME | F_POWER | F_VOLUME | F_APPLINK | F_VOICE;
    private long imeCounter, imeField;
    private CountDownLatch started;
    private volatile Boolean isOn;
    private volatile CountDownLatch voiceLatch;
    private volatile long voiceSession = -1;
    private volatile long voiceBegun = -1;

    private SSLSocket pairSock;

    Atv(Context ctx, Listener l) {
        this.ctx = ctx;
        this.l = l;
        this.prefs = ctx.getSharedPreferences("atv", Context.MODE_PRIVATE);
    }

    boolean paired(String host) { return host != null && prefs.getBoolean("paired_" + host, false); }

    // ------------------------------------------------------------ pairing

    void startPairing(String host) {
        io.execute(() -> {
            l.onPairing("starting", "");
            try {
                Tls.ensureIdentity(ctx);
                Tls.closeQuietly(pairSock);
                pairSock = Tls.connect(host, 6467, true, 8000);
                this.host = host;
                sendPair(outer().msg(10, new Proto.W().str(1, "atvremote").str(2, "Fling (Android)")));
                if (!recvPair().has(11)) throw new IOException("unexpected reply");
                Proto.W enc = new Proto.W().varint(1, 3).varint(2, 6);
                sendPair(outer().msg(20, new Proto.W().msg(1, enc).varint(3, 1)));
                if (!recvPair().has(20)) throw new IOException("unexpected reply");
                sendPair(outer().msg(30, new Proto.W().msg(1, enc).varint(2, 1)));
                if (!recvPair().has(31)) throw new IOException("unexpected reply");
                l.onPairing("pin", "");
            } catch (Exception e) {
                Tls.closeQuietly(pairSock);
                pairSock = null;
                l.onPairing("failed", message(e));
            }
        });
    }

    void finishPairing(String pin) {
        io.execute(() -> {
            l.onPairing("verifying", "");
            try {
                if (pairSock == null) throw new IOException(L.t("Сначала начните настройку"));
                byte[] pinBytes = hex(pin);
                RSAPublicKey c = Tls.clientPublic();
                RSAPublicKey s = (RSAPublicKey) ((X509Certificate) pairSock.getSession().getPeerCertificates()[0]).getPublicKey();
                MessageDigest h = MessageDigest.getInstance("SHA-256");
                h.update(Tls.unsigned(c.getModulus()));
                h.update(Tls.unsigned(c.getPublicExponent()));
                h.update(Tls.unsigned(s.getModulus()));
                h.update(Tls.unsigned(s.getPublicExponent()));
                h.update(pinBytes, 1, pinBytes.length - 1);
                byte[] digest = h.digest();
                if (digest[0] != pinBytes[0]) {
                    l.onPairing("pin", "");
                    l.onPairing("failed", L.t("Код не совпадает — посмотрите на экран ТВ ещё раз."));
                    return;
                }
                sendPair(outer().msg(40, new Proto.W().bytes(1, digest)));
                if (!recvPair().has(41)) throw new IOException("unexpected reply");
                prefs.edit().putBoolean("paired_" + host, true).apply();
                Tls.closeQuietly(pairSock);
                pairSock = null;
                l.onPairing("idle", "");
                connectNow(host);
            } catch (Exception e) {
                Tls.closeQuietly(pairSock);
                pairSock = null;
                l.onPairing("failed", message(e));
            }
        });
    }

    void cancelPairing() {
        io.execute(() -> { Tls.closeQuietly(pairSock); pairSock = null; l.onPairing("idle", ""); });
    }

    private Proto.W outer() { return new Proto.W().varint(1, 2).varint(2, 200); }

    private void sendPair(Proto.W m) throws IOException {
        byte[] d = m.out();
        OutputStream o = pairSock.getOutputStream();
        o.write(varint(d.length)); o.write(d); o.flush();
    }

    private Proto.R recvPair() throws IOException {
        pairSock.setSoTimeout(30000);
        InputStream in = pairSock.getInputStream();
        long n = Proto.readVarint(in);
        if (n < 0) throw new IOException(L.t("Телевизор закрыл соединение."));
        Proto.R r = new Proto.R(Proto.readFully(in, (int) n));
        Long st = r.varint(2);
        if (st == null || st != 200) throw new IOException(L.f("Телевизор отклонил код (статус %s). Начните заново.", st));
        return r;
    }

    // ------------------------------------------------------------ session

    void connect(String host) {
        if (!paired(host)) return;
        io.execute(() -> {
            try { connectNow(host); } catch (Exception e) { l.onError(message(e)); }
        });
    }

    private void connectNow(String host) throws Exception {
        if (connected && host.equals(this.host)) return;
        disconnectNow();
        Tls.ensureIdentity(ctx);
        this.host = host;
        sock = Tls.connect(host, 6466, true, 8000);
        out = new BufferedOutputStream(sock.getOutputStream());
        started = new CountDownLatch(1);
        final SSLSocket s = sock;
        Thread t = new Thread(() -> readLoop(s), "atv-read");
        t.setDaemon(true);
        t.start();
        if (!started.await(16, TimeUnit.SECONDS)) {
            disconnectNow();
            throw new IOException(L.t("Телевизор не ответил."));
        }
        connected = true;
    }

    private void disconnectNow() {
        connected = false;
        Tls.closeQuietly(sock);
        sock = null;
    }

    void disconnect() { io.execute(this::disconnectNow); }

    private void readLoop(SSLSocket s) {
        try {
            InputStream in = s.getInputStream();
            while (true) {
                long n = Proto.readVarint(in);
                if (n < 0) break;
                handle(new Proto.R(Proto.readFully(in, (int) n)));
            }
        } catch (Exception ignored) {
        } finally {
            if (s == sock) {
                connected = false;
                isOn = null;
                l.onPower(null);
                l.onApp(null);
            }
        }
    }

    private synchronized void send(byte[] d) throws IOException {
        if (out == null) throw new IOException(L.t("Нет соединения с ТВ"));
        out.write(varint(d.length));
        out.write(d);
        out.flush();
    }

    private void handle(Proto.R m) throws IOException {
        Proto.R c = m.msg(1);
        if (c != null) {
            Long sup = c.varint(1);
            if (sup != null) features &= sup;
            Proto.W dev = new Proto.W().varint(3, 1).str(4, "1").str(5, "atvremote").str(6, "1.0.0");
            Proto.W cfg = new Proto.W();
            if (features != 0) cfg.varint(1, features);
            cfg.msg(2, dev);
            send(new Proto.W().msg(1, cfg).out());
        }
        if (m.has(2)) {
            Proto.W a = new Proto.W();
            if (features != 0) a.varint(1, features);
            send(new Proto.W().msg(2, a).out());
        }
        Proto.R ping = m.msg(8);
        if (ping != null) {
            Long v = ping.varint(1);
            Proto.W p = new Proto.W();
            if (v != null && v != 0) p.varint(1, v);
            send(new Proto.W().msg(9, p).out());
        }
        Proto.R st = m.msg(40);
        if (st != null) {
            Long v = st.varint(1);
            isOn = v != null && v != 0;
            l.onPower(isOn);
            if (started != null) started.countDown();
        }
        Proto.R ime = m.msg(20);
        if (ime != null) {
            Proto.R app = ime.msg(1);
            if (app != null) {
                String pkg = app.str(12);
                l.onApp(pkg == null || pkg.isEmpty() ? null : pkg);
            }
        }
        Proto.R batch = m.msg(21);
        if (batch != null) {
            Long a = batch.varint(1), b = batch.varint(2);
            imeCounter = a == null ? 0 : a;
            imeField = b == null ? 0 : b;
        }
        Proto.R vb = m.msg(30);
        if (vb != null) {
            Long id = vb.varint(1);
            voiceBegun = id == null ? 0 : id;
            CountDownLatch vl = voiceLatch;
            if (vl != null) vl.countDown();
        }
    }

    private void command(String host, ThrowingSend body) {
        io.execute(() -> {
            try {
                connectNow(host);
                body.run();
            } catch (Exception e) {
                disconnectNow();
                l.onError(message(e));
            }
        });
    }

    private interface ThrowingSend { void run() throws Exception; }

    void key(String host, int code) {
        command(host, () -> send(new Proto.W().msg(10, new Proto.W().varint(1, code).varint(2, 3)).out()));
    }

    void launch(String host, String link) {
        command(host, () -> send(new Proto.W().msg(90, new Proto.W().str(1, link)).out()));
    }

    void text(String host, String text) {
        if (text == null || text.isEmpty()) return;
        command(host, () -> {
            int cursor = Math.max(0, text.codePointCount(0, text.length()) - 1);
            Proto.W obj = new Proto.W();
            if (cursor != 0) obj.varint(1, cursor).varint(2, cursor);
            obj.str(3, text);
            Proto.W b = new Proto.W();
            if (imeCounter != 0) b.varint(1, imeCounter);
            if (imeField != 0) b.varint(2, imeField);
            b.msg(3, new Proto.W().varint(1, 1).msg(2, obj));
            send(new Proto.W().msg(21, b).out());
        });
    }

    // ------------------------------------------------------------ voice

    void voiceBegin(String host) {
        command(host, () -> {
            if ((features & F_VOICE) == 0) throw new IOException(L.t("Этот ТВ не принимает голос — используйте ввод текста."));
            voiceLatch = new CountDownLatch(1);
            voiceBegun = -1;
            send(new Proto.W().msg(10, new Proto.W().varint(1, SEARCH).varint(2, 3)).out());
            if (!voiceLatch.await(3, TimeUnit.SECONDS)) throw new IOException(L.t("Телевизор не открыл голосовой поиск."));
            voiceSession = voiceBegun;
            send(new Proto.W().msg(30, new Proto.W().varint(1, voiceSession)).out());
            l.onVoice(true);
        });
    }

    void voiceChunk(byte[] pcm, int len) {
        final byte[] chunk = new byte[Math.max(len, 8192)];
        System.arraycopy(pcm, 0, chunk, 0, len);
        io.execute(() -> {
            if (voiceSession < 0) return;
            try {
                for (int o = 0; o < chunk.length; o += 20480) {
                    int n = Math.min(20480, chunk.length - o);
                    byte[] s = new byte[n];
                    System.arraycopy(chunk, o, s, 0, n);
                    send(new Proto.W().msg(31, new Proto.W().varint(1, voiceSession).bytes(2, s)).out());
                }
            } catch (Exception e) { l.onError(message(e)); }
        });
    }

    void voiceEnd() {
        io.execute(() -> {
            long id = voiceSession;
            voiceSession = -1;
            try { if (id >= 0) send(new Proto.W().msg(32, new Proto.W().varint(1, id)).out()); } catch (Exception ignored) {}
            l.onVoice(false);
        });
    }

    // ------------------------------------------------------------ helpers

    private static byte[] varint(long v) { Proto.W w = new Proto.W(); w.raw(v); return w.out(); }

    private static byte[] hex(String s) throws IOException {
        s = s.trim();
        if (s.length() != 6) throw new IOException(L.t("Код должен быть из 6 символов"));
        byte[] b = new byte[3];
        try {
            for (int i = 0; i < 3; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        } catch (NumberFormatException e) { throw new IOException(L.t("Код состоит из цифр 0-9 и букв A-F")); }
        return b;
    }

    static String message(Exception e) {
        if (e instanceof javax.net.ssl.SSLException)
            return L.t("Телевизор больше не доверяет этому телефону — свяжите его с ТВ заново.");
        if (e instanceof java.net.SocketTimeoutException) return L.t("Телевизор не ответил.");
        if (e instanceof java.net.ConnectException || e instanceof java.net.NoRouteToHostException)
            return L.t("Не удалось связаться с ТВ. Телефон и ТВ в одной сети Wi-Fi?");
        String m = e.getMessage();
        return m == null ? e.getClass().getSimpleName() : m;
    }
}
