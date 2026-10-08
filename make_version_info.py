"""Writes version_info.txt (the Windows version resource PyInstaller embeds in the exe).

SignPath requires product name and version to be set in the signed binary, so the
release build reads the version from disk_cleaner/__init__.py - one place to bump.

    python make_version_info.py            writes version_info.txt
    python make_version_info.py --print    prints the 4-part version, e.g. 0.1.0.0
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCT = "Disk Cleaner"
COMPANY = "Disk Cleaner contributors"


def version():
    text = (ROOT / "disk_cleaner" / "__init__.py").read_text(encoding="utf-8")
    raw = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    nums = [int(x) for x in re.findall(r"\d+", raw)][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def main():
    v = version()
    dotted = ".".join(map(str, v))
    if "--print" in sys.argv:
        print(dotted)
        return
    out = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{COMPANY}'),
      StringStruct('FileDescription', '{PRODUCT}'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', 'DiskCleaner'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Disk Cleaner contributors. MIT License'),
      StringStruct('OriginalFilename', 'DiskCleaner.exe'),
      StringStruct('ProductName', '{PRODUCT}'),
      StringStruct('ProductVersion', '{dotted}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    (ROOT / "version_info.txt").write_text(out, encoding="utf-8")
    print("wrote version_info.txt for", dotted)


if __name__ == "__main__":
    main()
