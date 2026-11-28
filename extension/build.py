#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Build the Fling browser extension for every browser from one source tree.

    python extension/build.py            → extension/dist/{chrome,firefox,safari}/ + .zip files

* chrome  — Chrome, Edge, Brave, Opera, Vivaldi, Yandex (Manifest V3, service worker)
* firefox — Firefox 128+ (Manifest V3, event page, gecko id)
* safari  — Safari 17+ web-extension folder; turn it into an app on a Mac with
            extension/safari/build-safari.sh (needs Xcode)

Standard library only.
"""
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
DIST = HERE / "dist"
VERSION = "0.6.1"

BASE = {
    "manifest_version": 3,
    "name": "__MSG_extName__",
    "short_name": "Fling",
    "description": "__MSG_extDesc__",
    "version": VERSION,
    "default_locale": "en",
    "icons": {str(n): f"icons/fling-{n}.png" for n in (16, 32, 48, 96, 128)},
    "action": {
        "default_title": "Fling",
        "default_popup": "popup.html",
        "default_icon": {str(n): f"icons/fling-{n}.png" for n in (16, 19, 32, 38)},
    },
    "options_ui": {"page": "options.html", "open_in_tab": True},
    # tabs: read the active tab's address for the app; scripting + activeTab: read the
    # page's player (resume position) only when you cast; contextMenus: the right-click
    # "Cast…" items; storage: settings and recent casts; alarms: heartbeat to the app.
    "permissions": ["tabs", "activeTab", "scripting", "storage", "contextMenus", "alarms"],
    # the Fling desktop app on this computer — nothing else
    "host_permissions": ["http://127.0.0.1/*"],
    # Only "cast-tab" gets a default key (fewer clashes with sites and other extensions);
    # the rest are assigned on the browser's shortcuts page, which the options page opens.
    "commands": {
        "cast-tab": {
            "suggested_key": {"default": "Alt+Shift+C", "mac": "Alt+Shift+C"},
            "description": "__MSG_cmdCastTab__",
        },
        "cast-tab-now": {"description": "__MSG_cmdCastNow__"},
        "play-pause": {"description": "__MSG_cmdPlayPause__"},
        "seek-back": {"description": "__MSG_cmdSeekBack__"},
        "seek-forward": {"description": "__MSG_cmdSeekForward__"},
        "volume-up": {"description": "__MSG_cmdVolUp__"},
        "volume-down": {"description": "__MSG_cmdVolDown__"},
    },
}


def manifest(target: str) -> dict:
    m = json.loads(json.dumps(BASE))
    if target == "chrome":
        m["background"] = {"service_worker": "background.js"}
        m["minimum_chrome_version"] = "116"
    elif target == "firefox":
        m["background"] = {"scripts": ["background.js"]}
        m["browser_specific_settings"] = {
            "gecko": {
                "id": "fling@orfixe.github.io",
                "strict_min_version": "128.0",
                "data_collection_permissions": {"required": ["none"]},
            }
        }
        m["options_ui"] = {"page": "options.html", "open_in_tab": True}
    elif target == "safari":
        m["background"] = {"scripts": ["background.js"], "persistent": False}
        m["browser_specific_settings"] = {"safari": {"strict_min_version": "17.0"}}
    return m


def build(target: str) -> Path:
    out = DIST / target
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SRC, out)
    (out / "manifest.json").write_text(json.dumps(manifest(target), indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    z = DIST / f"fling-{target}-{VERSION}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(out).as_posix())
    return z


def main(argv: list[str]) -> int:
    targets = argv or ["chrome", "firefox", "safari"]
    DIST.mkdir(exist_ok=True)
    for t in targets:
        if t not in ("chrome", "firefox", "safari"):
            print(f"unknown target {t}", file=sys.stderr)
            return 2
        print(f"{t:8} -> {build(t).relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
