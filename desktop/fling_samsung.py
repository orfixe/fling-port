# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Smart View for Samsung TVs (Tizen, 2016 and later), with Fling's extras.

Samsung's Smart View is three things, and Fling does all three plus more:

  * Screen mirroring. On a Samsung TV this is plain Miracast (Wi-Fi Display), which the
    computer's own OS speaks: Windows through its Cast panel (Win+K), Linux through
    gnome-network-displays (its daemon's D-Bus API; Fling runs it, it doesn't link it).
  * The remote. Tizen TVs take keys, text and app launches over a local WebSocket
    (wss://TV:8002, older sets ws://TV:8001). The first time, the TV asks "Allow?" and
    then hands out a token that Fling keeps, so it never asks again.
  * Media. Videos, photos and music from this computer and web links go to the TV's
    DLNA renderer (UPnP AVTransport), so the TV plays them itself at full quality while
    the computer stays free.

Extras over Smart View: turn the TV on over the network (Wake-on-LAN) and mirror in
one step, the installed apps list, typing into the TV, opening a link in the TV's
browser, the keyboard as a remote while the window is focused, and a check whether this
computer can mirror at all (Wi-Fi + graphics drivers).

Nothing here is a VPN, proxy or open port to the internet: the file server answers only
the selected TV's address, serves only the one file picked, and stops with the window.

There is no window of its own: Samsung and Miracast TVs are listed in the main TV chooser
and driven from the main panel through `Hub` (State.sv).

fling_app binds itself as F right after the import (like fling_tailscale)."""
from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

import fling_platform as P

F = None          # fling_app, set by fling_app right after the import

WS_PORT_TLS, WS_PORT = 8002, 8001
DLNA_PORT = 9197
CHANNEL = "/api/v2/channels/samsung.remote.control"

# Fallback app list (Tizen app ids) for TVs that don't answer "installed apps".
DEFAULT_APPS = [
    ("YouTube", "111299001912"),
    ("Netflix", "3201907018807"),
    ("Prime Video", "3201910019365"),
    ("Disney+", "3201901017640"),
    ("Spotify", "3201606009684"),
    ("Plex", "3201512006963"),
]
BROWSER_APP = "org.tizen.browser"

PHRASES = [
    ("Fling — Smart View", "Fling — Smart View"),
    ("Smart View…", "Smart View…"),
    ("Телевизор Samsung", "Samsung TV"),
    ("Искать", "Search"),
    ("Ищу телевизоры Samsung…", "Looking for Samsung TVs…"),
    ("Телевизоры Samsung не найдены. Проверьте, что ТВ включён и в той же сети, или добавьте его по IP.",
     "No Samsung TVs found. Make sure the TV is on and on the same network, or add it by IP."),
    ("Телевизоры не найдены ни в сети, ни по Wi‑Fi Direct. Включите ТВ и на нём — «Screen Mirroring» / «Зеркальное отображение».",
     "No TVs found on the network or over Wi‑Fi Direct. Turn the TV on and open Screen Mirroring on it."),
    ("Найдено телевизоров Samsung: {n}", "Samsung TVs found: {n}"),
    ("Найдено: {n} в сети, {m} по Wi‑Fi Direct", "Found: {n} on the network, {m} over Wi‑Fi Direct"),
    ("{name} найден по Wi‑Fi Direct, без общей сети — работает зеркалирование экрана. Для пульта, приложений и файлов "
     "нужна общая сеть с ТВ.",
     "{name} was found over Wi‑Fi Direct, without a shared network — screen mirroring works. The remote, apps and "
     "files need the TV on the same network."),
    ("Зеркалировать экран", "Mirror screen"),
    ("Остановить зеркалирование", "Stop mirroring"),
    ("Экран компьютера на ТВ по Miracast — как Smart View.",
     "This computer's screen on the TV over Miracast, like Smart View."),
    ("В окне «Трансляция» выберите {name}. Если ТВ спросит разрешение — нажмите «Разрешить» пультом.",
     "In the Cast panel, choose {name}. If the TV asks for permission, press Allow on its remote."),
    ("Этот компьютер не поддерживает Miracast: нужен Wi‑Fi и драйверы с «Wireless Display». Пульт, приложения и файлы работают и так.",
     "This computer can't do Miracast: it needs Wi‑Fi and drivers with Wireless Display. The remote, apps and files work anyway."),
    ("Для зеркалирования на Linux нужен gnome-network-displays. Установите его из магазина приложений или Flathub.",
     "Mirroring on Linux needs gnome-network-displays. Install it from your app store or Flathub."),
    ("Открываю gnome-network-displays — выберите в нём {name}.",
     "Opening gnome-network-displays — choose {name} there."),
    ("Подключаюсь к {name}…", "Connecting to {name}…"),
    ("Зеркалирование на {name} запущено.", "Mirroring to {name} started."),
    ("Включить ТВ", "Turn TV on"),
    ("Выключить ТВ", "Turn TV off"),
    ("Источник", "Source"),
    ("Пульт", "Remote"),
    ("Приложения", "Apps"),
    ("На ТВ", "To the TV"),
    ("Видео, фото, музыка…", "Video, photo, music…"),
    ("Ссылка из буфера", "Link from clipboard"),
    ("Ссылку в браузер ТВ", "Link in TV's browser"),
    ("Текст на ТВ — Enter отправляет", "Text to the TV — Enter sends"),
    ("IP-адрес Samsung, например 192.168.1.60", "Samsung's IP address, e.g. 192.168.1.60"),
    ("Добавить", "Add"),
    ("Подключено к {name}", "Connected to {name}"),
    ("Нажмите «Разрешить» на экране ТВ", "Press Allow on the TV screen"),
    ("ТВ не разрешил подключение. В настройках ТВ: Общие → Диспетчер внешних устройств → Диспетчер подключения устройств — разрешите Fling.",
     "The TV refused the connection. On the TV: General → External Device Manager → Device Connection Manager — allow Fling."),
    ("ТВ не отвечает — он выключен или в другой сети.", "The TV doesn't answer — it's off or on another network."),
    ("Включаю {name}…", "Turning {name} on…"),
    ("Не знаю MAC-адрес ТВ — включите его один раз пультом, чтобы Fling его запомнил.",
     "Fling doesn't know the TV's MAC address yet — turn the TV on once with its remote so Fling can remember it."),
    ("Чтобы включать по сети, на ТВ: Общие → Сеть → Экспертные настройки → «Включение с моб. устройства».",
     "To turn on over the network, on the TV: General → Network → Expert Settings → Power On with Mobile."),
    ("В буфере обмена нет ссылки.", "There's no link on the clipboard."),
    ("Отправляю на ТВ: {title}", "Sending to the TV: {title}"),
    ("ТВ не принял файл: {err}", "The TV didn't accept the file: {err}"),
    ("ТВ не поддерживает DLNA или он выключен.", "The TV doesn't support DLNA or it's off."),
    ("Пауза", "Pause"), ("Играть", "Play"), ("Стоп", "Stop"),
    ("Miracast · Wi‑Fi Direct — только зеркалирование экрана", "Miracast · Wi‑Fi Direct — screen mirroring only"),
    ("Samsung · пульт по сети", "Samsung · network remote"), ("Подключаюсь к ТВ…", "Connecting to the TV…"),
    ("Samsung · ТВ не отвечает", "Samsung · the TV doesn't answer"),
    ("Это приложение Fling не знает на Samsung — откройте его пультом.",
     "Fling doesn't know this app on Samsung — open it with the remote."),
    ("По Wi‑Fi Direct можно только зеркалировать экран — нажмите «Зеркалировать экран».",
     "Over Wi‑Fi Direct only screen mirroring works — press Mirror screen."),
    ("На Samsung можно отправить видео, музыку или фото.", "You can send videos, music or photos to a Samsung."),
    ("Экран на ТВ", "Screen on the TV"),
    ("Картинка на ТВ: {w}×{h}, без растяжения.", "Picture on the TV: {w}×{h}, not stretched."),
    ("Только ТВ", "TV only"), ("Расширить", "Extend"), ("Дублировать", "Duplicate"),
    ("Экран только на ТВ, в его разрешении", "Screen on the TV only, at its own resolution"),
    ("ТВ — второй экран, в его разрешении", "The TV as a second screen, at its own resolution"),
    ("Одна картинка на обоих экранах (на ТВ может растянуться)",
     "The same picture on both screens (may be stretched on the TV)"),
    ("Играть / пауза", "Play / pause"), ("Без звука", "Mute"), ("Тише", "Volume down"),
    ("Громче", "Volume up"), ("Канал назад", "Channel down"), ("Канал вперёд", "Channel up"),
    ("Гр −", "Vol −"), ("Гр +", "Vol +"), ("Назад", "Back"), ("Домой", "Home"), ("Меню", "Menu"),
    ("Клавиши пульта: стрелки, Enter, Backspace — назад, Home, +/− — громкость.",
     "Keyboard as a remote: arrows, Enter, Backspace = back, Home, +/− = volume."),
    ("Медиа (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mp3 *.m4a *.flac *.wav *.ogg *.aac *.jpg *.jpeg *.png *.gif *.bmp *.webp);;Все файлы (*)",
     "Media (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mp3 *.m4a *.flac *.wav *.ogg *.aac *.jpg *.jpeg *.png *.gif *.bmp *.webp);;All files (*)"),
]


def _t(ru: str, **kw) -> str:
    s = F.tr(ru) if F else ru
    return s.format(**kw) if kw else s


# ---------------------------------------------------------------- discovery

_SSDP_ADDR = ("239.255.255.250", 1900)
_SSDP_ST = ("urn:samsung.com:device:RemoteControlReceiver:1",
            "urn:schemas-upnp-org:device:MediaRenderer:1")


def device_info(ip: str, timeout: float = 2.5) -> dict | None:
    """The TV's own description from http://TV:8001/api/v2/ — None if it isn't a Tizen TV."""
    try:
        with urllib.request.urlopen(f"http://{ip}:{WS_PORT}/api/v2/", timeout=timeout) as r:
            d = json.load(r)
    except Exception:
        return None
    dev = d.get("device") or {}
    if not dev:
        return None
    name = html.unescape(dev.get("name") or d.get("name") or "Samsung TV")
    return {
        "ip": ip,
        "name": name,
        "model": dev.get("modelName") or dev.get("model") or "",
        "mac": (dev.get("wifiMac") or "").lower(),
        "power": dev.get("PowerState") or "on",
        "tls": str(dev.get("TokenAuthSupport", "")).lower() == "true",
        "os": dev.get("OS") or "Tizen",
    }


def discover(timeout: float = 3.0) -> tuple[list[dict], dict[str, str]]:
    """SSDP for Samsung remotes and DLNA renderers, then /api/v2 on each address.
    Returns (tvs, dlna_locations by ip)."""
    found: set[str] = set()
    dlna: dict[str, str] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(0.4)
        for st in _SSDP_ST:
            msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                   f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {st}\r\n\r\n')
            for _ in range(2):
                s.sendto(msg.encode(), _SSDP_ADDR)
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode(errors="replace")
            st = re.search(r"(?im)^st:\s*(\S+)", text)
            loc = re.search(r"(?im)^location:\s*(\S+)", text)
            server = re.search(r"(?im)^server:\s*(.+)$", text)
            st = st.group(1) if st else ""
            if "MediaRenderer" in st and loc:
                dlna[addr[0]] = loc.group(1)
            if "samsung" in st.lower() or (server and "samsung" in server.group(1).lower()) \
                    or "MediaRenderer" in st:
                found.add(addr[0])
    finally:
        s.close()
    tvs = []
    for ip in sorted(found):
        info = device_info(ip)
        if info:
            tvs.append(info)
    return tvs, dlna


# ---------------------------------------------------------------- Wake-on-LAN

def wake(mac: str, ip: str | None = None) -> bool:
    mac = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    if len(mac) != 12:
        return False
    packet = b"\xff" * 6 + bytes.fromhex(mac) * 16
    targets = {"255.255.255.255"}
    if ip and ip.count(".") == 3:
        targets.add(".".join(ip.split(".")[:3] + ["255"]))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            for t in targets:
                for port in (9, 7):
                    try:
                        s.sendto(packet, (t, port))
                    except OSError:
                        pass
    finally:
        s.close()
    return True


# ---------------------------------------------------------------- mirroring (Miracast)

def miracast_supported() -> bool | None:
    """Windows: does `netsh wlan show drivers` say Wireless Display is supported?
    None when it can't tell (no Wi-Fi service, other OS, unknown wording)."""
    if not P.IS_WIN:
        return None
    try:
        out = P.run(["netsh", "wlan", "show", "drivers"], capture_output=True, timeout=6).stdout
    except Exception:
        return None
    text = out.decode("utf-8", errors="replace")
    if "�" in text:
        text = out.decode("cp866", errors="replace")
    for line in text.splitlines():
        low = line.lower()
        if "wireless display" in low or "беспроводной дисплей" in low or "беспроводного дисплея" in low:
            val = line.split(":", 1)[-1].strip().lower()
            if val.startswith(("yes", "да")):
                return True
            if val.startswith(("no", "нет")):
                return False
    return None


# Windows' own list of Wi-Fi Direct (Miracast) displays — the same one its Cast panel shows.
# Works without a shared network (hotel rooms, guest Wi-Fi with client isolation).
_WFD_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$m = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and
      $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
[void][Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
[void][Windows.Devices.Enumeration.DeviceInformationCollection, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
$p = [System.Collections.Generic.List[string]]::new(); $p.Add('System.Devices.Aep.DeviceAddress')
$op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync(
  'System.Devices.Aep.ProtocolId:="{0407d24e-53de-4c9a-9ba1-9ced54641188}"', $p,
  [Windows.Devices.Enumeration.DeviceInformationKind]::AssociationEndpoint)
$t = $m.MakeGenericMethod([Windows.Devices.Enumeration.DeviceInformationCollection]).Invoke($null, @($op))
[void]$t.Wait(12000)
$out = @(foreach ($d in $t.Result) { @{ name = $d.Name; addr = [string]$d.Properties['System.Devices.Aep.DeviceAddress'] } })
ConvertTo-Json -Compress -InputObject $out
"""


def miracast_sinks() -> list[dict]:
    """Windows: Miracast displays in range over Wi-Fi Direct, as [{name, addr}]."""
    if not P.IS_WIN:
        return []
    try:
        out = P.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-Command", _WFD_PS], capture_output=True, timeout=25)
        data = json.loads(out.stdout.decode("utf-8", errors="replace").strip() or "[]")
    except Exception as e:
        print("smartview: miracast list", e, flush=True)
        return []
    if isinstance(data, dict):
        data = [data]
    seen, sinks = set(), []
    for d in data or []:
        name = (d.get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            sinks.append({"name": name, "addr": d.get("addr") or "", "kind": "miracast", "ip": ""})
    return sinks


# Windows often gives a Miracast TV a 4:3 mode such as 1152x864: the picture is huge and
# stretched. After "Mirror", Fling watches for the display that appears and moves it to the
# largest 16:9 mode it offers (4K or 2K when the TV and Windows allow it, usually 1920x1080).
# Displays that were there before are never touched.

def _devmode_type():
    import ctypes
    from ctypes import wintypes

    class DEVMODE(ctypes.Structure):
        _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                    ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
                    ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                    ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                    ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                    ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                    ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                    ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                    ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                    ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                    ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
                    ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                    ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                    ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                    ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]
    return DEVMODE


_DEVMODE = None


def _devmode(dev: str, mode: int):
    import ctypes
    global _DEVMODE
    _DEVMODE = _DEVMODE or _devmode_type()
    dm = _DEVMODE()
    dm.dmSize = ctypes.sizeof(dm)
    return dm if ctypes.windll.user32.EnumDisplaySettingsW(dev, mode, ctypes.byref(dm)) else None


def win_displays() -> dict:
    """Windows: displays on the desktop, {device: (width, height, [modes])}."""
    if not P.IS_WIN:
        return {}
    import ctypes
    from ctypes import wintypes

    class DISPLAY_DEVICE(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]
    u = ctypes.windll.user32
    out, i = {}, 0
    while True:
        dd = DISPLAY_DEVICE()
        dd.cb = ctypes.sizeof(dd)
        if not u.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & 0x1:                       # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            continue
        cur = _devmode(dd.DeviceName, -1)
        if not cur:
            continue
        modes, j = set(), 0
        while True:
            m = _devmode(dd.DeviceName, j)
            if not m:
                break
            modes.add((m.dmPelsWidth, m.dmPelsHeight))
            j += 1
        out[dd.DeviceName] = (cur.dmPelsWidth, cur.dmPelsHeight, sorted(modes))
    return out


def _is_wide(w: int, h: int) -> bool:
    return bool(h) and abs(w / h - 16 / 9) < 0.02


def best_tv_mode(modes) -> tuple | None:
    """The largest 16:9 mode up to 3840x2160 (4K, 2K or 1080p — whatever is offered)."""
    wide = [(w, h) for w, h in modes if _is_wide(w, h) and w <= 3840]
    return max(wide, default=None)


def set_display_mode(dev: str, w: int, h: int) -> bool:
    import ctypes
    dm = _devmode(dev, -1)
    if not dm:
        return False
    dm.dmPelsWidth, dm.dmPelsHeight = w, h
    dm.dmFields = 0x00080000 | 0x00100000                 # DM_PELSWIDTH | DM_PELSHEIGHT
    return ctypes.windll.user32.ChangeDisplaySettingsExW(dev, ctypes.byref(dm), None, 0x1, None) == 0


def fix_display(dev: str) -> tuple | None:
    """Move `dev` to its best 16:9 mode. (device, w, h, changed) or None if it's gone."""
    d = win_displays().get(dev)
    if not d:
        return None
    w, h, modes = d
    best = best_tv_mode(modes)
    if best and (w, h) != best and set_display_mode(dev, *best):
        return dev, best[0], best[1], True
    return dev, w, h, False


def fix_new_tv_display(before: set) -> tuple | None:
    """The display that appeared since `before`, moved to 16:9; None while none has."""
    for dev in win_displays():
        if dev not in before:
            return fix_display(dev)
    return None


def display_switch(mode: str) -> None:
    """Windows' own Win+P: 'external' (TV only), 'extend' or 'clone'."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "DisplaySwitch.exe")
    P.popen([exe, f"/{mode}"])


# Picks the TV in Windows' Cast panel (opened by Win+K) through UI Automation, so mirroring
# is one click in Fling. Skips when the panel already shows a connection (no accidental
# disconnect) and never clicks anything but an item named exactly like the TV.
_CAST_PICK_PS = r"""
param([string]$Name)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::NameProperty, $Name)))
  foreach ($e in $all) {
    $ct = $e.Current.ControlType.ProgrammaticName
    if ($ct -notmatch 'ListItem|Button') { continue }
    $panel = [System.Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($e)
    $busy = $false
    foreach ($word in 'Disconnect','Отключить','Отключиться','Connected','Подключено') {
      $hit = $panel.FindFirst([System.Windows.Automation.TreeScope]::Subtree,
        (New-Object System.Windows.Automation.PropertyCondition(
          [System.Windows.Automation.AutomationElement]::NameProperty, $word)))
      if ($hit) { $busy = $true }
    }
    if ($busy) { 'already'; exit 0 }
    $p = $null
    if ($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) { $p.Invoke(); 'picked'; exit 0 }
    if ($e.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) { $p.Select(); 'picked'; exit 0 }
  }
  Start-Sleep -Milliseconds 400
}
'notfound'
"""


def cast_pick(name: str) -> str:
    """'picked' / 'already' / 'notfound' / 'error'."""
    if not P.IS_WIN or not name:
        return "error"
    try:
        out = P.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-Command", f"& {{{_CAST_PICK_PS}}} -Name '{name.replace(chr(39), chr(39) * 2)}'"],
                    capture_output=True, timeout=25)
        res = out.stdout.decode("utf-8", errors="replace").strip().splitlines()
        return res[-1] if res else "error"
    except Exception as e:
        print("smartview: cast pick", e, flush=True)
        return "error"


def _send_win_k() -> bool:
    try:
        import ctypes
        u = ctypes.windll.user32
        VK_LWIN, VK_K, UP = 0x5B, 0x4B, 0x0002
        u.keybd_event(VK_LWIN, 0, 0, 0)
        u.keybd_event(VK_K, 0, 0, 0)
        u.keybd_event(VK_K, 0, UP, 0)
        u.keybd_event(VK_LWIN, 0, UP, 0)
        return True
    except Exception as e:
        print("smartview: Win+K", e, flush=True)
        return False


_GND_DEST = "org.gnome.NetworkDisplays.Manager"
_GND_PATH = "/org/gnome/NetworkDisplays/Manager"


def _gnd_displays() -> list[dict] | None:
    """Sinks the gnome-network-displays daemon sees: [{uuid, name, state, protocol}]."""
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return None
    try:
        out = P.run([gdbus, "call", "--session", "--dest", _GND_DEST, "--object-path", _GND_PATH,
                     "--method", "org.freedesktop.DBus.Properties.Get", _GND_DEST, "Displays"],
                    capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    sinks = []
    for chunk in re.findall(r"\{([^{}]*)\}", out.stdout):
        uuid = re.search(r"'uuid':\s*<'([^']*)'>", chunk)
        name = re.search(r"'display-name':\s*<'([^']*)'>", chunk)
        proto = re.search(r"'protocol':\s*<(?:uint32\s+)?(\d+)>", chunk)
        state = re.search(r"'state':\s*<(?:uint32\s+)?(\d+)>", chunk)
        if uuid:
            sinks.append({"uuid": uuid.group(1), "name": name.group(1) if name else "",
                          "protocol": int(proto.group(1)) if proto else 0,
                          "state": int(state.group(1)) if state else 0})
    return sinks


def _gnd_call(method: str, arg: str) -> str | None:
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return None
    try:
        out = P.run([gdbus, "call", "--session", "--dest", _GND_DEST, "--object-path", _GND_PATH,
                     "--method", f"{_GND_DEST}.{method}", arg], capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    m = re.search(r"'([^']*)'", out.stdout)
    return m.group(1) if m else ""


def _name_match(a: str, b: str) -> bool:
    a, b = re.sub(r"\W+", "", a.lower()), re.sub(r"\W+", "", b.lower())
    return bool(a and b) and (a in b or b in a)


def mirror_linux(tv_name: str) -> tuple[str, str | None]:
    """('started', unit) / ('opened', None) / ('missing', None)."""
    sinks = _gnd_displays()
    if sinks:
        # Miracast first (P2P = 1, MICE = 2); Chromecast (3) only if nothing else matches.
        for proto in ((1, 2), (3,)):
            for s in sinks:
                if s["protocol"] in proto and _name_match(s["name"], tv_name):
                    unit = _gnd_call("StartStream", s["uuid"])
                    if unit is not None:
                        return "started", unit
    for cmd in (["gnome-network-displays"], ["flatpak", "run", "org.gnome.NetworkDisplays"]):
        if shutil.which(cmd[0]):
            if cmd[0] == "flatpak":
                chk = P.run(["flatpak", "info", "org.gnome.NetworkDisplays"], capture_output=True)
                if chk.returncode != 0:
                    continue
            P.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "opened", None
    return "missing", None


def stop_mirror_linux(unit: str) -> None:
    if unit:
        _gnd_call("StopStream", unit)


# ---------------------------------------------------------------- the remote (WebSocket)

class SamsungRemote(QObject):
    """One WebSocket to the TV's remote-control channel. States: off, connecting, waiting
    (the TV shows "Allow?"), ready, denied, error."""
    state_changed = pyqtSignal(str)
    apps_changed = pyqtSignal(list)

    def __init__(self, tokens: dict, save_tokens):
        super().__init__()
        from PyQt6.QtWebSockets import QWebSocket
        self._QWebSocket = QWebSocket
        self.ws = None
        self.tv: dict | None = None
        self.state = "off"
        self.tokens = tokens
        self._save_tokens = save_tokens
        self._tried_plain = False
        self._wait_timer = QTimer(self)
        self._wait_timer.setSingleShot(True)
        self._wait_timer.timeout.connect(self._on_wait)

    def _set(self, st: str):
        if st != self.state:
            self.state = st
            self.state_changed.emit(st)

    def connect_to(self, tv: dict, plain: bool = False):
        self.close()
        self.tv = tv
        self._tried_plain = plain
        name = base64.b64encode(P.CLIENT_NAME.encode()).decode()
        q = {"name": name}
        tok = self.tokens.get(tv.get("mac") or tv["ip"])
        if tok:
            q["token"] = tok
        scheme, port = ("ws", WS_PORT) if plain else ("wss", WS_PORT_TLS)
        url = f"{scheme}://{tv['ip']}:{port}{CHANNEL}?{urllib.parse.urlencode(q)}"
        ws = self._QWebSocket("", parent=self)
        ws.sslErrors.connect(lambda _errs, w=ws: w.ignoreSslErrors())   # the TV's cert is self-signed
        ws.connected.connect(self._on_open)
        ws.disconnected.connect(self._on_closed)
        ws.textMessageReceived.connect(self._on_message)
        err = getattr(ws, "errorOccurred", None) or getattr(ws, "error", None)
        if err is not None:                 # refused / unreachable: no "disconnected" without a connect
            err.connect(lambda *_a, w=ws: self._on_error(w))
        self.ws = ws
        self._set("connecting")
        ws.open(QUrl(url))
        self._wait_timer.start(4000)       # no answer in 4 s: the TV is asking "Allow?" or is off

    def close(self):
        self._wait_timer.stop()
        if self.ws is not None:
            ws, self.ws = self.ws, None
            try:
                ws.disconnected.disconnect(self._on_closed)
            except TypeError:
                pass
            ws.abort()
            ws.deleteLater()
        self._set("off")

    def _fallback_or_error(self):
        if not self._tried_plain and self.tv:
            self.connect_to(self.tv, plain=True)       # a 2016 set without the TLS port
        else:
            self._set("error")

    def _on_error(self, ws):
        if ws is self.ws and self.state == "connecting" and ws.state().name != "ConnectedState":
            self._fallback_or_error()

    def _on_wait(self):
        if self.state == "connecting":
            if self.ws is not None and self.ws.state().name == "ConnectedState":
                self._set("waiting")
                self._wait_timer.start(30000)
            else:
                self._fallback_or_error()
        elif self.state == "waiting":
            self._set("error")

    def _on_open(self):
        pass                               # the TV speaks first: ms.channel.connect or unauthorized

    def _on_closed(self):
        if self.state == "connecting":
            self._fallback_or_error()
            return
        if self.state not in ("denied",):
            self._set("error" if self.state != "ready" else "off")

    def _on_message(self, text: str):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        ev = msg.get("event", "")
        data = msg.get("data") or {}
        if ev == "ms.channel.connect":
            self._wait_timer.stop()
            tok = data.get("token") if isinstance(data, dict) else None
            if tok and self.tv:
                self.tokens[self.tv.get("mac") or self.tv["ip"]] = str(tok)
                self._save_tokens()
            self._set("ready")
            self.request_apps()
        elif ev == "ms.channel.unauthorized":
            self._wait_timer.stop()
            self._set("denied")
        elif ev == "ms.channel.timeOut":
            self._set("error")
        elif ev == "ed.installedApp.get":
            apps = []
            for a in (data.get("data") if isinstance(data, dict) else None) or []:
                if a.get("appId") and a.get("name"):
                    apps.append((a["name"], a["appId"], a.get("app_type", 2)))
            if apps:
                self.apps_changed.emit(apps)

    def _send(self, obj: dict) -> bool:
        if self.ws is None or self.state != "ready":
            return False
        self.ws.sendTextMessage(json.dumps(obj))
        return True

    def key(self, code: str, cmd: str = "Click") -> bool:
        return self._send({"method": "ms.remote.control", "params": {
            "Cmd": cmd, "DataOfCmd": code, "Option": "false", "TypeOfRemote": "SendRemoteKey"}})

    def text(self, s: str) -> bool:
        ok = self._send({"method": "ms.remote.control", "params": {
            "Cmd": base64.b64encode(s.encode()).decode(), "DataOfCmd": "base64",
            "TypeOfRemote": "SendInputString"}})
        if ok:
            self._send({"method": "ms.remote.control", "params": {"TypeOfRemote": "SendInputEnd"}})
        return ok

    def launch(self, app_id: str, app_type: int = 2, meta: str = "") -> bool:
        return self._send({"method": "ms.channel.emit", "params": {
            "event": "ed.apps.launch", "to": "host",
            "data": {"appId": app_id, "action_type": "NATIVE_LAUNCH" if app_type == 4 else "DEEP_LINK",
                     "metaTag": meta}}})

    def request_apps(self) -> bool:
        return self._send({"method": "ms.channel.emit", "params": {
            "event": "ed.installedApp.get", "to": "host"}})


# ---------------------------------------------------------------- DLNA (files and links)

_AVT = "urn:schemas-upnp-org:service:AVTransport:1"
_DLNA_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"


def avtransport_url(ip: str, location: str | None) -> str | None:
    """The AVTransport control URL from the renderer's description."""
    for loc in filter(None, (location, f"http://{ip}:{DLNA_PORT}/dmr")):
        try:
            with urllib.request.urlopen(loc, timeout=3) as r:
                root = ET.fromstring(r.read())
        except Exception:
            continue
        for svc in root.iter():
            if not svc.tag.endswith("service"):
                continue
            kids = {c.tag.split("}")[-1]: (c.text or "").strip() for c in svc}
            if "AVTransport" in kids.get("serviceType", ""):
                return urllib.parse.urljoin(loc, kids.get("controlURL", ""))
    return None


