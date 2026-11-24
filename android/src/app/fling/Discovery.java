// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.Context;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.net.wifi.WifiManager;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** mDNS discovery of Chromecast / Google TV devices (_googlecast._tcp). */
@SuppressWarnings("deprecation")
final class Discovery {
    static final class Device {
        final String name, host, model;
        Device(String name, String host, String model) { this.name = name; this.host = host; this.model = model; }
    }

    interface Listener { void onDevices(List<Device> devices); }

    private static final String TYPE = "_googlecast._tcp.";

    private final NsdManager nsd;
    private final WifiManager wifi;
    private final Listener l;
    private WifiManager.MulticastLock lock;
    private NsdManager.DiscoveryListener discovery;

    private final ArrayDeque<NsdServiceInfo> queue = new ArrayDeque<>();
    private boolean resolving;
    private final Map<String, Device> byHost = new LinkedHashMap<>();
    private final Map<String, String> hostByService = new LinkedHashMap<>();

    Discovery(Context ctx, Listener l) {
        this.nsd = (NsdManager) ctx.getApplicationContext().getSystemService(Context.NSD_SERVICE);
        this.wifi = (WifiManager) ctx.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        this.l = l;
    }

    synchronized void start() {
        if (discovery != null) return;
        try {
            lock = wifi.createMulticastLock("fling-mdns");
            lock.setReferenceCounted(false);
            lock.acquire();
        } catch (Exception ignored) {}
        discovery = new NsdManager.DiscoveryListener() {
            public void onStartDiscoveryFailed(String t, int code) { synchronized (Discovery.this) { discovery = null; } }
            public void onStopDiscoveryFailed(String t, int code) {}
            public void onDiscoveryStarted(String t) {}
            public void onDiscoveryStopped(String t) {}
            public void onServiceFound(NsdServiceInfo info) { enqueue(info); }
            public void onServiceLost(NsdServiceInfo info) { lost(info.getServiceName()); }
        };
        try {
            nsd.discoverServices(TYPE, NsdManager.PROTOCOL_DNS_SD, discovery);
        } catch (Exception e) {
            discovery = null;
        }
    }

    synchronized void stop() {
        if (discovery != null) {
            try { nsd.stopServiceDiscovery(discovery); } catch (Exception ignored) {}
            discovery = null;
        }
        queue.clear();
        resolving = false;
        if (lock != null) {
            try { lock.release(); } catch (Exception ignored) {}
            lock = null;
        }
    }

    private synchronized void enqueue(NsdServiceInfo info) {
        for (NsdServiceInfo q : queue) if (q.getServiceName().equals(info.getServiceName())) return;
        queue.add(info);
        next();
    }

    private synchronized void next() {
        if (resolving || queue.isEmpty() || discovery == null) return;
        resolving = true;
        NsdServiceInfo info = queue.poll();
        try {
            nsd.resolveService(info, new NsdManager.ResolveListener() {
                public void onResolveFailed(NsdServiceInfo i, int code) {
                    synchronized (Discovery.this) {
                        resolving = false;
                        if (code == NsdManager.FAILURE_ALREADY_ACTIVE) queue.add(i);
                    }
                    next();
                }
                public void onServiceResolved(NsdServiceInfo i) {
                    resolved(i);
                    synchronized (Discovery.this) { resolving = false; }
                    next();
                }
            });
        } catch (Exception e) {
            resolving = false;
        }
    }

    private void resolved(NsdServiceInfo i) {
        InetAddress addr = i.getHost();
        if (addr == null) return;
        // IPv4 only — never keep a bare IPv6 host (Cast/ADB prompts and broken routes)
        if (!(addr instanceof Inet4Address)) {
            String service = i.getServiceName();
            synchronized (this) {
                String oldHost = hostByService.get(service);
                // If we already have an IPv4 for this service, ignore the IPv6 hit entirely
                if (oldHost != null && byHost.containsKey(oldHost)) return;
            }
            return;
        }
        String host = addr.getHostAddress();
        Map<String, byte[]> attrs = i.getAttributes();
        String name = attr(attrs, "fn");
        if (name == null || name.isEmpty()) name = i.getServiceName();
        String model = attr(attrs, "md");
        List<Device> snapshot;
        synchronized (this) {
            Device prev = byHost.get(host);
            String oldHost = hostByService.get(i.getServiceName());
            if (oldHost != null && !oldHost.equals(host)) {
                byHost.remove(oldHost);          // replace any previous address with this IPv4
            }
            hostByService.put(i.getServiceName(), host);
            if (prev != null && prev.name.equals(name) && eq(prev.model, model) && oldHost != null) return;
            byHost.put(host, new Device(name, host, model));
            snapshot = sorted();
        }
        l.onDevices(snapshot);
    }

    private void lost(String serviceName) {
        List<Device> snapshot;
        synchronized (this) {
            String host = hostByService.remove(serviceName);
            if (host == null || byHost.remove(host) == null) return;
            snapshot = sorted();
        }
        l.onDevices(snapshot);
    }

    private List<Device> sorted() {
        List<Device> list = new ArrayList<>(byHost.values());
        list.sort(Comparator.comparing(d -> d.name.toLowerCase()));
        return list;
    }

    private static boolean eq(String a, String b) { return a == null ? b == null : a.equals(b); }

    private static String attr(Map<String, byte[]> attrs, String key) {
        if (attrs == null) return null;
        byte[] v = attrs.get(key);
        return v == null ? null : new String(v, StandardCharsets.UTF_8);
    }
}
