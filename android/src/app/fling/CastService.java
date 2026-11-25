// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadata;
import android.media.VolumeProvider;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.widget.Toast;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** While something plays on the TV: a media notification (also on the lock screen) with
 *  pause, ±30 s, next/previous and stop; the phone's volume keys keep controlling the TV
 *  with the screen off; and phone files being cast stay served (MediaServer).
 *  Stops by itself once nothing plays for a while. */
public class CastService extends Service implements Cast.Listener {
    static final String ACTION_SHOW = "app.fling.SHOW", ACTION_LOCAL = "app.fling.LOCAL",
            ACTION_TOGGLE = "app.fling.TOGGLE", ACTION_BACK = "app.fling.BACK", ACTION_FWD = "app.fling.FWD",
            ACTION_PREV = "app.fling.PREV", ACTION_NEXT = "app.fling.NEXT",
            ACTION_STOP = "app.fling.STOP", ACTION_CLOSE = "app.fling.CLOSE", ACTION_DISMISS = "app.fling.DISMISS";
    static final String EXTRA_HOST = "host", EXTRA_URIS = "uris", EXTRA_TYPE = "type";
    private static final String CHANNEL = "playing";
    private static final int ID = 1;
    private static final long GRACE = 30000, IDLE_STOP = 20000;

    /** URLs of the phone files in the current queue, in order (read by MainActivity for prev/next). */
    static volatile List<String> queue = Collections.emptyList();
    static volatile boolean running;
    /** What was playing when the user closed the notification: MainActivity does not bring it back for that. */
    static volatile String dismissed;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService bg = Executors.newSingleThreadExecutor();
    private SharedPreferences prefs;
    private Cast cast;
    private Atv atv;
    private String host;
    private MediaSession session;
    private VolumeProvider volume;
    private boolean relativeVolume;
    private Cast.Status st;
    private long graceUntil, idleSince;
    private String artUrl;
    private Bitmap art;

    static int queueIndex(String url) { return url == null ? -1 : queue.indexOf(url); }

    /** Starts (or refreshes) the service; quietly does nothing when Android does not allow it now. */
    static void show(Context c, String host) {
        if (host == null) return;
        try {
            c.startForegroundService(new Intent(c, CastService.class).setAction(ACTION_SHOW).putExtra(EXTRA_HOST, host));
        } catch (Exception ignored) {}   // background start not allowed: the app was not on screen
    }

