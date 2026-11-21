# Documentation

Deeper notes for contributors and the curious. Install, usage and troubleshooting
live in the [root README](../README.md).

| Document | What it covers |
|---|---|
| [Architecture](architecture.md) | Components of the Linux and Android apps, threading, where data comes from |
| [Protocols](protocols.md) | Cast v2, Android TV Remote v2, ADB over TCP, YouTube Lounge, Bluetooth LE HID |
| [Development](development.md) | Building, checking, testing on a real TV, translations, releases |

## In short

Fling for Linux & Android is an unofficial port of
[Fling](https://github.com/tatianathevisionary/fling) by Damion Rashford and Tatiana.
On Linux it is one PyQt6 file plus a small Bluetooth service; casting goes through
`catt`, TV control through `androidtvremote2`. On Android it is plain Java with
its own small clients for every protocol and no third-party libraries.
Everything happens on the local network — the only outside calls are YouTube
lookups for titles and artwork.
