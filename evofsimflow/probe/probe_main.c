/*
 * probe_main.c — calibration probe for 'flow.py t2main'.
 *
 * Purpose: measure how many cycles the simulation takes to reach main.
 *
 * How: the very first thing main does is write tohost, the HTIF register a
 * program uses to tell fesvr "done, exit code 0" (tohost = (code << 1) | 1, so
 * code 0 gives 1). The simulation therefore stops as soon as main is entered,
 * and the "$finish at simulation time" in sim.log is t_main + eps, where eps is
 * the latency with which the host notices the write.
 *
 * eps is unknown but cancels out against probe_start.S, which does exactly the
 * same thing at the first instruction of the program (0x80000000, no crt0).
 *
 * Built with the same flags as the real tests (tests/CMakeLists.txt) and hence
 * the same newlib/libgloss crt0, so the boot being measured is the real one.
 *
 * Neither printf nor exit() is used: they would cost cycles and those cycles
 * would land inside the measurement. tohost is defined by libgloss (section
 * .htif); declaring it extern is enough here.
 */

extern volatile unsigned long tohost;

int main(void)
{
	tohost = 1;                     /* exit code 0 -> TestDriver calls $finish */
	for (;;)
		__asm__ volatile ("nop");   /* never return: libc's exit would write
		                               tohost again and skew the measurement */
}
