# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Everything in Fling's desktop app that depends on the operating system.

fling_app.py is shared by Linux (KDE) and Windows; it never calls an OS tool
directly but goes through the functions here.  Keep this module free of Qt
imports at module level: `prepare_qt_env()` must run before PyQt6 loads.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import threading

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
FROZEN = bool(getattr(sys, "frozen", False))      # PyInstaller build (windows/build.ps1)

HOME = os.path.expanduser("~")
OS_NAME = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"
CLIENT_NAME = f"Fling ({OS_NAME})"                  # how the TV lists this remote

# ---------------------------------------------------------------- paths

if IS_WIN:
    _LOCAL = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
    _ROAMING = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
    DATA = os.path.join(_LOCAL, "Fling")            # pairing certs, venv, adb
    CACHE = os.path.join(_LOCAL, "Fling", "logs")
    CONF = os.path.join(_ROAMING, "Fling", "fling.json")
else:
    DATA = f"{HOME}/.local/share/fling"
    CACHE = f"{HOME}/.cache/fling"
    CONF = f"{HOME}/.config/fling.json"

LOG = os.path.join(CACHE, "app.log")

# Hide console windows of helper processes (catt, adb, tasklist) on Windows.
NOWIN: dict = {"creationflags": 0x08000000} if IS_WIN else {}   # CREATE_NO_WINDOW


def run(cmd, **kw) -> subprocess.CompletedProcess:
    """subprocess.run that never flashes a console window."""
    return subprocess.run(cmd, **NOWIN, **kw)


def popen(cmd, **kw) -> subprocess.Popen:
    return subprocess.Popen(cmd, **NOWIN, **kw)


def _exe(name: str) -> str:
    return name + ".exe" if IS_WIN else name


def find_catt() -> str:
    if FROZEN:                                   # the bundled build runs catt in-process: Fling.exe --catt …
        return sys.executable
    found = shutil.which("catt")
    if found:
        return found
    for p in (os.path.join(HOME, ".local", "bin", _exe("catt")),
              os.path.join(DATA, "venv", "Scripts" if IS_WIN else "bin", _exe("catt"))):
        if os.path.exists(p):
            return p
    return os.path.join(HOME, ".local", "bin", _exe("catt"))


def catt_cmd(catt: str, args: list[str]) -> list[str]:
    return [catt, "--catt", *args] if FROZEN else [catt, *args]


# Official Google platform-tools zips (ADB). Used when the binary is missing.
PLATFORM_TOOLS_PAGE = "https://developer.android.com/tools/releases/platform-tools"
_PLATFORM_TOOLS_ZIP = {
    "win": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "mac": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
}


def platform_tools_zip_url() -> str:
    if IS_WIN:
        return _PLATFORM_TOOLS_ZIP["win"]
    if IS_MAC:
        return _PLATFORM_TOOLS_ZIP["mac"]
    return _PLATFORM_TOOLS_ZIP["linux"]


# catt is a Python program. With its output going to a pipe, Python on Windows writes in
# the ANSI code page (cp1251/cp1252), so Cyrillic titles came back as mojibake and emoji
# crashed `catt info`. Force UTF-8, which is what catt_run decodes.
CATT_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _toolkit_choice(key: str) -> str | None:
    """The copy picked in the Tools window (fling_tools); None → the search below decides."""
    try:
        import fling_tools
        return fling_tools.resolve(key)
    except Exception as e:                       # the toolkit must never break finding a tool
        print("toolkit:", e, flush=True)
        return None


def find_adb() -> str:
    chosen = _toolkit_choice("adb")
    if chosen:
        return chosen
    found = shutil.which("adb")
    if found:
        return found
    cands = [os.path.join(DATA, "platform-tools", _exe("adb"))]
    if FROZEN:
        cands.insert(0, os.path.join(os.path.dirname(sys.executable), "platform-tools", _exe("adb")))
    if IS_WIN:
        cands.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe"))
    else:
        cands.append(f"{HOME}/Android/Sdk/platform-tools/adb")
    for p in cands:
        if os.path.exists(p):
            return p
    return cands[0] if cands else _exe("adb")


SCRCPY_DIR = os.path.join(DATA, "scrcpy")      # scrcpy downloaded by Fling on first use
SCRCPY_RELEASES = "https://api.github.com/repos/Genymobile/scrcpy/releases/latest"


