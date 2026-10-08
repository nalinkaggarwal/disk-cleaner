"""Applies selected findings. Every path is re-checked against the guard right before it is deleted, links and
junctions are never followed, and the elevated helper receives finding ids only (see run_elevated)."""
import datetime
import glob
import json
import logging
import os
import re
import secrets
import stat
import subprocess
import time

from . import config, software
from .util import (CREATE_NO_WINDOW, disk_usage, is_admin, is_reparse, powershell, powershell_start,
                   process_running, ps_quote)

log = logging.getLogger("disk_cleaner")

BUILTINS = {
    "empty_recycle_bin": "Clear-RecycleBin -Force -ErrorAction SilentlyContinue",
    "dism_cleanup": "Dism.exe /Online /Cleanup-Image /StartComponentCleanup | Out-Null; exit $LASTEXITCODE",
}


def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.expandvars(p))) if p else ""


def _real(p):
    """Absolute form with symlinks, junctions and 8.3 short names resolved."""
    if not p:
        return ""
    p = os.path.abspath(os.path.expandvars(p))
    try:
        p = os.path.realpath(p)
    except OSError:
        pass
    return os.path.normcase(p)


def _forms(p):
    return {f for f in (_norm(p), _real(p)) if f}


def _under(p, root):
    return p == root or p.startswith(root.rstrip("\\") + "\\")


def _protected_roots(cfg):
    home = os.path.expanduser("~")
    roots = [os.path.join(home, d) for d in ("Pictures", "Videos", "Music", "Desktop")]
    roots += [config.expand(p) for p in cfg.get("media_roots", [])]
    return sorted(set().union(*(_forms(r) for r in roots)))


def guard(path, cfg=None, allow_personal=False):
    """Return None if the path may be deleted, else a human-readable refusal reason.
    allow_personal lifts only the photos/videos protection, and only for items strictly inside a personal folder.
    The path is checked both as written and with links and short names resolved."""
    cfg = cfg or config.load_config()
    expanded = os.path.expandvars(str(path))
    if not os.path.isabs(expanded) or expanded.startswith("\\\\"):
        return "only local, absolute paths can be deleted"
    for form in _forms(path):
        why = _guard_form(form, str(path), cfg, allow_personal)
        if why:
            return why
    return None


