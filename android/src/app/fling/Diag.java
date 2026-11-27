// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Fling's own log and the report for the developer.
 *
 *  log(): a line with the time into files/logs/app.log (256 KB, then app.log.old) and logcat.
 *  A crash is written there with its stack before Android closes the app.
 *  report(): one text file — the app, the phone, the settings without secrets, the log and
 *  this process's logcat — shared through Android's share sheet (mail, a messenger, Drive…),
 *  to attach to a GitHub issue or send to whoever asked for it. Nothing is sent by itself.
 *  Secrets are cleaned out first: tokens, keys, e-mails, MACs, public IP addresses. */
final class Diag {
    private Diag() {}

    private static File dir;
    private static final Object LOCK = new Object();
    private static final long MAX = 256 * 1024;

    static void init(Context ctx) {
        synchronized (LOCK) {
            if (dir != null) return;
            dir = new File(ctx.getApplicationContext().getFilesDir(), "logs");
            dir.mkdirs();
        }
        final Thread.UncaughtExceptionHandler prev = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((t, e) -> {
            StringWriter sw = new StringWriter();
            e.printStackTrace(new PrintWriter(sw));
            log("crash", "thread " + t.getName() + ": " + sw);
            if (prev != null) prev.uncaughtException(t, e);
        });
        log("app", "start " + BuildConfig.VERSION_NAME + " (" + BuildConfig.VERSION_CODE + ", "
                + BuildConfig.GIT_COMMIT + ") Android " + Build.VERSION.RELEASE + " API " + Build.VERSION.SDK_INT);
    }

