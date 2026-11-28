// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.BitmapShader;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.text.InputType;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.GestureDetector;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.view.Window;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Theme, drawn icons and the small custom widgets of the Fling look. */
final class Ui {
    final Context ctx;
    final boolean dark, black;
    final int page, card, fg, accent, green, orange, red, hairline;
    /** Material 3 tonal buttons: the secondary container and its content colour — from the
     *  wallpaper with Monet, a tint of the accent otherwise (they were neutral grey). */
    final int tonal, onTonal;
    /** M3 primary container (OK, the main key), surface variant (fields, segmented, mic) and
     *  its content colour (row icons) — wallpaper tones with Monet, accent-tinted otherwise. */
    final int primC, onPrimC, surfVar, onSurfVar;

    // ------------------------------------------------------------ themes (Settings → Appearance)

    /** Theme modes, stored in prefs "fling" → "theme"; "amoled" (boolean) makes dark pure black. */
    static final String[] THEMES = {"system", "light", "dark"};
    /** Accent ids, stored in "accent"; each has a colour R.color.acc_<id> and a style R.style.Accent_<id>. */
    static final String[] ACCENTS = {"you", "blue", "indigo", "purple", "pink", "teal", "green", "graphite"};

    static String theme(Context c) {
        android.content.SharedPreferences p = c.getSharedPreferences("fling", Context.MODE_PRIVATE);
        String t = p.getString("theme", "system");
        if (t.equals("black")) {                    // up to 0.5.0 black was a fourth theme: now dark + AMOLED
            p.edit().putString("theme", "dark").putBoolean("amoled", true).apply();
            return "dark";
        }
        return java.util.Arrays.asList(THEMES).contains(t) ? t : "system";
    }

    /** Dark theme in pure black (pixels off on AMOLED), whenever the theme ends up dark. */
    static boolean amoled(Context c) {
        theme(c);                                   // moves an old "black" over first
        return c.getSharedPreferences("fling", Context.MODE_PRIVATE).getBoolean("amoled", false);
    }

    static String accentId(Context c) {
        String a = c.getSharedPreferences("fling", Context.MODE_PRIVATE).getString("accent", "blue");
        if (a.equals("you") && android.os.Build.VERSION.SDK_INT < 31) return "blue";   // no wallpaper colours before Android 12
        if (a.equals("custom")) return a;                              // any hue from the rainbow
        return java.util.Arrays.asList(ACCENTS).contains(a) ? a : "blue";
    }

    static boolean dynamicAvailable() { return android.os.Build.VERSION.SDK_INT >= 31; }

    /** Call from Activity.attachBaseContext (after super): a light/dark choice that differs from the
     *  system's becomes an override of the activity's configuration, so resources and system dialogs
     *  follow it too. */
    static void applyNight(android.app.Activity a, Context base) {
        String t = theme(base);
        if (t.equals("system")) return;
        Configuration c = new Configuration();   // a delta: only uiMode is overridden
        int night = t.equals("light") ? Configuration.UI_MODE_NIGHT_NO : Configuration.UI_MODE_NIGHT_YES;
        c.uiMode = (base.getResources().getConfiguration().uiMode & ~Configuration.UI_MODE_NIGHT_MASK) | night;
        a.applyOverrideConfiguration(c);
    }

    /** Call from Activity.onCreate before any view exists: tints the system widgets and dialogs. */
    static void applyAccent(android.app.Activity a) {
        a.getTheme().applyStyle(accentRes(accentId(a))[1], true);
    }

    static int accentColor(Context c, String id) {
        if (id.equals("custom")) {
            boolean dark = (c.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                    == Configuration.UI_MODE_NIGHT_YES;
            return hueColor(c.getSharedPreferences("fling", Context.MODE_PRIVATE).getFloat("accent_hue", 210f), dark);
        }
        return c.getColor(accentRes(id)[0]);
    }

    /** The accent for a rainbow hue: bright on dark, a little deeper on light, readable on both. */
    static int hueColor(float hue, boolean dark) {
        return Color.HSVToColor(new float[]{((hue % 360) + 360) % 360, dark ? 0.62f : 0.82f, dark ? 1f : 0.86f});
    }

    /** {colour, style} resources of an accent id. */
    private static int[] accentRes(String id) {
        switch (id) {
            case "you": return new int[]{R.color.acc_you, R.style.Accent_you};
            case "indigo": return new int[]{R.color.acc_indigo, R.style.Accent_indigo};
            case "purple": return new int[]{R.color.acc_purple, R.style.Accent_purple};
            case "pink": return new int[]{R.color.acc_pink, R.style.Accent_pink};
            case "teal": return new int[]{R.color.acc_teal, R.style.Accent_teal};
            case "green": return new int[]{R.color.acc_green, R.style.Accent_green};
            case "graphite": return new int[]{R.color.acc_graphite, R.style.Accent_graphite};
            default: return new int[]{R.color.acc_blue, R.style.Accent_blue};
        }
    }

    Ui(Context ctx) {
        this.ctx = ctx;
        dark = (ctx.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
        black = dark && amoled(ctx);
        String acc = accentId(ctx);
        boolean you = acc.equals("you");
        if (black) { page = 0xFF000000; card = 0xFF000000; }   // AMOLED: pixels off; cards get a hairline instead
        else if (you) {   // Material You: page and cards take the wallpaper's neutral tones too
            page = ctx.getColor(dark ? android.R.color.system_neutral1_900 : android.R.color.system_neutral1_50);
            card = ctx.getColor(dark ? android.R.color.system_neutral1_800 : android.R.color.system_neutral1_10);
        } else {
            page = dark ? 0xFF000000 : 0xFFF2F2F7;
            card = dark ? 0xFF1C1C1E : 0xFFFFFFFF;
        }
        fg = dark ? 0xFFFFFFFF : 0xFF1C1C1E;
        accent = accentColor(ctx, acc);
        green = dark ? 0xFF30D158 : 0xFF28C840;
        orange = dark ? 0xFFFF9F0A : 0xFFE08600;
        red = dark ? 0xFFFF453A : 0xFFFF3B30;
        hairline = fgA(0.12f);
        if (you) {
            tonal = ctx.getColor(dark ? android.R.color.system_accent2_800 : android.R.color.system_accent2_100);
            onTonal = ctx.getColor(dark ? android.R.color.system_accent2_100 : android.R.color.system_accent2_900);
            primC = ctx.getColor(dark ? android.R.color.system_accent1_700 : android.R.color.system_accent1_200);
            onPrimC = ctx.getColor(dark ? android.R.color.system_accent1_100 : android.R.color.system_accent1_900);
            surfVar = black ? ctx.getColor(android.R.color.system_neutral2_900)
                    : ctx.getColor(dark ? android.R.color.system_neutral2_700 : android.R.color.system_neutral2_100);
            onSurfVar = ctx.getColor(dark ? android.R.color.system_neutral2_200 : android.R.color.system_neutral2_700);
        } else {
            tonal = blend(black ? 0xFF000000 : card, accent, dark ? 0.2f : 0.12f);
            onTonal = fg;
            primC = blend(black ? 0xFF000000 : card, accent, dark ? 0.32f : 0.22f);
            onPrimC = accent;
            surfVar = blend(black ? 0xFF000000 : card, fg, dark ? 0.1f : 0.05f);
            onSurfVar = fgA(0.55f);
        }
    }

    int dp(float v) { return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.getResources().getDisplayMetrics())); }
    static int alpha(int c, float a) { return (c & 0x00FFFFFF) | (Math.round(a * 255) << 24); }
    int fgA(float a) { return alpha(fg, a); }
    int accA(float a) { return alpha(accent, a); }

