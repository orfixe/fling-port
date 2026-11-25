# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Remote access over Tailscale: the TV at home, Fling anywhere.

Fling embeds no VPN code. It relies on the official Tailscale app on the computer and on
the TV, and only *reads* the computer's Tailscale state (`tailscale status --json`,
`tailscale ping`); it never runs `up`, `down`, `set` or `logout`, never changes the
tailnet's settings and never opens anything to the internet.

What works over the tailnet: everything that connects to the TV by address — the Android
TV Remote (ports 6466/6467), ADB (5555: apps, files, screenshots, the live screen) and
Cast to a known address (8009) when the TV's Cast service answers there. What doesn't:
discovery (mDNS stays on the home network), so tailnet TVs are added by address.

Pieces:
  * find_cli / read_status / parse_status: the computer's Tailscale client and its peers;
  * probe_tv: what a tailnet address offers (Cast, remote, ADB), used by State.add_device_ip;
  * KeepAlive: while a tailnet TV is selected, pings it, reconnects the remote and ADB when
    the path comes back (with backoff) and gives the footer its "via Tailscale · 38 ms";
  * TailnetSection / SecurityNote / settings_page: the UI in the TV chooser and Settings.

fling_app binds itself as F right after the import (like fling_media)."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

import fling_platform as P

F = None          # fling_app, set by fling_app right after the import

DOWNLOAD = "https://tailscale.com/download"
DOCS = {"ru": "https://github.com/orfixe/fling-port/blob/main/docs/tailscale.ru.md",
        "en": "https://github.com/orfixe/fling-port/blob/main/docs/tailscale.md"}
TV_PORTS = (8009, 6466, 5555)          # Cast, Android TV Remote, ADB: any one is a TV worth adding

PHRASES = [
    # the TV chooser / Settings section
    ("Через Tailscale", "Via Tailscale"),
    ("Удалённый доступ", "Remote access"),
    ("Удалённый доступ (Tailscale)", "Remote access (Tailscale)"),
    ("Проверяю Tailscale…", "Checking Tailscale…"),
    ("Tailscale не установлен на этом компьютере.", "Tailscale isn't installed on this computer."),
    ("С Tailscale пульт и экран ТВ работают вдали от дома. Установите его на компьютер и на ТВ "
     "и войдите в один и тот же аккаунт.",
     "With Tailscale, the remote and the TV's screen work away from home. Install it on this "
     "computer and on the TV, and sign in to the same account on both."),
    ("Скачать Tailscale", "Download Tailscale"),
    ("Как это работает", "How it works"),
    ("Tailscale выключен или не выполнен вход. Включите его в приложении Tailscale.",
     "Tailscale is off or you aren't signed in. Turn it on in the Tailscale app."),
    ("Не удалось прочитать состояние Tailscale.", "Couldn't read the Tailscale status."),
    ("В сети Tailscale нет телевизоров.", "There are no TVs on your tailnet."),
    ("Установите Tailscale на ТВ и войдите в тот же аккаунт, что и на компьютере.",
     "Install Tailscale on the TV and sign in to the same account as on this computer."),
    ("Установить Tailscale на ТВ…", "Install Tailscale on the TV…"),
    ("Показать все устройства ({n})", "Show all devices ({n})"),
    ("Только телевизоры", "TVs only"),
    ("Обновить", "Refresh"),
    ("в сети", "online"), ("не в сети", "offline"),
    ("Добавить", "Add"), ("добавлен", "added"), ("выбран", "selected"),
    ("ТВ сейчас не в сети Tailscale", "The TV isn't on the tailnet right now"),
    ("Добавить этот ТВ и управлять им через Tailscale",
     "Add this TV and control it over Tailscale"),
    ("Вдали от дома работают пульт, приложения, набор текста, ADB и экран ТВ, а трансляция — "
     "если ТВ отвечает на порт Cast. В поле выше подойдёт и адрес Tailscale: 100.x.y.z или имя MagicDNS.",
     "Away from home, the remote, apps, typing, ADB and the TV's screen work, and casting does "
     "if the TV answers on the Cast port. The field above also takes a Tailscale address: "
     "100.x.y.z or a MagicDNS name."),
    ("Этот компьютер: ", "This computer: "),
    # security
    ("ADB через Tailscale — полный доступ к ТВ", "ADB over Tailscale is full access to the TV"),
    ("Разрешите в правилах доступа (ACL) Tailscale подключаться к портам ТВ 5555, 6466 и 6467 "
     "только своим устройствам. Fling ничего не открывает в интернет и не пробрасывает порты.",
     "In your Tailscale access rules (ACLs), let only your own devices reach the TV's ports "
     "5555, 6466 and 6467. Fling opens nothing to the internet and forwards no ports."),
    ("Скопировать пример ACL", "Copy an example ACL"),
    ("Скопировано", "Copied"),
    ("Понятно", "Got it"),
    ("Безопасность", "Security"),
    ("Пометьте ТВ тегом tag:tv в консоли Tailscale (Machines → ТВ → Edit ACL tags) и вставьте "
     "правило ниже. Если в ваших правилах есть «всё всем» (src \"*\" → dst \"*:*\"), уберите его, "
     "иначе ТВ видят все устройства сети.",
     "Tag the TV as tag:tv in the Tailscale admin console (Machines → the TV → Edit ACL tags) "
     "and paste the rule below. If your rules allow everything to everyone (src \"*\" → dst \"*:*\"), "
     "remove that rule, or every device on the tailnet can reach the TV."),
    # what works
    ("Что работает через Tailscale", "What works over Tailscale"),
    ("Пульт, громкость, приложения и набор текста", "Remote, volume, apps and typing"),
    ("ADB: установка APK, файлы, снимки экрана, консоль", "ADB: installing APKs, files, screenshots, the console"),
    ("Экран ТВ вживую — начинается с качества «Экономно»",
     "The TV's live screen, starting at the “Saver” quality"),
    ("Ссылка на ТВ для YouTube — она работает через интернет", "The YouTube TV link, which works over the internet"),
    ("Трансляция ссылок — если ТВ отвечает на порт Cast (8009)",
     "Casting links, if the TV answers on the Cast port (8009)"),
    ("Файлы с компьютера — только если брандмауэр пускает ТВ к компьютеру",
     "Files from this computer, only if the firewall lets the TV reach it"),
    ("Поиск ТВ в сети — добавляйте ТВ по адресу Tailscale",
     "Finding TVs on the network: add the TV by its Tailscale address"),
    ("Телевизоры в сети Tailscale", "TVs on your tailnet"),
    ("Fling только читает состояние Tailscale и никогда не меняет его настройки.",
     "Fling only reads the Tailscale status and never changes its settings."),
    # footer / keep-alive
    ("через Tailscale", "via Tailscale"),
    ("нет связи", "no connection"),
    ("Tailscale: проверяю связь с ТВ…", "Tailscale: checking the connection to the TV…"),
    ("Tailscale: прямое соединение с ТВ.", "Tailscale: a direct connection to the TV."),
    ("Tailscale: через ретранслятор {relay} — задержка выше, чем напрямую.",
     "Tailscale: through the {relay} relay, so the delay is higher than with a direct connection."),
    ("ТВ не отвечает через Tailscale. Fling переподключится сам, когда связь вернётся.",
     "The TV isn't answering over Tailscale. Fling reconnects by itself once the connection is back."),
    ("Через Tailscale — качество «Экономно». Другое можно выбрать справа вверху.",
     "Via Tailscale, so the quality is “Saver”. Choose another at the top right."),
    # adding
    ("Проверяю {ip} через Tailscale…", "Checking {ip} over Tailscale…"),
    ("По адресу {ip} не отвечают ни Cast, ни пульт, ни ADB. Включён ли Tailscale на ТВ?",
     "Neither Cast, the remote nor ADB answers at {ip}. Is Tailscale running on the TV?"),
    ("Не нашёл «{name}» ни в сети Tailscale, ни в DNS.", "Couldn't find “{name}” on the tailnet or in DNS."),
    ("Этот ТВ добавлен через Tailscale, и порт Cast на нём не отвечает — трансляция недоступна. "
     "Пульт, приложения и ADB работают.",
     "This TV was added via Tailscale and its Cast port doesn't answer, so casting isn't available. "
     "The remote, apps and ADB work."),
    ("Установка Tailscale на ТВ появится в каталоге приложений. Пока установите его "
     "из Google Play на ТВ.",
     "Installing Tailscale on the TV is coming to the app catalogue. For now, install it "
     "from Google Play on the TV."),
]


