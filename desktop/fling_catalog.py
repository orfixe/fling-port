# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Catalog: extra modules from official sources only (the "Каталог" tab of the Tools window).

  * TV apps — free / open-source Android apps that fit a casting and remote tool (VLC,
    Kodi, Jellyfin, Moonlight, LocalSend, Tailscale, F-Droid, Material Files). Fling downloads the
    APK built for the TV's processor from the project's own channel, verifies it and
    installs it over ADB; shows which are already on the TV, opens and removes them;
  * PC programs — registered as ordinary toolkit tools (fling_tools.Tool, catalog=True),
    so they get the same detection, installer, guard registration and card.

Sources ("where such packages come from") are small pluggable providers. Each one says
how far it is trusted:

  GitHub      the project's own repository, latest non-prerelease release. The SHA-256 is
              GitHub's `digest` of the uploaded asset (computed by GitHub at upload) and,
              where the project publishes one, its checksum file — both must agree.
  Mirrorbits  VideoLAN (get.videolan.org) and Kodi (mirrors.kodi.tv) send downloads to
              volunteer mirrors. The SHA-256 is asked from the official redirector itself
              (`file?sha256`, no redirect allowed), which hashes the master copy — so a bad
              mirror cannot slip in another file.
  Tailscale   pkgs.tailscale.com: the APK named by `?mode=json` and its `.sha256` file, both
              from Tailscale's own server with redirects refused.
  F-Droid     f-droid.org's index: entry.jar is signed with the F-Droid repository key
              (pinned below); it gives the SHA-256 of index-v2.json, and that index gives
              the SHA-256, size and signer of every APK.

On top of the checksum, every APK must be signed by the developer's own certificate
(pinned per app below, or the signer the signed F-Droid index lists) and must carry the
expected package name; Android itself then checks the signature when it installs. A
download that fails any check is deleted and never reaches the TV.

The detection / download half never touches Qt and can run on any thread. The UI reuses
fling_tools' look (bound by fling_app) and its job board, so progress shows the same way.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mmap
import os
import re
import shutil
import struct
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

import fling_platform as P
import fling_tools as K
from fling_tools import Release, ToolError, parse_version

PKG_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
DEFAULT_ABIS = ["armeabi-v7a"]          # what is shown before the TV said what it runs
PEEK_TTL = 6 * 3600                     # how long a "latest version" answer is reused
CACHE = os.path.join(K.ROOT, ".cache")


# ================================================================ APK inspection (no aapt)

def _lp(buf: bytes, i: int) -> tuple[bytes, int]:
    """A uint32-length-prefixed field of the APK Signing Block → (bytes, next offset)."""
    if i + 4 > len(buf):
        raise ValueError("truncated")
    n = struct.unpack_from("<I", buf, i)[0]
    if i + 4 + n > len(buf):
        raise ValueError("truncated")
    return buf[i + 4:i + 4 + n], i + 4 + n


def _lp_items(buf: bytes) -> list[bytes]:
    out, i = [], 0
    while i < len(buf):
        item, i = _lp(buf, i)
        out.append(item)
    return out


_SCHEMES = ((0x1B93AD61, "v3.1"), (0xF05368C0, "v3"), (0x7109871A, "v2"))


def _signing_block_certs(f, size: int) -> tuple[str, list[bytes]]:
    """Signer certificates from the APK Signing Block (v3.1 > v3 > v2); ("", []) if none."""
    tail_len = min(size, 0xFFFF + 22)
    f.seek(size - tail_len)
    tail = f.read(tail_len)
    e = tail.rfind(b"PK\x05\x06")
    if e < 0:
        raise ToolError("файл не похож на приложение Android (APK)")
    cd_off = struct.unpack_from("<I", tail, e + 16)[0]
    if cd_off < 32 or cd_off > size:
        return "", []
    f.seek(cd_off - 24)
    foot = f.read(24)
    if foot[8:] != b"APK Sig Block 42":
        return "", []
    bsize = struct.unpack("<Q", foot[:8])[0]
    if bsize > cd_off or bsize > 64 << 20:
        return "", []
    f.seek(cd_off - bsize - 8)
    block = f.read(bsize + 8)
    pairs, i, found = block[8:-24], 0, {}
    while i + 12 <= len(pairs):
        n = struct.unpack_from("<Q", pairs, i)[0]
        if n < 4 or i + 8 + n > len(pairs):
            break
        found[struct.unpack_from("<I", pairs, i + 8)[0]] = pairs[i + 12:i + 8 + n]
        i += 8 + n
    for sid, name in _SCHEMES:
        if sid not in found:
            continue
        certs = []
        for signer in _lp_items(_lp(found[sid], 0)[0]):
            signed_data, _ = _lp(signer, 0)
            _digests, j = _lp(signed_data, 0)
            cert_seq, _ = _lp(signed_data, j)
            certs += _lp_items(cert_seq)[:1]          # the first one is the signer's own
        if certs:
            return name, certs
    return "", []


def _v1_certs(z: zipfile.ZipFile) -> list[bytes]:
    """JAR signing (v1): the signer certificate of META-INF/*.RSA|DSA|EC."""
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
    out = []
    for n in z.namelist():
        if re.fullmatch(r"META-INF/[^/]+\.(RSA|DSA|EC)", n, re.I):
            certs = pkcs7.load_der_pkcs7_certificates(z.read(n))
            if certs:
                out.append(certs[0].public_bytes(Encoding.DER))
    return out


# --- AndroidManifest.xml is binary XML (AXML): read <manifest package versionCode versionName>

_ATTR_IDS = {0x0101021B: "versionCode", 0x0101021C: "versionName"}