    // ------------------------------------------------------------ backgrounds

    GradientDrawable round(int color, float radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(radiusDp));
        return g;
    }

    GradientDrawable oval(int color) {
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(color);
        return g;
    }

    Drawable ripple(Drawable content, Drawable mask) {
        return new RippleDrawable(ColorStateList.valueOf(accA(0.22f)), content, mask);
    }

    Drawable rippleRound(int color, float r) { return ripple(round(color, r), round(0xFFFFFFFF, r)); }
    Drawable rippleOval(int color) { return ripple(oval(color), oval(0xFFFFFFFF)); }

    // ------------------------------------------------------------ text

    TextView text(String s, float sp, int color, boolean bold) {
        TextView t = new TextView(ctx);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setTypeface(Typeface.create("sans-serif" + (bold ? "-medium" : ""), Typeface.NORMAL));
        t.setIncludeFontPadding(false);
        return t;
    }

    TextView label(String s) {
        TextView t = text(s, 11, fgA(0.5f), true);
        t.setLetterSpacing(0.08f);
        t.setAllCaps(true);
        return t;
    }

    EditText field(String hint, boolean mono) {
        EditText e = new EditText(ctx);
        e.setHint(hint);
        e.setSingleLine(true);
        e.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        e.setTextColor(fg);
        e.setHintTextColor(fgA(0.35f));
        if (mono) e.setTypeface(Typeface.MONOSPACE);
        GradientDrawable bg = round(surfVar, 12);
        e.setBackground(bg);
        e.setPadding(dp(12), dp(10), dp(12), dp(10));
        e.setInputType(InputType.TYPE_CLASS_TEXT);
        return e;
    }

    static LinearLayout.LayoutParams lp(int w, int h) { return new LinearLayout.LayoutParams(w, h); }
    static LinearLayout.LayoutParams lpW(float weight) { return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, weight); }
    LinearLayout.LayoutParams margins(LinearLayout.LayoutParams p, float l, float t, float r, float b) {
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    LinearLayout col() { LinearLayout l = new LinearLayout(ctx); l.setOrientation(LinearLayout.VERTICAL); return l; }
    LinearLayout row() {
        LinearLayout l = new LinearLayout(ctx);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        return l;
    }

    LinearLayout card() {
        LinearLayout c = col();
        GradientDrawable bg = round(card, 18);
        if (black) bg.setStroke(Math.max(1, dp(1)), fgA(0.16f));
        c.setBackground(bg);
        c.setPadding(dp(6), dp(8), dp(6), dp(8));
        c.setElevation(dark ? 0 : dp(1));
        return c;
    }

    View separator() {
        View v = new View(ctx);
        v.setBackgroundColor(hairline);
        LinearLayout.LayoutParams p = lp(ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, dp(0.7f)));
        p.setMargins(dp(12), dp(6), dp(12), dp(6));
        v.setLayoutParams(p);
        return v;
    }

    View spacer() {
        View v = new View(ctx);
        v.setLayoutParams(new LinearLayout.LayoutParams(0, 1, 1f));
        return v;
    }

    ImageView icon(String name, int color, float sizeDp) {
        ImageView i = new ImageView(ctx);
        i.setImageDrawable(new Glyph(name, color, 1.8f));
        i.setLayoutParams(lp(dp(sizeDp), dp(sizeDp)));
        return i;
    }

    // ------------------------------------------------------------ menu row (MenuRow in PanelRows.swift)

    final class Row extends LinearLayout {
        final ImageView ic;
        final TextView title, trailing;
        private boolean accented;
        private final String iconName;
        private Integer iconColor;          // fixed icon colour (e.g. green "Pair with TV"), else follows the row

        Row(String t, String iconName, String trail, boolean accented) {
            super(ctx);
            this.iconName = iconName;
            setOrientation(HORIZONTAL);
            setGravity(Gravity.CENTER_VERTICAL);
            setPadding(dp(12), 0, dp(12), 0);
            setMinimumHeight(dp(46));
            setBackground(rippleRound(0x00000000, 12));
            ic = new ImageView(ctx);
            LinearLayout.LayoutParams ip = lp(dp(20), dp(20));
            ip.rightMargin = dp(12);
            ic.setLayoutParams(ip);
            ic.setVisibility(iconName == null ? GONE : VISIBLE);
            title = text(t, 16, fg, false);
            title.setSingleLine(true);
            title.setEllipsize(TextUtils.TruncateAt.END);
            trailing = text(trail == null ? "" : trail, 14, fgA(0.35f), false);
            addView(ic);
            addView(title, new LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f));
            addView(trailing);
            setAccented(accented);
        }

        void setAccented(boolean a) {
            accented = a;
            refreshColors();
        }

        void setIconColor(int color) {
            iconColor = color;
            refreshColors();
        }

        @Override public void setEnabled(boolean en) {
            super.setEnabled(en);
            refreshColors();
        }

        private void refreshColors() {
            boolean en = isEnabled();
            int c = !en ? fgA(0.3f) : accented ? accent : fg;
            title.setTextColor(c);
            title.setTypeface(Typeface.create(accented ? "sans-serif-medium" : "sans-serif", Typeface.NORMAL));
            int ico = !en ? fgA(0.3f) : iconColor != null ? iconColor : accented ? accent : onSurfVar;
            if (iconName != null) ic.setImageDrawable(new Glyph(iconName, ico, 1.8f));
        }
    }

    Row row(String t, String icon, String trail, boolean accented, View.OnClickListener on) {
        Row r = new Row(t, icon, trail, accented);
        r.setOnClickListener(on);
        return r;
    }

    // ------------------------------------------------------------ keys, chips, pills

    View key(String glyph, String label, float sizeDp, boolean accented, View.OnClickListener on) {
        FrameLayout f = new FrameLayout(ctx);
        f.setBackground(rippleOval(accented ? primC : tonal));
        int s = dp(sizeDp);
        f.setLayoutParams(lp(s, s));
        if (glyph != null) {
            ImageView i = new ImageView(ctx);
            // the d-pad and Back keep their bold strokes; every other key a lighter, Material-like line
            boolean bold = glyph.equals("up") || glyph.equals("down") || glyph.equals("left") || glyph.equals("right") || glyph.equals("back");
            i.setImageDrawable(new Glyph(glyph, accented ? onPrimC : onTonal, bold ? 2.0f : 1.3f));
            int is = Math.round(s * 0.46f);
            f.addView(i, new FrameLayout.LayoutParams(is, is, Gravity.CENTER));
        } else {
            TextView t = text(label, sizeDp * 0.28f, accented ? onPrimC : onTonal, true);
            f.addView(t, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));
        }
        f.setClickable(true);
        f.setOnClickListener(on);
        f.setHapticFeedbackEnabled(true);
        return f;
    }

    TextView chip(String name, View.OnClickListener on) {
        TextView t = text(name, 13, fg, false);
        t.setGravity(Gravity.CENTER);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setPadding(dp(4), dp(9), dp(4), dp(9));
        t.setOnClickListener(on);
        setChipActive(t, false);
        return t;
    }

    void setChipActive(TextView t, boolean a) {
        t.setBackground(rippleRound(a ? accA(0.3f) : tonal, 10));
        t.setTextColor(a ? accent : onTonal);
        t.setTypeface(Typeface.create(a ? "sans-serif-medium" : "sans-serif", Typeface.NORMAL));
    }

    LinearLayout pill(String glyph, String label, View.OnClickListener on) {
        LinearLayout r = row();
        r.setGravity(Gravity.CENTER);
        r.setPadding(dp(6), dp(10), dp(6), dp(10));
        r.setBackground(rippleRound(tonal, 10));
        ImageView i = icon(glyph, onTonal, 16);
        r.addView(i);
        TextView t = text(label, 13, onTonal, false);
        t.setPadding(dp(6), 0, 0, 0);
        r.addView(t);
        r.setOnClickListener(on);
        return r;
    }

    // ------------------------------------------------------------ Material 3 switch

    /** A Material Design 3 switch: a 52×32 dp track — outlined with a small grey thumb when off,
     *  filled with the accent and a large thumb carrying a check mark when on — animated. */
    final class M3Switch extends View {
        boolean on;
        private float t;                                   // 0 off … 1 on, animated
        Runnable onChange;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final android.graphics.Path check = new android.graphics.Path();

        M3Switch(boolean on) {
            super(ctx);
            this.on = on;
            t = on ? 1 : 0;
            setClickable(true);
            setOnClickListener(v -> toggle());
            describe();
        }

        void toggle() { set(!on, true); }

        void set(boolean v, boolean user) {
            if (v == on) return;
            on = v;
            android.animation.ValueAnimator a = android.animation.ValueAnimator.ofFloat(t, on ? 1 : 0).setDuration(170);
            a.addUpdateListener(x -> { t = (float) x.getAnimatedValue(); invalidate(); });
            a.start();
            describe();
            if (user) performHapticFeedback(android.view.HapticFeedbackConstants.CLOCK_TICK);
            if (user && onChange != null) onChange.run();
        }

        private void describe() {
            if (android.os.Build.VERSION.SDK_INT >= 30) setStateDescription(on ? "✓" : "—");
        }

        @Override protected void onMeasure(int w, int h) { setMeasuredDimension(dp(52), dp(32)); }

        @Override protected void onDraw(Canvas c) {
            float w = getWidth(), h = getHeight(), r = h / 2f, sw = dp(2);
            int onC = accent;
            boolean lightAccent = Color.luminance(accent) > 0.5f;
            int onAccent = lightAccent ? 0xFF1C1B1F : 0xFFFFFFFF;
            // track: outline when off, accent fill when on
            RectF tr = new RectF(sw / 2, sw / 2, w - sw / 2, h - sw / 2);
            p.setStyle(Paint.Style.FILL);
            p.setColor(alpha(fg, 0.08f * (1 - t)));
            c.drawRoundRect(tr, r, r, p);
            if (t > 0) { p.setColor(alpha(onC, t)); c.drawRoundRect(tr, r, r, p); }
            if (t < 1) {
                p.setStyle(Paint.Style.STROKE);
                p.setStrokeWidth(sw);
                p.setColor(alpha(fg, 0.45f * (1 - t)));
                c.drawRoundRect(tr, r, r, p);
            }
            // thumb: 16 dp grey → 24 dp on the accent's contrast colour
            float tr2 = dp(8) + (dp(12) - dp(8)) * t;
            float cx = r + (w - 2 * r) * t, cy = h / 2f;
            p.setStyle(Paint.Style.FILL);
            int off = alpha(fg, 0.55f);
            p.setColor(blend(off, onAccent, t));
            c.drawCircle(cx, cy, tr2, p);
            if (t > 0.6f) {                                    // the check mark
                p.setStyle(Paint.Style.STROKE);
                p.setStrokeWidth(dp(2));
                p.setStrokeCap(Paint.Cap.ROUND);
                p.setStrokeJoin(Paint.Join.ROUND);
                p.setColor(alpha(onC, (t - 0.6f) / 0.4f));
                float k = dp(5);
                check.reset();
                check.moveTo(cx - k, cy);
                check.lineTo(cx - k * 0.3f, cy + k * 0.7f);
                check.lineTo(cx + k, cy - k * 0.6f);
                c.drawPath(check, p);
            }
        }
    }

    static int blend(int a, int b, float t) {
        int ar = (a >> 16) & 255, ag = (a >> 8) & 255, ab = a & 255, aa = (a >>> 24);
        int br = (b >> 16) & 255, bg = (b >> 8) & 255, bb = b & 255, ba = (b >>> 24);
        return ((int) (aa + (ba - aa) * t) << 24) | ((int) (ar + (br - ar) * t) << 16)
                | ((int) (ag + (bg - ag) * t) << 8) | (int) (ab + (bb - ab) * t);
    }

    // ------------------------------------------------------------ questions

    /** A question in Fling's own look: a rounded card in the page colours, the main answer as a
     *  tonal accent button (like OK and LIVE), the others below it. The system AlertDialog under
     *  this app's platform theme is Android 5's — square, ALL-CAPS buttons, system colours.
     *  labels[0] is the main answer; a null action just closes. `extra` (a field) goes under the text. */
    Dialog ask(Activity a, String title, String message, View extra, String[] labels, Runnable[] actions) {
        Dialog d = new Dialog(a);
        d.requestWindowFeature(Window.FEATURE_NO_TITLE);
        LinearLayout box = col();
        GradientDrawable bg = round(card, 28);
        if (card == page) bg.setStroke(Math.max(1, dp(1)), fgA(0.16f));   // AMOLED: black on black needs an edge
        box.setBackground(bg);
        box.setPadding(dp(24), dp(24), dp(24), dp(16));
        box.addView(text(title, 20, fg, true));
        if (message != null && !message.isEmpty()) {
            TextView m = text(message, 15, fgA(0.72f), false);
            m.setLineSpacing(dp(3), 1f);
            box.addView(m, margins(lp(-1, -2), 0, 12, 0, 0));
        }
        if (extra != null) box.addView(extra, margins(lp(-1, -2), 0, 16, 0, 0));
        for (int i = 0; i < labels.length; i++) {
            final Runnable act = actions[i];
            boolean main = i == 0;
            TextView b = text(labels[i], 15, main ? accent : fg, main);
            b.setGravity(Gravity.CENTER);
            b.setMinHeight(dp(48));
            b.setPadding(dp(16), dp(12), dp(16), dp(12));
            b.setBackground(rippleRound(main ? accA(dark ? 0.22f : 0.14f) : tonal, 14));
            b.setOnClickListener(v -> { d.dismiss(); if (act != null) act.run(); });
            box.addView(b, margins(lp(-1, -2), 0, main ? 20 : 8, 0, 0));
        }
        d.setContentView(box);
        Window w = d.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(0));
            int screen = ctx.getResources().getDisplayMetrics().widthPixels;
            w.setLayout(Math.min(screen - dp(32), dp(420)), ViewGroup.LayoutParams.WRAP_CONTENT);
        }
        d.show();
        return d;
    }

    // ------------------------------------------------------------ drawn icons (SF-Symbols-like)

    static final class Glyph extends Drawable {
        private final String name;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float weight;
        private final int color;

        Glyph(String name, int color, float weight) {
            this.name = name;
            this.color = color;
            this.weight = weight;
            p.setColor(color);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeCap(Paint.Cap.ROUND);
            p.setStrokeJoin(Paint.Join.ROUND);
        }

        @Override public void draw(Canvas c) {
            float s = Math.min(getBounds().width(), getBounds().height());
            c.save();
            c.translate(getBounds().left, getBounds().top);
            p.setStrokeWidth(weight * s / 14f);
            p.setStyle(Paint.Style.STROKE);
            float m = s / 2;
            switch (name) {
                case "up": poly(c, false, s, .28f, .62f, .5f, .36f, .72f, .62f); break;
                case "down": poly(c, false, s, .28f, .38f, .5f, .64f, .72f, .38f); break;
                case "left": poly(c, false, s, .62f, .28f, .36f, .5f, .62f, .72f); break;
                case "right": poly(c, false, s, .38f, .28f, .64f, .5f, .38f, .72f); break;
                case "power":
                    c.drawArc(new RectF(s * .2f, s * .22f, s * .8f, s * .82f), -60, 300, false, p);
                    c.drawLine(m, s * .14f, m, s * .5f, p);
                    break;
                case "home":
                    poly(c, false, s, .2f, .5f, .5f, .2f, .8f, .5f);
                    poly(c, false, s, .28f, .44f, .28f, .82f, .72f, .82f, .72f, .44f);
                    poly(c, false, s, .43f, .82f, .43f, .62f, .57f, .62f, .57f, .82f);
                    break;
                case "back": {
                    Path a = new Path();
                    a.moveTo(s * .3f, s * .38f);
                    a.lineTo(s * .62f, s * .38f);
                    a.arcTo(new RectF(s * .44f, s * .38f, s * .8f, s * .74f), -90, 180);
                    a.lineTo(s * .4f, s * .74f);
                    c.drawPath(a, p);
                    poly(c, false, s, .42f, .24f, .28f, .38f, .42f, .52f);
                    break;
                }
                case "play": fill(c, s, .32f, .2f, .32f, .8f, .8f, .5f); break;
                case "pause": {
                    p.setStyle(Paint.Style.FILL);
                    float r = s * .05f;
                    c.drawRoundRect(new RectF(s * .27f, s * .2f, s * .43f, s * .8f), r, r, p);
                    c.drawRoundRect(new RectF(s * .57f, s * .2f, s * .73f, s * .8f), r, r, p);
                    break;
                }
                case "stop": {
                    p.setStyle(Paint.Style.FILL);
                    c.drawRoundRect(new RectF(s * .26f, s * .26f, s * .74f, s * .74f), s * .06f, s * .06f, p);
                    break;
                }
                case "back30": case "fwd30": {
                    boolean fwd = name.equals("fwd30");
                    RectF r = new RectF(s * .14f, s * .14f, s * .86f, s * .86f);
                    if (fwd) c.drawArc(r, -60, 300, false, p); else c.drawArc(r, -120, -300, false, p);
                    float ax = fwd ? s * .68f : s * .32f;
                    poly(c, false, s, ax / s - (fwd ? .1f : -.1f), .08f, ax / s, .19f, ax / s - (fwd ? .1f : -.1f), .3f);
                    Paint tp = new Paint(Paint.ANTI_ALIAS_FLAG);
                    tp.setColor(color);
                    tp.setTextSize(s * .3f);
                    tp.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
                    tp.setTextAlign(Paint.Align.CENTER);
                    c.drawText("30", m, m + s * .11f, tp);
                    break;
                }
                case "spk": case "spk_minus": case "spk_plus": case "spk_slash": case "spk_wave": {
                    poly(c, true, s, .14f, .4f, .3f, .4f, .5f, .22f, .5f, .78f, .3f, .6f, .14f, .6f);
                    if (name.equals("spk_minus")) c.drawLine(s * .64f, m, s * .88f, m, p);
                    else if (name.equals("spk_plus")) { c.drawLine(s * .64f, m, s * .88f, m, p); c.drawLine(s * .76f, s * .38f, s * .76f, s * .62f, p); }
                    else if (name.equals("spk_slash")) { c.drawLine(s * .62f, s * .38f, s * .86f, s * .62f, p); c.drawLine(s * .86f, s * .38f, s * .62f, s * .62f, p); }
                    else if (name.equals("spk_wave")) {
                        c.drawArc(new RectF(s * .42f, s * .32f, s * .72f, s * .68f), -50, 100, false, p);
                        c.drawArc(new RectF(s * .42f, s * .2f, s * .88f, s * .8f), -50, 100, false, p);
                    }
                    break;
                }
                case "mic": case "mic_fill": {
                    RectF body = new RectF(s * .38f, s * .12f, s * .62f, s * .58f);
                    if (name.equals("mic_fill")) { p.setStyle(Paint.Style.FILL); c.drawRoundRect(body, s * .12f, s * .12f, p); p.setStyle(Paint.Style.STROKE); }
                    else c.drawRoundRect(body, s * .12f, s * .12f, p);
                    c.drawArc(new RectF(s * .26f, s * .3f, s * .74f, s * .72f), 0, 180, false, p);
                    c.drawLine(m, s * .72f, m, s * .86f, p);
                    break;
                }
                case "tv_play":
                    c.drawRoundRect(new RectF(s * .12f, s * .22f, s * .88f, s * .74f), s * .08f, s * .08f, p);
                    fill(c, s, .43f, .36f, .43f, .6f, .62f, .48f);
                    break;
                case "clipboard":
                    c.drawRoundRect(new RectF(s * .24f, s * .2f, s * .76f, s * .86f), s * .07f, s * .07f, p);
                    c.drawRoundRect(new RectF(s * .38f, s * .12f, s * .62f, s * .26f), s * .04f, s * .04f, p);
                    break;
                case "tv_plus":                     // a TV with a plus: connect to a TV
                    c.drawRoundRect(new RectF(s * .08f, s * .18f, s * .74f, s * .66f), s * .07f, s * .07f, p);
                    poly(c, false, s, .26f, .82f, .56f, .82f);
                    poly(c, false, s, .82f, .56f, .82f, .92f);
                    poly(c, false, s, .64f, .74f, 1f, .74f);
                    break;
                case "camera":
                    c.drawRoundRect(new RectF(s * .12f, s * .3f, s * .88f, s * .82f), s * .08f, s * .08f, p);
                    poly(c, false, s, .36f, .3f, .42f, .18f, .58f, .18f, .64f, .3f);
                    c.drawCircle(m, s * .56f, s * .14f, p);
                    break;
                case "waves":
                    c.drawCircle(m, m, s * .06f, p);
                    c.drawArc(new RectF(s * .28f, s * .28f, s * .72f, s * .72f), -45, 90, false, p);
                    c.drawArc(new RectF(s * .28f, s * .28f, s * .72f, s * .72f), 135, 90, false, p);
                    c.drawArc(new RectF(s * .12f, s * .12f, s * .88f, s * .88f), -45, 90, false, p);
                    c.drawArc(new RectF(s * .12f, s * .12f, s * .88f, s * .88f), 135, 90, false, p);
                    break;
                case "clock":
                    c.drawCircle(m, m, s * .36f, p);
                    poly(c, false, s, .5f, .3f, .5f, .5f, .64f, .6f);
                    break;
                case "refresh":
                    c.drawArc(new RectF(s * .2f, s * .2f, s * .8f, s * .8f), -80, 300, false, p);
                    poly(c, false, s, .5f, .08f, .62f, .2f, .5f, .32f);
                    break;
                case "send":
                    poly(c, true, s, .14f, .5f, .86f, .18f, .62f, .86f, .48f, .56f);
                    break;
                case "prev":
                    fill(c, s, .74f, .24f, .74f, .76f, .36f, .5f);
                    c.drawLine(s * .28f, s * .26f, s * .28f, s * .74f, p);
                    break;
                case "next":
                    fill(c, s, .26f, .24f, .26f, .76f, .64f, .5f);
                    c.drawLine(s * .72f, s * .26f, s * .72f, s * .74f, p);
                    break;
                case "phone":                       // a phone with a small play mark: a file from this phone
                    c.drawRoundRect(new RectF(s * .28f, s * .1f, s * .72f, s * .9f), s * .08f, s * .08f, p);
                    fill(c, s, .44f, .38f, .44f, .62f, .62f, .5f);
                    break;
                case "moon": {                      // sleep timer
                    Path a = new Path();
                    a.addCircle(m, m, s * .32f, Path.Direction.CW);
                    Path cut = new Path();
                    cut.addCircle(s * .66f, s * .36f, s * .28f, Path.Direction.CW);
                    a.op(cut, Path.Op.DIFFERENCE);
                    c.drawPath(a, p);
                    break;
                }
                case "help": {                      // a question mark in a circle
                    c.drawCircle(m, m, s * .38f, p);
                    Paint tp = new Paint(Paint.ANTI_ALIAS_FLAG);
                    tp.setColor(color);
                    tp.setTextSize(s * .5f);
                    tp.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
                    tp.setTextAlign(Paint.Align.CENTER);
                    c.drawText("?", m, m + s * .18f, tp);
                    break;
                }
                case "gear": {                      // settings: Material Icons "settings" (outlined), Apache-2.0
                    if (gearPath == null) gearPath = androidx.core.graphics.PathParser.createPathFromPathData(GEAR);
                    c.save();
                    c.scale(s / 24f, s / 24f);
                    p.setStyle(Paint.Style.FILL);
                    c.drawPath(gearPath, p);
                    p.setStyle(Paint.Style.STROKE);
                    c.restore();
                    break;
                }
            }
            c.restore();
        }

        /** Material Icons "settings", outlined (github.com/google/material-design-icons, Apache License 2.0). */
        private static final String GEAR = "M19.43 12.98c.04-.32.07-.64.07-.98 0-.34-.03-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46c-.09-.16-.26-.25-.44-.25-.06 0-.12.01-.17.03l-2.49 1c-.52-.4-1.08-.73-1.69-.98l-.38-2.65C14.46 2.18 14.25 2 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1c-.06-.02-.12-.03-.18-.03-.17 0-.34.09-.43.25l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.07.65-.07.98 0 .33.03.66.07.98l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.09.16.26.25.44.25.06 0 .12-.01.17-.03l2.49-1c.52.4 1.08.73 1.69.98l.38 2.65c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.65c.61-.25 1.17-.59 1.69-.98l2.49 1c.06.02.12.03.18.03.17 0 .34-.09.43-.25l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65zm-1.98-1.71c.04.31.05.52.05.73 0 .21-.02.43-.05.73l-.14 1.13.89.7 1.08.84-.7 1.21-1.27-.51-1.04-.42-.9.68c-.43.32-.84.56-1.25.73l-1.06.43-.16 1.13-.2 1.35h-1.4l-.19-1.35-.16-1.13-1.06-.43c-.43-.18-.83-.41-1.23-.71l-.91-.7-1.06.43-1.27.51-.7-1.21 1.08-.84.89-.7-.14-1.13c-.03-.31-.05-.54-.05-.74s.02-.43.05-.73l.14-1.13-.89-.7-1.08-.84.7-1.21 1.27.51 1.04.42.9-.68c.43-.32.84-.56 1.25-.73l1.06-.43.16-1.13.2-1.35h1.39l.19 1.35.16 1.13 1.06.43c.43.18.83.41 1.23.71l.91.7 1.06-.43 1.27-.51.7 1.21-1.07.85-.89.7.14 1.13zM12 8c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4-1.79-4-4-4zm0 6c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z";
        private static Path gearPath;

        private void poly(Canvas c, boolean close, float s, float... pts) {
            Path a = new Path();
            a.moveTo(pts[0] * s, pts[1] * s);
            for (int i = 2; i < pts.length; i += 2) a.lineTo(pts[i] * s, pts[i + 1] * s);
            if (close) a.close();
            c.drawPath(a, p);
        }

        private void fill(Canvas c, float s, float... pts) {
            p.setStyle(Paint.Style.FILL);
            poly(c, true, s, pts);
            p.setStyle(Paint.Style.STROKE);
        }

        @Override public void setAlpha(int a) { p.setAlpha(a); }
        @Override public void setColorFilter(ColorFilter f) { p.setColorFilter(f); }
        @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
        @Override public int getIntrinsicWidth() { return 48; }
        @Override public int getIntrinsicHeight() { return 48; }
    }

    // ------------------------------------------------------------ artwork

    final class Artwork extends View {
        private Bitmap bmp;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
        String url;

        Artwork() {
            super(ctx);
            setOutlineProvider(new ViewOutlineProvider() {
                @Override public void getOutline(View v, Outline o) { o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), dp(14)); }
            });
            setClipToOutline(true);
        }

        void setBitmap(Bitmap b) { bmp = b; invalidate(); }

        @Override protected void onMeasure(int w, int h) {
            int width = MeasureSpec.getSize(w);
            setMeasuredDimension(width, Math.round(width * 9f / 16f));
        }

        @Override protected void onDraw(Canvas c) {
            int w = getWidth(), h = getHeight();
            paint.setColor(Color.BLACK);   // opaque: the placeholder leaves a translucent colour behind
            if (bmp != null) {
                float sc = Math.max(w / (float) bmp.getWidth(), h / (float) bmp.getHeight());
                Matrix mx = new Matrix();
                mx.setScale(sc, sc);
                mx.postTranslate((w - bmp.getWidth() * sc) / 2, (h - bmp.getHeight() * sc) / 2);
                BitmapShader sh = new BitmapShader(bmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP);
                sh.setLocalMatrix(mx);
                paint.setShader(sh);
                c.drawRect(0, 0, w, h, paint);
                paint.setShader(null);
            } else {
                paint.setShader(new LinearGradient(0, 0, w, h, Color.rgb(46, 59, 82), Color.rgb(74, 51, 82), Shader.TileMode.CLAMP));
                c.drawRect(0, 0, w, h, paint);
                paint.setShader(null);
                paint.setColor(0x5AFFFFFF);
                float cx = w / 2f, cy = h / 2f, s = dp(16);
                Path t = new Path();
                t.moveTo(cx - s * .6f, cy - s);
                t.lineTo(cx - s * .6f, cy + s);
                t.lineTo(cx + s, cy);
                t.close();
                c.drawPath(t, paint);
            }
        }
    }

    // ------------------------------------------------------------ refresh button on artwork

    /** 30dp translucent disc with a white circular arrow; spins while refreshing. */
    final class RefreshButton extends View {
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private float angle;
        private android.animation.ValueAnimator spin;
        private long spinStart;

        RefreshButton(Runnable onTap) {
            super(ctx);
            setClickable(true);
            setContentDescription(L.t("Обновить данные"));
            setOutlineProvider(ViewOutlineProvider.BACKGROUND);
            setBackground(new RippleDrawable(ColorStateList.valueOf(0x55FFFFFF), null, oval(0xFFFFFFFF)));
            setOnClickListener(v -> { spin(); onTap.run(); });
        }

        void spin() {
            spinStart = System.currentTimeMillis();
            if (spin != null && spin.isRunning()) return;
            spin = android.animation.ValueAnimator.ofFloat(0, 360);
            spin.setDuration(600);
            spin.setInterpolator(new android.view.animation.LinearInterpolator());
            spin.setRepeatCount(android.animation.ValueAnimator.INFINITE);
            spin.addUpdateListener(a -> { angle = (float) a.getAnimatedValue(); invalidate(); });
            spin.start();
        }

        /** Stops after the current turn, never before 600 ms have passed. */
        void done() {
            if (spin == null) return;
            long left = Math.max(0, 600 - (System.currentTimeMillis() - spinStart));
            postDelayed(() -> { if (spin != null) { spin.setRepeatCount(0); } }, left);
        }

        @Override public boolean onTouchEvent(MotionEvent e) {
            if (e.getActionMasked() == MotionEvent.ACTION_DOWN) animate().scaleX(0.88f).scaleY(0.88f).setDuration(90).start();
            else if (e.getActionMasked() == MotionEvent.ACTION_UP || e.getActionMasked() == MotionEvent.ACTION_CANCEL)
                animate().scaleX(1f).scaleY(1f).setDuration(160).start();
            return super.onTouchEvent(e);
        }

        @Override protected void onDraw(Canvas c) {
            float w = getWidth(), r = w / 2f;
            p.setStyle(Paint.Style.FILL);
            p.setColor(0x73000000);
            c.drawCircle(r, r, r, p);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(dp(1));
            p.setColor(0x40FFFFFF);
            c.drawCircle(r, r, r - dp(0.5f), p);
            c.save();
            c.rotate(angle, r, r);
            float ir = w * 0.24f;
            p.setColor(Color.WHITE);
            p.setStrokeWidth(dp(1.8f));
            p.setStrokeCap(Paint.Cap.ROUND);
            p.setStrokeJoin(Paint.Join.ROUND);
            c.drawArc(new RectF(r - ir, r - ir, r + ir, r + ir), -60, 300, false, p);
            // arrowhead at the arc's end (-60°)
            double a = Math.toRadians(-60);
            float ex = r + (float) (ir * Math.cos(a)), ey = r + (float) (ir * Math.sin(a));
            float h = w * 0.13f;
            Path ar = new Path();
            ar.moveTo(ex - h, ey - h * 0.15f);
            ar.lineTo(ex + h * 0.15f, ey - h * 0.1f);
            ar.lineTo(ex + h * 0.05f, ey + h);
            c.drawPath(ar, p);
            c.restore();
        }
    }

    /** Artwork with the refresh disc in its top-right corner. */
    FrameLayout artFrame(Artwork art, RefreshButton btn) {
        FrameLayout f = new FrameLayout(ctx);
        f.addView(art, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        FrameLayout.LayoutParams bp = new FrameLayout.LayoutParams(dp(30), dp(30), Gravity.TOP | Gravity.END);
        bp.setMargins(0, dp(8), dp(8), 0);
        btn.setElevation(dp(2));
        f.addView(btn, bp);
        return f;
    }

    // ------------------------------------------------------------ slim progress + slider

    final class Progress extends View {
        float value;
        Progress() { super(ctx); setLayoutParams(lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(4))); }
        void set(float v) { value = Math.max(0, Math.min(1, v)); invalidate(); }
        @Override protected void onDraw(Canvas c) {
            Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
            float h = getHeight(), w = getWidth(), r = h / 2;
            p.setColor(fgA(0.15f));
            c.drawRoundRect(new RectF(0, 0, w, h), r, r, p);
            p.setColor(accent);
            c.drawRoundRect(new RectF(0, 0, Math.max(h, w * value), h), r, r, p);
        }
    }

    interface OnValue { void onValue(int v, boolean fromUser); }

    final class Slider extends View {
        int value = 50;
        boolean dragging;
        OnValue listener;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        Slider() {
            super(ctx);
            setLayoutParams(new LinearLayout.LayoutParams(0, dp(32), 1f));
        }

        void set(int v) { if (!dragging) { value = v; invalidate(); } }

        @Override protected void onDraw(Canvas c) {
            float k = dp(18), w = getWidth() - k, y = getHeight() / 2f, th = dp(4);
            float x = k / 2 + w * value / 100f;
            p.setShadowLayer(0, 0, 0, 0);
            p.setColor(fgA(0.15f));
            c.drawRoundRect(new RectF(k / 2, y - th / 2, k / 2 + w, y + th / 2), th / 2, th / 2, p);
            p.setColor(fgA(0.85f));
            c.drawRoundRect(new RectF(k / 2, y - th / 2, x, y + th / 2), th / 2, th / 2, p);
            p.setColor(0x33000000);
            c.drawCircle(x, y + dp(0.8f), k / 2, p);
            p.setColor(Color.WHITE);
            c.drawCircle(x, y, k / 2 - dp(0.5f), p);
        }

        @Override public boolean onTouchEvent(MotionEvent e) {
            float k = dp(18);
            int v = Math.round(Math.max(0, Math.min(1, (e.getX() - k / 2) / (getWidth() - k))) * 100);
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    dragging = true;
                    getParent().requestDisallowInterceptTouchEvent(true);
                    // fallthrough
                case MotionEvent.ACTION_MOVE:
                    if (v != value) { value = v; invalidate(); if (listener != null) listener.onValue(v, true); }
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    dragging = false;
                    return true;
            }
            return false;
        }
    }

    // ------------------------------------------------------------ touchpad (swipe → d-pad, tap → OK)

    interface OnPad { void key(int code); }

    final class Touchpad extends View {
        private final GestureDetector gd;
        private float accX, accY;
        private long lastEmit;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private float rippleX = -1, rippleY, rippleR;

        Touchpad(OnPad pad) {
            super(ctx);
            setLayoutParams(margins(lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(210)), 12, 4, 12, 4));
            setBackground(round(fgA(dark ? 0.08f : 0.045f), 18));
            gd = new GestureDetector(ctx, new GestureDetector.SimpleOnGestureListener() {
                @Override public boolean onDown(MotionEvent e) { accX = accY = 0; return true; }
                @Override public boolean onSingleTapUp(MotionEvent e) { flash(e); pad.key(Atv.OK); return true; }
                @Override public void onLongPress(MotionEvent e) { flash(e); pad.key(Atv.BACK); performHapticFeedback(0); }
                @Override public boolean onScroll(MotionEvent a, MotionEvent b, float dx, float dy) {
                    accX -= dx; accY -= dy;
                    float step = dp(42);
                    long now = System.currentTimeMillis();
                    if (Math.abs(accX) > Math.abs(accY)) accY = 0; else accX = 0;
                    if (Math.max(Math.abs(accX), Math.abs(accY)) >= step && now - lastEmit > 70) {
                        if (Math.abs(accX) > Math.abs(accY)) { pad.key(accX > 0 ? Atv.RIGHT : Atv.LEFT); accX -= Math.signum(accX) * step; }
                        else { pad.key(accY > 0 ? Atv.DOWN : Atv.UP); accY -= Math.signum(accY) * step; }
                        lastEmit = now;
                        performHapticFeedback(1 /* VIRTUAL_KEY */);
                    }
                    return true;
                }
            });
        }

        private void flash(MotionEvent e) {
            rippleX = e.getX(); rippleY = e.getY(); rippleR = dp(10);
            postInvalidateOnAnimation();
        }

        @Override public boolean onTouchEvent(MotionEvent e) {
            getParent().requestDisallowInterceptTouchEvent(e.getActionMasked() != MotionEvent.ACTION_UP);
            return gd.onTouchEvent(e) || super.onTouchEvent(e);
        }

        @Override protected void onDraw(Canvas c) {
            p.setColor(fgA(0.35f));
            p.setTextSize(dp(13));
            p.setTextAlign(Paint.Align.CENTER);
            c.drawText(L.t("Свайп — стрелки · касание — OK"), getWidth() / 2f, getHeight() / 2f, p);
            p.setTextSize(dp(11));
            p.setColor(fgA(0.25f));
            c.drawText(L.t("долгое нажатие — назад"), getWidth() / 2f, getHeight() / 2f + dp(18), p);
            if (rippleX >= 0) {
                p.setColor(accA(Math.max(0, 0.35f - rippleR / dp(160))));
                c.drawCircle(rippleX, rippleY, rippleR, p);
                rippleR += dp(6);
                if (rippleR < dp(60)) postInvalidateOnAnimation(); else rippleX = -1;
            }
        }
    }

    // ------------------------------------------------------------ segmented control

    LinearLayout segmented(String[] items, int selected, java.util.function.IntConsumer on) {
        LinearLayout box = row();
        box.setBackground(round(surfVar, 12));
        box.setPadding(dp(2), dp(2), dp(2), dp(2));
        TextView[] tv = new TextView[items.length];
        for (int i = 0; i < items.length; i++) {
            final int idx = i;
            TextView t = text(items[i], 13, fg, false);
            t.setGravity(Gravity.CENTER);
            t.setPadding(dp(14), dp(6), dp(14), dp(6));
            tv[i] = t;
            t.setOnClickListener(v -> {
                for (int j = 0; j < tv.length; j++) styleSeg(tv[j], j == idx);
                on.accept(idx);
            });
            box.addView(t, lpW(1));
        }
        for (int j = 0; j < tv.length; j++) styleSeg(tv[j], j == selected);
        return box;
    }

    private void styleSeg(TextView t, boolean on) {
        t.setBackground(on ? round(primC, 10) : null);   // M3: the chosen segment in the primary container
        t.setTextColor(on ? onPrimC : onSurfVar);
        t.setElevation(0);
        t.setTypeface(Typeface.create(on ? "sans-serif-medium" : "sans-serif", Typeface.NORMAL));
    }
}
