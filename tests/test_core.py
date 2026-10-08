import datetime
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest

from disk_cleaner import actions, config, scanners, software

WIN = sys.platform == "win32"


@unittest.skipUnless(WIN, "Windows only")
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config()
        self.home = os.path.expanduser("~")

    def test_refuses_drive_root(self):
        self.assertTrue(actions.guard("C:\\", self.cfg))

    def test_refuses_profile_and_windows(self):
        self.assertTrue(actions.guard(self.home, self.cfg))
        self.assertTrue(actions.guard(os.environ["SystemRoot"], self.cfg))
        self.assertTrue(actions.guard(os.path.join(os.environ["SystemRoot"], "System32"), self.cfg))

    def test_refuses_personal_media(self):
        self.assertTrue(actions.guard(os.path.join(self.home, "Pictures"), self.cfg))
        self.assertTrue(actions.guard(os.path.join(self.home, "Videos", "Captures", "x"), self.cfg))

    def test_personal_allowed_only_with_flag_and_only_inside_folder(self):
        item = os.path.join(self.home, "Videos", "Captures")
        self.assertTrue(actions.guard(item, self.cfg))
        self.assertIsNone(actions.guard(item, self.cfg, allow_personal=True))
        self.assertTrue(actions.guard(os.path.join(self.home, "Videos"), self.cfg, allow_personal=True))
        self.assertTrue(actions.guard(self.home, self.cfg, allow_personal=True))
        self.assertTrue(actions.guard(os.path.join(self.home, ".gradle", "caches"), self.cfg, allow_personal=True))
        self.assertTrue(actions.guard(os.environ["SystemRoot"], self.cfg, allow_personal=True))

    def test_refuses_parent_of_protected_folder(self):
        self.assertTrue(actions.guard(os.path.dirname(self.home), self.cfg))

    def test_refuses_downloads_folder_itself_but_not_a_file_in_it(self):
        self.assertTrue(actions.guard(os.path.join(self.home, "Downloads"), self.cfg))
        self.assertIsNone(actions.guard(os.path.join(self.home, "Downloads", "old-setup.exe"), self.cfg))

    def test_allows_windows_update_download_cache(self):
        p = os.path.join(os.environ["SystemRoot"], "SoftwareDistribution", "Download")
        self.assertIsNone(actions.guard(p, self.cfg))

    def test_allows_ordinary_cache(self):
        self.assertIsNone(actions.guard(os.path.join(self.home, ".gradle", "caches"), self.cfg))


@unittest.skipUnless(WIN, "Windows only")
class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dc_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _make(self, rel, size=10, age_days=0, readonly=False):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"x" * size)
        if age_days:
            t = time.time() - age_days * 86400
            os.utime(p, (t, t))
        if readonly:
            os.chmod(p, stat.S_IREAD)
        return p

    def test_delete_tree_handles_readonly_and_keeps_root(self):
        self._make("a/b/ro.txt", readonly=True)
        self._make("a/c.txt")
        removed, fails = actions._delete_tree(self.tmp)
        self.assertEqual(fails, 0)
        self.assertEqual(removed, 20)
        self.assertTrue(os.path.isdir(self.tmp))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_older_than_only_removes_old_files(self):
        old = self._make("old.bin", age_days=40)
        new = self._make("new.bin")
        actions._delete_tree(self.tmp, older_than_days=30)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_dry_run_changes_nothing(self):
        p = self._make("keep/me.txt")
        res = actions.apply_plan([{"id": "t", "title": "t",
                                   "action": {"type": "delete_path", "paths": [os.path.join(self.tmp, "keep")]}}],
                                 dry_run=True)
        self.assertTrue(res[0]["ok"])
        self.assertTrue(os.path.exists(p))

    def test_plan_cannot_delete_guarded_path(self):
        res = actions.apply_plan([{"id": "t", "title": "t",
                                   "action": {"type": "delete_path", "paths": [os.path.expanduser("~")]}}])
        self.assertFalse(res[0]["ok"])
        self.assertTrue(os.path.isdir(os.path.expanduser("~")))

    def test_personal_item_needs_second_confirmation(self):
        base = tempfile.mkdtemp(prefix="dc_media_")
        self.addCleanup(shutil.rmtree, base, True)
        root = os.path.join(base, "wedding")
        os.makedirs(root)
        with open(os.path.join(root, "clip.mp4"), "wb") as fh:
            fh.write(b"x" * 10)
        cfg = dict(config.load_config(), media_roots=[base])
        action = {"type": "delete_path", "paths": [root], "personal": True}
        item = {"id": "m", "title": "m", "action": action}
        res = actions.apply_plan([item], cfg=cfg)
        self.assertFalse(res[0]["ok"])
        self.assertTrue(os.path.exists(root))
        res = actions.apply_plan([dict(item, personal_confirmed=True)], dry_run=True, cfg=cfg)
        self.assertTrue(os.path.exists(root))
        res = actions.apply_plan([dict(item, personal_confirmed=True)], cfg=cfg)
        self.assertTrue(res[0]["ok"])
        self.assertFalse(os.path.exists(root))

    def test_personal_flag_on_non_personal_path_is_still_refused(self):
        p = self._make("plain/x.txt")
        item = {"id": "m", "title": "m", "personal_confirmed": True,
                "action": {"type": "delete_path", "paths": [os.path.join(self.tmp, "plain")], "personal": True}}
        res = actions.apply_plan([item])
        self.assertFalse(res[0]["ok"])
        self.assertTrue(os.path.exists(p))

    def test_unknown_action_type_is_rejected(self):
        res = actions.apply_plan([{"id": "t", "title": "t", "action": {"type": "run", "cmd": "calc"}}])
        self.assertFalse(res[0]["ok"])

    def test_unknown_builtin_is_rejected(self):
        res = actions.apply_plan([{"id": "t", "title": "t", "action": {"type": "builtin", "name": "format_c"}}])
        self.assertFalse(res[0]["ok"])


