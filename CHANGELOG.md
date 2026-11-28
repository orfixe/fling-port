# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is an unofficial port of [Fling](https://github.com/tatianathevisionary/fling)
by Damion Rashford and Tatiana; see [CREDITS.md](CREDITS.md).

## [Unreleased]

### Added

- macOS: **the browser extension works with the Mac app.** The same local bridge as on Windows /
  Linux (`127.0.0.1:47613`, extensions and local tools only): cast the tab from where the page's
  video is, the popup's remote, right-click casts. The unpacked Chrome / Firefox extension ships
  inside the app (*Settings → Casting → Show extension folder*). Safari needs Xcode to build.
- macOS: **⌃⌥⌘C casts the current tab from anywhere** without Accessibility access (upstream's
  ⌘⇧C still works where granted); Firefox, Edge, Brave… through the extension.
- macOS: **files from the Mac to the TV** — videos, music, photos, folders and m3u / pls playlists
  as a queue (next / previous, repeat, shuffle), **photo slideshows**, subtitles next to a video
  (srt / ass / Windows-1251 converted), resume where a film was left off.
- macOS: **notification when the TV moves to the next video / track** (can be turned off).
- macOS: **Settings window** — language, open at login, casting browser tabs and the extension
  hint, notifications, the hotkey, the extension link, queue options. No themes: the Mac app
  follows the system's appearance.
- macOS: *Cast clipboard URL* also takes a copied file path and keeps `?t=` start times.

## [0.5.8] - 2026-11-28

### Fixed

- Android: **"An update is available" kept coming back after updating.** Releases 0.5.6 and
  0.5.7 carried the 0.5.5 APK, so the app compared its 0.5.5 with the release's 0.5.7, offered
  the update, installed the same 0.5.5 again — and offered it again. This APK is 0.5.8.
- Android: **one failed handshake no longer says "the TV doesn't trust this phone any more".**
  A TV that has just woken up or restarted can drop the first TLS handshakes; for a paired TV
  Fling now tries again after 2, 4 and 6 s before asking to pair. Not again for a TV that
  refused the phone in the last minute, key presses during the wait are dropped instead of
  arriving late, and switching TVs or closing the app stops the wait at once.

## [0.5.7] - 2026-11-28

### Fixed

- PC: **automatic updates were stopped by Fling's own guard.** In the signed builds the guard
  blocked the downloaded setup (it is neither in the install's file list nor
  Authenticode-signed) and showed "Fling stopped a program…". The guard now lets through
  exactly the setup whose SHA-256 is in the release's SHA256SUMS.txt signed with the release
  key — the list the updater already checked — and stops watching while it installs.
  **From 0.5.5 or 0.5.6, install 0.5.7 by hand once** (download the setup from the release
  page); later updates install by themselves.

- PC: **`fling quit` with no Fling running started the whole app** for a moment (it connected
  to the TV and left a TV search behind). Now it does nothing.
- PC: **the setup could hang for ~30 s** when it was run while Fling was searching for TVs:
  Fling's adb server kept its files busy. The setup now asks Fling to quit and stops Fling's
  own adb first (an Android SDK adb used by Android Studio is left alone).
- PC: with the panel closed and nothing playing, Fling asks the TV what's on once a minute
  instead of every 10 s (six adb starts a minute for nothing, and a fast-growing guard log).

## [0.5.6] - 2026-11-28

### Fixed

- PC: **after an automatic update the remote looked unpaired and Fling had switched to
  another TV.** A TV search cut short (by the update, a crash or a time-out) counted as
  "no Android TV on the network", so Fling picked another TV it saw in parallel (a Samsung)
  and saved it. A failed search now keeps the TVs Fling knew, and a chosen TV that doesn't
  answer once (asleep, off) stays chosen, shown offline, instead of being swapped out.
- PC: **one failed connect no longer drops the pairing.** The remote library calls any TLS
  hiccup "pair again"; for a TV that was paired, Fling now retries three times (2, 4, 6 s)
  before it shows "Pair with TV".
- PC: **"OSError: [Errno 22] Invalid argument" window during an update.** Before running the
  setup, Fling stops what it started (TV searches, adb, scrcpy), starts nothing new and
  closes the remote session properly; a TV search left without its Fling exits quietly.
- PC: with the remote not paired and no ADB / Bluetooth, keys aren't sent into the void any
  more: Fling says once to pair the remote.

## [0.5.5] - 2026-11-27

### Fixed

- PC: **"The TV no longer trusts this computer" kept coming back, in grey, with no way out.**
  When the TV has forgotten this computer (after a TV reset or update), Fling now drops its
  "paired" mark, so the panel shows "Pair with TV" at once, and says what happened and what
  to press.
