# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
"""What Fling can cast and how: local media formats, subtitles, queues (several files,
folders, m3u / pls playlists), photo slideshows, resume where you left off, recent casts,
playback speed and start-at-timestamp links.

fling_app.py only calls in through small hooks (State.media, Panel widgets, tray menu,
CLI verbs). It sets ``F`` to itself at import: this module must not ``import fling_app``,
because when Fling runs as a script that would load a second copy of the app.

Playing one item = one lasting ``catt cast`` process (catt serves a local file over HTTP
for exactly as long as the TV plays it, ``-b`` keeps it alive for web links). When the
process ends, the Cast status (pushed over the in-process CastLink, see State/CastLink)
tells whether the item *finished* (go to the next one) or was stopped / replaced (the
queue pauses)."""
from __future__ import annotations

import hashlib
import os
import random
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget

F = None          # the fling_app module (set by fling_app right after its imports)

# ---------------------------------------------------------------- formats

# The Default Media Receiver plays these as they are (Google Cast "supported media").
# mkv / mov: H.264 + AAC inside plays on Google TV and recent Chromecasts.
VIDEO = {"mp4", "m4v", "mov", "webm", "mkv"}
AUDIO = {"mp3", "m4a", "aac", "flac", "wav", "ogg", "oga", "opus"}
IMAGE = {"jpg", "jpeg", "png", "gif", "bmp"}
# Pictures Fling re-encodes to JPEG first: Cast can't show them (or catt sends the
# wrong type, as for webp). Only if this Qt build can read them, see image_ok().
IMAGE_CONVERT = {"webp", "tif", "tiff", "heic", "heif", "avif"}
# Containers Chromecast can't play: copied to the TV over ADB and opened in its own player.
TV_PLAYER = {"avi", "wmv", "flv", "mpg", "mpeg", "ts", "m2ts", "mts", "vob", "3gp", "divx",
             "asf", "wma", "ape", "ac3", "dts"}
PLAYLIST = {"m3u", "m3u8", "pls"}
SUBS = {"srt", "vtt", "ass", "ssa"}

FOLDER_DEPTH = 3          # music: Artist / Album / CD1
FOLDER_LIMIT = 1000
RESUME_MIN = 600          # remember the position only for things of 10+ minutes (films, episodes)
RESUME_KEEP = 150
HISTORY_KEEP = 25
SLIDE_DEFAULT = 7
SLIDE_CHOICES = (3, 5, 7, 10, 15, 30, 60)
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
TMP = os.path.join(tempfile.gettempdir(), "fling-cast")
PLAYING = ("PLAYING", "BUFFERING", "PAUSED")
UNC_ERROR = "Сетевые папки (\\\\сервер\\…) не поддерживаются — скопируйте файл на этот компьютер."


def ext_of(p: str) -> str:
    return os.path.splitext(p or "")[1].lstrip(".").lower()


def is_unc(p: str) -> bool:
    """\\\\server\\share and //server/share. Touching one (even os.path.isfile) makes Windows
    send the user's NTLM hash to that server, so such paths are refused before any file call."""
    p = (p or "").strip()
    return p.startswith(("\\\\", "//")) or p.lower().startswith(("file://", "\\\\?\\unc"))


def is_web(u: str) -> bool:
    try:
        x = urlparse(u or "")
    except ValueError:
        return False
    return x.scheme in ("http", "https") and bool(x.hostname)


def image_ok(ext: str) -> bool:
    """Can this Qt build decode the picture format (heic/avif need extra plugins)?"""
    if ext in IMAGE:
        return True
    try:
        from PyQt6.QtGui import QImageReader
        fmts = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
    except Exception:
        return False
    return ext in fmts or (ext == "tif" and "tiff" in fmts) or (ext == "heif" and "heic" in fmts)


def media_kind(path: str) -> str | None:
    """video | audio | image | tv | playlist | subs | None (not a media file)"""
    e = ext_of(path)
    if e in VIDEO:
        return "video"
    if e in AUDIO:
        return "audio"
    if e in IMAGE or (e in IMAGE_CONVERT and image_ok(e)):
        return "image"
    if e in TV_PLAYER:
        return "tv"
    if e in PLAYLIST:
        return "playlist"
    if e in SUBS:
        return "subs"
    return None


def natural_key(s: str):
    """'Episode 2' before 'Episode 10', 'pic.png' before 'pic2.png' (name first, then extension)."""
    stem, ext = os.path.splitext(s)
    return [[(0, int(t)) if t.isdigit() else (1, t.casefold()) for t in re.split(r"(\d+)", stem) if t],
            ext.casefold()]


def pretty_title(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0] or os.path.basename(path)


def _is_junction(entry) -> bool:
    try:
        return entry.is_symlink() or bool(getattr(entry, "is_junction", lambda: False)())
    except OSError:
        return True


def scan_folder(folder: str, depth: int = FOLDER_DEPTH, limit: int = FOLDER_LIMIT) -> list[str]:
    """Playable files in a folder and its subfolders (natural order, folders after files,
    hidden entries, links and junctions skipped)."""
    out: list[str] = []

    def walk(d, level):
        try:
            entries = sorted(os.scandir(d), key=lambda e: natural_key(e.name))
        except OSError:
            return
        subdirs = []
        for e in entries:
            if len(out) >= limit:
                return
            if e.name.startswith(".") or _is_junction(e):
                continue
            try:
                if e.is_dir():
                    subdirs.append(e.path)
                elif e.is_file() and media_kind(e.name) in ("video", "audio", "image"):
                    out.append(e.path)
            except OSError:
                continue
        if level < depth:
            for s in subdirs:
                walk(s, level + 1)

    walk(folder, 0)
    return out[:limit]


def _read_text(path: str, limit: int = 4 * 1024 * 1024) -> str:
    with open(path, "rb") as f:
        return decode_text(f.read(limit))


def decode_text(raw: bytes) -> str:
    """Subtitles and playlists come in UTF-8, UTF-16 or (old Russian rips) Windows-1251."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    cyr = raw.decode("cp1251", "replace")
    letters = [c for c in cyr if c.isalpha()]
    if letters and sum("\u0400" <= c <= "\u04ff" for c in letters) / len(letters) > 0.3:
        return cyr
    return raw.decode("cp1252", "replace")


def parse_playlist(path: str) -> list[dict]:
    """m3u / m3u8 / pls → [{"src", "title"}]. Web links stay links; local entries are resolved
    against the playlist's folder. Network shares, file://host/… and other schemes are dropped
    without touching them."""
    try:
        text = _read_text(path)
    except OSError:
        return []
    base = os.path.dirname(os.path.abspath(path))
    raw: list[tuple[str, str | None]] = []
    if ext_of(path) == "pls":
        files, titles = {}, {}
        for line in text.splitlines():
            m = re.match(r"\s*(File|Title)(\d+)\s*=\s*(.*\S)", line, re.I)
            if m:
                (files if m[1].lower() == "file" else titles)[int(m[2])] = m[3]
        raw = [(files[k], titles.get(k)) for k in sorted(files)]
    else:
        title = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                m = re.match(r"#EXTINF:[^,]*,(.*)", line)
                title = m[1].strip() if m else title
                continue
            raw.append((line, title))
            title = None
    out = []
    for src, title in raw:
        if is_web(src):
            out.append({"src": src, "title": title or None, "web": True})
            continue
        if src.lower().startswith("file:///"):
            src = unquote(urlparse(src).path)
            if re.match(r"^/[A-Za-z]:", src):          # file:///C:/… → C:/…
                src = src[1:]
        if is_unc(src) or re.match(r"^[a-z][a-z0-9+.-]+://", src, re.I):
            continue
        p = src if os.path.isabs(src) else os.path.join(base, src)
        p = os.path.normpath(p)
        if is_unc(p) or not os.path.isfile(p):
            continue
        if media_kind(p) in ("video", "audio", "image"):
            out.append({"src": p, "title": title or None})
    return out


# ---------------------------------------------------------------- timestamps

def parse_time(v: str | None) -> float | None:
    """'90', '90s', '1m30s', '1h2m3s', '1:30', '01:02:03', '90.5' → seconds."""
    v = (v or "").strip().lower()
    if not v:
        return None
    if re.fullmatch(r"\d+(\.\d+)?s?", v):
        return float(v.rstrip("s"))
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", v)
    if m and any(m.groups()):
        h, mi, s = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", v)
    if m:
        return int(m[1] or 0) * 3600 + int(m[2]) * 60 + float(m[3])
    return None


def url_start(url: str) -> float | None:
    """Start position written into a link: YouTube / Twitch ?t=, ?start=, Vimeo / media fragment #t=."""
    try:
        u = urlparse(url or "")
    except ValueError:
        return None
    q = parse_qs(u.query)
    for k in ("t", "start", "time_continue"):
        if q.get(k):
            t = parse_time(q[k][0])
            if t:
                return t
    frag = parse_qs(u.fragment)
    if frag.get("t"):
        return parse_time(frag["t"][0].split(",")[0])
    return None


