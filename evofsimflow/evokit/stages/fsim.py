"""The fault campaign: one fresh campaign per individual, then the checks that
decide whether the number it produced may be believed.

A fresh campaign is not optional. Inside one campaign the tool drops faults
between test cases, so a second individual would be measured on what the first
one left undetected and the two fitness values would not be comparable.
"""
from __future__ import annotations

from pathlib import Path

from flowkit.fsimgen import FULL_REPORTS, SUMMARY_REPORT, Rewrite, rewrite
from flowkit.shell import safe_rmtree

from ..manifest import write_text
from ..model import Job, StageResult, Status
from ..reports import campaign_facts, parse_summary
from . import HEAVY, Stage, workdir_for

TRANSIENT = (
    "license",
    "Licence",
    "Cannot find license",
    "No such file or directory",
    "Resource temporarily unavailable",
    "Cannot allocate memory",
)


class FsimStage(Stage):
    name = "fsim"
    phase = HEAVY

    def run(self, job: Job) -> StageResult:
        ctx = self.ctx
        wd = job.workdir or workdir_for(ctx.layout, job.ind)
        binary = wd / "ind.riscv"
        if not binary.exists():
            return StageResult(Status.INFRA, "binary missing before the campaign")

        # A retry must not inherit half a campaign: the database and the toggle
        # dumps are exactly what the tool refuses to restart from.
        for junk in ("fdb", "fcm.dir", "fcm_tsim_fsdb"):
            safe_rmtree(wd / junk, wd, ignore_errors=True)

        tcl = wd / "fsim.tcl"
        write_text(
            tcl,
            rewrite(
                ctx.ws.tcl_template.read_text(),
                Rewrite(
                    simv=ctx.ws.simv,
                    binary=binary,
                    run_tcl=ctx.ws.run_tcl if ctx.camp.force_regs else None,
                    report_dir=wd,
                    max_cycles=self._cycle_cap(job),
                    seed=ctx.site.sim_seed,
                    jobs=max(1, job.cores),
                    coats=ctx.camp.coats,
                    keep_reports={SUMMARY_REPORT, *FULL_REPORTS},
                ),
            ),
        )

        campaign = ctx.ws.zoix_campaign
        fcc = ctx.ex.run(
            [
                ctx.site.tools.vc_fcc,
                "-full64",
                "-daidir", str(ctx.ws.daidir),
                "-sff", str(ctx.ws.sff),
                "-campaign", campaign,
                "-prune", "global",
                "-uncontrollability",
                "-collapse", "off",
                "-overwrite",
                "-excludecolloff",
            ],
            cwd=wd,
            log=wd / "fcc.log",
            timeout=3600,
        )
        if not fcc.ok:
            return StageResult(Status.INFRA, _why("vc_fcc", wd / "fcc.log", fcc.rc))

        fcm = ctx.ex.run(
            [
                ctx.site.tools.vc_fcm,
                "-tcl_script", str(tcl),
                "-campaign", campaign,
                "-connect",
            ],
            cwd=wd,
            log=wd / "fcm.log",
            timeout=24 * 3600,
        )
        if not fcm.ok:
            return StageResult(Status.INFRA, _why("vc_fcm", wd / "fcm.log", fcm.rc))

        return self._harvest(job, wd)

    def _cycle_cap(self, job: Job) -> int:
        """Sized on the measured fault-free length. It does not bound the cost
        of a faulty machine (the campaign ends with the good machine's finish),
        but it is the only thing bounding a program that never terminates."""
        c = self.ctx.camp
        n = int(job.outcome.cycles * c.fsim_cycles_factor)
        return max(c.fsim_cycles_floor, min(c.fsim_cycles_cap, n or c.fsim_cycles_cap))

    def _harvest(self, job: Job, wd: Path) -> StageResult:
        report = wd / SUMMARY_REPORT
        if not report.exists():
            return StageResult(Status.INFRA, "the campaign wrote no summary")
        summary = parse_summary(report.read_text())
        problem = summary.consistent()
        if problem:
            return StageResult(Status.INFRA, f"summary does not hold together: {problem}")

        facts = campaign_facts(wd, self.ctx.site.clock_ns)
        if facts.fsdb_invalid:
            return StageResult(Status.INFRA, "toggle dump rejected (FCM-FSDB-INVALID)")
        if facts.gold == "FAIL":
            return StageResult(
                Status.INFRA, "the fault-free run inside the campaign did not pass"
            )
        note = "" if facts.gold == "PASS" else "gold verdict not found in the session"

        # The session directory holds the only evidence of these checks, so it
        # is pruned once the facts are extracted, and kept when they look wrong.
        if not facts.anomalous:
            for junk in ("fdb", "fcm.dir", "fcm_tsim_fsdb"):
                safe_rmtree(wd / junk, wd, ignore_errors=True)

        return StageResult(
            Status.OK,
            detail=note,
            data={
                "coverage": summary.coverage,
                "classes": summary.classes,
                "gold": facts.gold,
                "tasks": facts.tasks,
                "faults": summary.total,
            },
        )


def _why(tool: str, log: Path, rc: int) -> str:
    text = log.read_text(errors="replace")[-4000:] if log.exists() else ""
    for needle in TRANSIENT:
        if needle in text:
            return f"{tool} failed (rc={rc}): {needle}"
    last = " | ".join(text.strip().splitlines()[-4:])
    return f"{tool} failed (rc={rc}): {last[:250]}"
