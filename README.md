# TickClean

[![tests](https://github.com/nalinkaggarwal/tickclean/actions/workflows/ci.yml/badge.svg)](https://github.com/nalinkaggarwal/tickclean/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Free up disk space on Windows, safely. TickClean finds things you probably no longer need, shows them in a list with a **reason for each one**, and deletes only the items **you tick**.

- **Nothing is deleted without your confirmation.** Every row is a suggestion; you tick what you want and confirm.
- **Zero runtime dependencies.** Pure Python standard library (tkinter, winreg). Or download the single `TickClean.exe`, no Python needed.
- **No network access, no telemetry.** Everything stays on your machine.
- **Safe by design.** A guard re-checks every path right before it is deleted (see [Safety model](#safety-model)).
- **Optional schedule.** A background scan that opens the window only when there is something worth cleaning.

> Status: early release (0.x). Review the suggestions, and keep backups of anything you care about.

## What it looks for

| Category | Examples |
|---|---|
| Temporary files and caches | Temp, crash dumps, npm / pip / yarn / Gradle / NuGet / pub / conda / Maven caches, Recycle Bin |
| Browser caches | Chrome, Edge, Brave (logins and history untouched), Chrome's on-device AI model |
| Stale project build folders | `node_modules`, `build`, `target`, `.dart_tool`, `.gradle`, virtualenvs in projects untouched for N days |
| Android tooling | Duplicate SDKs, old system images, NDK, emulator disks |
| Installed software | Programs commonly reported as adware, OEM utilities, end-of-life .NET / Python / Java, server software whose services are stopped |
| Old installers in Downloads | `.exe`, `.msi`, `.iso`, `.msix`, `.appx` not changed for N days |
| Windows (needs admin) | Windows Update download cache, Delivery Optimization, component-store cleanup (DISM) |
| Report only | Large photo/video folders, WSL / Docker virtual disks, files named like passwords |

Each suggestion has a risk label: **Safe** (rebuilds itself), **Review** (read the reason first) or **Info** (reported only, never deleted). Use the filter buttons at the top to show one group.

## Download (no Python needed)

1. Open the **Releases** page of this repository and download `TickClean.exe`.
2. Double-click it. The first time, it asks whether you want automatic background checks. You can say no and change your mind later with the **Schedule** button.

Releases are code-signed once the SignPath Foundation certificate is active (see [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)). Until then, or while SmartScreen is still learning the file, Windows may say the app is unrecognized: click **More info**, then **Run anyway**. You can verify the download against the `.sha256` file on the release page, or build the exe yourself from this source. The release exe is built by GitHub Actions from the public source.

## Run from source

Needs only Python 3.10+ on Windows. No packages to install.

```
python -m tickclean
```

or `pip install .` and run `tickclean`.

## Build the exe yourself

```
pip install -r requirements-build.txt
build_exe.bat
```

The result is `dist\TickClean.exe`. Pushing a tag such as `v0.1.0` makes GitHub Actions build and publish it automatically.

Other commands:

```
python -m tickclean --scan            # text report, nothing is changed
python -m tickclean --scan --json
python -m tickclean --install-schedule
python -m tickclean --remove-schedule
python -m tickclean --schedule-status
```

## Code signing and privacy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. See the [code signing policy](CODE_SIGNING_POLICY.md) for who approves releases.

## Safety model

- Before anything is deleted, a guard checks the path again. It refuses drive roots, network and device paths, relative paths, your profile folder and your Documents, OneDrive and credential folders (`.ssh`, `.aws`, ...), other users' profiles, Program Files, ProgramData itself, Windows (except the update download cache, `Windows\Temp` and the Delivery Optimization cache), and the AppData roots. Links, junctions and short (8.3) names are resolved first, so a path cannot sneak past by pointing somewhere else.
- Symlinks and junctions are never followed: if a folder you clear contains one, only the link is removed, never what it points to.
- Photos, videos, music and Desktop are never touched by any other item. Large ones are listed unticked, and ticking one triggers a second warning dialog (default answer: Keep) before anything is deleted. Personal items are deleted permanently, not sent to the Recycle Bin. Add more personal folders under `media_roots` in the settings.
- Documents, Downloads and OneDrive are protected as folders; only specific old installer files inside Downloads can be suggested.
- Administrator work runs in a second copy of the program (one UAC prompt). That copy receives only the ids of the items you ticked and works out for itself, by scanning again, what each id means, so a modified file on disk cannot make it delete or run anything the scanners would not have offered. Only items that need administrator rights are run this way.
- Uninstalling a program runs that program's own registered uninstall command (read from the Windows registry), exactly as the Windows "Apps" settings page would. No other commands are built from data.
- Use **Preview only** to see what would happen without changing anything.
- Deleted files do not go to the Recycle Bin.

Found a way around this? Please read [SECURITY.md](SECURITY.md).

## Schedule

Click **Schedule** in the app, or use `--install-schedule`. It creates a per-user Windows Task Scheduler task ("TickClean Scan") that runs a private copy of the exe stored in `%LOCALAPPDATA%\TickClean\app`, so moving or deleting the file you downloaded does not break it. The task scans silently and opens the window only if it can free at least your chosen number of GB or the drive is below your chosen free-space percentage. If the PC was off at the scheduled time, it runs at the next opportunity. After updating the app, press **Save & turn on** in Schedule once to refresh that copy.

## Teaching it new rules

Software rules live in `tickclean/rules.json`:

```json
{"id": "my-rule", "pattern": "^SomeAnnoyingApp", "risk": "REVIEW", "reason": "Why a person should consider removing it."}
```

`pattern` is a case-insensitive regular expression matched against the program name. `risk` is `SAFE` or `REVIEW`; prefer `REVIEW` unless removing the program can never hurt. Describe programs factually. Pull requests that add well-explained rules are welcome (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Your data

Settings, the "don't suggest again" list and logs are stored in `%LOCALAPPDATA%\TickClean` (override with the `TICKCLEAN_HOME` environment variable). Only one window can be open at a time.

## Limitations

- Windows 10/11 only.
- Without administrator rights, sizes of some system-owned folders (and the data inside server software such as SQL Server) are under-reported.
- Uninstalling programs runs each program's own uninstaller, which may open its own window.
- Nothing inside your Documents or OneDrive folders is ever deleted by this tool, even if you add such a folder to the settings.

## Tests

```
python -m unittest discover -s tests -t .
```

## License

MIT. See [LICENSE](LICENSE). Bundled third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
