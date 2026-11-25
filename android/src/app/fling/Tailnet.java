// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;

import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.Locale;

/** Remote access over Tailscale. Fling embeds no VPN code: the official Tailscale app
 *  (com.tailscale.ipn) puts the phone on the tailnet, and Fling talks to the TV by its
 *  tailnet address (100.x.y.z or a MagicDNS name) like to any other address. */
final class Tailnet {
    private Tailnet() {}

    static final String PKG = "com.tailscale.ipn";
    private static final String STORE = "market://details?id=" + PKG;
    private static final String WEB = "https://tailscale.com/download/android";

    static boolean installed(Context c) {
        try { c.getPackageManager().getPackageInfo(PKG, 0); return true; }   // needs <queries> on Android 11+
        catch (Exception e) { return false; }
    }

    /** Any VPN is up (Tailscale is one). Android doesn't say whose VPN it is. */
    @SuppressWarnings("deprecation")
    static boolean vpnActive(Context c) {
        ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return false;
        try {
            for (Network n : cm.getAllNetworks()) {
                NetworkCapabilities nc = cm.getNetworkCapabilities(n);
                if (nc != null && nc.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) return true;
            }
        } catch (Exception ignored) {}
        return false;
    }

    /** 100.64.0.0/10 (Tailscale's CGNAT range) or a MagicDNS name. Pure string check, no lookup. */
    static boolean isTailnetHost(String h) {
        if (h == null) return false;
        String s = h.toLowerCase(Locale.ROOT);
        if (s.endsWith(".ts.net")) return true;
        String[] p = s.split("\\.", -1);
        if (p.length != 4) return false;
        try {
            int a = Integer.parseInt(p[0]), b = Integer.parseInt(p[1]);
            return a == 100 && b >= 64 && b <= 127;
        } catch (NumberFormatException e) { return false; }
    }

    /** True when packets to the host leave through the tailnet (the phone's source address is a
     *  100.64/10 one). Also true for a home TV reached through a subnet router. Blocking. */
    static boolean routedViaTailnet(String host) {
        try (DatagramSocket d = new DatagramSocket()) {   // UDP "connect" sends nothing; it only picks the route
            d.connect(InetAddress.getByName(host), 6466);
            InetAddress a = d.getLocalAddress();
            return a instanceof Inet4Address && isTailnetHost(a.getHostAddress());
        } catch (Exception e) { return false; }
    }

    /** Milliseconds a TCP connect to host:port takes, or -1 when it fails. Blocking. */
    static long connectMs(String host, int port, int timeoutMs) {
        try (Socket s = new Socket()) {
            InetAddress a = InetAddress.getByName(host);   // name lookup is not part of the measurement
            long t0 = System.nanoTime();
            s.connect(new InetSocketAddress(a, port), timeoutMs);
            return Math.max(1, (System.nanoTime() - t0) / 1_000_000);
        } catch (Exception e) { return -1; }
    }

    /** Play Store page of the official app, or the download page when there is no store. */
    static void openStore(Activity a) {
        try {
            a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(STORE)).setPackage("com.android.vending"));
        } catch (Exception e) {
            try { a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(WEB))); } catch (Exception ignored) {}
        }
    }

    /** Opens the Tailscale app (to sign in or switch it on); the store page if it is gone. */
    static void open(Activity a) {
        Intent i = a.getPackageManager().getLaunchIntentForPackage(PKG);
        if (i == null) { openStore(a); return; }
        try { a.startActivity(i); } catch (Exception e) { openStore(a); }
    }
}
