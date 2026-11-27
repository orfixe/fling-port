# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""A report for the developer: one zip with what's needed to understand a problem.

    fling-report-<date>.zip  (in Downloads)
      report.txt   version, build, system, Python/Qt, screens, settings, TVs
      app.log      the log (and app.log.old), the newest 3 MB

Settings → About → "Report for the developer", the tray menu, or `fling report`.
The user attaches the zip to a GitHub issue (or sends it to whoever asked for it).

Nothing leaves the computer by itself, and before the zip is written everything is cleaned:
passwords, tokens, keys and cookies (by name and by shape: GitHub tokens, Bearer, token=…),
e-mail addresses, MAC addresses (all but the last two bytes), public IP addresses (local
ones stay: they explain network problems) and the user name in paths."""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import sys
import time
import zipfile

import fling_platform as P

_SECRET_NAME = re.compile(r"(token|secret|passw|pass_?word|key|cookie|auth|pat\b|credential)", re.I)
_RULES = [
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "<token>"),
    (re.compile(r"(?i)\b(bearer|token|basic)(\s+|=|:\s*)[A-Za-z0-9._~+/=-]{8,}"), r"\1\2<hidden>"),
    (re.compile(r"(?i)([?&](?:token|key|sig|signature|auth|access_token|code)=)[^&\s\"']+"), r"\1<hidden>"),
    (re.compile(r"(?i)(\"(?:token|tokens|password|secret|key|auth|cookie)\"\s*:\s*)\"[^\"]*\""), r'\1"<hidden>"'),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"\b((?:[0-9A-Fa-f]{2}[:-]){4})([0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2})\b"), r"xx:xx:xx:xx:\2"),
]
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _mask_ip(m: re.Match) -> str:
    s = m.group(0)
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return s
    # home / hotel networks, Tailscale (100.64/10), loopback, link-local and the
    # fake-IP ranges of VPN clients (198.18/15) explain problems; a public address
    # only identifies the user
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10") \
            or ip in ipaddress.ip_network("198.18.0.0/15") or ip.is_unspecified or ip.is_multicast:
        return s
    return "<public-ip>"


def clean(text: str) -> str:
    """Secrets, e-mails, MACs, public IPs and the user's name out of `text`."""
    for rx, rep in _RULES:
        text = rx.sub(rep, text)
    text = _IP.sub(_mask_ip, text)
    home = os.path.expanduser("~")
    user = os.path.basename(home.rstrip("\\/"))
    if home and len(home) > 3:
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    if len(user) >= 3:
        text = re.sub(r"(?i)(\\Users\\|/Users/|/home/)" + re.escape(user) + r"\b", r"\1<user>", text)
    return text


def _clean_obj(o):
    """Settings: values under secret-looking names are dropped, the rest is cleaned text."""
    if isinstance(o, dict):
        return {k: ("<hidden>" if _SECRET_NAME.search(str(k)) else _clean_obj(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean_obj(v) for v in o]
    if isinstance(o, str):
        return clean(o)
    return o


def _tail(path: str, limit: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
        text = data.decode("utf-8", "replace")
        if size > limit:
            text = "[… earlier lines cut …]\n" + text.split("\n", 1)[-1]
        return text
    except OSError as e:
        return f"[{os.path.basename(path)}: {e}]\n"


def _section(title: str, body: str) -> str:
    return f"\n== {title} {'=' * max(3, 60 - len(title))}\n{body.rstrip()}\n"


def _system(F) -> str:
    lines = [
        f"Fling {getattr(F, 'VERSION', '?')}  ({P.OS_NAME})",
        "build: " + json.dumps(P.build_info(), ensure_ascii=False),
        f"frozen: {P.FROZEN}",
        f"os: {platform.platform()}  ({platform.machine()})",
        f"python: {sys.version.split()[0]}",
        f"time: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
    ]
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR, QLocale
        lines.append(f"qt: {QT_VERSION_STR}  pyqt: {PYQT_VERSION_STR}  locale: {QLocale.system().name()}")
        from PyQt6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            lines.append(f"platform plugin: {app.platformName()}")
            for s in app.screens():
                g = s.geometry()
                lines.append(f"screen {s.name()!r}: {g.width()}x{g.height()} at {g.x()},{g.y()}  "
                             f"scale {s.devicePixelRatio():g}  {s.refreshRate():.0f} Hz")
    except Exception as e:
        lines.append(f"qt: {e!r}")
    return "\n".join(lines)


def _state(state) -> str:
    if state is None:
        return "(Fling not running)"
    out = []
    for name in ("device", "panel", "remote_ready", "tv_status"):
        try:
            v = getattr(state, name)
            out.append(f"{name}: {v() if callable(v) and name == 'remote_ready' else v}")
        except Exception as e:
            out.append(f"{name}: <{e!r}>")
    try:
        out.append("devices: " + json.dumps(getattr(state, "devices", []), ensure_ascii=False))
        out.append("online: " + json.dumps(getattr(state, "online", {}), ensure_ascii=False, default=str))
    except Exception as e:
        out.append(f"devices: <{e!r}>")
    return "\n".join(out)


def make(F=None, state=None) -> str:
    """Writes the report zip; returns its path. F: the fling_app module (version, conf)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(P.downloads_dir(), f"fling-report-{stamp}.zip")
    conf = {}
    try:
        with open(getattr(F, "CONF", P.CONF), encoding="utf-8") as f:
            conf = json.load(f)
    except Exception as e:
        conf = {"<error>": repr(e)}
    sys.stdout.flush() if sys.stdout else None
    report = "Fling report — attach this file to an issue: https://github.com/orfixe/fling-port/issues\n"
    report += _section("system", _system(F))
    report += _section("now", _state(state))
    report += _section("settings (fling.json, secrets removed)",
                       json.dumps(_clean_obj(conf), ensure_ascii=False, indent=1))
    log = ""
    if os.path.exists(P.LOG + ".old"):
        log += _tail(P.LOG + ".old", 1_000_000)
    log += _tail(P.LOG, 2_000_000)
    bt = os.path.join(P.CACHE, "bt.log")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", clean(report))
        z.writestr("app.log", clean(log))
        if os.path.exists(bt):
            z.writestr("bt.log", clean(_tail(bt, 300_000)))
    print("report:", path, flush=True)
    return path


ISSUES = "https://github.com/orfixe/fling-port/issues/new"


def reveal(path: str) -> None:
    """Show the file in Explorer / Finder / the file manager, selected where possible."""
    try:
        if P.IS_WIN:
            P.popen(["explorer", "/select,", os.path.normpath(path)])
        elif P.IS_MAC:
            P.popen(["open", "-R", path])
        else:
            P.open_path(os.path.dirname(path))
    except Exception as e:
        print("reveal:", e, flush=True)
