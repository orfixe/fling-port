<#
.SYNOPSIS
  Installs Fling for Windows (tray caster + TV remote) for the current user. No admin rights needed.

.DESCRIPTION
  * uv (Python manager) if missing, a private Python 3.12 environment, PyQt6 and the TV libraries
  * catt — the Cast engine
  * Android platform-tools (adb) for "what's playing" and TV screenshots (skip with -NoAdb)
  * Start menu shortcut, the `fling` command, the browser extensions in %LOCALAPPDATA%\Fling\extension
  Everything lives in %LOCALAPPDATA%\Fling; settings in %APPDATA%\Fling.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\install.ps1
  powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Uninstall
#>
param(
  [switch]$Uninstall,
  [switch]$NoAdb,
  [switch]$NoLaunch,
  [switch]$KeepSettings
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Invoke-WebRequest is 10x faster without the progress bar

$Repo     = Split-Path -Parent $PSScriptRoot
$App      = Join-Path $env:LOCALAPPDATA 'Fling'
$Conf     = Join-Path $env:APPDATA 'Fling'
$Bin      = Join-Path $App 'bin'
$Venv     = Join-Path $App 'venv'
$Pyw      = Join-Path $Venv 'Scripts\pythonw.exe'
$Py       = Join-Path $Venv 'Scripts\python.exe'
$Main     = Join-Path $App 'app\fling_app.py'
$Programs = [Environment]::GetFolderPath('Programs')
$Lnk      = Join-Path $Programs 'Fling.lnk'

function Step($t) { Write-Host "==> $t" -ForegroundColor Cyan }

# Native tools write progress to stderr; Windows PowerShell 5.1 would turn that into errors.
function Exec {
  param([Parameter(Mandatory)][string]$Exe, [Parameter(ValueFromRemainingArguments)][string[]]$Args_, [switch]$AllowFail)
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Exe @Args_ 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
  finally { $ErrorActionPreference = $old }
  if ($LASTEXITCODE -ne 0 -and -not $AllowFail) { throw "$([IO.Path]::GetFileName($Exe)) failed (exit $LASTEXITCODE)" }
}
function Note($t) { Write-Host "    $t" -ForegroundColor DarkGray }

function Remove-FromUserPath($dir) {
  $p = [Environment]::GetEnvironmentVariable('Path', 'User')
  if ($p) {
    $new = ($p -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ne $dir.TrimEnd('\')) }) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $new, 'User')
  }
}

function Stop-Fling {
  if ((Test-Path $Py) -and (Test-Path $Main)) {
    Exec $Py $Main quit -AllowFail
    Start-Sleep -Milliseconds 800
  }
  Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe' OR Name='Fling.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*fling_app.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  # a running catt.exe (Fling polls the TV with it) is locked and breaks `uv tool install`
  Get-Process catt -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------------ uninstall
if ($Uninstall) {
  Step 'Removing Fling'
  Stop-Fling
  Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Fling' -ErrorAction SilentlyContinue
  Remove-Item $Lnk -Force -ErrorAction SilentlyContinue
  Remove-Item 'HKCU:\Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk' -Recurse -Force -ErrorAction SilentlyContinue
  Remove-FromUserPath $Bin
  $uv = Get-Command uv -ErrorAction SilentlyContinue
  if ($uv) { Exec $uv.Source tool uninstall catt -AllowFail }
  Remove-Item $App -Recurse -Force -ErrorAction SilentlyContinue
  if (-not $KeepSettings) { Remove-Item $Conf -Recurse -Force -ErrorAction SilentlyContinue }
  Write-Host 'Fling is removed. Remove the browser extension from the browser itself.' -ForegroundColor Green
  Write-Host 'The TV still lists "Fling (Windows)" among its remotes until you remove it there (optional).'
  return
}

if (-not (Test-Path (Join-Path $Repo 'desktop\fling_app.py'))) {
  throw "Run this script from the Fling folder (expected $Repo\desktop\fling_app.py)."
}

# ------------------------------------------------------------------ this PC
Step 'This PC'
$os = [Environment]::OSVersion.Version
if ($os.Major -lt 10 -or ($os.Major -eq 10 -and $os.Build -lt 17763)) {
  throw "Fling needs Windows 10 (1809) or Windows 11; this is $os. Qt 6 doesn't run on older Windows."
}
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Fling needs 64-bit Windows.' }
# Windows on ARM (Snapdragon laptops): Qt's Python wheels are x64-only; Windows 11 runs them emulated.
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$PySpec = if ($arch -eq 'ARM64') { 'cpython-3.12-windows-x86_64-none' } else { '3.12' }
Note "Windows $($os.Major).$($os.Build), $arch -> Python $PySpec"

# ------------------------------------------------------------------ uv
Step 'uv (Python manager)'
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
  Note 'not found - installing from astral.sh'
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
  $uvCmd = Get-Command uv -ErrorAction Stop
}
$Uv = $uvCmd.Source
Note $Uv

# ------------------------------------------------------------------ catt
Step 'Cast engine (catt)'
Stop-Fling                                    # before catt: its catt.exe must not be running
# --force: take over a catt.exe left behind by an earlier, interrupted install
Exec $Uv tool install --upgrade --force --python $PySpec catt
Exec $Uv tool update-shell -AllowFail       # puts %USERPROFILE%\.local\bin on PATH

# ------------------------------------------------------------------ Python environment
Step 'Python environment'
New-Item -ItemType Directory -Force -Path $App, $Bin, (Join-Path $App 'app'), (Join-Path $App 'logs') | Out-Null
if (-not (Test-Path $Py)) {
  Exec $Uv venv --python $PySpec $Venv
}
Exec $Uv pip install --python $Py -r (Join-Path $PSScriptRoot 'requirements.txt')

# ------------------------------------------------------------------ app files
Step 'Files'
Copy-Item (Join-Path $Repo 'desktop\*.py') (Join-Path $App 'app') -Force
Exec $Py $Main --make-icon (Join-Path $App 'fling.ico')

# Fling.exe: the venv's interpreter under Fling's own name and icon, so Task Manager lists
# "Fling" instead of "Python", as one process. Falls back to pythonw.exe if anything fails.
$FlingExe = Join-Path $Venv 'Scripts\Fling.exe'
try {
  $home_ = ((Get-Content (Join-Path $Venv 'pyvenv.cfg')) -match '^home\s*=' | Select-Object -First 1) -replace '^home\s*=\s*', ''
  $home_ = $home_.Trim()
  $scripts = Join-Path $Venv 'Scripts'
  Copy-Item (Join-Path $home_ 'pythonw.exe') $FlingExe -Force
  # the copy loads the Python DLLs from its own folder; pyvenv.cfg one level up gives it the venv
  Get-ChildItem $home_ -Filter '*.dll' | Where-Object { $_.Name -like 'python*.dll' -or $_.Name -like 'vcruntime*.dll' } |
    ForEach-Object { Copy-Item $_.FullName $scripts -Force }
  $ver = (Select-String -Path $Main -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
  Exec $Uv run --no-project --quiet --with pyinstaller python (Join-Path $PSScriptRoot 'brand-exe.py') $FlingExe (Join-Path $App 'fling.ico') $ver
  $Pyw = $FlingExe
  Note "launcher: $FlingExe"
} catch {
  Note "Fling.exe not made ($($_.Exception.Message)) - using pythonw.exe"
}

# browser extensions: built folders the browsers can load
Exec $Py (Join-Path $Repo 'extension\build.py')
$ExtDir = Join-Path $App 'extension'
Remove-Item $ExtDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $ExtDir | Out-Null
Copy-Item (Join-Path $Repo 'extension\dist\chrome') (Join-Path $ExtDir 'chrome') -Recurse
Copy-Item (Join-Path $Repo 'extension\dist\firefox') (Join-Path $ExtDir 'firefox') -Recurse
Copy-Item (Join-Path $Repo 'extension\dist\*.zip') $ExtDir
Copy-Item (Join-Path $PSScriptRoot 'install-extension.ps1') $App -Force
Note "extensions: $ExtDir"

# ------------------------------------------------------------------ adb (optional)
if (-not $NoAdb) {
  Step 'ADB (Android platform-tools)'
  $adb = Join-Path $App 'platform-tools\adb.exe'
  if ((Get-Command adb -ErrorAction SilentlyContinue) -or (Test-Path $adb)) {
    Note 'already present'
  } else {
    $zip = Join-Path $env:TEMP 'fling-platform-tools.zip'
    Invoke-WebRequest 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile $zip
    Expand-Archive $zip -DestinationPath $App -Force
    Remove-Item $zip -Force
    Note $adb
  }
}

# ------------------------------------------------------------------ launchers
Step 'Start menu and the `fling` command'
# fling.cmd: `start` returns at once; the running Fling picks the command up
$run = Join-Path $Bin 'fling-run.ps1'
$pywLit = $Pyw.Replace("'", "''")
$mainLit = $Main.Replace("'", "''")
# Start-Process joins -ArgumentList without quoting, so a profile path with a space
# ("John Smith") reached pythonw as two arguments. Quote every one.
$runBody = @'
$a = @('"__MAIN__"') + @($args | ForEach-Object { '"' + $_ + '"' })
Start-Process -FilePath '__PYW__' -ArgumentList $a
'@
[IO.File]::WriteAllText($run, $runBody.Replace('__MAIN__', $mainLit).Replace('__PYW__', $pywLit), [Text.UTF8Encoding]::new($true))
"@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0fling-run.ps1`" %*`r`n" | Set-Content -Encoding ascii (Join-Path $Bin 'fling.cmd')

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $Bin)) {
  [Environment]::SetEnvironmentVariable('Path', (("$userPath".TrimEnd(';')) + ";$Bin").TrimStart(';'), 'User')
  Note "added $Bin to PATH (new terminals)"
}

$sh = New-Object -ComObject WScript.Shell
$l = $sh.CreateShortcut($Lnk)
$l.TargetPath = $Pyw
$l.Arguments = "`"$Main`" show"
$l.WorkingDirectory = $App
$l.IconLocation = (Join-Path $App 'fling.ico')
$l.Description = 'Fling - cast to Chromecast / Google TV and control the TV'
$l.Save()
Note $Lnk

# Explorer: right-click an .apk -> "Install on TV (Fling)" (fling install-apk)
$apkKey = 'HKCU:\Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk'
$apkTitle = if ((Get-Culture).TwoLetterISOLanguageName -eq 'ru') { ((-join [char[]](0x423,0x441,0x442,0x430,0x43D,0x43E,0x432,0x438,0x442,0x44C,0x20,0x43D,0x430,0x20,0x422,0x412)) + ' (Fling)') } else { 'Install on TV (Fling)' }
New-Item -Path "$apkKey\command" -Force | Out-Null
Set-ItemProperty -Path $apkKey -Name 'MUIVerb' -Value $apkTitle
Set-ItemProperty -Path $apkKey -Name 'Icon' -Value (Join-Path $App 'fling.ico')
Set-Item -Path "$apkKey\command" -Value "`"$Pyw`" `"$Main`" install-apk `"%1`""

# keep an existing "Start at login" entry pointing at the current interpreter
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Get-ItemProperty -Path $run -Name 'Fling' -ErrorAction SilentlyContinue) {
  Set-ItemProperty -Path $run -Name 'Fling' -Value "`"$Pyw`" `"$Main`" tray"
}

# ------------------------------------------------------------------ done
Write-Host ''
Write-Host 'Fling is installed.' -ForegroundColor Green
Write-Host '  * Start menu -> Fling, or type  fling  in a new terminal.'
Write-Host '  * The icon sits in the notification area (bottom right; click ^ if hidden).'
Write-Host "  * Browser extension: load $ExtDir\chrome (Chrome/Edge) - see docs\install-windows.md"
if (-not $NoLaunch) {
  Start-Process $Pyw -ArgumentList "`"$Main`"", 'show' -WorkingDirectory $App
}
