// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.animation.ValueAnimator;
import android.app.Activity;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.AnimationUtils;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/** First launch: the language first (Русский / English, applied app-wide), then the regular
 *  first-run tips (Tour) in that language. Covers the screen, both languages on one page;
 *  the logo sits in slowly spreading rings in the accent colour. Shown once
 *  (prefs "welcome_done"); Settings → About replays the tips. */
final class Welcome {
    private Welcome() {}

    static void show(Activity a, Ui ui, Runnable done) {
        ViewGroup decor = (ViewGroup) a.getWindow().getDecorView();
        FrameLayout overlay = new FrameLayout(a);
        overlay.setClickable(true);                       // nothing underneath reacts meanwhile
        overlay.setBackgroundColor(ui.page);

        LinearLayout col = ui.col();
        col.setGravity(Gravity.CENTER_HORIZONTAL);
        col.setPadding(ui.dp(28), ui.dp(24), ui.dp(28), ui.dp(24));

        FrameLayout logoBox = new FrameLayout(a);
        Rings rings = new Rings(a, ui);
        logoBox.addView(rings, new FrameLayout.LayoutParams(-1, -1));
        ImageView logo = new ImageView(a);
        logo.setImageResource(R.mipmap.ic_launcher);
        logoBox.addView(logo, new FrameLayout.LayoutParams(ui.dp(96), ui.dp(96), Gravity.CENTER));
        col.addView(logoBox, new LinearLayout.LayoutParams(ui.dp(240), ui.dp(220)));

        TextView title = ui.text("Fling", 28, ui.fg, true);          // the same in both languages
        title.setGravity(Gravity.CENTER);
        col.addView(title, ui.margins(Ui.lp(-1, -2), 0, 8, 0, 24));

        final boolean[] closing = {false};
        Runnable[] finish = new Runnable[1];
        // each card entirely in its own language — nothing mixed on one line
        for (String[] lang : new String[][]{{"ru", "Русский", "Продолжить на русском"}, {"en", "English", "Continue in English"}}) {
            boolean on = lang[0].equals(L.english() ? "en" : "ru");
            LinearLayout b = ui.col();
            b.setGravity(Gravity.CENTER);
            b.setPadding(ui.dp(16), ui.dp(14), ui.dp(16), ui.dp(14));
            b.setBackground(ui.rippleRound(on ? ui.accA(0.16f) : ui.fgA(0.06f), 16));
            b.addView(ui.text(lang[1], 18, ui.fg, true));
            b.addView(ui.text(lang[2], 13, ui.fgA(0.55f), false));
            b.setOnClickListener(v -> {
                if (closing[0]) return;
                closing[0] = true;
                L.setLang(a, lang[0]);
                finish[0].run();
            });
            col.addView(b, ui.margins(Ui.lp(-1, -2), 0, 0, 0, 10));
        }

        FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER);
        overlay.addView(col, cp);
        decor.addView(overlay, new FrameLayout.LayoutParams(-1, -1));

        // in: the column rises and fades in on the platform's decelerate curve
        col.setAlpha(0f);
        col.setTranslationY(ui.dp(24));
        col.animate().alpha(1f).translationY(0).setDuration(420)
                .setInterpolator(AnimationUtils.loadInterpolator(a, android.R.interpolator.linear_out_slow_in)).start();

        finish[0] = () -> overlay.animate().alpha(0f).setDuration(220)
                .setInterpolator(AnimationUtils.loadInterpolator(a, android.R.interpolator.fast_out_linear_in))
                .withEndAction(() -> {
                    rings.stop();
                    decor.removeView(overlay);
                    done.run();
                }).start();
    }

    /** Three rings spreading from the logo, fading as they grow. */
    private static final class Rings extends View {
        private final Ui ui;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final ValueAnimator anim = ValueAnimator.ofFloat(0f, 1f);
        private float t;

        Rings(Context c, Ui ui) {
            super(c);
            this.ui = ui;
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(ui.dp(2));
            anim.setDuration(2400);
            anim.setRepeatCount(ValueAnimator.INFINITE);
            anim.setInterpolator(null);
            anim.addUpdateListener(v -> { t = (float) v.getAnimatedValue(); invalidate(); });
            anim.start();
        }

        void stop() { anim.cancel(); }

        @Override protected void onDetachedFromWindow() { anim.cancel(); super.onDetachedFromWindow(); }

        @Override protected void onDraw(Canvas c) {
            float cx = getWidth() / 2f, cy = getHeight() / 2f;
            float min = ui.dp(52), max = Math.min(cx, cy);
            for (int i = 0; i < 3; i++) {
                float k = (t + i / 3f) % 1f;
                p.setColor(ui.accA((1f - k) * 0.55f));
                c.drawCircle(cx, cy, min + (max - min) * k, p);
            }
        }
    }
}
