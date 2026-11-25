# Security

Fling is open source: anyone can read it, build it and change it. That is a feature —
but it also means anyone can build a copy with something bad inside and call it "Fling".
This page explains how to tell the real releases apart, what Fling does to protect
itself on your computer, and how to report a problem.

## Report a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting
(*Security → Report a vulnerability* on the repository page). You will get an answer
within a few days. Please include the version, the OS and the steps to reproduce.

## Is my copy the real Fling?

Only files from the **Releases page of `orfixe/fling-port`** are official. Every release
carries `SHA256SUMS.txt` and its signature `SHA256SUMS.txt.minisig`, made with the
Fling release key. The secret half of that key exists only on the maintainer's machine —
not in this repository, not in CI, not in any cloud.

Release public key (minisign / Ed25519):

```
see security/fling-release.pub
```

Check a download on **any OS** with [minisign](https://jedisct1.github.io/minisign/)
(`winget install jedisct1.minisign`, `brew install minisign`, `apt install minisign`):

```sh
minisign -Vm SHA256SUMS.txt -p fling-release.pub   # the list is genuine
sha256sum -c SHA256SUMS.txt --ignore-missing       # your file is on the list
```

or with Fling's own tool: `fling-guard verify SHA256SUMS.txt`.

A copy that fails this check — or comes from anywhere else — is not Fling, whatever it
calls itself. Forks are welcome, but they are signed with *their* key (or none), and
they must not use the Fling release key or claim to be the official build.

## What protects you after the install (Windows)

`fling-guard.exe` (source in [`guard/`](guard/), Rust, ~0.5 MB on disk, ~2 MB of RAM)
starts together with Fling:

1. **Signed file list.** The setup contains `fling.manifest` — the SHA-256 of *every*
   file of the install — signed with the release key. On every start the guard checks the
   signature, then compares all files in the background at low priority. A changed file,
   an added DLL or script, or a junction → Fling is closed and you are told what changed.
   Fling checks the guard itself against the same list before starting it.
2. **Every program Fling starts is checked.** Fling runs inside a Windows Job Object;
   Windows reports each new process to the guard at once. It may run only if it is
   - a file of the signed list with the same hash (adb, scrcpy…), or
   - in a folder only an administrator can write (System32, Program Files), or
   - signed by its publisher (Authenticode).

   Anything else — a miner dropped into `%TEMP%`, an unsigned helper — is stopped
   immediately and you get a warning. Processes cannot leave the job.
3. **CPU watchdog.** A process started by Fling that keeps more than 30% of the whole
   machine for 90 s is stopped (Fling itself is only reported, never killed).
4. Log: `%LOCALAPPDATA%\Fling\logs\guard.log`.

Builds from source (no signed list) run the same checks in **report-only** mode.

### Honest limits

- Software running as *your* Windows user can, in principle, replace all of Fling's
  files at once, the guard included. The guard detects partial tampering, bad updates and
  programs dropped next to Fling — it is not an antivirus. Keep Windows Defender on.
- If the release key ever leaked, signatures would no longer prove anything: the key
  would be replaced and announced on the Releases page.
- Code signing with an Authenticode certificate (SmartScreen reputation) is planned.

## Other safeguards in the code

- The browser bridge listens on `127.0.0.1` only, accepts extension origins only
  (optionally pinned IDs), rejects DNS rebinding, and needs a custom header + JSON body.
- Only `http(s)` links are cast from outside; UNC paths are refused (no NTLM leaks).
- Downloads (scrcpy, platform-tools) use HTTPS and are checked against the publisher's
  checksums where they exist.
- Android updates go through Android's own installer, which refuses an APK signed with a
  different key.
- GitHub Actions are pinned by commit SHA; CI never holds the release key.

## Future modules

Third-party modules will have to be signed by their author, shown with the author's key
fingerprint before install, and run in a separate restricted process (Windows
AppContainer: no access to your files, LAN only unless declared) under the same guard.
Unsigned modules will not load.

## For the maintainer: releasing

```powershell
# once: create the key (asks for a password), then commit ONLY the .pub
guard\target\release\fling-sign.exe keygen
copy $env:USERPROFILE\.fling-signing\fling-release.pub security\fling-release.pub

# every release
powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Official   # signs fling.manifest
guard\target\release\fling-sign.exe sign dist\SHA256SUMS.txt           # signs the release list
```

Back up `%USERPROFILE%\.fling-signing\` offline (a USB stick in a drawer). Never put the
secret key or its password into the repository, CI secrets or chat.
