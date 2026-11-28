// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** The TV in the notification shade and on the lock screen: what is on it now, and quick keys
 *  — power, volume −/+, play/pause, home — without opening Fling.
 *
 *  "What's on" comes from MainActivity while it is open (the remote session reports the app),
 *  otherwise from ADB every 20 s while the phone's screen is on. Keys go over ADB
 *  (`input keyevent`, no pairing needed) or, without ADB, over the paired remote. */
public class TvRemoteService extends Service {
    static final String A_SHOW = "app.fling.tv.SHOW", A_KEY = "app.fling.tv.KEY", A_CLOSE = "app.fling.tv.CLOSE";
    private static final String CHANNEL = "tv-remote";
    private static final int ID = 3;
    private static final long POLL_MS = 20_000;
    private static final Pattern TOP = Pattern.compile("u0 ([A-Za-z][\\w.]*)/");

    static volatile boolean running;
    /** MainActivity is on screen: it holds the remote session, so keys go through it. */
    static volatile Atv foregroundAtv;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService bg = Executors.newSingleThreadExecutor();
    private Adb adb;
    private Atv atv;            // only while Fling isn't on screen and ADB is off
    private String host, name, app;
    private Boolean on;

    /** Shows (or refreshes) the notification for this TV; from MainActivity. */
    static void show(Context c, String host, String name) {
        if (host == null) return;
        try {
            c.startForegroundService(new Intent(c, TvRemoteService.class).setAction(A_SHOW)
                    .putExtra("host", host).putExtra("name", name));
        } catch (Exception e) {
            Diag.log("remote-notify", "can't start: " + e.getMessage());   // background start limits
        }
    }

    static void hide(Context c) { c.stopService(new Intent(c, TvRemoteService.class)); }

    /** What the TV shows now, as MainActivity learns it (null pkg: unknown). */
    static void now(Context c, String pkg, Boolean tvOn) {
        if (!running) return;
        try {
            c.startService(new Intent(c, TvRemoteService.class).setAction(A_SHOW)
                .putExtra("app", pkg == null ? "" : pkg).putExtra("on", tvOn == null ? 0 : tvOn ? 1 : 2));
        } catch (Exception e) {
            Diag.log("remote-notify", "update: " + e.getMessage());
        }
    }

    @Override public void onCreate() {
        super.onCreate();
        running = true;
        adb = new Adb(this);
        NotificationChannel ch = new NotificationChannel(CHANNEL, L.t("Пульт ТВ"), NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);
    }

    @Override public int onStartCommand(Intent i, int flags, int startId) {
        if (i != null && i.hasExtra("host")) {
            String h = i.getStringExtra("host");
            if (!h.equals(host)) { app = null; on = null; closeAtv(); }
            host = h;
            name = i.getStringExtra("name");
        }
        if (i != null && i.hasExtra("app")) {
            String p = i.getStringExtra("app");
            app = p == null || p.isEmpty() ? null : p;
            int o = i.getIntExtra("on", 0);
            on = o == 0 ? null : o == 1;
        }
        try {
            startForeground();
        } catch (Exception e) {                                    // never take Fling down for the notification
            Diag.log("remote-notify", "foreground refused: " + e);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (host == null) { stopSelf(); return START_NOT_STICKY; }
        String a = i == null ? A_SHOW : i.getAction();
        if (A_KEY.equals(a)) key(i.getIntExtra("code", 0));
        else if (A_CLOSE.equals(a)) {
            stopSelf();                                         // swiped away: back the next time Fling opens
            return START_NOT_STICKY;
        }
        main.removeCallbacks(poll);
        main.postDelayed(poll, A_SHOW.equals(a) && i != null && i.hasExtra("app") ? POLL_MS : 500);
        return START_STICKY;
    }

    @Override public void onDestroy() {
        running = false;
        main.removeCallbacks(poll);
        closeAtv();
        bg.shutdownNow();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent i) { return null; }

    private void startForeground() {
        Notification n = build();
        if (Build.VERSION.SDK_INT >= 29) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        else startForeground(ID, n);
    }

    private void update() {
        if (!running) return;
        getSystemService(NotificationManager.class).notify(ID, build());
    }

    private Notification build() {
        String tv = name != null && !name.isEmpty() ? name : host == null ? "Fling" : host;
        String text = Boolean.FALSE.equals(on) ? L.t("ТВ выключен")
                : app != null ? L.f("Сейчас: %s", appLabel(app)) : L.t("Пульт и трансляция на телевизор");
        Intent open = new Intent(this, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        Notification.Builder b = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_tile)
                .setContentTitle(tv)
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setCategory(Notification.CATEGORY_SERVICE)
                .setContentIntent(PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE))
                .setDeleteIntent(pi(A_CLOSE, 0, 90))
                .addAction(action(R.drawable.ic_n_power, L.t("Питание"), Atv.POWER, 91))
                .addAction(action(R.drawable.ic_n_voldown, L.t("Тише"), Atv.VOL_DOWN, 92))
                .addAction(action(R.drawable.ic_live_playpause, L.t("Пауза / пуск"), Atv.PLAY_PAUSE, 93))
                .addAction(action(R.drawable.ic_n_volup, L.t("Громче"), Atv.VOL_UP, 94))
                .addAction(action(R.drawable.ic_n_home, L.t("Домой"), Atv.HOME, 95))
                // icon-only buttons in one row; volume and play/pause also on the lock screen's compact view
                .setStyle(new Notification.MediaStyle().setShowActionsInCompactView(1, 2, 3));
        return b.build();
    }

