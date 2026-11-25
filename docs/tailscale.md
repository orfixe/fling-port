# Remote access over Tailscale

[По-русски](tailscale.ru.md)

With [Tailscale](https://tailscale.com), Fling controls your TV from anywhere: from work, a
trip or your phone on mobile data. Fling contains no VPN code. It relies on the official
Tailscale apps and only **reads** the computer's Tailscale status (`tailscale status --json`,
`tailscale ping`). It never runs `tailscale up`, `down`, `set` or `logout`, never changes your
tailnet's settings, opens nothing to the internet and forwards no ports.

## 1. Install Tailscale (official sources only)

| Device | Where to get it |
|---|---|
| Windows, macOS, Linux | [tailscale.com/download](https://tailscale.com/download) |
| Android phone | Google Play: **Tailscale** (package `com.tailscale.ipn`) |
| Android TV / Google TV | Google Play on the TV: **Tailscale**, or Fling → TV chooser → *Install Tailscale on the TV…* (official APK over ADB, while you're at home) |

Sign in to **the same account** on every device. On the TV, open Tailscale, sign in (it shows
a code or a QR code to confirm on your phone) and turn it on. In the TV's Tailscale settings,
turn on *Always-on* / start at boot, so the TV stays reachable after a restart.

## 2. Add the TV in Fling

**Desktop.** Open the TV chooser (*Select* in the panel footer). The **Via Tailscale** group
lists the TVs on your tailnet with their online state; click **Add**. You can also type a
Tailscale address in the *Add by IP* field: `100.x.y.z` or a MagicDNS name (`living-room` or
`living-room.your-tailnet.ts.net`). Fling checks which of Cast (8009), the remote (6466) and
ADB (5555) answer; any one is enough. *Settings → Remote access* shows the same list, what
works remotely and an example ACL.

**Phone.** Install the official Tailscale app, turn it on, and add the TV in Fling by its
Tailscale address (shown in the Tailscale app), just as you would add a TV by IP.

Pair the remote and allow ADB **once at home** (Connect TV); the pairing keeps working over
the tailnet.

## 3. What works remotely

| Feature | Over Tailscale |
|---|---|
| Remote: d-pad, volume, apps, typing, voice | Works |
| ADB: installing APKs, files, screenshots, console | Works |
| TV's live screen (scrcpy) | Works; starts at the *Saver* quality (960 px, 2 Mbit/s), the quality menu changes it |
| YouTube TV link (Lounge) | Works: it goes through YouTube's servers anyway |
| Casting links (Cast, port 8009) | Only if the TV's Cast service answers on its Tailscale address. Fling checks when adding; if it doesn't, casting is switched off for that TV with a clear message |
| Casting files from the computer / phone | Only if the TV can reach the computer back (Tailscale address, firewall); not guaranteed |
| Finding TVs automatically (mDNS) | Doesn't cross Tailscale: add the TV by address |
| Bluetooth remote (Linux) | Local only |

While a Tailscale TV is selected, the panel footer shows **via Tailscale · 38 ms** (the
tooltip tells a direct connection from a relayed one). Fling pings the TV now and then and,
when the connection comes back after a gap, reconnects the remote and ADB by itself
(backing off while the TV is away). Nothing listens on your computer: this only keeps Fling's
own outgoing connections alive.

## 4. Security: limit who reaches the TV

ADB is full control of the TV. With Tailscale's default *allow all* policy, **every** device
on your tailnet can reach the TV's ADB port. Restrict it with an ACL:

1. In the [admin console](https://login.tailscale.com/admin/machines), open the TV →
   *Edit ACL tags* → add `tag:tv`.
2. In *Access controls*, add the rule below (use your login) and remove a catch-all
   `{"src": ["*"], "dst": ["*:*"]}` rule if you have one.

```jsonc
{
  "tagOwners": {"tag:tv": ["you@example.com"]},
  "acls": [
    // only your own devices reach the TV, and only the ports Fling uses
    {"action": "accept", "src": ["you@example.com"],
     "dst": ["tag:tv:5555,6466,6467,8008,8009"]}
  ]
}
```

5555 is ADB, 6466/6467 the Android TV Remote, 8008/8009 Cast (drop them if you don't cast
remotely). Also: don't share the TV's machine with other people, and never use Tailscale
Funnel or router port forwarding for these ports.
