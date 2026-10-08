"""Detection rules. Every scanner returns Findings that carry a plain-language reason.
Scanning is read-only; nothing here deletes anything."""
import datetime
import glob
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, software
from .models import Finding, SAFE, REVIEW, INFO
from .util import dir_size, is_admin, is_reparse, newest_mtime, system_drive

GB = 1024 ** 3

CAT_TEMP = "Temporary files & caches"
CAT_BROWSER = "Browser caches"
CAT_DEV = "Developer caches"
CAT_WIN = "Windows"
CAT_PROJ = "Stale project build folders"
CAT_ANDROID = "Android tooling"
CAT_SW = "Installed software"
CAT_DL = "Old installers & archives in Downloads"
CAT_MEDIA = "Large personal photos, videos & files (asks twice before deleting)"
CAT_SEC = "Security"
CAT_VDISK = "Virtual disks"

CATEGORY_ORDER = [CAT_TEMP, CAT_BROWSER, CAT_DEV, CAT_PROJ, CAT_ANDROID, CAT_SW, CAT_DL, CAT_WIN,
                  CAT_VDISK, CAT_MEDIA, CAT_SEC]


def norm(p):
    return os.path.normcase(os.path.normpath(p))


def _env(name):
    return os.environ.get(name, "")


class Ctx:
    def __init__(self, cfg, state=None):
        self.cfg = cfg
        self.state = state or {}
        self.min_bytes = int(cfg.get("min_item_mb", 50) * 1024 * 1024)
        self.home = os.path.expanduser("~")
        self.local = _env("LOCALAPPDATA")
        self.roaming = _env("APPDATA")
        self.admin = is_admin()
        self.today = datetime.date.today()

    def roots(self, key):
        seen, out = set(), []
        for p in self.cfg.get(key, []):
            e = config.expand(p)
            if os.path.isdir(e) and norm(e) not in seen:
                seen.add(norm(e))
                out.append(e)
        return out


def delete_action(paths, **kw):
    a = {"type": "delete_path", "paths": list(paths)}
    a.update(kw)
    return a


def _existing(paths):
    return [p for p in paths if p and os.path.lexists(p)]


def _size_paths(paths, older=None):
    return sum(dir_size(p, older) if os.path.isdir(p) else _file_size(p) for p in paths)


def _file_size(p):
    try:
        return os.lstat(p).st_size
    except OSError:
        return 0