def find_scrcpy() -> str | None:
    """scrcpy (TV screen + mouse): Fling's own download first, then the copy the setup
    ships next to Fling.exe, then PATH. A copy chosen in the Tools window wins."""
    chosen = _toolkit_choice("scrcpy")
    if chosen:
        return chosen
    own = os.path.join(SCRCPY_DIR, _exe("scrcpy"))
    if os.path.isfile(own):
        return own
    if FROZEN:
        shipped = os.path.join(os.path.dirname(sys.executable), "scrcpy", _exe("scrcpy"))
        if os.path.isfile(shipped):
            return shipped
    return shutil.which("scrcpy")


def scrcpy_asset_prefix() -> str | None:
    """Start of the release asset name for this computer, e.g. 'scrcpy-win64-'."""
    import platform
    m = platform.machine().lower()
    arm = m in ("arm64", "aarch64")
    if IS_WIN:
        return "scrcpy-winarm64-" if arm else ("scrcpy-win64-" if sys.maxsize > 2**32 else "scrcpy-win32-")
    if IS_MAC:
        return "scrcpy-macos-aarch64-" if arm else "scrcpy-macos-x86_64-"
    return None if arm else "scrcpy-linux-x86_64-"


def adb_available() -> bool:
    """True when an adb binary exists on disk (PATH, Fling data, or Android SDK)."""
    path = find_adb()
    return bool(path and os.path.exists(path))


# ---------------------------------------------------------------- Qt environment (call before importing PyQt6)

def _xwayland_scale() -> float:
    """KWin 'apply scaling themselves' for XWayland: match the desktop scale."""
    try:
        import configparser
        c = configparser.ConfigParser()
        c.read(os.path.expanduser("~/.config/kwinrc"))
        return float(c.get("Xwayland", "Scale", fallback="1"))
    except Exception:
        return 1.0


def prepare_qt_env() -> None:
    if IS_LINUX:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # popups need real positioning
        if os.environ["QT_QPA_PLATFORM"] == "xcb" and "QT_SCALE_FACTOR" not in os.environ:
            os.environ["QT_SCALE_FACTOR"] = str(_xwayland_scale())
            os.environ["QT_FONT_DPI"] = "96"
    elif IS_WIN:
        try:   # own taskbar / notification identity instead of "Python"
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Fling.Fling")
        except Exception:
            pass


def setup_logging() -> None:
    """pythonw.exe / the frozen build have no console: send output to the log file."""
    if sys.stdout is None or sys.stderr is None or (IS_WIN and FROZEN):
        try:
            os.makedirs(CACHE, exist_ok=True)
            if os.path.exists(LOG) and os.path.getsize(LOG) > 2_000_000:
                os.replace(LOG, LOG + ".old")
            f = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
            sys.stdout = sys.stderr = f
        except Exception:
            pass


def translucent_ok(conf: dict) -> bool:
    """Rounded, see-through panel corners need a compositing GPU path.  Remote Desktop,
    some VMs and old Intel/AMD drivers paint the transparent corners black — use a
    plain square panel there.  Override with "solid_panel": true/false in fling.json
    or FLING_SOLID_PANEL=1."""
    if os.environ.get("FLING_SOLID_PANEL") == "1" or conf.get("solid_panel") is True:
        return False
    if conf.get("solid_panel") is False:
        return True
    if IS_WIN:
        try:
            import ctypes
            if ctypes.windll.user32.GetSystemMetrics(0x1000):      # SM_REMOTESESSION
                return False
            enabled = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
            return bool(enabled.value)
        except Exception:
            return True
    return True


MONO_FONT = "Consolas" if IS_WIN else "Menlo" if IS_MAC else "monospace"

# ---------------------------------------------------------------- processes / files

def process_running(*names: str) -> bool:
    """Is any process with one of these executable names running?"""
    if IS_WIN:
        try:
            out = run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=4).stdout.lower()
        except Exception:
            return True          # can't tell: don't hide the tab
        return any(f'"{n.lower()}.exe"' in out for n in names)
    for n in names:
        if run(["pgrep", "-x", n], capture_output=True).returncode == 0:
            return True
    return any(run(["pgrep", "-f", f"/{n}/{n}"], capture_output=True).returncode == 0 for n in names)


