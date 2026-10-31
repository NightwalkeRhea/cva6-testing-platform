#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

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

// Every trap adds its cause, value, and address here, so it reaches the signature.
static volatile uint64_t traps;

// Skip the instruction that trapped and continue. Programs that never trap never get here.
__attribute__((interrupt("machine"), aligned(4)))
static void trap_handler(void) {
    uint64_t cause, value, pc;
    __asm__ volatile("csrr %0, mcause" : "=r"(cause));
    __asm__ volatile("csrr %0, mtval" : "=r"(value));
    __asm__ volatile("csrr %0, mepc" : "=r"(pc));
    traps = traps * 31 + (cause ^ value ^ pc);
    // A failed fetch has no instruction to skip, so stop with an error.
    if (cause == 0 || cause == 1 || cause == 12) {
        exit(3);
    }
    // Compressed instructions are 2 bytes; their two low bits are not 11.
    pc += (*(volatile uint16_t *)pc & 3) == 3 ? 4 : 2;
    __asm__ volatile("csrw mepc, %0" : : "r"(pc));
}

int main(void) {
    __asm__ volatile("csrw mtvec, %0" : : "r"(trap_handler));

    // Mix every result into one visible value.
    uint64_t signature = 0;

    // 8 directed inputs first, then 56 pseudo-random ones.
    for (uint64_t i = 0; i < 64; ++i) {
        signature ^= raw_body(i < 8 ? directed[i] : splitmix64(i));
    }

    // Keep the program result in the normal simulation log.
    printf("RAW evolution signature: 0x%016" PRIx64 "\n", signature ^ traps);

    // Zero tells the test harness that the program completed.
    return 0;
}
