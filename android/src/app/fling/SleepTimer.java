// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

/** Sleep timer: an alarm that turns the paired TV off, even with Fling closed and the phone asleep. */
public class SleepTimer extends BroadcastReceiver {

    private static SharedPreferences prefs(Context c) { return c.getSharedPreferences("fling", Context.MODE_PRIVATE); }

    private static PendingIntent pi(Context c) {
        return PendingIntent.getBroadcast(c, 0, new Intent(c, SleepTimer.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** When the timer fires (System.currentTimeMillis()), or 0 when it is off. */
    static long endsAt(Context c) {
        long t = prefs(c).getLong("sleepAt", 0);
        return t > System.currentTimeMillis() ? t : 0;
    }

    static void set(Context c, String host, int minutes) {
        long at = System.currentTimeMillis() + minutes * 60_000L;
        prefs(c).edit().putLong("sleepAt", at).putString("sleepHost", host).apply();
        AlarmManager am = c.getSystemService(AlarmManager.class);
        if (Build.VERSION.SDK_INT >= 31 && !am.canScheduleExactAlarms())
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi(c));   // may come a few minutes late
        else
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi(c));
    }

    static void cancel(Context c) {
        prefs(c).edit().remove("sleepAt").remove("sleepHost").apply();
        c.getSystemService(AlarmManager.class).cancel(pi(c));
    }

    @Override public void onReceive(Context c, Intent i) {
        L.init(c);
        String host = prefs(c).getString("sleepHost", null);
        prefs(c).edit().remove("sleepAt").remove("sleepHost").apply();
        if (host == null) return;
        final PendingResult pr = goAsync();   // the network work runs on the remote's own thread
        final Atv atv = new Atv(c.getApplicationContext(), new Atv.Listener() {
            public void onPower(Boolean on) {}
            public void onApp(String pkg) {}
            public void onPairing(String state, String message) {}
            public void onError(String message) {}
            public void onVoice(boolean on) {}
        });
        if (!atv.paired(host)) { pr.finish(); return; }
        atv.powerOff(host, () -> { atv.disconnect(); pr.finish(); });
    }
}
