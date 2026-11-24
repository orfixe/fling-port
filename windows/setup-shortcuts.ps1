# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
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
  Remove-Item 'HKCU:\Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk' -Recurse -Force -ErrorAction SilentlyContinue
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

# Explorer: right-click an .apk -> "Install on TV (Fling)" (fling install-apk)
$apkKey = 'HKCU:\Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk'
$apkTitle = if ((Get-Culture).TwoLetterISOLanguageName -eq 'ru') { ((-join [char[]](0x423,0x441,0x442,0x430,0x43D,0x43E,0x432,0x438,0x442,0x44C,0x20,0x43D,0x430,0x20,0x422,0x412)) + ' (Fling)') } else { 'Install on TV (Fling)' }
New-Item -Path "$apkKey\command" -Force | Out-Null
Set-ItemProperty -Path $apkKey -Name 'MUIVerb' -Value $apkTitle
Set-ItemProperty -Path $apkKey -Name 'Icon' -Value "$Exe,0"
Set-Item -Path "$apkKey\command" -Value "`"$Exe`" install-apk `"%1`""

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
# The .cmd stays ASCII. The real path lives in a UTF-8 script, so a Cyrillic profile name still works.
$run = Join-Path $Bin 'fling-run.ps1'
$exeLit = $Exe.Replace("'", "''")
# Quote each argument (Start-Process doesn't), and skip -ArgumentList when there are none:
# Windows PowerShell 5.1 rejects an empty -ArgumentList, which broke a plain `fling`.
$runBody = @'
$a = @($args | ForEach-Object { '"' + $_ + '"' })
if ($a.Count) { Start-Process -FilePath '__EXE__' -ArgumentList $a } else { Start-Process -FilePath '__EXE__' }
'@
[IO.File]::WriteAllText($run, $runBody.Replace('__EXE__', $exeLit), [Text.UTF8Encoding]::new($true))
"@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0fling-run.ps1`" %*`r`n" | Set-Content -Encoding ascii (Join-Path $Bin 'fling.cmd')
$p = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($p -split ';') -contains $Bin)) {
  [Environment]::SetEnvironmentVariable('Path', (($p, $Bin) -join ';').TrimStart(';'), 'User')
}
Write-Host "Start menu: Fling  -  command: fling (new terminals)"
Start-Process $Exe -ArgumentList 'show'
