// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
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
 *  A public repository needs no token. While it is private, GitHub answers 404 without one:
 *  then the user pastes their own read-only token once; it stays in this phone's prefs and is
 *  never built into the APK. A 403 without a token is GitHub's hourly limit, not "private".
 *
 *  The download is installed with PackageInstaller: Android shows its own confirm screen,
 *  and the update goes over the old app (same signing key), keeping pairing and settings. */
final class Updater {
    static final String REPO = "orfixe/fling-port";
    private static final String API = "https://api.github.com/repos/" + REPO;
    private static final String WEB = "https://github.com/" + REPO;    // the fallback: no API, another host
    private static final long DAY = 24L * 60 * 60 * 1000;

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

    /** Quietly once a day from onResume: says something only when there is a newer version. */
    void checkDaily() {
        long last = prefs.getLong("upd_checked", 0);
        if (System.currentTimeMillis() - last < DAY) return;
        check(false);
    }

    /** `manual`: the user asked, so report "up to date" and errors too. */
    void check(boolean manual) {
        if (busy) return;
        busy = true;
        bg.execute(() -> {
            Release r = null; String err = null; int code = 0;
            try {
                HttpURLConnection c = open(API + "/releases/latest", "application/vnd.github+json");
                code = c.getResponseCode();
                if (code == 200) r = Release.parse(readAll(c.getInputStream()));
                c.disconnect();
            } catch (Exception e) {
                // api.github.com unreachable (network, filter): github.com/…/releases/latest names the tag too
                try { r = Release.fromWeb(); }
                catch (Exception e2) { err = why(e, "api.github.com"); }
            }
            final Release rel = r; final String why = err; final int status = code;
            main.post(() -> {
                busy = false;
                if (rel != null) prefs.edit().putLong("upd_checked", System.currentTimeMillis()).apply();
                if (a.isFinishing()) return;
                String have = installedVersion(a);
                if (rel != null && newer(rel.version, have)) offer(rel, have);
                else if (!manual) return;
                else if (rel != null) toast(L.f("У вас последняя версия (%s).", have));
                else if (rel == null && why == null && status == 403 && token().isEmpty())
                    toast(L.t("GitHub ограничил число проверок с этого адреса. Попробуйте через час."));   // 60 an hour without a token
                else if (rel == null && why == null && (status == 404 || status == 401 || status == 403)) askToken(!token().isEmpty());
                else toast(L.t("Не удалось проверить обновления: ") + (why != null ? why : "HTTP " + status));
            });
        });
    }

    // ---- UI

    private void offer(Release r, String have) {
        if (r.apkId == 0 && (r.tag == null || r.tag.isEmpty())) { toast(L.f("В версии %s нет файла Fling.apk.", r.version)); return; }
        new AlertDialog.Builder(a)
                .setTitle(L.f("Доступна версия %s", r.version))
                .setMessage(L.f("У вас %s. Скачать и установить обновление? Связь с ТВ и настройки сохранятся.", have))
                .setPositiveButton(L.t("Обновить"), (d, w) -> download(r))
                .setNegativeButton(L.t("Позже"), null)
                .show();
    }