def firefox_session_files() -> list[str]:
    if IS_WIN:
        roam = os.environ.get("APPDATA", "")
        return glob.glob(os.path.join(roam, "Mozilla", "Firefox", "Profiles", "*",
                                      "sessionstore-backups", "recovery.jsonlz4"))
    if IS_MAC:
        return glob.glob(f"{HOME}/Library/Application Support/Firefox/Profiles/*/sessionstore-backups/recovery.jsonlz4")
    return glob.glob(f"{HOME}/snap/firefox/common/.mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4") + \
        glob.glob(f"{HOME}/.mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4")


def open_path(path: str) -> None:
    """Open a file with its default app."""
    try:
        if IS_WIN:
            os.startfile(path)                     # noqa: the Windows shell
        elif IS_MAC:
            popen(["open", path])
        else:
            popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print("open:", e, flush=True)


# Notifications: Linux uses notify-send; on Windows the tray icon shows a toast.
# fling_app installs the tray-based notifier with set_notifier().
_notifier = None


def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


def notify(msg: str) -> None:
    if _notifier:
        _notifier(msg)
        return
    if IS_LINUX:
        popen(["notify-send", "-a", "Fling", "-i", "video-display", "Fling", msg], stderr=subprocess.DEVNULL)


def on_battery() -> bool:
    """True when the computer runs on battery (no AC adapter online)."""
    if IS_WIN:
        try:
            import ctypes

            class SPS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
            s = SPS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
                return s.ACLineStatus == 0
        except Exception:
            pass
        return False
    for f in glob.glob("/sys/class/power_supply/*/online"):
        try:
            if "AC" in f or "ADP" in f or "ACAD" in f:
                return open(f).read().strip() == "0"
        except Exception:
            pass
    return False


# ---------------------------------------------------------------- launching Fling itself

def self_command(*args: str) -> list[str]:
    """Command line that starts Fling with these arguments (for autostart / restart)."""
    if FROZEN:
        return [sys.executable, *args]
    script = os.path.abspath(sys.argv[0])
    if IS_WIN:
        py = sys.executable
        if os.path.basename(py).lower() == "fling.exe":         # installer's branded launcher
            return [py, script, *args]
        d = os.path.dirname(py)
        for name in ("Fling.exe", "pythonw.exe"):               # no console window
            w = os.path.join(d, name)
            if os.path.exists(w):
                return [w, script, *args]
        return [py, script, *args]
    return [os.path.join(HOME, ".local", "bin", "fling"), *args]


def restart_app() -> None:
    """Start a fresh Fling that waits for this one to exit (used after a language switch)."""
    if IS_LINUX:
        popen(["bash", "-c", f"sleep 1; exec {HOME}/.local/bin/fling show"], start_new_session=True)
        return
    flags = 0x00000008 | 0x00000200 if IS_WIN else 0      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(self_command("--after-restart", "show"), creationflags=flags, close_fds=True)


# ---------------------------------------------------------------- guard (Windows)
#
# fling-guard.exe (guard/, Rust) sits next to Fling.exe. It checks the install against
# its signed file list (fling.manifest + .minisig) and puts Fling into a Job Object, so
# every program Fling starts is checked: listed with the same hash, in an admin-only
# folder, or Authenticode-signed — anything else is stopped. Fling checks the guard
# first (same list, same key), so a swapped guard is not started.

GUARD_EXE = "fling-guard.exe"


