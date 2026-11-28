// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.InputType;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.TextWatcher;
import android.text.style.ForegroundColorSpan;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.util.ArrayDeque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** LIVE: the TV screen on the phone (scrcpy over Fling's ADB, see Scrcpy) and a remote on top.
 *
 *  Touch rule on the video — one gesture, one TV key, always the same, overlay shown or not:
 *    tap = OK, swipe = arrow in that direction (held after the swipe = auto-repeat),
 *    press and hold = Back.
 *  The mini remote (Back, Home, arrows, volume, play/pause, keyboard, close) opens with the
 *  remote handle in the bottom corner and hides itself 3 s after the last touch. A one-line hint
 *  with this rule shows on the first LIVE start. The phone's volume keys change the TV volume.
 *
 *  Extras: "host" (String), "port" (int, ADB port of the TV). */
public final class LiveActivity extends Activity implements SurfaceHolder.Callback {
    static final String EXTRA_HOST = "host", EXTRA_PORT = "port";

    private static final int HIDE_MS = 3000;
    private static final int REPEAT_FIRST_MS = 400, REPEAT_EVERY_MS = 110;
    private static final int MAX_RETRIES = 6;
    private static final int MAX_LOWER = 2;                     // adaptive quality: at most two steps down
    private static final int STALL_WINDOW_MS = 20_000, STALLS_TO_LOWER = 3, SETTLE_MS = 5000;
    // the dark-theme colours of Ui: LIVE is always on black, whatever the app theme
    private static final int GREEN = 0xFF30D158, ORANGE = 0xFFFF9F0A, RED = 0xFFFF453A;
    private static final int PANEL = 0xB3101012, WHITE = 0xFFFFFFFF;

    private final Handler h = new Handler(Looper.getMainLooper());
    /** Control messages go out in order on one thread (socket writes never run on the main thread). */
    private final ExecutorService ctl = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "live-ctl");
        t.setDaemon(true);
        return t;
    });

    private Ui ui;
    private String host;
    private int port;

    // views
    private FrameLayout root, overlay;
    /** The three groups of keys: moved under the video when upright, to its sides when sideways. */
    private LinearLayout leftKeys, bottomKeys, below;
    private View dpadView;
    private boolean portrait;
    private AspectFrame frame;
    private SurfaceView video;
    private LinearLayout status, chipRow;
    private ProgressBar spinner;
    private TextView statusText, retry, chip, badge, hint;
    private ImageView handle;
    private EditText kb;

    // connection state (main thread only)
    private boolean resumed, surfaceReady;
    private int gen;                         // bumps on every connect/disconnect; stale results are dropped
    private volatile Scrcpy.Session session;
    private VideoDecoder dec;
    private boolean tailscale;
    private int lower;                       // adaptive quality steps taken (0..MAX_LOWER)
    private int retries;
    private String lastError;
    private long connectedAt, lastReset;
    private int lastStalls, slowSeconds;
    private final ArrayDeque<Long> stallTimes = new ArrayDeque<>();

    // network changes
    private ConnectivityManager cm;
    private ConnectivityManager.NetworkCallback netCb;
    private Network currentNet;
    private boolean netKnown;

    // ------------------------------------------------------------ lifecycle

    @Override protected void onCreate(Bundle b) {
        L.init(this);
        super.onCreate(b);
        host = getIntent().getStringExtra(EXTRA_HOST);
        port = getIntent().getIntExtra(EXTRA_PORT, 5555);
        if (host == null || host.isEmpty()) { finish(); return; }
        ui = new Ui(this);
        Window w = getWindow();
        w.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING
                | WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN);
        w.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.BLACK));
        w.setStatusBarColor(Color.BLACK);
        w.setNavigationBarColor(Color.BLACK);
        setContentView(build());
        arrange();
        cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
    }

    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        immersive();
        registerNetwork();
        retries = 0;
        if (surfaceReady && session == null) connect(false);
    }

    @Override protected void onPause() {
        resumed = false;
        unregisterNetwork();
        hideKeyboard();
        disconnect();
        super.onPause();
    }

    @Override protected void onDestroy() {
        disconnect();
        if (atv != null) atv.disconnect();
        h.removeCallbacksAndMessages(null);
        ctl.shutdown();
        super.onDestroy();
    }

    /** Rotation without a restart (configChanges): the stream keeps going, the keys move. */
    @Override public void onConfigurationChanged(android.content.res.Configuration c) {
        super.onConfigurationChanged(c);
        arrange();
    }

    /** Upright: video on top, the remote under it — always there, under the thumb.
     *  Sideways: the video fills the screen, the keys on its sides, shown / hidden by the remote button. */
    private void arrange() {
        portrait = getResources().getConfiguration().orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT;
        for (View v : new View[]{leftKeys, dpadView, bottomKeys}) {
            if (v.getParent() != null) ((ViewGroup) v.getParent()).removeView(v);
        }
        FrameLayout.LayoutParams fl = (FrameLayout.LayoutParams) frame.getLayoutParams();
        if (portrait) {
            fl.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
            fl.topMargin = ui.dp(36);
            leftKeys.setOrientation(LinearLayout.HORIZONTAL);
            below.addView(leftKeys, new LinearLayout.LayoutParams(-2, -2));
            int d = ui.dp(232);
            LinearLayout.LayoutParams dp = new LinearLayout.LayoutParams(d, d);
            dp.topMargin = ui.dp(22);
            dp.bottomMargin = ui.dp(22);
            below.addView(dpadView, dp);
            LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(-2, -2);
            bp.bottomMargin = ui.dp(28);
            below.addView(bottomKeys, bp);
            below.setVisibility(View.VISIBLE);
            overlay.setVisibility(View.GONE);
            if (handle != null) handle.setVisibility(View.GONE);
        } else {
            fl.gravity = Gravity.CENTER;
            fl.topMargin = 0;
            leftKeys.setOrientation(LinearLayout.VERTICAL);
            FrameLayout.LayoutParams lpL = new FrameLayout.LayoutParams(-2, -2, Gravity.START | Gravity.CENTER_VERTICAL);
            lpL.leftMargin = ui.dp(16);
            overlay.addView(leftKeys, lpL);
            overlay.addView(dpadView, dpadLp());
            FrameLayout.LayoutParams lpB = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
            lpB.bottomMargin = ui.dp(14);
            overlay.addView(bottomKeys, lpB);
            below.setVisibility(View.GONE);
            if (handle != null && session != null) handle.setVisibility(View.VISIBLE);
        }
        frame.setLayoutParams(fl);
    }

    @Override public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        if (focus) immersive();
    }

    @Override public void surfaceCreated(SurfaceHolder sh) {
        surfaceReady = true;
        if (resumed && session == null) connect(false);
    }

    @Override public void surfaceChanged(SurfaceHolder sh, int format, int width, int height) {}

    /** The codec renders into this Surface: it must be released before this returns. */
    @Override public void surfaceDestroyed(SurfaceHolder sh) {
        surfaceReady = false;
        disconnect();
    }

    private void immersive() {
        Window w = getWindow();
        if (Build.VERSION.SDK_INT >= 30) {
            w.setDecorFitsSystemWindows(false);
            w.getDecorView();                            // creates the decor: no controller before it
            WindowInsetsController c = w.getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            w.getDecorView().setSystemUiVisibility(legacyImmersiveFlags());
        }
    }

    @SuppressWarnings("deprecation")
    private static int legacyImmersiveFlags() {
        return View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN;
    }

    // ------------------------------------------------------------ connection

    private void connect(boolean again) {
        if (!resumed || !surfaceReady) return;
        disconnect();
        final int g = gen;
        showProgress(again ? L.t("Переподключение…") : L.t("Подключение…"));
        final int steps = lower;
        Thread t = new Thread(() -> {
            try {
                boolean ts = Tailnet.isTailnetHost(host) || Tailnet.routedViaTailnet(host);
                Scrcpy.Options o = Scrcpy.Options.forNetwork(ts);
                for (int i = 0; i < steps; i++) {
                    Scrcpy.Options l = o.lower();
                    if (l != null) o = l;
                }
                Scrcpy.Session s = Scrcpy.open(this, host, port, o);
                h.post(() -> {
                    if (g != gen || !resumed || !surfaceReady) { closeQuietly(s); return; }
                    started(s, ts);
                });
            } catch (Exception e) {
                h.post(() -> { if (g == gen) failed(e, again); });
            }
        }, "live-connect");
        t.setDaemon(true);
        t.start();
    }

    private void started(Scrcpy.Session s, boolean ts) {
        session = s;
        tailscale = ts;
        retries = 0;
        lastError = null;
        connectedAt = System.currentTimeMillis();
        lastStalls = 0;
        slowSeconds = 0;
        stallTimes.clear();
        Scrcpy.Header hd = s.header();
        frame.setAspect(hd.width, hd.height);
        dec = new VideoDecoder(s.video(), s.mime(), hd.width, hd.height,
                video.getHolder().getSurface(), new Events(gen, s));
        dec.start();
        status.setVisibility(View.GONE);
        handle.setVisibility(portrait ? View.GONE : View.VISIBLE);   // upright the keys are always under the video
        if (!portrait) showOverlay();                              // sideways: the keys shown from the start
        chipRow.setVisibility(View.VISIBLE);
        badge.setVisibility(ts ? View.VISIBLE : View.GONE);
        updateChip(null);
        h.removeCallbacks(tick);
        h.postDelayed(tick, 1000);
        firstHint();
    }

    private void failed(Exception e, boolean auto) {
        disconnect();
        lastError = e instanceof VideoDecoder.CodecError
                ? L.t("Телефон не может показать видео с этого ТВ.") : Scrcpy.message(e);
        if (auto && !(e instanceof VideoDecoder.CodecError)) scheduleReconnect(false);
        else showError(lastError);
    }

    /** Closes the session and stops the decoder; the Surface is free when this returns. */
    private void disconnect() {
        gen++;
        h.removeCallbacks(reconnect);
        h.removeCallbacks(tick);
        VideoDecoder d = dec;
        Scrcpy.Session s = session;
        dec = null;
        session = null;
        if (s != null) closeQuietly(s);   // unblocks the decoder's read
        if (d != null) d.stop();
        releaseKey();
    }

    private static void closeQuietly(Scrcpy.Session s) {
        Thread t = new Thread(() -> { try { s.close(); } catch (Exception ignored) {} }, "live-close");
        t.setDaemon(true);
        t.start();
    }

    private final Runnable reconnect = () -> connect(true);

    /** Lost the stream: try again with backoff (1, 2, 4, 8 s …), then show the error. */
    private void scheduleReconnect(boolean soon) {
        disconnect();
        if (!resumed) return;
        if (retries >= MAX_RETRIES) {
            showError(lastError != null ? lastError : L.t("Видео с ТВ прервалось."));
            return;
        }
        long delay = soon ? 500 : Math.min(8000, 1000L << retries);
        retries++;
        showProgress(L.t("Переподключение…"));
        h.postDelayed(reconnect, delay);
    }

    /** Decoder events, bound to one connection: events of an older one are ignored. */
    private final class Events implements VideoDecoder.Listener {
        private final int g;
        private final Scrcpy.Session s;

        Events(int g, Scrcpy.Session s) { this.g = g; this.s = s; }

        @Override public void onSize(int w, int hh) { h.post(() -> { if (g == gen) frame.setAspect(w, hh); }); }

        @Override public void onEnded(Exception cause) {
            h.post(() -> {
                if (g != gen) return;
                if (cause instanceof VideoDecoder.CodecError) failed(cause, false);
                else { lastError = L.t("Видео с ТВ прервалось."); scheduleReconnect(false); }
            });
        }

        @Override public void onKeyFrameNeeded() {
            long now = System.currentTimeMillis();
            if (now - lastReset < 1000) return;
            lastReset = now;
            ctl.execute(() -> { try { s.resetVideo(); } catch (Exception ignored) {} });
        }
    }

    // ------------------------------------------------------------ network changes

    private void registerNetwork() {
        if (cm == null || netCb != null) return;
        netKnown = false;
        currentNet = null;
        netCb = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network n) { h.post(() -> netAvailable(n)); }
            @Override public void onLost(Network n) { h.post(() -> { if (n.equals(currentNet)) currentNet = null; }); }
        };
        try { cm.registerDefaultNetworkCallback(netCb); } catch (Exception e) { netCb = null; }
    }

    private void unregisterNetwork() {
        if (cm != null && netCb != null) try { cm.unregisterNetworkCallback(netCb); } catch (Exception ignored) {}
        netCb = null;
    }

    /** The first callback only reports the network we started on; a different one later
     *  (Wi‑Fi ↔ mobile, Tailscale up) means the old sockets are dead or on the wrong path. */
    private void netAvailable(Network n) {
        Network prev = currentNet;
        currentNet = n;
        if (!netKnown) { netKnown = true; return; }
        if (!resumed || n.equals(prev)) return;
        retries = 0;
        scheduleReconnect(true);
    }

    // ------------------------------------------------------------ statistics, adaptive quality

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            VideoDecoder d = dec;
            if (d == null) return;
            VideoDecoder.Stats st = d.sample();
            updateChip(st);
            if (adapt(st)) return;
            h.postDelayed(this, 1000);
        }
    };

    /** Repeated stalls, or a decoder that can't keep up with what arrives: one step lower
     *  (Scrcpy.Options.lower: bitrate first, then size), at most MAX_LOWER times. */
    private boolean adapt(VideoDecoder.Stats st) {
        long now = System.currentTimeMillis();
        int fresh = st.stalls - lastStalls;
        lastStalls = st.stalls;
        if (now - connectedAt < SETTLE_MS) return false;   // the first key frame and warm-up lag
        for (int i = 0; i < fresh; i++) stallTimes.addLast(now);
        if (st.inFps >= 20 && st.fps < st.inFps * 0.6f) {
            if (++slowSeconds >= 4) { stallTimes.addLast(now); slowSeconds = 0; }
        } else slowSeconds = 0;
        while (!stallTimes.isEmpty() && now - stallTimes.peekFirst() > STALL_WINDOW_MS) stallTimes.removeFirst();
        if (stallTimes.size() < STALLS_TO_LOWER || lower >= MAX_LOWER) return false;
        lower++;
        retries = 0;
        disconnect();
        showProgress(L.t("Снижаю качество…"));
        h.postDelayed(reconnect, 300);
        return true;
    }

    private void updateChip(VideoDecoder.Stats st) {
        Scrcpy.Session s = session;
        int rtt = s != null ? s.rttMillis() : -1;
        StringBuilder b = new StringBuilder("●  ");
        b.append(rtt >= 0 ? L.f("%d мс", rtt) : "— " + L.t("мс"));
        if (st != null && st.width > 0) b.append(" · ").append(Math.min(st.width, st.height)).append('p');
        if (st != null) b.append(" · ").append(Math.round(st.fps)).append(" fps");
        for (int i = 0; i < lower; i++) b.append(i == 0 ? " · ↓" : "↓");
        int c = rtt < 0 ? 0x99FFFFFF : rtt < 60 ? GREEN : rtt < 150 ? ORANGE : RED;
        SpannableString sp = new SpannableString(b);
        sp.setSpan(new ForegroundColorSpan(c), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        chip.setText(sp);
    }

    // ------------------------------------------------------------ status line

    private void showProgress(String text) {
        status.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.VISIBLE);
        retry.setVisibility(View.GONE);
        statusText.setText(text);
        chipRow.setVisibility(View.GONE);
    }

    private void showError(String text) {
        Diag.log("live", "error: " + text);
        status.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.GONE);
        retry.setVisibility(View.VISIBLE);
        statusText.setText(text);
        chipRow.setVisibility(View.GONE);
        hideOverlay.run();
    }

    private void firstHint() {
        SharedPreferences p = getSharedPreferences("fling", Context.MODE_PRIVATE);
        if (p.getBoolean("live_hint", false)) return;
        p.edit().putBoolean("live_hint", true).apply();
        hint.setVisibility(View.VISIBLE);
        hint.setAlpha(1f);
        showOverlay();
        h.postDelayed(() -> hint.animate().alpha(0f).setDuration(400)
                .withEndAction(() -> hint.setVisibility(View.GONE)).start(), 5000);
    }

    // ------------------------------------------------------------ keys

    private void sendKey(int action, int code, int repeat) {
        Scrcpy.Session s = session;
        if (s == null) return;
        ctl.execute(() -> { try { s.key(action, code, repeat, 0); } catch (Exception ignored) {} });
    }

    private void press(int code) {
        sendKey(ScrcpyProto.KEY_DOWN, code, 0);
        sendKey(ScrcpyProto.KEY_UP, code, 0);
    }

    private void sendText(String t) {
        Scrcpy.Session s = session;
        if (s == null || t.isEmpty()) return;
        ctl.execute(() -> {
            try {   // in pieces: one message carries at most ScrcpyProto.TEXT_MAX bytes
                for (int i = 0; i < t.length(); ) {
                    int end = Math.min(t.length(), i + 90);
                    if (end < t.length() && Character.isHighSurrogate(t.charAt(end - 1))) end--;
                    s.text(t.substring(i, end));
                    i = end;
                }
            } catch (Exception ignored) {}
        });
    }

    /** One key held down with auto-repeat (a swipe that stays on the glass, a held arrow button). */
    private int heldKey = -1, heldRepeat;
    private final Runnable repeatKey = new Runnable() {
        @Override public void run() {
            if (heldKey < 0) return;
            sendKey(ScrcpyProto.KEY_DOWN, heldKey, ++heldRepeat);
            h.postDelayed(this, REPEAT_EVERY_MS);
        }
    };

    private void holdKey(int code) {
        releaseKey();
        heldKey = code;
        heldRepeat = 0;
        sendKey(ScrcpyProto.KEY_DOWN, code, 0);
        h.postDelayed(repeatKey, REPEAT_FIRST_MS);
    }

    private void releaseKey() {
        h.removeCallbacks(repeatKey);
        if (heldKey >= 0) sendKey(ScrcpyProto.KEY_UP, heldKey, 0);
        heldKey = -1;
    }

    /** The phone's volume keys change the TV volume while LIVE is open. */
    @Override public boolean onKeyDown(int code, KeyEvent e) {
        if (code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN) {
            sendKey(ScrcpyProto.KEY_DOWN, code, e.getRepeatCount());
            return true;
        }
        return super.onKeyDown(code, e);
    }

    @Override public boolean onKeyUp(int code, KeyEvent e) {
        if (code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN) {
            sendKey(ScrcpyProto.KEY_UP, code, 0);
            return true;
        }
        return super.onKeyUp(code, e);
    }

    // ------------------------------------------------------------ gestures on the video

    private float downX, downY;
    private long downAt;
    private boolean longDone, multi;
    private int swipeKey = -1;

    private final Runnable longPress = () -> {
        longDone = true;
        root.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
        press(KeyEvent.KEYCODE_BACK);
    };

    private boolean onVideoTouch(MotionEvent e) {
        if (session == null) return true;
        if (portrait && e.getActionMasked() == MotionEvent.ACTION_DOWN && e.getY() > frame.getBottom()) return false;
        int slop = ui.dp(28);
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = e.getX(); downY = e.getY(); downAt = e.getEventTime();
                longDone = false; multi = false; swipeKey = -1;
                h.postDelayed(longPress, ViewConfiguration.getLongPressTimeout());
                bumpOverlay();
                break;
            case MotionEvent.ACTION_POINTER_DOWN:   // two fingers: not a gesture of ours
                multi = true;
                h.removeCallbacks(longPress);
                if (swipeKey >= 0) { releaseKey(); swipeKey = -1; }
                break;
            case MotionEvent.ACTION_MOVE: {
                if (multi || longDone || swipeKey >= 0) break;
                float dx = e.getX() - downX, dy = e.getY() - downY;
                if (Math.hypot(dx, dy) < slop) break;
                h.removeCallbacks(longPress);
                swipeKey = Math.abs(dx) > Math.abs(dy)
                        ? (dx > 0 ? KeyEvent.KEYCODE_DPAD_RIGHT : KeyEvent.KEYCODE_DPAD_LEFT)
                        : (dy > 0 ? KeyEvent.KEYCODE_DPAD_DOWN : KeyEvent.KEYCODE_DPAD_UP);
                root.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
                holdKey(swipeKey);
                break;
            }
            case MotionEvent.ACTION_UP:
                h.removeCallbacks(longPress);
                if (swipeKey >= 0) { releaseKey(); swipeKey = -1; }
                else if (!longDone && !multi && e.getEventTime() - downAt < ViewConfiguration.getLongPressTimeout()) {
                    root.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                    press(KeyEvent.KEYCODE_DPAD_CENTER);
                }
                break;
            case MotionEvent.ACTION_CANCEL:
                h.removeCallbacks(longPress);
                if (swipeKey >= 0) { releaseKey(); swipeKey = -1; }
                break;
        }
        return true;
    }

    // ------------------------------------------------------------ overlay

    private final Runnable hideOverlay = () -> {
        if (overlay.getVisibility() != View.VISIBLE) return;
        overlay.animate().alpha(0f).setDuration(200).withEndAction(() -> overlay.setVisibility(View.GONE)).start();
        chipRow.animate().alpha(0.55f).setDuration(200).start();
    };

    private void showOverlay() {
        overlay.animate().cancel();
        if (overlay.getVisibility() != View.VISIBLE) { overlay.setAlpha(0f); overlay.setVisibility(View.VISIBLE); }
        overlay.animate().alpha(1f).setDuration(150).start();
        chipRow.animate().alpha(1f).setDuration(150).start();
        bumpOverlay();
    }

    /** Restarts the 3 s auto-hide while the overlay is up. */
    private void bumpOverlay() {
        h.removeCallbacks(hideOverlay);                       // it stays until the remote button hides it
    }

    private void toggleOverlay() {
        if (overlay.getVisibility() == View.VISIBLE && overlay.getAlpha() > 0.5f) {
            h.removeCallbacks(hideOverlay);
            hideOverlay.run();
        } else showOverlay();
    }

    // ------------------------------------------------------------ keyboard (phone IME → TV text)

    private String sent = "";
    private boolean muteWatcher;
    /** Text goes through the Android TV Remote protocol when the phone is paired with this TV:
     *  scrcpy's text injection maps characters to key events and drops what the TV's key map
     *  lacks (Cyrillic, emoji). Created on first use, closed in onDestroy. */
    private Atv atv;

    private Atv pairedRemote() {
        if (atv == null) atv = new Atv(this, new Atv.Listener() {   // events of the remote are not shown here
            @Override public void onPower(Boolean on) {}
            @Override public void onApp(String pkg) {}
            @Override public void onPairing(String state, String message) {}
            @Override public void onError(String message) {}
            @Override public void onVoice(boolean on) {}
        });
        return atv.paired(host) ? atv : null;
    }

    private void showKeyboard() {
        clearField();
        kb.requestFocus();
        InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        if (imm != null) imm.showSoftInput(kb, 0);
        h.removeCallbacks(hideOverlay);
        hideOverlay.run();
    }

    private void hideKeyboard() {
        if (kb == null) return;
        InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        if (imm != null) imm.hideSoftInputFromWindow(kb.getWindowToken(), 0);
        kb.clearFocus();
    }

    private void clearField() {
        muteWatcher = true;
        kb.setText("");
        muteWatcher = false;
        sent = "";
    }

    /** Paired remote: the whole field as typed. Otherwise (scrcpy fallback, ASCII only in practice)
     *  what the IME changed since last time, as TV keys: Del for each removed character, then the
     *  new text. Works with composing keyboards (Gboard rewrites the word as you type). */
    private void fieldChanged(String now) {
        Atv remote = pairedRemote();
        if (remote != null) {   // sets the TV's focused field to exactly this text
            remote.textLive(host, now);
            sent = now;
            return;
        }
        int p = 0, max = Math.min(now.length(), sent.length());
        while (p < max && now.charAt(p) == sent.charAt(p)) p++;
        if (p > 0 && Character.isHighSurrogate(now.charAt(p - 1))) p--;
        int del = sent.codePointCount(p, sent.length());
        for (int i = 0; i < del; i++) press(KeyEvent.KEYCODE_DEL);
        sendText(now.substring(p));
        sent = now;
    }

    // ------------------------------------------------------------ views

    private View build() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        root.setOnTouchListener((v, e) -> onVideoTouch(e));
        root.setContentDescription(L.t("Касание — OK, свайп — стрелки, удержание — «Назад»"));

        frame = new AspectFrame(this);
        video = new SurfaceView(this);
        video.getHolder().addCallback(this);
        frame.addView(video, new FrameLayout.LayoutParams(-1, -1));
        root.addView(frame, new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER));

        // hidden field that receives the phone keyboard
        kb = new EditText(this);
        kb.setAlpha(0f);
        kb.setBackground(null);
        kb.setCursorVisible(false);
        kb.setSingleLine(true);
        kb.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        kb.setImeOptions(EditorInfo.IME_ACTION_GO | EditorInfo.IME_FLAG_NO_EXTRACT_UI | EditorInfo.IME_FLAG_NO_FULLSCREEN);
        kb.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void afterTextChanged(Editable s) { if (!muteWatcher) fieldChanged(s.toString()); }
        });
        kb.setOnEditorActionListener((v, id, ev) -> {
            press(KeyEvent.KEYCODE_ENTER);
            clearField();
            hideKeyboard();
            return true;
        });
        kb.setOnKeyListener((v, code, ev) -> {
            if (ev.getAction() != KeyEvent.ACTION_DOWN) return false;
            if (code == KeyEvent.KEYCODE_DEL && kb.length() == 0) { press(KeyEvent.KEYCODE_DEL); return true; }
            if (code == KeyEvent.KEYCODE_ENTER) { press(KeyEvent.KEYCODE_ENTER); clearField(); return true; }
            return false;
        });
        root.addView(kb, new FrameLayout.LayoutParams(1, 1, Gravity.TOP | Gravity.START));

        // mini remote (non-clickable containers: touches between buttons reach the video)
        overlay = new FrameLayout(this);
        overlay.setVisibility(View.GONE);
        root.addView(overlay, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout left = leftKeys = panel(true);
        left.addView(button(null, R.drawable.ic_n_close, L.t("Закрыть"), v -> finish()));
        left.addView(button("back", 0, L.t("Назад"), v -> press(KeyEvent.KEYCODE_BACK)));
        left.addView(button("home", 0, L.t("Домой"), v -> press(KeyEvent.KEYCODE_HOME)));
        dpadView = dpad();

        LinearLayout bottom = bottomKeys = panel(false);
        bottom.addView(holdButton("spk_minus", 0, L.t("Тише"), KeyEvent.KEYCODE_VOLUME_DOWN));
        bottom.addView(button(null, R.drawable.ic_live_playpause, L.t("Пауза / воспроизведение"),
                v -> press(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE)));
        bottom.addView(holdButton("spk_plus", 0, L.t("Громче"), KeyEvent.KEYCODE_VOLUME_UP));
        bottom.addView(button(null, R.drawable.ic_live_keyboard, L.t("Клавиатура"), v -> showKeyboard()));
        // upright: the keys live in a column under the video (filled by arrange())
        below = new LinearLayout(this);
        below.setOrientation(LinearLayout.VERTICAL);
        below.setGravity(Gravity.CENTER_HORIZONTAL);
        root.addView(below, new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM));

        // chip: latency · size · fps, and the Tailscale badge
        chipRow = new LinearLayout(this);
        chipRow.setOrientation(LinearLayout.HORIZONTAL);
        chipRow.setGravity(Gravity.CENTER_VERTICAL);
        chipRow.setVisibility(View.GONE);
        badge = pill("Tailscale");
        badge.setVisibility(View.GONE);
        chipRow.addView(badge);
        chip = pill("");
        LinearLayout.LayoutParams lpc = new LinearLayout.LayoutParams(-2, -2);
        lpc.leftMargin = ui.dp(6);
        chipRow.addView(chip, lpc);
        FrameLayout.LayoutParams lpChip = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        lpChip.topMargin = ui.dp(12);
        lpChip.rightMargin = ui.dp(12);
        root.addView(chipRow, lpChip);

        // the handle that opens the mini remote
        handle = button(null, R.drawable.ic_live_remote, L.t("Пульт"), v -> toggleOverlay());
        handle.setBackground(circle(PANEL));
        handle.setVisibility(View.GONE);
        FrameLayout.LayoutParams lpH = new FrameLayout.LayoutParams(ui.dp(48), ui.dp(48), Gravity.BOTTOM | Gravity.END);
        lpH.rightMargin = ui.dp(14);
        lpH.bottomMargin = ui.dp(14);
        root.addView(handle, lpH);

        // first-start hint
        hint = pill(L.t("Касание — OK, свайп — стрелки, удержание — «Назад»"));
        hint.setVisibility(View.GONE);
        FrameLayout.LayoutParams lpHint = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        lpHint.topMargin = ui.dp(52);
        root.addView(hint, lpHint);

        // progress / error: icon + one short line (+ Retry)
        status = new LinearLayout(this);
        status.setOrientation(LinearLayout.VERTICAL);
        status.setGravity(Gravity.CENTER_HORIZONTAL);
        status.setBackground(rounded(PANEL, 18));
        status.setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(14));
        LinearLayout line = new LinearLayout(this);
        line.setOrientation(LinearLayout.HORIZONTAL);
        line.setGravity(Gravity.CENTER_VERTICAL);
        spinner = new ProgressBar(this, null, android.R.attr.progressBarStyleSmall);
        spinner.setIndeterminateTintList(ColorStateList.valueOf(WHITE));
        line.addView(spinner, new LinearLayout.LayoutParams(ui.dp(20), ui.dp(20)));
        statusText = ui.text("", 14, WHITE, false);
        statusText.setMaxWidth(ui.dp(420));
        statusText.setMaxLines(3);
        LinearLayout.LayoutParams lpt = new LinearLayout.LayoutParams(-2, -2);
        lpt.leftMargin = ui.dp(10);
        line.addView(statusText, lpt);
        status.addView(line);
        retry = ui.text(L.t("Повторить"), 15, WHITE, true);
        retry.setGravity(Gravity.CENTER);
        retry.setMinHeight(ui.dp(44));
        retry.setPadding(ui.dp(22), 0, ui.dp(22), 0);
        retry.setBackground(new RippleDrawable(ColorStateList.valueOf(0x55FFFFFF), rounded(ui.accent, 22), rounded(WHITE, 22)));
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
            retries = 0;
            connect(false);
        });
        LinearLayout.LayoutParams lpr = new LinearLayout.LayoutParams(-2, -2);
        lpr.topMargin = ui.dp(12);
        status.addView(retry, lpr);
        status.setClickable(true);   // touches on the card are not video gestures
        root.addView(status, new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER));
        return root;
    }

    private FrameLayout.LayoutParams dpadLp() {
        FrameLayout.LayoutParams p = new FrameLayout.LayoutParams(ui.dp(168), ui.dp(168), Gravity.END | Gravity.CENTER_VERTICAL);
        p.rightMargin = ui.dp(40);                                   // clear of the rounded corner / gesture edge
        return p;
    }

    /** Arrows around OK on a dark disc; the arrows repeat while held. */
    private View dpad() {
        FrameLayout d = new FrameLayout(this);
        d.setBackground(circle(PANEL));
        int s = ui.dp(52);
        d.addView(holdButton("up", 0, L.t("Вверх"), KeyEvent.KEYCODE_DPAD_UP), new FrameLayout.LayoutParams(s, s, Gravity.TOP | Gravity.CENTER_HORIZONTAL));
        d.addView(holdButton("down", 0, L.t("Вниз"), KeyEvent.KEYCODE_DPAD_DOWN), new FrameLayout.LayoutParams(s, s, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL));
        d.addView(holdButton("left", 0, L.t("Влево"), KeyEvent.KEYCODE_DPAD_LEFT), new FrameLayout.LayoutParams(s, s, Gravity.START | Gravity.CENTER_VERTICAL));
        d.addView(holdButton("right", 0, L.t("Вправо"), KeyEvent.KEYCODE_DPAD_RIGHT), new FrameLayout.LayoutParams(s, s, Gravity.END | Gravity.CENTER_VERTICAL));
        ImageView ok = button(null, 0, "OK", v -> press(KeyEvent.KEYCODE_DPAD_CENTER));
        GradientDrawable ring = new GradientDrawable();
        ring.setShape(GradientDrawable.OVAL);
        ring.setStroke(ui.dp(2), WHITE);
        ring.setSize(ui.dp(26), ui.dp(26));
        ok.setImageDrawable(ring);
        ok.setPadding(ui.dp(13), ui.dp(13), ui.dp(13), ui.dp(13));
        d.addView(ok, new FrameLayout.LayoutParams(s, s, Gravity.CENTER));
        return d;
    }

    private LinearLayout panel(boolean vertical) {
        LinearLayout p = new LinearLayout(this);
        p.setOrientation(vertical ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        p.setGravity(Gravity.CENTER);
        p.setBackground(rounded(PANEL, 26));
        p.setPadding(ui.dp(2), ui.dp(2), ui.dp(2), ui.dp(2));
        return p;
    }

    /** A 48 dp icon button: a Ui glyph by name, or a drawable resource. */
    private ImageView button(String glyph, int res, String desc, View.OnClickListener on) {
        ImageView b = new ImageView(this);
        b.setLayoutParams(new LinearLayout.LayoutParams(ui.dp(48), ui.dp(48)));
        b.setScaleType(ImageView.ScaleType.FIT_CENTER);
        int pad = ui.dp(12);
        b.setPadding(pad, pad, pad, pad);
        if (glyph != null) b.setImageDrawable(new Ui.Glyph(glyph, WHITE, 2.0f));
        else if (res != 0) b.setImageResource(res);
        b.setContentDescription(desc);
        b.setBackground(new RippleDrawable(ColorStateList.valueOf(0x40FFFFFF), null, circle(WHITE)));
        b.setClickable(true);
        b.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
            bumpOverlay();
            on.onClick(v);
        });
        return b;
    }

    /** A button that holds its TV key while pressed (auto-repeat), like a real remote. */
    private ImageView holdButton(String glyph, int res, String desc, int code) {
        ImageView b = button(glyph, res, desc, v -> {});
        b.setOnClickListener(v -> press(code));   // accessibility (TalkBack double tap) and keyboards
        b.setOnTouchListener((v, e) -> {
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    v.setPressed(true);
                    v.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                    h.removeCallbacks(hideOverlay);
                    holdKey(code);
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    v.setPressed(false);
                    releaseKey();
                    bumpOverlay();
                    return true;
            }
            return true;
        });
        return b;
    }

    private TextView pill(String s) {
        TextView t = ui.text(s, 12, WHITE, true);
        t.setBackground(rounded(PANEL, 14));
        t.setPadding(ui.dp(10), ui.dp(6), ui.dp(10), ui.dp(6));
        t.setGravity(Gravity.CENTER_VERTICAL);
        return t;
    }

    private GradientDrawable rounded(int color, float r) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(ui.dp(r));
        return g;
    }

    private static Drawable circle(int color) {
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(color);
        return g;
    }

    /** Keeps the TV's aspect ratio inside whatever space it gets; black bars around it. */
    static final class AspectFrame extends FrameLayout {
        private int vw = 16, vh = 9;

        AspectFrame(Context c) { super(c); }

        void setAspect(int w, int h) {
            if (w <= 0 || h <= 0 || (w == vw && h == vh)) return;
            vw = w;
            vh = h;
            requestLayout();
        }

        @Override protected void onMeasure(int wSpec, int hSpec) {
            int W = MeasureSpec.getSize(wSpec), H = MeasureSpec.getSize(hSpec);
            int w = W, hh = (int) ((long) W * vh / vw);
            if (hh > H) { hh = H; w = (int) ((long) H * vw / vh); }
            super.onMeasure(MeasureSpec.makeMeasureSpec(w, MeasureSpec.EXACTLY),
                    MeasureSpec.makeMeasureSpec(hh, MeasureSpec.EXACTLY));
        }
    }
}