# ----------------------------------------------------------------------------- caches
def scan_caches(ctx):
    L, R, H = ctx.local, ctx.roaming, ctx.home
    temp = _env("TEMP")
    defs = [
        ("cache:temp", CAT_TEMP, "Temporary files (older than 2 days)", [temp], 2, SAFE,
         "Leftovers that apps and installers forgot to delete. Only files untouched for 2+ days are removed, "
         "so anything a running program is using is left alone.", {}),
        ("cache:crash", CAT_TEMP, "Application crash dumps",
         [os.path.join(L, "CrashDumps"), os.path.join(L, "Microsoft", "Windows", "WER")], None, SAFE,
         "Memory dumps and error reports written when programs crash. Only useful for debugging a past crash.", {}),
        ("cache:inet", CAT_TEMP, "Windows internet & shader caches",
         [os.path.join(L, "Microsoft", "Windows", "INetCache"), os.path.join(L, "D3DSCache"),
          os.path.join(L, "SquirrelTemp")], None, SAFE,
         "Cached downloads, GPU shader caches and updater scratch space. Windows and apps rebuild them on demand.", {}),
        ("dev:npm", CAT_DEV, "npm download cache",
         [os.path.join(L, "npm-cache"), os.path.join(R, "npm-cache")], None, SAFE,
         "Copies of packages npm downloaded earlier. Rebuilt automatically the next time you run npm install "
         "(needs internet).", {}),
        ("dev:pip", CAT_DEV, "pip download cache", [os.path.join(L, "pip")], None, SAFE,
         "Cached Python package downloads. pip downloads them again when needed.", {}),
        ("dev:yarn", CAT_DEV, "Yarn cache", [os.path.join(L, "Yarn", "Cache")], None, SAFE,
         "Cached JavaScript packages. Yarn downloads them again when needed.", {}),
        ("dev:gradle", CAT_DEV, "Gradle build cache", [os.path.join(H, ".gradle", "caches")], None, SAFE,
         "Downloaded libraries and build outputs for Android/Java projects. The next build re-downloads what it "
         "needs (slower the first time, needs internet).", {}),
        ("dev:nuget", CAT_DEV, "NuGet package cache", [os.path.join(H, ".nuget", "packages")], None, SAFE,
         "Downloaded .NET packages. Restored automatically on the next build (needs internet).", {}),
        ("dev:pub", CAT_DEV, "Dart/Flutter pub cache", [os.path.join(L, "Pub", "Cache")], None, REVIEW,
         "Downloaded Dart/Flutter packages. 'flutter pub get' re-downloads them, but offline builds will fail "
         "until you do.", {}),
        ("dev:conda", CAT_DEV, "Conda package cache",
         [os.path.join(H, n, "pkgs") for n in ("anaconda3", "miniconda3", "miniforge3")], None, SAFE,
         "Downloaded installers for conda packages that are already unpacked into your environments. "
         "Equivalent to 'conda clean --all'.", {}),
        ("dev:maven", CAT_DEV, "Maven local repository", [os.path.join(H, ".m2", "repository")], None, REVIEW,
         "Downloaded Java libraries. Re-downloaded on the next build, but anything you installed into it by hand "
         "would be lost.", {}),
    ]
    out = []
    for fid, cat, title, paths, older, risk, why, extra in defs:
        paths = _existing(paths)
        if not paths:
            continue
        size = _size_paths(paths, older)
        if size < ctx.min_bytes:
            continue
        act = delete_action(paths, contents_only=True, **({"older_than_days": older} if older else {}))
        out.append(Finding(fid, cat, title, "; ".join(paths), size, why, risk, act))

    f = _recycle_bin(ctx)
    if f:
        out.append(f)
    return out


def _recycle_bin(ctx):
    size = dir_size(os.path.join(system_drive(), "$Recycle.Bin"))
    if size < ctx.min_bytes:
        return None
    return Finding("cache:recycle", CAT_TEMP, "Recycle Bin", os.path.join(system_drive(), "$Recycle.Bin"), size,
                   "Files you already deleted that are still occupying space. Emptying it is permanent, so look "
                   "inside first if you are unsure.", REVIEW, {"type": "builtin", "name": "empty_recycle_bin"})


def scan_browsers(ctx):
    L = ctx.local
    browsers = [("Google Chrome", os.path.join(L, "Google", "Chrome", "User Data"), "chrome.exe"),
                ("Microsoft Edge", os.path.join(L, "Microsoft", "Edge", "User Data"), "msedge.exe"),
                ("Brave", os.path.join(L, "BraveSoftware", "Brave-Browser", "User Data"), "brave.exe")]
    out = []
    for name, base, exe in browsers:
        if not os.path.isdir(base):
            continue
        paths = []
        for prof in glob.glob(os.path.join(base, "Default")) + glob.glob(os.path.join(base, "Profile *")):
            for sub in ("Cache", "Code Cache", "GPUCache", os.path.join("Service Worker", "CacheStorage")):
                paths.append(os.path.join(prof, sub))
        paths = _existing(paths)
        size = _size_paths(paths)
        if size >= ctx.min_bytes:
            out.append(Finding(f"browser:{exe}", CAT_BROWSER, f"{name} cache", base, size,
                               "Saved copies of web pages and scripts to make sites load faster. Deleting it only "
                               "makes the first visit to each site a little slower. Logins, history and bookmarks "
                               "are not touched. Close the browser first.", SAFE,
                               delete_action(paths, contents_only=True, require_closed=[exe])))
        model = os.path.join(base, "OptGuideOnDeviceModel")
        if os.path.isdir(model):
            msize = dir_size(model)
            if msize >= ctx.min_bytes:
                out.append(Finding(f"browser:{exe}:aimodel", CAT_BROWSER, f"{name} on-device AI model", model, msize,
                                   "A multi-GB local AI model the browser downloaded for its built-in AI features. "
                                   "It is downloaded again unless you disable the feature in the browser's flags "
                                   "page (optimization-guide-on-device-model). Close the browser first.", REVIEW,
                                   delete_action([model], require_closed=[exe])))
    return out


