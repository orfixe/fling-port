// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling.tv;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import android.util.Base64;

import java.io.ByteArrayOutputStream;

/** Runs on the TV (app_process, shell user): for each package, its label and icon as PNG. */
public final class Icons {
    public static void main(String[] args) throws Exception {
        android.os.Looper.prepareMainLooper();
        Object at = Class.forName("android.app.ActivityThread").getMethod("systemMain").invoke(null);
        Context ctx = (Context) at.getClass().getMethod("getSystemContext").invoke(at);
        PackageManager pm = ctx.getPackageManager();
        int size = Integer.parseInt(args[0]);
        for (int i = 1; i < args.length; i++) {
            String pkg = args[i];
            if (pkg.indexOf('/') > 0) {                     // a component (the screensaver): its own name
                try {
                    android.content.ComponentName cn = android.content.ComponentName.unflattenFromString(pkg);
                    String label = String.valueOf(pm.getServiceInfo(cn, 0).loadLabel(pm)).replace('\t', ' ').replace('\n', ' ');
                    System.out.println(pkg + "\t" + label + "\t");
                } catch (Throwable t) {
                    System.out.println(pkg + "\t\t");
                }
                continue;
            }
            try {
                ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
                String label = String.valueOf(pm.getApplicationLabel(ai)).replace('\t', ' ').replace('\n', ' ');
                Drawable d = pm.getApplicationIcon(ai);
                Bitmap b = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
                d.setBounds(0, 0, size, size);
                d.draw(new Canvas(b));
                ByteArrayOutputStream o = new ByteArrayOutputStream();
                b.compress(Bitmap.CompressFormat.PNG, 100, o);
                System.out.println(pkg + "\t" + label + "\t" + Base64.encodeToString(o.toByteArray(), Base64.NO_WRAP));
            } catch (Throwable t) {
                System.out.println(pkg + "\t\t");
            }
        }
        System.out.flush();
        System.exit(0);
    }
}
