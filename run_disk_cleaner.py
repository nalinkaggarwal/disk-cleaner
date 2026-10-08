"""Entry point used when building the standalone .exe (PyInstaller needs a plain script)."""
import sys

from disk_cleaner.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