def _release_pubkey() -> bytes | None:
    """The raw Ed25519 release key from security/fling-release.pub (bundled)."""
    import base64
    for base in (getattr(sys, "_MEIPASS", ""), os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")):
        p = os.path.join(base, "security", "fling-release.pub")
        try:
            with open(p, encoding="ascii") as f:
                line = [l.strip() for l in f if l.strip() and not l.startswith("untrusted comment:")][0]
            raw = base64.b64decode(line)
            return raw if len(raw) == 42 and raw[:2] == b"Ed" else None
        except (OSError, IndexError, ValueError):
            continue
    return None


def minisign_verify(data: bytes, sig_text: str, pub: bytes) -> str:
    """Check a minisign signature (prehashed Ed25519 "ED"); returns the trusted comment.
    Raises ValueError when it does not match."""
    import base64
    import hashlib
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    lines = sig_text.splitlines()
    if len(lines) < 4 or not lines[2].startswith("trusted comment: "):
        raise ValueError("not a minisign signature")
    sig = base64.b64decode(lines[1])
    glob_sig = base64.b64decode(lines[3])
    tc = lines[2][len("trusted comment: "):]
    if len(sig) != 74 or sig[:2] != b"ED" or sig[2:10] != pub[2:10] or len(glob_sig) != 64:
        raise ValueError("signature is not from the release key")
    key = Ed25519PublicKey.from_public_bytes(pub[10:])
    try:
        key.verify(sig[10:], hashlib.blake2b(data, digest_size=64).digest())
        key.verify(glob_sig, sig[10:] + tc.encode())
    except InvalidSignature:
        raise ValueError("the signature does not match") from None
    return tc


def guard_state(app_dir: str) -> tuple[str, str]:
    """("signed" | "unsigned" | "source" | "tampered", detail) for the install in app_dir.
    Quick: checks the list's signature and the guard's own hash only."""
    import hashlib
    man = os.path.join(app_dir, "fling.manifest")
    if not os.path.isfile(man):
        return "source", "no fling.manifest"
    with open(man, "rb") as f:
        data = f.read()
    pub = _release_pubkey()
    try:
        with open(man + ".minisig", encoding="utf-8") as f:
            sig = f.read()
        if not pub:
            raise ValueError("no release key in this build")
        lines = data.decode().splitlines()
        want_tc = f"fling-manifest version={lines[1][8:]} platform={lines[2][9:]}"
        if minisign_verify(data, sig, pub) != want_tc:
            raise ValueError("signed for something else")
        signed = True
    except (OSError, ValueError, IndexError, ImportError) as e:
        signed, why = False, str(e) or type(e).__name__
    entry = next((l.split("  ", 1)[0] for l in data.decode(errors="replace").splitlines()
                  if l.lower().endswith("  " + GUARD_EXE)), None)
    exe = os.path.join(app_dir, GUARD_EXE)
    if entry and os.path.isfile(exe):
        h = hashlib.sha256()
        with open(exe, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != entry.lower():
            return "tampered", f"{GUARD_EXE} differs from the list"
    return ("signed", "ok") if signed else ("unsigned", why)


def start_guard() -> str:
    """Start fling-guard for this process (Windows). Returns the install state."""
    if not IS_WIN or os.environ.get("FLING_NO_GUARD"):
        return "off"
    if FROZEN:
        app_dir = os.path.dirname(sys.executable)
    else:                                   # from source: guard/target/release, report only
        app_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "guard", "target", "release")
    exe = os.path.join(app_dir, GUARD_EXE)
    if not os.path.isfile(exe):
        return "missing"
    state, detail = guard_state(app_dir) if FROZEN else ("source", "")
    print(f"guard: {state} {detail}", flush=True)
    if state == "tampered":
        return state                        # never start a guard that is not ours
    try:
        subprocess.Popen([exe, "watch", str(os.getpid())], close_fds=True,
                         creationflags=0x00000008 | 0x00000200 | 0x08000000)  # detached, no window
    except OSError as e:
        print("guard:", e, flush=True)
        return "missing"
    return state


# ---------------------------------------------------------------- start at login

AUTOSTART = f"{HOME}/.config/autostart/fling.desktop"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def login_enabled() -> bool:
    if IS_WIN:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, "Fling")
            return True
        except OSError:
            return False
    return os.path.exists(AUTOSTART)


def toggle_login() -> None:
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if login_enabled():
                winreg.DeleteValue(k, "Fling")
            else:
                winreg.SetValueEx(k, "Fling", 0, winreg.REG_SZ, subprocess.list2cmdline(self_command("tray")))
        return
    if login_enabled():
        os.remove(AUTOSTART)
    else:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Fling\n"
                    f"Exec={HOME}/.local/bin/fling\nIcon=video-display\n"
                    "X-KDE-autostart-after=panel\n")


# ---------------------------------------------------------------- system-wide shortcut

# Linux: install.sh registers Meta+Shift+C with KDE (kglobalaccel → `fling cast-tab`).
# Windows: Fling registers Win+Shift+C itself (RegisterHotKey); if another app owns it,
# Ctrl+Alt+Shift+C is used instead.
if IS_WIN:
    GLOBAL_SHORTCUT, SHORTCUT_LABEL = "Meta+Shift+C", "Win+Shift+C"
elif IS_MAC:
    GLOBAL_SHORTCUT, SHORTCUT_LABEL = "Meta+Shift+C", "⌘⇧C"
else:
    GLOBAL_SHORTCUT, SHORTCUT_LABEL = "Meta+Shift+C", "Meta ⇧ C"