def soap(control: str, action: str, args: str = "") -> str:
    body = (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{_AVT}"><InstanceID>0</InstanceID>{args}</u:{action}>'
            f'</s:Body></s:Envelope>').encode()
    req = urllib.request.Request(control, data=body, method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{_AVT}#{action}"'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode(errors="replace")


def _didl(url: str, title: str, mime: str) -> str:
    cls = ("object.item.imageItem.photo" if mime.startswith("image/") else
           "object.item.audioItem.musicTrack" if mime.startswith("audio/") else
           "object.item.videoItem")
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            f'<item id="0" parentID="-1" restricted="1"><dc:title>{html.escape(title)}</dc:title>'
            f'<upnp:class>{cls}</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime}:{_DLNA_FEATURES}">{html.escape(url)}</res>'
            '</item></DIDL-Lite>')


def play_url(control: str, url: str, title: str, mime: str) -> None:
    try:
        soap(control, "Stop")
    except Exception:
        pass
    soap(control, "SetAVTransportURI",
         f"<CurrentURI>{html.escape(url)}</CurrentURI>"
         f"<CurrentURIMetaData>{html.escape(_didl(url, title, mime))}</CurrentURIMetaData>")
    soap(control, "Play", "<Speed>1</Speed>")


def local_ip_for(ip: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 9))
        return s.getsockname()[0]
    finally:
        s.close()


