// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.app.Activity;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.AnimationUtils;
import android.view.animation.Interpolator;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

/** First-run tips shown one at a time: the screen dims, a spotlight moves to the part of the
 *  app being explained, and a small card with one or two lines slides in next to it.
 *  Motion uses the platform's own Material curves (fast_out_slow_in / linear_out_slow_in /
 *  fast_out_linear_in) and respects the system "animation scale" setting. */
final class Tour {

    static final class Step {
        final View target; final String title, body;
        Step(View target, String title, String body) { this.target = target; this.title = title; this.body = body; }
    }

    private final Activity a;
    private final Ui ui;
    private final ScrollView scroll;
    private final List<Step> steps = new ArrayList<>();
    private final Runnable fullGuide;
    private final Interpolator standard, decelerate, accelerate;

    private FrameLayout overlay;
    private Scrim scrim;
    private LinearLayout bubble;
    private TextView counter, title, body, next, skip;
    private int index = -1;
    private boolean busy;

    private Tour(Activity a, Ui ui, ScrollView scroll, List<Step> all, Runnable fullGuide) {
        this.a = a; this.ui = ui; this.scroll = scroll; this.fullGuide = fullGuide;
        for (Step s : all) if (s.target != null && s.target.isShown()) steps.add(s);   // only what's on screen now
        standard = AnimationUtils.loadInterpolator(a, android.R.interpolator.fast_out_slow_in);
        decelerate = AnimationUtils.loadInterpolator(a, android.R.interpolator.linear_out_slow_in);
        accelerate = AnimationUtils.loadInterpolator(a, android.R.interpolator.fast_out_linear_in);
    }

    static void start(Activity a, Ui ui, ScrollView scroll, List<Step> steps, Runnable fullGuide) {
        Tour t = new Tour(a, ui, scroll, steps, fullGuide);
        if (!t.steps.isEmpty()) t.open();
    }

    // ------------------------------------------------------------ overlay

    private void open() {
        ViewGroup decor = (ViewGroup) a.getWindow().getDecorView();
        overlay = new FrameLayout(a);
        overlay.setClickable(true);                        // the app underneath doesn't react while the tips run
        scrim = new Scrim(a, ui);
        overlay.addView(scrim, new FrameLayout.LayoutParams(-1, -1));
        scrim.setOnClickListener(v -> advance());

        bubble = ui.col();
        bubble.setBackground(ui.round(ui.card, 18));
        bubble.setElevation(ui.dp(8));
        bubble.setPadding(ui.dp(18), ui.dp(14), ui.dp(12), ui.dp(8));
        counter = ui.text("", 12, ui.fgA(0.45f), true);
        title = ui.text("", 17, ui.fg, true);
        title.setPadding(0, ui.dp(2), 0, ui.dp(4));
        body = ui.text("", 15, ui.fgA(0.75f), false);
        body.setLineSpacing(ui.dp(2), 1f);
        bubble.addView(counter);
        bubble.addView(title);
        bubble.addView(body);
        LinearLayout buttons = ui.row();
        buttons.setGravity(Gravity.CENTER_VERTICAL | Gravity.END);
        skip = button(L.t("Пропустить"), false);
        skip.setOnClickListener(v -> close());
        next = button(L.t("Далее"), true);
        next.setOnClickListener(v -> advance());
        buttons.addView(skip);
        buttons.addView(next);
        bubble.addView(buttons, ui.margins(Ui.lp(-1, -2), 0, 8, 0, 0));
        FrameLayout.LayoutParams bp = new FrameLayout.LayoutParams(-1, -2, Gravity.TOP);
        bp.leftMargin = bp.rightMargin = ui.dp(16);
        overlay.addView(bubble, bp);
        bubble.setAlpha(0f);

        decor.addView(overlay, new FrameLayout.LayoutParams(-1, -1));
        overlay.setAlpha(0f);
        overlay.animate().alpha(1f).setDuration(220).setInterpolator(decelerate).start();
        overlay.post(this::advance);                       // after the overlay has its size
    }