class GlobalHotkey:
    """Windows RegisterHotKey on a private message-loop thread.  `callback` runs on
    that thread — hop to the Qt thread yourself (fling_app uses a signal)."""
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

    def __init__(self, callback):
        self.callback = callback
        self.label = None                     # what actually got registered
        self._tid = None
        self._ready = threading.Event()

    def start(self) -> str | None:
        if not IS_WIN:
            return None
        threading.Thread(target=self._loop, daemon=True, name="fling-hotkey").start()
        self._ready.wait(2)
        return self.label

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        vk_c = 0x43
        for mods, lab in ((self.MOD_WIN | self.MOD_SHIFT, "Win+Shift+C"),
                          (self.MOD_CONTROL | self.MOD_ALT | self.MOD_SHIFT, "Ctrl+Alt+Shift+C")):
            if user32.RegisterHotKey(None, 1, mods | self.MOD_NOREPEAT, vk_c):
                self.label = lab
                break
        self._ready.set()
        if not self.label:
            print("hotkey: Win+Shift+C and Ctrl+Alt+Shift+C are both taken", flush=True)
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                try:
                    self.callback()
                except Exception as e:
                    print("hotkey:", e, flush=True)
        user32.UnregisterHotKey(None, 1)

    def stop(self):
        if IS_WIN and self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)


class RemoteKeyHook:
    """Windows: the laptop keyboard drives the TV while the Fling panel is open.

    The panel is a tray popup, and Windows never makes a tray popup the active
    window, so Qt gets no key events at all — the arrows keep going to the browser.
    A low-level keyboard hook (WH_KEYBOARD_LL) sees the keys whatever window has
    focus. It is installed only while the panel is open (`start` on show, `stop` on
    hide) and swallows only the remote keys below, only while `active` is True,
    and never with Ctrl / Alt / Win held, so normal shortcuts keep working.

    `on_key(vk, shift, repeat)` runs on the hook thread — hop to the Qt thread yourself.
    """
    VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
    VK_RETURN, VK_BACK, VK_SPACE, VK_HOME, VK_ESCAPE = 0x0D, 0x08, 0x20, 0x24, 0x1B
    VK_OEM_PLUS, VK_OEM_MINUS, VK_ADD, VK_SUBTRACT = 0xBB, 0xBD, 0x6B, 0x6D
    KEYS = {VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_RETURN, VK_BACK, VK_SPACE, VK_HOME,
            VK_ESCAPE, VK_OEM_PLUS, VK_OEM_MINUS, VK_ADD, VK_SUBTRACT}

    def __init__(self, on_key):
        self.on_key = on_key
        self.active = False              # set from the Qt thread; read by the hook
        self.keys = set(self.KEYS)       # which of KEYS to swallow right now (Qt thread sets it)
        self._lock = threading.Lock()
        self._thread = None
        self._tid = None
        self._down = set()               # keys we swallowed a key-down for

    def start(self) -> None:
        if not IS_WIN:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._down.clear()
            ready = threading.Event()
            self._thread = threading.Thread(target=self._loop, args=(ready,), daemon=True,
                                            name="fling-remote-keys")
            self._thread.start()
        ready.wait(1)

    def stop(self) -> None:
        if not IS_WIN:
            return
        with self._lock:
            t, tid = self._thread, self._tid
        if t and tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(tid, 0x0012, 0, 0)   # WM_QUIT
            t.join(1)

    def _loop(self, ready):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)        # private copy: our argtypes only
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
        LLKHF_INJECTED = 0x10

        def held(vk):
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        def proc(code, wparam, lparam):
            try:
                if code == 0 and self.active:
                    kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = kb.vkCode
                    if vk in self.keys and not kb.flags & LLKHF_INJECTED:
                        down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                        if down and not (held(0x11) or held(0x12) or held(0x5B) or held(0x5C)):
                            repeat = vk in self._down
                            self._down.add(vk)
                            self.on_key(vk, held(0x10), repeat)
                            return 1                          # swallow: the TV gets it, not the browser
                        if not down and vk in self._down:
                            self._down.discard(vk)
                            return 1
            except Exception as e:
                print("remote keys:", e, flush=True)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        cb = HOOKPROC(proc)                                   # keep a reference while the hook lives
        hook = user32.SetWindowsHookExW(13, cb, kernel32.GetModuleHandleW(None), 0)   # WH_KEYBOARD_LL
        self._tid = kernel32.GetCurrentThreadId()
        ready.set()
        if not hook:
            print("remote keys: SetWindowsHookEx failed", ctypes.get_last_error(), flush=True)
            self._tid = None
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)
        self._tid = None
        self._down.clear()


