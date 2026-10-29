; Inno Setup recipe for Notewell-Setup.exe. Built by .github/workflows/release.yml:
;   iscc /DAppVersion=1.0.0 /DSourceDir=dist\Notewell desktop\installer.iss
;
; Installs for the current person only (no admin prompt). Uninstalling leaves
; their meetings in %USERPROFILE%\Notewell.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Notewell"
#endif

[Setup]
AppId={{6B0E5C1A-7E52-4C8B-9B39-2F1D4A7C9E31}
AppName=Notewell
AppVersion={#AppVersion}
AppPublisher=Notewell
DefaultDirName={localappdata}\Programs\Notewell
DefaultGroupName=Notewell
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Notewell-Setup-{#AppVersion}
SetupIconFile=notewell.ico
UninstallDisplayIcon={app}\Notewell.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Put a Notewell icon on the desktop"
Name: "startup"; Description: "Start Notewell when Windows starts (so it is ready for every meeting)"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Notewell"; Filename: "{app}\Notewell.exe"
Name: "{group}\Uninstall Notewell"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Notewell"; Filename: "{app}\Notewell.exe"; Tasks: desktopicon
Name: "{userstartup}\Notewell"; Filename: "{app}\Notewell.exe"; Tasks: startup

[Run]
Filename: "{app}\Notewell.exe"; Description: "Open Notewell now"; Flags: nowait postinstall skipifsilent
