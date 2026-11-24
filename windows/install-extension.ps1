# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
# Loads Fling's unpacked extension into Chrome or Edge by driving the browser's own UI
# (UI Automation): extensions page -> Developer mode -> Load unpacked -> pick the folder.
# Browsers don't let programs add an extension that isn't from their store any other way.
# Run by Fling ("Install the extension..." button):  install-extension.ps1 -Dir <ext folder> -Exe <chrome.exe>
# Prints one line: ok | already | fail:<step>.  ASCII only (Windows PowerShell 5.1 reads it as ANSI).
param([Parameter(Mandatory)] [string]$Dir, [Parameter(Mandatory)] [string]$Exe, [string]$Page = 'chrome://extensions')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
$AE = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]

function Wait-For([scriptblock]$Get, [int]$Ms = 8000) {
    $t = [Diagnostics.Stopwatch]::StartNew()
    while ($t.ElapsedMilliseconds -lt $Ms) {
        try { $r = & $Get; if ($r) { return $r } } catch { }
        Start-Sleep -Milliseconds 250
    }
    return $null
}
function By-Id($el, [string]$id) {
    $c = New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty, $id)
    return $el.FindFirst($Scope::Descendants, $c)
}
function Browser-Windows {
    $c = New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, 'Chrome_WidgetWin_1')
    $pid_ = @(Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($Exe)) -ErrorAction SilentlyContinue | ForEach-Object Id)
    # real browser windows only: their title ends with the browser's name
    return @($AE::RootElement.FindAll($Scope::Children, $c) | Where-Object {
        $pid_ -contains $_.Current.ProcessId -and $_.Current.Name -match '(Chrome|Edge)$' -and -not $_.Current.IsOffscreen })
}
Add-Type -Namespace Fling -Name Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, string l);
[DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
'@
function Bring-Front($el) {
    $h = [IntPtr]$el.Current.NativeWindowHandle
    if ([Fling.Win]::IsIconic($h)) { [void][Fling.Win]::ShowWindow($h, 9) }   # SW_RESTORE
    [void][Fling.Win]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 300
}
function Press($el) {
    $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
function Out-Result([string]$s) { Write-Output $s; exit 0 }

# our card on the extensions page: its title starts with "Fling" in every language
function Find-Card {
    $all = $w.FindAll($Scope::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) { if ($e.Current.Name -like 'Fling*') { return $e } }
    return $null
}

# 1. a browser window (start the browser if it isn't running)
$w = Wait-For { Browser-Windows | Select-Object -First 1 } 300
if (-not $w) { Start-Process $Exe; $w = Wait-For { Browser-Windows | Select-Object -First 1 } 15000 }
if (-not $w) { Out-Result 'fail:browser' }

# 2. open the extensions page: chrome:// URLs can't be passed on the command line, so type it
$open = $null
foreach ($x in Browser-Windows) { if (By-Id $x 'devMode') { $open = $x; break } }
if ($open) { $w = $open; Bring-Front $w } else {
Bring-Front $w
[System.Windows.Forms.SendKeys]::SendWait('^t')
Start-Sleep -Milliseconds 400
# the address bar (class OmniboxViewViews); typing is avoided - the keyboard layout may be Russian
$edit = Wait-For { $w.FindFirst($Scope::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, 'OmniboxViewViews'))) } 3000
$vp = $null
if ($edit) { try { $vp = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern) } catch { } }
if ($vp) {
    try { $edit.SetFocus() } catch { }
    $vp.SetValue($Page)
} else {                                   # fallback: paste into the focused address bar
    [System.Windows.Forms.Clipboard]::SetText($Page)
    [System.Windows.Forms.SendKeys]::SendWait('^l')
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait('^v')
}
Start-Sleep -Milliseconds 150
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
}

# 3. the page's own controls (the browser exposes web content once someone asks for it)
$dev = Wait-For { By-Id $w 'devMode' } 15000
if (-not $dev) { Out-Result 'fail:page' }

# already loaded? an extension card with our name
$already = Find-Card
if ($already) { Out-Result 'already' }

# 4. Developer mode on: "Load unpacked" only shows then. Buttons are pressed from the keyboard
# (focus + Space): Chrome cancels a file picker opened without a real user gesture.
function Key-Press($el) {
    Bring-Front $w
    try { $el.SetFocus() } catch { }
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait(' ')
}
$load = By-Id $w 'loadUnpacked'
if (-not $load -or $load.Current.IsOffscreen) {
    Key-Press $dev
    $load = Wait-For { $l = By-Id $w 'loadUnpacked'; if ($l -and -not $l.Current.IsOffscreen) { $l } } 5000
}
if (-not $load) { Out-Result 'fail:devmode' }
$dlgCond = New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, '#32770')
$old = $w.FindFirst($Scope::Children, $dlgCond)          # a picker left open earlier: cancel it
if ($old) { $b = By-Id $old '2'; if ($b) { [void][Fling.Win]::SendMessage([IntPtr]$b.Current.NativeWindowHandle, 0xF5, [IntPtr]::Zero, [IntPtr]::Zero) }; Start-Sleep -Milliseconds 600 }
Key-Press $load

# 5. the folder picker. Its classic controls have no UI Automation value, so plain window
# messages: WM_SETTEXT into the "Folder:" field (id 1152), BM_CLICK on "Select Folder" (id 1).
function Kid($d, [string]$id) { $d.FindFirst($Scope::Children, (New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty, $id))) }
$dlg = Wait-For { $d = $w.FindFirst($Scope::Children, $dlgCond); if ($d -and (Kid $d '1152') -and (Kid $d '1')) { $d } } 8000
if (-not $dlg) { Out-Result 'fail:dialog' }
[void][Fling.Win]::SendMessage([IntPtr](Kid $dlg '1152').Current.NativeWindowHandle, 0x000C, [IntPtr]::Zero, $Dir)
Start-Sleep -Milliseconds 200
[void][Fling.Win]::SendMessage([IntPtr](Kid $dlg '1').Current.NativeWindowHandle, 0xF5, [IntPtr]::Zero, [IntPtr]::Zero)

# 6. check: our card appears on the page
$card = Wait-For { Find-Card } 8000
if ($card) { Out-Result 'ok' }
Out-Result 'fail:verify'
