"""Test database isolation without requiring a Z01X installation."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evaluate


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / "campaign-base"
        (self.base / "fdb").mkdir(parents=True)
        (self.base / "fdb" / "results").write_text("NA")
        self.calls = []

    def fake_tool(self, command, cwd, log):
        """Model FCM reads and writes; use real cp for the database files."""
        self.calls.append((command, cwd))
        if command[0] == "cp":
            subprocess.run(command, check=True)
            return
        self.assertEqual(command[0], "vc_fcm")
        database = cwd / "fdb"
        if "-fdb_mgmt_only" in command:
            self.assertIn("-tcl_script", command)
            self.assertNotIn("-tcl", command)
            script = Path(command[-1]).read_text()
            self.assertIn(str(database.resolve()), script)
            self.assertIn("fcm::stop_server", script)
            (database / "postmaster.pid").unlink()
            return
        self.assertEqual(command[command.index("-fdb_path") + 1], str(database.resolve()))
        self.assertIn("-stop_server_on_exit", command)
        if Path(command[-1]).name == "check-unrun.tcl":
            na = 4 if (database / "results").read_text() == "NA" else 0
            (cwd / "unrun-summary.rpt").write_text(
                f"# Testable Faults: 4\n# Not Attempted NA {na}\n")
        else:
            (database / "results").write_text("DD")
        # Model a server started earlier, which the exit flag does not stop.
        (database / "postmaster.pid").write_text("123")

    def test_two_copies_start_unused_and_do_not_change_base(self):
        with patch.object(evaluate, "CAMPAIGN_BASE", self.base), \
             patch.object(evaluate, "campaign_is_ready", return_value=True), \
             patch.object(evaluate, "run", side_effect=self.fake_tool):
            for name in ("first", "second"):
                work = self.root / name
                work.mkdir()
                evaluate.copy_campaign(work)
                self.assertEqual((work / "fdb/results").read_text(), "NA")
                evaluate.run_fcm(work, work / "fsim.tcl", work / "fcm.log")
                self.assertFalse((work / "fdb/postmaster.pid").exists())
                self.assertEqual((self.base / "fdb/results").read_text(), "NA")
            evaluate.verify_unused_database(self.base)
        self.assertFalse(any(command[0] == "vc_fcc" for command, _ in self.calls))

    def test_dirty_base_is_preserved_and_not_copied(self):
        (self.base / "fdb/results").write_text("DD")
        work = self.root / "candidate"
        work.mkdir()
        with patch.object(evaluate, "CAMPAIGN_BASE", self.base), \
             patch.object(evaluate, "campaign_is_ready", return_value=True), \
             patch.object(evaluate, "run", side_effect=self.fake_tool):
            with self.assertRaisesRegex(RuntimeError, "database contains results"):
                evaluate.copy_campaign(work)
        self.assertFalse((work / "fdb").exists())
        self.assertTrue((self.base / "fdb/results").exists())

    def test_server_must_stop_before_cleanup(self):
        pid = self.base / "fdb/postmaster.pid"
        pid.write_text("123")
        with patch.object(evaluate, "run"):
            with self.assertRaisesRegex(RuntimeError, "still has a PID file"):
                evaluate.remove_run_data(self.base)
        self.assertTrue(pid.exists())

    def test_inherited_database_connection_is_removed(self):
        with patch.dict(evaluate.os.environ, {"SNPS_FDB_SERVER": "other:1234"}), \
             patch.object(evaluate.subprocess, "run") as process:
            process.return_value.returncode = 0
            evaluate.run(["vc_fcm", "-help"], self.root, self.root / "help.log")
            self.assertNotIn("SNPS_FDB_SERVER", process.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
