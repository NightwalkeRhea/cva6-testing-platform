"""Check parallel scoring and probes without EDA tools."""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import evaluate
import replay
import smoke
from select_faults import read_faults


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # The fault count is cached per process; one test's fault list must not leak.
        evaluate.testable_faults.cache_clear()
        self.addCleanup(evaluate.testable_faults.cache_clear)

    def test_elapsed_times_and_failures_are_saved(self):
        with patch.object(evaluate.time, "perf_counter", side_effect=[10.0, 12.5]):
            with evaluate.measure(self.root, "compile"):
                pass
        with self.assertRaisesRegex(RuntimeError, "failed"):
            with evaluate.measure(self.root, "fcm"):
                raise RuntimeError("failed")
        entries = [json.loads(line) for line in (self.root / "timings.jsonl").read_text().splitlines()]
        self.assertEqual(entries[0]["elapsed_seconds"], 2.5)
        self.assertEqual(entries[0]["status"], "ok")
        self.assertEqual(entries[1]["status"], "error")
        self.assertIn("host", entries[0])

    def test_task_times_survive_task_directory_removal(self):
        tasks = self.root / "fcm.dir"
        tasks.mkdir()
        (tasks / "summary.csv").write_text(
            "Section 1\n, task, testcase, num faults, elapsed time, CPU time, details\n"
            ", ID, , , [s], [s],\n,1,raw_candidate,4,7,6.5,DD:4\n")
        evaluate.record_task_timings(self.root)
        # A later FCM launch must not record the same summary again.
        evaluate.record_task_timings(self.root, since=(tasks / "summary.csv").stat().st_mtime + 1)
        shutil.rmtree(tasks)
        entry = json.loads((self.root / "timings.jsonl").read_text())
        self.assertEqual(entry["elapsed_seconds"], 7)
        self.assertEqual(entry["cpu_seconds"], 6.5)
        self.assertEqual(entry["faults"], "4")
        self.assertEqual(entry["testcase"], "raw_candidate")

    def test_command_returncode_is_saved_on_failure(self):
        with patch.object(evaluate.subprocess, "run") as process:
            process.return_value.returncode = 3
            with self.assertRaisesRegex(RuntimeError, "command failed"):
                evaluate.run(["vc_fcm"], self.root, self.root / "fcm.log")
        entry = json.loads((self.root / "timings.jsonl").read_text())
        self.assertEqual(entry["returncode"], 3)
        self.assertEqual(entry["status"], "error")

    def test_cached_candidate_is_labelled_for_benchmarks(self):
        work = self.root / "full" / "cached"
        work.mkdir(parents=True)
        for name in ("summary.rpt", "faults.rpt", "injection-markers.txt"):
            (work / name).touch()
        with patch.object(evaluate, "RUNS", self.root), \
             patch.object(evaluate, "evaluation_digest", return_value="cached"), \
             patch.object(evaluate, "score_locked", return_value=18.74):
            self.assertEqual(evaluate.score(Path("seed.S")), 18.74)
        entries = [json.loads(line) for line in (work / "timings.jsonl").read_text().splitlines()]
        candidate = next(entry for entry in entries if entry["stage"] == "candidate")
        self.assertTrue(candidate["cache_hit"])
        self.assertFalse(candidate["rejected"])
        self.assertEqual(candidate["coverage"], 18.74)

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

    def test_lone_seed_gets_all_jobs_and_a_full_batch_one_each(self):
        for count, jobs in ((1, 16), (5, 3), (16, 1), (20, 1)):
            with self.subTest(programs=count), \
                 patch.object(sys, "argv", ["evaluate.py", *[f"p{i}" for i in range(count)]]), \
                 patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(self.root / "f")}), \
                 patch.object(evaluate, "score", return_value=1.0) as score:
                evaluate.main()
                self.assertEqual({call.kwargs["jobs"] for call in score.call_args_list}, {jobs})

    def test_clones_do_not_take_a_share_of_the_jobs(self):
        # 16 programs, but only 4 distinct ones: each gets 16 // 4 = 4 jobs.
        sources = []
        for index in range(16):
            source = self.root / f"individual{index}"
            source.write_text(f"program {index % 4}\n")
            sources.append(str(source))
        with patch.object(sys, "argv", ["evaluate.py", *sources]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(self.root / "f")}), \
             patch.object(evaluate, "score", return_value=1.0) as score:
            evaluate.main()
        self.assertEqual({call.kwargs["jobs"] for call in score.call_args_list}, {4})

    def test_tcl_uses_the_jobs_it_is_given(self):
        tcl = self.root / "fsim.tcl"
        # About two tasks per job: 29,929 faults over 16 jobs gives 936 per task;
        # one job keeps Z01X's default size, and replays of chosen IDs too.
        for ids, jobs, size in ((None, 16, 936), (None, 1, 2046), ([7], 16, 2046)):
            with self.subTest(jobs=jobs, ids=ids), \
                 patch.object(evaluate, "raw_body_address", return_value=0x80000290), \
                 patch.object(evaluate, "testable_faults", return_value=29929):
                evaluate.write_tcl(tcl, self.root / "candidate.riscv", ids,
                                   self.root / "summary.rpt", 0x80000290, jobs=jobs)
                text = tcl.read_text()
                self.assertIn(f"set_config -global_max_jobs {jobs}", text)
                self.assertIn(f"set_config -max_faults_per_fsim_task {size}", text)

    def test_batch_runs_sixteen_programs_and_keeps_fitness_order(self):
        barrier = threading.Barrier(16, timeout=5)
        sources = [str(self.root / f"program{i}") for i in range(16)]

        def score(source, **kwargs):
            self.assertTrue(kwargs["full"])
            self.assertIsNone(kwargs["selected_ids"])
            self.assertEqual(kwargs["jobs"], 1)
            barrier.wait()
            return float(sources.index(str(source)))

        output = self.root / "fitness.output"
        with patch.object(sys, "argv", ["evaluate.py", *sources]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "score", side_effect=score):
            self.assertEqual(evaluate.main(), 0)
        self.assertEqual(output.read_text().splitlines(), [f"{i:.12f} 0" for i in range(16)])

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
             patch.object(evaluate, "write_profile"), \
             patch.object(evaluate, "raw_body_address", return_value=0x80000000), \
             patch.object(evaluate, "copy_campaign"), \
             patch.object(evaluate, "run_fcm", side_effect=fcm) as campaign, \
             patch.object(evaluate, "require_unused"), \
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
        with patch.object(evaluate, "write_linker_script", return_value=self.root / "candidate.ld"), \
             patch.object(evaluate, "run", side_effect=failure):
            with self.assertRaises(RuntimeError) as error:
                evaluate.compile_program(self.root / "seed.S", self.root)
        self.assertNotIsInstance(error.exception, evaluate.InvalidProgram)

    def test_htif_must_be_pinned_and_clear_of_the_program(self):
        def nm(tohost, stack_start):
            # nm output in the format of the real candidate on cattivo.
            text = (f"{tohost:016x} D tohost\n{tohost + 8:016x} D fromhost\n0000000080002240 B _end\n"
                    f"{stack_start:016x} B __stack_start\n0000000000008000 A __stack_size\n")
            return subprocess.CompletedProcess([], 0, text, "")

        with patch.object(evaluate.subprocess, "run", return_value=nm(0x80100000, 0x80023000)):
            evaluate.check_htif(self.root / "candidate.riscv")
        # The link flag was ignored: tohost stayed where the linker script put it.
        with patch.object(evaluate.subprocess, "run", return_value=nm(0x80001e00, 0x80023000)):
            with self.assertRaisesRegex(RuntimeError, "tohost is not at"):
                evaluate.check_htif(self.root / "candidate.riscv")
        # The stack would run into the uncached page.
        with patch.object(evaluate.subprocess, "run", return_value=nm(0x80100000, 0x800fc000)):
            with self.assertRaises(evaluate.InvalidProgram):
                evaluate.check_htif(self.root / "candidate.riscv")

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
        with patch.object(evaluate, "raw_body_address", return_value=0x80000290), \
             patch.object(evaluate, "testable_faults", return_value=29929):
            evaluate.write_tcl(tcl, self.root / "candidate.riscv", None,
                               self.root / "summary.rpt", 0x80000290)
        text = tcl.read_text()
        # COATS stays on, so missed faults get NC/NO labels.
        self.assertIn("fsim -verbose\n", text)
        self.assertNotIn("-no_coats", text)
        self.assertNotIn("-fids", text)
        self.assertNotIn("trace_file", text)
        # The unused-database counts come first, in the same FCM launch.
        self.assertLess(text.index("unrun-summary.rpt"), text.index("fsim -verbose"))

    def test_validation_tcl_disables_coats_without_enabling_traces(self):
        tcl = self.root / "fsim.tcl"
        with patch.object(evaluate, "raw_body_address", return_value=0x80000290):
            evaluate.write_tcl(tcl, self.root / "candidate.riscv", [3, 4, 5, 6],
                               self.root / "summary.rpt", 0x80000290)
        text = tcl.read_text()
        self.assertIn("fsim -verbose -no_coats -fids {3 4 5 6}", text)
        self.assertNotIn("trace_file", text)
        self.assertIn("unrun-summary.rpt", text)

    def test_replay_tcl_traces_one_fault_without_coats(self):
        tcl = self.root / "fsim.tcl"
        with patch.object(evaluate, "raw_body_address", return_value=0x80000290):
            evaluate.write_tcl(tcl, self.root / "candidate.riscv", [7], self.root / "summary.rpt",
                               0x80000290, trace_dir=self.root, check_unused=False)
        text = tcl.read_text()
        self.assertIn("fsim -verbose -no_coats -fids {7}", text)
        self.assertIn(f"+dependency_trace_file={self.root / 'dependency.csv'}", text)
        self.assertNotIn("unrun-summary.rpt", text)

    def test_full_fitness_counts_only_definite_detections(self):
        report = self.root / "summary.rpt"
        report.write_text("# Number of Faults: 37184\n# Detected DG 6967\n"
                          "# Potential PG 5\n# Untestable UG 7255\n")
        self.assertAlmostEqual(evaluate.parse_full_coverage(report), 100 * 6967 / 37184)
        report.write_text(report.read_text() + "# Not Attempted NA 1\n")
        with self.assertRaisesRegex(ValueError, "unattempted"):
            evaluate.parse_full_coverage(report)

    def test_failed_batch_leaves_no_old_scores_for_microgp(self):
        output = self.root / "fitness.output"
        output.write_text("100.000000000000 0\n")
        with patch.object(sys, "argv", ["evaluate.py", "seed.S"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "score", side_effect=RuntimeError("command failed with 127")):
            with self.assertRaises(RuntimeError):
                evaluate.main()
        self.assertFalse(output.exists())
        # The same holds when the reference itself fails to load.
        output.write_text("100.000000000000 0\n")
        with patch.object(sys, "argv", ["evaluate.py", "--require-reference", "seed.S"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "load_reference", side_effect=ValueError("reference mismatch")):
            with self.assertRaises(ValueError):
                evaluate.main()
        self.assertFalse(output.exists())

    @unittest.skipUnless(shutil.which("tclsh"), "tclsh is not installed")
    def test_unused_check_stops_tcl_before_fsim(self):
        # Run the generated Tcl with a stub "report" that writes chosen counts.
        report = self.root / "unrun-summary.rpt"
        for na, code in ((4, 0), (3, 1)):
            script = self.root / "check.tcl"
            script.write_text(
                f'proc report {{args}} {{ set f [open {{{report}}} w]; '
                f'puts $f "# Testable Faults: 4\\n#   Not Attempted NA {na}"; close $f }}\n'
                + evaluate.unused_report_command(report) + 'puts "fsim would start"\n')
            result = subprocess.run(["tclsh", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual("fsim would start" in result.stdout, code == 0)

    def test_validation_still_selects_four_faults(self):
        output = self.root / "fitness.output"
        with patch.object(sys, "argv", ["evaluate.py", "--validate-faults", "4", "seed.S"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "validation_fault_ids", return_value=[3, 4, 5, 6]), \
             patch.object(evaluate, "score", return_value=100.0) as score:
            self.assertEqual(evaluate.main(), 0)
        score.assert_called_once_with(Path("seed.S"), full=False, selected_ids=[3, 4, 5, 6],
                                      result_group="validate", jobs=16)

    def test_assembler_syntax_errors_are_recognized(self):
        def failure(command, work, log):
            log.write_text("bad.S: Assembler messages:\nbad.S:4: Error: unrecognized opcode")
            raise RuntimeError("compiler failed")
        with patch.object(evaluate, "write_linker_script", return_value=self.root / "candidate.ld"), \
             patch.object(evaluate, "run", side_effect=failure):
            with self.assertRaises(evaluate.InvalidProgram):
                evaluate.compile_program(self.root / "bad.S", self.root)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("as", "ld", "nm")), "GNU tools unavailable")
    def test_linker_keeps_data_and_stack_below_the_separate_htif_page(self):
        # Exercise GNU ld's real section placement, not mocked symbol output.
        # Host assembly is enough: this tests the linker layout, not RISC-V execution.
        original = self.root / "original.ld"
        original.write_text("""ENTRY(_start)
SECTIONS {
  . = 0x80000000;
  PROVIDE(__stack_size_min = 24K);
  .text : { *(.text) }
  .tdata : { PROVIDE_HIDDEN(__tdata_start = .); *(.tdata) }
  .tbss (NOLOAD) : { *(.tbss) PROVIDE_HIDDEN(__tbss_end = .); }
  .htif ALIGN(0x40) : { *(.htif) }
  .data : ALIGN(8) { *(.data) }
  .bss : ALIGN(8) { *(.bss) }
  PROVIDE(_end = .);
  PROVIDE(__heap_size = 128K);
  . += __heap_size;
  . = ALIGN(4K);
  PROVIDE(__heap_end = .);
  PROVIDE(__tcb_size = __tbss_end - __tdata_start);
  PROVIDE(__stack_align = MAX(ALIGNOF(.tdata), 0x10));
  PROVIDE(__stack_start = ALIGN(__stack_align));
  PROVIDE(__stack_shift = LOG2CEIL(ALIGN(__stack_size_min + __tcb_size, __stack_align)));
  PROVIDE(__stack_size = 1 << __stack_shift);
}
""")
        assembly = self.root / "layout.s"
        assembly.write_text(""".section .text
.global _start
_start: .byte 0
.section .htif,"aw"
.global tohost,fromhost
tohost: .quad 0
fromhost: .quad 0
.section .data._impure_data,"aw"
.global ordinary_data
ordinary_data: .quad 1
.section .bss,"aw",@nobits
.space 64
""")
        obj = self.root / "layout.o"
        subprocess.run(["as", "-o", str(obj), str(assembly)], check=True, capture_output=True)
        original_bytes = original.read_bytes()
        with patch.object(evaluate.config, "LINKER", original):
            linker = evaluate.write_linker_script(self.root)
        self.assertEqual(original.read_bytes(), original_bytes)
        binary = self.root / "layout.elf"
        linked = subprocess.run(["ld", "-T", str(linker), "-o", str(binary), str(obj)],
                                capture_output=True, text=True)
        self.assertEqual(linked.returncode, 0, linked.stderr)
        with patch.object(evaluate.config, "NM", Path(shutil.which("nm"))):
            evaluate.check_htif(binary)
        output = subprocess.check_output(["nm", str(binary)], text=True)
        symbols = {fields[-1]: int(fields[0], 16) for fields in map(str.split, output.splitlines())}
        self.assertEqual(symbols["tohost"], evaluate.config.HTIF_ADDRESS)
        self.assertEqual(symbols["fromhost"], evaluate.config.HTIF_ADDRESS + 8)
        self.assertLess(symbols["ordinary_data"], evaluate.config.HTIF_ADDRESS)
        self.assertLess(symbols["__stack_start"] + symbols["__stack_size"], evaluate.config.HTIF_ADDRESS)
        # Real linker assertions reject heap/stack overlap and oversized HTIF.
        generated_text = linker.read_text()
        for text in (generated_text.replace("__heap_size = 128K", "__heap_size = 2M"),
                     generated_text.replace("KEEP(*(.htif .htif.*))", "KEEP(*(.htif .htif.*)) . += 0x1000;")):
            linker.write_text(text)
            result = subprocess.run(["ld", "-T", str(linker), "-o", str(binary), str(obj)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RAW", result.stderr)

    def test_linker_refuses_an_unrecognized_template(self):
        original = self.root / "original.ld"
        original.write_text("SECTIONS { .text : { *(.text) } }\n")
        with patch.object(evaluate.config, "LINKER", original):
            with self.assertRaisesRegex(RuntimeError, "expected one .htif block"):
                evaluate.write_linker_script(self.root)

    def test_htif_rejects_fromhost_outside_the_uncached_page(self):
        symbols = ("0000000080100000 D tohost\n0000000080200000 D fromhost\n"
                   "0000000080002240 B _end\n0000000080023000 B __stack_start\n"
                   "0000000000008000 A __stack_size\n")
        with patch.object(evaluate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, symbols, "")):
            with self.assertRaisesRegex(RuntimeError, "fromhost is outside"):
                evaluate.check_htif(self.root / "candidate.riscv")

    def full_report(self, name, statuses):
        work = self.root / name
        work.mkdir()
        rows = [f' < 0> {status} 0 {{PORT "core.signal{i}"}}'
                for i, status in enumerate(statuses)]
        (work / "faults.rpt").write_text("FaultList {\n" + "\n".join(rows) + "\n}\n")
        (work / "summary.rpt").write_text(
            f"# Number of Faults: {len(statuses)}\n# Detected DG {statuses.count('DD')}\n"
            f"# Not Attempted NA {statuses.count('NA')}\n")
        (work / "injection-markers.txt").write_text("Info : Injecting faults\n")
        return work

    def test_reference_and_detected_ids_reuse_the_parser(self):
        base = self.full_report("base", ["DD", "ND", "DD", "UU"])
        candidate = self.full_report("candidate", ["ND", "DD", "DD", "UU"])
        sff = self.root / "faults.sff"
        sff.write_text((base / "faults.rpt").read_text().replace("< 0>", ""))
        reference_path = self.root / "reference.json"
        with patch.object(evaluate.config, "SFF", sff), \
             patch.object(evaluate.config, "FITNESS_REFERENCE", reference_path), \
             patch.object(evaluate, "campaign_fingerprint", return_value={"build": "same"}):
            # A report from another build must not be frozen with this build's label.
            evaluate.save_detected_ids(base)
            with patch.object(evaluate, "campaign_fingerprint", return_value={"build": "other"}):
                with self.assertRaisesRegex(ValueError, "another simulator build"):
                    evaluate.freeze_reference(base / "faults.rpt")
            evaluate.freeze_reference(base / "faults.rpt")
            reference = evaluate.load_reference(required=True)
            self.assertEqual(reference["detected_ids"], [1, 3])
            with self.assertRaises(FileExistsError):
                evaluate.freeze_reference(base / "faults.rpt")
            evaluate.save_detected_ids(candidate)
            self.assertEqual(json.loads((candidate / "detected-ids.json").read_text())[
                "detected_ids"], [2, 3])
            with patch.object(evaluate, "RUNS", self.root), \
                 patch.object(evaluate, "evaluation_digest", return_value="../candidate"):
                (self.root / "full").mkdir()
                self.assertEqual(evaluate.complementary_detections(Path("BEST_raw"), reference), 1)
            with patch.object(evaluate, "campaign_fingerprint", return_value={"build": "other"}):
                with self.assertRaisesRegex(ValueError, "does not match"):
                    evaluate.load_reference()

    def test_detection_evidence_refuses_wrong_counts_ids_and_file_io(self):
        work = self.full_report("candidate", ["DD", "ND"])
        sff = self.root / "faults.sff"
        sff.write_text((work / "faults.rpt").read_text().replace("< 0>", ""))
        with patch.object(evaluate.config, "SFF", sff):
            (work / "summary.rpt").write_text("# Number of Faults: 2\n# Detected DG 2\n")
            with self.assertRaisesRegex(ValueError, "do not match DG"):
                evaluate.detection_evidence(work / "faults.rpt")
            (work / "summary.rpt").write_text("# Number of Faults: 2\n# Detected DG 1\n")
            text = (work / "faults.rpt").read_text()
            (work / "faults.rpt").write_text(text.replace(" ND ", " IF "))
            with self.assertRaisesRegex(ValueError, "illegal file I/O"):
                evaluate.detection_evidence(work / "faults.rpt")
            (work / "faults.rpt").write_text(text.replace("core.signal1", "core.other"))
            with self.assertRaisesRegex(ValueError, "do not match the current SFF"):
                evaluate.detection_evidence(work / "faults.rpt")

    def test_parser_never_skips_other_fault_classes(self):
        report = self.root / "mixed.rpt"
        report.write_text('FaultList {\n< 1> ND 0 {PORT "core.a"}\n'
                          '< 1> ND 0 {NET "core.b"}\n}\n')
        with self.assertRaisesRegex(ValueError, "unsupported fault entry"):
            read_faults(report)

    def test_evolution_requires_a_reference_before_simulating(self):
        with patch.object(sys, "argv", ["evaluate.py", "--require-reference", "seed.S"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(self.root / "fitness.output")}), \
             patch.object(evaluate.config, "FITNESS_REFERENCE", self.root / "absent.json"), \
             patch.object(evaluate, "score") as score:
            with self.assertRaisesRegex(FileNotFoundError, "freeze a full report"):
                evaluate.main()
        score.assert_not_called()

    def test_two_values_are_written_in_order(self):
        output = self.root / "fitness.output"
        reference = {"detected_ids": [1, 3]}
        with patch.object(sys, "argv", ["evaluate.py", "--require-reference", "BEST_raw"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "load_reference", return_value=reference) as load, \
             patch.object(evaluate, "score", return_value=20.4), \
             patch.object(evaluate, "complementary_detections", return_value=4) as extra:
            evaluate.main()
        load.assert_called_once_with(True)
        extra.assert_called_once_with(Path("BEST_raw"), reference)
        self.assertEqual(output.read_text(), "20.400000000000 4\n")

    def test_smoke_uses_two_values_without_requiring_reference(self):
        output = self.root / "fitness.output"
        with patch.object(sys, "argv", ["evaluate.py", "--smoke", "seed.S"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output)}), \
             patch.object(evaluate, "compile_smoke", return_value=self.root / "candidate.riscv"), \
             patch.object(evaluate, "load_reference") as load:
            evaluate.main()
        load.assert_not_called()
        self.assertEqual(output.read_text(), "0.000000 0\n")
        population = self.root / "population.settings.xml"
        smoke.small_population(population)
        self.assertIn('<fitnessParameters value="2"', population.read_text())
        self.assertIn("--smoke", population.read_text())
        self.assertNotIn("--require-reference", population.read_text())

    def test_full_score_saves_ids_on_fresh_and_cached_results(self):
        fixture = self.full_report("fixture", ["DD", "ND", "DD", "UU"])
        sff = self.root / "faults.sff"
        sff.write_text((fixture / "faults.rpt").read_text().replace("< 0>", ""))
        work = self.root / "full/candidate"

        def run(work, *args):
            for name in ("summary.rpt", "faults.rpt", "injection-markers.txt"):
                shutil.copy2(fixture / name, work / name)

        with patch.object(evaluate.config, "SFF", sff), \
             patch.object(evaluate, "RUNS", self.root), \
             patch.object(evaluate, "evaluation_digest", return_value="candidate"), \
             patch.object(evaluate, "campaign_fingerprint", return_value={"build": "same"}), \
             patch.object(evaluate, "prepare", return_value=(work, work / "fsim.tcl")), \
             patch.object(evaluate, "probe", return_value=100000), \
             patch.object(evaluate, "write_profile"), \
             patch.object(evaluate, "raw_body_address", return_value=0x80000290), \
             patch.object(evaluate, "copy_campaign") as copy, \
             patch.object(evaluate, "run_fcm", side_effect=run) as fcm, \
             patch.object(evaluate, "require_unused"), \
             patch.object(evaluate, "require_injection_marker"), \
             patch.object(evaluate, "remove_run_data"):
            self.assertEqual(evaluate.score(Path("seed.S")), 50.0)
            self.assertEqual(evaluate.score(Path("seed.S")), 50.0)
        self.assertEqual(fcm.call_count, 1)
        self.assertEqual(copy.call_count, 1)
        self.assertEqual(json.loads((work / "detected-ids.json").read_text())[
            "detected_ids"], [1, 3])
        self.assertFalse((work / "detected-ids.json.tmp").exists())

    def replay_two_faults(self, streams):
        """Replay faults 1 and 2 with every tool mocked; return the mocks and the folder."""
        sff = self.root / "faults.sff"
        sff.write_text('FaultList {\nNA 0 {PORT "core.a"}\nNA 1 {PORT "core.b"}\n}\n')
        winner = self.root / "BEST_raw"
        winner.write_text("winner assembly\n")

        def clean(command, work, log):
            log.write_text("CVA6 RAW: injecting faults\n"
                           "*** PASSED *** Completed after 100000 simulation cycles\n")

        def build(source, work):
            self.assertEqual(source.read_text(), "winner assembly\n")
            return work / "candidate.riscv"

        def fcm(work, *args, database):
            (work / "summary.rpt").write_text("# Detected DG 1\n")
            (work / "faults.rpt").write_text(sff.read_text().replace("NA 1", "DD 1"))

        with patch.object(replay.evaluate, "check"), \
             patch.object(replay.config, "SFF", sff), \
             patch.object(replay.config, "REPLAY_STREAMS", streams), \
             patch.object(replay.evaluate, "RUNS", self.root), \
             patch.object(replay.evaluate, "evaluation_digest", return_value="never-evaluated"), \
             patch.object(replay.evaluate, "compile_program", side_effect=build), \
             patch.object(replay.evaluate, "raw_body_address", return_value=0x80000290), \
             patch.object(replay.evaluate, "run", side_effect=clean), \
             patch.object(replay.evaluate, "copy_campaign") as copy, \
             patch.object(replay.evaluate, "write_tcl") as tcl, \
             patch.object(replay.evaluate, "run_fcm", side_effect=fcm) as run_fcm, \
             patch.object(replay.evaluate, "require_unused") as unused, \
             patch.object(replay.evaluate, "require_injection_marker"), \
             patch.object(replay, "compare", return_value={"changed_fields": {}}), \
             patch.object(replay.evaluate, "remove_run_data"):
            replay.main([str(winner), "--fids", "1", "2"])
        root = next(self.root.glob("monitor-replay-*"))
        result = json.loads((root / "comparison.json").read_text())
        self.assertEqual(list(result["faults"]), ["1", "2"])
        self.assertEqual(result["faults"]["2"]["replay_status"], "DD")
        self.assertEqual(Path(result["source"]).read_text(), winner.read_text())
        self.assertEqual(tcl.call_args.kwargs["max_cycles"], 150000)
        return copy, tcl, run_fcm, unused, root

    def test_one_stream_copies_the_database_once_for_all_faults(self):
        copy, tcl, run_fcm, unused, root = self.replay_two_faults(streams=1)
        # One copy, shared by both faults; only the first run checks it is unused.
        copy.assert_called_once_with(root / "stream-0")
        self.assertEqual([call.kwargs["check_unused"] for call in tcl.call_args_list], [True, False])
        unused.assert_called_once()
        self.assertEqual({call.kwargs["database"] for call in run_fcm.call_args_list},
                         {root / "stream-0/fdb"})
        self.assertEqual(tcl.call_args.args[2], [2])

    def test_two_streams_replay_side_by_side_on_their_own_copies(self):
        copy, tcl, run_fcm, unused, root = self.replay_two_faults(streams=2)
        # Each stream has its own copy, and each copy is checked by its first run.
        self.assertEqual(sorted(call.args[0] for call in copy.call_args_list),
                         [root / "stream-0", root / "stream-1"])
        self.assertEqual([call.kwargs["check_unused"] for call in tcl.call_args_list], [True, True])
        self.assertEqual(unused.call_count, 2)
        databases = {call.args[0].name: call.kwargs["database"] for call in run_fcm.call_args_list}
        self.assertEqual(databases, {"fid-1": root / "stream-0/fdb", "fid-2": root / "stream-1/fdb"})

    def saved_clean_run(self, name, build):
        # A finished evaluation folder, made by simulator build `build`.
        work = self.root / "full" / name
        (work / "clean").mkdir(parents=True)
        for path in ("profile.json", "candidate.riscv", "clean/dependency.csv.gz",
                     "clean/raw-checker.csv.gz"):
            (work / path).write_text("")
        (work / "detected-ids.json").write_text(json.dumps({"campaign": {"format": 2, "build": build}}))
        (work / "probe.log").write_text("CVA6 RAW: injecting faults\n"
                                        "*** PASSED *** Completed after 4000 simulation cycles\n")
        return work

    def test_replay_reuses_the_clean_traces_from_evaluation(self):
        work = self.saved_clean_run("evaluated", "A")
        with patch.object(replay.evaluate, "RUNS", self.root), \
             patch.object(replay.evaluate, "campaign_fingerprint", return_value={"format": 2, "build": "A"}), \
             patch.object(replay.evaluate, "evaluation_digest", return_value="evaluated"), \
             patch.object(replay.evaluate, "compile_program") as build, \
             patch.object(replay.evaluate, "run") as simulate:
            result = replay.clean_traces(self.root / "source.S", self.root)
        self.assertEqual(result, (work / "clean", work / "candidate.riscv", 4000))
        build.assert_not_called()
        simulate.assert_not_called()

    def test_replay_reuses_the_folder_it_is_given_after_the_evaluator_changed(self):
        # The run's folder name no longer matches a recomputed digest.
        work = self.saved_clean_run("from-manifest", "A")
        with patch.object(replay.evaluate, "campaign_fingerprint", return_value={"format": 2, "build": "A"}), \
             patch.object(replay.evaluate, "evaluation_digest", return_value="changed"), \
             patch.object(replay.evaluate, "compile_program") as build:
            result = replay.clean_traces(self.root / "source.S", self.root, work)
        self.assertEqual(result, (work / "clean", work / "candidate.riscv", 4000))
        build.assert_not_called()

    def test_replay_runs_clean_again_after_a_simulator_rebuild(self):
        # Old clean traces must not be compared with faulty runs of a new build.
        work = self.saved_clean_run("old-build", "A")
        with patch.object(replay.evaluate, "campaign_fingerprint", return_value={"format": 2, "build": "B"}), \
             patch.object(replay.evaluate, "compile_program", side_effect=RuntimeError("fresh run")):
            with self.assertRaisesRegex(RuntimeError, "fresh run"):
                replay.clean_traces(self.root / "source.S", self.root, work)

    def dependency_trace(self, path, rows):
        header = ("cycle,issue_port,pc,instruction,consumer_id,fu,operation,rs1,rs2,"
                  "rs1_has_raw,rs2_has_raw,rs1_producer_id,rs2_producer_id,rs1_ready,rs2_ready,"
                  "rs1_forwarded,rs2_forwarded,rs1_source,rs2_source,same_cycle_dep_rs1,"
                  "same_cycle_dep_rs2,same_cycle_bypass_rs1,same_cycle_bypass_rs2,raw_stall_rs1,"
                  "raw_stall_rs2,structural_stall,external_stall,issue_accepted,flush,"
                  "accepted_order,operand_a,operand_b,raw_matches_rs1,raw_matches_rs2,"
                  "rs1_producer_valid,rs2_producer_valid").split(",")
        lines = [",".join(header)]
        for values in rows:
            lines.append(",".join(str(values.get(name, 0)) for name in header))
        path.write_text("\n".join(lines) + "\n")

    def test_profile_names_each_forwarded_operand_and_its_producer(self):
        clean = self.root / "clean"
        clean.mkdir()
        self.dependency_trace(clean / "dependency.csv", [
            # Harness code, then addi (ALU) and a xor that waits one cycle for it.
            {"pc": "50", "consumer_id": 0, "fu": 3, "issue_accepted": 1, "accepted_order": 1},
            {"pc": "100", "consumer_id": 1, "fu": 3, "issue_accepted": 1, "accepted_order": 2},
            {"pc": "104", "consumer_id": 2, "fu": 3, "rs1_has_raw": 1, "rs1_producer_id": 1,
             "rs1_source": 8, "raw_stall_rs1": 1},
            {"pc": "104", "consumer_id": 2, "fu": 3, "rs1_has_raw": 1, "rs1_producer_id": 1,
             "rs1_producer_valid": 1, "rs1_source": 1, "issue_accepted": 1, "accepted_order": 3},
        ])
        (clean / "raw-checker.csv").write_text("cycle\n")
        body = {0x100: "addi t0,a0,17", 0x104: "xor t1,t0,a0"}
        with patch.object(evaluate, "disassemble", return_value=body):
            evaluate.write_profile(self.root / "candidate.riscv", self.root, 4000)
        profile = json.loads((self.root / "profile.json").read_text())
        addi, xor = profile["instructions"]
        self.assertEqual(addi["issued"], 1)
        self.assertEqual(xor["rs1"], {"forward": 1})
        self.assertEqual(xor["rs1_producers"], {"0x100 addi t0,a0,17 (ALU, distance 1)": 1})
        self.assertEqual(xor["stall_cycles"], {"waiting for a producer": 1})
        self.assertEqual(profile["events"], [{"operand": "rs1", "producer_unit": "ALU",
                                              "distance": "1", "port": 0, "stall": "stalled",
                                              "count": 1}])
        # The raw traces are kept compressed, and still readable for replays.
        self.assertFalse((clean / "dependency.csv").exists())
        with evaluate.open_trace(clean / "dependency.csv") as stream:
            self.assertIn("consumer_id", stream.readline())

    def test_profile_distinguishes_invalid_zero_from_real_producer_zero(self):
        clean = self.root / "clean"
        clean.mkdir()
        self.dependency_trace(clean / "dependency.csv", [
            {"pc": "100", "consumer_id": 0, "fu": 1, "issue_accepted": 1, "accepted_order": 1},
            {"pc": "104", "consumer_id": 1, "fu": 3, "rs1_producer_id": 0,
             "rs1_source": 1, "issue_accepted": 1, "accepted_order": 2},
            {"pc": "108", "consumer_id": 2, "fu": 3, "rs1_producer_id": 0,
             "rs1_producer_valid": 1, "rs1_source": 1, "issue_accepted": 1, "accepted_order": 3},
        ])
        (clean / "raw-checker.csv").write_text("cycle\n")
        body = {0x100: "ld t0,0(sp)", 0x104: "addi t1,t0,31", 0x108: "xor t2,t0,a0"}
        with patch.object(evaluate, "disassemble", return_value=body):
            evaluate.write_profile(self.root / "candidate.riscv", self.root, 100)
        profile = json.loads((self.root / "profile.json").read_text())
        self.assertEqual(profile["format"], 2)
        invalid, valid = profile["instructions"][1:]
        self.assertEqual(invalid["rs1_producers"], {})
        self.assertEqual(invalid["unattributed_forwarding"], {"rs1: monitor found no producer": 1})
        self.assertEqual(valid["rs1_producers"], {"0x100 ld t0,0(sp) (LOAD, distance 2)": 1})
        self.assertEqual(len(profile["events"]), 1)

    def test_profile_uses_latest_instruction_after_scoreboard_id_reuse(self):
        clean = self.root / "clean"
        clean.mkdir()
        self.dependency_trace(clean / "dependency.csv", [
            {"pc": "100", "consumer_id": 1, "fu": 3, "issue_accepted": 1, "accepted_order": 1},
            {"pc": "104", "consumer_id": 1, "fu": 1, "issue_accepted": 1, "accepted_order": 9},
            {"pc": "108", "consumer_id": 2, "fu": 3, "rs1_producer_id": 1,
             "rs1_producer_valid": 1, "rs1_source": 1, "issue_accepted": 1, "accepted_order": 10},
        ])
        (clean / "raw-checker.csv").write_text("cycle\n")
        with patch.object(evaluate, "disassemble", return_value={0x104: "ld t0,0(sp)", 0x108: "addi t1,t0,31"}):
            evaluate.write_profile(self.root / "candidate.riscv", self.root, 100)
        profile = json.loads((self.root / "profile.json").read_text())
        self.assertEqual(profile["instructions"][1]["rs1_producers"],
                         {"0x104 ld t0,0(sp) (LOAD, distance 1)": 1})

    def test_profile_clears_stalls_but_keeps_issued_producers_on_flush(self):
        clean = self.root / "clean"
        clean.mkdir()
        self.dependency_trace(clean / "dependency.csv", [
            {"pc": "100", "consumer_id": 0, "fu": 1, "issue_accepted": 1, "accepted_order": 1},
            {"pc": "104", "consumer_id": 1, "raw_stall_rs1": 1},
            {"pc": "104", "consumer_id": 1, "flush": 1},
            {"pc": "104", "consumer_id": 1, "rs1_producer_id": 0, "rs1_producer_valid": 1,
             "rs1_source": 1, "issue_accepted": 1, "accepted_order": 2},
        ])
        (clean / "raw-checker.csv").write_text("cycle\n")
        with patch.object(evaluate, "disassemble", return_value={0x100: "ld t0,0(sp)",
                                                               0x104: "addi t1,t0,31"}):
            evaluate.write_profile(self.root / "candidate.riscv", self.root, 100)
        profile = json.loads((self.root / "profile.json").read_text())
        self.assertEqual(profile["events"][0]["stall"], "no stall")
        consumer = profile["instructions"][1]
        self.assertEqual(consumer["rs1_producers"], {"0x100 ld t0,0(sp) (LOAD, distance 1)": 1})
        self.assertEqual(consumer["unattributed_forwarding"], {})

    def test_profile_rejects_old_trace_without_producer_validity(self):
        clean = self.root / "clean"
        clean.mkdir()
        (clean / "dependency.csv").write_text("cycle,pc,rs1_producer_id\n")
        with patch.object(evaluate, "disassemble", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "old producer IDs are unreliable"):
                evaluate.write_profile(self.root / "candidate.riscv", self.root, 100)

    @unittest.skipUnless(shutil.which("verilator"), "Verilator is not installed")
    def test_monitor_follows_writeback_then_scoreboard_priority(self):
        rtl = (Path(evaluate.__file__).resolve().parent.parent /
               "testyard/generators/cva6/src/main/resources/cva6/vsrc/cva6/core/issue_read_operands.sv").read_text()
        declarations = rtl.split("  // These masks retain", 1)[1].split("\n", 1)[1].split(
            "  // synthesis translate_on", 1)[0]
        observer = rtl.split("  always_comb begin : life_story_classify", 1)[1].split(
            "  // Count cycles", 1)[0]
        bench = self.root / "monitor_check.sv"
        bench.write_text("""
package ariane_pkg; localparam int REG_ADDR_SIZE = 5; endpackage
module monitor_check;
  typedef struct packed {
    int NrIssuePorts, NR_SB_ENTRIES, NrWbPorts, TRANS_ID_BITS;
    bit FpPresent;
  } cfg_t;
  localparam cfg_t CVA6Cfg = '{1, 8, 2, 3, 0};
  localparam int STORE=2, CTRL_FLOW=4, ACCEL=10;
  typedef struct packed {logic [4:0] rd; logic [7:0] op;} entry_t;
  typedef struct packed {logic [2:0] trans_id;} wb_t;
  typedef struct packed {entry_t [7:0] sbe; wb_t [1:0] wb;} fwd_t;
  typedef struct packed {
    logic use_zimm, use_pc, use_imm;
    logic [7:0] op;
    logic [3:0] fu;
  } instr_t;
  fwd_t fwd_i;
  instr_t [0:0] issue_instr_i;
  logic [0:0][9:0] rs1_fwd_req, rs2_fwd_req;
  logic [0:0] rs1_has_raw, rs2_has_raw, rs1_valid, rs2_valid;
  function automatic bit is_rd_fpr(input logic [7:0] op); return 0; endfunction
  function automatic bit is_rs2_fpr(input logic [7:0] op); return 0; endfunction
  // These masks retain
""" + declarations + "\n  always_comb begin : life_story_classify" + observer + """
  initial begin
    fwd_i='0; issue_instr_i='0; rs1_fwd_req='0; rs2_fwd_req='0;
    rs1_has_raw=1; rs2_has_raw=1; rs1_valid=1; rs2_valid=1;
    fwd_i.wb[0].trans_id=1; rs1_fwd_req[0][0]=1;
    #1;
    assert(life_rs1_found[0] && life_rs1_producer[0]==1);
    assert(life_rs1_matches[0]==8'h02);
    // Direct WB wins over a ready scoreboard entry and another WB port.
    fwd_i.wb[0].trans_id=7; fwd_i.wb[1].trans_id=5;
    rs1_fwd_req[0][1]=1; rs1_fwd_req[0][3]=1; rs2_fwd_req[0][1]=1;
    #1;
    assert(life_rs1_producer[0]==7 && life_rs2_producer[0]==5);
    assert(life_rs1_matches[0]==8'ha2);
    // Scoreboard entry zero is a legitimate producer, not "not found".
    rs1_fwd_req='0; rs1_fwd_req[0][2]=1;
    #1;
    assert(life_rs1_found[0] && life_rs1_producer[0]==0);
    rs1_fwd_req='0;
    #1;
    assert(!life_rs1_found[0]);
    rs1_fwd_req[0][0]=1; issue_instr_i[0].use_zimm=1;
    #1;
    assert(!life_rs1_found[0] && life_rs1_source[0]==10);
    $display("Monitor attribution checks passed");
    $finish;
  end
endmodule
""")
        build = subprocess.run(["verilator", "--binary", "--timing", "--assert", "-Wno-fatal",
                                "--top-module", "monitor_check", "--Mdir", str(self.root / "obj"),
                                str(bench)], capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        result = subprocess.run([str(self.root / "obj/Vmonitor_check")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Monitor attribution checks passed", result.stdout)

    def test_manifest_lists_every_candidate_of_the_batch(self):
        manifest = self.root / "manifest.jsonl"
        runs = self.root / "runs"
        (runs / "full/bad").mkdir(parents=True)
        (runs / "full/bad/rejected.txt").write_text("assembly rejected\n")
        output = self.root / "fitness.output"
        with patch.object(sys, "argv", ["evaluate.py", "individualAAB", "individualAAC"]), \
             patch.dict(evaluate.os.environ, {"UGP3_FITNESS_FILE": str(output),
                                              "RAW_MANIFEST": str(manifest)}), \
             patch.object(evaluate, "HERE", self.root), \
             patch.object(evaluate, "RUNS", runs), \
             patch.object(evaluate, "evaluation_digest",
                          side_effect=lambda source, full: "good" if source.name.endswith("B") else "bad"), \
             patch.object(evaluate, "score", side_effect=[19.0, 0.0]), \
             patch.object(evaluate, "complementary_detections", return_value=0):
            evaluate.main()
        good, bad = [json.loads(line) for line in manifest.read_text().splitlines()]
        self.assertEqual(good["individual"], "individualAAB")
        self.assertEqual(good["run_dir"], "runs/full/good")
        self.assertIsNone(good["rejected"])
        self.assertEqual(bad["rejected"], "assembly rejected")

    def test_run_report_merges_candidates_and_replays_the_best(self):
        sff = self.root / "faults.sff"
        # Real signal shapes: an exception field, a data path, and the unused CVXIF.
        signals = ["i_scoreboard.wbdata_i[0][1]", "i_scoreboard.fwd_o.sbe[0].ex.cause[1]",
                   "i_issue_read_operands.fwd_i.wb[0].data[1]", "i_scoreboard.fwd_o.sbe[0].ex.tval[1]",
                   "i_scoreboard.x_we_i"]
        sff.write_text("FaultList {\n" + "\n".join(
            f'NA 0 {{PORT "core.issue_stage_i.{signal}"}}' for signal in signals) + "\n}\n")
        run = self.root / "results/run1"
        run.mkdir(parents=True)

        def program(name, coverage, by_status, events):
            work = self.root / "runs/full" / name
            work.mkdir(parents=True)
            (work / "detected-ids.json").write_text(json.dumps(
                {"ids_by_status": by_status, "campaign": {"build": "same"}}))
            (work / "profile.json").write_text(json.dumps({"events": events}))
            return {"individual": f"individual{name}", "digest": name,
                    "run_dir": f"runs/full/{name}", "coverage": coverage,
                    "beyond_reference": 0, "rejected": None}

        event = {"operand": "rs1", "producer_unit": "ALU", "distance": "1",
                 "port": 0, "stall": "no stall", "count": 5}
        entries = [
            program("A", 25.0, {"DD": [1], "NO": [2, 3], "NC": [4], "ND": [5]}, [event]),
            program("B", 50.0, {"DD": [1, 2], "NO": [3], "NC": [4], "ND": [5]}, [event]),
            {"individual": "individualC", "digest": "C", "run_dir": "runs/full/C",
             "coverage": 0.0, "beyond_reference": 0, "rejected": "assembly rejected"},
        ]
        (run / "manifest.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
        with patch.object(replay.config, "SFF", sff), \
             patch.object(replay.evaluate, "HERE", self.root), \
             patch.object(replay.config, "REPLAY_FAULTS", 5), \
             patch.object(replay.evaluate, "campaign_fingerprint", return_value={"build": "other"}):
            # Programs from another build must not be merged or replayed.
            with self.assertRaisesRegex(RuntimeError, "another simulator build"):
                replay.report_run(run)
        with patch.object(replay.config, "SFF", sff), \
             patch.object(replay.evaluate, "HERE", self.root), \
             patch.object(replay.config, "REPLAY_FAULTS", 5), \
             patch.object(replay.evaluate, "campaign_fingerprint", return_value={"build": "same"}), \
             patch.object(replay, "replay_faults") as replays:
            replay.report_run(run)
        report = json.loads((run / "report.json").read_text())
        self.assertEqual(report["best"]["individual"], "individualB")
        self.assertEqual((report["candidates"], report["rejected"]), (3, 1))
        # Fault 2 is detected by B, so it is no longer missed.
        self.assertEqual(report["faults_by_best_status"], {"DD": 2, "NO": 1, "NC": 1, "ND": 1})
        self.assertEqual(report["missed_ids"], {"NO": [3], "NC": [4], "ND": [5]})
        self.assertEqual(report["missed_by_signal"], {
            "i_issue_read_operands.fwd_i.wb[].data[]": {"NO": 1},
            "i_scoreboard.fwd_o.sbe[].ex.tval[]": {"NC": 1}, "i_scoreboard.x_we_i": {"ND": 1}})
        self.assertEqual({name: group["faults"] for name, group in report["missed_by_group"].items()},
                         {"data paths": {"NO": 1}, "exception fields": {"NC": 1},
                          "coprocessor interface (CVXIF)": {"ND": 1}})
        self.assertEqual(report["events_seen"][0]["programs"], 2)
        self.assertIn({"operand": "rs2", "producer_unit": "ALU", "distance": "1",
                       "port": 0, "stall": "no stall"}, report["events_never_seen"])
        # Only the best program's simulated, missed faults the monitors can see are
        # replayed: fault 3 (data path), not 5 (unused CVXIF) or 4 (never excited).
        replays.assert_called_once_with(self.root / "runs/full/B/source.S", [3], run / "replays",
                                        self.root / "runs/full/B")

    def test_run_report_gets_an_absolute_path(self):
        # make report passes results/NAME, relative to raw-evolution.
        with patch.object(replay.evaluate, "check"), \
             patch.object(replay, "report_run") as report:
            replay.main(["--run", "results/run1"])
        self.assertEqual(report.call_args.args[0], Path("results/run1").resolve())

    def test_injection_check_ignores_fault_free_tsim_tasks(self):
        for name, text in (("fsim:1", "Chronologic VCS simulator\nInfo : Injecting x\n"),
                           ("tsim:1", "Chronologic VCS simulator\n")):
            task = self.root / "fcm.dir" / name
            task.mkdir(parents=True)
            (task / "out.log").write_text(text)
        evaluate.require_injection_marker(self.root)
        self.assertIn("fsim:1", (self.root / "injection-markers.txt").read_text())


if __name__ == "__main__":
    unittest.main()