def _guard_form(p, path, cfg, allow_personal):
    _, rest = os.path.splitdrive(p)
    if not rest.strip("\\"):
        return "refusing to touch a drive root"
    env = os.environ.get

    def roots(*paths):
        return sorted(set().union(*(_forms(x) for x in paths if x)))
    home_raw = os.path.expanduser("~")
    home = roots(home_raw)
    system_raw = env("SystemRoot") or r"C:\Windows"
    system = roots(system_raw)
    protected = _protected_roots(cfg)
    # Exactly these folders (and anything that contains them) must never be deleted.
    never = roots(home_raw, os.path.dirname(home_raw), env("ProgramFiles"), env("ProgramFiles(x86)"),
                  env("ProgramData"), system_raw, env("APPDATA"), env("LOCALAPPDATA"),
                  os.path.join(home_raw, "Downloads"), os.path.join(home_raw, "Documents"),
                  os.path.join(home_raw, "OneDrive"))
    # Nothing inside these is ever a valid target.
    onedrive = [env(v) for v in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    onedrive += [os.path.join(home_raw, "OneDrive")] + glob.glob(os.path.join(home_raw, "OneDrive*"))
    off_limits = roots(os.path.join(home_raw, "Documents"), env("ProgramFiles"), env("ProgramFiles(x86)"),
                       *onedrive, *(os.path.join(home_raw, d) for d in (".ssh", ".gnupg", ".aws", ".azure", ".kube")))
    for r in never + ([] if allow_personal else protected):
        if p == r:
            return f"{path} is a protected folder"
        if _under(r, p):
            return f"{path} contains protected folders"
    for r in off_limits:
        if _under(p, r):
            return "inside a folder that holds your documents, credentials or installed programs"
    real = _real(p)  # compare resolved forms so a short 8.3 spelling of your own profile is not "another user"
    for u in roots(os.path.dirname(home_raw)):
        if _under(real, u) and not any(_under(real, h) for h in home):
            return "inside another user's profile"
    if allow_personal:
        if p in protected:
            return f"{path} is a protected folder"
        if not any(_under(p, r) for r in protected):
            return "not inside a personal folder"
    else:
        for r in protected:
            if _under(p, r):
                return "inside a personal photos/videos folder (needs the extra confirmation to delete)"
    if any(_under(p, s) for s in system):
        allowed = roots(os.path.join(system_raw, "SoftwareDistribution", "Download"), os.path.join(system_raw, "Temp"),
                        os.path.join(system_raw, "ServiceProfiles", "NetworkService", "AppData", "Local", "Microsoft",
                                     "Windows", "DeliveryOptimization", "Cache"))
        if not any(_under(p, a) for a in allowed):
            return "Windows system folder"
    return None


def _long(p):
    p = os.path.abspath(p)
    return p if p.startswith("\\\\?\\") else "\\\\?\\" + p


def _remove_link(path):
    """Remove a junction or symlink (or a file-like reparse point) itself, never what it points to."""
    try:
        os.rmdir(path)
    except OSError:
        os.remove(path)


def _delete_tree(root, older_than_days=None):
    """Delete files under root (only those older than N days if given), then prune empty folders.
    Links and junctions are removed as links and never followed. Does not remove root itself.
    Returns (removed_bytes, failures)."""
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    removed, fails = 0, 0
    folders, stack = [], [_long(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            fails += 1
            continue
        for e in entries:
            try:
                if is_reparse(e):
                    if cutoff is None:
                        _remove_link(e.path)
                    continue
                if e.is_dir(follow_symlinks=False):
                    folders.append(e.path)
                    stack.append(e.path)
                    continue
                st = e.stat(follow_symlinks=False)
                if cutoff is not None and st.st_mtime >= cutoff:
                    continue
                os.chmod(e.path, stat.S_IWRITE)
                os.remove(e.path)
                removed += st.st_size
            except OSError:
                fails += 1
    for d in reversed(folders):  # children before parents
        try:
            os.rmdir(d)  # succeeds only when empty
        except OSError:
            pass
    return removed, fails


def _delete_path_action(action, cfg, dry_run, allow_personal=False):
    for exe in action.get("require_closed", []):
        if process_running(exe):
            return False, 0, f"Close {exe} first, then try again."
    msgs, removed, fails = [], 0, 0
    stopped = []
    try:
        for s in ([] if dry_run else action.get("stop_services", [])):
            r = subprocess.run(["net", "stop", s], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
            if r.returncode == 0:
                stopped.append(s)
        for path in action["paths"]:
            why = guard(path, cfg, allow_personal)
            if why:
                msgs.append(f"blocked: {why}")
                fails += 1
                continue
            if not os.path.lexists(path):
                continue
            if dry_run:
                msgs.append(f"would delete {path}")
                continue
            if os.path.isfile(path) or os.path.islink(path):
                try:
                    size = os.lstat(path).st_size
                    os.chmod(path, stat.S_IWRITE)
                    os.remove(path)
                    removed += size
                except OSError as e:
                    fails += 1
                    msgs.append(str(e))
                continue
            if is_reparse(path):
                try:
                    os.rmdir(path)  # removes the junction itself, never what it points to
                except OSError as e:
                    fails += 1
                    msgs.append(str(e))
                continue
            r, f = _delete_tree(path, action.get("older_than_days"))
            removed += r
            fails += f
            if not action.get("contents_only") and not action.get("older_than_days"):
                try:
                    os.rmdir(_long(path))
                except OSError:
                    fails += 1
    finally:
        for s in stopped:  # restart only what this run stopped
            subprocess.run(["net", "start", s], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
    if dry_run:
        return True, 0, "; ".join(msgs) or "nothing to delete"
    msg = f"removed {removed / 1048576:.0f} MB"
    if fails:
        msg += f", {fails} item(s) skipped (in use or blocked)"
    if msgs:
        msg += " [" + "; ".join(msgs[:3]) + "]"
    return (fails == 0 or removed > 0), removed, msg


def _uninstall_action(action, dry_run):
    e = software.lookup(action["hive"], action["wow"], action["key"])
    if not e:
        return True, 0, "already uninstalled"
    cmd, silent = software.uninstall_command(e)
    if not cmd:
        return False, 0, "no uninstall command registered"
    if dry_run:
        return True, 0, f"would run: {cmd}"
    try:
        p = subprocess.run(cmd, shell=True, timeout=3600, creationflags=CREATE_NO_WINDOW if silent else 0)
    except subprocess.TimeoutExpired:
        return False, 0, "uninstaller timed out"
    if p.returncode in (0, 1605, 3010, 1641):
        extra = " (reboot needed to finish)" if p.returncode in (3010, 1641) else ""
        return True, e.get("size", 0), f"uninstalled{extra}"
    return False, 0, f"uninstaller exit code {p.returncode}"


def _builtin_action(action, dry_run):
    script = BUILTINS.get(action.get("name"))
    if not script:
        return False, 0, "unknown built-in action"
    if dry_run:
        return True, 0, f"would run built-in '{action['name']}'"
    r = powershell(script, timeout=3600)
    return r.returncode == 0, 0, "done" if r.returncode == 0 else f"exit code {r.returncode}"


def apply_plan(items, dry_run=False, cfg=None, progress=None):
    """items: list of dicts {id,title,action}. Returns list of result dicts.
    progress(done, total, title) is called before each item."""
    cfg = cfg or config.load_config()
    results = []
    for n, it in enumerate(items):
        if progress:
            progress(n, len(items), it["title"])
        a = it["action"]
        try:
            kind = a["type"]
            if kind == "delete_path":
                allow = bool(a.get("personal")) and bool(it.get("personal_confirmed"))
                ok, freed, msg = _delete_path_action(a, cfg, dry_run, allow)
            elif kind == "uninstall":
                ok, freed, msg = _uninstall_action(a, dry_run)
            elif kind == "builtin":
                ok, freed, msg = _builtin_action(a, dry_run)
            else:
                ok, freed, msg = False, 0, f"unsupported action {kind}"
        except Exception as ex:
            ok, freed, msg = False, 0, f"error: {ex}"
        log.info("%s | %s | ok=%s freed=%s | %s%s", it["id"], it["title"], ok, freed, msg, " (dry run)" if dry_run else "")
        results.append({"id": it["id"], "title": it["title"], "ok": ok, "freed": freed, "message": msg})
    return results


def _progress_files(token):
    return list(config.DATA_DIR.glob(f"progress_{token}_*.json"))


def _read_progress(token, last, progress):
    """Report the newest progress note the elevated helper has written. Returns the number reported."""
    newest = last
    for f in _progress_files(token):
        try:
            n = int(f.stem.rsplit("_", 1)[1])
        except ValueError:
            continue
        if n > newest:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                progress(int(data["done"]), int(data["total"]), str(data["text"]))
                newest = n
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return newest


def run_elevated(items, dry_run=False, progress=None):
    """Run administrator-only findings in an elevated copy of this program (one UAC prompt).
    Only finding ids cross the boundary: the elevated copy scans again and acts on its own results, so a
    modified plan file cannot make it delete or run anything the scanners would not have offered.
    progress(done, total, text) is called as the elevated copy reports back."""
    config.ensure_dirs()
    token = secrets.token_hex(8)
    plan = config.DATA_DIR / f"plan_{token}.json"
    out = config.DATA_DIR / f"result_{token}.json"
    plan.write_text(json.dumps({"ids": [i["id"] for i in items], "dry_run": dry_run}), encoding="utf-8")
    exe, lead, workdir = config.launcher()
    args = lead + ["--apply", f'"{plan}"', f'"{out}"']
    arglist = ",".join("'" + ps_quote(a) + "'" for a in args)
    ps = (f"try {{ Start-Process -FilePath '{ps_quote(exe)}' -ArgumentList {arglist} "
          f"-WorkingDirectory '{ps_quote(workdir)}' -Verb RunAs -Wait -WindowStyle Hidden; 'ok' }} "
          "catch { 'declined' }")
    declined = False
    try:
        proc = powershell_start(ps)
        started, last = time.time(), 0
        while True:
            try:
                stdout, _ = proc.communicate(timeout=0.3)
                declined = "declined" in (stdout or "")
                break
            except subprocess.TimeoutExpired:
                if time.time() - started > 7200:
                    proc.kill()
                    break
                if progress:
                    last = _read_progress(token, last, progress)
        if progress:
            _read_progress(token, last, progress)
        if declined or not out.exists():
            why = "Administrator prompt was declined" if declined else "elevated run produced no result"
            return [{"id": i["id"], "title": i["title"], "ok": False, "freed": 0, "message": why} for i in items]
        return json.loads(out.read_text(encoding="utf-8"))
    finally:
        for f in [plan, out] + _progress_files(token):
            try:
                f.unlink()
            except OSError:
                pass


def execute(findings, dry_run=False, personal_confirmed=False, progress=None):
    """High-level entry used by the GUI. Returns (results, free_before, free_after).
    Personal photo/video items are refused unless personal_confirmed is True (the GUI's second confirmation)."""
    _, _, before = disk_usage()
    items = [{"id": f.id, "title": f.title, "action": f.action, "personal_confirmed": personal_confirmed}
             for f in findings]
    admin_ids = {f.id for f in findings if f.needs_admin}
    now_items = [i for i in items if i["id"] not in admin_ids or is_admin()]
    adm_items = [i for i in items if i["id"] in admin_ids and not is_admin()]
    total = len(items)
    results = apply_plan(now_items, dry_run, progress=(lambda d, t, title: progress(d, total, title)) if progress else None)
    if adm_items:
        if progress:
            progress(len(now_items), total, "Waiting for administrator approval\u2026")
        results += run_elevated(
            adm_items, dry_run,
            progress=(lambda d, t, text: progress(len(now_items) + d, total, text)) if progress else None)
    if progress:
        progress(total, total, "Finishing\u2026")
    if not dry_run and any(r["id"] == "win:dism" and r["ok"] for r in results):
        st = config.load_state()
        st["last_dism"] = datetime.date.today().isoformat()
        config.save_state(st)
    _, _, after = disk_usage()
    return results, before, after


def apply_from_files(plan_path, result_path):
    """Entry point for the elevated child process. The plan holds finding ids only."""
    from . import scanners
    with open(plan_path, encoding="utf-8") as fh:
        data = json.load(fh)
    match = re.fullmatch(r"result_([0-9a-f]{16})\.json", os.path.basename(result_path))
    counter = [0]

    def report(done, total, text):
        # Best effort and exclusive-create, so a pre-planted file or link can never be written through.
        if not match:
            return
        counter[0] += 1
        path = os.path.join(os.path.dirname(result_path), f"progress_{match.group(1)}_{counter[0]}.json")
        try:
            with open(path, "x", encoding="utf-8") as fh:
                json.dump({"done": done, "total": total, "text": text}, fh)
        except OSError:
            pass
    ids = [str(i) for i in data.get("ids", [])]
    report(0, len(ids), "Approved. Checking this PC as administrator\u2026")
    findings, _ = scanners.scan(config.load_config(), config.load_state())
    by_id = {f.id: f for f in findings}
    items, res = [], []
    for fid in ids:
        f = by_id.get(fid)
        if f is None or not f.needs_admin or not f.selectable:
            res.append({"id": fid, "title": fid, "ok": False, "freed": 0,
                        "message": "no longer offered as an administrator item"})
        else:
            items.append({"id": f.id, "title": f.title, "action": f.action})
    res += apply_plan(items, bool(data.get("dry_run")), progress=report)
    with open(result_path, "x", encoding="utf-8") as fh:  # exclusive create: never write through a planted file
        json.dump(res, fh)
