; Morph for Windows — installer (Inno Setup 6). Built by desktop/build_windows.ps1:
;   ISCC.exe /DAppVersion=1.0.0 desktop\morph.iss    -> dist\Morph-windows-setup.exe
; Installs for the current user only (no admin rights), adds Start-menu + optional desktop shortcut.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{7C1E3A52-5E9B-4C7A-9C0F-4D2B8E61A0F3}
AppName=Morph
AppVersion={#AppVersion}
AppPublisher=Morph
AppPublisherURL=https://morphpdf.antideploy.app
DefaultDirName={localappdata}\Programs\Morph
DefaultGroupName=Morph
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Morph-windows-setup
SetupIconFile=morph.ico
UninstallDisplayIcon={app}\Morph.exe
; strongest compression: LZMA2, solid archive, large dictionary
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Morph\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Morph"; Filename: "{app}\Morph.exe"
Name: "{autodesktop}\Morph"; Filename: "{app}\Morph.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Morph.exe"; Description: "Open Morph now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
