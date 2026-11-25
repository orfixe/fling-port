# Protocols

Fling speaks five local protocols. Linux uses existing libraries for most of them;
the Android app implements each one itself.

## Google Cast v2

- **Transport:** TLS, port 8009. Each message is a 4-byte big-endian length followed by a
  `CastMessage` protobuf whose payload is JSON.
- **Namespaces:** `urn:x-cast:com.google.cast.tp.connection` (`CONNECT`),
  `…tp.heartbeat` (`PING` / `PONG`), `urn:x-cast:com.google.cast.receiver`
  (`GET_STATUS`, `LAUNCH`, `SET_VOLUME`, `STOP`), `urn:x-cast:com.google.cast.media`
  (`GET_STATUS`, `LOAD`, `PLAY`, `PAUSE`, `SEEK`, `STOP`).
- **Flow:** connect to `receiver-0` → receiver status gives the running app and its
  transport id → connect to that transport → media status / commands.
  Direct media files are loaded into the Default Media Receiver (`CC1AD845`).
  Media status carries `idleReason` (`FINISHED`, `CANCELLED`, `INTERRUPTED`, `ERROR`), which
  tells a queue whether to move on; `SET_PLAYBACK_RATE` changes the speed.
- **Linux** uses [catt](https://github.com/skorokithakis/catt), which also handles
  YouTube and sites supported by yt-dlp.

## Android TV Remote v2

- **Pairing — TLS, port 6467.** The client presents a self-signed certificate and
  exchanges varint-framed protobuf messages: pairing request → ack, options → options,
  configuration → ack. The TV then shows a 6-character hex code. The client sends
  `SHA-256(client modulus, client exponent, server modulus, server exponent, code bytes 2–3)`;
  the digest's first byte must equal the code's first byte, which catches typos locally.
- **Session — TLS, port 6466.** The TV opens with `configure` and `set_active`
  (feature negotiation) and sends `ping` regularly. Fling sends key injections
  (power 26, home 3, back 4, d-pad 19–23, volume 24/25, mute 164, play/pause 85,
  rewind 89, fast-forward 90), app-link launches, IME batch edits for typing, and
  voice begin / payload / end (8 kHz, 16-bit mono PCM). The TV reports power state
  and the foreground app.
- **Linux** uses [androidtvremote2](https://github.com/tronikos/androidtvremote2).

## ADB over TCP

- **Transport:** TCP, port 5555 (enabled on the TV in Developer options).
- **Handshake:** `CNXN` → the TV answers `AUTH` with a token → the client signs it
  (RSA, PKCS#1 with a SHA-1 DigestInfo prefix). If the key is unknown, the client sends
  its public key in Android's mincrypt format and the TV asks **Allow debugging?**
- **Commands:** `OPEN shell:` for text output (`dumpsys media_session`, `/proc/uptime`),
  `OPEN exec:` for binary-safe output (`screencap -p`).
- **Linux** uses the `adb` command; Android has its own client (`Adb.java`).

## YouTube Lounge (Linux)

- **Transport:** HTTPS to YouTube's Lounge API (long-polling), not to the TV directly.
- **Linking:** on the TV, *YouTube → Settings → Link with TV code*; the 12-digit code
  is exchanged for a screen id and a lounge token, which Fling stores and refreshes.
- **Use:** live `nowPlaying` / state events (video id, position, duration) and commands
  (play, pause, seek, next, previous). Library: [pyytlounge](https://github.com/FabioGNR/pyytlounge).

## Bluetooth LE HID (Linux)

- **Role:** the laptop is a BLE peripheral; the TV pairs with it as a remote
  (*Settings → Remotes & Accessories*).
- **GATT:** HID service `0x1812` with HID Information, Report Map, Control Point,
  Protocol Mode and two Report characteristics (keyboard, consumer control);
  Battery service `0x180F`.
- **Keys:** arrows / Enter / Escape on the keyboard report; Home, Back,
  play/pause, volume, mute, power, menu, next / previous, search on the consumer report.
  Each press is a notification with the key followed by an empty report.
- **Typing:** US-layout keyboard reports, so only Latin letters, digits and basic
  punctuation.
