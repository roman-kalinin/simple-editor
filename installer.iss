; Inno Setup script for Simple Video Editor.
; Builds a standard Windows installer: installs to Program Files, creates
; Start-menu and (optional) desktop shortcuts, and registers the app in
; Add/Remove Programs.
;
; Build the app first (produces dist\SimpleEditor.exe):
;     python -m PyInstaller --noconfirm SimpleEditor.spec
; Then compile this script (produces dist\SimpleEditor-Setup.exe):
;     "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
; (build-installer.bat does both for you.)

#define AppName "Simple Video Editor"
#define AppVersion "1.0.0"
#define AppPublisher "Simple Editor"
#define AppExeName "SimpleEditor.exe"

[Setup]
AppId={{4B2C7E9A-3F1D-4A6B-9C21-SIMPLEEDITOR01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\SimpleEditor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Install per-user by default so no admin prompt is required. Change to
; "admin" if you want a machine-wide install under Program Files.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=SimpleEditor-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; The self-contained PyInstaller exe (includes Python, ffmpeg, and the UI).
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
