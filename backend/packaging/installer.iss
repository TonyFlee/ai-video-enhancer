; AI Video Enhancer - Windows installer (Inno Setup 6)
;
; Build it through scripts\build_installer.ps1, which supplies the /D switches:
;
;   ISCC.exe packaging\installer.iss ^
;       /DAppVersion=1.0.0 ^
;       /DSourceDir=..\dist\AI Video Enhancer ^
;       /DOutputDir=..\installer_output
;
; What the end user sees:
;   Welcome -> License (I accept) -> Choose install folder -> Install (progress)
;   -> Finish (optional desktop shortcut + "Launch AI Video Enhancer")
; plus an entry in Settings > Apps (uninstaller).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\AI Video Enhancer"
#endif
#ifndef OutputDir
  #define OutputDir "..\installer_output"
#endif
#ifndef OutputBase
  #define OutputBase "AI-Video-Enhancer-Setup"
#endif
#ifndef IconFile
  #define IconFile "app.ico"
#endif
#define MyAppName "AI Video Enhancer"
#define MyAppExeName "AI Video Enhancer.exe"
; Fixed id - changing it would leave a second entry in Settings > Apps.
#define MyAppId "{{6B7E1A0D-4C11-4F0E-8A25-9D4E7C21B3F5}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher=fl33
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename={#OutputBase}-{#AppVersion}
OutputDir={#OutputDir}
; lzma2/max + solid: the payload is ~3.2 GB of CUDA DLLs, and the installer has
; to land under GitHub's 2 GB release-asset cap. Solid mode shares the LZMA
; dictionary across files, which is worth roughly another 10% here.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install: no UAC prompt, lands in %LOCALAPPDATA%\Programs, and the app
; already keeps all of its data under %LOCALAPPDATA% (so it also works from a
; non-admin account and from Program Files-like folders alike).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayName={#MyAppName} {#AppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
ChangesAssociations=no
SetupIconFile={#IconFile}
LicenseFile=EULA.txt
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The whole packaged app (exe, _internal, UI, models, FFmpeg, CUDA DLLs).
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Raise the frame rate of a video with AI"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Raise the frame rate of a video with AI"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WV2_KEY = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  { Careful: a line in [Code] may never *start* with '#' - the preprocessor would
    take it for a directive. Keep newlines mid-line or behind a constant like NL. }
  NL = #13#10;

function WebView2Installed(): Boolean;
var
  Pv: String;
begin
  Result := False;
  if RegQueryStringValue(HKCU, WV2_KEY, 'pv', Pv) then
    if (Pv <> '') and (Pv <> '0.0.0.0') then
      Result := True;
  if not Result then
    if RegQueryStringValue(HKLM32, WV2_KEY, 'pv', Pv) then
      if (Pv <> '') and (Pv <> '0.0.0.0') then
        Result := True;
  if not Result then
    if RegQueryStringValue(HKLM64, WV2_KEY, 'pv', Pv) then
      if (Pv <> '') and (Pv <> '0.0.0.0') then
        Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  { An already running instance holds _internal open and would make the file
    copy fail halfway through - close it first instead. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "AI Video Enhancer.exe" /F', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Msg: String;
begin
  if CurStep = ssPostInstall then
    if not WebView2Installed() then
    begin
      Msg := 'The app installed successfully, but the Microsoft Edge WebView2' + NL +
        'Runtime was not found on this PC.' + NL + NL +
        'Nothing is broken: the app will fall back to opening in your default' + NL +
        'browser. For the native window, install the free runtime from' + NL +
        'https://developer.microsoft.com/microsoft-edge/webview2/';
      MsgBox(Msg, mbInformation, MB_OK);
    end;
end;
