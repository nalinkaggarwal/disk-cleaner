"""Installed-software discovery, 'why remove it' rules, and uninstall-command construction."""
import datetime
import json
import re
import winreg
from pathlib import Path

from .util import powershell

UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
HIVES = [
    ("HKLM", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY, "64"),
    ("HKLM", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY, "32"),
    ("HKCU", winreg.HKEY_CURRENT_USER, 0, ""),
]


def _val(k, name, default=None):
    try:
        return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def _read_entry(hname, wow, sub, k):
    name = _val(k, "DisplayName")
    if not name or _val(k, "SystemComponent", 0) == 1 or _val(k, "ParentDisplayName"):
        return None
    size_kb = _val(k, "EstimatedSize", 0)
    return {
        "name": str(name).strip(),
        "version": str(_val(k, "DisplayVersion", "") or ""),
        "publisher": str(_val(k, "Publisher", "") or ""),
        "size": int(size_kb) * 1024 if isinstance(size_kb, int) else 0,
        "hive": hname, "wow": wow, "key": sub,
        "quiet": _val(k, "QuietUninstallString"),
        "uninstall": _val(k, "UninstallString"),
        "location": _val(k, "InstallLocation"),
    }


def installed():
    seen, out = set(), []
    for hname, hive, flag, wow in HIVES:
        try:
            root = winreg.OpenKey(hive, UNINSTALL, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | flag) as k:
                        e = _read_entry(hname, wow, sub, k)
                except OSError:
                    continue
                if e and (e["name"], e["version"], hname) not in seen:
                    seen.add((e["name"], e["version"], hname))
                    out.append(e)
    return out


def lookup(hive, wow, sub):
    """Re-read one entry fresh from the registry (the executor never trusts commands stored in a plan)."""
    for hname, h, flag, w in HIVES:
        if hname == hive and w == wow:
            try:
                with winreg.OpenKey(h, UNINSTALL + "\\" + sub, 0, winreg.KEY_READ | flag) as k:
                    return _read_entry(hname, wow, sub, k)
            except OSError:
                return None
    return None


def uninstall_command(e):
    """Return (command, silent). Prefers a silent uninstall; falls back to the vendor's own dialog."""
    if e.get("quiet"):
        return e["quiet"], True
    cmd = e.get("uninstall") or ""
    if "msiexec" in cmd.lower():
        m = re.search(r"\{[0-9A-Fa-f-]{36}\}", cmd)
        if m:
            return f"msiexec.exe /x {m.group(0)} /qn /norestart", True
    return cmd, False


def services():
    try:
        r = powershell("Get-CimInstance Win32_Service | Select-Object Name,State | ConvertTo-Json -Compress", 60)
        data = json.loads(r.stdout or "[]")
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def load_rules():
    try:
        data = json.loads((Path(__file__).with_name("rules.json")).read_text(encoding="utf-8"))
        return [(re.compile(r["pattern"], re.I), r["reason"], r.get("risk", "REVIEW")) for r in data.get("software", [])]
    except (OSError, ValueError, KeyError, re.error):
        return []


# ---------- built-in rules that need logic (versions, support dates, service state) ----------
SERVERS = [
    (re.compile(r"^Microsoft SQL Server 20\d\d( \(\d+-bit\))?$", re.I), re.compile(r"^(MSSQL|SQLSERVERAGENT)", re.I), "SQL Server"),
    (re.compile(r"^MySQL Server", re.I), re.compile(r"^MySQL", re.I), "MySQL"),
    (re.compile(r"^PostgreSQL \d+", re.I), re.compile(r"^postgresql", re.I), "PostgreSQL"),
    (re.compile(r"^MongoDB", re.I), re.compile(r"^MongoDB", re.I), "MongoDB"),
]
DOTNET_EOL = {
    1: datetime.date(2019, 6, 27), 2: datetime.date(2021, 8, 21), 3: datetime.date(2022, 12, 13),
    5: datetime.date(2022, 5, 10), 6: datetime.date(2024, 11, 12), 7: datetime.date(2024, 5, 14),
    8: datetime.date(2026, 11, 10), 9: datetime.date(2026, 11, 10),
}
PYTHON_EOL = {
    (3, 5): (2020, 9, 30), (3, 6): (2021, 12, 23), (3, 7): (2023, 6, 27), (3, 8): (2024, 10, 7),
    (3, 9): (2025, 10, 31), (3, 10): (2026, 10, 31), (3, 11): (2027, 10, 31),
}
JAVA_LTS = {8, 11, 17, 21, 25}
SQL_EOL_YEARS = {2008, 2012, 2014, 2016}


def classify(e, svcs, rules=None, today=None):
    """Return (reason, risk) if this program is a removal candidate, else None."""
    today = today or datetime.date.today()
    rules = load_rules() if rules is None else rules
    n = e["name"]

    for pat, reason, risk in rules:
        if pat.search(n):
            return reason, risk

    if ".NET" in n and "Framework" not in n and "Extended" not in n:
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", n)
        if m:
            major = int(m.group(1))
            eol = DOTNET_EOL.get(major)
            if eol and today >= eol:
                return (f".NET {major} reached end of support on {eol:%d %b %Y} and no longer gets security fixes. "
                        "Keep it only if one of your projects still targets this exact version.", "REVIEW")

    m = re.match(r"^Python (\d+)\.(\d+)\.\d+(\s*\(\d+-bit\))?$", n)
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        if major < 3 or (major == 3 and minor < 5):
            return ("Python 2 has been unsupported since January 2020. Leaving it installed also risks it being "
                    "picked up by mistake when you type 'python'.", "REVIEW")
        eol = PYTHON_EOL.get((major, minor))
        if eol and today >= datetime.date(*eol):
            return (f"Python {major}.{minor} is past end of life ({datetime.date(*eol):%d %b %Y}) and gets no "
                    "security fixes. Keep it only if a project is pinned to this version.", "REVIEW")

    m = re.search(r"(?:Development Kit|JDK|Temurin)\D{0,24}(\d+)", n, re.I)
    if m and re.search(r"java|jdk|temurin|corretto|zulu", n, re.I):
        major = int(m.group(1))
        if major not in JAVA_LTS and major >= 9:
            return (f"Java {major} is a short-lived, non-LTS release that stopped receiving updates when the next "
                    "version shipped. Use an LTS JDK (17/21) instead.", "REVIEW")

    for name_re, svc_re, label in SERVERS:
        if name_re.search(n):
            mine = [s for s in svcs if svc_re.search(s.get("Name", ""))]
            year = re.search(r"20\d\d", n)
            if label == "SQL Server" and year and int(year.group(0)) in SQL_EOL_YEARS:
                return (f"{n} is out of extended support. It holds database files that can be tens of GB (the size "
                        "shown is only the program); back up anything you still need first. The uninstaller opens "
                        "its own window.", "REVIEW")
            if mine and not any(str(s.get("State")) == "Running" for s in mine):
                return (f"{label} is installed but none of its services are running "
                        f"({', '.join(s['Name'] for s in mine[:3])}), so it looks unused. Its databases live inside "
                        "the install folder and can be very large (the size shown is only the program, not the "
                        "data), so back up anything you need first.", "REVIEW")
    return None