    /** Status bar / navigation bar heights, so the card never hides under them. */
    private int[] insets() {
        android.view.WindowInsets wi = overlay.getRootWindowInsets();
        if (wi == null) return new int[]{ui.dp(24), ui.dp(24)};
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            android.graphics.Insets i = wi.getInsets(android.view.WindowInsets.Type.systemBars()
                    | android.view.WindowInsets.Type.displayCutout());
            return new int[]{i.top, i.bottom};
        }
        return new int[]{wi.getSystemWindowInsetTop(), wi.getSystemWindowInsetBottom()};
    }

    private TextView button(String text, boolean primary) {
        TextView b = ui.text(text, 15, primary ? 0xFFFFFFFF : ui.accent, true);
        b.setGravity(Gravity.CENTER);
        b.setPadding(ui.dp(16), ui.dp(9), ui.dp(16), ui.dp(9));
        b.setBackground(primary ? ui.round(ui.accent, 20) : ui.rippleRound(0, 20));
        return b;
    }

    private void advance() {
        if (busy) return;
        if (index + 1 >= steps.size()) { close(); return; }
        busy = true;
        index++;
        boolean first = index == 0;
        Runnable show = () -> scrollTo(steps.get(index).target, this::reveal);
        if (first) show.run();
        else bubble.animate().alpha(0f).translationY(-ui.dp(8)).setDuration(140)
                .setInterpolator(accelerate).withEndAction(show).start();
    }

    /** Bring the target into view (smoothly), then continue. */
    private void scrollTo(View target, Runnable then) {
        int[] t = new int[2], s = new int[2];
        target.getLocationInWindow(t);
        scroll.getLocationInWindow(s);
        int top = t[1] - s[1], h = scroll.getHeight();
        int want = scroll.getScrollY() + top - Math.max(ui.dp(96), (h - target.getHeight()) / 3);
        if (top < ui.dp(72) || top + target.getHeight() > h - ui.dp(220)) {
            scroll.smoothScrollTo(0, Math.max(0, want));
            scroll.postDelayed(then, 320);
        } else then.run();
    }

    private void reveal() {
        Step s = steps.get(index);
        counter.setText((index + 1) + " / " + steps.size());
        title.setText(s.title);
        body.setText(s.body);
        boolean last = index == steps.size() - 1;
        next.setText(last ? L.t("Готово") : L.t("Далее"));
        skip.setVisibility(last ? View.GONE : View.VISIBLE);          // nothing left to skip
        if (last && fullGuide != null) {                     // the long guide is there, but never forced
            TextView more = ui.text(L.t("Полная инструкция"), 14, ui.accent, false);
            more.setPadding(0, ui.dp(8), 0, 0);
            more.setOnClickListener(v -> { close(); fullGuide.run(); });
            bubble.addView(more, 3);
        }

        // spotlight: the target's rectangle in overlay coordinates, padded a little
        int[] t = new int[2], o = new int[2];
        s.target.getLocationInWindow(t);
        overlay.getLocationInWindow(o);
        float pad = ui.dp(6);
        RectF to = new RectF(t[0] - o[0] - pad, t[1] - o[1] - pad,
                t[0] - o[0] + s.target.getWidth() + pad, t[1] - o[1] + s.target.getHeight() + pad);
        scrim.moveTo(to, standard);

        // card below the spotlight when there is room, else above
        bubble.measure(View.MeasureSpec.makeMeasureSpec(overlay.getWidth() - ui.dp(32), View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
        int bh = bubble.getMeasuredHeight(), gap = ui.dp(14);
        int[] bars = insets();
        int minY = bars[0] + ui.dp(12), maxY = overlay.getHeight() - bars[1] - ui.dp(12);
        boolean below = to.bottom + gap + bh <= maxY;
        float y = below ? to.bottom + gap : Math.max(minY, to.top - gap - bh);
        FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) bubble.getLayoutParams();
        lp.topMargin = Math.round(y);                      // the resting place; animation only adds translation
        bubble.setLayoutParams(lp);
        bubble.setScaleX(0.96f); bubble.setScaleY(0.96f);
        bubble.setPivotY(below ? 0 : bh);
        float from = below ? -ui.dp(10) : ui.dp(10);
        bubble.setTranslationY(from);
        bubble.animate().alpha(1f).translationY(0).scaleX(1f).scaleY(1f)
                .setStartDelay(120).setDuration(260).setInterpolator(decelerate)
                .withEndAction(() -> busy = false).start();
    }

    private void close() {
        if (overlay == null) return;
        FrameLayout o = overlay;
        overlay = null;
        o.animate().alpha(0f).setDuration(180).setInterpolator(accelerate)
                .setListener(new AnimatorListenerAdapter() {
                    @Override public void onAnimationEnd(Animator an) {
                        ((ViewGroup) o.getParent()).removeView(o);
                    }
                }).start();
    }

    // ------------------------------------------------------------ dimmed layer with a moving spotlight

    static final class Scrim extends View {
        private final Paint dim = new Paint(Paint.ANTI_ALIAS_FLAG), ring = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path path = new Path();
        private final RectF hole = new RectF();
        private final float radius;
        private ValueAnimator anim;

        Scrim(Context c, Ui ui) {
            super(c);
            dim.setColor(ui.dark ? 0xC8000000 : 0xA6000000);
            ring.setStyle(Paint.Style.STROKE);
            ring.setStrokeWidth(ui.dp(2));
            ring.setColor(Ui.alpha(ui.accent, 0.9f));
            radius = ui.dp(18);
        }

        void moveTo(RectF to, Interpolator in) {
            if (anim != null) anim.cancel();
            if (hole.isEmpty()) {                     // first step: grow out of the target's centre
                hole.set(to.centerX(), to.centerY(), to.centerX(), to.centerY());
            }
            RectF from = new RectF(hole);
            anim = ValueAnimator.ofFloat(0f, 1f);
            anim.setDuration(380);
            anim.setInterpolator(in);
            anim.addUpdateListener(va -> {
                float f = (float) va.getAnimatedValue();
                hole.set(from.left + (to.left - from.left) * f, from.top + (to.top - from.top) * f,
                        from.right + (to.right - from.right) * f, from.bottom + (to.bottom - from.bottom) * f);
                invalidate();
            });
            anim.start();
        }

        @Override protected void onDraw(Canvas c) {
            path.reset();
            path.setFillType(Path.FillType.EVEN_ODD);
            path.addRect(0, 0, getWidth(), getHeight(), Path.Direction.CW);
            if (!hole.isEmpty()) path.addRoundRect(hole, radius, radius, Path.Direction.CW);
            c.drawPath(path, dim);
            if (!hole.isEmpty()) c.drawRoundRect(hole, radius, radius, ring);
        }
    }
}
