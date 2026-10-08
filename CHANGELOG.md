# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - First public release

### Safety
- Folders are never emptied through links or junctions: only the link itself is removed.
- The path guard resolves links, junctions and short (8.3) names, refuses network, device and relative paths, and refuses anything inside Documents, OneDrive, credential folders, Program Files and other users' profiles.
- The elevated helper receives only item ids and re-scans, so a modified plan file cannot make it delete or run anything the scanners would not offer.
- Stale-project detection fails safe: a scan cut short counts the project as active; nested build folders follow the whole project's activity.

### Added
- Scans for reclaimable space: temp files and caches, browser caches, stale project build folders, Android tooling, installed software that is probably unneeded, old installers in Downloads, and Windows update leftovers.
- Every suggestion shows a reason and a risk level (Safe, Review, Info). Nothing is deleted until you tick it and confirm.
- Second confirmation before personal photos and videos are deleted.
- Preview-only mode, a "don't suggest this again" list, and settings for folders and age thresholds.
- Optional Task Scheduler job that scans in the background and opens the window only when there is enough to clean.
- Standalone single-file `DiskCleaner.exe` (no Python needed) with version metadata and an icon.
- Modern window: filter by risk, in-window dialogs, single running copy.
