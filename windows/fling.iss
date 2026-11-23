; Fling for Windows — setup (Inno Setup 6). Built by windows\build.ps1 after PyInstaller:
;   ISCC /DAppVersion=<ver> /DSourceDir=<build\windows\out\Fling> /DOutDir=<dist> windows\fling.iss
; Per-user install, no admin rights: %LOCALAPPDATA%\Programs\Fling. Settings in %APPDATA%\Fling
; stay when Fling is uninstalled.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\windows\out\Fling"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{6F1C2B9E-6A3D-4C1F-9E57-3B1F7C0D2A61}
AppName=Fling
AppVersion={#AppVersion}
AppVerName=Fling {#AppVersion}
AppPublisher=Fling for Windows (unofficial port by orfixe)
AppPublisherURL=https://github.com/orfixe/fling-port
AppSupportURL=https://github.com/orfixe/fling-port/issues
DefaultDirName={localappdata}\Programs\Fling
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=Fling-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\..\..\fling.ico
UninstallDisplayIcon={app}\Fling.exe
UninstallDisplayName=Fling
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; a running Fling is closed for the update and started again afterwards
CloseApplications=force
RestartApplications=no
ChangesEnvironment=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.DesktopIcon=Значок на рабочем столе
ru.Autostart=Запускать вместе с Windows
ru.ApkMenu=Пункт «Установить на ТВ (Fling)» в меню .apk-файлов
ru.FlingCommand=Команда fling в терминале
ru.InstallOnTv=Установить на ТВ (Fling)
ru.LaunchFling=Запустить Fling
en.DesktopIcon=Desktop icon
en.Autostart=Start with Windows
en.ApkMenu="Install on TV (Fling)" in the menu of .apk files
en.FlingCommand=The fling command in the terminal
en.InstallOnTv=Install on TV (Fling)
en.LaunchFling=Start Fling

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked
Name: "autostart"; Description: "{cm:Autostart}"
Name: "apkmenu"; Description: "{cm:ApkMenu}"
Name: "flingcmd"; Description: "{cm:FlingCommand}"

[InstallDelete]
; leftovers of an older build in the same folder
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "fling.cmd"; DestDir: "{app}"; Tasks: flingcmd; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Fling"; Filename: "{app}\Fling.exe"; Parameters: "show"; WorkingDir: "{app}"
Name: "{userdesktop}\Fling"; Filename: "{app}\Fling.exe"; Parameters: "show"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; Start with Windows (Fling's own tray switch writes the same value)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Fling"; \
  ValueData: """{app}\Fling.exe"" tray"; Tasks: autostart; Flags: uninsdeletevalue
; Explorer: right-click an .apk -> Install on TV
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk"; Tasks: apkmenu; \
  Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk"; ValueType: string; \
  ValueName: "MUIVerb"; ValueData: "{cm:InstallOnTv}"; Tasks: apkmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk"; ValueType: string; \
  ValueName: "Icon"; ValueData: "{app}\Fling.exe,0"; Tasks: apkmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.apk\shell\FlingInstallApk\command"; \
  ValueType: string; ValueName: ""; ValueData: """{app}\Fling.exe"" install-apk ""%1"""; Tasks: apkmenu
; `fling` command: the install folder on the user's PATH
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: flingcmd; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\Fling.exe"; Parameters: "show"; Description: "{cm:LaunchFling}"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; ask a running Fling to quit before its files go
Filename: "{app}\Fling.exe"; Parameters: "quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitFling"
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Fling /f"; \
  Flags: runhidden; RunOnceId: "DropAutostart"

[Code]
function NeedsAddPath(Dir: string): Boolean;
var
  Path: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';') = 0;
end;

procedure RemoveFromPath(Dir: string);
var
  Path: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
    exit;
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';');
  if P = 0 then
    exit;
  { P points into ';' + Path + ';' — in Path itself the entry starts at P, its ';' before it at P - 1 }
  if P = 1 then
    Delete(Path, 1, Length(Dir) + 1)        { first entry: "Dir;" }
  else
    Delete(Path, P - 1, Length(Dir) + 1);   { later entry: ";Dir" }
  RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