# ----------------------------------------------------------------------------- windows
def scan_windows(ctx):
    system = _env("SystemRoot") or r"C:\Windows"
    defs = [
        ("win:wu", "Windows Update download cache", [os.path.join(system, "SoftwareDistribution", "Download")],
         "Installer files Windows Update has already applied. Windows re-downloads anything it still needs. "
         "The update service is paused briefly while this runs.", SAFE, {"stop_services": ["wuauserv", "bits"]}, None),
        ("win:temp", "Windows system temp folder (older than 7 days)", [os.path.join(system, "Temp")],
         "Scratch files left by installers and services. Only files untouched for a week are removed.", SAFE, {}, 7),
        ("win:do", "Delivery Optimization cache",
         [os.path.join(system, "ServiceProfiles", "NetworkService", "AppData", "Local", "Microsoft", "Windows",
                       "DeliveryOptimization", "Cache")],
         "Update files Windows keeps to share with other PCs. Safe to clear; Windows downloads again if needed.",
         SAFE, {}, None),
        ("win:nvidia", "NVIDIA driver installer downloads",
         [os.path.join(_env("ProgramData"), "NVIDIA Corporation", "Downloader")],
         "Old GeForce driver installers kept after installation. The installed driver is unaffected; newer ones "
         "are downloaded when you update.", SAFE, {}, None),
    ]
    out = []
    for fid, title, paths, why, risk, extra, older in defs:
        paths = _existing(paths)
        if not paths:
            continue
        size = _size_paths(paths, older)
        if size < ctx.min_bytes:
            continue
        kw = dict(contents_only=True, **extra)
        if older:
            kw["older_than_days"] = older
        out.append(Finding(fid, CAT_WIN, title, "; ".join(paths), size, why, risk, delete_action(paths, **kw),
                           needs_admin=True))

    last = ctx.state.get("last_dism")
    stale = True
    if last:
        try:
            stale = (ctx.today - datetime.date.fromisoformat(last)).days > 30
        except ValueError:
            pass
    if stale:
        out.append(Finding("win:dism", CAT_WIN, "Windows component store cleanup (DISM)", "C:\\Windows\\WinSxS", 0,
                           "Windows keeps old copies of system components after updates. This official clean-up "
                           "removes superseded ones; it typically frees 2-6 GB (varies) and takes 5-15 minutes. "
                           "It is the supported way to shrink the WinSxS folder.", REVIEW,
                           {"type": "builtin", "name": "dism_cleanup"}, needs_admin=True,
                           note="Space freed varies; shown after it runs."))
    if os.path.isdir(os.path.join(system_drive(), "Windows.old")):
        size = dir_size(os.path.join(system_drive(), "Windows.old"))
        out.append(Finding("win:old", CAT_WIN, "Previous Windows installation (Windows.old)",
                           os.path.join(system_drive(), "Windows.old"), size,
                           "Left over from a Windows upgrade; only needed to roll back. Remove it with Settings > "
                           "System > Storage > Temporary files > 'Previous Windows installation(s)' (Windows deletes "
                           "it automatically after about 10 days).", INFO, {}, selectable=False))
    return out


# ----------------------------------------------------------------------------- projects
PROJECT_TARGETS = {
    "node_modules": (["package.json"], SAFE,
                     "Downloaded JavaScript packages. Recreated by 'npm install' (or yarn/pnpm)."),
    "build": (["pubspec.yaml", "build.gradle", "build.gradle.kts"], SAFE,
              "Compiled build output. Regenerated by the project's next build."),
    ".dart_tool": (["pubspec.yaml"], SAFE, "Dart/Flutter tooling cache. Regenerated by 'flutter pub get'."),
    "target": (["Cargo.toml", "pom.xml"], SAFE, "Compiled output (Rust/Maven). Regenerated by the next build."),
    ".gradle": (["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"], SAFE,
                "Gradle's per-project cache. Regenerated by the next build."),
}
VENV_NAMES = {".venv", "venv", "env"}
SKIP_DIRS = {"node_modules", "appdata", "$recycle.bin", "windows", "program files", "program files (x86)",
             "programdata", "build", "target", "dist", "__pycache__", "site-packages"}


