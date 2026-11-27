// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;

/** Google Play build: Play updates the app, so there is no self-update here (no download, no
 *  package installer, no REQUEST_INSTALL_PACKAGES). Same shape as the GitHub Updater, so the
 *  screens compile unchanged; they hide "Check for updates" when BuildConfig.SELF_UPDATE is false. */
final class Updater {
    Updater(Activity a, Ui ui, SharedPreferences prefs) {}

    static String installedVersion(Context ctx) {
        return BuildConfig.VERSION_NAME;
    }

    void checkDaily() {}

    void resumed() {}

    static void background(Context ctx) {}

    void check(boolean manual) {}
}
