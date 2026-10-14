; Inno Setup script for ArchPilot.
;
; Prerequisite: build the application first, so that dist\ArchPilot\ exists:
;
;     pyinstaller installer\archpilot.spec --noconfirm
;
; Then compile this script with Inno Setup 6:
;
;     iscc installer\archpilot.iss
;
; The result is installer\Output\ArchPilot-Setup-<version>.exe.
;
; FreeCAD is NOT bundled and is NOT installed by this script. ArchPilot drives an independent
; FreeCAD installation, so the setup checks for it and, if it is missing, tells the user where to
; get it rather than silently installing an unrelated third-party application.

#define AppName "ArchPilot"
#define AppVersion "0.1.0"
#define AppPublisher "ArchPilot"
#define AppExeName "ArchPilot.exe"
#define FreeCadUrl "https://www.freecad.org/downloads.php"

[Setup]
AppId={{8F2A6C31-5B7E-4A9D-9E5C-3A1B7D2F4C60}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
; Per-user install by default so no administrator rights are needed for a normal install.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller onedir output.
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Runtime data lives in Documents\ArchPilot and is deliberately NOT removed on uninstall, so a
; reinstall or an upgrade keeps the user's projects. Only the installed program files go.
Type: filesandordirs; Name: "{app}"

[Code]
// FreeCAD records its install directory in a versioned key under SOFTWARE, e.g.
// HKLM\SOFTWARE\FreeCAD114. Verified on FreeCAD 1.1.4: that key holds an *unnamed* default value
// (shown as "(Default)" in regedit) plus a named "Version" value. An empty value name reads the
// unnamed default value; 'Default' is tried only as a fallback, for a build that uses a name.
// This mirrors archpilot/cad/client.py, which searches the same keys at run time.

function LooksLikeFreeCadKey(const Name: String): Boolean;
begin
  Result :=
    (Length(Name) > Length('FreeCAD')) and
    (CompareText(Copy(Name, 1, Length('FreeCAD')), 'FreeCAD') = 0);
end;

function FreeCadBaseFromKey(RootKey: Integer; const FullKey: String): String;
var
  Value: String;
begin
  Result := '';
  if RegQueryStringValue(RootKey, FullKey, '', Value) then
    Result := Value
  else if RegQueryStringValue(RootKey, FullKey, 'Default', Value) then
    Result := Value;
end;

function FreeCadBaseFromRoot(RootKey: Integer; const SubKey: String): String;
var
  Names: TArrayOfString;
  I: Integer;
begin
  Result := '';
  if not RegGetSubkeyNames(RootKey, SubKey, Names) then
    Exit;
  for I := 0 to GetArrayLength(Names) - 1 do
    if LooksLikeFreeCadKey(Names[I]) then
    begin
      Result := FreeCadBaseFromKey(RootKey, SubKey + '\' + Names[I]);
      if Result <> '' then
        Exit;
    end;
end;

function FreeCadCmdPath(): String;
var
  Base: String;
begin
  Base := FreeCadBaseFromRoot(HKLM64, 'SOFTWARE');
  if Base = '' then
    Base := FreeCadBaseFromRoot(HKCU64, 'SOFTWARE');
  if Base <> '' then
    Result := Base + '\bin\freecadcmd.exe'
  else
    // Last resort for an install that left no registry entry: the usual default locations,
    // the same ones ArchPilot falls back to.
    Result := 'C:\Program Files\FreeCAD\bin\freecadcmd.exe';
end;

function FreeCadFound(): Boolean;
begin
  Result := FileExists(FreeCadCmdPath()) or
            FileExists('C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe') or
            FileExists('C:\Program Files (x86)\FreeCAD\bin\freecadcmd.exe');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not FreeCadFound() then
      MsgBox('ArchPilot was installed, but FreeCAD was not found on this computer.' + #13#10 + #13#10 +
             'FreeCAD does the 3D modelling. ArchPilot works without it for designing and editing rates, ' +
             'but it cannot build or measure a model until FreeCAD is installed.' + #13#10 + #13#10 +
             'Download FreeCAD free from: {#FreeCadUrl}' + #13#10 + #13#10 +
             'If FreeCAD is already installed somewhere unusual, point ArchPilot at it on the Settings screen.',
             mbInformation, MB_OK);
  end;
end;
