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
 *  The repository is private for now, so GitHub only answers with a token. The user pastes
 *  their own read-only token once; it stays in this phone's prefs and is never built into
 *  the APK. If the repository goes public, no token is needed at all.
 *
 *  The download is installed with PackageInstaller: Android shows its own confirm screen,
 *  and the update goes over the old app (same signing key), keeping pairing and settings. */
final class Updater {
    static final String REPO = "orfixe/fling-port";
    private static final String API = "https://api.github.com/repos/" + REPO;
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
            } catch (Exception e) { err = e.getMessage(); }
            final Release rel = r; final String why = err; final int status = code;
            main.post(() -> {
                busy = false;
                if (rel != null) prefs.edit().putLong("upd_checked", System.currentTimeMillis()).apply();
                if (a.isFinishing()) return;
                String have = installedVersion(a);
                if (rel != null && newer(rel.version, have)) offer(rel, have);
                else if (!manual) return;
                else if (rel != null) toast(L.f("У вас последняя версия (%s).", have));
                else if (rel == null && why == null && (status == 404 || status == 401 || status == 403)) askToken(!token().isEmpty());
                else toast(L.t("Не удалось проверить обновления: ") + (why != null ? why : "HTTP " + status));
            });
        });
    }

    // ---- UI

    private void offer(Release r, String have) {
        if (r.apkId == 0) { toast(L.f("В версии %s нет файла Fling.apk.", r.version)); return; }
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
                fetchAsset(r, apk, pct -> main.post(() -> progress.setMessage(L.f("Скачиваю… %d%%", pct))));
                main.post(() -> progress.setMessage(L.t("Открываю установку…")));
                install(apk);
            } catch (Exception e) { err = e.getMessage() != null ? e.getMessage() : e.toString(); }
            final String why = err;
            main.post(() -> {
                try { progress.dismiss(); } catch (Exception ignored) {}
                if (why != null) toast(L.t("Обновление не скачалось: ") + why);
            });
        });
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

    /** Release asset → file. GitHub answers with a redirect to a signed storage URL, which must be
     *  fetched without our Authorization header (the storage rejects two kinds of auth). */
    private void fetchAsset(Release r, File out, Progress p) throws Exception {
        HttpURLConnection c = open(API + "/releases/assets/" + r.apkId, "application/octet-stream");
        c.setInstanceFollowRedirects(false);
        int code = c.getResponseCode();
        if (code / 100 == 3) {
            String loc = c.getHeaderField("Location");
            c.disconnect();
            c = (HttpURLConnection) new URL(loc).openConnection();
            c.setConnectTimeout(10000); c.setReadTimeout(30000);
            c.setRequestProperty("User-Agent", "Fling-Android");
            code = c.getResponseCode();
        }
        if (code != 200) throw new Exception("HTTP " + code);
        long total = c.getContentLengthLong() > 0 ? c.getContentLengthLong() : r.apkSize;
        try (InputStream in = c.getInputStream(); OutputStream o = new FileOutputStream(out)) {
            byte[] buf = new byte[64 * 1024];
            long done = 0; int last = -1, n;
            while ((n = in.read(buf)) > 0) {
                o.write(buf, 0, n);
                done += n;
                int pct = total > 0 ? (int) (100 * done / total) : 0;
                if (pct != last) { last = pct; p.at(pct); }
            }
        } finally { c.disconnect(); }
        if (r.apkSize > 0 && out.length() != r.apkSize) throw new Exception(L.t("файл скачался не полностью"));
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
        String version; long apkId; long apkSize;

        static Release parse(String json) throws Exception {
            JSONObject o = new JSONObject(json);
            Release r = new Release();
            r.version = o.optString("tag_name", "").replaceFirst("^[vV]", "");
            JSONArray as = o.optJSONArray("assets");
            for (int k = 0; as != null && k < as.length(); k++) {
                JSONObject x = as.getJSONObject(k);
                if ("Fling.apk".equals(x.optString("name"))) { r.apkId = x.optLong("id"); r.apkSize = x.optLong("size"); }
            }
            return r;
        }
    }

    /** "0.3.10" > "0.3.9": compare number by number. */
    static boolean newer(String remote, String local) {
        String[] r = remote.split("[^0-9]+"), l = local.split("[^0-9]+");
        for (int k = 0; k < Math.max(r.length, l.length); k++) {
            int x = k < r.length && !r[k].isEmpty() ? Integer.parseInt(r[k]) : 0;
            int y = k < l.length && !l[k].isEmpty() ? Integer.parseInt(l[k]) : 0;
            if (x != y) return x > y;
        }
        return false;
    }
}