def _axml_strings(buf: bytes, at: int) -> list[str]:
    _t, hsz, _sz, count, _styles, flags, start = struct.unpack_from("<HHIIIII", buf, at)
    utf8 = bool(flags & 0x100)
    offs = struct.unpack_from(f"<{count}I", buf, at + hsz)
    base = at + start
    out = []
    for o in offs:
        p = base + o
        if utf8:
            n = buf[p]; p += 2 if n & 0x80 else 1               # length in UTF-16 units (skipped)
            n = buf[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | buf[p + 1]; p += 2
            else:
                p += 1
            out.append(buf[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", buf, p)[0]; p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]; p += 2
            out.append(buf[p:p + 2 * n].decode("utf-16-le", "replace"))
    return out


def parse_manifest(buf: bytes) -> dict:
    """package / versionCode / versionName of a binary AndroidManifest.xml."""
    if len(buf) < 8 or struct.unpack_from("<H", buf, 0)[0] != 0x0003:
        raise ToolError("файл не похож на приложение Android (APK)")
    strings, res_ids, i = [], [], 8
    while i + 8 <= len(buf):
        typ, hsz, size = struct.unpack_from("<HHI", buf, i)
        if size < 8:
            break
        if typ == 0x0001:
            strings = _axml_strings(buf, i)
        elif typ == 0x0180:
            res_ids = list(struct.unpack_from(f"<{(size - hsz) // 4}I", buf, i + hsz))
        elif typ == 0x0102:                                   # the first element: <manifest>
            name_i = struct.unpack_from("<I", buf, i + 20)[0]
            a_start, a_size, a_count = struct.unpack_from("<HHH", buf, i + 24)
            if strings[name_i] != "manifest":
                break
            out = {}
            for k in range(a_count):
                p = i + 16 + a_start + k * a_size
                _ns, an, raw, _vs, _r0, dtype, data = struct.unpack_from("<IIIHBBI", buf, p)
                key = strings[an] if an < len(strings) and strings[an] else None
                if not key and an < len(res_ids):
                    key = _ATTR_IDS.get(res_ids[an])
                if key == "package" or key in ("versionCode", "versionName"):
                    if dtype == 0x03 and raw != 0xFFFFFFFF:
                        out[key] = strings[raw]
                    elif dtype in (0x10, 0x11):
                        out[key] = data
            return out
        i += size
    raise ToolError("в APK нет описания приложения (AndroidManifest)")


@dataclass
class ApkInfo:
    pkg: str
    version_name: str
    version_code: int
    scheme: str                       # v3.1 | v3 | v2 | v1
    signers: list[str]                # SHA-256 of each signer certificate (hex)


def apk_info(path: str) -> ApkInfo:
    """Package, version and signer certificates of an APK, without aapt. The signature
    itself is checked by Android when it installs; here the certificate is compared with
    the one the developer is known to sign with."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        scheme, certs = _signing_block_certs(f, size)
    try:
        with zipfile.ZipFile(path) as z:
            man = parse_manifest(z.read("AndroidManifest.xml"))
            if not certs:
                scheme, certs = "v1", _v1_certs(z)
    except (zipfile.BadZipFile, KeyError, struct.error, IndexError) as e:
        raise ToolError("файл не похож на приложение Android (APK)") from e
    if not certs:
        raise ToolError("APK не подписан — устанавливать не буду")
    vc = man.get("versionCode")
    return ApkInfo(str(man.get("package") or ""), str(man.get("versionName") or ""),
                   int(vc) if isinstance(vc, int) or str(vc).isdigit() else 0, scheme,
                   [hashlib.sha256(c).hexdigest() for c in certs])


# ================================================================ HTTPS helpers

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        raise ToolError("официальный сервер перенаправил запрос контрольной суммы — отменено")


_STRICT = urllib.request.build_opener(_NoRedirect)


def _get_strict(url: str, limit: int = 1 << 20) -> bytes:
    """GET from exactly this HTTPS host — for checksums, which must not come from a mirror."""
    if urlparse(url).scheme != "https":
        raise ToolError("загрузка возможна только по HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": K.UA})
    try:
        with _STRICT.open(req, timeout=20) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise ToolError(f"сервер ответил {e.code}") from e
    except urllib.error.URLError as e:
        raise ToolError("нет связи с сервером загрузки") from e
    if len(data) > limit:
        raise ToolError("ответ сервера слишком большой")
    return data


def _hex64(s: str | None) -> str | None:
    s = (s or "").strip().lower()
    return s if re.fullmatch(r"[0-9a-f]{64}", s) else None


# ================================================================ sources

class Source:
    """Where an APK comes from. latest(abis) → the newest stable Release for the first of
    the TV's processors (abis, in the TV's order) that the publisher builds for, with the
    publisher's SHA-256. peek() is the same for display and may skip the heavy parts."""
    name = ""                         # shown in the card: "GitHub", "VideoLAN"…
    page = ""                         # the official page it is shown with

    def latest(self, abis: list[str], j: K.Job | None = None) -> Release:
        raise NotImplementedError

    def peek(self, abis: list[str]) -> Release:
        return self.latest(abis)


def _pick_abi(abis: list[str], have: dict) -> str | None:
    """The TV's first processor the publisher has a build for; "" = one APK for all."""
    if "" in have:
        return ""
    return next((a for a in abis if a in have), None)


class GitHub(Source):
    """Release assets of github.com/<repo>. assets: {abi or "": regex of the file name}."""
    name = "GitHub"

    def __init__(self, repo: str, assets: dict[str, str], sums: str = ""):
        self.repo, self.assets, self.sums = repo, assets, sums
        self.page = f"https://github.com/{repo}/releases"

    def latest(self, abis, j=None):
        rel = json.loads(K._get_bytes(f"https://api.github.com/repos/{self.repo}/releases/latest", 20))
        by_name = {a.get("name", ""): a for a in rel.get("assets", [])}
        have = {}
        for abi, rx in self.assets.items():
            name = next((n for n in by_name if re.fullmatch(rx, n)), None)
            if name:
                have[abi] = name
        abi = _pick_abi(abis, have)
        if abi is None:
            raise ToolError("у издателя нет сборки для процессора этого ТВ")
        name = have[abi]
        a = by_name[name]
        digest = (a.get("digest") or "").lower()
        sha = _hex64(digest[7:]) if digest.startswith("sha256:") else None
        if self.sums and self.sums in by_name:
            text = K._get_bytes(by_name[self.sums]["browser_download_url"], 20).decode(errors="replace")
            listed = next((_hex64(l.split()[0]) for l in text.splitlines()
                           if l.split() and l.split()[-1].lstrip("*") == name), None)
            if sha and listed and listed != sha:
                raise ToolError("контрольные суммы издателя не совпадают — установка отменена")
            sha = listed or sha
        if not sha:
            raise ToolError("издатель не дал контрольную сумму — устанавливать не буду")
        url = a.get("browser_download_url", "")
        if not url.startswith(f"https://github.com/{self.repo}/releases/download/"):
            raise ToolError("ссылка на загрузку ведёт не туда — отменено")
        return Release(str(rel.get("tag_name", "")).lstrip("v"), url, name, int(a.get("size") or 0),
                       sha256=sha, page=rel.get("html_url", self.page), abi=abi)


class Mirrorbits(Source):
    """An official download host that redirects files to mirrors (mirrorbits): VideoLAN,
    Kodi. dirs: {abi: official folder URL}; rx: file name with (?P<v>version)."""

    def __init__(self, name: str, page: str, dirs: dict[str, str], rx: str):
        self.name, self.page, self.dirs, self.rx = name, page, dirs, rx

    def latest(self, abis, j=None):
        abi = _pick_abi(abis, self.dirs)
        if abi is None:
            raise ToolError("у издателя нет сборки для процессора этого ТВ")
        folder = self.dirs[abi]
        html = K._get_bytes(folder, 20).decode(errors="replace")
        files = []
        for m in re.finditer(r'href="([^"/?#]+)"', html):
            fm = re.fullmatch(self.rx.replace("{abi}", re.escape(abi)), m[1])
            if fm:
                files.append((parse_version(fm["v"]), fm["v"], m[1]))
        if not files:
            raise ToolError("у издателя нет сборки для процессора этого ТВ")
        _pv, ver, fname = max(files)
        url = folder + fname
        # the checksum from the official host itself, never from a mirror
        line = _get_strict(url + "?sha256", 4096).decode(errors="replace").split()
        sha = _hex64(line[0]) if line else None
        if not sha or (len(line) > 1 and line[-1].lstrip("*") != fname):
            raise ToolError("издатель не дал контрольную сумму — устанавливать не буду")
        size = 0
        try:
            info = _get_strict(url + "?mirrorlist", 512 << 10).decode(errors="replace")
            m = re.search(r"\((\d+) bytes\)", info)
            size = int(m[1]) if m else 0
        except ToolError:
            pass
        return Release(ver, url, fname, size, sha256=sha, page=self.page, abi=abi)


# --- F-Droid: the signed repository index

FDROID_REPO = "https://f-droid.org/repo"
# SHA-256 of the F-Droid repository signing certificate (f-droid.org's published fingerprint).
FDROID_REPO_CERT = "43238d512c1e5eb2d6569f4a3afbf5523418b82e0a3ed1552770abb9a9c9ccab"
FDROID_INDEX_MAX = 256 << 20

_OID_SHA256 = bytes.fromhex("608648016503040201")
_OID_MESSAGE_DIGEST = bytes.fromhex("2a864886f70d010904")
_OID_RSA = bytes.fromhex("2a864886f70d010101")
_OID_SHA256_RSA = bytes.fromhex("2a864886f70d01010b")


def _der(buf: bytes, i: int) -> tuple[int, int, int]:
    """One DER element at i → (tag, content start, content end)."""
    tag, n = buf[i], buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        if not 0 < k <= 4:
            raise ValueError("bad DER length")
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    if i + n > len(buf):
        raise ValueError("truncated DER")
    return tag, i, i + n


def _der_children(buf: bytes, start: int, end: int) -> list[tuple[int, int, int, int]]:
    """(tag, content start, content end, element start) of each child."""
    out, i = [], start
    while i < end:
        t, s, e = _der(buf, i)
        out.append((t, s, e, i))
        i = e
    return out


def _oid_in(buf: bytes, el: tuple) -> bytes:
    """The OID inside an AlgorithmIdentifier SEQUENCE."""
    kids = _der_children(buf, el[1], el[2])
    return buf[kids[0][1]:kids[0][2]] if kids and kids[0][0] == 0x06 else b""


def verify_pkcs7(sig_der: bytes, data: bytes, pinned_cert_sha256: str) -> None:
    """A detached PKCS#7 SignedData over data (a JAR's .SF), made by the pinned RSA
    certificate with SHA-256. Raises ToolError on anything else."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
    bad = ToolError("подпись каталога F-Droid не сходится — отменено")
    try:
        cert = next((c for c in pkcs7.load_der_pkcs7_certificates(sig_der)
                     if hashlib.sha256(c.public_bytes(Encoding.DER)).hexdigest() == pinned_cert_sha256), None)
        if cert is None:
            raise bad
        _t, s, e = _der(sig_der, 0)                                     # ContentInfo
        ci = _der_children(sig_der, s, e)
        _t, s, e = _der(sig_der, ci[1][1])                              # [0] → SignedData
        sd = _der_children(sig_der, s, e)
        infos = sd[-1]
        if infos[0] != 0x31:
            raise bad
        signer = _der_children(sig_der, infos[1], infos[2])
        if len(signer) != 1:
            raise bad
        si = _der_children(sig_der, signer[0][1], signer[0][2])
        if _oid_in(sig_der, si[2]) != _OID_SHA256:
            raise bad
        k, signed = 3, data
        if si[3][0] == 0xA0:                                            # signed attributes
            attrs = si[3]
            md = None
            for a in _der_children(sig_der, attrs[1], attrs[2]):
                ak = _der_children(sig_der, a[1], a[2])
                if sig_der[ak[0][1]:ak[0][2]] == _OID_MESSAGE_DIGEST:
                    v = _der_children(sig_der, ak[1][1], ak[1][2])[0]
                    md = sig_der[v[1]:v[2]]
            if md != hashlib.sha256(data).digest():
                raise bad
            signed = b"\x31" + sig_der[attrs[3] + 1:attrs[2]]
            k = 4
        if _oid_in(sig_der, si[k]) not in (_OID_RSA, _OID_SHA256_RSA) or si[k + 1][0] != 0x04:
            raise bad
        sig = sig_der[si[k + 1][1]:si[k + 1][2]]
        key = cert.public_key()
        if not isinstance(key, rsa.RSAPublicKey):
            raise bad
        key.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, IndexError, StopIteration):
        raise bad from None


def _manifest_sections(text: str) -> list[dict]:
    """A JAR manifest / .SF: continuation lines joined, one dict per section."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith(" ") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    out, cur = [], {}
    for l in lines:
        if not l:
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = l.partition(": ")
        cur[k] = v
    if cur:
        out.append(cur)
    return out


def verify_entry_jar(data: bytes, pinned: str = FDROID_REPO_CERT) -> dict:
    """F-Droid's entry.jar → entry.json, only if the JAR is signed by the pinned key."""
    bad = ToolError("подпись каталога F-Droid не сходится — отменено")
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        sfs = [n for n in names if re.fullmatch(r"META-INF/[^/]+\.SF", n)]
        if len(sfs) != 1 or set(names) - {"entry.json", "META-INF/MANIFEST.MF", sfs[0],
                                          sfs[0][:-3] + ".RSA"}:
            raise bad
        entry, mf, sf = z.read("entry.json"), z.read("META-INF/MANIFEST.MF"), z.read(sfs[0])
        verify_pkcs7(z.read(sfs[0][:-3] + ".RSA"), sf, pinned)
    b64 = lambda b: base64.b64encode(hashlib.sha256(b).digest()).decode()
    if (_manifest_sections(sf.decode("utf-8", "replace"))[0].get("SHA-256-Digest-Manifest") != b64(mf)):
        raise bad
    sec = next((s for s in _manifest_sections(mf.decode("utf-8", "replace")) if s.get("Name") == "entry.json"), {})
    if sec.get("SHA-256-Digest") != b64(entry):
        raise bad
    return json.loads(entry)


def _fdroid_index(j: K.Job | None) -> str:
    """The verified index-v2.json on disk (downloaded again only when F-Droid publishes a
    new one). Older entries than one already seen are refused (no rollback)."""
    entry = verify_entry_jar(K._get_bytes(FDROID_REPO + "/entry.jar", 20, 1 << 20))
    ts = int(entry.get("timestamp") or 0)
    seen = int(K._load().get("fdroid_timestamp") or 0)
    if ts < seen:
        raise ToolError("F-Droid прислал устаревший каталог — отменено")
    idx = entry.get("index") or {}
    want, size = _hex64(idx.get("sha256")), int(idx.get("size") or 0)
    if not want or idx.get("name") != "/index-v2.json" or not 0 < size <= FDROID_INDEX_MAX:
        raise ToolError("подпись каталога F-Droid не сходится — отменено")
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "fdroid-index-v2.json")
    try:
        with open(path + ".sha256", encoding="ascii") as f:
            if f.read().strip() == want and os.path.getsize(path) == size:
                K._update(lambda d: d.__setitem__("fdroid_timestamp", max(ts, seen)))
                return path
    except OSError:
        pass
    part = path + ".part"
    h, done = hashlib.sha256(), 0
    r = K._open(FDROID_REPO + "/index-v2.json", 60, {"Accept-Encoding": "gzip"})
    with r, open(part, "wb") as out:
        gz = zlib.decompressobj(16 + zlib.MAX_WBITS) if \
            (r.headers.get("Content-Encoding") or "").lower() == "gzip" else None
        if j:
            K.step(j, "index", 0, size)
        while True:
            if j and j.cancel.is_set():
                raise K.Cancelled()
            chunk = r.read(1 << 16)
            if not chunk:
                break
            if gz:
                chunk = gz.decompress(chunk)
            h.update(chunk)
            out.write(chunk)
            done += len(chunk)
            if done > size:
                break
            if j:
                j.done, j.total = done, size
                j.tick += 1
        if gz and done <= size:
            tail = gz.flush()
            h.update(tail); out.write(tail); done += len(tail)
    if done != size or h.hexdigest() != want:
        os.remove(part)
        raise ToolError("каталог F-Droid повреждён: контрольная сумма не сошлась")
    os.replace(part, path)
    with open(path + ".sha256", "w", encoding="ascii") as f:
        f.write(want)
    K._update(lambda d: d.__setitem__("fdroid_timestamp", max(ts, seen)))
    return path


def fdroid_package(index_path: str, pkg: str) -> dict:
    """One package of the (verified) index, without loading 75 MB of JSON into memory."""
    with open(index_path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        p0 = mm.find(b'"packages":')
        if p0 < 0:
            raise ToolError("в каталоге F-Droid нет этого приложения")
        for key in (json.dumps(pkg).encode() + b": {", json.dumps(pkg).encode() + b":{"):
            i = mm.find(key, p0)
            if i >= 0:
                break
        else:
            raise ToolError("в каталоге F-Droid нет этого приложения")
        start = i + len(key) - 1
        for win in (1 << 20, 8 << 20, 64 << 20):
            text = mm[start:start + win].decode("utf-8", "ignore")
            try:
                obj, _ = json.JSONDecoder().raw_decode(text)
                break
            except ValueError:
                continue
        else:
            raise ToolError("в каталоге F-Droid нет этого приложения")
    if not isinstance(obj, dict) or not isinstance(obj.get("versions"), dict):
        raise ToolError("в каталоге F-Droid нет этого приложения")
    return obj


def fdroid_pick(pkg_entry: dict, abis: list[str]) -> tuple[dict, str]:
    """The newest stable version that runs on one of the TV's processors."""
    best = None
    for v in pkg_entry["versions"].values():
        man = v.get("manifest") or {}
        if v.get("releaseChannels"):                       # beta channel
            continue
        native = man.get("nativecode") or []
        abi = "" if not native else next((a for a in abis if a in native), None)
        if abi is None:
            continue
        code = int(man.get("versionCode") or 0)
        if best is None or code > best[0]:
            best = (code, v, abi)
    if not best:
        raise ToolError("у издателя нет сборки для процессора этого ТВ")
    return best[1], best[2]


class FDroid(Source):
    """An app from f-droid.org's main repository, checked through the signed index."""
    name = "F-Droid"

    def __init__(self, pkg: str):
        self.pkg = pkg
        self.page = f"https://f-droid.org/packages/{pkg}/"

    def latest(self, abis, j=None):
        v, abi = fdroid_pick(fdroid_package(_fdroid_index(j), self.pkg), abis)
        man, file = v.get("manifest") or {}, v.get("file") or {}
        name = str(file.get("name") or "")
        sha = _hex64(file.get("sha256"))
        if not re.fullmatch(r"/[A-Za-z0-9._-]+\.apk", name) or not sha:
            raise ToolError("издатель не дал контрольную сумму — устанавливать не буду")
        signers = [s for s in ((man.get("signer") or {}).get("sha256") or []) if _hex64(s)]
        return Release(str(man.get("versionName") or ""), FDROID_REPO + name, name.lstrip("/"),
                       int(file.get("size") or 0), sha256=sha, page=self.page, abi=abi, signers=signers)

    def peek(self, abis):
        """For display: F-Droid's public API names the suggested version; the size comes
        from the file itself. Nothing is installed from this — install uses latest()."""
        d = json.loads(K._get_bytes(f"https://f-droid.org/api/v1/packages/{self.pkg}", 20))
        code = d.get("suggestedVersionCode")
        v = next((p for p in d.get("packages", []) if p.get("versionCode") == code), None) or \
            (d.get("packages") or [{}])[0]
        name = f"{self.pkg}_{v.get('versionCode')}.apk"
        size = 0
        try:
            req = urllib.request.Request(f"{FDROID_REPO}/{name}", method="HEAD", headers={"User-Agent": K.UA})
            with _STRICT.open(req, timeout=15) as r:
                size = int(r.headers.get("Content-Length") or 0)
        except Exception:
            pass
        return Release(str(v.get("versionName") or ""), f"{FDROID_REPO}/{name}", name, size, page=self.page)


class Tailscale(Source):
    """pkgs.tailscale.com, Tailscale's own package server: `?mode=json` names the current
    universal APK, `<file>.sha256` next to it on the same host gives its SHA-256 (no
    redirects allowed for either). The APK is signed with Tailscale's own key — the same
    one as in Google Play, so it also updates a copy installed from the Play Store (the
    F-Droid build of Tailscale is signed by F-Droid and could not)."""
    name = "Tailscale"
    page = "https://pkgs.tailscale.com/stable/#android"
    BASE = "https://pkgs.tailscale.com/stable/"

    def latest(self, abis, j=None):
        d = json.loads(_get_strict(self.BASE + "?mode=json", 256 << 10))
        name = str((d.get("APKs") or {}).get("universal") or "")
        ver = str(d.get("APKsVersion") or "")
        if not re.fullmatch(r"tailscale-android-universal-[\d.]+\.apk", name):
            raise ToolError("у издателя нет сборки для процессора этого ТВ")
        url = self.BASE + name
        line = _get_strict(url + ".sha256", 4096).decode(errors="replace").split()
        sha = _hex64(line[0]) if line else None
        if not sha:
            raise ToolError("издатель не дал контрольную сумму — устанавливать не буду")
        size = 0
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": K.UA})
            with _STRICT.open(req, timeout=15) as r:
                size = int(r.headers.get("Content-Length") or 0)
        except Exception:
            pass
        return Release(ver, url, name, size, sha256=sha, page=self.page, abi="")


def short_version(v: str) -> str:
    """"1.104.7-tfb3b9b296-g226f029a3" → "1.104.7" for display (build tags stay in the log)."""
    m = re.match(r"\d+(?:\.\d+)+", v or "")
    return m[0] if m else (v or "")


# ================================================================ the TV apps

@dataclass
class TvApp:
    key: str
    title: str
    pkg: str
    purpose: str                      # Russian, translated by the UI
    license: str
    license_url: str
    homepage: str
    source: Source
    signers: tuple = ()               # SHA-256 of the developer's signing certificate(s)
    tile: tuple = ("?", "#888888")

    @property
    def source_url(self) -> str:
        return self.source.page

    @property
    def job_key(self) -> str:
        return "tv:" + self.key


APPS: dict[str, TvApp] = {}


def add(app: TvApp) -> TvApp:
    APPS[app.key] = app
    return app


# The signer pins were read from each project's current official APK (and, for F-Droid,
# from its signed index). A developer who changes keys makes the install stop with
# "signed by someone else" until the pin here is updated — the safe failure.

add(TvApp(
    key="vlc", title="VLC", pkg="org.videolan.vlc",
    purpose="Плеер, который откроет почти любой файл и поток — когда Cast не справляется с форматом.",
    license="GPL-2.0", license_url="https://code.videolan.org/videolan/vlc-android/-/blob/master/COPYING",
    homepage="https://www.videolan.org/vlc/download-android.html",
    source=Mirrorbits("VideoLAN", "https://get.videolan.org/vlc-android/last/", {
        a: "https://get.videolan.org/vlc-android/last/" for a in ABIS},
        r"VLC-Android-(?P<v>\d+(?:\.\d+)+)-{abi}\.apk"),
    signers=("c8768d2cea0c4b622e419b4b4715981946821e4ebc035fb41776cad395a7f68e",), tile=("VLC", "#f07c00"),
))

add(TvApp(
    key="kodi", title="Kodi", pkg="org.xbmc.kodi",
    purpose="Медиацентр: фильмы, сериалы и музыка из домашней сети с обложками и описаниями.",
    license="GPL-2.0", license_url="https://github.com/xbmc/xbmc/blob/master/LICENSE.md",
    homepage="https://kodi.tv/download/android/",
    source=Mirrorbits("Kodi", "https://mirrors.kodi.tv/releases/android/", {
        "arm64-v8a": "https://mirrors.kodi.tv/releases/android/arm64-v8a/",
        "armeabi-v7a": "https://mirrors.kodi.tv/releases/android/arm/"},
        r"kodi-(?P<v>\d+(?:\.\d+)+)-[A-Za-z]+-{abi}\.apk"),
    signers=("f517b44b5db5e62a6c1ec55ba47526db7de0d61f6ba26a7987520e293499b8d5",), tile=("K", "#17a2d8"),
))

add(TvApp(
    key="jellyfin", title="Jellyfin", pkg="org.jellyfin.androidtv",
    purpose="Клиент для своего медиасервера Jellyfin: библиотека фильмов и сериалов на ТВ.",
    license="GPL-2.0", license_url="https://github.com/jellyfin/jellyfin-androidtv/blob/master/LICENSE",
    homepage="https://jellyfin.org/downloads/",
    source=GitHub("jellyfin/jellyfin-androidtv", {"": r"jellyfin-androidtv-v[\d.]+-release\.apk"}),
    signers=("d881796ed2a67ff6ef9f676828723c6b1fa18e09388962cba4abc4a594a69131",), tile=("J", "#8e4fc4"),
))

add(TvApp(
    key="moonlight", title="Moonlight", pkg="com.limelight",
    purpose="Игры и рабочий стол компьютера на ТВ через Sunshine или NVIDIA GameStream.",
    license="GPL-3.0", license_url="https://github.com/moonlight-stream/moonlight-android/blob/master/LICENSE.txt",
    homepage="https://moonlight-stream.org/",
    source=GitHub("moonlight-stream/moonlight-android", {"": r"app-nonRoot-release\.apk"}),
    signers=("d6ce3a4df15060fe488fe52441a549dee4ba199b36e2cb1ed9085cca8bfa1aca",), tile=("M", "#3f51b5"),
))

add(TvApp(
    key="localsend", title="LocalSend", pkg="org.localsend.localsend_app",
    purpose="Передача файлов между ТВ, телефоном и компьютером по Wi-Fi, без интернета.",
    license="Apache-2.0", license_url="https://github.com/localsend/localsend/blob/main/LICENSE",
    homepage="https://localsend.org/",
    source=GitHub("localsend/localsend", {
        "arm64-v8a": r"LocalSend-[\d.]+-android-arm64v8\.apk",
        "armeabi-v7a": r"LocalSend-[\d.]+-android-arm32v7\.apk",
        "x86_64": r"LocalSend-[\d.]+-android-x64\.apk"}),
    signers=("3220c353a73cfbd0c2f3052471c445324cf452bcba26de1c473a52fe5c44e1d6",), tile=("LS", "#0f9d8a"),
))

add(TvApp(
    key="tailscale", title="Tailscale", pkg="com.tailscale.ipn",
    purpose="Своя защищённая сеть: ТВ виден с телефона и компьютера даже вне дома. "
            "После установки откройте его на ТВ и войдите в аккаунт.",
    license="BSD-3-Clause", license_url="https://github.com/tailscale/tailscale-android/blob/main/LICENSE",
    homepage="https://tailscale.com/download/android",
    source=Tailscale(),
    signers=("5cdb295551bfe1a087fed6acda07141c6c929fa7c29bd273a7092813acc434bf",), tile=("TS", "#242424"),
))

add(TvApp(
    key="files", title="Material Files", pkg="me.zhanghai.android.files",
    purpose="Файловый менеджер: открыть то, что Fling скопировал на ТВ в папку Download.",
    license="GPL-3.0", license_url="https://github.com/zhanghai/MaterialFiles/blob/master/LICENSE",
    homepage="https://github.com/zhanghai/MaterialFiles",
    source=GitHub("zhanghai/MaterialFiles", {"": r"app-release-universal\.apk"}),
    signers=("873b9b60c77cf7f3cd5fae66d0fe112c4a86973e118ee8a29c346c4c673c97f0",), tile=("MF", "#3c8d40"),
))

add(TvApp(
    key="fdroid", title="F-Droid", pkg="org.fdroid.fdroid",
    purpose="Магазин свободных приложений: остальные программы с открытым кодом и их обновления.",
    license="GPL-3.0", license_url="https://gitlab.com/fdroid/fdroidclient/-/blob/master/LICENSE",
    homepage="https://f-droid.org/",
    source=FDroid("org.fdroid.fdroid"),
    signers=(FDROID_REPO_CERT,), tile=("F", "#1976d2"),
))


# ================================================================ PC programs (toolkit tools)

def _ffmpeg_latest() -> Release:
    """FFmpeg-Builds (BtbN), the Windows/Linux builds linked from ffmpeg.org: the newest
    stable branch, "gpl-shared" flavour; checksums.sha256 and GitHub's digest must agree."""
    import platform
    m = platform.machine().lower()
    arm = m in ("arm64", "aarch64")
    host = ("winarm64" if arm else "win64") if P.IS_WIN else None if P.IS_MAC else ("linuxarm64" if arm else "linux64")
    if not host:
        raise ToolError("для этого компьютера нет готовой сборки — установите программу из пакетов")
    ext = r"\.zip" if P.IS_WIN else r"\.tar\.xz"
    src = GitHub("BtbN/FFmpeg-Builds", {}, sums="checksums.sha256")
    rel = json.loads(K._get_bytes("https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest", 20))
    rx = rf"ffmpeg-n(\d+(?:\.\d+)*)-latest-{host}-gpl-shared-[\d.]+{ext}"
    names = [(parse_version(m[1]), a["name"]) for a in rel.get("assets", [])
             if (m := re.fullmatch(rx, a.get("name", "")))]
    if not names:
        raise ToolError("в релизе нет сборки для этого компьютера")
    _v, name = max(names)
    src.assets = {"": re.escape(name)}
    src.repo = str(rel.get("url", "")).split("/repos/", 1)[-1].split("/releases", 1)[0] or src.repo
    out = src.latest([""])
    out.version = re.fullmatch(rx, name)[1]
    return out


K.register(K.Tool(
    key="ffmpeg", title="FFmpeg",
    purpose="Перекодирует видео и звук, которые ТВ не воспроизводит. Работает из командной строки.",
    license="GPL-3.0", license_url="https://ffmpeg.org/legal.html",
    homepage="https://ffmpeg.org/download.html",
    exe="ffmpeg", dir="ffmpeg", bin="bin", version_args=("-version",),
    version_re=r"ffmpeg version n?(\d+(?:\.\d+)+)",
    places=lambda: [(os.path.join(K.ROOT, "ffmpeg", "bin", K._exe("ffmpeg")), "fling")]
    + K._search("ffmpeg", sub=("bin", "")),
    latest=_ffmpeg_latest,
    catalog=True, source="GitHub", source_url="https://github.com/BtbN/FFmpeg-Builds/releases",
    tile=("ff", "#2e7d32"),
))


# ================================================================ the TV (ADB)

_STATE: list = []                     # fling_app's State, bound at start


def bind_state(state) -> None:
    _STATE[:] = [state]


def state():
    return _STATE[0] if _STATE else None


def _adb(ip: str, args: list[str], timeout: int = 20):
    return K._F.adb_cmd(ip, args, timeout)


def _adb_ready(ip: str) -> str | None:
    return K._F._adb_ready_error(ip, "Установка на ТВ")


# Tests replace these with fakes (no real TV is touched).
ADB: Callable = _adb
ADB_READY: Callable = _adb_ready


@dataclass
class TvInfo:
    ip: str = ""
    name: str = ""
    adb: bool = False                 # an authorised ADB link right now
    abis: list = field(default_factory=list)
    android: str = ""
    installed: dict = field(default_factory=dict)      # pkg → versionName ("" if unknown)
    error: str = ""


def tv_basic() -> TvInfo:
    """What Fling already knows, without touching the network (UI thread)."""
    s = state()
    if not s or not s.ip:
        return TvInfo()
    d = s.device or {}
    return TvInfo(s.ip, str(d.get("name") or s.ip), bool(s.adb_ok()))


def tv_probe(info: TvInfo) -> TvInfo:
    """Processor, Android version and installed catalog apps — read-only commands over an
    ADB link that is already authorised (background thread)."""
    if not info.ip or not info.adb:
        return info
    r = ADB(info.ip, ["shell", "getprop ro.product.cpu.abilist; echo @; getprop ro.product.cpu.abi; echo @; "
                      "getprop ro.build.version.release; echo @; pm list packages"], 20)
    if r.returncode != 0:
        info.error = "ТВ не ответил по ADB"
        return info
    parts = (r.stdout or "").replace("\r", "").split("@\n")
    while len(parts) < 4:
        parts.append("")
    abis = [a.strip() for a in parts[0].strip().split(",") if a.strip()] or \
        ([parts[1].strip()] if parts[1].strip() else [])
    info.abis = [a for a in abis if a in ABIS]
    info.android = parts[2].strip()
    pkgs = {l.strip()[8:] for l in parts[3].splitlines() if l.strip().startswith("package:")}
    mine = [a.pkg for a in APPS.values() if a.pkg in pkgs and PKG_RE.fullmatch(a.pkg)]
    info.installed = {p: "" for p in mine}
    if mine:
        script = "; ".join(f"echo @{p}; dumpsys package {p} | grep -m1 versionName=" for p in mine)
        r = ADB(info.ip, ["shell", script], 20)
        cur = None
        for l in (r.stdout or "").splitlines():
            l = l.strip()
            if l.startswith("@"):
                cur = l[1:]
            elif cur and "versionName=" in l:
                info.installed[cur] = l.split("versionName=", 1)[1].split()[0]
    return info


# ================================================================ latest versions (display)

def peek(app: TvApp, abis: list[str], fresh: bool = False) -> Release:
    """The latest version for display, cached for PEEK_TTL (10 minutes after "Refresh").
    If the publisher can't be asked right now (GitHub limits anonymous requests), the
    last answer is shown instead of an error."""
    key = f"{app.key}|{','.join(abis)}"
    cached = K._load().get("catalog", {}).get(key)
    old = None
    if isinstance(cached, dict):
        try:
            old = Release(**cached["rel"])
        except Exception:
            old = None
    if old and time.time() - cached.get("t", 0) < (600 if fresh else PEEK_TTL):
        return old
    try:
        rel = app.source.peek(abis)
    except Exception:
        if old:
            return old
        raise
    K._update(lambda d: d.setdefault("catalog", {}).__setitem__(key, {"t": time.time(), "rel": rel.__dict__}))
    return rel


# ================================================================ install / remove on the TV

_APK_ERRORS = {
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "на ТВ уже стоит это приложение с другой подписью (например, из "
                                          "Google Play) — удалите его на ТВ и повторите.",
    "INSTALL_FAILED_VERSION_DOWNGRADE": "на ТВ уже стоит более новая версия.",
    "INSTALL_FAILED_NO_MATCHING_ABIS": "эта сборка не подходит к процессору ТВ.",
    "INSTALL_FAILED_OLDER_SDK": "приложению нужен более новый Android, чем на ТВ.",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "на ТВ не хватает места.",
    "INSTALL_FAILED_USER_RESTRICTED": "ТВ запретил установку — подтвердите её на экране ТВ.",
    "INSTALL_FAILED_VERIFICATION_FAILURE": "ТВ не пропустил приложение (проверка Google Play Protect).",
}


def _pins(app: TvApp, rel: Release) -> set[str]:
    pins = {p.lower() for p in app.signers if _hex64(p)}
    if isinstance(app.source, FDroid):
        pins |= {p.lower() for p in rel.signers}              # the signed index names the signer
    return pins


def check_apk(app: TvApp, path: str, rel: Release) -> ApkInfo:
    """The downloaded APK is the app we mean, signed by the developer's key."""
    info = apk_info(path)
    if info.pkg != app.pkg:
        raise ToolError("в файле другое приложение — установка отменена")
    pins = _pins(app, rel)
    if not pins:
        raise ToolError("неизвестен ключ разработчика — устанавливать не буду")
    if not set(info.signers) & pins:
        raise ToolError("приложение подписано не ключом разработчика — установка отменена")
    return info


def _install_worker(app: TvApp) -> Callable[[K.Job], str]:
    def work(j: K.Job) -> str:
        ip = (state().ip if state() else "") or ""
        if not ip:
            raise ToolError("телевизор не выбран")
        K.step(j, "meta")
        err = ADB_READY(ip)
        if err:
            raise ToolError(err)
        info = tv_probe(TvInfo(ip, ip, True))
        rel = app.source.latest(info.abis or DEFAULT_ABIS, j)
        rel.name = f"{app.key}-{os.path.basename(rel.name)}"
        part, _sha = K._download(rel, j)                     # checksum verified here
        K.step(j, "sign")
        work_dir = os.path.join(K.ROOT, f".work-tv-{app.key}-{os.getpid()}-{int(time.time() * 1000)}")
        os.makedirs(work_dir)
        try:
            try:
                apk = check_apk(app, part, rel)
            except ToolError:
                for p in (part, part + ".json"):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                raise
            path = os.path.join(work_dir, app.pkg + ".apk")  # adb wants the .apk ending
            os.replace(part, path)
            try:
                os.remove(part + ".json")
            except OSError:
                pass
            K.step(j, "tv")
            r = ADB(ip, ["install", "-r", path], 900)
            out = f"{r.stdout}\n{r.stderr}"
            if r.returncode != 0 or "Success" not in out:
                m = re.search(r"(INSTALL_[A-Z_]+)", out)
                last = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "нет ответа")
                raise ToolError("ТВ не установил приложение: " + (_APK_ERRORS.get(m[1], m[1]) if m else last))
            K.step(j, "tv-check")
            r = ADB(ip, ["shell", f"pm list packages {app.pkg}"], 20)
            if f"package:{app.pkg}" not in (r.stdout or "").split():
                raise ToolError("ТВ сообщил об успехе, но приложения на нём нет")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        print(f"catalog: {app.pkg} {apk.version_name} signer {apk.signers[0]} ({apk.scheme}) "
              f"sha256 {rel.sha256} from {rel.url}", flush=True)
        j.message = "Установлено на ТВ: {name} {version}. Подпись разработчика проверена."
        return short_version(apk.version_name) or rel.version
    return work


def _remove_worker(app: TvApp) -> Callable[[K.Job], str]:
    def work(j: K.Job) -> str:
        ip = (state().ip if state() else "") or ""
        if not ip or not PKG_RE.fullmatch(app.pkg):
            raise ToolError("телевизор не выбран")
        K.step(j, "tv-remove")
        err = ADB_READY(ip)
        if err:
            raise ToolError(err)
        r = ADB(ip, ["uninstall", app.pkg], 120)
        if r.returncode != 0 or "Success" not in f"{r.stdout}\n{r.stderr}":
            raise ToolError("ТВ не удалил приложение")
        j.message = "Удалено с ТВ: {name}."
        return ""
    return work


def install(app: TvApp) -> K.Job:
    return K.start(app.job_key, "install", worker=_install_worker(app))


def remove(app: TvApp) -> K.Job:
    return K.start(app.job_key, "remove", worker=_remove_worker(app))


# ---------------------------------------------------------------- API for other modules
# (fling_tailscale and friends): the same verified install as the Catalog's button, with
# progress on the shared job board, so the Tools window shows it too.

def install_on_tv(key: str, state=None, on_done: Callable[[bool, str], None] | None = None) -> K.Job:
    """Download, verify and install catalog app `key` ("tailscale", "vlc"…) on the chosen
    TV over ADB. state: fling_app's State (bound once at start, so usually None).
    on_done(ok, message) runs on a BACKGROUND thread when the job ends — hop to the UI
    thread yourself (e.g. state.bridge.call.emit). The message is in the UI language.
    Joining a job already running for this app is fine. Raises KeyError for an unknown key."""
    app = APPS[key]
    if state is not None:
        bind_state(state)
    j = install(app)
    if on_done:
        def wait():
            j.finished.wait()
            try:
                on_done(j.state == "ok", K.job_result(j, app))
            except Exception as e:
                print("catalog on_done:", e, flush=True)
        threading.Thread(target=wait, name=f"catalog-wait-{key}", daemon=True).start()
    return j


def is_on_tv(key: str) -> str | None:
    """Version of catalog app `key` on the chosen TV ("" if unknown), None if it is not
    installed or ADB is not connected. Read-only ADB; call off the UI thread."""
    info = tv_probe(tv_basic())
    if not info.adb or info.error:
        return None
    return info.installed.get(APPS[key].pkg)


def open_on_tv(key: str, state=None) -> None:
    """Open catalog app `key` on the TV the way the panel opens apps (remote, else ADB).
    UI thread."""
    s = state if state is not None else (_STATE[0] if _STATE else None)
    if s is not None:
        import fling_remote as R
        R.controller(s).launch({"pkg": APPS[key].pkg, "name": APPS[key].title})


# ================================================================ texts

EN_PHRASES = [
    ("Дополнительные модули — только из официальных источников. Перед установкой Fling сверяет "
     "каждый файл с контрольной суммой издателя или его подписанным каталогом, а у приложений для ТВ "
     "проверяет ещё и ключ разработчика.",
     "Extra modules, from official sources only. Before installing, Fling checks every file against "
     "the publisher's checksum or signed catalog, and for TV apps also the developer's signing key."),
    ("Приложения для ТВ", "TV apps"),
    ("Программы для компьютера", "Programs for this computer"),
    ("Обновить", "Refresh"),
    ("Установить на ТВ", "Install on TV"),
    ("Удалить с ТВ", "Remove from TV"),
    ("Открываю на ТВ…", "Opening on the TV…"),
    ("Установлено на ТВ", "On the TV"),
    ("На ТВ версия {v}", "On the TV: version {v}"),
    ("На ТВ: версия неизвестна", "On the TV: version unknown"),
    ("Последняя версия {v}", "Latest version {v}"),
    (" для {abi}", " for {abi}"),
    ("Не удалось узнать версию: ", "Couldn't check the version: "),
    ("Телевизор не выбран — приложения для ТВ можно будет установить, когда Fling найдёт телевизор.",
     "No TV chosen — TV apps can be installed once Fling finds a TV."),
    ("{name}: ADB не подключён. Приложения на ТВ устанавливаются через ADB.",
     "{name}: ADB isn't connected. TV apps are installed over ADB."),
    ("{name}: проверяю ADB…", "{name}: checking ADB…"),
    ("{name} · ADB подключён", "{name} · ADB connected"),
    ("Android {v}", "Android {v}"),
    ("процессор {abi}", "processor {abi}"),
    ("Подключить ADB", "Connect ADB"),
    ("Подключаю…", "Connecting…"),
    ("Сначала подключите ADB: кнопка «Подключить ADB» вверху.",
     "Connect ADB first: the “Connect ADB” button at the top."),
    ("Телевизор не выбран.", "No TV is chosen."),
    ("Удалить {name} с телевизора?", "Remove {name} from the TV?"),
    ("Настройки и данные приложения на ТВ тоже удалятся.", "The app's settings and data on the TV are removed too."),
    ("Fling — каталог", "Fling — catalog"),
    ("Установлено на ТВ: {name} {version}. Подпись разработчика проверена.",
     "Installed on the TV: {name} {version}. The developer's signature checked out."),
    ("Удалено с ТВ: {name}.", "Removed from the TV: {name}."),
    # purposes
    ("Плеер, который откроет почти любой файл и поток — когда Cast не справляется с форматом.",
     "A player that opens almost any file or stream — for when Cast can't handle the format."),
    ("Медиацентр: фильмы, сериалы и музыка из домашней сети с обложками и описаниями.",
     "A media center: films, series and music from your home network, with artwork and descriptions."),
    ("Клиент для своего медиасервера Jellyfin: библиотека фильмов и сериалов на ТВ.",
     "The client for your own Jellyfin media server: your film and series library on the TV."),
    ("Игры и рабочий стол компьютера на ТВ через Sunshine или NVIDIA GameStream.",
     "Your computer's games and desktop on the TV, via Sunshine or NVIDIA GameStream."),
    ("Передача файлов между ТВ, телефоном и компьютером по Wi-Fi, без интернета.",
     "Sends files between the TV, phones and computers over Wi-Fi, no internet needed."),
    ("Своя защищённая сеть: ТВ виден с телефона и компьютера даже вне дома. "
     "После установки откройте его на ТВ и войдите в аккаунт.",
     "Your own private network: reach the TV from your phone and computer even away from home. "
     "After installing, open it on the TV and sign in."),
    ("Файловый менеджер: открыть то, что Fling скопировал на ТВ в папку Download.",
     "A file manager: open what Fling copied to the TV's Download folder."),
    ("Магазин свободных приложений: остальные программы с открытым кодом и их обновления.",
     "A store of free apps: other open-source programs and their updates."),
    ("Перекодирует видео и звук, которые ТВ не воспроизводит. Работает из командной строки.",
     "Converts video and audio the TV can't play. Runs from the command line."),
    # errors
    ("файл не похож на приложение Android (APK)", "the file doesn't look like an Android app (APK)"),
    ("APK не подписан — устанавливать не буду", "the APK isn't signed — not installing"),
    ("в APK нет описания приложения (AndroidManifest)", "the APK has no app description (AndroidManifest)"),
    ("официальный сервер перенаправил запрос контрольной суммы — отменено",
     "the official server redirected the checksum request — cancelled"),
    ("у издателя нет сборки для процессора этого ТВ", "the publisher has no build for this TV's processor"),
    ("контрольные суммы издателя не совпадают — установка отменена",
     "the publisher's checksums don't match — install cancelled"),
    ("ссылка на загрузку ведёт не туда — отменено", "the download link points somewhere else — cancelled"),
    ("подпись каталога F-Droid не сходится — отменено", "the F-Droid catalog signature doesn't match — cancelled"),
    ("F-Droid прислал устаревший каталог — отменено", "F-Droid sent an outdated catalog — cancelled"),
    ("каталог F-Droid повреждён: контрольная сумма не сошлась",
     "the F-Droid catalog is damaged: the checksum didn't match"),
    ("в каталоге F-Droid нет этого приложения", "this app isn't in the F-Droid catalog"),
    ("для этого компьютера нет готовой сборки — установите программу из пакетов",
     "there's no ready-made build for this computer — install the program from your packages"),
    ("в релизе нет сборки для этого компьютера", "the release has no build for this computer"),
    ("ТВ не ответил по ADB", "the TV didn't answer over ADB"),
    ("на ТВ уже стоит это приложение с другой подписью (например, из Google Play) — удалите его на ТВ и повторите.",
     "the TV already has this app with a different signature (from Google Play, for example) — remove it on the TV and try again."),
    ("на ТВ уже стоит более новая версия.", "the TV already has a newer version."),
    ("эта сборка не подходит к процессору ТВ.", "this build doesn't fit the TV's processor."),
    ("приложению нужен более новый Android, чем на ТВ.", "the app needs a newer Android than the TV has."),
    ("на ТВ не хватает места.", "the TV is out of space."),
    ("ТВ запретил установку — подтвердите её на экране ТВ.", "the TV blocked the install — allow it on the TV screen."),
    ("ТВ не пропустил приложение (проверка Google Play Protect).",
     "the TV rejected the app (Google Play Protect check)."),
    ("неизвестен ключ разработчика — устанавливать не буду", "the developer's key is unknown — not installing"),
    ("в файле другое приложение — установка отменена", "the file holds a different app — install cancelled"),
    ("приложение подписано не ключом разработчика — установка отменена",
     "the app isn't signed with the developer's key — install cancelled"),
    ("ТВ не установил приложение: ", "the TV didn't install the app: "),
    ("ТВ сообщил об успехе, но приложения на нём нет", "the TV reported success, but the app isn't there"),
    ("ТВ не удалил приложение", "the TV didn't remove the app"),
    ("телевизор не выбран", "no TV is chosen"),
    ("нет ответа", "no answer"),
    ("Установка на ТВ", "Installing on the TV"),
]


# ================================================================ UI (Qt)

from PyQt6.QtCore import QSize, Qt, QTimer                                # noqa: E402
from PyQt6.QtGui import QFont                                             # noqa: E402
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox,    # noqa: E402
                             QVBoxLayout, QWidget)


