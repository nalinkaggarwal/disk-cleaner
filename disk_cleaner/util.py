import base64
import ctypes
import os
import shutil
import subprocess
import time

CREATE_NO_WINDOW = 0x08000000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def system_drive():
    return (os.environ.get("SystemDrive") or "C:") + "\\"


def disk_usage(drive=None):
    u = shutil.disk_usage(drive or system_drive())
    return u.total, u.used, u.free


def is_reparse(path_or_entry):
    """True for symlinks and junctions. Directory walks skip them so sizes/deletes never leave the tree."""
    try:
        if hasattr(path_or_entry, "is_symlink"):
            if path_or_entry.is_symlink():
                return True
            st = path_or_entry.stat(follow_symlinks=False)
        else:
            if os.path.islink(path_or_entry):
                return True
            st = os.lstat(path_or_entry)
        return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True


def dir_size(path, older_than_days=None):
    """Total file bytes under path. Skips links/junctions. Optionally only files older than N days."""
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    total = 0
    stack = [str(path)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not is_reparse(e):
                                stack.append(e.path)
                        elif not e.is_symlink():
                            st = e.stat(follow_symlinks=False)
                            if cutoff is None or st.st_mtime < cutoff:
                                total += st.st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def newest_mtime(path, skip_names=(), max_files=20000):
    """Newest file mtime under path, ignoring directories named in skip_names.
    If the walk hits max_files before finishing, the folder is reported as just-touched (fail safe)."""
    newest, seen = 0.0, 0
    stack = [str(path)]
    skip = {s.lower() for s in skip_names}
    while stack and seen < max_files:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name.lower() not in skip and not is_reparse(e):
                                stack.append(e.path)
                        elif not e.is_symlink():
                            seen += 1
                            newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
    if stack and seen >= max_files:
        return time.time()
    return newest


def process_running(image_name):
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", creationflags=CREATE_NO_WINDOW, timeout=20,
        ).stdout
        return image_name.lower() in out.lower()
    except Exception:
        return True  # cannot tell, so assume it is running and let the user close it


def ps_quote(value):
    """Escape a value for use inside a single-quoted PowerShell string (typographic quotes count as quotes too)."""
    return "".join(c * 2 if c in "'\u2018\u2019\u201a\u201b" else c for c in str(value))


def _powershell_args(script):
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]


def powershell(script, timeout=120):
    return subprocess.run(_powershell_args(script), capture_output=True, text=True, errors="replace",
                          creationflags=CREATE_NO_WINDOW, timeout=timeout)


def powershell_start(script):
    """Start a PowerShell script without waiting, so the caller can report progress while it runs."""
    return subprocess.Popen(_powershell_args(script), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", creationflags=CREATE_NO_WINDOW)


_INSTANCE_NAME = r"Local\DiskCleaner.SingleInstance"
_instance_handle = None


def single_instance():
    """True if this is the only running window. Holds a named mutex until release_instance() or exit."""
    global _instance_handle
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.CreateMutexW(None, 0, _INSTANCE_NAME)
    err = ctypes.get_last_error()
    if not h:
        return False  # access denied: the other copy runs elevated
    if err == 183:  # ERROR_ALREADY_EXISTS
        k.CloseHandle(h)
        return False
    _instance_handle = h
    return True


def release_instance():
    global _instance_handle
    if _instance_handle:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(_instance_handle))
        _instance_handle = None


def focus_existing(title):
    """Best effort: bring the already-open window to the front."""
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, title)
    if hwnd:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        u.SetForegroundWindow(hwnd)
