    # Emit normal code without position-independent addressing.
    .option nopic
    # Place the function in the executable text section.
    .text
    # Keep the entry point aligned to four bytes.
    .align 2
    # Let the C harness call this symbol.
    .globl raw_body
    .type raw_body, @function

# A small starting program with several dependency distances.
raw_body:
    # Reserve one stack slot and save the input there.
    addi sp, sp, -16
    sd a0, 0(sp)

    # An adjacent producer and consumer.
    addi t0, a0, 17
    # t1 reads t0 immediately.
    xor  t1, t0, a0

    # One independent instruction separates this pair.
    slli t2, t1, 3
    # This instruction creates a one-step gap.
    addi t6, a0, 5
    # t3 reads the earlier t2 value.
    add  t3, t2, t6

    # Both inputs depend on recent producers.
    sub  t4, t3, t1
    # t5 reads both recent results.
    xor  t5, t4, t3

    # A load feeds the next instruction.
    ld   t0, 0(sp)
    # The ALU consumes the load result immediately.
    addi t1, t0, 31

    # Return a value that depends on all main chains above.
    xor  a0, t5, t1
    # Release the stack slot before returning to C.
    addi sp, sp, 16
    ret
    # Record the final size for the assembler and linker.
    .size raw_body, .-raw_body
