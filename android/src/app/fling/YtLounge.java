// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** YouTube on the TV as a remote the YouTube app itself knows: the Lounge API ("Link with TV
 *  code" in the TV app's settings). Exact seek, speed, next / previous — what keys can't do.
 *  The same protocol pyytlounge speaks for Fling on the PC. Blocking: call off the UI thread. */
final class YtLounge {
    private static final String API = "https://www.youtube.com/api/lounge";
    private static final Pattern SID = Pattern.compile("\\[\"c\",\"([^\"]+)\""),
            GSESSION = Pattern.compile("\\[\"S\",\"([^\"]+)\""),
            EVENT_ID = Pattern.compile("\\[(\\d+),\\[\"");

    private final SharedPreferences prefs;
    private String sid, gsession;
    private long aid = 0;
    private int ofs = 0;

    YtLounge(Context c) { prefs = c.getSharedPreferences("yt-lounge", Context.MODE_PRIVATE); }

    boolean paired() { return prefs.getString("screen", null) != null; }
    String screenName() { return prefs.getString("name", null); }

    private String deviceId() {
        String id = prefs.getString("device", null);
        if (id == null) { id = UUID.randomUUID().toString(); prefs.edit().putString("device", id).apply(); }
        return id;
    }

    /** The code the TV shows in YouTube → Settings → Link with TV code (spaces allowed). */
    synchronized void pair(String code) throws IOException {
        String digits = code.replaceAll("\\D", "");
        if (digits.length() < 8) throw new IOException(L.t("Введите код с экрана YouTube на ТВ."));
        String body;
        try { body = post(API + "/pairing/get_screen", form("pairing_code", digits)); }
        catch (HttpError e) {                                       // 404: no screen has this code (wrong or expired)
            throw new IOException(e.code == 404 ? L.t("Код не подошёл — проверьте его на экране ТВ.") : L.t("YouTube не ответил, попробуйте ещё раз."));
        }
        String screen = field(body, "screenId"), token = field(body, "loungeToken");
        if (screen == null || token == null) throw new IOException(L.t("Код не подошёл — проверьте его на экране ТВ."));
        prefs.edit().putString("screen", screen).putString("token", token).putString("name", field(body, "name")).apply();
        sid = null;
    }

    void forget() { prefs.edit().remove("screen").remove("token").remove("name").apply(); sid = null; }

    private void refreshToken() throws IOException {
        String body = post(API + "/pairing/get_lounge_token_batch", form("screen_ids", prefs.getString("screen", "")));
        String token = field(body, "loungeToken");
        if (token == null) throw new IOException("lounge token");
        prefs.edit().putString("token", token).apply();
    }

    private void connect() throws IOException {
        Map<String, String> f = new LinkedHashMap<>();
        f.put("app", "web");
        f.put("mdx-version", "3");
        f.put("name", "Fling");
        f.put("id", deviceId());
        f.put("device", "REMOTE_CONTROL");
        f.put("capabilities", "que,dsdtr,atp,vsp");
        f.put("magnaKey", "cloudPairedDevice");
        f.put("ui", "false");
        f.put("theme", "cl");
        f.put("loungeIdToken", prefs.getString("token", ""));
        String body = post(API + "/bc/bind?RID=1&VER=8&CVER=1&auth_failure_option=send_error", form(f));
        Matcher m = SID.matcher(body), g = GSESSION.matcher(body);
        if (!m.find() || !g.find()) throw new IOException("lounge: no session");
        sid = m.group(1);
        gsession = g.group(1);
        Matcher e = EVENT_ID.matcher(body);
        while (e.find()) aid = Math.max(aid, Long.parseLong(e.group(1)));
        ofs = 1;
    }

    /** One command; reconnects (new session, fresh token) once when YouTube dropped the old one. */
    synchronized void command(String cmd, Map<String, String> params) throws IOException {
        if (!paired()) throw new IOException(L.t("YouTube на ТВ не подключён"));
        for (int attempt = 0; ; attempt++) {
            try {
                if (sid == null) connect();
                Map<String, String> f = new LinkedHashMap<>();
                f.put("count", "1");
                f.put("ofs", String.valueOf(ofs));
                f.put("req0__sc", cmd);
                if (params != null) for (Map.Entry<String, String> p : params.entrySet()) f.put("req0_" + p.getKey(), p.getValue());
                ofs++;
                Map<String, String> q = new LinkedHashMap<>();
                q.put("name", "Fling");
                q.put("loungeIdToken", prefs.getString("token", ""));
                q.put("SID", sid);
                q.put("AID", String.valueOf(aid));
                q.put("gsessionid", gsession);
                q.put("device", "REMOTE_CONTROL");
                q.put("app", "youtube-desktop");
                q.put("VER", "8");
                q.put("v", "2");
                q.put("RID", String.valueOf(ofs));
                post(API + "/bc/bind?" + form(q), form(f));
                return;
            } catch (HttpError e) {
                if (attempt >= 1) throw e;
                if (e.code == 401) refreshToken();                   // expired token
                sid = null;                                          // 400 Unknown SID / 410 Gone: a new session
            }
        }
    }

    void seekTo(double seconds) throws IOException { command("seekTo", one("newTime", String.valueOf((int) Math.max(0, seconds)))); }
    void speed(double rate) throws IOException { command("setPlaybackSpeed", one("playbackSpeed", String.valueOf(rate))); }
    void next() throws IOException { command("next", null); }
    void previous() throws IOException { command("previous", null); }
    void play() throws IOException { command("play", null); }
    void pause() throws IOException { command("pause", null); }

    private static Map<String, String> one(String k, String v) { Map<String, String> m = new LinkedHashMap<>(); m.put(k, v); return m; }

    // ------------------------------------------------------------ HTTP

    private static final class HttpError extends IOException {
        final int code;
        HttpError(int code, String body) { super("lounge HTTP " + code + ": " + body); this.code = code; }
    }

    private static String post(String url, String body) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setConnectTimeout(8000);
            c.setReadTimeout(12000);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
            c.setRequestProperty("Origin", "https://www.youtube.com");
            try (OutputStream o = c.getOutputStream()) { o.write(body.getBytes(StandardCharsets.UTF_8)); }
            int code = c.getResponseCode();
            InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
            String text = in == null ? "" : read(in);
            if (code >= 400) throw new HttpError(code, text.length() > 200 ? text.substring(0, 200) : text);
            return text;
        } finally {
            c.disconnect();
        }
    }

    private static String read(InputStream in) throws IOException {
        try (InputStream i = in) {
            ByteArrayOutputStream o = new ByteArrayOutputStream();
            byte[] b = new byte[8192];
            for (int n; (n = i.read(b)) > 0; ) o.write(b, 0, n);
            return o.toString("UTF-8");
        }
    }

    private static String form(String k, String v) { return enc(k) + "=" + enc(v); }

    private static String form(Map<String, String> m) {
        StringBuilder s = new StringBuilder();
        for (Map.Entry<String, String> e : m.entrySet()) {
            if (s.length() > 0) s.append('&');
            s.append(enc(e.getKey())).append('=').append(enc(e.getValue() == null ? "" : e.getValue()));
        }
        return s.toString();
    }

    private static String enc(String s) {
        try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return s; }
    }

    /** "key":"value" from a JSON reply (the replies are small and flat enough). */
    private static String field(String json, String key) {
        Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"").matcher(json);
        return m.find() ? m.group(1) : null;
    }
}
