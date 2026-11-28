// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

/** The real names and icons of the TV's apps, read on the TV itself.
 *
 *  ADB alone gives only package names (labels and icons are resources inside each APK). So a
 *  3 KB program (assets/tv-icons.dex, source android/tvtool/Icons.java) is pushed to
 *  /data/local/tmp — as scrcpy-server is — and run with app_process: it asks the TV's
 *  PackageManager and prints "package TAB label TAB base64 PNG" per app. Icons are kept on the
 *  phone (files/tv-icons), names in prefs, so the tiles show them without ADB later. */
final class TvIcons {
    private static final String DEVICE_PATH = "/data/local/tmp/fling-icons.dex";
    private static final int SIZE = 96;
    private static final Pattern PKG = Pattern.compile("[A-Za-z][\\w]*(\\.[\\w]+)+");
    private static final Map<String, Bitmap> mem = new ConcurrentHashMap<>();
    private static final Map<String, String> labels = new ConcurrentHashMap<>();
    private static final java.util.Set<String> asked = java.util.Collections.newSetFromMap(new ConcurrentHashMap<>());

    private TvIcons() {}

    private static File dir(Context c) {
        File d = new File(c.getFilesDir(), "tv-icons");
        //noinspection ResultOfMethodCallIgnored
        d.mkdirs();
        return d;
    }

    private static SharedPreferences names(Context c) { return c.getSharedPreferences("tv-icons", Context.MODE_PRIVATE); }

    /** The icon read from the TV before, or null. */
    static Bitmap icon(Context c, String pkg) {
        if (pkg == null || !PKG.matcher(pkg).matches()) return null;
        Bitmap b = mem.get(pkg);
        if (b != null) return b;
        File f = new File(dir(c), pkg + ".png");
        if (!f.isFile()) return null;
        b = BitmapFactory.decodeFile(f.getPath());
        if (b != null) mem.put(pkg, b);
        return b;
    }

    /** The app's own name as the TV shows it, read before; or null. */
    static String label(Context c, String pkg) {
        if (pkg == null) return null;
        String l = labels.get(pkg);
        if (l == null && (l = names(c).getString(pkg, null)) != null) labels.put(pkg, l);
        return l;
    }

    /** The name read before, without a Context (null if this run hasn't seen it yet). */
    static String peek(String pkg) { return pkg == null ? null : labels.get(pkg); }

    /** The TV's own name and icon of the app on screen now, once per run, when not known yet
     *  ("Dreamx" is the TV's "Sleep mode", say). Off the UI thread; then runs `then` on success. */
    static void learn(Context c, Adb adb, String host, String pkg, Runnable then) {
        if (pkg == null || host == null || label(c, pkg) != null || !asked.add(pkg)) return;
        new Thread(() -> {
            try {
                if (fetch(c, adb, host, java.util.Collections.singletonList(pkg)) > 0 && then != null) then.run();
            } catch (Exception e) {
                Diag.log("icons", "name of " + pkg + ": " + e.getMessage());
            }
        }, "tv-label").start();
    }

    /** Which of these the phone has no icon for yet. */
    static List<String> missing(Context c, List<String> pkgs) {
        List<String> out = new ArrayList<>();
        for (String p : pkgs) if (p != null && PKG.matcher(p).matches() && !new File(dir(c), p + ".png").isFile()) out.add(p);
        return out;
    }

    /** Reads names and icons of these packages from the TV over ADB (blocking: call off the UI
     *  thread). Returns how many came back. */
    static int fetch(Context c, Adb adb, String host, List<String> pkgs) throws Exception {
        List<String> ok = new ArrayList<>();
        for (String p : pkgs) if (p != null && PKG.matcher(p).matches() && !ok.contains(p)) ok.add(p);
        if (ok.isEmpty()) return 0;
        byte[] dex = asset(c);
        int got = 0;
        try (AdbLink link = AdbLink.open(adb, host, 5555)) {
            String have = "";
            try { have = link.shell("sha256sum " + DEVICE_PATH + " 2>/dev/null", 5000).trim(); } catch (Exception ignored) {}
            if (!have.startsWith(sha256(dex))) link.push(dex, DEVICE_PATH, 0100644);
            StringBuilder cmd = new StringBuilder("CLASSPATH=" + DEVICE_PATH + " app_process / app.fling.tv.Icons " + SIZE);
            for (String p : ok) cmd.append(' ').append(p);   // validated package names only: they go through the TV's shell
            String out = link.shell(cmd.toString(), 60000);
            SharedPreferences.Editor e = names(c).edit();
            for (String line : out.split("\n")) {
                String[] f = line.trim().split("\t", -1);
                if (f.length < 3 || !ok.contains(f[0]) || f[2].isEmpty()) continue;
                try {
                    byte[] png = Base64.decode(f[2], Base64.DEFAULT);
                    Bitmap b = BitmapFactory.decodeByteArray(png, 0, png.length);
                    if (b == null) continue;
                    try (FileOutputStream o = new FileOutputStream(new File(dir(c), f[0] + ".png"))) { o.write(png); }
                    mem.put(f[0], b);
                    if (!f[1].trim().isEmpty()) { e.putString(f[0], f[1].trim()); labels.put(f[0], f[1].trim()); }
                    got++;
                } catch (IllegalArgumentException ignored) {}   // a broken line: skip it
            }
            e.apply();
        }
        return got;
    }

    private static byte[] asset(Context c) throws Exception {
        try (InputStream in = c.getAssets().open("tv-icons.dex")) {
            ByteArrayOutputStream o = new ByteArrayOutputStream();
            byte[] b = new byte[8192];
            for (int n; (n = in.read(b)) > 0; ) o.write(b, 0, n);
            return o.toByteArray();
        }
    }

    private static String sha256(byte[] d) throws Exception {
        StringBuilder s = new StringBuilder();
        for (byte x : MessageDigest.getInstance("SHA-256").digest(d)) s.append(String.format("%02x", x));
        return s.toString();
    }
}