- PC: errors on the panel are **red** (they were grey like hints); "…in progress" notes stay grey.
- PC: messages in plain words instead of raw errors: "No TV chosen — click Choose at the
  bottom of the panel", "The remote isn't answering: the TV is off or on another network…",
  "The TV screen didn't open: …" (no "scrcpy"), "There's no link on the clipboard", and
  clearer menu items ("Cast this tab instead of the current video", "TV screen in its own
  window", "Mouse on the TV, no picture", "Turn the TV on").

## [0.5.4] - 2026-11-27

### Changed

- PC: **ADB in one click.** Fling finds the TV's ADB ports itself (adb's mDNS) — no typing
  ports; after a reboot, when wireless debugging moves to a new port, it follows. Each TV keeps
  its own port (one global port mixed up two TVs). Then it does what the TV needs: waits for
  "Allow" on the TV and connects as soon as it's pressed; when the TV shows "Pair with code",
  asks for the 6 digits only; when debugging is off, says how to turn it on and connects by
  itself once it's on. IP and ports by hand are under "By hand…".
- PC: **the TV list is sorted by kind** — Android TV / Google TV, Chromecast, Samsung, screen
  mirroring (Miracast) — in the chooser and the menus; in each, the current TV, then those
  online, then by name. The kind comes from what the TV answers to (remote, ADB, Cast).
- PC: when the TV in use stops answering and another known TV answers, the panel offers it:
  "“Living room” is offline — switch to “Bedroom”", one click.

- PC, LIVE: **less delay.** The TV's encoder is asked for low latency (realtime priority, at
  most one frame held; a TV that refuses gets the plain start and is remembered), and "auto"
  runs at 60 frames/s — the encoder holds frames, so twice the frames is about half the wait.
  "Smooth" is now **"Fast"**: 960 px at 60 frames/s, the least delay.
- PC, LIVE: **the mouse lands where it points.** The default is now "Mouse": the computer's
  own pointer over the picture, each click exactly there. The TV's own cursor (the old
  default) moves from relative steps with Android's acceleration and can't stay in step with
  the picture — it's still there as "TV cursor" for apps that need it.
- PC, LIVE: **the keyboard goes to the TV** as soon as the picture opens and after a click on
  the toolbar (before, only after a click on the picture). Letters scrcpy can't type —
  Russian, accented — go through the TV remote instead.
- PC, LIVE: the number next to the quality said "1 ms" while the picture lagged: it is the
  network only. It now reads "network 1 ms", and its tooltip explains the rest of the delay
  (the TV compressing each frame, which the TV doesn't report).

### Fixed

- PC: **Fling 0.5.3 closed right after starting on computers that had Fling since 0.3.1–0.3.2.**
  The extension folder kept the Firefox zip of every past version; the oldest ones weren't in
  the signed file list, so fling-guard took them for tampering and closed Fling. The setup now
  makes that folder afresh, and the build ships this version's zip only.

## [0.5.3] - 2026-11-27

### Added

- PC: **updates by themselves.** Fling checks GitHub once a day. On Windows a new version is
  downloaded in the background, checked against the release signature (fling-guard) and the
  signed checksum list, and installed while the panel is closed and no TV screen is shown;
  Fling restarts by itself. Settings → About: Install / Skip this version, and switches for
  checking and installing automatically. Linux and source runs get a notification.
- PC: **a report for the developer** — Settings, the tray menu or `fling report` make
  `fling-report-<date>.zip` in Downloads: version, system, screens, settings and the log, with
  passwords, tokens, keys, e-mails, MACs, public IPs and the user name removed. The log now has
  the time on every line, Qt warnings, and the stacks of crashes.
- Android: **a report for the developer** (Settings → About), shared through the share sheet:
  the app's own log (errors, the TV's connection, pairing, updates, crashes) and its logcat,
  cleaned the same way.
- Android: **Skip this version** in the update window and notification, and **Install updates
  automatically**: the daily check downloads and installs the update while Fling is closed
  (Android 12+ without asking once Fling has updated itself before).
- Android: **updates download in the background**, with the progress in a notification
  (and Cancel); Fling can be closed meanwhile. When the file is in, Android's install screen
  opens at once if Fling is on screen, otherwise a "Tap to install" notification waits.
- Android: **a notification when a new version is out**: once a day Fling checks GitHub, also
  while closed and after a reboot; "Update" in the notification starts the download. Each
  version is announced once; Settings → "Tell me about new versions" turns it off. (Not a push
  service: nothing is sent to the phone, it asks GitHub.)

### Fixed

- Browser extension: on some YouTube videos it **didn't offer to cast the tab** — the popup's
  "Cast" stayed disabled with "not a web page", and the app's panel never heard of the tab.
  Finding the page's player waits for every frame, and some ad / embed frames never answer;
  that wait now gives up after 1.5 s (then asks the top frame alone), and the tab is
  reported either way.
- PC: **WASD drove the TV only every other time** on Windows: the keyboard hook that catches
  keys for the tray panel knew the arrows but not W A S D (nor H, M, P, < >), so they worked
  only when the panel happened to have the focus. The same keys on the Russian layout too,
  and the panel's d-pad now lights up for them as for the arrows.

### Changed

- PC and Android: **the first screen (language)** no longer mixes Russian and English on one
  line: "Fling", then two cards, each entirely in its own language (Русский · Продолжить на
  русском / English · Continue in English); a tap goes on.