def _tr(s: str) -> str:
    return F.tr(s) if F else s


def _css(c: QColor) -> str:
    return F.Theme.css(c)


# ---------------------------------------------------------------- addresses

def _ipv4(s: str) -> bool:
    parts = (s or "").split(".")
    return len(parts) == 4 and all(p.isascii() and p.isdigit() and int(p) <= 255 for p in parts)


def is_tailnet_ip(ip: str) -> bool:
    """100.64.0.0/10: the addresses Tailscale gives its devices (CGNAT range)."""
    if not _ipv4(ip):
        return False
    a, b = (int(x) for x in ip.split(".")[:2])
    return a == 100 and 64 <= b <= 127


def is_tailscale(d: dict | None) -> bool:
    """A TV reached over the tailnet: added that way, or simply sitting on a 100.64/10 address."""
    return bool(d) and (d.get("via") == "tailscale" or is_tailnet_ip(d.get("ip", "")))


_HOST_RX = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.?")


def wants(text: str) -> bool:
    """Should the TV chooser hand this address to Tailscale? A tailnet IP or a host name
    (MagicDNS: `tv` or `tv.tailnet.ts.net`); a plain LAN IP keeps the usual path."""
    t = (text or "").strip().lower()
    if _ipv4(t):
        return is_tailnet_ip(t)
    return bool(t) and not t[0].isdigit() and bool(_HOST_RX.fullmatch(t))


# ---------------------------------------------------------------- the Tailscale client (read-only)

def find_cli() -> str | None:
    """The `tailscale` command line client, or None. Windows: PATH or Program Files;
    Linux: PATH or the usual bin folders; macOS: inside Tailscale.app."""
    exe = shutil.which("tailscale")
    if exe:
        return exe
    if P.IS_WIN:
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                p = os.path.join(base, "Tailscale", "tailscale.exe")
                if os.path.isfile(p):
                    return p
        return None
    for p in ("/usr/bin/tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def read_status(cli: str | None = None, timeout: float = 6) -> dict:
    """`tailscale status --json`, parsed. Raises RuntimeError when it can't be read."""
    cli = cli or find_cli()
    if not cli:
        raise FileNotFoundError("tailscale")
    r = P.run([cli, "status", "--json"], capture_output=True, text=True, timeout=timeout,
              encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:200] or "tailscale status") from None


