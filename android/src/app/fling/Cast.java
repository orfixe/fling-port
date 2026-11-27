// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.Context;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.SSLSocket;

/** Google Cast V2 sender: receiver/media status, transport controls and LOAD
 *  of direct media into the Default Media Receiver. */
final class Cast {
    interface Listener {
        void onStatus(Status s);
        void onError(String message);
        /** A background (re)connect failed: the TV is off or away. Shown as status, not as an error. */
        default void onOffline(String message) {}
    }

    static final class Status {
        String state = "IDLE";
        double elapsed;
        double duration;
        long updatedAt;
        int volume = -1;
        boolean muted;
        String title, subtitle, imageUrl, contentId, appName, appId;

        boolean hasMedia() { return "PLAYING".equals(state) || "PAUSED".equals(state) || "BUFFERING".equals(state); }
        boolean playing() { return "PLAYING".equals(state) || "BUFFERING".equals(state); }

        double elapsedNow() {
            double e = elapsed;
            if ("PLAYING".equals(state) && updatedAt > 0)
                e += (SystemClock.elapsedRealtime() - updatedAt) / 1000.0;
            if (duration > 0 && e > duration) e = duration;
            return Math.max(0, e);
        }

        boolean stuck() { return "BUFFERING".equals(state) && elapsed == 0 && duration == 0; }

        Status copy() {
            Status s = new Status();
            s.state = state; s.elapsed = elapsed; s.duration = duration; s.updatedAt = updatedAt;
            s.volume = volume; s.muted = muted; s.title = title; s.subtitle = subtitle;
            s.imageUrl = imageUrl; s.contentId = contentId; s.appName = appName; s.appId = appId;
            return s;
        }
    }

    static final String NS_CONN = "urn:x-cast:com.google.cast.tp.connection";
    static final String NS_HEART = "urn:x-cast:com.google.cast.tp.heartbeat";
    static final String NS_RECV = "urn:x-cast:com.google.cast.receiver";
    static final String NS_MEDIA = "urn:x-cast:com.google.cast.media";
    static final String DEFAULT_RECEIVER = "CC1AD845";
    private static final String SENDER = "sender-0", RECEIVER = "receiver-0";

    private static final Map<String, String[]> OEMBED = new HashMap<>();

    private final Context ctx;
    private final Listener l;
    private final ScheduledExecutorService io = Executors.newSingleThreadScheduledExecutor();

    private SSLSocket sock;
    private DataOutputStream out;
    private volatile boolean connected;
    private String host;
    private ScheduledFuture<?> heartbeat;
    private int requestId = 1;

    private String transportId;      // connected app transport
    private String sessionId;
    private long mediaSessionId = -1;
    private final Status st = new Status();

    /** One thing to play on the Default Media Receiver. */
    static final class Media {
        final String url, title, mime;
        String subtitle, image;
        Media(String url, String title, String mime) { this.url = url; this.title = title; this.mime = mime; }
    }

    // pending LOAD waiting for the default receiver to come up
    private String pendingUrl;
    private java.util.List<Media> pendingItems;

    Cast(Context ctx, Listener l) {
        this.ctx = ctx;
        this.l = l;
    }

    Status status() { synchronized (st) { return st.copy(); } }

    // ------------------------------------------------------------ connection

    void connect(String host) {
        io.execute(() -> {
            try { ensure(host); } catch (Exception e) { markDown(); l.onOffline(message(e)); }
        });
    }

    void disconnect() { io.execute(this::closeNow); }

    private void ensure(String h) throws Exception {
        if (h == null) throw new IOException(L.t("Устройство не выбрано"));
        if (connected && h.equals(host)) return;
        closeNow();
        host = h;
        sock = Tls.connect(h, 8009, false, 8000);
        out = new DataOutputStream(sock.getOutputStream());
        try {                                       // a real Cast device, no man in the middle
            sock.setSoTimeout(8000);
            CastAuth.authenticate(sock, new DataInputStream(sock.getInputStream()), out, SENDER, RECEIVER);
            sock.setSoTimeout(0);
        } catch (Exception e) {
            closeNow();
            throw e;
        }
        connected = true;
        final SSLSocket s = sock;
        Thread t = new Thread(() -> readLoop(s), "cast-read");
        t.setDaemon(true);
        t.start();
        sendNow(RECEIVER, NS_CONN, new JSONObject().put("type", "CONNECT"));
        sendNow(RECEIVER, NS_RECV, req("GET_STATUS"));
        heartbeat = io.scheduleWithFixedDelay(() -> {
            try { if (connected) sendNow(RECEIVER, NS_HEART, new JSONObject().put("type", "PING")); }
            catch (Exception e) { markDown(); }
        }, 5, 5, TimeUnit.SECONDS);
    }