    @Override public void onCreate() {
        super.onCreate();
        L.init(this);
        running = true;
        prefs = getSharedPreferences("fling", MODE_PRIVATE);
        cast = new Cast(this, this);
        atv = new Atv(this, new Atv.Listener() {
            public void onPower(Boolean on) {}
            public void onApp(String pkg) {}
            public void onPairing(String state, String message) {}
            public void onError(String message) {}
            public void onVoice(boolean on) {}
        });
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel ch = new NotificationChannel(CHANNEL, L.t("Сейчас на ТВ"), NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);
        session = new MediaSession(this, "Fling");
        session.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { toggle(); }
            @Override public void onPause() { toggle(); }
            @Override public void onSkipToNext() { if (inQueue()) cast.queueJump(1); else seek(MainActivity.SEEK); }
            @Override public void onSkipToPrevious() { if (inQueue()) cast.queueJump(-1); else seek(-MainActivity.SEEK); }
            @Override public void onFastForward() { seek(MainActivity.SEEK); }
            @Override public void onRewind() { seek(-MainActivity.SEEK); }
            @Override public void onSeekTo(long ms) { cast.seekTo(ms / 1000.0); }
            @Override public void onStop() { cast.stop(); }
            @Override public void onCustomAction(String a, android.os.Bundle extras) { handle(a); }
        });
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        session.setSessionActivity(PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE));
    }

    @Override public int onStartCommand(Intent i, int flags, int startId) {
        String h = i == null ? null : i.getStringExtra(EXTRA_HOST);
        if (h == null) h = host != null ? host : prefs.getString("host", null);
        // Android requires startForeground() promptly after startForegroundService()
        startForeground();
        if (h == null) { stopSelf(); return START_NOT_STICKY; }
        if (!h.equals(host)) {
            if (host != null) cast.disconnect();
            host = h;
            st = null;
            cast.connect(h);
            setupVolume();
        }
        String a = i == null ? ACTION_SHOW : i.getAction();
        if (ACTION_LOCAL.equals(a)) castLocal(i);
        else if (ACTION_SHOW.equals(a)) { graceUntil = Math.max(graceUntil, SystemClock.elapsedRealtime() + GRACE); cast.refresh(); }
        else if (a != null) handle(a);
        main.removeCallbacks(watchdog);
        main.postDelayed(watchdog, 5000);
        return START_NOT_STICKY;
    }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public void onDestroy() {
        running = false;
        main.removeCallbacksAndMessages(null);
        queue = Collections.emptyList();
        MediaServer.stop();
        session.release();
        cast.disconnect();
        atv.disconnect();
        bg.shutdownNow();
        super.onDestroy();
    }

    // ------------------------------------------------------------ phone files

    private void castLocal(Intent i) {
        final ArrayList<Uri> uris = i.getParcelableArrayListExtra(EXTRA_URIS);
        final String type = i.getStringExtra(EXTRA_TYPE);
        final String h = host;
        if (uris == null || uris.isEmpty()) return;
        graceUntil = SystemClock.elapsedRealtime() + 60000;   // reading big files and starting the player take a while
        dismissed = null;
        bg.execute(() -> {
            List<Cast.Media> list = new ArrayList<>();
            List<String> urls = new ArrayList<>();
            String err = null;
            for (Uri u : uris) {
                try {
                    MediaServer.Item it = MediaServer.publish(this, u, type, h);
                    Cast.Media m = new Cast.Media(it.url, it.title, it.mime);
                    m.subtitle = it.artist;
                    m.image = it.thumbUrl;
                    list.add(m);
                    urls.add(it.url);
                } catch (Exception e) { err = e.getMessage(); }
            }
            final String error = err;
            main.post(() -> {
                if (list.isEmpty()) { toast(error != null ? error : L.t("Не удалось открыть файл.")); graceUntil = 0; stopIfIdle(true); return; }
                if (error != null) toast(L.f("Пропущено файлов: %d — %s", uris.size() - list.size(), error));
                queue = Collections.unmodifiableList(urls);
                cast.load(list);
                graceUntil = SystemClock.elapsedRealtime() + 60000;
            });
        });
    }

    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_LONG).show(); }

    // ------------------------------------------------------------ controls

    private boolean paired() { return atv.paired(host); }
    private boolean inQueue() { return st != null && queue.size() > 1 && queueIndex(st.contentId) >= 0; }

    private void handle(String a) {
        switch (a) {
            case ACTION_TOGGLE: toggle(); break;
            case ACTION_BACK: seek(-MainActivity.SEEK); break;
            case ACTION_FWD: seek(MainActivity.SEEK); break;
            case ACTION_PREV: cast.queueJump(-1); break;
            case ACTION_NEXT: cast.queueJump(1); break;
            case ACTION_STOP: cast.stop(); queue = Collections.emptyList(); stopSelf(); break;
            case ACTION_DISMISS:   // swiped away: a phone file keeps playing, so its controls come back
                if (st != null && queueIndex(st.contentId) >= 0) { post(); break; }
                dismissed = st == null ? "" : String.valueOf(st.contentId);
                stopSelf();
                break;
            case ACTION_CLOSE:
                if (st != null && queueIndex(st.contentId) >= 0) { handle(ACTION_STOP); break; }   // closing would cut the file off mid-play
                dismissed = st == null ? "" : String.valueOf(st.contentId);
                stopSelf();
                break;
        }
    }

    private void toggle() {
        if (st == null) return;
        // same rule as the app: the remote key works for every TV app, Cast only for Cast sessions
        if (paired() && (st.stuck() || queueIndex(st.contentId) < 0)) { atv.key(host, Atv.PLAY_PAUSE); return; }
        if (st.playing()) cast.pause(); else cast.play();
        onStatus(cast.status());
    }

    private void seek(int s) {
        if (st != null && st.stuck()) {
            if (paired()) for (int i = 0; i < 3; i++) atv.key(host, s > 0 ? Atv.FFWD : Atv.REWIND);
            return;
        }
        cast.seekBy(s);
    }

    // ------------------------------------------------------------ status → notification

    @Override public void onStatus(Cast.Status s) {
        final Cast.Status c = s == null ? null : s.copy();
        main.post(() -> { st = c; update(); });
    }

    @Override public void onError(String message) {}

    private final Runnable watchdog = new Runnable() {
        @Override public void run() {
            cast.refresh();
            stopIfIdle(false);
            main.postDelayed(this, 5000);
        }
    };

    private void stopIfIdle(boolean now) {
        long t = SystemClock.elapsedRealtime();
        boolean busy = st != null && (st.hasMedia() || (queueIndex(st.contentId) >= 0 && Cast.DEFAULT_RECEIVER.equals(st.appId)));
        if (busy || t < graceUntil) { idleSince = 0; return; }
        if (idleSince == 0) idleSince = t;
        if (now || t - idleSince > IDLE_STOP) stopSelf();
    }

    private void setupVolume() {
        relativeVolume = paired();
        // paired: TV volume keys (also drive a soundbar over HDMI-CEC); otherwise Cast volume, 20 steps of 5 %
        volume = relativeVolume
                ? new VolumeProvider(VolumeProvider.VOLUME_CONTROL_RELATIVE, 100, 50) {
                    @Override public void onAdjustVolume(int d) {
                        if (d != 0) atv.key(host, d > 0 ? Atv.VOL_UP : Atv.VOL_DOWN);
                    }
                }
                : new VolumeProvider(VolumeProvider.VOLUME_CONTROL_ABSOLUTE, 20, 10) {
                    @Override public void onSetVolumeTo(int v) {
                        v = Math.max(0, Math.min(20, v));
                        setCurrentVolume(v);
                        cast.setVolume(v * 5);
                    }
                    @Override public void onAdjustVolume(int d) { if (d != 0) onSetVolumeTo(getCurrentVolume() + d); }
                };
        session.setPlaybackToRemote(volume);
    }

    private void update() {
        stopIfIdle(false);
        if (st == null || !st.hasMedia()) { if (st != null) session.setActive(false); post(); return; }
        if (!relativeVolume && st.volume >= 0) volume.setCurrentVolume(Math.round(st.volume / 5f));
        boolean q = inQueue(), stuck = st.stuck();
        long actions = PlaybackState.ACTION_PLAY_PAUSE | PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_STOP
                | PlaybackState.ACTION_SKIP_TO_NEXT | PlaybackState.ACTION_SKIP_TO_PREVIOUS
                | PlaybackState.ACTION_FAST_FORWARD | PlaybackState.ACTION_REWIND;
        if (!stuck && st.duration > 0) actions |= PlaybackState.ACTION_SEEK_TO;
        int state = st.playing() ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED;
        PlaybackState.Builder pb = new PlaybackState.Builder()
                .setActions(actions)
                .setState(state, (long) (st.elapsedNow() * 1000), st.playing() && !stuck ? 1f : 0f, SystemClock.elapsedRealtime());
        if (q) {   // ±30 s next to previous/next in the system media player
            pb.addCustomAction(new PlaybackState.CustomAction.Builder(ACTION_BACK, L.t("Назад на 30 с"), R.drawable.ic_n_back30).build());
            pb.addCustomAction(new PlaybackState.CustomAction.Builder(ACTION_FWD, L.t("Вперёд на 30 с"), R.drawable.ic_n_fwd30).build());
        }
        pb.addCustomAction(new PlaybackState.CustomAction.Builder(ACTION_CLOSE, L.t("Закрыть"), R.drawable.ic_n_close).build());
        session.setPlaybackState(pb.build());
        String title = stuck ? L.t("Воспроизводится") : st.title != null ? st.title : L.t("Воспроизводится");
        String sub = st.subtitle != null ? st.subtitle : st.appName;
        MediaMetadata.Builder mb = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, title)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, sub)
                .putLong(MediaMetadata.METADATA_KEY_DURATION, stuck ? 0 : (long) (st.duration * 1000));
        if (art != null) mb.putBitmap(MediaMetadata.METADATA_KEY_ART, art);
        session.setMetadata(mb.build());
        session.setActive(true);
        loadArt(stuck ? null : st.imageUrl);
        post();
    }

    private void loadArt(String url) {
        if (url == null ? artUrl == null : url.equals(artUrl)) return;
        artUrl = url;
        art = null;
        if (url == null) return;
        bg.execute(() -> {
            Bitmap b = null;
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(5000);
                c.setReadTimeout(8000);
                try (InputStream in = c.getInputStream()) {
                    byte[] d = Cast.readAll(in);
                    BitmapFactory.Options o = new BitmapFactory.Options();
                    o.inJustDecodeBounds = true;
                    BitmapFactory.decodeByteArray(d, 0, d.length, o);
                    int sample = 1;
                    while (Math.max(o.outWidth, o.outHeight) / (sample * 2) >= 512) sample *= 2;
                    o = new BitmapFactory.Options();
                    o.inSampleSize = sample;
                    b = BitmapFactory.decodeByteArray(d, 0, d.length, o);
                } finally { c.disconnect(); }
            } catch (Exception ignored) {}
            final Bitmap bm = b;
            main.post(() -> { if (url.equals(artUrl) && bm != null) { art = bm; update(); } });
        });
    }

    private void startForeground() {
        Notification n = build();
        if (Build.VERSION.SDK_INT >= 29) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        else startForeground(ID, n);
    }

    private void post() {
        if (!running) return;
        try { getSystemService(NotificationManager.class).notify(ID, build()); } catch (Exception ignored) {}
    }

    private PendingIntent pi(String action, int code) {
        // a foreground-service intent: works even if the notification outlived the service
        return PendingIntent.getForegroundService(this, code, new Intent(this, CastService.class).setAction(action),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private Notification.Action action(int icon, String title, String a, int code) {
        return new Notification.Action.Builder(android.graphics.drawable.Icon.createWithResource(this, icon), title, pi(a, code)).build();
    }

    private Notification build() {
        boolean media = st != null && (st.hasMedia() || queueIndex(st.contentId) >= 0);   // a photo may report IDLE
        boolean q = media && inQueue();
        String title = !media ? L.t("Связываюсь с ТВ…") : st.stuck() || st.title == null ? L.t("Воспроизводится") : st.title;
        String tv = prefs.getString("name", null);
        String sub = media && st.subtitle != null ? st.subtitle : tv != null ? L.f("На «%s»", tv) : L.t("На телевизоре");
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        Notification.Builder b = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_tile)
                .setContentTitle(title)
                .setContentText(sub)
                .setContentIntent(PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE))
                .setDeleteIntent(pi(ACTION_DISMISS, 9))
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setCategory(Notification.CATEGORY_TRANSPORT);
        if (art != null) b.setLargeIcon(art);
        boolean playing = media && st.playing();
        if (q) b.addAction(action(R.drawable.ic_n_prev, L.t("Предыдущий"), ACTION_PREV, 1));
        else b.addAction(action(R.drawable.ic_n_back30, L.t("Назад на 30 с"), ACTION_BACK, 2));
        b.addAction(action(playing ? R.drawable.ic_n_pause : R.drawable.ic_n_play, playing ? L.t("Пауза") : L.t("Воспроизвести"), ACTION_TOGGLE, 3));
        if (q) b.addAction(action(R.drawable.ic_n_next, L.t("Следующий"), ACTION_NEXT, 4));
        else b.addAction(action(R.drawable.ic_n_fwd30, L.t("Вперёд на 30 с"), ACTION_FWD, 5));
        b.addAction(action(R.drawable.ic_n_stop, L.t("Остановить трансляцию"), ACTION_STOP, 6));
        b.setStyle(new Notification.MediaStyle().setMediaSession(session.getSessionToken()).setShowActionsInCompactView(0, 1, 2));
        return b.build();
    }
}
