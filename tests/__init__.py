"""Tests run against a throwaway data folder so they never touch the real settings or logs."""
import atexit
import os
import shutil
import tempfile

_home = tempfile.mkdtemp(prefix="dc_test_home_")
os.environ["TICKCLEAN_HOME"] = _home
atexit.register(shutil.rmtree, _home, True)
