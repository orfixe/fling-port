// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.Collections;

/** Remote access over Tailscale, without any Tailscale code: Fling only checks whether the
 *  official app (com.tailscale.ipn) is installed and whether this phone has a tailnet
 *  address, and lets the user add a TV by its Tailscale IP or MagicDNS name. The remote
 *  (6466/6467) and ADB connect by address, so they work away from home; casting works only
 *  if the TV's Cast port answers on the tailnet. */
final class Tailnet {
    private Tailnet() {}

    static final String PKG = "com.tailscale.ipn";

    /** The official Tailscale app is on this phone (needs the <queries> entry in the manifest). */
    static boolean installed(Context c) {
        try {
            c.getPackageManager().getPackageInfo(PKG, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    /** Tailscale is connected: one of this phone's interfaces has a 100.64.0.0/10 address. */
    static boolean connected() {
        try {
            for (NetworkInterface n : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!n.isUp()) continue;
                for (InetAddress a : Collections.list(n.getInetAddresses())) {
                    if (a instanceof Inet4Address && isTailnetIp(a.getHostAddress())) return true;
                }
            }
        } catch (Exception ignored) {}
        return false;
    }

    /** 100.64.0.0/10: the addresses Tailscale gives its devices. */
    static boolean isTailnetIp(String ip) {
        if (ip == null) return false;
        String[] p = ip.split("\\.", -1);
        if (p.length != 4) return false;
        try {
            int a = Integer.parseInt(p[0]), b = Integer.parseInt(p[1]);
            return a == 100 && b >= 64 && b <= 127;
        } catch (NumberFormatException e) {
            return false;
        }
    }

    /** A host name (MagicDNS: "tv" or "tv.tailnet.ts.net"), not an IP address. */
    static boolean isHostName(String s) {
        return s != null && !s.isEmpty() && !Character.isDigit(s.charAt(0))
                && s.matches("(?i)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\\.?");
    }

    /** The IPv4 address for a MagicDNS name (the Tailscale app answers these while it's on). Blocking. */
    static String resolve(String name) throws java.net.UnknownHostException {
        for (InetAddress a : InetAddress.getAllByName(name.endsWith(".") ? name.substring(0, name.length() - 1) : name)) {
            if (a instanceof Inet4Address) return a.getHostAddress();
        }
        throw new java.net.UnknownHostException(name);
    }

    /** Opens the Tailscale app (to switch it on), or returns false. */
    static boolean open(Context c) {
        Intent i = c.getPackageManager().getLaunchIntentForPackage(PKG);
        if (i == null) return false;
        c.startActivity(i);
        return true;
    }
}