@unittest.skipUnless(WIN, "Windows only")
class StaleProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dc_proj_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _project(self, name, age_days):
        proj = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(proj, "node_modules", "pkg"))
        files = [os.path.join(proj, "package.json"), os.path.join(proj, "node_modules", "pkg", "index.js")]
        for f in files:
            with open(f, "w") as fh:
                fh.write("x" * 1000)
        t = time.time() - age_days * 86400
        for f in files + [proj, os.path.join(proj, "node_modules"), os.path.join(proj, "node_modules", "pkg")]:
            os.utime(f, (t, t))
        return proj

    def test_only_untouched_projects_are_suggested(self):
        self._project("old_app", 200)
        self._project("fresh_app", 1)
        cfg = dict(config.load_config(), project_roots=[self.tmp], stale_project_days=60, min_item_mb=0)
        found = scanners.scan_projects(scanners.Ctx(cfg))
        titles = [f.title for f in found]
        self.assertEqual(titles, ["old_app\\node_modules"])
        self.assertIn("hasn't been edited", found[0].reason)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.rules = software.load_rules()
        self.today = datetime.date(2026, 10, 7)

    def c(self, name):
        return software.classify({"name": name}, [], self.rules, self.today)

    def test_adware_rule(self):
        r = self.c("RelevantKnowledge")
        self.assertIsNotNone(r)
        self.assertEqual(r[1], "REVIEW")

    def test_oem_utility_needs_review(self):
        r = self.c("HP CoolSense 2.22.2")
        self.assertEqual(r[1], "REVIEW")

    def test_dotnet_end_of_life(self):
        self.assertIsNotNone(self.c("Microsoft .NET Core SDK 3.1.426 (x64)"))

    def test_current_dotnet_is_left_alone(self):
        self.assertIsNone(self.c("Microsoft .NET SDK 99.0.100 (x64)"))

    def test_python2(self):
        self.assertIsNotNone(self.c("Python 2.7.15"))

    def test_current_python_is_left_alone(self):
        self.assertIsNone(self.c("Python 3.14.0 (64-bit)"))

    def test_java_non_lts(self):
        self.assertIsNotNone(self.c("Java SE Development Kit 14.0.1 (64-bit)"))

    def test_java_lts_left_alone(self):
        self.assertIsNone(self.c("Eclipse Temurin JDK with Hotspot 17.0.9"))

    def test_windows_sdk_is_not_java(self):
        self.assertIsNone(self.c("Windows Software Development Kit - Windows 10.0.19041"))

    def test_ordinary_app_left_alone(self):
        self.assertIsNone(self.c("Visual Studio Code"))


if __name__ == "__main__":
    unittest.main()
