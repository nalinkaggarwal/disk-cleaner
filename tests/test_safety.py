"""Regression tests for problems found in the pre-release safety review."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tickclean import actions, config, scanners, util
from tickclean.models import Finding, SAFE

WIN = sys.platform == "win32"


def short_path(p):
    buf = ctypes.create_unicode_buffer(520)
    ctypes.windll.kernel32.GetShortPathNameW(p, buf, 520)
    return buf.value


@unittest.skipUnless(WIN, "Windows only")
class JunctionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dc_safety_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_junction_inside_a_tree_is_removed_but_never_followed(self):
        victim = os.path.join(self.tmp, "victim")
        cache = os.path.join(self.tmp, "cache")
        os.makedirs(victim)
        os.makedirs(cache)
        keep = os.path.join(victim, "precious.txt")
        with open(keep, "w") as fh:
            fh.write("do not delete")
        with open(os.path.join(cache, "junk.bin"), "w") as fh:
            fh.write("x")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(cache, "link"), victim],
                           capture_output=True, creationflags=util.CREATE_NO_WINDOW)
        if r.returncode != 0:
            self.skipTest("cannot create a junction here")
        res = actions.apply_plan([{"id": "t", "title": "t", "action": {
            "type": "delete_path", "paths": [cache], "contents_only": True}}])
        self.assertTrue(res[0]["ok"], res)
        self.assertTrue(os.path.exists(keep), "files behind a junction must survive")
        self.assertFalse(os.path.exists(os.path.join(cache, "junk.bin")))
        self.assertFalse(os.path.exists(os.path.join(cache, "link")), "the link itself is removed")


@unittest.skipUnless(WIN, "Windows only")
class GuardBypassTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config()
        self.home = os.path.expanduser("~")

    def test_short_8_3_names_cannot_hide_a_protected_folder(self):
        for p in (self.home, os.environ.get("ProgramFiles"), os.path.join(self.home, "Pictures")):
            short = short_path(p)
            self.assertTrue(actions.guard(short, self.cfg), short)

    def test_device_and_network_paths_are_refused(self):
        windir = os.environ["SystemRoot"]
        self.assertTrue(actions.guard("\\\\?\\" + windir, self.cfg))
        self.assertTrue(actions.guard("\\\\?\\" + os.path.join(windir, "System32"), self.cfg))
        self.assertTrue(actions.guard("\\\\localhost\\c$\\Windows\\System32", self.cfg))

    def test_relative_paths_are_refused(self):
        self.assertTrue(actions.guard("NVIDIA Corporation\\Downloader", self.cfg))
        self.assertTrue(actions.guard("pip", self.cfg))

    def test_documents_onedrive_and_credentials_are_off_limits_below_the_top_level_too(self):
        for sub in ("Documents\\MyThesis", "OneDrive\\Work", ".ssh", ".aws\\creds"):
            self.assertTrue(actions.guard(os.path.join(self.home, sub), self.cfg), sub)
        self.assertTrue(actions.guard(os.path.join(os.environ["ProgramFiles"], "Windows Defender"), self.cfg))

    def test_another_users_profile_is_refused(self):
        other = os.path.join(os.path.dirname(self.home), "SomeoneElse", "AppData", "Local", "Temp")
        self.assertTrue(actions.guard(other, self.cfg))

    def test_normal_cache_locations_are_still_allowed(self):
        local = os.environ["LOCALAPPDATA"]
        self.assertIsNone(actions.guard(os.path.join(local, "npm-cache"), self.cfg))
        self.assertIsNone(actions.guard(os.path.join(local, "Temp"), self.cfg))

    def test_a_path_through_a_junction_is_judged_by_where_it_really_leads(self):
        tmp = tempfile.mkdtemp(prefix="dc_guard_")
        self.addCleanup(shutil.rmtree, tmp, True)
        link = os.path.join(tmp, "sneaky")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, os.path.join(self.home, "Documents")],
                           capture_output=True, creationflags=util.CREATE_NO_WINDOW)
        if r.returncode != 0:
            self.skipTest("cannot create a junction here")
        self.assertTrue(actions.guard(os.path.join(link, "anything"), self.cfg))


class ElevatedHelperTests(unittest.TestCase):
    def test_elevated_child_acts_only_on_its_own_admin_findings(self):
        tmp = tempfile.mkdtemp(prefix="dc_elev_")
        self.addCleanup(shutil.rmtree, tmp, True)
        victim = os.path.join(tmp, "victim")
        os.makedirs(victim)
        with open(os.path.join(victim, "f.txt"), "w") as fh:
            fh.write("x")
        admin = Finding("win:ok", "Windows", "admin item", "", 0, "r", SAFE,
                        {"type": "builtin", "name": "no_such_builtin"}, needs_admin=True)
        plain = Finding("user:item", "Temp", "user item", victim, 1, "r", SAFE,
                        {"type": "delete_path", "paths": [victim]}, needs_admin=False)
        plan, out = os.path.join(tmp, "plan.json"), os.path.join(tmp, "result.json")
        # a tampered plan: asks for a non-admin finding and for one nobody offered
        with open(plan, "w") as fh:
            json.dump({"ids": ["user:item", "forged:id", "win:ok"], "dry_run": False}, fh)
        with mock.patch.object(scanners, "scan", return_value=([admin, plain], [])):
            actions.apply_from_files(plan, out)
        with open(out) as fh:
            res = {r["id"]: r for r in json.load(fh)}
        self.assertFalse(res["user:item"]["ok"])
        self.assertFalse(res["forged:id"]["ok"])
        self.assertTrue(os.path.exists(victim), "a non-admin finding must not be executed by the elevated child")
        self.assertIn("win:ok", res)

    def test_result_file_is_never_written_through_an_existing_file(self):
        tmp = tempfile.mkdtemp(prefix="dc_elev_")
        self.addCleanup(shutil.rmtree, tmp, True)
        plan, out = os.path.join(tmp, "plan.json"), os.path.join(tmp, "result.json")
        with open(plan, "w") as fh:
            json.dump({"ids": [], "dry_run": True}, fh)
        with open(out, "w") as fh:
            fh.write("planted")
        with mock.patch.object(scanners, "scan", return_value=([], [])):
            with self.assertRaises(FileExistsError):
                actions.apply_from_files(plan, out)
        with open(out) as fh:
            self.assertEqual(fh.read(), "planted")


class StaleProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dc_proj_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_walk_cut_short_is_treated_as_recent_activity(self):
        for i in range(10):
            d = os.path.join(self.tmp, f"d{i}")
            os.makedirs(d)
            f = os.path.join(d, "f.txt")
            with open(f, "w") as fh:
                fh.write("x")
            os.utime(f, (1_000_000, 1_000_000))
        self.assertGreater(util.newest_mtime(self.tmp, max_files=3), time.time() - 60)
        self.assertLess(util.newest_mtime(self.tmp, max_files=100), 2_000_000)

    def test_nested_build_folder_is_judged_by_the_whole_project(self):
        root = os.path.join(self.tmp, "code")
        nested = os.path.join(root, "app", "android", "app")
        os.makedirs(nested)
        self.assertEqual(os.path.normcase(scanners._project_dir(root, nested)), os.path.normcase(os.path.join(root, "app")))
        os.makedirs(os.path.join(root, "app", "android", ".git"))
        self.assertEqual(os.path.normcase(scanners._project_dir(root, nested)),
                         os.path.normcase(os.path.join(root, "app", "android")))


class QuotingTests(unittest.TestCase):
    def test_typographic_quotes_are_doubled_for_powershell(self):
        for q in "'‘’‚‛":
            self.assertEqual(util.ps_quote(f"a{q}b"), f"a{q}{q}b")
        self.assertEqual(util.ps_quote("plain"), "plain")


if __name__ == "__main__":
    unittest.main()