class MouseEdgeRelease:
    """Windows, LIVE: give the mouse back as soon as it leaves the TV picture.

    With --mouse=uhid scrcpy captures the mouse (relative mode): Windows' own cursor is
    hidden and clipped to one pixel in the middle of scrcpy's window, so it can never
    "leave". This follows the real mouse movement from where the capture began and, once
    the mouse would have crossed the edge of the picture, presses LAlt — scrcpy's release
    key — and puts the cursor just outside that edge, so the mouse simply carries on.

    The movement comes from Raw Input (WM_INPUT, RIDEV_INPUTSINK) on a message-only window
    of its own thread: Windows posts it and never waits for Fling, so the system cursor
    can't lag the way a low-level mouse hook in Python makes it lag.

    `rect` = (left, top, right, bottom) of the picture in physical pixels; Qt sets it."""

    def __init__(self, hwnd: int):
        self.hwnd = int(hwnd)
        self.rect = None
        self._thread = None
        self._tid = None
        self._est = None            # where the mouse would be if it weren't captured
        self._busy = False
        self._scale = 1.0           # mickeys → pixels (the "pointer speed" setting)

    def start(self) -> None:
        if not IS_WIN or (self._thread and self._thread.is_alive()):
            return
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(ready,), daemon=True, name="fling-mouse-edge")
        self._thread.start()
        ready.wait(1)

    def stop(self) -> None:
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT
        self._tid = None

    def _release(self, x, y):
        """LAlt (scrcpy's capture toggle) — only into scrcpy's own window — then the cursor
        just past the edge it crossed."""
        import ctypes, time as _t
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        try:
            if (user32.GetForegroundWindow() or 0) != self.hwnd:
                return                     # focus moved on: an Alt there would open some app's menu
            user32.keybd_event(0xA4, 0, 0, 0)
            user32.keybd_event(0xA4, 0, 2, 0)
            _t.sleep(0.08)                 # let SDL leave relative mode first
            user32.SetCursorPos(int(x), int(y))
        finally:
            self._busy = False

    def _loop(self, ready):
        import ctypes
        from ctypes import wintypes as wt
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32")
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                        ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]

        class RAWINPUTHEADER(ctypes.Structure):
            _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD), ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]

        class RAWMOUSE(ctypes.Structure):
            _fields_ = [("usFlags", wt.USHORT), ("_pad", wt.USHORT), ("ulButtons", wt.ULONG),
                        ("ulRawButtons", wt.ULONG), ("lLastX", wt.LONG), ("lLastY", wt.LONG),
                        ("ulExtraInformation", wt.ULONG)]

        class RAWINPUT(ctypes.Structure):
            _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]

        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID, ctypes.POINTER(wt.UINT), wt.UINT]
        user32.GetClipCursor.argtypes = [ctypes.POINTER(wt.RECT)]
        user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        WM_INPUT, RID_INPUT, MOUSE_MOVE_ABSOLUTE = 0x00FF, 0x10000003, 0x01
        buttons = (0x01, 0x02, 0x04)       # VK_LBUTTON, VK_RBUTTON, VK_MBUTTON
        speed = wt.UINT(10)
        user32.SystemParametersInfoW(0x0070, 0, ctypes.byref(speed), 0)     # SPI_GETMOUSESPEED, 1..20
        s = speed.value
        self._scale = s / 10 if s <= 10 else 1 + (s - 10) * 0.25          # Windows' own speed table

        def captured():
            clip, win = wt.RECT(), wt.RECT()
            user32.GetClipCursor(ctypes.byref(clip))
            if not user32.GetWindowRect(self.hwnd, ctypes.byref(win)):
                return False
            small = clip.right - clip.left <= 4 and clip.bottom - clip.top <= 4
            return small and win.left <= clip.left <= win.right and win.top <= clip.top <= win.bottom

        def on_move(dx, dy):
            if not self.rect or self._busy:
                return
            if not captured():
                self._est = None
                return
            l, t, r, b = self.rect
            if self._est is None:          # capture just began: from where the cursor was clicked
                p = wt.POINT(); user32.GetCursorPos(ctypes.byref(p))
                self._est = [float(p.x), float(p.y)] if l <= p.x < r and t <= p.y < b \
                    else [(l + r) / 2, (t + b) / 2]
            self._est[0] += dx * self._scale
            self._est[1] += dy * self._scale
            x, y = self._est
            held = any(user32.GetAsyncKeyState(k) & 0x8000 for k in buttons)
            if not held and not (l - 2 <= x < r + 2 and t - 2 <= y < b + 2):
                out_x = l - 8 if x < l else r + 8 if x >= r else x
                out_y = t - 8 if y < t else b + 8 if y >= b else y
                self._busy = True
                self._est = None
                threading.Thread(target=self._release, args=(out_x, out_y), daemon=True).start()

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                try:
                    ri = RAWINPUT()
                    size = wt.UINT(ctypes.sizeof(ri))
                    if user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(ri), ctypes.byref(size),
                                              ctypes.sizeof(RAWINPUTHEADER)) not in (0, 0xFFFFFFFF) \
                            and ri.header.dwType == 0 and not ri.mouse.usFlags & MOUSE_MOVE_ABSOLUTE \
                            and (ri.mouse.lLastX or ri.mouse.lLastY):
                        on_move(ri.mouse.lLastX, ri.mouse.lLastY)
                except Exception as e:
                    print("mouse edge:", e, flush=True)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(wndproc)
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = f"FlingMouseEdge{id(self)}"
        wc = WNDCLASSW(); wc.lpfnWndProc = proc; wc.hInstance = hinst; wc.lpszClassName = cls_name
        user32.RegisterClassW(ctypes.byref(wc))
        win = user32.CreateWindowExW(0, cls_name, None, 0, 0, 0, 0, 0, wt.HWND(-3), None, hinst, None)  # HWND_MESSAGE
        dev = RAWINPUTDEVICE(0x01, 0x02, 0x00000100, win)                  # mouse, RIDEV_INPUTSINK
        ok = win and user32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev))
        self._tid = kernel32.GetCurrentThreadId()
        ready.set()
        if not ok:
            print("mouse edge: no raw input", ctypes.get_last_error(), flush=True)
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        rm = RAWINPUTDEVICE(0x01, 0x02, 0x00000001, None)                  # RIDEV_REMOVE
        user32.RegisterRawInputDevices(ctypes.byref(rm), 1, ctypes.sizeof(rm))
        user32.DestroyWindow(win)
        user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
        user32.UnregisterClassW(cls_name, hinst)


