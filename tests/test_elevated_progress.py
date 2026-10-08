"""The elevated helper reports progress back so the window can show real status instead of a fixed message."""
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tickclean import actions, config
from tickclean.models import Finding, SAFE


class ElevatedProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = config.Path(tempfile.mkdtemp(prefix="dc_prog_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch.object(config, "DATA_DIR", self.tmp)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_elevated_helper_reports_progress_and_ignores_planted_files(self):
        token = "0123456789abcdef"
        plan, out = self.tmp / f"plan_{token}.json", self.tmp / f"result_{token}.json"
        plan.write_text(json.dumps({"ids": ["a"], "dry_run": True}), encoding="utf-8")
        planted = self.tmp / f"progress_{token}_1.json"
        planted.write_text("planted", encoding="utf-8")
        admin = Finding("a", "c", "admin item", "", 1, "r", SAFE, {"type": "builtin", "name": "dism_cleanup"},
                        needs_admin=True)
        with mock.patch("tickclean.scanners.scan", return_value=([admin], [])):
            actions.apply_from_files(str(plan), str(out))
        self.assertEqual(planted.read_text(encoding="utf-8"), "planted", "an existing file is never written through")
        notes = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(self.tmp.glob("progress_*_[2-9].json"))]
        self.assertEqual([n["text"] for n in notes], ["admin item"])  # note 1 was blocked by the planted file
        self.assertTrue(out.exists())

    def test_the_first_note_says_the_prompt_was_approved(self):
        token = "fedcba9876543210"
        plan, out = self.tmp / f"plan_{token}.json", self.tmp / f"result_{token}.json"
        plan.write_text(json.dumps({"ids": [], "dry_run": True}), encoding="utf-8")
        with mock.patch("tickclean.scanners.scan", return_value=([], [])):
            actions.apply_from_files(str(plan), str(out))
        first = json.loads((self.tmp / f"progress_{token}_1.json").read_text(encoding="utf-8"))
        self.assertIn("Approved", first["text"])

    def test_the_parent_relays_the_helpers_progress_while_waiting(self):
        seen = []
        tmp = self.tmp

        class FakeProc:
            calls = 0

            def communicate(self, timeout=None):
                FakeProc.calls += 1
                token = next(tmp.glob("plan_*.json")).stem.split("_", 1)[1]
                if FakeProc.calls == 1:
                    note = {"done": 0, "total": 1, "text": "Approved. Checking this PC as administrator…"}
                    (tmp / f"progress_{token}_1.json").write_text(json.dumps(note), encoding="utf-8")
                    raise subprocess.TimeoutExpired("ps", timeout)
                note = {"done": 1, "total": 1, "text": "Windows Update cache"}
                (tmp / f"progress_{token}_2.json").write_text(json.dumps(note), encoding="utf-8")
                result = [{"id": "a", "ok": True, "title": "t", "message": "m", "freed": 0}]
                (tmp / f"result_{token}.json").write_text(json.dumps(result), encoding="utf-8")
                return "ok", ""
        items = [{"id": "a", "title": "t", "action": {}}]
        with mock.patch.object(actions, "powershell_start", return_value=FakeProc()):
            res = actions.run_elevated(items, progress=lambda d, t, text: seen.append((d, t, text)))
        self.assertTrue(res[0]["ok"])
        self.assertIn("Approved", seen[0][2])
        self.assertEqual(seen[-1], (1, 1, "Windows Update cache"))
        self.assertEqual(list(tmp.glob("*.json")), [], "plan, result and progress files are cleaned up")

    def test_declined_prompt_is_reported_as_declined(self):
        class Declined:
            def communicate(self, timeout=None):
                return "declined", ""
        with mock.patch.object(actions, "powershell_start", return_value=Declined()):
            res = actions.run_elevated([{"id": "a", "title": "t", "action": {}}])
        self.assertFalse(res[0]["ok"])
        self.assertIn("declined", res[0]["message"])


if __name__ == "__main__":
    unittest.main()
