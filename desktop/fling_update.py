# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Updates for the desktop app.

Checks GitHub on its own: 1.5 minutes after the start, then whenever 20 hours have passed
(looked at every 3 hours). A newer version that the user didn't skip:

  Windows, installed build, "Install updates automatically" on (the default):
      Fling-Setup-<v>.exe, SHA256SUMS.txt and its .minisig are downloaded to
      %LOCALAPPDATA%\\Fling\\updates; the signature is checked with fling-guard (the release
      key built into it) and the setup's SHA-256 with the signed list. Only then, once Fling
      is idle — the panel closed, no LIVE picture or screen mirroring — the setup runs
      silently (/VERYSILENT /relaunch=1: per-user, no UAC), closes Fling and starts it again.
  Otherwise (Linux, running from source, automatic install off):
      a notification; Settings → About shows Install / Download and Skip this version.

conf: upd_check (check by itself, True), upd_auto (install by itself, True),
      upd_skip (the version not to offer again), upd_checked (time of the last check)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import fling_platform as P

REPO = "orfixe/fling-port"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
WEB = f"https://github.com/{REPO}"
DIR = os.path.join(P.DATA, "updates")
CHECK_EVERY = 20 * 3600


def ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:4]) or (0,)


def _get(url: str, timeout=20, follow=True, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": "Fling-desktop", "Accept": accept})
    if not follow:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:      # 3xx arrives as an "error" without redirects
            return e
    return urllib.request.urlopen(req, timeout=timeout)


def latest() -> dict:
    """{'version', 'tag', 'page', 'assets': {name: url}}; the API, then github.com's redirect."""
    try:
        with _get(API) as r:
            d = json.loads(r.read().decode("utf-8"))
        tag = str(d.get("tag_name") or "")
        assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", []) if "browser_download_url" in a}
        return {"version": tag.lstrip("vV"), "tag": tag, "page": d.get("html_url") or f"{WEB}/releases/tag/{tag}",
                "assets": assets}
    except Exception as e:
        print("update: api.github.com:", e, flush=True)
    r = _get(f"{WEB}/releases/latest", follow=False)
    loc = r.headers.get("Location", "")
    if "/releases/tag/" not in loc:
        raise OSError(f"github.com: HTTP {getattr(r, 'status', getattr(r, 'code', '?'))}")
    tag = loc.rsplit("/", 1)[-1]
    v = tag.lstrip("vV")
    base = f"{WEB}/releases/download/{tag}/"
    names = [f"Fling-Setup-{v}.exe", "SHA256SUMS.txt", "SHA256SUMS.txt.minisig"]
    return {"version": v, "tag": tag, "page": f"{WEB}/releases/tag/{tag}", "assets": {n: base + n for n in names}}


def _download(url: str, out: str, tries=6) -> None:
    """With retries and resume (Range), as on Android: a dropped connection carries on.
    GitHub's file storage drops connections in bursts on some networks: back off up to ~45 s."""
    last = None
    for attempt in range(1, tries + 1):
        have = os.path.getsize(out) if os.path.exists(out) else 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Fling-desktop", "Accept": "application/octet-stream",
                                                       **({"Range": f"bytes={have}-"} if have else {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                mode = "ab" if have and r.status == 206 else "wb"
                with open(out, mode) as f:
                    while True:
                        b = r.read(256 * 1024)
                        if not b:
                            break
                        f.write(b)
            return
        except urllib.error.HTTPError as e:
            if e.code == 416:                    # already complete
                return
            last = e
        except Exception as e:
            last = e
        print(f"update: download {os.path.basename(out)} try {attempt}: {last}", flush=True)
        if attempt < tries:
            time.sleep(min(15, 3 * attempt))
    raise OSError(f"{os.path.basename(out)}: {last}")


def _guard() -> str | None:
    if P.FROZEN:
        import sys
        exe = os.path.join(os.path.dirname(sys.executable), P.GUARD_EXE)
    else:
        exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "guard", "target", "release", P.GUARD_EXE)
    return exe if os.path.isfile(exe) else None


def fetch_verified(rel: dict) -> str:
    """Downloads the setup and the signed checksum list; returns the setup's path only when the
    list is signed by the Fling release key for this version and the setup matches it."""
    v = rel["version"]
    setup_name = f"Fling-Setup-{v}.exe"
    urls = rel["assets"]
    for n in (setup_name, "SHA256SUMS.txt", "SHA256SUMS.txt.minisig"):
        if n not in urls:
            raise OSError(f"the release has no {n}")
    os.makedirs(DIR, exist_ok=True)
    for f in os.listdir(DIR):                    # an older download: not needed any more
        if not f.startswith(f"Fling-Setup-{v}") and f != "SHA256SUMS.txt" and f != "SHA256SUMS.txt.minisig":
            try:
                os.remove(os.path.join(DIR, f))
            except OSError:
                pass
    sums, sig, setup = (os.path.join(DIR, n) for n in ("SHA256SUMS.txt", "SHA256SUMS.txt.minisig", setup_name))
    for p in (sums, sig):                         # small: always fresh
        if os.path.exists(p):
            os.remove(p)
    _download(urls["SHA256SUMS.txt"], sums)
    _download(urls["SHA256SUMS.txt.minisig"], sig)
    guard = _guard()
    if not guard:
        raise OSError("fling-guard is missing: can't check the signature")
    r = subprocess.run([guard, "verify", sums, sig], capture_output=True, text=True, timeout=30, **P.NOWIN)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or f"version={v}" not in out:
        raise OSError("the release signature doesn't check out: " + out.strip()[-200:])
    want = None
    for line in open(sums, encoding="utf-8"):
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == setup_name:
            want = parts[0].lower()
    if not want:
        raise OSError(f"{setup_name} isn't in the signed list")
    _download(urls[setup_name], setup)
    h = hashlib.sha256()
    with open(setup, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    if h.hexdigest() != want:
        os.remove(setup)
        raise OSError(f"{setup_name}: the checksum doesn't match the signed list (download damaged or replaced)")
    print(f"update: {setup_name} downloaded and verified", flush=True)
    return setup


class Updater(QObject):
    """Owned by the tray. `found(rel)`: a newer version (Settings shows it)."""
    found = pyqtSignal(object)
    status = pyqtSignal(str)            # for Settings: "checking…", errors

    def __init__(self, F, state, notify, quit_app):
        """F: the running fling_app module (VERSION, tr, save_conf) — passed in, never imported:
        it runs as __main__, and importing it again would load a second copy."""
        super().__init__()
        self.F, self.state, self.notify, self.quit_app = F, state, notify, quit_app
        self.rel = None                 # the newest release seen, if newer
        self.ready = None               # a verified setup waiting for Fling to be idle
        self._busy = False
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._maybe)
        self._idle = QTimer(self)
        self._idle.timeout.connect(self._install_if_idle)

    @property
    def conf(self):
        return self.state.conf

    def can_install(self) -> bool:
        return P.IS_WIN and P.FROZEN and _guard() is not None

    def start(self):
        QTimer.singleShot(90_000, self._maybe)
        self._tick.start(3 * 3600 * 1000)

    def _maybe(self):
        if self.conf.get("upd_check", True) and time.time() - float(self.conf.get("upd_checked", 0)) > CHECK_EVERY:
            self.check(manual=False)

    def check(self, manual=True):
        if self._busy:
            return
        self._busy = True
        self.status.emit("checking")
        call = self.state.bridge.call.emit

        def work():
            try:
                rel, err = latest(), None
            except Exception as e:
                rel, err = None, str(e)
            call(lambda: self._checked(rel, err, manual))
        threading.Thread(target=work, daemon=True, name="fling-update-check").start()

    def _checked(self, rel, err, manual):
        self._busy = False
        if err or not rel:
            print("update: check failed:", err, flush=True)
            self.status.emit("error:" + (err or "?"))
            return
        self.conf["upd_checked"] = time.time()
        self._save()
        F = self.F
        newer = ver(rel["version"]) > ver(F.VERSION)
        print(f"update: latest {rel['version']}, have {F.VERSION}", flush=True)
        if not newer:
            self.rel = None
            self.status.emit("latest")
            return
        self.rel = rel
        self.found.emit(rel)
        self.status.emit("found")
        if not manual and rel["version"] == self.conf.get("upd_skip"):
            return                              # skipped: Settings still shows it, nothing else
        if self.can_install() and self.conf.get("upd_auto", True) and not manual:
            self.download(rel)
        elif not manual:
            self.notify(F.tr("Доступна версия Fling %s — Настройки → О программе.") % rel["version"])

    def skip(self):
        if self.rel:
            self.conf["upd_skip"] = self.rel["version"]
            self._save()
            self.ready = None
            self._idle.stop()

    def download(self, rel=None, now=False):
        """Download + verify in the background; then install when idle (now=True: at once)."""
        rel = rel or self.rel
        if not rel or self._busy:
            return
        self._busy = True
        self.status.emit("downloading")
        call = self.state.bridge.call.emit

        def work():
            try:
                path, err = fetch_verified(rel), None
            except Exception as e:
                path, err = None, str(e)
            call(lambda: self._downloaded(rel, path, err, now))
        threading.Thread(target=work, daemon=True, name="fling-update-download").start()

    def _downloaded(self, rel, path, err, now):
        self._busy = False
        F = self.F
        if err:
            print("update: download failed:", err, flush=True)
            self.status.emit("error:" + err)
            self.notify(F.tr("Обновление Fling не скачалось: ") + err)
            return
        self.ready = (rel["version"], path)
        self.status.emit("ready")
        if now:
            self.install()
        else:
            self._idle.start(60_000)
            self._install_if_idle()

    def _busy_with_tv(self) -> bool:
        s = self.state
        try:
            if s.panel_visible():
                return True
        except Exception:
            pass
        try:
            if s.screen.running():
                return True                     # LIVE / the TV screen window
        except Exception:
            pass
        try:
            if getattr(s.sv, "mirroring", False):
                return True
        except Exception:
            pass
        return False

    def _install_if_idle(self):
        if not self.ready or self._busy_with_tv():
            return
        self.install()

    def install(self):
        if not self.ready:
            return
        F = self.F
        v, path = self.ready
        self._idle.stop()
        print(f"update: installing {v}", flush=True)
        self.notify(F.tr("Fling обновляется до %s и сейчас перезапустится.") % v)
        # Before the setup: no new background work, the TV remote session closed politely
        # (a session the TV still holds open makes the new Fling's first connect fail),
        # and every helper Fling started stopped — `Fling.exe --catt scan` children used
        # to crash with "OSError: [Errno 22]" writing to the pipe of the closed Fling.
        P.UPDATING = True
        pause = getattr(self.state, "pause_polling", None)
        if pause:
            pause()
        try:
            self.state.atv.close()
        except Exception as e:
            print("update: remote close:", e, flush=True)
        print(f"update: stopped {P.kill_helpers()} helper(s)", flush=True)
        try:
            # per-user setup: no UAC; it closes this Fling and, with /relaunch=1, starts the new one
            setup = subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=1"],
                                     close_fds=True, creationflags=0x00000008 | 0x00000200)   # detached
        except OSError as e:
            P.UPDATING = False
            if pause:
                getattr(self.state, "resume_polling", lambda: None)()
            self.status.emit("error:" + str(e))
            return

        def quit_now():
            P.kill_helpers(keep=(setup.pid,))           # anything a worker thread started meanwhile
            self.quit_app()
        QTimer.singleShot(1500, quit_now)

    def _save(self):
        try:
            F = self.F
            F.save_conf(self.conf)
        except Exception as e:
            print("update: save:", e, flush=True)