def ping_ms(ip: str, timeout_ms: int = 800) -> float | None:
    """Round trip to `ip` in ms, None when it doesn't answer. Windows: IcmpSendEcho (no
    process, no locale-dependent output); elsewhere: `ping -c 1`."""
    import socket, struct
    if not IS_WIN:
        import re as _re
        try:
            r = run(["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), ip],
                    capture_output=True, text=True, timeout=timeout_ms / 1000 + 2)
            m = _re.search(r"time[=<]([\d.]+)", r.stdout)
            return float(m[1]) if m else None
        except Exception:
            return None
    import ctypes
    iph = ctypes.windll.iphlpapi
    iph.IcmpCreateFile.restype = ctypes.c_void_p
    iph.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint16,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    iph.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
    try:
        addr = struct.unpack("<I", socket.inet_aton(ip))[0]
    except OSError:
        return None
    h = iph.IcmpCreateFile()
    if not h:
        return None
    try:
        data = b"fling-live"
        reply = ctypes.create_string_buffer(64 + len(data) + 8)
        n = iph.IcmpSendEcho(h, addr, data, len(data), None, reply, len(reply), timeout_ms)
        if not n:
            return None
        status, rtt = struct.unpack_from("<II", reply, 4)     # ICMP_ECHO_REPLY: Address, Status, RoundTripTime
        return float(rtt) if status == 0 else None
    finally:
        iph.IcmpCloseHandle(h)


def force_foreground(win_id) -> bool:
    """Windows: make a Fling window the foreground window so its text fields get the
    keyboard. A tray app isn't allowed to by default; attaching to the current
    foreground thread's input for a moment is the usual way around it."""
    if not IS_WIN:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetFocus.argtypes = [wintypes.HWND]
    hwnd = wintypes.HWND(int(win_id))
    fg = user32.GetForegroundWindow()
    if fg and fg == hwnd.value:
        return True
    me = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other and other != me and user32.AttachThreadInput(other, me, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(other, me, False)
    return user32.GetForegroundWindow() == hwnd.value



# ---------------------------------------------------------------- microphone (voice search: 8 kHz, 16-bit, mono PCM)

class _Resampler:
    """int16 interleaved PCM at any rate / channel count → 8 kHz mono, linear
    interpolation with the fractional position carried across blocks."""
    OUT = 8000

    def __init__(self, rate: int, channels: int):
        self.step = rate / self.OUT
        self.ch = channels
        self.pos = 0.0          # read position (in input frames) relative to the current block
        self.prev = 0           # last sample of the previous block

    def feed(self, data: bytes) -> bytes:
        import array
        a = array.array("h", data)
        if self.ch > 1:
            a = a[::self.ch]                       # first channel
        if self.step == 1:
            return a.tobytes()
        n = len(a)
        out = array.array("h")
        pos = self.pos
        while pos < n - 1:
            i = int(pos)
            frac = pos - i
            s0 = a[i] if i >= 0 else self.prev
            out.append(int(s0 + (a[i + 1] - s0) * frac))
            pos += self.step
        self.pos = pos - n
        if n:
            self.prev = a[-1]
        return out.tobytes()


class MicRecorder:
    """Streams raw PCM chunks to `on_chunk(bytes)` from a background thread."""

    def __init__(self):
        self._proc = None
        self._stream = None

    @staticmethod
    def available() -> str | None:
        """None when recording can work, else a (Russian, translated by the app) reason."""
        if IS_WIN or IS_MAC:
            try:
                import sounddevice  # noqa: F401
                return None
            except Exception:
                return "Нет модуля sounddevice для записи микрофона."
        if shutil.which("parecord") or shutil.which("pw-record"):
            return None
        return "Нет parecord/pw-record для записи микрофона."

    def start(self, on_chunk) -> None:
        if IS_WIN or IS_MAC:
            self._start_sounddevice(on_chunk)
            return
        rec = shutil.which("parecord") or shutil.which("pw-record")
        if rec.endswith("parecord"):
            cmd = [rec, "--raw", "--format=s16le", "--rate=8000", "--channels=1"]
        else:
            cmd = [rec, "--format=s16", "--rate=8000", "--channels=1", "-"]
        self._proc = proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def pump():
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                on_chunk(chunk)
        threading.Thread(target=pump, daemon=True).start()

    def _start_sounddevice(self, on_chunk):
        import sounddevice as sd
        last = None
        # Laptop microphones differ a lot between vendors: some drivers resample to 8 kHz
        # themselves (MME usually does), Realtek/Intel/AMD array mics often accept only
        # 44.1/48 kHz and only 2 or 4 channels.  Try the cheapest format first, then the
        # device's own; downmix to the first channel and resample to 8 kHz here.
        try:
            dev = sd.query_devices(kind="input")
            native_rate = int(dev.get("default_samplerate") or 48000)
            max_ch = max(1, int(dev.get("max_input_channels") or 1))
        except Exception as e:
            raise RuntimeError(f"Микрофон недоступен: {e}")
        rates = list(dict.fromkeys((8000, 16000, native_rate, 48000, 44100)))
        chans = list(dict.fromkeys((1, min(2, max_ch), max_ch)))
        for ch in chans:
            for rate in rates:
                conv = _Resampler(rate, ch)
                try:
                    self._stream = sd.RawInputStream(samplerate=rate, channels=ch, dtype="int16",
                                                     blocksize=rate // 10,
                                                     callback=lambda d, f, t, s, c=conv: on_chunk(c.feed(bytes(d))))
                    self._stream.start()
                    print(f"mic: {rate} Hz × {ch} ch", flush=True)
                    return
                except Exception as e:
                    last = e
                    self._stream = None
        raise RuntimeError(f"Микрофон недоступен: {last}")

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            self._proc = None
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


# ---------------------------------------------------------------- pictures folder

def downloads_dir() -> str:
    """The user's Downloads folder (Windows: wherever it was moved to), else home."""
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                d = os.path.expandvars(winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
            if os.path.isdir(d):
                return d
        except OSError:
            pass
    d = os.path.join(HOME, "Downloads")
    return d if os.path.isdir(d) else HOME


def pictures_dir() -> str:
    if IS_LINUX:
        try:
            p = run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True).stdout.strip()
            if p:
                return p
        except Exception:
            pass
    try:
        from PyQt6.QtCore import QStandardPaths
        p = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        if p:
            return p
    except Exception:
        pass
    return os.path.join(HOME, "Pictures")

