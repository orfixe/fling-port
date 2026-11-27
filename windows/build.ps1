<#
.SYNOPSIS
  Builds the standalone Fling for Windows: Fling.exe (Python, Qt, catt, yt-dlp and the TV
  libraries bundled, nothing else to install) packed as dist\Fling-Setup-<ver>.exe with
  Inno Setup (windows\fling.iss). Without Inno Setup: dist\Fling-Windows-<ver>.zip instead.
  Plus the browser extension zips.
  Also builds fling-guard.exe (guard\, Rust) and writes fling.manifest, the list of every
  file with its SHA-256. -Sign signs that list with the release key (asks for its
  password); only a signed list turns the guard's protection on.

.PARAMETER Sign
  Sign fling.manifest with fling-sign (the key stays on the maintainer's machine).
.PARAMETER Official
  The release build: implies -Sign and builds the guard with FLING_OFFICIAL=1, so a
  missing or broken signature counts as tampering.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\build.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Official
#>
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
param([switch]$Sign, [switch]$Official)
$ErrorActionPreference = 'Stop'
$Repo  = Split-Path -Parent $PSScriptRoot
$Work  = Join-Path $Repo 'build\windows'
$Dist  = Join-Path $Repo 'dist'
$Venv  = Join-Path $Work 'venv'
$Py    = Join-Path $Venv 'Scripts\python.exe'
$Ver   = (Select-String -Path (Join-Path $Repo 'desktop\fling_app.py') -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value

function Exec {
  param([Parameter(Mandatory)][string]$Exe, [Parameter(ValueFromRemainingArguments)][string[]]$Args_)
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Exe @Args_ 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
  finally { $ErrorActionPreference = $old }
  if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($Exe)) failed (exit $LASTEXITCODE)" }
}

$Uv = (Get-Command uv -ErrorAction Stop).Source
New-Item -ItemType Directory -Force -Path $Work, $Dist | Out-Null

Write-Host '==> build environment' -ForegroundColor Cyan
if (-not (Test-Path $Py)) { Exec $Uv venv --python 3.12 $Venv }
Exec $Uv pip install --python $Py -r (Join-Path $PSScriptRoot 'requirements.txt') catt pyinstaller

Write-Host '==> icon' -ForegroundColor Cyan
$Ico = Join-Path $Work 'fling.ico'
Exec $Py (Join-Path $Repo 'desktop\fling_app.py') --make-icon $Ico

Write-Host '==> version info' -ForegroundColor Cyan
$n = $Ver.Split('.') + @('0', '0', '0')
$VerInfo = Join-Path $Work 'version.txt'
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($n[0]),$($n[1]),$($n[2]),0), prodvers=($($n[0]),$($n[1]),$($n[2]),0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Fling for Windows (unofficial port by orfixe)'),
    StringStruct('FileDescription', 'Fling'),
    StringStruct('FileVersion', '$Ver'),
    StringStruct('ProductName', 'Fling'),
    StringStruct('ProductVersion', '$Ver'),
    StringStruct('OriginalFilename', 'Fling.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | ForEach-Object { [IO.File]::WriteAllText($VerInfo, $_) }

Write-Host '==> PyInstaller' -ForegroundColor Cyan
$D = Join-Path $Repo 'desktop'
# Settings → About shows which build this is
$BuildInfo = Join-Path $Work 'build-info.json'
$commit = (git -C $Repo log -1 --format=%h 2>$null)
$dirty = [bool](git -C $Repo status --porcelain --untracked-files=no 2>$null)
@{ commit = "$commit"; date = (Get-Date -Format 'yyyy-MM-dd'); dirty = $dirty; kind = 'installer' } |
  ConvertTo-Json -Compress | ForEach-Object { [IO.File]::WriteAllText($BuildInfo, $_) }
Exec $Py -m PyInstaller --noconfirm --clean --windowed --name Fling `
  --icon $Ico --version-file $VerInfo `
  --distpath (Join-Path $Work 'out') --workpath (Join-Path $Work 'tmp') --specpath $Work `
  --paths $D `
  --hidden-import fling_platform --hidden-import fling_bridge --hidden-import fling_help --hidden-import sf_icons `
  --hidden-import fling_themes --hidden-import fling_settings --hidden-import fling_remote --hidden-import fling_tools `
  --hidden-import fling_media --hidden-import fling_devices --hidden-import fling_catalog --hidden-import fling_tailscale --hidden-import fling_samsung --hidden-import fling_welcome --hidden-import fling_report --hidden-import fling_update --hidden-import PyQt6.QtWebSockets `
  --collect-submodules catt --collect-submodules pychromecast --collect-submodules yt_dlp `
  --collect-submodules androidtvremote2 --collect-data androidtvremote2 `
  --collect-submodules pyytlounge --collect-submodules zeroconf `
  --copy-metadata catt --copy-metadata yt-dlp --exclude-module tkinter `
  --add-data "$(Join-Path $Repo 'security\fling-release.pub');security" `
  --add-data "$BuildInfo;." `
  (Join-Path $D 'fling_app.py')

Write-Host '==> extensions' -ForegroundColor Cyan
Exec $Py (Join-Path $Repo 'extension\build.py')

Write-Host '==> adb' -ForegroundColor Cyan
$Out = Join-Path $Work 'out\Fling'
$Adb = Join-Path $Out 'platform-tools\adb.exe'
if (-not (Test-Path $Adb)) {
  # kept between builds (refreshed weekly) and retried: a moment without network must not stop a release
  $zipAdb = Join-Path $Work 'platform-tools.zip'
  if (-not (Test-Path $zipAdb) -or (Get-Item $zipAdb).LastWriteTime -lt (Get-Date).AddDays(-7)) {
    for ($try = 1; ; $try++) {
      try {
        Invoke-WebRequest 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile "$zipAdb.part"
        Move-Item "$zipAdb.part" $zipAdb -Force
        break
      } catch {
        Remove-Item "$zipAdb.part" -Force -ErrorAction SilentlyContinue
        if (Test-Path $zipAdb) { Write-Host "    offline: using the platform-tools from the last build" -ForegroundColor Yellow; break }
        if ($try -ge 4) { throw }
        Write-Host "    download failed ($($_.Exception.Message)), retry $try of 3..." -ForegroundColor Yellow
        Start-Sleep -Seconds (5 * $try)
      }
    }
  }
  Expand-Archive $zipAdb -DestinationPath $Out -Force
}

# scrcpy (TV screen + mouse) ships inside, so the first "TV screen" needs no download.
# Its own adb.exe is left out: Fling points scrcpy at the bundled platform-tools adb.
Write-Host '==> scrcpy' -ForegroundColor Cyan
$ScrDir = Join-Path $Out 'scrcpy'
if (-not (Test-Path (Join-Path $ScrDir 'scrcpy.exe'))) {
  # Anonymous GitHub API calls are limited to 60/hour per IP, and a few builds in a row hit it.
  # Use a token when there is one (GITHUB_TOKEN / GH_TOKEN, or the logged-in gh CLI: 5000/hour);
  # if the API still refuses, read the latest tag from the releases page, which has no API limit.
  $ghHeaders = @{ 'User-Agent' = 'Fling-build' }
  $token = if ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } elseif ($env:GH_TOKEN) { $env:GH_TOKEN }
           elseif (Get-Command gh -ErrorAction SilentlyContinue) { (& gh auth token 2>$null | Out-String).Trim() }
  if ($token) { $ghHeaders['Authorization'] = "Bearer $token" }
  $asset = $null; $sums = $null
  try {
    $rel = Invoke-RestMethod 'https://api.github.com/repos/Genymobile/scrcpy/releases/latest' -Headers $ghHeaders
    $asset = $rel.assets | Where-Object { $_.name -like 'scrcpy-win64-*.zip' } | Select-Object -First 1
    $sums = $rel.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1
  } catch {
    Write-Host "    GitHub API unavailable ($($_.Exception.Message.Split([char]10)[0])); using the releases page" -ForegroundColor DarkGray
    # github.com/<repo>/releases/latest redirects to .../releases/tag/<tag>
    $req = [Net.HttpWebRequest]::Create('https://github.com/Genymobile/scrcpy/releases/latest')
    $req.AllowAutoRedirect = $false; $req.UserAgent = 'Fling-build'
    $resp = $req.GetResponse(); $loc = $resp.Headers['Location']; $resp.Close()
    $tag = ($loc -split '/tag/')[-1]
    if (-not $tag -or $tag -eq $loc) { throw "could not find the latest scrcpy release (redirect: $loc)" }
    $base = "https://github.com/Genymobile/scrcpy/releases/download/$tag"
    $asset = [pscustomobject]@{ name = "scrcpy-win64-$tag.zip"; browser_download_url = "$base/scrcpy-win64-$tag.zip" }
    $sums = [pscustomobject]@{ name = 'SHA256SUMS.txt'; browser_download_url = "$base/SHA256SUMS.txt" }
  }
  if (-not $asset -or -not $sums) { throw 'scrcpy release has no win64 zip / SHA256SUMS.txt' }
  $zipScr = Join-Path $Work $asset.name
  Invoke-WebRequest $asset.browser_download_url -OutFile $zipScr
  $sumsFile = Join-Path $Work 'scrcpy-SHA256SUMS.txt'
  Invoke-WebRequest $sums.browser_download_url -OutFile $sumsFile      # served as binary: read it from disk
  $want = ((Get-Content $sumsFile) | Where-Object { $_ -match [regex]::Escape($asset.name) } |
           Select-Object -First 1).Split(' ')[0]
  Remove-Item $sumsFile -Force
  $got = (Get-FileHash $zipScr -Algorithm SHA256).Hash
  if (-not $want -or $got -ne $want.ToUpper()) { throw "scrcpy checksum mismatch: $got vs $want" }
  $tmpScr = Join-Path $Work 'scrcpy-x'
  Remove-Item $tmpScr -Recurse -Force -ErrorAction SilentlyContinue
  Expand-Archive $zipScr -DestinationPath $tmpScr -Force
  $inner = Get-ChildItem $tmpScr -Directory | Select-Object -First 1
  New-Item -ItemType Directory -Force -Path $ScrDir | Out-Null
  Get-ChildItem $inner.FullName | Where-Object { $_.Name -notmatch '^(adb\.exe|AdbWinApi\.dll|AdbWinUsbApi\.dll|open_a_terminal_here\.bat|scrcpy-noconsole\.vbs)$' } |
    Copy-Item -Destination $ScrDir -Recurse -Force
  Remove-Item $zipScr, $tmpScr -Recurse -Force
}

# the Chrome / Edge extension ships inside, for "Load unpacked"
Copy-Item (Join-Path $Repo 'extension\dist\chrome') (Join-Path $Out 'extension\chrome') -Recurse -Force
Copy-Item (Join-Path $Repo 'extension\dist\fling-firefox-*.zip') (Join-Path $Out 'extension') -Force
# "Install the extension..." in the app drives the browser with this script
Copy-Item (Join-Path $PSScriptRoot 'install-extension.ps1') $Out -Force
# authorship and licenses travel with the program
foreach ($n in 'LICENSE', 'NOTICE', 'CREDITS.md', 'THIRD-PARTY-NOTICES.md') {
  Copy-Item (Join-Path $Repo $n) (Join-Path $Out $n) -Force
}
Copy-Item (Join-Path $Repo 'extension\dist\*.zip') $Dist -Force

# `fling` command (on PATH when the setup task is ticked) is part of the signed list
Copy-Item (Join-Path $PSScriptRoot 'fling.cmd') $Out -Force

Write-Host '==> guard (Rust)' -ForegroundColor Cyan
if ($Official) { $Sign = $true }
$Pub = Get-Content (Join-Path $Repo 'security\fling-release.pub') -Raw
if ($Sign -and $Pub -match 'placeholder') { throw 'security\fling-release.pub is a placeholder: run fling-sign keygen first (see SECURITY.md)' }
$Cargo = (Get-Command cargo -ErrorAction SilentlyContinue).Source
if (-not $Cargo) { $Cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe' }
if (-not (Test-Path $Cargo)) { throw 'Rust is not installed (winget install Rustlang.Rustup)' }
# the GNU toolchain needs mingw binutils (WinLibs) on PATH; MSVC does not
$WinLibs = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\BrechtSanders.WinLibs*\mingw64\bin" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if ($WinLibs -and -not (Get-Command dlltool -ErrorAction SilentlyContinue)) { $env:PATH = "$($WinLibs.FullName);$env:PATH" }
$GuardDir = Join-Path $Repo 'guard'
if ($Official) { $env:FLING_OFFICIAL = '1' } else { Remove-Item Env:FLING_OFFICIAL -ErrorAction SilentlyContinue }
Push-Location $GuardDir
try { Exec $Cargo build --release --locked } finally { Pop-Location; Remove-Item Env:FLING_OFFICIAL -ErrorAction SilentlyContinue }
$GuardBin = Join-Path $GuardDir 'target\release'
Copy-Item (Join-Path $GuardBin 'fling-guard.exe') $Out -Force

Write-Host '==> signed file list' -ForegroundColor Cyan
Remove-Item (Join-Path $Out 'fling.manifest'), (Join-Path $Out 'fling.manifest.minisig') -ErrorAction SilentlyContinue
Exec (Join-Path $GuardBin 'fling-guard.exe') manifest $Out $Ver 'windows-x64'
if ($Sign) {
  Write-Host '    release key password:' -ForegroundColor Yellow
  & (Join-Path $GuardBin 'fling-sign.exe') sign (Join-Path $Out 'fling.manifest') --comment "fling-manifest version=$Ver platform=windows-x64"
  if ($LASTEXITCODE -ne 0) { throw 'signing failed' }
  Exec (Join-Path $GuardBin 'fling-guard.exe') check $Out
} else {
  Write-Host '    not signed (-Sign / -Official): the guard only reports on this build' -ForegroundColor Yellow
}

$Iscc = @((Get-Command iscc -ErrorAction SilentlyContinue).Source,
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($Iscc) {
  Write-Host '==> setup (Inno Setup)' -ForegroundColor Cyan
  Exec $Iscc /Qp "/DAppVersion=$Ver" "/DSourceDir=$Out" "/DOutDir=$Dist" (Join-Path $PSScriptRoot 'fling.iss')
  Write-Host "==> $(Join-Path $Dist "Fling-Setup-$Ver.exe")" -ForegroundColor Green
} else {
  Write-Host '==> zip (Inno Setup not found, so no setup.exe)' -ForegroundColor Yellow
  $Zip = Join-Path $Dist "Fling-Windows-$Ver.zip"
  Remove-Item $Zip -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $PSScriptRoot 'setup-shortcuts.ps1') $Out -Force
  Copy-Item (Join-Path $PSScriptRoot 'Install shortcuts.cmd') $Out -Force
  Compress-Archive -Path $Out -DestinationPath $Zip
  Write-Host "==> $Zip" -ForegroundColor Green
}
