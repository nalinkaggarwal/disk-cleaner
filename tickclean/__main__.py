import argparse
import json
import logging
import logging.handlers
import os
import sys

from . import __version__, config


def _setup_logging():
    config.ensure_dirs()
    log = logging.getLogger("tickclean")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(config.LOG_DIR / "tickclean.log", maxBytes=512_000,
                                                 backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    return log


def _print_report(findings, errors, as_json):
    from .util import human
    if as_json:
        print(json.dumps({"findings": [f.to_dict() for f in findings], "errors": errors}, indent=2))
        return
    cat = None
    for f in findings:
        if f.category != cat:
            cat = f.category
            print(f"\n== {cat} ==")
        size = human(f.size) if f.size else "-"
        print(f"[{f.risk:6}] {size:>9}  {f.title}\n           {f.reason}")
    total = sum(f.size for f in findings if f.selectable)
    print(f"\n{len(findings)} finding(s); about {human(total)} could be reclaimed.")
    for e in errors:
        print("scanner problem:", e, file=sys.stderr)


def _scheduled(log):
    from . import scanners
    from .util import disk_usage
    cfg = config.load_config()
    ignored = config.load_ignored()
    findings, errors = scanners.scan(cfg, config.load_state())
    findings = [f for f in findings if f.id not in ignored]
    sched = cfg["schedule"]
    reclaim = sum(f.size for f in findings if f.selectable)
    total, _, free = disk_usage()
    free_pct = free / total * 100 if total else 100
    log.info("scheduled scan: %d findings, %.2f GB reclaimable, %.1f%% free", len(findings), reclaim / 1e9, free_pct)
    if reclaim >= sched["min_reclaim_gb"] * 1e9 or free_pct < sched["low_space_percent"]:
        from . import gui
        gui.run((findings, errors))


def main(argv=None):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    p = argparse.ArgumentParser(prog="tickclean", description="Find and remove unused files and programs, with reasons.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--scan", action="store_true", help="print findings to the console and exit")
    p.add_argument("--json", action="store_true", help="with --scan, output JSON")
    p.add_argument("--scheduled", action="store_true", help="silent scan; open the window only if worthwhile")
    p.add_argument("--apply", nargs=2, metavar=("PLAN", "RESULT"), help=argparse.SUPPRESS)
    p.add_argument("--install-schedule", action="store_true", help="create the Task Scheduler entry from saved settings")
    p.add_argument("--remove-schedule", action="store_true")
    p.add_argument("--schedule-status", action="store_true")
    a = p.parse_args(argv)

    if sys.platform != "win32":
        print("TickClean currently supports Windows 10/11 only.", file=sys.stderr)
        return 2
    log = _setup_logging()
    try:
        if a.apply:
            from . import actions
            actions.apply_from_files(*a.apply)
        elif a.scan:
            from . import scanners
            ignored = config.load_ignored()
            findings, errors = scanners.scan()
            _print_report([f for f in findings if f.id not in ignored], errors, a.json)
        elif a.scheduled:
            _scheduled(log)
        elif a.install_schedule or a.remove_schedule or a.schedule_status:
            from . import scheduler
            if a.install_schedule:
                s = config.load_config()["schedule"]
                print(scheduler.install(s["frequency"], s["day"], s["time"])[1])
            elif a.remove_schedule:
                print(scheduler.remove()[1])
            else:
                print(scheduler.status() or "Not scheduled.")
        else:
            from . import gui
            gui.run()
    except Exception:
        log.exception("fatal error")
        if not a.apply and not a.scheduled and sys.stderr:
            raise
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
