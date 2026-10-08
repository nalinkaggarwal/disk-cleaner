"""Headless tests for scheduling, settings files, the scheduled-run decision, elevation split and the GUI's resilience."""
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from disk_cleaner import __main__ as entry
from disk_cleaner import actions, config, scheduler, util
from disk_cleaner.models import Finding, SAFE

WIN = sys.platform == "win32"


class SchedulerTests(unittest.TestCase):
    def test_validate(self):
        self.assertIsNone(scheduler.validate("Weekly", "Sunday", "10:00"))
        self.assertIsNone(scheduler.validate("Daily", "Monday", "23:59"))
        for args in (("Monthly", "Sunday", "10:00"), ("Weekly", "Someday", "10:00"), ("Weekly", "Sunday", "9:00"),
                     ("Weekly", "Sunday", "24:00"), ("Weekly", "Sunday", "10:60"), ("Weekly", "Sunday", "x")):
            self.assertIsNotNone(scheduler.validate(*args), args)

    def test_install_rejects_bad_values_without_running_powershell(self):
        with mock.patch.object(scheduler, "powershell") as ps:
            ok, msg = scheduler.install("Weekly", "Sunday", "9:00")
        self.assertFalse(ok)
        ps.assert_not_called()

    def test_install_quotes_awkward_paths(self):
        captured = {}

        def fake(script, timeout=0):
            captured["script"] = script
            return subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(scheduler, "powershell", fake), \
                mock.patch.object(config, "launcher", return_value=("C:\\Users\\O\u2019Brien\\it's\\app.exe", [], "C:\\x")), \
                mock.patch.object(config, "FROZEN", False):
            ok, _ = scheduler.install("Daily", "Monday", "08:30")
        self.assertTrue(ok)
        self.assertIn("O\u2019\u2019Brien", captured["script"])
        self.assertIn("it''s", captured["script"])
        self.assertIn("New-TimeSpan -Seconds 0", captured["script"])

    def test_install_failure_message_is_readable_not_clixml(self):
        fake = subprocess.CompletedProcess([], 1, "ERROR: Access is denied\n", "#< CLIXML\n<Objs>...")
        with mock.patch.object(scheduler, "powershell", return_value=fake), mock.patch.object(config, "FROZEN", False):
            ok, msg = scheduler.install("Daily", "Monday", "08:30")
        self.assertFalse(ok)
        self.assertEqual(msg, "Access is denied")

    def test_stable_copy_is_replaced_atomically_and_old_one_kept_on_failure(self):
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="dc_sched_")
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "DiskCleaner.exe")
        with open(src, "wb") as fh:
            fh.write(b"new")
        with mock.patch.object(config, "DATA_DIR", config.Path(tmp) / "data"):
            path, warn = scheduler._stable_exe(src)
            self.assertEqual(warn, "")
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"new")
            with mock.patch("disk_cleaner.scheduler.shutil.copy2", side_effect=OSError("locked")):
                path2, warn2 = scheduler._stable_exe(src)
            self.assertEqual(path2, path)
            self.assertIn("older one", warn2)
        with mock.patch.object(config, "DATA_DIR", config.Path(tmp) / "empty"):
            with mock.patch("disk_cleaner.scheduler.shutil.copy2", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    scheduler._stable_exe(src)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = config.Path(tempfile.mkdtemp(prefix="dc_cfg_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.patches = [mock.patch.object(config, name, self.tmp / fname) for name, fname in
                        (("CONFIG_FILE", "config.json"), ("IGNORED_FILE", "ignored.json"), ("STATE_FILE", "state.json"))]
        self.patches.append(mock.patch.object(config, "LOG_DIR", self.tmp / "logs"))
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_partial_schedule_is_merged_with_defaults(self):
        (self.tmp / "config.json").write_text(json.dumps({"schedule": {"time": "07:15"}}), encoding="utf-8")
        cfg = config.load_config()
        self.assertEqual(cfg["schedule"]["time"], "07:15")
        self.assertEqual(cfg["schedule"]["frequency"], config.DEFAULTS["schedule"]["frequency"])

    def test_corrupt_or_wrongly_shaped_files_fall_back_to_defaults(self):
        for text in ("{not json", "[1, 2]", "null", '"text"'):
            (self.tmp / "config.json").write_text(text, encoding="utf-8")
            self.assertEqual(config.load_config()["schedule"], config.DEFAULTS["schedule"], text)
        (self.tmp / "ignored.json").write_text('{"a": 1}', encoding="utf-8")
        self.assertEqual(config.load_ignored(), set())
        (self.tmp / "state.json").write_text("[1]", encoding="utf-8")
        self.assertEqual(config.load_state(), {})

    def test_round_trip_and_no_temp_file_left_behind(self):
        config.save_ignored({"b", "a"})
        self.assertEqual(config.load_ignored(), {"a", "b"})
        cfg = config.load_config()
        cfg["min_item_mb"] = 123
        config.save_config(cfg)
        self.assertEqual(config.load_config()["min_item_mb"], 123)
        self.assertEqual(sorted(p.name for p in self.tmp.glob("*.tmp")), [])


class ScheduledRunTests(unittest.TestCase):
    def run_scheduled(self, findings, free_pct, ignored=()):
        cfg = config.load_config()
        cfg["schedule"].update(min_reclaim_gb=2.0, low_space_percent=15)
        total = 1000 * 1024 ** 3
        with mock.patch.object(config, "load_config", return_value=cfg), \
                mock.patch.object(config, "load_ignored", return_value=set(ignored)), \
                mock.patch("disk_cleaner.scanners.scan", return_value=(findings, [])), \
                mock.patch("disk_cleaner.util.disk_usage", return_value=(total, total, int(total * free_pct / 100))), \
                mock.patch("disk_cleaner.gui.run") as run:
            entry._scheduled(mock.MagicMock())
        return run

    @staticmethod
    def finding(fid, gb):
        return Finding(fid, "c", fid, "", int(gb * 1024 ** 3), "r", SAFE, {"type": "delete_path", "paths": []})

    def test_opens_only_when_enough_to_free_or_drive_nearly_full(self):
        self.assertTrue(self.run_scheduled([self.finding("a", 3)], free_pct=50).called)
        self.assertFalse(self.run_scheduled([self.finding("a", 1)], free_pct=50).called)
        self.assertTrue(self.run_scheduled([self.finding("a", 1)], free_pct=10).called)

    def test_ignored_findings_do_not_count(self):
        self.assertFalse(self.run_scheduled([self.finding("a", 3)], free_pct=50, ignored={"a"}).called)


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.user = Finding("u", "c", "user item", "", 1, "r", SAFE, {"type": "builtin", "name": "x"})
        self.admin = Finding("a", "c", "admin item", "", 1, "r", SAFE, {"type": "builtin", "name": "x"}, needs_admin=True)

    def test_admin_items_go_through_the_elevated_helper_when_not_elevated(self):
        with mock.patch.object(actions, "is_admin", return_value=False), \
                mock.patch.object(actions, "apply_plan", return_value=[{"id": "u", "ok": True}]) as local, \
                mock.patch.object(actions, "run_elevated", return_value=[{"id": "a", "ok": True}]) as elev:
            res, _, _ = actions.execute([self.user, self.admin])
        self.assertEqual([i["id"] for i in local.call_args[0][0]], ["u"])
        self.assertEqual([i["id"] for i in elev.call_args[0][0]], ["a"])
        self.assertEqual(len(res), 2)

    def test_elevated_process_runs_everything_itself(self):
        with mock.patch.object(actions, "is_admin", return_value=True), \
                mock.patch.object(actions, "apply_plan", return_value=[]) as local, \
                mock.patch.object(actions, "run_elevated") as elev:
            actions.execute([self.user, self.admin])
        self.assertEqual(len(local.call_args[0][0]), 2)
        elev.assert_not_called()

    def test_personal_confirmation_is_passed_to_every_item(self):
        with mock.patch.object(actions, "is_admin", return_value=True), \
                mock.patch.object(actions, "apply_plan", return_value=[]) as local:
            actions.execute([self.user], personal_confirmed=True)
            actions.execute([self.user])
        self.assertTrue(local.call_args_list[0][0][0][0]["personal_confirmed"])
        self.assertFalse(local.call_args_list[1][0][0][0]["personal_confirmed"])

    def test_dry_run_changes_nothing_and_writes_no_state(self):
        with mock.patch.object(actions, "is_admin", return_value=True), \
                mock.patch.object(actions.config, "save_state") as save:
            dism = Finding("win:dism", "c", "dism", "", 0, "r", SAFE, {"type": "builtin", "name": "dism_cleanup"},
                           needs_admin=True)
            res, _, _ = actions.execute([dism], dry_run=True)
        self.assertTrue(res[0]["ok"])
        self.assertIn("would run", res[0]["message"])
        save.assert_not_called()


@unittest.skipUnless(WIN, "Windows only")
class UtilTests(unittest.TestCase):
    def test_single_instance_lock_can_be_released_and_retaken(self):
        name = rf"Local\DiskCleaner.Test.{os.getpid()}"  # not the real name, so an open app cannot interfere
        child = [sys.executable, "-c",
                 "import sys; from disk_cleaner import util; util._INSTANCE_NAME = sys.argv[1]; "
                 "print(util.single_instance())", name]

        def other():
            return subprocess.run(child, capture_output=True, text=True).stdout.strip()
        with mock.patch.object(util, "_INSTANCE_NAME", name):
            self.assertTrue(util.single_instance())
            self.addCleanup(util.release_instance)
            self.assertEqual(other(), "False")
            util.release_instance()
            self.assertEqual(other(), "True")

    def test_human_sizes(self):
        self.assertEqual(util.human(0), "0 B")
        self.assertEqual(util.human(1023), "1023 B")
        self.assertEqual(util.human(1024), "1 KB")
        self.assertEqual(util.human(1024 ** 2), "1.0 MB")
        self.assertEqual(util.human(5 * 1024 ** 3), "5.0 GB")


@unittest.skipUnless(WIN, "Windows only")
class GuiResilienceTests(unittest.TestCase):
    def setUp(self):
        try:
            from disk_cleaner import gui
            self.gui = gui
            self.app = gui.App(preloaded=([], []))
        except Exception as e:  # no display available
            self.skipTest(f"no GUI available: {e}")
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.02)

    def test_a_failing_background_callback_does_not_stop_the_polling_loop(self):
        app = self.app
        hits = []
        with mock.patch.object(self.gui.log, "exception"):
            app.q.put(("call", lambda: 1 / 0))
            app.q.put(("call", lambda: hits.append("after the error")))
            self.pump(0.4)
            app.q.put(("call", lambda: hits.append("later")))
            self.pump(0.4)
        self.assertEqual(hits, ["after the error", "later"])

    def test_closing_the_window_while_a_question_is_open_does_not_hang(self):
        app = self.app
        app.after(300, app._on_close)
        started = time.time()
        result = app.confirm("Delete?", "really?", ok="Delete", danger=True, default_ok=False)
        self.assertFalse(result)
        self.assertLess(time.time() - started, 5)

    def test_ticks_survive_switching_filters_but_only_visible_items_are_acted_on(self):
        from disk_cleaner.models import REVIEW
        app = self.app
        a = Finding("a", "c", "safe one", "", 1 << 20, "r", SAFE, {"type": "builtin", "name": "x"})
        b = Finding("b", "c", "review one", "", 1 << 20, "r", REVIEW, {"type": "builtin", "name": "x"})
        app.findings = [a, b]
        app.scanned = True
        app.checked = {"a", "b"}
        app.set_filter(SAFE)
        self.assertEqual([f.id for f in app.shown()], ["a"])
        self.assertIn("1 selected", app.sel_lbl.cget("text"))
        app.set_filter("ALL")
        self.assertIn("2 selected", app.sel_lbl.cget("text"))

    def test_a_scan_that_fails_leaves_the_list_in_a_finished_state(self):
        app = self.app
        with mock.patch.object(app, "notify") as note:
            app._busy(True, "Scanning")
            app.scanned = False
            app.q.put(("scan_fatal", "boom"))
            self.pump(0.4)
        self.assertTrue(app.scanned)
        self.assertFalse(app.busy)
        note.assert_called_once()

    def test_a_rescan_requested_while_busy_runs_afterwards(self):
        app = self.app
        with mock.patch.object(app, "start_scan", wraps=app.start_scan) as scan, \
                mock.patch("disk_cleaner.scanners.scan", return_value=([], [])):
            app._busy(True, "Working")
            app.start_scan()
            self.assertTrue(app._pending_rescan)
            app._busy(False, "")
            self.pump(0.6)
        self.assertGreaterEqual(scan.call_count, 2)


if __name__ == "__main__":
    unittest.main()
