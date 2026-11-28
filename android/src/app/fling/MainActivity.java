// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputFilter;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** The Fling panel, as a phone app: casting, link sharing and the TV remote. */
public class MainActivity extends Activity implements Atv.Listener, Cast.Listener, Discovery.Listener, Samsung.Listener {

    static final String[][] TV_APPS = {
            {"YouTube", "vnd.youtube.launch://", "com.google.android.youtube.tv"},
            {"Netflix", "netflix://", "com.netflix.ninja"},
            {"Prime Video", "https://app.primevideo.com", "com.amazon.amazonvideo.livingroom"},
            {"Disney+", "https://www.disneyplus.com", "com.disney.disneyplus"},
            {"Spotify", "spotify://", "com.spotify.tv.android"},
            {"Plex", "plex://", "com.plexapp.android"},
            {"Tubi", "https://tubitv.com/", "com.tubitv"},
            {"Kinopoisk", "https://hd.kinopoisk.ru", "ru.kinopoisk"},
    };
    /** Names of TV apps a package name doesn't spell well (shared with Fling for PC). */
    static final String[][] KNOWN_APPS = {
            {"com.google.android.youtube.tvmusic", "YouTube Music"},
            {"com.google.android.youtube.tvkids", "YouTube Kids"},
            {"org.xbmc.kodi", "Kodi"},
            {"org.videolan.vlc", "VLC"},
            {"tv.twitch.android.app", "Twitch"},
            {"org.jellyfin.androidtv", "Jellyfin"},
            {"tv.pluto.android", "Pluto TV"},
            {"com.apple.atve.androidtv.appletv", "Apple TV"},
            {"com.wbd.stream", "Max"},
            {"com.hulu.livingroomplus", "Hulu"},
            {"com.cbs.ott", "Paramount+"},
            {"com.teamsmart.videomanager.tv", "SmartTube"},
            {"com.liskovsoft.smarttubetv.beta", "SmartTube"},
            {"ru.ivi.client", "ivi"},
            {"ru.more.play", "Okko"},
            {"com.android.tv.settings", "Настройки"},
            {"com.google.android.videos", "Google TV"},
            {"com.google.android.play.games", "Play Игры"},
            {"com.android.vending", "Google Play"},
            {"gpm.tnt_premier", "Premier"},
            {"ru.ivi.client", "ivi"},
            {"ru.more.play", "Okko"},
            {"ru.rt.video.app.tv", "Wink"},
            {"com.zaneschepke.wireguardautotunnel", "WG Tunnel"},
    };
    /** Package-name words that say nothing about the app ("com.foo.tv.android" → "Foo"). */
    private static final java.util.Set<String> GENERIC = new java.util.HashSet<>(java.util.Arrays.asList(
            "android", "androidtv", "app", "apps", "atv", "beta", "client", "com", "free", "launcher", "leanback",
            "livingroom", "mobile", "net", "ninja", "org", "ott", "player", "ru", "stream", "tv"));
    /** The TV's own home screens: not apps to pin. */
    private static final java.util.Set<String> HIDDEN = new java.util.HashSet<>(java.util.Arrays.asList(
            "com.google.android.apps.tv.launcher", "com.google.android.apps.tv.launcherx",
            "com.google.android.leanbacklauncher", "com.google.android.tvlauncher"));
    private static final int MAX_APPS = 12;
    /** Names of the tiles on screen (renamed ones too), for "Now: …". */
    private static final Map<String, String> tileNames = new java.util.concurrent.ConcurrentHashMap<>();
    static final int SEEK = 30;
    private static final int REQ_MIC = 7, REQ_FILES = 8, REQ_NOTIFY = 9;

    private Ui ui;
    /** Set in onDestroy. Cast/Atv/Discovery callbacks keep arriving after that; without
     *  this they reached sync() → net.execute() on a shut-down pool and crashed the app. */
    private volatile boolean destroyed;
    /** Main-thread handler that drops every post once the activity is destroyed. */
    private final Handler main = new Handler(Looper.getMainLooper()) {
        @Override public boolean sendMessageAtTime(android.os.Message msg, long uptimeMillis) {
            return !destroyed && super.sendMessageAtTime(msg, uptimeMillis);
        }
    };
    private final ExecutorService net = Executors.newFixedThreadPool(3);

    /** Run on the network pool; a no-op after onDestroy. */
    private void bg(Runnable r) {
        if (destroyed) return;
        try { net.execute(r); } catch (java.util.concurrent.RejectedExecutionException ignored) {}
    }
    private SharedPreferences prefs;
    private Updater updater;
    private Cast cast;
    private Atv atv;
    private Discovery discovery;

    // state
    private String host, devName;
    private List<Discovery.Device> devices = new ArrayList<>();
    private List<Discovery.Device> lastFound = new ArrayList<>();   // the latest mDNS list (Samsung TVs are merged in)
    private Samsung sam;
    private String samState = "";                                  // waiting | ready | denied | error
    private LinearLayout samBox;
    private Cast.Status st;
    private Cast.Status adbSt;          // real now-playing read from the TV over ADB
    private Adb adb;
    private volatile boolean adbBusy;

    /** What the screen shows: the TV's real media session when ADB has one, else Cast's. */
    private Cast.Status view() {
        return adbSt != null && adbSt.hasMedia() ? adbSt : st;
    }
    private String pairState = "idle", pairMsg = "";
    private Boolean tvOn;
    private String tvApp;
    private boolean voice;
    private String link, linkTitle;
    private boolean pendingAutoCast;
    private boolean resumed;
    private int padMode;   // 0 buttons, 1 touchpad
    private String look;   // theme + accent + language this screen was built with (Settings may change them)
    private ArrayList<Uri> pendingLocal;   // phone files waiting for a TV to be found
    private String pendingLocalType;

    // views
    private LinearLayout root;
    private ScrollView scroll;
    private View helpView, gearView;
    private View prevKey, nextKey, seekBack, seekFwd, gap1, gap2, hwHint;
    private Ui.Row sleepRow;
    private TextView notice, subtitleHeader;            // notice: the message line at the bottom
    private LinearLayout nowCard, linkCard, tvCard;
    private Ui.Artwork nowArt, linkArt;
    private Ui.RefreshButton nowRefresh, linkRefresh;
    private View linkArtFrame;
    private TextView nowTitle, nowSub, nowEl, nowRem, nowHint;
    private Ui.Slider nowProg;                    // the progress bar: drag it to seek
    private View ccKey;
    private TextView xPhone, xLink, xSpeed, xAudio, xRestart;     // the extra row under the keys
    /** The TV's own volume over ADB ("volume is 8 in range [0..15]"), -1 while unknown. */
    private volatile int tvVol = -1, tvVolMax = -1;
    private boolean adbOk() { return "ok".equals(Adb.lastState); }
    private YtLounge yt;
    /** YouTube on the TV is what plays (not Fling's own cast of something else). */
    private boolean ytOnTv(Cast.Status v) {
        return v != null && ((v.appId != null && v.appId.contains("youtube")) || (tvApp != null && tvApp.contains("youtube")));
    }
    /** Settings → YouTube on the TV: "exact" (Lounge, linked with the TV's code) or keys. */
    private boolean ytExact() { return "lounge".equals(prefs.getString("yt_seek", "keys")) && yt.paired(); }
    private void ytDo(YtCmd c) {
        bg(() -> {
            try { c.run(); } catch (Exception e) { main.post(() -> showError(L.t("YouTube на ТВ не ответил: ") + e.getMessage())); }
        });
        adbNext = 0; main.postDelayed(this::pollAdb, 1500);
    }
    private interface YtCmd { void run() throws Exception; }
    /** Media on the TV that Fling controls with keys (an app started on the TV, not Fling's cast). */
    private boolean tvMedia() { return adbSt != null && adbSt.hasMedia() && (paired() || adbOk()); }
    private FrameLayout playKey;
    private ImageView playIcon;
    private Ui.Slider castVol;
    private TextView castVolNum;
    private TextView linkTitleView, linkHost, linkKind, linkEmpty;
    private Ui.Row linkCastRow;
    private LinearLayout recentBox;
    private LinearLayout setupBox, pinBox, ctlBox, startingBox;
    private EditText pinEdit, typeEdit;
    private TextView pinOk, pinFail, tvNow;
    private final List<TextView> chips = new ArrayList<>();
    private View dpadBox, padBox;
    private FrameLayout micKey;
    private ImageView micIcon;
    private TextView listening;
    private View devDot;
    private TextView devNameView;
    private TextView liveKey, pathChip;

    // path to the TV (header chip): measured with a TCP connect to the remote port
    private volatile Boolean reachable;     // null until the first probe
    private volatile boolean viaTailnet;
    private volatile long pathMs = -1;
    private long probeDelay = PROBE_OK;
    private static final long PROBE_OK = 10000, PROBE_MIN = 2000, PROBE_MAX = 30000;
    private android.net.ConnectivityManager.NetworkCallback netCb;

    private AudioRecord recorder;
    private volatile boolean recording;
    /** Artwork cache bounded by bytes (1/8 of the heap): a plain map grew with every cover ever shown. */
    private final android.util.LruCache<String, Bitmap> images =
            new android.util.LruCache<String, Bitmap>((int) Math.min(Integer.MAX_VALUE, Runtime.getRuntime().maxMemory() / 8)) {
                @Override protected int sizeOf(String k, Bitmap b) { return b.getAllocationByteCount(); }
            };
    private final Map<String, String> ytTitles = new HashMap<>();
    private int volPending = -1;

    // ================================================================ lifecycle

    @Override protected void attachBaseContext(Context base) { super.attachBaseContext(base); Ui.applyNight(this, base); }

    private String lookNow() { return Ui.theme(this) + "|" + Ui.amoled(this) + "|" + Ui.accentId(this) + "|" + L.lang(this); }

    @Override protected void onCreate(Bundle b) {
        L.init(this);
        Ui.applyAccent(this);
        super.onCreate(b);
        ui = new Ui(this);
        prefs = getSharedPreferences("fling", MODE_PRIVATE);
        look = lookNow();
        host = prefs.getString("host", null);
        devName = prefs.getString("name", null);
        padMode = prefs.getInt("padMode", 0);
        cast = new Cast(this, this);
        adb = new Adb(this);
        yt = new YtLounge(this);
        atv = new Atv(this, this);
        sam = new Samsung(this, this);
        atv.sam = sam;                                   // Samsung addresses: the remote goes over Smart View
        discovery = new Discovery(this, this);
        updater = new Updater(this, ui, prefs);
        buildUi();
        handleIntent(getIntent());
        sync();
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        handleIntent(i);
        sync();
    }

    @Override protected void onResume() {
        super.onResume();
        if (!look.equals(lookNow())) { recreate(); return; }   // back from Settings with a new theme or language
        resumed = true;
        TvRemoteService.foregroundAtv = atv;                          // the notification's keys go through this session
        maybeSetup();
        if (prefs.getBoolean("remote_notify", true)) TvRemoteService.show(this, host, devName);
        if (prefs.getBoolean("keepOn", false)) getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (!prefs.getBoolean("welcome_done", false) && !prefs.contains("host")) {   // first launch: the language first
            welcomeShowing = true;
            Welcome.show(this, ui, () -> {
                welcomeShowing = false;
                prefs.edit().putBoolean("welcome_done", true).putBoolean("help_seen", true).apply();
                if (!look.equals(lookNow())) {                            // another language: rebuild, then the tips
                    prefs.edit().putBoolean("tour_after_lang", true).apply();
                    recreate(); return;
                }
                maybeSetup();                                             // first steps (the tour stays on "?")
            });
        } else if (!prefs.getBoolean("help_seen", false)) {           // first launch: first steps, not the tour too
            prefs.edit().putBoolean("help_seen", true).apply();
        }
        if (prefs.getBoolean("welcome_done", false) && prefs.getBoolean("tour_after_lang", false)) {
            prefs.edit().remove("tour_after_lang").apply();
            maybeSetup();
        }
        discovery.start();
        sam.discover(() -> main.post(() -> onDevices(lastFound)));   // Samsung TVs (SSDP) join the same list
        connectDevice();
        updater.resumed();                                           // back from "Install unknown apps": offer the update again
        if (BuildConfig.SELF_UPDATE) updater.checkDaily();             // quiet; speaks only if newer (GitHub build)
        main.post(poll);
        main.post(tick);
        probeDelay = PROBE_MIN;
        main.post(probe);
        watchNetwork(true);
    }

    @Override protected void onPause() {
        super.onPause();
        resumed = false;
        if (TvRemoteService.foregroundAtv == atv) TvRemoteService.foregroundAtv = null;
        main.removeCallbacks(poll);
        main.removeCallbacks(tick);
        main.removeCallbacks(probe);
        watchNetwork(false);
        discovery.stop();
        if (voice) stopVoice();
    }