class FileServer:
    """Serves one picked file to one TV address, with Range (seeking) and DLNA headers."""

    def __init__(self):
        self.httpd: ThreadingHTTPServer | None = None
        self.path = ""
        self.token = ""
        self.client = ""

    def share(self, path: str, tv_ip: str) -> str:
        self.path, self.client = path, tv_ip
        self.token = secrets.token_urlsafe(12)
        if self.httpd is None:
            outer = self

            class H(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, *a):
                    pass

                def _ok(self) -> bool:
                    if self.client_address[0] != outer.client or not outer.path \
                            or not self.path.startswith(f"/{outer.token}/"):
                        self.send_error(404)
                        return False
                    return True

                def do_HEAD(self):
                    self._serve(head=True)

                def do_GET(self):
                    self._serve(head=False)

                def _serve(self, head: bool):
                    if not self._ok():
                        return
                    try:
                        size = os.path.getsize(outer.path)
                    except OSError:
                        self.send_error(404)
                        return
                    start, end = 0, size - 1
                    rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
                    if rng and (rng.group(1) or rng.group(2)):
                        if rng.group(1):
                            start = int(rng.group(1))
                            if rng.group(2):
                                end = min(int(rng.group(2)), size - 1)
                        else:
                            start = max(0, size - int(rng.group(2)))
                        if start > end:
                            self.send_error(416)
                            return
                        self.send_response(206)
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    else:
                        self.send_response(200)
                    mime = mimetypes.guess_type(outer.path)[0] or "application/octet-stream"
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("transferMode.dlna.org",
                                     "Interactive" if mime.startswith("image/") else "Streaming")
                    self.send_header("contentFeatures.dlna.org", _DLNA_FEATURES)
                    self.end_headers()
                    if head:
                        return
                    try:
                        with open(outer.path, "rb") as f:
                            f.seek(start)
                            left = end - start + 1
                            while left > 0:
                                chunk = f.read(min(256 * 1024, left))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                left -= len(chunk)
                    except (ConnectionError, OSError):
                        pass

            self.httpd = ThreadingHTTPServer(("0.0.0.0", 0), H)
            self.httpd.daemon_threads = True
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        host = local_ip_for(tv_ip)
        port = self.httpd.server_address[1]
        name = urllib.parse.quote(os.path.basename(path))
        return f"http://{host}:{port}/{self.token}/{name}"

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.path = ""