def resume_key(src: str) -> str:
    """One key per thing, whatever link points at it: YouTube by video id, files by path,
    other links without the fragment and time parameters."""
    if not is_web(src):
        return os.path.normcase(os.path.abspath(src))
    yid = F.youtube_id(src) if F else None
    if yid:
        return "yt:" + yid
    u = urlparse(src)
    q = "&".join(p for p in u.query.split("&") if p and p.split("=")[0] not in ("t", "start", "time_continue"))
    return u._replace(query=q, fragment="").geturl()


# ---------------------------------------------------------------- subtitles

def find_subtitles(video: str) -> str | None:
    """A subtitle file for a video: next to it or in Subs/ or Subtitles/, named like the video
    (film.srt, film.ru.srt, film.English.vtt). The interface language wins, then the plain name."""
    stem = pretty_title(video).casefold()
    here = os.path.dirname(os.path.abspath(video))
    found = []
    for d in (here, os.path.join(here, "Subs"), os.path.join(here, "Subtitles"),
              os.path.join(here, "subs"), os.path.join(here, "Subs", pretty_title(video))):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if ext_of(n) in SUBS and (n.casefold().startswith(stem) or d.endswith(pretty_title(video))):
                found.append(os.path.join(d, n))
    if not found:
        return None
    lang = (getattr(F, "LANG", "ru") if F else "ru")
    tags = {"ru": ("ru", "rus", "russian", "рус"), "en": ("en", "eng", "english")}.get(lang, (lang,))

    def score(p):
        rest = pretty_title(p).casefold()[len(stem):] if pretty_title(p).casefold().startswith(stem) \
            else pretty_title(p).casefold()
        words = set(re.split(r"[\W_]+", rest)) - {""}
        return (0 if words & set(tags) else 1 if not words else 2, ext_of(p) != "srt" and ext_of(p) != "vtt", p)
    return min(found, key=score)


