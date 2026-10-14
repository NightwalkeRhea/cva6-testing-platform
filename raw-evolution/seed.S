    # Emit normal code without position-independent addressing.
    .option nopic
    .text
    .align 2
    # Let the C harness call this symbol
    .globl raw_body
    .type raw_body, @function

# A small program with several dependency distances
raw_body:
    # Reserve one stack slot and save the input there
    addi sp, sp, -16
    sd a0, 0(sp)
    # An adjacent producer and consumer, so t1 reads t0 immediately
    addi t0, a0, 17
    xor  t1, t0, a0
    # now one independent instruction in between
    slli t2, t1, 3
    addi t6, a0, 5
    add  t3, t2, t6
    # now dependency on both rs1 and rs2, so t5 has to read "recent" t4 and t3
    sub  t4, t3, t1
    xor  t5, t4, t3
    # A load, followed by ALU instruction to consume the load result
    ld   t0, 0(sp)
    addi t1, t0, 31
    # Return a value that depends on all main chains above
    xor  a0, t5, t1
    # Release the stack slot before returning to C
    addi sp, sp, 16
    ret
    # Record the final size for the assembler and linker
    .size raw_body, .-raw_body