def _walk_projects(root, max_depth=5):
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        names = {e.name.lower() for e in entries}
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or is_reparse(e):
                    continue
            except OSError:
                continue
            low = e.name.lower()
            t = PROJECT_TARGETS.get(low)
            if t and any(m.lower() in names for m in t[0]):
                yield d, e.path, e.name, t[1], t[2]
                continue
            if low in VENV_NAMES and os.path.exists(os.path.join(e.path, "pyvenv.cfg")):
                yield (d, e.path, e.name, REVIEW,
                       "Python virtual environment. Recreate it with 'python -m venv' and your requirements file.")
                continue
            if low in SKIP_DIRS or low.startswith("."):
                continue
            if depth < max_depth:
                stack.append((e.path, depth + 1))


def _project_dir(root, d):
    """The folder whose activity decides whether a build folder is stale: the nearest ancestor with a .git
    folder, otherwise the top-level folder under the configured project root (not a nested sub-module)."""
    cur = d
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if norm(parent) == norm(root) or parent == cur:
            return cur
        cur = parent


def project_last_touched(d):
    t = newest_mtime(d, skip_names=set(PROJECT_TARGETS) | VENV_NAMES | {".git"})
    for rel in ("index", "HEAD", "FETCH_HEAD"):
        try:
            t = max(t, os.stat(os.path.join(d, ".git", rel)).st_mtime)
        except OSError:
            pass
    return t


def scan_projects(ctx):
    days = int(ctx.cfg.get("stale_project_days", 60))
    cutoff = time.time() - days * 86400
    touched, out = {}, []
    for root in ctx.roots("project_roots"):
        for sub, target, name, risk, why in _walk_projects(root):
            proj = _project_dir(root, sub)
            if proj not in touched:
                touched[proj] = project_last_touched(proj)
            t = touched[proj]
            if not t or t > cutoff:
                continue
            size = dir_size(target)
            if size < ctx.min_bytes:
                continue
            age = (time.time() - t) / 86400
            last = datetime.date.fromtimestamp(t).strftime("%d %b %Y")
            out.append(Finding(
                "proj:" + norm(target), CAT_PROJ, os.path.relpath(target, os.path.dirname(proj)), target, size,
                f"{why} The project hasn't been edited for {age:.0f} days (last change {last}), so this is "
                "unlikely to be needed soon.", risk, delete_action([target])))
    return out


