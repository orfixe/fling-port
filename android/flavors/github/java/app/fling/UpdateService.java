// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.SystemClock;

import java.io.File;

/** Downloads an update in the background (Updater.fetchAsset), the progress in a notification
 *  with "Cancel"; then hands the APK to the package installer (Updater.install), which answers
 *  through Updater.Result: the confirm screen, or "tap to install". A failure becomes a
 *  notification with "Try again" and "Download in the browser". */
public final class UpdateService extends Service {
    static final String CANCEL = "app.fling.update.CANCEL";
    static volatile boolean running;

    private volatile boolean cancelled;
    private Thread worker;
    private long lastNote;

    /** Starts the download of `r` (from a screen: the app is in the foreground). */
    static void start(Context ctx, Updater.Release r) {
        ctx.startForegroundService(r.into(new Intent(ctx, UpdateService.class)));
    }

    /** "Update" / "Try again" in a notification. */
    static PendingIntent pending(Context ctx, Updater.Release r) {
        Intent i = r.into(new Intent(ctx, UpdateService.class));
        return PendingIntent.getForegroundService(ctx, 10, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        L.init(this);
        if (intent != null && CANCEL.equals(intent.getAction())) {
            cancelled = true;
            if (worker != null) worker.interrupt();
            finish();
            return START_NOT_STICKY;
        }
        Updater.Release r = Updater.Release.from(intent);
        Notification first = progress(r == null ? "" : r.version, -1, L.t("Подключаюсь к GitHub…"));
        if (Build.VERSION.SDK_INT >= 29) startForeground(Updater.N_PROGRESS, first, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        else startForeground(Updater.N_PROGRESS, first);
        if (r == null || running) {                              // nothing to do, or already downloading
            if (!running) finish();
            return START_NOT_STICKY;
        }
        running = true;
        cancelled = false;
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null) { nm.cancel(Updater.N_AVAILABLE); nm.cancel(Updater.N_FAILED); }
        worker = new Thread(() -> run(r), "fling-update");
        worker.start();
        return START_NOT_STICKY;
    }

    private void run(Updater.Release r) {
        File apk = new File(getCacheDir(), "fling-update.apk");
        String status = L.t("Скачиваю…");
        final String[] say = {status};
        try {
            Updater.fetchAsset(Updater.prefs(this), r, apk,
                    pct -> show(r.version, pct, say[0]),
                    msg -> { say[0] = msg; show(r.version, -1, msg); },
                    () -> cancelled);
            if (cancelled) return;
            show(r.version, 100, L.t("Открываю установку…"));
            Updater.install(this, apk, r.version);
        } catch (InterruptedException e) {
            apk.delete();                                        // cancelled
        } catch (Exception e) {
            if (!cancelled) Updater.notifyFailed(this, r, e.getMessage() != null ? e.getMessage() : e.toString());
        } finally {
            finish();
        }
    }

    /** At most every 300 ms (the system drops faster notification updates anyway). */
    private void show(String version, int pct, String text) {
        long now = SystemClock.uptimeMillis();
        if (pct >= 0 && pct < 100 && now - lastNote < 300) return;
        lastNote = now;
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null && running) nm.notify(Updater.N_PROGRESS, progress(version, pct, pct >= 0 && pct < 100 ? L.f("Скачиваю… %d%%", pct) : text));
    }

    private Notification progress(String version, int pct, String text) {
        PendingIntent cancel = PendingIntent.getService(this, 11, new Intent(this, UpdateService.class).setAction(CANCEL),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Updater.note(this, Updater.CH_PROGRESS)
                .setContentTitle(version.isEmpty() ? L.t("Обновление Fling") : L.f("Обновление Fling %s", version))
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setProgress(100, Math.max(0, pct), pct < 0)
                .addAction(new Notification.Action.Builder(null, L.t("Отмена"), cancel).build());
        if (Build.VERSION.SDK_INT >= 31) b.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        return b.build();
    }

    private void finish() {
        running = false;
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }
}
