#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

// MicroGP writes this function in assembly.
extern uint64_t raw_body(uint64_t seed);

int main(void) {
    // Mix every result into one visible value.
    uint64_t signature = 0;

    // Run many inputs so each program exercises more data values.
    for (uint64_t seed = 0; seed < 256; ++seed) {
        signature ^= raw_body(seed);
    }

    // Keep the program result in the normal simulation log.
    printf("RAW evolution signature: 0x%016" PRIx64 "\n", signature);

    // Zero tells the test harness that the program completed.
    return 0;
}
