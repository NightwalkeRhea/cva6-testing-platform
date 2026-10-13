/*
 * Companion of the evolved body, newlib flavour: same C runtime and same
 * linker script as the hand-written benchmarks, so the boot being simulated is
 * the one the injection cycle was calibrated against.
 *
 * The signature is stored, not just computed. A value left in the register
 * file never crosses the chip boundary, so nothing of the test would be
 * observable where the strobe sits.
 */

extern long sbst_body(void);

volatile long sbst_signature;

int main(void)
{
	sbst_signature = sbst_body();
	return 0;
}
