; Inno Setup script: builds SolarPVDesigner-Setup-<version>.exe from the PyInstaller output.
; Installs per user (no admin rights needed), which suits locked-down office laptops.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{5B8E2C1A-7A39-4C55-9F7E-3E5C2A9D1F40}
AppName=Solar PV Designer
AppVersion={#AppVersion}
AppPublisher=Solar PV Designer contributors
AppPublisherURL=https://github.com/mithilyr/solar-pv-designer
DefaultDirName={autopf}\Solar PV Designer
DefaultGroupName=Solar PV Designer
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=SolarPVDesigner-Setup-{#AppVersion}
SetupIconFile=..\src\solarpv\data\icon.ico
UninstallDisplayIcon={app}\SolarPVDesigner.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\SolarPVDesigner\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Solar PV Designer"; Filename: "{app}\SolarPVDesigner.exe"
Name: "{group}\Uninstall Solar PV Designer"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Solar PV Designer"; Filename: "{app}\SolarPVDesigner.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.spvd"; ValueType: string; ValueName: ""; ValueData: "SolarPVDesigner.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\SolarPVDesigner.Project"; ValueType: string; ValueName: ""; ValueData: "Solar PV Designer project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\SolarPVDesigner.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\SolarPVDesigner.exe,0"
Root: HKA; Subkey: "Software\Classes\SolarPVDesigner.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SolarPVDesigner.exe"" ""%1"""

[Run]
Filename: "{app}\SolarPVDesigner.exe"; Description: "Start Solar PV Designer"; Flags: nowait postinstall skipifsilent
