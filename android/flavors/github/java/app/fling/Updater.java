// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.Manifest;
import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Updates Fling itself from the newest GitHub release of the port (asset "Fling.apk").
 *
 *  - Checking: once a day in the background (UpdateJob, also when Fling is closed) and when the
 *    app opens; a newer version comes as a notification ("Update" downloads it).
 *  - Downloading: in the background (UpdateService), with the progress in a notification; the
 *    app can be closed meanwhile. Two sources, retries, a dropped connection resumes.
 *  - Installing: PackageInstaller. Android shows its own confirm screen: at once when Fling is on
 *    screen, otherwise from the "Tap to install" notification (Android doesn't let an app in the
 *    background open a screen). The update goes over the old app (same signing key), keeping
 *    pairing and settings.
 *
 *  A public repository needs no token; a 404 asks for one (a private fork). */
final class Updater {
    static final String REPO = "orfixe/fling-port";
    private static final String API = "https://api.github.com/repos/" + REPO;
    private static final String WEB = "https://github.com/" + REPO;    // the fallback: no API, another host
    static final long DAY = 24L * 60 * 60 * 1000;

    static final String CH_UPDATES = "updates", CH_PROGRESS = "update_progress";
    static final int N_AVAILABLE = 4101, N_PROGRESS = 4102, N_READY = 4103, N_FAILED = 4104;

    private final Activity a;
    private final Ui ui;
    private final SharedPreferences prefs;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService bg = Executors.newSingleThreadExecutor();
    private boolean busy;

    Updater(Activity a, Ui ui, SharedPreferences prefs) {
        this.a = a; this.ui = ui; this.prefs = prefs;
    }

    static String installedVersion(Context ctx) {
        try { return ctx.getPackageManager().getPackageInfo(ctx.getPackageName(), 0).versionName; }
        catch (Exception e) { return "0"; }
    }

    static SharedPreferences prefs(Context ctx) { return ctx.getSharedPreferences("fling", Context.MODE_PRIVATE); }

    /** The daily background check on or off (Settings), and notifications that are out of date:
     *  "version X is ready" once X is installed. Called when the app starts and from Settings. */
    static void background(Context ctx) {
        SharedPreferences p = prefs(ctx);
        if (p.getBoolean("upd_notify", true)) UpdateJob.schedule(ctx); else UpdateJob.cancel(ctx);
        String have = installedVersion(ctx), ready = p.getString("upd_ready", "");
        if (!ready.isEmpty() && !newer(ready, have)) {
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            if (nm != null) { nm.cancel(N_READY); nm.cancel(N_AVAILABLE); }
            p.edit().remove("upd_ready").apply();
        }
    }

    /** From the screens' onResume: back from Android's settings with the permission given →
     *  the "Update" window comes up again by itself. */
    void resumed() {
        // kept in prefs: Android may restart Fling while its settings screen is open
        Release r = Release.ofJson(prefs.getString("upd_await", ""));
        if (r == null || a.isFinishing()) return;
        if (!a.getPackageManager().canRequestPackageInstalls()) return;   // not given (yet): keep waiting
        prefs.edit().remove("upd_await").apply();
        if (!newer(r.version, installedVersion(a))) return;
        main.post(() -> { if (!a.isFinishing()) offer(r, installedVersion(a)); });
    }

    /** Quietly once a day from onResume: says something only when there is a newer version. */
    void checkDaily() {
        background(a);
        long last = prefs.getLong("upd_checked", 0);
        if (System.currentTimeMillis() - last < DAY) return;
        check(false);
    }

    /** `manual`: the user asked, so report "up to date" and errors too. */
    void check(boolean manual) {
        if (busy) return;
        if (UpdateService.running) { if (manual) toast(L.t("Обновление уже скачивается — ход загрузки в уведомлении.")); return; }
        busy = true;
        bg.execute(() -> {
            Check c = latest(prefs);
            main.post(() -> {
                busy = false;
                if (a.isFinishing()) return;
                String have = installedVersion(a);
                boolean skipped = c.rel != null && c.rel.version.equals(prefs.getString("upd_skip", ""));
                if (c.rel != null && newer(c.rel.version, have) && (manual || !skipped)) offer(c.rel, have);
                else if (!manual) return;
                else if (c.rel != null) toast(L.f("У вас последняя версия (%s).", have));
                else if (c.err == null && c.code == 403 && token(prefs).isEmpty())
                    toast(L.t("GitHub ограничил число проверок с этого адреса. Попробуйте через час."));   // 60 an hour without a token
                else if (c.err == null && (c.code == 404 || c.code == 401 || c.code == 403)) askToken(!token(prefs).isEmpty());
                else toast(L.t("Не удалось проверить обновления: ") + (c.err != null ? c.err : "HTTP " + c.code));
            });
        });
    }