# Host names that say "TV" (Android TV / Google TV boxes and sets name themselves after the model).
_TV_HINTS = ("tv", "chromecast", "bravia", "shield", "mibox", "mi-box", "mi box", "streamer",
             "philips", "tcl", "hisense", "nvidia", "onn", "sberbox", "yandex", "kivi", "xiaomi-tv",
             "box")
_NOT_TV = ("phone", "pixel", "galaxy", "sm-", "iphone", "ipad", "tablet", "redmi", "oneplus",
           "huawei", "honor", "moto", "nokia", "watch", "pad")


def tv_score(host: str, os_name: str) -> int:
    """2: looks like a TV; 1: an Android device that may be one; 0: something else."""
    h, o = (host or "").lower(), (os_name or "").lower()
    if "android" not in o:
        return 0
    if any(k in h for k in _NOT_TV):
        return 1 if any(k in h for k in ("tv", "chromecast")) else 0
    if any(k in h for k in _TV_HINTS):
        return 2
    return 1


def _peer(p: dict) -> dict:
    ips = p.get("TailscaleIPs") or []
    v4 = next((x for x in ips if _ipv4(x)), "")
    dns = (p.get("DNSName") or "").rstrip(".")
    host = p.get("HostName") or dns.split(".")[0] or v4
    cur = p.get("CurAddr") or ""
    return {"id": p.get("ID") or p.get("PublicKey") or v4, "host": host, "dns": dns,
            "short": dns.split(".")[0] if dns else "", "ip": v4, "os": p.get("OS") or "",
            "online": bool(p.get("Online")), "relay": p.get("Relay") or "", "direct": bool(cur),
            "tv": tv_score(host, p.get("OS") or "")}


def parse_status(js: dict) -> dict:
    """The parts of `tailscale status --json` Fling uses."""
    me = js.get("Self") or {}
    users = js.get("User") or {}
    login = ""
    uid = me.get("UserID")
    if uid is not None:
        u = users.get(str(uid)) or {}
        login = u.get("LoginName") or ""
    peers = [_peer(p) for p in (js.get("Peer") or {}).values()]
    peers = [p for p in peers if p["ip"]]
    peers.sort(key=lambda p: (-p["tv"], not p["online"], p["host"].lower()))
    state = js.get("BackendState") or ""
    return {"state": state, "running": state == "Running", "self": _peer(me) if me else None,
            "peers": peers, "suffix": js.get("MagicDNSSuffix") or "", "login": login}


def tailnet(cli: str | None = None) -> dict:
    """{"cli": path|None, "error": str|None, …parse_status()}. Runs a process: call off the UI thread."""
    cli = cli or find_cli()
    if not cli:
        return {"cli": None, "error": None, "running": False, "peers": [], "state": "", "self": None,
                "login": "", "suffix": ""}
    try:
        out = parse_status(read_status(cli))
    except Exception as e:
        return {"cli": cli, "error": str(e), "running": False, "peers": [], "state": "", "self": None,
                "login": "", "suffix": ""}
    out.update(cli=cli, error=None)
    return out


_PING_RX = re.compile(r"pong from .*? via (.+?) in ([\d.]+)\s*ms")


def ping(ip: str, cli: str | None = None) -> dict | None:
    """One round trip to a tailnet address: {"ms", "direct", "relay"} or None.
    `tailscale ping` (read-only; the disco ping also works where ACLs drop ICMP)
    tells a direct path from a relayed (DERP) one; without the CLI an ICMP ping."""
    cli = cli or find_cli()
    if cli:
        try:
            r = P.run([cli, "ping", "-c", "1", "--timeout", "3s", ip], capture_output=True, text=True,
                      timeout=8, encoding="utf-8", errors="replace")
            m = _PING_RX.search(r.stdout or "")
            if m:
                via = m[1].strip()
                relay = re.match(r"DERP\((\w+)\)", via)
                return {"ms": float(m[2]), "direct": not via.startswith(("DERP", "peer-relay")),
                        "relay": relay[1] if relay else ""}
            if r.returncode == 0:
                return None
        except Exception:
            pass
    ms = P.ping_ms(ip, 1500)
    return {"ms": ms, "direct": None, "relay": ""} if ms is not None else None


def _port_open(ip: str, port: int, timeout: float = 2.5) -> bool:
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.settimeout(timeout)
    try:
        c.connect((ip, port))
        return True
    except OSError:
        return False
    finally:
        c.close()


def probe_tv(ip: str, name: str | None = None) -> dict:
    """What a tailnet address offers. Any of Cast / remote / ADB answering is enough: the
    remote and ADB connect by address, and Cast may or may not listen on the tailnet.
    Raises RuntimeError (already translated) when nothing answers. Blocking."""
    with ThreadPoolExecutor(len(TV_PORTS)) as ex:
        res = dict(zip(TV_PORTS, ex.map(lambda p: _port_open(ip, p), TV_PORTS)))
    if not any(res.values()):
        raise RuntimeError(_tr("По адресу {ip} не отвечают ни Cast, ни пульт, ни ADB. "
                               "Включён ли Tailscale на ТВ?").replace("{ip}", ip))
    title = name or ip
    if res[8009]:
        try:   # the TV's own name from its DIAL device description
            import urllib.request
            with urllib.request.urlopen(f"http://{ip}:8008/ssdp/device-desc.xml", timeout=4) as r:
                xml = r.read().decode("utf-8", "ignore")
            m = re.search(r"<friendlyName>([^<]+)</friendlyName>", xml)
            if m:
                title = m[1].strip()
        except Exception:
            pass
    return {"ip": ip, "name": title, "model": "", "manual": True, "via": "tailscale",
            "cast": bool(res[8009])}