# ----------------------------------------------------------------------------- android
def scan_android(ctx):
    out = []
    env_roots = {norm(_env(v)) for v in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if _env(v)}
    cands = [os.path.join(ctx.local, "Android", "Sdk"), os.path.join(ctx.home, "Android", "Sdk"),
             os.path.join(system_drive(), "Android", "Sdk"), os.path.join(system_drive(), "Android", "sdk")]
    cands += [_env(v) for v in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if _env(v)]
    sdks, seen = [], set()
    for c in cands:
        if c and norm(c) not in seen and any(os.path.isdir(os.path.join(c, s)) for s in
                                             ("platform-tools", "platforms", "cmdline-tools", "build-tools")):
            seen.add(norm(c))
            sdks.append(c)

    if len(sdks) > 1 and not env_roots:
        out.append(Finding("android:multi", CAT_ANDROID, "Several Android SDK copies found", "; ".join(sdks), 0,
                           "More than one Android SDK is installed but ANDROID_HOME is not set, so I can't tell "
                           "which is in use. Set ANDROID_HOME to the one you want and rescan to get a removal "
                           "suggestion for the others.", INFO, {}, selectable=False))
    elif env_roots:
        for s in sdks:
            if norm(s) in env_roots:
                continue
            size = dir_size(s)
            if size >= ctx.min_bytes:
                out.append(Finding("android:dupsdk:" + norm(s), CAT_ANDROID, "Duplicate Android SDK", s, size,
                                   f"A second SDK copy. ANDROID_HOME points at {_env('ANDROID_HOME') or _env('ANDROID_SDK_ROOT')}, "
                                   "so this one is probably unused. If Android Studio is set to use it, "
                                   "point Android Studio at the other copy first.", REVIEW, delete_action([s])))
    active = [s for s in sdks if norm(s) in env_roots] or (sdks if len(sdks) == 1 else [])
    for s in active:
        for kind, pattern, why in (
                ("system image", os.path.join(s, "system-images", "*", "*"),
                 "Emulator system image. Needed only if you run the Android emulator; reinstall via SDK Manager."),
                ("NDK", os.path.join(s, "ndk", "*"),
                 "Native Development Kit. Only needed for projects/plugins that compile C/C++; SDK Manager "
                 "reinstalls it on demand.")):
            for d in glob.glob(pattern):
                size = dir_size(d)
                if size >= ctx.min_bytes:
                    out.append(Finding("android:" + norm(d), CAT_ANDROID, f"Android {kind}: {os.path.basename(d)}",
                                       d, size, why, REVIEW, delete_action([d])))
    avd_dirs = {_env("ANDROID_AVD_HOME"), os.path.join(_env("ANDROID_USER_HOME") or "x:\\none", "avd"),
                os.path.join(ctx.home, ".android", "avd")}
    avd_dirs |= {os.path.join(os.path.dirname(s), ".android", "avd") for s in sdks}
    for base in avd_dirs:
        if not base or not os.path.isdir(base):
            continue
        for d in glob.glob(os.path.join(base, "*.avd")):
            size = dir_size(d)
            if size >= ctx.min_bytes:
                name = os.path.basename(d)[:-4]
                out.append(Finding("avd:" + norm(d), CAT_ANDROID, f"Android emulator device: {name}", d, size,
                                   "Virtual phone disk plus saved snapshot. If you test on a real device you "
                                   "probably don't need it; recreate it in Android Studio's Device Manager.",
                                   REVIEW, delete_action([d, os.path.join(base, name + ".ini")])))
    return out


# ----------------------------------------------------------------------------- software
def scan_software(ctx):
    svcs = software.services()
    rules = software.load_rules()
    out = []
    for e in software.installed():
        hit = software.classify(e, svcs, rules, ctx.today)
        if not hit:
            continue
        reason, risk = hit
        size = e["size"]
        loc = e.get("location") or ""
        if not size and loc and os.path.isdir(loc):
            size = dir_size(loc)
        _, silent = software.uninstall_command(e)
        title = e["name"] if e["version"] and e["version"] in e["name"] else f"{e['name']} {e['version']}".strip()
        out.append(Finding(
            f"sw:{e['hive']}:{e['wow']}:{e['key']}", CAT_SW, title, loc or e["publisher"] or "", size, reason, risk,
            {"type": "uninstall", "hive": e["hive"], "wow": e["wow"], "key": e["key"]},
            needs_admin=e["hive"] == "HKLM",
            note="" if silent else "Opens the program's own uninstaller window."))
    return out


# ----------------------------------------------------------------------------- downloads
INSTALLER_EXT = {".exe", ".msi", ".iso", ".msix", ".appx"}


def scan_downloads(ctx):
    base = os.path.join(ctx.home, "Downloads")
    if not os.path.isdir(base):
        return []
    cutoff = time.time() - int(ctx.cfg.get("installer_age_days", 60)) * 86400
    found = []
    for dirpath, dirs, files in os.walk(base):
        if dirpath[len(base):].count(os.sep) >= 2:
            dirs[:] = []
        for f in files:
            if os.path.splitext(f)[1].lower() not in INSTALLER_EXT:
                continue
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size >= ctx.min_bytes and st.st_mtime < cutoff:
                found.append((st.st_size, st.st_mtime, p))
    found.sort(reverse=True)
    out = []
    for size, mtime, p in found[:40]:
        age = (time.time() - mtime) / 86400
        out.append(Finding("dl:" + norm(p), CAT_DL, os.path.basename(p), p, size,
                           f"Installer last changed {age:.0f} days ago. Once a program is installed the "
                           "installer is no longer needed, and you can download it again if you do.",
                           REVIEW, delete_action([p])))
    return out


# ----------------------------------------------------------------------------- personal files (report only)
MEDIA_FOLDER_GB = 5
MEDIA_FILE_GB = 1


