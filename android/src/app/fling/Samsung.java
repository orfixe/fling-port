// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.wifi.WifiManager;
import android.provider.Settings;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Smart View for Samsung TVs (Tizen, 2016 and later) — the Android side of desktop/fling_samsung.py.
 *
 *  Remote: the TV's WebSocket channel (wss://TV:8002, older sets ws://TV:8001). The first time
 *  the TV asks "Allow?"; then it hands out a token, kept in prefs "samsung", so it never asks
 *  again. Keys, typing and app launches go over it. Media: phone files and links go to the TV's
 *  DLNA renderer (UPnP AVTransport). Mirroring: Android has no public API to start Miracast, so
 *  Fling opens the system Cast / Smart View screen. Atv routes every remote call for a Samsung
 *  address here, so the app's normal remote UI drives the TV unchanged. */
final class Samsung {

    interface Listener { void onSamsung(String state, String message); }   // waiting | ready | denied | error

    static final String MODEL_PREFIX = "Samsung";
    private static final int WS_TLS = 8002, WS = 8001, DLNA = 9197;
    private static final String CHANNEL = "/api/v2/channels/samsung.remote.control";
    private static final String AVT = "urn:schemas-upnp-org:service:AVTransport:1";
    private static final String DLNA_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000";

    private final Context ctx;
    private final Listener l;
    private final SharedPreferences prefs;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ExecutorService bgPool = Executors.newCachedThreadPool();

    private volatile Ws ws;
    private volatile String host;
    private volatile boolean ready;

    Samsung(Context ctx, Listener l) {
        this.ctx = ctx.getApplicationContext();
        this.l = l;
        Tls.init(this.ctx);                                // TV certificate pins
        this.prefs = ctx.getSharedPreferences("samsung", Context.MODE_PRIVATE);
    }

    // ------------------------------------------------------------ the TVs Fling knows