# ---------------------------------------------------------------- the hub (inside the main panel)

class _Bridge(QObject):
    """Delivers results from worker threads to the UI thread."""
    done = pyqtSignal(object, object)          # (callback, result)

    def __init__(self):
        super().__init__()
        self.done.connect(lambda cb, res: cb(res))

    def run(self, work, cb):
        threading.Thread(target=lambda: self.done.emit(cb, _safe(work)), daemon=True).start()


def _safe(work):
    try:
        return work()
    except Exception as e:
        return e


def is_sv(d) -> bool:
    """A Samsung TV (remote over the network) or a Miracast display (mirroring only)."""
    return bool(d) and d.get("kind") in ("samsung", "miracast")


def is_miracast(d) -> bool:
    return bool(d) and d.get("kind") == "miracast"


# Android key codes (what the panel's remote sends) → Samsung remote keys
ANDROID_KEYS = {
    19: "KEY_UP", 20: "KEY_DOWN", 21: "KEY_LEFT", 22: "KEY_RIGHT", 23: "KEY_ENTER", 66: "KEY_ENTER",
    4: "KEY_RETURN", 111: "KEY_RETURN", 3: "KEY_HOME", 82: "KEY_MENU", 24: "KEY_VOLUP", 25: "KEY_VOLDOWN",
    164: "KEY_MUTE", 26: "KEY_POWER", 223: "KEY_POWER", 85: "KEY_PLAY", 126: "KEY_PLAY",
    127: "KEY_PAUSE", 86: "KEY_STOP", 89: "KEY_REWIND", 90: "KEY_FF", 87: "KEY_FF", 88: "KEY_REWIND",
    166: "KEY_CHUP", 167: "KEY_CHDOWN", 178: "KEY_SOURCE", 165: "KEY_INFO", 172: "KEY_GUIDE",
    183: "KEY_RED", 184: "KEY_GREEN", 185: "KEY_YELLOW", 186: "KEY_CYAN",
    **{7 + i: f"KEY_{i}" for i in range(10)},
}
WAKEUP = 224