def resolve(text: str, net: dict | None = None) -> tuple[str, str | None]:
    """A tailnet IP or MagicDNS name → (IPv4, peer host name or None). Blocking."""
    t = text.strip().rstrip(".")
    if _ipv4(t):
        peer = next((p for p in (net or {}).get("peers", []) if p["ip"] == t), None)
        return t, peer["host"] if peer else None
    low = t.lower()
    for p in (net or {}).get("peers", []):
        if low in (p["dns"].lower(), p["short"].lower(), p["host"].lower()):
            return p["ip"], p["host"]
    try:
        ip = socket.getaddrinfo(t, None, socket.AF_INET)[0][4][0]
    except (OSError, IndexError):
        raise RuntimeError(_tr("Не нашёл «{name}» ни в сети Tailscale, ни в DNS.").replace("{name}", t)) from None
    return ip, None


def add(state, text: str, name: str | None = None, on_ip=None):
    """Add a TV by its tailnet address (IP or MagicDNS name); on_ip(ip) once it's resolved."""
    def work():
        net = tailnet() if not _ipv4(text.strip()) or name is None else None
        return resolve(text, net)

    def done(r, e):
        if e or not r:
            state._set(last_error=str(e) if e else None)
            return
        ip, host = r
        if on_ip:
            on_ip(ip)
        # a host name may point into the LAN too: then it's an ordinary TV added by IP
        state.add_device_ip(ip, via="tailscale" if is_tailnet_ip(ip) else None, name=name or host)
    state.bg(work, done)


def cast_blocked(d: dict | None) -> str | None:
    """The error to show instead of casting to a tailnet TV whose Cast port didn't answer."""
    if is_tailscale(d) and d.get("cast") is False:
        return ("Этот ТВ добавлен через Tailscale, и порт Cast на нём не отвечает — трансляция недоступна. "
                "Пульт, приложения и ADB работают.")
    return None


def prefers_saver(state) -> bool:
    """LIVE over the tailnet starts light: the path may be relayed or on mobile data."""
    return is_tailscale(getattr(state, "device", None))


def install_tailscale_on_tv(state=None) -> bool:
    """Install the official Tailscale app on the selected TV over ADB.

    The Catalog (fling_catalog) downloads the official APK, checks it and installs it over ADB;
    the progress shows on the Tools window's Catalog tab. Without that module it opens the Tools
    window and says how to do it by hand. Returns True when an installer was started."""
    import fling_tools
    try:
        import fling_catalog
    except ImportError:
        fling_catalog = None
    if fling_catalog is not None:
        def done(ok, message):                 # runs on a background thread: hop to the UI
            if state is not None:
                state.bridge.call.emit(lambda: state._set(last_error=None if ok else (message or None)))
        try:
            fling_catalog.install_on_tv("tailscale", state=state, on_done=done)
            fling_tools.open_window(tab="catalog")   # the Tailscale card shows the progress
            return True
        except Exception as e:
            if state is not None:
                state._set(last_error=str(e))
            return False
    if state is not None:
        state._set(last_error=_tr("Установка Tailscale на ТВ появится в каталоге приложений. "
                                  "Пока установите его из Google Play на ТВ."))
    try:
        fling_tools.open_window()
    except Exception:
        pass
    return False


def acl_example(login: str = "") -> str:
    who = login or "you@example.com"
    return ("{\n"
            '  "tagOwners": {"tag:tv": ["' + who + '"]},\n'
            '  "acls": [\n'
            "    // only your own devices reach the TV, and only the ports Fling uses\n"
            '    {"action": "accept", "src": ["' + who + '"],\n'
            '     "dst": ["tag:tv:5555,6466,6467,8008,8009"]}\n'
            "  ]\n"
            "}\n")


# ---------------------------------------------------------------- keep-alive ("the service")