    /** Samsung TVs seen before: [{name, host, model, mac}]. */
    List<Discovery.Device> known() {
        List<Discovery.Device> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(prefs.getString("tvs", "[]"));
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                out.add(new Discovery.Device(o.getString("name"), o.getString("host"),
                        (MODEL_PREFIX + " " + o.optString("model", "")).trim()));
            }
        } catch (Exception ignored) {}
        return out;
    }

    boolean handles(String h) {
        if (h == null) return false;
        for (Discovery.Device d : known()) if (d.host.equals(h)) return true;
        return false;
    }

    private JSONObject entry(String h) {
        try {
            JSONArray a = new JSONArray(prefs.getString("tvs", "[]"));
            for (int i = 0; i < a.length(); i++) if (a.getJSONObject(i).optString("host").equals(h)) return a.getJSONObject(i);
        } catch (Exception ignored) {}
        return null;
    }

    private synchronized void remember(JSONObject info) {
        try {
            JSONArray a = new JSONArray(prefs.getString("tvs", "[]")), out = new JSONArray();
            String mac = info.optString("mac");
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                boolean same = o.optString("host").equals(info.optString("host"))
                        || (!mac.isEmpty() && mac.equals(o.optString("mac")));
                if (!same) out.put(o);
            }
            out.put(info);
            prefs.edit().putString("tvs", out.toString()).apply();
        } catch (Exception ignored) {}
    }

    void forget(String h) {
        Tls.forget(h);
        try {
            JSONArray a = new JSONArray(prefs.getString("tvs", "[]")), out = new JSONArray();
            for (int i = 0; i < a.length(); i++) if (!a.getJSONObject(i).optString("host").equals(h)) out.put(a.getJSONObject(i));
            prefs.edit().putString("tvs", out.toString()).apply();
        } catch (Exception ignored) {}
    }

    /** http://TV:8001/api/v2/ — the TV's own description; null if it isn't a Tizen TV. Slow: off the main thread. */
    static JSONObject info(String h, int timeoutMs) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL("http://" + h + ":" + WS + "/api/v2/").openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            try (InputStream in = c.getInputStream()) {
                JSONObject d = new JSONObject(new String(readAll(in, 64 * 1024), StandardCharsets.UTF_8));
                JSONObject dev = d.optJSONObject("device");
                if (dev == null) return null;
                String name = android.text.Html.fromHtml(dev.optString("name", d.optString("name", "Samsung TV")),
                        android.text.Html.FROM_HTML_MODE_LEGACY).toString();
                return new JSONObject().put("host", h).put("name", name)
                        .put("model", dev.optString("modelName", dev.optString("model", "")))
                        .put("mac", dev.optString("wifiMac", "").toLowerCase(Locale.ROOT))
                        .put("power", dev.optString("PowerState", "on"));
            } finally { c.disconnect(); }
        } catch (Exception e) {
            return null;
        }
    }

    /** A TV added by IP: if it answers as a Samsung, remember it. Off the main thread. */
    boolean probe(String h) {
        JSONObject i = info(h, 2500);
        if (i == null) return false;
        remember(i);
        return true;
    }

    /** SSDP search for Samsung remotes and DLNA renderers, then /api/v2 on each address. Calls back off the main thread. */
    void discover(Runnable changed) {
        bgPool.execute(() -> {
            WifiManager wifi = (WifiManager) ctx.getSystemService(Context.WIFI_SERVICE);
            WifiManager.MulticastLock lock = null;
            Set<String> hosts = new LinkedHashSet<>();
            try {
                lock = wifi.createMulticastLock("fling-ssdp");
                lock.setReferenceCounted(false);
                lock.acquire();
                try (DatagramSocket s = new DatagramSocket()) {
                    s.setSoTimeout(400);
                    InetAddress group = InetAddress.getByName("239.255.255.250");
                    for (String st : new String[]{"urn:samsung.com:device:RemoteControlReceiver:1",
                            "urn:schemas-upnp-org:device:MediaRenderer:1"}) {
                        byte[] m = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                                + "MX: 2\r\nST: " + st + "\r\n\r\n").getBytes(StandardCharsets.US_ASCII);
                        for (int k = 0; k < 2; k++) s.send(new DatagramPacket(m, m.length, group, 1900));
                    }
                    long end = System.currentTimeMillis() + 3000;
                    byte[] buf = new byte[4096];
                    while (System.currentTimeMillis() < end) {
                        DatagramPacket p = new DatagramPacket(buf, buf.length);
                        try { s.receive(p); } catch (SocketTimeoutException t) { continue; }
                        String text = new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8).toLowerCase(Locale.ROOT);
                        if (text.contains("samsung") || text.contains("mediarenderer")) hosts.add(p.getAddress().getHostAddress());
                    }
                }
            } catch (Exception ignored) {
            } finally {
                if (lock != null) try { lock.release(); } catch (Exception ignored) {}
            }
            boolean any = false;
            for (String h : hosts) {
                JSONObject i = info(h, 2500);
                if (i != null) { remember(i); any = true; }
            }
            if (any && changed != null) changed.run();
        });
    }

    // ------------------------------------------------------------ the remote

    void connect(String h) {
        io.execute(() -> {
            if (h.equals(host) && ws != null && ready) return;
            closeNow();
            host = h;
            try {
                openNow(h, true);
            } catch (Exception tls) {
                try { openNow(h, false); }                   // a 2016 set without the TLS port
                catch (Exception e) { l.onSamsung("error", L.t("ТВ не отвечает — он выключен или в другой сети.")); }
            }
        });
    }

    void disconnect() { io.execute(this::closeNow); }

    private void closeNow() {
        ready = false;
        Ws w = ws;
        ws = null;
        if (w != null) w.close();
    }

    private String tokenKey(String h) {
        JSONObject e = entry(h);
        String mac = e == null ? "" : e.optString("mac");
        return "token_" + (mac.isEmpty() ? h : mac);
    }

    private void openNow(String h, boolean tls) throws Exception {
        String name = Base64.encodeToString("Fling (Android)".getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
        String q = "name=" + java.net.URLEncoder.encode(name, "UTF-8");
        String tok = prefs.getString(tokenKey(h), null);
        if (tok != null) q += "&token=" + java.net.URLEncoder.encode(tok, "UTF-8");
        Socket sock;
        if (tls) {
            sock = Tls.connect(h, WS_TLS, false, 4000);          // the TV's certificate is self-signed
        } else {
            sock = new Socket();
            sock.connect(new InetSocketAddress(h, WS), 4000);
        }
        Ws w = new Ws(sock, h + ":" + (tls ? WS_TLS : WS), CHANNEL + "?" + q, this::onMessage, () -> {
            if (ready) { ready = false; l.onSamsung("error", ""); }
        });
        ws = w;
        w.start();
        // no answer in 3 s: the TV is showing "Allow?" on its screen
        bgPool.execute(() -> {
            try { Thread.sleep(3000); } catch (InterruptedException ignored) {}
            if (ws == w && !ready) l.onSamsung("waiting", L.t("Нажмите «Разрешить» на экране ТВ"));
        });
    }

    private void onMessage(String text) {
        try {
            JSONObject m = new JSONObject(text);
            String ev = m.optString("event");
            Object data = m.opt("data");
            if (ev.equals("ms.channel.connect")) {
                String tok = data instanceof JSONObject ? ((JSONObject) data).optString("token", "") : "";
                if (!tok.isEmpty() && host != null) prefs.edit().putString(tokenKey(host), tok).apply();
                ready = true;
                l.onSamsung("ready", "");
            } else if (ev.equals("ms.channel.unauthorized")) {
                ready = false;
                l.onSamsung("denied", L.t("ТВ не разрешил подключение. На ТВ: Общие → Диспетчер внешних устройств → Диспетчер подключения устройств — разрешите Fling."));
            } else if (ev.equals("ms.channel.timeOut")) {
                l.onSamsung("error", L.t("ТВ не ответил на запрос разрешения."));
            }
        } catch (Exception ignored) {}
    }

    private void send(String h, JSONObject msg) {
        io.execute(() -> {
            if (!h.equals(host) || ws == null) {
                connect(h);
                return;
            }
            if (!ready) return;
            try { ws.send(msg.toString()); }
            catch (Exception e) { closeNow(); l.onSamsung("error", L.t("Связь с ТВ прервалась.")); }
        });
    }

    /** Android key code (Atv constants) → Samsung KEY_*; null when the TV has no such key. */
    static String keyName(int code) {
        switch (code) {
            case Atv.UP: return "KEY_UP";
            case Atv.DOWN: return "KEY_DOWN";
            case Atv.LEFT: return "KEY_LEFT";
            case Atv.RIGHT: return "KEY_RIGHT";
            case Atv.OK: case Atv.ENTER: return "KEY_ENTER";
            case Atv.BACK: return "KEY_RETURN";
            case Atv.HOME: return "KEY_HOME";
            case Atv.POWER: return "KEY_POWER";
            case Atv.VOL_UP: return "KEY_VOLUP";
            case Atv.VOL_DOWN: return "KEY_VOLDOWN";
            case Atv.MUTE: return "KEY_MUTE";
            case Atv.PLAY_PAUSE: return "KEY_PLAY";
            case Atv.STOP: return "KEY_STOP";
            case Atv.REWIND: return "KEY_REWIND";
            case Atv.FFWD: return "KEY_FF";
            case Atv.NEXT: return "KEY_FF";
            case Atv.PREV: return "KEY_REWIND";
            case Atv.CH_UP: return "KEY_CHUP";
            case Atv.CH_DOWN: return "KEY_CHDOWN";
            case Atv.INPUT: return "KEY_SOURCE";
            case Atv.INFO: return "KEY_INFO";
            case Atv.GUIDE: return "KEY_GUIDE";
            case Atv.CAPTIONS: return "KEY_CAPTION";
            case Atv.RED: return "KEY_RED";
            case Atv.GREEN: return "KEY_GREEN";
            case Atv.YELLOW: return "KEY_YELLOW";
            case Atv.BLUE: return "KEY_CYAN";
            case Atv.SEARCH: return "KEY_SEARCH";
            default:
                if (code >= 7 && code <= 16) return "KEY_" + (code - 7);   // digits
                return null;
        }
    }

    void key(String h, int code) {
        String k = keyName(code);
        if (k == null) return;
        try {
            send(h, new JSONObject().put("method", "ms.remote.control").put("params", new JSONObject()
                    .put("Cmd", "Click").put("DataOfCmd", k).put("Option", "false").put("TypeOfRemote", "SendRemoteKey")));
        } catch (Exception ignored) {}
    }

    /** The whole text of the TV's field, as typed (the keyboard stays open). */
    void textLive(String h, String s) {
        if (s == null) return;
        try {
            send(h, new JSONObject().put("method", "ms.remote.control").put("params", new JSONObject()
                    .put("Cmd", Base64.encodeToString(s.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP))
                    .put("DataOfCmd", "base64").put("TypeOfRemote", "SendInputString")));
        } catch (Exception ignored) {}
    }

    void text(String h, String s) {
        if (s == null || s.isEmpty()) return;
        try {
            send(h, new JSONObject().put("method", "ms.remote.control").put("params", new JSONObject()
                    .put("Cmd", Base64.encodeToString(s.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP))
                    .put("DataOfCmd", "base64").put("TypeOfRemote", "SendInputString")));
            send(h, new JSONObject().put("method", "ms.remote.control").put("params", new JSONObject()
                    .put("TypeOfRemote", "SendInputEnd")));
        } catch (Exception ignored) {}
    }

    void launchApp(String h, String appId, boolean nativeLaunch, String meta) {
        try {
            send(h, new JSONObject().put("method", "ms.channel.emit").put("params", new JSONObject()
                    .put("event", "ed.apps.launch").put("to", "host").put("data", new JSONObject()
                            .put("appId", appId).put("action_type", nativeLaunch ? "NATIVE_LAUNCH" : "DEEP_LINK")
                            .put("metaTag", meta == null ? "" : meta))));
        } catch (Exception ignored) {}
    }

    /** What Atv.launch gets (an Android app link or package): the matching Tizen app, or the TV's browser for a web page. */
    void launch(String h, String link) {
        if (link == null) return;
        String s = link.toLowerCase(Locale.ROOT);
        Matcher yt = Pattern.compile("(?:v=|youtu\\.be/|shorts/|embed/)([A-Za-z0-9_-]{6,})").matcher(link);
        if (s.contains("youtube") || s.contains("youtu.be")) {
            launchApp(h, "111299001912", false, yt.find() ? "v=" + yt.group(1) : "");
        } else if (s.contains("netflix")) launchApp(h, "3201907018807", false, "");
        else if (s.contains("primevideo") || s.contains("amazon")) launchApp(h, "3201910019365", false, "");
        else if (s.contains("disney")) launchApp(h, "3201901017640", false, "");
        else if (s.contains("spotify")) launchApp(h, "3201606009684", false, "");
        else if (s.contains("plex")) launchApp(h, "3201512006963", false, "");
        else if (s.startsWith("http://") || s.startsWith("https://")) launchApp(h, "org.tizen.browser", true, link);
        else l.onSamsung("info", L.t("Этого приложения нет на Samsung или Fling не знает, как его открыть."));
    }

    // ------------------------------------------------------------ turning the TV on

    /** Wake-on-LAN to the TV's Wi-Fi MAC (works when "Power On with Mobile" is on in the TV's settings). */
    void wake(String h) {
        JSONObject e = entry(h);
        String mac = e == null ? "" : e.optString("mac").replaceAll("[^0-9a-fA-F]", "");
        if (mac.length() != 12) {
            l.onSamsung("info", L.t("Не знаю MAC-адрес ТВ — включите его один раз пультом, чтобы Fling его запомнил."));
            return;
        }
        bgPool.execute(() -> {
            byte[] p = new byte[6 + 16 * 6];
            for (int i = 0; i < 6; i++) p[i] = (byte) 0xff;
            for (int r = 0; r < 16; r++)
                for (int i = 0; i < 6; i++) p[6 + r * 6 + i] = (byte) Integer.parseInt(mac.substring(i * 2, i * 2 + 2), 16);
            try (DatagramSocket s = new DatagramSocket()) {
                s.setBroadcast(true);
                String[] parts = h.split("\\.");
                List<String> targets = new ArrayList<>();
                targets.add("255.255.255.255");
                if (parts.length == 4) targets.add(parts[0] + "." + parts[1] + "." + parts[2] + ".255");
                for (int k = 0; k < 3; k++)
                    for (String t : targets) for (int port : new int[]{9, 7})
                        s.send(new DatagramPacket(p, p.length, InetAddress.getByName(t), port));
            } catch (Exception ignored) {}
        });
    }

    // ------------------------------------------------------------ mirroring

    /** Opens the system screen that starts Smart View / Cast: Samsung's Smart View, then Cast settings, then wireless display. */
    static boolean openMirroring(Activity a) {
        Intent[] tries = {
                new Intent().setComponent(new ComponentName("com.samsung.android.smartmirroring",
                        "com.samsung.android.smartmirroring.CaptureActivity")),
                new Intent(Settings.ACTION_CAST_SETTINGS),
                new Intent("android.settings.WIFI_DISPLAY_SETTINGS"),
        };
        for (Intent i : tries) {
            try {
                if (i.resolveActivity(a.getPackageManager()) == null) continue;
                a.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                return true;
            } catch (Exception ignored) {}
        }
        return false;
    }

    // ------------------------------------------------------------ DLNA (files and links)

    private static String avTransport(String h) {
        for (String loc : new String[]{"http://" + h + ":" + DLNA + "/dmr"}) {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(loc).openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(3000);
                String xml;
                try (InputStream in = c.getInputStream()) { xml = new String(readAll(in, 256 * 1024), StandardCharsets.UTF_8); }
                finally { c.disconnect(); }
                Matcher m = Pattern.compile("<service>(.*?)</service>", Pattern.DOTALL).matcher(xml);
                while (m.find()) {
                    String svc = m.group(1);
                    if (!svc.contains("AVTransport")) continue;
                    Matcher u = Pattern.compile("<controlURL>\\s*(.*?)\\s*</controlURL>").matcher(svc);
                    if (u.find()) return new URL(new URL(loc), u.group(1)).toString();
                }
            } catch (Exception ignored) {}
        }
        return null;
    }

    private static void soap(String control, String action, String args) throws IOException {
        String body = "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
                + "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
                + "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body>"
                + "<u:" + action + " xmlns:u=\"" + AVT + "\"><InstanceID>0</InstanceID>" + args + "</u:" + action + ">"
                + "</s:Body></s:Envelope>";
        HttpURLConnection c = (HttpURLConnection) new URL(control).openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(8000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "text/xml; charset=\"utf-8\"");
        c.setRequestProperty("SOAPAction", "\"" + AVT + "#" + action + "\"");
        try (OutputStream o = c.getOutputStream()) { o.write(body.getBytes(StandardCharsets.UTF_8)); }
        int code = c.getResponseCode();
        c.disconnect();
        if (code >= 300) throw new IOException("DLNA " + action + ": HTTP " + code);
    }

    static String esc(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    private static String didl(String url, String title, String mime) {
        String cls = mime.startsWith("image/") ? "object.item.imageItem.photo"
                : mime.startsWith("audio/") ? "object.item.audioItem.musicTrack" : "object.item.videoItem";
        return "<DIDL-Lite xmlns=\"urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/\" "
                + "xmlns:dc=\"http://purl.org/dc/elements/1.1/\" xmlns:upnp=\"urn:schemas-upnp-org:metadata-1-0/upnp/\">"
                + "<item id=\"0\" parentID=\"-1\" restricted=\"1\"><dc:title>" + esc(title) + "</dc:title>"
                + "<upnp:class>" + cls + "</upnp:class>"
                + "<res protocolInfo=\"http-get:*:" + mime + ":" + DLNA_FEATURES + "\">" + esc(url) + "</res>"
                + "</item></DIDL-Lite>";
    }

    /** Plays a URL on the TV over DLNA. Slow: off the main thread. */
    void play(String h, String url, String title, String mime) throws IOException {
        String control = avTransport(h);
        if (control == null) throw new IOException(L.t("ТВ не поддерживает DLNA или он выключен."));
        try { soap(control, "Stop", ""); } catch (IOException ignored) {}
        soap(control, "SetAVTransportURI", "<CurrentURI>" + esc(url) + "</CurrentURI>"
                + "<CurrentURIMetaData>" + esc(didl(url, title == null ? "Fling" : title, mime)) + "</CurrentURIMetaData>");
        soap(control, "Play", "<Speed>1</Speed>");
    }

    void transport(String h, String action) {
        bgPool.execute(() -> {
            String control = avTransport(h);
            try {
                if (control != null) soap(control, action, action.equals("Play") ? "<Speed>1</Speed>" : "");
            } catch (IOException ignored) {}
        });
    }

    static String mimeOfUrl(String url) {
        String p = url.toLowerCase(Locale.ROOT);
        int q = p.indexOf('?');
        if (q > 0) p = p.substring(0, q);
        if (p.endsWith(".mp3")) return "audio/mpeg";
        if (p.endsWith(".m4a") || p.endsWith(".aac")) return "audio/mp4";
        if (p.endsWith(".flac")) return "audio/flac";
        if (p.endsWith(".ogg") || p.endsWith(".oga")) return "audio/ogg";
        if (p.endsWith(".wav")) return "audio/wav";
        if (p.endsWith(".webm")) return "video/webm";
        if (p.endsWith(".mkv")) return "video/x-matroska";
        if (p.endsWith(".mov")) return "video/quicktime";
        if (p.endsWith(".ts")) return "video/mp2t";
        if (p.endsWith(".m3u8")) return "application/vnd.apple.mpegurl";
        if (p.endsWith(".jpg") || p.endsWith(".jpeg")) return "image/jpeg";
        if (p.endsWith(".png")) return "image/png";
        return "video/mp4";
    }

    void runBg(Runnable r) { bgPool.execute(r); }

    // ------------------------------------------------------------ helpers

    static byte[] readAll(InputStream in, int limit) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        for (int n; (n = in.read(buf)) > 0; ) {
            b.write(buf, 0, n);
            if (b.size() > limit) throw new IOException("too large");
        }
        return b.toByteArray();
    }

    /** A minimal RFC 6455 client: text frames out (masked), text/ping/close in. */
    static final class Ws {
        interface OnText { void on(String s); }

        private static final int MAX_FRAME = 1 << 20;
        private final Socket sock;
        private final String hostPort, path;
        private final OnText onText;
        private final Runnable onClose;
        private OutputStream out;
        private InputStream in;
        private volatile boolean closed;
        private final SecureRandom rnd = new SecureRandom();

        Ws(Socket sock, String hostPort, String path, OnText onText, Runnable onClose) {
            this.sock = sock; this.hostPort = hostPort; this.path = path; this.onText = onText; this.onClose = onClose;
        }

        void start() throws IOException {
            sock.setSoTimeout(6000);
            out = sock.getOutputStream();
            in = sock.getInputStream();
            byte[] k = new byte[16];
            rnd.nextBytes(k);
            String req = "GET " + path + " HTTP/1.1\r\nHost: " + hostPort + "\r\nUpgrade: websocket\r\n"
                    + "Connection: Upgrade\r\nSec-WebSocket-Key: " + Base64.encodeToString(k, Base64.NO_WRAP)
                    + "\r\nSec-WebSocket-Version: 13\r\n\r\n";
            out.write(req.getBytes(StandardCharsets.US_ASCII));
            out.flush();
            String status = line();
            if (status == null || !status.contains(" 101")) throw new IOException("WebSocket: " + status);
            for (String h; (h = line()) != null && !h.isEmpty(); ) { /* headers */ }
            sock.setSoTimeout(0);
            Thread t = new Thread(this::readLoop, "fling-samsung-ws");
            t.setDaemon(true);
            t.start();
        }

        private String line() throws IOException {
            StringBuilder b = new StringBuilder();
            for (int c; (c = in.read()) != -1; ) {
                if (c == '\n') return b.toString().trim();
                if (b.length() > 8192) throw new IOException("header too long");
                b.append((char) c);
            }
            return b.length() == 0 ? null : b.toString();
        }

        private void readFully(byte[] b) throws IOException {
            int off = 0;
            while (off < b.length) {
                int n = in.read(b, off, b.length - off);
                if (n < 0) throw new IOException("closed");
                off += n;
            }
        }

        private void readLoop() {
            try {
                ByteArrayOutputStream msg = new ByteArrayOutputStream();
                while (!closed) {
                    int b0 = in.read(), b1 = in.read();
                    if (b0 < 0 || b1 < 0) break;
                    int op = b0 & 0x0f;
                    long len = b1 & 0x7f;
                    if (len == 126) { byte[] e = new byte[2]; readFully(e); len = ((e[0] & 0xff) << 8) | (e[1] & 0xff); }
                    else if (len == 127) { byte[] e = new byte[8]; readFully(e); len = 0; for (byte x : e) len = (len << 8) | (x & 0xff); }
                    if (len > MAX_FRAME) break;
                    byte[] mask = null;
                    if ((b1 & 0x80) != 0) { mask = new byte[4]; readFully(mask); }
                    byte[] p = new byte[(int) len];
                    readFully(p);
                    if (mask != null) for (int i = 0; i < p.length; i++) p[i] ^= mask[i & 3];
                    if (op == 8) break;
                    if (op == 9) { frame(10, p); continue; }
                    if (op == 1 || op == 0) {
                        msg.write(p);
                        if (msg.size() > MAX_FRAME) break;
                        if ((b0 & 0x80) != 0) {
                            String s = new String(msg.toByteArray(), StandardCharsets.UTF_8);
                            msg.reset();
                            onText.on(s);
                        }
                    }
                }
            } catch (IOException ignored) {
            } finally {
                boolean was = closed;
                close();
                if (!was) onClose.run();
            }
        }

        synchronized void send(String text) throws IOException { frame(1, text.getBytes(StandardCharsets.UTF_8)); }

        private synchronized void frame(int op, byte[] p) throws IOException {
            if (closed) throw new IOException("closed");
            ByteArrayOutputStream f = new ByteArrayOutputStream();
            f.write(0x80 | op);
            if (p.length < 126) f.write(0x80 | p.length);
            else if (p.length < 65536) { f.write(0x80 | 126); f.write(p.length >> 8); f.write(p.length & 0xff); }
            else { f.write(0x80 | 127); for (int i = 7; i >= 0; i--) f.write((int) (((long) p.length >> (8 * i)) & 0xff)); }
            byte[] m = new byte[4];
            rnd.nextBytes(m);
            f.write(m);
            for (int i = 0; i < p.length; i++) f.write(p[i] ^ m[i & 3]);
            out.write(f.toByteArray());
            out.flush();
        }

        void close() {
            closed = true;
            try { sock.close(); } catch (IOException ignored) {}
        }
    }
}
