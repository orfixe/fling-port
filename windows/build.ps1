<#
.SYNOPSIS
  Builds the standalone Fling for Windows: dist\Fling-Windows-<ver>.zip with Fling.exe
  (Python, Qt, catt, yt-dlp and the TV libraries bundled — nothing else to install),
  plus the browser extension zips.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\build.ps1
#>
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
    StringStruct('CompanyName', 'Fling for Windows (unofficial port)'),
    StringStruct('FileDescription', 'Fling'),
    StringStruct('FileVersion', '$Ver'),
    StringStruct('ProductName', 'Fling'),
    StringStruct('ProductVersion', '$Ver'),
    StringStruct('OriginalFilename', 'Fling.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -Encoding utf8 $VerInfo

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

Write-Host '==> zip' -ForegroundColor Cyan
$Out = Join-Path $Work 'out\Fling'
$Zip = Join-Path $Dist "Fling-Windows-$Ver.zip"
Remove-Item $Zip -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Repo 'extension\dist\chrome') (Join-Path $Out 'extension\chrome') -Recurse -Force
Copy-Item (Join-Path $Repo 'extension\dist\fling-firefox-*.zip') (Join-Path $Out 'extension') -Force
Copy-Item (Join-Path $PSScriptRoot 'setup-shortcuts.ps1') $Out -Force
Copy-Item (Join-Path $PSScriptRoot 'Install shortcuts.cmd') $Out -Force
Compress-Archive -Path $Out -DestinationPath $Zip
Copy-Item (Join-Path $Repo 'extension\dist\*.zip') $Dist -Force
Write-Host "==> $Zip" -ForegroundColor Green