    /** The repository is private: ask for a GitHub token (read-only, this repository only). */
    private void askToken(boolean rejected) {          // rejected: a saved token got no access
        EditText e = ui.field("github_pat_…", true);
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        e.setText(token());
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

    private void download(Release r) {
        // Android 8+: Fling needs "Install unknown apps" once; the system asks on its own screen
        if (!a.getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(a)
                    .setTitle(L.t("Разрешите установку"))
                    .setMessage(L.t("Чтобы Fling мог поставить своё обновление, разрешите ему «Установку неизвестных приложений» и нажмите «Обновить» ещё раз."))
                    .setPositiveButton(L.t("Открыть настройки"), (d, w) -> {
                        try { a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:" + a.getPackageName()))); } catch (Exception ignored) {}
                    })
                    .setNegativeButton(L.t("Отмена"), null)
                    .show();
            return;
        }
        AlertDialog progress = new AlertDialog.Builder(a)
                .setTitle(L.f("Обновление до %s", r.version))
                .setMessage(L.t("Скачиваю…"))
                .setCancelable(false)
                .show();
        bg.execute(() -> {
            File apk = new File(a.getCacheDir(), "fling-update.apk");
            String err = null;
            try {
                fetchAsset(r, apk, pct -> main.post(() -> progress.setMessage(L.f("Скачиваю… %d%%", pct))),
                        msg -> main.post(() -> progress.setMessage(msg)));
                main.post(() -> progress.setMessage(L.t("Открываю установку…")));
                install(apk);
            } catch (Exception e) { err = e.getMessage() != null ? e.getMessage() : e.toString(); }
            final String why = err;
            main.post(() -> {
                try { progress.dismiss(); } catch (Exception ignored) {}
                if (why != null && !a.isFinishing()) failed(r, why);
            });
        });
    }

    /** The download failed every way: say why, and offer the browser (it may take another route). */
    private void failed(Release r, String why) {
        new AlertDialog.Builder(a)
                .setTitle(L.t("Обновление не скачалось"))
                .setMessage(why)
                .setPositiveButton(L.t("Повторить"), (d, w) -> download(r))
                .setNeutralButton(L.t("Скачать в браузере"), (d, w) -> {
                    try { a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(r.apkUrl()))); } catch (Exception ignored) {}
                })
                .setNegativeButton(L.t("Отмена"), null)
                .show();
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

    private void toast(String s) { Toast.makeText(a, s, Toast.LENGTH_LONG).show(); }

    // ---- network

    private String token() { return prefs.getString("gh_token", ""); }

    private HttpURLConnection open(String url, String accept) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(10000); c.setReadTimeout(20000);
        c.setRequestProperty("Accept", accept);
        c.setRequestProperty("User-Agent", "Fling-Android");
        c.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
        if (!token().isEmpty()) c.setRequestProperty("Authorization", "Bearer " + token());
        return c;
    }

    interface Progress { void at(int pct); }
    interface Status { void say(String msg); }

