; Windows installer (ticket B1 design 11) -- Inno Setup script.
;
; Service wrapper decision (made during the real PyInstaller trial build,
; per direct instruction -- not deferred further): NSSM
; (nssm.cc, https://nssm.cc), not pywin32. The trial build (this session,
; on macOS -- see deploy/pyinstaller.spec's hiddenimports comments and
; deploy/stingray_cli.py's multiprocessing.freeze_support() fix) confirmed
; `stingray_cli.py` needs zero Windows-service-specific code once
; freeze_support() is in place -- NSSM wraps the *existing*, unmodified
; `stingray.exe planner`/`stingray.exe capture` invocations as Windows
; services via external configuration, with no pywin32 dependency to
; bundle (a Windows-only addition to the PyInstaller build, another
; potential hidden-import gap, untestable outside a real Windows CI
; runner) and no service-control-manager-aware code inside the
; cross-platform entry point. Matches this ticket's "no ceremony" bias.
; nssm.exe (bundled here, not built by this repo -- download nssm.cc's
; release zip and place win64/nssm.exe next to this script before
; compiling) does the actual service registration in [Run] below.

#define MyAppName "Stingray"
#define MyAppVersion "0.1.0"
#define MyInstallDir "{pf}\Stingray"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={#MyInstallDir}
DefaultGroupName={#MyAppName}
OutputBaseFilename=stingray-installer
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "..\..\dist\stingray.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist
Source: "nssm.exe"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; Planner: always installed and started. No env-file flag needed --
; deploy/stingray_cli.py loads ".env" from its working directory
; (AppDirectory, set below) via python-dotenv uniformly across all three
; service wrappers (design 11).
Filename: "{app}\nssm.exe"; Parameters: "install StingrayPlanner ""{app}\stingray.exe"" planner"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set StingrayPlanner AppDirectory ""{app}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set StingrayPlanner AppExit Default Restart"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "start StingrayPlanner"; Flags: runhidden

; Capture: installed but NOT started -- needs --gateway/--host/--device
; edited for this vessel's actual hardware during commissioning (B5).
; See deploy/README.md.
Filename: "{app}\nssm.exe"; Parameters: "install StingrayCapture ""{app}\stingray.exe"" capture --gateway yacht-devices --host 127.0.0.1"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set StingrayCapture AppDirectory ""{app}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set StingrayCapture AppExit Default Restart"; Flags: runhidden

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop StingrayPlanner"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove StingrayPlanner confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop StingrayCapture"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove StingrayCapture confirm"; Flags: runhidden
