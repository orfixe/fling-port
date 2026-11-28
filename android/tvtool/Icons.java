// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling.tv;

import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import android.os.Build;
import android.os.StatFs;
import android.os.SystemClock;
import android.util.Base64;
import android.util.DisplayMetrics;

import java.io.ByteArrayOutputStream;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Fling's helper on the TV (app_process as the shell user; pushed over ADB, a few KB).
 *    <size> <pkg|component>...  each package's label and icon (PNG, base64) — the tiles
 *    apps <size>                every app the TV's launcher shows, with label and icon
 *    info                       the TV: model, Android, screen, memory, storage, uptime
 *  One line per item, fields split by tabs. Reads only; changes nothing on the TV. */
public final class Icons {
    private static PackageManager pm;

    public static void main(String[] args) throws Exception {
        android.os.Looper.prepareMainLooper();
        Object at = Class.forName("android.app.ActivityThread").getMethod("systemMain").invoke(null);
        Context ctx = (Context) at.getClass().getMethod("getSystemContext").invoke(at);
        pm = ctx.getPackageManager();
        if (args.length > 0 && args[0].equals("info")) info(ctx);
        else if (args.length > 1 && args[0].equals("apps")) apps(Integer.parseInt(args[1]));
        else {
            int size = Integer.parseInt(args[0]);
            for (int i = 1; i < args.length; i++) one(args[i], size);
        }
        System.out.flush();
        System.exit(0);
    }

    private static void one(String pkg, int size) {
        if (pkg.indexOf('/') > 0) {                     // a component (the screensaver): its own name
            try {
                android.content.ComponentName cn = android.content.ComponentName.unflattenFromString(pkg);
                System.out.println(pkg + "\t" + clean(pm.getServiceInfo(cn, 0).loadLabel(pm)) + "\t");
            } catch (Throwable t) {
                System.out.println(pkg + "\t\t");
            }
            return;
        }
        try {
            ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
            System.out.println(pkg + "\t" + clean(pm.getApplicationLabel(ai)) + "\t" + png(icon(ai), size));
        } catch (Throwable t) {
            System.out.println(pkg + "\t\t");
        }
    }

    /** What the launcher lists (Leanback first, then phone-style apps), each once. */
    private static void apps(int size) {
        Set<String> seen = new HashSet<>();
        for (String cat : new String[]{Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER}) {
            List<ResolveInfo> l = pm.queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(cat), 0);
            for (ResolveInfo r : l) {
                String pkg = r.activityInfo.packageName;
                if (seen.add(pkg)) one(pkg, size);
            }
        }
    }

    /** The sharpest icon the app has: its xxxhdpi drawable, not whatever the TV's density picks. */
    private static Drawable icon(ApplicationInfo ai) {
        try {
            if (ai.icon != 0) {
                Drawable d = pm.getResourcesForApplication(ai).getDrawableForDensity(ai.icon, DisplayMetrics.DENSITY_XXXHIGH, null);
                if (d != null) return d;
            }
        } catch (Throwable ignored) {}
        return pm.getApplicationIcon(ai);
    }

    private static String png(Drawable d, int size) {
        Bitmap b = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        d.setBounds(0, 0, size, size);
        d.draw(new Canvas(b));
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        b.compress(Bitmap.CompressFormat.PNG, 100, o);
        return Base64.encodeToString(o.toByteArray(), Base64.NO_WRAP);
    }

    private static void info(Context ctx) {
        kv("manufacturer", Build.MANUFACTURER);
        kv("model", Build.MODEL);
        kv("android", Build.VERSION.RELEASE);
        kv("security", Build.VERSION.SECURITY_PATCH);
        try {
            DisplayMetrics m = new DisplayMetrics();
            android.view.Display d = ((android.hardware.display.DisplayManager) ctx.getSystemService(Context.DISPLAY_SERVICE)).getDisplay(0);
            d.getRealMetrics(m);
            kv("screen", m.widthPixels + "x" + m.heightPixels);
            kv("refresh", String.valueOf(Math.round(d.getRefreshRate())));
        } catch (Throwable ignored) {}
        try {
            ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
            ((ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE)).getMemoryInfo(mi);
            kv("ram_total", String.valueOf(mi.totalMem));
            kv("ram_free", String.valueOf(mi.availMem));
        } catch (Throwable ignored) {}
        try {
            StatFs s = new StatFs("/data");
            kv("disk_total", String.valueOf(s.getTotalBytes()));
            kv("disk_free", String.valueOf(s.getAvailableBytes()));
        } catch (Throwable ignored) {}
        kv("uptime", String.valueOf(SystemClock.elapsedRealtime() / 1000));
        kv("apps", String.valueOf(pm.getInstalledPackages(0).size()));
    }

    private static void kv(String k, String v) { System.out.println(k + "\t" + (v == null ? "" : v)); }

    private static String clean(CharSequence s) { return String.valueOf(s).replace('\t', ' ').replace('\n', ' '); }
}
