; Windows-Installer für KleinFaktura (Inno Setup 6).
; Bauen:  iscc /DAppVersion=X.Y.Z installer\KleinFaktura.iss   (nach PyInstaller, erwartet dist\KleinFaktura\)
; Installiert ohne Administratorrechte nach %LOCALAPPDATA%\Programs\KleinFaktura. Die Daten liegen getrennt unter
; %LOCALAPPDATA%\KleinFaktura und werden weder bei Updates noch bei der Deinstallation angefasst.

#ifndef AppVersion
  #error AppVersion fehlt: iscc /DAppVersion=X.Y.Z installer\KleinFaktura.iss
#endif
#define AppName "KleinFaktura"
#define AppExe "KleinFaktura.exe"

[Setup]
AppId={{6B7E4C1A-2F3D-4B8E-9C5A-1D0E7F3A2B64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=KleinFaktura (Open Source)
AppPublisherURL=https://kleinfaktura.de
AppSupportURL=https://github.com/Skibbie/KleinFaktura/issues
AppUpdatesURL=https://github.com/Skibbie/KleinFaktura/releases
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
LicenseFile=..\LICENSE
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop anlegen"; GroupDescription: "Zusätzliche Verknüpfungen:"

[InstallDelete]
; Programmdateien der Vorversion entfernen (alte Bibliotheken dürfen nicht liegen bleiben)
Type: filesandordirs; Name: "{app}\_internal"
; Umstieg von EEE-Faktura: alte Verknüpfung entfernen, damit nicht zwei Programme dieselbe Datenbank nutzen
Type: files; Name: "{userdesktop}\EEE-Faktura.lnk"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; nach einer stillen Installation (Programm-Update) startet das Update-Skript das Programm selbst
Filename: "{app}\{#AppExe}"; Description: "{#AppName} jetzt starten"; Flags: nowait postinstall skipifsilent

[Messages]
de.FinishedLabel=Die Installation von [name] ist abgeschlossen.%n%nIhre Daten (Datenbank, Belegarchiv) liegen getrennt im Benutzerordner unter AppData\Local\KleinFaktura und bleiben auch bei einer Deinstallation erhalten.
