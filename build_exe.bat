@echo off
rem Builds dist\TickClean.exe (no console window, no Python needed to run it).
rem Needs Python 3.10+ and: pip install -r requirements-build.txt
python make_version_info.py || exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --name TickClean ^
  --version-file version_info.txt ^
  --icon tickclean\icon.ico ^
  --add-data "tickclean\rules.json;tickclean" ^
  --add-data "tickclean\icon.ico;tickclean" run_tickclean.py || exit /b 1
