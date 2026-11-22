package app.fling;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
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
public class MainActivity extends Activity implements Atv.Listener, Cast.Listener, Discovery.Listener {

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
    static final int SEEK = 30;
    private static final int REQ_MIC = 7;

    private Ui ui;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService net = Executors.newFixedThreadPool(3);
    private SharedPreferences prefs;
    private Cast cast;
    private Atv atv;
    private Discovery discovery;

    // state
    private String host, devName;
    private List<Discovery.Device> devices = new ArrayList<>();
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
    private String error;
    private String link, linkTitle;
    private boolean pendingAutoCast;
    private boolean resumed;
    private int padMode;   // 0 buttons, 1 touchpad

    // views
    private LinearLayout root;
    private ScrollView scroll;
    private View helpView, langRowView;
    private TextView errorView, subtitleHeader;
    private LinearLayout nowCard, linkCard, tvCard;
    private Ui.Artwork nowArt, linkArt;
    private Ui.RefreshButton nowRefresh, linkRefresh;
    private View linkArtFrame;
    private TextView nowTitle, nowSub, nowEl, nowRem, nowHint;
    private Ui.Progress nowProg;
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

    private AudioRecord recorder;
    private volatile boolean recording;
    private final Map<String, Bitmap> images = new HashMap<>();
    private final Map<String, String> ytTitles = new HashMap<>();
    private int volPending = -1;

