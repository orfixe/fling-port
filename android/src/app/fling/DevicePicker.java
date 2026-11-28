// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.drawable.ColorDrawable;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** The TV chooser, as on the PC: opening it starts a search at once, a radar runs while
 *  Fling looks, and every TV is a card (name, model, address, what answers: Cast, remote,
 *  ADB, Samsung; online dot, delay over Tailscale). Three sources in one place:
 *    • at home — mDNS (Chromecast / Google TV) and SSDP (Samsung), as before;
 *    • ADB — devices on this Wi‑Fi with network debugging on port 5555 that aren't a Cast
 *      device (a Fire TV, an Android box): LIVE and the ADB tools work with them;
 *    • Tailscale — TVs added by their tailnet address, checked and timed on every search.
 *  Android can't list the tailnet's devices (the Tailscale app keeps that to itself), so a
 *  tailnet TV is added once by its 100.x address or MagicDNS name, right here. */
final class DevicePicker {
    interface Host {
        List<Discovery.Device> devices();               // discovered + Samsung + added by hand
        String current();                               // selected host, or null
        boolean paired(String host);                    // Android TV remote paired
        boolean manual(Discovery.Device d);
        void select(Discovery.Device d);
        void addByAddress(String text);                 // checks it, names it, saves it, selects it
        void addAdbOnly(String host, String name);      // a device that only answers ADB
        void forget(Discovery.Device d);
        void rescan();                                  // restart mDNS + SSDP
        void tailscaleHelp();                           // install / open Tailscale
    }

    private static final int SEARCH_MS = 10_000;
    private static final int[] PORTS = {8009, 6466, 5555, 8002};   // Cast, remote, ADB, Samsung

    /** What answered on one address, and how fast (ms; -1 = nothing). */
    private static final class Probe {
        boolean cast, remote, adb, samsung;
        long ms = -1;
        boolean any() { return ms >= 0; }
    }

    private final Activity a;
    private final Ui ui;
    private final Host host;
    private final Dialog dlg;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService pool = Executors.newFixedThreadPool(24);
    private final Map<String, Probe> probes = new HashMap<>();
    private final Map<String, String> adbOnly = new HashMap<>();   // host → name, from the sweep
    private long searchUntil;
    private int generation;

    private Radar radar;
    private TextView searchTitle, searchHint, againBtn, addHint;
    private LinearLayout homeList, adbList, tailList, tailState;
    private View homeEmpty, adbBox;
    private EditText addField;
    private boolean adding;                                       // an address check is running