    @Override protected void onDestroy() {
        destroyed = true;
        main.removeCallbacksAndMessages(null);    // pending polls, ticks and result posts
        super.onDestroy();
        cast.disconnect();
        atv.disconnect();
        net.shutdownNow();
    }

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (!resumed) return;
            if (host != null && !sam.handles(host)) cast.refresh();
            pollAdb();
            main.postDelayed(this, 1500);
        }
    };

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (!resumed) return;
            Cast.Status v = view();
            if (v != null && v.hasMedia()) updateTimes();
            main.postDelayed(this, 500);
        }
    };

    // ================================================================ path to the TV (home Wi‑Fi or Tailscale)

    /** TCP connect to the remote's port (6466; 8009 for a Cast-only device): reachable or not,
     *  through the tailnet or not, and how long it took. On failure it retries with backoff
     *  (2 → 4 → … → 30 s); when the TV comes back, the remote and Cast reconnect. */
    private final Runnable probe = new Runnable() {
        @Override public void run() {
            main.removeCallbacks(this);
            if (!resumed) return;
            final String h = host;
            if (h == null) { reachable = null; main.postDelayed(this, PROBE_OK); return; }
            bg(() -> {
                boolean tn = Tailnet.routedViaTailnet(h);
                long ms = Tailnet.connectMs(h, 6466, 3000);
                if (ms < 0) ms = Tailnet.connectMs(h, 8009, 3000);
                if (ms < 0) ms = Tailnet.connectMs(h, 8001, 3000);   // a Samsung (Smart View)
                final long m = ms;
                main.post(() -> {
                    if (!h.equals(host)) { main.post(probe); return; }
                    boolean was = Boolean.TRUE.equals(reachable);
                    if (reachable == null || reachable != (m >= 0))
                        Diag.log("tv", (m >= 0 ? "answers in " + m + " ms" : "doesn't answer") + (tn ? " (tailnet)" : ""));
                    reachable = m >= 0;
                    viaTailnet = tn;
                    pathMs = m;
                    if (m >= 0) {
                        probeDelay = PROBE_OK;
                        if (!was) { connectDevice(); adbNext = 0; atv.adopt(h); }   // back: reconnect now
                    } else probeDelay = Math.min(PROBE_MAX, Math.max(PROBE_MIN, probeDelay == PROBE_OK ? PROBE_MIN : probeDelay * 2));
                    sync();
                    main.postDelayed(probe, probeDelay);
                });
            });
        }
    };

    /** Wi‑Fi ↔ mobile data, Tailscale on or off: probe again at once instead of waiting. */
    private void watchNetwork(boolean on) {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        if (cm == null) return;
        try {
            if (on && netCb == null) {
                netCb = new android.net.ConnectivityManager.NetworkCallback() {
                    @Override public void onAvailable(android.net.Network n) { kick(); }
                    @Override public void onLost(android.net.Network n) { kick(); }
                    @Override public void onCapabilitiesChanged(android.net.Network n, android.net.NetworkCapabilities c) { kick(); }
                    private void kick() { main.post(() -> { probeDelay = PROBE_MIN; main.removeCallbacks(probe); main.postDelayed(probe, 600); }); }
                };
                cm.registerDefaultNetworkCallback(netCb);
            } else if (!on && netCb != null) {
                cm.unregisterNetworkCallback(netCb);
                netCb = null;
            }
        } catch (Exception ignored) { netCb = null; }
    }

    /** The connection in words, for a tap on the TV's name and for screen readers; on screen it
     *  is only the dot: green — the TV answers, red — it doesn't, grey — checking. */
    private String pathLabel() {
        if (reachable == null) return L.t("Проверяю связь с ТВ…");
        if (!reachable) return L.t("Нет связи с ТВ");
        if (viaTailnet) return pathMs > 0 ? L.f("Связь с ТВ через Tailscale · %d мс", pathMs) : L.t("Связь с ТВ через Tailscale");
        return L.t("Связь с ТВ есть");
    }

    // ================================================================ LIVE

    /** The TV screen on the phone (LiveActivity, scrcpy over ADB). Needs ADB on the TV. */
    private void openLive() {
        if (host == null) { showError(L.t("Устройство не выбрано")); return; }
        if (!"ok".equals(Adb.lastState)) {
            showError("unauthorized".equals(Adb.lastState)
                    ? L.t("Подтвердите на ТВ «Разрешить отладку» и нажмите LIVE ещё раз.")
                    : L.t("LIVE работает через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети»."));
            adbNext = 0; pollAdb();                                    // maybe it's on by now
            return;
        }
        try {
            startActivity(new Intent().setClassName(this, "app.fling.LiveActivity")
                    .putExtra("host", host).putExtra("port", 5555));
        } catch (Exception e) { showError(e.getMessage()); }
    }

    private final Map<String, String> arts = new HashMap<>();

    /** Cast sessions from the YouTube app carry a title but no artwork: find it by title. */
    private String artFor(String title, String artist) {
        if (title == null) return null;
        final String key = title + "|" + artist;
        if (arts.containsKey(key)) return arts.get(key);
        arts.put(key, null);
        bg(() -> {
            String[] r = adb.lookup(title, artist);
            if (r != null) main.post(() -> { arts.put(key, "https://img.youtube.com/vi/" + r[0] + "/hqdefault.jpg"); sync(); });
        });
        return null;
    }

    private long adbNext;

    private void pollAdb() {
        final String h = host;
        long now = SystemClock.elapsedRealtime();
        if (h == null || adbBusy || now < adbNext) return;
        // Skip IPv6 hosts — ADB/Cast on the TV are used over IPv4 here
        if (h.indexOf(':') >= 0) return;
        adbBusy = true;
        // every 3 s while healthy; long quiet period after a refused / closed link
        adbNext = now + 3000;
        bg(() -> {
            Cast.Status r = null;
            try {
                r = adb.nowPlaying(h);
                {
                    java.util.regex.Matcher vm = java.util.regex.Pattern.compile("volume is (\\d+) in range \\[0\\.\\.(\\d+)\\]")
                            .matcher(adb.shell(h, "cmd media_session volume --stream 3 --get"));
                    if (vm.find()) { tvVol = Integer.parseInt(vm.group(1)); tvVolMax = Integer.parseInt(vm.group(2)); }
                }
            } catch (Exception e) {
                // ADB off, or waiting for "Allow debugging": do NOT keep re-prompting the TV
                if ("unauthorized".equals(Adb.lastState))
                    adbNext = SystemClock.elapsedRealtime() + 300000;   // 5 min
                else if ("closed".equals(Adb.lastState))
                    adbNext = SystemClock.elapsedRealtime() + 120000;   // 2 min
            }
            if (r != null && r.hasMedia()) {
                String app = appName(r.appId);
                r.appName = app;
                r.subtitle = r.subtitle != null ? (app != null ? r.subtitle + " · " + app : r.subtitle) : app;
            }
            final Cast.Status res = r;
            main.post(() -> {
                adbBusy = false;
                if (!h.equals(host)) return;
                if (res != null || !"ok".equals(Adb.lastState)) adbSt = res;
                if ("ok".equals(Adb.lastState)) fetchTileIcons(apps());   // ADB is up: the tiles' icons, once
                sync();
            });
        });
    }

    private boolean inQueue() {
        Cast.Status v = view();
        return v != null && CastService.queue.size() > 1 && CastService.queueIndex(v.contentId) >= 0;
    }

    private void connectDevice() {
        if (host == null) return;
        if (!sam.handles(host)) cast.connect(host);   // a Samsung has no Cast receiver: Smart View only
        atv.connect(host);
    }

    private boolean isSamsung() { return sam.handles(host); }

    @Override public void onSamsung(String state, String message) {
        Diag.log("samsung", state + (message == null || message.isEmpty() ? "" : ": " + message));
        main.post(() -> {
            if (!"info".equals(state)) samState = state;
            if (message != null && !message.isEmpty()) {
                if (state.equals("waiting") || state.equals("info")) say(message);
                else if (!state.equals("offline")) showError(message);    // offline: the status line says it
            }
            if (state.equals("ready")) hideNotice.run();
            sync();
        });
    }

    private boolean paired() { return atv.paired(host); }

    /** A remote key: the paired remote (or Samsung's own), else `input keyevent` over ADB — so the
     *  buttons keep working while the phone isn't paired, as the notification's do. */
    private void tvKey(int code) {
        final String h = host;
        if (h == null) return;
        if (atv.paired(h) || !"ok".equals(Adb.lastState)) { atv.key(h, code); return; }
        bg(() -> {
            try { adb.shell(h, "input keyevent " + code); }
            catch (Exception e) { main.post(() -> showError(L.t("Нет соединения с ТВ"))); }
        });
    }

    // ================================================================ callbacks (background threads)

    @Override public void onStatus(Cast.Status s) {
        final Cast.Status c = s == null ? null : s.copy();
        main.post(() -> { st = c; sync(); });
    }

    @Override public void onError(String message) { main.post(() -> showError(message)); }

    /** The remote or Cast couldn't (re)connect on its own: check the TV at once, the status line shows it. */
    @Override public void onOffline(String message) { Diag.log("tv", "offline: " + message); main.post(() -> { if (resumed) { main.removeCallbacks(probe); main.post(probe); } }); }

    @Override public void onPower(Boolean on) { main.post(() -> { tvOn = on; sync(); TvRemoteService.now(this, tvApp, tvOn); }); }

    @Override public void onApp(String pkg) {
        main.post(() -> {
            tvApp = pkg;
            sync();
            TvRemoteService.now(this, tvApp, tvOn);
            if ("ok".equals(Adb.lastState)) TvIcons.learn(this, adb, host, pkg, () -> main.post(this::sync));   // its real name
        });
    }

    @Override public void onPairing(String state, String message) {
        Diag.log("pair", state + (message == null || message.isEmpty() ? "" : ": " + message));
        main.post(() -> {
            pairState = state;
            pairMsg = message == null ? "" : message;
            if (state.equals("idle") && paired()) { pinEdit.setText(""); hideNotice.run(); }
            sync();
        });
    }

    @Override public void onVoice(boolean on) {
        main.post(() -> {
            voice = on;
            if (on) startRecording(); else stopRecording();
            sync();
        });
    }

    @Override public void onDevices(List<Discovery.Device> list) {
        main.post(() -> {
            lastFound = new ArrayList<>(list);
            devices = withManual(list);   // discovered TVs + the ones the user added by IP
            DevicePicker.changed();
            if (host == null && !devices.isEmpty()) selectDevice(devices.get(0));
            else {
                for (Discovery.Device d : devices) if (d.host.equals(host)) devName = displayName(d);   // refresh shown name
                sync();
            }
        });
    }

    /** TV screenshot over ADB → Pictures/Fling, then shown full-screen. */
    private void tvScreenshot() {
        final String h = host;
        if (h == null) { showError(L.t("Устройство не выбрано")); return; }
        say(L.t("Делаю снимок экрана ТВ…"));
        bg(() -> {
            try {
                byte[] png = adb.shellBytes(h, "exec:screencap -p");
                if (png.length < 8 || (png[0] & 0xFF) != 0x89 || png[1] != 'P') throw new java.io.IOException("no image");
                android.graphics.Bitmap bmp = android.graphics.BitmapFactory.decodeByteArray(png, 0, png.length);
                android.graphics.Bitmap small = android.graphics.Bitmap.createScaledBitmap(bmp, 48, 27, true);
                int max = 0;
                for (int x = 0; x < 48; x++) for (int y = 0; y < 27; y++) {
                    int c = small.getPixel(x, y);
                    max = Math.max(max, Math.max(android.graphics.Color.red(c), Math.max(android.graphics.Color.green(c), android.graphics.Color.blue(c))));
                }
                final boolean black = max < 8;
                // shown first; saved only on "Save" (back / a tap outside just closes it)
                main.post(() -> {
                    if (isFinishing() || isDestroyed()) return;   // Back pressed during the capture
                    android.widget.ImageView iv = new android.widget.ImageView(this);
                    iv.setImageBitmap(bmp);
                    iv.setAdjustViewBounds(true);
                    iv.setClipToOutline(true);                     // the picture's corners follow the card's
                    iv.setOutlineProvider(new android.view.ViewOutlineProvider() {
                        @Override public void getOutline(View v, android.graphics.Outline o) {
                            o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), ui.dp(12));
                        }
                    });
                    ui.ask(this, L.t("Снимок экрана ТВ"),
                            black ? L.t("Снимок чёрный: видео защищено от снимков (DRM у YouTube/Netflix). Меню и главный экран ТВ снимаются нормально.") : null,
                            iv, new String[]{L.t("Сохранить")}, new Runnable[]{() -> saveScreenshot(png)});
                    hideNotice.run();
                });
            } catch (Exception e) {
                String st = Adb.lastState;
                main.post(() -> showError("unauthorized".equals(st)
                        ? L.t("Снимок экрана: подтвердите на ТВ «Разрешить отладку».")
                        : "closed".equals(st) ? L.t("Снимок экрана работает через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети».")
                        : L.t("Снимок экрана не получился: ") + e.getMessage()));
            }
        });
    }

    /** The TV screenshot into the phone's own screenshots folder — where its own screenshots
     *  go: DCIM/Screenshots on Samsung, Pictures/Screenshots elsewhere. */
    private void saveScreenshot(byte[] png) {
        bg(() -> {
            String dir = new java.io.File(android.os.Environment.getExternalStoragePublicDirectory(
                    android.os.Environment.DIRECTORY_DCIM), "Screenshots").isDirectory()
                    ? "DCIM/Screenshots" : "Pictures/Screenshots";
            android.content.ContentValues cv = new android.content.ContentValues();
            cv.put(android.provider.MediaStore.Images.Media.DISPLAY_NAME,
                    new java.text.SimpleDateFormat("'Screenshot_'yyyyMMdd-HHmmss'_TV.png'", java.util.Locale.US).format(new java.util.Date()));
            cv.put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/png");
            if (Build.VERSION.SDK_INT >= 29) cv.put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, dir);
            boolean ok = false;
            try {
                android.net.Uri uri = getContentResolver().insert(android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
                if (uri != null) try (java.io.OutputStream o = getContentResolver().openOutputStream(uri)) { o.write(png); ok = true; }
            } catch (Exception ignored) {}   // Android 8–9 without the storage permission
            final boolean saved = ok;
            main.post(() -> { if (saved) say(L.t("Снимок сохранён в «Скриншоты»")); else showError(L.t("Не удалось сохранить снимок")); });
        });
    }

    // ---------------------------------------------------------------- messages
    // One line at the bottom of the screen, over the content and above the navigation bar, so it
    // is seen wherever the page is scrolled: say() for progress and confirmations (neutral, short),
    // showError() for a failed action (red, stays longer). Tap to dismiss. The TV being off or away
    // is not a message: the status line under the title shows it (onOffline).

    private void say(String m) { notice(m, false); }

    private void showError(String m) {
        if (m == null) { hideNotice.run(); return; }
        if (DevicePicker.error(m)) return;                // the chooser is open: say it there
        notice(m, true);
    }

    private void notice(String m, boolean err) {
        if (m != null && !m.isEmpty()) Diag.log("ui", (err ? "error: " : "say: ") + m);
        if (notice == null || destroyed || m == null || m.isEmpty()) return;
        main.removeCallbacks(hideNotice);
        notice.setText(m);
        android.graphics.drawable.GradientDrawable bg = ui.round(ui.card, 14);
        bg.setStroke(ui.dp(1), err ? Ui.alpha(ui.red, 0.55f) : ui.hairline);
        notice.setBackground(bg);
        android.graphics.drawable.GradientDrawable dot = ui.oval(err ? ui.red : ui.accent);
        dot.setSize(ui.dp(8), ui.dp(8));
        notice.setCompoundDrawablesRelativeWithIntrinsicBounds(dot, null, null, null);
        notice.setContentDescription((err ? L.t("Ошибка") + ": " : "") + m);
        if (notice.getVisibility() != View.VISIBLE) {
            notice.setVisibility(View.VISIBLE);
            notice.setAlpha(0f);
            notice.setTranslationY(ui.dp(24));
        }
        notice.animate().alpha(1f).translationY(0).setDuration(180).start();
        notice.announceForAccessibility(notice.getContentDescription());
        // long enough to read: errors longer than confirmations
        long ms = Math.min(12000, (err ? 5000 : 2500) + 45L * m.length());
        main.postDelayed(hideNotice, ms);
    }

    private final Runnable hideNotice = () -> {
        if (notice == null || notice.getVisibility() != View.VISIBLE) return;
        notice.animate().alpha(0f).translationY(ui.dp(24)).setDuration(160)
                .withEndAction(() -> notice.setVisibility(View.GONE)).start();
    };

    // ================================================================ intents / links

    private static final Pattern URL_RE = Pattern.compile("https?://\\S+", Pattern.CASE_INSENSITIVE);

    private void handleIntent(Intent i) {
        if (i == null) return;
        ArrayList<Uri> files = sharedFiles(i);
        if (files != null) {
            i.setAction(Intent.ACTION_MAIN);   // don't recast on rotation
            castLocal(files, i.getType());
            return;
        }
        String text = null, subject = null;
        if (Intent.ACTION_SEND.equals(i.getAction())) {
            text = i.getStringExtra(Intent.EXTRA_TEXT);
            subject = i.getStringExtra(Intent.EXTRA_SUBJECT);
        } else if (Intent.ACTION_VIEW.equals(i.getAction()) && i.getData() != null) {
            text = i.getDataString();
        }
        if (text == null) return;
        String url = extractUrl(text);
        if (url == null) { showError(L.t("В отправленном нет ссылки.")); return; }
        setLink(url, subject);
        i.setAction(Intent.ACTION_MAIN);   // don't recast on rotation
        if (host != null) castLink(url, linkTitle); else pendingAutoCast = true;
    }

    /** Photos, videos or music handed to Fling (Share, Share several, Open with); null for links and text. */
    @SuppressWarnings("deprecation")
    private static ArrayList<Uri> sharedFiles(Intent i) {
        String a = i.getAction(), type = i.getType();
        if (type != null && type.startsWith("text/")) return null;
        ArrayList<Uri> out = new ArrayList<>();
        if (Intent.ACTION_SEND.equals(a)) {
            Uri u = i.getParcelableExtra(Intent.EXTRA_STREAM);
            if (u != null) out.add(u);
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(a)) {
            ArrayList<Uri> l = i.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (l != null) for (Uri u : l) if (u != null) out.add(u);
        } else if (Intent.ACTION_VIEW.equals(a) && i.getData() != null && "content".equals(i.getData().getScheme())) {
            out.add(i.getData());
        }
        if (out.isEmpty() && i.getClipData() != null && (Intent.ACTION_SEND.equals(a) || Intent.ACTION_SEND_MULTIPLE.equals(a)))
            for (int k = 0; k < i.getClipData().getItemCount(); k++) {
                Uri u = i.getClipData().getItemAt(k).getUri();
                if (u != null) out.add(u);
            }
        return out.isEmpty() ? null : out;
    }

    /** Phone files to CastService, which serves them over Wi-Fi and plays them on the TV, in order. */
    private void castLocal(ArrayList<Uri> uris, String type) {
        if (host == null) { pendingLocal = uris; pendingLocalType = type; say(L.t("Ищу телевизор…")); return; }
        pendingLocal = null;
        if (isSamsung()) {                                 // a Samsung plays phone files over DLNA (the first one)
            final String h = host;
            final Uri u = uris.get(0);
            toast(L.t("Отправляю на ТВ…"));
            sam.runBg(() -> {
                try {
                    MediaServer.Item it = MediaServer.publish(this, u, type, h);
                    sam.play(h, it.url, it.title, it.mime);
                } catch (Exception e) {
                    main.post(() -> showError(L.f("ТВ не принял файл: %s", e.getMessage())));
                }
            });
            return;
        }
        askNotifications();
        Intent s = new Intent(this, CastService.class).setAction(CastService.ACTION_LOCAL)
                .putExtra(CastService.EXTRA_HOST, host)
                .putExtra(CastService.EXTRA_TYPE, type)
                .putParcelableArrayListExtra(CastService.EXTRA_URIS, uris);
        // pass on the read access the sharing app gave us, so the service can read the files after we close
        ClipData cd = ClipData.newRawUri("", uris.get(0));
        for (int k = 1; k < uris.size(); k++) cd.addItem(new ClipData.Item(uris.get(k)));
        s.setClipData(cd);
        s.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startForegroundService(s);
            CastService.dismissed = null;
            toast(uris.size() == 1 ? L.t("Отправляю на ТВ…") : L.f("Отправляю файлы на ТВ (%d)…", uris.size()));
        } catch (Exception e) { showError(L.t("Не удалось открыть файл.")); }
    }

    private void pickFiles() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*")
                .putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"video/*", "audio/*", "image/*"})
                .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        try { startActivityForResult(i, REQ_FILES); } catch (Exception e) { showError(L.t("На телефоне нет приложения для выбора файлов.")); }
    }

    @Override protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req != REQ_FILES || res != RESULT_OK || data == null) return;
        ArrayList<Uri> uris = new ArrayList<>();
        if (data.getClipData() != null)
            for (int k = 0; k < data.getClipData().getItemCount(); k++) uris.add(data.getClipData().getItemAt(k).getUri());
        else if (data.getData() != null) uris.add(data.getData());
        for (Uri u : uris)   // keep access while the TV plays, even after Fling is closed
            try { getContentResolver().takePersistableUriPermission(u, Intent.FLAG_GRANT_READ_URI_PERMISSION); } catch (Exception ignored) {}
        if (!uris.isEmpty()) castLocal(uris, null);
    }

    /** Android 13+: ask once for notifications, so the playback controls can be shown. */
    private void askNotifications() {
        if (Build.VERSION.SDK_INT < 33 || prefs.getBoolean("askedNotify", false)) return;
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return;
        prefs.edit().putBoolean("askedNotify", true).apply();
        requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIFY);
    }

    static String extractUrl(String text) {
        Matcher m = URL_RE.matcher(text);
        if (!m.find()) return null;
        String u = m.group();
        while (u.endsWith(".") || u.endsWith(",") || u.endsWith(")") || u.endsWith("»")) u = u.substring(0, u.length() - 1);
        return u;
    }

    private void setLink(String url, String title) {
        link = url;
        linkTitle = title;
        String id = youtubeId(url);
        if (id != null) {
            loadImage("https://img.youtube.com/vi/" + id + "/hqdefault.jpg", linkArt);
            fetchYoutubeTitle(id, t -> { if (url.equals(link)) { linkTitle = t; sync(); } });
        } else {
            linkArt.url = null;
            linkArt.setBitmap(null);
        }
    }

    static String youtubeId(String raw) {
        if (raw == null) return null;
        Uri u = Uri.parse(raw);
        String h = u.getHost() == null ? "" : u.getHost().toLowerCase(Locale.ROOT);
        if (!(h.equals("youtu.be") || h.equals("youtube.com") || h.endsWith(".youtube.com"))) return null;   // not "notyoutube.com"
        List<String> seg = u.getPathSegments();
        if (h.equals("youtu.be") && !seg.isEmpty()) return seg.get(0);
        if (seg.size() >= 2 && (seg.get(0).equals("shorts") || seg.get(0).equals("embed") || seg.get(0).equals("live"))) return seg.get(1);
        try { return u.getQueryParameter("v"); } catch (Exception e) { return null; }
    }

    static boolean isDirectMedia(String url) {
        String p = Uri.parse(url).getPath();
        if (p == null) return false;
        p = p.toLowerCase(Locale.ROOT);
        for (String ext : new String[]{".mp4", ".m4v", ".webm", ".mkv", ".mov", ".ts", ".m3u8", ".mpd", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".flac", ".wav"})
            if (p.endsWith(ext)) return true;
        return false;
    }

    static String hostOf(String url) {
        String h = Uri.parse(url).getHost();
        if (h == null) return "—";
        return h.startsWith("www.") ? h.substring(4) : h;
    }

    /** Where a link goes: Cast for streams, the TV's own apps for everything else. */
    private void castLink(String url, String title) {
        if (host == null) { pendingAutoCast = true; say(L.t("Ищу телевизор…")); return; }
        String id = youtubeId(url);
        if (isSamsung()) {
            final String h = host;
            if (isDirectMedia(url)) {
                sam.runBg(() -> {
                    try { sam.play(h, url, title == null ? hostOf(url) : title, Samsung.mimeOfUrl(url)); }
                    catch (Exception e) { main.post(() -> showError(L.f("ТВ не принял ссылку: %s", e.getMessage()))); }
                });
            } else {
                atv.launch(host, url);                     // YouTube → its app; other pages → the TV's browser
            }
            addRecent(url, title);
            say(L.t("Отправляю на ТВ…"));
            sync();
            return;
        }
        if (isDirectMedia(url)) {
            cast.load(url, title);
        } else if (id != null) {
            if (!paired()) { showError(L.t("Чтобы открыть YouTube на ТВ, один раз нажмите «Связать с ТВ» ниже.")); return; }
            atv.launch(host, "https://www.youtube.com/watch?v=" + id);
        } else {
            if (!paired()) { showError(L.t("Эту страницу можно открыть на ТВ после того, как вы нажмёте «Связать с ТВ» ниже.")); return; }
            atv.launch(host, url);
        }
        addRecent(url, title);
        say(L.t("Отправляю на ТВ…"));
        sync();
    }

    private void toast(String s) { say(s); }          // one place for messages: the line at the bottom

    // recent links (last 5)
    private List<String[]> recents() {
        List<String[]> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(prefs.getString("recents", "[]"));
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                out.add(new String[]{o.getString("url"), o.optString("title", "")});
            }
        } catch (Exception ignored) {}
        return out;
    }

    private void addRecent(String url, String title) {
        List<String[]> r = recents();
        r.removeIf(x -> x[0].equals(url));
        r.add(0, new String[]{url, title == null ? "" : title});
        JSONArray a = new JSONArray();
        for (int i = 0; i < Math.min(5, r.size()); i++) {
            try { a.put(new JSONObject().put("url", r.get(i)[0]).put("title", r.get(i)[1])); } catch (Exception ignored) {}
        }
        prefs.edit().putString("recents", a.toString()).apply();
    }

    // ================================================================ app tiles

    private LinearLayout appGrid;

    /** Tiles are kept per TV, under its name (a new IP from the router keeps them). */
    private String appsKey() { return "apps:" + nz(devName, nz(host, "")); }

    /** {name, link, package}; a tile without a link opens by package (market://launch?id=…). */
    private List<String[]> apps() {
        List<String[]> out = new ArrayList<>();
        String raw = prefs.getString(appsKey(), null);
        if (raw != null) {
            try {
                JSONArray a = new JSONArray(raw);
                for (int i = 0; i < a.length(); i++) {
                    JSONObject o = a.getJSONObject(i);
                    out.add(new String[]{o.optString("name"), o.optString("link"), o.optString("pkg")});
                }
                return out;
            } catch (Exception ignored) { out.clear(); }
        }
        for (String[] a : TV_APPS) out.add(a.clone());
        return out;
    }

    private void saveApps(List<String[]> list) {
        JSONArray a = new JSONArray();
        for (String[] x : list) {
            try { a.put(new JSONObject().put("name", x[0]).put("link", x[1]).put("pkg", x[2])); } catch (Exception ignored) {}
        }
        prefs.edit().putString(appsKey(), a.toString()).apply();
        buildApps();
        sync();
    }

    /** Edit mode (a long press on a tile): drag a tile to move it, ✕ removes it, + adds one. */
    private boolean editTiles;
    private final List<View> tileBadges = new ArrayList<>();
    private View tileBar;

    private void buildApps() {
        if (appGrid == null) return;
        appGrid.removeAllViews();
        chips.clear();
        tileNames.clear();
        tileBadges.clear();
        List<String[]> list = apps();
        for (String[] a : list) {                                     // a name made from the package -> the TV's own
            String tvName = TvIcons.label(this, a[2]);
            if (tvName != null && a[0].equals(labelFor(a[2]))) a[0] = tvName;
            if (!a[2].isEmpty()) tileNames.put(a[2], a[0]);           // "Now: …" uses the same name
        }
        boolean icons = false;
        for (String[] a : list) if (TvIcons.icon(this, a[2]) != null) { icons = true; break; }
        final int tileH = ui.dp(icons ? 50 : 38);                 // one height for every tile, "+" too
        int n = list.size() + (list.size() < MAX_APPS ? 1 : 0);   // + the "+" tile
        LinearLayout r = null;
        for (int i = 0; i < n; i++) {
            if (i % 4 == 0) { r = ui.row(); appGrid.addView(r, ui.margins(Ui.lp(-1, -2), 0, i == 0 ? 0 : 6, 0, 0)); }
            View cellView;
            if (i < list.size()) {
                final int idx = i;
                final String[] app = list.get(i);
                final FrameLayout cell = new FrameLayout(this);
                TextView c = ui.chip(app[0], v -> { if (!editTiles) launchTile(app); });
                c.setTag(app[2]);
                withIcon(c, app[2], 20);
                c.setMinHeight(tileH);
                c.setOnLongClickListener(v -> { startTileDrag(cell, idx); return true; });
                chips.add(c);
                cell.addView(c, new FrameLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT));
                TextView x = ui.text("✕", 10, 0xFFFFFFFF, true);   // remove: a small round badge in the corner
                x.setGravity(Gravity.CENTER);
                x.setBackground(ui.oval(ui.red));
                x.setContentDescription(L.f("Убрать %s", app[0]));
                x.setOnClickListener(v -> { List<String[]> l = apps(); if (idx < l.size()) { l.remove(idx); saveApps(l); } });
                x.setVisibility(editTiles ? View.VISIBLE : View.GONE);
                tileBadges.add(x);
                FrameLayout.LayoutParams xp = new FrameLayout.LayoutParams(ui.dp(18), ui.dp(18), Gravity.TOP | Gravity.END);
                xp.setMargins(0, ui.dp(3), ui.dp(3), 0);
                cell.addView(x, xp);
                cell.setOnDragListener((v, ev) -> onTileDrag(v, ev, idx));
                cellView = cell;
            } else {
                TextView c = ui.chip("+", v -> pickTvApp(-1));
                c.setTextColor(ui.fgA(0.6f));
                c.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 20);
                c.setMinHeight(tileH);
                c.setContentDescription(L.t("Добавить приложение с ТВ"));
                cellView = c;
            }
            LinearLayout.LayoutParams p = Ui.lpW(1);
            p.setMargins(i % 4 == 0 ? 0 : ui.dp(3), 0, i % 4 == 3 ? 0 : ui.dp(3), 0);
            r.addView(cellView, p);
        }
        for (int k = n % 4; k != 0 && k < 4; k++) {             // keep the last row's tiles the same width
            LinearLayout.LayoutParams p = Ui.lpW(1);
            p.setMargins(ui.dp(3), 0, k == 3 ? 0 : ui.dp(3), 0);
            r.addView(new View(this), p);
        }
        // under the tiles while editing: what to do, back to the defaults, done
        LinearLayout bar = ui.row();
        TextView hint = ui.text(L.t("Перетащите плитку, чтобы переставить"), 12, ui.fgA(0.5f), false);
        bar.addView(hint, Ui.lpW(1));
        TextView reset = ui.text(L.t("По умолчанию"), 13, ui.fgA(0.7f), false);
        reset.setPadding(ui.dp(10), ui.dp(8), ui.dp(10), ui.dp(8));
        reset.setOnClickListener(v -> { prefs.edit().remove(appsKey()).apply(); buildApps(); sync(); });
        bar.addView(reset);
        TextView done = ui.text(L.t("Готово"), 14, ui.accent, true);
        done.setPadding(ui.dp(14), ui.dp(8), ui.dp(14), ui.dp(8));
        done.setBackground(ui.rippleRound(ui.accA(ui.dark ? 0.22f : 0.14f), 14));
        done.setOnClickListener(v -> setEditTiles(false));
        bar.addView(done);
        bar.setVisibility(editTiles ? View.VISIBLE : View.GONE);
        tileBar = bar;
        appGrid.addView(bar, ui.margins(Ui.lp(-1, -2), 0, 10, 0, 0));
        fetchTileIcons(list);
    }

    /** Badges and the bar appear / go without redrawing, so a long press can go straight into a drag. */
    private void setEditTiles(boolean on) {
        editTiles = on;
        for (View b : tileBadges) b.setVisibility(on ? View.VISIBLE : View.GONE);
        if (tileBar != null) tileBar.setVisibility(on ? View.VISIBLE : View.GONE);
    }

    private void startTileDrag(View cell, int idx) {
        setEditTiles(true);
        cell.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS);
        android.content.ClipData data = android.content.ClipData.newPlainText("fling-tile", String.valueOf(idx));
        View tile = ((FrameLayout) cell).getChildAt(0);              // the tile itself: no ✕ in the shadow
        if (cell.startDragAndDrop(data, new View.DragShadowBuilder(tile), idx, 0)) cell.setAlpha(0.3f);
    }

    /** Another tile under the finger lights up; dropping there puts the dragged tile in its place. */
    private boolean onTileDrag(View v, android.view.DragEvent ev, int idx) {
        switch (ev.getAction()) {
            case android.view.DragEvent.ACTION_DRAG_STARTED:
                return ev.getLocalState() instanceof Integer;
            case android.view.DragEvent.ACTION_DRAG_ENTERED:
                if (!Integer.valueOf(idx).equals(ev.getLocalState())) v.animate().scaleX(1.08f).scaleY(1.08f).setDuration(120).start();
                return true;
            case android.view.DragEvent.ACTION_DRAG_EXITED:
                v.animate().scaleX(1f).scaleY(1f).setDuration(120).start();
                return true;
            case android.view.DragEvent.ACTION_DROP: {
                int from = (Integer) ev.getLocalState();
                if (from != idx) {
                    main.post(() -> {
                        List<String[]> l = apps();
                        if (from < l.size() && idx < l.size()) { l.add(idx, l.remove(from)); saveApps(l); }
                    });
                }
                return true;
            }
            case android.view.DragEvent.ACTION_DRAG_ENDED:
                v.setScaleX(1f); v.setScaleY(1f); v.setAlpha(1f);
                return true;
        }
        return true;
    }

    /** The app's icon from the TV (TvIcons) above the name; nothing when it isn't known yet. */
    private void withIcon(TextView t, String pkg, int dp) {
        android.graphics.Bitmap b = TvIcons.icon(this, pkg);
        if (b == null) return;
        android.graphics.drawable.BitmapDrawable d = new android.graphics.drawable.BitmapDrawable(getResources(), b);
        d.setBounds(0, 0, ui.dp(dp), ui.dp(dp));
        t.setCompoundDrawablesRelative(null, d, null, null);
        t.setCompoundDrawablePadding(ui.dp(3));
        t.setPadding(t.getPaddingLeft(), ui.dp(6), t.getPaddingRight(), ui.dp(5));
    }

    /** Hosts whose tile icons were asked for in this run: once each, not on every redraw. */
    private final java.util.Set<String> iconsAsked = new java.util.HashSet<>();

    private void fetchTileIcons(List<String[]> list) {
        final String h = host;
        if (h == null || !"ok".equals(Adb.lastState) || !iconsAsked.add(h)) return;
        List<String> pkgs = new ArrayList<>();
        for (String[] a : list) pkgs.add(a[2]);
        final List<String> need = TvIcons.missing(this, pkgs);
        if (need.isEmpty()) return;
        bg(() -> {
            try {
                if (TvIcons.fetch(this, adb, h, need) > 0) main.post(() -> { if (h.equals(host)) buildApps(); });
            } catch (Exception e) {
                Diag.log("icons", "from the TV: " + e.getMessage());
            }
        });
    }

    /** ADB first: it opens any installed app by its package. Over the remote protocol many TVs
     *  (Google TV on Xiaomi, for one) refuse market://launch?id=… and https:// links; only an
     *  app's own link (vnd.youtube.launch://, netflix://) opens there. */
    private void launchTile(String[] app) {
        final String h = host;
        if (h == null) { showError(L.t("Устройство не выбрано")); return; }
        final String link = app[1], pkg = app[2], name = app[0];
        if (sam.handles(h)) { atv.launch(h, !link.isEmpty() ? link : pkg); return; }   // Samsung's own apps
        final boolean ownLink = link.contains("://") && !link.startsWith("http://") && !link.startsWith("https://")
                && !link.startsWith("market://");
        boolean adbMaybe = Adb.lastState == null || "ok".equals(Adb.lastState);
        if (adbMaybe && pkg.matches("[A-Za-z][\\w]*(\\.[\\w]+)+")) {
            bg(() -> {
                boolean opened = false;
                try {
                    for (String cat : new String[]{"LEANBACK_LAUNCHER", "LAUNCHER"}) {
                        String out = adb.shell(h, "monkey -p " + pkg + " -c android.intent.category." + cat + " 1");
                        if (out != null && !out.contains("No activities found") && !out.contains("Error")) { opened = true; break; }
                    }
                } catch (Exception ignored) {}
                final boolean ok = opened;
                main.post(() -> {
                    if (ok) return;
                    if (paired() && ownLink) atv.launch(h, link);          // ADB couldn't: the remote may still
                    else if ("ok".equals(Adb.lastState)) showError(L.f("%s не установлено на ТВ.", name));
                    else showError(L.f("%s открывается через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети».", name));
                });
            });
            return;
        }
        if (paired() && ownLink) { atv.launch(h, link); return; }
        showError(L.f("%s открывается через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети».", name));
    }

    /** The apps installed on the TV (over ADB), to add (idx -1) or to put in place of tile idx. */
    private void pickTvApp(int idx) {
        final String h = host;
        if (h == null) { showError(L.t("Устройство не выбрано")); return; }
        if (!"ok".equals(Adb.lastState)) {
            showError(L.t("Список приложений ТВ берётся через ADB — включите на ТВ «Для разработчиков» → «Отладка по сети»."));
            return;
        }
        say(L.t("Загружаю приложения с ТВ…"));
        bg(() -> {
            List<String> pkgs = new ArrayList<>();
            try {
                String out = adb.shell(h, "cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER");
                pkgs = launcherPackages(out);
                if (pkgs.isEmpty()) pkgs = launcherPackages(adb.shell(h, "pm list packages -3"));
            } catch (Exception ignored) {}
            try {                                                  // their real names and icons, once
                List<String> need = TvIcons.missing(this, pkgs);
                if (!need.isEmpty()) TvIcons.fetch(this, adb, h, need);
            } catch (Exception e) {
                Diag.log("icons", "from the TV: " + e.getMessage());
            }
            final List<String> found = pkgs;
            main.post(() -> {
                if (isFinishing() || isDestroyed()) return;
                hideNotice.run();
                if (found.isEmpty()) { showError(L.t("Не удалось получить список приложений с ТВ.")); return; }
                List<String[]> cur = apps();
                java.util.Set<String> have = new java.util.HashSet<>();
                for (String[] a : cur) have.add(a[2]);
                List<String[]> choices = new ArrayList<>();
                for (String p : found) if (!have.contains(p)) choices.add(new String[]{nz(TvIcons.label(this, p), labelFor(p)), "", p});
                choices.sort((a, b) -> a[0].compareToIgnoreCase(b[0]));
                if (choices.isEmpty()) { say(L.t("Все приложения с ТВ уже на плитках.")); return; }
                LinearLayout col = ui.col();
                final android.app.Dialog[] dlg = new android.app.Dialog[1];
                for (String[] c : choices) {
                    LinearLayout row = ui.row();
                    row.setPadding(ui.dp(8), ui.dp(8), ui.dp(12), ui.dp(8));
                    row.setBackground(ui.rippleRound(0x00000000, 12));
                    android.widget.ImageView ic = new android.widget.ImageView(this);
                    android.graphics.Bitmap bm = TvIcons.icon(this, c[2]);
                    if (bm != null) ic.setImageBitmap(bm);
                    row.addView(ic, ui.margins(Ui.lp(ui.dp(36), ui.dp(36)), 0, 0, 12, 0));
                    LinearLayout names = ui.col();
                    names.addView(ui.text(c[0], 16, ui.fg, false));
                    TextView pk = ui.text(c[2], 12, ui.fgA(0.45f), false);
                    pk.setPadding(0, ui.dp(3), 0, 0);
                    names.addView(pk);
                    row.addView(names, Ui.lpW(1));
                    row.setOnClickListener(v -> {
                        if (dlg[0] != null) dlg[0].dismiss();
                        List<String[]> l = apps();
                        if (idx >= 0 && idx < l.size()) l.set(idx, c); else if (l.size() < MAX_APPS) l.add(c);
                        saveApps(l);
                    });
                    col.addView(row, Ui.lp(-1, -2));
                }
                android.widget.ScrollView sv = new android.widget.ScrollView(this) {
                    @Override protected void onMeasure(int w, int hs) {
                        int max = (int) (getResources().getDisplayMetrics().heightPixels * 0.55f);
                        super.onMeasure(w, MeasureSpec.makeMeasureSpec(max, MeasureSpec.AT_MOST));
                    }
                };
                sv.addView(col);
                dlg[0] = ui.ask(this, idx >= 0 ? L.t("Заменить на…") : L.t("Добавить приложение с ТВ"), null, sv,
                        new String[]{L.t("Отмена")}, new Runnable[]{null});
            });
        });
    }

    /** `cmd package query-activities --brief` ("pkg/activity" lines) or `pm list packages` → packages. */
    private static List<String> launcherPackages(String text) {
        List<String> out = new ArrayList<>();
        if (text == null) return out;
        Pattern p = Pattern.compile("^\\s*(?:package:)?([A-Za-z][\\w]*(?:\\.[\\w]+)+)(?:/|$)");
        for (String line : text.split("\n")) {
            Matcher m = p.matcher(line.trim());
            if (m.find() && !out.contains(m.group(1)) && !HIDDEN.contains(m.group(1))) out.add(m.group(1));
        }
        return out;
    }

    private static String labelFor(String pkg) {
        for (String[] a : TV_APPS) if (a[2].equals(pkg)) return a[0];
        for (String[] a : KNOWN_APPS) if (a[0].equals(pkg)) return a[1];
        String tail = null;
        String[] parts = pkg.split("\\.");
        for (int i = parts.length - 1; i >= 0; i--) if (!GENERIC.contains(parts[i].toLowerCase(Locale.ROOT))) { tail = parts[i]; break; }
        if (tail == null) tail = parts[parts.length - 1];
        tail = tail.replace('_', ' ');
        return tail.isEmpty() ? pkg : Character.toUpperCase(tail.charAt(0)) + tail.substring(1);
    }

    // ================================================================ device

    private void selectDevice(Discovery.Device d) { selectHost(d.host, displayName(d)); }

    private void selectHost(String h, String name) {
        if (h.equals(host)) return;
        cast.disconnect();
        atv.disconnect();
        host = h;
        devName = name;
        st = null; tvOn = null; tvApp = null; pairState = "idle";
        adbSt = null; adbNext = 0;   // now-playing came from the old TV; read the new one right away
        prefs.edit().putString("host", h).putString("name", name).apply();
        buildApps();                                                   // this TV's own tiles
        maybeSetup();
        if (prefs.getBoolean("remote_notify", true)) TvRemoteService.show(this, h, name);
        reachable = null; pathMs = -1;
        connectDevice();
        atv.adopt(h);                                                  // paired at home → paired here too
        probeDelay = PROBE_MIN; main.removeCallbacks(probe); main.post(probe);
        if (pendingAutoCast && link != null) { pendingAutoCast = false; castLink(link, linkTitle); }
        if (pendingLocal != null) castLocal(pendingLocal, pendingLocalType);
        sync();
    }

    // ================================================================ several TVs

    /** TVs added by IP are stored as a JSON array [{name, host}] in prefs "manual". */
    private List<Discovery.Device> manualDevices() {
        List<Discovery.Device> out = new ArrayList<>();
        try {
            org.json.JSONArray a = new org.json.JSONArray(prefs.getString("manual", "[]"));
            for (int i = 0; i < a.length(); i++) {
                org.json.JSONObject o = a.getJSONObject(i);
                out.add(new Discovery.Device(o.getString("name"), o.getString("host"), "manual"));   // model "manual" marks them
            }
        } catch (Exception ignored) {}
        return out;
    }

    private void saveManual(List<Discovery.Device> list) {
        org.json.JSONArray a = new org.json.JSONArray();
        for (Discovery.Device d : list) {
            try { a.put(new org.json.JSONObject().put("name", d.name).put("host", d.host)); } catch (Exception ignored) {}
        }
        prefs.edit().putString("manual", a.toString()).apply();   // survives app restarts
    }

    /** Discovered TVs first, then manual ones that discovery didn't see (no duplicates by address). */
    private List<Discovery.Device> withManual(List<Discovery.Device> found) {
        List<Discovery.Device> out = new ArrayList<>(found);
        List<Discovery.Device> extra = new ArrayList<>(sam.known());   // Samsung TVs seen before
        extra.addAll(manualDevices());
        for (Discovery.Device m : extra) {
            boolean seen = false;
            for (Discovery.Device d : out) if (d.host.equals(m.host)) seen = true;
            if (!seen) out.add(m);
        }
        return out;
    }

    private boolean isManual(Discovery.Device d) { return "manual".equals(d.model); }

    /** IPv4 check: exactly 4 numbers separated by dots, each 0..255. */
    private static boolean validIp(String ip) {
        String[] p = ip.split("\\.", -1);
        if (p.length != 4) return false;
        for (String x : p) {
            if (!x.matches("\\d{1,3}") || Integer.parseInt(x) > 255) return false;
        }
        return true;
    }

    /** An IPv4 address or a host name such as a Tailscale MagicDNS name (tv.tail1234.ts.net). */
    private static boolean validHost(String h) {
        if (validIp(h)) return true;
        if (h.matches("[\\d.]+")) return false;                        // a mistyped IPv4, not a name
        return h.length() <= 253 && h.matches("(?i)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*");
    }

    /** True when another TV in the list has exactly the same name. */
    private boolean nameClash(Discovery.Device d) {
        int n = 0;
        for (Discovery.Device x : devices) if (x.name.equals(d.name)) n++;
        return n > 1;
    }

    /** Name shown in the header/footer: adds the address when two TVs share a name. */
    private String displayName(Discovery.Device d) {
        return nameClash(d) ? d.name + " (" + d.host + ")" : d.name;
    }

    /** Menu line: name + hints (address on a name clash, remote paired, added by IP). */
    private String menuLabel(Discovery.Device d) {
        List<String> marks = new ArrayList<>();
        if (nameClash(d)) marks.add(d.host);                 // tell identical names apart
        if (atv.paired(d.host)) marks.add("Android TV Remote ✓");   // network remote already paired
        if (Tailnet.isTailnetHost(d.host)) marks.add("Tailscale");   // remote access address
        else if (isManual(d)) marks.add(L.t("по IP"));       // not found by discovery
        return (d.host.equals(host) ? "✓  " : "") + d.name + (marks.isEmpty() ? "" : "  ·  " + String.join(", ", marks));
    }

    /** The TV chooser (DevicePicker): Wi‑Fi, ADB and Tailscale in one screen, searching at once. */
    private void openPicker() {
        DevicePicker.show(this, ui, new DevicePicker.Host() {
            public List<Discovery.Device> devices() { return devices; }
            public String current() { return host; }
            public boolean paired(String h) { return atv.paired(h); }
            public boolean manual(Discovery.Device d) { return isManual(d); }
            public void select(Discovery.Device d) { tvPicked = true; selectDevice(d); }
            public void addByAddress(String text) { addByIp(text); }
            public void addAdbOnly(String h, String name) {
                Discovery.Device d = new Discovery.Device(name, h, "manual");
                List<Discovery.Device> manual = manualDevices();
                manual.removeIf(x -> x.host.equals(h));
                manual.add(d);
                saveManual(manual);
                devices = withManual(devices);
                tvPicked = true;
                selectDevice(d);
            }
            public void forget(Discovery.Device d) { forgetManual(d); }
            public void rescan() {
                discovery.stop(); discovery.start();
                sam.discover(() -> main.post(() -> onDevices(lastFound)));
            }
            public void tailscaleHelp() { remoteAccess(); }
        });
    }

    private void askIp() {
        EditText e = ui.field("192.168.1.50", false);   // example address, not a real one
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        ui.ask(this, L.t("Адрес телевизора"),
                L.t("В локальной сети — IP из настроек ТВ (Сеть). Через Tailscale — 100.x.y.z или имя MagicDNS."),
                e, new String[]{L.t("Добавить"), L.t("Отмена")},
                new Runnable[]{() -> addByIp(e.getText().toString().trim()), null});
    }

    /** Checks there is a Cast device or a Google TV remote at the address, asks the TV its
     *  name, saves and selects it. Takes LAN and tailnet addresses and MagicDNS names. */
    private void addByIp(String input) {
        final String ip = input.toLowerCase(Locale.ROOT).replaceAll("\\.$", "");
        if (!validHost(ip)) { showError(L.t("Это не похоже на адрес ТВ (пример: 192.168.1.50 или 100.101.102.103).")); return; }
        bg(() -> {
            String name = ip;
            // 8009 = Google Cast; 6466 = the Android TV remote (a tailnet may reach only some ports)
            if (sam.probe(ip)) {                           // a Samsung: Smart View, remembered by Samsung
                main.post(() -> {
                    devices = withManual(lastFound);
                    for (Discovery.Device d : devices) if (d.host.equals(ip)) { selectDevice(d); break; }
                    DevicePicker.close();
                    sync();
                });
                return;
            }
            if (Tailnet.connectMs(ip, 8009, 3000) < 0 && Tailnet.connectMs(ip, 6466, 3000) < 0) {
                main.post(() -> showError(L.f("По адресу %s нет Chromecast, Google TV или Samsung.", ip)));
                return;
            }
            try {   // the TV's own name from its DIAL device description
                java.net.HttpURLConnection c = (java.net.HttpURLConnection)
                        new java.net.URL("http://" + ip + ":8008/ssdp/device-desc.xml").openConnection();
                c.setConnectTimeout(3000); c.setReadTimeout(3000);
                String xml;
                try (java.io.InputStream in = c.getInputStream()) {          // stream closed automatically
                    xml = new String(Cast.readAll(in), java.nio.charset.StandardCharsets.UTF_8);
                } finally {
                    c.disconnect();                                          // release the HTTP connection
                }
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("<friendlyName>([^<]+)</friendlyName>").matcher(xml);
                if (m.find()) name = m.group(1).trim();
            } catch (Exception ignored) {}   // keep the IP as the name
            final Discovery.Device d = new Discovery.Device(name, ip, "manual");
            main.post(() -> {
                List<Discovery.Device> manual = manualDevices();
                manual.removeIf(x -> x.host.equals(ip));   // replace an older entry for the same address
                manual.add(d);
                saveManual(manual);
                devices = withManual(devices);              // show it in the list right away
                selectDevice(d);
                DevicePicker.close();
            });
        });
    }

    private void forgetManual(Discovery.Device d) {
        List<Discovery.Device> manual = manualDevices();
        manual.removeIf(x -> x.host.equals(d.host));
        saveManual(manual);
        devices.removeIf(x -> x.host.equals(d.host) && isManual(x));   // discovered copies stay
        if (d.host.equals(host)) {
            if (!devices.isEmpty()) selectDevice(devices.get(0));   // fall back to another TV
            else {                                                  // that was the only TV: disconnect and forget it
                cast.disconnect(); atv.disconnect();
                host = null; devName = null; st = null; adbSt = null;
                prefs.edit().remove("host").remove("name").apply();
            }
        }
        sync();
    }

    /** Tailscale: Fling has no VPN of its own; the official app puts the phone on the tailnet. */
    private void remoteAccess() {
        String title = L.t("Удалённый доступ");
        if (!Tailnet.installed(this)) {
            ui.ask(this, title,
                    L.t("Чтобы управлять телевизором вне дома, установите Tailscale на телефон и на ТВ. На ТВ его установит Fling для ПК — из «Каталога»."),
                    null, new String[]{L.t("Установить Tailscale"), L.t("Отмена")},
                    new Runnable[]{() -> Tailnet.openStore(this), null});
        } else {
            ui.ask(this, title,
                    Tailnet.vpnActive(this)
                            ? L.t("Tailscale включён. Добавьте ТВ по его адресу 100.x.y.z или имени MagicDNS.")
                            : L.t("Tailscale выключен. Включите его, чтобы управлять телевизором вне дома."),
                    null, new String[]{L.t("Добавить ТВ…"), L.t("Открыть Tailscale"), L.t("Отмена")},
                    new Runnable[]{this::askIp, () -> Tailnet.open(this), null});
        }
    }

    // ================================================================ playback actions

    private void togglePlay() {
        if (tvMedia()) {
            tvKey(Atv.PLAY_PAUSE);
            adbSt.elapsed = adbSt.elapsedNow(); adbSt.updatedAt = SystemClock.elapsedRealtime();
            adbSt.state = adbSt.playing() ? "PAUSED" : "PLAYING";
            adbNext = 0; main.postDelayed(this::pollAdb, 1200);
            sync(); return;
        }
        if (st == null) return;
        if (paired() || (st.stuck() && adbOk())) { tvKey(Atv.PLAY_PAUSE); return; }
        if (st.stuck()) { showError(L.t("Телевизор игнорирует паузу по Chromecast (так ведёт себя YouTube на ТВ). Нажмите «Связать с ТВ» ниже — пауза будет работать как кнопка пульта.")); return; }
        if (st.playing()) cast.pause(); else cast.play();
        st = cast.status();   // Cast updates its status optimistically without onStatus
        sync();
    }

    private void seek(int s) {
        Cast.Status v = view();
        if (v == null) return;
        double target = Math.max(0, v.elapsedNow() + s);
        if (v.duration > 0) target = Math.min(target, v.duration);
        seekTo(target);
    }

    /** A Cast session playing the same thing seeks to the exact second. Its own position can be
     *  wrong (YouTube started from a phone reports ~0 s), so the target comes from the TV (view()),
     *  else seeking "+30 s" jumped back to the start. */
    private boolean castSeeks(Cast.Status v) {
        // Only Fling's own cast: YouTube's Cast session on these TVs jumps to 0:00 and pauses on SEEK
        return st != null && v == st && st.hasMedia() && !st.stuck() && !ytOnTv(st);
    }

    /** n presses of fast-forward (n > 0) or rewind, then Play: YouTube on the TV treats them as
     *  scrubbing (paused) and jumps only on Play. One ADB command for all. */
    private void seekKeys(int n, boolean resume) {
        if (n == 0) return;
        final int code = n > 0 ? Atv.FFWD : Atv.REWIND, k = Math.min(Math.abs(n), 60);
        final String h = host;
        if (!paired() && adbOk() && h != null) {
            StringBuilder cmd = new StringBuilder("input keyevent");
            for (int i = 0; i < k; i++) cmd.append(' ').append(code);
            bg(() -> {
                try {
                    adb.shell(h, cmd.toString());
                    if (resume) { Thread.sleep(900); adb.shell(h, "input keyevent 126"); }   // Play applies the jump
                } catch (Exception ignored) {}
            });
        } else {
            for (int i = 0; i < k; i++) tvKey(code);
            if (resume) tvKey(126);
        }
        adbNext = 0; main.postDelayed(this::pollAdb, 1500);
    }

    /** Seek to a second: exactly over Cast when it plays this; else by presses on the TV. */
    private void seekTo(double target) {
        Cast.Status v = view();
        if (v == null) return;
        if (ytOnTv(v) && ytExact()) { ytDo(() -> yt.seekTo(target)); return; }   // to the second
        if (castSeeks(v)) {
            cast.seekTo(target);
            adbNext = 0; main.postDelayed(this::pollAdb, 1500);
            return;
        }
        if (tvMedia() || v.stuck()) {
            if (!paired() && !adbOk()) { showError(L.t("Перемотка этого видео работает только после «Связать с ТВ» — кнопка ниже.")); return; }
            seekKeys((int) Math.round((target - v.elapsedNow()) / 10.0), v.playing());
            return;
        }
        cast.seekTo(target);
    }

    private final Runnable seekNow = () -> {
        Cast.Status v = view();
        if (v != null && v.duration > 0) seekTo(v.duration * nowProg.value / 100.0);
    };

    /** Continue on the phone: YouTube opens the same video at the same second; the TV pauses. */
    private void openOnPhone() {
        Cast.Status v = view();
        if (v == null) return;
        String id = v.contentId != null && v.contentId.matches("[\\w-]{11}") ? v.contentId : null;
        CharSequence title = nowTitle.getText();
        Intent i;
        if (id != null) {
            i = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse("https://www.youtube.com/watch?v=" + id + "&t=" + (int) v.elapsedNow() + "s"));
        } else if (title != null && title.length() > 0) {               // not identified: search it by name
            i = new Intent(Intent.ACTION_SEARCH).putExtra("query", title.toString());
        } else return;
        i.setPackage("com.google.android.youtube");
        if (v.playing()) togglePlay();
        try { startActivity(i); }
        catch (Exception e) {
            if (id == null) { showError(L.t("YouTube на телефоне не установлен.")); return; }
            try { startActivity(i.setPackage(null)); } catch (Exception ignored) {}
        }
    }

    private void copyLink() {
        Cast.Status v = view();
        if (v == null || v.contentId == null) return;
        String url = "https://youtu.be/" + v.contentId + "?t=" + (int) v.elapsedNow();
        android.content.ClipboardManager cb = getSystemService(android.content.ClipboardManager.class);
        if (cb != null) cb.setPrimaryClip(android.content.ClipData.newPlainText("YouTube", url));
        say(L.t("Ссылка с текущим моментом скопирована."));
    }

    private static String speedLabel(double r) {
        String n = r == Math.rint(r) ? String.valueOf((int) r) : String.valueOf(r).replace('.', ',');
        return n + "×";
    }

    private void askSpeed() {
        final double[] rates = {0.5, 0.75, 1, 1.25, 1.5, 2};
        String[] labels = new String[rates.length + 1];
        Runnable[] acts = new Runnable[rates.length + 1];
        for (int i = 0; i < rates.length; i++) {
            final double r = rates[i];
            labels[i] = speedLabel(r);
            acts[i] = () -> {
                if (ytOnTv(view()) && ytExact()) { ytDo(() -> yt.speed(r)); xSpeed.setText(speedLabel(r)); }
                else { cast.setRate(r); st = cast.status(); sync(); }
            };
        }
        labels[rates.length] = L.t("Отмена");
        ui.ask(this, L.t("Скорость"), null, null, labels, acts);
    }

    /** The TV's own volume (0..100 on the slider) over ADB: absolute, works for any app. */
    private void setTvVolume(int pct) {
        final String h = host;
        if (h == null) return;
        bg(() -> {
            try {
                int max = tvVolMax;
                if (max <= 0) {                                      // the range, once
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("range \\[0\\.\\.(\\d+)\\]")
                            .matcher(adb.shell(h, "cmd media_session volume --stream 3 --get"));
                    if (!m.find()) return;
                    max = tvVolMax = Integer.parseInt(m.group(1));
                }
                int level = Math.round(pct * max / 100f);
                tvVol = level;
                adb.shell(h, "cmd media_session volume --stream 3 --set " + level);
            } catch (Exception ignored) {}
        });
    }

    private final Runnable sendVolume = () -> {
        if (volPending < 0) return;
        if (adbOk() && !isSamsung()) setTvVolume(volPending);                  // the TV's volume, whatever plays
        else { cast.setVolume(volPending); st = cast.status(); }
        volPending = -1;
    };

    @Override public boolean onKeyDown(int code, KeyEvent e) {
        if ((code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN) && host != null && volKeys()) {
            boolean up = code == KeyEvent.KEYCODE_VOLUME_UP;
            if (paired() || adbOk()) tvKey(up ? Atv.VOL_UP : Atv.VOL_DOWN);
            else if (st != null && st.volume >= 0) {
                int v = Math.max(0, Math.min(100, (volPending >= 0 ? volPending : st.volume) + (up ? 5 : -5)));
                volPending = v;
                castVol.set(v);
                castVolNum.setText(String.valueOf(v));
                main.removeCallbacks(sendVolume);
                main.postDelayed(sendVolume, 150);
            } else return super.onKeyDown(code, e);
            return true;
        }
        return super.onKeyDown(code, e);
    }

    @Override public boolean onKeyUp(int code, KeyEvent e) {
        if ((code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN) && host != null && volKeys()
                && (paired() || adbOk() || (st != null && st.volume >= 0))) return true;
        return super.onKeyUp(code, e);
    }

    private boolean volKeys() { return prefs.getBoolean("volKeys", true); }

    // ================================================================ voice

    private void toggleVoice() {
        if (voice) { stopVoice(); return; }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        atv.voiceBegin(host);
    }

    @Override public void onRequestPermissionsResult(int req, String[] perms, int[] res) {
        if (req == REQ_NOTIFY) return;   // without it the playback controls just stay hidden
        if (req == REQ_MIC && res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED) atv.voiceBegin(host);
        else if (req == REQ_MIC) showError(L.t("Без доступа к микрофону голосовой поиск не работает."));
    }

    private void startRecording() {
        if (recording) return;
        try {
            int min = AudioRecord.getMinBufferSize(8000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 8000, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, Math.max(min, 8192 * 2));
            recorder.startRecording();
        } catch (SecurityException | IllegalStateException ex) {
            showError(L.t("Микрофон недоступен."));
            atv.voiceEnd();
            return;
        }
        recording = true;
        final AudioRecord r = recorder;
        // The mic thread owns the recorder: it stops and releases it after its last read(),
        // so release() never races a read() still running on this thread.
        Thread t = new Thread(() -> {
            byte[] buf = new byte[8192];
            try {
                while (recording) {
                    int off = 0;
                    while (recording && off < buf.length) {
                        int n = r.read(buf, off, buf.length - off);
                        if (n < 0) { recording = false; break; }   // recorder error: stop, don't spin
                        if (n == 0) break;
                        off += n;
                    }
                    if (off > 0) atv.voiceChunk(buf, off);
                }
            } finally {
                try { r.stop(); } catch (Exception ignored) {}
                r.release();
            }
        }, "mic");
        t.start();
    }

    private void stopRecording() {
        recording = false;   // the mic thread sees this, sends its last chunk and releases the recorder
        recorder = null;
    }

    private void stopVoice() {
        stopRecording();
        atv.voiceEnd();
    }

    // ================================================================ images / oEmbed

    interface StrCb { void on(String s); }

    private void fetchYoutubeTitle(String id, StrCb cb) {
        if (ytTitles.containsKey(id)) { cb.on(ytTitles.get(id)); return; }
        bg(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=" + id).openConnection();
                c.setConnectTimeout(5000);
                c.setReadTimeout(5000);
                try (InputStream in = c.getInputStream()) {
                    JSONObject o = new JSONObject(new String(Cast.readAll(in), "UTF-8"));
                    String t = o.optString("title", null);
                    String a = o.optString("author_name", null);
                    main.post(() -> {
                        ytTitles.put(id, t);
                        if (a != null) ytTitles.put(id + "#author", a);
                        cb.on(t);
                    });
                }
            } catch (Exception ignored) {}
        });
    }

    private void loadImage(String url, Ui.Artwork into) {
        if (url == null) { into.url = null; into.setBitmap(null); return; }
        if (url.equals(into.url)) return;
        into.url = url;
        Bitmap cached = images.get(url);
        into.setBitmap(cached);
        if (cached != null) return;
        bg(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(6000);
                c.setReadTimeout(8000);
                try (InputStream in = c.getInputStream()) {
                    Bitmap b = BitmapFactory.decodeStream(in);
                    if (b != null) main.post(() -> {
                        images.put(url, b);
                        if (url.equals(into.url)) into.setBitmap(b);
                    });
                }
            } catch (Exception ignored) {}
        });
    }

    // ================================================================ UI construction

    private void buildUi() {
        getWindow().setStatusBarColor(0);
        getWindow().setNavigationBarColor(0);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().getDecorView();   // the insets controller needs the decor to exist
            getWindow().setDecorFitsSystemWindows(false);
            getWindow().getDecorView();                  // creates the decor: no controller before it (Android 17 crashed)
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                c.setSystemBarsAppearance(ui.dark ? 0 : light, light);
            }
        } else {
            View d = getWindow().getDecorView();
            int f = View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION;
            if (!ui.dark) f |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            d.setSystemUiVisibility(f);
        }

        getWindow().setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(ui.page));
        scroll = new ScrollView(this);
        scroll.setBackgroundColor(ui.page);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        root = ui.col();
        root.setPadding(ui.dp(14), ui.dp(10), ui.dp(14), ui.dp(20));
        // phones get the full width, tablets a centered column like the panel
        FrameLayout center = new FrameLayout(this);
        FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL);
        center.addView(root, cp);
        scroll.addView(center);
        root.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> {
            int max = ui.dp(480);
            if (r - l > max && v.getLayoutParams().width != max) { v.getLayoutParams().width = max; v.requestLayout(); }
        });
        FrameLayout frame = new FrameLayout(this);
        frame.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        notice = ui.text("", 14, ui.fg, false);
        notice.setLineSpacing(0, 1.15f);
        notice.setCompoundDrawablePadding(ui.dp(10));
        notice.setGravity(Gravity.CENTER_VERTICAL);
        notice.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(12));
        notice.setElevation(ui.dp(6));
        notice.setVisibility(View.GONE);
        notice.setOnClickListener(v -> hideNotice.run());
        FrameLayout.LayoutParams np = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        frame.addView(notice, np);
        setContentView(frame);
        frame.setOnApplyWindowInsetsListener((v, ins) -> {
            int top, bottom, left, right;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets i = ins.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                top = i.top; bottom = i.bottom; left = i.left; right = i.right;
            } else {
                top = ins.getSystemWindowInsetTop(); bottom = ins.getSystemWindowInsetBottom();
                left = ins.getSystemWindowInsetLeft(); right = ins.getSystemWindowInsetRight();
            }
            scroll.setPadding(left, top, right, bottom);
            int side = ui.dp(14);
            np.setMargins(left + side, 0, right + side, bottom + ui.dp(14));
            np.width = getResources().getConfiguration().screenWidthDp > 520 ? ui.dp(480) : -1;   // tablets: the column's width
            notice.setLayoutParams(np);
            return Build.VERSION.SDK_INT >= 30 ? WindowInsets.CONSUMED : ins.consumeSystemWindowInsets();
        });

        // header
        LinearLayout header = ui.row();
        header.setPadding(ui.dp(6), ui.dp(6), ui.dp(4), ui.dp(14));   // no in-app logo: the title lines up with the cards
        LinearLayout ht = ui.col();
        ht.setPadding(0, 0, ui.dp(12), 0);
        TextView title = ui.text("Fling", 26, ui.fg, true);
        ht.addView(title);
        subtitleHeader = ui.text(L.t("Пульт и трансляция на телевизор"), 13, ui.fgA(0.5f), false);
        subtitleHeader.setPadding(0, ui.dp(3), 0, 0);
        ht.addView(subtitleHeader);
        pathChip = ui.text("", 14, ui.fgA(0.8f), true);                 // ● TV name: green / red / grey
        pathChip.setSingleLine(true);
        pathChip.setEllipsize(TextUtils.TruncateAt.END);
        pathChip.setGravity(Gravity.CENTER_VERTICAL);
        pathChip.setCompoundDrawablePadding(ui.dp(8));
        pathChip.setPadding(0, ui.dp(4), ui.dp(8), ui.dp(4));
        LinearLayout.LayoutParams pc = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        pc.topMargin = ui.dp(6);
        pathChip.setOnClickListener(v -> {                              // the dot in words, and a fresh check
            if (host == null) return;
            say(pathLabel());
            main.removeCallbacks(probe); main.post(probe);
        });
        ht.addView(pathChip, pc);
        header.addView(ht, Ui.lpW(1));
        liveKey = ui.text("● LIVE", 13, ui.red, true);                // same name as the desktop's LIVE
        liveKey.setGravity(Gravity.CENTER);
        liveKey.setPadding(ui.dp(12), 0, ui.dp(12), 0);
        liveKey.setBackground(ui.rippleRound(Ui.alpha(ui.red, 0.12f), 18));
        liveKey.setContentDescription(L.t("Экран ТВ на телефоне"));
        liveKey.setOnClickListener(v -> openLive());
        header.addView(liveKey, ui.margins(Ui.lp(-2, ui.dp(36)), 0, 0, 8, 0));
        TextView help = ui.text("?", 20, ui.onTonal, true);          // opens the "How to use" guide
        help.setGravity(Gravity.CENTER);
        help.setBackground(ui.rippleOval(ui.tonal));
        help.setContentDescription(L.t("Как пользоваться"));
        help.setOnClickListener(v -> startTour());
        helpView = help;
        header.addView(help, new LinearLayout.LayoutParams(ui.dp(36), ui.dp(36)));
        View gear = ui.key("gear", null, 36, false, v -> startActivity(new Intent(this, SettingsActivity.class)));
        gear.setContentDescription(L.t("Настройки"));
        gearView = gear;
        header.addView(gear, ui.margins(Ui.lp(ui.dp(36), ui.dp(36)), 8, 0, 0, 0));
        root.addView(header);

        buildNowCard();
        buildLinkCard();
        buildTvCard();
        buildFooter();
        root.removeView(tvCard);                                     // the remote: right under the header
        root.addView(tvCard, root.indexOfChild(header) + 1);
    }

    private void addCard(LinearLayout c) { root.addView(c, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 14)); }

    private void buildNowCard() {
        nowCard = ui.card();
        nowCard.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(8));
        nowArt = ui.new Artwork();
        nowRefresh = ui.new RefreshButton(() -> {
            if (host != null) cast.refresh();
            adbNext = 0; pollAdb();
            nowArt.url = null;   // reload artwork on next sync
            main.postDelayed(() -> { sync(); nowRefresh.done(); }, 700);
        });
        nowCard.addView(ui.artFrame(nowArt, nowRefresh), Ui.lp(-1, -2));
        nowTitle = ui.text("", 17, ui.fg, true);
        nowTitle.setSingleLine(true);                                  // one running line, not two cut ones
        nowTitle.setEllipsize(TextUtils.TruncateAt.MARQUEE);
        nowTitle.setMarqueeRepeatLimit(-1);
        nowTitle.setHorizontallyScrolling(true);
        nowTitle.setSelected(true);
        nowCard.addView(nowTitle, ui.margins(Ui.lp(-1, -2), 4, 12, 4, 0));
        nowSub = ui.text("", 14, ui.fgA(0.5f), false);
        nowCard.addView(nowSub, ui.margins(Ui.lp(-1, -2), 4, 4, 4, 0));
        nowProg = ui.new Slider();
        nowProg.listener = (v, user) -> {
            if (!user) return;
            Cast.Status cs = view();
            if (cs != null && cs.duration > 0) nowEl.setText(clock(cs.duration * v / 100.0));   // where it will go
            main.removeCallbacks(seekNow);
            main.postDelayed(seekNow, 450);                              // after the finger rests
        };
        nowCard.addView(nowProg, ui.margins(Ui.lp(-1, ui.dp(28)), 0, 8, 0, 0));
        LinearLayout times = ui.row();
        nowEl = ui.text("0:00", 12, ui.fgA(0.35f), false);
        nowEl.setFontFeatureSettings("tnum");
        nowRem = ui.text("", 12, ui.fgA(0.35f), false);
        nowRem.setFontFeatureSettings("tnum");
        times.addView(nowEl);
        times.addView(ui.spacer());
        times.addView(nowRem);
        nowCard.addView(times, ui.margins(Ui.lp(-1, -2), 4, 6, 4, 0));

        LinearLayout ctl = ui.row();
        ctl.setGravity(Gravity.CENTER);
        ctl.setPadding(0, ui.dp(10), 0, ui.dp(6));
        View back = seekBack = ui.key("back30", null, 50, false, v -> seek(-SEEK));
        playKey = (FrameLayout) ui.key("pause", null, 64, true, v -> togglePlay());
        playIcon = (ImageView) playKey.getChildAt(0);
        View fwd = seekFwd = ui.key("fwd30", null, 50, false, v -> seek(SEEK));
        // previous / next: only for a queue of phone files
        prevKey = ui.key("prev", null, 42, false, v -> {
            if (inQueue()) cast.queueJump(-1);
            else if (ytOnTv(view()) && ytExact()) ytDo(() -> yt.previous());
            else tvKey(Atv.PREV);
        });
        prevKey.setContentDescription(L.t("Предыдущий"));
        nextKey = ui.key("next", null, 42, false, v -> {
            if (inQueue()) cast.queueJump(1);
            else if (ytOnTv(view()) && ytExact()) ytDo(() -> yt.next());
            else tvKey(Atv.NEXT);
        });
        nextKey.setContentDescription(L.t("Следующий"));
        ctl.addView(prevKey, ui.margins(Ui.lp(ui.dp(42), ui.dp(42)), 0, 0, 12, 0));
        ctl.addView(back);
        gap1 = new View(this); ctl.addView(gap1, Ui.lp(ui.dp(28), 1));
        ctl.addView(playKey);
        gap2 = new View(this); ctl.addView(gap2, Ui.lp(ui.dp(28), 1));
        ctl.addView(fwd);
        ctl.addView(nextKey, ui.margins(Ui.lp(ui.dp(42), ui.dp(42)), 12, 0, 0, 0));
        nowCard.addView(ctl);

        // more: continue on the phone, the link, speed, audio track, from the start
        LinearLayout extra = ui.row();
        extra.setGravity(Gravity.CENTER);
        xPhone = ui.chip(L.t("На телефоне"), v -> openOnPhone());
        xLink = ui.chip(L.t("Ссылка"), v -> copyLink());
        xSpeed = ui.chip("1×", v -> askSpeed());
        xAudio = ui.chip(L.t("Дорожка"), v -> tvKey(Atv.AUDIO));
        xRestart = ui.chip(L.t("С начала"), v -> { cast.seekTo(0); st = cast.status(); sync(); });
        for (TextView t : new TextView[]{xPhone, xLink, xSpeed, xAudio, xRestart}) {
            t.setPadding(ui.dp(10), ui.dp(7), ui.dp(10), ui.dp(7));
            extra.addView(t, ui.margins(Ui.lp(-2, -2), 3, 0, 3, 0));
        }
        android.widget.HorizontalScrollView xs = new android.widget.HorizontalScrollView(this);
        xs.setHorizontalScrollBarEnabled(false);
        xs.addView(extra);
        nowCard.addView(xs, ui.margins(Ui.lp(-1, -2), 0, 2, 0, 8));

        nowHint = ui.text("", 13, ui.fgA(0.6f), false);
        nowHint.setLineSpacing(0, 1.15f);
        nowCard.addView(nowHint, ui.margins(Ui.lp(-1, -2), 4, 2, 4, 6));

        LinearLayout vol = ui.row();
        vol.setPadding(ui.dp(4), 0, ui.dp(4), 0);
        ccKey = ui.key(null, "CC", 36, false, v -> tvKey(Atv.CAPTIONS));   // subtitles on / off in the TV app
        ccKey.setContentDescription(L.t("Субтитры"));
        vol.addView(ccKey, ui.margins(Ui.lp(ui.dp(36), ui.dp(36)), 0, 0, 10, 0));
        vol.addView(ui.icon("spk_wave", ui.fgA(0.5f), 18));
        castVol = ui.new Slider();
        castVol.listener = (v, user) -> {
            castVolNum.setText(String.valueOf(v));
            volPending = v;
            main.removeCallbacks(sendVolume);
            main.postDelayed(sendVolume, 180);
        };
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(0, ui.dp(32), 1f);
        sp.setMargins(ui.dp(10), 0, ui.dp(10), 0);
        vol.addView(castVol, sp);
        castVolNum = ui.text("", 12, ui.fgA(0.35f), false);
        castVolNum.setFontFeatureSettings("tnum");
        castVolNum.setMinWidth(ui.dp(24));
        castVolNum.setGravity(Gravity.END);
        vol.addView(castVolNum);
        nowCard.addView(vol);

        nowCard.addView(ui.separator());
        nowCard.addView(ui.row(L.t("Остановить трансляцию"), "stop", null, false, v -> cast.stop()));
        addCard(nowCard);
    }

    private void buildLinkCard() {
        linkCard = ui.card();
        linkCard.setPadding(ui.dp(6), ui.dp(12), ui.dp(6), ui.dp(6));
        TextView lbl = ui.label(L.t("Ссылка"));
        linkCard.addView(lbl, ui.margins(Ui.lp(-1, -2), 10, 0, 10, 10));
        linkArt = ui.new Artwork();
        linkRefresh = ui.new RefreshButton(this::refreshLink);
        linkArtFrame = ui.artFrame(linkArt, linkRefresh);
        linkCard.addView(linkArtFrame, ui.margins(Ui.lp(-1, -2), 6, 0, 6, 10));
        linkTitleView = ui.text("", 16, ui.fg, true);
        linkTitleView.setMaxLines(2);
        linkTitleView.setEllipsize(TextUtils.TruncateAt.END);
        linkCard.addView(linkTitleView, ui.margins(Ui.lp(-1, -2), 10, 0, 10, 0));
        LinearLayout hk = ui.row();
        linkHost = ui.text("", 13, ui.fgA(0.5f), false);
        TextView dot = ui.text("  ·  ", 13, ui.fgA(0.5f), false);
        linkKind = ui.text("", 13, ui.green, false);
        hk.addView(linkHost); hk.addView(dot); hk.addView(linkKind);
        linkCard.addView(hk, ui.margins(Ui.lp(-1, -2), 10, 4, 10, 4));
        linkEmpty = ui.text(L.t("Поделитесь ссылкой из YouTube или браузера → «На телевизор» — и видео сразу начнётся на ТВ. Или скопируйте ссылку и вставьте её здесь."),
                14, ui.fgA(0.55f), false);
        linkEmpty.setLineSpacing(0, 1.2f);
        linkCard.addView(linkEmpty, ui.margins(Ui.lp(-1, -2), 10, 0, 10, 6));
        linkCard.addView(ui.separator());
        linkCastRow = ui.row(L.t("Отправить на ТВ"), "tv_play", null, true, v -> { if (link != null) castLink(link, linkTitle); });
        linkCard.addView(linkCastRow);
        linkCard.addView(ui.row(L.t("Вставить ссылку из буфера"), "clipboard", null, false, v -> pasteLink()));
        linkCard.addView(ui.row(L.t("Файл с телефона"), "phone", null, false, v -> pickFiles()));
        linkCard.addView(ui.row(L.t("Сделать снимок"), "camera", null, false, v -> tvScreenshot()));
        recentBox = ui.col();
        linkCard.addView(recentBox);
        addCard(linkCard);
    }

    /** Re-fetch the link's oEmbed title and thumbnail (and the TV status). */
    private void refreshLink() {
        if (host != null) cast.refresh();
        String id = youtubeId(link);
        if (id == null) { main.postDelayed(linkRefresh::done, 600); return; }
        ytTitles.remove(id);
        ytTitles.remove(id + "#author");
        String img = "https://img.youtube.com/vi/" + id + "/hqdefault.jpg";
        images.remove(img);
        linkArt.url = null;
        loadImage(img, linkArt);
        final String url = link;
        fetchYoutubeTitle(id, t -> { if (url.equals(link)) { linkTitle = t; sync(); } linkRefresh.done(); });
        main.postDelayed(linkRefresh::done, 6000);   // safety: stop spinning if offline
    }

    private void pasteLink() {
        ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        ClipData cd = cm == null ? null : cm.getPrimaryClip();
        CharSequence t = cd != null && cd.getItemCount() > 0 ? cd.getItemAt(0).coerceToText(this) : null;
        String url = t == null ? null : extractUrl(t.toString());
        if (url == null) { showError(L.t("В буфере обмена нет ссылки.")); return; }
        setLink(url, null);
        sync();
    }

    private void buildTvCard() {
        tvCard = ui.card();
        tvCard.setPadding(ui.dp(6), ui.dp(12), ui.dp(6), ui.dp(10));

        // starting
        startingBox = ui.row();
        startingBox.setPadding(ui.dp(10), ui.dp(6), ui.dp(10), ui.dp(6));
        android.widget.ProgressBar pb = new android.widget.ProgressBar(this);
        startingBox.addView(pb, Ui.lp(ui.dp(20), ui.dp(20)));
        TextView st1 = ui.text(L.t("Связываюсь с ТВ…"), 15, ui.fgA(0.55f), false);
        st1.setPadding(ui.dp(10), 0, 0, 0);
        startingBox.addView(st1);
        tvCard.addView(startingBox);

        // pin entry
        pinBox = ui.col();
        pinBox.setPadding(ui.dp(10), ui.dp(4), ui.dp(10), ui.dp(4));
        pinBox.addView(ui.text(L.t("Введите код с экрана телевизора"), 15, ui.fgA(0.6f), false));
        LinearLayout pr = ui.row();
        pr.setPadding(0, ui.dp(10), 0, 0);
        pinEdit = ui.field("A1B2C3", true);
        pinEdit.setFilters(new InputFilter[]{new InputFilter.AllCaps(), new InputFilter.LengthFilter(6)});
        pinEdit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        pinEdit.setLetterSpacing(0.3f);
        pinEdit.setTextSize(20);
        pinEdit.setImeOptions(EditorInfo.IME_ACTION_DONE);
        pinEdit.setOnEditorActionListener((v, a, e) -> { submitPin(); return true; });
        pinEdit.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            public void onTextChanged(CharSequence s, int a, int b, int c) {}
            public void afterTextChanged(android.text.Editable s) { if (s.length() == 6 && pairState.equals("pin")) submitPin(); sync(); }
        });
        pr.addView(pinEdit, Ui.lpW(1));
        pinOk = ui.text(L.t("Связать"), 15, ui.accent, true);
        pinOk.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(12));
        pinOk.setBackground(ui.rippleRound(ui.accA(0.12f), 10));
        pinOk.setOnClickListener(v -> submitPin());
        pr.addView(pinOk, ui.margins(Ui.lp(-2, -2), 8, 0, 0, 0));
        pinBox.addView(pr);
        pinBox.addView(ui.row(L.t("Отмена"), null, null, false, v -> atv.cancelPairing()), ui.margins(Ui.lp(-1, -2), -12, 6, -12, 0));
        tvCard.addView(pinBox);

        // setup (unpaired)
        setupBox = ui.col();
        TextView lbl = ui.label(L.t("Телевизор"));
        setupBox.addView(lbl, ui.margins(Ui.lp(-1, -2), 10, 0, 10, 8));
        TextView sx = ui.text(L.t("Один раз свяжите телефон с ТВ, чтобы открыть приложения, стрелки, ввод текста, голосовой поиск и отправку YouTube-ссылок."), 14, ui.fgA(0.55f), false);
        sx.setLineSpacing(0, 1.2f);
        setupBox.addView(sx, ui.margins(Ui.lp(-1, -2), 10, 0, 10, 6));
        Ui.Row pairRow = ui.row(L.t("Связать с ТВ"), "tv_plus", null, true, v -> {
            if (host == null) { showError(L.t("Телевизор ещё не найден.")); return; }
            atv.startPairing(host);
        });
        pairRow.setIconColor(ui.green);
        setupBox.addView(pairRow);
        tvCard.addView(setupBox);

        pinFail = ui.text("", 14, ui.red, false);
        tvCard.addView(pinFail, ui.margins(Ui.lp(-1, -2), 10, 4, 10, 4));

        // paired controls (RemoteView.swift)
        ctlBox = ui.col();
        LinearLayout hdr = ui.row();
        hdr.setPadding(ui.dp(10), 0, ui.dp(6), ui.dp(10));
        LinearLayout hv = ui.col();
        hv.addView(ui.label(L.t("На телевизоре")));
        tvNow = ui.text("", 17, ui.fg, true);
        tvNow.setPadding(0, ui.dp(4), 0, 0);
        hv.addView(tvNow);
        hdr.addView(hv, Ui.lpW(1));
        hdr.addView(ui.key("power", null, 40, false, v -> tvKey(Atv.POWER)));
        ctlBox.addView(hdr);

        // app grid, 4 per row: the user's own per TV (long press a tile, "+" adds one)
        appGrid = ui.col();
        appGrid.setPadding(ui.dp(6), 0, ui.dp(6), ui.dp(10));
        buildApps();
        ctlBox.addView(appGrid);

        LinearLayout segRow = ui.row();
        segRow.setGravity(Gravity.CENTER);
        segRow.addView(ui.segmented(new String[]{L.t("Кнопки"), L.t("Тачпад")}, padMode, idx -> {
            padMode = idx;
            prefs.edit().putInt("padMode", idx).apply();
            sync();
        }), Ui.lp(ui.dp(220), -2));
        ctlBox.addView(segRow, ui.margins(Ui.lp(-1, -2), 0, 4, 0, 8));

        // d-pad
        LinearLayout dp = ui.col();
        dp.setGravity(Gravity.CENTER_HORIZONTAL);
        dp.setPadding(0, ui.dp(6), 0, ui.dp(6));
        dp.addView(ui.key("up", null, 52, false, v -> tvKey(Atv.UP)));
        LinearLayout mid = ui.row();
        mid.setGravity(Gravity.CENTER);
        mid.setPadding(0, ui.dp(8), 0, ui.dp(8));
        mid.addView(ui.key("back", null, 46, false, v -> tvKey(Atv.BACK)));
        mid.addView(new View(this), Ui.lp(ui.dp(14), 1));
        mid.addView(ui.key("left", null, 52, false, v -> tvKey(Atv.LEFT)));
        mid.addView(new View(this), Ui.lp(ui.dp(10), 1));
        mid.addView(ui.key(null, "OK", 70, true, v -> tvKey(Atv.OK)));
        mid.addView(new View(this), Ui.lp(ui.dp(10), 1));
        mid.addView(ui.key("right", null, 52, false, v -> tvKey(Atv.RIGHT)));
        mid.addView(new View(this), Ui.lp(ui.dp(14), 1));
        mid.addView(ui.key("home", null, 46, false, v -> tvKey(Atv.HOME)));
        dp.addView(mid);
        dp.addView(ui.key("down", null, 52, false, v -> tvKey(Atv.DOWN)));
        dpadBox = dp;
        ctlBox.addView(dp);
        padBox = ui.new Touchpad(code -> tvKey(code));
        ctlBox.addView(padBox);

        // volume: round keys with just the symbol, like a remote's
        LinearLayout vk = ui.row();
        vk.setGravity(Gravity.CENTER);
        vk.setPadding(ui.dp(6), ui.dp(12), ui.dp(6), ui.dp(4));
        String[][] vol = {{"spk_minus", "Тише"}, {"spk_slash", "Без звука"}, {"spk_plus", "Громче"}};
        int[] codes = {Atv.VOL_DOWN, Atv.MUTE, Atv.VOL_UP};
        for (int i = 0; i < 3; i++) {
            final int code = codes[i];
            View k = ui.key(vol[i][0], null, 56, false, v -> { v.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP); tvKey(code); });
            k.setContentDescription(L.t(vol[i][1]));
            LinearLayout.LayoutParams kp = Ui.lp(ui.dp(56), ui.dp(56));
            kp.setMargins(ui.dp(i == 0 ? 0 : 14), 0, ui.dp(i == 2 ? 0 : 14), 0);
            vk.addView(k, kp);
        }
        ctlBox.addView(vk);
        TextView hw = ui.text(L.t("Кнопки громкости телефона тоже управляют ТВ"), 12, ui.fgA(0.35f), false);
        hw.setGravity(Gravity.CENTER);
        hwHint = hw;
        ctlBox.addView(hwHint, ui.margins(Ui.lp(-1, -2), 0, 2, 0, 6));
        sleepRow = ui.row(L.t("Таймер сна"), "moon", "", false, this::showSleepMenu);
        ctlBox.addView(sleepRow);

        // type + mic
        LinearLayout ty = ui.row();
        ty.setPadding(ui.dp(6), ui.dp(6), ui.dp(6), 0);
        typeEdit = ui.field(L.t("Напечатать на ТВ"), false);
        typeEdit.setImeOptions(EditorInfo.IME_ACTION_SEND);
        typeEdit.setOnEditorActionListener((v, a, e) -> {
            String t = typeEdit.getText().toString();
            typeEdit.setText("");
            if (!t.isEmpty()) atv.text(host, t);
            return true;
        });
        ty.addView(typeEdit, Ui.lpW(1));
        micKey = (FrameLayout) ui.key("mic", null, 44, false, v -> toggleVoice());
        micIcon = (ImageView) micKey.getChildAt(0);
        ty.addView(micKey, ui.margins(Ui.lp(ui.dp(44), ui.dp(44)), 8, 0, 0, 0));
        ctlBox.addView(ty);
        listening = ui.text(L.t("Слушаю — нажмите микрофон ещё раз, когда закончите."), 13, ui.red, false);
        ctlBox.addView(listening, ui.margins(Ui.lp(-1, -2), 10, 8, 10, 0));
        tvCard.addView(ctlBox);

        // Samsung (Smart View): mirror the phone's screen, turn the TV on, play/pause/stop over DLNA
        samBox = ui.col();
        samBox.setPadding(0, ui.dp(4), 0, ui.dp(4));
        Ui.Row mirror = ui.row(L.t("Зеркалировать экран"), "tv_play", null, true, v -> {
            if (!Samsung.openMirroring(this)) showError(L.t("На этом телефоне нет экрана трансляции. Откройте Smart View в шторке быстрых настроек."));
            else toast(L.t("Выберите телевизор в списке «Smart View»"));
        });
        samBox.addView(mirror);
        samBox.addView(ui.row(L.t("Включить ТВ по сети"), "power", null, false, v -> sam.wake(host)));
        LinearLayout tr = ui.row();
        tr.setPadding(ui.dp(10), ui.dp(4), ui.dp(10), ui.dp(4));
        tr.addView(ui.key("play", null, 42, false, v -> sam.transport(host, "Play")), Ui.lpW(1));
        tr.addView(ui.key("pause", null, 42, false, v -> sam.transport(host, "Pause")), Ui.lpW(1));
        tr.addView(ui.key("stop", null, 42, false, v -> sam.transport(host, "Stop")), Ui.lpW(1));
        samBox.addView(tr);
        tvCard.addView(samBox);
        addCard(tvCard);
    }

    private void showSleepMenu(View anchor) {
        PopupMenu pm = new PopupMenu(this, sleepRow.trailing);
        int[] mins = {15, 30, 45, 60, 90, 120};
        for (int i = 0; i < mins.length; i++) pm.getMenu().add(0, mins[i], i, sleepLabel(mins[i]));
        if (SleepTimer.endsAt(this) > 0) pm.getMenu().add(0, 0, 99, L.t("Выключить таймер"));
        pm.setOnMenuItemClickListener(it -> {
            int m = it.getItemId();
            if (m == 0) { SleepTimer.cancel(this); toast(L.t("Таймер сна выключен")); }
            else { SleepTimer.set(this, host, m, isSamsung()); toast(L.f("ТВ выключится в %s", hhmm(SleepTimer.endsAt(this)))); }
            sync();
            return true;
        });
        pm.show();
    }

    private static String sleepLabel(int m) {
        if (m < 60) return L.f("Через %d мин", m);
        if (m == 60) return L.t("Через 1 час");
        if (m == 90) return L.t("Через 1,5 часа");
        return L.f("Через %d часа", m / 60);
    }

    private String hhmm(long t) { return android.text.format.DateFormat.getTimeFormat(this).format(new java.util.Date(t)); }

    private void submitPin() {
        String code = pinEdit.getText().toString().trim().toUpperCase(Locale.ROOT);
        if (code.length() == 6 && pairState.equals("pin")) atv.finishPairing(code);
    }

    private void buildFooter() {
        LinearLayout f = ui.card();
        LinearLayout r = ui.row();
        r.setPadding(ui.dp(12), ui.dp(6), ui.dp(4), ui.dp(6));
        devDot = new View(this);
        r.addView(devDot, Ui.lp(ui.dp(8), ui.dp(8)));
        devNameView = ui.text("", 15, ui.fg, false);
        devNameView.setPadding(ui.dp(10), 0, 0, 0);
        devNameView.setSingleLine(true);
        devNameView.setEllipsize(TextUtils.TruncateAt.END);
        r.addView(devNameView, Ui.lpW(1));
        TextView sw = ui.text(L.t("Сменить"), 15, ui.accent, true);
        sw.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10));
        sw.setBackground(ui.rippleRound(0, 10));
        sw.setOnClickListener(v -> openPicker());
        r.addView(sw);
        f.addView(r);
        root.addView(f, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 8));
        TextView hint = ui.text(L.t("Fling для Android · дома — по Wi‑Fi, вне дома — через Tailscale"), 12, ui.fgA(0.3f), false);
        hint.setGravity(Gravity.CENTER);
        root.addView(hint, ui.margins(Ui.lp(-1, -2), 0, 6, 0, 0));
        // authorship: Fling is Damion Rashford's and Tatiana's app; this is an unofficial port by orfixe
        TextView credit = ui.text(L.t("Оригинальный Fling создали Damion Rashford и Tatiana · неофициальный порт — orfixe"), 12, ui.fgA(0.45f), false);
        credit.setGravity(Gravity.CENTER);
        credit.setPadding(0, ui.dp(6), 0, ui.dp(6));
        credit.setOnClickListener(v -> {
            try { startActivity(new android.content.Intent(android.content.Intent.ACTION_VIEW,
                    android.net.Uri.parse("https://github.com/tatianathevisionary/fling"))); } catch (Exception ignored) {}
        });
        root.addView(credit, ui.margins(Ui.lp(-1, -2), 0, 2, 0, 0));
        // version + "check for updates" (Updater: newest GitHub release)
        TextView upd = BuildConfig.SELF_UPDATE
                ? ui.text(L.f("Версия %s · Проверить обновления", Updater.installedVersion(this)), 12, ui.accent, false)
                : ui.text(L.f("Версия %s", Updater.installedVersion(this)), 12, ui.fgA(0.45f), false);   // Play updates it
        upd.setGravity(Gravity.CENTER);
        upd.setPadding(0, ui.dp(10), 0, ui.dp(10));
        if (BuildConfig.SELF_UPDATE) upd.setOnClickListener(v -> updater.check(true));
        root.addView(upd, ui.margins(Ui.lp(-1, -2), 0, 4, 0, 0));
    }

    // ================================================================ first steps

    private android.app.Dialog setupDlg;
    private boolean welcomeShowing;
    /** The person chose the TV in the list (First steps waits for this, not for auto-discovery). */
    private boolean tvPicked;
    private int adbAsks;
    private Runnable setupTick;

    /** "First steps": on the first start, and again from Settings. One step at a time, each
     *  moving on by itself once it is done: the TV → network debugging (then the 3 KB helper
     *  installs itself) → pairing → YouTube by code (optional) → done. */
    private void maybeSetup() {
        if (prefs.getBoolean("setup_shown", false) || setupDlg != null || welcomeShowing) return;
        main.postDelayed(() -> {                                      // re-checked when it fires: the welcome may be up
            if (isFinishing() || isDestroyed() || welcomeShowing || setupDlg != null || prefs.getBoolean("setup_shown", false)) return;
            prefs.edit().putBoolean("setup_shown", true).apply();
            showSetup();
        }, 900);
    }

    /** The helper ran on the TV: there are app icons from it. */
    private boolean helperReady() {
        for (String[] a : apps()) if (TvIcons.icon(this, a[2]) != null) return true;
        return false;
    }

    private void showSetup() {
        final int TV = 0, ADB = 1, HELPER = 2, PAIR = 3, YT = 4, DONE = 5;
        final int[] step = {TV};
        final int[] shown = {-1};                                   // the step the field was last set up for
        final boolean[] skipped = new boolean[6];
        final android.app.Dialog d = new android.app.Dialog(this);
        d.requestWindowFeature(android.view.Window.FEATURE_NO_TITLE);
        LinearLayout box = ui.col();
        android.graphics.drawable.GradientDrawable bg = ui.round(ui.card, 28);
        if (ui.card == ui.page) bg.setStroke(Math.max(1, ui.dp(1)), ui.fgA(0.16f));
        box.setBackground(bg);
        box.setPadding(ui.dp(24), ui.dp(22), ui.dp(24), ui.dp(16));
        TextView progress = ui.label("");
        box.addView(progress);
        TextView title = ui.text("", 20, ui.fg, true);
        box.addView(title, ui.margins(Ui.lp(-1, -2), 0, 8, 0, 0));
        TextView body = ui.text("", 15, ui.fgA(0.75f), false);
        body.setLineSpacing(ui.dp(3), 1f);
        box.addView(body, ui.margins(Ui.lp(-1, -2), 0, 10, 0, 0));
        EditText code = ui.field("", true);
        box.addView(code, ui.margins(Ui.lp(-1, -2), 0, 14, 0, 0));
        TextView main1 = ui.text("", 15, ui.accent, true);
        main1.setGravity(Gravity.CENTER);
        main1.setMinHeight(ui.dp(48));
        main1.setBackground(ui.rippleRound(ui.accA(ui.dark ? 0.22f : 0.14f), 14));
        box.addView(main1, ui.margins(Ui.lp(-1, -2), 0, 18, 0, 0));
        TextView skip = ui.text(L.t("Пропустить шаг"), 14, ui.fgA(0.6f), false);
        skip.setGravity(Gravity.CENTER);
        skip.setMinHeight(ui.dp(44));
        box.addView(skip, ui.margins(Ui.lp(-1, -2), 0, 6, 0, 0));
        d.setContentView(box);
        android.view.Window w = d.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(0));
            w.setLayout(Math.min(getResources().getDisplayMetrics().widthPixels - ui.dp(32), ui.dp(420)),
                    ViewGroup.LayoutParams.WRAP_CONTENT);
        }

        final Runnable[] render = new Runnable[1];
        render[0] = () -> {
            int s = step[0];
            progress.setText(s < DONE ? L.f("Шаг %d из %d", s + 1, 5) : L.t("Готово"));
            code.setVisibility(View.GONE);
            if (shown[0] != s) {                                    // a new step: an empty field, its own keyboard
                shown[0] = s;
                code.setText("");
                code.setInputType(s == YT ? InputType.TYPE_CLASS_NUMBER
                        : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
            }
            skip.setVisibility(s == TV || s == DONE ? View.GONE : View.VISIBLE);
            main1.setVisibility(View.VISIBLE);
            switch (s) {
                case TV:
                    title.setText(L.t("Найдите телевизор"));
                    body.setText(L.t("Телефон и ТВ должны быть в одной сети Wi‑Fi. Fling найдёт ТВ сам — выберите его в списке."));
                    tvPicked = false;
                    main1.setText(L.t("Выбрать телевизор"));
                    main1.setOnClickListener(v -> openPicker());
                    break;
                case ADB:
                    title.setText(L.t("Включите отладку по сети"));
                    body.setText(adbAsks >= 2 && !"asking".equals(Adb.lastState)
                            ? L.t("Окно на ТВ так и не появилось? На ТВ выключите и снова включите «Отладка по сети» (или перезагрузите ТВ), затем нажмите «Проверить».")
                            : "asking".equals(Adb.lastState)
                            ? L.t("Смотрите на ТВ: «Разрешить отладку?» — отметьте «Всегда разрешать с этого компьютера» и нажмите «Разрешить».")
                            : "unauthorized".equals(Adb.lastState)
                            ? L.t("ТВ не получил разрешения. Нажмите «Проверить» — на ТВ снова появится запрос, у вас будет полторы минуты.")
                            : L.t("На ТВ: Настройки → Система → Об устройстве → 7 раз нажмите «Сборка ОС». Затем Настройки → Система → «Для разработчиков» → включите «Отладка по сети»."));
                    main1.setText(L.t("Проверить"));
                    main1.setOnClickListener(v -> {
                        adbAsks++;
                        adb.promptNow(host);                      // the TV asks again now, not in 5 minutes
                        Adb.lastState = "asking";
                        adbNext = 0; pollAdb();
                        render[0].run();
                    });
                    break;
                case HELPER:
                    title.setText(L.t("Ставлю помощник на ТВ"));
                    body.setText(L.t("3 КБ: Fling узнаёт названия и иконки приложений ТВ. Это займёт несколько секунд."));
                    main1.setVisibility(View.GONE);
                    fetchTileIcons(apps());
                    break;
                case PAIR:
                    title.setText(L.t("Свяжите пульт"));
                    boolean pin = pairState.equals("pin") || pairState.equals("verifying");
                    body.setText(pin ? L.t("Введите код с экрана ТВ.")
                            : pairState.equals("failed") && !pairMsg.isEmpty() ? pairMsg
                            : L.t("Один раз: на ТВ появится код из 6 символов. После этого работают текст, голос и пульт без ADB."));
                    code.setVisibility(pin ? View.VISIBLE : View.GONE);
                    code.setHint("A1B2C3");
                    main1.setText(pin ? L.t("Связать") : L.t("Показать код на ТВ"));
                    main1.setOnClickListener(v -> {
                        if (pin) atv.finishPairing(code.getText().toString().trim());
                        else atv.startPairing(host);
                    });
                    break;
                case YT:
                    title.setText(L.t("YouTube по коду — по желанию"));
                    body.setText(L.t("Точная перемотка YouTube и скорость. На ТВ: YouTube → Настройки → «Подключить телевизор по коду»."));
                    code.setVisibility(View.VISIBLE);
                    code.setHint("1234 5678 9012");
                    main1.setText(L.t("Подключить"));
                    main1.setOnClickListener(v -> {
                        String c = code.getText().toString();
                        bg(() -> {
                            try {
                                yt.pair(c);
                                prefs.edit().putString("yt_seek", "lounge").apply();
                            } catch (Exception e) {
                                main.post(() -> body.setText(L.t("Не получилось: ") + e.getMessage()));
                            }
                        });
                    });
                    break;
                default:
                    title.setText(L.t("Всё готово"));
                    StringBuilder b = new StringBuilder();
                    b.append(adbOk() ? "✓ " : "— ").append(L.t("Отладка по сети")).append('\n');
                    b.append(helperReady() ? "✓ " : "— ").append(L.t("Помощник на ТВ")).append('\n');
                    b.append(paired() ? "✓ " : "— ").append(L.t("Пульт связан")).append('\n');
                    b.append(ytExact() ? "✓ " : "— ").append("YouTube");
                    body.setText(b.toString());
                    main1.setText(L.t("Начать"));
                    main1.setOnClickListener(v -> d.dismiss());
            }
        };
        skip.setOnClickListener(v -> { skipped[step[0]] = true; step[0]++; render[0].run(); });
        setupTick = new Runnable() {
            @Override public void run() {
                int s = step[0];
                boolean done = s == TV ? tvPicked && host != null
                        : s == ADB ? adbOk()
                        : s == HELPER ? helperReady()
                        : s == PAIR ? paired()
                        : s == YT && ytExact();
                if (s == ADB && !done) render[0].run();             // "Allow debugging?" may have appeared
                if (s == PAIR && !done) {
                    String key = pairState + "|" + pairMsg;
                    if (!key.equals(body.getTag())) { body.setTag(key); render[0].run(); }
                }
                if (done && s < DONE) {
                    step[0]++;
                    if (step[0] == HELPER && isSamsung()) step[0] = DONE;   // Samsung: its own remote
                    render[0].run();
                }
                main.postDelayed(this, 1000);
            }
        };
        render[0].run();                                            // always from the TV list: step 1
        d.setOnDismissListener(x -> { main.removeCallbacks(setupTick); setupDlg = null; });
        setupDlg = d;
        d.show();
        setupTick.run();
        tvPicked = false;
        openPicker();                                               // the TVs first, on top; then the steps
    }

    /** Short tips, one at a time, over the real screen (Tour); the long guide stays one tap away. */
    private void startTour() {
        List<Tour.Step> steps = new ArrayList<>();
        steps.add(new Tour.Step((View) devNameView.getParent().getParent(), L.t("Ваш телевизор"),   // the whole footer card
                L.t("Fling сам находит ТВ в той же сети Wi‑Fi. «Сменить» — другой ТВ или добавить по IP.")));
        steps.add(new Tour.Step(setupBox, L.t("Связать с ТВ"),
                L.t("Нажмите, и на ТВ появится код из 6 символов. После этого работают пауза, приложения и стрелки.")));
        steps.add(new Tour.Step(ctlBox, L.t("Пульт"),
                L.t("Приложения, стрелки или тачпад, громкость, текст и голос. Кнопки громкости телефона тоже управляют ТВ.")));
        steps.add(new Tour.Step(linkCard, L.t("Отправить видео"),
                L.t("В YouTube или браузере: «Поделиться» → «На телевизор». Или скопируйте ссылку и вставьте её здесь.")));
        steps.add(new Tour.Step(nowCard, L.t("Что играет"),
                L.t("Пауза, ±30 секунд и громкость того, что идёт на ТВ.")));
        steps.add(new Tour.Step(liveKey, "LIVE",
                L.t("Экран ТВ на телефоне: смотрите и управляйте — дома и через Tailscale.")));
        steps.add(new Tour.Step(gearView, L.t("Настройки"),
                L.t("Тема, цвет акцента, язык, уведомление с кнопками и кнопки громкости.")));
        steps.add(new Tour.Step(helpView, L.t("Подсказки"),
                L.t("Эти подсказки всегда можно повторить кнопкой «?».")));
        Tour.start(this, ui, scroll, steps, () -> Help.show(this, ui));
    }

    // ================================================================ state → views

    static String clock(double t) {
        if (Double.isNaN(t) || t < 0) t = 0;
        long s = Math.round(t);
        long h = s / 3600, m = s % 3600 / 60, x = s % 60;
        return h > 0 ? String.format(Locale.ROOT, "%d:%02d:%02d", h, m, x) : String.format(Locale.ROOT, "%d:%02d", m, x);
    }

    private void updateTimes() {
        final Cast.Status st = view();
        if (st == null) return;
        double el = st.elapsedNow(), d = st.duration;
        nowEl.setText(clock(el));
        nowRem.setText(d > 0 && d > el ? "−" + clock(d - el) : "");
        nowProg.set(d > 0 ? (int) Math.round(el * 100 / d) : 0);
    }

    private static String appName(String pkg) {
        if (pkg == null) return null;
        String tile = tileNames.get(pkg);
        if (tile != null) return tile;
        String tv = TvIcons.peek(pkg);                              // as the TV itself calls it
        if (tv != null) return tv;
        for (String[] a : TV_APPS) if (a[2].equals(pkg)) return a[0];
        for (String[] a : KNOWN_APPS) if (a[0].equals(pkg)) return a[1];
        String tail = pkg.substring(pkg.lastIndexOf('.') + 1);
        return tail.isEmpty() ? pkg : Character.toUpperCase(tail.charAt(0)) + tail.substring(1);
    }

    private void sync() {
        if (root == null || destroyed) return;
        final Cast.Status st = view();
        boolean paired = paired();

        if (host == null) {
            pathChip.setText(L.t("Ищу телевизор в сети…"));
            pathChip.setCompoundDrawablesRelativeWithIntrinsicBounds(null, null, null, null);
            pathChip.setContentDescription(null);
        } else {
            pathChip.setText(nz(devName, host));
            int dot = reachable == null ? ui.fgA(0.3f) : reachable ? ui.green : ui.red;
            android.graphics.drawable.GradientDrawable d = ui.oval(dot);
            d.setSize(ui.dp(10), ui.dp(10));
            pathChip.setCompoundDrawablesRelativeWithIntrinsicBounds(d, null, null, null);
            pathChip.setContentDescription(nz(devName, host) + ". " + pathLabel());
        }
        liveKey.setAlpha(host != null && "ok".equals(Adb.lastState) ? 1f : 0.45f);

        // now playing
        boolean media = st != null && st.hasMedia();
        nowCard.setVisibility(media ? View.VISIBLE : View.GONE);
        if (media) {
            String cid = st.contentId == null ? "" : st.contentId;
            boolean yt = cid.matches("[\\w-]{11}");
            String img = st.imageUrl != null ? st.imageUrl : yt ? "https://img.youtube.com/vi/" + cid + "/hqdefault.jpg" : artFor(st.title, st.subtitle);
            loadImage(img, nowArt);
            String title = st.title;
            String sub = st.subtitle != null ? st.subtitle : st.appName;
            if (title == null && yt) {
                if (ytTitles.containsKey(cid)) {
                    title = ytTitles.get(cid);
                    String a = ytTitles.get(cid + "#author");
                    if (a != null) sub = a + " · YouTube";
                } else fetchYoutubeTitle(cid, t -> sync());
            }
            if (st.stuck()) {
                // frozen Cast session: its cover/title are from long ago — don't show them
                loadImage(null, nowArt);
                String app = tvApp != null ? appName(tvApp) : (st.appName != null ? st.appName : "YouTube");
                title = L.f("%s на ТВ", app);
                sub = L.t("ТВ не сообщает, что сейчас играет — показываю только приложение.");
            }
            nowTitle.setText(title != null ? title : L.t("Воспроизводится"));
            nowSub.setText(sub == null ? "" : sub);
            nowSub.setVisibility(sub == null ? View.GONE : View.VISIBLE);
            updateTimes();
            boolean stuck = st.stuck();
            String icon = stuck ? (paired ? "pause" : "pause") : st.playing() ? "pause" : "play";
            playIcon.setImageDrawable(new Ui.Glyph(icon, ui.accent, 2.0f));
            nowHint.setText(stuck && !paired ? L.t("ТВ не принимает команды по Chromecast для этого видео. Нажмите «Связать с ТВ» ниже — пауза и перемотка будут работать как кнопки пульта.") : "");
            nowHint.setVisibility(stuck && !paired ? View.VISIBLE : View.GONE);
            if (volPending < 0) {
                if (adbOk() && tvVolMax > 0 && tvVol >= 0 && !isSamsung()) {   // the TV's own volume
                    castVol.set(Math.round(tvVol * 100f / tvVolMax));
                    castVolNum.setText(String.valueOf(tvVol));
                } else if (st.volume >= 0) { castVol.set(st.volume); castVolNum.setText(String.valueOf(st.volume)); }
            }
            boolean queue = inQueue();
            boolean tvKeys = !queue && (tvMedia() || st.stuck()) && (paired() || adbOk());
            prevKey.setVisibility(queue || tvKeys ? View.VISIBLE : View.GONE);
            nextKey.setVisibility(queue || tvKeys ? View.VISIBLE : View.GONE);
            ccKey.setVisibility(tvKeys ? View.VISIBLE : View.GONE);
            boolean ytId = st.contentId != null && st.contentId.matches("[\\w-]{11}");
            boolean ytApp = ytId || (tvApp != null && tvApp.contains("youtube")) || "YouTube".equals(st.appName);
            boolean ours = !tvMedia() && !st.stuck();                          // Fling's own cast session
            xPhone.setVisibility(ytApp ? View.VISIBLE : View.GONE);
            xLink.setVisibility(ytId ? View.VISIBLE : View.GONE);
            boolean ytLinked = ytOnTv(st) && ytExact();
            xSpeed.setVisibility((ours && !ytApp) || ytLinked ? View.VISIBLE : View.GONE);   // a media receiver, or YouTube over Lounge
            nowProg.setEnabled(st.duration > 0 && (!ytOnTv(st) || ytLinked || !tvMedia()));    // keys can't hit a second
            xSpeed.setText(speedLabel(st.rate));
            xAudio.setVisibility(tvKeys ? View.VISIBLE : View.GONE);
            xRestart.setVisibility(ours && st.duration > 0 ? View.VISIBLE : View.GONE);

            // five keys need 300 dp: on narrower phones a queue shows previous / play / next only
            boolean seekKeys = !(queue || tvKeys) || getResources().getConfiguration().screenWidthDp >= 360;
            seekBack.setVisibility(seekKeys ? View.VISIBLE : View.GONE);
            seekFwd.setVisibility(seekKeys ? View.VISIBLE : View.GONE);
            int gap = ui.dp(queue || tvKeys ? (seekKeys ? 14 : 24) : 28);
            if (gap1.getLayoutParams().width != gap) { gap1.getLayoutParams().width = gap; gap2.getLayoutParams().width = gap; gap1.requestLayout(); gap2.requestLayout(); }
        }
        // media notification + lock-screen controls (CastService) while the TV plays a Cast session
        Cast.Status cs = this.st;
        if (resumed && host != null && cs != null && cs.hasMedia() && !CastService.running && prefs.getBoolean("notify", true)
                && !String.valueOf(cs.contentId).equals(CastService.dismissed)) {
            askNotifications();
            CastService.show(this, host);
        }

        // link
        boolean hasLink = link != null;
        linkArtFrame.setVisibility(hasLink && youtubeId(link) != null ? View.VISIBLE : View.GONE);
        linkTitleView.setVisibility(hasLink ? View.VISIBLE : View.GONE);
        ((View) linkHost.getParent()).setVisibility(hasLink ? View.VISIBLE : View.GONE);
        linkEmpty.setVisibility(hasLink ? View.GONE : View.VISIBLE);
        linkCastRow.setVisibility(hasLink ? View.VISIBLE : View.GONE);
        if (hasLink) {
            linkTitleView.setText(nz(linkTitle, link));
            linkHost.setText(hostOf(link));
            boolean direct = isDirectMedia(link), yt = youtubeId(link) != null;
            if (direct) { linkKind.setText(L.t("видеофайл · Chromecast")); linkKind.setTextColor(ui.green); }
            else if (yt) { linkKind.setText(paired ? L.t("откроется в YouTube на ТВ") : L.t("сначала «Связать с ТВ»")); linkKind.setTextColor(paired ? ui.green : ui.fgA(0.55f)); }
            else { linkKind.setText(paired ? L.t("откроется на ТВ") : L.t("сначала «Связать с ТВ»")); linkKind.setTextColor(ui.fgA(0.55f)); }
            linkCastRow.setEnabled(host != null && (direct || paired));
            linkCastRow.title.setText(media ? L.t("Отправить вместо текущего") : L.t("Отправить на ТВ"));
        }
        recentBox.removeAllViews();
        List<String[]> rec = recents();
        if (!rec.isEmpty()) {
            recentBox.addView(ui.separator());
            TextView l = ui.label(L.t("Недавние"));
            recentBox.addView(l, ui.margins(Ui.lp(-1, -2), 10, 4, 10, 4));
            for (String[] x : rec) {
                String t = x[1].isEmpty() ? x[0] : x[1];
                Ui.Row row = ui.row(t, "clock", hostOf(x[0]), false, v -> { setLink(x[0], x[1].isEmpty() ? null : x[1]); castLink(x[0], x[1].isEmpty() ? null : x[1]); });
                row.title.setTextSize(15);
                row.trailing.setTextSize(12);
                recentBox.addView(row);
            }
        }

        // tv
        boolean starting = pairState.equals("starting");
        boolean pin = pairState.equals("pin") || pairState.equals("verifying");
        boolean idleLike = pairState.equals("idle") || pairState.equals("failed");
        startingBox.setVisibility(starting ? View.VISIBLE : View.GONE);
        pinBox.setVisibility(pin ? View.VISIBLE : View.GONE);
        pinEdit.setEnabled(!pairState.equals("verifying"));
        pinOk.setText(pairState.equals("verifying") ? "…" : L.t("Связать"));
        pinOk.setAlpha(pinEdit.getText().length() == 6 ? 1f : 0.4f);
        setupBox.setVisibility(idleLike && !paired ? View.VISIBLE : View.GONE);
        ctlBox.setVisibility(idleLike && (paired || (adbOk() && !isSamsung())) ? View.VISIBLE : View.GONE);
        samBox.setVisibility(isSamsung() ? View.VISIBLE : View.GONE);
        pinFail.setText(pairMsg);
        pinFail.setVisibility(pairState.equals("failed") && !pairMsg.isEmpty() ? View.VISIBLE : View.GONE);
        if (paired) {
            String now = isSamsung()
                    ? (samState.equals("ready") ? "Samsung · Smart View"
                       : samState.equals("waiting") ? L.t("Нажмите «Разрешить» на экране ТВ")
                       : samState.equals("denied") ? L.t("ТВ не разрешил подключение")
                       : samState.equals("error") || samState.equals("offline") ? L.t("ТВ не отвечает") : L.t("Подключаюсь…"))
                    : tvApp != null ? L.f("Сейчас: %s", appName(tvApp)) : Boolean.FALSE.equals(tvOn) ? L.t("ТВ выключен") : tvOn == null ? L.t("Подключаюсь…") : L.t("Главный экран");
            tvNow.setText(now);
            for (TextView c : chips) ui.setChipActive(c, c.getTag().equals(tvApp));
            dpadBox.setVisibility(padMode == 0 ? View.VISIBLE : View.GONE);
            padBox.setVisibility(padMode == 1 ? View.VISIBLE : View.GONE);
            micIcon.setImageDrawable(new Ui.Glyph(voice ? "mic_fill" : "mic", voice ? ui.red : ui.onTonal, 2.0f));
            micKey.setBackground(ui.rippleOval(voice ? Ui.alpha(ui.red, 0.15f) : ui.tonal));
            listening.setVisibility(voice ? View.VISIBLE : View.GONE);
            hwHint.setVisibility(volKeys() ? View.VISIBLE : View.GONE);
            long sleepAt = SleepTimer.endsAt(this);
            sleepRow.trailing.setText(sleepAt > 0 ? L.f("в %s", hhmm(sleepAt)) : L.t("выкл."));
            sleepRow.trailing.setTextColor(sleepAt > 0 ? ui.accent : ui.fgA(0.35f));
        }

        // footer
        android.graphics.drawable.GradientDrawable g = ui.oval(host == null || reachable == null ? ui.fgA(0.3f)
                : reachable ? ui.green : ui.red);
        devDot.setBackground(g);
        devNameView.setText(host == null ? L.t("Устройство не найдено") : nz(devName, host));
    }

    private static String nz(String a, String b) { return a == null || a.isEmpty() ? b : a; }
}
