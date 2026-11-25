# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Toolkit: the outside programs Fling runs (adb, scrcpy) and the Python parts it casts with.

  * a registry - one declarative `Tool` entry per program (what it is for, licence,
    homepage, how to read its version, where to look for it, where to download it);
  * detection - copies already on the computer (PATH, Android SDK, WinGet, Scoop,
    Chocolatey, Homebrew, Linux packages, Fling's own folders) and their versions;
  * the choice - which copy Fling uses (stored in tools.json next to fling.json); by
    default the newest compatible one;
  * an installer - HTTPS download (resumable), checksum check, safe unpacking, a test run,
    then an atomic swap into Fling's data folder with rollback; progress goes to a
    shared job board that any window can watch;
  * the "Tools" window / page (Qt).  Its look comes from fling_app through bind_ui(), so
    another window (Settings) can embed ToolsPage as is.

The detection / install half never touches Qt and can run on any thread.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse

import fling_platform as P

# ---------------------------------------------------------------- places

ROOT = P.DATA                                        # Fling's tools live in ROOT/<tool.dir>
STORE = os.path.join(os.path.dirname(P.CONF), "tools.json")   # which copy to use, latest versions
MARK = ".fling-tool.json"                            # written into every folder Fling installed
UA = "Fling-toolkit"
MAX_DOWNLOAD = 300 << 20                             # sanity limit for one archive
LATEST_TTL = 6 * 3600                                # how long a "latest version" answer is reused


class ToolError(Exception):
    """A failure the user should read (Russian text; the UI translates it)."""


class Cancelled(Exception):
    pass


def _exe(name: str) -> str:
    return name + ".exe" if P.IS_WIN else name


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _unc(p: str) -> bool:
    """\\\\server\\share and //server paths: never run or install from the network."""
    return p.startswith(("\\\\", "//"))


def parse_version(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


# ---------------------------------------------------------------- the registry

@dataclass
class Release:
    version: str
    url: str
    name: str                     # archive file name
    size: int = 0
    sha256: str | None = None     # from the publisher (scrcpy: SHA256SUMS.txt)
    sha1: str | None = None       # from the publisher (Google's SDK repository)
    page: str = ""


@dataclass
class Copy:
    path: str                     # the executable ("" for something built into Fling)
    source: str                   # fling | bundled | path | sdk | winget | scoop | choco | brew | system | custom | builtin
    version: str | None = None
    owned: bool = False           # Fling installed it: may be updated / removed
    portable: bool = False        # its folder can be copied into Fling's folder
    record: dict | None = None    # MARK contents

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path) if self.path else ""


@dataclass
class Tool:
    key: str
    title: str                    # shown as is (program names are not translated)
    purpose: str                  # Russian, translated by the UI
    license: str
    license_url: str
    homepage: str
    exe: str = ""                 # program name without .exe; "" = Python package
    dir: str = ""                 # install folder name inside ROOT
    version_args: tuple = ("--version",)
    version_re: str = r"(\d+(?:\.\d+)+)"
    min_version: str = ""         # older copies are listed but not picked
    places: Callable[[], list] | None = None          # -> [(exe path, source)]
    latest: Callable[[], Release] | None = None       # None: Fling can't install it
    builtin: Callable[[], Copy | None] | None = None  # a copy inside Fling's own Python
    portable_marker: str = ""     # a file next to the exe that says "the whole folder is the tool"
    before_swap: Callable[[str], None] | None = None  # free the folder before it is replaced
    note: str = ""                # Russian, shown when Fling can't install it itself

    @property
    def installable(self) -> bool:
        return self.latest is not None

    @property
    def target(self) -> str:
        return os.path.join(ROOT, self.dir) if self.dir else ""

    def own_exe(self) -> str:
        return os.path.join(self.target, _exe(self.exe))


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    TOOLS[tool.key] = tool
    return tool


# ---------------------------------------------------------------- HTTPS

class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise ToolError("сервер перенаправил загрузку не на HTTPS — отменено")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnly)


