@echo off
rem Builds dist\DiskCleaner.exe (no console window, no Python needed to run it).
rem Needs Python 3.10+ and: pip install -r requirements-build.txt
python make_version_info.py || exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --name DiskCleaner ^
  --version-file version_info.txt ^
  --icon disk_cleaner\icon.ico ^
  --add-data "disk_cleaner\rules.json;disk_cleaner" ^
  --add-data "disk_cleaner\icon.ico;disk_cleaner" run_disk_cleaner.py || exit /b 1