# Words in a Google TV app link / package → the Tizen app (by its name on the TV or a known id)
APP_WORDS = [
    ("youtube", "YouTube", "111299001912"), ("netflix", "Netflix", "3201907018807"),
    ("amazon", "Prime Video", "3201910019365"), ("primevideo", "Prime Video", "3201910019365"),
    ("disney", "Disney+", "3201901017640"), ("spotify", "Spotify", "3201606009684"),
    ("plex", "Plex", "3201512006963"), ("apple", "Apple TV", "3201807016597"),
    ("twitch", "Twitch", "3202203026841"), ("kinopoisk", "Кинопоиск", ""), ("ivi", "ivi", ""),
    ("okko", "Okko", ""), ("wink", "Wink", ""), ("vk", "VK Видео", ""),
]

_MEDIA_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".m3u8", ".mp3", ".m4a",
              ".flac", ".wav", ".ogg", ".aac", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")


class Hub(QObject):
    """Samsung / Miracast TVs as ordinary TVs of the main panel: State routes keys, typing,
    apps, casting and files here when such a TV is selected; the panel shows Mirror and the
    screen-layout buttons for them."""

    def __init__(self, state):
        super().__init__()
        self.state = state
        conf = state.conf.setdefault("samsung", {})
        conf.setdefault("tokens", {})
        self.conf = conf
        self.bridge = _Bridge()
        self.server = FileServer()
        self.remote = SamsungRemote(conf["tokens"], self._save)
        self.remote.state_changed.connect(self._on_remote)
        self.remote.apps_changed.connect(self._on_apps)
        self.apps: list[tuple] = []
        self.dlna_loc: dict[str, str] = {}
        self.tv_dev = None               # the TV's display once mirroring is on (Windows)
        self.mira_seen: dict[str, float] = {}   # Miracast name → when the last search saw it
        self._gnd_unit = None
        self._msg = None                 # the last status line Fling showed for this TV

    # ---- plumbing
    def _save(self):
        F.save_conf(self.state.conf)

    def _say(self, text):
        self._msg = F.tr(text) if text else None
        self.state._set(last_error=self._msg)

    def _clear(self):
        if self._msg and self.state.last_error == self._msg:
            self._msg = None
            self.state._set(last_error=None)

    @property
    def device(self):
        d = self.state.device
        return d if is_sv(d) else None

    @property
    def ready(self) -> bool:
        d = self.device
        return bool(d) and d.get("kind") == "samsung" and self.remote.state == "ready"

    def status_text(self) -> str:
        d = self.device
        if not d:
            return ""
        if is_miracast(d):
            return "Miracast · Wi‑Fi Direct — только зеркалирование экрана"
        return {"ready": "Samsung · пульт по сети", "waiting": "Нажмите «Разрешить» на экране ТВ",
                "connecting": "Подключаюсь к ТВ…"}.get(self.remote.state, "Samsung · ТВ не отвечает")

    # ---- discovery
    def scan(self, done):
        """Samsung TVs on the network and Miracast displays in range → done(list of devices)."""
        def work():
            box = {}
            t = threading.Thread(target=lambda: box.update(m=miracast_sinks()), daemon=True)
            t.start()
            tvs, dlna = discover()
            t.join(30)
            return tvs, dlna, box.get("m", [])

        def finish(res):
            if isinstance(res, Exception):
                print("smartview: scan", res, flush=True)
                done([])
                return
            tvs, dlna, mira = res
            self.dlna_loc.update(dlna)
            now = time.monotonic()
            self.mira_seen = {m["name"]: now for m in mira}
            out = [dict(t, kind="samsung") for t in tvs]
            names = {t["name"] for t in out}
            for m in mira:
                if m["name"] not in names:
                    out.append({"name": m["name"], "ip": "wfd:" + m["name"], "kind": "miracast",
                                "model": "Miracast · Wi‑Fi Direct"})
            done(out)
        self.bridge.run(work, finish)

    def probe(self, ip, done):
        """Add by IP: is it a Samsung? done(device or None)."""
        self.bridge.run(lambda: device_info(ip), lambda r: done(dict(r, kind="samsung") if isinstance(r, dict) else None))

    # ---- selection and the remote
    def select(self, d):
        self._clear()
        self.server.stop()               # the file shared with the previous TV stops being served
        if d and d.get("kind") == "samsung":
            self.remote.connect_to(d)
        else:
            self.remote.close()
        self.state.changed.emit()

    def _on_remote(self, st):
        if not self.device or is_miracast(self.device):
            return
        if st == "ready":
            self._clear()
        elif st == "waiting":
            self._say("Нажмите «Разрешить» на экране ТВ")
        elif st == "denied":
            self._say("ТВ не разрешил подключение. В настройках ТВ: Общие → Диспетчер внешних устройств → "
                      "Диспетчер подключения устройств — разрешите Fling.")
        elif st == "error":
            self._say("ТВ не отвечает — он выключен или в другой сети.")
        self.state.changed.emit()

    def _on_apps(self, apps):
        self.apps = apps

    def _reconnect(self):
        d = self.device
        if d and d.get("kind") == "samsung" and self.remote.state in ("off", "error"):
            self.remote.connect_to(d)

    def key(self, code: int):
        d = self.device
        if not d or is_miracast(d):
            return
        if code == WAKEUP or (code == 26 and self.remote.state in ("off", "error")):
            self.power_on()
            return
        name = ANDROID_KEYS.get(int(code))
        if name and not self.remote.key(name):
            self._reconnect()

    def text(self, s: str):
        if s and not self.remote.text(s):
            self._reconnect()

    def _app_for(self, link: str):
        low = (link or "").lower()
        for word, name, app_id in APP_WORDS:
            if word in low:
                for n, aid, typ in self.apps:              # the TV's own list first
                    if name.lower() in n.lower() or word in n.lower():
                        return aid, typ
                if app_id:
                    return app_id, 2
        return None, None

    def launch(self, link: str):
        aid, typ = self._app_for(link)
        if aid:
            self.remote.launch(aid, typ or 2)
        elif re.match(r"https?://", link or ""):
            self.remote.launch(BROWSER_APP, 4, link)
        else:
            self._say("Это приложение Fling не знает на Samsung — откройте его пультом.")

    def power_on(self):
        d = self.device
        if not d:
            return
        if not d.get("mac"):
            self._say("Не знаю MAC-адрес ТВ — включите его один раз пультом, чтобы Fling его запомнил.")
            return
        wake(d["mac"], d["ip"])
        self._say(_t("Включаю {name}…", name=d["name"]))
        tries = {"n": 0}

        def poll():
            tries["n"] += 1

            def done(info):
                if isinstance(info, dict) and info.get("power", "on") == "on":
                    self._clear()
                    self.remote.connect_to(d)
                elif tries["n"] < 10:
                    QTimer.singleShot(1500, poll)
                else:
                    self._say("Чтобы включать по сети, на ТВ: Общие → Сеть → Экспертные настройки → "
                              "«Включение с моб. устройства».")
            self.bridge.run(lambda: device_info(d["ip"], 1.5), done)
        QTimer.singleShot(1500, poll)

    # ---- casting: links, files (DLNA), the TV's browser
    def cast_url(self, url: str, title: str | None = None):
        d = self.device
        if not d or is_miracast(d):
            self._say("По Wi‑Fi Direct можно только зеркалировать экран — нажмите «Зеркалировать экран».")
            return
        path = urllib.parse.urlparse(url).path.lower()
        if path.endswith(_MEDIA_EXT):
            mime = mimetypes.guess_type(path)[0] or "video/mp4"
            self._dlna(url, title or urllib.parse.urlparse(url).netloc, mime)
            return
        yid = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]{11})", url)
        if yid and self.remote.launch("111299001912", 2, f"v={yid.group(1)}"):
            return
        if not self.remote.launch(BROWSER_APP, 4, url):
            self._reconnect()
            self._say("ТВ не отвечает — он выключен или в другой сети.")

    def send_files(self, paths):
        d = self.device
        if not d or is_miracast(d):
            self._say("По Wi‑Fi Direct можно только зеркалировать экран — нажмите «Зеркалировать экран».")
            return
        files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(_MEDIA_EXT)]
        if not files:
            self._say("На Samsung можно отправить видео, музыку или фото.")
            return
        path = files[0]
        url = self.server.share(path, d["ip"])
        self._dlna(url, os.path.basename(path), mimetypes.guess_type(path)[0] or "video/mp4")

    def _dlna(self, url, title, mime):
        d = self.device
        self._say(_t("Отправляю на ТВ: {title}", title=title))
        loc = self.dlna_loc.get(d["ip"])

        def work():
            control = d.get("dlna") or avtransport_url(d["ip"], loc)
            if not control:
                raise RuntimeError(F.tr("ТВ не поддерживает DLNA или он выключен."))
            d["dlna"] = control
            play_url(control, url, title, mime)

        def done(res):
            if isinstance(res, Exception):
                self._say(_t("ТВ не принял файл: {err}", err=res))
            else:
                self._clear()
        self.bridge.run(work, done)

    def transport(self, action):
        d = self.device
        if d and d.get("dlna"):
            args = "<Speed>1</Speed>" if action == "Play" else ""
            self.bridge.run(lambda: soap(d["dlna"], action, args), lambda _r: None)
        else:
            self.key({"Play": 126, "Pause": 127, "Stop": 86}[action])

    # ---- mirroring
    def mirror(self):
        d = self.device or {}
        name = d.get("name") or "Samsung"
        if d.get("power") == "standby" and d.get("mac"):
            wake(d["mac"], d["ip"])                       # wake first, mirror right after
        if P.IS_WIN:
            self._watch_tv_display()
            if not _send_win_k():
                try:
                    os.startfile("ms-settings-connectabledevices:devicediscovery")
                except OSError:
                    pass
            self._say(_t("Подключаюсь к {name}…", name=name))

            def picked(res):
                if res == "notfound":
                    self._say(_t("В окне «Трансляция» выберите {name}. Если ТВ спросит разрешение — "
                                 "нажмите «Разрешить» пультом.", name=name))
            self.bridge.run(lambda: cast_pick(name), picked)
            return
        if self._gnd_unit:
            stop_mirror_linux(self._gnd_unit)
            self._gnd_unit = None
            self.state.changed.emit()
            return

        def done(res):
            if isinstance(res, Exception):
                self._say(str(res))
                return
            how, unit = res
            if how == "started":
                self._gnd_unit = unit
                self._say(_t("Зеркалирование на {name} запущено.", name=name))
            elif how == "opened":
                self._say(_t("Открываю gnome-network-displays — выберите в нём {name}.", name=name))
            else:
                self._say("Для зеркалирования на Linux нужен gnome-network-displays. "
                          "Установите его из магазина приложений или Flathub.")
            self.state.changed.emit()
        self.bridge.run(lambda: mirror_linux(name), done)

    @property
    def mirroring(self) -> bool:
        return bool(self._gnd_unit) or (P.IS_WIN and bool(self.tv_dev) and self.tv_dev in win_displays())

    def _watch_tv_display(self):
        """After Mirror: the TV's display gets its largest 16:9 mode as soon as it shows up."""
        before = set(win_displays())
        tries = {"n": 0}

        def poll():
            tries["n"] += 1

            def done(res):
                if isinstance(res, tuple):
                    self.tv_dev = res[0]
                    if res[3]:
                        self._say(_t("Картинка на ТВ: {w}×{h}, без растяжения.", w=res[1], h=res[2]))
                    else:
                        self._clear()
                    if self.conf.get("layout"):
                        self.layout(self.conf["layout"], remember=False)
                    self.state.changed.emit()
                    return
                if tries["n"] < 60:                       # up to 2 minutes
                    QTimer.singleShot(2000, poll)
            self.bridge.run(lambda: fix_new_tv_display(before), done)
        QTimer.singleShot(2000, poll)

    def layout(self, mode, remember=True):
        if remember:
            self.conf["layout"] = mode                    # applied by itself after the next Mirror
            self._save()
        display_switch(mode)
        dev = self.tv_dev
        if dev and mode != "clone":                       # the switch may bring back a 4:3 mode
            QTimer.singleShot(2500, lambda: self.bridge.run(lambda: fix_display(dev), lambda _r: None))

    def close(self):
        self.remote.close()
        self.server.stop()
