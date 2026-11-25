# Documentation

Deeper notes for contributors and the curious. Install, usage and troubleshooting
live in the [root README](../README.md).

| Document | What it covers |
|---|---|
| [Windows install guide](install-windows.md) · [RU](install-windows.ru.md) | Installing the Windows app and the browser extensions, step by step |
| [Remote access over Tailscale](tailscale.md) · [RU](tailscale.ru.md) | Controlling the TV away from home: setup, ACLs, what works remotely |
| [Architecture](architecture.md) | Desktop app, extension bridge, Android app, where now-playing comes from |
| [Protocols](protocols.md) | Cast v2, Android TV Remote v2, ADB over TCP, YouTube Lounge, Bluetooth LE HID |
| [Development](development.md) | Building, checking, testing on a real TV, translations, releases |

## In short

Fling for Windows, Linux & Android (with browser extensions) is an unofficial port of
[Fling](https://github.com/tatianathevisionary/fling) by Damion Rashford and Tatiana.
On Windows and Linux it is one PyQt6 app (`desktop/`, plus a Bluetooth service on Linux); casting goes through
`catt`, TV control through `androidtvremote2`. On Android it is plain Java with
its own small clients for every protocol and no third-party libraries. The browser
extensions talk to the desktop app over a local, loopback-only bridge.
Everything happens on the local network — the only outside calls are YouTube
lookups for titles and artwork.