    // ---- UI

    private void offer(Release r, String have) {
        if (!r.hasApk()) { toast(L.f("В версии %s нет файла Fling.apk.", r.version)); return; }
        new AlertDialog.Builder(a)
                .setTitle(L.f("Доступна версия %s", r.version))
                .setMessage(L.f("У вас %s. Обновление скачается в фоне — ход загрузки будет в уведомлении, Fling можно закрыть. Связь с ТВ и настройки сохранятся.", have))
                .setPositiveButton(L.t("Обновить"), (d, w) -> { prefs.edit().remove("upd_skip").apply(); download(r); })
                .setNeutralButton(L.t("Пропустить эту версию"), (d, w) -> skip(a, r.version))
                .setNegativeButton(L.t("Позже"), null)
                .show();
    }

    /** "Skip this version": no more offers or notifications for it; the next one comes as usual. */
    static void skip(Context ctx, String version) {
        prefs(ctx).edit().putString("upd_skip", version).apply();
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(N_AVAILABLE);
    }

    /** "Skip this version" in the notification. */
    public static final class Skip extends BroadcastReceiver {
        @Override public void onReceive(Context ctx, Intent i) {
            String v = i.getStringExtra("version");
            if (v != null) skip(ctx, v);
        }
    }

    /** The repository is private: ask for a GitHub token (read-only, this repository only). */
    private void askToken(boolean rejected) {          // rejected: a saved token got no access
        EditText e = ui.field("github_pat_…", true);
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        e.setText(token(prefs));
        FrameLayout f = new FrameLayout(a);
        f.setPadding(ui.dp(20), ui.dp(8), ui.dp(20), 0);
        f.addView(e);
        new AlertDialog.Builder(a)
                .setTitle(rejected ? L.t("Токен не подошёл") : L.t("Нужен токен GitHub"))
                .setMessage(L.t("Репозиторий Fling приватный, поэтому GitHub отдаёт обновления только по токену. "
                        + "github.com → Settings → Developer settings → Fine-grained tokens → Generate: "
                        + "Repository access — только fling-port, Permissions → Contents — Read-only. "
                        + "Токен хранится только на этом телефоне."))
                .setView(f)
                .setPositiveButton(L.t("Сохранить и проверить"), (d, w) -> {
                    prefs.edit().putString("gh_token", e.getText().toString().trim()).apply();
                    check(true);
                })
                .setNeutralButton(L.t("Открыть GitHub"), (d, w) -> {
                    try { a.startActivity(new Intent(Intent.ACTION_VIEW,
                            Uri.parse("https://github.com/settings/personal-access-tokens/new"))); } catch (Exception ignored) {}
                })
                .setNegativeButton(L.t("Отмена"), null)
                .show();
    }

