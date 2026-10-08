# Code signing policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).

## What is signed

Only `TickClean.exe`, built by the GitHub Actions workflow in `.github/workflows/release.yml` from the source code in this repository. Nothing else is signed. The executable bundles the unmodified Python runtime, Tcl/Tk and the PyInstaller bootloader (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)); these are packaged, not modified, and no other third-party binaries are signed. The workflow runs on GitHub-hosted runners, so every signed release can be traced to a tagged commit in this repository.

Each release also includes a `TickClean.exe.sha256` checksum file.

## Team roles

This is currently a one-person project, so the same maintainer holds all three roles. Changes from anyone else go through review by that maintainer before they are merged, and signing is still approved by hand for every release. More maintainers will be listed here as they join.

Every signing request must be approved by hand by an Approver in SignPath. Members of all roles use multi-factor authentication on GitHub and on SignPath.

| Role | What they do | Who |
|---|---|---|
| Committers and authors | Change the source code without extra review | @nalinkaggarwal |
| Reviewers | Review every change from anyone who is not a committer | @nalinkaggarwal |
| Approvers | Approve each signing request, after checking the release tag and the CI result | @nalinkaggarwal |

## Privacy

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.

TickClean makes no network connections of its own and sends no telemetry. Scans read local files and the Windows registry. Settings, the hidden-items list and logs stay in `%LOCALAPPDATA%\TickClean` on your computer. When you choose to remove a program, that program's own uninstaller runs and may behave as its publisher designed.

## Reporting a problem with a signed release

Open an issue in this repository. If you believe a signed file is malicious, please also email the SignPath Foundation (see https://signpath.org).