    private DevicePicker(Activity a, Ui ui, Host host) {
        this.a = a; this.ui = ui; this.host = host;
        dlg = new Dialog(a, android.R.style.Theme_DeviceDefault_NoActionBar);
        dlg.requestWindowFeature(Window.FEATURE_NO_TITLE);
        dlg.setContentView(build());
        Window w = dlg.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(ui.page));
            w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
            w.setStatusBarColor(ui.page);
            w.setNavigationBarColor(ui.page);
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                w.getDecorView();
                android.view.WindowInsetsController c = w.getInsetsController();
                if (c != null) {
                    int light = android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                            | android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                    c.setSystemBarsAppearance(ui.dark ? 0 : light, light);
                }
            }
        }
        dlg.setOnDismissListener(d -> { generation++; pool.shutdownNow(); radar.stop(); if (open == this) open = null; });
    }

    private static DevicePicker open;

    static void show(Activity a, Ui ui, Host host) {
        if (open != null) return;
        open = new DevicePicker(a, ui, host);
        open.dlg.show();
        open.search();
    }

    /** The host's TV list changed (a search result came in): redraw the cards. */
    static void changed() { if (open != null) open.sync(); }

    /** An error while the chooser is open (adding by address): shown under the field. */
    static boolean error(String m) {
        if (open == null || !open.adding) return false;           // only the address being added
        open.adding = false;
        open.addHint.setText(m);
        open.addHint.setTextColor(open.ui.red);
        return true;
    }

    /** A TV was picked or added: close the chooser. */
    static void close() { if (open != null) open.dlg.dismiss(); }

    // ------------------------------------------------------------ layout

    private View build() {
        ScrollView sv = new ScrollView(a);
        sv.setFillViewport(true);
        sv.setOnApplyWindowInsetsListener((v, ins) -> {          // edge to edge: keep clear of the bars
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets b = ins.getInsets(android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.ime());
                v.setPadding(b.left, b.top, b.right, b.bottom);
            } else {
                v.setPadding(ins.getSystemWindowInsetLeft(), ins.getSystemWindowInsetTop(), ins.getSystemWindowInsetRight(), ins.getSystemWindowInsetBottom());
            }
            return ins;
        });
        LinearLayout root = ui.col();
        root.setPadding(ui.dp(16), ui.dp(12), ui.dp(16), ui.dp(24));
        sv.addView(root);

        // header: back, title, search again
        LinearLayout head = ui.row();
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.addView(ui.key("back", null, 40, false, v -> dlg.dismiss()));
        TextView title = ui.text(L.t("Телевизоры"), 26, ui.fg, true);
        title.setPadding(ui.dp(12), 0, 0, 0);
        head.addView(title, Ui.lpW(1));
        againBtn = ui.text(L.t("Искать снова"), 14, ui.accent, true);
        againBtn.setPadding(ui.dp(14), ui.dp(8), ui.dp(14), ui.dp(8));
        againBtn.setBackground(ui.rippleRound(ui.accA(0.12f), 18));
        againBtn.setOnClickListener(v -> search());
        head.addView(againBtn);
        root.addView(head, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 14));

        // searching: radar + text
        LinearLayout searching = ui.row();
        searching.setGravity(Gravity.CENTER_VERTICAL);
        radar = new Radar(a);
        searching.addView(radar, Ui.lp(ui.dp(64), ui.dp(64)));
        LinearLayout st = ui.col();
        st.setPadding(ui.dp(14), 0, 0, 0);
        searchTitle = ui.text("", 16, ui.fg, true);
        searchHint = ui.text("", 13, ui.fgA(0.55f), false);
        searchHint.setPadding(0, ui.dp(3), 0, 0);
        st.addView(searchTitle);
        st.addView(searchHint);
        searching.addView(st, Ui.lpW(1));
        root.addView(searching, ui.margins(Ui.lp(-1, -2), 4, 0, 0, 16));

        // at home
        root.addView(ui.label(L.t("Wi‑Fi")), ui.margins(Ui.lp(-1, -2), 12, 4, 12, 8));
        homeList = ui.col();
        root.addView(homeList, Ui.lp(-1, -2));
        LinearLayout empty = ui.card();
        empty.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(12));
        empty.addView(ui.text(L.t("Телевизоры не найдены"), 15, ui.fg, true));
        TextView et = ui.text(L.t("Проверьте, что ТВ включён и в той же сети Wi‑Fi, что и телефон. Если ТВ в другой подсети, добавьте его по адресу ниже."), 13, ui.fgA(0.6f), false);
        et.setPadding(0, ui.dp(4), 0, 0);
        empty.addView(et);
        homeEmpty = empty;
        root.addView(empty, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 8));

        // ADB-only devices
        LinearLayout ab = ui.col();
        ab.addView(ui.label(L.t("ADB · отладка по сети")), ui.margins(Ui.lp(-1, -2), 12, 12, 12, 8));
        adbList = ui.col();
        ab.addView(adbList, Ui.lp(-1, -2));
        adbBox = ab;
        root.addView(ab, Ui.lp(-1, -2));

        // Tailscale
        root.addView(ui.label("Tailscale"), ui.margins(Ui.lp(-1, -2), 12, 12, 12, 8));
        tailState = ui.col();
        root.addView(tailState, Ui.lp(-1, -2));
        tailList = ui.col();
        root.addView(tailList, Ui.lp(-1, -2));

        // add by address (LAN IP, tailnet IP or MagicDNS name)
        root.addView(ui.label(L.t("Добавить по адресу")), ui.margins(Ui.lp(-1, -2), 12, 12, 12, 8));
        LinearLayout addCard = ui.card();
        addCard.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(12));
        LinearLayout ar = ui.row();
        ar.setGravity(Gravity.CENTER_VERTICAL);
        addField = ui.field("192.168.1.50 · 100.x.y.z · tv.tailnet.ts.net", false);
        addField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        addField.setImeOptions(EditorInfo.IME_ACTION_DONE);
        addField.setSingleLine(true);
        addField.setOnEditorActionListener((v, id, e) -> {
            if (id == EditorInfo.IME_ACTION_DONE || (e != null && e.getKeyCode() == KeyEvent.KEYCODE_ENTER)) { add(); return true; }
            return false;
        });
        ar.addView(addField, Ui.lpW(1));
        TextView addBtn = ui.text(L.t("Добавить"), 15, 0xFFFFFFFF, true);
        addBtn.setPadding(ui.dp(16), ui.dp(10), ui.dp(16), ui.dp(10));
        addBtn.setBackground(ui.rippleRound(ui.accent, 10));
        addBtn.setOnClickListener(v -> add());
        ar.addView(addBtn, ui.margins(Ui.lp(-2, -2), 8, 0, 0, 0));
        addCard.addView(ar);
        addHint = ui.text(L.t("В локальной сети — IP из настроек ТВ (Сеть). Через Tailscale — 100.x.y.z или имя MagicDNS."), 12, ui.fgA(0.5f), false);
        addHint.setPadding(ui.dp(2), ui.dp(8), 0, 0);
        addCard.addView(addHint);
        root.addView(addCard, Ui.lp(-1, -2));
        return sv;
    }

    // ------------------------------------------------------------ search

    private void search() {
        final int gen = ++generation;
        searchUntil = SystemClock.elapsedRealtime() + SEARCH_MS;
        radar.start();
        host.rescan();
        sync();
        main.postDelayed(() -> { if (gen == generation) { radar.stop(); sync(); } }, SEARCH_MS);
        for (int t = 1; t <= 4; t++)                     // probe what turned up meanwhile
            main.postDelayed(() -> { if (gen == generation) probeAll(gen); }, t * 2500L);
        probeAll(gen);
        sweepAdb(gen);
    }

    private boolean searching() { return SystemClock.elapsedRealtime() < searchUntil; }

    /** Ports of every known TV, in parallel: what answers and how fast. */
    private void probeAll(int gen) {
        Set<String> hosts = new HashSet<>();
        for (Discovery.Device d : host.devices()) hosts.add(d.host);
        hosts.addAll(adbOnly.keySet());
        for (String h : hosts) {
            pool.execute(() -> {
                Probe p = new Probe();
                for (int port : PORTS) {
                    long ms = Tailnet.connectMs(h, port, Tailnet.isTailnetHost(h) ? 2500 : 1200);
                    if (ms < 0) continue;
                    if (port == 8009) p.cast = true; else if (port == 6466) p.remote = true;
                    else if (port == 5555) p.adb = true; else p.samsung = true;
                    p.ms = p.ms < 0 ? ms : Math.min(p.ms, ms);
                }
                main.post(() -> { if (gen == generation) { probes.put(h, p); sync(); } });
            });
        }
    }

    /** Port 5555 on every address of this Wi‑Fi (/24 around the phone): network debugging. */
    private void sweepAdb(int gen) {
        String[] own = wifiIpv4(a);
        if (own == null) return;
        String base = own[0].substring(0, own[0].lastIndexOf('.') + 1);
        for (int i = 1; i < 255; i++) {
            final String ip = base + i;
            if (ip.equals(own[0])) continue;
            pool.execute(() -> {
                if (gen != generation || Tailnet.connectMs(ip, 5555, 600) < 0) return;
                String name;
                try {
                    InetAddress ia = InetAddress.getByName(ip);
                    name = ia.getCanonicalHostName();             // the router's name for it, if any
                    if (name == null || name.equals(ip)) name = null;
                    else name = name.replaceAll("\\.(lan|local|home|localdomain)$", "");
                } catch (Exception e) { name = null; }
                final String n = name;
                main.post(() -> {
                    if (gen != generation) return;
                    adbOnly.put(ip, n == null ? L.t("Устройство с ADB") : n);
                    Probe p = probes.get(ip);
                    if (p == null) { p = new Probe(); probes.put(ip, p); }
                    p.adb = true;
                    sync();
                });
            });
        }
    }

    /** The phone's IPv4 on Wi‑Fi (not the VPN): {address, prefix}. */
    @SuppressWarnings("deprecation")
    static String[] wifiIpv4(Context c) {
        ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return null;
        try {
            for (Network n : cm.getAllNetworks()) {
                NetworkCapabilities nc = cm.getNetworkCapabilities(n);
                if (nc == null || !(nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                        || nc.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) continue;
                LinkProperties lp = cm.getLinkProperties(n);
                if (lp == null) continue;
                for (LinkAddress la : lp.getLinkAddresses())
                    if (la.getAddress() instanceof Inet4Address && la.getPrefixLength() >= 16)
                        return new String[]{la.getAddress().getHostAddress(), String.valueOf(la.getPrefixLength())};
            }
        } catch (Exception ignored) {}
        return null;
    }

    private void add() {
        String t = addField.getText().toString().trim();
        if (t.isEmpty()) { addField.requestFocus(); return; }
        addHint.setText(L.f("Проверяю %s…", t));
        addHint.setTextColor(ui.fgA(0.5f));
        adding = true;
        host.addByAddress(t);
    }

    // ------------------------------------------------------------ cards

    private void sync() {
        if (!dlg.isShowing() && open != this) return;
        boolean s = searching();
        againBtn.setEnabled(!s);
        againBtn.setAlpha(s ? 0.5f : 1f);
        List<Discovery.Device> all = host.devices();
        List<Discovery.Device> home = new ArrayList<>(), tail = new ArrayList<>();
        Set<String> listed = new HashSet<>();
        for (Discovery.Device d : all) {
            listed.add(d.host);
            if (Tailnet.isTailnetHost(d.host)) tail.add(d); else home.add(d);
        }
        int found = home.size() + tail.size();
        searchTitle.setText(s ? L.t("Ищу телевизоры…") : L.f("Найдено: %d", found + countAdb(listed)));
        searchHint.setText(s ? L.t("Wi‑Fi, ADB и Tailscale · обычно до 10 секунд.")
                             : L.t("Нажмите на ТВ, чтобы переключиться."));

        homeList.removeAllViews();
        for (Discovery.Device d : home) homeList.addView(card(d), ui.margins(Ui.lp(-1, -2), 0, 0, 0, 8));
        homeEmpty.setVisibility(home.isEmpty() && !s ? View.VISIBLE : View.GONE);

        adbList.removeAllViews();
        for (Map.Entry<String, String> e : adbOnly.entrySet()) {
            if (listed.contains(e.getKey())) continue;                // already a TV card above
            Discovery.Device d = new Discovery.Device(e.getValue(), e.getKey(), "adb");
            adbList.addView(card(d), ui.margins(Ui.lp(-1, -2), 0, 0, 0, 8));
        }
        adbBox.setVisibility(adbList.getChildCount() > 0 ? View.VISIBLE : View.GONE);

        tailState.removeAllViews();
        tailState.addView(tailnetStatus(), ui.margins(Ui.lp(-1, -2), 0, 0, 0, 8));
        tailList.removeAllViews();
        for (Discovery.Device d : tail) tailList.addView(card(d), ui.margins(Ui.lp(-1, -2), 0, 0, 0, 8));
    }

    private int countAdb(Set<String> listed) {
        int n = 0;
        for (String h : adbOnly.keySet()) if (!listed.contains(h)) n++;
        return n;
    }

    private View tailnetStatus() {
        boolean inst = Tailnet.installed(a), on = inst && Tailnet.vpnActive(a);
        LinearLayout c = ui.card();
        LinearLayout r = ui.row();
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(ui.dp(14), ui.dp(12), ui.dp(8), ui.dp(12));
        View dot = new View(a);
        dot.setBackground(ui.oval(on ? ui.green : inst ? ui.orange : ui.fgA(0.3f)));
        r.addView(dot, Ui.lp(ui.dp(8), ui.dp(8)));
        LinearLayout t = ui.col();
        t.setPadding(ui.dp(12), 0, ui.dp(8), 0);
        t.addView(ui.text(!inst ? L.t("Tailscale не установлен") : on ? L.t("Tailscale включён") : L.t("Tailscale выключен"), 15, ui.fg, false));
        TextView h = ui.text(!inst ? L.t("Нужен на телефоне и на ТВ, чтобы управлять ТВ не из дома.")
                : on ? L.t("ТВ из tailnet добавляется один раз — по адресу ниже.")
                : L.t("Включите его, чтобы ТВ из tailnet были на связи."), 12, ui.fgA(0.5f), false);
        h.setPadding(0, ui.dp(2), 0, 0);
        t.addView(h);
        r.addView(t, Ui.lpW(1));
        TextView b = ui.text(!inst ? L.t("Установить") : L.t("Открыть"), 14, ui.accent, true);
        b.setPadding(ui.dp(10), ui.dp(8), ui.dp(10), ui.dp(8));
        b.setBackground(ui.rippleRound(0, 10));
        b.setOnClickListener(v -> host.tailscaleHelp());
        r.addView(b);
        c.addView(r);
        return c;
    }

    private View card(Discovery.Device d) {
        boolean current = d.host.equals(host.current());
        boolean adbOnlyDev = "adb".equals(d.model);
        Probe p = probes.get(d.host);
        LinearLayout c = ui.card();
        if (current) c.setBackground(ui.round(ui.accA(0.14f), 14));
        LinearLayout r = ui.row();
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(ui.dp(14), ui.dp(12), ui.dp(10), ui.dp(12));
        r.addView(ui.icon(adbOnlyDev ? "phone" : "tv_play", current ? ui.accent : ui.fgA(0.7f), 24), Ui.lp(ui.dp(26), ui.dp(26)));
        LinearLayout t = ui.col();
        t.setPadding(ui.dp(12), 0, ui.dp(8), 0);
        TextView name = ui.text(d.name, 16, ui.fg, true);
        name.setSingleLine(true);
        name.setEllipsize(TextUtils.TruncateAt.END);
        t.addView(name);
        List<String> bits = new ArrayList<>();
        String model = d.model == null || "manual".equals(d.model) || adbOnlyDev ? null : d.model;
        if (model != null && !model.isEmpty() && !model.equals(d.name)) bits.add(model);
        bits.add(d.host);
        TextView sub = ui.text(String.join(" · ", bits), 12, ui.fgA(0.55f), false);
        sub.setSingleLine(true);
        sub.setEllipsize(TextUtils.TruncateAt.END);
        sub.setPadding(0, ui.dp(2), 0, 0);
        t.addView(sub);
        // what answers: Cast · remote · ADB · Samsung, plus pairing and origin
        List<String> tags = new ArrayList<>();
        if (p != null && p.cast) tags.add("Cast");
        if (host.paired(d.host)) tags.add(L.t("пульт ✓"));
        else if (p != null && p.remote) tags.add(L.t("пульт"));
        if (p != null && p.samsung) tags.add("Samsung");
        if (p != null && p.adb) tags.add("ADB");
        if (Tailnet.isTailnetHost(d.host)) tags.add(p != null && p.any() ? "Tailscale · " + p.ms + " " + L.t("мс") : "Tailscale");
        else if (host.manual(d)) tags.add(L.t("добавлен по IP"));
        if (adbOnlyDev) tags.add(L.t("LIVE и ADB, без пульта и Cast"));
        if (!tags.isEmpty()) {
            TextView tg = ui.text(String.join("  ·  ", tags), 12, ui.accent, false);
            tg.setPadding(0, ui.dp(4), 0, 0);
            tg.setSingleLine(true);
            tg.setEllipsize(TextUtils.TruncateAt.END);
            t.addView(tg);
        }
        r.addView(t, Ui.lpW(1));
        // online: green answers now, red doesn't, nothing while unknown
        if (p != null) {
            View dot = new View(a);
            dot.setBackground(ui.oval(p.any() ? ui.green : ui.red));
            dot.setContentDescription(p.any() ? L.t("В сети") : L.t("Не в сети"));
            r.addView(dot, ui.margins(Ui.lp(ui.dp(8), ui.dp(8)), 4, 0, 6, 0));
        }
        if (current) {
            TextView mark = ui.text("✓", 18, ui.accent, true);
            mark.setContentDescription(L.t("Сейчас выбран"));
            mark.setPadding(ui.dp(6), 0, ui.dp(6), 0);
            r.addView(mark);
        }
        if (host.manual(d)) {
            TextView x = ui.text("✕", 15, ui.fgA(0.55f), false);
            x.setGravity(Gravity.CENTER);
            x.setContentDescription(L.t("Убрать этот ТВ из списка"));
            x.setBackground(ui.rippleOval(0));
            x.setOnClickListener(v -> { host.forget(d); sync(); });
            r.addView(x, Ui.lp(ui.dp(36), ui.dp(36)));
        }
        c.addView(r);
        c.setOnClickListener(v -> {
            if (adbOnlyDev) host.addAdbOnly(d.host, d.name); else host.select(d);
            dlg.dismiss();
        });
        c.setForeground(ui.rippleRound(0, 14));
        return c;
    }

    // ------------------------------------------------------------ radar

    /** Three rings spreading from the middle while the search runs. */
    private final class Radar extends View {
        private final Paint pt = new Paint(Paint.ANTI_ALIAS_FLAG);
        private long t0;
        private boolean on;

        Radar(Context c) { super(c); pt.setStyle(Paint.Style.STROKE); }

        void start() { t0 = SystemClock.uptimeMillis(); on = true; invalidate(); }
        void stop() { on = false; invalidate(); }

        @Override protected void onDraw(Canvas cv) {
            float w = getWidth(), m = w / 2f;
            pt.setStyle(Paint.Style.FILL);
            pt.setColor(ui.accA(0.16f));
            cv.drawCircle(m, m, w * .2f, pt);
            Ui.Glyph g = new Ui.Glyph("tv_play", ui.accent, 1.6f);
            int s = Math.round(w * .26f);
            g.setBounds(Math.round(m - s / 2f), Math.round(m - s / 2f), Math.round(m + s / 2f), Math.round(m + s / 2f));
            g.draw(cv);
            if (!on) return;
            pt.setStyle(Paint.Style.STROKE);
            pt.setStrokeWidth(ui.dp(1.6f));
            long t = SystemClock.uptimeMillis() - t0;
            for (int k = 0; k < 3; k++) {
                float f = ((t + k * 600L) % 1800L) / 1800f;           // 0..1, one ring every 0.6 s
                pt.setColor(Ui.alpha(ui.accent, (1 - f) * 0.7f));
                cv.drawCircle(m, m, w * (.2f + .3f * f), pt);
            }
            postInvalidateOnAnimation();
        }
    }
}