def _open(url: str, timeout: float = 30, headers: dict | None = None):
    if urlparse(url).scheme != "https":
        raise ToolError("загрузка возможна только по HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        return _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416:
            raise
        raise ToolError(f"сервер ответил {e.code}") from e
    except urllib.error.URLError as e:
        raise ToolError("нет связи с сервером загрузки") from e


def _get_bytes(url: str, timeout: float = 30, limit: int = 8 << 20) -> bytes:
    with _open(url, timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ToolError("ответ сервера слишком большой")
    return data


# ---------------------------------------------------------------- where tools are found

def _env_dirs(*names: str) -> list[str]:
    return [os.environ[n] for n in names if os.environ.get(n)]


def _search(exe_name: str, sub: tuple = ("",), sdk: bool = False, extra: list | None = None) -> list:
    """Every usual place a program can be installed by hand or by a package manager.

    sub: sub-folders inside a package folder that may hold the program."""
    exe = _exe(exe_name)
    out: list[tuple[str, str]] = []

    def add(pattern: str, source: str):
        for p in sorted(glob.glob(pattern)) if any(c in pattern for c in "*?[") else [pattern]:
            out.append((p, source))

    found = shutil.which(exe_name)
    if found:
        out.append((found, "path"))
    for pth, src in extra or []:
        out.append((pth, src))
    if sdk:
        roots = _env_dirs("ANDROID_HOME", "ANDROID_SDK_ROOT")
        if P.IS_WIN:
            roots.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"))
        elif P.IS_MAC:
            roots.append(os.path.join(P.HOME, "Library", "Android", "sdk"))
        else:
            roots += [os.path.join(P.HOME, "Android", "Sdk"), "/opt/android-sdk", "/usr/lib/android-sdk"]
        for r in roots:
            add(os.path.join(r, "platform-tools", exe), "sdk")
    if P.IS_WIN:
        local = os.environ.get("LOCALAPPDATA", os.path.join(P.HOME, "AppData", "Local"))
        wg = os.path.join(local, "Microsoft", "WinGet")
        for s in sub:
            add(os.path.join(wg, "Packages", "*", s, exe), "winget")
            add(os.path.join(wg, "Packages", "*", "*", s, exe), "winget")
        add(os.path.join(wg, "Links", exe), "winget")
        for scoop in _env_dirs("SCOOP") + [os.path.join(P.HOME, "scoop"), os.path.join(
                os.environ.get("ProgramData", r"C:\ProgramData"), "scoop")]:
            for s in sub:
                add(os.path.join(scoop, "apps", "*", "current", s, exe), "scoop")
        choco = os.environ.get("ChocolateyInstall") or os.path.join(
            os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey")
        for s in sub:
            add(os.path.join(choco, "lib", "*", "tools", s, exe), "choco")
            add(os.path.join(choco, "lib", "*", "tools", "*", s, exe), "choco")
        add(os.path.join(choco, "bin", exe), "choco")
    elif P.IS_MAC:
        for d in ("/opt/homebrew/bin", "/usr/local/bin"):
            add(os.path.join(d, exe), "brew")
    else:
        for d in ("/usr/bin", "/usr/local/bin", "/snap/bin", "/var/lib/flatpak/exports/bin"):
            add(os.path.join(d, exe), "system")
    return out


def _fling_install_dirs() -> list[str]:
    """Folders of an installed Fling (the Windows setup ships adb and scrcpy next to Fling.exe)."""
    dirs = []
    if P.FROZEN:
        dirs.append(os.path.dirname(sys.executable))
    if P.IS_WIN:
        dirs.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Fling"))
    return dirs


# Shim folders hold launchers, not the program: they can be used but not copied.
def _shim(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return any(s in p for s in ("/chocolatey/bin/", "/scoop/shims/", "/winget/links/")) or \
        p.startswith(("/usr/", "/snap/", "/var/lib/flatpak/", "/opt/homebrew/bin/"))


# ---------------------------------------------------------------- tools.json (the choice)

_STORE_LOCK = threading.Lock()


def _load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    tmp = f"{STORE}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)
        os.replace(tmp, STORE)
    except OSError as e:
        print("tools.json:", e, flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


def _update(fn) -> dict:
    with _STORE_LOCK:
        d = _load()
        fn(d)
        _save(d)
        return d


def choice(key: str) -> dict:
    c = _load().get("use", {}).get(key)
    return c if isinstance(c, dict) else {}


def resolve(key: str) -> str | None:
    """The copy Fling should run, or None (then fling_platform's own search decides).
    Fast: one small file read, no subprocess."""
    p = choice(key).get("path")
    if p and not _unc(p) and os.path.isfile(p):
        return p
    return None


def choose(key: str, path: str, pinned: bool = True) -> None:
    """Remember the copy to use. pinned=False: an automatic pick a newer copy may replace."""
    if _unc(path):
        raise ToolError("программы из сетевых папок не запускаются")
    _update(lambda d: d.setdefault("use", {}).__setitem__(key, {"path": path, "pinned": pinned}))
    _changed()


def forget(key: str) -> None:
    _update(lambda d: d.setdefault("use", {}).pop(key, None))
    _changed()


# ---------------------------------------------------------------- versions

def _run_version(tool: Tool, exe: str) -> str | None:
    if _unc(exe):
        return None
    try:
        r = P.run([exe, *tool.version_args], capture_output=True, text=True, timeout=8,
                  encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(tool.version_re, (r.stdout or "") + "\n" + (r.stderr or ""), re.M)
    return m.group(1) if m else None


def _read_mark(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, MARK), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def compatible(tool: Tool, c: Copy) -> bool:
    if not tool.min_version or not c.version:
        return True
    return parse_version(c.version) >= parse_version(tool.min_version)


_SCAN: dict[str, list[Copy]] = {}


def scan(key: str) -> list[Copy]:
    """All copies of a tool on this computer, best first. Runs each one's version
    command, so call it off the UI thread."""
    tool = TOOLS[key]
    copies: list[Copy] = []
    if tool.builtin:
        try:
            b = tool.builtin()
        except Exception:
            b = None
        if b:
            copies.append(b)
    seen = set()
    cands = []
    for path, source in (tool.places() if tool.places else []):
        if not path or _unc(path) or not os.path.isfile(path):
            continue
        n = _norm(path)
        if n in seen:
            continue
        seen.add(n)
        cands.append((path, source))
    pinned = choice(key).get("path")
    if pinned and not _unc(pinned) and os.path.isfile(pinned) and _norm(pinned) not in seen:
        cands.append((pinned, "custom"))
    own = _norm(tool.target) if tool.target else None
    with ThreadPoolExecutor(max_workers=4) as ex:
        versions = list(ex.map(lambda ps: _run_version(tool, ps[0]), cands))
    for (path, source), ver in zip(cands, versions):
        folder = os.path.dirname(path)
        owned = bool(own) and _norm(folder) == own
        portable = not _shim(path) and (not tool.portable_marker or
                                        os.path.exists(os.path.join(folder, tool.portable_marker)))
        copies.append(Copy(path, "fling" if owned else source, ver, owned, portable and not owned,
                           _read_mark(folder) if owned else None))
    rank = {"fling": 3, "bundled": 2, "builtin": 2}
    copies.sort(key=lambda c: (compatible(tool, c), parse_version(c.version), rank.get(c.source, 0)),
                reverse=True)
    _SCAN[key] = copies
    return copies


def best(key: str, copies: list[Copy] | None = None) -> Copy | None:
    tool = TOOLS[key]
    copies = _SCAN.get(key, []) if copies is None else copies
    ok = [c for c in copies if c.path and compatible(tool, c)]
    return ok[0] if ok else None


def auto_pick(key: str, copies: list[Copy]) -> None:
    """Unless the user pinned a copy, use the newest compatible one."""
    c = choice(key)
    if c.get("pinned") and c.get("path") and os.path.isfile(c["path"]):
        return
    b = best(key, copies)
    if b and _norm(b.path) != _norm(c.get("path") or "~"):
        choose(key, b.path, pinned=False)
    elif not b and c.get("path") and not os.path.isfile(c["path"]):
        forget(key)


def scan_all(pick: bool = True) -> dict[str, list[Copy]]:
    out = {}
    for key, tool in TOOLS.items():
        out[key] = scan(key)
        if pick and tool.exe and tool.dir:
            auto_pick(key, out[key])
    return out


def first_run_scan() -> None:
    """Background, once per start: pick a copy for tools that have no (valid) choice yet."""
    def go():
        for key, tool in TOOLS.items():
            if tool.exe and tool.dir and not resolve(key):
                try:
                    auto_pick(key, scan(key))
                except Exception as e:
                    print("toolkit scan:", key, e, flush=True)
    threading.Thread(target=go, name="toolkit-scan", daemon=True).start()


# ---------------------------------------------------------------- latest versions

def latest(key: str, fresh: bool = False) -> Release | None:
    """What the publisher offers now (cached for LATEST_TTL in tools.json)."""
    tool = TOOLS[key]
    if not tool.latest:
        return None
    cached = _load().get("latest", {}).get(key)
    if not fresh and isinstance(cached, dict) and time.time() - cached.get("t", 0) < LATEST_TTL:
        try:
            return Release(**cached["rel"])
        except Exception:
            pass
    rel = tool.latest()
    _update(lambda d: d.setdefault("latest", {}).__setitem__(key, {"t": time.time(), "rel": rel.__dict__}))
    return rel


# ---------------------------------------------------------------- the job board

@dataclass
class Job:
    key: str
    action: str                       # install | absorb | remove
    phase: str = "start"              # start | meta | download | verify | unpack | copy | test | swap | done
    done: int = 0                     # bytes downloaded / files unpacked
    total: int = 0
    speed: float = 0.0                # bytes per second
    state: str = "run"                # run | ok | error | cancelled
    message: str = ""                 # Russian
    version: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    tick: int = 0
    serial: int = field(default_factory=lambda: next(_SERIAL))


_SERIAL = __import__("itertools").count(1)
_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_LISTENERS: list[Callable[[], None]] = []
REVISION = [0]                        # bumps on every change the UI should redraw for


def on_change(fn: Callable[[], None]) -> None:
    """fn() runs (on any thread) after a tool was installed, removed or chosen."""
    _LISTENERS.append(fn)


def _changed() -> None:
    REVISION[0] += 1
    for fn in list(_LISTENERS):
        try:
            fn()
        except Exception as e:
            print("toolkit listener:", e, flush=True)


def job(key: str) -> Job | None:
    return _JOBS.get(key)


def busy(key: str | None = None) -> bool:
    return any(j.state == "run" for k, j in _JOBS.items() if key in (None, k))


def start(key: str, action: str = "install", copy: Copy | None = None) -> Job:
    """Start (or join) a job on its own thread; watch it with job(key)."""
    with _JOBS_LOCK:
        j = _JOBS.get(key)
        if j and j.state == "run":
            return j
        j = _JOBS[key] = Job(key, action)
    REVISION[0] += 1
    threading.Thread(target=_work, args=(j, copy), name=f"toolkit-{key}", daemon=True).start()
    return j


def run_now(key: str, action: str = "install") -> Copy:
    """Blocking install on the calling (background) thread; shows on the job board too."""
    j = start(key, action)
    j.finished.wait()
    if j.state != "ok":
        raise ToolError(j.message or "не вышло")
    path = resolve(key) or TOOLS[key].own_exe()
    return Copy(path, "fling", j.version, owned=True)


def _work(j: Job, copy: Copy | None):
    tool = TOOLS[j.key]
    try:
        if j.action == "install":
            j.version = _install(tool, j)
            j.message = "Установлено: {name} {version}."
        elif j.action == "absorb":
            j.version = _absorb(tool, j, copy)
            j.message = "Скопировано в папку Fling: {name} {version}."
        elif j.action == "remove":
            _remove(tool, j)
            j.message = "Удалено: {name}."
        j.state, j.phase = "ok", "done"
    except Cancelled:
        j.state, j.message = "cancelled", "Отменено."
    except ToolError as e:
        j.state, j.message = "error", str(e)
    except Exception as e:                       # unexpected: keep the details for the log
        print(f"toolkit {j.key} {j.action}:", repr(e), flush=True)
        j.state, j.message = "error", f"{type(e).__name__}: {e}"
    finally:
        j.finished.set()
        print(f"toolkit {j.key} {j.action}: {j.state} {j.message} {j.version}", flush=True)
        if j.state == "ok":
            _SCAN.pop(j.key, None)
            _changed()
        else:
            REVISION[0] += 1


def _step(j: Job, phase: str, done: int = 0, total: int = 0):
    if j.cancel.is_set():
        raise Cancelled()
    j.phase, j.done, j.total = phase, done, total
    j.tick += 1


# ---------------------------------------------------------------- download / verify / unpack

def _download(rel: Release, j: Job) -> tuple[str, str]:
    """Archive into ROOT/.downloads (a cancelled download resumes); returns (path, sha256)."""
    if not rel.sha256 and not rel.sha1:
        raise ToolError("издатель не дал контрольную сумму — устанавливать не буду")
    if rel.size and rel.size > MAX_DOWNLOAD:
        raise ToolError("архив подозрительно большой — отменено")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", rel.name)
    folder = os.path.join(ROOT, ".downloads")
    os.makedirs(folder, exist_ok=True)
    part = os.path.join(folder, safe_name + ".part")
    meta = part + ".json"
    have = 0
    try:
        with open(meta, encoding="utf-8") as f:
            m = json.load(f)
        if m.get("url") == rel.url and m.get("sha256") == rel.sha256 and m.get("sha1") == rel.sha1:
            have = os.path.getsize(part)
    except Exception:
        pass
    if not have:
        for p in (part, meta):
            if os.path.exists(p):
                os.remove(p)
    with open(meta, "w", encoding="utf-8") as f:
        json.dump({"url": rel.url, "sha256": rel.sha256, "sha1": rel.sha1}, f)
    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        r = _open(rel.url, 60, headers)
    except urllib.error.HTTPError:                # 416: the part is already complete (or junk)
        have, r = 0, None
        os.remove(part)
        r = _open(rel.url, 60)
    with r:
        if have and getattr(r, "status", 200) != 206:
            have = 0                              # the server ignored Range: start over
        total = (have + int(r.headers.get("Content-Length") or 0)) or rel.size
        if total > MAX_DOWNLOAD:
            raise ToolError("архив подозрительно большой — отменено")
        done, t0, mark_t, mark_b = have, time.monotonic(), time.monotonic(), have
        _step(j, "download", done, total)
        with open(part, "ab" if have else "wb") as f:
            while True:
                if j.cancel.is_set():
                    raise Cancelled()
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if done > MAX_DOWNLOAD:
                    raise ToolError("архив подозрительно большой — отменено")
                now = time.monotonic()
                if now - mark_t >= 0.25:
                    inst = (done - mark_b) / (now - mark_t)
                    j.speed = inst if j.speed == 0 else j.speed * 0.6 + inst * 0.4
                    mark_t, mark_b = now, done
                    j.done, j.total = done, max(total, done)
                    j.tick += 1
        if rel.size and done != rel.size:
            raise ToolError("загрузка оборвалась — нажмите ещё раз, она продолжится")
    _step(j, "verify", done, done)
    h256, h1 = hashlib.sha256(), hashlib.sha1()
    with open(part, "rb") as f:
        while b := f.read(1 << 20):
            h256.update(b); h1.update(b)
    ok = (not rel.sha256 or h256.hexdigest() == rel.sha256.lower()) and \
         (not rel.sha1 or h1.hexdigest() == rel.sha1.lower())
    if not ok:
        for p in (part, meta):
            try:
                os.remove(p)
            except OSError:
                pass
        raise ToolError("архив повреждён: контрольная сумма не сошлась")
    return part, h256.hexdigest()


def _safe_parts(name: str) -> list[str] | None:
    """Path inside an archive → its parts; None for anything that could escape the folder."""
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or "\x00" in n:
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." or ":" in p for p in parts):
        return None
    return parts


def _unpack(archive: str, out: str, j: Job) -> None:
    root = os.path.realpath(out)
    os.makedirs(out, exist_ok=True)

    def dest_of(name):
        parts = _safe_parts(name)
        if parts is None:
            raise ToolError("в архиве опасные пути — установка отменена")
        if not parts:
            return None
        d = os.path.realpath(os.path.join(out, *parts))
        if os.path.commonpath([root, d]) != root:
            raise ToolError("в архиве опасные пути — установка отменена")
        return d

    if archive.endswith(".tar.gz.part") or archive.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as t:
            members = t.getmembers()
            files = [m for m in members if m.isfile()]
            n = 0
            _step(j, "unpack", 0, len(files))
            for m in members:
                d = dest_of(m.name)
                if d is None:
                    continue
                if m.isdir():
                    os.makedirs(d, exist_ok=True)
                    continue
                if not m.isfile():                # links, devices: never
                    continue
                os.makedirs(os.path.dirname(d), exist_ok=True)
                with t.extractfile(m) as src, open(d, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 16)
                if not P.IS_WIN:
                    os.chmod(d, (m.mode & 0o755) | 0o600)
                n += 1
                _step(j, "unpack", n, len(files))
    else:
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            files = [i for i in infos if not i.is_dir()]
            n = 0
            _step(j, "unpack", 0, len(files))
            for i in infos:
                d = dest_of(i.filename)
                if d is None:
                    continue
                if i.is_dir():
                    os.makedirs(d, exist_ok=True)
                    continue
                mode = (i.external_attr >> 16) & 0o170777
                if stat.S_ISLNK(mode):
                    continue
                os.makedirs(os.path.dirname(d), exist_ok=True)
                with z.open(i) as src, open(d, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 16)
                if not P.IS_WIN:
                    os.chmod(d, ((mode & 0o755) | 0o600) if mode & 0o777 else 0o644)
                n += 1
                _step(j, "unpack", n, len(files))


def _work_dir(tool: Tool) -> str:
    d = os.path.join(ROOT, f".work-{tool.key}-{os.getpid()}-{int(time.time() * 1000)}")
    os.makedirs(d)
    return d


def _test_and_mark(tool: Tool, folder: str, j: Job, record: dict) -> str:
    """Run the new copy's version command (only after its checksum passed)."""
    exe = os.path.join(folder, _exe(tool.exe))
    if not os.path.isfile(exe):
        raise ToolError(f"в архиве нет программы {tool.exe}")
    if not P.IS_WIN:
        os.chmod(exe, os.stat(exe).st_mode | 0o755)
    _step(j, "test")
    ver = _run_version(tool, exe)
    if not ver:
        raise ToolError("новая копия не запускается на этом компьютере")
    record.update(tool=tool.key, version=ver, installed=time.strftime("%Y-%m-%dT%H:%M:%S"),
                  by="Fling")
    with open(os.path.join(folder, MARK), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1, ensure_ascii=False)
    return ver


def _swap(tool: Tool, new: str, j: Job) -> None:
    """ROOT/<dir> ← new, atomically; the old copy comes back if anything fails."""
    _step(j, "swap")
    target = tool.target
    old = f"{target}.old-{int(time.time() * 1000)}"
    if tool.before_swap and os.path.isdir(target):
        try:
            tool.before_swap(target)
        except Exception as e:
            print("toolkit before_swap:", e, flush=True)
    moved = False
    try:
        if os.path.isdir(target):
            for attempt in range(6):
                try:
                    os.replace(target, old)
                    moved = True
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.5)
        os.replace(new, target)
    except OSError as e:
        if moved and not os.path.exists(target):
            os.replace(old, target)               # rollback: the previous copy is back
        raise ToolError("папка занята — закройте то, что её использует (например, экран ТВ), "
                        "и повторите") from e
    shutil.rmtree(old, ignore_errors=True)


def _install(tool: Tool, j: Job) -> str:
    if not tool.installable:
        raise ToolError("Fling не умеет устанавливать эту программу")
    _step(j, "meta")
    rel = latest(tool.key, fresh=True)
    work = _work_dir(tool)
    try:
        part, sha256 = _download(rel, j)
        out = os.path.join(work, "x")
        _unpack(part, out, j)
        top = os.listdir(out)
        src = os.path.join(out, top[0]) if len(top) == 1 and os.path.isdir(os.path.join(out, top[0])) else out
        ver = _test_and_mark(tool, src, j, {"url": rel.url, "archive": rel.name, "sha256": sha256,
                                            "sha1": rel.sha1, "publisher_version": rel.version})
        _swap(tool, src, j)
        for p in (part, part + ".json"):
            try:
                os.remove(p)
            except OSError:
                pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
    choose(tool.key, tool.own_exe(), pinned=False)
    return ver


def _absorb(tool: Tool, j: Job, copy: Copy | None) -> str:
    """Copy a found copy's folder into Fling's own folder ("absorb")."""
    if not copy or not copy.path or not copy.portable or _unc(copy.path):
        raise ToolError("эту копию нельзя скопировать — используйте её на месте")
    src_dir = copy.folder
    files = []
    size = 0
    for base, _dirs, names in os.walk(src_dir):
        for n in names:
            p = os.path.join(base, n)
            if os.path.islink(p):
                continue
            files.append(p)
            size += os.path.getsize(p)
            if len(files) > 5000 or size > MAX_DOWNLOAD:
                raise ToolError("в этой папке слишком много файлов — используйте копию на месте")
    work = _work_dir(tool)
    try:
        dst_root = os.path.join(work, "x")
        os.makedirs(dst_root)
        _step(j, "copy", 0, len(files))
        for i, p in enumerate(files, 1):
            rel = os.path.relpath(p, src_dir)
            if rel == MARK:
                continue
            d = os.path.join(dst_root, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(p, d)
            _step(j, "copy", i, len(files))
        h = hashlib.sha256()
        with open(os.path.join(dst_root, _exe(tool.exe)), "rb") as f:
            while b := f.read(1 << 20):
                h.update(b)
        ver = _test_and_mark(tool, dst_root, j, {"copied_from": copy.path, "sha256_exe": h.hexdigest()})
        _swap(tool, dst_root, j)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    choose(tool.key, tool.own_exe(), pinned=True)
    return ver


def _remove(tool: Tool, j: Job) -> None:
    """Only Fling's own folder (ROOT/<dir>); copies found elsewhere are never deleted."""
    target = tool.target
    if not target or not os.path.isdir(target):
        return
    _step(j, "swap")
    if tool.before_swap:
        try:
            tool.before_swap(target)
        except Exception:
            pass
    old = f"{target}.old-{int(time.time() * 1000)}"
    try:
        os.replace(target, old)
    except OSError as e:
        raise ToolError("папка занята — закройте то, что её использует (например, экран ТВ), "
                        "и повторите") from e
    shutil.rmtree(old, ignore_errors=True)
    c = choice(tool.key).get("path")
    if c and _norm(os.path.dirname(c)) == _norm(target):
        forget(tool.key)


def cleanup() -> None:
    """Leftovers of an interrupted install (work folders, half-swapped copies)."""
    for p in glob.glob(os.path.join(ROOT, ".work-*")) + glob.glob(os.path.join(ROOT, "*.old-*")):
        shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------- the tools

def _adb_kill_server(folder: str) -> None:
    adb = os.path.join(folder, _exe("adb"))
    if os.path.isfile(adb):                       # a running adb server keeps its folder locked
        P.run([adb, "kill-server"], capture_output=True, timeout=8)


_SDK_REPO = "https://dl.google.com/android/repository/"


def _platform_tools_latest() -> Release:
    """Google's SDK repository lists each platform-tools zip with its size and SHA-1."""
    import xml.etree.ElementTree as ET
    host = "windows" if P.IS_WIN else "macosx" if P.IS_MAC else "linux"
    root = ET.fromstring(_get_bytes(_SDK_REPO + "repository2-3.xml", 30))

    def tag(e):
        return e.tag.rsplit("}", 1)[-1]

    def child(e, name):
        return next((c for c in e if tag(c) == name), None)

    found = []
    for pk in root.iter():
        if tag(pk) != "remotePackage" or pk.get("path") != "platform-tools":
            continue
        ch = child(pk, "channelRef")
        if ch is not None and ch.get("ref") not in (None, "channel-0"):
            continue                              # stable channel only
        rv = child(pk, "revision")
        ver = ".".join((child(rv, n).text or "0") if child(rv, n) is not None else "0"
                       for n in ("major", "minor", "micro")) if rv is not None else "0"
        archives = child(pk, "archives")
        for a in (list(archives) if archives is not None else []):
            if (child(a, "host-os").text if child(a, "host-os") is not None else "") != host:
                continue
            comp = child(a, "complete")
            url = child(comp, "url").text.strip()
            sha = child(comp, "checksum")
            found.append(Release(ver, urljoin(_SDK_REPO, url), os.path.basename(url),
                                 int(child(comp, "size").text), sha1=sha.text.strip().lower()
                                 if sha is not None and sha.get("type") == "sha1" else None,
                                 page=P.PLATFORM_TOOLS_PAGE))
    if not found:
        raise ToolError("Google не опубликовал platform-tools для этой системы")
    rel = max(found, key=lambda r: parse_version(r.version))
    if not rel.url.startswith(_SDK_REPO):
        raise ToolError("загрузка возможна только по HTTPS")
    return rel


def _scrcpy_latest() -> Release:
    """GitHub release + its SHA256SUMS.txt (and GitHub's own asset digest when present)."""
    pre = P.scrcpy_asset_prefix()
    if not pre:
        raise ToolError("для этого компьютера нет готовой сборки scrcpy — установите его из пакетов")
    rel = json.loads(_get_bytes(P.SCRCPY_RELEASES, 20))
    assets = {a["name"]: a for a in rel.get("assets", [])}
    name = next((n for n in assets if n.startswith(pre) and n.endswith((".zip", ".tar.gz"))), None)
    if not name or "SHA256SUMS.txt" not in assets:
        raise ToolError("в релизе scrcpy нет сборки для этого компьютера")
    sums = _get_bytes(assets["SHA256SUMS.txt"]["browser_download_url"], 20).decode(errors="replace")
    want = next((l.split()[0].lower() for l in sums.splitlines()
                 if l.split() and l.split()[-1].lstrip("*") == name), None)
    if not want or not re.fullmatch(r"[0-9a-f]{64}", want):
        raise ToolError("нет контрольной суммы scrcpy")
    digest = (assets[name].get("digest") or "").lower()
    if digest.startswith("sha256:") and digest[7:] != want:
        raise ToolError("контрольные суммы scrcpy не совпадают — установка отменена")
    return Release(str(rel.get("tag_name", "")).lstrip("v"), assets[name]["browser_download_url"], name,
                   int(assets[name].get("size") or 0), sha256=want, page=rel.get("html_url", ""))


def _builtin(dist: str, module: str = "") -> Callable[[], Copy | None]:
    """A Python package inside Fling's own Python (the Windows build, or `uv run --with catt`)."""
    def find():
        try:
            from importlib.metadata import version
            return Copy("", "builtin", version(dist))
        except Exception:
            pass
        try:                                      # no package metadata (a frozen build): ask the module
            import importlib
            return Copy("", "builtin", str(importlib.import_module(module).__version__)) if module else None
        except Exception:
            return None
    return find


def _fling_extra(sub: str, exe: str) -> list:
    return [(os.path.join(d, sub, _exe(exe)), "bundled") for d in _fling_install_dirs()]


register(Tool(
    key="adb", title="Android platform-tools (adb)",
    purpose="ADB: что играет на ТВ, снимки экрана, установка APK и ADB-консоль.",
    license="Android SDK License", license_url="https://developer.android.com/studio/terms",
    homepage=P.PLATFORM_TOOLS_PAGE,
    exe="adb", dir="platform-tools", version_args=("version",),
    version_re=r"^Version\s+(\d+(?:\.\d+)+)", min_version="30.0.0",     # 30: `adb pair` (wireless)
    places=lambda: [(os.path.join(ROOT, "platform-tools", _exe("adb")), "fling")]
    + _fling_extra("platform-tools", "adb")
    + _search("adb", sub=("platform-tools", ""), sdk=True),
    latest=_platform_tools_latest,
    portable_marker="AdbWinApi.dll" if P.IS_WIN else "",
    before_swap=_adb_kill_server,
))

register(Tool(
    key="scrcpy", title="scrcpy",
    purpose="Экран ТВ в окне Fling и мышь компьютера на ТВ (LIVE).",
    license="Apache-2.0", license_url="https://github.com/Genymobile/scrcpy/blob/master/LICENSE",
    homepage="https://github.com/Genymobile/scrcpy",
    exe="scrcpy", dir="scrcpy", version_args=("--version",),
    version_re=r"scrcpy\s+v?(\d+(?:\.\d+)+)", min_version="2.4",          # 2.4: --mouse=uhid, --keyboard
    places=lambda: [(os.path.join(ROOT, "scrcpy", _exe("scrcpy")), "fling")]
    + _fling_extra("scrcpy", "scrcpy")
    + _search("scrcpy", sub=("", "scrcpy-win64-v*", "scrcpy-win32-v*", "scrcpy-winarm64-v*")),
    latest=_scrcpy_latest,
    portable_marker="scrcpy-server",
))

register(Tool(
    key="catt", title="catt",
    purpose="Отправка видео и ссылок на ТВ по Cast.",
    license="BSD-2-Clause", license_url="https://github.com/skorokithakis/catt/blob/master/LICENSE",
    homepage="https://github.com/skorokithakis/catt",
    exe="catt", version_re=r"catt\s+v?(\d+(?:\.\d+)+)",
    places=lambda: [] if P.FROZEN or _builtin("catt", "catt")() else [(P.find_catt(), "path")],
    builtin=_builtin("catt", "catt"),
    note="Устанавливается вместе с Fling.",
))

register(Tool(
    key="yt-dlp", title="yt-dlp",
    purpose="Прямые ссылки на видео с сайтов для отправки на ТВ.",
    license="Unlicense", license_url="https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE",
    homepage="https://github.com/yt-dlp/yt-dlp",
    builtin=_builtin("yt-dlp", "yt_dlp.version"),
    note="Устанавливается вместе с Fling.",
))


# ---------------------------------------------------------------- texts (fling_app adds them to EN_PHRASES)

EN_PHRASES = [
    ("Fling — инструменты", "Fling — tools"),
    ("Инструменты…", "Tools…"),
    ("Инструменты", "Tools"),
    ("Программы, без которых не работают ADB и экран ТВ. Если они уже есть на компьютере, "
     "Fling найдёт их и будет использовать; если нет — скачает и установит сам.",
     "Programs that ADB and the TV screen need. If they're already on this computer, Fling "
     "finds and uses them; if not, it downloads and installs them itself."),
    ("Найти заново", "Search again"),
    ("Ищу на компьютере…", "Searching this computer…"),
    ("ADB: что играет на ТВ, снимки экрана, установка APK и ADB-консоль.",
     "ADB: what's playing on the TV, screenshots, installing APKs and the ADB console."),
    ("Экран ТВ в окне Fling и мышь компьютера на ТВ (LIVE).",
     "The TV screen in a Fling window and the computer's mouse on the TV (LIVE)."),
    ("Отправка видео и ссылок на ТВ по Cast.", "Sending videos and links to the TV over Cast."),
    ("Прямые ссылки на видео с сайтов для отправки на ТВ.",
     "Direct video links from websites for sending to the TV."),
    ("Устанавливается вместе с Fling.", "Installed together with Fling."),
    ("Установлен через Fling", "Installed by Fling"),
    ("Встроен в Fling", "Built into Fling"),
    ("Не найден", "Not found"),
    ("Найден", "Found"),
    ("Устарел", "Outdated"),
    ("Устанавливаю…", "Installing…"),
    ("Удаляю…", "Removing…"),
    ("Копирую…", "Copying…"),
    ("Не найден на этом компьютере.", "Not found on this computer."),
    ("Используется версия {v}", "Using version {v}"),
    ("Используется: версия неизвестна", "In use: version unknown"),
    ("доступна {v}", "{v} available"),
    ("это последняя версия", "the latest version"),
    ("Нужна версия {v} или новее.", "Version {v} or newer is needed."),
    ("Выбранная копия пропала — использую найденную.", "The chosen copy is gone — using one that was found."),
    ("Найдено на компьютере", "Found on this computer"),
    ("Используется", "In use"),
    ("Использовать", "Use"),
    ("Использовать эту копию", "Use this copy"),
    ("Скопировать в папку Fling", "Copy into Fling's folder"),
    ("Открыть папку", "Open folder"),
    ("Установить", "Install"),
    ("Обновить до {v}", "Update to {v}"),
    ("Переустановить", "Reinstall"),
    ("Указать файл…", "Choose file…"),
    ("Удалить", "Remove"),
    ("Отмена", "Cancel"),
    ("Лицензия", "License"),
    ("Сайт проекта", "Project website"),
    ("Папка Fling", "Fling's folder"),
    ("В комплекте Fling", "Bundled with Fling"),
    ("Пакет системы", "System package"),
    ("Выбран вручную", "Chosen by hand"),
    ("версия ?", "version ?"),
    ("Узнаю последнюю версию…", "Checking the latest version…"),
    ("Скачиваю: {done} из {total} МБ · {speed} МБ/с", "Downloading: {done} of {total} MB · {speed} MB/s"),
    ("Скачиваю: {done} МБ · {speed} МБ/с", "Downloading: {done} MB · {speed} MB/s"),
    ("Проверяю контрольную сумму…", "Verifying the checksum…"),
    ("Распаковываю файлы: {n} / {total}", "Unpacking files: {n} / {total}"),
    ("Копирую файлы: {n} / {total}", "Copying files: {n} / {total}"),
    ("Проверяю, что программа запускается…", "Checking that the program runs…"),
    ("Ставлю на место…", "Putting it in place…"),
    ("Установлено: {name} {version}.", "Installed: {name} {version}."),
    ("Скопировано в папку Fling: {name} {version}.", "Copied into Fling's folder: {name} {version}."),
    ("Удалено: {name}.", "Removed: {name}."),
    ("Отменено.", "Cancelled."),
    ("Не вышло: ", "Failed: "),
    ("Точно удалить?", "Really remove?"),
    ("Удалить {name} из папки Fling? Копии в других папках останутся.",
     "Remove {name} from Fling's folder? Copies in other folders stay."),
    ("Выберите программу {name}", "Choose the {name} program"),
    ("Программы (*.exe);;Все файлы (*)", "Programs (*.exe);;All files (*)"),
    ("Этот файл не похож на {name}: он не назвал свою версию.",
     "This file doesn't look like {name}: it didn't report its version."),
    ("ADB не найден на этом компьютере.", "ADB was not found on this computer."),
    ("Установить автоматически", "Install automatically"),
    ("Fling скачает Android platform-tools с сайта Google (~10 МБ) и проверит контрольную сумму.",
     "Fling downloads Android platform-tools from Google (~10 MB) and checks the checksum."),
    ("Или поставьте platform-tools сами (", "Or install platform-tools yourself ("),
    ("zip для этой ОС", "zip for this OS"),
    ("страница Google", "Google’s page"),
    (") — Fling найдёт их сам.", ") — Fling finds them by itself."),
    ("Скачиваю scrcpy — ход загрузки в окне «Инструменты»…", "Downloading scrcpy — progress is in the “Tools” window…"),
    # errors raised above
    ("сервер перенаправил загрузку не на HTTPS — отменено", "the server redirected the download away from HTTPS — cancelled"),
    ("загрузка возможна только по HTTPS", "downloads are allowed over HTTPS only"),
    ("нет связи с сервером загрузки", "can't reach the download server"),
    ("сервер ответил ", "the server answered "),
    ("ответ сервера слишком большой", "the server's answer is too large"),
    ("программы из сетевых папок не запускаются", "programs from network folders are not run"),
    ("издатель не дал контрольную сумму — устанавливать не буду",
     "the publisher gave no checksum — not installing"),
    ("архив подозрительно большой — отменено", "the archive is suspiciously large — cancelled"),
    ("загрузка оборвалась — нажмите ещё раз, она продолжится",
     "the download broke off — press again and it will resume"),
    ("архив повреждён: контрольная сумма не сошлась", "the archive is damaged: the checksum didn't match"),
    ("в архиве опасные пути — установка отменена", "the archive has unsafe paths — install cancelled"),
    ("в архиве нет программы ", "the archive has no program "),
    ("новая копия не запускается на этом компьютере", "the new copy doesn't run on this computer"),
    ("папка занята — закройте то, что её использует (например, экран ТВ), и повторите",
     "the folder is in use — close whatever uses it (the TV screen, for example) and try again"),
    ("Fling не умеет устанавливать эту программу", "Fling can't install this program"),
    ("эту копию нельзя скопировать — используйте её на месте", "this copy can't be copied — use it where it is"),
    ("в этой папке слишком много файлов — используйте копию на месте",
     "this folder has too many files — use the copy where it is"),
    ("Google не опубликовал platform-tools для этой системы", "Google hasn't published platform-tools for this system"),
    ("контрольные суммы scrcpy не совпадают — установка отменена",
     "scrcpy's checksums don't match — install cancelled"),
    ("не вышло", "it didn't work"),
]

SOURCES = {"fling": "Папка Fling", "bundled": "В комплекте Fling", "path": "PATH", "sdk": "Android SDK",
           "winget": "WinGet", "scoop": "Scoop", "choco": "Chocolatey", "brew": "Homebrew",
           "system": "Пакет системы", "custom": "Выбран вручную", "builtin": "Встроен в Fling"}


# ================================================================ UI (Qt)

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, QUrl              # noqa: E402
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap, QGuiApplication  # noqa: E402
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu,  # noqa: E402
                             QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

_F = None                         # fling_app (label, glyph, font, T, tr, LANG …) — see bind_ui()


def bind_ui(app_module) -> None:
    """fling_app hands over its look: label(), glyph(), font(), small_button(), SlimProgress,
    Theme / T and tr()."""
    global _F
    _F = app_module


def _tr(text: str) -> str:
    return _F.tr(text) if _F else text


def _en() -> bool:
    return bool(_F) and _F.LANG == "en"


def _num(x: float) -> str:
    s = f"{x:.1f}"
    return s if _en() else s.replace(".", ",")


def _css(c) -> str:
    return _F.Theme.css(c)


def job_text(j: Job) -> str:
    """One line for a running job, in the UI language."""
    if j.phase in ("start", "meta"):
        return _tr("Узнаю последнюю версию…") if j.action == "install" else \
            _tr("Копирую…" if j.action == "absorb" else "Удаляю…")
    if j.phase == "download":
        mb = 1 << 20
        if j.total:
            return _tr("Скачиваю: {done} из {total} МБ · {speed} МБ/с").format(
                done=_num(j.done / mb), total=_num(j.total / mb), speed=_num(j.speed / mb))
        return _tr("Скачиваю: {done} МБ · {speed} МБ/с").format(done=_num(j.done / mb), speed=_num(j.speed / mb))
    if j.phase == "verify":
        return _tr("Проверяю контрольную сумму…")
    if j.phase == "unpack":
        return _tr("Распаковываю файлы: {n} / {total}").format(n=j.done, total=j.total)
    if j.phase == "copy":
        return _tr("Копирую файлы: {n} / {total}").format(n=j.done, total=j.total)
    if j.phase == "test":
        return _tr("Проверяю, что программа запускается…")
    if j.phase == "swap":
        return _tr("Ставлю на место…") if j.action != "remove" else _tr("Удаляю…")
    return ""


def job_result(j: Job, tool: Tool) -> str:
    if j.state == "error":
        return _tr("Не вышло: ") + _tr(j.message)
    return _tr(j.message).format(name=tool.title, version=j.version)


def _dots_icon(size: int, color) -> QIcon:
    """⋯ drawn crisp at any scale."""
    scr = QGuiApplication.primaryScreen()
    dpr = (scr.devicePixelRatio() if scr else 1) * 2
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    r = size * 0.09
    for x in (0.25, 0.5, 0.75):
        p.drawEllipse(QRectF(size * x - r, size * 0.5 - r, 2 * r, 2 * r))
    p.end()
    return QIcon(pm)


def _tool_icon(key: str, size: int, color) -> QIcon:
    names = {"adb": "android", "scrcpy": "tv", "catt": "play_on_tv", "yt-dlp": "play_on_tv"}
    return _F.glyph(names.get(key, "tv"), size, color)


class _Elided(QLabel):
    """One line that shortens in the middle instead of widening the window (paths)."""
    def __init__(self, text="", px=11, color=None, mono=False):
        super().__init__()
        self._full = ""
        self.setFont(_F.font(px, mono=mono))
        self.setStyleSheet(f"color:{_css(color or _F.T.fg)};background:transparent")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(40)
        self.set_full(text)

    def set_full(self, text: str):
        self._full = text or ""
        self.setToolTip(self._full if len(self._full) > 30 else "")
        self._elide()

    def _elide(self):
        w = max(10, self.width())
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, w))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


def _pill(text: str, color) -> QLabel:
    l = QLabel(_tr(text))
    l.setFont(_F.font(10.5, QFont.Weight.DemiBold))
    bg = QColor(color); bg.setAlphaF(0.16)
    l.setStyleSheet(f"color:{_css(color)};background:{_css(bg)};border-radius:9px;padding:0 9px")
    l.setFixedHeight(18)
    return l


def _primary(text: str, on) -> QPushButton:
    T = _F.T
    b = QPushButton(_tr(text))
    b.setFont(_F.font(11.5, QFont.Weight.DemiBold))
    b.setFixedHeight(24)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"QPushButton{{border:none;border-radius:5px;padding:0 12px;background:{_css(T.accent)};color:white}}"
                    f"QPushButton:hover{{background:{_css(T.acc_a(0.85))}}}"
                    f"QPushButton:disabled{{background:{_css(T.acc_a(0.35))}}}")
    b.clicked.connect(on)
    return b


def _clear(layout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w:
            w.hide()                              # gone now, not at the next event loop turn
            w.deleteLater()
        elif it.layout():
            _clear(it.layout())


class ToolCard(QFrame):
    """One tool: status, the copy in use, every copy found, install / update / remove."""
    def __init__(self, page: "ToolsPage", tool: Tool):
        super().__init__()
        T = _F.T
        self.page, self.tool = page, tool
        self.setObjectName("toolcard")
        self.setStyleSheet(f"QFrame#toolcard{{background:{_css(T.fg_a(0.045))};border-radius:10px}}")
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        top = QHBoxLayout(); top.setSpacing(9)
        self.icon = QLabel(); self.icon.setFixedSize(20, 20)
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(_F.label(tool.title, 13.5, weight=QFont.Weight.DemiBold), 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        self.pill_box = QHBoxLayout(); self.pill_box.setContentsMargins(0, 0, 0, 0)
        top.addLayout(self.pill_box)
        v.addLayout(top)
        v.addWidget(_F.label(tool.purpose, 11.5, T.fg_a(0.6), wrap=True))
        self.info = _F.label("", 11.5, T.fg_a(0.85), wrap=True)
        v.addWidget(self.info)
        self.path = _Elided("", 10.5, T.fg_a(0.5), mono=True)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.path)
        # progress of a running job
        self.prog_box = QWidget()
        pl = QVBoxLayout(self.prog_box); pl.setContentsMargins(0, 4, 0, 0); pl.setSpacing(6)
        self.bar = _F.SlimProgress()
        pl.addWidget(self.bar)
        pr = QHBoxLayout(); pr.setSpacing(8)
        self.prog = _F.label("", 11, T.fg_a(0.65))
        pr.addWidget(self.prog, 1)
        self.cancel_btn = _F.small_button("Отмена", self._cancel)
        pr.addWidget(self.cancel_btn)
        pl.addLayout(pr)
        self.prog_box.hide()
        v.addWidget(self.prog_box)
        self.result = _F.label("", 11, T.fg_a(0.65), wrap=True)
        self.result.hide()
        v.addWidget(self.result)
        # copies found
        self.copies_title = _F.label("Найдено на компьютере", 11, T.fg_a(0.5), QFont.Weight.DemiBold)
        self.copies_title.setContentsMargins(0, 6, 0, 0)
        v.addWidget(self.copies_title)
        self.copies = QVBoxLayout(); self.copies.setSpacing(2); self.copies.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.copies)
        # actions
        self.actions = QHBoxLayout(); self.actions.setSpacing(6); self.actions.setContentsMargins(0, 6, 0, 0)
        v.addLayout(self.actions)
        lic = _F.label(f'{_tr("Лицензия")}: <a href="{tool.license_url}" style="color:{_css(T.fg_a(0.6))}">'
                       f'{tool.license}</a> · <a href="{tool.homepage}" style="color:{_css(T.fg_a(0.6))}">'
                       f'{_tr("Сайт проекта")}</a>', 10.5, T.fg_a(0.45))
        lic.setOpenExternalLinks(True)
        lic.setContentsMargins(0, 2, 0, 0)
        v.addWidget(lic)
        self._job_tick = -1

    # ---- render
    def render(self, copies: list[Copy] | None, rel: Release | None):
        T, tool = _F.T, self.tool
        self.icon.setPixmap(_tool_icon(tool.key, 20, T.fg_a(0.85)).pixmap(QSize(20, 20)))
        j = job(tool.key)
        running = bool(j and j.state == "run")
        chosen = resolve(tool.key) if tool.dir else None
        cur = None
        if copies is not None:
            if chosen:
                cur = next((c for c in copies if c.path and _norm(c.path) == _norm(chosen)), None)
                if cur is None:
                    cur = Copy(chosen, "custom")
            else:
                listed0 = [c for c in copies if c.path]
                cur = copies[0] if copies and (not tool.dir or not copies[0].path) else                     best(tool.key, copies) or (listed0[0] if listed0 else None)
        # pill
        _clear(self.pill_box)
        if copies is None:
            pill = None
        elif running:
            pill = _pill({"install": "Устанавливаю…", "absorb": "Копирую…", "remove": "Удаляю…"}[j.action], T.accent)
        elif cur is None:
            pill = _pill("Не найден", T.red if tool.installable or not copies else T.orange)
        elif not compatible(tool, cur):
            pill = _pill("Устарел", T.orange)
        elif cur.source == "builtin":
            pill = _pill("Встроен в Fling", T.green)
        elif cur.owned:
            pill = _pill("Установлен через Fling", T.green)
        else:
            pill = _pill("Найден", T.green)
        if pill:
            self.pill_box.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        # the copy in use
        if copies is None:
            self.info.setText(_tr("Ищу на компьютере…")); self.path.set_full("")
        elif cur is None:
            self.info.setText(_tr("Не найден на этом компьютере.") +
                              ("" if tool.installable else " " + _tr(tool.note)))
            self.path.set_full("")
        else:
            line = _tr("Используется версия {v}").format(v=cur.version) if cur.version else \
                _tr("Используется: версия неизвестна")
            if rel and tool.installable:
                newer = parse_version(rel.version) > parse_version(cur.version)
                line += " · " + (_tr("доступна {v}").format(v=rel.version) if newer else _tr("это последняя версия"))
            if not compatible(tool, cur):
                line += ". " + _tr("Нужна версия {v} или новее.").format(v=tool.min_version)
            self.info.setText(line)
            self.path.set_full(cur.path)
        self.path.setVisible(bool(self.path._full))
        # copies list (only programs with more than the one in use, or a choice to make)
        _clear(self.copies)
        listed = [c for c in (copies or []) if c.path]
        show = bool(tool.dir and listed)
        self.copies_title.setVisible(show)
        if show:
            for c in listed:
                self.copies.addWidget(self._copy_row(c, cur))
        # actions
        _clear(self.actions)
        if tool.installable and not running:
            own = next((c for c in listed if c.owned), None)
            if rel and own and parse_version(rel.version) > parse_version(own.version):
                self.actions.addWidget(_primary(_tr("Обновить до {v}").format(v=rel.version), self._install))
            elif own:
                self.actions.addWidget(_F.small_button("Переустановить", self._install))
            elif rel and cur and cur.path and compatible(tool, cur) and                     parse_version(rel.version) <= parse_version(cur.version):
                # the copy in use is already the latest: a Fling-owned copy is optional
                self.actions.addWidget(_F.small_button(_tr("Установить") + f" {rel.version}", self._install))
            elif rel:
                self.actions.addWidget(_primary(_tr("Установить") + f" {rel.version}", self._install))
            else:
                self.actions.addWidget(_primary("Установить", self._install))
        if tool.dir and not running:
            self.actions.addWidget(_F.small_button("Указать файл…", self._pick_file))
        self.actions.addStretch(1)
        if cur and cur.path and not running:
            self.actions.addWidget(_F.small_button("Открыть папку", lambda: P.open_path(cur.folder)))
        if tool.dir and not running and os.path.isdir(tool.target):
            self.actions.addWidget(_F.small_button("Удалить", self._remove))
        self.update_job()

    def _copy_row(self, c: Copy, cur: Copy | None) -> QWidget:
        T = _F.T
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 1, 0, 1); h.setSpacing(8)
        using = cur is not None and c.path and _norm(c.path) == _norm(cur.path)
        dot = QLabel(); dot.setFixedSize(10, 10)
        dot.setPixmap(_F.glyph("dot", 10, T.accent if using else T.fg_a(0.22)).pixmap(QSize(10, 10)))
        h.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        ok = compatible(self.tool, c)
        ver = _F.label(c.version or _tr("версия ?"), 11.5, T.fg if ok else T.orange,
                       QFont.Weight.DemiBold if using else QFont.Weight.Normal)
        h.addWidget(ver, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(_F.label(_tr(SOURCES.get(c.source, c.source)), 11, T.fg_a(0.6)), 0, Qt.AlignmentFlag.AlignVCenter)
        path = _Elided(c.folder, 10.5, T.fg_a(0.42), mono=True)
        h.addWidget(path, 1, Qt.AlignmentFlag.AlignVCenter)
        if using:
            u = _F.label("Используется", 11, T.accent, QFont.Weight.DemiBold)
            u.setFixedHeight(24); u.setContentsMargins(0, 0, 4, 0)
            h.addWidget(u, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            h.addWidget(_F.small_button("Использовать", lambda _=False, c=c: self._use(c)), 0,
                        Qt.AlignmentFlag.AlignVCenter)
        more = QToolButton()
        more.setIcon(_dots_icon(16, T.fg_a(0.7))); more.setIconSize(QSize(16, 16))
        more.setFixedSize(24, 24)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setStyleSheet("QToolButton{border:none;border-radius:5px;background:transparent}"
                           f"QToolButton:hover{{background:{_css(T.fg_a(0.08))}}}"
                           "QToolButton::menu-indicator{image:none}")
        m = QMenu(more)
        a = m.addAction(_tr("Использовать эту копию"), lambda c=c: self._use(c)); a.setEnabled(not using)
        if not c.owned:
            a = m.addAction(_tr("Скопировать в папку Fling"), lambda c=c: self._absorb(c))
            a.setEnabled(c.portable and not busy(self.tool.key))
        m.addAction(_tr("Открыть папку"), lambda c=c: P.open_path(c.folder))
        more.setMenu(m)
        h.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def update_job(self):
        j = job(self.tool.key)
        if not j:
            self.prog_box.hide(); self.result.hide()
            return
        if j.tick == self._job_tick and j.state != "run":
            return
        self._job_tick = j.tick
        if j.state == "run":
            self.result.hide()
            self.prog_box.show()
            self.prog.setText(job_text(j))
            if j.phase in ("download", "unpack", "copy") and j.total:
                self.bar.value = min(1.0, j.done / j.total)
            elif j.phase in ("verify", "test", "swap"):
                self.bar.value = 1.0
            else:
                self.bar.value = 0.0
            self.bar.update()
            self.cancel_btn.setEnabled(j.phase in ("meta", "download", "unpack", "copy", "start"))
        else:
            self.prog_box.hide()
            T = _F.T
            col = T.red if j.state == "error" else T.fg_a(0.65) if j.state == "cancelled" else T.green
            self.result.setStyleSheet(f"color:{_css(col)};background:transparent")
            self.result.setText(job_result(j, self.tool))
            self.result.show()

    # ---- actions
    def _install(self):
        start(self.tool.key, "install")
        self.page.render_all()

    def _cancel(self):
        j = job(self.tool.key)
        if j:
            j.cancel.set()

    def _use(self, c: Copy):
        choose(self.tool.key, c.path, pinned=True)
        self.page.render_all()

    def _absorb(self, c: Copy):
        start(self.tool.key, "absorb", c)
        self.page.render_all()

    def _remove(self):
        """First click asks (the button turns red for a few seconds), the second removes."""
        b = self.sender()
        if not getattr(self, "_armed", False):
            self._armed = True
            T = _F.T
            b.setText(_tr("Точно удалить?"))
            b.setStyleSheet(b.styleSheet() + f"QPushButton{{color:{_css(T.red)};border-color:{_css(T.red)}}}")
            b.setToolTip(_tr("Удалить {name} из папки Fling? Копии в других папках останутся.")
                         .format(name=self.tool.title))
            QTimer.singleShot(4000, self._disarm)
            return
        self._armed = False
        start(self.tool.key, "remove")
        self.page.render_all()

    def _disarm(self):
        if getattr(self, "_armed", False):
            self._armed = False
            self.page.render_all()

    def _pick_file(self):
        tool = self.tool
        path, _ = QFileDialog.getOpenFileName(
            self, _tr("Выберите программу {name}").format(name=tool.exe),
            os.path.dirname(resolve(tool.key) or P.HOME),
            _tr("Программы (*.exe);;Все файлы (*)") if P.IS_WIN else "")
        if not path:
            return
        path = os.path.normpath(path)
        if _unc(path):
            self._say(_tr("Не вышло: ") + _tr("программы из сетевых папок не запускаются"), True)
            return

        def check():
            ver = _run_version(tool, path)
            if not ver:
                return _tr("Этот файл не похож на {name}: он не назвал свою версию.").format(name=tool.exe)
            choose(tool.key, path, pinned=True)
            return ""
        self.page.background(check, lambda err: (self.page.rescan(), err and self._say(err, True)))

    def _say(self, text: str, error: bool = False):
        T = _F.T
        self.result.setStyleSheet(f"color:{_css(T.red if error else T.fg_a(0.65))};background:transparent")
        self.result.setText(text)
        self.result.show()


class ToolsPage(QWidget):
    """The tools list without window chrome: a Settings window can add it as a page."""
    def __init__(self, parent=None, tools: list[str] | None = None):
        super().__init__(parent)
        T = _F.T
        self.keys = tools or list(TOOLS)
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.found: dict[str, list[Copy] | None] = {k: None for k in self.keys}
        self.rel: dict[str, Release | None] = {}
        self._results: list = []                    # (callback, value) from worker threads
        self._lock = threading.Lock()
        self._rev = -1
        self._seen: set = set()                     # finished jobs already reacted to
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(10)
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(_F.label("Инструменты", 17, weight=QFont.Weight.Bold), 1, Qt.AlignmentFlag.AlignVCenter)
        self.again = _F.small_button("Найти заново", self.rescan)
        head.addWidget(self.again, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(head)
        v.addWidget(_F.label("Программы, без которых не работают ADB и экран ТВ. Если они уже есть на компьютере, "
                             "Fling найдёт их и будет использовать; если нет — скачает и установит сам.",
                             11.5, T.fg_a(0.6), wrap=True))
        self.cards = {k: ToolCard(self, TOOLS[k]) for k in self.keys}
        for k in self.keys:
            v.addWidget(self.cards[k])
        v.addStretch(1)
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._tick)
        self.render_all()

    # ---- threads → UI (polled: no signals cross threads)
    def background(self, fn, done):
        def go():
            try:
                r = fn()
            except Exception as e:
                print("toolkit:", e, flush=True)
                r = None
            with self._lock:
                self._results.append((done, r))
        threading.Thread(target=go, daemon=True).start()

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start()
        if all(v is None for v in self.found.values()):
            self.rescan()

    def hideEvent(self, e):
        super().hideEvent(e)
        if not busy():
            self.timer.stop()

    def rescan(self):
        self.found = {k: None for k in self.keys}
        self.again.setEnabled(False)
        self.render_all()

        def work():
            out = {}
            for k in self.keys:
                out[k] = scan(k)
                if TOOLS[k].exe and TOOLS[k].dir:
                    auto_pick(k, out[k])
            return out

        def done(out):
            self.again.setEnabled(True)
            if out:
                self.found.update(out)
            self.render_all()
        self.background(work, done)
        for k in self.keys:
            if TOOLS[k].installable:
                self.background(lambda k=k: (k, latest(k)), self._got_latest)

    def _got_latest(self, r):
        if r:
            self.rel[r[0]] = r[1]
            self.cards[r[0]].render(self.found.get(r[0]), r[1])

    def render_all(self):
        for k, c in self.cards.items():
            c.render(self.found.get(k), self.rel.get(k))
        self._rev = REVISION[0]

    def _tick(self):
        with self._lock:
            res, self._results = self._results, []
        for fn, r in res:
            fn(r)
        if REVISION[0] != self._rev:
            self._rev = REVISION[0]
            fresh = [j for k in self.keys if (j := job(k)) and j.finished.is_set() and j.serial not in self._seen]
            for j in fresh:
                self._seen.add(j.serial)
            if any(j.state == "ok" for j in fresh):
                self.rescan()                     # versions / paths changed
            else:
                self.render_all()
        for c in self.cards.values():
            c.update_job()
        if not self.isVisible() and not busy():
            self.timer.stop()


class ToolsWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        T = _F.T
        self.setWindowTitle(_tr("Fling — инструменты"))
        self.setMinimumWidth(520)
        self.resize(600, 720)
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea(); area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent}")
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page = ToolsPage()
        area.setWidget(self.page)
        outer.addWidget(area)


_WINDOW: list = []


def open_window(install: str | None = None) -> ToolsWindow:
    """Open (or raise) the one Tools window; install="adb" also starts that install."""
    if not _WINDOW:
        _WINDOW.append(ToolsWindow())
    w = _WINDOW[0]
    if install and not busy(install):
        start(install, "install")
    w.show(); w.raise_(); w.activateWindow()
    w.page.render_all()
    return w


def missing_adb_html() -> str:
    """For the ADB badge when adb is missing: install automatically, or by hand."""
    col = _css(_F.T.accent)
    return (f'{_tr("ADB не найден на этом компьютере.")} '
            f'<a href="fling:tools-install-adb" style="color:{col}">{_tr("Установить автоматически")}</a>. '
            f'{_tr("Или поставьте platform-tools сами (")}'
            f'<a href="{P.platform_tools_zip_url()}" style="color:{col}">{_tr("zip для этой ОС")}</a> · '
            f'<a href="{P.PLATFORM_TOOLS_PAGE}" style="color:{col}">{_tr("страница Google")}</a>'
            f'{_tr(") — Fling найдёт их сам.")}')


def link(href: str) -> bool:
    """Handle fling:tools… links; False for ordinary links (the caller opens those)."""
    if href == "fling:tools":
        open_window()
        return True
    if href == "fling:tools-install-adb":
        open_window(install="adb")
        return True
    if href.startswith(("https://", "http://")):
        QDesktopServices.openUrl(QUrl(href))
        return True
    return False
