"""Windows Task Scheduler integration (per-user task, runs only while you're logged in so the window can open)."""
import os
import re
import shutil
from pathlib import Path

from . import config
from .util import powershell, ps_quote

TASK = "DiskCleaner Scan"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
FREQUENCIES = ["Daily", "Weekly"]
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def validate(frequency, day, time_str):
    """Return None if the values are usable, else a message for the user."""
    if frequency not in FREQUENCIES:
        return "Choose Daily or Weekly."
    if day not in DAYS:
        return "Choose a day of the week."
    if not _TIME.fullmatch(str(time_str)):
        return "Time must be 24-hour HH:MM, for example 09:30."
    return None


def _stable_exe(exe):
    """The task must not depend on where the user happened to save the download, so it runs a private copy.
    Returns (path, warning). Raises OSError if there is no usable copy."""
    dest = config.DATA_DIR / "app" / "DiskCleaner.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if Path(exe).resolve() == dest.resolve():
        return str(dest), ""
    tmp = dest.with_name("DiskCleaner.exe.new")
    try:
        shutil.copy2(exe, tmp)
        os.replace(tmp, dest)
        return str(dest), ""
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        if dest.exists():
            return str(dest), f" (Could not update the saved copy of the program, so the older one is used: {e})"
        raise


def install(frequency="Weekly", day="Sunday", time_str="10:00"):
    """Create or replace the scheduled scan. Returns (ok, message)."""
    bad = validate(frequency, day, time_str)
    if bad:
        return False, bad
    trigger = (f"New-ScheduledTaskTrigger -Daily -At '{time_str}'" if frequency == "Daily"
               else f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek {day} -At '{time_str}'")
    exe, lead, workdir = config.launcher()
    warning = ""
    if config.FROZEN:
        try:
            exe, warning = _stable_exe(exe)
        except OSError as e:
            return False, f"Could not save a copy of the program for the schedule: {e}"
        workdir = str(Path(exe).parent)
    argline = " ".join(lead + ["--scheduled"])
    script = f"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
try {{
$a = New-ScheduledTaskAction -Execute '{ps_quote(exe)}' -Argument '{ps_quote(argline)}' -WorkingDirectory '{ps_quote(workdir)}'
$t = {trigger}
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
Register-ScheduledTask -TaskName '{TASK}' -Action $a -Trigger $t -Settings $s -Description 'Scans for reclaimable disk space and shows what can be removed' -Force | Out-Null
}} catch {{ Write-Output ('ERROR: ' + $_.Exception.Message); exit 1 }}
"""
    r = powershell(script, 60)
    if r.returncode != 0:
        lines = [ln[7:].strip() for ln in (r.stdout or "").splitlines() if ln.startswith("ERROR: ")]
        return False, lines[0] if lines else "Task Scheduler could not create the task."
    when = "every day" if frequency == "Daily" else f"every {day}"
    return True, f"Scheduled: {when} at {time_str}. If the PC is off then, it runs the next time you log in.{warning}"


def remove():
    r = powershell(f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false -ErrorAction SilentlyContinue", 30)
    if config.FROZEN:
        shutil.rmtree(config.DATA_DIR / "app", ignore_errors=True)
    return r.returncode == 0, "Schedule removed."


def status():
    """Return None if not scheduled, else dict(state, next_run, last_run)."""
    script = f"""
$ProgressPreference = 'SilentlyContinue'
$t = Get-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue
if (-not $t) {{ 'none'; exit }}
$i = Get-ScheduledTaskInfo -TaskName '{TASK}'
$n = ''; if ($i.NextRunTime) {{ $n = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') }}
$l = ''; if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) {{ $l = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') }}
"$($t.State)|$n|$l"
"""
    try:
        out = (powershell(script, 30).stdout or "").strip()
    except Exception:
        return None
    if not out or out == "none":
        return None
    parts = (out.splitlines()[-1].split("|") + ["", "", ""])[:3]
    return {"state": parts[0], "next_run": parts[1], "last_run": parts[2]}
