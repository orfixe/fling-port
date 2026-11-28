// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.drawable.ColorDrawable;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.widget.FrameLayout;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

/** Settings: theme and accent colour (with a live preview), remote behaviour, language, updates. */
public class SettingsActivity extends Activity {
    private Ui ui;
    private SharedPreferences prefs;
    private LinearLayout root;
    private Updater updater;

    @Override protected void attachBaseContext(Context base) { super.attachBaseContext(base); Ui.applyNight(this, base); }

    @Override protected void onCreate(Bundle b) {
        L.init(this);
        Ui.applyAccent(this);
        super.onCreate(b);
        ui = new Ui(this);
        prefs = getSharedPreferences("fling", MODE_PRIVATE);
        updater = new Updater(this, ui, prefs);
        build();
    }

    /** Theme, accent and language apply at once: the screen is rebuilt in the new look. */
    private void restyle() {
        recreate();
    }

    private void build() {
        getWindow().setBackgroundDrawable(new ColorDrawable(ui.page));
        getWindow().setStatusBarColor(0);
        getWindow().setNavigationBarColor(0);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            getWindow().getDecorView();                  // creates the decor: no controller before it (Android 17 crashed)
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                c.setSystemBarsAppearance(ui.dark ? 0 : light, light);
            }
        } else {
            int f = View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION;
            if (!ui.dark) f |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            getWindow().getDecorView().setSystemUiVisibility(f);
        }
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(ui.page);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        root = ui.col();
        root.setPadding(ui.dp(14), ui.dp(10), ui.dp(14), ui.dp(24));
        FrameLayout center = new FrameLayout(this);
        center.addView(root, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL));
        scroll.addView(center);
        root.addOnLayoutChangeListener((v, l, t, r, bt, ol, ot, or, ob) -> {   // tablets: a centred column, like the main screen
            int max = ui.dp(480);
            if (r - l > max && v.getLayoutParams().width != max) { v.getLayoutParams().width = max; v.requestLayout(); }
        });
        setContentView(scroll);
        scroll.setOnApplyWindowInsetsListener((v, ins) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets i = ins.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                v.setPadding(i.left, i.top, i.right, i.bottom);
            } else {
                v.setPadding(ins.getSystemWindowInsetLeft(), ins.getSystemWindowInsetTop(), ins.getSystemWindowInsetRight(), ins.getSystemWindowInsetBottom());
            }
            return Build.VERSION.SDK_INT >= 30 ? WindowInsets.CONSUMED : ins.consumeSystemWindowInsets();
        });

        // header: back + title
        LinearLayout header = ui.row();
        header.setPadding(0, ui.dp(6), ui.dp(4), ui.dp(14));
        View back = ui.key("left", null, 36, false, v -> finish());
        back.setContentDescription(L.t("Назад"));
        header.addView(back);
        TextView title = ui.text(L.t("Настройки"), 26, ui.fg, true);
        title.setPadding(ui.dp(10), 0, 0, 0);
        header.addView(title, Ui.lpW(1));
        root.addView(header);

        buildLook();
        buildAccent();
        buildBehaviour();
        buildYoutube();
        buildLanguage();
        buildAbout();
    }

    private void section(String name) {
        root.addView(ui.label(name), ui.margins(Ui.lp(-1, -2), 12, 4, 12, 8));
    }

    private LinearLayout card() {
        LinearLayout c = ui.card();
        root.addView(c, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 18));
        return c;
    }

    // ------------------------------------------------------------ appearance

    private void buildLook() {
        section(L.t("Оформление"));
        LinearLayout c = card();
        c.addView(preview());
        c.addView(ui.separator());
        // Auto | Light | Dark in one control, as the system settings do; pure black is a switch
        // of its own below, for whenever the theme ends up dark (Auto at night too)
        String cur = Ui.theme(this);
        String[] names = {L.t("Авто"), L.t("Светлая"), L.t("Тёмная")};
        LinearLayout seg = ui.segmented(names, java.util.Arrays.asList(Ui.THEMES).indexOf(cur), idx -> {
            String id = Ui.THEMES[idx];
            if (id.equals(Ui.theme(this))) return;
            prefs.edit().putString("theme", id).apply();
            restyle();
        });
        LinearLayout box = ui.col();
        box.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(6));
        box.addView(seg, Ui.lp(-1, -2));
        c.addView(box);
        if (!cur.equals("light"))
            c.addView(toggle("AMOLED", null,
                    "amoled", false, this::restyle));
    }

    /** A small "now playing" card in the chosen colours. */
    private View preview() {
        LinearLayout p = ui.row();
        p.setPadding(ui.dp(10), ui.dp(8), ui.dp(10), ui.dp(10));
        View art = new View(this) {
            final Paint pt = new Paint(Paint.ANTI_ALIAS_FLAG);
            @Override protected void onDraw(Canvas cv) {
                pt.setColor(Ui.alpha(ui.accent, 0.22f));
                float r = ui.dp(10);
                cv.drawRoundRect(0, 0, getWidth(), getHeight(), r, r, pt);
                Ui.Glyph g = new Ui.Glyph("tv_play", ui.accent, 1.8f);
                int s = getWidth() / 2;
                g.setBounds(s / 2, s / 2, s / 2 + s, s / 2 + s);
                g.draw(cv);
            }
        };
        p.addView(art, Ui.lp(ui.dp(56), ui.dp(56)));
        LinearLayout t = ui.col();
        t.setPadding(ui.dp(12), 0, ui.dp(10), 0);
        TextView a = ui.text(L.t("Пример видео"), 16, ui.fg, true);
        a.setSingleLine(true);
        t.addView(a);
        TextView b = ui.text(L.t("Так будет выглядеть Fling"), 13, ui.fgA(0.5f), false);
        b.setPadding(0, ui.dp(4), 0, ui.dp(8));
        t.addView(b);
        Ui.Progress pr = ui.new Progress();
        pr.set(0.4f);
        t.addView(pr, Ui.lp(-1, ui.dp(4)));
        p.addView(t, Ui.lpW(1));
        p.addView(ui.key("pause", null, 44, true, v -> {}));
        return p;
    }

    private void buildAccent() {
        section(L.t("Цвет акцента"));
        LinearLayout c = card();
        c.setPadding(ui.dp(8), ui.dp(14), ui.dp(8), ui.dp(12));
        String cur = Ui.accentId(this);
        java.util.List<String> ids = new java.util.ArrayList<>();
        for (String id : Ui.ACCENTS) if (!id.equals("you") || Ui.dynamicAvailable()) ids.add(id);
        LinearLayout row = null;
        for (int i = 0; i < ids.size(); i++) {
            if (i % 4 == 0) { row = ui.row(); c.addView(row, ui.margins(Ui.lp(-1, -2), 0, i == 0 ? 0 : 10, 0, 0)); }
            final String id = ids.get(i);
            row.addView(swatch(id, id.equals(cur)), Ui.lpW(1));
        }
        for (int i = ids.size(); i % 4 != 0; i++) row.addView(new View(this), Ui.lpW(1));   // keep the last row on the grid
        // any colour: drag along the rainbow, the dot shows it at once, letting go applies it
        LinearLayout rr = ui.row();
        final View dot = new View(this);
        final int[] live = {Ui.accentColor(this, cur.equals("custom") ? "custom" : cur)};
        android.graphics.drawable.GradientDrawable dd = ui.oval(live[0]);
        dot.setBackground(dd);
        final TextView name = ui.text(accentName(cur), 14, ui.fgA(0.6f), false);
        View bar = new View(this) {
            final Paint pt = new Paint(Paint.ANTI_ALIAS_FLAG);
            float hue = cur.equals("custom") ? prefs.getFloat("accent_hue", 210f) : -1f;
            @Override protected void onDraw(Canvas cv) {
                float w = getWidth(), h = getHeight(), r = h / 2f;
                int[] hues = new int[13];
                for (int i = 0; i < hues.length; i++) hues[i] = Ui.hueColor(i * 30f, ui.dark);
                pt.setShader(new android.graphics.LinearGradient(r, 0, w - r, 0, hues, null, android.graphics.Shader.TileMode.CLAMP));
                cv.drawRoundRect(0, h * 0.3f, w, h * 0.7f, r, r, pt);
                pt.setShader(null);
                if (hue >= 0) {                                  // the thumb on the chosen hue
                    float x = r + (w - 2 * r) * hue / 360f;
                    pt.setColor(0xFFFFFFFF);
                    cv.drawCircle(x, h / 2f, r * 0.62f, pt);
                    pt.setColor(Ui.hueColor(hue, ui.dark));
                    cv.drawCircle(x, h / 2f, r * 0.45f, pt);
                }
            }
            @Override public boolean onTouchEvent(android.view.MotionEvent e) {
                float r = getHeight() / 2f;
                hue = Math.max(0f, Math.min(359.9f, (e.getX() - r) / (getWidth() - 2 * r) * 360f));
                live[0] = Ui.hueColor(hue, ui.dark);
                dd.setColor(live[0]);
                name.setText(L.t("Свой цвет"));
                invalidate();
                getParent().requestDisallowInterceptTouchEvent(e.getAction() != android.view.MotionEvent.ACTION_UP);
                if (e.getAction() == android.view.MotionEvent.ACTION_UP) {
                    prefs.edit().putString("accent", "custom").putFloat("accent_hue", hue).apply();
                    restyle();
                }
                return true;
            }
        };
        bar.setContentDescription(L.t("Свой цвет"));
        rr.addView(bar, ui.margins(Ui.lpW(1), 6, 0, 12, 0));
        rr.addView(dot, Ui.lp(ui.dp(30), ui.dp(30)));
        bar.setMinimumHeight(ui.dp(36));
        c.addView(rr, ui.margins(Ui.lp(-1, -2), 10, 14, 10, 0));
        name.setGravity(Gravity.CENTER);
        c.addView(name, ui.margins(Ui.lp(-1, -2), 8, 10, 8, 0));
        if (!Ui.dynamicAvailable()) {
            TextView n = ui.text(L.t("Monet (цвета обоев) доступен с Android 12."), 12, ui.fgA(0.4f), false);
            n.setGravity(Gravity.CENTER);
            c.addView(n, ui.margins(Ui.lp(-1, -2), 8, 6, 8, 0));
        }
    }

    private View swatch(String id, boolean on) {
        final int color = Ui.accentColor(this, id);
        View v = new View(this) {
            final Paint pt = new Paint(Paint.ANTI_ALIAS_FLAG);
            @Override protected void onDraw(Canvas cv) {
                float cx = getWidth() / 2f, cy = getHeight() / 2f, r = ui.dp(15);
                pt.setStyle(Paint.Style.FILL);
                pt.setColor(color);
                cv.drawCircle(cx, cy, r, pt);
                if (id.equals("you") && android.os.Build.VERSION.SDK_INT >= 31) {   // Monet: the wallpaper's three tones
                    pt.setColor(getColor(android.R.color.system_accent2_300));
                    cv.drawArc(cx - r, cy - r, cx + r, cy + r, 90, 120, true, pt);
                    pt.setColor(getColor(android.R.color.system_accent3_300));
                    cv.drawArc(cx - r, cy - r, cx + r, cy + r, 210, 120, true, pt);
                }
                if (on) {
                    pt.setStyle(Paint.Style.STROKE);
                    pt.setStrokeWidth(ui.dp(2));
                    pt.setColor(color);
                    cv.drawCircle(cx, cy, r + ui.dp(5), pt);
                }
            }
        };
        v.setMinimumHeight(ui.dp(46));
        v.setContentDescription(accentName(id));
        v.setBackground(ui.rippleOval(0));
        v.setOnClickListener(x -> {
            if (id.equals(Ui.accentId(this))) return;
            prefs.edit().putString("accent", id).apply();
            restyle();
        });
        return v;
    }

    private static String accentName(String id) {
        switch (id) {
            case "you": return L.t("Monet — цвета обоев");
            case "indigo": return L.t("Индиго");
            case "purple": return L.t("Фиолетовый");
            case "pink": return L.t("Розовый");
            case "teal": return L.t("Бирюзовый");
            case "green": return L.t("Зелёный");
            case "graphite": return L.t("Графит");
            case "custom": return L.t("Свой цвет");
            default: return L.t("Синий");
        }
    }

    // ------------------------------------------------------------ behaviour

    private void buildBehaviour() {
        section(L.t("Пульт и трансляция"));
        LinearLayout c = card();
        c.addView(toggle(L.t("Пульт в уведомлении"), L.t("Показывает, что идёт на телевизоре, и даёт управлять им из шторки и с экрана блокировки."),
                "remote_notify", true, () -> {
                    if (!prefs.getBoolean("remote_notify", true)) TvRemoteService.hide(this);
                    else TvRemoteService.show(this, getSharedPreferences("fling", MODE_PRIVATE).getString("host", null),
                            getSharedPreferences("fling", MODE_PRIVATE).getString("name", null));
                }));
        c.addView(ui.separator());
        c.addView(toggle(L.t("Уведомление с кнопками"), L.t("Пока на телевизоре что-то воспроизводится: пауза, перемотка и громкость на экране блокировки."), "notify", true));
        c.addView(ui.separator());
        c.addView(toggle(L.t("Кнопки громкости управляют ТВ"), L.t("Пока Fling открыт, кнопки громкости телефона регулируют громкость телевизора."), "volKeys", true));
        c.addView(ui.separator());
        c.addView(toggle(L.t("Не отключать экран, пока открыт Fling"), L.t("Телефон не уйдёт в спящий режим, пока вы пользуетесь пультом."), "keepOn", false));
    }

    private View toggle(String title, String hint, String key, boolean def) { return toggle(title, hint, key, def, null); }

    private View toggle(String title, String hint, String key, boolean def, Runnable after) {
        LinearLayout r = ui.row();
        r.setPadding(ui.dp(12), ui.dp(10), ui.dp(8), ui.dp(10));
        LinearLayout t = ui.col();
        t.addView(ui.text(title, 16, ui.fg, false));
        if (hint != null && !hint.isEmpty()) {
            TextView h = ui.text(hint, 13, ui.fgA(0.5f), false);
            h.setLineSpacing(0, 1.15f);
            h.setPadding(0, ui.dp(4), 0, 0);
            t.addView(h);
        }
        r.addView(t, Ui.lpW(1));
        Ui.M3Switch sw = ui.new M3Switch(prefs.getBoolean(key, def));   // Material Design 3
        sw.onChange = () -> {
            prefs.edit().putBoolean(key, sw.on).apply();
            if (after != null) after.run();
        };
        sw.setContentDescription(title);
        r.addView(sw, ui.margins(Ui.lp(-2, -2), 12, 0, 0, 0));
        r.setOnClickListener(v -> sw.toggle());
        r.setBackground(ui.rippleRound(0, 12));
        return r;
    }

    // ------------------------------------------------------------ YouTube on the TV

    /** Seeking YouTube on the TV: with keys (nothing to set up), or exactly — Fling linked to the
     *  TV's YouTube with its code (the Lounge API: seek to a second, speed, next / previous). */
    private void buildYoutube() {
        section("YouTube");
        LinearLayout c = card();
        YtLounge yt = new YtLounge(this);
        boolean exact = "lounge".equals(prefs.getString("yt_seek", "keys")) && yt.paired();
        LinearLayout box = ui.col();
        box.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(6));
        box.addView(ui.segmented(new String[]{L.t("Кнопками ±30 с"), L.t("Точно, по коду с ТВ")}, exact ? 1 : 0, idx -> {
            if (idx == 0) { prefs.edit().putString("yt_seek", "keys").apply(); restyle(); return; }
            if (yt.paired()) { prefs.edit().putString("yt_seek", "lounge").apply(); restyle(); return; }
            EditText code = ui.field("1234 5678 9012", true);
            code.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
            ui.ask(this, L.t("Подключить YouTube на ТВ"),
                    L.t("На телевизоре откройте YouTube → Настройки → «Подключить телевизор по коду» и введите код с экрана."),
                    code, new String[]{L.t("Подключить"), L.t("Отмена")},
                    new Runnable[]{() -> new Thread(() -> {
                        try {
                            yt.pair(code.getText().toString());
                            prefs.edit().putString("yt_seek", "lounge").apply();
                            runOnUiThread(this::restyle);
                        } catch (Exception e) {
                            runOnUiThread(() -> android.widget.Toast.makeText(this, L.t("Не получилось: ") + e.getMessage(), android.widget.Toast.LENGTH_LONG).show());
                        }
                    }).start(), this::restyle});
        }), Ui.lp(-1, -2));
        c.addView(box);
        TextView hint = ui.text(exact
                ? L.f("Подключено: %s. Перемотка на секунду, скорость, следующее и предыдущее видео.", yt.screenName() == null ? "YouTube" : yt.screenName())
                : L.t("±30 с кнопками пульта. Точный режим: перемотка полоской на секунду и скорость."), 13, ui.fgA(0.5f), false);
        hint.setPadding(ui.dp(12), ui.dp(4), ui.dp(12), ui.dp(10));
        c.addView(hint);
        if (yt.paired()) {
            c.addView(ui.separator());
            c.addView(ui.row(L.t("Отключить YouTube на ТВ"), "close", null, false, v -> {
                yt.forget();
                prefs.edit().putString("yt_seek", "keys").apply();
                restyle();
            }));
        }
    }

    // ------------------------------------------------------------ language, about

    private void buildLanguage() {
        section(L.t("Язык"));
        LinearLayout c = card();
        c.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(12));
        LinearLayout seg = ui.segmented(new String[]{"Русский", "English"}, L.english() ? 1 : 0, idx -> {
            boolean en = idx == 1, was = L.english();
            L.setLang(this, en ? "en" : "ru");
            if (en != was) restyle();
        });
        c.addView(seg, Ui.lp(-1, -2));
    }

    @Override protected void onResume() {
        super.onResume();
        if (updater != null) updater.resumed();          // back from "Install unknown apps": offer the update again
    }

    private void buildAbout() {
        section(L.t("О приложении"));
        LinearLayout c = card();
        if (BuildConfig.SELF_UPDATE) {                   // the GitHub build; Play updates the app itself
            c.addView(ui.row(L.t("Проверить обновления"), "refresh", Updater.installedVersion(this), false, v -> updater.check(true)));
            c.addView(toggle(L.t("Сообщать о новых версиях"), L.t("Раз в день Fling проверяет обновления на GitHub, даже когда закрыт, и сообщает о новой версии."),
                    "upd_notify", true, () -> Updater.background(this)));
            c.addView(toggle(L.t("Устанавливать обновления автоматически"), L.t("Новая версия скачивается и устанавливается сама, пока Fling закрыт. Android может один раз попросить подтверждение."),
                    "upd_auto", true));
        }   // trailing: the version, short enough for 320 dp
        c.addView(ui.row(L.t("Как пользоваться"), "help", null, false, v -> Help.show(this, ui)));
        c.addView(ui.row(L.t("Первые шаги"), "gear", null, false, v -> {   // shows on the main screen
            prefs.edit().putBoolean("setup_shown", false).apply();
            finish();
        }));
        c.addView(ui.row(L.t("Отчёт для разработчика"), "send", null, false, v -> Diag.report(this)));
        c.addView(ui.row(L.t("Показать подсказки снова"), "refresh", null, false, v -> {
            getSharedPreferences("fling", MODE_PRIVATE).edit().putBoolean("help_seen", false).apply();
            finish();                                    // the main screen runs the tips when it comes back
        }));
        String build = L.t("Сборка") + " " + BuildConfig.VERSION_NAME + " (" + BuildConfig.VERSION_CODE + ") · "
                + BuildConfig.GIT_COMMIT + " · " + BuildConfig.BUILD_DATE;
        TextView bi = ui.text(build, 12, ui.fgA(0.45f), false);
        bi.setGravity(Gravity.CENTER);
        bi.setTextIsSelectable(true);
        root.addView(bi, ui.margins(Ui.lp(-1, -2), 12, 10, 12, 0));
        TextView credit = ui.text(L.t("Оригинальный Fling создали Damion Rashford и Tatiana · неофициальный порт — orfixe"), 12, ui.fgA(0.45f), false);
        credit.setGravity(Gravity.CENTER);
        credit.setLineSpacing(0, 1.2f);
        root.addView(credit, ui.margins(Ui.lp(-1, -2), 12, 0, 12, 0));
    }
}
