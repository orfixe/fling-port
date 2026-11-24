<#
.SYNOPSIS
  Builds the standalone Fling for Windows: Fling.exe (Python, Qt, catt, yt-dlp and the TV
  libraries bundled, nothing else to install) packed as dist\Fling-Setup-<ver>.exe with
  Inno Setup (windows\fling.iss). Without Inno Setup: dist\Fling-Windows-<ver>.zip instead.
  Plus the browser extension zips.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\build.ps1
#>
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
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
Exec $Py -m PyInstaller --noconfirm --clean --windowed --name Fling `
  --icon $Ico --version-file $VerInfo `
  --distpath (Join-Path $Work 'out') --workpath (Join-Path $Work 'tmp') --specpath $Work `
  --paths $D `
  --hidden-import fling_platform --hidden-import fling_bridge --hidden-import fling_help --hidden-import sf_icons `
  --collect-submodules catt --collect-submodules pychromecast --collect-submodules yt_dlp `
  --collect-submodules androidtvremote2 --collect-data androidtvremote2 `
  --collect-submodules pyytlounge --collect-submodules zeroconf `
  --copy-metadata catt --exclude-module tkinter `
  (Join-Path $D 'fling_app.py')

Write-Host '==> extensions' -ForegroundColor Cyan
Exec $Py (Join-Path $Repo 'extension\build.py')

Write-Host '==> adb' -ForegroundColor Cyan
$Out = Join-Path $Work 'out\Fling'
$Adb = Join-Path $Out 'platform-tools\adb.exe'
if (-not (Test-Path $Adb)) {
  $zipAdb = Join-Path $Work 'platform-tools.zip'
  Invoke-WebRequest 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile $zipAdb
  Expand-Archive $zipAdb -DestinationPath $Out -Force
  Remove-Item $zipAdb -Force
}

# scrcpy (TV screen + mouse) ships inside, so the first "TV screen" needs no download.
# Its own adb.exe is left out: Fling points scrcpy at the bundled platform-tools adb.
Write-Host '==> scrcpy' -ForegroundColor Cyan
$ScrDir = Join-Path $Out 'scrcpy'
if (-not (Test-Path (Join-Path $ScrDir 'scrcpy.exe'))) {
  $rel = Invoke-RestMethod 'https://api.github.com/repos/Genymobile/scrcpy/releases/latest' -Headers @{ 'User-Agent' = 'Fling-build' }
  $asset = $rel.assets | Where-Object { $_.name -like 'scrcpy-win64-*.zip' } | Select-Object -First 1
  $sums = $rel.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1
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