    /** Release asset → file. Two sources — the API asset, then the github.com download link — and
     *  each is tried 3 times; a dropped connection resumes where it stopped (Range). GitHub
     *  redirects both to a signed storage URL, fetched without our Authorization header (the
     *  storage rejects two kinds of auth). Android checks the APK's signature on install. */
    private void fetchAsset(Release r, File out, Progress p, Status s) throws Exception {
        java.util.List<String> urls = new java.util.ArrayList<>();
        if (r.apkId != 0) urls.add(API + "/releases/assets/" + r.apkId);
        urls.add(r.apkUrl());
        if (out.exists()) out.delete();
        Exception last = null; String lastHost = "github.com";
        for (String url : urls) {
            for (int attempt = 1; attempt <= 3; attempt++) {
                try {
                    fetchOnce(url, r.apkSize, out, p);
                    if (r.apkSize > 0 && out.length() != r.apkSize) {
                        out.delete();
                        throw new java.io.EOFException(L.t("файл скачался не полностью"));
                    }
                    return;
                } catch (HttpStatus e) {
                    last = e; lastHost = e.host;
                    out.delete();
                    break;                                       // this source answers no: the next one
                } catch (Exception e) {
                    last = e; lastHost = hostOf(e, url);
                    if (attempt < 3) {
                        s.say(L.f("Связь оборвалась, повторяю (%d из 3)…", attempt + 1));
                        try { Thread.sleep(1500L * attempt); } catch (InterruptedException ie) { throw e; }
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

    private String currentHost = "";

    private String hostOf(Exception e, String fallback) {
        return currentHost.isEmpty() ? Uri.parse(fallback).getHost() : currentHost;
    }

    /** One try: follows the redirects by hand (the token goes to api.github.com only) and appends
     *  to what an earlier try already wrote. */
    private void fetchOnce(String url, long size, File out, Progress p) throws Exception {
        long have = out.exists() ? out.length() : 0;
        HttpURLConnection c = null;
        int code = 0;
        for (int hop = 0; hop < 6; hop++) {
            URL u = new URL(url);
            currentHost = u.getHost();
            c = (HttpURLConnection) u.openConnection();
            c.setInstanceFollowRedirects(false);
            c.setConnectTimeout(15000); c.setReadTimeout(30000);
            c.setRequestProperty("User-Agent", "Fling-Android");
            c.setRequestProperty("Accept", "application/octet-stream");
            if (u.getHost().equals("api.github.com")) {
                c.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
                if (!token().isEmpty()) c.setRequestProperty("Authorization", "Bearer " + token());
            }
            if (have > 0) c.setRequestProperty("Range", "bytes=" + have + "-");
            code = c.getResponseCode();
            if (code / 100 != 3) break;
            String loc = c.getHeaderField("Location");
            c.disconnect();
            if (loc == null) throw new HttpStatus(currentHost, code);
            url = new URL(u, loc).toString();
        }
        if (code == 200) have = 0;                                 // the whole file (no resume): start over
        else if (code != 206) { c.disconnect(); throw new HttpStatus(currentHost, code); }
        long total = size > 0 ? size : have + Math.max(0, c.getContentLengthLong());
        try (InputStream in = c.getInputStream(); OutputStream o = new FileOutputStream(out, have > 0)) {
            byte[] buf = new byte[64 * 1024];
            long done = have; int last = -1, n;
            while ((n = in.read(buf)) > 0) {
                o.write(buf, 0, n);
                done += n;
                int pct = total > 0 ? (int) Math.min(100, 100 * done / total) : 0;
                if (pct != last) { last = pct; p.at(pct); }
            }
        } finally { c.disconnect(); currentHost = ""; }
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

    private void install(File apk) throws Exception {
        PackageInstaller pi = a.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams sp = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        sp.setAppPackageName(a.getPackageName());
        sp.setSize(apk.length());
        int id = pi.createSession(sp);
        try (PackageInstaller.Session s = pi.openSession(id)) {
            try (InputStream in = new FileInputStream(apk); OutputStream o = s.openWrite("fling.apk", 0, apk.length())) {
                byte[] buf = new byte[64 * 1024]; int n;
                while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
                s.fsync(o);
            }
            Intent done = new Intent(a, Result.class);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
            PendingIntent pend = PendingIntent.getBroadcast(a, id, done, flags);
            s.commit(pend.getIntentSender());
        }
    }

    /** PackageInstaller's answer: show Android's confirm screen, or say why it failed. */
    public static final class Result extends BroadcastReceiver {
        @Override public void onReceive(Context ctx, Intent i) {
            int st = i.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
            if (st == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                Intent confirm = i.getParcelableExtra(Intent.EXTRA_INTENT);
                if (confirm != null) {
                    confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    try { ctx.startActivity(confirm); } catch (Exception ignored) {}
                }
            } else if (st != PackageInstaller.STATUS_SUCCESS) {
                String msg = i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
                if (st == PackageInstaller.STATUS_FAILURE_CONFLICT || st == PackageInstaller.STATUS_FAILURE_INCOMPATIBLE)
                    msg = L.t("подпись нового APK не совпадает с установленным — удалите старый Fling и поставьте новый вручную.");
                Toast.makeText(ctx, L.t("Обновление не установилось: ") + (msg != null ? msg : st), Toast.LENGTH_LONG).show();
            }
        }
    }

    // ---- release info

    static final class Release {
        String version, tag, url; long apkId; long apkSize;

        /** The github.com link to the APK (the browser and the fallback download use it). */
        String apkUrl() {
            return url != null && !url.isEmpty() ? url : WEB + "/releases/download/" + tag + "/Fling.apk";
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