    static void log(String tag, String msg) {
        Log.i("Fling/" + tag, msg == null ? "null" : msg);
        File d = dir;
        if (d == null) return;
        String line = new SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.ROOT).format(new Date())
                + " " + tag + ": " + msg + "\n";
        synchronized (LOCK) {
            try {
                File f = new File(d, "app.log");
                if (f.length() > MAX) f.renameTo(new File(d, "app.log.old"));
                try (FileOutputStream o = new FileOutputStream(f, true)) { o.write(line.getBytes(StandardCharsets.UTF_8)); }
            } catch (Exception ignored) {
            }
        }
    }

    // ---- the report

    static void report(Activity a) {
        log("app", "report requested");
        new Thread(() -> {
            String text;
            try { text = clean(build(a)); }
            catch (Exception e) { text = "report failed: " + e; }
            final String body = text;
            a.runOnUiThread(() -> share(a, body));
        }, "fling-report").start();
    }

    private static String build(Context ctx) {
        StringBuilder b = new StringBuilder();
        b.append("Fling report — attach this file to an issue: https://github.com/orfixe/fling-port/issues\n");
        section(b, "app");
        b.append("Fling ").append(BuildConfig.VERSION_NAME).append(" (").append(BuildConfig.VERSION_CODE).append(") ")
                .append(BuildConfig.FLAVOR).append(" · ").append(BuildConfig.GIT_COMMIT).append(" · ")
                .append(BuildConfig.BUILD_DATE).append('\n');
        b.append("time: ").append(new SimpleDateFormat("yyyy-MM-dd HH:mm:ss Z", Locale.ROOT).format(new Date())).append('\n');
        section(b, "phone");
        b.append(Build.MANUFACTURER).append(' ').append(Build.MODEL).append(" · Android ").append(Build.VERSION.RELEASE)
                .append(" (API ").append(Build.VERSION.SDK_INT).append(") · ").append(Locale.getDefault()).append('\n');
        android.util.DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
        b.append("screen: ").append(dm.widthPixels).append('x').append(dm.heightPixels).append(" @ ").append(dm.densityDpi).append(" dpi\n");
        section(b, "settings (secrets removed)");
        Map<String, ?> all = ctx.getSharedPreferences("fling", Context.MODE_PRIVATE).getAll();
        for (String k : new java.util.TreeSet<>(all.keySet())) {
            Object v = all.get(k);
            boolean secret = SECRET_NAME.matcher(k).find();
            String s = secret ? "<hidden>" : String.valueOf(v);
            if (s.length() > 600) s = s.substring(0, 600) + "…";
            b.append(k).append(" = ").append(s).append('\n');
        }
        section(b, "log");
        synchronized (LOCK) {
            if (dir != null) {
                b.append(read(new File(dir, "app.log.old")));
                b.append(read(new File(dir, "app.log")));
            }
        }
        section(b, "logcat (this app)");
        b.append(logcat());
        return b.toString();
    }

    private static void section(StringBuilder b, String t) { b.append("\n== ").append(t).append(" ==========\n"); }

    private static String read(File f) {
        if (!f.exists()) return "";
        try { return new String(java.nio.file.Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8); }
        catch (Exception e) { return "[" + f.getName() + ": " + e + "]\n"; }
    }

    /** The app may read its own lines of logcat (no permission needed): crashes, the system's
     *  warnings about it. The last 1500 lines. */
    private static String logcat() {
        try {
            Process p = new ProcessBuilder("logcat", "-d", "-v", "time", "-t", "1500",
                    "--pid=" + android.os.Process.myPid()).redirectErrorStream(true).start();
            StringBuilder out = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String l;
                while ((l = r.readLine()) != null) out.append(l).append('\n');
            }
            p.waitFor();
            return out.toString();
        } catch (Exception e) {
            return "[logcat: " + e + "]\n";
        }
    }

    private static void share(Activity a, String body) {
        try {
            File rd = new File(a.getCacheDir(), "reports");
            rd.mkdirs();
            File[] old = rd.listFiles();
            if (old != null) for (File f : old) f.delete();
            String name = "fling-report-" + new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date()) + ".txt";
            File f = new File(rd, name);
            try (FileOutputStream o = new FileOutputStream(f)) { o.write(body.getBytes(StandardCharsets.UTF_8)); }
            Uri u = androidx.core.content.FileProvider.getUriForFile(a, a.getPackageName() + ".files", f);
            Intent send = new Intent(Intent.ACTION_SEND)
                    .setType("text/plain")
                    .putExtra(Intent.EXTRA_STREAM, u)
                    .putExtra(Intent.EXTRA_SUBJECT, "Fling " + BuildConfig.VERSION_NAME + " report")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            send.setClipData(ClipData.newRawUri(name, u));
            a.startActivity(Intent.createChooser(send, L.t("Отправить отчёт")));
        } catch (Exception e) {
            log("app", "report share failed: " + e);
            android.widget.Toast.makeText(a, L.t("Отчёт не получился: ") + e.getMessage(), android.widget.Toast.LENGTH_LONG).show();
        }
    }

    // ---- cleaning

    private static final Pattern SECRET_NAME = Pattern.compile("(?i)(token|secret|passw|key|cookie|auth|pin|cert)");
    private static final Pattern[] RULES = {
            Pattern.compile("\\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
            Pattern.compile("(?i)\\b(bearer|token)(\\s+|=|:\\s*)[A-Za-z0-9._~+/=-]{8,}"),
            Pattern.compile("(?i)([?&](?:token|key|sig|signature|auth|access_token|code)=)[^&\\s\"']+"),
            Pattern.compile("\\b[\\w.+-]+@[\\w-]+\\.[\\w.-]+\\b"),
            Pattern.compile("\\b((?:[0-9A-Fa-f]{2}[:-]){4})([0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2})\\b"),
    };
    private static final String[] REPL = {"<token>", "$1$2<hidden>", "$1<hidden>", "<email>", "xx:xx:xx:xx:$2"};
    private static final Pattern IP = Pattern.compile("\\b(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\b");

    static String clean(String s) {
        for (int i = 0; i < RULES.length; i++) s = RULES[i].matcher(s).replaceAll(REPL[i]);
        Matcher m = IP.matcher(s);
        StringBuffer out = new StringBuffer();
        while (m.find()) {
            int a = num(m.group(1)), b = num(m.group(2));
            // home / hotel networks, Tailscale (100.64/10), loopback, link-local and VPN
            // fake-IP ranges explain problems; a public address only identifies the user
            boolean local = a == 10 || a == 127 || a == 0 || (a == 192 && b == 168) || (a == 172 && b >= 16 && b <= 31)
                    || (a == 100 && b >= 64 && b <= 127) || (a == 169 && b == 254) || (a == 198 && (b == 18 || b == 19))
                    || a >= 224 || a > 255;
            m.appendReplacement(out, local ? Matcher.quoteReplacement(m.group()) : "<public-ip>");
        }
        m.appendTail(out);
        return out.toString();
    }

    private static int num(String s) { try { return Integer.parseInt(s); } catch (Exception e) { return 999; } }
}