    private void closeNow() {
        if (heartbeat != null) { heartbeat.cancel(false); heartbeat = null; }
        connected = false;
        Tls.closeQuietly(sock);
        sock = null;
        out = null;
        transportId = null;
        mediaSessionId = -1;
    }

    private void markDown() {
        connected = false;
        transportId = null;
    }

    private void fail(Exception e) {
        markDown();
        l.onError(message(e));
    }

    static String message(Exception e) {
        if (e instanceof java.net.SocketTimeoutException) return L.t("Телевизор не ответил.");
        if (e instanceof java.net.ConnectException || e instanceof java.net.NoRouteToHostException)
            return L.t("Не удалось связаться с ТВ. Он включён и в той же сети Wi‑Fi или Tailscale?");
        if (e instanceof IOException && e.getMessage() == null) return L.t("Соединение с ТВ потеряно.");
        String m = e.getMessage();
        return m == null ? e.getClass().getSimpleName() : m;
    }

    // ------------------------------------------------------------ wire

    private JSONObject req(String type) throws Exception {
        return new JSONObject().put("type", type).put("requestId", requestId++);
    }

    private synchronized void sendNow(String dest, String ns, JSONObject payload) throws IOException {
        if (out == null) throw new IOException(L.t("Нет соединения с ТВ"));
        byte[] msg = new Proto.W()
                .varint(1, 0)
                .str(2, SENDER)
                .str(3, dest)
                .str(4, ns)
                .varint(5, 0)
                .str(6, payload.toString())
                .out();
        out.writeInt(msg.length);
        out.write(msg);
        out.flush();
    }

    private interface Body { void run() throws Exception; }

    /** Runs on the writer thread, reconnecting first when needed. */
    private void command(Body b) {
        io.execute(() -> {
            try {
                ensure(host);
                b.run();
            } catch (Exception e) { fail(e); }
        });
    }

