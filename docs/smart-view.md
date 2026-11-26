# Smart View — Samsung TVs and Miracast displays

[Русская версия](smart-view.ru.md)

Fling for Windows and Linux works with Samsung TVs (Tizen, 2016 and later) and with any
Miracast display, right in the main panel. There is no separate window: these TVs show up
in the normal TV chooser next to Chromecast and Google TV.

## What you get

| | Samsung on the same network | Miracast display over Wi‑Fi Direct (e.g. a hotel TV) |
|---|:---:|:---:|
| **Mirror screen** — the whole computer screen on the TV | ✓ | ✓ |
| TV only / Extend / Duplicate (Windows), the choice is remembered | ✓ | ✓ |
| The TV's display set to its largest 16:9 mode automatically (Windows) | ✓ | ✓ |
| The panel's remote: arrows, OK, back, home, volume, mute, channels, typing | ✓ | — |
| Apps from the panel (YouTube, Netflix, Prime Video, Disney+, Spotify, Plex…) | ✓ | — |
| Turn the TV on over the network (Wake-on-LAN) | ✓ ¹ | — |
| Videos, music, photos from the computer and media links — over DLNA | ✓ | — |
| Other links: YouTube opens in the TV's YouTube app, the rest in the TV's browser | ✓ | — |

<sub>¹ needs *Power On with Mobile* on the TV (see below).</sub>

## How it works

- **Mirroring is Miracast (Wi‑Fi Display)**, the same thing Samsung's Smart View uses. Fling
  doesn't stream video itself: on Windows it opens the system *Cast* panel (Win+K) and picks
  the TV in it for you; on Linux it asks
  [GNOME Network Displays](https://gitlab.gnome.org/GNOME/gnome-network-displays) over D-Bus.
- **Wi‑Fi Direct needs no shared network.** Guest and hotel Wi‑Fi usually isolate devices
  from each other, so a normal search finds nothing — but Windows still sees Miracast displays
  in range over Wi‑Fi Direct, and Fling lists them as *Miracast · Wi‑Fi Direct*. Only mirroring
  works for them; the remote, apps and files need the TV on the same network as the computer.
- **The Samsung remote** is the TV's own network remote (a WebSocket on port 8002, 8001 on
  older sets). The first time, the TV asks whether to allow *Fling (Windows)* — press **Allow**
  with the TV's remote. Fling keeps the token the TV hands out and won't ask again.
- **Files** go over DLNA: Fling serves the one file you picked from a small local web server
  that answers only the address of the TV it was sent to; picking another file replaces it.
- **Resolution.** Windows often gives a Miracast TV a 4:3 mode such as 1152×864, so the picture
  looks huge and stretched. After *Mirror screen*, Fling waits for the TV's display to appear
  and switches it to the largest 16:9 mode the TV offers (usually 1920×1080 over Miracast;
  2K/4K when the TV and Windows offer it). Displays that were there before are never touched.

## Requirements

- **Windows**: the computer must support Miracast (Wi‑Fi and a graphics driver with *Wireless
  Display*). Check with `netsh wlan show drivers` → *Wireless Display Supported: Yes*.
- **Linux**: install GNOME Network Displays (your distribution's app store or Flathub).
- **The TV**: for mirroring, turn the TV on; some TVs need *Screen Mirroring* opened as the
  source. For turning the TV on over the network: *Settings → General → Network → Expert
  Settings → Power On with Mobile*.

## Troubleshooting

| Problem | What to do |
|---|---|
| The TV isn't in the list | Turn it on and press **Select** → *Search again*. On a hotel network only Wi‑Fi Direct works: make sure Wi‑Fi is on on the computer. |
| "Press Allow on the TV screen" stays | Look at the TV: it shows a prompt for *Fling*. If you pressed *Deny*: *Settings → General → External Device Manager → Device Connection Manager* → allow Fling. |
| The picture on the TV is stretched | Use *TV only* or *Extend* in the panel. In *Duplicate*, Windows sends the laptop's aspect ratio (often 16:10) and the TV stretches it. |
| Windows' Cast panel opens but nothing is chosen | Pick the TV in it by hand; Fling then fixes the resolution anyway. |
