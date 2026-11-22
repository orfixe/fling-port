# Standalone Fling (Fling.exe from the release zip): Start menu shortcut + `fling` command.
# Run from the unpacked Fling folder; `-Remove` takes them away again.
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$Exe = Join-Path $Here 'Fling.exe'
$Lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'Fling.lnk'
$Bin = Join-Path $env:LOCALAPPDATA 'Fling\bin'

if ($Remove) {
  Remove-Item $Lnk -Force -ErrorAction SilentlyContinue
  Remove-Item $Bin -Recurse -Force -ErrorAction SilentlyContinue
  $p = [Environment]::GetEnvironmentVariable('Path', 'User')
  [Environment]::SetEnvironmentVariable('Path', (($p -split ';') | Where-Object { $_ -and $_ -ne $Bin }) -join ';', 'User')
  Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name Fling -ErrorAction SilentlyContinue
  Write-Host 'Shortcuts removed. Delete this folder to remove Fling completely.'
  return
}
if (-not (Test-Path $Exe)) { throw "Fling.exe not found next to this script ($Here)" }

$s = (New-Object -ComObject WScript.Shell).CreateShortcut($Lnk)
$s.TargetPath = $Exe; $s.Arguments = 'show'; $s.WorkingDirectory = $Here
$s.IconLocation = "$Exe,0"; $s.Description = 'Fling - cast to Chromecast / Google TV and control the TV'
$s.Save()

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
# The .cmd stays ASCII. The real path lives in a UTF-8 script, so a Cyrillic profile name still works.
$run = Join-Path $Bin 'fling-run.ps1'
$exeLit = $Exe.Replace("'", "''")
[IO.File]::WriteAllText($run, "Start-Process -FilePath '$exeLit' -ArgumentList `$args`r`n", [Text.UTF8Encoding]::new($true))
"@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0fling-run.ps1`" %*`r`n" | Set-Content -Encoding ascii (Join-Path $Bin 'fling.cmd')
$p = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($p -split ';') -contains $Bin)) {
  [Environment]::SetEnvironmentVariable('Path', (($p, $Bin) -join ';').TrimStart(';'), 'User')
}
Write-Host "Start menu: Fling  ·  command: fling (new terminals)"
Start-Process $Exe -ArgumentList 'show'