    // ================================================================ lifecycle

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        L.init(this);
        ui = new Ui(this);
        prefs = getSharedPreferences("fling", MODE_PRIVATE);
        host = prefs.getString("host", null);
        devName = prefs.getString("name", null);
        padMode = prefs.getInt("padMode", 0);
        cast = new Cast(this, this);
        adb = new Adb(this);
        atv = new Atv(this, this);
        discovery = new Discovery(this, this);
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
        resumed = true;
        if (!prefs.getBoolean("help_seen", false)) {                  // first launch: show the guide once
            prefs.edit().putBoolean("help_seen", true).apply();
            main.postDelayed(this::startTour, 900);                    // after the screen has been laid out
        }
        discovery.start();
        connectDevice();
        main.post(poll);
        main.post(tick);
    }

    @Override protected void onPause() {
        super.onPause();
        resumed = false;
        main.removeCallbacks(poll);
        main.removeCallbacks(tick);
        discovery.stop();
        if (voice) stopVoice();
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        cast.disconnect();
        atv.disconnect();
        net.shutdownNow();
    }

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (!resumed) return;
            if (host != null) cast.refresh();
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

    private final Map<String, String> arts = new HashMap<>();

    /** Cast sessions from the YouTube app carry a title but no artwork: find it by title. */
    private String artFor(String title, String artist) {
        if (title == null) return null;
        final String key = title + "|" + artist;
        if (arts.containsKey(key)) return arts.get(key);
        arts.put(key, null);
        net.execute(() -> {
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
        adbBusy = true;
        // every 3 s; back off to 30 s while ADB is off on the TV
        adbNext = now + 3000;
        net.execute(() -> {
            Cast.Status r = null;
            try { r = adb.nowPlaying(h); } catch (Exception e) {
                // ADB off, or waiting for "Allow debugging" on the TV: don't re-prompt every 3 s
                if ("closed".equals(Adb.lastState) || "unauthorized".equals(Adb.lastState))
                    adbNext = SystemClock.elapsedRealtime() + 30000;
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
                sync();
            });
        });
    }

    private void connectDevice() {
        if (host == null) return;
        cast.connect(host);
        atv.connect(host);
    }

    private boolean paired() { return atv.paired(host); }

    // ================================================================ callbacks (background threads)

    @Override public void onStatus(Cast.Status s) {
        final Cast.Status c = s == null ? null : s.copy();
        main.post(() -> { st = c; sync(); });
    }

    @Override public void onError(String message) { main.post(() -> showError(message)); }

    @Override public void onPower(Boolean on) { main.post(() -> { tvOn = on; sync(); }); }

    @Override public void onApp(String pkg) { main.post(() -> { tvApp = pkg; sync(); }); }

    @Override public void onPairing(String state, String message) {
        main.post(() -> {
            pairState = state;
            pairMsg = message == null ? "" : message;
            if (state.equals("idle") && paired()) { pinEdit.setText(""); error = null; }
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
            devices = withManual(list);   // discovered TVs + the ones the user added by IP
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
        showError(L.t("Делаю снимок экрана ТВ…"));
        net.execute(() -> {
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
                android.content.ContentValues cv = new android.content.ContentValues();
                cv.put(android.provider.MediaStore.Images.Media.DISPLAY_NAME,
                        new java.text.SimpleDateFormat("'tv-'yyyyMMdd-HHmmss'.png'", java.util.Locale.US).format(new java.util.Date()));
                cv.put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/png");
                if (Build.VERSION.SDK_INT >= 29) cv.put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures/Fling");
                android.net.Uri uri = null;
                try {
                    uri = getContentResolver().insert(android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
                    if (uri != null) try (java.io.OutputStream o = getContentResolver().openOutputStream(uri)) { o.write(png); }
                } catch (Exception ignored) {}   // pre-Android 10 without storage permission: just show it
                main.post(() -> {
                    android.widget.ImageView iv = new android.widget.ImageView(this);
                    iv.setImageBitmap(bmp);
                    iv.setAdjustViewBounds(true);
                    new android.app.AlertDialog.Builder(this)
                            .setTitle(L.t("Снимок экрана ТВ"))
                            .setMessage(black ? L.t("Снимок чёрный: видео защищено от снимков (DRM у YouTube/Netflix). Меню и главный экран ТВ снимаются нормально.")
                                              : L.t("Сохранено в Pictures/Fling"))
                            .setView(iv)
                            .setPositiveButton("OK", null)
                            .show();
                    showError(null);
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

    private void showError(String m) {
        error = m;
        sync();
        main.removeCallbacks(clearError);
        if (m != null) main.postDelayed(clearError, 9000);
    }

    private final Runnable clearError = () -> { error = null; sync(); };

    // ================================================================ intents / links

    private static final Pattern URL_RE = Pattern.compile("https?://\\S+", Pattern.CASE_INSENSITIVE);

    private void handleIntent(Intent i) {
        if (i == null) return;
        String text = null, subject = null;
        if (Intent.ACTION_SEND.equals(i.getAction())) {
            text = i.getStringExtra(Intent.EXTRA_TEXT);
            subject = i.getStringExtra(Intent.EXTRA_SUBJECT);
        } else if (Intent.ACTION_VIEW.equals(i.getAction()) && i.getData() != null) {
            text = i.getDataString();
        }
        if (text == null) return;
        String url = extractUrl(text);
        if (url == null) { showError(L.t("В том, чем поделились, нет ссылки.")); return; }
        setLink(url, subject);
        i.setAction(Intent.ACTION_MAIN);   // don't recast on rotation
        if (host != null) castLink(url, linkTitle); else pendingAutoCast = true;
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
        if (!(h.equals("youtu.be") || h.endsWith("youtube.com"))) return null;
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
        if (host == null) { pendingAutoCast = true; showError(L.t("Ищу телевизор…")); return; }
        String id = youtubeId(url);
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
        error = null;
        toast(L.t("Отправлено на ТВ"));
        sync();
    }

    private void toast(String s) { android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_SHORT).show(); }

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
        connectDevice();
        if (pendingAutoCast && link != null) { pendingAutoCast = false; castLink(link, linkTitle); }
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
        for (Discovery.Device m : manualDevices()) {
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
        if (atv.paired(d.host)) marks.add(L.t("пульт ✓"));   // network remote already paired
        if (isManual(d)) marks.add(L.t("по IP"));            // not found by discovery
        return (d.host.equals(host) ? "✓  " : "") + d.name + (marks.isEmpty() ? "" : "  ·  " + String.join(", ", marks));
    }

    private void showDeviceMenu(View anchor) {
        PopupMenu pm = new PopupMenu(this, anchor);
        for (int i = 0; i < devices.size(); i++) pm.getMenu().add(0, i, i, menuLabel(devices.get(i)));   // ids 0..n-1 = TVs
        if (devices.isEmpty()) pm.getMenu().add(0, 999, 0, L.t("ТВ не найдены")).setEnabled(false);
        pm.getMenu().add(1, 1001, 1001, L.t("Найти ТВ заново"));
        pm.getMenu().add(1, 1000, 1000, L.t("Добавить ТВ по IP…"));
        List<Discovery.Device> manual = manualDevices();
        if (!manual.isEmpty()) {
            android.view.SubMenu sm = pm.getMenu().addSubMenu(1, 1002, 1002, L.t("Убрать добавленный вручную"));
            for (int i = 0; i < manual.size(); i++) sm.add(2, 2000 + i, i, manual.get(i).name);   // ids 2000+ = manual TVs
        }
        pm.setOnMenuItemClickListener(it -> {
            int id = it.getItemId();
            if (id == 1000) askIp();
            else if (id == 1001) { discovery.stop(); discovery.start(); toast(L.t("Ищу устройства…")); }   // restart mDNS search
            else if (id >= 2000) forgetManual(manual.get(id - 2000));
            else if (id < devices.size()) selectDevice(devices.get(id));
            return true;
        });
        pm.show();
    }

    private void askIp() {
        EditText e = ui.field("192.168.1.50", false);   // example address, not a real one
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        FrameLayout f = new FrameLayout(this);
        f.setPadding(ui.dp(20), ui.dp(8), ui.dp(20), 0);
        f.addView(e);
        new AlertDialog.Builder(this)
                .setTitle(L.t("IP-адрес телевизора"))
                .setMessage(L.t("Найдите его на ТВ: Настройки → Сеть."))
                .setView(f)
                .setPositiveButton(L.t("Добавить"), (dlg, w) -> addByIp(e.getText().toString().trim()))
                .setNegativeButton(L.t("Отмена"), null)
                .show();
    }

    /** Checks there is a Cast device at the address, asks the TV its name, saves and selects it. */
    private void addByIp(String ip) {
        if (!validIp(ip)) { showError(L.t("Это не похоже на IP-адрес (пример: 192.168.1.50).")); return; }   // 4 numbers, each 0..255
        net.execute(() -> {
            String name = ip;
            try (java.net.Socket s = new java.net.Socket()) {
                s.connect(new java.net.InetSocketAddress(ip, 8009), 2500);   // 8009 = Google Cast port
            } catch (Exception e) {
                main.post(() -> showError(L.f("По адресу %s нет Chromecast / Google TV.", ip)));
                return;
            }
            try {   // the TV's own name from its DIAL device description
                java.net.HttpURLConnection c = (java.net.HttpURLConnection)
                        new java.net.URL("http://" + ip + ":8008/ssdp/device-desc.xml").openConnection();
                c.setConnectTimeout(3000); c.setReadTimeout(3000);
                String xml;
                try (java.io.InputStream in = c.getInputStream()) {          // stream closed automatically
                    xml = new String(in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
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

    // ================================================================ playback actions

    private void togglePlay() {
        if (adbSt != null && adbSt.hasMedia() && paired()) {
            atv.key(host, Atv.PLAY_PAUSE);
            adbSt.elapsed = adbSt.elapsedNow(); adbSt.updatedAt = SystemClock.elapsedRealtime();
            adbSt.state = adbSt.playing() ? "PAUSED" : "PLAYING";
            adbNext = 0; main.postDelayed(this::pollAdb, 1200);
            sync(); return;
        }
        if (st == null) return;
        if (paired()) { atv.key(host, Atv.PLAY_PAUSE); return; }
        if (st.stuck()) { showError(L.t("Телевизор игнорирует паузу по Chromecast (так ведёт себя YouTube на ТВ). Нажмите «Связать с ТВ» ниже — пауза пойдёт как кнопка пульта.")); return; }
        if (st.playing()) cast.pause(); else cast.play();
        st = cast.status();   // Cast updates its status optimistically without onStatus
        sync();
    }

    private void seek(int s) {
        if (adbSt != null && adbSt.hasMedia() && paired()) {
            for (int i = 0; i < Math.max(1, Math.abs(s) / 10); i++) atv.key(host, s > 0 ? Atv.FFWD : Atv.REWIND);
            adbNext = 0; main.postDelayed(this::pollAdb, 1500);
            return;
        }
        if (st != null && st.stuck()) {
            if (!paired()) { showError(L.t("Перемотка этого видео работает только после «Связать с ТВ» — кнопка ниже.")); return; }
            for (int i = 0; i < 3; i++) atv.key(host, s > 0 ? Atv.FFWD : Atv.REWIND);
            return;
        }
        cast.seekBy(s);
    }

    private final Runnable sendVolume = () -> {
        if (volPending >= 0) { cast.setVolume(volPending); volPending = -1; st = cast.status(); }
    };

    @Override public boolean onKeyDown(int code, KeyEvent e) {
        if ((code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN) && host != null) {
            boolean up = code == KeyEvent.KEYCODE_VOLUME_UP;
            if (paired()) atv.key(host, up ? Atv.VOL_UP : Atv.VOL_DOWN);
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
        if ((code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN) && host != null
                && (paired() || (st != null && st.volume >= 0))) return true;
        return super.onKeyUp(code, e);
    }

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
        Thread t = new Thread(() -> {
            byte[] buf = new byte[8192];
            while (recording) {
                int off = 0;
                while (recording && off < buf.length) {
                    int n = r.read(buf, off, buf.length - off);
                    if (n <= 0) break;
                    off += n;
                }
                if (off > 0) atv.voiceChunk(buf, off);
            }
        }, "mic");
        t.start();
    }

    private void stopRecording() {
        recording = false;
        if (recorder != null) {
            try { recorder.stop(); } catch (Exception ignored) {}
            recorder.release();
            recorder = null;
        }
    }

    private void stopVoice() {
        stopRecording();
        atv.voiceEnd();
    }

    // ================================================================ images / oEmbed

    interface StrCb { void on(String s); }

    private void fetchYoutubeTitle(String id, StrCb cb) {
        if (ytTitles.containsKey(id)) { cb.on(ytTitles.get(id)); return; }
        net.execute(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=" + id).openConnection();
                c.setConnectTimeout(5000);
                c.setReadTimeout(5000);
                try (InputStream in = c.getInputStream()) {
                    JSONObject o = new JSONObject(new String(in.readAllBytes(), "UTF-8"));
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
        net.execute(() -> {
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
        setContentView(scroll);
        scroll.setOnApplyWindowInsetsListener((v, ins) -> {
            int top, bottom, left, right;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets i = ins.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                top = i.top; bottom = i.bottom; left = i.left; right = i.right;
            } else {
                top = ins.getSystemWindowInsetTop(); bottom = ins.getSystemWindowInsetBottom();
                left = ins.getSystemWindowInsetLeft(); right = ins.getSystemWindowInsetRight();
            }
            v.setPadding(left, top, right, bottom);
            return WindowInsets.CONSUMED;
        });

        // header
        LinearLayout header = ui.row();
        header.setPadding(ui.dp(4), ui.dp(6), ui.dp(4), ui.dp(14));
        header.addView(ui.logo(40));
        LinearLayout ht = ui.col();
        ht.setPadding(ui.dp(12), 0, 0, 0);
        TextView title = ui.text("Fling", 26, ui.fg, true);
        ht.addView(title);
        subtitleHeader = ui.text("", 13, ui.fgA(0.5f), false);
        subtitleHeader.setPadding(0, ui.dp(3), 0, 0);
        ht.addView(subtitleHeader);
        header.addView(ht, Ui.lpW(1));
        TextView help = ui.text("?", 20, ui.accent, true);           // opens the "How to use" guide
        help.setGravity(Gravity.CENTER);
        help.setBackground(ui.round(Ui.alpha(ui.accent, 0.12f), 18));
        help.setContentDescription(L.t("Как пользоваться"));
        help.setOnClickListener(v -> startTour());
        helpView = help;
        header.addView(help, new LinearLayout.LayoutParams(ui.dp(36), ui.dp(36)));
        root.addView(header);

        errorView = ui.text("", 14, ui.orange, false);
        errorView.setBackground(ui.round(Ui.alpha(ui.orange, 0.12f), 12));
        errorView.setPadding(ui.dp(14), ui.dp(10), ui.dp(14), ui.dp(10));
        errorView.setOnClickListener(v -> { error = null; sync(); });
        root.addView(errorView, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 12));

        buildNowCard();
        buildLinkCard();
        buildTvCard();
        buildFooter();
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
        nowTitle.setMaxLines(2);
        nowTitle.setEllipsize(TextUtils.TruncateAt.END);
        nowCard.addView(nowTitle, ui.margins(Ui.lp(-1, -2), 4, 12, 4, 0));
        nowSub = ui.text("", 14, ui.fgA(0.5f), false);
        nowCard.addView(nowSub, ui.margins(Ui.lp(-1, -2), 4, 4, 4, 0));
        nowProg = ui.new Progress();
        nowCard.addView(nowProg, ui.margins(Ui.lp(-1, ui.dp(4)), 4, 14, 4, 0));
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
        View back = ui.key("back30", null, 50, false, v -> seek(-SEEK));
        playKey = (FrameLayout) ui.key("pause", null, 64, true, v -> togglePlay());
        playIcon = (ImageView) playKey.getChildAt(0);
        View fwd = ui.key("fwd30", null, 50, false, v -> seek(SEEK));
        ctl.addView(back);
        View g1 = new View(this); ctl.addView(g1, Ui.lp(ui.dp(28), 1));
        ctl.addView(playKey);
        View g2 = new View(this); ctl.addView(g2, Ui.lp(ui.dp(28), 1));
        ctl.addView(fwd);
        nowCard.addView(ctl);

        nowHint = ui.text("", 13, ui.orange, false);
        nowCard.addView(nowHint, ui.margins(Ui.lp(-1, -2), 4, 2, 4, 6));

        LinearLayout vol = ui.row();
        vol.setPadding(ui.dp(4), 0, ui.dp(4), 0);
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
        linkEmpty = ui.text(L.t("Поделитесь ссылкой из YouTube или браузера → «Fling» — и видео сразу пойдёт на телевизор. Или скопируйте ссылку и вставьте её здесь."),
                14, ui.fgA(0.55f), false);
        linkEmpty.setLineSpacing(0, 1.2f);
        linkCard.addView(linkEmpty, ui.margins(Ui.lp(-1, -2), 10, 0, 10, 6));
        linkCard.addView(ui.separator());
        linkCastRow = ui.row(L.t("Отправить на ТВ"), "tv_play", null, true, v -> { if (link != null) castLink(link, linkTitle); });
        linkCard.addView(linkCastRow);
        linkCard.addView(ui.row(L.t("Вставить ссылку из буфера"), "clipboard", null, false, v -> pasteLink()));
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
        TextView sx = ui.text(L.t("Свяжите один раз с ТВ, чтобы открыть приложения, крестовину, ввод текста, голосовой поиск и отправку YouTube-ссылок."), 14, ui.fgA(0.55f), false);
        sx.setLineSpacing(0, 1.2f);
        setupBox.addView(sx, ui.margins(Ui.lp(-1, -2), 10, 0, 10, 6));
        Ui.Row pairRow = ui.row(L.t("Связать с ТВ"), "tv_plus", null, true, v -> {
            if (host == null) { showError(L.t("Телевизор ещё не найден.")); return; }
            atv.startPairing(host);
        });
        pairRow.setIconColor(ui.green);
        setupBox.addView(pairRow);
        tvCard.addView(setupBox);

        pinFail = ui.text("", 14, ui.orange, false);
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
        hdr.addView(ui.key("power", null, 40, false, v -> atv.key(host, Atv.POWER)));
        ctlBox.addView(hdr);

        // app grid, 4 per row
        LinearLayout grid = ui.col();
        grid.setPadding(ui.dp(6), 0, ui.dp(6), ui.dp(10));
        LinearLayout r = null;
        for (int i = 0; i < TV_APPS.length; i++) {
            if (i % 4 == 0) { r = ui.row(); grid.addView(r, ui.margins(Ui.lp(-1, -2), 0, i == 0 ? 0 : 6, 0, 0)); }
            final String[] app = TV_APPS[i];
            TextView c = ui.chip(app[0], v -> atv.launch(host, app[1]));
            c.setTag(app[2]);
            chips.add(c);
            LinearLayout.LayoutParams p = Ui.lpW(1);
            p.setMargins(i % 4 == 0 ? 0 : ui.dp(3), 0, i % 4 == 3 ? 0 : ui.dp(3), 0);
            r.addView(c, p);
        }
        ctlBox.addView(grid);

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
        dp.addView(ui.key("up", null, 52, false, v -> atv.key(host, Atv.UP)));
        LinearLayout mid = ui.row();
        mid.setGravity(Gravity.CENTER);
        mid.setPadding(0, ui.dp(8), 0, ui.dp(8));
        mid.addView(ui.key("back", null, 46, false, v -> atv.key(host, Atv.BACK)));
        mid.addView(new View(this), Ui.lp(ui.dp(14), 1));
        mid.addView(ui.key("left", null, 52, false, v -> atv.key(host, Atv.LEFT)));
        mid.addView(new View(this), Ui.lp(ui.dp(10), 1));
        mid.addView(ui.key(null, "OK", 70, true, v -> atv.key(host, Atv.OK)));
        mid.addView(new View(this), Ui.lp(ui.dp(10), 1));
        mid.addView(ui.key("right", null, 52, false, v -> atv.key(host, Atv.RIGHT)));
        mid.addView(new View(this), Ui.lp(ui.dp(14), 1));
        mid.addView(ui.key("home", null, 46, false, v -> atv.key(host, Atv.HOME)));
        dp.addView(mid);
        dp.addView(ui.key("down", null, 52, false, v -> atv.key(host, Atv.DOWN)));
        dpadBox = dp;
        ctlBox.addView(dp);
        padBox = ui.new Touchpad(code -> atv.key(host, code));
        ctlBox.addView(padBox);

        // volume pills
        LinearLayout vk = ui.row();
        vk.setPadding(ui.dp(6), ui.dp(10), ui.dp(6), ui.dp(4));
        LinearLayout.LayoutParams p1 = Ui.lpW(1); p1.setMargins(0, 0, ui.dp(4), 0);
        LinearLayout.LayoutParams p2 = Ui.lpW(1); p2.setMargins(ui.dp(4), 0, ui.dp(4), 0);
        LinearLayout.LayoutParams p3 = Ui.lpW(1); p3.setMargins(ui.dp(4), 0, 0, 0);
        vk.addView(ui.pill("spk_minus", L.t("Громк −"), v -> atv.key(host, Atv.VOL_DOWN)), p1);
        vk.addView(ui.pill("spk_slash", L.t("Без звука"), v -> atv.key(host, Atv.MUTE)), p2);
        vk.addView(ui.pill("spk_plus", L.t("Громк +"), v -> atv.key(host, Atv.VOL_UP)), p3);
        ctlBox.addView(vk);
        TextView hwHint = ui.text(L.t("Кнопки громкости телефона тоже управляют ТВ"), 12, ui.fgA(0.35f), false);
        hwHint.setGravity(Gravity.CENTER);
        ctlBox.addView(hwHint, ui.margins(Ui.lp(-1, -2), 0, 2, 0, 6));

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
        addCard(tvCard);
    }

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
        sw.setOnClickListener(this::showDeviceMenu);
        r.addView(sw);
        f.addView(r);
        root.addView(f, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 8));
        TextView hint = ui.text(L.t("Fling для Android · телефон и ТВ должны быть в одной сети Wi-Fi"), 12, ui.fgA(0.3f), false);
        hint.setGravity(Gravity.CENTER);
        root.addView(hint, ui.margins(Ui.lp(-1, -2), 0, 6, 0, 0));
        // authorship: Fling is Damion Rashford's and Tatiana's app; this is an unofficial port
        TextView credit = ui.text(L.t("Fling создан Damion Rashford и Tatiana · неофициальный порт"), 12, ui.fgA(0.45f), false);
        credit.setGravity(Gravity.CENTER);
        credit.setPadding(0, ui.dp(6), 0, ui.dp(6));
        credit.setOnClickListener(v -> {
            try { startActivity(new android.content.Intent(android.content.Intent.ACTION_VIEW,
                    android.net.Uri.parse("https://github.com/tatianathevisionary/fling"))); } catch (Exception ignored) {}
        });
        root.addView(credit, ui.margins(Ui.lp(-1, -2), 0, 2, 0, 0));

        // language: RU | EN (saved; "auto" until the user picks)
        LinearLayout langRow = new LinearLayout(this);
        langRow.setGravity(Gravity.CENTER);
        LinearLayout seg = ui.segmented(new String[]{"RU", "EN"}, L.english() ? 1 : 0, idx -> {
            String want = idx == 1 ? "en" : "ru";
            if ((idx == 1) != L.english()) { L.setLang(this, want); recreate(); }
            else L.setLang(this, want);
        });
        langRow.addView(seg, Ui.lp(ui.dp(120), -2));
        root.addView(langRow, ui.margins(Ui.lp(-1, -2), 0, 10, 0, 0));
        langRowView = seg;
    }

    /** Short tips, one at a time, over the real screen (Tour); the long guide stays one tap away. */
    private void startTour() {
        List<Tour.Step> steps = new ArrayList<>();
        steps.add(new Tour.Step((View) devNameView.getParent().getParent(), L.t("Ваш телевизор"),   // the whole footer card
                L.t("Fling сам находит ТВ в той же сети Wi‑Fi. «Сменить» — другой ТВ или добавить по IP.")));
        steps.add(new Tour.Step(setupBox, L.t("Пульт — один раз"),
                L.t("Нажмите, и на ТВ появится код из 6 символов. После этого работают пауза, приложения и стрелки.")));
        steps.add(new Tour.Step(ctlBox, L.t("Пульт"),
                L.t("Приложения, стрелки или тачпад, громкость, текст и голос. Кнопки громкости телефона тоже управляют ТВ.")));
        steps.add(new Tour.Step(linkCard, L.t("Отправить видео"),
                L.t("В YouTube или браузере: «Поделиться» → Fling. Или скопируйте ссылку и вставьте её здесь.")));
        steps.add(new Tour.Step(nowCard, L.t("Что играет"),
                L.t("Пауза, ±30 секунд и громкость того, что идёт на ТВ.")));
        steps.add(new Tour.Step(langRowView, L.t("Язык"), L.t("Русский или английский — переключается здесь.")));
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
        nowProg.set(d > 0 ? (float) (el / d) : 0);
    }

    private static String appName(String pkg) {
        if (pkg == null) return null;
        for (String[] a : TV_APPS) if (a[2].equals(pkg)) return a[0];
        String tail = pkg.substring(pkg.lastIndexOf('.') + 1);
        return tail.isEmpty() ? pkg : Character.toUpperCase(tail.charAt(0)) + tail.substring(1);
    }

    private void sync() {
        if (root == null) return;
        final Cast.Status st = view();
        boolean paired = paired();

        subtitleHeader.setText(host == null ? L.t("Ищу телевизор в сети…")
                : st != null && st.hasMedia() ? L.f("Сейчас на «%s»", nz(devName, host)) : L.f("Пульт и трансляция на «%s»", nz(devName, host)));

        errorView.setText(error == null ? "" : error);
        errorView.setVisibility(error == null ? View.GONE : View.VISIBLE);

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
            nowHint.setText(stuck && !paired ? L.t("ТВ не принимает команды по Chromecast для этого видео. Нажмите «Связать с ТВ» ниже — пауза и перемотка пойдут как кнопки пульта.") : "");
            nowHint.setVisibility(stuck && !paired ? View.VISIBLE : View.GONE);
            if (st.volume >= 0 && volPending < 0) { castVol.set(st.volume); castVolNum.setText(String.valueOf(st.volume)); }
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
            else if (yt) { linkKind.setText(paired ? L.t("откроется в YouTube на ТВ") : L.t("нужна настройка ТВ")); linkKind.setTextColor(paired ? ui.green : ui.orange); }
            else { linkKind.setText(paired ? L.t("откроется на ТВ") : L.t("нужна настройка ТВ")); linkKind.setTextColor(paired ? ui.fgA(0.55f) : ui.orange); }
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
        ctlBox.setVisibility(idleLike && paired ? View.VISIBLE : View.GONE);
        pinFail.setText(pairMsg);
        pinFail.setVisibility(pairState.equals("failed") && !pairMsg.isEmpty() ? View.VISIBLE : View.GONE);
        if (paired) {
            String now = tvApp != null ? L.f("Сейчас: %s", appName(tvApp)) : Boolean.FALSE.equals(tvOn) ? L.t("ТВ выключен") : tvOn == null ? L.t("Подключаюсь…") : L.t("Главный экран");
            tvNow.setText(now);
            for (TextView c : chips) ui.setChipActive(c, c.getTag().equals(tvApp));
            dpadBox.setVisibility(padMode == 0 ? View.VISIBLE : View.GONE);
            padBox.setVisibility(padMode == 1 ? View.VISIBLE : View.GONE);
            micIcon.setImageDrawable(new Ui.Glyph(voice ? "mic_fill" : "mic", voice ? ui.red : ui.fg, 2.0f));
            micKey.setBackground(ui.rippleOval(voice ? Ui.alpha(ui.red, 0.15f) : ui.fgA(ui.dark ? 0.1f : 0.06f)));
            listening.setVisibility(voice ? View.VISIBLE : View.GONE);
        }

        // footer
        android.graphics.drawable.GradientDrawable g = ui.oval(host != null ? ui.green : ui.fgA(0.3f));
        devDot.setBackground(g);
        devNameView.setText(host == null ? L.t("Устройство не найдено") : nz(devName, host));
    }

    private static String nz(String a, String b) { return a == null || a.isEmpty() ? b : a; }
}