class KeepAlive(QObject):
    """While a tailnet TV is selected: ping it now and then, and when the path comes back
    after a gap reconnect the remote and (if it was in use) ADB. Backoff while it's away.
    Nothing listens anywhere: this only keeps Fling's own outgoing connections alive."""
    ONLINE_EVERY = 20.0
    BACKOFF = (10.0, 20.0, 40.0, 80.0, 120.0)

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.ip = None
        self.result = None          # last ping: {"ms", "direct", "relay"} | None
        self.online = None          # None: not checked yet
        self.misses = 0
        self.adb_used = False
        self.busy = False
        self.cli = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)
        state.changed.connect(self.follow)

    def follow(self):
        s = self.state
        d = s.device
        ip = d["ip"] if is_tailscale(d) else None
        if ip != self.ip:
            self.ip, self.result, self.online, self.misses, self.adb_used = ip, None, None, 0, False
            self.timer.stop()
            if ip:
                self.timer.start(800)
        if ip and not self.adb_used:
            try:
                self.adb_used = bool(s.adb_ok())
            except Exception:
                pass

    def tick(self):
        ip = self.ip
        if not ip or self.busy:
            return
        self.busy = True
        adb_used = self.adb_used

        def work():
            if self.cli is None:
                self.cli = find_cli() or ""
            r = ping(ip, self.cli or None)
            adb = None
            if r and adb_used and P.adb_available():
                adb = self._keep_adb(ip)
            return r, adb

        def done(res, e):
            self.busy = False
            if ip != self.ip:
                return
            r, _adb = res if res else (None, None)
            before = (self.footer_text(), self.online)
            came_back = bool(r) and self.online is False
            self.result, self.online = r, bool(r)
            self.misses = 0 if r else self.misses + 1
            if came_back:                       # the tailnet path is back: wake the remote up
                print("tailscale: path back to", ip, flush=True)
                try:
                    self.state.atv.connect(ip)
                except Exception:
                    pass
            wait = self.ONLINE_EVERY if r else self.BACKOFF[min(self.misses - 1, len(self.BACKOFF) - 1)]
            self.timer.start(int(wait * 1000))
            if (self.footer_text(), self.online) != before:
                self.state.changed.emit()
        self.state.bg(work, done)

    @staticmethod
    def _keep_adb(ip: str) -> str | None:
        """ADB was working with this TV: check the link, reconnect quietly if it dropped.
        Never while the TV is asking "Allow debugging?" (each connect would ask again)."""
        serial = F._adb_serial(ip)
        try:
            st = P.run([F._ADB, "-s", serial, "get-state"], capture_output=True, text=True,
                       timeout=6).stdout.strip()
        except Exception:
            st = ""
        if st == "device":
            F._ADB_HOST[ip] = ("device", 0.0)
            return st
        cached = F._adb_cached(ip)
        if cached == "unauthorized":
            return cached
        if cached in ("closed", "offline"):
            F._ADB_HOST.pop(ip, None)          # the path just came back: the old verdict is stale
        return F.adb_state(ip)

    # --- what the footer shows
    def footer_text(self) -> str:
        if not self.ip:
            return ""
        t = _tr("через Tailscale")
        if self.online is False:
            return t + " · " + _tr("нет связи")
        if self.result and self.result.get("ms") is not None:
            unit = "ms" if getattr(F, "LANG", "ru") == "en" else "мс"
            return f"{t} · {self.result['ms']:.0f} {unit}"
        return t

    def footer_tip(self) -> str:
        if not self.ip:
            return ""
        if self.online is False:
            return _tr("ТВ не отвечает через Tailscale. Fling переподключится сам, когда связь вернётся.")
        r = self.result
        if not r:
            return _tr("Tailscale: проверяю связь с ТВ…")
        if r.get("relay"):
            return _tr("Tailscale: через ретранслятор {relay} — задержка выше, чем напрямую.").replace(
                "{relay}", r["relay"].upper())
        return _tr("Tailscale: прямое соединение с ТВ.")


# ---------------------------------------------------------------- UI pieces

def _dpr() -> float:
    s = QGuiApplication.primaryScreen()
    return s.devicePixelRatio() if s else 1.0


def _small_button(text: str, primary: bool = False, h: int = 26) -> QPushButton:
    T = F.T
    b = QPushButton()
    b.setText(text)
    b.setFont(F.font(11.5, QFont.Weight.DemiBold if primary else QFont.Weight.Normal))
    b.setFixedHeight(h)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setStyleSheet(
            f"QPushButton{{border:none;border-radius:{h // 2 - 5}px;padding:0 12px;color:white;"
            f"background:{_css(T.accent)}}}"
            f"QPushButton:hover{{background:{_css(T.accent.lighter(112))}}}"
            f"QPushButton:disabled{{background:{_css(T.fg_a(0.12))};color:{_css(T.fg_a(0.45))}}}")
    else:
        b.setStyleSheet(
            f"QPushButton{{border:1px solid {_css(T.fg_a(0.16))};border-radius:{h // 2 - 5}px;padding:0 11px;"
            f"color:{_css(T.fg)};background:{_css(T.fg_a(0.05))}}}"
            f"QPushButton:hover{{background:{_css(T.acc_a(0.18))}}}"
            f"QPushButton:disabled{{color:{_css(T.fg_a(0.4))}}}")
    return b


def _link(text: str, on) -> QPushButton:
    T = F.T
    b = QPushButton()
    b.setText(text)
    b.setFont(F.font(11.5))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFixedHeight(22)
    b.setStyleSheet(f"QPushButton{{border:none;padding:0 2px;text-align:left;background:transparent;"
                    f"color:{_css(T.accent)}}}QPushButton:hover{{text-decoration:underline}}")
    if isinstance(on, str):
        b.setToolTip(on)
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(on)))
    else:
        b.clicked.connect(on)
    return b


def docs_url() -> str:
    return DOCS.get(getattr(F, "LANG", "ru"), DOCS["en"])