- PC: **one keyboard capture with three modes** (the "Capture ▾" button on the strip): the
  whole keyboard to the TV (new default — nothing typed leaks into the app behind the panel),
  only the remote's keys, or none. A key that does nothing on the TV says so; F1, "?" or a few
  such keys in a row open a guide: which key does what, and the ways out (Esc, the other
  modes, typing on the TV). Ctrl / Alt / Win shortcuts and the laptop's media keys always stay
  with Windows. The numpad (8 2 4 6 5 0 3) drives the TV too.
- Android: an update ended at once with "connection closed" when the connection to GitHub's
  file storage dropped. The APK now comes from two sources, each tried up to 5 times, a dropped
  download resumes where it stopped, the check falls back to github.com when api.github.com
  can't be reached, and a failure says which server and what to try, with Try again and
  Download in the browser.
- Android: after allowing "Install unknown apps" for the first update, the "Update" window now
  comes back by itself instead of asking to tap "Check for updates" again.
- Android (English): the update button read "Refresh".

## [0.5.2] - 2026-11-27

### Changed

- Android: **messages appear in one line at the bottom of the screen**, over the content, so
  they are seen wherever the page is scrolled: neutral for progress and confirmations, red for
  a failed action; tap to dismiss. This replaces the orange banner at the top of the page and
  the toasts.
- Android: the **language is chosen in Settings** (and at the first launch) only, no longer
  with a third switch at the bottom of the main screen.
- Android: hints are grey, not orange; a failed pairing is shown in red.

### Fixed

- Android: a TV that is off or away no longer shows an error each time the app opens: the
  status line under the title turns red and the TV is checked again at once (tap it to check
  again).
- Android: the TV dot was green whenever a TV was chosen; it is now green when the TV answers,
  red when it doesn't and grey before the first check, as on the PC.
- Android: "Sent to TV" was shown before the TV answered; it now says "Sending to the TV…" and
  a failure follows as an error.

## [0.5.1] - 2026-11-26

### Added

- Android: **Cast checks that the device is a real Chromecast.** A Chromecast replaces its TLS
  certificate by itself, so it can't be pinned; Fling now does what Chrome and the Google Cast
  SDK do — a challenge on the `deviceauth` channel, answered with the device's certificate
  (issued by Google's Cast root) and a signature over this connection's certificate. A man in
  the middle can't answer it, and Fling doesn't connect.
- Android: **a TV chooser like the PC's.** "Switch" opens a screen that searches at once and
  shows every TV as a card: at home (Chromecast, Google TV, Samsung), ADB devices on the same
  Wi‑Fi (network debugging, e.g. a Fire TV) and Tailscale TVs with their delay; what answers on
  each (Cast, remote, ADB, Samsung) and whether it's online. Add by address right there.
- Android: a **Google Play** build (App Bundle, no self-update) next to the GitHub APK.
- Android: TV certificates of the remote (6466/6467) and Samsung (8002) are **pinned on first
  use**; pairing again or forgetting a TV learns them again.

### Changed

- Android: built with Gradle, AndroidX and Material; release APKs are signed with a new release
  key **with key rotation**, so installed apps keep updating (Android 9+ moves to the new key,
  Android 8 keeps the old one).
- Android: Settings → theme is one **Auto | Light | Dark** control, with **AMOLED** (pure black)
  as its own switch; a saved "Black" theme becomes Dark + AMOLED.
- Android: a proper settings gear icon.
- Windows, Linux, browser extension: no changes besides the version number.
- macOS: installs without GitHub CLI once the repository is public; licensing notes updated now
  that the original Fling is under the Boost Software License 1.0.

### Fixed

- Android 17: Settings closed itself when opened.
- Android 8–10: a crash on `WindowInsets.CONSUMED` (API 30).

## [0.5.0] - 2026-11-26

### Added

- Desktop: **Smart View for Samsung TVs and Miracast displays, inside the main panel** (no
  separate window; see [docs/smart-view.md](docs/smart-view.md)). Samsung TVs and Miracast
  displays in range over **Wi‑Fi Direct** appear in the normal TV chooser, so mirroring works
  without a shared network too (hotel rooms, guest Wi‑Fi). *Mirror screen* in the panel and the
  tray (`fling mirror`): Fling opens Windows' Cast panel and picks the TV itself (Linux: GNOME
  Network Displays over D-Bus), then moves the TV's display to its largest 16:9 mode — a 4:3
  mode like 1152×864 used to stretch the picture. *TV only / Extend / Duplicate* in the panel,
  remembered. With a Samsung on the same network, the panel's remote, typing, volume and apps
  drive it over the TV's own network remote (the TV asks "Allow?" once), it turns on over the
  network, and videos, music, photos and media links go to it over DLNA (YouTube links open in
  its YouTube app, other pages in its browser). The file server serves only the picked file and
  answers only the TV's address.
- Desktop: a **first-run welcome** — the language first, then a short animated tour (Skip,
  arrow keys); Settings → About replays it.
- Desktop: **Hide** under the extension hint removes *Cast this tab* and the hint from the panel
  and the tray; Settings → General → *Casting browser tabs* brings them back.
- Desktop: Settings → About shows the **build** (commit and date; "from source" for a run from
  the repository), and the copied system info includes it.