def scan_media(ctx):
    out = []
    for root in ctx.roots("media_roots"):
        try:
            children = list(os.scandir(root))
        except OSError:
            continue
        for e in children:
            try:
                if e.is_dir(follow_symlinks=False):
                    if is_reparse(e):
                        continue
                    size, limit = dir_size(e.path), MEDIA_FOLDER_GB * GB
                else:
                    size, limit = e.stat().st_size, MEDIA_FILE_GB * GB
            except OSError:
                continue
            if size >= limit:
                out.append(Finding(
                    "media:" + norm(e.path), CAT_MEDIA, e.name, e.path, size,
                    "Large personal photos, videos or files. Nothing here can be regenerated and deleted files "
                    "do NOT go to the Recycle Bin. Only tick this after you have copied it to an external drive or "
                    "cloud storage and checked that it opens there. You will be asked to confirm a second time.",
                    REVIEW, dict(delete_action([e.path]), personal=True),
                    note="Personal data: permanent deletion, confirmed twice."))
    return out


SECRET_NAME = re.compile(r"(password|passwd|credential|secret|logins?|api[_ -]?keys?)", re.I)
SECRET_EXT = {".txt", ".csv", ".xlsx", ".xls", ".docx", ".doc", ".rtf"}


def scan_secrets(ctx):
    roots = [os.path.join(ctx.home, d) for d in ("Desktop", "Documents", "Downloads")] + ctx.roots("media_roots")
    out, seen = [], set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            if dirpath[len(root):].count(os.sep) >= 3:
                dirs[:] = []
            for f in files:
                if SECRET_NAME.search(f) and os.path.splitext(f)[1].lower() in SECRET_EXT:
                    p = os.path.join(dirpath, f)
                    if norm(p) in seen:
                        continue
                    seen.add(norm(p))
                    out.append(Finding("sec:" + norm(p), CAT_SEC, f, p, _file_size(p),
                                       "The file name suggests passwords or credentials stored as a plain document. "
                                       "Anyone (or any malware) that can read your files can read it. Move the "
                                       "entries into a password manager, then delete the file yourself.",
                                       INFO, {}, selectable=False))
    return out


def scan_vdisks(ctx):
    out = []
    cands = glob.glob(os.path.join(ctx.local, "Docker", "wsl", "**", "*.vhdx"), recursive=True)
    cands += glob.glob(os.path.join(ctx.local, "Packages", "*", "LocalState", "*.vhdx"))
    cands += glob.glob(os.path.join(ctx.local, "Docker", "*.vhdx"))
    for p in cands:
        size = _file_size(p)
        if size >= 5 * GB:
            out.append(Finding("vdisk:" + norm(p), CAT_VDISK, os.path.basename(p), p, size,
                               "WSL/Docker virtual disk. It grows when you use it but never shrinks on its own. "
                               "To reclaim space: free space inside Linux/Docker, run 'wsl --shutdown', then compact "
                               "the .vhdx (Optimize-VHD, or diskpart > compact vdisk). Deleting the file would "
                               "destroy everything stored inside it.", INFO, {}, selectable=False))
    return out


# ----------------------------------------------------------------------------- orchestration
SCANNERS = [
    ("Temporary files & caches", scan_caches), ("Browsers", scan_browsers), ("Windows", scan_windows),
    ("Developer projects", scan_projects), ("Android tooling", scan_android), ("Installed software", scan_software),
    ("Downloads", scan_downloads), ("Personal files", scan_media), ("Security", scan_secrets),
    ("Virtual disks", scan_vdisks),
]


def scan(cfg=None, state=None, progress=None):
    """Run every scanner (in parallel) and return (findings, errors)."""
    cfg = cfg or config.load_config()
    ctx = Ctx(cfg, state if state is not None else config.load_state())
    findings, errors = [], []
    lock = threading.Lock()
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fn, ctx): name for name, fn in SCANNERS}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                res = fut.result()
            except Exception as ex:  # one broken rule must never hide the others
                res = []
                errors.append(f"{name}: {ex}")
            with lock:
                findings.extend(res)
                done += 1
            if progress:
                progress(name, done, len(SCANNERS))
    findings.sort(key=lambda f: (CATEGORY_ORDER.index(f.category) if f.category in CATEGORY_ORDER else 99, -f.size))
    return findings, errors