    /** "Update": the permissions it needs, then the download in the background. */
    private void download(Release r) {
        // Android 8+: Fling needs "Install unknown apps" once; the system asks on its own screen
        if (!a.getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(a)
                    .setTitle(L.t("Разрешите установку"))
                    .setMessage(L.t("Чтобы Fling мог поставить своё обновление, разрешите ему «Установку неизвестных приложений». Когда вернётесь, Fling сразу предложит обновиться."))
                    .setPositiveButton(L.t("Открыть настройки"), (d, w) -> {
                        prefs.edit().putString("upd_await", r.json()).apply();   // resumed(): offer it again once allowed
                        try { a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:" + a.getPackageName()))); } catch (Exception e) { prefs.edit().remove("upd_await").apply(); }
                    })
                    .setNegativeButton(L.t("Отмена"), null)
                    .show();
            return;
        }
        // Android 13+: the progress and "tap to install" are notifications
        if (Build.VERSION.SDK_INT >= 33 && a.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            a.requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 4100);
        UpdateService.start(a, r);
        toast(L.t("Скачиваю обновление в фоне — ход загрузки в уведомлении."));
    }

    private void toast(String s) { Toast.makeText(a, s, Toast.LENGTH_LONG).show(); }

    // ================================================================ shared: UI, UpdateJob, UpdateService

    static final class Check { Release rel; String err; int code; }

    /** The newest release: the API, then the releases page when api.github.com can't be reached. */
    static Check latest(SharedPreferences prefs) {
        Check c = new Check();
        try {
            HttpURLConnection h = open(prefs, API + "/releases/latest", "application/vnd.github+json");
            c.code = h.getResponseCode();
            if (c.code == 200) c.rel = Release.parse(readAll(h.getInputStream()));
            h.disconnect();
        } catch (Exception e) {
            Diag.log("update", "api.github.com: " + e);
            try { c.rel = Release.fromWeb(); }
            catch (Exception e2) { c.err = why(e, "api.github.com"); Diag.log("update", "github.com: " + e2); }
        }
        Diag.log("update", "latest: " + (c.rel != null ? c.rel.version : "none (HTTP " + c.code + ")"));
        if (c.rel != null) prefs.edit().putLong("upd_checked", System.currentTimeMillis()).apply();
        return c;
    }

    private static String token(SharedPreferences prefs) { return prefs.getString("gh_token", ""); }

    private static HttpURLConnection open(SharedPreferences prefs, String url, String accept) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(10000); c.setReadTimeout(20000);
        c.setRequestProperty("Accept", accept);
        c.setRequestProperty("User-Agent", "Fling-Android");
        c.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
        if (!token(prefs).isEmpty()) c.setRequestProperty("Authorization", "Bearer " + token(prefs));
        return c;
    }

    /** An exception as a sentence: what failed, with which server, and what to try. */
    static String why(Exception e, String host) {
        if (e instanceof java.net.UnknownHostException)
            return L.f("Не найден сервер %s: нет интернета или DNS не отвечает.", host);
        if (e instanceof java.net.SocketTimeoutException)
            return L.f("Сервер %s не ответил вовремя. Попробуйте ещё раз или в другой сети.", host);
        if (e instanceof javax.net.ssl.SSLException || e instanceof java.io.EOFException
                || e instanceof java.net.SocketException || e instanceof java.net.ProtocolException
                || String.valueOf(e.getMessage()).toLowerCase(java.util.Locale.ROOT).contains("closed"))
            return L.f("Соединение с %s обрывается — так бывает, когда сеть или провайдер его блокирует. "
                    + "Попробуйте другую сеть (Wi‑Fi ↔ мобильный интернет) или VPN, либо скачайте в браузере.", host);
        String m = e.getMessage();
        return host + ": " + (m == null ? e.getClass().getSimpleName() : m);
    }

    private static final int TRIES = 5;                   // per source

    interface Progress { void at(int pct); }
    interface Status { void say(String msg); }
    interface Cancelled { boolean now(); }

    /** Release asset → file. Two sources — the API asset, then the github.com download link — and
     *  each is tried 3 times; a dropped connection resumes where it stopped (Range). GitHub
     *  redirects both to a signed storage URL, fetched without our Authorization header (the
     *  storage rejects two kinds of auth). Android checks the APK's signature on install. */
    static void fetchAsset(SharedPreferences prefs, Release r, File out, Progress p, Status s, Cancelled stop) throws Exception {
        java.util.List<String> urls = new java.util.ArrayList<>();
        if (r.apkId != 0) urls.add(API + "/releases/assets/" + r.apkId);
        urls.add(r.apkUrl());
        if (out.exists()) out.delete();
        Exception last = null; String lastHost = "github.com";
        String[] host = {""};
        for (String url : urls) {
            for (int attempt = 1; attempt <= TRIES; attempt++) {
                if (stop.now()) throw new InterruptedException();
                try {
                    fetchOnce(prefs, url, r.apkSize, out, p, host, stop);
                    if (r.apkSize > 0 && out.length() != r.apkSize) {
                        out.delete();
                        throw new java.io.EOFException(L.t("файл скачался не полностью"));
                    }
                    return;
                } catch (InterruptedException e) {
                    throw e;
                } catch (HttpStatus e) {
                    last = e; lastHost = e.host;
                    out.delete();
                    break;                                       // this source answers no: the next one
                } catch (Exception e) {
                    last = e; lastHost = host[0].isEmpty() ? Uri.parse(url).getHost() : host[0];
                    if (attempt < TRIES) {
                        s.say(L.f("Связь оборвалась, повторяю (%d из %d)…", attempt + 1, TRIES));
                        Thread.sleep(Math.min(15000L, 3000L * attempt));   // the storage drops connections in bursts
                    }
                }
            }
        }
        out.delete();
        throw new Exception(last instanceof HttpStatus ? last.getMessage() : why(last, lastHost));
    }

