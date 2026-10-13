/*
 * Companion of the evolved body, bare flavour: no C runtime at all.
 *
 * The full crt0 costs about 11750 cycles before main is even entered, which on
 * a short individual is most of the run: those cycles are identical for every
 * individual, so they measure nothing while being paid every time.
 *
 * The price is that the injection cycle must be recalibrated and compiled into
 * the fault-aware simulator again, because main is now reached far earlier.
 *
 * tohost/fromhost are defined here: without libgloss nothing else provides
 * them, and the host looks them up by name in the symbol table.
 */

	.section .text.init,"ax",@progbits
	.globl _start
_start:
	la      sp, _bare_stack_top
	/* Relaxed, this would load gp relative to gp itself. */
	.option push
	.option norelax
	la      gp, __global_pointer$
	.option pop
	li      s0, 0
	li      s1, 0

	call    sbst_body

	la      t0, sbst_signature
	sd      a0, 0(t0)               /* the signature must be written, not kept */
	fence   w, w

	la      t0, tohost
	li      t1, 1                   /* exit code 0 */
	sd      t1, 0(t0)
	fence   w, w
1:	j       1b

	.section .bss,"aw",@nobits
	.align  4
	.globl  sbst_signature
sbst_signature:
	.dword  0
_bare_stack:
	.skip   4096
	.globl  _bare_stack_top
_bare_stack_top:

	.section .htif,"aw",@progbits
	.align  6
	.globl  tohost
tohost:
	.dword  0
	.globl  fromhost
fromhost:
	.dword  0
