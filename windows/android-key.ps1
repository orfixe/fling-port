<#
.SYNOPSIS
  The Android release key of Fling: create it once, keep it safe, build with it.

  The key lives outside the repository, in %LOCALAPPDATA%\Fling-release\android:
    fling-release.jks   PKCS12 keystore, alias "fling", RSA 4096, valid ~30 years
    password.xml        its random password, encrypted with Windows DPAPI (this user, this PC)
    lineage.bin         key rotation proof: the old debug key -> the release key, so phones
                        that have Fling installed accept updates signed with the new key
    debug.keystore      a copy of the old key: Android 8 phones keep checking v1/v2 with it

  Losing the keystore or its password means Fling can never be updated on phones again.
  Run -Backup to a USB stick / safe place and -CopyPassword into a password manager.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\android-key.ps1 -Create
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\android-key.ps1 -Build
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\android-key.ps1 -Backup E:\fling-keys
#>
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
param([switch]$Create, [switch]$Build, [switch]$CopyPassword, [switch]$Fingerprint, [string]$Backup = '')
$ErrorActionPreference = 'Stop'
$Repo  = Split-Path -Parent $PSScriptRoot
$Dir   = Join-Path $env:LOCALAPPDATA 'Fling-release\android'
$Ks    = Join-Path $Dir 'fling-release.jks'
$Pw    = Join-Path $Dir 'password.xml'
$Lin   = Join-Path $Dir 'lineage.bin'
$Old   = Join-Path $Dir 'debug.keystore'
$Jdk   = Join-Path $env:LOCALAPPDATA 'Android\jdk-17.0.21.1+2'
$Sdk   = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
$Keytool = Join-Path $Jdk 'bin\keytool.exe'
$Tools = Get-ChildItem (Join-Path $Sdk 'build-tools') -Directory | Sort-Object { [version]($_.Name -replace '[^\d.].*$', '') } | Select-Object -Last 1
$Apksigner = Join-Path $Tools.FullName 'apksigner.bat'
$env:JAVA_HOME = $Jdk
$env:PATH = (Join-Path $Jdk 'bin') + ';' + $env:PATH

function Get-Password {
  if (-not (Test-Path $Pw)) { throw "no release key yet: run with -Create" }
  $s = Import-Clixml $Pw
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

if ($Create) {
  if (Test-Path $Ks) { throw "the release key already exists ($Ks) - it is never replaced" }
  $debug = Join-Path $env:USERPROFILE '.android\debug.keystore'
  if (-not (Test-Path $debug)) { throw "the old signing key is missing ($debug): published APKs were signed with it" }
  New-Item -ItemType Directory -Force -Path $Dir | Out-Null
  Copy-Item $debug $Old
  # 32 random bytes -> a 43-character password; never printed
  $bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $env:FLING_ANDROID_KS_PASS = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  try {
    & $Keytool -genkeypair -storetype PKCS12 -keystore $Ks -alias fling -keyalg RSA -keysize 4096 `
      -validity 11000 -dname 'CN=Fling port, O=orfixe' `
      -storepass:env FLING_ANDROID_KS_PASS -keypass:env FLING_ANDROID_KS_PASS
    if ($LASTEXITCODE -ne 0) { throw 'keytool failed' }
    ConvertTo-SecureString $env:FLING_ANDROID_KS_PASS -AsPlainText -Force | Export-Clixml $Pw
    # Rotation proof: phones with the debug-signed Fling (Android 9+) trust the new key
    & $Apksigner rotate --out $Lin `
      --old-signer --ks $Old --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android `
      --new-signer --ks $Ks --ks-key-alias fling --ks-pass env:FLING_ANDROID_KS_PASS
    if ($LASTEXITCODE -ne 0) { throw 'apksigner rotate failed' }
  } catch {
    Remove-Item $Ks, $Pw, $Lin -ErrorAction SilentlyContinue
    throw
  } finally {
    Remove-Item Env:FLING_ANDROID_KS_PASS -ErrorAction SilentlyContinue
  }
  Write-Host "==> release key created in $Dir" -ForegroundColor Green
  Write-Host '    Now back it up: -Backup <folder on a USB stick>, and -CopyPassword into a password manager.' -ForegroundColor Yellow
}

if ($Fingerprint) {
  $env:FLING_ANDROID_KS_PASS = Get-Password
  try { & $Keytool -list -v -keystore $Ks -alias fling -storepass:env FLING_ANDROID_KS_PASS | Select-String 'SHA256:|Owner:|Valid' }
  finally { Remove-Item Env:FLING_ANDROID_KS_PASS -ErrorAction SilentlyContinue }
}

if ($CopyPassword) {
  Set-Clipboard -Value (Get-Password)
  Write-Host 'The release key password is on the clipboard: paste it into your password manager, then copy something else.' -ForegroundColor Yellow
}

if ($Backup) {
  if (-not (Test-Path $Ks)) { throw "no release key yet: run with -Create" }
  New-Item -ItemType Directory -Force -Path $Backup | Out-Null
  Copy-Item $Ks, $Lin, $Old -Destination $Backup -Force
  @"
Fling Android signing keys - keep this folder and the password safe.
fling-release.jks  release key (alias fling); password: in your password manager (android-key.ps1 -CopyPassword)
lineage.bin        rotation proof debug key -> release key
debug.keystore     the old key (alias androiddebugkey, password android); Android 8 phones still check it
Restore: copy these files to %LOCALAPPDATA%\Fling-release\android and recreate password.xml with
  ConvertTo-SecureString '<password>' -AsPlainText -Force | Export-Clixml password.xml
"@ | Set-Content -Encoding ASCII (Join-Path $Backup 'README.txt')
  Write-Host "==> backed up to $Backup (the password is NOT in it)" -ForegroundColor Green
}

if ($Build) {
  # Builds the APK (and the Play bundle) with the release key; the password stays in this process
  if (-not (Test-Path $Ks)) { throw "no release key yet: run with -Create" }
  $env:FLING_ANDROID_KS = $Ks
  $env:FLING_ANDROID_LINEAGE = $Lin
  $env:FLING_ANDROID_OLD_KS = $Old
  $env:FLING_ANDROID_KS_PASS = Get-Password
  try {
    $bash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
    & $bash (Join-Path $Repo 'android\build.sh')
    if ($LASTEXITCODE -ne 0) { throw "android/build.sh failed ($LASTEXITCODE)" }
  } finally {
    Remove-Item Env:FLING_ANDROID_KS_PASS -ErrorAction SilentlyContinue
  }
}