class PeerRow(QFrame):
    """One tailnet device: online dot, name, address, and Add (or "added" / "selected")."""

    def __init__(self, peer: dict, added: bool, current: bool, on_add):
        super().__init__()
        T = F.T
        self.setObjectName("peer")
        self.setFixedHeight(46)
        self.setStyleSheet(f"QFrame#peer{{background:{_css(T.fg_a(0.04))};border-radius:9px}}")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(10)
        dot = QLabel()
        dot.setFixedSize(7, 7)
        dot.setToolTip(_tr("в сети" if peer["online"] else "не в сети"))
        dot.setStyleSheet(f"background:{_css(T.green if peer['online'] else T.fg_a(0.25))};border-radius:3px")
        h.addWidget(dot)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        name = F.label(peer["host"], 12.5, T.fg, QFont.Weight.DemiBold)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setToolTip(peer["dns"] or peer["host"])
        col.addWidget(name)
        bits = [peer["ip"]]
        if peer["short"] and peer["short"].lower() != peer["host"].lower():
            bits.append(peer["short"])
        bits.append(_tr("в сети" if peer["online"] else "не в сети"))
        sub = F.label(" · ".join(bits), 10.5, T.fg_a(0.55))
        sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(sub)
        h.addLayout(col, 1)
        if added or current:
            mark = F.label(_tr("выбран" if current else "добавлен"), 11, T.accent if current else T.fg_a(0.5))
            h.addWidget(mark)
        else:
            b = _small_button("Добавить", primary=True)
            b.setEnabled(peer["online"])
            b.setToolTip(_tr("Добавить этот ТВ и управлять им через Tailscale") if peer["online"]
                         else _tr("ТВ сейчас не в сети Tailscale"))
            b.clicked.connect(lambda: on_add(peer))
            h.addWidget(b)


class SecurityNote(QFrame):
    """The one-time warning: ADB on the tailnet is powerful; keep it to your own devices."""

    def __init__(self, state, dismissable: bool = True, on_dismiss=None):
        super().__init__()
        T = F.T
        self.state = state
        self.setObjectName("sec")
        tint = QColor(T.orange)
        tint.setAlphaF(0.12)
        self.setStyleSheet(f"QFrame#sec{{background:{_css(tint)};border-radius:10px}}")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        v.addWidget(F.label("ADB через Tailscale — полный доступ к ТВ", 12, T.fg, QFont.Weight.DemiBold))
        v.addWidget(F.label("Разрешите в правилах доступа (ACL) Tailscale подключаться к портам ТВ 5555, 6466 и 6467 "
                            "только своим устройствам. Fling ничего не открывает в интернет и не пробрасывает порты.",
                            11, T.fg_a(0.7), wrap=True))
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(8)
        self.copy = _small_button("Скопировать пример ACL")
        self.copy.clicked.connect(self._copy)
        row.addWidget(self.copy)
        row.addStretch(1)
        if dismissable:
            ok = _small_button("Понятно")
            ok.clicked.connect(lambda: (self._dismiss(), on_dismiss and on_dismiss()))
            row.addWidget(ok)
        v.addLayout(row)
        self.login = ""

    def _copy(self):
        QGuiApplication.clipboard().setText(acl_example(self.login))
        self.copy.setText("Скопировано")
        QTimer.singleShot(1600, lambda: self.copy.setText("Скопировать пример ACL"))

    def _dismiss(self):
        self.state.conf["tailscale_acl_seen"] = True
        F.save_conf(self.state.conf)
        self.hide()