    private Notification.Action action(int icon, String title, int code, int req) {
        return new Notification.Action.Builder(Icon.createWithResource(this, icon), title, pi(A_KEY, code, req)).build();
    }

    private PendingIntent pi(String action, int code, int req) {
        Intent i = new Intent(this, TvRemoteService.class).setAction(action).putExtra("code", code);
        return PendingIntent.getService(this, req, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private String appLabel(String pkg) {
        String l = TvIcons.label(this, pkg);                       // the TV's own name ("Спящий режим" for Dreamx)
        if (l != null) return l;
        for (String[] a : MainActivity.TV_APPS) if (a[2].equals(pkg)) return a[0];
        String tail = pkg.substring(pkg.lastIndexOf('.') + 1);
        return tail.isEmpty() ? pkg : Character.toUpperCase(tail.charAt(0)) + tail.substring(1);
    }

    /** ADB first (works without pairing); the remote when Fling is open or ADB is off. */
    private void key(int code) {
        final String h = host;
        if (h == null || code == 0) return;
        Atv fg = foregroundAtv;
        if (fg != null) { fg.key(h, code); return; }
        bg.execute(() -> {
            try {
                if (!"ok".equals(Adb.lastState) && Adb.lastState != null) throw new IllegalStateException("adb " + Adb.lastState);
                adb.shell(h, "input keyevent " + code);
                main.removeCallbacks(poll);
                main.postDelayed(poll, 1500);                           // show what the key opened
            } catch (Exception e) {
                main.post(() -> remoteKey(h, code));
            }
        });
    }

    private void remoteKey(String h, int code) {
        if (atv == null) {
            atv = new Atv(this, new Atv.Listener() {
                public void onPower(Boolean v) { main.post(() -> { on = v; update(); }); }
                public void onApp(String pkg) { main.post(() -> { app = pkg; update(); }); }
                public void onPairing(String state, String message) {}
                public void onError(String message) { Diag.log("remote-notify", message); }
                public void onVoice(boolean v) {}
            });
        }
        atv.key(h, code);
    }

    private void closeAtv() {
        if (atv != null) { atv.disconnect(); atv = null; }
    }

    /** What's on the TV, over ADB, while the phone's screen is on and Fling is closed. */
    private final Runnable poll = new Runnable() {
        @Override public void run() {
            final String h = host;
            PowerManager pm = getSystemService(PowerManager.class);
            if (h != null && foregroundAtv == null && (pm == null || pm.isInteractive()) && !"closed".equals(Adb.lastState)
                    && !"unauthorized".equals(Adb.lastState)) {
                bg.execute(() -> {
                    String pkg = null;
                    try {
                        String out = adb.shell(h, "dumpsys activity activities | grep -m1 topResumedActivity");
                        Matcher m = TOP.matcher(out);
                        if (m.find()) pkg = m.group(1);
                    } catch (Exception ignored) {}
                    final String p = pkg;
                    main.post(() -> {
                        if (p != null && !p.equals(app)) { app = p; on = true; update(); }
                        if (p != null) TvIcons.learn(TvRemoteService.this, adb, h, p, () -> main.post(TvRemoteService.this::update));
                    });
                });
            }
            main.postDelayed(this, POLL_MS);
        }
    };
}