    /** A server answered, but not with the file. */
    private static final class HttpStatus extends Exception {
        final String host;
        HttpStatus(String host, int code) { super(host + ": HTTP " + code); this.host = host; }
    }

    /** One try: follows the redirects by hand (the token goes to api.github.com only) and appends
     *  to what an earlier try already wrote. host[0]: the server being talked to, for messages. */
    private static void fetchOnce(SharedPreferences prefs, String url, long size, File out, Progress p,
                                  String[] host, Cancelled stop) throws Exception {
        long have = out.exists() ? out.length() : 0;
        HttpURLConnection c = null;
        int code = 0;
        for (int hop = 0; hop < 6; hop++) {
            URL u = new URL(url);
            host[0] = u.getHost();
            c = (HttpURLConnection) u.openConnection();
            c.setInstanceFollowRedirects(false);
            c.setConnectTimeout(15000); c.setReadTimeout(30000);
            c.setRequestProperty("User-Agent", "Fling-Android");
            c.setRequestProperty("Accept", "application/octet-stream");
            if (u.getHost().equals("api.github.com")) {
                c.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
                if (!token(prefs).isEmpty()) c.setRequestProperty("Authorization", "Bearer " + token(prefs));
            }
            if (have > 0) c.setRequestProperty("Range", "bytes=" + have + "-");
            code = c.getResponseCode();
            if (code / 100 != 3) break;
            String loc = c.getHeaderField("Location");
            c.disconnect();
            if (loc == null) throw new HttpStatus(host[0], code);
            url = new URL(u, loc).toString();
        }
        if (code == 200) have = 0;                                 // the whole file (no resume): start over
        else if (code != 206) { c.disconnect(); throw new HttpStatus(host[0], code); }
        long total = size > 0 ? size : have + Math.max(0, c.getContentLengthLong());
        try (InputStream in = c.getInputStream(); OutputStream o = new FileOutputStream(out, have > 0)) {
            byte[] buf = new byte[64 * 1024];
            long done = have; int last = -1, n;
            while ((n = in.read(buf)) > 0) {
                if (stop.now()) throw new InterruptedException();
                o.write(buf, 0, n);
                done += n;
                int pct = total > 0 ? (int) Math.min(100, 100 * done / total) : 0;
                if (pct != last) { last = pct; p.at(pct); }
            }
        } finally { c.disconnect(); host[0] = ""; }
    }

    private static String readAll(InputStream in) throws Exception {
        try (InputStream i = in) {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[16 * 1024]; int n;
            while ((n = i.read(buf)) > 0) b.write(buf, 0, n);
            return b.toString("UTF-8");
        }
    }

    // ---- install

    static void install(Context ctx, File apk, String version) throws Exception {
        PackageInstaller pi = ctx.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams sp = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        sp.setAppPackageName(ctx.getPackageName());
        sp.setSize(apk.length());
        if (Build.VERSION.SDK_INT >= 31) sp.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
        int id = pi.createSession(sp);
        try (PackageInstaller.Session s = pi.openSession(id)) {
            try (InputStream in = new FileInputStream(apk); OutputStream o = s.openWrite("fling.apk", 0, apk.length())) {
                byte[] buf = new byte[64 * 1024]; int n;
                while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
                s.fsync(o);
            }
            Intent done = new Intent(ctx, Result.class).putExtra("version", version);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
            PendingIntent pend = PendingIntent.getBroadcast(ctx, id, done, flags);
            s.commit(pend.getIntentSender());
        }
    }