class TailnetSection(QWidget):
    """The "Via Tailscale" group: the computer's tailnet, TV-like peers with Add, the
    security note (once) and what works remotely. Used by the TV chooser and Settings."""
    MAX_ROWS = 4

    def __init__(self, state, wide: bool = False, title: bool = True, security: bool = True):
        super().__init__()
        T = F.T
        self.state = state
        self.wide = wide
        self.net = None
        self.show_all = False
        self._loading = False
        self.on_render = []         # callbacks(net) after each render (the Settings page)
        self.setStyleSheet("background:transparent")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.head = QWidget()                  # Settings hides it and moves the refresh button
        head = QHBoxLayout(self.head)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.title = F.label("Через Tailscale", 12, T.fg, QFont.Weight.DemiBold)
        head.addWidget(self.title, 1)
        self.title.setVisible(title)
        self.count = F.label("", 10.5, T.fg_a(0.5))
        head.addWidget(self.count)
        self.again = QPushButton()
        self.again.setFixedSize(24, 24)
        self.again.setCursor(Qt.CursorShape.PointingHandCursor)
        self.again.setToolTip("Обновить")
        self.again.setIcon(_refresh_icon(13, T.fg_a(0.65)))
        self.again.setIconSize(QSize(13, 13))
        self.again.setStyleSheet(f"QPushButton{{border:none;border-radius:12px;background:transparent}}"
                                 f"QPushButton:hover{{background:{_css(T.fg_a(0.1))}}}")
        self.again.clicked.connect(self.refresh)
        head.addWidget(self.again)
        v.addWidget(self.head)
        self.msg = F.label("Проверяю Tailscale…", 11, T.fg_a(0.6), wrap=True)
        v.addWidget(self.msg)
        self.rows_box = QWidget()
        self.rows = QVBoxLayout(self.rows_box)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(6)
        v.addWidget(self.rows_box)
        self.links = QHBoxLayout()
        self.links.setSpacing(14)
        self.links.setContentsMargins(0, 0, 0, 0)
        lw = QWidget()
        lw.setLayout(self.links)
        v.addWidget(lw)
        self.sec = None
        if security and not state.conf.get("tailscale_acl_seen"):
            self.sec = SecurityNote(state, on_dismiss=self._relayout)
            self.sec.hide()
            v.addWidget(self.sec)
        self.foot = F.label("Вдали от дома работают пульт, приложения, набор текста, ADB и экран ТВ, а трансляция — "
                            "если ТВ отвечает на порт Cast. В поле выше подойдёт и адрес Tailscale: 100.x.y.z "
                            "или имя MagicDNS.", 10.5, T.fg_a(0.5), wrap=True)
        v.addWidget(self.foot)
        state.changed.connect(self._state_changed)
        self._known = self._known_ips()

    # -- data
    def refresh(self):
        if self._loading:
            return
        self._loading = True
        self.again.setEnabled(False)

        def done(net, e):
            self._loading = False
            self.again.setEnabled(True)
            self.net = net if net else {"cli": None, "error": str(e), "peers": [], "running": False}
            self.render()
        self.state.bg(tailnet, done)

    def set_net(self, net: dict):
        """Tests and callers that already have the status."""
        self.net = net
        self.render()

    def _known_ips(self):
        return {d["ip"] for d in self.state.devices}

    def _state_changed(self):
        k = self._known_ips()
        if k != self._known:
            self._known = k
            if self.net:
                self.render()

    def add(self, peer):
        add(self.state, peer["ip"], name=peer["host"])

    # -- view
    def _clear(self):
        for lay in (self.rows, self.links):
            while lay.count():
                it = lay.takeAt(0)
                if it.widget():
                    it.widget().hide()          # gone at once, not when the deferred delete runs
                    it.widget().deleteLater()

    def _relayout(self):
        w = self.window()
        if w is not None and not self.wide:
            QTimer.singleShot(0, w.adjustSize)

    def render(self):
        self._clear()
        net = self.net or {}
        peers = []
        self.count.setText("")
        if self.sec is not None:
            self.sec.hide()
        if self.net is None:
            self.msg.setText(_tr("Проверяю Tailscale…"))
        elif not net.get("cli"):
            self.msg.setText(F.typo_ru(_tr("Tailscale не установлен на этом компьютере.") + " " + _tr(
                "С Tailscale пульт и экран ТВ работают вдали от дома. Установите его на компьютер и на ТВ "
                "и войдите в один и тот же аккаунт.")))
            self.links.addWidget(_link("Скачать Tailscale", DOWNLOAD))
            self.links.addWidget(_link("Как это работает", docs_url()))
        elif net.get("error"):
            self.msg.setText(_tr("Не удалось прочитать состояние Tailscale."))
            self.msg.setToolTip(net["error"])
        elif not net.get("running"):
            self.msg.setText(_tr("Tailscale выключен или не выполнен вход. Включите его в приложении Tailscale."))
            self.links.addWidget(_link("Как это работает", docs_url()))
        else:
            allp = net.get("peers", [])
            known = self._known_ips()
            tvs = [p for p in allp if p["tv"] > 0 or p["ip"] in known]
            peers = allp if self.show_all else tvs
            on = sum(1 for p in tvs if p["online"])
            if tvs:
                self.count.setText(_tr("в сети") + f": {on}/{len(tvs)}" if not self.wide else "")
            if not peers:
                self.msg.setText(F.typo_ru(_tr("В сети Tailscale нет телевизоров.") + " " +
                                           _tr("Установите Tailscale на ТВ и войдите в тот же аккаунт, "
                                               "что и на компьютере.")))
            else:
                self.msg.setText("")
            cur = self.state.device["ip"] if self.state.device else None
            for p in peers[: 12 if self.wide else (2 * self.MAX_ROWS if self.show_all else self.MAX_ROWS)]:
                self.rows.addWidget(PeerRow(p, p["ip"] in known, p["ip"] == cur, self.add))
            others = len(allp) - len(tvs)
            if others > 0:
                if self.show_all:
                    self.links.addWidget(_link("Только телевизоры", self._toggle_all))
                else:
                    self.links.addWidget(_link(_tr("Показать все устройства ({n})").replace("{n}", str(len(allp))),
                                               self._toggle_all))
            if not tvs:
                self.links.addWidget(_link("Установить Tailscale на ТВ…",
                                           lambda: install_tailscale_on_tv(self.state)))
            # the one-time security note: once a tailnet TV is on the list, until "Got it"
            if self.sec is not None:
                self.sec.login = net.get("login", "")
                self.sec.setVisible(not self.state.conf.get("tailscale_acl_seen")
                                    and any(is_tailscale(d) for d in self.state.devices))
        if self.links.count():
            self.links.addStretch(1)
        self.msg.setVisible(bool(self.msg.text()))
        self.rows_box.setVisible(bool(peers) and bool(net.get("running")))
        self.links.parentWidget().setVisible(self.links.count() > 0)
        self.foot.setVisible(bool(net.get("cli")) and not self.wide)
        for fn in self.on_render:
            fn(net)
        self._relayout()

    def _toggle_all(self):
        self.show_all = not self.show_all
        self.render()


def _refresh_icon(s: int, color: QColor):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QIcon, QPainter, QPixmap
    k = _dpr() * 2
    pm = QPixmap(int(s * k), int(s * k))
    pm.setDevicePixelRatio(k)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    import fling_settings                 # the same "refresh" line icon as in Settings
    fling_settings.paint_icon(p, "refresh", QRectF(0, 0, s, s), color, 1.6)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- Settings page

