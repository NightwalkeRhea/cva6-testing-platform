"""One fault-free run before the expensive stage.

It is worth a core for half a minute because it answers three questions at
once: does this individual terminate at all (a malformed one is rejected here
instead of after minutes of fault simulation), how many cycles does it take
(which sets the cycle cap for the campaign and orders the heavy queue), and
does the plain simulator agree that the program is sane.
"""
from __future__ import annotations

from flowkit import simv, verdict

from ..model import Job, StageResult, Status
from . import PREP, Stage, workdir_for


class ProbeStage(Stage):
    name = "probe"
    phase = PREP

    def __init__(self, ctx):
        super().__init__(ctx)
        # The workspace's copy, not the live rundir: see workspace.py.
        self.rundir = ctx.ws.probe_rundir
        self.simv = ctx.ws.probe_simv
        self.run_tcl = ctx.ws.probe_run_tcl if ctx.camp.force_regs else None
        self._flags: simv.Flags | None = None

    def flags(self) -> simv.Flags:
        """The design-stable flags the flow computed, with this run's cap, seed
        and register initialisation in place of the flow's."""
        if self._flags is None:
            ini = self.ctx.site.repo / "generators/testchipip/src/main/resources/dramsim2_ini"
            self._flags = (simv.load_flags(self.rundir, ini)
                           .with_max_cycles(self.ctx.camp.probe_max_cycles)
                           .with_seed(self.ctx.site.sim_seed)
                           .with_initreg("0"))
        return self._flags

    def run(self, job: Job) -> StageResult:
        wd = job.workdir or workdir_for(self.ctx.layout, job.ind)
        binary = wd / "ind.riscv"
        if not self.simv.exists():
            return StageResult(Status.INFRA, f"probe simv missing: {self.simv}")
        if self.run_tcl and not self.run_tcl.exists():
            return StageResult(Status.INFRA, f"probe run.tcl missing: {self.run_tcl}")

        argv = simv.command(self.simv, self.flags(), str(binary), None, self.run_tcl)
        log = wd / "probe.log"
        res = self.ctx.ex.run(argv, cwd=wd, log=log, timeout=3600)
        v = verdict.parse_file(log, res.rc)
        cycles = v.cycles(self.ctx.site.clock_ns)

        if v.kind == "PASS":
            job.cost_hint = float(cycles)
            return StageResult(Status.OK, data={"cycles": cycles})
        if v.kind == "FAIL":
            # The program ran and did not reach a clean exit: that is the
            # individual's own fault, and the cycles it managed are the grade.
            job.cost_hint = float(cycles)
            return StageResult(
                Status.MALFORMED, f"probe {v.detail}", data={"cycles": cycles}
            )
        return StageResult(Status.INFRA, f"probe unusable: {v.detail}")
