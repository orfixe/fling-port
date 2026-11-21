package app.fling;

import android.app.PendingIntent;
import android.content.Intent;
import android.os.Build;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

/** Quick Settings tile: play/pause on the saved TV over the Android TV Remote protocol. */
public class PauseTile extends TileService {
    private Atv atv;

    private String host() { return getSharedPreferences("fling", MODE_PRIVATE).getString("host", null); }

    private Atv atv() {
        if (atv == null) atv = new Atv(this, new Atv.Listener() {
            public void onPower(Boolean on) {}
            public void onApp(String pkg) {}
            public void onPairing(String state, String message) {}
            public void onError(String message) {}
            public void onVoice(boolean on) {}
        });
        return atv;
    }

    @Override public void onStartListening() {
        L.init(this);
        Tile t = getQsTile();
        if (t == null) return;
        boolean ready = atv().paired(host());
        t.setLabel(L.t("Пауза на ТВ"));
        if (Build.VERSION.SDK_INT >= 29) t.setSubtitle(ready ? getSharedPreferences("fling", MODE_PRIVATE).getString("name", L.t("ТВ")) : L.t("Откройте Fling"));
        t.setState(ready ? Tile.STATE_ACTIVE : Tile.STATE_INACTIVE);
        t.updateTile();
    }

    @Override public void onClick() {
        L.init(this);
        String h = host();
        if (atv().paired(h)) {
            atv().key(h, Atv.PLAY_PAUSE);
            return;
        }
        Intent i = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(PendingIntent.getActivity(this, 0, i, PendingIntent.FLAG_IMMUTABLE));
        } else {
            startActivityAndCollapse(i);
        }
    }

    @Override public void onDestroy() {
        super.onDestroy();
        if (atv != null) atv.disconnect();
    }
}
