#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

// MicroGP writes this function in assembly.
extern uint64_t raw_body(uint64_t seed);

// Edge values first: zero, all ones, alternating bits, sign bit.
static const uint64_t directed[8] = {
    0x0000000000000000, 0xFFFFFFFFFFFFFFFF, 0x5555555555555555, 0xAAAAAAAAAAAAAAAA,
    0x0F0F0F0F0F0F0F0F, 0xF0F0F0F0F0F0F0F0, 0x8000000000000000, 0x7FFFFFFFFFFFFFFF};

// splitmix64: every output bit depends on every input bit, and it is deterministic.
static uint64_t splitmix64(uint64_t x) {
    x += 0x9E3779B97F4A7C15;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EB;
    return x ^ (x >> 31);
}

// Every trap adds its cause and address here, so it reaches the signature.
// (mtval always reads 0 in this build: TvalEn = 0.)
volatile uint64_t raw_traps;

// Skip the instruction that trapped and continue. Programs that never trap never
// get here. Written in assembly because this CPU has no FPU: a C interrupt
// handler would save float registers and trap again.
void raw_trap(void);
__asm__(
    "    .text\n"
    "    .align 2\n"
    "raw_trap:\n"
    "    addi sp, sp, -32\n"
    "    sd t0, 0(sp)\n"
    "    sd t1, 8(sp)\n"
    "    sd t2, 16(sp)\n"
    "    sd t3, 24(sp)\n"
    "    csrr t0, mcause\n"
    // A failed fetch (cause 0, 1, 12) has no instruction to skip: stop with an error.
    "    li t1, 1\n"
    "    bleu t0, t1, 2f\n"
    "    li t1, 12\n"
    "    beq t0, t1, 2f\n"
    // raw_traps = raw_traps * 31 + (mcause ^ mepc)
    "    csrr t2, mepc\n"
    "    xor t0, t0, t2\n"
    "    la t1, raw_traps\n"
    "    ld t3, 0(t1)\n"
    "    sub t0, t0, t3\n"
    "    slli t3, t3, 5\n"
    "    add t0, t0, t3\n"
    "    sd t0, 0(t1)\n"
    // Compressed instructions are 2 bytes; their two low bits are not 11.
    "    lhu t0, 0(t2)\n"
    "    andi t0, t0, 3\n"
    "    addi t2, t2, 2\n"
    "    li t1, 3\n"
    "    bne t0, t1, 1f\n"
    "    addi t2, t2, 2\n"
    "1:  csrw mepc, t2\n"
    "    ld t0, 0(sp)\n"
    "    ld t1, 8(sp)\n"
    "    ld t2, 16(sp)\n"
    "    ld t3, 24(sp)\n"
    "    addi sp, sp, 32\n"
    "    mret\n"
    "2:  li a0, 3\n"
    "    call exit\n");

int main(void) {
    __asm__ volatile("csrw mtvec, %0" : : "r"(raw_trap));

    // Mix every result into one visible value.
    uint64_t signature = 0;

    // 8 directed inputs first, then 56 pseudo-random ones.
    for (uint64_t i = 0; i < 64; ++i) {
        signature ^= raw_body(i < 8 ? directed[i] : splitmix64(i));
    }

    // Keep the program result in the normal simulation log.
    printf("RAW evolution signature: 0x%016" PRIx64 "\n", signature ^ raw_traps);

    // Zero tells the test harness that the program completed.
    return 0;
}
