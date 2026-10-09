#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

// Run a chain in which every instruction needs the previous result
static uint64_t run_alu_chain(uint64_t seed) {
    uint64_t value;

    __asm__ volatile(
        "addi %[value], %[seed], 1\n\t"
        "slli %[value], %[value], 1\n\t"
        "addi %[value], %[value], 3\n\t"
        : [value] "=&r"(value)
        : [seed] "r"(seed)
    );

    return value;
}

// Load a value and use it in the next instruction (create load use dependency)
static uint64_t run_load_use(const volatile uint64_t *address) {
    uint64_t value;

    __asm__ volatile(
        "ld %[value], 0(%[address])\n\t"
        "addi %[value], %[value], 7\n\t"
        : [value] "=&r"(value)
        : [address] "r"(address)
        : "memory"
    );

    return value;
}

int main(void) {
    // A volatile location to force a real load during every iteration
    volatile uint64_t memory_value = 0;
    //signature for observing the result at the end
    uint64_t signature = 0;

    //count mistmatches
    uint64_t failures = 0;

    printf("RAW hazard test started\n");
    // Repeat the patterns so a fault campaign gets many useful cycles.
    for (uint64_t seed = 0; seed < 256; ++seed) {
        const uint64_t alu_result = run_alu_chain(seed);
        const uint64_t alu_expected = ((seed + 1) << 1) + 3;

        if (alu_result != alu_expected) {
            ++failures;
        }
        // Change the source for each load-use sequence
        memory_value = seed ^ UINT64_C(0x5a5a5a5a);

        const uint64_t load_result = run_load_use(&memory_value);
        const uint64_t load_expected = memory_value + 7;

        if (load_result != load_expected) {
            ++failures;
        }

        // Mix both dependency chains into one stable final value
        signature ^= alu_result + (load_result << (seed & 7));
    }

    printf("RAW signature: 0x%016" PRIx64 "\n", signature);
    printf("RAW failures:  %" PRIu64 "\n", failures);

    // return 0 if passed
    return failures == 0 ? 0 : 1;
}
