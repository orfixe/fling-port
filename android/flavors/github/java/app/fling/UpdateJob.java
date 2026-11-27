// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;

/** The daily update check, also while Fling is closed: Android runs it about once a day when
 *  there is a network (and again after a reboot). A newer version comes as a notification with
 *  "Update"; each version is announced once. Off in Settings → "Tell me about new versions".
 *
 *  Not a push service: nothing is sent to the phone; it asks GitHub, as the app itself does. */
public final class UpdateJob extends JobService {
    private static final int ID = 4100;

    static void schedule(Context ctx) {
        JobScheduler js = ctx.getSystemService(JobScheduler.class);
        if (js == null || js.getPendingJob(ID) != null) return;
        js.schedule(new JobInfo.Builder(ID, new ComponentName(ctx, UpdateJob.class))
                .setPeriodic(Updater.DAY, 6L * 60 * 60 * 1000)      // once a day, within a 6 h window
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)                                // survives a reboot
                .build());
    }

    static void cancel(Context ctx) {
        JobScheduler js = ctx.getSystemService(JobScheduler.class);
        if (js != null) js.cancel(ID);
    }

    @Override public boolean onStartJob(JobParameters p) {
        L.init(this);
        new Thread(() -> {
            boolean retry = false;
            try {
                SharedPreferences prefs = Updater.prefs(this);
                if (!prefs.getBoolean("upd_notify", true) || UpdateService.running) return;
                Updater.Check c = Updater.latest(prefs);
                if (c.rel == null) { retry = true; return; }       // offline / GitHub down: later today
                String have = Updater.installedVersion(this);
                if (!Updater.newer(c.rel.version, have) || !c.rel.hasApk()) return;
                if (c.rel.version.equals(prefs.getString("upd_announced", ""))) return;   // said once
                Updater.notifyAvailable(this, c.rel);
                prefs.edit().putString("upd_announced", c.rel.version).apply();
            } catch (Exception ignored) {
            } finally {
                jobFinished(p, retry);
            }
        }, "fling-update-check").start();
        return true;
    }

    @Override public boolean onStopJob(JobParameters p) { return true; }
}
