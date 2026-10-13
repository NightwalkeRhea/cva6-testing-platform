/*
 * probe_start.S — calibration probe for 'flow.py t2main'.
 *
 * Twin of probe_main.c but without a crt0 (-nostdlib -nostartfiles): the very
 * first instruction the program executes, at 0x80000000 where the bootrom does
 * mret, writes tohost and stops the simulation.
 *
 * The finish time is therefore t_start + eps, where:
 *   t_start = reset + bootrom + the host MSIP handshake, none of which depends
 *             on the program: it all lives in the netlist
 *   eps     = the latency with which fesvr notices the tohost write, identical
 *             to probe_main since the mechanism is the same
 *
 * Since t_start >= RESET_DELAY, it follows that eps <= T0 - RESET_DELAY, hence
 * the lower bound on when main is entered:
 *
 *   t_main = T1 - eps >= T1 - T0 + RESET_DELAY
 *
 * which is the number 'flow.py t2main' prints, floored, for
 * timing.t2main_cycles: where 'flow.py fsim --inject-at main' injects.
 *
 * tohost/fromhost are defined here because without libgloss nothing else
 * provides them; fesvr looks them up by name in the ELF symbol table.
 */

	.section .text.init,"ax",@progbits
	.globl _start
_start:
	la      t0, tohost
	li      t1, 1                   /* exit code 0 */
	sd      t1, 0(t0)
	fence   w, w
1:	j       1b

	.section .htif,"aw",@progbits
	.align  6
	.globl  tohost
tohost:
	.dword  0
	.globl  fromhost
fromhost:
	.dword  0