- Android: **Samsung TVs** (Smart View) in the same TV list — found over SSDP or added by IP. The
  app's remote (d-pad, back, home, power, volume, typing, app chips) drives them over the TV's
  own WebSocket; the TV asks "Allow?" once and Fling keeps its token. Links and phone files go to
  the TV over DLNA (YouTube opens in its app, other pages in the TV's browser), plus *Mirror
  screen* (opens Samsung's Smart View or the system Cast screen) and *Turn the TV on over the
  network* (Wake-on-LAN).
- Android: on first launch, the **language first** (Русский / English), then the tips in it;
  Settings → About can show the tips again and shows the **build** (version, commit, date).

### Changed

- Desktop: the tray menu is grouped in a steadier order (casting, playback, TV and tools,
  switches, windows, Quit); *Fling log* left the ADB submenu (it stays in Settings → About and
  as `fling log`); wording fixes in the ADB console and the extension.
- Desktop: the TV's dot is **really green or red** now: Fling checks every known TV in the
  background (all its ports at once, about a second) instead of turning green whenever a TV is
  selected. Click the TV's name in the panel to **switch TVs** in one click — each with its
  status; the TV chooser cards show the same dots.
- Desktop: *Open on TV…* is now *Files from this computer to the TV…* (videos, music or photos, a
  whole folder, a photo slideshow, recent), in the panel and the tray.
- Desktop: a real cog for Settings.
- Desktop: after a theme or language change, Fling opens the panel in its new look instead of
  Settings again.

### Fixed

- Desktop: up to 10 small windows flashed for a moment at every start, worst at boot with
  autostart (widgets were shown before they were put into the panel).
- Desktop: switching the theme or the language could leave no Fling running: the new copy
  talked to the old one while it was still quitting and exited. It now waits for the old copy
  to end, and only a running Fling that answers counts.
- Desktop: the switch between the panel (full or compact) and LIVE no longer shakes at
  125/150 % scaling: the morph window stays still and draws on whole pixels.

## [0.4.1] - 2026-11-26

Remote access over Tailscale, the TV screen on the phone, and a Catalog of official apps for the
TV.

### Added

- Android: **LIVE on the phone** — the TV's screen in Fling for Android with low latency
  (scrcpy: the official scrcpy-server, checked against its published SHA-256, hardware decoding
  straight to the screen), a mini remote over the picture, swipes as arrows, typing to the TV and a
  latency chip. Works at home and over Tailscale; quality adapts to the network.
- **Remote access over Tailscale** — desktop: TVs on your tailnet in the TV chooser, a *Remote
  access* page in Settings with what works away from home and an example ACL, a keep-alive with
  the link quality in the footer ("via Tailscale · 38 ms"), LIVE starting at Saver quality.
  Android: add a TV by its Tailscale address. See [docs/tailscale.md](docs/tailscale.md).
- Desktop: a **Catalog** tab in the Tools window (`fling catalog`) — install free apps on the TV
  over ADB (VLC, Kodi, Jellyfin, Moonlight, LocalSend, Tailscale, Material Files, F-Droid) and
  FFmpeg on the computer, from official sources only. Every download is checked against the
  publisher's checksum or F-Droid's signed index, and every APK against the developer's signing
  key; the tab shows what is already on the TV and opens or removes it.

## [0.4.0] - 2026-11-25

Settings and themes, a much bigger remote, playlists and slideshows, a Tools window for adb and
scrcpy, and signed releases with a guard on Windows. Android casts files from the phone.

### Added

- Windows: **signed releases** and **fling-guard**. Every release carries `SHA256SUMS.txt` with a
  minisign signature made with the Fling release key — see
  [SECURITY.md → Is my copy the real Fling?](SECURITY.md#is-my-copy-the-real-fling). The guard
  starts with Fling, checks the installed files against a signed list, lets only listed or
  trusted programs run as Fling's children, and stops a child that keeps 30% of the CPU for 90 s.
- Desktop: **Settings** window (gear in the panel's corner, the tray menu, `fling settings`):
  Appearance, General, Remote, Casting and **About** (`fling about`) with version, credits,
  licenses, an optional update check and "Copy details" for bug reports.
- Desktop: **11 themes** (6 dark, 5 light) and 11 accent colours, with a live preview on hover;
  "As the system" picks a day and a night theme and follows the system accent colour too.
- Desktop: **TV chooser window** — "Choose" (and *Find TVs…* in the tray) searches at once with
  an animation, lists TVs as cards (model, address, paired, recently seen) and adds a TV by IP;
  TVs added by hand can be removed.
- Desktop remote: **modes** under the app grid — Arrows, Touchpad, Numbers (digits, channels,
  input, guide) and Media (prev / next, stop, subtitles, audio track, colour keys).
- Desktop remote: **favourite apps** per TV — pin the app that is open, any app installed on the
  TV or a package by name; reorder, rename, restore the default set.
- Desktop remote: **scenes** — steps such as turn on, open an app, set the volume, press a key,
  wait, turn off; run from the panel, the tray or `fling scene "Name"`.
- Desktop remote: **sleep timer** with a one-minute warning, explicit Turn on / Turn off, and
  `fling sleep-timer N`, `fling tv-on`, `fling tv-off`.
- Desktop remote: optional **keys in any app** (Ctrl+Alt+Shift + arrows, Enter…) and the
  keyboard's media keys for the TV; both off by default.
- Desktop media: mov, opus and image links cast directly; webp / tiff / heic and very large photos
  are converted; avi, wmv, ts and similar play in the TV's own player over ADB.
- Desktop media: **queue** — several files, folders and m3u / m3u8 / pls playlists, with shuffle
  and repeat; `fling play`, `fling next`, `fling prev`.
- Desktop media: **photo slideshow** from a folder with an interval (`fling slideshow`).
- Desktop media: **subtitles** next to a video (or in `Subs\`) are found automatically, chosen in
  the menu or dropped on a playing film; SRT in Windows-1251 and ASS work.
- Desktop media: links with `?t=` / `#t=` start at that point; long videos **resume** where you
  stopped; **recent casts** in "Open on TV…"; playback speed 0.5×–2×.
- Desktop: **Tools** window (tray menu, the ADB badge, `fling tools`) finds adb and scrcpy already
  on the computer (PATH, Android SDK, WinGet, Scoop, Chocolatey, Homebrew…), lets you choose a copy
  and installs or updates them with progress and cancel; downloads are checked against the
  publisher's checksums.
- Browser extension: right-click to cast a page, link, video or audio, **"Cast from this
  moment"**, and type the selected text on the TV.
- Browser extension: **timestamps** in YouTube, Twitch, Vimeo and `#t=` links start the TV there.
- Browser extension: new **shortcuts** (cast from this moment, play / pause, ±30 s, TV volume)
  that you can assign in the browser.
- Browser extension: previous / next for YouTube, click the progress bar to seek, "Cast from the
  beginning" next to Resume, **recent casts**; the options page is split into sections.
- Android: cast **photos, videos and music from the phone** — Share → "Cast to TV" from Gallery or a
  file manager (several files play as a queue), "Open with", or "File from this phone" in the Link
  card. Fling serves them over Wi-Fi with seeking; photos are sent upright and sized for the TV
  (HEIC included); music shows its tags and cover.
- Android: a **playback notification** with lock-screen controls while something plays on the TV
  (pause, ±30 s, previous/next for a queue, stop); the **phone's volume keys** can drive the TV
  through it.
- Android: **sleep timer** in the remote (15 min to 2 h) — turns the TV off even with Fling closed,
  and never turns on a TV that is already off.
- Android: **Settings** (gear in the header): theme (system, light, dark, black AMOLED), accent
  colour incl. **Material You** wallpaper colours on Android 12+, with a live preview; switches for
  the notification, the volume keys and "Keep the screen on"; language, updates.

### Changed

- Desktop: "Appearance" moved from the tray menu into Settings.
- Desktop: Turn on / Turn off never toggles a slow TV back: POWER is sent only when the TV has just
  reported the opposite state.
- Desktop: redrawn keyboard and Home icons — a thin outline instead of a solid block.
- Android: no app logo inside the app; the header title lines up with the cards.

### Fixed

- Android: update check no longer crashes on a long numeric version; backups and device transfer
  exclude the pairing key; YouTube links are recognised only on youtube.com; the mic no longer
  races its own release; artwork cache is bounded.

### Security

- Network paths (`\server\share`, `file://host`) are refused for casting, also inside playlists
  and dropped folders.
- The local bridge can be limited to given extension IDs (`"bridge_extension_ids"` in
  `fling.json`).

## [0.3.3] - 2026-11-24

The desktop app (Windows above all) grows a LIVE section; Android and macOS are rebuilt
from the same code as 0.3.2.

### Added

- Desktop (Windows): **LIVE** — "Remote | ● LIVE" at the top of the panel. LIVE grows the panel
  into the TV's screen, live: the video edge to edge, one row of icon buttons under it (back,
  home, play, volume, power, a d-pad flyout), sizes S / M / L and full screen. Click the picture
  and the mouse becomes a real pointer on the TV; move it past the picture's edge (or press Alt)
  and it comes back. The mouse can also tap or just watch, the keyboard can go to the TV or stay
  on the PC, and "Sound on PC" brings the TV's sound to the computer (Android 11+).
- Desktop: LIVE watches how the picture is doing — the network delay to the TV, lost packets and
  skipped frames, as a coloured dot in the header — and says so when the picture may lag. Quality:
  auto / sharp / smooth / saver; auto steps down by itself. No extra buffering (lowest latency).
- Desktop: "Cursor on the TV (no picture)" — only the pointer, in a small window.
- Desktop: ADB console — commands on the selected TV with live output, quick commands, history and
  Stop; commands that may delete data or restart the TV ask first; pulled files land in Downloads.
- Desktop: ADB works as a fallback remote — keys go as key events when the TV isn't paired.
- Desktop: send files to the TV — drop them on LIVE or the ADB console, `fling send`, or Explorer's
  Send to → "Fling — to the TV": APKs install, a video / song / picture plays (copied over ADB when
  the TV can't fetch it), anything else lands in the TV's Download folder.
- Desktop: "Appearance" in the tray menu — dark, light or as the system, and the accent colour.
- Desktop: the "keyboard captured" strip has a Release / Capture button (remembered) and a tooltip
  that lists the keys that go to the TV.
- Desktop: `fling screen`, `fling mouse`, `fling adb`, `fling log`, `fling send`,
  `fling install-extension`.
- Windows: the setup ships scrcpy (LIVE), so nothing is downloaded on first use. "Install the
  extension…" adds the browser extension to Chrome or Edge by itself, with the manual steps as a
  fallback.
- NOTICE and THIRD-PARTY-NOTICES.md; the setup puts them, LICENSE and CREDITS.md next to Fling.

### Changed

- Desktop: what's playing comes from one lasting Cast connection the TV pushes its status to,
  instead of starting catt every 2 seconds (in the setup build, a second Fling.exe each time).
- Desktop: clearer keyboard and Home icons; hints and errors are grey instead of orange; the credit
  line wraps instead of hiding behind the language flag.
- LICENSE names the copyright holder of the port; source files carry SPDX headers.

## [0.3.2] - 2026-11-23

Includes the macOS update below (Russian / English, WASD, TV volume slider, no keychain prompts).

### Fixed

- Android: the app crashed on Android 8–12 when adding a TV by IP, sharing a YouTube link or
  looking up what's playing over ADB.
- Android: no crash when Back or a dark-mode switch lands during a TV status update.
- Desktop: key presses right after the TV wakes no longer fail with "TV closed the connection" —
  one TV-remote connection at a time.
- Desktop: a locked `fling.json` or an error in a button handler no longer closes Fling; Add TV
  by IP rejects digits such as "²" instead of crashing.
- Desktop (Windows): Cyrillic titles and TV names from catt are no longer garbled.
- Windows: the `fling` command works for user folders with spaces and when run with no arguments.

### Security

- Desktop: the browser bridge only casts web (http/https) links, not local or network files;
  artwork is fetched over http/https only; seek requests are bounded.
- Android: TV keys and the paired-remote certificate are kept out of backups; oversized
  remote-protocol frames are rejected.

### Changed

- Credit line names orfixe as the porter in the apps, the extension, the installer and macOS.

## macOS update - 2026-11-23

The macOS files in the [0.3.1] release were replaced; Windows, Android and the extensions are
unchanged.

### Added

- macOS: Russian / English like the Windows build — follows the system language; *Language* row in
  the panel and the right-click menu switches it and relaunches Fling.
- macOS: the keyboard button types on the TV live (letters appear as you type, Return runs the
  search) or sends on Return (right-click to choose); paste the clipboard on the TV with the
  clipboard button or ⌘V.
- macOS: W A S D as an alternative to the arrow keys (E = OK, Q = Back), picked with a small
  arrows / WASD switch under the d-pad; a compact key guide below it follows the switch.
- macOS: TV volume slider from the level the TV reports (from the Windows build) — percent of the
  TV's own scale, a bubble while dragging, the speaker mutes; TVs that don't report volume keep the
  − / mute / + keys.
- macOS: the small ⌨ badge beside the TV power key turns keyboard capture on / off (on by default).

### Changed

- macOS: built on the author's current Fling with the author's d-pad layout (the round pad is gone);
  the bright "keyboard captured" strip is now a quiet badge.

### Fixed

- macOS: Fling kept asking for access to a key in the keychain ("Always Allow" didn't stick). The
  TV remote's identity now stays in memory, loaded from Fling's own file — no keychain, no prompts.
  Pairing is kept.
- macOS: the pairing code field only took the English layout; the Russian one works now
  (ф и с в у а → A–F), as on Windows.
- macOS: the build failed with Command Line Tools on macOS 27 (SwiftUI macro plugin missing); it
  now falls back to the macOS 26 SDK and finds the binary in either build folder.
- macOS: signed with a local "Fling Port Local" identity when the Mac has one, so the signature no
  longer changes with every build.

## [0.3.1] - 2026-11-22

### Added

- Desktop: install an APK on the TV over ADB — Android badge → *Install APK…*, the same button in
  Connect TV, `fling install-apk app.apk`, or right-click an `.apk` in Explorer → *Install on TV (Fling)*
  (Windows). Several files at once install one split app. Clear messages for the usual failures
  (other signature, newer version installed, wrong processor, no space).
- Android: Fling checks for a newer release (quietly once a day, or *Version … · Check for updates*
  at the bottom), downloads `Fling.apk` and opens Android's own install screen; it goes over the
  old app, pairing and settings stay. While the repository is private, GitHub needs your own
  read-only token, which Fling asks for once and keeps only on the phone.
- Windows: a real setup, `Fling-Setup-<version>.exe` (Inno Setup): per-user install without admin
  rights, Start menu, optional desktop icon and autostart, the `.apk` menu and the `fling` command,
  uninstall from Settings → Apps.

### Changed

- Desktop: the d-pad is a round pad like on a TV remote — the four arrows sit at the same
  distance from OK, the whole quarter of the ring presses that arrow (hold to repeat), and
  Back / Home sit below its sides.
- Desktop: a strip at the top of the panel says when the keyboard is captured for the TV
  (Esc releases it); it hides while a text field is open.
- Windows (installed from source): Fling runs as `Fling.exe` with its own name and icon, so Task
  Manager lists "Fling" as one process instead of two "Python" ones.
- macOS: the same round d-pad and "keyboard captured" strip; Esc closes the panel.
- Desktop: TV volume as a slider with the TV's own level (the TV reports it over the remote
  session, no polling); the speaker icon mutes. TVs that don't report a volume keep the
  − / mute / + keys. The laptop's own volume keys no longer drive the TV.
- Desktop: the volume shows in percent; while you drag, a bubble above the slider shows the
  level. Works on TVs whose scale isn't 0–100.
- Desktop: shorter button labels ("Settings", "Reboot", "Turn on" instead of "… TV"), and the
  keyboard hint under the d-pad is gone — each button's tooltip names its key.

### Fixed

- Windows installer: Fling is stopped before catt is updated. Updating catt while Fling was
  running could fail and leave casting without catt; a rerun now repairs that.

## [0.3.0] - 2026-11-22

### Added

- Desktop: the laptop keyboard works as a TV remote — arrow keys (hold to scroll through menus),
  WASD, Enter, Backspace, numpad and media keys; WASD also works on a Russian layout.
  On Windows this works while the panel is open, even though Windows keeps the keyboard on
  the browser: arrows, Enter = OK, Backspace = Back, Home, +/− volume, Space, Esc closes the
  panel, Shift+←/→ seeks. The matching button in the panel lights up. Ctrl / Alt / Win
  shortcuts are left alone, and a text field gets the keyboard while it is open.
- Desktop: the keyboard icon opens a field for typing on the TV.
- Desktop: an Android badge shows whether ADB is connected. Once the TV allows debugging,
  Connect TV offers everyday tools: wake, sleep, home, screenshot, settings, apps,
  force-stop, reboot, disconnect. The Android button in the panel opens the same menu.
- macOS: a private build of Fling with the keyboard icon, installed from Terminal
  (`macos/install.sh`) so macOS doesn't quarantine it.
- macOS: the Mac keyboard drives the TV while the panel is open — arrows, Return = OK,
  Delete = Back, Home, +/− volume, Space, Shift+←/→ seeks; the matching key lights up.

### Fixed

- The TV no longer gets a flood of "Allow debugging?" prompts: Fling waits before retrying,
  doesn't connect by itself when Connect TV opens, and uses IPv4 only.
- Connect TV accepts a typed IP and a wireless-debugging port other than 5555 (or `IP:port`).
- If `adb` is missing, Connect TV shows where to download platform-tools.
- Fewer windows on top of each other: help doesn't open by itself, and Connect TV and the guide
  don't both stay open.

### Changed

- Connect TV and the extension options no longer show the YouTube-code and Bluetooth rows or the
  unused notification / port settings. Notifications and start-at-login stay in the tray menu.

## [0.2.0] - 2026-11-21

### Added

- **Windows port** (Windows 10 1809+ / 11, x64; ARM64 through emulation): the same tray panel,
  Connect TV window, remote, voice search and guide as on Linux. A standalone `Fling.exe`
  (catt and yt-dlp bundled) or an install from source with `windows\install.cmd`, both without
  admin rights. Start menu entry, `fling` command, start at login, Windows notifications.
- Windows: system-wide **Win+Shift+C**; if another app owns it, Ctrl+Alt+Shift+C, and the panel
  shows which one works.
- **Browser extension** for Chrome, Edge, Brave, Opera, Vivaldi, Yandex, Firefox and Safari,
  built from one source (`extension/build.py`): a popup remote, *Cast this tab* that resumes at
  the page's position, the page's own stream for sites yt-dlp can't open, *Cast link / video /
  page* in the right-click menu, Alt+Shift+C, pausing the page after casting, English and Russian.
- A local bridge on `127.0.0.1:47613` between the extension and the app
  (`desktop/fling_bridge.py`). It accepts browser extensions and local tools only (Host and
  Origin checks, a required `X-Fling` header). With it the panel's *Cast this tab* works with
  any browser, not just Firefox.
- A notification when the TV moves to another video or track. The bell in the panel, the tray
  menu and the extension settings turn it on (green) or off (red).
- Panel: a compact view (artwork and playback keys, labels in tooltips), a **?** button for the
  guide, a small language flag at the bottom, icons for every row, clearer labels
  (*Pair with TV* with a green TV-plus icon, *Cast copied link*, *Take screenshot*, *Select* next to the TV's name). The Connect TV window, the TV menu and screenshot messages are now translated into English too.
- Android: short tips on first launch, one at a time over the real screen (a spotlight and a
  small card with the system's Material motion) instead of a long help page; the full guide is
  one tap away. Choose between several TVs, add or remove TVs by IP.
- Android: `Fling.apk` 0.2.0 (version code 2). It is signed with a new key, so a phone with
  0.1.0 has to uninstall the old app first and pair the TV remote again.
- `android/build.sh` also works on Windows (Git Bash) and creates the debug key when missing.
- Linux: choose between several TVs from the panel footer, the tray menu and the Connect TV
  window; find TVs again; add a TV by IP address; identical names are told apart by address;
  paired TVs are marked. Built-in "How to use" guide (`fling help`, panel, tray menu).
- Step-by-step Windows guide in English and Russian (`docs/install-windows*.md`) and a Russian
  README.

### Fixed

- A TV added by IP now has to be a real address (each number 0–255). Asking the TV for its name closes the connection afterwards. On Android, removing the only TV also disconnects from it.

### Changed

- The desktop app moved to `desktop/` and is shared by Linux and Windows; every OS-specific
  call goes through `desktop/fling_platform.py`. Linux behaviour, paths and pairing are unchanged.
- Voice search on Windows records through `sounddevice` and resamples any device format
  (44.1/48 kHz, stereo or 4-channel array mics) to the TV's 8 kHz mono.
- The panel drops its transparent rounded corners in Remote Desktop sessions and without
  desktop composition (`"solid_panel"` in `fling.json` overrides).
- Missing English texts in the panel (TV screenshot, How to use, Connect TV, the footer).
- Linux: on battery, no background polling while the panel is closed; the
  Bluetooth remote service starts only when you ask for it.
- Linux: switching TVs resets now-playing and remote state for the new TV.
- Linux: background polling every 10 s instead of 2 s, using the cheap ADB read;
  `catt` is only polled for a live Cast session ADB can't see (≈10% of a CPU
  core saved while the panel is closed).

## [0.1.0] - 2026-11-21

### Added

#### Linux (KDE Plasma)

- Tray icon with a popover-style panel modelled on the macOS Fling panel:
  artwork, title, progress, play/pause, ±30 s, a debounced volume slider,
  stop, and a device footer with a switcher.
- Casting of the current Firefox tab (read from the session store) and of
  clipboard links through [catt](https://github.com/skorokithakis/catt);
  global **Meta+Shift+C** shortcut casts the current tab.
- Real now-playing: title, artist, position and play state read from the TV
  over ADB (`dumpsys media_session`), with artwork and duration looked up
  on YouTube.
- Optional YouTube "Link with TV code" for the exact video and position in
  the YouTube TV app, plus next / previous video.
- Detection of the frozen Cast session that native YouTube on Google TV
  reports: stale artwork is hidden and pause / seek are sent as remote keys.
- TV remote over the Android TV Remote v2 protocol: one-time code pairing
  (on-screen 0–9 / A–F keys, Cyrillic layout auto-mapped), power, app
  launcher, d-pad with Back / Home, volume, mute, typing and voice search;
  scrolling over the panel moves the d-pad.
- The laptop as a **Bluetooth LE remote** (HID over GATT, "Fling Remote")
  for when the network remote isn't paired.
- **Connect TV** window: one button finds the TV on the local network,
  pairs the remote, connects ADB and shows what still needs a tap on the TV.
- TV screenshots over ADB, saved to `~/Pictures/Fling`; black DRM-protected
  frames are detected and explained.
- English / Russian interface, switchable from the panel.
- Terminal remote `fling-tui`, installer / uninstaller `install.sh`.

#### Android

- Plain-Java app (Android 8.0+, no Google Play Services, no Gradle) with the
  same panel: now playing, link card, TV remote, device footer.
- Share a link to Fling and it plays on the TV; the last five links can be
  re-sent with one tap.
- Built-in Cast v2, Android TV Remote v2 and ADB clients: real now-playing
  and artwork straight from the TV, screenshots to `Pictures/Fling`.
- Phone volume keys control the TV while the app is open; touchpad mode
  (swipe = d-pad, tap = OK, long press = Back); Quick Settings tile
  "Pause on TV".
- English / Russian interface (RU | EN switch, follows the system by default).

#### Both

- Authorship line "Fling by Damion Rashford & Tatiana · unofficial port by orfixe",
  linking to the original repository.

### Security

- Release clean-up: the APK's manual-IP hint now shows an example address
  instead of a real one, and the TV's name is hidden in the screenshots.

[unreleased]: https://github.com/orfixe/fling-port/compare/v0.5.8...HEAD
[0.5.8]: https://github.com/orfixe/fling-port/compare/v0.5.7...v0.5.8
[0.5.7]: https://github.com/orfixe/fling-port/compare/v0.5.6...v0.5.7
[0.5.6]: https://github.com/orfixe/fling-port/compare/v0.5.5...v0.5.6
[0.5.5]: https://github.com/orfixe/fling-port/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/orfixe/fling-port/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/orfixe/fling-port/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/orfixe/fling-port/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/orfixe/fling-port/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/orfixe/fling-port/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/orfixe/fling-port/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/orfixe/fling-port/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/orfixe/fling-port/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/orfixe/fling-port/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/orfixe/fling-port/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/orfixe/fling-port/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/orfixe/fling-port/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/orfixe/fling-port/releases/tag/v0.1.0