def _tr(text: str) -> str:
    return K._tr(text)


class AppCard(QFrame):
    """One TV app: is it on the TV (and which version), the latest version, install /
    update, open, remove."""
    def __init__(self, page: "CatalogPage", app: TvApp):
        super().__init__()
        F, T = K._F, K._F.T
        self.page, self.app = page, app
        self.setObjectName("toolcard")
        self.setStyleSheet(f"QFrame#toolcard{{background:{K._css(T.fg_a(0.045))};border-radius:10px}}")
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        top = QHBoxLayout(); top.setSpacing(10)
        self.icon = QLabel(); self.icon.setFixedSize(K.TILE, K.TILE)
        self.icon.setPixmap(K.tile_pixmap(*app.tile))
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(F.label(app.title, 13.5, weight=QFont.Weight.DemiBold), 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        self.pill_box = QHBoxLayout(); self.pill_box.setContentsMargins(0, 0, 0, 0)
        top.addLayout(self.pill_box)
        v.addLayout(top)
        v.addWidget(F.label(app.purpose, 11.5, T.fg_a(0.6), wrap=True))
        self.info = F.label("", 11.5, T.fg_a(0.85), wrap=True)
        v.addWidget(self.info)
        self.prog_box = QWidget()
        pl = QVBoxLayout(self.prog_box); pl.setContentsMargins(0, 4, 0, 0); pl.setSpacing(6)
        self.bar = F.SlimProgress()
        pl.addWidget(self.bar)
        pr = QHBoxLayout(); pr.setSpacing(8)
        self.prog = F.label("", 11, T.fg_a(0.65))
        pr.addWidget(self.prog, 1)
        self.cancel_btn = F.small_button("Отмена", self._cancel)
        pr.addWidget(self.cancel_btn)
        pl.addLayout(pr)
        self.prog_box.hide()
        v.addWidget(self.prog_box)
        self.result = F.label("", 11, T.fg_a(0.65), wrap=True)
        self.result.hide()
        v.addWidget(self.result)
        self.actions = QHBoxLayout(); self.actions.setSpacing(6); self.actions.setContentsMargins(0, 6, 0, 0)
        v.addLayout(self.actions)
        self.lic = F.label("", 10.5, T.fg_a(0.45), wrap=True)
        self.lic.setOpenExternalLinks(True)
        self.lic.setContentsMargins(0, 2, 0, 0)
        v.addWidget(self.lic)
        self._job_tick = -1

    def render(self, tv: TvInfo, rel: Release | None, err: str, probing: bool = False):
        F, T, app = K._F, K._F.T, self.app
        j = K.job(app.job_key)
        running = bool(j and j.state == "run")
        known = tv.adb and not probing and not tv.error
        have = tv.installed.get(app.pkg) if known and app.pkg in tv.installed else None
        K._clear(self.pill_box)
        if running:
            self.pill_box.addWidget(K._pill("Устанавливаю…" if j.action == "install" else "Удаляю…", T.accent))
        elif have is not None:
            self.pill_box.addWidget(K._pill("Установлено на ТВ", T.green))
        # the version line
        newer = False
        if have is not None:
            line = _tr("На ТВ версия {v}").format(v=short_version(have)) if have else _tr("На ТВ: версия неизвестна")
            if rel and rel.version:
                newer = bool(have) and parse_version(rel.version) > parse_version(short_version(have))
                line += " · " + (_tr("доступна {v}").format(v=rel.version) if newer else _tr("это последняя версия"))
        elif rel and rel.version:
            line = _tr("Последняя версия {v}").format(v=rel.version)
            if rel.abi and tv.abis:
                line += _tr(" для {abi}").format(abi=rel.abi)
        elif err:
            line = _tr("Не удалось узнать версию: ") + _tr(err)
        else:
            line = _tr("Узнаю последнюю версию…")
        self.info.setText(F.typo_ru(line))
        self.lic.setText(K.credits_html(app, rel.size if rel else 0))
        # actions
        K._clear(self.actions)
        can = known and not running
        if not running:
            if have is None or newer:
                b = K._primary(_tr("Обновить до {v}").format(v=rel.version) if newer else _tr("Установить на ТВ"),
                               self._install)
                b.setEnabled(can)
                if not can:                        # the strip above says what to do
                    b.setToolTip(_tr("Сначала подключите ADB: кнопка «Подключить ADB» вверху.")
                                 if tv.ip else _tr("Телевизор не выбран."))
                self.actions.addWidget(b)
            if have is not None:
                self.actions.addWidget(F.small_button("Открыть на ТВ", self._open))
        self.actions.addStretch(1)
        if have is not None and not running:
            self.actions.addWidget(F.small_button("Удалить с ТВ", self._remove))
        self.update_job()

    def update_job(self):
        j = K.job(self.app.job_key)
        if not j:
            self.prog_box.hide()
            if not getattr(self, "_said", False):
                self.result.hide()
            return
        if j.tick == self._job_tick and j.state != "run":
            return
        self._job_tick = j.tick
        self._said = False
        if j.state == "run":
            self.result.hide()
            self.prog_box.show()
            self.prog.setText(K.job_text(j))
            if j.phase in ("download", "index") and j.total:
                self.bar.value = min(1.0, j.done / j.total)
            elif j.phase in ("verify", "sign", "tv", "tv-check", "tv-remove"):
                self.bar.value = 1.0
            else:
                self.bar.value = 0.0
            self.bar.update()
            self.cancel_btn.setEnabled(j.phase in ("start", "meta", "index", "download"))
        else:
            self.prog_box.hide()
            T = K._F.T
            col = T.red if j.state == "error" else T.fg_a(0.65) if j.state == "cancelled" else T.green
            self.result.setStyleSheet(f"color:{K._css(col)};background:transparent")
            self.result.setText(K._F.typo_ru(K.job_result(j, self.app)))
            self.result.show()

    def _say(self, text: str):
        T = K._F.T
        self._said = True
        self.result.setStyleSheet(f"color:{K._css(T.fg_a(0.65))};background:transparent")
        self.result.setText(_tr(text))
        self.result.show()

    def _install(self):
        install(self.app)
        self.page.render_all()

    def _cancel(self):
        j = K.job(self.app.job_key)
        if j:
            j.cancel.set()

    def _open(self):
        s = state()
        if not s:
            return
        import fling_remote as R
        R.controller(s).launch({"pkg": self.app.pkg, "name": self.app.title})
        self._say("Открываю на ТВ…")

    def _remove(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(_tr("Fling — каталог"))
        box.setText(_tr("Удалить {name} с телевизора?").format(name=self.app.title))
        box.setInformativeText(_tr("Настройки и данные приложения на ТВ тоже удалятся."))
        yes = box.addButton(_tr("Удалить с ТВ"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(_tr("Отмена"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            remove(self.app)
            self.page.render_all()


class CatalogPage(QWidget):
    """The Catalog tab: the TV's status, TV apps, then PC programs (toolkit cards)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        F, T = K._F, K._F.T
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.tv = TvInfo()
        self.rel: dict[str, Release] = {}
        self.err: dict[str, str] = {}
        self._results: list = []
        self._lock = threading.Lock()
        self._rev = -1
        self._seen: set = set()
        self._probing = False
        self._connecting = False
        self._probed_ip = None
        v = QVBoxLayout(self); v.setContentsMargins(18, 6, 18, 16); v.setSpacing(10)
        self.again = F.small_button("Обновить", lambda: self.refresh(fresh=True))
        v.addWidget(F.label("Дополнительные модули — только из официальных источников. Перед установкой Fling сверяет "
                            "каждый файл с контрольной суммой издателя или его подписанным каталогом, а у приложений для ТВ "
                            "проверяет ещё и ключ разработчика.", 11.5, T.fg_a(0.6), wrap=True))
        # the TV strip
        self.strip = QFrame(); self.strip.setObjectName("tvstrip")
        self.strip.setStyleSheet(f"QFrame#tvstrip{{background:{K._css(T.acc_a(0.08))};border-radius:10px}}")
        sh = QHBoxLayout(self.strip); sh.setContentsMargins(12, 8, 10, 8); sh.setSpacing(10)
        self.tv_icon = QLabel(); self.tv_icon.setFixedSize(18, 18)
        self.tv_icon.setPixmap(F.glyph("tv", 18, T.fg_a(0.8)).pixmap(QSize(18, 18)))
        sh.addWidget(self.tv_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tv_text = F.label("", 11.5, T.fg_a(0.85), wrap=True)
        sh.addWidget(self.tv_text, 1, Qt.AlignmentFlag.AlignVCenter)
        self.connect_btn = F.small_button("Подключить ADB", self._connect)
        sh.addWidget(self.connect_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(self.strip)
        v.addSpacing(4)
        v.addWidget(self._section("Приложения для ТВ"))
        self.cards = {k: AppCard(self, a) for k, a in APPS.items()}
        for c in self.cards.values():
            v.addWidget(c)
        v.addSpacing(4)
        v.addWidget(self._section("Программы для компьютера"))
        self.pc = K.ToolsPage(tools=[k for k, t in K.TOOLS.items() if t.catalog], header=False, embedded=True)
        v.addWidget(self.pc)
        v.addStretch(1)
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._tick)

    def _section(self, text: str) -> QLabel:
        l = K._F.label(text, 12, K._F.T.fg_a(0.55), QFont.Weight.DemiBold)
        l.setContentsMargins(2, 4, 0, 0)
        return l

    # ---- threads → UI
    def background(self, fn, done):
        def go():
            try:
                r = fn()
            except Exception as e:
                print("catalog:", e, flush=True)
                r = e
            with self._lock:
                self._results.append((done, r))
        threading.Thread(target=go, daemon=True).start()

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start()
        if not self.rel and not self.err or self._probed_ip != tv_basic().ip:
            self.refresh()

    def hideEvent(self, e):
        super().hideEvent(e)
        if not K.busy():
            self.timer.stop()

    def refresh(self, fresh: bool = False):
        """The TV's state (read-only ADB), then each app's latest version."""
        self.tv = tv_basic()
        self._probed_ip = self.tv.ip
        self._probing = self.tv.adb
        if fresh:
            self.err.clear()
        self.render_all()
        tv = self.tv

        def probed(info):
            self._probing = False
            if isinstance(info, TvInfo):
                self.tv = info
            self._peek_all(fresh)
            self.render_all()
        self.background(lambda: tv_probe(TvInfo(tv.ip, tv.name, tv.adb)), probed)

    def _peek_all(self, fresh: bool):
        abis = self.tv.abis or DEFAULT_ABIS
        for key, app in APPS.items():
            def got(r, key=key):
                if isinstance(r, Release):
                    self.rel[key] = r
                    self.err.pop(key, None)
                else:
                    self.err[key] = str(r) if isinstance(r, ToolError) else "нет связи с сервером загрузки"
                self.cards[key].render(self.tv, self.rel.get(key), self.err.get(key, ""), self._probing)
            self.background(lambda app=app: peek(app, abis, fresh), got)

    def _connect(self):
        s = state()
        if not s or not s.ip:
            return
        self._connecting = True
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText(_tr("Подключаю…"))
        ip = s.ip

        def done(_r):
            self._connecting = False
            self.refresh()
        self.background(lambda: K._F.adb_state(ip, force=True), done)

    def render_all(self):
        F, T, tv = K._F, K._F.T, self.tv
        if not tv.ip:
            text = _tr("Телевизор не выбран — приложения для ТВ можно будет установить, "
                       "когда Fling найдёт телевизор.")
        elif not tv.adb:
            text = _tr("{name}: ADB не подключён. Приложения на ТВ устанавливаются через ADB.").format(name=tv.name)
        elif self._probing:
            text = _tr("{name}: проверяю ADB…").format(name=tv.name)
        else:
            bits = [_tr("{name} · ADB подключён").format(name=tv.name)]
            if tv.android:
                bits.append(_tr("Android {v}").format(v=tv.android))
            if tv.abis:
                bits.append(_tr("процессор {abi}").format(abi=tv.abis[0]))
            text = " · ".join(bits)
            if tv.error:
                text += " · " + _tr(tv.error)
        self.tv_text.setText(F.typo_ru(text))
        self.connect_btn.setVisible(bool(tv.ip) and not tv.adb)
        if not self._connecting:
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText(_tr("Подключить ADB"))
        for k, c in self.cards.items():
            c.render(tv, self.rel.get(k), self.err.get(k, ""), self._probing)
        self._rev = K.REVISION[0]

    def _tick(self):
        with self._lock:
            res, self._results = self._results, []
        for fn, r in res:
            fn(r)
        if K.REVISION[0] != self._rev:
            self._rev = K.REVISION[0]
            fresh = [j for a in APPS.values() if (j := K.job(a.job_key)) and j.finished.is_set()
                     and j.serial not in self._seen]
            for j in fresh:
                self._seen.add(j.serial)
            if any(j.state == "ok" for j in fresh):
                self.refresh()                     # what is on the TV changed
            else:
                self.render_all()
        for c in self.cards.values():
            c.update_job()
        if not self.isVisible() and not K.busy():
            self.timer.stop()