    private void readLoop(SSLSocket s) {
        try {
            DataInputStream in = new DataInputStream(s.getInputStream());
            while (true) {
                int n = in.readInt();
                if (n <= 0 || n > 4 * 1024 * 1024) throw new IOException("bad frame");
                Proto.R m = new Proto.R(Proto.readFully((InputStream) in, n));
                String ns = m.str(4), src = m.str(2), payload = m.str(6);
                if (ns == null || payload == null) continue;
                try { handle(ns, src, new JSONObject(payload)); } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {
        } finally {
            if (s == sock) markDown();
        }
    }

    private void handle(String ns, String src, JSONObject p) {
        String type = p.optString("type");
        if (NS_HEART.equals(ns) && "PING".equals(type)) {
            io.execute(() -> { try { sendNow(src == null ? RECEIVER : src, NS_HEART, new JSONObject().put("type", "PONG")); } catch (Exception ignored) {} });
            return;
        }
        if (NS_CONN.equals(ns) && "CLOSE".equals(type)) {
            if (src != null && src.equals(transportId)) { transportId = null; mediaSessionId = -1; }
            return;
        }
        if (NS_RECV.equals(ns) && "RECEIVER_STATUS".equals(type)) onReceiver(p.optJSONObject("status"));
        else if (NS_MEDIA.equals(ns) && "MEDIA_STATUS".equals(type)) onMedia(p.optJSONArray("status"));
        else if (NS_MEDIA.equals(ns) && ("LOAD_FAILED".equals(type) || "LOAD_CANCELLED".equals(type)))
            l.onError(L.t("Телевизор не смог открыть это видео."));
        else if (NS_RECV.equals(ns) && "LAUNCH_ERROR".equals(type))
            l.onError(L.f("Телевизор не запустил проигрыватель (%s).", p.optString("reason")));
    }

    private void onReceiver(JSONObject s) {
        if (s == null) return;
        JSONObject vol = s.optJSONObject("volume");
        JSONObject app = null;
        JSONArray apps = s.optJSONArray("applications");
        if (apps != null) for (int i = 0; i < apps.length(); i++) {
            JSONObject a = apps.optJSONObject(i);
            if (a != null && !a.optBoolean("isIdleScreen", false)) { app = a; break; }
        }
        String newTransport = app == null ? null : app.optString("transportId", null);
        synchronized (st) {
            if (vol != null) {
                if (vol.has("level")) st.volume = (int) Math.round(vol.optDouble("level", 0) * 100);
                st.muted = vol.optBoolean("muted", false);
            }
            st.appId = app == null ? null : app.optString("appId", null);
            st.appName = app == null ? null : app.optString("displayName", null);
            sessionId = app == null ? null : app.optString("sessionId", null);
            if (app == null) {
                st.state = "IDLE";
                st.title = st.subtitle = st.imageUrl = st.contentId = null;
                st.elapsed = st.duration = 0;
            }
        }
        if (newTransport != null && !newTransport.equals(transportId)) {
            final String tid = newTransport;
            final boolean isDefault = app != null && DEFAULT_RECEIVER.equals(app.optString("appId"));
            io.execute(() -> {
                try {
                    transportId = tid;
                    mediaSessionId = -1;
                    sendNow(tid, NS_CONN, new JSONObject().put("type", "CONNECT"));
                    sendNow(tid, NS_MEDIA, req("GET_STATUS"));
                    if (isDefault && pendingUrl != null) doLoad();
                } catch (Exception e) { fail(e); }
            });
        } else if (newTransport == null) {
            transportId = null;
            mediaSessionId = -1;
        } else if (pendingUrl != null && app != null && DEFAULT_RECEIVER.equals(app.optString("appId"))) {
            io.execute(() -> { try { doLoad(); } catch (Exception e) { fail(e); } });
        }
        publish();
    }

    private void onMedia(JSONArray arr) {
        JSONObject m = arr == null || arr.length() == 0 ? null : arr.optJSONObject(0);
        String ytFetch = null;
        synchronized (st) {
            if (m == null) {
                st.state = "IDLE";
                st.elapsed = 0; st.duration = 0;
                mediaSessionId = -1;
            } else {
                mediaSessionId = m.optLong("mediaSessionId", mediaSessionId);
                st.state = m.optString("playerState", st.state).toUpperCase(Locale.ROOT);
                if ("IDLE".equals(st.state)) {
                    st.elapsed = 0;
                } else {
                    st.elapsed = m.optDouble("currentTime", 0);
                    if (Double.isNaN(st.elapsed)) st.elapsed = 0;
                }
                st.updatedAt = SystemClock.elapsedRealtime();
                JSONObject vol = m.optJSONObject("volume");
                if (vol != null && vol.has("level") && st.volume < 0)
                    st.volume = (int) Math.round(vol.optDouble("level", 0) * 100);
                JSONObject media = m.optJSONObject("media");
                if (media != null) {
                    double d = media.optDouble("duration", 0);
                    st.duration = Double.isNaN(d) || d < 0 ? 0 : d;
                    String cid = media.optString("contentId", null);
                    if (cid != null && !cid.equals(st.contentId)) {
                        st.title = st.subtitle = st.imageUrl = null;
                    }
                    st.contentId = cid;
                    JSONObject md = media.optJSONObject("metadata");
                    if (md != null) {
                        String t = md.optString("title", null);
                        if (t != null && !t.isEmpty()) st.title = t;
                        String sub = md.optString("subtitle", null);
                        if (sub == null || sub.isEmpty()) sub = md.optString("artist", null);
                        if (sub != null && !sub.isEmpty()) st.subtitle = sub;
                        JSONArray imgs = md.optJSONArray("images");
                        if (imgs != null && imgs.length() > 0) {
                            JSONObject im = imgs.optJSONObject(0);
                            if (im != null) st.imageUrl = im.optString("url", st.imageUrl);
                        }
                    }
                }
                if (st.contentId != null && st.contentId.matches("[\\w-]{11}")) {
                    if (st.imageUrl == null) st.imageUrl = "https://img.youtube.com/vi/" + st.contentId + "/hqdefault.jpg";
                    if (st.title == null) {
                        String[] cached;
                        synchronized (OEMBED) { cached = OEMBED.get(st.contentId); }
                        if (cached != null) {
                            st.title = cached[0];
                            if (cached[1] != null && st.subtitle == null) st.subtitle = cached[1] + " · YouTube";
                        } else ytFetch = st.contentId;
                    }
                }
            }
        }
        publish();
        if (ytFetch != null) fetchYouTube(ytFetch);
    }

    private void fetchYouTube(String id) {
        synchronized (OEMBED) {
            if (OEMBED.containsKey(id)) return;
            OEMBED.put(id, new String[]{null, null});   // in-flight marker
        }
        Thread t = new Thread(() -> {
            String title = null, author = null;
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=" + id).openConnection();
                c.setConnectTimeout(4000);
                c.setReadTimeout(4000);
                if (c.getResponseCode() == 200) {
                    byte[] body = readAll(c.getInputStream());
                    JSONObject j = new JSONObject(new String(body, StandardCharsets.UTF_8));
                    title = j.optString("title", null);
                    author = j.optString("author_name", null);
                }
            } catch (Exception ignored) {
            } finally { if (c != null) c.disconnect(); }
            synchronized (OEMBED) { OEMBED.put(id, new String[]{title, author}); }
            boolean changed = false;
            synchronized (st) {
                if (id.equals(st.contentId) && st.title == null && title != null) {
                    st.title = title;
                    if (author != null) st.subtitle = author + " · YouTube";
                    changed = true;
                }
            }
            if (changed) publish();
        }, "oembed");
        t.setDaemon(true);
        t.start();
    }

    /** Whole stream as bytes (max 4 MB). InputStream.readAllBytes() exists only on
     *  Android 13+, and this app runs from Android 8 — so every HTTP read uses this. */
    static byte[] readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) {
            if (b.size() + n > 4 << 20) throw new IOException("response too large");
            b.write(buf, 0, n);
        }
        in.close();
        return b.toByteArray();
    }

    private void publish() { l.onStatus(status()); }

    // ------------------------------------------------------------ commands

    void refresh() {
        command(() -> {
            sendNow(RECEIVER, NS_RECV, req("GET_STATUS"));
            if (transportId != null) sendNow(transportId, NS_MEDIA, req("GET_STATUS"));
        });
    }

    private void mediaCmd(String type, JSONObject extra) {
        command(() -> {
            if (transportId == null || mediaSessionId < 0) throw new IOException(L.t("На ТВ сейчас ничего не играет."));
            JSONObject p = req(type).put("mediaSessionId", mediaSessionId);
            if (extra != null) for (java.util.Iterator<String> it = extra.keys(); it.hasNext(); ) {
                String k = it.next();
                p.put(k, extra.get(k));
            }
            sendNow(transportId, NS_MEDIA, p);
        });
    }

    void play() {
        synchronized (st) { if (st.hasMedia()) { st.elapsed = st.elapsedNow(); st.updatedAt = SystemClock.elapsedRealtime(); st.state = "PLAYING"; } }
        mediaCmd("PLAY", null);
    }

    void pause() {
        synchronized (st) { if (st.hasMedia()) { st.elapsed = st.elapsedNow(); st.updatedAt = SystemClock.elapsedRealtime(); st.state = "PAUSED"; } }
        mediaCmd("PAUSE", null);
    }

    void seekBy(int seconds) {
        double target;
        synchronized (st) {
            target = Math.max(0, st.elapsedNow() + seconds);
            if (st.duration > 0) target = Math.min(target, st.duration);
        }
        try {
            mediaCmd("SEEK", new JSONObject().put("currentTime", target).put("resumeState", "PLAYBACK_START"));
        } catch (Exception ignored) {}
    }

    void stop() {
        command(() -> {
            if (transportId != null && mediaSessionId >= 0) {
                sendNow(transportId, NS_MEDIA, req("STOP").put("mediaSessionId", mediaSessionId));
            } else if (sessionId != null) {
                sendNow(RECEIVER, NS_RECV, req("STOP").put("sessionId", sessionId));
            } else throw new IOException(L.t("На ТВ сейчас ничего не играет."));
        });
    }

    void setVolume(int level) {
        final double v = Math.max(0, Math.min(100, level)) / 100.0;
        synchronized (st) { st.volume = (int) Math.round(v * 100); }
        command(() -> sendNow(RECEIVER, NS_RECV, req("SET_VOLUME").put("volume", new JSONObject().put("level", v))));
    }

    void setMuted(boolean m) {
        synchronized (st) { st.muted = m; }
        command(() -> sendNow(RECEIVER, NS_RECV, req("SET_VOLUME").put("volume", new JSONObject().put("muted", m))));
    }

    void load(String url, String title) {
        java.util.List<Media> one = new java.util.ArrayList<>();
        one.add(new Media(url, title, contentType(url)));
        load(one);
    }

    /** Plays the items in order (a queue when there are several). */
    void load(java.util.List<Media> items) {
        if (items == null || items.isEmpty()) return;
        final String url = items.get(0).url;
        command(() -> {
            pendingUrl = url;
            pendingItems = items;
            String appId;
            synchronized (st) { appId = st.appId; }
            if (DEFAULT_RECEIVER.equals(appId) && transportId != null) {
                doLoad();
            } else {
                sendNow(RECEIVER, NS_RECV, req("LAUNCH").put("appId", DEFAULT_RECEIVER));
                // RECEIVER_STATUS with the new transportId triggers doLoad();
                io.schedule(() -> {
                    if (pendingUrl != null && url.equals(pendingUrl)) {
                        pendingUrl = null;
                        l.onError(L.t("Телевизор не запустил проигрыватель вовремя."));
                    }
                }, 20, TimeUnit.SECONDS);
            }
        });
    }

    /** Writer thread only. */
    private void doLoad() throws Exception {
        String url = pendingUrl;
        java.util.List<Media> list = pendingItems;
        if (url == null || list == null || transportId == null) return;
        pendingUrl = null;
        pendingItems = null;
        if (list.size() == 1) {
            sendNow(transportId, NS_MEDIA, req("LOAD").put("media", mediaJson(list.get(0))).put("autoplay", true).put("currentTime", 0));
            return;
        }
        JSONArray q = new JSONArray();
        for (Media m : list) q.put(new JSONObject().put("media", mediaJson(m)).put("autoplay", true).put("preloadTime", 10));
        sendNow(transportId, NS_MEDIA, req("QUEUE_LOAD").put("items", q).put("startIndex", 0).put("repeatMode", "REPEAT_OFF"));
    }

    private static JSONObject mediaJson(Media m) throws Exception {
        String t = m.title;
        if (t == null || t.isEmpty()) {
            String p = m.url.replaceAll("[?#].*$", "");
            t = p.substring(p.lastIndexOf('/') + 1);
        }
        String mime = m.mime == null ? contentType(m.url) : m.mime;
        // 0 generic, 3 music track, 4 photo: the receiver lays the screen out by type
        int type = mime.startsWith("image/") ? 4 : mime.startsWith("audio/") ? 3 : 0;
        JSONObject meta = new JSONObject().put("metadataType", type).put("title", t);
        if (m.subtitle != null) meta.put(type == 3 ? "artist" : "subtitle", m.subtitle);
        if (m.image != null) meta.put("images", new JSONArray().put(new JSONObject().put("url", m.image)));
        return new JSONObject()
                .put("contentId", m.url)
                .put("contentType", mime)
                .put("streamType", "BUFFERED")
                .put("metadata", meta);
    }

    /** Next (+1) or previous (-1) item of a queue loaded by load(list). */
    void queueJump(int by) {
        try { mediaCmd("QUEUE_UPDATE", new JSONObject().put("jump", by)); } catch (Exception ignored) {}
    }

    void seekTo(double seconds) {
        try {
            mediaCmd("SEEK", new JSONObject().put("currentTime", Math.max(0, seconds)).put("resumeState", "PLAYBACK_START"));
        } catch (Exception ignored) {}
    }

    static String contentType(String url) {
        String p = url.toLowerCase(Locale.ROOT).replaceAll("[?#].*$", "");
        String ext = p.substring(p.lastIndexOf('.') + 1);
        switch (ext) {
            case "mp4": case "m4v": return "video/mp4";
            case "webm": return "video/webm";
            case "mkv": return "video/x-matroska";
            case "mov": return "video/quicktime";
            case "m3u8": return "application/x-mpegURL";
            case "mpd": return "application/dash+xml";
            case "mp3": return "audio/mpeg";
            case "m4a": return "audio/mp4";
            case "aac": return "audio/aac";
            case "flac": return "audio/flac";
            case "ogg": case "oga": return "audio/ogg";
            case "wav": return "audio/wav";
            default: return "video/mp4";
        }
    }
}
