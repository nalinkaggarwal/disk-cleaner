import json
import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent

FROZEN = bool(getattr(sys, "frozen", False))


def launcher():
    """How to start this program again: (executable, leading args, working dir). Works from source and as an exe."""
    if FROZEN:
        return sys.executable, [], str(Path(sys.executable).parent)
    exe = sys.executable
    pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return (pw if os.path.exists(pw) else exe), ["-m", "disk_cleaner"], str(REPO_DIR)


# All per-user data (settings, ignore list, logs) lives outside the code folder.
DATA_DIR = Path(os.environ.get("DISK_CLEANER_HOME") or
                (Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DiskCleaner"))
LOG_DIR = DATA_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"
IGNORED_FILE = DATA_DIR / "ignored.json"
STATE_FILE = DATA_DIR / "state.json"

DEFAULTS = {
    # Folders searched for stale build output (node_modules, build, .dart_tool, ...). Add your own in Settings.
    "project_roots": ["~/source", "~/projects", "~/dev", "~/code", "~/repos", "~/Documents/GitHub"],
    # Personal folders. Nothing in them is touched unless you tick it and confirm a second time.
    "media_roots": ["~/Videos", "~/Pictures", "~/Music", "~/Desktop"],
    "stale_project_days": 60,
    "installer_age_days": 60,
    "min_item_mb": 50,
    "schedule": {
        "frequency": "Weekly",
        "day": "Sunday",
        "time": "10:00",
        "min_reclaim_gb": 2.0,
        "low_space_percent": 15,
    },
}


def ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    """Write atomically so a crash or power cut cannot leave a half-written settings file."""
    ensure_dirs()
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    saved = _read_json(CONFIG_FILE, {})
    for k, v in (saved.items() if isinstance(saved, dict) else ()):
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save_config(cfg):
    _write_json(CONFIG_FILE, cfg)


def expand(p):
    return os.path.normpath(os.path.expandvars(os.path.expanduser(p)))


def load_ignored():
    saved = _read_json(IGNORED_FILE, [])
    return {i for i in saved if isinstance(i, str)} if isinstance(saved, list) else set()


def save_ignored(ids):
    _write_json(IGNORED_FILE, sorted(ids))


def load_state():
    saved = _read_json(STATE_FILE, {})
    return saved if isinstance(saved, dict) else {}


def save_state(state):
    _write_json(STATE_FILE, state)