def settings_page(win) -> QWidget:
    """Settings → Remote access: this computer's Tailscale, tailnet TVs, what works, security."""
    import fling_settings as S
    T = F.T
    w, v = win._page()
    v.addWidget(S.heading("Удалённый доступ"))

    box, lay = S.card()
    top = QHBoxLayout()
    top.setSpacing(10)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(F.label("Телевизоры в сети Tailscale", 12.5, weight=QFont.Weight.DemiBold))
    me = S.note("")
    col.addWidget(me)
    top.addLayout(col, 1)
    lay.addLayout(top)
    sec_tn = TailnetSection(win.state, wide=True, title=False, security=False)
    top.addWidget(sec_tn.again, 0, Qt.AlignmentFlag.AlignTop)    # refresh beside the card's title
    sec_tn.head.hide()
    lay.addWidget(sec_tn)
    v.addWidget(box)

    def on_net(net):
        s = net.get("self")
        me.setVisible(bool(s and net.get("running")))
        if s and net.get("running"):
            me.setText(_tr("Этот компьютер: ") + " · ".join(x for x in (s["host"], s["ip"]) if x))
    sec_tn.on_render.append(on_net)
    me.hide()

    v.addWidget(S.section("Что работает через Tailscale"))
    box, lay = S.card()
    lay.setSpacing(6)
    for ok, text in ((True, "Пульт, громкость, приложения и набор текста"),
                     (True, "ADB: установка APK, файлы, снимки экрана, консоль"),
                     (True, "Экран ТВ вживую — начинается с качества «Экономно»"),
                     (True, "Ссылка на ТВ для YouTube — она работает через интернет"),
                     (None, "Трансляция ссылок — если ТВ отвечает на порт Cast (8009)"),
                     (None, "Файлы с компьютера — только если брандмауэр пускает ТВ к компьютеру"),
                     (False, "Поиск ТВ в сети — добавляйте ТВ по адресу Tailscale")):
        lay.addLayout(_check_row(ok, text))
    v.addWidget(box)

    v.addWidget(S.section("Безопасность"))
    box, lay = S.card()
    lay.addWidget(F.label("ADB через Tailscale — полный доступ к ТВ", 12.5, weight=QFont.Weight.DemiBold))
    lay.addWidget(S.note("Разрешите в правилах доступа (ACL) Tailscale подключаться к портам ТВ 5555, 6466 и 6467 "
                         "только своим устройствам. Fling ничего не открывает в интернет и не пробрасывает порты."))
    lay.addWidget(S.note("Пометьте ТВ тегом tag:tv в консоли Tailscale (Machines → ТВ → Edit ACL tags) и вставьте "
                         "правило ниже. Если в ваших правилах есть «всё всем» (src \"*\" → dst \"*:*\"), уберите его, "
                         "иначе ТВ видят все устройства сети."))
    code = QLabel()
    code.setFont(F.font(11.5, mono=True))
    code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    code.setStyleSheet(f"color:{_css(T.fg_a(0.85))};background:{_css(T.fg_a(0.06))};"
                       f"border:1px solid {_css(T.fg_a(0.1))};border-radius:7px;padding:8px 10px")
    code.setText(acl_example().rstrip("\n"))
    lay.addWidget(code)
    row = QHBoxLayout()
    row.setSpacing(8)
    cp = S.button("Скопировать пример ACL", lambda: None, icon_name="copy")

    def copy():
        login = (sec_tn.net or {}).get("login", "")
        QGuiApplication.clipboard().setText(acl_example(login))
        cp.setText(_tr("Скопировано"))
        QTimer.singleShot(1600, lambda: cp.setText(_tr("Скопировать пример ACL")))
    cp.clicked.connect(copy)
    row.addWidget(cp)
    row.addStretch(1)
    lay.addLayout(row)
    v.addWidget(box)

    sec_tn.on_render.append(lambda net: code.setText(acl_example(net.get("login", "")).rstrip("\n")))

    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(S.button("Установить Tailscale на ТВ…", lambda: install_tailscale_on_tv(win.state)))
    row.addWidget(S.link_button("Как это работает", docs_url()))
    row.addWidget(S.link_button("Скачать Tailscale", DOWNLOAD))
    row.addStretch(1)
    v.addLayout(row)
    v.addWidget(S.note("Fling только читает состояние Tailscale и никогда не меняет его настройки."))
    v.addStretch(1)
    w._tailnet = sec_tn
    return w


def _check_row(ok, text):
    """✓ / ~ / ✗ and a line of text."""
    T = F.T
    h = QHBoxLayout()
    h.setSpacing(9)
    mark = QLabel()
    mark.setFixedSize(16, 16)
    col = T.green if ok else (T.orange if ok is None else T.fg_a(0.4))
    mark.setPixmap(_mark_pixmap(16, ok, col))
    h.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
    t = F.label(text, 12, T.fg if ok is not False else T.fg_a(0.6), wrap=True)
    h.addWidget(t, 1)
    return h


def _mark_pixmap(s: int, ok, color: QColor):
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtGui import QPainter, QPen, QPixmap
    k = _dpr() * 2
    pm = QPixmap(int(s * k), int(s * k))
    pm.setDevicePixelRatio(k)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QColor(color)
    bg.setAlphaF(0.16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawEllipse(QRectF(0, 0, s, s))
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if ok:
        p.drawPolyline(QPointF(s * .29, s * .52), QPointF(s * .44, s * .67), QPointF(s * .72, s * .36))
    elif ok is None:
        p.drawLine(QPointF(s * .32, s * .5), QPointF(s * .68, s * .5))
    else:
        p.drawLine(QPointF(s * .35, s * .35), QPointF(s * .65, s * .65))
        p.drawLine(QPointF(s * .65, s * .35), QPointF(s * .35, s * .65))
    p.end()
    return pm
