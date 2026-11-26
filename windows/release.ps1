<#
.SYNOPSIS
  Builds and signs a Fling release on the maintainer's machine. Asks for the release key
  password once, then:
    1. windows\build.ps1 -Official   (guard built as official, fling.manifest signed)
    2. collects the release files in dist\release: the setup, the Android APK, the
       browser extension zips and the macOS files of the previous release
    3. writes SHA256SUMS.txt for all of them and signs it (SHA256SUMS.txt.minisig)
    4. checks both signatures with fling-guard
  Nothing is uploaded: publishing stays a separate, deliberate step (gh release create).

  -SavePassword keeps the password after a release that signed fine, encrypted with Windows
  DPAPI for this user on this computer only (%LOCALAPPDATA%\Fling-release). Later releases
  then run without asking. Trade-off: any program running as this user can sign a release.
  -ForgetPassword deletes it.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\release.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\release.ps1 -SavePassword
#>
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
param([string]$MacFrom = '', [switch]$SavePassword, [switch]$ForgetPassword)
$ErrorActionPreference = 'Stop'
$Store = Join-Path $env:LOCALAPPDATA 'Fling-release\sign-password.xml'   # DPAPI: this user, this PC
if ($ForgetPassword) {
  Remove-Item $Store -Force -ErrorAction SilentlyContinue
  Write-Host 'The saved release key password is deleted.' -ForegroundColor Green
  exit 0
}
$Repo = Split-Path -Parent $PSScriptRoot
$Ver  = (Select-String -Path (Join-Path $Repo 'desktop\fling_app.py') -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
$Bin  = Join-Path $Repo 'guard\target\release'

# A release is built from a committed tree only: the tag must match the signed files.
$dirty = git -C $Repo status --porcelain --untracked-files=no
if ($dirty) { throw "uncommitted changes - commit them first:`n$($dirty -join "`n")" }
$apk = Join-Path $Repo 'android\Fling.apk'
$newest = Get-ChildItem (Join-Path $Repo 'android') -Recurse -File -Include *.java, *.xml |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not (Test-Path $apk) -or $newest.LastWriteTime -gt (Get-Item $apk).LastWriteTime) {
  throw "android\Fling.apk is older than $($newest.Name): rebuild it with android/build.sh first"
}

$saved = Test-Path $Store
if ($saved) {
  $secure = Import-Clixml $Store                  # a SecureString only this Windows user can decrypt
  Write-Host "==> release key password: saved one (-ForgetPassword deletes it)" -ForegroundColor Cyan
} else {
  $secure = Read-Host "Fling $Ver - release key password" -AsSecureString
}
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:FLING_SIGN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
try {
  Write-Host "==> build $Ver (official, signed list)" -ForegroundColor Cyan
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build.ps1') -Official
  if ($LASTEXITCODE -ne 0) { throw "build.ps1 failed ($LASTEXITCODE)" }

  Write-Host '==> release files' -ForegroundColor Cyan
  $Rel = Join-Path $Repo 'dist\release'
  Remove-Item $Rel -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $Rel | Out-Null
  Copy-Item (Join-Path $Repo "dist\Fling-Setup-$Ver.exe") $Rel
  Copy-Item (Join-Path $Repo 'android\Fling.apk') $Rel
  Get-ChildItem (Join-Path $Repo 'dist') -Filter "*-$Ver.zip" | Copy-Item -Destination $Rel   # this version only
  # macOS: built by CI; macos/install.sh downloads them from the latest release
  if (-not $MacFrom) { $MacFrom = (gh release view --repo orfixe/fling-port --json tagName -q .tagName) }
  gh release download $MacFrom --repo orfixe/fling-port --dir $Rel `
    --pattern Fling --pattern Info.plist --pattern Fling.app.zip --clobber
  if ($LASTEXITCODE -ne 0) { throw "could not fetch the macOS files of $MacFrom" }
  Copy-Item (Join-Path $Repo 'macos\install.sh') $Rel -Force   # the installer from this commit, not the last release's

  Write-Host '==> SHA256SUMS.txt' -ForegroundColor Cyan
  $Sums = Join-Path $Rel 'SHA256SUMS.txt'
  $lines = Get-ChildItem $Rel -File | Where-Object { $_.Name -notlike 'SHA256SUMS*' } | Sort-Object Name |
    ForEach-Object { '{0}  {1}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name }
  [IO.File]::WriteAllText($Sums, ($lines -join "`n") + "`n")
  & (Join-Path $Bin 'fling-sign.exe') sign $Sums --comment "fling-release version=$Ver"
  if ($LASTEXITCODE -ne 0) {
    if ($saved) { throw 'signing SHA256SUMS.txt failed - wrong saved password? run with -ForgetPassword, then again' }
    throw 'signing SHA256SUMS.txt failed'
  }
  if ($SavePassword -and -not $saved) {             # only a password that just signed is kept
    New-Item -ItemType Directory -Force -Path (Split-Path $Store) | Out-Null
    $secure | Export-Clixml $Store
    Write-Host "==> password saved for later releases ($Store)" -ForegroundColor Cyan
  }

  Write-Host '==> check' -ForegroundColor Cyan
  & (Join-Path $Bin 'fling-guard.exe') verify $Sums
  if ($LASTEXITCODE -ne 0) { throw 'SHA256SUMS.txt signature does not verify' }
  & (Join-Path $Bin 'fling-guard.exe') check (Join-Path $Repo 'build\windows\out\Fling')
  if ($LASTEXITCODE -ne 0) { throw 'the signed install list does not verify' }
  Write-Host "==> ready: $Rel" -ForegroundColor Green
  Get-ChildItem $Rel | Format-Table Name, Length
} finally {
  Remove-Item Env:FLING_SIGN_PASSWORD -ErrorAction SilentlyContinue
}
