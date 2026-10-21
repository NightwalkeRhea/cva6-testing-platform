"""Check parallel scoring and probes without EDA tools."""
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import evaluate


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_probe_reads_cycles_and_rejects_failure_but_not_tool_errors(self):
        cases = [
            ("*** PASSED *** Completed after 123456 simulation cycles", 0, 123456),
            ("*** FAILED *** (timeout) after 1000001 simulation cycles", 1, None),
            ("*** FAILED *** exit code 1 after 50 simulation cycles", 1, None),
            ("license unavailable", 1, RuntimeError),
            ("", 0, RuntimeError),
            ("*** PASSED *** Completed after 123 simulation cycles", 1, RuntimeError),
        ]
        for text, status, expected in cases:
            with self.subTest(text=text, status=status):
                def simulate(command, **kwargs):
                    self.assertIn(f"+max-cycles={evaluate.config.PROBE_MAX_CYCLES}", command)
                    kwargs["stdout"].write(text)
                    return subprocess.CompletedProcess(command, status)

                with patch.object(evaluate, "raw_body_address", return_value=0x80000000), \
                     patch.object(evaluate.subprocess, "run", side_effect=simulate):
                    if expected is RuntimeError:
                        with self.assertRaises(RuntimeError):
                            evaluate.probe(self.root / "candidate.riscv", self.root)
                    else:
                        self.assertEqual(evaluate.probe(self.root / "candidate.riscv", self.root), expected)

    def test_batch_runs_sixteen_programs_and_keeps_fitness_order(self):
        barrier = threading.Barrier(16, timeout=5)
        sources = [str(self.root / f"program{i}") for i in range(16)]

        def score(source, **kwargs):
            self.assertTrue(kwargs["full"])
            self.assertIsNone(kwargs["selected_ids"])
            barrier.wait()
            return float(sources.index(str(source)))

        output = self.root / "fitness.output"
        with patch.object(sys, "argv", ["evaluate.py", *sources]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "score", side_effect=score):
            self.assertEqual(evaluate.main(), 0)
        self.assertEqual(output.read_text().splitlines(), [f"{i:.6f}" for i in range(16)])

    def test_duplicate_programs_run_faults_once_with_measured_cap(self):
        work = self.root / "same"

        def prepare(*args, **kwargs):
            binary = work / "candidate.riscv"
            binary.touch()
            return work, work / "fsim.tcl"

        def fcm(*args):
            (work / "summary.rpt").write_text(
                "# Detected DG 1\n# Testable Faults: 2\n# Not Attempted NA 0\n")
            (work / "faults.rpt").touch()

        with patch.object(evaluate, "RUNS", self.root), \
             patch.object(evaluate, "evaluation_digest", return_value="same"), \
             patch.object(evaluate, "prepare", side_effect=prepare) as build, \
             patch.object(evaluate, "probe", return_value=100000) as probe, \
             patch.object(evaluate, "raw_body_address", return_value=0x80000000), \
             patch.object(evaluate, "copy_campaign"), \
             patch.object(evaluate, "run_fcm", side_effect=fcm) as campaign, \
             patch.object(evaluate, "require_injection_marker",
                          side_effect=lambda wd: (wd / "injection-markers.txt").touch()), \
             patch.object(evaluate, "remove_run_data"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                scores = list(pool.map(lambda _: evaluate.score(self.root / "seed.S",
                                       full=False, selected_ids=[1, 2]), range(2)))
        self.assertEqual(scores, [50.0, 50.0])
        self.assertEqual(build.call_count, 1)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(campaign.call_count, 1)
        self.assertIn("+max-cycles=150000", (work / "fsim.tcl").read_text())
        self.assertIn("-global_max_jobs 1", (work / "fsim.tcl").read_text())

    def test_failed_probe_skips_database_and_faults(self):
        work = self.root / "same"
        with patch.object(evaluate, "RUNS", self.root), \
             patch.object(evaluate, "evaluation_digest", return_value="same"), \
             patch.object(evaluate, "prepare", return_value=(work, work / "fsim.tcl")), \
             patch.object(evaluate, "probe", return_value=None) as probe, \
             patch.object(evaluate, "copy_campaign") as copy, \
             patch.object(evaluate, "run_fcm") as campaign:
            for _ in range(2):
                self.assertEqual(evaluate.score(self.root / "seed.S", full=False,
                                                selected_ids=[1]), 0.0)
        self.assertEqual(probe.call_count, 1)
        copy.assert_not_called()
        campaign.assert_not_called()

    def test_invalid_assembly_gets_zero_without_probe_or_database(self):
        with patch.object(evaluate, "RUNS", self.root), \
             patch.object(evaluate, "evaluation_digest", return_value="invalid"), \
             patch.object(evaluate, "prepare", side_effect=evaluate.InvalidProgram("bad opcode")), \
             patch.object(evaluate, "probe") as probe, \
             patch.object(evaluate, "copy_campaign") as copy:
            self.assertEqual(evaluate.score(self.root / "bad.S", full=False,
                                            selected_ids=[1]), 0.0)
        probe.assert_not_called()
        copy.assert_not_called()
        self.assertTrue((self.root / "invalid/rejected.txt").is_file())

    def test_compiler_setup_errors_are_not_scored_as_bad_programs(self):
        def failure(command, work, log):
            log.write_text("cannot find htif_nano.specs")
            raise RuntimeError("compiler failed")
        with patch.object(evaluate, "run", side_effect=failure):
            with self.assertRaises(RuntimeError) as error:
                evaluate.compile_program(self.root / "seed.S", self.root)
        self.assertNotIsInstance(error.exception, evaluate.InvalidProgram)

    def test_default_score_uses_the_full_campaign_without_a_csv(self):
        with patch.object(evaluate, "RUNS", self.root), \
             patch.object(evaluate, "evaluation_digest", return_value="complete") as digest, \
             patch.object(evaluate, "score_locked", return_value=12.5) as score:
            self.assertEqual(evaluate.score(self.root / "seed.S"), 12.5)
        digest.assert_called_once_with(self.root / "seed.S", True, None)
        self.assertEqual(score.call_args.args[1:3], (True, None))
        self.assertEqual(score.call_args.args[4], self.root / "full/complete")

    def test_full_tcl_has_no_filter_for_fault_ids(self):
        tcl = self.root / "fsim.tcl"
        with patch.object(evaluate, "raw_body_address", return_value=0x80000290):
            evaluate.write_tcl(tcl, self.root / "candidate.riscv", None,
                               self.root / "summary.rpt", 0x80000290)
        self.assertIn("fsim -verbose -no_coats", tcl.read_text())
        self.assertNotIn("-fids", tcl.read_text())

    def test_full_fitness_counts_only_definite_detections(self):
        report = self.root / "summary.rpt"
        report.write_text("# Number of Faults: 37184\n# Detected DG 6967\n"
                          "# Potential PG 5\n# Untestable UG 7255\n")
        self.assertAlmostEqual(evaluate.parse_full_coverage(report), 100 * 6967 / 37184)
        report.write_text(report.read_text() + "# Not Attempted NA 1\n")
        with self.assertRaisesRegex(ValueError, "unattempted"):
            evaluate.parse_full_coverage(report)

    def test_validation_still_selects_four_faults(self):
        output = self.root / "fitness.output"
        with patch.object(sys, "argv", ["evaluate.py", "--validate-faults", "4", "seed.S"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "validation_fault_ids", return_value=[3, 4, 5, 6]), \
             patch.object(evaluate, "score", return_value=100.0) as score:
            self.assertEqual(evaluate.main(), 0)
        score.assert_called_once_with(Path("seed.S"), full=False,
                                      selected_ids=[3, 4, 5, 6], result_group="validate")

    def test_assembler_syntax_errors_are_recognized(self):
        def failure(command, work, log):
            log.write_text("bad.S: Assembler messages:\nbad.S:4: Error: unrecognized opcode")
            raise RuntimeError("compiler failed")
        with patch.object(evaluate, "run", side_effect=failure):
            with self.assertRaises(evaluate.InvalidProgram):
                evaluate.compile_program(self.root / "bad.S", self.root)


if __name__ == "__main__":
    unittest.main()