def _srt_to_vtt(text: str) -> str:
    text = re.sub(r"(\d{1,2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
    return "WEBVTT\n\n" + text.lstrip()


def _ass_to_vtt(text: str) -> str:
    """Plain text of ASS / SSA dialogue lines (styles and effects are dropped)."""
    fmt, cues = None, []
    for line in text.splitlines():
        if line.startswith("Format:") and fmt is None and "Start" in line:
            fmt = [x.strip().lower() for x in line[7:].split(",")]
        elif line.startswith("Dialogue:") and fmt:
            parts = line[9:].split(",", len(fmt) - 1)
            if len(parts) < len(fmt):
                continue
            d = dict(zip(fmt, parts))

            def ts(v):
                h, m, s = v.strip().split(":")
                sec, _, cs = s.partition(".")
                return f"{int(h):02d}:{int(m):02d}:{int(sec):02d}.{(cs + '00')[:2]}0"
            body = re.sub(r"\{[^}]*\}", "", d.get("text", "")).replace("\\N", "\n").replace("\\n", "\n").strip()
            if body:
                try:
                    cues.append((ts(d["start"]), ts(d["end"]), body))
                except (KeyError, ValueError):
                    continue
    cues.sort()
    return "WEBVTT\n\n" + "\n\n".join(f"{a} --> {b}\n{t}" for a, b, t in cues) + "\n"


def to_vtt(text: str, ext: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if ext in ("ass", "ssa"):
        return _ass_to_vtt(text)
    if ext == "vtt" or text.startswith("WEBVTT"):
        return text
    return _srt_to_vtt(text)


def _tmp_name(src: str, suffix: str) -> str:
    try:
        st = os.stat(src)
        sig = f"{os.path.abspath(src)}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        sig = src
    os.makedirs(TMP, exist_ok=True)
    return os.path.join(TMP, hashlib.sha1(sig.encode("utf-8", "replace")).hexdigest()[:16] + suffix)


def prepare_subtitles(path: str) -> str:
    """→ a UTF-8 WebVTT copy. catt itself reads SRT as UTF-8 or Latin-1 only, so Cyrillic
    Windows-1251 subtitles turned into mojibake, and it can't read ASS at all."""
    out = _tmp_name(path, ".vtt")
    if not os.path.isfile(out):
        vtt = to_vtt(_read_text(path), ext_of(path))
        with open(out + ".part", "w", encoding="utf-8", newline="\n") as f:
            f.write(vtt)
        os.replace(out + ".part", out)
    return out


def prepare_image(path: str, max_side: int = 3840) -> str | None:
    """A picture the TV can show: as it is, or re-encoded to JPEG (webp / tiff / heic, or
    bigger than 4K, which older Chromecasts refuse). None if it can't be decoded."""
    from PyQt6.QtGui import QImageReader
    e = ext_of(path)
    r = QImageReader(path)
    r.setAutoTransform(True)
    size = r.size()
    big = size.isValid() and max(size.width(), size.height()) > max_side
    if e in IMAGE and not big:
        return path
    out = _tmp_name(path, ".jpg")
    if os.path.isfile(out):
        return out
    if big:
        size.scale(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio)
        r.setScaledSize(size)
    img = r.read()
    if img.isNull():
        return None if e not in IMAGE else path
    if img.hasAlphaChannel():                       # JPEG has no transparency: put it on black
        from PyQt6.QtGui import QImage
        flat = QImage(img.size(), QImage.Format.Format_RGB32)
        flat.fill(QColor("black"))
        p = QPainter(flat)
        p.drawImage(0, 0, img)
        p.end()
        img = flat
    if not img.save(out + ".part.jpg", "JPEG", 90):
        return None if e not in IMAGE else path
    os.replace(out + ".part.jpg", out)
    return out


def clean_tmp(max_age: float = 2 * 86400):
    try:
        for n in os.listdir(TMP):
            p = os.path.join(TMP, n)
            if time.time() - os.path.getmtime(p) > max_age:
                os.remove(p)
    except OSError:
        pass


# ---------------------------------------------------------------- routing

def route(paths) -> dict:
    """Dropped files / `fling send` / `fling play` → what goes where:
    {"apk": [...], "items": [{"src", "title"}...], "tv": [...], "subs": [...], "push": [...],
     "skip": [(path, why)], "origin": {"src", "title", "kind"} | None}.
    Videos, music and pictures (also from folders and playlists) form the queue; formats
    Chromecast can't play open in the TV's own player; the rest is copied to Download."""
    out = {"apk": [], "items": [], "tv": [], "subs": [], "push": [], "skip": [], "origin": None}
    loose = []
    sources = [p for p in paths if p]
    for raw in sources:
        if is_web(raw):
            out["items"].append({"src": raw, "title": None, "web": True})
            continue
        if is_unc(raw):
            out["skip"].append((raw, "сетевые папки не поддерживаются"))
            continue
        f = os.path.abspath(raw)
        if os.path.isdir(f):
            found = scan_folder(f)
            if not found:
                out["skip"].append((f, "в папке нет видео, музыки или фото"))
            out["items"] += [{"src": p, "title": None} for p in found]
            if len(sources) == 1:
                out["origin"] = {"src": f, "title": os.path.basename(f.rstrip("\\/")) or f, "kind": "folder"}
            continue
        if not os.path.isfile(f):
            out["skip"].append((raw, "это не файл"))
            continue
        k = media_kind(f)
        if ext_of(f) == "apk":
            out["apk"].append(f)
        elif k == "playlist":
            found = parse_playlist(f)
            if not found:
                out["skip"].append((f, "плейлист пуст или его файлы не найдены"))
            out["items"] += found
            if len(sources) == 1:
                out["origin"] = {"src": f, "title": pretty_title(f), "kind": "playlist"}
        elif k in ("video", "audio", "image"):
            loose.append(f)
        elif k == "subs":
            out["subs"].append(f)
        elif k == "tv":
            out["tv"].append(f)
        elif os.path.getsize(f) > F_SEND_MAX:
            out["skip"].append((f, "файл больше 4 ГБ"))
        else:
            out["push"].append(f)
    # loose files: in name order (Explorer hands them over in no useful order)
    loose.sort(key=lambda p: natural_key(os.path.basename(p)))
    out["items"] += [{"src": p, "title": None} for p in loose]
    if len(sources) == 1 and len(loose) == 1:
        out["origin"] = {"src": loose[0], "title": pretty_title(loose[0]), "kind": "file"}
    # subtitles dropped with videos belong to them
    vids = [i for i in out["items"] if not i.get("web") and media_kind(i["src"]) == "video"]
    for s in list(out["subs"]):
        stem = pretty_title(s).casefold()
        match = [v for v in vids if stem.startswith(pretty_title(v["src"]).casefold())]
        if not match and len(vids) == 1:
            match = vids
        if match:
            match[0]["subs"] = s
            out["subs"].remove(s)
    # a lone file Chromecast can't play: copy it and open it in the TV's player;
    # in a bigger drop such files are just copied
    if out["tv"] and (out["items"] or len(out["tv"]) > 1):
        out["push"] += out["tv"]
        out["tv"] = []
    return out


F_SEND_MAX = 4 * 1024 ** 3


def cli_args(args: list[str]) -> list[str]:
    """`fling play` / `fling slideshow` arguments as the running Fling will get them: web links
    and plain numbers (slideshow seconds) as they are, everything else as an absolute path.
    Tabs would split the command, so they are dropped."""
    out = []
    for a in args:
        a = a.replace("\t", " ").strip()
        if not a:
            continue
        out.append(a if is_web(a) or a.isdigit() or is_unc(a) else os.path.abspath(a))
    return out


def cli(state, parts: list[str], panel=None):
    """fling play [file|folder|playlist|link…] · fling slideshow [folder] [seconds] · fling next · fling prev"""
    verb, args = parts[0], [a for a in parts[1:] if a]
    m: Media = state.media
    if verb == "next":
        m.next()
    elif verb == "prev":
        m.prev()
    elif verb == "play":
        m.send(args) if args else m.play_files_dialog(None)
    elif verb == "slideshow":
        secs = [a for a in args if a.isdigit()]
        if secs:
            m.set_slide_sec(max(2, min(3600, int(secs[0]))))
        folders = [a for a in args if not a.isdigit()]
        if folders:
            m.start_slideshow(folders[0])
        else:
            m.play_files_dialog(None, folder=True, slideshow=True)


# ---------------------------------------------------------------- translation

def _t(ru: str, **kw) -> str:
    s = F.tr(ru) if F else ru
    return s.format(**kw) if kw else s


def speed_label(r: float) -> str:
    s = f"{r:g}×"
    return s if (F and F.LANG == "en") else s.replace(".", ",")


PHRASES = [
    ("Файлы с компьютера на ТВ…", "Files from this computer to the TV…"),
    ("Файлы с компьютера на ТВ", "Files from this computer to the TV"),
    ("Открыть на ТВ", "Open on TV"),
    ("Видео, музыка или фото…", "Videos, music or photos…"), ("Целая папка…", "A whole folder…"),
    ("Слайд-шоу из папки с фото…", "Slideshow from a photo folder…"),
    ("Недавнее", "Recent"), ("Очистить недавнее", "Clear recent"),
    ("Выберите файлы для ТВ", "Choose files for the TV"), ("Выберите папку для ТВ", "Choose a folder for the TV"),
    ("Выберите папку с фото", "Choose a folder with photos"),
    ("Видео, музыка, фото и плейлисты", "Videos, music, photos and playlists"),
    ("Все файлы", "All files"), ("Субтитры (*.srt *.vtt *.ass *.ssa)", "Subtitles (*.srt *.vtt *.ass *.ssa)"),
    ("Выберите субтитры", "Choose subtitles"),
    ("{i} из {n} · далее: {name}", "{i} of {n} · next: {name}"), ("{i} из {n}", "{i} of {n}"),
    ("Слайд-шоу: {i} из {n}", "Slideshow: {i} of {n}"),
    ("Слайд-шоу на паузе: {i} из {n}", "Slideshow paused: {i} of {n}"),
    ("Очередь остановлена: {i} из {n}", "Queue stopped: {i} of {n}"),
    ("Загружаю: {name}…", "Loading: {name}…"),
    ("Субтитры: {name}", "Subtitles: {name}"), ("Без субтитров", "No subtitles"),
    ("Субтитров рядом с видео нет", "No subtitles next to the video"),
    ("Скорость: {r}", "Speed: {r}"), ("Скорость", "Speed"),
    ("Предыдущий", "Previous"), ("Следующий", "Next"),
    ("Перемешивание: выключено", "Shuffle: off"), ("Перемешивание: включено", "Shuffle: on"),
    ("Повтор: выключен", "Repeat: off"), ("Повтор: вся очередь", "Repeat: all"),
    ("Повтор: один файл", "Repeat: one"),
    ("Пауза слайд-шоу", "Pause the slideshow"), ("Продолжить слайд-шоу", "Resume the slideshow"),
    ("Продолжить очередь", "Resume the queue"), ("Очистить очередь", "Clear the queue"),
    ("Ещё", "More"), ("Очередь", "Queue"), ("Субтитры", "Subtitles"), ("Слайд-шоу", "Slideshow"), ("Начать сначала", "Start over"),
    ("Интервал слайд-шоу", "Slideshow interval"),
    ("Автоматически: {name}", "Automatic: {name}"), ("Автоматически", "Automatic"),
    ("Выбрать файл…", "Choose a file…"), ("Выключены", "Off"),
    ("Продолжать с места остановки", "Resume where I left off"),
    ("Очередь: {n} — начинаю с «{name}»", "Queue: {n} — starting with “{name}”"),
    ("Слайд-шоу «{name}»: смена каждые {s} с", "Slideshow “{name}”: a new photo every {s} s"),
    ("Продолжаю с {t}: {name}", "Resuming at {t}: {name}"), ("Начинаю с {t}", "Starting at {t}"),
    ("Это последний файл в очереди", "That's the last item in the queue"),
    ("Очередь закончилась", "The queue has finished"),
    ("Пропускаю {name}: {why}", "Skipping {name}: {why}"),
    ("Не получилось воспроизвести {name}: {why}", "Couldn't play {name}: {why}"),
    ("ТВ не может воспроизвести этот формат или кодек", "The TV can't play this format or codec"),
    ("в папке нет видео, музыки или фото", "the folder has no videos, music or photos"),
    ("плейлист пуст или его файлы не найдены", "the playlist is empty or its files weren't found"),
    ("сетевые папки не поддерживаются", "network shares aren't supported"),
    (UNC_ERROR,
     "Network shares (\\\\server\\…) aren't supported — copy the file to this computer first."),
    ("Файл не найден: {name}", "File not found: {name}"),
    ("Chromecast не играет этот формат — копирую на ТВ и открою в плеере ТВ: {name}",
     "Chromecast can't play this format — copying it to the TV to open in the TV's player: {name}"),
    ("Открыл на ТВ: {name}", "Opened on the TV: {name}"),
    ("{name} лежит на ТВ в папке Download, но открыть его не удалось. Откройте файл в плеере на ТВ (например, VLC).",
     "{name} is in the TV's Download folder, but it couldn't be opened. Open it in a player app on the TV (VLC, for example)."),
    ("ТВ не поддерживает смену скорости для этого видео", "The TV doesn't support changing the speed of this video"),
    ("Не удалось изменить скорость: {why}", "Couldn't change the speed: {why}"),
    ("Не удалось прочитать субтитры: {why}", "Couldn't read the subtitles: {why}"),
    ("Не удалось открыть изображение: {name}", "Couldn't open the image: {name}"),
    ("Нечего воспроизвести", "Nothing to play"), ("В папке нет фото", "The folder has no photos"),
    ("обычная", "normal"), ("папка", "folder"), ("плейлист", "playlist"), ("слайд-шоу", "slideshow"),
    ("Перемешивать", "Shuffle"), ("Выключен", "Off"), ("Вся очередь", "All"), ("Один файл", "One"),
    ("Не получилось показать {name} — копирую файл на ТВ",
     "Couldn't play {name} — copying the file to the TV"),
]


# ---------------------------------------------------------------- the player

class _Listener:
    """pychromecast media status listener (runs on pychromecast's socket thread)."""
    def __init__(self, media):
        self.media = media

    def new_media_status(self, st):
        d = {"state": (st.player_state or "UNKNOWN").upper(), "idle": st.idle_reason,
             "cid": st.content_id, "t": st.adjusted_current_time, "dur": st.duration,
             "rate": getattr(st, "playback_rate", 1.0)}
        self.media.state.bridge.call.emit(lambda: self.media._on_status(d))

    def load_media_failed(self, *a):
        pass


class Media(QObject):
    """The queue and the one catt process that plays its current item."""

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.items: list[dict] = []
        self.order: list[int] = []
        self.pos = 0
        self.phase = "idle"          # idle | loading | playing | showing | stopped
        self.gen = 0
        self.proc = None
        self.proc_lock = threading.Lock()
        self.played_any = False
        self.failures = 0
        self.origin = None
        self.slide_paused = False
        self.slide = QTimer(self, singleShot=True)
        self.slide.timeout.connect(lambda: self._advance(auto=True))
        self.speed = 1.0
        # tracking what Fling cast, for resume / finished detection
        self.cur_key = None
        self.armed_at = 0.0
        self.stale_cid = None
        self.lock_cid = None
        self.last: dict = {}
        self.last_cid = None
        self.last_idle = (None, 0.0)
        self.item_started = 0.0
        self.last_pos = (None, None)
        self._saved_at = 0.0
        threading.Thread(target=clean_tmp, daemon=True, name="fling-tmp").start()

    # --- settings in fling.json
    @property
    def conf(self):
        return self.state.conf

    @property
    def repeat(self) -> str:
        return self.conf.get("repeat", "off")

    @property
    def shuffle(self) -> bool:
        return bool(self.conf.get("shuffle"))

    @property
    def slide_sec(self) -> int:
        try:
            return max(2, int(self.conf.get("slideshow_sec", SLIDE_DEFAULT)))
        except (TypeError, ValueError):
            return SLIDE_DEFAULT

    @property
    def resume_on(self) -> bool:
        return self.conf.get("resume", True)

    def _save(self):
        F.save_conf(self.conf)
        self._saved_at = time.monotonic()

    # --- what the UI asks
    @property
    def count(self) -> int:
        return len(self.order)

    @property
    def current(self) -> dict | None:
        if 0 <= self.pos < len(self.order):
            return self.items[self.order[self.pos]]
        return None

    @property
    def queue_live(self) -> bool:
        return self.count > 1 and self.phase != "idle"

    @property
    def slideshow(self) -> bool:
        c = self.current
        return (bool(c) and self.count > 1 and self.phase in ("showing", "loading")
                and not c.get("web") and media_kind(c["src"]) == "image")

    @property
    def quiet_track(self) -> bool:
        """No "Now on TV" toast per photo of a slideshow."""
        c = self.current
        return bool(c) and self.phase != "idle" and not c.get("web") and media_kind(c["src"]) == "image"

    @property
    def owns_playback(self) -> bool:
        """The TV plays what Fling sent (not YouTube from a phone, not the TV's own app)."""
        st = self.state.status or {}
        return bool(self.cur_key and self.lock_cid and st.get("content_id") == self.lock_cid
                    and not st.get("lounge") and not st.get("tvm"))

    def item_name(self, it: dict | None) -> str:
        if not it:
            return ""
        if it.get("title"):
            return it["title"]
        if it.get("web"):
            u = urlparse(it["src"])
            return os.path.basename(unquote(u.path)) or (u.hostname or it["src"])
        return pretty_title(it["src"])

    def snapshot(self) -> dict:
        c = self.current
        return {"count": self.count, "index": self.pos, "phase": self.phase,
                "title": self.item_name(c), "repeat": self.repeat, "shuffle": self.shuffle}

    # --- entry points
    def send(self, paths: list[str], slideshow: bool = False):
        """Dropped files, `fling send`, `fling play`: install / play / copy."""
        s = self.state
        r = route(paths)
        for f, why in r["skip"]:
            F.notify_send(f"{F.tr('Не отправлю ')}{os.path.basename(f.rstrip(chr(92) + '/')) or f}: {F.tr(why)}")
        if r["subs"] and not r["items"] and self.owns_playback and self._local_video():
            self.set_subtitles(r["subs"][0])           # subtitles dropped onto a playing film
            r["subs"] = []
        r["push"] += r["subs"]
        if not (r["apk"] or r["items"] or r["tv"] or r["push"]):
            if not r["skip"]:
                s._set(last_error="Нечего отправить")
            return
        if not s.ip:
            s._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        if r["apk"]:
            s.tv_install_apk(r["apk"])
        if r["items"]:
            self.play(r["items"], origin=r["origin"], slideshow=slideshow)
        for f in r["tv"]:
            self.open_in_tv_player(f)
        for f in r["push"]:
            s.adb_push(f)

    def play(self, items: list[dict], origin=None, slideshow=False, start_index=0):
        if not items:
            self.state._set(last_error="Нечего воспроизвести")
            return
        self._halt()
        self.items = [dict(i) for i in items]
        self.order = list(range(len(self.items)))
        self.pos = max(0, min(start_index, len(self.items) - 1))
        if self.shuffle and len(self.items) > 1 and not slideshow:
            first = self.order.pop(self.pos)
            random.shuffle(self.order)
            self.order.insert(0, first)
            self.pos = 0
        self.played_any, self.failures, self.origin = False, 0, origin
        self.slide_paused = False
        if origin:
            self._remember(origin)
        if len(self.items) > 1:
            first = self.item_name(self.current)
            if slideshow and origin:
                F.notify_send(_t("Слайд-шоу «{name}»: смена каждые {s} с", name=origin["title"], s=self.slide_sec))
            else:
                F.notify_send(_t("Очередь: {n} — начинаю с «{name}»", n=len(self.items), name=first))
        self._start()

    def play_files_dialog(self, parent=None, folder=False, slideshow=False):
        from PyQt6.QtWidgets import QFileDialog
        if folder:
            d = QFileDialog.getExistingDirectory(
                parent, F.tr("Выберите папку с фото" if slideshow else "Выберите папку для ТВ"),
                self.conf.get("media_dir") or os.path.expanduser("~"))
            paths = [d] if d else []
        else:
            exts = " ".join(f"*.{e}" for e in sorted(VIDEO | AUDIO | IMAGE | IMAGE_CONVERT | TV_PLAYER | PLAYLIST | SUBS))
            paths, _ = QFileDialog.getOpenFileNames(
                parent, F.tr("Выберите файлы для ТВ"), self.conf.get("media_dir") or os.path.expanduser("~"),
                f"{F.tr('Видео, музыка, фото и плейлисты')} ({exts});;{F.tr('Все файлы')} (*)")
        if not paths:
            return
        self.conf["media_dir"] = paths[0] if folder else os.path.dirname(paths[0])
        self._save()
        if slideshow:
            self.start_slideshow(paths[0])
        else:
            self.send(paths)

    def start_slideshow(self, folder: str):
        """All photos of a folder (and its subfolders), one every slide_sec seconds."""
        if is_unc(folder):
            self.state._set(last_error=UNC_ERROR)
            return
        imgs = [p for p in scan_folder(folder) if media_kind(p) == "image"] if os.path.isdir(folder) else []
        if not imgs:
            self.state._set(last_error=_t("В папке нет фото"))
            return
        name = os.path.basename(folder.rstrip("\\/")) or folder
        self.play([{"src": p, "title": None} for p in imgs], slideshow=True,
                  origin={"src": folder, "title": name, "kind": "slideshow"})

    # --- web links cast by State.cast (no queue): track them for resume and history
    def start_for(self, url: str, start: float | None) -> float | None:
        """Where a link should start: the browser's position, else ?t= in the link, else
        where it was left off last time."""
        if start and start > 3:
            return start
        t = url_start(url)
        if t:
            F.notify_send(_t("Начинаю с {t}", t=F.clock(t)))
            return t
        r = self._resume_pos(resume_key(url))
        if r:
            F.notify_send(_t("Продолжаю с {t}: {name}", t=F.clock(r), name=url[:80]))
        return r

    def before_cast(self):
        """Another cast replaces whatever Fling's queue was playing."""
        if self.phase in ("loading", "playing", "showing"):
            self._halt()
            self.phase = "stopped" if self.count > 1 else "idle"
            if self.phase == "idle":
                self._clear()
        self._track(None)

    def url_started(self, url: str, title: str | None):
        self._track(resume_key(url))
        self._arm()
        ip = self.state.ip
        self.state.bg(lambda: self._ensure_listener(ip))
        self._remember({"src": url, "title": (title or "").strip() or url, "kind": "web"})

    # --- controls
    def next(self):
        if not self.count:
            return
        if self.pos + 1 >= self.count and self.repeat != "all":
            F.notify_send(_t("Это последний файл в очереди"))
            return
        self._advance(auto=False)

    def prev(self):
        if not self.count:
            return
        el = (self.state.status or {}).get("elapsed") or 0
        c = self.current
        if c and media_kind(c["src"]) != "image" and el > 5 and self.phase == "playing":
            self._start(start_at=0)              # first press: back to the start of this one
            return
        if self.pos == 0:
            if self.repeat != "all":
                self._start(start_at=0)
                return
            self.pos = self.count
        self.pos -= 1
        self._start(start_at=0)

    def jump(self, pos: int):
        if 0 <= pos < self.count:
            self.pos = pos
            self._start(start_at=0)

    def resume_queue(self):
        if self.count:
            self._start()

    def toggle_slideshow(self) -> bool:
        """Play/pause while a slideshow runs: pause the timer, not the TV. True if handled."""
        if not self.slideshow:
            return False
        self.slide_paused = not self.slide_paused
        if self.slide_paused:
            self.slide.stop()
        else:
            self.slide.start(self.slide_sec * 1000)
        self.state.changed.emit()
        return True

    def set_repeat(self, mode: str):
        self.conf["repeat"] = mode
        self._save()
        self.state.changed.emit()

    def cycle_repeat(self):
        self.set_repeat({"off": "all", "all": "one"}.get(self.repeat, "off"))

    def toggle_shuffle(self):
        on = not self.shuffle
        self.conf["shuffle"] = on
        self._save()
        if self.count > 1:                       # reorder what's left, keep the current one
            cur = self.order[self.pos]
            rest = [i for i in range(len(self.items)) if i != cur]
            if on:
                random.shuffle(rest)
                self.order = [cur] + rest
                self.pos = 0
            else:
                self.order = list(range(len(self.items)))
                self.pos = cur
        self.state.changed.emit()

    def set_slide_sec(self, s: int):
        self.conf["slideshow_sec"] = int(s)
        self._save()
        if self.slide.isActive():
            self.slide.start(int(s) * 1000)
        self.state.changed.emit()

    def toggle_resume(self):
        self.conf["resume"] = not self.resume_on
        self._save()

    def clear(self):
        self._halt()
        self._clear()
        self.state.changed.emit()

    def stop(self, quiet=False):
        """The Stop button / another TV: end the process; a queue stays, ready to resume."""
        self._save_resume()
        if self.phase == "idle" and not self.proc:
            return
        self._halt()
        if self.count > 1 and not quiet:
            self.phase = "stopped"
        else:
            self._clear()
        self.state.changed.emit()

    def shutdown(self):
        self._save_resume()
        self._halt()
        self._save()

    def restart_item(self):
        """'Start over' for whatever Fling plays now."""
        if self.current and self.phase in ("playing", "stopped"):
            self._start(start_at=0)
        else:
            self.state._catt(["seek", "0"])

    # --- subtitles
    def _local_video(self) -> bool:
        c = self.current
        return bool(c) and not c.get("web") and media_kind(c["src"]) == "video"

    def subtitle_label(self) -> str:
        c = self.current
        if not self._local_video():
            return ""
        s = c.get("subs")
        if s == "off":
            return _t("Без субтитров")
        s = s or c.get("auto_subs")
        return _t("Субтитры: {name}", name=os.path.basename(s)) if s else _t("Без субтитров")

    def set_subtitles(self, choice: str | None):
        """None = automatic, "off", or a path. Re-sends the film from where it is now."""
        c = self.current
        if not c:
            return
        if choice and choice != "off" and is_unc(choice):
            self.state._set(last_error=UNC_ERROR)
            return
        c["subs"] = choice
        el = (self.state.status or {}).get("elapsed")
        self._start(start_at=el if el and el > 3 else 0)

    def pick_subtitles(self, parent=None):
        from PyQt6.QtWidgets import QFileDialog
        c = self.current
        d = os.path.dirname(c["src"]) if c and not c.get("web") else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(parent, F.tr("Выберите субтитры"), d,
                                              F.tr("Субтитры (*.srt *.vtt *.ass *.ssa)"))
        if path:
            self.set_subtitles(path)

    # --- speed (Default Media Receiver; over the in-process Cast link)
    def can_speed(self) -> bool:
        st = self.state.status or {}
        return bool(F.CAST.available and self.state.ip and self.state.has_media
                    and not st.get("lounge") and not st.get("tvm")
                    and "Default Media Receiver" in (st.get("app") or ""))

    def set_speed(self, r: float):
        ip = self.state.ip

        def work():
            F.CAST.info(ip)
            cc = F.CAST.cc
            if cc is None:
                raise RuntimeError("no Cast link")
            mc = cc.media_controller
            mc.update_status()
            mc.set_playback_rate(r, timeout=6)
            time.sleep(0.8)
            return abs((mc.status.playback_rate or 1.0) - r) < 0.01

        def done(ok, e):
            if e or not ok:
                print("speed:", r, e, flush=True)
                self.state._set(last_error=_t("ТВ не поддерживает смену скорости для этого видео") if not e
                                else _t("Не удалось изменить скорость: {why}", why=e))
                return
            self.speed = r
            self.state._set(last_error=None)
        self.state.bg(work, done)

    # --- files Chromecast can't play: copy and open in the TV's own player (ADB)
    def open_in_tv_player(self, path: str, why: str = ""):
        name = os.path.basename(path)
        F.notify_send(_t("Chromecast не играет этот формат — копирую на ТВ и открою в плеере ТВ: {name}", name=name))
        self.state.adb_push(path, why=why, then=lambda: self._open_on_tv(name))

    def _open_on_tv(self, name: str):
        ip = self.state.ip
        e = ext_of(name)
        mime = "audio/*" if e in AUDIO | {"wma", "ape", "ac3", "dts"} else \
            "image/*" if e in IMAGE | IMAGE_CONVERT else "video/*"
        path = "/sdcard/Download/" + name

        def work():
            # make the media store see it, then hand it to whatever player the TV has
            F.adb_cmd(ip, ["shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                           "-d", "file://" + path], timeout=15)
            r = F.adb_cmd(ip, ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                               "-d", "file://" + path, "-t", mime], timeout=15)
            out = (r.stdout or "") + (r.stderr or "")
            return r.returncode == 0 and "Error" not in out and "Exception" not in out

        def done(ok, e):
            print("open on TV:", name, ok, e, flush=True)
            if ok:
                F.notify_send(_t("Открыл на ТВ: {name}", name=name))
            else:
                self.state._set(last_error=_t("{name} лежит на ТВ в папке Download, но открыть его не удалось. "
                                              "Откройте файл в плеере на ТВ (например, VLC).", name=name))
        self.state.bg(work, done)

    # --- history ("Recent")
    def history(self) -> list[dict]:
        return [h for h in self.conf.get("history", []) if isinstance(h, dict) and h.get("src")]

    def _remember(self, entry: dict):
        h = [x for x in self.history() if x["src"] != entry["src"]]
        h.insert(0, {**entry, "at": int(time.time())})
        self.conf["history"] = h[:HISTORY_KEEP]
        self._save()

    def clear_history(self):
        self.conf["history"] = []
        self._save()
        self.state.changed.emit()

    def recast(self, h: dict):
        src = h["src"]
        if is_web(src):
            self.state.cast(src, notify=not self.state.panel_visible(), title=h.get("title"))
            return
        if is_unc(src):
            return
        if not os.path.exists(src):
            self.conf["history"] = [x for x in self.history() if x["src"] != src]
            self._save()
            self.state._set(last_error=_t("Файл не найден: {name}", name=os.path.basename(src)))
            return
        if h.get("kind") == "slideshow":
            self.start_slideshow(src)
        else:
            self.send([src])

    # --- resume positions
    def _resume_pos(self, key: str) -> float | None:
        if not self.resume_on or not key:
            return None
        v = (self.conf.get("positions") or {}).get(key)
        if isinstance(v, list) and len(v) >= 2 and v[0] and v[1]:
            pos, dur = float(v[0]), float(v[1])
            if 30 < pos < dur - 30:
                return pos
        return None

    def _store_pos(self, key, pos, dur, force=False):
        if not key or not dur or dur < RESUME_MIN or pos is None:
            return
        book = dict(self.conf.get("positions") or {})
        if pos < 30 or pos > dur - 30:
            if key not in book:
                return
            book.pop(key)                       # watched to the end (or barely started)
        else:
            book[key] = [round(pos, 1), round(dur, 1), int(time.time())]
            if len(book) > RESUME_KEEP:
                for k in sorted(book, key=lambda k: book[k][2] if len(book[k]) > 2 else 0)[:len(book) - RESUME_KEEP]:
                    book.pop(k)
        self.conf["positions"] = book
        if force or time.monotonic() - self._saved_at > 30:
            self._save()

    def _save_resume(self):
        pos, dur = self.last_pos
        if self.cur_key and pos is not None:
            self._store_pos(self.cur_key, pos, dur, force=True)

    # --- Cast status: from the pushed listener and from State.refresh
    def observe(self, st: dict):
        if not st:
            return
        self._on_status({"state": st.get("state") or "UNKNOWN", "idle": None, "cid": st.get("content_id"),
                         "t": st.get("elapsed"), "dur": st.get("duration"), "from_poll": True})

    def _on_status(self, d: dict):
        now = time.monotonic()
        self.last = d
        if d.get("cid"):
            self.last_cid = d["cid"]
        if d["state"] == "IDLE" and d.get("idle"):
            self.last_idle = (d["idle"], now)
        if not self.cur_key or not self.armed_at:
            return
        if self.lock_cid is None:
            if d["state"] in PLAYING and d.get("cid") and (d["cid"] != self.stale_cid or now - self.armed_at > 8):
                self.lock_cid = d["cid"]
                self.state.changed.emit()
            else:
                return
        if d.get("cid") and d["cid"] != self.lock_cid:
            self._save_resume()
            self._track(None)                     # the TV plays something else now
            return
        if d["state"] in PLAYING and d.get("t") is not None:
            self.last_pos = (d["t"], d.get("dur") or self.last_pos[1])
            if d.get("rate"):
                self.speed = d["rate"]
            self._store_pos(self.cur_key, *self.last_pos)

    def _track(self, key):
        self.cur_key = key
        self.armed_at = 0.0
        self.lock_cid = None
        self.stale_cid = self.last_cid
        self.last_pos = (None, None)
        self.speed = 1.0

    def _arm(self):
        self.armed_at = time.monotonic()

    def _ensure_listener(self, ip) -> bool:
        C = F.CAST
        if not C.available:
            return False
        try:
            C.info(ip)
        except Exception:
            return False
        cc = C.cc
        if cc is None or C.ip != ip:
            return False
        if getattr(cc, "_fling_media_listener", None) is None:
            lst = _Listener(self)
            cc.media_controller.register_status_listener(lst)
            cc._fling_media_listener = lst
        return True

    # --- the process
    def _clear(self):
        self.items, self.order, self.pos = [], [], 0
        self.phase = "idle"
        self.origin = None

    def _halt(self):
        """Stop the current item's catt process (and with it the LAN file server)."""
        self.gen += 1
        self.slide.stop()
        with self.proc_lock:
            p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _advance(self, auto: bool):
        if not self.count:
            return
        if auto and self.repeat == "one":
            self._start(start_at=0)
            return
        if self.pos + 1 < self.count:
            self.pos += 1
        elif self.repeat == "all":
            if self.shuffle and self.count > 2:
                last = self.order[-1]
                random.shuffle(self.order)
                if self.order[0] == last:            # don't play the same thing twice in a row
                    self.order.append(self.order.pop(0))
            self.pos = 0
        else:
            self._halt()
            self._track(None)
            self._clear()
            F.notify_send(_t("Очередь закончилась"))
            self.state.changed.emit()
            return
        self._start(start_at=0 if not auto else None)

    def _start(self, start_at: float | None = None):
        """Play self.current. start_at None = resume if remembered, 0 = from the start."""
        it = self.current
        s = self.state
        if not it:
            return
        ip = s.ip
        if not ip:
            s._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        self._halt()
        gen = self.gen
        key = resume_key(it["src"])
        if start_at is None:
            start_at = self._resume_pos(key)
            if start_at:
                F.notify_send(_t("Продолжаю с {t}: {name}", t=F.clock(start_at), name=self.item_name(it)))
        self._track(key)
        self.phase = "loading"
        self.item_started = time.monotonic()
        self.slide_paused = False
        kind = "web" if it.get("web") else media_kind(it["src"])
        if not self.played_any and self.count == 1:
            F.notify_send(f"{F.tr('Отправляю на ТВ: ')}{self.item_name(it)}…")
        s.changed.emit()
        threading.Thread(target=self._run, args=(gen, it, kind, ip, start_at), daemon=True,
                         name="fling-media").start()

    def _post(self, fn):
        self.state.bridge.call.emit(fn)

    def _run(self, gen, it, kind, ip, start_at):
        src = it["src"]
        args = ["-d", ip, "cast"]
        try:
            self._ensure_listener(ip)
            if kind == "image":
                img = prepare_image(src)
                if not img:
                    self._post(lambda: self._failed(gen, it, _t("Не удалось открыть изображение: {name}",
                                                                 name=os.path.basename(src)), image=True))
                    return
                src = img
                args += ["--no-subs", "-l", self.item_name(it)[:200]]
            elif kind == "web":
                args += ["-b"] + (["-f"] if F.classify(src)[0] == "direct" else [])
            else:
                args += ["-l", self.item_name(it)[:200]]
                if kind == "video":
                    sub = it.get("subs")
                    if sub == "off":
                        args += ["--no-subs"]
                    else:
                        sub = sub or find_subtitles(src)
                        it["auto_subs"] = sub if not it.get("subs") else None
                        if sub:
                            try:
                                args += ["-s", prepare_subtitles(sub)]
                            except Exception as e:
                                print("subtitles:", sub, e, flush=True)
                                F.notify_send(_t("Не удалось прочитать субтитры: {why}", why=e))
                                args += ["--no-subs"]
                        else:
                            args += ["--no-subs"]
                else:
                    args += ["--no-subs"]
            if start_at and start_at > 1 and kind != "image":
                args += ["-t", str(int(start_at))]
            args.append(src)
            p = F.P.popen(F.P.catt_cmd(F.CATT, args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=F.P.CATT_ENV)
        except Exception as e:
            print("media start:", e, flush=True)
            msg = str(e)                           # `e` is gone once the except block ends
            self._post(lambda: self._failed(gen, it, msg))
            return
        with self.proc_lock:
            if gen != self.gen:                    # replaced while preparing
                try:
                    p.kill()
                except OSError:
                    pass
                return
            self.proc = p
        tail = []
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            tail = (tail + [line])[-12:]
            if line.startswith("Serving local file") or (kind == "web" and line.startswith("Playing ")):
                self._post(lambda: self._loaded(gen, it))
        code = p.wait()
        text = "\n".join(tail)
        self._post(lambda: self._exited(gen, it, kind, code, text))

    def _loaded(self, gen, it):
        if gen != self.gen:
            return
        self._arm()
        self.phase = "playing"
        self.failures = 0
        if not self.played_any and self.count == 1:
            F.notify_send(f"{F.tr('На ТВ: ')}{self.item_name(it)}")
        self.played_any = True
        if self.count == 1 and not self.origin:
            self._remember({"src": it["src"], "title": self.item_name(it), "kind": "web" if it.get("web") else "file"})
        self.state._set(last_error=None)
        QTimer.singleShot(1500, lambda: self.state.refresh(with_tab=False))

    def _exited(self, gen, it, kind, code, text):
        print(f"media {self.item_name(it)}: exit {code}\n{text[-600:]}", flush=True)
        if gen != self.gen:
            return                                  # stopped on purpose
        with self.proc_lock:
            self.proc = None
        if code != 0:
            err = next((l[6:].strip() for l in reversed(text.splitlines()) if l.startswith("Error:")), "") \
                or f"catt {code}"
            self._failed(gen, it, err)
            return
        if kind == "image":
            self.played_any = True
            self.failures = 0
            self.phase = "showing"
            self.state._set(last_error=None)
            if self.count > 1 and (self.pos + 1 < self.count or self.repeat != "off"):
                if not self.slide_paused:
                    self.slide.start(self.slide_sec * 1000)
            elif self.count == 1:
                F.notify_send(f"{F.tr('На ТВ: ')}{self.item_name(it)}")
                if not self.origin:
                    self._remember({"src": it["src"], "title": self.item_name(it), "kind": "file"})
            self.state.changed.emit()
            return
        # the TV went idle: finished, or stopped / replaced? Let the last status arrive.
        QTimer.singleShot(1200, lambda: self._decide(gen))

    def _decide(self, gen):
        if gen != self.gen:
            return
        reason, at = self.last_idle
        pos, dur = self.last_pos
        if reason is not None and at >= self.item_started:
            finished = reason == "FINISHED"          # the TV says why it stopped
        else:                                        # no pushed status (catt in its own env): guess
            finished = bool(pos and dur and pos >= dur - max(25, dur * 0.03))
        if finished and self.cur_key:
            self._store_pos(self.cur_key, dur, dur, force=True)      # watched: drop the bookmark
        else:
            self._save_resume()
        if finished and (self.count > 1 or self.repeat == "one"):
            self._advance(auto=True)
            return
        self._track(None)
        if self.count > 1 and not finished:
            self.phase = "stopped"                   # stopped on the TV: the queue waits
        else:
            self._clear()
        self.state.changed.emit()

    def _failed(self, gen, it, err, image=False):
        if gen != self.gen:
            return
        with self.proc_lock:
            self.proc = None
        why = _t("ТВ не может воспроизвести этот формат или кодек") if "failed to load media" in err.lower() \
            or "codec" in err.lower() else F.tr(err)
        name = self.item_name(it)
        print("media failed:", name, err, flush=True)
        if self.count == 1:
            self._clear()
            self._track(None)
            if not it.get("web") and not image and media_kind(it["src"]) in ("video", "audio"):
                # the TV couldn't fetch or decode it: the TV's own player may manage
                F.notify_send(_t("Не получилось показать {name} — копирую файл на ТВ", name=os.path.basename(it["src"])))
                self.state.adb_push(it["src"], why=err, then=lambda: self._open_on_tv(os.path.basename(it["src"])))
            else:
                self.state._set(last_error=_t("Не получилось воспроизвести {name}: {why}", name=name, why=why))
            return
        self.failures += 1
        if self.failures >= 3 or (not self.played_any and self.failures >= 2):
            self._halt()
            self.phase = "stopped"
            self.state._set(last_error=_t("Не получилось воспроизвести {name}: {why}", name=name, why=why))
            return
        F.notify_send(_t("Пропускаю {name}: {why}", name=name, why=why))
        if self.pos + 1 >= self.count and self.repeat != "all":
            self._halt()
            self._clear()
            self.state.changed.emit()
            return
        self._advance(auto=False)


# ---------------------------------------------------------------- UI

def _icon(name: str, size: int = 14, color: QColor | None = None, weight: float = 1.6) -> QIcon:
    """Line icons in the style of fling_app.glyph, for the queue strip."""
    color = color or F.T.fg
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1
    pm = QPixmap(int(size * dpr * 2), int(size * dpr * 2))
    pm.setDevicePixelRatio(dpr * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, weight * size / 14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    s = size / 14.0

    def P(x, y):
        return QPointF(x * s, y * s)

    def arrow_head(tip, dx):           # small open chevron pointing along x
        p.drawLine(tip, P(tip.x() / s - dx, tip.y() / s - 2))
        p.drawLine(tip, P(tip.x() / s - dx, tip.y() / s + 2))

    if name == "shuffle":
        a = QPainterPath(P(1.5, 4)); a.cubicTo(P(6, 4), P(7, 10), P(12, 10)); p.drawPath(a)
        b = QPainterPath(P(1.5, 10)); b.cubicTo(P(6, 10), P(7, 4), P(12, 4)); p.drawPath(b)
        arrow_head(P(12.5, 4), 2.2); arrow_head(P(12.5, 10), 2.2)
    elif name in ("repeat", "repeat_one"):
        path = QPainterPath(P(3, 7.5))
        path.lineTo(P(3, 5.5)); path.quadTo(P(3, 3.5), P(5, 3.5)); path.lineTo(P(12, 3.5))
        p.drawPath(path)
        path = QPainterPath(P(11, 6.5))
        path.lineTo(P(11, 8.5)); path.quadTo(P(11, 10.5), P(9, 10.5)); path.lineTo(P(2, 10.5))
        p.drawPath(path)
        arrow_head(P(12, 3.5), 2); p.drawLine(P(2, 10.5), P(4, 8.5)); p.drawLine(P(2, 10.5), P(4, 12.5))
        if name == "repeat_one":
            f = QFont(); f.setPixelSize(max(6, int(6.5 * s))); f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(4 * s, 4.2 * s, 6 * s, 5.8 * s), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "more":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        for x in (3, 7, 11):
            p.drawEllipse(P(x, 7), 1.25 * s, 1.25 * s)
    elif name in ("prev", "next"):
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        tri = QPainterPath()
        if name == "next":
            tri.moveTo(P(3, 3)); tri.lineTo(P(9.5, 7)); tri.lineTo(P(3, 11)); tri.closeSubpath()
            p.drawPath(tri); p.drawRoundedRect(QRectF(10 * s, 3 * s, 1.8 * s, 8 * s), 0.6 * s, 0.6 * s)
        else:
            tri.moveTo(P(11, 3)); tri.lineTo(P(4.5, 7)); tri.lineTo(P(11, 11)); tri.closeSubpath()
            p.drawPath(tri); p.drawRoundedRect(QRectF(2.2 * s, 3 * s, 1.8 * s, 8 * s), 0.6 * s, 0.6 * s)
    elif name == "play":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        tri = QPainterPath(); tri.moveTo(P(4, 2.5)); tri.lineTo(P(11.5, 7)); tri.lineTo(P(4, 11.5)); tri.closeSubpath()
        p.drawPath(tri)
    elif name == "pause":
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        p.drawRoundedRect(QRectF(3.5 * s, 2.5 * s, 2.4 * s, 9 * s), 0.7 * s, 0.7 * s)
        p.drawRoundedRect(QRectF(8.1 * s, 2.5 * s, 2.4 * s, 9 * s), 0.7 * s, 0.7 * s)
    elif name == "close":
        p.drawLine(P(3.5, 3.5), P(10.5, 10.5)); p.drawLine(P(10.5, 3.5), P(3.5, 10.5))
    elif name == "folder":
        path = QPainterPath(P(1.5, 11.5))
        path.lineTo(P(1.5, 3)); path.lineTo(P(5.5, 3)); path.lineTo(P(7, 4.5)); path.lineTo(P(12.5, 4.5))
        path.lineTo(P(12.5, 11.5)); path.closeSubpath()
        p.drawPath(path)
    p.end()
    return QIcon(pm)


class IconButton(QPushButton):
    def __init__(self, name, tip, on, size=24):
        super().__init__()
        self.name, self.active = name, False
        self.setFixedSize(size, size)
        self.setIconSize(QSize(14, 14))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.clicked.connect(on)
        self.set_state(name, False)

    def set_state(self, name, active):
        self.name, self.active = name, active
        T = F.T
        self.setIcon(_icon(name, 14, T.accent if active else T.fg_a(0.75)))
        bg = T.acc_a(0.18) if active else QColor(0, 0, 0, 0)
        self.setStyleSheet(
            f"QPushButton{{border:none;border-radius:6px;background:{F.Theme.css(bg)}}}"
            f"QPushButton:hover{{background:{F.Theme.css(T.acc_a(0.22))}}}"
            f"QPushButton:pressed{{background:{F.Theme.css(T.acc_a(0.35))}}}")


class ElidedLabel(QLabel):
    """One line that ends in … instead of widening the panel."""
    def __init__(self, px=11, color=None):
        super().__init__()
        self._full = ""
        self.setFont(F.font(px))
        self.setStyleSheet(f"color:{F.Theme.css(color or F.T.fg_a(0.6))};background:transparent")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(40)

    def setText(self, s):
        self._full = F.tr(s or "")
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        fm = self.fontMetrics()
        w = max(10, self.contentsRect().width())
        QLabel.setText(self, fm.elidedText(self._full, Qt.TextElideMode.ElideRight, w))
        self.setToolTip(self._full if fm.horizontalAdvance(self._full) > w else "")


class QueueStrip(QWidget):
    """One line under the progress bar while Fling plays a queue, a slideshow or a local film:
    "3 of 12 · next: …" (or the subtitles in use) with prev / play / next, shuffle, repeat
    and a ⋯ menu (queue list, speed, subtitles, slideshow interval)."""

    def __init__(self, state, where="casting"):
        super().__init__()
        self.state, self.where = state, where
        self.m: Media = state.media
        self.text = ElidedLabel(11)
        if where == "casting":            # one line: text, then the buttons
            lay = QHBoxLayout(self)
            lay.setContentsMargins(14, 2, 10, 2)
            lay.setSpacing(2)
            lay.addWidget(self.text, 1)
            lay.addSpacing(4)
            self.setFixedHeight(28)
        else:                             # idle: the text gets the whole width, buttons under it
            from PyQt6.QtWidgets import QVBoxLayout
            col = QVBoxLayout(self)
            col.setContentsMargins(9, 0, 10, 6)       # 9 + the icon's 5 px inset = the text's 14
            col.setSpacing(2)
            self.text.setContentsMargins(5, 0, 0, 0)
            col.addWidget(self.text)
            lay = QHBoxLayout()
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            col.addLayout(lay)
        self.b_prev = IconButton("prev", F.tr("Предыдущий"), self.m.prev)
        self.b_play = IconButton("play", F.tr("Продолжить очередь"), self._play)
        self.b_next = IconButton("next", F.tr("Следующий"), self.m.next)
        self.b_shuffle = IconButton("shuffle", "", self.m.toggle_shuffle)
        self.b_repeat = IconButton("repeat", "", self.m.cycle_repeat)
        self.b_more = IconButton("more", F.tr("Ещё"), self._menu)
        self.b_close = IconButton("close", F.tr("Очистить очередь"), self.m.clear)
        for b in (self.b_prev, self.b_play, self.b_next, self.b_shuffle, self.b_repeat, self.b_more, self.b_close):
            lay.addWidget(b)
        if where != "casting":
            lay.addStretch(1)
        state.changed.connect(self.sync)
        self.sync()

    def _play(self):
        if self.m.slideshow:
            self.m.toggle_slideshow()
        else:
            self.m.resume_queue()

    def sync(self):
        m, s = self.m, self.state
        casting = s.panel == "casting"
        queue = m.count > 1 and m.phase != "idle"
        single_local = m.count == 1 and m.owns_playback and m._local_video()
        single_web = m.count == 0 and m.owns_playback and m.can_speed()
        if self.where == "casting":
            show = casting and (queue or single_local or single_web)
        else:                             # idle view: a stopped queue or a slideshow between pictures
            show = not casting and queue
        self.setVisible(bool(show))
        if not show:
            return
        i, n = m.pos + 1, m.count
        nxt = m.items[m.order[m.pos + 1]] if m.pos + 1 < n else (m.items[m.order[0]] if m.repeat == "all" and n else None)
        if queue:
            if m.phase == "stopped":
                txt = _t("Очередь остановлена: {i} из {n}", i=i, n=n)
            elif m.slideshow:
                txt = _t("Слайд-шоу на паузе: {i} из {n}" if m.slide_paused else "Слайд-шоу: {i} из {n}", i=i, n=n)
            elif m.phase == "loading":
                txt = _t("{i} из {n}", i=i, n=n) + " · " + _t("Загружаю: {name}…", name=m.item_name(m.current))
            elif nxt is not None:
                txt = _t("{i} из {n} · далее: {name}", i=i, n=n, name=m.item_name(nxt))
            else:
                txt = _t("{i} из {n}", i=i, n=n)
        elif single_local:
            txt = m.subtitle_label()
        else:
            txt = _t("Скорость: {r}", r=speed_label(m.speed) if m.speed != 1 else F.tr("обычная"))
        self.text.setText(txt)
        for b in (self.b_prev, self.b_next, self.b_shuffle, self.b_repeat):
            b.setVisible(queue)
        self.b_play.setVisible(queue and (m.phase == "stopped" or m.slideshow))
        self.b_play.set_state("play" if m.phase == "stopped" or m.slide_paused else "pause", False)
        self.b_play.setToolTip(F.tr("Продолжить очередь" if m.phase == "stopped" else
                                    "Продолжить слайд-шоу" if m.slide_paused else "Пауза слайд-шоу"))
        self.b_close.setVisible(queue and m.phase == "stopped")
        self.b_shuffle.set_state("shuffle", m.shuffle)
        self.b_shuffle.setToolTip(F.tr("Перемешивание: включено" if m.shuffle else "Перемешивание: выключено"))
        rep = m.repeat
        self.b_repeat.set_state("repeat_one" if rep == "one" else "repeat", rep != "off")
        self.b_repeat.setToolTip(F.tr({"off": "Повтор: выключен", "all": "Повтор: вся очередь",
                                       "one": "Повтор: один файл"}.get(rep, "Повтор: выключен")))

    def _menu(self):
        menu = QMenu(self)
        fill_more_menu(menu, self.state, self.window())
        menu.exec(self.b_more.mapToGlobal(self.b_more.rect().bottomLeft()))


def fill_more_menu(menu: QMenu, state, parent=None, tray=False):
    """Queue list, speed, subtitles, slideshow interval, start over, clear."""
    from PyQt6.QtGui import QActionGroup
    m: Media = state.media
    if m.count > 1:
        qm = menu.addMenu(F.tr("Очередь"))
        lo = max(0, min(m.pos - 10, m.count - 40))
        for k in range(lo, min(m.count, lo + 40)):
            name = m.item_name(m.items[m.order[k]])[:70].replace("&", "&&")
            a = qm.addAction(f"{k + 1}. {name}", lambda k=k: m.jump(k))
            a.setCheckable(True); a.setChecked(k == m.pos)
    if m._local_video():
        sm = menu.addMenu(F.tr("Субтитры"))
        c = m.current
        auto = c.get("auto_subs") or find_subtitles(c["src"])
        g = QActionGroup(sm)
        a = sm.addAction(_t("Автоматически: {name}", name=os.path.basename(auto)) if auto
                         else F.tr("Субтитров рядом с видео нет"), lambda: m.set_subtitles(None))
        a.setCheckable(True); a.setChecked(not c.get("subs")); a.setEnabled(bool(auto)); g.addAction(a)
        if c.get("subs") and c["subs"] != "off":
            a = sm.addAction(os.path.basename(c["subs"]), lambda: None)
            a.setCheckable(True); a.setChecked(True); g.addAction(a)
        a = sm.addAction(F.tr("Выключены"), lambda: m.set_subtitles("off"))
        a.setCheckable(True); a.setChecked(c.get("subs") == "off"); g.addAction(a)
        sm.addSeparator()
        sm.addAction(F.tr("Выбрать файл…"), lambda: m.pick_subtitles(parent))
    if m.can_speed():
        sp = menu.addMenu(F.tr("Скорость"))
        g = QActionGroup(sp)
        for r in SPEEDS:
            a = sp.addAction(speed_label(r) + (f" ({F.tr('обычная')})" if r == 1 else ""), lambda r=r: m.set_speed(r))
            a.setCheckable(True); a.setChecked(abs(m.speed - r) < 0.01); g.addAction(a)
    if tray or any(media_kind(i["src"]) == "image" for i in m.items if not i.get("web")):
        im = menu.addMenu(F.tr("Интервал слайд-шоу"))
        g = QActionGroup(im)
        for sec in SLIDE_CHOICES:
            a = im.addAction(f"{sec} с", lambda sec=sec: m.set_slide_sec(sec))
            a.setCheckable(True); a.setChecked(m.slide_sec == sec); g.addAction(a)
    if state.has_media and (m.owns_playback or m.count):
        menu.addAction(F.tr("Начать сначала"), m.restart_item)
    if m.count > 1:
        menu.addSeparator()
        menu.addAction(F.tr("Очистить очередь"), m.clear)


def fill_open_menu(menu: QMenu, state, parent=None, before=None):
    """Files / folder / slideshow, then recent casts. `before` runs first (hide the popup panel:
    a file dialog over a Qt.Popup would close it anyway)."""
    m: Media = state.media

    def go(fn):
        def run():
            if before:
                before()
            fn()
        return run
    menu.addAction(F.tr("Видео, музыка или фото…"), go(lambda: m.play_files_dialog(parent)))
    menu.addAction(F.tr("Целая папка…"), go(lambda: m.play_files_dialog(parent, folder=True)))
    menu.addAction(F.tr("Слайд-шоу из папки с фото…"), go(lambda: m.play_files_dialog(parent, folder=True, slideshow=True)))
    hist = m.history()
    if hist:
        menu.addSeparator()
        head = menu.addAction(F.tr("Недавнее"))
        head.setEnabled(False)
        for h in hist[:12]:
            src = h["src"]
            where = {"folder": F.tr("папка"), "playlist": F.tr("плейлист"), "slideshow": F.tr("слайд-шоу")}.get(
                h.get("kind")) or (F.host_of(src) if is_web(src) else F.tr("файл"))
            title = (h.get("title") or src).replace("&", "&&")
            if len(title) > 56:
                title = title[:55] + "…"
            menu.addAction(f"{title}  ·  {where}", lambda h=h: m.recast(h))
        menu.addAction(F.tr("Очистить недавнее"), m.clear_history)


def fill_tray_menu(menu: QMenu, state, panel=None):
    """Tray → Open on TV: the open menu plus queue controls and options."""
    m: Media = state.media
    fill_open_menu(menu, state, None)
    if m.count > 1:
        menu.addSeparator()
        menu.addAction(F.tr("Предыдущий"), m.prev)
        if m.slideshow:
            menu.addAction(F.tr("Продолжить слайд-шоу" if m.slide_paused else "Пауза слайд-шоу"), m.toggle_slideshow)
        elif m.phase == "stopped":
            menu.addAction(F.tr("Продолжить очередь"), m.resume_queue)
        menu.addAction(F.tr("Следующий"), m.next)
    menu.addSeparator()
    a = menu.addAction(F.tr("Перемешивать"), m.toggle_shuffle)
    a.setCheckable(True); a.setChecked(m.shuffle)
    rm = menu.addMenu(F.tr({"off": "Повтор: выключен", "all": "Повтор: вся очередь",
                            "one": "Повтор: один файл"}.get(m.repeat, "Повтор: выключен")))
    from PyQt6.QtGui import QActionGroup
    g = QActionGroup(rm)
    for key, title in (("off", "Выключен"), ("all", "Вся очередь"), ("one", "Один файл")):
        a = rm.addAction(F.tr(title), lambda k=key: m.set_repeat(k))
        a.setCheckable(True); a.setChecked(m.repeat == key); g.addAction(a)
    a = menu.addAction(F.tr("Продолжать с места остановки"), m.toggle_resume)
    a.setCheckable(True); a.setChecked(m.resume_on)
    more = QMenu(menu)
    fill_more_menu(more, state, None, tray=True)
    for act in more.actions():
        if act.menu() is not None or act.text() in (F.tr("Начать сначала"),):
            menu.addAction(act)
    menu._more = more                      # keep the submenus alive with the tray menu


class OpenRow(QWidget):
    """Idle panel: "Files from this computer to the TV…" → files, folder, slideshow, recent."""

    def __init__(self, state, panel):
        super().__init__()
        self.state, self.panel = state, panel
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.row = F.MenuRow("Файлы с компьютера на ТВ…", on=self._menu)
        self.row.ic.setVisible(True)
        self.row.ic.setPixmap(_icon("folder", 14, F.T.fg_a(0.55)).pixmap(15, 15))
        lay.addWidget(self.row)
        state.changed.connect(self.sync)
        self.sync()

    def sync(self):
        want = not self.state.conf.get("compact") and self.state.panel != "setup"
        if want and self.isWindow():   # not in the panel yet: showing it now would flash a window
            return
        self.setVisible(want)

    def _menu(self):
        menu = QMenu(self)
        fill_open_menu(menu, self.state, None, before=self.panel.hide)
        menu.exec(self.row.mapToGlobal(self.row.rect().bottomLeft()))