    /** PackageInstaller's answer: Android's confirm screen (at once if Fling is on screen, else
     *  from a notification), or why it failed. */
    public static final class Result extends BroadcastReceiver {
        @Override public void onReceive(Context ctx, Intent i) {
            L.init(ctx);
            int st = i.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
            String version = i.getStringExtra("version");
            Diag.log("update", "installer: status " + st + " for " + version + " " + i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE));
            if (st == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                Intent confirm = i.getParcelableExtra(Intent.EXTRA_INTENT);
                if (confirm == null) return;
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                notifyReady(ctx, version, confirm);             // stays until installed (or tapped)
                if (onScreen()) try { ctx.startActivity(confirm); } catch (Exception ignored) {}
            } else if (st != PackageInstaller.STATUS_SUCCESS) {
                String msg = i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
                if (st == PackageInstaller.STATUS_FAILURE_CONFLICT || st == PackageInstaller.STATUS_FAILURE_INCOMPATIBLE)
                    msg = L.t("подпись нового APK не совпадает с установленным — удалите старый Fling и поставьте новый вручную.");
                else if (st == PackageInstaller.STATUS_FAILURE_ABORTED)
                    msg = L.t("установка отменена.");
                notifyFailed(ctx, null, L.t("Обновление не установилось: ") + (msg != null ? msg : st));
            }
        }
    }

    /** Fling has a screen in front of the user (it may then open the installer itself). */
    static boolean onScreen() {
        ActivityManager.RunningAppProcessInfo info = new ActivityManager.RunningAppProcessInfo();
        ActivityManager.getMyMemoryState(info);
        return info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND;
    }

    // ---- notifications

    static void channels(Context ctx) {
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) return;
        NotificationChannel u = new NotificationChannel(CH_UPDATES, L.t("Обновления Fling"), NotificationManager.IMPORTANCE_DEFAULT);
        u.setDescription(L.t("Новая версия вышла, скачана или не скачалась."));
        nm.createNotificationChannel(u);
        NotificationChannel p = new NotificationChannel(CH_PROGRESS, L.t("Загрузка обновления"), NotificationManager.IMPORTANCE_LOW);
        p.setDescription(L.t("Ход загрузки новой версии."));
        p.setShowBadge(false);
        nm.createNotificationChannel(p);
    }

    static Notification.Builder note(Context ctx, String channel) {
        channels(ctx);
        Intent open = new Intent(ctx, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return new Notification.Builder(ctx, channel)
                .setSmallIcon(R.drawable.ic_n_update)
                .setColor(ctx.getColor(R.color.acc_blue))
                .setContentIntent(PendingIntent.getActivity(ctx, 0, open, PendingIntent.FLAG_IMMUTABLE));
    }

    private static void post(Context ctx, int id, Notification n) {
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) return;
        if (Build.VERSION.SDK_INT >= 33 && ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        nm.notify(id, n);
    }

    /** "Version X is out" with "Update": the download starts in the background. */
    static void notifyAvailable(Context ctx, Release r) {
        String have = installedVersion(ctx);
        Notification n = note(ctx, CH_UPDATES)
                .setContentTitle(L.f("Доступна версия Fling %s", r.version))
                .setContentText(L.f("У вас %s. Нажмите «Обновить» — скачается в фоне.", have))
                .setAutoCancel(true)
                .addAction(new Notification.Action.Builder(null, L.t("Обновить"), UpdateService.pending(ctx, r)).build())
                .addAction(new Notification.Action.Builder(null, L.t("Пропустить эту версию"),
                        PendingIntent.getBroadcast(ctx, 12, new Intent(ctx, Skip.class).putExtra("version", r.version),
                                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT)).build())
                .build();
        post(ctx, N_AVAILABLE, n);
    }

    static void notifyReady(Context ctx, String version, Intent confirm) {
        prefs(ctx).edit().putString("upd_ready", version == null ? "" : version).apply();
        int flags = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT;
        Notification n = note(ctx, CH_UPDATES)
                .setContentTitle(version == null ? L.t("Обновление скачано") : L.f("Fling %s скачан", version))
                .setContentText(L.t("Нажмите, чтобы установить."))
                .setContentIntent(PendingIntent.getActivity(ctx, 1, confirm, flags))
                .setAutoCancel(true)
                .build();
        post(ctx, N_READY, n);
    }

    /** r != null: the download failed, offer it again and the browser. */
    static void notifyFailed(Context ctx, Release r, String why) {
        Diag.log("update", "failed: " + why);
        Notification.Builder b = note(ctx, CH_UPDATES)
                .setContentTitle(L.t("Обновление не скачалось"))
                .setContentText(why)
                .setStyle(new Notification.BigTextStyle().bigText(why))
                .setAutoCancel(true);
        if (r != null) {
            b.addAction(new Notification.Action.Builder(null, L.t("Повторить"), UpdateService.pending(ctx, r)).build());
            Intent web = new Intent(Intent.ACTION_VIEW, Uri.parse(r.apkUrl())).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            b.addAction(new Notification.Action.Builder(null, L.t("Скачать в браузере"),
                    PendingIntent.getActivity(ctx, 2, web, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT)).build());
        }
        post(ctx, N_FAILED, b.build());
    }

    // ---- release info

    static final class Release {
        String version, tag, url; long apkId; long apkSize;

        boolean hasApk() { return apkId != 0 || (tag != null && !tag.isEmpty()); }

        /** The github.com link to the APK (the browser and the fallback download use it). */
        String apkUrl() {
            return url != null && !url.isEmpty() ? url : WEB + "/releases/download/" + tag + "/Fling.apk";
        }

        String json() {
            try {
                return new JSONObject().put("version", version).put("tag", tag).put("url", url)
                        .put("id", apkId).put("size", apkSize).toString();
            } catch (Exception e) { return ""; }
        }

        static Release ofJson(String s) {
            if (s == null || s.isEmpty()) return null;
            try {
                JSONObject o = new JSONObject(s);
                Release r = new Release();
                r.version = o.getString("version");
                r.tag = o.optString("tag", null);
                r.url = o.optString("url", null);
                r.apkId = o.optLong("id");
                r.apkSize = o.optLong("size");
                return r;
            } catch (Exception e) { return null; }
        }

        Intent into(Intent i) {
            return i.putExtra("upd_version", version).putExtra("upd_tag", tag).putExtra("upd_url", url)
                    .putExtra("upd_id", apkId).putExtra("upd_size", apkSize);
        }

        static Release from(Intent i) {
            if (i == null || i.getStringExtra("upd_version") == null) return null;
            Release r = new Release();
            r.version = i.getStringExtra("upd_version");
            r.tag = i.getStringExtra("upd_tag");
            r.url = i.getStringExtra("upd_url");
            r.apkId = i.getLongExtra("upd_id", 0);
            r.apkSize = i.getLongExtra("upd_size", 0);
            return r;
        }

        /** Without the API: github.com/…/releases/latest redirects to …/releases/tag/<tag>. */
        static Release fromWeb() throws Exception {
            HttpURLConnection c = (HttpURLConnection) new URL(WEB + "/releases/latest").openConnection();
            c.setInstanceFollowRedirects(false);
            c.setConnectTimeout(10000); c.setReadTimeout(20000);
            c.setRequestProperty("User-Agent", "Fling-Android");
            int code = c.getResponseCode();
            String loc = c.getHeaderField("Location");
            c.disconnect();
            if (code / 100 != 3 || loc == null || !loc.contains("/releases/tag/")) throw new Exception("HTTP " + code);
            Release r = new Release();
            r.tag = Uri.decode(loc.substring(loc.lastIndexOf('/') + 1));
            r.version = r.tag.replaceFirst("^[vV]", "");
            return r;
        }

        static Release parse(String json) throws Exception {
            JSONObject o = new JSONObject(json);
            Release r = new Release();
            r.tag = o.optString("tag_name", "");
            r.version = r.tag.replaceFirst("^[vV]", "");
            JSONArray as = o.optJSONArray("assets");
            for (int k = 0; as != null && k < as.length(); k++) {
                JSONObject x = as.getJSONObject(k);
                if ("Fling.apk".equals(x.optString("name"))) {
                    r.apkId = x.optLong("id"); r.apkSize = x.optLong("size"); r.url = x.optString("browser_download_url");
                }
            }
            return r;
        }
    }

    /** "0.3.10" > "0.3.9": compare number by number. */
    static boolean newer(String remote, String local) {
        if (remote == null || local == null) return false;
        String[] r = remote.split("[^0-9]+"), l = local.split("[^0-9]+");
        for (int k = 0; k < Math.max(r.length, l.length); k++) {
            int c = cmpNum(k < r.length ? r[k] : "", k < l.length ? l[k] : "");
            if (c != 0) return c > 0;
        }
        return false;
    }

    /** Compares two digit strings of any length (no parseInt: "20261125000000" overflowed). */
    private static int cmpNum(String a, String b) {
        a = a.replaceFirst("^0+", ""); b = b.replaceFirst("^0+", "");
        if (a.length() != b.length()) return Integer.compare(a.length(), b.length());
        return a.compareTo(b);
    }
}
