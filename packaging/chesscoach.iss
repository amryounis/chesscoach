; Inno Setup: the Windows installer, from PyInstaller's dist\chesscoach (with
; Stockfish already put in dist\chesscoach\stockfish). Built by
; .github/workflows/installer.yml:  iscc /DAppVersion=0.28.0 packaging\chesscoach.iss
;
; Installs for the current user only (no administrator rights asked), into
; %LOCALAPPDATA%\Programs\chesscoach. Uninstalling leaves the settings and the
; saved games (they are in the user's own folders, not the program's).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C3E9B52-7A1D-4F0E-9B8C-2D5A41E7C930}
AppName=chesscoach
AppVersion={#AppVersion}
AppVerName=chesscoach {#AppVersion}
AppPublisher=Amr
AppPublisherURL=https://github.com/amryounis/chesscoach
AppSupportURL=https://github.com/amryounis/chesscoach/issues
DefaultDirName={localappdata}\Programs\chesscoach
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=chesscoach-setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\chesscoach.exe
UninstallDisplayName=chesscoach

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; an update replaces the program's own files; nothing of the user's is here
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\chesscoach\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\chesscoach"; Filename: "{app}\chesscoach.exe"
Name: "{autodesktop}\chesscoach"; Filename: "{app}\chesscoach.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\chesscoach.exe"; Description: "{cm:LaunchProgram,chesscoach}"; Flags: nowait postinstall skipifsilent
